"""Tests for two-way drop-in (Feature 4).

Covers the feasibility helper, DropInHandler fast-path parsing + spoken
responses (including full-duplex / connectivity / busy gating), the
accept/decline confirmation contract, and the streaming-layer relay +
teardown. The satellite open-mic client (chunk D) is Pi-side and
untestable on the dev host — see ui_testing_needs.txt.
"""

from __future__ import annotations

import array
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi.config import settings
from domovoi.dropin_common import OK, dropin_feasibility, pretty_room
from domovoi.handlers.dropin import _END_RE, _START_RE, DropInHandler
from domovoi.models import Context
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import requires_db


# ─── Test doubles ──────────────────────────────────────────────────────────


def make_app(*, active=None, fd=None, dropins=None):
    """A stand-in FastAPI app exposing just the app.state dicts drop-in reads."""
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


def ctx_for(room, app):
    return Context(session_id=uuid4(), room_id=room, online=True, app=app)


class FakeWS:
    """Minimal WebSocket: captures sent bytes/text, never fails."""

    def __init__(self, app):
        self.app = app
        self.sent_bytes: list[bytes] = []
        self.sent_text: list[str] = []

    async def send_bytes(self, b):
        self.sent_bytes.append(b)

    async def send_text(self, t):
        self.sent_text.append(t)


# ─── dropin_feasibility ──────────────────────────────────────────────────────


def test_feasibility_ok():
    app = make_app(active={"a": 1, "b": 1}, fd={"a": True, "b": True})
    assert dropin_feasibility(app, "a", "b") == OK


def test_feasibility_same_room():
    app = make_app(active={"a": 1}, fd={"a": True})
    assert dropin_feasibility(app, "a", "a") == "same_room"


def test_feasibility_target_offline():
    app = make_app(active={"a": 1}, fd={"a": True, "b": True})
    assert dropin_feasibility(app, "a", "b") == "target_offline"


def test_feasibility_initiator_offline():
    app = make_app(active={"b": 1}, fd={"a": True, "b": True})
    assert dropin_feasibility(app, "a", "b") == "initiator_offline"


def test_feasibility_no_aec():
    app = make_app(active={"a": 1, "b": 1}, fd={"a": True, "b": False})
    assert dropin_feasibility(app, "a", "b") == "target_no_aec"
    app2 = make_app(active={"a": 1, "b": 1}, fd={"a": False, "b": True})
    assert dropin_feasibility(app2, "a", "b") == "initiator_no_aec"


def test_feasibility_busy():
    app = make_app(
        active={"a": 1, "b": 1},
        fd={"a": True, "b": True},
        dropins={"b": {"peer": "c"}},
    )
    assert dropin_feasibility(app, "a", "b") == "target_busy"


def test_pretty_room():
    assert pretty_room("living_room") == "living room"


# ─── Fast-path regexes ───────────────────────────────────────────────────────


def test_start_regex_matches():
    for s in (
        "drop in on the kitchen",
        "drop-in on kitchen",
        "dropin on the office",
        "drop in to the garage",
        "connect me to the living room",
    ):
        assert _START_RE.match(s), s


def test_start_regex_rejects_unrelated():
    for s in ("play the kitchen", "stop", "drop it", "call mom"):
        assert not _START_RE.match(s), s


def test_end_regex_matches():
    for s in (
        "hang up",
        "hang up the call",
        "hang up the phone",
        "end the call",
        "end call",
        "stop the call",
        "end the drop-in",
        "stop drop in",
        "end dropin",
    ):
        assert _END_RE.match(s), s


def test_end_regex_never_poaches_bare_stop_or_music():
    # The handler sits ABOVE Music/Radio; a bare stop/end must fall through.
    for s in ("stop", "end", "stop the music", "stop the radio", "hang on", "end of story"):
        assert not _END_RE.match(s), s


# ─── DropInHandler._start ────────────────────────────────────────────────────


@pytest.fixture
def rooms_office_kitchen():
    """Provision office+kitchen for _resolve_target_rooms; clean up after."""
    from domovoi.clients import mpd as mpd_module

    mpd_module._room_ports = {"office": (6600, 8001), "kitchen": (6601, 8002)}
    try:
        yield
    finally:
        mpd_module._room_ports = {}


async def test_start_request_auto(rooms_office_kitchen, monkeypatch):
    monkeypatch.setattr(settings, "dropin_accept_mode", "auto", raising=False)
    app = make_app(active={"office": 1, "kitchen": 1}, fd={"office": True, "kitchen": True})
    r = DropInHandler()._start("the kitchen", ctx_for("office", app))
    assert r.dropin_action == "request"
    assert r.dropin_room == "kitchen"
    assert r.dropin_peer_label == "kitchen"
    assert "kitchen" in r.text.lower()


