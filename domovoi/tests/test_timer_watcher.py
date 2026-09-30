"""TimerWatcher — what an expired timer says, and where.

Since 2026-09-30 the watcher hands every tick to the house-wide delivery
(domovoi/timer_delivery.py): a fire is announced in its own room AND in
every other connected room whose "Only reminders for this device" is off,
a busy or offline room is waited for instead of dropped, and every
outcome is recorded. The full rule set is pinned in test_timer_delivery.py;
these tests keep the watcher-level contract: the tick pops and counts, the
two "timer fired" log lines are unchanged, the fired line's wording, and
the old "dropping" paths now wait or record instead.

Unit tier swaps the DB pop for canned rows (and V018 for the in-memory
ledger) so the rules run without Postgres. One DB-tier test drives a real
timer from the handler through the ledger so the duration the fired line
speaks is the one the handler stored.
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

from domovoi import timer_delivery as td
from domovoi.db.repositories import utcnow
from domovoi.handlers.timer import TimerHandler, _CREATE_RE
from domovoi.models import Context
from domovoi.tests.conftest import requires_db
from domovoi.workers.timer_watcher import TimerWatcher, _timer_done_text

_LOGGER = "domovoi.workers.timer_watcher"
_DELIVERY_LOGGER = "domovoi.timer_delivery"


class _FakeSession:
    """A connected room with no busy states: takes an announcement at once."""

    def __init__(self, room_id: str, *, explode: Exception | None = None) -> None:
        self.room_id = room_id
        self.explode = explode
        self.announced: list[str] = []

    async def announce(self, text: str, **_kwargs) -> None:
        if self.explode is not None:
            exc, self.explode = self.explode, None
            raise exc
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
    """Make the next tick() pop exactly ``rows`` without touching the DB:
    V018 reads as missing (the in-memory ledger) and the pop is canned."""

    def _set(*rows: tuple) -> None:
        @asynccontextmanager
        async def _scope():
            yield None

        class _Repo:
            def __init__(self, _s) -> None:
                pass

            async def pop_expired(self):
                return list(rows)

        async def _no_v018() -> bool:
            return False

        monkeypatch.setattr(td, "_probe_v018", _no_v018)
        monkeypatch.setattr(td, "session_scope", _scope)
        monkeypatch.setattr(td, "TimerRepository", _Repo)

    return _set


async def _drain(watcher: TimerWatcher) -> None:
    for _ in range(50):
        tasks = list(watcher.delivery._tasks.values())
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)


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
async def test_plain_timer_announces_in_its_room_and_every_other(expire, caplog) -> None:
    # Owner decision 2026-09-30: every online satellite announces it; the
    # other rooms say where it came from.
    kitchen, office = _FakeSession("kitchen"), _FakeSession("office")
    watcher = TimerWatcher(app=_app({"kitchen": kitchen, "office": office}))
    expire(_row(1, duration_sec=600))

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        assert await watcher.tick() == 1
    await _drain(watcher)

    assert kitchen.announced == ["Your 10 minute timer is done."]
    assert office.announced == ["From the kitchen: Your 10 minute timer is done."]
    # The pre-existing journal line, exactly.
    assert "timer fired: id=1 room=kitchen" in caplog.messages
    assert watcher.delivery._tasks == {}


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
async def test_timer_for_an_offline_room_waits_for_it(expire, caplog, connected) -> None:
    # Used to log "timer fired for offline room=kitchen; dropping ...". Now
    # the kitchen's row waits (pending, detail offline) for the kitchen to
    # reconnect within the grace window, and a connected room hears it now.
    sessions = {room: _FakeSession(room) for room in connected}
    watcher = TimerWatcher(app=_app(sessions))
    expire(_row(1, label="pasta"))

    with caplog.at_level(logging.INFO):
        assert await watcher.tick() == 1
    await _drain(watcher)

    assert "timer fired: pasta room=kitchen" in caplog.messages
    assert not any("dropping" in m for m in caplog.messages)
    for room, sess in sessions.items():
        assert sess.announced == ["From the kitchen: Your pasta timer is done."], room
    ledger = watcher.delivery._ledger
    rows = await ledger.rows(-1)
    assert rows[0] == ("kitchen", True, "pending", "offline")

    # The kitchen comes back inside the window and hears it.
    kitchen = _FakeSession("kitchen")
    sessions["kitchen"] = kitchen
    watcher.delivery.set_accepting()
    watcher.delivery.on_room_connected("kitchen")
    for _ in range(20):
        await asyncio.sleep(0)
    await _drain(watcher)
    assert kitchen.announced == ["Your pasta timer is done."]


@pytest.mark.asyncio
async def test_failed_announce_is_recorded_not_retried(expire, caplog) -> None:
    kitchen = _FakeSession("kitchen", explode=ConnectionError("socket closed"))
    watcher = TimerWatcher(app=_app({"kitchen": kitchen}))
    expire(_row(1))

    with caplog.at_level(logging.INFO, logger=_DELIVERY_LOGGER):
        await watcher.tick()
        await _drain(watcher)

    assert kitchen.announced == []
    rows = await watcher.delivery._ledger.rows(-1)
    assert rows == [("kitchen", True, "failed", "send_failed")]
    [line] = [r for r in caplog.records if "outcome=failed" in r.getMessage()]
    assert line.levelno == logging.WARNING
    assert line.getMessage().startswith(
        "timer fire -1 room=kitchen origin=kitchen outcome=failed detail=send_failed waited="
    )


@pytest.mark.asyncio
async def test_mid_response_announce_is_retried_not_dropped(expire) -> None:
    # "room kitchen mid-response, announce skipped" used to drop the timer.
    kitchen = _FakeSession(
        "kitchen",
        explode=td.AnnounceNotStarted(
            "room kitchen mid-response, announce skipped", reason="responding",
        ),
    )
    watcher = TimerWatcher(app=_app({"kitchen": kitchen}))
    watcher.delivery.poll_sec = 0.01
    expire(_row(1))

    await watcher.tick()
    await _drain(watcher)

    assert kitchen.announced == ["Your 10 minute timer is done."]


@pytest.mark.parametrize(
    ("message", "duration_sec", "expected"),
    [
        ("call mom", 600, "Reminder: call mom"),
        # set with no task (an empty message): named by its duration, the
        # way an unlabelled timer is
        ("", 600, "Here's your 10 minute reminder."),
        ("", 300, "Here's your 5 minute reminder."),
        ("", 5400, "Here's your 1 hour and 30 minute reminder."),
        ("", 45, "Here's your 45 second reminder."),
        ("", 0, "Here's your reminder."),
    ],
)
def test_reminder_text(message, duration_sec, expected) -> None:
    # The reminder's own room (or one set with no room), on time: the
    # house-wide wording in timer_delivery.fire_line keeps these lines.
    for origin, target in (("garage", "garage"), (None, "kitchen")):
        assert td.fire_line(
            "reminder", label=None, message=message, duration_sec=duration_sec,
            origin_room_id=origin, target_room_id=target, late_sec=0,
        ) == expected


@pytest.mark.asyncio
async def test_reminder_with_no_task_is_still_a_reminder(expire) -> None:
    """An empty message is a reminder, not a plain timer: it used to fall
    into the timer branch (``if message:``) and say "Your 10 minute
    reminder timer is done."."""
    garage = _FakeSession("garage")
    watcher = TimerWatcher(app=_app({"garage": garage}))
    expire(_row(10, label="10 minute reminder", message="", room_id="garage"))

    await watcher.tick()
    await _drain(watcher)

    assert garage.announced == ["Here's your 10 minute reminder."]


