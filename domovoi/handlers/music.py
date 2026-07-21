from __future__ import annotations

import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.capabilities import (
    CAPABILITIES,
    STREAMING_SEARCH_PROVIDER,
)
from domovoi.clients.mpd import (
    get_mpd_client_for,
    iter_mpd_clients,
    mpd_stream_url_for,
)
from domovoi.db.repositories import SessionRepository
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.handlers.shared.play_history import record_media_play
from domovoi.models import Context, Intent, Response

# Session-context keys for the external-streaming seam. A registered
# streaming-search provider's plays are stamped here so smart-skip can
# search for "another one like it" without core knowing any provider
# vocabulary. (Stage C3 re-homes provider-private state under the
# plugins namespace; these three stay core-owned because core writes
# and reads them.)
_LAST_PLAY_SOURCE_KEY = "last_play_source"
_LAST_STREAM_QUERY_KEY = "last_stream_query"
_LAST_STREAM_TITLE_KEY = "last_stream_title"

log = logging.getLogger(__name__)


_PLAY_ARTIST_RE = re.compile(r"^play (.+?) (?:by|from) (.+)$")
# Random / shuffle / "just play something." Anchored variants so a stray
# "play a song" doesn't fall through to _PLAY_ANY_RE and hit an external
# provider searching for the literal string "a song" (garbage; see the
# "play some lady gaga / play a song" friction circa 2026-05-07).
_PLAY_RANDOM_RE = re.compile(
    r"^(?:"
    # "play [me] <random-thing>" — each alternative is a complete tail
    # (the trailing $ anchor forbids extra words). Adding a phrase here
    # snatches it from the greedier _PLAY_ANY_RE which would otherwise
    # fuzzy-match and fall through to the external-provider cascade.
    r"play (?:me )?(?:"
        r"a song|some music|something|anything"
        r"|a random song|a random track|a random thing|a random one|a random tune"
        r"|random|random song|random track|random music|random thing|random one|random tune"
        r"|any song|any track|any music"
        r"|something random|anything random"
        r"|some random music|some random song|some random songs|some random tracks"
    r")"
    r"|shuffle(?: my (?:library|music))?"
    r"|surprise me"
    # "pick [me] <random-thing> [for me]" — natural alternative verb.
    r"|pick (?:me )?(?:something|anything|a song|a track|a tune|one)(?: for me)?"
    r")$"
)


# Titles the LLM tool router might pass as ``args["title"]`` when the
# user actually meant "just play something" — e.g. qwen2.5:14b turns
# "play something random" into a tool call with title="something
# random". Mirrors the regex alternatives above; kept as a set for
# O(1) lookup at dispatch time. Strings are lowercase.
_RANDOM_TITLE_ALIASES = frozenset({
    "something", "anything", "random",
    "a song", "some music",
    "a random song", "a random track", "a random thing",
    "a random one", "a random tune",
    "random song", "random track", "random music",
    "random thing", "random one", "random tune",
    "any song", "any track", "any music",
    "something random", "anything random",
    "some random music", "some random song",
    "some random songs", "some random tracks",
})
_PLAY_ANY_RE = re.compile(r"^play (.+)$")

# Leading recency qualifier stripped before a podcast-title match, so
# "play the latest wait what" searches subscriptions for "wait what", not
# "the latest wait what". See _podcast_show_candidate.
_RECENCY_PREFIX_RE = re.compile(r"^(?:the )?(?:latest|newest|last)\s+")


def _podcast_show_candidate(query: dict) -> str:
    """The show name the podcast fallback should match for an ambiguous
    "play …" that missed the local library. Prefer the "from/by X" part
    (``artist``) — the entity in "play the latest from Wait What" — else the
    free text (``any``/``title``) with a leading recency word stripped, so
    the subscription-title LIKE match lands cleanly."""
    cand = (query.get("artist") or query.get("any") or query.get("title") or "").strip()
    return _RECENCY_PREFIX_RE.sub("", cand).strip()
_PAUSE_RE = re.compile(r"^(?:pause|pause the music|pause music)$")
_RESUME_RE = re.compile(r"^(?:resume|resume the music|continue|continue the music)$")
_STOP_RE = re.compile(r"^(?:stop|stop the music|stop music)$")
# Includes "skip this / skip it / skip this one / skip this song / skip
# this track" — voice-natural ways of saying "next" without saying the
# word "next." Same dispatch as _NEXT.
_NEXT_RE = re.compile(
    r"^(?:"
    r"next|skip|next song|next track"
    r"|skip (?:this|it|this one|this song|this track)"
    r")$"
)
_PREV_RE = re.compile(r"^(?:previous|back|previous song|previous track|go back)$")
_NOW_PLAYING_RE = re.compile(
    r"^(?:what(?:'s| is) playing|what song is this|what is this song|who sang that|who sings this)$"
)
_VOLUME_SET_RE = re.compile(r"^(?:set (?:the )?volume to|volume) (\d{1,3})$")
_VOLUME_UP_RE = re.compile(r"^(?:volume up|louder|turn it up)$")
_VOLUME_DOWN_RE = re.compile(r"^(?:volume down|quieter|turn it down)$")

