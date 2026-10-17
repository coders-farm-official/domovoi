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

# A plugin zip this process will hold in memory before handing it to the
# core. 64 MB is far above any real plugin (the bundled radio plugin is
# tens of KB) and far below "fill the box's RAM by POSTing to a route the
# core has not authorized yet".
_MAX_PROXY_BODY_BYTES = int(
    os.environ.get("WEB_PLUGIN_MAX_UPLOAD_BYTES", str(64 * 1024 * 1024))
)


def _core_url() -> str:
    return os.environ.get("DOMOVOI_URL", "http://localhost:6370")


async def _proxy_post(request: Request, core_path: str) -> JSONResponse:
    """Re-send the incoming request body (JSON or multipart zip upload)
    to the core, auth + source headers forwarded, response verbatim —
    once this process has re-read the registry (:func:`_resync_registry`)."""
    headers = auth_forward_headers(request)
    content_type = request.headers.get("content-type")
    if content_type:
        headers["Content-Type"] = content_type
    # Size first, body second: the core is the one that decides whether this
    # caller may install anything, and until it has, nothing justifies
    # buffering an arbitrary upload here (WEB-8).
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > _MAX_PROXY_BODY_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"plugin upload too large (limit {_MAX_PROXY_BODY_BYTES} bytes)",
        )
    body = await request.body()
    if len(body) > _MAX_PROXY_BODY_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"plugin upload too large (limit {_MAX_PROXY_BODY_BYTES} bytes)",
        )
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
    await _resync_registry(core_path)
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


async def _resync_registry(core_path: str) -> None:
    """Re-read the plugins registry before the core's answer goes back
    (F-054). The Plugins page reads the list right after an action, and
    the list is this process's copy of the registry: left to the
    ``plugins_changed`` NOTIFY alone, it could still hold a state the
    action had already moved past (an upgraded plugin missing, a refused
    one ``ok``). Whatever the core answered, a refusal included: a refused
    load is a registry write too. Never fails the action."""
    try:
        await HOST.resync()
    except Exception as e:  # noqa: BLE001 - the NOTIFY path still runs
        log.warning("plugin registry re-read after %s failed: %s", core_path, e)


# NOTE: ``/api/plugins/manifest`` (the open frontend manifest) is served
# by web.backend.plugin_host.router — mounted before this router so the
# literal path wins over the ``/{slug}`` matchers below.


# The core's word on its running workers costs the list this long at
# most: a core that is down or frozen leaves the notes out, nothing else.
_NOTES_TIMEOUT_SEC = 1.5


async def _worker_notes() -> dict[str, list[dict[str, str]]]:
    """slug -> what that plugin's running workers say about themselves
    (``[{worker, note}]``: the radio fingerprinter paused after failures
    in a row, say), from the core's GET /v1/plugins, which runs them.
    Best effort: {} when the core doesn't answer."""
    url = f"{_core_url().rstrip('/')}/v1/plugins"
    try:
        async with httpx.AsyncClient(timeout=_NOTES_TIMEOUT_SEC) as client:
            r = await client.get(url)
        rows = r.json().get("plugins") if r.status_code == 200 else None
    except Exception:  # noqa: BLE001 - down, frozen, or an older core
        return {}
    out: dict[str, list[dict[str, str]]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("slug"), str):
            continue
        notes = [
            {"worker": str(n.get("worker") or "")[:100], "note": str(n["note"])[:300]}
            for n in (row.get("worker_notes") or [])
            if isinstance(n, dict) and n.get("note")
        ]
        if notes:
            out[row["slug"]] = notes
    return out


@router.get("")
async def list_installed() -> dict[str, Any]:
    """Installed plugins for the admin page: registry row + the fields
    the list renders (permissions, capabilities, pages), and what the
    plugin's running workers say about themselves (``worker_notes``, from
    the core). Open read — §12 keeps the registry list open; errors show
    for everyone."""
    plugins: list[dict[str, Any]] = []
    notes = await _worker_notes()
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
                "page_errors": HOST.page_route_errors(slug),
                "android_capabilities": (m.get("android") or {}).get(
                    "capabilities"
                ) or [],
                "web_load_error": HOST.load_errors.get(slug),
                "worker_notes": notes.get(slug, []),
                # The container guard (SDK 1.5): what the plugin declares it
                # runs, and whether a plugin that needs Docker declared
                # nothing — it still works, it just isn't watched.
                **_container_fields(m),
            }
        )
    return {"plugins": plugins}


def _container_fields(manifest: dict[str, Any]) -> dict[str, Any]:
    from domovoi.container_guard.expected import declared_containers, needs_docker

    declared = declared_containers(manifest)
    return {
        "containers": [
            {
                "id": d.id,
                "name": d.name,
                "image": d.image,
                "pinned": "@sha256:" in d.image,
                "required": d.required,
                "label": d.label,
                "allow": sorted(d.allow),
            }
            for d in declared
        ],
        "containers_watched": bool(declared),
        "containers_unwatched": not declared and needs_docker(manifest),
    }


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
