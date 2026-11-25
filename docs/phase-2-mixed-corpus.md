# Phase 2 mixed-corpus search

Place the two extracted datasets under `data/raw/scheme_articles/` and
`data/raw/scheme_pdfs/`. Nested directories are supported. Keep the archives,
raw files, generated reports, and indexes under the Git-ignored `data/` tree.

## Reproduce the workflow

Use the project Python environment and the development database described in
the [E4 runbook](../apps/api/README.md). Apply migrations 001 through 006 for
a fresh schema. For an existing schema that has applied 005, apply only
`db/migrations/006_mixed_corpus.sql`. Reapply `db/runtime-roles.sql` in the
same schema after migration. Keep `PDF_ROOT` and `INDEX_ROOT` the same across
services. Use a workspace-local storage directory and an owner/import
connection in `DATABASE_URL`. Keep credentials in the environment.

```powershell
python -m db.e3.mixed discover --report data/mixed/discovery.json
python -m db.e3.mixed sample --workers 4 --report data/mixed/sample.json
```

Start the updated E1 poller or drain explicitly with the restricted E1
connection. Use a single indexer. For an explicit drain from a separate
shell, set `DATABASE_URL` to `E1_DATABASE_URL` and run:

```powershell
python -m engines.e1 --from-database --workers 4
```

Start E2, Express, and Vite with their normal commands. Verify the sample
before full ingestion. Substitute the actual index directory if different.

```powershell
python -m db.e3.verify_mixed --index-root data/index --report data/mixed/sample-verify.json
python -m db.e3.mixed full --workers 4 --report data/mixed/full.json
```

Run the full import from the owner/import shell. After E1 finishes draining
the durable queue, verify the final manifest and public responses:

```powershell
python -m db.e3.verify_mixed --index-root data/index --report data/mixed/full-verify.json
node --test --test-concurrency=1 tests/*.test.js
npm.cmd run build
```

The manifest report records canonical and duplicate paths, SHA-256 hashes,
source UUIDs, metadata evidence, and outcomes. The importer creates the
document, provenance, and durable job in one transaction. E1 parses bounded
file-backed jobs and publishes through its existing single writer.

Migration 006 allows an unlinked document only for a scheme article or
snapshot. The existing scheme constraints still apply to curated scheme
identities. Exact normalized title and region can link to a distinct existing
scheme. Ambiguous or uncatalogued identities remain searchable via their
source metadata. A one-to-one provenance table stores duplicate paths,
region associations, export details, references, and extraction evidence.
The indexed view exposes regions and references without exposing local paths.
The snapshot format and tokenizer are unchanged.

PDF originals remain byte-for-byte unchanged. The imported article copy is
preprocessed UTF-8 text. The original TXT remains under the raw corpus
path. A PDF export date belongs to its snapshot provenance. Unknown issue
dates, circular numbers and unsupported category values remain null.
Concatenated PDF tags are retained as raw evidence where detectable.
A scheme information snapshot or scraped article reflects its source at
capture time. Its provenance and current legal accuracy require separate
verification.

## Recorded execution

| Input | Discovered | Unique SHA-256 | Exact copies |
| --- | ---: | ---: | ---: |
| TXT articles | 1,524 | 1,444 | 80 |
| PDF snapshots | 2,876 | 2,066 | 810 |

The TXT files occupy 29 geographic directories. The extensionless PDF-folder
file named `test` was skipped. Original filenames are preferred within exact
hash groups. All recorded regions survive TXT deduplication.

The pilot registered 20 articles and 20 PDFs. Its repeat reported 40 existing
rows without creating any more jobs. Four E1 workers published all 40 in
5.12 s (7.81 sources/s). The pilot snapshot included the three pre-existing
catalogued PDFs and contained 403 passages, 39,509 token occurrences and
4,140 unique terms. Live segment files occupied 2,590,550 bytes. The 15
public query requests had a 97.85 ms median and 204.21 ms maximum.

### Full corpus

| Measure | Result |
| --- | ---: |
| New records queued during full run | 3,467 |
| Existing pilot records during full run | 40 |
| Previously indexed catalogued PDFs matched by hash | 3 |
| Final indexed articles / PDFs | 1,444 / 2,066 |
| Total passages / distinct terms | 36,501 / 52,166 |
| Total token occurrences | 3,318,069 |
| Live index segment bytes | 220,951,800 |
| Full E1 drain with four workers | 328.25 s (10.56 sources/s) |
| Failed or rejected supported files | 0 |

PostgreSQL had 3,510 indexed job rows and 3,507 new mixed-corpus provenance
rows. The three previously indexed PDFs kept their identifiers and existing
segments. Their classification uses their existing catalogue evidence.
They retain their original pre-Phase 2 indexed text. A second discovery
confirmed every original path and SHA-256 hash was unchanged.

All 2,066 unique PDFs parsed, totaling 2,894 physical pages. All list
wkhtmltopdf 0.12.6 and PDF export date April 22, 2024. The importer found
2,064 displayed PDF titles, 2,041 PDF regions, 510 PDF ministries and
references in 1,077 PDFs. All 1,444 articles had directory region
associations, including 11 with more than one region. The importer
extracted article titles for all 1,444 and references for 605. Two PDF titles
used the filename fallback. Categories remained unknown for new sources
because the dataset did not provide reliable structured category labels.
The two curated PostgreSQL categories remained available.

| Public search | Matching passages | Top result |
| --- | ---: | --- |
| Scholarship | 1,807 | State Horticulture Scholarship |
| Handloom | 135 | National Handloom Development Programme: Concessional Credit/Weaver MUDRA Scheme |
| Atal Amrit Abhiyan | 229 | Atal Amrit Abhiyan 2020 |
| ARAVANAIPPU | 7 | Aravanaippu Illam |
| "capital investment subsidy" | 46 | Capital Investment Subsidy to Karaikal Region |
| scholarship AND education | 579 | Distance Education Scholarship for Disabled Students |
| pension OR retirement | 1,456 | One Rank One Pension (OROP) Scheme |

The region checks returned 59 matching passages for Karnataka, 128 for
Andhra Pradesh, 151 for Assam, 495 for Puducherry and 1,422 for Central
with no cross-region leakage among returned hits. Ministry of Textiles and
Business & Entrepreneurship filters returned 68 and 8 matching passages.
Autocomplete returned suggestions for `sch` and none for `adsby`.

Fifteen warm public requests had a 176.91 ms median and a 327.33 ms maximum.
These timings exclude the browser workflow. The full snapshot's cold
warm-up took 19.71 s and four requests returned 504 before it warmed.
The index was then warmed on the running development service. A fresh
server or generation may again incur cold-load timeouts.

The Vite build, 49 Python tests and all nine Node tests passed. A separate
headless Edge run verified live highlights, `Read Article` with no PDF
page label, and downloading the original PDF through the page-linked action.
The local database and writer reconciliation reported no failed jobs.
