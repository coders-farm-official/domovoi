"""Music API — library, playlists bridge, now-playing, controls, upload.

Read endpoints hit Postgres directly (library_tracks) and per-room MPD
daemons (current song / state / elapsed). Action endpoints — play,
pause, skip, reindex, enrich, add-by-query/url — proxy to the core's
``/v1/admin/*`` surface.

Provider-specific search/browse/download UIs are NOT here (design
§10.1): media-provider plugins ship their own pages under
``/api/plugins/<slug>/...``. The core music surface stays generic —
"add by query" / "add by url" enqueue rows in the provider-agnostic
media-acquisition queue (§4.8) and answer with graceful-absence copy
when no fulfiller plugin is installed. The generic queue readout lives
at ``/api/acquisitions``.
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text

from web.backend.db import session_scope
from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    get_cached_snapshot,
    post_admin,
)
from web.backend.schemas import (
    AddByQueryRequest,
    AddByUrlRequest,
    CastTracksRequest,
    FavoriteNowPlayingResult,
    LibraryPage,
    LibraryStats,
    NowPlaying,
    NowPlayingSong,
    Playlist,
    PlayPlaylistRequest,
    PlayRequest,
    PlayTrackRequest,
    Track,
    TrackPatch,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/music", tags=["music"])


# ─── Library ───────────────────────────────────────────────────────────────


_SORT_CLAUSES: dict[str, str] = {
    # Every clause ends with ``id`` as a stable tiebreaker so rows
    # with equal sort keys keep a consistent order. Without this, a
    # bulk-indexer batch that stamps the same ``added_at`` on every
    # row leaves ties to MVCC heap order — and a single UPDATE on a
    # tied row (favoriting it, say) rewrites its tuple at the end of
    # the heap, which the next SELECT then renders as "the row jumped
    # to the bottom of the page." The id tiebreaker pins the order.
    "added_desc": "added_at DESC, id DESC",
    "added_asc":  "added_at ASC, id ASC",
    "title":      "LOWER(title) ASC NULLS LAST, added_at DESC, id DESC",
    "artist":     "LOWER(artist) ASC NULLS LAST, added_at DESC, id DESC",
    "duration":   "duration_sec DESC NULLS LAST, added_at DESC, id DESC",
}


@router.get("/library", response_model=LibraryPage)
async def list_library(
    q: str | None = Query(default=None, description="Substring filter against title/artist/album/file_path"),
    source: str | None = Query(
        default=None,
        description="Filter by source column. Use 'manual' for rows with NULL source; "
                    "any other value matches exactly (source is an open enum — "
                    "provider plugins register their own values).",
    ),
    favorited: bool | None = Query(
        default=None,
        description="True → only favorited rows, False → only un-favorited, "
                    "omitted → no filter. The dashboard's 'favorites only' "
                    "checkbox sends True or omits.",
    ),
    sort: Literal["added_desc", "added_asc", "title", "artist", "duration"] = Query(default="added_desc"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> LibraryPage:
    """One page of ``library_tracks`` plus the unbounded count that
    matches the active ``q`` / ``source`` filters.

    Sort options are whitelisted (the ORDER BY can't be user-supplied)
    so a malformed ``sort`` returns 422 rather than a SQL error.
    """
    where_parts: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if q:
        where_parts.append(
            "(title ILIKE :q OR artist ILIKE :q OR album ILIKE :q OR file_path ILIKE :q)"
        )
        params["q"] = f"%{q}%"
    if source:
        if source == "manual":
            where_parts.append("source IS NULL")
        else:
            where_parts.append("source = :source")
            params["source"] = source
    if favorited is not None:
        where_parts.append("favorited = :favorited")
        params["favorited"] = favorited
    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    order_by = _SORT_CLAUSES[sort]

    async with session_scope() as s:
        total_row = await s.execute(
            text(f"SELECT COUNT(*) FROM library_tracks {where}"),
            params,
        )
        total = int(total_row.scalar_one())

        rows = await s.execute(
            text(
                f"""
                SELECT id, file_path, title, artist, album, duration_sec,
                       source, source_id, musicbrainz_recording_id,
                       added_at, added_via, enriched_at, favorited
                FROM library_tracks
                {where}
                ORDER BY {order_by}
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
        return LibraryPage(total=total, items=[_row_to_track(r) for r in rows.all()])


@router.get("/library/stats", response_model=LibraryStats)
async def library_stats() -> LibraryStats:
    """Whole-library aggregates for the Stats tab. One round-trip; no
    dependence on what the Library tab has loaded into memory.
    """
    async with session_scope() as s:
        totals = await s.execute(
            text(
                """
                SELECT
                  COUNT(*)::bigint                              AS total_tracks,
                  COALESCE(SUM(duration_sec), 0)::bigint        AS total_duration_sec,
                  COUNT(*) FILTER (WHERE enriched_at IS NOT NULL)::bigint AS enriched_count
                FROM library_tracks
                """
            )
        )
        row = totals.one()

        by_via_rows = await s.execute(
            text(
                """
                SELECT COALESCE(added_via, 'unknown') AS via, COUNT(*)::bigint
                FROM library_tracks
                GROUP BY 1
                """
            )
        )
        by_added_via = {str(r[0]): int(r[1]) for r in by_via_rows.all()}

        # Open-enum source buckets (design §6.4): whatever provider
        # plugins stamp into library_tracks.source shows up here
        # automatically — this also feeds the Library tab's source
        # filter options, so the dropdown is data-driven.
        by_source_rows = await s.execute(
            text(
                """
                SELECT COALESCE(source, 'manual') AS src, COUNT(*)::bigint
                FROM library_tracks
                GROUP BY 1
                """
            )
        )
        by_source = {str(r[0]): int(r[1]) for r in by_source_rows.all()}

    return LibraryStats(
        total_tracks=int(row[0]),
        total_duration_sec=int(row[1]),
        by_added_via=by_added_via,
        by_source=by_source,
        enriched_count=int(row[2]),
    )


@router.get("/library/{track_id}", response_model=Track)
async def get_track(track_id: int) -> Track:
    async with session_scope() as s:
        row = await s.execute(
            text(
                """
                SELECT id, file_path, title, artist, album, duration_sec,
                       source, source_id, musicbrainz_recording_id,
                       added_at, added_via, enriched_at, favorited
                FROM library_tracks WHERE id = :id
                """
            ),
            {"id": track_id},
        )
        result = row.first()
    if result is None:
        raise HTTPException(status_code=404, detail=f"track {track_id} not found")
    return _row_to_track(result)


@router.patch("/library/{track_id}", response_model=Track)
async def patch_track(track_id: int, payload: TrackPatch) -> Track:
    """Partial update of a library_tracks row. Today only ``favorited``
    is editable; the PATCH shape is general so future per-field edits
    (title/artist tweaks, etc.) won't need a new endpoint."""
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields provided")
    set_fragments = [f"{k} = :{k}" for k in updates]
    params: dict[str, Any] = {"id": track_id, **updates}
    favorited_changed = "favorited" in updates
    async with session_scope() as s:
        row = await s.execute(
            text(
                f"""
                UPDATE library_tracks
                SET {', '.join(set_fragments)}
                WHERE id = :id
                RETURNING id, file_path, title, artist, album, duration_sec,
                          source, source_id, musicbrainz_recording_id,
                          added_at, added_via, enriched_at, favorited
                """
            ),
            params,
        )
        result = row.first()
        if result is not None and favorited_changed:
            # The Favorites virtual playlist's track_count is derived
            # from this flag, so any flip needs to wake the
            # dashboard's LISTEN task — same pattern the other
            # NOTIFY-coupled CRUD endpoints use.
            await s.execute(
                text("SELECT pg_notify('playlists_changed', 'favorited')")
            )
    if result is None:
        raise HTTPException(status_code=404, detail=f"track {track_id} not found")
    return _row_to_track(result)


@router.get("/library/{track_id}/playlists", response_model=list[Playlist])
async def list_track_playlists(track_id: int) -> list[Playlist]:
    """Which playlists is this track in? Powers the library row's
    ``+`` drawer — it needs to render a ``✓ in`` badge per
    membership and let the user toggle. Includes the virtual
    Favorites playlist when ``library_tracks.favorited`` is true."""
    async with session_scope() as s:
        track_row = await s.execute(
            text(
                "SELECT favorited FROM library_tracks WHERE id = :id"
            ),
            {"id": track_id},
        )
        track = track_row.first()
        if track is None:
            raise HTTPException(
                status_code=404, detail=f"track {track_id} not found"
            )
        is_favorited = bool(track[0])
        rows = await s.execute(
            text(
                """
                SELECT p.id, p.name, p.created_at,
                       (SELECT COUNT(*) FROM playlist_tracks
                        WHERE playlist_id = p.id)::bigint
                FROM playlist_tracks pt
                JOIN playlists p ON p.id = pt.playlist_id
                WHERE pt.track_id = :id
                ORDER BY LOWER(p.name) ASC
                """
            ),
            {"id": track_id},
        )
    out: list[Playlist] = []
    if is_favorited:
        fav_count = 0
        async with session_scope() as s:
            fav_count = (
                await s.execute(
                    text("SELECT COUNT(*) FROM library_tracks WHERE favorited")
                )
            ).scalar_one()
        out.append(
            Playlist(
                id=0,
                name="Favorites",
                track_count=int(fav_count),
                created_at=None,
                is_virtual=True,
            )
        )
    for r in rows.all():
        out.append(
            Playlist(
                id=int(r[0]),
                name=r[1],
                created_at=r[2],
                track_count=int(r[3]),
                is_virtual=False,
            )
        )
    return out


@router.delete("/library/{track_id}", status_code=204)
async def delete_track(
    track_id: int,
    also_file: bool = Query(
        default=False,
        description=(
            "If true, delete the audio file from disk before removing "
            "the library_tracks row. The file_path is validated to be "
            "inside MUSIC_DIR so a malformed row can't cause the "
            "endpoint to unlink an arbitrary file outside the music "
            "directory. Defaults to false (metadata-only delete) — but "
            "note that the next library indexer sweep will then "
            "resurrect the row from the orphan file."
        ),
    ),
) -> None:
    """Remove a library track. By default the disk file is left in
    place; pass ``?also_file=true`` to also unlink it.

    Order matters when ``also_file=true``: file first, then row. If
    the unlink fails (permission denied, file locked by an active
    MPD playback, etc.) we propagate the error and leave the row
    intact so the user can retry. If the unlink succeeds but the
    row delete somehow fails, the next indexer sweep won't resurrect
    the row (the file is gone), so we're still consistent.

    Returns 404 if no row matches. With ``also_file=true``, the
    file-not-found case is treated as success (the user wanted it
    gone; it already is) — just a debug log.
    """
    import os
    from pathlib import Path

    from domovoi.config import settings as core_settings

    async with session_scope() as s:
        if also_file:
            row = await s.execute(
                text(
                    "SELECT file_path FROM library_tracks WHERE id = :id"
                ),
                {"id": track_id},
            )
            existing = row.first()
            if existing is None:
                raise HTTPException(
                    status_code=404, detail=f"track {track_id} not found"
                )
            file_path_str = existing[0]
            music_dir = Path(core_settings.music_dir).expanduser().resolve(strict=False)
            target = Path(file_path_str).resolve(strict=False)
            try:
                target.relative_to(music_dir)
            except ValueError:
                # Path doesn't sit inside MUSIC_DIR — refuse. Protects
                # against a corrupted row pointing somewhere wild
                # (e.g. an old import from a previous music_dir) from
                # turning this endpoint into an "unlink anything"
                # primitive.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"refusing to delete {file_path_str!r}: not "
                        f"inside MUSIC_DIR ({core_settings.music_dir!r}). "
                        "Delete the file by hand and re-run rescan."
                    ),
                )
            try:
                os.remove(target)
                log.info("deleted file on disk: %s", target)
            except FileNotFoundError:
                log.warning(
                    "track %s file already missing on disk: %s",
                    track_id, target,
                )
            except OSError as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"couldn't delete file {target!r}: {e}",
                ) from e

        result = await s.execute(
            text("DELETE FROM library_tracks WHERE id = :id"), {"id": track_id}
        )
    if (result.rowcount or 0) == 0:
        raise HTTPException(status_code=404, detail=f"track {track_id} not found")


