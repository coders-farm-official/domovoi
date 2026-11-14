"""ListenBrainz for Up next (phase P7): similar artists and song popularity.

The ONLY code that asks ListenBrainz anything. It stores the answers
(``artist_similarity``, ``artist_similarity_lookups``,
``recording_popularity``; V056) and the Up next picker reads them from the
database (:mod:`domovoi.up_next.data`). Building or extending a queue never
calls the network, cache miss included (owner rule 2026-11-14): a song or
artist with nothing stored simply has no such signal.

**Two switches, each its own.** ``music_similar_artists_enabled`` (default
follows the internet answer: on for Yes) and ``music_popularity_enabled``
(on for Yes and Sometimes, like the other small catalogue lookups:
MusicBrainz aliases, LRCLIB). Self-gated: the switches apply on the next
tick without a restart. A switch turned off stops that job before its next
request and deletes what it stored (once, when the worker sees it off), so
the mix is the library's own again.

**The gate** (:func:`_egress_allowed`), checked at the start of every tick
and again before every request: the internet answer is not "No"
(``egress.internet_allowed``), the connectivity probe says online, and no
shutdown is under way. The client also passes every URL through
``egress.require_destination``.

**Polite.** The client sends one request at a time, a second apart,
process-wide, with a User-Agent naming the project (no person, no house).
At most :data:`SIMILAR_PER_TICK` artist lookups and
:data:`POPULARITY_REQUESTS_PER_TICK` popularity requests (100 songs each)
per tick, a tick every ``listenbrainz_fetch_interval_sec`` (60 s). A 429 or
503 pauses the whole fetch for what ListenBrainz asks (or a minute); an
unreachable server pauses it for :data:`UNAVAILABLE_PAUSE_SEC`.

**What it costs.** A 6,000-song library with ~600 artists that carry a
MusicBrainz id and ~4,000 songs with a recording id: ~600 similar-artist
requests and ~40 popularity requests the first time (about 15 minutes of
requests spread over ~30 minutes of ticks), then each artist again after
:data:`SIMILAR_REFRESH_DAYS` and each song after
:data:`POPULARITY_REFRESH_DAYS` — about 8 requests a day — plus new
artists and songs as they arrive.

**What leaves the house:** MusicBrainz artist ids (similar artists) and
recording ids (popularity). No titles, no names, no listening history.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, Sequence

from sqlalchemy import text

from domovoi import egress, lifecycle
from domovoi.clients.listenbrainz import (
    POPULARITY_BATCH,
    ListenBrainzClient,
    ListenBrainzRateLimited,
    ListenBrainzUnavailable,
    get_listenbrainz_client,
)
from domovoi.config import settings
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

SIMILAR_PER_TICK = 20
POPULARITY_REQUESTS_PER_TICK = 3
SIMILAR_REFRESH_DAYS = 90
POPULARITY_REFRESH_DAYS = 30
#: Similar artists kept per artist (ListenBrainz sends up to 100).
SIMILAR_KEEP = 100
UNAVAILABLE_PAUSE_SEC = 300.0
#: An artist whose lookup failed this many times in a row waits for the
#: refresh like a found one (instead of 2^attempts hours).
MAX_ERROR_ATTEMPTS = 5


def _probe_online() -> bool:
    from domovoi.connectivity import current_probe

    probe = current_probe()
    return probe is not None and bool(probe.online)


def _egress_allowed() -> bool:
    """THE gate for every request: internet allowed, probe online, no
    shutdown. (Each job also checks its own switch.)"""
    if lifecycle.shutting_down():
        return False
    if not egress.internet_allowed():
        return False
    return _probe_online()


# ─── Status (in-memory; the core snapshot's "listenbrainz_fetch") ─────────


@dataclass
class _Status:
    state: str = "idle"
    last_tick_at: float | None = None
    last_request_at: float | None = None
    last_error: str | None = None
    paused_until: float | None = None
    requests: int = 0


_STATUS = _Status()


def listenbrainz_status() -> dict[str, Any]:
    def iso(ts: float | None) -> str | None:
        return None if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    return {
        "similar_artists_enabled": bool(settings.music_similar_artists_enabled),
        "popularity_enabled": bool(settings.music_popularity_enabled),
        "state": _STATUS.state,
        "last_tick_at": iso(_STATUS.last_tick_at),
        "last_request_at": iso(_STATUS.last_request_at),
        "last_error": _STATUS.last_error,
        "paused_until": iso(_STATUS.paused_until),
        "requests": _STATUS.requests,
    }


def reset_status_for_tests() -> None:
    global _STATUS
    _STATUS = _Status()


# ─── The store ────────────────────────────────────────────────────────────


class FetchStore(Protocol):
    async def clear_similar(self) -> None: ...

    async def clear_popularity(self) -> None: ...

    async def due_artists(self, *, now: datetime, limit: int) -> list[str]: ...

    async def save_similar(self, artist_mbid: str, rows: Sequence[tuple[str, float]], *, now: datetime) -> None: ...

    async def similar_error(self, artist_mbid: str, *, now: datetime) -> None: ...

    async def due_recordings(self, *, now: datetime, limit: int) -> list[str]: ...

    async def save_popularity(self, counts: dict[str, tuple[int | None, int | None]], *, now: datetime) -> None: ...


#: The library's artists with a MusicBrainz id (the enricher's, and the
#: alias fetch's verified matches).
_LIBRARY_ARTIST_MBIDS = """
    SELECT DISTINCT musicbrainz_artist_id AS mbid FROM library_tracks
    WHERE musicbrainz_artist_id IS NOT NULL AND length(musicbrainz_artist_id) = 36
    UNION
    SELECT DISTINCT mb_artist_id FROM library_alias_lookups
    WHERE status = 'matched' AND mb_artist_id IS NOT NULL AND length(mb_artist_id) = 36
