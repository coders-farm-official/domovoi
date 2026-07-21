"""Tests for the web dashboard's plugin hosting (design §5) and the
new generic web surfaces (§4.8 readout, §8 capabilities).

Covers:

* the ``sys.meta_path`` import guard — ``domovoi.*`` refuses to load in
  the web process except ``domovoi.webkit`` (§5.1, the testable claim);
* registry-driven mounting: a fake plugin row + on-disk web module gets
  its router mounted at ``/api/plugins/<slug>``, static assets served
  with containment, and everything 404s again once disabled;
* ``GET /api/plugins/manifest`` — the §5.2 frontend payload (scripts,
  pages, player sources, realtime channels, core_nav);
* ``GET /api/capabilities`` — §8: plugin android capabilities + merged
  handler display, served without the core process;
* ``GET /api/acquisitions`` — the generic queue readout, honest about
  core availability.

Lives under ``domovoi/tests`` for the test-DB conftest safety net (the
web backend has no test dir of its own — same as test_realtime_listen).
"""

from __future__ import annotations

import importlib
import textwrap

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from web.backend import plugin_host
from web.backend.main import app as web_app
from web.backend.plugin_host import HOST, install_import_guard, remove_import_guard


def _web() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test")


# ─── Import guard (§5.1) ───────────────────────────────────────────────────


def test_import_guard_blocks_core_modules_but_allows_webkit():
    install_import_guard()
    try:
        # webkit (and its preloaded allies) import fine.
        assert importlib.import_module("domovoi.webkit") is not None
        # A not-yet-imported core module is refused with the guard's
        # message — however the import is spelled.
        with pytest.raises(ImportError, match="web dashboard process"):
            importlib.import_module("domovoi.zz_guard_probe_never_exists")
    finally:
        remove_import_guard()
    # With the guard removed, the same import fails with a NORMAL
    # ModuleNotFoundError (proving the guard was what refused it).
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("domovoi.zz_guard_probe_never_exists")


# ─── Fake plugin fixture ───────────────────────────────────────────────────

_SLUG = "wdemo"

_MANIFEST_JSONB = {
    "plugin": {
        "slug": _SLUG, "name": "Web Demo", "version": "1.0.0",
        "publisher": "Coders Farm", "license": "MIT",
        "description": "test fixture plugin", "domovoi_api": ">=1.0,<2.0",
    },
    "entry_points": {"core": f"domovoi_plugin_{_SLUG}.core",
                     "web": f"domovoi_plugin_{_SLUG}.web"},
    "permissions": {"network": True, "warnings": ["does demo things"]},
    "handlers": [{"name": _SLUG, "band": 400, "label": "Web Demo",
                  "tone": "media"}],
    "web": {
        "scripts": ["web/static/wdemo.jsx"],
        "pages": [{"route": "wdemo", "page": "WdemoPage",
                   "nav_label": "Wdemo", "nav_order": 55}],
        "player_sources": [{"kind": "wdemo",
                            "stream_url_template": "/api/plugins/wdemo/items/{id}/stream"}],
    },
    "realtime": [{"notify_channel": f"plugin_{_SLUG}_items_changed",
                  "realtime_channel": "wdemo.items",
                  "snapshot": "snapshot_items"}],
    "android": {"capabilities": ["wdemo_items"]},
}


@pytest_asyncio.fixture
async def fake_plugin(tmp_path):
    """On-disk plugin web module + registry row; cleaned up after."""
    pkg = tmp_path / f"domovoi_plugin_{_SLUG}"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "web.py").write_text(textwrap.dedent(
        """
        from fastapi import APIRouter

        router = APIRouter()

        @router.get("/hello")
        async def hello():
            return {"hello": "from wdemo"}

        async def snapshot_items(session):
            return {"items": []}

        SNAPSHOTS = {"snapshot_items": snapshot_items}

        def register_web(ctx):
            ctx.add_router(router)
        """
    ), encoding="utf-8")
    static = tmp_path / "web" / "static"
    static.mkdir(parents=True)
    (static / "wdemo.jsx").write_text(
        "window.DomovoiPlugins = window.DomovoiPlugins || {};",
        encoding="utf-8",
    )
    (static / "secret.txt").write_text("asset", encoding="utf-8")

    import json

    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await s.execute(text("DELETE FROM plugins WHERE slug = :slug"), {"slug": _SLUG})
        await s.execute(
            text(
                "INSERT INTO plugins (slug, name, version, publisher, license, "
                "domovoi_api, enabled, bundled, install_source, install_dir, "
                "manifest, status) VALUES (:slug, 'Web Demo', '1.0.0', "
                "'Coders Farm', 'MIT', '>=1.0,<2.0', TRUE, FALSE, 'zip', "
                ":install_dir, CAST(:manifest AS JSONB), 'ok')"
            ),
            {"slug": _SLUG, "install_dir": str(tmp_path),
             "manifest": json.dumps(_MANIFEST_JSONB)},
        )
    try:
        await HOST.resync(web_app)
        yield tmp_path
    finally:
        async with session_scope() as s:
            await s.execute(
                text("DELETE FROM plugins WHERE slug = :slug"), {"slug": _SLUG}
            )
        await HOST.resync(web_app)