# ─── Upload ────────────────────────────────────────────────────────────────
# Drop audio files into the library straight from the browser. Files land
# in MUSIC_DIR/uploads/; the Domovoi server's reindex (triggered after the
# write) indexes them into library_tracks AND tells each room's MPD to
# rescan so they're immediately playable. Mirrors the established
# file-upload pattern in web/backend/api/voices.py (UploadFile → write to
# a dir the Domovoi server owns → ping the Domovoi server).


# Audio extensions accepted for upload. Mirrors
# domovoi/workers/library_indexer.py:_AUDIO_EXTENSIONS — the formats
# mutagen can tag-read and the indexer will pick up. Kept as a local copy
# so the web process doesn't import the indexer's worker chain (MPD /
# MusicBrainz clients) just for a constant; if you add a format in the
# indexer, add it here too.
_UPLOAD_AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".oga",
    ".opus", ".wav", ".wma", ".aac", ".alac",
})

# Subdirectory under MUSIC_DIR where uploads land — keeps browser-pushed
# files visually separate from provider plugins' per-source folders. The indexer
# rglob's the whole tree, so a subdir is indexed all the same.
_UPLOAD_SUBDIR = "uploads"

# Sanity cap on a single zip's member count, so a zip-bomb-ish archive
# can't fan out into an unbounded extract loop.
_MAX_ZIP_MEMBERS = 5000


