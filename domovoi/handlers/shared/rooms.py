"""The household's rooms, and the room a spoken request names.

"Play jazz in the garage", "stop the music in the dining room", "what's
playing in Kamron's office": a music command may end with a PLACE. This
module answers two questions about it, without deciding anything about
music:

* :func:`household_rooms` — which rooms the house has: every room id a
  satellite row (adopted, labelled, or seen by a hello), an MPD row (its
  satellite connected and got a music player) or a pairing names, plus the
  rooms connected right now. Room-mode phones and laptop satellites are
  rooms like any other; the app's chat mic (``phone-…`` ids) is not.
* :func:`read_place` — what the words after "in"/"on"/... name: this room
  ("in here"), every room ("everywhere", "the whole house"), one of the
  house's rooms by its id or its label in Settings → Satellites, or a word
  that sounds like a room the house doesn't have ("in the attic").

Matching is by the room's id and label with the usual slips forgiven —
"the", case, spaces/underscores/hyphens, a plural or possessive "s"
("the kids room" for ``kid_s_room``, "the garages" for ``garage``), and
word order ("bathroom upstairs" for "Upstairs Bathroom") — and nothing
fuzzier: a near miss must never move music into the wrong room.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal

from sqlalchemy import text

from domovoi.models import is_phone_room

log = logging.getLogger(__name__)

#: Words that start a place: "in the garage", "on the patio", "at the
#: cabin", "throughout the house", "through the kitchen speaker".
PLACE_PREPOSITIONS = frozenset({
    "in", "on", "at", "inside", "into", "throughout", "through", "across", "around",
})

# Place phrases (as room_key gives them) meaning the room the person is in.
_HERE = frozenset({"here", "thisroom", "righthere", "outhere", "overhere", "inhere"})
# ... and every room.
_EVERYWHERE = frozenset({
    "everywhere", "house", "wholehouse", "entirehouse", "home", "wholehome",
    "everyroom", "allroom", "everyroominhouse", "allroominhouse", "alloverhouse",
    "everywhereinhouse",
})
# A place that needs no preposition before it: "play jazz everywhere",
# "play music all over the house". Not a bare "here": "wish you were here"
# is a song, and "play X here" asks for nothing "play X" doesn't — the
# room the person is in is said "in here".
_BARE_PLACES = (("everywhere",), ("all", "over", "the", "house"))

# What a room is usually called — for telling "I don't have a room called
# the attic" apart from a song that merely ends in a place ("the boys in
# the band", "live at carnegie hall"). Deliberately short: a word here
# makes "play X in the <word>" answer about rooms instead of searching for
# the whole phrase when the house has no such room and the library has no
# such name.
ROOM_WORDS = frozenset({
    "room", "kitchen", "garage", "office", "bedroom", "bathroom", "basement",
    "attic", "den", "study", "nursery", "hallway", "porch", "patio", "lounge",
    "pantry", "laundry", "closet", "loft", "cellar", "shed", "workshop", "gym",
    "playroom", "foyer", "entryway", "sunroom", "upstairs", "downstairs",
    "balcony", "terrace", "backyard", "yard", "mudroom",
})

_WORD_RE = re.compile(r"[a-z0-9]+")
_ARTICLES = frozenset({"the", "my", "our", "your", "a", "an"})
# Said after a room's name without changing which room it is.
_TRAILING_NOISE = frozenset({"speaker", "speakers", "satellite", "puck", "area", "please", "now"})


def _singular(word: str) -> str:
    """'garages' → 'garage', 'kids' → 'kid'; short words and '-ss', '-us',
    '-is' ("glass", "this") stay."""
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _key_words(phrase: str) -> list[str]:
    t = (phrase or "").lower().replace("’", "'")
    t = re.sub(r"'s\b", "s", t).replace("'", "")
    words = [w for w in _WORD_RE.findall(t.replace("_", " ").replace("-", " "))]
    words = [w for w in words if w not in _ARTICLES]
    while words and words[-1] in _TRAILING_NOISE:
        words.pop()
    return [_singular(w) for w in words]


def room_key(phrase: str) -> str:
    """How a room name or a spoken place compares: lower case, no
    articles, no punctuation or spaces, each word singular."""
    return "".join(_key_words(phrase))


def _order_free_key(phrase: str) -> str:
    return "".join(sorted(_key_words(phrase)))


def spoken_room(room_id: str, label: str | None = None) -> str:
    """How a reply says a room: "the garage", "the living room",
    "Kamron's office" — its label from Settings → Satellites when it has
    one, else its id with the underscores read as spaces."""
    name = " ".join((label or "").split()) or " ".join(
        room_id.replace("_", " ").replace("-", " ").split()
    )
    low = name.lower()
    if low.startswith(("the ", "my ", "our ")) or re.search(r"['’]s\b", low):
        return name
    if label and name == name.title():
        name = low  # "Living Room" reads "the living room" mid-sentence
    return f"the {name}"


@dataclass(frozen=True)
class Room:
    """One room of the house, as a request may name it."""

    room_id: str
    label: str | None = None
    sat_type: str = "voice"
    #: Its satellite is connected now.
    online: bool = False
    #: This process has a music player for it (a provisioned MPD).
    has_player: bool = False
    #: Its satellite has connected at least once (an MPD row or a
    #: pairing), as opposed to adopted in Settings and never seen.
    seen: bool = True

    @property
    def spoken(self) -> str:
        return spoken_room(self.room_id, self.label)

    def keys(self) -> set[str]:
        out = {room_key(self.room_id)}
        if self.label:
            out.add(room_key(self.label))
        out.discard("")
        return out

    def order_free_keys(self) -> set[str]:
        out = {_order_free_key(self.room_id)}
        if self.label:
            out.add(_order_free_key(self.label))
        out.discard("")
        return out


_ROOMS_SQL = text(
    "SELECT r.room_id, s.room_label, s.sat_type, "
    "       (m.room_id IS NOT NULL OR p.room_id IS NOT NULL) AS seen "
    "FROM (SELECT room_id FROM satellites "
    "      UNION SELECT room_id FROM mpd_rooms "
    "      UNION SELECT room_id FROM satellite_pairings) r "
    "LEFT JOIN satellites s ON s.room_id = r.room_id "
    "LEFT JOIN mpd_rooms m ON m.room_id = r.room_id "
    "LEFT JOIN satellite_pairings p ON p.room_id = r.room_id "
    "ORDER BY r.room_id"
)


def _has_player(room_id: str) -> bool:
    from domovoi.clients import mpd as mpd_module
    from domovoi.config import settings

    if settings.use_stubs:
        return room_id in mpd_module._clients or room_id in mpd_module._room_ports
    return room_id in mpd_module._room_ports


async def household_rooms(session: Any, app: Any) -> list[Room]:
    """Every room the house has (see the module docstring), sorted by id.
    A database that can't be read leaves the rooms this process knows
    (connected now, or with a music player) — never a made-up one."""
    from domovoi.clients import mpd as mpd_module

    sessions = getattr(getattr(app, "state", None), "active_sessions", None) or {}
    found: dict[str, Room] = {}
    if session is not None:
        try:
            rows = (await session.execute(_ROOMS_SQL)).all()
        except Exception as e:  # noqa: BLE001 — the live rooms still answer
            log.warning("rooms: could not read the house's rooms: %s", e)
            rows = []
        for room_id, label, sat_type, seen in rows:
            if not room_id or is_phone_room(room_id):
                continue
            found[room_id] = Room(
                room_id=room_id,
                label=(label or "").strip() or None,
                sat_type=sat_type or "voice",
                online=room_id in sessions,
                has_player=_has_player(room_id),
                seen=bool(seen) or room_id in sessions,
            )
    for room_id in list(sessions) + list(mpd_module._room_ports):
        if room_id and room_id not in found and not is_phone_room(room_id):
            found[room_id] = Room(
                room_id=room_id,
                online=room_id in sessions,
                has_player=_has_player(room_id),
            )
    return [found[k] for k in sorted(found)]


@dataclass(frozen=True)
class Place:
    """What the words after a place preposition name.

    ``kind``: "here" (the room the person is in), "everywhere", "rooms"
    (``rooms`` holds every room those words name: one, or several
    satellites sharing a label), "unknown" (words that sound like a room
    the house doesn't have). ``said``: the words as said, without the
    preposition ("the garage")."""

    kind: Literal["here", "everywhere", "rooms", "unknown"]
    said: str
    rooms: tuple[Room, ...] = ()


def _strip_preposition(words: list[str]) -> list[str]:
    if words and words[0] in PLACE_PREPOSITIONS:
        return words[1:]
    return words


def read_place(phrase: str, rooms: Iterable[Room]) -> Place | None:
    """What ``phrase`` ("in the garage", "everywhere", "on the patio")
    names, or None when it names no place at all ("in the key of life")."""
    words = phrase.split()
    said_words = _strip_preposition(words)
    if not said_words:
        return None
    said = " ".join(said_words)
    key = room_key(said)
    if not key:
        return None
    if key in _HERE:
        return Place("here", said)
    if key in _EVERYWHERE:
        return Place("everywhere", said)
    rooms = list(rooms)
    hits = tuple(r for r in rooms if key in r.keys())
    if not hits:
        order_free = _order_free_key(said)
        hits = tuple(r for r in rooms if order_free in r.order_free_keys())
    if hits:
        return Place("rooms", said, hits)
    if looks_like_a_room(said):
        return Place("unknown", said)
    return None


def looks_like_a_room(said: str) -> bool:
    """Whether ``said`` sounds like a room ("the attic", "the guest
    room"): at most three words, no place preposition inside, and it ends
    in a room word."""
    raw = [w for w in _WORD_RE.findall(said.lower()) if w not in _ARTICLES]
    if not raw or len(raw) > 3 or any(w in PLACE_PREPOSITIONS for w in raw):
        return False
    last = _singular(raw[-1])
    return last in ROOM_WORDS or last.endswith("room")


def place_splits(words: list[str], start: int = 1) -> list[tuple[int, str]]:
    """Where a place may begin in ``words`` (from index ``start``): every
    place preposition, and the place phrases that need none ("everywhere",
    "all over the house"). Earliest first — the longest place first. Each
    item is (index, the place words joined)."""
    out: list[tuple[int, str]] = []
    n = len(words)
    for i in range(max(start, 0), n):
        if words[i] in PLACE_PREPOSITIONS and i < n - 1:
            out.append((i, " ".join(words[i:])))
            continue
        for bare in _BARE_PLACES:
            if tuple(words[i:]) == bare:
                out.append((i, " ".join(words[i:])))
                break
    return out


def say_rooms(rooms: Iterable[Room], *, limit: int = 6) -> str:
    """ "the dining room, the garage and the office": each name once (two
    satellites may share a label), A to Z, at most ``limit`` (then "and N
    more")."""
    names = sorted(
        {r.spoken for r in rooms},
        key=lambda n: (n[4:] if n.lower().startswith("the ") else n).lower(),
    )
    if not names:
        return ""
    if len(names) > limit:
        rest = len(names) - limit + 1
        names = names[: limit - 1] + [f"{rest} more"]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


__all__ = [
    "PLACE_PREPOSITIONS",
    "Place",
    "ROOM_WORDS",
    "Room",
    "household_rooms",
    "looks_like_a_room",
    "place_splits",
    "read_place",
    "room_key",
    "say_rooms",
    "spoken_room",
]
