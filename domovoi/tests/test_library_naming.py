"""The core "Artist - Title" parser (domovoi/library_naming.py), used by
the library indexer's file-name fallback and by plugins through the SDK's
``library.parse_title_artist``.

F-P025 (rc2 smoke): a hyphen inside a word split the title, so a download
titled "Lo-fi Piano Sample" was filed under the artist "Lo". Only a dash
with a space beside it separates artist from title.
"""

from __future__ import annotations

import pytest

from domovoi.library_naming import parse_title_artist
from domovoi.sdk.library import LibraryAPI


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The common upload shape, with the trailing descriptor stripped.
        ("Radiohead - Creep (Official Video)", ("Radiohead", "Creep")),
        ("AC/DC - Thunderstruck", ("AC/DC", "Thunderstruck")),
        # Hyphenated artists stay whole.
        ("Jay-Z - Empire State of Mind", ("Jay-Z", "Empire State of Mind")),
        ("Blink-182 - All the Small Things", ("Blink-182", "All the Small Things")),
        ("Yo-Yo Ma - Cello Suite No. 1", ("Yo-Yo Ma", "Cello Suite No. 1")),
        # En and em dashes, spaced.
        ("Beethoven – Symphony No. 5", ("Beethoven", "Symphony No. 5")),
        ("Nina Simone — Feeling Good", ("Nina Simone", "Feeling Good")),
        # The first spaced dash separates; later ones belong to the title.
        ("Run-DMC - It's Tricky - Live", ("Run-DMC", "It's Tricky - Live")),
        # A dash spaced on one side only still separates (real file names).
        ("ASAP Rocky- Fucken Problem", ("ASAP Rocky", "Fucken Problem")),
        ("Jay-Z -Empire State of Mind", ("Jay-Z", "Empire State of Mind")),
        # ...but a dash spaced on both sides wins over one spaced on one side.
        ("Foo -Bar - Baz", ("Foo -Bar", "Baz")),
    ],
)
def test_splits_on_a_spaced_dash(raw: str, expected: tuple[str, str]) -> None:
    assert parse_title_artist(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "Lo-fi Piano Sample [#11]",   # the F-P025 download
        "Lo-fi Piano Sample",
        "Beethoven–Symphony No. 5",  # an unspaced en dash: a range, not a separator
        "1990—2000 Hits",
        "Just A Title",
        "Artist - ",
        "- Title",
        "",
        "   ",
    ],
)
def test_no_spaced_dash_means_no_artist(raw: str) -> None:
    assert parse_title_artist(raw) == (None, None)


def test_sdk_wrapper_keeps_the_whole_title_and_no_artist() -> None:
    """The SDK falls back to the raw title, so the ytdlp fulfiller keeps the
    uploader as the artist instead of filing the track under "Lo"."""
    library = LibraryAPI("testplug")
    assert library.parse_title_artist("Lo-fi Piano Sample [#11]") == (
        "Lo-fi Piano Sample [#11]", None)
    assert library.parse_title_artist("Jay-Z - Empire State of Mind") == (
        "Empire State of Mind", "Jay-Z")
