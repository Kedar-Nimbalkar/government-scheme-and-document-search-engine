Act as the integration architect for Team 06. Implement the shared foundation for Sprint 2 of the Government Scheme and Document Search Engine. Use Gate 0, Gate 1, Sprint 1 Handbook and the Sprint 2 & 3 plan as the baseline. Execute changes in the repository, not just a design explanation.

## Scope and architecture

Use React + Vite (JavaScript), Node.js LTS + Express as the sole public backend, Python 3 multiprocessing for E1, Python FastAPI for internal E2, PostgreSQL for E3, local immutable index files and PDF storage. Use PyMuPDF only after license review; otherwise select a compatible page-aware extractor and document the change. No OCR, LLM, Elasticsearch, Redis or paid services. E1/E3 foundations first; E2/E4 start on shared fixtures in parallel.

## Repository setup

Inspect existing files before editing. Preserve working code. Establish apps/web, apps/api, engines/e1, engines/e2, packages/contracts, db/migrations, fixtures, tests and docs. Use shared Python tokenizer/index-reader package; define environment variables for database, index root, PDF root, E2 URL and JWT secret. Pin dependencies, provide .env.example, gitignore secrets/data, and scripts for local startup. PostgreSQL may run locally or via optional Compose. Bind internal services to loopback; proxy browser calls through Express.

## Work allocation

E1 Unnati Hiremath: FR-01–03. E2 Anvit Naik: FR-04–05 and baseline FR-06–07. E3 Khushi Devagiri: FR-08 and persistence for FR-11/13. E4 Kedar Nimbalkar: FR-11–13. Reserve full FR-09/10 for Sprint 3; prepare schema and security/operational logs now. Commit each engine separately; avoid modifying another engine without coordination.

## Definition of done

Create versioned shared contract files and sample fixtures before other agents begin. Run a basic startup/health smoke check. Record commands, files changed, tests actually run and blockers. Never claim completed features using mocks. Give the shared contracts and this prompt to every engine agent before its own prompts.
