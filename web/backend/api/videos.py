"""Videos web API — the dashboard's ``/api/videos`` surface.

Videos are discovered live from the same media-library registry the Files
tab uses (:mod:`files_security`): every core / plugin / removable root is
walked (bounded) for video containers, so any video dropped into any Files
folder shows up — no DB index. Identity is ``(library_id, rel_path)``,
which is also the key for the ``video_positions`` resume store (V005).

Endpoints:

* ``GET /list`` — bounded recursive walk of every library for video files.
* ``GET /stream`` — Range/206 playback via the shared ``audio_serve``
  machinery with video MIME types (``.mkv`` rides as x-matroska; Chromium
  demuxes Matroska, other browsers fall back to save-to-device).
* ``GET /poster`` — one ffmpeg-extracted frame per video, cached under
  ``video_posters_dir`` with a ``.none`` sentinel (cover-art pattern).
* ``GET /position`` / ``POST /position`` / ``DELETE /position`` — per-
  (device × person) resume rows, mirroring the podcasts store; saves fire
  ``video_positions_changed`` NOTIFY → ``video_positions.changed`` WS event.
* ``GET /recent`` — newest position rows for a device, existence-checked
  against the current registry (a row for an ejected USB drive is skipped,
  not deleted — the drive may come back).

Browse/serve security mirrors ``files.py``: the client only ever names a
``library_id`` + relative path, every path passes ``safe_join``, walked
entries are realpath-checked inside their root, and secret-shaped names are
filtered. File-content endpoints are admin-read-gated (cookie is enough for
GETs, so plain ``<video src>`` / ``<img src>`` work); the position store is
open like the podcasts one — it holds only rel paths and timestamps.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any, Optional

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi.admin_auth import require_admin_read
from domovoi.config import settings as core_settings
from web.backend.api.audio_serve import (
    VIDEO_CONTENT_TYPES,
    VIDEO_EXTENSIONS,
    safe_download_name,
    serve_audio_range,
)
from web.backend.api.files_security import (
    MediaLibrary,
    build_libraries,
    is_sensitive_name,
    safe_join,
)
from web.backend.api.media_walk import walk_library_files
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/videos", tags=["videos"])

# Walk bound — bounded fan-out per library (media_walk caps dirs too).
_MAX_VIDEOS_PER_LIBRARY = 2000

# ffmpeg poster extraction: frame size + subprocess wall-clock cap.
_POSTER_WIDTH = 480
_FFMPEG_TIMEOUT_SEC = 20.0


# ─── Registry resolution (files.py pattern) ──────────────────────────────────
async def _resolve_library(library_id: str) -> MediaLibrary:
    reg = {lib.id: lib for lib in await build_libraries()}
    lib = reg.get(library_id)
    if lib is not None:
        return lib
    if library_id.startswith("removable:"):
        raise HTTPException(status_code=410, detail="drive no longer present")
    raise HTTPException(status_code=404, detail=f"unknown library {library_id!r}")


def _resolve_video(lib: MediaLibrary, path: str) -> Path:
    """Containment-checked absolute path of one video file, or 404/400."""
    target = safe_join(lib.root_path, path)
    if target.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="not a video file")
    if is_sensitive_name(target.name):
        raise HTTPException(status_code=404, detail="video not found")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="video not found")
    return target


# ─── GET /list ───────────────────────────────────────────────────────────────
@router.get("/list", dependencies=[Depends(require_admin_read)])
async def list_videos() -> dict[str, Any]:
    """Every video across every present library. Bounded per library; the
    walk runs in worker threads (one per library, gathered)."""
    libs = await build_libraries()
    results = await asyncio.gather(
        *(
            anyio.to_thread.run_sync(
                walk_library_files, lib, VIDEO_EXTENSIONS, _MAX_VIDEOS_PER_LIBRARY
            )
            for lib in libs
        ),
        return_exceptions=True,
    )
    videos: list[dict[str, Any]] = []
    for lib, res in zip(libs, results):
        if isinstance(res, BaseException):
            log.warning("videos: walk failed for %s: %s", lib.id, res)
            continue
        videos.extend(res)
    videos.sort(key=lambda v: (v["library_id"], v["rel"].lower()))
    return {"videos": videos}


# ─── GET /stream ─────────────────────────────────────────────────────────────
@router.get("/stream", dependencies=[Depends(require_admin_read)])
async def stream(
    request: Request,
    library_id: str = Query(...),
    path: str = Query(...),
    download: bool = Query(False),
):
    """Range/206 playback (or attachment with ``?download=1``) of one video."""
    lib = await _resolve_library(library_id)
    target = _resolve_video(lib, path)
    name = safe_download_name(target.name, fallback="video") if download else None
    return serve_audio_range(
        target,
        request,
        download_name=name,
        content_type=VIDEO_CONTENT_TYPES[target.suffix.lower()],
    )


# ─── GET /poster ─────────────────────────────────────────────────────────────
def _poster_cache_key(library_id: str, rel: str, st_size: int, st_mtime_ns: int) -> str:
    h = hashlib.sha1(f"{library_id}|{rel}|{st_size}|{st_mtime_ns}".encode()).hexdigest()
    return h


async def _ffprobe_duration(path: Path) -> float | None:
    """Container duration in seconds via ffprobe. Best-effort → None."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=_FFMPEG_TIMEOUT_SEC)
        return float(out.decode().strip())
    except (FileNotFoundError, asyncio.TimeoutError, OSError, ValueError):
        return None


