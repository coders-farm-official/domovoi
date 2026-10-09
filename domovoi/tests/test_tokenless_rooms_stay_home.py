"""A LAN client with no pairing token never reaches another room (CORE-11).

The audit's critical repro (A1-02): with lenient pairing a tokenless
client connected as a brand-new room name, said "drop in on the kitchen",
and got the kitchen's live microphone. Lenient was the CODE default, so
every install whose .env lacked ``SATELLITE_PAIRING_STRICT=true`` ran it.
Two things now hold, and both are checked here without a database:

* strict is the default, so the tokenless hello for a new room is refused
  before the socket is a room at all;
* where a household has opted back into lenient, a socket the core let in
  without a token may use its own room and nothing else: it cannot start
  a drop-in or an announcement in another room — refused aloud by the
  handlers, and refused again by the streaming layer if anything else ever
  hands it such a response.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from domovoi import streaming
from domovoi.config import settings
from domovoi.handlers.dropin import DropInHandler
from domovoi.handlers.intercom import IntercomHandler
from domovoi.models import Context, Response
from domovoi.streaming import StreamSession

# ─── Test doubles ──────────────────────────────────────────────────────────


class _WS:
    """Records frames; carries a peer so the approval budget has a key."""

    def __init__(self, app) -> None:
        self.app = app
        self.text: list[dict] = []
        self.bytes: list[bytes] = []
        self.client = SimpleNamespace(host="192.168.1.66")
        self.headers: dict[str, str] = {}

    async def send_text(self, t: str) -> None:
        self.text.append(json.loads(t))

    async def send_bytes(self, b: bytes) -> None:
        self.bytes.append(b)

    async def close(self, code: int = 1000) -> None:  # pragma: no cover
        pass


def _app() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(
        active_sessions={},
        satellite_full_duplex={},
        active_dropins={},
        pending_dropins={},
        dropin_lock=asyncio.Lock(),
        resumable_music={},
        pending_music_start={},
        satellite_voice={},
        satellite_volume={},
        current_playlist={},
    ))


class _Pairings:
    """SatellitePairingRepository stand-in: nobody has paired anything."""

    paired: list[str] = []

    def __init__(self, _s) -> None:
        pass

    async def get_pairing(self, room_id):
        return None

    async def pair(self, room_id, token_hash):
        type(self).paired.append(room_id)

    async def touch_last_seen(self, room_id):  # pragma: no cover
        return None


class _Approvals:
    parked: list[str] = []

    def __init__(self, _s) -> None:
        pass

    async def request(self, room_id, **kw):
        type(self).parked.append(room_id)
        return "parked", kw.get("code")


@pytest.fixture
def no_db(monkeypatch):
    @asynccontextmanager
    async def _scope():
        yield SimpleNamespace()

    _Pairings.paired = []
    _Approvals.parked = []
    monkeypatch.setattr(streaming, "session_scope", _scope)
    monkeypatch.setattr(streaming, "SatellitePairingRepository", _Pairings)
    monkeypatch.setattr(streaming, "SatelliteApprovalRepository", _Approvals)
    streaming.APPROVAL_HELLO_LIMITER.reset()
    yield
    streaming.APPROVAL_HELLO_LIMITER.reset()


@pytest.fixture
def rooms(monkeypatch):
    """The kitchen (a paired XVF3800 room) and the caller's room are both
    provisioned rooms the handlers' room matcher knows."""
    from domovoi.clients import mpd as mpd_module

    monkeypatch.setattr(
        mpd_module, "_room_ports",
        {"kitchen": (6600, 8001), "zz-attacker": (6601, 8002)},
    )


def _live_pair(app, *, caller_paired: bool):
    """A paired kitchen and a caller room, both online and full duplex."""
    kitchen = StreamSession(_WS(app), "kitchen")  # type: ignore[arg-type]
    kitchen.token_authenticated = True
    caller = StreamSession(_WS(app), "zz-attacker")  # type: ignore[arg-type]
    caller.token_authenticated = caller_paired
    app.state.active_sessions.update({"kitchen": kitchen, "zz-attacker": caller})
    app.state.satellite_full_duplex.update({"kitchen": True, "zz-attacker": True})
    return kitchen, caller


def _ctx(app, room="zz-attacker") -> Context:
    return Context(session_id=uuid4(), room_id=room, online=True, app=app)


# ─── The default: the tokenless hello never becomes a room ────────────────


async def test_by_default_a_tokenless_hello_for_a_new_room_is_refused(no_db) -> None:
    """No setting anywhere means strict: the audit's first step (connect
    as `zz-attacker` with no token, get `ready`) now ends at the hello."""
    assert settings.satellite_pairing_strict is True
    sess = StreamSession(_WS(_app()), "zz-attacker")  # type: ignore[arg-type]
    accepted = await sess._validate_pairing(
        {"type": "hello", "room_id": "zz-attacker", "supports_full_duplex": True}
    )
    assert accepted is False
    assert sess.ws.text[-1]["reason"] == "pairing_rejected"
    assert sess.token_authenticated is False
    assert _Pairings.paired == [] and _Approvals.parked == []


async def test_by_default_a_token_alone_parks_instead_of_claiming(no_db) -> None:
    """Bringing a token proves only that you have a token: the first
    pairing of a room waits for a person, it is not taken on trust."""
    sess = StreamSession(_WS(_app()), "zz-attacker")  # type: ignore[arg-type]
    accepted = await sess._validate_pairing(
        {"type": "hello", "room_id": "zz-attacker", "pairing_token": "t" * 64}
    )
    assert accepted is False
    assert sess.ws.text[-1]["reason"] == "awaiting_approval"
    assert _Pairings.paired == [] and _Approvals.parked == ["zz-attacker"]


