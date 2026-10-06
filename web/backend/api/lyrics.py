"""Lyrics for the library's songs — ``/api/music/.../lyrics`` (V021
``track_lyrics`` and the ``track_lyrics_shown`` view; lyrics contract §11).

Three reads, and nothing else:

* ``GET /api/music/library/{track_id}/lyrics`` — what a player shows for one
  song (:class:`~web.backend.schemas.LyricsDoc`): timed lines when there
  are any, plain text otherwise, or why there is nothing;
* ``GET /api/music/now-playing/{room_id}/lyrics`` — a room's song, where it
  has got to and its lyrics in one call
  (:class:`~web.backend.schemas.RoomLyrics`), for following a room;
* ``GET /api/music/lyrics/status`` — counts and states for the Music page's
  Jobs card (:class:`~web.backend.schemas.LyricsStatus`).

Tier: the household's — ``require_device_read`` (the device token in
``X-Device-Token`` or ``?device_token=``, an admin Bearer, the dashboard
cookie, or the pre-setup grace); a bare request is ``401``. Lyrics are the
household's own copy of copyrighted text for its own songs, so unlike
``/api/music/library``, ``/now-playing`` and ``/cover`` — which stay open
and carry no lyrics — nothing here answers an open page (the kiosk
display), and the realtime ``lyrics`` channel carries counts only
(web/backend/realtime.py). Every answer, the refusals included, is
``Cache-Control: no-store``.

Nothing here writes: the core's workers fill these tables (lyrics_scan,
lyrics_fetch, lyrics_index), and which source is SHOWN — the owner's .lrc,
then timed beats plain, then the song's own tags beat LRCLIB — is decided
by the view, never here. Nothing here logs a lyric, and no error body
carries one.
"""

from __future__ import annotations

import json
import logging
import math
from bisect import bisect_right
from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.routing import APIRoute
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from domovoi.admin_auth import require_device_read
from web.backend.api import music as music_api
from web.backend.db import session_scope
from web.backend.domovoi_client import get_cached_snapshot
from web.backend.schemas import LyricsDoc, LyricsStatus, RoomLyrics

log = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "no-store"}

# The household tier's READ half — the same gate as the household's speech
# and media reads (domovoi/tests/test_route_auth_matrix.py LYRICS_READS).
READ = [Depends(require_device_read)]

# library_tracks.id is an INTEGER: anything past it is no track at all, and
# would otherwise reach Postgres as an out-of-range bind.
_MAX_TRACK_ID = 2_147_483_647


class _NoStoreRoute(APIRoute):
    """Every answer from this router carries ``Cache-Control: no-store`` —
    the 200s and the refusals alike (the gate's 401 / 429, a 404, the 503
    before V021, a 422 for a malformed id): no browser, proxy or
    service-worker cache keeps a copy of a song's words, and none keeps a
    refusal that pairing has since lifted."""

    def get_route_handler(self):  # type: ignore[override]
        handler = super().get_route_handler()

        async def no_store_handler(request: Request) -> Response:
            try:
                response = await handler(request)
            except StarletteHTTPException as e:
                raise HTTPException(
                    status_code=e.status_code,
                    detail=e.detail,
                    headers={**(e.headers or {}), **NO_STORE},
                ) from None
            except RequestValidationError as e:
                response = await request_validation_exception_handler(request, e)
            response.headers["Cache-Control"] = "no-store"
            return response

        return no_store_handler


router = APIRouter(prefix="/api/music", tags=["music"], route_class=_NoStoreRoute)


# ─── V021 present? ────────────────────────────────────────────────────────

# True once this process has seen the V021 tables; never set back (a
# migration is not undone under a running server).
_V021_READY = False

_V021_PRESENT_SQL = text(
    "SELECT to_regclass('public.track_lyrics') IS NOT NULL "
    "AND to_regclass('public.track_lyrics_shown') IS NOT NULL"
)


