"""Spoken clock times and days (handlers/shared/clock_time) and the
household wall clock they are placed on (domovoi/household_time).

DB-free. The rules (owner, 2026-10-17): "wake me up at 7" is 7 AM;
otherwise the next time that comes, a time already gone today meaning
tomorrow; the reply says the clock time. DST is tested on a hand-written
US Eastern zone (household_time_testkit), which needs no tzdata.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from domovoi import household_time
from domovoi.handlers.shared import clock_time as ct
from domovoi.tests.household_time_testkit import EASTERN, at

_WHEN = re.compile(rf"^(?:{ct.WHEN_PATTERN})$")
_WHEN_FOR = re.compile(rf"^(?:{ct.WHEN_FOR_PATTERN})$")
_ALARM_WHEN = re.compile(rf"^(?:{ct.ALARM_WHEN_PATTERN})$")

# Friday 2026-10-16, 23:20 — when the manual writer heard "in 10 hours".
LATE = datetime(2026, 10, 16, 23, 20)
MORNING = datetime(2026, 10, 16, 5, 0)
AFTERNOON = datetime(2026, 10, 16, 14, 0)


def _resolve(said: str, now: datetime, *, wake: bool = False):
    assert _WHEN.match(said), f"{said!r} is not a WHEN"
    w = ct.parse_when(said)
    assert w is not None, said
    return ct.resolve(w, now, wake=wake)


# ─── What the grammar takes ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "said",
    [
        "at 6 pm", "at 6pm", "at 6 p.m.", "at 6 p.m", "at 6:30 pm", "at 18:00",
        "at 07:30", "at 7", "at seven", "at six thirty", "at six oh five",
        "at 7 o'clock", "at half past six", "at quarter to seven",
        "at ten to six", "at noon", "at midnight", "at 12 noon",
        "at 7 in the morning", "at 9 at night", "at 9 tonight",
        "tomorrow", "tomorrow morning", "tomorrow at 7", "at 7 tomorrow",
        "tonight", "tonight at 9", "this evening", "in the morning",
        "friday", "on friday", "on friday at 5 pm", "next friday at 5",
        "friday night", "the day after tomorrow at noon",
    ],
)
def test_when_grammar_takes_spoken_times_and_days(said: str) -> None:
    assert _WHEN.match(said)
    assert ct.parse_when(said) is not None


@pytest.mark.parametrize(
    "said",
    ["in 10 minutes", "10 minutes", "at", "at the store", "later", "soon",
     "at 25", "at 6 things", "eventually", "at six to"],
)
def test_when_grammar_refuses_what_is_no_time(said: str) -> None:
    assert not _WHEN.match(said)


def test_a_bare_number_is_a_time_only_after_at_or_alarm() -> None:
    # "a reminder for 7" might be 7 minutes; "for 7 pm" can't be.
    assert not _WHEN_FOR.match("7")
    assert _WHEN_FOR.match("7 pm") and _WHEN_FOR.match("6:30") and _WHEN_FOR.match("7 o'clock")
    assert not _WHEN_FOR.match("10 minutes")
    # "an alarm for 7" is a time; "for 10 minutes" still isn't one.
    assert _ALARM_WHEN.match("7") and _ALARM_WHEN.match("7 tomorrow")
    assert not _ALARM_WHEN.match("10 minutes")


# ─── Where it lands ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("said", "now", "wake", "target", "spoken"),
    [
        # a clock time, 12 and 24 hour: the next one that comes
        ("at 6 pm", LATE, False, datetime(2026, 10, 17, 18, 0), "tomorrow at 6 PM"),
        ("at 6 pm", AFTERNOON, False, datetime(2026, 10, 16, 18, 0), "at 6 PM"),
        ("at 6 p.m.", AFTERNOON, False, datetime(2026, 10, 16, 18, 0), "at 6 PM"),
        ("at 18:00", LATE, False, datetime(2026, 10, 17, 18, 0), "tomorrow at 6 PM"),
        ("at 6:30 a.m", LATE, False, datetime(2026, 10, 17, 6, 30), "tomorrow at 6:30 AM"),
        ("at 07:30", AFTERNOON, False, datetime(2026, 10, 17, 7, 30), "tomorrow at 7:30 AM"),
        ("at 12 am", LATE, False, datetime(2026, 10, 17, 0, 0), "at midnight"),
        # "7" alone: whichever 7 comes next
        ("at 7", LATE, False, datetime(2026, 10, 17, 7, 0), "tomorrow at 7 AM"),
        ("at 7", AFTERNOON, False, datetime(2026, 10, 16, 19, 0), "at 7 PM"),
        ("at 7", MORNING, False, datetime(2026, 10, 16, 7, 0), "at 7 AM"),
        ("at seven", AFTERNOON, False, datetime(2026, 10, 16, 19, 0), "at 7 PM"),
        # "wake me up at 7" is 7 AM
        ("at 7", AFTERNOON, True, datetime(2026, 10, 17, 7, 0), "tomorrow at 7 AM"),
        ("at 7", MORNING, True, datetime(2026, 10, 16, 7, 0), "at 7 AM"),
        ("at 7 pm", AFTERNOON, True, datetime(2026, 10, 16, 19, 0), "at 7 PM"),
        # words
        ("at six thirty", AFTERNOON, False, datetime(2026, 10, 16, 18, 30), "at 6:30 PM"),
        ("at half past six", LATE, False, datetime(2026, 10, 17, 6, 30), "tomorrow at 6:30 AM"),
        ("at quarter to seven", AFTERNOON, False, datetime(2026, 10, 16, 18, 45), "at 6:45 PM"),
        ("at ten to six", AFTERNOON, False, datetime(2026, 10, 16, 17, 50), "at 5:50 PM"),
        ("at six oh five", AFTERNOON, False, datetime(2026, 10, 16, 18, 5), "at 6:05 PM"),
        ("at 7 in the morning", AFTERNOON, False, datetime(2026, 10, 17, 7, 0), "tomorrow at 7 AM"),
        ("at 9 at night", AFTERNOON, False, datetime(2026, 10, 16, 21, 0), "at 9 PM"),
        # noon and midnight
        ("at noon", AFTERNOON, False, datetime(2026, 10, 17, 12, 0), "tomorrow at noon"),
        ("at midnight", LATE, False, datetime(2026, 10, 17, 0, 0), "at midnight"),
        ("tomorrow at midnight", LATE, False, datetime(2026, 10, 18, 0, 0), "at midnight tomorrow night"),
        ("tonight at 12", AFTERNOON, False, datetime(2026, 10, 17, 0, 0), "at midnight"),
        # tomorrow / tonight / a weekday / a part of the day
        ("tomorrow", LATE, False, datetime(2026, 10, 17, 9, 0), "tomorrow at 9 AM"),
        ("tomorrow morning", LATE, False, datetime(2026, 10, 17, 9, 0), "tomorrow at 9 AM"),
        ("tomorrow evening", LATE, False, datetime(2026, 10, 17, 18, 0), "tomorrow at 6 PM"),
        ("tomorrow at 7", LATE, False, datetime(2026, 10, 17, 7, 0), "tomorrow at 7 AM"),
        ("at 7 tomorrow", LATE, False, datetime(2026, 10, 17, 7, 0), "tomorrow at 7 AM"),
        ("tonight", AFTERNOON, False, datetime(2026, 10, 16, 20, 0), "at 8 PM"),
        ("tonight at 9", AFTERNOON, False, datetime(2026, 10, 16, 21, 0), "at 9 PM"),
        ("at 9 tonight", AFTERNOON, False, datetime(2026, 10, 16, 21, 0), "at 9 PM"),
        ("in the morning", LATE, False, datetime(2026, 10, 17, 9, 0), "tomorrow at 9 AM"),
        ("this evening", AFTERNOON, False, datetime(2026, 10, 16, 18, 0), "at 6 PM"),
        ("on monday at 5 pm", LATE, False, datetime(2026, 10, 19, 17, 0), "on Monday at 5 PM"),
        ("monday", LATE, False, datetime(2026, 10, 19, 9, 0), "on Monday at 9 AM"),
        # today is Friday: a Friday time still to come is today, a gone one next week
        ("on friday at 5 pm", MORNING, False, datetime(2026, 10, 16, 17, 0), "at 5 PM"),
        ("on friday at 5 pm", LATE, False, datetime(2026, 10, 23, 17, 0),
         "on Friday, October 23rd at 5 PM"),
        ("next friday at 5 pm", MORNING, False, datetime(2026, 10, 23, 17, 0),
         "on Friday, October 23rd at 5 PM"),
        ("the day after tomorrow at noon", LATE, False, datetime(2026, 10, 18, 12, 0),
         "on Sunday at noon"),
    ],
)
def test_resolve_places_the_time_and_says_it(said, now, wake, target, spoken) -> None:
    got = _resolve(said, now, wake=wake)
    assert got == target, said
    assert ct.spoken_when(got, now) == spoken


@pytest.mark.parametrize(
    ("said", "now", "kind", "gone"),
    [
        # a named day whose time has gone is refused, never moved
        ("tonight at 9", datetime(2026, 10, 16, 22, 0), "past", datetime(2026, 10, 16, 21, 0)),
        ("at 9 tonight", datetime(2026, 10, 16, 22, 0), "past", datetime(2026, 10, 16, 21, 0)),
        # "today" with no time, or a part of today whose hour has gone
        ("today", AFTERNOON, "need_time", None),
        ("this evening", datetime(2026, 10, 16, 23, 0), "need_time", None),
        ("tonight", datetime(2026, 10, 16, 23, 0), "need_time", None),
    ],
)
def test_resolve_refuses_a_named_day_that_has_gone(said, now, kind, gone) -> None:
    got = _resolve(said, now)
    assert isinstance(got, ct.WhenProblem)
    assert got.kind == kind
    assert got.target == gone


def test_spoken_time_and_day() -> None:
    now = LATE
    assert ct.spoken_time_and_day(datetime(2026, 10, 17, 7, 0), now) == "7 AM tomorrow"
    assert ct.spoken_time_and_day(datetime(2026, 10, 16, 23, 45), now) == "11:45 PM"
    assert ct.spoken_time_and_day(datetime(2026, 10, 19, 17, 0), now) == "5 PM on Monday"
    assert ct.spoken_time_and_day(datetime(2026, 10, 17, 0, 0), now) == "midnight"


# ─── The household wall clock, DST included ───────────────────────────────


def test_dst_spring_forward_gap_reads_as_the_hour_after() -> None:
    with household_time.use_zone(EASTERN):
        # 2026-03-08 02:30 doesn't exist in US Eastern: it is 03:30 EDT.
        got = household_time.to_utc(datetime(2026, 3, 8, 2, 30))
        assert got == datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)
        assert household_time.to_local(got) == datetime(2026, 3, 8, 3, 30)


def test_dst_fall_back_repeated_hour_is_its_first_reading() -> None:
    with household_time.use_zone(EASTERN):
        got = household_time.to_utc(datetime(2026, 11, 1, 1, 30))
        assert got == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)  # EDT


@pytest.mark.parametrize(
    ("wall_now", "said", "hours"),
    [
        # "tomorrow at 7" across the spring-forward night is 8 hours away
        # from 22:00 (a 23-hour day), and 7 on the new wall clock
        ((2026, 3, 7, 22, 0), "tomorrow at 7", 8),
        # ... and across the fall-back night 10 hours (a 25-hour day)
        ((2026, 10, 31, 22, 0), "tomorrow at 7", 10),
        # an ordinary night: 9
        ((2026, 10, 16, 22, 0), "tomorrow at 7", 9),
        # "at 6 pm" said in the fall-back morning: 6 PM EST that day
        ((2026, 11, 1, 0, 30), "at 6 pm", 18.5),
    ],
)
def test_wall_clock_times_survive_a_dst_change(wall_now, said, hours) -> None:
    with at(*wall_now) as instant:
        local_now = household_time.now()
        assert local_now == datetime(*wall_now)
        target = _resolve(said, local_now)
        expires = household_time.to_utc(target)
        assert (expires - instant) == timedelta(hours=hours)
        # and back on the wall clock it is the time that was said
        assert household_time.to_local(expires) == target


def test_without_a_pinned_zone_the_os_clock_round_trips() -> None:
    """The server's own zone (no override): now and back again agree."""
    local = household_time.now()
    utc = household_time.to_utc(local)
    assert abs((utc - household_time.utc_now()).total_seconds()) < 5
    noon = datetime(2026, 7, 1, 12, 0)
    assert household_time.to_local(household_time.to_utc(noon)) == noon
