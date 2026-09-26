"""The household DEVICE tier for plugin routes: ``@device_endpoint``.

Plugin routers used to have two tiers — the admin default and
``@open_endpoint`` — so a plugin's everyday mutations (play a station,
star it) either demanded the admin password or answered anyone on the
LAN. A paired phone that was not signed in as an admin could not play the
radio (2026-09-25). ``@device_endpoint`` is the third tier: exactly the
core's own ``admin_auth.require_device`` — the household
``X-Device-Token`` or an admin Bearer, 401 with nothing, 403 on the
dashboard cookie alone, 429 once a source keeps guessing, and the
pre-setup LAN grace.

Both processes mount plugin routers behind one gate body
(``webkit.enforce_route_tier``), so every behavioural test here runs
against BOTH mounts — the core's ``plugin_http.mount_plugin_router`` and
the dashboard's ``PluginHost`` — with the same synthetic plugin router.

DB-free except the last test: the auth primitives are faked at the seam
``auth_testkit.install_fake_db`` patches, and no route body touches
Postgres. The ``requires_db`` test walks the same ground once with real
rows (a claimed admin, the household token the claim rotated in).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from domovoi import admin_auth, plugin_http, webkit
from domovoi.plugins_runtime.contracts import ContractReport, check_route_tiers
from domovoi.tests.auth_testkit import (
    COOKIE,
    HEADER,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    db_device_token,
    install_fake_db,
    make_request,
    web_client,
)
from domovoi.tests.conftest import requires_db
from web.backend.plugin_host import PluginHost

pytestmark = pytest.mark.asyncio

SLUG = "tierdemo"
ADMIN = "admin-session"
TOKEN = "household-token"
DEVICE_401 = f"{HEADER} or admin session required"

# One route per shape the gate tells apart. Loaded as a real plugin web
# module (the web mount imports it through PluginHost) and its router is
# handed to the core mount too — the markers are the same objects either
# way, which is the point.
PLUGIN_WEB = '''
from fastapi import APIRouter
from domovoi.webkit import device_endpoint, open_endpoint

router = APIRouter()


@router.get("/list")
async def list_items():
    return {"items": []}


@router.get("/mine")
@device_endpoint
async def my_items():
    return {"mine": []}


@router.post("/play")
@device_endpoint
async def play():
    return {"played": True}


@router.patch("/items/{item_id}")
@device_endpoint
async def edit(item_id: int):
    return {"edited": item_id}


@router.delete("/items/{item_id}")
@device_endpoint
async def forget(item_id: int):
    return {"forgot": item_id}


@router.post("/import")
async def bulk_import():
    return {"imported": True}


@router.post("/ping")
@open_endpoint
async def ping():
    return {"pong": True}


def register_web(ctx):
    ctx.add_router(router)
'''

DEVICE_MUTATIONS = [("POST", "/play"), ("PATCH", "/items/3"), ("DELETE", "/items/3")]


def _row(install_dir: Path, *, enabled: bool = True) -> dict[str, Any]:
    return {
        "slug": SLUG, "name": SLUG, "version": "1.0.0", "publisher": "Coders Farm",
        "license": "MIT", "enabled": enabled, "bundled": False,
        "install_source": "zip", "source_ref": None, "install_dir": str(install_dir),
        "manifest": {
            "plugin": {"slug": SLUG},
            "entry_points": {"core": f"domovoi_plugin_{SLUG}.core",
                             "web": f"domovoi_plugin_{SLUG}.web"},
        },
        "status": "ok", "last_error": None, "installed_at": None, "updated_at": None,
    }


def _write_plugin(root: Path, source: str) -> None:
    pkg = root / f"domovoi_plugin_{SLUG}"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "web.py").write_text(textwrap.dedent(source), encoding="utf-8")


def _forget_plugin_modules(root: Path) -> None:
    sys.modules.pop(f"domovoi_plugin_{SLUG}.web", None)
    sys.modules.pop(f"domovoi_plugin_{SLUG}", None)
    if str(root) in sys.path:
        sys.path.remove(str(root))


class Mount:
    """One process's mount of the synthetic plugin."""

    def __init__(self, app: FastAPI, prefix: str, disable) -> None:
        self.app = app
        self.prefix = prefix
        self.disable = disable

    def client(self) -> AsyncClient:
        return AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")


