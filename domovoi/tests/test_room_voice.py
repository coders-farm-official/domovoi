"""A room's voice when its device keeps none (domovoi/room_voice.py, V031).

Owner report 2026-10-17: "switch to Ryan" worked in a Pi's room and was
refused in a phone's (room mode): the phone's hello declares `supports`
without "voice", so the voice handler said "This room's device can't change
its voice." The server renders every room's speech anyway, so it now keeps
that room's choice itself: stored in `satellites.voice`, restored when the
device connects, said in `ready` / `sounds_changed` as `room_voice` so the
device mirrors that voice's greeting clips. A Pi is untouched: it keeps its
own voice, gets `pi_action: set_voice`, and its `voice_status` is what the
server renders in.

DB-free except the last section (the real SQL, `requires_db`).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from domovoi import room_voice, streaming
from domovoi.db import repositories
from domovoi.handlers import voice as voice_handler
from domovoi.handlers.voice import VoiceHandler
from domovoi.models import Context
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import requires_db

REPO = Path(__file__).resolve().parents[2]
FIXTURES = json.loads((REPO / "docs" / "uml" / "satellite-fixtures.json").read_text(encoding="utf-8"))
DEVICES: dict[str, dict[str, Any]] = FIXTURES["devices"]
PHONE = DEVICES["android_room"]["hello"]
PI = DEVICES["pi_2mic_hat"]["hello"]


def _state() -> SimpleNamespace:
    return SimpleNamespace(
        active_sessions={}, active_dropins={},
        satellite_full_duplex={}, satellite_synced_sha={}, satellite_sat_type={},
        satellite_mic_enabled={}, wifi_status={}, satellite_volume={}, satellite_voice={},
        satellite_config={}, satellite_display={},
    )


class FakeWS:
    def __init__(self, frames: list[dict[str, Any]] | None = None, state: Any = None) -> None:
        self.app = SimpleNamespace(state=state or _state())
        self._in = list(frames or [])
        self.sent: list[dict[str, Any]] = []
        self.closed: int | None = None

    async def accept(self) -> None:
        return None

    async def receive(self) -> dict[str, Any]:
        if self._in:
            return {"type": "websocket.receive", "text": json.dumps(self._in.pop(0))}
        return {"type": "websocket.disconnect"}

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self, code: int = 1000) -> None:
        self.closed = code


@pytest.fixture
def paired(monkeypatch):
    async def _accept(self, ctrl):
        self.token_authenticated = True
        return True

    monkeypatch.setattr(StreamSession, "_validate_pairing", _accept)


@pytest.fixture
def kept(monkeypatch):
    """The `satellites` table as a dict: room -> the voice the server keeps.
    Records which rooms were read."""
    voices: dict[str, str | None] = {}
    reads: list[str] = []

    class Repo:
        def __init__(self, _s):
            pass

        async def upsert_type(self, room, sat_type):
            pass

        async def upsert_board(self, room, board):
            pass

        async def room_voice(self, room):
            reads.append(room)
            return voices.get(room)

        async def set_room_voice(self, room, voice):
            voices[room] = voice

    @asynccontextmanager
    async def scope():
        yield None

    monkeypatch.setattr(streaming, "session_scope", scope)
    monkeypatch.setattr(streaming, "SatellitesRepository", Repo)
    monkeypatch.setattr(repositories, "SatellitesRepository", Repo)
    return SimpleNamespace(voices=voices, reads=reads)


async def _run(hello: dict[str, Any], state: Any = None) -> tuple[StreamSession, FakeWS, dict]:
    """A whole connection: hello, `ready`, then the device goes away. Returns
    the `ready` and the caches as they were when it went out."""
    ws = FakeWS([dict(hello)], state=state)
    session = StreamSession(ws, hello["room_id"])  # type: ignore[arg-type]
    seen: dict[str, Any] = {}
    real_send = ws.send_text

    async def spy(text):
        frame = json.loads(text)
        if frame.get("type") == "ready":
            seen["voice"] = ws.app.state.satellite_voice.get(hello["room_id"])
        await real_send(text)

    ws.send_text = spy  # type: ignore[method-assign]
    await session.run()
    return session, ws, seen


async def _hello(hello: dict[str, Any], state: Any = None) -> tuple[StreamSession, FakeWS]:
    """A live session (registered, as after `ready`)."""
    ws = FakeWS(state=state)
    session = StreamSession(ws, hello["room_id"])  # type: ignore[arg-type]
    await session._on_control(dict(hello))
    ws.app.state.active_sessions[hello["room_id"]] = session
    if not session.abilities.allows("voice"):
        await session._restore_room_voice()
    ws.sent.clear()
    return session, ws


def _ctx(session: StreamSession) -> Context:
    return Context(session_id=uuid4(), room_id=session.room_id, online=True, app=session.ws.app)


@pytest.fixture
def registry(monkeypatch):
    """The voices registry: Amy (default), Ryan."""
    rows = {"amy": {"name": "Amy"}, "ryan": {"name": "Ryan"}}

    class Voices:
        def __init__(self, _s):
            pass

        async def get_by_name(self, name):
            return rows.get(name.lower())

        async def all(self):
            return list(rows.values())

        async def get_default(self):
            return rows["amy"]

    monkeypatch.setattr(voice_handler, "VoicesRepository", Voices)


# ─── connecting ───────────────────────────────────────────────────────────


async def test_a_phone_room_speaks_in_the_voice_the_server_keeps_for_it(paired, kept):
    kept.voices[PHONE["room_id"]] = "Ryan"
    session, ws, seen = await _run(PHONE)
    ready = ws.sent[0]
    assert ready["type"] == "ready" and ready["room_voice"] == "Ryan"
    assert seen["voice"] == "Ryan", "restored before `ready`, where every synthesis reads it"
    # The disconnect clears it like any room's (restored on the next connect).
    assert PHONE["room_id"] not in ws.app.state.satellite_voice


async def test_a_phone_room_without_a_kept_voice_is_told_null(paired, kept):
    _, ws, seen = await _run(PHONE)
    assert ws.sent[0]["room_voice"] is None and seen["voice"] is None


async def test_a_pi_is_never_read_for_a_kept_voice_and_its_ready_is_unchanged(paired, kept):
    kept.voices[PI["room_id"]] = "Ryan"  # left by a phone that used to be this room
    _, ws, seen = await _run(PI)
    assert "room_voice" not in ws.sent[0]
    assert kept.reads == [] and seen["voice"] is None


async def test_an_unreadable_kept_voice_speaks_in_the_servers_voice(paired, kept, monkeypatch, caplog):
    async def broken(self, room):
        raise RuntimeError("db down")

    monkeypatch.setattr(streaming.SatellitesRepository, "room_voice", broken)
    _, ws, _ = await _run(PHONE)
    assert ws.sent[0]["type"] == "ready" and ws.sent[0]["room_voice"] is None
    assert "could not read" in caplog.text


async def test_a_kept_voice_the_registry_no_longer_has_is_not_said(paired, kept, monkeypatch):
    """Renamed or removed: no clips to mirror (the phone would prune its
    greetings), and the room speaks in the server's voice anyway."""

    class Voices:
        def __init__(self, _s):
            pass

        async def get_by_name(self, name):
            return {"name": "Kristin"} if name.lower() == "kristin" else None

    monkeypatch.setattr(repositories, "VoicesRepository", Voices)
    kept.voices[PHONE["room_id"]] = "Ryan"
    _, ws, seen = await _run(PHONE)
    assert ws.sent[0]["room_voice"] is None and seen["voice"] is None
    kept.voices[PHONE["room_id"]] = "kristin"
    phone, pws = await _hello(PHONE)
    assert phone.ws.app.state.satellite_voice[PHONE["room_id"]] == "Kristin", "the registry's spelling"
    phone.ws.app.state.satellite_voice[PHONE["room_id"]] = "Ryan"  # removed while connected
    await phone.notify_sounds_changed()
    assert pws.sent == [{"type": "sounds_changed", "room_voice": None}]


