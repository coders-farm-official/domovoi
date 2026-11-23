"""The satellite's half of a support session (satellite/support_shell.py).

Pins: only a Pi on a prepared card whose account can't use sudo declares
it (no spare PCs or phones in 1.5.0); the device's own limits (60-minute
cap, the session's time and idle limits, 16 KB frames, 64 KB/s, 4 KB
input, one at a time, busy refusals); echo reporting; the end (the shell's
group hung up, `support_closed` only when the device ended it, the
service restart through the client's `on_end`). A fake PTY everywhere,
and the real one on Linux (skipped elsewhere, run in CI).
"""

from __future__ import annotations

import base64
import inspect
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from satellite import client  # the audio stand-ins hold during collection (conftest)
from satellite import support_shell as sh


# ─── who declares it ──────────────────────────────────────────────────────


def _pi(tmp_path: Path, model: str = "Raspberry Pi Zero 2 W Rev 1.0", prepared: bool = True):
    m = tmp_path / "model"
    m.write_text(model + "\x00")
    state = tmp_path / "state"
    if prepared:
        state.mkdir()
    return m, state


def _run(rc: int | None = None, exc: Exception | None = None):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if exc is not None:
            raise exc
        return SimpleNamespace(returncode=rc)

    return run, calls


def test_a_prepared_pi_without_sudo_declares_it(tmp_path) -> None:
    model, state = _pi(tmp_path)
    run, calls = _run(rc=1)
    assert sh.should_declare(is_pc=False, platform="linux", model_path=model,
                             state_dir=state, run=run)
    assert calls == [["sudo", "-n", "true"]]


def test_a_pi_whose_account_can_sudo_never_declares_it(tmp_path) -> None:
    model, state = _pi(tmp_path)
    run, _ = _run(rc=0)
    assert not sh.should_declare(is_pc=False, platform="linux", model_path=model,
                                 state_dir=state, run=run)
    run, _ = _run(exc=subprocess.TimeoutExpired("sudo", 5))
    assert not sh.should_declare(is_pc=False, platform="linux", model_path=model,
                                 state_dir=state, run=run)


def test_only_a_pi_on_a_prepared_card(tmp_path) -> None:
    run, calls = _run(rc=1)
    model, state = _pi(tmp_path)
    assert not sh.should_declare(is_pc=True, platform="linux", model_path=model, state_dir=state, run=run)
    assert not sh.should_declare(is_pc=False, platform="win32", model_path=model, state_dir=state, run=run)
    assert not sh.should_declare(is_pc=False, platform="darwin", model_path=model, state_dir=state, run=run)
    other = tmp_path / "other-model"
    other.write_text("Generic x86 board")
    assert not sh.should_declare(is_pc=False, platform="linux", model_path=other, state_dir=state, run=run)
    assert not sh.should_declare(is_pc=False, platform="linux", model_path=tmp_path / "none",
                                 state_dir=state, run=run)
    assert not sh.should_declare(is_pc=False, platform="linux", model_path=model,
                                 state_dir=tmp_path / "not-prepared", run=run)
    assert calls == []          # sudo is never even asked off a prepared Pi


def test_the_client_says_it_in_its_hello_only_when_declared() -> None:
    hello = inspect.getsource(client.Satellite._hello_frame)
    assert '"support_shell": bool(getattr(self, "_support", None) and self._support.declared)' in hello
    init = inspect.getsource(client.Satellite.__init__)
    assert "support_shell.should_declare(is_pc=pc_engine.is_pc(cfg))" in init


def test_a_spare_pc_never_declares_it() -> None:
    src = Path(sh.__file__).read_text(encoding="utf-8")
    assert "if is_pc or not platform.startswith(\"linux\")" in src


# ─── the session, on a fake PTY ───────────────────────────────────────────


