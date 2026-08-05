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

from web.backend.api import acquisitions as acquisitions_api
from web.backend.api import auth as auth_api
from web.backend.api import calendar as calendar_api
from web.backend.api import capabilities as capabilities_api
from web.backend.api import chat as chat_api
from web.backend.api import config as config_api
from web.backend.api import denylist as denylist_api
from web.backend.api import audiobooks as audiobooks_api
from web.backend.api import documents as documents_api
from web.backend.api import files as files_api
from web.backend.api import greetings as greetings_api
from web.backend.api import images as images_api
from web.backend.api import models as models_api
from web.backend.api import music as music_api
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


# ─── CORS ─────────────────────────────────────────────────────────────────
# LAN-trust: allow localhost + RFC 1918 ranges for cross-origin requests
# from the browser when the user opens the UI by IP. Refuses public
# origins as a basic defense against malicious websites trying to hit
# the user's LAN device when they happen to have a tab open elsewhere.
# This is belt-and-suspenders next to binding only to LAN interfaces.

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"^https?://("
        r"localhost(:\d+)?"
        r"|127\.0\.0\.1(:\d+)?"
        r"|192\.168\.\d+\.\d+(:\d+)?"
        r"|10\.\d+\.\d+\.\d+(:\d+)?"
        r"|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+(:\d+)?"
        r"|[\w-]+\.local(:\d+)?"
        r")$"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.websocket("/ws/state")
async def websocket_state(ws: WebSocket) -> None:
    """Client subscribes to channels; server pushes state-change
    events as they're emitted by the poll loop. Subscription frame
    format::

        {"subscribe": ["music", "satellites", "downloads", ...]}

    Empty list / no frame = subscribed to all channels.
    """
    await ws.accept()
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
