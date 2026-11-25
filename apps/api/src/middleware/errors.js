import { errorResponse } from '../../../../packages/contracts/v2/http.js';
import { ApiError, absent } from '../exceptions/api-error.js';

export function notFound() { throw absent(); }

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
