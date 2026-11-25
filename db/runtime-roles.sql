-- Apply after 004 with the application schema first in search_path.
-- Run as the schema owner with permission to create NOLOGIN roles.
BEGIN;
DO $$
DECLARE
    service_role text;
    app_schema text := current_schema();
BEGIN
    IF to_regclass('e3_indexed_document') IS NULL THEN
        RAISE EXCEPTION 'Apply migration 004 in the target schema first';
    END IF;
    FOREACH service_role IN ARRAY ARRAY['team06_e1','team06_e2','team06_e4'] LOOP
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname=service_role) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', service_role);
        END IF;
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', app_schema, service_role);
    END LOOP;
END
$$;

GRANT SELECT (doc_id,scheme_id,title,issue_date,source_type,version_status,superseded_by,state,category,department)
    ON e3_indexed_document TO team06_e2;
GRANT SELECT ON e3_indexed_document TO team06_e4;
GRANT SELECT (doc_id,file_path,source_type) ON document TO team06_e1;
GRANT SELECT ON index_job TO team06_e1;
GRANT UPDATE (status,attempts,lease_token,lease_until,reason_code,available_at) ON index_job TO team06_e1;

GRANT INSERT (username,password_hash) ON app_user TO team06_e4;
GRANT SELECT (user_id,username,role,dept_id) ON app_user TO team06_e4;
GRANT EXECUTE ON FUNCTION e3_login_lookup(text) TO team06_e4;
GRANT SELECT (scheme_id,dept_id) ON scheme TO team06_e4;
GRANT SELECT (doc_id,scheme_id,title,circular_no,issue_date,file_path,uploaded_by,source_url,content_hash,
    source_type,version_status) ON document TO team06_e4;
GRANT INSERT (scheme_id,title,circular_no,issue_date,file_path,uploaded_by,source_url,source_type,content_hash)
    ON document TO team06_e4;
GRANT SELECT (job_id,doc_id,status) ON ingestion_job TO team06_e4;
GRANT INSERT (doc_id) ON ingestion_job TO team06_e4;
GRANT SELECT, DELETE ON bookmark TO team06_e4;
GRANT INSERT (user_id,doc_id,scheme_id,page_no) ON bookmark TO team06_e4;
GRANT USAGE ON SEQUENCE bookmark_bookmark_id_seq TO team06_e4;
GRANT EXECUTE ON FUNCTION e3_record_api_event(uuid,uuid,uuid,text) TO team06_e4;
COMMIT;
