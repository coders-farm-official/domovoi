"""Where a voice turn's time goes, and the numbers-only summary of it.

``intents_log.latency_ms`` is the router's share of a turn and nothing
else: its clock starts inside ``route()``, after speech-to-text, and stops
before any reply audio exists. Speech-to-text, voice identification and
the first sentence of text-to-speech sit outside it, and they are most of
what a person waits for after they stop talking. Phase 1 of the
early-endpointing work (design notes 2026-09-28) needs those numbers from
the real Domovoi server before anything is sized, so every voice turn now
carries a :class:`TurnTimings` stopwatch.

The stages, in pipeline order, all integer milliseconds:

``capture_audio_ms``
    Length of the audio the satellite sent (the capture itself, not a
    duration on the core's clock: 32 bytes per ms of 16 kHz int16).
``endpoint_silence_ms``
    The silence the satellite waited out after the last word before the
    turn's clock started: the frames after its last voiced frame, 30 ms
    each. Exact when the satellite reports that frame in
    ``utterance_end``; worked out from its ``listen.silence_timeout`` for
    one that doesn't; absent when neither is known.
``stt_ms``
    The Whisper call whose transcript the turn used: the whole capture's,
    or the speculative one started at the first pause (``stt_reused``).
``stt_wait_ms``
    From the turn's start to that transcript being ready — what
    speech-to-text actually cost the person. ``stt_ms`` (plus any wait for
    a decode of this room's already in flight) when nothing was reused;
    close to 0 when the speculative decode finished while the satellite
    was still counting silence.
``identify_ms``
    Voice identification (the embedding plus the profile lookup). When
    the turn used a speculative transcript at least
    ``voice_profile_min_utterance_sec`` long, the embedding was computed
    right after that decode and this is the lookup alone (plus whatever
    of the embedding was still running).
``route_ms``
    The routing transaction: ``route()`` (fast path or language model, the
    handler, the audit writes), the voice-profile hooks, and the commit.
    For a Q&A answer spoken as it is written (``Response.qa_stream``): up
    to its first complete sentence, the moment it can start to be heard;
    the rest of the answer and its records come after the first audio.
``tts_first_ms``
    From the routed response to the first reply audio handed to the
    socket: the voice lookup, the first sentence's synthesis and the
    ``response_start`` frame.
``total_ms``
    From the moment ``utterance_end`` arrived (or the server ended the
    capture early itself) to that first audio chunk.
    What the stages above don't account for is the small glue between
    them (the transcript frame, the chat-mode check).
``speech_to_reply_ms``
    ``endpoint_silence_ms + total_ms``: the last word to the first reply
    audio, the number a person actually feels. Only when
    ``endpoint_silence_ms`` is known.

Plus ``whisper``: the model, device, compute type and CPU threads that
actually transcribed, so a before/after comparison splits on the row, and
``stt_window_s``: the mel window the transcript was decoded on — 10 for a
short capture on the short window, 30 for faster-whisper's own path (a
long capture, the setting off, or a short decode that came back blank or
unsure; clients/whisper.py). Counted under ``stt_window``.
And, on a turn whose capture was transcribed speculatively while the
satellite was still counting silence (``domovoi/streaming.py``,
"Speculative transcription"): ``stt_reused`` (true when the turn used
that transcript), ``speculative_decodes`` (how many were started for the
utterance) and ``speculative_ms`` (their Whisper time, used or not — the
CPU the speculation cost). These ride the row but are not stages: the
summary counts them instead (``speculative``). A turn whose capture the
server ended early (part B, ``domovoi/early_commit.py``) also carries
``early_commit`` (the tier, "A" or "B"), ``early_commit_hold_ms``, and — once
the satellite's own ``utterance_end`` for it arrives — ``post_commit_voiced_ms``:
how much speech came after the server stopped listening (0 when none; the
misfire the hold exists to prevent). Counted under ``early_commit``.

And the capture clock (:data:`CAPTURE_TIMING_KEYS`, whole milliseconds, not
stages): when the capture's frames and its pause reached the server,
against the pace the audio was spoken at, and when the speculative decode
started. They answer "did the transcript start late, and whose fault was
it": the satellite (it began the capture, or sent the pause, with mic audio
still queued behind it: ``sat_*_backlog_ms``), the network (frames or the
pause held up on the way: ``frame_lag_*``, ``pause_net_ms``) or this server
(the decode queued behind another decode or a copy's voice embedding, or
started late: ``decode_wait_ms``, ``pause_to_decode_ms``). ``decode_start_ms`` is the one to watch: the
decode's start after the last voiced frame, about the satellite's pause
length (240 ms) when nothing is late. Only turns from a satellite that
stamps its messages carry the ``sat_*`` keys. Summarized under
``capture_timing``.

Where it is stored: ``intents_log.timings`` (V015, JSONB). The stages known
before routing ride ``Context.timings`` into ``router._persist_turn``, so
they land in the same INSERT, in the same transaction, as every routed
turn's audit row always has. ``route_ms``, ``tts_first_ms`` and
``total_ms`` only exist after that transaction has committed, so the
streaming layer merges them into the same row by id once the reply has
started (:func:`merge_post_route`), off the latency path, in a task a
barge-in can't cancel, and best-effort: a turn whose follow-up write fails
keeps its pre-route stages.

No identity is ever put in the column, and no text either, with one
opt-in exception: while ``fastlane_mode`` is ``shadow``, a turn the
streaming fast lane would have committed carries ``fastlane_text``, the
closed command the lane heard ("pause the music"), next to the
``fastlane_*`` numbers (domovoi/fast_lane.py): the lane's own hearing of
the words whose Whisper transcript the row's ``transcript`` column
already holds. The summary
(:func:`latency_summary`, ``GET /v1/stats/latency``) reads only the
column's numbers, ``matched_path`` and the time — never ``fastlane_text``
or ``fastlane_path`` — which is what lets that endpoint be open.
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# 16 kHz mono int16 PCM: 32 bytes per millisecond of capture.
PCM_BYTES_PER_MS = 32

# Every stage a turn can record, in pipeline order. The summary reports
# exactly these keys and reads nothing else back out of the column.
STAGES = (
    "capture_audio_ms",
    "endpoint_silence_ms",
    "stt_ms",
    "stt_wait_ms",
    "identify_ms",
    "route_ms",
    "tts_first_ms",
    "total_ms",
    "speech_to_reply_ms",
)
# The stages that only exist once the routing transaction has committed;
# merged into the row afterwards (see the module docstring).
POST_ROUTE_STAGES = ("route_ms", "tts_first_ms", "total_ms")
# Merged afterwards as well, but only when the turn has them: the last word
# to the reply needs the endpoint silence, and a capture the server ended
# early learns whether speech came after the commit from the satellite's
# own late utterance_end.
LATE_STAGES = ("speech_to_reply_ms", "post_commit_voiced_ms")

# What the per-turn `whisper` block carries, from clients.whisper.whisper_runtime.
WHISPER_KEYS = ("model", "device", "compute_type", "cpu_threads")
# faster-whisper's own window; a turn's `stt_window_s` below it was decoded
# on the short window (clients/whisper.py SHORT_WINDOW_SEC).
FULL_WINDOW_S = 30

# The capture clock (domovoi/streaming.py, _CaptureClock), in milliseconds.
# Recorded on the row next to the stages, summarized apart from them.
CAPTURE_TIMING_KEYS = (
    # The satellite's own view (only from one that stamps its messages):
    "sat_wake_ms",            # wake word -> utterance_start (acknowledgement included)
    "sat_start_backlog_ms",   # mic audio already queued when the capture opened
    "sat_pause_ms",           # utterance_start -> the pause the copy was taken at
    "sat_pause_backlog_ms",   # mic audio queued behind that pause when it was sent
    "sat_end_backlog_ms",     # ... and behind utterance_end
    # Arrival here, against the pace the audio was spoken at:
    "frame_lag_first_ms",     # the capture's first frame
    "frame_lag_max_ms",       # its worst frame
    "pause_rx_lag_ms",        # the pause the copy was taken at
    "end_rx_lag_ms",          # utterance_end
    "pause_net_ms",           # the pause's trip, beyond utterance_start's (signed)
    # This server:
    "pause_to_decode_ms",     # that pause's arrival -> its decode starting
    "decode_wait_ms",         # of which: waiting on this room's decode slot
    "decode_start_ms",        # last voiced frame -> the decode starting
    "stt_decode_wait_ms",     # the turn's own decode, waiting on this room's decode slot
)
# The one of those that may be negative.
_SIGNED_CAPTURE_KEYS = frozenset({"pause_net_ms"})

# The summary's default window, and the most recent timed turns it reads
# inside any window: enough for stable percentiles, bounded work per call.
DEFAULT_WINDOW = timedelta(days=7)
SUMMARY_ROW_CAP = 1000


def _ms_since(t0: float) -> int:
    # time.perf_counter(), not time.monotonic(): both only ever go forward,
    # but on Windows before Python 3.13 monotonic() ticks every 15.6 ms,
    # which would round a 20 ms route or a 40 ms voice lookup to a multiple
    # of the tick. Every mark handed to a TurnTimings (the utterance_end
    # receipt, each stage's start) comes from perf_counter too.
    return max(0, int(round((time.perf_counter() - t0) * 1000)))


class TurnTimings:
    """One voice turn's stopwatch, created when ``utterance_end`` arrives.

    Carried on ``Context.timings`` into the router, which writes the stages
    known so far with the turn's ``intents_log`` row and leaves the row's
    id in :attr:`intents_log_id`, so the streaming layer can add the rest
    once the reply has started playing. ``Context`` copies are shallow, so
    every copy the router makes shares this one object.
    """

    __slots__ = ("started", "stages", "flags", "whisper", "intents_log_id", "extra")

    def __init__(self, *, started: float | None = None, audio_bytes: int = 0) -> None:
        # time.perf_counter() at utterance_end receipt; now when not given.
        self.started = time.perf_counter() if started is None else started
        self.stages: dict[str, int] = {
            "capture_audio_ms": max(0, int(audio_bytes)) // PCM_BYTES_PER_MS,
        }
        # What the stages don't say on their own (was the transcript the
        # speculative one, how many were decoded). Written with the row,
        # never summarized as a stage.
        self.flags: dict[str, Any] = {}
        self.whisper: dict[str, Any] | None = None
        # Set by router._persist_turn (or the chat-turn writer) once this
        # turn's row carries the timings; None means there is nothing to
        # merge into.
        self.intents_log_id: int | None = None
        # Keys that ride along with the stages but aren't stages: the fast
        # lane's shadow record (fastlane_*, domovoi/fast_lane.py). Written
        # with the row; the summary never reads them as stages.
        self.extra: dict[str, Any] = {}

    def note(self, **fields: Any) -> None:
        """Add non-stage keys to the row document (see ``extra``)."""
        self.extra.update(fields)

    def stage(self, name: str, t0: float) -> None:
        """Record stage ``name`` as the time since ``t0`` (a
        ``time.perf_counter()`` mark taken just before the stage started)."""
        self.stages[name] = _ms_since(t0)

    def first_audio(self, tts_started: float) -> None:
        """The first reply audio chunk has just gone to the socket. Only the
        first call counts, so the send loops can call it per chunk."""
        if "total_ms" in self.stages:
            return
        self.stages["tts_first_ms"] = _ms_since(tts_started)
        self.stages["total_ms"] = _ms_since(self.started)
        silence = self.stages.get("endpoint_silence_ms")
        if silence is not None:
            self.stages["speech_to_reply_ms"] = silence + self.stages["total_ms"]

    def row_document(self) -> dict[str, Any]:
        """The JSON the turn's row is written with: every stage known now,
        the flags, the fast lane's shadow record, plus the Whisper block."""
        doc: dict[str, Any] = dict(self.stages)
        doc.update(self.flags)
        doc.update(self.extra)
        if self.whisper is not None:
            doc["whisper"] = dict(self.whisper)
        return doc

    def post_route_patch(self) -> dict[str, int]:
        """The stages the row doesn't have yet (see POST_ROUTE_STAGES and
        LATE_STAGES)."""
        return {
            k: self.stages[k]
            for k in POST_ROUTE_STAGES + LATE_STAGES
            if k in self.stages
        }

    def describe(self) -> str:
        """One log-line fragment: ``stt_ms=640 identify_ms=41 ...`` in
        pipeline order, then the Whisper that ran. No text, no identity."""
        parts = [f"{k}={self.stages[k]}" for k in STAGES if k in self.stages]
        if "stt_reused" in self.flags:
            parts.append(
                f"stt={'reused' if self.flags['stt_reused'] else 'full'}"
                f"/{self.flags.get('speculative_decodes', 0)}spec"
            )
        if "early_commit" in self.flags:
            parts.append(
                f"early_commit={self.flags['early_commit']}"
                f"/{self.stages.get('early_commit_hold_ms', '?')}ms"
            )
        if "stt_window_s" in self.flags:
            parts.append(f"window={self.flags['stt_window_s']}s")
        for key in ("decode_start_ms", "pause_rx_lag_ms", "sat_start_backlog_ms"):
            if key in self.flags:
                parts.append(f"{key}={self.flags[key]}")
        if self.whisper and self.whisper.get("model"):
            w = self.whisper
            threads = w.get("cpu_threads")
            parts.append(
                f"whisper={w.get('model')}/{w.get('device')}/{w.get('compute_type')}"
                + (f"/{threads}t" if threads else "")
            )
        return " ".join(parts)