@pytest.fixture(params=["core", "web"])
def mount(request, tmp_path: Path):
    """The synthetic plugin mounted by the core (``/v1/plugins/<slug>``)
    or by the dashboard's PluginHost (``/api/plugins/<slug>``)."""
    _write_plugin(tmp_path, PLUGIN_WEB)
    plugin_http._plugin_enabled.pop(SLUG, None)
    plugin_http._mounted.pop(SLUG, None)
    try:
        if request.param == "web":
            app = FastAPI()
            host = PluginHost()
            host.app = app
            row = _row(tmp_path)
            host.rows = {SLUG: row}
            host._mount_one(row)
            assert SLUG not in host.load_errors, host.load_errors

            def _disable() -> None:
                host.rows = {SLUG: {**row, "enabled": False}}

            yield Mount(app, f"/api/plugins/{SLUG}", _disable)
        else:
            if str(tmp_path) not in sys.path:
                sys.path.insert(0, str(tmp_path))
            import importlib

            module = importlib.import_module(f"domovoi_plugin_{SLUG}.web")
            app = FastAPI()
            plugin_http.mount_plugin_router(app, SLUG, module.router)
            yield Mount(
                app, f"/v1/plugins/{SLUG}",
                lambda: plugin_http.set_plugin_enabled(SLUG, False),
            )
    finally:
        plugin_http._plugin_enabled.pop(SLUG, None)
        plugin_http._mounted.pop(SLUG, None)
        _forget_plugin_modules(tmp_path)


@pytest.fixture
def claimed(monkeypatch):
    """A set-up install: one live admin session, one household token."""
    return install_fake_db(monkeypatch, admin=True, sessions={ADMIN}, device_token=TOKEN)


# ─── the exported surface ─────────────────────────────────────────────────


async def test_every_door_hands_out_the_same_marker() -> None:
    """``domovoi.webkit`` (web entry), ``domovoi.plugin_http`` and
    ``domovoi.sdk`` (core entry) export one decorator, one predicate."""
    from domovoi import sdk

    assert "device_endpoint" in webkit.__all__ and "device_endpoint" in sdk.__all__
    assert sdk.device_endpoint is webkit.device_endpoint is plugin_http.device_endpoint
    assert plugin_http.is_device_endpoint is webkit.is_device_endpoint
    assert plugin_http.enforce_route_tier is webkit.enforce_route_tier

    async def route() -> None:  # pragma: no cover — never called
        pass

    assert webkit.endpoint_tier(route) == "admin"
    assert webkit.device_endpoint(route) is route
    assert webkit.is_device_endpoint(route) and not webkit.is_open_endpoint(route)
    assert webkit.endpoint_tier(route) == "device"


# ─── the rule, in both processes ──────────────────────────────────────────


async def test_no_credential_is_401(mount, claimed) -> None:
    async with mount.client() as c:
        for method, path in DEVICE_MUTATIONS:
            r = await c.request(method, f"{mount.prefix}{path}")
            assert r.status_code == 401, (method, path, r.text)
            assert r.json()["detail"] == DEVICE_401
        # The admin default is untouched, and still says so in its own words.
        r = await c.post(f"{mount.prefix}/import")
        assert r.status_code == 401 and r.json()["detail"] == "admin session required"
        # Plain GETs stay open.
        assert (await c.get(f"{mount.prefix}/list")).status_code == 200


async def test_the_household_token_passes(mount, claimed) -> None:
    async with mount.client() as c:
        for method, path in DEVICE_MUTATIONS:
            r = await c.request(method, f"{mount.prefix}{path}", headers={HEADER: TOKEN})
            assert r.status_code == 200, (method, path, r.text)


async def test_an_admin_bearer_passes(mount, claimed) -> None:
    async with mount.client() as c:
        for method, path in DEVICE_MUTATIONS:
            r = await c.request(method, f"{mount.prefix}{path}", headers=bearer(ADMIN))
            assert r.status_code == 200, (method, path, r.text)
        assert (await c.post(f"{mount.prefix}/import", headers=bearer(ADMIN))).status_code == 200


async def test_the_cookie_alone_is_403_on_a_mutation(mount, claimed) -> None:
    """Exactly the core's device-tier answer: rendering a page is not the
    same as acting, so a live dashboard cookie with no token is refused
    403 and the refusal names the header the dashboard should send."""
    async with mount.client() as c:
        c.cookies.set(COOKIE, ADMIN)
        r = await c.post(f"{mount.prefix}/play")
        assert r.status_code == 403, r.text
        assert f"{HEADER} required" in r.json()["detail"]


