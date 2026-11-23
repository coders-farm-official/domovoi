"""A support session's shell on this satellite (1.5.0).

The core's side, and why it is safe, is domovoi/support_sessions.py and
docs/SECURITY_PRIVACY.md, "Support sessions". This side:

* **Who declares it.** Only a Raspberry Pi on a prepared card (stage 1 made
  ``/var/lib/domovoi``) whose service account can NOT use sudo
  (``sudo -n true`` fails at start). A hand-built Pi whose user is in sudo
  never declares it: there the shell would be root. Spare PCs and phones
  never declare it (:func:`should_declare`).
* **The shell.** A PTY running the account's login shell as this process's
  own user (never root), inside the service's own sandbox, with a clean
  environment, ``TERM=xterm-256color`` and the home folder as its working
  directory. No new sudoers line, no new helper, no new port: the bytes
  ride the satellite's existing WebSocket as base64 in JSON text frames.
* **Its own limits**, kept whatever the server says: it refuses a
  ``max_sec`` over 60 minutes, ends at ``max_sec`` and after ``idle_sec``
  without input, reads at most 16 KB a frame and 64 KB a second (over that
  it stops reading the PTY, so the shell slows rather than floods), takes
  at most 4 KB of input a frame, and ends on any socket drop (never
  resumes). One session at a time.
* **The end.** The PTY's process group is hung up (then killed), and the
  client restarts its own service so nothing started with ``nohup`` or
  ``setsid`` lives on: the unit's cgroup kill covers every child.

Linux only. The module imports anywhere; :class:`PtyBackend` needs
``pty``-era modules (``fcntl``, ``termios``) and is never built on Windows.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol

log = logging.getLogger("satellite.support")

MAX_SEC_CAP = 60 * 60
OUTPUT_FRAME_MAX = 16 * 1024
OUTPUT_RATE_MAX = 64 * 1024
INPUT_FRAME_MAX = 4 * 1024
COLS_RANGE = (20, 400)
ROWS_RANGE = (5, 200)
READ_POLL_SEC = 0.2

DEVICE_MODEL = Path("/proc/device-tree/model")
#: Root's state directory, made by stage 1 of a prepared card.
PREPARED_STATE_DIR = Path("/var/lib/domovoi")


def should_declare(
    *,
    is_pc: bool,
    platform: str = sys.platform,
    model_path: Path = DEVICE_MODEL,
    state_dir: Path = PREPARED_STATE_DIR,
    run: Callable[..., Any] = subprocess.run,
) -> bool:
    """Whether this device's hello may declare ``support_shell``: a Pi on a
    prepared card whose account cannot use sudo. Never a spare PC or a
    phone (1.5.0, owner decision 22 Nov)."""
    if is_pc or not platform.startswith("linux"):
        return False
    try:
        if "raspberry pi" not in model_path.read_text(errors="replace").lower():
            return False
    except OSError:
        return False
    if not state_dir.is_dir():
        return False
    try:
        rc = run(["sudo", "-n", "true"], capture_output=True, timeout=5.0,
                 stdin=subprocess.DEVNULL).returncode
    except FileNotFoundError:
        return True     # no sudo at all: nothing to escalate with
    except (OSError, subprocess.SubprocessError):
        return False    # couldn't tell: don't offer it
    if rc == 0:
        log.info("support sessions not offered: this account can use sudo (the shell would be root)")
        return False
    return True


class Backend(Protocol):
    def spawn(self, cols: int, rows: int) -> None: ...
    def read(self, max_bytes: int, timeout: float) -> bytes | None: ...  # None: the shell ended
    def write(self, data: bytes) -> None: ...
    def resize(self, cols: int, rows: int) -> None: ...
    def echo_on(self) -> bool: ...
    def terminate(self) -> int | None: ...


_CTTY_HELPER = (
    "import fcntl,os,sys,termios;"
    "fcntl.ioctl(0,termios.TIOCSCTTY,0);"
    "s=sys.argv[1];os.execve(s,['-'+os.path.basename(s)],os.environ)"
)


class PtyBackend:
    """The real PTY (Linux). The shell is started through a two-line
    helper that makes the PTY its controlling terminal and then becomes the
    login shell, so no Python runs in a forked child of this threaded
    process."""

    def __init__(self) -> None:
        self._master: int | None = None
        self._proc: subprocess.Popen[bytes] | None = None

    def spawn(self, cols: int, rows: int) -> None:
        import pwd

        master, slave = os.openpty()
        self._master = master
        self._winsize(slave, cols, rows)
        pw = pwd.getpwuid(os.getuid())
        shell = pw.pw_shell if pw.pw_shell and os.access(pw.pw_shell, os.X_OK) else "/bin/sh"
        home = pw.pw_dir or "/"
        env = {
            "HOME": home, "USER": pw.pw_name, "LOGNAME": pw.pw_name, "SHELL": shell,
            "PATH": "/usr/local/bin:/usr/bin:/bin", "TERM": "xterm-256color",
            "LANG": "C.UTF-8",
        }
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-I", "-c", _CTTY_HELPER, shell],
                stdin=slave, stdout=slave, stderr=slave, cwd=home, env=env,
                start_new_session=True, close_fds=True,
            )
        finally:
            os.close(slave)

    @staticmethod
    def _winsize(fd: int, cols: int, rows: int) -> None:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def read(self, max_bytes: int, timeout: float) -> bytes | None:
        import select

        assert self._master is not None
        ready, _, _ = select.select([self._master], [], [], timeout)
        if not ready:
            return b""
        try:
            data = os.read(self._master, max_bytes)
        except OSError:      # EIO: every writer of the PTY is gone
            return None
        return data or None

    def write(self, data: bytes) -> None:
        assert self._master is not None
        view = memoryview(data)
        while view:
            n = os.write(self._master, view)
            view = view[n:]

    def resize(self, cols: int, rows: int) -> None:
        if self._master is not None:
            self._winsize(self._master, cols, rows)

    def echo_on(self) -> bool:
        """False only while a program is reading a hidden line: ECHO off
        with the line discipline still canonical, the way getpass, sudo
        and passwd ask. A shell's line editor (readline) and full-screen
        programs turn ECHO off too, but in raw mode (ICANON off), and they
        draw what is typed themselves, so that input is not hidden."""
        import termios

        if self._master is None:
            return True
        try:
            lflag = termios.tcgetattr(self._master)[3]
        except termios.error:
            return True
        return bool(lflag & termios.ECHO) or not bool(lflag & termios.ICANON)

    def terminate(self) -> int | None:
        import signal

        proc = self._proc
        code: int | None = None
        if proc is not None:
            for sig, wait in ((signal.SIGHUP, 2.0), (signal.SIGKILL, 2.0)):
                if proc.poll() is not None:
                    break
                try:
                    os.killpg(proc.pid, sig)
                except OSError:
                    pass
                try:
                    proc.wait(timeout=wait)
                except subprocess.TimeoutExpired:
                    continue
            code = proc.poll()
        if self._master is not None:
            try:
                os.close(self._master)
            except OSError:
                pass
            self._master = None
        return code


def _clamp(value: Any, bounds: tuple[int, int]) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = bounds[0]
    return max(bounds[0], min(bounds[1], v))


class SupportShell:
    """One support session at a time on this device.

    ``emit(frame)`` sends a text frame to the core; ``on_end(reason,
    by_server)`` runs once when a session that OPENED ends (the client
    turns the light off and restarts its service); ``busy()`` names why a
    session can't open now (a drop-in call, a wake-word recording) or
    returns None."""

    def __init__(
        self,
        *,
        declared: bool,
        emit: Callable[[dict[str, Any]], Any],
        on_open: Callable[[], Any] = lambda: None,
        on_end: Callable[[str, bool], Any] = lambda reason, by_server: None,
        busy: Callable[[], str | None] = lambda: None,
        backend_factory: Callable[[], Backend] = PtyBackend,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.declared = declared
        self._emit = emit
        self._on_open = on_open
        self._on_end = on_end
        self._busy = busy
        self._factory = backend_factory
        self._clock = clock
        self._lock = threading.Lock()
        self.session_id: str | None = None
        self._backend: Backend | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.deadline = 0.0
        self.idle_sec = 0.0
        self.last_input = 0.0
        self._seq = 0
        self._echo = True
        self._tokens = float(OUTPUT_RATE_MAX)
        self._refill_at = 0.0

    @property
    def active(self) -> bool:
        return self.session_id is not None

    # ── frames from the core ─────────────────────────────────────────

    def handle(self, frame: dict[str, Any]) -> None:
        kind = frame.get("type")
        if kind == "support_open":
            self.open(frame)
        elif kind == "support_input":
            self.input(frame)
        elif kind == "support_resize":
            self.resize(frame)
        elif kind == "support_close":
            if frame.get("session_id") == self.session_id and self.active:
                self.close(str(frame.get("reason") or "closed"), by_server=True)

    def _refuse(self, sid: Any, reason: str) -> None:
        log.info("support session refused: %s", reason)
        self._emit({"type": "support_refused", "session_id": sid, "reason": reason})

    def open(self, frame: dict[str, Any]) -> None:
        sid = frame.get("session_id")
        if not isinstance(sid, str) or not sid:
            return
        if not self.declared:
            self._refuse(sid, "support sessions are not offered on this device")
            return
        busy = self._busy()
        if busy:
            self._refuse(sid, busy)
            return
        try:
            max_sec = int(frame.get("max_sec"))
            idle_sec = int(frame.get("idle_sec"))
        except (TypeError, ValueError):
            self._refuse(sid, "bad limits")
            return
        if not 0 < max_sec <= MAX_SEC_CAP or not 0 < idle_sec <= max_sec:
            self._refuse(sid, f"limits out of range (at most {MAX_SEC_CAP // 60} minutes)")
            return
        with self._lock:
            if self.session_id is not None:
                self._refuse(sid, "a support session is already open")
                return
            backend = self._factory()
            try:
                backend.spawn(_clamp(frame.get("cols"), COLS_RANGE), _clamp(frame.get("rows"), ROWS_RANGE))
            except Exception as e:  # noqa: BLE001
                self._refuse(sid, f"couldn't start a shell: {type(e).__name__}")
                return
            now = self._clock()
            self.session_id = sid
            self._backend = backend
            self.deadline = now + max_sec
            self.idle_sec = float(idle_sec)
            self.last_input = now
            self._seq = 0
            self._echo = True
            self._tokens = float(OUTPUT_RATE_MAX)
            self._refill_at = now
            self._stop = threading.Event()
        log.info("support session %s opened (%d min, idle %d min)", sid[:8], max_sec // 60, idle_sec // 60)
        self._emit({"type": "support_opened", "session_id": sid})
        try:
            self._on_open()
        except Exception:  # noqa: BLE001
            log.debug("support on_open failed", exc_info=True)
        self._thread = threading.Thread(target=self._reader, args=(sid,), daemon=True,
                                        name="support-shell")
        self._thread.start()

    def input(self, frame: dict[str, Any]) -> None:
        if frame.get("session_id") != self.session_id or self._backend is None:
            return
        raw = frame.get("data")
        if not isinstance(raw, str) or len(raw) > (INPUT_FRAME_MAX * 4) // 3 + 8:
            return
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            return
        if not data or len(data) > INPUT_FRAME_MAX:
            return
        self.last_input = self._clock()
        try:
            self._backend.write(data)
        except OSError:
            self.close("exit")
            return
        self._check_echo()

    def resize(self, frame: dict[str, Any]) -> None:
        if frame.get("session_id") != self.session_id or self._backend is None:
            return
        try:
            self._backend.resize(_clamp(frame.get("cols"), COLS_RANGE), _clamp(frame.get("rows"), ROWS_RANGE))
        except OSError:
            pass

    def connection_lost(self) -> None:
        """The socket dropped: the session ends here and never resumes."""
        if self.active:
            self.close("connection_lost", by_server=True)

    # ── the PTY ──────────────────────────────────────────────────────

    def _check_echo(self) -> None:
        backend = self._backend
        if backend is None:
            return
        on = backend.echo_on()
        if on != self._echo:
            self._echo = on
            self._emit({"type": "support_echo", "session_id": self.session_id, "on": on})

    def step(self) -> bool:
        """One pass of the reader: the clocks, then at most one frame of
        output within the rate. False once the session is over."""
        sid, backend = self.session_id, self._backend
        if sid is None or backend is None or self._stop.is_set():
            return False
        now = self._clock()
        if now >= self.deadline:
            self.close("time_limit")
            return False
        if now - self.last_input >= self.idle_sec:
            self.close("idle")
            return False
        self._tokens = min(float(OUTPUT_RATE_MAX), self._tokens + (now - self._refill_at) * OUTPUT_RATE_MAX)
        self._refill_at = now
        budget = int(min(OUTPUT_FRAME_MAX, self._tokens))
        if budget < 1:
            # Over the rate: stop reading the PTY until the bucket refills.
            # The shell blocks on a full PTY, so it slows rather than floods.
            time.sleep(0.05)
            return True
        chunk = backend.read(budget, READ_POLL_SEC)
        if chunk is None:
            self.close("exit")
            return False
        if chunk:
            self._tokens -= len(chunk)
            self._seq += 1
            self._emit({"type": "support_output", "session_id": sid, "seq": self._seq,
                        "data": base64.b64encode(chunk).decode("ascii")})
        self._check_echo()
        return True

    def _reader(self, sid: str) -> None:
        try:
            while self.session_id == sid and self.step():
                pass
        except Exception:  # noqa: BLE001
            log.exception("support shell reader failed")
            if self.session_id == sid:
                self.close("exit")

    def close(self, reason: str, *, by_server: bool = False) -> None:
        """End the session: hang up the shell's process group, tell the core
        (unless it is the one ending it, or is gone), then ``on_end``."""
        with self._lock:
            sid, backend = self.session_id, self._backend
            if sid is None:
                return
            self.session_id = None
            self._backend = None
            self._stop.set()
        code = None
        if backend is not None:
            try:
                code = backend.terminate()
            except Exception:  # noqa: BLE001
                log.debug("support terminate failed", exc_info=True)
        log.info("support session %s ended: %s", sid[:8], reason)
        if not by_server:
            frame: dict[str, Any] = {"type": "support_closed", "session_id": sid, "reason": reason}
            if code is not None:
                frame["exit_code"] = code
            self._emit(frame)
        try:
            self._on_end(reason, by_server)
        except Exception:  # noqa: BLE001
            log.debug("support on_end failed", exc_info=True)
