"""Restart a core that doesn't answer, from the dashboard (incident
2026-10-14): web/backend/core_restart.py and its two routes.

The dashboard's Restart used to go THROUGH the core, so a frozen core
could not be restarted from it. Now the web process ends a core that
doesn't answer and systemd starts it again, under the security tier, which
the web checks itself against the shared admin_sessions table (the core
that would normally check it is the one not answering).

The process side (a real kill, a real systemd restarting the unit) runs in
functional-testing/core-resilience/systemd (a container with systemd as
PID 1); here the decisions are pinned with systemd's answers scripted."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from web.backend import core_restart
from web.backend.core_restart import CoreUnit

from domovoi.tests.auth_testkit import (  # noqa: F401  (the _db fixture)
    COOKIE,
    _db,
    bearer,
    claim_admin,
    web_client,
)
from domovoi.tests.conftest import requires_db


def unit(**kw) -> CoreUnit:
    base = dict(load_state="loaded", active_state="active", sub_state="running", main_pid=4242,
                restart="on-failure", service_type="notify",
                started_monotonic_sec=time.monotonic() - 3600)
    base.update(kw)
    return CoreUnit(**base)


@pytest.fixture
def systemd(monkeypatch):
    """Script what systemd and the kernel say; record what would be done."""
    state = {"unit": unit(), "may_signal": True, "is_core": True, "sudo": (False, "no grant"),
             "ended": [], "restarted": 0}
    monkeypatch.setattr(core_restart, "core_unit", lambda: state["unit"])
    monkeypatch.setattr(core_restart, "_may_signal", lambda pid: state["may_signal"])
    monkeypatch.setattr(core_restart, "_looks_like_the_core", lambda pid: state["is_core"])
    monkeypatch.setattr(core_restart, "_systemctl", lambda: "/usr/bin/systemctl")
    monkeypatch.setattr(core_restart.self_restart, "capable", lambda mode=None: state["sudo"])
    monkeypatch.setattr(core_restart, "_LAST_KILL", None)

    def end_core(pid: int) -> bool:
        state["ended"].append(pid)
        return True

    async def restart():
        state["restarted"] += 1
        return {"ok": True, "mode": "update", "units": ["domovoi-update.service"],
                "delay_sec": 1.0, "error": None, "in_progress": False}

    async def not_answering(timeout: float = 3.0) -> bool:
        return False

    monkeypatch.setattr(core_restart, "end_core", end_core)
    monkeypatch.setattr(core_restart.self_restart, "restart", restart)
    monkeypatch.setattr(core_restart, "core_answers", not_answering)
    return state


# ─── what it would do ────────────────────────────────────────────────────


def test_a_frozen_core_under_systemd_is_ended_and_left_to_systemd(systemd) -> None:
    p = core_restart.plan()
    assert p == {"capable": True, "how": "signal", "why": None, "starting": False,
                 "manual_command": "sudo systemctl restart domovoi-core"}


@pytest.mark.parametrize("restart", ["always", "on-failure", "on-abnormal", "on-abort"])
def test_every_restart_policy_that_survives_a_kill(systemd, restart) -> None:
    systemd["unit"] = unit(restart=restart)
    assert core_restart.plan()["how"] == "signal"


@pytest.mark.parametrize("restart", ["no", "on-success", "on-watchdog", ""])
def test_a_unit_systemd_would_not_restart_is_never_killed(systemd, restart) -> None:
    systemd["unit"] = unit(restart=restart)
    p = core_restart.plan()
    assert not p["capable"] and p["how"] is None
    assert f"Restart={restart or 'no'}" in p["why"] and "LINUX_HOST.md" in p["why"]
    assert p["manual_command"] == "sudo systemctl restart domovoi-core"


def test_a_starting_core_is_left_to_start(systemd) -> None:
    systemd["unit"] = unit(active_state="activating", sub_state="start")
    p = core_restart.plan()
    assert p["starting"] and not p["capable"]
    systemd["unit"] = unit(active_state="activating", sub_state="auto-restart", main_pid=0)
    assert core_restart.plan()["starting"]
    # Type=simple says active at once: a young process may still be loading.
    systemd["unit"] = unit(service_type="simple", started_monotonic_sec=time.monotonic() - 30)
    p = core_restart.plan()
    assert p["starting"] and "started 30 s ago" in p["why"]
    # Type=notify is active only once it said READY: no grace needed.
    systemd["unit"] = unit(service_type="notify", started_monotonic_sec=time.monotonic() - 30)
    assert core_restart.plan()["how"] == "signal"


def test_one_restart_at_a_time(systemd) -> None:
    import asyncio

    res = asyncio.run(core_restart.restart_unanswering_core())
    assert res["ok"] and systemd["ended"] == [4242]
    # The real end_core records what it ended; the plan then holds off.
    core_restart._LAST_KILL = (4242, time.monotonic())
    p = core_restart.plan()
    assert p["starting"] and p["why"] == "a restart is already under way"
    # A new process (systemd started it again) can be restarted again.
    systemd["unit"] = unit(main_pid=5151)
    assert core_restart.plan()["how"] == "signal"


def test_another_account_or_no_process_falls_back_to_the_grant(systemd) -> None:
    systemd["may_signal"] = False
    p = core_restart.plan()
    assert not p["capable"] and "another account" in p["why"]
    systemd["sudo"] = (True, None)
    assert core_restart.plan()["how"] == "sudo"
    systemd["may_signal"] = True
    systemd["sudo"] = (False, "no grant")
    systemd["unit"] = unit(active_state="failed", sub_state="failed", main_pid=0)
    p = core_restart.plan()
    assert not p["capable"] and "isn't running (domovoi-core.service is failed)" in p["why"]


def test_a_pid_that_is_not_the_core_is_never_signalled(systemd) -> None:
    systemd["is_core"] = False
    assert core_restart.plan()["how"] != "signal"


def test_no_systemd_no_button(monkeypatch) -> None:
    monkeypatch.setattr(core_restart, "_systemctl", lambda: None)
    p = core_restart.plan()
    assert p["capable"] is False and p["manual_command"] is None
    assert "doesn't run as the systemd unit" in p["why"]


def test_reading_systemd(monkeypatch) -> None:
    out = ("LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=812\n"
           "Restart=on-failure\nType=notify\nExecMainStartTimestampMonotonic=123456789\n")
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(core_restart, "_systemctl", lambda: "/usr/bin/systemctl")
    monkeypatch.setattr(core_restart.subprocess, "run", run)
    u = core_restart.core_unit()
    assert u == CoreUnit("loaded", "active", "running", 812, "on-failure", "notify", 123.456789)
    assert calls[0][:3] == ["/usr/bin/systemctl", "show", "domovoi-core.service"]
    # No sudo anywhere: a plain read over the bus.
    assert "sudo" not in " ".join(calls[0])
    monkeypatch.setattr(core_restart.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "LoadState=not-found\n", ""))
    assert core_restart.core_unit() is None


# ─── the restart itself ──────────────────────────────────────────────────


async def test_an_answering_core_is_never_restarted_from_here(systemd, monkeypatch) -> None:
    async def answering(timeout: float = 3.0) -> bool:
        return True

    monkeypatch.setattr(core_restart, "core_answers", answering)
    res = await core_restart.restart_unanswering_core()
    assert res["ok"] is False and res["answering"] is True
    assert systemd["ended"] == [] and systemd["restarted"] == 0


async def test_the_sudo_route_is_the_cores_own_restart(systemd) -> None:
    systemd["unit"] = unit(main_pid=0, active_state="inactive", sub_state="dead")
    systemd["sudo"] = (True, None)
    res = await core_restart.restart_unanswering_core()
    assert res["ok"] and res["how"] == "sudo" and systemd["restarted"] == 1
    assert systemd["ended"] == []


async def test_status_says_what_the_button_would_do(systemd, monkeypatch) -> None:
    st = await core_restart.status()
    assert st["answering"] is False and st["how"] == "signal"

    async def answering(timeout: float = 3.0) -> bool:
        return True

    monkeypatch.setattr(core_restart, "core_answers", answering)
    assert (await core_restart.status())["answering"] is True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals and /proc")
def test_end_core_dumps_then_kills(tmp_path, monkeypatch) -> None:
    """A real process: SIGUSR1 first (the core's faulthandler prints its
    stacks), then SIGKILL, and end_core sees it gone."""
    marker = tmp_path / "usr1"
    child = subprocess.Popen([sys.executable, "-c", (
        "import signal, time\n"
        f"signal.signal(signal.SIGUSR1, lambda *a: open(r'{marker}', 'w').write('dumped'))\n"
        "print('ready', flush=True)\n"
        "while True: time.sleep(1)\n"
    )], stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "ready"
    monkeypatch.setattr(core_restart, "_LAST_KILL", None)
    notes: list[tuple[str, int]] = []
    monkeypatch.setattr(core_restart.loop_watchdog, "note_last_stall",
                        lambda reason, *, pid, **kw: notes.append((reason, pid)))
    assert core_restart.end_core(child.pid)
    assert child.wait(5) == -signal.SIGKILL
    assert marker.read_text() == "dumped"
    # The Version card says the dashboard did it, and why.
    assert notes == [("the dashboard restarted the core: it did not answer /v1/health", child.pid)]


# ─── the routes and their gate ───────────────────────────────────────────


@requires_db
async def test_the_restart_is_the_security_tier_checked_by_the_web(_db, systemd) -> None:
    async with web_client() as c:
        # Before an admin exists: refused outright (501), never the grace.
        r = await c.post("/api/config/core/restart")
        assert r.status_code == 501
        token = await claim_admin(c)
        c.cookies.clear()   # the setup answer signed this client in by cookie
        r = await c.post("/api/config/core/restart")
        assert r.status_code == 401
        r = await c.post("/api/config/core/restart", headers={
            "X-Requested-With": "domovoi-tests", **bearer("not-a-session")})
        assert r.status_code == 401
        r = await c.post("/api/config/core/restart", cookies={COOKIE: token})
        assert r.status_code == 403
        assert systemd["ended"] == []
        r = await c.post("/api/config/core/restart", headers={
            "X-Requested-With": "domovoi-tests", **bearer(token)})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True and r.json()["how"] == "signal"
        assert systemd["ended"] == [4242]


@requires_db
async def test_the_status_read_is_the_device_tier(_db, systemd) -> None:
    async with web_client() as c:
        token = await claim_admin(c)
        c.cookies.clear()
        assert (await c.get("/api/config/core/restart")).status_code == 401
        r = await c.get("/api/config/core/restart", headers=bearer(token))
        assert r.status_code == 200
        assert r.json()["answering"] is False and r.json()["capable"] is True


def test_the_web_never_imports_more_of_the_core_than_it_preloads() -> None:
    from web.backend import plugin_host

    assert {"domovoi.self_restart", "domovoi.git_version", "domovoi.loop_watchdog"} <= set(
        plugin_host._WEB_BACKEND_CORE_IMPORTS)
    assert "domovoi.self_restart" in sys.modules
    assert os.path.basename(core_restart.__file__) == "core_restart.py"
