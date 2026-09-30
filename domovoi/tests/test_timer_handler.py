from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from domovoi.db.repositories import TimerRepository, utcnow
from domovoi.handlers import timer as timer_mod
from domovoi.handlers.shared.number_words import parse_duration_seconds
from domovoi.handlers.timer import (
    TimerHandler,
    _CANCEL_RE,
    _CREATE_RE,
    _STATUS_RE,
    spoken_label,
)
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


@pytest.mark.parametrize(
    ("label", "spoken"),
    [
        ("pasta", "pasta"),
        ("the pasta", "pasta"),
        ("The Pasta", "Pasta"),
        ("my laundry", "laundry"),
        ("an egg", "egg"),
        ("  the pasta  ", "pasta"),
        ("theory exam", "theory exam"),     # a word, not an article
    ],
)
def test_spoken_label_drops_the_users_article(label: str, spoken: str) -> None:
    assert spoken_label(label) == spoken


class _OneTimerRepo:
    """TimerRepository stand-in holding one labelled timer, so the reply
    wording is checked without a database."""

    label = "the pasta"

    def __init__(self, session) -> None:
        pass

    async def cancel_by_label(self, label, room_id, *, house_wide=False) -> int:
        return 1

    async def label_rooms(self, label):
        return []

    async def next_active(self, room_id):
        return 1, utcnow() + timedelta(minutes=10, seconds=30), self.label

    async def next_for_status(self, room_id):
        return 1, utcnow() + timedelta(minutes=10, seconds=30), self.label, None


@pytest.mark.asyncio
async def test_labelled_cancel_and_status_read_the_label_once(monkeypatch) -> None:
    """"cancel the timer for the pasta" stores/asks with the label "the
    pasta"; the replies used to put their own "the" in front of it:
    "Cancelled the the pasta timer." / "... left on the the pasta timer."."""
    monkeypatch.setattr(timer_mod, "TimerRepository", _OneTimerRepo)
    handler = TimerHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)

    m = _CANCEL_RE.match("cancel the timer for the pasta")
    assert m and m.group(1) == "the pasta"
    response = await handler._cancel_from_match(m, ctx, None)  # type: ignore[arg-type]
    assert response.text == "Cancelled the pasta timer."

    m = _STATUS_RE.match("how much time left on the timer")
    assert m
    response = await handler._status_from_match(m, ctx, None)  # type: ignore[arg-type]
    assert response.text == "10 minutes left on the pasta timer."


@pytest.mark.asyncio
async def test_unarticled_label_wording_is_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(_OneTimerRepo, "label", "pasta")
    monkeypatch.setattr(timer_mod, "TimerRepository", _OneTimerRepo)
    handler = TimerHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
    response = await handler._cancel(label="pasta", ctx=ctx, session=None)  # type: ignore[arg-type]
    assert response.text == "Cancelled the pasta timer."
    response = await handler._status(ctx=ctx, session=None)  # type: ignore[arg-type]
    assert response.text == "10 minutes left on the pasta timer."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "message", "expected"),
    [
        # a plain timer: unchanged
        (None, None, "10 minutes left on the timer."),
        ("the pasta", None, "10 minutes left on the pasta timer."),
        # a reminder is read as one: set with no task, it used to be "10
        # minutes left on the 10 minute reminder timer."
        ("10 minute reminder", "", "10 minutes left on your 10 minute reminder."),
        ("call mom", "call mom", "10 minutes left on your reminder: call mom."),
        ("laundry", "laundry", "10 minutes left on your reminder: laundry."),
    ],
)
async def test_status_reads_a_reminder_as_a_reminder(
    monkeypatch, label, message, expected
) -> None:
    class _Repo(_OneTimerRepo):
        async def next_for_status(self, room_id):
            return 1, utcnow() + timedelta(minutes=10, seconds=30), label, message

    monkeypatch.setattr(timer_mod, "TimerRepository", _Repo)
    ctx = Context(session_id=uuid4(), room_id="garage", online=True)
    response = await TimerHandler()._status(ctx=ctx, session=None)  # type: ignore[arg-type]
    assert response.text == expected


