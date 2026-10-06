"""Lyric search's decision: ``lyric_search.decide``, the phrase rules
(``lyric_query``) and the line → entity step (``to_entities``)
(lyrics-build contract [Q26], [Q30]–[Q40]).

Every branch of [Q40] with the Settings DEFAULTS (0.90 / 0.75 — the same
bands as names), an ask bar at or above the play bar never asking, the
artist filter and ``avoid``. Pure or faked; never skips. Every line and
name here is invented.
"""

from __future__ import annotations

import contextlib

import pytest

from domovoi.config import Settings
from domovoi.handlers.shared import library_match
from domovoi.handlers.shared import lyric_search as ls
from domovoi.handlers.shared.library_match import Candidate, EntityRef, build_index
from domovoi.handlers.shared.lyric_search import (
    MAX_WORDS,
    LyricEntity,
    LyricQuery,
    RowScore,
    decide,
    lyric_query,
    to_entities,
)

DEFAULTS = Settings()
PLAY = DEFAULTS.music_match_play_threshold
ASK = DEFAULTS.music_match_ask_threshold

SEVEN = lyric_query("the lantern hums beside the river door")
FIVE = lyric_query("the lantern hums beside the")
SIX = lyric_query("the lantern hums beside the river")
FOUR = lyric_query("copper kettles sing loudly")
THREE = lyric_query("copper kettles singing")


def test_the_bands_are_the_names_bands() -> None:
    assert (PLAY, ASK) == (0.90, 0.75)


def _ent(n: int, score: float, *, exact: bool = False, repeats: int = 2, tracks: int = 1) -> LyricEntity:
    ref = EntityRef(type="title", key=f"song{n}|exampleband", label=f"Song {n}", artist_label="The Example Band")
    cand = Candidate(ref=ref, score=score, via="lyrics", speak=f"Song {n}",
                     track_ids=tuple(range(n * 10, n * 10 + tracks)))
    return LyricEntity(cand, score, exact, repeats)


def _decide(query: LyricQuery, ents, mode: str = "explicit", *, play: float = PLAY, ask: float = ASK):
    return decide(query, ents, mode=mode, play=play, ask=ask, heard=query.text)


def _labels(res) -> list[str]:
    return [c.ref.label for c in res.candidates]


# ─── The phrase ([Q26]) ───────────────────────────────────────────────────


def test_leading_filler_and_one_trailing_carrier_are_dropped() -> None:
    q = lyric_query("um uh like the lantern hums beside the river door in my library")
    assert q.text == "the lantern hums beside the river door"
    # One carrier only, and only at the end.
    q = lyric_query("in my library the kettle sings in my music in my collection")
    assert q.text == "in my library the kettle sings in my music"
    # Filler only at the start: "like" inside a lyric is a word of it.
    assert lyric_query("the river door is like a kettle").text == "the river door is like a kettle"


def test_trailing_politeness_is_dropped_however_much_there_is() -> None:
    """2026-10-06 review: "… please" cost a sure play (one word the line
    lacks is 1/n of the score); politeness is no word of the song."""
    seven = "the lantern hums beside the river door"
    for tail in ("please", "thanks", "thank you", "for me", "now", "for me please",
                 "please thank you", "in my library please", "please in my library"):
        assert lyric_query(f"{seven} {tail}").text == seven, tail
    # only at the end, and never the whole phrase
    assert lyric_query("please let the river door be open").text == "please let the river door be open"
    assert lyric_query("please").text == "please"
    assert lyric_query("thank you").text == "thank you"
    # one carrier, as before, whatever politeness comes around it
    assert lyric_query(f"{seven} in my music in my library please").text == f"{seven} in my music"


def test_long_phrases_are_cut_and_chars_count_letters() -> None:
    q = lyric_query(" ".join(["kettle"] * 40))
    assert len(q) == MAX_WORDS
    assert lyric_query("a b c d e f").chars == 6
    assert lyric_query("River-door, open!").words == ("river", "door", "open")