class FakePty:
    def __init__(self) -> None:
        self.spawned: tuple[int, int] | None = None
        self.written: list[bytes] = []
        self.output: list[bytes] = []
        self.echo = True
        self.ended = False
        self.sizes: list[tuple[int, int]] = []
        self.reads: list[int] = []

    def spawn(self, cols, rows):
        self.spawned = (cols, rows)

    def read(self, max_bytes, timeout):
        self.reads.append(max_bytes)
        if self.ended:
            return None
        if not self.output:
            return b""
        chunk = self.output.pop(0)
        if len(chunk) > max_bytes:
            self.output.insert(0, chunk[max_bytes:])
            chunk = chunk[:max_bytes]
        return chunk

    def write(self, data):
        self.written.append(data)

    def resize(self, cols, rows):
        self.sizes.append((cols, rows))

    def echo_on(self):
        return self.echo

    def terminate(self):
        self.ended = True
        return 0


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make(declared: bool = True, busy=None):
    sent: list[dict] = []
    ends: list[tuple[str, bool]] = []
    opened: list[bool] = []
    pty = FakePty()
    clock = Clock()
    shell = sh.SupportShell(
        declared=declared, emit=sent.append, on_open=lambda: opened.append(True),
        on_end=lambda reason, by_server: ends.append((reason, by_server)),
        busy=busy or (lambda: None), backend_factory=lambda: pty, clock=clock,
    )
    # The tests drive step() themselves.
    shell._thread = None
    return SimpleNamespace(shell=shell, sent=sent, ends=ends, opened=opened, pty=pty, clock=clock)


OPEN = {"type": "support_open", "session_id": "s1", "cols": 100, "rows": 30,
        "max_sec": 1800, "idle_sec": 600}


def opened(w, monkeypatch):
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)
    w.shell.handle(dict(OPEN))
    assert w.sent[-1] == {"type": "support_opened", "session_id": "s1"}
    return w


def test_refusals(monkeypatch) -> None:
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)
    w = make(declared=False)
    w.shell.handle(dict(OPEN))
    assert w.sent[-1]["type"] == "support_refused" and w.pty.spawned is None
    w = make(busy=lambda: "the room is in a drop-in call")
    w.shell.handle(dict(OPEN))
    assert w.sent[-1] == {"type": "support_refused", "session_id": "s1",
                          "reason": "the room is in a drop-in call"}
    w = make()
    w.shell.handle({**OPEN, "max_sec": 3601})                 # over 60 minutes
    assert w.sent[-1]["type"] == "support_refused" and w.pty.spawned is None
    w.shell.handle(dict(OPEN))
    assert w.sent[-1]["type"] == "support_opened" and w.opened == [True]
    w.shell.handle({**OPEN, "session_id": "s2"})              # one at a time
    assert w.sent[-1] == {"type": "support_refused", "session_id": "s2",
                          "reason": "a support session is already open"}


def test_input_and_resize(monkeypatch) -> None:
    w = opened(make(), monkeypatch)
    assert w.pty.spawned == (100, 30)
    w.shell.handle({"type": "support_input", "session_id": "s1",
                    "data": base64.b64encode(b"ls\r").decode()})
    assert w.pty.written == [b"ls\r"]
    w.shell.handle({"type": "support_input", "session_id": "s1",
                    "data": base64.b64encode(b"x" * (sh.INPUT_FRAME_MAX + 1)).decode()})
    w.shell.handle({"type": "support_input", "session_id": "other",
                    "data": base64.b64encode(b"y").decode()})
    w.shell.handle({"type": "support_input", "session_id": "s1", "data": "not base64!"})
    assert w.pty.written == [b"ls\r"]
    w.shell.handle({"type": "support_resize", "session_id": "s1", "cols": 9999, "rows": 1})
    assert w.pty.sizes == [(sh.COLS_RANGE[1], sh.ROWS_RANGE[0])]


def test_output_frames_and_the_rate(monkeypatch) -> None:
    w = opened(make(), monkeypatch)
    monkeypatch.setattr(sh.time, "sleep", lambda s: None)
    w.pty.output = [b"z" * (200 * 1024)]
    for _ in range(20):
        w.shell.step()
    frames = [f for f in w.sent if f["type"] == "support_output"]
    sizes = [len(base64.b64decode(f["data"])) for f in frames]
    assert max(sizes) <= sh.OUTPUT_FRAME_MAX
    assert sum(sizes) <= sh.OUTPUT_RATE_MAX                  # no time passed: one second's worth
    assert [f["seq"] for f in frames] == list(range(1, len(frames) + 1))
    w.clock.t += 1.0                                         # a second later, another
    for _ in range(20):
        w.shell.step()
    sizes = [len(base64.b64decode(f["data"])) for f in w.sent if f["type"] == "support_output"]
    assert sum(sizes) <= 2 * sh.OUTPUT_RATE_MAX and sum(sizes) > sh.OUTPUT_RATE_MAX


