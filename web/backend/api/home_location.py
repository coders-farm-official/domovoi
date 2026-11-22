"""Home location: Settings → Household → Home location (domovoi/home_location.py).

* ``GET /api/household/home-location`` — the place (or null), and whether
  the card should ask to pick the town from the list. Any household device.
* ``PUT /api/household/home-location`` — set it (``{"name", "lat", "lon",
  "country"}``, normally a suggestion as given) or clear it
  (``{"name": ""}``). Admin, like the other household settings.
* ``GET /api/places/suggest?q=&limit=8`` — towns starting with ``q``
  (accents and case ignored), biggest first, from the bundled GeoNames
  list (domovoi/places). Served from this box; nothing leaves the house.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from domovoi import home_location, places
from domovoi.admin_auth import require_admin_mutation, require_device_read
from web.backend.db import session_scope

router = APIRouter(tags=["household"])

ATTRIBUTION = "Town list from GeoNames (geonames.org), CC BY 4.0"


class PlaceIn(BaseModel):
    name: str = Field(default="", max_length=home_location.NAME_MAX)
    lat: float | None = None
    lon: float | None = None
    country: str | None = Field(default=None, max_length=2)


def _answer(value: dict[str, Any] | None) -> dict[str, Any]:
    place = {k: value.get(k) for k in ("name", "lat", "lon", "country")} if value else None
    return {
        "place": place,
        "needs_pick": home_location.needs_pick(value),
        "carried_over": bool(value and value.get("carried_over")),
        "attribution": ATTRIBUTION,
    }


@router.get("/api/household/home-location", dependencies=[Depends(require_device_read)])
async def get_home_location() -> dict[str, Any]:
    async with session_scope() as s:
        return _answer(await home_location.load(s))


@router.put("/api/household/home-location", dependencies=[Depends(require_admin_mutation)])
async def put_home_location(body: PlaceIn) -> dict[str, Any]:
    try:
        async with session_scope() as s:
            return _answer(await home_location.save(s, body.model_dump()))
    except home_location.InvalidPlace as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


@router.get("/api/places/suggest", dependencies=[Depends(require_device_read)])
async def suggest_places(q: str = Query(default="", max_length=100),
                         limit: int = Query(default=8, ge=1, le=places.MAX_LIMIT)) -> dict[str, Any]:
    return {"places": places.suggest(q, limit), "attribution": ATTRIBUTION}