@pytest.mark.asyncio
async def test_status_of_a_reminder_about_to_fire(monkeypatch) -> None:
    class _Repo(_OneTimerRepo):
        async def next_for_status(self, room_id):
            return 1, utcnow() - timedelta(seconds=1), "10 minute reminder", ""

    monkeypatch.setattr(timer_mod, "TimerRepository", _Repo)
    ctx = Context(session_id=uuid4(), room_id="garage", online=True)
    response = await TimerHandler()._status(ctx=ctx, session=None)  # type: ignore[arg-type]
    assert response.text == "That reminder is about to go off."


def _offered(transcript: str) -> list[str]:
    from domovoi.router import normalize_transcript, offered_tool_schemas, strip_leading_filler

    norm = strip_leading_filler(normalize_transcript(transcript))
    return [s["name"] for s in offered_tool_schemas(norm)]


@pytest.mark.parametrize(
    "transcript",
    [
        # talk about a timer is no timer command (the reminder shapes are
        # in test_reminder_handler)
        "My timer didn't go off.",
        "The garage timer never went off.",
        "That timer was useless.",
        "My timer for 10 minutes did not go off.",
        "You have a timer running in the kitchen.",
        "You've set a timer for the pasta already.",
        "Why didn't the timer go off?",
    ],
)
def test_talk_about_a_timer_is_not_offered_the_timer_tool(transcript: str) -> None:
    assert "timer" not in _offered(transcript)


@pytest.mark.parametrize(
    "transcript",
    [
        "The pasta timer, how long is left?",
        "How much time is left on the garage timer?",
        "Start a timer for the eggs.",
        "I need a timer for ten minutes.",
        "Cancel the garage timer.",
        "Stop the timer for the pasta.",
        "Put a timer on for the pizza.",
        "Is the timer still running?",
        "You set a timer for ten minutes.",
        "I set a timer for ten minutes.",
    ],
)
def test_timer_commands_keep_the_timer_tool(transcript: str) -> None:
    assert "timer" in _offered(transcript)


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
async def test_articled_label_round_trip_reads_naturally(db_session) -> None:
    handler = TimerHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
    m = _CREATE_RE.match("timer for 10 minutes for the pasta")
    assert m
    await handler._create_from_match(m, ctx, db_session)
    await db_session.commit()

    m2 = _STATUS_RE.match("how much time left on the timer")
    assert m2
    response = await handler._status_from_match(m2, ctx, db_session)
    assert response.text.endswith(" left on the pasta timer."), response.text

    m3 = _CANCEL_RE.match("cancel the timer for the pasta")
    assert m3
    response = await handler._cancel_from_match(m3, ctx, db_session)
    await db_session.commit()
    assert response.text == "Cancelled the pasta timer."
    assert await TimerRepository(db_session).next_active(room_id="kitchen") is None


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
async def test_status_prefers_the_timer_and_reads_a_reminder_as_one(db_session) -> None:
    """"How long left on the timer" answers the room's timer even when a
    reminder is due sooner, and reads a reminder as a reminder when that
    is all the room has."""
    repo = TimerRepository(db_session)
    now = utcnow()
    await repo.create(
        expires_at=now + timedelta(minutes=5), created_at=now,
        label="10 minute reminder", message="", room_id="garage",
    )
    await db_session.commit()
    handler = TimerHandler()
    ctx = Context(session_id=uuid4(), room_id="garage", online=True)
    m = _STATUS_RE.match("how much time left on the timer")

    response = await handler._status_from_match(m, ctx, db_session)
    assert response.text.endswith(" left on your 10 minute reminder."), response.text

    await repo.create(
        expires_at=now + timedelta(minutes=20), created_at=now,
        label="pasta", message=None, room_id="garage",
    )
    await db_session.commit()
    response = await handler._status_from_match(m, ctx, db_session)
    assert response.text.endswith(" left on the pasta timer."), response.text


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


# ─── Cancel scope and "stop the timer" after a fire (2026-09-30) ──────────
#
# A plain cancel never deletes a reminder; a labelled cancel stays in the
# room it was said in unless the person says "everywhere" (or the turn has
# no room); and "stop the timer" right after one was announced in this room
# acknowledges it instead of deleting this room's own timers.


