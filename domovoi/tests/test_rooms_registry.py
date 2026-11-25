"""Rooms with several satellites, slice 2: identity, sessions and the room
registry (domovoi/room_devices.py), approvals into a room, moves and the
room drawer's API.

The DB fixture gives NO satellite an id equal to its room's (like
test_rooms_model.py), so a place that still mixes a device up with its
room shows up here.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth, room_devices, streaming
from domovoi.admin_auth import token_sha256
from domovoi.config import settings
from domovoi.db.repositories import (
    RoomsRepository,
    SatelliteApprovalRepository,
    SatellitePairingRepository,
    SatellitesRepository,
    VoiceProfilesRepository,
    ensure_satellite,
)
from domovoi.db.session import session_scope
from domovoi.main import app as core_app
from domovoi.room_playout.service import InMemoryRoomRegistry, RoomPlayout, get_playout
from domovoi.satellite_abilities import Abilities
from domovoi.streaming import StreamSession
from domovoi.tests.auth_testkit import (
    HEADER,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    install_fake_db,
    web_client,
)
from domovoi.tests.conftest import requires_db
from domovoi.tests.route_walk import iter_route_contexts
from web.backend.main import app as web_app

ADMIN_TOKEN = "admin-token"
DEVICE_TOKEN = "device-token"


# ─── test doubles ──────────────────────────────────────────────────────────


class _WS:
    def __init__(self, app: Any = None) -> None:
        self.app = app or SimpleNamespace(state=SimpleNamespace())
        self.sent: list[dict] = []
        self.closed: int | None = None
        self.client = SimpleNamespace(host="192.168.1.41")
        self.headers: dict[str, str] = {}

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self, code: int = 1000) -> None:
        self.closed = code


def _fake(sid: str, room: str, **kw: Any) -> SimpleNamespace:
    kw.setdefault("abilities", Abilities())
    return SimpleNamespace(satellite_id=sid, room_id=room, **kw)


def _app() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace())


# ─── the registry, no database ─────────────────────────────────────────────


def test_a_room_keeps_one_speaker_and_hands_it_over() -> None:
    app = _app()
    reg = room_devices.registry(app)
    a, b = _fake("sat_a", "kitchen"), _fake("sat_b", "kitchen")
    assert reg.register(a) is None
    assert reg.register(b) is None
    # The first device speaks for the room; the second doesn't take it.
    assert app.state.active_sessions == {"kitchen": a}
    assert set(app.state.active_satellites) == {"sat_a", "sat_b"}
    assert reg.devices_in("kitchen") == ["sat_a", "sat_b"]
    assert reg.is_shared("kitchen") and reg.room_of("sat_b") == "kitchen"
    assert reg.session_for("sat_b") is b and reg.session_for("kitchen") is a
    # A reconnect of the same device replaces it, and the old one is handed back.
    a2 = _fake("sat_a", "kitchen")
    assert reg.register(a2) is a
    assert app.state.active_sessions["kitchen"] is a2
    assert reg.unregister(a) is False      # the stale one changes nothing
    assert reg.unregister(a2) is True
    assert app.state.active_sessions == {"kitchen": b}
    assert reg.unregister(b) is True
    assert app.state.active_sessions == {}


def test_the_registry_reads_a_swapped_session_map() -> None:
    app = _app()
    reg = room_devices.registry(app)
    app.state.active_sessions = {"den": "x"}
    assert reg.sessions == {"den": "x"}
    assert room_devices.registry(app) is reg


def test_listeners_speakers_screens_and_aec() -> None:
    reg = room_devices.registry(_app())
    pi = _fake("pi", "kitchen", abilities=Abilities(declared=True,
                                                    names=frozenset({"full_duplex"})))
    phone = _fake("phone", "kitchen", mic_enabled=False, room_display=object(),
                  abilities=Abilities(declared=True, names=frozenset({"room_display"})))
    for s in (pi, phone):
        reg.register(s)
    assert reg.listeners("kitchen") == [pi]
    assert reg.speakers("kitchen") == [phone, pi]
    assert reg.screens("kitchen") == [phone]
    assert reg.aec_device("kitchen") is pi


class _Sink:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_audio(self, frame: bytes) -> None:
        pass

    def send_control(self, msg: dict) -> None:
        self.sent.append(msg)


def test_a_move_rebinds_the_session_and_room_playout() -> None:
    app = _app()
    reg = room_devices.registry(app)
    app.state.room_playout = pl = RoomPlayout(reg, run_tasks=False)
    reg.on_move(pl.rebind)
    a, b = _fake("sat_a", "kitchen"), _fake("sat_b", "kitchen")
    reg.register(a)
    reg.register(b)
    sink = _Sink()
    assert pl.attach("sat_a", sink) == "kitchen"
    assert reg.moved("sat_a", "den") is a
    assert a.room_id == "den"
    assert app.state.active_sessions == {"kitchen": b, "den": a}
    assert pl.speakers("den") == ["sat_a"] and pl.speakers("kitchen") == []
    assert sink.sent[-1]["type"] == "timeline"


def test_get_playout_uses_the_real_registry() -> None:
    app = _app()
    pl = get_playout(app)
    assert pl.registry is room_devices.registry(app)
    assert not isinstance(pl.registry, InMemoryRoomRegistry)


async def test_a_hello_naming_another_device_is_refused() -> None:
    sess = StreamSession(_WS(), "sat_a")  # type: ignore[arg-type]
    await sess._on_control({"type": "hello", "satellite_id": "sat_b"})
    assert sess._pairing_refused and sess.ws.closed == 1008
    assert sess.ws.sent[-1]["reason"] == "satellite_id_mismatch"


# ─── the routes and their tiers, no database ──────────────────────────────

CORE_ROUTES = [
    ("GET", "/v1/admin/rooms", admin_auth.require_device_read),
    ("POST", "/v1/admin/rooms", admin_auth.require_admin_mutation),
    ("PATCH", "/v1/admin/rooms/{room_id}", admin_auth.require_admin_mutation),
    ("DELETE", "/v1/admin/rooms/{room_id}", admin_auth.require_admin_mutation),
    ("POST", "/v1/admin/rooms/{room_id}/merge", admin_auth.require_admin_mutation),
    ("POST", "/v1/admin/rooms/{room_id}/links", admin_auth.require_admin_mutation),
    ("DELETE", "/v1/admin/rooms/{room_id}/links/{other_room_id}", admin_auth.require_admin_mutation),
    ("POST", "/v1/admin/rooms/{room_id}/links/{other_room_id}/dismiss",
     admin_auth.require_admin_mutation),
    ("POST", "/v1/admin/rooms/{room_id}/invite", admin_auth.require_admin_mutation),
    ("POST", "/v1/admin/satellites/{satellite_id}/room", admin_auth.require_admin_mutation),
    ("PATCH", "/v1/admin/satellites/{satellite_id}/name", admin_auth.require_admin_mutation),
    ("GET", "/v1/admin/satellites/approvals", admin_auth.require_admin_read),
    ("POST", "/v1/admin/satellites/approvals/{room_id}/approve",
     admin_auth.require_admin_security),
    ("POST", "/v1/admin/satellites/approvals/{room_id}/reject",
     admin_auth.require_admin_security),
]
WEB_ROUTES = [
    ("GET", "/api/rooms", admin_auth.require_device_read),
    ("POST", "/api/rooms", admin_auth.require_admin_mutation),
    ("PATCH", "/api/rooms/{room_id}", admin_auth.require_admin_mutation),
    ("DELETE", "/api/rooms/{room_id}", admin_auth.require_admin_mutation),
    ("POST", "/api/rooms/{room_id}/merge", admin_auth.require_admin_mutation),
    ("POST", "/api/rooms/{room_id}/links", admin_auth.require_admin_mutation),
    ("DELETE", "/api/rooms/{room_id}/links/{other_room_id}", admin_auth.require_admin_mutation),
    ("POST", "/api/rooms/{room_id}/links/{other_room_id}/dismiss",
     admin_auth.require_admin_mutation),
    ("POST", "/api/satellites/{satellite_id}/room", admin_auth.require_admin_mutation),
    ("PATCH", "/api/satellites/{satellite_id}/name", admin_auth.require_admin_mutation),
    ("GET", "/api/satellites/approvals", admin_auth.require_admin_read),
    ("POST", "/api/satellites/approvals/{room_id}/approve", admin_auth.require_admin_security),
    ("POST", "/api/satellites/approvals/{room_id}/reject", admin_auth.require_admin_security),
]


def _calls(dependant: Any) -> set[Any]:
    out: set[Any] = set()
    for dep in getattr(dependant, "dependencies", ()):
        if dep.call is not None:
            out.add(dep.call)
        out |= _calls(dep)
    return out


def _gates(app: Any, method: str, path: str) -> set[Any]:
    for r in iter_route_contexts(app.routes):
        if getattr(r, "path", None) == path and method in (getattr(r, "methods", None) or ()):
            return _calls(r.dependant)
    raise AssertionError(f"no route {method} {path}")


@pytest.mark.parametrize(("method", "path", "gate"), CORE_ROUTES,
                         ids=[f"{m} {p}" for m, p, _ in CORE_ROUTES])
def test_core_route_wears_its_tier(method, path, gate) -> None:
    assert gate in _gates(core_app, method, path)


@pytest.mark.parametrize(("method", "path", "gate"), WEB_ROUTES,
                         ids=[f"{m} {p}" for m, p, _ in WEB_ROUTES])
def test_web_route_wears_its_tier(method, path, gate) -> None:
    assert gate in _gates(web_app, method, path)


def _sample(path: str) -> str:
    return (path.replace("{room_id}", "kitchen")
            .replace("{other_room_id}", "den")
            .replace("{satellite_id}", "sat_a"))


_BODIES = {
    "/v1/admin/rooms": {"name": "Den"},
    "/v1/admin/rooms/{room_id}": {"name": "Den"},
    "/v1/admin/rooms/{room_id}/merge": {"rooms": ["den"]},
    "/v1/admin/rooms/{room_id}/links": {"room_id": "den"},
    "/v1/admin/satellites/{satellite_id}/room": {"room_id": "den"},
    "/v1/admin/satellites/{satellite_id}/name": {"name": "Kitchen phone"},
    "/v1/admin/satellites/approvals/{room_id}/approve": {"code": "123456"},
}


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=core_app, raise_app_exceptions=False),
                       base_url="http://test")


@pytest.mark.parametrize(("method", "path", "gate"), CORE_ROUTES,
                         ids=[f"{m} {p}" for m, p, _ in CORE_ROUTES])
async def test_core_route_refuses_no_credential_and_takes_its_tier(
    method, path, gate, monkeypatch,
) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN},
                    device_token=DEVICE_TOKEN)
    kw = {"json": _BODIES.get(path, {})} if method in ("POST", "PATCH") else {}
    async with _client() as c:
        nobody = await c.request(method, _sample(path), **kw)
        household = await c.request(method, _sample(path), headers={HEADER: DEVICE_TOKEN}, **kw)
        admin = await c.request(method, _sample(path), headers=bearer(ADMIN_TOKEN), **kw)
    assert nobody.status_code == 401, nobody.text
    if gate is admin_auth.require_device_read:
        assert household.status_code not in (401, 403), household.text
    else:
        assert household.status_code in (401, 403), household.text
    assert admin.status_code not in (401, 403), admin.text


# ─── with the database ─────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def house():
    """kitchen: sat_a (paired) + sat_b (paired, named); den: sat_c (paired).
    No device id equals any room id."""
    tag = uuid.uuid4().hex[:6]
    h = {"kitchen": f"rr_kitchen_{tag}", "den": f"rr_den_{tag}",
         "a": f"rrsat_a_{tag}", "b": f"rrsat_b_{tag}", "c": f"rrsat_c_{tag}",
         "token": "t" * 64}
    async with session_scope() as s:
        rooms, sats = RoomsRepository(s), SatellitesRepository(s)
        await rooms.create(h["kitchen"], "Kitchen")
        await rooms.create(h["den"], "Den")
        for sid, room in ((h["a"], h["kitchen"]), (h["b"], h["kitchen"]), (h["c"], h["den"])):
            await SatellitePairingRepository(s).pair(sid, token_sha256(h["token"] + sid))
            assert await sats.move(sid, room)
            assert await rooms.delete(sid) == "deleted"
        await sats.set_name(h["b"], "Kitchen phone")
    yield h
    ids = [h["a"], h["b"], h["c"], f"rrsat_x_{tag}", f"rrsat_y_{tag}"]
    async with session_scope() as s:
        for t in ("satellite_pairings", "satellite_approvals", "room_battery"):
            await s.execute(text(f"DELETE FROM {t} WHERE satellite_id = ANY(:i)"), {"i": ids})
        await s.execute(text("DELETE FROM voice_profiles WHERE satellite_id = ANY(:i)"),
                        {"i": ids})
        await s.execute(text("DELETE FROM satellites WHERE satellite_id = ANY(:i)"), {"i": ids})
        await s.execute(text("DELETE FROM rooms WHERE room_id LIKE :p OR room_id = ANY(:i)"),
                        {"p": f"%{tag}%", "i": ids})


@pytest_asyncio.fixture
async def admin(_db, monkeypatch):  # noqa: F811
    """An admin Bearer, a clean registry and a test-only playout on the
    core app."""
    monkeypatch.setattr(core_app.state, "room_devices", None, raising=False)
    core_app.state.active_sessions = {}
    core_app.state.active_satellites = {}
    reg = room_devices.registry(core_app)
    pl = RoomPlayout(reg, run_tasks=False)
    monkeypatch.setattr(core_app.state, "room_playout", pl, raising=False)
    reg.on_move(pl.rebind)
    async with web_client() as w:
        token = await claim_admin(w)
    yield bearer(token)
    core_app.state.active_sessions = {}
    core_app.state.active_satellites = {}


def _core() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=core_app), base_url="http://test")


@requires_db
async def test_the_registry_loads_a_house_where_no_device_is_its_room(house) -> None:
    h = house
    reg = room_devices.registry(_app())
    await reg.refresh()
    assert reg.room_of(h["a"]) == h["kitchen"] and reg.room_of(h["c"]) == h["den"]
    assert reg.devices_in(h["kitchen"]) == [h["a"], h["b"]]
    assert reg.devices_in(h["a"]) == [] and reg.room_of(h["kitchen"]) is None
    assert reg.is_shared(h["kitchen"]) and not reg.is_shared(h["den"])


@requires_db
async def test_a_device_binds_to_its_room_not_its_id(house) -> None:
    h = house
    sess = StreamSession(_WS(), h["b"])  # type: ignore[arg-type]
    assert await sess._validate_pairing({"pairing_token": h["token"] + h["b"]})
    assert await sess._bind_room()
    assert (sess.satellite_id, sess.room_id) == (h["b"], h["kitchen"])
    room_devices.registry(sess.ws.app).register(sess)
    info = await sess._room_info()
    assert info == {"room_id": h["kitchen"], "name": "Kitchen", "shared": True, "claim": True}
    assert sess.ws.app.state.active_sessions[h["kitchen"]] is sess


@requires_db
async def test_an_old_client_whose_id_is_its_room_keeps_working() -> None:
    sid = f"rr_legacy_{uuid.uuid4().hex[:6]}"
    try:
        async with session_scope() as s:
            # A pre-1.5.0 device: paired under its room's id, in that room.
            await SatellitePairingRepository(s).pair(sid, token_sha256("k" * 64))
        sess = StreamSession(_WS(), sid)  # type: ignore[arg-type]
        assert await sess._validate_pairing({"pairing_token": "k" * 64})
        assert sess.token_authenticated
        assert await sess._bind_room()
        assert sess.room_id == sid == sess.satellite_id
        assert not sess.knows_rooms
        assert (await sess._room_info())["shared"] is False
    finally:
        async with session_scope() as s:
            await s.execute(text("DELETE FROM satellite_pairings WHERE satellite_id = :r"),
                            {"r": sid})
            await s.execute(text("DELETE FROM satellites WHERE satellite_id = :r"), {"r": sid})
            await s.execute(text("DELETE FROM rooms WHERE room_id = :r"), {"r": sid})


@requires_db
async def test_a_tokenless_device_cannot_join_an_occupied_room(house, monkeypatch) -> None:
    monkeypatch.setattr(settings, "satellite_pairing_strict", False)
    h = house
    # A tokenless socket under the kitchen's own id: the room has devices.
    sess = StreamSession(_WS(), h["kitchen"])  # type: ignore[arg-type]
    assert await sess._validate_pairing({})
    assert not sess.token_authenticated
    assert not await sess._bind_room()
    assert sess.ws.sent[-1]["reason"] == "tokenless_shared_room"
    # On its own it may still be a room.
    lone = f"rrsat_x_{h['kitchen'].rsplit('_', 1)[1]}"
    sess = StreamSession(_WS(), lone)  # type: ignore[arg-type]
    assert await sess._validate_pairing({})
    assert await sess._bind_room() and sess.room_id == lone


@requires_db
async def test_moving_a_live_satellite(house, admin) -> None:
    h = house
    reg = room_devices.registry(core_app)
    new_client = StreamSession(_WS(core_app), h["a"])  # type: ignore[arg-type]
    new_client.room_id, new_client.knows_rooms = h["kitchen"], True
    old_client = StreamSession(_WS(core_app), h["b"])  # type: ignore[arg-type]
    old_client.room_id = h["kitchen"]
    reg.register(new_client)
    reg.register(old_client)
    sink = _Sink()
    core_app.state.room_playout.attach(h["a"], sink)
    async with session_scope() as s:
        await s.execute(text("UPDATE satellites SET output_latency_us = 1200, "
                             "calibrated_at = now() WHERE satellite_id = :s"), {"s": h["a"]})
        people = (await s.execute(text(
            "INSERT INTO people (name) VALUES (:n) RETURNING id"), {"n": f"P {h['a']}"})).scalar_one()
        repo = VoiceProfilesRepository(s)
        emb = np.zeros(4, dtype=np.float32).tobytes()
        enrolled = await repo.add(person_id=people, embedding=emb, model="m",
                                  room_id=h["kitchen"], sample_seconds=1.0,
                                  satellite_id=h["a"])
        await repo.add(person_id=people, embedding=emb, model="m", room_id=h["kitchen"],
                       sample_seconds=1.0, satellite_id=h["a"], learned=True)
        await repo.add(person_id=people, embedding=emb, model="m", room_id=h["kitchen"],
                       sample_seconds=1.0, satellite_id=h["b"], learned=True)
    try:
        async with _core() as c:
            r = await c.post(f"/v1/admin/satellites/{h['a']}/room",
                             json={"room_id": h["den"]}, headers=admin)
            assert r.status_code == 200, r.text
            assert r.json() == {"satellite_id": h["a"], "room_id": h["den"],
                                "from_room_id": h["kitchen"], "moved": True,
                                "learned_samples_dropped": 1, "connected": True}
            # The new client hears where it is now; the socket stays open.
            assert new_client.ws.sent[-1] == {
                "type": "room", "room": {"room_id": h["den"], "name": "Den", "shared": True, "claim": True}}
            assert new_client.ws.closed is None
            assert new_client.room_id == h["den"] and reg.room_of(h["a"]) == h["den"]
            assert core_app.state.active_sessions[h["kitchen"]] is old_client
            assert core_app.state.room_playout.speakers(h["den"]) == [h["a"]]
            # An old client is closed 1001 and comes back into the new room.
            r = await c.post(f"/v1/admin/satellites/{h['b']}/room",
                             json={"new_room_name": "Porch"}, headers=admin)
            assert r.status_code == 200, r.text
            porch = r.json()["room_id"]
            assert old_client.ws.closed == 1001
        async with session_scope() as s:
            a = await SatellitesRepository(s).get(h["a"])
            assert (a["room_id"], a["output_latency_us"], a["calibrated_at"]) == (
                h["den"], None, None)
            left = (await s.execute(text(
                "SELECT id, learned FROM voice_profiles WHERE person_id = :p ORDER BY id"),
                {"p": people})).all()
            assert [(row[0], row[1]) for row in left] == [(enrolled, False)]
            assert (await RoomsRepository(s).get(porch))["name"] == "Porch"
            # History stays where it happened: the kitchen is kept, empty or not.
            assert await RoomsRepository(s).get(h["kitchen"]) is not None
    finally:
        async with session_scope() as s:
            await s.execute(text("DELETE FROM people WHERE id = :p"), {"p": people})


@requires_db
async def test_a_tokenless_device_is_not_moved_into_an_occupied_room(house, admin) -> None:
    h = house
    async with session_scope() as s:
        await SatellitePairingRepository(s).reset_pairing(h["c"])
    async with _core() as c:
        r = await c.post(f"/v1/admin/satellites/{h['c']}/room",
                         json={"room_id": h["kitchen"]}, headers=admin)
        assert r.status_code == 409 and "pairing key" in r.json()["detail"]
        r = await c.post(f"/v1/admin/rooms/{h['kitchen']}/merge",
                         json={"rooms": [h["den"]]}, headers=admin)
        assert r.status_code == 409
        r = await c.post(f"/v1/admin/satellites/{h['c']}/room", json={}, headers=admin)
        assert r.status_code == 422


@requires_db
async def test_approve_into_a_room(house, admin) -> None:
    h = house
    tag = h["kitchen"].rsplit("_", 1)[1]
    x, y = f"rrsat_x_{tag}", f"rrsat_y_{tag}"
    async with session_scope() as s:
        appr = SatelliteApprovalRepository(s)
        await appr.request(x, token_hash="h" * 64, code="123456", requested_room=h["kitchen"])
        await appr.request(y, token_hash="g" * 64, code="654321")
        # The same device refreshing keeps the room it asked for.
        await appr.request(x, token_hash="h" * 64, code="123456", requested_room=h["den"])
    async with _core() as c:
        r = await c.get("/v1/admin/satellites/approvals", headers=admin)
        rows = {row["satellite_id"]: row for row in r.json()}
        assert rows[x]["requested_room"] == h["kitchen"]
        assert rows[x]["requested_room_name"] == "Kitchen"
        assert rows[x]["room_id"] == x and rows[y]["requested_room"] is None
        # Both choices at once: refused before a try is spent.
        r = await c.post(f"/v1/admin/satellites/approvals/{x}/approve", headers=admin,
                         json={"code": "123456", "room_id": h["den"], "new_room_name": "Z"})
        assert r.status_code == 422
        r = await c.post(f"/v1/admin/satellites/approvals/{x}/approve", headers=admin,
                         json={"code": "123456", "room_id": "rr_nowhere"})
        assert r.status_code == 404
        # A wrong code binds nothing, and makes no room.
        r = await c.post(f"/v1/admin/satellites/approvals/{y}/approve", headers=admin,
                         json={"code": "000000", "new_room_name": f"Attic {tag}"})
        assert r.status_code == 422
        # No choice sent: the room the device asked for.
        r = await c.post(f"/v1/admin/satellites/approvals/{x}/approve", headers=admin,
                         json={"code": "123456"})
        assert r.status_code == 200, r.text
        assert r.json()["into_room_id"] == h["kitchen"]
        r = await c.post(f"/v1/admin/satellites/approvals/{y}/approve", headers=admin,
                         json={"code": "654321", "new_room_name": f"Attic {tag}"})
        assert r.status_code == 200, r.text
        attic = r.json()["into_room_id"]
    async with session_scope() as s:
        sats, rooms = SatellitesRepository(s), RoomsRepository(s)
        assert await sats.room_of(x) == h["kitchen"]
        assert await sats.room_of(y) == attic
        assert (await rooms.get(attic))["name"] == f"Attic {tag}"
        assert await rooms.get(f"attic_{tag}_2") is None     # the failed try left none
        # No own room is left behind for a device approved elsewhere.
        assert await rooms.get(x) is None and await rooms.get(y) is None
        assert await SatellitePairingRepository(s).get_pairing(x) is not None
    assert room_devices.registry(core_app).room_of(x) == h["kitchen"]


@requires_db
async def test_approval_refused_into_a_room_with_a_tokenless_device(house, admin) -> None:
    h = house
    tag = h["kitchen"].rsplit("_", 1)[1]
    x = f"rrsat_x_{tag}"
    async with session_scope() as s:
        await SatellitePairingRepository(s).reset_pairing(h["c"])
        await SatelliteApprovalRepository(s).request(x, token_hash="h" * 64, code="123456")
    async with _core() as c:
        r = await c.post(f"/v1/admin/satellites/approvals/{x}/approve", headers=admin,
                         json={"code": "123456", "room_id": h["den"]})
        assert r.status_code == 409
    async with session_scope() as s:
        assert (await SatelliteApprovalRepository(s).get(x)) is not None


@requires_db
async def test_the_room_drawer(house, admin) -> None:
    h = house
    tag = h["kitchen"].rsplit("_", 1)[1]
    async with _core() as c:
        r = await c.post("/v1/admin/rooms", json={"name": "Kitchen"}, headers=admin)
        assert r.status_code == 200
        twin = r.json()["room_id"]
        assert twin != h["kitchen"]
        r = await c.get("/v1/admin/rooms", headers=admin)
        body = r.json()
        rooms = {x["room_id"]: x for x in body["rooms"]}
        k = rooms[h["kitchen"]]
        assert [d["satellite_id"] for d in k["satellites"]] == [h["a"], h["b"]]
        assert k["shared"] and k["satellites"][1]["name"] == "Kitchen phone"
        assert k["satellites"][0] == {
            "satellite_id": h["a"], "name": None, "sat_type": "voice", "board": None,
            "paired": True, "connected": False, "speaks_for_room": False,
            "output_latency_us": None, "calibrated_at": None, "needs_update": False}
        assert h["a"] not in rooms
        # Two rooms called Kitchen: offered, never merged on their own.
        offer = [o for o in body["merge_offers"] if set(o["rooms"]) >= {h["kitchen"], twin}]
        assert offer and rooms[twin]["satellites"] == []
        r = await c.patch(f"/v1/admin/rooms/{twin}", json={"name": f"Pantry {tag}"},
                          headers=admin)
        assert r.json() == {"room_id": twin, "name": f"Pantry {tag}"}
        r = await c.patch(f"/v1/admin/satellites/{h['a']}/name",
                          json={"name": " Fridge mic "}, headers=admin)
        assert r.json() == {"satellite_id": h["a"], "name": "Fridge mic"}
        r = await c.patch("/v1/admin/satellites/rr_nobody/name", json={"name": "x"},
                          headers=admin)
        assert r.status_code == 404
        r = await c.delete(f"/v1/admin/rooms/{h['den']}", headers=admin)
        assert r.status_code == 409
        r = await c.delete(f"/v1/admin/rooms/{twin}", headers=admin)
        assert r.json() == {"room_id": twin, "deleted": True}
        r = await c.delete(f"/v1/admin/rooms/{twin}", headers=admin)
        assert r.status_code == 404
        r = await c.post(f"/v1/admin/rooms/{h['kitchen']}/merge",
                         json={"rooms": [h["den"]]}, headers=admin)
        assert r.status_code == 200, r.text
        assert r.json()["moved_satellites"] == [h["c"]]
    assert room_devices.registry(core_app).room_of(h["c"]) == h["kitchen"]
    async with session_scope() as s:
        assert await RoomsRepository(s).get(h["den"]) is None


@requires_db
async def test_a_new_device_still_gets_a_room_of_its_own(house) -> None:
    """Approving with no choice and no request: as before 1.5.0."""
    h = house
    tag = h["kitchen"].rsplit("_", 1)[1]
    x = f"rrsat_x_{tag}"
    async with session_scope() as s:
        appr = SatelliteApprovalRepository(s)
        await appr.request(x, token_hash="h" * 64, code="123456")
        assert await appr.approve(x, "123456") == "approved"
        assert await SatellitesRepository(s).room_of(x) == x
        await ensure_satellite(s, x)
        assert (await RoomsRepository(s).get(x))["name"] == x


def test_requested_room_is_a_room_id_or_nothing() -> None:
    assert streaming._requested_room({"requested_room": "kitchen"}) == "kitchen"
    # The portal's room names (dashes) are room ids too: the one rule.
    assert streaming._requested_room({"requested_room": "living-room"}) == "living-room"
    assert streaming._requested_room({"requested_room": "Kitchen!"}) is None
    assert streaming._requested_room({}) is None


def test_device_and_room_ids_share_one_rule() -> None:
    from domovoi import room_ids
    from domovoi.room_playout import protocol as P

    assert P.is_satellite_id is room_ids.is_room_id
    assert room_devices.is_room_id is room_ids.is_room_id


BAD_IDS = [
    ("POST", "/v1/admin/satellites/Bad!/room", {"room_id": "den"}),
    ("PATCH", "/v1/admin/satellites/Bad!/name", {"name": "x"}),
    ("PATCH", "/v1/admin/rooms/Bad!", {"name": "x"}),
    ("DELETE", "/v1/admin/rooms/Bad!", None),
    ("POST", "/v1/admin/rooms/Bad!/merge", {"rooms": ["den"]}),
    ("POST", "/v1/admin/rooms/kitchen/merge", {"rooms": ["Bad!"]}),
    ("POST", "/v1/admin/satellites/sat_a/room", {"room_id": "Bad!"}),
]


@pytest.mark.parametrize(("method", "path", "body"), BAD_IDS,
                         ids=[f"{m} {p} {b}" for m, p, b in BAD_IDS])
async def test_a_malformed_id_is_refused_before_anything_is_read(
    method, path, body, monkeypatch,
) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    kw = {"json": body} if body is not None else {}
    async with _client() as c:
        r = await c.request(method, path, headers=bearer(ADMIN_TOKEN), **kw)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("path", ["/api/rooms/Bad!", "/api/satellites/Bad!/name"])
async def test_the_web_hop_refuses_a_malformed_id(path, monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with web_client() as w:
        r = await w.patch(path, json={"name": "x"}, headers={
            **bearer(ADMIN_TOKEN), "X-Requested-With": "domovoi-tests"})
    assert r.status_code == 422, r.text
