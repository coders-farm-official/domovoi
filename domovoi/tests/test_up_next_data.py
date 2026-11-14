"""Up next, the data part against the database (domovoi/up_next/data.py).

* :class:`SqlLibrary`: candidates near the seeds (artist, era, genre),
  wildcards (dense ids and the sparse fallback), the lists a session can
  be seeded from, playlist neighbours;
* :class:`MusicListens`: personal vs household stats, the hold-back read,
  "played together" within ±30 min, and answering "no history" while the
  table is absent;
* one engine refill end to end over SQL.

``music_listens`` arrives with phase P1 (V051). Until it is on main, the
``listens_table`` fixture creates the V051 shape inside the test's own
transaction (rolled back afterwards), so these tests always run; once
V051 is applied the real table is used, and
``test_real_music_listens_has_the_columns_read`` checks its columns.

Needs the database (``requires_db``); ``db_session`` truncates the library.
"""

from __future__ import annotations

import random
from datetime import timedelta

import pytest
from sqlalchemy import text

from domovoi.up_next import data as D
from domovoi.up_next.engine import next_batch
from domovoi.up_next.model import PLAYED_TOGETHER, Track, TrackStats
from domovoi.up_next.session import SeedRef, UpNextSession
from domovoi.tests.conftest import requires_db
from domovoi.tests.up_next_fixtures import NOW, synthetic_library

pytestmark = requires_db

# The V051 shape (phase P1, wf/upnext-p1), for a lane that doesn't have it yet.
_V051_SHAPE = """
CREATE TABLE music_listens (
    id            BIGSERIAL    PRIMARY KEY,
    track_id      INT          NOT NULL REFERENCES library_tracks(id) ON DELETE CASCADE,
    person_id     INT          REFERENCES people(id) ON DELETE CASCADE,
    room_id       TEXT,
    device_id     TEXT,
    player        TEXT         NOT NULL,
    session_id    UUID,
    origin        TEXT         NOT NULL,
    started_at    TIMESTAMPTZ  NOT NULL,
    played_sec    INT          NOT NULL,
    duration_sec  INT,
    outcome       TEXT         NOT NULL,
    recorded_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (room_id IS NOT NULL OR device_id IS NOT NULL)
)"""


async def insert_library(db, tracks, *, albums=None) -> None:
    """Insert synthetic tracks with their own ids (artist = artist_norm)."""
    await db.execute(text("""
        INSERT INTO library_tracks (id, file_path, title, artist, album, year, genre_keys, favorited)
        SELECT i, '/m/' || i || '.mp3', 't' || i, a, al, y,
               string_to_array(NULLIF(g, ''), '|'), f
        FROM unnest(CAST(:ids AS int[]), CAST(:artists AS text[]), CAST(:albums AS text[]),
                    CAST(:years AS int[]), CAST(:genres AS text[]), CAST(:favs AS bool[]))
             AS x(i, a, al, y, g, f)"""), {
        "ids": [t.id for t in tracks],
        "artists": [t.artist_norm or None for t in tracks],
        "albums": [(albums or {}).get(t.id) for t in tracks],
        "years": [t.year for t in tracks],
        "genres": ["|".join(t.genre_keys) for t in tracks],
        "favs": [t.favorite for t in tracks],
    })
    await db.execute(text(
        "SELECT setval('library_tracks_id_seq', (SELECT max(id) FROM library_tracks))"))


async def add_playlist(db, pid: int, name: str, ids) -> None:
    await db.execute(text("INSERT INTO playlists (id, name) VALUES (:id, :n)"), {"id": pid, "n": name})
    for pos, tid in enumerate(ids):
        await db.execute(text(
            "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (:p, :t, :pos)"),
            {"p": pid, "t": tid, "pos": pos})


@pytest.fixture
async def listens_table(db_session, monkeypatch):
    present = (await db_session.execute(text("SELECT to_regclass('music_listens') IS NOT NULL"))).scalar()
    if not present:
        await db_session.execute(text(_V051_SHAPE))
    monkeypatch.setattr(D.MusicListens, "_present", False)
    yield db_session
    D.MusicListens._present = False


