"""The loop watchdog: notice a frozen core, say why, and get it restarted.

On 2026-10-14 the core stopped answering: its port still accepted
connections, nothing answered, every satellite dropped, and the
dashboard's Restart could not help because it asks the core to restart
itself. It stayed that way until a power cycle a day and a half later,
which also erased the box's RAM-only journal, and with it the record. Two ways to get there were found
(in the maintainers' functional test harness, kept outside this repo): synchronous work on the event loop
that never returns, and a full thread pool that every database connect
waits on forever. Both are fixed where they were found; this module is
for the next one nobody has found yet.

A daemon thread, outside the event loop, checks two things:

* **the heartbeat**: a callback the loop runs every
  :data:`HEARTBEAT_SEC`. When it is ``loop_watchdog_stall_sec`` old, the
  loop is stuck.
* **a database probe**: every ``loop_watchdog_probe_interval_sec``, a real
  ``SELECT 1`` through the normal session path, submitted to the loop.
  ``loop_watchdog_probe_failures`` probes in a row with no answer within
  ``loop_watchdog_probe_timeout_sec`` is a stall too: the loop runs, but
  nothing that needs the database can finish (the thread-pool case). A
  probe that FAILS fast is not counted: the database answered "no" (it is
  down or restarting), and restarting the core can't fix that; it is
  logged once per outage.

On a stall it writes the reason and every thread's stack (thread names
included, then the event loop's tasks) straight to the journal (stderr,
by file descriptor: no logging lock can hold it up) and to the retained
log ``~/.domovoi/logs/core-watchdog.log`` (1 MB, one older copy, flushed
to disk before the exit: on 2026-10-14 a power cycle took the RAM-only
journal of the box with it), notes the stall in
``core-watchdog-last.json`` beside it for the dashboard's Version card,
and exits with :data:`EXIT_CODE` (70). The unit's ``Restart=on-failure``
(or ``always``) starts the core again (docs/LINUX_HOST.md, "The loop
watchdog").

Two backstops for a freeze that stops this thread too (C code holding the
interpreter lock, say):

* every heartbeat re-arms ``faulthandler.dump_traceback_later``, which
  runs in C without the interpreter lock: if the loop hasn't beaten for
  the stall time plus :data:`BACKSTOP_EXTRA_SEC`, it dumps every thread's
  stack and exits (status 1);
* under the shipped unit (``Type=notify``, ``WatchdogSec=``) this thread
  sends systemd ``WATCHDOG=1`` only while all is well; if the keep-alives
  stop, systemd kills the core with SIGABRT (faulthandler, enabled in
  ``main()``, prints the stacks) and starts it again.

Everything faulthandler writes (those two, a crash, a SIGUSR1 dump) goes
to ``~/.domovoi/logs/core-faulthandler.log``, a file on disk, since C code
can write one file only. The next start moves it into the retained log,
says so in the journal with the dump itself, and notes a stall, an abort
or a crash for the Version card (:func:`adopt_faulthandler_dump`).

It runs only in the real server (``python -m domovoi.main`` installs it,
:func:`install`), and watches from the end of the lifespan startup
(:func:`attach_running_loop`, which also tells systemd ``READY=1``) until
a stop begins (lifecycle's exit watchdog bounds a stop). Before that, a
startup still unfinished after ``loop_watchdog_startup_sec`` counts as a
stall. Tests that start the app never install it.
"""

from __future__ import annotations

import asyncio
import faulthandler
import inspect
import json
import os
import signal
import sys
import threading
import time
import traceback
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from domovoi import lifecycle
from domovoi.sd_notify import Notifier

