"""Core routes for groups: named sets of rooms and the shared queues
(domovoi/music_groups.py). The dashboard reaches them through the web
process's ``/api/music/groups`` proxy; the phone apps do too.

Tiers (SHARED-QUEUES-PLAN §3): reading is a household read; making,
renaming and deleting groups, and starting, joining, leaving or ending a
shared queue, are household actions (``require_device``). A guest never
pulls music into another room (CORE-11), so nothing here is open to one; a
guest's skip or add on a room that plays a shared queue reaches the group
through the ordinary queue routes (owner answer 3).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from domovoi import music_groups
from domovoi.admin_auth import require_device, require_device_read

log = logging.getLogger(__name__)

router = APIRouter()


class _GroupBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=music_groups.NAME_MAX)
    rooms: list[str] = Field(..., min_length=2, max_length=64)


class _GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=music_groups.NAME_MAX)
    rooms: list[str] | None = Field(default=None, min_length=2, max_length=64)


class _ShareBody(BaseModel):
    #: The room whose music the others hear (a room in a shared queue
    #: stands for that queue's host).
    host_room: str = Field(..., min_length=1, max_length=64)
    rooms: list[str] = Field(default_factory=list, max_length=64)
    group_id: int | None = Field(default=None, ge=1)
    everywhere: bool = False
    device_id: str | None = Field(default=None, max_length=64)


class _JoinBody(BaseModel):
    room_id: str = Field(..., min_length=1, max_length=64)
    #: The room whose music to hear (any room of a shared queue will do).
    host_room: str = Field(..., min_length=1, max_length=64)
    device_id: str | None = Field(default=None, max_length=64)


class _LeaveBody(BaseModel):
    room_id: str = Field(..., min_length=1, max_length=64)


async def _house(request: Request):
    """The house's rooms (handlers/shared/rooms.household_rooms)."""
    from domovoi.db.session import session_scope
    from domovoi.handlers.shared import rooms as rooms_mod

    async with session_scope() as s:
        return await rooms_mod.household_rooms(s, request.app)


def _room_keys(rooms) -> set[str]:
    out: set[str] = set()
    for r in rooms:
        out |= r.keys()
    return out


def _listing() -> dict[str, Any]:
    return {
        "groups": [g.to_dict() for g in music_groups.groups()],
        "shared": [sh.to_dict() for sh in music_groups.shares()],
    }


@router.get("/v1/admin/music/groups", dependencies=[Depends(require_device_read)])
async def list_groups() -> dict[str, Any]:
    """The named groups (``groups``: id, name, rooms) and the shared queues
    playing now (``shared``: id, name, host_room, rooms — host first,
    group_id, everywhere)."""
    return _listing()


