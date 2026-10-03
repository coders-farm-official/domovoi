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
against is the set of types and functions in this module (the
did-you-mean dialog, the alias voice handler and the MusicBrainz fetch all
use it); the index and the scorer themselves are pure and live in
:mod:`domovoi.handlers.shared.spoken_index`, imported on first use so the
web process and the handler registry, which import this module at start-up,
never load rapidfuzz / Metaphone unless the resolver runs.

The process keeps ONE index of the library and its aliases. Every request
first reads a fingerprint of ``library_tracks`` and ``library_aliases``
(one query, ~1 ms): a changed alias table is folded in on the spot (so
"when I say X I mean Y" works on the very next turn), a changed library is
rebuilt in the background while the old index keeps answering — unless the
library is small enough to rebuild in a blink, then inline. Nothing here
ever fails a play: any problem answers ``decision="none"`` and
MusicHandler runs today's search.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any, Literal, Mapping, Sequence, get_args

from sqlalchemy import text

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

    from domovoi.handlers.shared import spoken_index

log = logging.getLogger(__name__)


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

    ``key`` identifies the entity within its type:
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
    built by :func:`build_index` (a thin handle on
    :class:`spoken_index.Index`)."""

    __slots__ = ("_impl",)

    def __init__(self, impl: "spoken_index.Index") -> None:
        self._impl = impl

    def stats(self) -> dict[str, Any]:
        """{"tracks", "entities", "forms", "aliases", "aliases_inert",
        "build_ms", "built_at"}."""
        return self._impl.stats()


# ─── The spoken-name resolver: pure functions ─────────────────────────────


def _impl():
    """The index/scorer module, imported on first use (see the module
    docstring: its dependencies must never be a start-up requirement)."""
    from domovoi.handlers.shared import spoken_index

    return spoken_index


def build_index(
    tracks: Sequence[Mapping[str, Any]], aliases: Sequence[Mapping[str, Any]] = ()
) -> LibraryIndex:
    """Pure: an index over ``tracks`` (mappings with id, title, artist,
    album, file_path) and ``aliases`` (library_aliases rows, suppressed
    ones excluded by the caller). No DB, no settings — the gate harness
    builds one from a library snapshot."""
    return LibraryIndex(_impl().build(tracks, aliases))


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
    return _impl().resolve(
        index._impl, query, kinds=kinds,
        play_threshold=play_threshold, ask_threshold=ask_threshold,
    )


# ─── The process-wide index ───────────────────────────────────────────────

#: A library at or under this many tracks is rebuilt inline when it
#: changes (≈0.2 s); a bigger one in the background while the old index
#: keeps answering.
INLINE_REBUILD_MAX_TRACKS = 1000
#: A background rebuild waits this long first, so a burst of library
#: writes (an upload, the enricher) costs one rebuild.
REBUILD_DEBOUNCE_SEC = 2.0

_LIBRARY_FP_SQL = text(
    "SELECT (SELECT count(*) FROM library_tracks),"
    " (SELECT coalesce(max(id), 0) FROM library_tracks),"
    " (SELECT max(greatest(added_at, coalesce(enriched_at, added_at))) FROM library_tracks),"
    " to_regclass('public.library_aliases') IS NOT NULL"
)
_FULL_FP_SQL = text(
    "SELECT (SELECT count(*) FROM library_tracks),"
    " (SELECT coalesce(max(id), 0) FROM library_tracks),"
    " (SELECT max(greatest(added_at, coalesce(enriched_at, added_at))) FROM library_tracks),"
    " (SELECT count(*) FROM library_aliases),"
    " (SELECT coalesce(max(id), 0) FROM library_aliases),"
    " (SELECT max(updated_at) FROM library_aliases)"
)
_TRACKS_SQL = text(
    "SELECT id, title, artist, album, file_path FROM library_tracks ORDER BY id"
)
_ALIASES_SQL = text(
    "SELECT id, alias, alias_key, target_type, target_key, target_name,"
    " target_artist_key, target_track_id, source, suppressed, created_at, updated_at"
    " FROM library_aliases WHERE NOT suppressed ORDER BY id"
)


@dataclass
class _State:
    index: LibraryIndex | None = None
    library_fp: tuple | None = None
    alias_fp: tuple | None = None
    #: library_aliases exists (V019): None = not checked yet. Once seen it
    #: is never re-checked (a table does not disappear under a running core).
    alias_table: bool | None = None
    warned_no_aliases: bool = False
    state: str = "not_built"
    last_error: str | None = None
    first_build: "asyncio.Future[None] | None" = None
    rebuild_task: "asyncio.Task[None] | None" = None
    rebuilds: int = field(default=0)


_STATE = _State()


async def _fingerprint(session: "AsyncSession") -> tuple[tuple, tuple | None]:
    """(library part, alias part or None when there is no alias table)."""
    if _STATE.alias_table:
        row = (await session.execute(_FULL_FP_SQL)).one()
        return tuple(row[:3]), tuple(row[3:])
    row = (await session.execute(_LIBRARY_FP_SQL)).one()
    if not row[3]:
        if not _STATE.warned_no_aliases:
            log.warning(
                "spoken-name resolver: no library_aliases table (V019 not "
                "applied) — matching library names only, without aliases"
            )
            _STATE.warned_no_aliases = True
        return tuple(row[:3]), None
    _STATE.alias_table = True
    row = (await session.execute(_FULL_FP_SQL)).one()
    return tuple(row[:3]), tuple(row[3:])


async def _load_tracks(session: "AsyncSession") -> list[dict[str, Any]]:
    return [dict(r) for r in (await session.execute(_TRACKS_SQL)).mappings().all()]


async def _load_aliases(session: "AsyncSession", alias_fp: tuple | None) -> list[dict[str, Any]]:
    if alias_fp is None:
        return []
    return [dict(r) for r in (await session.execute(_ALIASES_SQL)).mappings().all()]


def _install(index: LibraryIndex, library_fp: tuple, alias_fp: tuple | None) -> None:
    _STATE.index = index
    _STATE.library_fp = library_fp
    _STATE.alias_fp = alias_fp
    _STATE.state = "ready"
    _STATE.last_error = None
    stats = index.stats()
    log.info(
        "spoken-name index: %d tracks, %d entities, %d aliases in %.0f ms",
        stats["tracks"], stats["entities"], stats["aliases"], stats["build_ms"],
    )


async def _build_now(session: "AsyncSession", library_fp: tuple, alias_fp: tuple | None) -> None:
    """Build the whole index with the caller's session (it sees the
    caller's own uncommitted rows), sharing one build between concurrent
    first requests."""
    loop = asyncio.get_running_loop()
    pending = _STATE.first_build
    if pending is not None and not pending.done() and pending.get_loop() is loop:
        await asyncio.shield(pending)
        if _STATE.index is None:
            raise RuntimeError(_STATE.last_error or "index build failed")
        return
    fut: asyncio.Future[None] = loop.create_future()
    _STATE.first_build = fut
    if _STATE.index is None:
        _STATE.state = "building"
    try:
        tracks = await _load_tracks(session)
        aliases = await _load_aliases(session, alias_fp)
        index = await asyncio.to_thread(build_index, tracks, aliases)
        _install(index, library_fp, alias_fp)
    except Exception as e:
        _STATE.last_error = f"{type(e).__name__}: {e}"
        if _STATE.index is None:
            _STATE.state = "error"
        raise
    finally:
        if not fut.done():
            fut.set_result(None)


async def _rebuild_later() -> None:
    """The background rebuild: wait out the debounce, read the library
    with a session of its own, build in a worker thread, swap it in."""
    try:
        await asyncio.sleep(REBUILD_DEBOUNCE_SEC)
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            library_fp, alias_fp = await _fingerprint(s)
            tracks = await _load_tracks(s)
            aliases = await _load_aliases(s, alias_fp)
        index = await asyncio.to_thread(build_index, tracks, aliases)
        _STATE.rebuilds += 1
        _install(index, library_fp, alias_fp)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — the old index keeps answering
        _STATE.last_error = f"{type(e).__name__}: {e}"
        log.warning("spoken-name index: background rebuild failed: %s", e)


def _schedule_rebuild() -> None:
    task = _STATE.rebuild_task
    if task is not None and not task.done():
        return
    _STATE.rebuild_task = asyncio.get_running_loop().create_task(
        _rebuild_later(), name="spoken-index-rebuild"
    )


async def current_index(session: "AsyncSession") -> LibraryIndex:
    """The process-wide index, brought up to date with the library and the
    aliases as far as this request needs (see the module docstring)."""
    library_fp, alias_fp = await _fingerprint(session)
    if _STATE.index is None:
        await _build_now(session, library_fp, alias_fp)
    elif library_fp != _STATE.library_fp:
        if int(library_fp[0] or 0) <= INLINE_REBUILD_MAX_TRACKS:
            await _build_now(session, library_fp, alias_fp)
        else:
            _schedule_rebuild()
    index = _STATE.index
    assert index is not None
    if alias_fp != _STATE.alias_fp:
        aliases = await _load_aliases(session, alias_fp)
        index = LibraryIndex(await asyncio.to_thread(_impl().with_aliases, index._impl, aliases))
        _STATE.index = index
        _STATE.alias_fp = alias_fp
        log.info("spoken-name index: aliases reloaded (%d attached)", index.stats()["aliases"])
    return index


def _query_text(query: Mapping[str, str]) -> str:
    parts = [str(query.get(k) or "").strip() for k in ("any", "title", "artist")]
    if parts[1] and parts[2]:
        return f"{parts[1]} by {parts[2]}"
    return next((p for p in parts if p), "")


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
    from domovoi.config import settings

    heard = _query_text(query)
    if not settings.music_match_enabled:
        return Resolution.none("disabled", heard=heard)
    if not heard:
        return Resolution.none("empty_query")
    if session is None:
        return Resolution.none("no_session", heard=heard)
    try:
        index = await current_index(session)
        t0 = time.perf_counter()
        res = await asyncio.to_thread(
            resolve, index, query, kinds=kinds,
            play_threshold=float(settings.music_match_play_threshold),
            ask_threshold=float(settings.music_match_ask_threshold),
        )
    except Exception as e:  # noqa: BLE001 — today's search still runs
        log.warning("spoken-name resolver failed for %r: %s", heard, e)
        return Resolution.none("error", heard=heard)
    best = res.best
    log.info(
        "spoken-name match %r → %s%s (%s, %.0f ms)",
        heard, res.decision,
        f" {best.ref.type} {best.ref.label!r} {best.score:.3f}" if best else "",
        res.reason, (time.perf_counter() - t0) * 1000.0,
    )
    return res


async def candidate_from_ref(
    session: "AsyncSession", ref: EntityRef | Mapping[str, Any]
) -> Candidate | None:
    """The entity a parked ref names, as it is NOW (its current tracks),
    or None when it has left the library."""
    if not isinstance(ref, EntityRef):
        ref = EntityRef.from_dict(ref)
        if ref is None:
            return None
    try:
        index = await current_index(session)
    except Exception as e:  # noqa: BLE001
        log.warning("spoken-name resolver: candidate_from_ref failed: %s", e)
        return None
    impl = index._impl
    i = impl.find(ref.type, ref.key)
    if i is not None:
        return impl.candidate(i, 1.0, "ref")
    if ref.type == "track" and ref.key.isdigit():
        rows = await rows_by_id(session, [int(ref.key)])
        if rows:
            row = rows[0]
            from domovoi.handlers.shared.spoken_names import speakable, split_credit

            parts = split_credit(row.artist)
            return Candidate(
                ref=EntityRef(
                    type="track", key=ref.key, label=row.title or ref.label,
                    artist_label=parts[0] if parts else None,
                ),
                score=1.0, via="ref", speak=speakable(row.title or ref.label),
                track_ids=(row.id,),
            )
    return None


async def match_reply(
    session: "AsyncSession", text: str, refs: Sequence[EntityRef]
) -> tuple[int, float] | None:
    """Which of ``refs`` a bare reply ("Pink Floyd", "the second one" is
    the dialog's job, not this) names: (index into ``refs``, score) for
    the best one scoring ≥ the ask threshold, else None. Same scorer as
    :func:`resolve`, restricted to those entities."""
    from domovoi.config import settings

    try:
        index = await current_index(session)
        impl = index._impl
        idxs = []
        for ref in refs:
            if not isinstance(ref, EntityRef):
                ref = EntityRef.from_dict(ref)
            idxs.append(impl.find(ref.type, ref.key) if ref is not None else None)
        return _impl().match_reply(
            impl, text, idxs,
            play=float(settings.music_match_play_threshold),
            ask=float(settings.music_match_ask_threshold),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("spoken-name resolver: match_reply failed for %r: %s", text, e)
        return None


async def rows_by_id(session: "AsyncSession", ids: Sequence[int]) -> list[TrackRow]:
    """The library rows with these ids, in the order given (ids no longer
    in the library are left out)."""
    wanted = [int(i) for i in ids]
    if not wanted:
        return []
    res = await session.execute(
        text(
            "SELECT id, title, artist, album, file_path FROM library_tracks "
            "WHERE id = ANY(:ids)"
        ),
        {"ids": wanted},
    )
    by_id = {
        int(r.id): TrackRow(
            id=int(r.id), title=r.title, artist=r.artist, album=r.album,
            file_path=r.file_path,
        )
        for r in res
    }
    return [by_id[i] for i in dict.fromkeys(wanted) if i in by_id]


async def tracks_for(session: "AsyncSession", ref: EntityRef) -> list[TrackRow]:
    """The library rows ``ref`` plays, in the entity's natural order."""
    if not isinstance(ref, EntityRef):
        ref = EntityRef.from_dict(ref)
        if ref is None:
            return []
    try:
        index = await current_index(session)
    except Exception as e:  # noqa: BLE001
        log.warning("spoken-name resolver: tracks_for failed: %s", e)
        return []
    i = index._impl.find(ref.type, ref.key)
    if i is not None:
        return await rows_by_id(session, index._impl.entity(i).track_ids)
    if ref.type == "track" and ref.key.isdigit():
        return await rows_by_id(session, [int(ref.key)])
    return []


def speak_label(kind: str, label: str | None) -> str:
    """How to say a name of ``kind`` ("artist", ...) from the index already
    in memory (no DB): its spoken alias when the household has one, else
    the name made speakable."""
    from domovoi.handlers.shared.spoken_names import alias_key, speakable

    index = _STATE.index
    if index is not None and label:
        try:
            i = index._impl.find(kind, alias_key(label))
            if i is not None:
                return index._impl.speak(i)
        except Exception as e:  # noqa: BLE001
            log.debug("speak_label(%r, %r) failed: %s", kind, label, e)
    return speakable(label)


async def speak_for(session: "AsyncSession", ref: EntityRef) -> str:
    """How to say ``ref`` in a reply (see :attr:`Candidate.speak`)."""
    from domovoi.handlers.shared.spoken_names import speakable

    try:
        index = await current_index(session)
        i = index._impl.find(ref.type, ref.key)
        if i is not None:
            return index._impl.speak(i)
    except Exception as e:  # noqa: BLE001
        log.warning("spoken-name resolver: speak_for failed: %s", e)
    return speakable(ref.label)


def invalidate(*, library: bool = False) -> None:
    """Forget the cached alias fingerprint so the next request reloads the
    aliases — called in-process right after a voice alias add, so the very
    next "play subtract" sees it. Cheap; never blocks.

    ``library=True`` forgets the library's fingerprint too, for a change
    the fingerprint cannot see: a row renamed IN PLACE (the provider
    download pipeline re-ingesting a track, ``LibraryAPI.ingest_track``,
    rewrites title / artist / album without touching ``added_at`` or
    ``enriched_at``, so neither the count, the newest id nor the newest
    stamp moves). The next request rebuilds (inline for a small library);
    a running index also gets a background rebuild after the usual
    debounce, which reads the change once the writer has committed it."""
    _STATE.alias_fp = ("invalidated",)
    if not library:
        return
    _STATE.library_fp = None
    if _STATE.index is None:
        return
    try:
        _schedule_rebuild()
    except RuntimeError:  # no running loop (a sync caller): the next request rebuilds
        pass


def is_library_name(text: str | None) -> bool:
    """Whether ``text`` is, said aloud, the name of something IN the
    library (an artist, credit, title or album — not an alias), by the
    index already in memory. False when there is no index yet."""
    index = _STATE.index
    if index is None or not text:
        return False
    try:
        from domovoi.handlers.shared.spoken_names import compact, spoken_forms

        exact = index._impl.exact
        return any(compact(f) in exact for f in spoken_forms(text))
    except Exception as e:  # noqa: BLE001 — a reply never fails on its wording
        log.debug("is_library_name(%r) failed: %s", text, e)
        return False


def reset_for_tests() -> None:
    """Drop the process-wide index entirely (tests that rewrite
    library_tracks call this; the fingerprint would catch it anyway)."""
    global _STATE
    task = _STATE.rebuild_task
    if task is not None and not task.done():
        try:
            task.cancel()
        except RuntimeError:  # its loop is already closed
            pass
    _STATE = _State()


def index_status() -> dict[str, Any]:
    """For logs and the dashboard: {"state": "not_built" | "building" |
    "ready" | "error", "tracks": n, "entities": n, "aliases": n,
    "built_at": iso | None, "build_ms": n | None}."""
    index = _STATE.index
    if index is None:
        return {"state": _STATE.state, "tracks": 0, "entities": 0, "aliases": 0,
                "built_at": None, "build_ms": None, "last_error": _STATE.last_error}
    stats = index.stats()
    return {
        "state": _STATE.state,
        "tracks": stats["tracks"],
        "entities": stats["entities"],
        "aliases": stats["aliases"],
        "built_at": stats["built_at"],
        "build_ms": stats["build_ms"],
        "last_error": _STATE.last_error,
    }


async def warm() -> None:
    """Boot hook ``core.spoken_index`` (after ``core.library_index``):
    build the index before the first "play …" needs it. Never raises."""
    from domovoi.config import settings

    if not settings.music_match_enabled:
        return
    try:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            await current_index(s)
    except Exception as e:  # noqa: BLE001 — the first request builds it instead
        log.warning("spoken-name index: warm-up failed: %s", e)


__all__ = [
    "Candidate",
    "Decision",
    "ENTITY_TYPES",
    "EntityRef",
    "EntityType",
    "INLINE_REBUILD_MAX_TRACKS",
    "LibraryIndex",
    "Resolution",
    "TrackRow",
    "build_index",
    "candidate_from_ref",
    "current_index",
    "index_status",
    "invalidate",
    "is_library_name",
    "library_path_for_mpd_file",
    "match_reply",
    "mpd_file_for_library_path",
    "reset_for_tests",
    "resolve",
    "resolve_request",
    "rows_by_id",
    "speak_for",
    "speak_label",
    "tracks_for",
    "warm",
]
