"""Reminders, wake-ups and alarms at a clock time (2026-10-17).

Before: "remind me at 6 pm …" at 23:20 → "in 10 hours"; "wake me up at
7" → "in 7 hours"; "remind me tomorrow morning …" → "in 24 hours" (the tool
model guessing a duration). Now each is a fast path, placed on the
household's wall clock (US Eastern here, DST included, via
household_time_testkit), and the reply says the clock time.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from domovoi import household_time
from domovoi.handlers.reminder import ReminderHandler, _LIST_RE
from domovoi.models import Context
from domovoi.tests.conftest import fast_path_winner, requires_db
from domovoi.tests.household_time_testkit import EASTERN, at
from domovoi.timer_delivery import fire_line


def _ctx(room: str = "kitchen") -> Context:
    return Context(session_id=uuid4(), room_id=room, online=True)


def _utc(*wall: int) -> datetime:
    return datetime(*wall).replace(tzinfo=EASTERN).astimezone(timezone.utc)


async def _say(db_session, said: str, ctx: Context | None = None):
    from domovoi.router import plan_route

    plan = plan_route(said)
    assert plan is not None and plan.handler.name == "reminder", said
    response = await plan.fast_path.method(plan.handler, plan.match, ctx or _ctx(), db_session)
    await db_session.commit()
    return response


async def _rows(db_session) -> list[tuple]:
    rows = await db_session.execute(
        text("SELECT label, message, expires_at, created_at FROM timers ORDER BY expires_at")
    )
    return [tuple(r) for r in rows.all()]


# ─── Routing (DB-free) ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "said",
    [
        "remind me at 6 pm to take the bins out",
        "Remind me at 6 p.m. to take the bins out.",
        "remind me to take the bins out at 6 pm",
        "remind me to call mom at five",
        "remind me tomorrow morning to call the plumber",
        "remind me tomorrow to call mom",
        "remind me on friday at 5 pm to call grandma",
        "remind me about the dentist on friday",
        "remind me at 6 pm",
        "set a reminder for 6 pm",
        "set a reminder for tomorrow at 9",
        "i need a reminder at noon",
        "set a laundry reminder for 6 pm",
        "wake me up at 7",
        "Wake me up at 7.",
        "wake me up tomorrow at 6:30",
        "wake me up in 20 minutes",
        "set an alarm for 7",
        "set an alarm for 7 am tomorrow",
        "cancel my alarm",
        "cancel the 7 am alarm",
    ],
)
def test_clock_time_commands_are_reminder_fast_paths(said: str) -> None:
    assert fast_path_winner(said) == "reminder"


@pytest.mark.parametrize(
    "said",
    [
        # a time inside a "that" task is the task's ("the party is at 7")
        "remind me that the party is at 7",
        # an alarm for a duration still goes where it went (the tool model)
        "set an alarm for 10 minutes",
        # a day word inside the task, not at its end, is no time
        "remind me to watch the friday game",
    ],
)
def test_what_is_not_a_clock_time_command(said: str) -> None:
    assert fast_path_winner(said) is None


def test_durations_still_take_the_duration_paths() -> None:
    from domovoi.router import plan_route

    for said in ("remind me to call mom in 10 minutes", "remind me in 2 hours to take the bins out",
                 "set a reminder for 10 minutes"):
        plan = plan_route(said)
        assert plan.fast_path.method is ReminderHandler._create_from_match, said


def test_clock_time_paths_never_commit_early() -> None:
    from domovoi.early_commit import early_commit_for

    for said in ("remind me at 6 pm", "wake me up at 7", "set an alarm for 7",
                 "remind me tomorrow", "set a reminder for 6 pm"):
        assert early_commit_for(said) is None, said


# ─── The fired line (DB-free) ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "message", "duration", "expected"),
    [
        ("6 PM reminder", "", 37200, "Here's your 6 PM reminder."),
        ("7 AM alarm", "", 27600, "Here's your 7 AM alarm."),
        ("7 AM alarm", "time to wake up", 27600, "Reminder: time to wake up"),
        ("take the bins out", "take the bins out", 37200, "Reminder: take the bins out"),
        # what it said before, unchanged
        ("10 minute reminder", "", 600, "Here's your 10 minute reminder."),
        (None, "", 600, "Here's your 10 minute reminder."),
    ],
)
def test_a_clock_time_reminder_fires_with_its_name(label, message, duration, expected) -> None:
    line = fire_line(
        "reminder", label=label, message=message, duration_sec=duration,
        origin_room_id="kitchen", target_room_id="kitchen", late_sec=0,
    )
    assert line == expected


# ─── Setting them (DB) ────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_six_pm_said_late_at_night_is_tomorrows(db_session) -> None:
    with at(2026, 10, 16, 23, 20) as now:
        r = await _say(db_session, "remind me at 6 pm to take the bins out")
    assert r.text == "I'll remind you tomorrow at 6 PM to take the bins out."
    [(label, message, expires, created)] = await _rows(db_session)
    assert (label, message) == ("take the bins out", "take the bins out")
    assert expires == _utc(2026, 10, 17, 18, 0)
    assert created == now


@requires_db
@pytest.mark.asyncio
async def test_six_pm_said_in_the_afternoon_is_todays(db_session) -> None:
    with at(2026, 10, 16, 14, 0):
        r = await _say(db_session, "remind me to take the bins out at 6 pm")
    assert r.text == "I'll remind you at 6 PM to take the bins out."
    [(_, _, expires, _)] = await _rows(db_session)
    assert expires == _utc(2026, 10, 16, 18, 0)


@requires_db
@pytest.mark.asyncio
async def test_wake_me_up_at_7_is_7_am(db_session) -> None:
    with at(2026, 10, 16, 14, 0):
        r = await _say(db_session, "wake me up at 7")
    assert r.text == "I'll wake you tomorrow at 7 AM."
    [(label, message, expires, _)] = await _rows(db_session)
    assert (label, message) == ("7 AM alarm", "time to wake up")
    assert expires == _utc(2026, 10, 17, 7, 0)


@requires_db
@pytest.mark.asyncio
async def test_tomorrow_morning_is_said_back_as_a_clock_time(db_session) -> None:
    with at(2026, 10, 16, 23, 20):
        r = await _say(db_session, "remind me tomorrow morning to call the plumber")
    assert r.text == "I'll remind you tomorrow at 9 AM to call the plumber."
    [(_, message, expires, _)] = await _rows(db_session)
    assert message == "call the plumber"
    assert expires == _utc(2026, 10, 17, 9, 0)


@requires_db
@pytest.mark.asyncio
async def test_a_reminder_with_no_task_is_named_by_its_time(db_session) -> None:
    with at(2026, 10, 16, 14, 0):
        r = await _say(db_session, "set a reminder for 6 pm")
    assert r.text == "I'll remind you at 6 PM."
    [(label, message, _, _)] = await _rows(db_session)
    assert (label, message) == ("6 PM reminder", "")


@requires_db
@pytest.mark.asyncio
async def test_an_alarm(db_session) -> None:
    with at(2026, 10, 16, 23, 20):
        r = await _say(db_session, "set an alarm for 6:30")
    assert r.text == "Alarm set for 6:30 AM tomorrow."
    [(label, message, expires, _)] = await _rows(db_session)
    assert (label, message) == ("6:30 AM alarm", "")
    assert expires == _utc(2026, 10, 17, 6, 30)


@requires_db
@pytest.mark.asyncio
async def test_a_named_reminder_at_a_time(db_session) -> None:
    with at(2026, 10, 16, 14, 0):
        r = await _say(db_session, "set a laundry reminder for 6 pm")
    assert r.text == "Your laundry reminder is set for 6 PM."


@requires_db
@pytest.mark.asyncio
async def test_a_weekday(db_session) -> None:
    with at(2026, 10, 14, 10, 0):  # a Wednesday
        r = await _say(db_session, "remind me on friday at 5 pm to call grandma")
    assert r.text == "I'll remind you on Friday at 5 PM to call grandma."
    [(_, _, expires, _)] = await _rows(db_session)
    assert expires == _utc(2026, 10, 16, 17, 0)


@requires_db
@pytest.mark.asyncio
async def test_a_time_gone_tonight_is_refused_not_moved(db_session) -> None:
    with at(2026, 10, 16, 22, 0):
        r = await _say(db_session, "remind me tonight at 9 to lock up")
    assert r.text.startswith("9 PM has already gone tonight.")
    assert await _rows(db_session) == []


@requires_db
@pytest.mark.asyncio
async def test_today_with_no_time_asks_for_one(db_session) -> None:
    with at(2026, 10, 16, 14, 0):
        r = await _say(db_session, "remind me today to call mom")
    assert r.text.startswith("What time?")
    assert await _rows(db_session) == []


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("wall_now", "expected_hours"),
    [((2026, 3, 7, 22, 0), 8), ((2026, 10, 31, 22, 0), 10)],
)
async def test_tomorrow_at_7_across_a_dst_night(db_session, wall_now, expected_hours) -> None:
    with at(*wall_now):
        r = await _say(db_session, "remind me tomorrow at 7 to water the plants")
    assert r.text == "I'll remind you tomorrow at 7 AM to water the plants."
    [(_, _, expires, created)] = await _rows(db_session)
    assert expires - created == timedelta(hours=expected_hours)
    with household_time.use_zone(EASTERN):
        assert household_time.to_local(expires).hour == 7


@requires_db
@pytest.mark.asyncio
async def test_durations_keep_working(db_session) -> None:
    r = await _say(db_session, "remind me in 2 hours to take the bins out")
    assert r.text == "I'll remind you to take the bins out in 2 hours."
    [(_, _, expires, created)] = await _rows(db_session)
    assert expires - created == timedelta(hours=2)


@requires_db
@pytest.mark.asyncio
async def test_wake_me_up_in_twenty_minutes(db_session) -> None:
    r = await _say(db_session, "wake me up in 20 minutes")
    assert r.text == "I'll wake you in 20 minutes."
    [(label, message, expires, created)] = await _rows(db_session)
    assert (label, message) == ("20 minute alarm", "time to wake up")
    assert expires - created == timedelta(minutes=20)


# ─── The tool model's guess, corrected (DB) ───────────────────────────────


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    ["remind me at 6 pm to feed the dog", "feed the dog at 6 pm", "Feed the dog at 6 PM."],
)
async def test_a_tool_call_whose_message_holds_the_time(db_session, message) -> None:
    """qwen3:8b passes the time in ``message`` and guesses duration_sec
    (10 hours at 20:00 would be 6 AM). The time said wins."""
    with at(2026, 10, 16, 20, 0):
        r = await ReminderHandler().execute_from_tool(
            {"action": "create", "message": message, "duration_sec": 36000}, _ctx(), db_session,
        )
        await db_session.commit()
    assert r.text.lower() == "i'll remind you tomorrow at 6 pm to feed the dog."
    [(_, _, expires, _)] = await _rows(db_session)
    assert expires == _utc(2026, 10, 17, 18, 0)


@requires_db
@pytest.mark.asyncio
async def test_a_tool_call_with_no_time_in_its_message_keeps_its_duration(db_session) -> None:
    r = await ReminderHandler().execute_from_tool(
        {"action": "create", "message": "call mom", "duration_sec": 600}, _ctx(), db_session,
    )
    await db_session.commit()
    assert r.text == "I'll remind you to call mom in 10 minutes."


# ─── Listing and cancelling (DB) ──────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_the_list_says_a_far_reminder_by_its_clock_time(db_session) -> None:
    from domovoi.db.repositories import utcnow

    real_now = utcnow()
    zone_now = real_now.astimezone(EASTERN).replace(tzinfo=None)
    with household_time.use_zone(EASTERN, lambda: real_now):
        await _say(db_session, "remind me tomorrow at 7 to water the plants")
        listed = await ReminderHandler()._list_from_match(
            _LIST_RE.match("what reminders do i have"), _ctx(), db_session
        )
    target = datetime.combine(zone_now.date() + timedelta(days=1), datetime.min.time()).replace(hour=7)
    assert target > zone_now
    assert listed.text == "Tomorrow at 7 AM: water the plants."


@requires_db
@pytest.mark.asyncio
async def test_cancel_an_alarm_by_its_time(db_session) -> None:
    with at(2026, 10, 16, 14, 0):
        await _say(db_session, "wake me up at 7")
        await _say(db_session, "set an alarm for 9 pm")
        # a task that ends in the word is no alarm
        await _say(db_session, "remind me at 8 pm to check the smoke alarm")
        r = await _say(db_session, "cancel the 7 am alarm")
    assert r.text == "Cancelled the alarm."
    labels = [row[0] for row in await _rows(db_session)]
    assert labels == ["check the smoke alarm", "9 PM alarm"]
    with at(2026, 10, 16, 14, 1):
        r = await _say(db_session, "cancel my alarms")
    assert r.text == "Cancelled the alarm."
    assert [row[0] for row in await _rows(db_session)] == ["check the smoke alarm"]


@requires_db
@pytest.mark.asyncio
async def test_cancel_an_alarm_when_there_is_none_routes_on(db_session) -> None:
    """No alarm to cancel: the fast path declines (None), so the turn goes
    where "cancel the alarm" went before (the tool model)."""
    from domovoi.router import plan_route

    plan = plan_route("cancel the alarm")
    r = await plan.fast_path.method(plan.handler, plan.match, _ctx(), db_session)
    assert r is None


# ─── Going off (DB, the timer watcher) ────────────────────────────────────


class _Room:
    def __init__(self) -> None:
        self.announced: list[str] = []

    async def announce(self, text_: str, **_kw) -> None:
        self.announced.append(text_)


@requires_db
@pytest.mark.asyncio
async def test_a_clock_time_reminder_goes_off_through_the_watcher(db_session) -> None:
    from domovoi.tests.timer_fires_testkit import apply_v018

    await apply_v018()
    try:
        await _fire_clock_time_reminders(db_session)
    finally:
        await db_session.rollback()
        await apply_v018()


async def _fire_clock_time_reminders(db_session) -> None:
    from domovoi.db.repositories import utcnow
    from domovoi.workers.timer_watcher import TimerWatcher

    # Set them three hours ago on the household clock, for an hour after
    # that: both are due now.
    then = utcnow() - timedelta(hours=3)
    local_then = then.astimezone(EASTERN).replace(tzinfo=None)
    due = (local_then + timedelta(hours=1)).replace(second=0, microsecond=0)
    clock = f"{due.hour % 12 or 12}:{due.minute:02d} {'am' if due.hour < 12 else 'pm'}"
    with household_time.use_zone(EASTERN, lambda: then):
        await _say(db_session, f"remind me at {clock}")
        await _say(db_session, f"remind me at {clock} to take the bins out")
    room = _Room()
    app = SimpleNamespace(state=SimpleNamespace(active_sessions={"kitchen": room}))
    watcher = TimerWatcher(app=app)
    assert await watcher.tick() == 2
    for _ in range(50):
        tasks = list(watcher.delivery._tasks.values())
        if not tasks:
            break
        await asyncio.gather(*tasks, return_exceptions=True)
    from domovoi.handlers.shared.clock_time import spoken_clock

    assert set(room.announced) == {
        f"Here's your {spoken_clock(due)} reminder, from 2 hours ago.",
        "Reminder from 2 hours ago: take the bins out",
    }
