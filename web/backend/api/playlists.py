"""Playlists API — CRUD + Favorites virtual playlist bridging.

Routes are read-first (`GET /api/playlists`, `GET /api/playlists/{id}`,
`GET /api/library/{id}/playlists` — that last one lives in
``music.py`` since it sits under ``/api/music``-adjacent URL space).
Mutation routes (`POST`, `PATCH`, `DELETE`) fire
``pg_notify('playlists_changed', '<reason>')`` in the same
transaction as the write so the dashboard's realtime layer can
refresh sub-second instead of waiting for the next 1.5 s poll
tick — mirrors the calendar / radio / downloads notify pattern
already in place.

The "Favorites" virtual playlist (``id == 0``) is special: it has
no row in ``playlists``. Reads return library_tracks WHERE
favorited as if it were a playlist; the one mutation it accepts
(``DELETE /api/playlists/0/tracks/{track_id}``) flips
``library_tracks.favorited = FALSE``. Everything else against
``id == 0`` returns 400.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from web.backend.db import session_scope
from web.backend.schemas import (
    Playlist,
    PlaylistCreate,
    PlaylistPatch,
    PlaylistReorder,
    PlaylistTrackAdd,
    Track,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


FAVORITES_VIRTUAL_ID = 0
FAVORITES_VIRTUAL_NAME = "Favorites"

# Editable via PATCH. Keys are also spliced into the SET clause, so this
# allowlist is the injection guard — never widen it to raw user input.
_PATCH_ALLOWED = ("name", "description", "cover_color", "cover_emoji")
# cover_color is rendered into a CSS style on the client; constrain it to a
# color-ish charset (hex / oklch(...) / rgb(...)) with no ';' or ':' so it
# can't break out of the style attribute.
_COLOR_RE = re.compile(r"^[#A-Za-z0-9().,%/ -]{0,64}$")


def _playlist_from_row(r: Any, *, track_count: int) -> Playlist:
    """Build a Playlist from a row selecting
    (id, name, created_at, description, cover_color, cover_emoji)."""
    return Playlist(
        id=int(r[0]),
        name=r[1],
        created_at=r[2],
        description=r[3],
        cover_color=r[4],
        cover_emoji=r[5],
        track_count=track_count,
        is_virtual=False,
    )


# ─── List + per-playlist read ────────────────────────────────────────────


@router.get("", response_model=list[Playlist])
async def list_playlists() -> list[Playlist]:
    """Every playlist plus the virtual Favorites row pinned at the
    top. Track counts computed server-side so the tab never has to
    iterate."""
    async with session_scope() as s:
        fav_count = (
            await s.execute(
                text("SELECT COUNT(*) FROM library_tracks WHERE favorited")
            )
        ).scalar_one()
        rows = await s.execute(
            text(
                """
                SELECT p.id, p.name, p.created_at,
                       p.description, p.cover_color, p.cover_emoji,
                       COALESCE(c.track_count, 0)::bigint AS track_count
                FROM playlists p
                LEFT JOIN (
                    SELECT playlist_id, COUNT(*) AS track_count
                    FROM playlist_tracks
                    GROUP BY playlist_id
                ) c ON c.playlist_id = p.id
                ORDER BY LOWER(p.name) ASC
                """
            )
        )
    favorites = Playlist(
        id=FAVORITES_VIRTUAL_ID,
        name=FAVORITES_VIRTUAL_NAME,
        track_count=int(fav_count),
        created_at=None,
        is_virtual=True,
    )
    real = [
        _playlist_from_row(r, track_count=int(r[6])) for r in rows.all()
    ]
    return [favorites] + real


@router.get("/{playlist_id}/tracks", response_model=list[Track])
async def list_playlist_tracks(playlist_id: int) -> list[Track]:
    """Tracks in a playlist, in playback order.

    Real playlists: ``position ASC``.
    Virtual Favorites: ``library_tracks.id ASC`` (proxy for
    chronological since there's no ``favorited_at`` column today).
    """
    async with session_scope() as s:
        if playlist_id == FAVORITES_VIRTUAL_ID:
            rows = await s.execute(
                text(
                    """
                    SELECT id, file_path, title, artist, album, duration_sec,
                           source, source_id, musicbrainz_recording_id,
                           added_at, added_via, enriched_at, favorited
                    FROM library_tracks
                    WHERE favorited
                    ORDER BY id ASC
                    """
                )
            )
        else:
            # 404 if the playlist doesn't exist, so the dashboard
            # doesn't render an empty drawer for a deleted row.
            exists = (
                await s.execute(
                    text("SELECT 1 FROM playlists WHERE id = :id"),
                    {"id": playlist_id},
                )
            ).first()
            if exists is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"playlist {playlist_id} not found",
                )
            rows = await s.execute(
                text(
                    """
                    SELECT lt.id, lt.file_path, lt.title, lt.artist, lt.album,
                           lt.duration_sec, lt.source, lt.source_id,
                           lt.musicbrainz_recording_id, lt.added_at,
                           lt.added_via, lt.enriched_at, lt.favorited
                    FROM playlist_tracks pt
                    JOIN library_tracks lt ON pt.track_id = lt.id
                    WHERE pt.playlist_id = :id
                    ORDER BY pt.position ASC
                    """
                ),
                {"id": playlist_id},
            )
    return [_row_to_track(r) for r in rows.all()]


# ─── Create / rename / delete ────────────────────────────────────────────


@router.post("", response_model=Playlist, status_code=201)
async def create_playlist(payload: PlaylistCreate) -> Playlist:
    """Create a new playlist. Case-insensitive duplicate names are
    rejected by the ``idx_playlists_name_ci`` unique index — we
    pre-check here to return a friendly 409 instead of an IntegrityError."""
    async with session_scope() as s:
        dup = (
            await s.execute(
                text("SELECT id FROM playlists WHERE LOWER(name) = LOWER(:n)"),
                {"n": payload.name},
            )
        ).first()
        if dup is not None:
            raise HTTPException(
                status_code=409,
                detail=f"playlist named {payload.name!r} already exists (id={int(dup[0])})",
            )
        row = await s.execute(
            text(
                """
                INSERT INTO playlists (name)
                VALUES (:n)
                RETURNING id, name, created_at,
                          description, cover_color, cover_emoji
                """
            ),
            {"n": payload.name},
        )
        result = row.first()
        await s.execute(text("SELECT pg_notify('playlists_changed', 'created')"))
    if result is None:
        raise HTTPException(status_code=500, detail="insert returned no row")
    return _playlist_from_row(result, track_count=0)


@router.patch("/{playlist_id}", response_model=Playlist)
async def update_playlist(playlist_id: int, payload: PlaylistPatch) -> Playlist:
    """Edit a playlist's name and/or presentation (description, cover
    color/emoji). Any subset of the allowed fields may be sent."""
    if playlist_id == FAVORITES_VIRTUAL_ID:
        raise HTTPException(
            status_code=400,
            detail="the Favorites playlist is virtual and can't be edited",
        )
    updates = {
        k: v
        for k, v in payload.model_dump(exclude_unset=True).items()
        if k in _PATCH_ALLOWED
    }
    if not updates:
        raise HTTPException(status_code=400, detail="no editable fields provided")
    if updates.get("cover_color") and not _COLOR_RE.match(updates["cover_color"]):
        raise HTTPException(
            status_code=400,
            detail="cover_color must be a color value (hex, rgb(), oklch())",
        )
    async with session_scope() as s:
        if "name" in updates:
            dup = (
                await s.execute(
                    text(
                        "SELECT id FROM playlists "
                        "WHERE LOWER(name) = LOWER(:n) AND id != :id"
                    ),
                    {"n": updates["name"], "id": playlist_id},
                )
            ).first()
            if dup is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"another playlist already uses the name {updates['name']!r}",
                )
        # Dynamic SET — keys come only from _PATCH_ALLOWED (not user text),
        # values are bound, so this is injection-safe.
        set_sql = ", ".join(f"{k} = :{k}" for k in updates) + ", updated_at = NOW()"
        params: dict[str, Any] = {**updates, "id": playlist_id}
        row = await s.execute(
            text(
                f"""
                UPDATE playlists SET {set_sql}
                WHERE id = :id
                RETURNING id, name, created_at, description, cover_color,
                          cover_emoji,
                          (SELECT COUNT(*) FROM playlist_tracks
                           WHERE playlist_id = :id)::bigint AS track_count
                """
            ),
            params,
        )
        result = row.first()
        if result is not None:
            await s.execute(text("SELECT pg_notify('playlists_changed', 'updated')"))
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"playlist {playlist_id} not found"
        )
    return _playlist_from_row(result, track_count=int(result[6]))


@router.patch("/{playlist_id}/order", status_code=204)
async def reorder_playlist(playlist_id: int, payload: PlaylistReorder) -> None:
    """Full-order rewrite — ``track_ids`` is the playlist's tracks in the
    new order. The submitted set must equal the playlist's current set
    (no adds/removes here). Positions are rewritten 0..n-1 in one
    statement; the no-UNIQUE-on-position design makes this safe."""
    if playlist_id == FAVORITES_VIRTUAL_ID:
        raise HTTPException(
            status_code=400, detail="the Favorites playlist can't be reordered"
        )
    track_ids = payload.track_ids
    async with session_scope() as s:
        current = {
            int(r[0])
            for r in (
                await s.execute(
                    text("SELECT track_id FROM playlist_tracks WHERE playlist_id = :id"),
                    {"id": playlist_id},
                )
            ).all()
        }
        if not current:
            raise HTTPException(
                status_code=404,
                detail=f"playlist {playlist_id} not found or empty",
            )
        if set(track_ids) != current:
            raise HTTPException(
                status_code=400,
                detail="track_ids must be exactly the playlist's current tracks",
            )
        # UPDATE ... FROM (VALUES (tid,pos), ...) — one statement.
        values_sql = ", ".join(f"(:t{i}, {i})" for i in range(len(track_ids)))
        params: dict[str, Any] = {"id": playlist_id}
        for i, tid in enumerate(track_ids):
            params[f"t{i}"] = tid
        await s.execute(
            text(
                f"""
                UPDATE playlist_tracks AS pt
                SET position = v.pos
                FROM (VALUES {values_sql}) AS v(tid, pos)
                WHERE pt.playlist_id = :id AND pt.track_id = v.tid
                """
            ),
            params,
        )
        await s.execute(
            text("UPDATE playlists SET updated_at = NOW() WHERE id = :id"),
            {"id": playlist_id},
        )
        await s.execute(text("SELECT pg_notify('playlists_changed', 'reordered')"))


@router.delete("/{playlist_id}", status_code=204)
async def delete_playlist(playlist_id: int) -> None:
    if playlist_id == FAVORITES_VIRTUAL_ID:
        raise HTTPException(
            status_code=400,
            detail="the Favorites playlist is virtual and can't be deleted",
        )
    async with session_scope() as s:
        result = await s.execute(
            text("DELETE FROM playlists WHERE id = :id"),
            {"id": playlist_id},
        )
        if (result.rowcount or 0) > 0:
            await s.execute(text("SELECT pg_notify('playlists_changed', 'deleted')"))
    if (result.rowcount or 0) == 0:
        raise HTTPException(
            status_code=404, detail=f"playlist {playlist_id} not found"
        )


# ─── Membership: add / remove ─────────────────────────────────────────────


@router.post("/{playlist_id}/tracks", status_code=204)
async def add_track_to_playlist(
    playlist_id: int, payload: PlaylistTrackAdd
) -> None:
    """Append a library track to the end of a playlist.

    Refused for the virtual Favorites playlist (``id == 0``) —
    favoriting is done via ``PATCH /api/music/library/{id}
    {favorited: true}``. Refusing here avoids two equally-valid
    code paths writing the same flag.
    """
    if playlist_id == FAVORITES_VIRTUAL_ID:
        raise HTTPException(
            status_code=400,
            detail=(
                "use PATCH /api/music/library/{track_id} {favorited: true} "
                "to favorite a track"
            ),
        )
    async with session_scope() as s:
        # Existence guards so we return clean 404s, not 23503
        # foreign-key violations.
        playlist_exists = (
            await s.execute(
                text("SELECT 1 FROM playlists WHERE id = :id"),
                {"id": playlist_id},
            )
        ).first()
        if playlist_exists is None:
            raise HTTPException(
                status_code=404, detail=f"playlist {playlist_id} not found"
            )
        track_exists = (
            await s.execute(
                text("SELECT 1 FROM library_tracks WHERE id = :id"),
                {"id": payload.track_id},
            )
        ).first()
        if track_exists is None:
            raise HTTPException(
                status_code=404, detail=f"track {payload.track_id} not found"
            )
        # Dup check (the unique constraint would error anyway, but a
        # 409 reads cleaner than a 500 to the dashboard).
        dup = (
            await s.execute(
                text(
                    "SELECT 1 FROM playlist_tracks "
                    "WHERE playlist_id = :pid AND track_id = :tid"
                ),
                {"pid": playlist_id, "tid": payload.track_id},
            )
        ).first()
        if dup is not None:
            raise HTTPException(
                status_code=409,
                detail=f"track {payload.track_id} is already in this playlist",
            )
        # Append at the end. The COALESCE on MAX handles the empty-
        # playlist case (-1 + 1 = 0 → first row starts at position 0).
        await s.execute(
            text(
                """
                INSERT INTO playlist_tracks (playlist_id, track_id, position)
                VALUES (
                    :pid,
                    :tid,
                    COALESCE(
                        (SELECT MAX(position) + 1 FROM playlist_tracks WHERE playlist_id = :pid),
                        0
                    )
                )
                """
            ),
            {"pid": playlist_id, "tid": payload.track_id},
        )
        await s.execute(
            text(
                "UPDATE playlists SET updated_at = NOW() WHERE id = :id"
            ),
            {"id": playlist_id},
        )
        await s.execute(text("SELECT pg_notify('playlists_changed', 'added')"))


@router.delete("/{playlist_id}/tracks/{track_id}", status_code=204)
async def remove_track_from_playlist(playlist_id: int, track_id: int) -> None:
    """Remove a track from a playlist.

    For the virtual Favorites playlist (``id == 0``), this flips
    ``library_tracks.favorited = FALSE`` instead — so unfavoriting
    from the playlist drawer and unfavoriting via the heart on the
    library row both end up in the same place.
    """
    async with session_scope() as s:
        if playlist_id == FAVORITES_VIRTUAL_ID:
            result = await s.execute(
                text(
                    "UPDATE library_tracks "
                    "SET favorited = FALSE "
                    "WHERE id = :id AND favorited"
                ),
                {"id": track_id},
            )
            if (result.rowcount or 0) > 0:
                await s.execute(
                    text("SELECT pg_notify('playlists_changed', 'unfavorited')")
                )
            if (result.rowcount or 0) == 0:
                raise HTTPException(
                    status_code=404,
                    detail=f"track {track_id} is not currently favorited",
                )
            return
        result = await s.execute(
            text(
                "DELETE FROM playlist_tracks "
                "WHERE playlist_id = :pid AND track_id = :tid"
            ),
            {"pid": playlist_id, "tid": track_id},
        )
        if (result.rowcount or 0) > 0:
            await s.execute(
                text(
                    "UPDATE playlists SET updated_at = NOW() WHERE id = :id"
                ),
                {"id": playlist_id},
            )
            await s.execute(
                text("SELECT pg_notify('playlists_changed', 'removed')")
            )
    if (result.rowcount or 0) == 0:
        raise HTTPException(
            status_code=404,
            detail=(
                f"track {track_id} not in playlist {playlist_id} "
                "(or playlist doesn't exist)"
            ),
        )


# ─── Helpers ──────────────────────────────────────────────────────────────


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