class LibraryUploadResult(BaseModel):
    saved: int
    files: list[str]            # basenames actually written, in upload order
    skipped: list[str]          # "<name>: <reason>" for anything not saved
    reindex_triggered: bool     # False when the Domovoi server was unreachable


def _safe_basename(name: str) -> str:
    """Collapse an upload/zip-member name to a bare filename. Strips any
    directory components (defangs zip-slip / ``../`` traversal) across
    both path separators so a crafted archive can't write outside the
    upload dir."""
    return os.path.basename(name.replace("\\", "/")).strip()


def _unique_path(dirpath: Path, name: str) -> Path:
    """A non-colliding path inside ``dirpath`` for ``name`` — appends
    `` (1)``, `` (2)`` … to the stem if the target already exists, so a
    second upload of "song.mp3" doesn't clobber the first."""
    target = dirpath / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    i = 1
    while True:
        cand = dirpath / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
        i += 1


@router.post("/library/upload", response_model=LibraryUploadResult)
async def upload_to_library(
    files: list[UploadFile] = File(...),
) -> LibraryUploadResult:
    """Upload audio into the library from the browser.

    Accepts one or more audio files directly, and/or ``.zip`` archives
    (each unpacked, with audio members extracted and non-audio members
    — cover art, ``.txt`` — ignored). Everything lands in
    ``MUSIC_DIR/uploads/``; a library reindex is then triggered on the
    Domovoi server, which indexes the new rows and tells each room's MPD
    to rescan so the tracks are playable right away.

    Supported extensions: mp3, m4a, mp4, flac, ogg, oga, opus, wav,
    wma, aac, alac. Standalone files of any other type are reported in
    ``skipped``; if nothing supported is found, returns 400.

    If the Domovoi server is unreachable the files are still saved
    (``reindex_triggered=false``) and get picked up by the indexer on
    its next startup sweep.
    """
    from domovoi.config import settings as core_settings

    music_dir = Path(core_settings.music_dir).expanduser()
    dest = music_dir / _UPLOAD_SUBDIR
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(
            status_code=500, detail=f"could not create upload dir {dest}: {e}"
        ) from e

    saved: list[str] = []
    skipped: list[str] = []

    def _write_audio(raw_name: str, data: bytes) -> None:
        base = _safe_basename(raw_name)
        if not base:
            skipped.append(f"{raw_name!r}: bad filename")
            return
        if Path(base).suffix.lower() not in _UPLOAD_AUDIO_EXTENSIONS:
            skipped.append(f"{base}: unsupported type")
            return
        if not data:
            skipped.append(f"{base}: empty file")
            return
        path = _unique_path(dest, base)
        try:
            path.write_bytes(data)
        except OSError as e:
            skipped.append(f"{base}: write failed ({e})")
            return
        saved.append(path.name)

    for up in files:
        raw = await up.read()
        fname = up.filename or "upload"
        if fname.lower().endswith(".zip"):
            try:
                archive = zipfile.ZipFile(io.BytesIO(raw))
            except zipfile.BadZipFile:
                skipped.append(f"{fname}: not a valid zip")
                continue
            members = [m for m in archive.infolist() if not m.is_dir()]
            if len(members) > _MAX_ZIP_MEMBERS:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"{fname}: {len(members)} files exceeds the "
                        f"{_MAX_ZIP_MEMBERS}-file cap"
                    ),
                )
            for m in members:
                # Silently skip non-audio zip members (album art, .nfo,
                # .txt) — they're expected clutter in music archives, not
                # something worth a per-file "skipped" line.
                if Path(_safe_basename(m.filename)).suffix.lower() not in _UPLOAD_AUDIO_EXTENSIONS:
                    continue
                try:
                    _write_audio(m.filename, archive.read(m))
                except Exception as e:  # noqa: BLE001 — one bad member ≠ fail the batch
                    skipped.append(f"{_safe_basename(m.filename)}: {e}")
        else:
            _write_audio(fname, raw)

    if not saved:
        detail = "no supported audio files in upload"
        if skipped:
            detail += "; " + "; ".join(skipped[:10])
        raise HTTPException(status_code=400, detail=detail)

    # Index the new files into library_tracks and tell each room's MPD to
    # rescan (so they're playable). Best-effort: a saved-but-unindexed
    # file is recovered by the Domovoi server's startup sweep.
    status_code, _ = await post_admin("/v1/admin/library/reindex")
    reindex_triggered = status_code == 200
    if not reindex_triggered:
        log.warning(
            "library upload: saved %d file(s) but reindex trigger failed "
            "(domovoi status=%s); files will index on its next boot",
            len(saved), status_code,
        )

    return LibraryUploadResult(
        saved=len(saved),
        files=saved,
        skipped=skipped,
        reindex_triggered=reindex_triggered,
    )


