"""Core routes for groups: named sets of rooms and the shared queues
(domovoi/music_groups.py). The dashboard reaches them through the web
process's ``/api/music/groups`` proxy; the phone apps do too.

Tiers (SHARED-QUEUES-PLAN §3): reading is a household read; making,
renaming and deleting groups, and starting, joining, leaving or ending a
shared queue, are household actions (``require_device``). A guest never
pulls music into another room (CORE-11), so nothing here is open to one; a
guest's skip or add on a room that plays a shared queue reaches the group
through the ordinary queue routes (owner answer 3).

Listening along (1.5.0, domovoi/music_listeners.py): a paired phone joins
the music playing in a room and plays its stream itself. Join, leave and
the heartbeat are household actions too: a guest phone can't listen along
in 1.5.0 (the listeners are shown to the household by name, and a guest's
device id is its own say-so), and away chat has no op for it (the stream is
a home-network address).
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from domovoi import music_groups, music_listeners
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
        "shared": [
            {**sh.to_dict(), "listeners": music_listeners.listing(sh.host)}
            for sh in music_groups.shares()
        ],
    }


@router.get("/v1/admin/music/groups", dependencies=[Depends(require_device_read)])
async def list_groups() -> dict[str, Any]:
    """The named groups (``groups``: id, name, rooms) and the shared queues
    playing now (``shared``: id, name, host_room, rooms — host first,
    group_id, everywhere, listeners — the phones listening along,
    ``[{device_id, name}]``)."""
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
    if not music_groups.has_own_player(host) or not await _playing(host):
        raise HTTPException(status_code=409, detail=f"nothing is playing in {body.host_room}")
    rooms: list[str] = []
    unknown = [r for r in body.rooms if r not in by_id]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown room: {unknown[0]}.")
    # A room with no music player of its own (adopted, never connected)
    # can't be in a shared queue: asked for by name it is refused, and a
    # group or "everywhere" leaves it out.
    playerless = [r for r in body.rooms if not music_groups.has_own_player(r)]
    if playerless:
        raise HTTPException(
            status_code=409, detail=f"{playerless[0]} has no music player yet (it hasn't connected)",
        )
    rooms += body.rooms
    if body.group_id is not None:
        g = music_groups.group(body.group_id)
        if g is None:
            raise HTTPException(status_code=404, detail="no such group")
        rooms += [r for r in g.rooms if r in by_id and by_id[r].online
                  and music_groups.has_own_player(r)]
    if body.everywhere:
        rooms += [r.room_id for r in house if r.online and music_groups.has_own_player(r.room_id)]
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
    if not music_groups.has_own_player(body.room_id):
        raise HTTPException(
            status_code=409, detail=f"{body.room_id} has no music player yet (it hasn't connected)",
        )
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


# ─── Listening along (1.5.0) ─────────────────────────────────────────────


class _ListenJoinBody(BaseModel):
    #: The room whose music to hear (any room of a shared queue will do).
    room_id: str | None = Field(default=None, max_length=64)
    device_id: str | None = Field(default=None, max_length=128)


class _ListenDeviceBody(BaseModel):
    device_id: str | None = Field(default=None, max_length=128)


def _refuse(status: int, code: str, message: str) -> HTTPException:
    """A refusal the phone acts on by ``code``; ``message`` is a sentence it
    may show as is."""
    return HTTPException(status_code=status, detail={"code": code, "message": message})


_NO_DEVICE = ("no_device", "This phone didn't say which phone it is.")
_BAD_DEVICE = ("bad_device", "This phone's id isn't one Domovoi can use.")
_TOO_MANY = ("too_many_listeners", "Too many phones are listening along already.")


def _device_id(raw: str | None) -> str:
    """The body's ``device_id``, or the refusal: 400 ``no_device`` when it
    is missing, 400 ``bad_device`` when it isn't the devices tier's id
    shape (music_listeners.DEVICE_ID_RE)."""
    device_id = (raw or "").strip()
    if not device_id:
        raise _refuse(400, *_NO_DEVICE)
    if not music_listeners.valid_device_id(device_id):
        raise _refuse(400, *_BAD_DEVICE)
    return device_id


def _host_part(value: str) -> str | None:
    """The host of a Host-style value: ``192.168.1.20``, ``domovoi.local``,
    ``[fe80::1]``, ``fe80::1`` or any of them with a ``:port`` — a bare IPv6
    address is read whole, never cut at its first colon."""
    value = value.strip()
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip("[]")))
    except ValueError:
        pass
    try:
        return urlsplit(f"//{value}").hostname
    except ValueError:
        return None


def _dialed_host(request: Request) -> str | None:
    """The address the phone dialed to reach this server: the web proxy
    passes it on as ``X-Forwarded-Host`` (the core is a hop behind it),
    else this request's own Host. Only ever used to build the stream URL
    handed back to this same caller."""
    forwarded = (request.headers.get("x-forwarded-host") or "").split(",")[0]
    return _host_part(forwarded) or request.url.hostname


async def _player_state(host: str) -> str | None:
    from domovoi.clients.mpd import own_player

    try:
        client = own_player(host)
        return await client.state() if client is not None else None
    except Exception:  # noqa: BLE001 — not answering: nothing to hear
        return None


def _listen_answer(request: Request, entry: music_listeners.Listener, state: str) -> dict[str, Any]:
    """What a join (and a heartbeat while listening) answers."""
    from domovoi import lan_address
    from domovoi.clients.mpd import own_stream_url_for

    url = own_stream_url_for(entry.host)
    if url:
        url = lan_address.stream_url_for_peer(url, _dialed_host(request))
    sh = music_groups.share_of(entry.host)
    return {
        "room_id": entry.room_id,
        "host_room": entry.host,
        "stream_url": url,
        "state": state,
        "group": sh.to_dict() if sh is not None else None,
        "listeners": music_listeners.listing(entry.host),
        "heartbeat_sec": music_listeners.HEARTBEAT_SEC,
    }


@router.post("/v1/admin/music/listen/join", dependencies=[Depends(require_device)])
async def listen_join(body: _ListenJoinBody, request: Request) -> dict[str, Any]:
    """This phone (``device_id``) listens along to the music playing in
    ``room_id``: it plays the stream of the room's player (its shared
    queue's host's when it is in one). A phone listening elsewhere moves.
    Refusals carry ``detail: {code, message}``: 400 ``no_device``, 400
    ``bad_device``, 404 ``no_such_room``, 409 ``no_player`` (the room never
    connected), 409 ``nothing_playing`` (its player isn't playing or
    paused), 409 ``too_many_listeners`` (MAX_LISTENERS phones listen
    already)."""
    device_id = _device_id(body.device_id)
    if music_listeners.full_for(device_id):
        raise _refuse(409, *_TOO_MANY)
    room_id = (body.room_id or "").strip()
    room = None
    if room_id:
        room = next((r for r in await _house(request) if r.room_id == room_id), None)
    if room is None:
        raise _refuse(404, "no_such_room", "That room isn't in this house.")
    where = music_listeners.room_name(room.room_id, room.label)
    host = music_groups.host_of(room_id) or room_id
    if not music_groups.has_own_player(host):
        raise _refuse(409, "no_player", f"{where} has no music player yet.")
    state = await _player_state(host)
    if state not in ("play", "pause"):
        raise _refuse(409, "nothing_playing", f"Nothing is playing in {where}.")
    name = await music_listeners.device_name(device_id)
    # Again, with nothing awaited between the check and the entry: other
    # phones may have joined while the name was read.
    if music_listeners.full_for(device_id):
        raise _refuse(409, *_TOO_MANY)
    entry = await music_listeners.join(device_id, host, room_id=room_id, label=room.label,
                                       name=name, state=state)
    # The host was read before the awaits above: a room that joined a
    # shared queue meanwhile hands this phone on to that queue's host.
    await music_listeners.follow(entry)
    if entry.host != host:
        state = await _player_state(entry.host) or entry.state
    return _listen_answer(request, entry, state)


@router.post("/v1/admin/music/listen/leave", dependencies=[Depends(require_device)])
async def listen_leave(body: _ListenDeviceBody) -> dict[str, Any]:
    """This phone stops listening along: ``{left: <host_room>}``. Nothing
    changes in any room. 404 ``not_listening`` when it wasn't; 400
    ``no_device`` / ``bad_device`` as for a join."""
    device_id = _device_id(body.device_id)
    entry = await music_listeners.leave(device_id)
    if entry is None:
        raise _refuse(404, "not_listening", "This phone isn't listening along.")
    return {"left": entry.host}


@router.post("/v1/admin/music/listen/heartbeat", dependencies=[Depends(require_device)])
async def listen_heartbeat(body: _ListenDeviceBody, request: Request) -> dict[str, Any]:
    """Sent every ``heartbeat_sec`` while the phone listens. Always 200:
    still listening, the join's answer plus ``listening: true`` (a new
    ``stream_url`` after the music moved to another room: switch to it);
    else ``{listening: false, reason, message}``, ``reason`` one of
    ``stopped`` (a person stopped the music), ``ended`` (the queue ran
    out), ``not_listening`` (left, expired, or the core restarted without
    it). A ``device_id`` that is missing or not the devices tier's shape
    was never listening: ``not_listening``."""
    device_id = (body.device_id or "").strip()
    if not music_listeners.valid_device_id(device_id):
        return {"listening": False, "reason": music_listeners.REASON_NOT_LISTENING,
                "message": music_listeners.NOT_LISTENING_MESSAGE}
    entry = music_listeners.get(device_id)

    async def over(reason: str, message: str) -> dict[str, Any]:
        # Only this entry: a join that replaced it meanwhile stays.
        await music_listeners.forget(device_id, entry)
        return {"listening": False, "reason": reason, "message": message}

    if entry is None:
        return await over(music_listeners.REASON_NOT_LISTENING, music_listeners.NOT_LISTENING_MESSAGE)
    if not entry.live:
        return await over(entry.reason or music_listeners.REASON_STOPPED, entry.message or "")
    music_listeners.touch(entry)
    await music_listeners.follow(entry)
    if not music_groups.has_own_player(entry.host):
        await music_listeners.host_stopped(entry.host, music_listeners.REASON_STOPPED)
        return await over(entry.reason or music_listeners.REASON_STOPPED, entry.message or "")
    host = entry.host
    state = await _player_state(host)
    # The host may have joined a shared queue while its player was read.
    await music_listeners.follow(entry)
    if entry.host == host:
        await music_listeners.look(host, state)
    else:
        state = await _player_state(entry.host)
    if not entry.live:
        return await over(entry.reason or music_listeners.REASON_ENDED, entry.message or "")
    # A player that didn't answer: the state it last had, never a "stop"
    # it didn't say (the phone would show the music stopped).
    if state not in ("play", "pause"):
        state = entry.state
    return {**_listen_answer(request, entry, state), "listening": True}
