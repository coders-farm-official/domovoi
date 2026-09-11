"""Editable room queues — ``/api/music/queue``.

Every other music action REPLACES a room's queue (cast, play playlist, play
track). This router is the one that edits it in place: read it, append to it,
reorder it, drop one entry — and say who put each entry there.

**MPD remains the queue.** There is no DB copy. The core's
``/v1/admin/music/queue/*`` endpoints do the MPD work (the web process has no
per-room client cache or satellite WebSocket); this layer adds the two things
MPD has no concept of:

* **provenance** — ``room_queue_items`` maps (room, MPD songid) → the device
  that added it, so the UI can render an unobtrusive "added by Kitchen iPad".
  Keyed by songid, never position, because a songid survives reordering.
  Rows whose songid has left the queue are reaped on read.
* **blocks** — ``queue_device_blocks`` lets an admin take queue editing away
  from a named device, per-room or everywhere.

**The blocklist is household policy, not a security boundary.** A device id
is self-asserted by the client over a trusted LAN, exactly like the rest of
the daily tier, so someone determined can claim a different one. It reliably
stops the kids' tablet from hijacking the kitchen queue; it does not stop an
attacker, and the dashboard says so where blocks are managed. Managing blocks
IS admin-gated (``require_admin_mutation``), so it can't be undone from the
device it was applied to.

A block matches on device id OR device name: the id survives a rename (the
obvious way to slip a block) and the name survives a reinstall (new id, same
household label). Creating a block from the roster fills both.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from domovoi.admin_auth import require_admin_mutation, require_admin_read
from web.backend.db import session_scope
from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    get_admin,
    post_admin,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/music/queue", tags=["music"])


# ─── Schemas ───────────────────────────────────────────────────────────────


class QueueItem(BaseModel):
    song_id: int
    pos: int
    file: str
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration_sec: int | None = None
    # Provenance. Null for an entry we have no record of — a voice command,
    # a cast that predates this feature, or an MPD mutation from outside
    # Domovoi. The UI renders nothing rather than guessing.
    added_by: str | None = None
    added_by_device_id: str | None = None
    added_at: Any = None
    # True for the entry MPD is currently on.
    playing: bool = False


class RoomQueue(BaseModel):
    room_id: str
    items: list[QueueItem] = Field(default_factory=list)
    current_song_id: int | None = None
    # Whether the CALLING device may edit this queue. The client disables its
    # own controls from this rather than discovering the 403 on first click.
    editable: bool = True
    blocked_reason: str | None = None


# `device_id` is REQUIRED on every edit, optional only on the read.
#
# Not because it proves anything — it's self-asserted — but because the
# alternative is a blocklist anyone evades by leaving the field out, which is
# a great deal easier than claiming someone else's id. Every client sends it
# already; a caller that won't name itself doesn't get to edit the queue.
class QueueAddRequest(BaseModel):
    track_ids: list[int] = Field(..., min_length=1, max_length=500)
    device_id: str = Field(..., min_length=3, max_length=64)


class QueueRemoveRequest(BaseModel):
    song_ids: list[int] = Field(..., min_length=1, max_length=500)
    device_id: str = Field(..., min_length=3, max_length=64)


class QueueMoveRequest(BaseModel):
    song_id: int = Field(..., ge=0)
    to_position: int = Field(..., ge=0)
    device_id: str = Field(..., min_length=3, max_length=64)


class QueueClearRequest(BaseModel):
    device_id: str = Field(..., min_length=3, max_length=64)


class QueueBlock(BaseModel):
    id: int
    device_id: str | None = None
    device_name: str | None = None
    # Null = every room.
    room_id: str | None = None
    note: str | None = None
    created_at: Any = None


class QueueBlockCreate(BaseModel):
    device_id: str | None = Field(default=None, max_length=64)
    device_name: str | None = Field(default=None, max_length=60)
    room_id: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=200)


# ─── Block enforcement ─────────────────────────────────────────────────────


async def _device_name_for(s: Any, device_id: str | None) -> str | None:
    if not device_id:
        return None
    row = await s.execute(
        text("SELECT name FROM devices WHERE device_id = :id"), {"id": device_id}
    )
    found = row.first()
    return found[0] if found else None


async def _block_for(
    s: Any, device_id: str | None, device_name: str | None, room_id: str
) -> dict[str, Any] | None:
    """The block that applies to this device in this room, or None.

    Matches id OR name, and an all-rooms block (``room_id IS NULL``) as well
    as one scoped to this room. Room-scoped wins the ordering so the note the
    caller sees is the most specific one.
    """
    if not device_id and not device_name:
        return None
    row = await s.execute(
        text(
            """
            SELECT id, device_id, device_name, room_id, note
            FROM queue_device_blocks
            WHERE (room_id IS NULL OR room_id = :room_id)
              AND (
                   (device_id   IS NOT NULL AND device_id   = :device_id)
                OR (device_name IS NOT NULL AND device_name = :device_name)
              )
            ORDER BY (room_id IS NULL), id
            LIMIT 1
            """
        ),
        {
            "room_id": room_id,
            # Bind explicit NULLs rather than omitting: the IS NOT NULL guards
            # mean a NULL bind simply never matches that arm.
            "device_id": device_id,
            "device_name": device_name,
        },
    )
    found = row.first()
    if found is None:
        return None
    return {
        "id": int(found[0]), "device_id": found[1], "device_name": found[2],
        "room_id": found[3], "note": found[4],
    }


def _blocked_message(block: dict[str, Any], room_id: str) -> str:
    who = block.get("device_name") or block.get("device_id") or "this device"
    where = "every room" if block.get("room_id") is None else f"{room_id}"
    note = block.get("note")
    base = f"{who} isn't allowed to edit the queue in {where}"
    return f"{base} ({note})" if note else base


async def _assert_can_edit(device_id: str, room_id: str) -> tuple[str, str | None]:
    """Raise 403 when this device is blocked here; otherwise return
    ``(device_id, device_name)`` for provenance stamping. ``device_name`` is
    None for a device that has never registered — the id is still recorded, so
    the entry shows no "added by" rather than a wrong one."""
    async with session_scope() as s:
        device_name = await _device_name_for(s, device_id)
        block = await _block_for(s, device_id, device_name, room_id)
    if block is not None:
        raise HTTPException(status_code=403, detail=_blocked_message(block, room_id))
    return device_id, device_name


# ─── Read ──────────────────────────────────────────────────────────────────


@router.get("/{room_id}", response_model=RoomQueue)
async def read_queue(
    room_id: str, request: Request, device_id: str | None = None
) -> RoomQueue:
    """The room's live queue with provenance joined on.

    ``device_id`` is optional and only affects ``editable``/``blocked_reason``
    — reading a queue is never blocked, so a blocked device can still SEE
    what's playing and why it can't change it.
    """
    status, payload = await get_admin(
        f"/v1/admin/music/queue/{room_id}", headers=auth_forward_headers(request)
    )
    if status == 0:
        raise HTTPException(
            status_code=502, detail="domovoi core unreachable; can't read the queue"
        )
    if status != 200 or not isinstance(payload, dict):
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        raise HTTPException(status_code=status or 502, detail=detail or "queue read failed")

    raw_items = payload.get("items") or []
    current = payload.get("current_song_id")
    song_ids = [int(i["song_id"]) for i in raw_items if i.get("song_id") is not None]

    async with session_scope() as s:
        # Prefer the device's CURRENT name (a rename relabels the queue) and
        # fall back to the snapshot taken at add time, which is all that's
        # left once a device has been forgotten.
        rows = await s.execute(
            text(
                """
                SELECT q.song_id,
                       COALESCE(d.name, q.device_name) AS name,
                       q.device_id,
                       q.added_at
                FROM room_queue_items q
                LEFT JOIN devices d ON d.device_id = q.device_id
                WHERE q.room_id = :room_id
                """
            ),
            {"room_id": room_id},
        )
        provenance = {
            int(r[0]): (r[1], r[2], r[3]) for r in rows.all()
        }
        # Reap rows MPD no longer has. Cheap, bounded by the room's own
        # history, and keeps a long-lived room from accumulating forever.
        # Skipped when the queue is empty so a core read that came back
        # short can't wipe provenance for a queue that's still there.
        stale = [sid for sid in provenance if sid not in set(song_ids)]
        if stale and song_ids:
            await s.execute(
                text(
                    "DELETE FROM room_queue_items "
                    "WHERE room_id = :room_id AND song_id = ANY(:ids)"
                ),
                {"room_id": room_id, "ids": stale},
            )

        device_name = await _device_name_for(s, device_id)
        block = await _block_for(s, device_id, device_name, room_id)

    items: list[QueueItem] = []
    for raw in raw_items:
        song_id = int(raw["song_id"])
        name, dev_id, added_at = provenance.get(song_id, (None, None, None))
        items.append(
            QueueItem(
                song_id=song_id,
                pos=int(raw.get("pos") or 0),
                file=str(raw.get("file") or ""),
                title=raw.get("title"),
                artist=raw.get("artist"),
                album=raw.get("album"),
                duration_sec=raw.get("duration_sec"),
                added_by=name,
                added_by_device_id=dev_id,
                added_at=added_at,
                playing=current is not None and song_id == int(current),
            )
        )
    return RoomQueue(
        room_id=room_id,
        items=items,
        current_song_id=int(current) if current is not None else None,
        editable=block is None,
        blocked_reason=_blocked_message(block, room_id) if block else None,
    )


# ─── Edits ─────────────────────────────────────────────────────────────────


@router.post("/{room_id}/add")
async def add_to_queue(room_id: str, request: Request, body: QueueAddRequest):
    """Append library tracks to the room's queue and stamp provenance."""
    device_id, device_name = await _assert_can_edit(body.device_id, room_id)
    status, payload = await post_admin(
        f"/v1/admin/music/queue/{room_id}/add",
        {"track_ids": body.track_ids},
        headers=auth_forward_headers(request),
    )
    if status == 200 and isinstance(payload, dict):
        queued = payload.get("queued") or []
        song_ids = [int(q["song_id"]) for q in queued if q.get("song_id") is not None]
        if song_ids:
            await _stamp_provenance(room_id, song_ids, device_id, device_name)
    return bridge_response(status, payload)


