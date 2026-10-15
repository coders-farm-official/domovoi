"""Bounded thread pools for the core's heavy blocking work.

``asyncio.to_thread`` runs everything in the event loop's ONE default
pool (``min(32, cpu_count + 4)`` threads). That pool is shared with work
nothing ever plans for, such as resolving a host name before a connect.
When long jobs fill it, everything else queued behind them waits: on
2026-10-14 the core stopped answering, and one way to get there is a
voice download holding the speech engine's lock while every speech
request piled up behind it, one pool thread each, until the database
connects (a ``localhost`` lookup each) could no longer get a thread
(functional-testing incident-1014, ``starve.py``).

So the heavy users get pools of their own, each with a fixed number of
threads: speech recognition and voice analysis, speech synthesis, and
plugin installs. When one of them is saturated, only its own work waits;
the default pool, and the database behind it, are left alone.

:func:`run` is ``asyncio.to_thread`` for a named pool: same semantics
(context variables copied, the result or exception returned to the
awaiting coroutine). Cancelling the await does not stop a thread that is
already running, exactly like ``to_thread``.

The pools are process-wide and not bound to an event loop, so tests that
open a fresh loop each time share them safely. Threads are created on
demand, up to the limit.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# Pool name -> worker threads. Sized for a household: a few rooms talking
# at once, never a crowd. The streaming layer runs one Whisper call (or
# voice embedding) per room at a time, so "speech" is how many rooms,
# phones and chat mics can be heard at the same moment before one waits.
POOL_SIZES: dict[str, int] = {
    # Whisper transcriptions (speculative and final) and the voice
    # embedder that tells household members apart.
    "speech": 8,
    # Text to speech: Piper synthesis, and a Piper voice's first download.
    "tts": 4,
    # Plugin installs, upgrades and packaging: pip, unzip, hashing.
    "plugins": 2,
}

_pools: dict[str, ThreadPoolExecutor] = {}
_pools_lock = threading.Lock()


def pool(name: str) -> ThreadPoolExecutor:
    """The process-wide pool called ``name`` (one of :data:`POOL_SIZES`),
    created on first use."""
    if name not in POOL_SIZES:
        raise KeyError(f"no executor pool named {name!r}")
    ex = _pools.get(name)
    if ex is None:
        with _pools_lock:
            ex = _pools.get(name)
            if ex is None:
                ex = ThreadPoolExecutor(
                    max_workers=POOL_SIZES[name], thread_name_prefix=f"domovoi-{name}"
                )
                _pools[name] = ex
    return ex


async def run(name: str, func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run ``func(*args, **kwargs)`` in the pool called ``name`` and return
    its result: :func:`asyncio.to_thread`, in a bounded pool of its own."""
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    call = functools.partial(ctx.run, func, *args, **kwargs)
    return await loop.run_in_executor(pool(name), call)


def busy() -> dict[str, dict[str, int]]:
    """``{name: {"threads", "max", "queued"}}`` for each pool created so
    far: how many threads it runs, its limit, and how many calls wait for a
    thread. Diagnostics only (the loop watchdog prints it with a stall)."""
    out: dict[str, dict[str, int]] = {}
    for name, ex in list(_pools.items()):
        try:
            queued = ex._work_queue.qsize()  # noqa: SLF001 - read-only peek
            threads = len(ex._threads)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            queued, threads = -1, -1
        out[name] = {"threads": threads, "max": POOL_SIZES.get(name, -1), "queued": queued}
    return out
