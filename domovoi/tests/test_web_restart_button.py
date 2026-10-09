"""Settings -> Configuration -> Version: "Restart Domovoi", the restart an
admin can press whenever nothing is waiting to load.

OWNER REQUEST (2026-10-05): after hand-editing domovoi/.env (an
ACOUSTID_API_KEY) there was nowhere in the dashboard to restart. The Version
card offered "Restart to apply changes" only while pulled code or a staged
plugin upgrade was waiting, so the owner had to ssh in and run
`sudo systemctl restart domovoi-core`.

What this module pins, driven through ``jsx_interact_harness.js`` on top of
the REAL auth.js, data.js, components.jsx and settings.jsx. Only ``fetch``
and ``window.confirm`` are scripted: PRELUDE plays a server that answers by
the credential a request carried, accepts a restart from a Bearer, may keep
answering as the OLD server for a few reads (the bounce fires a second after
the answer), then GOES AWAY (every request rejected the way a browser rejects
one to a dead socket) until the scenario lets it back on a fresh start:

* who sees it: an admin signed in here, and a view-only tab (a reload kept
  the cookie; its press asks for the password once, like every restart);
  never a browser with no admin sign-in at all;
* never two restart buttons: while "Restart to apply changes" is offered,
  "Restart Domovoi" is not;
* a host that can't restart itself gets the command to run instead (the
  core's restart_command), and a host without systemd gets no systemctl
  line at all;
* the confirm says what a restart interrupts, and "cancel" sends nothing;
* the press is the dashboard's one restart (restartDomovoiServer): the card
  says "Restarting…" and shows the underway note while the server is away,
  then "restarted — now running <sha>" — with the update unit too, where it
  is that unit's quick plain restart, never "Updating…"; the check and the
  pull are greyed meanwhile;
* the wait ends on a NEW server: not on the old one still answering after
  the press, and not on a panel copy read before a restart done some other
  way (both looked "restarted" at once: a plain restart never sets
  restart_required, so its clearing proves nothing). A moved boot time is
  enough on its own (a host back before any poll found it gone); from a
  core that can't say when it started, having been seen away is;
* a view-only press signs in and the restart is replayed once, with the
  Bearer;
* a plain restart the update unit records as failed reports the unit's
  error;
* a press the update unit would run as its FULL update is asked and told
  as that. apply-update.sh compares the checkout with the commit it last
  applied, so two presses meet the full update: code pulled after the panel
  read the version (only the fresh read at the press shows it, so a second
  confirm asks), and code loaded outside the unit (pulled, then the core
  restarted by hand: when the panel's copy shows it, the one confirm says
  so). Either gets the update's words, "Updating…" (from the yes to the
  update until the card has read the version again), the update note and
  the unit's 15-minute wait; a second confirm declined sends nothing and
  re-reads the panel, and one that stood while the server changed is
  measured against the server after it. A rolled-back last run, a unit
  without a readable result, a staged plugin upgrade, a checkout the core
  can't read and a host without the unit stay the quick restart; untracked
  files (a "-dirty" checkout) change neither answer. "Restart to apply
  changes" keeps its own confirm, and a restart whose answer is cut off is
  told and waited for as what runs.

* one restart at a time (OWNER REPORT 2026-10-09: pressed "Restart to apply
  changes", reloaded, and the card offered the restart again while the
  update unit was still running): a card that loads while the server says
  a restart is under way (restart_in_progress) offers nothing, one greyed
  "Updating…" (or "Restarting…" for the unit's quick restart) and the
  underway note, follows that run quietly through the server going away,
  and reports how it ended; a press whose fresh read finds one under way,
  or that the server refuses for one (in_progress), follows it instead of
  asking for another.

The scripted unit runs its plain restart or its full update by the same
comparison (``applied`` against the checkout) and records which, so every
flow also says what the "server" did.

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

SHA = "5b7aa8a"
PULLED = "6c4de76"
# The commit the update unit applied before SHA (the Beelink's last update
# was 5196cc9 -> 5b7aa8a).
OLD = "5196cc9"
# last_update names commits in full (the core nulls anything else).
FULL = {
    SHA: "5b7aa8a995b0f9edfeccba25a078121d3fdd4e55",
    PULLED: "6c4de763406fcc73e85ff792fc69d2e6052cc554",
    OLD: "5196cc994de0592b69bbc1170ef475e28d976843",
}
PASSWORD = "right-password"
STARTED = 1791247053.25
RESTARTED_AT = 1791250000.5
# The core restarted by hand, after the panel read the version.
HAND_RESTARTED_AT = 1791249000.75
LINUX_RESTART = "sudo systemctl restart domovoi-core domovoi-web"
LINUX_UPDATE = "sudo systemctl start domovoi-update.service"

# ─── the in-sandbox prelude: a server that restarts ──────────────────────

PRELUDE = r"""
const __st = setTimeout;
// The restart's poll waits 2 s between reads in a browser; here it waits a
// couple of milliseconds. Every other timer keeps its length and does not
// keep node alive once the scenarios are done.
//
// clockStep: how far each poll's wait moves the page's clock. By default
// the 2 s it takes in a browser, so a wait reaches its deadline after as
// many polls as it would there (a quick restart's 90 s after 45, the update
// unit's 15 minutes after 450), a few milliseconds here: a press that goes
// wrong ends instead of holding node for the real 15 minutes.
const __realNow = Date.now.bind(Date);
let __skew = 0;
Date.now = () => __realNow() + __skew;
setTimeout = (fn, ms, ...a) => {
  const poll = Number(ms) === 2000;
  if (poll && window.__srv && window.__srv.clockStep) __skew += window.__srv.clockStep;
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

// Every confirm() the page asks, and what the operator answers: the
// __confirmAnswers queue in order, then __confirmAnswer. __whileAsking(n)
// is what happens on the server while the n-th question stands.
window.__confirms = [];
window.__confirmAnswer = true;
window.__confirmAnswers = [];
window.__whileAsking = null;
window.confirm = (msg) => {
  window.__confirms.push(String(msg));
  if (window.__whileAsking) window.__whileAsking(window.__confirms.length);
  return window.__confirmAnswers.length ? window.__confirmAnswers.shift() : window.__confirmAnswer;
};

// phase: 'up' answers; 'linger' is the OLD server still answering after it
// accepted the restart (lingerPolls version reads); 'down' rejects every
// request at the socket, as a browser does for a server that is not there.
// A restart moves up -> linger|down on the request AFTER it answered; the
// poll walks it back once a scenario lets it (downPolls), on a fresh start.
// applied: the commit the update unit last applied (apply-update.sh's
// applied_sha), the running one unless a scenario says otherwise. plugins:
// upgrades staged for the next restart.
// busy: a restart already under way that this page did not ask for (the
// update unit started before a reload, from another tab, by hand). Version
// reads say so (restart_in_progress) until a scenario sets busyPolls to 0;
// then the server is away for busyDownPolls reads and back on a fresh
// start. A restart asked for meanwhile is refused (in_progress), and so is
// one with busyOnPost (another tab's press got there first), which makes
// the server busy from then on.
window.__srv = Object.assign({
  sessions: new Set(__SESSIONS), cookie: __COOKIE, n: 0, device: 'house-token',
  phase: 'up', goDown: false, restarted: false, lingerPolls: 0, downPolls: 10 ** 9,
  run: 'run-1', last: null, newRunStatus: 'ok', newRunError: null, plugins: [], clockStep: 2000,
  busy: false, busyPolls: 10 ** 9, busyDownPolls: 0, busyOnPost: false,
}, __SRV);
if (window.__srv.applied === undefined) window.__srv.applied = window.__srv.running;
// A commit as the update unit names it: HEAD's, whatever the tree holds.
// The core's "-dirty" (git status --porcelain, untracked files too) is
// about the working tree, not the commit.
const __commit = (sha) => String(sha || '').replace(/-dirty$/, '');
const __full = (sha) => (sha && __FULLS[__commit(sha)]) || __commit(sha) || sha;
window.__fetches = [];
const __answer = (status, body) => {
  const text = body == null ? '' : JSON.stringify(body);
  return { ok: status >= 200 && status < 300, status, statusText: String(status),
           text: async () => text, json: async () => JSON.parse(text) };
};
const __ADMIN_401 = { detail: 'admin session required' };
const __version = () => {
  const s = window.__srv;
  const pending = s.running !== s.checkout;
  const v = { sha: s.running, running_sha: s.running, checkout_sha: s.checkout,
              restart_required: pending || s.plugins.length > 0, code_restart_required: pending,
              plugins_pending_restart: s.plugins,
              restart_capable: s.capable, restart_mode: s.mode, restart_hint: s.hint,
              started_at: s.started, uptime_sec: 7200, restart_in_progress: !!s.busy,
              last_update: s.mode === 'update' ? s.last : null, bad_sha: null };
  if (s.command !== undefined) v.restart_command = s.command;
  return v;
};
// Back on a fresh start: a new boot time, the checkout and the staged
// plugin upgrades loaded, and with the update unit the result of the run
// the restart started. That run is apply-update.sh's choice: from the
// commit it last applied to the checkout, its plain restart ("restart")
// when those are one commit and its full update ("update") when not.
const __comeBack = () => {
  const s = window.__srv;
  s.running = s.checkout;
  s.plugins = [];
  // noStart: a core that can't say when it started (its boot capture never ran).
  s.started = s.noStart ? null : __RESTARTED_AT;
  if (s.mode === 'update') {
    const from = s.applied;
    s.run = 'run-2';
    s.last = { status: s.newRunStatus, mode: __commit(from) === __commit(s.checkout) ? 'restart' : 'update',
               started_at: s.run, finished_at: '2026-10-05T23:59:00Z', from_sha: __full(from),
               to_sha: __full(s.checkout), prev_source: 'applied', error: s.newRunError };
    if (s.newRunStatus === 'ok') s.applied = s.checkout;
  }
};
const __route = (method, path, c, body) => {
  const srv = window.__srv;
  const admin = c.who === 'bearer' || c.who === 'cookie';
  const bare = path.split('?')[0];
  if (method === 'POST' && bare === '/api/auth/login') {
    let pw = null;
    try { pw = JSON.parse(body || '{}').password; } catch {}
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
    if (srv.busy || srv.busyOnPost) {
      srv.busy = true;
      return __answer(200, { ok: false, in_progress: true, mode: srv.mode, delay_sec: null,
                             error: 'a restart or update is already under way; wait for it to finish',
                             units: srv.mode === 'update' ? ['domovoi-update.service']
                               : ['domovoi-core.service', 'domovoi-web.service'] });
    }
    srv.restarted = true;
    srv.goDown = true;
    return __answer(200, { ok: true, mode: srv.mode, delay_sec: 1, error: null,
                           units: srv.mode === 'update' ? ['domovoi-update.service']
                             : ['domovoi-core.service', 'domovoi-web.service'] });
  }
  if (method === 'POST' && bare === '/api/config/version/check') {
    return __answer(200, { upstream: true, behind: srv.behind || 0, ahead: 0, upstream_sha: null, error: null });
  }
  if (method !== 'GET') return __answer(404, { detail: 'not found' });
  if (bare === '/api/auth/status') return __answer(200, { setup_complete: true, authenticated: admin });
  if (bare === '/api/auth/device-token') {
    return admin ? __answer(200, { token: srv.device, header: 'X-Device-Token' }) : __answer(401, __ADMIN_401);
  }
  if (bare === '/api/config') return __answer(200, { bot_name: 'Domovoi', web_version: 'test' });
  if (bare === '/api/config/version') return __answer(200, __version());
  return __answer(404, { detail: 'not found' });
};
// __onVersionRead(): called as each version read is SENT (a scenario that
// traces what the card shows at those moments).
window.__onVersionRead = null;
fetch = async (url, opts) => {
  const o = opts || {};
  const method = String(o.method || 'GET').toUpperCase();
  const path = String(url).replace(/^https?:\/\/[^/]+/, '');
  const bare = path.split('?')[0];
  const hd = o.headers || {};
  const srv = window.__srv;
  if (method === 'GET' && bare === '/api/config/version' && window.__onVersionRead) window.__onVersionRead();
  if (srv.busy && method === 'GET' && bare === '/api/config/version') {
    if (srv.busyPolls > 0) {
      srv.busyPolls -= 1;
    } else if (srv.busyDownPolls > 0) {
      srv.busyDownPolls -= 1;
      window.__fetches.push({ method, path, who: 'busy', phase: 'down', started: srv.started });
      throw new TypeError('Failed to fetch');
    } else {
      srv.busy = false;
      __comeBack();
    }
  }
  if (srv.goDown) { srv.goDown = false; srv.phase = srv.lingerPolls > 0 ? 'linger' : 'down'; }
  const auth = String(hd.Authorization || '');
  const bearer = auth.startsWith('Bearer ') ? auth.slice(7) : null;
  const who = bearer ? (srv.sessions.has(bearer) ? 'bearer' : 'invalid')
    : (srv.cookie && srv.sessions.has(srv.cookie) ? 'cookie' : 'none');
  window.__fetches.push({ method, path, who, phase: srv.phase, started: srv.started });
  // cutAnswer: the server takes the restart and is gone before its answer
  // arrives (the bounce cut it off): the request fails the way a browser
  // fails one to a socket that closed, and the server is down.
  if (srv.cutAnswer && srv.phase === 'up' && method === 'POST' && bare === '/api/config/version/restart'
      && who === 'bearer') {
    srv.restarted = true; srv.phase = 'down';
    throw new TypeError('Failed to fetch');
  }
  if (srv.phase === 'linger' && method === 'GET' && bare === '/api/config/version') {
    srv.lingerPolls -= 1;
    const answer = __route(method, path, { who, bearer }, o.body);
    // skipDown: a fast host, back on a fresh start before any poll found
    // the old server gone.
    if (srv.lingerPolls <= 0) {
      if (srv.skipDown) { srv.phase = 'up'; __comeBack(); } else srv.phase = 'down';
    }
    return answer;
  }
  if (srv.phase === 'down') {
    if (method === 'GET' && bare === '/api/config/version' && srv.restarted) {
      srv.downPolls -= 1;
      if (srv.downPolls <= 0) { srv.phase = 'up'; __comeBack(); }
    }
    throw new TypeError('Failed to fetch');
  }
  return __route(method, path, { who, bearer }, o.body);
};
window.__flush = async (h, n = 8) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
window.__cls = (e) => String((e.props && e.props.className) || '').split(' ');
"""

VERSION = ("(window.__Auth = Auth, function VersionWithModal() { return React.createElement("
           "React.Fragment, null, React.createElement(VersionSection), React.createElement(AuthModalHost)); })")

# restartDomovoiServer pressed by a bare button, with the panel's copy of
# the version passed in as it was read at mount (window.__staleCore).
DIRECT = ("(window.__Auth = Auth, function DirectRestart() {"
          " const [fire, node] = useToast();"
          " return React.createElement(React.Fragment, null,"
          "  React.createElement('button', { onClick: () => { window.__result = null;"
          "   restartDomovoiServer({ core: window.__staleCore, fire, plain: true })"
          "    .then((r) => { window.__result = r; }); } }, 'press'),"
          "  node); })")

HELPERS = r"""
const w = h.global('window'); const A = w.__Auth;
const step = () => w.__flush(h);
const inModal = (e) => h.inside(e, (a) => w.__cls(a).includes('cal-modal'));
const modalButton = (label) => (e) => e.type === 'button' && e.text.trim() === label
  && h.inside(e, (a) => w.__cls(a).includes('cal-modal-foot'));
const password = (e) => e.type === 'input' && e.props.type === 'password';
const pageButtons = () => h.findAll({ type: 'button' }).filter((e) => !inModal(e))
  .map((e) => e.text.trim() + (e.props.disabled ? ' [disabled]' : ''));
const toasts = () => h.findAll((x) => x.type === 'span' && x.text
  && h.inside(x, (a) => a.props && a.props.title === 'dismiss')).map((x) => x.text);
const deep = (n) => {
  if (n == null || typeof n === 'boolean') return '';
  if (typeof n === 'string' || typeof n === 'number') return String(n);
  if (Array.isArray(n)) return n.map(deep).join('');
  return n.props ? deep(n.props.children) : '';
};
const byClass = (cls) => h.findAll((e) => w.__cls(e).includes(cls));
const note = () => { const e = byClass('restart-underway')[0]; return e ? e.text : null; };
const hint = () => { const e = byClass('restart-by-hand')[0]; return e ? deep(e.props.children) : null; };
const posts = (p) => w.__fetches.filter((f) => f.method === 'POST' && f.path === p).map((f) => f.who);
const restarts = () => posts('/api/config/version/restart');
const press = (sel) => { const e = h.find(sel); if (!e) throw new Error('nothing to press'); return e.props.onClick({ preventDefault() {}, stopPropagation() {}, target: {} }); };
const plainButton = (e) => e.type === 'button' && /Restart Domovoi|Restarting…/.test(e.text) && !inModal(e);
const pumpUntil = async (cond, n = 400) => { for (let i = 0; i < n && !cond(); i++) await step(); return cond(); };
const letBack = () => { w.__srv.downPolls = 1; };
const versionReads = () => w.__fetches.filter((f) => f.method === 'GET' && f.path === '/api/config/version');
const start = async () => { h.render(); await step(); await step(); };
const view = () => ({ buttons: pageButtons(), hint: hint(), note: note(), toasts: toasts(),
                      modal: !!A.modalOpen });
"""


def scenario(script: str, *, srv: dict | None = None, cookie: str | None = "old-session",
             component: str = VERSION) -> dict:
    """``srv`` overrides the server: running/checkout SHAs, started_at,
    restart_mode, restart_capable, restart_hint, restart_command (absent
    unless given), lingerPolls, the update unit's results and the commit it
    last applied (``applied``), staged plugin upgrades, clockStep."""
    base = {"running": SHA, "checkout": SHA, "started": STARTED, "mode": "restart",
            "capable": True, "hint": None}
    if (srv or {}).get("mode") == "update":
        # The unit's last run applied SHA, the running commit.
        base["last"] = {"status": "ok", "mode": "update", "started_at": "run-1",
                        "finished_at": "2026-10-05T20:22:52Z", "from_sha": FULL[OLD], "to_sha": FULL[SHA],
                        "prev_source": "applied", "error": None}
    base.update(srv or {})
    sessions = [cookie] if cookie else []
    head = (f"const __LS = {json.dumps({'domovoi-device-token': 'house-token'})};"
            f" const __SESSIONS = {json.dumps(sessions)}; const __COOKIE = {json.dumps(cookie)};"
            f" const __PASSWORD = {json.dumps(PASSWORD)}; const __SRV = {json.dumps(base)};"
            f" const __RESTARTED_AT = {json.dumps(RESTARTED_AT)}; const __FULLS = {json.dumps(FULL)};\n")
    return {"files": FILES, "component": component, "props": {}, "setup": head + PRELUDE,
            "script": HELPERS + script}


SCENARIOS: dict[str, dict] = {}

# ─── who sees it ─────────────────────────────────────────────────────────

LOOK = r"""
await start();
return view();
"""
SIGNED_IN = r"""
await A.login(__PW);
await start();
return Object.assign(view(), { bearer: A.isLoggedIn() });
""".replace("__PW", json.dumps(PASSWORD))

SCENARIOS["admin"] = scenario(SIGNED_IN)
SCENARIOS["view_only"] = scenario(LOOK)
SCENARIOS["signed_out"] = scenario(LOOK, cookie=None)
SCENARIOS["pending_admin"] = scenario(SIGNED_IN, srv={"checkout": PULLED})
SCENARIOS["pending_update_unit_admin"] = scenario(SIGNED_IN, srv={"checkout": PULLED, "mode": "update"})
SCENARIOS["update_unit_admin"] = scenario(SIGNED_IN, srv={"mode": "update"})
# A host without the sudoers grant: the core names the command.
SCENARIOS["incapable_admin"] = scenario(SIGNED_IN, srv={
    "capable": False, "command": LINUX_RESTART,
    "hint": "no passwordless sudoers grant for systemctl restart — see the self-restart entry in docs/LINUX_HOST.md"})
SCENARIOS["incapable_update_unit_admin"] = scenario(SIGNED_IN, srv={
    "capable": False, "mode": "update", "command": LINUX_UPDATE,
    "hint": "domovoi-update.service is installed but there is no passwordless sudoers grant to start it"})
SCENARIOS["incapable_signed_out"] = scenario(LOOK, cookie=None, srv={
    "capable": False, "command": LINUX_RESTART, "hint": "no passwordless sudoers grant"})
# Windows or a development box: no systemd, no command to give.
SCENARIOS["no_systemd_admin"] = scenario(SIGNED_IN, srv={
    "capable": False, "command": None, "hint": "systemctl not found — not a systemd host"})
# A core from before restart_command: the command for its mode, as before.
SCENARIOS["older_core_incapable_admin"] = scenario(SIGNED_IN, srv={
    "capable": False, "hint": "no passwordless sudoers grant"})
SCENARIOS["no_systemd_pending_admin"] = scenario(SIGNED_IN, srv={
    "checkout": PULLED, "capable": False, "command": None,
    "hint": "systemctl not found — not a systemd host"})

# ─── the confirm ─────────────────────────────────────────────────────────

SCENARIOS["confirm_cancelled"] = scenario(r"""
await A.login(__PW);
await start();
w.__confirmAnswer = false;
const result = await press(plainButton); await step();
return Object.assign(view(), { result, confirms: w.__confirms, restarts: restarts() });
""".replace("__PW", json.dumps(PASSWORD)))

# ─── the restart itself ──────────────────────────────────────────────────

# __MEANWHILE: what happens on the server between the panel's read and the
# press. `unitRan`: what the scripted update unit ran (see __comeBack).
FLOW = r"""
await A.login(__PW);
await start();
const out = { before: view() };
__MEANWHILE
const flow = press(plainButton); await step();
await pumpUntil(() => !!note());
out.away = Object.assign(view(), { restarts: restarts(), phase: w.__srv.phase });
letBack();
out.result = await flow; await step(); await step();
out.after = view();
out.confirms = w.__confirms;
out.restarts = restarts();
out.reads = versionReads().map((f) => `${f.phase}:${f.started}`);
out.unitRan = w.__srv.mode === 'update' && w.__srv.restarted ? w.__srv.last.mode : null;
return out;
""".replace("__PW", json.dumps(PASSWORD))


def flow(meanwhile: str = "") -> str:
    return FLOW.replace("__MEANWHILE", meanwhile)


SCENARIOS["flow"] = scenario(flow())
# The update unit: its plain restart (nothing new since the last commit it
# applied) is what runs, and the wait ends on that run's result.
SCENARIOS["flow_update_unit"] = scenario(flow(), srv={"mode": "update"})
# The old server answers two more reads after taking the restart (the bounce
# fires a second after the answer).
SCENARIOS["flow_old_server_lingers"] = scenario(flow(), srv={"lingerPolls": 2})

# The update unit's plain restart that failed its health check.
SCENARIOS["flow_update_unit_failed"] = scenario(flow(), srv={
    "mode": "update", "newRunStatus": "failed",
    "newRunError": "health failed (exit 1): not healthy after 120s: core down, web up"})

# A core that can't say when it started (no started_at, before the press or
# after it): the old process answering after the press must not end the
# wait; having been seen away, the server answering again does.
SCENARIOS["flow_no_started_at"] = scenario(flow(), srv={"started": None, "noStart": True, "lingerPolls": 2})
# A fast host: the new server answers before any poll found the old one
# gone. Its boot time moved, and that alone ends the wait.
SCENARIOS["flow_back_between_polls"] = scenario(flow(), srv={"lingerPolls": 1, "skipDown": True})
# Behind the upstream, "Pull the latest" is the update action; a restart
# under way greys it, as it greys the check.
SCENARIOS["flow_pull_mode"] = scenario(r"""
await A.login(__PW);
await start();
await press({ type: 'button', text: 'Check for updates' }); await step();
await pumpUntil(() => pageButtons().some((b) => b.startsWith('Pull the latest')));
const out = { before: view() };
const flow = press(plainButton); await step();
await pumpUntil(() => !!note());
out.away = view();
letBack();
out.result = await flow; await step(); await step();
out.after = view();
out.restarts = restarts();
return out;
""".replace("__PW", json.dumps(PASSWORD)), srv={"behind": 2})

# A view-only tab: the press asks for the password, the restart is replayed.
SCENARIOS["flow_view_only"] = scenario(r"""
await start();
const out = { before: view() };
const flow = press(plainButton); await step();
out.prompted = { modal: !!A.modalOpen, restarts: restarts() };
await h.type(password, __PW);
await press(modalButton('log in')); await step();
await pumpUntil(() => !!note());
out.away = Object.assign(view(), { restarts: restarts() });
letBack();
out.result = await flow; await step(); await step();
out.after = Object.assign(view(), { restarts: restarts(), bearer: A.isLoggedIn() });
return out;
""".replace("__PW", json.dumps(PASSWORD)))

# The panel read the version long ago; the server has been restarted some
# other way since (a new started_at). The press measures against the server
# as it is NOW, so the old process answering after the press (one linger
# read) is not taken for the restart done.
SCENARIOS["stale_panel_copy"] = scenario(r"""
await A.login(__PW);
await start();
w.__staleCore = { sha: '5b7aa8a', running_sha: '5b7aa8a', checkout_sha: '5b7aa8a', restart_required: false,
                  restart_capable: true, restart_mode: 'restart', started_at: 1700000000.5 };
await step();
press({ type: 'button', text: 'press' }); await step();
await pumpUntil(() => w.__srv.phase === 'down');
await step(); await step();
const during = { result: w.__result, toasts: toasts() };
letBack();
await pumpUntil(() => w.__result !== null);
await step();
return { during, result: w.__result, toasts: toasts(), restarts: restarts(),
         reads: versionReads().map((f) => `${f.phase}:${f.started}`) };
""".replace("__PW", json.dumps(PASSWORD)), srv={"started": 1791240000.75, "lingerPolls": 1}, component=DIRECT)

# ─── a press the update unit runs as its full update ────────────────────

# (a) A pull lands after the panel read the version (another tab, the API):
# the panel still offers the quick restart, and only the fresh read at the
# press shows the pulled code waiting, which the unit applies as its full
# update.
PULL_LANDS = f"w.__srv.checkout = {json.dumps(PULLED)};"
SCENARIOS["flow_pulled_since_the_panel_read"] = scenario(flow(PULL_LANDS), srv={"mode": "update"})
# The same with a unit whose last result can't be read: nothing to compare
# with, but pulled code waiting is a full update all the same.
SCENARIOS["flow_pulled_since_without_a_unit_result"] = scenario(flow(PULL_LANDS),
                                                                srv={"mode": "update", "last": None})
# While the second question stands, the pulled code is applied some other
# way (the unit started by hand): a new run, a new process on the new code.
# The old process then answers the first poll after the press.
SCENARIOS["unit_ran_while_the_question_stood"] = scenario(flow(PULL_LANDS + r"""
w.__whileAsking = (n) => {
  if (n !== 2) return;
  const s = w.__srv;
  s.running = s.applied = s.checkout; s.started = __HAND; s.run = 'run-1b';
  s.last = { status: 'ok', mode: 'update', started_at: 'run-1b', finished_at: '2026-10-05T23:30:00Z',
             from_sha: __FROM, to_sha: __TO, prev_source: 'applied', error: null };
};""".replace("__HAND", json.dumps(HAND_RESTARTED_AT)).replace("__FROM", json.dumps(FULL[SHA]))
    .replace("__TO", json.dumps(FULL[PULLED]))), srv={"mode": "update", "lingerPolls": 1})
# ...and the operator says no to the full update.
SCENARIOS["pulled_since_declined"] = scenario(r"""
await A.login(__PW);
await start();
const before = view();
__PULL_LANDS
w.__confirmAnswers = [true, false];
const result = await press(plainButton); await step(); await step();
return Object.assign(view(), { before, result, confirms: w.__confirms, restarts: restarts(),
                               reads: versionReads().length });
""".replace("__PW", json.dumps(PASSWORD)).replace("__PULL_LANDS", PULL_LANDS), srv={"mode": "update"})
# (b) Code loaded outside the unit: pulled, then the core restarted by hand.
# Nothing is waiting, but the unit last applied 5196cc9, so its next run is
# the full update. The panel's copy shows it: the one confirm says so.
LOADED_BY_HAND = {"mode": "update", "applied": OLD, "last": {
    "status": "ok", "mode": "restart", "started_at": "run-1", "finished_at": "2026-10-05T20:22:52Z",
    "from_sha": FULL[OLD], "to_sha": FULL[OLD], "prev_source": "applied", "error": None}}
SCENARIOS["flow_loaded_outside_the_unit"] = scenario(flow(), srv=LOADED_BY_HAND)
# (b) after the panel read the version: pulled and restarted by hand since
# (a new boot time; nothing waiting). Only the fresh read at the press
# shows that the unit last applied another commit.
SCENARIOS["flow_restarted_by_hand_since_the_panel_read"] = scenario(flow(
    f"w.__srv.checkout = w.__srv.running = {json.dumps(PULLED)};"
    f" w.__srv.started = {json.dumps(HAND_RESTARTED_AT)};"), srv={"mode": "update"})
# The panel read the version before the update unit was installed (its copy
# says restart_mode "restart"); the unit, there at the press, runs its full
# update, and the press tells it as one.
SCENARIOS["unit_installed_since_the_panel_read"] = scenario(r"""
await A.login(__PW);
await start();
w.__staleCore = { sha: '5b7aa8a', running_sha: '5b7aa8a', checkout_sha: '5b7aa8a', restart_required: false,
                  code_restart_required: false, restart_capable: true, restart_mode: 'restart',
                  started_at: 1791247053.25, last_update: null };
await step();
press({ type: 'button', text: 'press' }); await step();
await pumpUntil(() => w.__srv.phase === 'down');
letBack();
await pumpUntil(() => w.__result !== null);
await step();
return { result: w.__result, toasts: toasts(), confirms: w.__confirms, restarts: restarts(),
         unitRan: w.__srv.last.mode };
""".replace("__PW", json.dumps(PASSWORD)), srv=LOADED_BY_HAND, component=DIRECT)
# What the restart button says each time the card reads the version, from
# the press to the card's own read once the server is back: a word that
# changed back mid-restart would show here. The trace re-renders only
# outside a render (a read an effect sends comes from inside one).
LABELS = r"""
await A.login(__PW);
await start();
__MEANWHILE
const labels = [];
let flushing = false;
const rerender = h.rerender.bind(h);
h.rerender = () => { flushing = true; try { return rerender(); } finally { flushing = false; } };
// The Restart Domovoi button whatever it says: the one in its own end of the row.
const ownButton = (e) => e.type === 'button' && h.inside(e, (a) => w.__cls(a).includes('version-restart'));
w.__onVersionRead = () => {
  if (flushing) return;
  h.rerender();
  const b = h.find(ownButton);
  labels.push(b ? b.text.trim() + (b.props.disabled ? ' [disabled]' : '') : null);
};
const flow = press(plainButton); await step();
await pumpUntil(() => !!note());
letBack();
const result = await flow;
w.__onVersionRead = null;
await step(); await step();
return { result, labels, buttons: pageButtons() };
""".replace("__PW", json.dumps(PASSWORD))
SCENARIOS["labels_loaded_outside_the_unit"] = scenario(LABELS.replace("__MEANWHILE", ""), srv=LOADED_BY_HAND)
SCENARIOS["labels_pulled_since_the_panel_read"] = scenario(LABELS.replace("__MEANWHILE", PULL_LANDS),
                                                           srv={"mode": "update"})
SCENARIOS["labels_quick_restart"] = scenario(LABELS.replace("__MEANWHILE", ""), srv={"mode": "update"})
# A full update outlasts a quick restart's 90 s: the wait is the unit's 15
# minutes, and when even that runs out the card says the update is slow.
# Each poll moves the page's clock a minute.
SCENARIOS["full_update_never_back"] = scenario(r"""
await A.login(__PW);
await start();
const flow = press(plainButton); await step();
await pumpUntil(() => !!note());
const away = view();
const result = await flow; await step(); await step();
return { away, result, after: view(), confirms: w.__confirms,
         awayReads: versionReads().filter((f) => f.phase === 'down').length };
""".replace("__PW", json.dumps(PASSWORD)), srv={**LOADED_BY_HAND, "clockStep": 60000})

# ─── ...and the quick restart it stays otherwise ────────────────────────

# The last update was rolled back: the checkout went back to the commit it
# came from, which is the unit's record again (to_sha names the bad one).
SCENARIOS["flow_after_a_rollback"] = scenario(flow(), srv={"mode": "update", "last": {
    "status": "rolled_back", "mode": "update", "started_at": "run-1", "finished_at": "2026-10-05T20:22:52Z",
    "from_sha": FULL[SHA], "to_sha": FULL[PULLED], "bad_sha": FULL[PULLED], "prev_source": "applied",
    "error": f"update to {PULLED} failed at health and was rolled back to {SHA}: not healthy after 120s"}})
# A unit whose last result can't be read: nothing to compare the checkout with.
SCENARIOS["flow_unit_without_a_result"] = scenario(flow(), srv={"mode": "update", "last": None})
# A plugin upgrade staged after the panel read the version sets
# restart_required, but the checkout is still the unit's own commit: its
# plain restart, which loads the plugin. With no readable result to compare
# with, it is no pulled code either.
PLUGIN_STAGED = "w.__srv.plugins = [{ slug: 'radio', from_version: '1.1.0', to_version: '1.2.0', where: ['core'] }];"
SCENARIOS["flow_plugin_staged_since_the_panel_read"] = scenario(flow(PLUGIN_STAGED), srv={"mode": "update"})
SCENARIOS["flow_plugin_staged_without_a_unit_result"] = scenario(flow(PLUGIN_STAGED),
                                                                 srv={"mode": "update", "last": None})
# No update unit: a restart is the same bounce whatever is on disk.
SCENARIOS["flow_pulled_since_without_the_unit"] = scenario(flow(PULL_LANDS))
# Untracked files make the core's checkout_sha "-dirty" (git status
# --porcelain counts them). The unit compares HEAD's commit and refuses only
# for TRACKED changes, so the tree's state decides nothing: on its own it is
# the quick restart, and code loaded by hand on such a tree the full update.
DIRTY = f"{SHA}-dirty"
SCENARIOS["flow_dirty_checkout"] = scenario(flow(), srv={"mode": "update", "running": DIRTY, "checkout": DIRTY})
SCENARIOS["flow_loaded_outside_the_unit_dirty"] = scenario(flow(), srv={**LOADED_BY_HAND, "running": DIRTY,
                                                                        "checkout": DIRTY})
# A core that can't read its checkout (git failing there answers "unknown"):
# nothing to compare the unit's commit with, so no full update is promised.
SCENARIOS["flow_checkout_unreadable"] = scenario(flow(), srv={"mode": "update", "running": "unknown",
                                                              "checkout": "unknown"})

# ─── around it: the apply press, and an answer that never arrives ───────

# "Restart to apply changes" with the update unit keeps the pulled-code
# confirm: the full-update wording belongs to a plain press alone.
SCENARIOS["apply_update_unit"] = scenario(r"""
await A.login(__PW);
await start();
const out = { before: view() };
const flow = press({ type: 'button', text: 'Restart to apply changes' }); await step();
await pumpUntil(() => !!note());
out.away = Object.assign(view(), { restarts: restarts() });
letBack();
out.result = await flow; await step(); await step();
out.after = view();
out.confirms = w.__confirms;
out.unitRan = w.__srv.last.mode;
return out;
""".replace("__PW", json.dumps(PASSWORD)), srv={"mode": "update", "checkout": PULLED})
# (b), and the server is gone before the restart's own answer arrives.
SCENARIOS["flow_loaded_outside_the_unit_cut"] = scenario(flow(), srv={**LOADED_BY_HAND, "cutAnswer": True})
# The panel's copy predates the update unit; the fresh read at the press
# finds it and its full update; the answer is cut off; the update is rolled
# back. Only the unit's wait knows to read its result.
SCENARIOS["cut_unit_installed_since_the_panel_read"] = scenario(r"""
await A.login(__PW);
await start();
w.__staleCore = { sha: '5b7aa8a', running_sha: '5b7aa8a', checkout_sha: '5b7aa8a', restart_required: false,
                  code_restart_required: false, restart_capable: true, restart_mode: 'restart',
                  started_at: 1791247053.25, last_update: null };
await step();
press({ type: 'button', text: 'press' }); await step();
await pumpUntil(() => w.__srv.phase === 'down');
letBack();
await pumpUntil(() => w.__result !== null);
await step();
return { result: w.__result, toasts: toasts(), confirms: w.__confirms, restarts: restarts() };
""".replace("__PW", json.dumps(PASSWORD)), srv={
    **LOADED_BY_HAND, "cutAnswer": True, "newRunStatus": "rolled_back",
    "newRunError": f"update to {SHA} failed at health and was rolled back to {OLD}: not healthy after 120s"},
    component=DIRECT)


# ─── one restart at a time ──────────────────────────────────────────────

# The update unit's record of the run under way (apply-update.sh writes
# "running" once it is past its pre-flight). __comeBack names the finished
# run 'run-2' too, so the run followed is the one that ends.
RUNNING_UPDATE = {"status": "running", "mode": "update", "started_at": "run-2", "finished_at": None,
                  "from_sha": FULL[SHA], "to_sha": FULL[PULLED], "prev_source": "applied",
                  "error": None}
RUNNING_QUICK = {**RUNNING_UPDATE, "mode": "restart", "to_sha": FULL[SHA]}
MID_UPDATE = {"mode": "update", "checkout": PULLED, "busy": True, "busyDownPolls": 2,
              "last": RUNNING_UPDATE}

# The owner's report: "Restart to apply changes" pressed, the page reloaded
# (a view-only tab now) while the update unit runs. __SIGN_IN: '' for that
# tab, or an admin signing in first.
RELOADED = r"""
__SIGN_IN
await start();
const out = { during: view() };
await pumpUntil(() => versionReads().length >= 4);
out.stillDuring = view();
out.readsDuring = versionReads().length;
w.__srv.busyPolls = 0;
await pumpUntil(() => toasts().length > 0);
await step(); await step();
out.after = view();
out.restarts = restarts();
out.pulls = posts('/api/config/version/pull');
out.downReads = versionReads().filter((f) => f.phase === 'down').length;
return out;
"""
SIGN_IN = f"await A.login({json.dumps(PASSWORD)});"
SCENARIOS["reloaded_mid_update"] = scenario(RELOADED.replace("__SIGN_IN", ""), srv=MID_UPDATE)
SCENARIOS["reloaded_mid_update_signed_in"] = scenario(RELOADED.replace("__SIGN_IN", SIGN_IN),
                                                      srv=MID_UPDATE)
SCENARIOS["reloaded_mid_update_rolled_back"] = scenario(RELOADED.replace("__SIGN_IN", ""), srv={
    **MID_UPDATE, "newRunStatus": "rolled_back",
    "newRunError": f"update to {PULLED} failed at health and was rolled back to {SHA}: not healthy after 120s"})
# The unit's quick restart (nothing new since the commit it applied).
SCENARIOS["reloaded_mid_quick_restart"] = scenario(RELOADED.replace("__SIGN_IN", SIGN_IN), srv={
    "mode": "update", "busy": True, "last": RUNNING_QUICK})
# A server whose unit is running but hasn't written its record yet: the
# record is the run before (ok), which the follow must not report as how
# this one went.
SCENARIOS["reloaded_before_the_unit_wrote_its_record"] = scenario(
    RELOADED.replace("__SIGN_IN", ""), srv={**MID_UPDATE, "last": None})

# The panel read the version before the run started (another tab pressed,
# or it was started by hand); the fresh read at the press finds it.
SCENARIOS["press_finds_one_under_way"] = scenario(r"""
await A.login(__PW);
await start();
const out = { before: view() };
w.__srv.busy = true; w.__srv.checkout = __PULLED; w.__srv.last = __RUNNING;
const flow = press(plainButton); await step();
await pumpUntil(() => !!note());
out.away = Object.assign(view(), { restarts: restarts() });
w.__srv.busyPolls = 0;
out.result = await flow; await step(); await step();
out.after = view();
out.confirms = w.__confirms;
out.restarts = restarts();
return out;
""".replace("__PW", json.dumps(PASSWORD)).replace("__PULLED", json.dumps(PULLED))
    .replace("__RUNNING", json.dumps(RUNNING_UPDATE)), srv={"mode": "update"})
# Both reads said nothing was under way, and the server refuses the press
# for one anyway (another tab's press landed between).
SCENARIOS["press_refused_for_one_under_way"] = scenario(r"""
await A.login(__PW);
await start();
const out = { before: view() };
const flow = press(plainButton); await step();
await pumpUntil(() => !!note());
out.away = Object.assign(view(), { restarts: restarts() });
w.__srv.busyPolls = 0;
out.result = await flow; await step(); await step();
out.after = view();
out.restarts = restarts();
return out;
""".replace("__PW", json.dumps(PASSWORD)), srv={"busyOnPost": True})


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("restart-button") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), "@" + str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    return _Outcomes(json.loads(proc.stdout))


class _Outcomes(dict):
    """A scenario whose script threw fails the tests that read IT, with the
    harness's own error, and no other."""

    def __getitem__(self, name):
        out = super().__getitem__(name)
        if isinstance(out, dict) and "__harness_error" in out:
            pytest.fail(f"{name}: {out['__harness_error']}")
        return out


RESTART = "Restart Domovoi"
APPLY = "Restart to apply changes"
RESTARTED = f"restarted — now running {SHA}"


# ─── who sees it ─────────────────────────────────────────────────────────


def test_an_admin_signed_in_here_gets_the_restart(driven) -> None:
    o = driven["admin"]
    assert o["bearer"] is True
    assert RESTART in o["buttons"]
    assert "Check for updates" in o["buttons"]          # the update check stays
    assert APPLY not in o["buttons"]
    assert o["hint"] is None and o["note"] is None


def test_a_view_only_tab_gets_it_too(driven) -> None:
    """A reload keeps the cookie and forgets the Bearer; the press asks for
    the password once (test_a_view_only_press_signs_in_and_replays_once)."""
    assert RESTART in driven["view_only"]["buttons"]


def test_a_browser_without_an_admin_sign_in_never_sees_it(driven) -> None:
    o = driven["signed_out"]
    assert RESTART not in o["buttons"], o["buttons"]
    assert "Check for updates" in o["buttons"]
    assert o["hint"] is None


@pytest.mark.parametrize("name", ["pending_admin", "pending_update_unit_admin"])
def test_never_two_restart_buttons(driven, name) -> None:
    """Pulled code is waiting: "Restart to apply changes" is the one action,
    and it restarts too."""
    buttons = driven[name]["buttons"]
    assert APPLY in buttons
    assert RESTART not in buttons, buttons
    assert sum("Restart" in b for b in buttons) == 1, buttons


def test_the_update_unit_host_gets_the_same_button(driven) -> None:
    assert RESTART in driven["update_unit_admin"]["buttons"]


# ─── a host that can't restart itself ────────────────────────────────────


@pytest.mark.parametrize("name, command", [("incapable_admin", LINUX_RESTART),
                                           ("incapable_update_unit_admin", LINUX_UPDATE),
                                           ("older_core_incapable_admin", LINUX_RESTART)])
def test_a_host_without_the_grant_shows_the_command(driven, name, command) -> None:
    o = driven[name]
    assert RESTART not in o["buttons"], o["buttons"]
    assert o["hint"] is not None
    assert o["hint"].endswith("Run by hand:" + command), o["hint"]


def test_the_reason_reads_as_a_sentence(driven) -> None:
    hint = driven["incapable_admin"]["hint"]
    assert hint.startswith("no passwordless sudoers grant for systemctl restart — see the "
                           "self-restart entry in docs/LINUX_HOST.md. Run by hand:"), hint


def test_the_command_is_for_admins_only(driven) -> None:
    o = driven["incapable_signed_out"]
    assert o["hint"] is None
    assert RESTART not in o["buttons"]


@pytest.mark.parametrize("name", ["no_systemd_admin", "no_systemd_pending_admin"])
def test_a_host_without_systemd_is_given_no_systemctl_line(driven, name) -> None:
    """Windows and development boxes: the core sends restart_command null.
    The card used to print the Linux command there too."""
    o = driven[name]
    assert o["hint"] == ("systemctl not found — not a systemd host. "
                         "Restart the Domovoi services the way they were started."), o["hint"]
    assert "systemctl restart" not in o["hint"] and "systemctl start" not in o["hint"]
    assert RESTART not in o["buttons"] and APPLY not in o["buttons"]


# ─── the confirm ─────────────────────────────────────────────────────────


def test_the_confirm_says_what_a_restart_interrupts(driven) -> None:
    (text,) = driven["flow"]["confirms"]
    first, _, body = text.partition("\n\n")
    assert first == "Restart Domovoi now?"
    assert "domovoi/.env" in body
    assert "satellites reconnect on their own" in body
    assert "Music playing in a room may stop" in body
    # Not the pulled-code confirm: nothing is backed up or applied.
    assert "backs up" not in body and "pulled code" not in text


def test_the_update_unit_host_gets_the_same_confirm(driven) -> None:
    assert driven["flow_update_unit"]["confirms"] == driven["flow"]["confirms"]


def test_cancel_sends_nothing(driven) -> None:
    o = driven["confirm_cancelled"]
    assert o["result"] is False
    assert len(o["confirms"]) == 1
    assert o["restarts"] == []
    assert RESTART in o["buttons"]                      # enabled, as before
    assert o["toasts"] == [] and o["note"] is None


# ─── the restart itself ──────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["flow", "flow_update_unit", "flow_old_server_lingers"])
def test_while_the_server_is_away_the_card_says_restarting(driven, name) -> None:
    o = driven[name]["away"]
    assert o["restarts"] == ["bearer"]
    assert "Restarting… [disabled]" in o["buttons"], o["buttons"]
    # Never "Updating…": with the update unit this is its plain restart.
    assert not any("Updating" in b for b in o["buttons"])
    assert "Check for updates [disabled]" in o["buttons"]
    assert o["note"] is not None and o["note"].startswith("Restarting —"), o["note"]
    assert "backing up" not in o["note"]
    assert "restarting…" in o["toasts"] and "updating…" not in o["toasts"]
    assert o["modal"] is False


@pytest.mark.parametrize("name", ["flow", "flow_update_unit", "flow_old_server_lingers"])
def test_the_card_says_when_it_is_back(driven, name) -> None:
    o = driven[name]
    assert o["result"] is True
    after = o["after"]
    assert RESTARTED in after["toasts"]
    assert RESTART in after["buttons"]                  # back, and enabled
    assert after["note"] is None and after["modal"] is False
    assert not any("fail" in t.lower() for t in after["toasts"]), after["toasts"]
    # The poll knocked while the server was away and stopped on the new one.
    assert any(r.startswith("down:") for r in o["reads"]), o["reads"]
    assert o["reads"][-1] == f"up:{RESTARTED_AT}", o["reads"]


def test_the_old_server_still_answering_is_not_the_restart_done(driven) -> None:
    """The bounce fires a second after the answer, so the old process
    answers the first reads after the press. Nothing was waiting, so it
    has nothing left to restart either; only its boot time tells it from
    the new one. Those reads must not end the wait."""
    o = driven["flow_old_server_lingers"]
    lingered = [r for r in o["reads"] if r.startswith("linger:")]
    assert lingered == [f"linger:{STARTED}"] * 2, o["reads"]
    # ...and the wait went on through them, to the server going away.
    last = max(i for i, r in enumerate(o["reads"]) if r.startswith("linger:"))
    assert o["reads"][last + 1].startswith("down:"), o["reads"]
    assert o["result"] is True
    assert o["after"]["toasts"].count(RESTARTED) == 1


def test_a_server_that_cannot_say_when_it_started_is_back_once_it_was_away(driven) -> None:
    """No started_at to compare (a core whose boot capture never ran): the
    old process still answering after the press is not the restart done.
    The wait ends once the server has been seen away and answers again."""
    o = driven["flow_no_started_at"]
    reads = o["reads"]
    assert reads.count("linger:null") == 2, reads
    last = max(i for i, r in enumerate(reads) if r.startswith("linger:"))
    assert reads[last + 1].startswith("down:"), reads
    assert reads[-1] == "up:null", reads
    assert o["result"] is True
    assert o["after"]["toasts"].count(RESTARTED) == 1


def test_a_restart_quicker_than_the_poll_is_still_seen(driven) -> None:
    """A fast host is back on a fresh start before any poll found the old
    server gone: no read ever failed, and its boot time moving is the proof
    the wait needs."""
    o = driven["flow_back_between_polls"]
    assert not any(r.startswith("down:") for r in o["reads"]), o["reads"]
    assert f"linger:{STARTED}" in o["reads"], o["reads"]
    assert o["reads"][-1] == f"up:{RESTARTED_AT}", o["reads"]
    assert o["result"] is True
    assert o["after"]["toasts"].count(RESTARTED) == 1


def test_a_restart_under_way_greys_the_pull(driven) -> None:
    o = driven["flow_pull_mode"]
    assert "Pull the latest" in o["before"]["buttons"], o["before"]["buttons"]
    assert RESTART in o["before"]["buttons"], o["before"]["buttons"]
    assert "Pull the latest [disabled]" in o["away"]["buttons"], o["away"]["buttons"]
    assert "Restarting… [disabled]" in o["away"]["buttons"], o["away"]["buttons"]
    assert o["result"] is True
    assert o["restarts"] == ["bearer"]


def test_a_restart_some_other_way_since_the_panel_read_is_not_this_one(driven) -> None:
    """The panel's copy predates a restart done by hand; the press reads the
    server again, so the old process answering after it is still the old
    one."""
    o = driven["stale_panel_copy"]
    assert o["during"]["result"] is None, o
    assert not any(t.startswith("restarted") for t in o["during"]["toasts"]), o["during"]
    assert o["result"] is True
    assert RESTARTED in o["toasts"]
    assert o["restarts"] == ["bearer"]
    # The fresh read at the press, then the old server once, then away.
    assert o["reads"][0] == "up:1791240000.75"
    assert "linger:1791240000.75" in o["reads"]


def test_a_failed_plain_restart_reports_the_units_error(driven) -> None:
    o = driven["flow_update_unit_failed"]
    assert o["result"] is False
    assert "health failed (exit 1): not healthy after 120s: core down, web up" in o["after"]["toasts"]
    assert not any(t.startswith("restarted") for t in o["after"]["toasts"])
    assert RESTART in o["after"]["buttons"] and o["after"]["note"] is None


def test_a_view_only_press_signs_in_and_replays_once(driven) -> None:
    o = driven["flow_view_only"]
    assert RESTART in o["before"]["buttons"]
    # Refused for the cookie: the admin login modal asks.
    assert o["prompted"] == {"modal": True, "restarts": ["cookie"]}
    # Signed in: the modal is down, the restart was replayed with the Bearer.
    assert o["away"]["modal"] is False
    assert o["away"]["restarts"] == ["cookie", "bearer"]
    assert o["away"]["note"].startswith("Restarting —")
    assert o["result"] is True
    assert RESTARTED in o["after"]["toasts"]
    assert o["after"]["restarts"] == ["cookie", "bearer"]
    assert o["after"]["bearer"] is True


# ─── a press the update unit runs as its full update ────────────────────

QUICK_CONFIRM_FIRST_LINE = "Restart Domovoi now?"
UPDATE_TEXT = ("This backs up the database, updates dependencies and migrations if they changed, and "
               "restarts domovoi-core and domovoi-web. If they don’t come back healthy it rolls "
               "everything back. Voice is unavailable meanwhile, usually for under a minute, longer "
               "when dependencies change.")
FULL_UPDATE_FLOWS = ["flow_pulled_since_the_panel_read", "flow_pulled_since_without_a_unit_result",
                     "flow_loaded_outside_the_unit", "flow_restarted_by_hand_since_the_panel_read"]


@pytest.mark.parametrize("name", ["flow_pulled_since_the_panel_read",
                                  "flow_pulled_since_without_a_unit_result"])
def test_a_pull_after_the_panel_read_is_asked_again_as_the_update(driven, name) -> None:
    """(a) The panel's copy offered the quick restart, and its confirm said
    so. The fresh read at the press shows the pulled code waiting, which
    the update unit applies as its full update: before anything is sent, a
    second confirm asks for that. Also from a unit whose last result can't
    be read."""
    o = driven[name]
    assert RESTART in o["before"]["buttons"], o["before"]["buttons"]
    quick, update = o["confirms"]
    assert quick == driven["flow"]["confirms"][0]
    question, _, body = update.partition("\n\n")
    assert question == "Run the full update instead?"
    assert body == (f"Pulled code is waiting to load (running {SHA}, checked out {PULLED}), so this "
                    "restart runs the update unit’s full update, not the quick restart you said yes "
                    f"to. {UPDATE_TEXT}")
    assert o["restarts"] == ["bearer"]
    assert o["unitRan"] == "update"


def test_code_loaded_outside_the_unit_is_asked_as_the_update_at_once(driven) -> None:
    """(b) Pulled, then the core restarted by hand: nothing is waiting, but
    the unit last applied another commit, so its next run is the full
    update. The panel's copy already shows it: the one confirm asks for the
    full update, and no second one follows."""
    o = driven["flow_loaded_outside_the_unit"]
    (text,) = o["confirms"]
    question, _, body = text.partition("\n\n")
    assert question == "Restart Domovoi now, as a full update?"
    assert body == (f"The checkout ({SHA}) isn’t the commit the update unit last applied ({OLD}), so "
                    f"this restart runs the update unit’s full update, not a quick restart. {UPDATE_TEXT}")
    assert o["restarts"] == ["bearer"]
    assert o["unitRan"] == "update"


def test_a_hand_restart_after_the_panel_read_is_asked_again_as_the_update(driven) -> None:
    """(b), arriving after the panel read the version: only the fresh read
    at the press shows that the checkout isn't the unit's commit."""
    o = driven["flow_restarted_by_hand_since_the_panel_read"]
    quick, update = o["confirms"]
    assert quick.startswith(QUICK_CONFIRM_FIRST_LINE + "\n\n")
    question, _, body = update.partition("\n\n")
    assert question == "Run the full update instead?"
    assert body.startswith(f"The checkout ({PULLED}) isn’t the commit the update unit last applied ({SHA}), "
                           "so this restart runs the update unit’s full update, not the quick restart "
                           "you said yes to. "), body
    assert o["unitRan"] == "update"
    # Measured against the server after the hand restart, not the panel's copy.
    assert f"up:{HAND_RESTARTED_AT}" in o["reads"], o["reads"]


@pytest.mark.parametrize("name", FULL_UPDATE_FLOWS)
def test_a_full_update_is_told_as_one(driven, name) -> None:
    """"Updating…", the update note and toast while the server is away (the
    Version card reads them from onUnderway's argument), then the version
    it came back on."""
    o = driven[name]
    away = o["away"]
    assert "Updating… [disabled]" in away["buttons"], away["buttons"]
    assert not any(b.startswith("Restarting") for b in away["buttons"]), away["buttons"]
    assert away["note"] is not None and away["note"].startswith(
        "Updating — backing up, applying and restarting the Domovoi services."), away["note"]
    assert "updating…" in away["toasts"] and "restarting…" not in away["toasts"], away["toasts"]
    assert o["result"] is True
    after = o["after"]
    running = SHA if name == "flow_loaded_outside_the_unit" else PULLED
    assert f"restarted — now running {running}" in after["toasts"], after["toasts"]
    assert RESTART in after["buttons"] and after["note"] is None


def test_a_question_that_stood_a_while_measures_the_server_after_it(driven) -> None:
    """The pulled code was applied some other way while the second question
    stood. The press reads the server again after the yes: the unit has
    nothing new to apply now, so the press is told as the quick restart it
    has become, and the old process still answering with that other run's
    result is not taken for this restart done."""
    o = driven["unit_ran_while_the_question_stood"]
    assert len(o["confirms"]) == 2
    assert "Restarting… [disabled]" in o["away"]["buttons"], o["away"]["buttons"]
    assert o["away"]["note"] is not None and o["away"]["note"].startswith("Restarting —"), o["away"]["note"]
    assert o["result"] is True
    assert o["after"]["toasts"].count(f"restarted — now running {PULLED}") == 1, o["after"]["toasts"]
    reads = o["reads"]
    assert f"linger:{HAND_RESTARTED_AT}" in reads, reads
    last = max(i for i, r in enumerate(reads) if r.startswith("linger:"))
    assert reads[last + 1].startswith("down:"), reads
    assert reads[-1] == f"up:{RESTARTED_AT}", reads
    assert o["unitRan"] == "restart"


UPDATING = "Updating… [disabled]"
RESTARTING = "Restarting… [disabled]"


def test_a_press_asked_as_the_update_says_so_from_the_yes(driven) -> None:
    """The operator said yes to the full update: the button says
    "Updating…" from then on (onStart's argument), through every read, to
    the card's own read once the server is back (it used to fall back to
    "Restarting…" there for a moment, once the note had gone)."""
    o = driven["labels_loaded_outside_the_unit"]
    assert o["result"] is True
    assert len(o["labels"]) >= 3 and set(o["labels"]) == {UPDATING}, o["labels"]
    assert RESTART in o["buttons"]


def test_a_quick_restart_turned_update_never_goes_back(driven) -> None:
    """Asked as a quick restart, then as the update: "Restarting…" until
    the server takes the update, "Updating…" from then to the end."""
    labels = driven["labels_pulled_since_the_panel_read"]["labels"]
    assert labels[0] == RESTARTING, labels
    first = labels.index(UPDATING)
    assert set(labels[:first]) == {RESTARTING} and set(labels[first:]) == {UPDATING}, labels
    assert driven["labels_pulled_since_the_panel_read"]["result"] is True


def test_a_quick_restart_says_restarting_throughout(driven) -> None:
    o = driven["labels_quick_restart"]
    assert o["result"] is True
    assert len(o["labels"]) >= 3 and set(o["labels"]) == {RESTARTING}, o["labels"]


def test_a_full_update_declined_sends_nothing(driven) -> None:
    """No at the second confirm: no restart is asked for, and the panel
    reads the version again, so it offers what is really waiting."""
    o = driven["pulled_since_declined"]
    assert o["result"] is False
    assert len(o["confirms"]) == 2
    assert o["restarts"] == []
    assert APPLY in o["buttons"] and RESTART not in o["buttons"], o["buttons"]
    assert o["note"] is None and o["toasts"] == [], o


def test_the_press_tells_what_the_server_runs_not_what_the_panel_read(driven) -> None:
    """The panel read the version before the update unit was installed; at
    the press the unit is there and runs its full update. Asked again as
    that, and told as that."""
    o = driven["unit_installed_since_the_panel_read"]
    assert [c.partition("\n\n")[0] for c in o["confirms"]] == [QUICK_CONFIRM_FIRST_LINE,
                                                              "Run the full update instead?"]
    assert "updating…" in o["toasts"] and "restarting…" not in o["toasts"], o["toasts"]
    assert o["result"] is True and o["unitRan"] == "update"


def test_a_full_update_gets_the_units_fifteen_minutes(driven) -> None:
    """A quick restart gives up after 90 s, two polls a minute apart here;
    the full update's wait goes on for 15 minutes, then says the UPDATE is
    slow and where to look."""
    o = driven["full_update_never_back"]
    assert "Updating… [disabled]" in o["away"]["buttons"], o["away"]["buttons"]
    assert o["result"] is False
    assert ("the update is taking longer than expected — check journalctl -u domovoi-update"
            in o["after"]["toasts"]), o["after"]["toasts"]
    # 15 polls, plus the card's own re-read once the wait is over.
    assert 15 <= o["awayReads"] <= 17, o["awayReads"]


# ─── ...and the quick restart it stays otherwise ────────────────────────

QUICK_FLOWS = ["flow_update_unit", "flow_after_a_rollback", "flow_unit_without_a_result",
               "flow_plugin_staged_since_the_panel_read", "flow_plugin_staged_without_a_unit_result",
               "flow_pulled_since_without_the_unit", "flow_dirty_checkout", "flow_checkout_unreadable"]


@pytest.mark.parametrize("name", QUICK_FLOWS)
def test_otherwise_it_stays_the_quick_restart(driven, name) -> None:
    """Nothing new for the update unit to apply, or no unit at all: asked,
    told and reported as the quick restart (these pass on the old static
    too: they pin what must not change). After a rollback the unit's commit
    is the one it went back to (from_sha), not the one it rolled back
    (to_sha); a staged plugin upgrade sets restart_required but is no
    pulled code, with the unit's result to compare with or without one; a
    unit without a readable result gives nothing to compare; without the
    unit the bounce is the bounce. Untracked files ("-dirty") leave HEAD the
    unit's commit; a checkout the core can't read ("unknown") gives nothing
    to compare either."""
    o = driven[name]
    assert o["confirms"] == driven["flow"]["confirms"], o["confirms"]
    away = o["away"]
    assert "Restarting… [disabled]" in away["buttons"], away["buttons"]
    assert not any("Updating" in b for b in away["buttons"]), away["buttons"]
    assert away["note"] is not None and away["note"].startswith("Restarting —"), away["note"]
    assert "backing up" not in away["note"]
    assert "restarting…" in away["toasts"] and "updating…" not in away["toasts"], away["toasts"]
    assert o["result"] is True
    assert o["unitRan"] == (None if name == "flow_pulled_since_without_the_unit" else "restart")


def test_untracked_files_leave_a_hand_restart_the_full_update(driven) -> None:
    """(b) on a tree with untracked files: the core says "5b7aa8a-dirty",
    the unit still compares commits, and its next run is still the full
    update. The suffix must not hide that."""
    o = driven["flow_loaded_outside_the_unit_dirty"]
    (text,) = o["confirms"]
    question, _, body = text.partition("\n\n")
    assert question == "Restart Domovoi now, as a full update?"
    assert body.startswith(f"The checkout ({SHA}) isn’t the commit the update unit last applied ({OLD}), "), body
    assert "Updating… [disabled]" in o["away"]["buttons"], o["away"]["buttons"]
    assert o["away"]["note"] is not None and o["away"]["note"].startswith(
        "Updating — backing up, applying and restarting the Domovoi services."), o["away"]["note"]
    assert o["result"] is True and o["unitRan"] == "update"


# ─── around it: the apply press, and an answer that never arrives ───────


def test_restart_to_apply_changes_keeps_its_own_confirm(driven) -> None:
    """The pulled-code restart on an update-unit host is asked as it always
    was, once: the full-update question is the plain press's alone."""
    o = driven["apply_update_unit"]
    assert APPLY in o["before"]["buttons"] and RESTART not in o["before"]["buttons"], o["before"]["buttons"]
    assert o["confirms"] == [f"Apply the pulled code?\n\n{UPDATE_TEXT}"], o["confirms"]
    away = o["away"]
    assert away["restarts"] == ["bearer"]
    assert "Updating… [disabled]" in away["buttons"], away["buttons"]
    assert away["note"] is not None and away["note"].startswith("Updating — backing up"), away["note"]
    assert "updating…" in away["toasts"], away["toasts"]
    assert o["result"] is True and o["unitRan"] == "update"
    assert f"restarted — now running {PULLED}" in o["after"]["toasts"], o["after"]["toasts"]


def test_a_full_update_whose_answer_is_cut_off_is_told_as_one(driven) -> None:
    """The server takes the restart and is gone before its answer arrives:
    that is the restart working, and it is told as what runs, the update."""
    o = driven["flow_loaded_outside_the_unit_cut"]
    away = o["away"]
    assert away["restarts"] == ["bearer"]
    assert "Updating… [disabled]" in away["buttons"], away["buttons"]
    assert away["note"] is not None and away["note"].startswith("Updating — backing up"), away["note"]
    assert "updating…" in away["toasts"] and "restarting…" not in away["toasts"], away["toasts"]
    assert o["result"] is True and o["unitRan"] == "update"
    assert f"restarted — now running {SHA}" in o["after"]["toasts"], o["after"]["toasts"]


def test_a_cut_off_answer_is_waited_for_as_the_read_at_the_press_says(driven) -> None:
    """The panel's copy predates the update unit and the restart's answer is
    cut off, so only the fresh read at the press says what runs: the unit's
    full update, waited for as the unit's, so its rolled-back run is
    reported rather than taken for a restart done."""
    o = driven["cut_unit_installed_since_the_panel_read"]
    assert [c.partition("\n\n")[0] for c in o["confirms"]] == [QUICK_CONFIRM_FIRST_LINE,
                                                              "Run the full update instead?"]
    assert o["restarts"] == ["bearer"]
    assert o["result"] is False
    assert f"update to {SHA} failed at health and was rolled back to {OLD}: not healthy after 120s" in o["toasts"], \
        o["toasts"]
    assert not any(t.startswith("restarted") for t in o["toasts"]), o["toasts"]
    assert "updating…" in o["toasts"] and "restarting…" not in o["toasts"], o["toasts"]


# ─── one restart at a time ───────────────────────────────────────────────


@pytest.mark.parametrize("name", ["reloaded_mid_update", "reloaded_mid_update_signed_in"])
def test_a_reload_mid_update_offers_nothing_that_restarts(driven, name) -> None:
    """The owner's report: the card offered "Restart to apply changes" again
    while the update it started was still running."""
    o = driven[name]
    for when in ("during", "stillDuring"):
        assert o[when]["buttons"] == [UPDATING], o[when]["buttons"]
        assert o[when]["note"].startswith("Updating —"), o[when]["note"]
        assert o[when]["modal"] is False
    assert o["restarts"] == [] and o["pulls"] == []


@pytest.mark.parametrize("name", ["reloaded_mid_update", "reloaded_mid_update_signed_in"])
def test_the_reloaded_card_follows_the_update_to_its_end(driven, name) -> None:
    o = driven[name]
    assert o["readsDuring"] >= 4, "the card polls while the update runs"
    assert o["downReads"] == 2, "and keeps polling while the server is away"
    assert o["after"]["toasts"] == [f"restarted — now running {PULLED}"]
    assert o["after"]["buttons"] == ["Check for updates", RESTART]
    assert o["after"]["note"] is None
    assert o["after"]["modal"] is False, "no prompt for a read refused while it came back"


def test_a_followed_update_that_rolled_back_says_so(driven) -> None:
    o = driven["reloaded_mid_update_rolled_back"]
    assert o["after"]["toasts"] == [
        f"update to {PULLED} failed at health and was rolled back to {SHA}: not healthy after 120s"]
    assert o["restarts"] == []


def test_the_units_quick_restart_is_told_as_a_restart(driven) -> None:
    o = driven["reloaded_mid_quick_restart"]
    assert o["during"]["buttons"] == [RESTARTING], o["during"]["buttons"]
    assert o["during"]["note"].startswith("Restarting —"), o["during"]["note"]
    assert o["after"]["toasts"] == [RESTARTED]
    assert o["after"]["buttons"] == ["Check for updates", RESTART]


def test_a_run_before_its_record_is_still_an_update_under_way(driven) -> None:
    o = driven["reloaded_before_the_unit_wrote_its_record"]
    assert o["during"]["buttons"] == [UPDATING]
    assert o["after"]["toasts"] == [f"restarted — now running {PULLED}"]
    assert o["restarts"] == []


def test_a_press_that_finds_one_under_way_asks_for_nothing(driven) -> None:
    o = driven["press_finds_one_under_way"]
    assert RESTART in o["before"]["buttons"]
    assert len(o["confirms"]) == 1, "no second question about a full update"
    assert o["restarts"] == [], "nothing was asked of the server"
    assert o["away"]["note"].startswith("Updating —")
    assert o["away"]["toasts"][0] == "an update is already under way — waiting for it to finish"
    assert o["result"] is True
    assert o["after"]["toasts"][-1] == f"restarted — now running {PULLED}"
    assert o["after"]["buttons"] == ["Check for updates", RESTART]


def test_a_press_the_server_refuses_for_one_under_way_follows_it(driven) -> None:
    o = driven["press_refused_for_one_under_way"]
    assert o["restarts"] == ["bearer"], "one press, refused"
    assert o["away"]["toasts"][0] == "a restart is already under way — waiting for it to finish"
    assert not any(t.startswith("restart failed") for t in o["after"]["toasts"]), o["after"]["toasts"]
    assert o["result"] is True
    assert o["after"]["toasts"][-1] == RESTARTED
    assert o["after"]["buttons"] == ["Check for updates", RESTART]
