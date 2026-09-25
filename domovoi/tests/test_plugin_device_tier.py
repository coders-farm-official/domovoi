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
