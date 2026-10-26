"""Quiet hours (domovoi/quiet_hours.py, owner request 2026-10-25).

A house-wide setting (on, 22:00-07:00, editable) and a per-room choice
(the house's, custom hours, or off). Inside them a room speaks no
unprompted announcement: low battery, printer failures, a letter or
document that is ready, plugins (the SDK). The gate is ONE place,
StreamSession.announce, and a notice is its default kind.

NEVER held back, pinned here so nobody breaks an alarm: timers, alarms and
reminders (the timer delivery), answers to a question (replies never pass
the gate), and what somebody asked to be said now (intercom / "say this in
the kitchen", the dashboard's Broadcast, a voice they picked).

Pure tests plus the lane's Postgres (V040) for the stored choice.
"""

from __future__ import annotations

import ast
import inspect
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from domovoi import quiet_hours as qh
from domovoi.tests.conftest import requires_db

REPO = Path(__file__).resolve().parents[2]


def at(h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, 25, h, m)


HOUSE = SimpleNamespace(quiet_hours_enabled=True, quiet_hours_start="22:00", quiet_hours_end="07:00")


# ─── the rules ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(("when", "start", "end", "quiet"), [
    (at(21, 59), "22:00", "07:00", False), (at(22, 0), "22:00", "07:00", True),
    (at(3), "22:00", "07:00", True), (at(6, 59), "22:00", "07:00", True), (at(7, 0), "22:00", "07:00", False),
    (at(13, 30), "13:00", "15:00", True), (at(15, 0), "13:00", "15:00", False), (at(12), "13:00", "15:00", False),
    (at(9), "09:00", "09:00", False),
])
def test_the_window_crosses_midnight_or_not(when, start, end, quiet):
    assert qh.in_window(when, start, end) is quiet


def test_clock_times():
    assert qh.normalize_clock("7:05") == "07:05"
    assert qh.normalize_clock(" 22:00 ") == "22:00"
    for bad in ("24:00", "7", "07:60", "seven", None, 7):
        assert qh.normalize_clock(bad) is None


def test_each_rooms_choice():
    assert qh.hours_for(qh.RoomChoice(), HOUSE) == ("22:00", "07:00")
    assert qh.hours_for(qh.RoomChoice("custom", "21:30", "06:15"), HOUSE) == ("21:30", "06:15")
    assert qh.hours_for(qh.RoomChoice("off"), HOUSE) is None
    off_house = SimpleNamespace(**{**vars(HOUSE), "quiet_hours_enabled": False})
    assert qh.hours_for(qh.RoomChoice(), off_house) is None
    # A room's own hours stand even with the house's off.
    assert qh.hours_for(qh.RoomChoice("custom", "21:30", "06:15"), off_house) == ("21:30", "06:15")


def test_the_house_setting_defaults_and_bad_values():
    from domovoi.config import Settings

    s = Settings(_env_file=None)
    assert (s.quiet_hours_enabled, s.quiet_hours_start, s.quiet_hours_end) == (True, "22:00", "07:00")
    s = Settings(_env_file=None, quiet_hours_start="9:30", quiet_hours_end="bedtime")
    assert (s.quiet_hours_start, s.quiet_hours_end) == ("09:30", "07:00")


def test_the_house_setting_is_in_settings_and_applies_live():
    from domovoi.config_schema import EDITABLE_FIELDS as FIELD_SPECS

    specs = {f.name: f for f in FIELD_SPECS}
    for name in ("quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end"):
        assert specs[name].group == "Voice & speech" and specs[name].tier == "hot"
    assert "Alarms, timers and reminders still ring" in specs["quiet_hours_enabled"].help


@pytest.mark.asyncio
async def test_the_gate_holds_back_notices_only(monkeypatch):
    async def always_quiet(room_id, **kw):
        return True

    monkeypatch.setattr(qh, "is_quiet", always_quiet)
    assert await qh.holds_back("kitchen", "notice")
    assert await qh.holds_back("kitchen", "anything new")   # unknown = notice
    assert not await qh.holds_back("kitchen", "alarm")
    assert not await qh.holds_back("kitchen", "user")


# ─── StreamSession.announce: the one gate ────────────────────────────────


