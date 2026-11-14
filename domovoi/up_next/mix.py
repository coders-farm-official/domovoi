"""The Music mix: plain choices in, engine settings out.

Settings → Household → Music mix offers two five-step sliders instead of
raw weights (owner 2026-11-14), plus the autoplay default and the list
length that starts over:

* **Discovery ↔ Favorites** (``discovery_favorites``, 1 = Discovery,
  3 = the middle, today's default, 5 = Favorites) sets the freshness
  weight's range and how much personal affinity counts
  (:data:`DISCOVERY_FAVORITES`). Discovery widens the lift for songs not
  heard in a while (and lowers songs heard lately more); Favorites
  narrows it and leans on taste and play count.
* **Stay close ↔ Wander** (``close_wander``, 1 = Stay close, 5 = Wander)
  sets how much of a batch follows the seeds' artist, era, genre and
  playlists versus wildcards from the whole library (library mode), and
  how strictly a list follows the song it started from (list mode)
  (:data:`CLOSE_WANDER`).
* ``autoplay`` — the default for a new session ("Autoplay when a playlist
  ends"); each browser and phone can still turn it off for itself.
* ``list_repeat_min`` — "no repeats within N songs": a list shorter than
  this plays through once and Up next carries on with similar songs; a
  longer one is reshuffled when used up, its last songs kept off the
  start of the next round.

The household's choices are ``household_settings`` key ``music_mix``
(V045); a person may override the two sliders (``people.music_mix``,
V056, NULL = use the household's). :func:`resolve` picks the listener's
when the session has one, else the household's.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Mapping

from sqlalchemy import text

from .model import (
    AFFINITY_STEP,
    DEFAULT_CONFIG,
    DEFAULT_SHARES,
    FRESH_MAX,
    FRESH_MIN,
    WILDCARD,
    EngineConfig,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

KEY = "music_mix"
STEPS = (1, 2, 3, 4, 5)
MIDDLE = 3
LIST_REPEAT_MIN_RANGE = (20, 500)

#: Discovery ↔ Favorites: step -> (fresh_min, fresh_max, affinity_step).
#: The middle row is the freshness weight's own numbers (model.py).
DISCOVERY_FAVORITES: Mapping[int, tuple[float, float, float]] = {
    1: (0.50, 1.75, 0.06),
    2: (0.60, 1.50, 0.08),
    3: (FRESH_MIN, FRESH_MAX, AFFINITY_STEP),
    4: (0.85, 1.15, 0.13),
    5: (0.95, 1.05, 0.16),
}
#: Stay close ↔ Wander: step -> (wildcard share of a library batch,
#: list mode's weighted share). The middle row is today's.
CLOSE_WANDER: Mapping[int, tuple[float, float]] = {
    1: (0.05, 0.95),
    2: (0.10, 0.90),
    3: (DEFAULT_SHARES[WILDCARD], DEFAULT_CONFIG.list_weighted_share),
    4: (0.25, 0.70),
    5: (0.40, 0.60),
}


@dataclass(frozen=True)
class Mix:
    discovery_favorites: int = MIDDLE
    close_wander: int = MIDDLE
    autoplay: bool = True
    list_repeat_min: int = DEFAULT_CONFIG.list_repeat_min

    def config(self) -> EngineConfig:
        return engine_config(self)

    def json(self) -> dict[str, Any]:
        return asdict(self)


def _step(v: Any, default: int = MIDDLE) -> int:
    try:
        i = int(v)
    except (TypeError, ValueError):
        return default
    return i if i in STEPS else default


def _repeat(v: Any) -> int:
    try:
        i = int(v)
    except (TypeError, ValueError):
        return DEFAULT_CONFIG.list_repeat_min
    lo, hi = LIST_REPEAT_MIN_RANGE
    return min(max(i, lo), hi)


def from_json(raw: Any, base: Mix | None = None) -> Mix:
    """A :class:`Mix` from stored JSON, bad or missing values falling back
    to ``base`` (the defaults)."""
    base = base or Mix()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if not isinstance(raw, Mapping):
        return base
    return Mix(
        discovery_favorites=_step(raw.get("discovery_favorites"), base.discovery_favorites),
        close_wander=_step(raw.get("close_wander"), base.close_wander),
        autoplay=bool(raw["autoplay"]) if isinstance(raw.get("autoplay"), bool) else base.autoplay,
        list_repeat_min=_repeat(raw["list_repeat_min"]) if "list_repeat_min" in raw else base.list_repeat_min,
    )


def engine_config(mix: Mix, base: EngineConfig = DEFAULT_CONFIG) -> EngineConfig:
    """The slider steps as the engine's numbers."""
    lo, hi, aff = DISCOVERY_FAVORITES[_step(mix.discovery_favorites)]
    wild, weighted = CLOSE_WANDER[_step(mix.close_wander)]
    others = {k: v for k, v in DEFAULT_SHARES.items() if k != WILDCARD}
    scale = (1.0 - wild) / sum(others.values())
    shares = {k: v * scale for k, v in others.items()}
    shares[WILDCARD] = wild
    return replace(base, fresh_min=lo, fresh_max=hi, affinity_step=aff, shares=shares,
                   list_weighted_share=weighted, list_repeat_min=_repeat(mix.list_repeat_min))


# ─── Stored ───────────────────────────────────────────────────────────────


async def household(db: "AsyncSession") -> Mix:
    raw = (await db.execute(text("SELECT value FROM household_settings WHERE key = :k"),
                            {"k": KEY})).scalar()
    return from_json(raw)


async def save_household(db: "AsyncSession", mix: Mix) -> None:
    await db.execute(text(
        "INSERT INTO household_settings (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
        {"k": KEY, "v": json.dumps(mix.json())})


def person_override(raw: Any) -> dict[str, int] | None:
    """A person's stored override: the sliders they set, or None."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, Mapping):
        return None
    out = {k: _step(raw.get(k)) for k in ("discovery_favorites", "close_wander") if k in raw}
    return out or None


async def person(db: "AsyncSession", person_id: int) -> dict[str, int] | None:
    raw = (await db.execute(text("SELECT music_mix FROM people WHERE id = :p"),
                            {"p": int(person_id)})).scalar()
    return person_override(raw)


async def save_person(db: "AsyncSession", person_id: int, override: Mapping[str, int] | None) -> bool:
    row = (await db.execute(
        text("UPDATE people SET music_mix = CAST(:v AS jsonb) WHERE id = :p RETURNING id"),
        {"p": int(person_id), "v": json.dumps(dict(override)) if override else None})).first()
    return row is not None


def apply_override(base: Mix, override: Mapping[str, int] | None) -> Mix:
    if not override:
        return base
    return replace(base, **{k: _step(v) for k, v in override.items()
                            if k in ("discovery_favorites", "close_wander")})


async def resolve(db: "AsyncSession", person_id: int | None) -> Mix:
    """The mix a session uses: the listener's sliders when the session has
    a listener who set their own, else the household's."""
    base = await household(db)
    if person_id is None:
        return base
    return apply_override(base, await person(db, person_id))


__all__ = [
    "CLOSE_WANDER", "DISCOVERY_FAVORITES", "KEY", "LIST_REPEAT_MIN_RANGE", "MIDDLE", "Mix",
    "apply_override", "engine_config", "from_json", "household", "person", "person_override",
    "resolve", "save_household", "save_person",
]