def whisper_block(runtime: dict[str, Any]) -> dict[str, Any]:
    """The per-turn ``whisper`` block from ``whisper_runtime()``."""
    return {k: runtime.get(k) for k in WHISPER_KEYS}


# ─── Storage ───────────────────────────────────────────────────────────────

# Whether intents_log has V015's `timings` column — the same tolerance
# repositories._has_utterance_trigger extends to V011. The INSERT that would
# carry it is on the path EVERY routed turn takes, so a core pulled and
# restarted without the migration must keep routing turns (just without
# timings) rather than fail them all. A yes is cached for the life of the
# process (migrations are append-only); a no is asked again after
# _COLUMN_RECHECK_SEC, so running Flyway on a live server starts the
# recording without a second restart. The dashboard's plain restart does
# not migrate (domovoi/self_restart.py); the update unit does.
_HAS_TIMINGS_COLUMN: bool | None = None
_COLUMN_RECHECK_SEC = 60.0
_column_checked_at = 0.0


async def has_timings_column(s: AsyncSession) -> bool:
    global _HAS_TIMINGS_COLUMN, _column_checked_at
    if _HAS_TIMINGS_COLUMN:
        return True
    now = time.monotonic()
    if _HAS_TIMINGS_COLUMN is False and now - _column_checked_at < _COLUMN_RECHECK_SEC:
        return False
    row = (
        await s.execute(
            text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'intents_log'
                  AND column_name = 'timings'
                """
            )
        )
    ).first()
    first_answer = _HAS_TIMINGS_COLUMN is None
    _HAS_TIMINGS_COLUMN = row is not None
    _column_checked_at = now
    if not _HAS_TIMINGS_COLUMN and first_answer:
        log.warning(
            "intents_log.timings is missing — voice turns are recorded "
            "without their stage timings, and GET /v1/stats/latency has "
            "nothing to report. Run the Flyway migrations (V015); recording "
            "starts within a minute, no restart needed."
        )
    elif _HAS_TIMINGS_COLUMN and not first_answer:
        log.info("intents_log.timings is present now — recording voice-turn stage timings")
    return _HAS_TIMINGS_COLUMN


async def timings_for_row(
    s: AsyncSession, timings: "TurnTimings | None"
) -> dict[str, Any] | None:
    """What an ``intents_log`` INSERT should carry in ``timings``: the
    turn's document, or None when the turn has no stopwatch (a typed
    ``/v1/intent``, a dashboard action) or the column isn't there yet."""
    if timings is None or not await has_timings_column(s):
        return None
    return timings.row_document()


async def merge_post_route(s: AsyncSession, row_id: int, patch: dict[str, int]) -> None:
    """Merge the post-route stages into the turn's row (``jsonb ||``: the
    keys in ``patch`` win, everything already there stays)."""
    await s.execute(
        text(
            """
            UPDATE intents_log
               SET timings = COALESCE(timings, '{}'::jsonb) || CAST(:patch AS jsonb)
             WHERE id = :id
            """
        ),
        {"patch": json.dumps(patch), "id": row_id},
    )


# ─── The summary ───────────────────────────────────────────────────────────


def percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation percentile (numpy's default, Postgres's
    ``percentile_cont``) over an already-sorted, non-empty list."""
    if not sorted_values:
        raise ValueError("percentile of an empty list")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = (len(sorted_values) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_values[lo])
    frac = pos - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def _round_ms(v: float) -> int:
    return int(math.floor(v + 0.5))


def _stage_value(doc: dict[str, Any], stage: str) -> float | None:
    v = doc.get(stage)
    # bool is an int subclass; a stage is never one.
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if not math.isfinite(v) or v < 0:
        return None
    return float(v)


def _whisper_key(doc: dict[str, Any]) -> tuple[Any, ...] | None:
    w = doc.get("whisper")
    if not isinstance(w, dict):
        return None
    out = []
    for k in WHISPER_KEYS:
        v = w.get(k)
        out.append(v if isinstance(v, (str, int)) and not isinstance(v, bool) else None)
    return tuple(out)


def _capture_value(doc: dict[str, Any], key: str) -> float | None:
    if key not in _SIGNED_CAPTURE_KEYS:
        return _stage_value(doc, key)
    v = doc.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _spread(vals: list[float]) -> dict[str, Any]:
    """``{count, p50, p95, max}`` of ``vals``; nulls when empty."""
    if not vals:
        return {"count": 0, "p50": None, "p95": None, "max": None}
    vals.sort()
    return {
        "count": len(vals),
        "p50": _round_ms(percentile(vals, 0.50)),
        "p95": _round_ms(percentile(vals, 0.95)),
        "max": _round_ms(vals[-1]),
    }


def summarize(rows: Iterable[tuple[Any, Any]]) -> dict[str, Any]:
    """Per-stage ``{count, p50, p95, max}``, the matched-path mix, the
    Whisper settings seen, how the speculative transcripts fared
    (``speculative``: turns that had one, how many used it, decodes
    started), the captures the server ended early (``early_commit``:
    how many, by tier, and how many of those cut in on speech), the
    window each turn's transcript was decoded on (``stt_window``:
    ``short``, the 10 s one, or ``full``, the 30 s one) and the
    capture clock (``capture_timing``: the same four numbers per
    :data:`CAPTURE_TIMING_KEYS` key), over ``(timings, matched_path)``
    rows.

    Pure — the endpoint's arithmetic, testable without a database. A row
    whose ``timings`` isn't a JSON object is skipped; a stage that is
    missing or not a non-negative number is left out of that stage's
    numbers only. Percentiles interpolate linearly and are rounded to
    whole milliseconds; a stage nobody recorded reports ``count: 0`` and
    nulls.
    """
    values: dict[str, list[float]] = {s: [] for s in STAGES}
    capture: dict[str, list[float]] = {k: [] for k in CAPTURE_TIMING_KEYS}
    paths: dict[str, int] = {}
    seen: dict[tuple[Any, ...], int] = {}
    turns = 0
    speculative = {"turns": 0, "reused": 0, "decodes": 0}
    early_commit = {"turns": 0, "A": 0, "B": 0, "cut_in": 0}
    stt_window = {"short": 0, "full": 0}
    for raw, matched_path in rows:
        doc = raw
        if isinstance(doc, (str, bytes)):
            try:
                doc = json.loads(doc)
            except ValueError:
                continue
        if not isinstance(doc, dict):
            continue
        turns += 1
        for stage in STAGES:
            v = _stage_value(doc, stage)
            if v is not None:
                values[stage].append(v)
        for key in CAPTURE_TIMING_KEYS:
            v = _capture_value(doc, key)
            if v is not None:
                capture[key].append(v)
        reused = doc.get("stt_reused")
        if isinstance(reused, bool):
            speculative["turns"] += 1
            speculative["reused"] += int(reused)
            decodes = _stage_value(doc, "speculative_decodes")
            if decodes is not None:
                speculative["decodes"] += int(decodes)
        tier = doc.get("early_commit")
        if tier in ("A", "B"):
            early_commit["turns"] += 1
            early_commit[tier] += 1
            cut_in = _stage_value(doc, "post_commit_voiced_ms")
            if cut_in:
                early_commit["cut_in"] += 1
        window = _stage_value(doc, "stt_window_s")
        if window is not None:
            stt_window["short" if window < FULL_WINDOW_S else "full"] += 1
        if isinstance(matched_path, str) and matched_path:
            paths[matched_path] = paths.get(matched_path, 0) + 1
        wk = _whisper_key(doc)
        if wk is not None:
            seen[wk] = seen.get(wk, 0) + 1

    stages = {stage: _spread(vals) for stage, vals in values.items()}
    whisper_seen = [
        {**dict(zip(WHISPER_KEYS, key)), "turns": n}
        for key, n in sorted(seen.items(), key=lambda kv: -kv[1])
    ]
    return {
        "turns": turns,
        "stages": stages,
        "paths": dict(sorted(paths.items(), key=lambda kv: (-kv[1], kv[0]))),
        "whisper_seen": whisper_seen,
        "speculative": speculative,
        "early_commit": early_commit,
        "stt_window": stt_window,
        "capture_timing": {key: _spread(vals) for key, vals in capture.items()},
    }


def _as_utc(dt: datetime) -> datetime:
    # A `since` with no offset is read as UTC — the database stores `at`
    # as timestamptz, and the host's own zone is not the caller's business.
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


async def latency_summary(
    s: AsyncSession,
    *,
    since: datetime | None = None,
    room: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The latency summary over the most recent :data:`SUMMARY_ROW_CAP`
    timed turns at or after ``since`` (default: the last
    :data:`DEFAULT_WINDOW`), optionally for one room. Numbers only — the
    query selects the timings, the matched path and nothing else."""
    now = now or datetime.now(timezone.utc)
    since_utc = _as_utc(since) if since is not None else now - DEFAULT_WINDOW
    where = ["timings IS NOT NULL", "at >= :since"]
    params: dict[str, Any] = {"since": since_utc, "cap": SUMMARY_ROW_CAP}
    if room:
        where.append("room_id = :room")
        params["room"] = room
    rows = (
        await s.execute(
            text(
                "SELECT timings, matched_path FROM intents_log "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY at DESC, id DESC LIMIT :cap"
            ),
            params,
        )
    ).all()
    doc = {
        "since": since_utc.isoformat(),
        "room": room or None,
        "limit": SUMMARY_ROW_CAP,
        **summarize((r[0], r[1]) for r in rows),
    }
    # The fast lane's shadow counts, when it is on or the window has any
    # (domovoi/fast_lane.py); absent otherwise, so the answer is unchanged.
    from domovoi.fast_lane import shadow_summary

    fastlane = shadow_summary(r[0] for r in rows)
    if fastlane is not None:
        doc["fastlane"] = fastlane
    return doc
