/* The LTL control-plane API client.
 *
 * Unwraps the `{data, error, timestamp}` envelope every /api/v1 response
 * carries, so pages deal in values and exceptions rather than in
 * envelopes. Errors keep their code, because the UI branches on a few of
 * them (PLAN_LIMIT sends you to pricing; UNAUTHENTICATED sends you to
 * sign-in).
 */

export const API_BASE = window.LTL_API_BASE || '';

export class ApiError extends Error {
  constructor(code, message, fieldErrors) {
    super(message || code);
    this.code = code;
    this.fieldErrors = fieldErrors || [];
  }
}

async function request(method, path, body) {
  const headers = {};
  if (body !== undefined) headers['content-type'] = 'application/json';

  const token = sessionToken();
  if (token) headers.authorization = `Bearer ${token}`;

  let response;
  try {
    response = await fetch(API_BASE + path, {
      method,
      headers,
      credentials: 'include',
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError('NETWORK', 'Could not reach Lazy Thumb Labs.');
  }

  let payload = null;
  try { payload = await response.json(); } catch { /* empty body */ }

  if (payload && payload.error) {
    throw new ApiError(payload.error.code, payload.error.message, payload.error.fieldErrors);
  }
  if (!response.ok) {
    throw new ApiError('HTTP_' + response.status, `Request failed (${response.status}).`);
  }
  return payload ? payload.data : null;
}

/* The Stytch session JWT. Kept in localStorage for the bearer path;
 * browsers that got a session cookie instead are covered by
 * `credentials: 'include'` above, so both flows work without the page
 * needing to know which one it is on. */
export function sessionToken() {
  try { return localStorage.getItem('ltl_session'); } catch { return null; }
}

export function setSessionToken(token) {
  try {
    if (token) localStorage.setItem('ltl_session', token);
    else localStorage.removeItem('ltl_session');
  } catch { /* private mode */ }
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, body === undefined ? {} : body),
  del: (path) => request('DELETE', path),

  plans: () => request('GET', '/api/v1/plans'),
  account: () => request('GET', '/api/v1/account'),
  bootstrap: () => request('POST', '/api/v1/account/bootstrap', {}),

  households: () => request('GET', '/api/v1/households'),
  claim: (code, name) => request('POST', '/api/v1/households/claim', { code, name }),
  usage: (householdId) => request('GET', `/api/v1/households/${householdId}/usage`),
  unpair: (householdId) => request('DELETE', `/api/v1/households/${householdId}`),

  devices: (householdId) => request('GET', `/api/v1/devices?householdId=${householdId}`),
  registerDevice: (payload) => request('POST', '/api/v1/devices', payload),
  revokeDevice: (deviceId) => request('DELETE', `/api/v1/devices/${deviceId}`),

  subscription: () => request('GET', '/api/v1/billing/subscription'),
  checkout: (planCode) => request('POST', '/api/v1/billing/checkout', { planCode }),
  portal: () => request('POST', '/api/v1/billing/portal', {}),
};

/* ── small shared helpers ────────────────────────────────────────────── */

export function fmtBytes(n) {
  if (n === null || n === undefined) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = Number(n);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

export function fmtMoney(cents, currency = 'usd') {
  return new Intl.NumberFormat(undefined, {
    style: 'currency', currency: currency.toUpperCase(), minimumFractionDigits: 0,
  }).format(cents / 100);
}

export function relTime(iso) {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const seconds = Math.round((then - Date.now()) / 1000);
  const units = [
    ['year', 31536000], ['month', 2592000], ['day', 86400],
    ['hour', 3600], ['minute', 60], ['second', 1],
  ];
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size || unit === 'second') {
      return formatter.format(Math.round(seconds / size), unit);
    }
  }
  return '—';
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function requireSignIn() {
  if (!sessionToken()) {
    window.location.href = 'signin.html?next='
      + encodeURIComponent(window.location.pathname + window.location.search);
    return false;
  }
  return true;
}
