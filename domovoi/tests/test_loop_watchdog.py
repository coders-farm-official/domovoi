"""The loop watchdog (domovoi/loop_watchdog.py): a frozen event loop, or a
database nothing can reach, ends in every thread's stack in the journal
and the retained log, and an exit the service manager restarts.

Unit tests drive a real event loop on a thread of its own and a watchdog
with short limits whose exit only records the status; the process tests
at the end run the real thing (os._exit, faulthandler) in a child."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from domovoi import lifecycle, loop_watchdog
from domovoi.loop_watchdog import EXIT_CODE, Limits, LoopWatchdog
from domovoi.sd_notify import Notifier

FAST = Limits(stall_sec=1.0, probe_interval_sec=0.3, probe_timeout_sec=0.4, probe_failures=3,
              startup_sec=0.0, heartbeat_sec=0.1, check_sec=0.05, backstop_extra_sec=5.0)


class FakeNotifier(Notifier):
    def __init__(self, watchdog_usec: int | None = None) -> None:
        super().__init__("@fake", watchdog_usec)
        self.sent: list[str] = []
        self.at: list[float] = []

    def notify(self, message: str) -> bool:
        self.sent.append(message)
        self.at.append(time.monotonic())
        return True


class Exited(Exception):
    pass


class Harness:
    """A loop on its own thread, and a watchdog on it whose exit records."""

    def __init__(self, tmp_path: Path, limits: Limits = FAST, probe=None, watchdog_usec=None):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.exited = threading.Event()
        self.code: int | None = None
        # A file, not a pipe: nothing reads it until the end, and a full
        # pipe would block the watchdog's write.
        self.out_path = tmp_path / "stderr.txt"
        self.out_w = os.open(self.out_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        self.notifier = FakeNotifier(watchdog_usec)
        self.backstops: list[float | None] = []

        async def ok_probe() -> None:
            return None

        def exit_fn(code: int) -> None:
            self.code = code
            self.exited.set()

        self.wd = LoopWatchdog(
            limits, probe=probe or ok_probe, notifier=self.notifier, exit_fn=exit_fn,
            backstop=self.backstops.append, report_file=tmp_path / "core-watchdog.log",
            last_file=tmp_path / "core-watchdog-last.json", out_fd=self.out_w,
        )
        self.tmp_path = tmp_path

    def __enter__(self) -> "Harness":
        lifecycle.reset()
        self.thread.start()
        attached = threading.Event()
        self.loop.call_soon_threadsafe(lambda: (self.wd.attach(self.loop), attached.set()))
        assert attached.wait(5)
        self.wd.start()
        return self

    def output(self) -> str:
        return self.out_path.read_bytes().decode("utf-8", "replace")

    def __exit__(self, *exc) -> None:
        def shutdown() -> None:
            for task in asyncio.all_tasks(self.loop):
                task.cancel()
            self.loop.call_later(0.1, self.loop.stop)

        self.loop.call_soon_threadsafe(shutdown)
        self.thread.join(10)
        self.wd.stop()   # after the loop: it closes a probe the loop never started
        if not self.thread.is_alive():
            self.loop.close()
        os.close(self.out_w)
        lifecycle.reset()


def blocking_worker(release: threading.Event) -> None:
    """A plugin worker that does synchronous work on the loop and never
    comes back (the radio fingerprinter's decode, as it was)."""
    release.wait(30)


def test_a_frozen_loop_dumps_every_stack_and_exits(tmp_path) -> None:
    release = threading.Event()
    with Harness(tmp_path) as h:
        h.loop.call_soon_threadsafe(blocking_worker, release)
        assert h.exited.wait(10), "the watchdog never fired on a frozen loop"
        release.set()
        out = h.output()
    assert h.code == EXIT_CODE
    assert "the event loop has not run for" in out
    assert "(the event loop)" in out and "blocking_worker" in out
    assert "--- thread loop-watchdog" in out
    retained = (tmp_path / "core-watchdog.log").read_text(encoding="utf-8")
    assert "blocking_worker" in retained and retained.startswith("===== ")
    last = json.loads((tmp_path / "core-watchdog-last.json").read_text(encoding="utf-8"))
    assert "event loop has not run" in last["reason"] and last["pid"] == os.getpid()
    assert any(m.startswith("STATUS=frozen") for m in h.notifier.sent)


def test_a_healthy_loop_is_left_alone_and_keeps_systemd_fed(tmp_path) -> None:
    with Harness(tmp_path, watchdog_usec=400_000) as h:
        time.sleep(2.5)
        assert not h.exited.is_set()
        out = h.output()
    assert "READY=1" in h.notifier.sent[0]
    assert h.notifier.sent.count("WATCHDOG=1") >= 5
    assert "ERROR" not in out
    # Every heartbeat re-armed the C-level backstop past the stall time.
    assert len(h.backstops) >= 10 and all(b == 6.0 for b in h.backstops)


def test_keepalives_stop_once_the_loop_stalls(tmp_path) -> None:
    """systemd hears WATCHDOG=1 only while the core is healthy: none once
    the loop has gone stall_sec without a heartbeat."""
    release = threading.Event()
    with Harness(tmp_path, watchdog_usec=200_000) as h:
        time.sleep(0.5)
        h.loop.call_soon_threadsafe(blocking_worker, release)
        frozen_at = time.monotonic()
        assert h.exited.wait(10)
        release.set()
    assert h.code == EXIT_CODE
    feeds = [t for t, m in zip(h.notifier.at, h.notifier.sent) if m == "WATCHDOG=1"]
    assert [t for t in feeds if t < frozen_at], "it was fed while healthy"
    assert not [t for t in feeds if t > frozen_at + FAST.stall_sec + FAST.heartbeat_sec]


def test_no_keepalive_from_the_look_that_finds_a_stall(tmp_path) -> None:
    clock = {"t": 100.0}
    n = FakeNotifier(watchdog_usec=1_000_000)
    wd = LoopWatchdog(Limits(stall_sec=1.0, probe_interval_sec=1000.0), notifier=n,
                      clock=lambda: clock["t"], backstop=lambda s: None, exit_fn=lambda c: None,
                      out_fd=os.open(os.devnull, os.O_WRONLY))
    loop = asyncio.new_event_loop()
    try:
        wd._loop, wd._beat_at, wd._probe_last = loop, 100.0, 100.0
        clock["t"] = 100.5
        assert wd.check() is None
        assert n.sent == ["WATCHDOG=1"]
        clock["t"] = 101.2
        assert wd.check() == "the event loop has not run for 1 s"
        assert n.sent == ["WATCHDOG=1"]
    finally:
        loop.close()


def test_database_probes_without_an_answer_are_a_stall(tmp_path) -> None:
    """The loop runs, but nothing that needs the database finishes: the
    thread-pool starvation of 2026-10-14."""

    async def hung_probe() -> None:
        await asyncio.Event().wait()

    with Harness(tmp_path, probe=hung_probe) as h:
        assert h.exited.wait(10)
        out = h.output()
    assert h.code == EXIT_CODE
    assert "3 database probes in a row got no answer" in out
    assert out.count("database probe got no answer") == 3


def test_a_database_that_refuses_is_not_a_stall(tmp_path) -> None:
    async def refused() -> None:
        raise ConnectionRefusedError("nothing listens on 6432")

    with Harness(tmp_path, probe=refused) as h:
        time.sleep(2.0)
        assert not h.exited.is_set()
        out = h.output()
    assert out.count("the database probe failed") == 1     # said once per outage
    assert "ConnectionRefusedError" in out


def test_a_probe_that_recovers_resets_the_count(tmp_path) -> None:
    calls = {"n": 0}

    async def flaky() -> None:
        calls["n"] += 1
        if calls["n"] % 2:
            await asyncio.sleep(5)   # no answer in time

    with Harness(tmp_path, probe=flaky) as h:
        time.sleep(3.0)
        assert not h.exited.is_set()
    assert calls["n"] >= 3


def test_a_stop_is_not_a_stall(tmp_path) -> None:
    release = threading.Event()
    with Harness(tmp_path) as h:
        lifecycle.signal_shutdown("test")
        h.loop.call_soon_threadsafe(blocking_worker, release)
        time.sleep(2.0)
        fired = h.exited.is_set()
        release.set()
    assert not fired


def test_detach_stands_down_and_tells_systemd(tmp_path) -> None:
    release = threading.Event()
    with Harness(tmp_path) as h:
        done = threading.Event()
        h.loop.call_soon_threadsafe(lambda: (h.wd.detach(), done.set()))
        assert done.wait(5)
        h.loop.call_soon_threadsafe(blocking_worker, release)
        time.sleep(2.0)
        fired = h.exited.is_set()
        release.set()
    assert not fired
    assert h.notifier.sent[-1] == "STOPPING=1"
    assert h.backstops[-1] is None


def test_a_startup_that_never_finishes_is_a_stall(tmp_path) -> None:
    clock = {"t": 0.0}
    exits: list[int] = []
    wd = LoopWatchdog(Limits(startup_sec=900.0), exit_fn=exits.append, backstop=lambda s: None,
                      clock=lambda: clock["t"], report_file=tmp_path / "r.log",
                      last_file=tmp_path / "l.json", out_fd=os.open(os.devnull, os.O_WRONLY))
    lifecycle.reset()
    clock["t"] = 899.0
    assert wd.check() is None
    clock["t"] = 900.5
    reason = wd.check()
    assert reason and "startup has not finished after 900 s" in reason
    wd.fire(reason)
    assert exits == [EXIT_CODE]
    # Off with 0.
    off = LoopWatchdog(Limits(startup_sec=0.0), exit_fn=exits.append, clock=lambda: 10_000.0)
    assert off.check() is None


def test_install_honours_the_switch(monkeypatch) -> None:
    class S:
        loop_watchdog_enabled = False

    monkeypatch.setattr(loop_watchdog, "_ACTIVE", None)
    assert loop_watchdog.install(S()) is None
    # Without one installed (every test that starts the app), the lifespan
    # hooks do nothing.
    loop_watchdog.detach()


def test_last_stall_reads_the_note(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(loop_watchdog, "last_stall_path", lambda: tmp_path / "last.json")
    assert loop_watchdog.last_stall() is None
    (tmp_path / "last.json").write_text(json.dumps(
        {"at": "2026-10-14T06:12:00+00:00", "reason": "the event loop has not run for 31 s",
         "pid": 4242, "log": "/home/domovoi/.domovoi/logs/core-watchdog.log", "extra": 1}))
    assert loop_watchdog.last_stall() == {
        "at": "2026-10-14T06:12:00+00:00", "reason": "the event loop has not run for 31 s",
        "pid": 4242, "log": "/home/domovoi/.domovoi/logs/core-watchdog.log"}
    (tmp_path / "last.json").write_text("not json")
    assert loop_watchdog.last_stall() is None


def test_the_retained_log_is_capped(tmp_path) -> None:
    wd = LoopWatchdog(FAST, report_file=tmp_path / "w.log", out_fd=os.open(os.devnull, os.O_WRONLY))
    (tmp_path / "w.log").write_bytes(b"x" * (loop_watchdog.REPORT_MAX_BYTES + 10))
    wd._retain("a new report\n")
    assert (tmp_path / "w.log").read_text().endswith("a new report\n")
    assert (tmp_path / "w.log.1").stat().st_size > loop_watchdog.REPORT_MAX_BYTES


# ─── sd_notify ───────────────────────────────────────────────────────────


def test_notifier_reads_and_clears_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    monkeypatch.setenv("WATCHDOG_USEC", "90000000")
    monkeypatch.setenv("WATCHDOG_PID", str(os.getpid()))
    n = Notifier.from_env()
    assert n.address == "/run/systemd/notify" and n.watchdog_sec == 90.0
    assert "NOTIFY_SOCKET" not in os.environ and "WATCHDOG_USEC" not in os.environ
    # A watchdog meant for another process is not ours.
    monkeypatch.setenv("WATCHDOG_USEC", "90000000")
    monkeypatch.setenv("WATCHDOG_PID", "1")
    assert Notifier.from_env().watchdog_sec is None
    # Off systemd: nothing is sent, nothing raises.
    assert Notifier(None).notify("READY=1") is False


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or sys.platform == "win32",
                    reason="datagram Unix sockets: POSIX only")
def test_notifier_speaks_the_protocol(tmp_path) -> None:
    path = str(tmp_path / "notify.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind(path)
        server.settimeout(2)
        assert Notifier(path).notify("READY=1\nSTATUS=serving")
        assert server.recv(4096) == b"READY=1\nSTATUS=serving"


# ─── the real thing, in a child process ──────────────────────────────────

_CHILD = textwrap.dedent("""
    import asyncio, re, sys, threading, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from domovoi import loop_watchdog
    from domovoi.loop_watchdog import Limits, LoopWatchdog
    mode, tmp = sys.argv[2], Path(sys.argv[3])

    async def ok_probe():
        return None

    def blocking_worker():
        # A worker doing synchronous work on the loop, never coming back.
        time.sleep(600)

    def gil_holder():
        # One C call that never lets go of the interpreter: a regex with
        # catastrophic backtracking. No Python thread can run past it.
        re.match(r"(a+)+$", "a" * 64 + "b")

    async def main():
        loop = asyncio.get_running_loop()
        extra = 2.0 if mode == "gil" else 30.0
        wd = LoopWatchdog(
            Limits(stall_sec=2.0, heartbeat_sec=0.2, check_sec=0.1, probe_interval_sec=60,
                   backstop_extra_sec=extra, startup_sec=0),
            probe=ok_probe, report_file=tmp / "core-watchdog.log",
            last_file=tmp / "core-watchdog-last.json")
        wd.start()
        wd.attach(loop)
        await asyncio.sleep(0.5)
        print("frozen now", flush=True)
        loop.call_soon(gil_holder if mode == "gil" else blocking_worker)
        await asyncio.sleep(600)

    asyncio.run(main())
""")


def _run_child(tmp_path: Path, mode: str, timeout: float = 60) -> subprocess.CompletedProcess:
    repo = str(Path(__file__).resolve().parents[2])
    script = tmp_path / "child.py"
    script.write_text(_CHILD, encoding="utf-8")
    return subprocess.run([sys.executable, str(script), repo, mode, str(tmp_path)],
                          capture_output=True, text=True, timeout=timeout)


def test_a_real_process_with_a_frozen_loop_exits_70_with_its_stacks(tmp_path) -> None:
    t0 = time.monotonic()
    proc = _run_child(tmp_path, "sleep")
    took = time.monotonic() - t0
    assert proc.returncode == EXIT_CODE, proc.stderr[-2000:]
    assert took < 30
    assert "the event loop has not run for" in proc.stderr
    assert "blocking_worker" in proc.stderr and "(the event loop)" in proc.stderr
    assert "blocking_worker" in (tmp_path / "core-watchdog.log").read_text(encoding="utf-8")


def test_a_real_process_holding_the_interpreter_is_ended_by_the_backstop(tmp_path) -> None:
    """The freeze that stops the watchdog's own thread too: faulthandler's
    C-level timer dumps the stacks and exits all the same."""
    proc = _run_child(tmp_path, "gil")
    assert proc.returncode == 1, proc.stderr[-2000:]
    assert "Timeout (0:00:04)!" in proc.stderr
    assert "gil_holder" in proc.stderr
