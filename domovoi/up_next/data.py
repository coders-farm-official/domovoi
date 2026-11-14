"""Up next, the data part: the SQL that builds the picker's inputs (§2, §9).

Two small interfaces keep the pure picker free of tables:

* :class:`LibrarySource` — the library itself (``library_tracks`` with
  V050's ``artist_norm`` / ``year`` / ``genre_keys``, ``playlists``,
  ``playlist_tracks``). :class:`SqlLibrary` is the only implementation.
* :class:`ListenSource` — listening history: per-track stats for personal
  affinity, the hold-back read, "played together" from listening
  sessions. :class:`MusicListens` reads ``music_listens`` (V051, phase
  P1); :class:`NoListens` answers "no history", which is also what
  :class:`MusicListens` answers while the table does not exist yet, so
  Up next works from the library alone (artist, era, genre, playlists),
  as it must under "don't keep" listening history (§9).

Every read is indexed and ``LIMIT``-ed; random-looking reads are ordered
by ``hashtext(id || salt)`` with the batch's salt, so they are
deterministic for a batch. Wildcards are drawn by random ids from the
id range (index lookups, no scan of the library), with a hashed-order
fallback when the ids are too sparse.

Scope (:class:`Scope`): ``person_id`` set means personal — affinity from
that person's listens, hold-back for what they heard anywhere or what
played on this target, "played together" within their own listening.
``person_id`` None means the household — every listen counts for
affinity, and hold-back and "played together" are this target's.

The online signals (phase P7) are read here from the DATABASE ONLY:
:meth:`SqlLibrary.similar` from ``artist_similarity`` and
:meth:`SqlLibrary.popularity` from ``recording_popularity`` (V056), both
filled by the core's background ``listenbrainz_fetch`` worker. Nothing in
this package makes a network call or imports an HTTP client, cache miss
included (owner rule 2026-11-14; ``test_up_next_offline.py`` enforces it):
a track or artist with nothing stored simply has no such signal, and the
mix uses the library's own.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Iterable, Mapping, Protocol, Sequence

from sqlalchemy import text

from .model import Track, TrackStats
from .session import SeedRef, parse_target

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: Per-key read limits (§9: scoring runs over at most ~1,500 candidates).
ARTIST_LIMIT = 60
ERA_LIMIT = 80
GENRE_LIMIT = 60
#: Genre keys read per seed (a track may carry 16).
GENRE_KEYS_PER_SEED = 2
WILDCARD_LIMIT = 200
TOGETHER_LIMIT = 300
#: A named list (playlist, album, artist, genre) is read up to this many tracks.
LIST_CAP = 2000
#: Affinity window, "played together" window and its pairing span (§2.2, §2.5).
AFFINITY_DAYS = 90
TOGETHER_DAYS = 180
TOGETHER_SEED_LISTENS = 200
TOGETHER_SPAN = timedelta(minutes=30)
PLAYLIST_LABEL_CHARS = 60
#: The freshness weight looks this far back for "last heard" (the default
#: listening-history keep).
FRESH_WINDOW_DAYS = 365
#: Similar artists per seed artist, and library tracks read for them.
SIMILAR_PER_ARTIST = 20
SIMILAR_TRACK_LIMIT = 120
SIMILAR_LABEL_CHARS = 60

_COLS = "id, artist_norm, year, genre_keys, favorited"


def _track(row: Mapping) -> Track:
    return Track(
        id=int(row["id"]),
        artist_norm=row["artist_norm"] or "",
        year=row["year"],
        genre_keys=tuple(row["genre_keys"] or ()),
        favorite=bool(row["favorited"]),
    )


@dataclass(frozen=True, slots=True)
class Scope:
    """Who and where: the target (``room:<id>`` / ``device:<id>``) and the
    person (None = household)."""

    target: str
    person_id: int | None = None

    @property
    def target_column(self) -> str:
        # A fixed column name per kind, never text from the caller.
        return {"room": "room_id", "device": "device_id"}[parse_target(self.target)[0]]

    @property
    def target_id(self) -> str:
        return parse_target(self.target)[1]


# ─── The interfaces ───────────────────────────────────────────────────────


class LibrarySource(Protocol):
    async def tracks(self, ids: Iterable[int]) -> dict[int, Track]: ...

    async def near(self, seeds: Sequence[Track], *, salt: str, era_years: int) -> list[Track]: ...

    async def wildcards(self, *, salt: str, rng: random.Random) -> list[Track]: ...

    async def playlist_together(
        self, seed_ids: Sequence[int], *, salt: str,
    ) -> tuple[dict[int, dict[int, str]], list[Track]]: ...

    async def list_members(self, seed: SeedRef) -> list[Track]: ...

    async def similar(self, seeds: Sequence[Track], *, salt: str = "") -> dict[int, str]: ...

    async def popularity(self, track_ids: Iterable[int]) -> dict[int, float]: ...


class ListenSource(Protocol):
    async def stats(self, scope: Scope, track_ids: Iterable[int], *, now: datetime) -> dict[int, TrackStats]: ...

    async def last_heard(self, scope: Scope, *, now: datetime, hours: float) -> dict[int, datetime]: ...

    async def together(
        self, scope: Scope, seed_ids: Sequence[int], *, now: datetime,
    ) -> dict[int, dict[int, str]]: ...


# ─── The library ──────────────────────────────────────────────────────────

# Each part picks its tracks by id and hash alone, and only those ids read
# the full rows: sorting thousands of whole rows (genre arrays included) per
# year or genre cost a third of the refill on a 100,000-track library. Same
# tracks, same order (part, then seed, then hash) as sorting the rows.
_NEAR_SQL = text(f"""
WITH picked AS (
    SELECT 1 AS part, a.o, t.h, t.id FROM unnest(CAST(:artists AS text[])) WITH ORDINALITY AS a(n, o)
    CROSS JOIN LATERAL (
        SELECT lt.id, hashtext(lt.id::text || :salt) AS h FROM library_tracks lt
        WHERE lt.artist_norm = a.n
        ORDER BY h LIMIT :artist_lim
    ) t
    UNION ALL
    SELECT 2, y.o, t.h, t.id FROM unnest(CAST(:years AS int[])) WITH ORDINALITY AS y(v, o)
    CROSS JOIN LATERAL (
        SELECT lt.id, hashtext(lt.id::text || :salt) AS h FROM library_tracks lt
        WHERE lt.year BETWEEN y.v - :span AND y.v + :span
        ORDER BY h LIMIT :era_lim
    ) t
    UNION ALL
    SELECT 3, g.o, t.h, t.id FROM unnest(CAST(:keys AS text[])) WITH ORDINALITY AS g(k, o)
    CROSS JOIN LATERAL (
        SELECT lt.id, hashtext(lt.id::text || :salt) AS h FROM library_tracks lt
        WHERE lt.genre_keys && ARRAY[g.k]
        ORDER BY h LIMIT :genre_lim
    ) t
)
SELECT {', '.join('lt.' + c for c in _COLS.split(', '))}
FROM picked p JOIN library_tracks lt ON lt.id = p.id
ORDER BY p.part, p.o, p.h
""")

_TRACKS_SQL = text(f"SELECT {_COLS} FROM library_tracks WHERE id = ANY(CAST(:ids AS int[]))")
_RANGE_SQL = text("SELECT min(id) AS lo, max(id) AS hi FROM library_tracks")
_WILD_HASH_SQL = text(
    f"SELECT {_COLS} FROM library_tracks ORDER BY hashtext(id::text || :salt) LIMIT :lim")

# Bounded by the batch's salt, so a seed in a huge playlist doesn't hand
# back the whole playlist.
_TOGETHER_PL_SQL = text(f"""
SELECT * FROM (
SELECT DISTINCT ON (s.track_id, o.track_id)
       s.track_id AS seed, p.name AS playlist, {', '.join('t.' + c for c in _COLS.split(', '))}
FROM playlist_tracks s
JOIN playlist_tracks o ON o.playlist_id = s.playlist_id AND o.track_id <> s.track_id
JOIN playlists p ON p.id = s.playlist_id
JOIN library_tracks t ON t.id = o.track_id
WHERE s.track_id = ANY(CAST(:seeds AS int[]))
ORDER BY s.track_id, o.track_id, p.id
) x ORDER BY hashtext(x.id::text || :salt), x.seed LIMIT :lim
""")

_LIST_SQL = {
    "playlist": text(f"""
        SELECT {', '.join('t.' + c for c in _COLS.split(', '))}
        FROM playlist_tracks pt JOIN library_tracks t ON t.id = pt.track_id
        WHERE pt.playlist_id = :ref ORDER BY pt.position, pt.id LIMIT :cap"""),
    "album": text(f"""
        SELECT {_COLS} FROM library_tracks
        WHERE album = :album AND (:artist = '' OR artist_norm = :artist)
        ORDER BY file_path LIMIT :cap"""),
    "artist": text(f"""
        SELECT {_COLS} FROM library_tracks WHERE artist_norm = :ref
        ORDER BY album NULLS LAST, file_path LIMIT :cap"""),
    "genre": text(f"""
        SELECT {_COLS} FROM library_tracks WHERE genre_keys && ARRAY[CAST(:ref AS text)]
        ORDER BY artist_norm, album NULLS LAST, file_path LIMIT :cap"""),
}


def playlist_label(name: str | None) -> str:
    """``from road trip``: lowercase chrome around the playlist's name."""
    n = " ".join(str(name or "").split())[:PLAYLIST_LABEL_CHARS].lower()
    return f"from {n}" if n else "played together"