async def add_listen(db, track_id, *, at, person=None, room=None, device=None, outcome="finished",
                     played=200, duration=200):
    await db.execute(text("""
        INSERT INTO music_listens (track_id, person_id, room_id, device_id, player, origin,
                                   started_at, played_sec, duration_sec, outcome)
        VALUES (:t, :p, :r, :d, :pl, 'explicit', :at, :ps, :ds, :o)"""), {
        "t": track_id, "p": person, "r": room, "d": device, "pl": "room" if room else "browser",
        "at": at, "ps": played, "ds": duration, "o": outcome})


async def add_people(db, *names) -> list[int]:
    out = []
    for n in names:
        out.append((await db.execute(text("INSERT INTO people (name) VALUES (:n) RETURNING id"), {"n": n})).scalar())
    return out


# ─── SqlLibrary ───────────────────────────────────────────────────────────


async def test_near_finds_artist_era_and_genre(db_session):
    lib = synthetic_library()
    await insert_library(db_session, lib)
    src = D.SqlLibrary(db_session)
    seed = (await src.tracks([1]))[1]
    assert seed == lib[0]
    near = await src.near([seed], salt="ab", era_years=5)
    ids = {t.id for t in near}
    same_artist = {t.id for t in lib if t.artist_norm == seed.artist_norm}
    assert same_artist <= ids  # 8 tracks, under the limit
    assert any(t.year is not None and abs(t.year - seed.year) <= 5 and t.artist_norm != seed.artist_norm
               for t in near)
    assert any(set(t.genre_keys) & set(seed.genre_keys) and t.artist_norm != seed.artist_norm for t in near)
    for t in near:
        assert (t.artist_norm == seed.artist_norm
                or (t.year is not None and abs(t.year - seed.year) <= 5)
                or set(t.genre_keys) & set(seed.genre_keys[:D.GENRE_KEYS_PER_SEED]))
    assert near == await src.near([seed], salt="ab", era_years=5)  # same salt, same rows
    assert len(near) <= D.ARTIST_LIMIT + D.ERA_LIMIT + 2 * D.GENRE_LIMIT
    assert await src.near([Track(999)], salt="ab", era_years=5) == []


async def test_wildcards_dense_and_sparse(db_session):
    lib = synthetic_library(n=1000, artists=40)
    await insert_library(db_session, lib)
    src = D.SqlLibrary(db_session)
    w1 = await src.wildcards(salt="x", rng=random.Random(1))
    w2 = await src.wildcards(salt="x", rng=random.Random(1))
    assert w1 == w2 and 100 <= len(w1) <= D.WILDCARD_LIMIT
    assert len({t.id for t in w1}) == len(w1)
    # very sparse ids: the hashed fallback still answers
    await db_session.execute(text("DELETE FROM library_tracks WHERE id BETWEEN 30 AND 990"))
    w3 = await src.wildcards(salt="x", rng=random.Random(1))
    assert {t.id for t in w3} == {t.id for t in lib if t.id < 30 or t.id > 990}


async def test_wildcards_empty_library(db_session):
    assert await D.SqlLibrary(db_session).wildcards(salt="x", rng=random.Random(1)) == []


async def test_list_members(db_session):
    lib = synthetic_library(n=60)
    await insert_library(db_session, lib, albums={i: "pastel blues" for i in (1, 41, 21)})
    await add_playlist(db_session, 4, "Road Trip", [30, 10, 20])
    src = D.SqlLibrary(db_session)
    ids = lambda ts: [t.id for t in ts]  # noqa: E731
    assert ids(await src.list_members(SeedRef(10, kind="playlist", ref="4"))) == [30, 10, 20]
    assert ids(await src.list_members(SeedRef(1, kind="album", ref="artist 00|pastel blues"))) == [1, 41]
    assert ids(await src.list_members(SeedRef(1, kind="album", ref="|pastel blues"))) == [1, 21, 41]
    assert set(ids(await src.list_members(SeedRef(1, kind="artist", ref="artist 00")))) == {1, 41}
    assert ids(await src.list_members(SeedRef(5, kind="ids", track_ids=(5, 999, 3, 5)))) == [5, 3]
    soul = await src.list_members(SeedRef(2, kind="genre", ref="soul"))
    assert soul and all("soul" in t.genre_keys for t in soul)
    assert await src.list_members(SeedRef(1, kind="playlist", ref="nope")) == []
    assert await src.list_members(SeedRef(1)) == []


