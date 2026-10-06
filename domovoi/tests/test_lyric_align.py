"""Lyric search's scorer: ``lyric_search.align`` and ``score_rows``
(lyrics-build contract [Q20]–[Q24]).

* the alignment's numbers on the contract's measured cases ([Q23]);
* the cached aligner ``score_rows`` uses equals the plain DP to the last
  bit, and skipping a line that cannot reach the ask band never changes a
  decision — on 500 randomized cases each ([Q24]);
* which candidate lines are scored ([Q20]) and the chorus weighting
  ([Q22]).

Pure; never skips. Every line and word here is invented.
"""

from __future__ import annotations

import random

import pytest

from domovoi.config import Settings
from domovoi.handlers.shared import lyric_search as ls
from domovoi.handlers.shared.library_match import Candidate, EntityRef
from domovoi.handlers.shared.lyric_search import (
    SCORED_ROWS,
    VERSE_FACTOR,
    LyricEntity,
    LyricQuery,
    RowHit,
    align,
    decide,
    lyric_query,
    score_rows,
)

CHORUS = "the lantern hums beside the river door".split()      # 7 words


def _score(said: str, line: list[str] = CHORUS) -> float:
    return round(align(said.split(), line)[0], 2)


# ─── [Q23]: the measured cases ─────────────────────────────────────────────


def test_the_exact_phrase() -> None:
    assert align(CHORUS, CHORUS) == (1.0, True)


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("the lantern hums beside river door", 0.83),            # one word left out
        ("the lantern beside hums the river door", 0.86),        # two words swapped
        ("the lantern hums beside the garden door", 0.86),       # one word replaced
        ("the lanterns hum beside the river door", 0.97),        # two near spellings
        ("the candle hums beside the garden door", 0.71),        # two words replaced
    ],
)
def test_the_measured_cases(said: str, expected: float) -> None:
    score, exact = align(said.split(), CHORUS)
    assert round(score, 2) == expected
    assert exact is False


def test_a_phrase_across_a_line_break_is_exact_on_the_pair() -> None:
    pair = "the lantern hums beside the river door and every copper kettle sings at dawn".split()
    assert align("river door and every copper kettle".split(), pair) == (1.0, True)


def test_a_phrase_sharing_four_of_five_words_with_another_line() -> None:
    other = "we carried paper boats along the hall".split()
    assert _score("we carried paper boats down", other) == 0.8


# ─── The alignment's rules ────────────────────────────────────────────────


def test_the_line_runs_on_freely_before_and_after() -> None:
    long_line = "oh oh the lantern hums beside the river door tonight and always".split()
    assert align("lantern hums beside the river".split(), long_line) == (1.0, True)


def test_every_word_said_must_be_accounted_for() -> None:
    # Words said that the line lacks cost one each, wherever they are.
    assert _score("the lantern hums beside the river door forever") == round(1 - 1 / 8, 2)
    assert _score("well the lantern hums beside the river door") == round(1 - 1 / 8, 2)


def test_a_swap_of_one_word_with_itself_is_no_swap() -> None:
    # "door door" against "door door": exact, and the swap rule needs two
    # different words.
    assert align(["door", "door"], ["door", "door"]) == (1.0, True)
    assert align(["kettle", "kettle", "sings"], ["kettle", "sings", "kettle"])[0] < 1.0


def test_nothing_said_and_nothing_sung() -> None:
    assert align([], CHORUS) == (0.0, False)
    assert align(CHORUS, []) == (0.0, False)


def test_scores_never_go_below_zero() -> None:
    assert align("one two three".split(), "kettle".split())[0] == 0.0


# ─── [Q24]: the optimized scorer is the plain DP ──────────────────────────

_VOCAB = (
    "lantern hums beside river door copper kettle sings dawn paper boats hall "
    "open tonight teapot whistles attic light pebbles garden stair mailbox "
    "secret moon bicycle remembers hill orchard fence marmalade heron trains "
    "clock half blue folded maps crown lighthouse scarf silver rain pocket "
    "buttons borrowed tune the a and of we my"
).split()


def _near(word: str, rng: random.Random) -> str:
    """A near spelling by rule: a plural, a dropped last letter, a doubled one."""
    kind = rng.randrange(3)
    if kind == 0:
        return word + "s"
    if kind == 1 and len(word) > 3:
        return word[:-1]
    return word + word[-1]


def _phrase_from(line: list[str], rng: random.Random) -> list[str]:
    """A said phrase made from a line by rule: a stretch of it, then a word
    dropped, swapped, replaced or misspelled now and then."""
    a = rng.randrange(len(line))
    b = rng.randrange(a + 1, len(line) + 1)
    out = list(line[a:b])
    for _ in range(rng.randrange(3)):
        op = rng.randrange(5)
        if op == 0 and len(out) > 1:
            del out[rng.randrange(len(out))]
        elif op == 1 and len(out) > 1:
            i = rng.randrange(len(out) - 1)
            out[i], out[i + 1] = out[i + 1], out[i]
        elif op == 2:
            out[rng.randrange(len(out))] = rng.choice(_VOCAB)
        elif op == 3:
            i = rng.randrange(len(out))
            out[i] = _near(out[i], rng)
        else:
            out.insert(rng.randrange(len(out) + 1), rng.choice(_VOCAB))
    return out