# ─── Mounting + manifest (§5.1/§5.2) ───────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_plugin_router_mounts_and_manifest_serves(fake_plugin) -> None:
    async with _web() as client:
        # Router mounted at /api/plugins/<slug>.
        r = await client.get(f"/api/plugins/{_SLUG}/hello")
        assert r.status_code == 200, r.text
        assert r.json() == {"hello": "from wdemo"}

        # Frontend manifest (§5.2) — open, data-driven.
        r = await client.get("/api/plugins/manifest")
        assert r.status_code == 200
        m = r.json()
        assert m["core_nav"]["music"] == 10
        entry = next(p for p in m["plugins"] if p["slug"] == _SLUG)
        assert entry["scripts"] == [f"/plugins/{_SLUG}/static/wdemo.jsx"]
        assert entry["pages"][0]["route"] == "wdemo"
        assert entry["pages"][0]["page"] == "WdemoPage"
        assert entry["player_sources"][0]["kind"] == "wdemo"
        assert entry["realtime_channels"] == ["wdemo.items"]

        # Static serving with containment.
        r = await client.get(f"/plugins/{_SLUG}/static/wdemo.jsx")
        assert r.status_code == 200
        assert "DomovoiPlugins" in r.text
        r = await client.get(f"/plugins/{_SLUG}/static/../../outside.txt")
        assert r.status_code in (400, 404)

        # Installed list carries permissions + declared pages.
        r = await client.get("/api/plugins")
        assert r.status_code == 200
        row = next(p for p in r.json()["plugins"] if p["slug"] == _SLUG)
        assert row["permissions"]["network"] is True
        assert row["pages"][0]["route"] == "wdemo"

    # Snapshot helper registered into the poll loop (§5.3).
    from web.backend.realtime import StatePollLoop, notify_channel_map

    assert "wdemo.items" in StatePollLoop._CHANNEL_HELPERS
    assert notify_channel_map()[f"plugin_{_SLUG}_items_changed"] == "wdemo.items"


@requires_db
@pytest.mark.asyncio
async def test_disabled_plugin_gates_router_and_drops_manifest(fake_plugin) -> None:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await s.execute(
            text("UPDATE plugins SET enabled = FALSE WHERE slug = :slug"),
            {"slug": _SLUG},
        )
    await HOST.resync(web_app)

    async with _web() as client:
        r = await client.get(f"/api/plugins/{_SLUG}/hello")
        assert r.status_code == 404
        r = await client.get(f"/plugins/{_SLUG}/static/wdemo.jsx")
        assert r.status_code == 404
        r = await client.get("/api/plugins/manifest")
        assert all(p["slug"] != _SLUG for p in r.json()["plugins"])

    from web.backend.realtime import StatePollLoop, notify_channel_map

    assert "wdemo.items" not in StatePollLoop._CHANNEL_HELPERS
    assert f"plugin_{_SLUG}_items_changed" not in notify_channel_map()


# ─── /api/capabilities (§8) ────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_capabilities_manifest_serves_without_core(fake_plugin, monkeypatch) -> None:
    # Point the core URL at a dead port — §8 requires the endpoint to
    # work with the core down (registry JSONB + static display table).
    monkeypatch.setenv("DOMOVOI_URL", "http://127.0.0.1:9")
    async with _web() as client:
        r = await client.get("/api/capabilities")
        assert r.status_code == 200
        body = r.json()
        plug = next(p for p in body["plugins"] if p["slug"] == _SLUG)
        assert plug["android_capabilities"] == ["wdemo_items"]
        display = {h["name"]: h for h in body["handler_display"]}
        assert display["music"]["tone"] == "media"       # core static table
        assert display[_SLUG]["label"] == "Web Demo"     # plugin manifest
        assert body["features"].keys() == {"chat", "office"}

        # /manual degrades to the static table when core is down and no
        # cache exists (stale flag set).
        monkeypatch.setenv(
            "DOMOVOI_HOME", str(fake_plugin / "nocache")
        )
        r = await client.get("/api/capabilities/manual")
        assert r.status_code == 200
        assert r.json()["stale"] is True
        assert any(h["name"] == "music" for h in r.json()["handlers"])