# ─── [Q40], branch by branch ──────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["explicit", "identify", "fallback"])
def test_too_short(mode: str) -> None:
    assert _decide(lyric_query("the kettle"), [_ent(1, 1.0, exact=True)], mode).reason == "too_short"
    # Three words but fewer than twelve letters.
    assert _decide(lyric_query("oh my my"), [_ent(1, 1.0, exact=True)], mode).reason == "too_short"


def test_the_last_resort_needs_four_words_to_look_at_all() -> None:
    res = _decide(THREE, [_ent(1, 1.0, exact=True)], "fallback")
    assert (res.decision, res.reason) == ("none", "too_short")
    # The explicit request with the same words looks (and may ask).
    assert _decide(THREE, [_ent(1, 1.0, exact=True)], "explicit").decision == "ask"


def test_nothing_or_nothing_in_the_ask_band() -> None:
    assert _decide(SEVEN, []).reason == "below_ask"
    assert _decide(SEVEN, [_ent(1, 0.74)]).reason == "below_ask"


def test_more_than_three_songs_as_good_as_the_best_are_too_common() -> None:
    ents = [_ent(1, 1.0, exact=True), _ent(2, 0.99), _ent(3, 0.97), _ent(4, 0.96)]
    assert _decide(SEVEN, ents).reason == "too_common"
    # The fourth more than MARGIN below: three near ones are asked about.
    ents[3] = _ent(4, 0.94)
    res = _decide(SEVEN, ents)
    assert (res.decision, res.reason) == ("ask", "tie")
    assert _labels(res) == ["Song 1", "Song 2", "Song 3"]


@pytest.mark.parametrize("mode", ["explicit", "identify"])
def test_an_explicit_request_plays_a_sure_match_of_four_words_or_more(mode: str) -> None:
    res = _decide(SEVEN, [_ent(1, 1.0, exact=True), _ent(2, 0.80)], mode)
    assert (res.decision, res.reason) == ("play", "lyrics")
    assert _labels(res) == ["Song 1"]           # a play's candidates are the song alone
    res = _decide(FOUR, [_ent(1, 0.93)], mode)
    assert res.decision == "play"
    # Three words never play: at most a question.
    res = _decide(THREE, [_ent(1, 1.0, exact=True)], mode)
    assert (res.decision, res.reason) == ("ask", "tie")


def test_the_last_resort_plays_only_an_exact_phrase_of_six_words_or_more() -> None:
    assert (_decide(SIX, [_ent(1, 1.0, exact=True)], "fallback").reason) == "lyrics_exact"
    assert _decide(SEVEN, [_ent(1, 0.97, exact=True, repeats=1)], "fallback").decision == "play"
    # Exact but five words: asked about.
    res = _decide(FIVE, [_ent(1, 1.0, exact=True)], "fallback")
    assert (res.decision, res.reason) == ("ask", "tie")
    # Six words, sure, but not exact: asked about.
    res = _decide(SEVEN, [_ent(1, 0.97, exact=False)], "fallback")
    assert res.decision == "ask"


def test_the_ask_band_asks_and_a_tie_at_the_top_asks_too() -> None:
    res = _decide(SEVEN, [_ent(1, 0.83), _ent(2, 0.60)])
    assert (res.decision, res.reason) == ("ask", "lyrics")
    assert _labels(res) == ["Song 1"]
    res = _decide(SEVEN, [_ent(1, 1.0, exact=True), _ent(2, 1.0, exact=True)])
    assert (res.decision, res.reason) == ("ask", "tie")
    assert len(res.candidates) == 2


def test_an_ask_bar_at_or_above_the_play_bar_never_asks() -> None:
    for ask in (PLAY, 0.95):
        res = _decide(SEVEN, [_ent(1, 0.85)], ask=ask)
        assert res.decision == "none" and res.reason == "below_ask"
        res = _decide(SEVEN, [_ent(1, 1.0), _ent(2, 1.0)], ask=ask)
        assert (res.decision, res.reason) == ("none", "below_play")
        assert _decide(SEVEN, [_ent(1, 1.0)], ask=ask).decision == "play"


