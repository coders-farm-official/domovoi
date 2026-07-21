from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from domovoi.handlers.clock import (
    ClockHandler,
    _DAY_OF_WEEK_RE,
    _FULL_DATE_RE,
    _MONTH_RE,
    _TIME_RE,
    _TOMORROW_RE,
    _YEAR_RE,
    _YESTERDAY_RE,
    _format_full_date,
    _format_short_date,
    _format_time,
    _ordinal,
)
from domovoi.models import Context


# ─── Pure helper tests ──────────────────────────────────────────────────────

def test_ordinal_basic() -> None:
    assert _ordinal(1) == "1st"
    assert _ordinal(2) == "2nd"
    assert _ordinal(3) == "3rd"
    assert _ordinal(4) == "4th"
    assert _ordinal(10) == "10th"


def test_ordinal_teens_use_th_suffix() -> None:
    """11/12/13 are the irregular case — "11th" not "11st"."""
    assert _ordinal(11) == "11th"
    assert _ordinal(12) == "12th"
    assert _ordinal(13) == "13th"
    assert _ordinal(111) == "111th"
    assert _ordinal(112) == "112th"
    assert _ordinal(113) == "113th"


def test_ordinal_post_twenty() -> None:
    assert _ordinal(21) == "21st"
    assert _ordinal(22) == "22nd"
    assert _ordinal(23) == "23rd"
    assert _ordinal(24) == "24th"
    assert _ordinal(31) == "31st"


def test_format_time_pm() -> None:
    assert _format_time(datetime(2026, 5, 4, 15, 47)) == "3:47 PM"


def test_format_time_am() -> None:
    assert _format_time(datetime(2026, 5, 4, 9, 5)) == "9:05 AM"


def test_format_time_noon_and_midnight() -> None:
    assert _format_time(datetime(2026, 5, 4, 12, 0)) == "12:00 PM"
    assert _format_time(datetime(2026, 5, 4, 0, 30)) == "12:30 AM"


def test_format_full_date() -> None:
    # 2026-05-04 is a Monday.
    assert _format_full_date(datetime(2026, 5, 4).date()) == "Monday, May 4th, 2026"


def test_format_short_date() -> None:
    assert _format_short_date(datetime(2026, 5, 5).date()) == "Tuesday, May 5th"


# ─── Regex tests ────────────────────────────────────────────────────────────

def test_time_regex() -> None:
    for s in (
        "what time is it",
        "what's the time",
        "what is the time",
        "tell me the time",
        "current time",
        "time check",
        "do you have the time",
        "do you know what time it is",
    ):
        assert _TIME_RE.match(s), f"expected match: {s!r}"
    assert not _TIME_RE.match("what's the date")
    assert not _TIME_RE.match("what day is it")


def test_full_date_regex() -> None:
    for s in (
        "what's the date",
        "what is the date",
        "what's today's date",
        "what is today's date",
        "what's the date today",
        "what is the date today",
        "what's today",
        "what is today",
        "today's date",
        "what date is it",
        "what date is it today",
    ):
        assert _FULL_DATE_RE.match(s), f"expected match: {s!r}"
    assert not _FULL_DATE_RE.match("what time is it")
    assert not _FULL_DATE_RE.match("what day is it")


def test_day_of_week_regex() -> None:
    for s in (
        "what day is it",
        "what day is today",
        "what day of the week is it",
    ):
        assert _DAY_OF_WEEK_RE.match(s), f"expected match: {s!r}"
    # "what day is tomorrow" goes to _TOMORROW_RE, not here.
    assert not _DAY_OF_WEEK_RE.match("what day is tomorrow")


def test_year_regex() -> None:
    for s in ("what year is it", "what's the year", "what is the year", "current year"):
        assert _YEAR_RE.match(s), f"expected match: {s!r}"


def test_month_regex() -> None:
    for s in ("what month is it", "what's the month", "current month"):
        assert _MONTH_RE.match(s), f"expected match: {s!r}"


