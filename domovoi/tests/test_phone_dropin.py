"""Tests for the phone drop-in endpoint (domovoi/phone_dropin.py).

Covers the phone-side feasibility helper and the PhoneDropinSession
lifecycle against a real StreamSession peer: pairing through the shared
_begin_dropin, both relay directions, initiator-flag correction, hang-up
from either side, and registry cleanup. Mirrors the doubles used in
test_dropin.py — no sockets, no DB (the audit row is best-effort).
"""

from __future__ import annotations

import array
import asyncio
import json
from types import SimpleNamespace

import pytest

from domovoi.config import settings
from domovoi.dropin_common import OK
from domovoi.phone_dropin import PhoneDropinSession, phone_dropin_feasibility
from domovoi.streaming import StreamSession


def make_app(*, active=None, fd=None, dropins=None):
    return SimpleNamespace(
        state=SimpleNamespace(
            active_sessions=active if active is not None else {},
            satellite_full_duplex=fd if fd is not None else {},
            active_dropins=dropins if dropins is not None else {},
            dropin_lock=asyncio.Lock(),
            resumable_music={},
            pending_music_start={},
            satellite_voice={},
            satellite_volume={},
        )
    )


class FakeWS:
    """Minimal WebSocket capturing sends; feeds scripted receive() frames."""

    def __init__(self, app):
        self.app = app
        self.sent_bytes: list[bytes] = []
        self.sent_text: list[str] = []
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.accepted = False
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def receive(self):
        return await self.inbox.get()

    async def send_bytes(self, b):
        self.sent_bytes.append(b)

    async def send_text(self, t):
        self.sent_text.append(t)

    async def close(self):
        self.closed = True

    # scripting helpers
    def feed_audio(self, data: bytes):
        self.inbox.put_nowait({"type": "websocket.receive", "bytes": data})

    def feed_text(self, payload: dict):
        self.inbox.put_nowait(
            {"type": "websocket.receive", "text": json.dumps(payload)}
        )

    def feed_disconnect(self):
        self.inbox.put_nowait({"type": "websocket.disconnect"})


LOUD = array.array("h", [12000] * 32).tobytes()


# ─── phone_dropin_feasibility ────────────────────────────────────────────────


def test_phone_feasibility_ok():
    app = make_app(active={"kitchen": 1}, fd={"kitchen": True})
    assert phone_dropin_feasibility(app, "phone-x", "kitchen") == OK


def test_phone_feasibility_disabled(monkeypatch):
    monkeypatch.setattr(settings, "dropin_enabled", False, raising=False)
    app = make_app(active={"kitchen": 1}, fd={"kitchen": True})
    assert phone_dropin_feasibility(app, "phone-x", "kitchen") == "disabled"


def test_phone_feasibility_target_offline():
    app = make_app(fd={"kitchen": True})
    assert phone_dropin_feasibility(app, "phone-x", "kitchen") == "target_offline"


def test_phone_feasibility_target_no_aec():
    app = make_app(active={"kitchen": 1}, fd={"kitchen": False})
    assert phone_dropin_feasibility(app, "phone-x", "kitchen") == "target_no_aec"


def test_phone_feasibility_target_busy():
    app = make_app(
        active={"kitchen": 1},
        fd={"kitchen": True},
        dropins={"kitchen": {"peer": "office"}},
    )
    assert phone_dropin_feasibility(app, "phone-x", "kitchen") == "target_busy"


def test_phone_feasibility_initiator_busy():
    app = make_app(
        active={"kitchen": 1, "office": 1},
        fd={"kitchen": True, "office": True},
        dropins={"phone-x": {"peer": "office"}},
    )
    assert phone_dropin_feasibility(app, "phone-x", "kitchen") == "initiator_busy"


# ─── Session lifecycle ───────────────────────────────────────────────────────


@pytest.fixture
def quiet_dropin(monkeypatch):
    """No watchdog task, no relay gate — tests drive tiny frames."""
    monkeypatch.setattr(settings, "dropin_silence_timeout_sec", 0.0, raising=False)
    monkeypatch.setattr(settings, "dropin_relay_gate_dbfs", -120.0, raising=False)


async def _run_phone(app, target_room="kitchen", phone_id="phone-test"):
    """Start a PhoneDropinSession.run() as a task; return (phone, task)."""
    phone = PhoneDropinSession(
        FakeWS(app), phone_id=phone_id, target_room=target_room
    )
    task = asyncio.create_task(phone.run())
    # Let the handshake (accept → feasibility → _begin_dropin → dropin_start
    # frames) complete. dropin_peer is set under the lock before the frames
    # go out, so wait for the start frame (or closure on refusal).
    for _ in range(100):
        await asyncio.sleep(0.01)
        if phone.ws.closed or any('"dropin_start"' in t for t in phone.ws.sent_text):
            break
    return phone, task


