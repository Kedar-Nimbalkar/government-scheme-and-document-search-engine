-- E3-owned prepared statements. Execute this file once per database session.
-- The writer holds this session lock in addition to its local filesystem lock.
PREPARE acquire_index_writer AS
SELECT pg_try_advisory_lock(hashtextextended('team06/index-writer/' || current_schema(), 0));

PREPARE claim_index_job(interval) AS
WITH candidate AS (
    SELECT job_id FROM index_job
    WHERE status = 'QUEUED' AND available_at <= now() AND attempts < max_attempts
      AND $1 > interval '0'
    ORDER BY available_at, created_at, job_id
    FOR UPDATE SKIP LOCKED LIMIT 1
)
UPDATE index_job j SET status = 'PARSING', attempts = attempts + 1,
    lease_token = gen_random_uuid(), lease_until = now() + $1, reason_code = NULL
FROM candidate c, document d
WHERE j.job_id = c.job_id AND d.doc_id = j.doc_id
RETURNING j.job_id, j.doc_id, j.attempts, j.lease_token, j.lease_until,
    d.file_path, d.source_type;

PREPARE renew_index_job(uuid, uuid, interval) AS
UPDATE index_job SET lease_until = now() + $3
WHERE job_id = $1 AND lease_token = $2 AND lease_until > now()
  AND status IN ('PARSING', 'COMMITTING') AND $3 > interval '0'
RETURNING job_id;

PREPARE begin_index_commit(uuid, uuid) AS
UPDATE index_job SET status = 'COMMITTING'
WHERE job_id = $1 AND lease_token = $2 AND lease_until > now() AND status = 'PARSING'
RETURNING job_id;

PREPARE finish_index_commit(uuid, uuid) AS
UPDATE index_job SET status = 'INDEXED', lease_token = NULL, lease_until = NULL
WHERE job_id = $1 AND lease_token = $2 AND status = 'COMMITTING'
RETURNING job_id;

-- For an ordinary failure while parsing. Committing jobs reconcile against the manifest.
PREPARE fail_index_parse(uuid, uuid, boolean, text, interval) AS
UPDATE index_job SET
    status = CASE WHEN $3 AND attempts < max_attempts THEN 'QUEUED' ELSE 'FAILED' END,
    lease_token = NULL, lease_until = NULL, reason_code = $4, available_at = now() + $5
WHERE job_id = $1 AND lease_token = $2 AND lease_until > now()
  AND status = 'PARSING' AND $5 >= interval '0'
RETURNING job_id, status;

-- Run while holding the same exclusive ownership as the index writer.
PREPARE list_expired_index_jobs AS
SELECT job_id, doc_id FROM index_job
WHERE status IN ('PARSING', 'COMMITTING') AND lease_until <= now()
ORDER BY lease_until, job_id;

-- Read-only delay before the next eligible recovery while the writer owns the index.
PREPARE next_index_recovery AS
SELECT EXTRACT(EPOCH FROM min(lease_until) - now()) FROM index_job
WHERE status IN ('PARSING', 'COMMITTING');

-- $2 says whether the validated manifest already contains this document UUID.
PREPARE reconcile_index_job(uuid, boolean, interval) AS
UPDATE index_job SET
    status = CASE WHEN $2 THEN 'INDEXED'
                  WHEN attempts < max_attempts THEN 'QUEUED' ELSE 'FAILED' END,
    lease_token = NULL, lease_until = NULL,
    reason_code = CASE WHEN $2 THEN NULL ELSE 'LEASE_EXPIRED' END,
    available_at = now() + $3
WHERE job_id = $1 AND status IN ('PARSING', 'COMMITTING') AND lease_until <= now()
  AND $2 IS NOT NULL AND $3 >= interval '0'
RETURNING job_id, status;
