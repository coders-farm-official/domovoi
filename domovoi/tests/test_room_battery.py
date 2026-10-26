"""A phone room's battery: the low-battery warnings (domovoi/room_battery.py).

The owner's rules (2026-10-25): a phone room warns at 20 % and again at
10 %, while it is not on its charger, once per discharge cycle; charging
starts a new cycle. Each warning is a dashboard notice and a notification
(the room's "Notify when the battery is low") and the room says it itself
(its "Say when the battery is low"), except between 22:00 and 07:00 on the
household's clock (Domovoi has no quiet-hours setting): then the notice
and the notification go, the room stays quiet. Both switches are per room,
on by default, read at each warning (no restart). Pis and laptops have
neither the warnings nor the switches.

The pure rules run without a database; the watch runs against the lane's
Postgres (V040), each test on rooms of its own.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

import pytest
from sqlalchemy import text

from domovoi import room_battery as rb
from domovoi import satellite_health as sh
from domovoi.tests.conftest import requires_db


def R(pct, charging=False, on_power=False):
    return rb.Reading(pct, charging, on_power)


# ─── the pure rules ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pct", "warned", "due"),
    [
        (21, set(), None),
        (20, set(), 20),
        (15, set(), 20),
        (15, {20}, None),
        (10, {20}, 10),
        (9, {20}, 10),
        (8, set(), 10),       # a first report under both: one warning, the lower
        (8, {10}, None),
        (15, {10}, None),     # said 10 already; a jump back up says nothing
        (3, {20, 10}, None),
        (None, set(), None),
    ],
)
def test_the_thresholds_once_each(pct, warned, due):
    assert rb.warning_due(R(pct), warned) == due


def test_never_on_the_charger():
    assert rb.warning_due(R(5, charging=True), set()) is None
    assert rb.warning_due(R(5, on_power=True), set()) is None   # held on its charger
    assert rb.warning_due(rb.Reading(5, None, None), set()) == 10


def test_charging_or_a_level_back_up_ends_the_cycle():
    assert rb.cycle_ends(R(12, charging=True))
    assert rb.cycle_ends(R(12, on_power=True))
    assert rb.cycle_ends(R(26))
    assert not rb.cycle_ends(R(25))
    assert not rb.cycle_ends(R(9))


def test_the_words():
    assert rb.display_name("pako") == "Pako"
    assert rb.display_name("living_room") == "Living room"
    assert rb.display_name("pako", "Dog speaker") == "Dog speaker"
    assert rb.notice_text("Pako", 15) == "Pako is at 15%. Plug it in."
    assert rb.spoken_text("Pako") == "This is Pako. My battery is low. Please plug me in."
    assert rb.spoken_text("The very long name of the upstairs") == "My battery is low. Please plug me in."


@pytest.mark.parametrize(
    ("online", "reading", "out"),
    [
        (False, R(5), True),
        (False, R(3), True),
        (False, R(6), False),
        (False, R(4, charging=True), False),
        (False, R(4, on_power=True), False),
        (True, R(2), False),
        (False, None, False),
        (False, rb.Reading(None, False, False), False),
    ],
)
def test_probably_out_of_battery(online, reading, out):
    assert rb.probably_out_of_battery(online, reading) is out


def test_a_reading_from_the_sample():
    assert rb.reading_from({"percent": 14.6, "charging": False, "on_power": False}) == R(15)
    assert rb.reading_from({"percent": 140}) is None
    assert rb.reading_from(None) is None
    assert rb.reading_from({"charging": True}) == rb.Reading(None, True, None)


def test_the_sample_keeps_which_charger():
    kept = sh.validate_sample({"rss_kb": 1, "battery": {"percent": 40, "charging": True,
                                                        "on_power": True, "plugged": "usb"}})
    assert kept["battery"]["plugged"] == "usb"
    odd = sh.validate_sample({"rss_kb": 1, "battery": {"percent": 40, "plugged": "nuclear"}})
    assert "plugged" not in odd["battery"]


# ─── the watch, against the lane's database ───────────────────────────────


class Clock:
    def __init__(self, hour: int = 12) -> None:
        self.hour = hour

    def __call__(self) -> datetime:
        return datetime(2026, 10, 25, self.hour, 0)


class Mono:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _room() -> str:
    return "bt_" + uuid.uuid4().hex[:10]


async def _cleanup(*rooms: str) -> None:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        for r in rooms:
            await s.execute(text("DELETE FROM room_battery_warnings WHERE room_id = :r"), {"r": r})
            await s.execute(text("DELETE FROM room_battery WHERE room_id = :r"), {"r": r})
            await s.execute(text("DELETE FROM satellites WHERE room_id = :r"), {"r": r})


async def _rows(room: str) -> list[dict]:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        res = await s.execute(text(
            "SELECT threshold, percent, spoken, ended_at IS NULL AS open FROM room_battery_warnings "
            "WHERE room_id = :r ORDER BY id"), {"r": room})
        return [dict(r) for r in res.mappings().all()]


def _bat(pct, charging=False, on_power=False):
    return {"percent": pct, "charging": charging, "on_power": on_power}


@pytest.fixture(autouse=True)
def _house_quiet_hours_on(monkeypatch):
    """These tests set their own clock against the house default, 22:00-07:00."""
    from domovoi.config import settings

    monkeypatch.setattr(settings, "quiet_hours_enabled", True)
    monkeypatch.setattr(settings, "quiet_hours_start", "22:00")
    monkeypatch.setattr(settings, "quiet_hours_end", "07:00")


def _watch(clock=None, said=None, answer=True):
    async def speaker(room, words):
        if said is not None:
            said.append((room, words))
        return answer

    return rb.BatteryWatch(clock=clock or Clock(), speaker=speaker, monotonic=Mono())


async def _settle(watch: rb.BatteryWatch) -> None:
    for _ in range(500):   # up to 5 s on a busy box
        if not watch._tasks:
            return
        await asyncio.sleep(0.01)


@requires_db
@pytest.mark.asyncio
async def test_a_discharge_warns_at_twenty_then_ten_once_each():
    room = _room()
    said: list = []
    w = _watch(said=said)
    try:
        assert (await w.observe(room, "android_room", _bat(25))).warned is None
        out = await w.observe(room, "android_room", _bat(19))
        assert out.warned == 20 and out.speak == "This is " + rb.display_name(room) + ". " + rb.SPOKEN_PLAIN
        await _settle(w)
        for pct in (18, 15, 12, 11):
            assert (await w.observe(room, "android_room", _bat(pct))).warned is None
        assert (await w.observe(room, "android_room", _bat(10))).warned == 10
        for pct in (9, 6, 3):
            assert (await w.observe(room, "android_room", _bat(pct))).warned is None
        await _settle(w)
        rows = await _rows(room)
        assert [(r["threshold"], r["percent"], r["open"]) for r in rows] == [(20, 19, True), (10, 10, True)]
        assert all(r["spoken"] for r in rows)
        assert [x[0] for x in said] == [room, room]
    finally:
        await _cleanup(room)


@requires_db
@pytest.mark.asyncio
async def test_charging_resets_the_cycle():
    room = _room()
    w = _watch()
    try:
        await w.observe(room, "android_room", _bat(19))
        await w.observe(room, "android_room", _bat(9))
        out = await w.observe(room, "android_room", _bat(9, charging=True, on_power=True))
        assert out.ended == 2 and out.warned is None
        assert all(not r["open"] for r in await _rows(room))
        # Unplugged again, still low: a new cycle warns again, at the lower.
        assert (await w.observe(room, "android_room", _bat(12))).warned == 20
        assert (await w.observe(room, "android_room", _bat(10))).warned == 10
        assert [r["open"] for r in await _rows(room)] == [False, False, True, True]
    finally:
        await _cleanup(room)


@requires_db
@pytest.mark.asyncio
async def test_a_first_report_under_both_warns_once_and_a_restart_remembers():
    room = _room()
    try:
        out = await _watch().observe(room, "android_room", _bat(8))
        assert out.warned == 10
        # A new core (a restart): the open rows are the cycle, nothing repeats.
        again = _watch()
        assert (await again.observe(room, "android_room", _bat(8))).warned is None
        assert (await again.observe(room, "android_room", _bat(15))).warned is None
        assert len(await _rows(room)) == 1
    finally:
        await _cleanup(room)


@requires_db
@pytest.mark.asyncio
async def test_quiet_hours_keep_the_room_quiet_but_still_warn():
    room = _room()
    said: list = []
    clock = Clock(hour=23)
    w = _watch(clock=clock, said=said)
    try:
        out = await w.observe(room, "android_room", _bat(18))
        assert out.warned == 20 and out.quiet and out.speak is None
        clock.hour = 7
        out = await w.observe(room, "android_room", _bat(10))
        assert out.warned == 10 and not out.quiet and out.speak
        await _settle(w)
        rows = await _rows(room)
        assert [(r["threshold"], r["spoken"]) for r in rows] == [(20, False), (10, True)]
        assert len(said) == 1   # the quiet one is not said later
    finally:
        await _cleanup(room)


@requires_db
@pytest.mark.asyncio
async def test_the_say_switch_counts_at_once_and_notify_hides_the_notice():
    from domovoi.db.session import session_scope

    room = _room()
    said: list = []
    w = _watch(said=said)
    try:
        async with session_scope() as s:
            assert await rb.switches(s, room) == rb.Switches(True, True)   # no row: both on
            await rb.set_switches(s, room, say=False)
        out = await w.observe(room, "android_room", _bat(19))
        assert out.warned == 20 and out.speak is None
        async with session_scope() as s:
            assert [r["room_id"] for r in await rb.open_warnings(s, room, notify_only=True)] == [room]
            sw = await rb.set_switches(s, room, notify=False)
            assert sw == rb.Switches(say=False, notify=False)
            assert await rb.open_warnings(s, room, notify_only=True) == []
            assert len(await rb.open_warnings(s, room)) == 1   # the cycle is still kept
            await rb.set_switches(s, room, say=True)
        out = await w.observe(room, "android_room", _bat(10))
        assert out.warned == 10 and out.speak
        await _settle(w)
        assert len(said) == 1
    finally:
        await _cleanup(room)


@requires_db
@pytest.mark.asyncio
async def test_a_pi_or_a_laptop_is_never_watched():
    room = _room()
    try:
        assert await _watch().observe(room, None, _bat(5)) is None
        assert await _watch().observe(room, "linux_pc", _bat(5)) is None
        assert await _rows(room) == []
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            assert await rb.last_reports(s, room) == {}
    finally:
        await _cleanup(room)


@requires_db
@pytest.mark.asyncio
async def test_the_last_report_is_kept_for_the_offline_line():
    from domovoi.db.session import session_scope

    room = _room()
    w = _watch()
    try:
        await w.observe(room, "android_room", _bat(4))
        async with session_scope() as s:
            rep = (await rb.last_reports(s, room))[room]
        reading = rb.Reading(rep["percent"], rep["charging"], rep["on_power"])
        assert reading == R(4)
        assert rb.probably_out_of_battery(False, reading)
        # Unchanged, inside the refresh window: not rewritten.
        assert not (await w.observe(room, "android_room", _bat(4))).wrote_report
        w._mono.t += rb.REPORT_REFRESH_SEC
        assert (await w.observe(room, "android_room", _bat(4))).wrote_report
    finally:
        await _cleanup(room)


@requires_db
@pytest.mark.asyncio
async def test_a_busy_room_is_tried_again_and_a_device_without_announcements_is_not():
    room = _room()
    calls: list = []
    answers = [False, False, True]

    async def speaker(r, words):
        calls.append(words)
        return answers.pop(0) if answers else True

    w = rb.BatteryWatch(clock=Clock(), speaker=speaker, monotonic=Mono())
    orig = rb.SPEAK_RETRY_SEC
    rb.SPEAK_RETRY_SEC = 0.0
    try:
        await w.observe(room, "android_room", _bat(19))
        await _settle(w)
        assert len(calls) == 3
        assert (await _rows(room))[0]["spoken"] is True

        async def never(r, words):
            calls.append("never")
            return None

        w.speaker = never
        calls.clear()
        await w.observe(room, "android_room", _bat(10))
        await _settle(w)
        assert calls == ["never"]
        assert (await _rows(room))[1]["spoken"] is False
    finally:
        rb.SPEAK_RETRY_SEC = orig
        await _cleanup(room)


@pytest.mark.asyncio
async def test_the_speaker_uses_the_rooms_own_session():
    from types import SimpleNamespace

    from domovoi.timer_delivery import AnnounceNotStarted

    seen: list = []

    class Sess:
        def __init__(self, exc=None):
            self.exc = exc

        async def announce(self, words, *, defer_to_capture=False, kind=None):
            seen.append((words, defer_to_capture, kind))
            if self.exc:
                raise self.exc

    app = SimpleNamespace(state=SimpleNamespace(active_sessions={"pako": Sess()}))
    speak = rb.session_speaker(app)
    assert await speak("pako", "hi") is True
    assert seen == [("hi", True, "notice")]
    assert await speak("gone", "hi") is False
    app.state.active_sessions["pako"] = Sess(AnnounceNotStarted("busy", reason="responding"))
    assert await speak("pako", "hi") is False
    app.state.active_sessions["pako"] = Sess(AnnounceNotStarted("no", reason="unsupported"))
    assert await speak("pako", "hi") is None
    app.state.active_sessions["pako"] = Sess(AnnounceNotStarted("shh", reason="quiet_hours"))
    assert await speak("pako", "hi") is None   # not retried: never said later


@pytest.mark.asyncio
async def test_a_missing_table_costs_a_log_line_not_the_session(caplog):
    class Boom:
        async def __aenter__(self):
            raise RuntimeError('relation "room_battery" does not exist')

        async def __aexit__(self, *a):
            return False

    w = rb.BatteryWatch(scope=Boom, clock=Clock(), speaker=None, monotonic=Mono())
    assert await w.observe("pako", "android_room", _bat(10)) is None
    assert await w.observe("pako", "android_room", _bat(9)) is None
    assert sum("V040" in r.message for r in caplog.records) == 1


# ─── the stream hands every phone report to the watch ─────────────────────


@pytest.mark.asyncio
async def test_the_health_frame_reaches_the_watch(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from domovoi import streaming
    from domovoi.streaming import StreamSession

    @asynccontextmanager
    async def no_v022():
        raise RuntimeError('relation "satellite_health" does not exist')
        yield  # pragma: no cover

    monkeypatch.setattr(streaming, "session_scope", no_v022)
    seen: list = []

    class Watch:
        async def observe(self, room, board, battery):
            seen.append((room, board, battery))

    class WS:
        app = SimpleNamespace(state=SimpleNamespace(active_sessions={}, room_battery=Watch()))

        async def send_text(self, t):  # pragma: no cover - never answered
            raise AssertionError(t)

    session = StreamSession(WS(), "pako")
    session.token_authenticated = True
    session.board = "android_room"
    await session._on_control({"type": "health", "seq": 1, "rss_kb": 1,
                               "battery": {"percent": 15, "charging": False, "on_power": False}})
    await session._on_control({"type": "health", "seq": 2, "rss_kb": 1})   # no battery: not asked
    assert seen == [("pako", "android_room", {"percent": 15, "charging": False, "on_power": False})]