# ─── Acquisition cancel ────────────────────────────────────────────────────
# The generic queue readout is GET /api/acquisitions (its own module);
# cancel lives here with the other music mutations because the Jobs
# readout on the Music page owns the affordance.


@router.delete("/acquisitions/{acq_id}", status_code=204)
async def cancel_acquisition(acq_id: int) -> None:
    """Cancel a queued or claimed media acquisition (design §4.8).

    Pending/claimed → status='cancelled'. A fulfiller mid-claim checks
    the row's status between steps and bails; a flip during an in-flight
    fetch can't abort the subprocess, but the row won't transition to
    'done' afterwards. Finished rows → 409.
    """
    async with session_scope() as s:
        existing = await s.execute(
            text("SELECT status FROM media_acquisitions WHERE id = :id"),
            {"id": acq_id},
        )
        row = existing.first()
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"acquisition {acq_id} not found"
            )
        if row[0] not in ("pending", "claimed"):
            raise HTTPException(
                status_code=409,
                detail=f"acquisition {acq_id} already {row[0]}; nothing to cancel",
            )
        await s.execute(
            text(
                """
                UPDATE media_acquisitions
                SET status = 'cancelled',
                    completed_at = NOW(),
                    error = COALESCE(error || ' / ', '') || 'cancelled via web'
                WHERE id = :id
                """
            ),
            {"id": acq_id},
        )
        # Wake the LISTEN task on the dashboard's realtime loop —
        # cancel-via-UI is the one mutation site where the user is
        # *watching* the row update, so sub-second feedback matters.
        await s.execute(
            text("SELECT pg_notify('acquisitions_changed', 'cancelled')")
        )


# ─── Now playing ───────────────────────────────────────────────────────────


@router.get("/now-playing", response_model=list[NowPlaying])
async def now_playing() -> list[NowPlaying]:
    """Per-room playback state.

    Reads the mpd_rooms table for control-port assignments, then opens
    a one-shot connection to each MPD daemon for ``status`` +
    ``currentsong``. Rooms that aren't reachable (container down, port
    blocked) surface as state ``"stop"`` with no song — same as a real
    idle MPD.
    """
    rooms = await _list_provisioned_rooms()
    return [await _now_playing_for(r) for r in rooms]


@router.post(
    "/now-playing/{room_id}/favorite",
    response_model=FavoriteNowPlayingResult,
)
async def favorite_now_playing(room_id: str) -> FavoriteNowPlayingResult:
    """Favorite whatever is currently playing in ``room_id``.

    Behavior depends on what the room's MPD currentsong looks like:

    * Local file (``file`` doesn't start with ``http``) → match against
      ``library_tracks.file_path`` by trailing-suffix (MPD stores
      relative paths, ``library_tracks`` stores host absolute paths)
      and flip ``favorited = TRUE`` on the matched row.
    * External stream (HTTP file) → enqueue a *generic* media
      acquisition by ``title artist`` query (design §4.8) so the track
      lands in the library. The core's dedup layers short-circuit if
      the user already has it (returns ``already_favorited=true``);
      with no fulfiller plugin installed the row waits ``pending`` and
      the graceful-absence copy comes back as the toast message.
      (Provider-owned streams — e.g. radio stations — offer their own
      richer favorite affordances on their plugin pages; this endpoint
      stays provider-agnostic.)

    Returns the resulting ``FavoriteNowPlayingResult`` describing what
    happened so the dashboard can render an appropriate toast.
    """
    rooms = await _list_provisioned_rooms()
    room = next((r for r in rooms if r[0] == room_id), None)
    if room is None:
        raise HTTPException(status_code=404, detail=f"room {room_id!r} not provisioned")

    np = await _now_playing_for(room)
    if np.song is None or np.state == "stop":
        raise HTTPException(
            status_code=400,
            detail=f"nothing is playing in {room_id!r}",
        )

    song_file = np.song.file or ""
    title = (np.song.title or "").strip()
    artist = (np.song.artist or "").strip()

    if not song_file.startswith("http"):
        return await _favorite_library_track(song_file, title, artist)

    return await _favorite_external_stream(title, artist, room_id)


# ─── Action endpoints (proxied to domovoi admin) ──────────────────────
# Music actions need the Domovoi server's per-room MPD client cache and
# session context (smart-skip queue, provider session keys, etc.) — the
# web backend can't reproduce those from a sibling process. Each route
# here POSTs to the matching ``/v1/admin/*`` on the Domovoi server and
# pipes status + body back. 502 when the Domovoi server can't be reached.


@router.post("/play")
async def play(body: PlayRequest):
    status, payload = await post_admin(
        "/v1/admin/music/play",
        {"room_id": body.room_id, "query": body.query},
    )
    return bridge_response(status, payload)


