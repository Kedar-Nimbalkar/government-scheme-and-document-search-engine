import assert from 'node:assert/strict';
import { CONTRACT_VERSION, INTERNAL_ENDPOINTS, PUBLIC_ENDPOINTS } from '../packages/contracts/v2/http.js';

const apiPort = process.env.API_PORT ?? '3000';
const checks = [
  [new URL(INTERNAL_ENDPOINTS.health.path, process.env.E2_URL), 'e2'],
  [new URL(PUBLIC_ENDPOINTS.health.path, 'http://127.0.0.1:' + apiPort), 'e4-api'],
  [new URL(PUBLIC_ENDPOINTS.health.path, 'http://127.0.0.1:5173'), 'e4-api'],
];
for (const [url, service] of checks) {
  const response = await fetch(url, { signal: AbortSignal.timeout(3000) });
  assert.equal(response.status, 200, url.href);
  const health = await response.json();
  assert.equal(health.status, 'ok');
  assert.equal(health.service, service);
  assert.equal(health.contract_version, CONTRACT_VERSION);
  console.log('OK', url.href);
}
const page = await fetch('http://127.0.0.1:5173/', { signal: AbortSignal.timeout(3000) });
assert.equal(page.status, 200);
assert.match(await page.text(), /Government Scheme and Document Search Engine/);
console.log('OK Vite HTML entry point');
