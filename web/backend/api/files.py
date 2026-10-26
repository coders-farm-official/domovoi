"""Generic multi-library Files API (design §2) — the web dashboard's
``/api/files`` surface.

One router browses/downloads/uploads/deletes/imports across every root the
Files tab exposes (core media dirs, enabled-plugin media libraries, present
removable drives), all resolved server-side by :mod:`files_security`. The
client only ever sends a ``library_id`` + a **relative** path; the absolute
``root_path`` is never serialized.

**Trust posture (device tier, with three exceptions).** Browsing,
downloading a file, uploading, moving and importing belong to the
HOUSEHOLD: a paired phone shouldn't need the admin password to drop a
file into the music folder. They take ``require_device`` — a valid
``X-Device-Token`` or an admin Bearer — and the byte serves take its read
half (``require_device_read``), which also accepts the dashboard cookie
and a ``?device_token=`` query because an ``<img src>`` cannot set a
header. The pre-setup grace is kept throughout, so a fresh install still
works before an admin password exists.

Three things sit a tier above that (resolved 2026-09-22, revised
2026-09-24):

* ``/delete`` — the one verb that destroys something;
* a ``/download`` whose path is a **directory** — the server builds the
  zip in memory, and handing a whole tree to anything on the LAN is not
  an ordinary daily action;
* any write whose target library is a **removable drive** — writing onto
  a stick somebody plugged into the server is a different risk from
  saving into the household's own libraries.

Each of those takes the admin tier instead.

**``core:documents`` is NOT one of them.** Saving a document, a
spreadsheet, a drawing or an image is a household action (2026-09-24),
so a write into the Documents library is device tier here exactly as it
is on ``/api/documents``. What still answers to the operator is deleting
one — and deletes go through ``/delete`` above, wherever the target
lives.

**Two doors, one room.** ``/api/files`` and ``/api/documents`` write into
the SAME folder. While the documents door was admin-only that did not
matter; now that both are device tier, any rule only one of them keeps is
a rule neither of them keeps, because a caller picks the door. So the
rules live here and the documents router calls them:
:func:`assert_documents_write_allowed` is the per-device block, the
admin-write library list and the ``editable`` flag as one entry point,
and the secret-shaped-name filter both doors apply is the shared
:func:`~web.backend.api.files_security.is_sensitive_name`. Adding a rule
to one door means adding it to that shared code, not to one handler —
the drift is what made the gap.

The device model the room queue uses (:mod:`web.backend.api.music_queue`)
still rides on top: every write names the calling ``device_id`` (required
— a blocklist anyone evades by omitting the field is no blocklist), and an
admin can take file writes away from a named device with
``files_device_blocks`` (V012). What changed with the token is what that
id means: only a caller who already holds the household credential gets
as far as the block check, so an unpaired device cannot write whatever it
calls itself. WITHIN the household the id is still self-asserted, so the
block remains household policy rather than a security boundary — same as
the queue's. Reads are never blocked; the browse response carries
``writable`` / ``blocked_reason`` for the calling device so a client can
disable its own controls and say why.

This module is **additive** — it does NOT touch ``/api/documents`` (design's
load-bearing decision): the homegrown editors keep their own surface, and the
Files page's "Edit" affordance for ``core:documents`` opens them through the
existing document endpoints. ``doc_editing`` gates that affordance
per-library.

The only web→core hop is the post-write reindex trigger for indexed libraries
(music), proxied like ``music.py`` via ``post_admin`` with the caller's
credentials forwarded; audiobooks reindex runs the in-process indexer;
podcasts/documents/removable reindex is a no-op.
"""

from __future__ import annotations

import io
import logging
import os
import re
import secrets
import shutil
import zipfile
from pathlib import Path
from typing import Any, Literal, Optional

import anyio

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from domovoi.admin_auth import (
    require_admin_mutation,
    require_admin_read,
    require_device,
    require_device_read,
)
from web.backend.api.audio_serve import (
    AUDIO_EXTENSIONS,
    VIDEO_EXTENSIONS,
    attachment_headers,
    safe_download_name,
    serve_audio_range,
)
from web.backend.api.documents import (
    _DRAWING_EXTS,
    _IMAGE_EXTS,
    _OFFICE_WP_EXTS,
    _SHEET_EXTS,
    _TEXT_EXTS,
)
from web.backend.api.devices import DEVICE_ID_COOKIE, valid_device_id
from web.backend.api.files_security import (
    DOCUMENTS_LIBRARY_ID,
    INDEXED_KINDS,
    MediaLibrary,
    build_libraries,
    core_library,
    has_sensitive_segment,
    is_sensitive_name,
    private_path_check,
    safe_join,
    unstorable_reason,
)
from web.backend.api import files_security as _fs
from web.backend.api.csrf_guard import require_requested_with
from web.backend.api import listing_cache, paging
from web.backend.api.music import _safe_basename, _unique_path
from web.backend.db import session_scope
from web.backend.domovoi_client import auth_forward_headers, post_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])

# Bounded fan-out caps (mirror music.py:_MAX_ZIP_MEMBERS). Guard a recursive
# delete / a dir zip / an import copytree against unbounded work.
_MAX_TREE_MEMBERS = 5000
_MAX_IMPORT_BYTES = 20 * 1024 * 1024 * 1024  # 20 GiB total per import

# Named libraries a household device may READ but not write into. EMPTY,
# and deliberately so: it held ``core:documents`` until 2026-09-24, when
# Kamron settled that saving documents, spreadsheets and images is a
# household action and only deleting needs admin. Putting an id back here
# reverses that decision — the hook stays for a library that genuinely
# needs it, not as a place to restore Documents to.
ADMIN_WRITE_LIBRARY_IDS: frozenset[str] = frozenset()


def _needs_admin_write(lib: MediaLibrary) -> bool:
    """Removable drives only: a stick somebody plugged into the server is
    not one of the household's own libraries, and nobody asked for the
    household to be able to write onto it."""
    return lib.id in ADMIN_WRITE_LIBRARY_IDS or lib.kind == "removable"


async def _assert_admin_for(lib: MediaLibrary, request: Request) -> None:
    """Raise 401/403 when this library's writes are admin-only and the
    caller isn't one. Called with the RESOLVED target library, so the
    answer never depends on a client-supplied name for it."""
    if _needs_admin_write(lib):
        await require_admin_mutation(request)


# ─── Registry resolution ─────────────────────────────────────────────────────
async def _registry() -> dict[str, MediaLibrary]:
    """Fresh {library_id → MediaLibrary} each call (core + plugin + removable)."""
    return {lib.id: lib for lib in await build_libraries()}


async def _resolve_library(library_id: str) -> MediaLibrary:
    """Resolve a client-supplied ``library_id`` against the fresh registry.
    Unknown id → 404; an absent removable → 410 (ejected mid-session)."""
    reg = await _registry()
    lib = reg.get(library_id)
    if lib is not None:
        return lib
    if library_id.startswith("removable:"):
        raise HTTPException(status_code=410, detail="drive no longer present")
    raise HTTPException(status_code=404, detail=f"unknown library {library_id!r}")


# ─── Entry-kind classification (mirrors documents._doc_category buckets) ──────
def _kind_for(name: str, is_dir: bool) -> str:
    """kind ∈ folder | audio | video | doc-office | doc-text | image | pdf | other,
    from the bare name (no Path built per entry — listings call it 10,000 times)."""
    if is_dir:
        return "folder"
    ext = os.path.splitext(name)[1].lower()
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext == ".pdf":
        return "pdf"
    if ext in _OFFICE_WP_EXTS or ext in _SHEET_EXTS:
        return "doc-office"
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _TEXT_EXTS or ext in _DRAWING_EXTS:
        return "doc-text"
    return "other"


# ─── Reindex trigger (the only web→core hop) ─────────────────────────────────
async def _trigger_reindex(
    reindex_kind: Optional[str], request: Request, *, removed: tuple = ()
) -> bool:
    """Fire the post-write reindex for an indexed library. music proxies the
    core admin endpoint (credentials forwarded); audiobooks runs the in-process
    indexer, then asks the core for an MPD rescan when it added a book;
    everything else is a no-op. Best-effort — a failure never fails the
    write (the file is already saved; the indexer's next sweep recovers it)."""
    if reindex_kind == "music":
        status, _ = await post_admin(
            "/v1/admin/library/reindex", headers=auth_forward_headers(request)
        )
        if status != 200:
            log.warning("files: music reindex trigger failed (status=%s)", status)
        return status == 200
    if reindex_kind == "audiobooks":
        try:
            from web.backend.api.audiobooks import index_and_tell_players

            # ``removed``: a delete's paths, pruned from the book list even
            # when the folder is left empty (the indexer keeps the list of an
            # empty folder otherwise, as an unmounted share looks the same).
            await index_and_tell_players(request, removed=removed)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("files: audiobook reindex failed: %s", e)
            return False
    # podcasts (feed-driven, no dir indexer) / documents (live) / None → no-op.
    return False