async def test_voice_status_from_a_phone_room_is_ignored_and_from_a_pi_cached(paired, kept):
    state = _state()
    kept.voices[PHONE["room_id"]] = "Ryan"
    phone, _ = await _hello(PHONE, state)
    await phone._on_control({"type": "voice_status", "voice": "Amy"})
    await phone._on_control({"type": "voice_status", "voice": None})
    assert state.satellite_voice[PHONE["room_id"]] == "Ryan"
    pi, _ = await _hello(PI, state)
    await pi._on_control({"type": "voice_status", "voice": "Amy"})
    assert state.satellite_voice[PI["room_id"]] == "Amy"


async def test_sounds_changed_names_the_voice_only_for_a_room_whose_voice_the_server_keeps(paired, kept):
    kept.voices[PHONE["room_id"]] = "Ryan"
    phone, pws = await _hello(PHONE)
    await phone.notify_sounds_changed()
    assert pws.sent == [{"type": "sounds_changed", "room_voice": "Ryan"}]
    pi, ws = await _hello(PI)
    await pi.notify_sounds_changed()
    assert ws.sent == [{"type": "sounds_changed"}], "a Pi's frame is exactly as before"


# ─── "switch to Ryan" ─────────────────────────────────────────────────────


async def test_switching_a_phone_rooms_voice_keeps_it_on_the_server(paired, kept, registry):
    phone, ws = await _hello(PHONE)
    resp = await VoiceHandler()._switch_response("ryan", _ctx(phone), object())  # type: ignore[arg-type]
    assert resp.text == "Okay, I'm Ryan now."
    assert resp.voice_override == "Ryan", "the confirmation is already in the new voice"
    assert resp.pi_action is None and resp.pi_action_arg is None, "nothing for the phone to do"
    assert kept.voices[PHONE["room_id"]] == "Ryan"
    assert ws.app.state.satellite_voice[PHONE["room_id"]] == "Ryan"
    assert ws.sent == [{"type": "sounds_changed", "room_voice": "Ryan"}], "it re-mirrors Ryan's clips"
    # The next turn's context reports it ("what voice are you using").
    current = await VoiceHandler()._current_response(
        Context(session_id=uuid4(), room_id=phone.room_id, online=True, app=ws.app, voice="Ryan"),
        object(),  # type: ignore[arg-type]
    )
    assert current.text == "I'm using Ryan right now."


