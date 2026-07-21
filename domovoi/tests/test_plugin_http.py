"""Plugin HTTP mounting (design §4.11): /v1/plugins/<slug> prefix,
disabled ⇒ 404 gate, default-DENY mutations once admin auth is set up,
open_endpoint opt-out, and the introspection endpoints."""

from __future__ import annotations

import hashlib
import secrets

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import plugin_http
from domovoi.plugin_http import (
    mount_plugin_router,
    open_endpoint,
    set_plugin_enabled,
)
from domovoi.tests.conftest import requires_db

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fresh_mount_state():
    plugin_http._plugin_enabled.clear()
    plugin_http._mounted.clear()
    yield
    plugin_http._plugin_enabled.clear()
    plugin_http._mounted.clear()


def _make_app() -> FastAPI:
    app = FastAPI()
    router = APIRouter()

    @router.get("/things")
    async def list_things():
        return {"things": []}

    @router.post("/things")
    async def create_thing():
        return {"created": True}

    @router.post("/tune")
    @open_endpoint
    async def tune():
        return {"tuned": True}

    # open_endpoint must be applied to the FUNCTION the route wraps —
    # declare it as the inner decorator (closest to the def).
    mount_plugin_router(app, "demo", router)
    return app


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@requires_db
async def test_routes_mount_under_plugin_prefix(db_session) -> None:
    app = _make_app()
    async with await _client(app) as client:
        r = await client.get("/v1/plugins/demo/things")
        assert r.status_code == 200
        assert r.json() == {"things": []}


@requires_db
async def test_disabled_plugin_routes_404(db_session) -> None:
    app = _make_app()
    set_plugin_enabled("demo", False)
    async with await _client(app) as client:
        assert (await client.get("/v1/plugins/demo/things")).status_code == 404
        assert (await client.post("/v1/plugins/demo/tune")).status_code == 404
    # Re-enable: the router object is reused, routes come back.
    set_plugin_enabled("demo", True)
    async with await _client(app) as client:
        assert (await client.get("/v1/plugins/demo/things")).status_code == 200


@requires_db
async def test_mutations_allowed_before_admin_setup(db_session) -> None:
    """Pre-setup LAN-trust: with no admin_auth row, the gate allows
    (the open posture until the first-run setup runs)."""
    await db_session.commit()   # ensure truncation is visible to the app's own sessions
    app = _make_app()
    async with await _client(app) as client:
        assert (await client.post("/v1/plugins/demo/things")).status_code == 200


@requires_db
async def test_mutations_denied_after_admin_setup(db_session) -> None:
    """Once admin auth exists: non-GET without a Bearer session → 401;
    a live admin session → 200; open_endpoint opt-outs stay open."""
    token = secrets.token_hex(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    await db_session.execute(
        text("INSERT INTO admin_auth (id, password_hash) VALUES (1, 'x')")
    )
    await db_session.execute(
        text(
            "INSERT INTO admin_sessions (token_hash, expires_at) "
            "VALUES (:h, NOW() + INTERVAL '1 day')"
        ),
        {"h": token_hash},
    )
    await db_session.commit()
    try:
        app = _make_app()
        async with await _client(app) as client:
            # GETs stay open.
            assert (await client.get("/v1/plugins/demo/things")).status_code == 200
            # Default-DENY mutation.
            assert (await client.post("/v1/plugins/demo/things")).status_code == 401
            # Bearer session passes.
            r = await client.post(
                "/v1/plugins/demo/things",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200
            # Wrong token fails.
            r = await client.post(
                "/v1/plugins/demo/things",
                headers={"Authorization": "Bearer wrong"},
            )
            assert r.status_code == 401
            # Explicit opt-out (daily-use action) stays open.
            assert (await client.post("/v1/plugins/demo/tune")).status_code == 200
    finally:
        await db_session.execute(text("DELETE FROM admin_sessions"))
        await db_session.execute(text("DELETE FROM admin_auth"))
        await db_session.commit()


# ─── Core introspection endpoints (main app) ──────────────────────────────


@requires_db
async def test_capabilities_and_plugins_endpoints(db_session) -> None:
    from domovoi.acquisitions import ACQUISITIONS
    from domovoi.capabilities import CAPABILITIES, MEDIA_ACQUISITION_FULFILLER
    from domovoi.main import app as main_app

    await db_session.commit()
    ACQUISITIONS.register_fulfiller("providerx", kinds={"query"})
    try:
        async with await _client(main_app) as client:
            r = await client.get("/v1/capabilities")
            assert r.status_code == 200
            caps = r.json()["capabilities"]
            assert caps.get(MEDIA_ACQUISITION_FULFILLER) == ["providerx"]

            r = await client.get("/v1/plugins")
            assert r.status_code == 200
            listed = r.json()["plugins"]
            # A registered capability provider is NOT a plugins row; only
            # registry rows appear. The list may legitimately carry the
            # bundled radio plugin once any lifespan-booting test has run
            # discovery against this DB (radio is bundled + enabled by
            # default, locked 14) — assert shape, not emptiness.
            assert all(p["slug"] != "providerx" for p in listed)
            assert all(
                {"slug", "name", "version", "enabled", "status"} <= set(p)
                for p in listed
            )

            r = await client.get("/v1/plugins/ghost/status")
            assert r.status_code == 404
    finally:
        CAPABILITIES.unregister(MEDIA_ACQUISITION_FULFILLER, slug="providerx")


@requires_db
async def test_plugin_status_scaffold_shape(db_session) -> None:
    from domovoi.main import app as main_app

    await db_session.execute(
        text(
            """
            INSERT INTO plugins (slug, name, version, domovoi_api,
                                 install_source, install_dir, manifest)
            VALUES ('demo', 'Demo', '1.0.0', '>=1.0,<2.0',
                    'bundled', '/tmp/demo', '{}'::jsonb)
            """
        )
    )
    await db_session.commit()
    try:
        async with await _client(main_app) as client:
            r = await client.get("/v1/plugins/demo/status")
            assert r.status_code == 200
            body = r.json()
            assert body["slug"] == "demo"
            assert body["status"] == "ok"
            # Stable §4.14 scaffold keys.
            for key in ("handlers", "workers", "startup_hooks"):
                assert key in body
    finally:
        await db_session.execute(text("DELETE FROM plugins WHERE slug = 'demo'"))
        await db_session.commit()
