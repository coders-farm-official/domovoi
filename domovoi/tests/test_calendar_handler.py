"""CalendarHandler: the household calendar by voice, read-only (2026-10-17).

Before: "what's on my calendar today" reached the answer model's volatile
gate and was offered as a web search; "do I have anything on Friday" was
the library's "do I have X". The household clock is pinned to US Eastern
(household_time_testkit) so day boundaries, DST days included, are exact.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from domovoi.handlers.household_calendar import CalendarHandler
from domovoi.models import Context
from domovoi.tests.conftest import fast_path_winner, requires_db
from domovoi.tests.household_time_testkit import EASTERN, at


def _ctx() -> Context:
    return Context(session_id=uuid4(), room_id="kitchen", online=True)


def _utc(*wall: int) -> datetime:
    return datetime(*wall).replace(tzinfo=EASTERN).astimezone(timezone.utc)


async def _event(db_session, title: str, start: tuple, end: tuple | None = None, *, all_day=False):
    await db_session.execute(
        text(
            "INSERT INTO calendar_events (title, starts_at, ends_at, all_day, source, created_via) "
            "VALUES (:t, :s, :e, :a, 'local', 'manual')"
        ),
        {"t": title, "s": _utc(*start), "e": _utc(*end) if end else None, "a": all_day},
    )
    await db_session.commit()


async def _say(db_session, said: str) -> str:
    from domovoi.router import plan_route

    plan = plan_route(said)
    assert plan is not None and plan.handler.name == "calendar", said
    r = await plan.fast_path.method(plan.handler, plan.match, _ctx(), db_session)
    return r.text


# ─── Routing (DB-free) ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "said",
    [
        "what's on my calendar today", "What's on my calendar today?", "what's on my calendar",
        "what is on the calendar tomorrow", "what's on my calendar for friday",
        "what's on my calendar this week", "what do i have on my calendar tomorrow",
        "what's my schedule like tomorrow", "what's my agenda for friday",
        "read me my calendar", "check my calendar for this week", "what's on today",
        "what's on tomorrow", "what do i have tomorrow", "what do we have planned this weekend",
        "what's next on my calendar", "what's coming up on the calendar",
        "what's my next appointment", "when's my next event",
        "do i have anything on friday", "do I have anything on Friday?",
        "do i have anything tomorrow", "is there anything on my calendar this weekend",
        "do we have any plans this weekend", "am i free on friday", "am i busy tomorrow",
        "add the dentist to my calendar on friday", "put soccer on the calendar",
    ],
)
def test_calendar_questions_are_calendar_fast_paths(said: str) -> None:
    assert fast_path_winner(said) == "calendar"


@pytest.mark.parametrize(
    ("said", "owner"),
    [
        ("do i have anything by weezer", "library"),   # "do i have X" is the library's
        ("do i have ok computer", "library"),
        ("what's tomorrow", "clock"),                    # the date
        ("what day is tomorrow", "clock"),
        ("what's new in science", "news"),
    ],
)
def test_calendar_poaches_nothing(said: str, owner: str) -> None:
    assert fast_path_winner(said) == owner


def test_the_tool_is_offered_only_when_the_calendar_is_named() -> None:
    from domovoi.router import offered_tool_schemas

    def offered(t: str) -> list[str]:
        return [s["name"] for s in offered_tool_schemas(t)]

    assert "calendar" in offered("anything happening on my calendar this friday")
    assert "calendar" in offered("when is my dentist appointment")
    assert "calendar" not in offered("")
    assert "calendar" not in offered("play some jazz in the kitchen")
    assert "calendar" not in offered("schedule a reminder for later")
    # it joins the end of the list, after the ordinary tools
    assert offered("anything happening on my calendar this friday")[: len(offered(""))] == offered("")


# ─── Reading it (DB) ──────────────────────────────────────────────────────

WEDNESDAY_10AM = (2026, 10, 14, 10, 0)


@requires_db
@pytest.mark.asyncio
async def test_today_reads_what_is_still_to_come(db_session) -> None:
    await _event(db_session, "Standup", (2026, 10, 14, 9, 0), (2026, 10, 14, 9, 30))
    await _event(db_session, "Dentist", (2026, 10, 14, 14, 0))
    await _event(db_session, "Soccer practice", (2026, 10, 14, 18, 30))
    await _event(db_session, "Book club", (2026, 10, 15, 19, 0))
    with at(*WEDNESDAY_10AM):
        assert await _say(db_session, "what's on my calendar today") == (
            "Still to come today: Dentist at 2 PM and Soccer practice at 6:30 PM."
        )
        # no day said is today
        assert await _say(db_session, "what's on my calendar") == (
            "Still to come today: Dentist at 2 PM and Soccer practice at 6:30 PM."
        )
        assert await _say(db_session, "what do i have tomorrow") == (
            "Tomorrow you have Book club at 7 PM."
        )
        assert await _say(db_session, "what's next on my calendar") == (
            "Next on the calendar: Dentist at 2 PM today."
        )
    with at(2026, 10, 14, 7, 0):
        assert await _say(db_session, "what's on my calendar today") == (
            "Today you have 3 things: Standup at 9 AM, Dentist at 2 PM, and Soccer practice at 6:30 PM."
        )
    with at(2026, 10, 14, 21, 0):
        assert await _say(db_session, "what's on my calendar today") == (
            "That's everything for today: nothing else on the calendar."
        )


@requires_db
@pytest.mark.asyncio
async def test_a_weekday_and_anything_on_it(db_session) -> None:
    await _event(db_session, "Dentist", (2026, 10, 16, 10, 0))
    await _event(db_session, "Mom's birthday", (2026, 10, 16, 0, 0), all_day=True)
    with at(*WEDNESDAY_10AM):
        assert await _say(db_session, "do I have anything on Friday") == (
            "Yes. On Friday you have 2 things: Mom's birthday, all day and Dentist at 10 AM."
        )
        assert await _say(db_session, "do i have anything on saturday") == (
            "No, nothing on the calendar on Saturday."
        )
        assert await _say(db_session, "am i free on saturday") == (
            "Yes, nothing on the calendar on Saturday."
        )
        assert (await _say(db_session, "am i free on friday")).startswith("Not completely. On Friday")
        assert await _say(db_session, "what's on my calendar for thursday") == (
            "Nothing on the calendar tomorrow. The next thing is Mom's birthday, all day on Friday."
        )


@requires_db
@pytest.mark.asyncio
async def test_this_week_and_next(db_session) -> None:
    await _event(db_session, "Dentist", (2026, 10, 15, 10, 0))
    await _event(db_session, "Soccer", (2026, 10, 17, 9, 0))
    await _event(db_session, "Book club", (2026, 10, 17, 19, 0))
    await _event(db_session, "Flight", (2026, 10, 20, 6, 15))
    with at(*WEDNESDAY_10AM):
        assert await _say(db_session, "what's on my calendar this week") == (
            "This week: tomorrow, Dentist at 10 AM; on Saturday, Soccer at 9 AM and Book club at 7 PM."
        )
        assert await _say(db_session, "what's on my calendar next week") == (
            "Next week: on Tuesday, Flight at 6:15 AM."
        )
        assert await _say(db_session, "do we have any plans this weekend") == (
            "Yes. This weekend: on Saturday, Soccer at 9 AM and Book club at 7 PM."
        )


@requires_db
@pytest.mark.asyncio
async def test_an_empty_calendar(db_session) -> None:
    with at(*WEDNESDAY_10AM):
        assert await _say(db_session, "what's on my calendar tomorrow") == "Nothing on the calendar tomorrow."
        assert await _say(db_session, "what's next on my calendar") == "Nothing coming up on the calendar."


@requires_db
@pytest.mark.asyncio
async def test_today_is_the_households_day_on_a_dst_change(db_session) -> None:
    """2026-11-01 is 25 hours long in US Eastern: 23:30 that night is still
    "today", 00:30 the next morning isn't."""
    await _event(db_session, "Late one", (2026, 11, 1, 23, 30))
    await _event(db_session, "Early one", (2026, 11, 2, 0, 30))
    with at(2026, 11, 1, 8, 0):
        assert await _say(db_session, "what's on my calendar today") == (
            "Today you have Late one at 11:30 PM."
        )
        assert await _say(db_session, "what's on my calendar tomorrow") == (
            "Tomorrow you have Early one at 12:30 AM."
        )


@requires_db
@pytest.mark.asyncio
async def test_nothing_is_ever_written(db_session) -> None:
    with at(*WEDNESDAY_10AM):
        said = await _say(db_session, "add the dentist to my calendar on friday")
    assert said.startswith("I can only read the calendar")
    count = (await db_session.execute(text("SELECT count(*) FROM calendar_events"))).scalar_one()
    assert count == 0


@requires_db
@pytest.mark.asyncio
async def test_the_tool_call_reads_the_same(db_session) -> None:
    await _event(db_session, "Book club", (2026, 10, 15, 19, 0))
    with at(*WEDNESDAY_10AM):
        h = CalendarHandler()
        r = await h.execute_from_tool({"when": "tomorrow"}, _ctx(), db_session)
        assert r.text == "Tomorrow you have Book club at 7 PM."
        r = await h.execute_from_tool({"when": "next"}, _ctx(), db_session)
        assert r.text == "Next on the calendar: Book club at 7 PM tomorrow."
        r = await h.execute_from_tool({}, _ctx(), db_session)
        assert r.text.startswith("Nothing on the calendar today.")
