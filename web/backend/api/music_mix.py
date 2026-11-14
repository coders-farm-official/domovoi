"""The Music mix: Settings → Household → Music mix (domovoi/up_next/mix.py).

* ``GET  /api/music/mix`` — the household's mix, the two ListenBrainz
  switches as the core last reported them, and the people with their own
  sliders (ids only; names come from ``/api/people``).
* ``PUT  /api/music/mix`` — change the household's mix (any field; the
  rest stay). Household tier, like playlists.
* ``GET  /api/people/{id}/music-mix`` — that person's override (null =
  "use the household's") and the mix their sessions use.
* ``PUT  /api/people/{id}/music-mix`` — set it; ``{"override": null}``
  goes back to the household's.

The two internet switches ("Similar artists from the internet", "Song
popularity from the internet") are core settings
(``music_similar_artists_enabled`` / ``music_popularity_enabled``),
changed through ``PATCH /api/config/editable`` like every other setting
(admin); this route only reports them. A new session takes the mix that
applies when it starts and when each batch is drawn.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi.admin_auth import require_device, require_device_read
from domovoi.up_next import mix as M
from web.backend.db import session_scope
from web.backend.domovoi_client import get_cached_snapshot

router = APIRouter(tags=["music"])

Step = Field(default=None, ge=1, le=5)


class MixIn(BaseModel):
    discovery_favorites: int | None = Step
    close_wander: int | None = Step
    autoplay: bool | None = None
    list_repeat_min: int | None = Field(default=None, ge=M.LIST_REPEAT_MIN_RANGE[0],
                                        le=M.LIST_REPEAT_MIN_RANGE[1])


class OverrideIn(BaseModel):
    discovery_favorites: int = Field(..., ge=1, le=5)
    close_wander: int = Field(..., ge=1, le=5)


class PersonMixIn(BaseModel):
    # null: use the household's mix.
    override: OverrideIn | None = None


def _internet() -> dict[str, Any]:
    live = (get_cached_snapshot() or {}).get("listenbrainz_fetch")
    if not isinstance(live, dict):
        return {"similar_artists": None, "popularity": None, "state": "unknown"}
    sim, pop = live.get("similar_artists_enabled"), live.get("popularity_enabled")
    return {
        "similar_artists": sim if isinstance(sim, bool) else None,
        "popularity": pop if isinstance(pop, bool) else None,
        "state": str(live.get("state") or "unknown"),
    }


async def _mix_answer(s: Any) -> dict[str, Any]:
    mix = await M.household(s)
    rows = (await s.execute(text(
        "SELECT id FROM people WHERE music_mix IS NOT NULL ORDER BY id"))).all()
    return {
        **mix.json(),
        "steps": list(M.STEPS),
        "list_repeat_min_range": list(M.LIST_REPEAT_MIN_RANGE),
        "internet": _internet(),
        "people_with_own_mix": [int(r[0]) for r in rows],
    }


@router.get("/api/music/mix", dependencies=[Depends(require_device_read)])
async def get_mix() -> dict[str, Any]:
    async with session_scope() as s:
        return await _mix_answer(s)


@router.put("/api/music/mix", dependencies=[Depends(require_device)])
async def put_mix(body: MixIn) -> dict[str, Any]:
    async with session_scope() as s:
        cur = await M.household(s)
        new = M.from_json({**cur.json(), **body.model_dump(exclude_none=True)})
        await M.save_household(s, new)
        return await _mix_answer(s)


async def _person_answer(s: Any, person_id: int) -> dict[str, Any]:
    exists = (await s.execute(text("SELECT 1 FROM people WHERE id = :p"), {"p": person_id})).first()
    if exists is None:
        raise HTTPException(status_code=404, detail=f"person {person_id} not found")
    override = await M.person(s, person_id)
    used = await M.resolve(s, person_id)
    return {"person_id": person_id, "override": override,
            "uses": {"discovery_favorites": used.discovery_favorites, "close_wander": used.close_wander}}


@router.get("/api/people/{person_id}/music-mix", dependencies=[Depends(require_device_read)])
async def get_person_mix(person_id: int) -> dict[str, Any]:
    async with session_scope() as s:
        return await _person_answer(s, person_id)


@router.put("/api/people/{person_id}/music-mix", dependencies=[Depends(require_device)])
async def put_person_mix(person_id: int, body: PersonMixIn) -> dict[str, Any]:
    async with session_scope() as s:
        await _person_answer(s, person_id)
        await M.save_person(s, person_id, body.override.model_dump() if body.override else None)
        return await _person_answer(s, person_id)
