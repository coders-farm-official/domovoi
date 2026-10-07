"""House-wide delivery of timers and reminders (domovoi/timer_delivery.py).

Owner decision 2026-09-30: when a timer OR a reminder fires, every online
satellite announces it; a satellite with "Only reminders for this device"
turned on announces only its own; the room it was set in always does.

DB-FREE (most of this file): the coordinator runs on a fake clock against
fake rooms (``announce_block`` / ``announce``) and the in-memory ledger, so
every rule — who announces, the wording, busy rooms, retries, grace
windows, never-twice, settlement, restart resume, V018 missing — is pinned
without Postgres. Two tests use real StreamSessions (stub TTS, fake socket)
for the busy-room and boot-race paths end to end.

DB tier (``requires_db``): the V018 ledger itself — the atomic pop, the
NOTIFYs, the exclusive claim, ON CONFLICT late joiners, the cascade prune,
and a boot race through the real ledger and real sessions. Each of those
applies V018's SQL itself and truncates the three tables (they are NOT in
conftest's TABLES_TO_TRUNCATE, which lanes without V018 must survive).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import wave
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import text

from domovoi import lifecycle
from domovoi import timer_delivery as td
from domovoi.config import settings
from domovoi.events import EventBus
from domovoi.tests.conftest import requires_db
from domovoi.tests.timer_fires_testkit import apply_v018
from domovoi.timer_delivery import (
    AnnounceInterrupted,
    AnnounceNotStarted,
    FireLedger,
    MemoryFireLedger,
    TimerDelivery,
    fire_line,
    is_target,
    lateness_phrase,
    spoken_room,
)

T0 = 10_000.0
BASE = datetime(2026, 9, 30, 15, 10, 0, tzinfo=timezone.utc)


# ─── C1 the wording ──────────────────────────────────────────────────────


def _line(kind="timer", *, label=None, message=None, duration_sec=600,
          origin="garage", target="garage", late=0.0) -> str:
    return fire_line(kind, label=label, message=message, duration_sec=duration_sec,
                     origin_room_id=origin, target_room_id=target, late_sec=late)


def test_the_four_pinned_examples() -> None:
    assert _line(target="kitchen") == "From the garage: Your 10 minute timer is done."
    assert (_line("reminder", message="call mom", target="kitchen")
            == "Reminder from the garage: call mom")
    assert (_line("reminder", message="call mom", origin="living-room",
                  target="kitchen", late=200)
            == "Reminder from the living room, 3 minutes ago: call mom")
    assert _line(label="pasta", late=150) == "Your pasta timer went off 2 minutes ago."


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # timer, origin
        ({}, "Your 10 minute timer is done."),
        ({"late": 120}, "Your 10 minute timer went off 2 minutes ago."),
        ({"label": "the pasta"}, "Your pasta timer is done."),
        # timer, other room
        ({"target": "kitchen"}, "From the garage: Your 10 minute timer is done."),
        ({"target": "kitchen", "late": 4000},
         "From the garage: Your 10 minute timer went off 1 hour ago."),
        # timer, roomless fire: no prefix anywhere
        ({"origin": None, "target": "kitchen"}, "Your 10 minute timer is done."),
        ({"origin": None, "target": "kitchen", "late": 60 * 5},
         "Your 10 minute timer went off 5 minutes ago."),
        # reminder, origin
        ({"kind": "reminder", "message": "call mom"}, "Reminder: call mom"),
        ({"kind": "reminder", "message": "call mom", "late": 90},
         "Reminder from 1 minute ago: call mom"),
        # reminder, other room
        ({"kind": "reminder", "message": "call mom", "target": "kitchen"},
         "Reminder from the garage: call mom"),
        ({"kind": "reminder", "message": "call mom", "target": "kitchen", "late": 7300},
         "Reminder from the garage, 2 hours ago: call mom"),
        # reminder, roomless
        ({"kind": "reminder", "message": "call mom", "origin": None, "target": "office"},
         "Reminder: call mom"),
        ({"kind": "reminder", "message": "call mom", "origin": None, "target": "office",
          "late": 600}, "Reminder from 10 minutes ago: call mom"),
        # reminder with no words
        ({"kind": "reminder", "message": ""}, "Here's your 10 minute reminder."),
        ({"kind": "reminder", "message": "", "late": 180},
         "Here's your 10 minute reminder, from 3 minutes ago."),
        ({"kind": "reminder", "message": "", "origin": None, "target": "office"},
         "Here's your 10 minute reminder."),
        ({"kind": "reminder", "message": "", "target": "kitchen"},
         "From the garage: Here's your 10 minute reminder."),
        ({"kind": "reminder", "message": "", "target": "kitchen", "late": 180},
         "From the garage: Here's your 10 minute reminder, from 3 minutes ago."),
    ],
)
def test_every_row_of_the_wording_table(kwargs, expected) -> None:
    kind = kwargs.pop("kind", "timer")
    assert _line(kind, **kwargs) == expected


def test_spoken_room() -> None:
    assert spoken_room("living-room") == "living room"
    assert spoken_room("guest_bath") == "guest bath"
    assert spoken_room("garage") == "garage"


@pytest.mark.parametrize(
    ("late", "phrase"),
    [
        (0, None), (89, None), (89.99, None),
        (90, "1 minute ago"), (119, "1 minute ago"), (120, "2 minutes ago"),
        (3599, "59 minutes ago"), (3600, "1 hour ago"), (7199, "1 hour ago"),
        (7200, "2 hours ago"),
    ],
)
def test_lateness_phrase_boundaries(late, phrase) -> None:
    assert lateness_phrase(late) == phrase


# ─── C2 who announces ────────────────────────────────────────────────────


def test_is_target() -> None:
    assert is_target("kitchen", "garage", set())                 # flag OFF
    assert not is_target("kitchen", "garage", {"kitchen"})       # flag ON
    assert is_target("garage", "garage", {"garage"})             # the origin always
    assert is_target("kitchen", None, set())                     # roomless → flag-OFF rooms
    assert not is_target("kitchen", None, {"kitchen"})


# ─── Harness ─────────────────────────────────────────────────────────────


class FakeClock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> float:
        return self.t

    def wall(self) -> datetime:
        return BASE + timedelta(seconds=self.t - T0)


class FakeRoom:
    """A connected satellite. ``busy(reason, hard, until)`` scripts its
    announce_block; ``fail_next(*excs)`` scripts announce() failures."""

    def __init__(self, room_id: str, clock: FakeClock) -> None:
        self.room_id = room_id
        self.clock = clock
        self.blocks: list[tuple[float, str, bool]] = []
        self.failures: list[Exception] = []
        self.heard: list[tuple[float, str]] = []
        self.calls = 0
        self.kwargs: list[dict] = []

    def busy(self, reason: str, hard: bool, until: float) -> "FakeRoom":
        self.blocks.append((until, reason, hard))
        return self

    def fail_next(self, *excs: Exception) -> "FakeRoom":
        self.failures.extend(excs)
        return self

    def announce_block(self, now: float):
        for until, reason, hard in self.blocks:
            if now < until:
                return (reason, hard)
        return None

    async def announce(self, text: str, **kwargs) -> None:
        self.calls += 1
        self.kwargs.append(kwargs)
        block = self.announce_block(self.clock())
        assert block is None or not block[1], f"{self.room_id} spoke while {block}"
        if self.failures:
            raise self.failures.pop(0)
        self.heard.append((self.clock(), text))

    @property
    def texts(self) -> list[str]:
        return [t for _, t in self.heard]


class Ledger(MemoryFireLedger):
    """The in-memory ledger with a canned pop and a scripted flag table.

    ``wall`` is the test's clock, the one the seeded fires are stamped on
    (BASE, 2026-09-30); pruning measures a fire's age on it too."""

    def __init__(self, own_only=(), wall=lambda: BASE) -> None:
        super().__init__()
        self.due: list[tuple] = []
        self.own = set(own_only)
        self.wall = wall

    async def _pop_expired(self):
        rows, self.due = self.due, []
        return rows

    async def own_only_rooms(self) -> set[str]:
        return set(self.own)

    async def prune(self, days: int) -> int:
        # On the real clock every seeded fire was older than the retention
        # window as soon as BASE was (2026-10-07), and the first tick's
        # prune deleted the fires the restart tests were about to resume.
        with patch.object(td, "utcnow", self.wall):
            return await super().prune(days)

    def row(self, fire_id: int, room: str) -> dict:
        return self._rows[fire_id][room]


def due(tid: int, room: str | None = "garage", *, label=None, message=None,
        duration=600, late=0.0) -> tuple:
    expires = BASE - timedelta(seconds=late)
    return (tid, label, message, room, expires - timedelta(seconds=duration), expires)


async def _yield(n: int = 20) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