@router.post("/play-playlist")
async def play_playlist(body: PlayPlaylistRequest):
    """Start a playlist (real or the Favorites virtual one) in a
    room. Proxies to the Domovoi server's
    ``/v1/admin/music/play-playlist`` which handles the SELECT,
    hands the first/picked track to MPD, and stamps
    ``app.state.current_playlist`` so subsequent ``next`` calls
    stay inside the playlist."""
    status, payload = await post_admin(
        "/v1/admin/music/play-playlist",
        {
            "room_id": body.room_id,
            "playlist_id": body.playlist_id,
            "shuffle": body.shuffle,
        },
    )
    return bridge_response(status, payload)


@router.post("/add-by-query")
async def add_by_query(body: AddByQueryRequest):
    """Queue a generic media acquisition by free-text query (design
    §4.8). Proxies the core's ``/v1/admin/music/add-by-query`` — an
    open daily action (§7.3). Exists even with no provider installed:
    the row waits ``pending`` and the response carries the graceful-
    absence copy; installing a fulfiller later drains the backlog."""
    status, payload = await post_admin(
        "/v1/admin/music/add-by-query",
        {
            "room_id": body.room_id,
            "query": body.query,
            "artist": body.artist,
            "attach_to_playlist_id": body.attach_to_playlist_id,
        },
    )
    return bridge_response(status, payload)


@router.post("/add-by-url")
async def add_by_url(body: AddByUrlRequest, request: Request):
    """Queue a media acquisition for an EXACT external URL. Proxies the
    core's ``/v1/admin/music/add-by-url``. Backs the Recently-played
    drawer's "+ add" button when a play carries a stored URL — precise
    where the now-playing heart re-searches by title.

    §7.3 outbound-fetch tier: the CORE endpoint decides (admin session
    OR fulfiller url_matcher allowlist + per-source rate limit), so the
    caller's credentials and real source address are forwarded and its
    verdict passes back through verbatim."""
    status, payload = await post_admin(
        "/v1/admin/music/add-by-url",
        {
            "room_id": body.room_id,
            "url": body.url,
            "title": body.title,
            "dedup_key": body.dedup_key,
            "attach_to_playlist_id": body.attach_to_playlist_id,
        },
        headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


@router.post("/play-track")
async def play_track(body: PlayTrackRequest):
    """Direct play of a library track by id. Proxies to the
    Domovoi server's ``/v1/admin/music/play-track``, which hands the
    file straight to MPD without going through the router — so a UI
    click doesn't write to ``conversation_log`` and never falls
    through to an external streaming provider the way the fuzzy
    ``/play`` text-query path can."""
    status, payload = await post_admin(
        "/v1/admin/music/play-track",
        {"room_id": body.room_id, "track_id": body.track_id},
    )
    return bridge_response(status, payload)


@router.post("/pause/{room_id}")
async def pause(room_id: str):
    status, payload = await post_admin(f"/v1/admin/music/pause/{room_id}")
    return bridge_response(status, payload)


@router.post("/resume/{room_id}")
async def resume(room_id: str):
    status, payload = await post_admin(f"/v1/admin/music/resume/{room_id}")
    return bridge_response(status, payload)


@router.post("/stop/{room_id}")
async def stop(room_id: str):
    status, payload = await post_admin(f"/v1/admin/music/stop/{room_id}")
    return bridge_response(status, payload)


@router.post("/skip/{room_id}")
async def skip(room_id: str):
    status, payload = await post_admin(f"/v1/admin/music/skip/{room_id}")
    return bridge_response(status, payload)


@router.post("/library/reindex")
async def reindex():
    status, payload = await post_admin("/v1/admin/library/reindex")
    return bridge_response(status, payload)


@router.post("/library/enrich")
async def enrich():
    status, payload = await post_admin("/v1/admin/library/enrich")
    return bridge_response(status, payload)


# ─── Browser music player (client-side playback) ────────────────────────────
# These back the dashboard's in-browser Web-Audio player. Unlike the action
# endpoints above (which drive server-side MPD in a room), these stream the
# raw library file / its embedded cover art to the
# browser so it can play locally. All served paths are containment-checked
# inside MUSIC_DIR / COVER_ART_DIR. No play-count or intents_log writes —
# browser playback history is deliberately ephemeral (client-side only).

# Content types for the audio Range endpoint, keyed by lowercase suffix.
# Mirrors the indexer's accepted formats; anything unknown falls back to
# a generic octet-stream (the browser sniffs / the <audio> element copes).
_AUDIO_CONTENT_TYPES: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".wma": "audio/x-ms-wma",
    ".alac": "audio/mp4",
}

# How big a chunk to pull off disk per iteration while streaming a range.
_STREAM_CHUNK = 256 * 1024


async def _library_file_path(track_id: int) -> Path:
    """Resolve a library track id to its on-disk file, containment-checked
    inside MUSIC_DIR. Raises 404 (no row / missing file) or 400 (path
    escapes MUSIC_DIR) — the same realpath/``relative_to`` guard the
    delete-with-file path uses, so a corrupted row can't turn these
    endpoints into an arbitrary-file read."""
    from domovoi.config import settings as core_settings

    async with session_scope() as s:
        row = await s.execute(
            text("SELECT file_path FROM library_tracks WHERE id = :id"),
            {"id": track_id},
        )
        existing = row.first()
    if existing is None:
        raise HTTPException(status_code=404, detail=f"track {track_id} not found")

    file_path_str = existing[0]
    music_dir = Path(core_settings.music_dir).expanduser().resolve(strict=False)
    target = Path(file_path_str).resolve(strict=False)
    try:
        target.relative_to(music_dir)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"refusing to serve {file_path_str!r}: not inside MUSIC_DIR "
                f"({core_settings.music_dir!r})"
            ),
        )
    if not target.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"track {track_id} file missing on disk: {target}",
        )
    return target


