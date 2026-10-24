"""What every "… in the garage" command shares: the room checks, doing it
there, and saying it here.

A media command may end with a place (``domovoi/handlers/shared/rooms.py``
reads it). Two handlers read such commands with their own fast paths, each
before its greedier ones: MusicHandler (``music_rooms.py``: songs, artists,
playlists, the transport controls, the volume) and SpokenAudioHandler
(``spoken_audio_rooms.py``: the latest episode of a show, an audiobook,
resuming either). :class:`RoomCommandsMixin` is the part they share:

* **May it, and can it?** (:meth:`RoomCommandsMixin._target`) A satellite
  that connected without a pairing token (lenient pairing) may only do what
  a guest of the house may do to a room that is actively playing — pause,
  resume, skip, go back, ask what's playing (``domovoi/guest_queue.py``);
  anything that starts, stops or changes the volume there is refused, as
  intercom and drop-in refuse it (CORE-11). Starting playback (and resume,
  skip, volume) needs the room's satellite connected. A play in a room whose
  music player isn't running yet starts it (``mpd_provisioner.ensure_room``,
  by the room's own id — one container per room, as the container guard
  expects, and only for a room the house already has). ``/v1/intent`` has no
  satellite to reach and says so.
* **Do it there** — on a copy of the turn's context for the target room
  (``Context.asked_from_room`` = the room that asked).
* **Say it here** (:meth:`RoomCommandsMixin._finish_in_room`): the target's
  ``music_start`` / ``music_stop`` / volume go to ITS satellite
  (``domovoi/room_music.py``), and the reply is said where it was asked
  (:func:`say_in_room`): "Playing Weezer in the garage, shuffled."
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.handlers.base import as_fast_path
from domovoi.handlers.shared import rooms as rooms_mod
from domovoi.handlers.shared.rooms import Room
from domovoi.models import Context, Response

log = logging.getLogger(__name__)

_PREP = r"(?:" + "|".join(sorted(rooms_mod.PLACE_PREPOSITIONS)) + r")"
#: A place at the end of a command (the splitting itself is done in code).
ROOM_TAIL = r"(?: " + _PREP + r" .+| everywhere| all over the house)"


def say_in_room(text: str, where: str) -> str:
    """A reply the target room's command gave, said in the room that asked
    ("Paused." → "Paused the music in the garage."). ``where`` as
    ``rooms.spoken_room`` says it ("the garage"). Replies it doesn't know
    are kept as they are (a "did you mean …?", a search that found
    nothing)."""
    t = (text or "").strip()
    place = f"in {where}"
    fixed = {
        "Paused.": f"Paused the music {place}.",
        "Resuming.": f"Resuming the music {place}.",
        "Stopped.": f"Stopped the music {place}.",
        "Next track.": f"Next track {place}.",
        "Previous track.": f"Previous track {place}.",
        "Nothing is playing right now.": f"Nothing is playing {place} right now.",
        "I couldn't reach the music player.": f"I couldn't reach the music player {place}.",
        "That was the last song in the queue.": f"That was the last song in the queue {place}.",
    }
    if t in fixed:
        return fixed[t]
    m = re.match(r"^This is (.+)\.$", t)
    if m:
        return f"{where[:1].upper()}{where[1:]} is playing {m.group(1)}."
    m = re.match(r"^(Volume (?:up|down) to \d+|Volume set to \d+)\.$", t)
    if m:
        return f"{m.group(1)} {place}."
    # A podcast or a book picked up where it was: "Playing X. Resuming at 5 minutes."
    m = re.match(r"^(Playing .+?)\.( Resuming at [^.]+\.)$", t)
    if m:
        return f"{m.group(1)} {place}.{m.group(2)}"
    if t.startswith(("Playing ", "Shuffling ", "Streaming ")):
        for tail in (", shuffled.", ", the song with those words."):
            if t.endswith(tail):
                return t[: -len(tail)] + f" {place}" + tail
        if t.endswith("."):
            return t[:-1] + f" {place}."
    if t.startswith("Next in ") and t.endswith("."):
        return t[:-1] + f", {place}."
    # "I don't have a podcast in progress for you here."
    if t.endswith(" here."):
        return t[: -len("here.")] + f"{place}."
    return t


def offline_text(room: Room, what: str) -> str:
    """The reply for a room whose satellite isn't connected; ``what`` says
    the thing that can't be done there ("play music there")."""
    where = room.spoken
    if not room.seen:
        return f"The device in {where} hasn't connected yet, so I can't {what}."
    return f"The speaker in {where} is offline right now, so I can't {what}."


def unknown_room_text(said: str, rooms: list[Room]) -> str:
    said = said.strip()
    if not rooms:
        return f"I don't have a room called {said} — no rooms have been set up yet."
    return f"I don't have a room called {said}. I have {rooms_mod.say_rooms(rooms)}."


UNPAIRED_TEXT = (
    "This speaker isn't paired with the house, so it can't change what's "
    "playing in other rooms."
)
NO_APP_TEXT = "I can only reach another room's speaker from a room or the app."
EVERYWHERE_OTHER_TEXT = "I can only do that in one room at a time."

# What a satellite without a pairing token may do in a room that is
# playing — a guest's controls (guest_queue.GUEST_TRANSPORT_ACTIONS), and
# the read.
GUEST_ACTIONS = frozenset({"pause", "resume", "next", "previous", "now_playing"})
# What needs the target's satellite connected (it would play to nobody).
NEEDS_ONLINE = frozenset({"play", "resume", "next", "previous", "volume"})