async def test_playlist_together(db_session):
    await insert_library(db_session, synthetic_library(n=60))
    await add_playlist(db_session, 4, "Road  Trip", [1, 2, 3])
    await add_playlist(db_session, 5, "Late", [3, 50])
    hits, tracks = await D.SqlLibrary(db_session).playlist_together([1, 3], salt="s")
    assert hits == {1: {2: "from road trip", 3: "from road trip"},
                    3: {1: "from road trip", 2: "from road trip", 50: "from late"}}
    assert {t.id for t in tracks} == {1, 2, 3, 50}
    assert await D.SqlLibrary(db_session).similar([Track(1)]) == {}  # nothing stored: no signal


# ─── MusicListens ─────────────────────────────────────────────────────────


async def test_no_table_means_no_history(db_session, monkeypatch):
    monkeypatch.setattr(D, "_HAS_TABLE_SQL", text("SELECT to_regclass('music_listens_not_here') IS NOT NULL"))
    monkeypatch.setattr(D.MusicListens, "_present", False)
    src = D.MusicListens(db_session)
    scope = D.Scope("room:kitchen", 3)
    assert not await src.available()
    assert await src.stats(scope, [1, 2], now=NOW) == {}
    assert await src.last_heard(scope, now=NOW, hours=4) == {}
    assert await src.together(scope, [1], now=NOW) == {}


