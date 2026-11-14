"""The room's queue and Up next by voice (UP-NEXT-QUEUE-PLAN §7, phase P6).

MusicHandler's fast paths for the queue a room is playing:

=================================  ==========================================
Say                                Does
=================================  ==========================================
"what's next", "what's up next",   "Next is Title by Artist, then Title."
"what's playing after this"        (the next two songs in the room's queue)
"play X next", "queue X next"      X goes right after the song playing now
"add X to the queue",              X goes after what was asked for, before
"queue up X", "queue X"            the Up next picks. Nothing playing: X
                                   plays now, as "play X" always did (owner,
                                   2026-11-12)
"clear the queue"                  everything after the song playing now
                                   goes; Up next refills while autoplay is on
"skip this artist",                skip, and that artist is out of this mix;
"no more <artist>"                 their waiting picks leave the queue
"turn autoplay off/on",            the room's Autoplay switch
"stop after this list"
"why is this playing",             the pick's reason, in words
"why this song"
"play something like this",        Up next starts again from the song
"more like this"                   playing now (library mode); the song
                                   plays on, the waiting picks are replaced
=================================  ==========================================

The closed phrasings take a room after them ("what's next in the garage",
"clear the queue in the office": :func:`~domovoi.handlers.music_rooms.
control_room_patterns`), so their patterns have no groups of their own.
"play X next" / "add X to the queue" act on the room that asked.

**Where they sit.** In MusicHandler (band 300), before its "play X by Y"
and "play (.+)" catch-alls, so "play jazz next" is not a search for "jazz
next" and "play something like this" not one for "something like this".
Lists (178) keeps "add X to the shopping list" and playlists (290) keep
"add this to my road trip playlist" and "add X after this": none of these
patterns ends on "list" or "playlist". "play the next song" and "play
next" stay skips (MusicHandler's own _NEXT_RE / the router), never "play X
next": X may not be empty, "the", or "the next song".

**Whose mix.** A command that changes the room's session from an
identified voice ("play X next", "skip this artist", "autoplay off", "more
like this") moves the mix to that person (plan §2.3). Reads ("what's
next", "why is this playing") change nothing.

Every reply is short and plain. Nothing here needs the internet
(``requires_network`` stays "no" on MusicHandler).
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.models import Context, Response
from domovoi.log_privacy import said

log = logging.getLogger(__name__)

# ─── Patterns ─────────────────────────────────────────────────────────────

_THIS = r"(?:this|that|it)(?: one| song| track| tune)?"

WHATS_NEXT_RE = re.compile(
    r"^(?:"
    r"what(?:'s| is) (?:up )?next"
    r"|what(?:'s| is) (?:playing|coming|on|coming up) (?:next|after " + _THIS + r")"
    r"|what(?:'s| is) after " + _THIS +
    r"|what (?:song |track )?(?:plays|comes) (?:next|after " + _THIS + r")"
    r"|what(?:'s| is) the next (?:song|track)"
    r"|what song is (?:next|up next)"
    r"|what(?:'s| is) in the queue"
    r")$"
)

#: "play X next" / "queue X next" / "put X on next". X may not be empty,
#: "the", or a word that only points at what plays ("play this next").
PLAY_NEXT_RE = re.compile(
    r"^(?:play|queue|cue|put on|put) "
    r"(?!(?:the|it|this|that|this one|that one|one|something|anything)(?: on)? next(?: please)?$)"
    r"(?P<q>.+?)(?: on)? next(?: please)?$"
)

#: "add X to the queue", "queue up X", "queue X" (not "queue it up").
ADD_TO_QUEUE_RE = re.compile(
    r"^(?:"
    r"(?:add|put) (?!(?:it|this|that|this one|this song)\s)(?P<q>.+?) "
    r"(?:to|in|on|onto|into|at the end of) (?:the|my|our|this) (?:play |music |song )?queue"
    r"|(?:queue|cue) up (?!(?:it|this|that|next)$)(?P<q2>.+?)"
    r"|queue (?!(?:it|this|that|up)$)(?!it up$)(?!up\b)(?P<q3>.+?)"
    r")(?: please| for me)?$"
)

CLEAR_QUEUE_RE = re.compile(
    r"^(?:"
    r"(?:clear|empty|wipe)(?: out)? (?:the |my |our |this )?(?:play |music |song )?queue"
    r"|clear (?:the )?up next"
    r")$"
)

SKIP_ARTIST_RE = re.compile(
    r"^(?:"
    r"(?:skip|no more|enough of|enough with|stop playing) (?:this|that|the) (?:artist|band|singer|group)"
    r"|skip (?:this|that) artist(?: please)?"
    r"|no more (?:songs |music )?(?:by|from) (?:this|that|the) (?:artist|band|singer|group)"
    r")$"
)

#: "no more <artist>": declined (the router routes on) when <artist> isn't
#: an artist in the library, or nothing plays.
NO_MORE_ARTIST_RE = re.compile(
    r"^(?:no more|enough of|enough with) (?!(?:this|that|the) (?:artist|band|singer|group)$)"
    r"(?P<artist>.+?)(?: songs| music| please| for now| today)*$"
)

AUTOPLAY_RE = re.compile(
    r"^(?:"
    r"(?:turn|switch|set) (?:on|off) (?:the )?auto ?play"
    r"|(?:turn|switch|set) (?:the )?auto ?play (?:on|off)"
    r"|auto ?play (?:on|off)"
    r"|(?:enable|disable|stop|start) (?:the )?auto ?play"
    r"|stop (?:the music )?(?:after|at the end of) (?:this|the) (?:list|playlist|album|queue)"
    r")$"
)

WHY_RE = re.compile(
    r"^(?:"
    r"why (?:is|was) (?:this|that|it) (?:song |track |one )?(?:playing|on)"
    r"|why (?:this|that) (?:song|one|track)"
    r"|why (?:did you|did it|does it) (?:play|pick|choose) (?:this|that|it)(?: one| song| track)?"
    r"|why (?:are you|is it) playing (?:this|that)(?: one| song| track)?"
    r")$"
)

MORE_LIKE_RE = re.compile(
    r"^(?:"
    r"(?:play |give me |find )?(?:something|more|some more|songs|more songs|music|more music) like "
    r"(?:this|that|it)(?: one| song| track)?"
    r"|(?:play )?more of (?:this|that)(?: kind of music| sort of music| kind of thing)?"
    r")(?: please)?$"
)

#: The closed patterns that take a room after them (no groups of their own).
ROOM_SUFFIXABLE = (WHATS_NEXT_RE, CLEAR_QUEUE_RE, SKIP_ARTIST_RE, AUTOPLAY_RE, WHY_RE, MORE_LIKE_RE)

#: What each fast path is, for the checks of a command said for another room
#: (music_rooms ``_ACTION_BY_METHOD``): reads a guest may make; edits never.
ACTION_BY_METHOD = {
    "_whats_next_from_match": "now_playing",
    "_why_from_match": "now_playing",
    "_clear_queue_from_match": "queue",
    "_skip_artist_from_match": "queue",
    "_autoplay_from_match": "queue",
    "_more_like_from_match": "queue",
}

#: An artist's songs added by "play/add <artist>": this many, shuffled.
ARTIST_ADD = 10
#: An album's songs added: at most this many, in order.
ALBUM_ADD = 50

_PHONE_TEXT = "On a phone, change what plays next from the queue screen."
_NOTHING_TEXT = "Nothing is playing in here right now."

_ERA_DECADE = re.compile(r"^\d0s$|^\d{4}s$")


def _clean(s: str) -> str:
    return s.strip().rstrip(".,!?").strip()


def _song_name(title: str | None, artist: str | None, file_: str | None = None) -> str:
    from domovoi.handlers.shared.spoken_names import speakable

    t = title or (file_ or "").rsplit("/", 1)[-1].rsplit(".", 1)[0] or "a song"
    t = speakable(t) or t
    if artist:
        return f"{t} by {speakable(artist) or artist}"
    return t


class _Room:
    """The room's player as one read: the client, the queue entries, the
    index of the song playing now (None: nothing current), the state."""

    def __init__(self, mpd: Any, queue: list[dict[str, Any]], cur: int | None, state: str) -> None:
        self.mpd = mpd
        self.queue = queue
        self.cur = cur
        self.state = state

    @property
    def playing(self) -> bool:
        return self.cur is not None and self.state in ("play", "pause")

    @property
    def current(self) -> dict[str, Any] | None:
        return self.queue[self.cur] if self.cur is not None else None

    @property
    def after(self) -> list[dict[str, Any]]:
        return self.queue[self.cur + 1:] if self.cur is not None else list(self.queue)


class MusicQueueMixin:
    """MusicHandler's queue commands (module docstring). Relies on
    MusicHandler for ``name``, ``_play``, ``_smart_skip`` and
    ``_unreachable``."""

    QUEUE_ACTIONS = ACTION_BY_METHOD

    def queue_fast_paths(self) -> list[Any]:
        """The fast paths, none an early commit: "what's next" also starts
        "what's next on my calendar", and "clear the queue" "clear the
        queue in the office"."""
        from domovoi.handlers.base import FastPath

        cls = type(self)
        return [
            FastPath(WHATS_NEXT_RE, cls._whats_next_from_match),
            FastPath(WHY_RE, cls._why_from_match),
            FastPath(CLEAR_QUEUE_RE, cls._clear_queue_from_match),
            FastPath(SKIP_ARTIST_RE, cls._skip_artist_from_match),
            FastPath(AUTOPLAY_RE, cls._autoplay_from_match),
            FastPath(MORE_LIKE_RE, cls._more_like_from_match),
            FastPath(NO_MORE_ARTIST_RE, cls._no_more_artist_from_match),
            FastPath(PLAY_NEXT_RE, cls._play_next_from_match),
            FastPath(ADD_TO_QUEUE_RE, cls._add_to_queue_from_match),
        ]

    # ── adapters ──
    async def _whats_next_from_match(self, m, ctx, session):
        return await self.whats_next(ctx, session)

    async def _why_from_match(self, m, ctx, session):
        return await self.why_this(ctx, session)

    async def _clear_queue_from_match(self, m, ctx, session):
        return await self.clear_queue(ctx, session)

    async def _skip_artist_from_match(self, m, ctx, session):
        return await self.skip_artist(ctx, session)

    async def _no_more_artist_from_match(self, m, ctx, session):
        return await self.no_more_artist(_clean(m.group("artist")), ctx, session)

    async def _autoplay_from_match(self, m, ctx, session):
        t = m.string
        on = not (" off" in f" {t} " or "disable" in t or t.startswith("stop"))
        return await self.set_autoplay(on, ctx, session, after_list=t.startswith("stop") and "auto" not in t)

    async def _more_like_from_match(self, m, ctx, session):
        return await self.more_like_this(ctx, session)

    async def _play_next_from_match(self, m, ctx, session):
        return await self.add_to_queue(_clean(m.group("q")), "next", ctx, session)

    async def _add_to_queue_from_match(self, m, ctx, session):
        q = m.group("q") or m.group("q2") or m.group("q3") or ""
        return await self.add_to_queue(_clean(q), "end", ctx, session)

    # ── shared ──
    def _q_reply(self, ctx: Context, text_: str) -> Response:
        return Response(text=text_, session_id=ctx.session_id, matched_handler=self.name)  # type: ignore[attr-defined]

    async def _read_room(self, ctx: Context) -> _Room | Response | None:
        """The room's player read once; a reply when it can't be (phone,
        no room, unreachable); None when the room has no player yet."""
        from domovoi.clients.mpd import MPDNotProvisioned, get_mpd_client_for

        if ctx.from_phone or not ctx.room_id:
            return self._q_reply(ctx, _PHONE_TEXT)
        try:
            mpd = get_mpd_client_for(ctx.room_id)
        except MPDNotProvisioned:
            return None
        try:
            queue = list(await mpd.queue_list())
            current = (await mpd.current_song()) or {}
            state = await mpd.state()
        except Exception as e:  # noqa: BLE001 — any failure to reach it
            log.warning("queue voice: the room's music player didn't answer: %s", e)
            return self._unreachable(ctx)  # type: ignore[attr-defined]
        cur = None
        cur_id = current.get("id", current.get("Id"))
        if cur_id is not None:
            for i, e in enumerate(queue):
                if str(e.get("id")) == str(cur_id):
                    cur = i
                    break
        return _Room(mpd, queue, cur, str(state or "stop"))

    async def _active(self, s: AsyncSession, room_id: str, *, lock: bool = False):
        from domovoi.up_next import rooms as up_next_rooms
        from domovoi.up_next import store

        st = await store.active_for(s, up_next_rooms.target_for(room_id))
        if st is not None and lock:
            st = await store.lock(s, st.sess.id)
        return st

    @staticmethod
    def _claim_for_speaker(st: Any, ctx: Context) -> None:
        """An identified voice changing the room's mix: it is theirs now."""
        pid = getattr(ctx, "person_id", None)
        if pid is not None and st.sess.person_id != pid:
            st.sess.set_listener(pid)

    async def _current_track(self, s: AsyncSession, room: _Room) -> int | None:
        from domovoi.up_next import rooms as up_next_rooms

        cur = room.current
        f = str((cur or {}).get("file") or "")
        if not f:
            return None
        return (await up_next_rooms.tracks_for_files(s, [f])).get(f)

    # ── what's next ──
    async def whats_next(self, ctx: Context, session: AsyncSession) -> Response:
        room = await self._read_room(ctx)
        if isinstance(room, Response):
            return room
        if room is None or not room.playing:
            return self._q_reply(ctx, _NOTHING_TEXT)
        nxt = room.after[:2]
        if not nxt:
            async with _scope() as s:
                st = await self._active(s, ctx.room_id)  # type: ignore[arg-type]
            if st is not None and st.sess.autoplay and not st.sess.ended:
                return self._q_reply(
                    ctx, "Nothing yet. Up next will add songs like this one before it ends.")
            return self._q_reply(ctx, "Nothing. The queue ends with this song.")
        names = [_song_name(e.get("title"), e.get("artist"), e.get("file")) for e in nxt]
        if len(names) == 1:
            return self._q_reply(ctx, f"Next is {names[0]}. That's the last one in the queue.")
        return self._q_reply(ctx, f"Next is {names[0]}, then {names[1]}.")

    # ── play X next / add X to the queue ──
    async def _resolve_tracks(self, query: str, session: AsyncSession) -> tuple[list[int], str] | str:
        """Library track ids for a spoken name, and how to say it — or the
        reply when there is no sure answer."""
        from domovoi.handlers.shared import library_match
        from domovoi.handlers.shared.library_dedup import find_fuzzy_library_match

        res = await library_match.resolve_request(session, {"any": query})
        if res.decision == "play" and res.candidates:
            c = res.candidates[0]
            ids = [int(i) for i in c.track_ids]
            if not ids:
                return f"I couldn't find {query} in your library."
            if c.ref.type in ("artist", "credit"):
                ids = random.sample(ids, min(ARTIST_ADD, len(ids)))
            elif c.ref.type == "album":
                ids = ids[:ALBUM_ADD]
            else:
                ids = ids[:1]
            return ids, c.speak or query
        if res.decision == "ask":
            return f"I'm not sure which {query} you mean. Say its full name, and the artist."
        match = await find_fuzzy_library_match(session, title=query, artist=None, clean_title=True)
        if match is not None:
            row = (await session.execute(
                text("SELECT id, title, artist FROM library_tracks WHERE title = :t "
                     "AND artist IS NOT DISTINCT FROM :a ORDER BY id LIMIT 1"),
                {"t": match["title"], "a": match["artist"]})).first()
            if row is not None:
                return [int(row[0])], _song_name(row[1], row[2])
        return f"I couldn't find {query} in your library."

    async def _insert_at(self, room_id: str, room: _Room, where: str) -> int | None:
        """Where an add goes: ``next`` right after the song playing now;
        ``end`` before the first Up next pick waiting after it (None:
        append)."""
        from domovoi.up_next import rooms as up_next_rooms

        assert room.cur is not None
        if where == "next":
            return room.cur + 1
        items = [{"song_id": int(e["id"]), "file": e.get("file") or ""}
                 for e in room.queue if e.get("id") is not None]
        await up_next_rooms.queue_view(room_id, items)
        for n, it in enumerate(items):
            if n > room.cur and it.get("origin") == "auto":
                return n
        return None

    async def add_to_queue(self, query: str, where: str, ctx: Context, session: AsyncSession) -> Response:
        if not query:
            return self._q_reply(ctx, "Add what to the queue?")
        room = await self._read_room(ctx)
        if isinstance(room, Response):
            return room
        if room is None or not room.playing:
            # Nothing playing: "queue up X" plays X, as it always did.
            return await self._play({"any": query}, ctx, session)  # type: ignore[attr-defined]
        found = await self._resolve_tracks(query, session)
        if isinstance(found, str):
            return self._q_reply(ctx, found)
        ids, name = found
        from domovoi.up_next import rooms as up_next_rooms

        uris_by = await up_next_rooms.uris_for(session, ids)
        uris = [uris_by[i] for i in ids if i in uris_by]
        if not uris:
            return self._q_reply(ctx, f"The music player can't reach {name}. Try 'rescan my library'.")
        try:
            pos = await self._insert_at(ctx.room_id, room, where)  # type: ignore[arg-type]
            added = [a for a in await room.mpd.queue_add_files(uris, position=pos) if a]
        except Exception as e:  # noqa: BLE001
            log.warning("queue voice: adding %r failed: %s", said(query), e)
            return self._unreachable(ctx)  # type: ignore[attr-defined]
        if not added:
            return self._q_reply(ctx, f"The music player couldn't find {name}. Try 'rescan my library'.")
        await self._speaker_takes_mix(ctx)
        n = len(added)
        what = name if n == 1 else f"{n} songs by {name}" if len(ids) > 1 and n > 1 else name
        if where == "next":
            return self._q_reply(ctx, f"Okay, {what} {'plays' if n == 1 else 'play'} next.")
        return self._q_reply(ctx, f"Added {what} to the queue.")

    async def _speaker_takes_mix(self, ctx: Context) -> None:
        """Best-effort: an identified voice editing the room's queue makes
        the room's mix theirs."""
        if getattr(ctx, "person_id", None) is None or not ctx.room_id:
            return
        from domovoi.up_next import store

        try:
            async with _scope() as s:
                st = await self._active(s, ctx.room_id, lock=True)
                if st is not None and st.sess.person_id != ctx.person_id:
                    st.sess.set_listener(ctx.person_id)
                    await store.save(s, st)
        except Exception as e:  # noqa: BLE001
            log.debug("queue voice: the mix stays whose it was: %s", e)

    # ── clear ──
    async def clear_queue(self, ctx: Context, session: AsyncSession) -> Response:
        room = await self._read_room(ctx)
        if isinstance(room, Response):
            return room
        if room is None or not room.queue:
            return self._q_reply(ctx, "The queue is already empty.")
        from domovoi.up_next import rooms as up_next_rooms

        if not room.playing:
            try:
                await room.mpd.queue_clear()
            except Exception as e:  # noqa: BLE001
                log.warning("queue voice: clear failed: %s", e)
                return self._unreachable(ctx)  # type: ignore[attr-defined]
            await up_next_rooms.end(ctx.room_id, "the queue was cleared")  # type: ignore[arg-type]
            return self._q_reply(ctx, "Cleared.")
        drop = [int(e["id"]) for e in room.after if e.get("id") is not None]
        if drop:
            try:
                await room.mpd.queue_remove(drop)
            except Exception as e:  # noqa: BLE001
                log.warning("queue voice: clear failed: %s", e)
                return self._unreachable(ctx)  # type: ignore[attr-defined]
        async with _scope() as s:
            st = await self._active(s, ctx.room_id)  # type: ignore[arg-type]
        if st is not None and st.sess.autoplay and not st.sess.ended:
            return self._q_reply(ctx, "Cleared. Up next will keep going.")
        return self._q_reply(ctx, "Cleared.")

    # ── skip this artist / no more X ──
    async def _exclude_artist(self, ctx: Context, track_id: int | None, artist_norm: str | None,
                              room: _Room) -> bool:
        """Leave an artist (or, with none, the track) out of the room's
        mix and take their waiting picks out of the queue. False without
        a session."""
        from domovoi.up_next import service, store
        from domovoi.up_next.model import Track

        async with _scope() as s:
            st = await self._active(s, ctx.room_id, lock=True)  # type: ignore[arg-type]
            if st is None:
                return False
            track = Track(id=int(track_id or 0), artist_norm=artist_norm or "")
            st.sess.less_like_this(track)
            self._claim_for_speaker(st, ctx)
            seqs = await service.take_back(s, st, track)
            await store.save(s, st)
            gone = [p for p in await store.picks(s, st.sess.id) if p["seq"] in set(seqs)]
        cur_id = (room.current or {}).get("id")
        song_ids = [int(p["mpd_song_id"]) for p in gone
                    if p["mpd_song_id"] is not None and str(p["mpd_song_id"]) != str(cur_id)]
        if song_ids:
            try:
                await room.mpd.queue_remove(song_ids)
            except Exception as e:  # noqa: BLE001 — the extender takes them out
                log.debug("queue voice: picks left for the extender: %s", e)
        return True

    async def skip_artist(self, ctx: Context, session: AsyncSession) -> Response:
        room = await self._read_room(ctx)
        if isinstance(room, Response):
            return room
        if room is None or not room.playing:
            return self._q_reply(ctx, _NOTHING_TEXT)
        cur = room.current or {}
        tid = await self._current_track(session, room)
        artist_norm = None
        who = cur.get("artist")
        if tid is not None:
            row = (await session.execute(
                text("SELECT artist_norm, artist FROM library_tracks WHERE id = :t"), {"t": tid})).first()
            if row is not None:
                artist_norm, who = row[0], row[1] or who
        excluded = False
        if tid is not None:
            excluded = await self._exclude_artist(ctx, tid, artist_norm, room)
        skipped = await self._smart_skip(ctx, session)  # type: ignore[attr-defined]
        if skipped.failure:
            return skipped
        if excluded and who:
            from domovoi.handlers.shared.spoken_names import speakable

            skipped.text = f"Okay, no more {speakable(who) or who} in this mix."
        elif excluded:
            skipped.text = "Okay, no more of that in this mix."
        return skipped

    async def no_more_artist(self, artist: str, ctx: Context, session: AsyncSession) -> Response | None:
        """ "no more <artist>": declined when <artist> isn't one of the
        library's artists (the router routes the turn on)."""
        from domovoi.handlers.shared import library_match

        if not artist or ctx.from_phone or not ctx.room_id:
            return None
        res = await library_match.resolve_request(
            session, {"artist": artist}, kinds=frozenset({"artist", "credit"}))
        if res.decision != "play" or not res.candidates or not res.candidates[0].track_ids:
            return None
        c = res.candidates[0]
        first = int(c.track_ids[0])
        artist_norm = (await session.execute(
            text("SELECT artist_norm FROM library_tracks WHERE id = :t"), {"t": first})).scalar()
        room = await self._read_room(ctx)
        if isinstance(room, Response):
            return room
        if room is None or not room.playing:
            return self._q_reply(ctx, _NOTHING_TEXT)
        if not await self._exclude_artist(ctx, first, artist_norm, room):
            return self._q_reply(ctx, "There's no Up next playing in here to leave them out of.")
        now = await self._current_track(session, room)
        now_norm = None
        if now is not None:
            now_norm = (await session.execute(
                text("SELECT artist_norm FROM library_tracks WHERE id = :t"), {"t": now})).scalar()
        if artist_norm and now_norm == artist_norm:
            skipped = await self._smart_skip(ctx, session)  # type: ignore[attr-defined]
            if skipped.failure:
                return skipped
        return self._q_reply(ctx, f"Okay, no more {c.speak or artist} in this mix.")

    # ── autoplay ──
    async def set_autoplay(self, on: bool, ctx: Context, session: AsyncSession, *,
                           after_list: bool = False) -> Response:
        from domovoi.up_next import rooms as up_next_rooms
        from domovoi.up_next import store
        from domovoi.up_next.session import SeedRef

        room = await self._read_room(ctx)
        if isinstance(room, Response):
            return room
        async with _scope() as s:
            st = await self._active(s, ctx.room_id, lock=True)  # type: ignore[arg-type]
            if st is not None:
                st.sess.autoplay = on
                if on and st.sess.mode == "list" and st.sess.ended:
                    st.sess.ended = False
                self._claim_for_speaker(st, ctx)
                await store.save(s, st)
        if st is None and on:
            # No session: the queue's library songs become one, armed.
            if room is None or not room.playing:
                return self._q_reply(ctx, "Autoplay is on for the next thing you play.")
            files = [str(e.get("file") or "") for e in room.queue[room.cur or 0:]]
            async with _scope() as s:
                by_file = await up_next_rooms.tracks_for_files(s, files)
            ids = [by_file[f] for f in files if f in by_file]
            if not ids:
                return self._q_reply(ctx, "Autoplay works with songs from your library, and this isn't one.")
            await up_next_rooms.start(
                ctx.room_id,  # type: ignore[arg-type]
                mode="continue", seed=SeedRef(track_id=ids[0], kind="ids", track_ids=tuple(ids)),
                name="this queue", person_id=getattr(ctx, "person_id", None), autoplay=True,
                client=room.mpd,
            )
        if on:
            return self._q_reply(ctx, "Autoplay is on. Similar songs will follow when the queue ends.")
        if after_list:
            return self._q_reply(ctx, "Okay, the music stops when the queue ends.")
        return self._q_reply(ctx, "Autoplay is off. The music stops when the queue ends.")

    # ── why is this playing ──
    async def why_this(self, ctx: Context, session: AsyncSession) -> Response:
        from domovoi.up_next import service

        room = await self._read_room(ctx)
        if isinstance(room, Response):
            return room
        if room is None or not room.playing:
            return self._q_reply(ctx, _NOTHING_TEXT)
        cur_id = (room.current or {}).get("id")
        async with _scope() as s:
            st = await self._active(s, ctx.room_id)  # type: ignore[arg-type]
            pick = None
            seed = None
            if st is not None and cur_id is not None:
                row = (await s.execute(
                    text("SELECT reason_code, reason_label, seed_track_id FROM up_next_picks "
                         "WHERE session_id = :s AND mpd_song_id = :m ORDER BY seq DESC LIMIT 1"),
                    {"s": st.sess.id, "m": int(cur_id)})).mappings().first()
                pick = dict(row) if row is not None else None
                if pick is not None and pick["seed_track_id"] is not None:
                    seed = await service.track_row(s, int(pick["seed_track_id"]))
        return self._q_reply(ctx, why_text(pick, seed, st))

    # ── more like this ──
    async def more_like_this(self, ctx: Context, session: AsyncSession) -> Response:
        from domovoi.up_next import rooms as up_next_rooms
        from domovoi.up_next import store
        from domovoi.up_next.session import SeedRef

        room = await self._read_room(ctx)
        if isinstance(room, Response):
            return room
        if room is None or not room.playing:
            return self._q_reply(ctx, _NOTHING_TEXT)
        tid = await self._current_track(session, room)
        if tid is None:
            return self._q_reply(
                ctx, "This isn't a song from your library, so I can't find more like it.")
        cur = room.current or {}
        person = getattr(ctx, "person_id", None)
        autoplay = True
        # The waiting picks of the mix playing now make way for the new one.
        async with _scope() as s:
            st = await self._active(s, ctx.room_id)  # type: ignore[arg-type]
            waiting: list[int] = []
            if st is not None:
                if person is None:
                    person = st.sess.person_id
                waiting = [int(p["mpd_song_id"]) for p in await store.picks(s, st.sess.id, statuses=("queued",))
                           if p["mpd_song_id"] is not None]
        after = {int(e["id"]) for e in room.after if e.get("id") is not None}
        drop = [i for i in waiting if i in after]
        if drop:
            try:
                await room.mpd.queue_remove(drop)
            except Exception as e:  # noqa: BLE001
                log.warning("queue voice: the old picks stay: %s", e)
        sid = await up_next_rooms.start(
            ctx.room_id,  # type: ignore[arg-type]
            mode="library", seed=SeedRef(track_id=tid), person_id=person, autoplay=autoplay,
            client=room.mpd,
        )
        if sid is None:
            return self._q_reply(ctx, "I couldn't start a mix from this song right now.")
        return self._q_reply(
            ctx, f"Okay. Songs like {_song_name(cur.get('title'), None, cur.get('file'))} are up next.")


