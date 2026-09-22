"""Plugin HTTP mounting on the core app (design §4.11).

``mount_plugin_router(app, slug, router)`` includes a plugin's
APIRouter at ``/v1/plugins/<slug>/...`` behind two gate dependencies:

* **Enable gate** — FastAPI can't remove routes cleanly, so a disabled
  plugin's routes 404 via a per-slug flag (``set_plugin_enabled``) and
  the router object is reused on re-enable.
* **Auth gate (default DENY for mutations)** — every non-GET route
  requires an admin session unless the endpoint author opted OUT with
  :func:`open_endpoint` (for genuinely daily-use actions; every opt-out
  is listed on the install preview, found by an AST scan of the staged
  package). GETs are open unless the plugin adds its own
  ``Depends(admin_required)``. The marker, the predicate and the gate
  dependency live in :mod:`domovoi.webkit` — the web dashboard process
  mounts plugin routers behind the very same rule (``web.backend.
  plugin_host``), so one decorator means one thing in both processes.

The v1 auth model (scope amendment) is the lightweight one: the admin
gate checks a Bearer token against ``admin_sessions`` (sha256-stored,
30-day sliding expiry — validation shared with the web process via
:mod:`domovoi.admin_auth`). **Until the first-run setup has created the
``admin_auth`` row, the gate allows everything** — the open LAN-trust
posture — so a fresh clone works before the setup flow runs.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import text

from domovoi.admin_auth import check_admin_request
from domovoi.db.session import session_scope
from domovoi.webkit import (  # noqa: F401 — re-exported for plugin authors
    _OPEN_MARKER,
    admin_required,
    is_open_endpoint,
    open_endpoint,
)

log = logging.getLogger(__name__)

# slug → enabled? Populated by mount_plugin_router / the plugin runtime.
_plugin_enabled: dict[str, bool] = {}
# slug → mounted router (reused across disable/enable).
_mounted: dict[str, Any] = {}


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


def _make_gate(slug: str):
    async def _gate(request: Request) -> None:
        if not _plugin_enabled.get(slug, False):
            raise HTTPException(status_code=404, detail=f"plugin {slug!r} disabled")
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return
        # Default-DENY for mutations: allow only explicit opt-outs.
        endpoint = request.scope.get("endpoint")
        if endpoint is not None and is_open_endpoint(endpoint):
            return
        await admin_required(request)
    return _gate


def mount_plugin_router(app: FastAPI, slug: str, router: Any) -> None:
    """Include ``router`` at ``/v1/plugins/<slug>`` behind the enable +
    auth gates, and mark the slug enabled. Re-mounting an
    already-mounted slug just re-enables it (route addition at runtime
    is supported; removal is the 404 gate's job)."""
    from fastapi import Depends

    if slug in _mounted:
        set_plugin_enabled(slug, True)
        return
    app.include_router(
        router,
        prefix=f"/v1/plugins/{slug}",
        dependencies=[Depends(_make_gate(slug))],
        tags=[f"plugin:{slug}"],
    )
    _mounted[slug] = router
    set_plugin_enabled(slug, True)
    # A router added post-startup must invalidate the cached OpenAPI doc.
    app.openapi_schema = None
