"""Generic multi-library Files API (design §2) — the web dashboard's
``/api/files`` surface.

One router browses/downloads/uploads/deletes/imports across every root the
Files tab exposes (core media dirs, enabled-plugin media libraries, present
removable drives), all resolved server-side by :mod:`files_security`. The
client only ever sends a ``library_id`` + a **relative** path; the absolute
``root_path`` is never serialized.

**Trust posture (daily tier, one exception).** Browsing, downloading,
uploading, moving and importing are OPEN on the LAN — the same posture as
playing music or editing a room queue; a household's phones shouldn't need
the admin password to drop a file into the music folder. Only ``/delete``
takes ``require_admin_mutation``: it's the one verb that destroys something.

What keeps the open writes governable is the same device model the room
queue uses (:mod:`web.backend.api.music_queue`): every write names the
calling ``device_id`` (required — a blocklist anyone evades by omitting the
field is no blocklist), and an admin can take file writes away from a named
device with ``files_device_blocks`` (V012). Reads are never blocked; the
browse response carries ``writable`` / ``blocked_reason`` for the calling
device so a client can disable its own controls and say why. Like the queue
blocklist this is household policy, not a security boundary — device ids
are self-asserted.

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
import shutil
import zipfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from domovoi.admin_auth import require_admin_mutation, require_admin_read
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
from web.backend.api.files_security import (
    INDEXED_KINDS,
    MediaLibrary,
    build_libraries,
    is_sensitive_name,
    safe_join,
)
from web.backend.api.music import _safe_basename, _unique_path
from web.backend.db import session_scope
from web.backend.domovoi_client import auth_forward_headers, post_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])

# Bounded fan-out caps (mirror music.py:_MAX_ZIP_MEMBERS). Guard a recursive
# delete / a dir zip / an import copytree against unbounded work.
_MAX_TREE_MEMBERS = 5000
_MAX_IMPORT_BYTES = 20 * 1024 * 1024 * 1024  # 20 GiB total per import


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
def _entry_kind(entry: Path, is_dir: bool) -> str:
    """kind ∈ folder | audio | video | doc-office | doc-text | image | pdf | other."""
    if is_dir:
        return "folder"
    ext = entry.suffix.lower()
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
async def _trigger_reindex(reindex_kind: Optional[str], request: Request) -> bool:
    """Fire the post-write reindex for an indexed library. music proxies the
    core admin endpoint (credentials forwarded); audiobooks runs the in-process
    indexer; everything else is a no-op. Best-effort — a failure never fails the
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
            from domovoi.workers.audiobook_indexer import index_audiobooks_dir

            await index_audiobooks_dir()
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


class UploadResponse(BaseModel):
    saved: list[str]
    skipped: list[str]
    reindex_triggered: bool


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


# ─── GET /libraries ──────────────────────────────────────────────────────────
@router.get("/libraries")
async def list_libraries() -> dict[str, Any]:
    """Rebuild the registry fresh and return the public records (root_path
    stripped), ordered core, plugin, removable."""
    libs = await build_libraries()
    return {"libraries": [lib.public() for lib in libs]}


# ─── GET /browse ─────────────────────────────────────────────────────────────
@router.get("/browse")
async def browse(
    library_id: str = Query(...),
    path: str = Query(""),
    device_id: Optional[str] = Query(None, min_length=3, max_length=64),
) -> BrowseResponse:
    """One directory level inside a library, sorted dirs-first then name. Every
    entry's realpath is re-checked inside the root (drops symlinks that escape)
    and secret-shaped names are filtered. (The per-file editor-lock join is
    gone with the office engines — the homegrown editors don't lock.)

    ``device_id`` is optional and only affects ``writable`` / ``blocked_reason``
    — reading is never blocked, so a blocked device can still SEE the folder
    and is told why it can't change it."""
    lib = await _resolve_library(library_id)
    root = lib.root_path
    target = safe_join(root, path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="directory not found")

    rel = target.relative_to(root).as_posix()
    rel = "" if rel == "." else rel
    breadcrumb = [seg for seg in rel.split("/") if seg] if rel else []

    entries: list[BrowseEntry] = []
    try:
        children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"cannot list directory: {e}")

    for entry in children:
        # Symlink guard — drop anything whose realpath escapes the root.
        real = entry.resolve(strict=False)
        try:
            real.relative_to(root)
        except ValueError:
            continue
        if is_sensitive_name(entry.name):
            continue
        try:
            is_dir = entry.is_dir()
            st = entry.stat()
        except OSError:
            continue
        erel = entry.relative_to(root).as_posix()
        entries.append(
            BrowseEntry(
                name=entry.name,
                rel=erel,
                is_dir=is_dir,
                size=None if is_dir else st.st_size,
                mtime=st.st_mtime,
                kind=_entry_kind(entry, is_dir),
                locked_by=None,
            )
        )

    blocked_reason = await _block_status(device_id)
    return BrowseResponse(
        library_id=lib.id,
        path=rel,
        editable=lib.editable,
        importable=lib.importable,
        doc_editing=lib.doc_editing,
        breadcrumb=breadcrumb,
        entries=entries,
        writable=blocked_reason is None,
        blocked_reason=blocked_reason,
    )