@router.post("/v1/admin/music/groups", dependencies=[Depends(require_device)])
async def create_group(body: _GroupBody, request: Request) -> dict[str, Any]:
    """Make a named group. 400 with a sentence in ``detail`` for a name it
    can't have (taken, a room's, a radio word) or fewer than two rooms of
    the house."""
    from domovoi.db.session import session_scope

    house = await _house(request)
    known = {r.room_id for r in house}
    unknown = [r for r in body.rooms if r not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown room: {unknown[0]}.")
    try:
        async with session_scope() as s:
            g = await music_groups.create_group(s, body.name, body.rooms, room_keys=_room_keys(house))
    except music_groups.GroupNameError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return g.to_dict()


@router.patch("/v1/admin/music/groups/{group_id}", dependencies=[Depends(require_device)])
async def update_group(group_id: int, body: _GroupPatch, request: Request) -> dict[str, Any]:
    """Rename a group or pick its rooms (either or both)."""
    from domovoi.db.session import session_scope

    house = await _house(request)
    if body.rooms is not None:
        known = {r.room_id for r in house}
        unknown = [r for r in body.rooms if r not in known]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown room: {unknown[0]}.")
    try:
        async with session_scope() as s:
            g = await music_groups.update_group(
                s, group_id, name=body.name, rooms=body.rooms, room_keys=_room_keys(house),
            )
    except music_groups.GroupNameError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if g is None:
        raise HTTPException(status_code=404, detail="no such group")
    return g.to_dict()


@router.delete("/v1/admin/music/groups/{group_id}", dependencies=[Depends(require_device)])
async def delete_group(group_id: int) -> dict[str, Any]:
    """Delete a named group. Music playing in its rooms plays on."""
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        if not await music_groups.delete_group(s, group_id):
            raise HTTPException(status_code=404, detail="no such group")
    return {"deleted": group_id}


async def _playing(room_id: str) -> bool:
    from domovoi.clients.mpd import get_mpd_client_for

    try:
        return await get_mpd_client_for(room_id).state() in ("play", "pause")
    except Exception:  # noqa: BLE001
        return False


@router.post("/v1/admin/music/shared", dependencies=[Depends(require_device)])
async def start_shared(body: _ShareBody, request: Request) -> dict[str, Any]:
    """Other rooms hear ``host_room``'s music: the rooms listed, a named
    group's rooms (``group_id``), or every connected room (``everywhere``).
    The host must be playing (409 otherwise). Rooms that aren't connected
    are left out of a group or everywhere; one named in ``rooms`` joins when
    it next connects. A room in another shared queue leaves it."""
    house = await _house(request)
    by_id = {r.room_id: r for r in house}
    if body.host_room not in by_id:
        raise HTTPException(status_code=404, detail="no such room")
    host = music_groups.host_of(body.host_room) or body.host_room
    if not await _playing(host):
        raise HTTPException(status_code=409, detail=f"nothing is playing in {body.host_room}")
    rooms: list[str] = []
    unknown = [r for r in body.rooms if r not in by_id]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown room: {unknown[0]}.")
    rooms += body.rooms
    if body.group_id is not None:
        g = music_groups.group(body.group_id)
        if g is None:
            raise HTTPException(status_code=404, detail="no such group")
        rooms += [r for r in g.rooms if r in by_id and by_id[r].online]
    if body.everywhere:
        rooms += [r.room_id for r in house if r.online]
    rooms = [r for r in dict.fromkeys(rooms) if r != host]
    if not rooms:
        raise HTTPException(status_code=409, detail="no other room is connected to share with")
    sh = await music_groups.share(
        request.app, host, rooms, group_id=body.group_id, everywhere=body.everywhere,
        started_by=body.device_id,
    )
    if sh is None:
        raise HTTPException(status_code=409, detail="nothing to share")
    return sh.to_dict()


@router.post("/v1/admin/music/shared/join", dependencies=[Depends(require_device)])
async def join_shared(body: _JoinBody, request: Request) -> dict[str, Any]:
    """``room_id`` hears ``host_room``'s music (409 when nothing plays there)."""
    house = {r.room_id for r in await _house(request)}
    if body.room_id not in house or body.host_room not in house:
        raise HTTPException(status_code=404, detail="no such room")
    host = music_groups.host_of(body.host_room) or body.host_room
    if host == body.room_id:
        raise HTTPException(status_code=409, detail="that is the room's own music")
    if not await _playing(host):
        raise HTTPException(status_code=409, detail=f"nothing is playing in {body.host_room}")
    sh = await music_groups.share(request.app, host, [body.room_id], started_by=body.device_id)
    if sh is None:
        raise HTTPException(status_code=409, detail="nothing to share")
    return sh.to_dict()


@router.post("/v1/admin/music/shared/leave", dependencies=[Depends(require_device)])
async def leave_shared(body: _LeaveBody, request: Request) -> dict[str, Any]:
    """``room_id`` leaves its shared queue and goes quiet; the other rooms
    play on (the host's queue moves to another room when the host leaves).
    404 when the room shares nothing."""
    from domovoi.models import Response
    from domovoi.music_pause import note_paused_by_person
    from domovoi.room_music import dispatch_music

    left = await music_groups.leave(request.app, body.room_id)
    if left is None:
        raise HTTPException(status_code=404, detail="that room isn't in a group")
    note_paused_by_person(request.app, body.room_id, False)
    await dispatch_music(request.app, Response(text="", music_action="stop"), body.room_id)
    return {
        "left": body.room_id,
        "group": left.share.to_dict() if not left.ended else None,
    }


@router.post("/v1/admin/music/shared/{share_id}/end", dependencies=[Depends(require_device)])
async def end_shared(share_id: str, request: Request) -> dict[str, Any]:
    """End a shared queue: the other rooms stop, the host plays on alone."""
    sh = await music_groups.end(request.app, share_id, silence_members=True)
    if sh is None:
        raise HTTPException(status_code=404, detail="no such shared queue")
    return {"ended": share_id, "host_room": sh.host}
