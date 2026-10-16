"""Restart a core that is frozen, from the dashboard (incident 2026-10-14):
web/backend/core_restart.py and its two routes.

The dashboard's Restart used to go THROUGH the core, so a frozen core
could not be restarted from it. Now the web process ends a core whose
event loop has given no answer at all for longer than the core's own
watchdog waits, and systemd starts it again; under the security tier,
which the web checks itself against the shared admin_sessions table.

Review 2026-10-15: the first build ended cores that were NOT frozen (a
503 from a database that was down, a 12 s busy loop, a 5 s health answer,
a Type=simple core still starting). Those are pinned here, against real
HTTP servers where it matters, and in functional-testing/core-resilience/
systemd under a real systemd (cases A, B2, C, I2); the POSIX process parts
run unmocked in test_core_restart_posix.py."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time

import pytest

from web.backend import core_restart
from web.backend.core_restart import CoreUnit, Probe

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
    """Script what systemd, the kernel and the core say; record what would
    be done. The core is silent and has been for long enough, unless a
    test says otherwise."""
    state = {"unit": unit(), "may_signal": True, "is_core": True, "sudo": (False, "no grant"),
             "uptime": 3600.0, "ready": True, "probes": [], "probe": Probe(alive=False),
             "ended": [], "restarted": 0}
    monkeypatch.setattr(core_restart, "core_unit", lambda: state["unit"])
    monkeypatch.setattr(core_restart, "_may_signal", lambda pid: state["may_signal"])
    monkeypatch.setattr(core_restart, "_looks_like_the_core", lambda pid: state["is_core"])
    monkeypatch.setattr(core_restart, "process_uptime_sec", lambda pid: state["uptime"])
    monkeypatch.setattr(core_restart, "_said_ready", lambda pid: state["ready"])
    monkeypatch.setattr(core_restart, "_systemctl", lambda: "/usr/bin/systemctl")
    monkeypatch.setattr(core_restart.self_restart, "capable", lambda mode=None: state["sudo"])
    monkeypatch.setattr(core_restart, "_LAST_KILL", None)
    monkeypatch.setattr(core_restart, "_SILENT_SINCE", {})
    monkeypatch.setattr(core_restart, "silence_needed_sec", lambda: 0.0)
    monkeypatch.setattr(core_restart, "PROBE_EVERY_SEC", 0.01)

    def end_core(pid: int) -> bool:
        state["ended"].append(pid)
        return True

    async def restart():
        state["restarted"] += 1
        return {"ok": True, "mode": "update", "units": ["domovoi-update.service"],
                "delay_sec": 1.0, "error": None, "in_progress": False}

    async def probe(timeout: float = 3.0, *, with_health: bool = True) -> Probe:
        if state["probes"]:
            return state["probes"].pop(0)
        return state["probe"]

    monkeypatch.setattr(core_restart, "end_core", end_core)
    monkeypatch.setattr(core_restart.self_restart, "restart", restart)
    monkeypatch.setattr(core_restart, "probe_core", probe)
    return state


# ─── what it would do ────────────────────────────────────────────────────


def test_a_frozen_core_under_systemd_is_ended_and_left_to_systemd(systemd) -> None:
    p = core_restart.plan()
    assert p == {"capable": True, "how": "signal", "why": None, "starting": False,
                 "manual_command": "sudo systemctl restart domovoi-core", "pid": 4242}


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


def test_a_type_simple_core_still_starting_is_left_alone(systemd) -> None:
    """Review case I2: a Type=simple core 126 s into a slow first boot was
    killed (the grace was 120 s). The grace is now the core's own startup
    limit, by the process's uptime, unless it said it is ready."""
    systemd["unit"] = unit(service_type="simple")
    systemd["ready"] = False
    systemd["uptime"] = 126.0
    p = core_restart.plan()
    assert p["starting"] and not p["capable"] and "started 126 s ago" in p["why"]
    systemd["uptime"] = core_restart.startup_limit_sec() + 1
    assert core_restart.plan()["how"] == "signal"       # past the core's own limit
    systemd["uptime"] = 126.0
    systemd["ready"] = True
    assert core_restart.plan()["how"] == "signal"       # it finished starting, then froze
    # Type=notify is active only once it said READY: no grace needed.
    systemd["unit"] = unit(service_type="notify")
    systemd["ready"] = False
    assert core_restart.plan()["how"] == "signal"


