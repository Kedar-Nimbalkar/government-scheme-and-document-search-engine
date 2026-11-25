import express from 'express';
import { createAuth } from './services/auth.js';
import { createSearchService } from './services/search.js';
import { phaseOneControllers } from './controllers/phase-one.js';
import { resourceControllers } from './controllers/resources.js';
import { queryService } from './adapters/engines.js';
import { handleError, notFound } from './middleware/errors.js';
import { cors, requestContext } from './middleware/request.js';
import { createE3Repository } from './persistence/repository.js';
import { createRoutes } from './routes/index.js';

export async function createApp(config, repo = createE3Repository(config)) {
  const app = express();
  const auth = createAuth(repo, config);
  const search = createSearchService(repo, queryService(config));
  const routes = await createRoutes(config, auth,
    phaseOneControllers(auth, search, config), resourceControllers(repo, auth, config));

  app.disable('x-powered-by');
  app.use('/api', requestContext, cors(config), express.json({ limit: '32kb' }));
  app.use(routes);
  app.use('/api', notFound);
  app.use(handleError);
  return { app, repo };
}
