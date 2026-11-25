BEGIN;

ALTER TABLE app_user ALTER COLUMN role SET DEFAULT 'citizen';

CREATE VIEW e3_indexed_document AS
SELECT d.doc_id, d.scheme_id, d.title, d.circular_no, d.issue_date, d.source_url,
       d.source_type, d.version_status, d.superseded_by, j.job_id,
       s.state, s.category, p.dept_name AS department
FROM document d
JOIN ingestion_job j USING (doc_id)
JOIN scheme s USING (scheme_id)
JOIN department p USING (dept_id)
WHERE j.status = 'INDEXED';

CREATE FUNCTION e3_login_lookup(login_name text)
RETURNS TABLE (user_id uuid, username text, role text, dept_id integer, password_hash text)
LANGUAGE sql SECURITY DEFINER STABLE AS $$
    SELECT u.user_id,u.username,u.role,u.dept_id,u.password_hash FROM app_user u
    WHERE u.username = login_name
$$;

CREATE FUNCTION e3_record_api_event(actor uuid, target uuid, request_uuid uuid, event_code text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    IF event_code IS NULL OR event_code NOT IN (
        'AUTH_DENIED', 'DEPARTMENT_SCOPE_DENIED', 'DOCUMENT_ACCESS_DENIED',
        'UPLOAD_REJECTED', 'INGESTION_TRANSACTION_FAILED', 'STORAGE_RECONCILIATION_REQUIRED'
    ) THEN
        RAISE EXCEPTION 'Unknown operational event code' USING ERRCODE = '22023';
    END IF;
    INSERT INTO operational_log (user_id,doc_id,event,details)
    VALUES ((SELECT user_id FROM app_user WHERE user_id=actor),
            (SELECT doc_id FROM document WHERE doc_id=target), event_code,
            jsonb_strip_nulls(jsonb_build_object('request_id',request_uuid)));
END;
$$;

CREATE FUNCTION e3_log_ingestion_error()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    INSERT INTO operational_log (doc_id,event,details)
    VALUES (NEW.doc_id, 'INGESTION_ERROR', jsonb_build_object(
        'job_id', NEW.job_id, 'status', NEW.status, 'reason',
        CASE WHEN NEW.error IN ('PDF_UNREADABLE','PDF_CORRUPT','PDF_ENCRYPTED','PDF_NO_TEXT',
             'PDF_LIMIT','NO_SEARCHABLE_TOKENS','STAGE_LIMIT','TASK_LIMIT','WORKER_EXIT',
             'WORKER_TIMEOUT','WORKER_ERROR','INVALID_JOB_SOURCE','MISSING_SCHEME_RECORD','LEASE_EXPIRED')
             THEN NEW.error ELSE 'UNCLASSIFIED' END
    ));
    RETURN NEW;
END;
$$;

CREATE TRIGGER ingestion_error_event AFTER UPDATE ON ingestion_job FOR EACH ROW
    WHEN (NEW.error IS NOT NULL AND
          (NEW.error IS DISTINCT FROM OLD.error OR NEW.attempts IS DISTINCT FROM OLD.attempts))
    EXECUTE FUNCTION e3_log_ingestion_error();

DO $$
DECLARE app_schema text := current_schema();
BEGIN
    EXECUTE format('ALTER FUNCTION e3_login_lookup(text) SET search_path TO pg_catalog, %I, pg_temp', app_schema);
    EXECUTE format('ALTER FUNCTION e3_record_api_event(uuid,uuid,uuid,text) SET search_path TO pg_catalog, %I, pg_temp', app_schema);
    EXECUTE format('ALTER FUNCTION e3_log_ingestion_error() SET search_path TO pg_catalog, %I, pg_temp', app_schema);
END
$$;

REVOKE ALL ON FUNCTION e3_login_lookup(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION e3_record_api_event(uuid,uuid,uuid,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION e3_log_ingestion_error() FROM PUBLIC;

COMMIT;