# ─── Schemas ─────────────────────────────────────────────────────────────────
class BrowseEntry(BaseModel):
    name: str
    rel: str
    is_dir: bool
    size: Optional[int]
    mtime: Optional[float]
    kind: str
    locked_by: Optional[str] = None  # only meaningful for core:documents


class BrowseResponse(BaseModel):
    library_id: str
    path: str
    editable: bool
    importable: bool
    doc_editing: bool
    breadcrumb: list[str]
    entries: list[BrowseEntry]
    # Whether the CALLING device may write here (upload/move/import). Only
    # meaningful when the read named a ``device_id``; the client disables its
    # own controls from this rather than discovering the 403 on first tap.
    # ``editable`` is the LIBRARY's property; ``writable`` is the device's.
    writable: bool = True
    blocked_reason: Optional[str] = None
    # Paging (web/backend/api/paging.py). ``total`` counts the entries that
    # match q / kind; ``folder_total`` counts the whole folder. An unpaged
    # request (an old client) gets every entry with has_more=false.
    total: int = 0
    folder_total: int = 0
    limit: Optional[int] = None
    offset: Optional[int] = None
    has_more: bool = False
    next_cursor: Optional[str] = None


class UploadResponse(BaseModel):
    saved: list[str]
    skipped: list[str]
    reindex_triggered: bool
    # ``create_dir=true`` made the destination folder (False when it was
    # already there, or wasn't asked for).
    created_dir: bool = False


class DeleteRequest(BaseModel):
    library_id: str
    paths: list[str] = Field(..., min_length=1, max_length=1000)
    recursive: bool = False


class DeleteResponse(BaseModel):
    deleted: list[str]
    failed: list[str]
    reindex_triggered: bool


# `device_id` is REQUIRED on every open write (upload / import / move), optional
# only on the browse read. Not because it proves anything — it's self-asserted
# — but because a blocklist anyone evades by leaving the field out is a great
# deal easier to slip than one that needs someone else's id. Every client
# sends it already (the same id the room queue uses); a caller that won't
# name itself doesn't get to write.
_DEVICE_ID_FIELD = Field(..., min_length=3, max_length=64)


class ImportRequest(BaseModel):
    source_library_id: str
    source_path: str
    target_library_id: str
    target_path: str = ""
    device_id: str = _DEVICE_ID_FIELD


class ImportResponse(BaseModel):
    copied: list[str]
    skipped: list[str]
    reindex_triggered: bool


class MoveRequest(BaseModel):
    source_library_id: str
    paths: list[str] = Field(..., min_length=1, max_length=1000)
    # Destination LIBRARY + DIRECTORY. Same library is the common case (drag a
    # file into a subfolder); a different one is allowed when both are
    # editable, which is what makes "drag from Downloads into Music" work.
    target_library_id: str
    target_path: str = ""
    device_id: str = _DEVICE_ID_FIELD


class MoveResponse(BaseModel):
    moved: list[str]
    # Harmless no-ops, kept apart from `failed` so dropping something into the
    # folder it's already in doesn't read as an error.
    skipped: list[str]
    failed: list[str]
    reindex_triggered: bool


class FilesBlock(BaseModel):
    id: int
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    note: Optional[str] = None
    created_at: Any = None


class FilesBlockCreate(BaseModel):
    device_id: Optional[str] = Field(default=None, max_length=64)
    device_name: Optional[str] = Field(default=None, max_length=60)
    note: Optional[str] = Field(default=None, max_length=200)


# ─── Block enforcement ───────────────────────────────────────────────────────
#
# Mirrors music_queue.py's blocklist, minus the per-room scope: a files block
# is all-or-nothing for the device. Matches on device id OR name — the id
# survives a rename (the obvious way to slip a block), the name survives a
# reinstall (new id, same household label). Creating a block from the roster
# fills both, so it keeps working through either.


async def _device_name_for(s: Any, device_id: Optional[str]) -> Optional[str]:
    if not device_id:
        return None
    row = await s.execute(
        text("SELECT name FROM devices WHERE device_id = :id"), {"id": device_id}
    )
    found = row.first()
    return found[0] if found else None


async def _block_for(
    s: Any, device_id: Optional[str], device_name: Optional[str]
) -> Optional[dict[str, Any]]:
    """The block that applies to this device, or None."""
    if not device_id and not device_name:
        return None
    row = await s.execute(
        text(
            """
            SELECT id, device_id, device_name, note
            FROM files_device_blocks
            WHERE (device_id   IS NOT NULL AND device_id   = :device_id)
               OR (device_name IS NOT NULL AND device_name = :device_name)
            ORDER BY id
            LIMIT 1
            """
        ),
        {"device_id": device_id, "device_name": device_name},
    )
    found = row.first()
    if found is None:
        return None
    return {
        "id": int(found[0]), "device_id": found[1],
        "device_name": found[2], "note": found[3],
    }


def _blocked_message(block: dict[str, Any]) -> str:
    who = block.get("device_name") or block.get("device_id") or "this device"
    note = block.get("note")
    base = f"{who} isn't allowed to change files"
    return f"{base} ({note})" if note else base


async def _block_status(device_id: Optional[str]) -> Optional[str]:
    """``blocked_reason`` for a device, or None when it may write (or when no
    device was named — reads never need one)."""
    if not device_id:
        return None
    async with session_scope() as s:
        device_name = await _device_name_for(s, device_id)
        block = await _block_for(s, device_id, device_name)
    return _blocked_message(block) if block is not None else None


async def _assert_can_write(device_id: str) -> None:
    """Raise 403 when this device is blocked from writing to any library."""
    reason = await _block_status(device_id)
    if reason is not None:
        raise HTTPException(status_code=403, detail=reason)


def caller_device_id(request: Request, body_device_id: Optional[str] = None) -> Optional[str]:
    """Which device is making this request, from everything the caller
    actually sent — not only from the body.

    The per-device block used to be one JSON field away from meaningless on
    the documents door: the field was optional (no client sends it there),
    so a tablet an admin had blocked in Settings kept saving simply by
    leaving it out, while the Files page on that same tablet showed the
    banner saying it could not. Requiring the field instead would have
    422'd every Save in the dashboard and in the Android app, so the
    previous pass left it optional and said so out loud. That was the right
    call with the information it had, and it is not the only option: the
    server knows more about the caller than the body does.

    Four places are consulted, most explicit first:

    1. the request body — a caller that names itself is taken at its word,
       exactly as ``/api/files`` takes it;
    2. ``X-Device-Id`` — one header, set once in a client's HTTP layer,
       covers every route at once. Nothing sends it yet; it is here so the
       Android app's fix is one line in ``DeviceAuthInterceptor`` rather
       than a per-screen edit;
    3. ``?device_id=`` — what ``/api/files/browse`` already carries, so a
       caller that identifies itself while reading is identified when it
       writes from the same URL shape;
    4. the ``domovoi-device-id`` COOKIE, which the server itself sets on
       ``POST /api/devices/register`` — the call every browser makes on
       every dashboard load, holding the very id the blocklist is keyed on.
       No client change, no new field, and it arrives on every request the
       browser makes for as long as the device exists.

    What that closes: a blocked BROWSER cannot write through the documents
    door any more, because the identity does not depend on the request
    body naming it. What it does not close, and this is stated rather than
    implied: the native Android app builds its OkHttp client with no cookie
    jar, so it stays unidentified on these routes until it sends the id
    (one header, item 2). And within the household an id is self-asserted
    either way — the module docstring has always said so — so this is
    household policy made to work on the clients people actually use, not
    a security boundary. The difference from before is the DEFAULT: a
    normal client now carries its name without being asked, instead of
    omitting it without being noticed.

    A malformed value is ignored rather than refused: an unreadable cookie
    from some older build must not turn into a 400 on a legitimate save.
    """
    for candidate in (
        body_device_id,
        request.headers.get("X-Device-Id"),
        request.query_params.get("device_id"),
        request.cookies.get(DEVICE_ID_COOKIE),
    ):
        ident = valid_device_id(candidate)
        if ident is not None:
            return ident
    return None


