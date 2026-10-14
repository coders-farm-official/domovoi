"""The Windows satellite's supervisor: what ``systemd --user`` is on Linux.

Spare-device satellites on Windows (2026-10-13). The microphone belongs to
the signed-in session, so the satellite is not a Windows service: it is a
background process of the person's own account, started at sign-in by an
entry under ``HKCU\\...\\CurrentVersion\\Run`` (Settings > Apps > Startup
lists it) or from the Start menu, and ended by Windows at sign-out. No
administrator rights, no scheduled task, nothing that runs while nobody is
signed in.

    pythonw.exe -m satellite.win_service [--root DIR] [--no-tray]

What it does, like the Linux unit (satellite/scripts/domovoi-satellite-user.service):

* starts the tray (``pythonw -m satellite.win_tray``) once, and the engine
  (``python -m satellite.client``, no window) with the install folder as its
  home, so its ``~/.domovoi`` is ``<root>\\.domovoi`` and nothing lands in
  the person's own profile folder;
* keeps the engine's output in ``<root>\\logs\\satellite.log`` (5 MB, three
  old copies) — the engine never writes the approval code there;
* starts the engine again when it asks (exit 75, after a settings change or
  an update), and after a crash with a growing pause (5 s up to a minute);
  leaves it stopped when it exits 78 (no pinned server identity) or 0 (a
  stop someone asked for);
* stops, engine first, when the tray's "Stop satellite…" or the uninstaller
  sets the user's stop event (satellite/win32.py ``STOP_EVENT``); the
  engine watches the same event and shuts down cleanly, and is ended after
  :data:`STOP_GRACE_SEC` if it hasn't.

One per user (a named mutex): a second start only makes sure the tray is
up. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from satellite import win32

EXIT_RESTART = 75
EXIT_UNPINNED = 78
#: Pauses before restarting an engine that crashed, in order; the last
#: repeats. Reset once an engine has run for :data:`HEALTHY_AFTER_SEC`.
CRASH_BACKOFF_SEC = (5.0, 10.0, 30.0, 60.0)
HEALTHY_AFTER_SEC = 300.0
STOP_GRACE_SEC = 15.0
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3
LOG_NAME = "satellite.log"
SUPERVISOR_LOG = "supervisor.log"
STATUS_NAME = "supervisor-status.json"
#: Test-only variables (satellite/debug_audio.py) passed to the engine only
#: when the supervisor is started with --test-audio-hooks, which nothing in
#: an install does: a variable left in the account's environment can never
#: swap a satellite's microphone for files on its own.
PASS_THROUGH = ("DOMOVOI_DEBUG_MIC_DIR", "DOMOVOI_DEBUG_AUDIO_SINK")
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def default_root() -> Path:
    """The install folder: the one holding ``python\\`` (this interpreter)
    and ``app\\satellite``."""
    return Path(sys.executable).resolve().parent.parent


def interpreter(root: Path, *, windowed: bool) -> str:
    return str(root / "python" / ("pythonw.exe" if windowed else "python.exe"))


def engine_env(root: Path, base: dict[str, str] | None = None, *, test_hooks: bool = False) -> dict[str, str]:
    """The engine's environment: the install folder as its home (Python's
    ``Path.home()`` on Windows is USERPROFILE), the log folder for the
    tray's "Show logs folder", unbuffered UTF-8 output."""
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key.startswith("DOMOVOI_DEBUG_") and not (test_hooks and key in PASS_THROUGH):
            env.pop(key)
    env.update({
        "USERPROFILE": str(root),
        "DOMOVOI_SERVICE_SCOPE": "windows",
        "DOMOVOI_LOG_DIR": str(root / "logs"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    env.pop("HOMEDRIVE", None)
    env.pop("HOMEPATH", None)
    return env


class RotatingLog:
    """Lines into ``path``, kept under :data:`LOG_MAX_BYTES` by rotating to
    ``.1`` … ``.<backups>``. Thread-safe."""

    def __init__(self, path: Path, *, max_bytes: int = LOG_MAX_BYTES, backups: int = LOG_BACKUPS) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backups = backups
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "ab")

    def _rotate(self) -> None:
        self._fh.close()
        for i in range(self.backups, 0, -1):
            src = self.path.with_name(f"{self.path.name}.{i - 1}") if i > 1 else self.path
            dst = self.path.with_name(f"{self.path.name}.{i}")
            try:
                if src.exists():
                    os.replace(src, dst)
            except OSError:
                pass
        self._fh = open(self.path, "ab")

    def write(self, data: bytes) -> None:
        with self._lock:
            if self._fh.tell() + len(data) > self.max_bytes:
                self._rotate()
            self._fh.write(data)
            self._fh.flush()

    def line(self, text: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.write(f"{stamp} supervisor: {text}\r\n".encode("utf-8"))

    def close(self) -> None:
        with self._lock:
            self._fh.close()


def crash_delay(crashes: int) -> float:
    """The pause before restart number ``crashes`` (1-based) in a row."""
    return CRASH_BACKOFF_SEC[min(max(crashes, 1), len(CRASH_BACKOFF_SEC)) - 1]


def decide(exit_code: int, *, stop_asked: bool) -> str:
    """What an engine's exit means: "restart" (at once), "crashed" (after a
    pause) or "stop"."""
    if stop_asked or exit_code == 0:
        return "stop"
    if exit_code == EXIT_RESTART:
        return "restart"
    if exit_code == EXIT_UNPINNED:
        return "stop"
    return "crashed"


class Supervisor:
    """Runs the engine until asked to stop. ``spawn``/``sleep``/``clock``
    are injectable so the restart rules are tested without processes."""

    def __init__(self, root: Path, *, stop: Any, log: RotatingLog, engine_log: RotatingLog,
                 spawn: Callable[..., Any] | None = None,
                 clock: Callable[[], float] = time.monotonic, test_hooks: bool = False) -> None:
        self.root = Path(root)
        self.stop = stop                  # .is_set() / .wait(timeout)
        self.log = log
        self.engine_log = engine_log
        self.spawn = spawn or self._spawn
        self.clock = clock
        self.proc: Any = None
        self.starts = 0
        self.test_hooks = test_hooks

    def _spawn(self) -> Any:
        return subprocess.Popen(
            [interpreter(self.root, windowed=False), "-m", "satellite.client"],
            cwd=str(self.root / "app"), env=engine_env(self.root, test_hooks=self.test_hooks),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )

    def _pump(self, proc: Any) -> threading.Thread:
        def copy() -> None:
            out = getattr(proc, "stdout", None)
            if out is None:
                return
            for line in iter(out.readline, b""):
                self.engine_log.write(line if line.endswith(b"\n") else line + b"\r\n")
        t = threading.Thread(target=copy, name="engine-log", daemon=True)
        t.start()
        return t

    def write_status(self, state: str, **extra: Any) -> None:
        doc = {"state": state, "at": time.time(), **extra}
        path = self.root / "logs" / STATUS_NAME
        try:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(doc), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

    def run_once(self) -> tuple[int, float]:
        """Start the engine and wait for it to end (or for a stop, which it
        is given :data:`STOP_GRACE_SEC` to honour). ``(exit code, seconds
        it ran)``."""
        self.starts += 1
        began = self.clock()
        proc = self.spawn()
        self.proc = proc
        self.log.line(f"engine started (pid {getattr(proc, 'pid', '?')}, start {self.starts})")
        self.write_status("running", pid=getattr(proc, "pid", None))
        pump = self._pump(proc)
        while proc.poll() is None:
            if self.stop.wait(0.5):
                self.log.line("stop asked for; waiting for the engine to finish")
                deadline = self.clock() + STOP_GRACE_SEC
                while proc.poll() is None and self.clock() < deadline:
                    time.sleep(0.2)
                if proc.poll() is None:
                    self.log.line("the engine did not stop in time; ending it")
                    proc.terminate()
                break
        code = proc.wait()
        pump.join(2.0)
        return int(code if code is not None else 1), self.clock() - began

    def run(self) -> int:
        crashes = 0
        while not self.stop.is_set():
            code, ran = self.run_once()
            what = decide(code, stop_asked=self.stop.is_set())
            self.log.line(f"engine exited {code} after {ran:.0f} s: {what}")
            if what == "stop":
                reason = ("no pinned server identity: enrol it again from the dashboard"
                          if code == EXIT_UNPINNED else "stopped")
                self.write_status("stopped", exit_code=code, reason=reason)
                return 0 if code in (0, EXIT_UNPINNED) else code
            if what == "restart":
                crashes = 0
                continue
            crashes = 0 if ran >= HEALTHY_AFTER_SEC else crashes
            crashes += 1
            delay = crash_delay(crashes)
            self.write_status("restarting", exit_code=code, reason=f"crashed (exit {code}); again in {delay:.0f} s")
            if self.stop.wait(delay):
                break
        self.write_status("stopped", reason="stopped")
        return 0


def start_tray(root: Path) -> None:
    """The tray, detached (it keeps itself to one copy)."""
    try:
        subprocess.Popen(
            [interpreter(root, windowed=True), "-m", "satellite.win_tray"],
            cwd=str(root / "app"), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True, creationflags=DETACHED_PROCESS,
        )
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pythonw -m satellite.win_service")
    parser.add_argument("--root", type=Path, default=None, help="the install folder (default: this Python's)")
    parser.add_argument("--no-tray", action="store_true", help="don't start the tray")
    parser.add_argument("--test-audio-hooks", action="store_true",
                        help="TESTS ONLY: pass DOMOVOI_DEBUG_MIC_DIR / _AUDIO_SINK on to the engine")
    args = parser.parse_args(argv)
    root = (args.root or default_root()).resolve()
    if not args.no_tray:
        start_tray(root)
    single = win32.SingleInstance(win32.object_name(win32.SUPERVISOR_MUTEX))
    if single.already_running:
        return 0
    stop = win32.NamedEvent(win32.object_name(win32.STOP_EVENT))
    stop.reset()
    log = RotatingLog(root / "logs" / SUPERVISOR_LOG, max_bytes=1024 * 1024, backups=1)
    engine_log = RotatingLog(root / "logs" / LOG_NAME)
    log.line(f"started for {root}")
    try:
        if args.test_audio_hooks:
            log.line("TEST AUDIO HOOKS passed to the engine (--test-audio-hooks)")
        return Supervisor(root, stop=stop, log=log, engine_log=engine_log,
                          test_hooks=args.test_audio_hooks).run()
    finally:
        log.line("supervisor exits")
        log.close()
        engine_log.close()
        single.close()


if __name__ == "__main__":
    raise SystemExit(main())
