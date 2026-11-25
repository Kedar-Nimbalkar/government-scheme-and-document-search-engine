import { randomUUID } from 'node:crypto';
import { errorResponse } from '../../../packages/contracts/v2/http.js';

export const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}
export const invalid = (message) => new ApiError(400, 'INVALID_REQUEST', message);
export const absent = () => new ApiError(404, 'NOT_FOUND', 'Resource not found');
export const forbidden = () => new ApiError(403, 'FORBIDDEN', 'This action is outside your access scope');
export const unavailable = (message) => new ApiError(503, 'SERVICE_UNAVAILABLE', message);

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

export function requestId(_request, response, next) {
  response.locals.requestId = randomUUID();
  response.set({ 'X-Request-ID': response.locals.requestId, 'X-Content-Type-Options': 'nosniff' });
  next();
}

const known = {
  DUPLICATE: [409, 'This resource already exists'],
  FORBIDDEN: [403, 'This action is outside your access scope'],
  INVALID_USER: [400, 'Invalid account information'],
  INVALID_DEPARTMENT: [400, 'Unknown department'],
  INVALID_STAGED_PDF: [400, 'Invalid staged PDF'],
  PERSISTENCE_UNAVAILABLE: [503, 'Database service unavailable'],
  23514: [400, 'Invalid data for this resource'],
  23503: [400, 'Unknown referenced resource'],
  23505: [409, 'This resource already exists'],
};

export function handleError(error, _request, response, next) {
  if (response.headersSent) return next(error);
  let status, code, message;
  if (error instanceof ApiError) ({ status, code, message } = error);
  else if (error.code === 'LIMIT_FILE_SIZE' || error.type === 'entity.too.large') {
    [status, code, message] = [413, 'FILE_TOO_LARGE', 'Request exceeds the size limit'];
  } else if (error.name === 'MulterError' || error.type === 'entity.parse.failed') {
    [status, code, message] = [400, 'INVALID_REQUEST', 'Invalid request body or upload fields'];
  } else if (Object.hasOwn(known, error.code)) {
    [status, message] = known[error.code];
    code = status === 400 ? 'INVALID_REQUEST' : status === 503 ? 'SERVICE_UNAVAILABLE' : error.code === '23505' ? 'DUPLICATE' : error.code;
  } else if (['ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT', '57P01', '28P01', '08006', '08001'].includes(error.code)) {
    [status, code, message] = [503, 'SERVICE_UNAVAILABLE', 'Database service unavailable'];
  } else if (error.status === 404) {
    [status, code, message] = [404, 'NOT_FOUND', 'Resource not found'];
  } else {
    [status, code, message] = [500, 'INTERNAL_ERROR', 'Unexpected service error'];
  }
  if (status >= 500) console.error(JSON.stringify({ event: code, request_id: response.locals.requestId, status }));
  return response.status(status).json(errorResponse(code, message, response.locals.requestId));
}
