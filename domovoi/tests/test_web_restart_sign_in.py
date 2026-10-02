"""Signing in to restart: the login modal reports the SIGN-IN, and the
restart's own panel reports everything after it.

LIVE BUG (owner, 2026-10-01, the Beelink updating e19ac3b -> 6c4de76; the
update itself succeeded): Settings -> Configuration -> Version, "Restart to
apply changes" on a tab that a reload had left view-only (the cookie, no
admin Bearer), so the restart was refused and the admin login modal came up.
He typed the password; the restart went ahead — the server went away and
came back on the new code — and the modal never went away, showing the
browser's "Failed to fetch" in red under the password field, so it looked
as though the sign-in had failed.

The sign-in had not failed. "Failed to fetch" is what a sign-in sent to a
server that is not there says, and the dashboard had three ways to leave a
login modal on screen while the restart it was signing in for took the
server away:

* the modal stayed up through the whole tail of a sign-in — the household
  token fetch, every listener, and the launch of the replayed restart —
  and closed only when ``Auth.login()`` returned;
* the restart's own "is it back yet?" poll, and any other read refused while
  the server was coming back, opened a FRESH login modal (a 401/403 on a
  read always asked for a sign-in), on top of a restart in progress;
* a read sent before the sign-in and refused for the credential it carried
  re-opened the modal the moment the sign-in closed it.

Whichever put it there, a password typed into it while the server was
away never reached anything, and the modal said "Failed to fetch". Which of
the three the Beelink hit is not recorded anywhere a read-only look can
reach (a faithful replay of the plain path — accepted, gone, back — closes
the modal on the old code too); all three are closed here. Pressing Enter
twice also signed in twice.

What this module pins, driven through ``jsx_interact_harness.js`` on top of
the REAL auth.js, data.js, components.jsx and settings.jsx (plugins.jsx for
the Plugins page's restart card) — only ``fetch`` is scripted: PRELUDE plays
a server that answers by the credential a request carried, accepts a
restart from a Bearer and then GOES AWAY (every request rejected the way a
browser rejects one to a dead socket) until the restart's poll has knocked
enough times, optionally answering 401 for a while on the way back up:

* the modal comes down as soon as the server accepts the password — while
  the household-token fetch is still out, before the restart is replayed;
* the replayed restart's answer (or the server cutting it off) starts the
  wait; the Version card says it is restarting, no prompt opens and no
  error shows while the server is away or half up, and the card shows the
  new version once it answers;
* a read refused for a credential the sign-in replaced opens no prompt and
  is read again under the new one;
* a sign-in that genuinely fails still shows in the modal: the server's
  "wrong password", and — for a server that is down before the sign-in —
  words that say so, never "Failed to fetch";
* one sign-in per press, Enter or no Enter; a prompt that comes back is a
  fresh form, and one that opens while a sign-in is still finishing is left
  standing until the restart takes the server away;
* the button does not offer the restart again before the card's own
  re-read of the new version lands;
* a 2xx sign-in answer that carries no session stays in the modal;
* the Plugins page's "restart to finish the upgrade" is the same restart.

No DB, never ``requires_db``; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/auth.js", "web/static/data.js", "web/static/components.jsx",
         "web/static/settings.jsx"]
PLUGIN_FILES = ["web/static/auth.js", "web/static/data.js", "web/static/components.jsx",
                "web/static/plugins.jsx"]

OLD_SHA = "e19ac3b"
NEW_SHA = "6c4de76"
PASSWORD = "right-password"

# ─── the in-sandbox prelude: a server that restarts ──────────────────────

PRELUDE = r"""
const __st = setTimeout;
// The restart's poll waits 2 s between reads in a browser; here it waits a
// couple of milliseconds, so a scenario walks a whole restart. Every other
// timer keeps its length (a toast stays up long enough to be read), and
// does not keep node alive once the scenarios are done.
setTimeout = (fn, ms, ...a) => {
  const poll = Number(ms) === 2000;
  const t = __st(fn, poll ? 2 : ms, ...a);
  if (!poll && t && t.unref) t.unref();
  return t;
};
setInterval = () => 0; clearInterval = () => {};
const __store = new Map(Object.entries(__LS || {}));
localStorage = {
  getItem: (k) => (__store.has(k) ? __store.get(k) : null),
  setItem: (k, v) => { __store.set(k, String(v)); },
  removeItem: (k) => { __store.delete(k); },
};
document.hidden = false;
WebSocket = function () { this.l = {}; };
WebSocket.prototype.addEventListener = function () {};
WebSocket.prototype.send = function () {};