async def assert_caller_may_write(
    request: Request, device_id: Optional[str] = None
) -> None:
    """The per-device write block for a caller arriving through a door
    other than ``/api/files``: identify it the way :func:`caller_device_id`
    does (body, ``X-Device-Id``, ``?device_id=``, the registration cookie)
    and ``403`` when that device is blocked. A caller that names itself
    nowhere is not identified, and so not blocked — the limit
    :func:`caller_device_id` states.

    The ``files_device_blocks`` row reads "block a device from uploading,
    moving or importing ANYWHERE", so every route that writes into a
    library calls this — the documents saves (via
    :func:`assert_documents_write_allowed`) and the music library upload
    (WEB-12), which writes into ``core:music`` without passing here.
    """
    caller = caller_device_id(request, device_id)
    if caller:
        await _assert_can_write(caller)


async def assert_documents_write_allowed(
    request: Request, device_id: Optional[str] = None
) -> None:
    """Apply THIS module's write policy for ``core:documents`` to a caller
    arriving through the other door.

    ``/api/files`` and ``/api/documents`` both write into the Documents
    folder — two doors, one room. Until 2026-09-24 the difference between
    them could not be exploited, because every ``/api/documents`` write
    needed an admin Bearer while ``/api/files`` was device tier: the
    stricter door could not be routed around. Both doors are device tier
    now, and two doors with different locks means the WEAKER one decides
    the policy. So the documents router asks the module that owns these
    rules rather than carrying a copy of them, because a copy drifts and
    the drift is invisible until somebody probes for it.

    The rules, in the order ``POST /api/files/upload`` applies them:

    1. the per-device block (:func:`_assert_can_write`) — 403;
    2. :func:`_assert_admin_for` — 401 when the resolved library is an
       admin-write one (a removable drive, or anything named in
       :data:`ADMIN_WRITE_LIBRARY_IDS`). ``core:documents`` is in neither
       today; this call is what makes the documents door follow if either
       ever changes, instead of silently staying open;
    3. the library's own ``editable`` flag — 403.

    ``device_id`` in the BODY is still optional here and required on
    ``/api/files`` — no client sends it on these routes, and requiring it
    would 422 every Save. It no longer decides anything on its own:
    :func:`caller_device_id` also reads the ``X-Device-Id`` header, the
    ``?device_id=`` query and the ``domovoi-device-id`` cookie the server
    sets on every device registration, so a browser is identified here
    whether or not the JSON says who it is. Read that function for what
    that closes and what it leaves open (the Android app, which keeps no
    cookie jar).
    """
    await assert_caller_may_write(request, device_id)
    # The admin-write test is applied to the library ID, BEFORE the record
    # is resolved. ``core_library`` answers None whenever ``root_rejection``
    # turns documents_dir down — most often "does not exist" on a headless
    # install — and the save that CREATES the folder is exactly the save
    # that hit that window, so the one save on a fresh install skipped the
    # library rules entirely. Nothing was open in practice (the set is
    # empty and core:documents is editable), but this function is what the
    # branch offers as proof that the documents door follows the hook if
    # the policy ever changes, and the proof had a hole in it that the
    # test could not see, because the test runs with the folder present.
    if DOCUMENTS_LIBRARY_ID in ADMIN_WRITE_LIBRARY_IDS:
        await require_admin_mutation(request)
    lib = core_library(DOCUMENTS_LIBRARY_ID)
    if lib is None:
        # Documents is not on the Files surface at all right now — almost
        # always because ``documents_dir`` does not exist yet on a
        # headless install. There is no library-level rule to apply, and
        # the first save is what creates the folder: refusing here would
        # leave the library permanently unreachable.
        return
    await _assert_admin_for(lib, request)
    if not lib.editable:
        raise HTTPException(status_code=403, detail="library is not editable")


# ─── GET /libraries ──────────────────────────────────────────────────────────
@router.get("/libraries", dependencies=[Depends(require_device_read)])
async def list_libraries() -> dict[str, Any]:
    """Rebuild the registry fresh and return the public records (root_path
    stripped), ordered core, plugin, removable."""
    libs = await build_libraries()
    return {"libraries": [lib.public() for lib in libs]}


# ─── GET /browse ─────────────────────────────────────────────────────────────
# Folders first under every sort, then the sort's own field, then the name
# (unique within a folder) so a cursor names exactly one place.
FileSort = Literal["name", "newest", "oldest", "largest"]
_FILE_SORT_KEYS: dict[str, Any] = {
    "name": lambda e: (not e["is_dir"], e["name"].lower(), e["name"]),
    "newest": lambda e: (not e["is_dir"], -float(e["mtime"] or 0.0), e["name"].lower(), e["name"]),
    "oldest": lambda e: (not e["is_dir"], float(e["mtime"] or 0.0), e["name"].lower(), e["name"]),
    "largest": lambda e: (not e["is_dir"], -int(e["size"] or 0), e["name"].lower(), e["name"]),
}
FileKind = Literal["folder", "audio", "video", "doc-office", "doc-text", "image", "pdf", "other"]


def _is_link(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        is_junction = getattr(entry, "is_junction", None)   # 3.12+, Windows
        return bool(is_junction and is_junction())
    except OSError:
        return True   # can't tell: resolve it like a link


def _read_folder(root: Path, target: Path) -> listing_cache.Snapshot:
    """Every entry of one folder, filtered for the Files surface. Sync (a
    worker thread). Every entry passes the same three checks as before:
    realpath inside the root, not secret-shaped, not private by location.

    Cost (measured 2026-10-11, one 10,000-entry folder on the Windows dev
    box): ~6 s before. ``target`` is already resolved inside ``root``
    (safe_join), so an entry that is not a link resolves to ``target/name``
    and only links (symlinks, Windows junctions) pay for a realpath; the
    relative path is built as a string rather than by ``relative_to``
    (~0.1 ms a call on Windows); and :func:`private_path_check` resolves its
    fixed directories once per folder."""
    private = private_path_check()
    rows: list[dict[str, Any]] = []
    folder_mtime = os.stat(target).st_mtime_ns
    rel_dir = target.relative_to(root).as_posix()
    prefix = "" if rel_dir in ("", ".") else rel_dir + "/"
    with os.scandir(target) as it:
        found = list(it)
    for entry in found:
        name = entry.name
        if is_sensitive_name(name):
            continue
        # An upload still on its way (/upload-stream's part-file).
        if name.startswith(PART_PREFIX):
            continue
        if _is_link(entry):
            # Symlink guard — drop anything whose realpath escapes the root.
            real = (target / name).resolve(strict=False)
            try:
                real.relative_to(root)
            except ValueError:
                continue
        else:
            real = target / name
        if private(real, resolved=True):
            continue
        try:
            is_dir = entry.is_dir()
            st = entry.stat()
        except OSError:
            continue
        rows.append({
            "name": name,
            "rel": prefix + name,
            "is_dir": is_dir,
            "size": None if is_dir else st.st_size,
            "mtime": st.st_mtime,
            "kind": _kind_for(name, is_dir),
            "locked_by": None,
        })
    return listing_cache.Snapshot(
        rows=rows, hay=[r["name"].lower() for r in rows],
        dirs={str(target): folder_mtime},
    )


@router.get("/browse", dependencies=[Depends(require_device_read)])
async def browse(
    library_id: str = Query(...),
    path: str = Query(""),
    device_id: Optional[str] = Query(None, min_length=3, max_length=64),
    q: Optional[str] = Query(None, max_length=200,
                             description="Case-insensitive substring of the entry name"),
    kind: Optional[FileKind] = Query(
        None, description="Only entries of this kind (folder = folders only)"),
    sort: FileSort = Query(
        "name", description="name (default) · newest · oldest · largest; folders first"),
    limit: Optional[int] = paging.limit_query(),
    cursor: Optional[str] = paging.cursor_query(),
    offset: Optional[int] = paging.offset_query(),
    refresh: bool = Query(
        False, description="Re-read the folder instead of using the cached listing"),
) -> BrowseResponse:
    """One directory level inside a library, folders first. Every entry's
    realpath is re-checked inside the root (drops symlinks that escape)
    and secret-shaped names are filtered. (The per-file editor-lock join is
    gone with the office engines — the homegrown editors don't lock.)

    Paged when ``limit`` / ``cursor`` / ``offset`` is sent (paging.py):
    ``entries`` is one page; the search (``q``), the ``kind`` filter and
    the sort run here over the whole folder. Unpaged — an old client — it
    is the whole folder, as before. The listing is cached between pages
    (listing_cache) and re-read when the folder's mtime moves, a write
    through this server lands in the library, or ``refresh=true``.

    ``device_id`` is optional and only affects ``writable`` / ``blocked_reason``
    — reading is never blocked, so a blocked device can still SEE the folder
    and is told why it can't change it."""
    lib = await _resolve_library(library_id)
    root = lib.root_path
    target = safe_join(root, path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="directory not found")
    # A secret-shaped folder (``.ssh``, ``.gnupg``, ``tls``) is left out of
    # its parent's listing below; typing its path must not list it either.
    if has_sensitive_segment(path, target, root):
        raise HTTPException(status_code=404, detail="directory not found")

    rel = target.relative_to(root).as_posix()
    rel = "" if rel == "." else rel
    breadcrumb = [seg for seg in rel.split("/") if seg] if rel else []

    needle = (q or "").strip().lower()

    def answer() -> tuple[list[dict[str, Any]], dict[str, Any], int]:
        """The folder's page, off the event loop: the read, the sort and a
        search's scan (memoised on the listing, so its later pages are
        lookups) are tens of milliseconds at 50,000 entries."""
        snap = listing_cache.get(
            (lib.id, str(root), "folder", rel),
            lambda: _read_folder(root, target),
            force=refresh,
        )
        rows, keys, order = snap.view(sort, _FILE_SORT_KEYS[sort])
        matches = None
        if needle or kind:
            hay = snap.hay
            matches = snap.memo(("folder", sort, needle, kind), lambda: [
                i for i in range(len(rows))
                if (not needle or needle in hay[order[i]]) and (not kind or rows[i]["kind"] == kind)
            ])
        if paging.wants_page(limit, cursor, offset):
            page, fields = paging.page_view(
                rows, keys, sort=sort, limit=limit or paging.DEFAULT_LIMIT,
                cursor=cursor, offset=offset, matches=matches,
            )
        else:
            page = rows if matches is None else [rows[i] for i in matches]
            fields = paging.page_fields(total=len(page), limit=None, has_more=False,
                                        next_cursor=None)
        return page, fields, len(rows)

    try:
        page, fields, folder_total = await anyio.to_thread.run_sync(answer)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"cannot list directory: {e}")

    blocked_reason = await _block_status(device_id)
    return BrowseResponse(
        library_id=lib.id,
        path=rel,
        editable=lib.editable,
        importable=lib.importable,
        doc_editing=lib.doc_editing,
        breadcrumb=breadcrumb,
        entries=[BrowseEntry(**r) for r in page],
        writable=blocked_reason is None,
        blocked_reason=blocked_reason,
        folder_total=folder_total,
        **fields,
    )


