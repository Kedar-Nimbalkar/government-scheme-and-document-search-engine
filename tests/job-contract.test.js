const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { PGlite } = require('@electric-sql/pglite');

const sql = (path) => readFileSync(join(__dirname, '..', path), 'utf8');

test('PostgreSQL migration preserves links and job SQL enforces bounded retries', async () => {
  const db = new PGlite();
  try {
    await db.exec(sql('db/migrations/001_foundation.sql'));
    await db.exec(`
      INSERT INTO department (dept_name, level) VALUES ('Housing', 'State');
      INSERT INTO scheme (dept_id, scheme_name, category, state)
      VALUES (1, 'Housing grant', 'Housing', 'Karnataka');
      INSERT INTO app_user (username, password_hash, role) VALUES ('reader', 'test-only', 'citizen');
      INSERT INTO document (scheme_id, title, file_path, uploaded_by)
      VALUES (1, 'Circular', 'circular.pdf', 1), (1, 'Summary', NULL, 1);
      INSERT INTO bookmark (user_id, doc_id) VALUES (1, 1);
    `);
    await db.exec(sql('db/migrations/002_contract_v2.sql'));
    await db.exec(sql('packages/contracts/v2/jobs.sql'));
    const { rows: [linked] } = await db.query(`
      SELECT doc_id, source_type, version_status FROM document
      JOIN bookmark USING (doc_id) JOIN app_user USING (user_id)
      WHERE username = 'reader'
    `);
    assert.match(linked.doc_id, /^[a-f0-9-]{36}$/);
    assert.equal(linked.source_type, 'PDF');
    assert.equal(linked.version_status, 'UNKNOWN');
    await db.query('INSERT INTO index_job (doc_id, max_attempts) VALUES ($1, 2)', [linked.doc_id]);
    await assert.rejects(db.query('INSERT INTO index_job (doc_id) VALUES ($1)', [linked.doc_id]), { code: '23505' });

    const { rows: [first] } = await db.query("EXECUTE claim_index_job('1 minute')");
    assert.equal(first.attempts, 1);
    assert.equal(first.doc_id, linked.doc_id);
    assert.equal((await db.query("EXECUTE claim_index_job('1 minute')")).rows.length, 0);
    const { rows: [retried] } = await db.query(
      `EXECUTE fail_index_parse('${first.job_id}', '${first.lease_token}', true, 'READ_FAILED', '0 seconds')`,
    );
    assert.equal(retried.status, 'QUEUED');

    const { rows: [second] } = await db.query("EXECUTE claim_index_job('1 minute')");
    assert.equal(second.attempts, 2);
    assert.notEqual(second.lease_token, first.lease_token);
    assert.equal((await db.query(`EXECUTE begin_index_commit('${first.job_id}', '${first.lease_token}')`)).rows.length, 0);
    assert.equal((await db.query(`EXECUTE renew_index_job('${second.job_id}', '${second.lease_token}', '1 minute')`)).rows.length, 1);
    assert.equal((await db.query(`EXECUTE begin_index_commit('${second.job_id}', '${second.lease_token}')`)).rows.length, 1);
    await db.query("UPDATE index_job SET lease_until = now() - interval '1 second' WHERE job_id = $1", [second.job_id]);
    assert.deepEqual((await db.query('EXECUTE list_expired_index_jobs')).rows, [{ job_id: second.job_id, doc_id: second.doc_id }]);
    const { rows: [failed] } = await db.query(`EXECUTE reconcile_index_job('${second.job_id}', false, '0 seconds')`);
    assert.equal(failed.status, 'FAILED');
    assert.equal((await db.query("EXECUTE claim_index_job('1 minute')")).rows.length, 0);

    await db.query("INSERT INTO index_job (doc_id) SELECT doc_id FROM document WHERE source_type = 'CSV'");
    const { rows: [csv] } = await db.query("EXECUTE claim_index_job('1 minute')");
    assert.equal((await db.query(`EXECUTE begin_index_commit('${csv.job_id}', '${csv.lease_token}')`)).rows.length, 1);
    await db.query("UPDATE index_job SET lease_until = now() - interval '1 second' WHERE job_id = $1", [csv.job_id]);
    const { rows: [indexed] } = await db.query(`EXECUTE reconcile_index_job('${csv.job_id}', true, '0 seconds')`);
    assert.equal(indexed.status, 'INDEXED');
  } finally {
    await db.close();
  }
});