// phase: 'up' answers; 'down' rejects every request at the socket, as a
// browser does for a server that is not there ("Failed to fetch");
// 'halfup' answers 401 to every /api/ route but /api/auth/* (a server back
// on its feet before it can check a session). A restart moves up -> down
// on the request AFTER it answered (or at once, with cutReplay), and the
// restart's own poll walks it back: downPolls version reads refused, then
// halfUpPolls answered 401, then up on the new code.
window.__srv = {
  sessions: new Set(__SESSIONS), cookie: __COOKIE, n: 0, device: 'house-token',
  running: __OLD, checkout: __NEW, run: 'run-1',
  phase: 'up', goDown: false, restarted: false,
  downPolls: __DOWN_POLLS, halfUpPolls: __HALFUP_POLLS, cutReplay: __CUT_REPLAY,
};
window.__fetches = [];
// `__hold[key] = n` holds the next n answers to `key` until __release():
// the answer is computed when the request is SENT (under the credential it
// carried) and delivered later.
window.__hold = {};
window.__held = [];
window.__release = () => { const x = window.__held.shift(); if (x) x.deliver(); return !!x; };
// The login modal opening and closing, in order, with how many requests had
// been sent at that moment.
window.__events = [];
const __answer = (status, body) => {
  const text = body == null ? '' : JSON.stringify(body);
  return { ok: status >= 200 && status < 300, status, statusText: String(status),
           text: async () => text, json: async () => JSON.parse(text) };
};
const __CUT = { cut: true };
const __ADMIN_401 = { detail: 'admin session required' };
const __version = () => {
  const s = window.__srv;
  const pending = s.running !== s.checkout;
  return { sha: s.running, running_sha: s.running, checkout_sha: s.checkout,
           restart_required: pending, code_restart_required: pending, plugins_pending_restart: [],
           restart_capable: true, restart_mode: 'update', restart_hint: null,
           last_update: { status: 'ok', mode: 'update', started_at: s.run,
                          from_sha: 'aaaaaaa', to_sha: s.running } };
};
const __route = (method, path, c, body) => {
  const srv = window.__srv;
  const admin = c.who === 'bearer' || c.who === 'cookie';
  const bare = path.split('?')[0];
  if (method === 'POST' && bare === '/api/auth/login') {
    let pw = null;
    try { pw = JSON.parse(body || '{}').password; } catch {}
    // A 2xx that hands over no session (a broken proxy, an older server).
    if (pw === '__no_token__') return __answer(200, { ok: true });
    if (pw !== __PASSWORD) return __answer(401, { detail: 'wrong password' });
    srv.n += 1;
    const token = `session-${srv.n}`;
    srv.sessions.add(token); srv.cookie = token;
    return __answer(200, { ok: true, token });
  }
  if (method === 'POST' && bare === '/api/config/version/restart') {
    if (c.who === 'cookie') {
      return __answer(403, { detail: 'mutations require Authorization: Bearer — the dashboard cookie only renders GET state' });
    }
    if (c.who !== 'bearer') return __answer(401, __ADMIN_401);
    srv.restarted = true;
    if (srv.cutReplay) { srv.phase = 'down'; return __CUT; }
    srv.goDown = true;
    return __answer(200, { ok: true, mode: 'update', units: ['domovoi-update.service'], delay_sec: 1, error: null });
  }
  if (method !== 'GET') return __answer(404, { detail: 'not found' });
  if (bare === '/api/auth/status') return __answer(200, { setup_complete: true, authenticated: admin });
  if (bare === '/api/auth/device-token') {
    return admin ? __answer(200, { token: srv.device, header: 'X-Device-Token' }) : __answer(401, __ADMIN_401);
  }
  if (bare === '/api/config') return __answer(200, { bot_name: 'Domovoi', web_version: 'test' });
  if (bare === '/api/config/version') return __answer(200, __version());
  // An admin read the cookie does not satisfy.
  if (bare === '/api/needs-bearer') return c.who === 'bearer' ? __answer(200, { who: 'bearer' }) : __answer(401, __ADMIN_401);
  // A web→core hop that drops the credential: refused whatever is sent.
  if (bare === '/api/broken') return __answer(401, __ADMIN_401);
  return __answer(404, { detail: 'not found' });
};
fetch = async (url, opts) => {
  const o = opts || {};
  const method = String(o.method || 'GET').toUpperCase();
  const path = String(url).replace(/^https?:\/\/[^/]+/, '');
  const bare = path.split('?')[0];
  const hd = o.headers || {};
  const srv = window.__srv;
  if (srv.goDown) { srv.goDown = false; srv.phase = 'down'; }
  const auth = String(hd.Authorization || '');
  const bearer = auth.startsWith('Bearer ') ? auth.slice(7) : null;
  const device = hd['X-Device-Token'] || null;
  const who = bearer ? (srv.sessions.has(bearer) ? 'bearer' : 'invalid')
    : (srv.cookie && srv.sessions.has(srv.cookie) ? 'cookie' : 'none');
  window.__fetches.push({ method, path, who, device, phase: srv.phase });
  if (srv.phase === 'down') {
    if (method === 'GET' && bare === '/api/config/version' && srv.restarted) {
      srv.downPolls -= 1;
      if (srv.downPolls <= 0) {
        srv.phase = srv.halfUpPolls > 0 ? 'halfup' : 'up';
        srv.running = srv.checkout; srv.run = 'run-2';
      }
    }
    throw new TypeError('Failed to fetch');
  }
  if (srv.phase === 'halfup' && bare.startsWith('/api/') && !bare.startsWith('/api/auth/')) {
    if (method === 'GET' && bare === '/api/config/version') {
      srv.halfUpPolls -= 1;
      if (srv.halfUpPolls <= 0) srv.phase = 'up';
    }
    return __answer(401, __ADMIN_401);
  }
  const res = __route(method, path, { who, bearer, device }, o.body);
  if (res === __CUT) throw new TypeError('Failed to fetch');
  const key = `${method} ${bare}`;
  if (window.__hold[key]) {
    window.__hold[key] -= 1;
    return new Promise((resolve) => { window.__held.push({ key, deliver: () => resolve(res) }); });
  }
  return res;
};
window.__flush = async (h, n = 8) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
window.__cls = (e) => String((e.props && e.props.className) || '').split(' ');
"""

# The modal's opening and closing, recorded from the channel the modal host
# itself listens on (subscribeModal; plain subscribe on an older auth.js).
RECORD = ("(() => { let last = Auth.modalOpen; const rec = () => { if (Auth.modalOpen !== last) {"
          " last = Auth.modalOpen; window.__events.push({ open: last, sent: window.__fetches.length }); } };"
          " (Auth.subscribeModal || Auth.subscribe).call(Auth, rec); })()")

# Counts how often the modal HOST (<AuthModalHost/>, which subscribes after
# RECORD) is told about a change on the modal channel. The harness renders
# the whole tree on every step, so a host that never heard the early close
# would still look closed here; in a browser it stays on screen until the
# household-token fetch returns. This is what pins the host's channel.
HOST_TAP = ("(() => { const orig = Auth.subscribeModal; window.__hostTold = 0; if (!orig) return;"
            " Auth.subscribeModal = function (fn) { return orig.call(Auth, () => { window.__hostTold += 1; fn(); }); };"
            " })()")

VERSION = ("(window.__Auth = Auth, " + RECORD + ", " + HOST_TAP
           + ", function VersionWithModal() { return React.createElement("
           "React.Fragment, null, React.createElement(VersionSection), React.createElement(AuthModalHost)); })")

PLUGIN_CARD = ("(window.__Auth = Auth, " + RECORD + ", function PluginCardWithModal() {"
               " const [version, setVersion] = React.useState(null);"
               " React.useEffect(() => { apiGet('/api/config/version').then(setVersion); }, []);"
               " const [fire, node] = useToast();"
               " const pending = version && version.restart_required ? [{ slug: 'radio', from_version: '1.1.0', to_version: '1.2.0' }] : [];"
               " window.__settled = window.__settled || 0;"
               " return React.createElement(React.Fragment, null,"
               "  React.createElement(PluginRestartCard, { version, pending, fire,"
               "   onSettled: () => { window.__settled += 1; apiGet('/api/config/version').then(setVersion); } }),"
               "  node, React.createElement(AuthModalHost)); })")

PROBE = ("(window.__Auth = Auth, " + RECORD + ", function ProbeWithModal() {"
         " const o = useApiObject('/api/needs-bearer');"
         " const line = o.loading ? 'loading' : (o.error ? `error ${o.error.status}` : (o.data ? o.data.who : 'empty'));"
         " return React.createElement(React.Fragment, null,"
         "  React.createElement('div', { className: 'probe' }, line), React.createElement(AuthModalHost)); })")

# The same read through useApiList.
PROBE_LIST = ("(window.__Auth = Auth, " + RECORD + ", function ProbeListWithModal() {"
              " const o = useApiList('/api/needs-bearer', { pickItems: (x) => (x ? [x] : []) });"
              " const line = o.loading ? 'loading' : (o.error ? `error ${o.error.status}`"
              " : (o.items.length ? o.items[0].who : 'empty'));"
              " return React.createElement(React.Fragment, null,"
              "  React.createElement('div', { className: 'probe' }, line), React.createElement(AuthModalHost)); })")

# What every scenario looks at.
HELPERS = r"""
const w = h.global('window'); const A = w.__Auth;
const step = () => w.__flush(h);
const inModal = (e) => h.inside(e, (a) => w.__cls(a).includes('cal-modal'));
const modalOnScreen = () => !!h.find((e) => w.__cls(e).includes('cal-modal'));
const modalErr = () => { const e = h.find((x) => w.__cls(x).includes('err') && inModal(x)); return e ? e.text : null; };
const modalHint = (cls) => !!h.find((e) => w.__cls(e).includes(cls));
const password = (e) => e.type === 'input' && e.props.type === 'password';
const pwValue = () => { const e = h.find(password); return e ? e.props.value : null; };
const modalButton = (label) => (e) => e.type === 'button' && e.text.trim() === label
  && h.inside(e, (a) => w.__cls(a).includes('cal-modal-foot'));
