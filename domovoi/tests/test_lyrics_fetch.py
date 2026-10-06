"""The LRCLIB worker (domovoi/workers/lyrics_fetch.py) and the database
side of its answers (domovoi/lyrics/store.py) — contract §7.2, §3.4–§3.5,
[R9]–[R33], [W14].

No test makes a network request: LRCLIB is a fake client. The first half
is DB-free (``ask_lrclib``, the gate, the failure handling, against a fake
store — these never skip); the second half runs the real SQL on the test
lane (``requires_db``). Every lyric line is INVENTED ([C2]).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from domovoi import connectivity, egress, lifecycle
from domovoi.clients.lrclib import (
    LrclibRateLimited,
    LrclibRecord,
    LrclibRejected,
    LrclibUnavailable,
)
from domovoi.config import settings
from domovoi.lyrics import sidecar, store
from domovoi.lyrics import status as lyrics_state
from domovoi.lyrics.status import lyrics_status
from domovoi.tests.conftest import requires_db
from domovoi.workers import lyrics_fetch as lf
from domovoi.workers.lyrics_fetch import (
    LrclibAnswer,
    LyricsFetchError,
    LyricsFetcher,
    TrackMeta,
    ask_lrclib,
)

L1 = "the lantern hums beside the river door"
L2 = "and every copper kettle sings at dawn"
L3 = "we carried paper boats along the hall"
SYNCED = f"[00:12.40]{L1}\n[00:16.85]{L2}\n"
PLAIN = f"{L1}\n\n{L2}"
WORDS = ("lantern", "kettle", "paper boats", "river door")


def rec(id: int = 1, *, track: str = "Lantern Song", artist: str = "The Example Band",
        album: str | None = "Glass Harbor", duration: float | None = 205.0,
        plain: str | None = PLAIN, synced: str | None = SYNCED,
        instrumental: bool = False) -> LrclibRecord:
    return LrclibRecord(id=id, track_name=track, artist_name=artist, album_name=album,
                        duration=duration, instrumental=instrumental, plain=plain, synced=synced)


class FakeClient:
    """LRCLIB: ``get`` / ``search`` are a value, an exception, or a callable
    of the call's keyword arguments."""

    def __init__(self, get: Any = None, search: Any = ()) -> None:
        self._get, self._search = get, search
        self.calls: list[tuple[str, dict]] = []

    @staticmethod
    def _answer(spec: Any, kw: dict) -> Any:
        value = spec(**kw) if callable(spec) and not isinstance(spec, type) else spec
        if isinstance(value, BaseException):
            raise value
        return value

    async def get(self, **kw):
        self.calls.append(("get", kw))
        return self._answer(self._get, kw)

    async def search(self, **kw):
        self.calls.append(("search", kw))
        return list(self._answer(self._search, kw) or ())


def run(coro):
    return asyncio.run(coro)


T = TrackMeta(1, "Lantern Song", "The Example Band", "Glass Harbor", 205)


class _Probe:
    def __init__(self, online: bool = True) -> None:
        self.online = online


@pytest.fixture(autouse=True)
def _fresh_status():
    lyrics_state.reset_for_tests()
    yield
    lyrics_state.reset_for_tests()


@pytest.fixture
def probe():
    p = _Probe(online=True)
    connectivity.set_current_probe(p)
    yield p
    connectivity.set_current_probe(None)


@pytest.fixture
def lrclib_on(monkeypatch):
    monkeypatch.setattr(settings, "lyrics_lrclib_enabled", True)
    monkeypatch.setattr(settings, "lyrics_write_lrc", True)


# ═══ ask_lrclib — one track, no database ([R15]–[R24]) ══════════════════


@pytest.mark.parametrize("meta, reason", [
    (TrackMeta(1, None, "The Example Band", None, 205), "no_metadata"),
    (TrackMeta(1, "   ", "The Example Band", None, 205), "no_metadata"),
    (TrackMeta(1, "Lantern Song", "", None, 205), "no_metadata"),
    (TrackMeta(1, "Lantern Song", "The Example Band", None, None), "no_duration"),
    (TrackMeta(1, "Lantern Song", "The Example Band", None, 0), "no_duration"),
    (TrackMeta(1, "Lantern Song", "The Example Band", None, 3601), "no_duration"),
])
def test_skipped_without_asking(meta, reason) -> None:
    c = FakeClient(get=rec())
    a = run(ask_lrclib(c, meta))
    assert (a.status, a.error, a.match) == ("skipped", reason, None)
    assert c.calls == []


def test_get_with_timed_lyrics_is_found_at_once() -> None:
    c = FakeClient(get=rec(id=7))
    a = run(ask_lrclib(c, TrackMeta(1, " Lantern Song ", " The Example Band ", "  ", 205)))
    assert (a.status, a.match, a.record.id) == ("found", "get", 7)
    assert a.synced == ((12400, L1), (16850, L2))
    assert a.plain == PLAIN                                # plainLyrics, cleaned
    assert c.calls == [("get", {"track": "Lantern Song", "artist": "The Example Band",
                                "album": None, "duration": 205})]


def test_timed_texts_stand_in_for_missing_plain_lyrics() -> None:
    a = run(ask_lrclib(FakeClient(get=rec(plain=None)), T))
    assert a.plain == f"{L1}\n{L2}"