# Volume is presented to the user as a 1-10 knob — people say "set it to 5",
# not "to 50%". Level 1 maps to 50% hardware (below that the line-level output
# is unusably quiet) and level 10 to 100%, evenly spaced. A spoken number
# outside 1-10 is clamped: "set it to 50" lands at max, "set it to 0" at the
# floor. Internally the satellite still works in hardware %, so the handler
# maps the level to a % to send, and maps the satellite's reported % back to
# the nearest level for relative "turn it up / down" notches.
_VOLUME_MIN_LEVEL = 1
_VOLUME_MAX_LEVEL = 10
_VOLUME_FLOOR_PCT = 50   # level 1
_VOLUME_TOP_PCT = 100    # level 10


def _clamp_level(level: int) -> int:
    return max(_VOLUME_MIN_LEVEL, min(_VOLUME_MAX_LEVEL, int(level)))


def level_to_percent(level: int) -> int:
    """1-10 knob level → hardware %, even steps from 50% (1) to 100% (10):
    1→50 2→56 3→61 4→67 5→72 6→78 7→83 8→89 9→94 10→100."""
    level = _clamp_level(level)
    span = _VOLUME_TOP_PCT - _VOLUME_FLOOR_PCT
    steps = _VOLUME_MAX_LEVEL - _VOLUME_MIN_LEVEL
    return round(_VOLUME_FLOOR_PCT + (level - _VOLUME_MIN_LEVEL) * span / steps)


def percent_to_level(percent: int) -> int:
    """Hardware % → nearest 1-10 knob level. Tolerant of amixer step
    quantization and manual mixer changes (snaps to the closest notch)."""
    span = _VOLUME_TOP_PCT - _VOLUME_FLOOR_PCT
    steps = _VOLUME_MAX_LEVEL - _VOLUME_MIN_LEVEL
    return _clamp_level(round((percent - _VOLUME_FLOOR_PCT) * steps / span) + _VOLUME_MIN_LEVEL)
# Manual library rescan. Needed on the Windows + Docker Desktop setup because
# inotify events from the host filesystem don't propagate through the bind
# mount, so MPD's auto_update doesn't fire when files are added by hand.
_RESCAN_RE = re.compile(
    r"^(?:rescan|update|refresh)(?: (?:my|the))? (?:music )?library$"
    r"|^scan for new (?:music|songs|tracks)$"
)


def _clean_capture(s: str) -> str:
    """Strip whitespace and trailing punctuation from a regex capture group.

    Whisper's transcripts often end with a period or other terminal
    punctuation (e.g. "Play Sunny Day by Akon."), and the greedy `.+`
    in the play patterns captures it. That extra char fails downstream
    substring searches against filenames that don't contain it.
    """
    return s.strip().rstrip(".,!?")


def _format_song(song: dict) -> str:
    title = song.get("title") or song.get("file", "this track").split("/")[-1]
    artist = song.get("artist")
    if artist:
        return f"{title} by {artist}"
    return title