# ─── GET /download ───────────────────────────────────────────────────────────
@router.get("/download", dependencies=[Depends(require_device_read)])
async def download(
    request: Request,
    library_id: str = Query(...),
    path: str = Query(...),
):
    """Download a file (as an attachment) or a directory (server-built zip with
    a member cap). Audio uses ``serve_audio_range`` (Range/206); everything else
    is a plain attachment ``FileResponse``.

    A FILE is device tier. A DIRECTORY is not: the zip is built in memory
    and hands back a whole tree in one request, so it asks for an admin
    session (REV-1)."""
    lib = await _resolve_library(library_id)
    root = lib.root_path
    target = safe_join(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="not found")
    # Every segment, as sent and as resolved: ``.ssh/config`` is inside a
    # withheld folder even though ``config`` is an ordinary name (REV-12).
    if is_sensitive_name(target.name) or has_sensitive_segment(path, target, root):
        raise HTTPException(status_code=404, detail="not found")

    if target.is_dir():
        await require_admin_read(request)
        return _zip_directory(target, root)

    if target.suffix.lower() in AUDIO_EXTENSIONS:
        return serve_audio_range(
            target, request, download_name=safe_download_name(target.name)
        )
    return FileResponse(
        str(target),
        filename=target.name,
        headers=attachment_headers(safe_download_name(target.name)),
    )


def _zip_directory(target: Path, root: Path) -> Response:
    """Zip a directory subtree (member-capped, symlink-escapes skipped). Built
    in memory with an explicit Content-Length — fine for a LAN dashboard."""
    buf = io.BytesIO()
    added = 0
    # rglob descends into a folder whose own entry is skipped below, so the
    # location check runs on every member: a library rooted above the
    # config dir must not zip up ~/.domovoi, nor any library the command
    # recordings.
    private = private_path_check()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for child in target.rglob("*"):
            if child.is_symlink():
                continue
            real = child.resolve(strict=False)
            try:
                real.relative_to(root)
            except ValueError:
                continue
            if is_sensitive_name(child.name) or private(real):
                continue
            if not child.is_file():
                continue
            added += 1
            if added > _MAX_TREE_MEMBERS:
                raise HTTPException(
                    status_code=413,
                    detail=f"directory exceeds the {_MAX_TREE_MEMBERS}-file zip cap",
                )
            z.write(child, arcname=child.relative_to(target).as_posix())
    if added == 0:
        raise HTTPException(status_code=404, detail="directory is empty")
    data = buf.getvalue()
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_download_name(target.name)}.zip"',
            "Content-Length": str(len(data)),
        },
    )


# ─── POST /upload ────────────────────────────────────────────────────────────
@router.post(
    "/upload",
    response_model=UploadResponse,
    # Multipart, so also the preflight-forcing header (WEB-6): without it a
    # page on any origin could auto-submit this form.
    dependencies=[Depends(require_device), Depends(require_requested_with)],
)
async def upload(
    request: Request,
    library_id: str = Form(...),
    path: str = Form(""),
    device_id: str = Form(..., min_length=3, max_length=64),
    files: list[UploadFile] = File(...),
    create_dir: bool = Form(False),
) -> UploadResponse:
    """Upload files into the currently-browsed directory. Open (daily tier);
    ``403`` when the calling device is blocked. Rejected unless the library is
    editable. Each name is sanitized to a bare basename, deduped, and the
    deduped target re-containment-checked before write.

    ``create_dir=true`` makes the destination ``path`` first — ONE level:
    its parent must exist; the new name is checked like a file name (no
    ``..``, control bytes, a Windows device name, a secret-shaped or
    trailing-dot name) and the folder re-checked inside the library root.
    A folder already there is fine. How the app adds a per-chapter folder
    as one audiobook: the folder, its files, then the usual reindex. A
    folder made here that ends up with nothing saved in it is removed."""
    await _assert_can_write(device_id)
    lib = await _resolve_library(library_id)
    await _assert_admin_for(lib, request)
    if not lib.editable:
        raise HTTPException(status_code=403, detail="library is not editable")
    root = lib.root_path
    created_dir = False
    made_id: tuple[int, int] | None = None
    if create_dir:
        dest, created_dir = _make_upload_folder(root, path)
        if created_dir:
            st = dest.stat()
            made_id = (st.st_dev, st.st_ino)
    else:
        dest = safe_join(root, path)
    if not dest.exists() or not dest.is_dir():
        raise HTTPException(status_code=404, detail="destination directory not found")

    # Every upload into ``dest`` is counted while it runs, from here (no
    # await since the folder was found) to its end: the folder this request
    # made is taken away again only when nothing was saved in it and no
    # other upload is using it.
    dest_key = os.path.normcase(str(dest))
    _UPLOADS_INTO[dest_key] = _UPLOADS_INTO.get(dest_key, 0) + 1
    saved: list[str] = []
    try:
        return await _upload_files(request, lib, root, dest, files, saved, created_dir)
    finally:
        left = _UPLOADS_INTO.get(dest_key, 1) - 1
        if left > 0:
            _UPLOADS_INTO[dest_key] = left
        else:
            _UPLOADS_INTO.pop(dest_key, None)
        if created_dir and not saved and left == 0:
            _remove_made_folder(dest, made_id)


# Uploads running now, by destination folder (one web process).
_UPLOADS_INTO: dict[str, int] = {}


def _remove_made_folder(dest: Path, made_id: tuple[int, int] | None) -> None:
    """Take away the folder ``create_dir`` made for an upload that saved
    nothing (refused names, an error part-way): only that very folder (the
    same device and inode as when it was made) and only while it is empty
    (``rmdir`` refuses anything else), so never someone else's files."""
    try:
        st = dest.stat()
        if made_id is not None and (st.st_dev, st.st_ino) != made_id:
            return
        dest.rmdir()
    except OSError:
        pass


