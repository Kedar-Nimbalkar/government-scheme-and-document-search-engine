# Government Scheme and Document Search Engine

Team 06's Sprint 2 foundation. See the [engine handoff](docs/sprint-2-handoff.md)
and [v2 contracts](packages/contracts/v2/README.md).

## Local startup

From the workspace root, use Node 22.12+ or Node 24 LTS, and Python 3.11+.
Run the following in PowerShell.

```powershell
Copy-Item .env.example .env
npm.cmd ci
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r engines/e2/requirements.txt
```

Run these commands in three separate terminals from the same directory.
Activate the Python environment in the E2 terminal.

```powershell
npm.cmd run dev:e2
npm.cmd run dev:api
npm.cmd run dev:web
```

Open http://127.0.0.1:5173. Run `npm.cmd run smoke` in a fourth terminal.
The smoke checks cover live FastAPI, Express, and the Vite proxy. For a local
build served outside Vite, host the built web files on the same origin as
Express or configure a same-origin reverse proxy.

For PostgreSQL, create the development database locally or run
`docker compose up -d postgres`. Set DATABASE_URL in .env and apply the
migrations in order. With Compose defaults, run only the files that are pending:

```powershell
Get-Content -Raw db/migrations/001_foundation.sql | docker compose exec -T postgres psql -U team06 -d team06 -v ON_ERROR_STOP=1
Get-Content -Raw db/migrations/002_contract_v2.sql | docker compose exec -T postgres psql -U team06 -d team06 -v ON_ERROR_STOP=1
Get-Content -Raw db/migrations/003_e3_metadata.sql | docker compose exec -T postgres psql -U team06 -d team06 -v ON_ERROR_STOP=1
Get-Content -Raw db/migrations/004_e3_services.sql | docker compose exec -T postgres psql -U team06 -d team06 -v ON_ERROR_STOP=1
```

With local PostgreSQL use `psql <database-url> -v ON_ERROR_STOP=1 -f <migration-file>`
for each pending file.
See the [E3 runbook](db/e3/README.md) for development schemas, seed accounts,
and reviewed PDF import.
See the [persistence handoff](docs/sprint-2-e3-persistence.md) for runtime
role provisioning and E1, E2, and E4 connections.
The current health check reports service reachability. It does not check
database or index readiness. Local data and credentials are ignored by Git.

## Foundation tests

```powershell
npm.cmd test
npm.cmd run build
python -m pip install -r db/e3/requirements.txt -r engines/e2/requirements-dev.txt
python -m pytest -q
```
