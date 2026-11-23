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

**From the wake word, not from the end of speech** (chat video, 1.4.0): a
room that has heard its wake word and is still taking in what the person
says counts as busy too. A satellite's stream registers itself here
(:func:`watch_capture`) and is asked, each time, whether a capture is live
(its ``_capturing``: an ``utterance_start`` came and audio is still
arriving). So a background job that checks before each step stops at the
wake word, and has the whole time the person is speaking to finish the one
step already under way: the turn itself never waits for it.

Process-local, no I/O: a counter of open turns and a weak set of streams.
"""

from __future__ import annotations

import contextlib
import contextvars
import itertools
import time
import weakref
from typing import Any, AsyncIterator

#: A turn open longer than this is treated as finished (a cold model on an
#: all-CPU box can take a minute; nothing real takes five).
MAX_TURN_SEC = 300.0

_open: dict[int, tuple[str, float]] = {}
_ids = itertools.count(1)
_last_end: float | None = None
#: Satellite streams that may be taking in a command (``_capturing(now)``).
_captures: "weakref.WeakSet[Any]" = weakref.WeakSet()
#: True inside a turn's own code (and the tasks it starts): the turn's own
#: model calls never wait for voice (:func:`in_turn`).
_in_turn: contextvars.ContextVar[bool] = contextvars.ContextVar("voice_turn", default=False)


def watch_capture(stream: Any) -> None:
    """Count ``stream``'s live captures as voice activity from now on (its
    ``_capturing(now)`` is asked on every snapshot). Idempotent; a stream
    that goes away drops out by itself."""
    _captures.add(stream)


def _listening(now: float) -> int:
    n = 0
    for s in list(_captures):
        try:
            if s._capturing(now):
                n += 1
        except Exception:  # noqa: BLE001 — a half-closed stream never blocks a job
            continue
    return n


@contextlib.asynccontextmanager
async def turn(kind: str = "voice") -> AsyncIterator[None]:
    """Marks a voice turn in progress for as long as the block runs."""
    global _last_end
    key = next(_ids)
    _open[key] = (kind, time.monotonic())
    token = _in_turn.set(True)
    try:
        yield
    finally:
        _in_turn.reset(token)
        _open.pop(key, None)
        _last_end = time.monotonic()


def in_turn() -> bool:
    """Whether the caller is a voice turn's own code (inside :func:`turn`),
    so a "voice first" wait would only hold the turn up behind itself."""
    return _in_turn.get()


def snapshot(now: float | None = None) -> dict[str, object]:
    """``{"busy", "turns", "listening", "oldest_ms", "since_last_ms"}`` —
    whether a voice turn is open now (or a room is taking in a command after
    its wake word), how many turns, how many rooms listening, how long the
    oldest turn has run, and how long since the last one ended (None before
    the first)."""
    now = time.monotonic() if now is None else now
    live = [t0 for _, t0 in _open.values() if now - t0 <= MAX_TURN_SEC]
    listening = _listening(time.monotonic())
    return {
        "busy": bool(live) or listening > 0,
        "turns": len(live),
        "listening": listening,
        "oldest_ms": int((now - min(live)) * 1000) if live else None,
        "since_last_ms": int((now - _last_end) * 1000) if _last_end is not None else None,
    }


def busy(now: float | None = None) -> bool:
    return bool(snapshot(now)["busy"])


def _reset_for_tests() -> None:
    global _last_end
    _open.clear()
    _captures.clear()
    _last_end = None
