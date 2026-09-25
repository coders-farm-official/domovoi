/* Tiny data layer — API fetch helpers + a single shared WebSocket
 * subscription to /ws/state, exposed as React hooks that page
 * components consume. Loaded via Babel-in-browser like the rest of
 * the bundle (so it can use JSX / hooks freely).
 *
 * Design constraints:
 *   * One WebSocket per page-load, multiplexed across hooks. The
 *     server already supports per-channel filtering; we subscribe
 *     to every channel we know about and let each hook filter what
 *     it cares about.
 *   * Hooks are fire-and-forget — `useApiList('/api/music/library')`
 *     returns `{ items, loading, error, refresh }`, and the page
 *     just renders against `items`.
 *   * Server pushes don't require a refetch. The poll loop on the
 *     backend already publishes the new state in the event payload
 *     (`data` field). Hooks merge those pushes in place.
 *   * Errors degrade silently in the UI — the page renders empty
 *     states / dashes rather than red banners. Console gets the
 *     details for debugging.
 */

// ─── Server selection ───────────────────────────────────────────────
// Multi-domovoi homes: the dashboard can point at a different
// backend than the one that served it. '' = same-origin (default).
// Selection + the saved server list persist in localStorage; switching
// reloads the page so every hook and the WebSocket re-init cleanly.
//
// A server is only ever SELECTED once the user has trusted it (FE-2):
// the switcher shows the discovered address and asks first, because
// selecting a server means running its plugin JS in this origin and
// sending it the admin password at login. Same-origin — the box that
// served the page — is trusted by construction; every other address
// sits in the trusted list only after that confirmation.

const SERVER_KEY = 'domovoi-server';
const SERVERS_KEY = 'domovoi-servers'; // [{url, name}]
const TRUSTED_KEY = 'domovoi-trusted-servers'; // [url, ...]

const API_BASE = (() => {
  try { return localStorage.getItem(SERVER_KEY) || ''; } catch { return ''; }
})();
const WS_PATH = '/ws/state';
// Must match web/backend/main.py WS_DEVICE_TOKEN_SUBPROTOCOL[_B64].
//
// The household token rides the handshake base64url-encoded. RFC 6455
// requires every Sec-WebSocket-Protocol element to be an RFC 9110 token —
// no space, none of " ( ) , / : ; < = > ? @ [ \ ] { } — and `new
// WebSocket(...)` THROWS on anything else before a byte leaves the tab.
// base64url's alphabet is a legal token for any value, which is what lets
// the household token itself be any printable ASCII.
const WS_DEVICE_TOKEN_SUBPROTOCOL = 'domovoi.device-token.';      // legacy, still read
const WS_DEVICE_TOKEN_SUBPROTOCOL_B64 = 'domovoi.device-token-b64.';

// RFC 9110 tchar: a token that matches this may ALSO be offered raw.
const WS_TCHAR_ONLY = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;

const WS_B64URL_ALPHABET =
  'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';

// Hand-rolled rather than btoa(): btoa is byte-oriented (it throws on any
// code point above 0xFF) and neither it nor TextEncoder exists in the Node
// vm the pairing tests run auth.js/data.js inside.
const wsUtf8Bytes = (s) => {
  const out = [];
  for (let i = 0; i < s.length; i++) {
    const c = s.codePointAt(i);
    if (c > 0xffff) i++;                       // surrogate pair, consumed
    if (c < 0x80) out.push(c);
    else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
    else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63),
                  0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
  }
  return out;
};

// base64url with the '=' padding stripped; the server puts it back.
const wsBase64Url = (s) => {
  const b = wsUtf8Bytes(s);
  let out = '';
  for (let i = 0; i < b.length; i += 3) {
    const n = (b[i] << 16) | ((b[i + 1] || 0) << 8) | (b[i + 2] || 0);
    const left = b.length - i;
    out += WS_B64URL_ALPHABET[(n >> 18) & 63] + WS_B64URL_ALPHABET[(n >> 12) & 63];
    if (left > 1) out += WS_B64URL_ALPHABET[(n >> 6) & 63];
    if (left > 2) out += WS_B64URL_ALPHABET[n & 63];
  }
  return out;
};

// What this browser offers on the handshake, b64 FIRST. The legacy raw
// element is added only when the token is itself a legal RFC 9110 token —
// the constructor validates EVERY element, so offering an illegal one
// alongside a legal one throws the whole call away. It is worth adding
// when it is legal: a server that has not been restarted yet does not know
// the b64 prefix, and a browser whose offers are all unrecognised gets no
// subprotocol echoed back and drops the socket.
const wsDeviceSubprotocols = (token) => {
  if (!token) return null;
  const offers = [`${WS_DEVICE_TOKEN_SUBPROTOCOL_B64}${wsBase64Url(token)}`];
  if (WS_TCHAR_ONLY.test(token)) offers.push(`${WS_DEVICE_TOKEN_SUBPROTOCOL}${token}`);
  return offers;
};