async def tables_present(session: Any) -> bool:
    """Whether this database has V021 (checked with ``to_regclass``,
    remembered once true). The realtime digest shares it."""
    global _V021_READY
    if _V021_READY:
        return True
    ready = bool((await session.execute(_V021_PRESENT_SQL)).scalar_one())
    if ready:
        _V021_READY = True
    return ready


async def _require_v021(session: Any) -> None:
    """``503`` on a database that has not taken V021 yet (an unmigrated
    test lane, a server mid-upgrade) rather than a 500 about a missing
    relation."""
    if not await tables_present(session):
        raise HTTPException(
            status_code=503,
            detail="lyrics are not set up on this server yet",
            headers=NO_STORE,
        )


def lyric_norm_version() -> int:
    """The lyric index's normalization version
    (``spoken_names.LYRIC_NORM_VERSION``; lyrics contract [M3]): a row's
    search lines are current when they were built from the shown text with
    this version. 1 where the core's lyric search is not in this tree."""
    from domovoi.handlers.shared import spoken_names

    try:
        return int(getattr(spoken_names, "LYRIC_NORM_VERSION", 1))
    except (TypeError, ValueError):
        return 1


# A track_lyrics row whose search lines are NOT current ([M3]): the shown
# text changed (or the normalization did) since the lines were built, or the
# text is gone and the lines are not. The same test as the core's
# lyrics_index catch-up query. Expects :version.
INDEX_PENDING_SQL = (
    "((plain_md5 IS NOT NULL AND (lines_md5 IS DISTINCT FROM plain_md5 "
    "OR lines_version IS DISTINCT FROM :version)) "
    "OR (plain_md5 IS NULL AND lines_md5 IS NOT NULL))"
)


# ─── The core snapshot's lyrics keys ──────────────────────────────────────


def _snapshot_part(snapshot: Any, *path: str) -> dict[str, Any]:
    """``snapshot[path[0]][path[1]]…`` when every step is a dict, else {}
    (the core has not reported, or reported something unexpected)."""
    node: Any = snapshot
    for key in path:
        if not isinstance(node, dict):
            return {}
        node = node.get(key)
    return node if isinstance(node, dict) else {}


def _flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, value)


def _state(part: dict[str, Any]) -> str:
    value = part.get("state")
    return str(value)[:40] if isinstance(value, str) and value else "unknown"


def _iso(value: Any) -> str | None:
    """An ISO timestamp the core reported, as given; anything else None."""
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def _short_code(value: Any) -> str | None:
    """A worker's ``last_error``: a short code by contract (``rate_limited``,
    ``unavailable:<code>``, an exception type) — cut short regardless, so
    nothing longer can ever ride through here."""
    if value is None or value == "":
        return None
    return str(value)[:120]


def lrclib_on(snapshot: Any) -> bool:
    """Whether the LRCLIB lookup is switched on and may run, as the core
    last reported it (``lyrics.fetch``): enabled, and its state is not
    ``off`` / ``internet_off``. False until the core reports."""
    fetch = _snapshot_part(snapshot, "lyrics", "fetch")
    return fetch.get("enabled") is True and fetch.get("state") not in ("off", "internet_off")


# ─── One song's lyrics ────────────────────────────────────────────────────

_DOC_SQL = text(
    """
    SELECT t.id AS track_id,
           l.track_id IS NOT NULL AS has_row,
           s.source, s.sidecar_name, s.synced, s.plain, s.has_synced, s.instrumental,
           s.local_checked_at, s.lrclib_status, s.updated_at, l.file_missing
    FROM library_tracks t
    LEFT JOIN track_lyrics_shown s ON s.track_id = t.id
    LEFT JOIN track_lyrics l ON l.track_id = t.id
    WHERE t.id = :id
    """
)

_SOURCE_LABELS = {"embedded": "from the song file", "lrclib": "from LRCLIB"}