async def test_the_household_token_is_not_an_admin_credential(mount, claimed) -> None:
    async with mount.client() as c:
        r = await c.post(f"{mount.prefix}/import", headers={HEADER: TOKEN})
        assert r.status_code == 401, r.text
        assert r.json()["detail"] == "admin session required"


async def test_open_endpoint_keeps_its_meaning(mount, claimed) -> None:
    async with mount.client() as c:
        assert (await c.post(f"{mount.prefix}/ping")).json() == {"pong": True}


async def test_a_stale_token_is_401_and_repeated_guesses_are_throttled(mount, claimed) -> None:
    async with mount.client() as c:
        for _ in range(admin_auth.DEVICE_TOKEN_FREE_ATTEMPTS + 1):
            r = await c.post(f"{mount.prefix}/play", headers={HEADER: "wrong"})
            assert r.status_code == 401, r.text
        r = await c.post(f"{mount.prefix}/play", headers={HEADER: "wrong"})
        assert r.status_code == 429, r.text
        assert int(r.headers["Retry-After"]) >= 1


async def test_pre_setup_keeps_the_lan_grace(mount, monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=False)
    async with mount.client() as c:
        for method, path in DEVICE_MUTATIONS:
            assert (await c.request(method, f"{mount.prefix}{path}")).status_code == 200
        assert (await c.post(f"{mount.prefix}/import")).status_code == 200


async def test_a_disabled_plugin_is_404_whatever_the_credential(mount, claimed) -> None:
    mount.disable()
    async with mount.client() as c:
        for method, path in DEVICE_MUTATIONS + [("GET", "/mine")]:
            r = await c.request(method, f"{mount.prefix}{path}", headers={HEADER: TOKEN})
            assert r.status_code == 404, (method, path)


async def test_a_mutation_never_takes_the_token_from_the_query_string(mount, claimed) -> None:
    """``?device_token=`` is a READ credential only (bytes the browser
    fetches by URL). On a device-tier mutation it authorizes nothing — a
    query string lands in logs and Referer headers — so the refusal is
    the plain no-credential 401."""
    async with mount.client() as c:
        for method, path in DEVICE_MUTATIONS:
            r = await c.request(
                method, f"{mount.prefix}{path}", params={"device_token": TOKEN}
            )
            assert r.status_code == 401, (method, path, r.text)
            assert r.json()["detail"] == DEVICE_401


async def test_a_device_get_is_a_device_read(mount, claimed) -> None:
    """On a GET the marker gates the read the way the core's media reads
    are gated: the header, the cookie and ``?device_token=`` all render;
    nothing at all does not."""
    async with mount.client() as c:
        assert (await c.get(f"{mount.prefix}/mine")).status_code == 401
        assert (await c.get(f"{mount.prefix}/mine", headers={HEADER: TOKEN})).status_code == 200
        assert (
            await c.get(f"{mount.prefix}/mine", params={"device_token": TOKEN})
        ).status_code == 200
        c.cookies.set(COOKIE, ADMIN)
        assert (await c.get(f"{mount.prefix}/mine")).status_code == 200


# ─── both markers on one route ────────────────────────────────────────────


async def test_the_markers_refuse_to_stack() -> None:
    async def a() -> None:  # pragma: no cover — never called
        pass

    async def b() -> None:  # pragma: no cover — never called
        pass

    webkit.open_endpoint(a)
    with pytest.raises(webkit.EndpointTierConflict, match="pick one"):
        webkit.device_endpoint(a)
    webkit.device_endpoint(b)
    with pytest.raises(webkit.EndpointTierConflict, match="pick one"):
        webkit.open_endpoint(b)


def _smuggled_router() -> APIRouter:
    """A route whose function carries BOTH markers without going through
    the decorators (a wrapper that copied ``__dict__``, a hand-set
    attribute) — what the later checks exist for."""
    router = APIRouter()

    async def both():
        return {"both": True}

    setattr(both, webkit._OPEN_MARKER, True)
    setattr(both, webkit._DEVICE_MARKER, True)
    router.add_api_route("/both", both, methods=["POST"])
    return router