def _session(monkeypatch, quiet: bool):
    """A room whose speech is recorded at the TTS (which then gives up, so
    nothing needs a socket): what reaches it was not held back."""
    from domovoi import streaming
    from domovoi.streaming import StreamSession

    async def is_quiet(room_id, **kw):
        return quiet

    monkeypatch.setattr(qh, "is_quiet", is_quiet)
    spoken: list = []

    class TTS:
        async def synthesize(self, text_, **kw):
            spoken.append(text_)
            raise RuntimeError("stop here")

    async def resolve_voice(name):
        return "piper", None

    monkeypatch.setattr(streaming, "get_tts_client", lambda: TTS())
    monkeypatch.setattr(streaming, "resolve_voice", resolve_voice)
    ws = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(active_sessions={}, satellite_voice={})))
    sess = StreamSession(ws, "kitchen")
    return sess, spoken


async def _say(sess, text_, **kw) -> str | None:
    """The AnnounceNotStarted reason (tts_failed = it got to the voice)."""
    from domovoi.timer_delivery import AnnounceNotStarted

    try:
        await sess.announce(text_, **kw)
    except AnnounceNotStarted as e:
        return e.reason
    return None


@pytest.mark.asyncio
async def test_a_notice_is_held_back_in_quiet_hours(monkeypatch):
    sess, spoken = _session(monkeypatch, quiet=True)
    assert await _say(sess, "Printing that didn't work.") == "quiet_hours"
    assert spoken == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["alarm", "user"])
async def test_alarms_and_asked_for_speech_ring_in_quiet_hours(monkeypatch, kind):
    sess, spoken = _session(monkeypatch, quiet=True)
    assert await _say(sess, "Your pasta timer is done.", kind=kind) == "tts_failed"
    assert spoken == ["Your pasta timer is done."]


@pytest.mark.asyncio
async def test_outside_quiet_hours_a_notice_is_spoken(monkeypatch):
    sess, spoken = _session(monkeypatch, quiet=False)
    await _say(sess, "Pako's battery is low.")
    assert spoken == ["Pako's battery is low."]


@pytest.mark.asyncio
async def test_a_plugin_cannot_get_past_it(monkeypatch):
    """The SDK passes no kind of the plugin's choosing: always a notice."""
    from domovoi.sdk import speech

    sess, spoken = _session(monkeypatch, quiet=True)
    monkeypatch.setattr(speech, "_active_sessions_provider", lambda: {"kitchen": sess})
    assert await speech.SpeechAPI("someplugin").announce("kitchen", "Your download finished.") == []
    assert spoken == []
    assert list(inspect.signature(speech.SpeechAPI.announce).parameters) == ["self", "room_id", "text"]


@pytest.mark.asyncio
async def test_a_printer_failure_is_held_back(monkeypatch):
    from domovoi.printing import jobs
    from domovoi.sdk import speech

    sess, spoken = _session(monkeypatch, quiet=True)
    monkeypatch.setattr(speech, "_active_sessions_provider", lambda: {"kitchen": sess})
    job = {"id": 5, "source": "voice", "announced": False, "room_id": "kitchen", "created_at": None,
           "error": "The printer is out of paper.", "title": "x"}

    async def get_job(i):
        return dict(job)

    async def update_job(i, **kw):
        job.update(kw)

    monkeypatch.setattr(jobs.store, "get_job", get_job)
    monkeypatch.setattr(jobs.store, "update_job", update_job)
    await jobs.announce_failure(5)
    assert spoken == [] and job["announced"] is True   # not said later either


@pytest.mark.asyncio
async def test_a_letter_ready_line_is_held_back(monkeypatch):
    from domovoi.handlers import documents
    from domovoi.sdk import speech

    sess, spoken = _session(monkeypatch, quiet=True)
    monkeypatch.setattr(speech, "_active_sessions_provider", lambda: {"kitchen": sess})

    class Saved:
        rel_path, title = "Documents/From Chat/x.docx", "Letter to the landlord"

    async def write_and_save(state):
        return Saved()

    monkeypatch.setattr(documents.letters, "write_and_save", write_and_save)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def scope():
        yield SimpleNamespace()

    monkeypatch.setattr("domovoi.db.session.session_scope", scope)

    class Repo:
        def __init__(self, s):
            pass

        async def set_context_key(self, *a):
            return None

    monkeypatch.setattr(documents, "SessionRepository", Repo)
    await documents._write_up_later({}, 1, "kitchen")
    assert spoken == []


# ─── never held back: pinned at each caller ──────────────────────────────


