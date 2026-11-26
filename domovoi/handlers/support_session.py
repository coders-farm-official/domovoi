"""SupportSessionHandler: "end support session", said in the room.

A satellite support session (an admin's shell on the room's device,
domovoi/support_sessions.py) can always be ended by the room itself:
satellites have no button for it, so the room's way is its voice. Ending
only removes power, so nothing more than being in the room is asked.

Fully local; ``requires_network="no"``. The usual intents_log row is
written by the router like any turn's.
"""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

_END_RE = re.compile(
    r"^(?:please )?(?:end|stop|close|finish|cancel|quit) (?:the |this )?support session(?: now)?$"
)

NONE_OPEN = "There's no support session open here."


class SupportSessionHandler(Handler):
    name = "support_session"
    # band rationale: right after dismiss (100) and ahead of help (105):
    # a closed, fully anchored phrase ("end support session") that nothing
    # else claims, and the room's way to end an admin's shell must never be
    # poached by a greedier handler.
    priority_band = 101
    display = HandlerDisplay(label="Support session", tone="device")
    example_phrases = ("end support session",)
    requires_network = "no"

    tool_schema = {
        "name": "support_session",
        "description": (
            "End the support session (an admin's remote shell) open on this "
            "room's device. Use for 'end support session', 'stop the support "
            "session', 'close the support session'. Takes no arguments."
        ),
        "parameters": {"type": "object", "properties": {}},
    }

    def __init__(self) -> None:
        self.fast_paths = [FastPath(_END_RE, SupportSessionHandler._from_match)]

    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        return await self._end(ctx)

    async def execute_from_tool(self, args: dict, ctx: Context, session: AsyncSession) -> Response:
        return await self._end(ctx)

    async def _from_match(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response:
        return await self._end(ctx)

    async def _end(self, ctx: Context) -> Response:
        from domovoi import support_sessions

        ended = 0
        if ctx.app is not None:
            # The reply below is the room's answer, so the end line is not
            # said a second time.
            ended = await support_sessions.get_manager(ctx.app).end_in_room(
                ctx.room_id, "voice", announce=False)
        return Response(
            text=support_sessions.VOICE_END_REPLY if ended else NONE_OPEN,
            session_id=ctx.session_id,
            matched_handler=self.name,
            expect_followup=False,
        )
