import { object, string } from './input.js';
import { invalid } from '../exceptions/api-error.js';

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