def _announce_kinds(path: str) -> list[str | None]:
    """The ``kind=`` of every ``….announce(…)`` call in a module (None: the
    default, a notice)."""
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    out: list[str | None] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "announce":
            kw = {k.arg: k.value for k in node.keywords}
            v = kw.get("kind")
            out.append(v.value if isinstance(v, ast.Constant) else None)
    return out


def test_timers_alarms_and_reminders_are_never_held_back():
    kinds = _announce_kinds("domovoi/timer_delivery.py")
    assert kinds and all(k == "alarm" for k in kinds), kinds
    # The timer delivery's own waits (announce_block) know nothing of quiet hours.
    from domovoi.streaming import StreamSession

    assert "quiet" not in inspect.getsource(StreamSession.announce_block)


def test_intercom_and_say_this_in_the_kitchen_are_never_held_back():
    # say_in_room's reply carries announce_to_rooms; both fan-outs say it as "user".
    assert _announce_kinds("domovoi/phone_voice.py") == ["user"]
    src = (REPO / "domovoi/streaming.py").read_text(encoding="utf-8")
    assert "await target.announce(response.announce_text, kind=\"user\")" in src
    # The intercom handler ("tell the kitchen dinner's ready") is what
    # carries a room's words to another; room_commands.say_in_room words
    # the asking room's own reply, which is an answer (never announced).
    from domovoi.handlers import intercom, room_commands

    assert "announce_to_rooms" in inspect.getsource(intercom)
    assert room_commands.say_in_room("Paused.", "the garage") == "Paused the music in the garage."


def test_the_dashboards_broadcast_and_a_picked_voice_are_never_held_back():
    from domovoi import main

    assert "await sess.announce(body.message, kind=\"user\")" in inspect.getsource(main.admin_announce)
    assert set(_announce_kinds("domovoi/room_voice.py")) == {"user"}


def test_answers_never_pass_the_gate():
    """A reply is streamed by the turn, not announced: only announce()
    consults quiet hours."""
    from domovoi import streaming

    src = (REPO / "domovoi/streaming.py").read_text(encoding="utf-8")
    assert src.count("quiet_hours.holds_back(") == 1
    assert "quiet_hours.holds_back(" in inspect.getsource(streaming.StreamSession._announce)


def test_the_unprompted_ones_are_notices():
    for path in ("domovoi/printing/jobs.py", "domovoi/handlers/documents.py", "domovoi/handlers/library.py",
                 "domovoi/handlers/voice.py", "domovoi/room_battery.py", "domovoi/sdk/speech.py"):
        kinds = _announce_kinds(path)
        assert kinds and all(k in (None, "notice") for k in kinds), (path, kinds)


# ─── the stored choice (V040) ────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_a_rooms_choice_is_stored_and_read_at_once():
    from domovoi.db.session import session_scope

    room = "qh_" + uuid.uuid4().hex[:10]
    try:
        async with session_scope() as s:
            assert await qh.room_choice(s, room) == qh.RoomChoice()
            await qh.set_room_choice(s, room, qh.RoomChoice("custom", "9:00", "11:30"))
        assert await qh.is_quiet(room, now=at(10), settings=HOUSE)
        assert not await qh.is_quiet(room, now=at(23), settings=HOUSE)
        async with session_scope() as s:
            await qh.set_room_choice(s, room, qh.RoomChoice("off"))
        assert not await qh.is_quiet(room, now=at(23), settings=HOUSE)
        async with session_scope() as s:
            back = await qh.set_room_choice(s, room, qh.RoomChoice("house", "1:00", "2:00"))
            assert back == qh.RoomChoice("house", None, None)
        assert await qh.is_quiet(room, now=at(23), settings=HOUSE)
        async with session_scope() as s:
            with pytest.raises(ValueError):
                await qh.set_room_choice(s, room, qh.RoomChoice("custom", "9:00", None))
            with pytest.raises(ValueError):
                await qh.set_room_choice(s, room, qh.RoomChoice("loud"))
    finally:
        async with session_scope() as s:
            await s.execute(text("DELETE FROM satellites WHERE room_id = :r"), {"r": room})


@pytest.mark.asyncio
async def test_an_unreadable_choice_is_the_houses():
    class Boom:
        async def __aenter__(self):
            raise RuntimeError('column "quiet_mode" does not exist')

        async def __aexit__(self, *a):
            return False

    assert await qh.is_quiet("kitchen", now=at(23), settings=HOUSE, scope=Boom)
    assert not await qh.is_quiet("kitchen", now=at(12), settings=HOUSE, scope=Boom)
