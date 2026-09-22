"""FastAPI entrypoint for the web management UI.

Single process, separate from the Domovoi server (port 6369 by
default). Reuses the Domovoi server's Postgres connection via
``web.backend.db.session_scope``.

Registers the route modules and the WebSocket, and runs the state
poll loop in lifespan.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from domovoi import admin_auth
from web.backend.api import acquisitions as acquisitions_api
from web.backend.api import auth as auth_api
from web.backend.api import calendar as calendar_api
from web.backend.api import capabilities as capabilities_api
from web.backend.api import chat as chat_api
from web.backend.api import config as config_api
from web.backend.api import denylist as denylist_api
from web.backend.api import devices as devices_api
from web.backend.api import audiobooks as audiobooks_api
from web.backend.api import documents as documents_api
from web.backend.api import files as files_api
from web.backend.api import greetings as greetings_api
from web.backend.api import images as images_api
from web.backend.api import models as models_api
from web.backend.api import music as music_api
from web.backend.api import music_queue as music_queue_api
from web.backend.api import news as news_api
from web.backend.api import playlists as playlists_api
from web.backend.api import people as people_api
from web.backend.api import podcasts as podcasts_api
# Radio is a PLUGIN feature: its web router mounts dynamically at
# /api/plugins/radio via the plugin host — no static import.
from web.backend.api import plugins as plugins_api
from web.backend.api import satellite_media as satellite_media_api
from web.backend.api import satellites as satellites_api
from web.backend.api import videos as videos_api
from web.backend.api import voices as voices_api
from web.backend.api import wake_words as wake_words_api
from web.backend import plugin_host
from web.backend import realtime as realtime_mod
from web.backend.middleware import (
    BodyLimitMiddleware,
    RequireRequestedWithMiddleware,
    SecurityHeadersMiddleware,
    cors_origin_regex,
)
from web.backend.realtime import (
    DEFAULT_POLL_INTERVAL_SEC,
    ListenTask,
    StateBroadcaster,
    StatePollLoop,
)
from web.backend.schemas import HealthResponse

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)


class _SnapshotPollOnlyAtDebug(logging.Filter):
    """Drop ``httpx`` INFO lines for the Domovoi server's snapshot
    endpoint unless the operator explicitly raised the verbosity to
    DEBUG. The realtime poll loop calls ``/v1/admin/snapshot`` every
    1.5 s; logging each one is ~40 noise lines per minute that drown
    real events. Other ``httpx`` INFO lines (a one-off ``announce-all``
    request, a 4xx anywhere) pass through unchanged.

    Implementation choice: keep the ``httpx`` logger at INFO so its
    other messages still flow. Filter checks the root logger's
    effective level — when the operator runs with ``LOG_LEVEL=DEBUG``
    to chase a problem, the snapshot polls come back into the log so
    timing / ordering can be inspected.
    """

    _SUPPRESSED_PATTERNS = ("/v1/admin/snapshot",)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if not any(p in msg for p in self._SUPPRESSED_PATTERNS):
            return True
        # It IS the snapshot poll line. Show it only when the root
        # logger is at DEBUG or below — otherwise drop.
        return logging.getLogger().getEffectiveLevel() <= logging.DEBUG


logging.getLogger("httpx").addFilter(_SnapshotPollOnlyAtDebug())


class _RoutinePollAccessOnlyAtDebug(logging.Filter):
    """Drop uvicorn access-log lines for the routine GETs the
    dashboard fires on a schedule, unless ``LOG_LEVEL=DEBUG``. Same
    level-aware idea as :py:class:`_SnapshotPollOnlyAtDebug`: bumping
    the operator's log level brings them back for timing inspection,
    but at the default ``INFO`` they're noise.

    The list is the set of endpoints the dashboard hits in a polling
    rhythm: ``/api/satellites`` (subscribed to wifi/presence events),
    the five sidebar-count GETs (``useSidebarCounts``), and the
    health probe. User-driven CRUD and one-shot fetches (search,
    drawer opens, detection feeds) keep logging normally so the log
    still shows interesting activity.
    """

    _SUPPRESSED_FRAGMENTS = (
        '"GET /api/satellites ',
        '"GET /api/people ',
        '"GET /api/calendar/events ',
        '"GET /api/music/library?limit=1 ',
        '"GET /api/plugins/manifest ',
        '"GET /api/health ',
    )

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if not any(frag in msg for frag in self._SUPPRESSED_FRAGMENTS):
            return True
        return logging.getLogger().getEffectiveLevel() <= logging.DEBUG


logging.getLogger("uvicorn.access").addFilter(_RoutinePollAccessOnlyAtDebug())


# Where Claude Design's exported bundle lands.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Default LAN-friendly bind. Override via WEB_HOST + WEB_PORT.
_HOST = os.environ.get("WEB_HOST", "0.0.0.0")
_PORT = int(os.environ.get("WEB_PORT", "6369"))
_POLL_INTERVAL = float(
    os.environ.get("WEB_POLL_INTERVAL_SEC", str(DEFAULT_POLL_INTERVAL_SEC))
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the state poll loop + LISTEN task alongside the FastAPI
    app, stop them on shutdown. The broadcaster is process-scoped so
    all workers (if multi-worker uvicorn) share it within a worker;
    cross-worker fanout would need Redis pub/sub but isn't required
    at homelab scale.

    The LISTEN task is the sub-second accelerator on top of the poll
    loop — see ``realtime.py``'s module docstring for the full
    contract. Start order: poll loop first (so emit_for_channel is
    operational), then listen task; reverse on shutdown.
    """
    # Household device token (device tier): the web boots the same hook as
    # the core so a web-only throwaway has a token row and
    # ~/.domovoi/device-token.txt (0600) too. Whichever process boots first
    # mints; the other reads the row back. Never fatal.
    try:
        await admin_auth.ensure_device_token()
    except Exception as e:
        log.warning("device-token boot hook raised: %s", e)

    broadcaster = StateBroadcaster()
    poll_loop = StatePollLoop(broadcaster, interval_sec=_POLL_INTERVAL)
    listen_task = ListenTask(poll_loop)

    app.state.broadcaster = broadcaster
    app.state.poll_loop = poll_loop
    app.state.listen_task = listen_task

    # ── Plugin host boot (design §5.1) ───────────────────────────────
    # 1. Install the web-process import guard: after this point, no NEW
    #    domovoi.* module can load here except domovoi.webkit — plugin
    #    web code cannot drag core runtime into this process.
    # 2. Read the plugins registry, mount enabled plugins' web routers +
    #    realtime wiring, and wire the resync callbacks so a
    #    plugins_changed NOTIFY re-mounts live and refreshes the LISTEN
    #    channel set (§3.2 step 15).
    plugin_host.install_import_guard()
    plugin_host.HOST.broadcast = broadcaster.broadcast

    async def _refresh_listen_channels() -> None:
        listen_task.refresh_channels()

    plugin_host.HOST.on_channels_changed = _refresh_listen_channels
    realtime_mod.ON_PLUGINS_CHANGED = lambda: plugin_host.HOST.resync(app)
    try:
        await plugin_host.HOST.resync(app)
    except Exception as e:
        log.warning("plugin web host boot resync failed: %s", e)

    await poll_loop.start()
    await listen_task.start()

    # NOTE (stage C6): the Office-Suite stale-lock sweeper
    # (domovoi/workers/document_lock_sweeper.py) moved onto the core
    # process's declarative worker registry (design §4.5) with the rest
    # of the background workers — this lifespan no longer hand-wires it.
    # document_sessions rows are still written only by THIS process
    # (web/backend/api/documents.py); the core just sweeps the stale ones.

    log.info("web backend started: bind=%s:%d", _HOST, _PORT)
    try:
        yield
    finally:
        await listen_task.stop()
        await poll_loop.stop()
        # Uninstall the import guard on shutdown. Irrelevant for a real
        # web process (it exits), essential for the test suite, where
        # web and core share one Python process and a persisting guard
        # would poison later core-side lazy imports.
        plugin_host.remove_import_guard()
        log.info("web backend stopped")


