"""The Mac side's updates (mac-installer PLAN 4.12; domovoi/windows_side.py,
the Mac's routes in domovoi/main.py, the Version card in
web/static/settings.jsx).

* a Mac side that says ``"updates": 1`` in its side.json gets the Windows
  side's step: the same reasons it isn't needed, the same request, claim
  and result, through its own loopback routes (``/v1/mac/update``);
* one without it stays "by hand" (test_mac_side.py);
* the Mac's routes answer only on a Mac, the Windows tray's see no Windows
  side there, and a Windows install's answers are unchanged;
* the Mac app's log is a path in Domovoi's own folders; a Windows path from
  it, or a Mac path from the tray, is dropped;
* the card shows the Mac flow worded for a Mac, with no Windows text, and
  an old failure the host side has caught up with is hidden on both kinds.

DB-free; the page half needs node.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from domovoi import egress, git_version, self_restart, windows_side

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
C = "c0ffee0123456789abcdef0123456789abcdef01"
MAC = {"schema": 1, "kind": "mac", "version": "1.1.0", "build_commit": C, "written_at": "2026-11-02T10:00:00Z",
       "updates": 1}
WIN = {"schema": 1, "version": "1.0.0", "build_commit": C, "written_at": "2026-10-22T21:30:00Z"}
MAC_LOG = "/Library/Application Support/Domovoi/updates/20261102-100000-0a1b2c3d/install.log"
WIN_LOG = "C:\\Users\\alex\\AppData\\Local\\Domovoi\\updates\\20261022-213000-0a1b2c3d\\setup.log"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DOMOVOI_WINDOWS_UPDATE_DIR", str(tmp_path / "wu"))
    monkeypatch.setenv("DOMOVOI_WINDOWS_SIDE_FILE", str(tmp_path / "win" / "side.json"))
    monkeypatch.setenv("DOMOVOI_HOST_SIDE_FILE", str(tmp_path / "host" / "side.json"))
    monkeypatch.setattr(windows_side, "_root_only", lambda st: True)
    windows_side._TRAY.clear()
    windows_side._TARGET_CACHE.clear()
    yield
    windows_side._TRAY.clear()
    windows_side._TARGET_CACHE.clear()


def _write(tmp_path: Path, which: str, doc: Any) -> None:
    p = tmp_path / which / "side.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture
def server(monkeypatch):
    s: dict[str, Any] = {"target": {"tag": "v1.2.0", "version": "1.2.0", "commit": C}, "internet": "always"}
    monkeypatch.setattr(windows_side, "target_release", lambda sha, ch: s["target"])
    monkeypatch.setattr(git_version, "boot_sha", lambda: "4d6b414")
    monkeypatch.setattr(git_version, "tracking", lambda: {"channel": "stable"})
    monkeypatch.setattr(git_version, "read_last_update", lambda: None)
    monkeypatch.setattr(self_restart, "underway", lambda last=None, mode=None: False)
    monkeypatch.setattr(egress, "policy", lambda: s["internet"])
    return s


@pytest.fixture
def mac(tmp_path, server):
    _write(tmp_path, "host", MAC)
    return server


# ─── the state ──────────────────────────────────────────────────────────


def test_the_updater_is_read_from_the_mac_sides_file(tmp_path):
    _write(tmp_path, "host", MAC)
    assert windows_side.read_host_side()["updates"] is True
    _write(tmp_path, "host", {**MAC, "updates": True})
    assert windows_side.read_host_side()["updates"] is False, "the number 1, as the helper writes it"
    _write(tmp_path, "host", {k: v for k, v in MAC.items() if k != "updates"})
    assert windows_side.read_host_side()["updates"] is False


def test_the_updater_field_and_the_host_kind_share_the_file(tmp_path, monkeypatch):
    """domovoi/host_kind.py reads the same side.json for "kind": "mac"; the
    helper's "updates": 1 beside it changes nothing there."""
    from domovoi import host_kind

    _write(tmp_path, "host", MAC)
    monkeypatch.setattr(host_kind, "_root_only", lambda st: True)
    assert host_kind.read_mac_marker() is True
    assert windows_side.read_host_side()["kind"] == "mac" and windows_side.read_host_side()["updates"] is True


