"""Music in another room by voice: "play jazz in the garage".

A music command may end with a place (``domovoi/handlers/shared/rooms.py``
reads it): "play <thing> in the garage", "put <artist> on in the office",
"play some music in the dining room", "stop / pause / skip / turn it up in
the garage", "what's playing in the garage", "... in here", "...
everywhere". :class:`MusicRoomsMixin` gives MusicHandler two fast paths for
them, before its "play X by Y" and "play (.+)" catch-alls, so the catch-all
no longer searches the library for "some music in the garage" (the
2026-10-14 incident).

What it does with one, in order:

1. **Find the place.** Every way the words can split into a command and a
   place is tried, the longest place first; the first split whose place
   is this room ("in here"), every room ("everywhere", "the whole house")
   or one of the house's rooms (by id or label) wins. Otherwise a place
   that merely sounds like a room ("in the attic") is kept for the
   unknown-room answer. Nothing like a place: the turn is MusicHandler's
   as if these paths did not exist ("play the boys in the band").
2. **Song or room?** A play whose WHOLE object is a name in the library
   ("play dancing in the kitchen" with a song of that name) is that name,
   played here, unless the words before the place are a name too ("play
   weezer in the garage": Weezer, in the garage). Both a name: Domovoi
   asks ("Did you mean Dancing in the Kitchen by …? Say no to play
   dancing in the kitchen instead."), parked as ``core.music_room_or_title``.
   With no words before the place at all ("play in the garage") it is
   never a room: that is the song "In the Garage".
3. **May it, and can it?** Another room: a satellite that connected
   without a pairing token (lenient pairing) may only do what a guest of
   the house may do to a room that is actively playing — pause, resume,
   skip, go back, ask what's playing (``domovoi/guest_queue.py``); a play,
   a stop or a volume change there is refused, as intercom and drop-in
   refuse it (CORE-11). A play, resume, skip or volume change needs the
   room's satellite connected ("The speaker in the garage is offline right
   now"). A play in a room whose music player isn't running yet starts it
   (``mpd_provisioner.ensure_room`` — one container per room, the room's
   own id, a room the house already has: never one from a name somebody
   said).
4. **Do it there, say it here.** The command runs on a copy of the turn's
   context for the target room (``Context.asked_from_room`` = the room
   that asked); the target's ``music_start`` / ``music_stop`` / volume go
   to ITS satellite (``domovoi/room_music.py``), and the reply is said
   where it was asked: "Playing Weezer in the garage, shuffled."

Groups (1.3.0, domovoi/music_groups.py): rooms can share one music queue.
"Play jazz everywhere" plays here and every connected room hears it; "play
jazz upstairs" / "... in the north wing" does the same for a named group (a
group's name needs no "in"); "play this everywhere", "play this in the
office too", "share this with the garage" let other rooms hear what plays
here; "join the kitchen" / "play the kitchen's music in here" makes this
room hear the kitchen's; "stop" in a room of a group stops the whole
group, as "pause" pauses it (owner, 2026-11-15); "stop here", "stop the
music in here" and "leave the group" stop only that room (it leaves, the
others play on), as "stop the music in the office only" does for another
room; "just in here" keeps the music here and stops the others; "which rooms are playing this" names them. A stop or a
pause "everywhere" or "upstairs" applies to every such room that plays.
Bringing music into other rooms needs a paired satellite (CORE-11).

The language model's ``music`` tool takes the same place as its ``room``
argument (:meth:`MusicRoomsMixin.room_tool_call`).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.handlers.room_commands import (
    _PREP,
    EVERYWHERE_OTHER_TEXT,
    NO_APP_TEXT,
    ROOM_TAIL,
    UNPAIRED_TEXT,
    RoomCommandsMixin,
    say_in_room,
    unknown_room_text,
)
from domovoi.handlers.shared import rooms as rooms_mod
from domovoi.handlers.shared.rooms import Place, Room
from domovoi.models import Context, Response
from domovoi.log_privacy import said

log = logging.getLogger(__name__)

#: The yes/no asked when a request reads both as a name in the library and
#: as a play in another room.
ROOM_OR_TITLE_KIND = "core.music_room_or_title"
ROOM_OR_TITLE_TTL_SEC = 60.0

# ─── The fast paths' patterns ────────────────────────────────────────────

#: "play X in R", "put on X in R", "put X on in R", "shuffle X in R".
ROOM_PLAY_RE = re.compile(r"^(?:play|put on|put|shuffle) .+?" + ROOM_TAIL + r"$")

#: Control phrasings that only make sense with a room after them, read as
#: the music command each means.
CONTROL_ALIASES: dict[str, str] = {
    "stop playing": "stop",
    "stop the song": "stop",
    "turn off the music": "stop",
    "turn the music off": "stop",
    "shut off the music": "stop",
    "pause the song": "pause",
    # Whisper's usual hearing of "skip this song" (ft run 2026-10-15).
    "skip the song": "skip",
    "skip the track": "skip",
    "skip that song": "skip",
    "what's on": "what's playing",
    "what is on": "what's playing",
    "what song is playing": "what's playing",
}


def control_room_patterns(
    control: Iterable[re.Pattern[str]],
) -> tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str]]:
    """(the room-control fast path, the control head alone, the room-first
    fast path) from MusicHandler's own anchored closed patterns ("pause",
    "turn it up", "what's playing", "shuffle" …) plus
    :data:`CONTROL_ALIASES`: "<control> in R", and "in R, <command>"."""
    bodies = [p.pattern[1:-1] for p in control]  # each is ^(...)$
    bodies += [re.escape(a) for a in CONTROL_ALIASES]
    head = r"(?:" + "|".join(f"(?:{b})" for b in bodies) + r")"
    first = (
        r"^" + _PREP + r" (?P<place>.+?),? "
        r"(?P<cmd>(?:play|put on|put|shuffle) .+|" + head + r")$"
    )
    return (
        re.compile(r"^" + head + ROOM_TAIL + r"$"),
        re.compile(r"^" + head + r"$"),
        re.compile(first),
    )


# ─── Groups' fast paths (shared queues, domovoi/music_groups.py) ─────────

#: What "play <these> everywhere" means when it is the music playing now.
THIS_WORDS = frozenset({
    "this", "it", "this song", "this music", "the music", "what's playing",
    "what is playing", "this one", "that", "the same", "the same music",
})
_THIS = r"(?:this(?: song| music| one)?|it|the (?:same )?music|what(?:'s| is) playing|that|the same)"

#: "share this with the garage", "play this in the office too", "play it
#: upstairs as well".
GROUP_SHARE_RE = re.compile(
    r"^(?:share " + _THIS + r" with (?P<with>.+)"
    r"|(?:play|put) " + _THIS + r" (?:on )?(?P<too>.+?) (?:too|as well))$"
)
#: "join the kitchen", "join the music in the kitchen", "play the kitchen's
#: music in here", "listen to what's playing in the kitchen".
GROUP_JOIN_RE = re.compile(
    r"^(?:join (?:the music (?:in|from) )?(?P<j>.+?)(?:'s music| music)?"
    r"|(?:play|put on|listen to) (?P<p>.+?)'s music(?: in here| here| too)?"
    r"|(?:play|put on|listen to) (?:the music|what(?:'s| is) playing) (?:in|from) (?P<w>.+?)"
    r"(?: in here| here| too)?)$"
)
#: "leave the group", "stop sharing", "just in here".
GROUP_LEAVE_RE = re.compile(
    r"^(?:leave (?:the |this )?(?:group|shared (?:queue|music))"
    r"|stop sharing(?: (?:the music|this|it))?(?: with (?:the )?other rooms)?"
    r"|(?:play (?:it|this|the music) )?(?:just|only) (?:in )?(?:here|this room)"
    r"|keep (?:it|the music|this) (?:just |only )?(?:in )?here)$"
)
#: Stopping the music, as said before a place.
_STOP_HEAD = (
    r"(?:stop(?: it| the music| music| playing| the song)?"
    r"|turn (?:off the music|the music off)|shut off the music)"
)
_HERE_WORDS = r"(?:(?:right |in )?here|(?:in )?this room)"
#: A stop for this room alone: "stop here", "stop in here", "stop just
#: here", "stop the music here", "only stop this room", "stop here only".
#: In a group this room leaves it and the others play on; a bare "stop"
#: stops the whole group (owner, 2026-11-15). Every phrasing starts with
#: "stop" or "turn/shut off", so no song with "here" in its name ("play
#: wish you were here") is taken.
STOP_HERE_RE = re.compile(
    r"^(?:only |just )?" + _STOP_HEAD + r" (?:(?:just|only) )?" + _HERE_WORDS + r"(?: only)?$"
)
#: A stop for another room alone, said with "only" or "just": "stop the
#: music in the office only", "stop the music just in the office", "only
#: stop the music in the office". Without the word, a stop in a room of a
#: group stops the group (the room-control path).
STOP_ONLY_RE = re.compile(
    r"^(?:only " + _STOP_HEAD + r" (?:(?:just|only) )?(?P<prep1>" + _PREP + r") (?P<place1>.+?)(?: only)?"
    r"|" + _STOP_HEAD + r" (?:just|only) (?P<prep2>" + _PREP + r") (?P<place2>.+?)(?: only)?"
    r"|" + _STOP_HEAD + r" (?P<prep3>" + _PREP + r") (?P<place3>.+?) only)$"
)

#: "which rooms are playing this", "where is this playing".
GROUP_WHICH_RE = re.compile(
    r"^(?:(?:which|what) rooms? (?:are|is) (?:playing|hearing|listening to) " + _THIS + r"(?: too)?"
    r"|where (?:is|'s) " + _THIS + r" playing"
    r"|(?:is|are) (?:this|it|the music) (?:shared|playing anywhere else))$"
)

#: A command that ends in a named group's bare name: see GroupPlacePattern.
_BARE_GROUP_SRC = r"^(?P<head>.+) (?P<place>[a-z0-9' ]+)$"


class GroupPlacePattern:
    """A fast-path pattern (``match`` and ``pattern`` like ``re.Pattern``,
    as documents.py's ``_ClaimingPattern``) that matches a command ending
    in a named group's name with no preposition before it ("play jazz
    upstairs", "stop the music downstairs") only when the house has a
    group of that name. With no groups it matches nothing, so it takes no
    command from anyone; "play the jazz station" is never a group's (a
    group's name can't hold "station": music_groups.check_name)."""

    def __init__(self) -> None:
        self.pattern = _BARE_GROUP_SRC
        self._re = re.compile(_BARE_GROUP_SRC)
        self.flags = self._re.flags
        self.groupindex = self._re.groupindex

    def match(self, text_: str, *args):
        from domovoi import music_groups

        if self._re.match(text_, *args) is None:
            return None
        words = text_.split()
        for phrase in music_groups.group_phrases():
            n = len(phrase)
            if len(words) > n and tuple(words[-n:]) == phrase:
                head = " ".join(words[:-n])
                if not _music_head(head):
                    # "do i have anything for the kids" with a group "the
                    # kids" is the library's, as it was: only a play or a
                    # music control is taken here, so no later band's
                    # sentence is poached (the router takes the first
                    # match and would never reach them).
                    return None
                return re.match(
                    r"^(?P<head>" + re.escape(head) + r") (?P<place>"
                    + re.escape(" ".join(phrase)) + r")$",
                    text_,
                )
        return None

    def search(self, text_: str, *args):
        return self._re.search(text_, *args)

    def __repr__(self) -> str:
        return f"GroupPlacePattern({self.pattern!r})"


#: A play's head, as the room paths read it: "play jazz", "put on jazz",
#: "shuffle jazz".
_PLAY_HEAD_RE = re.compile(r"^(?:play|put on|put|shuffle) \S.*$")


def _music_head(head: str) -> bool:
    """Whether ``head`` — the words before a group's bare name — is a play
    or one of MusicHandler's closed controls ("stop the music", "pause",
    "turn it up", "what's playing")."""
    if _PLAY_HEAD_RE.match(head):
        return True
    from domovoi.handlers import music as music_mod

    return bool(music_mod._CONTROL_HEAD_RE.match(CONTROL_ALIASES.get(head, head)))


GROUP_PLACE_RE = GroupPlacePattern()

#: Places a reply says bare ("Playing jazz upstairs."); any other group's
#: is said "in the north wing".
_BARE_REPLY_PLACES = frozenset({
    "upstairs", "downstairs", "outside", "inside", "outdoors", "indoors",
})


#: What "play <these> in the garage" asks for: any song of the library's.
RANDOM_WORDS = frozenset({
    "music", "some music", "any music", "random music", "some random music",
    "songs", "some songs", "a song", "any song", "a random song", "random songs",
    "some random songs", "tunes", "some tunes", "a tune", "something", "anything",
    "something random", "anything random", "random", "a few songs",
    "some tracks", "a track", "a random track",
})

# Verbs and how many words they take.
_PLAY_VERBS = (("put", "on"), ("play",), ("put",), ("shuffle",))


@dataclass(frozen=True)
class _Ask:
    """A parsed room command: ``family`` "play" or "control"; ``head`` the
    command without its place ("play weezer", "stop the music"); ``obj``
    the play's object without its place ("weezer"), "" for a control;
    ``whole`` the play's whole object with its place ("weezer in the
    garage"); ``verb`` as said; ``place``."""

    family: str
    head: str
    obj: str
    whole: str
    verb: str
    place: Place


