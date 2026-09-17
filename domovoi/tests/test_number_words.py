"""Unit tests for the shared spoken-number / duration grammar.

Deliberately DB-free so nothing here can skip on a box without Postgres
(see the ``requires_db`` note in test_timer_handler).
"""

from __future__ import annotations

import re

import pytest

from domovoi.handlers.shared.number_words import (
    DURATION_PATTERN,
    NUMBER_PATTERN,
    NUMBER_WORD_PATTERN,
    NUMBER_WORDS,
    ORDINAL_WORDS,
    normalize_number_token,
    parse_duration_seconds,
    parse_number,
)


def test_fragments_have_no_capturing_groups() -> None:
    # Handlers embed these inside their own numbered / named groups; a
    # capturing group leaking out of a fragment would shift them.
    for fragment in (NUMBER_WORD_PATTERN, NUMBER_PATTERN, DURATION_PATTERN):
        assert re.compile(fragment).groups == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5", 5),
        ("0", 0),
        ("007", 7),
        ("one", 1),
        ("nine", 9),
        ("ten", 10),
        ("fourteen", 14),
        ("nineteen", 19),
        ("twenty", 20),
        ("forty", 40),
        ("ninety", 90),
        ("twenty one", 21),
        ("forty five", 45),
        ("forty-five", 45),
        ("ninety nine", 99),
        ("  Forty Five ", 45),
    ],
)
def test_parse_number(text: str, expected: int) -> None:
    assert parse_number(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "zero", "hundred", "one hundred", "five five", "twenty ten", "fourty", "5 minutes"],
)
def test_parse_number_rejects_non_numbers(text: str) -> None:
    assert parse_number(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # digits
        ("1 minute", 60),
        ("90 seconds", 90),
        ("2 hours", 7200),
        ("0 minutes", 0),
        # words
        ("one minute", 60),
        ("ten minutes", 600),
        ("forty five minutes", 2700),
        ("forty-five minutes", 2700),
        ("seventeen seconds", 17),
        ("seventy seconds", 70),
        # articles
        ("a minute", 60),
        ("an hour", 3600),
        ("a second", 1),
        # halves
        ("half a minute", 30),
        ("half an hour", 1800),
        ("a half hour", 1800),
        ("a minute and a half", 90),
        ("an hour and a half", 5400),
        ("one and a half hours", 5400),
        ("1 and a half hours", 5400),
        ("two and a half minutes", 150),
        ("5 minutes and a half", 330),
        # two clauses, any mix of digits / words / articles
        ("1 hour and 30 minutes", 5400),
        ("1 hour, 30 minutes", 5400),
        ("5 minutes 30 seconds", 330),
        ("an hour and thirty minutes", 5400),
        ("two minutes and 30 seconds", 150),
        ("one minute and one second", 61),
        # case-insensitive for direct callers (the router lower-cases first)
        ("An Hour And A Half", 5400),
    ],
)
def test_parse_duration_seconds(text: str, expected: int) -> None:
    assert parse_duration_seconds(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "5",
        "twenty",
        "minutes",
        "a while",
        "pasta",
        "eleventy minutes",
        "one two minutes",
        "half",
        "and a half hours",
        "5 minutes for pasta",
        "5 minutes and",
        "an hour and",
        "1.5 hours",
    ],
)
def test_parse_duration_seconds_rejects_non_durations(text: str) -> None:
    assert parse_duration_seconds(text) is None


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        # digit ordinals — what our own _ordinal() helpers emit
        ("11th", "11"), ("1st", "1"), ("2nd", "2"), ("3rd", "3"),
        ("21st", "21"), ("12TH", "12"),
        # spelled-out ordinals, regular and irregular
        ("eleventh", "11"), ("first", "1"), ("second", "2"), ("third", "3"),
        ("fifth", "5"), ("eighth", "8"), ("ninth", "9"), ("twelfth", "12"),
        ("fourth", "4"), ("twentieth", "20"), ("eightieth", "80"),
        # plain cardinals collapse onto the same digits
        ("eleven", "11"), ("Eighty", "80"),
        # already canonical
        ("11", "11"), ("2026", "2026"),
        # not numbers — returned untouched
        ("september", "september"), ("", ""), ("th", "th"),
        ("timer", "timer"), ("2026th", "2026"),
    ],
)
def test_normalize_number_token(token: str, expected: str) -> None:
    assert normalize_number_token(token) == expected


def test_every_cardinal_has_a_distinct_ordinal() -> None:
    """The ordinal table is derived from NUMBER_WORDS, so a typo in the
    derivation would silently collapse two numbers onto one key."""
    assert len(ORDINAL_WORDS) == len(NUMBER_WORDS)
    assert set(ORDINAL_WORDS.values()) == set(NUMBER_WORDS.values())
    # No cardinal accidentally doubles as an ordinal ("second" is a unit
    # word, but it is genuinely the ordinal of two).
    assert ORDINAL_WORDS["second"] == 2
