"""Unified shutdown signaling, and the bounds that keep a stop short.

A single `shutdown_event`, bridged to asyncio. Long-running
tasks (workers, streaming TTS, WebSocket loops in later phases) use
`wait_or_shutdown()` to poll at a bounded interval while remaining responsive
to SIGINT/SIGTERM.

**Who owns SIGINT/SIGTERM: uvicorn.** Its ``Server`` installs
``handle_exit`` for them before it starts the app, and that handler is the
only thing that ever begins a real shutdown: stop accepting, close every
connection (satellite WebSockets get a 1012 close), wait for in-flight
requests up to ``timeout_graceful_shutdown``, run the lifespan teardown
(plugins, workers, timer delivery), then re-raise the signal so the process
exits with it. :func:`install_signal_handlers` therefore never *replaces*
that handler; it wraps it: the wrapper flips ``shutdown_event`` the moment
the signal lands, then hands the signal on unchanged.

That is a fix. Until 2026-09-30 this module registered
``loop.add_signal_handler(SIGTERM, signal_shutdown)`` from the lifespan,
which silently swapped uvicorn's handler out: a SIGTERM only flipped the
event, uvicorn never began shutting down, the lifespan teardown never ran,
and systemd SIGKILLed the core after TimeoutStopSec (90 s on every update
and restart on the Beelink, plugins still polling their streams until the
kill).

Two bounds, so a stop cannot hang again:

* :func:`run_teardown` runs the lifespan's teardown steps in order, each
  under its own timeout inside one overall budget. A step that overruns is
  cancelled and left behind (logged), never waited on.
* :func:`exit_watchdog`, armed around ``uvicorn.run`` in ``main()``: once a
  shutdown has begun, a daemon thread gives the whole process
  ``deadline_sec`` to be gone, then logs every thread's stack and exits.
  That covers what no timeout inside the event loop can: the loop itself
  stuck in synchronous code, or interpreter exit waiting on a worker thread.

On Windows (the dev box) the same wrapping works with ``signal.signal``:
Ctrl+C reaches uvicorn's handler through the wrapper and the dev server
stops the same way.
"""

from __future__ import annotations

import asyncio
import contextlib
import faulthandler
import inspect
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from typing import Any

log = logging.getLogger(__name__)

# threading.Event (not asyncio.Event) because it's not bound to any event
# loop. pytest-asyncio creates a fresh loop per test; a module-level
# asyncio.Event binds to the first loop and then blows up on every
# subsequent test.
shutdown_event: threading.Event = threading.Event()

_POLL_INTERVAL_SEC = 0.1