def _play_parts(words: list[str]) -> tuple[str, int] | None:
    for verb in _PLAY_VERBS:
        if tuple(words[: len(verb)]) == verb:
            return " ".join(verb), len(verb)
    return None



# MusicHandler's fast-path methods, by the command each is (see _action_of).
_ACTION_BY_METHOD = {
    "_play_random_from_match": "play",
    "_pause_from_match": "pause",
    "_resume_from_match": "resume",
    "_stop_from_match": "stop",
    "_next_from_match": "next",
    "_prev_from_match": "previous",
    "_now_playing_from_match": "now_playing",
    "_volume_set_from_match": "volume",
    "_volume_up_from_match": "volume",
    "_volume_down_from_match": "volume",
    # The queue's commands (music_queue_voice.py): reads a guest may make
    # ("now_playing"), edits never ("queue").
    "_whats_next_from_match": "now_playing",
    "_why_from_match": "now_playing",
    "_clear_queue_from_match": "queue",
    "_skip_artist_from_match": "queue",
    "_autoplay_from_match": "queue",
    "_more_like_from_match": "queue",
}
# Core handlers whose plays are the room's player and nothing else, so a
# play in another room may go through them: MusicHandler itself and
# playlists ("play my road trip playlist in the garage").
_ROOM_MEDIA_HANDLERS = frozenset({"music", "playlist"})

