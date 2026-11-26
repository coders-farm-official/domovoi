"""HelpHandler — "what can you do", "what can I say", "help".

Before this handler those reached the answer model, which made abilities
up ("control smart home devices, and even order groceries") or answered
like a counsellor ("You've got a lot on your mind, huh?"): a household's
first question got a wrong answer (manual-content findings, 2026-10-17).

The answer comes from the live handler registry — the same list behind GET
/v1/handlers and the dashboard's user manual (``/api/capabilities/manual``)
— so it is only ever what this server can do: a few areas, each with one
thing to say (``Handler.example_phrases``), the household's enabled plugins
included, then "the rest is in the manual". No model, offline; one
database read (the Home location).

Only what this house can do right now: an area whose prerequisite is a
setting (the internet answer, AI features, the Home location) is left out
when the setting rules it out (``_NEEDS``, ``HouseState``). With the
internet set to No, news isn't offered (capability finding N13).

Telling the question apart from a real one: every pattern is the WHOLE
utterance (``^...$``), with nothing after the question but "for me" / "for
us". "What can you do about the noise", "what can I say to my boss" or
"help me with my homework" don't match, so they route exactly as before
(the tool model, then the answer model). The tool is never offered to the
routing model (``offers_tool``): it would only be bait for those questions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

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

log = logging.getLogger(__name__)

# The areas the answer names, in order, when the handler is registered and
# has a phrase and this house can use it right now (``_NEEDS``). A
# plugin's handlers follow (up to _MAX_PLUGINS of them).
_CORE_AREAS = ("timer", "reminder", "music", "calendar", "lists", "news")
_MAX_PLUGINS = 2

# What a core area needs beyond being registered. Every area not named
# here works on any house (no internet, AI off, no Home location).
# "internet": the internet answer isn't No and the server is online now
# (news with no internet only reads what it already has).
_NEEDS: dict[str, frozenset[str]] = {
    "news": frozenset({"internet"}),
}

# Weather isn't a handler: core answers it with a web lookup read by the
# answer model, for the Home location (domovoi/weather.py). So it needs
# all three; a weather plugin, when installed, is offered as a plugin.
_WEATHER = ("Weather", "what's the weather")
_WEATHER_NEEDS = frozenset({"internet", "ai", "home"})


@dataclass(frozen=True)
class HouseState:
    """What this house can do right now, for the answer's filter."""

    internet: bool = True   # internet answer isn't No, and online now
    ai: bool = True         # AI features on
    home: bool = False      # a Home location is set


def _settings_state(*, online: bool = True, home: bool | None = None) -> HouseState:
    from domovoi import ai_features, egress, home_location

    if home is None:
        home = bool((home_location.cached() or {}).get("name"))
    return HouseState(
        internet=online and egress.internet_allowed(),
        ai=ai_features.enabled(),
        home=home,
    )


async def house_state(ctx: Context, session: AsyncSession) -> HouseState:
    """The settings the offered areas depend on, read now."""
    from domovoi import home_location

    try:
        home = bool((await home_location.load(session) or {}).get("name"))
    except Exception as e:  # noqa: BLE001 - an unreadable row: the last copy
        log.debug("help: home location read failed: %s", e)
        home = None
    return _settings_state(online=ctx.online, home=home)


def _has(state: HouseState, needs: frozenset[str]) -> bool:
    return all(getattr(state, n) for n in needs)


class HelpHandler(Handler):
    name = "help"
    # band rationale: identity cluster with dismiss/voice_profile. Every
    #   pattern is a whole closed question about the assistant itself, so it
    #   poaches nothing; early, so no later band's pattern can take "what
    #   can you do".
    priority_band = 105
    # Reads only (a loud room answers it without asking first: loud_room).
    read_only = True
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
        return await self._answer(ctx, session)

    async def execute_from_tool(self, args: dict, ctx: Context, session: AsyncSession) -> Response:
        return await self._answer(ctx, session)

    async def _from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._answer(ctx, session)

    async def _answer(self, ctx: Context, session: AsyncSession) -> Response:
        return Response(
            text=capabilities_summary(await house_state(ctx, session)),
            session_id=ctx.session_id,
            matched_handler=self.name,
        )


def _areas(state: HouseState) -> list[tuple[str, str]]:
    """(label, phrase) for the areas the answer names: the core ones in
    ``_CORE_AREAS`` order, then enabled plugins', from the live registry;
    only what ``state`` lets this house do right now."""
    from domovoi import weather
    from domovoi.handlers import HANDLER_BY_NAME, HANDLERS

    out: list[tuple[str, str]] = []
    for name in _CORE_AREAS:
        h = HANDLER_BY_NAME.get(name)
        if h is None or getattr(h, "plugin_slug", None) is not None:
            continue
        if not _has(state, _NEEDS.get(name, frozenset())):
            continue
        phrases = example_phrases_of(h)
        if phrases:
            out.append((h.display.label, phrases[0]))
    if _has(state, _WEATHER_NEEDS) and not weather.plugin_handles_weather():
        out.append(_WEATHER)
    plugins = 0
    for h in HANDLERS:
        if getattr(h, "plugin_slug", None) is None or plugins >= _MAX_PLUGINS:
            continue
        # A plugin that only works online (downloads, web services) isn't
        # offered with no internet.
        if getattr(h, "requires_network", "no") == "yes" and not state.internet:
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


def capabilities_summary(state: HouseState | None = None) -> str:
    """The spoken answer: a few areas with one thing to say for each, then
    where the rest is. ``state`` None: the settings as this process last
    read them (online, the cached Home location)."""
    areas = _areas(state if state is not None else _settings_state())
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
