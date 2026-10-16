"""Podcasts and audiobooks in another room: "play the latest episode of
The Daily in the garage", "play my audiobook in the office", "resume the
podcast in the kitchen".

SpokenAudioHandler sits at band 270, before radio, playlists and music,
and its "play the latest episode of (.+)" / "play the audiobook (.+)" /
"resume my book …" paths would take "… in the garage" as part of a show's
or a book's name, or ignore it, and play in the room that asked. Its first
fast path, :meth:`SpokenAudioRoomsMixin._room_spoken_from_match`, reads the
place first, with the same rules as music (``music_rooms.py``):

* the place is read by ``handlers/shared/rooms.py`` (ids, labels, slips,
  "in here", "everywhere", a room-like word the house doesn't have), the
  longest place first; words that name no place leave the turn to the
  handler's own paths, unchanged;
* a show or a book whose WHOLE name (place included) the household has —
  and the words without the place name nothing else — is that show or
  book, here;
* the shared checks and delivery (``room_commands.py``): the guest rule
  for a satellite without a pairing token (starting playback in another
  room is refused), the target's satellite must be connected, its music
  player is started if it isn't running, the confirmation is said in the
  asking room ("Playing <episode> in the garage.");
* it runs in the target room with the target's own resume positions for
  this person (positions are per room and person and never roam), and
  without the asking conversation's session: "what am I listening to",
  chapters and skips belong to the room that plays, asked there.

Only the commands that START playback take a room: the latest episode of a
show, an audiobook by name, resuming a book or a podcast ("play my
audiobook" is a resume). "Everywhere" is one room at a time.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.handlers.room_commands import (
    ROOM_TAIL,
    RoomCommandsMixin,
    unknown_room_text,
)
from domovoi.handlers.shared import rooms as rooms_mod
from domovoi.handlers.shared.rooms import Place
from domovoi.models import Context, Response

log = logging.getLogger(__name__)

# The handler's fast paths that start playback, by method name: the only
# ones a room may follow.
PLAY_METHODS = frozenset({
    "_play_latest_from_match", "_play_book_from_match", "_resume_from_match",
    "_play_mine_from_match",
})
# The ones that name a show or a book (the group(s) holding the name).
_NAME_GROUPS = {
    "_play_latest_from_match": ("show", "show2"),
    "_play_book_from_match": ("book",),
}
_EVERYWHERE_TEXT = "I can only play that in one room at a time. Ask again with the room's name."
_TOOL_PLAY_ACTIONS = frozenset({"resume", "play_latest", "play_book"})
_TOOL_OTHER_TEXT = (
    "I can start a podcast or an audiobook in another room; for the rest, ask "
    "the room that's playing it."
)


def _body(pattern: re.Pattern[str]) -> str:
    """An anchored pattern's body, its named groups made plain (several are
    joined into one alternation) and a trailing ``\\b.*`` dropped."""
    src = pattern.pattern
    src = src[1:] if src.startswith("^") else src
    for tail in (r"\b.*$", "$"):
        if src.endswith(tail):
            src = src[: -len(tail)]
            break
    return re.sub(r"\(\?P<\w+>", "(?:", src)


def spoken_room_pattern(*play_patterns: re.Pattern[str]) -> re.Pattern[str]:
    """SpokenAudioHandler's room path: one of its playback commands, then a
    place."""
    head = "|".join(f"(?:{_body(p)})" for p in play_patterns)
    return re.compile(r"^(?:" + head + r")" + ROOM_TAIL + r"$")


class SpokenAudioRoomsMixin(RoomCommandsMixin):
    """SpokenAudioHandler's commands for a named room. Relies on it for
    ``name``, ``fast_paths`` and ``execute_from_tool``."""

    ROOM_PATH_METHODS = ("_room_spoken_from_match",)
    OFFLINE_WHAT = "play that there"

    async def _room_spoken_from_match(self, m, ctx, session):
        transcript = m.string
        words = transcript.split()
        rooms = await rooms_mod.household_rooms(session, ctx.app)
        found = unknown = first = None
        for i, place_words in rooms_mod.place_splits(words, start=1):
            head = " ".join(words[:i])
            hit = self._own_path(head)
            if hit is None or getattr(hit[0].method, "__name__", "") not in PLAY_METHODS:
                continue
            first = first or hit
            place = rooms_mod.read_place(place_words, rooms)
            if place is None:
                continue
            if place.kind == "unknown":
                unknown = unknown or (head, hit, place)
                continue
            found = (head, hit, place)
            break
        if found is None and unknown is None:
            response = await self._as_without_place(transcript, ctx, session)
            if response is None and first is not None:
                # "play my book on repeat": no place, and no path takes the
                # whole — the command before those words, here.
                response = await first[0].method(self, first[1], ctx, session)
            return response
        if await self._whole_is_a_name(transcript, (found or unknown)[1], session):
            return await self._as_without_place(transcript, ctx, session)
        if found is None:
            return self._room_reply(ctx, unknown_room_text(unknown[2].said, rooms))
        head, (fp, hm), place = found
        if place.kind == "everywhere":
            return self._room_reply(ctx, _EVERYWHERE_TEXT)
        if place.kind == "here" or any(r.room_id == ctx.room_id for r in place.rooms):
            return await fp.method(self, hm, ctx, session)
        room = self._pick_room(place.rooms)
        target = await self._target(room, "play", ctx, keep_session=False)
        if isinstance(target, Response):
            return target
        log.info("spoken_audio: %r in room=%s for room=%s", head, room.room_id, ctx.room_id)
        response = await fp.method(self, hm, target, session)
        return self._finish_in_room(response, room, ctx)

    async def _whole_is_a_name(self, transcript: str, head_hit: Any, session: AsyncSession) -> bool:
        """A show or a book named by the whole words, place included ("the
        latest episode of Life in the Garage"), that the words before the
        place don't name: that show or book, here."""
        from domovoi import spoken_audio as sa

        fp, hm = head_hit
        groups = _NAME_GROUPS.get(getattr(fp.method, "__name__", ""))
        if not groups:
            return False
        whole_hit = self._own_path(transcript)
        if whole_hit is None or whole_hit[0].method is not fp.method:
            return False

        def name(match) -> str:
            return next((match.group(g) for g in groups if match.group(g)), "").strip().rstrip(".,!?")

        whole, part = name(whole_hit[1]), name(hm)
        if not whole or whole == part:
            return False
        find = sa.find_audiobook if "book" in groups else sa.subscription_for_show
        try:
            named = await find(session, whole)
            if named is None:
                return False
            # The lookups match by substring, so the words before the place
            # find the whole-named show too ("life" finds "Life in the
            # Garage"): the place is part of the name unless those words
            # name something else of their own.
            other = await find(session, part)
            return other is None or other.get("id") == named.get("id")
        except Exception as e:  # noqa: BLE001 — the room reading stands
            log.debug("spoken_audio: name check failed: %s", e)
            return False

    async def spoken_room_tool_call(
        self, args: dict[str, Any], ctx: Context, session: AsyncSession,
    ) -> Response | None:
        """The ``spoken_audio`` tool called with a ``room``."""
        said = str(args.get("room") or "").strip()
        rest = {k: v for k, v in args.items() if k != "room"}
        rooms = await rooms_mod.household_rooms(session, ctx.app)
        place = rooms_mod.read_place(said, rooms) or Place("unknown", said)
        if place.kind == "unknown":
            return self._room_reply(ctx, unknown_room_text(said, rooms))
        if place.kind == "here" or any(r.room_id == ctx.room_id for r in place.rooms):
            return await self.execute_from_tool(rest, ctx, session)  # type: ignore[attr-defined]
        if place.kind == "everywhere":
            return self._room_reply(ctx, _EVERYWHERE_TEXT)
        if str(args.get("action") or "") not in _TOOL_PLAY_ACTIONS:
            return self._room_reply(ctx, _TOOL_OTHER_TEXT)
        room = self._pick_room(place.rooms)
        target = await self._target(room, "play", ctx, keep_session=False)
        if isinstance(target, Response):
            return target
        response = await self.execute_from_tool(rest, target, session)  # type: ignore[attr-defined]
        return self._finish_in_room(response, room, ctx)


__all__ = ["PLAY_METHODS", "SpokenAudioRoomsMixin", "spoken_room_pattern"]