# ─── GET /download ───────────────────────────────────────────────────────────
@router.get("/download")
async def download(
    request: Request,
    library_id: str = Query(...),
    path: str = Query(...),
):
    """Download a file (as an attachment) or a directory (server-built zip with
    a member cap). Audio uses ``serve_audio_range`` (Range/206); everything else
    is a plain attachment ``FileResponse``."""
    lib = await _resolve_library(library_id)
    root = lib.root_path
    target = safe_join(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="not found")
    if is_sensitive_name(target.name):
        raise HTTPException(status_code=404, detail="not found")

    if target.is_dir():
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
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for child in target.rglob("*"):
            if child.is_symlink():
                continue
            real = child.resolve(strict=False)
            try:
                real.relative_to(root)
            except ValueError:
                continue
            if is_sensitive_name(child.name):
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
@router.post("/upload", response_model=UploadResponse)
async def upload(
    request: Request,
    library_id: str = Form(...),
    path: str = Form(""),
    device_id: str = Form(..., min_length=3, max_length=64),
    files: list[UploadFile] = File(...),
) -> UploadResponse:
    """Upload files into the currently-browsed directory. Open (daily tier);
    ``403`` when the calling device is blocked. Rejected unless the library is
    editable. Each name is sanitized to a bare basename, deduped, and the
    deduped target re-containment-checked before write."""
    await _assert_can_write(device_id)
    lib = await _resolve_library(library_id)
    if not lib.editable:
        raise HTTPException(status_code=403, detail="library is not editable")
    root = lib.root_path
    dest = safe_join(root, path)
    if not dest.exists() or not dest.is_dir():
        raise HTTPException(status_code=404, detail="destination directory not found")

    saved: list[str] = []
    skipped: list[str] = []
    for up in files:
        raw = up.filename or "upload"
        name = _safe_basename(raw)
        if not name or name in (".", "..") or is_sensitive_name(name):
            skipped.append(f"{raw}: bad filename")
            continue
        data = await up.read()
        cand = _unique_path(dest, name)
        try:
            cand.resolve(strict=False).relative_to(root)
        except ValueError:
            skipped.append(f"{name}: refused (escapes library root)")
            continue
        try:
            cand.write_bytes(data)
        except OSError as e:
            skipped.append(f"{name}: write failed ({e})")
            continue
        saved.append(cand.name)

    if not saved:
        raise HTTPException(
            status_code=400,
            detail="no files saved" + (f"; {skipped}" if skipped else ""),
        )

    reindex_triggered = False
    if lib.reindex_kind in INDEXED_KINDS:
        reindex_triggered = await _trigger_reindex(lib.reindex_kind, request)
    return UploadResponse(saved=saved, skipped=skipped, reindex_triggered=reindex_triggered)


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
            elif target.is_dir():
                if not req.recursive:
                    failed.append(f"{rel}: is a directory (recursive not set)")
                    continue
                budget = [_MAX_TREE_MEMBERS]
                _confined_rmtree(target, root, budget)
                target.rmdir()
                deleted.append(target.relative_to(root).as_posix())
            else:
                failed.append(f"{rel}: not found")
        except HTTPException:
            failed.append(f"{rel}: exceeded member cap")
        except OSError as e:
            failed.append(f"{rel}: {e}")

    reindex_triggered = False
    if deleted and lib.reindex_kind in INDEXED_KINDS:
        reindex_triggered = await _trigger_reindex(lib.reindex_kind, request)
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
@router.post("/import", response_model=ImportResponse)
async def import_media(request: Request, req: ImportRequest) -> ImportResponse:
    """Copy a file/dir from a removable source into an importable library.
    Open (daily tier); ``403`` when the calling device is blocked. Server-side
    copy (the browser never streams the bytes), member+byte capped, and the
    source side never follows a symlink out of the mount."""
    await _assert_can_write(req.device_id)
    source = await _resolve_library(req.source_library_id)
    target = await _resolve_library(req.target_library_id)
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

    reindex_triggered = False
    if target.reindex_kind in INDEXED_KINDS:
        reindex_triggered = await _trigger_reindex(target.reindex_kind, request)
    return ImportResponse(copied=copied, skipped=skipped, reindex_triggered=reindex_triggered)


# ─── POST /move ──────────────────────────────────────────────────────────────
@router.post("/move", response_model=MoveResponse)
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
    source = await _resolve_library(req.source_library_id)
    target = await _resolve_library(req.target_library_id)
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
        except OSError as e:
            failed.append(f"{rel}: {e}")

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
    for child in sorted(src_dir.iterdir(), key=lambda p: p.name.lower()):
        if is_sensitive_name(child.name):
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
