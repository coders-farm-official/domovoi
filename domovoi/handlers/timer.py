from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.db.repositories import TimerRepository, utcnow
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.handlers.shared.number_words import DURATION_PATTERN, parse_duration_seconds
from domovoi.handlers.shared.tool_gate import TIMER_STATEMENT_RE
from domovoi.models import Context, Intent, Response

# The duration grammar lives in handlers/shared/number_words: digits,
# spelled numbers ("ten minutes", "forty-five minutes"), articles ("an
# hour"), halves ("half an hour", "an hour and a half", "one and a half
# hours") and two joined clauses ("1 hour and 30 minutes"). Whisper spells
# small numbers out more often than not, and the digit-only pattern this
# replaced sent "set a timer for one minute" through the ~4 s LLM tool
# router. The fragment is a closed vocabulary with no capturing groups, so
# the create path stays anchored on "timer for" and cannot poach a later
# band.
_CREATE_RE = re.compile(
    rf"^(?:set a |)timer for (?P<duration>{DURATION_PATTERN})"
    r"(?: (?:for|called|named) (?P<label>.+))?$"
)
# A labelled cancel is scoped to the room it was said in; the suffix makes
# it house-wide ("cancel the timer for pasta everywhere"). Group 1 is still
# the label, so older callers reading m.group(1) keep working.
_CANCEL_RE = re.compile(
    r"^(?:cancel|stop) (?:the |)timer(?: (?:for|called|named) (?P<label>.+?))?"
    r"(?P<everywhere> everywhere| in every room| in all rooms| in the whole house)?$"
)
_STATUS_RE = re.compile(r"^(?:how much time|how long) (?:left |)on (?:the |)timer$")

# "timer for 10 minutes for the pasta" stores the label "the pasta". Every
# line that puts its own word in front of the label ("the pasta timer",
# "Your pasta timer is done") speaks it without the user's article, or it
# comes out as "the the pasta timer".
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|my|a|an)\s+", re.IGNORECASE)


def spoken_label(label: str) -> str:
    """A timer label ready to follow "the" or "your": "the pasta" → "pasta".
    Shared by these replies and the TimerWatcher's fired line."""
    return _LEADING_ARTICLE_RE.sub("", label.strip())


def _elsewhere_hint(label: str, rooms: list[str]) -> str:
    """A room-scoped labelled cancel found nothing here, but that label is
    running in other rooms: say where, and how to cancel it from here."""
    where = " and the ".join(
        r.replace("-", " ").replace("_", " ").strip() for r in rooms
    )
    return (
        f"The {spoken_label(label)} timer is in the {where}. "
        f'Say "cancel the timer for {label} everywhere" to cancel it from here.'
    )


def _tool_true(raw: object) -> bool:
    """A tool call's boolean, strictly: ``True`` or the string "true". The
    Ollama route hands arguments over unconverted and small models quote
    booleans, so ``bool("false")`` — True — made a room's "cancel the timer"
    delete every plain timer in the house."""
    if raw is True:
        return True
    return isinstance(raw, str) and raw.strip().lower() == "true"


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if minutes:
        return f"{hours} hour{'s' if hours != 1 else ''} and {minutes} minute{'s' if minutes != 1 else ''}"
    return f"{hours} hour{'s' if hours != 1 else ''}"


# "10 minutes" → "10 minute": the duration as an adjective.
_PLURAL_UNIT_RE = re.compile(r"\b(second|minute|hour)s\b")


def duration_adjective(seconds: int) -> str:
    """``_format_duration`` in front of a noun: 600 → "10 minute" (as in
    "Your 10 minute timer is done.", "10 minute reminder"), 5400 → "1 hour
    and 30 minute"."""
    return _PLURAL_UNIT_RE.sub(r"\1", _format_duration(seconds))


