BEGIN;

CREATE FUNCTION pg_temp.legacy_uuid(kind text, id integer) RETURNS uuid
LANGUAGE sql IMMUTABLE STRICT
RETURN md5('team06/legacy/' || kind || '/' || id::text)::uuid;

ALTER TABLE document
    DROP CONSTRAINT document_scheme_id_fkey,
    DROP CONSTRAINT document_uploaded_by_fkey,
    DROP CONSTRAINT document_superseded_by_fkey;
ALTER TABLE bookmark
    DROP CONSTRAINT bookmark_user_id_fkey,
    DROP CONSTRAINT bookmark_doc_id_fkey;
ALTER TABLE operational_log
    DROP CONSTRAINT operational_log_user_id_fkey,
    DROP CONSTRAINT operational_log_doc_id_fkey;

ALTER TABLE scheme ALTER COLUMN scheme_id DROP IDENTITY;
ALTER TABLE app_user ALTER COLUMN user_id DROP IDENTITY;
ALTER TABLE document ALTER COLUMN doc_id DROP IDENTITY;
ALTER TABLE scheme
    ALTER COLUMN scheme_id TYPE uuid USING pg_temp.legacy_uuid('scheme', scheme_id),
    ALTER COLUMN scheme_id SET DEFAULT gen_random_uuid();
ALTER TABLE app_user
    ALTER COLUMN user_id TYPE uuid USING pg_temp.legacy_uuid('user', user_id),
    ALTER COLUMN user_id SET DEFAULT gen_random_uuid();
ALTER TABLE document
    ALTER COLUMN doc_id TYPE uuid USING pg_temp.legacy_uuid('document', doc_id),
    ALTER COLUMN doc_id SET DEFAULT gen_random_uuid(),
    ALTER COLUMN scheme_id TYPE uuid USING pg_temp.legacy_uuid('scheme', scheme_id),
    ALTER COLUMN uploaded_by TYPE uuid USING pg_temp.legacy_uuid('user', uploaded_by),
    ALTER COLUMN superseded_by TYPE uuid USING pg_temp.legacy_uuid('document', superseded_by);
ALTER TABLE bookmark
    ALTER COLUMN user_id TYPE uuid USING pg_temp.legacy_uuid('user', user_id),
    ALTER COLUMN doc_id TYPE uuid USING pg_temp.legacy_uuid('document', doc_id);
ALTER TABLE operational_log
    ALTER COLUMN user_id TYPE uuid USING pg_temp.legacy_uuid('user', user_id),
    ALTER COLUMN doc_id TYPE uuid USING pg_temp.legacy_uuid('document', doc_id);

ALTER TABLE document
    ADD FOREIGN KEY (scheme_id) REFERENCES scheme,
    ADD FOREIGN KEY (uploaded_by) REFERENCES app_user,
    ADD FOREIGN KEY (superseded_by) REFERENCES document,
    ADD COLUMN source_type text CHECK (source_type IN ('PDF', 'CSV')),
    ADD COLUMN version_status text NOT NULL DEFAULT 'UNKNOWN'
        CHECK (version_status IN ('UNKNOWN', 'CURRENT', 'SUPERSEDED'));
UPDATE document SET source_type = CASE WHEN file_path IS NULL THEN 'CSV' ELSE 'PDF' END;
ALTER TABLE document ALTER COLUMN source_type SET NOT NULL;
ALTER TABLE bookmark
    ADD FOREIGN KEY (user_id) REFERENCES app_user,
    ADD FOREIGN KEY (doc_id) REFERENCES document;
ALTER TABLE operational_log
    ADD FOREIGN KEY (user_id) REFERENCES app_user,
    ADD FOREIGN KEY (doc_id) REFERENCES document;

CREATE TABLE index_job (
    job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id uuid NOT NULL UNIQUE REFERENCES document,
    status text NOT NULL DEFAULT 'QUEUED'
        CHECK (status IN ('QUEUED', 'PARSING', 'COMMITTING', 'INDEXED', 'FAILED')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts > 0 AND attempts <= max_attempts),
    lease_token uuid,
    lease_until timestamptz,
    available_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    reason_code text,
    CHECK ((status IN ('PARSING', 'COMMITTING')) = (lease_token IS NOT NULL)),
    CHECK ((lease_token IS NULL) = (lease_until IS NULL))
);
CREATE INDEX index_job_queue_idx ON index_job (available_at, created_at, job_id)
    WHERE status = 'QUEUED';

COMMIT;