def test_get_instrumental_is_accepted_without_a_search() -> None:
    c = FakeClient(get=rec(id=9, plain=None, synced=None, instrumental=True))
    a = run(ask_lrclib(c, T))
    assert (a.status, a.match, a.record.id, a.plain, a.synced) == ("instrumental", "get", 9, None, None)
    assert [k for k, _ in c.calls] == ["get"]


def test_get_plain_then_an_accepted_timed_search_record_replaces_it() -> None:
    c = FakeClient(get=rec(id=1, synced=None), search=[rec(id=2)])
    a = run(ask_lrclib(c, T))
    assert (a.status, a.match, a.record.id) == ("found", "search", 2)
    assert a.synced is not None


def test_get_plain_is_kept_when_search_has_nothing_timed() -> None:
    c = FakeClient(get=rec(id=1, synced=None), search=[rec(id=2, synced=None), rec(id=3, duration=300.0)])
    a = run(ask_lrclib(c, T))
    assert (a.status, a.match, a.record.id, a.synced) == ("found", "get", 1, None)
    assert a.plain == PLAIN


@pytest.mark.parametrize("found, expected", [
    ([rec(id=5)], ("found", "search", 5, True)),
    ([rec(id=5, synced=None)], ("found", "search", 5, False)),
    ([rec(id=5, plain=None, synced=None, instrumental=True)], ("instrumental", "search", 5, False)),
    ([], ("not_found", None, None, False)),
    ([rec(id=5, plain=None, synced=None)], ("not_found", None, None, False)),
])
def test_get_404_then_search(found, expected) -> None:
    a = run(ask_lrclib(FakeClient(get=None, search=found), T))
    assert (a.status, a.match, getattr(a.record, "id", None), a.synced is not None) == expected


def test_a_get_record_without_lyrics_is_no_answer() -> None:
    c = FakeClient(get=rec(id=1, plain=None, synced=None), search=[rec(id=2)])
    a = run(ask_lrclib(c, T))
    assert (a.status, a.match, a.record.id) == ("found", "search", 2)


def test_search_asks_with_the_clean_title_and_the_primary_performer() -> None:
    c = FakeClient(get=None, search=[])
    run(ask_lrclib(c, TrackMeta(1, "Lantern Song (Live)", "The Example Band feat. The Velvet Kites",
                                None, 205)))
    assert c.calls[1] == ("search", {"track": "Lantern Song", "artist": "The Example Band"})


@pytest.mark.parametrize("duration, ok", [
    (203.0, True), (202.99, False), (208.0, True), (208.01, False), (205.5, True), (None, False),
])
def test_the_duration_window_is_d_minus_2_to_d_plus_3(duration, ok) -> None:
    a = run(ask_lrclib(FakeClient(get=None, search=[rec(duration=duration)]), T))
    assert (a.status == "found") is ok


@pytest.mark.parametrize("title, artist, ok", [
    ("Lantern Song", "The Example Band", True),
    ("Lantern Song (Remastered 2011)", "The Example Band", True),     # clean_title
    ("lantern  song!", "the example band", True),                     # alias_key
    ("The Lantern Song Part", "The Example Band", True),              # token-set ratio
    ("Glass Harbor", "The Example Band", False),
    ("Lantern Song", "The Velvet Kites", False),
    ("Lantern Song", "The Example Band & The Velvet Kites", True),    # a shared performer
    ("Lantern Song", "Example Band", True),
])
def test_the_title_and_an_artist_must_agree(title, artist, ok) -> None:
    a = run(ask_lrclib(FakeClient(get=None, search=[rec(track=title, artist=artist)]), T))
    assert (a.status == "found") is ok


def test_ranking_timed_then_plain_then_instrumental_then_closest_then_lowest_id() -> None:
    records = [
        rec(id=10, synced=None, duration=205.5),                       # plain, exact
        rec(id=11, plain=None, synced=None, instrumental=True, duration=205.5),
        rec(id=12, duration=207.9),                                    # timed, 2.4 off
        rec(id=13, duration=204.0),                                    # timed, 1.5 off
        rec(id=8, duration=207.0),                                     # timed, 1.5 off, lower id
    ]
    a = run(ask_lrclib(FakeClient(get=None, search=records), T))
    assert (a.match, a.record.id) == ("search", 8)
    plain_only = [r for r in records if r.id in (10, 11)]
    assert run(ask_lrclib(FakeClient(get=None, search=plain_only), T)).record.id == 10


@pytest.mark.parametrize("exc", [LrclibRateLimited(5.0), LrclibUnavailable("timeout"),
                                 LrclibRejected(400), egress.InternetTurnedOff("x")])
def test_client_failures_reach_the_caller(exc) -> None:
    with pytest.raises(type(exc)):
        run(ask_lrclib(FakeClient(get=exc), T))
    with pytest.raises(type(exc)):
        run(ask_lrclib(FakeClient(get=None, search=exc), T))


def test_an_answer_never_shows_its_lyrics() -> None:
    a = run(ask_lrclib(FakeClient(get=rec()), T))
    assert not any(w in repr(a) for w in WORDS)
    assert not any(w in repr(a.record) for w in WORDS)


# ═══ The worker — DB-free, a fake store ([R9]–[R12], [R27]–[R30]) ════════