def test_tomorrow_regex() -> None:
    for s in ("what's tomorrow", "what's tomorrow's date", "tomorrow's date", "what day is tomorrow"):
        assert _TOMORROW_RE.match(s), f"expected match: {s!r}"


def test_yesterday_regex() -> None:
    for s in ("what was yesterday", "what was yesterday's date", "yesterday's date", "what day was yesterday"):
        assert _YESTERDAY_RE.match(s), f"expected match: {s!r}"


# ─── Behavior tests (frozen clock) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_time_response() -> None:
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    fixed = datetime(2026, 5, 4, 15, 47)
    with patch("domovoi.handlers.clock._now", return_value=fixed):
        m = _TIME_RE.match("what time is it")
        assert m
        response = await ClockHandler()._time_from_match(m, ctx, None)
    assert response.text == "It's 3:47 PM."
    assert response.matched_handler == "clock"


@pytest.mark.asyncio
async def test_date_response() -> None:
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    fixed = datetime(2026, 5, 4, 10, 30)  # Monday
    with patch("domovoi.handlers.clock._now", return_value=fixed):
        m = _FULL_DATE_RE.match("what's the date today")
        assert m
        response = await ClockHandler()._date_from_match(m, ctx, None)
    assert response.text == "It's Monday, May 4th, 2026."


@pytest.mark.asyncio
async def test_day_of_week_response() -> None:
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    fixed = datetime(2026, 5, 4)  # Monday
    with patch("domovoi.handlers.clock._now", return_value=fixed):
        m = _DAY_OF_WEEK_RE.match("what day is it")
        assert m
        response = await ClockHandler()._day_of_week_from_match(m, ctx, None)
    assert response.text == "It's Monday."


@pytest.mark.asyncio
async def test_year_response() -> None:
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    with patch("domovoi.handlers.clock._now", return_value=datetime(2026, 5, 4)):
        m = _YEAR_RE.match("what year is it")
        assert m
        response = await ClockHandler()._year_from_match(m, ctx, None)
    assert response.text == "It's 2026."


@pytest.mark.asyncio
async def test_month_response() -> None:
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    with patch("domovoi.handlers.clock._now", return_value=datetime(2026, 5, 4)):
        m = _MONTH_RE.match("what month is it")
        assert m
        response = await ClockHandler()._month_from_match(m, ctx, None)
    assert response.text == "It's May."


@pytest.mark.asyncio
async def test_tomorrow_response() -> None:
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    fixed = datetime(2026, 5, 4)  # Monday → tomorrow Tuesday May 5th
    with patch("domovoi.handlers.clock._now", return_value=fixed):
        m = _TOMORROW_RE.match("what's tomorrow")
        assert m
        response = await ClockHandler()._tomorrow_from_match(m, ctx, None)
    assert response.text == "Tomorrow is Tuesday, May 5th."


@pytest.mark.asyncio
async def test_yesterday_response() -> None:
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    fixed = datetime(2026, 5, 4)  # Monday → yesterday Sunday May 3rd
    with patch("domovoi.handlers.clock._now", return_value=fixed):
        m = _YESTERDAY_RE.match("yesterday's date")
        assert m
        response = await ClockHandler()._yesterday_from_match(m, ctx, None)
    assert response.text == "Yesterday was Sunday, May 3rd."


# ─── Tool-call entry ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_from_tool_time() -> None:
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    with patch("domovoi.handlers.clock._now", return_value=datetime(2026, 5, 4, 8, 0)):
        response = await ClockHandler().execute_from_tool({"kind": "time"}, ctx, None)
    assert response.text == "It's 8:00 AM."
    assert response.matched_handler == "clock"


@pytest.mark.asyncio
async def test_execute_from_tool_unknown_kind() -> None:
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    response = await ClockHandler().execute_from_tool({"kind": "decade"}, ctx, None)
    assert "decade" in response.text
    assert response.matched_handler == "clock"