def test_a_ready_note_counts_only_for_the_process_that_wrote_it(tmp_path, monkeypatch) -> None:
    """Review round 2, case I3: a note from an earlier boot named the PID
    a Type=simple core starting now happened to have, and the core was
    ended mid-start. A note counts only when it names the PID and was
    written after that process started; one whose age can't be told
    (no /proc) doesn't count either."""
    import json as _json

    note = tmp_path / "core-ready.json"
    monkeypatch.setattr(core_restart.loop_watchdog, "ready_path", lambda: note)
    monkeypatch.setattr(core_restart, "process_uptime_sec", lambda pid: 126.0)
    now = time.time()

    def write(pid: int, at: float) -> None:
        note.write_text(_json.dumps({"pid": pid, "at": at}), encoding="utf-8")

    write(4242, now - 3600)                      # an earlier boot's core, the same PID
    assert core_restart._said_ready(4242) is False
    write(4242, now - 100)                       # written 26 s into this process's life
    assert core_restart._said_ready(4242) is True
    write(4242, now - 127)                       # within the slack of its start
    assert core_restart._said_ready(4242) is True
    write(5151, now - 100)                       # another process's
    assert core_restart._said_ready(4242) is False
    write(4242, now - 100)
    monkeypatch.setattr(core_restart, "process_uptime_sec", lambda pid: None)
    assert core_restart._said_ready(4242) is False   # no way to tell its age
    note.write_text('{"pid": 4242}', encoding="utf-8")
    monkeypatch.setattr(core_restart, "process_uptime_sec", lambda pid: 126.0)
    assert core_restart._said_ready(4242) is False   # no time on it


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
    assert "sudo" not in " ".join(calls[0])
    monkeypatch.setattr(core_restart.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "LoadState=not-found\n", ""))
    assert core_restart.core_unit() is None


def test_the_silence_needed_is_longer_than_the_cores_own_watchdog(monkeypatch) -> None:
    from domovoi.config import settings

    monkeypatch.setattr(settings, "loop_watchdog_stall_sec", 30.0)
    assert core_restart.silence_needed_sec() == 45.0
    monkeypatch.setattr(settings, "loop_watchdog_stall_sec", 90.0)
    assert core_restart.silence_needed_sec() == 105.0
    monkeypatch.setattr(settings, "loop_watchdog_stall_sec", 5.0)   # never under 30 s
    assert core_restart.silence_needed_sec() == 45.0


# ─── the restart itself ──────────────────────────────────────────────────


async def test_a_core_whose_loop_answers_is_never_ended(systemd) -> None:
    """Review cases A and C: a core that answers at all (a 503 from a
    database that is down, a slow health answer, a 404 from an older core)
    is running. Nothing is ended; the answer says what is wrong."""
    systemd["probe"] = Probe(alive=True, health=503, db_down=True)
    res = await core_restart.restart_unanswering_core()
    assert res["ok"] is False and res["answering"] is True and res["db_down"] is True
    assert "database isn't answering" in res["error"]
    systemd["probe"] = Probe(alive=True, health=None)
    res = await core_restart.restart_unanswering_core()
    assert res["ok"] is False and res["answering"] is True
    systemd["probe"] = Probe(alive=True, health=200)
    res = await core_restart.restart_unanswering_core()
    assert "use Restart Domovoi" in res["error"]
    assert systemd["ended"] == [] and systemd["restarted"] == 0


async def test_a_press_waits_out_the_silence_and_stops_when_the_core_answers(systemd, monkeypatch) -> None:
    """Review case B2: a 12 s busy loop (the core's own watchdog counts it
    healthy) was killed at once. A press now waits for the silence the
    core's watchdog would need, and the core answering ends the wait."""
    monkeypatch.setattr(core_restart, "silence_needed_sec", lambda: 0.5)
    systemd["probes"] = [Probe(alive=False), Probe(alive=False), Probe(alive=False), Probe(alive=True)]
    t0 = time.monotonic()
    res = await core_restart.restart_unanswering_core()
    assert res["ok"] is False and res["answering"] is True and "busy, not frozen" in res["error"]
    assert systemd["ended"] == []
    assert time.monotonic() - t0 < 2.0


async def test_a_core_silent_for_the_whole_wait_is_ended(systemd, monkeypatch) -> None:
    monkeypatch.setattr(core_restart, "silence_needed_sec", lambda: 0.3)
    t0 = time.monotonic()
    res = await core_restart.restart_unanswering_core()
    assert res["ok"] is True and res["how"] == "signal"
    assert systemd["ended"] == [4242]
    assert time.monotonic() - t0 >= 0.3


