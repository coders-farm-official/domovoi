"""Weather answers: one entry point, ``answer_weather``.

Core asks SearXNG's weather engines for the household's place, the Home
location (Settings → Household → Home location, domovoi/home_location.py;
the same place that scopes local news). With none set, the answer says
where to set it. ``None`` means "nothing usable": the caller falls back to
the general web search, unchanged.

SearXNG's weather engines take a place NAME and geocode it themselves
(Open-Meteo's geocoder, first hit), so a Home location picked from the
town list is asked for by its town name; its coordinates go along in
``household_place()`` for a caller (or a plugin) that can use them.

A weather plugin takes over by registering the ``WEATHER_PROVIDER``
capability (``ctx.add_capability("weather", impl)``). While one is
registered core steps aside: the router skips the weather offer and the
lookup, and the plugin's own handler answers. (A plugin can also simply
claim weather phrases in its handler's priority band; the capability is the
explicit hand-off.)

One source of truth (owner, 2026-11-24): a plugin that answers wins. Only
when the plugin's handler FAILS on a weather question (raises, or gives no
answer) does core weather step in, and only when the house's internet
answer lets core look it up (:func:`core_may_step_in`); otherwise the turn
gets :data:`FAILED_LINE`. The voice turn (router) and the web answer
(double_check) both decide through this module: :func:`plugin_attempt`
notes the failure, :func:`answer_for` is the shared plugin-then-core answer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Awaitable

from domovoi import egress, home_location
from domovoi.capabilities import CAPABILITIES, WEATHER_PROVIDER
from domovoi.clients.searxng import get_searxng_client
from domovoi.models import Context, Response
from domovoi.uncertainty import CATEGORY_WEATHER, categorize_question

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from domovoi.handlers.base import Handler

log = logging.getLogger(__name__)

NO_PLACE_LINE = (
    "I don't know where home is yet. Set it in Settings → Household → "
    "Home location, and I can tell you the weather there."
)


# The weather plugin failed and core can't look it up instead: short, in
# the "Sorry, that didn't work." style, and never names the plugin.
FAILED_LINE = "Sorry, I couldn't get the weather just now."


def plugin_handles_weather() -> bool:
    return not CAPABILITIES.absent(WEATHER_PROVIDER)


def is_plugin_handler(handler: Any) -> bool:
    """Whether ``handler`` belongs to a plugin registered as the weather provider."""
    slug = getattr(handler, "plugin_slug", None)
    return slug is not None and slug in CAPABILITIES.providers_for(WEATHER_PROVIDER)


def core_may_step_in(ctx: Context) -> bool:
    """Whether core weather may stand in for a failed weather plugin: the
    house is online and its internet answer isn't No (the same answer that
    starts and stops the search helper core's lookup goes through). Past
    this, core's own path keeps its own gates: "Sometimes" still asks first."""
    return bool(ctx.online) and egress.internet_allowed()


def _no_answer(response: Response | None) -> bool:
    return response is None or not (response.text or "").strip()


async def plugin_attempt(
    handler: "Handler", call: Awaitable[Response | None], question: str
) -> tuple[Response | None, bool]:
    """Await a handler's answer: ``(response, weather_plugin_failed)``.

    Only a weather plugin's handler on a weather question is guarded: a
    raise or an empty answer becomes ``(None, True)`` so core weather can
    step in. Anything else is awaited as is (a raise propagates)."""
    if not (is_plugin_handler(handler) and categorize_question(question) == CATEGORY_WEATHER):
        return await call, False
    try:
        response = await call
    except Exception as e:  # noqa: BLE001 — the failure core weather covers
        log.warning("weather plugin %s failed: %s", getattr(handler, "plugin_slug", "?"), e)
        return None, True
    if _no_answer(response):
        log.warning("weather plugin %s gave no answer", getattr(handler, "plugin_slug", "?"))
        return None, True
    return response, False


async def household_place() -> dict[str, Any] | None:
    """The Home location to ask about, or None when none is set.

    ``{"name", "lat", "lon", "query"}``: with coordinates (a town picked
    from the list) ``query`` is the town's own name, which the weather
    engines' geocoder takes; without, it is the name as typed."""
    place = await home_location.current()
    if not place:
        return None
    name = place["name"]
    has_coords = place.get("lat") is not None and place.get("lon") is not None
    return {
        "name": name,
        "lat": place.get("lat") if has_coords else None,
        "lon": place.get("lon") if has_coords else None,
        "query": name.split(",")[0].strip() if has_coords else name,
    }


async def answer_weather(question: str, ctx: Context) -> str | None:
    """The spoken weather for the household's place, the no-place line, or None."""
    lookup = getattr(get_searxng_client(), "weather", None)
    if lookup is None:
        return None
    place = await household_place()
    if place is None:
        return NO_PLACE_LINE
    return await lookup(place["query"])


async def _plugin_answer(question: str, ctx: Context, session: "AsyncSession") -> str | None:
    """The weather plugin's spoken answer to ``question``, the way a voice
    turn reaches it (its handler's fast path), or None when it fails or has
    no fast path for the words."""
    from domovoi.handlers import HANDLERS
    from domovoi.router import first_fast_path, normalize_transcript, run_fast_path, strip_leading_filler

    mine = [h for h in HANDLERS if is_plugin_handler(h)]
    hit = first_fast_path(strip_leading_filler(normalize_transcript(question)), mine)
    if hit is None:
        return None
    handler, fp, m = hit
    response, failed = await plugin_attempt(
        handler, run_fast_path(handler, fp, m, ctx, session), question
    )
    return None if failed or response is None else response.text


async def answer_for(
    question: str,
    ctx: Context,
    session: "AsyncSession",
    *,
    plugin_failed: bool = False,
) -> str | None:
    """THE weather answer for a weather question, from one decision point.

    No weather plugin: core's lookup (:func:`answer_weather`), unchanged.
    A weather plugin: its answer; if it fails (``plugin_failed``: it already
    failed this turn, don't ask it twice), core's lookup when
    :func:`core_may_step_in`, else :data:`FAILED_LINE`. None: nothing
    usable from core, so the caller goes on to the general web search."""
    if not plugin_handles_weather():
        return await answer_weather(question, ctx)
    if not plugin_failed:
        spoken = await _plugin_answer(question, ctx, session)
        if spoken:
            return spoken
    if not core_may_step_in(ctx):
        return FAILED_LINE
    return await answer_weather(question, ctx)