class House:
    def __init__(self, *rooms: str, own_only=(), accepting=True) -> None:
        self.clock = FakeClock()
        self.ledger = Ledger(own_only, wall=self.clock.wall)
        self.sessions: dict[str, FakeRoom] = {r: FakeRoom(r, self.clock) for r in rooms}
        self.app = SimpleNamespace(state=SimpleNamespace(active_sessions=self.sessions))
        self.bus = EventBus()
        self.fired: list[dict] = []
        self.settled: list[dict] = []
        self.sleeps: list[float] = []

        async def _on_fired(evt):
            self.fired.append(evt.payload)

        async def _on_settled(evt):
            self.settled.append(evt.payload)

        self.bus.subscribe("core.timer_fired", _on_fired)
        self.bus.subscribe("core.timer_fire_settled", _on_settled)

        async def _sleep(dt: float) -> None:
            self.sleeps.append(dt)
            await asyncio.sleep(0)

        self.d = TimerDelivery(
            self.app, lambda: self.ledger, clock=self.clock, wall=self.clock.wall,
            sleep=_sleep, events=self.bus,
        )
        if accepting:
            self.d.set_accepting()

    def room(self, room_id: str) -> FakeRoom:
        return self.sessions[room_id]

    def connect(self, room_id: str) -> FakeRoom:
        self.sessions[room_id] = FakeRoom(room_id, self.clock)
        self.d.on_room_connected(room_id)
        return self.sessions[room_id]

    async def fire(self, *rows: tuple) -> int:
        self.ledger.due.extend(rows)
        n = await self.d.tick()
        await _yield()
        return n

    async def advance(self, seconds: float, step: float = 0.25) -> None:
        end = self.clock.t + seconds
        while self.clock.t < end - 1e-9:
            self.clock.t = min(end, self.clock.t + step)
            await _yield(8)

    async def sweep(self) -> None:
        await self.d.sweep()
        await _yield()

    async def idle(self) -> None:
        """Let every task finish (for rooms that are not busy)."""
        for _ in range(100):
            await _yield()
            if not self.d._tasks and not self.d._aux:
                return
        raise AssertionError(f"tasks still running: {list(self.d._tasks)}")

    def outcome(self, fire_id: int, room: str) -> tuple[str, str | None]:
        r = self.ledger.row(fire_id, room)
        return r["outcome"], r["detail"]


# ─── C3 fan-out ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fan_out_timer_every_flag_off_room_and_the_origin() -> None:
    h = House("garage", "kitchen", "office", own_only={"office"})
    assert await h.fire(due(1, "garage")) == 1
    await h.idle()

    assert h.room("garage").texts == ["Your 10 minute timer is done."]
    assert h.room("kitchen").texts == ["From the garage: Your 10 minute timer is done."]
    assert h.room("office").texts == []
    assert h.outcome(-1, "garage") == ("spoken", None)
    assert h.outcome(-1, "kitchen") == ("spoken", None)
    assert "office" not in h.ledger._rows[-1]
    assert h.ledger.row(-1, "garage")["spoken_text"] == "Your 10 minute timer is done."


@pytest.mark.asyncio
async def test_fan_out_reminder_same_rule() -> None:
    h = House("garage", "kitchen", "office", own_only={"office"})
    await h.fire(due(1, "garage", label="call mom", message="call mom"))
    await h.idle()

    assert h.room("garage").texts == ["Reminder: call mom"]
    assert h.room("kitchen").texts == ["Reminder from the garage: call mom"]
    assert h.room("office").texts == []


@pytest.mark.asyncio
async def test_the_origin_announces_even_with_its_flag_on() -> None:
    h = House("garage", "kitchen", own_only={"garage", "kitchen"})
    await h.fire(due(1, "garage"), due(2, "kitchen", label="pasta"))
    await h.idle()
    assert h.room("garage").texts == ["Your 10 minute timer is done."]
    assert h.room("kitchen").texts == ["Your pasta timer is done."]


@pytest.mark.asyncio
async def test_a_roomless_fire_goes_to_every_flag_off_room() -> None:
    h = House("garage", "kitchen", "office", own_only={"office"})
    await h.fire(due(1, None, label="call mom", message="call mom"))
    await h.idle()
    assert h.room("garage").texts == ["Reminder: call mom"]
    assert h.room("kitchen").texts == ["Reminder: call mom"]
    assert h.room("office").texts == []
    assert all(not r["is_origin"] for r in h.ledger._rows[-1].values())


# ─── C4 / C5 busy rooms ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_responding_room_is_waited_for_then_spoken_once() -> None:
    h = House("garage")
    h.room("garage").busy("responding", True, T0 + 3.0)
    await h.fire(due(1))
    await h.advance(2.9)
    assert h.room("garage").heard == []
    assert h.outcome(-1, "garage") == ("pending", "responding")

    await h.advance(1.0)
    await h.idle()
    [(at, line)] = h.room("garage").heard
    assert at >= T0 + 3.0
    assert line == "Your 10 minute timer is done."
    assert h.outcome(-1, "garage") == ("spoken", None)


@pytest.mark.asyncio
async def test_a_soft_followup_hold_is_forced_after_the_busy_wait() -> None:
    h = House("garage")
    h.room("garage").busy("followup", False, T0 + 1000)
    await h.fire(due(1))
    await h.advance(44.0)
    assert h.room("garage").heard == []
    assert h.outcome(-1, "garage") == ("pending", "followup")

    await h.advance(2.0)
    await h.idle()
    [(at, _line_)] = h.room("garage").heard
    assert T0 + 45.0 <= at <= T0 + 46.0
    assert h.outcome(-1, "garage") == ("spoken", "forced_over:followup")


@pytest.mark.asyncio
async def test_capturing_is_never_forced_and_the_cap_is_busy_timeout() -> None:
    h = House("garage")
    h.room("garage").busy("capturing", True, T0 + 10_000)
    await h.fire(due(1))
    await h.advance(299.0)
    assert h.room("garage").calls == 0
    assert h.outcome(-1, "garage") == ("pending", "capturing")

    await h.advance(2.0)
    await h.idle()
    assert h.room("garage").calls == 0
    assert h.outcome(-1, "garage") == ("busy_timeout", "capturing")


@pytest.mark.asyncio
async def test_the_busy_waits_are_hot_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "timer_announce_busy_wait_sec", 5.0)
    monkeypatch.setattr(settings, "timer_announce_max_wait_sec", 30.0)
    h = House("garage", "kitchen")
    h.room("garage").busy("settling", False, T0 + 1000)
    h.room("kitchen").busy("in_call", True, T0 + 1000)
    await h.fire(due(1))
    await h.advance(6.0)
    assert h.outcome(-1, "garage") == ("spoken", "forced_over:settling")
    await h.advance(25.0)
    await h.idle()
    assert h.outcome(-1, "kitchen") == ("busy_timeout", "in_call")


# ─── C6 retries ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_turn_starting_under_the_announcement_is_retried_uncounted() -> None:
    h = House("garage")
    h.room("garage").fail_next(
        AnnounceNotStarted("room garage mid-response, announce skipped", reason="responding"),
    )
    await h.fire(due(1))
    await h.idle()
    assert h.room("garage").texts == ["Your 10 minute timer is done."]
    assert h.room("garage").calls == 2
    assert h.outcome(-1, "garage") == ("spoken", None)
    assert h.ledger.row(-1, "garage")["attempts"] == 1


@pytest.mark.asyncio
async def test_tts_failing_three_times_is_failed() -> None:
    h = House("garage")
    h.room("garage").fail_next(*[
        AnnounceNotStarted("tts down", reason="tts_failed") for _ in range(5)
    ])
    await h.fire(due(1))
    await h.idle()
    assert h.room("garage").calls == 3
    assert h.outcome(-1, "garage") == ("failed", "tts_failed")
    assert h.sleeps.count(td.TTS_RETRY_SEC) == 2


@pytest.mark.asyncio
async def test_tts_failing_once_then_speaking_counts_both_attempts() -> None:
    h = House("garage")
    h.room("garage").fail_next(AnnounceNotStarted("tts down", reason="tts_failed"))
    await h.fire(due(1))
    await h.idle()
    assert h.room("garage").texts == ["Your 10 minute timer is done."]
    assert h.ledger.row(-1, "garage")["attempts"] == 2


@pytest.mark.asyncio
async def test_an_interruption_is_recorded_and_never_retried() -> None:
    h = House("garage")
    h.room("garage").fail_next(AnnounceInterrupted("cut off"))
    await h.fire(due(1))
    await h.idle()
    assert h.room("garage").calls == 1
    assert h.outcome(-1, "garage") == ("interrupted", None)


@pytest.mark.asyncio
async def test_a_dead_socket_is_failed_send_failed_never_retried() -> None:
    h = House("garage")
    h.room("garage").fail_next(ConnectionError("socket closed"))
    await h.fire(due(1))
    await h.idle()
    assert h.room("garage").calls == 1
    assert h.outcome(-1, "garage") == ("failed", "send_failed")


# ─── C7 offline origin ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_offline_origin_reconnecting_within_grace_hears_it() -> None:
    h = House("kitchen")
    await h.fire(due(1, "garage"))
    await h.idle()
    assert h.outcome(-1, "garage") == ("pending", "offline")
    assert h.room("kitchen").texts == ["From the garage: Your 10 minute timer is done."]

    await h.advance(60.0)
    await h.sweep()
    garage = h.connect("garage")
    await h.idle()
    assert garage.texts == ["Your 10 minute timer is done."]
    assert h.outcome(-1, "garage") == ("spoken", None)


@pytest.mark.asyncio
async def test_an_offline_origin_back_after_grace_is_offline_never_spoken() -> None:
    h = House()
    await h.fire(due(1, "garage"))
    await h.advance(121.0)
    await h.sweep()
    assert h.outcome(-1, "garage") == ("offline", "offline")
    assert h.settled and h.settled[0]["outcomes"] == {"garage": "offline"}

    garage = h.connect("garage")
    await h.idle()
    assert garage.heard == []


@pytest.mark.asyncio
async def test_a_room_that_drops_mid_wait_and_misses_the_window_is_offline() -> None:
    h = House("garage", "kitchen")
    h.room("kitchen").busy("responding", True, T0 + 50)
    await h.fire(due(1, "garage"))
    await h.advance(1.0)
    del h.sessions["kitchen"]               # Wi-Fi drop while it waited
    await h.advance(1.0)
    await h.idle()
    assert h.outcome(-1, "kitchen") == ("pending", "offline")
    await h.advance(120.0)
    await h.sweep()
    assert h.outcome(-1, "kitchen") == ("offline", "offline")


