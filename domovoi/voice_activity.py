"""Is a voice turn being answered right now? (documents plan D2, part 4)

Ollama answers one request at a time per model as provisioned, so a long
document job on the home server (the web process's
``web/backend/api/chat_doc_jobs.py``) could make a question in the
kitchen wait for minutes. The job reads its document part by part and,
between parts, asks the core ``GET /v1/voice/activity``; while a turn is
in progress here it waits. The longest a voice turn can be held up is the
one part already being read.

A turn counts from the moment the core starts on it (speech to text, the
router, the model, the spoken answer) to its end, through :func:`turn`:
the satellite stream's ``_process_utterance``, the app's chat mic
(``phone_voice``) and ``POST /v1/intent``. A turn left open by a bug can't
hold documents up forever: one older than :data:`MAX_TURN_SEC` no longer
counts.

Process-local, no I/O: a counter of open turns.
"""

from __future__ import annotations

import contextlib
import itertools
import time
from typing import AsyncIterator

#: A turn open longer than this is treated as finished (a cold model on an
#: all-CPU box can take a minute; nothing real takes five).
MAX_TURN_SEC = 300.0

_open: dict[int, tuple[str, float]] = {}
_ids = itertools.count(1)
_last_end: float | None = None


@contextlib.asynccontextmanager
async def turn(kind: str = "voice") -> AsyncIterator[None]:
    """Marks a voice turn in progress for as long as the block runs."""
    global _last_end
    key = next(_ids)
    _open[key] = (kind, time.monotonic())
    try:
        yield
    finally:
        _open.pop(key, None)
        _last_end = time.monotonic()


def snapshot(now: float | None = None) -> dict[str, object]:
    """``{"busy", "turns", "oldest_ms", "since_last_ms"}`` — whether a voice
    turn is open now, how many, how long the oldest has run, and how long
    since the last one ended (None before the first)."""
    now = time.monotonic() if now is None else now
    live = [t0 for _, t0 in _open.values() if now - t0 <= MAX_TURN_SEC]
    return {
        "busy": bool(live),
        "turns": len(live),
        "oldest_ms": int((now - min(live)) * 1000) if live else None,
        "since_last_ms": int((now - _last_end) * 1000) if _last_end is not None else None,
    }


def busy(now: float | None = None) -> bool:
    return bool(snapshot(now)["busy"])


def _reset_for_tests() -> None:
    global _last_end
    _open.clear()
    _last_end = None
