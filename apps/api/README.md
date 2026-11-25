# E4 · Client interface and ingestion

E4 serves the public REST API in Express. The React app lives in
[`apps/web`](../web). Search uses E2's loopback FastAPI service. E3 owns
account, bookmark, metadata and queue persistence. The indexer runs as a
separate process with E1 credentials.

## Modules

| Layer | Responsibility |
| --- | --- |
| `apps/web/src/` | React search, account pages and a same-origin API client |
| `src/routes/` | Shared endpoint registration and middleware order |
| `src/controllers/` | Extract HTTP inputs and write status, JSON, cookies or PDFs |
| `src/services/` | Credential verification, sessions, search enrichment and existing upload workflow |
| `src/adapters/`, `src/persistence/` | E2 HTTP, E1 PDF validator process and E3 repository |
| PostgreSQL, E1 index and PDF storage | Engine-owned persistent data |

`src/config/` validates environment settings. `src/validators/` parses
request data, `src/utils/` formats public PDF links, and `src/exceptions/`
defines typed application errors.

Request context and logging run first, followed by the CORS allowlist and
JSON parser. Routes attach validation, authentication, RBAC and throttling
where required. Unknown API routes and the centralized JSON error handler
run after the routes. Logs record a request ID, method, status and duration.
Authentication failures also use the E3 operational event interface.

`credentials` accepts only username and password for public registration.
`createAuth.register` hashes the password and delegates citizen creation
to E3. `createAuth.session` validates JWT expiration and checks the active
E3 session. `authenticationMiddleware.roles` checks the persisted account
role and `createAuth.logout` revokes the session. The browser uses the
HTTP-only same-site cookie and retrieves the account via `/api/auth/me`.

`searchParameters` bounds query length and pagination. `createSearchService`
delegates ranking and autocomplete to E2 and gets filter options and
result metadata from E3.

`CORS_ORIGINS` is a comma-separated allowlist of exact browser origins.
The auth limiter shares 20 attempts per 60 seconds across login and
registration per Express client IP by default. It resets on restart and
treats clients behind the same local proxy as one IP. Configure
`AUTH_RATE_MAX` and `AUTH_RATE_WINDOW_MS` for local runs.

## Local setup

From the repository root:

```powershell
npm.cmd ci
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r db/e3/requirements.txt -r engines/e2/requirements.txt
Copy-Item .env.example .env
```

Create a virtual environment first with `python -m venv .venv` if needed.
Apply migrations 001–006 in order and then provision runtime role grants.
For a fresh local schema, set the owner connection as an environment
variable and run:

```powershell
python -m db.e3 migrate-dev --schema team06_dev_e4
psql $env:DATABASE_URL -v ON_ERROR_STOP=1 -c "SET search_path TO team06_dev_e4" -f db/runtime-roles.sql
```

Create separate login accounts for E1, E2 and E4 using the
[E3 role setup](../../docs/sprint-2-e3-persistence.md). Set their schema
search path and put their URLs in `E1_DATABASE_URL`, `E2_DATABASE_URL`,
and `E4_DATABASE_URL` in `.env`. If passing PostgreSQL startup options
in a URL, encode spaces as `%20` for psycopg. Set the same `PDF_ROOT` and
`INDEX_ROOT` for all engines. Generate a private JWT secret with
`node -e "console.log(require('node:crypto').randomBytes(32).toString('hex'))"`
and enter it in the ignored `.env`. Enable `COOKIE_SECURE` for HTTPS.
For development HTTP, keep the default `false`. Put the active virtual
environment's Python on PATH or set `PYTHON_BIN`.

Run from the repository root in four terminals:

```powershell
npm.cmd run dev:e1
npm.cmd run dev:e2
npm.cmd run dev:api
npm.cmd run dev:web
```

Open the Vite URL printed in the terminal, normally
`http://127.0.0.1:5173`. The Vite proxy keeps browser requests same-origin.
E4 binds to loopback on port 3000 by default, and E2 binds to loopback on
8001. Each worker poll checks for eligible jobs before running E1 and
initializes an empty index when no manifest exists.

## Development credentials

Use environment-supplied credentials with E3's local-only seed. For example:

```powershell
$env:E3_TEST_ADMIN_USERNAME = 'local-admin'
$env:E3_TEST_ADMIN_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host 'New development password' -AsSecureString)).Password
python -m db.e3 seed --schema team06_dev_e4
Remove-Item Env:E3_TEST_ADMIN_PASSWORD
```

Use the same username and password at sign-in. The seed stores a bcrypt
hash. For a pre-seeded department account use the
`E3_TEST_DEPARTMENT_USERNAME` and `E3_TEST_DEPARTMENT_PASSWORD` variables.
An administrator can also create department accounts in the dashboard.