class SqlLibrary:
    """:class:`LibrarySource` over the database (both processes)."""

    def __init__(self, db: "AsyncSession") -> None:
        self.db = db

    async def tracks(self, ids: Iterable[int]) -> dict[int, Track]:
        want = sorted({int(i) for i in ids})
        if not want:
            return {}
        rows = (await self.db.execute(_TRACKS_SQL, {"ids": want})).mappings().all()
        return {int(r["id"]): _track(r) for r in rows}

    async def near(self, seeds: Sequence[Track], *, salt: str, era_years: int) -> list[Track]:
        artists = sorted({s.artist_norm for s in seeds if s.artist_norm})
        years = sorted({s.year for s in seeds if s.year is not None})
        keys = sorted({k for s in seeds for k in s.genre_keys[:GENRE_KEYS_PER_SEED]})
        if not (artists or years or keys):
            return []
        rows = (await self.db.execute(_NEAR_SQL, {
            "artists": artists, "years": years, "keys": keys, "salt": salt, "span": int(era_years),
            "artist_lim": ARTIST_LIMIT, "era_lim": ERA_LIMIT, "genre_lim": GENRE_LIMIT,
        })).mappings().all()
        return list({int(r["id"]): _track(r) for r in rows}.values())

    async def wildcards(self, *, salt: str, rng: random.Random) -> list[Track]:
        rng_row = (await self.db.execute(_RANGE_SQL)).mappings().one()
        lo, hi = rng_row["lo"], rng_row["hi"]
        if lo is None:
            return []
        lo, hi = int(lo), int(hi)
        span = hi - lo + 1
        if span <= 3 * WILDCARD_LIMIT:
            ids = list(range(lo, hi + 1))
        else:
            ids = sorted({rng.randint(lo, hi) for _ in range(2 * WILDCARD_LIMIT)})
        found = await self.tracks(ids)
        if len(found) < WILDCARD_LIMIT // 2 and span > 3 * WILDCARD_LIMIT:
            # Ids too sparse (a library that was cleared and re-indexed):
            # one hashed pass instead.
            rows = (await self.db.execute(_WILD_HASH_SQL, {"salt": salt, "lim": WILDCARD_LIMIT})).mappings().all()
            return [_track(r) for r in rows]
        out = [found[i] for i in sorted(found)]
        if len(out) > WILDCARD_LIMIT:
            out = rng.sample(out, WILDCARD_LIMIT)
        return out

    async def playlist_together(
        self, seed_ids: Sequence[int], *, salt: str,
    ) -> tuple[dict[int, dict[int, str]], list[Track]]:
        seeds = sorted({int(i) for i in seed_ids})
        if not seeds:
            return {}, []
        rows = (await self.db.execute(
            _TOGETHER_PL_SQL, {"seeds": seeds, "salt": salt, "lim": TOGETHER_LIMIT})).mappings().all()
        hits: dict[int, dict[int, str]] = {}
        tracks: dict[int, Track] = {}
        for r in rows:
            tid = int(r["id"])
            hits.setdefault(int(r["seed"]), {})[tid] = playlist_label(r["playlist"])
            tracks[tid] = _track(r)
        return hits, list(tracks.values())

    async def list_members(self, seed: SeedRef) -> list[Track]:
        if seed.kind is None:
            return []
        if seed.kind == "ids":
            found = await self.tracks(seed.track_ids)
            out, seen = [], set()
            for i in seed.track_ids:
                if i in found and i not in seen:
                    seen.add(i)
                    out.append(found[i])
            return out
        if seed.kind == "album":
            artist, _, album = str(seed.ref).partition("|")
            params = {"artist": artist, "album": album, "cap": LIST_CAP}
        elif seed.kind == "playlist":
            try:
                params = {"ref": int(str(seed.ref)), "cap": LIST_CAP}
            except ValueError:
                return []
        else:
            params = {"ref": str(seed.ref), "cap": LIST_CAP}
        rows = (await self.db.execute(_LIST_SQL[seed.kind], params)).mappings().all()
        return [_track(r) for r in rows]

    async def similar(self, seeds: Sequence[Track], *, salt: str = "") -> dict[int, str]:
        """Track id -> ``similar to <artist>``: library tracks by artists
        the stored ``artist_similarity`` rows relate to the seeds' artists
        (the best :data:`SIMILAR_PER_ARTIST` each, by score). Database only;
        nothing stored means nothing here, never a network call."""
        norms = sorted({s.artist_norm for s in seeds if s.artist_norm})
        if not norms or not await _has_table(self.db, "artist_similarity"):
            return {}
        rows = (await self.db.execute(_SIMILAR_SQL, {
            "norms": norms, "per": SIMILAR_PER_ARTIST, "salt": salt, "lim": SIMILAR_TRACK_LIMIT,
        })).mappings().all()
        out: dict[int, str] = {}
        for r in rows:
            name = " ".join(str(r["seed_name"] or "").split())[:SIMILAR_LABEL_CHARS].lower()
            out.setdefault(int(r["id"]), f"similar to {name}" if name else "similar artist")
        return out

    async def popularity(self, track_ids: Iterable[int]) -> dict[int, float]:
        """Track id -> 0..1: the song's ListenBrainz listen count against
        its artist's best-known song in the library (log scale), for tracks
        whose recording MBID has a stored figure and whose artist has at
        least two such songs. Database only."""
        ids = sorted({int(i) for i in track_ids})
        if not ids or not await _has_table(self.db, "recording_popularity"):
            return {}
        rows = (await self.db.execute(_POPULARITY_SQL, {"ids": ids})).mappings().all()
        out: dict[int, float] = {}
        for r in rows:
            mx = int(r["mx"] or 0)
            if mx <= 0 or int(r["n"] or 0) < 2:
                continue
            out[int(r["id"])] = math.log1p(max(int(r["c"]), 0)) / math.log1p(mx)
        return out


