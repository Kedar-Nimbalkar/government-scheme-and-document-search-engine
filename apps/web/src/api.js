export async function api(path, { body, method = 'GET', signal } = {}) {
  const multipart = body instanceof FormData;
  const response = await fetch('/api' + path, {
    method, signal, credentials: 'same-origin',
    headers: body && !multipart ? { 'Content-Type': 'application/json' } : undefined,
    body: body && !multipart ? JSON.stringify(body) : body,
  });
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('session-expired'));
    const error = new Error(data?.error?.message || 'The service is unavailable');
    error.status = response.status;
    throw error;
  }
  return { data, warning: response.headers.get('X-Search-Warning') };
}

export function pdfPage(url, page) {
  return url && Number.isInteger(page) && page > 0 ? url + '#page=' + page : url;
}
