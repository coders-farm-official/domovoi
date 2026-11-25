"""A timer, alarm or reminder asked for without its detail (M2, case 10.6).

"Can you set a timer?" got the model's hedge ("I can't do that yet, I
don't think") although timers are the first thing Domovoi does. Now the
timer asks "Sure. How long should it be?" and the next turn ("five minutes") sets
it; "set an alarm" asks for the time and "set a reminder" for the task
and the time (domovoi/handlers/shared/ask_detail.py).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.confirmations import PENDING_CONFIRMATION_KEY
from domovoi.db.repositories import SessionRepository
from domovoi.handlers.reminder import ASK_ALARM_TEXT, ASK_REMINDER_TEXT
from domovoi.handlers.shared import ask_detail
from domovoi.handlers.timer import ASK_DURATION_TEXT
from domovoi.models import Context, Intent
from domovoi.router import plan_route, route
from domovoi.tests.conftest import requires_db

# ─── Which turns ask ──────────────────────────────────────────────────────


@pytest.mark.parametrize(("said", "handler", "method"), [
    ("Can you set a timer?", "timer", "_ask_duration"),
    ("set a timer", "timer", "_ask_duration"),
    ("Could you start a timer for me, please?", "timer", "_ask_duration"),
    ("timer", "timer", "_ask_duration"),
    ("Can you set an alarm?", "reminder", "_ask_alarm_time"),
    ("set my alarm", "reminder", "_ask_alarm_time"),
    ("Can you set a reminder?", "reminder", "_ask_reminder"),
    ("remind me", "reminder", "_ask_reminder"),
    ("set a quick reminder", "reminder", "_ask_reminder"),
])
def test_the_bare_requests_ask(said: str, handler: str, method: str) -> None:
    plan = plan_route(said)
    assert plan is not None and plan.handler.name == handler, said
    assert plan.fast_path.method.__name__ == method, said


@pytest.mark.parametrize(("said", "handler", "method"), [
    # Neighbours keep theirs.
    ("set a timer for 10 minutes", "timer", "_create_from_match"),
    ("cancel the timer", "timer", "_cancel_from_match"),
    ("what timers do I have", "timer", "_list_from_match"),
    ("set an alarm for 7", "reminder", "_alarm_from_match"),
    ("is my alarm set", "reminder", "_list_alarms_from_match"),
    ("remind me to call mom in 10 minutes", "reminder", "_create_from_match"),
    ("set a reminder for 10 minutes", "reminder", "_create_from_match"),
    ("can you set timers", "house_facts", "_capability"),
])
def test_neighbours_are_unchanged(said: str, handler: str, method: str) -> None:
    plan = plan_route(said)
    assert plan is not None and plan.handler.name == handler, said
    assert plan.fast_path.method.__name__ == method, said


@pytest.mark.parametrize("said", [
    "my timer didn't go off", "the timer is beeping", "timer for the pasta", "set the alarm off",
])
def test_talk_about_a_timer_doesnt_ask(said: str) -> None:
    plan = plan_route(said)
    assert plan is None or plan.fast_path.method.__name__ not in (
        "_ask_duration", "_ask_alarm_time", "_ask_reminder"), said


# ─── How a reply is completed ─────────────────────────────────────────────


@pytest.mark.parametrize(("reply", "first"), [
    ("five minutes", "timer for five minutes"),
    ("um, ten minutes please", "timer for ten minutes"),
    ("for 10 minutes", "timer for for 10 minutes"),
    ("make it half an hour", "timer for half an hour"),
])
def test_candidates(reply: str, first: str) -> None:
    got = ask_detail.candidates({"prefixes": ["timer for "]}, reply)
    assert got[0] == first
    assert all(c.startswith("timer for ") for c in got)


def test_the_preposition_is_tried_without_too() -> None:
    assert "timer for 10 minutes" in ask_detail.candidates({"prefixes": ["timer for "]}, "for 10 minutes")


# ─── The whole conversation ───────────────────────────────────────────────


class _Talk:
    """One room's conversation through route(), one session throughout."""

    def __init__(self, db) -> None:
        self.db = db
        self.session_id = None

    async def __call__(self, said: str):
        ctx = Context(room_id="kitchen", session_id=self.session_id, online=True)
        resp = await route(Intent(transcript=said, room_id="kitchen"), ctx, self.db)
        await self.db.commit()
        self.session_id = resp.session_id
        return resp

    async def pending(self):
        ctx = await SessionRepository(self.db).get_context(self.session_id) or {}
        return ctx.get(PENDING_CONFIRMATION_KEY)


async def _timers(db) -> list[tuple]:
    return [tuple(r) for r in (await db.execute(text(
        "SELECT label, message, room_id FROM timers ORDER BY id"))).all()]


@pytest.fixture
async def clean_timers(db_session):
    await db_session.execute(text("DELETE FROM timers"))
    await db_session.commit()
    yield db_session


@requires_db
@pytest.mark.asyncio
async def test_can_you_set_a_timer_then_five_minutes(clean_timers) -> None:
    db = clean_timers
    talk = _Talk(db)
    asked = await talk("Can you set a timer?")
    assert asked.text == ASK_DURATION_TEXT and "how long" in asked.text.lower()
    assert asked.matched_handler == "timer" and asked.expect_followup is True
    assert (await talk.pending())["kind"] == "core.timer_how_long"
    done = await talk("Five minutes.")
    assert done.text == "Timer set for 5 minutes."
    assert done.matched_handler == "timer"
    assert len(await _timers(db)) == 1
    assert await talk.pending() is None


@requires_db
@pytest.mark.asyncio
async def test_never_mind_drops_the_question(clean_timers) -> None:
    db = clean_timers
    talk = _Talk(db)
    await talk("set a timer")
    r = await talk("never mind")
    assert r.text == "Okay."
    assert await _timers(db) == []


@requires_db
@pytest.mark.asyncio
async def test_a_reply_that_isnt_a_duration_is_routed_as_usual(clean_timers) -> None:
    db = clean_timers
    talk = _Talk(db)
    await talk("set a timer")
    r = await talk("what time is it")
    assert r.matched_handler == "clock"
    assert await _timers(db) == []


@requires_db
@pytest.mark.asyncio
async def test_can_you_set_an_alarm_then_a_time(clean_timers) -> None:
    db = clean_timers
    talk = _Talk(db)
    asked = await talk("Can you set an alarm?")
    assert asked.text == ASK_ALARM_TEXT
    done = await talk("7 am")
    assert done.matched_handler == "reminder"
    rows = await _timers(db)
    assert len(rows) == 1 and rows[0][0].lower() == "7 am alarm", rows


@requires_db
@pytest.mark.asyncio
async def test_can_you_set_a_reminder_then_what_and_when(clean_timers) -> None:
    db = clean_timers
    talk = _Talk(db)
    asked = await talk("Can you set a reminder?")
    assert asked.text == ASK_REMINDER_TEXT
    done = await talk("call mom in 10 minutes")
    assert done.matched_handler == "reminder"
    rows = await _timers(db)
    assert len(rows) == 1 and rows[0][1] == "call mom", rows
