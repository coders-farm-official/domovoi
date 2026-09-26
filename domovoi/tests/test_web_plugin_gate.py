"""The web dashboard's plugin-router gate (design §5.1, PLG-4).

Every non-GET route on a plugin router mounted by ``web.backend.
plugin_host`` requires an admin session unless the route function is
decorated ``@domovoi.webkit.device_endpoint`` (the household device tier —
exercised in both processes by ``test_plugin_device_tier.py``) or
``@domovoi.webkit.open_endpoint`` — the same default-deny rule the core
applies in ``domovoi.plugin_http``. These tests are DB-free by
construction: the admin check is faked at the seam both gates share
(``webkit.check_admin_request``), the plugin rows are handed to a fresh
``PluginHost`` directly, and no route body that touches Postgres is ever
reached (the gate answers first).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from domovoi import plugin_http, webkit
from web.backend.plugin_host import PluginHost

pytestmark = pytest.mark.asyncio

REPO_ROOT = Path(__file__).resolve().parents[2]
RADIO_DIR = REPO_ROOT / "plugins" / "radio"


# ─── the exported surface ─────────────────────────────────────────────────


async def test_webkit_exports_open_endpoint_and_admin_required() -> None:
    """A plugin's web.py may import only domovoi.webkit — so the gate
    helpers a plugin author needs must be exported from there, and the
    core's plugin_http must hand out the very same objects (one marker,
    one meaning, both processes)."""
    assert "open_endpoint" in webkit.__all__
    assert "admin_required" in webkit.__all__
    assert callable(webkit.open_endpoint)
    assert callable(webkit.admin_required)
    assert plugin_http.open_endpoint is webkit.open_endpoint
    assert plugin_http.admin_required is webkit.admin_required
    assert plugin_http.is_open_endpoint is webkit.is_open_endpoint

    async def route() -> None:  # pragma: no cover — never called
        pass

    assert webkit.is_open_endpoint(route) is False
    assert webkit.open_endpoint(route) is route
    assert webkit.is_open_endpoint(route) is True


# ─── fixtures ─────────────────────────────────────────────────────────────


def _fake_admin(monkeypatch, result: str) -> None:
    """Pin the classification the shared gate sees for every request."""

    async def _check(request, session=None):
        return result

    monkeypatch.setattr(webkit, "check_admin_request", _check)


def _row(slug: str, install_dir: Path, *, enabled: bool = True) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": slug,
        "version": "1.0.0",
        "publisher": "Coders Farm",
        "license": "MIT",
        "enabled": enabled,
        "bundled": False,
        "install_source": "zip",
        "source_ref": None,
        "install_dir": str(install_dir),
        "manifest": {
            "plugin": {"slug": slug},
            "entry_points": {"core": f"domovoi_plugin_{slug}.core",
                             "web": f"domovoi_plugin_{slug}.web"},
        },
        "status": "ok",
        "last_error": None,
        "installed_at": None,
        "updated_at": None,
    }


def _mount(host: PluginHost, app: FastAPI, row: dict[str, Any]) -> None:
    host.app = app
    host.rows = {row["slug"]: row}
    host._mount_one(row)
    assert row["slug"] not in host.load_errors, host.load_errors
    assert row["slug"] in host.mounted


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def gated_plugin(tmp_path: Path):
    """A synthetic plugin web module with one route per shape the gate
    distinguishes, mounted through the real PluginHost on a fresh app."""
    slug = "gatedemo"
    pkg = tmp_path / f"domovoi_plugin_{slug}"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "web.py").write_text(textwrap.dedent(
        """
        from fastapi import APIRouter, Depends
        from domovoi.webkit import admin_required, open_endpoint

        router = APIRouter()

        @router.get("/things")
        async def list_things():
            return {"things": []}

        @router.get("/secrets", dependencies=[Depends(admin_required)])
        async def secrets():
            return {"secret": True}

        @router.post("/things")
        async def create_thing():
            return {"created": True}

        @router.patch("/things/{thing_id}")
        async def patch_thing(thing_id: int):
            return {"patched": thing_id}

        @router.delete("/things/{thing_id}")
        async def delete_thing(thing_id: int):
            return {"deleted": thing_id}

        @router.post("/tune")
        @open_endpoint
        async def tune():
            return {"tuned": True}

        def register_web(ctx):
            ctx.add_router(router)
        """
    ), encoding="utf-8")
    app = FastAPI()
    host = PluginHost()
    row = _row(slug, tmp_path)
    _mount(host, app, row)
    try:
        yield app, host, row
    finally:
        sys.modules.pop(f"domovoi_plugin_{slug}.web", None)
        sys.modules.pop(f"domovoi_plugin_{slug}", None)
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))


# ─── the rule ─────────────────────────────────────────────────────────────


async def test_mutations_return_401_without_an_admin_session(
    gated_plugin, monkeypatch
) -> None:
    app, _host, _row = gated_plugin
    _fake_admin(monkeypatch, "no-auth")
    base = "/api/plugins/gatedemo"
    async with _client(app) as c:
        # GETs stay open for daily use.
        r = await c.get(f"{base}/things")
        assert r.status_code == 200 and r.json() == {"things": []}
        # Every other method is refused with 401 when nothing authenticates.
        for method, path in (
            ("POST", "/things"), ("PATCH", "/things/1"), ("DELETE", "/things/1"),
        ):
            r = await c.request(method, f"{base}{path}")
            assert r.status_code == 401, (method, path, r.text)
            assert r.json()["detail"] == "admin session required"
        # …unless the route function opted out with @open_endpoint.
        r = await c.post(f"{base}/tune")
        assert r.status_code == 200 and r.json() == {"tuned": True}
        # A GET that asked for the gate itself is refused too.
        r = await c.get(f"{base}/secrets")
        assert r.status_code == 401


async def test_mutations_succeed_with_an_admin_session(
    gated_plugin, monkeypatch
) -> None:
    app, _host, _row = gated_plugin
    _fake_admin(monkeypatch, "ok")
    base = "/api/plugins/gatedemo"
    async with _client(app) as c:
        assert (await c.post(f"{base}/things")).json() == {"created": True}
        assert (await c.patch(f"{base}/things/3")).json() == {"patched": 3}
        assert (await c.delete(f"{base}/things/3")).json() == {"deleted": 3}
        assert (await c.get(f"{base}/secrets")).status_code == 200


async def test_cookie_only_renders_gets_but_never_mutates(
    gated_plugin, monkeypatch
) -> None:
    """The dashboard cookie may render GET state; a mutation must carry a
    Bearer (§7.3) — cookie-only is 403, not 401, so the dashboard can
    tell "sign in" from "send the header"."""
    app, _host, _row = gated_plugin
    _fake_admin(monkeypatch, "cookie-only")
    base = "/api/plugins/gatedemo"
    async with _client(app) as c:
        assert (await c.get(f"{base}/things")).status_code == 200
        assert (await c.get(f"{base}/secrets")).status_code == 200
        r = await c.post(f"{base}/things")
        assert r.status_code == 403
        assert "Bearer" in r.json()["detail"]


async def test_pre_setup_keeps_the_lan_trust_grace(
    gated_plugin, monkeypatch
) -> None:
    """Before first-run setup has created the admin credential the core
    gate allows everything (a fresh clone works before setup); the web
    gate mirrors that so both processes agree."""
    app, _host, _row = gated_plugin
    _fake_admin(monkeypatch, "pre-setup")
    async with _client(app) as c:
        assert (await c.post("/api/plugins/gatedemo/things")).status_code == 200


async def test_disabled_plugin_is_404_for_every_method(
    gated_plugin, monkeypatch
) -> None:
    app, host, row = gated_plugin
    _fake_admin(monkeypatch, "ok")
    host.rows = {row["slug"]: {**row, "enabled": False}}
    async with _client(app) as c:
        for method, path in (("GET", "/things"), ("POST", "/things"), ("POST", "/tune")):
            r = await c.request(method, f"/api/plugins/gatedemo{path}")
            assert r.status_code == 404, (method, path)


async def test_re_enable_serves_the_one_live_registration(tmp_path: Path) -> None:
    """The web process tears nothing down on disable — the gate is the
    whole unmount — and an enable finds the slug mounted and re-runs
    nothing. So the routes answering after a re-enable are the ones
    ``register_web`` built, holding the context it was handed, which is
    still the live one. (The core DOES tear its SDK down on disable, so
    it replaces the slug's routes on every enable instead:
    ``test_plugin_http.py``.)"""
    slug = "webcycle"
    pkg = tmp_path / f"domovoi_plugin_{slug}"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "web.py").write_text(textwrap.dedent(
        """
        from fastapi import APIRouter

        CONTEXTS = []

        def register_web(ctx):
            CONTEXTS.append(ctx)
            router = APIRouter()

            @router.get("/ctx")
            async def which():
                return {"ctx": id(ctx), "registrations": len(CONTEXTS)}

            ctx.add_router(router)
        """
    ), encoding="utf-8")
    app, host, row = FastAPI(), PluginHost(), _row(slug, tmp_path)
    try:
        _mount(host, app, row)
        contexts = sys.modules[f"domovoi_plugin_{slug}.web"].CONTEXTS
        live = {"ctx": id(contexts[0]), "registrations": 1}
        size = len(app.router.routes)
        async with _client(app) as c:
            assert (await c.get(f"/api/plugins/{slug}/ctx")).json() == live

        host.rows = {slug: {**row, "enabled": False}}
        async with _client(app) as c:
            assert (await c.get(f"/api/plugins/{slug}/ctx")).status_code == 404

        # What resync does for an enabled row.
        host.rows = {slug: row}
        host._mount_one(row)
        async with _client(app) as c:
            assert (await c.get(f"/api/plugins/{slug}/ctx")).json() == live
        assert len(contexts) == 1
        assert len(app.router.routes) == size
    finally:
        sys.modules.pop(f"domovoi_plugin_{slug}.web", None)
        sys.modules.pop(f"domovoi_plugin_{slug}", None)
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))




# ─── the bundled radio plugin under the gate ──────────────────────────────
#
# Radio's everyday mutations are @device_endpoint (a paired phone plays
# the radio without the admin password); the FCC bulk import keeps the
# admin default. Each route's tier is asserted against the real router
# mounted through the real PluginHost, with the auth primitives faked
# (auth_testkit.install_fake_db) and a context whose database and core
# client answer 418 — so "418" reads "the gate let it through to the
# handler" and any refusal is the gate's own answer.

RADIO_DEVICE_MUTATIONS = [
    ("POST", "/play"),
    ("POST", "/stations"),
    ("PATCH", "/stations/1"),
    ("DELETE", "/stations/1"),
    ("POST", "/stations/1/resolve-simulcast"),
]
RADIO_ADMIN_MUTATIONS = [("POST", "/fcc-import")]
RADIO_ADMIN = "admin-session"
RADIO_TOKEN = "household-token"


class _TeapotSession:
    """A db_session_scope whose every use answers 418 — proof the route
    body ran, which only a request the gate admitted can reach."""

    def __call__(self):
        return self

    async def __aenter__(self):
        from fastapi import HTTPException

        raise HTTPException(status_code=418, detail="handler ran")

    async def __aexit__(self, *exc):  # pragma: no cover
        return False


@pytest.fixture
def radio_app(monkeypatch):
    """The real radio web router mounted through the PluginHost, against
    a context whose database and core client answer 418."""
    if str(RADIO_DIR) not in sys.path:
        sys.path.insert(0, str(RADIO_DIR))
    from fastapi import HTTPException

    from web.backend import plugin_host as ph

    class _Ctx:
        def __init__(self, slug: str) -> None:
            import logging

            self.slug = slug
            self.log = logging.getLogger(f"webplugin.{slug}")
            self.db_session_scope = _TeapotSession()
            self.routers: list[Any] = []

            class _Core:
                async def get(self, path, *, params=None):
                    return {"state": "idle"}

                async def post(self, *a, **kw):  # pragma: no cover
                    raise HTTPException(status_code=418, detail="core reached")

                async def post_admin(self, *a, **kw):
                    raise HTTPException(status_code=418, detail="core reached")

            self.core = _Core()

        def add_router(self, router) -> None:
            self.routers.append(router)

    monkeypatch.setattr(ph, "WebPluginContext", _Ctx)
    app = FastAPI()
    host = PluginHost()
    _mount(host, app, _row("radio", RADIO_DIR))
    return app


def _radio_claimed(monkeypatch) -> None:
    from domovoi.tests.auth_testkit import install_fake_db

    install_fake_db(
        monkeypatch, admin=True, sessions={RADIO_ADMIN}, device_token=RADIO_TOKEN
    )


async def _radio_call(c: AsyncClient, method: str, path: str, **kw):
    return await c.request(
        method, f"/api/plugins/radio{path}", json={"name": "x", "station_id": 1}, **kw
    )


@pytest.mark.parametrize("method,path", RADIO_DEVICE_MUTATIONS + RADIO_ADMIN_MUTATIONS)
async def test_radio_mutations_return_401_unauthenticated(
    radio_app, monkeypatch, method: str, path: str
) -> None:
    _radio_claimed(monkeypatch)
    async with _client(radio_app) as c:
        r = await _radio_call(c, method, path)
        assert r.status_code == 401, (method, path, r.text)


@pytest.mark.parametrize("method,path", RADIO_DEVICE_MUTATIONS)
async def test_radio_daily_mutations_take_the_household_token(
    radio_app, monkeypatch, method: str, path: str
) -> None:
    """Kamron's phone: paired, not signed in as an admin. Play, the star,
    the edits, forget and the simulcast lookup all go through."""
    _radio_claimed(monkeypatch)
    from domovoi.tests.auth_testkit import HEADER

    async with _client(radio_app) as c:
        r = await _radio_call(c, method, path)
        assert r.json()["detail"] == f"{HEADER} or admin session required"
        r = await _radio_call(c, method, path, headers={HEADER: RADIO_TOKEN})
        assert r.status_code == 418, (method, path, r.text)
        r = await _radio_call(
            c, method, path, headers={"Authorization": f"Bearer {RADIO_ADMIN}"}
        )
        assert r.status_code == 418, (method, path, r.text)


@pytest.mark.parametrize("method,path", RADIO_DEVICE_MUTATIONS)
async def test_radio_daily_mutations_refuse_the_cookie_alone(
    radio_app, monkeypatch, method: str, path: str
) -> None:
    _radio_claimed(monkeypatch)
    from domovoi.tests.auth_testkit import COOKIE

    async with _client(radio_app) as c:
        c.cookies.set(COOKIE, RADIO_ADMIN)
        r = await _radio_call(c, method, path)
        assert r.status_code == 403, (method, path, r.text)


async def test_radio_fcc_import_stays_admin(radio_app, monkeypatch) -> None:
    """The bulk import is a long server-side job, like the core's library
    sweeps: the household token does not start it; an admin Bearer does."""
    _radio_claimed(monkeypatch)
    from domovoi.tests.auth_testkit import HEADER

    async with _client(radio_app) as c:
        r = await c.post("/api/plugins/radio/fcc-import", headers={HEADER: RADIO_TOKEN})
        assert r.status_code == 401, r.text
        assert r.json()["detail"] == "admin session required"
        r = await c.post(
            "/api/plugins/radio/fcc-import",
            headers={"Authorization": f"Bearer {RADIO_ADMIN}"},
        )
        assert r.status_code == 418, r.text


async def test_radio_reads_stay_open(radio_app, monkeypatch) -> None:
    _fake_admin(monkeypatch, "no-auth")
    async with _client(radio_app) as c:
        r = await c.get("/api/plugins/radio/fcc-import")
        assert r.status_code == 200
