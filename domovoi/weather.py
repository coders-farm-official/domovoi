"""Weather answers: one entry point, ``answer_weather``.

Core asks SearXNG's weather engines (``categories=weather``) for the
household's place, ``news_location`` in settings (the same city/region
that scopes local news). ``None`` means "nothing usable": the caller falls
back to the general web search, unchanged.

A weather plugin takes over by registering the ``WEATHER_PROVIDER``
capability (``ctx.add_capability("weather", impl)``). While one is
registered core steps aside completely: the router skips the weather offer
and the lookup, and the plugin's own handler answers. (A plugin can also
simply claim weather phrases in its handler's priority band; the capability
is the explicit hand-off.)
"""

from __future__ import annotations

from domovoi.capabilities import CAPABILITIES, WEATHER_PROVIDER
from domovoi.clients.searxng import get_searxng_client
from domovoi.config import settings
from domovoi.models import Context


def plugin_handles_weather() -> bool:
    return not CAPABILITIES.absent(WEATHER_PROVIDER)


def household_place() -> str:
    return (settings.news_location or "").strip()


async def answer_weather(question: str, ctx: Context) -> str | None:
    """The spoken weather for the household's place, or None."""
    lookup = getattr(get_searxng_client(), "weather", None)
    if lookup is None:
        return None
    return await lookup(household_place())