async def test_real_music_listens_has_the_columns_read(db_session):
    present = (await db_session.execute(text("SELECT to_regclass('music_listens') IS NOT NULL"))).scalar()
    if not present:
        pytest.skip("music_listens (V051, phase P1) is not on this lane yet; listens_table stands in")
    cols = {r[0] for r in (await db_session.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'music_listens'"))).all()}
    assert D.MUSIC_LISTENS_COLUMNS <= cols


async def test_stats_personal_and_household(listens_table):
    db = listens_table
    await insert_library(db, synthetic_library(n=20))
    alex, sam = await add_people(db, "Alex", "Sam")
    for k in range(3):
        await add_listen(db, 1, at=NOW - timedelta(days=k + 1), person=alex, room="kitchen")
    await add_listen(db, 1, at=NOW - timedelta(days=2), person=sam, device="ph", outcome="skipped", played=5)
    await add_listen(db, 1, at=NOW - timedelta(days=200), person=alex, room="kitchen")  # outside 90 d
    await add_listen(db, 2, at=NOW - timedelta(days=1), device="ph", outcome="skipped", played=5)
    src = D.MusicListens(db)
    # last_days: days since the same people last heard it, within a year
    # (the freshness weight); the 200-day-old listen counts there only.
    assert await src.stats(D.Scope("room:kitchen", alex), [1, 2], now=NOW) == {1: TrackStats(3, 3, 0, 1.0)}
    assert await src.stats(D.Scope("device:ph", sam), [1, 2], now=NOW) == {1: TrackStats(0, 1, 1, 2.0)}
    house = await src.stats(D.Scope("room:kitchen", None), [1, 2], now=NOW)
    assert house == {1: TrackStats(3, 4, 1, 1.0), 2: TrackStats(0, 1, 1, 1.0)}
    old_only = await src.stats(D.Scope("room:kitchen", alex), [1], now=NOW + timedelta(days=150))
    assert old_only == {1: TrackStats(0, 0, 0, 151.0)}


async def test_last_heard_scope(listens_table):
    db = listens_table
    await insert_library(db, synthetic_library(n=20))
    alex, sam = await add_people(db, "Alex", "Sam")
    await add_listen(db, 1, at=NOW - timedelta(hours=1), person=alex, device="ph")    # alex elsewhere
    await add_listen(db, 2, at=NOW - timedelta(hours=2), person=sam, room="kitchen")  # this room
    await add_listen(db, 3, at=NOW - timedelta(hours=2), person=sam, room="office")   # neither
    await add_listen(db, 4, at=NOW - timedelta(hours=5), person=alex, room="kitchen")  # too long ago
    src = D.MusicListens(db)
    assert set(await src.last_heard(D.Scope("room:kitchen", alex), now=NOW, hours=4)) == {1, 2}
    assert set(await src.last_heard(D.Scope("room:kitchen", None), now=NOW, hours=4)) == {2}
    assert set(await src.last_heard(D.Scope("device:ph", None), now=NOW, hours=4)) == {1}
    assert await src.last_heard(D.Scope("room:kitchen", None), now=NOW, hours=0) == {}


async def test_played_together_within_half_an_hour(listens_table):
    db = listens_table
    await insert_library(db, synthetic_library(n=20))
    alex, sam = await add_people(db, "Alex", "Sam")
    t0 = NOW - timedelta(days=3)
    await add_listen(db, 1, at=t0, person=alex, room="kitchen")
    await add_listen(db, 2, at=t0 + timedelta(minutes=4), person=alex, device="ph")
    await add_listen(db, 3, at=t0 + timedelta(minutes=25), person=alex, room="kitchen")
    await add_listen(db, 4, at=t0 + timedelta(minutes=50), person=alex, room="kitchen")  # too far
    await add_listen(db, 5, at=t0 + timedelta(minutes=10), person=sam, room="kitchen")   # someone else
    src = D.MusicListens(db)
    assert await src.together(D.Scope("room:kitchen", alex), [1], now=NOW) == {
        1: {2: "played together", 3: "played together"}}
    assert await src.together(D.Scope("room:kitchen", None), [1], now=NOW) == {
        1: {3: "played together", 5: "played together"}}
    assert await src.together(D.Scope("room:kitchen", alex), [1], now=NOW + timedelta(days=200)) == {}


# ─── One refill over SQL ──────────────────────────────────────────────────


async def test_engine_refill_over_sql(listens_table):
    db = listens_table
    lib = synthetic_library()
    await insert_library(db, lib)
    await add_playlist(db, 4, "Road Trip", [1, 250, 251, 252, 253, 254])
    (alex,) = await add_people(db, "Alex")
    await add_listen(db, 2, at=NOW - timedelta(hours=1), person=alex, room="kitchen")
    library, listens = D.SqlLibrary(db), D.MusicListens(db)

    async def one():
        s = UpNextSession.start(target="room:kitchen", mode="library", seed=SeedRef(1), person_id=alex,
                                rng_seed=1234)
        return s, await next_batch(s, library=library, listens=listens, now=NOW)

    s, b = await one()
    _s2, b2 = await one()
    assert b == b2 and len(b.picks) == 30 and s.batch_no == 1
    ids = [p.track_id for p in b.picks]
    assert len(set(ids)) == 30 and 1 not in ids and 2 not in ids  # the seed; heard an hour ago
    assert any(p.reason_code == PLAYED_TOGETHER and p.reason_label == "from road trip" for p in b.picks)
    nxt = await next_batch(s, library=library, listens=listens, now=NOW)
    assert not set(ids) & {p.track_id for p in nxt.picks}

    lst = UpNextSession.start(target="device:ph", mode="list", rng_seed=5,
                              seed=SeedRef(250, kind="playlist", ref="4"), autoplay=False)
    lb = await next_batch(lst, library=library, listens=listens, now=NOW)
    assert sorted(p.track_id for p in lb.picks) == [1, 251, 252, 253, 254] and lb.ended