@pytest.mark.asyncio
async def test_a_newer_release_is_a_mac_step(mac):
    card = await windows_side.card_state()
    assert card["kind"] == "mac" and card["updates"] is True
    assert card["step_needed"] is True and card["why_not"] is None
    assert "updates" not in card["target"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change,why", [
    ({"target": {"tag": "v1.1.0", "version": "1.1.0", "commit": C}}, "up_to_date"),
    ({"target": {"tag": "v1.0.9", "version": "1.0.9", "commit": C}}, "up_to_date"),
    ({"internet": "never"}, "internet_off"),
    ({"target": None}, "no_release"),
])
async def test_the_reasons_there_is_no_mac_step_are_the_windows_sides(mac, change, why):
    mac.update(change)
    card = await windows_side.card_state()
    assert card["why_not"] == why and card["step_needed"] is False


@pytest.mark.asyncio
async def test_a_windows_card_has_no_updates_field(tmp_path, server):
    _write(tmp_path, "win", WIN)
    _write(tmp_path, "host", MAC)
    card = await windows_side.card_state()
    assert card["kind"] == "windows" and "updates" not in card


@pytest.mark.asyncio
async def test_the_mac_apps_state_is_the_mac_side(mac):
    st = await windows_side.agent_state("mac")
    assert st["mac"] == {"version": "1.1.0", "build_commit": C, "written_at": "2026-11-02T10:00:00Z"}
    assert st["step_needed"] is True and st["why_not"] is None and "windows" not in st
    tray = await windows_side.tray_state()
    assert tray["windows"] is None and tray["step_needed"] is False and tray["why_not"] == "no_windows_side"


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["win", None])
async def test_the_mac_apps_state_elsewhere_is_nothing(tmp_path, server, which):
    if which:
        _write(tmp_path, which, WIN)
    windows_side.make_request(on_this_pc=True)
    st = await windows_side.agent_state("mac")
    assert st["mac"] is None and st["step_needed"] is False and st["why_not"] == "no_mac_side"
    assert st["request"] is None, "a Windows request is nothing to a Mac app"


@pytest.mark.asyncio
async def test_the_windows_trays_state_is_what_it_was(tmp_path, server):
    """The tray's poll, key for key, on a Windows install."""
    _write(tmp_path, "win", WIN)
    req, _ = windows_side.make_request(on_this_pc=False)
    st = await windows_side.tray_state()
    assert set(st) == {"schema", "windows", "target", "step_needed", "why_not", "internet", "channel", "linux", "request"}
    assert st["windows"]["version"] == "1.0.0" and st["step_needed"] is True and st["request"]["id"] == req["id"]


# ─── results ────────────────────────────────────────────────────────────


def test_the_mac_apps_log_is_kept_and_a_windows_path_from_it_is_not():
    doc = windows_side.record_result({"outcome": "repair_failed", "log": MAC_LOG}, "v1.2.0", "mac")
    assert doc["log"] == MAC_LOG
    for bad in [WIN_LOG, "/etc/passwd", "/Library/Logs/Other/x.log", "/Library/Logs/Domovoi/\x07bell"]:
        assert windows_side.record_result({"outcome": "failed", "log": bad}, None, "mac")["log"] is None, bad


def test_the_trays_log_rule_is_unchanged():
    assert windows_side.record_result({"outcome": "failed", "log": WIN_LOG}, None)["log"] == WIN_LOG
    assert windows_side.record_result({"outcome": "failed", "log": MAC_LOG}, None)["log"] is None


# ─── the routes ─────────────────────────────────────────────────────────


def _req(host: str = "127.0.0.1", headers: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers or {})


def _json(resp) -> tuple[int, dict]:
    return resp.status_code, json.loads(resp.body)


@pytest.fixture
def unfrozen(monkeypatch):
    from domovoi.container_guard import freeze as container_freeze

    async def ok(_what):
        return None

    monkeypatch.setattr(container_freeze, "require_unfrozen", ok)


