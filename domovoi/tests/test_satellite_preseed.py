"""USB-adoption preseed — the core side of the adopt flow.

Covers:
  * ``POST /v1/admin/satellites/{room}/pairing/preseed`` — token minted,
    sha256 stored, inventory row upserted; the RAW token authenticates the
    device's first connect as pairing case 2 (match, not TOFU claim),
    including under strict pairing;
  * duplicate preseed 409; ``force`` rotates (old token then refused);
  * room-id charset and sat_type validation (422);
  * ``DELETE /v1/admin/satellites/{room}`` — removes inventory + pairing,
    409 for a provisioned (mpd_rooms) room;
  * ``POST /v1/admin/satellites/{room}/label`` — set/clear.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.config import settings
from domovoi.db.session import engine, session_scope
from domovoi.main import app as core_app
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import requires_db

pytestmark = requires_db


@pytest_asyncio.fixture(autouse=True)
async def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path / "cfg")
    admin_auth.LOGIN_BACKOFF.reset()
    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE satellite_pairings, satellites, admin_auth, admin_sessions CASCADE")
        )
    yield
    admin_auth.LOGIN_BACKOFF.reset()
    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE satellite_pairings, satellites, admin_auth, admin_sessions CASCADE")
        )


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        pass


async def _preseed(client: AsyncClient, room: str, **body):
    return await client.post(
        f"/v1/admin/satellites/{room}/pairing/preseed", json=body
    )


@pytest.mark.asyncio
async def test_preseed_creates_rows_and_token_authenticates() -> None:
    transport = ASGITransport(app=core_app)
    async with core_app.router.lifespan_context(core_app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await _preseed(
                client, "den",
                sat_type="video", hardware="Radxa Zero 3W",
                board="radxa_zero3w", mac="AA:BB:CC:11:22:33",
                room_label="Living Room",
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rotated"] is False
    token = body["token"]
    assert len(token) == 64

    async with session_scope() as s:
        pair = (
            await s.execute(text("SELECT token_hash FROM satellite_pairings WHERE room_id='den'"))
        ).first()
        meta = (
            await s.execute(
                text(
                    "SELECT sat_type, hardware, mac, room_label, adopted_via, adopted_at "
                    "FROM satellites WHERE room_id='den'"
                )
            )
        ).first()
    assert pair is not None and pair[0] == admin_auth.token_sha256(token)
    assert meta is not None
    assert meta[0] == "video"
    assert meta[2] == "aa:bb:cc:11:22:33"   # MAC lowercased
    assert meta[3] == "Living Room"
    assert meta[4] == "usb" and meta[5] is not None

    # The raw token authenticates the device's FIRST connect as case 2
    # (hash match) — no TOFU claim, so it also passes under strict mode.
    session = StreamSession(_FakeWS(), "den")  # type: ignore[arg-type]
    assert await session._validate_pairing({"pairing_token": token}) is True
    orig = settings.satellite_pairing_strict
    settings.satellite_pairing_strict = True
    try:
        session2 = StreamSession(_FakeWS(), "den")  # type: ignore[arg-type]
        assert await session2._validate_pairing({"pairing_token": token}) is True
    finally:
        settings.satellite_pairing_strict = orig


@pytest.mark.asyncio
async def test_preseed_conflicts_and_force_rotation() -> None:
    transport = ASGITransport(app=core_app)
    async with core_app.router.lifespan_context(core_app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = await _preseed(client, "kitchen")
            assert first.status_code == 200
            token_a = first.json()["token"]

            dup = await _preseed(client, "kitchen")
            assert dup.status_code == 409

            forced = await _preseed(client, "kitchen", force=True)
            assert forced.status_code == 200
            assert forced.json()["rotated"] is True
            token_b = forced.json()["token"]

    # After rotation the OLD token is a case-3 mismatch (refused); the new
    # one authenticates.
    stale = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    assert await stale._validate_pairing({"pairing_token": token_a}) is False
    fresh = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    assert await fresh._validate_pairing({"pairing_token": token_b}) is True


@pytest.mark.asyncio
async def test_preseed_validation() -> None:
    transport = ASGITransport(app=core_app)
    async with core_app.router.lifespan_context(core_app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            bad_room = await _preseed(client, "Kitchen!")
            assert bad_room.status_code == 422
            bad_type = await _preseed(client, "den", sat_type="toaster")
            assert bad_type.status_code == 422


@pytest.mark.asyncio
async def test_delete_satellite_and_provisioned_guard() -> None:
    transport = ASGITransport(app=core_app)
    async with core_app.router.lifespan_context(core_app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await _preseed(client, "den")
            r = await client.delete("/v1/admin/satellites/den")
            assert r.status_code == 200 and r.json()["deleted"] is True
            async with session_scope() as s:
                left = (
                    await s.execute(
                        text(
                            "SELECT (SELECT count(*) FROM satellites WHERE room_id='den') + "
                            "(SELECT count(*) FROM satellite_pairings WHERE room_id='den')"
                        )
                    )
                ).scalar()
            assert left == 0

            # A provisioned room (mpd_rooms row) refuses adoption rollback.
            async with session_scope() as s:
                await s.execute(
                    text(
                        "INSERT INTO mpd_rooms (room_id, control_port, http_port, container_name) "
                        "VALUES ('den_prov', 6699, 8099, 'domovoi-mpd-den-prov-test') "
                        "ON CONFLICT (room_id) DO NOTHING"
                    )
                )
            try:
                r = await client.delete("/v1/admin/satellites/den_prov")
                assert r.status_code == 409
            finally:
                async with session_scope() as s:
                    await s.execute(
                        text("DELETE FROM mpd_rooms WHERE room_id='den_prov'")
                    )


@pytest.mark.asyncio
async def test_room_label_set_and_clear() -> None:
    transport = ASGITransport(app=core_app)
    async with core_app.router.lifespan_context(core_app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/satellites/kitchen/label",
                json={"room_label": "  Ground Floor  "},
            )
            assert r.status_code == 200
            assert r.json()["room_label"] == "Ground Floor"
            r = await client.post(
                "/v1/admin/satellites/kitchen/label", json={"room_label": None}
            )
            assert r.status_code == 200 and r.json()["room_label"] is None
    async with session_scope() as s:
        row = (
            await s.execute(
                text("SELECT room_label FROM satellites WHERE room_id='kitchen'")
            )
        ).first()
    assert row is not None and row[0] is None