@pytest.mark.asyncio
async def test_no_app_pops_and_records_and_a_roomless_fire_reaches_every_room(expire) -> None:
    expire(_row(1), _row(2, room_id=None))
    assert await TimerWatcher(app=None).tick() == 2

    # Set from the app or the dashboard chat (no room): every flag-OFF room
    # says it, with no "From the ..." (it came from nowhere in the house).
    kitchen = _FakeSession("kitchen")
    watcher = TimerWatcher(app=_app({"kitchen": kitchen}))
    expire(_row(3, room_id=None))
    assert await watcher.tick() == 1
    await _drain(watcher)
    assert kitchen.announced == ["Your 10 minute timer is done."]


@pytest.mark.asyncio
async def test_reminder_speaks_its_message(expire, caplog) -> None:
    kitchen = _FakeSession("kitchen")
    watcher = TimerWatcher(app=_app({"kitchen": kitchen}))
    expire(_row(1, label="call mom", message="call mom"))

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await watcher.tick()
    await _drain(watcher)

    assert kitchen.announced == ["Reminder: call mom"]
    assert "timer fired (reminder): call mom room=kitchen message='call mom'" in caplog.messages


@pytest.mark.asyncio
async def test_reminder_for_an_offline_room_is_heard_elsewhere(expire, caplog) -> None:
    office = _FakeSession("office")
    watcher = TimerWatcher(app=_app({"office": office}))
    expire(_row(1, label="call mom", message="call mom"))

    with caplog.at_level(logging.INFO):
        await watcher.tick()
    await _drain(watcher)

    assert office.announced == ["Reminder from the kitchen: call mom"]
    assert not any("dropping" in m for m in caplog.messages)
    # The new per-room outcome line never carries the reminder's words.
    delivery_lines = [
        r.getMessage() for r in caplog.records if r.name == _DELIVERY_LOGGER
    ]
    assert delivery_lines and not any("call mom" in m for m in delivery_lines)