def _parse_range(range_header: str | None, file_size: int) -> tuple[int, int] | None:
    """Parse a single-range ``Range: bytes=start-end`` header into an
    inclusive ``(start, end)`` byte pair clamped to the file. Returns None
    for a missing/unsatisfiable/multi-range header (caller then serves the
    whole file with 200). Only the common single-range form is supported —
    that's all ``<audio>`` seeking ever sends."""
    if not range_header:
        return None
    range_header = range_header.strip()
    if not range_header.startswith("bytes=") or "," in range_header:
        return None
    spec = range_header[len("bytes="):].strip()
    if "-" not in spec:
        return None
    start_s, _, end_s = spec.partition("-")
    try:
        if start_s == "":
            # Suffix range: last N bytes.
            n = int(end_s)
            if n <= 0:
                return None
            start = max(0, file_size - n)
            end = file_size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else file_size - 1
    except ValueError:
        return None
    if start > end or start >= file_size:
        return None
    end = min(end, file_size - 1)
    return start, end


@router.get("/library/{track_id}/audio")
async def stream_audio(
    track_id: int,
    request: Request,
    download: bool = Query(False, description="serve as attachment (save to device)"),
):
    """Stream a library track's file to the browser player with HTTP
    ``Range`` / ``206 Partial Content`` support so ``<audio>`` can seek and
    progressively load. A rangeless request gets the whole file (200); a
    ``Range: bytes=…`` request gets just that slice (206). Always advertises
    ``Accept-Ranges: bytes``. With ``?download=1`` the same bytes are marked
    ``attachment`` so browsers/Android save the file instead of playing it."""
    from web.backend.api.audio_serve import attachment_headers, safe_download_name

    target = await _library_file_path(track_id)
    file_size = target.stat().st_size
    content_type = _AUDIO_CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")

    rng = _parse_range(request.headers.get("range"), file_size)

    base_headers = {
        "Accept-Ranges": "bytes",
        # Library files are immutable-ish; let the browser + service worker
        # cache aggressively. The URL is keyed by track id, and re-index
        # replaces the row rather than mutating the file in place.
        "Cache-Control": "public, max-age=86400",
    }
    if download:
        # Library files are stored on disk as <artist>/<title>.<ext>, so the
        # on-disk basename is already the human-meaningful name.
        base_headers.update(attachment_headers(safe_download_name(target.name)))

    if rng is None:
        # Whole-file response. Still streamed so a big FLAC doesn't buffer
        # fully in memory.
        headers = {**base_headers, "Content-Length": str(file_size)}
        return StreamingResponse(
            _iter_file(target, 0, file_size - 1),
            status_code=200,
            media_type=content_type,
            headers=headers,
        )

    start, end = rng
    length = end - start + 1
    headers = {
        **base_headers,
        "Content-Length": str(length),
        "Content-Range": f"bytes {start}-{end}/{file_size}",
    }
    return StreamingResponse(
        _iter_file(target, start, end),
        status_code=206,
        media_type=content_type,
        headers=headers,
    )


