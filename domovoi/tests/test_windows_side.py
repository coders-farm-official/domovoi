"""The Windows side of a Windows install, and the one "Update Domovoi"
across both halves (design note WINDOWS-ONE-UPDATE-2026-10-22;
domovoi/windows_side.py).

* the version DomovoiHost reports is believed only from a root-owned file;
* the Windows target is the newest release the running code includes
  (release candidates not counted on stable), and the step is needed only
  when it is newer, internet access isn't "never" and the release isn't
  known to lack a signed Setup;
* the tray's routes answer a loopback peer with no forwarding header only,
  and keep only known outcomes and plain text;
* the admin's request is the security tier, and the web says whether the
  browser is on the PC by the same loopback rule;
* the tray's own Linux part (``python -m domovoi.windows_side
  linux-update``, run by DomovoiHost) is the dashboard's update in its
  order.

DB-free: the git, the update unit and the container guard are faked.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from domovoi import admin_auth, egress, git_version, self_restart, windows_side
from domovoi.container_guard import freeze as container_freeze
from domovoi.main import app as core_app

LOOPBACK = ("127.0.0.1", 50123)
LAN = ("192.168.1.40", 50123)
C = "c0ffee0123456789abcdef0123456789abcdef01"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DOMOVOI_WINDOWS_UPDATE_DIR", str(tmp_path / "wu"))
    monkeypatch.setenv("DOMOVOI_WINDOWS_SIDE_FILE", str(tmp_path / "side" / "side.json"))
    windows_side._TRAY.clear()
    windows_side._TARGET_CACHE.clear()
    yield
    windows_side._TRAY.clear()
    windows_side._TARGET_CACHE.clear()


def _side(tmp_path: Path, doc: Any, root: bool = True, monkeypatch=None) -> None:
    p = tmp_path / "side" / "side.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    if monkeypatch is not None:
        monkeypatch.setattr(windows_side, "_root_only", lambda st: root)


GOOD_SIDE = {"schema": 1, "version": "1.0.0", "build_commit": C, "written_at": "2026-10-22T21:30:00Z"}


# ─── versions ───────────────────────────────────────────────────────────


def test_versions_order_like_the_tray_does() -> None:
    order = ["0.9.0", "1.0.0-rc1", "1.0.0-rc2", "1.0.0-rc10", "1.0.0", "1.0.1-rc1", "1.0.1", "1.1.0", "v2.0.0"]
    for a, b in zip(order, order[1:]):
        assert windows_side.compare_versions(a, b) < 0, (a, b)
        assert windows_side.compare_versions(b, a) > 0, (b, a)
    assert windows_side.compare_versions("1.0.0", "v1.0.0") == 0
    with pytest.raises(ValueError):
        windows_side.compare_versions("one", "1.0.0")


# ─── the report ─────────────────────────────────────────────────────────


def test_no_file_is_a_linux_only_install(tmp_path) -> None:
    assert windows_side.read_side() is None


def test_a_root_owned_report_is_read(tmp_path, monkeypatch) -> None:
    _side(tmp_path, GOOD_SIDE, monkeypatch=monkeypatch)
    assert windows_side.read_side() == {"version": "1.0.0", "build_commit": C, "written_at": "2026-10-22T21:30:00Z"}


def test_a_report_anyone_but_root_could_write_is_not_believed(tmp_path, monkeypatch) -> None:
    _side(tmp_path, GOOD_SIDE, root=False, monkeypatch=monkeypatch)
    assert windows_side.read_side() is None


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX owners")
def test_the_real_owner_check(tmp_path) -> None:
    _side(tmp_path, GOOD_SIDE)
    # This test's own file belongs to the user running it, not root.
    assert (windows_side.read_side() is None) == (os.getuid() != 0)


def test_the_owner_check_trusts_nothing_without_posix_owners(monkeypatch) -> None:
    if hasattr(os, "getuid"):
        monkeypatch.delattr(os, "getuid")
    assert windows_side._root_only(os.stat(__file__)) is False


@pytest.mark.parametrize("doc", [
    "not json",
    {"schema": 2, "version": "1.0.0"},
    {"schema": 1, "version": "1.0; rm -rf /"},
    {"schema": 1},
    "[1, 2]",
])
def test_a_report_that_is_not_one_is_ignored(tmp_path, monkeypatch, doc) -> None:
    _side(tmp_path, doc, monkeypatch=monkeypatch)
    assert windows_side.read_side() is None


def test_a_bad_build_commit_is_dropped_not_shown(tmp_path, monkeypatch) -> None:
    _side(tmp_path, {**GOOD_SIDE, "build_commit": "HEAD; echo"}, monkeypatch=monkeypatch)
    assert windows_side.read_side()["build_commit"] is None


# ─── the target ─────────────────────────────────────────────────────────


class _Proc:
    def __init__(self, out: str, code: int = 0) -> None:
        self.stdout, self.returncode, self.stderr = out, code, ""


def _fake_git(monkeypatch, describe: dict[str, str]) -> list[tuple]:
    calls: list[tuple] = []

    def run(*args: str):
        calls.append(args)
        if args[0] == "describe":
            return _Proc(describe.get("stable" if "--exclude" in args else "any", ""), 0 if describe else 128)
        if args[0] == "rev-parse":
            return _Proc(C)
        return _Proc("", 1)

    monkeypatch.setattr(git_version, "_run", run)
    return calls


def test_the_target_is_the_newest_release_the_running_code_includes(monkeypatch) -> None:
    calls = _fake_git(monkeypatch, {"any": "v1.0.1-rc1\n", "stable": "v1.0.0\n"})
    assert windows_side.target_release("4d6b414", "main") == {"tag": "v1.0.1-rc1", "version": "1.0.1-rc1", "commit": C}
    assert windows_side.target_release("4d6b414", "stable") == {"tag": "v1.0.0", "version": "1.0.0", "commit": C}
    describe = [c for c in calls if c[0] == "describe"]
    assert describe[0][-1] == "4d6b414" and "--exclude" not in describe[0]
    assert "--exclude" in describe[1] and "v*-*" in describe[1], "stable doesn't count release candidates"
    # Cached per running commit.
    windows_side.target_release("4d6b414", "main")
    assert len([c for c in calls if c[0] == "describe"]) == 2


def test_no_release_yet_and_no_running_commit(monkeypatch) -> None:
    _fake_git(monkeypatch, {})
    assert windows_side.target_release("4d6b414", "main") is None
    assert windows_side.target_release("unknown", "main") is None
    _fake_git(monkeypatch, {"any": "not-a-tag\n"})
    windows_side._TARGET_CACHE.clear()
    assert windows_side.target_release("4d6b414", "main") is None


# ─── the state ──────────────────────────────────────────────────────────


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A Windows install whose Linux side runs 4d6b414, on stable, with the
    Windows side at 1.0.0 and v1.0.1 in the code."""
    s: dict[str, Any] = {"side": dict(GOOD_SIDE), "target": {"tag": "v1.0.1", "version": "1.0.1", "commit": C},
                         "channel": "stable", "internet": "always", "busy": False, "last": None}

    def side():
        d = s["side"]
        return None if d is None else {"version": d["version"], "build_commit": d.get("build_commit"),
                                       "written_at": d.get("written_at")}

    monkeypatch.setattr(windows_side, "read_side", side)
    monkeypatch.setattr(windows_side, "target_release", lambda sha, ch: s["target"])
    monkeypatch.setattr(git_version, "boot_sha", lambda: "4d6b414")
    monkeypatch.setattr(git_version, "tracking", lambda: {"channel": s["channel"]})
    monkeypatch.setattr(git_version, "read_last_update", lambda: s["last"])
    monkeypatch.setattr(self_restart, "underway", lambda last=None, mode=None: s["busy"])
    monkeypatch.setattr(egress, "policy", lambda: s["internet"])
    return s


