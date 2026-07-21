"""PlaylistHandler — voice surface for the playlist feature.

Six fast paths, in regex priority order:

1. ``play my favorites``                → Favorites in ordered mode
2. ``shuffle my favorites``             → Favorites in shuffle mode
3. ``play (?:my |the )?X playlist``      → playlist X, ordered mode
4. ``shuffle (?:my |the )?X playlist``   → playlist X, shuffle mode
5. ``make (?:a )?(?:new )?playlist called/named X`` → create empty
6. ``add (?:this|that|it|the current song/track) to (?:my )?X playlist``
   → resolve room's currentsong to a library row, auto-create
   playlist if missing, append to end

Voice can create playlists (per user's call). Playlist-name lookup
is case-insensitive and supports multi-match disambiguation via the
standard pending-confirmation flow (namespaced kinds, design §4.7).

``requires_network = "no"`` — playlists are entirely local.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.capabilities import (
    ACQUISITION_ABSENCE_MESSAGE,
    CAPABILITIES,
    MEDIA_ACQUISITION_FULFILLER,
)
from domovoi.clients.mpd import get_mpd_client_for, mpd_stream_url_for
from domovoi.confirmations import request_confirmation
from domovoi.db.repositories import SessionRepository
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.handlers.shared.library_dedup import find_fuzzy_library_match
from domovoi.handlers.shared.library_match import library_path_for_mpd_file
from domovoi.handlers.shared.play_history import record_media_play
from domovoi.handlers.shared.playlist_pick import (
    persist_resume_position,
    pick_next_track,
    read_resume_position,
)
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


_PLAY_FAVORITES_RE = re.compile(r"^play my favorites$")
_SHUFFLE_FAVORITES_RE = re.compile(r"^shuffle my favorites$")
_PLAY_PLAYLIST_RE = re.compile(
    r"^play (?:my |the )?(.+?) playlist$"
)
_SHUFFLE_PLAYLIST_RE = re.compile(
    r"^shuffle (?:my |the )?(.+?) playlist$"
)
_MAKE_PLAYLIST_RE = re.compile(
    r"^make (?:a )?(?:new )?playlist (?:called|named) (.+)$"
)
_ADD_TO_PLAYLIST_RE = re.compile(
    r"^add (?:this|that|it|the current (?:song|track)) "
    r"to (?:my |the )?(.+?) playlist$"
)
# "add <song description> after this" — insert into the CURRENTLY-PLAYING
# playlist right after the current track. Anchored on the trailing
# "after this" so it can't poach "add this to my X playlist".
_ADD_AFTER_THIS_RE = re.compile(r"^add (.+?) after this$")


FAVORITES_VIRTUAL_ID = 0
FAVORITES_VIRTUAL_NAME = "Favorites"


class PlaylistHandler(Handler):
    name = "playlist"
    # band rationale: before music (300) so "play the X playlist" / "shuffle the X
    #   playlist" / "make a new playlist called X" / "add this to my X
    #   playlist" win their tightly-anchored phrasing before music's
    #   greedy "^play (.+)$" catch-all matches.
    priority_band = 290
    display = HandlerDisplay(label="Playlists", tone="media")
    confirmation_kinds = ("core.playlist_choice", "core.playlist_add_choice")
    chat_exposed = True  # organic media tool in chat mode (#8)
    requires_network = "no"

    tool_schema = {
        "name": "playlist",
        "description": (
            "Manage and play user playlists. Use this when the user "
            "names a playlist by name (or says 'my favorites'). "
            "Different from the music tool — playlists are saved "
            "collections, music is general playback control."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "shuffle", "create", "add_current"],
                },
                "playlist_name": {"type": "string"},
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_PLAY_FAVORITES_RE, PlaylistHandler._play_favorites_from_match),
            FastPath(_SHUFFLE_FAVORITES_RE, PlaylistHandler._shuffle_favorites_from_match),
            FastPath(_MAKE_PLAYLIST_RE, PlaylistHandler._make_from_match),
            # Before _ADD_TO_PLAYLIST_RE so "add X after this" wins over the
            # "add ... to my X playlist" pattern.
            FastPath(_ADD_AFTER_THIS_RE, PlaylistHandler._add_after_from_match),
            FastPath(_ADD_TO_PLAYLIST_RE, PlaylistHandler._add_to_from_match),
            FastPath(_PLAY_PLAYLIST_RE, PlaylistHandler._play_playlist_from_match),
            FastPath(_SHUFFLE_PLAYLIST_RE, PlaylistHandler._shuffle_playlist_from_match),
        ]

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        # Catch-all for tool-call dispatch that didn't pass a known
        # action. Treat the transcript as a playlist name and try to
        # play it ordered.
        return await self._play_by_name(
            intent.transcript.strip(), ctx, session, mode="ordered"
        )

    async def execute_from_tool(
        self, args: dict[str, Any], ctx: Context, session: AsyncSession
    ) -> Response:
        action = args.get("action")
        name = (args.get("playlist_name") or "").strip()
        if action == "play":
            if name.lower() == "favorites" or name.lower() == "my favorites":
                return await self._play_virtual_favorites(ctx, session, mode="ordered")
            return await self._play_by_name(name, ctx, session, mode="ordered")
        if action == "shuffle":
            if name.lower() == "favorites" or name.lower() == "my favorites":
                return await self._play_virtual_favorites(ctx, session, mode="shuffle")
            return await self._play_by_name(name, ctx, session, mode="shuffle")
        if action == "create":
            return await self._create_playlist(name, ctx, session)
        if action == "add_current":
            return await self._add_current_to_named(name, ctx, session)
        return self._reply(ctx, f"I don't know how to {action} a playlist.")

    # ─── Fast-path adapters ─────────────────────────────────────────────

    async def _play_favorites_from_match(self, m, ctx, session):
        return await self._play_virtual_favorites(ctx, session, mode="ordered")

    async def _shuffle_favorites_from_match(self, m, ctx, session):
        return await self._play_virtual_favorites(ctx, session, mode="shuffle")

    async def _play_playlist_from_match(self, m, ctx, session):
        return await self._play_by_name(
            m.group(1).strip(), ctx, session, mode="ordered",
        )

    async def _shuffle_playlist_from_match(self, m, ctx, session):
        return await self._play_by_name(
            m.group(1).strip(), ctx, session, mode="shuffle",
        )

    async def _make_from_match(self, m, ctx, session):
        return await self._create_playlist(m.group(1).strip(), ctx, session)

    async def _add_to_from_match(self, m, ctx, session):
        return await self._add_current_to_named(
            m.group(1).strip(), ctx, session,
        )

    # ─── Multi-turn disambiguation ─────────────────────────────────────

    async def handle_confirmation(
        self,
        kind: str,
        data: dict[str, Any],
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        """The router routes a clear yes/no answer back here when this
        handler had a parked pending_confirmation. Disambiguation shape:
        affirmative means "the first candidate."
        """
        if kind == "core.playlist_choice":
            candidates = data.get("candidates") or []
            mode = data.get("mode") or "ordered"
            if not candidates:
                return self._reply(ctx, "I lost track of those playlists — say it again?")
            if affirmative:
                chosen = candidates[0]
                return await self._play_resolved(
                    playlist_id=int(chosen["id"]),
                    playlist_name=str(chosen["name"]),
                    mode=mode,
                    ctx=ctx,
                    session=session,
                )
            return self._reply(ctx, "OK, never mind.")
        if kind == "core.playlist_add_choice":
            candidates = data.get("candidates") or []
            track_id = data.get("track_id")
            if not candidates or track_id is None:
                return self._reply(ctx, "I lost track of that — say it again?")
            if affirmative:
                chosen = candidates[0]
                return await self._append_track(
                    playlist_id=int(chosen["id"]),
                    playlist_name=str(chosen["name"]),
                    track_id=int(track_id),
                    ctx=ctx,
                    session=session,
                )
            return self._reply(ctx, "OK, never mind.")
        return self._reply(ctx, "I'm not sure what you're confirming.")

    # ─── Play paths ────────────────────────────────────────────────────

    async def _play_by_name(
        self,
        name: str,
        ctx: Context,
        session: AsyncSession,
        *,
        mode: str,
    ) -> Response:
        """Resolve a playlist name to an id and play it. Multi-match
        → park pending_confirmation. Zero matches → polite error."""
        if not name:
            return self._reply(ctx, "Which playlist?")
        result = await session.execute(
            text(
                """
                SELECT id, name FROM playlists
                WHERE LOWER(name) LIKE :like
                ORDER BY (LOWER(name) = LOWER(:exact)) DESC, name
                LIMIT 5
                """
            ),
            {"like": f"%{name.lower()}%", "exact": name.lower()},
        )
        candidates = [
            {"id": int(r[0]), "name": r[1]} for r in result.all()
        ]
        if not candidates:
            return self._reply(
                ctx,
                f"I don't have a playlist called {name}. "
                "Make one in the dashboard or say "
                f"'make a new playlist called {name}'.",
            )
        if len(candidates) == 1:
            return await self._play_resolved(
                playlist_id=candidates[0]["id"],
                playlist_name=candidates[0]["name"],
                mode=mode,
                ctx=ctx,
                session=session,
            )
        await self._park_play_confirmation(ctx, session, candidates, mode)
        names = " or ".join(c["name"] for c in candidates[:2])
        return Response(
            text=f"I have {len(candidates)} playlists matching — {names}?",
            session_id=ctx.session_id,
            matched_handler=self.name,
            expect_followup=True,
        )

    async def _play_virtual_favorites(
        self, ctx: Context, session: AsyncSession, *, mode: str,
    ) -> Response:
        return await self._play_resolved(
            playlist_id=FAVORITES_VIRTUAL_ID,
            playlist_name=FAVORITES_VIRTUAL_NAME,
            mode=mode,
            ctx=ctx,
            session=session,
        )

    async def _play_resolved(
        self,
        *,
        playlist_id: int,
        playlist_name: str,
        mode: str,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        # Durable resume: ordered mode continues from the saved position
        #. Shuffle / Favorites return None and start fresh.
        resume = await read_resume_position(session, playlist_id, mode)
        picked = await pick_next_track(
            session=session,
            playlist_id=playlist_id,
            mode=mode,
            last_track_id=None,
            last_position=resume,
        )
        if picked is None:
            return self._reply(
                ctx, f"The {playlist_name} playlist is empty."
            )

        # Three-stage MPD lookup mirroring MusicHandler._play_random.
        # Stamps current_playlist on success.
        from pathlib import PurePath

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
            log.warning("playlist play MPD failed: %s", e)
            return self._reply(ctx, "I couldn't reach the music player.")

        if not song:
            return self._reply(
                ctx,
                f"I picked {picked.title or 'a track'} but the music "
                "player couldn't find it. Try 'rescan my library'.",
            )

        # Stamp playlist state for _smart_skip to read.
        if ctx.app is not None and ctx.room_id:
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
                log.debug("could not stamp current_playlist: %s", e)

        await persist_resume_position(session, playlist_id, mode, picked.position)

        await record_media_play(
            session,
            room_id=ctx.room_id,
            source="playlist",
            title=picked.title,
            artist=picked.artist,
            library_track_id=picked.track_id,
        )

        # Stash a small marker in session context so the router /
        # other handlers know what's playing in this room without
        # having to peek at app.state.
        if ctx.session_id:
            try:
                repo = SessionRepository(session)
                await repo.set_context_key(
                    ctx.session_id, "last_play_source", "playlist",
                )
                # Reset any stale external-stream seam state so a later
                # "next" advances the playlist, not an old search.
                await repo.set_context_key(
                    ctx.session_id, "last_stream_query", None,
                )
                await repo.set_context_key(
                    ctx.session_id, "last_stream_title", None,
                )
            except Exception as e:
                log.debug("couldn't save playlist play state: %s", e)

        verb = "Shuffling" if mode == "shuffle" else "Playing"
        return Response(
            text=f"{verb} {playlist_name}.",
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

    # ─── Create + add paths ────────────────────────────────────────────

    async def _create_playlist(
        self, name: str, ctx: Context, session: AsyncSession,
    ) -> Response:
        if not name:
            return self._reply(ctx, "What should I call the playlist?")
        # Case-insensitive dup check — matches the web API's pre-check.
        dup = (
            await session.execute(
                text(
                    "SELECT name FROM playlists WHERE LOWER(name) = LOWER(:n)"
                ),
                {"n": name},
            )
        ).first()
        if dup is not None:
            return self._reply(
                ctx, f"There's already a playlist called {dup[0]}."
            )
        await session.execute(
            text("INSERT INTO playlists (name) VALUES (:n)"),
            {"n": name},
        )
        await session.execute(text("SELECT pg_notify('playlists_changed', 'created')"))
        return self._reply(ctx, f"Created playlist {name}.")

    async def _add_after_from_match(
        self, m: re.Match, ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._add_after_this(m.group(1).strip(), ctx, session)

    async def _resolve_library_track(
        self, query: str, session: AsyncSession
    ) -> tuple[int | None, str | None]:
        """Resolve a spoken song description to a (track_id, title) — exact
        title match first, then the shared fuzzy matcher (which returns
        title/artist, so we re-select the id)."""
        row = (
            await session.execute(
                text(
                    "SELECT id, title FROM library_tracks "
                    "WHERE LOWER(title) = LOWER(:q) LIMIT 1"
                ),
                {"q": query},
            )
        ).first()
        if row is not None:
            return int(row[0]), row[1]
        match = await find_fuzzy_library_match(
            session, title=query, artist=None, clean_title=True
        )
        if match is not None:
            row = (
                await session.execute(
                    text(
                        "SELECT id, title FROM library_tracks "
                        "WHERE title = :t AND artist IS NOT DISTINCT FROM :a "
                        "LIMIT 1"
                    ),
                    {"t": match["title"], "a": match["artist"]},
                )
            ).first()
            if row is not None:
                return int(row[0]), row[1]
        return None, None

    async def _add_after_this(
        self, query: str, ctx: Context, session: AsyncSession
    ) -> Response:
        """Insert a song right after the currently-playing playlist track.

        Only meaningful while an ordered playlist is the active source —
        shuffle / Favorites fall back to appending at the end with a note.
        A song not in the library becomes a media acquisition (design §4.8):
        the queue row is attached to this playlist and lands at its end once
        a registered fulfiller downloads it. Stays requires_network='no' —
        enqueue is local; with no fulfiller installed the row simply waits
        and the user hears the graceful-absence message."""
        if not query:
            return self._reply(ctx, "Add what after this?")
        entry = None
        if ctx.app is not None and ctx.room_id:
            e = ctx.app.state.current_playlist.get(ctx.room_id)
            if isinstance(e, dict):
                entry = e
        if entry is None:
            return self._reply(
                ctx,
                "Nothing's playing from a playlist right now — start a "
                "playlist first, then I can add a song after the current one.",
            )
        playlist_id = int(entry.get("playlist_id", 0))
        mode = str(entry.get("mode") or "ordered")
        last_pos = entry.get("last_position")
        playlist_name = str(entry.get("name") or "the playlist")

        track_id, track_title = await self._resolve_library_track(query, session)

        if track_id is not None:
            if playlist_id == 0:
                return self._reply(
                    ctx,
                    "You're playing your Favorites, which I can't insert into "
                    "mid-list — favorite the song instead.",
                )
            if mode == "ordered" and isinstance(last_pos, int):
                # Shift everything after the current track down one, then
                # slot the new track in. the no-UNIQUE-on-position schema makes
                # the transient mid-shift safe.
                await session.execute(
                    text(
                        "UPDATE playlist_tracks SET position = position + 1 "
                        "WHERE playlist_id = :pid AND position > :pos"
                    ),
                    {"pid": playlist_id, "pos": last_pos},
                )
                await session.execute(
                    text(
                        "INSERT INTO playlist_tracks (playlist_id, track_id, position) "
                        "VALUES (:pid, :tid, :pos) "
                        "ON CONFLICT (playlist_id, track_id) DO NOTHING"
                    ),
                    {"pid": playlist_id, "tid": track_id, "pos": last_pos + 1},
                )
                await session.execute(text("SELECT pg_notify('playlists_changed', 'added')"))
                return self._reply(
                    ctx, f"Added {track_title} to {playlist_name}, right after this one."
                )
            # Shuffle (or no usable position) → append at the end.
            await session.execute(
                text(
                    "INSERT INTO playlist_tracks (playlist_id, track_id, position) "
                    "VALUES (:pid, :tid, COALESCE((SELECT MAX(position) + 1 "
                    "FROM playlist_tracks WHERE playlist_id = :pid), 0)) "
                    "ON CONFLICT (playlist_id, track_id) DO NOTHING"
                ),
                {"pid": playlist_id, "tid": track_id},
            )
            await session.execute(text("SELECT pg_notify('playlists_changed', 'added')"))
            return self._reply(
                ctx,
                f"Added {track_title} to the end of {playlist_name} — it's "
                "shuffling, so I can't slot it right after this one.",
            )

        # Not in the library. Real playlist → enqueue a media acquisition
        # attached to this playlist (design §4.8: enqueue always succeeds;
        # a registered fulfiller drains it, absence degrades gracefully).
        # Favorites (virtual id 0) can't be attached to.
        if playlist_id != 0:
            enqueued = await self._enqueue_fetch_missing(
                query, playlist_id, session
            )
            if enqueued:
                if CAPABILITIES.absent(MEDIA_ACQUISITION_FULFILLER):
                    return self._reply(
                        ctx,
                        f"I couldn't find {query} in your library. "
                        + ACQUISITION_ABSENCE_MESSAGE,
                    )
                return self._reply(
                    ctx,
                    f"I couldn't find {query} in your library, so I've "
                    f"queued it to download — it'll land at the end of "
                    f"{playlist_name} once it's ready.",
                )
        return self._reply(
            ctx, f"I couldn't find {query} in your library.",
        )

    async def _enqueue_fetch_missing(
        self, query: str, playlist_id: int, session: AsyncSession
    ) -> bool:
        """Queue a 'query'-kind media acquisition soft-attached to the
        playlist, through the core acquisition service (design §4.8).
        Best-effort: False on failure (the caller falls back to the plain
        couldn't-find message). This producer only writes the request —
        claim/complete is a fulfiller's job (requested_by='voice:playlist',
        the four-producer contract)."""
        from domovoi.acquisitions import ACQUISITIONS

        try:
            result = await ACQUISITIONS.enqueue(
                session,
                kind="query",
                text=query,
                metadata={"title": query},
                requested_by="voice:playlist",
                attach_to_playlist_id=playlist_id,
                # The caller already searched the library and missed;
                # a second fuzzy pass would only block legitimately
                # different tracks with similar names.
                skip_library_dedup=True,
            )
            return result.outcome == "enqueued"
        except Exception as e:
            log.warning("fetch-missing enqueue failed for %r: %s", query, e)
            return False

    async def _add_current_to_named(
        self, name: str, ctx: Context, session: AsyncSession,
    ) -> Response:
        """Read the room's MPD currentsong, resolve it to a
        library_tracks row, find-or-create the named playlist, append.
        Refuses HTTP-source playback (external streams) with a hint."""
        if not name:
            return self._reply(ctx, "Which playlist?")
        if not ctx.room_id:
            return self._reply(ctx, "I don't know which room you're in.")
        mpd = get_mpd_client_for(ctx.room_id)
        try:
            song = await mpd.current_song()
        except Exception as e:
            log.warning("add-to-playlist: MPD currentsong failed: %s", e)
            return self._reply(ctx, "I couldn't tell what's playing right now.")
        if not song or not song.get("file"):
            return self._reply(ctx, "Nothing is playing right now.")
        mpd_file = str(song["file"])
        if mpd_file.startswith("http"):
            return self._reply(
                ctx,
                "That's streaming from somewhere else — add it to your "
                "library first, then I can add it to a playlist.",
            )

        # Resolve to library_tracks row (exact-path match).
        expected_path = library_path_for_mpd_file(mpd_file)
        track_row = (
            await session.execute(
                text(
                    "SELECT id, title FROM library_tracks WHERE file_path = :p"
                ),
                {"p": expected_path},
            )
        ).first()
        if track_row is None:
            return self._reply(
                ctx,
                "I couldn't find the current track in your library. "
                "Try 'rescan my library'.",
            )
        track_id = int(track_row[0])
        track_title = track_row[1]

        # Find-or-create playlist. Multi-match → disambiguation.
        match_rows = (
            await session.execute(
                text(
                    """
                    SELECT id, name FROM playlists
                    WHERE LOWER(name) LIKE :like
                    ORDER BY (LOWER(name) = LOWER(:exact)) DESC, name
                    LIMIT 5
                    """
                ),
                {"like": f"%{name.lower()}%", "exact": name.lower()},
            )
        ).all()
        if not match_rows:
            # Auto-create per user's spec.
            inserted = (
                await session.execute(
                    text(
                        "INSERT INTO playlists (name) VALUES (:n) RETURNING id, name"
                    ),
                    {"n": name},
                )
            ).first()
            if inserted is None:
                return self._reply(ctx, "I couldn't create that playlist.")
            await session.execute(
                text("SELECT pg_notify('playlists_changed', 'created')")
            )
            return await self._append_track(
                playlist_id=int(inserted[0]),
                playlist_name=inserted[1],
                track_id=track_id,
                ctx=ctx,
                session=session,
            )
        if len(match_rows) == 1:
            return await self._append_track(
                playlist_id=int(match_rows[0][0]),
                playlist_name=match_rows[0][1],
                track_id=track_id,
                ctx=ctx,
                session=session,
            )
        # Multi-candidate: park a different kind of confirmation.
        candidates = [{"id": int(r[0]), "name": r[1]} for r in match_rows]
        if ctx.session_id is not None:
            try:
                await request_confirmation(
                    session,
                    ctx.session_id,
                    kind="core.playlist_add_choice",
                    handler=self.name,
                    data={"candidates": candidates, "track_id": track_id},
                )
            except Exception as e:
                log.warning("add-to: couldn't park pending: %s", e)
        names = " or ".join(c["name"] for c in candidates[:2])
        return Response(
            text=(
                f"I have {len(candidates)} matching playlists — "
                f"{names}? (For {track_title or 'this track'}.)"
            ),
            session_id=ctx.session_id,
            matched_handler=self.name,
            expect_followup=True,
        )

    async def _append_track(
        self,
        *,
        playlist_id: int,
        playlist_name: str,
        track_id: int,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        # Dup check.
        dup = (
            await session.execute(
                text(
                    "SELECT 1 FROM playlist_tracks "
                    "WHERE playlist_id = :pid AND track_id = :tid"
                ),
                {"pid": playlist_id, "tid": track_id},
            )
        ).first()
        if dup is not None:
            return self._reply(
                ctx, f"That's already in your {playlist_name} playlist."
            )
        await session.execute(
            text(
                """
                INSERT INTO playlist_tracks (playlist_id, track_id, position)
                VALUES (
                    :pid, :tid,
                    COALESCE(
                        (SELECT MAX(position) + 1 FROM playlist_tracks
                         WHERE playlist_id = :pid),
                        0
                    )
                )
                """
            ),
            {"pid": playlist_id, "tid": track_id},
        )
        await session.execute(
            text("UPDATE playlists SET updated_at = NOW() WHERE id = :id"),
            {"id": playlist_id},
        )
        await session.execute(text("SELECT pg_notify('playlists_changed', 'added')"))
        return self._reply(ctx, f"Added to {playlist_name}.")

    # ─── Helpers ───────────────────────────────────────────────────────

    async def _park_play_confirmation(
        self,
        ctx: Context,
        session: AsyncSession,
        candidates: list[dict[str, Any]],
        mode: str,
    ) -> None:
        if ctx.session_id is None:
            return
        try:
            await request_confirmation(
                session,
                ctx.session_id,
                kind="core.playlist_choice",
                handler=self.name,
                data={"candidates": candidates, "mode": mode},
            )
        except Exception as e:
            log.warning("playlist: couldn't park pending: %s", e)

    def _reply(self, ctx: Context, text_: str) -> Response:
        return Response(
            text=text_,
            session_id=ctx.session_id,
            matched_handler=self.name,
        )