class TimerHandler(Handler):
    name = "timer"
    # band rationale: after reminder (140) — see the "remind" collision note there.
    priority_band = 160
    display = HandlerDisplay(label="Timers", tone="info")
    requires_network = "no"

    tool_schema = {
        "name": "timer",
        "description": "Create, cancel, or check a countdown timer.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "cancel", "status"]},
                "duration_sec": {"type": "integer"},
                "label": {"type": "string"},
                "everywhere": {
                    "type": "boolean",
                    "description": "true only when the user explicitly asked to cancel it in every room or the whole house",
                },
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            # early_commit "B" (FastPath): a duration can go on ("10 minutes
            # … and 30 seconds") and a label can follow either ("… for the
            # pasta"); status is closed ("A").
            FastPath(_CREATE_RE, TimerHandler._create_from_match, early_commit="B"),
            FastPath(_CANCEL_RE, TimerHandler._cancel_from_match, early_commit="B"),
            FastPath(_STATUS_RE, TimerHandler._status_from_match, early_commit="A"),
        ]

    def offers_tool(self, transcript: str) -> bool:
        # Talk about a timer or reminder ("my timer didn't go off", "the
        # reminder in 10 minutes is for the oven") is no timer command. The
        # reminder handler withholds its tool on the same shapes, and with
        # only that one withheld qwen3:8b (2026-09-30) set a timer instead.
        return not TIMER_STATEMENT_RE.match(transcript)

    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        # execute() is normally reached via fast paths or tool-call —
        # this is just a safety net.
        return Response(
            text="I didn't catch a timer command.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        action = args.get("action")
        if action == "create":
            duration = int(args.get("duration_sec") or 0)
            if duration <= 0:
                return Response(
                    text="I need a duration to set a timer.",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
            return await self._create(
                duration_sec=duration,
                label=args.get("label"),
                ctx=ctx,
                session=session,
            )
        if action == "cancel":
            label = args.get("label")
            label = label.strip() if isinstance(label, str) else None
            return await self._cancel(
                label=label or None,
                everywhere=_tool_true(args.get("everywhere")),
                ctx=ctx,
                session=session,
            )
        if action == "status":
            return await self._status(ctx=ctx, session=session)
        return Response(
            text=f"I don't know how to {action} a timer.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    # Fast-path adapters.
    async def _create_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        duration_sec = parse_duration_seconds(m.group("duration"))
        if not duration_sec:
            # "timer for 0 minutes" fits the grammar but means nothing —
            # mirror the tool-call path's refusal rather than set a timer
            # that fires immediately.
            return Response(
                text="I need a duration to set a timer.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        label = (m.group("label") or None)
        if label:
            label = label.strip()
        return await self._create(
            duration_sec=duration_sec, label=label, ctx=ctx, session=session
        )

    async def _cancel_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        label = (m.group("label") or None)
        if label:
            label = label.strip() or None
        return await self._cancel(
            label=label,
            everywhere=m.group("everywhere") is not None,
            ctx=ctx,
            session=session,
        )

    async def _status_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._status(ctx=ctx, session=session)

    # Core actions.
    async def _create(
        self, *, duration_sec: int, label: str | None, ctx: Context, session: AsyncSession
    ) -> Response:
        repo = TimerRepository(session)
        # One instant for both ends: the TimerWatcher recovers the duration
        # as expires_at - created_at to say "Your 10 minute timer is done."
        now = utcnow()
        await repo.create(
            expires_at=now + timedelta(seconds=duration_sec),
            created_at=now,
            label=label,
            message=None,
            room_id=ctx.room_id,
        )
        spoken = _format_duration(duration_sec)
        text = (
            f"Timer set for {spoken}, labeled {label}." if label else f"Timer set for {spoken}."
        )
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name)

    async def _cancel(
        self,
        *,
        label: str | None,
        ctx: Context,
        session: AsyncSession,
        everywhere: bool = False,
    ) -> Response:
        # "Stop the timer" right after one went off in this room means "I
        # heard it": acknowledge that fire (which also stops it being
        # announced in rooms still waiting their turn) and cancel nothing —
        # also when another room, or this one, acknowledged it a moment
        # ago. Otherwise a kitchen "stop the timer" after the garage's timer
        # was announced there deleted the kitchen's own running timer. Only
        # a TIMER that went off counts: after a reminder, "cancel the
        # timer" still cancels this room's timer.
        if label is None and not everywhere and ctx.room_id is not None:
            from domovoi.timer_delivery import ACK_WITHIN_SEC, ack_recent_fire

            if await ack_recent_fire(
                session, ctx.room_id, within_sec=ACK_WITHIN_SEC, kind="timer",
            ) is not None:
                return Response(
                    text="Okay.", session_id=ctx.session_id, matched_handler=self.name,
                )
        repo = TimerRepository(session)
        deleted = await repo.cancel_by_label(
            label=label, room_id=ctx.room_id, house_wide=everywhere,
        )
        if deleted == 0:
            if label and not everywhere and ctx.room_id is not None:
                rooms = await repo.label_rooms(label)
                if rooms:
                    return Response(
                        text=_elsewhere_hint(label, rooms),
                        session_id=ctx.session_id,
                        matched_handler=self.name,
                    )
            return Response(
                text="I couldn't find a timer to cancel.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if label:
            text = f"Cancelled the {spoken_label(label)} timer."
        elif deleted == 1:
            text = "Cancelled the timer."
        else:
            text = f"Cancelled {deleted} timers."
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name)

    async def _status(self, *, ctx: Context, session: AsyncSession) -> Response:
        repo = TimerRepository(session)
        # The room's timer first; its reminder only when no timer runs.
        nxt = await repo.next_for_status(room_id=ctx.room_id)
        if nxt is None:
            return Response(
                text="No timers running.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        _id, expires_at, label, message = nxt
        is_reminder = message is not None
        remaining = int((expires_at - utcnow()).total_seconds())
        if remaining <= 0:
            return Response(
                text=f"That {'reminder' if is_reminder else 'timer'} is about to go off.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        spoken = _format_duration(remaining)
        if is_reminder and message.strip():
            # "4 minutes left on your reminder: call mom."
            text = f"{spoken} left on your reminder: {message}."
        elif is_reminder:
            # A reminder set with no task is labelled "10 minute reminder":
            # "8 minutes left on your 10 minute reminder.", not "... on the
            # 10 minute reminder timer."
            text = f"{spoken} left on your {label or 'reminder'}."
        elif label:
            text = f"{spoken} left on the {spoken_label(label)} timer."
        else:
            text = f"{spoken} left on the timer."
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name)
