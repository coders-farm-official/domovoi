"""The built-in holiday table (domovoi/clients/holidays.py)."""

from __future__ import annotations

from datetime import date

import pytest

from domovoi.clients.holidays import easter, find, next_occurrence


@pytest.mark.parametrize(
    ("year", "expected"),
    [(2000, date(2000, 4, 23)), (2019, date(2019, 4, 21)), (2024, date(2024, 3, 31)),
     (2025, date(2025, 4, 20)), (2026, date(2026, 4, 5)), (2027, date(2027, 3, 28)),
     (2038, date(2038, 4, 25))],
)
def test_easter_computus(year: int, expected: date) -> None:
    assert easter(year) == expected


def test_days_from_easter() -> None:
    assert find("good friday").on(2026) == date(2026, 4, 3)
    assert find("easter monday").on(2026) == date(2026, 4, 6)
    assert find("ash wednesday").on(2026) == date(2026, 2, 18)
    assert find("mardi gras").on(2026) == date(2026, 2, 17)


@pytest.mark.parametrize(
    ("year", "us", "ca"),
    [(2024, date(2024, 11, 28), date(2024, 10, 14)),
     (2025, date(2025, 11, 27), date(2025, 10, 13)),
     (2026, date(2026, 11, 26), date(2026, 10, 12))],
)
def test_thanksgiving_rule(year: int, us: date, ca: date) -> None:
    assert find("thanksgiving", "US").on(year) == us
    assert find("thanksgiving", "CA").on(year) == ca


@pytest.mark.parametrize(
    ("label", "country", "on_2026"),
    [("mlk day", "US", date(2026, 1, 19)), ("presidents' day", "US", date(2026, 2, 16)),
     ("memorial day", "US", date(2026, 5, 25)), ("labor day", "US", date(2026, 9, 7)),
     ("labour day", "NZ", date(2026, 10, 26)), ("victoria day", "CA", date(2026, 5, 18)),
     ("father's day", "US", date(2026, 6, 21)), ("father's day", "AU", date(2026, 9, 6)),
     ("summer bank holiday", "GB", date(2026, 8, 31)),
     ("summer bank holiday", "IE", date(2026, 8, 3)),
     ("boxing day", "GB", date(2026, 12, 26)), ("the 4th of july", None, date(2026, 7, 4))],
)
def test_country_rules(label: str, country, on_2026: date) -> None:
    assert find(label, country).on(2026) == on_2026


@pytest.mark.parametrize(
    "label", ["new year's day", "New Years Day", "new year", "new years", "the new year's day"],
)
def test_new_years_day_names(label: str) -> None:
    assert find(label).name == "New Year's Day"


def test_next_occurrence_rolls_over_and_today_counts() -> None:
    assert next_occurrence("christmas", date(2026, 12, 26)) == date(2027, 12, 25)
    assert next_occurrence("christmas", date(2026, 12, 25)) == date(2026, 12, 25)
    assert next_occurrence("dragonday", date(2026, 1, 1)) is None
