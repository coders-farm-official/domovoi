"""HelpHandler — "what can you do", "what can I say", "help".

Before this handler those reached the answer model, which made abilities
up ("control smart home devices, and even order groceries") or answered
like a counsellor ("You've got a lot on your mind, huh?"): a household's
first question got a wrong answer (manual-content findings, 2026-10-17).

The answer comes from the live handler registry — the same list behind GET
/v1/handlers and the dashboard's user manual (``/api/capabilities/manual``)
— so it is only ever what this server can do: a few areas, each with one
thing to say (``Handler.example_phrases``), the household's enabled plugins
included, then "the rest is in the manual". In memory, no database, no
model, offline.

Telling the question apart from a real one: every pattern is the WHOLE
utterance (``^...$``), with nothing after the question but "for me" / "for
us". "What can you do about the noise", "what can I say to my boss" or
"help me with my homework" don't match, so they route exactly as before
(the tool model, then the answer model). The tool is never offered to the
routing model (``offers_tool``): it would only be bait for those questions.
"""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.handlers.base import FastPath, Handler, HandlerDisplay, example_phrases_of
from domovoi.models import Context, Intent, Response

_FOR_ME = r"(?: for (?:me|us))?"
_HELP_RE = re.compile(
    r"^(?:"
    # "what can you do", "what else can you do", "what all can you do (for me)"
    rf"what (?:else |all |things |sort of things |kind of things )?can you do{_FOR_ME}"
    rf"|what (?:else )?(?:are you able to do|are you capable of|do you do){_FOR_ME}"
    rf"|what can (?:you|domovoi) help (?:me |us )?with"
    rf"|what can domovoi do{_FOR_ME}"
    rf"|tell me what you can do"
    rf"|what are (?:all )?your (?:features|abilities|skills|commands)"
    rf"|what (?:commands|things) do you (?:know|understand|have)"
    rf"|list (?:your |the )?(?:commands|features|skills)"
    # "what can I say", "what can I ask (you)", "what should I say"
    rf"|what (?:else )?(?:can|should|do) i (?:say|ask(?: you)?)"
    rf"|what can i ask you to do"
    rf"|how do i use (?:you|domovoi)"
    # "help", "help me", "help please" (not "help me with X": a question)
    rf"|help(?: me| us)?(?: please)?"
    r")$"
)

# The areas the answer names, in order, when the handler is registered and
# has a phrase. A plugin's handlers follow (up to _MAX_PLUGINS of them).
_CORE_AREAS = ("timer", "reminder", "music", "calendar", "news")
_MAX_PLUGINS = 2


class HelpHandler(Handler):
    name = "help"
    # band rationale: identity cluster with dismiss/voice_profile. Every
    #   pattern is a whole closed question about the assistant itself, so it
    #   poaches nothing; early, so no later band's pattern can take "what
    #   can you do".
    priority_band = 105
    display = HandlerDisplay(label="Help", tone="info")
    example_phrases = ("what can you do", "what can I say")
    requires_network = "no"

    tool_schema = {
        "name": "help",
        "description": "Say a few of the things the assistant can do, with an example each.",
        "parameters": {"type": "object", "properties": {}},
    }

    def __init__(self) -> None:
        # No early commit: "what can you do … about the noise" is a question.
        self.fast_paths = [FastPath(_HELP_RE, HelpHandler._from_match)]

    def offers_tool(self, transcript: str) -> bool:
        # Never offered: the fast path answers the questions this is for,
        # and shown the schema the tool model would reach for it on a real
        # question ("what can you do about a leaky tap").
        return False

    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        return self._answer(ctx)

    async def execute_from_tool(self, args: dict, ctx: Context, session: AsyncSession) -> Response:
        return self._answer(ctx)

    async def _from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._answer(ctx)

    def _answer(self, ctx: Context) -> Response:
        return Response(
            text=capabilities_summary(),
            session_id=ctx.session_id,
            matched_handler=self.name,
        )


def _areas() -> list[tuple[str, str]]:
    """(label, phrase) for the areas the answer names: the core ones in
    ``_CORE_AREAS`` order, then enabled plugins', from the live registry."""
    from domovoi.handlers import HANDLER_BY_NAME, HANDLERS

    out: list[tuple[str, str]] = []
    for name in _CORE_AREAS:
        h = HANDLER_BY_NAME.get(name)
        if h is None or getattr(h, "plugin_slug", None) is not None:
            continue
        phrases = example_phrases_of(h)
        if phrases:
            out.append((h.display.label, phrases[0]))
    plugins = 0
    for h in HANDLERS:
        if getattr(h, "plugin_slug", None) is None or plugins >= _MAX_PLUGINS:
            continue
        try:
            phrases = example_phrases_of(h)
            label = h.display.label
        except Exception:  # noqa: BLE001 - a broken plugin never breaks the answer
            continue
        if phrases and label:
            out.append((label, phrases[0]))
            plugins += 1
    return out


def capabilities_summary() -> str:
    """The spoken answer: a few areas with one thing to say for each, then
    where the rest is."""
    areas = _areas()
    if not areas:
        return (
            "I can do quite a lot around the house. The manual on the "
            "dashboard lists everything, with things to say."
        )
    parts = [f'{label}: try "{phrase}".' for label, phrase in areas]
    return (
        "Here are a few things I can do. "
        + " ".join(parts)
        + " The rest is in the manual, on the dashboard."
    )
