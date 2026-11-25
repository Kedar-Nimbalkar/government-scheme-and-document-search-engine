# Sprint 2 E3 verification

## Corpus provenance

Hugging Face CLI found the local `shrijayan/gov_myscheme` cache at revision
`2e7a91a1cd99c40d6a584b237022116fa21d2e88`. Its card declares Apache-2.0
and attributes the source to the Government of India's MyScheme portal.
The archive and expanded directory both contain 2,876 PDF names and the
expanded PDFs yield 2,066 distinct SHA-256 hashes. The
sampled PDFs were produced by `wkhtmltopdf` and contain scheme web pages.
Their PDF creation timestamps are distinct from official issue dates.
[Pinned dataset card](https://huggingface.co/datasets/shrijayan/gov_myscheme/blob/2e7a91a1cd99c40d6a584b237022116fa21d2e88/README.md)

| Reviewed cached file | Jurisdiction | Searchable pages | Scheme and department evidence |
| --- | --- | ---: | --- |
| `a-pudu.pdf` | Puducherry | 1 of 1 | [Government of Puducherry scheme page](https://py.gov.in/aravanaippu) |
| `bbbp.pdf` | All India | 2 of 2 | [Ministry scheme page](https://wcd.gov.in/offerings/beti-bachao-beti-padhao-scheme) and [PIB national scope](https://www.pib.gov.in/Pressreleaseshare.aspx?PRID=1943758&lang=2&reg=48) |
| `25-ciss.pdf` | Lakshadweep | 2 of 2 | [UT notice](https://lakshadweep.gov.in/notice/25-capital-investment-subsidy/) and [Directorate circular listing](https://lakshadweep.gov.in/past-notices/circulars/page/84) |

The [reviewed import catalog](../db/e3/catalogs/gov_myscheme.json) records the
exact hashes, source URLs, and verified scheme/department metadata. The
three PDFs are stored and visibly titled as dataset snapshots. Official
issue dates and circular numbers remain null. Originals were imported from
the existing cache.

The full scan found searchable text in all 2,066 unique PDFs and recorded
810 duplicate paths. Three files passed the metadata review. The remaining
2,873 paths were quarantined with `UNVERIFIED_SCHEME_DEPARTMENT`. The PDF
annotation scan yielded no embedded URLs. The three reviewed rows have
source URLs from the official sites listed above.

## Import and verification

The isolated PostgreSQL run applied migrations 001–003 and seeded one
development account using an environment-provided bcrypt password. The
pilot imported one reviewed PDF. The follow-up imported the other two,
and a replay returned three duplicates. SQL reconciled three departments,
three schemes, three documents, three provenance records, and three
ingestion jobs. E1 indexed all three via its existing SQL contract into
22 passages and 5,065 tokens. All three document version statuses started
as `UNKNOWN`. The stored originals matched all three catalog checksums.

- `python -m pytest -q` passed 27 Python tests with standalone PostgreSQL.
  E3 tests exercised invalid foreign keys, restrictive deletes, duplicate
  registration and bookmarks, department roles, bcrypt seed verification,
  null optional metadata, transactional rollback, and job timestamps.
- `node --experimental-test-isolation=none --test tests/*.test.js` passed
  all five existing contract and job SQL tests.
- `python -m pip check` reported no broken requirements.
- Serial and spawned inspection produced byte-identical synthetic reports.
  The eight-worker inspection completed the full cached corpus inventory.

The full inventory (`real-inventory-parallel.json`), pilot report, import
report, and duplicate replay report are under `data/e3-verification/`. The
[E3 runbook](../db/e3/README.md) records the commands and inputs.
