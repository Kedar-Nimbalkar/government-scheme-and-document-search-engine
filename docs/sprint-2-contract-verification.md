# Sprint 2 v2 contract verification

The v2 contract is required for all four engine prompts. The endpoint registry
and JSON Schemas are under `packages/contracts/v2`, with positive and failure
examples under `fixtures/v2`. The Python package provides the tokenizer,
passage ID codec, snapshot reader and E3 metadata read interface. Migration
002 and the prepared statements define PostgreSQL identity and job ownership.
The running Node, FastAPI and React foundations agree on the v2 health contract.

| Command | Result |
| --- | --- |
| `npm.cmd test` | Five tests passed, including JSON Schema fixture validation and a PostgreSQL migration and job transition test in PGlite. |
| `.venv\Scripts\python.exe -m unittest discover -s tests` | Seven tests passed, including Unicode spans, canonical passage IDs, index version rejection and checksum verification. |
| `npm.cmd run build` | Vite production build passed. |
| `npm.cmd run smoke` | Live FastAPI, Express and Vite proxy health passed. Further live probes verified the public error envelope and request ID for 404 and 503. |

The PGlite test exercises the PostgreSQL SQL and migration in one embedded
session. A standalone PostgreSQL server, parallel job claim contention,
and deployment-specific database roles remain to be verified. Search,
upload, authentication and bookmarks have contracts and fixtures. Their
engine implementations are still pending.
