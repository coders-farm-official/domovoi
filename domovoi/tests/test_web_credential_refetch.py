"""Reads whose ANSWER depends on the credential follow every sign-in and
sign-out — and Settings → Configuration shows Advanced after a sign-in.

LIVE BUG (owner, 2026-09-30): Settings → Configuration said "Advanced
settings — database URL, ports, paths — need an admin sign-in on this
browser" and Advanced never appeared, through "sign out", "sign in" and a
reload. The core sends the advanced section (and the unmasked secrets)
only to a live admin Bearer (CORE-6); the dashboard keeps that Bearer in
JS memory only, so a reload leaves the HttpOnly cookie, which is
view-only. ConfigPanel read ``/api/config/editable`` with
``useApiObject``, which re-read after a credential change ONLY when the
previous read had been refused 401/403 — and the cookie-only read
SUCCEEDS. So signing in never re-read it (switching Settings tabs, which
remounts the panel, was the one accidental way in), a reload dropped the
Bearer again, and signing out left the advanced values and unmasked
secrets on screen. A cookie-only "sign out" did nothing at all: the
server revokes a session only for a Bearer.

What this module pins, driven through ``jsx_interact_harness.js`` on top
of the REAL auth.js and data.js (only ``fetch`` is scripted — PRELUDE
below plays a small server that answers by the credential a request
actually carried):

* ``refetchOnAuth`` (useApiObject and useApiList): ONE re-read per
  credential change — pairing, sign-in, sign-out, unpairing — and none for
  a notify that changed nothing (a modal opening or closing); the answer
  dropped the moment a credential goes, before the re-read lands; an
  answer to a request sent under the old credential thrown away when it
  lands late; the re-read quiet, so a refused one opens no prompt;
* the post-sign-in retry of an ordinary (not opted-in) read still runs
  AT MOST ONCE per error — a read refused against a fresh Bearer does not
  loop the login modal;
* ConfigPanel: the withheld note says honestly what a reload keeps and
  what it forgets, and its "sign in" button opens the login modal and, on
  success, shows Advanced — opened — without a reload; signing out takes
  the advanced values and the unmasked secret off the screen at once and
  opens no prompt;
* the Admin card tells a live admin sign-in in this tab from the cookie a
  reload keeps ("signed in (view only after reload)", with "sign in
  again" beside "sign out"), and a view-only tab's "sign out" asks for the
  password once and then really revokes the session.

No DB, never ``requires_db``; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/auth.js", "web/static/data.js", "web/static/components.jsx",
         "web/static/settings.jsx"]

SECRET_DB = "postgresql+asyncpg://domovoi:SECRETPW@127.0.0.1:6432/domovoi"
SECRET_KEY = "ACOUSTID-REAL-KEY"
MASK = "••••-KEY"


def _field(name, label, group, section, typ, value, *, secret=False, masked=False, tier="hot"):
    return {"name": name, "label": label, "group": group, "section": section, "tier": tier,
            "type": typ, "min": None, "max": None, "choices": None, "unit": None,
            "help": label, "secret": secret, "masked": masked, "value": value}


# /api/config/editable as the core answers each caller (domovoi/main.py
# admin_get_config): a Bearer reads everything; the cookie reads the common
# section with secrets masked and no advanced section; nothing is a 401.
EDITABLE = {
    "bearer": {"fields": [
        _field("bot_name", "Bot name", "General", "common", "str", "Domovoi"),
        _field("acoustid_api_key", "AcoustID key", "Music", "common", "str", SECRET_KEY, secret=True),
        _field("database_url", "Database URL", "Infrastructure", "advanced", "str", SECRET_DB,
               secret=True, tier="restart"),
        _field("web_port", "Web port", "Infrastructure", "advanced", "int", 6369, tier="restart"),
    ], "plugin_fields": [], "advanced_available": True},
    "cookie": {"fields": [
        _field("bot_name", "Bot name", "General", "common", "str", "Domovoi"),
        _field("acoustid_api_key", "AcoustID key", "Music", "common", "str", MASK,
               secret=True, masked=True),
    ], "plugin_fields": [], "advanced_available": False},
}

# ─── the in-sandbox prelude: a server that answers by credential ─────────

PRELUDE = r"""
const __st = setTimeout;
setTimeout = (fn, ms, ...a) => { const t = __st(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };
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

// The server: admin sessions (a Bearer, or the cookie jar's one session
// token) and the household token. Login mints a session and sets the
// cookie; logout revokes the CALLING Bearer's session and clears the
// cookie, and without a Bearer is a 401 — exactly web/backend/api/auth.py.
window.__srv = { sessions: new Set(__SESSIONS), cookie: __COOKIE, n: 0, device: 'house-token' };
window.__fetches = [];
// `__hold[key] = n` holds the next n answers to `key` until __release():
// the answer is computed when the request is SENT (under the credential
// it carried) and delivered later.
window.__hold = {};
window.__held = [];
window.__release = () => { const x = window.__held.shift(); if (x) x.deliver(); return !!x; };
const __answer = (status, body) => {
  const text = body == null ? '' : JSON.stringify(body);
  return { ok: status >= 200 && status < 300, status, statusText: String(status),
           text: async () => text, json: async () => JSON.parse(text) };
};
const __ADMIN_401 = { detail: 'admin session required' };
const __route = (method, path, c) => {
  const srv = window.__srv;
  const admin = c.who === 'bearer' || c.who === 'cookie';
  if (method === 'POST' && path === '/api/auth/login') {
    srv.n += 1;
    const token = `session-${srv.n}`;
    srv.sessions.add(token); srv.cookie = token;
    return __answer(200, { ok: true, token });
  }
  if (method === 'POST' && path === '/api/auth/logout') {
    if (c.who !== 'bearer') return __answer(401, { detail: 'Bearer token required' });
    srv.sessions.delete(c.bearer); srv.cookie = null;
    return __answer(200, { ok: true, revoked: true });
  }
  if (method !== 'GET') return __answer(404, { detail: 'not found' });
  const bare = path.split('?')[0];
  if (bare === '/api/auth/status') return __answer(200, { setup_complete: true, authenticated: admin });
  if (bare === '/api/auth/device-token') {
    return admin ? __answer(200, { token: srv.device, header: 'X-Device-Token' }) : __answer(401, __ADMIN_401);
  }
  if (bare === '/api/config/editable') {
    return __EDITABLE[c.who] ? __answer(200, __EDITABLE[c.who]) : __answer(401, __ADMIN_401);
  }
  if (bare === '/api/config') return __answer(200, { bot_name: 'Domovoi', web_version: 'test' });
  if (bare === '/api/config/version') return __answer(200, { sha: 'abc1234', restart_required: false });
  // A read that answers every credential differently.
  if (bare === '/api/probe' || bare === '/api/probe/list') {
    const who = admin ? c.who : (c.household ? 'device' : null);
    if (!who) return __answer(401, __ADMIN_401);
    return __answer(200, bare === '/api/probe' ? { who } : [{ who }]);
  }
  // A web→core hop that drops the credential: refused whatever is sent.
  if (bare === '/api/broken') return __answer(401, __ADMIN_401);
  return __answer(404, { detail: 'not found' });
};
fetch = async (url, opts) => {
  const o = opts || {};
  const method = String(o.method || 'GET').toUpperCase();
  const path = String(url).replace(/^https?:\/\/[^/]+/, '');
  const hd = o.headers || {};
  const srv = window.__srv;
  const auth = String(hd.Authorization || '');
  const bearer = auth.startsWith('Bearer ') ? auth.slice(7) : null;
  const device = hd['X-Device-Token'] || null;
  const who = bearer ? (srv.sessions.has(bearer) ? 'bearer' : 'invalid')
    : (srv.cookie && srv.sessions.has(srv.cookie) ? 'cookie' : 'none');
  const household = who === 'bearer' || who === 'cookie' || device === srv.device;
  window.__fetches.push({ method, path, who, device });
  const res = __route(method, path, { who, bearer, device, household });
  const key = `${method} ${path}`;
  if (window.__hold[key]) {
    window.__hold[key] -= 1;
    return new Promise((resolve) => { window.__held.push({ key, deliver: () => resolve(res) }); });
  }
  return res;
};
window.__flush = async (h, n = 8) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
window.__gets = (p) => window.__fetches.filter((f) => f.method === 'GET' && f.path === p);
window.__cls = (e) => String((e.props && e.props.className) || '').split(' ');
"""

# A probe per hook shape; each renders one line of text saying what it holds.
PROBE = r"""(() => {
  window.__Auth = Auth;
  const line = (o, v) => (o.loading ? 'loading' : (o.error ? `error ${o.error.status}` : v));
  const ObjectProbe = () => {
    const o = useApiObject(__PROBE_PATH, __PROBE_OPTS);
    window.__probe = o;
    return React.createElement('div', { className: 'probe' }, line(o, o.data ? o.data.who : 'empty'));
  };
  const ListProbe = () => {
    const o = useApiList(__PROBE_PATH, __PROBE_OPTS);
    window.__probe = o;
    return React.createElement('div', { className: 'probe' },
      line(o, o.items.map((x) => x.who).join(',') || 'empty'));
  };
  return __PROBE_KIND === 'list' ? ListProbe : ObjectProbe;
})()"""

# Settings → Configuration as the page mounts it, plus the shell's modal
# host, so the login modal is really rendered, typed into and submitted.
CONFIG = ("(window.__Auth = Auth, function ConfigWithModal() { return React.createElement("
          "React.Fragment, null, React.createElement(ConfigPanel), React.createElement(AuthModalHost)); })")


def scenario(script: str, *, component: str = CONFIG, ls: dict | None = None,
             cookie: str | None = None, probe: tuple | None = None) -> dict:
    sessions = [cookie] if cookie else []
    head = (f"const __LS = {json.dumps(ls or {})}; const __SESSIONS = {json.dumps(sessions)};"
            f" const __COOKIE = {json.dumps(cookie)}; const __EDITABLE = {json.dumps(EDITABLE)};\n")
    if probe:
        kind, path, opts = probe
        head += (f"const __PROBE_KIND = {json.dumps(kind)}; const __PROBE_PATH = {json.dumps(path)};"
                 f" const __PROBE_OPTS = {json.dumps(opts)};\n")
    return {"files": FILES, "component": component, "props": {}, "setup": head + PRELUDE,
            "script": "const w = h.global('window'); const A = w.__Auth;"
                      " const step = () => w.__flush(h);"
                      " const view = () => h.text().filter((t) => t).join('|');"
                      " h.render(); await step();\n" + script}


# What the Settings scenarios look at after each step.
SNAP = r"""
const snap = () => {
  const blob = h.tree().map((e) => [e.text].concat(Object.values(e.props || {})
    .filter((v) => typeof v === 'string' || typeof v === 'number').map(String)).join(' ')).join('\n');
  const pills = h.findAll((e) => w.__cls(e).includes('pill')).map((e) => e.text).filter((t) => t);
  const inModal = (e) => h.inside(e, (a) => w.__cls(a).includes('cal-modal'));
  return {
    blob,
    pills,
    withheld: h.findAll((e) => w.__cls(e).includes('config-advanced-withheld')).length,
    withheldText: h.findAll((e) => h.inside(e, (a) => w.__cls(a).includes('config-advanced-withheld')))
      .map((e) => e.text).filter((t) => t).join(' '),
    advancedToggle: !!h.find((e) => e.type === 'button' && e.text.includes('Advanced')),
    inputs: h.findAll({ type: 'input' }).filter((e) => !inModal(e)).map((e) => e.props.value),
    buttons: h.findAll({ type: 'button' }).filter((e) => !inModal(e)).map((e) => e.text.trim()),
    modal: A.modalOpen,
    modalTitle: (h.find((e) => w.__cls(e).includes('ttl')) || {}).text || null,
    signOutWhy: !!h.find((e) => w.__cls(e).includes('login-sign-out-why')),
    editable: w.__gets('/api/config/editable').map((f) => f.who),
    cookie: w.__srv.cookie,
  };
};
const noteSignIn = (e) => e.type === 'button' && e.text.includes('sign in')
  && h.inside(e, (a) => w.__cls(a).includes('config-advanced-withheld'));
const cardButton = (label) => (e) => e.type === 'button' && e.text.trim() === label
  && !h.inside(e, (a) => w.__cls(a).includes('cal-modal'))
  && !h.inside(e, (a) => w.__cls(a).includes('config-advanced-withheld'));
const modalButton = (label) => (e) => e.type === 'button' && e.text.trim() === label
  && h.inside(e, (a) => w.__cls(a).includes('cal-modal-foot'));
const password = (e) => e.type === 'input' && e.props.type === 'password';
"""

SCENARIOS: dict[str, dict] = {}

# ─── the hooks ───────────────────────────────────────────────────────────

# An opted-in object read through every kind of credential change. The
# browser starts with nothing: the first read is refused (and, being an
# ordinary read, opens the sign-in prompt).
SCENARIOS["object_opt_in"] = scenario(
    r"""
    const n = () => w.__gets('/api/probe').length;
    const out = { first: { view: view(), reads: n(), modal: A.modalOpen } };
    A.closeModal(); await step(); A.openModal(); await step(); A.closeModal(); await step();
    out.modalOnly = { reads: n() };
    A.pair('house-token'); await step();
    out.paired = { view: view(), reads: n() };
    await A.login('pw'); await step();
    out.signedIn = { view: view(), reads: n(), sentWith: w.__gets('/api/probe').pop().who };
    // Sign out with the re-read held: what is on screen in between.
    w.__hold['GET /api/probe'] = 1;
    await A.logout(); await step();
    out.signingOut = { view: view(), reads: n() };
    w.__release(); await step();
    out.signedOut = { view: view(), reads: n(), sentWith: w.__gets('/api/probe').pop().who };
    A.unpair(); await step();
    out.unpaired = { view: view(), reads: n(), modal: A.modalOpen, pair: A.pairModalOpen };
    return out;
    """,
    component=PROBE, probe=("object", "/api/probe", {"refetchOnAuth": True}),
)
# A request sent under the admin sign-in lands AFTER the sign-out's re-read.
SCENARIOS["object_stale_answer"] = scenario(
    r"""
    await A.login('pw'); await step();
    const signedIn = view();
    w.__hold['GET /api/probe'] = 1;
    w.__probe.refresh();                  // sent under the Bearer; held
    await A.logout(); await step();       // dropped; the re-read is not held
    const afterSignOut = view();
    const released = w.__release(); await step();
    return { signedIn, afterSignOut, released, after: view(),
             sent: w.__gets('/api/probe').map((f) => f.who) };
    """,
    component=PROBE, probe=("object", "/api/probe", {"refetchOnAuth": True}),
    ls={"domovoi-device-token": "house-token"},
)
SCENARIOS["list_opt_in"] = scenario(
    r"""
    const n = () => w.__gets('/api/probe/list').length;
    const out = { first: { view: view(), reads: n() } };
    await A.login('pw'); await step();
    out.signedIn = { view: view(), reads: n() };
    w.__hold['GET /api/probe/list'] = 1;
    await A.logout(); await step();
    out.signingOut = { view: view(), reads: n() };
    w.__release(); await step();
    // The sign-in paired this browser, and a sign-out keeps that.
    out.signedOut = { view: view(), reads: n() };
    A.unpair(); await step();
    out.unpaired = { view: view(), reads: n(), modal: A.modalOpen, pair: A.pairModalOpen };
    return out;
    """,
    component=PROBE, probe=("list", "/api/probe/list", {"refetchOnAuth": True}),
    cookie="old-session",
)
# An ORDINARY read (not opted in) that a fresh Bearer cannot fix: one retry
# per error, however many notifies and sign-ins follow.
SCENARIOS["retry_guard"] = scenario(
    r"""
    const n = () => w.__gets('/api/broken').length;
    const out = { first: { reads: n(), modal: A.modalOpen } };
    A.closeModal(); await step();
    await A.login('pw'); await step();
    out.signedIn = { reads: n(), modal: A.modalOpen };
    A.closeModal(); await step(); A.openModal(); await step(); A.closeModal(); await step();
    out.modalOnly = { reads: n() };
    await A.login('pw'); await step();
    out.signedInAgain = { reads: n(), view: view() };
    return out;
    """,
    component=PROBE, probe=("object", "/api/broken", {}),
)
# The same broken read, opted in: one quiet re-read per credential change.
SCENARIOS["opt_in_refused_does_not_loop"] = scenario(
    r"""
    const n = () => w.__gets('/api/broken').length;
    const out = { first: { reads: n(), modal: A.modalOpen } };
    A.closeModal(); await step();
    await A.login('pw'); await step();
    out.signedIn = { reads: n(), modal: A.modalOpen };
    A.openModal(); await step(); A.closeModal(); await step();
    out.modalOnly = { reads: n() };
    A.pair('house-token'); await step();
    await A.login('pw'); await step();
    out.later = { reads: n(), modal: A.modalOpen, view: view() };
    return out;
    """,
    component=PROBE, probe=("object", "/api/broken", {"refetchOnAuth": True}),
)

# ─── Settings → Configuration ────────────────────────────────────────────

# The owner's browser: reloaded, so it holds the cookie and no Bearer.
SCENARIOS["config_note_sign_in"] = scenario(
    SNAP + r"""
    const before = snap();
    await h.click(noteSignIn); await step();
    const prompted = snap();
    await h.type(password, 'the-admin-password');
    await h.click(modalButton('log in')); await step();
    const signedIn = snap();
    return { before, prompted, signedIn };
    """,
    cookie="old-session",
)
# Signed in, then signed out: the re-read is held so the screen in between
# can be read.
SCENARIOS["config_sign_out"] = scenario(
    SNAP + r"""
    await h.click(noteSignIn); await step();
    await h.type(password, 'the-admin-password');
    await h.click(modalButton('log in')); await step();
    const signedIn = snap();
    w.__hold['GET /api/config/editable'] = 1;
    await h.click(cardButton('sign out')); await step();
    const signingOut = snap();
    w.__release(); await step();
    return { signedIn, signingOut, signedOut: snap() };
    """,
    cookie="old-session",
)
SCENARIOS["config_sign_in_again"] = scenario(
    SNAP + r"""
    await h.click(cardButton('sign in again')); await step();
    const prompted = snap();
    await h.type(password, 'the-admin-password');
    await h.click(modalButton('log in')); await step();
    return { prompted, signedIn: snap() };
    """,
    cookie="old-session",
)
# A view-only tab's "sign out": the password once, then a real revoke.
SCENARIOS["config_view_only_sign_out"] = scenario(
    SNAP + r"""
    await h.click(cardButton('sign out')); await step();
    const asked = snap();
    await h.type(password, 'the-admin-password');
    await h.click(modalButton('sign out')); await step();
    const posts = w.__fetches.filter((f) => f.method === 'POST').map((f) => `${f.path} ${f.who}`);
    return { asked, after: snap(), posts };
    """,
    cookie="old-session",
)
SCENARIOS["config_view_only_sign_out_cancelled"] = scenario(
    SNAP + r"""
    await h.click(cardButton('sign out')); await step();
    await h.click(modalButton('cancel')); await step();
    const posts = w.__fetches.filter((f) => f.method === 'POST').map((f) => f.path);
    return { after: snap(), posts };
    """,
    cookie="old-session",
)
# A browser with no session at all: the read is refused, and the card
# offers the sign-in itself, not only "retry".
SCENARIOS["config_signed_out"] = scenario(
    SNAP + r"""
    const before = { ...snap(), signIns: h.findAll(cardButton('sign in')).length };
    A.closeModal(); await step();
    // The Admin card's "sign in" comes first; the config card's is last.
    const signIns = h.findAll(cardButton('sign in'));
    const cardSignIn = signIns[signIns.length - 1];
    await h.click((e) => e === cardSignIn); await step();
    const prompted = snap();
    await h.type(password, 'the-admin-password');
    await h.click(modalButton('log in')); await step();
    return { before, prompted, signedIn: snap() };
    """,
)


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("credential-refetch") / "scenarios.json"
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


# ─── refetchOnAuth ───────────────────────────────────────────────────────


def test_one_re_read_per_credential_change_and_none_for_a_modal(driven) -> None:
    o = driven["object_opt_in"]
    # The first read is an ordinary one: refused, and it asks.
    assert o["first"] == {"view": "error 401", "reads": 1, "modal": True}
    # Modals opening and closing change no credential: nothing re-read.
    assert o["modalOnly"] == {"reads": 1}
    assert o["paired"] == {"view": "device", "reads": 2}
    assert o["signedIn"] == {"view": "bearer", "reads": 3, "sentWith": "bearer"}


def test_signing_in_rereads_a_read_that_had_succeeded(driven) -> None:
    """The bug itself, on the bare hook: the cookie's read SUCCEEDED, so
    the old retry-after-a-refusal never ran for it."""
    o = driven["list_opt_in"]
    assert o["first"] == {"view": "cookie", "reads": 1}
    assert o["signedIn"] == {"view": "bearer", "reads": 2}


def test_signing_out_drops_the_answer_before_the_re_read_lands(driven) -> None:
    o = driven["object_opt_in"]
    assert o["signingOut"] == {"view": "loading", "reads": 4}
    # Still paired: the household token outlives an admin sign-out.
    assert o["signedOut"] == {"view": "device", "reads": 4, "sentWith": "none"}
    lst = driven["list_opt_in"]
    assert lst["signingOut"] == {"view": "loading", "reads": 3}


def test_a_refused_re_read_is_quiet(driven) -> None:
    """Nobody pressed anything: the read after an unpairing (or a sign-out
    on a browser left with nothing) is refused without a prompt."""
    o = driven["object_opt_in"]["unpaired"]
    assert o == {"view": "error 401", "reads": 5, "modal": False, "pair": False}
    lst = driven["list_opt_in"]
    assert lst["signedOut"] == {"view": "device", "reads": 3}
    assert lst["unpaired"] == {"view": "error 401", "reads": 4, "modal": False, "pair": False}


def test_an_answer_sent_under_the_old_credential_is_thrown_away(driven) -> None:
    o = driven["object_stale_answer"]
    assert o["signedIn"] == "bearer"
    assert o["afterSignOut"] == "device"
    assert o["released"] is True
    # The Bearer's answer landed last, and was not shown.
    assert o["after"] == "device"
    # (`none` is a request with no admin credential: the household token
    # rode along, so the server answered `device`.)
    assert o["sent"] == ["none", "bearer", "bearer", "none"]


def test_an_ordinary_read_still_retries_at_most_once_per_error(driven) -> None:
    """The retry guard the opt-in must not disturb: a read that a fresh
    Bearer does not fix is retried ONCE, re-opens the prompt once, and
    nothing after that — no modal loop."""
    o = driven["retry_guard"]
    assert o["first"] == {"reads": 1, "modal": True}
    assert o["signedIn"] == {"reads": 2, "modal": True}
    assert o["modalOnly"] == {"reads": 2}
    assert o["signedInAgain"] == {"reads": 2, "view": "error 401"}


def test_an_opted_in_read_that_stays_refused_does_not_loop(driven) -> None:
    o = driven["opt_in_refused_does_not_loop"]
    assert o["first"] == {"reads": 1, "modal": True}
    assert o["signedIn"] == {"reads": 2, "modal": False}
    assert o["modalOnly"] == {"reads": 2}
    assert o["later"] == {"reads": 4, "modal": False, "view": "error 401"}


# ─── Settings → Configuration ────────────────────────────────────────────


def test_a_reloaded_tab_says_what_it_kept_and_what_it_forgot(driven) -> None:
    before = driven["config_note_sign_in"]["before"]
    assert before["editable"] == ["cookie"]
    assert before["withheld"] == 1 and not before["advancedToggle"]
    note = before["withheldText"]
    assert "need an admin sign-in in this tab" in note
    assert "reload keeps you signed in for viewing" in note
    assert "forgets the admin sign-in" in note and "Advanced and saving changes" in note
    # The secret reads back masked; nothing advanced is on the page.
    assert MASK in before["inputs"] and SECRET_KEY not in before["blob"]
    assert SECRET_DB not in before["blob"]
    # The Admin card: view only, with both ways out.
    assert "signed in (view only after reload)" in before["pills"]
    assert "sign in again" in before["buttons"] and "sign out" in before["buttons"]
    assert "reload keeps this browser signed in for viewing" in before["blob"]


def test_the_notes_sign_in_button_opens_the_login_modal(driven) -> None:
    prompted = driven["config_note_sign_in"]["prompted"]
    assert prompted["modal"] is True
    assert prompted["modalTitle"] == "admin login"


def test_signing_in_from_the_note_shows_advanced_without_a_reload(driven) -> None:
    after = driven["config_note_sign_in"]["signedIn"]
    # ONE re-read, under the new Bearer.
    assert after["editable"] == ["cookie", "bearer"]
    assert after["modal"] is False
    assert after["withheld"] == 0 and after["advancedToggle"]
    # Opened, because that is what the button was pressed for.
    assert SECRET_DB in after["inputs"] and 6369 in after["inputs"]
    assert SECRET_KEY in after["inputs"] and MASK not in after["inputs"]
    assert "signed in" in after["pills"]
    assert "signed in (view only after reload)" not in after["pills"]
    assert "sign in again" not in after["buttons"]


def test_signing_out_takes_the_advanced_values_off_the_screen_at_once(driven) -> None:
    o = driven["config_sign_out"]
    assert SECRET_DB in o["signedIn"]["inputs"]
    mid = o["signingOut"]
    # The re-read has not landed, and the privileged answer is already gone.
    assert mid["editable"] == ["cookie", "bearer", "none"]
    assert SECRET_DB not in mid["blob"] and SECRET_KEY not in mid["blob"]
    assert not mid["advancedToggle"]
    assert "loading settings…" in mid["blob"]
    end = o["signedOut"]
    assert SECRET_DB not in end["blob"] and SECRET_KEY not in end["blob"]
    assert "admin login required" in end["blob"]
    # A sign-out is not answered with a sign-in prompt.
    assert end["modal"] is False
    assert end["cookie"] is None
    assert "signed out" in end["pills"]


def test_sign_in_again_on_the_admin_card_brings_advanced_back(driven) -> None:
    o = driven["config_sign_in_again"]
    assert o["prompted"]["modal"] is True
    after = o["signedIn"]
    assert after["editable"] == ["cookie", "bearer"]
    assert after["advancedToggle"] and after["withheld"] == 0
    assert "signed in" in after["pills"]


def test_a_view_only_tab_signs_out_for_real(driven) -> None:
    """The server revokes a session only for a Bearer, so the cookie
    alone used to be refused and the tab went on being signed in after
    the next reload. It asks for the password once, and says why."""
    o = driven["config_view_only_sign_out"]
    asked = o["asked"]
    assert asked["modal"] is True
    assert asked["modalTitle"] == "sign out"
    assert asked["signOutWhy"] is True
    assert o["posts"] == ["/api/auth/login cookie", "/api/auth/logout bearer"]
    after = o["after"]
    assert after["cookie"] is None
    assert "signed out" in after["pills"]
    assert after["modal"] is False
    assert SECRET_DB not in after["blob"] and "admin login required" in after["blob"]


def test_cancelling_a_view_only_sign_out_changes_nothing(driven) -> None:
    o = driven["config_view_only_sign_out_cancelled"]
    assert o["posts"] == []
    after = o["after"]
    assert after["cookie"] == "old-session"
    assert "signed in (view only after reload)" in after["pills"]
    assert after["modal"] is False and after["withheld"] == 1


def test_a_refused_config_read_offers_the_sign_in_itself(driven) -> None:
    o = driven["config_signed_out"]
    assert o["before"]["editable"] == ["none"]
    assert "admin login required" in o["before"]["blob"]
    assert o["before"]["signIns"] == 2        # the Admin card's, and the config card's
    assert "retry" in o["before"]["buttons"]
    assert o["prompted"]["modal"] is True
    after = o["signedIn"]
    assert after["editable"] == ["none", "bearer"]
    assert after["advancedToggle"] and SECRET_KEY in after["inputs"]


# ─── the audit: every credential-dependent read is opted in ──────────────

# (file, the hook call's path as written) for every dashboard read whose
# ANSWER — not only whether it is allowed — depends on the credential.
CREDENTIAL_DEPENDENT_READS = [
    # CORE-6: advanced section + unmasked secrets for a live Bearer only.
    ("settings.jsx", "useApiObject('/api/config/editable'"),
    # The same registry, through the Models tab: Whisper model/device/
    # compute type are advanced settings.
    ("models.jsx", "useApiObject('/api/models/active'"),
    # Rules M1 / F1: a reminder's words and the whole fire ledger for a
    # household credential only.
    ("home.jsx", "useApiObject('/api/timers'"),
    ("satellites.jsx", "useApiObject(`/api/timers/fires?room_id="),
    ("satellites.jsx", "useApiList(`/api/satellites/${room}/timers`"),
]


@pytest.mark.parametrize("name,call", CREDENTIAL_DEPENDENT_READS)
def test_every_credential_dependent_read_re_reads_on_a_credential_change(name, call) -> None:
    src = (STATIC / name).read_text(encoding="utf-8")
    assert src.count(call) == 1, f"{call} in {name}"
    start = src.index(call)
    end = src.index(");", start)
    assert "refetchOnAuth: true" in src[start:end], f"{call} in {name} is not refetchOnAuth"


def test_the_timer_alert_cards_re_read_on_a_credential_change() -> None:
    """TimerFireAlerts reads by hand (apiGet), not through the hooks: it
    carries its own once-per-credential re-read (driven for real in
    test_web_timer_fire_alerts.py)."""
    src = (STATIC / "components.jsx").read_text(encoding="utf-8")
    start = src.index("const TimerFireAlerts = ")
    body = src[start:src.index("\n};\n", start)]
    assert "Auth.credentialVersion" in body and "rereadRef.current()" in body


def test_opting_in_is_not_the_default() -> None:
    """A read that is merely allowed-or-refused must not re-read on every
    change: the default stays off in both hooks."""
    data = (STATIC / "data.js").read_text(encoding="utf-8")
    for hook in ("useApiList", "useApiObject"):
        sig = re.search(rf"^const {hook} = \(path, \{{([^}}]*)\}} = \{{\}}\) => \{{", data, re.M | re.S)
        assert sig, hook
        assert "refetchOnAuth = false" in sig.group(1), hook
