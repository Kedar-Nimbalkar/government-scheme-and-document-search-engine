import { readFile, unlink } from 'node:fs/promises';
import pg from 'pg';
import { committedDocumentIds, managedPdfs, promotePdf, quarantinePdf, sha256, stagedPdf, storedFile } from './storage.js';

const { Pool } = pg;
const PROFILE = 'user_id, username, role, dept_id';
const BOOKMARK = 'bookmark_id, doc_id, scheme_id, page_no, created_at';
const CAN_UPLOAD = `
  SELECT 1 FROM app_user u CROSS JOIN scheme s
  WHERE u.user_id=$1 AND s.scheme_id=$2
    AND (u.role='admin' OR (u.role='department' AND u.dept_id=s.dept_id))
`;

export class PersistenceError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

async function one(db, sql, values) {
  const { rows } = await db.query(sql, values);
  return rows[0] ?? null;
}

async function transaction(pool, action) {
  const client = await pool.connect();
  let unusable;
  try {
    await client.query('BEGIN');
    const result = await action(client);
    await client.query('COMMIT');
    return result;
  } catch (error) {
    try {
      await client.query('ROLLBACK');
    } catch (rollbackError) {
      unusable = rollbackError;
    }
    throw error;
  } finally {
    client.release(unusable);
  }
}

function contentLock(db, hash) {
  return db.query('SELECT pg_advisory_xact_lock(hashtextextended($1, 0))', ['team06/import/' + hash]);
}

async function existingUpload(db, hash, input) {
  const existing = await one(db, `
    SELECT d.doc_id, d.file_path, j.job_id, j.status,
           d.uploaded_by=$2 AND d.scheme_id=$3 AND d.title=$4
             AND d.circular_no IS NOT DISTINCT FROM $5
             AND d.issue_date IS NOT DISTINCT FROM $6::date
             AND d.source_url IS NOT DISTINCT FROM $7 AS same_request
    FROM document d LEFT JOIN ingestion_job j USING (doc_id)
    WHERE d.content_hash=$1
  `, [hash, input.userId, input.schemeId, input.title, input.circularNo ?? null, input.issueDate ?? null, input.sourceUrl ?? null]);
  if (!existing) return null;
  if (!existing.same_request || !existing.job_id) {
    throw new PersistenceError('DUPLICATE', 'A document with this content already exists');
  }
  return existing;
}

export class E3Repository {
  constructor({ pool, pdfRoot, indexRoot }) {
    this.pool = pool;
    this.pdfRoot = pdfRoot;
    this.indexRoot = indexRoot;
  }

  async createUser({ username, passwordHash }) {
    if ([username, passwordHash].some((value) => typeof value !== 'string' || !value.trim() || value.includes('\0'))) {
      throw new PersistenceError('INVALID_USER', 'Invalid account data');
    }
    return one(this.pool, `
      INSERT INTO app_user (username,password_hash) VALUES ($1,$2) RETURNING ${PROFILE}
    `, [username, passwordHash]).catch((error) => {
      if (error.code === '23505') throw new PersistenceError('DUPLICATE', 'Username already exists');
      if (['23502', '23514', '22021'].includes(error.code)) {
        throw new PersistenceError('INVALID_USER', 'Invalid account data');
      }
      // PostgreSQL constraint diagnostics can contain the supplied password hash.
      throw new PersistenceError('PERSISTENCE_UNAVAILABLE', 'Account could not be created');
    });
  }

  findUserByUsername(username) {
    return one(this.pool, 'SELECT * FROM e3_login_lookup($1)', [username]);
  }

  findUserById(userId) {
    return one(this.pool, `SELECT ${PROFILE} FROM app_user WHERE user_id=$1`, [userId]);
  }

  async startSession(tokenId, userId, expiresAt) {
    await this.pool.query('DELETE FROM e4_session WHERE expires_at <= now()');
    await this.pool.query('INSERT INTO e4_session (token_id,user_id,expires_at) VALUES ($1,$2,$3)',
      [tokenId, userId, expiresAt]);
  }

  findSession(tokenId, userId) {
    return one(this.pool, `
      SELECT u.user_id,u.username,u.role,u.dept_id
      FROM e4_session j JOIN app_user u USING (user_id)
      WHERE j.token_id=$1 AND j.user_id=$2 AND j.expires_at>now()
    `, [tokenId, userId]);
  }