async def test_start_request_confirm_wording(rooms_office_kitchen, monkeypatch):
    monkeypatch.setattr(settings, "dropin_accept_mode", "confirm", raising=False)
    app = make_app(active={"office": 1, "kitchen": 1}, fd={"office": True, "kitchen": True})
    r = DropInHandler()._start("kitchen", ctx_for("office", app))
    assert r.dropin_action == "request"
    assert "asking" in r.text.lower()


async def test_start_target_offline(rooms_office_kitchen):
    app = make_app(active={"office": 1}, fd={"office": True, "kitchen": True})
    r = DropInHandler()._start("the kitchen", ctx_for("office", app))
    assert r.dropin_action is None
    assert "isn't connected" in r.text


async def test_start_target_no_aec(rooms_office_kitchen):
    app = make_app(active={"office": 1, "kitchen": 1}, fd={"office": True, "kitchen": False})
    r = DropInHandler()._start("kitchen", ctx_for("office", app))
    assert r.dropin_action is None
    assert "echo-cancelling" in r.text


async def test_start_same_room(rooms_office_kitchen):
    app = make_app(active={"office": 1}, fd={"office": True})
    r = DropInHandler()._start("the office", ctx_for("office", app))
    assert r.dropin_action is None
    assert "your own room" in r.text


async def test_start_busy(rooms_office_kitchen):
    app = make_app(
        active={"office": 1, "kitchen": 1},
        fd={"office": True, "kitchen": True},
        dropins={"kitchen": {"peer": "garage"}},
    )
    r = DropInHandler()._start("kitchen", ctx_for("office", app))
    assert r.dropin_action is None
    assert "already in a call" in r.text


async def test_start_unknown_room(rooms_office_kitchen):
    app = make_app(active={"office": 1}, fd={"office": True})
    r = DropInHandler()._start("the bathroom", ctx_for("office", app))
    assert r.dropin_action is None
    assert "don't know" in r.text.lower()


async def test_start_broadcast_rejected(rooms_office_kitchen):
    app = make_app(active={"office": 1, "kitchen": 1}, fd={"office": True, "kitchen": True})
    r = DropInHandler()._start("everyone", ctx_for("office", app))
    assert r.dropin_action is None
    assert "one room at a time" in r.text


async def test_start_no_app():
    ctx = Context(session_id=uuid4(), room_id="office", online=True, app=None)
    r = DropInHandler()._start("kitchen", ctx)
    assert r.dropin_action is None
    assert "satellite" in r.text.lower()


async def test_start_disabled(rooms_office_kitchen, monkeypatch):
    monkeypatch.setattr(settings, "dropin_enabled", False, raising=False)
    app = make_app(active={"office": 1, "kitchen": 1}, fd={"office": True, "kitchen": True})
    r = DropInHandler()._start("kitchen", ctx_for("office", app))
    assert r.dropin_action is None
    assert "turned off" in r.text


# ─── DropInHandler._end + confirmation + tool ────────────────────────────────


async def test_end_in_call():
    app = make_app(dropins={"office": {"peer": "kitchen"}})
    r = DropInHandler()._end(ctx_for("office", app))
    assert r.dropin_action == "end"
    assert "hang" in r.text.lower()


async def test_end_not_in_call():
    app = make_app()
    r = DropInHandler()._end(ctx_for("office", app))
    assert r.dropin_action is None
    assert "not in a call" in r.text


async def test_confirmation_accept():
    app = make_app()
    r = await DropInHandler().handle_confirmation(
        "core.dropin_invite", {"initiator_room": "office"}, True, ctx_for("kitchen", app), None
    )
    assert r.dropin_action == "accept"
    assert r.dropin_room == "office"


async def test_confirmation_decline():
    app = make_app()
    r = await DropInHandler().handle_confirmation(
        "core.dropin_invite", {"initiator_room": "office"}, False, ctx_for("kitchen", app), None
    )
    assert r.dropin_action is None
    assert "never mind" in r.text.lower()


def test_tool_schema_shape():
    h = DropInHandler()
    assert h.name == "dropin"
    assert h.requires_network == "no"
    assert h.tool_schema["name"] == "dropin"
    assert set(h.tool_schema["parameters"]["properties"]) == {"room", "action"}


