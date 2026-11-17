"""The dashboard side of the admin idle timeout (2026-11-16).

The server ends an admin sign-in an hour after its last admin change and
says how long is left on every auth answer (``idle_in_sec``,
``idle_timeout_min``; a Bearer status read of a session that is over says
why: ``ended``). Driven through ``jsx_interact_harness.js`` on the REAL
auth.js, data.js, components.jsx and settings.jsx, on the small server
``test_web_credential_refetch`` and ``test_web_admin_reload`` play, with the
idle clock added the way ``web/backend/api/auth.py`` serves it.

Pinned:

* when this tab's count runs out it asks the server once, with the Bearer:
  over, and the page drops to signed out cleanly — no token, no prompt, the
  card and the footer say signed out, and "Signed out after 1 hour without
  admin activity" is said (the card, the toast);
* still live (another tab of this browser acted) and it stays signed in and
  counts again;
* a READ refused 401 for the Bearer after the hour drops the tab the same
  way, with no password prompt over the page;
* a CHANGE someone pressed after the hour opens the password prompt, which
  says why, and replays once signed in;
* where the dashboard explains admin sign-in it says "signs out after 1
  hour idle" (the Admin card, the login prompt);
* a fresh sign-in clears the notice.

No DB, never ``requires_db``; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from domovoi.tests.test_web_admin_reload import MORE_SNAP, RELOAD_PRELUDE
from domovoi.tests.test_web_credential_refetch import (
    EDITABLE,
    FILES,
    HARNESS,
    PRELUDE,
    REPO_ROOT,
    SNAP,
)

# The idle clock, served as web/backend/api/auth.py serves it. Wraps the
# reload prelude's fetch:
#   window.__idle.left   seconds the server says a live session has left
#   window.__idle.ended  why a dead Bearer's session ended ('idle' | 'ended')
#   window.__expire()    the server ends every session (the idle hour passed)
IDLE_PRELUDE = r"""
window.__idle = { left: __IDLE_LEFT, ended: 'idle' };
window.__expire = () => { window.__srv.sessions.clear(); };
const __reloadFetch = fetch;
fetch = async (url, opts) => {
  const o = opts || {};
  const method = String(o.method || 'GET').toUpperCase();
  const path = String(url).replace(/^https?:\/\/[^/]+/, '').split('?')[0];
  const hd = o.headers || {};
  const auth = String(hd.Authorization || '');
  const bearer = auth.startsWith('Bearer ') ? auth.slice(7) : null;
  // A Bearer whose session is over is refused 401 on every admin gate
  // (check_admin_request: "invalid"), the config write included.
  if (method === 'PATCH' && path === '/api/config/editable' && bearer
      && !window.__srv.sessions.has(bearer)) {
    window.__fetches.push({ method, path, who: 'invalid' });
    return __answer(401, { detail: 'admin session required' });
  }
  const res = await __reloadFetch(url, opts);
  const idleFields = (live) => ({ idle_timeout_min: 60,
                                  idle_in_sec: live ? window.__idle.left : null });
  if (method === 'GET' && path === '/api/auth/status' && res.ok) {
    const body = await res.json();
    Object.assign(body, idleFields(!!body.authenticated));
    body.ended = (bearer && !body.authenticated) ? window.__idle.ended : null;
    if (bearer) window.__fetches.push({ method: 'CHECK', path, who: body.authenticated ? 'live' : 'over' });
    return __answer(200, body);
  }
  if (method === 'POST' && (path === '/api/auth/login' || path === '/api/auth/resume') && res.ok) {
    const body = await res.json();
    if (body.token) Object.assign(body, idleFields(true));
    return __answer(200, body);
  }
  return res;
};
"""

# Settings → Configuration (the Admin card + the config card), the sidebar
# footer, the shell's modal host and the signed-out notice.
SHELL = ("(window.__Auth = Auth, function IdleShell() { return React.createElement("
         "React.Fragment, null, React.createElement(SidebarFooter), React.createElement(ConfigPanel),"
         " React.createElement(AuthModalHost), React.createElement(AdminSignedOutNotice)); })")

IDLE_SNAP = r"""
const rowText = (cls) => h.findAll((e) => w.__cls(e).includes(cls)).map((e) => e.text).join(' ');
const notice = () => h.findAll((e) => w.__cls(e).includes('toast-stay-text')).map((e) => e.text).join(' ');
const checks = () => w.__fetches.filter((f) => f.method === 'CHECK').map((f) => f.who);
const idle = () => ({ ...full(), notice: notice(), idleUntil: rowText('admin-idle-until'),
                      signedOutWhy: rowText('admin-signed-out-why'),
                      modalWhy: rowText('login-signed-out-why'),
                      authNotice: A.signedOutNotice ? A.signedOutNotice() : null,
                      checks: checks(), version: A.credentialVersion });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