  endSession(tokenId, userId) {
    return this.pool.query('DELETE FROM e4_session WHERE token_id=$1 AND user_id=$2', [tokenId, userId]);
  }

  async listAccounts(actorId) {
    const { rows: users } = await this.pool.query(`
      SELECT u.user_id,u.username,u.role,u.dept_id FROM app_user u
      WHERE EXISTS (SELECT 1 FROM app_user a WHERE a.user_id=$1 AND a.role='admin')
      ORDER BY u.username
    `, [actorId]);
    const { rows: departments } = await this.pool.query(`
      SELECT dept_id,dept_name,level FROM department
      WHERE EXISTS (SELECT 1 FROM app_user a WHERE a.user_id=$1 AND a.role='admin')
      ORDER BY dept_name,dept_id
    `, [actorId]);
    return { users, departments };
  }

  async provisionDepartment(actorId, { username, passwordHash, deptId }) {
    try {
      return await one(this.pool, 'SELECT * FROM e4_provision_department($1,$2,$3,$4)',
        [actorId, username, passwordHash, deptId]);
    } catch (error) {
      if (error.code === '23505') throw new PersistenceError('DUPLICATE', 'Username already exists');
      if (error.code === '23503') throw new PersistenceError('INVALID_DEPARTMENT', 'Unknown department');
      if (error.code === '42501') throw new PersistenceError('FORBIDDEN', 'Administrator required');
      throw new PersistenceError('PERSISTENCE_UNAVAILABLE', 'Account could not be created');
    }
  }

  canUpload(userId, schemeId) {
    return one(this.pool, CAN_UPLOAD, [userId, schemeId]);
  }

  async listUploadSchemes(userId) {
    const { rows } = await this.pool.query(`
      SELECT s.scheme_id,s.scheme_name,s.state,s.category,s.dept_id,p.dept_name AS department
      FROM scheme s JOIN department p USING (dept_id) CROSS JOIN app_user u
      WHERE u.user_id=$1 AND (u.role='admin' OR (u.role='department' AND u.dept_id=s.dept_id))
      ORDER BY s.scheme_name,s.scheme_id
    `, [userId]);
    return rows;
  }

  async listUploads(userId) {
    const { rows } = await this.pool.query(`
      SELECT d.doc_id,d.scheme_id,d.title,d.source_type,s.scheme_name,j.job_id,j.status AS index_status,
             j.error AS failure_reason,j.updated_at,d.version_status
      FROM document d JOIN scheme s USING (scheme_id)
      LEFT JOIN ingestion_job j USING (doc_id) CROSS JOIN app_user u
      WHERE u.user_id=$1 AND (u.role='admin' OR (
        u.role='department' AND u.dept_id=s.dept_id AND d.uploaded_by=u.user_id
      ))
      ORDER BY j.created_at DESC NULLS LAST,d.doc_id
    `, [userId]);
    return rows;
  }

  async searchMetadata(docIds) {
    const { rows } = await this.pool.query(`
      SELECT doc_id,scheme_name,state,category FROM e3_indexed_document WHERE doc_id=ANY($1::uuid[])
    `, [docIds]);
    return new Map(rows.map((row) => [row.doc_id, row]));
  }

  createBookmark(userId, { docId = null, schemeId = null, pageNo = null }) {
    return one(this.pool, `
      INSERT INTO bookmark (user_id,doc_id,scheme_id,page_no) VALUES ($1,$2,$3,$4)
      RETURNING ${BOOKMARK}
    `, [userId, docId, schemeId, pageNo]).catch((error) => {
      if (error.code === '23505') throw new PersistenceError('DUPLICATE', 'Bookmark already exists');
      throw error;
    });
  }

  async listBookmarks(userId) {
    const { rows } = await this.pool.query(`
      SELECT b.bookmark_id,b.doc_id,b.scheme_id,b.page_no,b.created_at,d.title,d.source_type,
             s.scheme_name
      FROM bookmark b LEFT JOIN document d ON d.doc_id=b.doc_id
      LEFT JOIN scheme s ON s.scheme_id=coalesce(b.scheme_id,d.scheme_id)
      WHERE b.user_id=$1 ORDER BY b.created_at DESC,b.bookmark_id DESC
    `, [userId]);
    return rows;
  }

  async deleteBookmark(userId, bookmarkId) {
    const { rowCount } = await this.pool.query('DELETE FROM bookmark WHERE user_id=$1 AND bookmark_id=$2', [userId, bookmarkId]);
    return rowCount === 1;
  }

