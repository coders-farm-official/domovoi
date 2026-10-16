"""The process side of the dashboard's "Restart the core", unmocked (review
2026-10-15, note 9): which PID may be signalled, whether it is the core,
how long it has run, and the dump-then-kill. POSIX only (it reads /proc
and sends signals); on this repo's Windows dev box these skip, and
functional-testing/core-resilience/linux-pytest runs them in a Linux
container (plus the systemd container for the whole button)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX: /proc and signals")

from web.backend import core_restart  # noqa: E402


def _child(*argv: str, code: str = "import time; time.sleep(60)") -> subprocess.Popen:
    p = subprocess.Popen([sys.executable, "-c", code, *argv], stdout=subprocess.PIPE, text=True)
    return p


def test_may_signal_this_account_and_nothing_else() -> None:
    assert core_restart._may_signal(os.getpid()) is True
    child = _child()
    try:
        assert core_restart._may_signal(child.pid) is True
    finally:
        child.kill()
        child.wait()
    assert core_restart._may_signal(child.pid) is False       # gone
    assert core_restart._may_signal(0) is False
    assert core_restart._may_signal(-1) is False
    if os.geteuid() != 0:
        assert core_restart._may_signal(1) is False            # init: another account


def test_looks_like_the_core_reads_the_real_command_line() -> None:
    core = _child("-m", "domovoi.main")
    other = _child("something-else")
    try:
        time.sleep(0.2)
        assert core_restart._looks_like_the_core(core.pid) is True
        assert core_restart._looks_like_the_core(other.pid) is False
        assert core_restart._looks_like_the_core(os.getpid()) is ("domovoi.main" in " ".join(sys.argv))
    finally:
        for p in (core, other):
            p.kill()
            p.wait()
    assert core_restart._looks_like_the_core(core.pid) is False   # gone: never the core


def test_process_uptime_is_the_processes_own() -> None:
    child = _child()
    try:
        time.sleep(1.2)
        up = core_restart.process_uptime_sec(child.pid)
        assert up is not None and 1.0 <= up < 30
        mine = core_restart.process_uptime_sec(os.getpid())
        assert mine is not None and mine >= up
    finally:
        child.kill()
        child.wait()
    assert core_restart.process_uptime_sec(child.pid) is None


def test_end_core_dumps_then_kills_the_core_it_checked(tmp_path, monkeypatch) -> None:
    """SIGUSR1 first (the core's faulthandler writes its stacks), then
    SIGKILL; the PID is re-checked to still be the core's before each, and
    the Version card's note names the dashboard."""
    marker = tmp_path / "usr1"
    child = _child("domovoi.main", code=(
        "import signal, time\n"
        f"signal.signal(signal.SIGUSR1, lambda *a: open(r'{marker}', 'w').write('dumped'))\n"
        "print('ready', flush=True)\n"
        "while True: time.sleep(1)\n"
    ))
    assert child.stdout.readline().strip() == "ready"
    monkeypatch.setattr(core_restart, "_LAST_KILL", None)
    monkeypatch.setattr(core_restart, "core_unit", lambda: core_restart.CoreUnit(
        "loaded", "active", "running", child.pid, "on-failure", "notify", None))
    notes: list[tuple[str, int]] = []
    monkeypatch.setattr(core_restart.loop_watchdog, "note_last_stall",
                        lambda reason, *, pid, **kw: notes.append((reason, pid)))
    assert core_restart.end_core(child.pid)
    assert child.wait(5) == -signal.SIGKILL
    assert marker.read_text() == "dumped"
    assert notes and notes[0][1] == child.pid and "the dashboard restarted the core" in notes[0][0]


def test_end_core_refuses_a_pid_that_is_no_longer_the_cores(monkeypatch) -> None:
    child = _child("not-the-core")
    try:
        monkeypatch.setattr(core_restart, "core_unit", lambda: core_restart.CoreUnit(
            "loaded", "active", "running", child.pid, "on-failure", "notify", None))
        assert core_restart.end_core(child.pid) is False
        assert child.poll() is None                     # untouched
        monkeypatch.setattr(core_restart, "core_unit", lambda: core_restart.CoreUnit(
            "loaded", "active", "running", 999999, "on-failure", "notify", None))
        assert core_restart.end_core(child.pid) is False
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()


def test_end_core_checks_again_between_the_dump_and_the_kill(tmp_path, monkeypatch) -> None:
    """Review round 2 note 4: the PID is checked again after SIGUSR1's
    grace, before SIGKILL. Here systemd moves on during the grace (a new
    main PID), while the old process is still there: it is not killed."""
    marker = tmp_path / "usr1"
    child = _child("domovoi.main", code=(
        "import signal, time\n"
        f"signal.signal(signal.SIGUSR1, lambda *a: open(r'{marker}', 'w').write('dumped'))\n"
        "print('ready', flush=True)\n"
        "while True: time.sleep(1)\n"
    ))
    try:
        assert child.stdout.readline().strip() == "ready"
        monkeypatch.setattr(core_restart, "_LAST_KILL", None)
        # The main PID systemd reports: this child until it has dumped its
        # stacks, then another process.
        monkeypatch.setattr(core_restart, "core_unit", lambda: core_restart.CoreUnit(
            "loaded", "active", "running", 999999 if marker.exists() else child.pid,
            "on-failure", "notify", None))
        notes: list[str] = []
        monkeypatch.setattr(core_restart.loop_watchdog, "note_last_stall",
                            lambda reason, **kw: notes.append(reason))
        assert core_restart.end_core(child.pid) is False
        assert marker.read_text() == "dumped"           # the dump went out
        assert child.poll() is None                     # the kill did not
        assert notes == []
    finally:
        child.kill()
        child.wait()


def test_a_ready_note_older_than_the_process_is_not_its(tmp_path, monkeypatch) -> None:
    """Review round 2, case I3, with the real /proc: a note naming this PID
    from before the process started (an earlier boot's) doesn't say it
    finished starting; one written since does."""
    import json

    note = tmp_path / "core-ready.json"
    monkeypatch.setattr(core_restart.loop_watchdog, "ready_path", lambda: note)
    child = _child("domovoi.main")
    try:
        time.sleep(1.5)
        note.write_text(json.dumps({"pid": child.pid, "at": time.time() - 3600}), encoding="utf-8")
        assert core_restart._said_ready(child.pid) is False
        note.write_text(json.dumps({"pid": child.pid, "at": time.time()}), encoding="utf-8")
        assert core_restart._said_ready(child.pid) is True
    finally:
        child.kill()
        child.wait()