_TABLES_SEEN: set[str] = set()


async def _has_table(db: "AsyncSession", name: str) -> bool:
    """Whether a P7 table exists (process-wide once seen), so the engine
    runs on a database that predates V056."""
    if name in _TABLES_SEEN:
        return True
    present = bool((await db.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name})).scalar())
    if present:
        _TABLES_SEEN.add(name)
    return present


#: The seeds' artists with a MusicBrainz id: from the enricher
#: (``library_tracks.musicbrainz_artist_id``) and from the alias fetch's
#: verified matches (``library_alias_lookups``).
_SEED_MBIDS_CTE = """
seed_mbids AS (
    SELECT DISTINCT artist_norm, mbid FROM (
        SELECT lt.artist_norm, lt.musicbrainz_artist_id AS mbid FROM library_tracks lt
        WHERE lt.artist_norm = ANY(CAST(:norms AS text[])) AND lt.musicbrainz_artist_id IS NOT NULL
        UNION ALL
        SELECT library_search_norm(l.artist_name), l.mb_artist_id FROM library_alias_lookups l
        WHERE l.status = 'matched' AND l.mb_artist_id IS NOT NULL
          AND library_search_norm(l.artist_name) = ANY(CAST(:norms AS text[]))
    ) x
)"""

_SIMILAR_SQL = text(f"""
WITH {_SEED_MBIDS_CTE},
sim AS (
    SELECT s.artist_norm AS seed_norm, a.similar_mbid, a.score FROM seed_mbids s
    CROSS JOIN LATERAL (
        SELECT similar_mbid, score FROM artist_similarity
        WHERE artist_mbid = s.mbid ORDER BY score DESC LIMIT :per
    ) a
),
hits AS (
    SELECT DISTINCT ON (t.id) t.id, sim.seed_norm FROM sim
    JOIN library_tracks t ON t.musicbrainz_artist_id = sim.similar_mbid
    WHERE t.artist_norm <> ALL(CAST(:norms AS text[]))
    ORDER BY t.id, sim.score DESC
)
SELECT h.id, h.seed_norm,
       (SELECT min(lt.artist) FROM library_tracks lt WHERE lt.artist_norm = h.seed_norm) AS seed_name
FROM hits h ORDER BY hashtext(h.id::text || :salt) LIMIT :lim
""")

