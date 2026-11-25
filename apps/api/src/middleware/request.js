import { randomUUID } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { ApiError, forbidden } from '../exceptions/api-error.js';

export function requestContext(request, response, next) {
  const requestId = randomUUID();
  const started = performance.now();
  response.locals.requestId = requestId;
  response.set({ 'X-Request-ID': requestId, 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store' });
  response.once('finish', () => console.info(JSON.stringify({
    event: 'HTTP_REQUEST', request_id: requestId, method: request.method,
    status: response.statusCode, duration_ms: Math.round(performance.now() - started),
  })));
  next();
}

export function cors(config) {
  const origins = new Set(config.corsOrigins ?? []);
  return (request, response, next) => {
    const origin = request.get('origin');
    response.vary('Origin');
    if (!origin) return next();
    // Vite rewrites the upstream Host. Local browser origins are configured explicitly.
    if (!origins.has(origin)) throw forbidden();
    response.set({
      'Access-Control-Allow-Origin': origin,
      'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Expose-Headers': 'X-Request-ID, X-Search-Warning, Retry-After',
    });
    if (request.method !== 'OPTIONS' || !request.get('access-control-request-method')) return next();
    response.set({
      'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    });
    response.sendStatus(204);
  };
}

export function validate(source, parse) {
  return (request, response, next) => {
    response.locals.input = parse(request[source]);
    next();
  };
}

export function authRateLimit({ authRateWindowMs = 60000, authRateMax = 20 }) {
  const clients = new Map();
  let nextSweep = 0;
  return (request, response, next) => {
    const now = Date.now();
    if (now >= nextSweep) {
      for (const [key, entry] of clients) if (entry.until <= now) clients.delete(key);
      nextSweep = now + authRateWindowMs;
    }
    const key = request.ip;
    const entry = clients.get(key) ?? { until: 0, count: 0 };
    if (entry.until <= now) {
      entry.until = now + authRateWindowMs;
      entry.count = 0;
      clients.set(key, entry);
    }
    if (++entry.count > authRateMax) {
      response.set('Retry-After', String(Math.ceil((entry.until - now) / 1000)));
      throw new ApiError(429, 'RATE_LIMITED', 'Too many authentication attempts. Try again later.');
    }
    next();
  };
}
