"""HelpDeskHandler — voice "how do I…" about Domovoi itself.

"How do I add a room?", "how do I change the wake word?", "how can I
install a plugin?": the answer is in Domovoi's own help (the manual, the
FAQ, the troubleshooting guide, plugins' help files —
``domovoi/help_desk``), so it comes from there and nowhere else:

1. a short spoken answer: the best page's first sentence or two, with the
   page's name (:func:`domovoi.help_desk.answer.spoken_answer`). Taken
   from the passage itself, never from a model, so it is the same with the
   household's AI features on or off;
2. "Want me to send the steps to your phone?" (``core.help_send``). Yes
   sends them to the ASKER only (owner decisions, 2026-10-21):

   * recognised by voice → the phones marked "This phone belongs to
     <them>" (:mod:`domovoi.help_desk.delivery`), as a home-server chat
     "Help: …" and a notification through the app's alerts;
   * not recognised → "Who should I send it to?" (``core.help_who``, a
     free-text choice); a name the household knows sends it there, and
     anything else, or no answer at all, sends nothing;
   * recognised but no phone marked as theirs → said plainly, with where
     to set it (the app's Settings → Connection → "This phone belongs
     to"); nothing is guessed;
   * never a broadcast: there is no "everyone" here.

**Not poaching, not poached.** The fast path is "how do/can/should I …",
"how to …", "where do I find/change/set/turn …", "is there a way to …". It
DECLINES (returns None, and the turn routes on as before: the tool model,
then the answer model) unless the question names something Domovoi has
(a room, the dashboard, a plugin, a timer… :func:`looks_like_domovoi`) AND
the help index holds a confident page for it (most of the question's own
words). "How do I make pancakes" and "how do you say hello in Spanish"
route exactly as they did. The band sits right after ``help`` (105): no
earlier band has a "how do I" pattern ("how do I use you" is ``help``'s,
whole), and every later handler's phrases start differently, which
``test_help_desk_voice.py`` checks against every example phrase and the
manual's "try saying" phrases. The tool is never offered to the routing
model (``offers_tool``).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.confirmations import request_confirmation
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)

SEND_KIND = "core.help_send"
WHO_KIND = "core.help_who"

_HOWTO_RE = re.compile(
    r"^(?:(?:hey |ok |okay )?(?:domovoi|domo)[, ]+)?"
    # "how do I use you" is help's (105), whole.
    r"(?!how do i use (?:you|domovoi)$)"
    r"(?P<q>(?:how (?:do|can|should|would) (?:i|we)|how to|where (?:do|can) i (?:find|change|set|turn|see|add)"
    r"|is there a way to|what's the way to|what is the way to) .{3,})$"
)

_DOMOVOI_WORDS = re.compile(
    r"\b(?:domovoi|domo|dashboard|app|satellite|satellites|room|rooms|wake word|plugin|plugins|"
    r"settings?|manual|update|upgrade|server|chat|voice|voices|timer|timers|reminder|reminders|"
    r"alarm|music|playlist|podcast|podcasts|audiobook|audiobooks|radio|station|calendar|files?|"
    r"household|admin|pair|token|phone|internet|model|models|backup|restart|guest|people|news|"
    r"videos?|intercom|drop in|lyrics|greeting|wi-?fi|speaker|microphone|mic|room mode|queue|"
    r"notes?|profile|password)\b"
)

NO_PHONE_TEXT = (
    "There's no phone marked as {who} yet, so I haven't sent anything. In the Domovoi app, "
    "open Settings, then Connection, and set This phone belongs to."
)


def looks_like_domovoi(question: str) -> bool:
    return bool(_DOMOVOI_WORDS.search(question or ""))


def _index_for(rows: list[dict[str, Any]]):
    from domovoi.help_desk.index import get_index
    from domovoi.help_desk.source import plugin_help_from_rows

    installed = {str(r.get("slug")) for r in rows
                 if r.get("enabled") and r.get("status") in ("ok", "degraded")}
    return get_index(plugin_help_from_rows(rows), installed_plugins=installed)


async def _index(session: AsyncSession):
    from domovoi.help_desk.source import plugin_rows

    return _index_for(await plugin_rows(session))


def steps_text(question: str, passages: list[Any]) -> str:
    """The chat message the phone opens: the pages, whole, with links."""
    parts = [f"Here's what Domovoi's help says about *{question.strip().rstrip('?')}*:"]
    for p in passages:
        link = f"\n\n[Open {p.title}]({p.link})" if p.link else ""
        parts.append(f"**{p.section}**\n\n{p.text}{link}")
    return "\n\n".join(parts)


_DECLINE_RE = re.compile(
    r"^(?:no|nope|nah|nobody|no one|noone|none|never mind|nevermind|cancel|forget it|don't|do not|"
    r"don't send it|no thanks|no thank you|stop)\b"
)
_LEAD_RE = re.compile(r"^(?:(?:please|um|uh|well|oh|ok|okay)[, ]+)*"
                      r"(?:send (?:it|them|the steps) to|send to|to|it's for|its for|for|it's|its)\s+")
_TAIL_RE = re.compile(r"(?:'s phone|s phone| phone|,? please)$")
_SELF_RE = re.compile(r"^(?:me|myself|mine|my phone|i am|i'm)$")


def parse_who(transcript: str) -> tuple[str, str]:
    """``("decline", "")``, ``("self", "")``, ``("name", name)`` or
    ``("pass", "")`` (not an answer to the question)."""
    t = " ".join((transcript or "").lower().replace("?", "").replace(".", "").split())
    if not t:
        return ("decline", "")
    if _DECLINE_RE.match(t):
        return ("decline", "")
    t = _LEAD_RE.sub("", t).strip()
    t = _TAIL_RE.sub("", t).strip()
    if _SELF_RE.match(t):
        return ("self", "")
    if not t or len(t.split()) > 3 or not re.fullmatch(r"[\w' -]+", t):
        return ("pass", "")
    return ("name", t)


class HelpDeskHandler(Handler):
    name = "help_desk"
    # band rationale: right after help (105), with the identity cluster.
    #   Every pattern starts "how do I / how to / where do I / is there a
    #   way to", which no other handler's phrases do, and it declines
    #   unless the question names something of Domovoi's and the help index
    #   has a page for it: it poaches nothing, and nothing earlier can take
    #   a how-to question from it.
    priority_band = 106
    display = HandlerDisplay(label="Help desk", tone="info")
    example_phrases = ("how do I add a room", "how do I change the wake word")
    requires_network = "no"
    confirmation_kinds = (SEND_KIND, WHO_KIND)
    choice_kinds = (WHO_KIND,)

    tool_schema = {
        "name": "help_desk",
        "description": "Answer a how-to question about the assistant itself from its manual.",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}}},
    }

    def __init__(self) -> None:
        self.fast_paths = [FastPath(_HOWTO_RE, HelpDeskHandler._from_match)]

    def offers_tool(self, transcript: str) -> bool:
        # Never offered: the fast path answers the how-to questions, and the
        # schema would be bait for every other "how" question.
        return False

    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        r = await self._answer(intent.transcript or "", ctx, session)
        return r or self._reply(ctx, "The manual doesn't cover that.")

    async def execute_from_tool(self, args: dict, ctx: Context, session: AsyncSession) -> Response | None:
        return await self._answer(str(args.get("question") or ""), ctx, session)

    async def _from_match(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response | None:
        return await self._answer(m.group("q"), ctx, session)

    def _reply(self, ctx: Context, text: str, *, followup: bool = False, data: dict | None = None) -> Response:
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name,
                        expect_followup=followup, data=data or {})

    async def _answer(self, question: str, ctx: Context, session: AsyncSession) -> Response | None:
        from domovoi.help_desk.answer import spoken_answer

        question = (question or "").strip()
        if not question or not looks_like_domovoi(question):
            return None
        try:
            index = await _index(session)
            hits = [h for h in index.search(question, limit=2) if h.confident()]
        except Exception as e:  # noqa: BLE001 — no help index: route the turn on
            log.warning("help desk: the index failed: %s", e)
            return None
        if not hits:
            return None
        said = spoken_answer(hits[0])
        data = {"question": question, "passages": [h.passage.id for h in hits],
                "section": hits[0].passage.section}
        if ctx.from_phone or ctx.session_id is None:
            # A phone turn is on the phone already; a turn with no session
            # can't hear a yes. The page is named either way.
            return self._reply(ctx, said, data=data)
        await request_confirmation(
            session, ctx.session_id, kind=SEND_KIND, handler=self.name,
            data={**data, "person_id": ctx.person_id},
            prompt="Want me to send the steps to your phone?",
        )
        return self._reply(ctx, said + " Want me to send the steps to your phone?", followup=True, data=data)

    # ── the parked questions ─────────────────────────────────────────────

    async def handle_confirmation(
        self, kind: str, data: dict, affirmative: bool, ctx: Context, session: AsyncSession
    ) -> Response:
        if not affirmative:
            return self._reply(ctx, "Okay, I won't send it.")
        if kind == WHO_KIND:
            # A bare "yes" to "who should I send it to?" names nobody.
            return self._reply(ctx, "I need a name to send it to, so I haven't sent anything.")
        person_id = data.get("person_id") or ctx.person_id
        if not person_id:
            await request_confirmation(
                session, ctx.session_id, kind=WHO_KIND, handler=self.name,
                data={k: v for k, v in data.items() if k not in ("handler", "kind")},
                prompt="Who should I send it to?",
            )
            return self._reply(ctx, "Who should I send it to?", followup=True)
        return await self._send(int(person_id), data, ctx, session, asker=True)

    async def handle_choice_reply(
        self, kind: str, data: dict, transcript: str, ctx: Context, session: AsyncSession
    ) -> Response | None:
        from domovoi.help_desk.delivery import people_named

        if kind != WHO_KIND:
            return None
        action, name = parse_who(transcript)
        if action == "pass":
            return None
        if action == "decline":
            return self._reply(ctx, "Okay, I won't send it.")
        if action == "self":
            if ctx.person_id:
                return await self._send(int(ctx.person_id), data, ctx, session, asker=True)
            return self._reply(ctx, "I can't tell who you are by your voice yet, so I won't guess. "
                                    "Nothing was sent.")
        people = await people_named(session, name)
        if not people:
            return self._reply(ctx, f"I don't know anyone called {name}, so I haven't sent anything.")
        if len(people) > 1:
            return self._reply(ctx, f"More than one person here is called {people[0][1]}, so I won't "
                                    "guess. Nothing was sent.")
        return await self._send(people[0][0], data, ctx, session, asker=False)

    async def _send(self, person_id: int, data: dict, ctx: Context, session: AsyncSession, *,
                    asker: bool) -> Response:
        from domovoi.help_desk import delivery

        question = str(data.get("question") or "")
        name = await delivery.person_name(session, person_id)
        if name is None:
            return self._reply(ctx, "I don't know who that is any more, so I haven't sent anything.")
        phones = await delivery.phones_of(session, person_id)
        if not phones:
            who = "yours" if asker else f"{name}'s"
            return self._reply(ctx, NO_PHONE_TEXT.format(who=who))
        index = await _index(session)
        # The pages the spoken answer came from; if the index changed since
        # (a plugin, a new manual), the question's pages today.
        passages = [p for p in (index.get(pid) for pid in data.get("passages") or []) if p is not None]
        if not passages:
            passages = [h.passage for h in index.search(question, limit=2) if h.confident()]
        if not passages:
            return self._reply(ctx, "I couldn't find those steps again, so I haven't sent anything.")
        d = await delivery.deliver(session, person_id=person_id, question=question,
                                   steps=steps_text(question, passages), phones=phones)
        where = "your phone" if asker else f"{name}'s phone"
        return self._reply(ctx, f"Sent to {where}. It's in Chat as {d.title}.",
                           data={"delivery_id": d.id, "thread_id": d.thread_id})