@pytest.mark.parametrize(
    ("transcript", "label", "everywhere"),
    [
        ("cancel the timer", None, False),
        ("stop the timer", None, False),
        ("cancel timer", None, False),
        ("cancel the timer for pasta", "pasta", False),
        ("cancel the timer called the pasta", "the pasta", False),
        ("cancel the timer everywhere", None, True),
        ("stop the timer in every room", None, True),
        ("cancel the timer in all rooms", None, True),
        ("cancel the timer in the whole house", None, True),
        ("cancel the timer for pasta everywhere", "pasta", True),
        ("cancel the timer named rice and beans in all rooms", "rice and beans", True),
    ],
)
def test_cancel_regex_everywhere_variants(transcript, label, everywhere) -> None:
    m = _CANCEL_RE.match(transcript)
    assert m is not None, transcript
    assert m.group("label") == label
    assert (m.group("everywhere") is not None) is everywhere
    assert m.group(1) == label          # older callers read group 1
    assert fast_path_winner(transcript) == "timer"


def test_tool_schema_offers_everywhere() -> None:
    props = TimerHandler.tool_schema["parameters"]["properties"]
    assert props["everywhere"]["type"] == "boolean"
    assert "explicitly" in props["everywhere"]["description"]


def _ctx(room: str | None) -> Context:
    return Context(session_id=uuid4(), room_id=room, online=True)


async def _timer(s, *, room: str | None, label: str | None = None,
                 message: str | None = None, minutes: int = 10) -> int:
    return await TimerRepository(s).create(
        expires_at=utcnow() + timedelta(minutes=minutes), label=label,
        message=message, room_id=room,
    )


async def _rows(s) -> list[tuple]:
    from sqlalchemy import text

    res = await s.execute(text(
        "SELECT room_id, label, message IS NOT NULL FROM timers ORDER BY id"))
    return [tuple(r) for r in res.all()]


@pytest.fixture
async def v017_session(db_session):
    from domovoi.tests.timer_fires_testkit import apply_v017

    await apply_v017()
    yield db_session
    # End the test's transaction first: the TRUNCATE waits for its locks.
    await db_session.rollback()
    await apply_v017()


@requires_db
@pytest.mark.asyncio
async def test_stop_the_timer_never_deletes_a_reminder(v017_session) -> None:
    s = v017_session
    await _timer(s, room="garage", label="call mom", message="call mom")
    await _timer(s, room="garage")
    await s.commit()

    m = _CANCEL_RE.match("stop the timer")
    reply = await TimerHandler()._cancel_from_match(m, _ctx("garage"), s)
    await s.commit()
    assert reply.text == "Cancelled the timer."
    assert await _rows(s) == [("garage", "call mom", True)]


@requires_db
@pytest.mark.asyncio
async def test_a_labelled_cancel_stays_in_its_room_and_says_where_it_is(v017_session) -> None:
    s = v017_session
    await _timer(s, room="garage", label="pasta")
    await s.commit()

    m = _CANCEL_RE.match("cancel the timer for pasta")
    reply = await TimerHandler()._cancel_from_match(m, _ctx("kitchen"), s)
    await s.commit()
    assert reply.text == (
        'The pasta timer is in the garage. Say "cancel the timer for pasta '
        'everywhere" to cancel it from here.'
    )
    assert await _rows(s) == [("garage", "pasta", False)]


@requires_db
@pytest.mark.asyncio
async def test_the_hint_names_every_room_it_is_in(v017_session) -> None:
    s = v017_session
    await _timer(s, room="garage", label="the pasta")
    await _timer(s, room="living-room", label="the pasta")
    await s.commit()
    m = _CANCEL_RE.match("cancel the timer for the pasta")
    reply = await TimerHandler()._cancel_from_match(m, _ctx("kitchen"), s)
    assert reply.text == (
        'The pasta timer is in the garage and the living room. Say "cancel the '
        'timer for the pasta everywhere" to cancel it from here.'
    )