async def _upload_files(
    request: Request, lib: MediaLibrary, root: Path, dest: Path,
    files: list[UploadFile], saved: list[str], created_dir: bool,
) -> UploadResponse:
    skipped: list[str] = []
    for up in files:
        raw = up.filename or "upload"
        name = _safe_basename(raw)
        # ``is_sensitive_name`` now asks about every name this one could
        # turn into on disk (".env." and ".env " both land as ".env" on
        # Windows), and ``unstorable_reason`` refuses the shapes that would
        # be stored somewhere other than where they say — a stream
        # separator, a trailing dot or space, a control character.
        if (
            not name
            or name in (".", "..")
            or is_sensitive_name(name)
            or unstorable_reason(name) is not None
        ):
            skipped.append(f"{raw}: bad filename")
            continue
        cand = _unique_path(dest, name)
        try:
            cand.resolve(strict=False).relative_to(root)
        except ValueError:
            skipped.append(f"{name}: refused (escapes library root)")
            continue
        # Copied in chunks from the parser's spool, never read whole: this
        # was ``cand.write_bytes(await up.read())``, which held every file
        # of the request in memory at once — a 3 GB audiobook was 3 GB of
        # this process.
        try:
            await anyio.to_thread.run_sync(_copy_spool_to, up.file, cand)
        except OSError as e:
            skipped.append(f"{name}: write failed ({e})")
            continue
        saved.append(cand.name)

    if not saved:
        raise HTTPException(
            status_code=400,
            detail="no files saved" + (f"; {skipped}" if skipped else ""),
        )

    listing_cache.invalidate(lib.id)
    reindex_triggered = False
    if lib.reindex_kind in INDEXED_KINDS:
        reindex_triggered = await _trigger_reindex(lib.reindex_kind, request)
    return UploadResponse(saved=saved, skipped=skipped, reindex_triggered=reindex_triggered,
                          created_dir=created_dir)


# One write's worth of an upload. Memory per upload in flight is about this
# much, whatever the file's size.
UPLOAD_CHUNK_BYTES = 1024 * 1024


def _copy_spool_to(src: Any, cand: Path) -> None:
    """Copy a multipart part's spooled file to ``cand`` a chunk at a time;
    a part-written file is removed again."""
    src.seek(0)
    try:
        with open(cand, "xb") as out:
            shutil.copyfileobj(src, out, UPLOAD_CHUNK_BYTES)
    except FileExistsError:
        raise OSError(f"{cand.name} appeared while it was being saved")
    except OSError:
        try:
            cand.unlink()
        except OSError:
            pass
        raise


# ─── Folder upload: one file per request, streamed (WEB-19) ─────────────────
#
# The dashboard's Upload → Folder… and a folder dropped on the Files page
# send their files one request each, a few at a time, to
# ``POST /upload-stream``. The body IS the file — no multipart — and it goes
# to disk a chunk at a time as it arrives, so memory does not grow with the
# file and there is no second copy in a temp spool. ``rel_path`` carries the
# file's place inside the folder that was picked ("Book Title/01.mp3");
# missing folders are made inside the target, one checked level at a time.
#
# Before the queue starts, ``POST /upload-check`` says which of the batch's
# paths are already there, so the page can ask ONCE for the whole batch:
# keep both, replace or skip (``on_conflict``). Each file is sent with
# ``reindex=false`` and the batch ends with one ``POST /upload-done``, which
# runs the library's reindex once rather than once per file.

#: The largest single file ``/upload-stream`` takes — the same ceiling the
#: transport guard sets on the route (domovoi/transport_guard.py). A long
#: audiobook as one .m4b is 1–3 GB; this leaves room for a lossless one.
MAX_UPLOAD_FILE_BYTES = 8 * 1024 * 1024 * 1024

#: How deep a picked folder may go, and how many paths one check may name.
_MAX_REL_DEPTH = 32
_MAX_CHECK_PATHS = 5000

#: The prefix of the hidden file a streamed upload is written to before it
#: takes its name. Hidden from listings (``_read_folder``).
PART_PREFIX = ".domovoi-upload-"

_RESERVED_ON_WINDOWS_CHARS = '<>:"|?*'

ConflictRule = Literal["keep_both", "replace", "skip"]


def upload_segment_problem(seg: str) -> str | None:
    """Why ``seg`` can't be one folder or file name of an uploaded path, or
    None. Applied to EVERY segment of ``rel_path``.

    Looser than :func:`new_folder_name_problem` in one way on purpose: a
    colon or question mark is refused only on a Windows server, where NTFS
    can't store it — "Dune: Messiah/01.mp3" from a Mac is an ordinary name on
    a Linux box. Everything that changes WHERE a file lands is refused on
    every host: a dot segment, a separator inside a name, a trailing dot or
    space (Windows trims it), a Windows device name, a secret-shaped name."""
    if not seg or not seg.strip():
        return "an empty name"
    if seg in (".", ".."):
        return "a '.' or '..' segment"
    if "\\" in seg or "/" in seg:
        return "a backslash in a name"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in seg):
        return "control characters in a name"
    if seg != seg.strip() or seg.endswith("."):
        return "a name can't start or end with a space, or end with a dot"
    if len(seg.encode("utf-8", "surrogatepass")) > 255:
        return "a name longer than 255 bytes"
    if seg.split(".", 1)[0].rstrip(" ").upper() in _WINDOWS_DEVICE_NAMES:
        return "a name Windows reserves for a device"
    if is_sensitive_name(seg):
        return "a reserved name"
    if _fs.WINDOWS_NAME_RULES and any(ch in seg for ch in _RESERVED_ON_WINDOWS_CHARS):
        return "a character this server's filesystem can't store (< > : \" | ? *)"
    return None


def split_upload_rel(rel_path: str) -> list[str]:
    """``rel_path`` → its segments, or ``400`` naming what is wrong with it.
    The last segment is the file name. Never normalised into something else:
    a path that needs fixing is refused, not guessed at."""
    rel = rel_path or ""
    if not rel.strip():
        raise HTTPException(status_code=400, detail="rel_path is empty")
    if rel.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel):
        raise HTTPException(status_code=400, detail=f"{rel!r}: absolute paths are refused")
    segs = rel.split("/")
    if len(segs) > _MAX_REL_DEPTH:
        raise HTTPException(status_code=400, detail=f"{rel!r}: more than {_MAX_REL_DEPTH} folders deep")
    for seg in segs:
        problem = upload_segment_problem(seg)
        if problem is not None:
            raise HTTPException(status_code=400, detail=f"{rel!r}: {problem}")
    return segs


def _within(p: Path, root: Path) -> bool:
    try:
        p.resolve(strict=False).relative_to(root)
        return True
    except ValueError:
        return False


def make_upload_parents(root: Path, base: Path, folders: list[str]) -> Path:
    """Make ``folders`` (already checked by :func:`split_upload_rel`) under
    ``base``, one level at a time, and return the deepest. Every level is
    re-resolved inside ``root`` after it exists, so a link sitting (or
    raced) in a folder's place can point anywhere INSIDE the library and
    never out; a file in a folder's place is a ``409``."""
    cur = base
    for name in folders:
        nxt = cur / name
        try:
            nxt.mkdir()
        except FileExistsError:
            pass
        except OSError as e:
            raise HTTPException(status_code=400, detail=f"couldn't make the folder {name!r}: {e}")
        if not _within(nxt, root):
            raise HTTPException(status_code=400, detail="path escapes library root")
        if not nxt.is_dir():
            raise HTTPException(status_code=409, detail=f"a file named {name!r} is where a folder should go")
        cur = nxt.resolve(strict=False)
    return cur


def _reserve_unique(dirpath: Path, name: str) -> Path:
    """``name``, or ``name (1)``… — the first free one, CREATED empty so a
    second upload running at the same moment can't pick it too."""
    stem, suffix = Path(name).stem, Path(name).suffix
    i = 0
    while True:
        cand = dirpath / (name if i == 0 else f"{stem} ({i}){suffix}")
        try:
            fd = os.open(cand, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0))
            os.close(fd)
            return cand
        except FileExistsError:
            i += 1
            if i > 10_000:
                raise HTTPException(status_code=409, detail=f"{name}: no free name left")


async def _writable_library(request: Request, library_id: str, device_id: str) -> MediaLibrary:
    """The write gates every upload route applies, in upload's order: the
    device block (403), the admin-write libraries (401), ``editable`` (403)."""
    await _assert_can_write(device_id)
    lib = await _resolve_library(library_id)
    await _assert_admin_for(lib, request)
    if not lib.editable:
        raise HTTPException(status_code=403, detail="library is not editable")
    return lib


