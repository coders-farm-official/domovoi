"""A room name in a URL never provisions a room (CORE-13, audit A1-01).

`GET /v1/admin/music/queue/{room_id}` was the one music route with no auth
dependency, and the first thing it did was `_ensure_room_mpd(room_id)`:
for a name the house had never seen that allocated ports, inserted an
`mpd_rooms` row and started a docker container that restarts on every
boot. Any LAN host could mint as many as it liked with a curl loop.

Now the read is on the device tier's read half (token, Bearer or the
dashboard cookie), and `_ensure_room_mpd` answers 404 for a room with no
`mpd_rooms`, pairing or inventory row, whichever route calls it — rooms
are created by a satellite's accepted hello, never by an HTTP path.

DB-free: the gate is driven with the auth primitives faked, and the room
lookup is replaced where it would reach Postgres. The SQL itself is
covered by the ``requires_db`` test at the end.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from domovoi import admin_auth
from domovoi import main as core_main
from domovoi.config import settings
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import COOKIE, HEADER, bearer, install_fake_db
from domovoi.tests.conftest import requires_db

ADMIN_TOKEN = "admin-token"
DEVICE_TOKEN = "device-token"
QUEUE = "/v1/admin/music/queue/zz-audit-probe"


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=core_app, raise_app_exceptions=False),
        base_url="http://test",
    )


def _gates(method: str, path: str) -> list:
    from domovoi.tests.route_walk import iter_route_contexts

    def calls(dependant):
        for dep in getattr(dependant, "dependencies", ()) if dependant else ():
            if dep.call is not None:
                yield dep.call
            yield from calls(dep)

    for rc in iter_route_contexts(core_app.routes):
        if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or set()):
            return list(calls(getattr(rc, "dependant", None)))
    raise AssertionError(f"{method} {path} is not a core route")


@pytest.fixture
def provisioning(monkeypatch):
    """A real (non-stub) core as far as `_ensure_room_mpd` can tell, with
    `ensure_room` recorded instead of run and no rooms provisioned yet."""
    from domovoi import mpd_provisioner
    from domovoi.clients import mpd as mpd_module

    calls: list[str] = []

    async def fake_ensure_room(room_id):
        calls.append(room_id)
        return (6600, 8001)

    monkeypatch.setattr(settings, "use_stubs", False)
    monkeypatch.setattr(mpd_provisioner, "ensure_room", fake_ensure_room)
    monkeypatch.setattr(mpd_module, "_room_ports", {})
    return calls


def _known(monkeypatch, rooms: set[str]) -> list[str]:
    asked: list[str] = []

    async def fake_known(room_id):
        asked.append(room_id)
        return room_id in rooms

    monkeypatch.setattr(core_main, "_room_is_known", fake_known)
    return asked


# ─── The gate ──────────────────────────────────────────────────────────────


def test_the_queue_read_is_on_the_device_read_tier() -> None:
    gates = _gates("GET", "/v1/admin/music/queue/{room_id}")
    assert admin_auth.require_device_read in gates


@pytest.mark.asyncio
async def test_the_queue_read_refuses_a_caller_with_no_credential(
    monkeypatch, provisioning
) -> None:
    """The audit's request, after setup: no header at all. Refused at the
    gate, so nothing is looked up and nothing is started."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    asked = _known(monkeypatch, set())
    async with _client() as c:
        r = await c.get(QUEUE)
    assert r.status_code == 401, r.text
    assert asked == [] and provisioning == []


