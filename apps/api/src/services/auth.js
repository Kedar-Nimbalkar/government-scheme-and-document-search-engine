import { randomUUID } from 'node:crypto';
import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import { unauthorized } from '../exceptions/api-error.js';
import { UUID } from '../validators/input.js';

const issuer = 'team06-e4';
const audience = 'team06-api';
const cost = 12;
export function createAuth(repo, config) {
  const dummy = bcrypt.hash(randomUUID(), cost);
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
    denied,
    logout: (tokenId, userId) => repo.endSession(tokenId, userId),
    async session(token) {
      let claim;
      try {
        claim = jwt.verify(token, config.jwtSecret, { algorithms: ['HS256'], issuer, audience });
      } catch {
        return null;
      }
      if (typeof claim?.sub !== 'string' || !UUID.test(claim.sub)
          || typeof claim.jti !== 'string' || !UUID.test(claim.jti) || !Number.isInteger(claim.exp)) return null;
      const user = await repo.findSession(claim.jti, claim.sub);
      return user ? { user, tokenId: claim.jti } : null;
    },
  };
}