"""

_DUE_ARTISTS_SQL = text(f"""
SELECT a.mbid FROM ({_LIBRARY_ARTIST_MBIDS}) a
LEFT JOIN artist_similarity_lookups l ON l.artist_mbid = a.mbid
WHERE l.artist_mbid IS NULL
   OR (l.outcome <> 'error' AND l.fetched_at < CAST(:refresh_before AS timestamptz))
   OR (l.outcome = 'error' AND l.attempts < :max_attempts
       AND l.fetched_at + power(2, l.attempts) * interval '1 hour' < CAST(:now AS timestamptz))
   OR (l.outcome = 'error' AND l.fetched_at < CAST(:refresh_before AS timestamptz))
ORDER BY l.fetched_at NULLS FIRST, a.mbid
LIMIT :lim
""")

_DUE_RECORDINGS_SQL = text("""
SELECT DISTINCT t.musicbrainz_recording_id AS mbid, rp.fetched_at
FROM library_tracks t
LEFT JOIN recording_popularity rp ON rp.recording_mbid = t.musicbrainz_recording_id
WHERE t.musicbrainz_recording_id IS NOT NULL AND length(t.musicbrainz_recording_id) = 36
  AND (rp.recording_mbid IS NULL OR rp.fetched_at < :refresh_before)