@pytest.mark.asyncio
async def test_the_step_is_needed_when_the_release_is_newer(server) -> None:
    st = await windows_side.tray_state()
    assert st["schema"] == 1 and st["step_needed"] is True and st["why_not"] is None
    assert st["windows"]["version"] == "1.0.0" and st["target"]["tag"] == "v1.0.1"
    card = await windows_side.card_state()
    assert card["present"] and card["step_needed"] and card["follows"] == "releases"
    assert card["tray"] == {"running": False, "seen_at": None, "version": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("change,why", [
    ({"internet": "never"}, "internet_off"),
    ({"target": None}, "no_release"),
    ({"target": {"tag": "v1.0.0", "version": "1.0.0", "commit": C}}, "up_to_date"),
    ({"target": {"tag": "v0.9.0", "version": "0.9.0", "commit": C}}, "up_to_date"),
    ({"side": None}, "no_windows_side"),
])
async def test_why_there_is_no_step(server, change, why) -> None:
    server.update(change)
    st = await windows_side.tray_state()
    assert st["step_needed"] is False and st["why_not"] == why
    card = await windows_side.card_state()
    assert card == {"present": False} if why == "no_windows_side" else card["why_not"] == why


@pytest.mark.asyncio
async def test_a_release_with_no_signed_setup_stops_being_offered(server) -> None:
    windows_side.make_request(False)
    windows_side.record_result({"outcome": "no_signed_setup", "request_id": None}, "v1.0.1")
    st = await windows_side.tray_state()
    assert st["step_needed"] is False and st["why_not"] == "unsigned_release"


@pytest.mark.asyncio
async def test_a_request_with_nothing_left_to_do_goes_away(server) -> None:
    req = windows_side.make_request(True)
    assert (await windows_side.tray_state())["request"] == req
    server["busy"] = True
    server["target"] = {"tag": "v1.0.0", "version": "1.0.0", "commit": C}
    assert (await windows_side.tray_state())["request"] == req, "kept while an update runs"
    server["busy"] = False
    assert (await windows_side.tray_state())["request"] is None
    assert windows_side.read_request() is None


def test_the_tray_counts_as_running_for_two_minutes(monkeypatch) -> None:
    t = [1000.0]
    monkeypatch.setattr(windows_side, "_now", lambda: t[0])
    windows_side.note_tray("1.0.0")
    assert windows_side.tray_seen()["running"] and windows_side.tray_seen()["version"] == "1.0.0"
    t[0] += windows_side.TRAY_FRESH_SEC + 1
    assert not windows_side.tray_seen()["running"]
    windows_side.note_tray("not a version")
    assert windows_side.tray_seen()["version"] is None


def test_a_result_keeps_known_outcomes_and_plain_text_only() -> None:
    req = windows_side.make_request(False)
    doc = windows_side.record_result({"outcome": "repair_failed", "request_id": req["id"],
                                      "detail": "Setup failed\x1b[31m at venv", "log": "C:\\Users\\k\\setup.log",
                                      "version": "1.0.1"}, "v1.0.1")
    assert doc["detail"] == "Setup failed[31m at venv" and doc["log"] == "C:\\Users\\k\\setup.log"
    assert windows_side.read_request() is None, "the request it answers is done"
    assert windows_side.read_result()["outcome"] == "repair_failed"
    doc = windows_side.record_result({"outcome": "declined", "log": "\\\\evil\\share\\x", "version": "x"}, None)
    assert doc["log"] is None and doc["version"] is None
    with pytest.raises(ValueError):
        windows_side.record_result({"outcome": "run_this"}, None)


def test_a_result_for_another_request_leaves_the_current_one() -> None:
    req = windows_side.make_request(False)
    windows_side.record_result({"outcome": "declined", "request_id": "0" * 16}, None)
    assert windows_side.read_request() == req


# ─── the routes ─────────────────────────────────────────────────────────


def _core(peer=LOOPBACK) -> TestClient:
    return TestClient(core_app, base_url="http://127.0.0.1:6370", client=peer,
                      headers={"X-Requested-With": "DomovoiTray"})


def test_the_tray_reads_the_state_over_loopback_only(server, monkeypatch) -> None:
    r = _core().get("/v1/windows/update", params={"tray": "1.0.0"})
    assert r.status_code == 200 and r.json()["step_needed"] is True
    assert r.headers["cache-control"] == "no-store"
    assert windows_side.tray_seen()["running"], "the poll says a tray is there"
    r = _core(LAN).get("/v1/windows/update")
    assert r.status_code == 403 and r.json()["reason"] == "not_local"
    for h in ("X-Forwarded-For", "Forwarded", "X-Real-IP", "X-Forwarded-Host"):
        r = _core().get("/v1/windows/update", headers={h: "127.0.0.1"})
        assert r.status_code == 403, h


def test_the_tray_reports_over_loopback_only(server) -> None:
    req = windows_side.make_request(False)
    r = _core(LAN).post("/v1/windows/update/result", json={"outcome": "ok", "request_id": req["id"]})
    assert r.status_code == 403 and windows_side.read_request() == req
    r = _core().post("/v1/windows/update/result", json={"outcome": "ok", "request_id": req["id"]},
                     headers={"X-Forwarded-For": "10.0.0.9"})
    assert r.status_code == 403
    r = _core().post("/v1/windows/update/result", json={"outcome": "ok", "request_id": req["id"], "version": "1.0.1"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert windows_side.read_result()["outcome"] == "ok" and windows_side.read_request() is None
    assert _core().post("/v1/windows/update/result", json={"outcome": "whatever"}).status_code == 422


def _gates(method: str, path: str) -> list:
    from domovoi.tests.route_walk import iter_route_contexts

    def calls(dependant):
        for dep in getattr(dependant, "dependencies", ()) if dependant else ():
            if dep.call is not None:
                yield dep.call
            yield from calls(dep)

    for rc in iter_route_contexts(core_app.routes):
        if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or set()):
            return list(calls(getattr(rc, "dependant", None)))
    raise AssertionError(f"{method} {path} is not a core route")


def test_the_admin_request_is_the_security_tier() -> None:
    assert admin_auth.require_admin_security in _gates("POST", "/v1/admin/windows/request")
    assert admin_auth.require_admin_security in _gates("POST", "/v1/admin/windows/request/cancel")


@pytest.fixture
def as_admin(monkeypatch):
    core_app.dependency_overrides[admin_auth.require_admin_security] = lambda: None

    async def not_frozen(*a, **k):
        return None

    monkeypatch.setattr(container_freeze, "require_unfrozen", not_frozen)
    yield
    core_app.dependency_overrides.pop(admin_auth.require_admin_security, None)


def test_an_admin_asks_for_the_step(server, as_admin) -> None:
    r = _core().post("/v1/admin/windows/request", json={"on_this_pc": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["request"]["on_this_pc"] is True and body["tray"]["running"] is False
    assert windows_side.read_request()["id"] == body["request"]["id"]
    assert _core().post("/v1/admin/windows/request/cancel").json() == {"ok": True}
    assert windows_side.read_request() is None


@pytest.mark.parametrize("change,words", [
    ({"internet": "never"}, "internet access is turned off"),
    ({"side": None}, "isn't a Windows install"),
    ({"target": {"tag": "v1.0.0", "version": "1.0.0", "commit": C}}, "up to date"),
])
def test_no_request_when_there_is_no_step(server, as_admin, change, words) -> None:
    server.update(change)
    r = _core().post("/v1/admin/windows/request", json={})
    assert r.status_code == 409 and words in r.json()["detail"]
    assert windows_side.read_request() is None


@pytest.mark.asyncio
async def test_the_version_card_gets_the_windows_side(server, monkeypatch) -> None:
    from domovoi import main as core_main

    async def fake_state():
        return {"restart_required": False}

    monkeypatch.setattr(git_version, "version_state", fake_state)
    from domovoi import loop_watchdog

    monkeypatch.setattr(loop_watchdog, "last_stall", lambda: None)
    state = await core_main.admin_version()
    assert state["windows_side"]["present"] and state["windows_side"]["version"] == "1.0.0"
    server["side"] = None
    state = await core_main.admin_version()
    assert state["windows_side"] == {"present": False}


def test_the_web_says_whether_the_browser_is_on_the_pc(monkeypatch) -> None:
    from web.backend.api import config as web_config
    from web.backend.main import app as web_app

    sent: list[dict] = []

    async def post_admin(path, body, headers=None, timeout=None):
        sent.append({"path": path, "body": body})
        return 200, {"ok": True, "request": {"id": "a" * 16}, "tray": {"running": True}}

    monkeypatch.setattr(web_config, "post_admin", post_admin)
    web_app.dependency_overrides[admin_auth.require_admin_security] = lambda: None
    try:
        def call(peer, **headers):
            c = TestClient(web_app, base_url="http://127.0.0.1:6369", client=peer,
                           headers={"X-Requested-With": "domovoi-tests", **headers})
            return c.post("/api/config/version/windows-request", json={})
        assert call(LOOPBACK).status_code == 200
        assert call(LAN).status_code == 200
        assert call(LOOPBACK, **{"X-Forwarded-For": "192.168.1.40"}).status_code == 200
    finally:
        web_app.dependency_overrides.pop(admin_auth.require_admin_security, None)
    assert [s["body"]["on_this_pc"] for s in sent] == [True, False, False]
    assert all(s["path"] == "/v1/admin/windows/request" for s in sent)


# ─── the tray's Linux part ──────────────────────────────────────────────


@pytest.fixture
def linux(monkeypatch):
    s: dict[str, Any] = {"internet_off": False, "frozen": False, "busy": False, "last": None,
                         "check": {"behind": 2, "ahead": 0, "upstream": True, "upstream_sha": "f" * 40, "error": None},
                         "pull": {"pulled": True, "new_sha": "bbbbbbb"}, "shas": ["aaaaaaa", "bbbbbbb"],
                         "applied": "a" * 40, "restart": {"ok": True}, "calls": []}

    async def is_frozen(*a, **k):
        return s["frozen"]

    async def commits_behind():
        s["calls"].append("check")
        return s["check"]

    async def pull():
        s["calls"].append("pull")
        return s["pull"]

    async def current_sha():
        return s["shas"][0] if "pull" not in s["calls"] else s["shas"][-1]

    async def restart():
        s["calls"].append("restart")
        return s["restart"]

    monkeypatch.setattr(egress, "internet_turned_off", lambda: s["internet_off"])
    monkeypatch.setattr(container_freeze, "is_frozen", is_frozen)
    monkeypatch.setattr(git_version, "read_last_update", lambda: s["last"])
    monkeypatch.setattr(self_restart, "underway", lambda last=None, mode=None: s["busy"])
    monkeypatch.setattr(git_version, "commits_behind", commits_behind)
    monkeypatch.setattr(git_version, "pull", pull)
    monkeypatch.setattr(git_version, "current_sha", current_sha)
    monkeypatch.setattr(self_restart, "restart", restart)
    monkeypatch.setattr(windows_side, "_applied_sha", lambda: s["applied"])
    return s


@pytest.mark.asyncio
async def test_behind_it_checks_pulls_and_starts_the_update_unit(linux) -> None:
    r = await windows_side.linux_update()
    assert r == {"ok": True, "started": True, "nothing_new": False, "error": None, "from_sha": "aaaaaaa", "to_sha": "bbbbbbb"}
    assert linux["calls"] == ["check", "pull", "restart"]


@pytest.mark.asyncio
async def test_nothing_new_is_said_and_nothing_restarts(linux) -> None:
    linux["check"] = {**linux["check"], "behind": 0}
    linux["shas"] = ["a" * 40]
    r = await windows_side.linux_update()
    assert r["ok"] and r["nothing_new"] and not r["started"]
    assert linux["calls"] == ["check"]


@pytest.mark.asyncio
async def test_pulled_code_not_yet_applied_is_applied(linux) -> None:
    linux["check"] = {**linux["check"], "behind": 0}
    linux["shas"] = ["b" * 40]
    r = await windows_side.linux_update()
    assert r["started"] and linux["calls"] == ["check", "restart"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change,words,calls", [
    ({"internet_off": True}, "internet access is turned off", []),
    ({"frozen": True}, "container guard", []),
    ({"busy": True}, "already under way", []),
    ({"check": {"behind": 0, "upstream": False, "error": "can't reach the update server"}}, "update check failed", ["check"]),
    ({"last": {"bad_sha": "f" * 40}}, "rolled back", ["check"]),
    ({"pull": {"pulled": False, "error": "diverged"}}, "pull failed: diverged", ["check", "pull"]),
    ({"restart": {"ok": False, "error": "no sudoers grant"}}, "restart didn't start", ["check", "pull", "restart"]),
])
async def test_the_dashboards_refusals(linux, change, words, calls) -> None:
    linux.update(change)
    r = await windows_side.linux_update()
    assert not r["ok"] and not r["started"] and words in r["error"], r
    assert linux["calls"] == calls


def test_the_cli_prints_one_json_line(linux, capsys) -> None:
    assert windows_side.main(["linux-update"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert json.loads(out[-1])["started"] is True
    assert windows_side.main(["something"]) == 2