def due(i: int, title: str = "Lantern Song") -> store.DueTrack:
    return store.DueTrack(i, title, "The Example Band", "Glass Harbor", 205)


class FakeStore:
    def __init__(self, due_rows=(), lrc_due=(), rows=None, fail_save=()) -> None:
        self.due_rows = list(due_rows)
        self.lrc_due_ids = list(lrc_due)
        self.rows = dict(rows or {})
        self.fail_save = set(fail_save)
        self.saved: list[tuple[int, str]] = []
        self.failures: list[tuple[int, str]] = []
        self.lrc: list[tuple[int, str | None, str | None]] = []
        self.calls: list[str] = []
        self.timer: datetime | None = None

    async def due(self, limit):
        self.calls.append("due")
        done = {t for t, _ in self.saved} | {t for t, _ in self.failures}
        rows = [r for r in self.due_rows if r.id not in done]
        return rows[:limit], len(rows)

    async def next_timer(self):
        self.calls.append("next_timer")
        return self.timer

    async def save_answer(self, track_id, answer, query_md5):
        self.calls.append("save_answer")
        if track_id in self.fail_save:
            raise ValueError(f"refused row containing {L1}")      # text a DB error could quote
        self.saved.append((track_id, answer.status))

    async def save_failure(self, track_id, code, query_md5):
        self.calls.append("save_failure")
        self.failures.append((track_id, code))

    async def lrc_due(self, limit):
        self.calls.append("lrc_due")
        return self.lrc_due_ids[:limit]

    async def lrc_row(self, track_id):
        return self.rows.get(track_id)

    async def record_lrc(self, track_id, outcome):
        self.calls.append("record_lrc")
        self.lrc.append((track_id, outcome.state, outcome.error))


def test_off_does_nothing_at_all() -> None:
    s, c = FakeStore([due(1)], lrc_due=[1]), FakeClient(get=rec())
    run(LyricsFetcher(client=c, store=s).tick())
    assert s.calls == [] and c.calls == []
    assert lyrics_state.FETCH.state == "off"
    assert lyrics_status()["fetch"]["state"] == "off"


def test_internet_off_asks_nothing_but_still_writes_lrc_files(lrclib_on, probe) -> None:
    s, c = FakeStore([due(1)]), FakeClient(get=rec())
    with egress.override_policy("never"):
        run(LyricsFetcher(client=c, store=s).tick())
        assert lyrics_status()["fetch"]["state"] == "internet_off"
    assert s.calls == ["lrc_due"] and c.calls == []
    assert lyrics_state.FETCH.state == "internet_off"


def test_no_probe_is_offline(lrclib_on) -> None:
    s, c = FakeStore([due(1)]), FakeClient(get=rec())
    run(LyricsFetcher(client=c, store=s).tick())
    assert lyrics_state.FETCH.state == "offline" and c.calls == []
    connectivity.set_current_probe(_Probe(online=False))
    try:
        run(LyricsFetcher(client=c, store=s).tick())
        assert lyrics_state.FETCH.state == "offline" and c.calls == []
    finally:
        connectivity.set_current_probe(None)


def test_a_shutdown_stops_everything(lrclib_on, probe) -> None:
    s, c = FakeStore([due(1)], lrc_due=[1]), FakeClient(get=rec())
    lifecycle.signal_shutdown("test")
    run(LyricsFetcher(client=c, store=s).tick())
    assert s.calls == [] and c.calls == []


def test_a_batch_asks_saves_and_counts(lrclib_on, probe, caplog) -> None:
    rows = [due(1), due(2, "Glass Harbor"), due(3, "Paper Lanterns")]

    def answers(**kw):
        return {"Lantern Song": rec(id=1), "Glass Harbor": rec(id=2, synced=None)}.get(kw["track"])

    s, c = FakeStore(rows), FakeClient(get=answers, search=[])
    with caplog.at_level(logging.DEBUG):
        counts = run(LyricsFetcher(client=c, store=s).tick())
    assert s.saved == [(1, "found"), (2, "found"), (3, "not_found")]
    assert counts["asked"] == 3 and counts["found"] == 2 and counts["timed"] == 1
    assert counts["requests"] == 5                      # get+search for 2 and 3, get for 1
    F = lyrics_state.FETCH
    assert F.due == 0 and F.state == "done" and F.last_request_at is not None
    info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert any(m.startswith("lyrics fetch: 3 asked in") for m in info)
    for m in info:                                      # never a title or a lyric at INFO
        assert "Lantern" not in m and "Glass" not in m and "Example" not in m
    assert not any(w in caplog.text for w in WORDS)


def test_a_rate_limit_pauses_and_leaves_the_rows(lrclib_on, probe) -> None:
    s, c = FakeStore([due(1), due(2)]), FakeClient(get=LrclibRateLimited(None))
    before = time.time()
    run(LyricsFetcher(client=c, store=s).tick())
    F = lyrics_state.FETCH
    assert F.state == "rate_limited" and F.last_error == "rate_limited"
    assert F.rate_limited_until >= before + 60
    assert s.saved == [] and s.failures == []
    assert len(c.calls) == 1
    run(LyricsFetcher(client=c, store=s).tick())        # still paused: nothing asked
    assert len(c.calls) == 1 and lyrics_state.FETCH.state == "rate_limited"
    assert lyrics_status()["fetch"]["rate_limited_until"] is not None


