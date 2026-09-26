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
* :func:`device_endpoint` / :func:`open_endpoint` / :func:`admin_required`
  — the auth surface for plugin routers, shared with the core process
  (``domovoi.plugin_http`` re-exports these). Both processes mount plugin
  routers behind the same default-deny gate (:func:`enforce_route_tier`):
  every non-GET route requires an admin session unless its function is
  decorated ``@device_endpoint`` (the household device tier — any paired
  device) or ``@open_endpoint`` (no credential at all); a GET that wants
  gating adds ``Depends(admin_required)``.
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
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

import httpx
from fastapi import HTTPException, Request

from domovoi import net_safety, route_markers
from domovoi.admin_auth import (
    DEVICE_TOKEN_HEADER,
    check_admin_request,
    require_device,
    require_device_read,
)
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
    "device_endpoint",
    "is_device_endpoint",
    "open_endpoint",
    "is_open_endpoint",
    "EndpointTierConflict",
    "endpoint_tier",
    "tier_conflicts",
    "iter_plugin_routes",
    "ungated_routes",
    "websocket_routes",
    "WEBSOCKET_UNSUPPORTED",
    "unlisted_tier_routes",
    "enforce_route_tier",
    "admin_required",
]

log = logging.getLogger(__name__)

# Attributes the gates look for on a route function. One set of markers
# for both processes so a plugin author learns exactly one decorator per
# tier, and it means the same thing wherever the route is mounted.
_OPEN_MARKER = "_domovoi_open_endpoint"
_DEVICE_MARKER = "_domovoi_device_endpoint"

EndpointTier = Literal["open", "device", "admin"]


class EndpointTierConflict(TypeError):
    """A route function carries both ``@open_endpoint`` and
    ``@device_endpoint``. The two say different things about who may call
    it, so the combination is refused outright rather than resolved by a
    rule the author has to remember: at decoration time, again by the
    core's load-time contract check and the web mount, and at install by
    the trust-screen scan."""


def _conflict_message(fn: Callable[..., Any]) -> str:
    name = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", repr(fn))
    return (
        f"{name} carries both @open_endpoint and @device_endpoint — pick one: "
        "@device_endpoint for a household action any paired device may take, "
        "@open_endpoint only for a route that must answer with no credential "
        "at all"
    )


