"""Capability manifest for Android + the dashboard (design §8).

``GET /api/capabilities`` is served ENTIRELY from data the web process
already has — plugin rows come from the ``plugins`` registry JSONB, and
core handlers' display metadata comes from the static table below (the
manifest makes ``label``/``tone`` declared facts, cross-checked against
code at install, precisely so this endpoint never needs the core
process). It therefore works even while the core is down.

``GET /api/capabilities/manual`` is the one display surface that needs
live core data (example phrases derived from tool schemas): it proxies
the core's ``GET /v1/handlers`` and caches the last good response to
disk — core down ⇒ the stale cache is served with ``"stale": true``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter

from web.backend.plugin_host import HOST

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])

SERVER_VERSION = "1.0.0"
DOMOVOI_API = "1.0.0"

# Static display table for CORE handlers (§8): name → label/tone. Kept
# in lockstep with each handler class's HandlerDisplay — the §13.2-style
# cross-check for these is the core test suite; the payoff is that
# /api/capabilities never consults the core's in-memory registry.
CORE_HANDLER_DISPLAY: list[dict[str, str]] = [
    {"name": "dismiss",       "label": "Dismiss",        "tone": "neutral"},
    {"name": "voice_profile", "label": "Voice Profile",  "tone": "info"},
    {"name": "wifi",          "label": "Wi-Fi",          "tone": "device"},
    {"name": "voice",         "label": "Voice",          "tone": "device"},
    {"name": "reminder",      "label": "Reminders",      "tone": "info"},
    {"name": "calculator",    "label": "Calculator",     "tone": "info"},
    {"name": "timer",         "label": "Timers",         "tone": "info"},
    {"name": "clock",         "label": "Clock",          "tone": "info"},
    {"name": "repeat",        "label": "Repeat",         "tone": "info"},
    {"name": "double_check",  "label": "Double-Check",   "tone": "info"},
    {"name": "dropin",        "label": "Drop-In",        "tone": "comms"},
    {"name": "intercom",      "label": "Intercom",       "tone": "comms"},
    {"name": "chat_mode",     "label": "Chat Mode",      "tone": "comms"},
    {"name": "voice_notes",   "label": "Voice Notes",    "tone": "comms"},
    {"name": "memory",        "label": "Memory",         "tone": "info"},
    {"name": "homelab",       "label": "Homelab",        "tone": "device"},
    {"name": "news",          "label": "News",           "tone": "info"},
    {"name": "spoken_audio",  "label": "Podcasts & Books", "tone": "media"},
    {"name": "playlist",      "label": "Playlists",      "tone": "media"},
    {"name": "music",         "label": "Music",          "tone": "media"},
    {"name": "library",       "label": "Library",        "tone": "media"},
]

# Extra now-playing/history *source* tones the dashboard needs beyond
# handler names (media_plays.source values that aren't handler names).
CORE_SOURCE_TONES: dict[str, str] = {
    "library": "media",
    "playlist": "media",
    "spoken_audio": "media",
}


def _features() -> dict[str, bool]:
    """Feature booleans for compiled-in screens/tabs that aren't plugin
    capabilities. Read from the web process's settings copy — these are
    restart-tier flags, so staleness is not a concern."""
    try:
        from domovoi.config import settings

        return {
            "chat": bool(getattr(settings, "chat_mode_enabled", False)),
            "office": bool(
                getattr(settings, "onlyoffice_enabled", False)
                or getattr(settings, "collabora_enabled", False)
            ),
        }
    except Exception:  # pragma: no cover - config import failure
        return {"chat": False, "office": False}


@router.get("")
async def capabilities() -> dict[str, Any]:
    """Screens/tabs gating + handler display metadata (§8). Open."""
    plugins: list[dict[str, Any]] = []
    handler_display: list[dict[str, Any]] = list(CORE_HANDLER_DISPLAY)
    seen = {h["name"] for h in handler_display}
    for slug in sorted(HOST.rows):
        r = HOST.rows[slug]
        if not r["enabled"] or r["status"] not in ("ok", "degraded"):
            continue
        m = r["manifest"] or {}
        plugins.append(
            {
                "slug": slug,
                "version": r["version"],
                "android_capabilities": (m.get("android") or {}).get(
                    "capabilities"
                ) or [],
            }
        )
        for h in m.get("handlers") or []:
            name = h.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            handler_display.append(
                {
                    "name": name,
                    "label": h.get("label") or name,
                    "tone": h.get("tone") or "neutral",
                }
            )
    return {
        "domovoi_api": DOMOVOI_API,
        "server_version": SERVER_VERSION,
        "plugins": plugins,
        "handler_display": handler_display,
        "source_tones": CORE_SOURCE_TONES,
        "features": _features(),
    }


# ─── /manual — proxied + disk-cached core handler registry ─────────────────


def _cache_path() -> Path:
    root = Path(
        os.environ.get("DOMOVOI_HOME", str(Path.home() / ".domovoi"))
    )
    return root / "cache" / "handlers.json"


def _read_cache() -> list[dict[str, Any]] | None:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(handlers: list[dict[str, Any]]) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(handlers), encoding="utf-8")
    except Exception as e:  # pragma: no cover - disk trouble
        log.debug("handlers cache write failed: %s", e)


@router.get("/manual")
async def capabilities_manual() -> dict[str, Any]:
    """The user-manual feature table: merged handler registry with
    origin, band, display, and example phrases (core ``/v1/handlers``,
    §12). Core down ⇒ last good response with ``stale: true``; no cache
    yet ⇒ names/labels only from the static display table."""
    core = os.environ.get("DOMOVOI_URL", "http://localhost:6370")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{core.rstrip('/')}/v1/handlers")
        r.raise_for_status()
        handlers = r.json()
        _write_cache(handlers)
        return {"handlers": handlers, "stale": False}
    except Exception as e:
        log.debug("core /v1/handlers unreachable: %s", e)
    cached = _read_cache()
    if cached is not None:
        return {"handlers": cached, "stale": True}
    # No cache — degrade to names + labels from the static table.
    fallback = [
        {
            "name": h["name"],
            "origin": "core",
            "display": {"label": h["label"], "tone": h["tone"], "icon": None},
            "example_phrases": [],
        }
        for h in CORE_HANDLER_DISPLAY
    ]
    return {"handlers": fallback, "stale": True, "unavailable": True}
