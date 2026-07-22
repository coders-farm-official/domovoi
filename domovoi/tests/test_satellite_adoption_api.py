"""USB-adoption web API — pending scan + adopt flow, no hardware.

Fake volumes are plain tmp dirs injected via ``SATELLITE_ADOPTION_SCAN_DIRS``
(the same dev harness a user can point at a real USB stick). The core admin
hops (``preseed`` / rollback delete) are monkeypatched — the core side has
its own suite (test_satellite_preseed).

Covers: pending list (valid / corrupt / non-setup ignored), route not
shadowed by ``/{room_id}``, adopt happy path (checksum-valid provision file,
nonce echo, secrets never logged), 409 duplicate room, 410 vanished device
(+ preseed rollback on a failed write), and the `waiting` satellite in the
merged roster.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import web.backend.api.satellites as sat_api
import web.backend.satellite_adoption as adoption
from domovoi.db.repositories import SatellitesRepository
from domovoi.db.session import engine, session_scope
from domovoi.tests.conftest import requires_db
from satellite import provisioning_protocol as proto
from web.backend.main import app

pytestmark = requires_db

NONCE = "9f3ab2c47d1e08aa"


def _run(coro):
    """Run a coroutine from sync test code. Fine with this repo's NullPool
    engine — no pooled connection outlives the throwaway loop."""
    return asyncio.run(coro)


def _truncate() -> None:
    async def _t():
        async with engine.begin() as conn:
            await conn.execute(
                text("TRUNCATE satellites, satellite_pairings CASCADE")
            )

    _run(_t())


def _write_device_info(vol: Path, **overrides) -> dict:
    info = proto.build_device_info(
        nonce=NONCE,
        mac="b8:27:eb:aa:bb:cc",
        board="raspberry_pi_zero_2_w",
        model="Raspberry Pi Zero 2 W Rev 1.0",
        profiles_supported=["respeaker_2mic_hat", "xvf3800_usb"],
    )
    info.update(overrides)
    vol.mkdir(parents=True, exist_ok=True)
    (vol / proto.DEVICE_INFO_NAME).write_text(json.dumps(info), encoding="utf-8")
    return info


@pytest.fixture
def volume(tmp_path, monkeypatch):
    """One fake gadget volume, wired into the scanner with a cold cache."""
    vol = tmp_path / "fakevol"
    _write_device_info(vol)
    monkeypatch.setenv("SATELLITE_ADOPTION_SCAN_DIRS", str(vol))
    adoption.invalidate_cache()
    yield vol
    adoption.invalidate_cache()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_tables():
    _truncate()
    yield
    _truncate()


def test_pending_lists_valid_volume(client, volume):
    r = client.get("/api/satellites/pending")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    p = items[0]
    assert p["pending_id"] == NONCE
    assert p["model"] == "Raspberry Pi Zero 2 W Rev 1.0"
    assert p["sat_type"] == "voice"
    assert p["parse_error"] is None
    assert p["already_adopted_as"] is None


def test_pending_surfaces_corrupt_volume(client, tmp_path, monkeypatch):
    vol = tmp_path / "corrupt"
    vol.mkdir()
    (vol / proto.DEVICE_INFO_NAME).write_text('{"domovoi_setup": 99}', encoding="utf-8")
    monkeypatch.setenv("SATELLITE_ADOPTION_SCAN_DIRS", str(vol))
    adoption.invalidate_cache()
    r = client.get("/api/satellites/pending")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["parse_error"] is not None
    assert items[0]["pending_id"].startswith("drive:")


def test_pending_ignores_plain_volume(client, tmp_path, monkeypatch):
    vol = tmp_path / "justfiles"
    vol.mkdir()
    (vol / "holiday-photos.txt").write_text("not a satellite", encoding="utf-8")
    monkeypatch.setenv("SATELLITE_ADOPTION_SCAN_DIRS", str(vol))
    adoption.invalidate_cache()
    r = client.get("/api/satellites/pending")
    assert r.status_code == 200
    assert r.json() == []


def test_pending_route_not_shadowed_by_room_id(client, monkeypatch, tmp_path):
    """GET /api/satellites/pending must hit the pending route, never be
    captured as room_id='pending' by the detail route (404)."""
    monkeypatch.setenv("SATELLITE_ADOPTION_SCAN_DIRS", str(tmp_path / "empty"))
    adoption.invalidate_cache()
    r = client.get("/api/satellites/pending")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def _fake_admin_hops(monkeypatch, token="c" * 64, status=200):
    calls = {"preseed": [], "delete": []}

    async def fake_post(path, body=None, timeout=30.0, headers=None):
        calls["preseed"].append((path, body))
        if status != 200:
            return status, {"detail": "refused"}
        return 200, {"room_id": body and path.split("/")[4], "token": token, "rotated": False}

    async def fake_delete(path, timeout=10.0, headers=None):
        calls["delete"].append(path)
        return 200, {"deleted": True}

    monkeypatch.setattr(sat_api, "post_admin", fake_post)
    monkeypatch.setattr(sat_api, "delete_admin", fake_delete)
    return calls


ADOPT_BODY = {
    "room_id": "den",
    "room_label": "Living Room",
    "wifi_ssid": "HomeNet",
    "wifi_psk": "hunter2hunter2",
    "wifi_country": "US",
    "device_profile": "respeaker_2mic_hat",
}


def test_adopt_happy_path_writes_valid_provision(client, volume, monkeypatch, caplog):
    calls = _fake_admin_hops(monkeypatch)
    client.get("/api/satellites/pending")  # populate the nonce→mount cache
    with caplog.at_level(logging.INFO):
        r = client.post(f"/api/satellites/pending/{NONCE}/adopt", json=ADOPT_BODY)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "provisioned"

    # The provision file landed, checksum-valid, nonce-echoed, and the
    # device-side validator accepts it end-to-end.
    doc = json.loads((volume / proto.PROVISION_NAME).read_text(encoding="utf-8"))
    payload = proto.validate_provision(doc, NONCE)
    assert payload["room_id"] == "den"
    assert payload["pairing_token"] == "c" * 64
    assert payload["wifi"]["ssid"] == "HomeNet"
    assert payload["device_profile"] == "respeaker_2mic_hat"
    assert payload["domovoi_url"].startswith("ws://")
    assert "127.0.0.1" not in payload["domovoi_url"]

    # The preseed hop carried the device metadata + label.
    (path, body), = calls["preseed"]
    assert path == "/v1/admin/satellites/den/pairing/preseed"
    assert body["mac"] == "b8:27:eb:aa:bb:cc"
    assert body["room_label"] == "Living Room"

    # Secrets never reach the logs.
    assert "hunter2hunter2" not in caplog.text
    assert "c" * 64 not in caplog.text


def test_adopt_409_on_existing_room(client, volume, monkeypatch):
    _fake_admin_hops(monkeypatch)

    async def fake_rooms():
        return [{"room_id": "den", "control_port": 6650, "http_port": 8050,
                 "last_connected_at": None}]

    monkeypatch.setattr(sat_api, "_list_rooms", fake_rooms)
    client.get("/api/satellites/pending")
    r = client.post(f"/api/satellites/pending/{NONCE}/adopt", json=ADOPT_BODY)
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_adopt_410_when_device_vanished(client, volume, monkeypatch):
    _fake_admin_hops(monkeypatch)
    client.get("/api/satellites/pending")
    (volume / proto.DEVICE_INFO_NAME).unlink()
    r = client.post(f"/api/satellites/pending/{NONCE}/adopt", json=ADOPT_BODY)
    assert r.status_code == 410


def test_adopt_rolls_back_preseed_on_write_failure(client, volume, monkeypatch):
    calls = _fake_admin_hops(monkeypatch)

    def boom(mount, doc):
        raise OSError("device yanked")

    monkeypatch.setattr(adoption, "write_provision", boom)
    client.get("/api/satellites/pending")
    r = client.post(f"/api/satellites/pending/{NONCE}/adopt", json=ADOPT_BODY)
    assert r.status_code == 410
    assert calls["delete"] == ["/v1/admin/satellites/den"]


def test_waiting_room_in_merged_roster(client):
    """An adopted-but-never-connected satellite (inventory row, no
    mpd_rooms row) lists as status='waiting' with null ports."""

    async def _seed():
        async with session_scope() as s:
            await SatellitesRepository(s).preseed_upsert(
                "attic", sat_type="video", room_label="Upstairs",
                hardware="Radxa Zero 3W", mac="aa:aa:aa:aa:aa:aa",
            )

    _run(_seed())
    r = client.get("/api/satellites")
    assert r.status_code == 200
    rooms = {x["room_id"]: x for x in r.json()}
    assert "attic" in rooms
    attic = rooms["attic"]
    assert attic["status"] == "waiting"
    assert attic["mpd_ports"] is None
    assert attic["now_playing"] is None
    assert attic["sat_type"] == "video"
    assert attic["room_label"] == "Upstairs"


def test_pending_marks_already_adopted_mac(client, volume):
    """A re-plugged device whose MAC matches an inventory row is flagged."""

    async def _seed():
        async with session_scope() as s:
            await SatellitesRepository(s).preseed_upsert(
                "den", mac="b8:27:eb:aa:bb:cc"
            )

    _run(_seed())
    adoption.invalidate_cache()
    r = client.get("/api/satellites/pending")
    assert r.status_code == 200
    assert r.json()[0]["already_adopted_as"] == "den"
