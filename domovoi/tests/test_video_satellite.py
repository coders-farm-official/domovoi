"""Satellite type threading — hello `sat_type` / `mic_enabled` + V003 rows.

Covers (foundation):
  * hello caches sat_type / mic_enabled with lenient defaults (absent →
    "voice" / True) and coerces an unknown type to "voice";
  * an EXPLICIT hello sat_type upserts the `satellites` inventory row, while
    an implicit (absent) one never overwrites a preseeded row — the rule that
    protects USB-adoption metadata from old clients;
  * ``SatellitesRepository`` CRUD (preseed upsert / label / get_by_mac /
    delete);
  * ``/v1/admin/snapshot`` exposing satellite_sat_type / satellite_mic_enabled;
  * web ``_satellite_for`` resolving the inventory row FIRST (explicit
    sources win over the live-snapshot default).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi.db.repositories import SatellitesRepository
from domovoi.db.session import engine, session_scope
from domovoi.main import app as core_app
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import requires_db


# ─── Test doubles (mirrors test_satellite_upgrade.py) ─────────────────────


class _FakeAppState:
    def __init__(self) -> None:
        self.satellite_full_duplex: dict[str, bool] = {}
        self.satellite_synced_sha: dict[str, str | None] = {}
        self.satellite_sat_type: dict[str, str] = {}
        self.satellite_mic_enabled: dict[str, bool] = {}


class _FakeApp:
    def __init__(self) -> None:
        self.state = _FakeAppState()


class _FakeWS:
    """Minimal WebSocket stand-in — the hello branch only reaches
    ``self.ws.app.state`` (and ``send_text`` on a pairing reject)."""

    def __init__(self) -> None:
        self.app = _FakeApp()
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        pass


@pytest_asyncio.fixture(autouse=True)
async def _clean_satellites():
    """Truncate the inventory table around each test (DB-less tests skip
    the fixture body errors via requires_db gating on the tests that write)."""
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE satellites CASCADE"))
    except Exception:
        yield
        return
    yield
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE satellites CASCADE"))


async def _get_row(room_id: str) -> dict | None:
    async with session_scope() as s:
        return await SatellitesRepository(s).get(room_id)


# ─── hello caching + lenient defaults ─────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_hello_caches_explicit_sat_type_and_mic() -> None:
    ws = _FakeWS()
    session = StreamSession(ws, "den")  # type: ignore[arg-type]
    await session._on_control(
        {"type": "hello", "sat_type": "video", "mic_enabled": False}
    )
    assert ws.app.state.satellite_sat_type["den"] == "video"
    assert ws.app.state.satellite_mic_enabled["den"] is False
    row = await _get_row("den")
    assert row is not None and row["sat_type"] == "video"
    assert row["adopted_via"] == "hello"


@requires_db
@pytest.mark.asyncio
async def test_hello_defaults_when_fields_absent() -> None:
    """An old client's hello (no sat_type / mic_enabled) caches the
    historical defaults and writes NO inventory row."""
    ws = _FakeWS()
    session = StreamSession(ws, "kitchen")  # type: ignore[arg-type]
    await session._on_control({"type": "hello", "supports_full_duplex": False})
    assert ws.app.state.satellite_sat_type["kitchen"] == "voice"
    assert ws.app.state.satellite_mic_enabled["kitchen"] is True
    assert await _get_row("kitchen") is None


@requires_db
@pytest.mark.asyncio
async def test_hello_unknown_sat_type_coerced_to_voice() -> None:
    ws = _FakeWS()
    session = StreamSession(ws, "garage")  # type: ignore[arg-type]
    await session._on_control({"type": "hello", "sat_type": "toaster"})
    assert ws.app.state.satellite_sat_type["garage"] == "voice"
    # An unknown value is treated as ABSENT — nothing persisted.
    assert await _get_row("garage") is None


@requires_db
@pytest.mark.asyncio
async def test_hello_implicit_never_downgrades_preseeded_row() -> None:
    """The adoption-protection rule: a preseeded 'video' row survives an old
    client's hello that omits sat_type; an explicit hello still updates."""
    async with session_scope() as s:
        await SatellitesRepository(s).preseed_upsert(
            "den", sat_type="video", hardware="Radxa Zero 3W", mac="aa:bb:cc:dd:ee:ff"
        )
    ws = _FakeWS()
    session = StreamSession(ws, "den")  # type: ignore[arg-type]
    await session._on_control({"type": "hello"})
    row = await _get_row("den")
    assert row is not None and row["sat_type"] == "video"

    await session._on_control({"type": "hello", "sat_type": "voice"})
    row = await _get_row("den")
    assert row is not None and row["sat_type"] == "voice"
    # The explicit update keeps the adoption metadata (only type + stamp move).
    assert row["hardware"] == "Radxa Zero 3W"