# ─── Handler → DB → watcher ─────────────────────────────────────────────────

@requires_db
@pytest.mark.asyncio
async def test_handler_timer_fires_with_its_spoken_duration(db_session) -> None:
    """The duration comes back out of the row exactly: the handler stamps
    created_at and expires_at from one instant, not NOW() at transaction
    start (which a slow tool-routed turn would skew)."""
    from domovoi.tests.timer_fires_testkit import apply_v018

    # The fire ledger is not in conftest's truncation set: start and end
    # empty, so no later core boot in this run resumes this fire.
    await apply_v018()
    try:
        await _fire_a_handler_timer(db_session)
    finally:
        await db_session.rollback()
        await apply_v018()


async def _fire_a_handler_timer(db_session) -> None:
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


@requires_db
@pytest.mark.asyncio
async def test_misheard_no_task_reminder_fires_as_a_reminder(db_session) -> None:
    """The live 2026-09-30 garage utterance, end to end: it used to store
    its own words and fire "Reminder: Better reminder for 10 minutes"."""
    from domovoi.tests.timer_fires_testkit import apply_v018

    # Like the timer test above: the fire ledger starts and ends empty.
    await apply_v018()
    try:
        await _fire_a_misheard_reminder(db_session)
    finally:
        await db_session.rollback()
        await apply_v018()


async def _fire_a_misheard_reminder(db_session) -> None:
    from domovoi.router import plan_route

    plan = plan_route("Better reminder for 10 minutes.")
    assert plan is not None and plan.handler.name == "reminder"
    ctx = Context(session_id=uuid4(), room_id="garage", online=True)
    await plan.fast_path.method(plan.handler, plan.match, ctx, db_session)
    await db_session.execute(
        text(
            "UPDATE timers SET created_at = created_at - interval '10 minutes', "
            "expires_at = expires_at - interval '10 minutes'"
        )
    )
    await db_session.commit()

    garage = _FakeSession("garage")
    watcher = TimerWatcher(app=_app({"garage": garage}))
    assert await watcher.tick() == 1
    await _drain(watcher)

    assert garage.announced == ["Here's your 10 minute reminder."]
