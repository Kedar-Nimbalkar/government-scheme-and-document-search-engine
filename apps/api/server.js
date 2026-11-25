import { createApp } from './src/app.js';
import { loadConfig } from './src/config/index.js';

const config = loadConfig();
const { app, repo } = await createApp(config);
const server = app.listen(config.port, '127.0.0.1', () => console.log(`API listening on 127.0.0.1:${config.port}`));
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, () => server.close(() => repo.pool.end()));
}