# The signals a server hands to its shutdown: uvicorn's HANDLED_SIGNALS.
HANDLED_SIGNALS: tuple[int, ...] = (signal.SIGINT, signal.SIGTERM) + (
    (signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ()  # Windows
)

# When the shutdown began (time.monotonic()) and what began it — the first
# signal the wrapper saw, or the lifespan teardown itself. Plain module
# attributes, written without a lock: a Python signal handler runs on the
# main thread between two bytecodes, possibly while that thread holds any
# lock at all, so the handler takes none.
_began_at: float | None = None
_began_by: str | None = None

# A teardown step that overran its time is cancelled, then given this long
# to unwind before the next step starts without it.
CANCEL_GRACE_SEC = 0.5
# Exit status when the watchdog has to end the process: not 0, so systemd
# records the stop as having gone wrong ("status=1/FAILURE"), and the ERROR
# line just before it says why.
WATCHDOG_EXIT_CODE = 1


def _note_shutdown(reason: str) -> None:
    global _began_at, _began_by
    if _began_at is None:
        _began_at = time.monotonic()
        _began_by = reason


def shutdown_began_at() -> float | None:
    """``time.monotonic()`` when this shutdown began, or None."""
    return _began_at


def shutting_down() -> bool:
    """Whether a shutdown has begun: a signal arrived (even before the
    event loop got round to flipping ``shutdown_event``) or the lifespan
    teardown started."""
    return _began_at is not None or shutdown_event.is_set()


def signal_shutdown(reason: str = "requested") -> None:
    """Idempotently flip the shutdown event. Safe from any thread/loop."""
    _note_shutdown(reason)
    if not shutdown_event.is_set():
        log.info("shutdown signaled (%s)", reason)
        shutdown_event.set()


def reset() -> None:
    """A fresh start: no shutdown in progress. The lifespan calls it before
    anything else (tests enter it again and again in one process; the
    event used to stay set from the first teardown on)."""
    global _began_at, _began_by
    _began_at = None
    _began_by = None
    shutdown_event.clear()


def _signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return f"signal {signum}"


def _flip_soon(loop: asyncio.AbstractEventLoop | None, reason: str) -> None:
    """Flip ``shutdown_event`` from a signal handler: through the event
    loop, so the Event's lock and the log handler's stream are never
    re-entered from a handler that interrupted them. With no loop to hand
    it to (it closed), directly."""
    if loop is not None:
        try:
            loop.call_soon_threadsafe(signal_shutdown, reason)
            return
        except RuntimeError:  # the loop is closed
            pass
    signal_shutdown(reason)


def _chain(signum: int, previous: Callable[..., Any],
           loop: asyncio.AbstractEventLoop | None) -> Callable[[int, Any], Any]:
    name = _signal_name(signum)

    def _handler(sig: int, frame: Any) -> Any:
        _note_shutdown(name)
        _flip_soon(loop, name)
        # The server's own handler: uvicorn's Server.handle_exit sets
        # should_exit (a second Ctrl+C sets force_exit), which is what
        # actually stops it. Never skipped, never delayed.
        return previous(sig, frame)

    _handler._domovoi_chained = True  # type: ignore[attr-defined]
    _handler._domovoi_previous = previous  # type: ignore[attr-defined]
    return _handler


def install_signal_handlers() -> None:
    """Make SIGINT/SIGTERM (and SIGBREAK on Windows) flip ``shutdown_event``
    at once, then reach the handler that was installed before — uvicorn's.

    Call from within the FastAPI lifespan setup: uvicorn installs its
    handlers before it starts the app, so they are there to wrap. Only
    wraps a Python-level handler; a signal nobody handles (SIG_DFL,
    SIG_IGN) is left exactly as it is, because installing a handler there
    would swallow a SIGTERM that is meant to end the process. Does nothing
    off the main thread (``signal.signal`` only works there; TestClient
    runs the lifespan on a portal thread). Idempotent.
    """
    if threading.current_thread() is not threading.main_thread():
        return
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    for sig in HANDLED_SIGNALS:
        try:
            previous = signal.getsignal(sig)
        except (ValueError, OSError):
            continue
        if getattr(previous, "_domovoi_chained", False):
            continue  # a re-entered lifespan: already wrapped
        if not callable(previous):
            continue
        try:
            signal.signal(sig, _chain(sig, previous, loop))
        except (ValueError, OSError, RuntimeError):
            pass


# ─── the exit watchdog ────────────────────────────────────────────────────


def _dump_and_exit(deadline_sec: float, exit_fn: Callable[[int], Any]) -> None:
    """Say why, dump every thread's stack, exit. Straight to the file
    descriptor, not through ``logging``: the thread that is stuck may be
    holding the log handler's lock, and this must never wait on it."""
    try:
        try:
            fd = sys.stderr.fileno()
        except Exception:  # noqa: BLE001 — a stderr with no descriptor
            fd = 2
        line = (
            f"{time.strftime('%H:%M:%S')} ERROR {__name__}: shutdown did not "
            f"finish within {deadline_sec:.0f} s of {_began_by or 'the shutdown'}; "
            "exiting now. Every thread's stack follows.\n"
        )
        try:
            os.write(fd, line.encode("utf-8", "replace"))
            faulthandler.dump_traceback(file=fd, all_threads=True)
        except (OSError, ValueError, RuntimeError):
            pass
    finally:
        exit_fn(WATCHDOG_EXIT_CODE)


@contextlib.contextmanager
def exit_watchdog(
    deadline_sec: float, *, exit_fn: Callable[[int], Any] = os._exit,
    poll_sec: float = 0.25,
) -> Iterator[None]:
    """Guarantee the process is gone ``deadline_sec`` after a shutdown
    begins. ``main()`` wraps ``uvicorn.run`` in it.

    A daemon thread waits (it takes no lock a signal handler could hold)
    for a shutdown to begin, then for the deadline; if the process is still
    here it logs, dumps every thread's stack to stderr (the journal) and
    calls ``exit_fn`` (``os._exit``). Set the deadline below systemd's
    TimeoutStopSec so the core's own account of what hung lands in the
    journal, instead of a bare SIGKILL.

    Leaving the block disarms it — unless a shutdown began inside it: then
    it keeps watching through interpreter exit (thread joins, the default
    executor), which is exactly where a Ctrl+C on the dev box can hang.
    ``deadline_sec <= 0`` turns it off.
    """
    if deadline_sec <= 0:
        yield
        return
    reset()
    disarmed = threading.Event()

    def _watch() -> None:
        while not disarmed.is_set():
            began = _began_at
            if began is None:
                disarmed.wait(poll_sec)
                continue
            remaining = began + deadline_sec - time.monotonic()
            if remaining <= 0:
                _dump_and_exit(deadline_sec, exit_fn)
                return
            disarmed.wait(min(remaining, poll_sec))

    thread = threading.Thread(target=_watch, name="shutdown-watchdog", daemon=True)
    thread.start()
    try:
        yield
    finally:
        if _began_at is None:
            disarmed.set()


# ─── the bounded teardown ─────────────────────────────────────────────────

# (name, step, timeout_sec): ``step`` is a coroutine function or a plain
# callable; a plain one runs inline and must not block.
TeardownStep = tuple[str, Callable[[], Any], float]


async def run_teardown(steps: Sequence[TeardownStep], *, budget_sec: float,
                       cancel_grace_sec: float = CANCEL_GRACE_SEC) -> list[str]:
    """Run each teardown step in order, each for at most its own timeout
    and never past what is left of ``budget_sec`` (every step still gets a
    short try: a later step is often the one that matters). A step that
    raises is logged and the next one runs. A step that overruns is
    cancelled, given ``cancel_grace_sec`` to unwind and then left behind —
    not awaited, so one that swallows the cancel cannot hold the stop.
    Returns the names of the steps that did not finish."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, budget_sec)
    unfinished: list[str] = []
    for name, step, timeout in steps:
        started = loop.time()
        allowed = max(min(timeout, deadline - started), cancel_grace_sec)
        try:
            result = step()
        except Exception as e:  # noqa: BLE001 — teardown isolation
            log.warning("shutdown: %s raised: %s", name, e)
            continue
        if not inspect.isawaitable(result):
            continue
        task = asyncio.ensure_future(result)
        done, _ = await asyncio.wait({task}, timeout=allowed)
        if not done:
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=cancel_grace_sec)
            unfinished.append(name)
            log.warning(
                "shutdown: %s did not finish within %.1f s; %s",
                name, allowed,
                "cancelled it" if done else "left it running",
            )
        if done and not task.cancelled() and task.exception() is not None:
            log.warning("shutdown: %s raised: %s", name, task.exception())
        took = loop.time() - started
        if took >= 1.0:
            log.info("shutdown: %s took %.1f s", name, took)
    return unfinished


async def wait_or_shutdown(seconds: float) -> bool:
    """Sleep up to `seconds` or until shutdown fires. Returns True if shutdown.

    Polls the event at 100 ms intervals so Ctrl+C lands promptly. Safe across
    event loops (the event is a threading.Event, not asyncio.Event).
    """
    if shutdown_event.is_set():
        return True
    loop = asyncio.get_event_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        if shutdown_event.is_set():
            return True
        remaining = deadline - loop.time()
        await asyncio.sleep(min(_POLL_INTERVAL_SEC, max(0.0, remaining)))
    return shutdown_event.is_set()
