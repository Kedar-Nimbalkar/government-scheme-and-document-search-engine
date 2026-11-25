# E1 · Sequential reference indexer

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

This reference command reads an exported batch. Live PostgreSQL job
claim/status updates, multiprocessing, and segment compaction remain separate
E1 integration work.
