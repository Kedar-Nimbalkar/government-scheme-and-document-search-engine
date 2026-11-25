export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}
export const invalid = (message) => new ApiError(400, 'INVALID_REQUEST', message);
export const unauthorized = () => new ApiError(401, 'AUTH_REQUIRED', 'Sign in with valid credentials');
export const absent = () => new ApiError(404, 'NOT_FOUND', 'Resource not found');
export const forbidden = () => new ApiError(403, 'FORBIDDEN', 'This action is outside your access scope');
export const unavailable = (message) => new ApiError(503, 'SERVICE_UNAVAILABLE', message);