def test_a_long_retry_after_is_honoured(lrclib_on, probe) -> None:
    s, c = FakeStore([due(1)]), FakeClient(get=LrclibRateLimited(600.0))
    before = time.time()
    run(LyricsFetcher(client=c, store=s).tick())
    assert lyrics_state.FETCH.rate_limited_until >= before + 600


def test_an_unreachable_lrclib_pauses_five_minutes(lrclib_on, probe) -> None:
    s, c = FakeStore([due(1), due(2)]), FakeClient(get=LrclibUnavailable("timeout"))
    before = time.time()
    run(LyricsFetcher(client=c, store=s).tick())
    F = lyrics_state.FETCH
    assert F.state == "paused" and F.last_error == "unavailable:timeout"
    assert F.paused_until >= before + 300 and s.saved == [] and s.failures == []
    run(LyricsFetcher(client=c, store=s).tick())
    assert len(c.calls) == 1 and lyrics_state.FETCH.state == "paused"


def test_internet_turned_off_by_the_client_leaves_the_row(lrclib_on, probe) -> None:
    s, c = FakeStore([due(1)]), FakeClient(get=egress.InternetTurnedOff("https://lrclib.net"))
    run(LyricsFetcher(client=c, store=s).tick())
    assert lyrics_state.FETCH.state == "internet_off" and s.saved == [] and s.failures == []


def test_a_refusal_is_an_error_row_and_the_batch_goes_on(lrclib_on, probe) -> None:
    def answers(**kw):
        return LrclibRejected(400) if kw["track"] == "Lantern Song" else rec(id=2)

    s = FakeStore([due(1), due(2, "Glass Harbor")])
    run(LyricsFetcher(client=FakeClient(get=answers), store=s).tick())
    assert s.failures == [(1, "rejected:400")]
    assert s.saved == [(2, "found")]
    assert lyrics_state.FETCH.last_error is None       # the last row answered


def test_five_refusals_in_a_row_pause(lrclib_on, probe) -> None:
    s = FakeStore([due(i) for i in range(1, 9)])
    before = time.time()
    run(LyricsFetcher(client=FakeClient(get=LrclibRejected(403)), store=s).tick())
    assert [t for t, _ in s.failures] == [1, 2, 3, 4, 5]
    F = lyrics_state.FETCH
    assert F.state == "paused" and F.last_error == "rejected:403" and F.paused_until >= before + 300


def test_a_row_that_cannot_be_saved_is_an_error_and_the_batch_goes_on(lrclib_on, probe, caplog) -> None:
    s = FakeStore([due(1), due(2)], fail_save={1})
    with caplog.at_level(logging.DEBUG):
        run(LyricsFetcher(client=FakeClient(get=rec()), store=s).tick())
    assert s.failures == [(1, "save:ValueError")]
    assert s.saved == [(2, "found")]
    assert not any(w in caplog.text for w in WORDS)     # the error's text never logged


def test_switching_off_mid_batch_stops_before_the_next_request(lrclib_on, probe, monkeypatch) -> None:
    def answers(**kw):
        monkeypatch.setattr(settings, "lyrics_lrclib_enabled", False)
        return rec(id=1)

    s, c = FakeStore([due(1), due(2), due(3)]), FakeClient(get=answers)
    run(LyricsFetcher(client=c, store=s).tick())
    assert s.saved == [(1, "found")]
    assert len(c.calls) == 1 and lyrics_state.FETCH.state == "off"


def test_going_offline_mid_batch_stops_too(lrclib_on, probe) -> None:
    def answers(**kw):
        probe.online = False
        return None

    s, c = FakeStore([due(1), due(2)]), FakeClient(get=answers, search=[])
    run(LyricsFetcher(client=c, store=s).tick())
    assert [k for k, _ in c.calls] == ["get"]          # not even the search
    assert s.saved == [] and lyrics_state.FETCH.state == "offline"


def test_a_failing_tick_reports_the_type_only(lrclib_on, probe) -> None:
    class Broken(FakeStore):
        async def due(self, limit):
            raise RuntimeError(f"database said: {L1}")

    with pytest.raises(LyricsFetchError) as e:
        run(LyricsFetcher(client=FakeClient(), store=Broken()).tick())
    assert "RuntimeError" in str(e.value) and "lantern" not in str(e.value)
    assert e.value.__cause__ is None
    assert lyrics_state.FETCH.state == "error" and lyrics_state.FETCH.last_error == "RuntimeError"


def test_idle_while_a_recheck_is_near_done_when_not(lrclib_on, probe) -> None:
    s = FakeStore([])
    s.timer = datetime.now(timezone.utc) + timedelta(hours=3)
    run(LyricsFetcher(client=FakeClient(), store=s).tick())
    assert lyrics_state.FETCH.state == "idle"
    s.timer = datetime.now(timezone.utc) + timedelta(days=20)
    run(LyricsFetcher(client=FakeClient(), store=s).tick())
    assert lyrics_state.FETCH.state == "done"
    lyrics_state.SCAN.unscanned = 4                     # the local scan is still reading
    run(LyricsFetcher(client=FakeClient(), store=s).tick())
    assert lyrics_state.FETCH.state == "idle"


