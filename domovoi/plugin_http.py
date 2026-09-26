"""Plugin HTTP mounting on the core app (design §4.11).

``mount_plugin_routers(app, slug, routers)`` includes every APIRouter a
plugin registered at ``/v1/plugins/<slug>/...`` behind two gate
dependencies:

* **Enable gate** — a disabled plugin's routes 404 via a per-slug flag
  (``set_plugin_enabled``); disable never touches the route table. Every
  (re-)enable mounts the routers that load's ``register()`` built IN
  PLACE of the slug's previous ones, because disable tore the previous
  load's SDK down and those routes' closures still hold it
  (:func:`mount_plugin_routers`). The gate also 404s a route whose mount
  is no longer the slug's live one, so a route of a torn-down load never
  answers.
* **Auth gate (default DENY for mutations)** — every non-GET route
  requires an admin session unless the endpoint author put it on another
  tier: :func:`device_endpoint` (daily household actions — the household
  ``X-Device-Token`` or an admin Bearer, exactly the core's own
  ``require_device``) or, rarely, :func:`open_endpoint` (no credential at
  all). Both kinds are listed on the install preview, found by an AST
  scan of the staged package. GETs are open unless the plugin adds its
  own ``Depends(admin_required)`` or marks the GET ``@device_endpoint``.
  The markers, the predicate and the gate body
  (:func:`~domovoi.webkit.enforce_route_tier`) live in
  :mod:`domovoi.webkit` — the web dashboard process mounts plugin routers
  behind the very same rule (``web.backend.plugin_host``), so one
  decorator means one thing in both processes. A route carrying both
  markers is refused (decorator, load-time contract check, and
  :func:`mount_plugin_routers` itself), and so is a route FastAPI would
  mount WITHOUT the gate dependency — a plain Starlette ``Route`` /
  ``Mount`` / ``Host`` (``webkit.ungated_routes``) — and any websocket
  route: the gate is HTTP-only, so plugin websocket routes are not
  supported yet (``webkit.websocket_routes``).

The v1 auth model (scope amendment) is the lightweight one: the admin
gate checks a Bearer token against ``admin_sessions`` (sha256-stored,
30-day sliding expiry — validation shared with the web process via
:mod:`domovoi.admin_auth`). **Until the first-run setup has created the
``admin_auth`` row, the gate allows everything** — the open LAN-trust
posture — so a fresh clone works before the setup flow runs.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import text

from domovoi.admin_auth import check_admin_request
from domovoi.db.session import session_scope
from domovoi.webkit import (  # noqa: F401 — re-exported for plugin authors
    _DEVICE_MARKER,
    _OPEN_MARKER,
    WEBSOCKET_UNSUPPORTED,
    EndpointTierConflict,
    admin_required,
    device_endpoint,
    endpoint_tier,
    enforce_route_tier,
    is_device_endpoint,
    is_open_endpoint,
    open_endpoint,
    tier_conflicts,
    ungated_routes,
    websocket_routes,
)

log = logging.getLogger(__name__)

# slug → enabled? Populated by mount_plugin_routers / the plugin runtime.
_plugin_enabled: dict[str, bool] = {}
# slug → the live mount: every router the current load registered, in
# registration order. A fresh list per mount — the gate of each route
# holds the list its mount created and answers only while it is still
# the one here.
_mounted: dict[str, list[Any]] = {}
# Set on each app route-table entry a mount adds (an ``_IncludedRouter``
# from FastAPI 0.139, the copied ``APIRoute`` objects before it), so the
# next mount of the slug on that app finds exactly its entries to replace.
_SLUG_ATTR = "_domovoi_plugin_slug"
# Serializes mounts. Requests never take it: a mount runs start to finish
# without an await, so on the event loop a request routes against the
# table as it was before the mount or as it is after, never in between.
_MOUNT_LOCK = threading.Lock()


def set_plugin_enabled(slug: str, enabled: bool) -> None:
    _plugin_enabled[slug] = enabled


def plugin_enabled(slug: str) -> bool:
    return _plugin_enabled.get(slug, False)


def mounted_slugs() -> list[str]:
    return sorted(_mounted.keys())


async def has_admin_auth() -> bool:
    """True once the first-run setup has created the ``admin_auth`` row.
    Fail-closed on DB trouble (callers treating False as 'pre-setup'
    must pair this with their own gate — see ``domovoi.auth``)."""
    try:
        async with session_scope() as s:
            return (
                await s.execute(text("SELECT 1 FROM admin_auth WHERE id = 1"))
            ).first() is not None
    except Exception as e:  # pragma: no cover — DB down
        log.warning("admin_auth existence check failed: %s", e)
        return False


async def _admin_ok(request: Request) -> bool:
    """True when the request may perform admin-tier actions. Allows all
    while no admin password has been set up (pre-setup LAN-trust —
    module docstring); afterwards requires a live Bearer session
    (sha256 lookup + sliding expiry via :mod:`domovoi.admin_auth`)."""
    return await check_admin_request(request) in ("ok", "pre-setup")


def _make_gate(slug: str, mount: list[Any] | None = None):
    """The per-slug gate dependency. ``mount`` is the ``_mounted`` entry
    of the mount that includes the route: once the slug is mounted again
    (a later enable, on this app or another) the route 404s like a
    disabled one, whatever the flag says."""
    async def _gate(request: Request) -> None:
        if not _plugin_enabled.get(slug, False) or (
            mount is not None and _mounted.get(slug) is not mount
        ):
            raise HTTPException(status_code=404, detail=f"plugin {slug!r} disabled")
        # Default-DENY for mutations: the admin tier unless the route
        # function carries a tier marker (shared body with the web gate).
        await enforce_route_tier(request)
    return _gate


def _unmount(app: FastAPI, slug: str) -> None:
    """Take the slug's mount down: drop every route-table entry a mount of
    ``slug`` added to ``app``, and forget the live mount (so its routes
    404 on any other app they are still in)."""
    app.router.routes[:] = [
        r for r in app.router.routes if getattr(r, _SLUG_ATTR, None) != slug
    ]
    _mounted.pop(slug, None)
    app.openapi_schema = None


def _refuse(slug: str, routers: list[Any]) -> None:
    """Raise if any route in ``routers`` cannot sit behind the gate. The
    loader's contract check refuses these first (load_error); this is the
    last door, for any other caller."""
    conflicts = tier_conflicts(routers)
    if conflicts:
        raise EndpointTierConflict(
            f"plugin {slug!r}: route(s) carry both @open_endpoint and "
            f"@device_endpoint: {'; '.join(conflicts)}"
        )
    sockets = websocket_routes(routers)
    if sockets:
        # The gate's dependency takes a Request; on a websocket FastAPI
        # cannot call it and the handshake dies with a TypeError. Say why
        # at mount instead.
        raise TypeError(
            f"plugin {slug!r}: {WEBSOCKET_UNSUPPORTED}: {'; '.join(sockets)}"
        )
    ungated = ungated_routes(routers)
    if ungated:
        # A route FastAPI mounts without the gate dependency would answer
        # with no tier and no disabled-404.
        raise TypeError(
            f"plugin {slug!r}: route(s) the plugin gate cannot cover — use "
            f"the router's own decorators / add_api_route: {'; '.join(ungated)}"
        )


def mount_plugin_routers(app: FastAPI, slug: str, routers: Any) -> None:
    """Include every router in ``routers`` — all one load of the plugin
    registered, in order — at ``/v1/plugins/<slug>`` behind the enable +
    auth gates, and mark the slug enabled.

    Mounting is per slug, and each call REPLACES the slug's previous
    mount. Enable re-runs the plugin's ``register()`` against a fresh
    SDK, after disable tore the previous one down (workers, subscriptions,
    state), and the routes it builds close over the new SDK — while the
    previous mount's routes still close over the old one. So the new
    entries take the old ones' place in ``app``'s route table: requests
    reach the current load, a different route set (a plugin upgraded in
    place) serves as registered, and the table never grows across
    enables. The OpenAPI doc is rebuilt on its next read.

    Every router is checked before any is included. A refused set, or an
    empty one, mounts nothing and takes the previous mount down too — it
    belongs to a load that has been torn down."""
    from fastapi import Depends

    routers = list(routers)
    with _MOUNT_LOCK:
        if not routers:
            _unmount(app, slug)
            return
        try:
            _refuse(slug, routers)
        except Exception:
            _unmount(app, slug)
            raise
        table = app.router.routes
        # include_router appends, so everything past ``start`` is ours.
        start = len(table)
        # It also folds each router's lifespan into the app's. Plugins
        # mount from inside the app's lifespan (or later), where that can
        # never run — and on every re-enable it would chain one more
        # wrapper holding that load's routers, and their SDK, alive.
        lifespan = app.router.lifespan_context
        try:
            for router in routers:
                app.include_router(
                    router,
                    prefix=f"/v1/plugins/{slug}",
                    dependencies=[Depends(_make_gate(slug, routers))],
                    tags=[f"plugin:{slug}"],
                )
        except Exception:
            del table[start:]
            _unmount(app, slug)
            raise
        finally:
            app.router.lifespan_context = lifespan
        added = table[start:]
        for entry in added:
            setattr(entry, _SLUG_ATTR, slug)
        before = table[:start]
        old = [i for i, r in enumerate(before) if getattr(r, _SLUG_ATTR, None) == slug]
        kept = [r for r in before if getattr(r, _SLUG_ATTR, None) != slug]
        # The previous mount's position (nothing ahead of it was dropped),
        # else the end — where a first mount lands.
        at = old[0] if old else len(kept)
        table[:] = kept[:at] + added + kept[at:]
        _mounted[slug] = routers
        set_plugin_enabled(slug, True)
        # Routes changed after startup: the cached OpenAPI doc is stale.
        app.openapi_schema = None


def mount_plugin_router(app: FastAPI, slug: str, router: Any) -> None:
    """:func:`mount_plugin_routers` for a plugin with ONE router. The
    per-slug rule applies: a second call for the same slug replaces the
    first one's router rather than adding to it, so a plugin with several
    routers hands them over together (the loader passes ``ctx.routers``
    whole)."""
    mount_plugin_routers(app, slug, [router])