_POPULARITY_SQL = text("""
WITH c AS (
    SELECT t.id, t.artist_norm, rp.listen_count AS c FROM library_tracks t
    JOIN recording_popularity rp ON rp.recording_mbid = t.musicbrainz_recording_id
    WHERE t.id = ANY(CAST(:ids AS int[])) AND rp.listen_count IS NOT NULL AND t.artist_norm <> ''
),
m AS (
    SELECT t.artist_norm, max(rp.listen_count) AS mx, count(*) AS n FROM library_tracks t
    JOIN recording_popularity rp ON rp.recording_mbid = t.musicbrainz_recording_id
    WHERE t.artist_norm IN (SELECT DISTINCT artist_norm FROM c) AND rp.listen_count IS NOT NULL
    GROUP BY t.artist_norm
)
SELECT c.id, c.c, m.mx, m.n FROM c JOIN m USING (artist_norm)
""")


# ─── Listening history ────────────────────────────────────────────────────


class NoListens:
    """No history: Up next from the library alone."""

    async def stats(self, scope: Scope, track_ids: Iterable[int], *, now: datetime) -> dict[int, TrackStats]:
        return {}

    async def last_heard(self, scope: Scope, *, now: datetime, hours: float) -> dict[int, datetime]:
        return {}

    async def together(self, scope: Scope, seed_ids: Sequence[int], *, now: datetime) -> dict[int, dict[int, str]]:
        return {}


