"""TimerWatcher — what an expired timer says, and where.

Unit tier swaps the DB pop for canned rows so the dispatch rules (live
room, offline room, failed announce, reminder vs plain timer) run without
Postgres. One DB-tier test drives a real timer from the handler through
``pop_expired`` so the duration the fired line speaks is the one the
handler stored. Delivery mechanics (mid-response skip, music restore)
belong to StreamSession.announce and are covered by the streaming tests.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from domovoi.db.repositories import utcnow
from domovoi.handlers.timer import TimerHandler, _CREATE_RE
from domovoi.models import Context
from domovoi.tests.conftest import requires_db
from domovoi.workers import timer_watcher as tw_mod
from domovoi.workers.timer_watcher import TimerWatcher, _timer_done_text

_LOGGER = "domovoi.workers.timer_watcher"


class _FakeSession:
    def __init__(self, room_id: str, *, explode: Exception | None = None) -> None:
        self.room_id = room_id
        self.explode = explode
        self.announced: list[str] = []

    async def announce(self, text: str) -> None:
        if self.explode is not None:
            raise self.explode
        self.announced.append(text)


def _app(sessions: dict[str, _FakeSession]) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(active_sessions=sessions))


def _row(
    tid: int,
    *,
    label: str | None = None,
    message: str | None = None,
    room_id: str | None = "kitchen",
    duration_sec: int = 600,
) -> tuple:
    expires_at = utcnow()
    return (
        tid, label, message, room_id,
        expires_at - timedelta(seconds=duration_sec), expires_at,
    )


@pytest.fixture
def expire(monkeypatch):
    """Make the next tick() pop exactly ``rows`` without touching the DB."""

    def _set(*rows: tuple) -> None:
        @asynccontextmanager
        async def _scope():
            yield None

        class _Repo:
            def __init__(self, _s) -> None:
                pass

            async def pop_expired(self):
                return list(rows)

        monkeypatch.setattr(tw_mod, "session_scope", _scope)
        monkeypatch.setattr(tw_mod, "TimerRepository", _Repo)

    return _set


async def _drain(watcher: TimerWatcher) -> None:
    await asyncio.gather(*list(watcher._inflight), return_exceptions=True)


# ─── The fired line ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("label", "duration_sec", "expected"),
    [
        (None, 600, "Your 10 minute timer is done."),
        (None, 60, "Your 1 minute timer is done."),
        (None, 30, "Your 30 second timer is done."),
        (None, 3600, "Your 1 hour timer is done."),
        (None, 5400, "Your 1 hour and 30 minute timer is done."),
        (None, 0, "Your timer is done."),
        ("pasta", 600, "Your pasta timer is done."),
        ("the pasta", 600, "Your pasta timer is done."),
    ],
)
def test_timer_done_text(label, duration_sec, expected) -> None:
    assert _timer_done_text(label, duration_sec) == expected


# ─── Dispatch (DB-free) ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_plain_timer_announces_in_its_room(expire) -> None:
    kitchen, office = _FakeSession("kitchen"), _FakeSession("office")
    watcher = TimerWatcher(app=_app({"kitchen": kitchen, "office": office}))
    expire(_row(1, duration_sec=600))

    assert await watcher.tick() == 1
    await _drain(watcher)

    assert kitchen.announced == ["Your 10 minute timer is done."]
    assert office.announced == []
    assert watcher._inflight == set()


@pytest.mark.asyncio
async def test_labelled_timer_uses_the_label(expire) -> None:
    kitchen = _FakeSession("kitchen")
    watcher = TimerWatcher(app=_app({"kitchen": kitchen}))
    expire(_row(1, label="pasta", duration_sec=600))

    await watcher.tick()
    await _drain(watcher)

    assert kitchen.announced == ["Your pasta timer is done."]


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [[], ["office"]])
async def test_plain_timer_for_offline_room_logs_and_drops(
    expire, caplog, connected,
) -> None:
    # [] is the one-satellite house whose only Pi is down — it must log
    # the drop too, not return silently.
    sessions = {room: _FakeSession(room) for room in connected}
    watcher = TimerWatcher(app=_app(sessions))
    expire(_row(1, label="pasta"))

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        assert await watcher.tick() == 1

    assert watcher._inflight == set()
    assert all(s.announced == [] for s in sessions.values())
    assert (
        "timer fired for offline room=kitchen; dropping 'Your pasta timer is done.'"
        in caplog.messages
    )


@pytest.mark.asyncio
async def test_failed_announce_logs_and_drops(expire, caplog) -> None:
    kitchen = _FakeSession(
        "kitchen", explode=RuntimeError("room kitchen mid-response, announce skipped"),
    )
    watcher = TimerWatcher(app=_app({"kitchen": kitchen}))
    expire(_row(1))

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await watcher.tick()
        await _drain(watcher)

    assert watcher._inflight == set()
    assert any(
        m.startswith("timer-broadcast-kitchen failed; dropping:") for m in caplog.messages
    )


@pytest.mark.asyncio
async def test_no_app_or_no_room_is_log_only(expire) -> None:
    expire(_row(1), _row(2, room_id=None))
    assert await TimerWatcher(app=None).tick() == 2

    kitchen = _FakeSession("kitchen")
    watcher = TimerWatcher(app=_app({"kitchen": kitchen}))
    expire(_row(3, room_id=None))
    assert await watcher.tick() == 1
    assert watcher._inflight == set()
    assert kitchen.announced == []


@pytest.mark.asyncio
async def test_reminder_speaks_its_message(expire) -> None:
    kitchen = _FakeSession("kitchen")
    watcher = TimerWatcher(app=_app({"kitchen": kitchen}))
    expire(_row(1, label="call mom", message="call mom"))

    await watcher.tick()
    await _drain(watcher)

    assert kitchen.announced == ["Reminder: call mom"]


@pytest.mark.asyncio
async def test_reminder_for_offline_room_logs_and_drops(expire, caplog) -> None:
    watcher = TimerWatcher(app=_app({"office": _FakeSession("office")}))
    expire(_row(1, label="call mom", message="call mom"))

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await watcher.tick()

    assert (
        "reminder fired for offline room=kitchen; dropping message='call mom'"
        in caplog.messages
    )


# ─── Handler → DB → watcher ─────────────────────────────────────────────────

@requires_db
@pytest.mark.asyncio
async def test_handler_timer_fires_with_its_spoken_duration(db_session) -> None:
    """The duration comes back out of the row exactly: the handler stamps
    created_at and expires_at from one instant, not NOW() at transaction
    start (which a slow tool-routed turn would skew)."""
    m = _CREATE_RE.match("timer for 10 minutes")
    assert m
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
    await TimerHandler()._create_from_match(m, ctx, db_session)
    # Age the row so it's expired, keeping the stored duration intact.
    await db_session.execute(
        text(
            "UPDATE timers SET created_at = created_at - interval '10 minutes', "
            "expires_at = expires_at - interval '10 minutes'"
        )
    )
    await db_session.commit()

    kitchen = _FakeSession("kitchen")
    watcher = TimerWatcher(app=_app({"kitchen": kitchen}))
    assert await watcher.tick() == 1
    await _drain(watcher)

    assert kitchen.announced == ["Your 10 minute timer is done."]