async def test_a_silence_already_watched_counts(systemd, monkeypatch) -> None:
    """The page polls the status read: a press after the core has been
    watched silent long enough acts at once."""
    monkeypatch.setattr(core_restart, "silence_needed_sec", lambda: 30.0)
    await core_restart.status()
    _silent_for(4242, 31.0, last_probe_ago=4.0)     # watched by the page's 5 s polls
    t0 = time.monotonic()
    res = await core_restart.restart_unanswering_core()
    assert res["ok"] is True and systemd["ended"] == [4242]
    assert time.monotonic() - t0 < 1.0


def _silent_for(pid: int, sec: float, *, last_probe_ago: float) -> None:
    now = time.monotonic()
    core_restart._SILENT_SINCE[pid] = (now - sec, now - last_probe_ago)


async def test_an_old_silence_nobody_watched_does_not_count(systemd, monkeypatch) -> None:
    """Review round 2, case N: the core was busy while the page read the
    status, then answered with nobody looking (the page had closed), then
    was busy again when an admin pressed. The old silence counted, and a
    12 s busy loop was ended after 12.6 s. A gap of more than
    SILENCE_GAP_SEC since the last silent probe starts the count afresh,
    so the press waits again, and the core answering ends that wait."""
    monkeypatch.setattr(core_restart, "silence_needed_sec", lambda: 0.5)
    _silent_for(4242, 73.0, last_probe_ago=core_restart.SILENCE_GAP_SEC + 48.0)
    systemd["probes"] = [Probe(alive=False)] * 4 + [Probe(alive=True)]
    res = await core_restart.restart_unanswering_core()
    assert res["ok"] is False and res["answering"] is True and "busy, not frozen" in res["error"]
    assert systemd["ended"] == []


def test_silence_is_counted_per_process(monkeypatch) -> None:
    """The silence a press needs belongs to one main PID: a new process
    starts its own count, an old one's is dropped (a PID comes round again
    after enough starts, and must not inherit a silence it never had), and
    any answer clears them all."""
    monkeypatch.setattr(core_restart, "_SILENT_SINCE", {})
    core_restart._note_probe(4242, False)
    _silent_for(4242, 100.0, last_probe_ago=2.0)            # silent for 100 s, watched
    assert core_restart._note_probe(4242, False) >= 100.0
    assert core_restart._note_probe(5151, False) < 1.0      # systemd's next core
    assert core_restart._note_probe(4242, False) < 1.0      # the PID again, later: afresh
    _silent_for(4242, 50.0, last_probe_ago=2.0)
    assert core_restart._note_probe(0, True) == 0.0
    assert core_restart._SILENT_SINCE == {}


def test_a_gap_between_silent_probes_breaks_the_silence(monkeypatch) -> None:
    """Only silence somebody watched counts (review round 2, case N): the
    last silent probe within SILENCE_GAP_SEC continues the run, an older
    one starts it again; at the edge, both sides."""
    gap = core_restart.SILENCE_GAP_SEC
    monkeypatch.setattr(core_restart, "_SILENT_SINCE", {})
    _silent_for(4242, 40.0, last_probe_ago=gap - 1.0)
    assert core_restart._note_probe(4242, False) >= 40.0
    _silent_for(4242, 40.0, last_probe_ago=gap + 1.0)
    assert core_restart._note_probe(4242, False) < 1.0
    # And the run carries on from there, probe by probe.
    _silent_for(4242, 5.0, last_probe_ago=1.0)
    assert 5.0 <= core_restart._note_probe(4242, False) < 6.0
    # A press never breaks its own run: one turn of its wait (a pause, a
    # probe, systemd's MainPID) fits in the gap even when each takes its
    # longest.
    assert (core_restart.PROBE_EVERY_SEC + core_restart.PROBE_TIMEOUT_SEC
            + core_restart._SYSTEMCTL_TIMEOUT_SEC) < gap


async def test_a_new_process_meanwhile_stops_the_press(systemd, monkeypatch) -> None:
    """systemd started another core while the press waited (its own
    watchdog did its job): nothing is ended."""
    monkeypatch.setattr(core_restart, "silence_needed_sec", lambda: 0.3)
    calls = {"n": 0}

    def changing():
        calls["n"] += 1
        return unit() if calls["n"] < 3 else unit(main_pid=5151)

    monkeypatch.setattr(core_restart, "core_unit", changing)
    res = await core_restart.restart_unanswering_core()
    assert res["ok"] is False and res["starting"] is True and "restarting by itself" in res["error"]
    assert systemd["ended"] == []