#: Columns :class:`MusicListens` reads (the V051 shape); a test checks the
#: real table has them once it exists.
MUSIC_LISTENS_COLUMNS = frozenset({
    "track_id", "person_id", "room_id", "device_id", "started_at", "played_sec",
    "duration_sec", "outcome",
})

_HAS_TABLE_SQL = text("SELECT to_regclass('music_listens') IS NOT NULL")


def _stats_sql(personal: bool):
    who = "AND person_id = :person" if personal else ""
    return text(f"""
        SELECT track_id,
               count(*) FILTER (WHERE outcome = 'finished' AND started_at >= :since) AS finished,
               count(*) FILTER (WHERE started_at >= :since) AS plays,
               count(*) FILTER (WHERE outcome = 'skipped' AND started_at >= :since) AS skipped,
               max(started_at) AS last_at
        FROM music_listens
        WHERE track_id = ANY(CAST(:ids AS int[])) AND started_at >= :fresh_since {who}
        GROUP BY track_id""")


def _heard_sql(col: str, personal: bool):
    where = f"({col} = :tid OR person_id = :person)" if personal else f"{col} = :tid"
    return text(f"""
        SELECT track_id, max(started_at) AS at FROM music_listens
        WHERE started_at >= :since AND {where}
        GROUP BY track_id""")


