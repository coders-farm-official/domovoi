"""Time limits on a voice turn before its reply, and what the core knows
about speech recognition that has stopped.

Incident 2026-11-09: speech recognition on the Domovoi server wedged and
every room sat in its yellow "thinking" ring for good. The satellite had
sent its request; the core never said what it heard, never answered, never
sent an error; nothing between the end of a request and its reply had a
time limit (the room's decode slot, the decode in its worker thread, the
wait for a speculative copy, voice identification), and a worker thread
that never returns can't be stopped.

So (``domovoi/streaming.py`` applies them):

* **Speech recognition** for one request: :func:`stt_cap` — at least
  ``voice_stt_timeout_min_sec`` (20 s), three times the recording, at most
  ``voice_stt_timeout_max_sec`` (40 s). The second hearing gets what is
  left of the same budget, and on expiry the first hearing is routed.
* **Voice identification**: ``voice_identify_timeout_sec`` (10 s); on
  expiry the turn goes on unidentified.
* **The turn up to the start of routing**: ``voice_reply_timeout_sec``
  (150 s). Once routing starts the limit moves to :func:`routing_cap`
  from then (a cold model load plus two model calls plus a margin, 570 s
  by default), so a slow but working model always answers, or fails with
  its own error and fallback, first. The first reply frame (the reply, or
  the line said before a cold model load) ends the limit, and chat mode
  is outside it (Letta has its own timeout).

On expiry the turn raises :class:`TurnStalled` naming the stage; the
streaming layer says a short apology (or sends ``error`` + ``response_end``
when it can't be said), logs one line naming the room and the stage, marks
the history entry "no answer — timed out", and :func:`note_stall` records
it here for ``/v1/health``.

**Stuck workers.** :mod:`domovoi.executors` knows when each speech thread
started its current call. One busy for ``voice_speech_stuck_after_sec``
(120 s) is stuck — no decode or embedding takes minutes — and
:func:`speech_health` says so (the Health page: "speech recognition is
stuck · restart Domovoi"). :func:`restart_reason` is the loop watchdog's
question (domovoi/loop_watchdog.py): stuck for
``voice_speech_restart_after_sec`` (600 s) — every worker stuck, or one
stuck with no speech call finishing since and a request timed out on it
meanwhile (also what one wedged room in a quiet house looks like) — and
only then does the core restart: the dashboard's graceful restart, else
the exit for systemd to start it again, with every thread's stack in the
retained log first. Not while a restart, an update or a plugin install is
under way, and at most once an hour.
"""

from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from domovoi import executors
from domovoi.config import settings

# The stages a turn can be stuck in, in the words the log line and the
# Health page use ("timed out while <stage>").
STAGE_STT = "recognising speech"
STAGE_STT_SLOT = "waiting for the room's previous speech recognition"
STAGE_SECOND_HEARING = "hearing the request a second time"
STAGE_IDENTIFY = "identifying the voice"
STAGE_REPLY = "working out the reply"

# The spoken apology's own cap: its speech synthesis must not hold a turn
# that is being ended for holding too long. 40 s of speech recognition plus
# this (50 s) stays well under the satellite's 75 s reply deadline.
APOLOGY_TTS_SEC = 10.0

APOLOGY_TEXT = "Sorry, that took too long and I lost track of it. Please try again."

# How long a timed-out request keeps the Health row at "slow" when no
# worker is stuck (it recovered, or it was the reply stage).
RECENT_STALL_SEC = 900.0

SPEECH_POOL = "speech"


class TurnStalled(Exception):
    """A voice turn's stage ran past its time limit."""

    def __init__(self, stage: str, limit_s: float) -> None:
        super().__init__(f"timed out after {limit_s:.0f} s while {stage}")
        self.stage = stage
        self.limit_s = limit_s


@dataclass(frozen=True)
class Stall:
    stage: str
    at: str          # ISO 8601, UTC
    mono: float      # time.monotonic() when it happened
    room: str | None


_lock = threading.Lock()
_stalls: collections.deque[Stall] = collections.deque(maxlen=50)


def stt_cap(audio_seconds: float) -> float:
    """The speech-recognition budget for a recording ``audio_seconds``
    long (see the module docstring)."""
    lo = max(0.1, float(settings.voice_stt_timeout_min_sec))
    hi = max(lo, float(settings.voice_stt_timeout_max_sec))
    want = float(settings.voice_stt_timeout_audio_factor) * max(0.0, audio_seconds)
    return min(hi, max(lo, want))


def routing_cap() -> float:
    """The reply limit once routing has started (seconds from then): a
    cold model load (``ollama_load_timeout_sec``) plus two model calls
    (``ollama_timeout_sec`` each: the tool model's routing, then the
    answer's first sentence), plus a margin — so a slow but working model
    always answers, or fails with its own error and fallback, first. Only
    a stage that never returns runs into it."""
    return (
        float(settings.ollama_load_timeout_sec)
        + 2 * float(settings.ollama_timeout_sec)
        + ROUTING_MARGIN_SEC
    )


