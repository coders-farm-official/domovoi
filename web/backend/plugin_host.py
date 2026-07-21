"""Plugin hosting for the web dashboard process (design §5).

The web process discovers plugins WITHOUT importing any core plugin
machinery: it reads the ``plugins`` registry table (the ``manifest``
JSONB column is the whole contract) and, for each enabled row whose
manifest declares a ``web`` entry point, imports
``domovoi_plugin_<slug>.web`` and mounts its router. Three surfaces
live here:

* **Import guard** (§5.1) — a ``sys.meta_path`` finder installed at web
  boot that refuses to load any not-yet-imported ``domovoi.*`` module
  other than ``domovoi.webkit`` (and its submodules). The web backend's
  own small set of core imports is preloaded first, so the guard turns
  "plugin web code cannot drag core runtime into this process" into a
  runtime invariant rather than a lint suggestion.
* **Router + static hosting** — plugin routers mount at
  ``/api/plugins/<slug>`` behind a per-slug gate dependency that 404s
  while the slug is disabled (FastAPI can't remove routes; the gate is
  the unmount). Static assets serve from ``<install_dir>/web/static``
  via a single parameterized route with a containment check.
* **Frontend manifest** (§5.2) — ``GET /api/plugins/manifest`` payload:
  scripts, pages, player sources, realtime channels, and the published
  core nav orders, all derived from registry JSONB.

Realtime wiring (§5.3) extends ``NOTIFY_CHANNEL_TO_REALTIME`` and the
poll loop's snapshot helpers from manifest ``[[realtime]]`` entries;
snapshot callables resolve against the plugin web module's module-level
``SNAPSHOTS`` dict and run with a plugin-schema-scoped session.

Hot pickup: the ListenTask subscribes this module's :func:`resync` to
the ``plugins_changed`` NOTIFY channel. New plugins mount live;
*upgraded* plugin web code cannot be re-imported into a running
process (same corollary as design §3.4) — the dashboard shows a
"restart the web process" toast for that case.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text

from web.backend.db import session_scope

log = logging.getLogger(__name__)

# ─── Published core nav orders (§5.2 — versioned API surface) ─────────────
# Plugins slot anywhere; equal values sort core-first then slug.
CORE_NAV: dict[str, int] = {
    "music": 10,
    "podcasts": 12,
    "audiobooks": 14,
    "news": 16,
    "people": 20,
    "satellites": 30,
    "calendar": 40,
    "files": 45,
    "plugins": 95,
    "settings": 100,
}


# ─── Import guard (§5.1) ───────────────────────────────────────────────────

# Core modules the web backend itself legitimately uses. They are
# preloaded by install_import_guard() so later lazy imports resolve from
# sys.modules (the meta_path is never consulted for cached modules).
_WEB_BACKEND_CORE_IMPORTS = (
    "domovoi.webkit",
    "domovoi.config",
    "domovoi.db.session",
    "domovoi.db.repositories",
    "domovoi.admin_auth",
    "domovoi.handlers.shared.library_match",
)


class _WebProcessImportGuard:
    """Meta-path finder that refuses NEW ``domovoi.*`` imports in the web
    process, ``domovoi.webkit`` (and submodules) excepted. Modules already
    in ``sys.modules`` are unaffected — Python resolves those before
    consulting finders — which is exactly the property we want: the web
    backend's own preloaded imports keep working, while a plugin web
    module spelling ``importlib.import_module("domovoi.streaming")`` (or
    any other core-runtime module) fails at runtime, however the import
    is spelled."""

    def find_spec(self, fullname: str, path=None, target=None):
        if fullname == "domovoi" or not fullname.startswith("domovoi."):
            return None
        if fullname == "domovoi.webkit" or fullname.startswith("domovoi.webkit."):
            return None
        raise ImportError(
            f"{fullname!r} is not importable in the web dashboard process — "
            "plugin web modules may import only domovoi.webkit (design §5.1)"
        )


_GUARD: _WebProcessImportGuard | None = None


def install_import_guard() -> None:
    """Preload the web backend's own core imports, then install the
    guard. Idempotent."""
    global _GUARD
    for mod in _WEB_BACKEND_CORE_IMPORTS:
        try:
            importlib.import_module(mod)
        except Exception as e:  # pragma: no cover - defensive
            log.warning("import-guard preload of %s failed: %s", mod, e)
    if _GUARD is None:
        _GUARD = _WebProcessImportGuard()
        sys.meta_path.insert(0, _GUARD)
        log.info("web-process import guard installed (domovoi.* → webkit only)")


def remove_import_guard() -> None:
    """Test hook — uninstall the guard."""
    global _GUARD
    if _GUARD is not None:
        try:
            sys.meta_path.remove(_GUARD)
        except ValueError:
            pass
        _GUARD = None


# ─── Registry access (raw SQL — the web process never imports
#     domovoi.plugins_runtime) ─────────────────────────────────────────────


async def fetch_plugin_rows() -> list[dict[str, Any]]:
    """Every ``plugins`` row, manifest JSONB parsed. Missing table (fresh
    DB mid-migration) degrades to an empty list."""
    try:
        async with session_scope() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT slug, name, version, publisher, license, "
                        "enabled, bundled, install_source, source_ref, "
                        "install_dir, manifest, status, last_error, "
                        "installed_at, updated_at "
                        "FROM plugins ORDER BY slug"
                    )
                )
            ).all()
    except Exception as e:
        log.warning("plugins registry read failed: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        manifest = r.manifest
        if isinstance(manifest, str):
            try:
                manifest = json.loads(manifest)
            except Exception:
                manifest = {}
        out.append(
            {
                "slug": r.slug,
                "name": r.name,
                "version": r.version,
                "publisher": r.publisher,
                "license": r.license,
                "enabled": bool(r.enabled),
                "bundled": bool(r.bundled),
                "install_source": r.install_source,
                "source_ref": r.source_ref,
                "install_dir": r.install_dir,
                "manifest": manifest or {},
                "status": r.status,
                "last_error": r.last_error,
                "installed_at": r.installed_at.isoformat() if r.installed_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
        )
    return out


# ─── The host state ────────────────────────────────────────────────────────


class WebPluginContext:
    """Handed to a plugin's ``register_web(ctx)`` (design §5.1)."""

    def __init__(self, slug: str) -> None:
        from domovoi import webkit

        self.slug = slug
        self.log = logging.getLogger(f"webplugin.{slug}")
        self.core = webkit.CoreClient(slug)
        self.routers: list[APIRouter] = []

        schema = f"plugin_{slug}"

        class _ScopedSession:
            """Async context manager yielding a session whose
            ``search_path`` starts in the plugin's own schema."""

            def __init__(self) -> None:
                self._scope = webkit.session_scope()

            async def __aenter__(self):
                s = await self._scope.__aenter__()
                await s.execute(text(f'SET search_path TO "{schema}", public'))
                return s

            async def __aexit__(self, *exc):
                return await self._scope.__aexit__(*exc)

        self.db_session_scope = _ScopedSession

    def http(self, **kwargs):
        """UA-preset httpx.AsyncClient factory (mirror of ``sdk.http``)."""
        import httpx

        headers = kwargs.pop("headers", {}) or {}
        headers.setdefault(
            "User-Agent",
            "domovoi (+github.com/coders-farm-official/domovoi)",
        )
        return httpx.AsyncClient(headers=headers, **kwargs)

    def add_router(self, router: APIRouter) -> None:
        self.routers.append(router)