# ─── C8 boot race ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_boot_a_fire_popped_before_accepting_waits_for_its_rooms() -> None:
    # The watcher's first tick runs inside lifespan startup, before uvicorn
    # accepts a single socket: the registry is empty. Nothing is lost.
    h = House(accepting=False)
    assert await h.fire(due(1, "garage", late=5)) == 1
    assert h.outcome(-1, "garage") == ("pending", "offline")

    # However long startup takes, the deadline is open until accepting.
    await h.advance(600.0)
    await h.sweep()
    assert h.outcome(-1, "garage") == ("pending", "offline")
    assert h.settled == []

    h.d.set_accepting()
    await h.advance(10.0)
    garage = h.connect("garage")
    kitchen = h.connect("kitchen")
    await h.idle()
    # 10 minutes of startup: it is late now, and says so.
    assert garage.texts == ["Your 10 minute timer went off 10 minutes ago."]
    assert kitchen.texts == ["From the garage: Your 10 minute timer went off 10 minutes ago."]

    # The window closes 120 s after accepting, then the fire settles.
    await h.advance(111.0)
    await h.sweep()
    assert h.settled and h.settled[0]["heard_in"] == ["garage", "kitchen"]


@pytest.mark.asyncio
async def test_boot_the_grace_window_counts_from_accepting() -> None:
    h = House(accepting=False)
    await h.fire(due(1, "garage"))
    await h.advance(30.0)
    h.d.set_accepting()
    await h.advance(119.0)            # 149 s after the fire, 119 s after accepting
    await h.sweep()
    garage = h.connect("garage")
    await h.idle()
    assert garage.texts == ["Your 10 minute timer went off 2 minutes ago."]


# ─── C9 late joiners ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_late_joiner_with_its_flag_off_hears_it_with_the_prefix() -> None:
    h = House("garage", own_only={"office"})
    await h.fire(due(1, "garage", label="call mom", message="call mom"))
    await h.idle()
    await h.advance(30.0)
    kitchen = h.connect("kitchen")
    office = h.connect("office")
    await h.idle()
    assert kitchen.texts == ["Reminder from the garage: call mom"]
    assert office.texts == []
    assert "office" not in h.ledger._rows[-1]


# ─── C10 never twice ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconnecting_after_it_was_spoken_never_repeats_it() -> None:
    h = House("garage", "kitchen")
    await h.fire(due(1, "garage"))
    await h.idle()
    for _ in range(2):
        h.connect("kitchen")
        await h.idle()
    assert h.room("kitchen").heard == []      # the fresh FakeRoom heard nothing
    assert h.outcome(-1, "kitchen") == ("spoken", None)


@pytest.mark.asyncio
async def test_duplicate_room_connected_calls_start_one_task() -> None:
    h = House()
    await h.fire(due(1, "garage"))
    garage = FakeRoom("garage", h.clock).busy("connecting", False, T0 + 2)
    h.sessions["garage"] = garage
    h.d.on_room_connected("garage")
    h.d.on_room_connected("garage")
    h.d.on_room_connected("garage")
    await _yield()
    assert list(h.d._tasks) == [(-1, "garage")]
    await h.advance(3.0)
    await h.idle()
    assert garage.texts == ["Your 10 minute timer is done."]


@pytest.mark.asyncio
async def test_two_fires_into_one_room_play_one_after_the_other_in_due_order() -> None:
    h = House("garage")
    # Popped in the wrong order on purpose: due order wins.
    await h.fire(
        due(2, "garage", label="eggs", late=1),
        due(1, "garage", label="pasta", late=3),
    )
    await h.idle()
    assert h.room("garage").texts == ["Your pasta timer is done.", "Your eggs timer is done."]


@pytest.mark.asyncio
async def test_a_claimed_row_is_never_claimed_again() -> None:
    ledger = Ledger()
    await ledger.add_target(-1, "garage", True)
    assert await ledger.add_target(-1, "garage", True) is False
    assert await ledger.claim(-1, "garage") is True
    assert await ledger.claim(-1, "garage") is False
    await ledger.finish(-1, "garage", "spoken", None)
    assert await ledger.claim(-1, "garage") is False


# ─── C11 events ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timer_fired_and_settled_events() -> None:
    h = House("garage", "kitchen")
    await h.fire(due(7, "garage", label="call mom", message="call mom"))
    await h.idle()
    assert len(h.fired) == 1
    payload = h.fired[0]
    assert set(payload) == {
        "fire_id", "timer_id", "kind", "label", "message", "origin_room_id",
        "due_at", "fired_at", "text", "targets",
    }
    assert payload["timer_id"] == 7 and payload["kind"] == "reminder"
    assert payload["text"] == "Reminder: call mom"
    assert payload["targets"] == ["garage", "kitchen"]
    assert payload["due_at"] == BASE.isoformat()

    assert h.settled == []
    await h.advance(121.0)
    await h.sweep()
    await h.sweep()
    assert len(h.settled) == 1
    assert h.settled[0] == {
        "fire_id": -1, "timer_id": 7, "kind": "reminder", "origin_room_id": "garage",
        "outcomes": {"garage": "spoken", "kitchen": "spoken"},
        "heard_in": ["garage", "kitchen"], "acked_by": None,
    }
    assert h.d._fires == {}


@pytest.mark.asyncio
async def test_the_global_bus_carries_the_timer_events() -> None:
    from domovoi.events import EVENTS

    seen: list[str] = []

    async def cb(evt):
        seen.append(evt.name)

    subs = [EVENTS.subscribe("core.timer_fired", cb),
            EVENTS.subscribe("core.timer_fire_settled", cb)]
    try:
        clock = FakeClock()
        ledger = Ledger()
        ledger.due.append(due(1, None))

        async def _sleep(dt):
            await asyncio.sleep(0)

        d = TimerDelivery(None, lambda: ledger, clock=clock, wall=clock.wall, sleep=_sleep)
        await d.tick()
        clock.t += 121
        await d.sweep()
        await _yield()
    finally:
        for sub in subs:
            EVENTS.unsubscribe(sub)
    assert seen == ["core.timer_fired", "core.timer_fire_settled"]


# ─── C12 restart resume ──────────────────────────────────────────────────


def _seed_unsettled(ledger: MemoryFireLedger, fire_id: int, *, age_sec: float,
                    rows: dict[str, str], origin: str | None = "garage") -> None:
    fired_at = BASE - timedelta(seconds=age_sec)
    ledger._fires[fire_id] = td.FireRecord(
        fire_id=fire_id, timer_id=100 + abs(fire_id), kind="timer", label=None,
        message=None, origin_room_id=origin, created_at=fired_at - timedelta(minutes=10),
        due_at=fired_at, fired_at=fired_at, base_text="Your 10 minute timer is done.",
    )
    ledger._rows[fire_id] = {
        room: {"is_origin": room == origin, "outcome": outcome, "detail": None,
               "attempts": 0, "spoken_text": None}
        for room, outcome in rows.items()
    }


@pytest.mark.asyncio
async def test_resume_after_a_restart() -> None:
    h = House(accepting=False)
    _seed_unsettled(h.ledger, -50, age_sec=120,
                    rows={"garage": "sending", "kitchen": "pending", "office": "spoken"})
    _seed_unsettled(h.ledger, -51, age_sec=900, rows={"garage": "pending", "den": "sending"})

    await h.d.tick()        # the first tick resumes before popping anything
    await _yield()
    # Caught mid-send: never risk saying it twice.
    assert h.outcome(-50, "garage") == ("failed", "core_restarted")
    # Recent: the waiting room is still waiting, with a fresh window.
    assert h.outcome(-50, "kitchen") == ("pending", None)
    assert h.outcome(-50, "office") == ("spoken", None)
    assert -50 in h.d._fires
    # Old: given up and settled.
    assert h.outcome(-51, "garage") == ("offline", "core_restarted")
    assert h.outcome(-51, "den") == ("failed", "core_restarted")
    assert [s["fire_id"] for s in h.settled] == [-51]

    h.d.set_accepting()
    kitchen = h.connect("kitchen")
    await h.idle()
    assert kitchen.texts == ["From the garage: Your 10 minute timer went off 2 minutes ago."]
    await h.advance(121.0)
    await h.sweep()
    assert [s["fire_id"] for s in h.settled] == [-51, -50]


# ─── C12b a stop with fires in flight (2026-09-30) ───────────────────────
#
# The core now really shuts down on SIGTERM (it used to be SIGKILLed after
# 90 s): uvicorn closes every satellite socket, then the lifespan teardown
# cancels what is left. Nothing may be lost to that, and nothing said twice.


async def _no_wait(_dt: float) -> None:
    await asyncio.sleep(0)


def _restarted(h: House) -> TimerDelivery:
    """The next process: the same ledger (the database), a fresh
    coordinator, no satellites connected yet."""
    lifecycle.reset()
    h.sessions.clear()
    return TimerDelivery(h.app, lambda: h.ledger, clock=h.clock, wall=h.clock.wall,
                         sleep=_no_wait, events=h.bus)


@pytest.mark.asyncio
async def test_a_stopping_core_pops_nothing() -> None:
    """A timer that comes due while the sockets are closing stays in
    `timers`: the next boot fires it, once its rooms are back."""
    h = House("garage", "kitchen")
    lifecycle.signal_shutdown("SIGTERM")
    assert await h.fire(due(1, "garage")) == 0
    assert h.ledger._fires == {}
    assert h.fired == []
    assert len(h.ledger.due) == 1
    assert h.room("garage").calls == 0


