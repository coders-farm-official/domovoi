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

**Guests** (owner decisions 2026-10-10): a phone or browser holding no
household credential at all may read, add to, remove from and reorder the
queue of a room that is PLAYING — never clear it — and use its pause /
resume / skip / previous, until an admin bans that guest. This process
cannot see MPD, so it passes a guest through to the core, which decides
"playing" at the moment of the request and checks the ban again
(``domovoi/guest_queue.py``); the hop forwards only what the caller itself
sent (``auth_forward_headers``), so a guest reaches the core as a guest.
What stays here: the household switch (refused at this hop too while it
is off), the per-device blocks (a guest's self-asserted ``device_id`` is
checked like anyone's), guest bans (``domovoi/guest_bans.py``), a
per-address throttle on a guest's actions, and provenance — a guest's
entries read "added by a guest", never a device name looked up from an id
it merely claimed, and a guest is never shown which household device
queued what (the open now-playing read's rule, ``api/music.py``). An admin
sees which guest queued an entry, and bans from there or from the
guests-seen list (:data:`guests_router`, :data:`guest_bans_router`).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from domovoi import guest_bans
from domovoi.admin_auth import (
    GUEST_BANNED_DETAIL,
    GUEST_DEVICE_HEADER,
    GUEST_ROOM_IDLE_DETAIL,
    charge_guest_queue_edit,
    check_admin_request,
    check_queue_guest,
    guest_device_id,
    presents_household_credential,
    request_source,
    require_admin_mutation,
    require_admin_read,
    require_device,
)
from domovoi.config import settings as core_settings
from web.backend.db import session_scope
from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    get_admin,
    get_cached_snapshot,
    post_admin,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/music/queue", tags=["music"])

# Editing a room's queue is an ordinary household action (REV-1): the
# household device token or an admin Bearer, matching the core routes
# these proxy to. The per-device block below still applies on top — it is
# household policy WITHIN the credential, not a substitute for it. Clear
# stays here: it stops the room, so it is never a guest's.
DEVICE = [Depends(require_device)]

# How a guest's own entries are labelled. Stored as the row's device_name
# with a NULL device_id, so the read's join to ``devices`` can never swap
# in the name of a device whose id the guest claimed. Which guest it was
# goes in the row's guest_device_id / guest_source (V023), for an admin.
GUEST_ADDED_BY = "a guest"

# ``blocked_reason`` for a guest reading a room that isn't playing, and
# for a guest an admin banned.
GUEST_IDLE_REASON = "Pair this phone to change a room that isn't playing"
GUEST_BANNED_REASON = "An admin has stopped guests on this phone from changing the music"


def guests_may_edit() -> bool:
    """The live ``GUEST_QUEUE_EDITING``. Saved through the core (Settings →
    Configuration, tier ``hot``), which mutates the CORE's settings; this
    process's copy is whatever ``.env`` said at boot. So the value comes
    from the core's snapshot (refreshed every 1.5 s), falling back to this
    process's own copy only while the core has not answered. The core
    re-checks its own value on every request either way: this hop refusing
    early is what makes "off" read exactly as it did before."""
    live = (get_cached_snapshot() or {}).get("guest_queue_editing")
    if isinstance(live, bool):
        return live
    return bool(core_settings.guest_queue_editing)


async def _guest_ban(request: Request, device_id: str | None) -> dict[str, Any] | None:
    """The admin's ban on this guest — by ``device_id`` or its address —
    or None. Fails CLOSED (503) when the table can't be read."""
    try:
        return await guest_bans.find_ban(device_id, request_source(request))
    except Exception as e:  # noqa: BLE001 — the DB did not answer
        log.warning("guest ban check failed: %s", e)
        raise HTTPException(
            status_code=503, detail="couldn't check guest access right now"
        ) from e


async def assert_guest_not_banned(request: Request, device_id: str | None) -> None:
    """403 with :data:`GUEST_BANNED_DETAIL` for a banned guest."""
    if await _guest_ban(request, device_id) is not None:
        raise HTTPException(status_code=403, detail=GUEST_BANNED_DETAIL)


async def require_device_or_guest(request: Request) -> None:
    """Dependency for a guest-capable room action at this hop — the queue's
    add / remove / move, and the previous button: :func:`require_device`,
    exactly as before, for any request carrying a credential (and the
    pre-setup grace); otherwise a GUEST, while the household switch is on,
    charged against the per-address throttle, refused if an admin banned
    the device id it sends (``X-Domovoi-Device-Id``) or its address, and
    marked for the handler. Whether the room is playing is the core's to
    say — this hop cannot see MPD."""
    if await check_queue_guest(request, read=False, guests_allowed=guests_may_edit()):
        charge_guest_queue_edit(request)
        await assert_guest_not_banned(request, guest_device_id(request))
        request.state.queue_guest = True


GUEST_OR_DEVICE = [Depends(require_device_or_guest)]


def _is_guest(request: Request) -> bool:
    return bool(getattr(request.state, "queue_guest", False))


async def _guest_hop_headers(request: Request, device_id: str) -> dict[str, str]:
    """The core hop's headers for a queue edit. For a guest the body's
    ``device_id`` is the one the core matches a ban on and records — and
    if it differs from the header the dependency checked, it is checked
    here too, so neither id slips a ban. A household caller's hop is
    exactly what it was."""
    headers = auth_forward_headers(request)
    if _is_guest(request):
        if device_id != guest_device_id(request):
            await assert_guest_not_banned(request, device_id)
        headers[GUEST_DEVICE_HEADER] = device_id
    return headers


async def _reads_as_guest(request: Request) -> bool:
    """Whether a queue READ comes from a guest: no credential of any kind,
    on a box that has finished setup. Deliberately not a gate — a caller
    with a credential is judged by the core exactly as before, and nothing
    here charges a token's backoff."""
    if presents_household_credential(request, read=True):
        return False
    return await check_admin_request(request) == "no-auth"


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
    # For an entry a GUEST queued, and only in an answer to an admin: the
    # device id the guest sent and its address, which is what a guest ban
    # (POST /api/music/guest-bans) takes — the "ban this guest" button.
    guest_device_id: str | None = None
    guest_source: str | None = None
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
    # True when this answer was given to a GUEST (no household credential).
    # A guest may add, remove and move on a playing room, but never clear,
    # never remove the entry playing now, and sees no device names; the
    # client leaves those controls out instead of discovering the refusal.
    guest: bool = False
    # True when the caller holds an ADMIN session: the entries a guest
    # queued then carry guest_device_id / guest_source, for a ban.
    admin: bool = False


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

    A GUEST is passed through to the core with nothing but what it sent.
    For a room that isn't playing the core refuses it
    (``GUEST_ROOM_IDLE_DETAIL``), and this read answers that QUIETLY — an
    empty, non-editable queue with :data:`GUEST_IDLE_REASON` — because a
    phone that merely opened the tab must not be met with a pairing prompt.
    A banned guest reads a playing room's queue with ``editable: false``.
    With the household switch off the core's 401 passes through as before.
    """
    guest = await _reads_as_guest(request)
    admin = (not guest) and await check_admin_request(request) in ("ok", "cookie-only")
    status, payload = await get_admin(
        f"/v1/admin/music/queue/{room_id}", headers=auth_forward_headers(request)
    )
    if status == 0:
        raise HTTPException(
            status_code=502, detail="domovoi core unreachable; can't read the queue"
        )
    if (
        guest
        and status == 403
        and isinstance(payload, dict)
        and payload.get("detail") == GUEST_ROOM_IDLE_DETAIL
    ):
        return RoomQueue(
            room_id=room_id,
            editable=False,
            blocked_reason=GUEST_IDLE_REASON,
            guest=True,
        )
    if status != 200 or not isinstance(payload, dict):
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        raise HTTPException(status_code=status or 502, detail=detail or "queue read failed")

    raw_items = payload.get("items") or []
    current = payload.get("current_song_id")
    song_ids = [int(i["song_id"]) for i in raw_items if i.get("song_id") is not None]

    guest_ids: dict[int, tuple[str | None, str | None]] = {}
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

        # Which guest queued what — read only for an admin, and only when a
        # guest queued something (V023's columns: nothing else reads them).
        if admin and any(
            dev is None and name == GUEST_ADDED_BY for name, dev, _at in provenance.values()
        ):
            found = await s.execute(
                text(
                    "SELECT song_id, guest_device_id, guest_source "
                    "FROM room_queue_items WHERE room_id = :room_id "
                    "AND device_id IS NULL AND device_name = :guest"
                ),
                {"room_id": room_id, "guest": GUEST_ADDED_BY},
            )
            guest_ids = {int(r[0]): (r[1], r[2]) for r in found.all()}

    blocked_reason = _blocked_message(block, room_id) if block else None
    if guest and block is None:
        # A read is never refused for this: a ban that can't be checked
        # just leaves the queue read-only for the guest (edits fail closed).
        try:
            banned = await guest_bans.find_ban(
                device_id or guest_device_id(request), request_source(request)
            ) is not None
        except Exception as e:  # noqa: BLE001 — the DB did not answer
            log.warning("guest ban check failed on a read: %s", e)
            banned = True
        if banned:
            blocked_reason = GUEST_BANNED_REASON

    items: list[QueueItem] = []
    for raw in raw_items:
        song_id = int(raw["song_id"])
        name, dev_id, added_at = provenance.get(song_id, (None, None, None))
        if guest:
            # A guest learns which entries guests added, and nothing about
            # the household's devices: no names, no ids (the now-playing
            # read names a device only to a household credential, too).
            if not (dev_id is None and name == GUEST_ADDED_BY):
                name = None
            dev_id = None
        who_guest = guest_ids.get(song_id, (None, None))
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
                guest_device_id=who_guest[0],
                guest_source=who_guest[1],
                playing=current is not None and song_id == int(current),
            )
        )
    return RoomQueue(
        room_id=room_id,
        items=items,
        current_song_id=int(current) if current is not None else None,
        editable=blocked_reason is None,
        blocked_reason=blocked_reason,
        guest=guest,
        admin=admin,
    )


# ─── Edits ─────────────────────────────────────────────────────────────────


@router.post("/{room_id}/add", dependencies=GUEST_OR_DEVICE)
async def add_to_queue(room_id: str, request: Request, body: QueueAddRequest):
    """Append library tracks to the room's queue and stamp provenance.
    A guest's entries are stamped "a guest" with no device id — the name a
    claimed id is registered under is not the guest's to borrow — and the
    id it sent and its address go in the guest columns, for an admin."""
    device_id, device_name = await _assert_can_edit(body.device_id, room_id)
    guest: tuple[str, str] | None = None
    if _is_guest(request):
        guest = (body.device_id, request_source(request))
        device_id, device_name = None, GUEST_ADDED_BY
    status, payload = await post_admin(
        f"/v1/admin/music/queue/{room_id}/add",
        {"track_ids": body.track_ids},
        headers=await _guest_hop_headers(request, body.device_id),
    )
    if status == 200 and isinstance(payload, dict):
        queued = payload.get("queued") or []
        song_ids = [int(q["song_id"]) for q in queued if q.get("song_id") is not None]
        if song_ids:
            await _stamp_provenance(room_id, song_ids, device_id, device_name, guest=guest)
    return bridge_response(status, payload)


async def _stamp_provenance(
    room_id: str,
    song_ids: list[int],
    device_id: str | None,
    device_name: str | None,
    *,
    guest: tuple[str, str] | None = None,
) -> None:
    """Record who added these songids. ON CONFLICT because MPD reuses a
    songid only after a very long wrap, but a provenance row for a songid
    that has since been removed and re-issued must not collide. ``guest``
    is (the device id it sent, its address) for a guest's entries; only
    then are V023's guest columns written."""
    async with session_scope() as s:
        for song_id in song_ids:
            params = {
                "room_id": room_id,
                "song_id": song_id,
                "device_id": device_id,
                "device_name": device_name,
            }
            if guest is None:
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
                    params,
                )
                continue
            await s.execute(
                text(
                    """
                    INSERT INTO room_queue_items
                        (room_id, song_id, device_id, device_name,
                         guest_device_id, guest_source)
                    VALUES (:room_id, :song_id, :device_id, :device_name,
                            :guest_device_id, :guest_source)
                    ON CONFLICT (room_id, song_id) DO UPDATE
                    SET device_id       = EXCLUDED.device_id,
                        device_name     = EXCLUDED.device_name,
                        guest_device_id = EXCLUDED.guest_device_id,
                        guest_source    = EXCLUDED.guest_source,
                        added_at        = now()
                    """
                ),
                {**params, "guest_device_id": guest[0], "guest_source": guest[1]},
            )


@router.post("/{room_id}/remove", dependencies=GUEST_OR_DEVICE)
async def remove_from_queue(room_id: str, request: Request, body: QueueRemoveRequest):
    """Drop entries. A guest is refused the entry playing now by the core,
    which is the one that knows which entry that is."""
    await _assert_can_edit(body.device_id, room_id)
    status, payload = await post_admin(
        f"/v1/admin/music/queue/{room_id}/remove",
        {"song_ids": body.song_ids},
        headers=await _guest_hop_headers(request, body.device_id),
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


@router.post("/{room_id}/move", dependencies=GUEST_OR_DEVICE)
async def move_in_queue(room_id: str, request: Request, body: QueueMoveRequest):
    """Reorder. Provenance is keyed by songid, so a move needs no DB write —
    that's the reason for keying it that way."""
    await _assert_can_edit(body.device_id, room_id)
    status, payload = await post_admin(
        f"/v1/admin/music/queue/{room_id}/move",
        {"song_id": body.song_id, "to_position": body.to_position},
        headers=await _guest_hop_headers(request, body.device_id),
    )
    return bridge_response(status, payload)


@router.post("/{room_id}/clear", dependencies=DEVICE)
async def clear_queue(room_id: str, request: Request, body: QueueClearRequest):
    """Empty the queue (the core stops the room). Household only: a guest
    is refused here, before the block lookup or the hop, as before."""
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


# ─── Guests seen, and guest bans (admin) ──────────────────────────────────
#
# Owner decision 2026-10-10: a guest may change a playing room's music
# "until they get banned by someone with admin privileges". Guests never
# register (an unpaired browser can't), so the core records each guest
# action it lets through (domovoi/guest_bans.py, table guests_seen) and an
# admin bans from that list — or from the queue row a guest added. A ban
# matches the guest's device id OR its address, on every room, for queue
# edits and transport alike, and never touches a household credential.
#
# Household policy, not a security boundary — the same honesty as the
# queue blocks above: the device id is self-asserted and the address is
# a DHCP lease. The dashboard says so where bans are managed.

guests_router = APIRouter(prefix="/api/music/guests", tags=["music"])
guest_bans_router = APIRouter(prefix="/api/music/guest-bans", tags=["music"])

# How far back "guests seen recently" looks, and how many it lists.
GUESTS_SEEN_DAYS = 7
GUESTS_SEEN_LIMIT = 100


class GuestSeen(BaseModel):
    device_id: str | None = None
    source: str
    user_agent: str | None = None
    room_id: str | None = None
    last_action: str | None = None
    first_seen: Any = None
    last_seen: Any = None
    # The ban that covers this guest now (by id or by address), if any.
    ban_id: int | None = None


class GuestBan(BaseModel):
    id: int
    device_id: str | None = None
    source: str | None = None
    note: str | None = None
    created_at: Any = None


class GuestBanCreate(BaseModel):
    device_id: str | None = Field(default=None, max_length=64)
    source: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=200)


_BAN_COLUMNS = "id, device_id, source, note, created_at"


def _row_to_ban(r: Any) -> GuestBan:
    return GuestBan(
        id=int(r[0]), device_id=r[1], source=r[2], note=r[3], created_at=r[4]
    )


@guests_router.get(
    "", response_model=list[GuestSeen], dependencies=[Depends(require_admin_read)]
)
async def list_guests_seen() -> list[GuestSeen]:
    """Guests that changed a room's music in the last
    :data:`GUESTS_SEEN_DAYS` days, most recent first, each with the ban
    that covers it now (if any)."""
    async with session_scope() as s:
        rows = await s.execute(
            text(
                f"""
                SELECT g.device_id, g.source, g.user_agent, g.room_id,
                       g.last_action, g.first_seen, g.last_seen,
                       (SELECT b.id FROM guest_bans b
                         WHERE (b.device_id IS NOT NULL AND g.device_id <> ''
                                AND b.device_id = g.device_id)
                            OR (b.source IS NOT NULL AND b.source = g.source)
                         ORDER BY b.id LIMIT 1) AS ban_id
                FROM guests_seen g
                WHERE g.last_seen > now() - interval '{GUESTS_SEEN_DAYS} days'
                ORDER BY g.last_seen DESC
                LIMIT {GUESTS_SEEN_LIMIT}
                """
            )
        )
        return [
            GuestSeen(
                device_id=r[0] or None, source=r[1], user_agent=r[2], room_id=r[3],
                last_action=r[4], first_seen=r[5], last_seen=r[6],
                ban_id=int(r[7]) if r[7] is not None else None,
            )
            for r in rows.all()
        ]


@guest_bans_router.get(
    "", response_model=list[GuestBan], dependencies=[Depends(require_admin_read)]
)
async def list_guest_bans() -> list[GuestBan]:
    async with session_scope() as s:
        rows = await s.execute(
            text(f"SELECT {_BAN_COLUMNS} FROM guest_bans ORDER BY id DESC")
        )
        return [_row_to_ban(r) for r in rows.all()]


@guest_bans_router.post(
    "",
    response_model=GuestBan,
    status_code=201,
    dependencies=[Depends(require_admin_mutation)],
)
async def create_guest_ban(payload: GuestBanCreate) -> GuestBan:
    """Ban a guest from changing any room's music: by the device id it
    sends, its address, or both (what the Ban buttons send). Needs one of
    the two; a duplicate is 409."""
    device_id = guest_bans.clean_device_id(payload.device_id)
    source = (payload.source or "").strip() or None
    if not device_id and not source:
        raise HTTPException(
            status_code=400, detail="a guest ban needs a device_id, a source, or both"
        )
    async with session_scope() as s:
        try:
            row = await s.execute(
                text(
                    f"""
                    INSERT INTO guest_bans (device_id, source, note)
                    VALUES (:device_id, :source, :note)
                    RETURNING {_BAN_COLUMNS}
                    """
                ),
                {"device_id": device_id, "source": source, "note": payload.note},
            )
        except IntegrityError as e:
            raise HTTPException(
                status_code=409, detail="that guest is already banned"
            ) from e
        result = row.first()
    if result is None:  # pragma: no cover — RETURNING always yields on insert
        raise HTTPException(status_code=500, detail="insert returned no row")
    return _row_to_ban(result)


@guest_bans_router.delete(
    "/{ban_id}", status_code=204, dependencies=[Depends(require_admin_mutation)]
)
async def delete_guest_ban(ban_id: int) -> None:
    """Unban."""
    async with session_scope() as s:
        result = await s.execute(
            text("DELETE FROM guest_bans WHERE id = :id"), {"id": ban_id}
        )
    if (result.rowcount or 0) == 0:
        raise HTTPException(status_code=404, detail=f"guest ban {ban_id} not found")