async def test_both_markers_resolve_to_the_stricter_tier(claimed) -> None:
    """Defence in depth behind the refusals below: a route that slipped
    past them is treated as DEVICE tier, never as open."""
    fn = _smuggled_router().routes[0].endpoint
    assert webkit.endpoint_tier(fn) == "device"
    request = make_request()
    request.scope["method"] = "POST"
    request.scope["endpoint"] = fn
    with pytest.raises(HTTPException) as exc:
        await webkit.enforce_route_tier(request)
    assert exc.value.status_code == 401
    assert exc.value.detail == DEVICE_401


async def test_the_core_contract_check_refuses_both_markers() -> None:
    report = ContractReport()
    check_route_tiers([_smuggled_router()], report)
    assert len(report.errors) == 1
    assert "POST /both" in report.errors[0] and "pick one tier" in report.errors[0]
    ok = ContractReport()
    check_route_tiers([APIRouter()], ok)
    assert ok.errors == []


async def test_the_core_mount_refuses_both_markers() -> None:
    plugin_http._mounted.pop(SLUG, None)
    try:
        with pytest.raises(webkit.EndpointTierConflict, match="POST /both"):
            plugin_http.mount_plugin_router(FastAPI(), SLUG, _smuggled_router())
        assert SLUG not in plugin_http._mounted
    finally:
        plugin_http._plugin_enabled.pop(SLUG, None)
        plugin_http._mounted.pop(SLUG, None)


async def test_the_web_mount_refuses_both_markers(tmp_path: Path) -> None:
    _write_plugin(tmp_path, '''
        from fastapi import APIRouter
        from domovoi import webkit

        router = APIRouter()

        async def both():
            return {}

        setattr(both, webkit._OPEN_MARKER, True)
        setattr(both, webkit._DEVICE_MARKER, True)
        router.add_api_route("/both", both, methods=["POST"])

        def register_web(ctx):
            ctx.add_router(router)
    ''')
    try:
        app = FastAPI()
        host = PluginHost()
        host.app = app
        row = _row(tmp_path)
        host.rows = {SLUG: row}
        host._mount_one(row)
        assert SLUG not in host.mounted
        assert "both @open_endpoint and @device_endpoint" in host.load_errors[SLUG]
        assert not any(
            getattr(r, "path", "").startswith(f"/api/plugins/{SLUG}") for r in app.routes
        )
    finally:
        _forget_plugin_modules(tmp_path)


# ─── a marker the trust screen cannot see never serves ────────────────────
#
# The install preview lists what an AST walk of the source finds: a marker
# DECORATOR on a ``def`` in the package. A marker put on any other way
# moves a route off the admin tier without the admin ever being shown it,
# so both processes hold every runtime tier to that same walk at load and
# refuse the plugin when a route is missing from it — for both markers.

_HIDDEN_TAIL = '''

def register(ctx):
    ctx.add_router(router)


def register_web(ctx):
    ctx.add_router(router)
'''

HIDDEN_MARKERS = {
    # The marker attribute set by hand (spelled so no literal names it).
    "setattr-device": '''
        from fastapi import APIRouter

        router = APIRouter()


        async def sneaky():
            return {"sneaky": True}


        setattr(sneaky, "_domovoi_" + "device_endpoint", True)
        router.add_api_route("/sneaky", sneaky, methods=["POST"])
    ''',
    "setattr-open": '''
        from fastapi import APIRouter

        router = APIRouter()


        async def sneaky():
            return {"sneaky": True}


        setattr(sneaky, "_domovoi_" + "open_endpoint", True)
        router.add_api_route("/sneaky", sneaky, methods=["POST"])
    ''',
    # The real decorator, called rather than stacked on the def.
    "call-device": '''
        from fastapi import APIRouter
        from domovoi.webkit import device_endpoint

        router = APIRouter()


        async def sneaky():
            return {"sneaky": True}


        router.add_api_route("/sneaky", device_endpoint(sneaky), methods=["POST"])
    ''',
    "call-open": '''
        from fastapi import APIRouter
        from domovoi.webkit import open_endpoint

        router = APIRouter()


        async def sneaky():
            return {"sneaky": True}


        router.add_api_route("/sneaky", open_endpoint(sneaky), methods=["POST"])
    ''',
    # The hand-set marker one router down: FastAPI >= 0.139 keeps an
    # included router nested, so a walk of router.routes alone missed it
    # while the gate (which reads the matched endpoint) honoured it.
    "nested-setattr-device": '''
        from fastapi import APIRouter

        inner = APIRouter()


        async def sneaky():
            return {"sneaky": True}


        setattr(sneaky, "_domovoi_" + "device_endpoint", True)
        inner.add_api_route("/sneaky", sneaky, methods=["POST"])
        router = APIRouter()
        router.include_router(inner, prefix="/in")
    ''',
}