class PluginHost:
    """Singleton owning mounted plugin web modules + the cached
    frontend manifest."""

    def __init__(self) -> None:
        self.app: FastAPI | None = None
        self.rows: dict[str, dict[str, Any]] = {}       # slug → registry row
        self.mounted: set[str] = set()                   # router already included
        self.load_errors: dict[str, str] = {}            # slug → error string
        self.snapshots: dict[str, Callable[[], Awaitable[Any]]] = {}
        self._manifest_cache: dict[str, Any] | None = None
        self._resync_lock = asyncio.Lock()
        # web/realtime callbacks installed by main.py at startup:
        self.on_channels_changed: Callable[[], Awaitable[None]] | None = None
        self.broadcast: Callable[[str, dict], Awaitable[None]] | None = None

    # ── helpers ──────────────────────────────────────────────────────
    def row(self, slug: str) -> dict[str, Any] | None:
        return self.rows.get(slug)

    def enabled(self, slug: str) -> bool:
        r = self.rows.get(slug)
        return bool(r and r["enabled"])

    def static_root(self, slug: str) -> Path | None:
        r = self.rows.get(slug)
        if not r:
            return None
        return Path(r["install_dir"]) / "web" / "static"

    # ── mounting ─────────────────────────────────────────────────────
    def _slug_gate(self, slug: str):
        async def gate() -> None:
            if not self.enabled(slug):
                raise HTTPException(
                    status_code=404, detail=f"plugin {slug!r} is not enabled"
                )

        return gate

    def _mount_one(self, row: dict[str, Any]) -> None:
        """Import + register one plugin's web module. Exception-isolated:
        a broken plugin surfaces as a load error chip, never a web-process
        crash."""
        slug = row["slug"]
        manifest = row["manifest"]
        entry_web = (manifest.get("entry_points") or {}).get("web")
        if not entry_web:
            return  # core-only plugin — nothing to mount
        if slug in self.mounted:
            return  # router gate handles enable/disable; re-import is unsafe
        install_dir = row["install_dir"]
        try:
            if install_dir not in sys.path:
                sys.path.insert(0, install_dir)
            module = importlib.import_module(entry_web)
            register_web = getattr(module, "register_web", None)
            if register_web is None:
                raise RuntimeError(f"{entry_web} does not expose register_web(ctx)")
            ctx = WebPluginContext(slug)
            register_web(ctx)
            assert self.app is not None
            from fastapi import Depends

            for router in ctx.routers:
                self.app.include_router(
                    router,
                    prefix=f"/api/plugins/{slug}",
                    dependencies=[Depends(self._slug_gate(slug))],
                    tags=[f"plugin:{slug}"],
                )
            # The frontend StaticFiles mount at "/" is a catch-all that
            # was registered at import time — routes appended after it
            # would never match. Keep it LAST.
            self._sink_static_mount()
            # Snapshot helpers for [[realtime]] wiring (§5.3).
            from web.backend.realtime import CORE_REALTIME_CHANNELS

            snapshots = getattr(module, "SNAPSHOTS", {}) or {}
            for decl in manifest.get("realtime") or []:
                name = decl.get("snapshot")
                channel = decl.get("realtime_channel")
                if not name or not channel or channel in self.snapshots:
                    continue
                # Refuse a realtime_channel that collides with a CORE channel:
                # the snapshot-helper map is a shared namespace, so accepting
                # it would let this plugin overwrite (and, on disable, pop) a
                # core snapshot helper. NOTIFY channels are already namespaced
                # (plugin_<slug>_) elsewhere; this closes the realtime side.
                if channel in CORE_REALTIME_CHANNELS:
                    log.warning(
                        "plugin %s: realtime_channel %r collides with a core "
                        "channel — refusing (a plugin may not displace a core "
                        "snapshot helper)",
                        slug, channel,
                    )
                    continue
                fn = snapshots.get(name)
                if fn is None:
                    log.warning(
                        "plugin %s: [[realtime]] snapshot %r not in SNAPSHOTS",
                        slug, name,
                    )
                    continue
                self.snapshots[channel] = self._snapshot_wrapper(slug, fn)
            self.mounted.add(slug)
            self.load_errors.pop(slug, None)
            log.info("mounted plugin web module %s (%d router(s))",
                     slug, len(ctx.routers))
        except Exception as e:
            self.load_errors[slug] = f"{type(e).__name__}: {e}"
            log.exception("plugin %s web module failed to load", slug)

    def _sink_static_mount(self) -> None:
        """Move the catch-all ``/`` StaticFiles mount (name="static") to
        the end of the route table so dynamically-mounted plugin routes
        stay reachable."""
        assert self.app is not None
        routes = self.app.router.routes
        from starlette.routing import Mount

        static_mounts = [
            r for r in routes
            if isinstance(r, Mount) and getattr(r, "path", None) == ""
            or (isinstance(r, Mount) and getattr(r, "name", None) == "static")
        ]
        for r in static_mounts:
            routes.remove(r)
            routes.append(r)

    @staticmethod
    def _snapshot_wrapper(slug: str, fn) -> Callable[[], Awaitable[Any]]:
        schema = f"plugin_{slug}"

        async def helper() -> Any:
            async with session_scope() as s:
                await s.execute(text(f'SET search_path TO "{schema}", public'))
                return await fn(s)

        return helper

    # ── the resync cycle ─────────────────────────────────────────────
    async def resync(self, app: FastAPI | None = None) -> None:
        """(Re)read the registry, mount anything new, refresh realtime
        wiring, invalidate caches. Called at boot and on every
        ``plugins_changed`` NOTIFY."""
        async with self._resync_lock:
            if app is not None:
                self.app = app
            rows = await fetch_plugin_rows()
            self.rows = {r["slug"]: r for r in rows}
            self._manifest_cache = None

            if self.app is not None:
                for r in rows:
                    if r["enabled"] and r["status"] in ("ok", "degraded"):
                        self._mount_one(r)
                # OpenAPI cache invalidation (§5.4) — the live doc must
                # reflect newly-mounted routers.
                self.app.openapi_schema = None

            # Realtime channel map (§5.3): NOTIFY channel → realtime
            # channel from every enabled manifest, snapshot helpers into
            # the poll loop.
            from web.backend import realtime as rt

            plugin_map: dict[str, str] = {}
            for r in rows:
                if not r["enabled"]:
                    continue
                for decl in r["manifest"].get("realtime") or []:
                    nc, rc = decl.get("notify_channel"), decl.get("realtime_channel")
                    if nc and rc and nc.startswith(f"plugin_{r['slug']}_"):
                        plugin_map[nc] = rc
            rt.set_plugin_notify_channels(plugin_map)
            for channel, helper in self.snapshots.items():
                # Defense in depth: never let a plugin helper key touch a core
                # channel's slot (the pop below would otherwise delete a core
                # helper). _mount_one already refuses these, so this only fires
                # if a colliding channel ever slipped into self.snapshots.
                if channel in rt.CORE_REALTIME_CHANNELS:
                    continue
                # Only expose helpers whose plugin is currently enabled.
                slug_enabled = any(
                    r["enabled"] and channel in {
                        d.get("realtime_channel")
                        for d in (r["manifest"].get("realtime") or [])
                    }
                    for r in rows
                )
                if slug_enabled:
                    rt.StatePollLoop._CHANNEL_HELPERS[channel] = helper
                else:
                    rt.StatePollLoop._CHANNEL_HELPERS.pop(channel, None)

            if self.on_channels_changed is not None:
                try:
                    await self.on_channels_changed()
                except Exception as e:
                    log.warning("listen-channel refresh failed: %s", e)

            if self.broadcast is not None:
                try:
                    await self.broadcast(
                        "plugins.changed",
                        {"data": [
                            {"slug": r["slug"], "enabled": r["enabled"],
                             "version": r["version"], "status": r["status"]}
                            for r in rows
                        ]},
                    )
                except Exception:
                    pass

    # ── frontend manifest (§5.2) ─────────────────────────────────────
    def frontend_manifest(self) -> dict[str, Any]:
        if self._manifest_cache is not None:
            return self._manifest_cache
        plugins: list[dict[str, Any]] = []
        for slug in sorted(self.rows):
            r = self.rows[slug]
            if not r["enabled"] or r["status"] not in ("ok", "degraded"):
                continue
            m = r["manifest"]
            web = m.get("web") or {}
            static_base = f"/plugins/{slug}/static"

            def _asset_url(p: str | None) -> str | None:
                if not p:
                    return None
                rel = p.replace("\\", "/")
                for prefix in ("web/static/", "static/"):
                    if rel.startswith(prefix):
                        rel = rel[len(prefix):]
                        break
                return f"{static_base}/{rel}"

            pages = []
            for pg in web.get("pages") or []:
                pages.append(
                    {
                        "route": pg.get("route"),
                        "page": pg.get("page"),
                        "nav_label": pg.get("nav_label"),
                        "nav_icon": _asset_url(pg.get("nav_icon")),
                        "nav_order": pg.get("nav_order", 50),
                        "badge": pg.get("badge"),
                    }
                )
            realtime_channels = sorted(
                {
                    d.get("realtime_channel")
                    for d in (m.get("realtime") or [])
                    if d.get("realtime_channel")
                }
            )
            plugins.append(
                {
                    "slug": slug,
                    "version": r["version"],
                    "name": r["name"],
                    "status": r["status"],
                    "scripts": [
                        _asset_url(sc) for sc in (web.get("scripts") or [])
                    ],
                    "pages": pages,
                    "player_sources": web.get("player_sources") or [],
                    "realtime_channels": realtime_channels,
                    "web_load_error": self.load_errors.get(slug),
                }
            )
        self._manifest_cache = {
            "domovoi_api": "1.0.0",
            "core_nav": CORE_NAV,
            "plugins": plugins,
        }
        return self._manifest_cache


HOST = PluginHost()


# ─── Open routes: frontend manifest + plugin static assets ────────────────

router = APIRouter(tags=["plugins"])


@router.get("/api/plugins/manifest")
async def plugins_manifest() -> dict[str, Any]:
    """Open (no auth — daily use, §5.2): everything the frontend shell
    needs to load plugin pages without a rebuild."""
    return HOST.frontend_manifest()


@router.get("/plugins/{slug}/static/{path:path}")
async def plugin_static(slug: str, path: str) -> FileResponse:
    """Serve a plugin's ``web/static`` assets. Containment-checked so a
    crafted path can't escape the plugin's static dir."""
    root = HOST.static_root(slug)
    if root is None or not HOST.enabled(slug):
        raise HTTPException(status_code=404, detail=f"plugin {slug!r} not enabled")
    root = root.resolve(strict=False)
    target = (root / path).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes plugin static dir")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"no such asset: {path}")
    return FileResponse(str(target))