app = FastAPI(
    title="Domovoi Web",
    description="Management UI for the local-first voice domovoi.",
    lifespan=lifespan,
)


# ─── Write backstop (WEB-6) ───────────────────────────────────────────────
# A write under /api/ without X-Requested-With is refused here, in front of
# the router, so no endpoint runs and nothing reads the body. Registered
# FIRST and therefore INNERMOST (add_middleware inserts at the front of
# user_middleware and the front is the outer wrapper), so CORS still
# answers its own preflight and an oversized body is still refused 413 by
# the limiter below whether or not it brought the header.
app.add_middleware(RequireRequestedWithMiddleware)


# ─── Request-body budgets (WEB-4) ─────────────────────────────────────────
# Outside CORS so an oversized upload is refused before anything reads
# it. Only the routes named in middleware.BODY_LIMITS have a budget.
app.add_middleware(BodyLimitMiddleware)


# ─── CORS (WEB-8) ─────────────────────────────────────────────────────────
# LAN-trust, narrowed to THIS PORT: a browser opening the dashboard by IP
# works, and so does the server switcher pointing one dashboard at another
# Domovoi — both are this port on a LAN host. Every other service on the
# same machine is a different origin but the same site, and the old
# any-port regex let a page served by one of them read this API with the
# dashboard's cookie attached.

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=cors_origin_regex(_PORT),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Response headers (WEB-8) ─────────────────────────────────────────────
# Registered last, so it wraps everything above and stamps CSP,
# X-Frame-Options, nosniff and Referrer-Policy onto every response this
# process makes — including a CORS preflight and the refusals from the two
# middlewares above.
app.add_middleware(SecurityHeadersMiddleware)


