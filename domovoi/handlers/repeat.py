"""RepeatHandler — re-speak the assistant's previous response verbatim.

Triggered when the user couldn't hear or missed what was said ("repeat
that", "say that again", "what did you say"). Re-emits the prior turn's
``assistant_text`` unchanged so the user can crank the volume and hear
it again without having to remember the original question.

Resolution order for "what to repeat":

  1. ``sessions.context.last_assistant_response`` — populated on every
     routed turn by ``SessionRepository.record_exchange``. Same field
     DoubleCheckHandler reads; the obvious source of truth when the
     repeat happens inside the same active session.
  2. ``conversation_log`` fallback — latest non-empty ``assistant_text``
     scoped to the originating ``room_id`` within the trailing 10 min
     window. Covers the case where the session quietly aged out between
     turns (default session inactivity ~minutes; user comes back, says
     "say that again"). Room-scoped so a "repeat" in the kitchen never
     surfaces something said in the garage.
  3. Nothing recent in either → friendly "I don't have anything recent
     to repeat" message.

Fully local; declares ``requires_network="no"``.

Notes on what is intentionally NOT done:

* The bare words "again", "what", "huh" are NOT matched — too high a
  false-positive rate against music ("play that again"), wifi
  diagnostics ("what's wrong"), and intercom verbs.
* ``expect_followup`` from the prior turn isn't preserved — that flag
  isn't currently captured in ``sessions.context``, and adding it just
  for the rare "repeat my question" case isn't worth the schema churn.
  If a user repeats a bot question, they'll need to re-trigger with the
  wake word as usual.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.db.repositories import ConversationLogRepository, SessionRepository
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


# Window for the conversation_log fallback. 10 minutes is the sweet spot —
# long enough to survive a session timing out between turns, short enough
# that "repeat that" said an hour later doesn't dredge up the wrong
# response. Tunable later if real-world use shows it's too tight.
_FALLBACK_WINDOW_SEC = 600


# ─── Fast-path regex ──────────────────────────────────────────────────────
# Router lower-cases + strips trailing punctuation before dispatching, so
# patterns are anchored on a normalized lower-case transcript. Tightly
# anchored — must NOT poach phrasings that include "say" / "again" /
# "what" but mean something different ("play that again" → music,
# "what's wrong" → wifi, "tell the kitchen X" → intercom).

_REPEAT_RE = re.compile(
    r"^(?:"
    # repeat (alone) / repeat that / repeat it / repeat please / repeat again
    r"repeat(?: (?:that|it|please|again))?"
    # please repeat / please repeat that / please repeat it
    r"|please repeat(?: (?:that|it))?"
    # say that again / say it again
    r"|say (?:that|it) again"
    # can/could/would you (please) (repeat / say that again)
    r"|(?:can|could|would) you (?:please )?(?:repeat(?: (?:that|it))?|say (?:that|it) again)"
    # what did/'d you (just) say / what'd you say
    r"|what(?: did| 'd|'d) you (?:just )?say"
    # what was that
    r"|what was that"
    # come again
    r"|come again"
    # one more time / once more
    r"|(?:one more time|once more)"
    # i didn't (hear|catch) (that|you|what you said)
    r"|i (?:didn't|didnt|did not) (?:hear|catch) (?:that|you|what you said)"
    # i missed that
    r"|i missed that"
    r")$"
)


class RepeatHandler(Handler):
    name = "repeat"
    # band rationale: clusters with double_check (190) — both operate on the previous
    #   turn's assistant_text and sit above anything that could brush
    #   against "say" / "again" / "what was that" phrasings. Tightly
    #   anchored regexes still won't poach "play that again" (music).
    priority_band = 180
    display = HandlerDisplay(label="Repeat", tone="info")
    requires_network = "no"

    tool_schema = {
        "name": "repeat",
        "description": (
            "Re-speak the assistant's most recent spoken response verbatim. "
            "Use when the user couldn't hear or missed the previous answer "
            "('repeat that', 'say that again', 'what did you say', 'I "
            "didn't catch that'). Takes no arguments — the prior response "
            "is recovered from session context."
        ),
        "parameters": {"type": "object", "properties": {}},
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_REPEAT_RE, RepeatHandler._from_match),
        ]

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._repeat(ctx, session)

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._repeat(ctx, session)

    # ─── Fast-path adapter ────────────────────────────────────────────
    async def _from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._repeat(ctx, session)

    # ─── Core flow ────────────────────────────────────────────────────
    async def _repeat(self, ctx: Context, session: AsyncSession) -> Response:
        last = await self._read_session_last_response(ctx, session)
        if not last:
            last = await self._read_room_recent_response(ctx, session)
        if not last:
            return Response(
                text="I don't have anything recent to repeat.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return Response(
            text=last,
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    # ─── Helpers ──────────────────────────────────────────────────────
    async def _read_session_last_response(
        self, ctx: Context, session: AsyncSession
    ) -> str | None:
        if ctx.session_id is None:
            return None
        try:
            ctx_data = await SessionRepository(session).get_context(ctx.session_id)
        except Exception as e:
            log.warning("Repeat: get_context failed: %s", e)
            return None
        if not isinstance(ctx_data, dict):
            return None
        last = ctx_data.get("last_assistant_response")
        if not isinstance(last, str) or not last.strip():
            return None
        return last.strip()

    async def _read_room_recent_response(
        self, ctx: Context, session: AsyncSession
    ) -> str | None:
        if not ctx.room_id:
            return None
        try:
            return await ConversationLogRepository(
                session
            ).latest_assistant_text_in_room(
                room_id=ctx.room_id, within_seconds=_FALLBACK_WINDOW_SEC
            )
        except Exception as e:
            log.warning("Repeat: conversation_log fallback failed: %s", e)
            return None
