/* Admin auth wiring (design §7.2/§7.3, v1 lightweight model) + the
 * household device token (the device tier, 2026-09-22).
 *
 * The bearer token lives ONLY in JS memory — mutations send it as
 * `Authorization: Bearer` explicitly (data.js attaches it), while the
 * HttpOnly SameSite=Strict cookie the login endpoint sets lets plain
 * GET page loads render authenticated state after a reload.
 *
 * So a reloaded tab is VIEW-ONLY: `status.authenticated` (the cookie) with
 * no token. It renders; every change asks for the password again, and
 * the settings it reads come back with secrets masked and without the
 * advanced section (CORE-6). The Settings Admin card says so and offers
 * "sign in again", and the reads whose answer depends on the credential
 * re-read on every credentialVersion change (data.js refetchOnAuth).
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
  // Which opening of the login modal this is. <AuthModalHost/> keys the
  // modal on it, so every opening is a fresh form — a prompt that comes
  // back never shows the password or the error of the one before it.
  let modalSeq = 0;
  let pairModalOpen = false;
  // Why the login modal is up, when it is not the usual "an admin action
  // needs you": 'sign-out' (logout, below). The modal says so.
  let loginReason = null;
  // null = not yet probed; {setup_complete, authenticated} afterwards.
  let status = null;
  // Bumped whenever a credential appears or goes away (login, setup,
  // logout, pair, unpair) so a hook that errored can tell "something
  // changed since my 401" from "the modal merely opened".
  let credentialVersion = 0;
  const listeners = new Set();
  // The modal host's own channel (subscribeModal): told about EVERY
  // change, like any listener, and also — alone — the moment a sign-in
  // succeeds, before the household-token fetch and before the flows
  // waiting on ensureLoggedIn hear about it (signedIn, below).
  const modalListeners = new Set();
  const notifyModal = () => modalListeners.forEach((fn) => { try { fn(); } catch {} });
  const notify = () => {
    listeners.forEach((fn) => { try { fn(); } catch {} });
    notifyModal();
  };

  const DEVICE_TOKEN_HEADER = 'X-Device-Token';
  const DEVICE_TOKEN_KEY = 'domovoi-device-token';

  // What gets STORED: the token exactly as it was given, with the outer
  // whitespace trimmed and nothing else. An admin may set the household
  // token to any printable ASCII of 12 characters or more, so `MyT0ken!!`
  // has to stay `MyT0ken!!` — lowercasing it here would pair the browser
  // to a token that does not exist and every request would 401.
  //
  // This used to canonicalise, because the token rode the WebSocket
  // handshake raw and a subprotocol must be an RFC 9110 token. It no
  // longer does: data.js base64url-encodes the token into the
  // `domovoi.device-token-b64.` element, so the transport's grammar is
  // the transport's problem. See normalize_device_token in
  // domovoi/admin_auth.py — the server still accepts the CANONICAL form of
  // a token that is itself canonical, which is what keeps typing a
  // generated eight-word phrase forgiving about case and spaces.
  const storableDeviceToken = (value) => String(value == null ? '' : value).trim();

  // The canonical form, for display and for comparing two spellings of a
  // generated phrase. Nothing on the storage path may call this.
  const normalizeDeviceToken = (value) =>
    String(value == null ? '' : value).trim().toLowerCase()
      .replace(/[\s_-]+/g, '-').replace(/^-+|-+$/g, '');

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
  //
  // A request that never got an answer (the server is down, restarting,
  // or unreachable from here) rejects with the browser's own words —
  // "Failed to fetch", "NetworkError when attempting to fetch resource" —
  // which read as "your password failed". It is re-thrown as `unreachable`
  // so the login modal can say what actually happened.
  const post = async (path, body) => {
    if (!trusted()) {
      const err = new Error('this server has not been trusted yet — pick it in the server switcher first');
      err.status = 0;
      err.untrusted = true;
      throw err;
    }
    let r;
    try {
      r = await fetch(`${base()}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'include', // receive/carry the GET-state cookie
        body: JSON.stringify(body || {}),
      });
    } catch (e) {
      const err = new Error('the Domovoi server did not answer');
      err.status = 0;
      err.unreachable = true;
      err.cause = e;
      throw err;
    }
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
        writeDeviceToken(storableDeviceToken(data.token));
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

  // The bearer a 2xx login/setup answer carries. An answer without one
  // signed nobody in, and says so in the modal rather than closing it on
  // a sign-in that did not happen.
  const sessionToken = (data) => {
    if (data && typeof data.token === 'string' && data.token) return data.token;
    const err = new Error('the server answered without a session — try again');
    err.status = 0;
    throw err;
  };

  // The rest of a sign-in, once the server has handed over a bearer.
  //
  // THE SIGN-IN IS DONE when the bearer arrives, so the login modal comes
  // down HERE — before the household-token fetch, before anyone is told,
  // and so before data.js replays the request the prompt was opened for.
  // What that request does next is its own business: a replayed restart
  // takes this very server away a second later, and nothing that happens
  // after this line may land in the login modal as if the password had
  // failed (2026-10-01: the owner signed in to restart, the restart went
  // ahead, and the modal stayed up saying "Failed to fetch"). Nothing
  // after this point throws: once the server has said yes, login() and
  // setup() resolve, whatever the token fetch or the replay meets.
  //
  // Only the modal host hears about it at once (notifyModal): the flows
  // waiting on ensureLoggedIn are told after the household token has been
  // fetched, as before, so a replayed request still carries both. The one
  // exception is "sign in as an admin instead" (signInInstead), where the
  // pair modal waits underneath: closing the login first would flash that
  // prompt for the length of the token fetch, so both come down together
  // once autoPair has answered it. A login modal opened AGAIN meanwhile
  // (a new opening, modalSeq) is a new prompt and is left alone.
  const signedIn = async (newToken) => {
    token = newToken;
    status = { setup_complete: true, authenticated: true };
    credentialVersion += 1;
    const opening = modalSeq;
    if (modalOpen && !pairModalOpen) {
      modalOpen = false;
      notifyModal();
    }
    try { await autoPair(); } catch { /* best effort, as autoPair says */ }
    if (modalSeq === opening) modalOpen = false;
    notify();
  };

  return {
    get token() { return token; },
    isLoggedIn: () => !!token,
    get modalOpen() { return modalOpen; },
    get modalSeq() { return modalSeq; },
    get loginReason() { return loginReason; },
    get pairModalOpen() { return pairModalOpen; },
    get status() { return status; },
    get credentialVersion() { return credentialVersion; },
    DEVICE_TOKEN_HEADER,

    // ── Device token ────────────────────────────────────────────────
    normalizeDeviceToken,
    deviceToken: () => readDeviceToken(),
    isPaired: () => !!readDeviceToken(),
    // Store the household token for the current server (pasted in the
    // pair modal, fetched at login, or handed over by a rotation).
    pair(value) {
      // Verbatim (trimmed): the server stores what an admin chose, and
      // this browser must present exactly that.
      const clean = storableDeviceToken(value);
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
    // For <AuthModalHost/> only: every change subscribe() hears, plus the
    // modal coming down the moment a sign-in succeeds (signedIn).
    subscribeModal(fn) {
      modalListeners.add(fn);
      return () => modalListeners.delete(fn);
    },

    // Called by data.js on a 401/403 — pops the login modal once.
    requestLogin() {
      if (modalOpen) return;
      modalOpen = true;
      modalSeq += 1;
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
      modalSeq += 1;
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
    openModal() {
      if (!modalOpen) modalSeq += 1;
      modalOpen = true;
      notify();
    },
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
      // Setup ROTATES the household token: whatever this browser held
      // from the pre-setup window is stale now. signedIn fetches the
      // fresh one (autoPair).
      await signedIn(sessionToken(data));
      return data;
    },

    async login(password, label) {
      const data = await post('/api/auth/login', {
        password, label: label || 'dashboard',
      });
      // An admin login pairs the browser without a prompt (autoPair).
      await signedIn(sessionToken(data));
      return data;
    },

    // Resolves true once signed out, false when a cookie-only tab's
    // password prompt (below) was dismissed and nothing changed.
    async logout() {
      // A reload keeps the HttpOnly cookie but forgets the bearer, and the
      // server ends a session only for a Bearer (POST /api/auth/logout: a
      // cross-site POST carrying just the cookie must not sign anyone
      // out). Sending the cookie alone was refused 401, the cookie
      // survived, and this tab said "signed out" until the next reload
      // said "signed in" again. So a tab holding only the cookie asks for
      // the password once, and revokes the session that proves.
      if (!token && status && status.authenticated) {
        loginReason = 'sign-out';
        let proved = false;
        try { proved = await this.ensureLoggedIn(); } finally { loginReason = null; }
        if (!proved) { notify(); return false; }
      }
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
      return true;
    },
  };
})();