# ─── API routes ───────────────────────────────────────────────────────────

app.include_router(auth_api.router)
# plugin_host.router first: its literal /api/plugins/manifest (open) and
# /plugins/<slug>/static/* asset routes must win before the /{slug}
# matchers in plugins_api.
app.include_router(plugin_host.router)
app.include_router(plugins_api.router)
app.include_router(capabilities_api.router)
app.include_router(acquisitions_api.router)
app.include_router(music_api.router)
# Room-queue editing + the device blocklist. Its own module so the
# /api/music/queue/{room_id} matcher stays clear of music_api's literal
# paths, and so the blocks sit on a prefix no room id can shadow.
app.include_router(music_queue_api.router)
app.include_router(music_queue_api.blocks_router)
app.include_router(devices_api.router)
app.include_router(people_api.router)
app.include_router(denylist_api.router)
# Media-prep BEFORE satellites: its /api/satellites/media/* paths must
# never be captured by the satellites router's /{room_id} parameters.
app.include_router(satellite_media_api.router)
app.include_router(satellites_api.router)
app.include_router(calendar_api.router)
app.include_router(config_api.router)
app.include_router(playlists_api.router)
app.include_router(greetings_api.router)
app.include_router(voices_api.router)
app.include_router(wake_words_api.router)
app.include_router(documents_api.router)
app.include_router(files_api.router)
app.include_router(files_api.blocks_router)
app.include_router(podcasts_api.router)
app.include_router(audiobooks_api.router)
app.include_router(videos_api.router)
app.include_router(images_api.router)
app.include_router(chat_api.router)
app.include_router(models_api.router)
app.include_router(news_api.router)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Cheap readiness probe — DB ping + domovoi ping. Returns
    'degraded' (HTTP 200, not 503) when the Domovoi server is down so
    the UI can show a partial-degradation banner instead of refusing
    to render."""
    from sqlalchemy import text

    db_ok = True
    try:
        from web.backend.db import session_scope
        async with session_scope() as s:
            await s.execute(text("SELECT 1"))
    except Exception as e:
        log.warning("health: DB ping failed: %s", e)
        db_ok = False

    # Domovoi ping: best-effort against /v1/health on the
    # configured domovoi URL. Doesn't fail the response.
    core_ok = False
    try:
        import httpx
        domovoi_url = os.environ.get("DOMOVOI_URL", "http://localhost:6370")
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{domovoi_url}/v1/health")
            core_ok = r.status_code == 200
    except Exception:
        core_ok = False

    return HealthResponse(
        status="ok" if db_ok and core_ok else "degraded",
        db_reachable=db_ok,
        domovoi_reachable=core_ok,
    )


# ─── WebSocket ────────────────────────────────────────────────────────────


# How a BROWSER presents the household device token on this socket: it
# cannot set a request header, but it can offer a subprotocol, and that
# travels in the handshake rather than in the URL (a query string would
# land in every access log). The dashboard offers
# "domovoi.device-token.<token>" when it holds one (web/static/data.js).
WS_DEVICE_TOKEN_SUBPROTOCOL = "domovoi.device-token."

_REFUSE = object()


def _offered_token_subprotocol(ws: WebSocket) -> tuple[str | None, str | None]:
    """``(token, subprotocol)`` from ``Sec-WebSocket-Protocol``, or
    ``(None, None)``. The subprotocol has to be echoed back on accept or the
    browser drops the connection, so it is returned even when the token in
    it turns out to be useless."""
    raw = ws.headers.get("sec-websocket-protocol") or ""
    for offered in (part.strip() for part in raw.split(",")):
        if offered.startswith(WS_DEVICE_TOKEN_SUBPROTOCOL):
            token = offered[len(WS_DEVICE_TOKEN_SUBPROTOCOL):].strip()
            return (token or None), offered
    return None, None


async def _authorize_state_socket(ws: WebSocket) -> object | None:
    """Decide whether this socket may subscribe, and with which
    subprotocol echoed back. Returns ``_REFUSE`` to turn the handshake
    away.

    What counts, in the order a caller is likely to have it:

    * ``X-Device-Token`` — the Android app and any other header-setting
      client (``check_device_request``);
    * an admin ``Bearer``, or the dashboard's session cookie: this socket
      only renders state, which is the read tier's bar (the cookie is
      ``SameSite=Strict``, so another site's page cannot bring it here);
    * the ``domovoi.device-token.<token>`` subprotocol, for a browser that
      is paired but not signed in — a kiosk display, mostly;
    * the pre-setup grace, so a fresh install's dashboard is live before
      anyone has claimed the admin password.

    Anything else is refused before the socket is accepted, which means an
    unauthenticated subscriber is never registered with the broadcaster and
    is pushed nothing at all.
    """
    _, offered = _offered_token_subprotocol(ws)
    result = await admin_auth.check_device_request(ws)
    if result in ("ok", "admin", "pre-setup", "cookie-only"):
        return offered
    token, _ = _offered_token_subprotocol(ws)
    if token:
        try:
            async with admin_auth.session_scope() as s:
                if await admin_auth.validate_device_token(s, token):
                    return offered
        except Exception as e:  # pragma: no cover — DB down ⇒ fail closed
            log.warning("ws device-token check failed: %s", e)
    return _REFUSE


@app.websocket("/ws/state")
async def websocket_state(ws: WebSocket) -> None:
    """Client subscribes to channels; server pushes state-change
    events as they're emitted by the poll loop. Subscription frame
    format::

        {"subscribe": ["music", "satellites", "downloads", ...],
         "device_token": "<the household device token>"}

    Empty list / no frame = subscribed to all channels.

    **The handshake needs a household credential** (WEB-9): this stream
    carries who is home, what the calendar says and which devices are on
    the network, so it is not for any socket that can reach the port. See
    :func:`_authorize_state_socket` for what counts; without one the
    handshake is refused (the socket is closed before it is accepted, which
    the server answers as HTTP 403) and nothing is ever pushed to it.

    ``device_token`` in the first frame is accepted and ignored: the
    credential is settled during the handshake, and an older client that
    sends the field is unaffected.
    """
    subprotocol = await _authorize_state_socket(ws)
    if subprotocol is _REFUSE:
        await ws.close(code=1008, reason="household device token or admin session required")
        return
    await ws.accept(subprotocol=subprotocol)
    broadcaster: StateBroadcaster = ws.app.state.broadcaster
    await broadcaster.connect(ws)
    try:
        while True:
            # Keep the socket alive + accept subscription updates.
            try:
                msg = await ws.receive_json()
            except (WebSocketDisconnect, ValueError):
                break
            channels = msg.get("subscribe") if isinstance(msg, dict) else None
            if isinstance(channels, list):
                await broadcaster.set_subscriptions(
                    ws, [str(c) for c in channels if isinstance(c, str)]
                )
    finally:
        await broadcaster.disconnect(ws)


# ─── Static frontend ──────────────────────────────────────────────────────
# Mounted last so /api and /ws routes win when paths overlap. The
# directory may be empty during early development; FastAPI handles
# that fine and just returns 404 for any path under /.

if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
else:
    log.warning("static dir %s does not exist; frontend not served", _STATIC_DIR)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "web.backend.main:app",
        host=_HOST,
        port=_PORT,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
        # Multi-worker is OK here (no GPU pinning); start at 1 and
        # bump only if the dashboard's request count justifies it.
        workers=1,
    )


if __name__ == "__main__":
    main()