const ServerStore = {
  current: () => API_BASE, // '' = same-origin
  currentLabel() {
    if (!API_BASE) return window.location.host;
    try { return new URL(API_BASE).host; } catch { return API_BASE; }
  },
  // The host:port a person can check against the box — what the trust
  // prompt shows, prominently, before anything is persisted.
  hostOf(url) {
    if (!url) return window.location.host;
    try { return new URL(url).host; } catch { return String(url).replace(/^https?:\/\//, ''); }
  },
  list() {
    try { return JSON.parse(localStorage.getItem(SERVERS_KEY) || '[]'); } catch { return []; }
  },
  _save(list) {
    try { localStorage.setItem(SERVERS_KEY, JSON.stringify(list)); } catch {}
  },
  upsert(url, name) {
    const clean = url.replace(/\/+$/, '');
    const list = this.list().filter((s) => s.url !== clean);
    list.push({ url: clean, name: name || null });
    this._save(list);
  },
  remove(url) {
    this._save(this.list().filter((s) => s.url !== url));
    this.untrust(url);
  },
  // ── Trust ──────────────────────────────────────────────────────────
  // Servers the user has confirmed in the switcher. Dashboards from
  // before the trust list existed carry a saved list that was only ever
  // filled by explicit "use" clicks, so the first load seeds the trusted
  // list from it (plus the current selection) rather than re-asking.
  trustedList() {
    let raw = null;
    try { raw = localStorage.getItem(TRUSTED_KEY); } catch {}
    if (raw === null) {
      const seed = this.list().map((s) => s.url);
      if (API_BASE && !seed.includes(API_BASE)) seed.push(API_BASE);
      try { localStorage.setItem(TRUSTED_KEY, JSON.stringify(seed)); } catch {}
      return seed;
    }
    try { return JSON.parse(raw) || []; } catch { return []; }
  },
  isTrusted(url) {
    const clean = (url || '').replace(/\/+$/, '');
    if (!clean) return true; // same-origin: the server that served this page
    return this.trustedList().includes(clean);
  },
  trust(url) {
    const clean = (url || '').replace(/\/+$/, '');
    if (!clean) return;
    const list = this.trustedList().filter((u) => u !== clean);
    list.push(clean);
    try { localStorage.setItem(TRUSTED_KEY, JSON.stringify(list)); } catch {}
  },
  untrust(url) {
    const clean = (url || '').replace(/\/+$/, '');
    const list = this.trustedList().filter((u) => u !== clean);
    try { localStorage.setItem(TRUSTED_KEY, JSON.stringify(list)); } catch {}
  },
  // Point the dashboard at `url` and reload. Refused — nothing written,
  // no reload, returns false — for a server that has not been trusted;
  // the switcher asks first and calls trust() on confirmation.
  select(url) {
    const clean = (url || '').replace(/\/+$/, '');
    if (clean && !this.isTrusted(clean)) return false;
    try {
      if (clean) localStorage.setItem(SERVER_KEY, clean);
      else localStorage.removeItem(SERVER_KEY);
    } catch {}
    window.location.reload();
    return true;
  },
  // Probe one base URL for a live web backend; resolves {url, name} or null.
  async probe(base, timeoutMs = 1200) {
    const clean = base.replace(/\/+$/, '');
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const r = await fetch(`${clean}/api/health`, { signal: ctrl.signal });
      if (!r.ok) return null;
      let name = null;
      try {
        const cfg = await fetch(`${clean}/api/config`).then((x) => x.json());
        name = cfg.bot_name || null;
      } catch {}
      return { url: clean, name };
    } catch {
      return null;
    } finally {
      clearTimeout(timer);
    }
  },
  // Scan the /24 around the current host for backends on :6369. Only
  // possible when the page was loaded by IPv4 address (the browser
  // can't learn its own LAN address); callers fall back to manual add.
  scanPrefix() {
    let host = window.location.hostname;
    if (API_BASE) { try { host = new URL(API_BASE).hostname; } catch {} }
    const m = host.match(/^(\d+\.\d+\.\d+)\.\d+$/);
    return m ? m[1] : null;
  },
  async scan(onProgress) {
    const prefix = this.scanPrefix();
    if (!prefix) return null; // caller shows the "load by IP to scan" hint
    const found = [];
    let done = 0;
    const hosts = Array.from({ length: 254 }, (_, i) => i + 1);
    const CONC = 24;
    const worker = async () => {
      while (hosts.length) {
        const n = hosts.shift();
        const hit = await this.probe(`http://${prefix}.${n}:6369`, 900);
        if (hit) found.push(hit);
        done += 1;
        if (onProgress) onProgress(done, 254, found.length);
      }
    };
    await Promise.all(Array.from({ length: CONC }, worker));
    return found;
  },
};

// Admin auth (auth.js, loaded first): the in-memory bearer token AND the
// stored household device token ride along on every API call
// (Auth.headers() carries both); a 401/403 from an admin-gated endpoint
// pops the login modal so the user can authenticate, and one that names
// the device token pops the pair modal instead.
const _authHeaders = () => {
  try { return (typeof Auth !== 'undefined' && Auth.headers()) || {}; }
  catch { return {}; }
};

// A refusal on the DEVICE tier: the server's detail names the header it
// wanted ("X-Device-Token or admin session required", or the cookie-only
// 403). Anything else that is 401/403 is the admin tier's business.
const _isDeviceTokenRefusal = (status, text) => (
  (status === 401 || status === 403) && /x-device-token|device token/i.test(String(text || ''))
);

/* ── A device an admin BLOCKED, told apart from a missing credential ──
 *
 * An admin can block one device in Settings → Devices; the server then
 * refuses that device's writes 403, with the block's own reason as the
 * body. 403 is also what "sign in" looks like, so the retry below used
 * to open the admin-password modal over the editor and replay the save.
 * That is useless — the block is on the DEVICE and no password lifts it
 * — and it is misleading, because being asked implies it would. The
 * person whose tablet was deliberately blocked ends up typing the
 * household's admin password to be refused a second time.
 *
 * It must not be fixed by matching words in the refusal: the sentence
 * is copy, and copy moves. Ask the SERVER instead. /api/files/browse
 * answers `writable` and `blocked_reason` for a named device on the
 * READ tier (reading is never blocked), and both that field and the
 * write refusal are rendered by the same function on the server — so a
 * refusal whose body IS this device's blocked_reason is the block,
 * however anyone rewords it later.
 *
 * Deliberately narrow. Anything else on 401/403 keeps the prompt it
 * needs, including a DELETE refused for want of an admin bearer on a
 * device that also happens to be blocked: delete is admin-gated and
 * does not consult the block, so its refusal is a different sentence
 * and this comparison says no. */
const BLOCK_PROBE_LIBRARY = 'core:documents';

// Raw fetch on purpose: apiFetch would come back through the retry
// below and could open the very modal this exists to avoid.
const _blockProbeReason = async () => {
  try {
    const id = DeviceIdentity.id();
    if (!id) return null;
    const r = await fetch(
      `${API_BASE}/api/files/browse?library_id=${encodeURIComponent(BLOCK_PROBE_LIBRARY)}`
      + `&path=&device_id=${encodeURIComponent(id)}`,
      { credentials: 'include', headers: apiHeaders() },
    );
    if (!r.ok) return null;
    const j = await r.json();
    if (!j || j.writable !== false) return null;
    return (typeof j.blocked_reason === 'string' && j.blocked_reason) ? j.blocked_reason : null;
  } catch { return null; }
};

// The refused body as the server's own sentence (FastAPI wraps it in
// {"detail": …}); null for a body that is not one.
const _refusalDetail = (text) => {
  try {
    const j = JSON.parse(String(text || ''));
    return typeof j.detail === 'string' ? j.detail : null;
  } catch { return null; }
};

const _deviceBlockRefusal = async (text) => {
  const said = _refusalDetail(text);
  if (!said) return null;
  const reason = await _blockProbeReason();
  return (reason && said.indexOf(reason) === 0) ? reason : null;
};

// Before first-run setup the security tier (config write, service
// restart, satellite code push, pairing changes) answers 501 rather than
// letting the request through — the setup modal is the right prompt for
// that, exactly as it is for a 401 once setup is done. Only while the
// status probe says setup is incomplete: a 501 from a set-up server is a
// real "not implemented" and stays an error.
const _setupPending = () => {
  try {
    return typeof Auth !== 'undefined' && !!Auth.status
      && Auth.status.setup_complete === false;
  } catch { return false; }
};
const _maybeRequestLogin = (status) => {
  if (status !== 401 && status !== 403 && !(status === 501 && _setupPending())) return;
  try { if (typeof Auth !== 'undefined') Auth.requestLogin(); } catch {}
};

// The preflight-forcing header (WEB-6). A multipart or body-less POST is a
// CORS "simple request" — a page on any other origin can submit one and the
// side effect lands even though it can't read the answer. A header the
// fetch spec doesn't allow on a simple request makes the browser preflight
// the call instead, so the server gets to refuse it first. Every API call
// from this dashboard carries it; the app sends the same one.
const REQUESTED_WITH = { 'X-Requested-With': 'XMLHttpRequest' };

// Every header an /api call carries. Exported because a handful of callers
// need a RAW fetch (streamed downloads with progress, uploads that report
// bytes) and must send the same set these helpers do.
const apiHeaders = () => ({ ..._authHeaders(), ...REQUESTED_WITH });

// Media the BROWSER fetches by URL — <img src>, <video src>, window.open —
// can't carry a header, so the household device token rides in the query
// for those reads (the server honours it there for reads only). Returns the
// url unchanged when this browser holds no token yet.
// Auth owns the storage and keys the token PER SERVER (`<key>@<base>`,
// bare key for same-origin), so read through it rather than guessing the
// key here: otherwise a dashboard pointed at another Domovoi would put no
// token on these URLs at all. The bare key stays as the pre-Auth fallback.
const DEVICE_TOKEN_KEY = 'domovoi-device-token';
const deviceToken = () => {
  try {
    if (typeof Auth !== 'undefined' && Auth.deviceToken) return Auth.deviceToken() || null;
  } catch { /* Auth not loaded yet - fall through */ }
  try { return localStorage.getItem(DEVICE_TOKEN_KEY) || null; } catch { return null; }
};
const withDeviceToken = (url) => {
  const token = deviceToken();
  if (!token) return url;
  return `${url}${url.includes('?') ? '&' : '?'}device_token=${encodeURIComponent(token)}`;
};

// ─── Resuming an action across a sign-in ────────────────────────────
//
// A 401/403 on a mutation used to end the story: the login modal popped
// and the caller's promise rejected, so the operator signed in and then
// had to go find the button and press it again. The request is replayed
// here instead — once, as soon as a new bearer exists — so the thing the
// user asked for is the thing that happens. Doing it in this layer is
// what makes that true of every button on every page, rather than the
// three that had hand-rolled it.
//
// Mutations only. A GET that 401s is a panel that renders empty, and the
// hooks below already refetch after login; parking every admin-gated GET
// of a page load behind the modal would turn a dismissible prompt into a
// dashboard that looks hung.
//
// At MOST one replay, and only carrying a token the refused attempt did
// not have. A 401 that persists against a fresh bearer is a real bug — a
// web→core hop that forgets to forward credentials, say — and prompting
// for it forever is a login-modal loop standing where a visible error
// belongs.
const _isAuthStatus = (status) => (
  status === 401 || status === 403 || (status === 501 && _setupPending())
);

const _isMutation = (method) => {
  const m = (method || 'GET').toUpperCase();
  return m !== 'GET' && m !== 'HEAD';
};

// Bodies that survive being sent twice: fetch re-reads a string or a
// FormData on each call, where a stream is consumed by the first.
const _replayableBody = (body) => (
  body === undefined || body === null || typeof body === 'string'
  || (typeof FormData !== 'undefined' && body instanceof FormData)
);

const _authToken = () => {
  try { return typeof Auth !== 'undefined' ? Auth.token : null; }
  catch { return null; }
};

const _signInAgain = async (refusedToken) => {
  try {
    if (typeof Auth === 'undefined') return false;
    return await Auth.ensureLoggedIn(refusedToken);
  } catch { return false; }
};

const _deviceToken = () => {
  try { return typeof Auth !== 'undefined' && Auth.deviceToken ? Auth.deviceToken() : null; }
  catch { return null; }
};

const _pairAgain = async (refusedDeviceToken) => {
  try {
    if (typeof Auth === 'undefined' || !Auth.ensurePaired) return false;
    return await Auth.ensurePaired(refusedDeviceToken);
  } catch { return false; }
};

const _maybeRequestPairing = () => {
  try { if (typeof Auth !== 'undefined' && Auth.requestPairing) Auth.requestPairing(); } catch {}
};

// Shared tail for apiFetch/apiUpload. `send` MUST rebuild its headers on
// each call — that is how the replay carries the bearer (or the device
// token) the first attempt was missing.
//
// Two prompts, chosen by what the refusal asked for: a body naming the
// device token opens the "pair this browser" modal and replays once the
// household token is stored; any other 401/403 opens the admin login
// and replays once a fresh bearer exists. The replay is the only reason
// a refused response body is read before the error is built.
//
// `raw` hands the Response back instead of the parsed JSON, for the
// callers that must read the body themselves (a streamed download with a
// progress bar, an SSE reply that fills in live). They used to call
// fetch() directly and got none of this: no prompt, no replay, and a
// thrown "403 Forbidden" with the reason discarded.
const _sendWithAuthRetry = async (send, { method, body, raw } = {}) => {
  const refusedToken = _authToken();
  const refusedDeviceToken = _deviceToken();
  let r = await send();
  let promptedHere = false;
  let signInDismissed = false;
  let text = null;
  let deviceRefusal = false;
  let deviceBlock = null;

  if (_isAuthStatus(r.status)) {
    text = await r.text().catch(() => '');
    deviceRefusal = _isDeviceTokenRefusal(r.status, text);
    // Only a mutation can be the block — reads are never blocked — and
    // probing on every refused GET would cost a request per empty panel.
    if (!deviceRefusal && _isMutation(method)) deviceBlock = await _deviceBlockRefusal(text);
  }

  if (_isAuthStatus(r.status) && !deviceBlock && _isMutation(method) && _replayableBody(body)) {
    promptedHere = true;
    const again = deviceRefusal ? _pairAgain(refusedDeviceToken) : _signInAgain(refusedToken);
    if (await again) { r = await send(); text = null; }
    else signInDismissed = true;
  }

  if (!r.ok) {
    if (text === null) text = await r.text().catch(() => '');
    // Never re-open a modal we have just come back from — that is the
    // loop. And never open one at all for a device block: no credential
    // this dashboard can collect will lift it.
    if (!promptedHere && !deviceBlock) {
      if (_isDeviceTokenRefusal(r.status, text)) _maybeRequestPairing();
      else _maybeRequestLogin(r.status);
    }
    const err = new Error(`${r.status} ${r.statusText}: ${text.slice(0, 200)}`);
    err.status = r.status;   // callers branch on auth failures
    // Lets a caller say "cancelled" instead of "failed": the operator
    // dismissed the sign-in (or the pairing), they did not hit a broken
    // endpoint.
    if (signInDismissed) err.authCancelled = true;
    // A refusal no password can lift. The caller renders it where the
    // button was pressed; nothing here offered a sign-in for it, so it
    // is NOT a loginPrompted failure and must not be swallowed as one.
    if (deviceBlock) { err.deviceBlocked = true; err.blockedReason = deviceBlock; }
    // The login (or pair) modal was shown for THIS refusal (and, on a
    // mutation, dismissed) — see isAuthFailure. Deliberately false for a
    // 401 that came back against the fresh credential: no modal was
    // re-opened for it, so the caller's error toast is the only thing
    // the operator will see.
    err.loginPrompted = signInDismissed
      || (!promptedHere && !deviceBlock && _isAuthStatus(r.status));
    err.deviceTokenRequired = _isDeviceTokenRefusal(r.status, text);
    try { err.detail = JSON.parse(text); } catch { /* non-JSON body */ }
    throw err;
  }
  if (raw) return r;
  if (r.status === 204) return null;
  return r.json();
};

const apiFetch = (path, opts = {}) => {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`;
  const send = () => fetch(url, {
    credentials: 'include',
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...apiHeaders(),
      ...(opts.headers || {}),
    },
  });
  return _sendWithAuthRetry(send, { method: opts.method, body: opts.body });
};

/* apiFetch's raw-Response twin, for the calls that read the body
 * themselves: a zip streamed so a progress bar can move, an SSE reply
 * rendered token by token. Same headers, same 401/403 prompt, same
 * replay, same error object — it just hands back the Response rather
 * than parsed JSON, and leaves Content-Type to the caller (a multipart
 * body must not have one).
 *
 * The rule this exists to make keepable: EVERY mutation in this
 * dashboard goes through apiFetch, apiUpload or apiFetchRaw. A raw
 * fetch() sends the right headers but skips the retry, so a refused save
 * on a browser whose in-memory bearer is gone dies with no prompt and no
 * replay, taking the operator's typing with it. */
const apiFetchRaw = (path, opts = {}) => {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`;
  const send = () => fetch(url, {
    credentials: 'include',
    ...opts,
    headers: { ...apiHeaders(), ...(opts.headers || {}) },
  });
  return _sendWithAuthRetry(send, { method: opts.method, body: opts.body, raw: true });
};

/* The human-readable half of a rejected apiFetch.
 *
 * `err.detail` is the PARSED RESPONSE BODY, not a string — FastAPI's is
 * `{detail: "..."}`, so the useful text is one level down. Reaching for
 * `e.detail` directly renders "[object Object]" in a toast, which is how this
 * helper came to exist. Falls back to the message (which already carries
 * "<status> <statusText>: <body>") and finally to String(e). */
/* Fit a sentence into somewhere that has a size, WITHOUT stopping
 * mid-word.
 *
 * A refusal is advice, and the end of the advice is usually the part
 * that says how to recover: the core's approval throttle finishes by
 * naming the three places the six digits can still be read. A bare
 * `slice(n)` cut that to "…it is the code column of th" in the
 * operator's face — the recovery route lost, and a sentence stopping
 * mid-word reads as a broken dashboard rather than as advice. So back
 * up to the last word boundary and say plainly that it was cut.
 *
 * `max` falsy (0, null, Infinity) means DO NOT CLIP. That is the right
 * answer wherever the text lands somewhere it can wrap — a card, a
 * field error — and it is why this is a parameter rather than a
 * constant: a toast has a size, a card does not. */
const clipSentence = (s, max) => {
  const text = String(s);
  if (!max || !Number.isFinite(max) || text.length <= max) return text;
  const cut = text.slice(0, max - 1);
  const space = cut.lastIndexOf(' ');
  const kept = space > Math.floor(max / 2) ? cut.slice(0, space) : cut;
  return kept.replace(/[\s.,;:—-]+$/, '') + '…';
};

const apiErrorText = (e, max = 160) => {
  const nested = e && e.detail && e.detail.detail;
  const text = (typeof nested === 'string' && nested)
    || (nested && JSON.stringify(nested))
    || (e && e.message)
    || String(e);
  return clipSentence(text, max);
};

/* The reason an admin's per-device block refused this write, or null.
 * A caller that holds the operator's unsent work renders it beside the
 * button rather than in a toast — the block is not transient and the
 * sentence names the person's own device. */
const deviceBlockReason = (e) => ((e && e.deviceBlocked && e.blockedReason) || null);

/* True when the login modal already owns a rejected apiFetch: the request
 * was refused for want of a sign-in and the modal was shown for it. A
 * mutation's catch should stay quiet then — `delete failed: 401 Unauthorized:
 * {"detail":"admin session required"}` sitting behind the password prompt
 * reads as a crash, not as "please sign in" (F-006), and if the operator
 * dismissed the prompt they already know the action did not happen.
 *
 * NOT true for a 401 that survived a fresh sign-in (the replay above was
 * refused too): that is a real bug — a web→core hop dropping credentials,
 * say — no modal was re-opened for it, and the visible error belongs. */
const isAuthFailure = (e) => !!(e && e.loginPrompted);

/* What an editor should say when a save (or any other mutation that holds
 * the operator's unsent work) was refused. Returns null for "say nothing".
 *
 * Three outcomes, and only the third is a failure:
 *   * the sign-in (or pairing) prompt was shown and DISMISSED —
 *     `authCancelled`. Nothing broke and nothing was lost: the request was
 *     never authorised, so say cancelled, and say the work is still here.
 *     Reporting "Save failed: 403 …" for this is how the editor came to
 *     look broken when the operator simply changed their mind.
 *   * the prompt owns the story and is still on screen (`isAuthFailure`) —
 *     stay quiet, the modal IS the message (F-006).
 *   * anything else — a real error, with the server's own detail rather
 *     than a bare status line. */
const mutationErrorText = (e, verb = 'Save', { kept = true } = {}) => {
  // An admin blocked this DEVICE. Say that, and say who can lift it —
  // never "sign in", which is what a bare 403 used to become.
  const blocked = deviceBlockReason(e);
  if (blocked) {
    return `${verb} refused — ${blocked}. An admin lifts the block in `
      + `Settings → Devices.${kept ? ' Your changes are still here.' : ''}`;
  }
  if (e && e.authCancelled) {
    const what = e.deviceTokenRequired ? 'this browser is not paired' : 'not signed in';
    // `kept` is for the callers that hold an editor buffer: the whole
    // point of saying cancelled is that the typing is still on screen.
    // A page action that had nothing to keep passes kept: false.
    return `${verb} cancelled — ${what}.${kept ? ' Your changes are still here.' : ''}`;
  }
  if (isAuthFailure(e)) return null;
  return `${verb} failed: ${apiErrorText(e, 120)}`;
};

const apiGet = (path) => apiFetch(path);
const apiPost = (path, body) => apiFetch(path, { method: 'POST', body: JSON.stringify(body || {}) });
const apiPatch = (path, body) => apiFetch(path, { method: 'PATCH', body: JSON.stringify(body || {}) });
const apiDelete = (path, body) => apiFetch(path, {
  method: 'DELETE',
  body: body !== undefined ? JSON.stringify(body) : undefined,
});

// Save a server-side media file to THIS device (music track, podcast
// episode, audiobook). The endpoints serve Content-Disposition: attachment
// (the ?download=1 variants and /download routes), so the browser saves the
// file — even when API_BASE points at a cross-origin Domovoi server (the
// selected server), where the <a download> attribute alone would be ignored.
// The server also picks the filename, so no name is set here.
const deviceDownload = (path) => {
  const a = document.createElement('a');
  a.href = path.startsWith('http') ? path : `${API_BASE}${path}`;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
};

// Multipart upload (file + fields). Doesn't set Content-Type — the
// browser fills in the multipart boundary. Used by the Voices page to
// upload Piper .onnx models.
const apiUpload = (path, formData) => {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`;
  const send = () => fetch(url, {
    method: 'POST',
    body: formData,
    credentials: 'include',
    // Deliberately no Content-Type (the browser writes the multipart
    // boundary) — but the preflight-forcing header rides along, which is
    // what stops a cross-site form posting here.
    headers: apiHeaders(),
  });
  return _sendWithAuthRetry(send, { method: 'POST', body: formData });
};

// ─── Shared WebSocket bus ───────────────────────────────────────────
// One socket per page-load; subscribers get every event and filter
// client-side. The protocol's server-side filter is a perf
// optimization not a correctness gate, so subscribing-to-all and
// distributing locally is fine at homelab scale.

class StateBus {
  constructor() {
    this.subscribers = new Set();
    this.ws = null;
    this.connected = false;
    this.reconnectDelayMs = 1000;
    this.shouldRun = false;
  }

  start() {
    if (this.shouldRun) return;
    this.shouldRun = true;
    this._connect();
  }

  _connect() {
    if (!this.shouldRun) return;
    const httpBase = API_BASE
      || `${window.location.protocol}//${window.location.host}`;
    const url = httpBase.replace(/^http/, 'ws') + WS_PATH;
    // The state stream needs a household credential (WEB-9). A signed-in
    // browser has the session cookie, which rides the handshake on its own;
    // a paired one that is not signed in offers its device token as a
    // SUBPROTOCOL instead. That is the one place a browser can put a
    // credential on a WebSocket handshake — it cannot set a header, and a
    // query string would end up in every access log.
    const device = deviceToken();
    const protocols = wsDeviceSubprotocols(device);
    let ws;
    try {
      ws = protocols ? new WebSocket(url, protocols) : new WebSocket(url);
    } catch (e) {
      // The constructor throws a SyntaxError whose MESSAGE contains the
      // whole subprotocol string — i.e. the household token. Never log the
      // exception, and do not schedule a reconnect that will throw again
      // forever: retry once with no subprotocol, which still authenticates
      // a signed-in browser by its cookie (a kiosk, which has only the
      // token, is then refused by the server rather than silently looping).
      console.warn('ws connect failed with a device subprotocol — retrying without one');
      try {
        ws = new WebSocket(url);
      } catch {
        console.warn('ws connect failed');
        this._scheduleReconnect();
        return;
      }
    }
    this.ws = ws;

    ws.addEventListener('open', () => {
      this.connected = true;
      this.reconnectDelayMs = 1000;
      // Empty subscribe = subscribe to all channels (server contract).
      // `device_token` in the frame is the older FE-2 spelling; the
      // credential that counts rode the handshake above (subprotocol
      // or cookie), and the server accepts and ignores this field.
      const hello = { subscribe: [] };
      if (device) hello.device_token = device;
      try { ws.send(JSON.stringify(hello)); } catch {}
      this._notifyAll({ type: '_status', connected: true });
    });

    ws.addEventListener('message', (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      this._notifyAll(msg);
    });

    ws.addEventListener('close', () => {
      this.connected = false;
      this._notifyAll({ type: '_status', connected: false });
      this._scheduleReconnect();
    });

    ws.addEventListener('error', () => {
      // 'error' precedes 'close' in browsers; the close handler does
      // the reconnect bookkeeping. Just log here.
      // (Don't spam — most "errors" on close are normal.)
    });
  }

  _scheduleReconnect() {
    if (!this.shouldRun) return;
    const delay = this.reconnectDelayMs;
    this.reconnectDelayMs = Math.min(delay * 1.6, 15000);
    setTimeout(() => this._connect(), delay);
  }

  _notifyAll(event) {
    for (const cb of this.subscribers) {
      try { cb(event); } catch (e) { console.warn('ws subscriber threw:', e); }
    }
  }

  subscribe(cb) {
    this.subscribers.add(cb);
    if (!this.shouldRun) this.start();
    return () => this.subscribers.delete(cb);
  }
}

