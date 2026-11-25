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
      SELECT ${BOOKMARK} FROM bookmark WHERE user_id=$1 ORDER BY created_at DESC, bookmark_id DESC
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
      SELECT d.doc_id,j.job_id,j.status AS index_status,d.version_status
      FROM document d LEFT JOIN ingestion_job j USING (doc_id)
      WHERE d.doc_id=$1 AND (
        (j.status='INDEXED' AND d.doc_id=ANY($2::uuid[]))
        OR EXISTS (
          SELECT 1 FROM app_user u JOIN scheme s ON s.scheme_id=d.scheme_id
          WHERE u.user_id=$3 AND (u.role='admin' OR (u.role='department' AND u.dept_id=s.dept_id))
        )
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
  return new E3Repository({ pool: new Pool({ connectionString }), pdfRoot, indexRoot });
}