def test_the_whole_mac_step_through_the_routes(mac, unfrozen):
    from domovoi import main

    out = asyncio.run(main.admin_windows_request(main._WindowsRequestBody(on_this_pc=True)))
    assert out["ok"] and not out["joined"]
    rid = out["request"]["id"]
    status, st = _json(asyncio.run(main.mac_update_state(_req(), "1.1.0")))
    assert status == 200 and st["request"]["id"] == rid and st["request"]["on_this_pc"] is True
    assert windows_side.tray_seen()["running"] is True and windows_side.tray_seen()["version"] == "1.1.0"
    status, claim = _json(asyncio.run(main.mac_update_claim(main._TrayClaimBody(request_id=rid), _req())))
    assert status == 200 and claim["ok"]
    status, again = _json(asyncio.run(main.mac_update_claim(main._TrayClaimBody(request_id=rid), _req())))
    assert status == 409 and again["reason"] == "claimed", "a second account's app doesn't run it too"
    body = main._TrayResultBody(request_id=rid, claim=claim["claim"], outcome="ok", version="1.2.0", log=MAC_LOG)
    status, _ = _json(asyncio.run(main.mac_update_result(body, _req())))
    assert status == 200
    assert windows_side.read_request() is None, "the answer ends the request"
    r = windows_side.read_result()
    assert r["outcome"] == "ok" and r["version"] == "1.2.0" and r["log"] == MAC_LOG and r["tag"] == "v1.2.0"


def test_a_poll_without_app_is_not_the_app(mac):
    """The helper's own wait on the update unit polls the same route."""
    from domovoi import main

    status, _ = _json(asyncio.run(main.mac_update_state(_req(), None)))
    assert status == 200 and windows_side.tray_seen()["running"] is False


def test_a_package_less_release_hides_the_step_once_the_claiming_app_says_so(mac, unfrozen):
    from domovoi import main

    rid = asyncio.run(main.admin_windows_request(main._WindowsRequestBody()))["request"]["id"]
    _, claim = _json(asyncio.run(main.mac_update_claim(main._TrayClaimBody(request_id=rid), _req())))
    body = main._TrayResultBody(request_id=rid, claim=claim["claim"], outcome="no_signed_setup")
    asyncio.run(main.mac_update_result(body, _req()))
    card = asyncio.run(windows_side.card_state())
    assert card["why_not"] == "unsigned_release" and card["step_needed"] is False
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        asyncio.run(main.admin_windows_request(main._WindowsRequestBody()))
    assert e.value.status_code == 409 and e.value.detail == "that release has no signed Mac package"


def test_a_stray_answer_without_the_claim_is_refused(mac, unfrozen):
    from fastapi import HTTPException

    from domovoi import main

    rid = asyncio.run(main.admin_windows_request(main._WindowsRequestBody()))["request"]["id"]
    body = main._TrayResultBody(request_id=rid, claim="0" * 32, outcome="no_signed_setup")
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.mac_update_result(body, _req()))
    assert e.value.status_code == 403
    assert windows_side.read_request() is not None
    assert asyncio.run(windows_side.card_state())["step_needed"] is True


@pytest.mark.parametrize("route", ["state", "claim", "result"])
@pytest.mark.parametrize("req", [_req("192.168.1.30"), _req("127.0.0.1", {"x-forwarded-for": "192.168.1.30"})])
def test_the_mac_routes_answer_this_mac_only(mac, route, req):
    from domovoi import main

    call = {
        "state": lambda: main.mac_update_state(req, None),
        "claim": lambda: main.mac_update_claim(main._TrayClaimBody(request_id="a" * 16), req),
        "result": lambda: main.mac_update_result(main._TrayResultBody(outcome="ok"), req),
    }[route]
    status, body = _json(asyncio.run(call()))
    assert status == 403 and body["reason"] == "not_local"