ORDER BY rp.fetched_at NULLS FIRST, t.musicbrainz_recording_id
LIMIT :lim
""")


class DbFetchStore:
    async def clear_similar(self) -> None:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            await s.execute(text("DELETE FROM artist_similarity"))
            await s.execute(text("DELETE FROM artist_similarity_lookups"))

    async def clear_popularity(self) -> None:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            await s.execute(text("DELETE FROM recording_popularity"))

    async def due_artists(self, *, now: datetime, limit: int) -> list[str]:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            rows = (await s.execute(_DUE_ARTISTS_SQL, {
                "now": now, "refresh_before": now - timedelta(days=SIMILAR_REFRESH_DAYS),
                "max_attempts": MAX_ERROR_ATTEMPTS, "lim": limit,
            })).all()
        return [str(r[0]) for r in rows]

    async def save_similar(self, artist_mbid: str, rows: Sequence[tuple[str, float]], *, now: datetime) -> None:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            await s.execute(text("DELETE FROM artist_similarity WHERE artist_mbid = :a"), {"a": artist_mbid})
            for mbid, score in list(rows)[:SIMILAR_KEEP]:
                await s.execute(text(
                    "INSERT INTO artist_similarity (artist_mbid, similar_mbid, score, source, fetched_at) "
                    "VALUES (:a, :b, :s, 'listenbrainz', :t) ON CONFLICT DO NOTHING"),
                    {"a": artist_mbid, "b": mbid, "s": float(score), "t": now})
            await s.execute(text(
                "INSERT INTO artist_similarity_lookups (artist_mbid, fetched_at, outcome, attempts) "
                "VALUES (:a, :t, :o, 0) ON CONFLICT (artist_mbid) DO UPDATE "
                "SET fetched_at = EXCLUDED.fetched_at, outcome = EXCLUDED.outcome, attempts = 0"),
                {"a": artist_mbid, "t": now, "o": "ok" if rows else "none"})

    async def similar_error(self, artist_mbid: str, *, now: datetime) -> None:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            await s.execute(text(
                "INSERT INTO artist_similarity_lookups (artist_mbid, fetched_at, outcome, attempts) "
                "VALUES (:a, :t, 'error', 1) ON CONFLICT (artist_mbid) DO UPDATE "
                "SET fetched_at = EXCLUDED.fetched_at, outcome = 'error', "
                "attempts = CASE WHEN artist_similarity_lookups.outcome = 'error' "
                "THEN artist_similarity_lookups.attempts + 1 ELSE 1 END"),
                {"a": artist_mbid, "t": now})

    async def due_recordings(self, *, now: datetime, limit: int) -> list[str]:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            rows = (await s.execute(_DUE_RECORDINGS_SQL, {
                "refresh_before": now - timedelta(days=POPULARITY_REFRESH_DAYS), "lim": limit,
            })).all()
        return [str(r[0]) for r in rows]

    async def save_popularity(self, counts: dict[str, tuple[int | None, int | None]], *, now: datetime) -> None:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            for mbid, (lc, uc) in counts.items():
                await s.execute(text(
                    "INSERT INTO recording_popularity (recording_mbid, listen_count, user_count, fetched_at) "
                    "VALUES (:m, :l, :u, :t) ON CONFLICT (recording_mbid) DO UPDATE "
                    "SET listen_count = EXCLUDED.listen_count, user_count = EXCLUDED.user_count, "
                    "fetched_at = EXCLUDED.fetched_at"),
                    {"m": mbid, "l": lc, "u": uc, "t": now})


# ─── The worker ───────────────────────────────────────────────────────────


class ListenBrainzFetch(Worker):
    """See the module docstring."""

    name = "listenbrainz_fetch"
    enabled_setting = None  # self-gated: both switches apply live
    interval_setting = "listenbrainz_fetch_interval_sec"
    stub_suppressed = True
    # Not requires_online: a switch turned off is cleared up offline too.
    requires_online = False

    def __init__(self, *, client: ListenBrainzClient | None = None,
                 store: FetchStore | None = None) -> None:
        self._client = client
        self._store: FetchStore = store or DbFetchStore()
        # What each switch was at the last tick; None = not seen yet (the
        # first tick clears a switch that is off, in case it was turned
        # off while the server was down).
        self._seen: dict[str, bool | None] = {"similar": None, "popularity": None}

    def _lb(self) -> ListenBrainzClient:
        return self._client or get_listenbrainz_client()

    async def _clear_switched_off(self, sim_on: bool, pop_on: bool) -> None:
        for job, on, clear in (("similar", sim_on, self._store.clear_similar),
                               ("popularity", pop_on, self._store.clear_popularity)):
            if not on and self._seen[job] is not False:
                await clear()
                log.info("ListenBrainz %s is off: what it fetched was deleted", job)
            self._seen[job] = on

    async def tick(self) -> dict[str, int]:
        result = {"similar": 0, "popularity": 0}
        sim_on = bool(settings.music_similar_artists_enabled)
        pop_on = bool(settings.music_popularity_enabled)
        await self._clear_switched_off(sim_on, pop_on)
        if not (sim_on or pop_on):
            _STATUS.state = "off"
            return result
        if not _egress_allowed():
            _STATUS.state = "offline"
            return result
        now_ts = time.time()
        if _STATUS.paused_until is not None and now_ts < _STATUS.paused_until:
            _STATUS.state = "paused"
            return result
        _STATUS.last_tick_at = now_ts
        _STATUS.state = "running"
        try:
            if sim_on and not await self._similar(result):
                return result
            if pop_on:
                await self._popularity(result)
        except Exception as e:
            _STATUS.state = "error"
            _STATUS.last_error = type(e).__name__[:64]
            raise
        if _STATUS.state == "running":
            _STATUS.state = "idle" if not any(result.values()) else "running"
        return result

    def _pause(self, e: Exception) -> None:
        if isinstance(e, ListenBrainzRateLimited):
            _STATUS.paused_until = time.time() + e.retry_after
            _STATUS.last_error = "rate_limited"
            _STATUS.state = "paused"
            log.info("ListenBrainz asked us to slow down; pausing %.0f s", e.retry_after)
        else:
            _STATUS.paused_until = time.time() + UNAVAILABLE_PAUSE_SEC
            _STATUS.last_error = str(e)[:64] or type(e).__name__
            _STATUS.state = "paused"
            log.info("ListenBrainz unavailable (%s); pausing %.0f s", e, UNAVAILABLE_PAUSE_SEC)

    async def _similar(self, result: dict[str, int]) -> bool:
        """One tick of similar artists. False when it stopped the tick
        (paused, switched off, offline)."""
        now = datetime.now(timezone.utc)
        for mbid in await self._store.due_artists(now=now, limit=SIMILAR_PER_TICK):
            if not settings.music_similar_artists_enabled or not _egress_allowed():
                return False
            _STATUS.last_request_at = time.time()
            _STATUS.requests += 1
            try:
                rows = await self._lb().similar_artists(mbid)
            except ListenBrainzRateLimited as e:
                self._pause(e)
                return False
            except ListenBrainzUnavailable as e:
                await self._store.similar_error(mbid, now=now)
                self._pause(e)
                return False
            await self._store.save_similar(mbid, rows, now=now)
            result["similar"] += 1
        return True

    async def _popularity(self, result: dict[str, int]) -> None:
        now = datetime.now(timezone.utc)
        due = await self._store.due_recordings(
            now=now, limit=POPULARITY_BATCH * POPULARITY_REQUESTS_PER_TICK)
        for i in range(0, len(due), POPULARITY_BATCH):
            if not settings.music_popularity_enabled or not _egress_allowed():
                return
            chunk = due[i:i + POPULARITY_BATCH]
            _STATUS.last_request_at = time.time()
            _STATUS.requests += 1
            try:
                counts = await self._lb().recording_popularity(chunk)
            except (ListenBrainzRateLimited, ListenBrainzUnavailable) as e:
                self._pause(e)
                return
            await self._store.save_popularity(counts, now=now)
            result["popularity"] += len(counts)


__all__ = [
    "DbFetchStore", "FetchStore", "ListenBrainzFetch", "listenbrainz_status",
    "reset_status_for_tests",
]