# Routes FastAPI mounts with NO router-level dependency — so without a
# refusal they answer with no tier at all, and even while the plugin is
# disabled.
UNGATED_ROUTES = {
    "starlette-route": '''
        from fastapi import APIRouter
        from starlette.responses import JSONResponse

        router = APIRouter()


        async def plain(request):
            return JSONResponse({"plain": True})


        router.add_route("/plain", plain, methods=["POST"])
    ''',
    "mount": '''
        from fastapi import APIRouter
        from starlette.responses import JSONResponse

        router = APIRouter()
        router.mount("/plain", JSONResponse({"mounted": True}))
    ''',
    "nested-starlette-route": '''
        from fastapi import APIRouter
        from starlette.responses import JSONResponse

        inner = APIRouter()


        async def plain(request):
            return JSONResponse({"plain": True})


        inner.add_route("/plain", plain, methods=["POST"])
        router = APIRouter()
        router.include_router(inner, prefix="/in")
    ''',
}

NESTED_DEVICE_ROUTE = '''
    from fastapi import APIRouter
    from domovoi.webkit import device_endpoint

    inner = APIRouter()


    @inner.post("/deep")
    @device_endpoint
    async def deep():
        return {"deep": True}


    router = APIRouter()
    router.include_router(inner, prefix="/in")
'''

ALIASED_MARKER = '''
    from fastapi import APIRouter
    from domovoi.webkit import device_endpoint as household

    router = APIRouter()
    pal = household


    @router.post("/aliased")
    @household
    async def aliased():
        return {"aliased": True}


    @router.post("/chained")
    @pal
    async def chained():
        return {"chained": True}
'''


def _plugin_source(body: str) -> str:
    return textwrap.dedent(body) + _HIDDEN_TAIL


@pytest.mark.parametrize("shape", sorted(HIDDEN_MARKERS))
async def test_the_web_mount_refuses_a_marker_the_preview_cannot_see(
    shape: str, tmp_path: Path, claimed
) -> None:
    _write_plugin(tmp_path, _plugin_source(HIDDEN_MARKERS[shape]))
    try:
        app = FastAPI()
        host = PluginHost()
        host.app = app
        row = _row(tmp_path)
        host.rows = {SLUG: row}
        host._mount_one(row)
        assert SLUG not in host.mounted
        assert "does not list" in host.load_errors[SLUG], host.load_errors
        assert "POST /sneaky" in host.load_errors[SLUG]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.post(f"/api/plugins/{SLUG}/sneaky")).status_code == 404
    finally:
        _forget_plugin_modules(tmp_path)


def _write_core_plugin(root: Path, source: str) -> Path:
    """A loadable core plugin (manifest + package) whose ``core`` module
    is ``source``."""
    _write_plugin(root, source)
    pkg = root / f"domovoi_plugin_{SLUG}"
    (pkg / "core.py").write_text((pkg / "web.py").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "domovoi-plugin.toml").write_text(textwrap.dedent(f'''
        [plugin]
        slug = "{SLUG}"
        name = "{SLUG}"
        version = "1.0.0"
        publisher = "tests"
        license = "MIT"
        description = "generated test plugin"
        domovoi_api = ">=1.3,<2.0"

        [entry_points]
        core = "domovoi_plugin_{SLUG}.core"
    '''), encoding="utf-8")
    return root


async def _load_core(root: Path):
    from domovoi import bootstrap
    from domovoi.plugins_runtime.loader import LOADER
    from domovoi.plugins_runtime.manifest import parse_manifest

    bootstrap.register_nvidia_dlls()
    manifest = parse_manifest((root / "domovoi-plugin.toml").read_text(encoding="utf-8"))
    return await LOADER.load_plugin(
        slug=SLUG, install_dir=root, manifest=manifest,
        foreign_corpus=[], foreign_web_routes=[], update_registry_status=False,
    )


def _forget_core_plugin(root: Path) -> None:
    sys.modules.pop(f"domovoi_plugin_{SLUG}.core", None)
    _forget_plugin_modules(root)
    resolved = str(root.resolve())
    if resolved in sys.path:
        sys.path.remove(resolved)


