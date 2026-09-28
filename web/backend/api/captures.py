"""Command recordings API — the admin's side of the opt-in collection.

An admin turns recording on or off per room, lists what has been kept,
plays a recording, labels it ("cut off too early" / "fine" / "waited too
long") and deletes it. The recordings are household speech kept for one
purpose — tuning when Domovoi stops listening (design notes 2026-09-28) —
so every route here is the admin SECURITY tier at both ends:

* reads (``GET``) take ``require_admin_security_read``: an admin Bearer or
  the dashboard cookie, and **501 until first-run setup is complete**. Not
  the device tier every other speech read uses: the household token opens
  conversations and voice notes to every paired phone, and these are raw
  audio of anyone who spoke near an opted-in satellite.
* writes take ``require_admin_security``: Bearer-only, same 501.

The opt-in is a row in ``command_capture_rooms`` (V016), written here
directly — the web process shares the database. The recordings are files
the core writes under ``COMMAND_CAPTURES_DIR``; this process reads, labels
and deletes them in place (it runs on the same host, as it does for the
wake-word clips) through :mod:`domovoi.command_captures`, whose path
helpers keep every name inside that directory. No other route serves it.

Opting a room out deletes its row FIRST and its files second, in the same
request. The core asks about the opt-in again after each write, so a
recording that lands after this delete is removed by the core itself (and
the pruner sweeps any room with files but no row).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi import command_captures as cc
from domovoi.admin_auth import require_admin_security, require_admin_security_read
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/captures", tags=["captures"])

READ = [Depends(require_admin_security_read)]
WRITE = [Depends(require_admin_security)]

_ROOM_MAX = 120


class CaptureRoom(BaseModel):
    room_id: str
    enabled: bool
    enabled_at: datetime | None = None
    count: int = 0
    bytes: int = 0


class CaptureList(BaseModel):
    rooms: list[CaptureRoom]
    # Each capture is its sidecar as written (see domovoi/command_captures.py
    # build_sidecar) plus ``bytes``, the audio's size.
    captures: list[dict[str, Any]]
    count: int
    bytes: int
    cap_bytes: int
    retention_days: int
    labels: dict[str, str]
    # Why nothing can be recorded right now (an unsafe COMMAND_CAPTURES_DIR),
    # else null.
    problem: str | None = None


class LabelBody(BaseModel):
    # One of cut_off / fine / waited_too_long, or null to clear.
    label: str | None = Field(default=None, max_length=40)


class RoomState(BaseModel):
    room_id: str
    enabled: bool
    enabled_at: datetime | None = None
    deleted: int = 0


def _check_room(room_id: str) -> None:
    if not room_id or len(room_id) > _ROOM_MAX:
        raise HTTPException(status_code=422, detail="room_id must be 1-120 characters")


def _paths_or_404(room_id: str, capture_id: str):
    _check_room(room_id)
    paths = cc.capture_paths(room_id, capture_id)
    if paths is None:
        raise HTTPException(status_code=404, detail="recording not found")
    when = cc.capture_time(capture_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=cc.retention_days())
    if when is None or when < cutoff:
        raise HTTPException(status_code=404, detail="recording not found")
    return paths


@router.get("", response_model=CaptureList, dependencies=READ)
async def list_captures(
    room_id: str | None = Query(default=None, max_length=_ROOM_MAX),
) -> CaptureList:
    """Every room that is opted in or still has recordings, and the
    recordings themselves (one room with ``?room_id=``), newest first."""
    enabled = await cc.opted_in_rooms()
    captures, usage = await asyncio.to_thread(
        lambda: (cc.list_captures(room_id), cc.usage())
    )
    rooms: dict[str, CaptureRoom] = {
        rid: CaptureRoom(room_id=rid, enabled=True, enabled_at=at)
        for rid, at in enabled.items()
    }
    everything = captures if room_id is None else await asyncio.to_thread(cc.list_captures)
    for c in everything:
        rid = c.get("room_id")
        if isinstance(rid, str) and rid not in rooms:
            rooms[rid] = CaptureRoom(room_id=rid, enabled=False)
    for rid, room in rooms.items():
        entry = usage["by_dir"].get(cc.room_dir_name(rid)) or {}
        room.count = int(entry.get("count", 0))
        room.bytes = int(entry.get("bytes", 0))
    return CaptureList(
        rooms=sorted(rooms.values(), key=lambda r: r.room_id),
        captures=captures,
        count=usage["count"],
        bytes=usage["bytes"],
        cap_bytes=usage["cap_bytes"],
        retention_days=usage["retention_days"],
        labels=dict(cc.LABELS),
        problem=cc.root_problem(),
    )


@router.get(
    "/clips/{room_id}/{capture_id}/audio",
    dependencies=READ,
    response_class=FileResponse,
)
async def capture_audio(room_id: str, capture_id: str) -> FileResponse:
    """The recording's WAV (16 kHz mono), exactly as the satellite sent it."""
    wav, side = _paths_or_404(room_id, capture_id)
    if not wav.is_file() or not side.is_file():
        raise HTTPException(status_code=404, detail="recording not found")
    return FileResponse(
        wav,
        media_type="audio/wav",
        filename=f"{capture_id}.wav",
        headers={"Cache-Control": "no-store"},
    )


