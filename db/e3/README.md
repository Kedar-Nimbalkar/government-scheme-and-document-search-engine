# E3 · PostgreSQL metadata and corpus import

See the [schema and ERD](../../docs/sprint-2-e3-schema.md).
Install from the repository root in the project virtual environment.

```powershell
python -m pip install -r requirements.txt -r db/e3/requirements.txt
```

## Migrate or reset development data

For a configured database, apply pending migrations 001 through 005 in
order with `psql -v ON_ERROR_STOP=1`. For a local scratch database, set
`DATABASE_URL` to an explicit loopback URL and use:

```powershell
python -m db.e3 migrate-dev --schema team06_dev_e3
```

A safe dev reset uses a new schema name, such as `team06_dev_e3_next`.
The command requires a fresh schema name and preserves existing schemas. Use the selected
schema in `--schema` for imports and in the database connection's
`search_path` for downstream E1/E2. On a default application schema,
omit `--schema`.

To seed development accounts, supply one or more complete environment
variable pairs. The prefixes are `E3_TEST_ADMIN_`, `E3_TEST_CITIZEN_`,
and `E3_TEST_DEPARTMENT_`, each with `USERNAME` and `PASSWORD`.
Then run `python -m db.e3 seed --schema team06_dev_e3`. Passwords use
bcrypt, the department account receives a synthetic department, and
existing usernames remain untouched. The seed command requires a local
schema created by `migrate-dev`.

## Inspect and import reviewed PDFs

An import catalog supplies the dataset ID, pinned revision, license, and
one record for each reviewed PDF. Use [the project catalog](catalogs/gov_myscheme.json)
or [the synthetic example](../../fixtures/e3-synthetic/catalog.json) as a
template. Record the relative PDF path, SHA-256, verified scheme, department,
level, category, jurisdiction and provenance kind. Supply official
metadata evidence for real artifacts. Optional issue dates, circular numbers,
and source URLs may be null. Use the scheme's verified jurisdiction instead
of guessing it from a filename or a PDF timestamp.

```powershell
python -m db.e3 inspect --source-root fixtures/e1-synthetic --catalog fixtures/e3-synthetic/catalog.json --report data/e3/inspection.json
python -m db.e3 import --schema team06_dev_e3 --source-root fixtures/e1-synthetic --catalog fixtures/e3-synthetic/catalog.json --pdf-root data/e3/pdfs --limit 1 --report data/e3/pilot.json
python -m db.e3 import --schema team06_dev_e3 --source-root fixtures/e1-synthetic --catalog fixtures/e3-synthetic/catalog.json --pdf-root data/e3/pdfs --report data/e3/import.json
```

For the cached corpus, use its local `text_data` folder as the source root
and `db/e3/catalogs/gov_myscheme.json` as the catalog. Inspection reads
every local PDF and records parseability, hashes, duplicate paths, embedded
links if present, and unresolved linkage. For a large local inventory,
`inspect --workers 8` parses unique hashes in a bounded process pool.
Import considers only reviewed
catalog rows and verifies hashes again. Reported quarantine leaves the
original input untouched. Imported original bytes are stored at
`PDF_ROOT/imports/<sha256>.pdf`. A transaction registers the document,
provenance and exactly one queued job. A failed database commit can leave
an unreferenced content-addressed file, which a subsequent verified import
can reuse safely.

Dataset snapshots retain a visible title prefix and their provenance type.
An authoritative scheme page can verify its linkage without changing a
dataset snapshot into an official circular. Imported files and generated
reports belong under ignored `data/`.

Run the PostgreSQL tests with `E3_TEST_DATABASE_URL` pointed at a disposable
database, using `python -m pytest tests/test_e3.py -q`.
