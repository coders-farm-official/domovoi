"""A satellite's voice turn in the room's conversation history from the
moment its transcript exists (owner's design, 2026-11-09; V043).

Until now a turn reached ``conversation_log`` only through
``router._persist_turn``, inside route()'s transaction — so a turn that
hung or failed before or during routing left no trace, and the
2026-11-09 wedge (every room "thinking" for good) showed nothing at all.

Now:

1. **Transcript** — :func:`open_entry` writes the row at once, committed
   on its own, with ``turn_status = 'pending'``: the dashboard drawer and
   the app show what was said and "waiting for an answer…".
2. **Reply** — the router's ``_persist_turn`` (and the chat-mode writer)
   pass the entry to ``ConversationLogRepository.record_turn``, which
   fills in the SAME row (``answered``) instead of inserting one. Still
   exactly one ``intents_log`` row and one ``conversation_log`` row per
   routed turn. The entry is taken once (:meth:`HistoryEntry.take`), so a
   second write in one turn inserts as it always did; a row that is gone
   (deleted by "forget what I said" mid-turn) is inserted again.
3. **No reply** — :func:`close_entry` marks a still-pending row
   ``no_answer`` with the reason (timed out, error, interrupted). An
   answer always wins: the router's update ignores the status, the
   failure mark touches only a pending row, so a barge-in racing the
   streamed answer's record can't leave "no answer" over an answer.
4. **Nothing heard** — speech recognition failed or timed out:
   :func:`record_unheard` writes a row of its own, ``unheard``, no text,
   the recording's length and the time of the request.
5. **Restart** — :func:`sweep_pending` closes rows a stopped core left
   pending (``no_answer`` / ``restarted``), once at start-up.

Everything here is best-effort and bounded: a history write that fails
or takes longer than :data:`WRITE_TIMEOUT_SEC` is logged and the turn
goes on (the router then inserts its row as before).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

PENDING = "pending"
ANSWERED = "answered"
NO_ANSWER = "no_answer"
UNHEARD = "unheard"

TIMED_OUT = "timed_out"
ERROR = "error"
INTERRUPTED = "interrupted"
RESTARTED = "restarted"
SPEECH_OFF = "speech_off"

REASONS = frozenset({TIMED_OUT, ERROR, INTERRUPTED, RESTARTED, SPEECH_OFF})

# A history write never holds a turn longer than this.
WRITE_TIMEOUT_SEC = 5.0

# Whether conversation_log has V043's columns. Probed once per process, then
# cached; like V011's utterance_trigger (repositories._has_utterance_trigger),
# a core started before Flyway ran keeps answering, just without the new
# history states.
_HAS_STATUS: bool | None = None


async def has_status_columns(s: AsyncSession) -> bool:
    global _HAS_STATUS
    if _HAS_STATUS is None:
        row = (
            await s.execute(
                text(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'conversation_log'
                      AND column_name = 'turn_status'
                    """
                )
            )
        ).first()
        _HAS_STATUS = row is not None
        if not _HAS_STATUS:
            log.warning(
                "conversation_log.turn_status is missing: voice turns are "
                "recorded once answered, as before. Run the Flyway migrations "
                "(V043) for the history to show requests still waiting for, "
                "or never given, an answer."
            )
    return _HAS_STATUS


def _forget_probe() -> None:
    """Probe again on next use (tests that drop and re-add the columns)."""
    global _HAS_STATUS
    _HAS_STATUS = None


@dataclass
class HistoryEntry:
    """A pending conversation_log row of one voice turn. Shared by
    reference across the turn's Context copies (``Context.history_entry``;
    pydantic's model_copy is shallow), so whichever write uses it first
    takes it."""

    row_id: int
    taken: bool = False

    def take(self) -> int | None:
        """The row to fill in with the routed turn — once; None after."""
        if self.taken:
            return None
        self.taken = True
        return self.row_id


async def _bounded(coro, what: str, room: str | None):
    try:
        return await asyncio.wait_for(coro, WRITE_TIMEOUT_SEC)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — the history never breaks a turn
        log.warning("history: %s for %s failed: %s", what, room, e)
        return None


async def open_entry(
    *,
    room_id: str | None,
    session_id: UUID | None,
    user_text: str,
    trigger: str | None,
    audio_ms: int | None,
    at: datetime,
) -> HistoryEntry | None:
    """Write the turn's row now, reply pending; None when that can't be
    done (no V043, the database didn't answer)."""
    from domovoi.db.session import session_scope

    async def _write() -> HistoryEntry | None:
        async with session_scope() as s:
            if not await has_status_columns(s):
                return None
            row = (
                await s.execute(
                    text(
                        """
                        INSERT INTO conversation_log
                            (session_id, room_id, user_text, utterance_trigger,
                             turn_status, audio_ms, at)
                        VALUES
                            (:session_id, :room_id, :user_text, :trigger,
                             'pending', :audio_ms, :at)
                        RETURNING id
                        """
                    ),
                    {
                        "session_id": str(session_id) if session_id else None,
                        "room_id": room_id,
                        "user_text": user_text,
                        "trigger": trigger,
                        "audio_ms": audio_ms,
                        "at": at,
                    },
                )
            ).first()
            return HistoryEntry(int(row[0])) if row is not None else None

    return await _bounded(_write(), "opening the history entry", room_id)


