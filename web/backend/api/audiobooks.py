"""Audiobooks web API.

Browser surface for the ``audiobooks`` table. Playback is the shared browser
mini-player (player.jsx) and the Android app; this router provides the book
list + chapters + the files a book is made of (with sizes, for saving it to
a phone), Range audio serving (a folder book file by file), the cover read
from the book's own files, a reindex trigger, the per-(device × person ×
book) resume-position store, and the read of this person's places in the
book on OTHER devices and rooms ("Continue from the kitchen").

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
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from starlette.background import BackgroundTask

from domovoi.admin_auth import require_device, require_device_read
from domovoi.config import settings as core_settings
from domovoi.models import is_phone_room
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
from web.backend.domovoi_client import auth_forward_headers, post_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audiobooks", tags=["audiobooks"])

# Listening to audiobooks — and re-walking the folder they live in — is an
# ordinary household action, so the writes here take the DEVICE tier: a
# valid ``X-Device-Token`` or an admin Bearer, pre-setup LAN grace kept.
# (The MUSIC library's reindex is admin tier instead, because the core
# route it proxies to is; this one runs in the web process and touches
# nothing outside ``audiobooks_dir``, then asks the core's device-tier MPD
# rescan when it added a book.)
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
                   is_folder, duration_sec, added_via, added_at, file_path,
                   size_bytes, files"""


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


def library_path_for(file_path: str | None) -> str | None:
    """A book's path relative to ``audiobooks_dir`` in POSIX form ("Dune"
    for a folder book, "Dune.m4b" for a file): what the Files API's
    ``core:audiobooks`` library calls it, so a client can delete the book
    through ``POST /api/files/delete``. Lexical first (the indexer stores
    the configured dir joined with the entry name, so no disk is touched
    per row), then resolved; None for a row outside the dir."""
    if not file_path:
        return None
    base = Path(core_settings.audiobooks_dir).expanduser()
    target = Path(file_path)
    for b, t in ((base, target), (_audiobooks_dir(), target.resolve(strict=False))):
        try:
            rel = t.relative_to(b)
        except ValueError:
            continue
        posix = rel.as_posix()
        return posix if posix not in ("", ".") else None
    return None


def _files_for(d: dict[str, Any], fp: str | None) -> list[dict[str, Any]]:
    """The files a book is made of, ``[{file, title, start_sec,
    duration_sec, size_bytes}]`` in play order. The indexer stores them
    (V032); a row it hasn't seen since is answered from its chapters (a
    folder book's chapters name their files), sizes unknown."""
    stored = d.get("files")
    if isinstance(stored, list) and stored:
        return [
            {
                "file": f.get("file"),
                "title": f.get("title"),
                "start_sec": int(f.get("start_sec") or 0),
                "duration_sec": f.get("duration_sec"),
                "size_bytes": f.get("size_bytes"),
            }
            for f in stored if isinstance(f, dict) and f.get("file")
        ]
    chapters = d.get("chapters") if isinstance(d.get("chapters"), list) else []
    if d.get("is_folder"):
        named = [c for c in chapters if isinstance(c, dict) and c.get("file")]
        out = []
        for i, c in enumerate(named):
            start = int(c.get("start_sec") or 0)
            nxt = named[i + 1].get("start_sec") if i + 1 < len(named) else d.get("duration_sec")
            dur = int(nxt) - start if isinstance(nxt, (int, float)) and nxt > start else None
            out.append({"file": c["file"], "title": c.get("title") or c["file"],
                        "start_sec": start, "duration_sec": dur, "size_bytes": None})
        return out
    if not fp:
        return []
    return [{"file": Path(fp).name, "title": d.get("title"), "start_sec": 0,
             "duration_sec": d.get("duration_sec"), "size_bytes": d.get("size_bytes")}]


def cover_path(book_id: int) -> str:
    return f"/api/audiobooks/{int(book_id)}/cover"


def _book_row(r: Any) -> dict[str, Any]:
    """Row → response dict. Swaps the private server path for ``file_ext``
    (what save-to-device clients name the file; folder books download as
    zip, so their ext is None) and ``library_path`` (its path inside the
    audiobooks library). ``artwork`` in the table only says where the
    indexer found a cover; the response carries ``cover_url`` (null when
    there is none) and, for clients built before it, the same URL as
    ``artwork``, which they already draw."""
    d = dict(r)
    fp = d.pop("file_path", None)
    d["file_ext"] = None if d.get("is_folder") else (Path(fp).suffix.lower() if fp else None)
    d["library_path"] = library_path_for(fp)
    d["files"] = _files_for(d, fp)
    if d.get("size_bytes") is None:
        sizes = [f["size_bytes"] for f in d["files"]]
        if sizes and all(isinstance(x, int) for x in sizes):
            d["size_bytes"] = sum(sizes)
    d["size_bytes"] = int(d["size_bytes"]) if d.get("size_bytes") is not None else None
    cover = cover_path(d["id"]) if d.get("artwork") else None
    d["cover_url"] = cover
    d["artwork"] = cover
    return d


