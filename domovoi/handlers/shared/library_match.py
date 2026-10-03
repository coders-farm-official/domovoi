"""Library lookups by name: MPD paths ↔ library rows, and the spoken-name
resolver that turns "play suicide boys" into the right library rows.

Two jobs live here.

**1. MPD path → library row** (:func:`library_path_for_mpd_file`).

The :mod:`domovoi.workers.library_indexer` walks
``settings.music_dir`` via :py:meth:`pathlib.Path.rglob` and stamps
``str(path)`` — on Windows that produces a backslash-separated
absolute path like ``C:\\Users\\Kamron\\music\\Artist\\Track.mp3``.
MPD inside Docker mounts the same directory at ``/music`` and
reports forward-slash relative URIs like ``Artist/Track.mp3``.

Joining ``Path(settings.music_dir).expanduser() / mpd_file`` and
string-ifying it reproduces what the indexer stored. Exact-string
match against ``library_tracks.file_path``; no ``LIKE`` escape
rules in play.

Used by:

* :func:`web.backend.api.music.favorite_now_playing` (the
  now-playing heart and the heart's filled-state lookup).
* :class:`domovoi.handlers.playlist.PlaylistHandler` (the
  voice "add this to my X playlist" path, which needs to resolve
  the now-playing local file to a library row before inserting).

**2. The spoken-name resolver** (everything below the divider).

A spoken request is matched against the library by how names SOUND
(:mod:`domovoi.handlers.shared.spoken_names`), not by substring, before
MusicHandler's existing MPD search runs. The contract every caller codes
against is the set of types and functions in this module; their behaviour
is specified in the spoken-match CONTRACT (scratchpad
``spoken-match/CONTRACT.md``) and owned by the resolver.

THIS FILE IS THE STAGE-0 SEAM: the types are final, the functions are
safe placeholders — :func:`resolve_request` answers ``decision="none"``
(so MusicHandler falls through to today's cascade, exactly as before) and
the rest answer "nothing" — until the resolver builder fills them in. Code
written against them (the did-you-mean dialog, the alias voice handler,
the MusicBrainz fetch) works unchanged once it lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any, Literal, Mapping, Sequence, get_args

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession


def library_path_for_mpd_file(mpd_file: str) -> str:
    """Return the absolute host path the library indexer would have
    stamped for an MPD-relative URI.

    Imports ``domovoi.config.settings`` lazily so this module
    is safe to import from places that haven't initialized config
    yet (e.g. test bootstrap).
    """
    from domovoi.config import settings

    return str(Path(settings.music_dir).expanduser() / mpd_file)


def mpd_file_for_library_path(file_path: str) -> str | None:
    """The inverse of :func:`library_path_for_mpd_file`: the MPD-relative
    URI (forward slashes) of a ``library_tracks.file_path``, or None when
    the path is not inside ``settings.music_dir`` (then the caller falls
    back to MPD's own search for that row)."""
    from domovoi.config import settings

    if not file_path:
        return None
    root = Path(settings.music_dir).expanduser()
    try:
        rel = PurePath(file_path).relative_to(root)
    except ValueError:
        return None
    uri = rel.as_posix()
    return uri if uri and uri != "." else None


# ─── The spoken-name resolver: types (final) ──────────────────────────────

#: What a resolved name can be. "artist": one performer (all tracks whose
#: credit names them, every spelling of the name merged by alias_key);
#: "credit": a whole multi-artist credit string ("Earth, Wind & Fire");
#: "title": a song title by one primary artist; "album": an album (its
#: name + the folder it sits in); "track": one specific library row (the
#: target of a song alias). "title artist" phrases are an index-internal
#: way of reaching a title entity, never a type of their own.
EntityType = Literal["artist", "credit", "title", "album", "track"]
ENTITY_TYPES: frozenset[str] = frozenset(get_args(EntityType))

Decision = Literal["play", "ask", "none"]


@dataclass(frozen=True)
class EntityRef:
    """A library entity, JSON-safe: the did-you-mean dialog parks it in
    session context and resolves it again on "yes" (:func:`candidate_from_ref`).

    ``key`` identifies the entity within its type (see the CONTRACT):
    artist / credit — ``alias_key(name)``; title — ``"<title key>|<artist
    key>"``; album — ``"<album key>|<folder key>"``; track — the
    library_tracks id as a string. ``label`` is the library's own spelling;
    ``artist_label`` the primary artist's, for titles, albums and tracks."""

    type: EntityType
    key: str
    label: str
    artist_label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "key": self.key,
            "label": self.label,
            "artist_label": self.artist_label,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "EntityRef | None":
        """The ref a :meth:`to_dict` payload describes, or None for anything
        malformed (a parked payload from an older build, a hand edit)."""
        if not isinstance(data, Mapping):
            return None
        kind, key, label = data.get("type"), data.get("key"), data.get("label")
        artist = data.get("artist_label")
        if kind not in ENTITY_TYPES or not isinstance(key, str) or not key:
            return None
        if not isinstance(label, str) or (artist is not None and not isinstance(artist, str)):
            return None
        return cls(type=kind, key=key, label=label, artist_label=artist)


@dataclass(frozen=True)
class Candidate:
    """One scored reading of a request.

    ``score`` is 0..1 (1.0 = an exact spoken-form or alias match).
    ``via`` says what matched: "exact", "alias" (a household alias),
    "musicbrainz" (a fetched alias), "fuzzy", "phonetic", "by_split"
    ("<title> by <artist>"), "phrase" (a "title artist" phrase).
    ``speak`` is how to SAY it in a reply (a household or MusicBrainz
    spoken alias that sounds like the name when there is one, else
    ``spoken_names.speakable(label)``). ``track_ids`` are the library rows
    it would play, in the entity's natural order (albums: file order;
    artists and credits: library order — the caller shuffles)."""

    ref: EntityRef
    score: float
    via: str
    speak: str
    track_ids: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe, WITHOUT the track ids (an artist can have hundreds;
        :func:`candidate_from_ref` finds them again)."""
        return {
            "ref": self.ref.to_dict(),
            "score": round(float(self.score), 4),
            "via": self.via,
            "speak": self.speak,
        }


@dataclass(frozen=True)
class Resolution:
    """The resolver's answer to one request.

    ``decision``: "play" (best ≥ the play threshold), "ask" (best in
    [ask threshold, play threshold)), "none" (nothing ≥ the ask threshold,
    or the resolver is off / not ready). ``candidates``: best first — for
    "ask" the distinct entities to offer (≤ 3, all ≥ the ask threshold),
    for "play" the winner then its runners-up. ``heard``: the request text
    the decision was made on (carrier words stripped). ``reason``: a short
    code for logs and the gate ("exact", "alias", "fuzzy", "below_ask",
    "disabled", "index_not_ready", "empty_query", "not_built", ...)."""

    decision: Decision
    candidates: tuple[Candidate, ...] = ()
    heard: str = ""
    reason: str = ""

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    @classmethod
    def none(cls, reason: str, heard: str = "") -> "Resolution":
        return cls(decision="none", candidates=(), heard=heard, reason=reason)


@dataclass(frozen=True)
class TrackRow:
    """The library_tracks columns a play needs."""

    id: int
    title: str | None
    artist: str | None
    album: str | None
    file_path: str


class LibraryIndex:
    """The in-memory index the resolver scores against. Opaque to callers;
    built by :func:`build_index`."""


# ─── The spoken-name resolver: functions (stage-0 placeholders) ───────────


def build_index(
    tracks: Sequence[Mapping[str, Any]], aliases: Sequence[Mapping[str, Any]] = ()
) -> LibraryIndex:
    """Pure: an index over ``tracks`` (mappings with id, title, artist,
    album, file_path) and ``aliases`` (library_aliases rows, suppressed
    ones excluded by the caller). No DB, no settings — the gate harness
    builds one from a library snapshot."""
    raise NotImplementedError("spoken-name resolver not built yet (stage-0 seam)")


def resolve(
    index: LibraryIndex,
    query: Mapping[str, str],
    *,
    kinds: frozenset[str] | None = None,
    play_threshold: float,
    ask_threshold: float,
) -> Resolution:
    """Pure: resolve one MusicHandler query dict ({"any": ...},
    {"title": ..., "artist": ...} or {"artist": ...}) against ``index``.
    ``kinds`` restricts the entity types considered (None = all)."""
    raise NotImplementedError("spoken-name resolver not built yet (stage-0 seam)")


async def resolve_request(
    session: "AsyncSession",
    query: Mapping[str, str],
    *,
    kinds: frozenset[str] | None = None,
) -> Resolution:
    """The production entry point: make sure the process-wide index is
    current (rebuilding it when the library or the aliases changed), then
    :func:`resolve` with the thresholds from settings. Never raises for a
    data problem — it answers ``decision="none"`` with a reason and the
    caller runs today's cascade."""
    heard = (query.get("any") or query.get("title") or query.get("artist") or "").strip()
    return Resolution.none("not_built", heard=heard)


async def candidate_from_ref(
    session: "AsyncSession", ref: EntityRef | Mapping[str, Any]
) -> Candidate | None:
    """The entity a parked ref names, as it is NOW (its current tracks),
    or None when it has left the library."""
    return None


async def match_reply(
    session: "AsyncSession", text: str, refs: Sequence[EntityRef]
) -> tuple[int, float] | None:
    """Which of ``refs`` a bare reply ("Pink Floyd", "the second one" is
    the dialog's job, not this) names: (index into ``refs``, score) for
    the best one scoring ≥ the ask threshold, else None. Same scorer as
    :func:`resolve`, restricted to those entities."""
    return None


async def tracks_for(session: "AsyncSession", ref: EntityRef) -> list[TrackRow]:
    """The library rows ``ref`` plays, in the entity's natural order."""
    return []


async def speak_for(session: "AsyncSession", ref: EntityRef) -> str:
    """How to say ``ref`` in a reply (see :attr:`Candidate.speak`)."""
    from domovoi.handlers.shared.spoken_names import speakable

    return speakable(ref.label)


def invalidate() -> None:
    """Forget the cached fingerprint so the next request re-checks the
    library and the aliases — called in-process right after a voice alias
    add, so the very next "play subtract" sees it. Cheap; never blocks."""


def reset_for_tests() -> None:
    """Drop the process-wide index entirely (tests that rewrite
    library_tracks call this; the fingerprint would catch it anyway)."""


def index_status() -> dict[str, Any]:
    """For logs and the dashboard: {"state": "not_built" | "building" |
    "ready" | "error", "tracks": n, "entities": n, "aliases": n,
    "built_at": iso | None, "build_ms": n | None}."""
    return {"state": "not_built", "tracks": 0, "entities": 0, "aliases": 0,
            "built_at": None, "build_ms": None}


__all__ = [
    "Candidate",
    "Decision",
    "ENTITY_TYPES",
    "EntityRef",
    "EntityType",
    "LibraryIndex",
    "Resolution",
    "TrackRow",
    "build_index",
    "candidate_from_ref",
    "index_status",
    "invalidate",
    "library_path_for_mpd_file",
    "match_reply",
    "mpd_file_for_library_path",
    "reset_for_tests",
    "resolve",
    "resolve_request",
    "speak_for",
    "tracks_for",
]