def test_the_status_shape(lrclib_on, probe) -> None:
    s = lyrics_status()
    assert set(s) == {"scan", "fetch"}
    assert set(s["scan"]) == {"state", "last_tick_at", "last_pass_at", "tracks", "unscanned",
                              "read_last_pass", "last_error"}
    assert set(s["fetch"]) == {"enabled", "write_lrc", "state", "last_tick_at", "last_request_at",
                               "due", "rate_limited_until", "paused_until", "last_error"}
    assert s["fetch"]["enabled"] is True and s["fetch"]["write_lrc"] is True
    assert s["fetch"]["state"] == "idle"
    json.dumps(s)
    lyrics_state.FETCH.state = "offline"               # a stale gate state clears once allowed
    assert lyrics_status()["fetch"]["state"] == "idle"
    lyrics_state.FETCH.paused_until = time.time() + 100
    assert lyrics_status()["fetch"]["state"] == "paused"
    lyrics_state.FETCH.paused_until = time.time() - 1
    assert lyrics_status()["fetch"]["paused_until"] is None


def test_a_found_timed_answer_writes_its_lrc_after_the_save(lrclib_on, probe, tmp_path, monkeypatch) -> None:
    music = tmp_path / "Music"
    music.mkdir()
    audio = music / "Lantern Song.flac"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(settings, "music_dir", str(music))
    row = store.LrcRow(1, str(audio), "Lantern Song", "The Example Band", None, 205, False,
                       ((12400, L1), (16850, L2)), None, None, None)
    s = FakeStore([due(1)], rows={1: row})
    counts = run(LyricsFetcher(client=FakeClient(get=rec()), store=s).tick())
    assert s.calls.index("save_answer") < s.calls.index("record_lrc")     # [R29]
    assert s.lrc == [(1, "written", None)] and counts["lrc_written"] == 1
    assert (music / "Lantern Song.lrc").is_file()
    assert not sidecar.SIDECAR_LOCK.locked()


def test_saving_off_writes_no_lrc(lrclib_on, probe, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "lyrics_write_lrc", False)
    s = FakeStore([due(1)], lrc_due=[1])
    run(LyricsFetcher(client=FakeClient(get=rec()), store=s).tick())
    assert "lrc_due" not in s.calls and s.lrc == []


# ═══ The real SQL (requires_db) ══════════════════════════════════════════


_PATHS = itertools.count()


@pytest.fixture
async def v021(db_session):
    from domovoi.tests.lyrics_testkit import apply_v021, clear_lyrics

    await apply_v021()
    yield
    await clear_lyrics()


async def _seed(title: str | None = "Lantern Song", artist: str | None = "The Example Band",
                album: str | None = "Glass Harbor", duration: int | None = 205, *,
                file_path: str | None = None, favorited: bool = False,
                enriched: bool = False) -> int:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return (await s.execute(text(
            "INSERT INTO library_tracks (file_path, title, artist, album, duration_sec, favorited, "
            "enriched_at) VALUES (:p, :t, :a, :al, :d, :f, CASE WHEN :e THEN now() END) RETURNING id"
        ), {"p": file_path or f"/music/lyrics-fetch/{next(_PATHS):04d}.mp3", "t": title, "a": artist,
            "al": album, "d": duration, "f": favorited, "e": enriched})).scalar_one()


async def _scanned(track_id: int, *, source: str | None = None, plain: str | None = None,
                   synced: list | None = None, missing: bool = False) -> None:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO track_lyrics (track_id, local_checked_at, scan_version, file_missing, "
            "local_source, local_detail, local_plain, local_synced) "
            "VALUES (:id, now(), 1, :m, :src, :det, :plain, CAST(:synced AS JSONB))"
        ), {"id": track_id, "m": missing, "src": source, "det": "USLT" if source else None,
            "plain": plain, "synced": json.dumps(synced) if synced is not None else None})


async def _rows(sql: str, **params) -> list[tuple]:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return [tuple(r) for r in (await s.execute(text(sql), params)).all()]


async def _record(track_id: int, answer: LrclibAnswer, md5: str, now: datetime | None = None) -> None:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await store.record_lrclib(s, track_id, answer, query_md5=md5, now=now)


async def _failure(track_id: int, code: str, md5: str, now: datetime | None = None) -> None:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await store.record_failure(s, track_id, code=code, query_md5=md5, now=now)


FOUND_TIMED = LrclibAnswer("found", "get", rec(id=11), PLAIN, ((12400, L1), (16850, L2)))
FOUND_PLAIN = LrclibAnswer("found", "get", rec(id=12, synced=None), PLAIN, None)
INSTRUMENTAL = LrclibAnswer("instrumental", "search", rec(id=13, plain=None, synced=None, instrumental=True))
NOT_FOUND = LrclibAnswer("not_found")


@requires_db
async def test_the_query_md5_twins_agree(v021) -> None:
    cases = [("Lantern Song", "The Example Band", "Glass Harbor", 205),
             ("Café Lanterne — über die Brücke", "Léa & The Köttle", None, None),
             (None, None, None, 1)]
    for title, artist, album, d in cases:
        tid = await _seed(title, artist, album, d)
        (sql_md5,) = (await _rows(f"SELECT {store.QUERY_MD5_SQL} FROM library_tracks t WHERE t.id = :id",
                                  id=tid))[0]
        assert sql_md5 == store.query_md5(title, artist, album, d)