# The tool's actions, as the commands above name them.
_TOOL_ACTIONS = {
    "play": "play", "pause": "pause", "resume": "resume", "stop": "stop",
    "next": "next", "previous": "previous", "now_playing": "now_playing",
    "volume_set": "volume", "volume_up": "volume", "volume_down": "volume",
}


class MusicRoomsMixin(RoomCommandsMixin):
    """MusicHandler's commands for a named room (the shared checks and the
    delivery: :class:`~domovoi.handlers.room_commands.RoomCommandsMixin`).
    Relies on MusicHandler for ``name``, ``fast_paths``, ``_play_random``,
    ``_simple_ack``, ``execute_from_tool``, ``play_candidate`` and
    ``_choice_phrase``."""

    ROOM_PATH_METHODS = (
        "_room_play_from_match", "_room_control_from_match", "_room_first_from_match",
        "_room_group_from_match", "_group_share_from_match", "_group_join_from_match",
        "_group_leave_from_match", "_group_which_from_match",
        "_stop_here_from_match", "_stop_only_from_match",
    )

    # ── the fast paths ──
    async def _room_play_from_match(self, m, ctx, session):
        return await self._room_command(m.string, ctx, session, "play")

    async def _room_control_from_match(self, m, ctx, session):
        return await self._room_command(m.string, ctx, session, "control")

    async def _room_first_from_match(self, m, ctx, session):
        """ "In the garage, play some jazz": the place said first is never a
        song's words, so no song-or-room question. A place that isn't one
        ("in a minute, play …"): the router routes the turn on, as before
        this path existed."""
        cmd, place = m.group("cmd"), m.group("place")
        rooms = await rooms_mod.household_rooms(session, ctx.app)
        if rooms_mod.read_place(f"in {place}", rooms) is None:
            return None
        family = "play" if _play_parts(cmd.split()) is not None else "control"
        return await self._room_command(
            f"{cmd} in {place}", ctx, session, family, name_checked=True,
        )

    async def _room_group_from_match(self, m, ctx, session):
        """A command ending in a named group's bare name: "play jazz
        upstairs", "pause the music downstairs"."""
        family = "play" if _play_parts(m.group("head").split()) is not None else "control"
        return await self._room_command(m.string, ctx, session, family)

    async def _group_share_from_match(self, m, ctx, session):
        said = (m.group("with") or m.group("too") or "").strip()
        rooms = await rooms_mod.household_rooms(session, ctx.app)
        first = said.split()[0] if said.split() else ""
        place = rooms_mod.read_place(
            said if first in rooms_mod.PLACE_PREPOSITIONS or said == "everywhere" else f"in {said}",
            rooms,
        )
        if place is None:
            return await self._as_without_place(m.string, ctx, session)
        if place.kind == "unknown":
            return self._room_reply(ctx, unknown_room_text(place.said, rooms))
        if place.kind == "here":
            return self._room_reply(ctx, "That's already playing here.")
        return await self._share_here(place, rooms, ctx)

    async def _group_join_from_match(self, m, ctx, session):
        said = (m.group("j") or m.group("p") or m.group("w") or "").strip()
        rooms = await rooms_mod.household_rooms(session, ctx.app)
        place = rooms_mod.read_place(f"in {said}", rooms)
        if place is None or place.kind not in ("rooms", "group"):
            # "play mozart's music": not a room — the command as before.
            return await self._as_without_place(m.string, ctx, session)
        return await self._join(place, ctx, session)

    async def _group_leave_from_match(self, m, ctx, session):
        return await self._leave_or_keep(m.string, ctx)

    async def _group_which_from_match(self, m, ctx, session):
        return await self._which_rooms(ctx, session)

    async def _stop_here_from_match(self, m, ctx, session):
        return await self._stop_here(ctx)  # type: ignore[attr-defined]

    async def _stop_only_from_match(self, m, ctx, session):
        """ "Stop the music in the office only": that room alone stops."""
        n = next(i for i in (1, 2, 3) if m.group(f"place{i}") is not None)
        prep, place = m.group(f"prep{n}"), m.group(f"place{n}")
        return await self._room_command(
            f"stop the music {prep} {place}", ctx, session, "control", only=True,
        )

    def _action_of(self, text_: str) -> str | None:
        hit = self._own_path(CONTROL_ALIASES.get(text_, text_))
        if hit is None:
            return None
        return _ACTION_BY_METHOD.get(getattr(hit[0].method, "__name__", ""))

    # ── reading the request ──
    def _parse(self, transcript: str, family: str, rooms: list[Room]) -> tuple[_Ask | None, _Ask | None]:
        """(the split whose place is a place the house has — or here, or
        everywhere —, the first split whose place only sounds like a room).
        Either may be None."""
        from domovoi.handlers import music as music_mod

        words = transcript.split()
        found: _Ask | None = None
        unknown: _Ask | None = None
        if family == "play":
            parts = _play_parts(words)
            if parts is None:
                return None, None
            verb, n = parts
            splits = rooms_mod.place_splits(words, start=n + 1)
        else:
            verb, n = "", 0
            splits = rooms_mod.place_splits(words, start=1)
        for i, place_words in splits:
            head_words = words[:i]
            if family == "play":
                obj_words = head_words[n:]
                if verb == "put" and obj_words and obj_words[-1] == "on":
                    obj_words = obj_words[:-1]  # "put weezer on in the office"
                if verb == "play" and len(obj_words) > 1 and obj_words[0] == "me":
                    obj_words = obj_words[1:]  # "play me something in the den"
                if not obj_words:
                    continue
                obj = " ".join(obj_words)
                head = f"{verb} {obj}"
                whole = " ".join(obj_words + words[i:])
            else:
                head = " ".join(head_words)
                if not music_mod._CONTROL_HEAD_RE.match(head):
                    continue
                obj, whole = "", ""
            place = rooms_mod.read_place(place_words, rooms)
            if place is None:
                continue
            ask = _Ask(family=family, head=head, obj=obj, whole=whole, verb=verb, place=place)
            if place.kind == "unknown":
                unknown = unknown or ask
                continue
            found = ask
            break
        return found, unknown

    async def _whole_is_a_name(self, ask: _Ask, session: AsyncSession):
        """Song or room (see the module docstring, step 2): None — the
        room reading; ("title", candidate) — the whole object is a name in
        the library and the words before the place are not; ("ask",
        candidate) — both are names."""
        from domovoi.handlers.shared import library_match

        if ask.family != "play" or not ask.whole:
            return None
        whole = await library_match.resolve_request(session, {"any": ask.whole})
        if whole.decision != "play" or whole.best is None:
            return None
        if ask.obj in RANDOM_WORDS:
            return ("ask", whole.best)
        part = await library_match.resolve_request(session, {"any": ask.obj})
        if part.decision == "play" and part.best is not None:
            return ("ask", whole.best)
        return ("title", whole.best)

    # ── the command ──
    async def _room_command(
        self,
        transcript: str,
        ctx: Context,
        session: AsyncSession,
        family: str,
        *,
        name_checked: bool = False,
        only: bool = False,
    ) -> Response | None:
        """``only``: a stop for the named room alone ("stop the music in the
        office only"), never its whole group."""
        rooms = await rooms_mod.household_rooms(session, ctx.app)
        ask, unknown = self._parse(transcript, family, rooms)
        if ask is None and unknown is None:
            return await self._as_without_place(transcript, ctx, session)
        if not name_checked:
            reading = await self._whole_is_a_name(ask or unknown, session)  # type: ignore[arg-type]
            if reading is not None:
                kind, cand = reading
                if kind == "ask" and ask is not None and ctx.session_id is not None and ctx.answerable:
                    return await self._ask_room_or_title(ask, cand, transcript, ctx, session)
                log.info("music: %r is the name %r, not a room", said(transcript), cand.ref.label)
                return await self._as_without_place(transcript, ctx, session)
        if ask is None:
            assert unknown is not None
            if unknown.verb not in ("", "play") and not await self._worth_a_room(unknown, session):
                return await self._as_without_place(transcript, ctx, session)
            return self._room_reply(ctx, unknown_room_text(unknown.place.said, rooms))
        if family == "play" and ask.verb != "play" and not await self._worth_a_room(ask, session):
            # "put the kettle on in the kitchen": not a name in the library,
            # so not a request for music — the router routes it on.
            return await self._as_without_place(transcript, ctx, session)
        action = "play" if family == "play" else (self._action_of(ask.head) or "")
        place = ask.place
        if place.kind == "everywhere":
            return await self._everywhere(ask, action, rooms, ctx, session)
        if place.kind == "group":
            return await self._in_group(ask, action, place, ctx, session)
        if place.kind == "here" or any(r.room_id == ctx.room_id for r in place.rooms):
            if action == "stop":
                # "Stop the music in here": a stop qualified by this room
                # is for this room alone, never its group (2026-11-15).
                return await self._stop_here(ctx)  # type: ignore[attr-defined]
            return await self._here(ask, transcript, ctx, session)
        room = self._pick_room(place.rooms)
        return await self._in_room(ask, action, room, ctx, session, only=only)

    async def _worth_a_room(self, ask: _Ask, session: AsyncSession) -> bool:
        """Whether a play's object is music for sure: words that mean any
        song, a playlist ("my road trip playlist"), or a name the library
        knows (a sure match or a "did you mean")."""
        from domovoi.handlers.shared import library_match

        if ask.obj in RANDOM_WORDS:
            return True
        hit = self._media_path(f"play {ask.obj}")
        if hit is not None and hit[0].name != "music":
            return True
        if ask.verb == "shuffle" and self._media_path(f"shuffle {ask.obj}") is not None:
            return True
        res = await library_match.resolve_request(session, {"any": ask.obj})
        return res.decision in ("play", "ask")

    def _media_path(self, text_: str):
        """The first fast path for ``text_`` among the handlers a play in
        another room may go through (``_ROOM_MEDIA_HANDLERS``), with
        MusicHandler's room paths left out — or None. A path of any other
        handler that would take it first is also None: that command isn't
        a play in a room's player."""
        from domovoi.router import first_fast_path

        hit = first_fast_path(text_)
        if hit is None:
            return None
        handler, fp, m = hit
        if handler is self or handler.name == self.name:  # type: ignore[attr-defined]
            own = self._own_path(text_)
            return (self, own[0], own[1]) if own is not None else None
        if handler.name not in _ROOM_MEDIA_HANDLERS:
            return None
        return handler, fp, m

    async def _here(self, ask: _Ask, transcript: str, ctx: Context, session: AsyncSession):
        """ "... in here", or the room the person is in by name: the command
        without its place, here."""
        if ask.family == "play":
            if ask.obj in RANDOM_WORDS:
                return await self._play_random(ctx, session)  # type: ignore[attr-defined]
            from domovoi.router import first_fast_path, run_fast_path

            head = ask.head if ask.verb in ("play", "shuffle") else f"play {ask.obj}"
            hit = first_fast_path(head)
            if hit is not None and hit[0].name == self.name:  # type: ignore[attr-defined]
                own = self._own_path(head)
                hit = (self, own[0], own[1]) if own is not None else None
            if hit is not None:
                response = await run_fast_path(hit[0], hit[1], hit[2], ctx, session)
                if response is not None:
                    return response
            return await self._as_without_place(transcript, ctx, session)
        head = CONTROL_ALIASES.get(ask.head, ask.head)
        hit = self._own_path(head)
        if hit is None:
            return await self._as_without_place(transcript, ctx, session)
        return await hit[0].method(self, hit[1], ctx, session)

    # ── another room ──
    async def _in_room(
        self, ask: _Ask, action: str, room: Room, ctx: Context, session: AsyncSession,
        *, only: bool = False,
    ) -> Response | None:
        target = await self._target(room, action, ctx)
        if isinstance(target, Response):
            return target
        log.info(
            "music: %s in room=%s for room=%s (%r)", action, room.room_id, ctx.room_id, ask.head,
        )
        response: Response | None
        if ask.family == "play":
            if ask.obj in RANDOM_WORDS:
                response = await self._play_random(target, session)  # type: ignore[attr-defined]
            else:
                response = await self._play_there(ask, target, session)
        elif only and action == "stop":
            # That room alone: it leaves its group, the others play on.
            response = await self._stop_here(target)  # type: ignore[attr-defined]
        else:
            hit = self._own_path(CONTROL_ALIASES.get(ask.head, ask.head))
            if hit is None:  # the head matched _CONTROL_HEAD_RE, so it has one
                return None
            response = await hit[0].method(self, hit[1], target, session)
        return self._finish_in_room(response, room, ctx)

    async def _play_there(self, ask: _Ask, target: Context, session: AsyncSession) -> Response | None:
        from domovoi.router import run_fast_path

        heads = [ask.head] if ask.verb in ("play", "shuffle") else []
        heads.append(f"play {ask.obj}")
        for head in heads:
            hit = self._media_path(head)
            if hit is None:
                continue
            handler, fp, m = hit
            if handler is self:
                return await fp.method(self, m, target, session)
            # Another room's player through another handler (a playlist):
            # it may not ask a question here — its answer would play in the
            # room that asked (session_id is the asking room's; see below).
            response = await run_fast_path(
                handler, fp, m, target.model_copy(update={"session_id": None}), session,
            )
            if response is not None and response.expect_followup:
                response.expect_followup = False
                response.text = (
                    f"{response.text.rstrip()} Ask again with its full name and the room."
                )
            return response
        return self._room_reply(target, "That isn't something I can play in another room yet.")

    async def _everywhere(
        self, ask: _Ask, action: str, rooms: list[Room], ctx: Context, session: AsyncSession,
        *, place: Place | None = None, run_play: Any = None,
    ) -> Response:
        """Every room (or, with ``place``, a named group's rooms): a play
        here — or in one of them — that every connected one hears (a
        shared queue, domovoi/music_groups.py); a stop or a pause in each
        of them that is playing."""
        from domovoi import guest_queue, music_groups
        from domovoi.dropin_common import room_may_reach_other_rooms
        from domovoi.room_music import deliver_later

        where = self._place_phrase(place)
        if action == "play":
            return await self._play_shared(ask, rooms, place, ctx, session, run_play=run_play)
        if action not in ("stop", "pause"):
            return self._room_reply(ctx, EVERYWHERE_OTHER_TEXT)
        if ctx.app is None:
            return self._room_reply(ctx, NO_APP_TEXT)
        if action == "stop" and not room_may_reach_other_rooms(ctx.app, ctx.room_id):
            return self._room_reply(ctx, UNPAIRED_TEXT)
        # A room hearing a shared queue plays from its host's player: a stop
        # ends the shared queue first, so each room is stopped once and no
        # queue is handed from room to room on the way out.
        listening: set[str] = set()
        if action == "stop":
            ids = {r.room_id for r in rooms}
            for sh in music_groups.shares():
                if set(sh.rooms) <= ids:
                    listening |= set(sh.members)
                    await music_groups.end(ctx.app, sh.id, silence_members=False)
                else:
                    for member in [m for m in sh.members if m in ids]:
                        listening.add(member)
                        await music_groups.leave(ctx.app, member)
        done: list[Room] = []
        own_action = None
        paused_hosts: set[str] = set()
        for room in rooms:
            if room.room_id in listening:
                done.append(room)
                if room.room_id == ctx.room_id:
                    own_action = "stop"
                else:
                    deliver_later(ctx.app, room.room_id, music_action="stop")
                continue
            if not room.has_player or not (await guest_queue.room_activity(room.room_id)).active:
                continue
            if action == "pause":
                host = music_groups.host_of(room.room_id) or room.room_id
                if host in paused_hosts:
                    done.append(room)  # its group's player is paused already
                    continue
                paused_hosts.add(host)
            there = ctx.model_copy(update={
                "room_id": room.room_id, "device_kind": None, "device_player": {},
                "session_id": None,
                "asked_from_room": None if room.room_id == ctx.room_id else ctx.room_id,
            })
            response = await self._simple_ack(action, there)  # type: ignore[attr-defined]
            if response.failure:
                continue
            done.append(room)
            if room.room_id == ctx.room_id:
                own_action = response.music_action
            else:
                deliver_later(ctx.app, room.room_id, music_action=response.music_action)
        if not done:
            text_ = (
                "Nothing is playing anywhere right now." if place is None
                else f"Nothing is playing {where} right now."
            )
            reply = self._room_reply(ctx, text_)
        else:
            verb = "Stopped" if action == "stop" else "Paused"
            if place is None:
                text_ = f"{verb} the music in {rooms_mod.say_rooms(done, limit=99)}."
            else:
                text_ = f"{verb} the music {where}."
            reply = self._room_reply(ctx, text_)
        reply.music_action = own_action
        reply.data = {"rooms": [r.room_id for r in done]}
        return reply

    # ── groups: rooms sharing one queue (domovoi/music_groups.py) ──
    @staticmethod
    def _place_phrase(place: Place | None) -> str:
        """How a reply says the place: "everywhere", "upstairs", "in the
        north wing"."""
        if place is None or place.kind == "everywhere":
            return "everywhere"
        said = " ".join(place.said.split())
        if rooms_mod.room_key(said) in {rooms_mod.room_key(w) for w in _BARE_REPLY_PLACES}:
            return said
        return f"in {said}"

    @staticmethod
    def _said_at(text_: str, phrase: str) -> str:
        """A reply the play gave here, said for the place ("Playing jazz."
        becomes "Playing jazz upstairs.")."""
        marker = "\x00"
        t = say_in_room(text_, marker)
        return t.replace(f"in {marker}", phrase).replace(marker, phrase)

    async def _in_group(
        self, ask: _Ask, action: str, place: Place, ctx: Context, session: AsyncSession,
        *, run_play: Any = None,
    ) -> Response | None:
        """A command for a named group ("play jazz upstairs", "stop the
        music in the north wing")."""
        rooms = list(place.rooms)
        if not rooms:
            name = place.group.name if place.group is not None else place.said
            return self._room_reply(ctx, f"None of the rooms in {name} are set up yet.")
        if action in ("play", "stop", "pause"):
            return await self._everywhere(
                ask, action, rooms, ctx, session, place=place, run_play=run_play,
            )
        # Another control: the group's music, through a room of it that plays.
        from domovoi import guest_queue

        for room in sorted(rooms, key=lambda r: (not r.online, r.room_id)):
            if room.has_player and (await guest_queue.room_activity(room.room_id)).active:
                if room.room_id == ctx.room_id:
                    return await self._here(ask, ask.head, ctx, session)
                return await self._in_room(ask, action, room, ctx, session)
        return self._room_reply(ctx, f"Nothing is playing {self._place_phrase(place)} right now.")

    def _shared_allowed(self, ctx: Context) -> Response | None:
        """Bringing music into other rooms is for a paired satellite or the
        household's app (CORE-11)."""
        from domovoi.dropin_common import room_may_reach_other_rooms

        if ctx.app is None:
            return self._room_reply(ctx, NO_APP_TEXT)
        if not room_may_reach_other_rooms(ctx.app, ctx.room_id):
            return self._room_reply(ctx, UNPAIRED_TEXT)
        return None

    async def _play_shared(
        self, ask: _Ask, rooms: list[Room], place: Place | None, ctx: Context,
        session: AsyncSession, *, run_play: Any = None,
    ) -> Response:
        """Play in several rooms at once: here when this room is one of
        them, else in one of them; every other connected one hears it."""
        from domovoi import music_groups

        refused = self._shared_allowed(ctx)
        if refused is not None:
            return refused
        phrase = self._place_phrase(place)
        if run_play is None and ask.obj in THIS_WORDS:
            return await self._share_here(place or Place("everywhere", "everywhere"), rooms, ctx)
        # Rooms that can be in a shared queue: connected, with a music player
        # of their own (one adopted but never connected has none).
        online = [r for r in rooms if r.online and r.has_player]
        here = next((r for r in rooms if r.room_id == ctx.room_id), None)
        host_room = here or (self._pick_room(online) if online else None)
        if host_room is None:
            return self._room_reply(ctx, f"None of the speakers {phrase} are online right now.")
        response: Response | None
        started = False
        if host_room.room_id == ctx.room_id:
            if run_play is not None:
                response = await run_play(ctx)
            elif ask.obj in RANDOM_WORDS:
                response = await self._play_random(ctx, session)  # type: ignore[attr-defined]
            else:
                response = await self._here(ask, ask.head, ctx, session)
            started = response is not None and response.music_action == "start"
        else:
            target = await self._target(host_room, "play", ctx)
            if isinstance(target, Response):
                return target
            if run_play is not None:
                response = await run_play(target)
            elif ask.obj in RANDOM_WORDS:
                response = await self._play_random(target, session)  # type: ignore[attr-defined]
            else:
                response = await self._play_there(ask, target, session)
            started = response is not None and response.music_action == "start"
            response = self._finish_in_room(response, host_room, ctx)
        if response is None:
            return self._room_reply(ctx, "I couldn't play that.")
        if response.failure or not started:
            return response
        # The play went to the player this room hears: its own, or — when it
        # is a member of a shared queue — its host's (the whole group's
        # music changed, owner answer 1). That player is the one the other
        # rooms join; nothing left a group before the play worked.
        player_room = music_groups.host_of(host_room.room_id) or host_room.room_id
        others = [r.room_id for r in online if r.room_id != player_room]
        if others:
            await music_groups.share(
                ctx.app, player_room, others,
                group_id=place.group.id if place is not None and place.group is not None else None,
                everywhere=place is None or place.kind == "everywhere",
            )
        if host_room.room_id == ctx.room_id:
            response.text = self._said_at(response.text, phrase)
        else:
            # _finish_in_room said it "in the garage": say the place instead.
            response.text = response.text.replace(f"in {host_room.spoken}", phrase)
        response.data = {**(response.data or {}), "group_rooms": [host_room.room_id, *others]}
        return response

    async def _share_here(self, place: Place, rooms: list[Room], ctx: Context) -> Response:
        """Other rooms hear what plays here ("play this everywhere", "share
        this with the garage")."""
        from domovoi import guest_queue, music_groups
        from domovoi.handlers.room_commands import offline_text

        refused = self._shared_allowed(ctx)
        if refused is not None:
            return refused
        host = music_groups.host_of(ctx.room_id) or ctx.room_id
        if not host or not (await guest_queue.room_activity(ctx.room_id)).active:
            return self._room_reply(ctx, "Nothing is playing here to share.")
        wanted = rooms if place.kind == "everywhere" else list(place.rooms)
        others = [r for r in wanted if r.online and r.has_player
                  and r.room_id != host and r.room_id != ctx.room_id]
        if place.kind == "rooms" and others:
            others = [self._pick_room(others)]
        if not others:
            if place.kind == "rooms" and place.rooms:
                room = self._pick_room(place.rooms)
                if room.room_id in (host, ctx.room_id):
                    return self._room_reply(ctx, "That's already playing there.")
                return self._room_reply(ctx, offline_text(room, "play music there"))
            return self._room_reply(ctx, "No other room is connected right now.")
        await music_groups.share(
            ctx.app, host, [r.room_id for r in others],
            group_id=place.group.id if place.group is not None else None,
            everywhere=place.kind == "everywhere",
        )
        where = f"in {others[0].spoken}" if place.kind == "rooms" else self._place_phrase(place)
        return self._room_reply(ctx, f"Playing this {where} too.")

    async def _join(self, place: Place, ctx: Context, session: AsyncSession) -> Response:
        """This room hears another room's music ("join the kitchen")."""
        from domovoi import guest_queue, music_groups

        refused = self._shared_allowed(ctx)
        if refused is not None:
            return refused
        if ctx.from_phone or not ctx.room_id:
            return self._room_reply(ctx, "Only a room's speaker can join another room's music.")
        if not music_groups.has_own_player(ctx.room_id):
            return self._room_reply(
                ctx, "This room's music player isn't running yet, so it can't join another room.",
            )
        candidates = [r for r in place.rooms if r.room_id != ctx.room_id]
        if not candidates:
            return self._room_reply(ctx, "That's this room.")
        source = None
        for room in sorted(candidates, key=lambda r: (not r.online, r.room_id)):
            if room.has_player and (await guest_queue.room_activity(room.room_id)).active:
                source = room
                break
        if source is None:
            where = (
                self._place_phrase(place) if place.kind == "group"
                else f"in {self._pick_room(candidates).spoken}"
            )
            return self._room_reply(ctx, f"Nothing is playing {where} right now.")
        host = music_groups.host_of(source.room_id) or source.room_id
        if host == (music_groups.host_of(ctx.room_id) or ctx.room_id):
            # Already in: send the stream to this room's speaker again
            # rather than refuse, so a satellite that fell silent while the
            # server still counts it in (after a core restart, 2026-11-16)
            # has a way back.
            music_groups.resync(ctx.app, ctx.room_id)
            return self._room_reply(ctx, f"Playing the music from {source.spoken} here.")
        await music_groups.share(ctx.app, host, [ctx.room_id])
        return self._room_reply(ctx, f"Playing the music from {source.spoken} here too.")

    async def _leave_or_keep(self, transcript: str, ctx: Context) -> Response:
        """ "Leave the group": this room stops, the others play on. "Just in
        here" / "stop sharing": this room keeps the music, the others stop."""
        from domovoi import music_groups

        if music_groups.share_of(ctx.room_id) is None:
            return self._room_reply(ctx, "This room isn't sharing music with any other room.")
        if transcript.startswith("leave"):
            response = await self._leave_group(ctx)  # type: ignore[attr-defined]
            return response or self._room_reply(ctx, "OK.")
        refused = self._shared_allowed(ctx)
        if refused is not None:
            return refused
        await music_groups.keep_only(ctx.app, ctx.room_id)
        return self._room_reply(ctx, "Just in here now. The other rooms have stopped.")

    async def _which_rooms(self, ctx: Context, session: AsyncSession) -> Response:
        """ "Which rooms are playing this?" """
        from domovoi import guest_queue, music_groups

        sh = music_groups.share_of(ctx.room_id)
        if sh is None:
            if ctx.room_id and (await guest_queue.room_activity(ctx.room_id)).active:
                return self._room_reply(ctx, "Just this room.")
            return self._room_reply(ctx, "Nothing is playing here right now.")
        house = {r.room_id: r for r in await rooms_mod.household_rooms(session, ctx.app)}
        others = [house.get(r) or Room(room_id=r) for r in sh.rooms if r != ctx.room_id]
        names = sorted(
            {r.spoken for r in others},
            key=lambda n: (n[4:] if n.lower().startswith("the ") else n).lower(),
        )
        names.append("here")
        said = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        return self._room_reply(ctx, f"{said[:1].upper()}{said[1:]}.")

    # ── song or room: the question ──
    async def _ask_room_or_title(
        self, ask: _Ask, cand: Any, transcript: str, ctx: Context, session: AsyncSession,
    ) -> Response:
        from domovoi.confirmations import request_confirmation

        phrase = await self._choice_phrase(cand, session)  # type: ignore[attr-defined]
        where = "in here" if ask.place.kind == "here" else f"in {ask.place.said}"
        if ask.place.kind == "rooms":
            where = f"in {self._pick_room(ask.place.rooms).spoken}"
        prompt = f"Did you mean {phrase}? Say no to play {ask.obj} {where} instead."
        try:
            await request_confirmation(
                session, ctx.session_id, kind=ROOM_OR_TITLE_KIND,
                handler=self.name,  # type: ignore[attr-defined]
                data={
                    "candidate": cand.to_dict(),
                    "transcript": transcript,
                    "family": ask.family,
                    "expires_at": time.time() + ROOM_OR_TITLE_TTL_SEC,
                },
                prompt=prompt,
            )
        except Exception as e:  # noqa: BLE001 — the name alone is today's answer
            log.warning("music: couldn't park the song-or-room question: %s", e)
            return await self._as_without_place(transcript, ctx, session)
        log.info("music: asked %r for %r", prompt, said(transcript))
        return Response(
            text=prompt,
            session_id=ctx.session_id,
            matched_handler=self.name,  # type: ignore[attr-defined]
            expect_followup=True,
        )

    async def _answer_room_or_title(
        self, data: dict[str, Any], affirmative: bool, ctx: Context, session: AsyncSession,
    ) -> Response:
        from domovoi.handlers.shared import library_match
        from domovoi.handlers.shared.library_match import EntityRef

        try:
            expired = time.time() > float(data.get("expires_at"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            expired = True
        transcript = str(data.get("transcript") or "")
        if expired or not transcript:
            return self._room_reply(ctx, "I've lost track of that one — what would you like to hear?")
        if not affirmative:
            response = await self._room_command(
                transcript, ctx, session, str(data.get("family") or "play"), name_checked=True,
            )
            return response or self._room_reply(ctx, "OK.")
        cand_d = data.get("candidate") if isinstance(data.get("candidate"), dict) else {}
        ref = EntityRef.from_dict(cand_d.get("ref"))
        cand = await library_match.candidate_from_ref(session, ref) if ref is not None else None
        if cand is None:
            return self._room_reply(ctx, "That one isn't in your library anymore.")
        return await self.play_candidate(cand, ctx, session)  # type: ignore[attr-defined]

    # ── confirmations: the question above, and a "did you mean" asked for
    #    another room (its payload's play_room) ──
    async def handle_confirmation(self, kind, data, affirmative, ctx, session):
        if kind == ROOM_OR_TITLE_KIND:
            return await self._answer_room_or_title(data, affirmative, ctx, session)
        retarget = await self._retarget(data, ctx, session)
        if retarget is None:
            return await super().handle_confirmation(kind, data, affirmative, ctx, session)  # type: ignore[misc]
        if isinstance(retarget, Response):
            return retarget
        room, target = retarget
        response = await super().handle_confirmation(kind, data, affirmative, target, session)  # type: ignore[misc]
        return self._finish_in_room(response, room, ctx)

    async def handle_choice_reply(self, kind, data, transcript, ctx, session):
        retarget = await self._retarget(data, ctx, session)
        if retarget is None:
            return await super().handle_choice_reply(kind, data, transcript, ctx, session)  # type: ignore[misc]
        if isinstance(retarget, Response):
            return retarget
        room, target = retarget
        response = await super().handle_choice_reply(kind, data, transcript, target, session)  # type: ignore[misc]
        return self._finish_in_room(response, room, ctx)

    async def _retarget(self, data: Any, ctx: Context, session: AsyncSession):
        """For a "did you mean …?" asked about another room's play: (that
        room, the turn's context for it), the reply that says why it can't
        play there now, or None (an ordinary question: answered here)."""
        play_room = data.get("play_room") if isinstance(data, dict) else None
        if not play_room or play_room == ctx.room_id:
            return None
        rooms = await rooms_mod.household_rooms(session, ctx.app)
        room = next((r for r in rooms if r.room_id == play_room), None)
        if room is None:
            return self._room_reply(ctx, "That room isn't part of the house anymore.")
        target = await self._target(room, "play", ctx)
        if isinstance(target, Response):
            return target
        return room, target

    # ── the language model's tool ──
    async def room_tool_call(
        self, args: dict[str, Any], ctx: Context, session: AsyncSession,
    ) -> Response | None:
        """The ``music`` tool called with a ``room``: the same rules as the
        fast paths, the room as the model gave it."""
        said = str(args.get("room") or "").strip()
        rest = {k: v for k, v in args.items() if k != "room"}
        action = _TOOL_ACTIONS.get(str(args.get("action") or ""))
        if action is None:
            return await self.execute_from_tool(rest, ctx, session)  # type: ignore[attr-defined]
        rooms = await rooms_mod.household_rooms(session, ctx.app)
        place = rooms_mod.read_place(said, rooms) or Place("unknown", said)
        if place.kind == "unknown":
            return self._room_reply(ctx, unknown_room_text(said, rooms))
        if place.kind == "here" or any(r.room_id == ctx.room_id for r in place.rooms):
            return await self.execute_from_tool(rest, ctx, session)  # type: ignore[attr-defined]
        if place.kind in ("everywhere", "group"):
            ask = _Ask(family="control", head=action, obj="", whole="", verb="", place=place)

            async def _run(target: Context) -> Response | None:
                return await self.execute_from_tool(rest, target, session)  # type: ignore[attr-defined]

            if place.kind == "group":
                return await self._in_group(ask, action, place, ctx, session, run_play=_run)
            return await self._everywhere(ask, action, rooms, ctx, session, run_play=_run)
        room = self._pick_room(place.rooms)
        target = await self._target(room, action, ctx)
        if isinstance(target, Response):
            return target
        response = await self.execute_from_tool(rest, target, session)  # type: ignore[attr-defined]
        return self._finish_in_room(response, room, ctx)

__all__ = [
    "CONTROL_ALIASES",
    "GROUP_JOIN_RE",
    "GROUP_LEAVE_RE",
    "GROUP_PLACE_RE",
    "GROUP_SHARE_RE",
    "GROUP_WHICH_RE",
    "THIS_WORDS",
    "MusicRoomsMixin",
    "RANDOM_WORDS",
    "ROOM_OR_TITLE_KIND",
    "ROOM_PLAY_RE",
    "ROOM_TAIL",
    "STOP_HERE_RE",
    "STOP_ONLY_RE",
    "control_room_patterns",
    "say_in_room",
]
