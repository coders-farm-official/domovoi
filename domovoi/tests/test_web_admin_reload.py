"""The dashboard side of "a reload stays signed in for an hour" (owner
decision 2026-10-14): auth.js, the shell's boot wait, the Settings Admin
card, the Advanced note and the sidebar footer.

Driven through ``jsx_interact_harness.js`` on the REAL auth.js, data.js,
components.jsx and settings.jsx, on top of the small server
``test_web_credential_refetch`` plays (sessions, the cookie jar, login,
logout, the config read and write) with the reload window added the way
``web/backend/api/auth.py`` serves it: ``GET /api/auth/status`` carries
``reload_window_min`` and, for a cookie session inside its window,
``resume_in_sec``; ``POST /api/auth/resume`` answers the cookie's own token.

Pinned:

* a reload inside the window (``Auth.ready()``, what index.html waits on
  before the first render) takes the sign-in back with ONE resume and no
  prompt: the config read goes out with the Bearer, the card says "signed
  in" and until when, the footer says "Admin", and a setting saves with no
  password — the token is never written to storage;
* past the window, or with the window at 0, nothing is asked for and the
  tab is view only exactly as before (the note says how long the window
  is when the server says);
* a resume that could not reach the server leaves the window open, and
  the refused save takes the sign-in back and replays — no prompt;
* a resume the server REFUSES closes the window: the save prompts, and no
  status read asks again;
* "sign out" inside the window needs no password;
* the "until" note goes away when the window closes, with no countdown;
* a password sign-in starts the window (the login answer says so).

No DB, never ``requires_db``; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from domovoi.tests.test_web_credential_refetch import (
    EDITABLE,
    FILES,
    HARNESS,
    PRELUDE,
    REPO_ROOT,
    SNAP,
)

# The reload window, served as web/backend/api/auth.py serves it. Wraps the
# PRELUDE's fetch: status and login answers gain the window fields, and
# POST /api/auth/resume answers the cookie's own session token.
#   window.__win.min       the setting (ADMIN_RELOAD_WINDOW_MIN)
#   window.__win.left      seconds left for the cookie's session (null = shut)
#   window.__resumeDown    the next n resumes never reach the server
#   window.__resumeRefuse  the server refuses every resume (401)
RELOAD_PRELUDE = r"""
window.__win = { min: __WINDOW_MIN, left: __LEFT_SEC };
window.__resumeDown = 0;
window.__resumeRefuse = false;
const __baseFetch = fetch;
fetch = async (url, opts) => {
  const o = opts || {};
  const method = String(o.method || 'GET').toUpperCase();
  const path = String(url).replace(/^https?:\/\/[^/]+/, '').split('?')[0];
  const srv = window.__srv;
  const cookieLive = !!(srv.cookie && srv.sessions.has(srv.cookie));
  const open = window.__win.min > 0 && typeof window.__win.left === 'number' && window.__win.left > 0;
  if (method === 'POST' && path === '/api/auth/resume') {
    const hd = o.headers || {};
    window.__fetches.push({ method, path, who: cookieLive ? 'cookie' : 'none',
                            xrw: hd['X-Requested-With'] || null, creds: o.credentials || null });
    if (window.__resumeDown > 0) { window.__resumeDown -= 1; throw new TypeError('Failed to fetch'); }
    if (!hd['X-Requested-With']) return __answer(403, { detail: 'X-Requested-With header required' });
    if (!cookieLive) return __answer(401, { detail: 'no admin sign-in in this browser to resume' });
    if (!open || window.__resumeRefuse) return __answer(401, { detail: 'sign in again' });
    return __answer(200, { ok: true, token: srv.cookie, reload_window_min: window.__win.min,
                           resume_in_sec: window.__win.left, resume_until: '2026-10-14T12:00:00+00:00' });
  }
  const res = await __baseFetch(url, opts);
  if (method === 'GET' && path === '/api/auth/status' && res.ok) {
    const body = await res.json();
    body.reload_window_min = window.__win.min;
    body.resume_in_sec = cookieLive && open ? window.__win.left : null;
    body.resume_until = body.resume_in_sec ? '2026-10-14T12:00:00+00:00' : null;
    return __answer(200, body);
  }
  if (method === 'POST' && path === '/api/auth/login' && res.ok) {
    const body = await res.json();
    window.__win.left = window.__win.min > 0 ? window.__win.min * 60 : null;
    body.reload_window_min = window.__win.min;
    body.resume_in_sec = window.__win.left;
    return __answer(200, body);
  }
  return res;
};
"""

# Settings → Configuration (the Admin card + the config card, with its
# Advanced note), the sidebar footer, and the shell's modal host.
SHELL = ("(window.__Auth = Auth, function ReloadShell() { return React.createElement("
         "React.Fragment, null, React.createElement(SidebarFooter), React.createElement(ConfigPanel),"
         " React.createElement(AuthModalHost)); })")

MORE_SNAP = r"""
const who = () => h.findAll((e) => w.__cls(e).includes('name')
  && h.inside(e, (a) => w.__cls(a).includes('who'))).map((e) => e.text);