async def _stamp_provenance(
    room_id: str,
    song_ids: list[int],
    device_id: str,
    device_name: str | None,
) -> None:
    """Record who added these songids. ON CONFLICT because MPD reuses a
    songid only after a very long wrap, but a provenance row for a songid
    that has since been removed and re-issued must not collide."""
    async with session_scope() as s:
        for song_id in song_ids:
            await s.execute(
                text(
                    """
                    INSERT INTO room_queue_items
                        (room_id, song_id, device_id, device_name)
                    VALUES (:room_id, :song_id, :device_id, :device_name)
                    ON CONFLICT (room_id, song_id) DO UPDATE
                    SET device_id   = EXCLUDED.device_id,
                        device_name = EXCLUDED.device_name,
                        added_at    = now()
                    """
                ),
                {
                    "room_id": room_id,
                    "song_id": song_id,
                    "device_id": device_id,
                    "device_name": device_name,
                },
            )


@router.post("/{room_id}/remove")
async def remove_from_queue(room_id: str, request: Request, body: QueueRemoveRequest):
    await _assert_can_edit(body.device_id, room_id)
    status, payload = await post_admin(
        f"/v1/admin/music/queue/{room_id}/remove",
        {"song_ids": body.song_ids},
        headers=auth_forward_headers(request),
    )
    if status == 200 and isinstance(payload, dict):
        removed = [int(sid) for sid in (payload.get("removed") or [])]
        if removed:
            async with session_scope() as s:
                await s.execute(
                    text(
                        "DELETE FROM room_queue_items "
                        "WHERE room_id = :room_id AND song_id = ANY(:ids)"
                    ),
                    {"room_id": room_id, "ids": removed},
                )
    return bridge_response(status, payload)


