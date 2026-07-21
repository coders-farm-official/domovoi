"""Plugin management API for the dashboard (design §3, §7.3, §7.5).

Reads come straight from the ``plugins`` registry table (the web
process shares the core's Postgres). Mutations — install, confirm,
enable, disable, uninstall, upgrade — are owned by the CORE process
(:6370), which runs pip, migrations, and the hot loader; this module
proxies them verbatim with the caller's admin credentials forwarded
(both processes validate against the same ``admin_sessions`` table),
so an unauthenticated caller gets the core's own 401 and the dashboard
pops its login modal.

The two-phase install flow (§3.2) passes through unchanged: Phase A
returns ``{staged_id, preview}`` (the preview carries permissions,
warnings, the resolved dependency tree, and the trust statement the
§7.5 confirm screen must render unskippably); Phase B confirms by
``staged_id``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from web.backend.domovoi_client import auth_forward_headers
from web.backend.plugin_host import HOST

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

# Generous timeout: confirm runs pip + migrations + hot load.
_PROXY_TIMEOUT = float(os.environ.get("WEB_PLUGIN_PROXY_TIMEOUT_SEC", "600"))


def _core_url() -> str:
    return os.environ.get("DOMOVOI_URL", "http://localhost:6370")


async def _proxy_post(request: Request, core_path: str) -> JSONResponse:
    """Re-send the incoming request body (JSON or multipart zip upload)
    to the core, auth + source headers forwarded, response verbatim."""
    headers = auth_forward_headers(request)
    content_type = request.headers.get("content-type")
    if content_type:
        headers["Content-Type"] = content_type
    body = await request.body()
    url = f"{_core_url().rstrip('/')}{core_path}"
    try:
        async with httpx.AsyncClient(timeout=_PROXY_TIMEOUT) as client:
            r = await client.post(url, content=body, headers=headers)
    except Exception as e:
        log.warning("plugin admin proxy %s failed: %s", core_path, e)
        raise HTTPException(
            status_code=503,
            detail="the Domovoi core service is not running — plugin "
                   "management needs it up",
        )
    try:
        payload: Any = r.json()
    except Exception:
        payload = {"detail": r.text}
    if r.status_code >= 400:
        # Surface the core's error detail in the server log too — the
        # uvicorn access line alone ("422 Unprocessable Entity") hides
        # the reason the dashboard toast shows.
        log.warning(
            "plugin admin proxy %s -> %s: %.500s",
            core_path, r.status_code, payload,
        )
    return JSONResponse(status_code=r.status_code, content=payload)


# NOTE: ``/api/plugins/manifest`` (the open frontend manifest) is served
# by web.backend.plugin_host.router — mounted before this router so the
# literal path wins over the ``/{slug}`` matchers below.


@router.get("")
async def list_installed() -> dict[str, Any]:
    """Installed plugins for the admin page: registry row + the fields
    the list renders (permissions, capabilities, pages). Open read —
    §12 keeps the registry list open; errors show for everyone."""
    plugins: list[dict[str, Any]] = []
    for slug in sorted(HOST.rows):
        r = HOST.rows[slug]
        m = r["manifest"] or {}
        plugin_tbl = m.get("plugin") or {}
        caps = m.get("capabilities") or {}
        plugins.append(
            {
                "slug": slug,
                "name": r["name"],
                "version": r["version"],
                "publisher": r["publisher"],
                "license": r["license"],
                "description": plugin_tbl.get("description"),
                "homepage": plugin_tbl.get("homepage"),
                "enabled": r["enabled"],
                "bundled": r["bundled"],
                "install_source": r["install_source"],
                "source_ref": r["source_ref"],
                "status": r["status"],
                "last_error": r["last_error"],
                "installed_at": r["installed_at"],
                "updated_at": r["updated_at"],
                "permissions": m.get("permissions") or {},
                "provides": caps.get("provides") or [],
                "consumes": caps.get("consumes") or [],
                "handlers": m.get("handlers") or [],
                "pages": ((m.get("web") or {}).get("pages")) or [],
                "android_capabilities": (m.get("android") or {}).get(
                    "capabilities"
                ) or [],
                "web_load_error": HOST.load_errors.get(slug),
            }
        )
    return {"plugins": plugins}


@router.get("/{slug}/purge-preview")
async def purge_preview(slug: str) -> dict[str, Any]:
    """What uninstall-with-purge would drop (§3.5): the plugin schema's
    tables + row counts. Rendered in the keep-vs-purge dialog."""
    if slug not in HOST.rows:
        raise HTTPException(status_code=404, detail=f"plugin {slug!r} not found")
    from sqlalchemy import text

    from web.backend.db import session_scope

    schema = f"plugin_{slug}"
    tables: list[dict[str, Any]] = []
    async with session_scope() as s:
        names = (
            await s.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema ORDER BY table_name"
                ),
                {"schema": schema},
            )
        ).scalars().all()
        for name in names:
            if name == "schema_history":
                continue
            count = (
                await s.execute(
                    text(f'SELECT COUNT(*) FROM "{schema}"."{name}"')
                )
            ).scalar_one()
            tables.append({"table": name, "rows": int(count)})
    return {"schema": schema, "tables": tables}


@router.post("/install")
async def install(request: Request) -> JSONResponse:
    """Phase A — stage & validate (zip upload or ``{"github_url": ...}``).
    Admin-gated by the core."""
    return await _proxy_post(request, "/v1/plugins/install")


@router.post("/install/{staged_id}/confirm")
async def confirm(request: Request, staged_id: str) -> JSONResponse:
    """Phase B — the user has read the §7.5 trust screen."""
    return await _proxy_post(request, f"/v1/plugins/install/{staged_id}/confirm")


@router.post("/{slug}/enable")
async def enable(request: Request, slug: str) -> JSONResponse:
    return await _proxy_post(request, f"/v1/plugins/{slug}/enable")


@router.post("/{slug}/disable")
async def disable(request: Request, slug: str) -> JSONResponse:
    return await _proxy_post(request, f"/v1/plugins/{slug}/disable")


@router.post("/{slug}/uninstall")
async def uninstall(request: Request, slug: str) -> JSONResponse:
    """Body: ``{"data": "keep" | "purge"}`` (§3.5)."""
    return await _proxy_post(request, f"/v1/plugins/{slug}/uninstall")


@router.post("/{slug}/upgrade")
async def upgrade(request: Request, slug: str) -> JSONResponse:
    return await _proxy_post(request, f"/v1/plugins/{slug}/upgrade")