async def test_execute_from_tool_start_and_end(rooms_office_kitchen):
    app = make_app(active={"office": 1, "kitchen": 1}, fd={"office": True, "kitchen": True})
    h = DropInHandler()
    r = await h.execute_from_tool({"action": "start", "room": "kitchen"}, ctx_for("office", app), None)
    assert r.dropin_action == "request" and r.dropin_room == "kitchen"

    app2 = make_app(dropins={"office": {"peer": "kitchen"}})
    r2 = await h.execute_from_tool({"action": "end"}, ctx_for("office", app2), None)
    assert r2.dropin_action == "end"


# ─── Streaming relay + teardown ──────────────────────────────────────────────


async def test_relay_pairs_forwards_and_tears_down(monkeypatch):
    # Disable the silence watchdog so the test doesn't spawn a timer task.
    monkeypatch.setattr(settings, "dropin_silence_timeout_sec", 0.0, raising=False)
    app = make_app(fd={"office": True, "kitchen": True})
    a = StreamSession(FakeWS(app), "office")
    b = StreamSession(FakeWS(app), "kitchen")
    app.state.active_sessions["office"] = a
    app.state.active_sessions["kitchen"] = b

    await a._begin_dropin(b)

    # Both ends got the soft "connected" chime.
    from domovoi.dropin_chimes import END_CHIME_PCM, START_CHIME_PCM
    assert START_CHIME_PCM in a.ws.sent_bytes
    assert START_CHIME_PCM in b.ws.sent_bytes

    # Paired both directions + recorded in active_dropins.
    assert a.dropin_peer is b and b.dropin_peer is a
    assert app.state.active_dropins["office"]["peer"] == "kitchen"
    assert app.state.active_dropins["office"]["initiator"] is True
    assert app.state.active_dropins["kitchen"]["initiator"] is False

    # Each side got a dropin_start with the forced 16 kHz inbound rate.
    starts_a = [json.loads(t) for t in a.ws.sent_text if '"dropin_start"' in t]
    starts_b = [json.loads(t) for t in b.ws.sent_text if '"dropin_start"' in t]
    assert starts_a and starts_a[0]["audio_sample_rate"] == 16000
    assert starts_a[0]["peer_room"] == "kitchen"
    assert starts_a[0]["full_duplex"] is True
    assert starts_b and starts_b[0]["peer_room"] == "office"

    # Relay: A's mic frame is forwarded to B and NOT buffered for STT.
    await a._on_audio(b"\x01\x02\x03\x04")
    assert b.ws.sent_bytes[-1] == b"\x01\x02\x03\x04"
    assert len(a.audio_buf) == 0

    # Mid-call command capture: an active utterance buffers (for "hang up"
    # STT) instead of relaying.
    a.utterance_active = True
    await a._on_audio(b"\xaa\xbb")
    assert bytes(a.audio_buf) == b"\xaa\xbb"
    assert b"\xaa\xbb" not in b.ws.sent_bytes
    a.utterance_active = False
    a.audio_buf.clear()

    # Teardown clears both sides + emits the end chime then dropin_end to each.
    await a._end_dropin(ended_by="office")
    assert a.dropin_peer is None and b.dropin_peer is None
    assert "office" not in app.state.active_dropins
    assert "kitchen" not in app.state.active_dropins
    assert END_CHIME_PCM in a.ws.sent_bytes
    assert END_CHIME_PCM in b.ws.sent_bytes
    assert any('"dropin_end"' in t for t in a.ws.sent_text)
    assert any('"dropin_end"' in t for t in b.ws.sent_text)


async def test_begin_refused_when_already_paired(monkeypatch):
    monkeypatch.setattr(settings, "dropin_silence_timeout_sec", 0.0, raising=False)
    app = make_app(fd={"office": True, "kitchen": True, "garage": True})
    a = StreamSession(FakeWS(app), "office")
    b = StreamSession(FakeWS(app), "kitchen")
    c = StreamSession(FakeWS(app), "garage")
    for s in (a, b, c):
        app.state.active_sessions[s.room_id] = s

    await a._begin_dropin(b)
    # A is already in a call; a second begin (A↔C) must be refused without
    # disturbing the existing A↔B pairing.
    await a._begin_dropin(c)
    assert a.dropin_peer is b
    assert c.dropin_peer is None
    assert "garage" not in app.state.active_dropins

    await a._end_dropin(ended_by="office")


async def test_relay_failure_tears_down(monkeypatch):
    monkeypatch.setattr(settings, "dropin_silence_timeout_sec", 0.0, raising=False)
    app = make_app(fd={"office": True, "kitchen": True})
    a = StreamSession(FakeWS(app), "office")
    b = StreamSession(FakeWS(app), "kitchen")
    app.state.active_sessions["office"] = a
    app.state.active_sessions["kitchen"] = b
    await a._begin_dropin(b)

    # Make B's socket dead — a relay send failure must tear the call down
    # on BOTH sides rather than stream into a void.
    async def boom(_):
        raise ConnectionError("peer gone")

    b.ws.send_bytes = boom
    await a._on_audio(b"\x01\x02")
    assert a.dropin_peer is None and b.dropin_peer is None
    assert "office" not in app.state.active_dropins


