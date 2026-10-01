"""The core's stop, end to end (2026-09-30: every update on the Beelink
waited 90 s for systemd's SIGKILL).

* A real uvicorn server in a subprocess, with the lifespan wiring the core
  uses, signalled the way systemd signals it: it must run its shutdown —
  close the open WebSocket with 1012, cancel what outlives the grace
  timeout, run the lifespan teardown with the event already set — and
  exit on its own, promptly. The same child with the pre-fix wiring
  (``loop.add_signal_handler``) must NOT exit: that proves the test can
  see the bug it guards against (POSIX only; Windows has no
  add_signal_handler).
* ``main()`` hands uvicorn a graceful-shutdown timeout and arms the exit
  watchdog.
* The core lifespan's own teardown is bounded: a plugin runtime that hangs
  on the way out (and swallows the cancel) costs its step's time, and the
  core workers and the timer delivery still stop after it.

DB-free: nothing here may skip.
"""

from __future__ import annotations

import os
import queue
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from domovoi.config import settings

REPO_ROOT = Path(__file__).resolve().parents[2]

_CHILD = textwrap.dedent('''
    import asyncio, logging, os, signal, sys, threading
    from contextlib import asynccontextmanager

    import uvicorn
    from fastapi import FastAPI, WebSocket

    from domovoi import lifecycle

    MODE, PORT = sys.argv[1], int(sys.argv[2])
    logging.basicConfig(level=logging.INFO)   # "shutdown signaled" shows

    @asynccontextmanager
    async def lifespan(app):
        if MODE == "chained":            # what domovoi.main's lifespan does now
            lifecycle.reset()
            lifecycle.install_signal_handlers()
        else:                            # the wiring before 2026-09-30
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lifecycle.signal_shutdown)
        print("READY", flush=True)
        try:
            yield
        finally:
            print(f"TEARDOWN event_was_set={lifecycle.shutdown_event.is_set()}", flush=True)

    app = FastAPI(lifespan=lifespan)

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("ready")
        # A session busy in a long step that never reads again: only the
        # server's graceful-shutdown timeout can end it.
        await asyncio.sleep(3600)

    def _stdin_signals():
        # Windows cannot deliver a SIGTERM from outside; the parent asks
        # the child to raise it on itself (it still goes through the C
        # runtime and the Python-level handler, like a real one).
        for line in sys.stdin:
            name = line.strip()
            if name in ("SIGTERM", "SIGINT"):
                signal.raise_signal(getattr(signal, name))

    threading.Thread(target=_stdin_signals, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info",
                timeout_graceful_shutdown=1)
    print("RUN RETURNED", flush=True)
''')


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Child:
    def __init__(self, mode: str) -> None:
        self.port = _free_port()
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONPATH=str(REPO_ROOT))
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _CHILD, mode, str(self.port)],
            cwd=str(REPO_ROOT), env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self.lines: "queue.Queue[str]" = queue.Queue()
        self.out: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.out.append(line.rstrip("\n"))
            self.lines.put(line)

    def wait_for_line(self, text: str, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any(text in line for line in self.out):
                return True
            try:
                self.lines.get(timeout=0.1)
            except queue.Empty:
                pass
        return any(text in line for line in self.out)

    def send(self, name: str) -> None:
        if os.name == "nt":
            assert self.proc.stdin is not None
            self.proc.stdin.write(name + "\n")
            self.proc.stdin.flush()
        else:
            self.proc.send_signal(getattr(signal, name))

    def exited_within(self, seconds: float) -> bool:
        try:
            self.proc.wait(timeout=seconds)
            return True
        except subprocess.TimeoutExpired:
            return False

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)

    def output(self) -> str:
        return "\n".join(self.out)


def _open_socket(port: int):
    """A client holding a WebSocket open, like a satellite. Returns (the
    close code it saw, a thread to join)."""
    websockets_sync = pytest.importorskip("websockets.sync.client")
    result: dict = {}
    ready = threading.Event()

    def _run() -> None:
        try:
            with websockets_sync.connect(f"ws://127.0.0.1:{port}/ws", open_timeout=5) as ws:
                result["first"] = ws.recv(timeout=5)
                ready.set()
                try:
                    while True:
                        ws.recv(timeout=30)
                except Exception:  # noqa: BLE001 — the close is what we want
                    pass
                result["code"] = ws.close_code
        except Exception as e:  # noqa: BLE001
            result["error"] = repr(e)
            ready.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    assert ready.wait(10), "websocket never connected"
    assert "error" not in result, result
    return result, t


@pytest.mark.parametrize("sig", ["SIGTERM", "SIGINT"])
def test_a_signal_runs_the_servers_shutdown_and_the_process_exits(sig) -> None:
    """SIGTERM is systemd's stop; SIGINT is Ctrl+C on the dev box."""
    child = _Child("chained")
    try:
        assert child.wait_for_line("Uvicorn running on", 30), child.output()
        seen, reader = _open_socket(child.port)
        t0 = time.monotonic()
        child.send(sig)
        assert child.exited_within(15), (
            f"the server never shut down after {sig}:\n" + child.output())
        took = time.monotonic() - t0
        reader.join(5)
    finally:
        child.kill()
    out = child.output()
    # The event flipped as the signal landed...
    assert f"shutdown signaled ({sig})" in out, out
    # ...and uvicorn's own shutdown ran: the thing the old wiring prevented.
    assert "Shutting down" in out, out
    assert "Waiting for application shutdown" in out, out
    assert "Application shutdown complete" in out, out
    # The session that would never finish was cut off by the grace timeout.
    assert "timeout graceful shutdown exceeded" in out, out
    # The lifespan teardown ran, with the shutdown already flagged.
    assert "TEARDOWN event_was_set=True" in out, out
    # The satellite got a proper close, and will reconnect to the next process.
    assert seen.get("code") == 1012, seen
    assert took < 10, took
    if sig == "SIGTERM" and os.name != "nt":
        # uvicorn re-raises the signal once it is done: the exit systemd
        # counts as a clean stop.
        assert child.proc.returncode == -signal.SIGTERM, child.proc.returncode
    if sig == "SIGINT":
        # Ctrl+C ends in a KeyboardInterrupt uvicorn.run swallows: a normal
        # return, as before.
        assert "RUN RETURNED" in out, out