async def test_phone_call_full_lifecycle(quiet_dropin):
    app = make_app(fd={"kitchen": True})
    pi = StreamSession(FakeWS(app), "kitchen")
    app.state.active_sessions["kitchen"] = pi

    phone, task = await _run_phone(app)

    # Paired both ways through the shared bridge.
    assert phone.dropin_peer is pi and pi.dropin_peer is phone

    # Phone asserted AEC for the duration.
    assert app.state.satellite_full_duplex["phone-test"] is True

    # Both ends got dropin_start; the phone's frame names the room and
    # carries the forced 16 kHz rate + full_duplex true.
    starts = [json.loads(t) for t in phone.ws.sent_text if '"dropin_start"' in t]
    assert starts and starts[0]["peer_room"] == "kitchen"
    assert starts[0]["audio_sample_rate"] == 16000
    assert starts[0]["full_duplex"] is True
    assert any('"dropin_start"' in t for t in pi.ws.sent_text)

    # The phone placed the call — initiator flags read true after the flip.
    assert app.state.active_dropins["phone-test"]["initiator"] is True
    assert app.state.active_dropins["kitchen"]["initiator"] is False

    # Phone → Pi relay.
    phone.ws.feed_audio(LOUD)
    await asyncio.sleep(0.01)
    assert pi.ws.sent_bytes[-1] == LOUD

    # Pi → phone relay via the existing StreamSession path.
    await pi._on_audio(b"\x05\x06\x07\x08")
    assert phone.ws.sent_bytes[-1] == b"\x05\x06\x07\x08"

    # Phone hangs up: both sides cleared, registries cleaned, socket closed.
    phone.ws.feed_text({"type": "dropin_end"})
    await asyncio.wait_for(task, timeout=2)
    assert phone.dropin_peer is None and pi.dropin_peer is None
    assert "phone-test" not in app.state.active_dropins
    assert "kitchen" not in app.state.active_dropins
    assert "phone-test" not in app.state.satellite_full_duplex
    assert any('"dropin_end"' in t for t in pi.ws.sent_text)
    assert any('"dropin_end"' in t for t in phone.ws.sent_text)
    assert phone.ws.closed


async def test_phone_call_target_offline(quiet_dropin):
    app = make_app()
    phone, task = await _run_phone(app, target_room="kitchen")
    await asyncio.wait_for(task, timeout=2)
    errs = [json.loads(t) for t in phone.ws.sent_text if '"error"' in t]
    assert errs and errs[0]["code"] == "target_offline"
    assert phone.ws.closed
    assert "phone-test" not in app.state.satellite_full_duplex


async def test_phone_call_pi_side_hangup_ends_loop(quiet_dropin):
    app = make_app(fd={"kitchen": True})
    pi = StreamSession(FakeWS(app), "kitchen")
    app.state.active_sessions["kitchen"] = pi

    phone, task = await _run_phone(app)
    assert phone.dropin_peer is pi

    # Pi (or admin) ends the call; the phone's next inbound frame exits
    # the pump and the socket closes.
    await pi._end_dropin(ended_by="kitchen")
    phone.ws.feed_audio(LOUD)
    await asyncio.wait_for(task, timeout=2)
    assert phone.dropin_peer is None
    assert phone.ws.closed
    assert "phone-test" not in app.state.active_dropins


async def test_phone_disconnect_tears_down_call(quiet_dropin):
    app = make_app(fd={"kitchen": True})
    pi = StreamSession(FakeWS(app), "kitchen")
    app.state.active_sessions["kitchen"] = pi

    phone, task = await _run_phone(app)
    assert pi.dropin_peer is phone

    phone.ws.feed_disconnect()
    await asyncio.wait_for(task, timeout=2)
    assert pi.dropin_peer is None
    assert "kitchen" not in app.state.active_dropins
    assert "phone-test" not in app.state.satellite_full_duplex


async def test_phone_relay_failure_tears_down(quiet_dropin):
    app = make_app(fd={"kitchen": True})
    pi = StreamSession(FakeWS(app), "kitchen")
    app.state.active_sessions["kitchen"] = pi

    phone, task = await _run_phone(app)

    async def boom(_):
        raise ConnectionError("pi gone")

    pi.ws.send_bytes = boom
    phone.ws.feed_audio(LOUD)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if phone.dropin_peer is None:
            break
    assert phone.dropin_peer is None and pi.dropin_peer is None
    # Loop exits on the next frame once unpaired.
    phone.ws.feed_audio(LOUD)
    await asyncio.wait_for(task, timeout=2)


async def test_second_phone_refused_while_room_busy(quiet_dropin):
    app = make_app(fd={"kitchen": True})
    pi = StreamSession(FakeWS(app), "kitchen")
    app.state.active_sessions["kitchen"] = pi

    phone1, task1 = await _run_phone(app, phone_id="phone-one")
    assert phone1.dropin_peer is pi

    phone2, task2 = await _run_phone(app, phone_id="phone-two")
    await asyncio.wait_for(task2, timeout=2)
    errs = [json.loads(t) for t in phone2.ws.sent_text if '"error"' in t]
    assert errs and errs[0]["code"] == "target_busy"

    # First call is undisturbed.
    assert phone1.dropin_peer is pi and pi.dropin_peer is phone1
    phone1.ws.feed_text({"type": "dropin_end"})
    await asyncio.wait_for(task1, timeout=2)