def test_echo_changes_are_reported(monkeypatch) -> None:
    w = opened(make(), monkeypatch)
    w.pty.echo = False
    w.shell.step()
    assert {"type": "support_echo", "session_id": "s1", "on": False} in w.sent
    w.pty.echo = True
    w.shell.handle({"type": "support_input", "session_id": "s1",
                    "data": base64.b64encode(b"\r").decode()})
    assert w.sent[-1] == {"type": "support_echo", "session_id": "s1", "on": True}


def test_its_own_time_and_idle_limits(monkeypatch) -> None:
    w = opened(make(), monkeypatch)
    w.clock.t += 599
    assert w.shell.step()
    w.pty.output = [b"tail -f output"]
    w.shell.step()                                          # output is not input
    w.clock.t += 2
    assert not w.shell.step()
    assert w.sent[-1] == {"type": "support_closed", "session_id": "s1", "reason": "idle", "exit_code": 0}
    assert w.ends == [("idle", False)] and w.pty.ended

    w = opened(make(), monkeypatch)
    for _ in range(4):
        w.clock.t += 500
        w.shell.handle({"type": "support_input", "session_id": "s1",
                        "data": base64.b64encode(b"\r").decode()})
    assert not w.shell.step()
    assert w.ends == [("time_limit", False)]


def test_the_shell_exiting(monkeypatch) -> None:
    w = opened(make(), monkeypatch)
    w.pty.ended = True
    assert not w.shell.step()
    assert w.sent[-1]["reason"] == "exit" and w.ends == [("exit", False)]


def test_the_server_ending_it_and_a_dropped_socket(monkeypatch) -> None:
    w = opened(make(), monkeypatch)
    w.shell.handle({"type": "support_close", "session_id": "s1", "reason": "admin_ended"})
    assert w.pty.ended and w.ends == [("admin_ended", True)]
    assert w.sent[-1]["type"] == "support_opened"            # no support_closed back
    assert not w.shell.active

    w = opened(make(), monkeypatch)
    w.shell.connection_lost()
    assert w.ends == [("connection_lost", True)] and w.pty.ended


def test_the_client_restarts_its_service_when_a_session_ends(monkeypatch) -> None:
    sat = object.__new__(client.Satellite)
    calls: list[str] = []
    sat._leds = SimpleNamespace(set_support=lambda on: calls.append(f"leds {on}"), state="idle")
    sat.playback_active = SimpleNamespace(is_set=lambda: False)
    sat._restart_service = lambda: calls.append("restart") or True
    monkeypatch.setattr(client.time, "sleep", lambda s: None)
    started = []

    class NowThread:
        def __init__(self, target, **kw):
            self.target = target

        def start(self):
            started.append(True)
            self.target()

    monkeypatch.setattr(client.threading, "Thread", NowThread)
    sat._support_ended("idle", False)
    assert calls == ["leds False", "restart"] and started == [True]


def test_the_client_refuses_while_busy() -> None:
    sat = object.__new__(client.Satellite)
    sat.dropin_active = SimpleNamespace(is_set=lambda: True)
    sat.wake_recording = SimpleNamespace(is_set=lambda: False)
    assert sat._support_busy() == "the room is in a drop-in call"
    sat.dropin_active = SimpleNamespace(is_set=lambda: False)
    sat.wake_recording = SimpleNamespace(is_set=lambda: True)
    assert sat._support_busy() == "the room is recording wake-word clips"


# ─── the real PTY (Linux; CI runs it) ─────────────────────────────────────


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a PTY needs Linux")
def test_a_real_pty_runs_a_shell_as_this_user() -> None:
    import os

    pty = sh.PtyBackend()
    pty.spawn(80, 24)
    try:
        out = b""
        deadline = time.monotonic() + 10

        def drain(want: bytes) -> bytes:
            nonlocal out
            while want not in out and time.monotonic() < deadline:
                chunk = pty.read(4096, 0.2)
                if chunk is None:
                    break
                out += chunk
            return out

        pty.write(b"echo uid=$(id -u) cols=$(tput cols 2>/dev/null || stty size)\n")
        assert f"uid={os.getuid()}".encode() in drain(b"uid=" + str(os.getuid()).encode())
        # The shell's own line editor is raw mode: not hidden input.
        assert pty.echo_on()
        # A hidden prompt (ECHO off, canonical, as getpass / sudo ask) is.
        pty.write(b"read -s -p pw: x\n")
        deadline2 = time.monotonic() + 5
        while pty.echo_on() and time.monotonic() < deadline2:
            pty.read(4096, 0.1)
        assert not pty.echo_on()
    finally:
        code = pty.terminate()
    assert code is not None