def _scope():
    from domovoi.db.session import session_scope

    return session_scope()


def why_text(pick: dict[str, Any] | None, seed: dict[str, Any] | None, st: Any) -> str:
    """ "Why is this playing", in words, from the pick's own reason. Never
    names a person (the room is household-visible)."""
    if pick is None:
        if st is not None and st.sess.mode in ("list", "continue") and st.list_name:
            return f"It's part of {st.list_name}, which was asked for."
        return "It was asked for. It isn't an Up next pick."
    code = str(pick.get("reason_code") or "")
    label = str(pick.get("reason_label") or "").strip()
    like = "the song you started with"
    if seed is not None and st is not None and seed.get("id") != st.sess.seed.track_id:
        like = _song_name(seed.get("title"), None)
    if code == "same_artist" or label == "same artist":
        return f"Up next picked it: same artist as {like}."
    if code == "same_era" or _ERA_DECADE.match(label) or label.startswith("from ") and label.endswith("-ish"):
        era = f"the {label}" if _ERA_DECADE.match(label) else label
        return f"Up next picked it: it's from {era.removeprefix('from ')}, like {like}."
    if code == "same_genre":
        return f"Up next picked it: it's {label}, like {like}."
    if code == "played_together":
        if label.startswith("from "):
            return f"Up next picked it: it's on your {label.removeprefix('from ')} playlist with {like}."
        return f"Up next picked it: it's often played together with {like}."
    if code == "similar_artist":
        return f"Up next picked it: it's by an artist {label or 'similar to what you were playing'}."
    if code == "wildcard":
        return "Up next picked it as a wildcard, a song from anywhere in your library to keep the mix fresh."
    if code == "from_list":
        name = st.list_name if st is not None and st.list_name else "the list"
        if label and label not in ("from this list",) and not label.startswith("from "):
            return f"It's from {name}. Up next chose it now because it's {label}, like {like}."
        return f"It's the next song from {name}."
    return "Up next picked it to follow the songs before it."


__all__ = [
    "ACTION_BY_METHOD", "ADD_TO_QUEUE_RE", "AUTOPLAY_RE", "CLEAR_QUEUE_RE", "MORE_LIKE_RE",
    "MusicQueueMixin", "NO_MORE_ARTIST_RE", "PLAY_NEXT_RE", "ROOM_SUFFIXABLE", "SKIP_ARTIST_RE",
    "WHATS_NEXT_RE", "WHY_RE", "why_text",
]
