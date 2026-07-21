"""Pick the next track to play for a given playlist + mode.

Used by both :class:`domovoi.handlers.music.MusicHandler` 's
``_smart_skip`` and the core's ``/v1/admin/music/play-playlist``
admin endpoint so the SQL stays in one place.

Two modes:

* ``ordered`` — playback follows the ``position`` column.
  Successive ``next`` calls advance to the smallest
  ``position > last_position``. End of playlist loops back to
  the lowest-position row (per the user's "loop at end" decision).
* ``shuffle`` — pick a random row from the playlist, excluding
  ``last_track_id`` when the playlist has more than one row so we
  don't get back-to-back repeats.

A special case: ``playlist_id == 0`` is the virtual Favorites view
backed by ``library_tracks WHERE favorited`` — no
``playlist_tracks`` rows exist. Ordered mode there uses
``library_tracks.added_at ASC`` (rough proxy for "the order I
favorited them in" — accurate enough until we add a
``favorited_at`` column).
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PlaylistMode = Literal["ordered", "shuffle"]


class PickedTrack:
    """Result type for :func:`pick_next_track`.

    Slim deliberately — callers want title/artist/file_path to hand
    to MPD plus the ``position`` to stamp back into the per-room
    ``current_playlist`` state. ``track_id`` lets shuffle dedup
    against the immediately-previous track on the *next* call.
    """

    __slots__ = ("track_id", "title", "artist", "file_path", "position")

    def __init__(
        self,
        *,
        track_id: int,
        title: str | None,
        artist: str | None,
        file_path: str,
        position: int | None,
    ) -> None:
        self.track_id = track_id
        self.title = title
        self.artist = artist
        self.file_path = file_path
        self.position = position


async def read_resume_position(
    session: AsyncSession, playlist_id: int, mode: str
) -> int | None:
    """The saved ordered-mode resume point, or None. Always None for
    shuffle (its contract is 'pick fresh') and Favorites (id 0 has no
    playlists row)."""
    if mode != "ordered" or playlist_id == 0:
        return None
    row = (
        await session.execute(
            text("SELECT resume_position FROM playlists WHERE id = :id"),
            {"id": playlist_id},
        )
    ).first()
    return int(row[0]) if row is not None and row[0] is not None else None


async def persist_resume_position(
    session: AsyncSession, playlist_id: int, mode: str, position: int | None
) -> None:
    """Save the ordered-mode playback position so re-opening the playlist
    continues from here. No-op for shuffle / Favorites / a null position."""
    if mode != "ordered" or playlist_id == 0 or position is None:
        return
    await session.execute(
        text("UPDATE playlists SET resume_position = :pos WHERE id = :id"),
        {"pos": int(position), "id": playlist_id},
    )


async def pick_next_track(
    *,
    session: AsyncSession,
    playlist_id: int,
    mode: PlaylistMode,
    last_track_id: int | None = None,
    last_position: int | None = None,
) -> PickedTrack | None:
    """Resolve the next track for ``playlist_id`` under ``mode``.

    ``last_track_id`` and ``last_position`` are about the
    *currently-playing* track (or the one that just finished); the
    returned :class:`PickedTrack` describes what should play next.
    Returns ``None`` if the playlist has no tracks at all.
    """
    if playlist_id == 0:
        return await _pick_favorites(
            session=session,
            mode=mode,
            last_track_id=last_track_id,
            last_position=last_position,
        )
    return await _pick_real_playlist(
        session=session,
        playlist_id=playlist_id,
        mode=mode,
        last_track_id=last_track_id,
        last_position=last_position,
    )


async def _pick_real_playlist(
    *,
    session: AsyncSession,
    playlist_id: int,
    mode: PlaylistMode,
    last_track_id: int | None,
    last_position: int | None,
) -> PickedTrack | None:
    if mode == "ordered":
        params: dict[str, Any] = {"pid": playlist_id}
        # First: anything with position > last_position. If
        # last_position is None, start at the lowest position.
        if last_position is not None:
            where_extra = "AND pt.position > :last_pos"
            params["last_pos"] = last_position
        else:
            where_extra = ""
        row = (
            await session.execute(
                text(
                    f"""
                    SELECT lt.id, lt.title, lt.artist, lt.file_path, pt.position
                    FROM playlist_tracks pt
                    JOIN library_tracks lt ON pt.track_id = lt.id
                    WHERE pt.playlist_id = :pid
                      {where_extra}
                    ORDER BY pt.position ASC
                    LIMIT 1
                    """
                ),
                params,
            )
        ).first()
        if row is None and last_position is not None:
            # End of playlist — loop back to the lowest position.
            row = (
                await session.execute(
                    text(
                        """
                        SELECT lt.id, lt.title, lt.artist, lt.file_path, pt.position
                        FROM playlist_tracks pt
                        JOIN library_tracks lt ON pt.track_id = lt.id
                        WHERE pt.playlist_id = :pid
                        ORDER BY pt.position ASC
                        LIMIT 1
                        """
                    ),
                    {"pid": playlist_id},
                )
            ).first()
        return _row_to_picked(row)

    # Shuffle: random row, avoid back-to-back repeat when possible.
    params = {"pid": playlist_id}
    where_extra = ""
    if last_track_id is not None:
        where_extra = "AND lt.id != :exclude_id"
        params["exclude_id"] = last_track_id
    row = (
        await session.execute(
            text(
                f"""
                SELECT lt.id, lt.title, lt.artist, lt.file_path, pt.position
                FROM playlist_tracks pt
                JOIN library_tracks lt ON pt.track_id = lt.id
                WHERE pt.playlist_id = :pid
                  {where_extra}
                ORDER BY RANDOM()
                LIMIT 1
                """
            ),
            params,
        )
    ).first()
    if row is None and last_track_id is not None:
        # Single-track playlist — allow the repeat rather than
        # returning None (which would make the caller think the
        # playlist is empty).
        row = (
            await session.execute(
                text(
                    """
                    SELECT lt.id, lt.title, lt.artist, lt.file_path, pt.position
                    FROM playlist_tracks pt
                    JOIN library_tracks lt ON pt.track_id = lt.id
                    WHERE pt.playlist_id = :pid
                    ORDER BY RANDOM()
                    LIMIT 1
                    """
                ),
                {"pid": playlist_id},
            )
        ).first()
    return _row_to_picked(row)


async def _pick_favorites(
    *,
    session: AsyncSession,
    mode: PlaylistMode,
    last_track_id: int | None,
    last_position: int | None,
) -> PickedTrack | None:
    """Virtual Favorites. ``position`` mirrors ``library_tracks.id``
    (monotonic, stable for the lifetime of a row) so the per-room
    ``current_playlist`` state's ``last_position`` can be a single
    int regardless of whether we're in a real playlist or this
    virtual one."""
    if mode == "ordered":
        if last_position is not None:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT id, title, artist, file_path
                        FROM library_tracks
                        WHERE favorited AND id > :last_pos
                        ORDER BY id ASC
                        LIMIT 1
                        """
                    ),
                    {"last_pos": last_position},
                )
            ).first()
            if row is None:
                row = (
                    await session.execute(
                        text(
                            """
                            SELECT id, title, artist, file_path
                            FROM library_tracks
                            WHERE favorited
                            ORDER BY id ASC
                            LIMIT 1
                            """
                        )
                    )
                ).first()
        else:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT id, title, artist, file_path
                        FROM library_tracks
                        WHERE favorited
                        ORDER BY id ASC
                        LIMIT 1
                        """
                    )
                )
            ).first()
    else:
        # Shuffle favorites.
        params: dict[str, Any] = {}
        where_extra = ""
        if last_track_id is not None:
            where_extra = "AND id != :exclude_id"
            params["exclude_id"] = last_track_id
        row = (
            await session.execute(
                text(
                    f"""
                    SELECT id, title, artist, file_path
                    FROM library_tracks
                    WHERE favorited {where_extra}
                    ORDER BY RANDOM()
                    LIMIT 1
                    """
                ),
                params,
            )
        ).first()
        if row is None and last_track_id is not None:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT id, title, artist, file_path
                        FROM library_tracks
                        WHERE favorited
                        ORDER BY RANDOM()
                        LIMIT 1
                        """
                    )
                )
            ).first()
    if row is None:
        return None
    return PickedTrack(
        track_id=int(row[0]),
        title=row[1],
        artist=row[2],
        file_path=row[3],
        position=int(row[0]),  # for favorites, position == id (stable)
    )


def _row_to_picked(row: Any) -> PickedTrack | None:
    if row is None:
        return None
    return PickedTrack(
        track_id=int(row[0]),
        title=row[1],
        artist=row[2],
        file_path=row[3],
        position=int(row[4]) if row[4] is not None else None,
    )
