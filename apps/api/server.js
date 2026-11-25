import express from 'express';

const app = express();
const port = Number(process.env.API_PORT ?? 3000);
const e2Url = new URL(process.env.E2_URL ?? 'http://127.0.0.1:8001');
if (e2Url.protocol !== 'http:' || !['127.0.0.1', '[::1]'].includes(e2Url.hostname)) {
  throw new Error('E2_URL must use a loopback HTTP address.');
}

app.get('/api/health', async (_request, response) => {
  try {
    const upstream = await fetch(new URL('/health', e2Url), {
      signal: AbortSignal.timeout(2000),
      redirect: 'error',
    });
    if (!upstream.ok || (await upstream.json()).status !== 'ok') {
      throw new Error('E2 health check failed');
    }
    response.json({ status: 'ok', service: 'e4-api', e2: 'ok' });
  } catch {
    response.status(503).json({ status: 'unavailable', service: 'e4-api', e2: 'unavailable' });
  }
});

app.listen(port, '127.0.0.1', () => console.log('API listening on 127.0.0.1:' + port));