const pageButtons = () => h.findAll({ type: 'button' }).filter((e) => !inModal(e))
  .map((e) => e.text.trim() + (e.props.disabled ? ' [disabled]' : ''));
const toasts = () => h.findAll((x) => x.type === 'span' && x.text
  && h.inside(x, (a) => a.props && a.props.title === 'dismiss')).map((x) => x.text);
const blob = () => h.text().join(' | ');
const underwayNote = () => !!h.find((e) => w.__cls(e).includes('restart-underway'));
const posts = (p) => w.__fetches.filter((f) => f.method === 'POST' && f.path === p).map((f) => f.who);
const restarts = () => posts('/api/config/version/restart');
const logins = () => posts('/api/auth/login').length;
// Press without waiting: a restart press resolves only when the whole
// restart is over, and a sign-in only after its token fetch.
const press = (sel) => { const e = h.find(sel); if (!e) throw new Error('nothing to press'); return e.props.onClick({ preventDefault() {}, stopPropagation() {}, target: {} }); };
const restartButton = (e) => e.type === 'button' && e.text.includes('Restart to apply changes');
const pumpUntil = async (cond, n = 400) => { for (let i = 0; i < n && !cond(); i++) await step(); return cond(); };
// The server stays away until a scenario lets it back: the restart's next
// poll then finds it (half) up on the new code.
const letBack = () => { w.__srv.downPolls = 1; };
const letUp = () => { w.__srv.halfUpPolls = 1; };
const start = async () => { h.render(); await step(); };
"""


def scenario(script: str, *, component: str = VERSION, files: list[str] | None = None,
             cookie: str | None = "old-session", ls: dict | None = None,
             down_polls: int = 10**9, halfup_polls: int = 0, cut_replay: bool = False) -> dict:
    sessions = [cookie] if cookie else []
    # A reloaded tab that had signed in before: the cookie, and the household
    # token the earlier sign-in stored.
    store = {"domovoi-device-token": "house-token"} if ls is None else ls
    head = (f"const __LS = {json.dumps(store)}; const __SESSIONS = {json.dumps(sessions)};"
            f" const __COOKIE = {json.dumps(cookie)}; const __OLD = {json.dumps(OLD_SHA)};"
            f" const __NEW = {json.dumps(NEW_SHA)}; const __PASSWORD = {json.dumps(PASSWORD)};"
            f" const __DOWN_POLLS = {down_polls}; const __HALFUP_POLLS = {halfup_polls};"
            f" const __CUT_REPLAY = {json.dumps(cut_replay)};\n")
    return {"files": files or FILES, "component": component, "props": {}, "setup": head + PRELUDE,
            "script": HELPERS + script}


SCENARIOS: dict[str, dict] = {}

# The owner's flow, step by step: a view-only tab presses Restart, signs in,
# and the server goes away right after it took the restart.
OWNER_FLOW = r"""
await start();
const out = {};
out.before = { buttons: pageButtons(), modal: A.modalOpen, blob: blob() };
const flow = press(restartButton); await step();
out.prompted = { modal: A.modalOpen, onScreen: modalOnScreen(), restarts: restarts(),
                 note: underwayNote(), buttons: pageButtons() };
