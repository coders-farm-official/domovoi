"""ReminderHandler — set reminders that *speak* a message when they fire.

A reminder is just a row in `timers` with `message IS NOT NULL` (V001
schema is already shaped for this). The TimerWatcher fires reminders
the same way it fires plain timers, but when `message` is populated it
delivers the message via the originating room's `StreamSession.announce`
instead of just logging.

Three usage shapes:

1. "remind me to <thing> in <duration>" — most common.
2. "what (are my) reminders" / "list reminders" — list pending reminders
   in the current room.
3. "cancel (my|the) reminder (about|for|to) <thing>" — cancel by label
   match, or all reminders in the room when no label given.

Absolute-time reminders ("remind me at 5pm") are deferred — they need a
real natural-language datetime parser, which isn't worth bolting on
yet.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.db.repositories import TimerRepository, utcnow
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.handlers.timer import _format_duration
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)

_UNIT_TO_SECONDS = {"second": 1, "minute": 60, "hour": 3600}

# "remind me to call mom in 10 minutes"
# "remind me to take the trash out in an hour"  ← we don't yet support "an" / "a"
# Greedy `.+?` for the message lets the duration capture stay anchored.
_CREATE_RE = re.compile(
    r"^remind me to (?P<message>.+?) in (?P<amount>\d+) (?P<unit>second|minute|hour)s?$"
)

# "what reminders do I have" / "list my reminders" / "what are my reminders"
_LIST_RE = re.compile(
    r"^(?:"
    r"what(?:'s| is| are)? (?:my )?reminders?(?: do i have)?"
    r"|list (?:my |the )?reminders?"
    r")$"
)

# "cancel my reminder to call mom" / "cancel the reminder for taking the trash"
_CANCEL_RE = re.compile(
    r"^cancel (?:my |the |that )?reminder(?:s)?"
    r"(?: (?:to|about|for|named|called) (?P<label>.+))?$"
)


class ReminderHandler(Handler):
    name = "reminder"
    # band rationale: "remind me to ..." before timer (160) so the reminder regex wins
    #   over any future timer regex that might brush against "remind".
    priority_band = 140
    display = HandlerDisplay(label="Reminders", tone="info")
    requires_network = "no"

    tool_schema = {
        "name": "reminder",
        "description": (
            "Schedule a spoken reminder for the current room. Reminders "
            "are timers with a message that gets read aloud when they "
            "fire. Use 'list' to read pending reminders, 'cancel' to "
            "remove them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "list", "cancel"],
                },
                "message": {
                    "type": "string",
                    "description": "What to remind about (for create action).",
                },
                "duration_sec": {
                    "type": "integer",
                    "description": "How long until the reminder fires.",
                },
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_CREATE_RE, ReminderHandler._create_from_match),
            FastPath(_LIST_RE, ReminderHandler._list_from_match),
            FastPath(_CANCEL_RE, ReminderHandler._cancel_from_match),
        ]

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        return Response(
            text="I didn't catch a reminder command.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        action = args.get("action")
        if action == "create":
            message = (args.get("message") or "").strip()
            duration = int(args.get("duration_sec") or 0)
            if not message:
                return Response(
                    text="What should I remind you about?",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
            if duration <= 0:
                return Response(
                    text="When should I remind you?",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
            return await self._create(message, duration, ctx, session)
        if action == "list":
            return await self._list(ctx, session)
        if action == "cancel":
            return await self._cancel(args.get("message"), ctx, session)
        return Response(
            text=f"I don't know how to {action} a reminder.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    # ─── Fast-path adapters ───────────────────────────────────────────
    async def _create_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        message = m.group("message").strip()
        amount = int(m.group("amount"))
        unit = m.group("unit")
        duration_sec = amount * _UNIT_TO_SECONDS[unit]
        return await self._create(message, duration_sec, ctx, session)

    async def _list_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._list(ctx, session)

    async def _cancel_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        label_phrase = m.groupdict().get("label")
        if label_phrase:
            label_phrase = label_phrase.strip()
        return await self._cancel(label_phrase, ctx, session)

    # ─── Core actions ─────────────────────────────────────────────────
    async def _create(
        self, message: str, duration_sec: int, ctx: Context, session: AsyncSession
    ) -> Response:
        repo = TimerRepository(session)
        expires_at = utcnow() + timedelta(seconds=duration_sec)
        # Use the message as the label too so "cancel the reminder for X"
        # can match without a separate column. Trimmed at 100 chars to
        # keep labels searchable; the full message stays in `message`.
        await repo.create(
            expires_at=expires_at,
            label=message[:100],
            message=message,
            room_id=ctx.room_id,
        )
        spoken = _format_duration(duration_sec)
        return Response(
            text=f"I'll remind you to {message} in {spoken}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _list(self, ctx: Context, session: AsyncSession) -> Response:
        # Reminders are timers WHERE message IS NOT NULL. Plain timers
        # (no message) are the TimerHandler's territory.
        result = await session.execute(
            text(
                """
                SELECT message, expires_at
                FROM timers
                WHERE room_id IS NOT DISTINCT FROM :room_id
                  AND message IS NOT NULL
                ORDER BY expires_at ASC
                """
            ),
            {"room_id": ctx.room_id},
        )
        rows = result.all()
        if not rows:
            return Response(
                text="You have no reminders set.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if len(rows) == 1:
            msg, expires = rows[0]
            remaining = int((expires - utcnow()).total_seconds())
            spoken = _format_duration(max(1, remaining))
            return Response(
                text=f"In {spoken}: {msg}.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        # 2+ reminders: report the first two by spoken time-to-fire and
        # the count. Reading every one aloud gets long fast.
        first_msg, first_exp = rows[0]
        second_msg, _ = rows[1]
        first_spoken = _format_duration(max(1, int((first_exp - utcnow()).total_seconds())))
        return Response(
            text=(
                f"You have {len(rows)} reminders. The next one, in "
                f"{first_spoken}: {first_msg}. After that: {second_msg}."
            ),
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _cancel(
        self, label_phrase: str | None, ctx: Context, session: AsyncSession
    ) -> Response:
        # Two modes:
        # 1. label given → DELETE WHERE label LIKE :phrase% AND room_id = X
        #    AND message IS NOT NULL. Substring match because Whisper's
        #    transcript rarely matches the original label exactly.
        # 2. no label → DELETE all reminders in this room.
        if label_phrase:
            result = await session.execute(
                text(
                    """
                    DELETE FROM timers
                    WHERE room_id IS NOT DISTINCT FROM :room_id
                      AND message IS NOT NULL
                      AND lower(label) LIKE :pattern
                    """
                ),
                {
                    "room_id": ctx.room_id,
                    "pattern": f"%{label_phrase.lower()}%",
                },
            )
        else:
            result = await session.execute(
                text(
                    """
                    DELETE FROM timers
                    WHERE room_id IS NOT DISTINCT FROM :room_id
                      AND message IS NOT NULL
                    """
                ),
                {"room_id": ctx.room_id},
            )
        deleted = result.rowcount or 0
        if deleted == 0:
            return Response(
                text="I couldn't find a matching reminder.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if deleted == 1:
            return Response(
                text="Cancelled the reminder.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return Response(
            text=f"Cancelled {deleted} reminders.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )
