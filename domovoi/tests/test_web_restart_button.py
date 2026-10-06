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
  is that unit's quick plain restart, never "Updating…";
* the wait ends on a NEW server: not on the old one still answering after
  the press, and not on a panel copy read before a restart done some other
  way (both looked "restarted" at once: a plain restart never sets
  restart_required, so its clearing proves nothing);
* a view-only press signs in and the restart is replayed once, with the
  Bearer;
* a plain restart the update unit records as failed reports the unit's
  error.

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
PASSWORD = "right-password"
STARTED = 1791247053.25
RESTARTED_AT = 1791250000.5
LINUX_RESTART = "sudo systemctl restart domovoi-core domovoi-web"
LINUX_UPDATE = "sudo systemctl start domovoi-update.service"

# ─── the in-sandbox prelude: a server that restarts ──────────────────────

PRELUDE = r"""
const __st = setTimeout;
// The restart's poll waits 2 s between reads in a browser; here it waits a
// couple of milliseconds. Every other timer keeps its length and does not
// keep node alive once the scenarios are done.
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

// Every confirm() the page asks, and what the operator answers.
window.__confirms = [];
window.__confirmAnswer = true;
window.confirm = (msg) => { window.__confirms.push(String(msg)); return window.__confirmAnswer; };

// phase: 'up' answers; 'linger' is the OLD server still answering after it
// accepted the restart (lingerPolls version reads); 'down' rejects every
// request at the socket, as a browser does for a server that is not there.
// A restart moves up -> linger|down on the request AFTER it answered; the
// poll walks it back once a scenario lets it (downPolls), on a fresh start.
window.__srv = Object.assign({
  sessions: new Set(__SESSIONS), cookie: __COOKIE, n: 0, device: 'house-token',
  phase: 'up', goDown: false, restarted: false, lingerPolls: 0, downPolls: 10 ** 9,
  run: 'run-1', last: null, newRunStatus: 'ok', newRunError: null,
}, __SRV);
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
              restart_required: pending, code_restart_required: pending, plugins_pending_restart: [],
              restart_capable: s.capable, restart_mode: s.mode, restart_hint: s.hint,
              started_at: s.started, uptime_sec: 7200,
              last_update: s.mode === 'update' ? s.last : null, bad_sha: null };
  if (s.command !== undefined) v.restart_command = s.command;
  return v;
};
// Back on a fresh start: a new boot time, the checkout loaded, and with the
// update unit the result of the run the restart started.
const __comeBack = () => {
  const s = window.__srv;
  const from = s.running;
  s.running = s.checkout;
  s.started = __RESTARTED_AT;
  if (s.mode === 'update') {
    s.run = 'run-2';
    s.last = { status: s.newRunStatus, mode: from === s.checkout ? 'restart' : 'update',
               started_at: s.run, finished_at: '2026-10-05T23:59:00Z', from_sha: from, to_sha: s.checkout,
               error: s.newRunError };
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
    srv.restarted = true;
    srv.goDown = true;
    return __answer(200, { ok: true, mode: srv.mode, delay_sec: 1, error: null,
                           units: srv.mode === 'update' ? ['domovoi-update.service']
                             : ['domovoi-core.service', 'domovoi-web.service'] });
  }
  if (method === 'POST' && bare === '/api/config/version/check') {
    return __answer(200, { upstream: true, behind: 0, ahead: 0, upstream_sha: null, error: null });
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
fetch = async (url, opts) => {
  const o = opts || {};
  const method = String(o.method || 'GET').toUpperCase();
  const path = String(url).replace(/^https?:\/\/[^/]+/, '');
  const bare = path.split('?')[0];
  const hd = o.headers || {};
  const srv = window.__srv;
  if (srv.goDown) { srv.goDown = false; srv.phase = srv.lingerPolls > 0 ? 'linger' : 'down'; }
  const auth = String(hd.Authorization || '');
  const bearer = auth.startsWith('Bearer ') ? auth.slice(7) : null;
  const who = bearer ? (srv.sessions.has(bearer) ? 'bearer' : 'invalid')
    : (srv.cookie && srv.sessions.has(srv.cookie) ? 'cookie' : 'none');
  window.__fetches.push({ method, path, who, phase: srv.phase, started: srv.started });
  if (srv.phase === 'linger' && method === 'GET' && bare === '/api/config/version') {
    srv.lingerPolls -= 1;
    if (srv.lingerPolls <= 0) srv.phase = 'down';
    return __route(method, path, { who, bearer }, o.body);
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
    unless given), lingerPolls, the update unit's results."""
    base = {"running": SHA, "checkout": SHA, "started": STARTED, "mode": "restart",
            "capable": True, "hint": None}
    if (srv or {}).get("mode") == "update":
        base["last"] = {"status": "ok", "mode": "update", "started_at": "run-1",
                        "finished_at": "2026-10-05T20:22:52Z", "from_sha": "aaaaaaa", "to_sha": SHA,
                        "error": None}
    base.update(srv or {})
    sessions = [cookie] if cookie else []
    head = (f"const __LS = {json.dumps({'domovoi-device-token': 'house-token'})};"
            f" const __SESSIONS = {json.dumps(sessions)}; const __COOKIE = {json.dumps(cookie)};"
            f" const __PASSWORD = {json.dumps(PASSWORD)}; const __SRV = {json.dumps(base)};"
            f" const __RESTARTED_AT = {json.dumps(RESTARTED_AT)};\n")
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

FLOW = r"""
await A.login(__PW);
await start();
const out = { before: view() };
const flow = press(plainButton); await step();
await pumpUntil(() => !!note());
out.away = Object.assign(view(), { restarts: restarts(), phase: w.__srv.phase });
letBack();
out.result = await flow; await step(); await step();
out.after = view();
out.confirms = w.__confirms;
out.restarts = restarts();
out.reads = versionReads().map((f) => `${f.phase}:${f.started}`);
return out;
""".replace("__PW", json.dumps(PASSWORD))

SCENARIOS["flow"] = scenario(FLOW)
# The update unit: its plain restart (nothing new since the last commit it
# applied) is what runs, and the wait ends on that run's result.
SCENARIOS["flow_update_unit"] = scenario(FLOW, srv={"mode": "update"})
# The old server answers two more reads after taking the restart (the bounce
# fires a second after the answer).
SCENARIOS["flow_old_server_lingers"] = scenario(FLOW, srv={"lingerPolls": 2})

# The update unit's plain restart that failed its health check.
SCENARIOS["flow_update_unit_failed"] = scenario(FLOW, srv={
    "mode": "update", "newRunStatus": "failed",
    "newRunError": "health failed (exit 1): not healthy after 120s: core down, web up"})

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
