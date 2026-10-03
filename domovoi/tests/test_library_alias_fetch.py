"""The opt-in MusicBrainz "also called" fetch (workers/library_alias_fetch.py)
and the artist search it rides on (clients/musicbrainz.py).

No test makes a network request: MusicBrainz answers come from
``fixtures/musicbrainz_artist_search.json`` (CC0 MusicBrainz data recorded
by the 2026-10-02 study) or from hand-written records, and the HTTP client
is exercised against a fake ``requests.get``.

What is pinned here, in order:

* the client: the Lucene query, the User-Agent, 503/429 → rate limited,
  a dead network → unavailable, one request a second shared with the
  recording lookup;
* verification: a hit counts only when it is the same name as the library
  spelling (the search for "B.o.B" returns Bob Dylan, "MAGIC!" Yellow
  Magic Orchestra — both rejected), ties are "ambiguous";
* the STRICT real-name filter on the recorded data: 50 Cent loses all eight
  "Jackson" forms, P!nk "Alecia Moore", JAY-Z "Shawn Carter", A$AP Rocky
  "Rakim Mayers"; spoken stage names survive;
* which library artists are looked up (single performers, a band credit as
  a whole, never a featured guest or a fragment of a credit);
* the worker: off → nothing at all; offline → nothing; switched on → the
  next tick runs; the egress gate before every request; rate-limit and
  outage back-off; batches of 25; top-ups; error retries;
* the real V019 writes (``requires_db``): source=musicbrainz rows, a
  household alias is never displaced, a removed MusicBrainz alias stays
  removed, a name the library already has is never taken.

Every synthetic name below was checked against the frozen spoken-name gate
before use; the recorded artists are the CONTRACT's fixture set. No
assertion is about a transcript.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from domovoi import connectivity, lifecycle
from domovoi.clients import musicbrainz as mbmod
from domovoi.clients.musicbrainz import (
    HttpMusicBrainzClient,
    MbAlias,
    MbArtist,
    MusicBrainzRateLimited,
    MusicBrainzStubClient,
    MusicBrainzUnavailable,
    artist_search_query,
    parse_artist_search,
)
from domovoi.config import Settings, settings
from domovoi.handlers.shared.spoken_names import alias_key
from domovoi.tests.conftest import requires_db
from domovoi.workers import library_alias_fetch as laf
from domovoi.workers.library_alias_fetch import (
    BATCH_SIZE,
    LibraryAliasFetcher,
    LookupRow,
    Verification,
    alias_fetch_status,
    classify_aliases,
    filter_aliases,
    scan_library,
    uninvert_sort_name,
    verify_artist,
)

FIXTURE = Path(__file__).parent / "fixtures" / "musicbrainz_artist_search.json"


def _searches() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["searches"]


def _hits(name: str) -> list[MbArtist]:
    return parse_artist_search(_searches()[name])


def _matched(name: str) -> MbArtist:
    v = verify_artist(name, _hits(name))
    assert v.status == "matched", (name, v)
    assert v.artist is not None
    return v.artist


def _artist(name: str, *aliases: MbAlias, type_: str | None = "Person",
            score: int = 100, id_: str | None = None, sort: str | None = None) -> MbArtist:
    return MbArtist(id=id_ or f"id-{name}", name=name, sort_name=sort or name,
                    type=type_, score=score, aliases=list(aliases))


class _Probe:
    def __init__(self, online: bool = True) -> None:
        self.online = online


@pytest.fixture(autouse=True)
def _fresh_status():
    laf.reset_status_for_tests()
    yield
    laf.reset_status_for_tests()


@pytest.fixture
def probe():
    p = _Probe(online=True)
    connectivity.set_current_probe(p)
    yield p
    connectivity.set_current_probe(None)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "music_alias_fetch_enabled", True)


# ─── The client ───────────────────────────────────────────────────────────


def test_the_search_is_one_quoted_artist_phrase_with_quotes_and_backslashes_escaped() -> None:
    assert artist_search_query("deadmau5") == 'artist:"deadmau5"'
    assert artist_search_query('Velvet "VH" Harbor') == 'artist:"Velvet \\"VH\\" Harbor"'
    assert artist_search_query("Velvet\\Harbor") == 'artist:"Velvet\\\\Harbor"'
    # Nothing else is touched: inside a quoted phrase the rest is literal.
    assert artist_search_query("Copper, Linen & Fern!") == 'artist:"Copper, Linen & Fern!"'


def test_the_recorded_answers_parse_into_artists_with_types_and_aliases() -> None:
    pink = _hits("P!nk")
    assert [(a.name, a.type, a.score) for a in pink] == [
        ("P!nk", "Person", 100), ("Mr. P!nk", "Person", 65), ("P!nk Panther", None, 65),
    ]
    assert pink[0].sort_name == "Pink"
    legal = [a.name for a in pink[0].aliases if a.type == "Legal name"]
    assert legal == ["Alecia Beth Moore"]
    chv = _hits("CHVRCHES")[0]
    assert chv.type == "Group"
    en = [a for a in chv.aliases if a.locale == "en"]
    assert [(a.name, a.primary) for a in en] == [("CHVRCHES", True)]


@pytest.mark.parametrize("data", [None, {}, {"artists": None}, {"artists": [{"name": "x"}]},
                                  {"artists": ["junk", {"id": "1"}]}, "not json"])
def test_a_malformed_answer_is_no_artists(data) -> None:
    assert parse_artist_search(data) == []


async def test_the_stub_client_finds_nobody() -> None:
    assert await MusicBrainzStubClient().search_artists("deadmau5") == []


class _Resp:
    def __init__(self, status: int, data: Any = None) -> None:
        self.status_code = status
        self._data = data

    def json(self) -> Any:
        if self._data is None:
            raise ValueError("no json")
        return self._data


def _fake_get(monkeypatch, answers: list[Any], calls: list[dict]) -> None:
    import requests

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {}),
                      "timeout": timeout, "at": time.monotonic()})
        answer = answers.pop(0) if answers else _Resp(200, {"artists": []})
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(requests, "get", fake_get)


async def test_an_artist_search_sends_the_query_and_the_project_user_agent(monkeypatch) -> None:
    calls: list[dict] = []
    _fake_get(monkeypatch, [_Resp(200, _searches()["deadmau5"])], calls)
    client = HttpMusicBrainzClient()
    hits = await client.search_artists("deadmau5", limit=5)
    assert [h.name for h in hits] == ["deadmau5"]
    (call,) = calls
    assert call["url"] == "https://musicbrainz.org/ws/2/artist"
    assert call["params"] == {"query": 'artist:"deadmau5"', "fmt": "json", "limit": "5"}
    assert call["headers"]["User-Agent"] == (
        "domovoi/1.0.0 (+https://github.com/coders-farm-official/domovoi)"
    )
    assert call["headers"]["Accept"] == "application/json"


@pytest.mark.parametrize("status", [503, 429])
async def test_a_503_or_429_means_slow_down(monkeypatch, status) -> None:
    _fake_get(monkeypatch, [_Resp(status)], [])
    with pytest.raises(MusicBrainzRateLimited):
        await HttpMusicBrainzClient().search_artists("deadmau5")


async def test_no_answer_at_all_means_unavailable(monkeypatch) -> None:
    import requests

    _fake_get(monkeypatch, [requests.ConnectionError("no route")], [])
    with pytest.raises(MusicBrainzUnavailable):
        await HttpMusicBrainzClient().search_artists("deadmau5")


async def test_any_other_error_status_is_an_empty_answer(monkeypatch) -> None:
    _fake_get(monkeypatch, [_Resp(500), _Resp(200)], [])
    client = HttpMusicBrainzClient()
    client.MIN_INTERVAL_SEC = 0.0
    assert await client.search_artists("deadmau5") == []
    assert await client.search_artists("deadmau5") == []  # 200 without JSON


async def test_the_recording_lookup_still_swallows_every_failure(monkeypatch) -> None:
    import requests

    _fake_get(monkeypatch, [requests.Timeout("slow"), _Resp(503)], [])
    client = HttpMusicBrainzClient()
    client.MIN_INTERVAL_SEC = 0.0
    assert await client.lookup(title="Paper Lantern", artist="Velvet Harbor") is None
    assert await client.lookup(title="Paper Lantern", artist="Velvet Harbor") is None


async def test_searches_and_lookups_share_one_pace(monkeypatch) -> None:
    """One client, one lock: interleaved artist searches and recording
    lookups never go out closer together than the minimum interval."""
    calls: list[dict] = []
    _fake_get(monkeypatch, [], calls)
    client = HttpMusicBrainzClient()
    client.MIN_INTERVAL_SEC = 0.15
    await asyncio.gather(
        client.search_artists("Velvet Harbor"),
        client.lookup(title="Paper Lantern", artist="Velvet Harbor"),
        client.search_artists("Orrin Vance"),
    )
    times = sorted(c["at"] for c in calls)
    assert len(times) == 3
    assert all(b - a >= 0.14 for a, b in zip(times, times[1:])), times


def test_the_shipped_pace_is_one_request_a_second() -> None:
    assert HttpMusicBrainzClient.MIN_INTERVAL_SEC == 1.0


# ─── Verification ─────────────────────────────────────────────────────────


def test_bob_dylan_is_not_b_o_b() -> None:
    hits = _hits("B.o.B")
    assert hits[0].name == "Bob Dylan" and hits[0].score == 100
    assert verify_artist("B.o.B", hits) == Verification("no_match")


def test_yellow_magic_orchestra_is_not_magic() -> None:
    hits = _hits("MAGIC!")
    assert hits[0].name == "Yellow Magic Orchestra" and hits[0].score == 100
    assert verify_artist("MAGIC!", hits) == Verification("no_match")


def test_low_scored_namesakes_are_ignored() -> None:
    v = verify_artist("P!nk", _hits("P!nk"))
    assert v.status == "matched" and v.artist is not None
    assert v.artist.name == "P!nk" and v.artist.id.startswith("f4d5cc07")


def test_a_hit_below_ninety_never_counts_even_with_the_same_name() -> None:
    assert verify_artist("Velvet Harbor", [_artist("Velvet Harbor", score=89)]).status == "no_match"


def test_two_different_artists_sharing_the_top_score_are_ambiguous() -> None:
    hits = [
        _artist("Quillon", id_="a", score=100, type_="Group"),
        _artist("Quillon", id_="b", score=100, type_="Person"),
    ]
    assert verify_artist("Quillon", hits) == Verification("ambiguous")


def test_a_clear_winner_among_namesakes_is_matched() -> None:
    hits = [_artist("Quillon", id_="b", score=93), _artist("Quillon", id_="a", score=100)]
    v = verify_artist("Quillon", hits)
    assert v.status == "matched" and v.artist is not None and v.artist.id == "a"


def test_the_same_artist_twice_is_not_ambiguous() -> None:
    hits = [_artist("Quillon", id_="a", score=100), _artist("Quillon", id_="a", score=100)]
    assert verify_artist("Quillon", hits).status == "matched"


def test_a_hit_may_be_recognised_by_its_sort_name_or_an_alias_but_not_a_legal_name() -> None:
    by_sort = _artist("Orrin V.", sort="Vance, Orrin")
    assert verify_artist("Orrin Vance", [by_sort]).status == "matched"
    by_alias = _artist("O. Vance Project", MbAlias("Orrin Vance", "Search hint"))
    assert verify_artist("Orrin Vance", [by_alias]).status == "matched"
    by_legal = _artist("Kestrel Moon", MbAlias("Orrin Vance", "Legal name"))
    assert verify_artist("Orrin Vance", [by_legal]).status == "no_match"


@pytest.mark.parametrize("sort, spoken", [
    ("Dylan, Bob", "Bob Dylan"),
    ("Beatles, The", "The Beatles"),
    ("Valentine, Ricky, Jr.", "Valentine, Ricky, Jr."),
    ("CHVRCHES", "CHVRCHES"),
    (None, None),
])
def test_sort_names_are_said_first_name_first(sort, spoken) -> None:
    assert uninvert_sort_name(sort) == spoken


# ─── The strict real-name filter, on recorded MusicBrainz data ────────────


def _kept_names(name: str, library_keys=()) -> list[str]:
    kept, _dropped = filter_aliases(name, _matched(name), library_keys)
    return [d.name for d in kept]


def _reasons(name: str) -> dict[str, str | None]:
    return {d.name: d.reason for d in classify_aliases(name, _matched(name), ())}


def test_fifty_cent_keeps_a_spoken_form_and_none_of_the_jacksons() -> None:
    decisions = classify_aliases("50 Cent", _matched("50 Cent"), ())
    kept = [d for d in decisions if d.reason is None]
    assert {d.key for d in kept} == {"fiftycents"}  # ".50 Cents" / "50 Cents": one name
    assert [d.name for d in kept] == ["50 Cents"]
    jacksons = [d for d in decisions if "jackson" in d.name.lower()]
    assert len(jacksons) == 8
    assert all(d.reason in ("legal_name", "legal_word") for d in jacksons), jacksons
    # The bare surname is a real name too: nothing kept can answer to it.
    assert not any("jackson" in d.key for d in kept)


def test_pink_keeps_p_nk_and_not_her_real_name() -> None:
    assert _kept_names("P!nk") == ["P.nk"]
    reasons = _reasons("P!nk")
    assert reasons["Alecia Moore"] == "legal_word"  # typed "Artist name" on MusicBrainz
    assert reasons["Alecia Beth Moore"] == "legal_name"
    assert reasons["Alecia B. Moore"] == "legal_word"
    assert reasons["Pink"] == "redundant"


def test_jay_z_keeps_nothing() -> None:
    assert _kept_names("JAŸ-Z") == []
    reasons = _reasons("JAŸ-Z")
    assert reasons["Shawn Carter"] == "legal_word"
    assert reasons["S. Carter"] == "legal_word"
    assert reasons["Hova"] == "unlike_name"
    assert reasons["Jigga"] == "unlike_name"


def test_asap_rocky_keeps_nothing() -> None:
    assert _kept_names("A$AP Rocky") == []
    reasons = _reasons("A$AP Rocky")
    assert reasons["Rakim Mayers"] == "legal_word"
    assert reasons["Lord Flacko"] == "unlike_name"
    assert reasons["ASAP Rocky"] == "redundant"


def test_deadmau5_keeps_its_spoken_spellings() -> None:
    kept, _ = filter_aliases("deadmau5", _matched("deadmau5"), ())
    assert [d.name for d in kept] == ["Dead Mouse", "Deadmau", "Deadmru5"]
    # "Deadmouse" is the same name as "Dead Mouse" (one key, one row).
    assert alias_key("Deadmouse") in {d.key for d in kept}


def test_tech_n9ne_keeps_its_nicknames_and_not_its_legal_name() -> None:
    assert _kept_names("Tech N9ne") == ["Tek N9NE", "Tec 9"]
    assert _reasons("Tech N9ne")["Aaron D. Yates"] in ("legal_word", "legal_name")


def test_a_group_keeps_its_spoken_spelling_and_drops_other_scripts() -> None:
    assert _kept_names("CHVRCHES") == ["Churches"]
    assert _reasons("CHVRCHES")["CHVRCHΞS"] == "script"


def test_a_group_may_keep_a_name_that_does_not_sound_like_it() -> None:
    assert _kept_names("INXS") == ["In Excess", "The Farriss Brothers"]


def test_names_normalization_already_hears_are_not_stored() -> None:
    assert _kept_names("$uicideboy$") == []
    reasons = _reasons("$uicideboy$")
    # Dropped because it IS the library name once normalized, not as a
    # real name: the resolver hears it without any alias.
    assert reasons["Suicide Boys"] == "redundant"
    assert set(reasons.values()) == {"redundant"}


def test_the_dropped_count_counts_names_not_spellings() -> None:
    kept, dropped = filter_aliases("CHVRCHES", _matched("CHVRCHES"), ())
    # Greek form + the name itself + its sort name; "CHURCHES" is a second
    # spelling of the kept "Churches", neither kept nor dropped.
    assert (len(kept), dropped) == (1, 3)


def test_a_name_the_library_already_has_is_never_taken() -> None:
    assert _kept_names("Tech N9ne", library_keys={alias_key("Tec 9")}) == ["Tek N9NE"]


@pytest.mark.parametrize("alias, reason", [
    (MbAlias("Velvet Harbour", "Artist name", "en_GB"), None),
    (MbAlias("Velvet Harbour", "Search hint", "de"), "locale"),
    (MbAlias("Velvet Harbour", "Legal name"), "legal_name"),
    (MbAlias("Velvet Harbour", "Some new type"), "alias_type"),
    (MbAlias("VH", "Search hint"), "too_short"),
    (MbAlias("Velvet Harbor Tribute Band Live At The Hall", "Search hint"), "too_long"),
    (MbAlias("Вельвет Харбор", "Search hint"), "script"),
    (MbAlias("Harbor Boys", "Search hint"), None),
])
def test_the_filter_rules_for_a_group(alias, reason) -> None:
    group = _artist("Velvet Harbor", alias, type_="Group")
    (decision,) = [d for d in classify_aliases("Velvet Harbor", group, ()) if d.mb_type != "sort name"]
    assert decision.reason == reason


def test_a_person_or_an_unknown_type_must_sound_like_the_name() -> None:
    for type_ in ("Person", None, "Character"):
        person = _artist("Orrin Vance", MbAlias("Harbor Boys", "Search hint"),
                         MbAlias("Orrin Vanse", "Search hint"), type_=type_)
        assert _kept(classify_aliases("Orrin Vance", person, ())) == ["Orrin Vanse"], type_
    for type_ in ("Group", "Orchestra", "Choir"):
        group = _artist("Orrin Vance", MbAlias("Harbor Boys", "Search hint"), type_=type_)
        assert _kept(classify_aliases("Orrin Vance", group, ())) == ["Harbor Boys"], type_


def test_a_word_of_a_legal_name_is_enough_to_drop_a_persons_alias() -> None:
    person = _artist(
        "Kestrel Moon",
        MbAlias("Nadia Elspeth Brook", "Legal name"),
        MbAlias("Nadia B", "Search hint"),
        MbAlias("Kestrel Brook", "Search hint"),
        MbAlias("Kestral Moon", "Search hint"),
    )
    decisions = {d.name: d.reason for d in classify_aliases("Kestrel Moon", person, ())}
    assert decisions["Nadia B"] == "legal_word"
    assert decisions["Kestrel Brook"] == "legal_word"
    assert decisions["Kestral Moon"] is None


def test_words_of_the_stage_name_are_not_legal_words() -> None:
    # A legal name that shares a word with the stage name ("Moon") does
    # not make every alias with that word a real name.
    person = _artist(
        "Kestrel Moon",
        MbAlias("Nadia Moon", "Legal name"),
        MbAlias("Kestrel Moon Music", "Search hint"),
    )
    decisions = {d.name: d.reason for d in classify_aliases("Kestrel Moon", person, ())}
    assert decisions["Kestrel Moon Music"] is None


def _kept(decisions) -> list[str]:
    return [d.name for d in decisions if d.reason is None]


# ─── Which library artists are looked up ──────────────────────────────────


def test_single_performers_and_band_credits_are_looked_up_and_guests_are_not() -> None:
    rows = [
        ("Paper Lantern", "Velvet Harbor", "Night Bus"),
        ("Second Song", "Velvet Harbor", "Night Bus"),
        ("Hollow Pines", "Velvet Harbor feat. Orrin Vance", None),  # guest only
        ("Lantern Club", "Copper, Linen & Fern", None),  # a band credit
        ("Too Many", "Cobalt, Wren, Quillon, Ashgrove, Lumen", None),  # 5 parts
        ("Versus", "Kestrel Moon vs. Quillon", None),
        ("Duet", "Thistle Engine x Quillon", None),
        ("Mixed", "Ashgrove; Kestrel Moon", None),  # 2 parts, ; only
        ("Solo", "Quillon", None),  # Quillon also on its own: looked up
        ("Orrin Vance - Night Bus Home", None, None),  # filename-derived artist
        ("Compilation", "Various Artists", None),
        ("Untitled", "Unknown Artist", None),
        ("Short", "VH", None),
    ]
    scan = scan_library(rows)
    assert scan.artists == {
        alias_key("Velvet Harbor"): "Velvet Harbor",
        alias_key("Ashgrove; Kestrel Moon"): "Ashgrove; Kestrel Moon",
        alias_key("Copper, Linen & Fern"): "Copper, Linen & Fern",
        alias_key("Orrin Vance"): "Orrin Vance",
        alias_key("Quillon"): "Quillon",
    }
    assert list(scan.artists)[0] == alias_key("Velvet Harbor")  # most tracks first
    for fragment in ("Linen", "Fern", "Cobalt", "Thistle Engine", "Kestrel Moon"):
        assert alias_key(fragment) not in scan.artists, fragment
    # Every name the library knows, for the "never take a library name" rule.
    for name in ("Paper Lantern", "Night Bus", "Hollow Pines", "Fern", "Thistle Engine"):
        assert alias_key(name) in scan.name_keys, name


def test_the_most_frequent_spelling_is_the_one_looked_up() -> None:
    rows = [("a", "VELVET HARBOR", None), ("b", "Velvet Harbor", None), ("c", "Velvet Harbor", None)]
    assert scan_library(rows).artists == {"velvetharbor": "Velvet Harbor"}


# ─── The worker (fake client, fake store) ─────────────────────────────────


class _FakeClient:
    """Answers from the recorded fixture (or a per-name override); counts
    every request."""

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[str] = []
        self.on_call = None

    async def search_artists(self, name: str, *, limit: int = 5) -> list[MbArtist]:
        self.calls.append(name)
        if self.on_call is not None:
            self.on_call(name)
        answer = self.answers.get(name)
        if isinstance(answer, BaseException):
            raise answer
        if answer is not None:
            return answer
        searches = _searches()
        return parse_artist_search(searches[name]) if name in searches else []

    async def lookup(self, *, title, artist):  # pragma: no cover - unused
        return None


class _FakeStore:
    """In-memory stand-in for the V019 writes, with ON CONFLICT (alias_key)
    DO NOTHING semantics. Counts every call so 'off' can be proved to do
    nothing at all."""

    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.rows: list[tuple] = list(rows or [])
        self.lookups: dict[str, LookupRow] = {}
        self.aliases: dict[str, dict] = {}
        self.calls: list[str] = []

    async def library_fingerprint(self):
        self.calls.append("fingerprint")
        return (len(self.rows),)

    async def library_rows(self):
        self.calls.append("rows")
        return list(self.rows)

    async def lookup_rows(self):
        self.calls.append("lookups")
        return list(self.lookups.values())

    def _bump(self, key: str, status: str) -> None:
        prev = self.lookups.get(key)
        self.lookups[key] = LookupRow(key, status, (prev.attempts + 1) if prev else 1,
                                      datetime.now(timezone.utc))

    async def record_result(self, *, artist_key, artist_name, verification, kept, dropped):
        self.calls.append("record")
        added = 0
        if verification.status == "matched":
            for d in kept:
                if d.key not in self.aliases:
                    self.aliases[d.key] = {"alias": d.name, "target_key": artist_key}
                    added += 1
        self._bump(artist_key, verification.status)
        return added

    async def record_error(self, *, artist_key, artist_name, code):
        self.calls.append("error")
        self._bump(artist_key, "error")


def _worker(rows=None, answers=None) -> tuple[LibraryAliasFetcher, _FakeClient, _FakeStore]:
    client, store = _FakeClient(answers), _FakeStore(rows)
    return LibraryAliasFetcher(client=client, store=store), client, store


_RECORDED_ROWS = [
    ("Paper Lantern", "deadmau5", None),
    ("Night Bus", "CHVRCHES", None),
    ("Hollow Pines", "B.o.B", None),
    ("Lantern Club", "Tech N9ne", None),
]


def test_the_worker_is_self_gated_online_only_and_stub_suppressed() -> None:
    assert LibraryAliasFetcher.name == "library_alias_fetch"
    assert LibraryAliasFetcher.enabled_setting is None
    assert LibraryAliasFetcher.interval_setting == "music_alias_fetch_interval_sec"
    assert LibraryAliasFetcher.stub_suppressed is True
    assert LibraryAliasFetcher.requires_online is True
    assert Settings().music_alias_fetch_enabled is False  # opt-in


def test_the_core_registers_the_worker() -> None:
    import inspect

    from domovoi import main

    src = inspect.getsource(main.lifespan)
    assert 'WORKERS.add_worker(LibraryAliasFetcher(), owner="core")' in src


async def test_off_sends_nothing_and_reads_nothing(probe, monkeypatch) -> None:
    monkeypatch.setattr(settings, "music_alias_fetch_enabled", False)
    worker, client, store = _worker(_RECORDED_ROWS)
    for _ in range(3):
        await worker.tick()
    assert client.calls == [] and store.calls == []
    assert alias_fetch_status()["state"] == "off"
    assert alias_fetch_status()["enabled"] is False


async def test_offline_sends_nothing(probe, enabled) -> None:
    probe.online = False
    worker, client, store = _worker(_RECORDED_ROWS)
    await worker.tick()
    assert client.calls == [] and store.calls == []
    assert alias_fetch_status()["state"] == "offline"


async def test_without_a_connectivity_probe_nothing_is_sent(enabled) -> None:
    connectivity.set_current_probe(None)
    worker, client, store = _worker(_RECORDED_ROWS)
    await worker.tick()
    assert client.calls == [] and store.calls == []


async def test_nothing_is_sent_while_shutting_down(probe, enabled) -> None:
    worker, client, _store = _worker(_RECORDED_ROWS)
    lifecycle.signal_shutdown("test")
    try:
        await worker.tick()
    finally:
        lifecycle.reset()
    assert client.calls == []


async def test_switching_it_on_applies_on_the_next_tick(probe, monkeypatch) -> None:
    monkeypatch.setattr(settings, "music_alias_fetch_enabled", False)
    worker, client, store = _worker(_RECORDED_ROWS)
    await worker.tick()
    assert client.calls == []
    monkeypatch.setattr(settings, "music_alias_fetch_enabled", True)
    result = await worker.tick()  # same worker instance: no restart
    assert sorted(client.calls) == sorted(r[1] for r in _RECORDED_ROWS)
    assert result == {"looked_up": 4, "matched": 3, "aliases_added": 6}
    assert {k: v.status for k, v in store.lookups.items()} == {
        "deadmau5": "matched", "chvrches": "matched", "bob": "no_match", "techn9ne": "matched",
    }
    assert sorted(store.aliases) == sorted(
        ["deadmouse", "deadmau", "deadmru5", "churches", "tekn9ne", "tecnine"]
    )
    assert alias_fetch_status()["state"] == "done"


async def test_switching_it_off_stops_before_the_next_request(probe, monkeypatch) -> None:
    monkeypatch.setattr(settings, "music_alias_fetch_enabled", True)
    worker, client, _store = _worker(_RECORDED_ROWS)
    client.on_call = lambda name: setattr(settings, "music_alias_fetch_enabled", False)
    await worker.tick()
    assert len(client.calls) == 1
    assert alias_fetch_status()["state"] == "off"


async def test_going_offline_stops_before_the_next_request(probe, enabled) -> None:
    worker, client, _store = _worker(_RECORDED_ROWS)
    client.on_call = lambda name: setattr(probe, "online", False)
    await worker.tick()
    assert len(client.calls) == 1
    assert alias_fetch_status()["state"] == "offline"


async def test_a_rate_limit_pauses_the_fetch(probe, enabled) -> None:
    worker, client, store = _worker(
        _RECORDED_ROWS, answers={"B.o.B": MusicBrainzRateLimited("HTTP 503")}
    )
    await worker.tick()
    assert client.calls == ["B.o.B"]
    assert "bob" not in store.lookups  # not the artist's fault: no row
    status = alias_fetch_status()
    assert status["state"] == "rate_limited" and status["rate_limited_until"] is not None
    until = laf._STATUS.rate_limited_until
    assert until is not None and 55 <= until - time.time() <= 61
    await worker.tick()
    assert client.calls == ["B.o.B"]  # still paused
    laf._STATUS.rate_limited_until = time.time() - 1
    client.answers.clear()
    await worker.tick()
    assert len(client.calls) == 1 + len(_RECORDED_ROWS)


async def test_an_unreachable_server_records_an_error_and_pauses(probe, enabled) -> None:
    worker, client, store = _worker(
        _RECORDED_ROWS, answers={"B.o.B": MusicBrainzUnavailable("ConnectionError")}
    )
    await worker.tick()
    assert client.calls == ["B.o.B"]
    assert store.lookups["bob"].status == "error"
    assert alias_fetch_status()["state"] == "error"
    assert alias_fetch_status()["last_error"] == "unavailable"
    await worker.tick()
    assert client.calls == ["B.o.B"]  # paused


async def test_at_most_a_batch_per_tick_then_the_rest(probe, enabled) -> None:
    rows = [(f"Song {i}", f"Velvet Harbor {i}", None) for i in range(BATCH_SIZE + 5)]
    worker, client, _store = _worker(rows)
    await worker.tick()
    assert len(client.calls) == BATCH_SIZE
    assert alias_fetch_status()["state"] == "running"
    assert alias_fetch_status()["artists_total"] == BATCH_SIZE + 5
    assert alias_fetch_status()["artists_due"] == 5
    await worker.tick()
    assert len(client.calls) == BATCH_SIZE + 5
    assert len(set(client.calls)) == BATCH_SIZE + 5  # nobody twice
    await worker.tick()
    assert len(client.calls) == BATCH_SIZE + 5
    assert alias_fetch_status()["state"] == "done"


async def test_a_new_library_artist_is_topped_up(probe, enabled) -> None:
    worker, client, store = _worker(_RECORDED_ROWS[:1])
    await worker.tick()
    assert client.calls == ["deadmau5"]
    await worker.tick()
    assert client.calls == ["deadmau5"]
    reads = store.calls.count("lookups")
    await worker.tick()  # nothing changed: not even the lookups table is read
    assert store.calls.count("lookups") == reads
    store.rows.append(("Night Bus", "CHVRCHES", None))
    await worker.tick()
    assert client.calls == ["deadmau5", "CHVRCHES"]
    assert "churches" in store.aliases


async def test_a_failed_lookup_is_retried_with_backoff_then_given_up(probe, enabled) -> None:
    worker, client, store = _worker([("Paper Lantern", "Velvet Harbor", None)])
    now = datetime.now(timezone.utc)
    store.lookups["velvetharbor"] = LookupRow("velvetharbor", "error", 1, now)
    await worker.tick()
    assert client.calls == []  # 2^1 h not over yet
    store.lookups["velvetharbor"] = LookupRow("velvetharbor", "error", 2, now - timedelta(hours=3))
    worker._quiet = None
    await worker.tick()
    assert client.calls == []  # 2^2 h not over yet
    store.lookups["velvetharbor"] = LookupRow("velvetharbor", "error", 2, now - timedelta(hours=5))
    worker._quiet = None
    await worker.tick()
    assert client.calls == ["Velvet Harbor"]
    store.lookups["velvetharbor"] = LookupRow("velvetharbor", "error", 5, now - timedelta(days=30))
    worker._quiet = None
    await worker.tick()
    assert client.calls == ["Velvet Harbor"]  # five failures: given up


async def test_an_errored_artist_comes_due_without_a_library_change(probe, enabled) -> None:
    worker, client, store = _worker([("Paper Lantern", "Velvet Harbor", None)])
    store.lookups["velvetharbor"] = LookupRow(
        "velvetharbor", "error", 1, datetime.now(timezone.utc) - timedelta(hours=1, minutes=59)
    )
    await worker.tick()
    assert client.calls == [] and worker._quiet is not None
    # Its retry time passes: the quiet shortcut must not hide it.
    store.lookups["velvetharbor"] = LookupRow(
        "velvetharbor", "error", 1, datetime.now(timezone.utc) - timedelta(hours=3)
    )
    worker._quiet = (worker._quiet[0], datetime.now(timezone.utc) - timedelta(seconds=1))
    await worker.tick()
    assert client.calls == ["Velvet Harbor"]


async def test_new_aliases_tell_the_resolver(probe, enabled, monkeypatch) -> None:
    from domovoi.handlers.shared import library_match

    calls: list[int] = []
    monkeypatch.setattr(library_match, "invalidate", lambda: calls.append(1))
    worker, _client, _store = _worker([("Hollow Pines", "B.o.B", None)])
    await worker.tick()
    assert calls == []  # nothing added, nothing to tell
    worker2, _c2, _s2 = _worker(_RECORDED_ROWS[:1])
    await worker2.tick()
    assert calls == [1]


def test_the_status_shape(probe, enabled) -> None:
    status = alias_fetch_status()
    assert set(status) == {
        "enabled", "state", "last_tick_at", "last_request_at", "last_error",
        "rate_limited_until", "artists_total", "artists_due",
    }
    assert status["enabled"] is True and status["state"] == "idle"


# ─── The real V019 writes ─────────────────────────────────────────────────


_PATHS = itertools.count()


async def _seed_tracks(rows: list[tuple[str, str | None, str | None]]) -> None:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        for title, artist, album in rows:
            i = next(_PATHS)
            await s.execute(text(
                "INSERT INTO library_tracks (file_path, title, artist, album) "
                "VALUES (:p, :t, :a, :al)"
            ), {"p": f"/music/fetch-test/{i:04d}.mp3", "t": title, "a": artist, "al": album})


async def _fetch(sql: str, **params) -> list[tuple]:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return [tuple(r) for r in (await s.execute(text(sql), params)).all()]


@pytest.fixture
async def v019(db_session):
    from domovoi.tests.library_aliases_testkit import apply_v019

    await apply_v019()
    yield
    from domovoi.tests.library_aliases_testkit import clear_library_aliases

    await clear_library_aliases()


@requires_db
async def test_fetched_aliases_land_as_musicbrainz_rows(probe, enabled, v019) -> None:
    await _seed_tracks(_RECORDED_ROWS + [("Tec 9", "Orrin Vance", None)])
    client = _FakeClient()
    worker = LibraryAliasFetcher(client=client)
    await worker.tick()
    assert sorted(client.calls) == sorted(["deadmau5", "CHVRCHES", "B.o.B", "Tech N9ne", "Orrin Vance"])
    rows = await _fetch(
        "SELECT alias, alias_key, key_version, target_type, target_key, target_name, "
        "target_track_id, source, created_by_kind, mb_artist_id, mb_alias_type, suppressed "
        "FROM library_aliases ORDER BY id"
    )
    by_key = {r[1]: r for r in rows}
    # "Tec 9" is a song title in this library: never taken as an alias.
    assert sorted(by_key) == sorted(["deadmouse", "deadmau", "deadmru5", "churches", "tekn9ne"])
    tek = by_key["tekn9ne"]
    assert tek[0] == "Tek N9NE" and tek[2] == 1
    assert tek[3:7] == ("artist", "techn9ne", "Tech N9ne", None)
    assert tek[7:9] == ("musicbrainz", "system")
    assert tek[9] == "dde3d9b1-0e44-48bc-b0c9-d739b3570000"
    assert tek[10] == "Search hint" and tek[11] is False
    lookups = {r[0]: r[1:] for r in await _fetch(
        "SELECT artist_key, status, mb_name, mb_type, aliases_added, aliases_dropped, "
        "attempts, last_error FROM library_alias_lookups"
    )}
    assert lookups["bob"] == ("no_match", None, None, 0, 0, 1, None)
    assert lookups["techn9ne"][:4] == ("matched", "Tech N9ne", "Person", 1)
    assert lookups["techn9ne"][4] >= 1  # "Aaron D. Yates" & co. are counted, never stored
    assert lookups["chvrches"][:4] == ("matched", "CHVRCHES", "Group", 1)
    assert lookups["orrinvance"][0] == "no_match"
    # Every artist is done: another tick asks MusicBrainz nothing.
    await worker.tick()
    assert len(client.calls) == 5
    # A new artist arrives (top-up).
    await _seed_tracks([("Song", "P!nk", None)])
    worker2 = LibraryAliasFetcher(client=client)
    await worker2.tick()
    assert client.calls[-1] == "P!nk"
    assert (await _fetch("SELECT alias FROM library_aliases WHERE target_key = 'pink'")) == [("P.nk",)]


@requires_db
async def test_a_household_alias_is_never_displaced(probe, enabled, v019) -> None:
    from domovoi.db.session import session_scope

    await _seed_tracks([("Paper Lantern", "INXS", None), ("Night Bus", "Velvet Harbor", None)])
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO library_aliases (alias, alias_key, target_type, target_key, "
            "target_name, source, created_by_kind) VALUES ('The Farriss Brothers', "
            "'farrissbrothers', 'artist', 'velvetharbor', 'Velvet Harbor', 'manual', 'admin')"
        ))
    await LibraryAliasFetcher(client=_FakeClient()).tick()
    rows = await _fetch(
        "SELECT alias_key, target_key, source FROM library_aliases ORDER BY alias_key"
    )
    assert rows == [
        ("farrissbrothers", "velvetharbor", "manual"),
        ("inexcess", "inxs", "musicbrainz"),
    ]
    (added,) = (await _fetch(
        "SELECT aliases_added FROM library_alias_lookups WHERE artist_key = 'inxs'"
    ))[0]
    assert added == 1


@requires_db
async def test_a_removed_musicbrainz_alias_stays_removed(probe, enabled, v019) -> None:
    from domovoi.db.session import session_scope

    await _seed_tracks([("Lantern Club", "Tech N9ne", None)])
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO library_aliases (alias, alias_key, target_type, target_key, "
            "target_name, source, created_by_kind, suppressed) VALUES ('Tec 9', 'tecnine', "
            "'artist', 'techn9ne', 'Tech N9ne', 'musicbrainz', 'system', true)"
        ))
    await LibraryAliasFetcher(client=_FakeClient()).tick()
    rows = await _fetch("SELECT alias_key, suppressed FROM library_aliases ORDER BY alias_key")
    assert rows == [("tecnine", True), ("tekn9ne", False)]


@requires_db
async def test_errors_are_recorded_and_counted_up(probe, enabled, v019) -> None:
    await _seed_tracks([("Paper Lantern", "Velvet Harbor", None)])
    store = laf.DbAliasFetchStore()
    for _ in range(2):
        await store.record_error(artist_key="velvetharbor", artist_name="Velvet Harbor",
                                 code="unavailable")
    assert await _fetch(
        "SELECT status, attempts, last_error FROM library_alias_lookups"
    ) == [("error", 2, "unavailable")]
    (row,) = await store.lookup_rows()
    assert (row.key, row.status, row.attempts) == ("velvetharbor", "error", 2)
    # Errored twice just now: not due for 4 h.
    client = _FakeClient()
    await LibraryAliasFetcher(client=client).tick()
    assert client.calls == []


@requires_db
async def test_the_core_snapshot_carries_the_fetch_state(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app

    monkeypatch.setattr(settings, "music_alias_fetch_enabled", False)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/v1/admin/snapshot")
    assert r.status_code == 200, r.text
    fetch = r.json()["music_alias_fetch"]
    assert fetch["enabled"] is False and fetch["state"] == "off"
