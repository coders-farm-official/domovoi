"""Settings → About & updates → Version on a Windows install: both halves
and the one "Update Domovoi" (design note WINDOWS-ONE-UPDATE-2026-10-22;
domovoi/windows_side.py; web/static/settings.jsx VersionSection).

* a Linux-only server's card is as before, but for the button's name;
* a Windows install shows "Linux side" and "Windows side" (its version and
  build commit, "updates on releases"), and "Windows side is behind" when
  the release the Linux side includes is newer;
* with only the Windows side behind, "Update Domovoi" asks the tray on the
  PC for the Windows step; behind on Linux too, it is one press: one
  question, the pull, then the restart (not asked again);
* the card says "Finish on your PC", "sign in on the PC to finish", the
  half-done update after a declined prompt (with "Try again"), the
  tray's failure with its log, why there is no step (internet "never", no
  signed Setup), and on main that the Windows side follows releases.

Driven through domovoi/tests/jsx_interact_harness.js. No DB; needs node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/settings.jsx"]
C = "c0ffee0123456789abcdef0123456789abcdef01"

_SETUP = r"""
window.__deep = (n) => {
  if (n == null || typeof n === 'boolean') return '';
  if (typeof n === 'string' || typeof n === 'number') return String(n);
  if (Array.isArray(n)) return n.map(window.__deep).join('');
  return n.props ? window.__deep(n.props.children) : '';
};
window.__cls = (e) => String((e && e.props && e.props.className) || '').split(' ');
window.__settle = async (h, n = 4) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
window.__confirms = [];
window.confirm = (m) => { window.__confirms.push(String(m)); return window.__answer !== false; };
"""

_SCRIPT = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
const view = () => {
  const one = (cls) => { const e = h.find((x) => w.__cls(x).includes(cls)); return e ? w.__deep(e).trim() : null; };
  return {
    windows: one('version-windows'), behind: one('version-windows-behind'), request: one('version-windows-request'),
    result: one('version-windows-result'), why: one('version-windows-why'), channel: one('version-windows-channel'),
    buttons: h.findAll({ type: 'button' }).map((b) => w.__deep(b).trim() + (b.props.disabled ? ' [disabled]' : '')).filter(Boolean),
    text: h.text().join(' | '),
  };
};
const before = view();
__ACT__
await w.__settle(h, 6);
return { before, after: view(), confirms: w.__confirms,
         calls: h.calls.map((c) => `${c.method} ${c.path}${c.body && Object.keys(c.body).length ? ' ' + JSON.stringify(c.body) : ''}`) };
"""
NOTHING = ""
CHECK = "await h.click({ type: 'button', text: 'Check for updates' }); await w.__settle(h, 4);"
UPDATE = "await h.click({ type: 'button', text: 'Update Domovoi' });"
RETRY = "await h.click({ type: 'button', text: 'Try again' });"


def _win(**over) -> dict:
    w = {"present": True, "version": "1.0.0", "build_commit": C, "reported_at": "2026-10-22T21:30:00Z",
         "target": {"tag": "v1.0.1", "version": "1.0.1", "commit": C}, "step_needed": True, "why_not": None,
         "follows": "releases", "request": None, "result": None,
         "tray": {"running": True, "seen_at": "2026-10-22T21:31:00Z", "version": "1.0.0"}}
    w.update(over)
    return w


def _version(win: dict | None, **over) -> dict:
    v = {"sha": "4d6b414", "running_sha": "4d6b414", "checkout_sha": "4d6b414",
         "restart_required": False, "restart_capable": True, "restart_mode": "update",
         "restart_in_progress": False, "version": "1.0.1", "checkout_version": "1.0.1",
         "channel": "stable", "channels": ["stable", "main"], "channel_pin": None}
    if win is not None:
        v["windows_side"] = win
    v.update(over)
    return v


def _check(**over) -> dict:
    c = {"behind": 0, "ahead": 0, "upstream": True, "upstream_sha": "f" * 40, "error": None,
         "channel": "stable", "upstream_version": "1.0.1", "latest_release": "1.0.1"}
    c.update(over)
    return c


def _scenario(version: dict, act: str = NOTHING, check: dict | None = None, extra: dict | None = None) -> dict:
    api = {"GET /api/config": {"web_version": "1.0.1"}, "GET /api/config/version": version,
           "GET /api/config/core/restart": {"answering": True},
           "POST /api/config/version/check": check or _check()}
    api.update(extra or {})
    return {"files": FILES, "component": "VersionSection", "setup": _SETUP, "api": api,
            "script": _SCRIPT.replace("__ACT__", act)}


_REQ_OK = {"POST /api/config/version/windows-request": {"ok": True, "request": {"id": "a" * 16}, "tray": {"running": True}}}