def source_label(source: str | None, sidecar_name: str | None) -> str | None:
    """Where the shown lyrics came from, in words ([A8])."""
    if source == "sidecar":
        name = str(sidecar_name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
        return f"from {name}" if name else "from a .lrc file"
    return _SOURCE_LABELS.get(source or "")


def timed_lines(synced: Any) -> list[dict[str, Any]]:
    """The stored ``[[ms, "line"], …]`` pairs as ``{"t", "text"}``, in the
    stored (time) order; a malformed pair is skipped ([A7])."""
    if isinstance(synced, (bytes, bytearray)):
        synced = synced.decode("utf-8", "replace")
    if isinstance(synced, str):
        try:
            synced = json.loads(synced)
        except ValueError:
            return []
    if not isinstance(synced, list):
        return []
    out: list[dict[str, Any]] = []
    for pair in synced:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        ms, line = pair
        if isinstance(ms, bool) or not isinstance(ms, (int, float)) or not isinstance(line, str):
            continue
        if not math.isfinite(ms) or ms < 0:
            continue
        out.append({"t": int(ms), "text": line})
    return out


def doc_status(synced: Any, plain: Any, instrumental: Any) -> str:
    """``synced`` / ``plain`` / ``instrumental`` / ``none`` ([A6])."""
    if synced is not None:
        return "synced"
    if plain is not None:
        return "plain"
    if instrumental:
        return "instrumental"
    return "none"


def build_doc(row: Mapping[str, Any], *, lrclib_active: bool) -> dict[str, Any]:
    """A ``LyricsDoc`` from one row of :data:`_DOC_SQL` ([A6]–[A9]).

    ``checking`` — Domovoi is still looking: the song has no lyrics row yet,
    or the local scan has not read its files yet, or nothing is shown, the
    LRCLIB lookup is on and has not been asked about it, and the file is
    there."""
    has_row = bool(row.get("has_row"))
    synced = row.get("synced") if has_row else None
    plain = row.get("plain") if has_row else None
    status = doc_status(synced, plain, bool(row.get("instrumental")) if has_row else False)
    if not has_row or row.get("local_checked_at") is None:
        checking = True
    else:
        checking = (
            status == "none"
            and lrclib_active
            and row.get("lrclib_status") is None
            and not row.get("file_missing")
        )
    source = row.get("source") if has_row else None
    return {
        "track_id": int(row["track_id"]),
        "status": status,
        "checking": checking,
        "source": source,
        "source_label": source_label(source, row.get("sidecar_name")),
        "lines": timed_lines(synced) if status == "synced" else None,
        "text": plain if status in ("synced", "plain") else None,
        "updated_at": row.get("updated_at") if has_row else None,
    }


def line_at(lines: list[dict[str, Any]] | None, elapsed_ms: float) -> int:
    """The last timed line at ``elapsed_ms`` (``t <= elapsed_ms``), -1 before
    the first ([A10]; the dashboard's ``lyricsActiveIndex`` without its
    150 ms lead)."""
    if not lines:
        return -1
    return bisect_right([line["t"] for line in lines], elapsed_ms) - 1


async def _doc_row(session: Any, track_id: int) -> Mapping[str, Any] | None:
    return (await session.execute(_DOC_SQL, {"id": track_id})).mappings().first()


@router.get("/library/{track_id}/lyrics", response_model=LyricsDoc, dependencies=READ)
async def track_lyrics(
    track_id: int = Path(..., ge=1, le=_MAX_TRACK_ID),
) -> LyricsDoc:
    """One song's lyrics, as a player shows them."""
    async with session_scope() as s:
        await _require_v021(s)
        row = await _doc_row(s, track_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"track {track_id} not found", headers=NO_STORE)
    return LyricsDoc(**build_doc(row, lrclib_active=lrclib_on(get_cached_snapshot())))


# ─── A room's song, where it is, and its lyrics ──────────────────────────


@router.get("/now-playing/{room_id}/lyrics", response_model=RoomLyrics, dependencies=READ)
async def room_lyrics(room_id: str) -> RoomLyrics:
    """What ``room_id`` is playing, how far in, and its lyrics — one read
    that gives a client both the words and a fresh anchor to follow the
    room by. ``404`` for a room that is not provisioned."""
    async with session_scope() as s:
        await _require_v021(s)
    np = await music_api.now_playing_for_room(room_id)
    read_at = datetime.now(timezone.utc)
    if np is None:
        raise HTTPException(
            status_code=404, detail=f"room {room_id!r} not provisioned", headers=NO_STORE
        )
    playing = np.song is not None and np.state in ("play", "pause")
    track_id = np.track_id if playing else None
    doc: dict[str, Any] | None = None
    line_index: int | None = None
    if track_id is not None:
        async with session_scope() as s:
            row = await _doc_row(s, track_id)
        if row is not None:
            doc = build_doc(row, lrclib_active=lrclib_on(get_cached_snapshot()))
            if doc["status"] == "synced":
                line_index = line_at(doc["lines"], (np.elapsed_sec or 0.0) * 1000.0)
    return RoomLyrics(
        room_id=np.room_id,
        state=np.state,
        track_id=track_id,
        elapsed_sec=np.elapsed_sec,
        duration_sec=np.song.duration_sec if np.song is not None else None,
        read_at=read_at,
        line_index=line_index,
        lyrics=LyricsDoc(**doc) if doc is not None else None,
    )


# ─── The Jobs card ────────────────────────────────────────────────────────

_STATUS_SQL = text(
    f"""
    SELECT
      (SELECT count(*) FROM library_tracks) AS tracks,
      count(*) FILTER (WHERE local_checked_at IS NOT NULL) AS scanned,
      count(*) FILTER (WHERE eff_source IS NOT NULL) AS with_lyrics,
      count(*) FILTER (WHERE has_synced) AS synced,
      count(*) FILTER (WHERE eff_source IS NOT NULL AND NOT has_synced) AS plain,
      count(*) FILTER (WHERE instrumental) AS instrumental,
      count(*) FILTER (WHERE eff_source = 'sidecar') AS src_sidecar,
      count(*) FILTER (WHERE eff_source = 'embedded') AS src_embedded,
      count(*) FILTER (WHERE eff_source = 'lrclib') AS src_lrclib,
      count(*) FILTER (WHERE lrclib_status IS NOT NULL) AS asked,
      count(*) FILTER (WHERE lrclib_status = 'found') AS found,
      count(*) FILTER (WHERE lrclib_status = 'not_found') AS not_found,
      count(*) FILTER (WHERE lrclib_status = 'instrumental') AS lrclib_instrumental,
      count(*) FILTER (WHERE lrclib_status = 'skipped') AS skipped,
      count(*) FILTER (WHERE lrclib_status = 'error') AS errors,
      min(lrclib_next_at) FILTER (WHERE lrclib_status = 'not_found') AS next_retry_at,
      count(*) FILTER (WHERE lrc_state = 'written') AS lrc_written,
      count(*) FILTER (WHERE lrc_state = 'exists') AS lrc_exists,
      count(*) FILTER (WHERE lrc_state = 'edited') AS lrc_edited,
      count(*) FILTER (WHERE lrc_state = 'deleted') AS lrc_deleted,
      count(*) FILTER (WHERE lrc_state = 'failed') AS lrc_failed,
      (SELECT f.lrc_error FROM track_lyrics f
        WHERE f.lrc_state = 'failed' AND f.lrc_error IS NOT NULL
        GROUP BY f.lrc_error ORDER BY count(*) DESC, f.lrc_error LIMIT 1) AS lrc_last_error,
      count(*) FILTER (WHERE {INDEX_PENDING_SQL}) AS index_pending,
      count(*) FILTER (WHERE plain_md5 IS NOT NULL AND lines_md5 = plain_md5
                       AND lines_version = :version) AS indexed
    FROM track_lyrics
    """
)


def build_status(counts: Mapping[str, Any], snapshot: Any) -> dict[str, Any]:
    """A ``LyricsStatus`` from one row of :data:`_STATUS_SQL` and the core's
    snapshot (keys ``lyrics`` and ``lyrics_index``; lyrics contract §11.4):
    counts from the database; states, ``due``, the timers and the switches
    from the core. Before the core has reported: states ``"unknown"``,
    flags null."""
    c = counts
    tracks = int(c.get("tracks") or 0)
    scanned = int(c.get("scanned") or 0)
    fetch = _snapshot_part(snapshot, "lyrics", "fetch")
    scan = _snapshot_part(snapshot, "lyrics", "scan")
    index = _snapshot_part(snapshot, "lyrics_index")
    enabled = _flag(fetch.get("enabled"))
    write_lrc = _flag(fetch.get("write_lrc"))
    if enabled is False or write_lrc is False:
        lrc_enabled: bool | None = False
    elif enabled is True and write_lrc is True:
        lrc_enabled = True
    else:
        lrc_enabled = None
    return {
        "tracks": tracks,
        "scanned": scanned,
        "with_lyrics": int(c.get("with_lyrics") or 0),
        "synced": int(c.get("synced") or 0),
        "plain": int(c.get("plain") or 0),
        "instrumental": int(c.get("instrumental") or 0),
        "by_source": {
            "sidecar": int(c.get("src_sidecar") or 0),
            "embedded": int(c.get("src_embedded") or 0),
            "lrclib": int(c.get("src_lrclib") or 0),
        },
        "lrclib": {
            "enabled": enabled,
            "state": _state(fetch),
            "asked": int(c.get("asked") or 0),
            "found": int(c.get("found") or 0),
            "not_found": int(c.get("not_found") or 0),
            "instrumental": int(c.get("lrclib_instrumental") or 0),
            "skipped": int(c.get("skipped") or 0),
            "errors": int(c.get("errors") or 0),
            "due": _count(fetch.get("due")),
            "next_retry_at": c.get("next_retry_at"),
            "rate_limited_until": _iso(fetch.get("rate_limited_until")),
            "paused_until": _iso(fetch.get("paused_until")),
            "last_error": _short_code(fetch.get("last_error")),
        },
        "lrc_files": {
            "enabled": lrc_enabled,
            "written": int(c.get("lrc_written") or 0),
            "exists": int(c.get("lrc_exists") or 0),
            "edited": int(c.get("lrc_edited") or 0),
            "deleted": int(c.get("lrc_deleted") or 0),
            "failed": int(c.get("lrc_failed") or 0),
            "last_error": _short_code(c.get("lrc_last_error")),
        },
        "scan": {
            "state": _state(scan),
            "unscanned": max(0, tracks - scanned),
            "last_pass_at": _iso(scan.get("last_pass_at")),
        },
        "index": {
            "state": _state(index),
            "pending": int(c.get("index_pending") or 0),
            "indexed": int(c.get("indexed") or 0),
        },
        "search_enabled": _flag(index.get("search_enabled")),
    }


@router.get("/lyrics/status", response_model=LyricsStatus, dependencies=READ)
async def lyrics_status() -> LyricsStatus:
    """How far the house has got with its lyrics: how many songs have them
    (and timed), where they came from, the LRCLIB lookup, the .lrc files
    Domovoi saved, the local scan and the search index — counts and states,
    never a lyric or a title."""
    async with session_scope() as s:
        await _require_v021(s)
        row = (await s.execute(_STATUS_SQL, {"version": lyric_norm_version()})).mappings().one()
    return LyricsStatus(**build_status(row, get_cached_snapshot()))