# ─── Echo mitigation: relay noise gate + in-call volume trim ─────────────────


def test_pcm_dbfs_levels():
    from domovoi.streaming import _pcm_dbfs

    assert _pcm_dbfs(b"") <= -120.0
    assert _pcm_dbfs(b"\x00\x00" * 16) <= -120.0  # silence
    loud = array.array("h", [20000] * 16).tobytes()
    assert _pcm_dbfs(loud) > -20.0


async def test_relay_noise_gate(monkeypatch):
    monkeypatch.setattr(settings, "dropin_silence_timeout_sec", 0.0, raising=False)
    monkeypatch.setattr(settings, "dropin_relay_gate_dbfs", -55.0, raising=False)
    app = make_app(fd={"office": True, "kitchen": True})
    a = StreamSession(FakeWS(app), "office")
    b = StreamSession(FakeWS(app), "kitchen")
    app.state.active_sessions["office"] = a
    app.state.active_sessions["kitchen"] = b
    await a._begin_dropin(b)

    loud = array.array("h", [12000] * 32).tobytes()
    silent = b"\x00\x00" * 32

    n0 = len(b.ws.sent_bytes)
    await a._on_audio(loud)
    assert b.ws.sent_bytes[-1] == loud          # above gate → relayed
    assert len(b.ws.sent_bytes) == n0 + 1
    t_after_loud = a._dropin_last_audio

    await a._on_audio(silent)
    assert len(b.ws.sent_bytes) == n0 + 1       # below gate → NOT relayed
    assert a._dropin_last_audio == t_after_loud  # silence doesn't reset the timer

    await a._end_dropin(ended_by="office")


# ─── Admin endpoints (/v1/admin/dropin/*) ────────────────────────────────────


def _seed_two_rooms(app, *, fd_kitchen=True):
    """Register office+kitchen as live sessions on a real app for admin tests."""
    a = StreamSession(FakeWS(app), "office")
    b = StreamSession(FakeWS(app), "kitchen")
    app.state.active_sessions = {"office": a, "kitchen": b}
    app.state.satellite_full_duplex = {"office": True, "kitchen": fd_kitchen}
    app.state.active_dropins = {}
    return a, b


@requires_db
async def test_admin_dropin_start_happy(monkeypatch):
    monkeypatch.setattr(settings, "dropin_silence_timeout_sec", 0.0, raising=False)
    from domovoi.main import app

    async with app.router.lifespan_context(app):
        a, b = _seed_two_rooms(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/dropin/start",
                json={"initiator_room": "office", "target_room": "kitchen"},
            )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "active"
        assert a.dropin_peer is b and b.dropin_peer is a
        await a._end_dropin(ended_by="office")


@requires_db
async def test_admin_dropin_start_404_when_target_offline():
    from domovoi.main import app

    async with app.router.lifespan_context(app):
        _seed_two_rooms(app)
        del app.state.active_sessions["kitchen"]  # provisioned in fd but not connected
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/dropin/start",
                json={"initiator_room": "office", "target_room": "kitchen"},
            )
        assert r.status_code == 404


@requires_db
async def test_admin_dropin_start_409_when_no_aec():
    from domovoi.main import app

    async with app.router.lifespan_context(app):
        _seed_two_rooms(app, fd_kitchen=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/dropin/start",
                json={"initiator_room": "office", "target_room": "kitchen"},
            )
        assert r.status_code == 409
        assert r.json()["detail"] == "target_no_aec"


@requires_db
async def test_admin_dropin_end_happy_and_404(monkeypatch):
    monkeypatch.setattr(settings, "dropin_silence_timeout_sec", 0.0, raising=False)
    from domovoi.main import app

    async with app.router.lifespan_context(app):
        a, b = _seed_two_rooms(app)
        await a._begin_dropin(b)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/v1/admin/dropin/end", json={"room_id": "kitchen"})
            assert r.status_code == 200, r.text
            assert r.json() == {"ended": True, "peer": "office"}
            assert a.dropin_peer is None and b.dropin_peer is None
            # Second end on a room no longer in a call → 404.
            r2 = await client.post("/v1/admin/dropin/end", json={"room_id": "kitchen"})
            assert r2.status_code == 404
