# Team 06 integration contracts · v1

Historical foundation contract. New engine work consumes [v2](../v2/README.md).

## Browser → Express

Public URLs stay under `/api`. JSON errors use
`{"error":{"code":"UPPER_SNAKE_CASE","message":"Readable explanation"}}`.
Identifiers are positive integers. Page numbers are one-based. Dates use
`YYYY-MM-DD`. Optional values are omitted unless explicitly nullable.

| Method and path | Input | Success | Expected errors |
| --- | --- | --- | --- |
| GET /api/health | — | 200 with status, service and e2 | 503 |
| GET /api/search | q, optional state, department, category and page (default 1) | 200 with total and results | 400, 503, 504 |
| GET /api/autocomplete | prefix | 200 with suggestions array | 400, 503, 504 |
| POST /api/auth/register | username, password | 201 with user_id | 400, 409 |
| POST /api/auth/login | username, password | 200 with access_token and role | 400, 401 |
| GET /api/bookmarks | Bearer JWT | 200 with user’s bookmark array | 401, 403 |
| POST /api/bookmarks | Bearer JWT, doc_id, optional page_no | 201 with bookmark_id | 400, 401, 403, 409 |
| POST /api/documents | Bearer JWT, multipart file, scheme_id, title, circular_no, issue_date | 202 with doc_id and status Queued | 400, 401, 403, 413, 503 |

Upload roles are admin and department. The optional `replaces_doc_id` input
is reserved for Sprint 3 and must not be silently applied in Sprint 2.
The search schema defines result keys. Zero hits return 200 with total 0,
an empty results array, and message “No matching records found”. The public
snippet is HTML-escaped by Express before adding `<mark>` tags. Clients render
only these tags as markup.

## Express → internal FastAPI

Loopback HTTP only. GET /health is the liveness check. GET /search uses the
same query parameters with optional `k` (default 10) for page size. `total`
counts matching passages before pagination. GET /autocomplete accepts
`prefix` and returns `{"suggestions":[]}` when empty. E2 emits raw snippet
text. Express handles highlighting, converts upstream timeouts to 504 and
reports unavailable dependencies as 503.

## E2 → E3 and E4 → E3

E3 owns schema and query contracts. Allowed document IDs come from joined
document, scheme and department rows. The state and category filters match
their respective scheme fields, and department matches department.dept_name.
Filters combine with AND. Without filters E2 searches across the committed
corpus. E2 reads department, issue_date, status and superseded_by by doc_id.
In Sprint 2 ordinary results return superseded false and current_doc_id null.
E4 uses E3 persistence for local login, bookmarks and newly registered
documents. Stored file_path values are relative to PDF_ROOT.

## E4 → E1

A producer hands E1 `{doc_id, file_path}` after validation and metadata
registration. E1 controls the bounded multiprocessing work and result queues.
Outcome is `{doc_id,status}` with INDEXED or FAILED, plus reason on failure.
The Node-to-Python producer transport and durable acceptance/retry semantics
must be coordinated between E1, E3 and E4 before implementing real upload.
A 202 acknowledges accepted work. It makes no claim about index visibility.

## E1 → E2

UTF-8 JSON manifest.json identifies a generation and an ordered list of
immutable segment basenames. Each segment has passage records and terms
mapped to postings sorted by passage_id. Positions are increasing zero-based
token offsets inside one passage, generated with the shared tokenizer.
Passage IDs are unique across the snapshot. document_frequency is the number
of passages containing a term, measured across committed segments.
Statistics cover the entire snapshot for BM25. The writer publishes segments
before atomically replacing the manifest. Previously published segment files
must stay readable while an old snapshot can reference them. The initial
shared reader materializes a snapshot for fixture and baseline integration.