@router.post("/{room_id}/move")
async def move_in_queue(room_id: str, request: Request, body: QueueMoveRequest):
    """Reorder. Provenance is keyed by songid, so a move needs no DB write —
    that's the reason for keying it that way."""
    await _assert_can_edit(body.device_id, room_id)
    status, payload = await post_admin(
        f"/v1/admin/music/queue/{room_id}/move",
        {"song_id": body.song_id, "to_position": body.to_position},
        headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


@router.post("/{room_id}/clear")
async def clear_queue(room_id: str, request: Request, body: QueueClearRequest):
    await _assert_can_edit(body.device_id, room_id)
    status, payload = await post_admin(
        f"/v1/admin/music/queue/{room_id}/clear",
        {},
        headers=auth_forward_headers(request),
    )
    if status == 200:
        async with session_scope() as s:
            await s.execute(
                text("DELETE FROM room_queue_items WHERE room_id = :room_id"),
                {"room_id": room_id},
            )
    return bridge_response(status, payload)


# ─── Blocks (admin) ────────────────────────────────────────────────────────
#
# Mounted under a literal path segment that can't collide with a room id:
# FastAPI matches in declaration order and "/{room_id}" is declared above,
# so these live on their own prefix instead.

blocks_router = APIRouter(prefix="/api/music/queue-blocks", tags=["music"])

_BLOCK_COLUMNS = "id, device_id, device_name, room_id, note, created_at"


def _row_to_block(r: Any) -> QueueBlock:
    return QueueBlock(
        id=int(r[0]), device_id=r[1], device_name=r[2],
        room_id=r[3], note=r[4], created_at=r[5],
    )


@blocks_router.get(
    "", response_model=list[QueueBlock], dependencies=[Depends(require_admin_read)]
)
async def list_blocks() -> list[QueueBlock]:
    async with session_scope() as s:
        rows = await s.execute(
            text(
                f"SELECT {_BLOCK_COLUMNS} FROM queue_device_blocks "
                "ORDER BY (room_id IS NULL) DESC, room_id NULLS FIRST, id"
            )
        )
        return [_row_to_block(r) for r in rows.all()]


@blocks_router.post(
    "",
    response_model=QueueBlock,
    status_code=201,
    dependencies=[Depends(require_admin_mutation)],
)
async def create_block(payload: QueueBlockCreate) -> QueueBlock:
    """Block a device from editing one room's queue, or every room's
    (``room_id`` omitted). Needs a device id, a name, or both — a block
    naming neither would match nothing, and the schema refuses it."""
    device_id = (payload.device_id or "").strip() or None
    device_name = " ".join((payload.device_name or "").split()) or None
    room_id = (payload.room_id or "").strip() or None
    if not device_id and not device_name:
        raise HTTPException(
            status_code=400,
            detail="a block needs a device_id, a device_name, or both",
        )
    async with session_scope() as s:
        try:
            row = await s.execute(
                text(
                    f"""
                    INSERT INTO queue_device_blocks
                        (device_id, device_name, room_id, note)
                    VALUES (:device_id, :device_name, :room_id, :note)
                    RETURNING {_BLOCK_COLUMNS}
                    """
                ),
                {
                    "device_id": device_id,
                    "device_name": device_name,
                    "room_id": room_id,
                    "note": payload.note,
                },
            )
        except IntegrityError as e:
            # The paired partial unique indexes make a duplicate block an
            # integrity error (and the CHECK catches a block naming nothing,
            # though the explicit test above gets there first with a better
            # message). Either way it's a conflict, not a 500.
            raise HTTPException(
                status_code=409, detail="that device is already blocked there"
            ) from e
        result = row.first()
    if result is None:  # pragma: no cover — RETURNING always yields on insert
        raise HTTPException(status_code=500, detail="insert returned no row")
    return _row_to_block(result)


@blocks_router.delete(
    "/{block_id}", status_code=204, dependencies=[Depends(require_admin_mutation)]
)
async def delete_block(block_id: int) -> None:
    async with session_scope() as s:
        result = await s.execute(
            text("DELETE FROM queue_device_blocks WHERE id = :id"), {"id": block_id}
        )
    if (result.rowcount or 0) == 0:
        raise HTTPException(status_code=404, detail=f"block {block_id} not found")