@pytest.mark.parametrize("shape", sorted(HIDDEN_MARKERS))
async def test_the_core_load_refuses_a_marker_the_preview_cannot_see(
    shape: str, tmp_path: Path
) -> None:
    from domovoi.plugins_runtime.contracts import ContractError
    from domovoi.plugins_runtime.loader import LOADER

    root = _write_core_plugin(tmp_path, _plugin_source(HIDDEN_MARKERS[shape]))
    try:
        with pytest.raises(ContractError) as exc:
            await _load_core(root)
        joined = " ".join(exc.value.errors)
        assert "POST /sneaky" in joined and "does not list" in joined, joined
        assert SLUG not in LOADER.loaded
    finally:
        _forget_core_plugin(root)


@pytest.mark.parametrize("shape", sorted(UNGATED_ROUTES))
async def test_the_web_mount_refuses_a_route_the_gate_cannot_cover(
    shape: str, tmp_path: Path, claimed
) -> None:
    _write_plugin(tmp_path, _plugin_source(UNGATED_ROUTES[shape]))
    try:
        app = FastAPI()
        host = PluginHost()
        host.app = app
        row = _row(tmp_path)
        host.rows = {SLUG: row}
        host._mount_one(row)
        assert SLUG not in host.mounted
        assert "gate cannot cover" in host.load_errors[SLUG], host.load_errors
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            for path in ("/plain", "/in/plain"):
                r = await c.post(f"/api/plugins/{SLUG}{path}")
                assert r.status_code == 404, (path, r.status_code, r.text)
    finally:
        _forget_plugin_modules(tmp_path)


@pytest.mark.parametrize("shape", sorted(UNGATED_ROUTES))
async def test_the_core_refuses_a_route_the_gate_cannot_cover(
    shape: str, tmp_path: Path
) -> None:
    from domovoi.plugins_runtime.contracts import ContractError
    from domovoi.plugins_runtime.loader import LOADER

    root = _write_core_plugin(tmp_path, _plugin_source(UNGATED_ROUTES[shape]))
    try:
        with pytest.raises(ContractError) as exc:
            await _load_core(root)
        joined = " ".join(exc.value.errors)
        assert "without the plugin gate" in joined, joined
        assert SLUG not in LOADER.loaded
        # The mount itself is the last door, for any caller but the loader.
        import importlib

        module = importlib.import_module(f"domovoi_plugin_{SLUG}.core")
        plugin_http._mounted.pop(SLUG, None)
        with pytest.raises(TypeError, match="gate cannot cover"):
            plugin_http.mount_plugin_router(FastAPI(), SLUG, module.router)
        assert SLUG not in plugin_http._mounted
    finally:
        plugin_http._plugin_enabled.pop(SLUG, None)
        plugin_http._mounted.pop(SLUG, None)
        _forget_core_plugin(root)


async def test_a_nested_router_route_keeps_its_tier_and_its_listing(
    tmp_path: Path, claimed
) -> None:
    """A device route one router down is listed, passes the load checks
    and is gated on the device tier."""
    from domovoi.route_markers import scan_marked_endpoints

    _write_plugin(tmp_path, _plugin_source(NESTED_DEVICE_ROUTE))
    try:
        listed = scan_marked_endpoints(tmp_path / f"domovoi_plugin_{SLUG}")
        assert [e["function"] for e in listed["device"]] == ["deep"]
        app = FastAPI()
        host = PluginHost()
        host.app = app
        row = _row(tmp_path)
        host.rows = {SLUG: row}
        host._mount_one(row)
        assert SLUG in host.mounted, host.load_errors
        url = f"/api/plugins/{SLUG}/in/deep"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.post(url)).status_code == 401
            assert (await c.post(url, headers={HEADER: TOKEN})).status_code == 200
    finally:
        _forget_plugin_modules(tmp_path)


async def test_both_markers_one_router_down_are_still_a_conflict() -> None:
    outer = APIRouter()
    outer.include_router(_smuggled_router(), prefix="/in")
    assert webkit.tier_conflicts([outer]) != []
    report = ContractReport()
    check_route_tiers([outer], report)
    assert any("POST /both" in e for e in report.errors), report.errors


