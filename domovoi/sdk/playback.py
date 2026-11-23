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
* a streaming-search provider's song arms the room's Up next, which
  carries the music on from the library when the song ends
  (:func:`domovoi.up_next.rooms.follow_stream`; a radio station's
  stream does not).

A voice turn's Response rides home on the turn's own socket. Code that
plays or stops OUTSIDE a voice turn — a plugin's HTTP route (a page's
"play in the bedroom" button), a worker (a sleep timer running out) —
has no such socket, so it hands the Response to :meth:`PlaybackAPI.dispatch`,
which sends the room's satellite its ``music_start`` / ``music_stop`` the
way the dashboard's own room controls do (``domovoi.room_music``). Without
it the room's MPD sits paused and the room stays silent (plugin board
F-P017).

Under ``INTERNET_ACCESS=never`` a stream URL whose host is not on this
network is refused BEFORE it reaches MPD: the room's MPD container fetches
whatever it is given, and no in-process gate can see that traffic. The
refusal is a Response with no ``music_action`` and
``data["status"] == "internet_off"``.
"""

from __future__ import annotations

import logging
from typing import Any

from domovoi import egress, registered_values
from domovoi.clients.mpd import (
    MPDClient,
    get_mpd_client_for,
    iter_mpd_clients,
    mpd_stream_url_for,
)
from domovoi.db.session import session_scope
from domovoi.handlers.shared.play_history import record_media_play
from domovoi.models import Response
from domovoi.player_route import PlayerRoute, player_route
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
        # The room's MPD would fetch an internet stream itself, out of
        # sight of every in-process gate: refuse it here under never. A
        # relay URL (relay_url) is judged by the remote URL it stands for.
        from domovoi import player_relay

        if egress.check_destination(player_relay.target_for(stream_url) or stream_url) is not None:
            egress.log_refusal(f"music stream {stream_url[:120]}")
            return Response(
                text=(
                    f"{egress.spoken_offline_phrase()}, so I can't play "
                    f"{title or 'that'}."
                ),
                data={"stream_url": stream_url, "ok": False, "status": "internet_off"},
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

        if self._streams_songs():
            await self._follow_with_up_next(room_id, title, artist, data)

        label = f"{title} by {artist}" if artist else title
        return Response(
            text=f"Playing {label}.",
            data={"title": title, "artist": artist, "source": source,
                  "stream_url": stream_url, "ok": True},
            music_action="start",
            music_stream_url=mpd_stream_url_for(room_id),
        )

    def _streams_songs(self) -> bool:
        """Is this plugin a streaming-search provider? Its plays are songs,
        which Up next carries on from when they end; a radio station's are
        not."""
        from domovoi.capabilities import CAPABILITIES, STREAMING_SEARCH_PROVIDER

        return self.slug in CAPABILITIES.providers_for(STREAMING_SEARCH_PROVIDER)

    async def _follow_with_up_next(
        self, room_id: str, title: str, artist: str | None, data: dict[str, Any],
    ) -> None:
        """Arm the room's Up next to carry on from the library when this
        song ends (domovoi/up_next/rooms.py ``follow_stream``). Best-effort:
        the stream plays regardless."""
        try:
            from domovoi.up_next import rooms as up_next_rooms

            by = artist or data.get("artist") or data.get("channel")
            await up_next_rooms.follow_stream(
                room_id, get_mpd_client_for(room_id), title=title,
                artist=str(by) if by else None,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("play_url: up next won't follow room=%s: %s", room_id, e)

    async def stop(self, room_id: str) -> Response:
        """Stop the room's MPD + clear its now-playing stamp; the returned
        Response's ``music_action="stop"`` makes the streaming layer send
        the Pi its ``music_stop`` frame and drop ``resumable_music``.

        A room that shares a queue (a group, domovoi/music_groups.py) stops
        alone: it leaves the group and the other rooms play on."""
        from domovoi import music_groups, player_relay

        # A relay serving the room ends with the music (SDK 1.16).
        player_relay.revoke_room(room_id)
        if music_groups.share_of(room_id) is not None:
            from domovoi.plugins_runtime.loader import LOADER

            await music_groups.leave(LOADER.app, room_id)
            return Response(text="Stopped.", music_action="stop")
        try:
            await get_mpd_client_for(room_id).stop()
        except Exception as e:
            log.warning("playback.stop: MPD stop failed for room=%s: %s", room_id, e)
        NOW_PLAYING.clear(room_id)
        return Response(text="Stopped.", music_action="stop")

    async def dispatch(self, response: Response, room_id: str) -> bool:
        """Send the room's satellite what ``response`` asks for, when the
        play or stop did NOT come from a voice turn: a plugin's HTTP route
        or a worker. ``music_action="start"`` → ``music_start`` (and, with
        no satellite connected, MPD is resumed so a later connect joins a
        live stream); ``"stop"`` → ``music_stop``. Anything else (a refused
        play, an internet-off answer) sends nothing.

        Never call it for a Response a handler returns from a voice turn:
        the streaming layer already delivers that one, and the satellite
        would get the frame twice.

        Returns True when the Response was handed to the room's delivery
        (whether or not a satellite is connected right now), False when
        there was nothing to send or no core app is running (a unit test,
        the web process)."""
        if response.music_action not in ("start", "stop"):
            return False
        from domovoi.plugins_runtime.loader import LOADER

        app = LOADER.app
        if app is None:
            log.warning(
                "playback.dispatch: no core app is bound; room=%s gets no %s",
                room_id, response.music_action,
            )
            return False
        from domovoi.room_music import dispatch_music

        await dispatch_music(app, response, room_id)
        return True

    async def ensure_room(self, room_id: str) -> bool:
        """SDK 1.10: whether ``room_id`` is a room this house has, and if so
        make sure its music player is running (idempotent, quick when it
        already is). A plugin route that plays in a room someone NAMED (a
        page's room picker, a phone) asks this first: False means "no such
        room" (answer 404), never a new room — a room is created by a
        satellite's accepted hello, not by a name in a URL (CORE-13). The
        same check the dashboard's own play buttons make.

        A room lookup that fails raises (the caller answers 503); a music
        player that won't start is only logged, and the play that follows
        reports it."""
        from domovoi.clients import mpd as mpd_module
        from domovoi.config import settings
        from domovoi.db.repositories import SatellitesRepository

        room_id = (room_id or "").strip()
        if not room_id:
            return False
        if room_id not in mpd_module._room_ports:
            async with session_scope() as s:
                if not await SatellitesRepository(s).room_is_known(room_id):
                    return False
        if settings.use_stubs:
            return True
        try:
            from domovoi.mpd_provisioner import ensure_room

            await ensure_room(room_id)
        except Exception as e:  # noqa: BLE001 — the play surfaces it
            log.warning("playback.ensure_room(%s) failed: %s", room_id, e)
        return True

    async def player_route(self, room_id: str) -> PlayerRoute | None:
        """SDK 1.15: how the music player that plays ``room_id``'s music
        reaches this core (``.host`` / ``.port``, ``.url(path)``) and the
        address the core sees it call from (``.admits(request.client.host)``),
        for a plugin that hands the player a URL on the core itself, such
        as a stream relay (domovoi/player_route.py). None when it can't be
        told (no player yet, Docker didn't answer)."""
        return await player_route(room_id)

    async def relay_url(self, room_id: str, remote_url: str, *, kind: str = "file") -> str:
        """SDK 1.16: a URL on the core for ``room_id``'s music player that
        plays ``remote_url`` through the core's checked fetch
        (domovoi/player_relay.py), to hand to :meth:`play_url` instead of
        the remote URL itself. The player never fetches the remote URL, so
        never follows a redirect of its own into the house.

        ``kind="radio"``: a live stream, one connection, ICY titles passed
        through. ``kind="file"``: a finite file, any number of byte-range
        requests (seek, resume) for 6 hours. Either way the token is bound
        to this room, this URL and the room's player's address, must be
        opened within 30 s, and ends at the room's next relay or stop.

        Raises ``RelayUnavailable`` when the room's player can't be told
        (no such room, no player yet, Docker didn't answer), ``ValueError``
        for an unknown kind or a URL the core won't fetch (the outbound
        check runs here, before the room's current relay is ended, and
        again at every fetch).

        The token is keyed on the room whose player plays ``room_id``'s
        music, never on the caller's string (L25, review 2026-11-23): a
        made-up room name can't evict another room's relay."""
        from domovoi import net_safety, player_relay
        from domovoi.clients import mpd as mpd_module
        from domovoi.config import settings
        from domovoi.player_route import player_room

        if kind not in player_relay.KINDS:
            raise ValueError(f"relay kind must be one of {player_relay.KINDS}, not {kind!r}")
        host = mpd_module._host_of((room_id or "").strip())
        if not host:
            raise ValueError("relay needs a room")
        if not settings.use_stubs or mpd_module._room_ports:
            if player_room(host) != host:
                raise player_relay.RelayUnavailable(f"no room {room_id!r} with a music player")
        await net_safety.avet_outbound_url(remote_url, require_resolution=False)
        route = await player_route(host)
        if route is None:
            raise player_relay.RelayUnavailable(f"no route to room {room_id!r}'s player")
        return player_relay.mint(host, remote_url, route, kind=kind)

    def relay_target(self, player_url: str | None) -> str | None:
        """SDK 1.16: the remote URL a :meth:`relay_url` stands for while it
        is still good (a now-playing matcher reading the player's file),
        else None."""
        from domovoi import player_relay

        return player_relay.target_for(player_url)

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
