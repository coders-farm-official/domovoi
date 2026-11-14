"""ListenBrainz for Up next (phase P7): the client's parsing, the fetch
worker's gates and pacing, what it stores, and the picker reading it back.

* domovoi/clients/listenbrainz.py — parse_similar / parse_popularity, the
  pinned dataset name, the User-Agent (no person, no house);
* domovoi/workers/listenbrainz_fetch.py — two switches, each its own; the
  gate (internet answer, probe, shutdown) before every request; a switch
  turned off deletes what it stored; rate limits pause; batches per tick;
* domovoi/up_next/data.py — SqlLibrary.similar / .popularity read the
  stored rows (DB tests).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from domovoi import connectivity, egress, lifecycle
from domovoi.clients import listenbrainz as lbc
from domovoi.config import settings
from domovoi.tests.conftest import requires_db
from domovoi.workers import listenbrainz_fetch as lbf

A1 = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
A2 = "5b11f4ce-a62d-471e-81fc-a69a8278c7da"
A3 = "9c9f1380-2516-4fc9-a3e6-f9f61941d090"
R1 = "9e2ad5bc-c6f9-40d2-a36f-3122ee2072a3"


# ─── The client's parsing ─────────────────────────────────────────────────


def test_parse_similar_best_first_without_itself_or_junk():
    data = [
        {"artist_mbid": A2, "score": 11156, "name": "x"},
        {"artist_mbid": A1, "score": 99999},          # the artist itself
        {"artist_mbid": A3, "score": 12000},
        {"artist_mbid": "short", "score": 1},
        {"artist_mbid": A2, "score": True},            # a bool is not a score
        "nonsense",
    ]
    assert lbc.parse_similar(data, A1) == [(A3, 12000.0), (A2, 11156.0)]
    assert lbc.parse_similar({"error": 1}, A1) == []


def test_parse_popularity_answers_every_id_asked():
    data = [{"recording_mbid": R1, "total_listen_count": 3900960, "total_user_count": 284929},
            {"recording_mbid": "not-asked", "total_listen_count": 5},
            {"recording_mbid": A2, "total_listen_count": -1, "total_user_count": None}]
    assert lbc.parse_popularity(data, [R1, A2, A3]) == {
        R1: (3900960, 284929), A2: (None, None), A3: (None, None)}


def test_the_dataset_name_is_pinned_and_the_user_agent_names_no_one():
    assert lbc.SIMILAR_ALGORITHM.startswith("session_based_days_")
    ua = lbc.user_agent()
    assert ua.startswith("domovoi/") and "github.com/coders-farm-official/domovoi" in ua
    assert "@" not in ua


def test_rate_limit_header_wait():
    assert lbc._retry_after({"X-RateLimit-Reset-In": "9"}, 60) == 9.0
    assert lbc._retry_after({"Retry-After": "0"}, 60) == 1.0
    assert lbc._retry_after({}, 60) == 60


# ─── The worker (fake client and store) ───────────────────────────────────


class _Probe:
    def __init__(self, online: bool = True) -> None:
        self.online = online


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.on_call = None
        self.fail: Exception | None = None

    async def similar_artists(self, mbid):
        self.calls.append(("similar", mbid))
        if self.on_call:
            self.on_call()
        if self.fail:
            raise self.fail
        return [(A3, 5.0)]

    async def recording_popularity(self, mbids):
        self.calls.append(("popularity", tuple(mbids)))
        if self.on_call:
            self.on_call()
        if self.fail:
            raise self.fail
        return {m: (10, 2) for m in mbids}


class FakeStore:
    def __init__(self, artists=(A1, A2), recordings=()) -> None:
        self.artists = list(artists)
        self.recordings = list(recordings)
        self.log: list[str] = []
        self.similar: dict[str, list] = {}
        self.errors: list[str] = []
        self.popularity: dict[str, tuple] = {}

    async def clear_similar(self):
        self.log.append("clear_similar")
        self.similar.clear()

    async def clear_popularity(self):
        self.log.append("clear_popularity")
        self.popularity.clear()

    async def due_artists(self, *, now, limit):
        self.log.append("due_artists")
        return [a for a in self.artists if a not in self.similar][:limit]

    async def save_similar(self, mbid, rows, *, now):
        self.similar[mbid] = list(rows)

    async def similar_error(self, mbid, *, now):
        self.errors.append(mbid)

    async def due_recordings(self, *, now, limit):
        self.log.append("due_recordings")
        return [r for r in self.recordings if r not in self.popularity][:limit]

    async def save_popularity(self, counts, *, now):
        self.popularity.update(counts)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    lbf.reset_status_for_tests()
    monkeypatch.setattr(settings, "music_similar_artists_enabled", False)
    monkeypatch.setattr(settings, "music_popularity_enabled", False)
    yield
    lbf.reset_status_for_tests()


@pytest.fixture
def probe():
    p = _Probe(online=True)
    connectivity.set_current_probe(p)
    yield p
    connectivity.set_current_probe(None)


@pytest.fixture
def both_on(monkeypatch):
    monkeypatch.setattr(settings, "music_similar_artists_enabled", True)
    monkeypatch.setattr(settings, "music_popularity_enabled", True)


def _worker(store=None, client=None):
    client = client or FakeClient()
    store = store or FakeStore(recordings=[f"{i:08d}-0000-0000-0000-000000000000" for i in range(250)])
    return lbf.ListenBrainzFetch(client=client, store=store), client, store


async def test_both_on_fetches_similar_then_popularity(probe, both_on):
    w, client, store = _worker()
    result = await w.tick()
    assert result == {"similar": 2, "popularity": 250}
    assert [c[0] for c in client.calls] == ["similar", "similar", "popularity", "popularity", "popularity"]
    assert [len(c[1]) for c in client.calls if c[0] == "popularity"] == [100, 100, 50]
    assert store.similar == {A1: [(A3, 5.0)], A2: [(A3, 5.0)]}


async def test_under_never_nothing_is_sent(probe, both_on):
    w, client, store = _worker()
    with egress.override_policy("never"):
        await w.tick()
    assert client.calls == []
    assert "due_artists" not in store.log and "due_recordings" not in store.log
    assert lbf.listenbrainz_status()["state"] == "offline"


async def test_the_gate_reads_the_internet_answer(monkeypatch):
    """Mutant check: _egress_allowed must consult egress.internet_allowed."""
    connectivity.set_current_probe(_Probe(True))
    try:
        assert lbf._egress_allowed() is True
        monkeypatch.setattr(egress, "internet_allowed", lambda: False)
        assert lbf._egress_allowed() is False
    finally:
        connectivity.set_current_probe(None)


async def test_offline_or_no_probe_or_shutting_down_sends_nothing(probe, both_on, monkeypatch):
    w, client, _ = _worker()
    probe.online = False
    await w.tick()
    connectivity.set_current_probe(None)
    await w.tick()
    connectivity.set_current_probe(probe)
    probe.online = True
    monkeypatch.setattr(lifecycle, "shutting_down", lambda: True)
    await w.tick()
    assert client.calls == []


async def test_off_sends_nothing_and_clears_once(probe):
    w, client, store = _worker()
    await w.tick()
    assert store.log == ["clear_similar", "clear_popularity"]  # first tick: cleared once
    await w.tick()
    await w.tick()
    assert store.log == ["clear_similar", "clear_popularity"]  # then nothing at all
    assert client.calls == []


async def test_each_switch_is_its_own(probe, monkeypatch):
    monkeypatch.setattr(settings, "music_popularity_enabled", True)
    w, client, store = _worker()
    await w.tick()
    assert {c[0] for c in client.calls} == {"popularity"}
    assert "due_artists" not in store.log
    monkeypatch.setattr(settings, "music_popularity_enabled", False)
    monkeypatch.setattr(settings, "music_similar_artists_enabled", True)
    client.calls.clear()
    await w.tick()
    assert {c[0] for c in client.calls} == {"similar"}
    assert store.popularity == {}  # switched off: what it stored is gone


async def test_switching_off_stops_before_the_next_request(probe, both_on, monkeypatch):
    w, client, store = _worker(store=FakeStore(artists=[A1, A2, A3]))
    client.on_call = lambda: monkeypatch.setattr(settings, "music_similar_artists_enabled", False)
    await w.tick()
    assert [c for c in client.calls if c[0] == "similar"] == [("similar", A1)]


async def test_going_offline_stops_before_the_next_request(probe, both_on):
    w, client, _ = _worker(store=FakeStore(artists=[A1, A2, A3]))
    client.on_call = lambda: setattr(probe, "online", False)
    await w.tick()
    assert len(client.calls) == 1


async def test_a_rate_limit_pauses_everything(probe, both_on):
    w, client, store = _worker()
    client.fail = lbc.ListenBrainzRateLimited(30)
    await w.tick()
    assert len(client.calls) == 1 and store.errors == []
    client.fail = None
    await w.tick()  # still paused
    assert len(client.calls) == 1
    assert lbf.listenbrainz_status()["state"] == "paused"


async def test_an_unreachable_server_records_an_error_and_pauses(probe, both_on):
    w, client, store = _worker()
    client.fail = lbc.ListenBrainzUnavailable("ConnectionError")
    await w.tick()
    assert store.errors == [A1] and len(client.calls) == 1


async def test_at_most_a_batch_per_tick(probe, monkeypatch):
    monkeypatch.setattr(settings, "music_similar_artists_enabled", True)
    many = [f"{i:08d}-1111-1111-1111-111111111111" for i in range(45)]
    w, client, store = _worker(store=FakeStore(artists=many))
    await w.tick()
    assert len(client.calls) == lbf.SIMILAR_PER_TICK
    await w.tick()
    await w.tick()
    assert len(store.similar) == 45


# ─── The database: what is stored, and the picker reading it ──────────────


@requires_db
async def test_db_store_due_save_refresh_and_clear(db_session):
    db = db_session
    await db.execute(text("""
        INSERT INTO library_tracks (id, file_path, artist, musicbrainz_artist_id, musicbrainz_recording_id)
        VALUES (1, '/m/1', 'Radiohead', :a1, :r1), (2, '/m/2', 'Radiohead', :a1, NULL),
               (3, '/m/3', 'Nirvana', :a2, NULL)"""), {"a1": A1, "a2": A2, "r1": R1})
    await db.commit()
    store = lbf.DbFetchStore()
    now = datetime.now(timezone.utc)
    assert sorted(await store.due_artists(now=now, limit=10)) == sorted([A1, A2])
    assert await store.due_recordings(now=now, limit=10) == [R1]
    await store.save_similar(A1, [(A2, 9.0), (A3, 4.0)], now=now)
    await store.similar_error(A2, now=now)
    await store.save_popularity({R1: (100, 7)}, now=now)
    assert await store.due_artists(now=now, limit=10) == []          # A2 waits 2 h after an error
    assert await store.due_artists(now=now + timedelta(hours=3), limit=10) == [A2]
    assert sorted(await store.due_artists(now=now + timedelta(days=91), limit=10)) == sorted([A1, A2])
    assert await store.due_recordings(now=now, limit=10) == []
    assert await store.due_recordings(now=now + timedelta(days=31), limit=10) == [R1]
    await store.clear_similar()
    await store.clear_popularity()
    n = (await db.execute(text(
        "SELECT (SELECT count(*) FROM artist_similarity) + (SELECT count(*) FROM artist_similarity_lookups)"
        " + (SELECT count(*) FROM recording_popularity)"))).scalar()
    assert n == 0


@requires_db
async def test_sql_library_reads_similar_and_popularity(db_session):
    from domovoi.up_next import data as D
    from domovoi.up_next.model import Track

    db = db_session
    recs = [f"0000000{i}-2222-2222-2222-222222222222" for i in range(1, 4)]
    await db.execute(text("""
        INSERT INTO library_tracks (id, file_path, title, artist, musicbrainz_artist_id, musicbrainz_recording_id)
        VALUES (1, '/m/1', 'a', 'Radiohead', :a1, :r1), (2, '/m/2', 'b', 'Radiohead', :a1, :r2),
               (3, '/m/3', 'c', 'Nirvana', :a2, :r3), (4, '/m/4', 'd', 'Muse', NULL, NULL)"""),
        {"a1": A1, "a2": A2, "r1": recs[0], "r2": recs[1], "r3": recs[2]})
    await db.execute(text("INSERT INTO artist_similarity (artist_mbid, similar_mbid, score) VALUES (:a, :b, 5)"),
                     {"a": A1, "b": A2})
    await db.execute(text("""INSERT INTO recording_popularity (recording_mbid, listen_count, user_count)
        VALUES (:r1, 1000, 10), (:r2, 10, 1), (:r3, 500, 5)"""), {"r1": recs[0], "r2": recs[1], "r3": recs[2]})
    norm = (await db.execute(text("SELECT artist_norm FROM library_tracks WHERE id = 1"))).scalar()
    lib = D.SqlLibrary(db)
    assert await lib.similar([Track(1, artist_norm=norm)], salt="s") == {3: "similar to radiohead"}
    pop = await lib.popularity([1, 2, 3, 4])
    assert set(pop) == {1, 2}            # Nirvana has one song with a figure: no comparison
    assert pop[1] == pytest.approx(1.0) and 0 < pop[2] < pop[1]