def open_endpoint(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Opt a plugin route OUT of every gate (design §4.11 core, §5.1 web):
    anyone who can reach the port may call it, with no credential at all.
    Rarely the right choice — a daily household action (play, favorite, a
    tune) belongs on :func:`device_endpoint`, which any paired phone or
    browser passes. Every opted-out route is listed on the install preview
    as part of the plugin's open surface. Stack it directly on the route
    function::

        @router.post("/tune")
        @open_endpoint
        async def tune(...): ...
    """
    if is_device_endpoint(fn):
        raise EndpointTierConflict(_conflict_message(fn))
    setattr(fn, _OPEN_MARKER, True)
    return fn


def is_open_endpoint(fn: Callable[..., Any]) -> bool:
    return bool(getattr(fn, _OPEN_MARKER, False))


def device_endpoint(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Put a plugin route on the DEVICE tier instead of the default admin
    gate: daily household actions any paired device may take — play a
    station, favorite it, edit your own list. The caller presents the
    household ``X-Device-Token`` (every paired browser, phone and
    satellite does) or an admin Bearer; nothing, a stale token, or the
    dashboard cookie alone is refused exactly as the core's own
    device-tier routes refuse it (``admin_auth.require_device``: 401 / 403
    cookie-only / 429 once a source keeps guessing), and the pre-setup LAN
    grace applies. On a GET it gates the read the way the core's media
    reads are gated (``require_device_read`` — the cookie and
    ``?device_token=`` also pass).

    Keep code-adjacent, destructive-beyond-recovery and long server-side
    jobs on the admin default. Every device-tier route is listed on the
    install preview beside the open ones. Stack it directly on the route
    function::

        @router.post("/play")
        @device_endpoint
        async def play(...): ...
    """
    if is_open_endpoint(fn):
        raise EndpointTierConflict(_conflict_message(fn))
    setattr(fn, _DEVICE_MARKER, True)
    return fn


def is_device_endpoint(fn: Callable[..., Any]) -> bool:
    return bool(getattr(fn, _DEVICE_MARKER, False))


def endpoint_tier(fn: Callable[..., Any] | None) -> EndpointTier:
    """Which gate a plugin route answers to: ``"device"``, ``"open"`` or
    the ``"admin"`` default. The one predicate both processes' gates, the
    load-time contract check and the route-tier tests consult. A function
    that somehow carries BOTH markers (set by hand, or copied across by a
    wrapper — the decorators themselves refuse to stack) resolves to
    ``"device"``: the stricter of the two, so a slipped check never opens
    a route wider than its author asked for."""
    if fn is None:
        return "admin"
    if is_device_endpoint(fn):
        return "device"
    if is_open_endpoint(fn):
        return "open"
    return "admin"


def iter_plugin_routes(routers: Iterable[Any]) -> Iterable[Any]:
    """Every route on ``routers``, with routers a plugin included INTO its
    own flattened. FastAPI >= 0.139 keeps an included router nested as one
    ``_IncludedRouter`` entry (its routes live on ``.original_router``)
    where older releases copied them in, so a walk of ``router.routes``
    alone would never see a nested route's markers — while the gate, which
    reads the matched route's endpoint, would still honour them."""
    seen: set[int] = set()

    def _walk(routes: Iterable[Any]) -> Iterable[Any]:
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is None:
                yield route
            elif id(inner) not in seen:
                seen.add(id(inner))
                yield from _walk(getattr(inner, "routes", None) or [])

    for router in routers:
        yield from _walk(getattr(router, "routes", None) or [])


def ungated_routes(routers: Iterable[Any]) -> list[str]:
    """Every HTTP route on ``routers`` that the per-slug gate would never
    run for. The gate is a router-level dependency, and FastAPI applies
    those to its own route classes only: a plain Starlette ``Route`` (from
    ``router.add_route``), a ``Mount`` or a ``Host`` is mounted with no
    dependency at all — no auth tier, and no 404 while the plugin is
    disabled. Both processes refuse a plugin with any. Websocket routes of
    either kind are :func:`websocket_routes`' to report."""
    from fastapi.routing import APIRoute
    from starlette.routing import WebSocketRoute

    out: list[str] = []
    for route in iter_plugin_routes(routers):
        if isinstance(route, (APIRoute, WebSocketRoute)):
            continue
        fn = getattr(route, "endpoint", None)
        label = _route_label(route, fn) if fn is not None else (
            f"{type(route).__name__} {getattr(route, 'path', '?')}"
        )
        out.append(f"{label} is a {type(route).__name__}")
    return out


# The one sentence both processes' load errors carry for a plugin
# websocket route, so a plugin author can search for it.
WEBSOCKET_UNSUPPORTED = "plugin websocket routes are not supported yet"


def websocket_routes(routers: Iterable[Any]) -> list[str]:
    """``"WEBSOCKET path (module.function)"`` for every websocket route on
    ``routers`` — ``@router.websocket`` / ``add_api_websocket_route``
    (FastAPI's ``APIWebSocketRoute``) or a plain Starlette
    ``WebSocketRoute``. Both processes refuse a plugin with any
    (:data:`WEBSOCKET_UNSUPPORTED`).

    The per-slug gate is written for HTTP: its dependency takes a
    ``Request``, so on a websocket FastAPI cannot call it and the
    handshake died with a ``TypeError`` at connect time — closed, but for
    no reason a plugin author could read — while a plain
    ``WebSocketRoute`` skipped the gate altogether. Until the gate has a
    websocket form (what tier a socket needs, and how a browser presents
    it), the load fails up front and says so."""
    from starlette.routing import WebSocketRoute

    out: list[str] = []
    for route in iter_plugin_routes(routers):
        if not isinstance(route, WebSocketRoute):   # APIWebSocketRoute too
            continue
        fn = getattr(route, "endpoint", None)
        where = f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', '?')}"
        out.append(f"WEBSOCKET {getattr(route, 'path', '?')} ({where})")
    return out


def tier_conflicts(routers: Iterable[Any]) -> list[str]:
    """``"METHOD path (module.function)"`` for every route on ``routers``
    whose function carries both markers — what the core's contract check
    and the web mount refuse to load."""
    out: list[str] = []
    for route in iter_plugin_routes(routers):
        fn = getattr(route, "endpoint", None)
        if fn is None or not (is_open_endpoint(fn) and is_device_endpoint(fn)):
            continue
        out.append(_route_label(route, fn))
    return out


def _route_label(route: Any, fn: Any) -> str:
    methods = ",".join(sorted(getattr(route, "methods", None) or [])) or "?"
    where = f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', '?')}"
    return f"{methods} {getattr(route, 'path', '?')} ({where})"


def unlisted_tier_routes(routers: Iterable[Any], package_dir: Path | str) -> list[str]:
    """Every route on ``routers`` that is off the admin tier at RUNTIME but
    that the install preview's source walk (:mod:`domovoi.route_markers`)
    of ``package_dir`` — the plugin's ``domovoi_plugin_<slug>`` package —
    does not list on that tier. The core's contract check and the web
    mount refuse to serve a plugin with any.

    The trust screen can only show what the walk reads off the source: a
    marker decorator on a ``def`` in the package. A marker put on any
    other way — ``setattr`` of the marker attribute, ``device_endpoint(fn)``
    called rather than stacked, a route function from outside the package
    — moves a route off the admin tier without the admin ever seeing it.
    Holding the runtime tiers to the walk closes that for both markers
    alike; a route the walk lists that is admin at runtime is harmless
    (the screen overstated it) and is not reported. Matching is by
    ``(module, function name, tier)``. This keeps an honest mistake — or a
    lazy evasion — from shipping an unlisted route; it is not a sandbox:
    plugin code is unsandboxed (docs/SECURITY_PRIVACY.md)."""
    off_admin: list[tuple[Any, Any, str]] = []
    for route in iter_plugin_routes(routers):
        fn = getattr(route, "endpoint", None)
        if fn is None:
            continue
        tier = endpoint_tier(fn)
        if tier != "admin":
            off_admin.append((route, fn, tier))
    if not off_admin:
        return []
    try:
        scanned = route_markers.scan_marked_endpoints(Path(package_dir))
    except (OSError, SyntaxError, RecursionError, ValueError) as e:
        # The package was readable enough to import; if its source cannot
        # be walked now, nothing off the admin tier can be vouched for.
        log.warning("route-marker walk of %s failed: %s", package_dir, e)
        scanned = {}
    listed = {
        (rec.get("module"), rec.get("function"), tier)
        for tier in ("open", "device")
        for rec in scanned.get(tier) or []
    }
    return [
        f"{_route_label(route, fn)} is {tier} tier"
        for route, fn, tier in off_admin
        if (getattr(fn, "__module__", None), getattr(fn, "__name__", None), tier)
        not in listed
    ]


async def enforce_route_tier(request: Request) -> None:
    """The auth half of the per-slug gate BOTH processes mount plugin
    routers behind (``domovoi.plugin_http`` on the core,
    ``web.backend.plugin_host`` on the dashboard) — one body so the two
    cannot drift.

    * ``OPTIONS`` always passes (a CORS preflight carries no credential).
    * ``GET`` / ``HEAD`` pass unless the route is ``@device_endpoint``,
      which gates them as a device-tier read; a GET that wants the admin
      tier adds ``Depends(admin_required)`` itself.
    * every other method: ``@open_endpoint`` passes, ``@device_endpoint``
      takes the household token or an admin Bearer, and everything else
      requires an admin session (:func:`admin_required`).
    """
    method = request.method
    if method == "OPTIONS":
        return
    tier = endpoint_tier(request.scope.get("endpoint"))
    if method in ("GET", "HEAD"):
        if tier == "device":
            await require_device_read(request)
        return
    if tier == "open":
        return
    if tier == "device":
        await require_device(request)
        return
    await admin_required(request)


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
    request's credentials to the core-side gate — the admin Bearer, the
    cookie and the household ``X-Device-Token`` alike, so a core route on
    either tier sees exactly what the caller presented. The web process
    holds no ambient credential of its own (§5.1).
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
        # The household token rides the hop too (same as the core pages'
        # web.backend.domovoi_client.auth_forward_headers): a
        # @device_endpoint route on the core must see the token a phone
        # presented to the web process, or a paired device that passed the
        # web gate is refused one hop later.
        device_token = request.headers.get(DEVICE_TOKEN_HEADER.lower())
        if device_token:
            headers[DEVICE_TOKEN_HEADER] = device_token
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