await h.type(password, __PW);
// The household-token fetch that follows a sign-in is held: the server has
// accepted the password, the rest of the sign-in has not happened yet.
w.__hold['GET /api/auth/device-token'] = 1;
const toldBefore = w.__hostTold;
const signingIn = press(modalButton('log in')); await step();
out.signedIn = { modal: A.modalOpen, onScreen: modalOnScreen(), err: modalErr(), bearer: A.isLoggedIn(),
                 restarts: restarts() };
out.hostToldAtClose = w.__hostTold - toldBefore;
w.__release(); await signingIn; await step();
// The replay went out: the server took it and is going away.
await pumpUntil(() => underwayNote());
out.away = { modal: A.modalOpen, onScreen: modalOnScreen(), err: modalErr(), restarts: restarts(),
             note: underwayNote(), buttons: pageButtons(), toasts: toasts(), phase: w.__srv.phase };
letBack();
const result = await flow; await step();
out.after = { result, modal: A.modalOpen, onScreen: modalOnScreen(), err: modalErr(), note: underwayNote(),
              buttons: pageButtons(), toasts: toasts(), blob: blob(), restarts: restarts(), logins: logins(),
              versionReads: w.__fetches.filter((f) => f.path === '/api/config/version').map((f) => f.phase) };
const replayAt = w.__fetches.findIndex((f) => f.method === 'POST' && f.path === '/api/config/version/restart' && f.who === 'bearer');
out.events = w.__events; out.replayAt = replayAt;
return out;
"""

SCENARIOS["owner_flow"] = scenario(OWNER_FLOW.replace("__PW", json.dumps(PASSWORD)))
# The server cuts the replayed restart's own answer off.
SCENARIOS["owner_flow_cut"] = scenario(OWNER_FLOW.replace("__PW", json.dumps(PASSWORD)), cut_replay=True)

# On the way back up, the server answers 401 to every read for a while; a
# read nobody pressed is sent meanwhile, and again once the restart is over.
SCENARIOS["reads_refused_while_restarting"] = scenario(
    r"""
    await start();
    const out = {};
    const flow = press(restartButton); await step();
    await h.type(password, __PW);
    await press(modalButton('log in')); await step();
    await pumpUntil(() => underwayNote());
    letBack();
    await pumpUntil(() => w.__srv.phase === 'halfup');
    await step();
    let during = null;
    try { await w.apiGet('/api/broken'); } catch (e) { during = { status: e.status, loginPrompted: !!e.loginPrompted }; }
    await step();
    out.halfUp = { modal: A.modalOpen, onScreen: modalOnScreen(), err: modalErr(), during, note: underwayNote() };
    letUp();
    const result = await flow; await step();
    out.after = { result, modal: A.modalOpen, toasts: toasts(), buttons: pageButtons(),
                  refusedPolls: w.__fetches.filter((f) => f.path === '/api/config/version' && f.phase === 'halfup').length };
    out.eventsDuring = w.__events.slice();
    // The window is over: the same refusal asks for a sign-in again.
    try { await w.apiGet('/api/broken'); } catch (e) { /* the prompt is the point */ }
    await step();
    out.afterWindow = { modal: A.modalOpen };
    return out;
    """.replace("__PW", json.dumps(PASSWORD)),
    halfup_polls=10**9,
)

# The restart is under way and the server is away; somebody opens the sign-in
# anyway (Settings' "sign in again") and tries it.
SCENARIOS["sign_in_while_restarting"] = scenario(
    r"""
    await start();
    const out = {};
    const flow = press(restartButton); await step();
    await h.type(password, __PW);
    await press(modalButton('log in')); await step();
    await pumpUntil(() => underwayNote());
    A.openModal(); await step();
    out.opened = { modal: A.modalOpen, hint: modalHint('login-restarting'), pw: pwValue(), err: modalErr() };
    await h.type(password, __PW);
    await press(modalButton('log in')); await step();
    out.tried = { modal: A.modalOpen, err: modalErr(), logins: logins() };
    await press(modalButton('cancel')); await step();
    letBack();
    const result = await flow; await step();
    out.after = { result, modal: A.modalOpen, toasts: toasts(), buttons: pageButtons() };
    return out;
    """.replace("__PW", json.dumps(PASSWORD)),
)

# A read sent under the cookie is still out when the operator signs in; it is
# refused for the cookie it carried and lands after the sign-in.
SCENARIOS["stale_refusal"] = scenario(
    r"""
    w.__hold['GET /api/needs-bearer'] = 1;
    await start();
    const before = { view: h.text().join('|'), modal: A.modalOpen };
    await A.login(__PW); await step();
    const signedIn = { modal: A.modalOpen };
    w.__release(); await step(); await step();
    return { before, signedIn, after: { view: h.text().join('|'), modal: A.modalOpen, events: w.__events },
             reads: w.__fetches.filter((f) => f.path === '/api/needs-bearer').map((f) => f.who) };
    """.replace("__PW", json.dumps(PASSWORD)),
    component=PROBE,
)
# The same, read through useApiList.
SCENARIOS["stale_refusal_list"] = dict(SCENARIOS["stale_refusal"], component=PROBE_LIST)

# A prompt opens AGAIN while the sign-in is still finishing (the household
# token fetch is out): it is a new prompt, and neither the sign-in's tail nor
# the first form's close takes it down. The restart, once the server takes
# it, does — nothing can check a password on a server on its way down.
SCENARIOS["prompt_reopened_during_sign_in"] = scenario(
    r"""
    await start();
    const out = {};
    const flow = press(restartButton); await step();
    await h.type(password, __PW);
    w.__hold['GET /api/auth/device-token'] = 1;
    w.__hold['POST /api/config/version/restart'] = 1;
    const signingIn = press(modalButton('log in')); await step();
    const seq1 = A.modalSeq;
    out.signedIn = { modal: A.modalOpen };
    A.requestLogin(); await step();
    out.reopened = { modal: A.modalOpen, seqMoved: A.modalSeq !== seq1, pw: pwValue(), err: modalErr() };
    w.__release(); await signingIn; await step();
    out.afterSignIn = { modal: A.modalOpen, onScreen: modalOnScreen(), pw: pwValue(),
                        heldRestart: w.__held.map((x) => x.key) };
    w.__release(); await step();
    await pumpUntil(() => underwayNote());
    out.underway = { modal: A.modalOpen, onScreen: modalOnScreen(), note: underwayNote() };
    letBack();
    const result = await flow; await step();
    out.after = { result, modal: A.modalOpen, toasts: toasts() };
    return out;
    """.replace("__PW", json.dumps(PASSWORD)),
)

# Once the server is back, the poll's read and then the card's own re-read
# answer; the second is held. Until it lands the button must not offer the
# restart again on the version the card read before it.
SCENARIOS["no_restart_offer_before_the_re_read"] = scenario(
    r"""
    await start();
    const flow = press(restartButton); await step();
    await h.type(password, __PW);
    await press(modalButton('log in')); await step();
    await pumpUntil(() => underwayNote());
    w.__hold['GET /api/config/version'] = 2;
    letBack();
    await pumpUntil(() => w.__held.length === 1);
    w.__release(); await step();
    await pumpUntil(() => w.__held.length === 1);
    const during = { buttons: pageButtons(), held: w.__held.map((x) => x.key) };
    w.__release();
    const result = await flow; await step();
    return { during, after: { result, buttons: pageButtons(), toasts: toasts() } };
    """.replace("__PW", json.dumps(PASSWORD)),
)

# A 2xx sign-in answer that carries no session signed nobody in.
SCENARIOS["answer_without_a_session"] = scenario(
    r"""
    await start();
    A.openModal(); await step();
    await h.type(password, '__no_token__');
    await press(modalButton('log in')); await step();
    return { modal: A.modalOpen, onScreen: modalOnScreen(), err: modalErr(), bearer: A.isLoggedIn() };
    """,
)

# What Auth.login() rejects with when the server is not there.
SCENARIOS["login_unreachable"] = scenario(
    r"""
    await start();
    w.__srv.phase = 'down';
    let e = null;
    try { await A.login(__PW); } catch (x) { e = x; }
    return { status: e && e.status, unreachable: !!(e && e.unreachable), bearer: A.isLoggedIn() };
    """.replace("__PW", json.dumps(PASSWORD)),
)

# A genuine failure: the wrong password, then the right one.
SCENARIOS["wrong_password"] = scenario(
    r"""
    await start();
    const out = {};
    const flow = press(restartButton); await step();
    await h.type(password, 'not-the-password');
    await press(modalButton('log in')); await step();
    out.wrong = { modal: A.modalOpen, err: modalErr(), restarts: restarts(), bearer: A.isLoggedIn(),
                  note: underwayNote(), toasts: toasts() };
    await h.type(password, __PW);
    await press(modalButton('log in')); await step();
    out.right = { modal: A.modalOpen, onScreen: modalOnScreen(), err: modalErr() };
    letBack();
    const result = await flow; await step();
    out.after = { result, restarts: restarts(), toasts: toasts(), buttons: pageButtons() };
    return out;
    """.replace("__PW", json.dumps(PASSWORD)),
)

# The server is down BEFORE the sign-in (not a restart this page asked for).
SCENARIOS["down_before_sign_in"] = scenario(
    r"""
    await start();
    const out = {};
    const flow = press(restartButton); await step();
    w.__srv.phase = 'down';
    await h.type(password, __PW);
    await press(modalButton('log in')); await step();
    out.down = { modal: A.modalOpen, err: modalErr(), restarts: restarts(), bearer: A.isLoggedIn(),
                 note: underwayNote(), hint: modalHint('login-restarting') };
    w.__srv.phase = 'up';
    await press(modalButton('log in')); await step();
    out.back = { modal: A.modalOpen, onScreen: modalOnScreen(), err: modalErr() };
    letBack();
    const result = await flow; await step();
    out.after = { result, restarts: restarts(), toasts: toasts(), buttons: pageButtons() };
    return out;
    """.replace("__PW", json.dumps(PASSWORD)),
)

# Enter pressed twice in the password field while the first sign-in is out.
SCENARIOS["enter_twice"] = scenario(
    r"""
    await start();
    const flow = press(restartButton); await step();
    await h.type(password, __PW);
    w.__hold['POST /api/auth/login'] = 1;
    const field = h.find(password);
    const a = field.props.onKeyDown({ key: 'Enter', preventDefault() {} });
    const b = field.props.onKeyDown({ key: 'Enter', preventDefault() {} });
    await step();
    const sent = logins();
    w.__release(); await a; await b; await step();
    letBack();
    const result = await flow; await step();
    return { sent, logins: logins(), restarts: restarts(), result, modal: A.modalOpen };
    """.replace("__PW", json.dumps(PASSWORD)),
)

# A prompt that comes back is a fresh form: the last one's typing and error
# do not come with it.
SCENARIOS["fresh_form"] = scenario(
    r"""
    await start();
    A.openModal(); await step();
    const seq1 = A.modalSeq;
    await h.type(password, 'not-the-password');
    await press(modalButton('log in')); await step();
    const first = { pw: pwValue(), err: modalErr() };
    A.closeModal(); A.openModal(); await step();
    return { first, again: { pw: pwValue(), err: modalErr(), modal: A.modalOpen },
             seqMoved: A.modalSeq !== seq1 };
    """,
)

# The Plugins page's restart card is the same restart.
SCENARIOS["plugins_card"] = scenario(
    r"""
    await start();
    const out = {};
    const btn = (e) => e.type === 'button' && e.text.includes('Restart to finish the upgrade');
    await pumpUntil(() => !!h.find(btn));
    const flow = press(btn); await step();
    out.prompted = { modal: A.modalOpen };
    await h.type(password, __PW);
    w.__hold['GET /api/auth/device-token'] = 1;
    const signingIn = press(modalButton('log in')); await step();
    out.signedIn = { modal: A.modalOpen, onScreen: modalOnScreen() };
    w.__release(); await signingIn; await step();
    await pumpUntil(() => underwayNote());
    out.away = { modal: A.modalOpen, note: underwayNote(), err: modalErr() };
    letBack();
    const result = await flow; await step(); await step();
    out.after = { result, modal: A.modalOpen, toasts: toasts(), settled: w.__settled, restarts: restarts(),
                  card: !!h.find(btn) };
    return out;
    """.replace("__PW", json.dumps(PASSWORD)),
    component=PLUGIN_CARD, files=PLUGIN_FILES,
)


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("restart-sign-in") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), "@" + str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    return _Outcomes(json.loads(proc.stdout))


class _Outcomes(dict):
    """A scenario whose script threw fails the tests that read IT, with
    the harness's own error, and no other."""

    def __getitem__(self, name):
        out = super().__getitem__(name)
        if isinstance(out, dict) and "__harness_error" in out:
            pytest.fail(f"{name}: {out['__harness_error']}")
        return out


