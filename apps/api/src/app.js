import express from 'express';
import { PUBLIC_ENDPOINTS, CONTRACT_VERSION } from '../../../packages/contracts/v2/http.js';
import { createAuth } from './auth.js';
import { controllers } from './controllers.js';
import { uploadMiddleware } from './documents.js';
import { queryService } from './engines.js';
import { absent, handleError, requestId, unavailable } from './http.js';
import { createE3Repository } from './persistence/repository.js';

export async function createApp(config, repo = createE3Repository(config)) {
  const app = express();
  const engine = queryService(config);
  const auth = createAuth(repo, config);
  const handlers = controllers(repo, engine, auth, config);
  const signedIn = auth.middleware();
  const staff = auth.roles('admin', 'department');
  const admin = auth.roles('admin');
  app.disable('x-powered-by');
  app.use('/api', requestId, (_request, response, next) => {
    response.set('Cache-Control', 'no-store');
    next();
  }, express.json({ limit: '32kb' }));
  app.get(PUBLIC_ENDPOINTS.health.path, async (_request, response) => {
    try {
      await Promise.all([engine.health(), repo.pool.query('SELECT 1')]);
    } catch {
      throw unavailable('A dependent service is unavailable');
    }
    response.json({ status: 'ok', service: 'e4-api', e2: 'ok', contract_version: CONTRACT_VERSION });
  });
  app.post(PUBLIC_ENDPOINTS.register.path, handlers.register);
  app.post(PUBLIC_ENDPOINTS.login.path, handlers.login);
  app.get(PUBLIC_ENDPOINTS.me.path, signedIn, handlers.me);
  app.post(PUBLIC_ENDPOINTS.logout.path, signedIn, handlers.logout);
  app.get(PUBLIC_ENDPOINTS.search.path, handlers.search);
  app.get(PUBLIC_ENDPOINTS.autocomplete.path, handlers.autocomplete);
  app.get(PUBLIC_ENDPOINTS.filters.path, handlers.filters);
  app.get(PUBLIC_ENDPOINTS.bookmarks.path, signedIn, handlers.bookmarks);
  app.post(PUBLIC_ENDPOINTS.createBookmark.path, signedIn, handlers.bookmark);
  app.delete(PUBLIC_ENDPOINTS.deleteBookmark.path, signedIn, handlers.removeBookmark);
  app.get(PUBLIC_ENDPOINTS.document.path, handlers.document);
  app.get(PUBLIC_ENDPOINTS.pdf.path, handlers.pdf);
  app.get(PUBLIC_ENDPOINTS.status.path, auth.middleware(false), handlers.status);
  app.post(PUBLIC_ENDPOINTS.upload.path, signedIn, staff, await uploadMiddleware(config), handlers.upload);
  app.get(PUBLIC_ENDPOINTS.uploads.path, signedIn, staff, handlers.documents);
  app.get(PUBLIC_ENDPOINTS.uploadSchemes.path, signedIn, staff, handlers.schemes);
  app.get(PUBLIC_ENDPOINTS.adminUsers.path, signedIn, admin, handlers.accounts);
  app.post(PUBLIC_ENDPOINTS.provisionDepartment.path, signedIn, admin, handlers.provision);
  app.use('/api', () => { throw absent(); });
  app.use(handleError);
  return { app, repo };
}