@pytest.mark.parametrize("which", ["win", None])
def test_the_mac_routes_refuse_where_there_is_no_mac(tmp_path, server, which):
    from domovoi import main

    if which:
        _write(tmp_path, which, WIN)
    rid = windows_side.make_request(on_this_pc=False)[0]["id"]
    status, body = _json(asyncio.run(main.mac_update_claim(main._TrayClaimBody(request_id=rid), _req())))
    assert status == 409 and body["reason"] == "no_mac_side"
    assert not list(windows_side.state_dir().glob("claim-*.json")), "nothing claimed"
    status, body = _json(asyncio.run(main.mac_update_result(main._TrayResultBody(outcome="ok"), _req())))
    assert status == 409 and windows_side.read_result() is None
    status, st = _json(asyncio.run(main.mac_update_state(_req(), "1.1.0")))
    assert status == 200 and st["mac"] is None
    assert windows_side.tray_seen()["running"] is False, "a poll from no Mac side is no Mac app"


@pytest.mark.parametrize("change,detail", [
    ({"target": {"tag": "v1.1.0", "version": "1.1.0", "commit": C}}, "the Mac side is up to date"),
    ({"internet": "never"}, "internet access is turned off, and the Mac side updates from Domovoi's releases on the internet"),
])
def test_the_request_is_refused_in_mac_words(mac, unfrozen, change, detail):
    from fastapi import HTTPException

    from domovoi import main

    mac.update(change)
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.admin_windows_request(main._WindowsRequestBody()))
    assert e.value.status_code == 409 and e.value.detail == detail


