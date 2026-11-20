"""CORE-43 (round 6): a wake model trained on the household's voices is
served only to this server's own rooms, its household devices and an admin.

The card's proof of concept: a host on the LAN with no credential reads
``/v1/wake-models/manifest`` and then GETs ``domovoi_v1_tuned.onnx``. The
manifest stays readable (names and digests: a room checks its cache
against it before it has a session); the body is now refused.

DB-free: the core app over ASGI, with the auth primitives faked
(:func:`domovoi.tests.auth_testkit.install_fake_db`).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import main, wake_default, wake_model_access
from domovoi.tests.auth_testkit import install_fake_db

GUEST = "192.168.1.77"
ROOM = "192.168.1.41"
TUNED = "domovoi_v1_tuned.onnx"


@pytest.fixture
def models(tmp_path, monkeypatch):
    d = tmp_path / "wake_models"
    d.mkdir()
    (d / TUNED).write_bytes(b"household-shaped weights")
    (d / "porch_cat.onnx").write_bytes(b"a word trained here")
    published = wake_default.PUBLISHED["domovoi_v1"].file
    (d / published).write_bytes(b"the published word")
    monkeypatch.setattr(main.settings, "wake_models_dir", str(d))
    wake_model_access.forget_room_peers()
    yield {"published": published}
    wake_model_access.forget_room_peers()


def client(host: str, **kw) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=main.app, client=(host, 50000)),
        base_url="http://127.0.0.1:6370", **kw,
    )


@pytest.mark.asyncio
async def test_a_lan_host_with_no_credential_can_not_take_the_tuned_model(models, monkeypatch):
    install_fake_db(monkeypatch, admin=True)
    async with client(GUEST) as c:
        manifest = (await c.get("/v1/wake-models/manifest")).json()
        assert TUNED in manifest            # the list still says it exists
        r = await c.get(f"/v1/wake-models/{TUNED}")
        assert r.status_code == 403, r.text
        assert b"household-shaped" not in r.content
        assert (await c.get("/v1/wake-models/porch_cat.onnx")).status_code == 403
        # A published word is public anyway.
        r = await c.get(f"/v1/wake-models/{models['published']}")
        assert r.status_code == 200 and r.content == b"the published word"


@pytest.mark.asyncio
async def test_a_room_that_passed_pairing_gets_it_from_its_own_address(models, monkeypatch):
    install_fake_db(monkeypatch, admin=True)
    room_socket = SimpleNamespace(client=SimpleNamespace(host=ROOM, port=40000))
    wake_model_access.remember_room_peer(room_socket)
    async with client(ROOM) as c:
        r = await c.get(f"/v1/wake-models/{TUNED}")
    assert r.status_code == 200 and r.content == b"household-shaped weights"
    assert r.headers["cache-control"] == "no-store"
    async with client(GUEST) as c:
        assert (await c.get(f"/v1/wake-models/{TUNED}")).status_code == 403


@pytest.mark.asyncio
async def test_a_connected_rooms_address_counts(models, monkeypatch):
    install_fake_db(monkeypatch, admin=True)
    sess = SimpleNamespace(ws=SimpleNamespace(client=SimpleNamespace(host=ROOM, port=1)))
    monkeypatch.setattr(main.app.state, "active_sessions", {"kitchen": sess}, raising=False)
    async with client(ROOM) as c:
        assert (await c.get(f"/v1/wake-models/{TUNED}")).status_code == 200


@pytest.mark.asyncio
async def test_a_household_device_or_an_admin_may_download_it(models, monkeypatch):
    install_fake_db(monkeypatch, admin=True, sessions={"admin-bearer"}, device_token="house-token-1234")
    async with client(GUEST) as c:
        r = await c.get(f"/v1/wake-models/{TUNED}", headers={"X-Device-Token": "house-token-1234"})
        assert r.status_code == 200
        r = await c.get(f"/v1/wake-models/{TUNED}", headers={"Authorization": "Bearer admin-bearer"})
        assert r.status_code == 200
        r = await c.get(f"/v1/wake-models/{TUNED}", headers={"X-Device-Token": "wrong-token-9999"})
        assert r.status_code == 403


def test_remembered_rooms_expire_and_are_bounded(monkeypatch):
    wake_model_access.forget_room_peers()
    for i in range(wake_model_access.ROOM_PEER_MAX + 5):
        wake_model_access.remember_room_peer(SimpleNamespace(client=SimpleNamespace(host=f"10.0.{i // 250}.{i % 250}")))
    assert len(wake_model_access._ROOM_PEERS) == wake_model_access.ROOM_PEER_MAX
    assert not wake_model_access.is_room_peer("10.0.0.0")       # the oldest went first
    monkeypatch.setattr(wake_model_access, "ROOM_PEER_TTL_SEC", -1.0)
    assert not wake_model_access.is_room_peer("10.0.1.0")
    wake_model_access.forget_room_peers()