@pytest.mark.asyncio
async def test_the_queue_read_takes_the_token_the_bearer_or_the_cookie(
    monkeypatch, provisioning
) -> None:
    """Every credential the dashboard and the app hold still reads it (the
    room is unknown here, so each answer is the 404 that follows the
    gate)."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    _known(monkeypatch, set())
    async with _client() as c:
        r = await c.get(QUEUE, headers={HEADER: DEVICE_TOKEN})
        assert r.status_code == 404 and "no room named" in r.json()["detail"]
        assert (await c.get(QUEUE, headers=bearer(ADMIN_TOKEN))).status_code == 404
    async with _client() as c:
        c.cookies.set(COOKIE, ADMIN_TOKEN)
        assert (await c.get(QUEUE)).status_code == 404
    assert provisioning == []


# ─── No HTTP route provisions a new room ──────────────────────────────────


@pytest.mark.asyncio
async def test_an_unknown_room_is_404_and_nothing_is_provisioned(
    monkeypatch, provisioning
) -> None:
    asked = _known(monkeypatch, {"kitchen"})
    with pytest.raises(HTTPException) as exc:
        await core_main._ensure_room_mpd("zz-audit-probe")
    assert exc.value.status_code == 404
    assert asked == ["zz-audit-probe"]
    assert provisioning == []


@pytest.mark.asyncio
async def test_a_known_room_still_gets_its_player_started(monkeypatch, provisioning) -> None:
    """The self-healing cast the helper exists for is unchanged."""
    _known(monkeypatch, {"kitchen"})
    await core_main._ensure_room_mpd("kitchen")
    assert provisioning == ["kitchen"]


@pytest.mark.asyncio
async def test_a_room_provisioned_this_boot_needs_no_lookup(monkeypatch, provisioning) -> None:
    from domovoi.clients import mpd as mpd_module

    monkeypatch.setattr(mpd_module, "_room_ports", {"kitchen": (6600, 8001)})

    async def no_db():  # pragma: no cover — must not be reached
        raise AssertionError("looked up a room the port map already knows")

    monkeypatch.setattr(core_main, "session_scope", no_db)
    assert await core_main._room_is_known("kitchen") is True


@pytest.mark.asyncio
async def test_a_lookup_that_fails_is_refused_not_waved_through(monkeypatch, provisioning) -> None:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def down():
        raise ConnectionError("database down")
        yield  # pragma: no cover

    monkeypatch.setattr(core_main, "session_scope", down)
    with pytest.raises(HTTPException) as exc:
        await core_main._ensure_room_mpd("zz-audit-probe")
    assert exc.value.status_code == 503
    assert provisioning == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/admin/music/play-track", {"room_id": "zz-audit-probe", "track_id": 1}),
        ("/v1/admin/music/queue/zz-audit-probe/add", {"track_ids": [1]}),
        ("/v1/admin/music/queue/zz-audit-probe/clear", {}),
    ],
)
async def test_the_device_tier_music_routes_cannot_mint_a_room_either(
    monkeypatch, provisioning, path, body
) -> None:
    """The audit's secondary: a household-token holder could do the same
    through the play and queue routes. They share the helper."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    _known(monkeypatch, set())

    from contextlib import asynccontextmanager

    class _Rows:
        def first(self):
            return (1, "a.mp3", "A", "B")

        def all(self):
            return [self.first()]

    class _S:
        async def execute(self, *_a, **_k):
            return _Rows()

    @asynccontextmanager
    async def fake_scope():
        yield _S()

    monkeypatch.setattr(core_main, "session_scope", fake_scope)
    async with _client() as c:
        r = await c.post(path, json=body, headers={HEADER: DEVICE_TOKEN})
    assert r.status_code == 404, r.text
    assert "no room named 'zz-audit-probe'" in r.json()["detail"]
    assert provisioning == []


# ─── DB-backed: the lookup itself ─────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_room_is_known_reads_the_three_tables() -> None:
    from sqlalchemy import text

    from domovoi.db.repositories import SatellitesRepository
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await s.execute(text(
            "DELETE FROM mpd_rooms WHERE room_id IN ('zz-k1','zz-k2','zz-k3','zz-none')"
        ))
        await s.execute(text(
            "DELETE FROM satellite_pairings WHERE room_id IN ('zz-k1','zz-k2','zz-k3','zz-none')"
        ))
        await s.execute(text(
            "DELETE FROM satellites WHERE room_id IN ('zz-k1','zz-k2','zz-k3','zz-none')"
        ))
        await s.execute(text(
            "INSERT INTO mpd_rooms (room_id, control_port, http_port, container_name) "
            "VALUES ('zz-k1', 16600, 18001, 'domovoi-mpd-zz-k1')"
        ))
        await s.execute(text(
            "INSERT INTO satellite_pairings (room_id, token_hash) VALUES ('zz-k2', 'h')"
        ))
        await s.execute(text(
            "INSERT INTO satellites (room_id, adopted_via) VALUES ('zz-k3', 'manual')"
        ))
    try:
        async with session_scope() as s:
            repo = SatellitesRepository(s)
            assert await repo.room_is_known("zz-k1") is True
            assert await repo.room_is_known("zz-k2") is True
            assert await repo.room_is_known("zz-k3") is True
            assert await repo.room_is_known("zz-none") is False
    finally:
        async with session_scope() as s:
            for table in ("mpd_rooms", "satellite_pairings", "satellites"):
                await s.execute(text(
                    f"DELETE FROM {table} WHERE room_id IN ('zz-k1','zz-k2','zz-k3')"
                ))