RESTARTED = f"restarted — now running {NEW_SHA}"


# ─── the owner's flow ────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["owner_flow", "owner_flow_cut"])
def test_the_view_only_tab_is_asked_to_sign_in_for_the_restart(driven, name) -> None:
    o = driven[name]
    assert "Restart to apply changes" in o["before"]["buttons"]
    assert o["before"]["modal"] is False
    # The restart was refused for the cookie, and the modal asks.
    assert o["prompted"]["modal"] is True and o["prompted"]["onScreen"] is True
    assert o["prompted"]["restarts"] == ["cookie"]
    assert o["prompted"]["note"] is False


@pytest.mark.parametrize("name", ["owner_flow", "owner_flow_cut"])
def test_the_modal_comes_down_as_soon_as_the_password_is_accepted(driven, name) -> None:
    """The household-token fetch after the sign-in is still out, and the
    restart has not been replayed yet: the modal is already gone. It used
    to stay up until all of that, and the replay it launched, had run."""
    o = driven[name]["signedIn"]
    assert o == {"modal": False, "onScreen": False, "err": None, "bearer": True, "restarts": ["cookie"]}
    # ...and the modal host itself was told, so a browser takes it down now.
    assert driven[name]["hostToldAtClose"] >= 1
    events = driven[name]["events"]
    # Opened once, closed once — the close before the replay was sent.
    assert [e["open"] for e in events] == [True, False]
    assert events[1]["sent"] <= driven[name]["replayAt"]