@requires_db
@pytest.mark.asyncio
async def test_everywhere_cancels_plain_timers_in_every_room_but_no_reminder(v017_session) -> None:
    s = v017_session
    await _timer(s, room="garage", label="pasta")
    await _timer(s, room="office", label="pasta")
    await _timer(s, room="kitchen", label="pasta", message="pasta")
    await s.commit()

    m = _CANCEL_RE.match("cancel the timer for pasta everywhere")
    reply = await TimerHandler()._cancel_from_match(m, _ctx("kitchen"), s)
    await s.commit()
    assert reply.text == "Cancelled the pasta timer."
    assert await _rows(s) == [("kitchen", "pasta", True)]


@requires_db
@pytest.mark.asyncio
async def test_a_roomless_labelled_cancel_is_house_wide(v017_session) -> None:
    s = v017_session
    await _timer(s, room="garage", label="pasta")
    await _timer(s, room=None, label="pasta")
    await s.commit()
    m = _CANCEL_RE.match("cancel the timer for pasta")
    reply = await TimerHandler()._cancel_from_match(m, _ctx(None), s)
    await s.commit()
    assert reply.text == "Cancelled the pasta timer."
    assert await _rows(s) == []


@requires_db
@pytest.mark.asyncio
async def test_the_tool_cancel_is_room_scoped_unless_everywhere(v017_session) -> None:
    s = v017_session
    handler = TimerHandler()
    await _timer(s, room="garage", label="pasta")
    await _timer(s, room="kitchen", label="pasta")
    await s.commit()

    reply = await handler.execute_from_tool(
        {"action": "cancel", "label": "pasta"}, _ctx("kitchen"), s)
    await s.commit()
    assert reply.text == "Cancelled the pasta timer."
    assert await _rows(s) == [("garage", "pasta", False)]

    reply = await handler.execute_from_tool(
        {"action": "cancel", "label": "pasta"}, _ctx("kitchen"), s)
    assert "is in the garage" in reply.text

    reply = await handler.execute_from_tool(
        {"action": "cancel", "label": "pasta", "everywhere": True}, _ctx("kitchen"), s)
    await s.commit()
    assert reply.text == "Cancelled the pasta timer."
    assert await _rows(s) == []


@requires_db
@pytest.mark.asyncio
async def test_status_never_calls_a_reminder_a_timer(v017_session) -> None:
    # next_active is timers only. "How long left on the timer" reads the
    # room's plain timer first (wf/reminder-parse's next_for_status) and a
    # lone reminder as a reminder, never as "the call mom timer".
    s = v017_session
    await _timer(s, room="kitchen", label="call mom", message="call mom", minutes=2)
    await s.commit()
    assert await TimerRepository(s).next_active(room_id="kitchen") is None
    m = _STATUS_RE.match("how much time left on the timer")
    reply = await TimerHandler()._status_from_match(m, _ctx("kitchen"), s)
    assert reply.text.endswith(" left on your reminder: call mom."), reply.text
    assert "timer" not in reply.text

    await _timer(s, room="kitchen", label="pasta", minutes=20)
    await s.commit()
    reply = await TimerHandler()._status_from_match(m, _ctx("kitchen"), s)
    assert reply.text.endswith(" left on the pasta timer."), reply.text


async def _spoken_fire(s, *, spoken_ago_sec: float) -> int:
    """A garage timer that went off and was announced in the kitchen
    ``spoken_ago_sec`` seconds ago, still waiting for the office."""
    from sqlalchemy import text

    fid = (await s.execute(text(
        "INSERT INTO timer_fires (timer_id, kind, origin_room_id, created_at, due_at, "
        "base_text) VALUES (99, 'timer', 'garage', now() - interval '11 minutes', "
        "now() - interval '1 minute', 'Your 10 minute timer is done.') RETURNING id"
    ))).scalar_one()
    for room, origin, outcome, ago in (
        ("garage", True, "spoken", 40.0),
        ("kitchen", False, "spoken", spoken_ago_sec),
        ("office", False, "pending", None),
    ):
        started = f"now() - make_interval(secs => {ago + 3.0})" if ago is not None else "NULL"
        finished = f"now() - make_interval(secs => {ago})" if ago is not None else "NULL"
        await s.execute(
            text(
                "INSERT INTO timer_fire_deliveries "
                "(fire_id, room_id, is_origin, outcome, started_at, finished_at) "
                f"VALUES (:f, :r, :o, :out, {started}, {finished})"
            ),
            {"f": fid, "r": room, "o": origin, "out": outcome},
        )
    await s.commit()
    return int(fid)


