# E2 · Query processing and passage ranking

Install the base and E2 requirements from the repository root.

```powershell
python -m pip install -r requirements.txt -r engines/e2/requirements.txt
npm.cmd run dev:e2
```

Set `E2_DATABASE_URL` to the PostgreSQL search-role connection and `INDEX_ROOT`
to the directory that contains E1's `manifest.json`. Use the
[E3 handoff](../../docs/sprint-2-e3-persistence.md) for migrations and runtime
grants. `/health` reports service liveness and contract version. Search uses
`POST /internal/search` over loopback. The public API boundary stays in E4.
`GET /internal/autocomplete?prefix=...` returns up to eight suggestions
from the same committed snapshot as the search statistics.

```powershell
$body = @{
  query = '"housing grant" AND NOT coastal'
  filters = @{ state = 'Karnataka' }
  k = 10
  page = 1
} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8001/internal/search -Method Post -ContentType application/json -Body $body
```

Operators are uppercase standalone `NOT`, `AND`, and `OR`, in descending
precedence. Parentheses override precedence. Adjacent unquoted keywords
imply OR. The parser also inserts OR between adjacent operands and groups.
Quoted phrases match consecutive normalized token offsets within one passage.
Malformed Boolean syntax falls back to an OR of its keyword terms and
returns `X-Search-Warning: BOOLEAN_FALLBACK`. The v2 JSON body keeps its
existing shape. Requests are limited to 512 characters, 64 lexical tokens
(including operators and grouping), `k` from 1 to 50, and `page * k ≤ 1000`.
Invalid input returns 400. Unavailable metadata or an unreadable snapshot
returns 503.

E2 reads one E1 snapshot and asks E3 for eligible documents in a read-only
repeatable-read transaction. E3 normalizes filter values with NFKC,
casefold, and collapsed whitespace. The engine combines active filters by
AND and excludes missing attributes under those constraints. Empty filters
restore the full eligible universe. NOT complements that same universe.

BM25 uses corpus-wide passage count, summed segment df, and average passage
length. Set `E2_BM25_K1` and `E2_BM25_B` to override 1.2 and 0.75.
Distinct terms in positive query operands contribute once. Results order by
score, then known newer issue date, then ascending passage ID. The heap
retains the requested page and all preceding ranks. Total counts matching
passages before slicing. Snippets use up to 280 UTF-16 code units of original
passage text and visible match offsets. Missing page numbers and metadata
stay null.

## Query assistance

Autocomplete normalizes the final token of the supplied prefix using the
shared tokenizer. A prefix contains 1–64 characters. The Trie caches up to
eight completions at each node, ordered by total occurrences and then
lexically. Traversal costs O(L) for L prefix characters, plus at most eight
outputs. An unmatched prefix returns an empty array.

The spelling pass only inspects unquoted unknown words. It preserves
all-uppercase terms as possible acronyms and leaves compound input that
tokenizes into several words intact. A unique one-edit match can be
applied automatically to words of at least five characters. Wider matches
and ties are suggestion-only. The Trie search prunes edit rows over the
distance limit, visits at most 2,048 nodes per word, and considers at most
32 candidates per pass and eight unknown words per request. Suggestion-only
search can consider up to three edits for longer words. Each visited node
updates one DP row in O(word length) time. Suggestions come
from the committed vocabulary and preserve the original query's operators
and quoting. A query with no qualifying candidate returns null. Malformed
Boolean fallback retains its keyword behavior.

The [curated alias map](aliases.json) records UMANG and the healthcare
spelling variant with source provenance. Expansion adds grouped OR
alternatives within the original Boolean operand. Quoted operands stay
literal and unknown acronyms pass through. Each loaded generation publishes
its Trie, postings and BM25 statistics together in memory. Existing
requests retain their captured generation.

Both internal handlers execute synchronously outside the async event loop.
Eight request slots cap concurrent work per process. Search and autocomplete
use cooperative processing deadlines of three seconds and one second,
respectively. PostgreSQL connection and statement timeouts bound database
work. A budget failure returns 504 and capacity or dependency failures
return 503 with a typed error. Cold snapshot loading is checked when the
load completes.

## Complexity and verification

Let P be passages, A be distinct query atoms, and M be eligible matches.
One generation load materializes the index and sorts each term's posting
IDs in O(Σ df(t) log(df(t) + 1)) time after loading the index. A request
scans P passages against E3's ID HashSet. The metadata repository normalizes
distinct facet tuples for active filters before resolving eligible IDs.
Each Boolean merge
is linear in its two sorted inputs. Phrase evaluation intersects eligible
posting IDs and uses shifted two-pointer intersections of token positions.
Ranking evaluates at most the distinct positive terms for each of M
matches. Top-K selection costs O(M log(page × k + 1)) time and
O(page × k) heap space. Only returned hits build snippets.

```powershell
python -m pip install -r engines/e2/requirements-dev.txt
python -m pytest tests/test_e2.py -q -s
```

Set `E2_TEST_DATABASE_URL` to a disposable PostgreSQL administrator
connection for the standalone integration and timing test. The tests create
temporary schemas and exercise E1 and E2 under their restricted roles.
The [labelled corpus](../../fixtures/e2-labelled/README.md) is synthetic.

The FR-04–06 baseline integration run passed 43 Python tests and six Node tests. The
standalone PostgreSQL case used the restricted runtime roles and a snapshot
published by the spawned E1 writer. It matched `"housing grant"` on page 3
and `"grant housing"` on page 1, verified normalized filters and a
zero-result cross-state query, and returned 503 after switching to a role
without metadata access. The fixture ranker matched a full sort and two
independently calculated BM25 scores.

Twenty warm-index ASGI phrase searches with a state filter measured
**52.56 ms median and 162.10 ms p95** on the local two-passage snapshot.
The measurement included the PostgreSQL connection, reads, and response
serialization. It covers a small integration workload. Broader performance
and complete FR-06 acceptance remain open.

See the [query assistance verification](../../docs/sprint-2-e2-assistance.md)
for actual-corpus HTTP timing, incremental upload evidence, and Gate 1 gaps.
