"""Up next refills stay fast on a big library (UP-NEXT-QUEUE-PLAN §9).

A generated library of 100,000 tracks (5,000 artists, years 1950-2025 on
nine tracks in ten, 30 genres on most), 60 playlists and 20,000 listens
over 90 days; a library-mode refill (every read plus the picking) must
build in under 150 ms, median of several sessions. A 6,000-track library
is timed too. Marked ``slow``: generating the library takes a few seconds.

The library is committed and vacuumed before timing, as a real one is
(rows still in an open transaction sit in the GIN index's pending list and
make the genre read several times slower than it ever is in a house), and
removed afterwards; a ``music_listens`` this test had to create (V051 not
on the lane yet) is dropped again.

Needs the database (``requires_db``). Timings print with ``-s``.
"""

from __future__ import annotations

import statistics
import time

import pytest
from sqlalchemy import text

from domovoi.up_next import data as D
from domovoi.up_next.engine import next_batch
from domovoi.up_next.session import SeedRef, UpNextSession
from domovoi.db.session import engine
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_up_next_data import _V051_SHAPE
from domovoi.tests.up_next_fixtures import NOW

pytestmark = [requires_db, pytest.mark.slow]

REFILL_BUDGET_MS = 150.0

_GENRES = ("rock pop soul jazz folk blues country metal punk reggae hip_hop r&b electronic house "
           "techno ambient classical opera gospel funk disco latin indie alternative_rock grunge "
           "new_wave synthpop trip_hop drum_and_bass soundtrack").split()


async def _generate(db, n: int) -> None:
    artists = max(1, n // 20)
    await db.execute(text("""
        INSERT INTO library_tracks (id, file_path, title, artist, album, year, genre_keys, favorited)
        SELECT i, '/music/' || i || '.flac', 'title ' || i,
               'Artist ' || (i % :artists), 'Album ' || (i % :artists) || '-' || (i % 3),
               CASE WHEN i % 10 = 0 THEN NULL ELSE 1950 + ((i % :artists) * 7 + i % 5) % 76 END,
               CASE WHEN i % 7 = 0 THEN NULL
                    ELSE ARRAY[replace((CAST(:genres AS text[]))[1 + (i % :artists) % 30], '_', ' ')]
                         || CASE WHEN i % 3 = 0
                                 THEN ARRAY[replace((CAST(:genres AS text[]))[1 + ((i % :artists) + 7) % 30], '_', ' ')]
                                 ELSE ARRAY[]::text[] END
               END,
               i % 50 = 0
        FROM generate_series(1, :n) AS i"""), {"n": n, "artists": artists, "genres": list(_GENRES)})
    await db.execute(text(
        "INSERT INTO playlists (id, name) SELECT p, 'playlist ' || p FROM generate_series(1, 60) p"))
    await db.execute(text("""
        INSERT INTO playlist_tracks (playlist_id, track_id, position)
        SELECT p, 1 + ((p * 7919 + k * 104729) % :n), k
        FROM generate_series(1, 60) p, generate_series(1, 100) k
        ON CONFLICT DO NOTHING"""), {"n": n})
    await db.execute(text("INSERT INTO people (id, name) VALUES (901, 'Alex'), (902, 'Sam')"))
    await db.execute(text("""
        INSERT INTO music_listens (track_id, person_id, room_id, device_id, player, origin,
                                   started_at, played_sec, duration_sec, outcome)
        SELECT 1 + ((k * 7919) % :n), CASE WHEN k % 3 = 0 THEN NULL ELSE 901 + k % 2 END,
               CASE WHEN k % 2 = 0 THEN 'kitchen' END, CASE WHEN k % 2 = 1 THEN 'phone' END,
               CASE WHEN k % 2 = 0 THEN 'room' ELSE 'browser' END, 'explicit',
               CAST(:now AS timestamptz) - (k * interval '6 minutes'), 200, 200,
               CASE WHEN k % 5 = 0 THEN 'skipped' ELSE 'finished' END
        FROM generate_series(1, 20000) k"""), {"n": n, "now": NOW})
    await db.commit()
    async with engine.connect() as c:
        c = await c.execution_options(isolation_level="AUTOCOMMIT")
        for table in ("library_tracks", "playlists", "playlist_tracks", "music_listens"):
            await c.execute(text(f"VACUUM ANALYZE {table}"))


async def _time_refills(db, n: int, *, runs: int = 9) -> dict[str, float]:
    library, listens = D.SqlLibrary(db), D.MusicListens(db)
    lib_ms, list_ms = [], []
    for r in range(runs):
        seed = 1 + (r * 7717) % n
        s = UpNextSession.start(target="room:kitchen", mode="library", seed=SeedRef(seed),
                                person_id=901 if r % 2 else None, rng_seed=r)
        s.recent_listened = [1 + (seed * (k + 3)) % n for k in range(5)]
        t0 = time.perf_counter()
        b = await next_batch(s, library=library, listens=listens, now=NOW)
        lib_ms.append((time.perf_counter() - t0) * 1000)
        assert len(b.picks) == 30
        ls = UpNextSession.start(target="device:phone", mode="list", rng_seed=r,
                                 seed=SeedRef(seed, kind="artist", ref=f"artist {seed % max(1, n // 20)}"))
        t0 = time.perf_counter()
        await next_batch(ls, library=library, listens=listens, now=NOW)
        list_ms.append((time.perf_counter() - t0) * 1000)
    return {"library_median": statistics.median(lib_ms[1:]), "library_max": max(lib_ms[1:]),
            "list_median": statistics.median(list_ms[1:]), "first": lib_ms[0]}


@pytest.fixture
async def big_db(db_session, monkeypatch):
    created = not (await db_session.execute(text("SELECT to_regclass('music_listens') IS NOT NULL"))).scalar()
    if created:
        await db_session.execute(text(_V051_SHAPE))
    monkeypatch.setattr(D.MusicListens, "_present", False)
    try:
        yield db_session
    finally:
        await db_session.rollback()
        await db_session.execute(text(
            "TRUNCATE library_tracks, playlists, playlist_tracks, people RESTART IDENTITY CASCADE"))
        if created:
            await db_session.execute(text("DROP TABLE IF EXISTS music_listens"))
        else:
            await db_session.execute(text("TRUNCATE music_listens"))
        await db_session.commit()
        D.MusicListens._present = False


@pytest.mark.parametrize("n", [6000, 100_000])
async def test_refill_is_fast(big_db, n):
    db = big_db
    t0 = time.perf_counter()
    await _generate(db, n)
    gen = time.perf_counter() - t0
    t = await _time_refills(db, n)
    print(f"\nup next refill at {n} tracks: library median {t['library_median']:.1f} ms, "
          f"max {t['library_max']:.1f} ms, first {t['first']:.1f} ms; "
          f"list (artist) median {t['list_median']:.1f} ms; library generated in {gen:.1f} s")
    assert t["library_median"] < REFILL_BUDGET_MS, t
