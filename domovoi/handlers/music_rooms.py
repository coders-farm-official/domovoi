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

"Everywhere": a play is answered "I can only play music in one room at a
time"; a stop or a pause applies to every room that is playing.

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


_EVERYWHERE_PLAY_TEXT = (
    "I can only play music in one room at a time. Ask again with the room's name."
)

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
    ) -> Response | None:
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
        if place.kind == "here" or any(r.room_id == ctx.room_id for r in place.rooms):
            return await self._here(ask, transcript, ctx, session)
        room = self._pick_room(place.rooms)
        return await self._in_room(ask, action, room, ctx, session)

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
    ) -> Response:
        """A stop or a pause in every room that is playing; anything else is
        one room at a time."""
        from domovoi import guest_queue
        from domovoi.dropin_common import room_may_reach_other_rooms
        from domovoi.room_music import deliver_later

        if action == "play":
            return self._room_reply(ctx, _EVERYWHERE_PLAY_TEXT)
        if action not in ("stop", "pause"):
            return self._room_reply(ctx, EVERYWHERE_OTHER_TEXT)
        if ctx.app is None:
            return self._room_reply(ctx, NO_APP_TEXT)
        if action == "stop" and not room_may_reach_other_rooms(ctx.app, ctx.room_id):
            return self._room_reply(ctx, UNPAIRED_TEXT)
        done: list[Room] = []
        own_action = None
        for room in rooms:
            if not room.has_player or not (await guest_queue.room_activity(room.room_id)).active:
                continue
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
            reply = self._room_reply(ctx, "Nothing is playing anywhere right now.")
        else:
            verb = "Stopped" if action == "stop" else "Paused"
            reply = self._room_reply(
                ctx, f"{verb} the music in {rooms_mod.say_rooms(done, limit=99)}.",
            )
        reply.music_action = own_action
        reply.data = {"rooms": [r.room_id for r in done]}
        return reply

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
        if place.kind == "everywhere":
            ask = _Ask(family="control", head=action, obj="", whole="", verb="", place=place)
            return await self._everywhere(ask, action, rooms, ctx, session)
        room = self._pick_room(place.rooms)
        target = await self._target(room, action, ctx)
        if isinstance(target, Response):
            return target
        response = await self.execute_from_tool(rest, target, session)  # type: ignore[attr-defined]
        return self._finish_in_room(response, room, ctx)

__all__ = [
    "CONTROL_ALIASES",
    "MusicRoomsMixin",
    "RANDOM_WORDS",
    "ROOM_OR_TITLE_KIND",
    "ROOM_PLAY_RE",
    "ROOM_TAIL",
    "control_room_patterns",
    "say_in_room",
]
