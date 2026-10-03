"""The spoken-name resolver (domovoi/handlers/shared/library_match.py +
spoken_index.py) and the music hook that plays what it finds.

The pure half runs on a hand-written synthetic library
(fixtures/spoken_match_library.json: the owner's examples, the contract's
list and invented filler — every name checked against the held-out gate
with ``scripts/eval_spoken_match.py --check-names`` first, none taken from
it). The DB half needs Postgres (``requires_db``) and V019
(``apply_v019``): the fingerprint that keeps the process-wide index
current, and ``MusicHandler._play`` end to end with the MPD stub.

The MPD stub "finds" every search on today's path, so every resolver play
here is proved by a stub whose search methods FAIL the test: a song that
plays came from the resolver's exact file paths, and a miss is a real miss.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from domovoi.clients import mpd as mpd_module
from domovoi.clients.mpd import MPDStubClient, RealMPDClient
from domovoi.config import settings
from domovoi.handlers.music import _PLAY_ANY_RE, _PLAY_ARTIST_RE, MusicHandler
from domovoi.handlers.shared import library_match as lm
from domovoi.handlers.shared import spoken_index as si
from domovoi.handlers.shared.library_match import EntityRef, build_index, resolve
from domovoi.models import Context
from domovoi.tests.conftest import requires_db

FIXTURE = Path(__file__).parent / "fixtures" / "spoken_match_library.json"
TRACKS: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))["tracks"]

PLAY, ASK = 0.90, 0.75


def _index(aliases: list[dict] = ()):  # type: ignore[assignment]
    return build_index(TRACKS, list(aliases))


def _res(query: dict, aliases: list[dict] = (), *, play: float = PLAY, ask: float = ASK, kinds=None):  # type: ignore[assignment]
    return resolve(_index(aliases), query, kinds=kinds, play_threshold=play, ask_threshold=ask)


def _top(res) -> tuple[str, str, tuple[int, ...]]:
    b = res.best
    assert b is not None, res
    return b.ref.type, b.ref.label, b.track_ids


def _alias(alias: str, target_type: str, *, key: str | None = None, track: int | None = None,
           source: str = "manual", artist_key: str | None = None, id_: int = 1) -> dict:
    return {
        "id": id_, "alias": alias, "target_type": target_type, "target_key": key,
        "target_track_id": track, "target_artist_key": artist_key, "source": source,
        "suppressed": False,
    }


# ─── Entities: spellings merge, credits, titles, albums ──────────────────


def test_both_spellings_of_an_artist_are_one_entity_with_all_its_tracks() -> None:
    res = _res({"any": "suicide boys"})
    assert res.decision == "play"
    kind, label, ids = _top(res)
    # $uicideboy$ and $UICIDEBOY$, plus the row it shares with a guest.
    assert (kind, set(ids)) == ("artist", {1, 2, 3, 4})
    assert res.best.via == "exact" and res.best.score == 1.0


def test_an_artist_inside_a_multi_artist_credit_is_reachable_on_its_own() -> None:
    res = _res({"any": "generation"})
    assert res.decision == "play"
    assert _top(res) == ("artist", "GENER8ION", (7,))
    # The Who's "My Generation" is close, but an exact spoken form wins.
    assert any(c.ref.label == "My Generation" for c in res.candidates[1:])
    assert _top(_res({"any": "my generation"}))[:2] == ("title", "My Generation")


def test_a_featured_guest_credit_counts_for_the_guest() -> None:
    kind, label, ids = _top(_res({"any": "tech nine"}))
    assert (kind, label, set(ids)) == ("artist", "Tech N9ne", {4, 5, 6})


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("pink", ("artist", "P!nk")),
        ("pink floyd", ("artist", "Pink Floyd")),
        ("pink pony club", ("title", "Pink Pony Club")),
        ("pink noise", ("title", "Pink noise")),
        ("earth wind and fire", ("credit", "Earth, Wind & Fire")),
        ("motley crue", ("artist", "Mötley Crüe")),
        ("in two deep", ("artist", "IN2DEEP")),
        ("tin roof", ("title", "Tin Roof")),
    ],
)
def test_names_resolve_to_the_right_entity(said: str, expected: tuple[str, str]) -> None:
    res = _res({"any": said})
    assert res.decision == "play"
    assert _top(res)[:2] == expected


def test_a_filename_derived_title_gives_its_artist_too() -> None:
    kind, label, ids = _top(_res({"any": "cold harbor kids"}))
    assert (kind, label, set(ids)) == ("artist", "Cold Harbor Kids", {16, 32})


def test_short_names_match_exactly_only() -> None:
    assert _top(_res({"any": "ti"}))[:2] == ("artist", "T.I.")
    assert _top(_res({"any": "bugatti"}))[:2] == ("title", "Bugatti")
    index = _index()._impl
    # A short form is in no fuzzy pool, and a short request scores nothing
    # fuzzily: "tia" is not T.I.
    assert all("ti" not in pool.compacts for pool in index.pools.values())
    hits = si.score_reading(index, "tia", si.ARTIST_KINDS, phrase=False, cap=PLAY - 0.01)
    assert not hits


def test_an_album_and_a_title_of_the_same_name() -> None:
    # Both exact; the bigger entity (the album) plays.
    assert _top(_res({"any": "lemonade"}))[:2] == ("album", "Lemonade")
    # A carrier word says which one is meant.
    assert _top(_res({"any": "the song lemonade"}))[:2] == ("title", "Lemonade")
    assert _top(_res({"any": "the album lemonade"}))[:2] == ("album", "Lemonade")


def test_albums_are_per_folder_and_play_in_file_order() -> None:
    res = _res({"any": "greatest hits"})
    # The same album name in two folders: two entities, tied → asked.
    assert res.decision == "ask" and res.reason == "tie"
    assert sorted(c.ref.artist_label for c in res.candidates) == ["Brass Lantern", "Juniper Hollow"]
    brass = next(c for c in res.candidates if c.ref.artist_label == "Brass Lantern")
    assert brass.track_ids == (23, 22)  # "01 Ember Road" before "02 Winter Static"


def test_the_same_title_by_two_artists_is_asked_about() -> None:
    res = _res({"any": "in too deep"})
    assert res.decision == "ask" and res.reason == "tie"
    assert {c.ref.artist_label for c in res.candidates} == {"Harbor Lights", "Velvet Antlers"}
    # With asking switched off (ask bar ≥ play bar) the first one plays.
    assert _res({"any": "in too deep"}, ask=0.95, play=0.90).decision == "play"


# ─── "X by Y", carriers ──────────────────────────────────────────────────


def test_title_by_artist_uses_the_by_split() -> None:
    res = _res({"title": "paris dawn", "artist": "suicide boys"})
    assert res.decision == "play"
    assert _top(res) == ("title", "Paris Dawn", (1,))
    assert res.best.via == "by_split"
    assert res.heard == "paris dawn by suicide boys"


def test_by_inside_a_title_stays_a_title() -> None:
    # _PLAY_ARTIST_RE splits "play stand by me" into "stand" by "me".
    m = _PLAY_ARTIST_RE.match("play stand by me")
    assert m
    res = _res({"title": m.group(1), "artist": m.group(2)})
    assert res.decision == "play"
    assert _top(res)[:2] == ("title", "Stand By Me")


def test_a_title_said_without_its_by_reaches_the_title() -> None:
    res = _res({"any": "paris dawn suicide boys"})
    assert res.decision == "play"
    assert _top(res)[:2] == ("title", "Paris Dawn") and res.best.via == "phrase"


@pytest.mark.parametrize(
    "query",
    [
        {"any": "some tech nine"},
        {"any": "music by tech nine"},
        {"any": "the artist tech nine"},
        {"title": "songs", "artist": "tech nine"},
        {"title": "some songs", "artist": "tech nine"},
        {"artist": "tech nine"},
    ],
)
def test_carrier_words_mean_the_artist(query: dict) -> None:
    res = _res(query)
    assert res.decision == "play"
    assert _top(res)[:2] == ("artist", "Tech N9ne")
    assert res.heard == "tech nine"


def test_the_album_x_by_y() -> None:
    res = _res({"title": "the album lemonade", "artist": "juniper hollow"})
    assert res.decision == "play"
    assert _top(res) == ("album", "Lemonade", (17, 18))


def test_the_unstripped_reading_is_scored_too() -> None:
    # "some" is a carrier, but a name may start with it: an entity that
    # matches the whole request beats the stripped reading.
    tracks = TRACKS + [
        {"id": 90, "title": "Lantern Song", "artist": "Somerset Quarry", "album": None,
         "file_path": "Somerset Quarry/Lantern Song.mp3"},
    ]
    index = build_index(tracks)
    res = resolve(index, {"any": "somerset quarry"}, play_threshold=PLAY, ask_threshold=ASK)
    assert _top(res)[:2] == ("artist", "Somerset Quarry")


# ─── Scoring rules ───────────────────────────────────────────────────────


def test_an_entity_that_misses_a_word_is_never_auto_played() -> None:
    res = _res({"any": "glass balloon go"})
    assert res.decision == "ask"
    assert _top(res)[:2] == ("title", "Glass Balloon")
    assert res.best.score == pytest.approx(PLAY - 0.01)


def test_nothing_close_is_none() -> None:
    res = _res({"any": "quartz meridian"})
    assert res.decision == "none"
    assert res.candidates == ()


def test_middle_band_asks_and_an_ask_bar_at_the_play_bar_never_asks() -> None:
    res = _res({"any": "subtract"})
    assert res.decision == "ask"
    assert _top(res)[:2] == ("artist", "SBTRKT")
    assert ASK <= res.best.score < PLAY
    assert _res({"any": "subtract"}, ask=0.90).decision == "none"


def test_kinds_restrict_the_entity_types() -> None:
    res = _res({"any": "lemonade"}, kinds=frozenset({"title"}))
    assert _top(res)[:2] == ("title", "Lemonade")


def test_empty_query() -> None:
    assert _res({"any": "  "}).reason == "empty_query"


# ─── Fragments of a credit, one-word sound-alikes, long requests ─────────
# (2026-10-03 review; every name below checked with --check-names.)

EXTRA = [
    {"id": 40, "title": "Lantern Drift", "artist": "Ashgrove, Tallow & Ember", "album": None,
     "file_path": "Ashgrove, Tallow & Ember/Lantern Drift.mp3"},
    {"id": 41, "title": "Ember", "artist": "Wren Hollow", "album": None,
     "file_path": "Wren Hollow/Ember.mp3"},
    {"id": 42, "title": "Cinder Static", "artist": "Marrow Lane, The Quiet Engineers", "album": None,
     "file_path": "Marrow Lane/Cinder Static.mp3"},
    {"id": 43, "title": "Kettle Bloom", "artist": "Copper Wren feat. Velvet Moth", "album": None,
     "file_path": "Copper Wren/Kettle Bloom.mp3"},
    {"id": 44, "title": "Marlo", "artist": "Wren Hollow", "album": None,
     "file_path": "Wren Hollow/Marlo.mp3"},
    {"id": 45, "title": "Chimera", "artist": "Wren Hollow", "album": None,
     "file_path": "Wren Hollow/Chimera.mp3"},
]


def _res_extra(query: dict, extra: list[dict] = ()):  # type: ignore[assignment]
    index = build_index(TRACKS + EXTRA + list(extra))
    return resolve(index, query, play_threshold=PLAY, ask_threshold=ASK)


def test_a_credit_fragment_never_beats_a_real_name() -> None:
    """ "Ashgrove, Tallow & Ember" splits at its commas into three
    "artists" nobody is called on their own. A song called "Ember" is a
    real name: an exact tie goes to it, not to the band."""
    index = build_index(TRACKS + EXTRA)._impl
    assert index.entity(index.find("artist", "ember")).fragment
    assert not index.entity(index.find("artist", "techn9ne")).fragment     # beside a "Ft."
    assert not index.entity(index.find("artist", "velvetmoth")).fragment   # beside a "feat."
    assert not index.entity(index.find("artist", "pinkfloyd")).fragment    # a credit of its own
    res = _res_extra({"any": "ember"})
    assert res.decision == "play"
    assert _top(res)[:2] == ("title", "Ember")


def test_a_fragment_said_exactly_still_plays_when_nothing_else_is_called_that() -> None:
    res = _res_extra({"any": "ashgrove"})
    assert res.decision == "play"
    assert _top(res) == ("artist", "Ashgrove", (40,))
    # The owner's example: GENER8ION is only ever one of four in a credit.
    assert _top(_res_extra({"any": "generation"}))[:2] == ("artist", "GENER8ION")


def test_a_near_match_to_a_fragment_is_asked_about_never_played() -> None:
    res = _res_extra({"any": "quiet engineer"})
    assert res.decision == "ask"
    assert _top(res)[:2] == ("artist", "The Quiet Engineers")
    # The same performer with a credit of their own is a name: played.
    own = [{"id": 46, "title": "Hollow Signal", "artist": "The Quiet Engineers", "album": None,
            "file_path": "The Quiet Engineers/Hollow Signal.mp3"}]
    res = _res_extra({"any": "quiet engineer"}, own)
    assert res.decision == "play" and _top(res)[:2] == ("artist", "The Quiet Engineers")
    # A guest beside a "feat." is a performer too.
    res = _res_extra({"any": "velvet moths"})
    assert res.decision == "play" and _top(res)[:2] == ("artist", "Velvet Moth")


def test_one_word_that_only_sounds_like_a_name_is_asked_about() -> None:
    """One short word whose spelling is off by more than a letter or so
    ("marlowe" / "Marlo": ratio 0.83) but sounds the same is as often
    another word: asked, not played. Spelled close ("chimeras" /
    "Chimera": 0.93), it plays."""
    res = _res_extra({"any": "marlowe"})
    assert res.decision == "ask" and _top(res)[:2] == ("title", "Marlo")
    res = _res_extra({"any": "chimeras"})
    assert res.decision == "play" and _top(res)[:2] == ("title", "Chimera")
    # Two words are not one word: "glas harbour" still plays Glass Harbor.
    res = _res({"any": "glas harbour"})
    assert res.decision == "play" and _top(res)[:2] == ("title", "Glass Harbor")


def test_a_request_that_is_a_sentence_is_not_a_name() -> None:
    res = _res({"any": " ".join(["paris dawn"] * 13)})
    assert (res.decision, res.reason) == ("none", "too_long")
    res = _res({"title": " ".join(["lantern"] * 20), "artist": " ".join(["suicide boys"] * 3)})
    assert res.reason == "too_long"


def test_a_long_run_of_numbers_resolves_quickly() -> None:
    """The review's blow-up: "play X by Y" with ten years a side took 6.6 s
    and 243 MB; sixteen a side ran out of memory."""
    import time

    index = _index()
    years = " ".join(str(y) for y in range(2000, 2016))
    t0 = time.perf_counter()
    for query in ({"title": years, "artist": years}, {"any": " ".join(["2"] * 23)}):
        resolve(index, query, play_threshold=PLAY, ask_threshold=ASK)
    assert time.perf_counter() - t0 < 2.0


def test_is_library_name_reads_the_index_in_memory() -> None:
    lm.reset_for_tests()
    assert lm.is_library_name("pink floyd") is False  # no index yet
    lm._install(_index([_alias("lantern boys", "artist", key="sbtrkt")]), (0, 0, None), ())
    try:
        assert lm.is_library_name("Pink Floyd") is True
        assert lm.is_library_name("velvet antlers") is True
        assert lm.is_library_name("lantern boys") is False   # an alias, not a library name
        assert lm.is_library_name("") is False
    finally:
        lm.reset_for_tests()


# ─── Aliases ─────────────────────────────────────────────────────────────


def test_a_household_alias_beats_a_library_name_on_an_exact_tie() -> None:
    res = _res({"any": "pink"}, [_alias("pink", "artist", key="pinkfloyd")])
    assert res.decision == "play"
    assert _top(res)[:2] == ("artist", "Pink Floyd")
    assert res.best.via == "alias"


def test_a_library_name_beats_a_musicbrainz_alias_on_an_exact_tie() -> None:
    res = _res({"any": "lemonade"}, [_alias("Lemonade", "artist", key="techn9ne", source="musicbrainz")])
    assert _top(res)[:2] == ("album", "Lemonade")
    assert any(c.ref.label == "Tech N9ne" and c.via == "musicbrainz" for c in res.candidates)


def test_one_target_any_number_of_names() -> None:
    names = ["subtract", "sub track", "the subtractor", "esbee", "lantern boys"]
    aliases = [_alias(n, "artist", key="sbtrkt", id_=i) for i, n in enumerate(names, start=1)]
    index = _index(aliases)
    for n in names:
        res = resolve(index, {"any": n}, play_threshold=PLAY, ask_threshold=ASK)
        assert res.decision == "play", n
        assert _top(res)[:2] == ("artist", "SBTRKT"), n


def test_alias_speak_prefers_a_sounding_household_alias() -> None:
    index = _index([
        _alias("lantern boys", "artist", key="sbtrkt", id_=1),
        _alias("subtract", "artist", key="sbtrkt", id_=2),
    ])._impl
    i = index.find("artist", "sbtrkt")
    assert index.speak(i) == "subtract"  # the one that sounds like the name
    plain = _index([_alias("lantern boys", "artist", key="sbtrkt")])._impl
    assert plain.speak(plain.find("artist", "sbtrkt")) == "SBTRKT"


def test_a_musicbrainz_alias_reaches_a_respelled_name() -> None:
    # Without an alias "churches" is too far from CHVRCHES to play or ask.
    assert _res({"any": "churches"}).decision == "none"
    assert _top(_res({"any": "chvrches"}))[:2] == ("artist", "CHVRCHES")
    res = _res({"any": "churches"}, [_alias("Churches", "artist", key="chvrches", source="musicbrainz")])
    assert res.decision == "play"
    assert _top(res)[:2] == ("artist", "CHVRCHES") and res.best.via == "musicbrainz"
    assert res.best.speak == "Churches"


def test_song_and_album_aliases() -> None:
    aliases = [
        _alias("the pony one", "track", track=12, id_=1),
        _alias("best of brass", "album", key="greatesthits", artist_key="brasslantern", id_=2),
    ]
    res = _res({"any": "the pony one"}, aliases)
    assert res.decision == "play"
    assert _top(res) == ("track", "Pink Pony Club", (12,))
    res = _res({"any": "best of brass"}, aliases)
    assert res.decision == "play"
    assert _top(res) == ("album", "Greatest Hits", (23, 22))


def test_an_alias_whose_target_is_gone_is_inert() -> None:
    index = _index([_alias("ghost band", "artist", key="nosuchartist")])._impl
    assert index.aliases.inert == 1 and index.aliases.attached == 0
    assert _res({"any": "ghost band"}, [_alias("ghost band", "artist", key="nosuchartist")]).decision == "none"


def test_suppressed_aliases_are_ignored() -> None:
    a = _alias("pink", "artist", key="pinkfloyd", source="musicbrainz")
    a["suppressed"] = True
    assert _top(_res({"any": "pink"}, [a]))[:2] == ("artist", "P!nk")


# ─── match_reply ─────────────────────────────────────────────────────────


def test_match_reply_picks_the_parked_entity_a_bare_name_says() -> None:
    index = _index()._impl
    refs = [index.find("artist", "pink"), index.find("artist", "pinkfloyd")]
    assert si.match_reply(index, "pink floyd", refs, play=PLAY, ask=ASK) == (1, 1.0)
    assert si.match_reply(index, "pink", refs, play=PLAY, ask=ASK)[0] == 0
    assert si.match_reply(index, "quartz meridian", refs, play=PLAY, ask=ASK) is None
    assert si.match_reply(index, "pink floyd", [None, refs[1]], play=PLAY, ask=ASK) == (1, 1.0)


def test_match_reply_knows_a_parked_title_or_album_by_its_artist() -> None:
    index = _index()._impl
    albums = list(index.albums_by_name["greatesthits"])
    by = {index.entity(i).artist_label: pos for pos, i in enumerate(albums)}
    for said in ("brass lantern", "the brass lantern one", "greatest hits by brass lantern"):
        hit = si.match_reply(index, said, albums, play=PLAY, ask=ASK)
        assert hit is not None and hit[0] == by["Brass Lantern"], said
    titles = [index.find("title", "intoodeep|harborlights"), index.find("title", "intoodeep|velvetantlers")]
    assert si.match_reply(index, "velvet antlers", titles, play=PLAY, ask=ASK)[0] == 1


# ─── The MPD client: prepare_files ───────────────────────────────────────


class _AckError(Exception):
    """What python-mpd2 raises when MPD refuses a command."""


_AckError.__name__ = "CommandError"


class _RecordingMPD:
    def __init__(self, refuse: set[str] = frozenset(), *, die_on: str | None = None,
                 refuse_seek: bool = False, queue: list[str] | None = None) -> None:
        self.refuse, self.die_on, self.refuse_seek = refuse, die_on, refuse_seek
        self.sent: list[str] = []
        self.queue: list[str] = list(queue or [])

    async def clear(self) -> None:
        self.sent.append("clear")
        self.queue = []

    async def delete(self, rng: str) -> None:
        self.sent.append(f"delete {rng}")
        start, _, end = rng.partition(":")
        del self.queue[int(start):int(end)]

    async def add(self, uri: str) -> None:
        if uri == self.die_on:
            raise ConnectionError("Connection lost")
        if uri in self.refuse:
            raise _AckError("[50@0] {add} Not found")
        self.sent.append(f"add {uri}")
        self.queue.append(uri)

    async def play(self, pos: Any = None) -> None:
        self.sent.append("play" if pos is None else f"play {pos}")

    async def seek(self, pos: Any, t: Any) -> None:
        if self.refuse_seek:
            raise _AckError("[2@0] {seek} Decoder failed to seek")
        self.sent.append(f"seek {pos} {t}")

    async def pause(self, state: Any) -> None:
        self.sent.append(f"pause {state}")

    async def playlistinfo(self) -> list[dict]:
        return [{"file": u, "Id": str(100 + i), "Pos": str(i)} for i, u in enumerate(self.queue)]


def _real(conn: _RecordingMPD) -> RealMPDClient:
    from contextlib import asynccontextmanager

    client = RealMPDClient("127.0.0.1", 1)

    @asynccontextmanager
    async def one():
        yield conn

    client._connect = one  # type: ignore[method-assign]
    return client


async def test_prepare_files_queues_exact_paths_paused_and_skips_refused_ones() -> None:
    conn = _RecordingMPD(refuse={"b.mp3"})
    queued = await _real(conn).prepare_files(["a.mp3", "b.mp3", "c.mp3"])
    assert conn.sent == ["add a.mp3", "add c.mp3", "play 0", "pause 1"]
    assert [(e["file"], e["id"], e["pos"]) for e in queued] == [("a.mp3", 100, 0), ("c.mp3", 101, 1)]


async def test_prepare_files_replaces_the_old_queue_only_once_something_is_in() -> None:
    conn = _RecordingMPD(refuse={"b.mp3"}, queue=["old1.mp3", "old2.mp3"])
    queued = await _real(conn).prepare_files(["a.mp3", "b.mp3"])
    assert conn.sent == ["add a.mp3", "delete 0:2", "play 0", "pause 1"]
    assert conn.queue == ["a.mp3"] and [e["file"] for e in queued] == ["a.mp3"]


async def test_prepare_files_refused_everything_leaves_the_queue_as_it_was() -> None:
    """2026-10-03 review: "play ghost lantern" for a row MPD does not know
    cleared the queue first, so the song that was playing was lost and the
    sweeper stopped the room. Nothing accepted → nothing touched."""
    conn = _RecordingMPD(refuse={"a.mp3", "b.mp3"}, queue=["playing.mp3", "next.mp3"])
    assert await _real(conn).prepare_files(["a.mp3", "b.mp3"]) == []
    assert conn.sent == []
    assert conn.queue == ["playing.mp3", "next.mp3"]
    assert await _real(_RecordingMPD()).prepare_files([]) == []
    stub = MPDStubClient()
    await stub.prepare_files(["A/x.mp3"])
    assert await stub.prepare_files([]) == []
    assert [e["file"] for e in await stub.queue_list()] == ["A/x.mp3"]


async def test_prepare_files_lets_a_dead_connection_raise() -> None:
    with pytest.raises(ConnectionError):
        await _real(_RecordingMPD(die_on="b.mp3")).prepare_files(["a.mp3", "b.mp3"])


async def test_prepare_files_start_sec() -> None:
    conn = _RecordingMPD()
    await _real(conn).prepare_files(["a.mp3"], start_sec=12.7)
    assert conn.sent[-2:] == ["seek 0 12", "pause 1"]
    conn = _RecordingMPD(refuse_seek=True)
    await _real(conn).prepare_files(["a.mp3"], start_sec=12.7)
    assert conn.sent[-2:] == ["play 0", "pause 1"]


def test_a_filename_hit_inside_a_video_id_is_no_hit() -> None:
    """2026-10-03 review: today's filename search matched "kygo" inside
    "[FQKdHGgKygo]" and played an unrelated song."""
    from domovoi.clients.mpd import filename_hit_matches

    path = "uploads/Night Ferry - Paper Comets [FQKdHGgKygo].mp3"
    assert not filename_hit_matches(path, ("kygo",))
    assert filename_hit_matches(path, ("paper comets",))
    assert filename_hit_matches(path, ("NIGHT", "comets"))
    assert filename_hit_matches("Kygo Lights/Song.mp3", ("kygo",))


async def test_the_filename_search_skips_hits_inside_a_video_id() -> None:
    class _Searching(_RecordingMPD):
        async def search(self, *args):
            return [{"file": "a/Song [FQKdHGgKygo].mp3"}, {"file": "b/Kygo Night.mp3"}]

    conn = _Searching()
    song = await _real(conn).prepare_filename("kygo")
    assert song == {"file": "b/Kygo Night.mp3"}
    assert conn.sent == ["clear", "add b/Kygo Night.mp3", "play", "pause 1"]

    class _OnlyTheId(_RecordingMPD):
        async def search(self, *args):
            return [{"file": "a/Song [FQKdHGgKygo].mp3"}]

    conn = _OnlyTheId(queue=["playing.mp3"])
    assert await _real(conn).prepare_filename("kygo") is None
    assert conn.sent == [] and conn.queue == ["playing.mp3"]


async def test_the_stub_prepare_files_is_a_paused_queue() -> None:
    stub = MPDStubClient()
    queued = await stub.prepare_files(["A/x.mp3", "A/y.mp3"])
    assert [e["file"] for e in queued] == ["A/x.mp3", "A/y.mp3"]
    assert [e["pos"] for e in queued] == [0, 1]
    assert await stub.state() == "pause"
    assert (await stub.current_song())["file"] == "A/x.mp3"
    await stub.next()
    assert (await stub.current_song())["file"] == "A/y.mp3"


# ─── The process-wide index and the music hook (Postgres) ────────────────


class _ResolverOnlyMPD(MPDStubClient):
    """Fails the test if today's search path is reached: anything this
    plays came from the resolver's exact paths."""

    def __init__(self) -> None:
        super().__init__()
        self.files_calls: list[list[str]] = []

    async def prepare_search(self, query):
        raise AssertionError(f"today's tag search reached for {query!r}")

    async def prepare_filename(self, *substrings):
        raise AssertionError(f"today's filename search reached for {substrings!r}")

    async def prepare_files(self, uris, *, start_sec: float = 0.0):
        self.files_calls.append(list(uris))
        return await super().prepare_files(uris, start_sec=start_sec)