def test_below_play_when_a_sure_match_cannot_play_and_asking_is_off() -> None:
    res = _decide(FIVE, [_ent(1, 1.0, exact=True)], "fallback", ask=1.0)
    assert (res.decision, res.reason) == ("none", "below_play")


def test_ranking_score_then_repeats_then_tracks_then_key() -> None:
    a, b = _ent(1, 0.85, repeats=1), _ent(2, 0.85, repeats=3)
    assert _labels(_decide(SEVEN, [a, b])) == ["Song 2", "Song 1"]
    a, b = _ent(1, 0.85, tracks=1), _ent(2, 0.85, tracks=2)
    assert _labels(_decide(SEVEN, [a, b])) == ["Song 2", "Song 1"]
    a, b = _ent(2, 0.85), _ent(1, 0.85)
    assert _labels(_decide(SEVEN, [a, b])) == ["Song 1", "Song 2"]


def test_every_lyric_candidate_says_how_it_was_found() -> None:
    res = _decide(SEVEN, [_ent(1, 1.0, exact=True)])
    assert res.best.via == "lyrics" and res.heard == SEVEN.text


# ─── Lines → entities ([Q30]–[Q35]), the index faked ──────────────────────

TRACKS = [
    {"id": 1, "title": "Lantern Song", "artist": "The Example Band", "album": "Paper Hall",
     "file_path": "/m/The Example Band/Paper Hall/01 Lantern Song.mp3"},
    {"id": 2, "title": "Lantern Song", "artist": "The Example Band", "album": "Live at the Hall",
     "file_path": "/m/The Example Band/Live at the Hall/04 Lantern Song.mp3"},
    {"id": 3, "title": "Copper Morning", "artist": "Glass Harbor", "album": "Tidewater",
     "file_path": "/m/Glass Harbor/Tidewater/02 Copper Morning.mp3"},
    {"id": 4, "title": "Attic Light", "artist": "The Velvet Kites, Glass Harbor", "album": "Rooftops",
     "file_path": "/m/The Velvet Kites/Rooftops/05 Attic Light.mp3"},
    {"id": 5, "title": None, "artist": "The Velvet Kites", "album": "Rooftops",
     "file_path": "/m/The Velvet Kites/Rooftops/Track 07.mp3"},
]


class _Session:
    """Enough of an AsyncSession for to_entities: savepoints that do
    nothing (the database calls are faked)."""

    def begin_nested(self):
        return contextlib.AsyncExitStack()


@pytest.fixture
def faked_index(monkeypatch):
    index = build_index(TRACKS)

    async def current_index(session):
        return index

    async def rows_by_id(session, ids):
        by_id = {t["id"]: t for t in TRACKS}
        return [
            library_match.TrackRow(id=i, title=by_id[i]["title"], artist=by_id[i]["artist"],
                                   album=by_id[i]["album"], file_path=by_id[i]["file_path"])
            for i in ids if i in by_id
        ]

    monkeypatch.setattr(library_match, "current_index", current_index)
    monkeypatch.setattr(library_match, "rows_by_id", rows_by_id)
    return index


def _row(tid: int, score: float, *, exact: bool = False, repeats: int = 2, line_no: int = 0) -> RowScore:
    return RowScore(track_id=tid, score=score, exact=exact, repeats=repeats, span=1, line_no=line_no)


async def test_copies_of_one_song_are_one_title_candidate(faked_index) -> None:
    ents, dropped = await to_entities(_Session(), [_row(1, 1.0, exact=True), _row(2, 0.97, repeats=1)])
    assert not dropped
    (only,) = ents
    ref = only.candidate.ref
    assert (ref.type, ref.label, ref.artist_label) == ("title", "Lantern Song", "The Example Band")
    assert set(only.candidate.track_ids) == {1, 2}
    assert only.score == 1.0 and only.exact and only.candidate.via == "lyrics"


