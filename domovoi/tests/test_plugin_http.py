"""Plugin HTTP mounting (design §4.11): /v1/plugins/<slug> prefix,
disabled ⇒ 404 gate, default-DENY mutations once admin auth is set up,
the device_endpoint tier (household token), the open_endpoint opt-out,
each enable's routes replacing the torn-down load's, and the
introspection endpoints. The device tier's full matrix, in both
processes, is ``test_plugin_device_tier.py``."""

from __future__ import annotations

import hashlib
import secrets

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import plugin_http
from domovoi import admin_auth
from domovoi.plugin_http import (
    device_endpoint,
    mount_plugin_router,
    open_endpoint,
    set_plugin_enabled,
)
from domovoi.tests.conftest import requires_db
from domovoi.tests.route_walk import iter_route_contexts

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

    @router.post("/play")
    @device_endpoint
    async def play():
        return {"played": True}

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
    device_token = await admin_auth.ensure_device_token_row(db_session)
    await db_session.commit()
    admin_auth.DEVICE_TOKEN_BACKOFF.reset()
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
            # Device tier: nothing is 401, the household token or the
            # Bearer passes — and the token does not open the admin tier.
            assert (await client.post("/v1/plugins/demo/play")).status_code == 401
            header = {admin_auth.DEVICE_TOKEN_HEADER: device_token}
            r = await client.post("/v1/plugins/demo/play", headers=header)
            assert r.status_code == 200, r.text
            r = await client.post(
                "/v1/plugins/demo/play",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200, r.text
            r = await client.post("/v1/plugins/demo/things", headers=header)
            assert r.status_code == 401, r.text
    finally:
        await db_session.execute(text("DELETE FROM admin_sessions"))
        await db_session.execute(text("DELETE FROM admin_auth"))
        await db_session.execute(text("DELETE FROM household_device_tokens"))
        await db_session.commit()


# ─── Every router a plugin registers, mounted once ────────────────────────
#
# Mounting is per slug: each call replaces the slug's whole mount. The
# loader used to mount a plugin's routers one call at a time, so a plugin
# with two core routers served only one of them. These need no DB — GETs
# pass the gate without a credential lookup.


def _two_routers() -> tuple[APIRouter, APIRouter]:
    first, second = APIRouter(), APIRouter()

    @first.get("/first")
    async def from_first():
        return {"router": 1}

    @second.get("/second")
    async def from_second():
        return {"router": 2}

    return first, second


async def _statuses(app: FastAPI, *paths: str) -> list[int]:
    async with await _client(app) as client:
        return [(await client.get(f"/v1/plugins/demo{p}")).status_code for p in paths]


async def test_every_router_a_plugin_registers_is_mounted() -> None:
    app = FastAPI()
    first, second = _two_routers()
    plugin_http.mount_plugin_routers(app, "demo", [first, second])
    assert await _statuses(app, "/first", "/second") == [200, 200]
    assert plugin_http._mounted["demo"] == [first, second]


async def test_re_mounting_a_slug_replaces_its_routes_in_place() -> None:
    """Enable re-runs ``register()``, which builds FRESH router objects:
    they replace the slug's mounted ones, nothing is included twice, and
    the route table does not grow. The one-router form and an empty set
    replace the slug's whole mount the same way."""
    app = FastAPI()
    first, second = _two_routers()
    plugin_http.mount_plugin_routers(app, "demo", [first, second])
    size = len(app.router.routes)

    set_plugin_enabled("demo", False)
    assert await _statuses(app, "/first", "/second") == [404, 404]

    fresh = list(_two_routers())
    plugin_http.mount_plugin_routers(app, "demo", fresh)
    assert len(app.router.routes) == size
    assert await _statuses(app, "/first", "/second") == [200, 200]
    assert plugin_http._mounted["demo"] == fresh

    only = APIRouter()

    @only.get("/only")
    async def only_route():
        return {}

    mount_plugin_router(app, "demo", only)
    assert await _statuses(app, "/first", "/second", "/only") == [404, 404, 200]

    plugin_http.mount_plugin_routers(app, "demo", [])
    assert await _statuses(app, "/only") == [404]
    assert "demo" not in plugin_http._mounted


async def test_one_refused_router_mounts_none_of_them() -> None:
    """Every router is checked before any is included: a conflict on the
    second must not leave the first serving."""
    app = FastAPI()
    first, _ = _two_routers()
    bad = APIRouter()

    async def both():
        return {}

    setattr(both, plugin_http._OPEN_MARKER, True)
    setattr(both, plugin_http._DEVICE_MARKER, True)
    bad.add_api_route("/both", both, methods=["POST"])
    with pytest.raises(plugin_http.EndpointTierConflict, match="POST /both"):
        plugin_http.mount_plugin_routers(app, "demo", [first, bad])
    assert "demo" not in plugin_http._mounted
    assert await _statuses(app, "/first") == [404]


async def test_a_refused_re_mount_leaves_nothing_serving() -> None:
    """A re-mount the checks refuse takes the previous mount's routes
    down with it: they belong to a load that has been torn down, so no
    enable flag may bring them back."""
    app = FastAPI()
    first, _ = _two_routers()
    plugin_http.mount_plugin_routers(app, "demo", [first])
    set_plugin_enabled("demo", False)

    bad = APIRouter()

    async def both():
        return {}

    setattr(both, plugin_http._OPEN_MARKER, True)
    setattr(both, plugin_http._DEVICE_MARKER, True)
    bad.add_api_route("/both", both, methods=["POST"])
    with pytest.raises(plugin_http.EndpointTierConflict, match="POST /both"):
        plugin_http.mount_plugin_routers(app, "demo", [_two_routers()[0], bad])
    assert "demo" not in plugin_http._mounted
    set_plugin_enabled("demo", True)
    assert await _statuses(app, "/first") == [404]


# One route per tier, answering with the generation that built it — so a
# test can tell which mount served a request.


def _tiered_router(gen: int) -> APIRouter:
    router = APIRouter()

    @router.get("/things")
    async def list_things():
        return {"gen": gen}

    @router.post("/things")
    async def create_thing():
        return {"gen": gen}

    @router.post("/play")
    @device_endpoint
    async def play():
        return {"gen": gen}

    @router.post("/tune")
    @open_endpoint
    async def tune():
        return {"gen": gen}

    return router


async def test_tiers_hold_on_re_enabled_routes(monkeypatch) -> None:
    """The replacement routes wear the same gate as the first mount's:
    admin default for mutations, the household token on
    ``@device_endpoint``, nothing on ``@open_endpoint`` — and the
    disabled-404 ahead of all of it."""
    from domovoi.tests.auth_testkit import HEADER, bearer, install_fake_db

    install_fake_db(
        monkeypatch, admin=True, sessions={"admin-session"},
        device_token="household-token",
    )
    device = {HEADER: "household-token"}
    admin = bearer("admin-session")
    app = FastAPI()
    plugin_http.mount_plugin_routers(app, "demo", [_tiered_router(1)])
    set_plugin_enabled("demo", False)
    plugin_http.mount_plugin_routers(app, "demo", [_tiered_router(2)])

    p = "/v1/plugins/demo"
    async with await _client(app) as client:
        r = await client.get(f"{p}/things")
        assert (r.status_code, r.json()) == (200, {"gen": 2})
        # Admin default.
        assert (await client.post(f"{p}/things")).status_code == 401
        assert (await client.post(f"{p}/things", headers=device)).status_code == 401
        r = await client.post(f"{p}/things", headers=admin)
        assert (r.status_code, r.json()) == (200, {"gen": 2})
        # Device tier.
        assert (await client.post(f"{p}/play")).status_code == 401
        r = await client.post(f"{p}/play", headers=device)
        assert (r.status_code, r.json()) == (200, {"gen": 2})
        assert (await client.post(f"{p}/play", headers=admin)).status_code == 200
        # Open.
        r = await client.post(f"{p}/tune")
        assert (r.status_code, r.json()) == (200, {"gen": 2})

    set_plugin_enabled("demo", False)
    async with await _client(app) as client:
        for method, path in (("GET", "/things"), ("POST", "/things"),
                             ("POST", "/play"), ("POST", "/tune")):
            r = await client.request(method, f"{p}{path}", headers=admin)
            assert r.status_code == 404, (method, path)


async def test_the_route_table_does_not_grow_across_enable_cycles() -> None:
    """N disable / enable cycles: the table, its effective routes and the
    OpenAPI doc stay the size of one mount, each cycle's own routes are
    the ones answering, and the first mount's router is released — a
    torn-down load is not kept alive. ``include_router`` also folds each
    router's lifespan into the app's; that is undone, or every cycle
    would chain one more wrapper holding that load's routers."""
    import gc
    import weakref

    app = FastAPI()
    first = _tiered_router(0)
    released = weakref.ref(first)
    plugin_http.mount_plugin_routers(app, "demo", [first])
    del first
    lifespan = app.router.lifespan_context
    entries = len(app.router.routes)
    effective = len(list(iter_route_contexts(app.routes)))
    paths = len(app.openapi()["paths"])

    for gen in range(1, 6):
        set_plugin_enabled("demo", False)
        plugin_http.mount_plugin_routers(app, "demo", [_tiered_router(gen)])
        assert len(app.router.routes) == entries
        assert len(list(iter_route_contexts(app.routes))) == effective
        assert len(app.openapi()["paths"]) == paths
        async with await _client(app) as client:
            r = await client.get("/v1/plugins/demo/things")
            assert r.json() == {"gen": gen}
    assert app.router.lifespan_context is lifespan
    gc.collect()
    assert released() is None


async def test_a_route_answers_only_for_the_mount_that_included_it() -> None:
    """The gate itself checks that its route belongs to the slug's live
    mount, so a stale route never answers even where the swap could not
    reach it — here, the table of another app the slug was mounted on
    before."""
    old_app, new_app = FastAPI(), FastAPI()
    plugin_http.mount_plugin_routers(old_app, "demo", [_tiered_router(1)])
    plugin_http.mount_plugin_routers(new_app, "demo", [_tiered_router(2)])
    assert await _statuses(old_app, "/things") == [404]
    async with await _client(new_app) as client:
        assert (await client.get("/v1/plugins/demo/things")).json() == {"gen": 2}


_TWO_ROUTER_PLUGIN = '''
from fastapi import APIRouter

first = APIRouter()
second = APIRouter()


@first.get("/first")
async def from_first():
    return {"router": 1}


@second.get("/second")
async def from_second():
    return {"router": 2}


def register(ctx):
    ctx.add_router(first)
    ctx.add_router(second)
'''


class _DemoPlugin:
    """A ``demo`` plugin on disk under ``tmp_path``, loaded by a fresh
    ``PluginLoader`` bound to a fresh core app — the enable / disable
    path the dashboard drives, without the registry."""

    def __init__(self, tmp_path) -> None:
        from domovoi import bootstrap
        from domovoi.plugins_runtime.loader import PluginLoader

        self.root = tmp_path
        self.pkg = tmp_path / "domovoi_plugin_demo"
        self.pkg.mkdir()
        (self.pkg / "__init__.py").write_text("", encoding="utf-8")
        bootstrap.register_nvidia_dlls()
        self.loader = PluginLoader()
        self.app = FastAPI()
        self.loader.bind_app(self.app)

    def write(self, source: str) -> None:
        """(Re)write the core module and forget the imported copy, which
        is what a core restart would do for an upgraded plugin — the
        loader itself never re-imports."""
        import importlib
        import sys

        (self.pkg / "core.py").write_text(source, encoding="utf-8")
        sys.modules.pop("domovoi_plugin_demo.core", None)
        importlib.invalidate_caches()

    async def load(self):
        import textwrap

        from domovoi.plugins_runtime.manifest import parse_manifest

        manifest = parse_manifest(textwrap.dedent('''
            [plugin]
            slug = "demo"
            name = "demo"
            version = "1.0.0"
            publisher = "tests"
            license = "MIT"
            description = "core routers"
            domovoi_api = ">=1.3,<2.0"

            [entry_points]
            core = "domovoi_plugin_demo.core"
        '''))
        return await self.loader.load_plugin(
            slug="demo", install_dir=self.root, manifest=manifest,
            foreign_corpus=[], foreign_web_routes=[], update_registry_status=False,
        )

    async def close(self) -> None:
        import sys

        if "demo" in self.loader.loaded:
            await self.loader.unload_plugin("demo")
        sys.modules.pop("domovoi_plugin_demo.core", None)
        sys.modules.pop("domovoi_plugin_demo", None)
        for entry in (str(self.root), str(self.root.resolve())):
            if entry in sys.path:
                sys.path.remove(entry)


@pytest.fixture
async def demo_plugin(tmp_path):
    plugin = _DemoPlugin(tmp_path)
    try:
        yield plugin
    finally:
        await plugin.close()


async def test_the_loader_mounts_both_routers_and_re_enables_cleanly(demo_plugin) -> None:
    """The path the bug lived on: ``PluginLoader`` → the core app. Both
    routers serve, disable 404s both, and enable brings both back
    without growing the route table."""
    app = demo_plugin.app
    demo_plugin.write(_TWO_ROUTER_PLUGIN)

    await demo_plugin.load()
    assert await _statuses(app, "/first", "/second") == [200, 200]
    size = len(app.router.routes)

    await demo_plugin.loader.unload_plugin("demo")
    assert await _statuses(app, "/first", "/second") == [404, 404]

    await demo_plugin.load()
    assert await _statuses(app, "/first", "/second") == [200, 200]
    assert len(app.router.routes) == size


# ─── Re-enable serves the CURRENT load ────────────────────────────────────
#
# Disable tears a load down (on_disable hooks, workers, handlers, the
# SDK's subscriptions, capabilities and state); enable re-runs register()
# against a fresh PluginSDK, which builds fresh routers. Mounting used to
# be once per slug, so the routes of the FIRST enable kept serving on
# every later one, their closures holding that first, torn-down SDK —
# radio's /state read a state dict nothing wrote to any more.

_STATEFUL_PLUGIN = '''
from fastapi import APIRouter


def register(ctx):
    sdk = ctx.sdk
    sdk.state["loads"] = sdk.state.get("loads", 0) + 1
    router = APIRouter()

    @router.get("/state")
    async def read_state():
        return {"sdk": id(sdk), "loads": sdk.state.get("loads")}

    ctx.add_router(router)
'''


async def _get(app: FastAPI, path: str):
    async with await _client(app) as client:
        return await client.get(f"/v1/plugins/demo{path}")


async def test_re_enable_serves_the_new_load_not_the_torn_down_one(demo_plugin) -> None:
    app = demo_plugin.app
    demo_plugin.write(_STATEFUL_PLUGIN)

    first = (await demo_plugin.load()).sdk
    assert (await _get(app, "/state")).json() == {"sdk": id(first), "loads": 1}

    await demo_plugin.loader.unload_plugin("demo")
    assert (await _get(app, "/state")).status_code == 404

    second = (await demo_plugin.load()).sdk
    assert second is not first
    # The torn-down SDK's state dict still says loads=1 — reading it is
    # the bug. The live load's state is a fresh store.
    assert (await _get(app, "/state")).json() == {"sdk": id(second), "loads": 1}
    second.state["loads"] = 7
    assert (await _get(app, "/state")).json()["loads"] == 7


_V1_PLUGIN = '''
from fastapi import APIRouter


def register(ctx):
    router = APIRouter()

    @router.get("/kept")
    async def kept():
        return {"version": 1}

    @router.get("/dropped")
    async def dropped():
        return {"version": 1}

    ctx.add_router(router)
'''

_V2_PLUGIN = '''
from fastapi import APIRouter


def register(ctx):
    router = APIRouter()

    @router.get("/kept")
    async def kept():
        return {"version": 2}

    @router.get("/added")
    async def added():
        return {"version": 2}

    ctx.add_router(router)
'''


async def test_an_upgraded_route_set_is_served_after_re_enable(demo_plugin) -> None:
    """The plugin comes back with a different set of routes (upgraded in
    place, its module re-imported): the new set serves, a route the new
    version dropped is gone rather than answering from the old code, and
    the live OpenAPI doc says the same."""
    app = demo_plugin.app
    demo_plugin.write(_V1_PLUGIN)
    await demo_plugin.load()
    assert (await _get(app, "/dropped")).json() == {"version": 1}
    size = len(list(iter_route_contexts(app.routes)))

    await demo_plugin.loader.unload_plugin("demo")
    demo_plugin.write(_V2_PLUGIN)
    await demo_plugin.load()

    assert (await _get(app, "/kept")).json() == {"version": 2}
    assert (await _get(app, "/added")).json() == {"version": 2}
    assert (await _get(app, "/dropped")).status_code == 404
    assert len(list(iter_route_contexts(app.routes))) == size
    paths = app.openapi()["paths"]
    assert "/v1/plugins/demo/added" in paths
    assert "/v1/plugins/demo/dropped" not in paths


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
