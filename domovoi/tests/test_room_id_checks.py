"""Room ids are checked where they come in, and encoded where the dashboard
puts them into a URL (domovoi/room_ids.py)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from starlette.websockets import WebSocketDisconnect

from domovoi import room_ids, streaming
from domovoi.config import settings
from domovoi.db.repositories import SatelliteApprovalRepository
from domovoi.db.session import SessionLocal, engine
from domovoi.main import app as core_app
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import requires_db
from web.backend.main import app as web_app

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "web" / "static"

BAD_ROOM_IDS = [
    "..\\kitchen\\pairing\\reset?",
    "..\\..\\auth\\device-token\\rotate?",
    "kitchen?x",
    "kitchen#x",
    "..",
    "Kitchen",
    "kitchen room",
    "kitchen%2F",
    "-kitchen",
    "",
    "a" * 33,
]
GOOD_ROOM_IDS = ["kitchen", "living_room", "kids-room", "2nd-floor", "den"]


@pytest.mark.parametrize("room", GOOD_ROOM_IDS)
def test_room_ids_in_use_are_accepted(room: str) -> None:
    assert room_ids.is_room_id(room)


@pytest.mark.parametrize("room", BAD_ROOM_IDS)
def test_other_room_ids_are_refused(room: str) -> None:
    assert not room_ids.is_room_id(room)


def test_non_strings_are_refused() -> None:
    assert not room_ids.is_room_id(None)
    assert not room_ids.is_room_id(7)


def test_the_new_room_rule_is_inside_the_accepted_one() -> None:
    for room in GOOD_ROOM_IDS:
        if room_ids.ROOM_ID_RE.fullmatch(room):
            assert room_ids.is_room_id(room)


# ─── the satellite socket ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/v1/stream/..%5Ckitchen%5Cpairing%5Creset%3F",
        "/v1/stream/kitchen%3Fx",
        "/v1/stream/Kitchen",
    ],
)
def test_the_stream_socket_refuses_a_bad_room_id(path: str) -> None:
    client = TestClient(core_app)
    with client.websocket_connect(path) as ws:
        frame = json.loads(ws.receive_text())
        assert frame == {"type": "error", "code": "bad_room_id"}
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 1008


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.client = type("C", (), {"host": "192.168.1.50"})()
        self.headers: dict[str, str] = {}

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        pass


@pytest.fixture
async def _clean_approvals():
    streaming.APPROVAL_HELLO_LIMITER.reset()
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE satellite_pairings, satellite_approvals CASCADE"))
    yield
    streaming.APPROVAL_HELLO_LIMITER.reset()
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE satellite_pairings, satellite_approvals CASCADE"))


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize("room", ["..\\kitchen\\pairing\\reset?", "Kitchen"])
async def test_a_hello_under_a_bad_room_id_parks_nothing(
    room: str, monkeypatch: pytest.MonkeyPatch, _clean_approvals
) -> None:
    monkeypatch.setattr(settings, "satellite_pairing_strict", True)
    sess = StreamSession(_FakeWS(), room)  # type: ignore[arg-type]
    assert await sess._validate_pairing({"pairing_token": "a" * 64}) is False
    assert json.loads(sess.ws.sent[-1])["reason"] == "bad_room_id"
    async with SessionLocal() as s:
        assert await SatelliteApprovalRepository(s).list_pending() == []


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize("room", ["den", "kids-room"])
async def test_a_hello_under_a_good_room_id_still_parks(
    room: str, monkeypatch: pytest.MonkeyPatch, _clean_approvals
) -> None:
    monkeypatch.setattr(settings, "satellite_pairing_strict", True)
    sess = StreamSession(_FakeWS(), room)  # type: ignore[arg-type]
    assert await sess._validate_pairing({"pairing_token": "a" * 64}) is False
    assert json.loads(sess.ws.sent[-1])["reason"] == "awaiting_approval"
    async with SessionLocal() as s:
        assert await SatelliteApprovalRepository(s).get(room) is not None


# ─── the web routes ────────────────────────────────────────────────────────


def _web() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=web_app, raise_app_exceptions=False),
        base_url="http://127.0.0.1",
        headers={"X-Requested-With": "domovoi-tests"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/satellites/approvals/..%5Ckitchen%5Cpairing%5Creset%3F/reject"),
        ("POST", "/api/satellites/approvals/Kitchen/approve"),
        ("GET", "/api/satellites/kitchen%3Fx/config"),
        ("POST", "/api/satellites/..%5Cx/restart"),
        ("POST", "/api/music/skip/kitchen%23x"),
        ("GET", "/api/music/queue/..%5Cx"),
        ("GET", "/api/music/now-playing/Kitchen/lyrics"),
        ("PUT", "/api/captures/rooms/..%5Cx"),
    ],
)
async def test_web_routes_refuse_a_bad_room_id(method: str, path: str) -> None:
    async with _web() as c:
        r = await c.request(method, path, json={"code": "123456"})
    assert r.status_code == 422, (path, r.status_code, r.text)
    assert r.json()["detail"] == "not a room id"


# ─── the dashboard ─────────────────────────────────────────────────────────


def test_the_approval_buttons_encode_the_room_id() -> None:
    # Since rooms slice 8 the approval is addressed by the device's own
    # satellite id (the core's path segment is the satellite id since
    # piece 2), falling back to the room id from an older core. Either one
    # rides encoded, never raw.
    src = (STATIC / "satellites.jsx").read_text(encoding="utf-8")
    assert ("`/api/satellites/approvals/${encodeURIComponent(a.satellite_id || a.room_id)}/${action}`"
            in src)
    assert "/api/satellites/approvals/${a.room_id}" not in src
    assert "/api/satellites/approvals/${a.satellite_id" not in src


_ROOM_IN_URL = re.compile(
    r"/api/[^`'\"]*\$\{(?:s\.room_id|a\.room_id|c\.room_id|room_id|roomId|"
    r"target\.roomId|t\.roomId|room|DP_ROOM)\}"
)


def test_dashboard_urls_encode_room_ids() -> None:
    found = []
    for f in sorted(STATIC.glob("*.js*")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if _ROOM_IN_URL.search(line):
                found.append(f"{f.name}:{n}: {line.strip()}")
    assert found == []