@pytest.mark.parametrize("name", ["owner_flow", "owner_flow_cut"])
def test_while_the_server_is_away_the_panel_says_restarting_and_nothing_else(driven, name) -> None:
    o = driven[name]["away"]
    assert o["modal"] is False and o["onScreen"] is False and o["err"] is None
    assert o["restarts"] == ["cookie", "bearer"]          # replayed exactly once, with the Bearer
    assert o["note"] is True
    assert "Updating… [disabled]" in o["buttons"]
    assert "updating…" in o["toasts"]
    assert not any("fail" in t.lower() for t in o["toasts"]), o["toasts"]


@pytest.mark.parametrize("name", ["owner_flow", "owner_flow_cut"])
def test_the_panel_shows_the_new_version_once_the_server_answers(driven, name) -> None:
    o = driven[name]["after"]
    assert o["result"] is True
    assert o["modal"] is False and o["onScreen"] is False and o["err"] is None
    assert RESTARTED in o["toasts"]
    assert not any("fail" in t.lower() for t in o["toasts"]), o["toasts"]
    assert NEW_SHA in o["blob"] and OLD_SHA not in o["blob"]
    assert o["note"] is False
    assert "Check for updates" in o["buttons"]
    assert o["restarts"] == ["cookie", "bearer"] and o["logins"] == 1
    # The poll knocked while the server was away, and stopped once it answered.
    assert o["versionReads"].count("down") >= 1
    assert o["versionReads"][-1] == "up"


