"""Co-editing routes: the WebSocket ``/api/collab/{doc_id}`` and version
history (``/api/collab/history``, ``/api/collab/restore``).

How co-editing works, and how the clean file gets written, is in
``web/backend/collab/__init__.py``. This module is the doors and the rules.

**Who may do what: the Documents rules, exactly** (``documents.py``):

* opening a document, reading its history, previewing a version: the
  DEVICE READ tier (household token, admin Bearer, the dashboard cookie,
  ``?device_token=``), on a path that passes the same containment and
  secret-name checks as ``GET /api/documents/text`` (``_readable_target``);
* editing it, and restoring a version: the DEVICE tier (household token or
  admin Bearer; never the cookie alone, never ``?device_token=``), plus the
  Files write policy for ``core:documents`` (the per-device block, the
  admin-write list, the library's editable flag) and the save routes' name
  check (``_assert_saveable_name``).

On the socket the same split decides the SESSION, not the handshake: a
caller with read access but not write access gets a **read-only session**
(it sees edits and presence; its own edits are dropped). A browser cannot
set headers on a WebSocket, so the household token comes as the
``domovoi.device-token-b64.<base64url>`` subprotocol, as on ``/ws/state``
(an app may send ``X-Device-Token``). A session whose only credential rides
along on its own (the cookie, or the pre-setup grace) must come from a page
of this dashboard: its ``Origin`` is checked against this server, so another
service on the same machine cannot open a document with the dashboard's
cookie.

The doc id is the server's: ``GET /api/collab/doc?path=`` maps a path to it
after the path checks; the socket takes only the id and re-checks the path
the id stands for. A raw client path never reaches a socket.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.transport_guard import host_allowed
from domovoi.admin_auth import require_device, require_device_read
from web.backend.api import documents as documents_api
from web.backend.collab import rooms, store, workers
from web.backend.db import session_scope
from web.backend.middleware import cors_origin_regex

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/collab", tags=["collab"])

DAILY = [Depends(require_device_read)]
DEVICE = [Depends(require_device)]

# What co-editing opens: the formatted editor's documents (builder A). Each
# still saves through its own Documents route. Spreadsheets are not co-edited
# in v1 (builder D); .xlsx comes back here, with its own Yjs root registered
# in collab.content, when they are.
COLLAB_EXTS = frozenset({".html", ".md", ".markdown", ".docx"})

COLLAB_HEADER = "x-domovoi-collab"

# Presence colours: distinct from each other, readable as a dot or a caret
# on the light and the dark theme.
_COLORS = (
    "#d9480f", "#1971c2", "#2f9e44", "#9c36b5", "#0c8599", "#c2255c",
    "#5f3dc4", "#66a80f", "#e67700", "#1864ab", "#a61e4d", "#087f5b",
)


def color_for(name: str) -> str:
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return _COLORS[digest[0] % len(_COLORS)]


# ─── Paths ─────────────────────────────────────────────────────────────


def _collab_target(rel_path: str) -> Path:
    """The Documents read checks, plus "is this something co-editing
    opens". 400/404 as documents.py answers them; 415 for other types."""
    target = documents_api._readable_target(rel_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"{rel_path} not found")
    if target.suffix.lower() not in COLLAB_EXTS:
        raise HTTPException(
            status_code=415, detail=f"{target.suffix or target.name} isn't co-edited"
        )
    return target


async def _may_write_path(conn: Any, target: Path) -> bool:
    """The save routes' extra checks for a caller that already holds the
    device tier: the name check and the Files write policy."""
    try:
        documents_api._assert_saveable_name(target)
        await documents_api._assert_files_policy(conn, None)
    except HTTPException:
        return False
    return True


# ─── Who is this ───────────────────────────────────────────────────────