const stateBus = new StateBus();

// ─── React hooks ────────────────────────────────────────────────────
//
// We reach the hooks via the `React.` prefix here rather than
// destructuring `const { useState, useEffect } = React;`. Babel-in-
// browser shares top-level scope across every
// `<script type="text/babel">` tag, so a destructure in data.js plus
// the same destructure in components.jsx would collide with a
// SyntaxError and kill the whole bundle. The verbose `React.X` form
// sidesteps that without forcing components.jsx (which is the
// canonical first-declared place for these names) to change.

// One-shot list fetch with refresh. `eventTypes` is a list of WS
// event types that should trigger a refetch (server doesn't always
// embed the full new payload, so a refetch is the safest move).
const useApiList = (path, { eventTypes = [], pickItems = (x) => x } = {}) => {
  const [items, setItems] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  // Guards the post-login retry below to one attempt per error.
  const retriedRef = React.useRef(false);

  const refresh = React.useCallback(async () => {
    try {
      const data = await apiGet(path);
      setItems(pickItems(data) || []);
      setError(null);
      retriedRef.current = false;
    } catch (e) {
      console.warn(`fetch ${path}:`, e);
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [path]);

  React.useEffect(() => { refresh(); }, [refresh]);

  // Recover after login (or pairing). An admin-gated path 401s on first
  // mount (the token lives only in JS memory, so a page load always starts
  // unauthenticated), which pops the login modal via _maybeRequestLogin —
  // but nothing re-ran the request once the user authenticated, leaving
  // the panel stuck on a stale error against a now-valid session. Re-fetch
  // when a 401/403 is followed by a successful login or pairing.
  //
  // AT MOST ONE retry per error, reset on any success. A 401 that persists
  // while logged in is a real failure (e.g. a web→core hop that forgets to
  // forward credentials), and retrying it on every Auth notify produces an
  // infinite login-modal loop rather than surfacing the bug.
  React.useEffect(() => {
    if (!error || (error.status !== 401 && error.status !== 403)) return;
    if (typeof Auth === 'undefined') return;
    try {
      // Retry when a CREDENTIAL changed since the refusal — a login, or
      // the household token arriving through the pair modal — never on
      // the notify that merely opened a modal.
      const seen = Auth.credentialVersion;
      return Auth.subscribe(() => {
        if (Auth.credentialVersion === seen || retriedRef.current) return;
        if (!Auth.isLoggedIn() && !(Auth.isPaired && Auth.isPaired())) return;
        retriedRef.current = true;
        refresh();
      });
    } catch { /* auth.js absent — nothing to recover from */ }
  }, [error, refresh]);

  React.useEffect(() => {
    if (!eventTypes || eventTypes.length === 0) return;
    return stateBus.subscribe((ev) => {
      if (eventTypes.includes(ev.type)) refresh();
    });
  }, [refresh, eventTypes.join(',')]);

  return { items, loading, error, refresh, setItems };
};

// One-shot single-resource fetch (e.g. /api/config). Same shape as
// useApiList minus the array-ness — `data` instead of `items`.
const useApiObject = (path, { eventTypes = [] } = {}) => {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  // Guards the post-login retry below to one attempt per error.
  const retriedRef = React.useRef(false);

  const refresh = React.useCallback(async () => {
    // Skip when path is null/empty (drawers fetch conditionally
    // and pass null when they're closed). Without this guard,
    // `apiGet(null)` would issue a request against the literal
    // string 'null'.
    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    try {
      setData(await apiGet(path));
      setError(null);
      retriedRef.current = false;
    } catch (e) {
      console.warn(`fetch ${path}:`, e);
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [path]);

  React.useEffect(() => { refresh(); }, [refresh]);

  // Recover after login (or pairing). An admin-gated path 401s on first
  // mount (the token lives only in JS memory, so a page load always starts
  // unauthenticated), which pops the login modal via _maybeRequestLogin —
  // but nothing re-ran the request once the user authenticated, leaving
  // the panel stuck on a stale error against a now-valid session. Re-fetch
  // when a 401/403 is followed by a successful login or pairing.
  //
  // AT MOST ONE retry per error, reset on any success. A 401 that persists
  // while logged in is a real failure (e.g. a web→core hop that forgets to
  // forward credentials), and retrying it on every Auth notify produces an
  // infinite login-modal loop rather than surfacing the bug.
  React.useEffect(() => {
    if (!error || (error.status !== 401 && error.status !== 403)) return;
    if (typeof Auth === 'undefined') return;
    try {
      // Retry when a CREDENTIAL changed since the refusal — a login, or
      // the household token arriving through the pair modal — never on
      // the notify that merely opened a modal.
      const seen = Auth.credentialVersion;
      return Auth.subscribe(() => {
        if (Auth.credentialVersion === seen || retriedRef.current) return;
        if (!Auth.isLoggedIn() && !(Auth.isPaired && Auth.isPaired())) return;
        retriedRef.current = true;
        refresh();
      });
    } catch { /* auth.js absent — nothing to recover from */ }
  }, [error, refresh]);

  React.useEffect(() => {
    if (!eventTypes || eventTypes.length === 0) return;
    return stateBus.subscribe((ev) => {
      if (eventTypes.includes(ev.type)) refresh();
    });
  }, [refresh, eventTypes.join(',')]);

  return { data, loading, error, refresh };
};

// Debounce any value. Tail-edge: returns the latest value after it
// stops changing for `delay` ms. Used to avoid refetching on every
// keystroke when a filter input drives a server-side query.
const useDebouncedValue = (value, delay = 250) => {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
};

// Subscribe to specific WS event types and feed every event to a
// callback. Useful for streaming updates (now-playing tick, satellite
// presence) where we don't want to refetch on every event.
const useStateEvents = (eventTypes, onEvent) => {
  const cbRef = React.useRef(onEvent);
  cbRef.current = onEvent;
  React.useEffect(() => {
    return stateBus.subscribe((ev) => {
      if (!eventTypes || eventTypes.length === 0 || eventTypes.includes(ev.type)) {
        cbRef.current?.(ev);
      }
    });
  }, [eventTypes.join(',')]);
};

// Sidebar count badges. Computed from the same lists each page uses;
// kept here so the App shell can render counts without each page
// having to lift state up.
const useSidebarCounts = () => {
  const [counts, setCounts] = React.useState({
    music: null, people: null, satellites: null, calendar: null,
  });

  React.useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      const [music, people, sats, cal] = await Promise.all([
        // Library endpoint returns {total, items}; ask for 1 item so the
        // network payload stays tiny — we only want the count.
        apiGet('/api/music/library?limit=1').catch(() => null),
        apiGet('/api/people').catch(() => null),
        apiGet('/api/satellites').catch(() => null),
        apiGet('/api/calendar/events').catch(() => null),
        // Plugin pages declare their own sidebar badges in the plugin
        // manifest (usePluginBadges in components.jsx) — nothing
        // plugin-specific is fetched here.
      ]);
      if (cancelled) return;
      const lenOf = (x) => Array.isArray(x) ? x.length : (x?.total ?? x?.items?.length ?? null);
      setCounts({
        music: typeof music?.total === 'number' ? music.total : null,
        people: lenOf(people),
        satellites: lenOf(sats),
        calendar: lenOf(cal),
      });
    };
    refresh();
    // Only refetch on events that actually CHANGE counts. Earlier
    // version subscribed to `people.last_seen.changed` and
    // `satellites.presence.changed` too — but those don't add or
    // remove rows from their tables (`last_seen` is a timestamp
    // update; presence is online/offline flag, not row creation), and
    // they fire frequently enough during normal operation to flood
    // the network with 5× parallel sidebar GETs each time. The
    // events listed here are the ones whose underlying tables
    // actually gain/lose rows.
    const off = stateBus.subscribe((ev) => {
      if (
        ev.type === 'library.indexer.changed'
        || ev.type === 'calendar.events.changed'
      ) refresh();
    });
    return () => { cancelled = true; off(); };
  }, []);

  return counts;
};

// ─── Device identity ────────────────────────────────────────────────
// This browser's stable id + human name. The id is the SAME one spoken
// audio has always used for resume positions ('domovoi-client-id'), so a
// device is one device everywhere — renaming it in Settings relabels its
// room-queue entries, and an admin block on it covers both features.
//
// register() is fire-and-forget on boot: it upserts the row, refreshes
// last_seen_at, and seeds the name ONLY if the row is new (the server
// COALESCEs), so this can run on every load without stomping a rename.

const DeviceIdentity = (() => {
  const ID_KEY = 'domovoi-client-id';
  const NAME_KEY = 'domovoi-device-name';   // local echo, for instant render

  const id = () => {
    let v = null;
    try { v = localStorage.getItem(ID_KEY); } catch {}
    if (!v) {
      v = 'browser-' + Math.random().toString(36).slice(2, 12);
      try { localStorage.setItem(ID_KEY, v); } catch {}
    }
    return v;
  };

  /* A name a person will recognise in a queue, from what the browser will
   * actually tell us. Deliberately coarse: userAgent parsing is a losing
   * game, and this is only a SEED — the real answer is whatever the user
   * types in Settings. */
  const suggestedName = () => {
    const ua = (navigator.userAgent || '');
    const browser = /Edg\//.test(ua) ? 'Edge'
      : /OPR\//.test(ua) ? 'Opera'
      : /Firefox\//.test(ua) ? 'Firefox'
      : /Chrome\//.test(ua) ? 'Chrome'
      : /Safari\//.test(ua) ? 'Safari'
      : 'Browser';
    const os = /Windows/.test(ua) ? 'Windows'
      : /Android/.test(ua) ? 'Android'
      : /iPhone|iPad|iPod/.test(ua) ? 'iOS'
      : /Mac OS X/.test(ua) ? 'macOS'
      : /Linux/.test(ua) ? 'Linux'
      : null;
    return os ? `${browser} on ${os}` : browser;
  };

  const cachedName = () => {
    try { return localStorage.getItem(NAME_KEY) || null; } catch { return null; }
  };
  const cacheName = (name) => {
    try {
      if (name) localStorage.setItem(NAME_KEY, name);
      else localStorage.removeItem(NAME_KEY);
    } catch {}
  };

  let registered = null;   // in-flight / resolved registration promise

  const register = () => {
    if (registered) return registered;
    registered = apiPost('/api/devices/register', {
      device_id: id(),
      name: suggestedName(),
      platform: 'browser',
      user_agent: (navigator.userAgent || '').slice(0, 400),
    }).then((row) => {
      if (row && row.name) cacheName(row.name);
      return row;
    }).catch((e) => {
      // Never fatal: the dashboard works unnamed, queue entries just show
      // no "added by" tag. Retry on the next load.
      console.warn('device register failed:', e);
      registered = null;
      return null;
    });
    return registered;
  };

  const rename = async (name) => {
    const row = await apiPatch(`/api/devices/${encodeURIComponent(id())}`, { name });
    if (row && row.name) cacheName(row.name);
    return row;
  };

  return { id, name: cachedName, suggestedName, register, rename };
})();

// ─── Time helpers (page-local NOW vs reference NOW) ─────────────────
// The skill's components.jsx exposes a frozen NOW for sample data.
// The wired pages need wall-clock NOW so relative times tick.
const liveNow = () => new Date();
const liveRelTime = (iso) => {
  if (!iso) return '—';
  const t = new Date(iso);
  const s = (liveNow() - t) / 1000;
  if (s < 30) return 'just now';
  if (s < 90) return '1m ago';
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

// Expose to other Babel scripts (mirrors components.jsx's pattern).
Object.assign(window, {
  apiGet, apiPost, apiPatch, apiDelete, deviceDownload,
  apiFetch, apiFetchRaw, apiUpload,
  apiHeaders, withDeviceToken,
  stateBus, ServerStore, DeviceIdentity, apiErrorText, isAuthFailure,
  mutationErrorText, deviceBlockReason, clipSentence,
  useApiList, useApiObject, useStateEvents, useSidebarCounts,
  useDebouncedValue,
  liveNow, liveRelTime,
});