  async listFilters() {
    const ids = await committedDocumentIds(this.indexRoot);
    return one(this.pool, `
      SELECT coalesce(array_agg(DISTINCT state ORDER BY state) FILTER (WHERE state IS NOT NULL), '{}') AS state,
             coalesce(array_agg(DISTINCT department ORDER BY department) FILTER (WHERE department IS NOT NULL), '{}') AS department,
             coalesce(array_agg(DISTINCT category ORDER BY category) FILTER (WHERE category IS NOT NULL), '{}') AS category
      FROM e3_indexed_document WHERE doc_id=ANY($1::uuid[])
    `, [ids]);
  }

  async getDocument(docId) {
    const ids = await committedDocumentIds(this.indexRoot);
    return one(this.pool, `
      SELECT doc_id,scheme_id,title,circular_no,issue_date::text,source_type,version_status,
             CASE WHEN version_status='SUPERSEDED' THEN superseded_by END AS current_doc_id
      FROM e3_indexed_document WHERE doc_id=$1 AND doc_id=ANY($2::uuid[])
    `, [docId, ids]);
  }

  async getDocumentPdf(docId) {
    const ids = await committedDocumentIds(this.indexRoot);
    const row = await one(this.pool, `
      SELECT d.file_path FROM document d JOIN e3_indexed_document e USING (doc_id)
      WHERE d.doc_id=$1 AND d.doc_id=ANY($2::uuid[]) AND d.source_type='PDF'
    `, [docId, ids]);
    return row?.file_path ? storedFile(this.pdfRoot, row.file_path) : null;
  }

  async getDocumentStatus(docId, { userId = null, requestId = null } = {}) {
    const ids = await committedDocumentIds(this.indexRoot);
    const status = await one(this.pool, `
      SELECT d.doc_id,j.job_id,j.status AS index_status,d.version_status,
        CASE WHEN u.role='admin' OR (u.role='department' AND u.dept_id=s.dept_id AND d.uploaded_by=u.user_id)
             THEN j.error END AS failure_reason
      FROM document d LEFT JOIN ingestion_job j USING (doc_id)
      JOIN scheme s ON s.scheme_id=d.scheme_id
      LEFT JOIN app_user u ON u.user_id=$3
      WHERE d.doc_id=$1 AND (
        (j.status='INDEXED' AND d.doc_id=ANY($2::uuid[]))
        OR u.role='admin' OR (u.role='department' AND u.dept_id=s.dept_id AND d.uploaded_by=u.user_id)
      )
    `, [docId, ids, userId]);
    if (!status && userId) {
      await this.recordOperationalEvent('DOCUMENT_ACCESS_DENIED', { userId, docId, requestId });
    }
    return status;
  }

  recordOperationalEvent(code, { userId = null, docId = null, requestId = null } = {}) {
    return this.pool.query('SELECT e3_record_api_event($1,$2,$3,$4)', [userId, docId, requestId, code]);
  }

