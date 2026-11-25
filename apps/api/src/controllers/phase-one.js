import { CONTRACT_VERSION } from '../../../../packages/contracts/v2/http.js';
import { sessionCookie } from '../middleware/auth.js';

export function phaseOneControllers(auth, search, config) {
  const cookieOptions = { httpOnly: true, secure: config.secureCookie, sameSite: 'strict', path: '/api' };
  return {
    register: async (_request, response) => {
      const user = await auth.register(response.locals.input);
      response.status(201).json({ user_id: user.user_id });
    },
    login: async (_request, response) => {
      const result = await auth.login(response.locals.input, response.locals.requestId);
      response.cookie(sessionCookie, result.access_token, { ...cookieOptions, maxAge: config.sessionSeconds * 1000 });
      response.json(result);
    },
    me: (request, response) => response.json(request.user),
    logout: async (request, response) => {
      await auth.logout(request.tokenId, request.user.user_id);
      response.clearCookie(sessionCookie, cookieOptions);
      response.json({ status: 'logged_out' });
    },
    search: async (_request, response) => {
      const { data, warning } = await search.search(response.locals.input);
      if (warning === 'BOOLEAN_FALLBACK') response.set('X-Search-Warning', warning);
      response.json(data);
    },
    autocomplete: async (_request, response) => response.json(await search.autocomplete(response.locals.input)),
    filters: async (_request, response) => response.json(await search.filters()),
    health: async (_request, response) => {
      await search.health();
      response.json({ status: 'ok', service: 'e4-api', e2: 'ok', contract_version: CONTRACT_VERSION });
    },
  };
}