async def test_every_guard_runs_again_before_the_kill(systemd, monkeypatch) -> None:
    """Review note 8: the policy, the account and the PID are read again,
    fresh, right before the kill."""
    monkeypatch.setattr(core_restart, "silence_needed_sec", lambda: 0.1)
    calls = {"n": 0}

    def drifting():
        calls["n"] += 1
        return unit() if calls["n"] == 1 else unit(restart="no")

    monkeypatch.setattr(core_restart, "core_unit", drifting)
    res = await core_restart.restart_unanswering_core()
    assert res["ok"] is False and "Restart=no" in res["error"]
    assert systemd["ended"] == []


async def test_one_restart_at_a_time(systemd) -> None:
    res = await core_restart.restart_unanswering_core()
    assert res["ok"] and systemd["ended"] == [4242]
    core_restart._LAST_KILL = (4242, time.monotonic())
    p = core_restart.plan()
    assert p["starting"] and p["why"] == "a restart is already under way"
    systemd["unit"] = unit(main_pid=5151)
    assert core_restart.plan()["how"] == "signal"


async def test_two_presses_at_once_end_the_core_once(systemd, monkeypatch) -> None:
    """Review case D (unmocked in the systemd container too): two admins
    press during the same silence. Both used to wait it out and both
    signalled; now the second is told a restart is under way."""
    monkeypatch.setattr(core_restart, "silence_needed_sec", lambda: 0.3)
    a, b = await asyncio.gather(core_restart.restart_unanswering_core(),
                                core_restart.restart_unanswering_core())
    assert sorted([a["ok"], b["ok"]]) == [False, True]
    told = b if a["ok"] else a
    assert "already under way" in told["error"]
    assert systemd["ended"] == [4242]
    assert core_restart._PRESS_UNDER_WAY is False       # and the next press may run


async def test_the_sudo_route_is_the_cores_own_restart(systemd) -> None:
    systemd["unit"] = unit(main_pid=0, active_state="inactive", sub_state="dead")
    systemd["sudo"] = (True, None)
    res = await core_restart.restart_unanswering_core()
    assert res["ok"] and res["how"] == "sudo" and systemd["restarted"] == 1
    assert systemd["ended"] == []


async def test_status_says_what_the_button_would_do(systemd) -> None:
    st = await core_restart.status()
    assert st["answering"] is False and st["how"] == "signal" and "pid" not in st
    assert st["silence_needed_sec"] == 0.0
    systemd["probe"] = Probe(alive=True, health=503, db_down=True)
    st = await core_restart.status()
    assert st["answering"] is True and st["db_down"] is True and st["capable"] is False


# ─── probe_core against real HTTP servers (no mocks) ─────────────────────


async def _serve(handler) -> tuple[asyncio.AbstractServer, int]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def _responder(routes: dict[str, tuple[int, bytes, float]]):
    async def handle(reader, writer):
        line = await reader.readline()
        path = line.split()[1].decode() if len(line.split()) > 1 else "/"
        while (await reader.readline()) not in (b"\r\n", b"\n", b""):
            pass
        status, body, delay = routes.get(path, (404, b'{"detail":"Not Found"}', 0.0))
        if delay:
            await asyncio.sleep(delay)
        writer.write(f"HTTP/1.1 {status} X\r\ncontent-length: {len(body)}\r\nconnection: close\r\n\r\n"
                     .encode() + body)
        try:
            await writer.drain()
        except ConnectionError:
            pass
        writer.close()
    return handle


async def test_probe_any_answer_from_the_loop_is_alive(monkeypatch) -> None:
    """The kill rule, unmocked: ANY HTTP answer on /v1/alive is a running
    loop (the 503 of a database that is down, a slow health check, a 404
    from a core older than /v1/alive). A mutant that wants a 200 from
    /v1/health (the first build's rule) fails here."""
    cases = {
        "db down": ({"/v1/alive": (200, b'{"alive":true}', 0.0),
                     "/v1/health": (503, b'{"detail":"db unreachable: refused"}', 0.0)},
                    Probe(alive=True, health=503, db_down=True)),
        "slow health": ({"/v1/alive": (200, b"{}", 0.0), "/v1/health": (200, b"{}", 5.0)},
                        Probe(alive=True, health=None)),
        "older core": ({"/v1/health": (200, b"{}", 0.0)}, Probe(alive=True, health=200)),
        "healthy": ({"/v1/alive": (200, b"{}", 0.0), "/v1/health": (200, b"{}", 0.0)},
                    Probe(alive=True, health=200)),
        # The core's other 503 is not about the database: not named so.
        "no handlers": ({"/v1/alive": (200, b"{}", 0.0),
                         "/v1/health": (503, b'{"detail":"no handlers registered"}', 0.0)},
                        Probe(alive=True, health=503, db_down=False)),
        "server error": ({"/v1/alive": (200, b"{}", 0.0), "/v1/health": (500, b"oops", 0.0)},
                         Probe(alive=True, health=500, db_down=False)),
    }
    for name, (routes, want) in cases.items():
        server, port = await _serve(_responder(routes))
        monkeypatch.setenv("DOMOVOI_URL", f"http://127.0.0.1:{port}")
        try:
            got = await core_restart.probe_core(timeout=2.0)
        finally:
            server.close()
            await server.wait_closed()
        assert got == want, name


