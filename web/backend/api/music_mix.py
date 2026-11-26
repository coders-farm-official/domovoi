"""The Music mix: Settings → Household → Music mix (domovoi/up_next/mix.py).

* ``GET  /api/music/mix`` — the household's mix, the two ListenBrainz
  switches as the core last reported them, and the people with their own
  sliders (ids only; names come from ``/api/people``).
* ``PUT  /api/music/mix`` — change the household's mix (any field; the
  rest stay). Admin, like the other household settings.
* ``GET  /api/people/{id}/music-mix`` — that person's override (null =
  "use the household's") and the mix their sessions use.
* ``PUT  /api/people/{id}/music-mix`` — set it; ``{"override": null}``
  goes back to the household's. An admin, or a phone or browser that
  said "this is me" as that person (``devices.owner_person_id``) and
  proves it with its claim key (``X-Domovoi-Claim-Key``, 1.5.0: L39/L40
  of the 2026-11 review), as clearing the person's listening history
  does. Without a matching key: 403 ``{"detail", "code":
  "claim_key_required"}``.

The two internet switches ("Similar artists from the internet", "Song
popularity from the internet") are core settings
(``music_similar_artists_enabled`` / ``music_popularity_enabled``),
changed through ``PATCH /api/config/editable`` like every other setting
(admin); this route only reports them. A new session takes the mix that
applies when it starts and when each batch is drawn.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi.admin_auth import require_admin_mutation, require_device, require_device_read
from domovoi.up_next import mix as M
from web.backend.api import devices as devices_api
from web.backend.db import session_scope
from web.backend.domovoi_client import get_cached_snapshot

router = APIRouter(tags=["music"])

NOT_YOURS_DETAIL = "Only an admin, or this person's own phone, can change their mix."
#: A claimed device that can't prove the claim (no key, or a wrong one).
CLAIM_KEY_REQUIRED = "claim_key_required"

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


@router.put("/api/music/mix", dependencies=[Depends(require_admin_mutation)])
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


@router.put("/api/people/{person_id}/music-mix", dependencies=[Depends(require_device)],
            responses={403: {"description": 'not this person\'s device, or {"detail", "code": '
                                            '"claim_key_required"} without its claim key'}})
async def put_person_mix(person_id: int, body: PersonMixIn, request: Request) -> Any:
    if not await devices_api.caller_is_admin(request):
        async with session_scope() as s:
            # The caller's own device: devices.caller_device_row.
            device = await devices_api.caller_device_row(s, request)
            name = (await s.execute(text("SELECT name FROM people WHERE id = :p"),
                                    {"p": person_id})).scalar_one_or_none()
        mine = device is not None and device.owner_person_id is not None
        if not mine or int(device.owner_person_id) != person_id or device.shared_screen:
            raise HTTPException(status_code=403, detail=NOT_YOURS_DETAIL)
        # Proven by its device key with its own claim, or (apps from before
        # 1.5.0) by the claim key: devices.proves_claim.
        if not devices_api.proves_claim(device, request):
            detail = (devices_api.RECLAIM_DETAIL if not device.claim_key_hash
                      else devices_api.WRONG_KEY_DETAIL).format(name=name or "this person")
            return JSONResponse(status_code=403, content={"detail": detail, "code": "claim_key_required"})
    async with session_scope() as s:
        await _person_answer(s, person_id)
        await M.save_person(s, person_id, body.override.model_dump() if body.override else None)
        return await _person_answer(s, person_id)
