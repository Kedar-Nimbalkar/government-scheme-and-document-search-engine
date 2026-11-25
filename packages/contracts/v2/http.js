import endpoints from './endpoints.json' with { type: 'json' };

export const CONTRACT_VERSION = endpoints.schema_version;
export const PUBLIC_ENDPOINTS = endpoints.public;
export const INTERNAL_ENDPOINTS = endpoints.internal;

export function errorResponse(code, message, requestId) {
  return { error: { code, message, request_id: requestId } };
}
