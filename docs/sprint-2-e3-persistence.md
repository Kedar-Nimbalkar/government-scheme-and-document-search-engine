# Sprint 2 E3 persistence handoff

## Database setup and runtime connections

Apply migrations 001, 002, 003, and 004 in order as the schema owner. Then
run [runtime-roles.sql](../db/runtime-roles.sql) with the application schema
first in `search_path`. The provisioning account needs permission to create
roles. The script creates NOLOGIN permission groups and grants access to
the selected schema. These group names are cluster-wide. Use distinct names
for separate environments sharing a cluster. Use separate nonsuperuser login accounts and grant
one group to each. For example, as a PostgreSQL administrator:

```sql
CREATE ROLE team06_indexer LOGIN;
CREATE ROLE team06_search LOGIN;
CREATE ROLE team06_api LOGIN;
GRANT team06_e1 TO team06_indexer;
GRANT team06_e2 TO team06_search;
GRANT team06_e4 TO team06_api;
```

Set the passwords through your PostgreSQL administration tool. In psql use
`\password team06_indexer`, `\password team06_search`, and
`\password team06_api`. Give each login the application schema as its
`search_path` through the role configuration or the connection's
`options`. The database owner connection remains reserved for migration,
seed, and corpus import. Restart connection pools after applying schema
and role changes.

| Consumer | Connection | Access |
| --- | --- | --- |
| E1 | Set `DATABASE_URL` to the indexer login when invoking `python -m engines.e1 --from-database`. | Claim, lease, retry, reconcile, and complete ingestion jobs through the existing psycopg adapter. Read private PDF paths for parsing. |
| E2 | Open a psycopg connection with the search login. | Read restricted columns from the indexed metadata view. |
| E4 | Pass the API login URL to `createE3Repository` with PDF and index roots. | Create citizen accounts, look up logins, maintain bookmarks, read metadata, and enqueue uploads. |

E4's grants permit inserting the `doc_id` of a queued job and reading its
status. E1's grants permit job-state updates. Registration uses the database's
citizen default. Create privileged application accounts using the owner's
seed or account administration connection. The API role can execute a
parameterized login lookup, and its ordinary account reads exclude hashes.

Set `E1_DATABASE_URL`, `E2_DATABASE_URL`, and `E4_DATABASE_URL` in the
corresponding process environments. The E1 CLI reads `DATABASE_URL`, so
assign the indexer URL to that name in its terminal before startup.

## E4 repository signatures

Import `createE3Repository({ connectionString, pdfRoot, indexRoot })` from
[repository.js](../apps/api/src/persistence/repository.js). It creates a
node-postgres pool. Close `repository.pool` when shutting down the server.
An existing pool can be injected via `new E3Repository({ pool, pdfRoot, indexRoot })`.
Actor and owner UUIDs come from E4's authenticated session.

```js
const repository = createE3Repository({
  connectionString: process.env.E4_DATABASE_URL,
  pdfRoot: process.env.PDF_ROOT,
  indexRoot: process.env.INDEX_ROOT,
});
```

| Method | Result |
| --- | --- |
| `createUser({ username, passwordHash })` | Citizen profile with UUID, username, role and nullable department. E4 handles password hashing and validation. |
| `findUserByUsername(username)` | Internal login projection with bcrypt hash, or null. Keep this result inside login. |
| `findUserById(userId)` | Profile or null. |
| `createBookmark(userId, {docId, schemeId, pageNo})` | Created bookmark, enforcing one target and null-safe uniqueness. Includes nullable document and scheme IDs for persistence. |
| `listBookmarks(userId)`, `deleteBookmark(userId, bookmarkId)` | Only that account's rows. Deletion returns a boolean. E4 adapts results to the public bookmark schema. |
| `listFilters()` | Distinct sorted state, department and category values from eligible documents. |
| `getDocument(docId)` | Public document fields or null, without a storage path. |
| `getDocumentPdf(docId)` | A checked local file path for E4's private streaming implementation, or null. |
| `getDocumentStatus(docId, {userId, requestId})` | Job and version status of an eligible document, or any job in the signed-in department user's scope or an admin's scope. Null for absent or inaccessible records. |
| `createDocumentAndJob({userId, schemeId, title, circularNo, issueDate, sourceUrl, stagedName, requestId})` | Document UUID, job UUID, status and `replayed`. A same-request replay returns the existing IDs and current job status. A conflicting request with the same hash raises `DUPLICATE`. |
| `recordOperationalEvent(code, {userId, docId, requestId})` | Records a fixed event code and optional IDs. E4 can record login and authorization denials. |
| `reconcileStorage({quarantineOrphans, repairMissingJobs})` | Reports orphan PDF paths and DB-only documents, with optional repair and quarantine. Use from trusted maintenance code. |

Before upload registration, E4 validates and closes the PDF in
`PDF_ROOT/.staging/<stagedName>` and transfers ownership of the staged
file to the repository. Storage directories must share a filesystem that
supports hard links. The repository verifies the bytes, syncs the staged
file, and places an immutable content-addressed file in `uploads/` under
the same per-hash lock used by E3 import. PostgreSQL then inserts the
document and a single job in one transaction. Retry with a freshly staged
copy. An exact replay can also restore a missing canonical upload with its
verified original bytes.

Reconciliation defaults to reporting storage differences and recording a
fixed operational event. It can move confirmed unreferenced originals to
`.quarantine/`, rechecking under the per-hash lock. It can queue missing
jobs for registered PDFs when the original matches its stored hash.
Missing files for referenced rows remain reported for restoration.
The upload and reconciliation paths leave referenced originals intact.

## E2 and E1 contracts

```python
manifest, segments = read_snapshot(index_root)
metadata = MetadataRepository(psycopg_connection, manifest["committed_doc_ids"])
allowed = metadata.get_matching_docs({"state": "Karnataka"})
details = metadata.get_metadata(list(allowed))
```

Use the same validated snapshot for retrieval and metadata eligibility.
Eligibility is the intersection of the captured E1 inventory and
PostgreSQL `INDEXED` rows. Filters combine with AND over NFKC, casefolded,
whitespace-normalized values. SQL NULL attributes are excluded by active filters.
Historical documents remain eligible when
indexed. Unknown metadata remains null. PostgreSQL stores metadata and
job state while E2 reads postings and passage text from the index files.

E1 uses the existing [job statements](../packages/contracts/v2/jobs.sql)
with the restricted indexer role. An expired lease is reconciled against the
validated manifest inventory. Job errors record only allowlisted reason
codes and identifiers. Document version status and the Sprint 3 audit
workflow remain separate from these operational events.

## Integration tests

Set `E3_TEST_DATABASE_URL` to a disposable PostgreSQL database with a test
administrator connection. Run `npm.cmd test` and `python -m pytest -q`.
The PostgreSQL cases use isolated schemas and exercise the actual runtime
grants. Also set `E1_TEST_DATABASE_URL` to run E1's standalone recovery tests.

On the loopback PostgreSQL cluster, 28 Python tests and six Node tests
passed. The tests held two open claim transactions to verify that
`SKIP LOCKED` selects a different job, then reconciled an expired claim
and verified the retry and structured error. Separate E4 sessions verified
persistent bookmarks, concurrent duplicate inserts, upload rollback,
reconciliation, and permission failures when attempting to create an
INDEXED job or read hashes outside the login lookup. A spawned E1 writer
also indexed an imported PDF under the restricted indexer login, and E3
matched the resulting manifest inventory to the database state.