def _fmt_gb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GB"


class StreamUploadResponse(BaseModel):
    #: Where the file landed, relative to the library root — "" when skipped.
    saved: str = ""
    #: Why it was not saved ("already there" for on_conflict=skip).
    skipped: Optional[str] = None
    replaced: bool = False
    bytes: int = 0
    reindex_triggered: bool = False


@router.post(
    "/upload-stream",
    response_model=StreamUploadResponse,
    dependencies=[Depends(require_device), Depends(require_requested_with)],
)
async def upload_stream(
    request: Request,
    library_id: str = Query(...),
    rel_path: str = Query(..., max_length=4096),
    path: str = Query(""),
    device_id: str = Query(..., min_length=3, max_length=64),
    on_conflict: ConflictRule = Query("keep_both"),
    reindex: bool = Query(True),
) -> StreamUploadResponse:
    """One file of a folder upload; the request body is the file's bytes.

    ``path`` is the folder on screen (it must exist); ``rel_path`` is the
    file's place under it ("Book Title/Disc 1/01.mp3"), each segment checked
    (:func:`upload_segment_problem`), missing folders made inside the
    library (:func:`make_upload_parents`). The body is written to a hidden
    part-file next to its destination a chunk at a time and renamed into
    place only when it is complete — a cancelled or broken upload leaves
    nothing behind, and a ``replace`` never leaves a half-written file under
    the real name. ``on_conflict``: ``keep_both`` (the default, and what
    ``/upload`` always did) saves as "name (1).ext"; ``replace`` overwrites;
    ``skip`` answers ``skipped="already there"`` without reading the body.
    One file is at most :data:`MAX_UPLOAD_FILE_BYTES` (``413``)."""
    lib = await _writable_library(request, library_id, device_id)
    root = lib.root_path
    segs = split_upload_rel(rel_path)
    folders, name = segs[:-1], segs[-1]

    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_UPLOAD_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(f"{name} is {_fmt_gb(int(declared))}; one file can be at most "
                    f"{_fmt_gb(MAX_UPLOAD_FILE_BYTES)}"),
        )

    base = safe_join(root, path)
    if not base.is_dir():
        raise HTTPException(status_code=404, detail="destination directory not found")
    rel_dir = "/".join(folders)
    if rel_dir:
        # The whole folder path as a plain destination would be, before
        # anything is made: containment, the config dir, the recordings.
        safe_join(root, ((path or "").rstrip("/") + "/" + rel_dir).lstrip("/"))
    dest_dir = make_upload_parents(root, base, folders)
    final = dest_dir / name
    if not _within(final, root):
        raise HTTPException(status_code=400, detail="path escapes library root")

    def rel_of(p: Path) -> str:
        return p.resolve(strict=False).relative_to(root).as_posix()

    if final.exists() or final.is_symlink():
        if final.is_dir():
            return StreamUploadResponse(skipped="a folder of that name is already there")
        if on_conflict == "skip":
            return StreamUploadResponse(skipped="already there")

    part = dest_dir / f"{PART_PREFIX}{secrets.token_hex(8)}.part"
    written = 0
    try:
        f = await anyio.to_thread.run_sync(lambda: open(part, "xb"))
        try:
            buf = bytearray()
            async for chunk in request.stream():
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_UPLOAD_FILE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"{name}: one file can be at most {_fmt_gb(MAX_UPLOAD_FILE_BYTES)}",
                    )
                buf += chunk
                if len(buf) >= UPLOAD_CHUNK_BYTES:
                    data, buf = bytes(buf), bytearray()
                    await anyio.to_thread.run_sync(f.write, data)
            if buf:
                await anyio.to_thread.run_sync(f.write, bytes(buf))
        finally:
            await anyio.to_thread.run_sync(f.close)
        replaced = False
        if on_conflict == "replace" and final.is_file() and not final.is_symlink():
            target = final
            replaced = True
        elif on_conflict == "skip" and (final.exists() or final.is_symlink()):
            # Arrived while this one was on its way.
            _unlink_quiet(part)
            return StreamUploadResponse(skipped="already there")
        else:
            target = await anyio.to_thread.run_sync(_reserve_unique, dest_dir, name)
        await anyio.to_thread.run_sync(os.replace, part, target)
    except HTTPException:
        _unlink_quiet(part)
        raise
    except OSError as e:
        _unlink_quiet(part)
        raise HTTPException(status_code=507 if getattr(e, "errno", None) == 28 else 400,
                            detail=f"{name}: write failed ({e.strerror or e})")
    except BaseException:
        # The client went away (cancel), or the server is stopping.
        _unlink_quiet(part)
        raise

    reindex_triggered = False
    if reindex:
        listing_cache.invalidate(lib.id)
        if lib.reindex_kind in INDEXED_KINDS:
            reindex_triggered = await _trigger_reindex(lib.reindex_kind, request)
    return StreamUploadResponse(saved=rel_of(target), replaced=replaced, bytes=written,
                                reindex_triggered=reindex_triggered)


def _unlink_quiet(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


class UploadCheckRequest(BaseModel):
    library_id: str
    path: str = ""
    rel_paths: list[str] = Field(..., min_length=1, max_length=_MAX_CHECK_PATHS)
    device_id: str = _DEVICE_ID_FIELD


class UploadCheckResponse(BaseModel):
    #: The rel_paths that already name a file (or folder) in place.
    existing: list[str]
    #: rel_paths the server would refuse, each with why.
    refused: dict[str, str]


@router.post("/upload-check", response_model=UploadCheckResponse,
             dependencies=[Depends(require_device)])
async def upload_check(request: Request, req: UploadCheckRequest) -> UploadCheckResponse:
    """Before a batch: which of its files are already there (so the page
    can ask once — keep both, replace, skip) and which names the server
    would refuse. Writes nothing; gated like the upload itself, so a
    blocked device hears it before the first byte is sent."""
    lib = await _writable_library(request, req.library_id, req.device_id)
    root = lib.root_path
    base = safe_join(root, req.path)
    if not base.is_dir():
        raise HTTPException(status_code=404, detail="destination directory not found")

    def check() -> tuple[list[str], dict[str, str]]:
        existing: list[str] = []
        refused: dict[str, str] = {}
        for rel in req.rel_paths:
            try:
                split_upload_rel(rel)
            except HTTPException as e:
                refused[rel] = str(e.detail).split(": ", 1)[-1]
                continue
            cand = base / rel
            if not _within(cand, root):
                refused[rel] = "path escapes library root"
            elif cand.exists():
                existing.append(rel)
        return existing, refused

    existing, refused = await anyio.to_thread.run_sync(check)
    return UploadCheckResponse(existing=existing, refused=refused)


class UploadDoneRequest(BaseModel):
    library_id: str
    device_id: str = _DEVICE_ID_FIELD


class UploadDoneResponse(BaseModel):
    reindex_triggered: bool
    #: "music" / "audiobooks" when the library is one the server indexes.
    reindex_kind: Optional[str] = None


@router.post("/upload-done", response_model=UploadDoneResponse,
             dependencies=[Depends(require_device)])
async def upload_done(request: Request, req: UploadDoneRequest) -> UploadDoneResponse:
    """The end of a batch sent with ``reindex=false``: the listing is read
    again everywhere, and an indexed library (Music, Audiobooks) reindexes
    ONCE for the whole batch. Same gates as the upload, and nothing the
    upload itself would not have run."""
    lib = await _writable_library(request, req.library_id, req.device_id)
    listing_cache.invalidate(lib.id)
    kind = lib.reindex_kind if lib.reindex_kind in INDEXED_KINDS else None
    triggered = await _trigger_reindex(kind, request) if kind else False
    return UploadDoneResponse(reindex_triggered=triggered, reindex_kind=kind)


# Windows device names: a file or folder so named, with any extension,
# opens a device rather than a folder on a Windows server (and can't be
# deleted from Explorer) — refused for a new folder on every host, so a
# library stays portable between them.
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
     *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10)),
     "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}
)


def new_folder_name_problem(name: str) -> str | None:
    """Why ``name`` can't be a new folder's name, or None. Stricter than
    the platform needs, on purpose: the same answer on a Linux and a
    Windows server."""
    if not name or not name.strip():
        return "empty folder name"
    if "/" in name or "\\" in name:
        return "a folder name can't hold a path separator"
    if name in (".", "..") or not name.strip("."):
        return "not a folder name"
    if name != name.strip() or name.endswith("."):
        return "a folder name can't start or end with a space, or end with a dot"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in name):
        return "control characters are not allowed in a folder name"
    if any(ch in name for ch in '<>:"|?*'):
        return "a folder name can't hold < > : \" | ? or *"
    if name.split(".", 1)[0].rstrip(" ").upper() in _WINDOWS_DEVICE_NAMES:
        return "that name is reserved on Windows"
    if is_sensitive_name(name):
        return "that name is reserved"
    return None


