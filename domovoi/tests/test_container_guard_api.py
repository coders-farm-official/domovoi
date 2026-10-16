"""The container guard's freeze on every operation it covers, the clear
that lifts it, and the API tiers (core and web) — against the real apps
and the test database.

The freeze list (domovoi/container_guard/freeze.py): plugin install
(stage, confirm), upgrade (stage, confirm) and enable; satellite code
upgrades and the satellite code / plugin payload channels; satellite media
preparation and its cache refresh; the update pull, and a restart that
would load something new. Each answers 423 naming the incident while a
tamper incident is open, and goes back to its usual answer once an admin
clears it. Disable and uninstall stay available.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.container_guard import freeze, store
from domovoi.container_guard.model import FAILURE, TAMPER, Finding
from domovoi.db.session import engine as db_engine
from domovoi.db.session import session_scope
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import (
    COOKIE,
    HEADER,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    core_client,
    db_device_token,
    web_client,
)
from domovoi.tests.conftest import requires_db

pytestmark = requires_db

D_OTHER = "sha256:" + "2" * 64


def tamper_finding() -> Finding:
    return Finding(
        key=f"tamper:image:domovoi-searxng:{D_OTHER}", cls=TAMPER, kind="image", severity="error",
        container="domovoi-searxng", owner="core", label="the web search helper",
        summary="The web search helper is running a different image than the one Domovoi expects.",
        expected={"image": "searxng/searxng:2026@sha256:" + "1" * 64}, found={"image_id": D_OTHER},
    )


def failure_finding(required: bool = True) -> Finding:
    return Finding(
        key="failure:domovoi-mpd-kitchen", cls=FAILURE, kind="stopped",
        severity="error" if required else "warn", container="domovoi-mpd-kitchen", owner="core",
        label="the music player for kitchen", summary="The music player for kitchen stopped.",
        expected={"state": "running"}, found={"status": "exited", "exit_code": 137},
    )


async def _truncate() -> None:
    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE container_incidents RESTART IDENTITY"))
    freeze.forget()


@pytest_asyncio.fixture
async def guard_db(_db):  # noqa: F811 — the auth fixture
    await _truncate()
    yield
    await _truncate()


async def _open(f: Finding) -> int:
    async with session_scope() as s:
        iid, _ = await store.open_incident(s, f)
    freeze.forget()
    return iid


async def _admin() -> str:
    async with web_client() as w:
        return await claim_admin(w)


# ─── the freeze, operation by operation ───────────────────────────────────

CORE_FROZEN = [
    ("POST", "/v1/plugins/install", {}, "plugin_install"),
    ("POST", "/v1/plugins/install/no-such-stage/confirm", None, "plugin_install"),
    ("POST", "/v1/plugins/someplugin/upgrade", {}, "plugin_upgrade"),
    ("POST", "/v1/plugins/someplugin/enable", None, "plugin_enable"),
    ("POST", "/v1/admin/satellite/upgrade", {"room_id": "kitchen"}, "satellite_upgrade"),
    ("POST", "/v1/admin/version/pull", None, "update_pull"),
    ("POST", "/v1/admin/android-app/fetch", None, "android_app"),
]

CHANNELS = [
    "/v1/satellite-code/manifest",
    "/v1/satellite-code/manifest.sig",
    "/v1/satellite-code/client.py",
    "/v1/satellite-plugins/manifest",
    "/v1/satellite-plugins/manifest.sig",
    "/v1/satellite-plugins/radio/x.txt",
    "/v1/android-app/files/domovoi-1.0.0-arm64-v8a.apk",
]


async def _call(client, method, path, body, token):
    kw = {"headers": bearer(token)}
    if body is not None:
        kw["json"] = body
    return await client.request(method, path, **kw)


@pytest.mark.parametrize(("method", "path", "body", "operation"), CORE_FROZEN,
                         ids=[p for _, p, _, _ in CORE_FROZEN])
async def test_each_core_operation_refuses_while_frozen_and_not_after_the_clear(
    guard_db, method, path, body, operation,
) -> None:
    token = await _admin()
    iid = await _open(tamper_finding())
    async with core_client() as c:
        r = await _call(c, method, path, body, token)
        assert r.status_code == 423, r.text
        err = r.json()["detail"]["error"]
        assert err["code"] == freeze.CODE and err["details"]["operation"] == operation
        assert f"incident #{iid}" in err["message"]
        assert "different image" in err["message"]
        async with session_scope() as s:
            await store.clear(s, iid, "admin via test", "reviewed")
        freeze.forget()
        r = await _call(c, method, path, body, token)
        assert r.status_code != 423, r.text


@pytest.mark.parametrize("path", CHANNELS)
async def test_the_satellite_channels_pause_without_saying_why(guard_db, path) -> None:
    await _open(tamper_finding())
    async with core_client() as c:
        r = await c.get(path)
    assert r.status_code == 423
    body = r.text
    assert "domovoi-searxng" not in body and "image" not in body
    assert r.json()["detail"]["error"]["message"] == freeze.PUBLIC_MESSAGE


async def test_the_channels_answer_normally_when_not_frozen(guard_db) -> None:
    async with core_client() as c:
        r = await c.get("/v1/satellite-code/manifest")
    assert r.status_code == 200


async def test_a_failure_freezes_nothing(guard_db) -> None:
    token = await _admin()
    await _open(failure_finding())
    async with core_client() as c:
        r = await c.post("/v1/admin/version/pull", headers=bearer(token))
        assert r.status_code != 423
        assert (await c.get("/v1/satellite-code/manifest")).status_code == 200


async def test_disable_and_uninstall_stay_available(guard_db) -> None:
    token = await _admin()
    await _open(tamper_finding())
    async with core_client() as c:
        for path in ("/v1/plugins/someplugin/disable", "/v1/plugins/someplugin/uninstall"):
            r = await c.post(path, headers=bearer(token), json={})
            assert r.status_code != 423, r.text


async def test_a_restart_that_would_apply_is_frozen_a_plain_one_is_not(guard_db, monkeypatch) -> None:
    from domovoi import main as core_main
    from domovoi import self_restart

    async def fake_restart():
        return {"ok": True, "mode": "restart", "units": [], "delay_sec": 1.0, "error": None,
                "in_progress": False}

    monkeypatch.setattr(self_restart, "restart", fake_restart)
    token = await _admin()
    iid = await _open(tamper_finding())
    async with core_client() as c:
        async def would(value):
            async def _f():
                return value
            monkeypatch.setattr(core_main, "_restart_would_apply", _f)

        await would(True)
        r = await c.post("/v1/admin/version/restart", headers=bearer(token))
        assert r.status_code == 423
        assert r.json()["detail"]["error"]["details"]["operation"] == "update_apply"
        assert f"#{iid}" in r.json()["detail"]["error"]["message"]
        await would(False)
        r = await c.post("/v1/admin/version/restart", headers=bearer(token))
        assert r.status_code == 200 and r.json()["ok"] is True


async def test_the_channel_switch_is_frozen_like_the_pull(guard_db, monkeypatch) -> None:
    """Switching stable / main decides what every later pull follows: it
    pauses with the pull. (git_version.switch_channel is faked: the real
    one would fetch and re-point the checkout these tests run from.)"""
    from domovoi import git_version

    calls: list[str] = []

    async def switch(channel: str) -> dict:
        calls.append(channel)
        return {"ok": True, "changed": True, "channel": channel, "error": None}

    monkeypatch.setattr(git_version, "switch_channel", switch)
    token = await _admin()
    iid = await _open(tamper_finding())
    async with core_client() as c:
        r = await c.post("/v1/admin/version/channel", json={"channel": "stable"}, headers=bearer(token))
        assert r.status_code == 423, r.text
        assert r.json()["detail"]["error"]["details"]["operation"] == "update_pull"
        assert calls == []
        async with session_scope() as s:
            await store.clear(s, iid, "admin via test", "reviewed")
        freeze.forget()
        r = await c.post("/v1/admin/version/channel", json={"channel": "stable"}, headers=bearer(token))
        assert r.status_code == 200 and r.json()["channel"] == "stable", r.text
    assert calls == ["stable"]


async def test_restart_would_apply_reads_pulled_code_and_staged_plugins(monkeypatch) -> None:
    from domovoi import git_version
    from domovoi import main as core_main
    from domovoi.plugins_runtime.loader import LOADER

    async def checkout():
        return "abc1234"

    monkeypatch.setattr(git_version, "current_sha", checkout)
    monkeypatch.setattr(git_version, "boot_sha", lambda: "abc1234-dirty")
    monkeypatch.setattr(LOADER, "pending_restart", {})
    assert await core_main._restart_would_apply() is False
    monkeypatch.setattr(git_version, "boot_sha", lambda: "0000000")
    assert await core_main._restart_would_apply() is True
    monkeypatch.setattr(git_version, "boot_sha", lambda: "abc1234")
    monkeypatch.setattr(LOADER, "pending_restart", {"x": {"slug": "x"}})
    assert await core_main._restart_would_apply() is True


async def test_satellite_media_preparation_is_frozen(guard_db, monkeypatch) -> None:
    from domovoi import egress
    from web.backend.api import satellite_media

    def must_not_run(*a, **kw):
        raise AssertionError("a frozen prepare reached the builder")

    monkeypatch.setattr(satellite_media.overlay, "validate_wifi_country", must_not_run)
    token = await _admin()
    iid = await _open(tamper_finding())
    async with web_client() as w:
        headers = {**bearer(token), "X-Requested-With": "t"}
        for path in ("/api/satellites/media/prepare", "/api/satellites/media/cache/refresh"):
            r = await w.post(path, headers=headers, json={})
            assert r.status_code == 423, r.text
            assert f"incident #{iid}" in r.json()["detail"]["error"]["message"]
        async with session_scope() as s:
            await store.clear(s, iid, "admin", None)
        freeze.forget()
        with egress.override_policy("never"):
            r = await w.post("/api/satellites/media/cache/refresh", headers=headers, json={})
        assert r.status_code == 409   # the internet answer's refusal, not the freeze


async def test_an_unauthenticated_caller_learns_nothing_from_the_freeze(guard_db) -> None:
    await _admin()
    await _open(tamper_finding())
    async with core_client() as c:
        r = await c.post("/v1/plugins/install", json={})
    assert r.status_code == 401 and "searxng" not in r.text


async def test_freeze_fails_open_without_the_table(monkeypatch) -> None:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def broken():
        raise RuntimeError('relation "container_incidents" does not exist')
        yield  # pragma: no cover

    freeze.forget()
    assert await freeze.frozen_incidents(broken) == []
    freeze.forget()


# ─── the tiers ────────────────────────────────────────────────────────────

async def test_the_open_summary_says_only_that_something_needs_an_admin(guard_db) -> None:
    await _admin()
    async with web_client() as w:
        r = await w.get("/api/containers/summary")
        assert r.json() == {"attention": False, "tone": None, "text": ""}
        await _open(tamper_finding())
        r = await w.get("/api/containers/summary")
    assert r.status_code == 200
    assert r.json() == {"attention": True, "tone": "err", "text": "Something needs an admin's attention."}
    assert "searxng" not in r.text and "image" not in r.text


async def test_the_summary_is_amber_for_an_optional_failure(guard_db) -> None:
    await _open(failure_finding(required=False))
    async with web_client() as w:
        assert (await w.get("/api/containers/summary")).json()["tone"] == "warn"


async def test_the_incident_list_is_household_tier_details_admin_only(guard_db) -> None:
    token = await _admin()
    device = await db_device_token()
    await _open(tamper_finding())
    async with web_client() as w:
        assert (await w.get("/api/containers/incidents")).status_code == 401
        r = await w.get("/api/containers/incidents", headers={HEADER: device})
        assert r.status_code == 200
        body = r.json()
        (inc,) = body["incidents"]
        assert inc["summary"].startswith("The web search helper") and inc["freezes"] is True
        assert "expected" not in inc and "found" not in inc
        assert body["frozen"] is True and body["banner"]["tone"] == "tamper"
        assert "paused" in body["banner"]["text"]
        r = await w.get("/api/containers/incidents", headers=bearer(token))
        (inc,) = r.json()["incidents"]
        assert inc["found"]["image_id"] == D_OTHER


async def test_the_banner_hides_an_acknowledged_failure_but_never_tamper(guard_db) -> None:
    token = await _admin()
    fid = await _open(failure_finding())
    tid = await _open(tamper_finding())
    async with core_client() as c:
        r = await c.post(f"/v1/admin/containers/incidents/{tid}/acknowledge", headers=bearer(token))
        assert r.status_code == 409 and "clear it" in r.text
        r = await c.post(f"/v1/admin/containers/incidents/{fid}/acknowledge", headers=bearer(token))
        assert r.status_code == 200 and r.json()["incident"]["acknowledged"] is True
        assert r.json()["incident"]["acknowledged_by"].startswith("admin via ")
    async with web_client() as w:
        banner = (await w.get("/api/containers/incidents", headers=bearer(token))).json()["banner"]
    assert banner["incident_ids"] == [tid] and banner["tone"] == "tamper"
    async with core_client() as c:
        r = await c.post(f"/v1/admin/containers/incidents/{fid}/clear", headers=bearer(token), json={})
        assert r.status_code == 409 and "resolves by itself" in r.text
        r = await c.post(f"/v1/admin/containers/incidents/{tid}/clear", headers=bearer(token),
                         json={"note": "our own SearXNG upgrade"})
        assert r.status_code == 200
        assert r.json()["frozen"] is False
        assert r.json()["incident"]["clear_note"] == "our own SearXNG upgrade"
        r = await c.post(f"/v1/admin/containers/incidents/{tid}/clear", headers=bearer(token), json={})
        assert r.status_code == 409
        r = await c.post("/v1/admin/containers/incidents/987654/clear", headers=bearer(token), json={})
        assert r.status_code == 404
    async with web_client() as w:
        banner = (await w.get("/api/containers/incidents", headers=bearer(token))).json()["banner"]
    assert banner["show"] is False and banner["frozen"] is False


async def test_acknowledge_and_clear_are_admin_only(guard_db) -> None:
    await _admin()
    device = await db_device_token()
    tid = await _open(tamper_finding())
    async with core_client() as c:
        for path in (f"/v1/admin/containers/incidents/{tid}/clear",
                     f"/v1/admin/containers/incidents/{tid}/acknowledge",
                     "/v1/admin/containers/check"):
            assert (await c.post(path, json={})).status_code == 401
            assert (await c.post(path, json={}, headers={HEADER: device})).status_code == 401
            r = await c.post(path, json={}, cookies={COOKIE: "x"})
            assert r.status_code in (401, 403)
        assert (await c.get("/v1/admin/containers")).status_code == 401
        assert (await c.get("/v1/admin/containers", headers={HEADER: device})).status_code == 401
    async with web_client() as w:
        for path in (f"/api/containers/incidents/{tid}/clear",
                     f"/api/containers/incidents/{tid}/acknowledge", "/api/containers/check"):
            assert (await w.post(path, json={})).status_code == 401
            assert (await w.post(path, json={}, headers={HEADER: device, "X-Requested-With": "t"})).status_code == 401
        assert (await w.get("/api/containers/status")).status_code == 401
    freeze.forget()
    assert await freeze.is_frozen()


async def test_the_admin_status_carries_the_guard_and_the_incidents(guard_db) -> None:
    from domovoi.container_guard import engine as cg_engine

    token = await _admin()
    await _open(tamper_finding())
    async with core_client() as c:
        r = await c.get("/v1/admin/containers", headers=bearer(token))
    assert r.status_code == 200
    body = r.json()
    assert body["frozen"] is True and body["incidents"][0]["found"]["image_id"] == D_OTHER
    assert "plugin_install" in body["freeze_operations"]
    assert body["guard"] == cg_engine.get_guard().status


def test_the_gates_on_the_route_tables() -> None:
    from domovoi.tests.route_walk import iter_route_contexts
    from web.backend.main import app as web_app

    def gates(app, method, path):
        for rc in iter_route_contexts(app.routes):
            if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or set()):
                found, stack = [], [getattr(rc, "dependant", None)]
                while stack:
                    d = stack.pop()
                    for dep in getattr(d, "dependencies", ()) if d else ():
                        found.append(dep.call)
                        stack.append(dep)
                return found
        raise AssertionError(f"{method} {path} not routed")

    for path in ("/v1/admin/containers/check", "/v1/admin/containers/incidents/{incident_id}/acknowledge",
                 "/v1/admin/containers/incidents/{incident_id}/clear"):
        assert admin_auth.require_admin_mutation in gates(core_app, "POST", path)
    assert admin_auth.require_admin_read in gates(core_app, "GET", "/v1/admin/containers")
    assert admin_auth.require_device_read in gates(web_app, "GET", "/api/containers/incidents")
    assert admin_auth.require_admin_read in gates(web_app, "GET", "/api/containers/status")
    assert gates(web_app, "GET", "/api/containers/summary") == []
    for path in ("/api/containers/check", "/api/containers/incidents/{incident_id}/acknowledge",
                 "/api/containers/incidents/{incident_id}/clear"):
        assert admin_auth.require_admin_mutation in gates(web_app, "POST", path)


async def test_the_realtime_channel_carries_the_household_view(guard_db) -> None:
    from web.backend import realtime

    assert realtime.notify_channel_map()["container_incidents_changed"] == "containers"
    await _open(tamper_finding())
    value = await realtime.StatePollLoop._CHANNEL_HELPERS["containers"]()
    (inc,) = value["incidents"]
    assert value["frozen"] is True and "found" not in inc