# ─── SatellitesRepository CRUD ────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_repository_crud_roundtrip() -> None:
    async with session_scope() as s:
        repo = SatellitesRepository(s)
        await repo.preseed_upsert(
            "office",
            sat_type="video",
            room_label="Study",
            hardware="Raspberry Pi Zero 2 W Rev 1.0",
            board="raspberry_pi_zero_2_w",
            mac="AA:BB:CC:00:11:22".lower(),
        )
    async with session_scope() as s:
        repo = SatellitesRepository(s)
        row = await repo.get("office")
        assert row is not None
        assert row["sat_type"] == "video"
        assert row["room_label"] == "Study"
        assert row["adopted_via"] == "usb"
        assert row["adopted_at"] is not None
        by_mac = await repo.get_by_mac("AA:BB:CC:00:11:22")
        assert by_mac is not None and by_mac["room_id"] == "office"
        await repo.set_room_label("office", None)
    async with session_scope() as s:
        repo = SatellitesRepository(s)
        row = await repo.get("office")
        assert row is not None and row["room_label"] is None
        assert await repo.delete("office") is True
        assert await repo.delete("office") is False
    assert await _get_row("office") is None


@requires_db
@pytest.mark.asyncio
async def test_set_room_label_creates_row_for_legacy_room() -> None:
    """Labeling a pre-V003 satellite (mpd_rooms only) creates the inventory
    row with the voice default rather than failing."""
    async with session_scope() as s:
        await SatellitesRepository(s).set_room_label("kitchen", "Ground Floor")
    row = await _get_row("kitchen")
    assert row is not None
    assert row["room_label"] == "Ground Floor"
    assert row["sat_type"] == "voice"
    assert row["adopted_via"] == "manual"


# ─── /v1/admin/snapshot exposure ──────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_admin_snapshot_exposes_sat_type_and_mic() -> None:
    transport = ASGITransport(app=core_app)
    async with core_app.router.lifespan_context(core_app):
        core_app.state.satellite_sat_type = {"den": "video"}
        core_app.state.satellite_mic_enabled = {"den": False}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/v1/admin/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert body["satellite_sat_type"] == {"den": "video"}
    assert body["satellite_mic_enabled"] == {"den": False}


# ─── web _satellite_for resolution ────────────────────────────────────────


@pytest.mark.asyncio
async def test_satellite_for_prefers_inventory_row(monkeypatch) -> None:
    """The V003 row (explicit adoption/hello writes) outranks the live
    snapshot's default; mic state is live-only; label/hardware surface."""
    from web.backend.api import satellites as sat_api

    monkeypatch.setattr(sat_api, "_now_playing_for", AsyncMock(return_value=None))
    room = {
        "room_id": "den",
        "control_port": 6650,
        "http_port": 8050,
        "last_connected_at": None,
    }
    snapshot = {
        "active_rooms": ["den"],
        # An old client connected: the snapshot only has the implicit default.
        "satellite_sat_type": {"den": "voice"},
        "satellite_mic_enabled": {"den": False},
    }
    meta = {
        "den": {
            "sat_type": "video",
            "room_label": "Living Room",
            "hardware": "Radxa Zero 3W",
            "board": "radxa_zero3w",
            "adopted_at": None,
        }
    }
    sat = await sat_api._satellite_for(room, snapshot, {}, meta)
    assert sat.sat_type == "video"
    assert sat.mic_enabled is False
    assert sat.room_label == "Living Room"
    assert sat.hardware == "Radxa Zero 3W"


@pytest.mark.asyncio
async def test_satellite_for_defaults_without_row_or_snapshot(monkeypatch) -> None:
    from web.backend.api import satellites as sat_api

    monkeypatch.setattr(sat_api, "_now_playing_for", AsyncMock(return_value=None))
    room = {
        "room_id": "attic",
        "control_port": 6651,
        "http_port": 8051,
        "last_connected_at": None,
    }
    sat = await sat_api._satellite_for(room, {}, {}, {})
    assert sat.sat_type == "voice"
    assert sat.mic_enabled is True
    assert sat.room_label is None
    assert sat.display is None


@pytest.mark.asyncio
async def test_satellite_for_surfaces_display_state(monkeypatch) -> None:
    from web.backend.api import satellites as sat_api

    monkeypatch.setattr(sat_api, "_now_playing_for", AsyncMock(return_value=None))
    room = {
        "room_id": "den",
        "control_port": 6650,
        "http_port": 8050,
        "last_connected_at": None,
    }
    snapshot = {
        "active_rooms": ["den"],
        "satellite_display": {
            "den": {"on": True, "kiosk_alive": False,
                    "brightness": 70, "idle_mode": "art"},
        },
    }
    sat = await sat_api._satellite_for(room, snapshot, {}, {})
    assert sat.display is not None
    assert sat.display.on is True
    assert sat.display.kiosk_alive is False
    assert sat.display.brightness == 70
    assert sat.display.idle_mode == "art"


