BEGIN;

ALTER TABLE scheme ADD CONSTRAINT scheme_identity_unique UNIQUE (dept_id, scheme_name, state);
ALTER TABLE department ADD CONSTRAINT department_name_nonempty CHECK (btrim(dept_name) <> '');
ALTER TABLE scheme ADD CONSTRAINT scheme_fields_nonempty
    CHECK (btrim(scheme_name) <> '' AND btrim(category) <> '' AND btrim(state) <> '');
ALTER TABLE app_user ADD CONSTRAINT user_fields_nonempty
    CHECK (btrim(username) <> '' AND btrim(password_hash) <> '');

ALTER TABLE document
    ADD COLUMN source_url text,
    ADD COLUMN content_hash text UNIQUE,
    ADD CONSTRAINT document_url_http CHECK (source_url ~ '^https?://[^[:space:]]+$'),
    ADD CONSTRAINT document_hash_sha256 CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT document_title_nonempty CHECK (btrim(title) <> ''),
    ADD CONSTRAINT document_local_relative_path CHECK (
        btrim(file_path) <> '' AND strpos(file_path, chr(92)) = 0
        AND file_path !~ '(^/|(^|/)\.\.(/|$)|:)'
    ) NOT VALID,
    ADD CONSTRAINT document_pdf_storage CHECK (
        source_type <> 'PDF' OR (file_path IS NOT NULL AND content_hash IS NOT NULL)
    ) NOT VALID;
COMMENT ON COLUMN document.status IS 'Legacy status preserved for existing rows. Use version_status for current reads.';

ALTER TABLE bookmark
    ALTER COLUMN doc_id DROP NOT NULL,
    ADD COLUMN scheme_id uuid REFERENCES scheme ON DELETE RESTRICT,
    DROP CONSTRAINT bookmark_user_id_doc_id_page_no_key,
    ADD CONSTRAINT bookmark_exactly_one_target CHECK (
        (doc_id IS NOT NULL AND scheme_id IS NULL)
        OR (doc_id IS NULL AND scheme_id IS NOT NULL AND page_no IS NULL)
    );
CREATE UNIQUE INDEX bookmark_document_target_unique
    ON bookmark (user_id, doc_id, page_no) NULLS NOT DISTINCT WHERE doc_id IS NOT NULL;
CREATE UNIQUE INDEX bookmark_scheme_target_unique
    ON bookmark (user_id, scheme_id) WHERE scheme_id IS NOT NULL;
CREATE INDEX bookmark_user_idx ON bookmark (user_id);
CREATE INDEX bookmark_document_idx ON bookmark (doc_id);
CREATE INDEX bookmark_scheme_idx ON bookmark (scheme_id);

CREATE TABLE audit_log (
    log_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES app_user ON DELETE RESTRICT,
    doc_id uuid REFERENCES document ON DELETE RESTRICT,
    action text NOT NULL CHECK (btrim(action) <> ''),
    old_value jsonb,
    new_value jsonb,
    logged_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_user_idx ON audit_log (user_id);
CREATE INDEX audit_document_idx ON audit_log (doc_id);

ALTER TABLE index_job RENAME TO ingestion_job;
ALTER TABLE ingestion_job RENAME COLUMN reason_code TO error;
ALTER TABLE ingestion_job ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX ingestion_status_updated_idx ON ingestion_job (status, updated_at);
CREATE INDEX ingestion_active_lease_idx ON ingestion_job (lease_until, job_id)
    WHERE status IN ('PARSING', 'COMMITTING');

CREATE FUNCTION stamp_ingestion_job_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;
CREATE TRIGGER ingestion_job_updated_at BEFORE UPDATE ON ingestion_job
    FOR EACH ROW EXECUTE FUNCTION stamp_ingestion_job_update();

-- Single-table, automatically updatable view for the existing E1 prepared SQL.
CREATE VIEW index_job AS
SELECT job_id, doc_id, status, attempts, max_attempts, lease_token, lease_until,
       available_at, created_at, error AS reason_code
FROM ingestion_job;

CREATE TABLE corpus_dataset (
    dataset_id text NOT NULL,
    revision text NOT NULL,
    license text NOT NULL CHECK (btrim(license) <> ''),
    PRIMARY KEY (dataset_id, revision)
);
CREATE TABLE document_import (
    doc_id uuid PRIMARY KEY REFERENCES document ON DELETE RESTRICT,
    dataset_id text NOT NULL,
    revision text NOT NULL,
    relative_path text NOT NULL,
    artifact_kind text NOT NULL CHECK (artifact_kind IN ('official_document', 'dataset_snapshot', 'synthetic')),
    evidence_url text CHECK (evidence_url ~ '^https?://[^[:space:]]+$'),
    FOREIGN KEY (dataset_id, revision) REFERENCES corpus_dataset ON DELETE RESTRICT,
    CHECK (artifact_kind = 'synthetic' OR evidence_url IS NOT NULL)
);
CREATE INDEX document_import_dataset_idx ON document_import (dataset_id, revision);
CREATE INDEX document_uploader_idx ON document (uploaded_by);
CREATE INDEX document_supersession_idx ON document (superseded_by);
CREATE INDEX user_department_idx ON app_user (dept_id);
CREATE INDEX operational_log_user_idx ON operational_log (user_id);
CREATE INDEX operational_log_document_idx ON operational_log (doc_id);

COMMIT;
