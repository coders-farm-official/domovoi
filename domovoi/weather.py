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
registered core steps aside completely: the router skips the weather offer
and the lookup, and the plugin's own handler answers. (A plugin can also
simply claim weather phrases in its handler's priority band; the capability
is the explicit hand-off.)
"""

from __future__ import annotations

from typing import Any

from domovoi import home_location
from domovoi.capabilities import CAPABILITIES, WEATHER_PROVIDER
from domovoi.clients.searxng import get_searxng_client
from domovoi.models import Context

NO_PLACE_LINE = (
    "I don't know where home is yet. Set it in Settings → Household → "
    "Home location, and I can tell you the weather there."
)


def plugin_handles_weather() -> bool:
    return not CAPABILITIES.absent(WEATHER_PROVIDER)


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
