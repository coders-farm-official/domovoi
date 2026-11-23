"""Built-in holiday table for the calculator's date questions ("how many
days until Christmas", "when is Thanksgiving", "what day is Easter next
year"). Works offline.

Hand-written rather than the PyPI ``holidays`` package: the voice handler
needs the dates people actually ask about in the main English-speaking
countries, plus Easter, and a small table keeps the lockfiles and the
offline installers as they are.

Each holiday has a spoken name, the names people call it (written as
spoken, apostrophes and all; matching ignores apostrophes and dots), and
a rule per country: ``"*"`` means everywhere. A country without its own
rule uses ``"*"``, then the US rule, so a Canadian asking about Memorial
Day still gets the US date. Countries are ISO 3166-1 codes, as the Home
location stores them.
"""

from __future__ import annotations

import re
from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

Rule = Callable[[int], date]

DEFAULT_COUNTRY = "US"

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


def easter(year: int) -> date:
    """Western (Gregorian) Easter Sunday: the anonymous Gregorian computus."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _fixed(month: int, day: int) -> Rule:
    return lambda year: date(year, month, day)


def _nth(month: int, weekday: int, n: int) -> Rule:
    """The nth ``weekday`` of ``month``; n = -1 is the last one."""
    def rule(year: int) -> date:
        if n > 0:
            first = date(year, month, 1)
            return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
        last = date(year, month, monthrange(year, month)[1])
        return last - timedelta(days=(last.weekday() - weekday) % 7)
    return rule


def _monday_before(month: int, day: int) -> Rule:
    """The last Monday strictly before ``month``/``day`` (Victoria Day: before May 25)."""
    def rule(year: int) -> date:
        d = date(year, month, day) - timedelta(days=1)
        return d - timedelta(days=d.weekday())
    return rule


def _from_easter(days: int) -> Rule:
    return lambda year: easter(year) + timedelta(days=days)


thanksgiving = _nth(11, THU, 4)   # US; kept by name for older callers

# (spoken name, other names, {country: rule})
_TABLE: tuple[tuple[str, tuple[str, ...], dict[str, Rule]], ...] = (
    ("New Year's Day", ("new year's", "new year", "new years day"), {"*": _fixed(1, 1)}),
    ("New Year's Eve", (), {"*": _fixed(12, 31)}),
    ("Christmas", ("christmas day", "xmas"), {"*": _fixed(12, 25)}),
    ("Christmas Eve", (), {"*": _fixed(12, 24)}),
    ("Boxing Day", (), {"*": _fixed(12, 26)}),
    ("Valentine's Day", ("valentine's", "valentines"), {"*": _fixed(2, 14)}),
    ("Groundhog Day", (), {"*": _fixed(2, 2)}),
    ("St. Patrick's Day", ("saint patrick's day", "st. paddy's day", "paddy's day"),
     {"*": _fixed(3, 17)}),
    ("April Fools' Day", ("april fool's day", "april fools"), {"*": _fixed(4, 1)}),
    ("Earth Day", (), {"*": _fixed(4, 22)}),
    ("Halloween", (), {"*": _fixed(10, 31)}),
    ("Guy Fawkes Night", ("bonfire night", "guy fawkes day"), {"*": _fixed(11, 5)}),
    ("Remembrance Day", ("armistice day",), {"*": _fixed(11, 11)}),
    ("Veterans Day", ("veteran's day",), {"*": _fixed(11, 11)}),
    # Easter and the days counted from it.
    ("Easter", ("easter sunday", "easter day"), {"*": easter}),
    ("Good Friday", (), {"*": _from_easter(-2)}),
    ("Easter Monday", (), {"*": _from_easter(1)}),
    ("Maundy Thursday", ("holy thursday",), {"*": _from_easter(-3)}),
    ("Palm Sunday", (), {"*": _from_easter(-7)}),
    ("Ash Wednesday", (), {"*": _from_easter(-46)}),
    ("Shrove Tuesday", ("mardi gras", "pancake day", "fat tuesday"), {"*": _from_easter(-47)}),
    ("Ascension Day", (), {"*": _from_easter(39)}),
    ("Pentecost", ("whit sunday", "pentecost sunday"), {"*": _from_easter(49)}),
    # Days whose rule differs by country.
    ("Mother's Day", ("mothering sunday",), {"*": _nth(5, SUN, 2),
                                             "GB": _from_easter(-21), "IE": _from_easter(-21)}),
    ("Father's Day", (), {"*": _nth(6, SUN, 3), "AU": _nth(9, SUN, 1), "NZ": _nth(9, SUN, 1)}),
    ("Thanksgiving", ("thanksgiving day",), {"US": _nth(11, THU, 4), "CA": _nth(10, MON, 2)}),
    ("Labor Day", ("labour day",), {"US": _nth(9, MON, 1), "CA": _nth(9, MON, 1),
                                    "NZ": _nth(10, MON, 4)}),
    # United States.
    ("Martin Luther King Jr. Day", ("martin luther king day", "mlk day", "m.l.k. day"),
     {"US": _nth(1, MON, 3)}),
    ("Presidents' Day", ("president's day", "washington's birthday"), {"US": _nth(2, MON, 3)}),
    ("Memorial Day", (), {"US": _nth(5, MON, -1)}),
    ("Juneteenth", (), {"US": _fixed(6, 19)}),
    ("Independence Day", ("fourth of july", "4th of july", "july fourth", "july 4th"),
     {"US": _fixed(7, 4)}),
    ("Columbus Day", ("indigenous peoples' day",), {"US": _nth(10, MON, 2)}),
    # Canada.
    ("Canada Day", (), {"CA": _fixed(7, 1)}),
    ("Victoria Day", (), {"CA": _monday_before(5, 25)}),
    # United Kingdom and Ireland.
    ("the early May bank holiday", ("may day", "may bank holiday", "early may bank holiday"),
     {"GB": _nth(5, MON, 1), "IE": _nth(5, MON, 1)}),
    ("the spring bank holiday", ("spring bank holiday",), {"GB": _nth(5, MON, -1)}),
    ("the summer bank holiday", ("summer bank holiday", "august bank holiday"),
     {"GB": _nth(8, MON, -1), "IE": _nth(8, MON, 1)}),
    # Australia and New Zealand.
    ("Australia Day", (), {"AU": _fixed(1, 26)}),
    ("Anzac Day", (), {"AU": _fixed(4, 25), "NZ": _fixed(4, 25)}),
    ("Waitangi Day", (), {"NZ": _fixed(2, 6)}),
)


def _key(name: str) -> str:
    """Lowercase, no apostrophes or dots, single spaces, no leading 'the'."""
    s = re.sub(r"['’.]", "", name.lower())
    s = " ".join(s.split())
    return s[4:] if s.startswith("the ") else s


_BY_KEY: dict[str, tuple[str, dict[str, Rule]]] = {}
for _name, _aliases, _rules in _TABLE:
    for _alias in (_name, *_aliases):
        _BY_KEY[_key(_alias)] = (_name, _rules)


def spoken_names() -> list[str]:
    """Every name and alias as written (for building match patterns)."""
    out = []
    for name, aliases, _rules in _TABLE:
        out.append(name[4:] if name.startswith("the ") else name)
        out.extend(aliases)
    return out


@dataclass(frozen=True)
class Holiday:
    name: str
    rule: Rule

    def on(self, year: int) -> date:
        return self.rule(year)

    def next_from(self, today: date) -> date:
        """This year's date, or next year's once it has passed (today counts)."""
        d = self.rule(today.year)
        return d if d >= today else self.rule(today.year + 1)


def find(label: str, country: str | None = None) -> Holiday | None:
    """The holiday called ``label`` with ``country``'s rule, or None."""
    key = _key(label)
    hit = _BY_KEY.get(key)
    if hit is None and key.endswith(" day"):
        hit = _BY_KEY.get(key[:-4])
    if hit is None:
        hit = _BY_KEY.get(key + " day")
    if hit is None:
        return None
    name, rules = hit
    cc = (country or DEFAULT_COUNTRY).upper()
    rule = rules.get(cc) or rules.get("*") or rules.get(DEFAULT_COUNTRY) or next(iter(rules.values()))
    return Holiday(name, rule)


def next_occurrence(name: str, today: date, country: str | None = None) -> date | None:
    """The next date of the named holiday on or after ``today``, or None
    for a name not in the table. Today counts: on Dec 25 Christmas is today."""
    h = find(name, country)
    return h.next_from(today) if h else None