async def _extract_poster(src: Path, dest: Path) -> bool:
    """One scaled poster frame via ffmpeg. Seeks ~10% in (min 1 s, max 90 s)
    so the frame isn't a black lead-in; retries at 0 s for very short files."""
    duration = await _ffprobe_duration(src)
    seek = min(max((duration or 0.0) * 0.10, 1.0), 90.0)
    for ss in (seek, 0.0):
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-v", "quiet", "-ss", f"{ss:.2f}", "-i", str(src),
                "-frames:v", "1", "-vf", f"scale={_POSTER_WIDTH}:-2",
                "-q:v", "4", "-y", str(dest),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=_FFMPEG_TIMEOUT_SEC)
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            return False
        if dest.is_file() and dest.stat().st_size > 0:
            return True
        dest.unlink(missing_ok=True)
    return False


@router.get("/poster", dependencies=[Depends(require_admin_read)])
async def poster(
    library_id: str = Query(...),
    path: str = Query(...),
):
    """Cached poster frame for one video. 204 when extraction isn't possible
    (missing ffmpeg, unreadable file) — the client shows a placeholder tile.
    Cache key includes size+mtime so an edited file re-extracts."""
    lib = await _resolve_library(library_id)
    target = _resolve_video(lib, path)
    st = target.stat()
    cache_dir = Path(core_settings.video_posters_dir).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _poster_cache_key(library_id, path, st.st_size, st.st_mtime_ns)
    jpg = cache_dir / f"{key}.jpg"
    sentinel = cache_dir / f"{key}.none"

    headers = {"Cache-Control": "public, max-age=604800"}
    if jpg.is_file():
        return FileResponse(jpg, media_type="image/jpeg", headers=headers)
    if sentinel.is_file():
        return Response(status_code=204, headers=headers)

    if await _extract_poster(target, jpg):
        return FileResponse(jpg, media_type="image/jpeg", headers=headers)
    sentinel.touch()
    return Response(status_code=204, headers=headers)


# ─── Resume positions (podcasts-store pattern, keyed by library+rel) ─────────
class PositionSave(BaseModel):
    library_id: str
    path: str
    device_id: str
    person_id: Optional[int] = None
    position_sec: int = Field(..., ge=0)
    duration_sec: Optional[int] = None
    title: Optional[str] = None


class PositionClear(BaseModel):
    library_id: str
    path: str
    device_id: str
    person_id: Optional[int] = None


def _person_clause(person_id: Optional[int]) -> str:
    return "person_id = :person" if person_id is not None else "person_id IS NULL"


