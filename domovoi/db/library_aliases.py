"""Household "also called" names for library artists, albums and songs
(migration V019, ``library_aliases``) — the repository and the policy, in
one place both processes use: the web dashboard's ``/api/music/aliases``
routes and the core's voice handler (``handlers/music_alias.py``).

The rules (spoken-match contract §3, owner decisions 2026-10-02):

* **One row per name → target.** A target can have any number of names
  ("there may be many names a band could be called"); a NAME means exactly
  one thing in the household. A name's identity is
  :func:`spoken_names.alias_key` — "Gramps", "gramps!" and "GRAMPS"
  are one alias — and the unique index on ``alias_key`` holds across every
  source, MusicBrainz included.
* **Adding a name that already exists:** the same target is a no-op; a
  MusicBrainz name a household member removed (``suppressed``) is taken over
  in place; a DIFFERENT target is a conflict — never a silent second
  meaning. The caller asks "replace it?" and, on yes, retries with
  ``replace=True``, which succeeds when the caller may modify the row
  already there (:func:`may_modify`) and moves that row to the new target.
* **A name that is already the target's own library name is refused**
  (``already_its_name``): normalization covers it already.
* **Precedence vs. real library names:** a household alias MAY take a key
  that another library name has ("Lemonade" the song, taught to mean the
  album) — :func:`add_alias` answers which names it shadows so the caller
  can say "instead of …", and the resolver prefers a household alias on an
  exact tie. MusicBrainz aliases never do (the fetch skips them).
* **Who may change what** (:func:`may_modify`): an admin anything; anyone a
  MusicBrainz row (removing one hides it so the next top-up does not bring
  it back); a paired device the names IT added; a voice speaker the names
  the same recognized person added (or, for an unrecognized speaker, ones
  added by voice from the same room). Device ids are self-asserted on the
  LAN, so this is household policy, not a security boundary — the same
  posture as the per-device queue blocks (V010).

Everything here takes the caller's ``AsyncSession`` and never commits; the
web's ``session_scope`` and the core's turn commit. Spoken-name logic comes
from :mod:`domovoi.handlers.shared.spoken_names` (pure, safe in the web
process).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePath
from typing import Any, Iterable, Literal, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.handlers.shared.spoken_names import (
    KEY_VERSION,
    alias_key,
    clean_title,
    compact_forms,
    entity_names,
    same_name,
    split_credit,
)

TargetType = Literal["artist", "album", "track"]
TARGET_TYPES: tuple[str, ...] = ("artist", "album", "track")
ActorKind = Literal["admin", "device", "voice", "system"]
HOUSEHOLD_SOURCES: tuple[str, ...] = ("manual", "voice")

#: Longest alias accepted (the V019 CHECK says the same).
MAX_ALIAS_CHARS = 120
#: How many shadowed library names an add reports.
MAX_SHADOWS = 5

#: Sayings of a refusal the dashboard and the voice handler share.
ONLY_ADMIN_REMOVE = "Only an admin can remove a name someone else added."
ONLY_ADMIN_CHANGE = "Only an admin can change a name someone else added."


# ─── Who is asking ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Actor:
    """The caller of an add or a removal.

    ``admin`` — an admin Bearer (or the pre-setup LAN grace);
    ``device`` — a paired dashboard or phone, ``device_id`` the
    self-asserted id it sent (None when it sent none: the Android app
    today — its adds are then removable by an admin only);
    ``voice`` — a spoken command, ``room_id`` where it was heard and
    ``person_id`` who said it when the speaker was recognized;
    ``system`` — the MusicBrainz fetch."""

    kind: ActorKind
    device_id: str | None = None
    room_id: str | None = None
    person_id: int | None = None

    @property
    def is_admin(self) -> bool:
        return self.kind == "admin"


# ─── Rows ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AliasRow:
    """One ``library_aliases`` row (plus the adding device's live name)."""

    id: int
    alias: str
    alias_key: str
    key_version: int
    target_type: str
    target_key: str | None
    target_name: str
    target_artist_key: str | None
    target_track_id: int | None
    source: str
    created_by_kind: str
    created_by_device: str | None
    created_by_room: str | None
    created_by_person: int | None
    mb_artist_id: str | None
    mb_alias_type: str | None
    suppressed: bool
    created_at: datetime | None
    updated_at: datetime | None
    device_name: str | None = None

    @property
    def is_household(self) -> bool:
        return self.source in HOUSEHOLD_SOURCES


_COLS = (
    "a.id, a.alias, a.alias_key, a.key_version, a.target_type, a.target_key, "
    "a.target_name, a.target_artist_key, a.target_track_id, a.source, "
    "a.created_by_kind, a.created_by_device, a.created_by_room, "
    "a.created_by_person, a.mb_artist_id, a.mb_alias_type, a.suppressed, "
    "a.created_at, a.updated_at, d.name AS device_name"
)
_FROM = "library_aliases a LEFT JOIN devices d ON d.device_id = a.created_by_device"


def _row(r: Any) -> AliasRow:
    m = r._mapping if hasattr(r, "_mapping") else r
    return AliasRow(
        id=int(m["id"]),
        alias=m["alias"],
        alias_key=m["alias_key"],
        key_version=int(m["key_version"]),
        target_type=m["target_type"],
        target_key=m["target_key"],
        target_name=m["target_name"],
        target_artist_key=m["target_artist_key"],
        target_track_id=m["target_track_id"],
        source=m["source"],
        created_by_kind=m["created_by_kind"],
        created_by_device=m["created_by_device"],
        created_by_room=m["created_by_room"],
        created_by_person=m["created_by_person"],
        mb_artist_id=m["mb_artist_id"],
        mb_alias_type=m["mb_alias_type"],
        suppressed=bool(m["suppressed"]),
        created_at=m["created_at"],
        updated_at=m["updated_at"],
        device_name=m.get("device_name") if hasattr(m, "get") else None,
    )


# ─── The policy ────────────────────────────────────────────────────────────


def may_modify(row: AliasRow, actor: Actor) -> bool:
    """May ``actor`` remove ``row``, or replace it with another meaning?

    * an admin: anything;
    * anyone: a MusicBrainz row (removing it hides it);
    * a device: household rows a device added under ITS id (a row added
      without an id — the Android app today — is an admin's);
    * a voice speaker: rows added by voice by the same recognized person,
      or — when the row has no person — from the same room.
    Everything else needs an admin."""
    if actor.kind == "admin":
        return True
    if row.source == "musicbrainz":
        return True
    if actor.kind == "device":
        return (
            row.created_by_kind == "device"
            and bool(row.created_by_device)
            and row.created_by_device == actor.device_id
        )
    if actor.kind == "voice":
        if row.created_by_kind != "voice":
            return False
        if row.created_by_person is not None:
            return actor.person_id is not None and row.created_by_person == actor.person_id
        return bool(row.created_by_room) and row.created_by_room == actor.room_id
    return False


def added_by(row: AliasRow) -> str:
    """Who added a row, for the drawer — never another device's raw id."""
    if row.source == "musicbrainz":
        return "MusicBrainz"
    if row.created_by_kind == "admin":
        return "admin"
    if row.created_by_kind == "device":
        return row.device_name or "a device"
    if row.created_by_kind == "voice":
        return f"voice in {row.created_by_room}" if row.created_by_room else "voice"
    return "Domovoi"


def public_alias(row: AliasRow, actor: Actor | None) -> dict[str, Any]:
    """The ``LibraryAlias`` shape the REST API answers with."""
    return {
        "id": row.id,
        "alias": row.alias,
        "alias_key": row.alias_key,
        "target_type": row.target_type,
        "target_key": row.target_key,
        "target_name": row.target_name,
        "target_artist_key": row.target_artist_key,
        "target_track_id": row.target_track_id,
        "source": row.source,
        "created_by_kind": row.created_by_kind,
        "added_by": added_by(row),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "suppressed": row.suppressed,
        "can_remove": bool(actor is not None and may_modify(row, actor)),
    }


def normalize_alias(raw: str | None) -> str:
    """An alias as stored: trimmed, inner whitespace collapsed, trailing
    sentence punctuation dropped (a dictated "gramps." is "gramps")."""
    s = " ".join((raw or "").split())
    return s.rstrip(".,!?;:").strip()


# ─── Targets ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Target:
    """What an alias means: an artist (or a whole multi-artist credit) by
    the alias_key of its library name, an album by its name's key (pinned
    to its primary artist when ``artist_key`` is set), or one song by its
    ``library_tracks`` id. ``name`` is the library spelling, for display;
    ``artist_name`` the primary artist's, for albums and songs."""

    type: TargetType
    name: str
    key: str | None = None
    artist_key: str | None = None
    track_id: int | None = None
    artist_name: str | None = None

    def matches(self, row: AliasRow) -> bool:
        """Does ``row`` already mean this target?"""
        if row.target_type != self.type:
            return False
        if self.type == "track":
            return row.target_track_id == self.track_id
        return row.target_key == self.key and (row.target_artist_key or None) == (
            self.artist_key or None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "key": self.key,
            "artist_key": self.artist_key,
            "track_id": self.track_id,
            "artist_name": self.artist_name,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Target | None":
        """The target a :meth:`to_dict` payload describes (a parked voice
        question), or None for anything malformed."""
        if not isinstance(data, Mapping):
            return None
        kind, name = data.get("type"), data.get("name")
        if kind not in TARGET_TYPES or not isinstance(name, str):
            return None
        track_id = data.get("track_id")
        key = data.get("key")
        if kind == "track":
            if not isinstance(track_id, int):
                return None
            key = None
        elif not isinstance(key, str) or not key:
            return None
        artist_key = data.get("artist_key") if kind == "album" else None
        artist_name = data.get("artist_name")
        return cls(
            type=kind,
            name=name,
            key=key,
            artist_key=artist_key if isinstance(artist_key, str) and artist_key else None,
            track_id=track_id if kind == "track" else None,
            artist_name=artist_name if isinstance(artist_name, str) else None,
        )


def target_of(row: AliasRow) -> Target:
    """The target a stored row points at."""
    return Target(
        type=row.target_type,  # type: ignore[arg-type]
        name=row.target_name,
        key=row.target_key,
        artist_key=row.target_artist_key,
        track_id=row.target_track_id,
    )


def target_label(target: Target) -> str:
    """How the dashboard names a target in a sentence."""
    if target.type == "album":
        return f"the album {target.name}"
    return target.name


# ─── The library, as names ─────────────────────────────────────────────────

_FEATURE_SEP_RE = re.compile(r"\s(?:feat\.?|ft\.?|featuring|x|vs\.?)\s", re.I)
_NOT_AN_ARTIST = frozenset({"variousartists", "unknownartist", "va", "unknown"})


def band_like_credit(credit: str) -> bool:
    """A multi-part credit that may be ONE band's name ("Earth, Wind &
    Fire"): only ``,`` ``&`` ``;`` between its parts, at most 4 parts. A
    "feat." / "x" / "vs" credit is several performers."""
    parts = split_credit(credit)
    return 1 < len(parts) <= 4 and not _FEATURE_SEP_RE.search(f" {credit} ")


@dataclass
class LibraryNames:
    """Every name the library knows things by, keyed by alias_key — built
    from one grouped scan of ``library_tracks`` (see :func:`library_names`).
    Spellings are counted by tracks so "most frequent spelling" wins."""

    artists: dict[str, Counter] = field(default_factory=dict)   # part key → spellings
    credits: dict[str, Counter] = field(default_factory=dict)   # whole multi-part credit
    singles: dict[str, Counter] = field(default_factory=dict)   # single-performer credits
    titles: dict[str, Counter] = field(default_factory=dict)    # title (and cleaned) key
    # album key → {album spelling: Counter(primary artist key → tracks)}
    albums: dict[str, dict[str, Counter]] = field(default_factory=dict)
    primary_names: dict[str, Counter] = field(default_factory=dict)  # artist key → spellings

    def add_row(self, title: str | None, artist: str | None, album: str | None, n: int = 1) -> None:
        names = entity_names(title, artist, album)
        parts = [nm for kind, nm in names if kind == "artist"]
        primary = parts[0] if parts else ""
        primary_key = alias_key(primary) if primary else ""
        if primary_key:
            self.primary_names.setdefault(primary_key, Counter())[primary] += n
        for kind, nm in names:
            key = alias_key(nm)
            if not key:
                continue
            if kind == "artist":
                self.artists.setdefault(key, Counter())[nm] += n
            elif kind == "credit":
                self.credits.setdefault(key, Counter())[nm] += n
            elif kind == "title":
                self.titles.setdefault(key, Counter())[nm] += n
            elif kind == "album":
                self.albums.setdefault(key, {}).setdefault(nm, Counter())[primary_key] += n
        if len(parts) == 1:
            k = alias_key(parts[0])
            if k:
                self.singles.setdefault(k, Counter())[parts[0]] += n

    def keyed(self) -> dict[str, list[tuple[str, str]]]:
        """alias_key → [(type, name)] over every library name (types
        "artist", "title", "album"; a whole credit counts as an artist)."""
        out: dict[str, list[tuple[str, str]]] = {}
        for kind, table in (("artist", self.artists), ("artist", self.credits), ("title", self.titles)):
            for key, spellings in table.items():
                for nm in spellings:
                    out.setdefault(key, []).append((kind, nm))
        for key, spellings in self.albums.items():
            for nm in spellings:
                out.setdefault(key, []).append(("album", nm))
        return out


async def library_names(session: AsyncSession) -> LibraryNames:
    """One grouped scan of the library → :class:`LibraryNames`."""
    rows = (
        await session.execute(
            text(
                "SELECT title, artist, album, count(*) FROM library_tracks "
                "GROUP BY title, artist, album"
            )
        )
    ).all()
    names = LibraryNames()
    for title, artist, album, n in rows:
        names.add_row(title, artist, album, int(n))
    return names


def lookup_candidates(names: LibraryNames) -> dict[str, str]:
    """The artists the MusicBrainz fetch would look up, key → spelling
    (the contract's §4 step 4 rule): every single-performer credit, plus a
    multi-part credit AS A WHOLE when it reads like one band's name; a part
    only through its own single-performer credit; never a key under 3
    characters or "Various Artists". The dashboard's "checking — n of N"
    uses it as N."""
    out: dict[str, str] = {}
    for key, spellings in names.singles.items():
        if len(key) >= 3 and key not in _NOT_AN_ARTIST:
            out[key] = spellings.most_common(1)[0][0]
    for key, spellings in names.credits.items():
        if len(key) < 3 or key in _NOT_AN_ARTIST or key in out:
            continue
        spelling = spellings.most_common(1)[0][0]
        if band_like_credit(spelling):
            out[key] = spelling
    return out


def _most_common(c: Counter) -> str:
    return c.most_common(1)[0][0]


def _find_by_name(table: Mapping[str, Counter], name: str) -> tuple[str, str] | None:
    """(key, most frequent spelling) of the entry ``name`` names — by key
    first, then by any shared spoken form."""
    key = alias_key(name)
    if key and key in table:
        return key, _most_common(table[key])
    forms = compact_forms(name)
    if not forms:
        return None
    for k, spellings in table.items():
        for spelling in spellings:
            if compact_forms(spelling) & forms:
                return k, _most_common(spellings)
    return None


async def resolve_target(
    session: AsyncSession,
    target_type: str,
    *,
    name: str | None = None,
    artist_name: str | None = None,
    track_id: int | None = None,
    names: LibraryNames | None = None,
) -> Target | None:
    """The library thing a REST add names, or None when it is not in the
    library. Artists: a performer (any spelling cluster) or a whole
    multi-artist credit. Albums: by name, pinned to ``artist_name``'s
    primary-artist key when given (then some track of that album must have
    that primary artist). Songs: by ``library_tracks`` id."""
    if target_type == "track":
        if track_id is None:
            return None
        row = (
            await session.execute(
                text("SELECT id, title, artist, album, file_path FROM library_tracks WHERE id = :id"),
                {"id": int(track_id)},
            )
        ).first()
        if row is None:
            return None
        title, artist = track_display(row[1], row[2], row[3], row[4])
        return Target(type="track", name=title, track_id=int(row[0]), artist_name=artist)
    if not name or not name.strip():
        return None
    lib = names if names is not None else await library_names(session)
    if target_type == "artist":
        hit = _find_by_name(lib.artists, name) or _find_by_name(lib.credits, name)
        if hit is None:
            return None
        return Target(type="artist", name=hit[1], key=hit[0])
    if target_type != "album":
        return None
    key = alias_key(name)
    forms = compact_forms(name)
    want_artist = alias_key(artist_name) if artist_name and artist_name.strip() else None
    best: tuple[int, int, str, str, str | None] | None = None  # (exact, tracks, key, spelling, artist key)
    for akey, spellings in lib.albums.items():
        for spelling, by_artist in spellings.items():
            exact = akey == key
            if not exact and not (
                alias_key(clean_title(spelling)) == key or (forms and compact_forms(spelling) & forms)
            ):
                continue
            if want_artist is not None:
                pinned = want_artist if by_artist.get(want_artist) else _artist_by_form(by_artist, artist_name)
                if pinned is None:
                    continue
                cand = (int(exact), by_artist[pinned], akey, spelling, pinned)
            else:
                cand = (int(exact), sum(by_artist.values()), akey, spelling, None)
            if best is None or cand[:2] > best[:2]:
                best = cand
    if best is None:
        return None
    artist_label = None
    if best[4]:
        spellings = lib.primary_names.get(best[4])
        artist_label = _most_common(spellings) if spellings else artist_name
    return Target(type="album", name=best[3], key=best[2], artist_key=best[4], artist_name=artist_label)


def _artist_by_form(by_artist: Counter, artist_name: str | None) -> str | None:
    """The primary-artist key in ``by_artist`` that ``artist_name`` names by
    a shared spoken form (a spelling the key alone does not catch)."""
    if not artist_name:
        return None
    forms = compact_forms(artist_name)
    for k in by_artist:
        if k and forms and k in forms:
            return k
    return None


def track_display(
    title: str | None, artist: str | None, album: str | None, file_path: str | None
) -> tuple[str, str | None]:
    """(title, primary artist) a row is known by — the filename-derived
    "Artist - Title" reading included — for display and replies."""
    names = entity_names(title, artist, album)
    t = next((nm for kind, nm in names if kind == "title"), None)
    parts = [nm for kind, nm in names if kind == "artist"]
    if not t:
        t = PurePath(file_path or "").stem or "this song"
    return t, (parts[0] if parts else None)


def _own_names(target: Target) -> list[str]:
    """The target's own library name(s): an alias equal to one is
    redundant (``already_its_name``)."""
    if target.type == "artist":
        return [target.name]
    return [target.name, clean_title(target.name)]


def shadows_for(key: str, target: Target, names: LibraryNames) -> list[dict[str, str]]:
    """The OTHER library names whose key ``key`` takes over (the add says
    "instead of …"): an artist, a title or an album that is not the target
    itself. At most :data:`MAX_SHADOWS`."""
    seen: dict[tuple[str, str], None] = {}
    for kind, nm in names.keyed().get(key, []):
        if kind == target.type and alias_key(nm) == (target.key or "") and kind != "title":
            continue
        if target.type == "track" and kind == "title" and same_name(nm, target.name):
            continue
        seen.setdefault((kind, nm), None)
        if len(seen) >= MAX_SHADOWS:
            break
    return [{"type": k, "name": n} for k, n in seen]


# ─── Reads ─────────────────────────────────────────────────────────────────


async def get_alias(session: AsyncSession, alias_id: int, *, for_update: bool = False) -> AliasRow | None:
    lock = " FOR UPDATE OF a" if for_update else ""
    row = (
        await session.execute(text(f"SELECT {_COLS} FROM {_FROM} WHERE a.id = :id{lock}"), {"id": int(alias_id)})
    ).first()
    return _row(row) if row is not None else None


async def get_by_key(session: AsyncSession, key: str, *, for_update: bool = False) -> AliasRow | None:
    lock = " FOR UPDATE OF a" if for_update else ""
    row = (
        await session.execute(text(f"SELECT {_COLS} FROM {_FROM} WHERE a.alias_key = :k{lock}"), {"k": key})
    ).first()
    return _row(row) if row is not None else None


async def find_alias(session: AsyncSession, spoken: str) -> AliasRow | None:
    """The non-hidden row a spoken or typed name IS (by alias_key)."""
    key = alias_key(normalize_alias(spoken))
    if not key:
        return None
    row = await get_by_key(session, key)
    return row if row is not None and not row.suppressed else None


_ORDER = (
    "ORDER BY (a.source = 'musicbrainz'), a.created_at, a.id"
)


async def list_aliases(
    session: AsyncSession,
    *,
    target_type: str | None = None,
    target_key: str | None = None,
    track_id: int | None = None,
    source: str | None = None,
    include_suppressed: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[AliasRow], int]:
    """A filtered page of aliases (household names first) and the total."""
    where: list[str] = []
    params: dict[str, Any] = {"limit": int(limit), "offset": int(offset)}
    if target_type:
        where.append("a.target_type = :tt")
        params["tt"] = target_type
    if target_key:
        where.append("a.target_key = :tk")
        params["tk"] = target_key
    if track_id is not None:
        where.append("a.target_track_id = :tid")
        params["tid"] = int(track_id)
    if source:
        where.append("a.source = :src")
        params["src"] = source
    if not include_suppressed:
        where.append("NOT a.suppressed")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total = int(
        (await session.execute(text(f"SELECT count(*) FROM library_aliases a {clause}"), params)).scalar_one()
    )
    rows = (
        await session.execute(
            text(f"SELECT {_COLS} FROM {_FROM} {clause} {_ORDER} LIMIT :limit OFFSET :offset"), params
        )
    ).all()
    return [_row(r) for r in rows], total


async def aliases_for_target(
    session: AsyncSession,
    target: Target,
    *,
    track_ids: Sequence[int] = (),
    include_suppressed: bool = False,
) -> list[AliasRow]:
    """Every name ``target`` is also called, household names first. An
    album target with an artist key also collects the album's unpinned
    names; a song target collects the names of every row in ``track_ids``
    too (a title the library holds twice)."""
    hidden = "" if include_suppressed else " AND NOT a.suppressed"
    if target.type == "track":
        ids = sorted({int(i) for i in (*track_ids, target.track_id) if i is not None})
        if not ids:
            return []
        sql = f"SELECT {_COLS} FROM {_FROM} WHERE a.target_type = 'track' AND a.target_track_id = ANY(:ids){hidden} {_ORDER}"
        params: dict[str, Any] = {"ids": ids}
    elif target.type == "album":
        sql = (
            f"SELECT {_COLS} FROM {_FROM} WHERE a.target_type = 'album' AND a.target_key = :k "
            "AND (a.target_artist_key IS NULL OR a.target_artist_key = :ak)"
            f"{hidden} {_ORDER}"
        )
        params = {"k": target.key, "ak": target.artist_key or ""}
    else:
        sql = f"SELECT {_COLS} FROM {_FROM} WHERE a.target_type = 'artist' AND a.target_key = :k{hidden} {_ORDER}"
        params = {"k": target.key}
    rows = (await session.execute(text(sql), params)).all()
    return [_row(r) for r in rows]


# ─── Writes ────────────────────────────────────────────────────────────────

AddStatus = Literal["added", "exists", "replaced", "taken", "forbidden", "invalid"]


@dataclass
class AddOutcome:
    """What :func:`add_alias` did.

    ``added`` (a new row, or a hidden MusicBrainz row taken over),
    ``exists`` (this name already means this target — nothing changed),
    ``replaced`` (``replace=True`` moved the name to this target),
    ``taken`` (the name means something else; ``existing`` is that row and
    ``can_replace`` whether this caller may replace it), ``forbidden``
    (``replace=True`` by a caller who may not), ``invalid`` (``code``:
    ``empty`` / ``too_long`` / ``nothing_to_say`` / ``already_its_name``)."""

    status: AddStatus
    row: AliasRow | None = None
    existing: AliasRow | None = None
    can_replace: bool = False
    shadows: list[dict[str, str]] = field(default_factory=list)
    code: str | None = None
    message: str | None = None


def check_alias_text(alias: str, target: Target) -> tuple[str, str, AddOutcome | None]:
    """(normalized alias, key, refusal-or-None) for a proposed alias."""
    normalized = normalize_alias(alias)
    if not normalized:
        return normalized, "", AddOutcome(status="invalid", code="empty", message="Type a name first.")
    if len(normalized) > MAX_ALIAS_CHARS:
        return normalized, "", AddOutcome(
            status="invalid", code="too_long",
            message=f"A name can be at most {MAX_ALIAS_CHARS} characters.",
        )
    key = alias_key(normalized)
    if not key:
        return normalized, key, AddOutcome(
            status="invalid", code="nothing_to_say", message="That name has nothing to say."
        )
    if any(same_name(normalized, own) for own in _own_names(target) if own):
        return normalized, key, AddOutcome(
            status="invalid", code="already_its_name",
            message=f"That's already what {target.name} is called.",
        )
    return normalized, key, None


_SET_HOUSEHOLD = (
    "alias = :alias, key_version = :kv, target_type = :tt, target_key = :tk, "
    "target_name = :tn, target_artist_key = :tak, target_track_id = :tid, "
    "source = :src, created_by_kind = :ck, created_by_device = :cd, "
    "created_by_room = :cr, created_by_person = :cp, mb_artist_id = NULL, "
    "mb_alias_type = NULL, suppressed = FALSE, created_at = now(), updated_at = now()"
)


def _values(alias: str, key: str, target: Target, source: str, actor: Actor) -> dict[str, Any]:
    return {
        "alias": alias,
        "key": key,
        "kv": KEY_VERSION,
        "tt": target.type,
        "tk": None if target.type == "track" else target.key,
        "tn": target.name,
        "tak": target.artist_key if target.type == "album" else None,
        "tid": target.track_id if target.type == "track" else None,
        "src": source,
        "ck": actor.kind,
        "cd": actor.device_id if actor.kind == "device" else None,
        "cr": actor.room_id if actor.kind == "voice" else None,
        "cp": actor.person_id if actor.kind == "voice" else None,
    }


async def add_alias(
    session: AsyncSession,
    *,
    alias: str,
    target: Target,
    actor: Actor,
    source: str,
    replace: bool = False,
    names: LibraryNames | None = None,
) -> AddOutcome:
    """Teach the household that ``alias`` means ``target`` (rules in the
    module docstring). ``source`` is "manual" (dashboard / app) or "voice".
    Never commits."""
    if source not in HOUSEHOLD_SOURCES:
        raise ValueError(f"add_alias: household sources only, not {source!r}")
    normalized, key, refusal = check_alias_text(alias, target)
    if refusal is not None:
        return refusal
    values = _values(normalized, key, target, source, actor)

    existing = await get_by_key(session, key, for_update=True)
    if existing is None:
        inserted = (
            await session.execute(
                text(
                    "INSERT INTO library_aliases (alias, alias_key, key_version, target_type, "
                    "target_key, target_name, target_artist_key, target_track_id, source, "
                    "created_by_kind, created_by_device, created_by_room, created_by_person) "
                    "VALUES (:alias, :key, :kv, :tt, :tk, :tn, :tak, :tid, :src, :ck, :cd, :cr, :cp) "
                    "ON CONFLICT (alias_key) DO NOTHING RETURNING id"
                ),
                values,
            )
        ).first()
        if inserted is not None:
            row = await get_alias(session, int(inserted[0]))
            return AddOutcome(status="added", row=row, shadows=await _shadows(session, key, target, names))
        # Lost a race to another writer: treat its row as the existing one.
        existing = await get_by_key(session, key, for_update=True)
        if existing is None:  # pragma: no cover — deleted again in between
            return AddOutcome(status="invalid", code="retry", message="Try that again.")

    if existing.suppressed:
        # A MusicBrainz name someone removed: the household may take its key.
        row = await _overwrite(session, existing.id, values)
        return AddOutcome(status="added", row=row, shadows=await _shadows(session, key, target, names))
    if target.matches(existing):
        return AddOutcome(status="exists", row=existing, shadows=await _shadows(session, key, target, names))
    can_replace = may_modify(existing, actor)
    if not replace:
        return AddOutcome(status="taken", existing=existing, can_replace=can_replace)
    if not can_replace:
        return AddOutcome(
            status="forbidden", existing=existing, can_replace=False, message=ONLY_ADMIN_CHANGE
        )
    row = await _overwrite(session, existing.id, values)
    return AddOutcome(
        status="replaced", row=row, existing=existing, shadows=await _shadows(session, key, target, names)
    )


async def _overwrite(session: AsyncSession, alias_id: int, values: dict[str, Any]) -> AliasRow | None:
    params = {k: v for k, v in values.items() if k != "key"}
    params["id"] = alias_id
    await session.execute(text(f"UPDATE library_aliases SET {_SET_HOUSEHOLD} WHERE id = :id"), params)
    return await get_alias(session, alias_id)


async def _shadows(
    session: AsyncSession, key: str, target: Target, names: LibraryNames | None
) -> list[dict[str, str]]:
    lib = names if names is not None else await library_names(session)
    return shadows_for(key, target, lib)


RemoveStatus = Literal["removed", "suppressed", "not_found", "forbidden"]


@dataclass
class RemoveOutcome:
    status: RemoveStatus
    row: AliasRow | None = None


async def remove_alias(session: AsyncSession, alias_id: int, actor: Actor) -> RemoveOutcome:
    """Remove a name: a household row is deleted; a MusicBrainz row is
    hidden (``suppressed``) so the next top-up does not add it back. A row
    already hidden is "not found". Never commits."""
    row = await get_alias(session, alias_id, for_update=True)
    if row is None or row.suppressed:
        return RemoveOutcome(status="not_found", row=row)
    if not may_modify(row, actor):
        return RemoveOutcome(status="forbidden", row=row)
    if row.source == "musicbrainz":
        await session.execute(
            text("UPDATE library_aliases SET suppressed = TRUE, updated_at = now() WHERE id = :id"),
            {"id": row.id},
        )
        return RemoveOutcome(status="suppressed", row=row)
    await session.execute(text("DELETE FROM library_aliases WHERE id = :id"), {"id": row.id})
    return RemoveOutcome(status="removed", row=row)


# ─── The drawer: one track's names ─────────────────────────────────────────


async def drawer_for_track(session: AsyncSession, track_id: int, actor: Actor | None) -> dict[str, Any] | None:
    """Everything the track drawer's "also called" lists need, in one
    call: the song's names, each performer's (one row per credit part, plus
    the whole credit first when it reads like one band's name — "Earth,
    Wind & Fire" — so a band can be named too), and the album's. None
    when the track does not exist."""
    row = (
        await session.execute(
            text("SELECT id, title, artist, album, file_path FROM library_tracks WHERE id = :id"),
            {"id": int(track_id)},
        )
    ).first()
    if row is None:
        return None
    tid, title, artist, album, file_path = int(row[0]), row[1], row[2], row[3], row[4]
    shown_title, primary = track_display(title, artist, album, file_path)
    names = entity_names(title, artist, album)
    credit = next((nm for kind, nm in names if kind == "credit"), None)
    parts = [nm for kind, nm in names if kind == "artist"]

    def pub(rows: Iterable[AliasRow]) -> list[dict[str, Any]]:
        return [public_alias(r, actor) for r in rows]

    song = Target(type="track", name=shown_title, track_id=tid, artist_name=primary)
    out: dict[str, Any] = {
        "track": {"id": tid, "title": shown_title, "aliases": pub(await aliases_for_target(session, song))},
        "artists": [],
        "album": None,
    }
    performers: list[tuple[str, bool]] = []
    if credit and band_like_credit(credit):
        performers.append((credit, True))
    performers.extend((p, False) for p in parts)
    for name, whole in performers:
        key = alias_key(name)
        if not key:
            continue
        rows = await aliases_for_target(session, Target(type="artist", name=name, key=key))
        out["artists"].append({"name": name, "key": key, "credit": whole, "aliases": pub(rows)})
    album_name = next((nm for kind, nm in names if kind == "album"), None)
    if album_name:
        akey = alias_key(album_name)
        artist_key = alias_key(primary) if primary else None
        if akey:
            target = Target(type="album", name=album_name, key=akey, artist_key=artist_key, artist_name=primary)
            out["album"] = {
                "name": album_name,
                "key": akey,
                "artist_key": artist_key,
                "artist_name": primary,
                "aliases": pub(await aliases_for_target(session, target)),
            }
    return out


# ─── Status (the Stats tab card) ───────────────────────────────────────────


async def alias_counts(session: AsyncSession) -> dict[str, Any]:
    """Counts for ``GET /api/music/aliases/status``: names by kind, and the
    MusicBrainz lookups' progress (``library_alias_lookups``)."""
    a = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE source <> 'musicbrainz'), "
                "count(*) FILTER (WHERE source = 'musicbrainz' AND NOT suppressed), "
                "count(*) FILTER (WHERE suppressed) FROM library_aliases"
            )
        )
    ).one()
    lk = (
        await session.execute(
            text(
                "SELECT count(*), count(*) FILTER (WHERE status = 'matched'), "
                "count(*) FILTER (WHERE status = 'no_match'), "
                "count(*) FILTER (WHERE status = 'ambiguous'), "
                "count(*) FILTER (WHERE status = 'error'), "
                "coalesce(sum(aliases_added), 0), max(looked_up_at) FROM library_alias_lookups"
            )
        )
    ).one()
    names = await library_names(session)
    return {
        "household": int(a[0]),
        "musicbrainz": int(a[1]),
        "suppressed": int(a[2]),
        "fetch": {
            "artists_total": len(lookup_candidates(names)),
            "checked": int(lk[0]),
            "matched": int(lk[1]),
            "no_match": int(lk[2]),
            "ambiguous": int(lk[3]),
            "errors": int(lk[4]),
            "aliases_added": int(lk[5]),
            "last_lookup_at": lk[6].isoformat() if lk[6] else None,
        },
    }