def _make_upload_folder(root: Path, path: str) -> tuple[Path, bool]:
    """``create_dir=true``: make ``path`` (one new level under an existing
    folder) inside ``root`` and return it, with whether it was made now.

    The parent and the whole path go through :func:`safe_join` exactly like
    a plain upload's destination (containment, symlinks resolved, ``..``
    and drive paths refused); the new name through
    :func:`new_folder_name_problem`; the folder is re-checked inside the
    root once made (a link raced into its place). A folder already there is
    returned as it is."""
    norm = (path or "").replace("\\", "/").strip("/")
    if not norm.strip():
        raise HTTPException(status_code=400, detail="create_dir needs a folder name in path")
    parent_rel, _, name = norm.rpartition("/")
    problem = new_folder_name_problem(name)
    if problem is not None:
        raise HTTPException(status_code=400, detail=f"{name!r}: {problem}")
    parent = safe_join(root, parent_rel)
    if not parent.is_dir():
        raise HTTPException(status_code=404, detail="destination's parent folder not found")
    # The whole path as a plain upload's destination would be: a link in
    # the new name's place may point anywhere INSIDE the root, never out.
    safe_join(root, norm)
    created = False
    try:
        (parent / name).mkdir()
        created = True
    except FileExistsError:
        pass
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"couldn't make the folder: {e}")
    made = (parent / name).resolve(strict=False)
    try:
        made.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes library root")
    if not made.is_dir():
        raise HTTPException(status_code=409, detail="a file of that name is in the way")
    return made, created


# ─── POST /delete ────────────────────────────────────────────────────────────
@router.post(
    "/delete", response_model=DeleteResponse, dependencies=[Depends(require_admin_mutation)]
)
async def delete(request: Request, req: DeleteRequest) -> DeleteResponse:
    """Delete files (and, with ``recursive=true``, folders) from an editable
    library. A library root can never be deleted (refused when ``t == root`` or
    ``rel`` is empty). Recursive delete uses a bounded, symlink-confined walk.
    For ``core:documents`` any editor lock on a deleted path is released."""
    lib = await _resolve_library(req.library_id)
    if not lib.editable:
        raise HTTPException(status_code=403, detail="library is not editable")
    root = lib.root_path

    deleted: list[str] = []
    removed_paths: list[str] = []
    failed: list[str] = []
    for rel in req.paths:
        if not rel or not rel.strip():
            failed.append(f"{rel}: empty path")
            continue
        try:
            target = safe_join(root, rel)
        except HTTPException:
            failed.append(f"{rel}: rejected")
            continue
        if target == root:
            failed.append(f"{rel}: refusing to delete library root")
            continue
        try:
            if target.is_symlink() or target.is_file():
                target.unlink()
                deleted.append(target.relative_to(root).as_posix())
                removed_paths.append(str(target))
            elif target.is_dir():
                if not req.recursive:
                    failed.append(f"{rel}: is a directory (recursive not set)")
                    continue
                budget = [_MAX_TREE_MEMBERS]
                _confined_rmtree(target, root, budget)
                target.rmdir()
                deleted.append(target.relative_to(root).as_posix())
                removed_paths.append(str(target))
            else:
                failed.append(f"{rel}: not found")
        except HTTPException:
            failed.append(f"{rel}: exceeded member cap")
        except OSError as e:
            failed.append(f"{rel}: {e}")

    if deleted:
        listing_cache.invalidate(lib.id)
        if lib.id == DOCUMENTS_LIBRARY_ID:
            # Co-editing state and version history go with the documents.
            from web.backend.api import collab

            await collab.forget_paths(deleted)
    reindex_triggered = False
    if deleted and lib.reindex_kind in INDEXED_KINDS:
        reindex_triggered = await _trigger_reindex(
            lib.reindex_kind, request, removed=tuple(removed_paths)
        )
    return DeleteResponse(deleted=deleted, failed=failed, reindex_triggered=reindex_triggered)


def _confined_rmtree(directory: Path, root: Path, budget: list[int]) -> None:
    """Recursively delete ``directory``'s contents (caller removes the dir
    itself). Never follows a symlink out — a symlinked entry has only its link
    removed; a child whose realpath escapes ``root`` is skipped. Bounded by
    ``budget[0]`` remaining members (raises HTTPException 413 when exhausted)."""
    for child in directory.iterdir():
        budget[0] -= 1
        if budget[0] < 0:
            raise HTTPException(status_code=413, detail="delete exceeds member cap")
        if child.is_symlink():
            child.unlink()  # remove the link, never descend it
            continue
        real = child.resolve(strict=False)
        try:
            real.relative_to(root)
        except ValueError:
            continue  # escapes root — leave it untouched
        if child.is_dir():
            _confined_rmtree(child, root, budget)
            child.rmdir()
        else:
            child.unlink()


# ─── POST /import ────────────────────────────────────────────────────────────
@router.post("/import", response_model=ImportResponse, dependencies=[Depends(require_device)])
async def import_media(request: Request, req: ImportRequest) -> ImportResponse:
    """Copy a file/dir from a removable source into an importable library.
    Open (daily tier); ``403`` when the calling device is blocked. Server-side
    copy (the browser never streams the bytes), member+byte capped, and the
    source side never follows a symlink out of the mount."""
    await _assert_can_write(req.device_id)
    source = await _resolve_library(req.source_library_id)
    target = await _resolve_library(req.target_library_id)
    await _assert_admin_for(target, request)
    if source.kind != "removable":
        raise HTTPException(status_code=409, detail="source must be a removable drive")
    if not target.importable:
        raise HTTPException(status_code=409, detail="target library is not importable")

    src = safe_join(source.root_path, req.source_path)
    if not src.exists():
        raise HTTPException(status_code=404, detail="source not found")
    dst_dir = safe_join(target.root_path, req.target_path)
    if not dst_dir.exists() or not dst_dir.is_dir():
        raise HTTPException(status_code=404, detail="target directory not found")

    name = _safe_basename(src.name)
    if not name or name in (".", "..") or is_sensitive_name(name):
        raise HTTPException(status_code=400, detail="bad source name")
    dest = _unique_path(dst_dir, name)
    try:
        dest.resolve(strict=False).relative_to(target.root_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="destination escapes target root")

    copied: list[str] = []
    skipped: list[str] = []
    budget = [_MAX_TREE_MEMBERS]
    bytes_budget = [_MAX_IMPORT_BYTES]
    try:
        if src.is_dir():
            dest.mkdir()
            _confined_copytree(src, dest, source.root_path, budget, bytes_budget, skipped)
        else:
            if src.is_symlink() and _escapes(src, source.root_path):
                raise HTTPException(status_code=400, detail="source symlink escapes mount")
            shutil.copy2(src, dest)
        copied.append(dest.relative_to(target.root_path).as_posix())
    except HTTPException:
        raise
    except FileNotFoundError:
        # Removable ejected mid-copy.
        raise HTTPException(status_code=410, detail="drive no longer present")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"copy failed: {e}")

    listing_cache.invalidate(target.id)
    reindex_triggered = False
    if target.reindex_kind in INDEXED_KINDS:
        reindex_triggered = await _trigger_reindex(target.reindex_kind, request)
    return ImportResponse(copied=copied, skipped=skipped, reindex_triggered=reindex_triggered)


