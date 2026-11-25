import { randomUUID } from 'node:crypto';
import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import { ApiError, UUID, forbidden, invalid, object, string } from './http.js';

const issuer = 'team06-e4';
const audience = 'team06-api';
const cookieName = 'team06_session';
const cost = 12;
const unauthorized = () => new ApiError(401, 'AUTH_REQUIRED', 'Sign in with valid credentials');

export function credentials(body, extra = [], registration = false) {
  object(body, ['username', 'password', ...extra]);
  const username = string(body.username, 'Username', 80);
  const password = body.password;
  if (typeof password !== 'string' || !password.trim() || password.includes('\0')
      || Buffer.byteLength(password) > 72 || (registration && password.length < 8)) {
    throw invalid(registration ? 'Use at least 8 characters and at most 72 UTF-8 bytes for your password' : 'Invalid password format');
  }
  return { username, password };
}

export function createAuth(repo, config) {
  const dummy = bcrypt.hash(randomUUID(), cost);
  const cookies = { httpOnly: true, secure: config.secureCookie, sameSite: 'strict', path: '/api' };
  const denied = (requestId, userId = null) => repo.recordOperationalEvent('AUTH_DENIED', { requestId, userId }).catch(() => {});

  return {
    async register({ username, password }) {
      return repo.createUser({ username, passwordHash: await bcrypt.hash(password, cost) });
    },
    async provision(actorId, { username, password, deptId }) {
      return repo.provisionDepartment(actorId, { username, deptId, passwordHash: await bcrypt.hash(password, cost) });
    },
    async login({ username, password }, requestId) {
      const user = await repo.findUserByUsername(username);
      const valid = await bcrypt.compare(password, user?.password_hash || await dummy);
      if (!user || !valid) {
        await denied(requestId, user?.user_id);
        throw unauthorized();
      }
      const tokenId = randomUUID();
      const token = jwt.sign({}, config.jwtSecret, {
        algorithm: 'HS256', issuer, audience, subject: user.user_id,
        jwtid: tokenId, expiresIn: config.sessionSeconds,
      });
      await repo.startSession(tokenId, user.user_id, new Date(jwt.decode(token).exp * 1000));
      return { access_token: token, role: user.role };
    },
    setCookie(response, token) {
      response.cookie(cookieName, token, { ...cookies, maxAge: config.sessionSeconds * 1000 });
    },
    async logout(request, response) {
      await repo.endSession(request.tokenId, request.user.user_id);
      response.clearCookie(cookieName, cookies);
    },
    middleware(required = true) {
      return async (request, response, next) => {
        const header = request.get('authorization');
        const cookie = request.get('cookie')?.split(';').map((item) => item.trim())
          .find((item) => item.startsWith(cookieName + '='))?.slice(cookieName.length + 1);
        if (!header && !cookie && !required) return next();
        const token = header ? /^Bearer ([A-Za-z0-9_.-]+)$/i.exec(header)?.[1] : cookie;
        let claim;
        try {
          claim = jwt.verify(token, config.jwtSecret, { algorithms: ['HS256'], issuer, audience });
        } catch {
          if (!required && !header) return next();
          await denied(response.locals.requestId);
          throw unauthorized();
        }
        const user = typeof claim.sub === 'string' && UUID.test(claim.sub)
          && typeof claim.jti === 'string' && UUID.test(claim.jti) && Number.isInteger(claim.exp)
          ? await repo.findSession(claim.jti, claim.sub) : null;
        if (!user) {
          if (!required && !header) return next();
          await denied(response.locals.requestId);
          throw unauthorized();
        }
        request.user = user;
        request.tokenId = claim.jti;
        return next();
      };
    },
    roles(...roles) {
      return async (request, response, next) => {
        if (roles.includes(request.user.role)) return next();
        await denied(response.locals.requestId, request.user.user_id);
        throw forbidden();
      };
    },
  };
}