@requires_db
async def test_ensure_rows_creates_only_missing_library_rows(v021) -> None:
    from domovoi.db.session import session_scope

    a, b = await _seed(), await _seed("Glass Harbor")
    async with session_scope() as s:
        assert await store.ensure_rows(s, [a, b, a, 999_999]) == 2
    async with session_scope() as s:
        assert await store.ensure_rows(s, [a, b]) == 0
        assert await store.ensure_rows(s, []) == 0
    assert await _rows("SELECT count(*) FROM track_lyrics") == [(2,)]


@requires_db
async def test_found_timed_found_plain_and_instrumental(v021) -> None:
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    a, b, c = await _seed(), await _seed("Glass Harbor"), await _seed("Velvet Kites")
    for tid, answer in ((a, FOUND_TIMED), (b, FOUND_PLAIN), (c, INSTRUMENTAL)):
        await _scanned(tid)
        await _record(tid, answer, "m", now)
    rows = {r[0]: r[1:] for r in await _rows(
        "SELECT track_id, lrclib_status, lrclib_id, lrclib_match, lrclib_plain, lrclib_synced IS NOT NULL, "
        "lrclib_instrumental, lrclib_next_at, lrclib_attempts, lrclib_checked_at, lrclib_error "
        "FROM track_lyrics")}
    assert rows[a] == ("found", 11, "get", PLAIN, True, False, None, 0, now, None)
    assert rows[b] == ("found", 12, "get", PLAIN, False, False, now + timedelta(days=28), 0, now, None)
    assert rows[c] == ("instrumental", 13, "search", None, False, True, now + timedelta(days=180), 0, now, None)
    (synced,) = (await _rows("SELECT lrclib_synced FROM track_lyrics WHERE track_id = :id", id=a))[0]
    assert store.synced_from_json(synced) == ((12400, L1), (16850, L2))
    shown = await _rows("SELECT track_id, source, has_synced, instrumental FROM track_lyrics_shown "
                        "ORDER BY track_id")
    assert shown == [(a, "lrclib", True, False), (b, "lrclib", False, False), (c, None, False, True)]


@requires_db
async def test_not_found_windows_double_to_sixteen_weeks(v021) -> None:
    tid = await _seed()
    await _scanned(tid)
    t0 = datetime(2026, 10, 5, tzinfo=timezone.utc)
    seen = []
    for i in range(5):
        now = t0 + timedelta(days=200 * i)
        await _record(tid, NOT_FOUND, "m", now)
        (status, attempts, nxt), = await _rows(
            "SELECT lrclib_status, lrclib_attempts, lrclib_next_at FROM track_lyrics WHERE track_id = :id",
            id=tid)
        seen.append((status, attempts, (nxt - now).days))
    assert seen == [("not_found", 1, 28), ("not_found", 2, 56), ("not_found", 3, 112),
                    ("not_found", 4, 112), ("not_found", 5, 112)]
    await _record(tid, FOUND_PLAIN, "m", t0)                 # the kind changes: attempts reset
    await _record(tid, NOT_FOUND, "other", t0)               # (a changed question clears, [R26])
    assert await _rows("SELECT lrclib_status, lrclib_attempts, lrclib_plain FROM track_lyrics") == [
        ("not_found", 1, None)]


@requires_db
async def test_skipped_records_why_and_waits_for_new_metadata(v021) -> None:
    tid = await _seed(duration=None)
    await _scanned(tid)
    await _record(tid, LrclibAnswer("skipped", error="no_duration"), "m")
    assert await _rows("SELECT lrclib_status, lrclib_error, lrclib_next_at, lrclib_attempts "
                       "FROM track_lyrics") == [("skipped", "no_duration", None, 0)]


@requires_db
async def test_r26_a_recheck_that_finds_nothing_keeps_or_clears(v021) -> None:
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    a, b = await _seed(), await _seed("Glass Harbor")
    for tid in (a, b):
        await _scanned(tid)
        await _record(tid, FOUND_PLAIN, "same")
    await _record(a, NOT_FOUND, "same", now)                 # the same question: keep
    await _record(b, NOT_FOUND, "changed", now)              # the song's tags changed: clear
    rows = {r[0]: r[1:] for r in await _rows(
        "SELECT track_id, lrclib_status, lrclib_plain, lrclib_id, lrclib_match, lrclib_next_at, "
        "lrclib_query_md5 FROM track_lyrics")}
    assert rows[a] == ("found", PLAIN, 12, "get", now + timedelta(days=28), "same")
    assert rows[b] == ("not_found", None, None, None, now + timedelta(days=28), "changed")


