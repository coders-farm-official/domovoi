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
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from starlette.background import BackgroundTask

from domovoi.config import settings as core_settings
from domovoi import spoken_audio as sa
from web.backend.api.audio_serve import (
    AUDIO_EXTENSIONS,
    attachment_headers,
    safe_download_name,
    serve_audio_range,
)
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audiobooks", tags=["audiobooks"])


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
@router.get("")
@router.get("/")
async def list_books() -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT id, title, author, narrator, artwork, chapters,
                           is_folder, duration_sec, added_via, added_at, file_path
                      FROM audiobooks
                     ORDER BY LOWER(title)
                    """
                )
            )
        ).mappings().all()
    return [_book_row(r) for r in rows]


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


@router.post("/reindex")
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


@router.post("/{book_id}/position")
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
