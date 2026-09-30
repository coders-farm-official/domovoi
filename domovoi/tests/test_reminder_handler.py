from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from domovoi.db.repositories import TimerRepository, utcnow
from domovoi.handlers.reminder import (
    ReminderHandler,
    _CANCEL_RE,
    _CREATE_RE,
    _LIST_RE,
)
from domovoi.handlers.shared.number_words import parse_duration_seconds
from domovoi.models import Context
from domovoi.tests.conftest import fast_path_winner, requires_db


# ─── Regex tests ────────────────────────────────────────────────────────────

def test_create_regex() -> None:
    m = _CREATE_RE.match("remind me to call mom in 10 minutes")
    assert m
    assert m.group("message") == "call mom"
    assert m.group("duration") == "10 minutes"
    assert parse_duration_seconds(m.group("duration")) == 600


def test_create_regex_singular_unit() -> None:
    m = _CREATE_RE.match("remind me to take the trash out in 1 hour")
    assert m and parse_duration_seconds(m.group("duration")) == 3600


def test_create_regex_seconds() -> None:
    m = _CREATE_RE.match("remind me to check the oven in 90 seconds")
    assert m and parse_duration_seconds(m.group("duration")) == 90


# (transcript, message, seconds) — the digit rows stay so a grammar change
# can't regress them; the rest are the forms Whisper actually produces.
_CREATE_CASES: list[tuple[str, str, int]] = [
    ("remind me to call mom in 10 minutes", "call mom", 600),
    ("remind me to call mom in ten minutes", "call mom", 600),
    ("remind me to feed the cat in five minutes", "feed the cat", 300),
    ("remind me to take the trash out in an hour", "take the trash out", 3600),
    ("remind me to stretch in a minute", "stretch", 60),
    ("remind me to flip the pancakes in forty-five seconds", "flip the pancakes", 45),
    ("remind me to check the oven in half an hour", "check the oven", 1800),
    ("remind me to check the oven in an hour and a half", "check the oven", 5400),
    ("remind me to leave in one and a half hours", "leave", 5400),
    ("remind me to call the bank in 1 hour and 30 minutes", "call the bank", 5400),
    ("remind me to take a break in twenty minutes and 30 seconds", "take a break", 1230),
    # "in" inside the message must not be mistaken for the duration marker
    ("remind me to check in on grandma in ten minutes", "check in on grandma", 600),
    ("remind me to log in 5 minutes", "log", 300),
    ("remind me to bring in the washing in an hour", "bring in the washing", 3600),
]


@pytest.mark.parametrize(("transcript", "message", "seconds"), _CREATE_CASES)
def test_create_regex_accepts_spoken_durations(
    transcript: str, message: str, seconds: int
) -> None:
    m = _CREATE_RE.match(transcript)
    assert m, f"{transcript!r} did not match the create fast path"
    assert m.group("message") == message
    assert parse_duration_seconds(m.group("duration")) == seconds


@pytest.mark.parametrize(
    "transcript",
    [
        "remind me to call mom",
        "remind me to call mom in",
        "remind me to call mom in a while",
        "remind me to call mom in 5",
        "remind me to call mom in eleventy minutes",
        "remind me in 10 minutes",
        "remind me to call mom at 5pm",
    ],
)
def test_create_regex_requires_a_duration(transcript: str) -> None:
    assert _CREATE_RE.match(transcript) is None


def test_create_regex_does_not_match_set_a_timer() -> None:
    """Plain timer commands should still go to TimerHandler, not here."""
    assert not _CREATE_RE.match("set a timer for 10 minutes")
    assert not _CREATE_RE.match("timer for 5 minutes")


@pytest.mark.parametrize(
    "transcript",
    [
        "remind me to feed the cat in 10 minutes",
        "remind me to feed the cat in ten minutes",
        "Please remind me to take the trash out in an hour.",
        "remind me to check in on grandma in an hour and a half",
    ],
)
def test_spoken_durations_reach_the_reminder_fast_path(transcript: str) -> None:
    # Through the real band-sorted registry: reminder (140) must win, and
    # the widened grammar must not have handed anything to timer (160).
    assert fast_path_winner(transcript) == "reminder"


def test_reminder_without_duration_does_not_fast_path() -> None:
    assert fast_path_winner("remind me to call mom") is None


def test_list_regex() -> None:
    for s in (
        "what reminders do i have",
        "what are my reminders",
        "list my reminders",
        "list reminders",
    ):
        assert _LIST_RE.match(s), f"expected match: {s!r}"


def test_cancel_regex_with_label() -> None:
    m = _CANCEL_RE.match("cancel my reminder to call mom")
    assert m and m.group("label") == "call mom"
    m = _CANCEL_RE.match("cancel the reminder for the oven")
    assert m and m.group("label") == "the oven"


def test_cancel_regex_without_label() -> None:
    m = _CANCEL_RE.match("cancel my reminders")
    assert m and m.group("label") is None
    m = _CANCEL_RE.match("cancel the reminder")
    assert m and m.group("label") is None


