"""Tiny holiday table for CalculatorHandler's `days until X` queries.

Hand-rolled rather than `holidays`-the-PyPI-package: the voice
handler only needs a handful of dates people actually ask about, and
we don't want to ship a multi-locale calendar lib for that.

Fixed-date holidays live in `FIXED_HOLIDAYS` as (month, day). Floating
holidays (Thanksgiving = 4th Thursday of November) compute their date
for a given year. `next_occurrence` always returns the next future
date — if Christmas already passed this year, you get next year's.
"""

from __future__ import annotations

from datetime import date


# Canonical name → (month, day). Keys are matched case-insensitively
# in the handler; the handler also handles a few aliases ("xmas",
# "new years", "july fourth") before lookup.
FIXED_HOLIDAYS: dict[str, tuple[int, int]] = {
    "christmas": (12, 25),
    "new year's": (1, 1),
    "new years": (1, 1),
    "july 4th": (7, 4),
    "fourth of july": (7, 4),
    "independence day": (7, 4),
    "halloween": (10, 31),
    "valentine's day": (2, 14),
    "valentines day": (2, 14),
}


def thanksgiving(year: int) -> date:
    """4th Thursday of November in the given year."""
    nov1 = date(year, 11, 1)
    # weekday(): Monday=0 ... Thursday=3 ... Sunday=6.
    offset_to_first_thursday = (3 - nov1.weekday()) % 7
    first_thursday = nov1.day + offset_to_first_thursday
    return date(year, 11, first_thursday + 21)


# Floating holidays compute their date for a year. Add more here as
# users ask for them — Mother's Day (2nd Sunday of May), Father's Day
# (3rd Sunday of June), etc.
FLOATING_HOLIDAYS: dict[str, callable] = {
    "thanksgiving": thanksgiving,
}


def next_occurrence(name: str, today: date) -> date | None:
    """Return the next future date for the named holiday, relative to
    ``today``. Returns None for unknown names.

    Today counts as "future" — "days until Christmas" on Dec 25 returns
    0 ("It's today!") rather than rolling to next year.
    """
    key = name.strip().lower()
    if key in FIXED_HOLIDAYS:
        month, day = FIXED_HOLIDAYS[key]
        candidate = date(today.year, month, day)
        if candidate < today:
            candidate = date(today.year + 1, month, day)
        return candidate
    if key in FLOATING_HOLIDAYS:
        candidate = FLOATING_HOLIDAYS[key](today.year)
        if candidate < today:
            candidate = FLOATING_HOLIDAYS[key](today.year + 1)
        return candidate
    return None