# Exit status of a watchdog exit: not 0, so Restart=on-failure starts the
# core again, and not 1 (an ordinary failure, faulthandler's backstop), so
# the journal's "status=70" says the watchdog did it.
EXIT_CODE = 70
# How often the loop beats, and how often the thread looks.
HEARTBEAT_SEC = 2.0
CHECK_SEC = 1.0
# faulthandler's C-level backstop fires this long after the Python-level
# stall time, so the richer report normally comes first.
BACKSTOP_EXTRA_SEC = 30.0
# The retained log: kept at most this big, with one older copy beside it.
REPORT_MAX_BYTES = 1024 * 1024
# Tasks listed in a report, at most.
REPORT_MAX_TASKS = 80
# Keep-alive cadence under systemd's WatchdogSec: a quarter of it, and at
# least this often.
KEEPALIVE_MAX_SEC = 5.0


def logs_dir() -> Path:
    return Path.home() / ".domovoi" / "logs"


def report_path() -> Path:
    return logs_dir() / "core-watchdog.log"


def last_stall_path() -> Path:
    return logs_dir() / "core-watchdog-last.json"


def faulthandler_path() -> Path:
    return logs_dir() / "core-faulthandler.log"


def ready_path() -> Path:
    """Where a started core says so: ``{"pid", "at"}``, written when its
    startup is done (the dashboard reads it to tell a core still starting
    from one that started and then froze, under a unit that isn't
    ``Type=notify``)."""
    return Path.home() / ".domovoi" / "run" / "core-ready.json"


def read_ready(path: Path | None = None) -> dict[str, Any] | None:
    try:
        data = json.loads((path or ready_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("pid"), int) else None


def _note_ready(path: Path | None = None) -> None:
    path = path or ready_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"pid": os.getpid(), "at": time.time()}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _clear_ready(path: Path | None = None) -> None:
    """A core starting: whatever note is there is an earlier process's
    (an earlier boot's, whose PID this one may have), not this one's."""
    try:
        (path or ready_path()).unlink()
    except OSError:  # none there, or not ours to remove: nothing to clear
        pass