# ─── Behavior tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_zero_duration_is_refused_before_touching_the_db() -> None:
    handler = ReminderHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _CREATE_RE.match("remind me to call mom in 0 minutes")
    assert m
    # The guard returns before any repository call, so no session is needed.
    response = await handler._create_from_match(m, ctx, None)  # type: ignore[arg-type]
    assert "when should i remind you" in response.text.lower()
    assert response.matched_handler == "reminder"


@requires_db
@pytest.mark.asyncio
async def test_create_from_spoken_duration_inserts_timer_with_message(db_session) -> None:
    handler = ReminderHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _CREATE_RE.match("remind me to check in on grandma in an hour and a half")
    assert m
    response = await handler._create_from_match(m, ctx, db_session)
    await db_session.commit()

    assert "i'll remind you to check in on grandma in 1 hour and 30 minutes" in response.text.lower()
    rows = (
        await db_session.execute(
            text("SELECT message, expires_at FROM timers WHERE message IS NOT NULL")
        )
    ).all()
    assert len(rows) == 1
    assert rows[0][0] == "check in on grandma"
    assert timedelta(seconds=5390) < (rows[0][1] - utcnow()) <= timedelta(seconds=5400)


@requires_db
@pytest.mark.asyncio
async def test_create_inserts_timer_with_message(db_session) -> None:
    handler = ReminderHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _CREATE_RE.match("remind me to call mom in 10 minutes")
    assert m
    response = await handler._create_from_match(m, ctx, db_session)
    await db_session.commit()

    assert "i'll remind you to call mom" in response.text.lower()
    # Confirm the row was actually persisted with message != null.
    rows = (
        await db_session.execute(
            text(
                "SELECT label, message, room_id FROM timers "
                "WHERE message IS NOT NULL"
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0][1] == "call mom"
    assert rows[0][2] == "kitchen"


@requires_db
@pytest.mark.asyncio
async def test_list_when_empty(db_session) -> None:
    handler = ReminderHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _LIST_RE.match("what reminders do i have")
    assert m
    response = await handler._list_from_match(m, ctx, db_session)
    assert "no reminders" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_list_with_one_reminder(db_session) -> None:
    repo = TimerRepository(db_session)
    await repo.create(
        expires_at=utcnow() + timedelta(minutes=5),
        label="call mom",
        message="call mom",
        room_id="kitchen",
    )
    await db_session.commit()

    handler = ReminderHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _LIST_RE.match("list my reminders")
    assert m
    response = await handler._list_from_match(m, ctx, db_session)
    assert "call mom" in response.text


@requires_db
@pytest.mark.asyncio
async def test_list_excludes_plain_timers(db_session) -> None:
    """A timer with NULL message is a plain timer, not a reminder. List
    should ignore it."""
    repo = TimerRepository(db_session)
    await repo.create(
        expires_at=utcnow() + timedelta(minutes=5),
        label=None,
        message=None,  # plain timer
        room_id="kitchen",
    )
    await db_session.commit()

    handler = ReminderHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _LIST_RE.match("what reminders do i have")
    assert m
    response = await handler._list_from_match(m, ctx, db_session)
    assert "no reminders" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_list_scoped_to_current_room(db_session) -> None:
    """Reminders set in the kitchen shouldn't surface for the garage."""
    repo = TimerRepository(db_session)
    await repo.create(
        expires_at=utcnow() + timedelta(minutes=5),
        label="kitchen note",
        message="kitchen note",
        room_id="kitchen",
    )
    await db_session.commit()

    handler = ReminderHandler()
    ctx = Context(session_id=None, room_id="garage", online=True)
    m = _LIST_RE.match("what reminders do i have")
    assert m
    response = await handler._list_from_match(m, ctx, db_session)
    assert "no reminders" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_cancel_by_label_substring(db_session) -> None:
    repo = TimerRepository(db_session)
    await repo.create(
        expires_at=utcnow() + timedelta(minutes=5),
        label="call mom",
        message="call mom",
        room_id="kitchen",
    )
    await repo.create(
        expires_at=utcnow() + timedelta(minutes=10),
        label="check oven",
        message="check oven",
        room_id="kitchen",
    )
    await db_session.commit()

    handler = ReminderHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _CANCEL_RE.match("cancel my reminder to call mom")
    assert m
    response = await handler._cancel_from_match(m, ctx, db_session)
    await db_session.commit()

    assert "cancelled" in response.text.lower()
    remaining = (
        await db_session.execute(text("SELECT label FROM timers WHERE message IS NOT NULL"))
    ).all()
    assert [r[0] for r in remaining] == ["check oven"]


@requires_db
@pytest.mark.asyncio
async def test_cancel_all_in_room(db_session) -> None:
    repo = TimerRepository(db_session)
    await repo.create(
        expires_at=utcnow() + timedelta(minutes=5),
        label="reminder1",
        message="reminder1",
        room_id="kitchen",
    )
    await repo.create(
        expires_at=utcnow() + timedelta(minutes=10),
        label="reminder2",
        message="reminder2",
        room_id="kitchen",
    )
    await repo.create(
        expires_at=utcnow() + timedelta(minutes=15),
        label="garage one",
        message="garage one",
        room_id="garage",
    )
    await db_session.commit()

    handler = ReminderHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _CANCEL_RE.match("cancel my reminders")
    assert m
    response = await handler._cancel_from_match(m, ctx, db_session)
    await db_session.commit()

    assert "cancelled 2 reminders" in response.text.lower()
    # Garage reminder untouched.
    remaining = (
        await db_session.execute(
            text("SELECT room_id FROM timers WHERE message IS NOT NULL")
        )
    ).all()
    assert [r[0] for r in remaining] == ["garage"]


@requires_db
@pytest.mark.asyncio
async def test_cancel_no_match_returns_helpful_text(db_session) -> None:
    handler = ReminderHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _CANCEL_RE.match("cancel my reminder to call mom")
    assert m
    response = await handler._cancel_from_match(m, ctx, db_session)
    assert "couldn't find" in response.text.lower()


# ─── A reminder with no task, and the words in front of "reminder" ─────────
#
# Live garage row, 2026-09-30 04:00Z (core 7f5d2cd): label and message
# "Better reminder for 10 minutes". Whisper heard "set a reminder for 10
# minutes" as "Better reminder for 10 minutes.", no fast path matched, and
# the tool model (qwen3:8b, reproduced locally) copied the transcript into
# the tool call's ``message``: the reply was "I'll remind you to Better
# reminder for 10 minutes in 10 minutes." and the fired line "Reminder:
# Better reminder for 10 minutes". These run the real router plan and the
# real handler method; only the INSERT is captured, so none of them can
# hide behind requires_db. The fired line is the watcher's own.

class _CapturingTimers:
    """Stands in for TimerRepository: records what create() was given."""

    rows: list[dict] = []

    def __init__(self, _session) -> None:
        pass

    async def create(self, **kwargs) -> int:
        type(self).rows.append(kwargs)
        return len(type(self).rows)


@pytest.fixture
def captured_timers(monkeypatch):
    from domovoi.handlers import reminder as reminder_mod

    _CapturingTimers.rows = []
    monkeypatch.setattr(reminder_mod, "TimerRepository", _CapturingTimers)
    return _CapturingTimers.rows


async def _say(transcript: str):
    from domovoi.router import plan_route

    plan = plan_route(transcript)
    assert plan is not None and plan.handler.name == "reminder", (
        f"{transcript!r} did not reach the reminder fast path: {plan}"
    )
    assert plan.fast_path.method is ReminderHandler._create_from_match
    ctx = Context(session_id=None, room_id="garage", online=True)
    return await plan.fast_path.method(plan.handler, plan.match, ctx, None)


def _fired(row: dict) -> str:
    """What the reminder's own room says when this row goes off, on time
    (the house-wide delivery's fire_line; other rooms add "from the ...")."""
    from domovoi.timer_delivery import fire_line

    duration = round((row["expires_at"] - row["created_at"]).total_seconds())
    return fire_line(
        "reminder", label=row["label"], message=row["message"],
        duration_sec=duration, origin_room_id=row.get("room_id"),
        target_room_id=row.get("room_id") or "garage", late_sec=0,
    )


_NO_TASK = ""
_NO_TASK_10 = ("", "10 minute reminder", 600, "I'll remind you in 10 minutes.",
               "Here's your 10 minute reminder.")


def _task(message: str, seconds: int, reply: str) -> tuple:
    """A reminder with a task: label = message, fires "Reminder: <task>"."""
    return (message, message, seconds, reply, f"Reminder: {message}")


# (Whisper transcript, stored message, stored label, seconds, spoken reply,
#  fired line)
_SPOKEN_CREATE_CASES: list[tuple] = [
    # ── no task: the command words are never the message ──
    ("Set a reminder for 10 minutes.", *_NO_TASK_10),
    # the live row: a mumbled "set a" heard as "better"
    ("Better reminder for 10 minutes.", *_NO_TASK_10),
    ("Said a reminder for five minutes.", _NO_TASK, "5 minute reminder", 300,
     "I'll remind you in 5 minutes.", "Here's your 5 minute reminder."),
    ("Set reminder for ten minutes.", *_NO_TASK_10),
    ("A reminder for 10 minutes.", *_NO_TASK_10),
    ("Reminder for 10 minutes.", *_NO_TASK_10),
    ("Reminder 10 minutes.", *_NO_TASK_10),
    ("Remind me in 10 minutes.", *_NO_TASK_10),
    ("Set a 10 minute reminder.", *_NO_TASK_10),
    ("Set a reminder for 10 minutes from now.", *_NO_TASK_10),
    ("Set a quick reminder for 10 minutes.", *_NO_TASK_10),
    ("Set the reminder for 10 minutes.", *_NO_TASK_10),
    ("Set my reminder for 10 minutes.", *_NO_TASK_10),
    ("Hey, set a reminder for 10 minutes.", *_NO_TASK_10),
    ("Can you set a reminder for 10 minutes?", *_NO_TASK_10),
    ("Please set me a reminder for 10 minutes.", *_NO_TASK_10),
    ("Could you please set up a reminder for 10 minutes?", *_NO_TASK_10),
    ("Give me a reminder in 10 minutes.", *_NO_TASK_10),
    ("Just a reminder for 10 minutes.", *_NO_TASK_10),
    ("Another reminder in 10 minutes.", *_NO_TASK_10),
    ("Ping me in ten minutes.", *_NO_TASK_10),
    ("Please set a reminder for 1 hour and 30 minutes.", _NO_TASK,
     "1 hour and 30 minute reminder", 5400,
     "I'll remind you in 1 hour and 30 minutes.",
     "Here's your 1 hour and 30 minute reminder."),
    # "for me" is whom it is for, not the task ("I'll remind you about me")
    ("Could you set a reminder for me in 10 minutes?", *_NO_TASK_10),
    ("Set a reminder for me in 10 minutes.", *_NO_TASK_10),
    # a dangling connector (cut off at a pause) or a pronoun is no task:
    # "I'll remind you to to in 10 minutes." / "Reminder: to"
    ("Set a reminder for 10 minutes to...", *_NO_TASK_10),
    ("Set a reminder for 10 minutes to.", *_NO_TASK_10),
    ("Remind me in 10 minutes to", *_NO_TASK_10),
    ("Set a reminder for 10 minutes to, um", *_NO_TASK_10),
    ("Remind me about it in 10 minutes.", *_NO_TASK_10),
    # politeness is never the task, wherever it is said
    ("Set a reminder for 10 minutes please.", *_NO_TASK_10),
    ("Set a reminder please for 10 minutes.", *_NO_TASK_10),
    ("Remind me in 10 minutes, please.", *_NO_TASK_10),
    # ── a task after the duration or a connector: only the task is kept ──
    ("Remind me to check the oven in 10 minutes.",
     *_task("check the oven", 600, "I'll remind you to check the oven in 10 minutes.")),
    ("Set a reminder for 10 minutes to call mom.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    ("Reminder in 5 minutes take the laundry out.",
     *_task("take the laundry out", 300,
            "I'll remind you to take the laundry out in 5 minutes.")),
    ("Remind me in 10 minutes to call mom.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    ("Set a reminder to call mom in 10 minutes.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    ("Better reminder for 10 minutes to call mom.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    ("Set a ten minute reminder to check the oven.",
     *_task("check the oven", 600, "I'll remind you to check the oven in 10 minutes.")),
    ("Remind me in an hour and a half to leave.",
     *_task("leave", 5400, "I'll remind you to leave in 1 hour and 30 minutes.")),
    ("Set a reminder for 10 minutes for the pasta.",
     *_task("the pasta", 600, "I'll remind you about the pasta in 10 minutes.")),
    ("Remind me in 10 minutes about the pasta.",
     *_task("the pasta", 600, "I'll remind you about the pasta in 10 minutes.")),
    ("Remind me about the laundry in 5 minutes.",
     *_task("the laundry", 300, "I'll remind you about the laundry in 5 minutes.")),
    ("Remind me in 10 minutes that the oven is on.",
     *_task("the oven is on", 600, "I'll remind you that the oven is on in 10 minutes.")),
    ("Set a reminder for 10 minutes that the pizza is ready.",
     *_task("the pizza is ready", 600,
            "I'll remind you that the pizza is ready in 10 minutes.")),
    ("Set a reminder for 10 minutes, to call mom.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    ("Set a reminder for me to call mom in 10 minutes.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    ("I want a reminder in 10 minutes to call mom.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    ("One more reminder in 10 minutes to call mom.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    ("New reminder for 10 minutes call mom.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    # politeness between the parts is dropped, the task kept
    ("Remind me in 10 minutes, please, to call mom.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    ("Remind me in 10 minutes please to call mom.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    ("Remind me, please, to call mom in 10 minutes.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    ("Remind me to please call mom in 10 minutes.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    ("Set a reminder for 10 minutes to call mom please.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    # a sentence break, an ellipsis or a filler before the task
    ("Remind me in ten minutes. Call mom.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    ("Remind me in 10 minutes to... call mom.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    ("Set a reminder for ten minutes, um, to call mom.",
     *_task("call mom", 600, "I'll remind you to call mom in 10 minutes.")),
    # ... but a "please" inside the task is the task's
    ("Remind me to say please to grandma in 10 minutes.",
     *_task("say please to grandma", 600,
            "I'll remind you to say please to grandma in 10 minutes.")),
    # ── a task named in FRONT of "reminder" is the task, not a verb ──
    ("Laundry reminder in 5 minutes.",
     *_task("laundry", 300, "Your laundry reminder is set for 5 minutes.")),
    ("Oven reminder for 10 minutes.",
     *_task("oven", 600, "Your oven reminder is set for 10 minutes.")),
    ("Set oven reminder for 10 minutes.",
     *_task("oven", 600, "Your oven reminder is set for 10 minutes.")),
    ("Medication reminder in 30 minutes.",
     *_task("medication", 1800, "Your medication reminder is set for 30 minutes.")),
    ("A laundry reminder in 5 minutes.",
     *_task("laundry", 300, "Your laundry reminder is set for 5 minutes.")),
    ("Set a laundry reminder for 5 minutes please.",
     *_task("laundry", 300, "Your laundry reminder is set for 5 minutes.")),
    ("Pizza reminder, 10 minutes.",
     *_task("pizza", 600, "Your pizza reminder is set for 10 minutes.")),
    ("Call mom reminder in 10 minutes.",
     *_task("call mom", 600, "Your call mom reminder is set for 10 minutes.")),
    ("Mom's birthday reminder in an hour.",
     *_task("mom's birthday", 3600, "Your mom's birthday reminder is set for 1 hour.")),
    ("Set a 10 minute laundry reminder.",
     *_task("laundry", 600, "Your laundry reminder is set for 10 minutes.")),
    ("Test reminder in 10 seconds.",
     *_task("test", 10, "Your test reminder is set for 10 seconds.")),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transcript", "message", "label", "seconds", "reply", "fired"),
    _SPOKEN_CREATE_CASES,
    ids=[c[0] for c in _SPOKEN_CREATE_CASES],
)
async def test_spoken_reminder_stores_only_the_task(
    captured_timers, transcript, message, label, seconds, reply, fired
) -> None:
    response = await _say(transcript)

    assert response.text == reply
    assert len(captured_timers) == 1
    row = captured_timers[0]
    assert row["message"] == message
    # message IS NOT NULL is what makes a row a reminder on every reader
    # (the watcher, the list, the dashboard's is_reminder).
    assert row["message"] is not None
    assert row["label"] == label
    assert row["room_id"] == "garage"
    # One instant for both ends, so the fired line's duration is exact.
    assert row["expires_at"] - row["created_at"] == timedelta(seconds=seconds)
    assert _fired(row) == fired


# Talking ABOUT a reminder, cancelling or changing one, or a shape the
# grammar leaves to the tool model: never a new reminder on a fast path.
_NOT_A_CREATE = [
    # complaints and statements (all were creates at 7fa80b9: "I'll remind
    # you to did not go off in 10 minutes.")
    "My reminder for 10 minutes didn't go off.",
    "My reminder for 10 minutes did not go off.",
    "That reminder for 10 minutes was useless.",
    "That reminder was useless.",
    "You have a reminder in 10 minutes.",
    "I have a reminder in 10 minutes.",
    "The reminder in 10 minutes is for the oven.",
    "Your reminder in 10 minutes.",
    "Why didn't my reminder for 10 minutes go off?",
    "Set a reminder for 10 minutes that was useless.",
    "Set a reminder for 10 minutes didn't go off.",
    "Remind me in 10 minutes because my last reminder didn't go off.",
    # asking about, cancelling or changing a reminder is not a new one
    "cancel the reminder for 10 minutes",
    "stop the reminder for 10 minutes",
    "delete the reminder for 10 minutes",
    "snooze the reminder for 10 minutes",
    "turn off the reminder in 10 minutes",
    "extend the reminder for 10 minutes",
    "bump the reminder for 10 minutes",
    "move the reminder for 10 minutes to 20 minutes",
    "what's my reminder for 10 minutes",
    "is there a reminder in 10 minutes",
    "did i set a reminder for 10 minutes",
    "i don't need a reminder for 10 minutes",
    # an existing reminder, or one again, named in front
    "Next reminder in 10 minutes.",
    "Upcoming reminder in 10 minutes.",
    "Repeat reminder in 10 minutes.",
    "Snooze reminder for 10 minutes.",
    # a past time is not a new reminder's task ("Reminder: ago")
    "Set a reminder 10 minutes ago.",
    # a task in front AND after is one task said in two halves: the tool
    # model joins it ("Move the laundry to the dryer")
    "Laundry reminder in 5 minutes to move it to the dryer.",
    # an article inside the named task is past what the grammar keeps
    "Water the plants reminder in 10 minutes.",
    # still the timer's
    "set a timer for 10 minutes",
    "timer for 5 minutes called eggs",
    # no duration: not a fast path at all
    "remind me to call mom",
    "set a reminder",
    "set reminders for 10 minutes",
    # a clock time is not a duration (absolute reminders are deferred)
    "remind me at 6 pm to feed the dog",
    "set a reminder for 5 pm",
    "set a reminder for tomorrow at 9",
]


@pytest.mark.parametrize("transcript", _NOT_A_CREATE)
def test_the_create_paths_do_not_poach(transcript: str) -> None:
    from domovoi.router import plan_route

    plan = plan_route(transcript)
    assert not (
        plan is not None
        and plan.handler.name == "reminder"
        and plan.fast_path.method is ReminderHandler._create_from_match
    ), f"{transcript!r} became a new reminder"


# The tool model is not offered the reminder tool for talk ABOUT a
# reminder: qwen3:8b (2026-09-30) made "You have a reminder in 10 minutes."
# a no-task reminder and "The reminder in 10 minutes is for the oven." a
# reminder "oven" when it was — nor the timer's, which it reached for next
# (a 10 minute "oven reminder" timer).
@pytest.mark.parametrize(
    "transcript",
    [
        "My reminder for 10 minutes didn't go off.",
        "my reminder didn't go off",
        "That reminder was useless.",
        "that reminder for 10 minutes was useless",
        "You have a reminder in 10 minutes.",
        "you've got a reminder at noon",
        "I have a reminder in 10 minutes.",
        "The reminder in 10 minutes is for the oven.",
        "the reminder never went off",
        "Why didn't my reminder go off?",
        "where did my reminders go",
        "I set a reminder for 10 minutes and it never went off.",
        "i set a reminder and it did not go off",
    ],
)
def test_statements_about_a_reminder_are_not_offered_the_tool(transcript: str) -> None:
    from domovoi.router import normalize_transcript, offered_tool_schemas, strip_leading_filler

    norm = strip_leading_filler(normalize_transcript(transcript))
    offered = [s["name"] for s in offered_tool_schemas(norm)]
    assert "reminder" not in offered
    assert "timer" not in offered


@pytest.mark.parametrize(
    "transcript",
    [
        # the commands that reach the tool model keep the tool
        "remind me to call mom at five",
        "remind me at 6 pm to feed the dog",
        "set a reminder for 5 pm",
        "laundry reminder in 5 minutes to move it to the dryer",
        "i need a reminder at noon",
        "i want a reminder tomorrow morning",
        "can i have a reminder at six",
        "i have to leave at five, set a reminder",
        "i set a reminder for ten minutes",  # also "hi, set a reminder ..."
        "what reminders do i have tomorrow",
        "cancel the laundry reminder",
        "change my reminder to 10 minutes",
        "snooze the reminder for 10 minutes",
        "the reminder should say call mom",
    ],
)
def test_reminder_commands_keep_the_tool(transcript: str) -> None:
    from domovoi.router import normalize_transcript, offered_tool_schemas, strip_leading_filler

    norm = strip_leading_filler(normalize_transcript(transcript))
    assert "reminder" in [s["name"] for s in offered_tool_schemas(norm)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transcript", "label_phrase"),
    [
        ("Cancel my reminder to call mom.", "call mom"),
        ("Cancel the reminder for the oven.", "the oven"),
        ("Cancel the reminder for 10 minutes.", "10 minutes"),
        ("Cancel my reminders.", None),
        ("Cancel the reminder.", None),
        # what the create paths name: a task in front of "reminder", and a
        # no-task reminder's "10 minute reminder" label
        ("Cancel the laundry reminder.", "laundry"),
        ("Cancel my medication reminder.", "medication"),
        ("Cancel the ten minute reminder.", "10 minute reminder"),
        ("Cancel the 5 minute reminder.", "5 minute reminder"),
    ],
)
async def test_cancel_names_the_reminder(monkeypatch, transcript, label_phrase) -> None:
    from domovoi.router import plan_route

    seen: list = []

    async def fake_cancel(self, phrase, ctx, session):
        seen.append(phrase)
        return None

    monkeypatch.setattr(ReminderHandler, "_cancel", fake_cancel)
    plan = plan_route(transcript)
    assert plan is not None and plan.fast_path.method is ReminderHandler._cancel_from_match
    ctx = Context(session_id=None, room_id="garage", online=True)
    await plan.fast_path.method(plan.handler, plan.match, ctx, None)
    assert seen == [label_phrase]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "label_phrase"),
    [
        ("Laundry reminder", "Laundry"),
        ("laundry", "laundry"),
        ("10 minute reminder", "10 minute reminder"),
        ("call mom.", "call mom"),
        ("", None),
        (None, None),
    ],
)
async def test_tool_cancel_names_the_reminder(monkeypatch, message, label_phrase) -> None:
    seen: list = []

    async def fake_cancel(self, phrase, ctx, session):
        seen.append(phrase)
        return None

    monkeypatch.setattr(ReminderHandler, "_cancel", fake_cancel)
    ctx = Context(session_id=None, room_id="garage", online=True)
    args = {"action": "cancel"}
    if message is not None:
        args["message"] = message
    await ReminderHandler().execute_from_tool(args, ctx, None)  # type: ignore[arg-type]
    assert seen == [label_phrase]


def test_cancel_by_duration_still_reaches_cancel() -> None:
    from domovoi.router import plan_route

    plan = plan_route("Cancel the reminder for 10 minutes.")
    assert plan is not None
    assert plan.fast_path.method is ReminderHandler._cancel_from_match
    assert plan.match.group("label") == "10 minutes"


def test_new_create_paths_never_commit_early() -> None:
    """Each is whole with no task, and a pause can come before "... to
    call mom": an early commit there would drop the task."""
    from domovoi.early_commit import early_commit_for

    for text_ in ("set a reminder for 10 minutes", "better reminder for 10 minutes",
                  "remind me in 10 minutes", "set a 10 minute reminder",
                  "laundry reminder in 5 minutes", "remind me about the laundry in 5 minutes",
                  "set a 10 minute laundry reminder"):
        assert early_commit_for(text_) is None, text_


# What the tool model passes as ``message`` (qwen3:8b, temperature 0, this
# checkout's prompt and schemas, 2026-09-30) and what gets stored.
_TOOL_CASES: list[tuple[object, int, str, str, str]] = [
    # (tool message, duration_sec, stored message, stored label, reply)
    # ── the command copied or paraphrased: no task ──
    ("Better reminder for 10 minutes", 600, _NO_TASK, "10 minute reminder",
     "I'll remind you in 10 minutes."),
    ("Better reminder", 600, _NO_TASK, "10 minute reminder",
     "I'll remind you in 10 minutes."),
    ("Reminder set for 10 minutes.", 600, _NO_TASK, "10 minute reminder",
     "I'll remind you in 10 minutes."),
    ("Reminder set for ten minutes.", 600, _NO_TASK, "10 minute reminder",
     "I'll remind you in 10 minutes."),
    ("Reminder for 10 minutes", 600, _NO_TASK, "10 minute reminder",
     "I'll remind you in 10 minutes."),
    ("Said a reminder for five minutes.", 300, _NO_TASK, "5 minute reminder",
     "I'll remind you in 5 minutes."),
    ("10 minute reminder", 600, _NO_TASK, "10 minute reminder",
     "I'll remind you in 10 minutes."),
    ("10 minutes", 600, _NO_TASK, "10 minute reminder", "I'll remind you in 10 minutes."),
    ("Set reminder", 600, _NO_TASK, "10 minute reminder", "I'll remind you in 10 minutes."),
    ("Reminder", 600, _NO_TASK, "10 minute reminder", "I'll remind you in 10 minutes."),
    ("Ping", 600, _NO_TASK, "10 minute reminder", "I'll remind you in 10 minutes."),
    # a dangling connector is no task ("I'll remind you to to in 10 minutes.")
    ("to...", 600, _NO_TASK, "10 minute reminder", "I'll remind you in 10 minutes."),
    # no message at all used to answer "What should I remind you about?"
    # and set nothing: nothing hears the answer to that question
    ("", 600, _NO_TASK, "10 minute reminder", "I'll remind you in 10 minutes."),
    (None, 600, _NO_TASK, "10 minute reminder", "I'll remind you in 10 minutes."),
    # ── a real task is kept as the model wrote it (terminal stop dropped) ──
    ("Call mom", 600, "Call mom", "Call mom", "I'll remind you to Call mom in 10 minutes."),
    ("Take the laundry out.", 300, "Take the laundry out", "Take the laundry out",
     "I'll remind you to Take the laundry out in 5 minutes."),
    ("Take your medication", 1800, "Take your medication", "Take your medication",
     "I'll remind you to Take your medication in 30 minutes."),
    ("run a ping", 600, "run a ping", "run a ping",
     "I'll remind you to run a ping in 10 minutes."),
    ("Check the reminder app", 600, "Check the reminder app", "Check the reminder app",
     "I'll remind you to Check the reminder app in 10 minutes."),
    # ... with the model's heading, politeness and connector dropped
    ("Reminder: call mom", 600, "call mom", "call mom",
     "I'll remind you to call mom in 10 minutes."),
    ("Please, to call mom", 600, "call mom", "call mom",
     "I'll remind you to call mom in 10 minutes."),
    ("please call mom", 600, "call mom", "call mom",
     "I'll remind you to call mom in 10 minutes."),
    ("Reminder to call mom", 600, "call mom", "call mom",
     "I'll remind you to call mom in 10 minutes."),
    ("remind me to take my pills", 600, "take my pills", "take my pills",
     "I'll remind you to take my pills in 10 minutes."),
    # ── a task named in front of "reminder" is the task, in the model's case ──
    ("Medication reminder", 1800, "Medication", "Medication",
     "Your Medication reminder is set for 30 minutes."),
    ("Water reminder", 600, "Water", "Water", "Your Water reminder is set for 10 minutes."),
    ("doctor reminder", 600, "doctor", "doctor", "Your doctor reminder is set for 10 minutes."),
    ("email reminder", 600, "email", "email", "Your email reminder is set for 10 minutes."),
    ("Call mom reminder", 600, "Call mom", "Call mom",
     "Your Call mom reminder is set for 10 minutes."),
    ("set the oven reminder", 600, "oven", "oven", "Your oven reminder is set for 10 minutes."),
    # ── the whole command passed as the message: only its task survives ──
    ("Set a reminder for 10 minutes to call mom", 600, "call mom", "call mom",
     "I'll remind you to call mom in 10 minutes."),
    ("Laundry reminder in 5 minutes", 300, "Laundry", "Laundry",
     "Your Laundry reminder is set for 5 minutes."),
    # ... subject and all (qwen3:8b for "I set a reminder for ten minutes.")
    ("I set a reminder for ten minutes", 600, _NO_TASK, "10 minute reminder",
     "I'll remind you in 10 minutes."),
    ("I'll set a reminder for 10 minutes to call mom.", 600, "call mom", "call mom",
     "I'll remind you to call mom in 10 minutes."),
    # ... but a subject in front of a real task stays (the model's words)
    ("I need to call mom", 600, "I need to call mom", "I need to call mom",
     "I'll remind you to I need to call mom in 10 minutes."),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "duration", "stored", "label", "reply"), _TOOL_CASES,
    ids=[repr(c[0]) for c in _TOOL_CASES],
)
async def test_tool_call_message_keeps_only_the_task(
    captured_timers, message, duration, stored, label, reply
) -> None:
    ctx = Context(session_id=None, room_id="garage", online=True)
    args = {"action": "create", "duration_sec": duration}
    if message is not None:
        args["message"] = message
    response = await ReminderHandler().execute_from_tool(args, ctx, None)  # type: ignore[arg-type]

    assert response.text == reply
    assert [(r["message"], r["label"]) for r in captured_timers] == [(stored, label)]


@pytest.mark.asyncio
async def test_tool_call_without_a_duration_still_asks_when(captured_timers) -> None:
    ctx = Context(session_id=None, room_id="garage", online=True)
    response = await ReminderHandler().execute_from_tool(
        {"action": "create", "message": "call mom"}, ctx, None  # type: ignore[arg-type]
    )
    assert response.text == "When should I remind you?"
    assert captured_timers == []


def test_a_no_task_reminder_reads_as_its_label_in_the_satellite_tab() -> None:
    """The satellite details page's timers tab (web satellites.jsx and
    android SatHistoryTabs.kt) shows ``label`` first, and every reader
    calls a row a reminder when ``message IS NOT NULL``, an empty message
    included."""
    from web.backend.api.satellites import _timer_from_row

    now = utcnow()
    row = (10, now + timedelta(minutes=10), "10 minute reminder", "", "garage", now)
    t = _timer_from_row(row)
    assert t.is_reminder is True
    assert t.label == "10 minute reminder"


# ─── DB tier ───────────────────────────────────────────────────────────────

@requires_db
@pytest.mark.asyncio
async def test_no_task_reminder_round_trip(db_session) -> None:
    """Set with no task, list it, cancel it by its duration."""
    from domovoi.router import plan_route

    handler = ReminderHandler()
    ctx = Context(session_id=None, room_id="garage", online=True)
    plan = plan_route("Better reminder for 10 minutes.")
    assert plan is not None
    response = await plan.fast_path.method(handler, plan.match, ctx, db_session)
    await db_session.commit()
    assert response.text == "I'll remind you in 10 minutes."

    row = (
        await db_session.execute(
            text(
                "SELECT label, message, expires_at - created_at FROM timers "
                "WHERE message IS NOT NULL"
            )
        )
    ).one()
    assert row[0] == "10 minute reminder"
    assert row[1] == ""
    assert row[2] == timedelta(minutes=10)

    listed = await handler._list_from_match(
        _LIST_RE.match("what are my reminders"), ctx, db_session
    )
    assert listed.text.startswith("In ")
    assert listed.text.endswith(": your 10 minute reminder.")

    m = _CANCEL_RE.match("cancel the reminder for 10 minutes")
    cancelled = await handler._cancel_from_match(m, ctx, db_session)
    await db_session.commit()
    assert cancelled.text == "Cancelled the reminder."
    left = (await db_session.execute(text("SELECT count(*) FROM timers"))).scalar_one()
    assert left == 0


@requires_db
@pytest.mark.asyncio
async def test_named_reminder_round_trip(db_session) -> None:
    """"laundry reminder in 5 minutes" keeps "laundry"; "cancel the laundry
    reminder" cancels that one and leaves the no-task one, which "cancel
    the ten minute reminder" then finds."""
    from domovoi.router import plan_route

    handler = ReminderHandler()
    ctx = Context(session_id=None, room_id="garage", online=True)
    for said in ("Laundry reminder in 5 minutes.", "Set a reminder for ten minutes."):
        plan = plan_route(said)
        await plan.fast_path.method(handler, plan.match, ctx, db_session)
    await db_session.commit()
    rows = (
        await db_session.execute(
            text("SELECT label, message FROM timers ORDER BY expires_at")
        )
    ).all()
    assert [tuple(r) for r in rows] == [("laundry", "laundry"), ("10 minute reminder", "")]

    plan = plan_route("Cancel the laundry reminder.")
    response = await plan.fast_path.method(handler, plan.match, ctx, db_session)
    await db_session.commit()
    assert response.text == "Cancelled the reminder."
    left = (await db_session.execute(text("SELECT label FROM timers"))).scalars().all()
    assert left == ["10 minute reminder"]

    plan = plan_route("Cancel the ten minute reminder.")
    response = await plan.fast_path.method(handler, plan.match, ctx, db_session)
    await db_session.commit()
    assert response.text == "Cancelled the reminder."
    assert (await db_session.execute(text("SELECT count(*) FROM timers"))).scalar_one() == 0


@requires_db
@pytest.mark.asyncio
async def test_list_reads_a_task_and_a_no_task_reminder(db_session) -> None:
    repo = TimerRepository(db_session)
    now = utcnow()
    await repo.create(
        expires_at=now + timedelta(minutes=5), created_at=now,
        label="5 minute reminder", message="", room_id="kitchen",
    )
    await repo.create(
        expires_at=now + timedelta(minutes=9), created_at=now,
        label="call mom", message="call mom", room_id="kitchen",
    )
    await db_session.commit()

    ctx = Context(session_id=None, room_id="kitchen", online=True)
    response = await ReminderHandler()._list_from_match(
        _LIST_RE.match("list my reminders"), ctx, db_session
    )
    assert "You have 2 reminders." in response.text
    assert ": your 5 minute reminder. After that: call mom." in response.text