@pytest.mark.skipif(os.name == "nt", reason="asyncio has no add_signal_handler on Windows")
def test_the_old_wiring_never_shuts_down() -> None:
    """The bug itself, kept visible: with the handler replaced, SIGTERM only
    flips the event. uvicorn never shuts down and the process stays up
    (systemd would SIGKILL it after TimeoutStopSec)."""
    child = _Child("replaced")
    try:
        assert child.wait_for_line("Uvicorn running on", 30), child.output()
        child.send("SIGTERM")
        assert not child.exited_within(3), child.output()
        out = child.output()
        assert "shutdown signaled" in out, out
        assert "Shutting down" not in out, out
        assert "TEARDOWN" not in out, out
    finally:
        child.kill()


def test_main_hands_uvicorn_a_graceful_timeout_and_arms_the_watchdog(monkeypatch) -> None:
    import uvicorn

    from domovoi import lifecycle, main as core_main

    captured: dict = {}
    armed: list[float] = []

    def fake_run(_app, **kwargs):
        captured.update(kwargs)

    real_watchdog = lifecycle.exit_watchdog

    def fake_watchdog(deadline_sec, **kw):
        armed.append(deadline_sec)
        return real_watchdog(deadline_sec, exit_fn=lambda code: None, **kw)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(core_main.lifecycle, "exit_watchdog", fake_watchdog)
    monkeypatch.setattr("sys.argv", ["domovoi"])
    core_main.main()
    assert captured["timeout_graceful_shutdown"] == settings.shutdown_grace_sec
    assert armed == [settings.shutdown_deadline_sec]


def test_the_bounds_nest_inside_systemds_timeout() -> None:
    """grace + teardown fit inside the watchdog's deadline, and the
    deadline inside the documented TimeoutStopSec (docs/LINUX_HOST.md), so
    the core's own account of a hang reaches the journal before a SIGKILL."""
    assert 0 < settings.shutdown_grace_sec
    assert settings.shutdown_grace_sec + settings.shutdown_teardown_sec < settings.shutdown_deadline_sec
    doc = (REPO_ROOT / "docs" / "LINUX_HOST.md").read_text(encoding="utf-8")
    core_unit = doc[doc.index("domovoi-core.service`**"):]
    core_unit = core_unit[:core_unit.index("[Install]")]
    stop = [line for line in core_unit.splitlines() if line.startswith("TimeoutStopSec=")]
    assert stop, "the documented core unit has no TimeoutStopSec"
    assert settings.shutdown_deadline_sec < float(stop[0].split("=", 1)[1])
    assert "KillMode=" in core_unit


def test_the_lifespan_teardown_is_bounded_even_by_a_plugin_that_hangs(monkeypatch) -> None:
    """A plugin runtime that does not finish unloading (and eats the
    cancel) costs its own step's time; the core workers and the timer
    delivery still stop after it, and the teardown stays inside its
    budget."""
    import asyncio

    from fastapi.testclient import TestClient

    from domovoi import main as core_main
    from domovoi.plugins_runtime.loader import LOADER
    from domovoi.plugins_runtime.workers import WORKERS
    from domovoi.timer_delivery import TimerDelivery

    stopped: list[tuple[str, float]] = []

    async def _hung_shutdown(**_kw) -> None:
        # Eats every cancel for 6 s, then gives up: long enough to outlast
        # its teardown step, short enough that the test's own event loop
        # (which, unlike the core's SIGTERM exit, waits for leftover tasks)
        # can close.
        end = time.monotonic() + 6.0
        while time.monotonic() < end:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue

    real_stop_owner = WORKERS.stop_owner

    async def _stop_owner(owner, **kw):
        stopped.append((owner, time.monotonic()))
        await real_stop_owner(owner, **kw)

    real_delivery_shutdown = TimerDelivery.shutdown

    async def _delivery_shutdown(self, **kw):
        stopped.append(("timer delivery", time.monotonic()))
        await real_delivery_shutdown(self, **kw)

    monkeypatch.setattr(LOADER, "shutdown", _hung_shutdown)
    monkeypatch.setattr(WORKERS, "stop_owner", _stop_owner)
    monkeypatch.setattr(TimerDelivery, "shutdown", _delivery_shutdown)
    monkeypatch.setattr(core_main, "_STOP_WORKERS_SEC", 0.3)
    monkeypatch.setattr(settings, "shutdown_teardown_sec", 3.0)

    client = TestClient(core_main.app)
    client.__enter__()
    t0 = time.monotonic()
    client.__exit__(None, None, None)
    assert [name for name, _ in stopped] == ["core", "timer delivery"]
    # plugins: 0.3 + 1 s step, + the 0.5 s cancel grace; then the rest.
    assert stopped[-1][1] - t0 < 3.0, stopped[-1][1] - t0
