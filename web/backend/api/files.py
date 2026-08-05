"""Generic multi-library Files API (design §2) — the web dashboard's
``/api/files`` surface.

One router browses/downloads/uploads/deletes/imports across every root the
Files tab exposes (core media dirs, enabled-plugin media libraries, present
removable drives), all resolved server-side by :mod:`files_security`. The
client only ever sends a ``library_id`` + a **relative** path; the absolute
``root_path`` is never serialized. Every route is admin-gated
(``require_admin_read`` for GET, ``require_admin_mutation`` for writes).

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


class ImportRequest(BaseModel):
    source_library_id: str
    source_path: str
    target_library_id: str
    target_path: str = ""


class ImportResponse(BaseModel):
    copied: list[str]
    skipped: list[str]
    reindex_triggered: bool


# ─── GET /libraries ──────────────────────────────────────────────────────────
@router.get("/libraries", dependencies=[Depends(require_admin_read)])
async def list_libraries() -> dict[str, Any]:
    """Rebuild the registry fresh and return the public records (root_path
    stripped), ordered core, plugin, removable."""
    libs = await build_libraries()
    return {"libraries": [lib.public() for lib in libs]}


# ─── GET /browse ─────────────────────────────────────────────────────────────
@router.get("/browse", dependencies=[Depends(require_admin_read)])
async def browse(
    library_id: str = Query(...),
    path: str = Query(""),
) -> BrowseResponse:
    """One directory level inside a library, sorted dirs-first then name. Every
    entry's realpath is re-checked inside the root (drops symlinks that escape)
    and secret-shaped names are filtered. (The per-file editor-lock join is
    gone with the office engines — the homegrown editors don't lock.)"""
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

    return BrowseResponse(
        library_id=lib.id,
        path=rel,
        editable=lib.editable,
        importable=lib.importable,
        doc_editing=lib.doc_editing,
        breadcrumb=breadcrumb,
        entries=entries,
    )


# ─── GET /download ───────────────────────────────────────────────────────────
@router.get("/download", dependencies=[Depends(require_admin_read)])
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
@router.post(
    "/upload", response_model=UploadResponse, dependencies=[Depends(require_admin_mutation)]
)
async def upload(
    request: Request,
    library_id: str = Form(...),
    path: str = Form(""),
    files: list[UploadFile] = File(...),
) -> UploadResponse:
    """Upload files into the currently-browsed directory. Rejected unless the
    library is editable. Each name is sanitized to a bare basename, deduped,
    and the deduped target re-containment-checked before write."""
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
@router.post(
    "/import", response_model=ImportResponse, dependencies=[Depends(require_admin_mutation)]
)
async def import_media(request: Request, req: ImportRequest) -> ImportResponse:
    """Copy a file/dir from a removable source into an importable library.
    Server-side copy (the browser never streams the bytes), member+byte capped,
    and the source side never follows a symlink out of the mount."""
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
