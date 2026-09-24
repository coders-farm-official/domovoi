/* Admin auth wiring (design §7.2/§7.3, v1 lightweight model) + the
 * household device token (the device tier, 2026-09-22).
 *
 * The bearer token lives ONLY in JS memory — mutations send it as
 * `Authorization: Bearer` explicitly (data.js attaches it), while the
 * HttpOnly SameSite=Strict cookie the login endpoint sets lets plain
 * GET page loads render authenticated state after a reload.
 *
 * The DEVICE TOKEN is different: it is the household's credential for
 * ordinary actions (queue edits, announcements, intents…), not a
 * person's, so it persists in localStorage — one entry per server this
 * dashboard has been pointed at — and rides along on every request as
 * `X-Device-Token`. A browser learns it in one of two ways: an admin
 * login fetches it (`GET /api/auth/device-token`) and stores it
 * silently, or a request refused for want of it pops the "pair this
 * browser" modal, where the household token (Settings → Devices on an
 * admin's dashboard) is pasted once. Nobody is cornered by that modal:
 * it also offers "sign in as an admin instead" (signInInstead) — the
 * first way, spelled as a button.
 *
 * `Auth` is a tiny global store: components.jsx's <AuthModalHost/>
 * subscribes and shows the login/setup modal whenever an API call
 * comes back 401/403 (data.js calls Auth.requestLogin()) and the pair
 * modal whenever the refusal names the device token
 * (Auth.requestPairing()).
 *
 * Loaded before data.js in index.html.
 */