class _NothingFoundMPD(MPDStubClient):
    """A library MPD has nothing of: today's search misses for real."""

    async def prepare_search(self, query):
        return None

    async def prepare_filename(self, *substrings):
        return None


@pytest.fixture
def music_dir(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "music"
    monkeypatch.setattr(settings, "music_dir", str(root))
    monkeypatch.setattr(settings, "music_match_enabled", True)
    monkeypatch.setattr(settings, "music_match_play_threshold", PLAY)
    monkeypatch.setattr(settings, "music_match_ask_threshold", ASK)
    lm.reset_for_tests()
    yield root
    lm.reset_for_tests()


async def _seed(session, root: Path, tracks: list[dict] = TRACKS) -> None:
    from domovoi.tests.library_aliases_testkit import apply_v019

    await apply_v019()
    for t in tracks:
        await session.execute(
            text(
                "INSERT INTO library_tracks (id, file_path, title, artist, album) "
                "VALUES (:id, :fp, :title, :artist, :album)"
            ),
            {"id": t["id"], "fp": str(root / t["file_path"]), "title": t["title"],
             "artist": t["artist"], "album": t["album"]},
        )
    await session.execute(text(
        "SELECT setval(pg_get_serial_sequence('library_tracks', 'id'), "
        "(SELECT max(id) FROM library_tracks))"
    ))
    await session.commit()


async def _ctx(session, room: str = "kitchen", *, online: bool = False) -> Context:
    from domovoi.db.repositories import SessionRepository

    sid = await SessionRepository(session).get_or_create(None, room)
    await session.commit()
    return Context(session_id=sid, room_id=room, online=online)


async def _say(handler: MusicHandler, said: str, ctx: Context, session):
    m = _PLAY_ARTIST_RE.match(said)
    if m:
        return await handler._play_artist_from_match(m, ctx, session)
    m = _PLAY_ANY_RE.match(said)
    assert m, said
    return await handler._play_any_from_match(m, ctx, session)


@requires_db
async def test_play_an_artist_queues_all_its_tracks_shuffled_by_exact_path(db_session, music_dir) -> None:
    await _seed(db_session, music_dir)
    mpd = _ResolverOnlyMPD()
    mpd_module._clients = {"kitchen": mpd}
    ctx = await _ctx(db_session)
    random.seed(7)

    r = await _say(MusicHandler(), "play suicide boys", ctx, db_session)
    await db_session.commit()

    assert r.text == "Playing Suicide Boys, shuffled."
    assert r.music_action == "start"
    expected = {
        "$uicideboy$/Gloom Harbor/01 Paris Dawn.mp3",
        "$uicideboy$/Gloom Harbor/02 Static Choir.mp3",
        "$uicideboy$/Gloom Harbor/03 Low Tide Prayer.mp3",
        "$uicideboy$/Singles/Copper Rain.mp3",
    }
    assert len(mpd.files_calls) == 1 and set(mpd.files_calls[0]) == expected
    assert r.data["resolved"]["ref"]["label"] == "$uicideboy$"
    assert r.data["resolved"]["queued"] == 4
    assert await mpd.state() == "pause"

    # The play is recorded against the library row that plays first.
    first_file = mpd.files_calls[0][0]
    played = (await db_session.execute(text(
        "SELECT library_track_id, title FROM media_plays ORDER BY id DESC LIMIT 1"
    ))).one()
    expected_id = next(t["id"] for t in TRACKS if t["file_path"] == first_file)
    assert played.library_track_id == expected_id

    from domovoi.db.repositories import SessionRepository

    state = await SessionRepository(db_session).get_context(ctx.session_id)
    assert state["last_play_source"] == "local"
    assert state["last_played_track"]["file"] == first_file


@requires_db
async def test_next_and_previous_stay_with_the_artist_and_the_queue_ends(db_session, music_dir) -> None:
    await _seed(db_session, music_dir)
    mpd = _ResolverOnlyMPD()
    mpd_module._clients = {"kitchen": mpd}
    ctx = await _ctx(db_session)
    handler = MusicHandler()
    await _say(handler, "play tech nine", ctx, db_session)
    queue = [e["file"] for e in await mpd.queue_list()]
    assert len(queue) == 3
    await mpd.resume()

    r = await handler._smart_skip(ctx, db_session)
    assert r.text == "Next track." and (await mpd.current_song())["file"] == queue[1]
    r = await handler._simple_ack("previous", ctx)
    assert (await mpd.current_song())["file"] == queue[0]
    await handler._smart_skip(ctx, db_session)
    await handler._smart_skip(ctx, db_session)
    assert (await mpd.current_song())["file"] == queue[2]
    r = await handler._smart_skip(ctx, db_session)
    assert r.text == "That was the last song in the queue."
    assert r.music_action == "stop"


@requires_db
async def test_titles_albums_and_the_readback(db_session, music_dir) -> None:
    await _seed(db_session, music_dir)
    mpd = _ResolverOnlyMPD()
    mpd_module._clients = {"kitchen": mpd}
    ctx = await _ctx(db_session)
    handler = MusicHandler()

    r = await _say(handler, "play paris dawn by suicide boys", ctx, db_session)
    assert r.text == "Playing Paris Dawn by Suicide Boys."
    assert mpd.files_calls[-1] == ["$uicideboy$/Gloom Harbor/01 Paris Dawn.mp3"]

    r = await _say(handler, "play the album strange engines", ctx, db_session)
    assert r.text == "Playing the album Strange Engines by Tech N9ne."
    assert mpd.files_calls[-1] == [
        "Tech N9ne/Strange Engines/01 Iron Lantern.mp3",
        "Tech N9ne/Strange Engines/02 Paper Wolves.mp3",
    ]

    # One-song artist: says the song.
    r = await _say(handler, "play generation", ctx, db_session)
    assert r.text == "Playing Neon Ferry by Generation."

    # Not exact → no echo: the speakable library spelling.
    r = await _say(handler, "play wildfire line", ctx, db_session)
    assert r.text == "Playing Wildfire Lines by SBTRKT."


@requires_db
async def test_a_real_miss_runs_todays_cascade(db_session, music_dir) -> None:
    await _seed(db_session, music_dir)
    mpd_module._clients = {"kitchen": _NothingFoundMPD()}
    ctx = await _ctx(db_session, online=False)
    r = await _say(MusicHandler(), "play quartz meridian", ctx, db_session)
    assert r.text == "I couldn't find anything called quartz meridian."
    assert r.music_action is None


@requires_db
async def test_a_middle_band_match_without_the_dialog_runs_todays_cascade(
    db_session, music_dir, monkeypatch
) -> None:
    await _seed(db_session, music_dir)
    mpd_module._clients = {"kitchen": _NothingFoundMPD()}
    ctx = await _ctx(db_session, online=False)
    handler = MusicHandler()
    # The dialog (when it is in this build) asks only while it is switched on.
    monkeypatch.setattr(settings, "music_choice_enabled", False)
    r = await _say(handler, "play subtract", ctx, db_session)
    assert r.text == "I couldn't find anything called subtract."

    # With a dialog it asks; allow_choice=False never does.
    asked: list = []

    async def ask(res, query, ctx_, session_):
        asked.append((res.decision, res.best.ref.label, query))
        from domovoi.models import Response

        return Response(text=f"Did you mean {res.best.speak}?", expect_followup=True)

    handler.ask_did_you_mean = ask  # type: ignore[attr-defined]
    r = await _say(handler, "play subtract", ctx, db_session)
    assert r.text == "Did you mean SBTRKT?"
    assert asked == [("ask", "SBTRKT", {"any": "subtract"})]
    r = await handler._play({"any": "subtract"}, ctx, db_session, allow_choice=False)
    assert r.text == "I couldn't find anything called subtract."
    assert len(asked) == 1


@requires_db
async def test_switched_off_is_todays_path_exactly(db_session, music_dir, monkeypatch) -> None:
    await _seed(db_session, music_dir)
    monkeypatch.setattr(settings, "music_match_enabled", False)
    stub = MPDStubClient()
    mpd_module._clients = {"kitchen": stub}
    ctx = await _ctx(db_session)
    statements: list[str] = []
    real_execute = db_session.execute

    async def spy(stmt, *a, **kw):
        statements.append(str(stmt))
        return await real_execute(stmt, *a, **kw)

    monkeypatch.setattr(db_session, "execute", spy)
    r = await _say(MusicHandler(), "play suicide boys", ctx, db_session)
    # The stub's tag search answers, as it always has.
    assert r.text == "Playing suicide boys by Stub Artist."
    assert r.data == {"song": {"file": "stub/suicide boys.mp3", "title": "suicide boys", "artist": "Stub Artist"}}
    assert not any("library_aliases" in s or "count(*) FROM library_tracks" in s for s in statements)
    assert lm.index_status()["state"] == "not_built"


@requires_db
async def test_nothing_queued_and_an_unreachable_player(db_session, music_dir) -> None:
    await _seed(db_session, music_dir)
    ctx = await _ctx(db_session)

    class _Empty(_ResolverOnlyMPD):
        async def prepare_files(self, uris, *, start_sec: float = 0.0):
            return []

    class _Down(_ResolverOnlyMPD):
        async def prepare_files(self, uris, *, start_sec: float = 0.0):
            raise ConnectionError("Connection refused")

    mpd_module._clients = {"kitchen": _Empty()}
    r = await _say(MusicHandler(), "play pink floyd", ctx, db_session)
    assert r.text == (
        "I found Pink Floyd, but the music player couldn't find the files. "
        "Try 'rescan my library'."
    )
    mpd_module._clients = {"kitchen": _Down()}
    r = await _say(MusicHandler(), "play pink floyd", ctx, db_session)
    assert (r.text, r.failure) == ("I couldn't reach the music player.", "unreachable")


@requires_db
async def test_rows_outside_the_music_dir_are_found_by_mpds_own_search(db_session, music_dir) -> None:
    outside = [dict(t, file_path=t["file_path"]) for t in TRACKS]
    await _seed(db_session, music_dir.parent / "elsewhere", outside)
    calls: list = []

    class _Tracks(_ResolverOnlyMPD):
        async def prepare_tracks(self, specs, *, start_sec: float = 0.0):
            calls.append(specs)
            return await MPDStubClient.prepare_tracks(self, specs, start_sec=start_sec)

    mpd = _Tracks()
    mpd_module._clients = {"kitchen": mpd}
    ctx = await _ctx(db_session)
    r = await _say(MusicHandler(), "play hollow clock", ctx, db_session)
    assert r.text == "Playing Hollow Clock by Pink Floyd."
    assert mpd.files_calls == []
    assert calls and calls[0][0]["title"] == "Hollow Clock"


@requires_db
async def test_the_index_follows_the_library_and_the_aliases(db_session, music_dir) -> None:
    await _seed(db_session, music_dir)
    res = await lm.resolve_request(db_session, {"any": "quartz meridian"})
    assert res.decision == "none"
    assert lm.index_status()["state"] == "ready"

    # A new row (an upload, a rescan).
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title, artist) "
        "VALUES (:fp, 'Prism Gate', 'Quartz Meridian')"
    ), {"fp": str(music_dir / "Quartz Meridian" / "Prism Gate.mp3")})
    res = await lm.resolve_request(db_session, {"any": "quartz meridian"})
    assert (res.decision, res.best.ref.label) == ("play", "Quartz Meridian")

    # A dashboard tag edit (stamps enriched_at).
    await db_session.execute(text(
        "UPDATE library_tracks SET artist = 'Quartz Meridians', enriched_at = now() + interval '1 second' "
        "WHERE title = 'Prism Gate'"
    ))
    res = await lm.resolve_request(db_session, {"any": "quartz meridians"})
    assert res.best.ref.label == "Quartz Meridians" and res.best.score == 1.0

    # An alias (dashboard or voice): the very next request sees it.
    await db_session.execute(text(
        "INSERT INTO library_aliases (alias, alias_key, target_type, target_key, target_name, "
        "source, created_by_kind) VALUES ('crystal door', 'crystaldoor', 'artist', "
        "'quartzmeridians', 'Quartz Meridians', 'manual', 'admin')"
    ))
    res = await lm.resolve_request(db_session, {"any": "crystal door"})
    assert (res.decision, res.best.ref.label, res.best.via) == ("play", "Quartz Meridians", "alias")
    lm.invalidate()
    res = await lm.resolve_request(db_session, {"any": "crystal door"})
    assert res.best.via == "alias"


