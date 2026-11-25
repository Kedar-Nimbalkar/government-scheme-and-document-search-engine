import { forbidden, unauthorized } from '../exceptions/api-error.js';

export const sessionCookie = 'team06_session';

export function authenticationMiddleware(auth) {
  return {
    authentication(required = true) {
      return async (request, response, next) => {
        const authorization = request.get('authorization');
        const cookie = request.get('cookie')?.split(';').map((item) => item.trim())
          .find((item) => item.startsWith(sessionCookie + '='))?.slice(sessionCookie.length + 1);
        if (!authorization && !cookie && !required) return next();
        const token = authorization ? /^Bearer ([A-Za-z0-9_.-]+)$/i.exec(authorization)?.[1] : cookie;
        const session = token ? await auth.session(token) : null;
        if (!session) {
          if (!required && !authorization) return next();
          await auth.denied(response.locals.requestId);
          throw unauthorized();
        }
        request.user = session.user;
        request.tokenId = session.tokenId;
        next();
      };
    },
    roles(...allowed) {
      return async (request, response, next) => {
        if (!request.user) throw unauthorized();
        if (allowed.includes(request.user.role)) return next();
        await auth.denied(response.locals.requestId, request.user.user_id);
        throw forbidden();
      };
    },
  };
}