const Auth = (() => {
  let token = null;              // in-memory bearer (never persisted)
  let modalOpen = false;
  let pairModalOpen = false;
  // null = not yet probed; {setup_complete, authenticated} afterwards.
  let status = null;
  // Bumped whenever a credential appears or goes away (login, setup,
  // logout, pair, unpair) so a hook that errored can tell "something
  // changed since my 401" from "the modal merely opened".
  let credentialVersion = 0;
  const listeners = new Set();
  const notify = () => listeners.forEach((fn) => { try { fn(); } catch {} });

  const DEVICE_TOKEN_HEADER = 'X-Device-Token';
  const DEVICE_TOKEN_KEY = 'domovoi-device-token';

  const base = () => {
    try { return localStorage.getItem('domovoi-server') || ''; } catch { return ''; }
  };

  // One stored token per server: the household token of the box that
  // served the page (same-origin, '') and, separately, of each selected
  // server — they are different households with different tokens.
  const deviceTokenKey = () => (base() ? `${DEVICE_TOKEN_KEY}@${base()}` : DEVICE_TOKEN_KEY);
  const readDeviceToken = () => {
    try { return localStorage.getItem(deviceTokenKey()) || null; } catch { return null; }
  };
  const writeDeviceToken = (value) => {
    try {
      if (value) localStorage.setItem(deviceTokenKey(), value);
      else localStorage.removeItem(deviceTokenKey());
    } catch {}
  };

  // The dashboard only talks to a server the user has explicitly trusted
  // (ServerStore in data.js — same-origin always is). Until then nothing
  // that carries a secret goes out: no password, no device token.
  const trusted = () => {
    try {
      if (typeof ServerStore === 'undefined' || !ServerStore.isTrusted) return true;
      return ServerStore.isTrusted(base());
    } catch { return true; }
  };

  // Login, setup and password change are writes like any other, so they
  // carry the preflight-forcing header too (data.js REQUESTED_WITH; the
  // value is spelled out here because auth.js loads first).
  const post = async (path, body) => {
    if (!trusted()) {
      const err = new Error('this server has not been trusted yet — pick it in the server switcher first');
      err.status = 0;
      err.untrusted = true;
      throw err;
    }
    const r = await fetch(`${base()}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      credentials: 'include', // receive/carry the GET-state cookie
      body: JSON.stringify(body || {}),
    });
    let data = null;
    try { data = await r.json(); } catch {}
    if (!r.ok) {
      const detail = (data && data.detail) || `${r.status} ${r.statusText}`;
      const err = new Error(detail);
      err.status = r.status;
      throw err;
    }
    return data;
  };

  // An admin session can read the household token: store it so this
  // browser is paired without ever seeing the modal. Best effort — a
  // failure here (e.g. the pre-setup 501, or an old server without the
  // endpoint) leaves the browser unpaired, which the modal handles later.
  const autoPair = async () => {
    if (!token) return false;
    try {
      const r = await fetch(`${base()}/api/auth/device-token`, {
        headers: { Authorization: `Bearer ${token}` },
        credentials: 'include',
      });
      if (!r.ok) return false;
      const data = await r.json();
      if (data && data.token) {
        writeDeviceToken(data.token);
        credentialVersion += 1;
        // If the "pair this browser" prompt is standing, it has just
        // been answered without anyone typing (or seeing) the token —
        // take it down. Nothing about the pairing itself changes:
        // ensurePaired still resolves off the stored token and data.js
        // still replays the refused request exactly once.
        pairModalOpen = false;
        return true;
      }
    } catch { /* stays unpaired; the pair modal covers it */ }
    return false;
  };

  return {
    get token() { return token; },
    isLoggedIn: () => !!token,
    get modalOpen() { return modalOpen; },
    get pairModalOpen() { return pairModalOpen; },
    get status() { return status; },
    get credentialVersion() { return credentialVersion; },
    DEVICE_TOKEN_HEADER,

    // ── Device token ────────────────────────────────────────────────
    deviceToken: () => readDeviceToken(),
    isPaired: () => !!readDeviceToken(),
    // Store the household token for the current server (pasted in the
    // pair modal, fetched at login, or handed over by a rotation).
    pair(value) {
      const clean = String(value || '').trim();
      if (!clean) return false;
      writeDeviceToken(clean);
      credentialVersion += 1;
      pairModalOpen = false;
      notify();
      return true;
    },
    unpair() {
      writeDeviceToken(null);
      credentialVersion += 1;
      notify();
    },

    // Every credential this browser holds for the current server, as
    // request headers. data.js attaches these to each API call.
    headers() {
      const h = {};
      if (token) h.Authorization = `Bearer ${token}`;
      const device = readDeviceToken();
      if (device) h[DEVICE_TOKEN_HEADER] = device;
      return h;
    },

    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },

    // Called by data.js on a 401/403 — pops the login modal once.
    requestLogin() {
      if (modalOpen) return;
      modalOpen = true;
      notify();
    },

    // Called by data.js when a refusal names the device token — pops
    // the "pair this browser" modal once. An admin session never needs
    // it: the login path pairs the browser itself.
    requestPairing() {
      if (pairModalOpen) return;
      pairModalOpen = true;
      notify();
    },

    // The pair modal's way out for someone who does NOT have the
    // household token — the case that made this exist: a browser with
    // cleared storage 401s on its first device-tier request and is
    // shown a prompt asking for a secret it has never heard of.
    // Signing in as an admin is the answer, because login() fetches the
    // household token itself (autoPair), so it is offered as an action
    // rather than described in a hint.
    //
    // The pair modal deliberately STAYS open underneath:
    // <AuthModalHost/> renders the login modal in preference to it, so
    // only one is ever on screen, and dismissing the login falls back
    // to the pairing prompt instead of a dead end — the flow waiting on
    // ensurePaired is still waiting, and can still be answered with the
    // token or cancelled.
    signInInstead() {
      if (modalOpen) return;
      modalOpen = true;
      notify();
    },

    // Resolve true once a live Bearer exists (popping the login modal
    // if needed), false if the user closes the modal without signing
    // in. Lets a flow that hit a 401/403 pause, authenticate, and
    // RESUME — instead of dying after the login (the failed action was
    // previously just lost).
    //
    // `refusedToken` is the bearer the caller actually sent and the
    // server actually rejected. Handing that same token straight back
    // would resume the flow with the credential that just failed, so a
    // match counts as not-signed-in and prompts. Omit it and any live
    // token satisfies the call, which is what a caller asking "is
    // anyone signed in?" means.
    ensureLoggedIn(refusedToken) {
      const usable = () => !!token && token !== refusedToken;
      if (usable()) return Promise.resolve(true);
      this.requestLogin();
      return new Promise((resolve) => {
        const un = this.subscribe(() => {
          if (usable()) { un(); resolve(true); }
          else if (!modalOpen) { un(); resolve(false); }
        });
      });
    },

    // The pairing twin of ensureLoggedIn: resolve true once a device
    // token OTHER than the refused one is stored (the pair modal, or an
    // admin login's auto-pair), false when the modal is dismissed.
    ensurePaired(refusedDeviceToken) {
      const usable = () => {
        const current = readDeviceToken();
        return !!current && current !== refusedDeviceToken;
      };
      if (usable()) return Promise.resolve(true);
      this.requestPairing();
      return new Promise((resolve) => {
        const un = this.subscribe(() => {
          if (usable()) { un(); resolve(true); }
          else if (!pairModalOpen) { un(); resolve(false); }
        });
      });
    },
    openModal() { modalOpen = true; notify(); },
    closeModal() { modalOpen = false; notify(); },
    closePairModal() { pairModalOpen = false; notify(); },

    async refreshStatus() {
      try {
        const r = await fetch(`${base()}/api/auth/status`, { credentials: 'include' });
        if (r.ok) status = await r.json();
      } catch { /* server unreachable — leave stale */ }
      notify();
      return status;
    },

    async setup(setupCode, password) {
      const data = await post('/api/auth/setup', {
        setup_code: setupCode, password,
      });
      token = data.token || null;
      status = { setup_complete: true, authenticated: !!token };
      credentialVersion += 1;
      // Setup ROTATES the household token: whatever this browser held
      // from the pre-setup window is stale now. Fetch the fresh one.
      await autoPair();
      notify();
      return data;
    },

    async login(password, label) {
      const data = await post('/api/auth/login', {
        password, label: label || 'dashboard',
      });
      token = data.token || null;
      status = { setup_complete: true, authenticated: !!token };
      credentialVersion += 1;
      // An admin login pairs the browser without a prompt.
      await autoPair();
      notify();
      return data;
    },

    async logout() {
      try {
        await fetch(`${base()}/api/auth/logout`, {
          method: 'POST',
          headers: { ...this.headers(), 'X-Requested-With': 'XMLHttpRequest' },
          credentials: 'include',
        });
      } catch { /* best-effort */ }
      token = null;
      if (status) status.authenticated = false;
      credentialVersion += 1;
      // The device token stays: it is the household's, not the admin's.
      notify();
    },
  };
})();
