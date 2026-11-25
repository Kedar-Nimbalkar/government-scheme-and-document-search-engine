# E1 · Index construction

This command implements FR-01 for a verified E3 export and optional imported
scheme records. It writes a positional index readable through
`search_shared.index_reader.read_snapshot` under the
[v2 contracts](../../packages/contracts/v2/README.md).

## Run the synthetic example

From the repository root, with the project Python environment activated:

```powershell
python -m pip install -r requirements.txt -r engines/e1/requirements-dev.txt
python -m engines.e1 --pdf-root fixtures/e1-synthetic --pdf-jobs fixtures/e1-synthetic/pdf-jobs.json --schemes fixtures/e1-synthetic/schemes.csv --index-root data/e1-reference
python -m pytest -q
```

The corpus and all its IDs are synthetic. The first run produced three
passages and 13 tokens. It indexed PDF pages 1 and 3 and omitted blank page 2
and image-only page 4. It also indexed one CSV passage with a null page.
[Extraction report](../../fixtures/e1-synthetic/extraction-report.json) ·
[Reader fixture](../../fixtures/e1-synthetic/index/manifest.json)

## E3 input and output

- For PDFs, supply a trusted JSON array projected from E3's verified
  `claim_index_job`/import rows. Each item has canonical `doc_id` and `job_id`
  UUIDs, `source_type: "PDF"`, and a relative `file_path`. An optional
  `scheme_id` can accompany an enriched export. E1 confines paths to
  `PDF_ROOT` and checks each PDF for extractable digital text.
- For schemes, supply an E3-imported UTF-8 CSV with `doc_id`, `scheme_id`,
  and `text`. E1 uses the supplied UUIDs, represents the input as
  `source_type: "scheme_record"`, and reports the scheme link. Both PDF and
  CSV passages join to E3 by `doc_id`. E3's v2 metadata identifies the scheme
  source as `CSV`. The strict v2 passage table carries no metadata fields.
- `--pdf-root` and `--index-root` can be replaced by the process environment
  variables `PDF_ROOT` and `INDEX_ROOT`. For a PDF-only or CSV-only run,
  omit the unused input option.
- The command prints a JSON report with per-document outcomes and omitted
  one-based PDF pages. Exit code 0 indicates success (including already
  indexed documents), 1 indicates per-document failures with a report, and
  2 indicates a fatal input or index error.

The writer allocates passage IDs from the current manifest, deduplicates by
document UUID, and publishes a checksummed immutable segment before replacing
the manifest. It processes a fresh fixture in document UUID order for
repeatable output. It stores the extractor's exact page text in contiguous
slices of up to 256 tokens by default. A Unicode character that expands into
overlapping tokens stays in one slice. Every passage resets token position
to zero. Term frequency is the posting's position count, and term document
frequency counts matching passages in the segment.

## Spawn workers and a dedicated writer

Add `--workers 2` (or `--workers 4`) to the export command above to use OS
processes. Use a fresh index root to compare runs. Worker and writer PIDs,
queue peaks, wall time, and indexed documents per second appear in the JSON
report. JSON lifecycle events go to stderr and contain job/document IDs,
states, and reason codes.

The supervisor assigns one in-flight job per worker and drains result
chunks while workers run. Queue limits default to four items per shared
queue, 64 KiB per result chunk, and 256 KiB per serialized task. The writer
spools a document's sequenced chunks to disk, up to 8 MiB per document,
and publishes only after the end marker. PDFs are limited to 16 MiB on
disk and 10,000 pages in process mode. Tune limits using
`--queue-items`, `--chunk-bytes`, `--task-bytes`, `--staged-bytes`,
`--input-bytes`, and `--worker-timeout`. Oversized documents fail with a
reason code. Two attempts are allowed for recoverable failures in export mode.

The writer fsyncs staged index files, renames immutable segments, and
replaces the manifest atomically. It keeps the previous manifest in
`manifest.previous.json` and retains all old segment files. Append uses
the manifest's committed document IDs and next passage ID. Use
`python -m engines.e1 --index-root data/e1-reference --compact` for an
explicit k-way heap merge that preserves IDs, postings and stats.

## Drain E3's PostgreSQL queue

Apply migrations 001 and 002, set `DATABASE_URL`, `PDF_ROOT` and
`INDEX_ROOT` in the process environment, then run:

```powershell
python -m pip install -r engines/e1/requirements-durable.txt
python -m engines.e1 --from-database --workers 2
```

Use the same PostgreSQL schema and local index root for this queue. The
writer holds filesystem and database advisory locks for the run. The
supervisor claims jobs through E3's prepared SQL and renews parsing leases.
The writer persists COMMITTING, fences manifest publication with the current
lease, then marks the job INDEXED. At restart it validates the snapshot,
cleans abandoned temporary files, and reconciles expired leases against the
manifest. Startup waits for outstanding leases within the configured
timeout. Committed IDs become INDEXED, while uncommitted jobs requeue within
their attempt budget. A checksum mismatch stops startup so the previous
manifest is available for inspection.

The command drains currently eligible jobs once. For jobs delayed with
`--retry-seconds`, invoke it again once they become eligible. For queued CSV
records, supply the verified export with `--schemes` so E1 can resolve their
search text by document UUID.

Run the benchmark with `python -m engines.e1.benchmark`. The optional
standalone PostgreSQL tests use `E1_TEST_DATABASE_URL` and create isolated
test schemas.
