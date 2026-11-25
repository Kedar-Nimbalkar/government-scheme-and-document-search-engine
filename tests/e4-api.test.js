const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs/promises');
const { execFile, spawn } = require('node:child_process');
const { createServer } = require('node:http');
const { randomBytes, randomUUID } = require('node:crypto');
const { once } = require('node:events');
const { tmpdir } = require('node:os');
const { resolve, dirname, join } = require('node:path');
const { pathToFileURL } = require('node:url');
const { setTimeout: delay } = require('node:timers/promises');
const { promisify } = require('node:util');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const { Pool } = require('pg');

const database = process.env.E3_TEST_DATABASE_URL;
const fixture = resolve(__dirname, '../fixtures/e1-synthetic');
const execute = promisify(execFile);

async function listen(server) {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return `http://127.0.0.1:${server.address().port}`;
}

async function stop(child) {
  if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return;
  const finished = once(child, 'exit');
  if (process.platform === 'win32') {
    await execute('taskkill.exe', ['/PID', String(child.pid), '/T', '/F']).catch(() => {});
  } else {
    try { process.kill(-child.pid, 'SIGTERM'); } catch {}
  }
  let timer;
  try {
    await Promise.race([finished, new Promise((_done, reject) => {
      timer = setTimeout(() => reject(new Error('Test service did not stop')), 5000);
    })]);
  } finally { clearTimeout(timer); }
}

async function until(check, milliseconds = 20000) {
  const deadline = Date.now() + milliseconds;
  do {
    if (await check()) return;
    await delay(250);
  } while (Date.now() < deadline);
  assert.fail('Timed out waiting for service or indexing status');
}

async function verifyBrowser(base, scheme, password) {
  const { chromium } = await import(pathToFileURL(process.env.E4_TEST_BROWSER_MODULE).href);
  const browser = await chromium.launch({ executablePath: process.env.E4_TEST_BROWSER, headless: true });
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(12000);
    await page.goto(base);
    await page.getByRole('link', { name: 'Sign in', exact: true }).click();
    await page.getByLabel('Username').fill('test-dept');
    await page.getByLabel('Password').fill(password);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.getByRole('heading', { name: 'Department dashboard' }).waitFor();
    await page.getByLabel('Scheme', { exact: true }).selectOption(scheme);
    await page.getByLabel('Document title').fill('Synthetic browser upload');
    await page.getByLabel('Circular number').fill('TEST-BROWSER');
    await page.getByLabel('Issue date').fill('2026-01-10');
    await page.getByLabel('PDF file').setInputFiles(resolve(fixture, '../v1/housing-guidelines.pdf'));
    await page.getByRole('button', { name: 'Upload PDF' }).click();
    await page.getByRole('row', { name: /Synthetic browser upload/ }).getByText('INDEXED', { exact: true })
      .waitFor({ timeout: 25000 });

    await page.getByRole('button', { name: 'Sign out' }).click();
    await page.getByRole('link', { name: 'Register', exact: true }).click();
    await page.getByLabel('Username').fill('browser-citizen');
    await page.getByLabel('Password').fill(password);
    await page.getByRole('button', { name: 'Create account' }).click();
    await page.getByRole('link', { name: 'Bookmarks' }).waitFor();
    await page.getByLabel('Search query').fill('guid');
    await page.getByRole('button', { name: 'guidelines', exact: true }).click();
    await page.getByLabel('State', { exact: true }).selectOption('Karnataka');
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    const result = page.locator('article').filter({ has: page.getByRole('heading', { name: 'Synthetic browser upload' }) });
    await result.waitFor();
    assert.equal((await result.locator('mark').textContent()).toLowerCase(), 'guidelines');
    assert.match(await result.getByRole('link', { name: 'Open PDF' }).getAttribute('href'), /\/pdf#page=1$/);
    await result.getByRole('button', { name: 'Save document' }).click();
    await page.getByText('Bookmark saved.').waitFor();
    await page.getByRole('link', { name: 'Bookmarks' }).click();
    await page.getByRole('heading', { name: 'Synthetic browser upload' }).waitFor();
    await page.getByRole('button', { name: 'Sign out' }).click();
    await page.getByRole('link', { name: 'Sign in', exact: true }).click();
    await page.getByLabel('Username').fill('browser-citizen');
    await page.getByLabel('Password').fill(password);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.getByRole('link', { name: 'Bookmarks' }).click();
    await page.getByRole('heading', { name: 'Synthetic browser upload' }).waitFor();
    console.log('Browser workflow passed with real E1–E4 services.');
  } finally {
    await browser.close();
  }
}