@requires_db
async def test_a_track_renamed_in_place_by_a_reingest_is_heard_on_the_next_request(
    db_session, music_dir, monkeypatch
) -> None:
    """2026-10-03 review: the provider pipeline's re-ingest
    (LibraryAPI.ingest_track, same source id) rewrites title / artist in
    place without moving added_at / enriched_at, so the fingerprint stayed
    put and the old name kept playing. The ingest now tells the resolver."""
    from domovoi.sdk.library import LibraryAPI

    await _seed(db_session, music_dir)
    mpd_module._clients = {"kitchen": MPDStubClient()}
    monkeypatch.setattr(lm, "REBUILD_DEBOUNCE_SEC", 60.0)  # the request rebuilds, not the timer
    api = LibraryAPI()
    path = music_dir / "Quartz Meridian" / "Prism Gate.mp3"
    await api.ingest_track(
        db_session, file_path=path, title="Prism Gate", artist="Quartz Meridian",
        source="spoken-match-test", source_id="pg-1", added_via="manual",
    )
    res = await lm.resolve_request(db_session, {"any": "quartz meridian"})
    assert (res.decision, res.best.ref.label) == ("play", "Quartz Meridian")

    fp_before = (await lm._fingerprint(db_session))[0]
    await api.ingest_track(
        db_session, file_path=path, title="Prism Gate", artist="Cobalt Vesper",
        source="spoken-match-test", source_id="pg-1", added_via="manual",
    )
    # The fingerprint really cannot see it…
    assert (await lm._fingerprint(db_session))[0] == fp_before
    # …and yet the very next request hears the new name, not the old one.
    res = await lm.resolve_request(db_session, {"any": "cobalt vesper"})
    assert (res.decision, res.best.ref.label) == ("play", "Cobalt Vesper")
    assert (await lm.resolve_request(db_session, {"any": "quartz meridian"})).decision == "none"