# ─── nothing prompts while the server is coming back ─────────────────────


def test_reads_refused_while_the_server_comes_back_open_no_prompt(driven) -> None:
    """The restart's poll — and a read nobody pressed — are refused 401
    while the server is half up. Neither asks for a sign-in: the old poll
    opened a fresh login modal over the restart, and a password typed into
    it went to a server on its way down."""
    o = driven["reads_refused_while_restarting"]
    assert o["halfUp"]["modal"] is False and o["halfUp"]["onScreen"] is False
    assert o["halfUp"]["during"] == {"status": 401, "loginPrompted": False}
    assert o["halfUp"]["note"] is True
    assert o["after"]["refusedPolls"] >= 1
    assert o["after"]["result"] is True
    assert RESTARTED in o["after"]["toasts"]
    # Only the sign-in for the restart opened and closed the modal.
    assert [e["open"] for e in o["eventsDuring"]] == [True, False]


def test_the_quiet_window_ends_with_the_restart(driven) -> None:
    o = driven["reads_refused_while_restarting"]
    assert o["afterWindow"]["modal"] is True


def test_a_sign_in_tried_while_the_server_restarts_says_so(driven) -> None:
    o = driven["sign_in_while_restarting"]
    # Opened by hand mid-restart: a fresh form that says what is going on.
    assert o["opened"] == {"modal": True, "hint": True, "pw": "", "err": None}
    assert o["tried"]["modal"] is True
    assert o["tried"]["err"].startswith("The Domovoi server is restarting"), o["tried"]["err"]
    assert "Failed to fetch" not in o["tried"]["err"]
    assert o["after"]["result"] is True
    assert RESTARTED in o["after"]["toasts"]


@pytest.mark.parametrize("name", ["stale_refusal", "stale_refusal_list"])
def test_a_read_refused_for_the_credential_a_sign_in_replaced_is_read_again(driven, name) -> None:
    o = driven[name]
    assert o["before"]["modal"] is False
    assert o["signedIn"]["modal"] is False
    # Refused for the cookie it carried, AFTER the sign-in: no prompt, and
    # the read runs again under the Bearer.
    assert o["after"]["modal"] is False
    assert o["after"]["events"] == []
    assert o["after"]["view"] == "bearer"
    assert o["reads"] == ["cookie", "bearer"]


