"""``sdk.household`` and ``sdk.places`` (SDK 1.13): the household's Home
location and the offline town list behind its type-ahead.

    place = await sdk.household.home_location()
    # {"name": "Denver, Colorado, US", "lat": 39.7392, "lon": -104.9847,
    #  "country": "US"} or None. lat/lon/country are None for a name that
    #  matched no town (carried over from the old news location).

    sdk.places.suggest("denv")      # [{"name", "lat", "lon", "country"}, ...]

Read-only: only an admin changes the Home location (Settings → Household).
The town list is bundled (GeoNames, CC BY 4.0); nothing leaves the house.
"""

from __future__ import annotations

from typing import Any

from domovoi import home_location, places

_FIELDS = ("name", "lat", "lon", "country")


class HouseholdAPI:
    async def home_location(self) -> dict[str, Any] | None:
        value = await home_location.current()
        return {k: value.get(k) for k in _FIELDS} if value else None


class PlacesAPI:
    def suggest(self, text: str, limit: int = 8) -> list[dict[str, Any]]:
        return places.suggest(text, limit)
