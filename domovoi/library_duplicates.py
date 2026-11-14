"""The duplicates finder's rules (V058), pure: no database, no files.

Two library tracks are copies of one song when they share a MusicBrainz
recording id, or a **song key**: the first credited artist and the title,
each normalised (case, accents, punctuation, "the", "&"/"and" — the
household name key), the title without an "Artist - " in front and without
bracketed noise ("(Official Video)", "[<video id>]", "(Lyrics)", "(feat.
X)"). Copies are grouped transitively (A~B by id, B~C by song key: one
group of three).

What a group is called, from its lengths:

* every copy within :data:`SAME_LENGTH_SEC` of the others: **likely the
  same recording**;
* copies with one recording id more than :data:`ID_LENGTH_GAP_SEC` apart:
  **identified as the same recording, but the lengths differ**;
* otherwise: **same song, different length** (a live take, a radio edit,
  an extended mix — maybe worth keeping both).

The copy kept by default (:func:`best_copy`): the highest bitrate (file
size over length; within 5 % counts as equal), then the best identified
(a MusicBrainz id) > tagged (an artist and an album) > untagged, then the
one with cover art, then the oldest in the library.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import cmp_to_key
from typing import Iterable, Sequence

SAME_LENGTH_SEC = 3
ID_LENGTH_GAP_SEC = 20
#: Bitrates within this fraction of each other count as equal.
BITRATE_TOLERANCE = 0.05

KIND_SAME = "same_recording"
KIND_ID_LENGTHS = "same_id_lengths_differ"
KIND_DIFFERENT_LENGTH = "different_length"
KIND_LABELS = {
    KIND_SAME: "Likely the same recording",
    KIND_ID_LENGTHS: "Identified as the same recording, but the lengths differ",
    KIND_DIFFERENT_LENGTH: "Same song, different length",
}

_BRACKETS_RE = re.compile(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]")
# Upload words left outside brackets ("Song - Lyrics", "Song Official Video").
_NOISE_RE = re.compile(
    r"\s*[-–—|]?\s*\b(?:official\s+(?:music\s+|lyrics?\s+)?(?:video|audio)|lyrics?\s+video"
    r"|music\s+video|lyrics?|official|hd|hq|4k)\b\s*$", re.I)


@dataclass
class Copy:
    id: int
    title: str | None
    artist: str | None
    album: str | None = None
    duration_sec: int | None = None
    recording_id: str | None = None
    size_bytes: int | None = None
    has_art: bool = False
    added_order: int = 0

    @property
    def bitrate_kbps(self) -> float | None:
        if not self.size_bytes or not self.duration_sec or self.duration_sec <= 0:
            return None
        return self.size_bytes * 8 / self.duration_sec / 1000

    @property
    def identity(self) -> int:
        """2 identified, 1 tagged, 0 untagged."""
        if self.recording_id:
            return 2
        if (self.artist or "").strip() and (self.album or "").strip():
            return 1
        return 0


@dataclass
class Group:
    key: str
    kind: str
    copies: list[Copy] = field(default_factory=list)
    best_id: int = 0

    @property
    def member_ids(self) -> list[int]:
        return sorted(c.id for c in self.copies)


def song_key(title: str | None, artist: str | None) -> str | None:
    """``"<artist key>|<title key>"``, or None when either is empty."""
    from domovoi.handlers.shared.spoken_names import alias_key, split_credit

    a = (artist or "").strip()
    t = (title or "").strip()
    if not a or not t:
        return None
    parts = split_credit(a)
    first = parts[0] if parts else a
    akey = alias_key(first)
    # "Artist - Title" written into the title: the artist part goes.
    m = re.match(r"^(.+?)\s+[-–—]\s+(.+)$", t)
    if m and alias_key(m.group(1)) in (akey, alias_key(a)):
        t = m.group(2)
    t = _NOISE_RE.sub(" ", _BRACKETS_RE.sub(" ", t))
    t = re.split(r"\s+(?:feat\.?|ft\.?|featuring)\s+", t, maxsplit=1, flags=re.I)[0]
    tkey = alias_key(t) or alias_key(title)
    if not akey or not tkey:
        return None
    return f"{akey}|{tkey}"


def _find(parent: dict[int, int], x: int) -> int:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def classify(copies: Sequence[Copy]) -> str:
    lengths = [c.duration_sec for c in copies if c.duration_sec]
    if len(lengths) == len(copies) and max(lengths) - min(lengths) <= SAME_LENGTH_SEC:
        return KIND_SAME
    by_id: dict[str, list[int]] = {}
    for c in copies:
        if c.recording_id and c.duration_sec:
            by_id.setdefault(c.recording_id, []).append(c.duration_sec)
    if any(max(v) - min(v) > ID_LENGTH_GAP_SEC for v in by_id.values() if len(v) > 1):
        return KIND_ID_LENGTHS
    if not lengths:
        return KIND_SAME
    return KIND_DIFFERENT_LENGTH


def _compare(a: Copy, b: Copy) -> int:
    """<0 when ``a`` is the better copy."""
    ra, rb = a.bitrate_kbps, b.bitrate_kbps
    if ra and rb and abs(ra - rb) / max(ra, rb) > BITRATE_TOLERANCE:
        return -1 if ra > rb else 1
    if (ra is None) != (rb is None):
        return -1 if ra is not None else 1
    for x, y in ((a.identity, b.identity), (int(a.has_art), int(b.has_art))):
        if x != y:
            return -1 if x > y else 1
    return (a.added_order > b.added_order) - (a.added_order < b.added_order) or (a.id - b.id)


def best_copy(copies: Sequence[Copy]) -> Copy:
    return sorted(copies, key=cmp_to_key(_compare))[0]


def group_key(member_ids: Iterable[int]) -> str:
    """A stable name for a group: its smallest member id and a hash of all
    of them (so a group whose membership changes is a new group)."""
    ids = sorted(int(i) for i in member_ids)
    digest = hashlib.sha256(",".join(map(str, ids)).encode()).hexdigest()[:16]
    return f"{ids[0]}-{digest}"


def find_groups(copies: Iterable[Copy]) -> list[Group]:
    """Every group of two or more copies, biggest first then by first id.
    ``best_id`` is set; sizes and art are whatever the caller filled in."""
    items = list(copies)
    parent = {c.id: c.id for c in items}
    first_by: dict[tuple[str, str], int] = {}
    for c in items:
        keys = []
        if c.recording_id:
            keys.append(("mb", c.recording_id))
        sk = song_key(c.title, c.artist)
        if sk:
            keys.append(("song", sk))
        for k in keys:
            if k in first_by:
                ra, rb = _find(parent, first_by[k]), _find(parent, c.id)
                if ra != rb:
                    parent[rb] = ra
            else:
                first_by[k] = c.id
    members: dict[int, list[Copy]] = {}
    for c in items:
        members.setdefault(_find(parent, c.id), []).append(c)
    groups = []
    for copies_ in members.values():
        if len(copies_) < 2:
            continue
        copies_.sort(key=lambda c: c.id)
        groups.append(Group(group_key(c.id for c in copies_), classify(copies_), copies_,
                            best_copy(copies_).id))
    groups.sort(key=lambda g: (-len(g.copies), g.copies[0].id))
    return groups


def candidate_ids(copies: Iterable[Copy]) -> list[Copy]:
    """Only the copies that share an id or a song key with another (what is
    worth a file stat); pure, cheap."""
    return [c for g in find_groups(copies) for c in g.copies]


__all__ = [
    "Copy", "Group", "KIND_LABELS", "best_copy", "classify", "find_groups", "group_key", "song_key",
]