# ─── The resolver's feed ───────────────────────────────────────────────────

_INDEX_COLS = (
    "id, alias, alias_key, key_version, target_type, target_key, target_name, "
    "target_artist_key, target_track_id, source, created_at, updated_at"
)


async def alias_rows_for_index(session: AsyncSession) -> list[dict[str, Any]] | None:
    """The rows the spoken-name resolver's ``build_index(tracks, aliases)``
    takes: every NON-hidden alias, as plain mappings, oldest first. None
    when this database has no ``library_aliases`` table (a lane without
    V019) — the resolver then runs without aliases (one warning, its
    side)."""
    present = (
        await session.execute(text("SELECT to_regclass('public.library_aliases') IS NOT NULL"))
    ).scalar_one()
    if not present:
        return None
    rows = (
        await session.execute(
            text(f"SELECT {_INDEX_COLS} FROM library_aliases WHERE NOT suppressed ORDER BY created_at, id")
        )
    ).mappings().all()
    return [dict(r) for r in rows]


__all__ = [
    "Actor",
    "AddOutcome",
    "AliasRow",
    "LibraryNames",
    "MAX_ALIAS_CHARS",
    "ONLY_ADMIN_CHANGE",
    "ONLY_ADMIN_REMOVE",
    "RemoveOutcome",
    "TARGET_TYPES",
    "Target",
    "add_alias",
    "added_by",
    "alias_counts",
    "alias_rows_for_index",
    "aliases_for_target",
    "band_like_credit",
    "check_alias_text",
    "drawer_for_track",
    "find_alias",
    "get_alias",
    "get_by_key",
    "library_names",
    "list_aliases",
    "lookup_candidates",
    "may_modify",
    "normalize_alias",
    "public_alias",
    "remove_alias",
    "resolve_target",
    "shadows_for",
    "target_label",
    "target_of",
    "track_display",
]
