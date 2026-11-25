# Sprint 2 interface contracts · v2

Use [the endpoint registry](endpoints.json), [JSON Schemas](schemas.json)
and [v2 fixtures](../../../fixtures/v2/) together. Registry schema names
refer to `$defs` in the schema bundle. The v1 files are historical. Existing v1
index data must be reindexed after migrating its PostgreSQL identifiers.

## Index and identities

- Use lowercase UUID strings for document, scheme, job and user IDs.
  Existing department and bookmark IDs remain positive integers.
- The sole E1 writer allocates increasing passage IDs across generations.
  Encode IDs through 9007199254740991 as numbers and larger IDs as canonical
  decimal strings. JS compares IDs with BigInt and Python compares them with
  `passage_id_value`. Preserve IDs during compaction.
- Publish a UTF-8 JSON manifest with schema and tokenizer versions, generation,
  ordered segment references, the next passage ID, committed document UUIDs
  and corpus statistics. Each reference records SHA-256 of the complete segment
  bytes. Write durable immutable segments before atomically replacing the
  manifest. Keep segments available while any reader snapshot can reference them.
- A segment contains a lexicographically sorted dictionary, postings and
  per-term passage df, and a passage table. Postings are sorted numerically by
  passage ID, and positions are strictly increasing zero-based token offsets.
  `df[term]` counts distinct passages in this segment. `N` counts committed
  passages across all segments, `total_tokens` sums their lengths, and
  `avgdl` is their quotient (0 for an empty index).
- E1 keeps all passages for one document together. Deduplicate by document UUID
  against both the committed inventory and the pending writer batch, under
  exclusive writer ownership. Compaction preserves the inventory. Different
  source versions use different UUIDs. PDF pages start at 1 and CSV pages use null.
- Both engines import the shared NFKC + casefold tokenizer. Store exact passage
  text and one `token_spans` entry per token. Spans use zero-based half-open
  UTF-16 offsets in original text. Compatibility expansion can map multiple
  tokens to the same source span. Both engines use the same Python runtime
  and Unicode database. Readers reject unknown schema and tokenizer versions
  and fail closed on checksum mismatch.

## Search and the public boundary

Express owns all public routes. E2 receives `POST /internal/search` and
`GET /internal/autocomplete` over loopback. Express translates public
`q` to `query` and the three named filters to `filters`. An omitted page
defaults to 1 and the public page size is 10. E3 combines supplied filters with
AND after NFKC, casefold, and whitespace normalization. E2 filters before ranking, reports total matching
passages before pagination, and returns the searched manifest generation.
An empty search is HTTP 200 with an empty list and total 0.

Autocomplete uses a 1–64-character prefix and returns at most eight
committed vocabulary terms. Search returns a nullable `did_you_mean`
string drawn from the same vocabulary. E2 applies curated expansions within
unquoted operands and preserves quoted search text.

Malformed Boolean syntax produces keyword results with the response header
`X-Search-Warning: BOOLEAN_FALLBACK`. Express can forward this header to the
browser. The search limits are 512 characters, 64 lexical tokens, at most
50 results per page, and at most 1,000 retained ranks.

Both search responses contain plain snippet text, normalized matched terms and
UTF-16 match spans relative to the returned snippet. E2 rebases spans when it
extracts a snippet, merges overlaps for display and keeps them sorted. React
slices the text and renders matching slices inside `<mark>` elements as text
nodes. Unknown hit metadata is null. Version status remains `UNKNOWN` until
verified, with null `superseded` and `current_doc_id` in that case.

The registry covers search, autocomplete, filters, document details, PDF
streaming, job status, auth, bookmarks and multipart upload. PDF responses
carry application/pdf and bookmark deletion returns 204. Missing resources
return 404. Public failures use the error envelope and include a server
generated request UUID. Use 503 for an unavailable dependency or exhausted
capacity and 504 for search timeout. Health exposes contract_version and
Express rejects an incompatible internal service version.

Public search accepts optional `k` and adds nullable scheme name, state,
category, and a PDF URL to each hit. A bookmark targets either a document
with an optional page or a scheme. Staff document and scheme lists use
`GET /api/documents` and `GET /api/schemes`. Administrators use
`GET/POST /api/admin/users` to view accounts and provision department users.
Upload uses `POST /api/documents`, and job status uses the existing
`GET /api/documents/:id/status` with the job UUID and a scoped failure reason.

## PostgreSQL and job ownership

E3 owns migrations, the Python [read repository](../../search_shared/search_shared/metadata.py)
and [prepared job statements](jobs.sql). E2 calls `get_matching_docs(filters)`
and `get_metadata(doc_ids)` through a read-only PostgreSQL account. Supply
`manifest["committed_doc_ids"]` from the same validated snapshot used for
retrieval. Only documents in that inventory with an INDEXED job are eligible,
including available historical versions. See the
[persistence handoff](../../../docs/sprint-2-e3-persistence.md) for roles and connections.

E4 validates and stages uploads under PDF_ROOT, then inserts document and job
rows in one database transaction. Return 202 after durable acceptance.
Persist QUEUED → PARSING → COMMITTING → INDEXED, or FAILED. The job row's
unique document UUID prevents multiple jobs for the same document. Claim in a
short transaction via row locking and SKIP LOCKED. E1 renews leases and passes
the current lease token when transitioning jobs. A retryable parsing failure
returns to QUEUED with a delay while attempts remain. Status is independent of
document version status. Use sanitized reason codes internally.

Only the exclusive writer may publish or reconcile an index. The E3 session
advisory lock and local index-root lock fence a durable writer. Before publishing,
confirm ownership and the lease while holding writer ownership. Publish the
manifest before recording INDEXED. On restart, inspect expired in-flight jobs
under that same ownership and reconcile against the verified manifest's UUID
inventory. Already published jobs become INDEXED. Unpublished jobs requeue
while attempts remain and become FAILED when exhausted. A stale token may not
commit a job reclaimed by another worker.

## Access and engine adoption

Citizen access covers public reads and their own bookmarks. Department
accounts additionally upload within their assigned dept_id, checked against
the scheme's department. Admins may upload across departments. Use 401 for a
missing or expired session, 403 for denied access, and 409 for a duplicate.
Keep paths, hashes, leases and raw database errors out of public responses.

| Owner | Required consumers |
| --- | --- |
| E1 | Shared tokenizer and passage ID codec, manifest and segment schemas, prepared claim and state transitions. |
| E2 | Shared tokenizer and reader, E3 read repository, internal request and response schemas and fixture checks. |
| E3 | UUID migration, repository result fields, job states and SQL. |
| E4 | Public endpoint registry, request and response schemas, job acceptance and error envelope. |

Every engine prompt starts with this contract and the
[integration brief](../../../docs/sprint-2-handoff.md). Include v2 conformance
tests for any new interface implementation. Coordinate changes here before
changing engine code that produces or consumes these fields.
