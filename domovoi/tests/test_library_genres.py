"""Genre and year from a file's tags (V050, Up next P0) — DB-free.

* the normalisation rules of ``domovoi/library_genres.py`` (split, case,
  spelling aliases, ID3 numbers, placeholders, caps);
* the year rule (first plausible four-digit year, original before date);
* the indexer's ``_read_tags`` reading genre and year out of every format
  the reader knows, from files written byte by byte (media_writer.py);
* the tag backfill's pacing and its "file not there" / "folder not there"
  rules, against a fake store;
* the MusicBrainz year: the client's recording lookup and the year fetch's
  gate — under Internet "never", with the switch off, or offline it makes
  no request (and with the switch off it does not read the database).

The database half (the migration, the writes, the web API) is
``test_library_genre_db.py``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from domovoi import connectivity, egress
from domovoi.config import settings
from domovoi.library_genres import (
    TAGS_VERSION,
    display_genre,
    genre_keys,
    genre_label,
    parse_year,
    resolve_numeric,
)
from domovoi.media_tags._id3 import GENRES_COUNT, ID3V1_GENRES
from domovoi.tests import media_writer as mw
from domovoi.workers import library_indexer as li

# ─── Normalisation ────────────────────────────────────────────────────────


@pytest.mark.parametrize("values, keys", [
    (["Rock"], ["rock"]),
    (["  ROCK  "], ["rock"]),
    (["Rock; Indie"], ["rock", "indie"]),
    (["Rock/Pop, Soul | Funk"], ["rock", "pop", "soul", "funk"]),
    (["Folk & Country"], ["folk", "country"]),                  # " & " splits
    (["R&B"], ["r&b"]),                                         # "&" alone does not
    (["Hip-Hop"], ["hip hop"]),
    (["Hip Hop", "hiphop", "Rap"], ["hip hop"]),                # one genre, three spellings
    (["Hip-Hop/Rap"], ["hip hop"]),
    (["RnB", "Rhythm and Blues"], ["r&b"]),
    (["Drum & Bass"], ["drum and bass"]),                       # a known whole stays whole
    (["Rock & Roll"], ["rock and roll"]),
    (["Singer/Songwriter"], ["singer songwriter"]),
    (["Alt Rock", "Post-Rock"], ["alternative rock", "post rock"]),
    (["Electronica"], ["electronic"]),
    (["Soul", "soul", "SOUL "], ["soul"]),                      # once, first seen
    (["Unknown", "Other", "genre", "", "  "], []),              # placeholders
    (["x" * 41], []),                                           # a comment, not a genre
    (["Rock​Pop"], []),                                    # format characters
    (["Indie Rock"], ["indie rock"]),                           # no taxonomy: not "rock"
])
def test_genre_keys(values, keys) -> None:
    assert genre_keys(values) == keys


def test_keys_are_capped() -> None:
    assert len(genre_keys([";".join(f"g{i}" for i in range(20))])) == 8


@pytest.mark.parametrize("raw, names", [
    ("(17)", ["Rock"]),
    ("(17)Rock", ["Rock"]),             # ID3v2.3: the reference, then the text
    ("(4)(9)", ["Disco", "Metal"]),
    ("17", ["Rock"]),                   # a bare number (ID3v2.4 writers)
    ("(RX)", ["Remix"]),
    ("(CR)", ["Cover"]),
    ("((Foo)", ["(Foo)"]),              # the escape for a text starting "("
    ("(999)", []),
    ("Rock", ["Rock"]),
])
def test_id3_numbers(raw, names) -> None:
    assert resolve_numeric(raw) == names


def test_the_id3v1_table() -> None:
    assert GENRES_COUNT == len(ID3V1_GENRES) == 192
    assert (ID3V1_GENRES[0], ID3V1_GENRES[17], ID3V1_GENRES[79], ID3V1_GENRES[80],
            ID3V1_GENRES[191]) == ("Blues", "Rock", "Hard Rock", "Folk", "Psybient")
    assert len(set(ID3V1_GENRES)) == 192


def test_display_and_labels() -> None:
    assert display_genre(["(17)"]) == "Rock"
    assert display_genre(["Unknown", " Neo  Soul "]) == "Neo Soul"
    assert display_genre(["Unknown"]) is None
    assert display_genre(["y" * 300]) is None            # too long to be a genre at all
    assert genre_label("hip hop") == "Hip Hop"
    assert genre_label("r&b") == "R&B"
    assert genre_label("alternative rock") == "Alternative Rock"
    assert genre_label("k pop") == "K-Pop"


@pytest.mark.parametrize("value, year", [
    ("1997", 1997), ("1997-05-21", 1997), ("1997-05-21T07:00:00Z", 1997),
    ("℗ 2003 Some Label", 2003), ("0000", None), ("1066", None),
    ("2099", None), ("", None), (None, None), ("19970521", None),
    (1984, 1984), (True, None), ("track 12 of 1999", 1999),
])
def test_parse_year(value, year) -> None:
    assert parse_year(value, today=date(2026, 11, 12)) == year


def test_next_year_is_allowed() -> None:
    assert parse_year("2027", today=date(2026, 11, 12)) == 2027


# ─── The reader, per format ───────────────────────────────────────────────


def _tags(path: Path) -> dict[str, Any]:
    got = li._read_tags(path)
    return {k: got[k] for k in ("genre", "genre_keys", "year")}


def test_id3v24_multi_value_genre_and_original_date(tmp_path) -> None:
    p = mw.write_mp3(tmp_path / "a.mp3", [
        mw.text_frame("TIT2", "Song"), mw.text_frame("TCON", "Rock", "Indie"),
        mw.text_frame("TDRC", "2011-03-01"), mw.text_frame("TDOR", "1971"),
    ])
    assert _tags(p) == {"genre": "Rock", "genre_keys": ["rock", "indie"], "year": 1971}


def test_id3v23_numeric_genre_and_tyer(tmp_path) -> None:
    p = mw.write_mp3(tmp_path / "a.mp3", [mw.text_frame("TCON", "(17)Rock"),
                                          mw.text_frame("TYER", "1994")], version=3)
    assert _tags(p) == {"genre": "Rock", "genre_keys": ["rock"], "year": 1994}


def test_txxx_original_year_wins(tmp_path) -> None:
    p = mw.write_mp3(tmp_path / "a.mp3", [mw.text_frame("TDRC", "2015"),
                                          mw.TXXX(desc="originalyear", text="1969")])
    assert _tags(p)["year"] == 1969


def test_an_implausible_original_date_falls_back_to_the_date(tmp_path) -> None:
    p = mw.write_mp3(tmp_path / "a.mp3", [mw.text_frame("TDOR", "0000"),
                                          mw.text_frame("TDRC", "2002")])
    assert _tags(p)["year"] == 2002


def test_id3v1_genre_byte(tmp_path) -> None:
    v1 = mw.id3v1(title="T", artist="A", year="1988", genre=8)
    p = mw.write_mp3(tmp_path / "a.mp3", [], v1=v1)
    assert _tags(p) == {"genre": "Jazz", "genre_keys": ["jazz"], "year": 1988}
    none = mw.write_mp3(tmp_path / "b.mp3", [], v1=mw.id3v1(title="T", genre=255))
    assert _tags(none)["genre"] is None


def test_vorbis_several_genre_fields(tmp_path) -> None:
    p = tmp_path / "a.flac"
    p.write_bytes(mw.flac_file(comments=[("TITLE", "S"), ("GENRE", "Soul"), ("genre", "Funk"),
                                         ("DATE", "1999-01-01"), ("ORIGINALDATE", "1972-06")]))
    assert _tags(p) == {"genre": "Soul", "genre_keys": ["soul", "funk"], "year": 1972}


def test_mp4_text_genre_and_day(tmp_path) -> None:
    p = tmp_path / "a.m4a"
    p.write_bytes(mw.m4a_file([mw.mp4_text("\xa9gen", "Hip-Hop"), mw.mp4_text("\xa9day", "2004-02-10T08:00:00Z")]))
    assert _tags(p) == {"genre": "Hip-Hop", "genre_keys": ["hip hop"], "year": 2004}


def test_mp4_numeric_gnre(tmp_path) -> None:
    p = tmp_path / "a.m4a"
    p.write_bytes(mw.m4a_file([(b"gnre", [mw.mp4_data(0, (17 + 1).to_bytes(2, "big"))])]))
    assert _tags(p)["genre_keys"] == ["rock"]


def test_asf_genre_and_year(tmp_path) -> None:
    p = tmp_path / "a.wma"
    p.write_bytes(mw.asf_file(title="T", extended=[("WM/Genre", 0, mw.asf_text("Blues")),
                                                   ("WM/Year", 0, mw.asf_text("1962"))]))
    assert _tags(p) == {"genre": "Blues", "genre_keys": ["blues"], "year": 1962}


def test_ape_genre_and_year(tmp_path) -> None:
    p = tmp_path / "a.ape"
    p.write_bytes(b"MAC " + b"\x00" * 60 + mw.ape_tag([("Genre", "Reggae"), ("Year", "1977")]))
    assert _tags(p) == {"genre": "Reggae", "genre_keys": ["reggae"], "year": 1977}


def test_no_tags_no_genre(tmp_path) -> None:
    p = tmp_path / "a.mp3"
    p.write_bytes(mw.silent_mp3())
    assert _tags(p) == {"genre": None, "genre_keys": None, "year": None}


def test_the_metadata_the_indexer_writes_carries_them(tmp_path) -> None:
    p = mw.write_mp3(tmp_path / "Artist - Title.mp3", [mw.text_frame("TCON", "Soul"),
                                                        mw.text_frame("TDRC", "1970")])
    meta = li._resolve_metadata_for(p)
    assert (meta["genre"], meta["genre_keys"], meta["year"]) == ("Soul", ["soul"], 1970)
    assert meta["artist"] == "Artist" and meta["title"] == "Title"


# ─── The backfill (fake store) ────────────────────────────────────────────


class _Store:
    def __init__(self, rows: list[tuple[int, str]]) -> None:
        self.rows = list(rows)
        self.written: list[tuple[int, Any]] = []
        self.pending_calls = 0

    async def pending(self, version: int, limit: int) -> list[tuple[int, str]]:
        self.pending_calls += 1
        done = {i for i, _ in self.written}
        return [r for r in self.rows if r[0] not in done][:limit]

    async def write(self, rows, version: int) -> None:
        assert version == TAGS_VERSION
        self.written.extend(rows)


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.mark.asyncio
async def test_backfill_reads_a_batch_per_tick_and_stamps_missing_files(tmp_path, monkeypatch) -> None:
    from domovoi.workers import library_tag_backfill as bf

    monkeypatch.setattr(settings, "music_dir", str(tmp_path))
    monkeypatch.setattr(bf, "BATCH_SIZE", 2)
    a = mw.write_mp3(tmp_path / "a.mp3", [mw.text_frame("TCON", "Soul"), mw.text_frame("TDRC", "1970")])
    store = _Store([(1, str(a)), (2, str(tmp_path / "gone.mp3")), (3, str(a))])
    clock = _Clock()
    w = bf.LibraryTagBackfill(store=store, clock=clock)
    assert await w.tick() == {"read": 1, "missing": 1}
    assert store.written[0] == (1, {"genre": "Soul", "genre_keys": ["soul"], "year": 1970})
    assert store.written[1] == (2, None)                     # stamped, so the pass ends
    assert await w.tick() == {"read": 1, "missing": 0}
    # Nothing left: idle for a minute, then one look.
    assert await w.tick() == {"read": 0, "missing": 0}
    calls = store.pending_calls
    clock.t += 30
    await w.tick()
    assert store.pending_calls == calls                      # still idle
    clock.t += bf.IDLE_RECHECK_SEC
    await w.tick()
    assert store.pending_calls == calls + 1


@pytest.mark.asyncio
async def test_backfill_waits_while_the_music_folder_is_away(tmp_path, monkeypatch) -> None:
    from domovoi.workers import library_tag_backfill as bf

    monkeypatch.setattr(settings, "music_dir", str(tmp_path / "unmounted"))
    store = _Store([(1, str(tmp_path / "unmounted" / "a.mp3"))])
    w = bf.LibraryTagBackfill(store=store, clock=_Clock())
    assert await w.tick() == {"read": 0, "missing": 0}
    assert store.pending_calls == 0 and store.written == []


def test_backfill_reader_refuses_what_is_not_an_audio_file(tmp_path) -> None:
    from domovoi.workers.library_tag_backfill import read_genre_year

    (tmp_path / "notes.txt").write_text("Genre: Rock")
    assert read_genre_year(tmp_path / "notes.txt") is None
    assert read_genre_year(tmp_path) is None
    assert read_genre_year(tmp_path / "missing.mp3") is None


# ─── MusicBrainz: the client's recording year ─────────────────────────────

MBID = "5b11f4ce-a62d-471e-81fc-a69a8278c7da"


class _Resp:
    def __init__(self, status: int, body: Any = None) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no json")
        return self._body


@pytest.fixture
def mb_http(monkeypatch):
    """requests.get answering with the queued responses; records URLs."""
    import requests

    from domovoi.clients.musicbrainz import HttpMusicBrainzClient

    calls: list[tuple[str, dict]] = []
    queue: list[_Resp] = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, dict(params or {})))
        return queue.pop(0)

    monkeypatch.setattr(requests, "get", fake_get)
    client = HttpMusicBrainzClient()
    monkeypatch.setattr(client, "MIN_INTERVAL_SEC", 0)
    return client, calls, queue


@pytest.mark.asyncio
async def test_recording_year_reads_first_release_date(mb_http) -> None:
    client, calls, queue = mb_http
    queue.append(_Resp(200, {"id": MBID, "title": "x", "first-release-date": "1971-05-21"}))
    assert await client.recording_year(MBID) == 1971
    assert calls == [(f"https://musicbrainz.org/ws/2/recording/{MBID}", {"fmt": "json"})]


@pytest.mark.asyncio
async def test_recording_year_answers(mb_http) -> None:
    from domovoi.clients.musicbrainz import (
        MusicBrainzRateLimited,
        MusicBrainzRejected,
        MusicBrainzUnavailable,
    )

    client, calls, queue = mb_http
    queue.extend([_Resp(200, {"first-release-date": ""}), _Resp(404), _Resp(503), _Resp(500),
                  _Resp(400), _Resp(200, None)])
    assert await client.recording_year(MBID) is None           # answered: no date
    assert await client.recording_year(MBID) is None           # no such recording
    with pytest.raises(MusicBrainzRateLimited):
        await client.recording_year(MBID)
    with pytest.raises(MusicBrainzUnavailable):
        await client.recording_year(MBID)
    with pytest.raises(MusicBrainzRejected):
        await client.recording_year(MBID)
    with pytest.raises(MusicBrainzUnavailable):
        await client.recording_year(MBID)                       # a 200 that is not JSON


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "../artist/x", MBID.upper(), MBID + "/x", "abc", f"{MBID}?inc=x"])
async def test_only_an_mbid_reaches_the_path(mb_http, bad) -> None:
    from domovoi.clients.musicbrainz import MusicBrainzRejected

    client, calls, _queue = mb_http
    with pytest.raises(MusicBrainzRejected):
        await client.recording_year(bad)
    assert calls == []


@pytest.mark.asyncio
async def test_recording_year_sends_nothing_under_never(mb_http) -> None:
    from domovoi.clients.musicbrainz import MusicBrainzUnavailable

    client, calls, _queue = mb_http
    with egress.override_policy("never"):
        with pytest.raises(MusicBrainzUnavailable, match="internet off"):
            await client.recording_year(MBID)
    assert calls == []


@pytest.mark.asyncio
async def test_the_search_lookup_keeps_the_release_date(mb_http) -> None:
    client, _calls, queue = mb_http
    queue.append(_Resp(200, {"recordings": [{
        "id": MBID, "score": 100, "title": "Creep", "first-release-date": "1992-09-21",
        "artist-credit": [{"name": "Radiohead", "artist": {"id": "a"}}],
        "releases": [{"id": "r", "title": "Pablo Honey"}],
    }]}))
    match = await client.lookup(title="Creep", artist="Radiohead")
    assert match is not None and match.first_release_date == "1992-09-21" and match.year == 1992


# ─── MusicBrainz: the year fetch and its gate ─────────────────────────────


class _Probe:
    def __init__(self, online: bool = True) -> None:
        self.online = online


class _YearStore:
    def __init__(self, due: list[str]) -> None:
        self._due = list(due)
        self.due_calls = 0
        self.recorded: list[tuple[str, Any]] = []
        self.refused: list[str] = []

    async def due(self, version: int, limit: int) -> list[str]:
        self.due_calls += 1
        done = {m for m, _ in self.recorded} | set(self.refused)
        return [m for m in self._due if m not in done][:limit]

    async def record(self, recording_id: str, year) -> None:
        self.recorded.append((recording_id, year))

    async def record_refused(self, recording_id: str) -> None:
        self.refused.append(recording_id)


class _Client:
    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.answers = answers or {}
        self.asked: list[str] = []

    async def recording_year(self, recording_id: str):
        self.asked.append(recording_id)
        a = self.answers.get(recording_id)
        if isinstance(a, Exception):
            raise a
        return a


@pytest.fixture
def year_fetch(monkeypatch):
    from domovoi.workers import library_year_fetch as yf

    yf.reset_status_for_tests()
    probe = _Probe(online=True)
    connectivity.set_current_probe(probe)
    monkeypatch.setattr(settings, "library_enricher_enabled", True)
    yield yf, probe
    connectivity.set_current_probe(None)
    yf.reset_status_for_tests()


def _ids(n: int) -> list[str]:
    return [f"{i:08x}-0000-4000-8000-000000000000" for i in range(n)]


@pytest.mark.asyncio
async def test_year_fetch_asks_and_records(year_fetch) -> None:
    yf, _probe = year_fetch
    a, b = _ids(2)
    store, client = _YearStore([a, b]), _Client({a: 1971, b: None})
    with egress.override_policy("always"):
        assert await yf.LibraryYearFetch(client=client, store=store).tick() == {"asked": 2, "years": 1}
    assert store.recorded == [(a, 1971), (b, None)]


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["never", "switch_off", "offline", "no_probe"])
async def test_year_fetch_gate_sends_nothing(year_fetch, monkeypatch, why) -> None:
    """The gate the plan names (§2.8): under Internet "never", with
    song recognition off, offline or before the probe exists, no request is
    made. Mutant check: drop any one condition from ``_egress_allowed`` and
    its case here fails."""
    yf, probe = year_fetch
    store, client = _YearStore(_ids(3)), _Client()
    policy = "always"
    if why == "never":
        policy = "never"
    elif why == "switch_off":
        monkeypatch.setattr(settings, "library_enricher_enabled", False)
    elif why == "offline":
        probe.online = False
    else:
        connectivity.set_current_probe(None)
    with egress.override_policy(policy):
        assert await yf.LibraryYearFetch(client=client, store=store).tick() == {"asked": 0, "years": 0}
    assert client.asked == []
    # Nothing is read either: the gate comes before the database.
    assert store.due_calls == 0
    assert yf.year_fetch_status()["state"] == ("off" if why == "switch_off" else "offline")


@pytest.mark.asyncio
async def test_the_gate_is_checked_before_every_request(year_fetch) -> None:
    yf, probe = year_fetch
    ids = _ids(5)

    class _GoesDark(_Client):
        async def recording_year(self, recording_id):
            got = await super().recording_year(recording_id)
            probe.online = False                     # the connection dropped meanwhile
            return got

    store, client = _YearStore(ids), _GoesDark({i: 2000 for i in ids})
    with egress.override_policy("always"):
        await yf.LibraryYearFetch(client=client, store=store).tick()
    assert client.asked == ids[:1]


@pytest.mark.asyncio
async def test_a_rate_limit_or_an_outage_pauses_and_marks_nothing(year_fetch) -> None:
    from domovoi.clients.musicbrainz import MusicBrainzRateLimited, MusicBrainzUnavailable

    yf, _probe = year_fetch
    a, b, c = _ids(3)
    for err, state in ((MusicBrainzRateLimited("503"), "rate_limited"),
                       (MusicBrainzUnavailable("timeout"), "paused")):
        yf.reset_status_for_tests()
        store, client = _YearStore([a, b, c]), _Client({a: 1990, b: err, c: 1991})
        w = yf.LibraryYearFetch(client=client, store=store)
        with egress.override_policy("always"):
            await w.tick()
            assert store.recorded == [(a, 1990)] and store.refused == []
            assert yf.year_fetch_status()["state"] == state
            await w.tick()                                   # paused: not asked again
        assert client.asked == [a, b]


@pytest.mark.asyncio
async def test_a_refused_request_marks_that_track_and_pauses(year_fetch) -> None:
    from domovoi.clients.musicbrainz import MusicBrainzRejected

    yf, _probe = year_fetch
    a, b = _ids(2)
    store, client = _YearStore([a, b]), _Client({a: MusicBrainzRejected("HTTP 403"), b: 1999})
    with egress.override_policy("always"):
        await yf.LibraryYearFetch(client=client, store=store).tick()
    assert store.refused == [a] and client.asked == [a]


@pytest.mark.asyncio
async def test_a_stored_id_that_is_not_an_mbid_is_never_sent(year_fetch) -> None:
    yf, _probe = year_fetch
    store, client = _YearStore(["not-an-id", _ids(1)[0]]), _Client({_ids(1)[0]: 2001})
    with egress.override_policy("always"):
        await yf.LibraryYearFetch(client=client, store=store).tick()
    assert client.asked == [_ids(1)[0]] and store.refused == ["not-an-id"]


def test_year_fetch_worker_shape() -> None:
    from domovoi.workers.library_tag_backfill import LibraryTagBackfill
    from domovoi.workers.library_year_fetch import LibraryYearFetch

    assert LibraryYearFetch.enabled_setting is None              # self-gated per tick
    assert LibraryYearFetch.requires_online and LibraryYearFetch.stub_suppressed
    assert LibraryYearFetch.interval_setting == "library_year_fetch_interval_sec"
    assert settings.library_year_fetch_interval_sec == 30.0
    assert LibraryTagBackfill.interval_setting == "library_tag_backfill_interval_sec"
    assert settings.library_tag_backfill_interval_sec == 1.0
