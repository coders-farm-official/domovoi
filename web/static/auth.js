/* Admin auth wiring (design §7.2/§7.3, v1 lightweight model) + the
 * household device token (the device tier, 2026-09-22).
 *
 * The bearer token lives ONLY in JS memory — mutations send it as
 * `Authorization: Bearer` explicitly (data.js attaches it), while the
 * HttpOnly SameSite=Strict cookie the login endpoint sets lets plain
 * GET page loads render authenticated state after a reload.
 *
 * A reload forgets the token — but NOT within the reload window (owner
 * decision 2026-10-14: "refreshing should not lose admin on the same
 * machine, same session"). For ADMIN_RELOAD_WINDOW_MIN (default 60)
 * minutes after the password was entered in this browser, the status read
 * says so (`resume_in_sec`) and `resume()` asks POST /api/auth/resume for
 * the session's own token back: the HttpOnly cookie proves the browser,
 * the server checks the origin and the window. The shell's boot waits on
 * it (`ready()`, index.html), so a reload inside the window renders signed
 * in, with no prompt and no "view only" in between; a refused action
 * tries it before it opens the sign-in prompt (ensureLoggedIn /
 * requestLogin). The token goes back into memory ONLY, never into web
 * storage. The window counts from the sign-in, not from use.
 *
 * Past the window a reloaded tab is VIEW-ONLY: `status.authenticated` (the
 * cookie) with no token. It renders; every change asks for the password
 * again, and the settings it reads come back with secrets masked and
 * without the advanced section (CORE-6). The Settings Admin card says so
 * and offers "sign in again", and the reads whose answer depends on the
 * credential re-read on every credentialVersion change (data.js
 * refetchOnAuth).
 *
 * The DEVICE TOKEN is different: it is the household's credential for
 * ordinary actions (queue edits, announcements, intents…), not a
 * person's, so it persists in localStorage — one entry per server this
 * dashboard has been pointed at — and rides along on every request as
 * `X-Device-Token`. A browser learns it in one of two ways: an admin
 * login fetches it (`GET /api/auth/device-token`) and stores it
 * silently, or a request refused for want of it pops the "pair this
 * browser" modal, where the household token (Settings → Phones & browsers on an
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
  // logout, pair, unpair, resume) so a hook that errored can tell
  // "something changed since my 401" from "the modal merely opened".
  let credentialVersion = 0;
  // The reload window (owner decision 2026-10-14). `resumeEndsAt`: when
  // this browser's session stops being resumable, on THIS browser's clock
  // (Date.now() + the server's `resume_in_sec`, never its wall-clock
  // `resume_until`: a device whose clock is off would otherwise ask, or
  // say, the wrong thing); null = not resumable. `windowMin`: the window's
  // length as the server last said it (null = never said: an older server).
  let resumeEndsAt = null;
  let windowMin = null;
  let resuming = null;       // the one POST /api/auth/resume in flight
  // The server refused a resume on this page (the window closed early, the
  // session ended, or this origin is not the page's own — a proxy in
  // front, say). Not asked again until a sign-in or a fresh page load:
  // every status read would otherwise send one more refused request.
  let resumeRefused = false;
  // Why the last sign-out did not happen ("the Domovoi server did not
  // answer"), or null. Set by logout() when the server never confirmed it;
  // cleared by any sign-in, a resume, or a sign-out that went through.
  let signOutError = null;
  let statusRead = null;     // the one GET /api/auth/status in flight
  let readyPromise = null;   // ready(): the first status read (+ resume)
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
  const perServer = (key) => (base() ? `${key}@${base()}` : key);
  const deviceTokenKey = () => perServer(DEVICE_TOKEN_KEY);
  const readDeviceToken = () => {
    try { return localStorage.getItem(deviceTokenKey()) || null; } catch { return null; }
  };

  // ── A household token the server no longer takes (2026-10-17) ────
  //
  // The server answers a wrong household token 401, and an address that
  // keeps sending wrong ones 429 (five free, then a doubling wait to five
  // minutes, per address: DEVICE_TOKEN_BACKOFF in domovoi/admin_auth.py).
  // A tab left open on a server that was reinstalled, or whose household
  // token was changed, used to send its old token on every poll and every
  // socket reconnect, for as long as it stayed open, and kept that wait
  // running for EVERY device at its address: the owner's phone app was
  // locked out by a forgotten browser tab on the same phone.
  //
  // So two facts are kept, per server, beside the token:
  //   * REFUSED: the token the server answered "wrong" to (the value itself,
  //     so a new token, typed here or stored by another tab, is not
  //     refused). It is not sent again: not as the header, not on the state
  //     socket, not in a media URL. Requests go out without it, which the
  //     server does not count. The shell says "this Domovoi doesn't
  //     recognise this browser any more" (DeviceTokenNotice).
  //   * WAIT: until when the server asked this address to wait (429
  //     Retry-After). Until then the token is not sent either.
  // A token is only ever marked refused from a refusal of a request that
  // CARRIED it (data.js); "no token" is the pair modal's business, as before.
  const DEVICE_TOKEN_REFUSED_KEY = 'domovoi-device-token-refused';
  const DEVICE_TOKEN_WAIT_KEY = 'domovoi-device-token-wait';
  const readRefused = () => {
    try { return localStorage.getItem(perServer(DEVICE_TOKEN_REFUSED_KEY)) || null; } catch { return null; }
  };
  const writeRefused = (value) => {
    try {
      if (value) localStorage.setItem(perServer(DEVICE_TOKEN_REFUSED_KEY), value);
      else localStorage.removeItem(perServer(DEVICE_TOKEN_REFUSED_KEY));
    } catch {}
  };
  const readWaitUntil = () => {
    try { return Number(localStorage.getItem(perServer(DEVICE_TOKEN_WAIT_KEY))) || 0; } catch { return 0; }
  };
  const writeWaitUntil = (ms) => {
    try {
      if (ms) localStorage.setItem(perServer(DEVICE_TOKEN_WAIT_KEY), String(Math.round(ms)));
      else localStorage.removeItem(perServer(DEVICE_TOKEN_WAIT_KEY));
    } catch {}
  };
  const waitLeftMs = () => Math.max(0, readWaitUntil() - Date.now());
  // The token that may go out now: the stored one, unless the server
  // refused it or asked this address to wait.
  const usableDeviceToken = () => {
    const stored = readDeviceToken();
    if (!stored || readRefused() === stored || waitLeftMs() > 0) return null;
    return stored;
  };

  // The core's and the web's own device-tier refusals, word for word
  // (domovoi/admin_auth.py): the only bodies that mean "this household token
  // is not accepted". A plugin's own 401 that merely names the header must
  // not make this browser drop its token (review N-T2, 2026-10-17).
  const SERVER_DEVICE_REFUSALS = [
    `${DEVICE_TOKEN_HEADER} or admin session required`,
    `${DEVICE_TOKEN_HEADER} required — the dashboard cookie does not authorize device-tier actions`,
  ];
  const isServerDeviceRefusal = (status, text) => {
    if (status !== 401 && status !== 403) return false;
    try {
      const j = JSON.parse(String(text || ''));
      return typeof j.detail === 'string' && SERVER_DEVICE_REFUSALS.indexOf(j.detail) !== -1;
    } catch { return false; }
  };

  // Seconds from a 429: Retry-After, which a cross-origin page cannot read,
  // then the server's own "retry in Ns", then a minute.
  const retryAfterSeconds = (r, text) => {
    try {
      const n = parseInt(r && r.headers && r.headers.get ? r.headers.get('Retry-After') : '', 10);
      if (Number.isFinite(n) && n > 0) return n;
    } catch { /* the words */ }
    const m = /retry in (\d+)\s*s/i.exec(String(text || ''));
    return m ? parseInt(m[1], 10) : 60;
  };

  // A device-tier read (the satellites list) any household token may make.
  const TOKEN_CHECK_PATH = '/api/satellites';

  // Any new value (or none) is not the token the server refused.
  const writeDeviceToken = (value) => {
    try {
      if (value) localStorage.setItem(deviceTokenKey(), value);
      else localStorage.removeItem(deviceTokenKey());
    } catch {}
    writeRefused(null);
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

  // ── The reload window ───────────────────────────────────────────
  // What a status / login / setup / resume answer says about it.
  const noteWindow = (data) => {
    if (!data || typeof data !== 'object') return;
    if (typeof data.reload_window_min === 'number') windowMin = data.reload_window_min;
    const sec = data.resume_in_sec;
    resumeEndsAt = (typeof sec === 'number' && sec > 0) ? Date.now() + sec * 1000 : null;
  };
  const resumeOpen = () => resumeEndsAt !== null && resumeEndsAt > Date.now();
  // A reloaded tab whose browser can take its sign-in back: no token in
  // memory, the cookie session good, the window open, the server trusted.
  const mayResume = () => !token && !resumeRefused && !!status && !!status.authenticated
    && resumeOpen() && trusted();

  // Take this browser's admin sign-in back after a reload (POST
  // /api/auth/resume). The server answers only the page holding the
  // session cookie, from this origin, inside the window, with that
  // session's OWN token — this exact session, nothing new — which goes into
  // memory and nowhere else. (The cookie is a secret of its own, never the
  // token: it is what the server unwraps the token with.) Resolves true when a token is in memory afterwards.
  // One request at a time; never throws. A refusal closes the window here
  // (the cards say "view only"); an unreachable server leaves it, so the
  // next action tries again.
  const resume = () => {
    if (token) return Promise.resolve(true);
    if (!mayResume()) return Promise.resolve(false);
    if (resuming) return resuming;
    resuming = (async () => {
      let data = null;
      try {
        // post(): the cookie rides along, with the preflight-forcing header.
        data = await post('/api/auth/resume', {});
      } catch (e) {
        // Unreachable: the window stays and nothing else changes, so the
        // next action tries again. Any answer that is not a yes closes it.
        if (!(e && e.unreachable)) { resumeRefused = true; resumeEndsAt = null; }
      }
      resuming = null;
      // A sign-in that landed meanwhile stands; it is the newer credential.
      if (token) return true;
      if (!data || typeof data.token !== 'string' || !data.token) {
        if (data) { resumeRefused = true; resumeEndsAt = null; }
        notify();
        return false;
      }
      token = data.token;
      signOutError = null;
      status = Object.assign({}, status, { setup_complete: true, authenticated: true });
      noteWindow(data);
      credentialVersion += 1;
      // The sign-in paired this browser; a browser whose storage was
      // cleared since gets the household token the same way (autoPair).
      // So is one whose stored token the server has refused since.
      if (!readDeviceToken() || readRefused() === readDeviceToken()) {
        try { await autoPair(); } catch { /* best effort */ }
      }
      notify();
      return true;
    })();
    return resuming;
  };

  // How long the window is, in words for a sentence ("an hour", "90
  // minutes"); null when it is off or the server never said.
  const windowWords = () => {
    const m = windowMin;
    if (typeof m !== 'number' || !(m > 0)) return null;
    if (m % 60 === 0) {
      const h = m / 60;
      if (h === 1) return 'an hour';
      if (h === 24) return 'a day';
      return `${h} hours`;
    }
    return m === 1 ? 'a minute' : `${m} minutes`;
  };

  // The login prompt itself, opened once (no resume attempt).
  const openLogin = () => {
    if (modalOpen) return;
    modalOpen = true;
    modalSeq += 1;
    notify();
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
  const signedIn = async (newToken, data) => {
    token = newToken;
    status = { setup_complete: true, authenticated: true };
    // The answer says when a reload stops being signed in (the window
    // starts now). An older server says nothing, and nothing is assumed.
    resumeRefused = false;
    signOutError = null;
    noteWindow(data);
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
    // Why the last sign-out did not go through, or null (logout()).
    get signOutError() { return signOutError; },
    DEVICE_TOKEN_HEADER,

    // ── The reload window (owner decision 2026-10-14) ───────────────
    // When a reload of this browser stops being signed in (Date.now()
    // milliseconds), while it still is; null otherwise.
    resumeEndsAt: () => (resumeOpen() ? resumeEndsAt : null),
    // A reloaded tab that will take its sign-in back on its first action
    // (or right now, resume()) without a prompt.
    canResume: () => mayResume(),
    // The window's length in words ("an hour"), null when it is off.
    reloadWindowWords: () => windowWords(),
    resume: () => resume(),
    // The shell waits on this before it renders (index.html): the first
    // status read and the resume it may lead to. Never rejects.
    ready() {
      if (!readyPromise) {
        readyPromise = Promise.resolve()
          .then(() => this.refreshStatus())
          .catch(() => status);
      }
      return readyPromise;
    },

    // ── Device token ────────────────────────────────────────────────
    normalizeDeviceToken,
    // The token that goes out now (header, socket, media URLs): null when
    // none is stored, when the server refused it, or while it asked this
    // address to wait.
    deviceToken: () => usableDeviceToken(),
    isPaired: () => !!usableDeviceToken(),
    // What is stored, refused or not (the Household token card's "this
    // browser holds it").
    storedDeviceToken: () => readDeviceToken(),

    // The server answered a device-tier 401/403 to a request that carried
    // `sent` (data.js): if that is still the stored token, it is not sent
    // again. Not a credential change (credentialVersion stays): nothing
    // new can be tried with it.
    noteDeviceRefusal(sent) {
      if (!sent || readDeviceToken() !== sent || readRefused() === sent) return;
      writeRefused(sent);
      notify();
    },
    // The server answered 429 for wrong household tokens from this
    // address: wait `seconds` before the token goes out again. When the
    // wait is over the token counts as usable again (credentialVersion),
    // so the reads it refused are asked once more.
    noteDeviceWait(seconds) {
      const s = Math.min(Math.max(Number(seconds) || 60, 1), 15 * 60);
      const until = Date.now() + s * 1000;
      if (until <= readWaitUntil()) return;
      writeWaitUntil(until);
      notify();
      try {
        const t = setTimeout(() => {
          if (waitLeftMs() > 0) return;
          credentialVersion += 1;
          notify();
        }, s * 1000 + 50);
        // (Node's timers keep a test harness alive; a browser's are numbers.)
        if (t && typeof t.unref === 'function') t.unref();
      } catch { /* no timers (a test harness): the next read still finds it over */ }
    },
    // What stands between this browser and the server, for the notice:
    // null, or {kind: 'refused' | 'waiting', waitMs}. Nothing while no
    // token is stored: that is the pair modal's business.
    deviceTrouble() {
      const stored = readDeviceToken();
      if (!stored) return null;
      const waitMs = waitLeftMs();
      if (readRefused() === stored) return { kind: 'refused', waitMs };
      if (waitMs > 0) return { kind: 'waiting', waitMs };
      return null;
    },
    isServerDeviceRefusal,
    retryAfterSeconds,

    // Ask the server ONCE whether it takes `candidate` (2026-10-17: a pasted
    // token used to be saved unchecked, and an old custom one left every
    // page failing). One device-tier read carrying the candidate alone
    // (credentials: 'omit', so no cookie vouches for it). Resolves
    //   {kind: 'accepted'} | {kind: 'refused'} | {kind: 'waiting', waitMs}
    //   | {kind: 'unavailable'} (a 503, no answer, an untrusted server).
    // While the server makes this address wait nothing is sent. Never
    // retried: a wrong paste costs one wrong token on the server's count.
    async checkDeviceToken(candidate) {
      const clean = storableDeviceToken(candidate);
      if (!clean) return { kind: 'refused' };
      const left = waitLeftMs();
      if (left > 0) return { kind: 'waiting', waitMs: left };
      if (!trusted()) return { kind: 'unavailable' };
      let r = null;
      let text = '';
      try {
        r = await fetch(`${base()}${TOKEN_CHECK_PATH}`, {
          credentials: 'omit', cache: 'no-store',
          headers: { [DEVICE_TOKEN_HEADER]: clean, 'X-Requested-With': 'XMLHttpRequest' },
        });
        text = await r.text().catch(() => '');
      } catch { return { kind: 'unavailable' }; }
      if (r.ok || r.status === 404) return { kind: 'accepted' };
      if (isServerDeviceRefusal(r.status, text)) return { kind: 'refused' };
      if (r.status === 429 && /x-device-token|device token|household token/i.test(text)) {
        this.noteDeviceWait(retryAfterSeconds(r, text));
        return { kind: 'waiting', waitMs: waitLeftMs() };
      }
      return { kind: 'unavailable' };
    },
    // The pair prompt's "pair": check, and store only what the server takes.
    async checkAndPair(candidate) {
      const out = await this.checkDeviceToken(candidate);
      if (out.kind === 'accepted') this.pair(candidate);
      return out;
    },
    // "try the old token again": the refusal may have been a blip. ONE probe
    // with the refused token; only when the server takes it is it released
    // to every panel (credentialVersion). Before, every open panel re-read at
    // once with it, which could start the address's wait (review N-T1).
    async retryRefusedDeviceToken() {
      const refused = readRefused();
      if (!refused) return { kind: 'accepted' };
      const out = await this.checkDeviceToken(refused);
      if (out.kind === 'accepted' && readRefused() === refused) {
        writeRefused(null);
        credentialVersion += 1;
      }
      notify();
      return out;
    },
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
      const device = usableDeviceToken();
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

    // Called by data.js on a 401/403 — pops the login modal once. Inside
    // the reload window the sign-in comes back first, with no prompt
    // (resume); the modal opens only if it can't.
    requestLogin() {
      if (modalOpen) return;
      if (mayResume()) {
        resume().then((ok) => { if (!ok && !token) openLogin(); });
        return;
      }
      openLogin();
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
    //
    // Inside the reload window a tab with no token takes this browser's
    // sign-in back first (resume) and only prompts if that fails — the
    // replayed request then goes out with no modal at all. The resumed
    // token is the session's own, so a refusal of THAT token still
    // prompts, as above.
    ensureLoggedIn(refusedToken) {
      const usable = () => !!token && token !== refusedToken;
      if (usable()) return Promise.resolve(true);
      const prompt = () => {
        openLogin();
        return new Promise((resolve) => {
          const un = this.subscribe(() => {
            if (usable()) { un(); resolve(true); }
            else if (!modalOpen) { un(); resolve(false); }
          });
        });
      };
      if (!mayResume()) return prompt();
      return resume().then(() => (usable() ? true : prompt()));
    },

    // The pairing twin of ensureLoggedIn: resolve true once a device
    // token OTHER than the refused one is stored (the pair modal, or an
    // admin login's auto-pair), false when the modal is dismissed.
    ensurePaired(refusedDeviceToken) {
      const usable = () => {
        const current = usableDeviceToken();
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

    // One read at a time: a second caller while one is in flight gets
    // that one's answer. A tab inside the reload window takes its sign-in
    // back before this resolves, so whoever waits on it (ready(), the
    // Settings Admin card) sees "signed in", never "view only" first.
    refreshStatus() {
      if (statusRead) return statusRead;
      statusRead = (async () => {
        try {
          const r = await fetch(`${base()}/api/auth/status`, { credentials: 'include' });
          if (r.ok) { status = await r.json(); noteWindow(status); }
        } catch { /* server unreachable — leave stale */ }
        statusRead = null;
        if (mayResume()) await resume();
        notify();
        return status;
      })();
      return statusRead;
    },

    async setup(setupCode, password) {
      const data = await post('/api/auth/setup', {
        setup_code: setupCode, password,
      });
      // Setup ROTATES the household token: whatever this browser held
      // from the pre-setup window is stale now. signedIn fetches the
      // fresh one (autoPair).
      await signedIn(sessionToken(data), data);
      return data;
    },

    async login(password, label) {
      const data = await post('/api/auth/login', {
        password, label: label || 'dashboard',
      });
      // An admin login pairs the browser without a prompt (autoPair).
      await signedIn(sessionToken(data), data);
      return data;
    },

    // Resolves true once signed out, false when a cookie-only tab's
    // password prompt (below) was dismissed and nothing changed, or when
    // the server never confirmed the sign-out (then `signOutError` says
    // why, and the tab is still signed in).
    async logout() {
      // A reload keeps the HttpOnly cookie but forgets the bearer, and the
      // server ends a session only for a Bearer (POST /api/auth/logout: a
      // cross-site POST carrying just the cookie must not sign anyone
      // out). Sending the cookie alone was refused 401, the cookie
      // survived, and this tab said "signed out" until the next reload
      // said "signed in" again. So a tab holding only the cookie asks for
      // the password once, and revokes the session that proves — unless
      // the reload window is still open, when it takes the sign-in back
      // without asking (ensureLoggedIn → resume) and signs THAT out.
      if (!token && status && status.authenticated) {
        loginReason = 'sign-out';
        let proved = false;
        try { proved = await this.ensureLoggedIn(); } finally { loginReason = null; }
        if (!proved) { notify(); return false; }
      }
      let answer = null;
      try {
        answer = await fetch(`${base()}/api/auth/logout`, {
          method: 'POST',
          headers: { ...this.headers(), 'X-Requested-With': 'XMLHttpRequest' },
          credentials: 'include',
        });
      } catch { answer = null; }
      // Signed out only when the server says so (2xx), or says there was
      // nothing left to end (401: the session had already gone). Anything
      // else — no answer, a 5xx — leaves the session AND its cookie alive
      // on the server, and saying "signed out" would be a lie the next
      // status read takes back: inside the reload window it would resume
      // full admin on its own (review finding 1, 2026-10-14). So this tab
      // keeps its sign-in and says the sign-out did not happen.
      if (!answer || !(answer.ok || answer.status === 401)) {
        signOutError = answer
          ? `the Domovoi server answered ${answer.status}`
          : 'the Domovoi server did not answer';
        notify();
        return false;
      }
      signOutError = null;
      token = null;
      if (status) status.authenticated = false;
      // The session is gone server-side and its cookie with it: nothing
      // left to resume.
      resumeEndsAt = null;
      credentialVersion += 1;
      // The device token stays: it is the household's, not the admin's.
      notify();
      return true;
    },
  };
})();