# ─── POST /move ──────────────────────────────────────────────────────────────
@router.post("/move", response_model=MoveResponse, dependencies=[Depends(require_device)])
async def move(request: Request, req: MoveRequest) -> MoveResponse:
    """Move files and folders into another folder — the drag-and-drop verb.
    Open (daily tier); ``403`` when the calling device is blocked.

    Within one library or between two, as long as BOTH are editable: a move
    deletes from the source, so a read-only root (a removable drive, a
    read-only plugin library) can only ever be a destination. Removables stay
    copy-only through ``/import``.

    Per-path outcome rather than all-or-nothing, like ``/delete``: one
    colliding name shouldn't abandon the other forty files in the drag.

    The guards, in the order they matter:

    * ``safe_join`` on every source path AND the destination — containment is
      never re-implemented here (see files_security).
    * A library ROOT can't be moved.
    * A directory can't be moved into itself or into its own descendant.
      ``shutil.move`` would happily start recursing into the copy it is
      creating; the check is a realpath prefix test, so a symlinked path that
      resolves back inside the source is caught too.
    * An existing destination name FAILS rather than being overwritten or
      silently renamed. A move that quietly replaced a file would be the one
      unrecoverable operation on this page.
    * Secret-shaped names are refused the same way upload/import refuse them.
    """
    # Device block FIRST, like upload/import: a blocked device gets its 403
    # before any library is resolved or any path touched. (F-010: the block
    # was UI-only for moves — the drag verb never asked.)
    await _assert_can_write(req.device_id)
    source = await _resolve_library(req.source_library_id)
    target = await _resolve_library(req.target_library_id)
    # A move both writes the target and REMOVES from the source, so either
    # side being an admin-write library makes the whole move one.
    await _assert_admin_for(target, request)
    await _assert_admin_for(source, request)
    if not source.editable:
        raise HTTPException(
            status_code=403,
            detail=f"{source.label} is read-only — nothing can be moved out of it",
        )
    if not target.editable:
        raise HTTPException(
            status_code=403, detail=f"{target.label} is read-only"
        )

    dst_dir = safe_join(target.root_path, req.target_path)
    if not dst_dir.exists() or not dst_dir.is_dir():
        raise HTTPException(status_code=404, detail="target directory not found")

    moved: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    for rel in req.paths:
        if not rel or not rel.strip():
            failed.append(f"{rel}: empty path")
            continue
        try:
            src = safe_join(source.root_path, rel)
        except HTTPException:
            failed.append(f"{rel}: rejected")
            continue
        if src == source.root_path:
            failed.append(f"{rel}: refusing to move a library root")
            continue
        if not src.exists() and not src.is_symlink():
            failed.append(f"{rel}: not found")
            continue

        name = _safe_basename(src.name)
        if not name or name in (".", "..") or is_sensitive_name(name):
            failed.append(f"{rel}: bad name")
            continue

        # Already where it's being dropped — a no-op, not a failure.
        if src.parent == dst_dir:
            skipped.append(f"{rel}: already in that folder")
            continue

        # Into itself / into a descendant. Resolve both sides so a symlinked
        # destination that lands back inside the source is caught as well.
        if src.is_dir():
            real_src = src.resolve(strict=False)
            real_dst = dst_dir.resolve(strict=False)
            if real_dst == real_src or _is_under(real_dst, real_src):
                failed.append(f"{rel}: can't move a folder into itself")
                continue

        dest = dst_dir / name
        if dest.exists() or dest.is_symlink():
            failed.append(f"{rel}: “{name}” already exists there")
            continue
        # Belt-and-braces: the joined destination must still be inside the
        # target root after resolution.
        try:
            dest.resolve(strict=False).relative_to(target.root_path)
        except ValueError:
            failed.append(f"{rel}: destination escapes the target library")
            continue

        try:
            # shutil.move handles the cross-filesystem case (copy + unlink),
            # which a plain rename can't — libraries can live on different
            # drives, and on Windows that's the common case.
            shutil.move(str(src), str(dest))
            moved.append(dest.relative_to(target.root_path).as_posix())
            if DOCUMENTS_LIBRARY_ID in (source.id, target.id):
                await _collab_follow_move(source, target, src, dest)
        except OSError as e:
            failed.append(f"{rel}: {e}")

    if moved:
        listing_cache.invalidate(source.id)
        listing_cache.invalidate(target.id)
    # Both sides can be indexed libraries, and a move changes both.
    reindex_triggered = False
    if moved:
        for kind in {source.reindex_kind, target.reindex_kind}:
            if kind in INDEXED_KINDS:
                reindex_triggered = await _trigger_reindex(kind, request) or reindex_triggered
    return MoveResponse(
        moved=moved, skipped=skipped, failed=failed,
        reindex_triggered=reindex_triggered,
    )


async def _collab_follow_move(source: MediaLibrary, target: MediaLibrary,
                              src: Path, dest: Path) -> None:
    """A move in or out of Documents: a co-edited document's id and history
    follow it inside Documents, and go when it leaves."""
    from web.backend.api import collab

    old_rel = src.relative_to(source.root_path).as_posix()
    if source.id == target.id == DOCUMENTS_LIBRARY_ID:
        await collab.note_moved(old_rel, dest.relative_to(target.root_path).as_posix())
    elif source.id == DOCUMENTS_LIBRARY_ID:
        await collab.forget_paths([old_rel])


def _is_under(candidate: Path, ancestor: Path) -> bool:
    """True when ``candidate`` is inside ``ancestor`` (both already resolved)."""
    try:
        candidate.relative_to(ancestor)
        return True
    except ValueError:
        return False


def _escapes(p: Path, root: Path) -> bool:
    try:
        p.resolve(strict=False).relative_to(root)
        return False
    except ValueError:
        return True


def _confined_copytree(
    src_dir: Path,
    dst_dir: Path,
    src_root: Path,
    budget: list[int],
    bytes_budget: list[int],
    skipped: list[str],
) -> None:
    """Copy ``src_dir``'s contents into ``dst_dir`` (both already exist).
    Member+byte capped; a source entry that is a symlink escaping ``src_root``
    (or a secret-shaped name) is skipped, never followed."""
    private = private_path_check()
    for child in sorted(src_dir.iterdir(), key=lambda p: p.name.lower()):
        if is_sensitive_name(child.name) or private(child.resolve(strict=False)):
            skipped.append(child.name)
            continue
        if child.is_symlink() and _escapes(child, src_root):
            skipped.append(f"{child.name}: symlink escapes mount")
            continue
        budget[0] -= 1
        if budget[0] < 0:
            raise HTTPException(status_code=413, detail="import exceeds member cap")
        dest_child = dst_dir / child.name
        if child.is_dir():
            dest_child.mkdir(exist_ok=True)
            _confined_copytree(child, dest_child, src_root, budget, bytes_budget, skipped)
        elif child.is_file():
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
            bytes_budget[0] -= size
            if bytes_budget[0] < 0:
                raise HTTPException(status_code=413, detail="import exceeds byte cap")
            shutil.copy2(child, dest_child)


# ─── Device blocks (admin) ───────────────────────────────────────────────────
#
# Its own prefix rather than ``/api/files/blocks`` so the admin-only surface
# is visibly separate from the open one, and so nothing under ``/api/files``
# needs a dependency that varies by route. Managing blocks IS admin-gated —
# that's what stops a block being lifted from the device it was applied to.

blocks_router = APIRouter(prefix="/api/files/device-blocks", tags=["files"])

_BLOCK_COLUMNS = "id, device_id, device_name, note, created_at"


def _row_to_block(r: Any) -> FilesBlock:
    return FilesBlock(
        id=int(r[0]), device_id=r[1], device_name=r[2], note=r[3], created_at=r[4],
    )


@blocks_router.get(
    "", response_model=list[FilesBlock], dependencies=[Depends(require_admin_read)]
)
async def list_blocks() -> list[FilesBlock]:
    async with session_scope() as s:
        rows = await s.execute(
            text(f"SELECT {_BLOCK_COLUMNS} FROM files_device_blocks ORDER BY id")
        )
        return [_row_to_block(r) for r in rows.all()]


@blocks_router.post(
    "",
    response_model=FilesBlock,
    status_code=201,
    dependencies=[Depends(require_admin_mutation)],
)
async def create_block(payload: FilesBlockCreate) -> FilesBlock:
    """Block a device from writing to any library (upload / move / import).
    Needs a device id, a name, or both — a block naming neither would match
    nothing, and the schema refuses it."""
    device_id = (payload.device_id or "").strip() or None
    device_name = " ".join((payload.device_name or "").split()) or None
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
                    INSERT INTO files_device_blocks (device_id, device_name, note)
                    VALUES (:device_id, :device_name, :note)
                    RETURNING {_BLOCK_COLUMNS}
                    """
                ),
                {"device_id": device_id, "device_name": device_name, "note": payload.note},
            )
        except IntegrityError as e:
            # The partial unique indexes make a duplicate block an integrity
            # error (and the CHECK catches a block naming nothing, though the
            # explicit test above gets there first with a better message).
            # Either way it's a conflict, not a 500.
            raise HTTPException(
                status_code=409, detail="that device is already blocked"
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
            text("DELETE FROM files_device_blocks WHERE id = :id"), {"id": block_id}
        )
    if (result.rowcount or 0) == 0:
        raise HTTPException(status_code=404, detail=f"block {block_id} not found")