def _together_sql(col: str, personal: bool):
    s_scope = "person_id = :person" if personal else f"{col} = :tid"
    o_scope = "o.person_id = :person" if personal else f"o.{col} = :tid"
    return text(f"""
        WITH s AS (
            SELECT track_id AS seed, started_at FROM music_listens
            WHERE track_id = ANY(CAST(:seeds AS int[])) AND started_at >= :since AND {s_scope}
            ORDER BY started_at DESC LIMIT :seed_lim
        )
        SELECT DISTINCT s.seed, o.track_id FROM s
        JOIN music_listens o ON {o_scope}
         AND o.started_at BETWEEN s.started_at - CAST(:span AS interval)
                              AND s.started_at + CAST(:span AS interval)
         AND o.track_id <> s.seed
        LIMIT :lim""")


class MusicListens:
    """:class:`ListenSource` over ``music_listens`` (V051). Until that
    table exists it answers like :class:`NoListens`."""

    _present: bool = False  # process-wide once seen: a table doesn't vanish

    def __init__(self, db: "AsyncSession") -> None:
        self.db = db

    async def available(self) -> bool:
        if MusicListens._present:
            return True
        present = bool((await self.db.execute(_HAS_TABLE_SQL)).scalar())
        if present:
            MusicListens._present = True
        return present

    async def stats(self, scope: Scope, track_ids: Iterable[int], *, now: datetime) -> dict[int, TrackStats]:
        ids = sorted({int(i) for i in track_ids})
        if not ids or not await self.available():
            return {}
        personal = scope.person_id is not None
        params = {"ids": ids, "since": now - timedelta(days=AFFINITY_DAYS),
                  "fresh_since": now - timedelta(days=FRESH_WINDOW_DAYS)}
        if personal:
            params["person"] = scope.person_id
        rows = (await self.db.execute(_stats_sql(personal), params)).mappings().all()
        out: dict[int, TrackStats] = {}
        for r in rows:
            last = r["last_at"]
            days = max((now - last).total_seconds() / 86400.0, 0.0) if last is not None else None
            out[int(r["track_id"])] = TrackStats(
                int(r["finished"]), int(r["plays"]), int(r["skipped"]), days)
        return out

    async def last_heard(self, scope: Scope, *, now: datetime, hours: float) -> dict[int, datetime]:
        if hours <= 0 or not await self.available():
            return {}
        personal = scope.person_id is not None
        params = {"tid": scope.target_id, "since": now - timedelta(hours=hours)}
        if personal:
            params["person"] = scope.person_id
        rows = (await self.db.execute(_heard_sql(scope.target_column, personal), params)).mappings().all()
        return {int(r["track_id"]): r["at"] for r in rows}

    async def together(self, scope: Scope, seed_ids: Sequence[int], *, now: datetime) -> dict[int, dict[int, str]]:
        seeds = sorted({int(i) for i in seed_ids})
        if not seeds or not await self.available():
            return {}
        personal = scope.person_id is not None
        params = {
            "seeds": seeds, "since": now - timedelta(days=TOGETHER_DAYS), "tid": scope.target_id,
            "seed_lim": TOGETHER_SEED_LISTENS, "span": TOGETHER_SPAN, "lim": TOGETHER_LIMIT,
        }
        if personal:
            params["person"] = scope.person_id
        rows = (await self.db.execute(_together_sql(scope.target_column, personal), params)).mappings().all()
        out: dict[int, dict[int, str]] = {}
        for r in rows:
            out.setdefault(int(r["seed"]), {})[int(r["track_id"])] = "played together"
        return out