class MusicHandler(Handler):
    name = "music"
    # band rationale: greedy "^play (.+)$" catch-all — after every anchored media band
    #   (spoken_audio 270, radio plugin 280, playlist 290).
    priority_band = 300
    display = HandlerDisplay(label="Music", tone="media")
    chat_exposed = True  # organic media tool in chat mode (#8)
    requires_network = "no"

    tool_schema = {
        "name": "music",
        "description": "Control local music playback: play, pause, stop, skip, volume, and what's-playing.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "play", "pause", "resume", "stop", "next", "previous",
                        "now_playing", "volume_set", "volume_up", "volume_down",
                        "rescan_library",
                    ],
                },
                "title": {"type": "string", "description": "Song title or free-text query"},
                "artist": {"type": "string"},
                "volume": {
                    "type": "integer", "minimum": 1, "maximum": 10,
                    "description": "Volume level on a 1-10 knob (1≈50%, 10=max). Numbers above 10 clamp to 10.",
                },
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_PLAY_ARTIST_RE, MusicHandler._play_artist_from_match),
            # Random/shuffle BEFORE _PLAY_ANY_RE so "play a song" hits the
            # local-library random pick rather than falling through to
            # an external search for the literal string "a song."
            FastPath(_PLAY_RANDOM_RE, MusicHandler._play_random_from_match),
            FastPath(_PAUSE_RE, MusicHandler._pause_from_match),
            FastPath(_RESUME_RE, MusicHandler._resume_from_match),
            FastPath(_STOP_RE, MusicHandler._stop_from_match),
            FastPath(_NEXT_RE, MusicHandler._next_from_match),
            FastPath(_PREV_RE, MusicHandler._prev_from_match),
            FastPath(_NOW_PLAYING_RE, MusicHandler._now_playing_from_match),
            FastPath(_VOLUME_SET_RE, MusicHandler._volume_set_from_match),
            FastPath(_VOLUME_UP_RE, MusicHandler._volume_up_from_match),
            FastPath(_VOLUME_DOWN_RE, MusicHandler._volume_down_from_match),
            FastPath(_RESCAN_RE, MusicHandler._rescan_from_match),
            # Keep _PLAY_ANY_RE last — it's the greediest pattern and must only
            # match when no more specific play-X-by-Y pattern matched above.
            FastPath(_PLAY_ANY_RE, MusicHandler._play_any_from_match),
        ]

    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        return Response(
            text="I didn't catch a music command.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def execute_from_tool(self, args: dict, ctx: Context, session: AsyncSession) -> Response:
        action = args.get("action")
        title = args.get("title")
        artist = args.get("artist")
        volume = args.get("volume")
        if action == "play":
            # The tool router fills whatever it could pull from the
            # utterance: a title, an artist, both, or (for "play
            # something") neither. Honor each combination — the earlier
            # bug collapsed EVERY title-less play to a random track, so
            # "play the latest TI song" (which the router routes as
            # artist="TI", no title) threw the artist away and shuffled the
            # library instead of playing any T.I.
            title_clean = (title or "").strip().lower()
            artist_clean = (artist or "").strip()
            # Explicit "play something / anything / a random song" → shuffle
            # the local library rather than search for that literal string.
            if title_clean in _RANDOM_TITLE_ALIASES:
                return await self._play_random(ctx, session)
            if title and artist_clean:
                return await self._play({"title": title, "artist": artist_clean}, ctx, session)
            if title:
                return await self._play({"any": title}, ctx, session)
            if artist_clean:
                # Artist only ("play some Adele", "play the new TI song"):
                # search by artist locally, then fall through to the cascade —
                # never silently substitute a random track.
                return await self._play({"artist": artist_clean}, ctx, session)
            # Nothing usable at all → treat as "surprise me."
            return await self._play_random(ctx, session)
        if action == "pause":
            return await self._simple_ack("pause", ctx)
        if action == "resume":
            return await self._simple_ack("resume", ctx)
        if action == "stop":
            return await self._simple_ack("stop", ctx)
        if action == "next":
            return await self._simple_ack("next", ctx)
        if action == "previous":
            return await self._simple_ack("previous", ctx)
        if action == "now_playing":
            return await self._now_playing(ctx)
        if action == "volume_set" and volume is not None:
            return await self._volume_set(int(volume), ctx)
        if action == "volume_up":
            return await self._volume_bump(+1, ctx)
        if action == "volume_down":
            return await self._volume_bump(-1, ctx)
        if action == "rescan_library":
            return await self._rescan_library(ctx)
        return Response(
            text=f"I don't know how to {action} the music.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    # ─── Fast-path adapters ─────────────────────────────────────────────
    async def _play_artist_from_match(self, m, ctx, session):
        return await self._play(
            {"title": _clean_capture(m.group(1)), "artist": _clean_capture(m.group(2))},
            ctx,
            session,
        )

    async def _play_any_from_match(self, m, ctx, session):
        return await self._play({"any": _clean_capture(m.group(1))}, ctx, session)

    async def _play_random_from_match(self, m, ctx, session):
        return await self._play_random(ctx, session)

    async def _pause_from_match(self, m, ctx, session):
        return await self._simple_ack("pause", ctx)

    async def _resume_from_match(self, m, ctx, session):
        return await self._simple_ack("resume", ctx)

    async def _stop_from_match(self, m, ctx, session):
        return await self._simple_ack("stop", ctx)

    async def _next_from_match(self, m, ctx, session):
        return await self._smart_skip(ctx, session)

    async def _prev_from_match(self, m, ctx, session):
        return await self._simple_ack("previous", ctx)

    async def _now_playing_from_match(self, m, ctx, session):
        return await self._now_playing(ctx)

    async def _volume_set_from_match(self, m, ctx, session):
        return await self._volume_set(int(m.group(1)), ctx)

    async def _volume_up_from_match(self, m, ctx, session):
        return await self._volume_bump(+1, ctx)

    async def _volume_down_from_match(self, m, ctx, session):
        return await self._volume_bump(-1, ctx)

    async def _rescan_from_match(self, m, ctx, session):
        return await self._rescan_library(ctx)

    # ─── Core actions ───────────────────────────────────────────────────
    async def _play(self, query: dict, ctx: Context, session: AsyncSession) -> Response:
        mpd = get_mpd_client_for(ctx.room_id)
        try:
            song = await mpd.prepare_search(query)
        except Exception as e:
            log.warning("MPD play failed: %s", e)
            return Response(
                text="I couldn't reach the music player.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        q_display = " by ".join(v for v in (query.get("title"), query.get("artist")) if v) or query.get("any", "")
        if not song:
            # Tag-based search missed. Many libraries (especially manually
            # added MP3s and provider downloads without rich metadata) have
            # empty ID3 tags but informative filenames like
            # "Akon - Sunny Day.mp3". Try a filename substring search before
            # falling through to the cascade — searches case-insensitive with
            # all title/artist/any words ANDed against the file path.
            substrings = [
                v for k, v in query.items()
                if k in ("title", "artist", "any") and v
            ]
            if substrings:
                try:
                    song = await mpd.prepare_filename(*substrings)
                except Exception as e:
                    log.warning("MPD filename search failed: %s", e)
        if not song:
            # Local library missed (tags + filename). Cascade for an
            # ambiguous "play …": subscribed podcast → streaming provider →
            # graceful absence / generic. A show the user actually subscribed
            # to is a stronger signal than an external hit, so it goes first;
            # a registered streaming-search provider still covers real
            # artists they don't own. Only when EVERY source misses do we
            # give a generic "couldn't find anything" — the podcast-specific
            # "no subscribed podcast" error is reserved for when the user
            # explicitly asked for a podcast (SpokenAudio's own fast paths).
            from domovoi.handlers.spoken_audio import SpokenAudioHandler

            show = _podcast_show_candidate(query)
            if show:
                try:
                    pod_response = await SpokenAudioHandler().try_play_latest(
                        ctx, session, show
                    )
                except Exception as e:
                    log.warning("podcast fallback failed for %r: %s", show, e)
                    pod_response = None
                if pod_response is not None:
                    return pod_response

            # External streaming fallback — the capability seam (design
            # §10.2). Present ⇒ the provider resolves a fresh stream URL and
            # starts playback itself; absent ⇒ the design's graceful-absence
            # copy. Keeps the chain (play X streams externally; later
            # "play X" hits the local file once the user said "add it").
            if ctx.online and q_display:
                provider = CAPABILITIES.resolve(STREAMING_SEARCH_PROVIDER)
                if provider is None:
                    return Response(
                        text=(
                            f"I couldn't find {q_display} in your library, "
                            "and no streaming provider is installed."
                        ),
                        session_id=ctx.session_id,
                        matched_handler=self.name,
                    )
                stream_response: Response | None = None
                try:
                    stream_response = await provider.stream(
                        ctx.room_id, query=q_display
                    )
                except Exception as e:
                    log.warning(
                        "streaming provider %r failed for %r: %s",
                        getattr(provider, "slug", "?"), q_display, e,
                    )
                if stream_response is not None:
                    # Tag the matched_handler so logs make the path visible,
                    # and stamp the stream seam keys so smart-skip can find
                    # "another one like it" later.
                    stream_response.matched_handler = self.name
                    await self._stamp_stream_play(
                        ctx, session, provider, q_display, stream_response
                    )
                    return stream_response
            return Response(
                text=f"I couldn't find anything called {q_display}.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if ctx.session_id:
            try:
                repo = SessionRepository(session)
                await repo.set_context_key(ctx.session_id, "last_played_track", song)
                # Tag the most recent referent as local so a follow-up
                # "add it to my library" gets a "that's already in your
                # library" response instead of accidentally enqueuing
                # whatever external stream was last surfaced.
                await repo.set_context_key(ctx.session_id, "last_play_source", "local")
            except Exception as e:
                log.warning("couldn't save last_played_track: %s", e)
        await record_media_play(
            session,
            room_id=ctx.room_id,
            source="library",
            title=song.get("title"),
            artist=song.get("artist"),
        )
        return Response(
            text=f"Playing {_format_song(song)}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"song": song},
            music_action="start",
            music_stream_url=mpd_stream_url_for(ctx.room_id),
        )

    async def _play_random(self, ctx: Context, session: AsyncSession) -> Response:
        """Pick a random track from ``library_tracks`` and play via MPD.

        Used by "play a song / play something / shuffle / surprise me."
        Bypasses the external-provider fallthrough — random play that
        searches an external service for the literal string would be
        useless garbage; the user's intent is "something I already
        have, surprise me."

        Empty library returns a friendly nudge to use the add-to-library
        flow rather than crashing or playing silence.
        """
        row = (
            await session.execute(
                text(
                    "SELECT id, title, artist, file_path FROM library_tracks "
                    "ORDER BY RANDOM() LIMIT 1"
                )
            )
        ).first()
        if row is None:
            return Response(
                text=(
                    "Your library is empty. Try adding something with "
                    "'add X to my library' first."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        mpd = get_mpd_client_for(ctx.room_id)
        # Three-stage search, falling back on each miss:
        #   1. Tag search by title+artist — works only when the file's
        #      ID3 tags are populated AND MPD's tag DB has indexed
        #      them. For manually-placed files with empty ID3, this
        #      almost always misses.
        #   2. Filename substring search using parsed title+artist as
        #      substrings — works when both fragments appear in the
        #      filename (e.g. row title="Sunny Day" + artist="Akon"
        #      against file "Akon - Sunny Day.mp3").
        #   3. Filename substring search using the basename of
        #      `file_path` (the indexer stamped this when we walked
        #      MUSIC_DIR). Bulletproof — the basename is exactly what
        #      MPD sees in its file URI, so this always matches as
        #      long as MPD's DB has the file. Last-resort fallback.
        try:
            song: dict[str, Any] | None = None
            if row.title and row.artist:
                song = await mpd.prepare_search({"title": row.title, "artist": row.artist})
            elif row.title:
                song = await mpd.prepare_search({"title": row.title})
            if song is None:
                substrings = [s for s in (row.title, row.artist) if s]
                if substrings:
                    song = await mpd.prepare_filename(*substrings)
            if song is None and row.file_path:
                # Final fallback: basename of the host path. MPD inside
                # Docker sees the same file at `/music/<basename>`, so
                # substring-matching the basename against MPD's URIs
                # always works as long as the file is indexed.
                from pathlib import PurePath
                basename = PurePath(row.file_path).name
                if basename:
                    log.info(
                        "play_random: tag + parsed-substring missed for "
                        "%r by %r; falling back to basename %r",
                        row.title, row.artist, basename,
                    )
                    song = await mpd.prepare_filename(basename)
        except Exception as e:
            log.warning("MPD random play failed: %s", e)
            return Response(
                text="I couldn't reach the music player.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if not song:
            return Response(
                text=(
                    f"I picked {row.title or 'a track'} but the music "
                    "player couldn't find it. Try 'rescan my library'."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if ctx.session_id:
            try:
                repo = SessionRepository(session)
                await repo.set_context_key(ctx.session_id, "last_played_track", song)
                await repo.set_context_key(ctx.session_id, "last_play_source", "local")
                # Random play resets any stale external-stream state —
                # "next" after random should mean MPD's next, not skip-
                # through-an-old-search-from-an-hour-ago.
                await self._clear_stream_state(repo, ctx.session_id)
            except Exception as e:
                log.warning("couldn't save last_played_track: %s", e)
        await record_media_play(
            session,
            room_id=ctx.room_id,
            source="library",
            title=song.get("title") or row.title,
            artist=song.get("artist") or row.artist,
            library_track_id=row.id,
        )
        return Response(
            text=f"Playing {_format_song(song)}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"song": song},
            music_action="start",
            music_stream_url=mpd_stream_url_for(ctx.room_id),
        )

    async def _smart_skip(self, ctx: Context, session: AsyncSession) -> Response:
        """Voice "next" / "skip this / it / this one / this song / this track."

        Four cases, in order:

        * **An external stream from a registered streaming-search
          provider is active** (``last_play_source`` matches the
          provider's slug) — search the same query again via the
          capability seam and stream the first result that isn't
          ``likely_same`` as the current title.
        * **A playlist is active in this room** —
          ``app.state.current_playlist[room_id]`` is set AND its
          stored ``last_file_path`` matches MPD's currentsong (so
          the entry isn't stale from a prior playlist play). In
          ``ordered`` mode advance by position (loop at end); in
          ``shuffle`` mode pick a random track from the playlist
          excluding the just-played one.
        * **Local library track is playing (not in a playlist)** —
          pick another random library track. Detected via session
          context (``last_play_source == "local"``) or by
          MPD-currentsong-file inspection when the admin/web path
          has no session_id.
        * **Anything else** (unknown source) — fall through
          to ``mpd.next()`` via ``_simple_ack``.
        """
        if ctx.session_id:
            try:
                ctx_data = await SessionRepository(session).get_context(ctx.session_id) or {}
            except Exception as e:
                log.warning("smart_skip: couldn't read session context: %s", e)
                ctx_data = {}
            source = ctx_data.get(_LAST_PLAY_SOURCE_KEY)
            provider = CAPABILITIES.resolve(STREAMING_SEARCH_PROVIDER)
            if (
                provider is not None
                and source
                and source == getattr(provider, "slug", None)
            ):
                stream_skip = await self._skip_via_provider(
                    provider, ctx_data, ctx, session
                )
                if stream_skip is not None:
                    return stream_skip
        else:
            source = None

        # Playlist branch — checks per-room state with a freshness
        # guard against MPD's currentsong, so an entry from a prior
        # playlist play that's been superseded by some other source
        # in the same room is correctly ignored.
        playlist_response = await self._maybe_skip_in_playlist(ctx, session)
        if playlist_response is not None:
            return playlist_response

        local_play = source == "local"
        if not local_play:
            try:
                mpd_inspect = get_mpd_client_for(ctx.room_id)
                current = (await mpd_inspect.current_song()) or {}
                file_ = current.get("file") or ""
                if file_ and not file_.startswith("http"):
                    local_play = True
            except Exception as e:
                log.debug("smart_skip: MPD inspect failed: %s", e)

        if local_play:
            return await self._play_random(ctx, session)
        return await self._simple_ack("next", ctx)

    async def _maybe_skip_in_playlist(
        self, ctx: Context, session: AsyncSession
    ) -> Response | None:
        """If ``app.state.current_playlist[ctx.room_id]`` is set AND
        passes the MPD-file freshness check, advance within the
        playlist and return the resulting Response. Otherwise return
        None and let ``_smart_skip`` continue to its other branches.

        Reuses :func:`domovoi.handlers.shared.playlist_pick.
        pick_next_track` so the SELECT logic is identical to the
        ``play-playlist`` admin endpoint's first-pick path.
        """
        if ctx.app is None or not ctx.room_id:
            return None
        entry = None
        try:
            entry = ctx.app.state.current_playlist.get(ctx.room_id)
        except Exception:
            return None
        if not isinstance(entry, dict):
            return None

        # Freshness check: MPD's currentsong should still be the
        # track we last played from this playlist. If anything else
        # has taken over the room, drop the entry and let the rest of
        # _smart_skip handle the new source.
        try:
            mpd_inspect = get_mpd_client_for(ctx.room_id)
            current = (await mpd_inspect.current_song()) or {}
            current_file = current.get("file") or ""
        except Exception as e:
            log.debug("smart_skip playlist freshness check failed: %s", e)
            return None
        if current_file != entry.get("last_file_path"):
            try:
                ctx.app.state.current_playlist.pop(ctx.room_id, None)
            except Exception:
                pass
            return None

        from pathlib import PurePath

        from domovoi.handlers.shared.playlist_pick import (
            persist_resume_position,
            pick_next_track,
        )

        playlist_id = int(entry.get("playlist_id", 0))
        playlist_name = str(entry.get("name") or "playlist")
        mode = str(entry.get("mode") or "ordered")
        last_track_id = entry.get("last_track_id")
        last_position = entry.get("last_position")

        picked = await pick_next_track(
            session=session,
            playlist_id=playlist_id,
            mode=mode,
            last_track_id=last_track_id,
            last_position=last_position,
        )
        if picked is None:
            return self._reply(
                ctx, f"The {playlist_name} playlist is empty."
            )

        mpd = get_mpd_client_for(ctx.room_id)
        song: dict[str, Any] | None = None
        try:
            if picked.title and picked.artist:
                song = await mpd.prepare_search(
                    {"title": picked.title, "artist": picked.artist}
                )
            elif picked.title:
                song = await mpd.prepare_search({"title": picked.title})
            if song is None:
                substrings = [s for s in (picked.title, picked.artist) if s]
                if substrings:
                    song = await mpd.prepare_filename(*substrings)
            if song is None and picked.file_path:
                basename = PurePath(picked.file_path).name
                if basename:
                    song = await mpd.prepare_filename(basename)
        except Exception as e:
            log.warning("smart_skip playlist play failed: %s", e)
            return self._reply(ctx, "I couldn't reach the music player.")
        if not song:
            return self._reply(
                ctx,
                f"I picked {picked.title or 'a track'} but the music "
                "player couldn't find it. Try 'rescan my library'.",
            )

        # Update the room's playlist state with the new pick.
        try:
            ctx.app.state.current_playlist[ctx.room_id] = {
                "playlist_id": playlist_id,
                "name": playlist_name,
                "mode": mode,
                "last_track_id": picked.track_id,
                "last_position": picked.position,
                "last_file_path": str(song.get("file", "")),
            }
        except Exception as e:
            log.debug("smart_skip: couldn't update current_playlist: %s", e)

        # Durable resume — guarded inside the helper to ordered + non-Favorites
        # (this stamp block also runs for shuffle, which must NOT persist).
        await persist_resume_position(session, playlist_id, mode, picked.position)

        await record_media_play(
            session,
            room_id=ctx.room_id,
            source="playlist",
            title=picked.title,
            artist=picked.artist,
            library_track_id=picked.track_id,
        )
        return Response(
            text=f"Next in {playlist_name}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            music_action="start",
            music_stream_url=mpd_stream_url_for(ctx.room_id),
            data={
                "playlist_id": playlist_id,
                "playlist_name": playlist_name,
                "mode": mode,
                "track_id": picked.track_id,
            },
        )

    def _reply(self, ctx: Context, text_: str) -> Response:
        return Response(
            text=text_,
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _stamp_stream_play(
        self,
        ctx: Context,
        session: AsyncSession,
        provider: Any,
        query: str,
        response: Response,
    ) -> None:
        """Record an external-provider play in session context so a later
        "skip this" can run the provider-search skip. Best-effort — a
        session-context hiccup never fails the play."""
        if not ctx.session_id:
            return
        try:
            repo = SessionRepository(session)
            await repo.set_context_key(
                ctx.session_id, _LAST_PLAY_SOURCE_KEY, getattr(provider, "slug", None)
            )
            await repo.set_context_key(ctx.session_id, _LAST_STREAM_QUERY_KEY, query)
            title = None
            if isinstance(response.data, dict):
                title = response.data.get("title")
            await repo.set_context_key(ctx.session_id, _LAST_STREAM_TITLE_KEY, title)
        except Exception as e:
            log.warning("couldn't stamp stream play state: %s", e)

    async def _clear_stream_state(
        self, repo: SessionRepository, session_id
    ) -> None:
        """Reset the external-stream seam keys — used when a local/playlist
        play takes over so a later "next" doesn't skip through a stale
        external search from an hour ago."""
        await repo.set_context_key(session_id, _LAST_STREAM_QUERY_KEY, None)
        await repo.set_context_key(session_id, _LAST_STREAM_TITLE_KEY, None)

    async def _skip_via_provider(
        self,
        provider: Any,
        ctx_data: dict,
        ctx: Context,
        session: AsyncSession,
    ) -> Response | None:
        """Skip within an external provider stream via the capability seam
        (design §10.2): re-run the stored query through ``provider.search``,
        drop candidates ``likely_same`` as the current title, stream the
        first fresh one. Polite exhaustion message when nothing fresh
        remains; ``None`` (fall through to the other skip branches) when
        there's no stored query or the provider search fails."""
        query = (ctx_data.get(_LAST_STREAM_QUERY_KEY) or "").strip()
        current_title = ctx_data.get(_LAST_STREAM_TITLE_KEY) or ""
        if not query:
            return None
        try:
            candidates = await provider.search(query, limit=10)
        except Exception as e:
            log.warning("smart_skip: provider search failed for %r: %s", query, e)
            return None
        seen_current = False
        for candidate in candidates or []:
            title = getattr(candidate, "title", "") or ""
            if current_title and provider.likely_same(title, current_title):
                seen_current = True
                continue
            if not current_title and not seen_current:
                # No stored title to dedup against — treat the first
                # result as "what's already playing" and take the next.
                seen_current = True
                continue
            try:
                response = await provider.stream(ctx.room_id, candidate)
            except Exception as e:
                log.warning("smart_skip: provider stream failed: %s", e)
                continue
            if response is None:
                continue
            response.matched_handler = self.name
            if ctx.session_id:
                try:
                    await SessionRepository(session).set_context_key(
                        ctx.session_id, _LAST_STREAM_TITLE_KEY, title
                    )
                except Exception as e:
                    log.warning("couldn't update stream skip state: %s", e)
            return response
        # Exhausted — no more different-titled results.
        return Response(
            text=(
                f"I've run out of fresh {query} results — that "
                "was the last one I had. Want to try a different search?"
            ),
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _rescan_library(self, ctx: Context) -> Response:
        """Trigger every per-room MPD to rescan the music directory AND
        re-index ``library_tracks`` from disk.

        Two indexes need refreshing whenever files appear in MUSIC_DIR
        outside the provider download path:

          * Each MPD daemon's tag DB (per-room — they have separate
            DB files), via the ``update`` command. Needed because
            inotify events from the Windows host don't propagate
            through Docker Desktop's bind mount.
          * The core's ``library_tracks`` table, via
            `library_indexer.index_music_dir`. Needed for random
            play, "how many songs do I have", and add-to-library
            dedup.

        Both fan out from this one voice command so the user only
        needs to learn one phrase to refresh after dropping files in.
        """
        from domovoi.workers.library_indexer import index_music_dir

        any_mpd_ok = False
        for room_id, mpd in iter_mpd_clients():
            try:
                await mpd.update_library()
                any_mpd_ok = True
            except Exception as e:
                log.warning("MPD update_library failed for room=%s: %s", room_id, e)

        try:
            counts = await index_music_dir()
        except Exception as e:
            log.warning("library_tracks indexer failed during rescan: %s", e)
            counts = None

        if not any_mpd_ok and counts is None:
            return Response(
                text="I couldn't reach the music player or index the library.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        # Past-tense, with actual numbers — both the indexer and the
        # MPD update have already finished by the time we get here
        # (sync awaits above), so reporting in present tense leaves
        # the user wondering whether anything happened.
        if counts is None:
            text_resp = "Rescan complete — couldn't index the library this time."
        else:
            scanned = counts.get("scanned", 0)
            inserted = counts.get("inserted", 0)
            if inserted > 0:
                text_resp = (
                    f"Rescan complete — found {scanned} tracks, "
                    f"{inserted} new since last scan."
                )
            else:
                text_resp = (
                    f"Rescan complete — your library has {scanned} "
                    f"tracks and nothing's changed since last scan."
                )
        return Response(
            text=text_resp,
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _simple_ack(self, action: str, ctx: Context) -> Response:
        mpd = get_mpd_client_for(ctx.room_id)
        try:
            if action == "pause":
                await mpd.pause()
                text = "Paused."
            elif action == "resume":
                await mpd.resume()
                text = "Resuming."
            elif action == "stop":
                await mpd.stop()
                text = "Stopped."
            elif action == "next":
                await mpd.next()
                text = "Next track."
            elif action == "previous":
                await mpd.previous()
                text = "Previous track."
            else:
                text = f"Done: {action}."
        except Exception as e:
            log.warning("MPD %s failed: %s", action, e)
            return Response(
                text="I couldn't reach the music player.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        # "stop" tears down the Pi's music subprocess; pause/resume keep the
        # HTTP stream open (MPD just stops sending audio frames during pause)
        # so no Pi-side state change needed.
        music_action: str | None = "stop" if action == "stop" else None
        return Response(
            text=text,
            session_id=ctx.session_id,
            matched_handler=self.name,
            music_action=music_action,
        )

    async def _now_playing(self, ctx: Context) -> Response:
        mpd = get_mpd_client_for(ctx.room_id)
        try:
            song = await mpd.current_song()
        except Exception as e:
            log.warning("MPD current_song failed: %s", e)
            return Response(
                text="I couldn't reach the music player.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if not song:
            return Response(
                text="Nothing is playing right now.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return Response(
            text=f"This is {_format_song(song)}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"song": song},
        )

    # ── Volume ─────────────────────────────────────────────────────────
    # User-facing volume is a 1-10 knob (see level_to_percent above). It
    # maps to the satellite's HARDWARE output gain — the single point both
    # TTS and music flow through — so one command controls Domovoi's voice
    # AND the music. The handler puts the target *percent* in
    # ``Response.satellite_volume``; the streaming layer sends a
    # ``set_volume`` frame to the Pi before the spoken confirmation. MPD's
    # own volume is pinned to 100% so music isn't attenuated twice
    # (hardware × MPD). The satellite reports its real % back via
    # ``volume_status`` (Context.satellite_volume); relative "turn it up /
    # down" notches convert that to the nearest level and step by one.

    async def _pin_mpd_full(self, ctx: Context) -> None:
        """Best-effort: keep MPD at 100% so the satellite's hardware mixer
        is the sole volume control (no double attenuation of music)."""
        try:
            await get_mpd_client_for(ctx.room_id).set_volume(100)
        except Exception as e:
            log.debug("MPD pin-to-100 failed (non-fatal): %s", e)

    async def _volume_set(self, number: int, ctx: Context) -> Response:
        # `number` is a spoken 1-10 knob value: below 1 floors at 1 (50%),
        # above 10 caps at 10 (100%).
        level = _clamp_level(number)
        await self._pin_mpd_full(ctx)
        return Response(
            text=f"Volume set to {level}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            satellite_volume=level_to_percent(level),
        )

    async def _volume_bump(self, delta_levels: int, ctx: Context) -> Response:
        # Step one notch on the 1-10 scale from the satellite's real level
        # (default to 10 if it hasn't reported its % yet). "Turn it down"
        # bottoms out at level 1 (50%) — never quieter.
        current_level = (
            percent_to_level(ctx.satellite_volume)
            if ctx.satellite_volume is not None
            else _VOLUME_MAX_LEVEL
        )
        new_level = _clamp_level(current_level + delta_levels)
        await self._pin_mpd_full(ctx)
        verb = "up" if delta_levels > 0 else "down"
        return Response(
            text=f"Volume {verb} to {new_level}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            satellite_volume=level_to_percent(new_level),
        )
