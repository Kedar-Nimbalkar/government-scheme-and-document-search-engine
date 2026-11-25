import { invalid } from '../exceptions/api-error.js';

export const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;

export function id(value) {
  if (typeof value !== 'string' || !UUID.test(value)) throw invalid('Use a valid UUID');
  return value;
}

export function object(value, allowed) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).some((key) => !allowed.includes(key))) {
    throw invalid('Request contains unsupported fields');
  }
  return value;
}

export function string(value, field, max) {
  if (typeof value !== 'string' || !value.trim() || [...value].length > max || value.includes('\0')) {
    throw invalid(`${field} is required and must be at most ${max} characters`);
  }
  return value.trim();
}

export function integer(value, max) {
  if (typeof value === 'string' && /^[1-9][0-9]*$/.test(value)) value = Number(value);
  if (!Number.isInteger(value) || value < 1 || value > max) throw invalid('Invalid number');
  return value;
}
