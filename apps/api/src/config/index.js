import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const workspace = fileURLToPath(new URL('../../../../', import.meta.url));

export function loadConfig(env = process.env) {
  const e2 = new URL(env.E2_URL || 'http://127.0.0.1:8001');
  if (e2.protocol !== 'http:' || !['127.0.0.1', '[::1]'].includes(e2.hostname)
      || e2.username || e2.password || e2.pathname !== '/' || e2.search || e2.hash) {
    throw new Error('E2_URL must be a loopback HTTP origin');
  }
  if (!env.E4_DATABASE_URL || Buffer.byteLength(env.JWT_SECRET || '') < 32
      || env.JWT_SECRET.includes('replace-with')) {
    throw new Error('Set E4_DATABASE_URL and a private JWT_SECRET of at least 32 bytes');
  }
  const integer = (key, fallback, min, max) => {
    const value = Number(env[key] || fallback);
    if (!Number.isInteger(value) || value < min || value > max) throw new Error(`Invalid ${key}`);
    return value;
  };
  const port = integer('API_PORT', 3000, 1, 65535);
  const corsOrigins = (env.CORS_ORIGINS ?? `http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:${port},http://localhost:${port}`)
    .split(',').map((origin) => origin.trim()).filter(Boolean);
  for (const origin of corsOrigins) {
    const url = new URL(origin);
    if (!['http:', 'https:'].includes(url.protocol) || url.origin !== origin) throw new Error('Invalid CORS_ORIGINS');
  }
  return {
    port, corsOrigins,
    authRateMax: integer('AUTH_RATE_MAX', 20, 1, 1000),
    authRateWindowMs: integer('AUTH_RATE_WINDOW_MS', 60000, 1000, 3600000),
    connectionString: env.E4_DATABASE_URL,
    jwtSecret: env.JWT_SECRET,
    sessionSeconds: integer('JWT_TTL_SECONDS', 3600, 60, 86400),
    secureCookie: env.COOKIE_SECURE === 'true',
    pdfRoot: resolve(workspace, env.PDF_ROOT || 'data/pdfs'),
    indexRoot: resolve(workspace, env.INDEX_ROOT || 'data/index'),
    e2, engineTimeout: integer('E2_TIMEOUT_MS', 4000, 1, 30000),
    maxPdfBytes: integer('MAX_PDF_BYTES', 16 * 1024 * 1024, 1, 16 * 1024 * 1024),
    python: env.PYTHON_BIN || 'python',
  };
}
