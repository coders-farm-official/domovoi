"""Lyric search's one normalization: ``spoken_names.lyric_words``
(lyrics-build contract [N1]–[N3]).

Every line the index stores and every phrase a person says go through it,
so the two meet in exactly one form. Pure; never skips. Every line here is
invented.
"""

from __future__ import annotations

import pytest

from domovoi.handlers.shared import spoken_names
from domovoi.handlers.shared.spoken_names import LYRIC_NORM_VERSION, lyric_words


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The contract's vectors ([N2]), measured on the base.
        ("Oh, the Lantern HUMS — beside the river-door!", "oh the lantern hums beside the river door"),
        ("I've got 99 copper kettles", "ive got ninety nine copper kettles"),
        ("Rock & roll", "rock and roll"),
        ("naïve café", "naive cafe"),
        ("2nd floor, 3 a.m.", "second floor three am"),
        ("“the paper boats” (are sailing)", "the paper boats are sailing"),
        ("007 kettles", "seven kettles"),
        ("", ""),
    ],
)
def test_the_contract_vectors(text: str, expected: str) -> None:
    assert " ".join(lyric_words(text)) == expected


def test_none_and_nothing_sayable_are_no_words() -> None:
    assert lyric_words(None) == []
    assert lyric_words("  ...  !!  ") == []


def test_a_whole_number_is_its_cardinal_words_never_the_paired_reading() -> None:
    # Names also get "nineteen ninety nine"; lyric search keeps ONE form.
    assert lyric_words("1999 paper boats") == "one thousand nine hundred ninety nine paper boats".split()
    assert lyric_words("4th of the kettles") == "fourth of the kettles".split()


def test_stop_words_are_kept() -> None:
    """Lyrics are made of them: nothing is dropped."""
    assert lyric_words("a the and of to by") == ["a", "the", "and", "of", "to", "by"]


@pytest.mark.parametrize(
    "line",
    [
        "The lantern hums beside the river door",
        "And every copper kettle sings at dawn!",
        "We carried 12 paper boats along the hall…",
        "The river-door is open, 2nite & 4ever",
        "Ça va, the 1st kettle — singing at 5 a.m.",
    ],
)
def test_normalizing_twice_changes_nothing(line: str) -> None:
    """A stored line and a phrase normalized from a stored line meet: the
    form is a fixed point."""
    once = lyric_words(line)
    assert lyric_words(" ".join(once)) == once


def test_version_and_exports() -> None:
    assert LYRIC_NORM_VERSION == 1
    assert {"lyric_words", "LYRIC_NORM_VERSION"} <= set(spoken_names.__all__)
