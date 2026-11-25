# Sprint 2 E3 PostgreSQL schema

```mermaid
erDiagram
    DEPARTMENT ||--o{ SCHEME : manages
    DEPARTMENT o|--o{ APP_USER : scopes
    SCHEME ||--o{ DOCUMENT : contains
    APP_USER o|--o{ DOCUMENT : uploads
    DOCUMENT o|--o{ DOCUMENT : supersedes
    APP_USER ||--o{ BOOKMARK : owns
    DOCUMENT o|--o{ BOOKMARK : targets
    SCHEME o|--o{ BOOKMARK : targets
    APP_USER ||--o{ AUDIT_LOG : acts
    DOCUMENT o|--o{ AUDIT_LOG : concerns
    DOCUMENT ||--o| INGESTION_JOB : queues
    CORPUS_DATASET ||--o{ DOCUMENT_IMPORT : identifies
    DOCUMENT ||--o| DOCUMENT_IMPORT : records
    DEPARTMENT {
        integer dept_id PK
        text dept_name
        text level
    }
    SCHEME {
        uuid scheme_id PK
        integer dept_id FK
        text scheme_name
        text category
        text state
    }
    DOCUMENT {
        uuid doc_id PK
        uuid scheme_id FK
        text title
        text circular_no
        date issue_date
        text file_path UK
        uuid uploaded_by FK
        uuid superseded_by FK
        text source_url
        text source_type
        text content_hash UK
        text version_status
    }
    APP_USER {
        uuid user_id PK
        integer dept_id FK
        text username UK
        text password_hash
        text role
    }
    BOOKMARK {
        integer bookmark_id PK
        uuid user_id FK
        uuid doc_id FK
        uuid scheme_id FK
        integer page_no
        timestamp created_at
    }
    AUDIT_LOG {
        bigint log_id PK
        uuid user_id FK
        uuid doc_id FK
        text action
        jsonb old_value
        jsonb new_value
        timestamp logged_at
    }
    INGESTION_JOB {
        uuid job_id PK
        uuid doc_id FK, UK
        text status
        integer attempts
        integer max_attempts
        text error
        timestamp created_at
        timestamp updated_at
        timestamp available_at
        timestamp lease_until
        uuid lease_token
    }
    CORPUS_DATASET {
        text dataset_id PK
        text revision PK
        text license
    }
    DOCUMENT_IMPORT {
        uuid doc_id PK, FK
        text dataset_id FK
        text revision FK
        text relative_path
        text artifact_kind
        text evidence_url
    }
```

### Normalization and constraints

- Each department stores its name and level once. Scheme identity is unique
  by department, name, and jurisdiction. Documents reference the scheme
  instead of repeating its department, category, or state. E2 resolves those
  fields through E3's parameterized joins.
- A bookmark belongs to one account and targets exactly one document or
  scheme. Page numbers apply only to document bookmarks. The partial B-tree
  indexes distinguish a document-level bookmark from a page bookmark and
  treat repeated null pages as duplicates. Scheme bookmarks are unique per
  account and scheme.
- Job status belongs to the job. Document version status defaults to
  `UNKNOWN` and changes independently of job execution. The migration
  preserves legacy status data. The single-table `index_job` view lets the
  existing E1 prepared statements update the same `ingestion_job` rows.
- Dataset license depends on dataset and revision, so document-level
  provenance references a release. SHA-256 identifies the original
  bytes and deduplicates them across imports. Older PDFs without hashes
  are preserved. New or updated PDF rows require both the hash and relative file path.
- Foreign keys restrict deletion where official or historical records are
  referenced. Named B-tree indexes cover state/category/department filters,
  reverse FK lookups, available jobs, and active leases. The existing
  operational log is retained. The new audit table reserves storage for
  Sprint 3 auditing and supersession.

Apply [migration 003](../db/migrations/003_e3_metadata.sql) after the two
foundation migrations. An existing database with conflicting rows must be
reviewed before applying new unique constraints. The migration is
transactional and contains no destructive data reset.