@pytest.mark.asyncio
async def test_a_room_still_waiting_at_the_stop_hears_it_after_the_restart() -> None:
    h = House("garage", "kitchen")
    h.room("kitchen").busy("responding", True, T0 + 3600)
    await h.fire(due(1, "garage"))
    await _yield()
    assert h.room("garage").texts == ["Your 10 minute timer is done."]
    assert h.outcome(-1, "kitchen")[0] == "pending"

    lifecycle.signal_shutdown("SIGTERM")
    await h.advance(1.0)
    # The waiting delivery gave up without claiming the room.
    assert not h.d._tasks
    assert h.outcome(-1, "kitchen")[0] == "pending"
    assert h.room("kitchen").calls == 0
    await h.d.shutdown()

    d2 = _restarted(h)
    await d2.tick()                       # resumes before popping anything
    d2.set_accepting()
    kitchen = h.connect("kitchen")        # tells the OLD coordinator: closed, no-op
    d2.on_room_connected("kitchen")
    garage = FakeRoom("garage", h.clock)
    h.sessions["garage"] = garage
    d2.on_room_connected("garage")
    for _ in range(100):
        await _yield()
        if not d2._tasks and not d2._aux:
            break
    assert kitchen.texts == ["From the garage: Your 10 minute timer is done."]
    assert garage.texts == []             # spoken there before the stop: never twice
    assert h.outcome(-1, "kitchen")[0] == "spoken"
    await d2.shutdown()


@pytest.mark.asyncio
async def test_an_announcement_cut_by_the_stop_is_never_repeated() -> None:
    """Mid-announcement when the teardown cancels it: the row is left
    'sending', and the next boot records failed/core_restarted — the room
    may have heard part of it, so it is not said again."""
    h = House("garage")
    playing = asyncio.Event()

    class _Playing(FakeRoom):
        async def announce(self, text: str, **kwargs) -> None:
            self.calls += 1
            playing.set()
            await asyncio.sleep(3600)

    h.sessions["garage"] = _Playing("garage", h.clock)
    await h.fire(due(1, "garage"))
    await asyncio.wait_for(playing.wait(), 1)
    assert h.outcome(-1, "garage")[0] == "sending"

    lifecycle.signal_shutdown("SIGTERM")
    await h.d.shutdown()
    assert h.outcome(-1, "garage")[0] == "sending"

    d2 = _restarted(h)
    await d2.tick()
    assert h.outcome(-1, "garage") == ("failed", "core_restarted")
    garage = FakeRoom("garage", h.clock)
    h.sessions["garage"] = garage
    d2.set_accepting()
    d2.on_room_connected("garage")
    await _yield(40)
    assert garage.calls == 0
    await d2.shutdown()


@pytest.mark.asyncio
async def test_a_socket_closed_before_the_first_frame_goes_back_to_pending() -> None:
    """uvicorn closed the socket while the first sentence synthesized:
    nothing was heard, so the room gets it when it reconnects."""
    h = House("garage")

    class _Closing(FakeRoom):
        async def announce(self, text: str, **kwargs) -> None:
            self.calls += 1
            h.sessions.pop(self.room_id, None)       # announce() evicts it
            raise AnnounceNotStarted("socket closed before the announcement started",
                                     reason="send_failed")

    closing = _Closing("garage", h.clock)
    h.sessions["garage"] = closing
    await h.fire(due(1, "garage"))
    await _yield(40)
    assert closing.calls == 1
    assert h.outcome(-1, "garage")[0] == "pending"

    garage = h.connect("garage")
    await h.idle()
    assert garage.texts == ["Your 10 minute timer is done."]
    assert h.outcome(-1, "garage")[0] == "spoken"


@pytest.mark.asyncio
async def test_a_delivery_still_synthesizing_at_the_stop_is_heard_after_the_restart() -> None:
    """SIGTERM a millisecond after a pop (seen in the Linux repro): the room
    is claimed and its first sentence is still synthesizing when the
    teardown stops the delivery. uvicorn has closed the socket by then, so
    the first frame fails and the row goes back to pending. Cancelled
    there instead, the task left the row 'sending' with not one frame
    sent, and the next boot recorded failed/core_restarted: a timer nobody
    ever heard."""
    h = House("garage")
    synthesizing = asyncio.Event()
    socket_closed = asyncio.Event()

    class _Synthesizing(FakeRoom):
        async def announce(self, text: str, **kwargs) -> None:
            self.calls += 1
            synthesizing.set()
            await socket_closed.wait()          # the first sentence is rendering...
            await asyncio.sleep(0.05)           # ...a little past the close
            h.sessions.pop(self.room_id, None)  # announce() evicts it
            raise AnnounceNotStarted("socket closed before the announcement started",
                                     reason="send_failed")

    h.sessions["garage"] = _Synthesizing("garage", h.clock)
    await h.fire(due(1, "garage"))
    await asyncio.wait_for(synthesizing.wait(), 1)
    assert h.outcome(-1, "garage")[0] == "sending"

    lifecycle.signal_shutdown("SIGTERM")
    socket_closed.set()                         # uvicorn closes every satellite socket
    await h.d.shutdown()
    assert h.outcome(-1, "garage")[0] == "pending"

    d2 = _restarted(h)
    await d2.tick()
    d2.set_accepting()
    garage = h.connect("garage")                # tells the OLD coordinator: closed, no-op
    d2.on_room_connected("garage")
    for _ in range(100):
        await _yield()
        if not d2._tasks and not d2._aux:
            break
    assert garage.texts == ["Your 10 minute timer is done."]
    assert h.outcome(-1, "garage")[0] == "spoken"
    await d2.shutdown()


@pytest.mark.asyncio
async def test_shutdown_does_not_wait_on_a_task_that_eats_the_cancel(caplog) -> None:
    h = House("garage")
    release = asyncio.Event()
    started = asyncio.Event()

    class _Stubborn(FakeRoom):
        async def announce(self, text: str, **kwargs) -> None:
            started.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(0.02)
                except asyncio.CancelledError:
                    continue

    h.sessions["garage"] = _Stubborn("garage", h.clock)
    await h.fire(due(1, "garage"))
    await asyncio.wait_for(started.wait(), 1)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await h.d.shutdown(grace_sec=0.1)
    assert loop.time() - t0 < 0.5
    assert any("still unwinding at shutdown" in m for m in caplog.messages)
    release.set()
    await asyncio.sleep(0.05)


# ─── C13 V018 missing ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_without_v018_timers_still_fire_and_fan_out(monkeypatch, caplog) -> None:
    probes: list[int] = []

    async def _no_v018() -> bool:
        probes.append(1)
        return False

    @asynccontextmanager
    async def _scope():
        yield None

    batches = [[due(1, "garage")], [due(2, "garage", label="eggs")]]

    class _Repo:
        def __init__(self, _s) -> None:
            pass

        async def pop_expired(self):
            return batches.pop(0) if batches else []

    monkeypatch.setattr(td, "_probe_v018", _no_v018)
    monkeypatch.setattr(td, "session_scope", _scope)
    monkeypatch.setattr(td, "TimerRepository", _Repo)

    clock = FakeClock()
    garage, kitchen = FakeRoom("garage", clock), FakeRoom("kitchen", clock)
    app = SimpleNamespace(state=SimpleNamespace(
        active_sessions={"garage": garage, "kitchen": kitchen}))

    async def _sleep(dt):
        await asyncio.sleep(0)

    d = TimerDelivery(app, clock=clock, wall=clock.wall, sleep=_sleep)
    d.set_accepting()
    with caplog.at_level(logging.WARNING, logger="domovoi.timer_delivery"):
        assert await d.tick() == 1
        await _yield(40)
        clock.t += 60
        assert await d.tick() == 1
        await _yield(40)
    assert isinstance(d._ledger, MemoryFireLedger)
    assert caplog.messages.count(td.MISSING_V018_WARNING) == 1
    assert len(probes) == 1                     # re-probed only every 10 min
    assert garage.texts == ["Your 10 minute timer is done.", "Your eggs timer is done."]
    assert kitchen.texts == ["From the garage: Your 10 minute timer is done.",
                             "From the garage: Your eggs timer is done."]

    clock.t += td.PROBE_RETRY_SEC
    await d.tick()
    assert len(probes) == 2


@pytest.mark.asyncio
async def test_v018_appearing_later_switches_to_the_real_ledger(monkeypatch) -> None:
    answers = [False, True]

    async def _probe() -> bool:
        return answers.pop(0)

    monkeypatch.setattr(td, "_probe_v018", _probe)
    clock = FakeClock()
    d = TimerDelivery(None, clock=clock)
    assert isinstance(await d.ensure_ledger(), MemoryFireLedger)
    clock.t += td.PROBE_RETRY_SEC + 1
    assert isinstance(await d.ensure_ledger(), FireLedger)


# ─── Real StreamSessions: a busy room, end to end (DB-free) ──────────────


