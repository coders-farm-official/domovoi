"""Audiobooks web API.

Browser surface for the ``audiobooks`` table. Playback is the shared browser
mini-player (player.jsx); this router provides the book list + chapters,
Range audio serving for single-file books, a reindex trigger, and the
per-(device × person × book) resume-position store.

Served paths are containment-checked inside ``audiobooks_dir`` (music.py
pattern). Single-file books (.m4b) stream directly; per-chapter FOLDER books
serve a named chapter file (also containment-checked to sit inside the book
folder inside audiobooks_dir).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from starlette.background import BackgroundTask

from domovoi.admin_auth import require_device
from domovoi.config import settings as core_settings
from domovoi import spoken_audio as sa
from web.backend.api.audio_serve import (
    AUDIO_EXTENSIONS,
    attachment_headers,
    safe_download_name,
    serve_audio_range,
)
from web.backend.api import paging
from web.backend.api.paging import SortKey
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audiobooks", tags=["audiobooks"])

# Listening to audiobooks — and re-walking the folder they live in — is an
# ordinary household action, so the writes here take the DEVICE tier: a
# valid ``X-Device-Token`` or an admin Bearer, pre-setup LAN grace kept.
# (The MUSIC library's reindex is admin tier instead, because the core
# route it proxies to is; this one runs in the web process and touches
# nothing outside ``audiobooks_dir``.)
DEVICE = [Depends(require_device)]


def _audiobooks_dir() -> Path:
    return Path(core_settings.audiobooks_dir).expanduser().resolve(strict=False)


def _safe_within(target: Path) -> Path:
    base = _audiobooks_dir()
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(base)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"refusing path {str(target)!r}: not inside AUDIOBOOKS_DIR",
        )
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="audio file missing on disk")
    return resolved


class PositionSave(BaseModel):
    device_id: str
    person_id: Optional[int] = None
    position_sec: int
    speed: Optional[float] = None


# ─── Books ──────────────────────────────────────────────────────────────
AudiobookSort = Literal["title", "author", "newest"]
# Each ends in ``id``: unique, so a cursor names one row (paging.py).
_BOOK_SORT_KEYS: dict[str, list[SortKey]] = {
    "title": [SortKey("LOWER(title)"), SortKey("id", kind="int")],
    "author": [SortKey("LOWER(author)", nulls_last=True), SortKey("LOWER(title)"),
               SortKey("id", kind="int")],
    "newest": [SortKey("added_at", desc=True, kind="ts"), SortKey("id", desc=True, kind="int")],
}
_BOOK_COLUMNS = """id, title, author, narrator, artwork, chapters,
                   is_folder, duration_sec, added_via, added_at, file_path"""


@router.get("")
@router.get("/")
async def list_books(
    q: Optional[str] = Query(None, max_length=200,
                             description="Case-insensitive substring of title, author or narrator"),
    sort: AudiobookSort = Query("title", description="title (default) · author · newest"),
    limit: Optional[int] = paging.limit_query(),
    cursor: Optional[str] = paging.cursor_query(),
    offset: Optional[int] = paging.offset_query(),
) -> Any:
    """The indexed books, sorted and searched here.

    Paged when ``limit`` / ``cursor`` / ``offset`` is sent:
    ``{items, total, limit, has_more, next_cursor}`` (web/backend/api/
    paging.py). With none of them — an old client, the installed Android
    app — the bare list it was built for, every match, as before."""
    keys = _BOOK_SORT_KEYS[sort]
    params: dict[str, Any] = {}
    where_parts: list[str] = []
    if q and q.strip():
        where_parts.append("(title ILIKE :q OR author ILIKE :q OR narrator ILIKE :q)")
        params["q"] = f"%{q.strip()}%"
    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    if not paging.wants_page(limit, cursor, offset):
        async with session_scope() as s:
            rows = (
                await s.execute(
                    text(f"SELECT {_BOOK_COLUMNS} FROM audiobooks {where}"
                         f" ORDER BY {paging.order_sql(keys)}"),
                    params,
                )
            ).mappings().all()
        return [_book_row(r) for r in rows]

    lim = limit or paging.DEFAULT_LIMIT
    page_where = where
    if cursor is not None:
        after = paging.keyset_where(keys, cursor, sort, params)
        page_where = f"{where} AND {after}" if where else f"WHERE {after}"
    params.update({"limit": lim + 1, "offset": 0 if cursor is not None else (offset or 0)})
    async with session_scope() as s:
        total = int((await s.execute(
            text(f"SELECT COUNT(*) FROM audiobooks {where}"), params)).scalar_one())
        rows = (
            await s.execute(
                text(
                    f"SELECT {_BOOK_COLUMNS}, {paging.key_columns(keys)} FROM audiobooks"
                    f" {page_where} ORDER BY {paging.order_sql(keys)}"
                    " LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        ).mappings().all()
    page, has_more, nxt = paging.db_page_dicts(rows, keys, limit=lim, sort=sort)
    return {
        "items": [_book_row(r) for r in page],
        **paging.page_fields(total=total, limit=lim, has_more=has_more, next_cursor=nxt,
                             offset=None if cursor is not None else (offset or 0)),
    }


def _book_row(r: Any) -> dict[str, Any]:
    """Row → response dict. Swaps the private server path for ``file_ext``
    (what save-to-device clients name the file; folder books download as
    zip, so their ext is None)."""
    d = dict(r)
    fp = d.pop("file_path", None)
    d["file_ext"] = None if d.get("is_folder") else (Path(fp).suffix.lower() if fp else None)
    return d


@router.get("/{book_id}")
async def get_book(book_id: int) -> dict[str, Any]:
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    """
                    SELECT id, title, author, narrator, artwork, chapters,
                           is_folder, duration_sec, added_via, added_at, file_path
                      FROM audiobooks WHERE id = :id
                    """
                ),
                {"id": book_id},
            )
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"audiobook {book_id} not found")
    return _book_row(row)


@router.post("/reindex", dependencies=DEVICE)
async def reindex() -> dict[str, int]:
    """Re-walk ``audiobooks_dir``. Runs the indexer directly in the web
    process against the shared DB (best-effort — ffprobe/mutagen for
    chapters where present)."""
    from domovoi.workers.audiobook_indexer import index_audiobooks_dir

    try:
        return await index_audiobooks_dir()
    except Exception as e:
        log.warning("audiobook reindex failed: %s", e)
        raise HTTPException(status_code=502, detail=f"reindex failed: {e}")


# ─── Audio (Range) ──────────────────────────────────────────────────────
@router.get("/{book_id}/audio")
async def book_audio(
    book_id: int,
    request: Request,
    file: Optional[str] = Query(None, description="chapter filename for folder books"),
    download: bool = Query(False, description="serve as attachment (save to device)"),
) -> StreamingResponse:
    """Stream a book's audio. Single-file books ignore ``file``; per-chapter
    FOLDER books require ``file`` (a bare filename from the book's chapter
    list) which is resolved INSIDE the book folder (containment-checked).
    ``?download=1`` marks the response ``attachment`` — a single chapter for
    folder books, the whole book for single-file ones (see also
    ``/{book_id}/download`` which zips a folder book)."""
    async with session_scope() as s:
        row = (
            await s.execute(
                text("SELECT file_path, is_folder, title FROM audiobooks WHERE id = :id"),
                {"id": book_id},
            )
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"audiobook {book_id} not found")

    if row["is_folder"]:
        if not file:
            raise HTTPException(status_code=400, detail="folder book requires ?file=<chapter>")
        # Bare filename only — strip any path component, then resolve inside
        # the book folder and re-check containment against audiobooks_dir.
        name = Path(file.replace("\\", "/")).name
        target = Path(row["file_path"]) / name
    else:
        target = Path(row["file_path"])
    resolved = _safe_within(target)
    dl_name = None
    if download:
        if row["is_folder"]:
            dl_name = resolved.name  # already a bare chapter filename
        else:
            dl_name = (
                safe_download_name(row["title"] or resolved.stem, fallback="audiobook")
                + resolved.suffix.lower()
            )
    return serve_audio_range(resolved, request, download_name=dl_name)


@router.get("/{book_id}/download")
async def download_book(book_id: int) -> FileResponse:
    """Save a whole book to the requesting device. Single-file books come
    back as the file itself (attachment); folder books are zipped (stored,
    not compressed — it's already-compressed audio) into a temp file that a
    background task removes after the response is sent."""
    async with session_scope() as s:
        row = (
            await s.execute(
                text("SELECT file_path, is_folder, title FROM audiobooks WHERE id = :id"),
                {"id": book_id},
            )
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"audiobook {book_id} not found")

    title = safe_download_name(row["title"] or f"audiobook-{book_id}", fallback="audiobook")

    if not row["is_folder"]:
        target = _safe_within(Path(row["file_path"]))
        return FileResponse(
            str(target),
            media_type="application/octet-stream",
            headers=attachment_headers(title + target.suffix.lower()),
        )

    folder = Path(row["file_path"]).resolve(strict=False)
    try:
        folder.relative_to(_audiobooks_dir())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"refusing path {row['file_path']!r}: not inside AUDIOBOOKS_DIR",
        )
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="book folder missing on disk")
    files = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    )
    if not files:
        raise HTTPException(status_code=404, detail="book folder has no audio files")

    import anyio

    def _build_zip() -> str:
        import tempfile
        import zipfile

        tmp = tempfile.NamedTemporaryFile(prefix="domovoi-book-", suffix=".zip", delete=False)
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
                for p in files:
                    zf.write(p, arcname=f"{title}/{p.name}")
        except BaseException:
            tmp.close()
            os.unlink(tmp.name)
            raise
        tmp.close()
        return tmp.name

    zip_path = await anyio.to_thread.run_sync(_build_zip)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        headers=attachment_headers(title + ".zip"),
        background=BackgroundTask(os.unlink, zip_path),
    )


# ─── Resume positions ───────────────────────────────────────────────────
@router.get("/{book_id}/position")
async def get_position(
    book_id: int,
    device_id: str = Query(...),
    person_id: Optional[int] = Query(None),
) -> dict[str, Any]:
    async with session_scope() as s:
        pos = await sa.get_position(
            s, item_type=sa.ITEM_AUDIOBOOK, item_id=book_id,
            device_id=device_id, person_id=person_id,
        )
    return pos or {"position_sec": 0, "speed": 1.0}


@router.post("/{book_id}/position", dependencies=DEVICE)
async def save_position(book_id: int, body: PositionSave) -> dict[str, bool]:
    async with session_scope() as s:
        await sa.upsert_position(
            s, item_type=sa.ITEM_AUDIOBOOK, item_id=book_id,
            device_id=body.device_id, person_id=body.person_id,
            position_sec=body.position_sec, speed=body.speed,
        )
        await s.execute(text("SELECT pg_notify('podcast_positions_changed', :p)"),
                        {"p": f"audiobook:{book_id}"})
    return {"saved": True}