const until = () => h.findAll((e) => w.__cls(e).includes('admin-reload-until')).map((e) => e.text).join(' ');
const viewOnlyNote = () => h.findAll((e) => w.__cls(e).includes('admin-view-only')).map((e) => e.text).join(' ');
const posts = () => w.__fetches.filter((f) => f.method === 'POST').map((f) => `${f.path} ${f.who}`);
const patches = () => w.__fetches.filter((f) => f.method === 'PATCH').map((f) => f.who);
const saveBtn = (e) => e.type === 'button' && e.text.includes('Save')
  && !h.inside(e, (a) => w.__cls(a).includes('cal-modal'));
const press = (sel) => { h.find(sel).props.onClick({ preventDefault() {}, stopPropagation() {} }); };
const stored = () => Object.values(w.__dump());
const full = () => ({ ...snap(), who: who(), until: until(), viewOnlyNote: viewOnlyNote(),
                      token: A.token, canResume: A.canResume ? A.canResume() : null });
"""


def scenario(script: str, *, cookie: str | None = "old-session", window_min: int = 60,
             left_sec: int | None = 2400, pre: str = "") -> dict:
    sessions = [cookie] if cookie else []
    head = (f"const __LS = {{}}; const __SESSIONS = {json.dumps(sessions)};"
            f" const __COOKIE = {json.dumps(cookie)}; const __EDITABLE = {json.dumps(EDITABLE)};"
            f" const __WINDOW_MIN = {json.dumps(window_min)}; const __LEFT_SEC = {json.dumps(left_sec)};\n")
    # localStorage as data.js/auth.js see it, readable from the script.
    dump = "\nwindow.__dump = () => { const o = {}; for (const [k, v] of __store) o[k] = v; return o; };\n"
    start = ("const w = h.global('window'); const A = w.__Auth;"
             " const step = () => w.__flush(h);\n")
    # What index.html does: wait on Auth.ready() before the first render.
    # `pre` runs first (what the server will do to the first requests).
    start += pre + "\nawait A.ready(); h.render(); await step();\n"
    return {"files": FILES, "component": SHELL, "props": {},
            "setup": head + PRELUDE + dump + RELOAD_PRELUDE,
            "script": start + SNAP + MORE_SNAP + script}


SCENARIOS: dict[str, dict] = {}

# A reload 20 minutes after signing in: the shell waits, the sign-in comes
# back, and a setting saves with no prompt.
SCENARIOS["boot_inside_window"] = scenario(
    r"""
    const booted = full();
    const resume = w.__fetches.find((f) => f.path === '/api/auth/resume');
    await h.type((e) => e.type === 'input' && e.props.value === 'Domovoi', 'Domovoi-2'); await step();
    press(saveBtn); await step(); await step();
    return { booted, resume, saved: full(), patches: patches(), posts: posts(),
             stored: stored(), version: A.credentialVersion };
    """,
)
# Past the window: nothing asked for, view only, as before.
SCENARIOS["boot_past_window"] = scenario(
    r"""
    return { booted: full(), posts: posts() };
    """,
    left_sec=None,
)
# The window off (0): today's behaviour, today's words.
SCENARIOS["boot_window_off"] = scenario(
    r"""
    return { booted: full(), posts: posts() };
    """,
    window_min=0,
)
# The resume at boot never reached the server: the window stays open, the
# card says signed in, and the refused save takes the sign-in back first.
SCENARIOS["resume_unreachable_then_save"] = scenario(
    r"""
    const booted = full();
    await h.type((e) => e.type === 'input' && e.props.value === 'Domovoi', 'Domovoi-2'); await step();
    press(saveBtn); await step(); await step();
    return { booted, saved: full(), patches: patches(), posts: posts() };
    """,
    # The shell's resume and the Admin card's status read both miss.
    pre="w.__resumeDown = 2;",
)
# The server refuses the resume (the session ended, or the window shut
# early): view only, the save prompts, and no later status read asks again.
SCENARIOS["resume_refused"] = scenario(
    r"""
    const booted = full();
    await A.refreshStatus(); await step();
    const resumesAfterReread = w.__fetches.filter((f) => f.path === '/api/auth/resume').length;
    await h.type((e) => e.type === 'input' && e.props.value === 'Domovoi', 'Domovoi-2'); await step();
    press(saveBtn); await step();
    return { booted, resumesAfterReread, asked: full(), posts: posts(), patches: patches() };
    """,
    pre="w.__resumeRefuse = true;",
)
# "sign out" on a tab that holds no token yet, inside the window: no password.
SCENARIOS["sign_out_inside_window"] = scenario(
    r"""
    const booted = full();
    await h.click(cardButton('sign out')); await step();
    return { booted, after: full(), posts: posts() };
    """,
    pre="w.__resumeDown = 2;",
)
# The "until" line goes when the window closes; the live tab stays signed in.
SCENARIOS["until_goes_when_the_window_closes"] = scenario(
    r"""
    const booted = full();
    await new Promise((r) => setTimeout(r, 1700)); await step();
    return { booted, later: full() };
    """,
    left_sec=1,
)
# A browser with no session: the password sign-in starts the window.
SCENARIOS["login_starts_the_window"] = scenario(
    r"""
    const before = full();
    A.closeModal(); await step();
    const signIns = h.findAll(cardButton('sign in'));
    await h.click((e) => e === signIns[0]); await step();
    await h.type(password, 'the-admin-password');
    await h.click(modalButton('log in')); await step();
    return { before, after: full(), words: A.reloadWindowWords(), posts: posts() };
    """,
    cookie=None, left_sec=None,
)


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("admin-reload") / "scenarios.json"
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


def test_a_reload_inside_the_window_renders_signed_in_with_no_prompt(driven) -> None:
    o = driven["boot_inside_window"]
    b = o["booted"]
    # One resume, from this page, with the preflight header and the cookie.
    assert o["resume"]["xrw"] == "XMLHttpRequest"
    assert o["resume"]["creds"] == "include"
    assert b["token"] == "old-session"            # the session's own token
    assert b["modal"] is False
    # The shell waited: the config read went out with the Bearer, never the
    # cookie, so Advanced is there and nothing says "view only".
    assert b["editable"] == ["bearer"]
    assert b["advancedToggle"] and b["withheld"] == 0
    assert "signed in" in b["pills"]
    assert "signed in (view only after reload)" not in b["pills"]
    assert b["who"] == ["Admin"]
    assert "keeps you signed in until" in b["until"]
    assert b["viewOnlyNote"] == ""


def test_a_setting_saves_after_the_reload_with_no_password(driven) -> None:
    o = driven["boot_inside_window"]
    assert o["patches"] == ["bearer"]
    assert "saved ✓" in o["saved"]["blob"]
    assert o["saved"]["modal"] is False
    assert o["posts"] == ["/api/auth/resume cookie"]


def test_the_resumed_token_is_never_written_to_storage(driven) -> None:
    o = driven["boot_inside_window"]
    assert all("old-session" not in str(v) for v in o["stored"])


def test_past_the_window_a_reload_is_view_only_and_asks_nothing(driven) -> None:
    o = driven["boot_past_window"]
    b = o["booted"]
    assert o["posts"] == []
    assert b["token"] is None and b["modal"] is False
    assert b["editable"] == ["cookie"] and b["withheld"] == 1
    assert "signed in (view only after reload)" in b["pills"]
    assert b["who"] == ["Admin (view only)"]
    assert b["until"] == ""
    assert "more than an hour after you signed in" in b["viewOnlyNote"]
    assert "more than an hour after you signed in" in b["withheldText"]
    assert "sign in again" in b["buttons"]


def test_with_the_window_off_it_is_exactly_as_before(driven) -> None:
    o = driven["boot_window_off"]
    b = o["booted"]
    assert o["posts"] == []
    assert "signed in (view only after reload)" in b["pills"]
    assert "A page reload keeps this browser signed in for viewing" in b["viewOnlyNote"]
    assert "reload keeps you signed in for viewing" in b["withheldText"]


def test_an_unreachable_resume_leaves_the_window_and_the_save_resumes(driven) -> None:
    o = driven["resume_unreachable_then_save"]
    b = o["booted"]
    # Not in memory yet, but the browser can take it back: signed in.
    assert b["token"] is None and b["canResume"] is True
    assert "signed in" in b["pills"] and b["who"] == ["Admin"]
    # The save was refused for the cookie, the sign-in came back, the
    # save replayed with it — and no prompt at any point.
    assert o["patches"] == ["cookie", "bearer"]
    assert o["saved"]["modal"] is False
    assert "saved ✓" in o["saved"]["blob"]
    assert o["posts"] == ["/api/auth/resume cookie"] * 3


def test_a_refused_resume_is_view_only_prompts_and_is_not_asked_again(driven) -> None:
    o = driven["resume_refused"]
    b = o["booted"]
    assert b["token"] is None and b["canResume"] is False
    assert "signed in (view only after reload)" in b["pills"]
    assert o["resumesAfterReread"] == 1
    assert o["asked"]["modal"] is True and o["asked"]["modalTitle"] == "admin login"
    assert o["posts"] == ["/api/auth/resume cookie"]
    assert o["patches"] == ["cookie"]


def test_sign_out_inside_the_window_needs_no_password(driven) -> None:
    o = driven["sign_out_inside_window"]
    assert o["booted"]["canResume"] is True
    after = o["after"]
    assert after["modal"] is False
    assert after["cookie"] is None
    assert "signed out" in after["pills"]
    assert o["posts"] == ["/api/auth/resume cookie"] * 3 + ["/api/auth/logout bearer"]


def test_the_until_line_goes_when_the_window_closes(driven) -> None:
    o = driven["until_goes_when_the_window_closes"]
    assert "keeps you signed in until" in o["booted"]["until"]
    later = o["later"]
    assert later["until"] == ""
    # This tab still holds the sign-in: it is a reload that would not.
    assert "signed in" in later["pills"] and later["who"] == ["Admin"]


def test_a_password_sign_in_starts_the_window(driven) -> None:
    o = driven["login_starts_the_window"]
    assert o["before"]["who"] == ["Not signed in"]
    after = o["after"]
    assert after["modal"] is False and after["token"]
    assert "keeps you signed in until" in after["until"]
    assert o["words"] == "an hour"
    assert "/api/auth/resume none" not in o["posts"]