class RoomCommandsMixin:
    """The shared half of a handler's commands for a named room. The
    handler lists its room fast paths' method names in
    ``ROOM_PATH_METHODS``; relies on it for ``name`` and ``fast_paths``."""

    #: The handler's room fast paths, by method name: left out when the
    #: words turn out to name no place.
    ROOM_PATH_METHODS: tuple[str, ...] = ()
    #: How an offline room's reply ends: "so I can't <this>".
    OFFLINE_WHAT = "play music there"

    def _is_room_path(self, fp: Any) -> bool:
        return getattr(fp.method, "__name__", "") in self.ROOM_PATH_METHODS

    def _own_path(self, text_: str) -> tuple[Any, re.Match[str]] | None:
        """The handler's own fast path for ``text_``, its room paths left
        out."""
        for entry in self.fast_paths:  # type: ignore[attr-defined]
            fp = as_fast_path(entry)
            if self._is_room_path(fp):
                continue
            m = fp.pattern.match(text_)
            if m:
                return fp, m
        return None

    async def _as_without_place(self, transcript: str, ctx: Context, session: AsyncSession):
        """The turn as the handler took it before it knew rooms: its first
        other fast path, or None (the router routes on)."""
        hit = self._own_path(transcript)
        if hit is None:
            return None
        fp, m = hit
        return await fp.method(self, m, ctx, session)

    @staticmethod
    def _pick_room(candidates: Iterable[Room]) -> Room:
        """One room of several sharing a label: a connected one, with a
        music player, a voice satellite before a display — then by id."""
        return sorted(
            candidates,
            key=lambda r: (not r.online, not r.has_player, r.sat_type != "voice", r.room_id),
        )[0]

    async def _target(
        self, room: Room, action: str, ctx: Context, *, keep_session: bool | None = None,
    ) -> Context | Response:
        """The checks for ``action`` in ``room`` (module docstring), and the
        turn's context for it — or the reply that says why not.
        ``keep_session``: whether the copy keeps the asking conversation's
        session (default: for a play only — the asking conversation's last
        play and its "did you mean" follow a play)."""
        from domovoi.config import settings
        from domovoi.dropin_common import room_may_reach_other_rooms

        app = ctx.app
        if app is None:
            return self._room_reply(ctx, NO_APP_TEXT)
        if not room_may_reach_other_rooms(app, ctx.room_id):
            from domovoi import guest_queue

            allowed = (
                action in GUEST_ACTIONS
                and settings.guest_queue_editing
                and (await guest_queue.room_activity(room.room_id)).active
            )
            if not allowed:
                log.warning(
                    "%s: room=%s (no pairing token) refused %s in room=%s",
                    self.name, ctx.room_id, action, room.room_id,  # type: ignore[attr-defined]
                )
                return self._room_reply(ctx, UNPAIRED_TEXT)
        if action in NEEDS_ONLINE and not room.online:
            what = self.OFFLINE_WHAT if action == "play" else "do that there"
            return self._room_reply(ctx, offline_text(room, what))
        if action == "play":
            if not await self._make_player(room):
                return self._room_reply(
                    ctx,
                    f"I couldn't start the music player for {room.spoken}. "
                    "Check that Docker is running on the Domovoi server.",
                )
        elif not room.has_player and action != "volume":
            return self._room_reply(ctx, f"Nothing is playing in {room.spoken} right now.")
        if keep_session is None:
            keep_session = action == "play"
        volumes = getattr(app.state, "satellite_volume", None) or {}
        return ctx.model_copy(update={
            "room_id": room.room_id,
            "device_kind": None,
            "device_player": {},
            "satellite_volume": volumes.get(room.room_id),
            "asked_from_room": ctx.room_id,
            "session_id": ctx.session_id if keep_session else None,
        })

    async def _make_player(self, room: Room) -> bool:
        """The room's music player, started if it isn't yet: a room the
        house has, by its own id (one container per room, as the
        container guard expects)."""
        from domovoi.clients import mpd as mpd_module

        if room.has_player:
            return True
        try:
            from domovoi.mpd_provisioner import ensure_room

            await ensure_room(room.room_id)
        except Exception as e:  # noqa: BLE001 — said aloud by the caller
            log.warning("rooms: could not start the music player for room=%s: %s", room.room_id, e)
            return False
        log.info("rooms: started the music player for room=%s on request", room.room_id)
        return room.room_id in mpd_module._room_ports

    def _finish_in_room(self, response: Response | None, room: Room, ctx: Context) -> Response | None:
        """The target's reply, made the asking room's: its music frames and
        volume go to the target's satellite, its words are said here."""
        from domovoi.room_music import deliver_later

        if response is None:
            return None
        action, url, volume = (
            response.music_action, response.music_stream_url, response.satellite_volume,
        )
        response.music_action = None
        response.music_stream_url = None
        response.satellite_volume = None
        response.device_actions = []
        deliver_later(
            ctx.app, room.room_id, music_action=action, stream_url=url, volume_percent=volume,
        )
        response.text = say_in_room(response.text, room.spoken)
        response.data = {**(response.data or {}), "room_id": room.room_id, "room": room.spoken}
        return response

    def _room_reply(self, ctx: Context, text_: str) -> Response:
        return Response(
            text=text_,
            session_id=ctx.session_id,
            matched_handler=self.name,  # type: ignore[attr-defined]
        )


__all__ = [
    "EVERYWHERE_OTHER_TEXT",
    "GUEST_ACTIONS",
    "NEEDS_ONLINE",
    "NO_APP_TEXT",
    "ROOM_TAIL",
    "RoomCommandsMixin",
    "UNPAIRED_TEXT",
    "offline_text",
    "say_in_room",
    "unknown_room_text",
]
