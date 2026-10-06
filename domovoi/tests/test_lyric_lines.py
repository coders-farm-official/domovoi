"""Lyric search's line index rows: ``lyric_search.build_lines``
(lyrics-build contract [Q1]).

One row per distinct line (span 1) and per distinct pair of consecutive
lines (span 2), with how often it is sung and where it first comes;
section labels and empty lines are no lines. Pure; never skips. Every
line here is invented.
"""

from __future__ import annotations

from domovoi.handlers.shared.lyric_search import (
    MAX_KEY_CHARS,
    MAX_PAIR_CHARS,
    MAX_ROWS_PER_SPAN,
    LineRow,
    build_lines,
    line_key,
)

SONG = """[Verse 1]
The lantern hums beside the river door,
And every copper kettle sings at dawn

[Chorus]
We carried paper boats along the hall
The river door is open tonight!
We carried paper boats along the hall
The river door is open tonight!
(x2)
"""

L0 = "the lantern hums beside the river door"
L1 = "and every copper kettle sings at dawn"
L2 = "we carried paper boats along the hall"
L3 = "the river door is open tonight"


def _by_span(rows: list[LineRow], span: int) -> list[tuple[int, int, str]]:
    return [(r.line_no, r.repeats, r.text) for r in rows if r.span == span]


def test_lines_pairs_repeats_and_first_positions() -> None:
    rows = build_lines(SONG)
    # Labels and the empty line are gone; the stanza break resets nothing.
    assert _by_span(rows, 1) == [(0, 1, L0), (1, 1, L1), (2, 2, L2), (3, 2, L3)]
    assert _by_span(rows, 2) == [
        (0, 1, f"{L0} {L1}"),
        (1, 1, f"{L1} {L2}"),
        (2, 2, f"{L2} {L3}"),
        (3, 1, f"{L3} {L2}"),
    ]
    # Sorted by (span, line_no).
    assert rows == sorted(rows, key=lambda r: (r.span, r.line_no))


def test_bracket_only_lines_are_labels_but_bracketed_words_in_a_line_are_kept() -> None:
    text = "[Chorus]\n( x2 )\n  [bridge: slower]  \n(the kettle answers) the river door\n"
    rows = build_lines(text)
    assert _by_span(rows, 1) == [(0, 1, "the kettle answers the river door")]
    assert _by_span(rows, 2) == []


def test_nothing_to_index() -> None:
    assert build_lines("") == []
    assert build_lines(None) == []
    assert build_lines("\n\n[Intro]\n...\n!!!\n") == []


def test_a_line_is_stored_as_its_lyric_words() -> None:
    assert line_key("  Oh, the LANTERN hums — beside the river-door!  ") == (
        "oh the lantern hums beside the river door"
    )
    assert line_key("We carried 12 paper boats") == "we carried twelve paper boats"


def test_one_line_said_over_and_over_counts_once_with_its_repeats() -> None:
    rows = build_lines("\n".join([L2] * 5))
    assert _by_span(rows, 1) == [(0, 5, L2)]
    assert _by_span(rows, 2) == [(0, 4, f"{L2} {L2}")]


def test_caps_long_lines_and_many_rows() -> None:
    long_line = " ".join(["kettle"] * 400)            # 2799 characters of words
    rows = build_lines(f"{long_line}\n{long_line} river")
    singles = [r for r in rows if r.span == 1]
    pairs = [r for r in rows if r.span == 2]
    assert all(len(r.text) <= MAX_KEY_CHARS for r in singles)
    assert all(len(r.text) <= MAX_PAIR_CHARS for r in pairs)  # the CHECK's 2000
    assert all(r.text == r.text.strip() and r.text for r in rows)

    many = "\n".join(f"the kettle number {i} sings" for i in range(1500))
    rows = build_lines(many)
    singles = [r for r in rows if r.span == 1]
    pairs = [r for r in rows if r.span == 2]
    assert len(singles) == MAX_ROWS_PER_SPAN and len(pairs) == MAX_ROWS_PER_SPAN
    # The earliest kept.
    assert [r.line_no for r in singles] == list(range(MAX_ROWS_PER_SPAN))


def test_positions_and_counts_fit_smallint_columns() -> None:
    """line_no and repeats are SMALLINT: a pathological text (one line
    40,000 times) must never produce a row the table refuses."""
    text = "\n".join([L0] * 40_000 + [L1])
    rows = build_lines(text)
    assert all(0 <= r.line_no <= 32767 and 1 <= r.repeats <= 32767 for r in rows)
    assert (0, 32767, L0) in _by_span(rows, 1)
    assert all(r.text != L1 for r in rows)            # first seen past 32767: left out
