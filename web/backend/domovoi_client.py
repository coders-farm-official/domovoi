"""Thin best-effort client for the Domovoi server's admin endpoints.

The web backend runs in a separate process from the Domovoi server, so
in-memory state like ``app.state.active_sessions`` and
``app.state.wifi_status`` isn't directly reachable. A small admin
surface on the core exposes that state; this module is the wrapper the
web backend uses to call those endpoints.

Every method here returns ``None`` on any failure
(connection refused, 404, timeout) so route handlers can degrade
gracefully rather than 500-ing. That means rooms surface as
``status="unknown"`` rather than ``"online"``/``"offline"`` until the
Domovoi server side is wired up — surfaced honestly in the schema.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Cached short-lived snapshot. The poll loop refreshes this; route
# handlers read from it without paying a per-request HTTP round-trip
# back to the Domovoi server (which would dwarf the actual DB query).
_snapshot: dict[str, Any] | None = None


def domovoi_url() -> str:
    return os.environ.get("DOMOVOI_URL", "http://localhost:6370")


async def fetch_admin_snapshot(timeout: float = 1.5) -> dict[str, Any] | None:
    """Pull the Domovoi server's process-state snapshot.

    Expected shape:

    .. code-block:: json

        {
          "active_rooms": ["kitchen", "garage"],
          "resumable_music": {"kitchen": "http://..."},
          "wifi_status": {"kitchen": {"rx_mbits": 39, "tx_mbits": 72, "ssid": "..."}}
        }

    Returns ``None`` on any failure — connection refused, non-200,
    invalid JSON. Callers must handle that case.
    """
    url = f"{domovoi_url().rstrip('/')}/v1/admin/snapshot"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        log.debug("admin snapshot fetch failed: %s", e)
        return None


def get_cached_snapshot() -> dict[str, Any] | None:
    """Return the most-recent snapshot the poll loop fetched, or ``None``."""
    return _snapshot


def set_cached_snapshot(snapshot: dict[str, Any] | None) -> None:
    global _snapshot
    _snapshot = snapshot


async def post_admin(
    path: str,
    body: dict[str, Any] | None = None,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    """POST to a Domovoi server admin endpoint. Returns ``(status_code, json_or_text)``.

    Returns ``(0, None)`` on connection failure so callers can map that
    to a 502 / 503 without inspecting exception types. Default timeout
    is generous (30 s) because some admin paths invoke the LLM tool
    router for a transcribed-style intent — qwen2.5:14b can take a
    few seconds on first warm-up.

    ``headers`` lets proxies for admin-GATED core endpoints forward the
    caller's credentials (see :func:`auth_forward_headers`) — both
    processes validate against the same ``admin_sessions`` table.
    """
    url = f"{domovoi_url().rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=body or {}, headers=headers)
    except Exception as e:
        log.warning("admin POST %s failed: %s", path, e)
        return 0, None
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


async def get_admin(
    path: str,
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    """GET a Domovoi server admin endpoint. Returns ``(status_code, json_or_text)``,
    or ``(0, None)`` on connection failure (callers map that to a 502).
    Mirrors :func:`post_admin` for read-only admin reads like the editable
    config registry, which must come from the Domovoi server process (the web
    process holds a separate, stale ``settings`` copy)."""
    url = f"{domovoi_url().rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, headers=headers)
    except Exception as e:
        log.warning("admin GET %s failed: %s", path, e)
        return 0, None
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


async def delete_admin(
    path: str,
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    """DELETE a Domovoi server admin endpoint. Returns ``(status_code,
    json_or_text)``, or ``(0, None)`` on connection failure (callers map that
    to a 502). Mirrors :func:`post_admin` for admin-gated deletes like the
    satellite pairing reset; ``headers`` forwards the caller's credentials."""
    url = f"{domovoi_url().rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.request("DELETE", url, headers=headers)
    except Exception as e:
        log.warning("admin DELETE %s failed: %s", path, e)
        return 0, None
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def auth_forward_headers(request: Any) -> dict[str, str]:
    """Credential + provenance headers to forward on a web→core hop for
    admin-gated endpoints (design §7.3: both processes validate tokens
    against the same ``admin_sessions`` table, so forwarding the caller's
    ``Authorization``/``Cookie`` verbatim lets the core apply its own
    gate). ``X-Forwarded-For`` carries the real client for the core's
    per-source rate limiting — otherwise every proxied request would
    look like localhost."""
    headers: dict[str, str] = {}
    auth = request.headers.get("authorization")
    if auth:
        headers["Authorization"] = auth
    cookie = request.headers.get("cookie")
    if cookie:
        headers["Cookie"] = cookie
    if request.client is not None:
        headers["X-Forwarded-For"] = request.client.host
    return headers


async def post_admin_bytes(
    path: str,
    body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, bytes, dict[str, str]]:
    """POST to an admin endpoint that returns binary (e.g. synthesized audio).
    Returns ``(status_code, content, headers)``. ``(0, b"", {})`` on a
    connection failure so callers can map it to a 502."""
    url = f"{domovoi_url().rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=body or {})
    except Exception as e:
        log.warning("admin POST(bytes) %s failed: %s", path, e)
        return 0, b"", {}
    return r.status_code, r.content, dict(r.headers)


def bridge_response(status_code: int, payload: Any):
    """Convert a Domovoi server admin call into a FastAPI response.

    ``status_code == 0`` (connection failure) → 502 Bad Gateway. Every
    other status passes through verbatim so the web caller sees the
    Domovoi server's own response — 404 when a room isn't connected,
    503 when nothing's connected, 200 with the announced-to list, etc.
    """
    from fastapi.responses import JSONResponse

    if status_code == 0:
        return JSONResponse(
            status_code=502,
            content={"detail": "domovoi unreachable"},
        )
    content = payload if isinstance(payload, dict) else {"detail": str(payload)}
    return JSONResponse(status_code=status_code, content=content)
