BEGIN;

CREATE TABLE e4_session (
    token_id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES app_user ON DELETE CASCADE,
    expires_at timestamptz NOT NULL
);
CREATE INDEX e4_session_expiry_idx ON e4_session (expires_at);
CREATE INDEX e4_session_user_idx ON e4_session (user_id);

CREATE OR REPLACE VIEW e3_indexed_document AS
SELECT d.doc_id, d.scheme_id, d.title, d.circular_no, d.issue_date, d.source_url,
       d.source_type, d.version_status, d.superseded_by, j.job_id,
       s.state, s.category, p.dept_name AS department, s.scheme_name
FROM document d
JOIN ingestion_job j USING (doc_id)
JOIN scheme s USING (scheme_id)
JOIN department p USING (dept_id)
WHERE j.status = 'INDEXED';

CREATE FUNCTION e4_provision_department(actor uuid, account_name text, digest text, department_id integer)
RETURNS TABLE (user_id uuid, username text, role text, dept_id integer)
LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM app_user u WHERE u.user_id=actor AND u.role='admin') THEN
        RAISE EXCEPTION 'Administrator required' USING ERRCODE='42501';
    END IF;
    RETURN QUERY INSERT INTO app_user AS u (username, password_hash, role, dept_id)
    VALUES (account_name,digest,'department',department_id)
    RETURNING u.user_id,u.username,u.role,u.dept_id;
END;
$$;

DO $$
BEGIN
    EXECUTE format('ALTER FUNCTION e4_provision_department(uuid,text,text,integer) SET search_path TO pg_catalog, %I, pg_temp',
                   current_schema());
END;
$$;
REVOKE ALL ON FUNCTION e4_provision_department(uuid,text,text,integer) FROM PUBLIC;

COMMIT;