async def test_probe_a_frozen_or_absent_core_is_silent(monkeypatch) -> None:
    async def never(reader, writer):
        await asyncio.sleep(30)

    server, port = await _serve(never)          # accepts, never answers: frozen
    monkeypatch.setenv("DOMOVOI_URL", f"http://127.0.0.1:{port}")
    try:
        t0 = time.monotonic()
        assert await core_restart.probe_core(timeout=1.0) == Probe(alive=False)
        assert time.monotonic() - t0 < 3
    finally:
        server.close()
    server2, port2 = await _serve(never)
    server2.close()
    await server2.wait_closed()
    monkeypatch.setenv("DOMOVOI_URL", f"http://127.0.0.1:{port2}")   # nothing listens
    assert await core_restart.probe_core(timeout=1.0) == Probe(alive=False)


async def test_a_press_against_a_real_core_that_answers_503_ends_nothing(monkeypatch) -> None:
    """Review case A end to end on this side: a real server answering 503
    "db unreachable" and plan() pretending everything else would allow a
    kill. Nothing is ended."""
    ended: list[int] = []
    monkeypatch.setattr(core_restart, "end_core", lambda pid: ended.append(pid) or True)
    monkeypatch.setattr(core_restart, "plan", lambda: {"capable": True, "how": "signal", "why": None,
                                                       "starting": False, "manual_command": None,
                                                       "pid": 4242})
    server, port = await _serve(_responder({
        "/v1/alive": (200, b"{}", 0.0),
        "/v1/health": (503, b'{"detail":"db unreachable: connection refused"}', 0.0)}))
    monkeypatch.setenv("DOMOVOI_URL", f"http://127.0.0.1:{port}")
    try:
        res = await core_restart.restart_unanswering_core()
    finally:
        server.close()
    assert res["ok"] is False and res["db_down"] is True and ended == []


def test_may_signal_never_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(core_restart, "_WINDOWS", True)
    assert core_restart._may_signal(os.getpid()) is False


# ─── the routes and their gate ───────────────────────────────────────────


@requires_db
async def test_the_restart_is_the_security_tier_checked_by_the_web(_db, systemd) -> None:
    async with web_client() as c:
        r = await c.post("/api/config/core/restart")
        assert r.status_code == 501
        token = await claim_admin(c)
        c.cookies.clear()
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


@requires_db
async def test_web_health_tells_a_running_core_from_a_frozen_one(_db, monkeypatch) -> None:
    """/api/health's core_alive: a core whose /v1/health fails but whose
    loop answers is not the frozen one (the Satellites banner says so)."""
    server, port = await _serve(_responder({
        "/v1/alive": (200, b"{}", 0.0),
        "/v1/health": (503, b'{"detail":"db unreachable: refused"}', 0.0)}))
    monkeypatch.setenv("DOMOVOI_URL", f"http://127.0.0.1:{port}")
    try:
        async with web_client() as c:
            h = (await c.get("/api/health")).json()
    finally:
        server.close()
    assert h["domovoi_reachable"] is False and h["core_alive"] is True
    monkeypatch.setenv("DOMOVOI_URL", "http://127.0.0.1:9")
    async with web_client() as c:
        h = (await c.get("/api/health")).json()
    assert h["domovoi_reachable"] is False and h["core_alive"] is False


def test_the_web_never_imports_more_of_the_core_than_it_preloads() -> None:
    from web.backend import plugin_host

    assert {"domovoi.self_restart", "domovoi.git_version", "domovoi.loop_watchdog"} <= set(
        plugin_host._WEB_BACKEND_CORE_IMPORTS)
    assert "domovoi.self_restart" in sys.modules