@router.get("/position")
async def get_position(
    library_id: str = Query(...),
    path: str = Query(...),
    device_id: str = Query(...),
    person_id: Optional[int] = Query(None),
) -> dict[str, Any]:
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    f"""
                    SELECT position_sec, duration_sec FROM video_positions
                     WHERE library_id = :lib AND rel_path = :rel
                       AND device_id = :dev AND {_person_clause(person_id)}
                    """
                ),
                {"lib": library_id, "rel": path, "dev": device_id, "person": person_id},
            )
        ).first()
    if row is None:
        return {"position_sec": 0, "duration_sec": None}
    return {"position_sec": row[0], "duration_sec": row[1]}


@router.post("/position")
async def save_position(body: PositionSave) -> dict[str, bool]:
    """Upsert one resume row. The two ON CONFLICT targets mirror the partial
    unique indexes (person vs anon). Fires the NOTIFY that becomes the
    ``video_positions.changed`` WS event."""
    conflict = (
        "(library_id, rel_path, device_id, person_id) WHERE person_id IS NOT NULL"
        if body.person_id is not None
        else "(library_id, rel_path, device_id) WHERE person_id IS NULL"
    )
    async with session_scope() as s:
        await s.execute(
            text(
                f"""
                INSERT INTO video_positions
                    (library_id, rel_path, device_id, person_id,
                     position_sec, duration_sec, title, updated_at)
                VALUES (:lib, :rel, :dev, :person, :pos, :dur, :title, now())
                ON CONFLICT {conflict}
                DO UPDATE SET position_sec = EXCLUDED.position_sec,
                              duration_sec = COALESCE(EXCLUDED.duration_sec,
                                                      video_positions.duration_sec),
                              title = COALESCE(EXCLUDED.title, video_positions.title),
                              updated_at = now()
                """
            ),
            {
                "lib": body.library_id, "rel": body.path, "dev": body.device_id,
                "person": body.person_id, "pos": body.position_sec,
                "dur": body.duration_sec, "title": body.title,
            },
        )
        await s.execute(
            text("SELECT pg_notify('video_positions_changed', :p)"),
            {"p": f"{body.library_id}:{body.path}"},
        )
    return {"saved": True}


@router.delete("/position")
async def clear_position(body: PositionClear) -> dict[str, bool]:
    """Drop one resume row ("remove from recently played")."""
    async with session_scope() as s:
        await s.execute(
            text(
                f"""
                DELETE FROM video_positions
                 WHERE library_id = :lib AND rel_path = :rel
                   AND device_id = :dev AND {_person_clause(body.person_id)}
                """
            ),
            {"lib": body.library_id, "rel": body.path, "dev": body.device_id,
             "person": body.person_id},
        )
        await s.execute(
            text("SELECT pg_notify('video_positions_changed', :p)"),
            {"p": f"{body.library_id}:{body.path}"},
        )
    return {"cleared": True}


@router.get("/recent")
async def recent(
    device_id: str = Query(...),
    person_id: Optional[int] = Query(None),
    limit: int = Query(12, ge=1, le=50),
) -> dict[str, Any]:
    """Newest position rows for this device (+person), existence-checked
    against the current registry. A row whose library is absent (ejected
    drive) or whose file is gone is skipped, never deleted — the drive may
    come back. Rows fetched with headroom so filtering still fills `limit`."""
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    f"""
                    SELECT library_id, rel_path, position_sec, duration_sec,
                           title, updated_at
                      FROM video_positions
                     WHERE device_id = :dev AND {_person_clause(person_id)}
                     ORDER BY updated_at DESC
                     LIMIT :lim
                    """
                ),
                {"dev": device_id, "person": person_id, "lim": limit * 3},
            )
        ).all()

    reg = {lib.id: lib for lib in await build_libraries()}
    out: list[dict[str, Any]] = []
    for lib_id, rel, pos, dur, title, updated in rows:
        lib = reg.get(lib_id)
        if lib is None:
            continue
        try:
            target = safe_join(lib.root_path, rel)
        except HTTPException:
            continue
        if not target.is_file():
            continue
        out.append(
            {
                "library_id": lib_id,
                "library_label": lib.label,
                "rel": rel,
                "name": target.name,
                "position_sec": pos,
                "duration_sec": dur,
                "title": title,
                "updated_at": updated.isoformat() if updated else None,
            }
        )
        if len(out) >= limit:
            break
    return {"recent": out}
