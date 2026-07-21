"""PlaybackAPI — the one-call "play this URL in room X" (design §4.10).

Encapsulates the incident-hardened play coordination (dossier §7
invariant 9) so plugins never hand-assemble it:

* ``prepare_url`` queues the stream in the room's MPD **paused** with
  title/artist stamped (the station-name-as-title convention downstream
  favorites matching depends on);
* the returned :class:`~domovoi.models.Response` carries
  ``music_action="start"`` + the room's MPD http stream URL — the
  streaming layer (or ``_admin_dispatch_music``) reads those and runs
  the ``music_start`` → ``music_ready`` handshake, the
  ``expect_followup`` suppression, and the ``resumable_music`` arming.
  Those all stay core-internal: a handler returns this Response AS-IS.
* the room's now-playing stamp is placed (source must be a registered
  now-playing source, §4.7) and one ``media_plays`` history row is
  recorded (best-effort).
"""

from __future__ import annotations

import logging
from typing import Any

from domovoi import registered_values
from domovoi.clients.mpd import (
    MPDClient,
    get_mpd_client_for,
    iter_mpd_clients,
    mpd_stream_url_for,
)
from domovoi.db.session import session_scope
from domovoi.handlers.shared.play_history import record_media_play
from domovoi.models import Response
from domovoi.now_playing import NOW_PLAYING

log = logging.getLogger(__name__)


class PlaybackAPI:
    def __init__(self, slug: str = "core") -> None:
        self.slug = slug

    async def play_url(
        self,
        room_id: str,
        stream_url: str,
        *,
        title: str,
        artist: str | None = None,
        source: str,                          # registered now-playing source slug
        now_playing_data: dict[str, Any] | None = None,
        record_play: bool = True,
        play_ref: str | None = None,
    ) -> Response:
        """Queue ``stream_url`` in the room's MPD and return a
        fully-populated Response the handler returns as-is. On failure
        the Response has no ``music_action`` and a spoken fallback text
        (callers may overwrite ``text`` either way)."""
        # Fail fast on an unregistered source — same rule as stamp() (§4.7).
        if source not in NOW_PLAYING.sources():
            raise ValueError(
                f"play_url: {source!r} is not a registered now-playing source"
            )
        ok = False
        try:
            ok = await get_mpd_client_for(room_id).prepare_url(
                stream_url, title=title or None, artist=artist or None
            )
        except Exception as e:
            log.warning("play_url: MPD prepare_url raised for room=%s: %s", room_id, e)
        if not ok:
            return Response(
                text=(
                    f"I found {title}, but the music player wouldn't "
                    "accept the stream."
                ),
                data={"stream_url": stream_url, "ok": False},
            )

        # Now-playing stamp — data carries "stream_url" by convention (the
        # sweeper's freshness key) and "title" for the dashboard card.
        data = dict(now_playing_data or {})
        data.setdefault("stream_url", stream_url)
        data.setdefault("title", title)
        if play_ref is not None:
            data.setdefault("ref", play_ref)
        try:
            NOW_PLAYING.stamp(room_id, source, data)
        except Exception as e:
            log.warning("play_url: now-playing stamp failed: %s", e)

        if record_play:
            # Open-enum registration on first use, then a best-effort
            # media_plays row on a short-lived session (never fails the play).
            registered_values.register(
                "media_play_source", source, owner=self.slug
            )
            try:
                async with session_scope() as s:
                    await record_media_play(
                        s,
                        room_id=room_id,
                        source=source,
                        title=title,
                        artist=artist,
                        url=play_ref,
                        stream_url=stream_url,
                    )
            except Exception as e:
                log.debug("play_url: media_plays record failed: %s", e)

        label = f"{title} by {artist}" if artist else title
        return Response(
            text=f"Playing {label}.",
            data={"title": title, "artist": artist, "source": source,
                  "stream_url": stream_url, "ok": True},
            music_action="start",
            music_stream_url=mpd_stream_url_for(room_id),
        )

    async def stop(self, room_id: str) -> Response:
        """Stop the room's MPD + clear its now-playing stamp; the returned
        Response's ``music_action="stop"`` makes the streaming layer send
        the Pi its ``music_stop`` frame and drop ``resumable_music``."""
        try:
            await get_mpd_client_for(room_id).stop()
        except Exception as e:
            log.warning("playback.stop: MPD stop failed for room=%s: %s", room_id, e)
        NOW_PLAYING.clear(room_id)
        return Response(text="Stopped.", music_action="stop")

    async def mpd_client_for(self, room_id: str) -> MPDClient:
        """Escape hatch (search_only, seek, ...) — same client the core
        media handlers use."""
        return get_mpd_client_for(room_id)

    def mpd_stream_url_for(self, room_id: str) -> str:
        return mpd_stream_url_for(room_id)

    async def update_library_all_rooms(self) -> None:
        """Explicit MPD update fan-out. Each per-room MPD has its own
        database file AND Docker Desktop drops host inotify events, so
        after writing files under MUSIC_DIR every daemon must be told to
        rescan (dossier §7 invariant 10)."""
        for room_id, mpd in iter_mpd_clients():
            try:
                await mpd.update_library()
            except Exception as e:
                log.warning(
                    "update_library_all_rooms: room=%s failed: %s", room_id, e
                )
