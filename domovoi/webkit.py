"""``domovoi.webkit`` — the ONLY ``domovoi.*`` module a plugin's web
entry may import (design §5.1/§5.2).

The web dashboard runs as a separate process (:6369) that must never
load core-runtime code (DLL-order-sensitive imports, single-process
state, worker machinery). Plugin ``web.py`` modules therefore get a
deliberately tiny surface:

* :func:`session_scope` / ``SessionLocal`` — the shared async-Postgres
  session factory (same DB both processes read).
* :class:`CoreClient` / :exc:`CoreDown` — the typed
  HTTP client for calling the core service (:6370), with admin-auth
  forwarding for gated endpoints.
* :func:`paginate` — the offset/limit clamp helper most list endpoints
  want.
* :func:`open_endpoint` / :func:`admin_required` — the auth surface for
  plugin routers, shared with the core process (``domovoi.plugin_http``
  re-exports these). Both processes mount plugin routers behind the same
  default-deny gate: every non-GET route requires an admin session unless
  its function is decorated ``@open_endpoint``; a GET that wants gating
  adds ``Depends(admin_required)``.
* :mod:`domovoi.net_safety` (re-exported as ``webkit.net_safety``) — the
  shared outbound-URL check and its safe fetchers, so a plugin web module
  that fetches a caller-chosen URL goes through the same gate the core
  does.

Everything else in ``domovoi.*`` is refused at runtime in the web
process by a ``sys.meta_path`` guard (``web.backend.plugin_host``), so
"imports only webkit" is an enforced invariant, not a convention. This
module must therefore keep its own import footprint minimal: db.session,
admin_auth (already part of the web backend's preloaded core set) plus
stdlib/httpx/fastapi only.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

import httpx
from fastapi import HTTPException, Request

from domovoi import net_safety
from domovoi.admin_auth import check_admin_request
from domovoi.db.session import SessionLocal, engine, session_scope

__all__ = [
    "SessionLocal",
    "engine",
    "net_safety",
    "session_scope",
    "CoreClient",
    "CoreDown",
    "core_url",
    "paginate",
    "open_endpoint",
    "is_open_endpoint",
    "admin_required",
]

log = logging.getLogger(__name__)

# Attribute the gates look for on a route function. One marker for both
# processes so a plugin author learns exactly one decorator.
_OPEN_MARKER = "_domovoi_open_endpoint"


def open_endpoint(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Opt a plugin route OUT of the default admin gate (design §4.11 core,
    §5.1 web). For genuinely daily-use mutations (a tune / play action);
    every opted-out route is listed on the install preview as part of the
    plugin's open surface. Stack it directly on the route function::

        @router.post("/tune")
        @open_endpoint
        async def tune(...): ...
    """
    setattr(fn, _OPEN_MARKER, True)
    return fn


def is_open_endpoint(fn: Callable[..., Any]) -> bool:
    return bool(getattr(fn, _OPEN_MARKER, False))


async def admin_required(request: Request) -> None:
    """Shared admin-gate dependency (usable by plugin GETs that want
    gating: ``Depends(admin_required)``). Mutations behind this gate are
    Bearer-only — a cookie-only request is refused with 403 so the
    dashboard cookie can never authorize a cross-site POST (§7.3). Keeps
    the pre-setup LAN-trust grace (a fresh install works before the admin
    password exists); afterwards a request with no usable credential is
    refused with 401."""
    result = await check_admin_request(request)
    if result in ("ok", "pre-setup"):
        return
    if result == "cookie-only":
        if request.method in ("GET", "HEAD"):
            return  # cookies may render GET state (§7.3)
        raise HTTPException(
            status_code=403,
            detail=(
                "mutations require Authorization: Bearer — the dashboard "
                "cookie only renders GET state"
            ),
        )
    raise HTTPException(status_code=401, detail="admin session required")


def core_url() -> str:
    """Base URL of the core voice service (:6370 by default)."""
    return os.environ.get("DOMOVOI_URL", "http://localhost:6370")


class CoreDown(RuntimeError):
    """The core service (:6370) is unreachable. Web routers map this to
    a 503 with a friendly "core is not running" body (§5.1)."""


class CoreClient:
    """Typed HTTP client a plugin web router uses to reach the core.

    ``path`` may be absolute (``"/v1/..."``) or plugin-relative
    (``"stations/refresh"`` → ``"/v1/plugins/<slug>/stations/refresh"``).
    ``post_admin(..., forward_auth=True)`` forwards the INCOMING
    request's admin Bearer/cookie to the core-side gate — the web
    process holds no ambient admin credential (§5.1).
    """

    def __init__(self, slug: str | None = None, *, timeout: float = 30.0) -> None:
        self._slug = slug
        self._timeout = timeout

    # ── internals ────────────────────────────────────────────────────
    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            if not self._slug:
                raise ValueError(
                    f"relative path {path!r} needs a plugin-scoped client"
                )
            path = f"/v1/plugins/{self._slug}/{path}"
        return f"{core_url().rstrip('/')}{path}"

    @staticmethod
    def _auth_headers(request: Any | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if request is None:
            return headers
        auth = request.headers.get("authorization")
        if auth:
            headers["Authorization"] = auth
        cookie = request.headers.get("cookie")
        if cookie:
            headers["Cookie"] = cookie
        client = getattr(request, "client", None)
        if client is not None and getattr(client, "host", None):
            headers["X-Forwarded-For"] = client.host
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = self._url(path)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.request(
                    method, url, params=params, json=json_body, headers=headers
                )
        except Exception as e:
            raise CoreDown(
                f"core service unreachable at {core_url()}: {e}"
            ) from e
        if r.status_code >= 400:
            # Surface the core's own error verbatim — FastAPI routers can
            # re-raise as HTTPException(status_code=r.status_code, ...).
            from fastapi import HTTPException

            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            raise HTTPException(status_code=r.status_code, detail=detail)
        try:
            return r.json()
        except Exception:
            return r.text

    # ── public surface (normative — design §5.1) ─────────────────────
    async def get(self, path: str, *, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, *, json: Any = None) -> Any:
        return await self._request("POST", path, json_body=json)

    async def post_admin(
        self,
        path: str,
        *,
        json: Any = None,
        forward_auth: bool = True,
        request: Any | None = None,
    ) -> Any:
        headers = self._auth_headers(request) if forward_auth else {}
        return await self._request("POST", path, json_body=json, headers=headers)


def paginate(limit: int, offset: int, *, max_limit: int = 200) -> tuple[int, int]:
    """Clamp a caller-supplied ``limit``/``offset`` pair to sane bounds."""
    return max(1, min(int(limit), max_limit)), max(0, int(offset))