@requires_db
@pytest.mark.asyncio
async def test_stop_the_timer_right_after_a_fire_acknowledges_it(v017_session) -> None:
    from sqlalchemy import text

    s = v017_session
    own = await _timer(s, room="kitchen", label="pasta")
    await s.commit()
    fid = await _spoken_fire(s, spoken_ago_sec=10)

    m = _CANCEL_RE.match("stop the timer")
    reply = await TimerHandler()._cancel_from_match(m, _ctx("kitchen"), s)
    await s.commit()
    assert reply.text == "Okay."
    # The kitchen's own running timer is untouched.
    assert [r[0] for r in (await s.execute(text("SELECT id FROM timers"))).all()] == [own]
    acked = (await s.execute(text(
        "SELECT acked_at IS NOT NULL, acked_by FROM timer_fires WHERE id = :f"),
        {"f": fid})).one()
    assert tuple(acked) == (True, "kitchen")
    office = (await s.execute(text(
        "SELECT outcome, detail FROM timer_fire_deliveries "
        "WHERE fire_id = :f AND room_id = 'office'"), {"f": fid})).one()
    assert tuple(office) == ("cancelled", "acknowledged:kitchen")

    # Said again: the fire is already acknowledged, so it is a normal cancel.
    reply = await TimerHandler()._cancel_from_match(m, _ctx("kitchen"), s)
    await s.commit()
    assert reply.text == "Cancelled the timer."


@requires_db
@pytest.mark.asyncio
async def test_stop_the_timer_31_seconds_later_is_a_normal_cancel(v017_session) -> None:
    from sqlalchemy import text

    s = v017_session
    await _timer(s, room="kitchen", label="pasta")
    await s.commit()
    fid = await _spoken_fire(s, spoken_ago_sec=31)

    m = _CANCEL_RE.match("stop the timer")
    reply = await TimerHandler()._cancel_from_match(m, _ctx("kitchen"), s)
    await s.commit()
    assert reply.text == "Cancelled the timer."
    assert (await s.execute(text("SELECT count(*) FROM timers"))).scalar_one() == 0
    assert (await s.execute(text(
        "SELECT acked_by FROM timer_fires WHERE id = :f"), {"f": fid})).scalar_one() is None


@requires_db
@pytest.mark.asyncio
async def test_a_fire_heard_only_in_other_rooms_is_not_acknowledged_here(v017_session) -> None:
    s = v017_session
    await _timer(s, room="office", label="eggs")
    await s.commit()
    await _spoken_fire(s, spoken_ago_sec=5)     # spoken in garage and kitchen
    m = _CANCEL_RE.match("stop the timer")
    reply = await TimerHandler()._cancel_from_match(m, _ctx("office"), s)
    assert reply.text == "Cancelled the timer."


@pytest.mark.asyncio
async def test_the_acknowledgement_is_asked_only_for_a_plain_room_cancel(monkeypatch) -> None:
    """ack_recent_fire answers None when V017 is missing (or nothing is
    recent) and the cancel goes ahead as before. A labelled or house-wide
    cancel, or a turn with no room, never asks."""
    from domovoi import timer_delivery

    calls: list[str] = []

    async def _no_ack(session, room_id, within_sec=30):
        calls.append(room_id)
        return None

    monkeypatch.setattr(timer_delivery, "ack_recent_fire", _no_ack)
    monkeypatch.setattr(timer_mod, "TimerRepository", _OneTimerRepo)
    m = _CANCEL_RE.match("stop the timer")
    reply = await TimerHandler()._cancel_from_match(m, _ctx("kitchen"), None)  # type: ignore[arg-type]
    assert calls == ["kitchen"]
    assert reply.text == "Cancelled the timer."
    for transcript, room in (("cancel the timer for pasta", "kitchen"),
                             ("cancel the timer everywhere", "kitchen"),
                             ("stop the timer", None)):
        m = _CANCEL_RE.match(transcript)
        await TimerHandler()._cancel_from_match(m, _ctx(room), None)  # type: ignore[arg-type]
    assert calls == ["kitchen"]