test('public API timeout mapping and safe React highlights', async (t) => {
  const slow = createServer((_req, _res) => {});
  const base = await listen(slow);
  t.after(() => { slow.closeAllConnections(); slow.close(); });
  const { queryService } = await import('../apps/api/src/engines.js');
  await assert.rejects(queryService({ e2: base, engineTimeout: 20 }).search({ query: 'grant' }), { status: 504 });
  const { Highlighted } = await import('../apps/web/src/highlight.js');
  const { createElement } = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const text = '🏠 grant <script>alert(1)</script>';
  const markup = renderToStaticMarkup(createElement(Highlighted, { text, spans: [{ start: 3, end: 8 }] }));
  assert.match(markup, /<mark>grant<\/mark>/);
  assert.match(markup, /&lt;script&gt;/);
  assert.doesNotMatch(markup, /<script>/);
});

test('real PostgreSQL, E1 worker, E2 HTTP and E4 public workflows', { skip: !database, timeout: 75000 }, async (t) => {
  const schema = 'e4_test_' + randomUUID().replaceAll('-', '');
  const root = await fs.mkdtemp(join(tmpdir(), 'team06-e4-'));
  const admin = new Pool({ connectionString: database, options: '-c search_path=' + schema });
  const roleUrl = (role) => {
    const url = new URL(database);
    url.search = 'options=' + encodeURIComponent(`-c search_path=${schema} -c role=team06_${role}`);
    return url.toString();
  };
  let e1, e2, listener, repository;
  async function closeApi() {
    if (listener?.listening) await new Promise((done) => listener.close(done));
    if (repository) await repository.pool.end();
    listener = repository = null;
  }
  t.after(async () => {
    try {
      await Promise.all([stop(e1), stop(e2), closeApi()]);
    } finally {
      await admin.query(`DROP SCHEMA IF EXISTS "${schema}" CASCADE`);
      await admin.end();
      assert.equal(dirname(resolve(root)), resolve(tmpdir()));
      await fs.rm(root, { recursive: true });
    }
  });
  await admin.query(`CREATE SCHEMA "${schema}"`);
  const migrations = resolve(__dirname, '../db/migrations');
  for (const migration of (await fs.readdir(migrations)).filter((file) => file.endsWith('.sql')).sort()) {
    await admin.query(await fs.readFile(join(migrations, migration), 'utf8'));
  }
  await admin.query(await fs.readFile(resolve(__dirname, '../db/runtime-roles.sql'), 'utf8'));
  const one = async (sql, values) => (await admin.query(sql, values)).rows[0];
  const { dept_id: home } = await one("INSERT INTO department(dept_name,level) VALUES ('Test Housing','State') RETURNING dept_id");
  const { dept_id: other } = await one("INSERT INTO department(dept_name,level) VALUES ('Test Other','State') RETURNING dept_id");
  const { scheme_id: scheme } = await one(`
    INSERT INTO scheme(dept_id,scheme_name,category,state) VALUES ($1,'Synthetic Housing','Housing','Karnataka')
    RETURNING scheme_id
  `, [home]);
  const { scheme_id: foreign } = await one(`
    INSERT INTO scheme(dept_id,scheme_name,category,state) VALUES ($1,'Synthetic Other','Housing','Goa')
    RETURNING scheme_id
  `, [other]);
  const testPassword = 'synthetic-fixture-secret';
  await admin.query("INSERT INTO app_user(username,password_hash,role) VALUES ('test-admin',$1,'admin')",
    [await bcrypt.hash(testPassword, 10)]);
  const portProbe = createServer();
  const e2Url = await listen(portProbe);
  await new Promise((done) => portProbe.close(done));
  const { loadConfig } = await import('../apps/api/src/config.js');
  const { createApp } = await import('../apps/api/src/app.js');
  const config = loadConfig({
    E4_DATABASE_URL: roleUrl('e4'), JWT_SECRET: randomBytes(32).toString('hex'),
    E2_URL: e2Url, PDF_ROOT: join(root, 'pdfs'), INDEX_ROOT: join(root, 'index'),
    PYTHON_BIN: process.env.E4_TEST_PYTHON || 'python', MAX_PDF_BYTES: '16384',
  });
  const service = (args, env) => {
    const child = spawn(config.python, args, {
      cwd: resolve(__dirname, '..'), env: { ...process.env, ...env },
      detached: process.platform !== 'win32', windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'],
    });
    child.pollFailures = 0;
    child.stdout.on('data', (data) => {
      child.pollFailures += (data.toString().match(/INDEXER_POLL_FAILED/g) || []).length;
    });
    return child;
  };
  e2 = service(['-m', 'uvicorn', 'engines.e2.main:app', '--host', '127.0.0.1',
    '--port', new URL(e2Url).port, '--no-access-log'], { E2_DATABASE_URL: roleUrl('e2'), INDEX_ROOT: config.indexRoot });
  async function openApi() {
    const { app, repo } = await createApp(config);
    repository = repo;
    if (process.env.E4_TEST_BROWSER_MODULE) {
      const express = require('express');
      const web = express();
      web.use(app);
      web.use(express.static(resolve(__dirname, '../apps/web/dist')));
      listener = createServer(web);
    } else listener = createServer(app);
    return listen(listener);
  }
  let base = await openApi();
  await until(async () => (await fetch(base + '/api/health')).ok);
  e1 = service(['-m', 'apps.api.indexer'], {
    E1_DATABASE_URL: roleUrl('e1'), INDEX_ROOT: config.indexRoot, PDF_ROOT: config.pdfRoot,
  });
  async function call(path, method = 'GET', body, token, cookie) {
    const form = body instanceof FormData;
    const response = await fetch(base + '/api' + path, {
      method, headers: {
        ...(body && !form ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: 'Bearer ' + token } : {}),
        ...(cookie ? { Cookie: cookie } : {}),
      }, body: body && !form ? JSON.stringify(body) : body,
    });
    return {
      response, status: response.status,
      ...(response.headers.get('content-type')?.includes('json') ? { data: await response.json() } : {}),
    };
  }
  const login = (username, password = testPassword) => call('/auth/login', 'POST', { username, password });
  const adminLogin = await login('test-admin');
  assert.equal(adminLogin.status, 200);
  const adminToken = adminLogin.data.access_token;
  const adminCookie = adminLogin.response.headers.get('set-cookie');
  assert.match(adminCookie, /HttpOnly.*SameSite=Strict/i);
  assert.equal((await call('/auth/me', 'GET', undefined, null, adminCookie.split(';')[0])).data.role, 'admin');
  const newDepartment = await call('/admin/users', 'POST', { username: 'test-dept', password: testPassword, dept_id: home }, adminToken);
  assert.equal(newDepartment.status, 201);
  assert.equal(newDepartment.data.role, 'department');
  assert.equal((await call('/admin/users', 'GET', undefined, adminToken)).data.users.length, 2);
  const departmentToken = (await login('test-dept')).data.access_token;
  assert.equal((await call('/admin/users', 'GET', undefined, departmentToken)).status, 403);
  assert.deepEqual((await call('/schemes', 'GET', undefined, departmentToken)).data.map((row) => row.scheme_id), [scheme]);

  assert.equal((await call('/auth/register', 'POST', { username: 'escalate', password: testPassword, role: 'admin' })).status, 400);
  assert.equal((await call('/auth/register', 'POST', { username: 'test-citizen', password: testPassword })).status, 201);
  assert.equal((await call('/auth/register', 'POST', { username: 'test-citizen', password: testPassword })).status, 409);
  assert.equal((await login('test-citizen', 'wrong-password')).status, 401);
  const citizenLogin = await login('test-citizen');
  const citizen = citizenLogin.data.access_token;
  const user = (await call('/auth/me', 'GET', undefined, citizen)).data;
  assert.equal(user.role, 'citizen');
  const expired = jwt.sign({}, config.jwtSecret, {
    issuer: 'team06-e4', audience: 'team06-api', subject: user.user_id, jwtid: randomUUID(), expiresIn: -10,
  });
  for (const token of [expired, 'invalid.jwt']) assert.equal((await call('/auth/me', 'GET', undefined, token)).status, 401);

  const pdf = await fs.readFile(join(fixture, 'digital-and-mixed.pdf'));
  function upload(bytes = pdf, schemeId = scheme, type = 'application/pdf') {
    const body = new FormData();
    body.set('scheme_id', schemeId); body.set('title', 'Synthetic upload');
    body.set('circular_no', 'TEST-01'); body.set('issue_date', '2026-01-10');
    body.set('file', new Blob([bytes], { type }), 'synthetic.pdf');
    return body;
  }
  assert.equal((await call('/documents', 'POST', upload())).status, 401);
  assert.equal((await call('/documents', 'POST', upload(), citizen)).status, 403);
  assert.equal((await call('/documents', 'POST', upload(pdf, foreign), departmentToken)).status, 403);
  assert.equal((await call('/documents', 'POST', upload(pdf, scheme, 'text/plain'), departmentToken)).status, 400);
  assert.equal((await call('/documents', 'POST', upload(Buffer.alloc(17000)), departmentToken)).status, 413);
  assert.equal((await call('/documents', 'POST', upload(Buffer.from('not pdf')), departmentToken)).status, 400);
  assert.equal((await call('/documents', 'POST', upload(Buffer.from('%PDF-invalid')), departmentToken)).data.error.code, 'PDF_CORRUPT');
  const image = await fs.readFile(join(fixture, 'image-only.pdf'));
  assert.equal((await call('/documents', 'POST', upload(image), departmentToken)).data.error.code, 'PDF_NO_TEXT');
  const encrypted = join(root, 'encrypted.pdf');
  await execute(config.python, ['-c',
    'from pypdf import PdfWriter; import sys; p=PdfWriter(clone_from=sys.argv[1]); p.encrypt("test"); p.write(sys.argv[2])',
    join(fixture, 'digital-and-mixed.pdf'), encrypted], { windowsHide: true });
  assert.equal((await call('/documents', 'POST', upload(await fs.readFile(encrypted)), departmentToken)).data.error.code, 'PDF_ENCRYPTED');
  assert.equal((await one('SELECT count(*)::integer AS count FROM ingestion_job')).count, 0);
  const accepted = await call('/documents', 'POST', upload(), departmentToken);
  assert.equal(accepted.status, 202);
  const { doc_id: docId, job_id: jobId } = accepted.data;
  assert.equal(accepted.data.status, 'QUEUED');
  assert.equal((await call('/documents', 'POST', upload(), departmentToken)).status, 409);
  await until(async () => {
    assert.equal(e1.exitCode, null, 'Indexer process exited before completing its job');
    assert.ok(e1.pollFailures < 3, 'Indexer could not access or drain its job queue');
    const status = await call(`/documents/${docId}/status`, 'GET', undefined, departmentToken);
    assert.equal(status.status, 200);
    assert.equal(status.data.job_id, jobId);
    assert.notEqual(status.data.index_status, 'FAILED', status.data.failure_reason);
    return status.data.index_status === 'INDEXED';
  });
  assert.equal((await call('/documents', 'GET', undefined, departmentToken)).data[0].doc_id, docId);
  assert.equal((await call('/documents', 'GET', undefined, adminToken)).data[0].doc_id, docId);

  assert.equal((await call('/filters')).data.state[0], 'Karnataka');
  for (const query of ['housing', '"grant housing"', 'housing AND NOT coastal']) {
    const result = await call('/search?' + new URLSearchParams({ q: query, state: 'Karnataka', k: '1' }));
    assert.equal(result.status, 200);
    assert.equal(result.data.results.length, 1);
    const hit = result.data.results[0];
    assert.equal(hit.doc_id, docId);
    assert.equal(hit.scheme_name, 'Synthetic Housing');
    assert.equal(hit.state, 'Karnataka');
    assert.ok(hit.match_spans.length && hit.score > 0);
    assert.equal(hit.pdf_url, `/api/documents/${docId}/pdf`);
  }
  assert.equal((await call('/search?q=housing&state=Goa')).data.total, 0);
  assert.equal((await call('/search?q=housing&k=51')).status, 400);
  assert.equal((await call('/search?q=housing%20AND%20%28')).response.headers.get('x-search-warning'), 'BOOLEAN_FALLBACK');
  assert.ok((await call('/autocomplete?prefix=hou')).data.suggestions.includes('housing'));
  assert.equal((await call(`/documents/${docId}`)).data.doc_id, docId);
  const original = await fetch(base + `/api/documents/${docId}/pdf`);
  assert.equal(original.headers.get('content-type'), 'application/pdf');
  assert.deepEqual(Buffer.from(await original.arrayBuffer()), pdf);
  const partial = await fetch(base + `/api/documents/${docId}/pdf`, { headers: { Range: 'bytes=0-4' } });
  assert.equal(partial.status, 206);
  assert.equal(await partial.text(), '%PDF-');

  const bookmark = await call('/bookmarks', 'POST', { doc_id: docId, page_no: 1 }, citizen);
  assert.equal(bookmark.status, 201);
  assert.equal((await call('/bookmarks', 'POST', { scheme_id: scheme }, citizen)).status, 201);
  assert.equal((await call('/bookmarks', 'POST', { doc_id: docId, page_no: 1 }, citizen)).status, 409);
  assert.equal((await call('/bookmarks/' + bookmark.data.bookmark_id, 'DELETE', undefined, departmentToken)).status, 404);
  assert.equal((await call('/auth/logout', 'POST', undefined, citizen)).status, 200);
  await closeApi();
  base = await openApi();
  assert.equal((await call('/auth/me', 'GET', undefined, citizen)).status, 401);
  const again = (await login('test-citizen')).data.access_token;
  assert.equal((await call('/bookmarks', 'GET', undefined, again)).data.length, 2);
  assert.equal((await call('/bookmarks/' + bookmark.data.bookmark_id, 'DELETE', undefined, again)).status, 204);
  assert.equal((await call('/bookmarks', 'GET', undefined, again)).data.length, 1);
  if (process.env.E4_TEST_BROWSER_MODULE) await verifyBrowser(base, scheme, testPassword);
  await stop(e2);
  const failure = await call('/search?q=housing');
  assert.equal(failure.status, 503);
  assert.equal(failure.data.error.request_id, failure.response.headers.get('x-request-id'));
});