@requires_db
async def test_a_household_name_that_takes_over_a_library_name_is_not_echoed(
    db_session, music_dir
) -> None:
    """2026-10-03 review: someone taught "velvet antlers" (a band in the
    library) to mean SBTRKT. "Playing Velvet Antlers" would hide that the
    real Velvet Antlers is not what plays — the reply says the target."""
    await _seed(db_session, music_dir)
    for i, alias in enumerate(("velvet antlers", "lantern boys"), start=1):
        await db_session.execute(text(
            "INSERT INTO library_aliases (alias, alias_key, target_type, target_key, target_name, "
            "source, created_by_kind) VALUES (:a, :k, 'artist', 'sbtrkt', 'SBTRKT', 'manual', 'admin')"
        ), {"a": alias, "k": alias.replace(" ", "")})
    await db_session.commit()
    mpd = _ResolverOnlyMPD()
    mpd_module._clients = {"kitchen": mpd}
    ctx = await _ctx(db_session)
    handler = MusicHandler()

    r = await _say(handler, "play velvet antlers", ctx, db_session)
    assert r.data["resolved"]["ref"]["label"] == "SBTRKT" and r.data["resolved"]["via"] == "alias"
    assert r.text == "Playing Wildfire Lines by SBTRKT."
    # A household name that is nobody else's is said back as it was said.
    r = await _say(handler, "play lantern boys", ctx, db_session)
    assert r.text == "Playing Wildfire Lines by Lantern Boys."


