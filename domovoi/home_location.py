"""The household's Home location: Settings → Household → Home location.

Stored in ``household_settings`` under key ``home_location`` (V045, no
migration of its own)::

    {"name": "Denver, Colorado, US", "lat": 39.7392, "lon": -104.9847,
     "country": "US", "carried_over": false}

``lat``/``lon``/``country`` are null for a name that matched no town in the
bundled list (domovoi/places): the card then asks to pick the town from
the list. ``{"name": ""}`` means "none set". An admin changes it; any
household device reads it. Core's weather and local news use it, and
plugins read it as ``sdk.household.home_location()`` or, by its old name,
``sdk.core_config.news_location`` (just the name).

Until 1.4.1 the place was the ``news_location`` setting (Settings → System →
Advanced → News, in ``.env``). The first boot of 1.4.1 carries it over
once (``carry_over``): a row here, even an empty one, means that is done,
so the old setting is never read again after it.
"""

from __future__ import annotations

import json
import logging
import math
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from domovoi import places

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

KEY = "home_location"
NAME_MAX = 200

# The last value read in this process, for the synchronous readers
# (``core_config.news_location``). Every read and write refreshes it.
_cache: dict[str, Any] | None = None
_cached_at = 0.0
REFRESH_AFTER_S = 60.0


class InvalidPlace(ValueError):
    pass


def _number(v: Any, lo: float, hi: float) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise InvalidPlace("lat and lon must be numbers") from None
    if not math.isfinite(f) or not lo <= f <= hi:
        raise InvalidPlace("lat or lon is out of range")
    return round(f, 4)


def clean(value: Any, *, rematch: bool = True) -> dict[str, Any] | None:
    """A stored or submitted place made safe, or None for "none set".

    Raises InvalidPlace for a malformed one. A name without coordinates is
    matched against the town list once more, so a typed "Denver, CO" still
    gets them."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidPlace("a place is an object")
    name = " ".join(str(value.get("name") or "").split())
    if not name:
        return None
    if len(name) > NAME_MAX:
        raise InvalidPlace(f"the name is longer than {NAME_MAX} characters")
    lat = _number(value.get("lat"), -90, 90)
    lon = _number(value.get("lon"), -180, 180)
    if (lat is None) != (lon is None):
        raise InvalidPlace("lat and lon come together")
    country = str(value.get("country") or "").strip().upper() or None
    if country is not None and (len(country) != 2 or not country.isalpha() or not country.isascii()):
        raise InvalidPlace("country is a two-letter ISO 3166-1 code")
    out = {"name": name, "lat": lat, "lon": lon, "country": country,
           "carried_over": bool(value.get("carried_over"))}
    if lat is None and rematch:
        found = places.match(name)
        if found:
            out.update(found)
    return out


def _remember(value: dict[str, Any] | None) -> None:
    global _cache, _cached_at
    _cache, _cached_at = value, time.monotonic()


async def _row(db: "AsyncSession") -> Any:
    return (await db.execute(text("SELECT value FROM household_settings WHERE key = :k"),
                             {"k": KEY})).scalar()


async def load(db: "AsyncSession") -> dict[str, Any] | None:
    """The Home location, or None when none is set."""
    raw = await _row(db)
    if isinstance(raw, str):
        raw = json.loads(raw)
    try:
        # What was stored, not a fresh match: the nudge stays until someone picks.
        value = clean(raw, rematch=False) if isinstance(raw, dict) else None
    except InvalidPlace:
        value = None
    _remember(value)
    return value


async def save(db: "AsyncSession", value: Any) -> dict[str, Any] | None:
    """Store a place (or clear it with None / an empty name)."""
    cleaned = clean(value)
    stored = cleaned or {"name": ""}
    if cleaned is not None:
        stored = {**cleaned, "carried_over": False}
    await db.execute(text(
        "INSERT INTO household_settings (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
        {"k": KEY, "v": json.dumps(stored)})
    _remember(cleaned and stored)
    return cleaned and stored


async def carry_over(db: "AsyncSession", legacy: str) -> dict[str, Any] | None:
    """Once: fill Home location from the old ``news_location`` text.

    Nothing happens when a row exists (set, cleared, or carried before).
    Otherwise the row is written: the matched town (with coordinates), the
    text alone when no town clearly matches, or empty when there was no
    text. Returns what was carried, or None."""
    if await _row(db) is not None:
        return None
    name = " ".join(str(legacy or "").split())[:NAME_MAX]
    value: dict[str, Any] = {"name": ""}
    if name:
        found = places.match(name)
        value = {"name": name, "lat": None, "lon": None, "country": None, **(found or {}),
                 "carried_over": True}
    await db.execute(text(
        "INSERT INTO household_settings (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
        "ON CONFLICT (key) DO NOTHING"), {"k": KEY, "v": json.dumps(value)})
    if name:
        log.info("home location: carried over from the news location (%s)",
                 "matched a town" if value.get("lat") is not None else "no town matched")
    _remember(value if name else None)
    return value if name else None


async def current() -> dict[str, Any] | None:
    """Read it with a session of its own."""
    from domovoi.db.session import session_scope

    async with session_scope() as db:
        return await load(db)


def cached() -> dict[str, Any] | None:
    """The last value this process read, without waiting. A stale copy is
    refreshed in the background when an event loop is running."""
    global _cached_at
    if time.monotonic() - _cached_at > REFRESH_AFTER_S:
        try:
            import asyncio

            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            _cached_at = time.monotonic()   # one refresh at a time

            async def _refresh() -> None:
                try:
                    await current()
                except Exception as e:  # noqa: BLE001 — a read that fails keeps the old copy
                    log.debug("home location refresh failed: %s", e)

            loop.create_task(_refresh())
    return _cache


def name() -> str:
    """Just the name ('' when none): what ``news_location`` used to hold."""
    return str((cached() or {}).get("name") or "")


def needs_pick(value: dict[str, Any] | None) -> bool:
    """A name with no coordinates: the card asks to pick the town."""
    return bool(value and value.get("name") and value.get("lat") is None)
