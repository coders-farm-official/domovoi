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

Everything else in ``domovoi.*`` is refused at runtime in the web
process by a ``sys.meta_path`` guard (``web.backend.plugin_host``), so
"imports only webkit" is an enforced invariant, not a convention. This
module must therefore keep its own import footprint minimal: db.session
plus stdlib/httpx only.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from domovoi.db.session import SessionLocal, engine, session_scope

__all__ = [
    "SessionLocal",
    "engine",
    "session_scope",
    "CoreClient",
    "CoreDown",
    "core_url",
    "paginate",
]

log = logging.getLogger(__name__)


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
