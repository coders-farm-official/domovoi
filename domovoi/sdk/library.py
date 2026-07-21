"""LibraryAPI — library ingest + search/dedup wrappers (design §4.10).

Plugins never hand-roll SQL against core tables; everything a media
provider needs from ``library_tracks`` / ``media_plays`` flows through
here. ``ingest_track`` is the write path the download pipeline uses:
Windows-strict sanitize/rename BEFORE the insert (dossier §7 invariant
10), file-path upsert, playlist attach, MPD update fan-out, NOTIFY on
the caller's open session (commit-coupled), and a
``core.library_track_added`` bus event.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi import library_naming, registered_values
from domovoi.clients.musicbrainz import get_musicbrainz_client
from domovoi.config import settings
from domovoi.events import EVENTS
from domovoi.handlers.shared.library_dedup import find_fuzzy_library_match
from domovoi.handlers.shared.library_match import library_path_for_mpd_file
from domovoi.handlers.shared.play_history import record_media_play

log = logging.getLogger(__name__)

_TRACK_COLUMNS = (
    "id, file_path, title, artist, album, duration_sec, source, source_id, "
    "added_via"
)


@dataclass(frozen=True)
class LibraryTrack:
    id: int
    file_path: str
    title: str | None
    artist: str | None
    album: str | None
    duration_sec: int | None
    source: str | None
    source_id: str | None
    added_via: str | None


def _row_to_track(row: Any) -> LibraryTrack:
    return LibraryTrack(
        id=int(row.id),
        file_path=row.file_path,
        title=row.title,
        artist=row.artist,
        album=row.album,
        duration_sec=int(row.duration_sec) if row.duration_sec is not None else None,
        source=row.source,
        source_id=row.source_id,
        added_via=row.added_via,
    )


class LibraryAPI:
    def __init__(self, slug: str = "core") -> None:
        self.slug = slug

    # ── Ingest (the provider download pipeline's write path) ───────────
    async def ingest_track(
        self,
        session: AsyncSession,
        *,
        file_path: Path,
        title: str,
        artist: str | None,
        source: str,
        source_id: str | None,
        added_via: str,
        attach_to_playlist_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LibraryTrack:
        """Add a media file to the library.

        1. Sanitize/rename to the canonical
           ``MUSIC_DIR/<artist>/<title>.mp3`` layout BEFORE the DB insert
           — the row and the disk never disagree (invariant 10). The
           rename never overwrites: an existing target keeps the file
           where it is.
        2. Upsert: an existing ``(source, source_id)`` row is updated in
           place; otherwise upsert on the UNIQUE ``file_path``.
        3. Soft playlist attach (skipped with a log if the playlist died).
        4. MPD update fan-out to every room (per-room DBs, no inotify).
        5. ``pg_notify('library_changed', ...)`` on the caller's session
           (commit-coupled) + ``core.library_track_added`` bus event.

        ``metadata`` may carry ``album`` / ``duration_sec``. ``added_via``
        must satisfy the core CHECK ('voice' | 'manual'); ``source`` is an
        open enum, registered here on first use (§6.4).
        """
        registered_values.register("library_source", source, owner=self.slug)
        meta = dict(metadata or {})
        album = meta.get("album")
        duration_sec = meta.get("duration_sec")

        # 1 — sanitize-before-insert.
        final_path = self._move_to_canonical(Path(file_path), title, artist)

        # 2 — upsert.
        existing = None
        if source_id:
            existing = (
                await session.execute(
                    text(
                        f"SELECT {_TRACK_COLUMNS} FROM library_tracks "
                        "WHERE source = :src AND source_id = :sid LIMIT 1"
                    ),
                    {"src": source, "sid": source_id},
                )
            ).first()
        if existing is not None:
            row = (
                await session.execute(
                    text(
                        f"""
                        UPDATE library_tracks
                        SET file_path = :fp, title = :title, artist = :artist,
                            album = COALESCE(:album, album),
                            duration_sec = COALESCE(:dur, duration_sec)
                        WHERE id = :id
                        RETURNING {_TRACK_COLUMNS}
                        """
                    ),
                    {
                        "id": int(existing.id),
                        "fp": str(final_path),
                        "title": title,
                        "artist": artist,
                        "album": album,
                        "dur": duration_sec,
                    },
                )
            ).first()
        else:
            row = (
                await session.execute(
                    text(
                        f"""
                        INSERT INTO library_tracks
                            (file_path, title, artist, album, duration_sec,
                             source, source_id, added_via)
                        VALUES
                            (:fp, :title, :artist, :album, :dur,
                             :src, :sid, :via)
                        ON CONFLICT (file_path) DO UPDATE SET
                            title = EXCLUDED.title,
                            artist = EXCLUDED.artist,
                            album = EXCLUDED.album,
                            duration_sec = EXCLUDED.duration_sec,
                            source = EXCLUDED.source,
                            source_id = EXCLUDED.source_id
                        RETURNING {_TRACK_COLUMNS}
                        """
                    ),
                    {
                        "fp": str(final_path),
                        "title": title,
                        "artist": artist,
                        "album": album,
                        "dur": duration_sec,
                        "src": source,
                        "sid": source_id,
                        "via": added_via,
                    },
                )
            ).first()
        track = _row_to_track(row)

        # 3 — soft playlist attach (NO FK — locked 5).
        if attach_to_playlist_id is not None:
            attached = await self._attach_to_playlist(
                session, attach_to_playlist_id, track.id
            )
            if not attached:
                log.info(
                    "ingest_track: playlist %s vanished before attach; skipping",
                    attach_to_playlist_id,
                )

        # 4 — MPD fan-out (best-effort; the track row is already durable).
        from domovoi.clients.mpd import iter_mpd_clients

        for room_id, mpd in iter_mpd_clients():
            try:
                await mpd.update_library()
            except Exception as e:
                log.warning(
                    "ingest_track: MPD update failed for room=%s: %s", room_id, e
                )

        # 5 — commit-coupled NOTIFY + bus event.
        await session.execute(
            text("SELECT pg_notify('library_changed', :reason)"),
            {"reason": source},
        )
        EVENTS.emit(
            "core.library_track_added",
            {
                "track_id": track.id,
                "source": track.source,
                "source_id": track.source_id,
                "file_path": track.file_path,
                "title": track.title,
                "artist": track.artist,
                "added_via": track.added_via,
            },
        )
        return track

    @staticmethod
    def _move_to_canonical(current: Path, title: str, artist: str | None) -> Path:
        """Windows-strict sanitize/rename before insert. No-ops when the
        file is already canonical, the target exists (never overwrite),
        or the rename fails (keeps the original, logs)."""
        target = library_naming.canonical_track_path(
            settings.music_dir, artist, title, ext=current.suffix or ".mp3",
        )
        try:
            if not current.exists() or target.resolve() == current.resolve():
                return current if not target.exists() else target
        except OSError:
            return current
        if target.exists():
            log.info(
                "ingest_track: canonical path %s already exists; keeping %s",
                target, current,
            )
            return current
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            current.rename(target)
        except OSError as e:
            log.warning(
                "ingest_track: rename to canonical failed (%s); keeping %s",
                e, current,
            )
            return current
        # Best-effort: drop the now-empty source directory.
        try:
            if not any(current.parent.iterdir()):
                current.parent.rmdir()
        except OSError:
            pass
        return target

    @staticmethod
    async def _attach_to_playlist(
        session: AsyncSession, playlist_id: int, track_id: int
    ) -> bool:
        exists = (
            await session.execute(
                text("SELECT 1 FROM playlists WHERE id = :pid"),
                {"pid": playlist_id},
            )
        ).first()
        if exists is None:
            return False
        await session.execute(
            text(
                """
                INSERT INTO playlist_tracks (playlist_id, track_id, position)
                VALUES (
                    :pid, :tid,
                    COALESCE((SELECT MAX(position) + 1 FROM playlist_tracks
                              WHERE playlist_id = :pid), 0)
                )
                ON CONFLICT (playlist_id, track_id) DO NOTHING
                """
            ),
            {"pid": playlist_id, "tid": track_id},
        )
        await session.execute(
            text("SELECT pg_notify('playlists_changed', 'attached')")
        )
        return True

    # ── Search / dedup wrappers ────────────────────────────────────────
    async def find_fuzzy_match(
        self, session: AsyncSession, title: str, artist: str | None = None
    ) -> LibraryTrack | None:
        """pg_trgm fuzzy dedup ("do we already have this song?") —
        wraps the shared core helper and resolves the hit to a full row."""
        match = await find_fuzzy_library_match(session, title=title, artist=artist)
        if match is None:
            return None
        row = (
            await session.execute(
                text(
                    f"SELECT {_TRACK_COLUMNS} FROM library_tracks "
                    "WHERE title = :title "
                    "AND (artist = :artist OR (:artist)::text IS NULL) "
                    "LIMIT 1"
                ),
                {"title": match["title"], "artist": match["artist"]},
            )
        ).first()
        return _row_to_track(row) if row is not None else None

    async def search(
        self, session: AsyncSession, query: str, limit: int = 10
    ) -> list[LibraryTrack]:
        """Case-insensitive substring search over title / artist /
        filename — the offline-fallback search providers use."""
        like = f"%{(query or '').strip()}%"
        if like == "%%":
            return []
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT {_TRACK_COLUMNS} FROM library_tracks
                    WHERE title ILIKE :q OR artist ILIKE :q OR file_path ILIKE :q
                    ORDER BY title NULLS LAST
                    LIMIT :limit
                    """
                ),
                {"q": like, "limit": limit},
            )
        ).all()
        return [_row_to_track(r) for r in rows]

    async def get_by_source_id(
        self, session: AsyncSession, source: str, source_id: str
    ) -> LibraryTrack | None:
        row = (
            await session.execute(
                text(
                    f"SELECT {_TRACK_COLUMNS} FROM library_tracks "
                    "WHERE source = :src AND source_id = :sid LIMIT 1"
                ),
                {"src": source, "sid": source_id},
            )
        ).first()
        return _row_to_track(row) if row is not None else None

    # ── Bridges / helpers ──────────────────────────────────────────────
    def path_for_mpd_file(self, mpd_file: str) -> Path | None:
        """The MPD-relative-URI → host-absolute-path bridge (the ONLY
        one — dossier §7 invariant 10)."""
        resolved = library_path_for_mpd_file(mpd_file)
        return Path(resolved) if resolved else None

    def parse_title_artist(self, raw_title: str) -> tuple[str, str | None]:
        """``(title, artist)`` from an "Artist - Title (modifier)"-style
        external media title; falls back to ``(raw_title, None)`` when no
        clear pattern is present."""
        artist, title = library_naming.parse_title_artist(raw_title)
        return (title or (raw_title or "").strip(), artist)

    async def record_media_play(
        self,
        session: AsyncSession,
        *,
        room_id: str,
        source: str,
        title: str | None,
        artist: str | None,
        ref: str | None = None,
    ) -> None:
        """One "Recently played" history row (best-effort, never raises).
        Registers ``source`` in the open media_play_source enum on first
        use and emits ``core.media_play_recorded``."""
        registered_values.register("media_play_source", source, owner=self.slug)
        await record_media_play(
            session, room_id=room_id, source=source, title=title,
            artist=artist, url=ref,
        )
        EVENTS.emit(
            "core.media_play_recorded",
            {"room_id": room_id, "source": source, "title": title,
             "artist": artist, "ref": ref},
        )

    async def musicbrainz(self):
        """The core MusicBrainz client (deliberate deviation from the
        dossier move-list: the library enricher depends on it, so it
        stays core and providers consume it here — design §4.10)."""
        return get_musicbrainz_client()