# ─── /api/acquisitions (§4.8 readout) ──────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_acquisitions_readout_lists_rows(monkeypatch) -> None:
    from domovoi.db.session import session_scope

    monkeypatch.setenv("DOMOVOI_URL", "http://127.0.0.1:9")
    async with session_scope() as s:
        await s.execute(
            text(
                "INSERT INTO media_acquisitions (kind, text, requested_by) "
                "VALUES ('query', 'test artist test title', 'web')"
            )
        )
    async with _web() as client:
        r = await client.get("/api/acquisitions")
        assert r.status_code == 200
        body = r.json()
        assert any(
            a["text"] == "test artist test title" for a in body["acquisitions"]
        )
        # Core down ⇒ availability is honestly unknown, not fabricated.
        assert body["core_reachable"] is False
        assert body["can_fulfill_query"] is None

        r = await client.get("/api/acquisitions?status=bogus")
        assert r.status_code == 400


# ─── Plugin realtime_channel must not clobber a CORE channel (§5.3) ─────────


@requires_db
@pytest.mark.asyncio
async def test_plugin_realtime_channel_cannot_displace_core_helper(tmp_path) -> None:
    """A plugin declaring ``realtime_channel="news"`` (a core channel) must
    be REFUSED at mount — the core ``news`` snapshot helper stays intact and
    the plugin's helper is never wired under that key. Without the guard the
    plugin would overwrite the core helper (and pop it on disable)."""
    import json

    from domovoi.db.session import session_scope
    from web.backend.realtime import (
        CORE_REALTIME_CHANNELS,
        StatePollLoop,
        _snapshot_news,
    )

    assert "news" in CORE_REALTIME_CHANNELS
    core_helper = StatePollLoop._CHANNEL_HELPERS["news"]
    assert core_helper is _snapshot_news

    slug = "newsclash"
    pkg = tmp_path / f"domovoi_plugin_{slug}"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "web.py").write_text(
        textwrap.dedent(
            """
            from fastapi import APIRouter

            router = APIRouter()

            async def plugin_news_snapshot(session):
                return {"plugin": "clobbered core"}

            SNAPSHOTS = {"plugin_news_snapshot": plugin_news_snapshot}

            def register_web(ctx):
                ctx.add_router(router)
            """
        ),
        encoding="utf-8",
    )

    manifest = {
        "plugin": {
            "slug": slug, "name": "News Clash", "version": "1.0.0",
            "publisher": "Coders Farm", "license": "MIT",
            "description": "collides with core news", "domovoi_api": ">=1.0,<2.0",
        },
        "entry_points": {"core": f"domovoi_plugin_{slug}.core",
                         "web": f"domovoi_plugin_{slug}.web"},
        "handlers": [{"name": slug, "band": 400, "label": "News Clash",
                      "tone": "info"}],
        # The offending declaration: a core channel name.
        "realtime": [{"notify_channel": f"plugin_{slug}_news_changed",
                      "realtime_channel": "news",
                      "snapshot": "plugin_news_snapshot"}],
    }

    async with session_scope() as s:
        await s.execute(text("DELETE FROM plugins WHERE slug = :slug"), {"slug": slug})
        await s.execute(
            text(
                "INSERT INTO plugins (slug, name, version, publisher, license, "
                "domovoi_api, enabled, bundled, install_source, install_dir, "
                "manifest, status) VALUES (:slug, 'News Clash', '1.0.0', "
                "'Coders Farm', 'MIT', '>=1.0,<2.0', TRUE, FALSE, 'zip', "
                ":install_dir, CAST(:manifest AS JSONB), 'ok')"
            ),
            {"slug": slug, "install_dir": str(tmp_path),
             "manifest": json.dumps(manifest)},
        )
    try:
        await HOST.resync(web_app)
        # The core helper is UNTOUCHED — not overwritten by the plugin's.
        assert StatePollLoop._CHANNEL_HELPERS["news"] is core_helper
        assert StatePollLoop._CHANNEL_HELPERS["news"] is _snapshot_news
        # The plugin never got a "news" helper wired for it.
        assert "news" not in HOST.snapshots
    finally:
        async with session_scope() as s:
            await s.execute(
                text("DELETE FROM plugins WHERE slug = :slug"), {"slug": slug}
            )
        await HOST.resync(web_app)
        # Core helper survives the plugin's teardown resync too.
        assert StatePollLoop._CHANNEL_HELPERS["news"] is _snapshot_news