async def test_an_aliased_marker_is_listed_and_serves_on_its_tier(
    tmp_path: Path, claimed
) -> None:
    """The walk follows a local alias (``import … as``, ``pal = household``),
    so an honest author who renames the import is listed, not refused."""
    from domovoi.route_markers import scan_marked_endpoints

    _write_plugin(tmp_path, _plugin_source(ALIASED_MARKER))
    try:
        listed = scan_marked_endpoints(tmp_path / f"domovoi_plugin_{SLUG}")
        assert {e["function"] for e in listed["device"]} == {"aliased", "chained"}
        app = FastAPI()
        host = PluginHost()
        host.app = app
        row = _row(tmp_path)
        host.rows = {SLUG: row}
        host._mount_one(row)
        assert SLUG in host.mounted, host.load_errors
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            for path in ("/aliased", "/chained"):
                url = f"/api/plugins/{SLUG}{path}"
                assert (await c.post(url)).status_code == 401
                assert (await c.post(url, headers={HEADER: TOKEN})).status_code == 200
    finally:
        _forget_plugin_modules(tmp_path)


async def test_the_core_load_accepts_an_aliased_marker(tmp_path: Path) -> None:
    from domovoi.plugins_runtime.loader import LOADER

    root = _write_core_plugin(tmp_path, _plugin_source(ALIASED_MARKER))
    try:
        await _load_core(root)
        assert SLUG in LOADER.loaded
    finally:
        if SLUG in LOADER.loaded:
            await LOADER.unload_plugin(SLUG)
        _forget_core_plugin(root)


async def test_the_bundled_radio_routers_match_their_source_walk() -> None:
    """Radio's real routers, held to the walk of its real package — what
    both processes check when they load it: nothing off the admin tier is
    missing from the trust screen."""
    radio_dir = Path(__file__).resolve().parents[2] / "plugins" / "radio"
    if str(radio_dir) not in sys.path:
        sys.path.insert(0, str(radio_dir))
    from domovoi_plugin_radio import core as radio_core
    from domovoi_plugin_radio import web as radio_web

    class _Ctx:
        db_session_scope = None
        core = None

    routers = [radio_web.build_router(_Ctx()), radio_core._build_core_router(None)]
    package = radio_dir / "domovoi_plugin_radio"
    assert webkit.unlisted_tier_routes(routers, package) == []
    report = ContractReport()
    check_route_tiers(routers, report, package)
    assert report.errors == []
    # The same routers against a walk that lists nothing: every device
    # route is reported, so the empty answer above is not vacuous.
    assert len(webkit.unlisted_tier_routes(routers, radio_dir / "missing")) == 6


# ─── the web→core hop carries the household token ─────────────────────────


async def test_core_client_forwards_the_household_token() -> None:
    """A web route on the device tier that proxies to a core route on the
    device tier must hand the caller's token on — otherwise a paired phone
    passes the first hop and is refused by the second."""
    request = make_request(headers={HEADER: TOKEN, "Authorization": "Bearer x"})
    headers = webkit.CoreClient._auth_headers(request)
    assert headers[HEADER] == TOKEN
    assert headers["Authorization"] == "Bearer x"
    assert headers["X-Forwarded-For"] == "192.168.1.50"
    assert HEADER not in webkit.CoreClient._auth_headers(make_request())


# ─── the same ground against the real tables ──────────────────────────────


@requires_db
async def test_the_device_tier_holds_against_the_real_tables(mount, _db) -> None:  # noqa: F811
    """Real rows, no fakes: a claimed admin, the household token first-run
    setup rotated into ``household_device_tokens``, a rotation."""
    async with web_client() as web:
        admin_token = await claim_admin(web)
    device_token = await db_device_token()
    assert device_token, "first-run setup should have left a household token"

    async with mount.client() as c:
        play = f"{mount.prefix}/play"
        r = await c.post(play)
        assert r.status_code == 401 and r.json()["detail"] == DEVICE_401
        assert (await c.post(play, headers={HEADER: device_token})).status_code == 200
        assert (await c.post(play, headers=bearer(admin_token))).status_code == 200
        # The household token is not an admin credential.
        r = await c.post(f"{mount.prefix}/import", headers={HEADER: device_token})
        assert r.status_code == 401, r.text
        # A rotated-out token stops working at once.
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            rotated = await admin_auth.rotate_device_token(s)
        assert (await c.post(play, headers={HEADER: device_token})).status_code == 401
        assert (await c.post(play, headers={HEADER: rotated})).status_code == 200
