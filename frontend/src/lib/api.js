/**
 * API client — session cookies, 401 redirect, error handling.
 */
import toast from 'react-hot-toast';
import { captureError } from './posthog';

const BASE = '';

async function request(method, path, body = null, opts = {}) {
  const url = `${BASE}${path}`;
  const headers = {};
  if (body && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  const { silent, errorToastOptions, ...fetchOpts } = opts;

  const res = await fetch(url, {
    method,
    headers,
    credentials: 'include',
    body: body instanceof FormData ? body : body ? JSON.stringify(body) : null,
    ...fetchOpts,
  });

  if (res.status === 401) {
    // Clear and redirect to login
    window.location.href = '/login';
    throw new Error('Session expired');
  }

  if (res.status === 429) {
    toast.error('Too many requests — slow down');
    throw new Error('Rate limited');
  }

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const msg = data.detail || `Request failed (${res.status})`;
    if (!silent) toast.error(msg, errorToastOptions);
    const err = new Error(msg);
    err.status = res.status;
    err.data = data;
    captureError(err, { endpoint: path, status: res.status });
    throw err;
  }

  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  get: (path, opts) => request('GET', path, null, opts),
  post: (path, body, opts) => request('POST', path, body, opts),
  put: (path, body, opts) => request('PUT', path, body, opts),
  del: (path, opts) => request('DELETE', path, null, opts),
  upload: (path, formData) => request('POST', path, formData, { silent: true }),
};