# ─── the Version card ───────────────────────────────────────────────────

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
window.confirm = (m) => { window.__confirms.push(String(m)); return true; };
"""

_SCRIPT = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
const view = () => {
  const one = (cls) => { const e = h.find((x) => w.__cls(x).includes(cls)); return e ? w.__deep(e).trim() : null; };
  return {
    mac: one('version-mac'), behind: one('version-mac-behind'), request: one('version-mac-request'),
    result: one('version-mac-result'), why: one('version-mac-why'), channel: one('version-mac-channel'),
    byhand: one('version-mac-notes'), wresult: one('version-windows-result'),
    buttons: h.findAll({ type: 'button' }).map((b) => w.__deep(b).trim()).filter(Boolean),
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
UPDATE = "await h.click({ type: 'button', text: 'Update Domovoi' });"
RETRY = "await h.click({ type: 'button', text: 'Try again' });"


def _card(**over) -> dict:
    c = {"present": True, "kind": "mac", "updates": True, "version": "1.1.0", "build_commit": C, "reported_at": "x",
         "target": {"tag": "v1.2.0", "version": "1.2.0", "commit": C}, "step_needed": True,
         "why_not": None, "follows": "releases", "request": None, "result": None,
         "tray": {"running": True, "seen_at": "2026-11-02T10:01:00Z", "version": "1.1.0"}}
    c.update(over)
    return c


def _version(card: dict, **over) -> dict:
    v = {"sha": "4d6b414", "running_sha": "4d6b414", "checkout_sha": "4d6b414",
         "restart_required": False, "restart_capable": True, "restart_mode": "update",
         "restart_in_progress": False, "version": "1.2.0", "checkout_version": "1.2.0",
         "channel": "stable", "channels": ["stable", "main"], "channel_pin": None, "windows_side": card}
    v.update(over)
    return v


def _check(**over) -> dict:
    c = {"behind": 0, "ahead": 0, "upstream": True, "upstream_sha": "f" * 40, "error": None,
         "channel": "stable", "upstream_version": "1.2.0", "latest_release": "1.2.0"}
    c.update(over)
    return c


def _scenario(version: dict, act: str = NOTHING, check: dict | None = None, extra: dict | None = None) -> dict:
    api = {"GET /api/config": {"web_version": "1.2.0"}, "GET /api/config/version": version,
           "GET /api/config/core/restart": {"answering": True},
           "POST /api/config/version/check": check or _check()}
    api.update(extra or {})
    return {"files": ["web/static/components.jsx", "web/static/settings.jsx"], "component": "VersionSection",
            "setup": _SETUP, "api": api, "script": _SCRIPT.replace("__ACT__", act)}


_REQ_OK = {"POST /api/config/version/windows-request": {"ok": True, "request": {"id": "a" * 16}, "tray": {"running": True}}}
_WIN = {"present": True, "version": "1.0.0", "build_commit": C, "reported_at": "x",
        "target": {"tag": "v1.0.1", "version": "1.0.1", "commit": C}, "step_needed": False, "why_not": "up_to_date",
        "follows": "releases", "request": None,
        "result": {"outcome": "not_newer", "detail": "Domovoi didn't run the downloaded Setup: it is version 1.0.1, "
                   "which isn't newer than the installed 1.0.1."},
        "tray": {"running": True, "seen_at": "x", "version": "1.0.1"}}

SCENARIOS = {
    "behind": _scenario(_version(_card()), UPDATE, extra=_REQ_OK),
    "current": _scenario(_version(_card(step_needed=False, why_not="up_to_date", version="1.2.0"))),
    "both_behind": _scenario(_version(_card()), UPDATE, _check(behind=3, upstream_version="1.2.1"), {
        "POST /api/config/version/pull": {"pulled": True, "new_sha": "0a1b2c3"},
        "POST /api/config/version/restart": {"ok": False, "error": "no sudoers grant"}, **_REQ_OK}),
    "finish_on_mac": _scenario(_version(_card(request={"id": "a" * 16, "on_this_pc": False, "at": "x"}))),
    "on_this_mac": _scenario(_version(_card(request={"id": "a" * 16, "on_this_pc": True, "at": "x"}))),
    "no_app": _scenario(_version(_card(request={"id": "a" * 16, "on_this_pc": False, "at": "x"},
                                       tray={"running": False, "seen_at": None, "version": None}))),
    "declined": _scenario(_version(_card(result={"outcome": "declined", "detail": None, "log": None, "after_linux": True})),
                          RETRY, extra=_REQ_OK),
    "declined_mac_only": _scenario(_version(_card(result={"outcome": "declined", "detail": None, "log": None,
                                                          "after_linux": False}))),
    "repair_failed": _scenario(_version(_card(result={
        "outcome": "repair_failed", "detail": "The package couldn't update the Mac side: provision.sh failed at venv.",
        "log": MAC_LOG}))),
    "bad_signature": _scenario(_version(_card(result={"outcome": "bad_signature", "detail": None, "log": None}))),
    "updated": _scenario(_version(_card(step_needed=False, why_not="up_to_date", version="1.2.0",
                                        result={"outcome": "ok", "version": "1.2.0"}))),
    "stale_failure": _scenario(_version(_card(step_needed=False, why_not="up_to_date", version="1.2.0",
                                              result={"outcome": "not_newer", "detail": "Domovoi didn't install the "
                                                      "downloaded package: it is version 1.2.0, which isn't newer than "
                                                      "the installed 1.2.0."}))),
    "windows_stale_failure": _scenario(_version(_WIN, version="1.0.1")),
    "internet_never": _scenario(_version(_card(step_needed=False, why_not="internet_off"))),
    "unsigned": _scenario(_version(_card(step_needed=False, why_not="unsigned_release"))),
    "main_channel": _scenario(_version(_card(), channel="main", version="1.2.0 + 4 (4d6b414)")),
    "by_hand": _scenario(_version(_card(updates=False, step_needed=False, why_not="mac_by_hand"))),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("version-mac-update") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def _no_windows(o: dict) -> None:
    for view in ("before", "after"):
        assert "Windows" not in o[view]["text"] and " PC" not in o[view]["text"], o[view]["text"]
    for q in o.get("confirms", []):
        assert "Windows" not in q and " PC" not in q, q


def test_a_behind_mac_side_has_update_domovoi_and_asks_the_mac(driven):
    o = driven["behind"]
    assert o["before"]["behind"] == "Mac side is behind: 1.2.0 is out"
    assert "Update Domovoi" in o["before"]["buttons"]
    assert o["before"]["byhand"] is None, "no 'open it by hand' line beside a button that does it"
    assert [b for b in o["before"]["buttons"] if b != "Restart Domovoi"] == ["Update Domovoi"],         "one action: no Check for updates beside it (main's Windows card, 2026-11-14)"
    assert o["calls"] == ["POST /api/config/version/check",
                          'POST /api/config/version/windows-request {"after_linux":false}'], "checks first itself"
    assert o["confirms"] == [], "macOS asks there"
    assert "finish on your Mac" in o["after"]["text"]
    _no_windows(o)


def test_a_current_mac_side_offers_nothing(driven):
    b = driven["current"]["before"]
    assert b["behind"] is None and "Update Domovoi" not in b["buttons"] and b["byhand"] is None
    _no_windows(driven["current"])


def test_behind_on_both_is_one_question_worded_for_a_mac(driven):
    o = driven["both_behind"]
    assert len(o["confirms"]) == 1
    q = o["confirms"][0]
    assert "First the Linux part" in q and "Then the Mac part" in q and "administrator’s password" in q
    posts = [c for c in o["calls"] if c.startswith("POST")]
    assert posts[:3] == ["POST /api/config/version/check", "POST /api/config/version/pull", "POST /api/config/version/restart"]
    _no_windows(o)


def test_the_request_says_where_to_finish(driven):
    assert driven["finish_on_mac"]["before"]["request"].startswith("Finish on your Mac: click the Domovoi notification there")
    assert "Update Domovoi" not in driven["finish_on_mac"]["before"]["buttons"]
    assert driven["on_this_mac"]["before"]["request"].startswith("macOS asks for an administrator’s password on this Mac next")
    assert driven["no_app"]["before"]["request"].startswith("Sign in on the Mac to finish: the Domovoi icon in its menu bar")
    for k in ("finish_on_mac", "on_this_mac", "no_app"):
        _no_windows(driven[k])


def test_a_declined_prompt_on_the_mac(driven):
    o = driven["declined"]
    assert o["before"]["result"].startswith("The update is half done: the Linux part is updated, the Mac part isn’t")
    assert "Try again" in o["before"]["buttons"]
    assert o["calls"] == ['POST /api/config/version/windows-request {"after_linux":false}']
    r = driven["declined_mac_only"]["before"]["result"]
    assert r.startswith("The Mac part wasn’t updated: macOS didn’t get an administrator’s password")
    _no_windows(o)


def test_a_failure_names_its_log_and_a_refusal_says_why(driven):
    r = driven["repair_failed"]["before"]["result"]
    assert "provision.sh failed at venv" in r and MAC_LOG in r
    assert driven["bad_signature"]["before"]["result"] == (
        "The Mac part didn’t update: the downloaded package didn’t check out against Domovoi’s release key, "
        "so it wasn’t installed")


def test_an_update_that_worked_says_so(driven):
    assert driven["updated"]["before"]["result"] == "The Mac side updated to 1.2.0."


def test_a_failure_the_host_side_caught_up_with_is_not_shown(driven):
    """bb35a73a on main (2026-11-02): an old refusal stayed red on a card
    whose Windows side was current. The Mac card follows the same rule."""
    m = driven["stale_failure"]["before"]
    assert m["result"] is None and "didn’t update" not in m["text"]
    w = driven["windows_stale_failure"]["before"]
    assert w["wresult"] is None and "didn't run" not in w["text"]


def test_why_there_is_no_mac_step(driven):
    b = driven["internet_never"]["before"]
    assert b["why"].startswith("The Mac side updates from Domovoi’s releases on the internet")
    assert "Update Domovoi" not in b["buttons"]
    assert driven["unsigned"]["before"]["why"].startswith("Domovoi 1.2.0 has no signed Mac package, so the Mac side stays at 1.1.0")


def test_on_main_the_mac_side_follows_releases(driven):
    c = driven["main_channel"]["before"]["channel"]
    assert c.startswith("The Linux side follows every change on main; the Mac side follows releases")


def test_a_mac_side_without_the_updater_is_still_by_hand(driven):
    b = driven["by_hand"]["before"]
    assert b["behind"] == "Mac side is behind: 1.2.0 is out"
    assert "open it on the Mac" in b["byhand"] and "Update Domovoi" not in b["buttons"]