@requires_db
async def test_a_turned_down_entity_goes_on_to_todays_search(db_session, music_dir) -> None:
    """``_play(..., avoid=...)``: what the did-you-mean dialog passes after
    "no, I said <the same words>" — the resolver's answer is skipped when
    it is one of them, and today's search answers instead."""
    await _seed(db_session, music_dir)
    mpd_module._clients = {"kitchen": _NothingFoundMPD()}
    ctx = await _ctx(db_session, online=False)
    handler = MusicHandler()
    res = await lm.resolve_request(db_session, {"any": "wildfire lines"})
    assert res.decision == "play"
    r = await handler._play({"any": "wildfire lines"}, ctx, db_session, avoid=(res.best.ref,))
    assert r.text == "I couldn't find anything called wildfire lines."
    assert r.music_action is None


@requires_db
async def test_a_big_library_rebuilds_in_the_background(db_session, music_dir, monkeypatch) -> None:
    await _seed(db_session, music_dir)
    monkeypatch.setattr(lm, "INLINE_REBUILD_MAX_TRACKS", 0)
    monkeypatch.setattr(lm, "REBUILD_DEBOUNCE_SEC", 0.0)
    assert (await lm.resolve_request(db_session, {"any": "pink floyd"})).decision == "play"
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title, artist) "
        "VALUES (:fp, 'Prism Gate', 'Quartz Meridian')"
    ), {"fp": str(music_dir / "Quartz Meridian" / "Prism Gate.mp3")})
    await db_session.commit()
    # The old index answers at once, and a rebuild starts.
    assert (await lm.resolve_request(db_session, {"any": "quartz meridian"})).decision == "none"
    task = lm._STATE.rebuild_task
    assert task is not None
    await task
    res = await lm.resolve_request(db_session, {"any": "quartz meridian"})
    assert (res.decision, res.best.ref.label) == ("play", "Quartz Meridian")


