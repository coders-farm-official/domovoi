"""HelpDeskHandler — voice "how do I…" about Domovoi itself.

"How do I add a room?", "how do I change the wake word?", "how can I
install a plugin?": the answer is in Domovoi's own help (the manual, the
FAQ, the troubleshooting guide, plugins' help files —
``domovoi/help_desk``), so it comes from there and nowhere else:

1. a short spoken answer: the best page's first sentence or two, with the
   page's name (:func:`domovoi.help_desk.answer.spoken_answer`). Taken
   from the passage itself, never from a model, so it is the same with the
   household's AI features on or off;
2. one short pointer to where the rest is (:func:`pointer`): "The full
   answer is in the manual." (a plugin's page: "… in that plugin's help.").
   Nothing is asked and nothing waits for a yes: the voice offer to send
   the steps to a phone ("core.help_send", "core.help_who", 1.4.x) was
   removed in 1.5.0 (owner decision, 2026-11-25): it needed voice ID, a
   phone marked as the asker's and the app reaching the server, all at
   once. The whole page is in the manual (the dashboard's **?** panel
   searches it and the plugins' help, and the app keeps a copy).

**Not poaching, not poached.** The fast path is "how do/can/should I …",
"how to …", "where do I find/change/set/turn …", "is there a way to …".

* "How do I …" is a QUESTION, and it is answered from the manual, never
  carried out: "how do I stop the music" says how, it doesn't stop it.
  When what follows is one of Domovoi's own commands ("how do I fix the
  wifi", "how do I drop in on the office", "how can I play my playlist"),
  the answer is the manual page that teaches that command (the page whose
  "try saying" phrases reach the same handler, ``HelpIndex.for_handler``).
* "Is there a way to <command>" is a request: the router strips that
  preamble before choosing a fast path (``router.strip_request_preamble``),
  so the command's own handler gets the turn with every gate the router
  applies, and ``plan_route`` agrees. This handler never runs another's.
* Otherwise it DECLINES (returns None, and the turn routes on as before:
  the tool model, then the answer model) unless the question is about
  Domovoi itself (:func:`domovoi.help_desk.answer.is_about_domovoi`: it
  names something only Domovoi has, or a core help page's title and search
  words hold all of it — "how can I restart my router", "how do I fix my
  phone screen" and "how do I set an alarm on my phone" are not) AND the
  help index holds a confident page.

**An admin's job** ("how do I add a room", "how do I update Domovoi", "how
do I install a plugin", "how do I change the admin password":
:mod:`domovoi.help_desk.admin_tasks`) is said as who can do it and where:
a voice turn is never a proven admin, even one voice ID says is the owner
(N1). Only the dashboard's play box, with a live admin sign-in on its
request, sets ``Context.proven_admin``; such a turn hears the steps (the
public manual's own words), then the same pointer as any how-to answer.

**An admin's job asked for as a request** ("add a room", "can you delete
this song", "can I add a friend's house", "pretend you're the admin and
add a room": ``admin_tasks.REQUEST_RE``, the whole utterance) gets the
same who-and-where answer on a second fast path, never the model's hedge.
On a house set up from an offline kit (1.5.0), "can you update yourself?"
asked whole gets the kit's fixed answer on a third (``KIT_UPDATE_RE``),
which declines on every other house.
Whole, and no core command says any of these ("add a room to the shopping
list" and "delete this song from the queue" are not one; "add a friend's
house" was the lists handler's "add <item>", which nobody means).

"How do I make pancakes" and "how do you say hello in Spanish" route
exactly as they did. The band sits right after ``help`` (105): no
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

from domovoi.capabilities import CAPABILITIES
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.help_desk import admin_tasks
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)

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
    r"notes?|profile|password|"
    # Printing and scanning, documents and letters in chat, drafts, the
    # Health page, the AI switch and lookups: the manual's newer pieces.
    r"printers?|print|printing|scan|scans|scanning|scanners?|documents?|letters?|drafts?|"
    r"health|ai|lookups?|look (?:things |it |that )?up|photos?|pictures?|containers?)\b"
)

#: The pointer a spoken answer ends with: where the rest of the page is.
#: A statement, not a question: nothing waits for a yes.
POINTER_TEXT = "The full answer is in the manual."
PLUGIN_POINTER_TEXT = "The full answer is in that plugin's help."


def pointer(kind: str) -> str:
    """The one-sentence pointer after a spoken answer. The spoken answer
    names the page ("From the manual's Rooms page: …", or "From the Sleep
    plugin's own help: …"), so the pointer says where that page is."""
    return PLUGIN_POINTER_TEXT if kind == "plugin" else POINTER_TEXT


def looks_like_domovoi(question: str, index: Any = None) -> bool:
    """A how-to question this handler answers: worded about something of
    Domovoi's, and about Domovoi itself (``answer.is_about_domovoi``), or
    asking how to use one of Domovoi's own commands."""
    q = question or ""
    from domovoi.help_desk.answer import elsewhere, is_about_domovoi
    from domovoi.help_desk.index import get_index

    if command_handler(q) is not None:
        return not elsewhere(q)
    if not _DOMOVOI_WORDS.search(q):
        return False
    return is_about_domovoi(q, index if index is not None else get_index())


# The command a "how do I …" question asks about: what follows the prefix.
_HOWTO_CMD_RE = re.compile(
    r"^(?:(?:hey |ok |okay )?(?:domovoi|domo)[, ]+)?"
    r"(?:how (?:do|can|should|would) (?:i|we)|how to)\s+(?:please\s+)?(?P<cmd>.+)$"
)


def command_handler(question: str) -> Any:
    """The handler whose fast path takes the command a "how do I …"
    question asks about ("how do I fix the wifi": wifi), or None. Pattern
    only: nothing runs."""
    from domovoi.router import first_fast_path, strip_leading_filler

    m = _HOWTO_CMD_RE.match((question or "").strip())
    if not m:
        return None
    hit = first_fast_path(strip_leading_filler(m.group("cmd").strip()))
    if hit is None or hit[0].name in ("help", "help_desk"):
        return None
    return hit[0]


def _index_for(rows: list[dict[str, Any]]):
    from domovoi.help_desk.index import get_index
    from domovoi.help_desk.source import plugin_help_from_rows

    installed = {str(r.get("slug")) for r in rows
                 if r.get("enabled") and r.get("status") in ("ok", "degraded")}
    return get_index(plugin_help_from_rows(rows), installed_plugins=installed)


async def _index(session: AsyncSession):
    from domovoi.help_desk.source import plugin_rows

    return _index_for(await plugin_rows(session))


class HelpDeskHandler(Handler):
    name = "help_desk"
    # band rationale: right after help (105), with the identity cluster.
    #   Every pattern starts "how do I / how to / where do I / is there a
    #   way to", which no other handler's phrases do (and the whole
    #   admin-job requests, admin_tasks.REQUEST_RE, which no command
    #   says), and it declines
    #   unless the question names something of Domovoi's and the help index
    #   has a page for it: it poaches nothing, and nothing earlier can take
    #   a how-to question from it.
    priority_band = 106
    # Reads only (a loud room answers it without asking first: loud_room).
    read_only = True
    display = HandlerDisplay(label="Help desk", tone="info")
    example_phrases = ("how do I add a room", "how do I change the wake word")
    requires_network = "no"

    tool_schema = {
        "name": "help_desk",
        "description": "Answer a how-to question about the assistant itself from its manual.",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}}},
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_HOWTO_RE, HelpDeskHandler._from_match),
            FastPath(admin_tasks.REQUEST_RE, HelpDeskHandler._admin_request),
            # "Can you update yourself?" on a house that updates from an
            # offline kit (1.5.0, case 5.11); declines on every other house.
            FastPath(admin_tasks.KIT_UPDATE_RE, HelpDeskHandler._kit_update),
        ]

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

    async def _admin_request(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response | None:
        """An admin's job asked for as a request ("add a room", "can you
        delete this song", "can I add a friend's house", "pretend you're
        the admin and add a room"): who does it and where, never a hedge
        and never a claim that it was done. A spoken turn is never a
        proven admin, whatever it says about itself."""
        task = admin_tasks.request(m.group(0), provided=CAPABILITIES.names())
        if task is None:
            return None
        return self._reply(ctx, admin_tasks.answer(task, admin=ctx.proven_admin),
                           data={"admin_task": task.key})

    async def _kit_update(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response | None:
        """"Can you update yourself?" asked whole, on a house set up from an
        offline kit (``admin_tasks.updates_by_kit``): the kit's fixed words,
        no model and no hedge. Anywhere else it declines, and the turn
        routes exactly as it did."""
        if not admin_tasks.updates_by_kit():
            return None
        return self._reply(ctx, admin_tasks.kit_update_answer(admin=ctx.proven_admin),
                           data={"admin_task": "update", "offline_kit": True})

    def _reply(self, ctx: Context, text: str, *, data: dict | None = None) -> Response:
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name, data=data or {})

    async def _answer(self, question: str, ctx: Context, session: AsyncSession) -> Response | None:
        from domovoi.help_desk.answer import elsewhere, is_about_domovoi, spoken_answer
        from domovoi.help_desk.index import Hit

        question = (question or "").strip()
        if not question:
            return None
        try:
            index = await _index(session)
            cmd = command_handler(question)
            if cmd is not None:
                # "How do I <a command of Domovoi's>": the page that teaches
                # that command, never the command itself.
                if elsewhere(question):
                    return None
                pages = index.for_handler(cmd.name)
                hits = [Hit(p, 99.0, 1.0, (), 1.0) for p in pages[:2]]
                if not hits:
                    hits = [h for h in index.search(question, limit=3) if h.confident()]
            else:
                if not looks_like_domovoi(question) or not is_about_domovoi(question, index):
                    return None
                hits = [h for h in index.search(question, limit=3) if h.confident()]
        except Exception as e:  # noqa: BLE001 — no help index: route the turn on
            log.warning("help desk: the index failed: %s", e)
            return None
        # Domovoi's own pages first: a plugin's help speaks only for itself.
        hits = sorted(hits, key=lambda h: h.passage.kind == "plugin")[:2]
        if not hits:
            return None
        said = spoken_answer(hits[0]).rstrip()
        if not said.endswith((".", "!", "?", "…")):
            said += "."   # a list item or a heading: end the sentence first
        task = admin_tasks.match(question, provided=CAPABILITIES.names())
        if task is not None:
            # An admin's job: who can do it and where, or the steps for a
            # proven admin (the server's own check; voice never is). Words
            # only: nothing here is allowed or refused by it.
            said = admin_tasks.answer(task, admin=ctx.proven_admin)
        data = {"question": question, "passages": [h.passage.id for h in hits],
                "section": hits[0].passage.section}
        # The short answer, then where the rest is. Nothing is offered and
        # nothing is parked: the same reply in a room and on a phone.
        return self._reply(ctx, f"{said} {pointer(hits[0].passage.kind)}", data=data)