async def test_switching_a_pis_voice_still_goes_to_the_pi(paired, kept, registry):
    pi, ws = await _hello(PI)
    resp = await VoiceHandler()._switch_response("ryan", _ctx(pi), object())  # type: ignore[arg-type]
    assert (resp.pi_action, resp.pi_action_arg, resp.voice_override) == ("set_voice", "Ryan", "Ryan")
    assert kept.voices == {} and ws.sent == []
    assert pi.room_id not in ws.app.state.satellite_voice, "the Pi reports it via voice_status"


async def test_an_unknown_voice_changes_nothing_in_a_phone_room(paired, kept, registry):
    phone, ws = await _hello(PHONE)
    resp = await VoiceHandler()._switch_response("zelda", _ctx(phone), object())  # type: ignore[arg-type]
    assert resp.text.startswith("I don't have a voice called zelda.")
    assert kept.voices == {} and ws.sent == [] and ws.app.state.satellite_voice == {}


def test_a_room_with_no_live_device_is_not_kept_by_the_server():
    app = SimpleNamespace(state=SimpleNamespace(active_sessions={}))
    assert room_voice.kept_by_server(app, "kitchen") is False
    assert room_voice.kept_by_server(None, "kitchen") is False


# ─── the real SQL (V031) ──────────────────────────────────────────────────


@pytest.fixture
async def _clean_satellites():
    from domovoi.db.session import engine

    async def wipe():
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE satellite_approvals, satellite_pairings, satellites CASCADE"))

    await wipe()
    yield
    await wipe()


@requires_db
async def test_the_kept_voice_is_stored_upserted_and_cleared(_clean_satellites):
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        repo = repositories.SatellitesRepository(s)
        assert await repo.room_voice("nursery") is None
        await repo.set_room_voice("nursery", "Ryan")  # no row yet: created
    async with session_scope() as s:
        repo = repositories.SatellitesRepository(s)
        assert await repo.room_voice("nursery") == "Ryan"
        await repo.upsert_board("nursery", "android_room")
        await repo.set_room_voice("nursery", "Amy")
    async with session_scope() as s:
        repo = repositories.SatellitesRepository(s)
        row = await repo.get("nursery")
        assert row["board"] == "android_room" and row["adopted_via"] == "hello"
        assert await repo.room_voice("nursery") == "Amy"
        await repo.set_room_voice("nursery", None)
    async with session_scope() as s:
        assert await repositories.SatellitesRepository(s).room_voice("nursery") is None


@requires_db
async def test_a_phone_room_reconnects_in_the_voice_it_was_switched_to(_clean_satellites, paired):
    from domovoi.db.session import engine, session_scope

    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM voices WHERE name = 'RoomVoiceTest'"))
        await conn.execute(text(
            "INSERT INTO voices (name, engine, model_ref) VALUES ('RoomVoiceTest', 'piper', 'x.onnx')"))
    try:
        async with session_scope() as s:
            await repositories.SatellitesRepository(s).set_room_voice(PHONE["room_id"], "RoomVoiceTest")
        _, ws, seen = await _run(PHONE)
        assert ws.sent[0]["room_voice"] == "RoomVoiceTest" and seen["voice"] == "RoomVoiceTest"
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM voices WHERE name = 'RoomVoiceTest'"))
