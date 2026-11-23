from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import json
import logging
import secrets

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.clients.speaker_encoder import MODEL_TAG
from domovoi.models import PHONE_ROOM_PREFIX

log = logging.getLogger(__name__)

# Whether conversation_log has V011's `utterance_trigger` column. Probed once
# per process, then cached.
#
# The column is additive, but the INSERT that uses it is on the path EVERY
# spoken turn takes — so a core started against a database that hasn't had
# Flyway run would fail every interaction, not degrade. That's too sharp an
# edge for a diagnostic field. Same tolerance `_list_rooms` extends to a
# missing V003, for the same reason.
_HAS_UTTERANCE_TRIGGER: bool | None = None


async def _has_utterance_trigger(s: AsyncSession) -> bool:
    global _HAS_UTTERANCE_TRIGGER
    if _HAS_UTTERANCE_TRIGGER is None:
        row = (
            await s.execute(
                text(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'conversation_log'
                      AND column_name = 'utterance_trigger'
                    """
                )
            )
        ).first()
        _HAS_UTTERANCE_TRIGGER = row is not None
        if not _HAS_UTTERANCE_TRIGGER:
            log.warning(
                "conversation_log.utterance_trigger is missing — recording "
                "turns without it. Run the Flyway migrations (V011) to see "
                "what opened the mic for each turn in the dashboard."
            )
    return _HAS_UTTERANCE_TRIGGER


# NOTIFY channel for any change to the `timers` table. The web process
# LISTENs on it and pushes the `timers` realtime channel (web/backend/
# realtime.py), so the dashboard's Home page sees a voice-set timer, a
# cancel from any client, and a timer firing within a moment instead of on
# a poll. Commit-coupled like `acquisitions_changed` (domovoi/acquisitions
# .py): it runs on the caller's open session, and Postgres delivers a
# NOTIFY only on COMMIT, so a rolled-back write never announces anything.
TIMERS_CHANGED_CHANNEL = "timers_changed"

# NOTIFY channel for any write to the V018 fire ledger (timer_fires /
# timer_fire_deliveries): a timer or reminder going off, each room's
# announcement starting and ending, an acknowledgement. The web maps it to
# the `timer_fires` realtime channel. The payload is informational (the
# fire ids, comma-separated); the listener refetches its snapshot whatever
# it says. Commit-coupled like `timers_changed`. Written by
# domovoi/timer_delivery.py.
TIMER_FIRES_CHANGED_CHANNEL = "timer_fires_changed"


async def notify_timers_changed(session: AsyncSession, reason: str) -> None:
    """Queue a ``timers_changed`` NOTIFY on ``session``'s transaction.

    ``reason`` is informational (``created`` / ``cancelled`` / ``fired``);
    the listener refetches the whole list whatever it says. Every site that
    writes the ``timers`` table calls this — :class:`TimerRepository`, the
    reminder handler's own cancels, and the dashboard's cancel routes."""
    await session.execute(
        text("SELECT pg_notify(:channel, :reason)"),
        {"channel": TIMERS_CHANGED_CHANNEL, "reason": reason},
    )


class TimerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def create(
        self,
        *,
        expires_at: datetime,
        label: str | None,
        message: str | None,
        room_id: str | None,
        created_at: datetime | None = None,
    ) -> int:
        # ``created_at`` defaults to NOW(), the transaction start, which on a
        # tool-routed turn is seconds before the caller computed
        # ``expires_at``. A caller that needs ``expires_at - created_at`` to
        # be the exact duration passes the instant it computed it from.
        row = await self.s.execute(
            text(
                """
                INSERT INTO timers (label, message, expires_at, room_id, created_at)
                VALUES (:label, :message, :expires_at, :room_id,
                        COALESCE(:created_at, NOW()))
                RETURNING id
                """
            ),
            {
                "label": label,
                "message": message,
                "expires_at": expires_at,
                "room_id": room_id,
                "created_at": created_at,
            },
        )
        timer_id = int(row.scalar_one())
        await notify_timers_changed(self.s, "created")
        return timer_id

    async def cancel_by_label(
        self, label: str | None, room_id: str | None, *, house_wide: bool = False,
    ) -> int:
        """Cancel plain timers — never a reminder (``message IS NULL``
        always; reminders have their own cancel in ReminderHandler).

        * no label: this room's timers (``room_id`` NULL = the roomless
          ones), or every timer in the house when ``house_wide``;
        * a label: that label in this room, or in every room when
          ``house_wide`` or when the turn has no room (the app and the
          dashboard chat are house-wide by nature).

        Before 2026-09-30 a plain cancel deleted this room's reminders too,
        and a labelled one deleted the label in every room."""
        if label is None:
            if house_wide:
                sql, params = "DELETE FROM timers WHERE message IS NULL", {}
            else:
                sql = (
                    "DELETE FROM timers WHERE room_id IS NOT DISTINCT FROM :room_id "
                    "AND message IS NULL"
                )
                params = {"room_id": room_id}
        elif house_wide or room_id is None:
            sql = "DELETE FROM timers WHERE label = :label AND message IS NULL"
            params = {"label": label}
        else:
            sql = (
                "DELETE FROM timers WHERE label = :label AND message IS NULL "
                "AND room_id = :room_id"
            )
            params = {"label": label, "room_id": room_id}
        result = await self.s.execute(text(sql), params)
        deleted = result.rowcount or 0
        if deleted:
            await notify_timers_changed(self.s, "cancelled")
        return deleted

    async def label_rooms(self, label: str) -> list[str]:
        """The rooms that have a plain timer with this label, for the "it's
        in the garage" hint when a room-scoped cancel found nothing."""
        rows = await self.s.execute(
            text(
                """
                SELECT DISTINCT room_id FROM timers
                WHERE label = :label AND message IS NULL AND room_id IS NOT NULL
                ORDER BY room_id
                """
            ),
            {"label": label},
        )
        return [r[0] for r in rows.all()]

    async def next_active(self, room_id: str | None) -> tuple[int, datetime, str | None] | None:
        """The room's next plain timer; reminders are not timers. "How long
        left on the timer" reads :meth:`next_for_status`, which prefers this
        and names a lone reminder as a reminder."""
        row = await self.s.execute(
            text(
                """
                SELECT id, expires_at, label
                FROM timers
                WHERE room_id IS NOT DISTINCT FROM :room_id
                  AND message IS NULL
                ORDER BY expires_at ASC
                LIMIT 1
                """
            ),
            {"room_id": room_id},
        )
        result = row.first()
        if result is None:
            return None
        return int(result[0]), result[1], result[2]

    async def next_for_status(
        self, room_id: str | None
    ) -> tuple[int, datetime, str | None, str | None] | None:
        """What "how long left on the timer" reads: the room's soonest plain
        timer, or its soonest reminder when it has no timer running.

        Returns (id, expires_at, label, message); ``message`` is not None
        for a reminder (``''`` for one set with no task), so the reply can
        call it a reminder rather than "the 10 minute reminder timer".
        """
        row = await self.s.execute(
            text(
                """
                SELECT id, expires_at, label, message
                FROM timers
                WHERE room_id IS NOT DISTINCT FROM :room_id
                ORDER BY (message IS NOT NULL), expires_at ASC
                LIMIT 1
                """
            ),
            {"room_id": room_id},
        )
        result = row.first()
        if result is None:
            return None
        return int(result[0]), result[1], result[2], result[3]

    async def pop_expired(
        self,
    ) -> list[tuple[int, str | None, str | None, str | None, datetime, datetime]]:
        """Atomically select + delete all expired timers.

        Returns (id, label, message, room_id, created_at, expires_at).

        NOTIFYs ``timers_changed`` only when something fired: the watcher
        calls this every tick, and an empty sweep changed nothing.
        """
        result = await self.s.execute(
            text(
                """
                DELETE FROM timers
                WHERE expires_at <= NOW()
                RETURNING id, label, message, room_id, created_at, expires_at
                """
            )
        )
        rows = [(int(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in result.all()]
        if rows:
            await notify_timers_changed(self.s, "fired")
        return rows


class IntentLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def log(
        self,
        *,
        room_id: str | None,
        transcript: str | None,
        matched_handler: str | None,
        matched_path: str | None,
        online: bool | None,
        latency_ms: int | None,
        person_id: int | None = None,
        presence_tier: str | None = None,
        timings: dict[str, Any] | None = None,
    ) -> int:
        """Write one row and return its id.

        ``timings`` is a voice turn's stage document (domovoi/turn_timings.py).
        The column is named only when a document is given, and callers get
        one from ``turn_timings.timings_for_row``, which answers None while
        V015 is missing — so a database that hasn't had the migration still
        takes every row."""
        cols = [
            "room_id", "transcript", "matched_handler", "matched_path",
            "online", "latency_ms", "person_id", "presence_tier",
        ]
        params: dict[str, Any] = {
            "room_id": room_id,
            "transcript": transcript,
            "matched_handler": matched_handler,
            "matched_path": matched_path,
            "online": online,
            "latency_ms": latency_ms,
            "person_id": person_id,
            "presence_tier": presence_tier,
        }
        values = [f":{c}" for c in cols]
        if timings is not None:
            cols.append("timings")
            values.append("CAST(:timings AS jsonb)")
            params["timings"] = json.dumps(timings)
        row = await self.s.execute(
            text(
                f"INSERT INTO intents_log ({', '.join(cols)}) "
                f"VALUES ({', '.join(values)}) RETURNING id"
            ),
            params,
        )
        return int(row.scalar_one())


class ConversationLogRepository:
    """Flat audit log of every routed turn: user transcript + assistant
    response side-by-side, plus the routing metadata for joins back to
    `intents_log` if you need it.

    Lives independent of `sessions.context.recent_turns` (which is
    capped by ``session_recent_turns_cap`` for prompt-budget reasons
    and gets truncated as turns roll off). This table is the
    full-fidelity history — useful for debugging multi-turn issues,
    auditing what the bot said in any room over the last week, or
    feeding a future training dataset — for as long as "Keep voice
    history" keeps it (domovoi/voice_history.py; 90 days by default).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def latest_assistant_text_in_room(
        self, *, room_id: str | None, within_seconds: int
    ) -> str | None:
        """Most recent non-empty assistant_text spoken in ``room_id`` within
        the trailing ``within_seconds`` window. Used by RepeatHandler as a
        fallback when ``sessions.context.last_assistant_response`` is empty
        (typically because the prior session aged out between turns).
        Returns None if no qualifying row exists.
        """
        result = await self.s.execute(
            text(
                """
                SELECT assistant_text
                FROM conversation_log
                WHERE room_id IS NOT DISTINCT FROM :room_id
                  AND assistant_text IS NOT NULL
                  AND assistant_text <> ''
                  AND at >= NOW() - make_interval(secs => :within_seconds)
                ORDER BY at DESC
                LIMIT 1
                """
            ),
            {"room_id": room_id, "within_seconds": within_seconds},
        )
        row = result.first()
        if row is None:
            return None
        value = row[0]
        if not isinstance(value, str) or not value.strip():
            return None
        return value

    async def record_turn(
        self,
        *,
        session_id: UUID | None,
        room_id: str | None,
        person_id: int | None,
        user_text: str | None,
        assistant_text: str | None,
        matched_handler: str | None,
        matched_path: str | None,
        presence_tier: str | None,
        online: bool | None,
        latency_ms: int | None,
        trigger: str | None = None,
        history_entry: Any = None,
    ) -> None:
        """One routed turn's row. ``history_entry`` (a voice turn's
        :class:`domovoi.turn_history.HistoryEntry`): the row was written
        pending when the transcript existed, and is filled in here instead
        of a second one being inserted — still one row per turn. Taken once:
        a second write in the same turn inserts, as it always did."""
        with_trigger = await _has_utterance_trigger(self.s)
        params: dict[str, Any] = {
            "session_id": str(session_id) if session_id else None,
            "room_id": room_id,
            "person_id": person_id,
            "user_text": user_text,
            "assistant_text": assistant_text,
            "matched_handler": matched_handler,
            "matched_path": matched_path,
            "presence_tier": presence_tier,
            "online": online,
            "latency_ms": latency_ms,
            **({"trigger": trigger} if with_trigger else {}),
        }
        pending_id = await history_entry.take() if history_entry is not None else None
        from domovoi import turn_history

        # How voice identification scored the turn (V049), when the
        # streaming layer has said by now.
        voice_match = getattr(history_entry, "voice_match", None)
        if voice_match is not None and await turn_history.has_voice_match_columns(self.s):
            params.update(voice_match.columns())
        if pending_id is not None and await turn_history.complete_row(
            self.s, pending_id, params, with_trigger=with_trigger,
        ):
            return
        with_status = await turn_history.has_status_columns(self.s)
        cols = (
            "presence_tier, online, latency_ms, utterance_trigger"
            if with_trigger
            else "presence_tier, online, latency_ms"
        )
        vals = (
            ":presence_tier, :online, :latency_ms, :trigger"
            if with_trigger
            else ":presence_tier, :online, :latency_ms"
        )
        if with_status:
            cols += ", turn_status"
            vals += ", 'answered'"
        for col in turn_history.VOICE_MATCH_COLUMNS:
            if col in params:
                cols += f", {col}"
                vals += f", :{col}"
        await self.s.execute(
            text(
                f"""
                INSERT INTO conversation_log
                    (session_id, room_id, person_id, user_text,
                     assistant_text, matched_handler, matched_path,
                     {cols})
                VALUES
                    (:session_id, :room_id, :person_id, :user_text,
                     :assistant_text, :matched_handler, :matched_path,
                     {vals})
                """
            ),
            params,
        )


class PeopleRepository:
    #: The ``pg_advisory_xact_lock`` key every writer of a person's NAME
    #: takes first (voice enrolment, the People page's rename): the
    #: one-name-one-person rule is checked and the row written under it, so
    #: two enrolments of one new name at the same moment can't both see
    #: "nobody" and make two. Not a unique index: an install may already
    #: hold two people with one name (a "no" to "is that you?", from before
    #: the rule, or a parent
    #: and child), and they stay until an admin renames or removes one.
    NAME_LOCK_KEY = 0x646F6D6F6E616D65  # "domoname"

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def lock_names(self) -> None:
        """Hold :data:`NAME_LOCK_KEY` until this transaction ends."""
        await self.s.execute(
            text("SELECT pg_advisory_xact_lock(CAST(:k AS BIGINT))"),
            {"k": self.NAME_LOCK_KEY},
        )

    async def rename(self, person_id: int, name: str) -> str:
        """Call ``person_id`` ``name``, unless someone else is already
        called that (case and surrounding space aside): ``"renamed"``,
        ``"taken"`` or ``"missing"``. Under :meth:`lock_names`."""
        await self.lock_names()
        if await self.get(person_id) is None:
            return "missing"
        if any(pid != person_id for pid, _ in await self.find_by_name(name)):
            return "taken"
        await self.s.execute(
            text("UPDATE people SET name = :name WHERE id = :id"),
            {"id": person_id, "name": name},
        )
        return "renamed"

    async def create(self, *, name: str, notes: str | None = None) -> int:
        row = await self.s.execute(
            text(
                """
                INSERT INTO people (name, notes, last_seen_at)
                VALUES (:name, :notes, NOW())
                RETURNING id
                """
            ),
            {"name": name, "notes": notes},
        )
        return int(row.scalar_one())

    async def touch_last_seen(self, person_id: int) -> None:
        await self.s.execute(
            text("UPDATE people SET last_seen_at = NOW() WHERE id = :id"),
            {"id": person_id},
        )

    async def get(self, person_id: int) -> tuple[int, str, datetime | None] | None:
        row = await self.s.execute(
            text("SELECT id, name, last_seen_at FROM people WHERE id = :id"),
            {"id": person_id},
        )
        result = row.first()
        if result is None:
            return None
        return int(result[0]), result[1], result[2]

    async def names(self) -> list[tuple[int, str]]:
        """Every person's ``(id, name)``: what a heard name is compared with
        for sound-alikes (domovoi/person_names.py)."""
        rows = await self.s.execute(text("SELECT id, name FROM people ORDER BY id"))
        return [(int(r[0]), str(r[1])) for r in rows.all()]

    async def find_by_name(self, name: str) -> list[tuple[int, str]]:
        """Everyone called ``name`` (case and surrounding space aside), as
        ``(id, name)``, oldest row first — at most two, which is enough to
        tell "nobody", "this person" and "more than one" apart. A caller
        that finds more than one must not pick (a parent and child can
        share a name)."""
        rows = await self.s.execute(
            text(
                """
                SELECT id, name FROM people
                 WHERE lower(btrim(name)) = lower(btrim(:name))
                 ORDER BY id
                 LIMIT 2
                """
            ),
            {"name": name},
        )
        return [(int(r[0]), r[1]) for r in rows.all()]

    async def list_recently_seen(
        self, since: datetime
    ) -> list[tuple[int, datetime | None]]:
        """Return ``(id, last_extracted_at)`` for every person whose
        ``last_seen_at`` is at-or-after ``since``. Used by the implicit
        memory extractor to decide who's active enough to bother
        scanning — there's no point burning Ollama time on someone
        who hasn't spoken in months."""
        row = await self.s.execute(
            text(
                """
                SELECT id, last_extracted_at
                FROM people
                WHERE last_seen_at IS NOT NULL
                  AND last_seen_at >= :since
                """
            ),
            {"since": since},
        )
        return [(int(r[0]), r[1]) for r in row.all()]

    async def touch_last_extracted(self, person_id: int) -> None:
        """Stamp ``last_extracted_at = NOW()`` after a successful
        extraction pass (whether or not it produced any rows). Failed
        passes (Ollama down, JSON broke, etc.) deliberately do NOT
        stamp, so the next loop tick retries the same window instead
        of silently dropping it."""
        await self.s.execute(
            text(
                """
                UPDATE people SET last_extracted_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": person_id},
        )

    async def latest_seen(self) -> datetime | None:
        """Most recent `last_seen_at` across every known person.

        Drives the presence-tier ladder: if nobody known has spoken in
        ages, an unrecognized voice is far more likely to be a stranger
        and warrants a direct identity prompt.
        """
        row = await self.s.execute(text("SELECT MAX(last_seen_at) FROM people"))
        result = row.scalar()
        return result  # may be None when the people table is empty

    async def relearn_pending(self, model: str) -> int:
        """How many people lost their voice samples to the 1.3.0 speaker
        model change (V061, ``voice_relearn_since``) and have no sample of
        ``model`` since: they aren't recognized until they say "my name is
        …" again."""
        row = await self.s.execute(
            text(
                """
                SELECT COUNT(*) FROM people p
                 WHERE p.voice_relearn_since IS NOT NULL
                   AND NOT EXISTS (SELECT 1 FROM voice_profiles v
                                    WHERE v.person_id = p.id AND v.model = :model)
                """
            ),
            {"model": model},
        )
        return int(row.scalar_one())

    async def delete(self, person_id: int) -> int:
        """Cascades through voice_profiles via FK; intents_log.person_id
        becomes NULL via the FK's ON DELETE SET NULL. Forgetting a person
        deletes their turns FIRST (voice_history.delete_person_history):
        after this, they can no longer be found. Their faces become
        unnamed (``faces.unname_faces``), not kept as confirmed faces of
        nobody."""
        from domovoi.media_understanding.faces import unname_faces

        await unname_faces(self.s, person_id)
        result = await self.s.execute(
            text("DELETE FROM people WHERE id = :id"),
            {"id": person_id},
        )
        return result.rowcount or 0

    # ─── Preferences JSONB ─────────────────────────────────────
    # Scalar/atom prefs for the speaker. Anything list-shaped goes in
    # the favorites table; anything free-form goes in memories.

    async def get_preferences(self, person_id: int) -> dict:
        row = await self.s.execute(
            text("SELECT preferences FROM people WHERE id = :id"),
            {"id": person_id},
        )
        result = row.scalar()
        if result is None:
            return {}
        return dict(result) if result else {}

    async def set_preference(self, person_id: int, key: str, value) -> None:
        """Merge a single (key, value) into ``people.preferences``.

        Read-modify-write rather than ``jsonb_set`` because the values
        we store are heterogeneous (strings, bools, small ints) and
        jsonb_set's type discipline is awkward to thread through the
        asyncpg driver. At household scale (a few dozen people max,
        one or two writes per turn) the extra round-trip is invisible.
        """
        import json

        prefs = await self.get_preferences(person_id)
        prefs[key] = value
        await self.s.execute(
            text(
                """
                UPDATE people
                SET preferences = CAST(:prefs AS JSONB)
                WHERE id = :id
                """
            ),
            {"id": person_id, "prefs": json.dumps(prefs)},
        )

    async def delete_preference(self, person_id: int, key: str) -> bool:
        prefs = await self.get_preferences(person_id)
        if key not in prefs:
            return False
        prefs.pop(key, None)
        import json

        await self.s.execute(
            text(
                """
                UPDATE people
                SET preferences = CAST(:prefs AS JSONB)
                WHERE id = :id
                """
            ),
            {"id": person_id, "prefs": json.dumps(prefs)},
        )
        return True


class FavoritesRepository:
    """List-shaped favorites by (person_id, kind).

    Multiple values per (person, kind) supported — a person can have
    several favorite teams without a JSON-array read-modify-write
    dance. Handlers query by kind ("any favorite genres for this
    user? → suggest a playlist"); the web UI lists by person.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def add(
        self,
        *,
        person_id: int,
        kind: str,
        value: str,
        rank: int = 0,
    ) -> int:
        """Insert (or no-op if the (person, kind, value) already exists).

        Returns the row id either way. Idempotent so handlers don't
        have to special-case "user told me twice" — saying "my favorite
        team is the Mariners" a second time silently succeeds.
        """
        row = await self.s.execute(
            text(
                """
                INSERT INTO favorites (person_id, kind, value, rank)
                VALUES (:person_id, :kind, :value, :rank)
                ON CONFLICT (person_id, kind, value) DO UPDATE
                    SET rank = EXCLUDED.rank
                RETURNING id
                """
            ),
            {
                "person_id": person_id,
                "kind": kind.strip().lower(),
                "value": value.strip(),
                "rank": rank,
            },
        )
        return int(row.scalar_one())

    async def list_for_person(
        self, person_id: int, kind: str | None = None
    ) -> list[tuple[int, str, str, int]]:
        """Return ``(id, kind, value, rank)`` rows ordered by rank
        then created_at. ``kind=None`` returns every favorite."""
        if kind is None:
            row = await self.s.execute(
                text(
                    """
                    SELECT id, kind, value, rank
                    FROM favorites
                    WHERE person_id = :pid
                    ORDER BY kind, rank, created_at
                    """
                ),
                {"pid": person_id},
            )
        else:
            row = await self.s.execute(
                text(
                    """
                    SELECT id, kind, value, rank
                    FROM favorites
                    WHERE person_id = :pid AND kind = :kind
                    ORDER BY rank, created_at
                    """
                ),
                {"pid": person_id, "kind": kind.strip().lower()},
            )
        return [(int(r[0]), r[1], r[2], int(r[3])) for r in row.all()]

    async def delete(self, favorite_id: int, person_id: int) -> int:
        """Scoped to (id, person_id) so a web bug can't blow away
        another person's row by ID-guessing."""
        result = await self.s.execute(
            text(
                """
                DELETE FROM favorites
                WHERE id = :id AND person_id = :pid
                """
            ),
            {"id": favorite_id, "pid": person_id},
        )
        return result.rowcount or 0

    async def delete_by_value(
        self, person_id: int, kind: str, value: str
    ) -> int:
        result = await self.s.execute(
            text(
                """
                DELETE FROM favorites
                WHERE person_id = :pid
                  AND kind = :kind
                  AND lower(value) = lower(:value)
                """
            ),
            {
                "pid": person_id,
                "kind": kind.strip().lower(),
                "value": value.strip(),
            },
        )
        return result.rowcount or 0


class MemoriesRepository:
    """Free-form facts about a speaker.

    Three sources:
      * ``explicit`` — captured via voice ("remember that ___")
      * ``manual``   — captured via the web UI
      * ``implicit`` — surfaced by the (future) extractor worker;
                       lands as ``status='pending'`` so the next turn
                       can prompt for confirmation before going active.

    Only ``status='active'`` rows feed into the QA prompt prefix —
    pending and rejected memories are tracked for audit / dedup.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def add(
        self,
        *,
        person_id: int,
        body: str,
        source: str,
        status: str = "active",
        topic: str | None = None,
        confidence: float | None = None,
    ) -> int:
        row = await self.s.execute(
            text(
                """
                INSERT INTO memories
                    (person_id, body, topic, source, status, confidence)
                VALUES
                    (:person_id, :body, :topic, :source, :status, :confidence)
                RETURNING id
                """
            ),
            {
                "person_id": person_id,
                "body": body.strip(),
                "topic": topic,
                "source": source,
                "status": status,
                "confidence": confidence,
            },
        )
        return int(row.scalar_one())

    async def list_active(
        self, person_id: int, limit: int = 100
    ) -> list[tuple[int, str, str | None, str]]:
        """Return ``(id, body, topic, source)`` rows for the prompt-
        prefix injection. Excludes pending / rejected memories."""
        row = await self.s.execute(
            text(
                """
                SELECT id, body, topic, source
                FROM memories
                WHERE person_id = :pid AND status = 'active'
                ORDER BY created_at
                LIMIT :limit
                """
            ),
            {"pid": person_id, "limit": limit},
        )
        return [(int(r[0]), r[1], r[2], r[3]) for r in row.all()]

    async def list_all(
        self, person_id: int, limit: int = 200
    ) -> list[tuple[int, str, str | None, str, str, datetime]]:
        """``(id, body, topic, source, status, created_at)`` — every
        row regardless of status; powers the web UI audit view."""
        row = await self.s.execute(
            text(
                """
                SELECT id, body, topic, source, status, created_at
                FROM memories
                WHERE person_id = :pid
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"pid": person_id, "limit": limit},
        )
        return [
            (int(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in row.all()
        ]

    async def set_status(
        self, memory_id: int, person_id: int, status: str
    ) -> int:
        """Scoped to person_id so cross-person ID guessing can't flip
        states. Returns rowcount (0 = not found / wrong owner)."""
        result = await self.s.execute(
            text(
                """
                UPDATE memories
                SET status = :status
                WHERE id = :id AND person_id = :pid
                """
            ),
            {"id": memory_id, "pid": person_id, "status": status},
        )
        return result.rowcount or 0

    async def touch_used(self, memory_ids: list[int]) -> None:
        """Stamp ``last_used_at = NOW()`` on each row that ended up in
        the prompt prefix. Drives later relevance-ranking once we
        cross the prefix-budget threshold."""
        if not memory_ids:
            return
        await self.s.execute(
            text(
                """
                UPDATE memories
                SET last_used_at = NOW()
                WHERE id = ANY(CAST(:ids AS INT[]))
                """
            ),
            {"ids": memory_ids},
        )

    async def delete(self, memory_id: int, person_id: int) -> int:
        result = await self.s.execute(
            text(
                """
                DELETE FROM memories
                WHERE id = :id AND person_id = :pid
                """
            ),
            {"id": memory_id, "pid": person_id},
        )
        return result.rowcount or 0

    async def next_pending_for_offer(
        self,
        person_id: int,
        offer_cooldown_sec: int,
    ) -> tuple[int, str, str | None] | None:
        """Pick the oldest pending memory eligible to surface.

        Eligibility: ``status='pending'`` AND
        ``last_offered_at IS NULL OR last_offered_at < NOW() -
        cooldown``. Returns ``(id, body, topic)`` or None.

        Ordered by created_at so memories the extractor surfaced first
        get asked about first — keeps the conversational ordering
        sensible when several have piled up across a long session.
        """
        row = await self.s.execute(
            text(
                """
                SELECT id, body, topic
                FROM memories
                WHERE person_id = :pid
                  AND status = 'pending'
                  AND (last_offered_at IS NULL
                       OR last_offered_at < NOW() - make_interval(secs => :cooldown))
                ORDER BY created_at
                LIMIT 1
                """
            ),
            {"pid": person_id, "cooldown": offer_cooldown_sec},
        )
        result = row.first()
        if result is None:
            return None
        return int(result[0]), result[1], result[2]

    async def mark_offered(self, memory_id: int) -> None:
        await self.s.execute(
            text(
                """
                UPDATE memories
                SET last_offered_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": memory_id},
        )

    async def count_new_turns_since(
        self, person_id: int, since: datetime | None
    ) -> int:
        """Count ``conversation_log`` rows attributed to ``person_id``
        with ``at > since`` (or all rows if ``since`` is None — first
        extraction pass for this person). Drives the threshold check
        in the extractor worker."""
        if since is None:
            row = await self.s.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM conversation_log
                    WHERE person_id = :pid
                    """
                ),
                {"pid": person_id},
            )
        else:
            row = await self.s.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM conversation_log
                    WHERE person_id = :pid AND at > :since
                    """
                ),
                {"pid": person_id, "since": since},
            )
        return int(row.scalar() or 0)

    async def recent_turns_for_extraction(
        self, person_id: int, limit: int
    ) -> list[tuple[str | None, str | None]]:
        """Return up to ``limit`` recent (user_text, assistant_text)
        pairs for the implicit extractor's prompt. Newest first so
        the truncation drops oldest first; the worker reverses the
        list before formatting so the model reads chronologically."""
        row = await self.s.execute(
            text(
                """
                SELECT user_text,
                       -- Answers that read out words from outside the
                       -- house (a friend house's messages or list items)
                       -- never feed the memory extractor (R6 FH-15).
                       CASE WHEN matched_handler IN ('friend_houses', 'lists')
                            THEN NULL ELSE assistant_text END
                FROM conversation_log
                WHERE person_id = :pid
                ORDER BY at DESC
                LIMIT :limit
                """
            ),
            {"pid": person_id, "limit": limit},
        )
        return [(r[0], r[1]) for r in row.all()]

    async def has_similar_active_or_pending(
        self, person_id: int, fragment: str
    ) -> bool:
        """Cheap dedup gate before inserting a pending memory.

        Substring ILIKE comparison both directions — catches the
        common "I have a dog named Max" vs "user has a dog named
        Max" type duplicates without bringing in pg_trgm. Misses
        semantic dupes ("loves jazz" vs "is a jazz fan") — fine; the
        user will see them in the offer flow and can reject."""
        if not fragment.strip():
            return False
        row = await self.s.execute(
            text(
                """
                SELECT 1
                FROM memories
                WHERE person_id = :pid
                  AND status IN ('active', 'pending')
                  AND (
                       body ILIKE :pat
                       OR :fragment ILIKE '%' || body || '%'
                  )
                LIMIT 1
                """
            ),
            {
                "pid": person_id,
                "pat": f"%{fragment.strip()}%",
                "fragment": fragment.strip(),
            },
        )
        return row.first() is not None

    async def find_fuzzy(
        self, person_id: int, fragment: str, limit: int = 5
    ) -> list[tuple[int, str]]:
        """Substring search for the voice "forget that ___" path.

        Case-insensitive ILIKE — at household scale (typically a few
        dozen memories per person max) a full-table ILIKE scan is
        cheap; pg_trgm is overkill here. If memory counts ever climb
        into the thousands per person, swap in trgm similarity
        scoring without changing the call site."""
        if not fragment.strip():
            return []
        row = await self.s.execute(
            text(
                """
                SELECT id, body
                FROM memories
                WHERE person_id = :pid
                  AND status = 'active'
                  AND body ILIKE :pat
                ORDER BY length(body), id
                LIMIT :limit
                """
            ),
            {
                "pid": person_id,
                "pat": f"%{fragment.strip()}%",
                "limit": limit,
            },
        )
        return [(int(r[0]), r[1]) for r in row.all()]


@dataclass(frozen=True)
class VoiceSample:
    """One ``voice_profiles`` row as "forget me" and room learning read it
    (:meth:`VoiceProfilesRepository.person_samples`).

    ``claimed``: added by a name claim. ``claim_of``: learned from a voice
    recognized only through the claim with that id. Either way not the
    person's own voice (:attr:`own`)."""

    id: int
    embedding: bytes
    source: str
    room_id: str | None
    claimed: bool
    claim_of: int | None

    @property
    def own(self) -> bool:
        return not self.claimed and self.claim_of is None

    @property
    def claim_root(self) -> int | None:
        """The claim this sample stands on: itself, or the one it was
        learned through; None for the person's own voice."""
        if self.claimed:
            return self.id
        return self.claim_of


class VoiceProfilesRepository:
    """All speaker embeddings live here. A person can have several rows
    (enrollment, plus at most one learned sample per room). The
    identifier scores a person against the centroid of their rows —
    see ``voice_identifier._person_scores``.

    Two sets per person (V026, ``source``): ``satellite`` rows, heard by
    a room's satellite, and ``phone`` rows, said into the app's chat mic.
    :meth:`all_for_model` is the satellites' matching set and holds no
    phone row; a phone turn reads both (:meth:`phone_matching_rows`).

    Claimed samples (V029, delta review S1): a row added because somebody
    said a known person's name and "yes" carries ``claimed_at`` /
    ``claim_room`` / ``claimed_by_person_id``; a row a satellite learned
    from a voice recognized only through such a row carries ``claim_of``
    (that claim's id, and goes with it). Everything else is the person's
    own voice. :meth:`person_samples` reads them for "forget me".
    """

    #: The speaker model every sample is made with (1.3.0: 3D-Speaker's
    #: ERes2Net, domovoi/clients/speaker_encoder.py). Rows with another tag
    #: can't be compared with its embeddings and are never read (V061
    #: removed Resemblyzer's).
    EMBEDDING_MODEL_TAG = MODEL_TAG
    SATELLITE = "satellite"
    PHONE = "phone"

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def add(
        self,
        *,
        person_id: int,
        embedding: bytes,
        model: str,
        room_id: str | None,
        sample_seconds: float | None,
        source: str = SATELLITE,
        claimed: bool = False,
        claim_room: str | None = None,
        claimed_by: int | None = None,
        claim_of: int | None = None,
        satellite_id: str | None = None,
        learned: bool = False,
    ) -> int:
        """One sample. ``satellite_id``: the mic that heard it (V073;
        default: the device whose id is ``room_id``, as every device's
        was until rooms held several). ``claimed``: it is being added because the speaker
        said this person's name and "yes" (V029), in ``claim_room``, by a
        voice recognized as ``claimed_by`` (None: an unknown voice).
        ``claim_of``: a learned sample that rests on that claim.
        ``learned``: saved by the identifier on its own after confident
        matches (job 4), not enrolled on purpose (V076); a device moved to
        another room drops its learned samples."""
        row = await self.s.execute(
            text(
                """
                INSERT INTO voice_profiles
                    (person_id, embedding, model, room_id, satellite_id, sample_seconds,
                     source, claimed_at, claim_room, claimed_by_person_id, claim_of,
                     learned)
                VALUES
                    (:person_id, :embedding, :model, :room_id, :satellite_id,
                     :sample_seconds, :source,
                     CASE WHEN :claimed THEN NOW() END, :claim_room,
                     (SELECT id FROM people WHERE id = :claimed_by), :claim_of,
                     :learned)
                RETURNING id
                """
            ),
            {
                "person_id": person_id,
                "embedding": embedding,
                "model": model,
                "room_id": room_id,
                "satellite_id": satellite_id
                or (room_id if source == self.SATELLITE else None),
                "sample_seconds": sample_seconds,
                "source": source,
                "claimed": bool(claimed),
                "claim_room": claim_room if claimed else None,
                "claimed_by": claimed_by if claimed else None,
                "claim_of": claim_of,
                "learned": bool(learned),
            },
        )
        return int(row.scalar_one())

    async def drop_learned_by(self, satellite_id: str) -> int:
        """Delete the samples a device's mic learned on its own (V076),
        when it moves to another room: they carry the old room's sound.
        Enrolled samples stay. Returns how many went."""
        result = await self.s.execute(
            text("DELETE FROM voice_profiles WHERE satellite_id = :s AND learned"),
            {"s": satellite_id},
        )
        return int(result.rowcount or 0)

    async def add_phone_sample(
        self,
        *,
        person_id: int,
        embedding: bytes,
        model: str,
        room_id: str | None,
        cap: int,
        claimed: bool = False,
        claimed_by: int | None = None,
    ) -> tuple[int, int]:
        """Add one of ``person_id``'s phone samples, keeping at most
        ``cap`` of them: the new one, then the person's own (unclaimed)
        samples, then claimed ones, newest first in each, so another
        phone's claims can't push the person's own phone voice out (V029).
        Returns (the new row's id, how many old phone samples were
        dropped). Their satellite samples are never touched."""
        new_id = await self.add(
            person_id=person_id, embedding=embedding, model=model,
            room_id=room_id, sample_seconds=None, source=self.PHONE,
            claimed=claimed, claim_room=room_id, claimed_by=claimed_by,
        )
        dropped = await self.s.execute(
            text(
                """
                DELETE FROM voice_profiles
                 WHERE person_id = :pid AND source = 'phone'
                   AND id NOT IN (
                       SELECT id FROM voice_profiles
                        WHERE person_id = :pid AND source = 'phone'
                        ORDER BY (id = :new_id) DESC,
                                 (claimed_at IS NULL AND claim_of IS NULL) DESC,
                                 enrolled_at DESC, id DESC
                        LIMIT :keep
                   )
                """
            ),
            {"pid": person_id, "keep": max(1, int(cap)), "new_id": new_id},
        )
        return new_id, int(dropped.rowcount or 0)

    async def person_samples(self, person_id: int, model: str) -> list["VoiceSample"]:
        """Every sample of ``person_id`` at ``model``, both sets, with its
        claim marks (V029), oldest first."""
        result = await self.s.execute(
            text(
                """
                SELECT id, embedding, source, room_id, claimed_at, claim_of
                  FROM voice_profiles
                 WHERE person_id = :pid AND model = :model
                 ORDER BY id
                """
            ),
            {"pid": person_id, "model": model},
        )
        return [
            VoiceSample(
                id=int(r[0]), embedding=bytes(r[1]), source=str(r[2]), room_id=r[3],
                claimed=r[4] is not None, claim_of=None if r[5] is None else int(r[5]),
            )
            for r in result.all()
        ]

    async def replace_embedding(
        self, sample_id: int, person_id: int, embedding: bytes, sample_seconds: float | None,
    ) -> bool:
        """Put a new embedding in one of ``person_id``'s samples (the
        introduction's, as it hears more of the voice: handlers/
        voice_profile.py). False when the sample is gone (the person was
        forgotten since)."""
        result = await self.s.execute(
            text(
                """
                UPDATE voice_profiles
                   SET embedding = :embedding, sample_seconds = :secs
                 WHERE id = :id AND person_id = :pid
                """
            ),
            {"id": int(sample_id), "pid": int(person_id), "embedding": embedding,
             "secs": sample_seconds},
        )
        return bool(result.rowcount)

    async def room_samples(
        self, person_id: int, room_id: str, model: str,
    ) -> list[tuple[int, float | None]]:
        """``(id, sample_seconds)`` of ``person_id``'s SATELLITE samples
        from ``room_id``, oldest first."""
        rows = await self.s.execute(
            text(
                """
                SELECT id, sample_seconds FROM voice_profiles
                 WHERE person_id = :pid AND room_id = :room AND model = :model
                   AND source = 'satellite'
                 ORDER BY id
                """
            ),
            {"pid": int(person_id), "room": room_id, "model": model},
        )
        return [(int(r[0]), None if r[1] is None else float(r[1])) for r in rows.all()]

    async def room_sample_count(self, person_id: int, room_id: str, model: str) -> int:
        """How many SATELLITE samples ``person_id`` has from ``room_id``."""
        row = await self.s.execute(
            text(
                """
                SELECT COUNT(*) FROM voice_profiles
                 WHERE person_id = :pid AND room_id = :room AND model = :model
                   AND source = 'satellite'
                """
            ),
            {"pid": int(person_id), "room": room_id, "model": model},
        )
        return int(row.scalar_one())

    async def delete_samples(self, person_id: int, ids: list[int]) -> int:
        """Delete these samples of ``person_id`` (and, by V029's cascade,
        anything learned from them). Returns how many rows the statement
        named directly."""
        if not ids:
            return 0
        result = await self.s.execute(
            text("DELETE FROM voice_profiles WHERE person_id = :pid AND id = ANY(:ids)"),
            {"pid": person_id, "ids": [int(i) for i in ids]},
        )
        return int(result.rowcount or 0)

    async def all_for_model(
        self, model: str
    ) -> list[tuple[int, int, bytes, str | None, float | None]]:
        """Return ``(profile_id, person_id, embedding_bytes, room_id,
        sample_seconds)`` for every SATELLITE profile matching ``model`` —
        what a satellite's turn is matched against. Phone samples are never
        in it, so they cannot move a satellite centroid. ``sample_seconds``
        (the speech the sample holds, None when unrecorded) decides whether
        a room's samples are long enough to hold a person to them
        (``voice_identifier.ROOM_SAMPLE_GOOD_SEC``).

        Pulls every row on every match call. At household scale (a few
        dozen samples max) this is microseconds; if it ever becomes hot
        we can move the cosine sim into a postgres extension or cache.
        """
        result = await self.s.execute(
            text(
                """
                SELECT id, person_id, embedding, room_id, sample_seconds
                FROM voice_profiles
                WHERE model = :model AND source = 'satellite'
                """
            ),
            {"model": model},
        )
        return [
            (int(r[0]), int(r[1]), bytes(r[2]), r[3], None if r[4] is None else float(r[4]))
            for r in result.all()
        ]

    async def phone_matching_rows(
        self, model: str
    ) -> list[tuple[int, int, bytes, str | None, str]]:
        """Every profile matching ``model``, both sets, as
        ``(profile_id, person_id, embedding_bytes, room_id, source)`` — a
        phone turn picks per person which set to score
        (``voice_identifier.phone_matching_samples``)."""
        result = await self.s.execute(
            text(
                """
                SELECT id, person_id, embedding, room_id, source
                FROM voice_profiles
                WHERE model = :model
                """
            ),
            {"model": model},
        )
        return [
            (int(r[0]), int(r[1]), bytes(r[2]), r[3], str(r[4])) for r in result.all()
        ]


class VoiceDenylistRepository:
    """Persistent voice opt-out. Embeddings here belong to people
    who explicitly told us not to save their voice. The identifier
    consults this list on every match attempt and suppresses the
    identification result when an embedding falls within match
    threshold of any denylist row, so no downstream prompt fires.

    Schema-wise this is intentionally NOT a relation to ``people`` —
    a denylisted user has explicitly opted out of being a person row.
    """

    EMBEDDING_MODEL_TAG = MODEL_TAG

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def add(
        self,
        *,
        embedding: bytes,
        model: str,
        notes: str | None = None,
    ) -> int:
        row = await self.s.execute(
            text(
                """
                INSERT INTO voice_denylist (embedding, model, notes)
                VALUES (:embedding, :model, :notes)
                RETURNING id
                """
            ),
            {"embedding": embedding, "model": model, "notes": notes},
        )
        return int(row.scalar_one())

    async def all_for_model(self, model: str) -> list[tuple[int, bytes]]:
        """Return ``(id, embedding_bytes)`` for every denylist row at
        the given model tag. Same flat-list pattern as
        VoiceProfilesRepository.all_for_model — household-scale, fast."""
        result = await self.s.execute(
            text(
                """
                SELECT id, embedding
                FROM voice_denylist
                WHERE model = :model
                """
            ),
            {"model": model},
        )
        return [(int(r[0]), bytes(r[1])) for r in result.all()]


def trim_recent_turns(recent: list[dict], cap: int) -> list[dict]:
    """``recent`` (oldest first) once it has passed ``cap`` entries: only
    its newer half, in whole exchanges — never less than the last one.

    The history is the middle of the Q&A model's prompt, and Ollama reuses
    a prompt's cached start only up to the first token that changed. Sliding
    the window by one exchange a turn changed the very first history entry
    every turn once a room's session was full, so the whole history was
    read again each time (~380 tokens, ~1.4 s on a CPU host). Cut in halves,
    the history only grows between cuts and the cached start holds for
    about ``cap / 4`` exchanges at a time. So a session keeps between half
    the cap and the cap."""
    if len(recent) <= cap:
        return recent
    keep = max(2, (cap // 2) // 2 * 2)
    return recent[-keep:]


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get_or_create(self, session_id: UUID | None, room_id: str | None) -> UUID:
        if session_id is not None:
            existing = await self.s.execute(
                text("SELECT id FROM sessions WHERE id = :id"),
                {"id": str(session_id)},
            )
            if existing.first() is not None:
                await self.s.execute(
                    text("UPDATE sessions SET last_activity = NOW() WHERE id = :id"),
                    {"id": str(session_id)},
                )
                return session_id

        new_id = uuid4()
        await self.s.execute(
            text(
                """
                INSERT INTO sessions (id, room_id)
                VALUES (:id, :room_id)
                """
            ),
            {"id": str(new_id), "room_id": room_id},
        )
        return new_id

    async def get_context(self, session_id: UUID) -> dict:
        row = await self.s.execute(
            text("SELECT context FROM sessions WHERE id = :id"),
            {"id": str(session_id)},
        )
        result = row.first()
        if result is None:
            return {}
        return dict(result[0]) if result[0] else {}

    async def record_exchange(
        self,
        session_id: UUID,
        user_text: str,
        assistant_text: str,
        recent_turns_cap: int,
        *,
        last_response: str | None = None,
    ) -> None:
        """Append user + assistant turns and update last_assistant_response.

        ``last_response``: what was actually said, when the history keeps a
        stand-in for it (Response.history_text); "say that again" repeats it.

        Past ``recent_turns_cap`` entries the OLDER HALF goes in one cut
        (see ``trim_recent_turns``), not one exchange per turn.

        Read-modify-write on `sessions.context` JSONB. At our QPS this is fine;
        if it ever becomes hot, switch to Postgres jsonb_set/jsonb_array_append.
        """
        from datetime import datetime, timezone
        import json

        now = datetime.now(tz=timezone.utc).isoformat()
        ctx = await self.get_context(session_id)
        recent = list(ctx.get("recent_turns") or [])
        recent.append({"role": "user", "text": user_text, "at": now})
        recent.append({"role": "assistant", "text": assistant_text, "at": now})
        recent = trim_recent_turns(recent, recent_turns_cap)

        ctx["recent_turns"] = recent
        ctx["last_assistant_response"] = last_response if last_response is not None else assistant_text
        # A reply the history keeps a stand-in for carries text that must not
        # reach a model (a friend house's words): "double check that" asks.
        ctx["last_response_kept_out"] = last_response is not None

        await self.s.execute(
            text(
                """
                UPDATE sessions
                SET context = CAST(:ctx AS JSONB), last_activity = NOW()
                WHERE id = :id
                """
            ),
            {"id": str(session_id), "ctx": json.dumps(ctx)},
        )

    async def set_context_key(self, session_id: UUID, key: str, value) -> None:
        """Merge a single key into `sessions.context`. Used for e.g. last_played_track."""
        import json

        ctx = await self.get_context(session_id)
        ctx[key] = value
        await self.s.execute(
            text(
                """
                UPDATE sessions
                SET context = CAST(:ctx AS JSONB), last_activity = NOW()
                WHERE id = :id
                """
            ),
            {"id": str(session_id), "ctx": json.dumps(ctx)},
        )


class VoiceNotesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def add(self, *, room_id: str | None, body: str) -> int:
        row = await self.s.execute(
            text(
                """
                INSERT INTO voice_notes (room_id, text)
                VALUES (:room_id, :body)
                RETURNING id
                """
            ),
            {"room_id": room_id, "body": body},
        )
        return int(row.scalar_one())

    async def added_within(
        self, *, since: datetime, limit: int = 20
    ) -> list[tuple[int, str | None, str, datetime]]:
        """Return notes (id, room_id, body, created_at) added at-or-after ``since``."""
        result = await self.s.execute(
            text(
                """
                SELECT id, room_id, text, created_at
                FROM voice_notes
                WHERE created_at >= :since
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"since": since, "limit": limit},
        )
        return [(int(r[0]), r[1], r[2], r[3]) for r in result.all()]

    async def latest(self) -> tuple[int, str | None, str, datetime] | None:
        result = await self.s.execute(
            text(
                """
                SELECT id, room_id, text, created_at
                FROM voice_notes
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
        )
        row = result.first()
        if row is None:
            return None
        return int(row[0]), row[1], row[2], row[3]


class WebSearchPrefsRepository:
    """Per-speaker prefs for the proactive web-search offer.

    A row exists for a (person_id, category) once the speaker has
    answered yes or no to at least one offer in that category.
    Anonymous speakers (``person_id IS NULL``) never get a row — the
    caller short-circuits and we offer every time.

    Two pieces of state:
      * ``auto_search`` — once true, future questions in that category
        skip the offer entirely and route straight to the SearxNG
        synth path.
      * ``yes_count`` / ``no_count`` — counters that drive the meta-
        offer ("want me to always check these?") after a threshold of
        yeses. ``prefs_offered_at`` is stamped once we ask, so we
        don't pester the user every turn after the threshold is hit.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(
        self, person_id: int, category: str
    ) -> tuple[bool, int, int, datetime | None] | None:
        """Return ``(auto_search, yes_count, no_count, prefs_offered_at)``
        or None if no row exists for this (person, category)."""
        row = await self.s.execute(
            text(
                """
                SELECT auto_search, yes_count, no_count, prefs_offered_at
                FROM web_search_prefs
                WHERE person_id = :person_id AND category = :category
                """
            ),
            {"person_id": person_id, "category": category},
        )
        result = row.first()
        if result is None:
            return None
        return bool(result[0]), int(result[1]), int(result[2]), result[3]

    async def is_auto(self, person_id: int, category: str) -> bool:
        row = await self.s.execute(
            text(
                """
                SELECT auto_search FROM web_search_prefs
                WHERE person_id = :person_id AND category = :category
                """
            ),
            {"person_id": person_id, "category": category},
        )
        result = row.scalar()
        return bool(result) if result is not None else False

    async def record_offer_response(
        self, person_id: int, category: str, affirmative: bool
    ) -> tuple[int, int]:
        """Upsert + bump the matching counter. Returns the new
        ``(yes_count, no_count)`` so the caller can decide whether to
        surface the auto-search meta-offer."""
        column = "yes_count" if affirmative else "no_count"
        row = await self.s.execute(
            text(
                f"""
                INSERT INTO web_search_prefs (person_id, category, {column})
                VALUES (:person_id, :category, 1)
                ON CONFLICT (person_id, category) DO UPDATE
                SET {column} = web_search_prefs.{column} + 1,
                    updated_at = NOW()
                RETURNING yes_count, no_count
                """
            ),
            {"person_id": person_id, "category": category},
        )
        result = row.first()
        if result is None:
            return (0, 0)
        return int(result[0]), int(result[1])

    async def mark_prefs_offered(self, person_id: int, category: str) -> None:
        await self.s.execute(
            text(
                """
                INSERT INTO web_search_prefs (person_id, category, prefs_offered_at)
                VALUES (:person_id, :category, NOW())
                ON CONFLICT (person_id, category) DO UPDATE
                SET prefs_offered_at = NOW(),
                    updated_at = NOW()
                """
            ),
            {"person_id": person_id, "category": category},
        )

    async def set_auto(
        self, person_id: int, category: str, auto_search: bool
    ) -> None:
        await self.s.execute(
            text(
                """
                INSERT INTO web_search_prefs (person_id, category, auto_search)
                VALUES (:person_id, :category, :auto_search)
                ON CONFLICT (person_id, category) DO UPDATE
                SET auto_search = :auto_search,
                    updated_at = NOW()
                """
            ),
            {
                "person_id": person_id,
                "category": category,
                "auto_search": auto_search,
            },
        )


class ConnectivityEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def log_transition(self, *, connectivity_state: str, target: str) -> None:
        await self.s.execute(
            text(
                """
                INSERT INTO connectivity_events (connectivity_state, target)
                VALUES (:state, :target)
                """
            ),
            {"state": connectivity_state, "target": target},
        )


class ClientGreetingsRepository:
    """Wake-word greetings the satellites play (table client_greetings).
    ``all_enabled`` feeds the canned-sound renderer; the rest back
    the web dashboard's Greetings management page."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def all_enabled(self) -> list[tuple[str, str]]:
        """``(text, category)`` for every enabled greeting — what the
        core renders to MP3."""
        row = await self.s.execute(
            text(
                """
                SELECT text, category
                FROM client_greetings
                WHERE enabled
                ORDER BY category, id
                """
            )
        )
        return [(r[0], r[1]) for r in row.all()]

    async def list_all(self) -> list[tuple[int, str, str, bool]]:
        """``(id, text, category, enabled)`` for the management UI."""
        row = await self.s.execute(
            text(
                """
                SELECT id, text, category, enabled
                FROM client_greetings
                ORDER BY category, id
                """
            )
        )
        return [(int(r[0]), r[1], r[2], bool(r[3])) for r in row.all()]

    async def create(self, *, greeting_text: str, category: str, enabled: bool = True) -> int:
        row = await self.s.execute(
            text(
                """
                INSERT INTO client_greetings (text, category, enabled)
                VALUES (:text, :category, :enabled)
                RETURNING id
                """
            ),
            {"text": greeting_text, "category": category, "enabled": enabled},
        )
        return int(row.scalar_one())

    async def update(
        self,
        greeting_id: int,
        *,
        greeting_text: str | None = None,
        category: str | None = None,
        enabled: bool | None = None,
    ) -> bool:
        """Patch only the provided fields. Returns True if a row changed."""
        sets: list[str] = []
        params: dict[str, object] = {"id": greeting_id}
        if greeting_text is not None:
            sets.append("text = :text")
            params["text"] = greeting_text
        if category is not None:
            sets.append("category = :category")
            params["category"] = category
        if enabled is not None:
            sets.append("enabled = :enabled")
            params["enabled"] = enabled
        if not sets:
            return False
        row = await self.s.execute(
            text(f"UPDATE client_greetings SET {', '.join(sets)} WHERE id = :id"),
            params,
        )
        return bool(row.rowcount)

    async def delete(self, greeting_id: int) -> bool:
        row = await self.s.execute(
            text("DELETE FROM client_greetings WHERE id = :id"),
            {"id": greeting_id},
        )
        return bool(row.rowcount)


class VoicesRepository:
    """The voice registry (table ``voices``). Each row is a
    user-selectable TTS voice: a spoken ``name``, the ``engine`` that
    renders it (``piper``; the Microsoft Edge rows of older releases are
    removed at boot, domovoi/voice_catalog.py), and a ``model_ref``
    locating the model (an uploaded ``.onnx`` path or a bare HF name).
    Exactly one row is ``is_default`` — the voice
    a satellite uses until it reports its own selection.

    Backs the streaming voice resolver, the per-voice clip renderer, the
    VoiceHandler, and the web Voices page."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    @staticmethod
    def _row(r) -> dict:
        return {
            "id": int(r[0]),
            "name": r[1],
            "engine": r[2],
            "model_ref": r[3],
            "is_default": bool(r[4]),
        }

    async def all(self) -> list[dict]:
        """Every registered voice, default first then alphabetical."""
        row = await self.s.execute(
            text(
                """
                SELECT id, name, engine, model_ref, is_default
                FROM voices
                ORDER BY is_default DESC, lower(name)
                """
            )
        )
        return [self._row(r) for r in row.all()]

    async def get_by_name(self, name: str) -> dict | None:
        """Case-insensitive name lookup — what people say rarely matches
        the stored casing exactly."""
        row = await self.s.execute(
            text(
                """
                SELECT id, name, engine, model_ref, is_default
                FROM voices
                WHERE lower(name) = lower(:name)
                """
            ),
            {"name": name},
        )
        r = row.first()
        return self._row(r) if r else None

    async def get_default(self) -> dict | None:
        row = await self.s.execute(
            text(
                """
                SELECT id, name, engine, model_ref, is_default
                FROM voices
                WHERE is_default
                """
            )
        )
        r = row.first()
        return self._row(r) if r else None

    async def count(self) -> int:
        row = await self.s.execute(text("SELECT COUNT(*) FROM voices"))
        return int(row.scalar_one())

    async def create(
        self,
        *,
        name: str,
        engine: str,
        model_ref: str,
        is_default: bool = False,
    ) -> int:
        if is_default:
            await self.s.execute(text("UPDATE voices SET is_default = FALSE WHERE is_default"))
        row = await self.s.execute(
            text(
                """
                INSERT INTO voices (name, engine, model_ref, is_default)
                VALUES (:name, :engine, :model_ref, :is_default)
                RETURNING id
                """
            ),
            {"name": name, "engine": engine, "model_ref": model_ref, "is_default": is_default},
        )
        return int(row.scalar_one())

    async def set_default(self, voice_id: int) -> bool:
        """Make ``voice_id`` the sole default. Clears the flag everywhere
        first so the partial unique index never trips."""
        exists = await self.s.execute(
            text("SELECT 1 FROM voices WHERE id = :id"), {"id": voice_id}
        )
        if exists.first() is None:
            return False
        await self.s.execute(text("UPDATE voices SET is_default = FALSE WHERE is_default"))
        await self.s.execute(
            text(
                "UPDATE voices SET is_default = TRUE, updated_at = NOW() WHERE id = :id"
            ),
            {"id": voice_id},
        )
        return True

    async def clear_default(self) -> None:
        """Leave no default (the caller sets another one in the same
        transaction): what lets :meth:`delete` take out a former default."""
        await self.s.execute(text("UPDATE voices SET is_default = FALSE WHERE is_default"))

    async def update(
        self,
        voice_id: int,
        *,
        name: str | None = None,
        engine: str | None = None,
        model_ref: str | None = None,
    ) -> bool:
        """Patch only the provided fields (default is set via
        :meth:`set_default`). Returns True if a row changed."""
        sets: list[str] = ["updated_at = NOW()"]
        params: dict[str, object] = {"id": voice_id}
        if name is not None:
            sets.append("name = :name")
            params["name"] = name
        if engine is not None:
            sets.append("engine = :engine")
            params["engine"] = engine
        if model_ref is not None:
            sets.append("model_ref = :model_ref")
            params["model_ref"] = model_ref
        if len(sets) == 1:
            return False
        row = await self.s.execute(
            text(f"UPDATE voices SET {', '.join(sets)} WHERE id = :id"),
            params,
        )
        return bool(row.rowcount)

    async def delete(self, voice_id: int) -> dict | None:
        """Delete and return the removed row (so the caller can clean up an
        uploaded ``.onnx``). Returns None if nothing was deleted. Refuses to
        delete the default voice — callers should set a new default first."""
        row = await self.s.execute(
            text(
                """
                DELETE FROM voices
                WHERE id = :id AND NOT is_default
                RETURNING id, name, engine, model_ref, is_default
                """
            ),
            {"id": voice_id},
        )
        r = row.first()
        return self._row(r) if r else None


class PronunciationsRepository:
    """The owner's changes to the "say it like" list (table
    ``pronunciations``, V027): one row per word, keyed by the converter's
    normalized form. ``say_like`` None marks a shipped default the owner
    removed. ``pronunciations_changed`` holds when the list last changed
    (:meth:`touch` after every change, deletions included). The effective
    list (defaults + these rows) is built by :mod:`domovoi.voice_settings`."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def all(self) -> list[dict]:
        row = await self.s.execute(
            text("SELECT key, word, say_like, updated_at FROM pronunciations ORDER BY lower(word), key")
        )
        return [
            {"key": r[0], "word": r[1], "say_like": r[2], "updated_at": r[3]} for r in row.all()
        ]

    async def put(self, *, key: str, word: str, say_like: str | None) -> None:
        """Insert or replace the row for ``key``; None removes the word
        from the effective list (a default) or keeps it out."""
        await self.s.execute(
            text(
                """
                INSERT INTO pronunciations (key, word, say_like, updated_at)
                VALUES (:key, :word, :say_like, NOW())
                ON CONFLICT (key) DO UPDATE
                SET word = EXCLUDED.word, say_like = EXCLUDED.say_like, updated_at = NOW()
                """
            ),
            {"key": key, "word": word, "say_like": say_like},
        )

    async def delete(self, keys) -> int:
        """Delete the rows of ``keys``; how many went."""
        keys = list(keys)
        if not keys:
            return 0
        row = await self.s.execute(
            text("DELETE FROM pronunciations WHERE key = ANY(:keys)"), {"keys": keys}
        )
        return int(row.rowcount or 0)

    async def count(self) -> int:
        row = await self.s.execute(text("SELECT COUNT(*) FROM pronunciations"))
        return int(row.scalar_one())

    async def touch(self) -> None:
        """Record that the list changed, now."""
        await self.s.execute(
            text(
                """
                INSERT INTO pronunciations_changed (id, changed_at) VALUES (1, NOW())
                ON CONFLICT (id) DO UPDATE SET changed_at = NOW()
                """
            )
        )

    async def last_change(self):
        row = await self.s.execute(text("SELECT changed_at FROM pronunciations_changed WHERE id = 1"))
        return row.scalar()


class WakeWordsRepository:
    """The custom wake-word registry (table ``wake_words``). Each row
    is a household-trained openWakeWord model: a user-facing ``name``, the
    load-bearing ``slug`` (= ``voice_slug(name)`` — the served
    ``<slug>.onnx``, the Pi's effective wake word, the openWakeWord
    prediction-dict key, and the model file stem all equal it), the spoken
    ``phrase`` the model detects, a detection ``threshold``, and a
    ``model_ref`` locating the trained ``.onnx``.

    A row moves through ``status`` ``recording`` → ``training`` →
    ``ready`` | ``failed`` as positive clips are captured on a satellite,
    handed to the external trainer, and a model lands (or errors). Exactly
    one row may be ``is_default``.

    Backs the wake-recording streaming path, the trainer worker, the
    /v1/wake-models channel, and the web Wake Words page."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    @staticmethod
    def _row(r) -> dict:
        return {
            "id": int(r[0]),
            "name": r[1],
            "slug": r[2],
            "phrase": r[3],
            "threshold": float(r[4]),
            "model_ref": r[5],
            "is_default": bool(r[6]),
            "status": r[7],
            "source_room_id": r[8],
            "clip_count": int(r[9]),
            "error": r[10],
            # Trains on the synthetic voices alone: no recordings, no
            # minimum clip count (V047).
            "synthetic_only": bool(r[11]),
            # Recordings the CURRENT model was trained on (0 = synthetic
            # voices only; None = no model, or one from before V047).
            "trained_clips": None if r[12] is None else int(r[12]),
        }

    _COLS = (
        "id, name, slug, phrase, threshold, model_ref, is_default, "
        "status, source_room_id, clip_count, error, synthetic_only, trained_clips"
    )

    async def all(self) -> list[dict]:
        """Every wake word, default first then alphabetical."""
        row = await self.s.execute(
            text(
                f"""
                SELECT {self._COLS}
                FROM wake_words
                ORDER BY is_default DESC, lower(name)
                """
            )
        )
        return [self._row(r) for r in row.all()]

    async def get(self, wake_word_id: int) -> dict | None:
        row = await self.s.execute(
            text(f"SELECT {self._COLS} FROM wake_words WHERE id = :id"),
            {"id": wake_word_id},
        )
        r = row.first()
        return self._row(r) if r else None

    async def get_by_name(self, name: str) -> dict | None:
        """Case-insensitive name lookup — what people type rarely matches
        the stored casing exactly."""
        row = await self.s.execute(
            text(
                f"""
                SELECT {self._COLS}
                FROM wake_words
                WHERE lower(name) = lower(:name)
                """
            ),
            {"name": name},
        )
        r = row.first()
        return self._row(r) if r else None

    async def get_by_slug(self, slug: str) -> dict | None:
        """Exact slug lookup. Unlike :class:`VoicesRepository`, wake words
        carry a real ``slug`` column (the load-bearing runtime identifier),
        so the match is exact, not case-folded."""
        row = await self.s.execute(
            text(f"SELECT {self._COLS} FROM wake_words WHERE slug = :slug"),
            {"slug": slug},
        )
        r = row.first()
        return self._row(r) if r else None

    async def get_default(self) -> dict | None:
        row = await self.s.execute(
            text(f"SELECT {self._COLS} FROM wake_words WHERE is_default")
        )
        r = row.first()
        return self._row(r) if r else None

    async def count(self) -> int:
        row = await self.s.execute(text("SELECT COUNT(*) FROM wake_words"))
        return int(row.scalar_one())

    async def create(
        self,
        *,
        name: str,
        slug: str,
        phrase: str,
        threshold: float = 0.7,
        source_room_id: str | None = None,
        synthetic_only: bool = False,
    ) -> int:
        """Insert a fresh wake word in the ``recording`` status. A brand-new
        recording row is never the default (a model must reach ``ready``
        before it can be set default), so there is no default flag to clear
        here. ``synthetic_only`` is fixed here for the word's life: it trains
        on the synthetic voices alone and takes no recordings. Raises
        ``IntegrityError`` on a name/slug collision — the caller maps that to
        a 409."""
        row = await self.s.execute(
            text(
                """
                INSERT INTO wake_words (name, slug, phrase, threshold, source_room_id,
                                        synthetic_only)
                VALUES (:name, :slug, :phrase, :threshold, :source_room_id, :synthetic_only)
                RETURNING id
                """
            ),
            {
                "name": name,
                "slug": slug,
                "phrase": phrase,
                "threshold": threshold,
                "source_room_id": source_room_id,
                "synthetic_only": bool(synthetic_only),
            },
        )
        return int(row.scalar_one())

    async def set_default(self, wake_word_id: int) -> bool:
        """Make ``wake_word_id`` the sole default. Refuses (returns False) a
        row that is not yet ``ready`` — you can't default a wake word with no
        trained model. Clears the flag everywhere first so the partial unique
        index never trips."""
        existing = await self.s.execute(
            text("SELECT status FROM wake_words WHERE id = :id"),
            {"id": wake_word_id},
        )
        r = existing.first()
        if r is None or r[0] != "ready":
            return False
        await self.s.execute(text("UPDATE wake_words SET is_default = FALSE WHERE is_default"))
        await self.s.execute(
            text(
                "UPDATE wake_words SET is_default = TRUE, updated_at = NOW() WHERE id = :id"
            ),
            {"id": wake_word_id},
        )
        return True

    async def update(
        self,
        wake_word_id: int,
        *,
        name: str | None = None,
        threshold: float | None = None,
        phrase: str | None = None,
    ) -> bool:
        """Patch only the provided fields (default is set via
        :meth:`set_default`, status via the transition helpers). Returns True
        if a row changed. ``phrase`` is how the trainer's voices say the
        word (a respelling such as "alex sander" changes the synthetic clips
        of the next training, never the slug the rooms know it by); the
        caller refuses it while the word trains."""
        sets: list[str] = ["updated_at = NOW()"]
        params: dict[str, object] = {"id": wake_word_id}
        if name is not None:
            sets.append("name = :name")
            params["name"] = name
        if phrase is not None:
            sets.append("phrase = :phrase")
            params["phrase"] = phrase
        if threshold is not None:
            sets.append("threshold = :threshold")
            params["threshold"] = threshold
        if len(sets) == 1:
            return False
        # A training read the phrase when it started: never change it under
        # one (atomically; the caller's earlier check names the reason).
        guard = " AND (status <> 'training' OR phrase = :phrase)" if phrase is not None else ""
        row = await self.s.execute(
            text(f"UPDATE wake_words SET {', '.join(sets)} WHERE id = :id{guard}"),
            params,
        )
        return bool(row.rowcount)

    async def delete(self, wake_word_id: int) -> dict | None:
        """Delete and return the removed row (so the caller can clean up the
        trained ``.onnx`` and the recorded clips). Returns None if nothing was
        deleted. Refuses to delete the default wake word — callers should set
        a new default first."""
        row = await self.s.execute(
            text(
                f"""
                DELETE FROM wake_words
                WHERE id = :id AND NOT is_default
                RETURNING {self._COLS}
                """
            ),
            {"id": wake_word_id},
        )
        r = row.first()
        return self._row(r) if r else None

    async def bump_clip_count(self, wake_word_id: int) -> int:
        """Record one more captured positive clip and return the new total.
        Called from the streaming clip-write as each clip lands."""
        row = await self.s.execute(
            text(
                """
                UPDATE wake_words
                SET clip_count = clip_count + 1, updated_at = NOW()
                WHERE id = :id
                RETURNING clip_count
                """
            ),
            {"id": wake_word_id},
        )
        return int(row.scalar_one())

    async def prepare_for_recording(self, wake_word_id: int, clip_count: int) -> str | None:
        """A take is starting. Takes APPEND (2026-11): the clips already
        recorded stay, so this only sets ``clip_count`` to the clips on disk
        (``clip_count``, the file set, then grow together, one clip at a
        time), clears a prior error, and puts a ``failed`` word back to
        ``recording``. A ``ready`` word stays ready: its model is still the
        one in use. Refuses a word that is training (its clips are being
        staged and trained), atomically. Returns the status, or None when the
        row is missing or training."""
        row = await self.s.execute(
            text(
                """
                UPDATE wake_words
                SET status = CASE WHEN status = 'failed' THEN 'recording' ELSE status END,
                    clip_count = :n, error = NULL, updated_at = NOW()
                WHERE id = :id AND status <> 'training'
                RETURNING status
                """
            ),
            {"id": wake_word_id, "n": int(clip_count)},
        )
        r = row.first()
        return r[0] if r else None

    async def set_clip_count(self, wake_word_id: int, clip_count: int) -> bool:
        """Set ``clip_count`` to the clips on disk (after a delete)."""
        row = await self.s.execute(
            text(
                """
                UPDATE wake_words SET clip_count = :n, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": wake_word_id, "n": max(0, int(clip_count))},
        )
        return bool(row.rowcount)

    async def mark_training(self, wake_word_id: int, min_clips: int) -> bool:
        """Promote a ``recording`` row, a ``failed`` one (training it again
        on the clips it has: a timeout, a stop, an out-of-memory), or a
        ``ready`` one (Train again, on the clips recorded since plus the
        old ones; its current model stays in use until the new one lands) to
        ``training`` — only if it still holds enough captured clips
        (``clip_count >= min_clips``). Returns False if the row is missing,
        already training, or has too few clips, so the caller can surface a
        409. A synthetic-voices-only word has no minimum: it uses no clips."""
        row = await self.s.execute(
            text(
                """
                UPDATE wake_words
                SET status = 'training', error = NULL, updated_at = NOW()
                WHERE id = :id AND status IN ('recording', 'failed', 'ready')
                  AND (synthetic_only OR clip_count >= :min)
                RETURNING id
                """
            ),
            {"id": wake_word_id, "min": min_clips},
        )
        return row.first() is not None

    async def mark_stopped(self, wake_word_id: int) -> bool:
        """Put a ``training`` word back (the dashboard's Stop): its clips
        stay, it is not queued again, and its row says it was stopped. A word
        that already had a model (Train again) goes back to ``ready`` (that
        model is still the one in use), any other to ``recording``. False
        when it wasn't training."""
        row = await self.s.execute(
            text(
                """
                UPDATE wake_words
                SET status = CASE WHEN model_ref IS NOT NULL THEN 'ready'
                                  ELSE 'recording' END,
                    error = 'Training stopped from the dashboard.',
                    updated_at = NOW()
                WHERE id = :id AND status = 'training'
                RETURNING id
                """
            ),
            {"id": wake_word_id},
        )
        return row.first() is not None

    async def next_training(self) -> dict | None:
        """Oldest ``training`` row — the trainer worker's queue head."""
        row = await self.s.execute(
            text(
                f"""
                SELECT {self._COLS}
                FROM wake_words
                WHERE status = 'training'
                ORDER BY updated_at
                LIMIT 1
                """
            )
        )
        r = row.first()
        return self._row(r) if r else None

    async def mark_ready(self, wake_word_id: int, model_ref: str,
                         trained_clips: int | None = None) -> bool:
        """The trainer produced a model — flip to ``ready`` and record where
        it landed and how many of the household's recordings it was trained
        on (0 = synthetic voices only; None = not known). Returns True if a
        row changed."""
        row = await self.s.execute(
            text(
                """
                UPDATE wake_words
                SET status = 'ready', model_ref = :model_ref, error = NULL,
                    trained_clips = :trained_clips, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": wake_word_id, "model_ref": model_ref,
             "trained_clips": None if trained_clips is None else max(0, int(trained_clips))},
        )
        return bool(row.rowcount)

    async def mark_failed(self, wake_word_id: int, error: str) -> bool:
        """The trainer errored (or training is unconfigured) — flip to
        ``failed`` and store the reason. Returns True if a row changed.

        A Train again that fails on a word that already had a model goes
        back to ``ready`` with the reason: the model it had is still on disk
        and still the one its rooms use, so it can still be pushed."""
        row = await self.s.execute(
            text(
                """
                UPDATE wake_words
                SET status = CASE WHEN status = 'training' AND model_ref IS NOT NULL
                                  THEN 'ready' ELSE 'failed' END,
                    error = :error, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": wake_word_id, "error": error},
        )
        return bool(row.rowcount)


class DropInCallsRepository:
    """Audit trail for two-way drop-in calls. The live pairing lives
    in app.state; this records just the start/end metadata."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def start(self, initiator_room: str, target_room: str) -> int:
        row = await self.s.execute(
            text(
                "INSERT INTO dropin_calls (initiator_room, target_room, status) "
                "VALUES (:i, :t, 'active') RETURNING id"
            ),
            {"i": initiator_room, "t": target_room},
        )
        return int(row.scalar_one())

    async def finish(
        self, call_id: int, *, status: str = "ended", ended_by: str | None = None
    ) -> None:
        await self.s.execute(
            text(
                "UPDATE dropin_calls SET status = :st, ended_at = NOW(), "
                "ended_by = :by, "
                "duration_sec = EXTRACT(EPOCH FROM (NOW() - started_at))::int "
                "WHERE id = :id AND ended_at IS NULL"
            ),
            {"st": status, "by": ended_by, "id": call_id},
        )


class SatellitePairingRepository:
    """CRUD for `satellite_pairings` — the WS trust-on-first-use rows (V002).

    Only the sha256 of a satellite's pairing token is stored (``token_hash``);
    the raw token lives solely in the Pi's ``~/.domovoi/pairing_token`` sidecar.
    The streaming layer's hello-frame validation drives every method here.
    Keyed by the DEVICE (V073: ``satellite_id``, the WebSocket path), never
    by its room: room playout's auth (domovoi/room_playout/session.py)
    reads it the same way."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get_pairing(
        self, satellite_id: str
    ) -> tuple[str, datetime, datetime | None] | None:
        """The (token_hash, paired_at, last_seen_at) for a room, or None when
        the room has never paired."""
        row = (
            await self.s.execute(
                text(
                    "SELECT token_hash, paired_at, last_seen_at "
                    "FROM satellite_pairings WHERE satellite_id = :r"
                ),
                {"r": satellite_id},
            )
        ).first()
        if row is None:
            return None
        return row.token_hash, row.paired_at, row.last_seen_at

    async def pair(self, satellite_id: str, token_hash: str) -> None:
        """Claim a room for a token (trust-on-first-use). Idempotent on the
        (room_id, token_hash) pair: a re-pair with the SAME hash refreshes
        paired_at/last_seen_at rather than erroring; a DIFFERENT hash would
        only reach here after the row was reset, so ON CONFLICT overwrites.
        The device gets an inventory row and, when new, a room (V073)."""
        await ensure_satellite(self.s, satellite_id)
        await self.s.execute(
            text(
                "INSERT INTO satellite_pairings (satellite_id, token_hash, last_seen_at) "
                "VALUES (:r, :h, now()) "
                "ON CONFLICT (satellite_id) DO UPDATE SET "
                "token_hash = EXCLUDED.token_hash, paired_at = now(), "
                "last_seen_at = now()"
            ),
            {"r": satellite_id, "h": token_hash},
        )

    async def touch_last_seen(self, satellite_id: str) -> None:
        """Stamp last_seen_at=now() for an accepted, already-paired room."""
        await self.s.execute(
            text(
                "UPDATE satellite_pairings SET last_seen_at = now() "
                "WHERE satellite_id = :r"
            ),
            {"r": satellite_id},
        )

    async def reset_pairing(self, satellite_id: str) -> bool:
        """Delete a room's pairing row so the next connect re-pairs (after a
        re-flash / new device). True when a row was actually removed."""
        row = (
            await self.s.execute(
                text(
                    "DELETE FROM satellite_pairings WHERE satellite_id = :r RETURNING 1"
                ),
                {"r": satellite_id},
            )
        ).first()
        return row is not None


# Compared against when a pending request has no code of its own, so the
# answer costs the same work either way. It is not a code anyone can send:
# the endpoint accepts digits only.
_CODE_DECOY = "\x00no-code-on-file"

#: A pending request nobody has refreshed for this long is gone (1.3.0). A
#: device that is still waiting re-asks far more often than this (a Pi at
#: least every minute, a phone every 35 s, an "every house" device every
#: 10 s), so only a request whose device was switched off, set up again
#: elsewhere, or approved by another house and could not withdraw here
#: expires — and with it the room name it was holding.
APPROVAL_EXPIRY_SEC = 900.0
#: The most requests that may wait on a human at once. A new room name past
#: it is told to try again later; a device already waiting still refreshes.
MAX_PENDING_APPROVALS = 32
#: How long a code typed for a proof request waits for its device to come
#: back and run the exchange (V062).
ENTERED_CODE_TTL_SEC = 600.0


class SatelliteApprovalRepository:
    """CRUD for `satellite_approvals` — satellites waiting on a human (V009).

    A portal-onboarded satellite parks here instead of claiming its room,
    because the core was never a participant in its adoption and has no
    preseeded token to match. Approving copies ``token_hash`` into
    `satellite_pairings`, which binds the room to THAT device rather than
    merely to the room name."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def request(
        self,
        room_id: str,
        *,
        token_hash: str,
        code: str | None = None,
        mac: str | None = None,
        board: str | None = None,
        sat_type: str = "voice",
        takeover_after_sec: float | None = None,
        proof: bool = False,
        requested_room: str | None = None,
    ) -> tuple[str, str | None]:
        """Record (or refresh) a pending approval.

        Returns ``("parked", code)`` when the row is this device's to hold
        — ``code`` being the code the operator will be asked for, which on
        a retry is the one already on file rather than the one just
        offered. Returns ``("conflict", None)`` when a DIFFERENT device is
        already parked under that room name: the parked ``token_hash`` and
        ``code`` are retained and nothing about the request is written.
        Only the device that got here first can refresh the row; a human
        clears it with *reject* on the dashboard, which is the one place a
        person is looking at both the request and the room it claims.

        ``takeover_after_sec`` (CORE-22) bounds how long "first" lasts:
        once the parked request is that old (from when its device FIRST
        parked, so retrying does not extend it), a different device's
        request replaces it — token hash, code, metadata and attempts
        together, and the window starts again for the newcomer. The code
        is never kept across a takeover: the operator approves by the code
        the device in the room is saying, so an old code with a new hash
        would bind the wrong device (CORE-3). ``None`` keeps the first
        device for as long as nobody rejects it.

        The device retries on its own, so a repeat connect from the SAME
        device bumps ``attempts`` and refreshes the metadata rather than
        piling up rows, and it keeps the code the customer was already
        shown: whatever they are holding must stay the thing that works.

        ``proof`` (V062): the device proves approvals itself and never sent
        a code, so none is kept — ``code`` is NULL on such a row whatever
        was there — and a code an operator already typed for it survives a
        refresh by the same device (it is waiting for exactly that
        device). A takeover clears it with everything else.

        A request nobody refreshed for :data:`APPROVAL_EXPIRY_SEC` is
        deleted first, so it holds its room name against nobody.

        ``requested_room`` (V073): the room the device asked to join. The
        first one on file stays while the same device refreshes (an
        operator's choice made meanwhile is kept); a takeover replaces it.
        ``mac`` and ``sat_type`` are the device's own words and are bounded
        here. Returns ``("full", None)`` when :data:`MAX_PENDING_APPROVALS`
        requests already wait and none of them is this room's.
        """
        from domovoi.satellite_abilities import clean_mac, clean_sat_type

        await self.purge_expired()
        mac = clean_mac(mac)
        sat_type = clean_sat_type(sat_type)
        waiting = (
            await self.s.execute(
                text(
                    "SELECT count(*) AS n, "
                    "count(*) FILTER (WHERE satellite_id = :r) AS mine "
                    "FROM satellite_approvals"
                ),
                {"r": room_id},
            )
        ).one()
        if not waiting.mine and waiting.n >= MAX_PENDING_APPROVALS:
            return "full", None
        # Every SET expression reads the row as it was before this
        # statement, so "same device" below is the parked device's hash.
        same = "satellite_approvals.token_hash = EXCLUDED.token_hash"
        row = (
            await self.s.execute(
                text(
                    "INSERT INTO satellite_approvals "
                    "(satellite_id, token_hash, code, mac, board, sat_type, proof, "
                    "requested_room) "
                    "VALUES (:r, :h, :c, :m, :b, :t, :p, :room) "
                    "ON CONFLICT (satellite_id) DO UPDATE SET "
                    "code = CASE WHEN EXCLUDED.proof THEN NULL "
                    f"WHEN {same} "
                    "THEN COALESCE(satellite_approvals.code, EXCLUDED.code) "
                    "ELSE EXCLUDED.code END, "
                    "proof = EXCLUDED.proof, "
                    f"entered_code = CASE WHEN {same} AND EXCLUDED.proof "
                    "THEN satellite_approvals.entered_code ELSE NULL END, "
                    f"entered_at = CASE WHEN {same} AND EXCLUDED.proof "
                    "THEN satellite_approvals.entered_at ELSE NULL END, "
                    f"proof_failures = CASE WHEN {same} "
                    "THEN satellite_approvals.proof_failures ELSE 0 END, "
                    f"last_proof_failure_at = CASE WHEN {same} "
                    "THEN satellite_approvals.last_proof_failure_at ELSE NULL END, "
                    f"first_seen_at = CASE WHEN {same} "
                    "THEN satellite_approvals.first_seen_at ELSE now() END, "
                    f"attempts = CASE WHEN {same} "
                    "THEN satellite_approvals.attempts + 1 ELSE 1 END, "
                    f"requested_room = CASE WHEN {same} THEN "
                    "COALESCE(satellite_approvals.requested_room, EXCLUDED.requested_room) "
                    "ELSE EXCLUDED.requested_room END, "
                    "token_hash = EXCLUDED.token_hash, "
                    "mac = EXCLUDED.mac, board = EXCLUDED.board, "
                    "sat_type = EXCLUDED.sat_type, last_seen_at = now() "
                    f"WHERE {same} "
                    "OR (CAST(:takeover AS double precision) IS NOT NULL "
                    "AND satellite_approvals.first_seen_at <= now() - "
                    "make_interval(secs => CAST(:takeover AS double precision))) "
                    "RETURNING code"
                ),
                {
                    "r": room_id, "h": token_hash,
                    "c": None if proof else code,
                    "m": mac, "b": board, "t": sat_type, "p": bool(proof),
                    "room": requested_room,
                    "takeover": (
                        None if takeover_after_sec is None
                        else float(takeover_after_sec)
                    ),
                },
            )
        ).first()
        if row is None:
            return "conflict", None
        return "parked", row.code

    async def list_pending(self) -> list[dict]:
        """Everything waiting on a human, oldest first.

        Neither the token hash nor the CODE is returned. The code is what
        the operator types in to approve, and it is proof they are looking
        at the device that made the request — printing it on the same page
        as the approve button would make it a label again rather than
        something only the room's device can tell them. ``has_code`` says
        whether there is anything to type.

        A proof request (V062) has no code on file and still has something
        to type: the device checks it. ``proof`` marks such a row,
        ``waiting_for_device`` says a typed code is waiting for the device's
        next visit, and ``code_rejected`` says the last code typed for it was
        not the device's (the device did not confirm it). Expired requests
        are deleted before the list is read.

        1.5.0: ``satellite_id`` is the device (``room_id`` repeats it for
        older readers); ``requested_room`` is the room it asked for (or an
        operator chose) and ``requested_room_name`` that room's name (None
        when no such room exists yet)."""
        await self.purge_expired()
        rows = (
            await self.s.execute(
                text(
                    "SELECT a.satellite_id AS room_id, a.satellite_id, "
                    "(a.code IS NOT NULL OR a.proof) AS has_code, "
                    "a.mac, a.board, a.sat_type, a.first_seen_at, a.last_seen_at, a.attempts, "
                    "a.proof, a.requested_room, r.name AS requested_room_name, "
                    "(a.entered_code IS NOT NULL AND a.entered_at > now() - "
                    "make_interval(secs => CAST(:ttl AS double precision))) "
                    "AS waiting_for_device, "
                    "(a.last_proof_failure_at IS NOT NULL AND a.entered_code IS NULL) "
                    "AS code_rejected, a.proof_failures "
                    "FROM satellite_approvals a "
                    "LEFT JOIN rooms r ON r.room_id = a.requested_room "
                    "ORDER BY a.first_seen_at"
                ),
                {"ttl": float(ENTERED_CODE_TTL_SEC)},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def get(self, room_id: str) -> dict | None:
        row = (
            await self.s.execute(
                text(
                    "SELECT satellite_id AS room_id, token_hash, code, mac, board, sat_type "
                    "FROM satellite_approvals WHERE satellite_id = :r"
                ),
                {"r": room_id},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def approve(self, room_id: str, code: str, *, into: str | None = None) -> str:
        """Promote a pending request into a real pairing, atomically, once
        the operator has typed back the code the device showed them.

        Returns one of:

        * ``"approved"`` — the code matched; the row is gone and the
          pairing is written;
        * ``"not_pending"`` — nothing was pending. An approval racing a
          reject (or a second click) must not invent a pairing out of
          nothing;
        * ``"no_code"`` — the request carries no code to check against
          (a row parked by an older server). Nothing is bound whatever was
          sent: the device re-asks on its next connect and arrives with
          one;
        * ``"mismatch"`` — wrong code. The row is untouched, so the device
          that IS in the room keeps its place in the queue.

        The comparison is :func:`secrets.compare_digest` and runs even when
        there is nothing on file, so the answers cost the same.

        A proof request (V062) has nothing on file to compare: its device
        never sent its code. The typed code is kept for one exchange with
        the device instead, and the answer is:

        * ``"entered"`` — kept; the device runs the exchange on its next
          visit and the room is bound only if it confirms the code;
        * ``"malformed"`` — not six digits, which no such device holds.

        ``into`` (1.5.0): the room the operator put the device in (an
        existing room's id). Kept on the request for the binding, so a
        proof request that binds later still lands there.
        """
        await self.purge_expired()
        pending = (
            await self.s.execute(
                text(
                    "SELECT token_hash, code, proof FROM satellite_approvals "
                    "WHERE satellite_id = :r"
                ),
                {"r": room_id},
            )
        ).first()
        if pending is None:
            return "not_pending"
        if into is not None:
            await self.s.execute(
                text("UPDATE satellite_approvals SET requested_room = :i WHERE satellite_id = :r"),
                {"r": room_id, "i": into},
            )
        if pending.proof:
            from satellite import pairing_proof

            candidate = (code or "").strip()
            if not pairing_proof.valid_code(candidate):
                return "malformed"
            # Only for the device that was read: a takeover in between
            # parked another one, which this code was not typed for.
            entered = await self.s.execute(
                text(
                    "UPDATE satellite_approvals SET entered_code = :c, "
                    "entered_at = now() WHERE satellite_id = :r AND token_hash = :h "
                    "AND proof"
                ),
                {"r": room_id, "c": candidate, "h": pending.token_hash},
            )
            if not entered.rowcount:
                return "not_pending"
            return "entered"
        stored = pending.code
        candidate = (code or "").strip()
        # Compared as BYTES: compare_digest refuses a non-ASCII str, and
        # "whatever was posted" is not a promise about the alphabet. The
        # comparison happens even when there is nothing to compare against,
        # so the two answers cost the same; which one comes back is decided
        # afterwards, and "this row has no code" is already on the pending
        # list as has_code.
        matched = secrets.compare_digest(
            (stored or _CODE_DECOY).encode("utf-8"),
            candidate.encode("utf-8"),
        )
        if stored is None:
            return "no_code"
        if not matched:
            return "mismatch"
        row = (
            await self.s.execute(
                text(
                    "DELETE FROM satellite_approvals WHERE satellite_id = :r "
                    "AND token_hash = :h AND code = :c "
                    "RETURNING token_hash, board, sat_type, requested_room"
                ),
                {"r": room_id, "h": pending.token_hash, "c": stored},
            )
        ).first()
        if row is None:
            # Raced a reject between the read and the delete, or a takeover
            # parked another device under the name: the code checked was
            # not that device's, so nothing is bound.
            return "not_pending"
        await self._bind(room_id, row)
        return "approved"

    async def _bind(self, room_id: str, row: Any) -> None:
        """Write the pairing for a request just taken off the list (``row``:
        its token_hash, board, sat_type and requested_room). The device gets
        an inventory row and, when new, a room of its own (V073), unless
        the request names an existing room: then it goes there (1.5.0), and
        a room of its own made a moment ago for it is not kept."""
        sats = SatellitesRepository(self.s)
        fresh = await sats.room_of(room_id) is None
        made_own = fresh and await RoomsRepository(self.s).get(room_id) is None
        await ensure_satellite(self.s, room_id)
        target = getattr(row, "requested_room", None)
        if target and target != await sats.room_of(room_id):
            if await sats.move(room_id, target) and made_own:
                await self.s.execute(
                    text(
                        "DELETE FROM rooms WHERE room_id = :r AND NOT EXISTS "
                        "(SELECT 1 FROM satellites WHERE room_id = :r)"
                    ),
                    {"r": room_id},
                )
        await self.s.execute(
            text(
                "INSERT INTO satellite_pairings (satellite_id, token_hash, last_seen_at) "
                "VALUES (:r, :h, now()) "
                "ON CONFLICT (satellite_id) DO UPDATE SET "
                "token_hash = EXCLUDED.token_hash, paired_at = now(), "
                "last_seen_at = now()"
            ),
            {"r": room_id, "h": row.token_hash},
        )
        if row.board:
            # The board the parked hello named (S0): kept on approval, so the
            # dashboard names the device kind before its next hello. Only a
            # request that carried one writes anything — no Pi sends a board,
            # so an approved Pi's inventory row is left exactly as it was.
            await self.s.execute(
                text(
                    "UPDATE satellites SET board = :b, updated_at = now(), "
                    "sat_type = CASE WHEN :fresh THEN :t ELSE sat_type END "
                    "WHERE satellite_id = :r"
                ),
                {
                    "r": room_id,
                    "fresh": fresh,
                    "t": row.sat_type if row.sat_type in ("voice", "video") else "voice",
                    "b": row.board,
                },
            )

    async def take_entered(
        self, room_id: str, token_hash: str,
        ttl_sec: float = ENTERED_CODE_TTL_SEC,
    ) -> tuple[str, Any] | None:
        """The code an operator typed for THIS device's proof request and
        when, taken off the row for one exchange — a rogue device holding
        the request gets one guess per code typed, never a second run with
        the same one (a run that made no guess puts it back,
        :meth:`restore_entered_code`). None when nothing was typed, when it
        is older than ``ttl_sec``, or when the row is some other device's."""
        row = (
            await self.s.execute(
                text(
                    "SELECT entered_code AS code, entered_at, (entered_at > now() - "
                    "make_interval(secs => CAST(:ttl AS double precision))) AS fresh "
                    "FROM satellite_approvals WHERE satellite_id = :r AND token_hash = :h "
                    "AND proof AND entered_code IS NOT NULL FOR UPDATE"
                ),
                {"r": room_id, "h": token_hash, "ttl": float(ttl_sec)},
            )
        ).first()
        if row is None:
            return None
        await self.s.execute(
            text(
                "UPDATE satellite_approvals SET entered_code = NULL, entered_at = NULL "
                "WHERE satellite_id = :r"
            ),
            {"r": room_id},
        )
        return (row.code, row.entered_at) if row.fresh else None

    async def take_entered_code(
        self, room_id: str, token_hash: str,
        ttl_sec: float = ENTERED_CODE_TTL_SEC,
    ) -> str | None:
        """:meth:`take_entered`, the code alone."""
        taken = await self.take_entered(room_id, token_hash, ttl_sec)
        return taken[0] if taken else None

    async def restore_entered_code(
        self, room_id: str, token_hash: str, code: str, entered_at: Any,
    ) -> bool:
        """Put a typed code back after a run in which the device made no
        guess (it declined, timed out or went away), with its original
        typing time so it still goes stale on time. Only onto the same
        device's request, and never over a code typed since."""
        row = (
            await self.s.execute(
                text(
                    "UPDATE satellite_approvals SET entered_code = :c, "
                    "entered_at = COALESCE(CAST(:t AS timestamptz), now()) "
                    "WHERE satellite_id = :r AND token_hash = :h AND proof "
                    "AND entered_code IS NULL RETURNING 1"
                ),
                {"r": room_id, "h": token_hash, "c": code, "t": entered_at},
            )
        ).first()
        return row is not None

    async def note_proof_failure(self, room_id: str, token_hash: str) -> None:
        """The device did not confirm the code typed for it: wrong digits,
        or not the device in the room. Counted on the row for the
        dashboard; the request itself stays parked for the real device."""
        await self.s.execute(
            text(
                "UPDATE satellite_approvals SET proof_failures = proof_failures + 1, "
                "last_proof_failure_at = now() "
                "WHERE satellite_id = :r AND token_hash = :h"
            ),
            {"r": room_id, "h": token_hash},
        )

    async def promote_proven(self, room_id: str, token_hash: str) -> str:
        """Bind the room to the device whose proof request this is, once
        the device has confirmed the code the operator typed (the exchange
        in domovoi/streaming.py). ``"approved"`` or ``"not_pending"`` (the
        request was rejected, expired or taken over meanwhile)."""
        row = (
            await self.s.execute(
                text(
                    "DELETE FROM satellite_approvals WHERE satellite_id = :r "
                    "AND token_hash = :h AND proof "
                    "RETURNING token_hash, board, sat_type, requested_room"
                ),
                {"r": room_id, "h": token_hash},
            )
        ).first()
        if row is None:
            return "not_pending"
        await self._bind(room_id, row)
        return "approved"

    async def withdraw(self, room_id: str, token_hash: str) -> bool:
        """The device asks to be taken off the list: another house approved
        it. Only the device whose request it is can do that — the hash of
        the token it parked with is the proof."""
        row = (
            await self.s.execute(
                text(
                    "DELETE FROM satellite_approvals WHERE satellite_id = :r "
                    "AND token_hash = :h RETURNING 1"
                ),
                {"r": room_id, "h": token_hash},
            )
        ).first()
        return row is not None

    async def purge_expired(self, expiry_sec: float = APPROVAL_EXPIRY_SEC,
                            entered_ttl_sec: float = ENTERED_CODE_TTL_SEC) -> int:
        """Delete requests nobody has refreshed for ``expiry_sec``, and
        forget a typed code (V062) once it is older than
        ``entered_ttl_sec``: a stale code is never used, so it is not kept
        either. Returns how many requests were deleted."""
        await self.s.execute(
            text(
                "UPDATE satellite_approvals SET entered_code = NULL, entered_at = NULL "
                "WHERE entered_code IS NOT NULL AND (entered_at IS NULL OR entered_at "
                "< now() - make_interval(secs => CAST(:t AS double precision)))"
            ),
            {"t": float(entered_ttl_sec)},
        )
        result = await self.s.execute(
            text(
                "DELETE FROM satellite_approvals WHERE last_seen_at < now() - "
                "make_interval(secs => CAST(:e AS double precision))"
            ),
            {"e": float(expiry_sec)},
        )
        return int(result.rowcount or 0)

    async def reject(self, room_id: str) -> bool:
        """Drop a pending request. The device keeps retrying — rejection is
        not a ban, and pretending otherwise would be a lie the UI can't
        keep. Powering the satellite off is what actually stops it."""
        row = (
            await self.s.execute(
                text(
                    "DELETE FROM satellite_approvals WHERE satellite_id = :r RETURNING 1"
                ),
                {"r": room_id},
            )
        ).first()
        return row is not None


# ─── rooms and satellites (V073) ────────────────────────────────────────────
#
# A room is the place (`rooms`); a satellite is a device in exactly one room
# (`satellites`, keyed by `satellite_id`). Every device that existed before
# 1.5.0 has satellite_id = its room's id, so code that still passes a "room
# id" for a device reaches the right row while each room has one device.
# A device the house has never seen gets a room of its own, named by its id.

#: The room a device the house hasn't seen yet lands in: its own, by its id.
_ENSURE_OWN_ROOM = (
    "INSERT INTO rooms (room_id, name) SELECT CAST(:r AS text), CAST(:r AS text) "
    "WHERE NOT EXISTS (SELECT 1 FROM satellites WHERE satellite_id = :r) "
    "ON CONFLICT (room_id) DO NOTHING"
)

#: A room's display label as the pre-1.5.0 callers read it: None when the
#: room has no name of its own (its name is its id).
ROOM_LABEL_SQL = "NULLIF(r.name, r.room_id)"

_SATELLITE_COLS = (
    "s.satellite_id, s.room_id, s.name, s.sat_type, "
    f"{ROOM_LABEL_SQL} AS room_label, r.name AS room_name, "
    "s.hardware, s.board, s.mac, s.wake_max, s.output_latency_us, s.calibrated_at, "
    "s.adopted_via, s.adopted_at, s.created_at, s.updated_at"
)
_SATELLITE_FROM = "FROM satellites s JOIN rooms r ON r.room_id = s.room_id"

_ROOM_COLS = (
    "r.room_id, r.name, r.voice, r.quiet_mode, r.quiet_start, r.quiet_end, "
    "r.playout_ms, r.created_at, r.updated_at, "
    "(SELECT count(*) FROM satellites s WHERE s.room_id = r.room_id) AS satellites"
)


async def ensure_satellite(
    session: AsyncSession, satellite_id: str, *, adopted_via: str = "hello"
) -> None:
    """Make sure a device has an inventory row and a room. A new device
    gets a room of its own named by its id; a known one stays where it is.
    The phone app's chat mic (``phone-...``) is not a room: left alone."""
    if satellite_id.startswith(PHONE_ROOM_PREFIX):
        return
    await session.execute(text(_ENSURE_OWN_ROOM), {"r": satellite_id})
    await session.execute(
        text(
            "INSERT INTO satellites (satellite_id, room_id, adopted_via) "
            "VALUES (:r, :r, :v) ON CONFLICT (satellite_id) DO NOTHING"
        ),
        {"r": satellite_id, "v": adopted_via},
    )


class RoomsRepository:
    """`rooms` (V073): the places of the house. A room keeps its id for
    life; its ``name`` is what people call it. Empty rooms are kept until
    an admin deletes them. Room-level settings (voice, quiet hours,
    playout) live here; device facts live in :class:`SatellitesRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def list(self) -> list[dict]:
        rows = (
            await self.s.execute(text(f"SELECT {_ROOM_COLS} FROM rooms r ORDER BY r.room_id"))
        ).mappings()
        return [dict(r) for r in rows]

    async def get(self, room_id: str) -> dict | None:
        row = (
            await self.s.execute(
                text(f"SELECT {_ROOM_COLS} FROM rooms r WHERE r.room_id = :r"), {"r": room_id}
            )
        ).mappings().first()
        return dict(row) if row is not None else None

    async def create(self, room_id: str, name: str | None = None) -> bool:
        """Add a room. False when the id is taken (nothing changes)."""
        row = (
            await self.s.execute(
                text(
                    "INSERT INTO rooms (room_id, name) VALUES (:r, :n) "
                    "ON CONFLICT (room_id) DO NOTHING RETURNING 1"
                ),
                {"r": room_id, "n": (name or "").strip() or room_id},
            )
        ).first()
        return row is not None

    async def rename(self, room_id: str, name: str | None) -> bool:
        """Set the room's name; None or blank goes back to its id."""
        row = (
            await self.s.execute(
                text(
                    "UPDATE rooms SET name = :n, updated_at = now() "
                    "WHERE room_id = :r RETURNING 1"
                ),
                {"r": room_id, "n": (name or "").strip() or room_id},
            )
        ).first()
        return row is not None

    async def delete(self, room_id: str) -> str:
        """Delete an EMPTY room: "deleted", "missing", or "has_satellites"
        (move or remove its satellites first). History rows keep its id."""
        busy = (
            await self.s.execute(
                text("SELECT 1 FROM satellites WHERE room_id = :r LIMIT 1"), {"r": room_id}
            )
        ).first()
        if busy is not None:
            return "has_satellites"
        row = (
            await self.s.execute(
                text("DELETE FROM rooms WHERE room_id = :r RETURNING 1"), {"r": room_id}
            )
        ).first()
        return "deleted" if row is not None else "missing"

    async def merge(self, into: str, others: list[str]) -> list[str]:
        """Move every satellite of ``others`` into ``into`` and delete those
        rooms (the Rooms page's merge for rooms that shared a label; never
        done on its own). Returns the satellite ids moved. History stays
        under the old room ids; the merged rooms' wake-word rows go, so the
        devices take ``into``'s."""
        others = [o for o in dict.fromkeys(others) if o != into]
        if not others or await self.get(into) is None:
            return []
        moved = [
            r[0]
            for r in (
                await self.s.execute(
                    text(
                        "UPDATE satellites SET room_id = :into, updated_at = now() "
                        "WHERE room_id = ANY(:o) RETURNING satellite_id"
                    ),
                    {"into": into, "o": others},
                )
            ).all()
        ]
        await self.s.execute(
            text("DELETE FROM room_wake_words WHERE room_id = ANY(:o)"), {"o": others}
        )
        await self.s.execute(text("DELETE FROM rooms WHERE room_id = ANY(:o)"), {"o": others})
        return sorted(moved)

    async def voice(self, room_id: str) -> str | None:
        row = (
            await self.s.execute(text("SELECT voice FROM rooms WHERE room_id = :r"), {"r": room_id})
        ).first()
        return row[0] if row is not None and row[0] else None

    async def set_voice(self, room_id: str, voice: str | None) -> None:
        """Keep (or clear) the voice the server speaks this room in. Upserts:
        a room known only by its music player can be given one."""
        await self.s.execute(
            text(
                "INSERT INTO rooms (room_id, name, voice) VALUES (:r, :r, :v) "
                "ON CONFLICT (room_id) DO UPDATE SET voice = EXCLUDED.voice, updated_at = now()"
            ),
            {"r": room_id, "v": voice},
        )

    async def linked(self, room_id: str) -> list[str]:
        """The rooms marked as hearing this one (`room_hear_links`)."""
        rows = (
            await self.s.execute(
                text(
                    "SELECT CASE WHEN room_a = :r THEN room_b ELSE room_a END "
                    "FROM room_hear_links WHERE room_a = :r OR room_b = :r ORDER BY 1"
                ),
                {"r": room_id},
            )
        ).all()
        return [r[0] for r in rows]

    async def link(self, a: str, b: str) -> bool:
        """Mark two rooms as hearing each other. False when already linked
        or the same room."""
        if a == b:
            return False
        lo, hi = sorted((a, b))
        row = (
            await self.s.execute(
                text(
                    "INSERT INTO room_hear_links (room_a, room_b) VALUES (:a, :b) "
                    "ON CONFLICT DO NOTHING RETURNING 1"
                ),
                {"a": lo, "b": hi},
            )
        ).first()
        return row is not None

    async def unlink(self, a: str, b: str) -> bool:
        lo, hi = sorted((a, b))
        row = (
            await self.s.execute(
                text("DELETE FROM room_hear_links WHERE room_a = :a AND room_b = :b RETURNING 1"),
                {"a": lo, "b": hi},
            )
        ).first()
        return row is not None


class SatellitesRepository:
    """`satellites` (V003, re-keyed by V073): the device table. A row is a
    known device: preseeded by USB adoption, written by an accepted
    `hello`, or by a pairing. ``satellite_id`` is the device's identity
    (the WebSocket path); ``room_id`` the room it is in. sat_type /
    adopted_via are app-validated open enums (registered_values domains).

    Rows come back as dicts with the device columns plus ``room_name``
    and ``room_label`` (the room's name, None when it is just its id)."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    # ── reads ──────────────────────────────────────────────────────────────

    async def get(self, satellite_id: str) -> dict | None:
        row = (
            await self.s.execute(
                text(f"SELECT {_SATELLITE_COLS} {_SATELLITE_FROM} WHERE s.satellite_id = :r"),
                {"r": satellite_id},
            )
        ).mappings().first()
        return dict(row) if row is not None else None

    async def get_by_mac(self, mac: str) -> dict | None:
        """The device previously seen with this wlan MAC (lowercased) —
        correlates a re-plugged device with its existing adoption row."""
        row = (
            await self.s.execute(
                text(
                    f"SELECT {_SATELLITE_COLS} {_SATELLITE_FROM} "
                    "WHERE s.mac = :m ORDER BY s.satellite_id LIMIT 1"
                ),
                {"m": mac.lower()},
            )
        ).mappings().first()
        return dict(row) if row is not None else None

    async def all(self) -> list[dict]:
        rows = (
            await self.s.execute(
                text(f"SELECT {_SATELLITE_COLS} {_SATELLITE_FROM} ORDER BY s.satellite_id")
            )
        ).mappings()
        return [dict(r) for r in rows]

    async def in_room(self, room_id: str) -> list[dict]:
        """The room's satellites, by id."""
        rows = (
            await self.s.execute(
                text(
                    f"SELECT {_SATELLITE_COLS} {_SATELLITE_FROM} "
                    "WHERE s.room_id = :r ORDER BY s.satellite_id"
                ),
                {"r": room_id},
            )
        ).mappings()
        return [dict(r) for r in rows]

    async def room_of(self, satellite_id: str) -> str | None:
        """The room a device is in, or None for a device the house has no
        row for."""
        row = (
            await self.s.execute(
                text("SELECT room_id FROM satellites WHERE satellite_id = :r"),
                {"r": satellite_id},
            )
        ).first()
        return row[0] if row is not None else None

    async def room_is_known(self, room_id: str) -> bool:
        """Whether ``room_id`` is a room this house already has: a `rooms`
        row, an MPD row, or a pairing. An HTTP route that is about to start
        a room's music player asks this first, so a name somebody merely
        typed into a URL never provisions a new one (CORE-13)."""
        row = (
            await self.s.execute(
                text(
                    "SELECT 1 FROM rooms WHERE room_id = :r "
                    "UNION ALL SELECT 1 FROM mpd_rooms WHERE room_id = :r "
                    "UNION ALL SELECT 1 FROM satellite_pairings WHERE satellite_id = :r "
                    "LIMIT 1"
                ),
                {"r": room_id},
            )
        ).first()
        return row is not None

    # ── writes ─────────────────────────────────────────────────────────────

    async def preseed_upsert(
        self,
        satellite_id: str,
        *,
        sat_type: str = "voice",
        room_label: str | None = None,
        hardware: str | None = None,
        board: str | None = None,
        mac: str | None = None,
        adopted_via: str = "usb",
    ) -> None:
        """Full-row upsert from the adoption flow; stamps adopted_at=now().
        ``room_label`` names the device's room (None: the room's id)."""
        await ensure_satellite(self.s, satellite_id, adopted_via=adopted_via)
        await self.s.execute(
            text(
                "UPDATE satellites SET sat_type = :t, hardware = :h, board = :b, "
                "mac = :m, adopted_via = :v, adopted_at = now(), updated_at = now() "
                "WHERE satellite_id = :r"
            ),
            {
                "r": satellite_id,
                "t": sat_type,
                "h": hardware,
                "b": board,
                "m": mac,
                "v": adopted_via,
            },
        )
        await self._name_room_of(satellite_id, room_label)

    async def upsert_type(self, satellite_id: str, sat_type: str) -> None:
        """Self-registration from a `hello` frame that EXPLICITLY carried
        sat_type. Only ever called for explicit frames — an old client that
        omits the field must never funnel through here and reset an adopted
        row back to 'voice'."""
        await ensure_satellite(self.s, satellite_id)
        await self.s.execute(
            text(
                "UPDATE satellites SET sat_type = :t, updated_at = now() "
                "WHERE satellite_id = :r"
            ),
            {"r": satellite_id, "t": sat_type},
        )

    async def upsert_board(self, satellite_id: str, board: str) -> None:
        """Self-registration of the board slug an accepted `hello` named
        (S0, spare-device satellites). Same rule as :meth:`upsert_type`:
        only for a hello that carried one, so a hello without it never
        clears what USB adoption or an earlier hello stored. The caller
        bounds the value (``satellite_abilities.clean_board``)."""
        await ensure_satellite(self.s, satellite_id)
        await self.s.execute(
            text(
                "UPDATE satellites SET board = :b, updated_at = now() "
                "WHERE satellite_id = :r AND board IS DISTINCT FROM :b"
            ),
            {"r": satellite_id, "b": board},
        )

    async def set_name(self, satellite_id: str, name: str | None) -> bool:
        """The device's own display name ("Kitchen phone"); None clears it."""
        row = (
            await self.s.execute(
                text(
                    "UPDATE satellites SET name = :n, updated_at = now() "
                    "WHERE satellite_id = :r RETURNING 1"
                ),
                {"r": satellite_id, "n": (name or "").strip() or None},
            )
        ).first()
        return row is not None

    async def set_wake_max(self, satellite_id: str, wake_max: int | None) -> None:
        """How many wake words the device can run at once (its hello's
        ``wake_max``; was ``room_wake_words.device_max`` before V073)."""
        await self.s.execute(
            text(
                "UPDATE satellites SET wake_max = :m "
                "WHERE satellite_id = :r AND wake_max IS DISTINCT FROM :m"
            ),
            {"r": satellite_id, "m": wake_max},
        )

    async def set_calibration(self, satellite_id: str, output_latency_us: int) -> bool:
        """Store a speaker calibration (domovoi/room_calibration.py): the
        device's output latency, relative within its room, stamped now."""
        row = (
            await self.s.execute(
                text(
                    "UPDATE satellites SET output_latency_us = :us, calibrated_at = now() "
                    "WHERE satellite_id = :r RETURNING 1"
                ),
                {"r": satellite_id, "us": int(output_latency_us)},
            )
        ).first()
        return row is not None

    async def move(self, satellite_id: str, room_id: str) -> bool:
        """Put the device in another (existing) room. Its id never changes;
        its calibration is cleared (the room around it changed). False when
        the device or the room is missing."""
        row = (
            await self.s.execute(
                text(
                    "UPDATE satellites SET room_id = :room, output_latency_us = NULL, "
                    "calibrated_at = NULL, updated_at = now() "
                    "WHERE satellite_id = :r AND EXISTS "
                    "(SELECT 1 FROM rooms WHERE room_id = :room) RETURNING 1"
                ),
                {"r": satellite_id, "room": room_id},
            )
        ).first()
        return row is not None

    async def delete(self, satellite_id: str) -> bool:
        """Remove a device's inventory row (adopt rollback / remove a
        device). A room left empty that was only this device's own room
        (same id) goes with it, as removing a one-device room always did;
        any other room is kept. True when a row was actually removed."""
        row = (
            await self.s.execute(
                text("DELETE FROM satellites WHERE satellite_id = :r RETURNING room_id"),
                {"r": satellite_id},
            )
        ).first()
        if row is None:
            return False
        if row[0] == satellite_id:
            await self.s.execute(
                text(
                    "DELETE FROM rooms WHERE room_id = :r "
                    "AND NOT EXISTS (SELECT 1 FROM satellites WHERE room_id = :r)"
                ),
                {"r": satellite_id},
            )
        return True

    # ── room settings, by the room's id (pre-1.5.0 callers) ────────────────

    async def room_voice(self, room_id: str) -> str | None:
        """The voice the server keeps for this room (V031, on `rooms` since
        V073), or None (the server's own voice). See domovoi/room_voice.py."""
        return await RoomsRepository(self.s).voice(room_id)

    async def set_room_voice(self, room_id: str, voice: str | None) -> None:
        await RoomsRepository(self.s).set_voice(room_id, voice)

    async def set_room_label(self, room_id: str, room_label: str | None) -> None:
        """Set (or clear, with None) a room's display name. Upserts so a
        room known only by its music player can be named."""
        await self.s.execute(
            text(
                "INSERT INTO rooms (room_id, name) VALUES (:r, :n) "
                "ON CONFLICT (room_id) DO UPDATE SET name = EXCLUDED.name, updated_at = now()"
            ),
            {"r": room_id, "n": (room_label or "").strip() or room_id},
        )

    async def _name_room_of(self, satellite_id: str, room_label: str | None) -> None:
        await self.s.execute(
            text(
                "UPDATE rooms r SET name = :n, updated_at = now() "
                "FROM satellites s WHERE s.satellite_id = :sid AND r.room_id = s.room_id"
            ),
            {"sid": satellite_id, "n": (room_label or "").strip() or satellite_id},
        )


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _jsonb(value: Any) -> Any:
    """asyncpg hands JSONB back decoded through SQLAlchemy's codec; a raw
    text() read on a connection without it gives a string. Take both."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


class SatelliteHealthRepository:
    """`satellite_health` (V022): one row per `health` sample a satellite
    sends (about one a minute), kept 24 h per room. Numbers only — see
    domovoi/satellite_health.py for the shape."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def insert(self, room_id: str, sample: dict[str, Any]) -> None:
        await self.s.execute(
            text(
                "INSERT INTO satellite_health (satellite_id, sample) "
                "VALUES (:r, CAST(:s AS jsonb))"
            ),
            {"r": room_id, "s": json.dumps(sample, default=str)},
        )

    async def prune(self, room_id: str | None, keep_hours: int = 24) -> int:
        """Drop samples older than ``keep_hours``: one room's, or every
        room's when ``room_id`` is None (the stream handler's call, so a
        room that stopped connecting is pruned too). Each room's newest
        sample stays, however old: it is when the room was last heard from
        (the Satellites page's "last heard", review 2026-10-15), and the
        Health card says how old it is."""
        keep_newest = (
            "AND h.id <> (SELECT n.id FROM satellite_health n WHERE n.satellite_id = h.satellite_id "
            "ORDER BY n.received_at DESC, n.id DESC LIMIT 1)"
        )
        if room_id is None:
            result = await self.s.execute(
                text(
                    "DELETE FROM satellite_health h "
                    "WHERE h.received_at < now() - make_interval(hours => CAST(:h AS int)) "
                    + keep_newest
                ),
                {"h": int(keep_hours)},
            )
            return int(result.rowcount or 0)
        result = await self.s.execute(
            text(
                "DELETE FROM satellite_health h WHERE h.satellite_id = :r "
                "AND h.received_at < now() - make_interval(hours => CAST(:h AS int)) "
                + keep_newest
            ),
            {"r": room_id, "h": int(keep_hours)},
        )
        return int(result.rowcount or 0)

    async def latest(self, room_id: str) -> dict[str, Any] | None:
        row = (
            await self.s.execute(
                text(
                    "SELECT received_at, sample FROM satellite_health "
                    "WHERE satellite_id = :r ORDER BY received_at DESC, id DESC LIMIT 1"
                ),
                {"r": room_id},
            )
        ).first()
        if row is None:
            return None
        return {"received_at": row[0].isoformat(), "sample": _jsonb(row[1])}

    async def history(
        self, room_id: str, hours: int = 24, limit: int = 1500
    ) -> list[tuple[datetime, dict[str, Any]]]:
        """``(received_at, sample)`` oldest first, at most ``limit`` rows."""
        rows = (
            await self.s.execute(
                text(
                    "SELECT received_at, sample FROM satellite_health "
                    "WHERE satellite_id = :r "
                    "AND received_at >= now() - make_interval(hours => CAST(:h AS int)) "
                    "ORDER BY received_at ASC, id ASC LIMIT :n"
                ),
                {"r": room_id, "h": int(hours), "n": int(limit)},
            )
        ).all()
        return [(r[0], _jsonb(r[1]) or {}) for r in rows]


class SatelliteOutageRepository:
    """`satellite_outages` (V022): the `prev_health` reports a satellite's
    hello carries — what its previous process life left behind. The last
    ``keep`` per room are retained."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def insert(
        self, room_id: str, prev_boot_id: str | None, report: dict[str, Any], *, keep: int = 20
    ) -> int:
        row = (
            await self.s.execute(
                text(
                    "INSERT INTO satellite_outages (satellite_id, prev_boot_id, report) "
                    "VALUES (:r, :b, CAST(:p AS jsonb)) RETURNING id"
                ),
                {"r": room_id, "b": prev_boot_id, "p": json.dumps(report, default=str)},
            )
        ).first()
        await self.s.execute(
            text(
                "DELETE FROM satellite_outages WHERE satellite_id = :r AND id NOT IN ("
                "SELECT id FROM satellite_outages WHERE satellite_id = :r "
                "ORDER BY reported_at DESC, id DESC LIMIT :n)"
            ),
            {"r": room_id, "n": int(keep)},
        )
        return int(row[0]) if row else 0

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        return {
            "id": int(row[0]),
            "reported_at": row[1].isoformat(),
            "prev_boot_id": row[2],
            "report": _jsonb(row[3]),
        }

    async def latest(self, room_id: str) -> dict[str, Any] | None:
        row = (
            await self.s.execute(
                text(
                    "SELECT id, reported_at, prev_boot_id, report FROM satellite_outages "
                    "WHERE satellite_id = :r ORDER BY reported_at DESC, id DESC LIMIT 1"
                ),
                {"r": room_id},
            )
        ).first()
        return self._row(row) if row is not None else None

    async def list(self, room_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = (
            await self.s.execute(
                text(
                    "SELECT id, reported_at, prev_boot_id, report FROM satellite_outages "
                    "WHERE satellite_id = :r ORDER BY reported_at DESC, id DESC LIMIT :n"
                ),
                {"r": room_id, "n": int(limit)},
            )
        ).all()
        return [self._row(r) for r in rows]