@requires_db
async def test_refusals_back_off_and_never_wipe_found_lyrics(v021) -> None:
    t0 = datetime(2026, 10, 5, tzinfo=timezone.utc)
    a, b, c = await _seed(), await _seed("Glass Harbor"), await _seed("Velvet Kites")
    for tid in (a, b, c):
        await _scanned(tid)
    waits = []
    for i in range(10):
        await _failure(a, "rejected:400", "m", t0)
        (nxt, attempts), = await _rows(
            "SELECT lrclib_next_at, lrclib_attempts FROM track_lyrics WHERE track_id = :id", id=a)
        waits.append((attempts, nxt - t0))
    assert [w for _, w in waits[:4]] == [timedelta(hours=h) for h in (1, 2, 4, 8)]
    assert waits[-1] == (10, timedelta(days=7))
    assert await _rows("SELECT lrclib_status, lrclib_error FROM track_lyrics WHERE track_id = :id",
                       id=a) == [("error", "rejected:400")]
    # found lyrics + the same question: kept, still found, the error noted
    await _record(b, FOUND_PLAIN, "m")
    await _failure(b, "save:IntegrityError", "m", t0)
    assert await _rows("SELECT lrclib_status, lrclib_plain IS NOT NULL, lrclib_error, lrclib_next_at "
                       "FROM track_lyrics WHERE track_id = :id", id=b) == [
        ("found", True, "save:IntegrityError", t0 + timedelta(hours=1))]
    # found lyrics + a different question: they go
    await _record(c, FOUND_PLAIN, "old")
    await _failure(c, "rejected:422", "new", t0)
    assert await _rows("SELECT lrclib_status, lrclib_plain, lrclib_query_md5 FROM track_lyrics "
                       "WHERE track_id = :id", id=c) == [("error", None, "new")]


@requires_db
async def test_which_tracks_are_due_r13(v021) -> None:
    from domovoi.db.session import session_scope

    never = await _seed("Never Asked")
    plain_local = await _seed("Plain Tags")
    unscanned = await _seed("Not Scanned")
    missing = await _seed("Missing File")
    owners_lrc = await _seed("Owners Lrc")
    timed_tags = await _seed("Timed Tags")
    answered = await _seed("Answered")
    retag = await _seed("Retagged")
    timer = await _seed("Timer Due")
    await _scanned(never)
    await _scanned(plain_local, source="embedded", plain=L1)
    # (unscanned has no track_lyrics row at all)
    await _scanned(missing, missing=True)
    await _scanned(owners_lrc, source="sidecar", plain=L1)
    await _scanned(timed_tags, source="embedded", plain=L1, synced=[[1000, L1]])
    for tid in (answered, retag, timer):
        await _scanned(tid)
    md5 = store.query_md5("Answered", "The Example Band", "Glass Harbor", 205)
    await _record(answered, FOUND_TIMED, md5)
    await _record(retag, FOUND_TIMED, "an older question")
    await _record(timer, NOT_FOUND, store.query_md5("Timer Due", "The Example Band", "Glass Harbor", 205),
                  datetime.now(timezone.utc) - timedelta(days=60))
    async with session_scope() as s:
        rows, count = await store.due_tracks(s, limit=50)
    ids = {r.id for r in rows}
    assert ids == {never, plain_local, retag, timer}
    assert count == 4
    assert unscanned not in ids and missing not in ids and owners_lrc not in ids
    assert timed_tags not in ids and answered not in ids


@requires_db
async def test_the_order_played_favorite_never_asked_enriched_id_r14(v021) -> None:
    from domovoi.db.session import session_scope

    plain = await _seed("A")
    enriched = await _seed("B", enriched=True)
    asked = await _seed("C")
    favorite = await _seed("D", favorited=True)
    played = await _seed("E")
    old_play = await _seed("F")
    for tid in (plain, enriched, asked, favorite, played, old_play):
        await _scanned(tid)
    await _record(asked, NOT_FOUND, "stale", datetime.now(timezone.utc) - timedelta(days=90))
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO media_plays (room_id, source, library_track_id, started_at) VALUES "
            "('kitchen', 'library', :p, now() - interval '2 days'), "
            "('kitchen', 'library', :o, now() - interval '45 days')"
        ), {"p": played, "o": old_play})
    async with session_scope() as s:
        rows, _ = await store.due_tracks(s, limit=50)
    assert [r.id for r in rows] == [played, favorite, enriched, plain, old_play, asked]


@requires_db
async def test_end_to_end_a_tick_stores_answers_and_writes_the_lrc(
    v021, lrclib_on, probe, tmp_path, monkeypatch,
) -> None:
    music = tmp_path / "Music"
    (music / "The Example Band").mkdir(parents=True)
    monkeypatch.setattr(settings, "music_dir", str(music))
    paths = {name: music / "The Example Band" / f"{name}.flac" for name in ("Lantern Song", "Glass Harbor")}
    for p in paths.values():
        p.write_bytes(b"audio")
    a = await _seed("Lantern Song", file_path=str(paths["Lantern Song"]))
    b = await _seed("Glass Harbor", file_path=str(paths["Glass Harbor"]))
    c = await _seed("No Length", duration=None)
    for tid in (a, b, c):
        await _scanned(tid)

    def answers(**kw):
        return rec(id=21) if kw["track"] == "Lantern Song" else None

    counts = await LyricsFetcher(client=FakeClient(get=answers, search=[])).tick()
    assert counts["asked"] == 3 and counts["found"] == 1 and counts["lrc_written"] == 1
    rows = {r[0]: r[1:] for r in await _rows(
        "SELECT track_id, lrclib_status, lrclib_error, lrc_state, lrc_name, lrc_sha256 FROM track_lyrics")}
    assert rows[a][:4] == ("found", None, "written", "Lantern Song.lrc")
    written = (paths["Lantern Song"].parent / "Lantern Song.lrc").read_bytes()
    assert rows[a][4] == sidecar.sha256_hex(written)
    assert rows[b][:3] == ("not_found", None, None)
    assert rows[c][:3] == ("skipped", "no_duration", None)
    assert not (paths["Glass Harbor"].parent / "Glass Harbor.lrc").exists()
    assert lyrics_state.FETCH.state == "done" and lyrics_state.FETCH.due == 0
    # the next tick asks nothing again
    c2 = FakeClient(get=answers)
    again = await LyricsFetcher(client=c2).tick()
    assert c2.calls == [] and again.get("asked", 0) == 0