## REST endpoints and workflows

| Scope | Endpoints |
| --- | --- |
| Public | `GET /api/health`, `/api/search`, `/api/autocomplete`, `/api/filters`, `/api/documents/:id`, `/api/documents/:id/pdf`; `POST /api/auth/register`, `/api/auth/login` |
| Signed in | `GET /api/auth/me`; `POST /api/auth/logout`; `GET/POST /api/bookmarks`; `DELETE /api/bookmarks/:id` |
| Staff | `POST /api/documents`, `GET /api/documents`, `GET /api/schemes` |
| Per-document status | `GET /api/documents/:id/status` exposes eligible public status and private job details to the uploader or administrator |
| Administrator | `GET/POST /api/admin/users` |

The v2 upload and status routes are `POST /api/documents` and
`GET /api/documents/:id/status`. The status carries the job UUID.
Search accepts `q`, optional `state`, `department`, `category`, `page`,
and `k` (default 10). E4 forwards these to E2 and enriches matching IDs
through E3. React uses UTF-16 ranges to render original snippets as text
and `mark` elements. PDF links use a browser page fragment.

Registration creates citizens. Login verifies bcrypt credentials and
issues an expiring, signed JWT. Browser requests use a same-origin
HttpOnly cookie, and API clients may use the bearer token returned by
login. Requests verify the JWT and its active session row in PostgreSQL.
Logout deletes that session. Role access is checked on the server.
Bookmarks are scoped by user and survive process restarts.

Uploads require a staff JWT, a valid scheme linkage, title, circular
number, issue date, and PDF. The server generates the staging filename.
The E1 parser checks signature, readability and extractability after the
size, MIME and department checks. E3 promotes the original and inserts
one document and one durable job in the same database transaction.
The response is 202 once the job is queued. E1 owns all subsequent states,
and dashboards poll every 2.5 seconds. E3's maintenance reconciliation
handles unreferenced originals after a storage or commit failure.

## Demonstration and tests

1. Import the reviewed MyScheme snapshots using the
   [E3 import runbook](../../db/e3/README.md), or use a fresh local test
   schema for a synthetic upload demonstration. To create a synthetic scheme
   in that schema's SQL session:

   ```sql
   INSERT INTO department (dept_name,level) VALUES ('Synthetic Test Department','State')
       ON CONFLICT DO NOTHING;
   INSERT INTO scheme (dept_id,scheme_name,category,state)
   SELECT dept_id,'Synthetic Housing Grant','Housing','Synthetic State'
   FROM department WHERE dept_name='Synthetic Test Department' AND level='State'
   ON CONFLICT DO NOTHING;
   ```

2. Sign in as staff. Select a verified scheme and upload its source PDF
   with verified metadata. For the synthetic scheme use
   [the synthetic E1 PDF](../../fixtures/e1-synthetic/digital-and-mixed.pdf)
   with a clearly labelled synthetic title, circular number and date.
   Watch its actual state reach `INDEXED`.
3. Search for `"grant housing"` with the synthetic state filter. Open the
   PDF at the returned page. Register a citizen account and save both the
   document and its scheme. Sign out and back in to verify persistence.

Run `npm.cmd test` and `npm.cmd run build`. To exercise the full integration
test, set `E3_TEST_DATABASE_URL` to a disposable administrator database and
`E4_TEST_PYTHON` to the project Python executable. It creates an isolated
schema and starts real E1 and E2 subprocesses.

In the Sprint 2 baseline, with the disposable PostgreSQL cluster available, E4 verification
passed 47 Python tests, eight Node tests, and the Vite build. The browser
workflow also passed in headless Edge with the real services. It covered
staff upload, indexing status, citizen registration, autocomplete, filtered
highlighted results, a page-specific PDF link, and bookmark persistence.
The browser check uses optional `E4_TEST_BROWSER` and
`E4_TEST_BROWSER_MODULE` paths for an installed Chromium browser and a local
Playwright Core module. Run the Vite build before enabling that check.
Use `node --test --test-concurrency=1 tests/*.test.js` on development
machines where the browser and PostgreSQL compete with parallel tests.

To demonstrate Phase 1, register a citizen, verify the profile, and visit
`#admin` to see the access-denied page. Return to search, enter a prefix,
select a suggested term and apply a filter. Sign out, then use a seeded
staff account to verify its role-specific home page.

FR-11, FR-12 and FR-13 are implemented for the Sprint 2 workflow. Gate 1
load/relevance acceptance, production security deployment, supersession
and transactional audit remain separate work.

For local TXT and PDF corpus ingestion, see the [Phase 2 runbook](../../docs/phase-2-mixed-corpus.md).