def test_the_cached_aligner_is_the_plain_dp_to_the_last_bit() -> None:
    rng = random.Random(20261005)
    for _ in range(500):
        line = [rng.choice(_VOCAB) for _ in range(rng.randrange(1, 20))]
        said = _phrase_from(line, rng) if rng.random() < 0.8 else [
            rng.choice(_VOCAB) for _ in range(rng.randrange(1, 12))
        ]
        cache = ls._SubCache(said)
        assert ls._align_matrix(said, line, cache.matrix(line)) == align(said, line), (said, line)


def _entities(rows: list[RowHit], scored: list) -> list[LyricEntity]:
    """One entity per track, scored by its best line (the [Q32] rule)."""
    best: dict[int, tuple] = {}
    for s in scored:
        key = (s.score, s.exact, s.repeats)
        if s.track_id not in best or key > best[s.track_id]:
            best[s.track_id] = key
    out = []
    for tid, (score, exact, repeats) in best.items():
        cand = Candidate(
            ref=EntityRef(type="track", key=str(tid), label=f"Song {tid}"),
            score=score, via="lyrics", speak=f"Song {tid}", track_ids=(tid,),
        )
        out.append(LyricEntity(cand, score, exact, repeats))
    return out


@pytest.mark.parametrize("mode", ["explicit", "identify", "fallback"])
def test_skipping_lines_that_cannot_reach_the_ask_band_changes_no_decision(mode: str) -> None:
    defaults = Settings()
    play, ask = defaults.music_match_play_threshold, defaults.music_match_ask_threshold
    rng = random.Random(f"skip-{mode}")
    skipped_some = False
    for _ in range(500 // 3 + 1):
        songs = {tid: [[rng.choice(_VOCAB) for _ in range(rng.randrange(3, 12))]
                       for _ in range(rng.randrange(1, 4))]
                 for tid in range(1, rng.randrange(2, 9))}
        rows = [
            RowHit(track_id=tid, span=1, line_no=k, repeats=rng.choice((1, 1, 2, 3)), text=" ".join(line))
            for tid, lines in songs.items() for k, line in enumerate(lines)
        ]
        line = rng.choice(rng.choice(list(songs.values())))
        said = _phrase_from(line, rng)
        query = LyricQuery(tuple(said))
        full = score_rows(query, rows)
        fast = score_rows(query, rows, floor=min(ask, play))
        skipped_some |= len(fast) < len(full)
        # Every line it kept has exactly the plain DP's score…
        plain = {(s.track_id, s.line_no): s for s in full}
        for s in fast:
            assert s == plain[(s.track_id, s.line_no)]
        # …every line it skipped could never have counted…
        kept = {(s.track_id, s.line_no) for s in fast}
        assert all(s.score < min(ask, play) for k, s in plain.items() if k not in kept)
        # …and the decision is the same.
        a = decide(query, _entities(rows, full), mode=mode, play=play, ask=ask)
        b = decide(query, _entities(rows, fast), mode=mode, play=play, ask=ask)
        assert (a.decision, a.reason, [c.ref for c in a.candidates]) == (
            b.decision, b.reason, [c.ref for c in b.candidates])
    assert skipped_some, "the randomized cases never exercised the skip"


# ─── [Q20] / [Q22]: which lines are scored, and the chorus weighting ──────


def test_a_line_sung_once_counts_slightly_less_than_the_chorus() -> None:
    q = lyric_query("the lantern hums beside the river door")
    rows = [
        RowHit(track_id=1, span=1, line_no=0, repeats=1, text=" ".join(CHORUS)),
        RowHit(track_id=2, span=1, line_no=3, repeats=2, text=" ".join(CHORUS)),
    ]
    by_track = {s.track_id: s for s in score_rows(q, rows)}
    assert by_track[2].score == 1.0 and by_track[2].exact
    assert by_track[1].score == pytest.approx(VERSE_FACTOR) and by_track[1].exact
    assert VERSE_FACTOR == 0.97


def test_the_first_rows_by_similarity_and_every_row_holding_the_phrase_are_scored() -> None:
    q = lyric_query("paper boats along the hall")
    filler = [
        RowHit(track_id=100 + k, span=1, line_no=0, repeats=1, text="teapot whistles in the attic light")
        for k in range(SCORED_ROWS + 20)
    ]
    exact_late = RowHit(track_id=7, span=2, line_no=4, repeats=1,
                        text="we carried paper boats along the hall the river door")
    near_late = RowHit(track_id=8, span=1, line_no=0, repeats=1, text="paper boat along a hall")
    scored = score_rows(q, filler + [exact_late, near_late])
    ids = {s.track_id for s in scored}
    assert 7 in ids                       # holds the phrase whole: never cut off
    assert 8 not in ids                   # past the first SCORED_ROWS, not whole
    assert len(scored) == SCORED_ROWS + 1
    assert next(s for s in scored if s.track_id == 7).exact