async def _iter_file(path: Path, start: int, end: int):
    """Yield ``[start, end]`` (inclusive) of ``path`` in chunks. Runs the
    blocking file reads in a thread so the event loop keeps serving."""
    import anyio

    remaining = end - start + 1
    with path.open("rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = await anyio.to_thread.run_sync(f.read, min(_STREAM_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


# Embedded-art extraction. mutagen ships in the ``[real-clients]`` extra; we
# import it lazily so the web process boots even without it (the endpoint
# then just reports "no art" and the frontend renders its gradient tile).
_COVER_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _extract_embedded_cover(path: Path) -> tuple[bytes, str] | None:
    """Return ``(image_bytes, mime)`` for a file's embedded cover art, or
    None when there's none (or mutagen isn't installed). Handles the common
    containers: ID3 APIC (mp3), FLAC pictures, MP4/M4A ``covr``, and
    Vorbis ``metadata_block_picture`` (ogg/opus)."""
    try:
        import mutagen
    except Exception as e:  # pragma: no cover - env without mutagen
        log.debug("mutagen not importable for cover extraction: %s", e)
        return None

    try:
        # ID3 (mp3) — APIC frames.
        from mutagen.id3 import ID3

        try:
            tags = ID3(str(path))
            apics = tags.getall("APIC")
            if apics:
                pic = apics[0]
                return pic.data, (pic.mime or "image/jpeg")
        except Exception:
            pass

        f = mutagen.File(str(path))
        if f is None:
            return None

        # FLAC (and anything exposing .pictures).
        pics = getattr(f, "pictures", None)
        if pics:
            pic = pics[0]
            return bytes(pic.data), (pic.mime or "image/jpeg")

        tags = getattr(f, "tags", None)
        if tags is None:
            return None

        # MP4 / M4A cover atoms.
        covr = None
        try:
            covr = tags.get("covr")
        except Exception:
            covr = None
        if covr:
            data = bytes(covr[0])
            fmt = getattr(covr[0], "imageformat", None)
            # 14 == PNG per mutagen's MP4Cover.FORMAT_PNG, else JPEG.
            mime = "image/png" if fmt == 14 else "image/jpeg"
            return data, mime

        # Vorbis comments (ogg/opus): metadata_block_picture (base64 FLAC pic).
        import base64

        from mutagen.flac import Picture

        mbp = None
        try:
            mbp = tags.get("metadata_block_picture")
        except Exception:
            mbp = None
        if mbp:
            raw = mbp[0] if isinstance(mbp, list) else mbp
            try:
                pic = Picture(base64.b64decode(raw))
                return bytes(pic.data), (pic.mime or "image/jpeg")
            except Exception:
                return None
    except Exception as e:
        log.debug("cover extraction failed for %s: %s", path, e)
        return None
    return None


@router.get("/library/{track_id}/cover")
async def track_cover(track_id: int):
    """Serve a library track's embedded album art, extracted once and cached
    under COVER_ART_DIR (keyed by track id). Returns 404 when the file has
    no embedded art — the browser player then renders its emoji/color
    gradient tile fallback. A ``<track_id>.none`` sentinel records the
    negative result so artless files aren't re-probed each request."""
    from domovoi.config import settings as core_settings

    cover_dir = Path(core_settings.cover_art_dir).expanduser()
    # Serve a previously-extracted image if present.
    for ext in (".jpg", ".png", ".webp", ".gif"):
        cached = cover_dir / f"{track_id}{ext}"
        if cached.is_file():
            data = cached.read_bytes()
            return Response(
                content=data,
                media_type=_ext_to_mime(ext),
                headers={"Cache-Control": "public, max-age=604800"},
            )
    if (cover_dir / f"{track_id}.none").is_file():
        raise HTTPException(status_code=404, detail="no embedded cover art")

    # Not cached yet — resolve the source file (containment-checked) and
    # probe it.
    target = await _library_file_path(track_id)
    extracted = _extract_embedded_cover(target)

    try:
        cover_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning("could not create cover_art_dir %s: %s", cover_dir, e)

    if extracted is None:
        # Record the negative so we don't re-probe on every request.
        try:
            (cover_dir / f"{track_id}.none").write_bytes(b"")
        except OSError:
            pass
        raise HTTPException(status_code=404, detail="no embedded cover art")

    data, mime = extracted
    ext = _COVER_MIME_EXT.get(mime.lower(), ".jpg")
    try:
        (cover_dir / f"{track_id}{ext}").write_bytes(data)
    except OSError as e:
        log.debug("could not cache cover for track %s: %s", track_id, e)
    return Response(
        content=data,
        media_type=_ext_to_mime(ext),
        headers={"Cache-Control": "public, max-age=604800"},
    )


def _ext_to_mime(ext: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/jpeg")


@router.post("/play-tracks")
async def play_tracks(body: CastTracksRequest):
    """Cast an arbitrary ordered queue of library tracks into a room's MPD
    (the browser player's Spotify-Connect-style hand-off). Proxies to the
    Domovoi server's ``/v1/admin/music/play-tracks``; from there, the existing
    pause/resume/skip/now-playing routes drive the room."""
    status, payload = await post_admin(
        "/v1/admin/music/play-tracks",
        {"room_id": body.room_id, "track_ids": body.track_ids},
    )
    return bridge_response(status, payload)


# ─── Helpers ───────────────────────────────────────────────────────────────


def _row_to_track(r: Any) -> Track:
    return Track(
        id=int(r[0]),
        file_path=r[1],
        title=r[2],
        artist=r[3],
        album=r[4],
        duration_sec=r[5],
        source=r[6],
        source_id=r[7],
        musicbrainz_recording_id=r[8],
        added_at=r[9],
        added_via=r[10],
        enriched_at=r[11],
        favorited=bool(r[12]),
    )


async def _list_provisioned_rooms() -> list[tuple[str, int, int]]:
    """``(room_id, control_port, http_port)`` for every row in mpd_rooms."""
    async with session_scope() as s:
        rows = await s.execute(
            text(
                "SELECT room_id, control_port, http_port "
                "FROM mpd_rooms ORDER BY room_id"
            )
        )
        return [(r[0], int(r[1]), int(r[2])) for r in rows.all()]


async def _now_playing_for(room: tuple[str, int, int]) -> NowPlaying:
    room_id, control_port, http_port = room
    from domovoi.config import settings as core_settings

    stream_url = f"{core_settings.mpd_http_base.rstrip('/')}:{http_port}"

    state, song_dict, elapsed = await _read_mpd(core_settings.mpd_host, control_port)

    song_model: NowPlayingSong | None = None
    favorited = False
    source: str | None = None
    source_url: str | None = None
    source_ref: str | None = None
    if song_dict:
        duration_raw = song_dict.get("duration") or song_dict.get("Time")
        try:
            duration_sec: int | None = int(float(duration_raw)) if duration_raw else None
        except (TypeError, ValueError):
            duration_sec = None
        song_model = NowPlayingSong(
            file=str(song_dict.get("file", "")),
            title=song_dict.get("Title") or song_dict.get("title"),
            artist=song_dict.get("Artist") or song_dict.get("artist"),
            album=song_dict.get("Album") or song_dict.get("album"),
            duration_sec=duration_sec,
        )
        favorited = await _lookup_favorited(song_model)
        source, source_url, source_ref = _lookup_source_stamp(room_id, song_model)

    return NowPlaying(
        room_id=room_id,
        state=state,
        song=song_model,
        elapsed_sec=elapsed,
        stream_url=stream_url,
        favorited=favorited,
        source=source,
        source_url=source_url,
        source_ref=source_ref,
    )


def _lookup_source_stamp(
    room_id: str, song: NowPlayingSong
) -> tuple[str | None, str | None, str | None]:
    """Resolve the room's generic now-playing SOURCE STAMP (design §4.7)
    from the cached core snapshot: ``(source, source_url, source_ref)``.

    A provider plugin stamps ``{source, data}`` on the room when it
    starts playback; the dashboard renders a provider-agnostic
    "open source ↗" pill from ``data.url`` when the stamp supplies one.
    Freshness check: for HTTP playback the stamp's ``data.stream_url``
    must match the MPD currentsong file — that's what stops a stale
    stamp from a prior provider play leaking through after a
    library/playlist playback takes over the room."""
    snapshot = get_cached_snapshot() or {}
    by_room = snapshot.get("now_playing") or {}
    entry = by_room.get(room_id)
    if not isinstance(entry, dict):
        return None, None, None
    source = entry.get("source")
    data = entry.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    file_ = song.file or ""
    if file_.startswith("http"):
        stamp_stream = data.get("stream_url")
        if stamp_stream and stamp_stream != file_:
            return None, None, None
    url = data.get("url")
    ref = data.get("ref") or data.get("id")
    return (
        str(source) if source else None,
        str(url) if url else None,
        str(ref) if ref else None,
    )


async def _lookup_favorited(song: NowPlayingSong) -> bool:
    """Resolve the favorited state of a now-playing song. Local files
    mirror the matching logic in ``favorite_now_playing`` so the heart
    icon reflects the same row the favorite button would write.
    External streams are provider territory (their plugin pages own
    richer favorite state), so they render un-filled here."""
    file_ = song.file or ""
    if not file_.startswith("http"):
        expected_path = _library_match_path(file_)
        async with session_scope() as s:
            row = await s.execute(
                text(
                    """
                    SELECT favorited
                    FROM library_tracks
                    WHERE file_path = :exact
                    LIMIT 1
                    """
                ),
                {"exact": expected_path},
            )
            match = row.first()
        return bool(match[0]) if match is not None else False
    return False


from domovoi.handlers.shared.library_match import (
    library_path_for_mpd_file as _library_match_path,
)
# Re-export under the old private name so the rest of this module's
# call sites read the same way. The implementation moved to
# ``domovoi/handlers/shared/`` so the PlaylistHandler can also
# resolve "add this now-playing track to my X playlist" without a
# circular web → domovoi → web import.


async def _favorite_library_track(
    mpd_file: str, title: str, artist: str
) -> FavoriteNowPlayingResult:
    """Match the MPD-relative path against ``library_tracks.file_path``
    (stored as absolute host paths) by reconstructing the indexer's
    path join, and flip ``favorited = TRUE``."""
    expected_path = _library_match_path(mpd_file)
    async with session_scope() as s:
        row = await s.execute(
            text(
                """
                SELECT id, title, artist, favorited
                FROM library_tracks
                WHERE file_path = :exact
                LIMIT 1
                """
            ),
            {"exact": expected_path},
        )
        result = row.first()
        if result is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"playing local file {mpd_file!r} (expected "
                    f"library_tracks.file_path = {expected_path!r}) but "
                    "no matching row — run a rescan to index it"
                ),
            )
        track_id = int(result[0])
        already = bool(result[3])
        if not already:
            await s.execute(
                text("UPDATE library_tracks SET favorited = TRUE WHERE id = :id"),
                {"id": track_id},
            )
    return FavoriteNowPlayingResult(
        kind="library",
        title=result[1] or title or None,
        artist=result[2] or artist or None,
        track_id=track_id,
        already_favorited=already,
    )