ROUTING_MARGIN_SEC = 30.0


def note_stall(stage: str, room: str | None = None, *, now: float | None = None) -> None:
    """Record a turn that timed out in ``stage`` (for /v1/health and the
    automatic restart's evidence)."""
    with _lock:
        _stalls.append(Stall(
            stage=stage,
            at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            mono=time.monotonic() if now is None else now,
            room=room,
        ))


def last_stall() -> Stall | None:
    with _lock:
        return _stalls[-1] if _stalls else None


SPEECH_STAGES = frozenset({STAGE_STT, STAGE_STT_SLOT, STAGE_SECOND_HEARING, STAGE_IDENTIFY})


def _speech_stalls_since(mono: float) -> int:
    with _lock:
        return sum(
            1 for s in _stalls
            if s.mono >= mono and s.stage in (STAGE_STT, STAGE_STT_SLOT, STAGE_SECOND_HEARING)
        )


def _last_where(pred) -> Stall | None:
    with _lock:
        for s in reversed(_stalls):
            if pred(s.stage):
                return s
    return None


def reset() -> None:
    """Forget every recorded stall (tests)."""
    with _lock:
        _stalls.clear()


def speech_health(*, now: float | None = None) -> dict[str, Any]:
    """The speech-recognition watchdog's state for ``/v1/health``:

    ``state``: ``ok``; ``slow`` (a request timed out in the last 15
    minutes, nothing stuck now); ``stalled`` (a speech worker has been on
    one call for ``voice_speech_stuck_after_sec`` or more: only a restart
    frees it). ``stuck_workers`` / ``workers``: how many of the speech
    pool's threads are stuck, of how many. ``stuck_for_s``: the longest.
    ``last_timeout``: the last request that timed out in speech
    recognition or voice identification — its stage and when (no room:
    /v1/health is open, and which room spoke when is a household read).
    ``last_reply_timeout``: the last one that timed out later, while its
    answer was being worked out (a slow or loading model, most likely —
    not a speech problem, so it never makes ``state`` slow)."""
    t = time.monotonic() if now is None else now
    threshold = max(1.0, float(settings.voice_speech_stuck_after_sec))
    ages = executors.running_ages(SPEECH_POOL, now=t)
    stuck = [a for a in ages if a >= threshold]
    last = _last_where(lambda st: st in SPEECH_STAGES)
    reply = _last_where(lambda st: st not in SPEECH_STAGES)
    doc: dict[str, Any] = {
        "state": "ok",
        "workers": executors.POOL_SIZES.get(SPEECH_POOL, 0),
        "stuck_workers": len(stuck),
        "stuck_for_s": int(stuck[0]) if stuck else 0,
        "last_timeout": (
            {"stage": last.stage, "at": last.at} if last is not None else None
        ),
        "last_reply_timeout": (
            {"stage": reply.stage, "at": reply.at}
            if reply is not None and t - reply.mono <= RECENT_STALL_SEC else None
        ),
    }
    if stuck:
        doc["state"] = "stalled"
    elif last is not None and t - last.mono <= RECENT_STALL_SEC:
        doc["state"] = "slow"
    return doc


def restart_reason(*, now: float | None = None) -> str | None:
    """Why the core should restart itself for speech recognition, or None.
    Asked by the loop watchdog's thread (it only acts on it under a
    service manager). Conservative on purpose: one room's decode stuck
    while the others still decode is a Health warning, not a restart. But
    a single stuck decode IS a reason when nothing has decoded since it
    began and a request has timed out on it — which is also what one room
    wedged in a quiet house looks like: then that room's turns all fail,
    and the restart is what frees it."""
    after = float(settings.voice_speech_restart_after_sec)
    if after <= 0:
        return None
    t = time.monotonic() if now is None else now
    ages = executors.running_ages(SPEECH_POOL, now=t)
    stuck = [a for a in ages if a >= after]
    if not stuck:
        return None
    size = executors.POOL_SIZES.get(SPEECH_POOL, 0)
    if size and len(stuck) >= size:
        return (f"speech recognition is stuck: all {size} speech workers have been on "
                f"one call for {stuck[-1]:.0f} s or more")
    since = t - stuck[0]
    done = executors.last_finished(SPEECH_POOL)
    nothing_finished = done is None or done <= since
    if nothing_finished and _speech_stalls_since(since) > 0:
        return (f"speech recognition is stuck: a speech worker has been on one call for "
                f"{stuck[0]:.0f} s, no speech recognition has finished since, and a "
                "request timed out waiting for it")
    return None
