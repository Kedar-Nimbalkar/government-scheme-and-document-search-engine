# Sprint 2 foundation verification

## Files changed

- Shared configuration and tooling in `.env.example`, `.gitignore`,
  `.gitattributes`, `requirements.txt`, `package.json`, and `package-lock.json`.
- Versioned contracts, installable Python utilities, sample data and tests in
  `packages/contracts/v1`, `packages/search_shared`, `fixtures/v1`, and `tests`.
- E1 PDF extraction in `engines/e1` and E2 liveness in `engines/e2`.
- E3 migration and optional PostgreSQL service in `db/migrations` and
  `compose.yaml`. E4 service reachability and browser shell in `apps/api` and
  `apps/web`.
- Startup and handoff materials in `README.md`, `scripts/smoke.mjs`, and this
  document's directory.

## Commands run

| Command | Result |
| --- | --- |
| `npm.cmd install` and `npm.cmd audit fix` | Installed dependencies, locked the patched Vite/esbuild versions, and reported zero npm advisories. |
| `.venv\Scripts\python.exe -m pip --isolated install -r requirements.txt` and `.venv\Scripts\python.exe -m pip check` | Installed Python dependencies and verified consistency. The requirements now pin the resolved transitive versions as well. |
| `npm.cmd test` | One contract fixture test passed. |
| `.venv\Scripts\python.exe -m unittest discover -s tests` | Four tests passed, covering Unicode tokenization, positional postings, PDF extraction and blank PDF rejection. |
| `npm.cmd run build` | Vite production build passed. |
| `npm.cmd run smoke` | FastAPI liveness, Express-to-FastAPI health, Vite-to-Express proxy and the web entry point passed. An additional HTTP probe verified Express returned 503 when FastAPI stopped. |

The Windows sandbox initially blocked Node's test subprocess and the
esbuild subprocess with EPERM. The test, build and live service checks
passed outside the sandbox. The smoke services were stopped after testing.

## Pending integration

PostgreSQL and Docker were unavailable locally, so the SQL migration was
not executed. The standalone revised Sprint 2 and 3 plan was also absent.
The engine teams still need to build the concurrent writer, full search,
metadata operations, authentication, bookmarks and uploads. Before connecting
uploads, E1, E3 and E4 need to agree on the durable Node-to-Python intake
and retry behavior. Full FR-09/10 belongs to Sprint 3.
