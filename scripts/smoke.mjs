import assert from 'node:assert/strict';

const apiPort = process.env.API_PORT ?? '3000';
const checks = [
  [new URL('/health', process.env.E2_URL), 'e2'],
  [new URL('/api/health', 'http://127.0.0.1:' + apiPort), 'e4-api'],
  [new URL('/api/health', 'http://127.0.0.1:5173'), 'e4-api'],
];
for (const [url, service] of checks) {
  const response = await fetch(url, { signal: AbortSignal.timeout(3000) });
  assert.equal(response.status, 200, url.href);
  const health = await response.json();
  assert.equal(health.status, 'ok');
  assert.equal(health.service, service);
  console.log('OK', url.href);
}
const page = await fetch('http://127.0.0.1:5173/', { signal: AbortSignal.timeout(3000) });
assert.equal(page.status, 200);
assert.match(await page.text(), /Government Scheme and Document Search Engine/);
console.log('OK Vite HTML entry point');