def _wav(pcm: bytes, rate: int = 16_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class _TTS:
    async def synthesize(self, text, *, engine=None, voice=None):
        return _wav(b"\x01\x00" * 800)       # 0.1 s at 16 kHz


class _WS:
    def __init__(self, app) -> None:
        self.app = app
        self.frames: list[tuple[str, object]] = []

    async def send_text(self, t: str) -> None:
        self.frames.append(("text", json.loads(t)))

    async def send_bytes(self, b: bytes) -> None:
        self.frames.append(("bytes", len(b)))

    def spoken(self) -> list[str]:
        return [f["text"] for kind, f in self.frames
                if kind == "text" and f.get("type") == "response_start"]


def _real_app() -> SimpleNamespace:
    # What a StreamSession reads on announce() and on an utterance_start.
    return SimpleNamespace(state=SimpleNamespace(
        active_sessions={}, satellite_voice={}, resumable_music={},
        wifi_status={}, satellite_volume={}, satellite_config={},
        greeting_phrases=[], current_playlist={},
        probe=SimpleNamespace(online=True),
    ))


@pytest.fixture
def stub_tts(monkeypatch):
    from domovoi import streaming

    async def _voice(_name):
        return (None, None)

    monkeypatch.setattr(streaming, "get_tts_client", lambda: _TTS())
    monkeypatch.setattr(streaming, "resolve_voice", _voice)


async def _until(pred, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_busy_room_real_session_waits_out_the_reply_and_its_playback(stub_tts) -> None:
    from domovoi.streaming import StreamSession

    app = _real_app()
    ws = _WS(app)
    garage = StreamSession(ws, "garage")  # type: ignore[arg-type]
    app.state.active_sessions["garage"] = garage
    ledger = Ledger()
    d = TimerDelivery(app, lambda: ledger, poll_sec=0.01, wall=lambda: BASE)
    d.set_accepting()

    # A reply is being worked out in the garage when the timer goes off.
    reply_done = asyncio.Event()
    garage._response_task = asyncio.create_task(reply_done.wait())
    ledger.due.append(due(1, "garage"))
    await d.tick()
    await asyncio.sleep(0.1)
    assert ws.frames == []
    assert ledger.row(-1, "garage")["detail"] == "responding"

    # The reply streams 0.3 s of audio and ends; the announcement must come
    # after its playback, never interleaved with it.
    await garage._safe_send_text({"type": "response_start", "text": "It's ten."})
    await ws.send_bytes(b"\x00" * 9600)
    garage._note_audio_sent(9600, 16_000)
    await garage._safe_send_text(
        {"type": "response_end", "interrupted": False, "expect_followup": False})
    reply_done.set()
    await asyncio.sleep(0.1)
    assert ws.spoken() == ["It's ten."]
    assert garage.announce_block()[0] in ("playing", "settling")

    await _until(lambda: ledger.row(-1, "garage")["outcome"] == "spoken")
    assert ws.spoken() == ["It's ten.", "Your 10 minute timer is done."]
    ends = [i for i, (k, f) in enumerate(ws.frames)
            if k == "text" and f.get("type") == "response_end"]
    starts = [i for i, (k, f) in enumerate(ws.frames)
              if k == "text" and f.get("type") == "response_start"]
    assert ends[0] < starts[1]          # the reply ended before the timer began
    await d.shutdown()


@pytest.mark.asyncio
async def test_boot_race_real_sessions_connecting_after_the_first_tick(stub_tts) -> None:
    """The restart race from the 2026-09-30 investigation, end to end: the
    first tick pops a due timer with nobody connected; the satellites
    reconnect a moment later and each hears it once."""
    from domovoi.streaming import StreamSession

    app = _real_app()
    ledger = Ledger()
    d = TimerDelivery(app, lambda: ledger, poll_sec=0.01, wall=lambda: BASE)
    ledger.due.append(due(1, "garage", label="pasta"))
    assert await d.tick() == 1                 # lifespan startup: empty registry
    assert ledger.row(-1, "garage")["outcome"] == "pending"
    d.set_accepting()                          # uvicorn accepts sockets

    sockets = {}
    for room in ("garage", "kitchen"):
        ws = _WS(app)
        sess = StreamSession(ws, room)  # type: ignore[arg-type]
        sess.token_authenticated = True      # a paired satellite
        app.state.active_sessions[room] = sess
        d.on_room_connected(room)
        sockets[room] = ws
    await _until(lambda: all(
        ledger._rows[-1].get(r, {}).get("outcome") == "spoken" for r in ("garage", "kitchen")
    ))
    assert sockets["garage"].spoken() == ["Your pasta timer is done."]
    assert sockets["kitchen"].spoken() == ["From the garage: Your pasta timer is done."]

    # A second reconnect of the garage (a Wi-Fi flap) repeats nothing.
    ws = _WS(app)
    app.state.active_sessions["garage"] = StreamSession(ws, "garage")  # type: ignore[arg-type]
    d.on_room_connected("garage")
    await asyncio.sleep(0.2)
    assert ws.frames == []
    await d.shutdown()


# ─── Review fixes 2026-09-30 (DB-free) ───────────────────────────────────
#
# What the review of wf/int-0930 found, each pinned where it was fixed:
# the timer's own announce() call defers to a capture that began during the
# claim; a silent TTS is a TTS failure; TTS attempts are counted per fire
# and room across reconnects; the busy caps count from when a task was
# queued; a failed ledger write is retried instead of ending the delivery;
# the pop's room snapshot, the sweep and the room-connected check no longer
# race; a restart's resume is retried; a socket with no pairing token hears
# only its own room.


@pytest.mark.asyncio
async def test_the_coordinator_asks_announce_to_defer_to_a_capture() -> None:
    h = House("garage")
    await h.fire(due(1))
    await h.idle()
    assert h.room("garage").kwargs == [{"defer_to_capture": True}]


@pytest.mark.asyncio
async def test_a_capture_refusal_waits_uncounted_and_then_speaks() -> None:
    h = House("garage")
    h.room("garage").fail_next(
        AnnounceNotStarted("room garage capturing, announce deferred", reason="capturing"),
    )
    await h.fire(due(1))
    await h.idle()
    assert h.room("garage").texts == ["Your 10 minute timer is done."]
    assert h.ledger.row(-1, "garage")["attempts"] == 1


class _DropAfterFirstTTSFailure(FakeRoom):
    """A room whose first announcement fails in TTS and whose Wi-Fi drops
    right after (the next task starts on reconnect)."""

    def __init__(self, room_id, clock, house) -> None:
        super().__init__(room_id, clock)
        self.house = house

    async def announce(self, text: str, **kwargs) -> None:
        self.calls += 1
        del self.house.sessions[self.room_id]
        raise AnnounceNotStarted("tts down", reason="tts_failed")


@pytest.mark.asyncio
async def test_tts_attempts_are_counted_across_a_reconnect() -> None:
    """R7: at most 3 attempts in all. The count used to live in the task,
    so a reconnect started it again and a room with a dead TTS engine
    could be tried forever."""
    h = House()
    h.sessions["garage"] = _DropAfterFirstTTSFailure("garage", h.clock, h)
    await h.fire(due(1))
    await h.idle()
    assert "garage" not in h.sessions
    assert h.outcome(-1, "garage")[0] == "pending"
    garage = h.connect("garage")
    garage.fail_next(*[AnnounceNotStarted("tts down", reason="tts_failed") for _ in range(2)])
    await h.idle()
    assert garage.calls == 2
    assert h.outcome(-1, "garage") == ("failed", "tts_failed")
    assert garage.heard == []


@pytest.mark.asyncio
async def test_each_queued_fires_caps_count_from_when_it_was_queued() -> None:
    """Behind a long call a room gives up on every queued fire at the cap
    (R3: "from when this room's task began waiting"), not at N x 300 s."""
    h = House("garage")
    h.room("garage").busy("in_call", True, until=T0 + 10_000_000)
    await h.fire(due(1, "garage"), due(2, "garage"))
    await h.advance(305, step=1.0)
    await h.idle()
    assert h.outcome(-1, "garage") == ("busy_timeout", "in_call")
    assert h.outcome(-2, "garage") == ("busy_timeout", "in_call")


@pytest.mark.asyncio
async def test_the_soft_window_also_counts_from_the_queue() -> None:
    h = House("garage")
    # The first fire plays at once; the second waits for it and then for a
    # follow-up hold that would run past its 45 s soft window.
    h.room("garage").busy("followup", False, until=T0 + 1000)
    await h.fire(due(1, "garage", label="eggs"), due(2, "garage", label="tea"))
    await h.advance(46.0)
    await h.idle()
    assert h.outcome(-1, "garage") == ("spoken", "forced_over:followup")
    assert h.outcome(-2, "garage") == ("spoken", "forced_over:followup")


class _FlakyLedger(Ledger):
    """Each named method raises ConnectionError the first ``n`` times."""

    def __init__(self, **fail) -> None:
        super().__init__()
        self.fail = dict(fail)

    def _maybe(self, name: str) -> None:
        if self.fail.get(name, 0) > 0:
            self.fail[name] -= 1
            raise ConnectionError(f"db blip in {name}")

    async def note_detail(self, fire_id, room_id, detail):
        self._maybe("note_detail")
        return await super().note_detail(fire_id, room_id, detail)

    async def claim(self, fire_id, room_id):
        self._maybe("claim")
        return await super().claim(fire_id, room_id)

    async def finish(self, fire_id, room_id, outcome, detail, spoken_text=None):
        self._maybe("finish")
        return await super().finish(fire_id, room_id, outcome, detail, spoken_text)

    async def release(self, fire_id, room_id, detail, *, count_attempt):
        self._maybe("release")
        return await super().release(fire_id, room_id, detail, count_attempt=count_attempt)

    async def unsettled(self):
        self._maybe("unsettled")
        return await super().unsettled()


def _flaky_house(*rooms, **fail) -> House:
    h = House(*rooms)
    h.ledger = _FlakyLedger(**fail)
    h.d._ledger = None
    h.d._ledger_factory = lambda: h.ledger
    return h


@pytest.mark.asyncio
async def test_a_failed_wait_reason_write_never_ends_the_delivery() -> None:
    h = _flaky_house("garage", note_detail=1)
    h.room("garage").busy("responding", True, until=T0 + 3)
    await h.fire(due(1, "garage"))
    await h.advance(10)
    await h.idle()
    assert h.room("garage").texts == ["Your 10 minute timer is done."]
    assert h.outcome(-1, "garage") == ("spoken", None)


@pytest.mark.asyncio
async def test_a_failed_claim_is_tried_again() -> None:
    h = _flaky_house("garage", claim=2)
    await h.fire(due(1, "garage"))
    await h.advance(3.0)
    await h.idle()
    assert h.room("garage").texts == ["Your 10 minute timer is done."]
    assert h.sleeps.count(td.LEDGER_RETRY_SEC) == 2


@pytest.mark.asyncio
async def test_a_failed_outcome_write_is_tried_again_so_spoken_is_not_lost() -> None:
    h = _flaky_house("garage", finish=1)
    await h.fire(due(1, "garage"))
    await h.advance(2.0)
    await h.idle()
    assert h.outcome(-1, "garage") == ("spoken", None)
    assert h.room("garage").calls == 1           # retried the write, not the speech


@pytest.mark.asyncio
async def test_a_failed_release_is_tried_again() -> None:
    h = _flaky_house("garage", release=1)
    h.room("garage").fail_next(
        AnnounceNotStarted("room garage mid-response, announce skipped", reason="responding"),
    )
    await h.fire(due(1, "garage"))
    await h.advance(3.0)
    await h.idle()
    assert h.outcome(-1, "garage") == ("spoken", None)


@pytest.mark.asyncio
async def test_a_claim_that_keeps_failing_gives_up_when_the_room_goes() -> None:
    h = _flaky_house("garage", claim=10_000)
    await h.fire(due(1, "garage"))
    await h.advance(2.0)
    del h.sessions["garage"]
    await h.advance(2.0)
    await h.idle()
    assert h.outcome(-1, "garage")[0] == "pending"


@pytest.mark.asyncio
async def test_a_failed_resume_is_tried_again_on_the_next_tick(caplog) -> None:
    h = _flaky_house(unsettled=1)
    h.d._accepting_since = None
    _seed_unsettled(h.ledger, -50, age_sec=120, rows={"kitchen": "pending"})
    with caplog.at_level(logging.WARNING, logger="domovoi.timer_delivery"):
        await h.d.tick()
    assert any("resuming announcements" in m for m in caplog.messages)
    assert -50 not in h.d._fires
    await h.d.tick()
    assert -50 in h.d._fires
    h.d.set_accepting()
    kitchen = h.connect("kitchen")
    await h.idle()
    assert kitchen.texts == ["From the garage: Your 10 minute timer went off 2 minutes ago."]


class _ConnectsDuringPop(Ledger):
    """The kitchen's satellite gets `ready` while the pop transaction runs,
    between the targets of the first fire and the second: its own
    room-connected check found no fire yet."""

    def __init__(self, house) -> None:
        super().__init__()
        self.house = house

    async def pop_due(self, targets_for):
        calls = {"n": 0}

        def wrapped(origin, own_only):
            calls["n"] += 1
            if calls["n"] == 2:
                self.house.sessions["kitchen"] = FakeRoom("kitchen", self.house.clock)
                self.house.d.on_room_connected("kitchen")
            return targets_for(origin, own_only)

        return await super().pop_due(wrapped)


@pytest.mark.asyncio
async def test_a_room_connecting_during_a_multi_fire_pop_gets_every_fire() -> None:
    h = House("garage")
    h.ledger = _ConnectsDuringPop(h)
    h.d._ledger = None
    h.d._ledger_factory = lambda: h.ledger
    await h.fire(due(1, "garage", label="eggs"), due(2, "garage", label="tea"))
    await h.idle()
    # Both, whichever first (the one it joined late comes second).
    assert sorted(h.room("kitchen").texts) == ["From the garage: Your eggs timer is done.",
                                               "From the garage: Your tea timer is done."]


class _SlowRows(Ledger):
    """rows() parks until released: a room-connected check in flight."""

    def __init__(self) -> None:
        super().__init__()
        self.hold: asyncio.Event | None = None
        self.parked = asyncio.Event()

    async def rows(self, fire_id):
        if self.hold is not None:
            self.parked.set()
            await self.hold.wait()
        return await super().rows(fire_id)


@pytest.mark.asyncio
async def test_the_sweep_waits_for_a_room_being_added_before_settling() -> None:
    """A room that reconnects inside the grace window is never recorded
    offline, nor the fire settled, while its check is still reading the
    ledger; and it is not announced after the fire settled."""
    h = House()
    h.ledger = _SlowRows()
    h.d._ledger = None
    h.d._ledger_factory = lambda: h.ledger
    await h.fire(due(1, "garage"))
    assert h.outcome(-1, "garage") == ("pending", "offline")

    await h.advance(119.0)
    h.ledger.hold = asyncio.Event()
    garage = h.connect("garage")                 # just inside the window
    await asyncio.wait_for(h.ledger.parked.wait(), 2)
    await h.advance(5.0)                         # the deadline passes meanwhile
    await h.sweep()
    assert h.settled == []
    assert h.outcome(-1, "garage") == ("pending", "offline")

    h.ledger.hold.set()
    h.ledger.hold = None
    await h.idle()
    assert garage.texts == ["Your 10 minute timer went off 2 minutes ago."]
    await h.sweep()
    assert h.settled and h.settled[0]["outcomes"] == {"garage": "spoken"}


@pytest.mark.asyncio
async def test_a_check_that_finds_the_fire_settled_starts_nothing() -> None:
    h = House()
    h.ledger = _SlowRows()
    h.d._ledger = None
    h.d._ledger_factory = lambda: h.ledger
    await h.fire(due(1, "garage"))
    h.ledger.hold = asyncio.Event()
    garage = h.connect("garage")
    await asyncio.wait_for(h.ledger.parked.wait(), 2)
    # Settled some other way meanwhile (a restart's resume of an old fire).
    h.d._fires.pop(-1)
    h.ledger.hold.set()
    h.ledger.hold = None
    await h.idle()
    assert garage.heard == []


def _unpaired(room: str, clock: FakeClock) -> FakeRoom:
    r = FakeRoom(room, clock)
    r.token_authenticated = False   # type: ignore[attr-defined]
    return r


@pytest.mark.asyncio
async def test_a_socket_with_no_pairing_token_hears_only_its_own_rooms(caplog) -> None:
    """Security review 2026-09-30: with strict pairing off any LAN client
    can open /v1/stream/<new name> with no token and was a house-wide
    target, receiving every room's reminder words (response_start.text),
    reminders set with no room included."""
    h = House("garage")
    h.sessions["spy"] = _unpaired("spy", h.clock)
    with caplog.at_level(logging.WARNING, logger="domovoi.timer_delivery"):
        await h.fire(
            due(1, "garage", label="biopsy", message="pick up the biopsy results"),
            due(2, None, label="pill", message="take the pill"),
            due(3, "spy", label="eggs"),
        )
        await h.idle()
    assert h.room("spy").texts == ["Your eggs timer is done."]
    # Nor does it speak to the house (security review 2026-09-30, second
    # round): what is set in a tokenless room is announced there only.
    assert h.room("garage").texts == [
        "Reminder: pick up the biopsy results", "Reminder: take the pill"]
    assert "spy" not in h.ledger._rows[-1] and "spy" not in h.ledger._rows[-2]
    assert set(h.ledger._rows[-3]) == {"spy"}
    warned = [m for m in caplog.messages if "has no pairing token" in m]
    assert warned == [
        "room spy has no pairing token; it announces only its own timers and reminders "
        "(pair it, or turn on SATELLITE_PAIRING_STRICT)"]
    # Reminder words never reach the journal through this line.
    assert all("biopsy" not in m and "pill" not in m for m in warned)


@pytest.mark.asyncio
async def test_an_unpaired_late_joiner_gets_no_other_rooms_fire() -> None:
    h = House("garage")
    await h.fire(due(1, "garage", label="call mom", message="call mom"))
    await h.idle()
    h.sessions["spy"] = _unpaired("spy", h.clock)
    h.d.on_room_connected("spy")
    await h.idle()
    assert h.room("spy").heard == []
    assert "spy" not in h.ledger._rows[-1]


@pytest.mark.asyncio
async def test_an_unpaired_origin_still_announces_its_own_after_a_reconnect() -> None:
    h = House()
    await h.fire(due(1, "garage"))
    h.sessions["garage"] = _unpaired("garage", h.clock)
    h.d.on_room_connected("garage")
    await h.idle()
    assert h.room("garage").texts == ["Your 10 minute timer is done."]


# ─── Real StreamSessions: the review's repros (DB-free) ──────────────────


class _SlowClaimLedger(Ledger):
    def __init__(self) -> None:
        super().__init__()
        self.in_claim = asyncio.Event()
        self.release_claim = asyncio.Event()

    async def claim(self, fire_id, room_id):
        self.in_claim.set()
        await self.release_claim.wait()     # a database round trip
        return await super().claim(fire_id, room_id)


@pytest.mark.asyncio
async def test_a_capture_that_begins_during_the_claim_is_not_talked_over(stub_tts) -> None:
    """R3/R4: never talk over an active capture. The claim is a round trip;
    an utterance_start in that gap found no announce lock and no task, and
    the announcement went out into the capture — its response_end then
    released the satellite's wait for the real reply."""
    from domovoi.streaming import StreamSession

    app = _real_app()
    ws = _WS(app)
    sess = StreamSession(ws, "kitchen")  # type: ignore[arg-type]
    app.state.active_sessions["kitchen"] = sess
    ledger = _SlowClaimLedger()
    ledger.due.append(due(1, "kitchen"))
    d = TimerDelivery(app, lambda: ledger, poll_sec=0.01, wall=lambda: BASE)
    d.set_accepting()
    await d.tick()
    await asyncio.wait_for(ledger.in_claim.wait(), 2)
    await sess._on_control({"type": "utterance_start", "trigger": "wake_word"})
    await sess._on_audio(b"\x00" * 640)
    ledger.release_claim.set()
    await _until(lambda: ledger.row(-1, "kitchen")["detail"] == "capturing"
                 and ledger.row(-1, "kitchen")["outcome"] == "pending")
    await asyncio.sleep(0.1)
    assert ws.spoken() == []
    assert ledger.row(-1, "kitchen")["attempts"] == 0      # not counted

    # The capture ends (a follow-up that timed out): the room takes it now.
    sess.utterance_active = False
    await _until(lambda: ledger.row(-1, "kitchen")["outcome"] == "spoken")
    assert ws.spoken() == ["Your 10 minute timer is done."]
    await d.shutdown()


class _SilentTTS:
    async def synthesize(self, text, *, engine=None, voice=None):
        return _wav(b"")           # RealTTSClient when every engine failed


@pytest.mark.asyncio
async def test_a_silent_tts_is_a_tts_failure_never_heard(monkeypatch) -> None:
    from domovoi import streaming
    from domovoi.streaming import StreamSession

    async def _voice(_name):
        return (None, None)

    monkeypatch.setattr(streaming, "get_tts_client", lambda: _SilentTTS())
    monkeypatch.setattr(streaming, "resolve_voice", _voice)

    async def _fast(_dt):
        await asyncio.sleep(0)

    app = _real_app()
    ws = _WS(app)
    app.state.active_sessions["garage"] = StreamSession(ws, "garage")  # type: ignore[arg-type]
    ledger = Ledger()
    ledger.due.append(due(1, "garage"))
    d = TimerDelivery(app, lambda: ledger, sleep=_fast, wall=lambda: BASE)
    d.set_accepting()
    await d.tick()
    await _until(lambda: ledger.row(-1, "garage")["outcome"] != "pending"
                 and ledger.row(-1, "garage")["outcome"] != "sending")
    assert (ledger.row(-1, "garage")["outcome"], ledger.row(-1, "garage")["detail"]) == (
        "failed", "tts_failed")
    assert ws.frames == []
    await d.shutdown()


class _FakePairings:
    """SatellitePairingRepository stand-in: ``rows`` maps room to hash."""

    rows: dict[str, str] = {}

    def __init__(self, _s) -> None:
        pass

    async def get_pairing(self, room_id):
        h = self.rows.get(room_id)
        return None if h is None else (h,)

    async def pair(self, room_id, token_hash):
        self.rows[room_id] = token_hash

    async def touch_last_seen(self, room_id):
        return None


@pytest.fixture
def fake_pairing(monkeypatch):
    from domovoi import streaming

    @asynccontextmanager
    async def _scope():
        yield SimpleNamespace()

    monkeypatch.setattr(_FakePairings, "rows", {})
    monkeypatch.setattr(streaming, "session_scope", _scope)
    monkeypatch.setattr(streaming, "SatellitePairingRepository", _FakePairings)
    monkeypatch.setattr(settings, "satellite_pairing_strict", False)
    return _FakePairings


@pytest.mark.asyncio
async def test_a_tokenless_stream_hears_no_other_rooms_reminder(stub_tts, fake_pairing) -> None:
    """The security review's repro, end to end: a tokenless hello (case 5,
    strict pairing off) is accepted but never hears another room's fire or
    a roomless one; a token-paired satellite does."""
    from domovoi.admin_auth import token_sha256
    from domovoi.streaming import StreamSession

    app = _real_app()
    spy_ws = _WS(app)
    spy = StreamSession(spy_ws, "spy")  # type: ignore[arg-type]
    assert await spy._validate_pairing({"type": "hello", "room_id": "spy"}) is True
    assert spy.token_authenticated is False
    app.state.active_sessions["spy"] = spy

    fake_pairing.rows["kitchen"] = token_sha256("kitchen-token")
    kitchen_ws = _WS(app)
    kitchen = StreamSession(kitchen_ws, "kitchen")  # type: ignore[arg-type]
    assert await kitchen._validate_pairing(
        {"type": "hello", "room_id": "kitchen", "pairing_token": "kitchen-token"}) is True
    assert kitchen.token_authenticated is True
    app.state.active_sessions["kitchen"] = kitchen

    garage_ws = _WS(app)
    garage = StreamSession(garage_ws, "garage")  # type: ignore[arg-type]
    garage.token_authenticated = True        # a paired satellite
    app.state.active_sessions["garage"] = garage

    ledger = Ledger()
    d = TimerDelivery(app, lambda: ledger, poll_sec=0.01, wall=lambda: BASE)
    d.set_accepting()
    ledger.due.append(due(1, "garage", label="biopsy", message="pick up the biopsy results"))
    ledger.due.append(due(2, None, label="pill", message="take the pill"))
    await d.tick()
    await _until(lambda: len(kitchen_ws.spoken()) == 2)
    await asyncio.sleep(0.1)
    assert kitchen_ws.spoken() == ["Reminder from the garage: pick up the biopsy results",
                                   "Reminder: take the pill"]
    assert spy_ws.spoken() == []

    # A tokenless late joiner inside the grace window: nothing either.
    late_ws = _WS(app)
    late = StreamSession(late_ws, "spy-late")  # type: ignore[arg-type]
    assert await late._validate_pairing({"type": "hello", "room_id": "spy-late"}) is True
    app.state.active_sessions["spy-late"] = late
    d.on_room_connected("spy-late")
    await asyncio.sleep(0.2)
    assert late_ws.spoken() == []
    await d.shutdown()


@pytest.mark.asyncio
async def test_a_tokenless_rooms_own_fires_stay_in_that_room() -> None:
    """The origin side of the pairing rule: a timer or reminder set in a
    room whose socket has no pairing token (strict pairing off) is
    announced there only, and a paired room that connects inside the grace
    window is not added to it. A paired room's fire still reaches every
    other paired room (and still skips the tokenless one)."""
    h = House("kitchen")
    h.sessions["spy"] = _unpaired("spy", h.clock)
    await h.fire(due(1, "spy", label="eggs", message="the spy's words"))
    await h.idle()
    assert h.room("spy").texts == ["Reminder: the spy's words"]
    assert h.room("kitchen").texts == []
    assert set(h.ledger._rows[-1]) == {"spy"}
    late = h.connect("office")                   # paired, inside the window
    await h.idle()
    assert late.texts == []
    assert set(h.ledger._rows[-1]) == {"spy"}
    # A paired room's fire still goes everywhere it should.
    await h.fire(due(2, "kitchen", label="pasta"))
    await h.idle()
    assert late.texts == ["From the kitchen: Your pasta timer is done."]
    assert h.room("spy").texts == ["Reminder: the spy's words"]


@pytest.mark.asyncio
async def test_a_first_pairing_with_a_token_is_token_authenticated(fake_pairing) -> None:
    from domovoi.streaming import StreamSession

    app = _real_app()
    sess = StreamSession(_WS(app), "attic")  # type: ignore[arg-type]
    assert await sess._validate_pairing(
        {"type": "hello", "room_id": "attic", "pairing_token": "attic-token"}) is True
    assert sess.token_authenticated is True


@pytest.mark.asyncio
async def test_a_pairing_check_that_cannot_run_is_not_token_authenticated(monkeypatch) -> None:
    from domovoi import streaming
    from domovoi.streaming import StreamSession

    @asynccontextmanager
    async def _down():
        raise ConnectionError("database down")
        yield  # pragma: no cover

    monkeypatch.setattr(streaming, "session_scope", _down)
    monkeypatch.setattr(settings, "satellite_pairing_strict", False)
    sess = StreamSession(_WS(_real_app()), "garage")  # type: ignore[arg-type]
    assert await sess._validate_pairing(
        {"type": "hello", "room_id": "garage", "pairing_token": "t"}) is True
    assert sess.token_authenticated is False


# ─── DB tier: the V018 ledger ────────────────────────────────────────────


@pytest.fixture
async def v018(db_session):
    await apply_v018()
    yield db_session
    # End the test's transaction first: the TRUNCATE waits for its locks.
    await db_session.rollback()
    await apply_v018()


async def _add_timer(*, room: str | None, label=None, message=None,
                     age_sec: float = 5, duration: int = 600) -> None:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await s.execute(
            text(
                "INSERT INTO timers (label, message, room_id, created_at, expires_at) "
                "VALUES (:l, :m, :r, now() - make_interval(secs => :set_ago), "
                "now() - make_interval(secs => :age))"
            ),
            {"l": label, "m": message, "r": room, "age": float(age_sec),
             "set_ago": float(age_sec + duration)},
        )


async def _q(sql: str, **params):
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return (await s.execute(text(sql), params)).all()


class _Heard:
    """LISTEN on both timer channels on a raw asyncpg connection."""

    def __init__(self) -> None:
        self.got: list[tuple[str, str]] = []
        self._conn = None

    async def __aenter__(self):
        import asyncpg

        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        self._conn = await asyncpg.connect(dsn)
        for ch in ("timers_changed", "timer_fires_changed"):
            await self._conn.add_listener(
                ch, lambda _c, _p, channel, payload: self.got.append((channel, payload)))
        return self

    async def __aexit__(self, *exc):
        await self._conn.close()

    async def settle(self, n: int | None = None, timeout: float = 2.0):
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        if n is None:
            await asyncio.sleep(0.3)
        while n is not None and len(self.got) < n and loop.time() < end:
            await asyncio.sleep(0.05)
        return list(self.got)


def _targets(online: set[str]):
    def targets_for(origin, own_only):
        out = []
        if origin is not None:
            out.append((origin, True, None if origin in online else "offline"))
        for room in sorted(online):
            if room != origin and is_target(room, origin, own_only):
                out.append((room, False, None))
        return out
    return targets_for


@requires_db
@pytest.mark.asyncio
async def test_pop_due_records_targets_and_notifies_only_when_something_fired(v018) -> None:
    await _q("INSERT INTO timer_own_only_rooms (room_id) VALUES ('office') RETURNING 1")
    ledger = FireLedger()
    async with _Heard() as heard:
        assert await ledger.pop_due(_targets({"kitchen", "office"})) == []
        assert await heard.settle() == []

        await _add_timer(room="garage", label="call mom", message="call mom")
        [(rec, rows)] = await ledger.pop_due(_targets({"kitchen", "office"}))
        got = await heard.settle(2)
    assert ("timers_changed", "fired") in got
    assert ("timer_fires_changed", str(rec.fire_id)) in got
    assert rec.kind == "reminder" and rec.base_text == "Reminder: call mom"
    assert rows == [("garage", True, "pending", "offline"), ("kitchen", False, "pending", None)]
    assert await _q("SELECT count(*) FROM timers") == [(0,)]
    db_rows = await _q(
        "SELECT room_id, is_origin, outcome, detail FROM timer_fire_deliveries "
        "WHERE fire_id = :f ORDER BY is_origin DESC, room_id", f=rec.fire_id)
    assert [tuple(r) for r in db_rows] == [
        ("garage", True, "pending", "offline"), ("kitchen", False, "pending", None)]
    [(kind, message, base, created, due_at)] = await _q(
        "SELECT kind, message, base_text, created_at, due_at FROM timer_fires")
    assert (kind, message, base) == ("reminder", "call mom", "Reminder: call mom")
    assert round((due_at - created).total_seconds()) == 600


@requires_db
@pytest.mark.asyncio
async def test_pop_due_is_atomic_a_failed_insert_keeps_the_timer(v018) -> None:
    await _add_timer(room="garage", label="pasta")
    ledger = FireLedger()

    def exploding_targets(origin, own_only):
        # A NOT NULL room_id violation on the deliveries insert.
        return [(None, True, None)]

    with pytest.raises(Exception):
        await ledger.pop_due(exploding_targets)
    assert await _q("SELECT label FROM timers") == [("pasta",)]
    assert await _q("SELECT count(*) FROM timer_fires") == [(0,)]

    [(rec, _rows)] = await ledger.pop_due(_targets(set()))
    assert rec.label == "pasta"


@requires_db
@pytest.mark.asyncio
async def test_claim_is_exclusive_late_joiners_conflict_and_prune_cascades(v018) -> None:
    ledger = FireLedger()
    await _add_timer(room="garage")
    [(rec, _)] = await ledger.pop_due(_targets({"garage"}))
    fid = rec.fire_id

    assert await ledger.claim(fid, "garage") is True
    assert await ledger.claim(fid, "garage") is False
    await ledger.release(fid, "garage", "responding", count_attempt=False)
    assert await ledger.claim(fid, "garage") is True
    assert await ledger.finish(fid, "garage", "spoken", None, "Your 10 minute timer is done.")
    assert await ledger.finish(fid, "garage", "failed", "send_failed") is False
    assert await ledger.claim(fid, "garage") is False
    [(outcome, attempts, spoken)] = await _q(
        "SELECT outcome, attempts, spoken_text FROM timer_fire_deliveries WHERE fire_id = :f",
        f=fid)
    assert (outcome, attempts, spoken) == ("spoken", 1, "Your 10 minute timer is done.")

    assert await ledger.add_target(fid, "kitchen", False) is True
    assert await ledger.add_target(fid, "kitchen", False) is False
    await ledger.note_detail(fid, "kitchen", "capturing")
    assert await ledger.rows(fid) == [
        ("garage", True, "spoken", None), ("kitchen", False, "pending", "capturing")]
    assert [r[0].fire_id for r in await ledger.unsettled()] == [fid]
    assert await ledger.settle(fid) is None
    assert await ledger.unsettled() == []

    await _q("UPDATE timer_fires SET fired_at = now() - interval '8 days' RETURNING 1")
    assert await ledger.prune(7) == 1
    assert await _q("SELECT count(*) FROM timer_fire_deliveries") == [(0,)]


@requires_db
@pytest.mark.asyncio
async def test_boot_race_through_the_real_ledger(v018, stub_tts) -> None:
    """A timer due while the core was down: the first tick (no satellite
    connected yet) records it, the rooms reconnect after the server starts
    accepting, and each hears it once — recorded in timer_fire_deliveries."""
    from domovoi.streaming import StreamSession

    await _add_timer(room="garage", label="pasta", age_sec=20)
    app = _real_app()
    d = TimerDelivery(app, poll_sec=0.01)
    assert await d.tick() == 1
    assert isinstance(d._ledger, FireLedger)
    [(fid,)] = await _q("SELECT id FROM timer_fires")
    await d.sweep()
    assert await _q("SELECT settled_at FROM timer_fires") == [(None,)]

    d.set_accepting()
    socks = {}
    for room in ("garage", "kitchen"):
        ws = _WS(app)
        sess = StreamSession(ws, room)  # type: ignore[arg-type]
        sess.token_authenticated = True      # a paired satellite
        app.state.active_sessions[room] = sess
        d.on_room_connected(room)
        socks[room] = ws

    async def _both_spoken():
        rows = await _q("SELECT outcome FROM timer_fire_deliveries WHERE outcome = 'spoken'")
        return len(rows) == 2

    loop = asyncio.get_running_loop()
    end = loop.time() + 5
    while not await _both_spoken():
        assert loop.time() < end, await _q("SELECT * FROM timer_fire_deliveries")
        await asyncio.sleep(0.05)
    assert socks["garage"].spoken() == ["Your pasta timer is done."]
    assert socks["kitchen"].spoken() == ["From the garage: Your pasta timer is done."]
    assert [tuple(r) for r in await _q(
        "SELECT room_id, is_origin, spoken_text FROM timer_fire_deliveries "
        "WHERE fire_id = :f ORDER BY is_origin DESC, room_id", f=fid)] == [
        ("garage", True, "Your pasta timer is done."),
        ("kitchen", False, "From the garage: Your pasta timer is done."),
    ]
    await d.shutdown()


@requires_db
@pytest.mark.asyncio
async def test_resume_marks_a_mid_send_row_failed_through_the_real_ledger(v018) -> None:
    ledger = FireLedger()
    await _add_timer(room="garage")
    [(rec, _)] = await ledger.pop_due(_targets({"garage", "kitchen"}))
    assert await ledger.claim(rec.fire_id, "garage")
    # The core dies here. A fresh one resumes.
    d = TimerDelivery(_real_app(), poll_sec=0.01)
    await d.tick()
    assert await ledger.rows(rec.fire_id) == [
        ("garage", True, "failed", "core_restarted"), ("kitchen", False, "pending", None)]
    await d.shutdown()


@requires_db
@pytest.mark.asyncio
async def test_retiring_a_room_clears_its_timer_scope_but_keeps_history(v018) -> None:
    from domovoi import main as core_main

    await _q("INSERT INTO timer_own_only_rooms (room_id) VALUES ('attic'), ('den') RETURNING 1")
    await _add_timer(room="attic")
    await FireLedger().pop_due(_targets(set()))

    await core_main._forget_timer_scope("attic")
    assert await _q("SELECT room_id FROM timer_own_only_rooms") == [("den",)]
    assert await _q("SELECT origin_room_id FROM timer_fires") == [("attic",)]


@pytest.mark.asyncio
async def test_retiring_a_room_without_v018_is_quiet(monkeypatch, caplog) -> None:
    from domovoi import main as core_main

    class _Missing(Exception):
        def __str__(self) -> str:
            return 'relation "timer_own_only_rooms" does not exist'

    @asynccontextmanager
    async def _scope():
        raise _Missing()
        yield  # pragma: no cover

    monkeypatch.setattr(core_main, "session_scope", _scope)
    with caplog.at_level(logging.WARNING):
        await core_main._forget_timer_scope("attic")
    assert not [r for r in caplog.records if "timer scope" in r.getMessage()]


# ─── DB tier: acknowledged means done (2026-09-30 review) ───────────────


def _house_on_real_ledger(*rooms):
    clock = FakeClock()
    sessions = {r: FakeRoom(r, clock) for r in rooms}
    app = SimpleNamespace(state=SimpleNamespace(active_sessions=sessions))

    async def _sleep(_dt):
        await asyncio.sleep(0)

    d = TimerDelivery(app, FireLedger, clock=clock, sleep=_sleep, events=EventBus())
    d.set_accepting()
    return d, sessions, clock


async def _drain(d, n: int = 300) -> None:
    for _ in range(n):
        await asyncio.sleep(0.01)
        if not d._tasks and not d._aux:
            return


@requires_db
@pytest.mark.asyncio
async def test_a_room_that_joins_after_the_acknowledgement_never_hears_it(v018) -> None:
    """The review's repro: the garage says "stop the timer"; the office,
    back from a Wi-Fi drop inside the grace window (or any room after a
    core restart), used to get a fresh row and announce it anyway."""
    from domovoi.db.session import session_scope
    from domovoi.timer_delivery import ack_recent_fire

    d, sessions, clock = _house_on_real_ledger("garage", "kitchen")
    await _add_timer(room="garage")
    assert await d.tick() == 1
    await _drain(d)
    assert sessions["garage"].texts and sessions["kitchen"].texts
    async with session_scope() as s:
        assert await ack_recent_fire(s, "garage") is not None
    sessions["office"] = FakeRoom("office", clock)
    d.on_room_connected("office")
    await _drain(d)
    assert sessions["office"].heard == []
    assert await _q("SELECT room_id FROM timer_fire_deliveries WHERE room_id = 'office'") == []
    await d.shutdown()


@requires_db
@pytest.mark.asyncio
async def test_the_ledger_refuses_a_late_row_after_an_acknowledgement(v018) -> None:
    ledger = FireLedger()
    await _add_timer(room="garage")
    [(rec, _)] = await ledger.pop_due(_targets({"garage", "kitchen"}))
    fid = rec.fire_id
    await _q("UPDATE timer_fires SET acked_at = now(), acked_by = 'garage' RETURNING 1")
    # A late joiner is not added; the origin's own row always could be.
    assert await ledger.add_target(fid, "office", False) is False
    # A row that slipped in before the ack is cancelled when claimed, not spoken.
    await _q("INSERT INTO timer_fire_deliveries (fire_id, room_id) VALUES (:f, 'den') RETURNING 1",
             f=fid)
    assert await ledger.claim(fid, "den") is False
    assert [tuple(r) for r in await _q(
        "SELECT outcome, detail FROM timer_fire_deliveries WHERE fire_id = :f AND room_id = 'den'",
        f=fid)] == [("cancelled", "acknowledged:garage")]
    # The origin still announces its own (owner rule 2).
    assert await ledger.claim(fid, "garage") is True
