import express from 'express';
import { randomUUID } from 'node:crypto';
import { CONTRACT_VERSION, INTERNAL_ENDPOINTS, PUBLIC_ENDPOINTS, errorResponse } from '../../packages/contracts/v2/http.js';

const app = express();
const port = Number(process.env.API_PORT ?? 3000);
const e2Url = new URL(process.env.E2_URL ?? 'http://127.0.0.1:8001');
if (e2Url.protocol !== 'http:' || !['127.0.0.1', '[::1]'].includes(e2Url.hostname)) {
  throw new Error('E2_URL must use a loopback HTTP address.');
}

app.use((_request, response, next) => {
  response.locals.requestId = randomUUID();
  response.set('X-Request-ID', response.locals.requestId);
  next();
});

app.get(PUBLIC_ENDPOINTS.health.path, async (_request, response) => {
  try {
    const upstream = await fetch(new URL(INTERNAL_ENDPOINTS.health.path, e2Url), {
      signal: AbortSignal.timeout(2000),
      redirect: 'error',
    });
    if (!upstream.ok) {
      throw new Error('E2 health check failed');
    }
    const health = await upstream.json();
    if (health.status !== 'ok' || health.contract_version !== CONTRACT_VERSION) {
      throw new Error('E2 health check or contract version check failed');
    }
    response.json({ status: 'ok', service: 'e4-api', e2: 'ok', contract_version: CONTRACT_VERSION });
  } catch {
    response.status(503).json(errorResponse('SERVICE_UNAVAILABLE', 'Query service unavailable', response.locals.requestId));
  }
});

app.use('/api', (_request, response) => {
  response.status(404).json(errorResponse('NOT_FOUND', 'API route not found', response.locals.requestId));
});

app.listen(port, '127.0.0.1', () => console.log('API listening on 127.0.0.1:' + port));