  async createDocumentAndJob(input) {
    const { userId, schemeId, title, circularNo = null, issueDate = null, sourceUrl = null, stagedName, requestId = null } = input;
    if (!await one(this.pool, CAN_UPLOAD, [userId, schemeId])) {
      await this.recordOperationalEvent('DEPARTMENT_SCOPE_DENIED', { userId, requestId });
      throw new PersistenceError('FORBIDDEN', 'Upload is outside the account department scope');
    }
    let staged;
    try {
      staged = await stagedPdf(this.pdfRoot, stagedName);
    } catch {
      await this.recordOperationalEvent('UPLOAD_REJECTED', { userId, requestId });
      throw new PersistenceError('INVALID_STAGED_PDF', 'A validated staged PDF is required');
    }
    try {
      return await transaction(this.pool, async (db) => {
        await contentLock(db, staged.hash);
        if (!await one(db, CAN_UPLOAD, [userId, schemeId])) {
          throw new PersistenceError('FORBIDDEN', 'Upload is outside the account department scope');
        }
        const duplicate = await existingUpload(db, staged.hash, input);
        if (duplicate) {
          const { doc_id, job_id, status, file_path } = duplicate;
          if (file_path === 'uploads/' + staged.hash + '.pdf') await promotePdf(this.pdfRoot, staged);
          return { doc_id, job_id, status, replayed: true };
        }
        const filePath = await promotePdf(this.pdfRoot, staged);
        const document = await one(db, `
          INSERT INTO document (uploaded_by,scheme_id,title,circular_no,issue_date,source_url,file_path,source_type,content_hash)
          SELECT u.user_id,s.scheme_id,$3,$4,$5::date,$6,$7,'PDF',$8
          FROM app_user u CROSS JOIN scheme s
          WHERE u.user_id=$1 AND s.scheme_id=$2
            AND (u.role='admin' OR (u.role='department' AND u.dept_id=s.dept_id))
          RETURNING doc_id
        `, [userId, schemeId, title, circularNo, issueDate, sourceUrl, filePath, staged.hash]);
        if (!document) throw new PersistenceError('FORBIDDEN', 'Upload is outside the account department scope');
        const job = await one(db, `
          INSERT INTO ingestion_job (doc_id) VALUES ($1) RETURNING job_id,status
        `, [document.doc_id]);
        return { ...document, ...job, replayed: false };
      });
    } catch (error) {
      if (error instanceof PersistenceError && error.code === 'DUPLICATE') throw error;
      const event = error.code === 'FORBIDDEN' ? 'DEPARTMENT_SCOPE_DENIED' : 'INGESTION_TRANSACTION_FAILED';
      await this.recordOperationalEvent(event, { userId, requestId }).catch(() => {});
      throw error;
    } finally {
      try {
        await unlink(staged.path);
      } catch (error) {
        if (error.code !== 'ENOENT') {
          await this.recordOperationalEvent('STORAGE_RECONCILIATION_REQUIRED', { userId, requestId }).catch(() => {});
        }
      }
    }
  }

  async reconcileStorage({ quarantineOrphans = false, repairMissingJobs = false } = {}) {
    const report = { orphan_files: [], quarantined: [], missing_files: [], missing_jobs: [], repaired_jobs: [] };
    const { rows } = await this.pool.query(`
      SELECT d.doc_id,d.file_path,d.content_hash,j.job_id
      FROM document d LEFT JOIN ingestion_job j USING (doc_id) WHERE d.source_type='PDF'
    `);
    for (const row of rows) {
      let file = row.file_path ? await storedFile(this.pdfRoot, row.file_path) : null;
      if (!file) report.missing_files.push(row.doc_id);
      if (!row.job_id) {
        report.missing_jobs.push(row.doc_id);
        if (repairMissingJobs && file && row.content_hash) {
          const repaired = await transaction(this.pool, async (db) => {
            await contentLock(db, row.content_hash);
            const current = await one(db, `
              SELECT file_path,content_hash FROM document WHERE doc_id=$1 AND source_type='PDF'
            `, [row.doc_id]);
            if (!current?.file_path || current.content_hash !== row.content_hash) return null;
            file = await storedFile(this.pdfRoot, current.file_path);
            if (!file || sha256(await readFile(file)) !== current.content_hash) return null;
            return one(db, `
              INSERT INTO ingestion_job (doc_id) VALUES ($1)
              ON CONFLICT (doc_id) DO NOTHING RETURNING job_id
            `, [row.doc_id]);
          });
          if (repaired) report.repaired_jobs.push(row.doc_id);
        }
      }
    }
    const referenced = new Set(rows.map((row) => row.file_path));
    for (const filePath of await managedPdfs(this.pdfRoot)) {
      if (referenced.has(filePath)) continue;
      await transaction(this.pool, async (db) => {
        const hash = filePath.split('/').at(-1).slice(0, -4);
        await contentLock(db, hash);
        if (await one(db, 'SELECT doc_id FROM document WHERE file_path=$1', [filePath])) return;
        report.orphan_files.push(filePath);
        if (quarantineOrphans && await quarantinePdf(this.pdfRoot, filePath)) report.quarantined.push(filePath);
      });
    }
    if (report.orphan_files.length || report.missing_files.length || report.missing_jobs.length) {
      await this.recordOperationalEvent('STORAGE_RECONCILIATION_REQUIRED');
    }
    return report;
  }
}

export function createE3Repository({ connectionString, pdfRoot, indexRoot }) {
  if (!connectionString || !pdfRoot || !indexRoot) throw new Error('E4 database URL, PDF root, and index root are required');
  return new E3Repository({ pool: new Pool({ connectionString, connectionTimeoutMillis: 2000, statement_timeout: 5000 }),
    pdfRoot, indexRoot });
}
