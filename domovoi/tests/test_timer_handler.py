from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from domovoi.db.repositories import TimerRepository, utcnow
from domovoi.handlers.shared.number_words import parse_duration_seconds
from domovoi.handlers.timer import TimerHandler, _CANCEL_RE, _CREATE_RE, _STATUS_RE
from domovoi.models import Context, Intent
from domovoi.tests.conftest import fast_path_winner, requires_db


# ─── Pure regex tests (no DB) ───────────────────────────────────────────────

def test_create_regex_basic() -> None:
    m = _CREATE_RE.match("timer for 5 minutes")
    assert m and m.group("duration") == "5 minutes" and m.group("label") is None


def test_create_regex_with_set_a() -> None:
    m = _CREATE_RE.match("set a timer for 30 seconds")
    assert m and m.group("duration") == "30 seconds"


def test_create_regex_with_label() -> None:
    m = _CREATE_RE.match("timer for 10 minutes for pasta")
    assert m and m.group("duration") == "10 minutes" and m.group("label") == "pasta"


def test_create_regex_singular_unit() -> None:
    m = _CREATE_RE.match("timer for 1 hour")
    assert m and m.group("duration") == "1 hour"


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


# ─── Spelled-out and mixed durations (no DB) ────────────────────────────────
#
# Whisper spells small numbers out more often than not; before the shared
# duration grammar every one of these fell through to the ~4 s LLM tool
# router. (transcript, seconds, label) — the digit forms stay in the table
# so a grammar change can't regress them.

_CREATE_CASES: list[tuple[str, int, str | None]] = [
    # digits — the original grammar
    ("set a timer for 1 minute", 60, None),
    ("timer for 90 seconds", 90, None),
    ("set a timer for 2 hours for the roast", 7200, "the roast"),
    # spelled-out numbers
    ("set a timer for one minute", 60, None),
    ("set a timer for ten minutes", 600, None),
    ("timer for twenty minutes", 1200, None),
    ("timer for thirty seconds", 30, None),
    ("timer for forty five minutes", 2700, None),
    ("timer for forty-five minutes", 2700, None),
    ("timer for sixty seconds", 60, None),
    ("timer for ninety minutes", 5400, None),
    ("set a timer for seventeen minutes", 1020, None),
    # articles
    ("set a timer for an hour", 3600, None),
    ("set a timer for a minute", 60, None),
    # halves
    ("set a timer for half an hour", 1800, None),
    ("set a timer for a half hour", 1800, None),
    ("set a timer for half a minute", 30, None),
    ("set a timer for an hour and a half", 5400, None),
    ("set a timer for a minute and a half", 90, None),
    ("set a timer for one and a half hours", 5400, None),
    ("set a timer for two and a half minutes", 150, None),
    # mixed digits / words / articles, two clauses
    ("set a timer for 1 hour and 30 minutes", 5400, None),
    ("set a timer for an hour and thirty minutes", 5400, None),
    ("set a timer for two minutes and 30 seconds", 150, None),
    ("timer for 5 minutes 30 seconds", 330, None),
    ("timer for one minute and one second", 61, None),
    # labels still parse after every duration shape
    ("timer for ten minutes for pasta", 600, "pasta"),
    ("set a timer for an hour and a half called laundry", 5400, "laundry"),
    ("set a timer for half an hour named eggs", 1800, "eggs"),
    ("timer for 5 minutes for rice and beans", 300, "rice and beans"),
]


@pytest.mark.parametrize(("transcript", "seconds", "label"), _CREATE_CASES)
def test_create_regex_accepts_spoken_durations(
    transcript: str, seconds: int, label: str | None
) -> None:
    m = _CREATE_RE.match(transcript)
    assert m, f"{transcript!r} did not match the create fast path"
    assert parse_duration_seconds(m.group("duration")) == seconds
    assert m.group("label") == label


@pytest.mark.parametrize(
    "transcript",
    [
        "set a timer",
        "set a timer for",
        "set a timer for pasta",
        "set a timer for a while",
        "set a timer for minutes",
        "set a timer for 5",
        "set a timer for eleventy minutes",
        "set a timer for and a half hours",
        "timer for one two minutes",
    ],
)
def test_create_regex_requires_a_duration(transcript: str) -> None:
    assert _CREATE_RE.match(transcript) is None


@pytest.mark.parametrize(
    "transcript",
    [
        "set a timer for 1 minute",
        "set a timer for one minute",
        "set a timer for ten minutes",
        "Please set a timer for forty-five minutes.",
        "set a timer for an hour and a half for the laundry",
    ],
)
def test_spoken_durations_reach_the_timer_fast_path(transcript: str) -> None:
    # Through the real band-sorted registry, not just this handler's regex.
    assert fast_path_winner(transcript) == "timer"


def test_bare_set_a_timer_does_not_fast_path() -> None:
    # No duration → no fast path anywhere in the registry; the utterance
    # falls through to LLM tool routing, which can ask for the duration.
    assert fast_path_winner("set a timer") is None
    assert fast_path_winner("set a timer for") is None


@pytest.mark.asyncio
async def test_zero_duration_is_refused_before_touching_the_db() -> None:
    handler = TimerHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
    m = _CREATE_RE.match("timer for 0 minutes")
    assert m
    # The guard returns before any repository call, so no session is needed.
    response = await handler._create_from_match(m, ctx, None)  # type: ignore[arg-type]
    assert "duration" in response.text.lower()
    assert response.matched_handler == "timer"


# ─── DB-backed behavior tests ───────────────────────────────────────────────

@requires_db
@pytest.mark.asyncio
async def test_create_from_spoken_number_inserts_row(db_session) -> None:
    handler = TimerHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
    m = _CREATE_RE.match("set a timer for ten minutes for pasta")
    assert m
    response = await handler._create_from_match(m, ctx, db_session)
    await db_session.commit()

    assert "10 minutes" in response.text and "pasta" in response.text
    nxt = await TimerRepository(db_session).next_active(room_id="kitchen")
    assert nxt is not None
    _id, expires_at, label = nxt
    assert label == "pasta"
    assert timedelta(seconds=590) < (expires_at - utcnow()) <= timedelta(seconds=600)


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
