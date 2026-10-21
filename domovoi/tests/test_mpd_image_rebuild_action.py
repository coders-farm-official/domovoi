"""The admin's "rebuild the music player image" (Containers page,
``POST /v1/admin/containers/mpd-image/rebuild``; docs/CONTAINER_GUARD.md).

The core builds the image the way it does when it is missing
(``mpd_provisioner._build_image``), records it as the guard's baseline
(``built=True``), and moves every room's player onto it the way an update
does: a room container on another image is removed and created again.
Docker is a fake; the rooms and the baseline are the lane database's.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from domovoi import admin_auth, egress, mpd_provisioner
from domovoi.config import settings
from domovoi.container_guard import engine as cg_engine
from domovoi.container_guard import holds, store
from domovoi.db.session import session_scope
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import (
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    core_client,
    web_client,
)
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_container_guard_engine import guard_env  # noqa: F401 (fixture)

OLD = "sha256:" + "0" * 64
NEW = "sha256:" + "7" * 64
ROOMS = (("zz_rebuild_kitchen", "domovoi-mpd-zz_rebuild_kitchen", 16650, 18050),
         ("zz_rebuild_den", "domovoi-mpd-zz_rebuild_den", 16651, 18051))


@pytest.fixture
def docker(monkeypatch):
    """The tag's image, each container's image, every call."""
    state = {"tag": OLD, "containers": {ROOMS[0][1]: OLD, ROOMS[1][1]: NEW}, "calls": [],
             "held_during_build": None}

    async def run_docker(*args, timeout=60.0):
        state["calls"].append(args)
        if args[0] == "build":
            state["held_during_build"] = holds.held("mpd-image")
            state["tag"] = NEW
            return 0, "", ""
        if args[:3] == ("image", "inspect", "-f"):
            return 0, state["tag"] + "\n", ""
        if args[:2] == ("inspect", "-f") and args[2] == "{{.Image}}":
            img = state["containers"].get(args[3])
            return (0, img + "\n", "") if img else (1, "", "No such object")
        if args[:2] == ("rm", "-f"):
            state["containers"].pop(args[2], None)
            return 0, "", ""
        return 0, "", ""

    async def ensure_container(*, name, image, **kw):
        state["calls"].append(("ensure", name, image, kw["volume_name"]))
        state["containers"].setdefault(name, state["tag"])

    monkeypatch.setattr(mpd_provisioner, "_run_docker", run_docker)
    monkeypatch.setattr(mpd_provisioner, "_ensure_container", ensure_container)
    monkeypatch.setattr(egress, "internet_turned_off", lambda: False)
    monkeypatch.setattr(mpd_provisioner, "_REBUILD_LOCK", None)
    return state


@pytest.fixture
async def rooms():
    async def clear():
        async with session_scope() as s:
            await s.execute(text("DELETE FROM mpd_rooms WHERE room_id LIKE 'zz_rebuild_%'"))
    await clear()
    async with session_scope() as s:
        for room_id, name, ctrl, http in ROOMS:
            await s.execute(text(
                "INSERT INTO mpd_rooms (room_id, control_port, http_port, container_name) "
                "VALUES (:r, :c, :h, :n)"), {"r": room_id, "c": ctrl, "h": http, "n": name})
    yield
    await clear()


@requires_db
async def test_rebuild_builds_records_and_moves_the_rooms(guard_env, docker, rooms) -> None:  # noqa: F811
    guard, *_ = guard_env
    cg_engine.reset_guard(guard)
    try:
        assert await cg_engine.note_mpd_image(OLD, built=False) == "adopted"
        out = await mpd_provisioner.rebuild_image()
        builds = [c for c in docker["calls"] if c[0] == "build"]
        assert builds == [("build", "-t", settings.mpd_image_tag, "-f",
                           str(mpd_provisioner._DOCKERFILE), str(mpd_provisioner._BUILD_CONTEXT))]
        assert docker["held_during_build"], "the tag is held while it is rebuilt"
        assert (out["image_id"], out["baseline"]) == (NEW, "built")
        async with session_scope() as s:
            b = await store.get_baseline(s, cg_engine.MPD_BASELINE_KEY)
        assert (b["value"], b["source"]) == (NEW, "core-build")
        mine = {r["room"]: r for r in out["rooms"] if r["room"].startswith("zz_rebuild_")}
        assert mine["zz_rebuild_kitchen"] == {"room": "zz_rebuild_kitchen", "recreated": True, "error": None}
        assert mine["zz_rebuild_den"]["recreated"] is False, "already on the new image: left running"
        assert ("rm", "-f", ROOMS[0][1]) in docker["calls"]
        assert ("rm", "-f", ROOMS[1][1]) not in docker["calls"]
        ensured = [c for c in docker["calls"] if c[0] == "ensure" and c[1].startswith("domovoi-mpd-zz_rebuild")]
        assert {c[1] for c in ensured} == {ROOMS[0][1], ROOMS[1][1]}
        assert all(c[2] == settings.mpd_image_tag for c in ensured)
        assert docker["containers"][ROOMS[0][1]] == NEW
        # Removed before it was created again.
        calls = docker["calls"]
        assert calls.index(("rm", "-f", ROOMS[0][1])) < next(
            i for i, c in enumerate(calls) if c[0] == "ensure" and c[1] == ROOMS[0][1])
    finally:
        cg_engine.reset_guard(None)
        holds.clear()


@requires_db
async def test_a_room_that_fails_does_not_stop_the_others(guard_env, docker, rooms, monkeypatch) -> None:  # noqa: F811
    guard, *_ = guard_env
    cg_engine.reset_guard(guard)
    real = mpd_provisioner._ensure_container

    async def flaky(*, name, **kw):
        if name == ROOMS[0][1]:
            raise RuntimeError("docker run: port is already allocated")
        await real(name=name, **kw)

    monkeypatch.setattr(mpd_provisioner, "_ensure_container", flaky)
    try:
        out = await mpd_provisioner.rebuild_image()
        mine = {r["room"]: r for r in out["rooms"] if r["room"].startswith("zz_rebuild_")}
        assert "port is already allocated" in mine["zz_rebuild_kitchen"]["error"]
        assert mine["zz_rebuild_den"]["error"] is None
    finally:
        cg_engine.reset_guard(None)
        holds.clear()


async def test_not_with_internet_access_turned_off(docker, monkeypatch) -> None:
    monkeypatch.setattr(egress, "internet_turned_off", lambda: True)
    assert "internet access is turned off" in mpd_provisioner.rebuild_unavailable()
    with pytest.raises(RuntimeError, match="internet access is turned off"):
        await mpd_provisioner.rebuild_image()
    assert docker["calls"] == []


async def test_one_at_a_time(docker, monkeypatch) -> None:
    lock = asyncio.Lock()
    monkeypatch.setattr(mpd_provisioner, "_REBUILD_LOCK", lock)
    async with lock:
        with pytest.raises(mpd_provisioner.RebuildBusy):
            await mpd_provisioner.rebuild_image()
    assert docker["calls"] == []


# ─── the endpoints ────────────────────────────────────────────────────────

def test_the_security_tier_at_both_hops() -> None:
    from domovoi.tests.route_walk import iter_route_contexts
    from web.backend.main import app as web_app

    def gates(app, path):
        for rc in iter_route_contexts(app.routes):
            if getattr(rc, "path", None) == path and "POST" in (getattr(rc, "methods", None) or set()):
                found, stack = [], [getattr(rc, "dependant", None)]
                while stack:
                    d = stack.pop()
                    for dep in getattr(d, "dependencies", ()) if d else ():
                        found.append(dep.call)
                        stack.append(dep)
                return found
        raise AssertionError(f"POST {path} not routed")

    assert admin_auth.require_admin_security in gates(core_app, "/v1/admin/containers/mpd-image/rebuild")
    assert admin_auth.require_admin_security in gates(web_app, "/api/containers/mpd-image/rebuild")


@requires_db
async def test_the_endpoint_answers_for_the_admin_only(_db, monkeypatch) -> None:  # noqa: F811
    calls = []

    async def fake_rebuild():
        calls.append(1)
        return {"image_id": NEW, "baseline": "built", "rooms": []}

    monkeypatch.setattr(mpd_provisioner, "rebuild_image", fake_rebuild)
    async with web_client() as w:
        token = await claim_admin(w)
    async with core_client() as c:
        assert (await c.post("/v1/admin/containers/mpd-image/rebuild", json={})).status_code == 401
        assert calls == []
        r = await c.post("/v1/admin/containers/mpd-image/rebuild", json={}, headers=bearer(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["image_id"], body["baseline"]) == (NEW, "built") and "guard" in body
        status = (await c.get("/v1/admin/containers", headers=bearer(token))).json()
        assert status["mpd_rebuild"] in ({"available": True, "why": None},
                                         {"available": False, "why": mpd_provisioner.rebuild_unavailable()})

        async def busy():
            raise mpd_provisioner.RebuildBusy("the music player image is already being rebuilt")

        monkeypatch.setattr(mpd_provisioner, "rebuild_image", busy)
        r = await c.post("/v1/admin/containers/mpd-image/rebuild", json={}, headers=bearer(token))
        assert r.status_code == 409

        async def broken():
            raise RuntimeError("docker build failed: E: Unable to locate package mpd")

        monkeypatch.setattr(mpd_provisioner, "rebuild_image", broken)
        r = await c.post("/v1/admin/containers/mpd-image/rebuild", json={}, headers=bearer(token))
        assert r.status_code == 503 and "Unable to locate package" in r.json()["detail"]
    async with web_client() as w:
        assert (await w.post("/api/containers/mpd-image/rebuild", json={})).status_code == 401


async def test_the_admin_status_says_why_it_is_not_offered(monkeypatch) -> None:
    monkeypatch.setattr(egress, "internet_turned_off", lambda: True)
    assert mpd_provisioner.rebuild_unavailable().startswith("internet access is turned off")
    monkeypatch.setattr(settings, "mpd_image_tag", "")
    assert "MPD_IMAGE_TAG" in mpd_provisioner.rebuild_unavailable()