@router.get("/{book_id}")
async def get_book(book_id: int) -> dict[str, Any]:
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    """
                    SELECT id, title, author, narrator, artwork, chapters,
                           is_folder, duration_sec, added_via, added_at, file_path,
                           size_bytes, files
                      FROM audiobooks WHERE id = :id
                    """
                ),
                {"id": book_id},
            )
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"audiobook {book_id} not found")
    return _book_row(row)


async def index_and_tell_players(request: Request, *, removed: tuple = ()) -> dict[str, int]:
    """Index ``audiobooks_dir`` in this process, then, when a book was
    added, ask the core to have every room's player read the library: the
    rooms' MPDs live behind the core, and a Windows or network folder never
    tells them about new files, so a new book would not play until "rescan
    my library" (F-V023). The core route is the device-tier MPD rescan the
    music upload uses. Best-effort: the books are indexed either way.
    ``removed``: the paths a Files delete just took away, pruned even when
    the folder is left empty (the indexer's unmounted-share guard)."""
    from domovoi.workers.audiobook_indexer import index_audiobooks_dir

    counts = await (index_audiobooks_dir(removed=removed) if removed else index_audiobooks_dir())
    if counts.get("inserted"):
        status, _ = await post_admin(
            "/v1/admin/music/mpd-rescan", headers=auth_forward_headers(request)
        )
        if status != 200:
            log.warning(
                "audiobook reindex: added %d book(s) but the rooms' players "
                "couldn't be asked to read them (core status=%s)",
                counts["inserted"], status,
            )
    return counts


@router.post("/reindex", dependencies=DEVICE)
async def reindex(request: Request) -> dict[str, int]:
    """Re-walk ``audiobooks_dir``. Runs the indexer directly in the web
    process against the shared DB (best-effort — ffprobe for
    chapters where present, domovoi.media_tags for tags), then asks the
    core to have the rooms' players read any book it added."""
    try:
        return await index_and_tell_players(request)
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
        if Path(name).suffix.lower() not in AUDIO_EXTENSIONS:
            # A folder book's files are its chapters; its cover.jpg (or
            # anything else beside them) is not this route's to serve.
            raise HTTPException(status_code=404, detail="not one of this book's files")
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


# ─── Cover, read from the book's own files ──────────────────────────────
# The cover-art decision (2026-10-02) applied to books: the picture built
# into the book (the file, or a folder book's first chapter file), else an
# image beside it (a folder book's cover / folder / front / album image; a
# single-file book's image of the same name, never a cover.jpg loose in
# AUDIOBOOKS_DIR). Read on request, no online lookup, nothing stored: the
# music cover route's ETag + Cache-Control and its in-memory memo of each
# file version's picture do the caching (web/backend/api/music.py).
def _probe_book_cover(file_path: str, is_folder: bool) -> tuple[str, Path | None, Path | None, int, str | None] | None:
    """``(ETag, file whose embedded picture to read, cover image, image
    size, its content type)`` for a book, from stats and one directory listing; None when the
    book is gone from disk or sits outside AUDIOBOOKS_DIR. Blocking."""
    import hashlib

    from web.backend.api import music
    from domovoi.workers.audiobook_indexer import chapter_files, find_cover_image

    root = _audiobooks_dir()
    book = Path(file_path)
    try:
        book.resolve(strict=False).relative_to(root)
    except ValueError:
        return None
    try:
        audio = (chapter_files(book) or [None])[0] if is_folder else book
    except OSError:
        return None
    if audio is not None:
        try:
            audio.resolve(strict=False).relative_to(root)
        except ValueError:
            audio = None
    image = find_cover_image(book, is_folder, root)
    h = hashlib.sha256(music._COVER_ETAG_VERSION + b"\0book")
    image_size, image_mime = 0, None
    if audio is not None:
        try:
            st = audio.stat()
        except OSError:
            audio = None
        else:
            h.update(b"\0" + os.fsencode(str(audio)) + b"\0%d\0%d" % (st.st_mtime_ns, st.st_size))
    if image is not None:
        try:
            st = image.stat()
            with image.open("rb") as f:
                image_mime = music._sniff_image_type(f.read(16))
        except OSError:
            image_mime = None
        if image_mime is None:
            image = None
        else:
            image_size = st.st_size
            h.update(b"\1" + os.fsencode(str(image)) + b"\0%d\0%d" % (st.st_mtime_ns, st.st_size))
    if audio is None and image is None:
        return None
    return f'"{h.hexdigest()[:32]}"', audio, image, image_size, image_mime