SCENARIOS = {
    "linux_only": _scenario(_version({"present": False}), CHECK, _check(behind=2, upstream_version="1.0.2")),
    "windows_behind": _scenario(_version(_win()), UPDATE, extra=_REQ_OK),
    "windows_current": _scenario(_version(_win(step_needed=False, why_not="up_to_date", version="1.0.1"))),
    "both_behind": _scenario(_version(_win()), CHECK + UPDATE, _check(behind=3, upstream_version="1.0.2"), {
        "POST /api/config/version/pull": {"pulled": True, "new_sha": "0a1b2c3"},
        "POST /api/config/version/restart": {"ok": False, "error": "no sudoers grant"},
        **_REQ_OK}),
    "both_behind_declined": _scenario(_version(_win()), CHECK + "w.__answer = false; " + UPDATE,
                                      _check(behind=3, upstream_version="1.0.2")),
    "finish_on_pc": _scenario(_version(_win(request={"id": "a" * 16, "on_this_pc": False, "at": "x"}))),
    "on_this_pc": _scenario(_version(_win(request={"id": "a" * 16, "on_this_pc": True, "at": "x"}))),
    "no_tray": _scenario(_version(_win(request={"id": "a" * 16, "on_this_pc": False, "at": "x"},
                                       tray={"running": False, "seen_at": None, "version": None}))),
    "declined": _scenario(_version(_win(result={"outcome": "declined", "detail": None, "log": None})), RETRY, extra=_REQ_OK),
    "repair_failed": _scenario(_version(_win(result={
        "outcome": "repair_failed", "detail": "Setup couldn't update the Windows side: provision.sh failed at venv.",
        "log": "C:\\Users\\k\\AppData\\Local\\Domovoi\\updates\\20261022-213000-0a1b2c3d\\setup.log"}))),
    "updated": _scenario(_version(_win(step_needed=False, why_not="up_to_date", version="1.0.1",
                                       result={"outcome": "ok", "version": "1.0.1"}))),
    "internet_never": _scenario(_version(_win(step_needed=False, why_not="internet_off"))),
    "unsigned": _scenario(_version(_win(step_needed=False, why_not="unsigned_release"))),
    "main_channel": _scenario(_version(_win(), channel="main", version="1.0.1 + 4 (4d6b414)")),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("version-windows") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_a_linux_only_server_is_as_before_but_for_the_name(driven) -> None:
    o = driven["linux_only"]
    assert o["before"]["windows"] is None and "Windows side" not in o["before"]["text"]
    assert "domovoi" in o["before"]["text"] and "Linux side" not in o["before"]["text"]
    assert "Update Domovoi" in o["after"]["buttons"]
    assert "the restart that loads it" in o["after"]["text"]


def test_a_windows_install_shows_both_halves(driven) -> None:
    b = driven["windows_current"]["before"]
    assert "Linux side" in b["text"] and "Windows side" in b["text"]
    assert b["windows"].startswith("1.0.1") and "(c0ffee0)" in b["windows"] and "updates on releases" in b["windows"]
    assert b["behind"] is None
    assert "Update Domovoi" not in b["buttons"]


def test_behind_on_windows_only_update_asks_the_pc(driven) -> None:
    o = driven["windows_behind"]
    assert o["before"]["behind"] == "Windows side is behind: 1.0.1 is out"
    assert "Update Domovoi" in o["before"]["buttons"]
    assert o["calls"] == ["POST /api/config/version/windows-request"]
    assert o["confirms"] == [], "asking the PC is not a question here: Windows asks there"
    assert "finish on your PC" in o["after"]["text"]


def test_behind_on_both_one_question_then_the_pull_then_the_restart(driven) -> None:
    o = driven["both_behind"]
    assert len(o["confirms"]) == 1, o["confirms"]
    q = o["confirms"][0]
    assert q.startswith("Update Domovoi?") and "First the Linux part" in q and "Then the Windows part" in q
    posts = [c for c in o["calls"] if c.startswith("POST")]
    assert posts[:3] == ["POST /api/config/version/check", "POST /api/config/version/pull",
                         "POST /api/config/version/restart"]
    # The restart failed here: no Windows step is asked for.
    assert "POST /api/config/version/windows-request" not in o["calls"]
    assert "restart failed: no sudoers grant" in o["after"]["text"]


def test_a_declined_question_does_nothing(driven) -> None:
    o = driven["both_behind_declined"]
    assert o["calls"] == ["POST /api/config/version/check"]


def test_finish_on_your_pc(driven) -> None:
    b = driven["finish_on_pc"]["before"]
    assert b["request"].startswith("Finish on your PC: click the Domovoi notification there")
    assert "Update Domovoi" not in b["buttons"], "asked already"
    assert driven["on_this_pc"]["before"]["request"].startswith("Windows asks for an administrator’s permission on this PC")


def test_sign_in_on_the_pc_to_finish(driven) -> None:
    assert driven["no_tray"]["before"]["request"].startswith("Sign in on the PC to finish")


def test_a_declined_prompt_is_half_done_with_try_again(driven) -> None:
    o = driven["declined"]
    assert o["before"]["result"].startswith("The update is half done: the Linux part is updated, the Windows part isn’t")
    assert "Try again" in o["before"]["buttons"]
    assert o["calls"] == ["POST /api/config/version/windows-request"]


def test_a_failed_repair_names_its_log(driven) -> None:
    r = driven["repair_failed"]["before"]["result"]
    assert "provision.sh failed at venv" in r and "setup.log" in r
    assert "Try again" in driven["repair_failed"]["before"]["buttons"]


def test_an_update_that_worked_says_so(driven) -> None:
    assert driven["updated"]["before"]["result"] == "The Windows side updated to 1.0.1."


def test_why_there_is_no_step(driven) -> None:
    assert "internet access is turned off here" in driven["internet_never"]["before"]["why"]
    assert "Update Domovoi" not in driven["internet_never"]["before"]["buttons"]
    assert driven["unsigned"]["before"]["why"].startswith("Domovoi 1.0.1 has no signed Windows Setup")


def test_on_main_the_windows_side_follows_releases(driven) -> None:
    c = driven["main_channel"]["before"]["channel"]
    assert c.startswith("The Linux side follows every change on main; the Windows side follows releases")
    assert "1.0.1" in c
