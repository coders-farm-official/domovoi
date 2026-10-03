"""Music "also called" names — ``/api/music/aliases`` (V019
``library_aliases``; spoken-match contract §3).

A library artist, album or song can have any number of other names the
household says instead ("gramps" for Hearth Ensemble, "lullaby" for one song);
each name means ONE thing household-wide. The rules — uniqueness, the
"replace it?" conflict, who may remove what — live in
:mod:`domovoi.db.library_aliases`, shared with the core's voice handler
("when I say X I mean Y"); this module is the HTTP door.

Tiers:

* reads are OPEN, like ``GET /api/music/library`` — names of music.
  Who added a name is said as "admin", "voice in <room>", "MusicBrainz"
  or the adding device's registered name — that last one only to a caller
  on the device tier (the device inventory, ``GET /api/devices``, is
  admin-only); anyone else reads "a device";
* adding and removing take the household device tier (``require_device``:
  the device token or an admin Bearer). Inside it, an admin may remove or
  replace anything; a paired device only the names IT added (by the
  self-asserted device id the dashboard's cookie or ``X-Device-Id``
  carries — household policy, not a security boundary); anyone may remove
  a MusicBrainz name (it is then hidden, not deleted, so the next lookup
  does not bring it back).

Every write is a plain database write: the core's resolver notices it on
its next request through its cheap library/aliases fingerprint, so no
NOTIFY is needed.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from domovoi.admin_auth import check_admin_request, check_device_request, require_device
from domovoi.db import library_aliases as repo
from web.backend.api.files import caller_device_id
from web.backend.db import session_scope
from web.backend.domovoi_client import get_cached_snapshot
from web.backend.schemas import (
    AliasStatus,
    LibraryAlias,
    LibraryAliasAddResult,
    LibraryAliasCreate,
    LibraryAliasPage,
    TrackAliases,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/music/aliases", tags=["music"])

DEVICE = [Depends(require_device)]

_NOT_IN_LIBRARY = {
    "artist": "There's no artist called {name} in the library.",
    "album": "There's no album called {name} in the library.",
    "track": "That song isn't in the library.",
}


async def caller_actor(request: Request) -> repo.Actor:
    """Who is asking, for the alias policy: an admin (a live Bearer, or
    the pre-setup LAN grace), else a device by the id it asserted (None
    when it sent none — the Android app today)."""
    result = await check_admin_request(request)
    if result in ("ok", "pre-setup"):
        return repo.Actor(kind="admin")
    return repo.Actor(kind="device", device_id=caller_device_id(request))


async def shows_device_names(request: Request, actor: repo.Actor) -> bool:
    """Whether an OPEN read may name the devices that added names: only
    for a caller on the device tier (a device token, an admin Bearer, the
    dashboard cookie, the pre-setup grace). A wrong token pays the usual
    device-token backoff here too."""
    if actor.is_admin:
        return True
    return await check_device_request(request) in ("ok", "admin", "pre-setup", "cookie-only")


@router.get("", response_model=LibraryAliasPage)
async def list_aliases(
    request: Request,
    target_type: str | None = Query(default=None, pattern="^(artist|album|track)$"),
    target_key: str | None = Query(default=None, max_length=240),
    track_id: int | None = Query(default=None),
    source: str | None = Query(default=None, pattern="^(manual|voice|musicbrainz)$"),
    include_suppressed: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> LibraryAliasPage:
    """A page of names, household ones first, with the filtered total."""
    actor = await caller_actor(request)
    show = await shows_device_names(request, actor)
    async with session_scope() as s:
        rows, total = await repo.list_aliases(
            s,
            target_type=target_type,
            target_key=target_key,
            track_id=track_id,
            source=source,
            include_suppressed=include_suppressed,
            limit=limit,
            offset=offset,
        )
    return LibraryAliasPage(
        items=[LibraryAlias(**repo.public_alias(r, actor, show_device=show)) for r in rows],
        total=total,
    )


@router.get("/status", response_model=AliasStatus)
async def alias_status() -> AliasStatus:
    """Counts for the Stats tab, plus the MusicBrainz lookup's switch and
    state as the core last reported them (snapshot key
    ``music_alias_fetch``; absent → ``enabled: null, state: "unknown"``)."""
    async with session_scope() as s:
        counts = await repo.alias_counts(s)
    live = (get_cached_snapshot() or {}).get("music_alias_fetch")
    fetch: dict[str, Any] = dict(counts["fetch"])
    if isinstance(live, dict):
        enabled = live.get("enabled")
        fetch["enabled"] = enabled if isinstance(enabled, bool) else None
        fetch["state"] = str(live.get("state") or "unknown")
        err = live.get("last_error")
        fetch["last_error"] = str(err) if err else None
    else:
        fetch.update({"enabled": None, "state": "unknown", "last_error": None})
    return AliasStatus(
        household=counts["household"],
        musicbrainz=counts["musicbrainz"],
        suppressed=counts["suppressed"],
        fetch=fetch,
    )


@router.get("/for-track/{track_id}", response_model=TrackAliases)
async def aliases_for_track(track_id: int, request: Request) -> TrackAliases:
    """The track drawer's "also called" lists in one call: the song, each
    performer (the whole credit first when it reads like one band), and
    the album."""
    actor = await caller_actor(request)
    show = await shows_device_names(request, actor)
    async with session_scope() as s:
        out = await repo.drawer_for_track(s, track_id, actor, show_device=show)
    if out is None:
        raise HTTPException(status_code=404, detail=f"track {track_id} not found")
    return TrackAliases(**out)


@router.post(
    "",
    response_model=LibraryAliasAddResult,
    status_code=201,
    dependencies=DEVICE,
    responses={
        200: {"description": "That name already means that target (nothing changed)."},
        403: {"description": "Replacing someone else's name needs an admin."},
        404: {"description": "The target is not in the library."},
        409: {"description": "The name already means something else: "
                             '{"detail": {"code": "alias_taken", "existing": LibraryAlias, '
                             '"can_replace": bool}} — retry with replace=true.'},
        422: {"description": 'Not a usable name: {"detail": {"code": "empty" | "too_long" | '
                             '"nothing_to_say" | "already_its_name", "message": str}}.'},
    },
)
async def add_alias(payload: LibraryAliasCreate, request: Request) -> Any:
    """Teach the household another name for an artist, album or song."""
    actor = await caller_actor(request)
    async with session_scope() as s:
        # One scan of the library's names serves both the target check and
        # the "instead of" (shadows) answer.
        names = await repo.library_names(s)
        target = await repo.resolve_target(
            s,
            payload.target_type,
            name=payload.target_name,
            artist_name=payload.target_artist_name,
            track_id=payload.target_track_id,
            names=names,
        )
        if target is None:
            raise HTTPException(
                status_code=404,
                detail=_NOT_IN_LIBRARY[payload.target_type].format(
                    name=(payload.target_name or "").strip() or "that"
                ),
            )
        outcome = await repo.add_alias(
            s, alias=payload.alias, target=target, actor=actor, source="manual",
            replace=payload.replace, names=names,
        )
        if outcome.status == "invalid":
            raise HTTPException(status_code=422, detail={"code": outcome.code, "message": outcome.message})
        if outcome.status == "taken":
            existing = outcome.existing
            assert existing is not None
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "alias_taken",
                    "existing": repo.public_alias(existing, actor),
                    "can_replace": outcome.can_replace,
                    "message": f"'{existing.alias}' already means "
                               f"{repo.target_label(repo.target_of(existing))}.",
                },
            )
        if outcome.status == "forbidden":
            raise HTTPException(status_code=403, detail=repo.ONLY_ADMIN_CHANGE)
        row = outcome.row
        assert row is not None
        body = LibraryAliasAddResult(
            status=outcome.status,
            alias=LibraryAlias(**repo.public_alias(row, actor)),
            shadows=outcome.shadows,
        )
    code = 200 if outcome.status == "exists" else 201
    return JSONResponse(status_code=code, content=body.model_dump(mode="json"))


@router.delete("/{alias_id}", status_code=204, dependencies=DEVICE)
async def delete_alias(alias_id: int, request: Request) -> Response:
    """Remove one name. A MusicBrainz name is hidden rather than deleted;
    someone else's household name needs an admin (403)."""
    actor = await caller_actor(request)
    async with session_scope() as s:
        outcome = await repo.remove_alias(s, alias_id, actor)
    if outcome.status == "not_found":
        raise HTTPException(status_code=404, detail=f"alias {alias_id} not found")
    if outcome.status == "forbidden":
        raise HTTPException(status_code=403, detail=repo.ONLY_ADMIN_REMOVE)
    return Response(status_code=204)
