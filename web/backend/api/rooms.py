"""Rooms and their satellites (1.5.0): the room drawer's API.

A room is the place, a satellite a device in one room (V072). The core
owns the live registry of which device is in which room
(domovoi/room_devices.py), so every route here proxies the core's
``/v1/admin/rooms…`` and ``/v1/admin/satellites/{id}/room|name`` at the
same tier: the list is a household read (the phones' Rooms screens),
every change is the admin's. Shapes: docs/API_REFERENCE.md, "Rooms and
satellites".
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from domovoi.admin_auth import require_admin_mutation, require_device_read
from domovoi.room_devices import require_id_paths
from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    delete_admin,
    get_admin,
    patch_admin,
    post_admin,
)

# Every {room_id} / {satellite_id} here follows the one id rule
# (domovoi/room_ids.py): 422 before anything is proxied.
router = APIRouter(prefix="/api", tags=["rooms"], dependencies=[Depends(require_id_paths)])

READ = [Depends(require_device_read)]
ADMIN = [Depends(require_admin_mutation)]


class RoomName(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class SatelliteName(BaseModel):
    name: str | None = Field(default=None, max_length=120)


class RoomChoice(BaseModel):
    """An existing room's id OR a name for a new room."""

    room_id: str | None = Field(default=None, max_length=120)
    new_room_name: str | None = Field(default=None, max_length=120)


class MergeRooms(BaseModel):
    rooms: list[str] = Field(..., min_length=1, max_length=50)


def _bridge(result: tuple[int, Any]) -> Any:
    status, doc = result
    return doc if status == 200 else bridge_response(status, doc)


@router.get("/rooms", dependencies=READ)
async def list_rooms(request: Request) -> Any:
    """Every room with its satellites, and the merges on offer."""
    return _bridge(await get_admin("/v1/admin/rooms", headers=auth_forward_headers(request)))


@router.post("/rooms", dependencies=ADMIN)
async def create_room(body: RoomName, request: Request) -> Any:
    return _bridge(await post_admin(
        "/v1/admin/rooms", body.model_dump(), headers=auth_forward_headers(request)))


@router.patch("/rooms/{room_id}", dependencies=ADMIN)
async def rename_room(room_id: str, body: RoomName, request: Request) -> Any:
    return _bridge(await patch_admin(
        f"/v1/admin/rooms/{room_id}", body.model_dump(), headers=auth_forward_headers(request)))


@router.delete("/rooms/{room_id}", dependencies=ADMIN)
async def delete_room(room_id: str, request: Request) -> Any:
    return _bridge(await delete_admin(
        f"/v1/admin/rooms/{room_id}", headers=auth_forward_headers(request)))


@router.post("/rooms/{room_id}/merge", dependencies=ADMIN)
async def merge_rooms(room_id: str, body: MergeRooms, request: Request) -> Any:
    return _bridge(await post_admin(
        f"/v1/admin/rooms/{room_id}/merge", body.model_dump(),
        headers=auth_forward_headers(request)))


@router.post("/satellites/{satellite_id}/room", dependencies=ADMIN)
async def move_satellite(satellite_id: str, body: RoomChoice, request: Request) -> Any:
    return _bridge(await post_admin(
        f"/v1/admin/satellites/{satellite_id}/room", body.model_dump(),
        headers=auth_forward_headers(request)))


@router.patch("/satellites/{satellite_id}/name", dependencies=ADMIN)
async def name_satellite(satellite_id: str, body: SatelliteName, request: Request) -> Any:
    return _bridge(await patch_admin(
        f"/v1/admin/satellites/{satellite_id}/name", body.model_dump(),
        headers=auth_forward_headers(request)))


@router.post("/rooms/{room_id}/calibrate", dependencies=ADMIN)
async def calibrate_room(room_id: str, request: Request) -> Any:
    """"Tune the speakers": a chirp from each speaker in turn, a few seconds
    per speaker; the answer says what was tuned and what kept its value."""
    return _bridge(await post_admin(
        f"/v1/admin/rooms/{room_id}/calibrate", {}, timeout=90.0,
        headers=auth_forward_headers(request)))
