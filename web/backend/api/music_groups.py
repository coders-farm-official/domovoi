"""Groups on the dashboard: named sets of rooms and the shared queues
(domovoi/music_groups.py in the core, which routes every room's music and
so owns them).

* ``/api/music/groups…`` proxies the core's ``/v1/admin/music/groups`` and
  ``/v1/admin/music/shared`` routes, at the same tiers: reading is a
  household read, every change a household action. Never a guest's.
* :func:`live_groups` reads the shared queues playing now straight from the
  database (V060), for the now-playing cards and the room queue: a member
  room shows its host's song, and its queue's "who added it" is the host's.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi.admin_auth import require_device, require_device_read
from web.backend.db import session_scope
from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    delete_admin,
    get_admin,
    patch_admin,
    post_admin,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/music/groups", tags=["music"])

DEVICE = [Depends(require_device)]
DEVICE_READ = [Depends(require_device_read)]


# ─── The live shared queues, from the database ───────────────────────────

#: Seconds a read of the shared queues is reused: the now-playing poll asks
#: once per room, and the satellites list once per room too.
_MEMO_SEC = 1.0
_memo: tuple[float, dict[str, dict[str, Any]]] | None = None


def forget() -> None:
    """Drop the memo (tests, and after this process changed a group)."""
    global _memo
    _memo = None


async def live_groups() -> dict[str, dict[str, Any]]:
    """room id -> the shared queue it hears, for every room in one:
    ``{id, name, host_room, rooms, group_id, everywhere}`` (the core's
    ``Share.to_dict``; ``rooms`` lists the host first). Empty when the
    database can't answer: the cards then read each room's own player."""
    global _memo
    now = time.monotonic()
    if _memo is not None and now - _memo[0] < _MEMO_SEC:
        return _memo[1]
    out: dict[str, dict[str, Any]] = {}
    try:
        async with session_scope() as s:
            rows = (await s.execute(text(
                "SELECT q.id::text, q.host_room_id, q.group_id, q.everywhere, g.name, "
                "coalesce(array_agg(r.room_id ORDER BY r.joined_at, r.room_id) "
                "FILTER (WHERE r.room_id IS NOT NULL), '{}') "
                "FROM shared_queues q "
                "LEFT JOIN room_groups g ON g.id = q.group_id "
                "LEFT JOIN shared_queue_rooms r ON r.queue_id = q.id "
                "WHERE q.ended_at IS NULL "
                "GROUP BY q.id, q.host_room_id, q.group_id, q.everywhere, g.name"
            ))).all()
    except Exception as e:  # noqa: BLE001 — the cards read without groups
        log.debug("groups: couldn't read shared queues: %s", e)
        rows = []
    for sid, host, gid, everywhere, gname, members in rows:
        members = [m for m in (members or []) if m and m != host]
        if not members:
            continue
        name = gname or ("Everywhere" if everywhere else None)
        info = {
            "id": sid,
            "name": name,
            "host_room": host,
            "rooms": [host, *members],
            "group_id": gid,
            "everywhere": bool(everywhere),
        }
        for room in info["rooms"]:
            out.setdefault(room, info)
    _memo = (now, out)
    return out


async def host_room_for(room_id: str) -> str:
    """The room whose player ``room_id`` hears (itself outside a group)."""
    info = (await live_groups()).get(room_id)
    return info["host_room"] if info else room_id


# ─── The proxies ─────────────────────────────────────────────────────────


class GroupBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    rooms: list[str] = Field(..., min_length=2, max_length=64)


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    rooms: list[str] | None = Field(default=None, min_length=2, max_length=64)


class ShareBody(BaseModel):
    host_room: str = Field(..., min_length=1, max_length=64)
    rooms: list[str] = Field(default_factory=list, max_length=64)
    group_id: int | None = Field(default=None, ge=1)
    everywhere: bool = False
    device_id: str | None = Field(default=None, max_length=64)


class JoinBody(BaseModel):
    room_id: str = Field(..., min_length=1, max_length=64)
    host_room: str = Field(..., min_length=1, max_length=64)
    device_id: str | None = Field(default=None, max_length=64)


class LeaveBody(BaseModel):
    room_id: str = Field(..., min_length=1, max_length=64)


def _done(status: int, payload: Any):
    forget()
    return bridge_response(status, payload)


@router.get("", dependencies=DEVICE_READ)
async def list_groups(request: Request):
    """The named groups (``groups``: id, name, rooms) and the shared queues
    playing now (``shared``: id, name, host_room, rooms, group_id,
    everywhere)."""
    status, payload = await get_admin("/v1/admin/music/groups", headers=auth_forward_headers(request))
    return bridge_response(status, payload)


@router.post("", dependencies=DEVICE)
async def create_group(body: GroupBody, request: Request):
    """Make a named group of two or more rooms. 400 says why a name can't
    be used, in words."""
    status, payload = await post_admin(
        "/v1/admin/music/groups", body.model_dump(), headers=auth_forward_headers(request),
    )
    return _done(status, payload)


@router.patch("/{group_id}", dependencies=DEVICE)
async def update_group(group_id: int, body: GroupPatch, request: Request):
    """Rename a group, or pick its rooms."""
    status, payload = await patch_admin(
        f"/v1/admin/music/groups/{group_id}", body.model_dump(exclude_none=True),
        headers=auth_forward_headers(request),
    )
    return _done(status, payload)


@router.delete("/{group_id}", dependencies=DEVICE)
async def delete_group(group_id: int, request: Request):
    """Delete a named group (music playing in its rooms plays on)."""
    status, payload = await delete_admin(
        f"/v1/admin/music/groups/{group_id}", headers=auth_forward_headers(request),
    )
    return _done(status, payload)


@router.post("/shared", dependencies=DEVICE)
async def start_shared(body: ShareBody, request: Request):
    """Other rooms hear ``host_room``'s music: ``rooms``, a named group's
    connected rooms (``group_id``) or every connected room
    (``everywhere``). 409 when nothing plays in ``host_room``."""
    status, payload = await post_admin(
        "/v1/admin/music/shared", body.model_dump(), headers=auth_forward_headers(request),
    )
    return _done(status, payload)


@router.post("/shared/join", dependencies=DEVICE)
async def join_shared(body: JoinBody, request: Request):
    """``room_id`` hears ``host_room``'s music."""
    status, payload = await post_admin(
        "/v1/admin/music/shared/join", body.model_dump(), headers=auth_forward_headers(request),
    )
    return _done(status, payload)


@router.post("/shared/leave", dependencies=DEVICE)
async def leave_shared(body: LeaveBody, request: Request):
    """``room_id`` leaves its group and goes quiet; the others play on."""
    status, payload = await post_admin(
        "/v1/admin/music/shared/leave", body.model_dump(), headers=auth_forward_headers(request),
    )
    return _done(status, payload)


@router.post("/shared/{share_id}/end", dependencies=DEVICE)
async def end_shared(share_id: str, request: Request):
    """End a shared queue: the other rooms stop, the host plays on."""
    status, payload = await post_admin(
        f"/v1/admin/music/shared/{share_id}/end", headers=auth_forward_headers(request),
    )
    return _done(status, payload)