async def test_lenient_is_still_honoured_but_unauthenticated(no_db, monkeypatch) -> None:
    """A household that writes SATELLITE_PAIRING_STRICT=false keeps its
    tokenless fleet working; the socket is accepted and marked as having
    proved nothing."""
    monkeypatch.setattr(settings, "satellite_pairing_strict", False)
    sess = StreamSession(_WS(_app()), "zz-attacker")  # type: ignore[arg-type]
    assert await sess._validate_pairing({"type": "hello"}) is True
    assert sess.token_authenticated is False


# ─── Lenient: the spoken requests are refused aloud ───────────────────────


async def test_a_tokenless_room_cannot_ask_for_a_drop_in(rooms, monkeypatch) -> None:
    monkeypatch.setattr(settings, "dropin_accept_mode", "auto", raising=False)
    app = _app()
    _live_pair(app, caller_paired=False)
    r = DropInHandler()._start("the kitchen", _ctx(app))
    assert r.dropin_action is None
    assert "isn't paired" in r.text


async def test_a_paired_room_still_drops_in(rooms, monkeypatch) -> None:
    monkeypatch.setattr(settings, "dropin_accept_mode", "auto", raising=False)
    app = _app()
    _live_pair(app, caller_paired=True)
    r = DropInHandler()._start("the kitchen", _ctx(app))
    assert r.dropin_action == "request" and r.dropin_room == "kitchen"


async def test_a_tokenless_room_cannot_announce_to_the_house(rooms) -> None:
    app = _app()
    _live_pair(app, caller_paired=False)
    h = IntercomHandler()
    r = h._build_response(None, "the door is open", _ctx(app))
    assert r.announce_to_rooms == [] and r.announce_text is None
    assert "isn't paired" in r.text
    r = h._build_response("the kitchen", "come here", _ctx(app))
    assert r.announce_to_rooms == []


async def test_a_paired_room_still_announces(rooms) -> None:
    app = _app()
    _live_pair(app, caller_paired=True)
    r = IntercomHandler()._build_response("the kitchen", "come here", _ctx(app))
    assert r.announce_to_rooms == ["kitchen"]


# ─── The streaming layer refuses again, whatever produced the request ─────


async def test_the_bridge_never_opens_from_a_tokenless_initiator(monkeypatch) -> None:
    monkeypatch.setattr(settings, "dropin_accept_mode", "auto", raising=False)
    monkeypatch.setattr(settings, "dropin_silence_timeout_sec", 0.0, raising=False)
    app = _app()
    kitchen, caller = _live_pair(app, caller_paired=False)

    await caller._handle_dropin_action(
        Response(text="x", dropin_action="request", dropin_room="kitchen")
    )

    assert caller.dropin_peer is None and kitchen.dropin_peer is None
    assert app.state.active_dropins == {}
    assert not any(f.get("type") == "dropin_start" for f in kitchen.ws.text)
    assert kitchen.ws.bytes == []          # not even the connect chime


async def test_the_bridge_still_opens_from_a_paired_initiator(monkeypatch) -> None:
    monkeypatch.setattr(settings, "dropin_accept_mode", "auto", raising=False)
    monkeypatch.setattr(settings, "dropin_silence_timeout_sec", 0.0, raising=False)
    app = _app()
    kitchen, caller = _live_pair(app, caller_paired=True)

    await caller._handle_dropin_action(
        Response(text="x", dropin_action="request", dropin_room="kitchen")
    )
    try:
        assert caller.dropin_peer is kitchen and kitchen.dropin_peer is caller
    finally:
        await caller._end_dropin(ended_by="zz-attacker")


def test_the_intercom_fan_out_skips_a_tokenless_room(monkeypatch) -> None:
    """End to end over the socket: a response that names other rooms (as
    IntercomHandler's would) reaches none of them when the room it came
    from connected without a token, and still reaches them from a paired
    one."""
    from fastapi.testclient import TestClient

    from domovoi.main import app
    from domovoi.tests.test_streaming import _FakeTTS, _FakeWhisper, _hello, _patch_pipeline

    paired_rooms = {"garage", "office"}

    async def _validate(self, ctrl):
        self.token_authenticated = self.room_id in paired_rooms
        return True

    monkeypatch.setattr(StreamSession, "_validate_pairing", _validate)
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper("announce to the house dinner is ready"),
        tts=_FakeTTS(),
        response=Response(
            text="Broadcasting to the house.",
            matched_handler="intercom",
            matched_path="fast",
            online=True,
            announce_to_rooms=["garage"],
            announce_text="dinner is ready",
        ),
    )

    def _speak(ws) -> None:
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
        ws.send_bytes(b"\x00" * 1024)
        ws.send_text(json.dumps({"type": "utterance_end"}))
        ws.receive_json()   # transcript
        ws.receive_json()   # response_start
        ws.receive_bytes()  # audio
        ws.receive_json()   # response_end

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/garage") as garage_ws:
            _hello(garage_ws, "garage")
            with client.websocket_connect("/v1/stream/zz-attacker") as tokenless:
                _hello(tokenless, "zz-attacker")
                _speak(tokenless)
            # Nothing was announced: the next frame the garage sees is the
            # answer to its own ping.
            garage_ws.send_text(json.dumps({"type": "ping"}))
            assert garage_ws.receive_json() == {"type": "pong"}

            with client.websocket_connect("/v1/stream/office") as office_ws:
                _hello(office_ws, "office")
                _speak(office_ws)
            start = garage_ws.receive_json()
            assert start["type"] == "response_start"
            assert start["text"] == "dinner is ready"