async def _favorite_external_stream(
    title: str, artist: str, room_id: str
) -> FavoriteNowPlayingResult:
    """Enqueue a generic media acquisition for ``title artist`` via the
    core's add-by-query admin endpoint (design §4.8).

    Goes through the core (rather than INSERTing the queue row here)
    because enqueue owns the dedup layers — library fuzzy match plus
    the live-queue unique index — so repeated clicks don't queue
    duplicates. With no fulfiller plugin installed the row simply waits
    ``pending`` and the graceful-absence message rides back in
    ``message`` for the toast.
    """
    query = " ".join(p for p in (title, artist) if p).strip()
    if not query:
        raise HTTPException(
            status_code=400,
            detail="playing track has no title or artist; nothing to search for",
        )
    status_code, payload = await post_admin(
        "/v1/admin/music/add-by-query",
        {"room_id": room_id, "query": query, "artist": artist or None},
    )
    if status_code == 0:
        raise HTTPException(
            status_code=502,
            detail="domovoi core unreachable; can't queue the acquisition",
        )
    if status_code >= 400 or not isinstance(payload, dict):
        detail = payload.get("detail") if isinstance(payload, dict) else str(payload)
        raise HTTPException(status_code=status_code, detail=detail or "add-by-query failed")
    return FavoriteNowPlayingResult(
        kind="acquisition",
        title=payload.get("title") or title or None,
        artist=artist or None,
        acquisition_id=payload.get("acquisition_id"),
        already_favorited=bool(payload.get("already_in_library")),
        message=payload.get("message"),
    )


async def _read_mpd(
    host: str, port: int, timeout: float = 1.5
) -> tuple[str, dict[str, Any] | None, float | None]:
    """Best-effort MPD ``status`` + ``currentsong`` read.

    Falls back to ``("stop", None, None)`` on any failure — connection
    refused, timeout, malformed response. We don't want one offline
    daemon to crash the whole now-playing list.
    """
    import asyncio

    try:
        from mpd.asyncio import MPDClient as _MPDClient  # type: ignore[import]
    except Exception as e:
        log.debug("python-mpd2 not importable: %s", e)
        return "stop", None, None

    client = _MPDClient()
    try:
        await asyncio.wait_for(client.connect(host, port), timeout=timeout)
    except Exception as e:
        log.debug("mpd connect %s:%d failed: %s", host, port, e)
        return "stop", None, None

    try:
        status = await asyncio.wait_for(client.status(), timeout=timeout)
        song = await asyncio.wait_for(client.currentsong(), timeout=timeout)
    except Exception as e:
        log.debug("mpd query %s:%d failed: %s", host, port, e)
        try:
            client.disconnect()
        except Exception:
            pass
        return "stop", None, None

    try:
        client.disconnect()
    except Exception:
        pass

    raw_state = status.get("state", "stop")
    state = raw_state if raw_state in ("play", "pause", "stop") else "stop"
    elapsed_raw = status.get("elapsed")
    try:
        elapsed = float(elapsed_raw) if elapsed_raw is not None else None
    except (TypeError, ValueError):
        elapsed = None

    return state, dict(song) if song else None, elapsed
