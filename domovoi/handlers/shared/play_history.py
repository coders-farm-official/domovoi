"""Record what a room plays into ``media_plays``.

One row per play-start, written from every site that emits a
``music_action="start"`` Response — the core media handlers, provider
plugins, and the UI-initiated admin play endpoints. The web
dashboard's Satellites drawer reads it back as the "Recently played"
tab (domovoi owns the writes; the web backend only reads).

Deliberately best-effort: a history-write failure must NEVER break
playback, so every call is wrapped in try/except and only logged. This
mirrors the now-playing / current_playlist / last_played_track
stamps each call sits beside.

Consecutive-dedup: ``_stream_to_mpd`` and the playlist smart-skip fire
on every stream (re)start, including a "next" that re-streams the same
track or a double-clicked UI play. To keep the tab readable we skip an
insert when the room's most recent row is the SAME item within a short
window. A genuine replay minutes later still records a fresh row.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Suppress a repeat of the identical item within this many seconds
# (rapid double-fire / "next" re-stream), not a deliberate later replay.
_DEDUP_WINDOW_SEC = 10.0


async def record_media_play(
    session: AsyncSession,
    *,
    room_id: str,
    source: str,
    title: str | None = None,
    artist: str | None = None,
    channel: str | None = None,
    video_id: str | None = None,
    url: str | None = None,
    stream_url: str | None = None,
    library_track_id: int | None = None,
    duration_sec: int | None = None,
) -> None:
    """Insert one ``media_plays`` row for a play-start, best-effort.

    ``source`` is an OPEN enum (registered_values domain
    'media_play_source' — core stamps library / playlist / spoken_audio;
    plugins register their own). Never raises — logs and returns on any
    failure so a history-write can't take playback down with it.
    """
    if not room_id or not source:
        return
    try:
        # Consecutive-dedup: compare the room's most recent row's identity
        # tuple (source + video_id + library_track_id + title) to this one.
        last = (
            await session.execute(
                text(
                    """
                    SELECT source, video_id, library_track_id, title,
                           started_at > NOW() - make_interval(secs => :win)
                               AS recent
                    FROM media_plays
                    WHERE room_id = :room_id
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
                ),
                {"room_id": room_id, "win": _DEDUP_WINDOW_SEC},
            )
        ).first()
        if (
            last is not None
            and last.recent
            and last.source == source
            and last.video_id == video_id
            and last.library_track_id == library_track_id
            and last.title == title
        ):
            return

        await session.execute(
            text(
                """
                INSERT INTO media_plays (
                    room_id, source, title, artist, channel, video_id,
                    url, stream_url, library_track_id, duration_sec
                ) VALUES (
                    :room_id, :source, :title, :artist, :channel, :video_id,
                    :url, :stream_url, :library_track_id, :duration_sec
                )
                """
            ),
            {
                "room_id": room_id,
                "source": source,
                "title": title,
                "artist": artist,
                "channel": channel,
                "video_id": video_id,
                "url": url,
                "stream_url": stream_url,
                "library_track_id": library_track_id,
                "duration_sec": duration_sec,
            },
        )
    except Exception as e:  # noqa: BLE001 — never break playback over history
        log.debug("record_media_play(%s/%s) failed: %s", room_id, source, e)