async def close_entry(entry: HistoryEntry | None, reason: str, *, room_id: str | None) -> None:
    """The turn ended with no reply: a still-pending row says so. Leaves an
    answered row alone."""
    if entry is None:
        return
    row_id = entry.row_id
    from domovoi.db.session import session_scope

    async def _write() -> None:
        async with session_scope() as s:
            await s.execute(
                text(
                    """
                    UPDATE conversation_log
                    SET turn_status = 'no_answer', no_answer_reason = :reason
                    WHERE id = :id AND turn_status = 'pending'
                    """
                ),
                {"id": row_id, "reason": reason},
            )

    await _bounded(_write(), "closing the history entry", room_id)


async def settle_entry(
    entry: HistoryEntry | None, assistant_text: str | None, *, room_id: str | None,
) -> None:
    """A turn that ended normally but whose record never filled in its
    pending row (that write failed): the row gets what was said, or "no
    answer — something went wrong" when there is nothing to show."""
    if entry is None:
        return
    row_id = entry.row_id
    from domovoi.db.session import session_scope

    async def _write() -> None:
        async with session_scope() as s:
            if assistant_text:
                await s.execute(
                    text(
                        """
                        UPDATE conversation_log
                        SET turn_status = 'answered', assistant_text = :t
                        WHERE id = :id AND turn_status = 'pending'
                        """
                    ),
                    {"id": row_id, "t": assistant_text},
                )
            else:
                await s.execute(
                    text(
                        """
                        UPDATE conversation_log
                        SET turn_status = 'no_answer', no_answer_reason = 'error'
                        WHERE id = :id AND turn_status = 'pending'
                        """
                    ),
                    {"id": row_id},
                )

    await _bounded(_write(), "settling the history entry", room_id)


async def record_unheard(
    *,
    room_id: str | None,
    session_id: UUID | None,
    trigger: str | None,
    audio_ms: int | None,
    reason: str,
    at: datetime,
) -> None:
    """A request speech recognition never made out: a row of its own, no
    text, with when it was made and how long the recording was."""
    from domovoi.db.session import session_scope

    async def _write() -> None:
        async with session_scope() as s:
            if not await has_status_columns(s):
                return
            await s.execute(
                text(
                    """
                    INSERT INTO conversation_log
                        (session_id, room_id, user_text, utterance_trigger,
                         turn_status, no_answer_reason, audio_ms, at)
                    VALUES
                        (:session_id, :room_id, '', :trigger,
                         'unheard', :reason, :audio_ms, :at)
                    """
                ),
                {
                    "session_id": str(session_id) if session_id else None,
                    "room_id": room_id,
                    "trigger": trigger,
                    "reason": reason,
                    "audio_ms": audio_ms,
                    "at": at,
                },
            )

    await _bounded(_write(), "recording an unheard request", room_id)


async def complete_row(
    s: AsyncSession,
    row_id: int,
    params: dict,
    *,
    with_trigger: bool,
) -> bool:
    """Fill in a pending row with the routed turn (the router's write, in
    its transaction). True when the row was there to fill in."""
    trigger_set = ", utterance_trigger = COALESCE(:trigger, utterance_trigger)" if with_trigger else ""
    result = await s.execute(
        text(
            f"""
            UPDATE conversation_log
            SET session_id = :session_id, room_id = :room_id,
                person_id = :person_id, user_text = :user_text,
                assistant_text = :assistant_text,
                matched_handler = :matched_handler, matched_path = :matched_path,
                presence_tier = :presence_tier, online = :online,
                latency_ms = :latency_ms{trigger_set},
                turn_status = 'answered', no_answer_reason = NULL
            WHERE id = :pending_id
            """
        ),
        {**params, "pending_id": row_id},
    )
    return (result.rowcount or 0) > 0


async def sweep_pending() -> int:
    """At start-up: rows a stopped core left pending say it restarted.
    Returns how many."""
    from domovoi.db.session import session_scope

    async def _write() -> int:
        async with session_scope() as s:
            if not await has_status_columns(s):
                return 0
            result = await s.execute(
                text(
                    """
                    UPDATE conversation_log
                    SET turn_status = 'no_answer', no_answer_reason = 'restarted'
                    WHERE turn_status = 'pending'
                    """
                )
            )
            return int(result.rowcount or 0)

    n = await _bounded(_write(), "closing turns left pending", None)
    if n:
        log.info("history: %d voice turn(s) left waiting by the last stop now say so", n)
    return n or 0
