"""HouseFactsHandler — privacy and "can you …?" questions, answered from
this house's real state (1.4.1, N2 with N7's capability answers).

"Are you recording me?", "how long do you keep what I say?", "does
anything leave the house?", "did you use the internet for that?", and
"can you order pizza?" / "can you set timers?" for the closed list of
abilities in :data:`domovoi.house_facts.CAPABILITIES`, and the trust
questions no model may answer: "what's the admin password / household
token / setup code?" (also behind "ignore your rules and …") gets "I
can't share that." and where an admin manages it; "can I sign in as
admin?" / "make me admin" / "am I admin?" get where the admin signs in,
and the turn's real state (a voice is never an admin), never a claimed
sign-in; "which plugins do you have?" lists them by name (the one
answer that may: the person asked); "who can approve this?" / "who's the
admin?" / "who set this up?" / "who can change settings?" get who does an
admin's job and where, never a name (Domovoi doesn't record who the admin
is) and never an ownership joke; "who's in this house?" / "who lives
here?" / "who's home?" get who Domovoi knows (the People list), never who
is home. The facts and the
wording live in :mod:`domovoi.house_facts`; this is only its voice. No
model, ever.

Not poaching, not poached: every pattern is a WHOLE question (``^…$``)
about the assistant itself, so a command ("record a voice memo", "set a
timer for 10 minutes", "play …") never matches. A "can you" question
reaches this fast path only when the router kept it whole
(``router.strip_leading_filler`` → ``house_facts.kept_question``): the
words after "can you" are a capability-table X that no command takes.
"Can you set a timer for 10 minutes" still sets it, and an X the table
doesn't hold ("can you juggle") routes exactly as before, to the Q&A
model and its hedge. The tool is never offered to the routing model.
"""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi import house_facts
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response


class HouseFactsHandler(Handler):
    name = "house_facts"
    # band rationale: identity cluster, right after help (105) and help_desk
    #   (106). Every pattern is a whole closed question about the assistant
    #   itself ("are you recording me", "can you order pizza"), which no
    #   command starts with, so it poaches nothing; early, so no later
    #   band's pattern can take "do you keep what I say".
    priority_band = 107
    display = HandlerDisplay(label="House facts", tone="info")
    example_phrases = ("are you recording me", "can you order pizza")
    requires_network = "no"

    tool_schema = {
        "name": "house_facts",
        "description": "Say what this house keeps, sends and can do, from its settings.",
        "parameters": {"type": "object", "properties": {}},
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(house_facts.QUESTION_RE, HouseFactsHandler._question),
            FastPath(house_facts.CAP_RE, HouseFactsHandler._capability),
            FastPath(house_facts.SECRET_RE, HouseFactsHandler._secret),
            FastPath(house_facts.ADMIN_QUESTION_RE, HouseFactsHandler._admin),
            FastPath(house_facts.PLUGINS_QUESTION_RE, HouseFactsHandler._plugins),
            # "Who can approve this?" / "who's the admin?" / "who set this
            # up?": facts, before voice_profile's ownership jokes (110).
            FastPath(house_facts.ADMIN_WHO_RE, HouseFactsHandler._admin_who),
            # "Who's in this house?" / "who's home?": who Domovoi knows (the
            # People list), never who is home (owner ruling, case 8.10).
            FastPath(house_facts.HOUSEHOLD_RE, HouseFactsHandler._household),
        ]

    def offers_tool(self, transcript: str) -> bool:
        # Never offered: the fast paths are the whole surface, and the tool
        # would be bait for every other question.
        return False

    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        return Response(text=house_facts.CANT_CHECK, session_id=ctx.session_id,
                        matched_handler=self.name)

    async def execute_from_tool(self, args: dict, ctx: Context, session: AsyncSession) -> None:
        return None

    async def _facts(self, ctx: Context) -> house_facts.HouseFacts:
        return await house_facts.read(room_id=ctx.room_id, device_kind=ctx.device_kind, app=ctx.app)

    async def _question(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response | None:
        qid = m.lastgroup
        if qid is None:
            return None
        if qid == "internet_for_that":
            text = house_facts.D3_VOICE
        else:
            text = house_facts.answer(qid, await self._facts(ctx))
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name)

    async def _secret(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response | None:
        which = house_facts.secret_asked(m.group(0))
        if which is None:
            return None
        return Response(text=house_facts.secret_answer(which), session_id=ctx.session_id,
                        matched_handler=self.name)

    async def _admin(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response | None:
        kind = house_facts.admin_question(m.group(0))
        if kind is None:
            return None
        # A spoken turn is never a proven admin; only the dashboard's play
        # box, with a live admin sign-in on its request, is.
        text = house_facts.admin_answer(kind, chat=False, admin=ctx.proven_admin)
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name)

    async def _admin_who(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response | None:
        kind = house_facts.admin_who(m.group(0))
        if kind is None:
            return None
        text = house_facts.admin_who_answer(kind, admin=ctx.proven_admin)
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name)

    async def _household(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response | None:
        kind = house_facts.household_question(m.group(0))
        if kind is None:
            return None
        text = await house_facts.household_reply(
            kind, scope=session, guest=house_facts.voice_turn_is_guest(ctx))
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name)

    async def _plugins(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response | None:
        return Response(text=await house_facts.plugins_answer(), session_id=ctx.session_id,
                        matched_handler=self.name)

    async def _capability(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response | None:
        found = house_facts.capability_of(m.group(0))
        if found is None:
            return None
        # A proven admin (the play box with a live sign-in; voice never is)
        # hears the steps in a "how to get it" clause (N1).
        text = await house_facts.capability_answer(found[0], await self._facts(ctx),
                                                    admin=ctx.proven_admin)
        if text is None:
            return None
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name)
