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
from domovoi.models import Context
from domovoi.tests.conftest import requires_db


# ─── Regex tests ────────────────────────────────────────────────────────────

def test_create_regex() -> None:
    m = _CREATE_RE.match("remind me to call mom in 10 minutes")
    assert m
    assert m.group("message") == "call mom"
    assert m.group("amount") == "10"
    assert m.group("unit") == "minute"


def test_create_regex_singular_unit() -> None:
    m = _CREATE_RE.match("remind me to take the trash out in 1 hour")
    assert m and m.group("amount") == "1" and m.group("unit") == "hour"


def test_create_regex_seconds() -> None:
    m = _CREATE_RE.match("remind me to check the oven in 90 seconds")
    assert m and m.group("amount") == "90" and m.group("unit") == "second"


def test_create_regex_does_not_match_set_a_timer() -> None:
    """Plain timer commands should still go to TimerHandler, not here."""
    assert not _CREATE_RE.match("set a timer for 10 minutes")
    assert not _CREATE_RE.match("timer for 5 minutes")


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
