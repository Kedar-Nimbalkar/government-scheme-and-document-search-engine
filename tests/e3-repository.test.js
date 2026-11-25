const assert = require('node:assert/strict');
const { randomUUID, createHash } = require('node:crypto');
const fs = require('node:fs/promises');
const { dirname, join, resolve } = require('node:path');
const { tmpdir } = require('node:os');
const { test } = require('node:test');
const { Pool } = require('pg');

const url = process.env.E3_TEST_DATABASE_URL;
const fixture = resolve(__dirname, '../fixtures/e1-synthetic/digital-and-mixed.pdf');

test('E4 repository works with PostgreSQL runtime grants', { skip: !url }, async (t) => {
  const schema = 'e3_node_' + randomUUID().replaceAll('-', '');
  const root = await fs.mkdtemp(join(tmpdir(), 'team06-e3-'));
  const admin = new Pool({ connectionString: url, options: '-c search_path=' + schema });
  const e4Options = { connectionString: url, options: '-c search_path=' + schema + ' -c role=team06_e4' };
  const pool = new Pool(e4Options);
  const e1 = new Pool({ connectionString: url, options: '-c search_path=' + schema + ' -c role=team06_e1' });
  const e2 = new Pool({ connectionString: url, options: '-c search_path=' + schema + ' -c role=team06_e2' });
  t.after(async () => {
    await Promise.all([pool.end(), e1.end(), e2.end()]);
    await admin.query(`DROP SCHEMA IF EXISTS "${schema}" CASCADE`);
    await admin.end();
    assert.equal(dirname(resolve(root)), resolve(tmpdir()));
    await fs.rm(root, { recursive: true });
  });
  await admin.query(`CREATE SCHEMA "${schema}"`);
  const migrations = resolve(__dirname, '../db/migrations');
  for (const migration of (await fs.readdir(migrations)).filter((name) => name.endsWith('.sql')).sort()) {
    await admin.query(await fs.readFile(join(migrations, migration), 'utf8'));
  }
  await admin.query(await fs.readFile(resolve(__dirname, '../db/runtime-roles.sql'), 'utf8'));
  const { E3Repository } = await import('../apps/api/src/persistence/repository.js');
  const pdfRoot = join(root, 'pdfs');
  const indexRoot = join(root, 'index');
  const stagedRoot = join(pdfRoot, '.staging');
  await fs.mkdir(stagedRoot, { recursive: true });
  await fs.mkdir(indexRoot);
  const repository = new E3Repository({ pool, pdfRoot, indexRoot });
  const bytes = await fs.readFile(fixture);
  const stage = (name, content = bytes) => fs.writeFile(join(stagedRoot, name), content);
  const hash = (content) => createHash('sha256').update(content).digest('hex');
  const query = async (statement, values) => (await admin.query(statement, values)).rows[0];

  const accounts = await Promise.allSettled([
    repository.createUser({ username: 'test-citizen', passwordHash: 'internal-test-hash' }),
    repository.createUser({ username: 'test-citizen', passwordHash: 'internal-test-hash' }),
  ]);
  const citizen = accounts.find(({ status }) => status === 'fulfilled').value;
  assert.equal(accounts.find(({ status }) => status === 'rejected').reason.code, 'DUPLICATE');
  assert.deepEqual(Object.keys(citizen).sort(), ['dept_id', 'role', 'user_id', 'username']);
  assert.equal(citizen.role, 'citizen');
  assert.equal((await repository.findUserByUsername('test-citizen')).password_hash, 'internal-test-hash');
  await assert.rejects(repository.createUser({ username: '', passwordHash: 'private-failed-hash' }),
    (error) => error.code === 'INVALID_USER' && error.detail === undefined
      && !String(error).includes('private-failed-hash'));
  assert.equal(await repository.findUserByUsername("' OR TRUE --"), null);
  assert.deepEqual(await repository.findUserById(citizen.user_id), citizen);
  await assert.rejects(pool.query('SELECT password_hash FROM app_user'), { code: '42501' });
  await assert.rejects(e2.query('SELECT * FROM app_user'), { code: '42501' });
  await assert.rejects(pool.query("INSERT INTO app_user (username,password_hash,role) VALUES ('escalate','hash','admin')"),
    { code: '42501' });

  const { dept_id: home } = await query("INSERT INTO department (dept_name,level) VALUES ('Home','State') RETURNING dept_id");
  const { dept_id: other } = await query("INSERT INTO department (dept_name,level) VALUES ('Other','State') RETURNING dept_id");
  const { scheme_id: scheme } = await query(
    "INSERT INTO scheme (dept_id,scheme_name,state,category) VALUES ($1,'Housing','Karnataka','Housing') RETURNING scheme_id", [home],
  );
  const { user_id: deptUser } = await query(
    "INSERT INTO app_user (username,password_hash,role,dept_id) VALUES ('dept','test-only','department',$1) RETURNING user_id",
    [home],
  );
  const { user_id: wrongUser } = await query(
    "INSERT INTO app_user (username,password_hash,role,dept_id) VALUES ('wrong-dept','test-only','department',$1) RETURNING user_id",
    [other],
  );
  await Promise.all([stage('first.pdf'), stage('second.pdf'), stage('denied.pdf')]);
  const input = { userId: deptUser, schemeId: scheme, title: 'Synthetic upload', stagedName: 'first.pdf' };
  await assert.rejects(repository.createDocumentAndJob({ ...input, userId: wrongUser, stagedName: 'denied.pdf' }),
    { code: 'FORBIDDEN' });
  const uploads = await Promise.all([
    repository.createDocumentAndJob(input),
    repository.createDocumentAndJob({ ...input, stagedName: 'second.pdf' }),
  ]);
  assert.deepEqual(uploads.map((entry) => entry.replayed).sort(), [false, true]);
  const [upload] = uploads;
  assert.equal(upload.job_id, uploads[1].job_id);
  assert.equal(upload.doc_id, uploads[1].doc_id);
  assert.equal(upload.status, 'QUEUED');
  assert.equal((await query('SELECT count(*)::int AS n FROM ingestion_job')).n, 1);
  assert.equal(await repository.getDocumentStatus(upload.doc_id, { userId: wrongUser }), null);
  assert.equal((await repository.getDocumentStatus(upload.doc_id, { userId: deptUser })).index_status, 'QUEUED');
  assert.equal(await repository.getDocument(upload.doc_id), null);

  const bookmark = await repository.createBookmark(citizen.user_id, { docId: upload.doc_id });
  await repository.createBookmark(citizen.user_id, { schemeId: scheme });
  await assert.rejects(repository.createBookmark(citizen.user_id, { docId: upload.doc_id }), { code: 'DUPLICATE' });
  const freshPool = new Pool(e4Options);
  try {
    const fresh = new E3Repository({ pool: freshPool, pdfRoot, indexRoot });
    assert.equal((await fresh.listBookmarks(citizen.user_id)).length, 2);
    assert.deepEqual(await fresh.listBookmarks(wrongUser), []);
    assert.equal(await fresh.deleteBookmark(wrongUser, bookmark.bookmark_id), false);
    assert.equal(await fresh.deleteBookmark(citizen.user_id, bookmark.bookmark_id), true);
  } finally {
    await freshPool.end();
  }

  await assert.rejects(pool.query("UPDATE ingestion_job SET status='INDEXED' WHERE doc_id=$1", [upload.doc_id]), { code: '42501' });
  await assert.rejects(pool.query("INSERT INTO ingestion_job (doc_id,status) VALUES ($1,'INDEXED')", [upload.doc_id]), { code: '42501' });
  await assert.rejects(e2.query('SELECT file_path FROM document'), { code: '42501' });
  await e1.query("UPDATE index_job SET status='INDEXED' WHERE doc_id=$1", [upload.doc_id]);
  assert.equal(await repository.getDocument(upload.doc_id), null);
  await fs.writeFile(join(indexRoot, 'manifest.json'), JSON.stringify({
    schema_version: 2, tokenizer_version: 'nfkc-casefold-v1', committed_doc_ids: [upload.doc_id],
  }));
  await admin.query("UPDATE document SET version_status='SUPERSEDED' WHERE doc_id=$1", [upload.doc_id]);
  assert.equal((await repository.getDocument(upload.doc_id)).version_status, 'SUPERSEDED');
  assert.deepEqual(await repository.listFilters(), { state: ['Karnataka'], department: ['Home'], category: ['Housing'] });
  const streamPath = await repository.getDocumentPdf(upload.doc_id);
  assert.deepEqual(await fs.readFile(streamPath), bytes);
  assert.equal((await repository.getDocumentStatus(upload.doc_id)).index_status, 'INDEXED');
  // Simulate external storage loss in the test schema, then retry the upload.
  await fs.rename(streamPath, join(root, 'lost-test-copy.pdf'));
  await stage('restore.pdf');
  const restored = await repository.createDocumentAndJob({ ...input, stagedName: 'restore.pdf' });
  assert.equal(restored.job_id, upload.job_id);
  assert.equal(restored.replayed, true);
  assert.deepEqual(await fs.readFile(streamPath), bytes);

  const failBytes = Buffer.concat([bytes, Buffer.from('\n% reject transaction\n')]);
  await stage('rollback.pdf', failBytes);
  await admin.query(`
    CREATE FUNCTION test_reject_job() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'test job failure'; END $$;
    CREATE TRIGGER test_reject_job BEFORE INSERT ON ingestion_job FOR EACH ROW EXECUTE FUNCTION test_reject_job();
  `);
  await assert.rejects(repository.createDocumentAndJob({ ...input, stagedName: 'rollback.pdf' }), { code: 'P0001' });
  assert.equal(await query('SELECT doc_id FROM document WHERE content_hash=$1', [hash(failBytes)]), undefined);
  await assert.rejects(fs.stat(join(stagedRoot, 'rollback.pdf')), { code: 'ENOENT' });
  await admin.query('DROP TRIGGER test_reject_job ON ingestion_job');

  const recoverBytes = Buffer.concat([bytes, Buffer.from('\n% repair queued job\n')]);
  const recoverHash = hash(recoverBytes);
  await fs.mkdir(join(pdfRoot, 'imports'));
  await fs.writeFile(join(pdfRoot, 'imports', recoverHash + '.pdf'), recoverBytes);
  const { doc_id: repairId } = await query(`
    INSERT INTO document (scheme_id,title,source_type,file_path,content_hash)
    VALUES ($1,'Repair','PDF',$2,$3) RETURNING doc_id
  `, [scheme, 'imports/' + recoverHash + '.pdf', recoverHash]);
  const report = await repository.reconcileStorage({ quarantineOrphans: true, repairMissingJobs: true });
  assert.deepEqual(report.repaired_jobs, [repairId]);
  assert.deepEqual(report.quarantined, ['uploads/' + hash(failBytes) + '.pdf']);
  assert.deepEqual(await fs.readFile(streamPath), bytes);
  assert.equal((await fs.readdir(join(pdfRoot, '.quarantine'))).length, 1);
  await assert.rejects(repository.recordOperationalEvent('arbitrary token-like message'), { code: '22023' });
  const { rows: events } = await admin.query('SELECT event,details FROM operational_log');
  assert.ok(events.some((entry) => entry.event === 'DEPARTMENT_SCOPE_DENIED'));
  assert.ok(events.some((entry) => entry.event === 'INGESTION_TRANSACTION_FAILED'));
  assert.ok(events.every((entry) => !JSON.stringify(entry).includes('internal-test-hash')));
});
