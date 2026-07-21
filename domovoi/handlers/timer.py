from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.db.repositories import TimerRepository, utcnow
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

_UNIT_TO_SECONDS = {"second": 1, "minute": 60, "hour": 3600}

_CREATE_RE = re.compile(
    r"^(?:set a |)timer for (\d+) (second|minute|hour)s?(?: (?:for|called|named) (.+))?$"
)
_CANCEL_RE = re.compile(r"^(?:cancel|stop) (?:the |)timer(?: (?:for|called|named) (.+))?$")
_STATUS_RE = re.compile(r"^(?:how much time|how long) (?:left |)on (?:the |)timer$")


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
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_CREATE_RE, TimerHandler._create_from_match),
            FastPath(_CANCEL_RE, TimerHandler._cancel_from_match),
            FastPath(_STATUS_RE, TimerHandler._status_from_match),
        ]

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
            return await self._cancel(label=args.get("label"), ctx=ctx, session=session)
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
        amount = int(m.group(1))
        unit = m.group(2)
        label = (m.group(3) or None)
        if label:
            label = label.strip()
        duration_sec = amount * _UNIT_TO_SECONDS[unit]
        return await self._create(
            duration_sec=duration_sec, label=label, ctx=ctx, session=session
        )

    async def _cancel_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        label = (m.group(1) or None)
        if label:
            label = label.strip()
        return await self._cancel(label=label, ctx=ctx, session=session)

    async def _status_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._status(ctx=ctx, session=session)

    # Core actions.
    async def _create(
        self, *, duration_sec: int, label: str | None, ctx: Context, session: AsyncSession
    ) -> Response:
        repo = TimerRepository(session)
        expires_at = utcnow() + timedelta(seconds=duration_sec)
        await repo.create(
            expires_at=expires_at,
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
        self, *, label: str | None, ctx: Context, session: AsyncSession
    ) -> Response:
        repo = TimerRepository(session)
        deleted = await repo.cancel_by_label(label=label, room_id=ctx.room_id)
        if deleted == 0:
            return Response(
                text="I couldn't find a timer to cancel.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if label:
            text = f"Cancelled the {label} timer."
        elif deleted == 1:
            text = "Cancelled the timer."
        else:
            text = f"Cancelled {deleted} timers."
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name)

    async def _status(self, *, ctx: Context, session: AsyncSession) -> Response:
        repo = TimerRepository(session)
        nxt = await repo.next_active(room_id=ctx.room_id)
        if nxt is None:
            return Response(
                text="No timers running.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        _id, expires_at, label = nxt
        remaining = int((expires_at - utcnow()).total_seconds())
        if remaining <= 0:
            return Response(
                text="That timer is about to go off.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        spoken = _format_duration(remaining)
        text = (
            f"{spoken} left on the {label} timer." if label else f"{spoken} left on the timer."
        )
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name)