async def test_a_song_with_no_title_entity_is_a_track_candidate(faked_index) -> None:
    ents, _ = await to_entities(_Session(), [_row(5, 0.9)])
    (only,) = ents
    ref = only.candidate.ref
    assert (ref.type, ref.key, ref.label, ref.artist_label) == ("track", "5", "Track 07", "The Velvet Kites")
    assert only.candidate.track_ids == (5,) and only.candidate.speak == "Track 07"


async def test_without_an_index_every_hit_is_a_track_candidate(faked_index, monkeypatch) -> None:
    async def broken(session):
        raise RuntimeError("index build failed")

    monkeypatch.setattr(library_match, "current_index", broken)
    ents, _ = await to_entities(_Session(), [_row(1, 1.0), _row(3, 0.8)])
    assert sorted((e.candidate.ref.type, e.candidate.ref.label) for e in ents) == [
        ("track", "Copper Morning"), ("track", "Lantern Song")]


async def test_the_best_line_scores_the_song_exact_then_repeats_break_ties(faked_index) -> None:
    ents, _ = await to_entities(_Session(), [
        _row(3, 0.9, exact=False, repeats=5, line_no=1),
        _row(3, 0.9, exact=True, repeats=1, line_no=2),
        _row(3, 0.8, exact=True, repeats=9, line_no=3),
    ])
    (only,) = ents
    assert (only.score, only.exact, only.repeats) == (0.9, True, 1)


async def test_the_artist_filter_keeps_songs_by_someone_who_sounds_like_it(faked_index) -> None:
    rows = [_row(1, 1.0), _row(3, 0.9), _row(4, 0.9)]
    ents, _ = await to_entities(_Session(), rows, artist="glass harbour")
    assert sorted(e.candidate.ref.label for e in ents) == ["Attic Light", "Copper Morning"]
    ents, _ = await to_entities(_Session(), rows, artist="the example band")
    assert [e.candidate.ref.label for e in ents] == ["Lantern Song"]
    ents, _ = await to_entities(_Session(), rows, artist="the marmalade orchestra")
    assert ents == []


async def test_avoid_drops_what_was_just_turned_down(faked_index) -> None:
    ents, dropped = await to_entities(_Session(), [_row(1, 1.0), _row(3, 0.9)])
    lantern = next(e.candidate.ref for e in ents if e.candidate.ref.label == "Lantern Song")
    ents, dropped = await to_entities(_Session(), [_row(1, 1.0), _row(3, 0.9)], avoid=(lantern,))
    assert dropped and [e.candidate.ref.label for e in ents] == ["Copper Morning"]
    ents, dropped = await to_entities(_Session(), [_row(1, 1.0)], avoid=(lantern,))
    assert dropped and ents == []


async def test_search_answers_declined_when_everything_was_turned_down(faked_index, monkeypatch) -> None:
    lantern = EntityRef(type="title", key=faked_index._impl.entities[
        faked_index._impl.title_entity_for_track(1)].key, label="Lantern Song",
        artist_label="The Example Band")

    async def present(session):
        return True

    async def rows(session, query):
        return [ls.RowHit(track_id=1, span=1, line_no=0, repeats=2,
                          text="the lantern hums beside the river door")]

    monkeypatch.setattr(ls, "lyrics_tables_present", present)
    monkeypatch.setattr(ls, "fetch_rows", rows)
    res, n = await ls.search(_Session(), "the lantern hums beside the river door",
                             mode="explicit", play=PLAY, ask=ASK, avoid=(lantern,))
    assert (res.decision, res.reason, n) == ("none", "declined", 7)
    res, _ = await ls.search(_Session(), "the lantern hums beside the river door",
                             mode="explicit", play=PLAY, ask=ASK)
    assert (res.decision, res.reason, res.best.ref) == ("play", "lyrics", lantern)


def test_title_entity_for_track(faked_index) -> None:
    impl = faked_index._impl
    i1, i2 = impl.title_entity_for_track(1), impl.title_entity_for_track(2)
    assert i1 is not None and i1 == i2
    assert impl.entities[i1].type == "title" and impl.entities[i1].label == "Lantern Song"
    assert impl.title_entity_for_track(5) is None          # no title
    assert impl.title_entity_for_track(999) is None        # not in the library