# ─── display_status frame + set_display sender ────────────────────────────


class _FakeAppStateFull(_FakeAppState):
    def __init__(self) -> None:
        super().__init__()
        self.satellite_display: dict[str, dict] = {}


class _FakeWSFull(_FakeWS):
    def __init__(self) -> None:
        super().__init__()
        self.app = _FakeApp()
        self.app.state = _FakeAppStateFull()


@pytest.mark.asyncio
async def test_display_status_cached_and_clamped() -> None:
    ws = _FakeWSFull()
    session = StreamSession(ws, "den")  # type: ignore[arg-type]
    await session._on_control({
        "type": "display_status", "on": True, "kiosk_alive": True,
        "brightness": 250, "idle_mode": "blank",
    })
    cached = ws.app.state.satellite_display["den"]
    assert cached == {
        "on": True, "kiosk_alive": True, "brightness": 100,
        "idle_mode": "blank",
    }


@pytest.mark.asyncio
async def test_display_status_malformed_ignored() -> None:
    ws = _FakeWSFull()
    session = StreamSession(ws, "den")  # type: ignore[arg-type]
    await session._on_control({"type": "display_status", "brightness": "max"})
    assert "den" not in ws.app.state.satellite_display


@pytest.mark.asyncio
async def test_set_display_sends_frame_and_updates_cache() -> None:
    import json as _json

    ws = _FakeWSFull()
    session = StreamSession(ws, "den")  # type: ignore[arg-type]
    ws.app.state.satellite_display["den"] = {
        "on": True, "kiosk_alive": True, "brightness": None,
        "idle_mode": "clock",
    }
    await session.set_display("off")
    frames = [_json.loads(f) for f in ws.sent]
    assert {"type": "set_display", "action": "off"} in frames
    assert ws.app.state.satellite_display["den"]["on"] is False
    # restart_kiosk sends but never flips the cached power state.
    await session.set_display("restart_kiosk")
    assert ws.app.state.satellite_display["den"]["on"] is False


# ─── admin display endpoint + wake-record gate ────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_admin_display_endpoint_paths() -> None:
    transport = ASGITransport(app=core_app)
    async with core_app.router.lifespan_context(core_app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 503 — nothing connected.
            core_app.state.active_sessions = {}
            r = await client.post(
                "/v1/admin/satellite/display",
                json={"room_id": "den", "action": "off"},
            )
            assert r.status_code == 503

            # 404 — some other room connected.
            fake = type(
                "FakeSession",
                (),
                {"room_id": "kitchen", "set_display": AsyncMock()},
            )()
            core_app.state.active_sessions = {"kitchen": fake}
            r = await client.post(
                "/v1/admin/satellite/display",
                json={"room_id": "den", "action": "off"},
            )
            assert r.status_code == 404

            # 409 — connected but not a video satellite.
            core_app.state.satellite_sat_type = {"kitchen": "voice"}
            r = await client.post(
                "/v1/admin/satellite/display",
                json={"room_id": "kitchen", "action": "off"},
            )
            assert r.status_code == 409

            # 200 — video room: frame sent + audit row written.
            core_app.state.satellite_sat_type = {"kitchen": "video"}
            r = await client.post(
                "/v1/admin/satellite/display",
                json={"room_id": "kitchen", "action": "restart_kiosk"},
            )
            assert r.status_code == 200, r.text
            fake.set_display.assert_awaited_once_with("restart_kiosk")

            # 422 — unknown action refused by the body model.
            r = await client.post(
                "/v1/admin/satellite/display",
                json={"room_id": "kitchen", "action": "brighter"},
            )
            assert r.status_code == 422
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    "SELECT transcript FROM intents_log "
                    "WHERE room_id = 'kitchen' "
                    "ORDER BY id DESC LIMIT 1"
                )
            )
        ).first()
    assert row is not None and row[0] == "[ui] display restart_kiosk"


@requires_db
@pytest.mark.asyncio
async def test_wake_record_refused_for_mic_disabled_room() -> None:
    transport = ASGITransport(app=core_app)
    async with core_app.router.lifespan_context(core_app):
        fake = type("FakeSession", (), {"room_id": "den"})()
        core_app.state.active_sessions = {"den": fake}
        core_app.state.satellite_mic_enabled = {"den": False}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/wake/record/start",
                json={"room_id": "den", "wake_word_id": 1},
            )
    assert r.status_code == 400
    assert "voice input disabled" in r.json()["detail"]
