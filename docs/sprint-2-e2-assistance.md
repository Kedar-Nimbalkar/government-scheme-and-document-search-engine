# Sprint 2 E2 query assistance verification

## Tested behavior

- Trie ranking, empty prefix results, Unicode normalization, bounded typo
  correction, severe suggestion-only fallback, unknown acronym passthrough,
  grouped OR expansion, and literal quoted phrases.
- Response models for internal search, autocomplete, and health. Invalid
  requests produce 400, and work-budget exhaustion produces a typed 504.
- A staged PDF was registered through E3's durable job table and processed
  by a spawned E1 writer. Search results and Trie completions both refreshed
  to the new generation. The previous snapshot and its segment files stayed
  readable.
- The standalone PostgreSQL run passed 47 Python tests. Node passed all six
  tests with the same database available.

The acronym and spelling variant are curated from official sources.
[UMANG](https://www.umang.gov.in/landing/aboutus) ·
[Press Information Bureau healthcare terminology](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2210432&lang=1&reg=6)

## Actual-corpus benchmark

The run used the three reviewed MyScheme PDF snapshots imported by E3.
E1 generation 3 contains 22 passages and 5,065 tokens. Each route was warmed
once and timed for 30 sequential HTTP requests over loopback. Search used
PostgreSQL under the restricted E2 role. Search queried `subsidy` with the
Lakshadweep filter and found eight matches. Autocomplete used `sub` and
returned six terms.

| Endpoint | Median | p95 |
| --- | ---: | ---: |
| Internal search | 53.93 ms | 167.68 ms |
| Internal autocomplete | 3.99 ms | 21.39 ms |

To repeat against a running internal service on the same corpus:

```powershell
python -m engines.e2.benchmark --base http://127.0.0.1:8001 --query subsidy --state Lakshadweep --prefix sub --samples 30
```

The state leak check queried `scheme` separately for Puducherry, All India,
and Lakshadweep. The 19 returned passages all belonged to the expected
source document for their filter. One autocomplete result,
`subsidyâ`, was a token from the source PDF extraction. The Trie preserves
the committed vocabulary, which exposes this corpus-cleaning gap.

## Gate 1 comparison

| Requirement | Sprint 2 evidence | Remaining validation |
| --- | --- | --- |
| FR-07 | Internal Trie autocomplete, curated query-time expansion and bounded correction with suggestion-only fallback. The severe test `schmlog` suggests the existing fixture term `school`. | Expand curated coverage, review index-time expansion, and evaluate public UI behavior and correction quality on broader language and typo samples. |
| NFR-01 | Both internal endpoints had sub-two-second p95 on the admitted corpus. | Gate 1's 50-concurrent-user REST measurement through Express and broader-load testing. |
| NFR-02 | No cross-state leakage among the 19 sampled passages. The labelled E2 corpus also passed its expected-result checks. | Establish a labelled relevance set and threshold, then measure Precision@10. |

[Gate 1 requirements](Gate1%20(1).docx) ·
[E3 corpus verification](sprint-2-e3-verification.md)
