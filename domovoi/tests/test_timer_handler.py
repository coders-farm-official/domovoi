from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from domovoi.db.repositories import TimerRepository, utcnow
from domovoi.handlers.timer import TimerHandler, _CANCEL_RE, _CREATE_RE, _STATUS_RE
from domovoi.models import Context, Intent
from domovoi.tests.conftest import requires_db


# ─── Pure regex tests (no DB) ───────────────────────────────────────────────

def test_create_regex_basic() -> None:
    m = _CREATE_RE.match("timer for 5 minutes")
    assert m and m.group(1) == "5" and m.group(2) == "minute" and m.group(3) is None


def test_create_regex_with_set_a() -> None:
    m = _CREATE_RE.match("set a timer for 30 seconds")
    assert m and m.group(1) == "30" and m.group(2) == "second"


def test_create_regex_with_label() -> None:
    m = _CREATE_RE.match("timer for 10 minutes for pasta")
    assert m and m.group(3) == "pasta"


def test_create_regex_singular_unit() -> None:
    m = _CREATE_RE.match("timer for 1 hour")
    assert m and m.group(1) == "1" and m.group(2) == "hour"


def test_cancel_regex_no_label() -> None:
    m = _CANCEL_RE.match("cancel the timer")
    assert m and m.group(1) is None


def test_cancel_regex_with_label() -> None:
    m = _CANCEL_RE.match("cancel the timer called pasta")
    assert m and m.group(1) == "pasta"


def test_cancel_regex_stop_variant() -> None:
    m = _CANCEL_RE.match("stop the timer")
    assert m is not None


def test_status_regex() -> None:
    assert _STATUS_RE.match("how much time left on the timer")
    assert _STATUS_RE.match("how long on the timer")
    assert _STATUS_RE.match("how long left on timer")


def test_unrelated_strings_dont_match() -> None:
    assert _CREATE_RE.match("play some music") is None
    assert _CANCEL_RE.match("what's the weather") is None


# ─── DB-backed behavior tests ───────────────────────────────────────────────

@requires_db
@pytest.mark.asyncio
async def test_create_inserts_row_and_formats_response(db_session) -> None:
    handler = TimerHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
    m = _CREATE_RE.match("timer for 5 minutes")
    assert m
    response = await handler._create_from_match(m, ctx, db_session)
    await db_session.commit()

    assert "5 minutes" in response.text
    nxt = await TimerRepository(db_session).next_active(room_id="kitchen")
    assert nxt is not None
    _id, expires_at, label = nxt
    assert label is None
    assert (expires_at - utcnow()) > timedelta(seconds=290)


@requires_db
@pytest.mark.asyncio
async def test_create_with_label(db_session) -> None:
    handler = TimerHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
    m = _CREATE_RE.match("timer for 3 minutes for pasta")
    assert m
    response = await handler._create_from_match(m, ctx, db_session)
    await db_session.commit()
    assert "pasta" in response.text

    nxt = await TimerRepository(db_session).next_active(room_id="kitchen")
    assert nxt is not None
    assert nxt[2] == "pasta"


@requires_db
@pytest.mark.asyncio
async def test_cancel_removes_row(db_session) -> None:
    handler = TimerHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
    m = _CREATE_RE.match("timer for 10 minutes")
    assert m
    await handler._create_from_match(m, ctx, db_session)
    await db_session.commit()

    m2 = _CANCEL_RE.match("cancel the timer")
    assert m2
    response = await handler._cancel_from_match(m2, ctx, db_session)
    await db_session.commit()
    assert "ancel" in response.text.lower() or "cancelled" in response.text.lower()

    nxt = await TimerRepository(db_session).next_active(room_id="kitchen")
    assert nxt is None


@requires_db
@pytest.mark.asyncio
async def test_status_reports_remaining(db_session) -> None:
    handler = TimerHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
    m = _CREATE_RE.match("timer for 10 minutes")
    assert m
    await handler._create_from_match(m, ctx, db_session)
    await db_session.commit()

    m2 = _STATUS_RE.match("how much time left on the timer")
    assert m2
    response = await handler._status_from_match(m2, ctx, db_session)
    assert "minute" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_status_with_no_timer(db_session) -> None:
    handler = TimerHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
    m2 = _STATUS_RE.match("how much time left on the timer")
    assert m2
    response = await handler._status_from_match(m2, ctx, db_session)
    assert "no timer" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_pop_expired_returns_and_deletes(db_session) -> None:
    repo = TimerRepository(db_session)
    # Insert an already-expired timer.
    await repo.create(
        expires_at=utcnow() - timedelta(seconds=1),
        label="expired",
        message=None,
        room_id="kitchen",
    )
    await db_session.commit()

    fired = await repo.pop_expired()
    await db_session.commit()
    assert len(fired) == 1
    assert fired[0][1] == "expired"

    fired_again = await repo.pop_expired()
    assert fired_again == []