@requires_db
async def test_an_oversize_answer_is_a_save_error_and_the_batch_goes_on(v021, lrclib_on, probe) -> None:
    a, b = await _seed("Lantern Song"), await _seed("Glass Harbor")
    for tid in (a, b):
        await _scanned(tid)
    huge = (L1 + "\n") * 8000                                # > 256 KiB once stored

    def answers(**kw):
        if kw["track"] == "Lantern Song":
            return rec(id=31, plain=huge, synced=None)
        return rec(id=32)

    await LyricsFetcher(client=FakeClient(get=answers, search=[])).tick()
    rows = dict((r[0], r[1:]) for r in await _rows(
        "SELECT track_id, lrclib_status, lrclib_error, lrclib_plain IS NULL FROM track_lyrics"))
    assert rows[a][0] == "error" and rows[a][1].startswith("save:") and rows[a][2] is True
    assert rows[b][0] == "found"


@requires_db
async def test_failures_leave_rows_exactly_as_they_were(v021, lrclib_on, probe) -> None:
    tid = await _seed()
    await _scanned(tid)
    before = await _rows("SELECT * FROM track_lyrics")
    for exc in (LrclibRateLimited(1.0), LrclibUnavailable("http_502"), egress.InternetTurnedOff("x")):
        lyrics_state.reset_for_tests()
        await LyricsFetcher(client=FakeClient(get=exc)).tick()
        assert await _rows("SELECT * FROM track_lyrics") == before


@requires_db
async def test_the_lrc_catch_up_and_the_daily_retry_w14(v021, lrclib_on, probe, tmp_path, monkeypatch) -> None:
    from domovoi.db.session import session_scope

    music = tmp_path / "Music"
    music.mkdir()
    monkeypatch.setattr(settings, "music_dir", str(music))
    files = {}
    ids = {}
    for name in ("Backfill", "Failed Long Ago", "Failed Just Now"):
        p = music / f"{name}.mp3"
        p.write_bytes(b"audio")
        files[name] = p
        ids[name] = await _seed(name, file_path=str(p))
        await _scanned(ids[name])
        await _record(ids[name], FOUND_TIMED,
                      store.query_md5(name, "The Example Band", "Glass Harbor", 205))
    async with session_scope() as s:
        await s.execute(text(
            "UPDATE track_lyrics SET lrc_state = 'failed', lrc_error = 'io', lrc_attempted_at = "
            "CASE WHEN track_id = :old THEN now() - interval '25 hours' ELSE now() - interval '1 hour' END "
            "WHERE track_id IN (:old, :new)"
        ), {"old": ids["Failed Long Ago"], "new": ids["Failed Just Now"]})
    # nothing is due at LRCLIB: the tick only writes files
    c = FakeClient()
    counts = await LyricsFetcher(client=c).tick()
    assert c.calls == []
    assert counts["lrc_written"] == 2
    state = dict(await _rows("SELECT track_id, lrc_state FROM track_lyrics"))
    assert state[ids["Backfill"]] == "written" and state[ids["Failed Long Ago"]] == "written"
    assert state[ids["Failed Just Now"]] == "failed"
    assert not (music / "Failed Just Now.lrc").exists()
    # LRCLIB off: no file at all
    monkeypatch.setattr(settings, "lyrics_lrclib_enabled", False)
    async with session_scope() as s:
        await s.execute(text("UPDATE track_lyrics SET lrc_attempted_at = now() - interval '2 days' "
                             "WHERE track_id = :id"), {"id": ids["Failed Just Now"]})
    await LyricsFetcher(client=c).tick()
    assert not (music / "Failed Just Now.lrc").exists()


@requires_db
async def test_an_owners_lrc_is_recorded_as_exists_and_left_alone(v021, lrclib_on, probe, tmp_path, monkeypatch) -> None:
    music = tmp_path / "Music"
    music.mkdir()
    monkeypatch.setattr(settings, "music_dir", str(music))
    audio = music / "Lantern Song.mp3"
    audio.write_bytes(b"audio")
    theirs = music / "lantern song.LRC"
    theirs.write_bytes(b"[00:01.00]their own words\n")
    tid = await _seed(file_path=str(audio))
    await _scanned(tid)
    await _record(tid, FOUND_TIMED, store.query_md5("Lantern Song", "The Example Band", "Glass Harbor", 205))
    c = FakeClient()
    await LyricsFetcher(client=c).tick()
    assert c.calls == []
    assert await _rows("SELECT lrc_state FROM track_lyrics") == [("exists",)]
    assert theirs.read_bytes() == b"[00:01.00]their own words\n"
    assert sorted(p.name for p in music.iterdir()) == ["Lantern Song.mp3", "lantern song.LRC"]