@router.patch("/clips/{room_id}/{capture_id}", dependencies=WRITE)
async def label_capture(room_id: str, capture_id: str, body: LabelBody) -> dict[str, Any]:
    """Record whether the capture ended at the right moment."""
    _paths_or_404(room_id, capture_id)
    if body.label is not None and body.label not in cc.LABELS:
        raise HTTPException(
            status_code=422,
            detail=f"label must be one of {sorted(cc.LABELS)} or null",
        )
    side = await asyncio.to_thread(cc.set_label, room_id, capture_id, body.label)
    if side is None:
        raise HTTPException(status_code=404, detail="recording not found")
    return side


@router.delete("/clips/{room_id}/{capture_id}", status_code=204, dependencies=WRITE)
async def delete_capture(room_id: str, capture_id: str) -> None:
    _check_room(room_id)
    if cc.capture_paths(room_id, capture_id) is None:
        raise HTTPException(status_code=404, detail="recording not found")
    if not await asyncio.to_thread(cc.delete_capture, room_id, capture_id):
        raise HTTPException(status_code=404, detail="recording not found")


async def _room_known(s: Any, room_id: str) -> bool:
    """A room that has connected (mpd_rooms) or been adopted (satellites)."""
    row = (
        await s.execute(
            text("SELECT 1 FROM mpd_rooms WHERE room_id = :r"), {"r": room_id}
        )
    ).first()
    if row is not None:
        return True
    try:
        async with s.begin_nested():
            row = (
                await s.execute(
                    text("SELECT 1 FROM satellites WHERE room_id = :r"), {"r": room_id}
                )
            ).first()
    except Exception:  # noqa: BLE001 — V003 not applied
        return False
    return row is not None


@router.put("/rooms/{room_id}", response_model=RoomState, dependencies=WRITE)
async def enable_room(room_id: str) -> RoomState:
    """Start keeping this room's command recordings. Idempotent: a room
    already on keeps its original ``enabled_at``."""
    _check_room(room_id)
    problem = cc.root_problem()
    if problem:
        raise HTTPException(
            status_code=409,
            detail=f"COMMAND_CAPTURES_DIR is not safe to record into: {problem}",
        )
    try:
        async with session_scope() as s:
            if not await _room_known(s, room_id):
                raise HTTPException(status_code=404, detail=f"unknown room {room_id!r}")
            await s.execute(
                text(
                    "INSERT INTO command_capture_rooms (room_id) VALUES (:r) "
                    "ON CONFLICT (room_id) DO NOTHING"
                ),
                {"r": room_id},
            )
            at = (
                await s.execute(
                    text("SELECT enabled_at FROM command_capture_rooms WHERE room_id = :r"),
                    {"r": room_id},
                )
            ).scalar_one()
    except HTTPException:
        raise
    except Exception as e:
        if cc.is_missing_table(e):
            raise HTTPException(
                status_code=503,
                detail="command recording needs database migration V016 — run Flyway",
            )
        raise
    log.info("command captures: room %s opted in", room_id)
    return RoomState(room_id=room_id, enabled=True, enabled_at=at)


@router.delete("/rooms/{room_id}", response_model=RoomState, dependencies=WRITE)
async def disable_room(room_id: str) -> RoomState:
    """Stop keeping this room's recordings and delete every one already
    kept — row first, files second (see the module docstring)."""
    _check_room(room_id)
    try:
        async with session_scope() as s:
            await s.execute(
                text("DELETE FROM command_capture_rooms WHERE room_id = :r"),
                {"r": room_id},
            )
    except Exception as e:
        if not cc.is_missing_table(e):
            raise
    deleted = await asyncio.to_thread(cc.delete_room, room_id)
    log.info("command captures: room %s opted out, %d recording(s) deleted", room_id, deleted)
    return RoomState(room_id=room_id, enabled=False, deleted=deleted)
