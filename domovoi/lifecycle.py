"""Unified shutdown signaling.

A single `shutdown_event`, bridged to asyncio. Long-running
tasks (workers, streaming TTS, WebSocket loops in later phases) use
`wait_or_shutdown()` to poll at a bounded interval while remaining responsive
to SIGINT/SIGTERM.

On Linux/Mac the signal handlers wire directly into the running loop. On
Windows `loop.add_signal_handler` is unsupported, but uvicorn catches SIGINT
via its own mechanism and triggers FastAPI's lifespan teardown — which calls
`signal_shutdown()` explicitly. Either way, `shutdown_event` flips reliably.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import threading

log = logging.getLogger(__name__)

# threading.Event (not asyncio.Event) because it's not bound to any event
# loop. pytest-asyncio creates a fresh loop per test; a module-level
# asyncio.Event binds to the first loop and then blows up on every
# subsequent test.
shutdown_event: threading.Event = threading.Event()

_POLL_INTERVAL_SEC = 0.1


def signal_shutdown() -> None:
    """Idempotently flip the shutdown event. Safe from any thread/loop."""
    if not shutdown_event.is_set():
        log.info("shutdown signaled")
        shutdown_event.set()


def install_signal_handlers() -> None:
    """Attach SIGINT/SIGTERM handlers to the running loop where supported.

    No-op on Windows (uvicorn handles it + lifespan teardown calls
    signal_shutdown directly). Call from within the FastAPI lifespan setup.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_shutdown)
        except (NotImplementedError, RuntimeError):
            # Windows ProactorEventLoop doesn't support add_signal_handler.
            pass


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