def test_a_prompt_opened_again_during_the_sign_in_is_left_alone(driven) -> None:
    """The first form came down when the password was accepted; a prompt
    that opens while the household-token fetch is still out is a NEW one
    (a fresh form), and neither the end of that sign-in nor the first
    form's own close takes it down."""
    o = driven["prompt_reopened_during_sign_in"]
    assert o["signedIn"] == {"modal": False}
    assert o["reopened"] == {"modal": True, "seqMoved": True, "pw": "", "err": None}
    assert o["afterSignIn"]["modal"] is True and o["afterSignIn"]["onScreen"] is True
    assert o["afterSignIn"]["pw"] == ""
    # The replayed restart went out under the sign-in that finished.
    assert o["afterSignIn"]["heldRestart"] == ["POST /api/config/version/restart"]


def test_a_prompt_still_standing_comes_down_when_the_restart_is_under_way(driven) -> None:
    o = driven["prompt_reopened_during_sign_in"]
    assert o["underway"] == {"modal": False, "onScreen": False, "note": True}
    assert o["after"]["result"] is True and o["after"]["modal"] is False
    assert RESTARTED in o["after"]["toasts"]


def test_the_button_does_not_offer_the_restart_again_before_the_re_read(driven) -> None:
    o = driven["no_restart_offer_before_the_re_read"]
    assert o["during"]["held"] == ["GET /api/config/version"]
    assert "Restart to apply changes" not in o["during"]["buttons"], o["during"]["buttons"]
    assert "Updating… [disabled]" in o["during"]["buttons"]
    assert o["after"]["result"] is True
    assert "Check for updates" in o["after"]["buttons"]
    assert RESTARTED in o["after"]["toasts"]


def test_a_sign_in_answer_without_a_session_stays_in_the_modal(driven) -> None:
    o = driven["answer_without_a_session"]
    assert o == {"modal": True, "onScreen": True, "bearer": False,
                 "err": "the server answered without a session — try again"}


def test_a_sign_in_to_a_server_that_is_not_there_rejects_as_unreachable(driven) -> None:
    assert driven["login_unreachable"] == {"status": 0, "unreachable": True, "bearer": False}


# ─── a real failure still shows in the modal ─────────────────────────────


def test_a_wrong_password_stays_in_the_modal_and_restarts_nothing(driven) -> None:
    o = driven["wrong_password"]
    assert o["wrong"]["modal"] is True
    assert o["wrong"]["err"] == "wrong password"
    assert o["wrong"]["restarts"] == ["cookie"]
    assert o["wrong"]["bearer"] is False
    assert o["wrong"]["note"] is False
    assert o["wrong"]["toasts"] == []
    # Then the right one: the modal goes, the restart runs.
    assert o["right"] == {"modal": False, "onScreen": False, "err": None}
    assert o["after"]["result"] is True
    assert o["after"]["restarts"] == ["cookie", "bearer"]
    assert RESTARTED in o["after"]["toasts"]


def test_a_server_down_before_the_sign_in_says_so_in_words(driven) -> None:
    o = driven["down_before_sign_in"]
    assert o["down"]["modal"] is True
    assert o["down"]["err"] == "Couldn’t reach the Domovoi server — check that it’s running, then try again."
    assert o["down"]["restarts"] == ["cookie"]
    assert o["down"]["bearer"] is False
    # Nothing this page asked for is restarting the server.
    assert o["down"]["note"] is False and o["down"]["hint"] is False
    # Back up: the same modal signs in, and the restart goes ahead.
    assert o["back"] == {"modal": False, "onScreen": False, "err": None}
    assert o["after"]["result"] is True
    assert o["after"]["restarts"] == ["cookie", "bearer"]


# ─── one sign-in per press, and a fresh form each time ───────────────────


def test_enter_twice_signs_in_once(driven) -> None:
    o = driven["enter_twice"]
    assert o["sent"] == 1
    assert o["logins"] == 1
    assert o["restarts"] == ["cookie", "bearer"]
    assert o["result"] is True and o["modal"] is False


def test_a_prompt_that_comes_back_is_a_fresh_form(driven) -> None:
    o = driven["fresh_form"]
    assert o["first"] == {"pw": "not-the-password", "err": "wrong password"}
    assert o["again"] == {"pw": "", "err": None, "modal": True}
    assert o["seqMoved"] is True


# ─── the Plugins page's restart is the same restart ─────────────────────


def test_the_plugins_restart_card_signs_in_and_waits_the_same_way(driven) -> None:
    o = driven["plugins_card"]
    assert o["prompted"]["modal"] is True
    assert o["signedIn"] == {"modal": False, "onScreen": False}
    assert o["away"] == {"modal": False, "note": True, "err": None}
    assert o["after"]["result"] is True and o["after"]["modal"] is False
    assert o["after"]["restarts"] == ["cookie", "bearer"]
    assert o["after"]["settled"] >= 1
    assert RESTARTED in o["after"]["toasts"]
    # The upgrade is loaded: nothing left for the card to offer.
    assert o["after"]["card"] is False