@requires_db
async def test_without_the_alias_table_it_matches_library_names_and_warns_once(
    db_session, music_dir, monkeypatch, caplog
) -> None:
    await _seed(db_session, music_dir)
    no_v019 = text(
        "SELECT (SELECT count(*) FROM library_tracks),"
        " (SELECT coalesce(max(id), 0) FROM library_tracks),"
        " (SELECT max(added_at) FROM library_tracks), false"
    )
    monkeypatch.setattr(lm, "_LIBRARY_FP_SQL", no_v019)
    with caplog.at_level(logging.WARNING, logger=lm.__name__):
        a = await lm.resolve_request(db_session, {"any": "pink floyd"})
        b = await lm.resolve_request(db_session, {"any": "pink"})
    assert (a.decision, b.decision) == ("play", "play")
    assert sum("library_aliases" in r.getMessage() for r in caplog.records) == 1


@requires_db
async def test_parked_refs_come_back_as_they_are_now(db_session, music_dir) -> None:
    await _seed(db_session, music_dir)
    res = await lm.resolve_request(db_session, {"any": "greatest hits"})
    assert res.decision == "ask"
    refs = [c.ref for c in res.candidates]
    parked = [c.to_dict() for c in res.candidates]

    cand = await lm.candidate_from_ref(db_session, parked[0]["ref"])
    assert cand is not None and cand.ref == refs[0] and cand.track_ids
    hit = await lm.match_reply(db_session, "brass lantern", refs)
    assert hit is not None
    assert refs[hit[0]].artist_label == "Brass Lantern"
    rows = await lm.tracks_for(db_session, refs[0])
    assert [r.id for r in rows] == list(cand.track_ids)
    assert await lm.speak_for(db_session, EntityRef("artist", "pink", "P!nk")) == "Pink"

    # A track that left the library.
    await db_session.execute(text("DELETE FROM library_tracks WHERE id = 31"))
    gone = await lm.candidate_from_ref(db_session, {"type": "track", "key": "31", "label": "Neon Hammer"})
    assert gone is None
    assert await lm.candidate_from_ref(db_session, {"type": "bogus"}) is None


async def test_no_session_and_errors_answer_none(monkeypatch) -> None:
    monkeypatch.setattr(settings, "music_match_enabled", True)
    lm.reset_for_tests()
    assert (await lm.resolve_request(None, {"any": "pink"})).reason == "no_session"

    class _Broken:
        async def execute(self, *a, **kw):
            raise RuntimeError("database is down")

    res = await lm.resolve_request(_Broken(), {"any": "pink"})
    assert (res.decision, res.reason, res.heard) == ("none", "error", "pink")
    monkeypatch.setattr(settings, "music_match_enabled", False)
    assert (await lm.resolve_request(_Broken(), {"any": "pink"})).reason == "disabled"
    lm.reset_for_tests()