def _fsync_dir(path: Path) -> None:
    if sys.platform == "win32":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _append_synced(path: Path, text: str) -> None:
    """Append ``text`` to ``path`` (rotated past :data:`REPORT_MAX_BYTES`
    to one ``.1`` copy) and flush it to disk: the exit, or a power cycle,
    may follow at once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > REPORT_MAX_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
    except FileNotFoundError:
        pass
    with open(path, "ab") as f:
        f.write(text.encode("utf-8", "replace"))
        f.flush()
        os.fsync(f.fileno())
    _fsync_dir(path.parent)


def note_last_stall(reason: str, *, pid: int | None, at: str | None = None,
                    path: Path | None = None, log: Path | None = None) -> None:
    """Write ``core-watchdog-last.json`` (what the Version card shows as
    the last time the core was ended for freezing), atomically and to disk.
    The dashboard writes it too, when it ends a core that doesn't answer
    (web/backend/core_restart.py)."""
    path = path or last_stall_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "at": at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "reason": reason,
                "pid": pid,
                "log": str(log or report_path()),
            }))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except OSError:
        pass


# What a faulthandler dump says about how the run before ended.
def _dump_kind(text: str) -> tuple[str, bool]:
    """(what happened, whether it ended the core): the backstop's timeout,
    an abort (systemd's watchdog sends SIGABRT), another fatal error, or a
    stack dump someone asked for (SIGUSR1), which ends nothing."""
    # An abort first: systemd's SIGABRT reaches a process that was stopped
    # outright, and its resumption can trip the backstop's timer too, so
    # both dumps are then in the file.
    if "Fatal Python error: Aborted" in text:
        return ("the core was aborted (SIGABRT: systemd's watchdog sends it when the keep-alives "
                "stop)", True)
    if "Timeout (" in text:
        return ("the event loop stayed frozen past the backstop, so faulthandler ended the core "
                "(its watchdog thread could not run)", True)
    for line in text.splitlines():
        if line.startswith("Fatal Python error:"):
            return (f"the core crashed ({line.strip()[:160]})", True)
    return ("a stack dump on request (SIGUSR1)", False)


# The most of an adopted dump echoed to the journal (the retained log
# keeps all of it).
_ADOPT_ECHO_MAX = 64 * 1024


def adopt_faulthandler_dump(src: Path | None = None, *, out_fd: int | None = None,
                            report: Path | None = None, last: Path | None = None) -> str | None:
    """At start: move what faulthandler wrote during the run before into
    the retained log (with a header naming what it was), echo it to the
    journal, note a stall, abort or crash as the last stall unless a newer
    note is there (the watchdog's own, or the dashboard's), and empty the
    file. Returns what it was, or None when there was nothing."""
    src = src or faulthandler_path()
    try:
        data = src.read_bytes()
        mtime = src.stat().st_mtime
    except OSError:
        return None
    if not data.strip():
        return None
    text = data.decode("utf-8", "replace")
    kind, ended = _dump_kind(text)
    at = datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec="seconds")
    report = report or report_path()
    try:
        _append_synced(report, f"===== {at} (faulthandler: {kind}) =====\n{text}"
                       + ("" if text.endswith("\n") else "\n"))
    except OSError:
        return kind
    fd = out_fd if out_fd is not None else _stderr_fd()
    echo = (f"{time.strftime('%H:%M:%S')} WARNING {__name__}: the run before left a faulthandler "
            f"dump from {at}: {kind}; it is kept in {report}\n{text[-_ADOPT_ECHO_MAX:]}\n")
    try:
        os.write(fd, echo.encode("utf-8", "replace"))
    except OSError:
        pass
    if ended:
        prior = last_stall(last)
        if prior is None or str(prior.get("at") or "") < at:
            note_last_stall(kind, pid=None, at=at, path=last, log=report)
    try:
        with open(src, "wb"):
            pass
    except OSError:
        pass
    return kind


@dataclass(frozen=True)
class Limits:
    stall_sec: float = 30.0
    probe_interval_sec: float = 15.0
    probe_timeout_sec: float = 10.0
    probe_failures: int = 3
    startup_sec: float = 900.0
    heartbeat_sec: float = HEARTBEAT_SEC
    check_sec: float = CHECK_SEC
    backstop_extra_sec: float = BACKSTOP_EXTRA_SEC
    # False (loop_watchdog_enabled=false): no stall is ever found, nothing
    # exits; READY=1 still goes out and, under a unit with WatchdogSec=,
    # the thread keeps systemd fed (the shipped unit is Type=notify: a core
    # that never said READY, or stopped feeding it, would be killed).
    detect: bool = True

    @classmethod
    def from_settings(cls, s: Any) -> "Limits":
        return cls(
            stall_sec=max(5.0, float(s.loop_watchdog_stall_sec)),
            probe_interval_sec=max(1.0, float(s.loop_watchdog_probe_interval_sec)),
            probe_timeout_sec=max(1.0, float(s.loop_watchdog_probe_timeout_sec)),
            probe_failures=max(1, int(s.loop_watchdog_probe_failures)),
            startup_sec=max(0.0, float(s.loop_watchdog_startup_sec)),
        )


async def db_probe() -> None:
    """``SELECT 1`` the way every request reaches the database."""
    from sqlalchemy import text

    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await s.execute(text("SELECT 1"))


# The file faulthandler writes to (install() opens it and keeps it open:
# faulthandler holds the descriptor, not the path); stderr until then.
_FAULT_FILE: Any = None


def _faulthandler_backstop(seconds: float | None) -> None:
    """Arm (or, with None, cancel) faulthandler's C-level dump-and-exit."""
    try:
        if seconds is None:
            faulthandler.cancel_dump_traceback_later()
        else:
            faulthandler.dump_traceback_later(seconds, repeat=False,
                                              file=_FAULT_FILE or sys.stderr, exit=True)
    except (RuntimeError, ValueError, OSError, AttributeError):
        pass


def _close_if_unstarted(coro: Any) -> None:
    if inspect.getcoroutinestate(coro) == inspect.CORO_CREATED:
        try:
            coro.close()
        except (RuntimeError, ValueError):
            pass


def _stderr_fd() -> int:
    try:
        return sys.stderr.fileno()
    except Exception:  # noqa: BLE001 - a stderr without a descriptor
        return 2


class LoopWatchdog:
    """See the module docstring. :meth:`check` is one look (the thread
    calls it every ``check_sec``); everything that can block the thread
    (the clock, the exit, the backstop, the notifier) is injectable."""

    def __init__(
        self,
        limits: Limits,
        *,
        probe: Callable[[], Awaitable[None]] = db_probe,
        notifier: Notifier | None = None,
        exit_fn: Callable[[int], Any] = os._exit,
        backstop: Callable[[float | None], None] = _faulthandler_backstop,
        clock: Callable[[], float] = time.monotonic,
        report_file: Path | None = None,
        last_file: Path | None = None,
        out_fd: int | None = None,
    ) -> None:
        self.limits = limits
        self._probe = probe
        self._notifier = notifier or Notifier(None)
        self._exit = exit_fn
        self._backstop = backstop
        self._clock = clock
        self._report_file = report_file
        self._last_file = last_file
        self._out_fd = out_fd
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._installed_at = clock()
        self._started_wall = time.time()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None
        self._attached_once = False
        self._beat_at: float | None = None
        self._beat_handle: asyncio.TimerHandle | None = None
        self._probe_fut: Future | None = None
        self._probe_coro: Any = None
        self._probe_started: float | None = None
        self._probe_last: float | None = None
        self._hung = 0
        self._db_error_said = False
        self._keepalive_at: float | None = None
        self._fired = False

    # ── lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="loop-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the thread (tests); the process is not touched."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
        self._drop_probe(close_unstarted=True)

    def _drop_probe(self, *, close_unstarted: bool = False) -> None:
        """Cancel the probe in flight (the loop cancels its task). With
        ``close_unstarted``, a probe the loop never got round to starting
        is also closed, so no "never awaited" warning follows it: only
        where the loop can't start it meanwhile (on the loop's own thread,
        or once it has stopped), since closing a coroutine runs its
        cleanup on the caller's thread."""
        fut, coro = self._probe_fut, self._probe_coro
        self._probe_fut = self._probe_coro = None
        if fut is not None:
            fut.cancel()
        if coro is None:
            return
        if close_unstarted:
            _close_if_unstarted(coro)
            return
        # From this thread: let the loop close it when it gets there (after
        # the callback that now won't start it, so the order is safe).
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(_close_if_unstarted, coro)
            except RuntimeError:  # the loop closed
                pass

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start watching ``loop``. Call ON the loop, when startup is done."""
        self._loop = loop
        self._loop_thread = threading.get_ident()
        self._attached_once = True
        self._hung = 0
        self._probe_fut = None
        self._probe_last = self._clock()
        self._beat()
        self._notifier.notify("READY=1\nSTATUS=serving")
        wd = self._notifier.watchdog_sec
        if not self.limits.detect:
            self._say("WARNING", "loop watchdog: off (LOOP_WATCHDOG_ENABLED=false): a frozen core "
                      "stays frozen" + (f"; systemd WatchdogSec={wd:.0f} is still fed" if wd else ""))
            return
        self._say(
            "INFO",
            f"loop watchdog: watching (a stall is {self.limits.stall_sec:.0f} s without a "
            f"heartbeat or {self.limits.probe_failures} database probes without an answer in "
            f"{self.limits.probe_timeout_sec:.0f} s"
            + (f"; systemd WatchdogSec={wd:.0f}" if wd else "")
            + ")",
        )

    def detach(self) -> None:
        """Stop watching (a stop has begun). Call on the loop."""
        self._loop = None
        handle, self._beat_handle = self._beat_handle, None
        if handle is not None:
            handle.cancel()
        self._drop_probe(close_unstarted=True)
        self._backstop(None)
        self._notifier.notify("STOPPING=1")

    def _beat(self) -> None:
        loop = self._loop
        if loop is None:
            return
        self._beat_at = self._clock()
        if self.limits.detect:
            self._backstop(self.limits.stall_sec + self.limits.backstop_extra_sec)
        self._beat_handle = loop.call_later(self.limits.heartbeat_sec, self._beat)

    # ── the thread ───────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.wait(self.limits.check_sec):
            try:
                reason = self.check()
            except Exception as e:  # noqa: BLE001 - the watchdog must not die quietly
                self._say("WARNING", f"loop watchdog: a check raised {type(e).__name__}: {e}")
                continue
            if reason is not None:
                self.fire(reason)
                return

    def check(self) -> str | None:
        """One look. The reason for a stall, or None (and, while all is
        well, a keep-alive to systemd when one is due)."""
        if self._fired or lifecycle.shutting_down():
            return None
        now = self._clock()
        # Still starting: systemd watches a Type=notify core only from
        # READY=1, but a unit changed to Type=simple is watched from the
        # fork, so it is fed while starting too (a startup that never ends
        # is this thread's to catch, below; a held interpreter starves the
        # feed and systemd's watchdog then catches it).
        starting = self._loop is None and not self._attached_once
        if not self.limits.detect:
            if self._loop is not None or starting:
                self._keepalive(now)
            return None
        loop = self._loop
        if loop is None:
            if not starting:
                return None
            if (self.limits.startup_sec > 0
                    and now - self._installed_at >= self.limits.startup_sec):
                return (f"startup has not finished after {now - self._installed_at:.0f} s "
                        f"(loop_watchdog_startup_sec={self.limits.startup_sec:.0f})")
            self._keepalive(now)
            return None
        age = now - (self._beat_at if self._beat_at is not None else now)
        if age >= self.limits.stall_sec:
            return f"the event loop has not run for {age:.0f} s"
        reason = self._check_probe(loop, now)
        if reason is not None:
            return reason
        self._keepalive(now)
        return None

    def _check_probe(self, loop: asyncio.AbstractEventLoop, now: float) -> str | None:
        fut = self._probe_fut
        if fut is not None:
            if fut.done():
                self._probe_fut = self._probe_coro = None
                exc = None if fut.cancelled() else fut.exception()
                if exc is None:
                    self._hung = 0
                    if self._db_error_said:
                        self._say("INFO", "loop watchdog: the database answers again")
                    self._db_error_said = False
                else:
                    # It answered, with an error: the database is down or
                    # refusing, not a frozen core. Said once per outage.
                    self._hung = 0
                    if not self._db_error_said:
                        self._db_error_said = True
                        self._say(
                            "WARNING",
                            f"loop watchdog: the database probe failed ({type(exc).__name__}: "
                            f"{str(exc)[:200]}); the database looks down, which restarting the "
                            "core won't fix, so this doesn't count as a stall",
                        )
            elif now - (self._probe_started or now) >= self.limits.probe_timeout_sec:
                self._drop_probe()
                self._hung += 1
                self._say(
                    "WARNING",
                    f"loop watchdog: database probe got no answer within "
                    f"{self.limits.probe_timeout_sec:.0f} s ({self._hung} of "
                    f"{self.limits.probe_failures} in a row)",
                )
                if self._hung >= self.limits.probe_failures:
                    return (f"{self._hung} database probes in a row got no answer within "
                            f"{self.limits.probe_timeout_sec:.0f} s")
        if self._probe_fut is None and (
            self._probe_last is None or now - self._probe_last >= self.limits.probe_interval_sec
        ):
            self._probe_last = now
            self._probe_started = now
            self._probe_coro = self._probe()
            try:
                self._probe_fut = asyncio.run_coroutine_threadsafe(self._probe_coro, loop)
            except RuntimeError:  # the loop closed under us
                self._drop_probe()
        return None

    def _keepalive(self, now: float) -> None:
        wd = self._notifier.watchdog_sec
        if not wd:
            return
        every = min(KEEPALIVE_MAX_SEC, wd / 4)
        if self._keepalive_at is None or now - self._keepalive_at >= every:
            self._keepalive_at = now
            self._notifier.notify("WATCHDOG=1")

    # ── the stall ────────────────────────────────────────────────────

    def fire(self, reason: str) -> None:
        """Report the stall everywhere it can be read later, then exit."""
        if self._fired:
            return
        self._fired = True
        try:
            report = self.report(reason)
            self._write_out(report)
            self._retain(report)
            self._note_last(reason)
            self._notifier.notify(f"STATUS=frozen ({reason}); exiting to be restarted")
        finally:
            self._exit(EXIT_CODE)

    def report(self, reason: str) -> str:
        now = self._clock()
        beat = (f"{now - self._beat_at:.1f} s ago" if self._beat_at is not None
                else "never (startup)")
        lines = [
            f"{time.strftime('%H:%M:%S')} ERROR {__name__}: {reason}; exiting with status "
            f"{EXIT_CODE} so the service manager starts the core again. Every thread's "
            "stack follows.",
            f"  pid {os.getpid()}, up {time.time() - self._started_wall:.0f} s, last heartbeat "
            f"{beat}, database probes without an answer in a row: {self._hung}",
            f"  thread pools: {self._pools()}",
        ]
        names = {t.ident: t.name for t in threading.enumerate()}
        for ident, frame in sys._current_frames().items():  # noqa: SLF001
            tag = " (the event loop)" if ident == self._loop_thread else ""
            lines.append(f"--- thread {names.get(ident, '?')} [{ident}]{tag}")
            lines.extend(x.rstrip("\n") for x in traceback.format_stack(frame))
        lines.extend(self._tasks())
        lines.append(f"--- end of the loop watchdog report ({reason})")
        return "\n".join(lines) + "\n"

    def _pools(self) -> str:
        parts: list[str] = []
        loop = self._loop
        ex = getattr(loop, "_default_executor", None) if loop is not None else None
        if ex is not None:
            try:
                parts.append(f"default {len(ex._threads)} threads, {ex._work_queue.qsize()} waiting")  # noqa: SLF001
            except Exception:  # noqa: BLE001
                pass
        try:
            from domovoi import executors

            for name, b in executors.busy().items():
                parts.append(f"{name} {b['threads']}/{b['max']} threads, {b['queued']} waiting")
        except Exception:  # noqa: BLE001
            pass
        return "; ".join(parts) or "none started"

    def _tasks(self) -> list[str]:
        loop = self._loop
        if loop is None:
            return []
        try:
            tasks = list(asyncio.all_tasks(loop))
        except Exception as e:  # noqa: BLE001 - read from another thread; best effort
            return [f"--- asyncio tasks: unreadable ({type(e).__name__})"]
        out = [f"--- {len(tasks)} asyncio tasks"]
        for t in tasks[:REPORT_MAX_TASKS]:
            try:
                coro = t.get_coro()
                where = getattr(coro, "__qualname__", type(coro).__name__)
                frame = getattr(coro, "cr_frame", None)
                while frame is not None:
                    inner = getattr(coro, "cr_await", None)
                    if inner is None or getattr(inner, "cr_frame", None) is None:
                        break
                    coro, frame = inner, inner.cr_frame
                if frame is not None:
                    where += f" at {frame.f_code.co_filename}:{frame.f_lineno}"
                out.append(f"  {t.get_name()}: {where}")
            except Exception:  # noqa: BLE001
                out.append(f"  {t!r}"[:300])
        if len(tasks) > REPORT_MAX_TASKS:
            out.append(f"  ... and {len(tasks) - REPORT_MAX_TASKS} more")
        return out

    def _write_out(self, text: str) -> None:
        fd = self._out_fd if self._out_fd is not None else _stderr_fd()
        data = text.encode("utf-8", "replace")
        try:
            while data:
                n = os.write(fd, data)
                data = data[n:]
        except OSError:
            pass

    def _retain(self, text: str) -> None:
        path = self._report_file or report_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if path.stat().st_size > REPORT_MAX_BYTES:
                    os.replace(path, path.with_name(path.name + ".1"))
            except FileNotFoundError:
                pass
            _append_synced(path, f"===== {datetime.now(timezone.utc).isoformat(timespec='seconds')} =====\n"
                           + text)
        except OSError:
            pass

    def _note_last(self, reason: str) -> None:
        note_last_stall(reason, pid=os.getpid(), path=self._last_file,
                        log=self._report_file or report_path())

    def _say(self, level: str, message: str) -> None:
        """One log line straight to stderr: the thread never takes the
        logging module's locks, which the stuck loop may hold."""
        self._write_out(f"{time.strftime('%H:%M:%S')} {level} {__name__}: {message}\n")


# ─── the process-wide watchdog ──────────────────────────────────────────

_ACTIVE: LoopWatchdog | None = None
# install() ran (the real server, not a test), whether or not the checks
# are on: attach_running_loop() then says the core is ready.
_INSTALLED = False


def _open_fault_file() -> Any:
    try:
        path = faulthandler_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        return open(path, "a", encoding="utf-8")  # noqa: SIM115 - held for the process's life
    except OSError:
        return None


def install(settings: Any) -> LoopWatchdog | None:
    """Start the watchdog for this process (``main()``, before uvicorn),
    unless ``loop_watchdog_enabled`` is off. Moves the run before's
    faulthandler dump into the retained log first, then points faulthandler
    at its file and turns it on (POSIX): a crash, a SIGABRT from systemd's
    watchdog and a SIGUSR1 each leave every stack there."""
    global _ACTIVE, _FAULT_FILE, _INSTALLED
    if _ACTIVE is not None:
        return _ACTIVE
    _INSTALLED = True
    # Not ready yet: an old note (an earlier boot's core may have had this
    # PID) must not say this one finished starting (review round 2, I3).
    _clear_ready()
    # The dumps, whether or not the watchdog itself is on: the dashboard
    # sends SIGUSR1 before it ends a core that doesn't answer, which with no
    # handler would end it with nothing written.
    adopt_faulthandler_dump()
    _FAULT_FILE = _open_fault_file()
    target = _FAULT_FILE or sys.stderr
    if sys.platform != "win32":
        try:
            faulthandler.enable(file=target, all_threads=True)
            # SIGUSR1: every stack, and the core carries on. The dashboard
            # sends it before it ends a core that doesn't answer
            # (web/backend/core_restart.py); anyone may by hand.
            faulthandler.register(signal.SIGUSR1, file=target, all_threads=True)
        except (RuntimeError, ValueError, OSError, AttributeError):
            pass
    if not getattr(settings, "loop_watchdog_enabled", True):
        # Off: only what the service manager needs (READY=1, and WATCHDOG=1
        # under a WatchdogSec= unit), never a stall or an exit.
        notifier = Notifier.from_env()
        if notifier.enabled:
            _ACTIVE = LoopWatchdog(Limits(detect=False), notifier=notifier, backstop=lambda s: None)
            _ACTIVE.start()
        return _ACTIVE
    _ACTIVE = LoopWatchdog(Limits.from_settings(settings), notifier=Notifier.from_env())
    _ACTIVE.start()
    return _ACTIVE


def attach_running_loop() -> None:
    """The lifespan's startup is done: watch this loop, tell systemd, and
    leave the ready note (:func:`ready_path`)."""
    if _INSTALLED:
        _note_ready()
    if _ACTIVE is not None:
        _ACTIVE.attach(asyncio.get_running_loop())


def detach() -> None:
    """A stop has begun: stand down (the exit watchdog bounds the stop)."""
    if _ACTIVE is not None:
        _ACTIVE.detach()


def last_stall(path: Path | None = None) -> dict[str, Any] | None:
    """The last time the core was ended for freezing on this box
    (``core-watchdog-last.json``): ``{"at", "reason", "pid", "log"}``, or
    None."""
    try:
        data = json.loads((path or last_stall_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("at"), str):
        return None
    return {k: data.get(k) for k in ("at", "reason", "pid", "log")}
