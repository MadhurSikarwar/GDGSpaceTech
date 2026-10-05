// Thin client for the OrbitWatch REST API. Every state-changing call carries
// the X-Requested-With header the server requires (CSRF protection).

const HEADERS = { 'X-Requested-With': 'OrbitWatch' };

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export async function api(path, { method = 'GET', body } = {}) {
  const opts = { method, headers: { ...HEADERS }, credentials: 'same-origin' };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch('/api' + path, opts);
  } catch (e) {
    throw new ApiError('Cannot reach the OrbitWatch server.', 0, null);
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty or non-JSON body */ }
  if (!res.ok) {
    // The server no longer sees a session (logged out elsewhere, expired, account disabled):
    // let the shell re-check who is logged in instead of showing stale role-based UI.
    if (res.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new CustomEvent('ow:unauthorized'));
    const msg = (data && (data.error || data.detail)) || res.statusText || 'Request failed';
    throw new ApiError(msg, res.status, data);
  }
  return data;
}

export const get = (path) => api(path);
export const post = (path, body = {}) => api(path, { method: 'POST', body });
export const put = (path, body = {}) => api(path, { method: 'PUT', body });
export const patch = (path, body = {}) => api(path, { method: 'PATCH', body });
export const del = (path) => api(path, { method: 'DELETE' });

export function qs(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== '' && v !== false) p.set(k, v);
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

// CSV exports are plain GET links (the session cookie authorises them).
export const csvUrl = (path, params = {}) => '/api' + path + qs({ ...params, format: 'csv' });
