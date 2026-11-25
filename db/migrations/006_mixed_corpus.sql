BEGIN;

ALTER TABLE document
    ALTER COLUMN scheme_id DROP NOT NULL,
    DROP CONSTRAINT document_source_type_check,
    ADD CHECK (source_type IN ('PDF','CSV','SCHEME_ARTICLE','SCHEME_PDF','OFFICIAL_PDF')),
    ADD CHECK (scheme_id IS NOT NULL OR source_type IN ('SCHEME_ARTICLE','SCHEME_PDF')),
    ADD CHECK (source_type NOT IN ('SCHEME_ARTICLE','SCHEME_PDF','OFFICIAL_PDF')
        OR (file_path IS NOT NULL AND content_hash IS NOT NULL));

CREATE TABLE corpus_source (
    doc_id uuid PRIMARY KEY REFERENCES document ON DELETE RESTRICT,
    paths text[] NOT NULL CHECK (cardinality(paths) > 0),
    regions text[] NOT NULL,
    displayed_title text,
    department text,
    category text,
    references_found text[] NOT NULL,
    page_count integer CHECK (page_count > 0),
    export_date date,
    evidence jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE VIEW e3_indexed_document AS
SELECT d.doc_id, d.scheme_id, d.title, d.circular_no, d.issue_date, d.source_url,
       CASE WHEN d.source_type = 'PDF' AND i.artifact_kind = 'dataset_snapshot' THEN 'SCHEME_PDF'
            WHEN d.source_type = 'PDF' AND i.artifact_kind = 'official_document' THEN 'OFFICIAL_PDF'
            ELSE d.source_type END AS source_type,
       d.version_status, d.superseded_by, j.job_id,
       CASE WHEN c.doc_id IS NULL THEN s.state WHEN cardinality(c.regions)=1 THEN c.regions[1] END AS state,
       coalesce(c.category,s.category) AS category, coalesce(c.department,p.dept_name) AS department,
       coalesce(c.displayed_title,s.scheme_name) AS scheme_name,
       coalesce(c.regions,array_remove(ARRAY[s.state],NULL)) AS regions,
       coalesce(c.references_found,array_remove(ARRAY[d.source_url],NULL)) AS source_references
FROM document d
JOIN ingestion_job j USING (doc_id)
LEFT JOIN scheme s USING (scheme_id)
LEFT JOIN department p USING (dept_id)
LEFT JOIN corpus_source c USING (doc_id)
LEFT JOIN document_import i USING (doc_id)
WHERE j.status = 'INDEXED';

COMMIT;
