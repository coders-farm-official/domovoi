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
  route that is not a ``@device_endpoint`` FastAPI websocket route
  (``webkit.refused_websocket_routes``).
* **Websockets (SDK 1.11)** — a plugin websocket route is DEVICE TIER
  only, and the same per-slug gate runs on its upgrade (it takes an
  ``HTTPConnection``, so FastAPI calls it for both scopes). Everything it
  checks is checked before the socket is accepted, so a refused upgrade
  never reaches the plugin's function: the plugin is enabled and this is
  its live mount; ``Host`` names this box on the LAN and a browser's
  ``Origin`` is a LAN page (``transport_guard``, what the HTTP routes and
  the core's own sockets enforce); the household token (``X-Device-Token``
  or ``?token=``) or an admin Bearer, with the pre-setup LAN grace and the
  wrong-token backoff (``admin_auth.check_device_websocket``: the HTTP
  device tier's rule, the dashboard cookie alone refused); and room under
  the per-plugin and per-source socket caps. While it is open the socket
  is held to the same rule: the credential that opened it is read again
  every :data:`WS_RECHECK_SEC` (a rotated token, an admin session that
  idled out or signed out, or the end of the pre-setup grace closes it;
  the socket itself never counts as admin activity), every message is
  held to the request-body cap and :data:`WS_MESSAGES_PER_SEC`, and
  disabling or re-mounting the slug closes it (``1001``).

The v1 auth model (scope amendment) is the lightweight one: the admin
gate checks a Bearer token against ``admin_sessions`` (sha256-stored,
signs out after an hour without an admin action — validation shared
with the web process via
:mod:`domovoi.admin_auth`). **Until the first-run setup has created the
``admin_auth`` row, the gate allows everything** — the open LAN-trust
posture — so a fresh clone works before the setup flow runs.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocketException
from sqlalchemy import text
from starlette.requests import HTTPConnection

from domovoi import admin_auth
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
    refused_websocket_routes,
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
    if not enabled:
        close_plugin_sockets(slug, reason=f"plugin {slug!r} disabled")


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


async def _admin_ok(request: HTTPConnection) -> bool:
    """True when the request may perform admin-tier actions. Allows all
    while no admin password has been set up (pre-setup LAN-trust —
    module docstring); afterwards requires a live Bearer session
    (sha256 lookup + the idle hour via :mod:`domovoi.admin_auth`)."""
    return await check_admin_request(request) in ("ok", "pre-setup")


def _make_gate(slug: str, mount: list[Any] | None = None):
    """The per-slug gate dependency. ``mount`` is the ``_mounted`` entry
    of the mount that includes the route: once the slug is mounted again
    (a later enable, on this app or another) the route 404s like a
    disabled one, whatever the flag says."""
    def _gone() -> bool:
        return not _plugin_enabled.get(slug, False) or (
            mount is not None and _mounted.get(slug) is not mount
        )

    def _refuse_if_gone() -> None:
        if _gone():
            raise HTTPException(status_code=404, detail=f"plugin {slug!r} disabled")

    # A generator, so that on a websocket the code after ``yield`` runs
    # when the plugin's function returns: that is where the socket leaves
    # the slug's ledger. On HTTP nothing follows the yield.
    async def _gate(conn: HTTPConnection):
        if conn.scope.get("type") == "websocket":
            sock = await _admit_websocket(slug, conn, _gone)
            try:
                yield
            finally:
                _release_socket(sock)
            return
        _refuse_if_gone()
        # Default-DENY for mutations: the admin tier unless the route
        # function carries a tier marker (shared body with the web gate).
        await enforce_route_tier(conn)  # type: ignore[arg-type] — an http scope is a Request
        # The tier check can await the DB. A disable (or disable and
        # re-enable) that ran meanwhile tore this route's SDK down, so
        # look again before the endpoint runs against it.
        _refuse_if_gone()
        yield
    return _gate


# ─── Plugin websockets (SDK 1.11) ─────────────────────────────────────────

#: Sockets one plugin may hold open at once, and sockets one source (an
#: address, ``admin_auth.request_source``) may hold open to one plugin.
#: The core serves at most ``MAX_CONCURRENT_CONNECTIONS`` (128) connections
#: in all, the rooms' own sockets among them, so no plugin may take more
#: than a quarter, and no single device most of a plugin's share.
WS_MAX_PER_PLUGIN = 32
WS_MAX_PER_SOURCE = 8
#: How often an open socket's credential is read again
#: (``admin_auth.device_websocket_still_admitted``): the longest a socket
#: outlives a rotated token, a signed-out or idled-out admin session, or
#: the end of the pre-setup grace.
WS_RECHECK_SEC = 30.0
#: Consecutive rechecks the database may fail to answer before the socket
#: is closed (1011): a blip is not a revoked credential, a long outage is
#: a credential nobody can vouch for any more.
WS_RECHECK_MAX_FAILURES = 3
#: The message rate one socket may keep up (a token bucket: the burst,
#: refilled at the rate). Over it the socket is closed 1008. Each message
#: is also held to the request-body cap (:func:`ws_max_message_bytes`).
WS_MESSAGES_PER_SEC = 50.0
WS_MESSAGE_BURST = 100

# Close codes (RFC 6455 §7.4.1).
WS_POLICY = 1008        # refused / credential gone / too many messages
WS_TOO_BIG = 1009
WS_GOING_AWAY = 1001    # the plugin was disabled or mounted again
WS_INTERNAL = 1011      # the credential could not be checked for too long
WS_TRY_AGAIN = 1013     # full, or the credential check could not run


def ws_max_message_bytes() -> int:
    """The largest message a plugin socket may carry: what one request
    body may (``MAX_REQUEST_BYTES``, 1 MiB by default). Over it the socket
    is closed 1009. uvicorn's own ``ws_max_size`` (16 MiB) still bounds
    what is buffered before this sees it."""
    from domovoi.transport_guard import default_body_limit

    return default_body_limit()


class _PluginSocket:
    """One open plugin websocket the gate admitted: what it needs to be
    rechecked, limited, counted and closed."""

    __slots__ = (
        "ws", "slug", "source", "admitted", "loop", "watcher",
        "tokens", "stamp", "closing",
    )

    def __init__(self, ws: Any, slug: str, source: str, admitted: str) -> None:
        self.ws = ws
        self.slug = slug
        self.source = source
        self.admitted = admitted
        self.loop = asyncio.get_running_loop()
        self.watcher: asyncio.Task | None = None
        self.tokens = float(WS_MESSAGE_BURST)
        self.stamp = time.monotonic()
        self.closing = False

    def take(self) -> bool:
        """One message off the token bucket; False when the peer is
        sending faster than :data:`WS_MESSAGES_PER_SEC` beyond the burst."""
        now = time.monotonic()
        self.tokens = min(
            float(WS_MESSAGE_BURST), self.tokens + (now - self.stamp) * WS_MESSAGES_PER_SEC
        )
        self.stamp = now
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True


# slug → the sockets its gate admitted that are still open. An entry leaves
# when the plugin's function returns (the gate's ``finally``).
_open_sockets: dict[str, set[_PluginSocket]] = {}
# Close tasks in flight: the event loop keeps only weak references to tasks.
_closing_tasks: set[asyncio.Future] = set()


def open_socket_count(slug: str | None = None) -> int:
    """How many plugin sockets are open (for ``slug``, or in all)."""
    if slug is not None:
        return len(_open_sockets.get(slug) or ())
    return sum(len(v) for v in _open_sockets.values())


async def _shut(sock: _PluginSocket, code: int, reason: str) -> None:
    """Close ``sock`` once. Never raises: the peer may be gone already."""
    if sock.closing:
        return
    sock.closing = True
    try:
        await sock.ws.close(code=code, reason=reason)
    except Exception:  # noqa: BLE001 — already closed / peer gone
        pass


def close_plugin_sockets(slug: str, *, reason: str, code: int = WS_GOING_AWAY) -> int:
    """Close every socket ``slug``'s routes hold open (``1001`` by
    default). The gate judges an upgrade, so without this a socket opened
    before a disable — or before the slug was mounted again — would keep
    talking to a load that has been torn down. Safe from any thread: each
    close runs on its socket's own event loop. Returns how many."""
    socks = list(_open_sockets.get(slug) or ())
    for sock in socks:
        def _schedule(sock: _PluginSocket = sock) -> None:
            task = sock.loop.create_task(_shut(sock, code, reason))
            _closing_tasks.add(task)
            task.add_done_callback(_closing_tasks.discard)

        try:
            sock.loop.call_soon_threadsafe(_schedule)
        except RuntimeError:  # its loop is closed: nothing is served there
            pass
    return len(socks)


def _refuse_ws(code: int, reason: str) -> WebSocketException:
    # The reason travels to the client: it never quotes a credential.
    return WebSocketException(code=code, reason=reason)


async def _admit_websocket(slug: str, ws: Any, gone: Any) -> _PluginSocket:
    """The websocket form of the per-slug gate. Raises
    :class:`WebSocketException` — the upgrade is refused before it is
    accepted, so the plugin's function never runs (uvicorn answers the
    handshake ``403``; a test client reads the close code) — or returns
    the socket, entered in the slug's ledger, with its limits and its
    recheck running."""
    from domovoi.transport_guard import host_allowed, origin_allowed

    if gone():
        raise _refuse_ws(WS_POLICY, f"plugin {slug!r} disabled")
    # Only @device_endpoint sockets are mounted (refused_websocket_routes);
    # look again, so a route that slipped past is still not served.
    if endpoint_tier(ws.scope.get("endpoint")) != "device":
        raise _refuse_ws(WS_POLICY, "not a device-tier socket")
    # LanHostMiddleware lets websocket scopes through; the HTTP routes'
    # Host rule (DNS rebinding, CORE-8) applies to a plugin socket too.
    if not host_allowed(ws.headers.get("host")):
        raise _refuse_ws(WS_POLICY, "Host is not a name this server answers to")
    # A browser page may open a socket to any host (the same-origin policy
    # does not cover upgrades): a page off the LAN is turned away the way
    # the core's own sockets turn it away. No Origin = not a browser.
    if not origin_allowed(ws.headers.get("origin")):
        raise _refuse_ws(WS_POLICY, "cross-origin socket refused")
    try:
        admitted = await admin_auth.check_device_websocket(ws)
    except admin_auth.AuthCheckUnavailable:
        raise _refuse_ws(
            WS_TRY_AGAIN, "the server could not check credentials just now"
        ) from None
    if admitted == "throttled":
        raise _refuse_ws(
            WS_POLICY, "too many wrong household tokens from here; wait and try again"
        )
    if admitted not in ("ok", "admin", "pre-setup"):
        raise _refuse_ws(WS_POLICY, "household device token or admin session required")
    # The check awaited the database: a disable or remount meanwhile tore
    # this route's SDK down.
    if gone():
        raise _refuse_ws(WS_POLICY, f"plugin {slug!r} disabled")
    source = admin_auth.request_source(ws)
    held = _open_sockets.get(slug) or set()
    if len(held) >= WS_MAX_PER_PLUGIN:
        raise _refuse_ws(WS_TRY_AGAIN, "this plugin has too many open sockets")
    if sum(1 for s in held if s.source == source) >= WS_MAX_PER_SOURCE:
        raise _refuse_ws(WS_TRY_AGAIN, "too many open sockets from this device")
    sock = _PluginSocket(ws, slug, source, admitted)
    _limit_messages(sock)
    _open_sockets.setdefault(slug, set()).add(sock)
    sock.watcher = sock.loop.create_task(_watch(sock, gone))
    return sock


def _release_socket(sock: _PluginSocket) -> None:
    held = _open_sockets.get(sock.slug)
    if held is not None:
        held.discard(sock)
        if not held:
            _open_sockets.pop(sock.slug, None)
    if sock.watcher is not None and not sock.watcher.done():
        sock.watcher.cancel()


def _limit_messages(sock: _PluginSocket) -> None:
    """Hold every message the plugin reads to the size cap and the rate.
    Wraps the connection's own ``receive``, so it applies whichever of
    ``receive_text`` / ``_bytes`` / ``_json`` / ``iter_*`` the plugin
    uses. An offending message is never handed over: the socket is closed
    and the plugin reads a disconnect carrying the close code."""
    ws = sock.ws
    inner = ws._receive

    async def receive() -> dict:
        message = await inner()
        if message.get("type") != "websocket.receive" or sock.closing:
            return message
        text_ = message.get("text")
        size = len(message.get("bytes") or b"") + (
            len(text_.encode("utf-8")) if text_ else 0
        )
        if size > ws_max_message_bytes():
            await _shut(sock, WS_TOO_BIG, "message too big")
            return {"type": "websocket.disconnect", "code": WS_TOO_BIG}
        if not sock.take():
            await _shut(sock, WS_POLICY, "too many messages")
            return {"type": "websocket.disconnect", "code": WS_POLICY}
        return message

    ws._receive = receive


async def _watch(sock: _PluginSocket, gone: Any) -> None:
    """Re-read the credential that opened ``sock`` every
    :data:`WS_RECHECK_SEC` while it is open, and close it once that
    credential would no longer open it (module docstring)."""
    failures = 0
    while not sock.closing:
        await asyncio.sleep(WS_RECHECK_SEC)
        if sock.closing:
            return
        if gone():
            await _shut(sock, WS_GOING_AWAY, f"plugin {sock.slug!r} disabled")
            return
        try:
            still = await admin_auth.device_websocket_still_admitted(sock.ws, sock.admitted)
        except Exception as e:  # noqa: BLE001 — a DB blip: judged below
            failures += 1
            log.warning(
                "plugin %s socket: credential recheck failed (%d/%d): %s",
                sock.slug, failures, WS_RECHECK_MAX_FAILURES, type(e).__name__,
            )
            if failures >= WS_RECHECK_MAX_FAILURES:
                await _shut(sock, WS_INTERNAL, "credentials could not be checked")
                return
            continue
        failures = 0
        if not still:
            log.info("plugin %s socket closed: its credential no longer opens it", sock.slug)
            await _shut(sock, WS_POLICY, "household device token or admin session required")
            return


def _unmount(app: FastAPI, slug: str) -> None:
    """Take the slug's mount down: drop every route-table entry a mount of
    ``slug`` added to ``app``, and forget the live mount (so its routes
    404 on any other app they are still in)."""
    app.router.routes[:] = [
        r for r in app.router.routes if getattr(r, _SLUG_ATTR, None) != slug
    ]
    _mounted.pop(slug, None)
    close_plugin_sockets(slug, reason=f"plugin {slug!r} unmounted")
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
    sockets = refused_websocket_routes(routers)
    if sockets:
        # Only a @device_endpoint FastAPI websocket route takes the gate's
        # websocket form; say why at mount instead of serving it.
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
        # It also folds each router's lifespan into the app's, and appends
        # its startup / shutdown handlers to the app's. Plugins mount from
        # inside the app's lifespan (or later), where none of that can
        # run — and on every re-enable it would chain one more wrapper, and
        # add one more handler, holding that load's routers and SDK alive.
        lifespan = app.router.lifespan_context
        startup = list(app.router.on_startup)
        shutdown = list(app.router.on_shutdown)
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
            app.router.on_startup[:] = startup
            app.router.on_shutdown[:] = shutdown
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
        # Sockets the previous mount let through talk to its torn-down SDK
        # (their gate would refuse them now): close them rather than wait.
        close_plugin_sockets(slug, reason=f"plugin {slug!r} reloaded")
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
