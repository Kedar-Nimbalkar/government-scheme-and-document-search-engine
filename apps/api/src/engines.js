import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { CONTRACT_VERSION, INTERNAL_ENDPOINTS } from '../../../packages/contracts/v2/http.js';
import { workspace } from './config.js';
import { ApiError, invalid, unavailable } from './http.js';

const execute = promisify(execFile);

export function queryService(config) {
  async function call(endpoint, payload = null, query = null) {
    const url = new URL(endpoint.path, config.e2);
    if (query) url.search = new URLSearchParams(query);
    try {
      const response = await fetch(url, {
        method: endpoint.method, headers: payload ? { 'Content-Type': 'application/json' } : {},
        body: payload ? JSON.stringify(payload) : undefined,
        signal: AbortSignal.timeout(config.engineTimeout), redirect: 'error',
      });
      if (response.status === 504) throw new ApiError(504, 'SEARCH_TIMEOUT', 'Query service timed out');
      if (response.status === 400) {
        throw invalid((await response.json()).error?.message || 'Invalid search input');
      }
      if (!response.ok) throw unavailable('Query service unavailable');
      return { data: await response.json(), warning: response.headers.get('X-Search-Warning') };
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (error.name === 'TimeoutError' || error.name === 'AbortError') {
        throw new ApiError(504, 'SEARCH_TIMEOUT', 'Query service timed out');
      }
      throw unavailable('Query service unavailable');
    }
  }
  return {
    async health() {
      const { data } = await call(INTERNAL_ENDPOINTS.health);
      if (data.status !== 'ok' || data.service !== 'e2' || data.contract_version !== CONTRACT_VERSION) {
        throw unavailable('Query service contract mismatch');
      }
    },
    search: (payload) => call(INTERNAL_ENDPOINTS.search, payload),
    autocomplete: (prefix) => call(INTERNAL_ENDPOINTS.autocomplete, null, { prefix }),
  };
}

const pdfErrors = {
  PDF_ENCRYPTED: 'Encrypted PDFs are not supported',
  PDF_NO_TEXT: 'The PDF is blank or image-only and contains no searchable text',
  PDF_CORRUPT: 'The PDF is corrupt or unreadable',
  PDF_UNREADABLE: 'The PDF could not be read',
  PDF_LIMIT: 'The PDF exceeds the extraction limits',
};

export async function validatePdf(config, path) {
  let result;
  try {
    const { stdout } = await execute(config.python, ['-m', 'apps.api.pdf_check', path],
      { cwd: workspace, windowsHide: true, timeout: 15000, maxBuffer: 4096 });
    result = JSON.parse(stdout);
  } catch {
    throw unavailable('PDF validation service unavailable');
  }
  if (result.valid === true) return;
  if (Object.hasOwn(pdfErrors, result.reason)) throw new ApiError(400, result.reason, pdfErrors[result.reason]);
  throw unavailable('PDF validation service unavailable');
}