async def resolve_user(conn: Any) -> dict[str, str]:
    """``{name, color}`` for presence and history: the household person who
    owns this device, else the device's name, else "Someone". Read from the
    devices table by the id the caller identifies itself with (the same
    places the per-device block reads: X-Device-Id, ``?device_id=``, the
    registration cookie). Self-asserted within the household, like every
    device id; never a credential."""
    from web.backend.api import files as files_api

    name = None
    device_id = files_api.caller_device_id(conn, None)
    if device_id:
        try:
            async with session_scope() as s:
                row = (
                    await s.execute(
                        text(
                            "SELECT d.name AS device_name, p.name AS person_name "
                            "FROM devices d LEFT JOIN people p ON p.id = d.owner_person_id "
                            "WHERE d.device_id = :id"
                        ),
                        {"id": device_id},
                    )
                ).first()
            if row is not None:
                name = (row.person_name or row.device_name or "").strip() or None
        except Exception as e:  # noqa: BLE001 — a name is a nicety
            log.debug("collab: no name for device: %s", e)
    name = (name or "Someone")[:60]
    return {"name": name, "color": color_for(name)}


# ─── Socket credentials ────────────────────────────────────────────────

Access = Literal["write", "read"]


class Refusal(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _offered_token(ws: WebSocket) -> tuple[Optional[str], Optional[str]]:
    from web.backend.main import _offered_token_subprotocol  # one parser for both sockets

    return _offered_token_subprotocol(ws)


def _web_port(ws: WebSocket) -> int:
    host = ws.headers.get("host") or ""
    m = re.search(r":(\d+)$", host)
    if m:
        return int(m.group(1))
    return int(os.environ.get("WEB_PORT", "6369"))


def origin_ok(ws: WebSocket) -> bool:
    """For a session with only an ambient credential: the page must be a
    Domovoi dashboard on this server's port and on a LAN name (this one, or
    another one reached through the server switcher), never another service
    on the machine. Origin == Host is NOT enough on its own: under DNS
    rebinding a public name is "same origin" (review L4); the name itself
    must be one this box answers to on the LAN."""
    origin = (ws.headers.get("origin") or "").strip()
    if not origin:
        return True     # not a browser: nothing ambient rode along
    if origin.lower() == "null":
        return False
    port = _web_port(ws)
    if re.match(cors_origin_regex(port), origin, flags=re.IGNORECASE):
        return True
    parts = urlsplit(origin)
    try:
        origin_port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return False
    return parts.hostname is not None and host_allowed(parts.hostname) and origin_port == port


async def socket_access(ws: WebSocket, token: Optional[str]) -> Access:
    """Which session this socket gets, before any path is looked at.
    Raises :class:`Refusal`."""
    throttled = Refusal(rooms.CLOSE_THROTTLED, "too many wrong household tokens; wait and try again")
    try:
        # X-Device-Token header, admin Bearer, cookie, pre-setup grace.
        result = await admin_auth.check_device_request(ws)
        if result in ("ok", "admin"):
            return "write"
        if result == "throttled":
            raise throttled
        # The browser's spelling of the household token.
        if token:
            r2 = await admin_auth.check_device_credential(ws, token)
            if r2 in ("ok", "admin"):
                return "write"
            if r2 == "throttled":
                raise throttled
        # Documents' reads take ?device_token=; its writes never do.
        if result != "pre-setup":
            query = (ws.query_params.get(admin_auth.DEVICE_TOKEN_QUERY) or "").strip()
            if query:
                r3 = await admin_auth.check_device_credential(ws, query)
                if r3 in ("ok", "admin"):
                    return "read"
                if r3 == "throttled":
                    raise throttled
    except admin_auth.AuthCheckUnavailable:
        raise Refusal(rooms.CLOSE_BUSY, "the server could not check credentials just now") from None
    if result in ("pre-setup", "cookie-only"):
        # Nothing but what a browser attaches on its own: only from a page
        # of this dashboard.
        if not origin_ok(ws):
            raise Refusal(rooms.CLOSE_FORBIDDEN, "not from this dashboard")
        return "write" if result == "pre-setup" else "read"
    raise Refusal(rooms.CLOSE_UNAUTHORIZED, "household device token or admin session required")


def _device_key(conn: Any) -> Optional[str]:
    from web.backend.api import files as files_api

    return files_api.caller_device_id(conn, None)


def _source(ws: WebSocket) -> str:
    return admin_auth.request_source(ws)


@router.websocket("/{doc_id}")
async def collab_socket(ws: WebSocket, doc_id: str) -> None:
    """The y-websocket endpoint. ``?epoch=`` is the epoch of the client's
    local copy (IndexedDB); see web/backend/collab/__init__.py."""
    token, offered = _offered_token(ws)
    # Accepted first and closed with a 44xx code when refused: the stock
    # client reconnects forever after a refused handshake, and stops on 44xx.
    await ws.accept(subprotocol=offered)
    source = _source(ws)
    # Counted before the first await, so a burst of sockets from one device
    # cannot all pass the check together.
    if rooms.MANAGER.sockets_by_source.get(source, 0) >= rooms.MAX_SOCKETS_PER_SOURCE:
        await _refuse(ws, rooms.CLOSE_BUSY, "too many documents open from this device")
        return
    rooms.MANAGER.sockets_by_source[source] = rooms.MANAGER.sockets_by_source.get(source, 0) + 1
    try:
        await _serve_socket(ws, doc_id, token, source)
    finally:
        left = rooms.MANAGER.sockets_by_source.get(source, 1) - 1
        if left > 0:
            rooms.MANAGER.sockets_by_source[source] = left
        else:
            rooms.MANAGER.sockets_by_source.pop(source, None)


async def _refuse(ws: WebSocket, code: int, reason: str) -> None:
    try:
        await ws.close(code=code, reason=reason)
    except Exception:  # noqa: BLE001
        pass


async def _serve_socket(ws: WebSocket, doc_id: str, token: Optional[str], source: str) -> None:
    try:
        # The LAN host middleware passes WebSocket upgrades through, so a
        # rebound public name is refused here (review L4).
        if not host_allowed(ws.headers.get("host")):
            raise Refusal(rooms.CLOSE_FORBIDDEN, "not a name this server answers to")
        if rooms.MANAGER.crash_blocked(source):
            # This device's changes broke document workers several times
            # lately: it waits (CRASH_WINDOW_SEC) before co-editing again.
            raise Refusal(rooms.CLOSE_THROTTLED, "too many failed changes from this device; try later")
        access = await socket_access(ws, token)
        row = await store.get(doc_id) if len(doc_id) <= 64 else None
        if row is None:
            raise Refusal(rooms.CLOSE_NOT_FOUND, "no such document")
        try:
            target = _collab_target(row.rel_path)
        except HTTPException as e:
            raise Refusal(
                rooms.CLOSE_UNSUPPORTED if e.status_code == 415 else rooms.CLOSE_NOT_FOUND,
                "that document is not available",
            ) from None
        if access == "write" and not await _may_write_path(ws, target):
            access = "read"
        readonly = access != "write"
        # Everything that awaits comes BEFORE the room is opened: open()
        # hands back a room that is open now, and the join below happens
        # before anything else can run (review M3).
        user = await resolve_user(ws)
        device = _device_key(ws) or user["name"]
        try:
            room = await rooms.MANAGER.open(row, target, readonly=readonly)
        except rooms.RoomsFull:
            raise Refusal(rooms.CLOSE_BUSY, "too many documents are open right now") from None
        except rooms.RoomUnsafe:
            raise Refusal(rooms.CLOSE_UNAVAILABLE, "co-editing is not available for this document") from None
        except (workers.WorkerFailed, workers.WorkerRefused):
            raise Refusal(rooms.CLOSE_RETRY, "the server could not open the document just now") from None
        if not room.has_room_for(readonly):
            raise Refusal(rooms.CLOSE_BUSY, "too many people have this document open")
    except Refusal as r:
        await _refuse(ws, r.code, r.reason)
        return

    try:
        client_epoch = int(ws.query_params.get("epoch") or room.epoch)
    except ValueError:
        client_epoch = room.epoch

    async def send_bytes(data: bytes) -> None:
        await ws.send_bytes(data)

    async def close(code: int, reason: str) -> None:
        await ws.close(code=code, reason=reason)

    peer = rooms.Peer(
        send_bytes=send_bytes, close=close, name=user["name"], color=user["color"],
        readonly=readonly, source=source, epoch=client_epoch,
        archive=client_epoch != room.epoch, session=uuid.uuid4().hex[:12], device=device,
    )
    peer.writer = asyncio.get_running_loop().create_task(peer.run_writer())
    room.join(peer)
    try:
        while not peer.closed:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data is None:
                continue   # y-websocket sends binary frames only
            await room.handle(peer, data)
    except Exception as e:  # noqa: BLE001 — the socket dropped
        log.debug("collab %s: socket ended: %s", doc_id, e)
    finally:
        room.leave(peer)
        # Shielded: this handler may be cancelled as the connection goes,
        # and the room must still be released (last edits to Postgres).
        await asyncio.shield(_finish(peer, room))


async def _finish(peer: rooms.Peer, room: rooms.Room) -> None:
    if peer.writer is not None:
        try:
            await asyncio.wait_for(asyncio.shield(peer.writer), timeout=2.0)
        except Exception:  # noqa: BLE001
            peer.writer.cancel()
    try:
        await rooms.MANAGER.release(room)
    except Exception as e:  # noqa: BLE001
        log.warning("collab %s: release failed: %s", room.doc_id, e)


# ─── REST ──────────────────────────────────────────────────────────────


async def _rest_may_write(request: Request, target: Path) -> bool:
    try:
        result = await admin_auth.check_device_request(request)
    except admin_auth.AuthCheckUnavailable:
        return False
    if result not in ("ok", "admin", "pre-setup"):
        return False
    return await _may_write_path(request, target)


@router.get("/doc", dependencies=DAILY)
async def open_doc(request: Request, path: str = Query(..., max_length=1024)) -> dict[str, Any]:
    """Map a Documents path to its co-editing id (made on first use) and say
    what this caller may do with it. ``ws`` is the socket path for the
    y-websocket provider (server URL ``/api/collab``, room = ``doc_id``)."""
    target = _collab_target(path)
    rel = documents_api._rel_of(target)
    row = await store.get_or_create(rel)
    if row.unavailable:
        row = await _maybe_available_again(row, target)
    room = rooms.MANAGER.rooms.get(row.doc_id)
    if row.unavailable:
        # Its stored state broke the document workers: the editor opens the
        # file on its own, read and saved the ordinary way.
        return {"doc_id": row.doc_id, "path": rel, "unavailable": True,
                "readonly": not await _rest_may_write(request, target),
                "user": await resolve_user(request)}
    return {
        "doc_id": row.doc_id,
        "path": rel,
        "epoch": room.epoch if room is not None else row.epoch,
        "readonly": not await _rest_may_write(request, target),
        "user": await resolve_user(request),
        "ws": f"/api/collab/{row.doc_id}",
        "editing_now": len(room.peers) if room is not None else 0,
    }


async def _maybe_available_again(row: store.DocRow, target: Path) -> store.DocRow:
    """Co-editing comes back once the file has changed since the state that
    broke the workers was built (someone saved it single-editor): the shared
    state is rebuilt from the file, under a new epoch."""
    async with rooms.path_lock(row.rel_path):
        try:
            sha = rooms.file_fingerprint(target)[0]
        except OSError:
            return row
    if row.file_sha256 is not None and sha == row.file_sha256:
        return row
    await store.rebuild(row.doc_id)
    return await store.get(row.doc_id) or row


_KIND_LABELS = {
    "from_file": "Opened from the file",
    "auto": "Edited",
    "leave": "Edited",
    "before_rebuild": "Before the file was changed outside the editor",
    "offline_copy": "Offline changes that could not be merged",
    "before_restore": "Before a restore",
    "restored": "Restored an earlier version",
}


def _version_json(sn: store.SnapshotRow) -> dict[str, Any]:
    return {
        "id": sn.id,
        "at": sn.created_at.isoformat(),
        "kind": sn.kind,
        "label": _KIND_LABELS.get(sn.kind, "Edited"),
        "editors": sn.editors,
        "size": sn.size,
    }


@router.get("/history", dependencies=DAILY)
async def history(path: str = Query(..., max_length=1024)) -> dict[str, Any]:
    """The versions of a document, newest first: when, and who edited."""
    target = documents_api._readable_target(path)
    rel = documents_api._rel_of(target)
    row = await store.get_by_path(rel)
    if row is None:
        return {"path": rel, "doc_id": None, "versions": []}
    snaps = await store.list_snapshots(row.doc_id)
    return {"path": rel, "doc_id": row.doc_id, "versions": [_version_json(s) for s in snaps]}


@router.get("/history/{version_id}", dependencies=DAILY)
async def version(version_id: int, path: str = Query(..., max_length=1024)) -> dict[str, Any]:
    """One version, for the preview: its Yjs state (base64, for an editor to
    render read-only) and its plain text. The path must be the document the
    version belongs to."""
    target = documents_api._readable_target(path)
    row = await store.get_by_path(documents_api._rel_of(target))
    sn = await store.get_snapshot(row.doc_id, version_id) if row is not None else None
    if sn is None or sn.state is None:
        raise HTTPException(status_code=404, detail="no such version")
    try:
        result, _ = await workers.scratch_call("preview", [sn.state])
        text_ = str(result.get("text") or "")
    except (workers.WorkerFailed, workers.WorkerRefused):
        raise HTTPException(status_code=409, detail="this version cannot be shown") from None
    return {
        **_version_json(sn),
        "update": base64.b64encode(sn.state).decode("ascii"),
        "text": text_,
    }


class RestoreRequest(BaseModel):
    path: str = Field(..., max_length=1024)
    version_id: int
    device_id: Optional[str] = Field(None, min_length=3, max_length=64)


@router.post("/restore", dependencies=DEVICE)
async def restore(request: Request, req: RestoreRequest) -> dict[str, Any]:
    """Restore a version as a NEW change (nothing in history is lost: the
    version just before it is kept too). Device tier and the Files write
    policy, as for a save. Everyone with the document open sees it arrive;
    the editor that made the restore is then asked to save the file."""
    await documents_api._assert_files_policy(request, req.device_id)
    target = _collab_target(req.path)
    documents_api._assert_saveable_name(target)
    rel = documents_api._rel_of(target)
    row = await store.get_by_path(rel)
    sn = await store.get_snapshot(row.doc_id, req.version_id) if row is not None else None
    if row is None or sn is None or sn.state is None:
        raise HTTPException(status_code=404, detail="no such version")
    user = await resolve_user(request)
    try:
        room = await rooms.MANAGER.open(row, target)
    except rooms.RoomsFull:
        raise HTTPException(status_code=503, detail="too many documents are open right now") from None
    except rooms.RoomUnsafe:
        raise HTTPException(status_code=409, detail="co-editing is not available for this document") from None
    room.holds += 1         # before any await: the room stays open for us
    try:
        sid = await room.restore(sn.state, user["name"])
    except rooms.StillOpening:
        raise HTTPException(status_code=409, detail="the document is still opening; try again") from None
    except rooms.RoomGone:
        raise HTTPException(status_code=409, detail="the document changed just now; try again") from None
    except rooms.RestoreFailed:
        raise HTTPException(status_code=409, detail="that version could not be restored") from None
    finally:
        room.holds -= 1
        await rooms.MANAGER.release(room)
    return {"restored": req.version_id, "version_id": sid, "path": rel,
            "file_saved": not room.closed and bool(room.writable_peers)}


# ─── Hooks the Documents and Files doors call ───────────────────────────

_HOOK_TIMEOUT = 5.0


def _parse_header(value: str) -> Optional[tuple[str, int, int]]:
    parts = value.split(";")
    if len(parts) != 3:
        return None
    try:
        return parts[0].strip(), int(parts[1]), int(parts[2])
    except ValueError:
        return None


class SaveRefused(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=409, detail=detail)


async def _check_save(conn: Any, target: Path, rel: str) -> None:
    """Refuse (409) a co-editing save that must not be written: a revision
    older than what the file already holds (two saves in flight, the older
    one landing last), one that does not exist yet, one from before the
    document was rebuilt, or one for a document that has moved since.
    Called under the path lock, before the file is touched. A save without
    the header is not co-editing's to refuse."""
    raw = conn.headers.get(COLLAB_HEADER)
    if raw is None:
        return
    parsed = _parse_header(raw)
    if parsed is None:
        raise SaveRefused("unreadable co-editing header")
    doc_id, epoch, rev = parsed
    row = await asyncio.wait_for(store.get(doc_id), _HOOK_TIMEOUT)
    if row is None:
        raise SaveRefused("this document is no longer being edited together; reopen it")
    if row.rel_path != rel:
        raise SaveRefused("this document was moved or renamed; reopen it")
    if row.epoch != epoch:
        raise SaveRefused("this document changed outside the editor; reopen it")
    room = rooms.MANAGER.rooms.get(doc_id)
    if room is not None and not room.closed:
        why = room.save_refusal(rev)
    elif rev > row.rev:
        why = "that revision does not exist"
    elif rev < row.saved_rev:
        why = "a newer version was saved already"
    else:
        why = None
    if why:
        raise SaveRefused(why)


@asynccontextmanager
async def saving(conn: Any, target: Path) -> AsyncIterator[None]:
    """Around a Documents save of a co-edited type: refuse a stale
    co-editing save before anything is written, and hold the path's lock
    across the write and the note of what was written, so the
    outside-change check never sees our own save half done.

        async with collab.saving(request, target):
            target.write_text(...)
    """
    if target.suffix.lower() not in COLLAB_EXTS:
        yield
        return
    rel = documents_api._rel_of(target)
    async with rooms.path_lock(rel):
        await _check_save(conn, target, rel)
        yield
        await _note_saved(conn, target, rel)


async def note_file_saved(conn: Any, target: Path) -> None:
    """For a save route that does not wrap its write in :func:`saving`
    (builder A's ``PUT /doc`` calls this after writing): the same note,
    taken under the path lock. Never raises."""
    try:
        if target.suffix.lower() not in COLLAB_EXTS:
            return
        rel = documents_api._rel_of(target)
        async with rooms.path_lock(rel):
            await _note_saved(conn, target, rel)
    except Exception as e:  # noqa: BLE001
        log.warning("collab: could not note a save of %s: %s", target.name, e)


async def _note_saved(conn: Any, target: Path, rel: str) -> None:
    """A Documents save route wrote ``target``. With the co-editing header
    (``<doc_id>;<epoch>;<rev>``) it is the clean file of that revision:
    remember its fingerprint and that the file holds ``rev``. Without it,
    the file changed under the shared state: the live room (if any) checks
    at once and rebuilds from it. Never raises: a save never fails over
    co-editing bookkeeping once it is written."""
    try:
        room = rooms.MANAGER.by_path(rel)
        parsed = _parse_header(conn.headers.get(COLLAB_HEADER) or "")
        if parsed is None:
            if room is not None:
                room.rebuild_wanted = True
            return
        doc_id, epoch, rev = parsed
        fp = rooms.file_fingerprint(target)
        if room is not None and room.doc_id == doc_id and room.epoch == epoch and rev <= room.rev:
            room.note_saved(rev, fp)
        elif room is not None:
            # Not a revision this room has (a route that skipped the check):
            # whatever was written, it is not ours to vouch for.
            room.rebuild_wanted = True
            return
        row = await asyncio.wait_for(store.get_by_path(rel), _HOOK_TIMEOUT)
        if row is None or row.doc_id != doc_id or row.epoch != epoch or rev > row.rev:
            return
        await asyncio.wait_for(store.record_save(doc_id, epoch, fp, rev), _HOOK_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        log.warning("collab: could not note a save of %s: %s", target.name, e)


async def forget_paths(rel_paths: list[str]) -> None:
    """Documents were deleted: their shared state and version history go too
    (folders: everything under them)."""
    if not rel_paths:
        return
    try:
        gone = await asyncio.wait_for(store.forget(rel_paths), _HOOK_TIMEOUT)
        await rooms.MANAGER.forget(gone)
    except Exception as e:  # noqa: BLE001
        log.warning("collab: could not forget deleted documents: %s", e)


async def note_moved(old_rel: str, new_rel: str) -> None:
    """A document (or a folder of them) moved inside Documents: its id and
    history follow it, and so does a room open on it."""
    try:
        await asyncio.wait_for(store.move(old_rel, new_rel), _HOOK_TIMEOUT)
        prefix = old_rel.rstrip("/") + "/"
        for room in list(rooms.MANAGER.rooms.values()):
            if room.rel_path == old_rel:
                moved = new_rel
            elif room.rel_path.startswith(prefix):
                moved = new_rel.rstrip("/") + "/" + room.rel_path[len(prefix):]
            else:
                continue
            room.rel_path = moved
            room.target = documents_api._safe_target(moved)
            # Editors still hold the old name and would save to it (and so
            # make the old file again): tell them, and close, so they reopen
            # under the new one.
            for p in list(room.peers):
                p.send(rooms.control_message({"t": "moved", "path": moved}))
                p.kill_after(rooms.CLOSE_MOVED, "the document was moved or renamed")
    except Exception as e:  # noqa: BLE001
        log.warning("collab: could not follow a move: %s", e)
