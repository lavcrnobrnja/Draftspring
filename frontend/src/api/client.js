/**
 * API client — session cookies, 401 redirect, error handling.
 */
import toast from 'react-hot-toast';

const BASE = '';

async function request(method, path, body = null, opts = {}) {
  const url = `${BASE}${path}`;
  const headers = {};
  if (body && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  const res = await fetch(url, {
    method,
    headers,
    credentials: 'include',
    body: body instanceof FormData ? body : body ? JSON.stringify(body) : null,
    ...opts,
  });

  if (res.status === 401) {
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
    throw new ApiError(msg, res.status, data);
  }

  if (res.status === 204) return null;
  return res.json();
}

export class ApiError extends Error {
  constructor(message, status, data = {}) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, body),
  put: (path, body) => request('PUT', path, body),
  del: (path) => request('DELETE', path),
  upload: (path, formData) => request('POST', path, formData),
};