"""


def scenario(script: str, *, cookie: str | None = "old-session", idle_left: int = 3000,
             left_sec: int | None = 2400, pre: str = "") -> dict:
    sessions = [cookie] if cookie else []
    head = (f"const __LS = {{}}; const __SESSIONS = {json.dumps(sessions)};"
            f" const __COOKIE = {json.dumps(cookie)}; const __EDITABLE = {json.dumps(EDITABLE)};"
            f" const __WINDOW_MIN = 60; const __LEFT_SEC = {json.dumps(left_sec)};"
            f" const __IDLE_LEFT = {json.dumps(idle_left)};\n")
    dump = "\nwindow.__dump = () => { const o = {}; for (const [k, v] of __store) o[k] = v; return o; };\n"
    start = ("const w = h.global('window'); const A = w.__Auth;"
             " const step = () => w.__flush(h);\n")
    start += pre + "\nawait A.ready(); h.render(); await step();\n"
    return {"files": FILES, "component": SHELL, "props": {},
            "setup": head + PRELUDE + dump + RELOAD_PRELUDE + IDLE_PRELUDE,
            "script": start + SNAP + MORE_SNAP + IDLE_SNAP + script}


SCENARIOS: dict[str, dict] = {}

# A reload inside the hour, then nobody does anything: the count runs out
# (one second here), the server says the session is over, the tab drops.
SCENARIOS["count_runs_out_and_the_session_is_over"] = scenario(
    r"""
    const booted = idle();
    w.__expire();
    await sleep(2600); await step();
    return { booted, after: idle() };
    """,
    idle_left=1,
)
# The count runs out but another tab of this browser acted: still live.
SCENARIOS["count_runs_out_but_the_session_lives"] = scenario(
    r"""
    w.__idle.left = 3000;            // what the server says when asked
    await sleep(2600); await step();
    return { after: idle() };
    """,
    idle_left=1,
)
# The hour passed while this tab's timer was late (a background tab): the
# next read is refused for the Bearer, and the tab drops quietly.
SCENARIOS["a_read_refused_after_the_hour_drops_quietly"] = scenario(
    r"""
    const booted = idle();
    w.__expire();
    const read = await w.apiGet('/api/auth/device-token').then(() => 'ok', (e) => e.status);
    await step();
    return { booted, read, after: idle() };
    """,
)
# A change pressed after the hour: the prompt says why, and the change
# goes through once signed in again.
SCENARIOS["a_change_after_the_hour_prompts_and_says_why"] = scenario(
    r"""
    w.__expire();
    await h.type((e) => e.type === 'input' && e.props.value === 'Domovoi', 'Domovoi-2'); await step();
    press(saveBtn); await step(); await step();
    const asked = idle();
    await h.type(password, 'the-admin-password');
    await h.click(modalButton('log in')); await step(); await step();
    return { asked, after: idle(), patches: patches() };
    """,
)
# What the dashboard says about the rule while signed in.
SCENARIOS["the_rule_is_said_where_sign_in_is_explained"] = scenario(
    r"""
    const booted = idle();
    A.openModal(); await step();
    const modalHint = h.findAll((e) => w.__cls(e).includes('hint')
      && h.inside(e, (a) => w.__cls(a).includes('cal-modal'))).map((e) => e.text).join(' ');
    return { booted, modalHint };
    """,
)


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("admin-idle") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), "@" + str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    return _Outcomes(json.loads(proc.stdout))


class _Outcomes(dict):
    def __getitem__(self, name):
        out = super().__getitem__(name)
        if isinstance(out, dict) and "__harness_error" in out:
            pytest.fail(f"{name}: {out['__harness_error']}")
        return out


SIGNED_OUT = "Signed out after 1 hour without admin activity"


def test_when_the_count_runs_out_the_tab_drops_to_signed_out_and_says_why(driven) -> None:
    o = driven["count_runs_out_and_the_session_is_over"]
    b = o["booted"]
    assert b["token"] == "old-session" and "signed in" in b["pills"]
    assert b["idleUntil"]                       # "Signs out at … unless you change something"
    a = o["after"]
    assert a["checks"] == ["over"]              # asked once, with the Bearer
    assert a["token"] is None
    assert a["modal"] is False                  # no prompt nobody asked for
    assert "signed out" in a["pills"] and "signed in" not in a["pills"]
    assert a["who"] == ["Not signed in"]
    assert a["authNotice"] == SIGNED_OUT
    assert SIGNED_OUT in a["signedOutWhy"]
    assert SIGNED_OUT in a["notice"]
    assert a["version"] > b["version"]          # the reads re-read as signed out
    # No admin-only control is left offering what it can't do.
    assert "sign out" not in a["buttons"]
    assert "sign in" in a["buttons"]


def test_when_the_count_runs_out_but_the_session_lives_it_stays_signed_in(driven) -> None:
    a = driven["count_runs_out_but_the_session_lives"]["after"]
    assert a["checks"] == ["live"]
    assert a["token"] == "old-session"
    assert "signed in" in a["pills"] and a["who"] == ["Admin"]
    assert a["authNotice"] is None and a["notice"] == ""
    assert a["idleUntil"]                       # counting again


def test_a_read_refused_after_the_hour_drops_the_tab_with_no_prompt(driven) -> None:
    o = driven["a_read_refused_after_the_hour_drops_quietly"]
    assert o["booted"]["token"] == "old-session"
    assert o["read"] == 401
    a = o["after"]
    assert a["checks"] == ["over"]
    assert a["token"] is None and a["modal"] is False
    assert a["authNotice"] == SIGNED_OUT
    assert SIGNED_OUT in a["notice"]


def test_a_change_after_the_hour_asks_for_the_password_and_says_why(driven) -> None:
    o = driven["a_change_after_the_hour_prompts_and_says_why"]
    asked = o["asked"]
    assert asked["modal"] is True and asked["modalTitle"] == "admin login"
    assert SIGNED_OUT in asked["modalWhy"]
    assert asked["notice"] == ""                # the prompt says it; the toast steps aside
    after = o["after"]
    assert after["modal"] is False and after["token"]
    assert after["authNotice"] is None and after["notice"] == ""   # a sign-in clears it
    # Refused for the dead Bearer, then replayed on the fresh one.
    assert o["patches"] == ["invalid", "bearer"]


def test_the_dashboard_says_signs_out_after_1_hour_idle(driven) -> None:
    o = driven["the_rule_is_said_where_sign_in_is_explained"]
    assert "Signs out after 1 hour idle" in o["booted"]["blob"]   # the Admin card
    assert "Signs out after 1 hour idle" in o["modalHint"]        # the login prompt