@router.get("/{book_id}/cover")
async def book_cover(book_id: int, request: Request):
    """A book's cover, read from its files on request: the picture built
    into it (an .m4b's ``covr``, ID3 APIC, FLAC / Vorbis pictures; a folder
    book's first chapter file), else an image beside it (a folder book's
    cover / folder / front / album .jpg / .jpeg / .png / .webp; a
    single-file book's image of the same name). Served as-is with its real
    content type, a strong ``ETag`` and ``Cache-Control``; ``304`` when
    ``If-None-Match`` still matches; ``404`` when there is none.

    Open, like the book list it decorates and the audio route (an
    ``<img>`` and the phone's lock screen can't send a credential); the id
    resolves the path from the database, containment-checked inside
    AUDIOBOOKS_DIR. Nothing is stored on disk; no online lookup."""
    import anyio

    from web.backend.api import music

    async with session_scope() as s:
        row = (
            await s.execute(
                text("SELECT file_path, is_folder FROM audiobooks WHERE id = :id"),
                {"id": book_id},
            )
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"audiobook {book_id} not found")
    probe = await anyio.to_thread.run_sync(_probe_book_cover, row["file_path"], bool(row["is_folder"]))
    if probe is None:
        return music._no_cover()
    etag, audio, image, image_size, image_mime = probe
    headers = {"ETag": etag, "Cache-Control": music._COVER_CACHE_CONTROL}
    if music._etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    if audio is not None:
        embedded = await music._embedded_cover(audio, etag)
        if embedded is not None:
            data, mime = embedded
            return Response(content=data, media_type=mime, headers=headers)
    if image is not None and image_size > 0:
        return StreamingResponse(
            music._iter_file(image, 0, image_size - 1),
            media_type=image_mime,
            headers={**headers, "Content-Length": str(image_size)},
        )
    return music._no_cover()


# ─── Resume positions ───────────────────────────────────────────────────
# Client device ids are minted per install with these prefixes (V010); a
# position under any other id was saved by the voice path in a room, where
# the device id IS the room ("default": a turn that named no room, not a
# place anyone can continue from).
_CLIENT_ID_LABELS = (("browser-", "browser"), ("android-", "phone"))


@router.get("/{book_id}/positions", dependencies=[Depends(require_device_read)])
async def other_positions(
    book_id: int,
    device_id: str = Query(..., min_length=1, max_length=200,
                           description="the asking device: its own place is left out"),
    person_id: Optional[int] = Query(None, description="the listener; none = the unidentified one"),
) -> list[dict[str, Any]]:
    """This listener's places in the book on OTHER devices and rooms, newest
    first: ``[{device_label, room, position_sec, updated_at}]``. ``room`` is
    the room id for a place a room's player saved ("play my book" in the
    kitchen), null for a phone or a browser (``device_label`` is then its
    name from the device list). Only rows for exactly this ``person_id`` —
    the unidentified listener's when it is absent, never another
    person's — and only places past the start. What the app's "Continue
    from the kitchen (2 h 13 m)" offers. Device tier."""
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT p.device_id, p.position_sec, p.updated_at, d.name AS device_name
                      FROM playback_positions p
                      LEFT JOIN devices d ON d.device_id = p.device_id
                     WHERE p.item_type = :it AND p.item_id = :id
                       AND p.device_id <> :dev
                       AND p.person_id IS NOT DISTINCT FROM :pid
                       AND p.position_sec > 0
                     ORDER BY p.updated_at DESC
                     LIMIT 50
                    """
                ),
                {"it": sa.ITEM_AUDIOBOOK, "id": book_id, "dev": device_id, "pid": person_id},
            )
        ).mappings().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        dev = str(r["device_id"])
        room: Optional[str] = None
        if r["device_name"]:
            label = str(r["device_name"])
        else:
            label = next((name for prefix, name in _CLIENT_ID_LABELS if dev.startswith(prefix)), None)
            if label is None:
                if dev == "default" or is_phone_room(dev):
                    continue
                room = label = dev
        out.append({
            "device_label": label,
            "room": room,
            "position_sec": int(r["position_sec"]),
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] is not None else None,
        })
    return out


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
