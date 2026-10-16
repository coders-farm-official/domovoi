"""The freeze: while a tamper incident is open, the operations that change
what code runs on the house's machines refuse.

Frozen (docs/CONTAINER_GUARD.md, "The freeze"):

* plugin installs (stage and confirm), upgrades (stage and confirm) and
  enabling a plugin — core ``/v1/plugins/...``;
* satellite code upgrades and the plugin payload pushes that ride them —
  core ``/v1/admin/satellite/upgrade``, and the satellite code and plugin
  payload channels themselves (``/v1/satellite-code/*``,
  ``/v1/satellite-plugins/*``), so an upgrade started any other way gets
  nothing to install;
* satellite media preparation — web ``/api/satellites/media/prepare`` and
  its download-cache refresh;
* handing out the Android app — core ``/v1/android-app/files/*`` (the
  APKs phones download from the join page) and the admin's fetch and
  upload that fill that cache;
* the update pull and apply — core ``/v1/admin/version/pull``, the
  channel switch beside it (``/v1/admin/version/channel``: what every later
  pull follows), and
  ``/v1/admin/version/restart`` whenever it would load code that isn't
  running yet (pulled code or a staged plugin upgrade; on an update-unit
  host that is the unit's trigger). A plain restart with nothing new to
  load still works.

Everything else keeps working. The refusal is HTTP 423 with the coded body
the dashboard already renders (``{"detail": {"error": {code, message,
details}}}``), naming the incident. The check runs AFTER the route's own
credential gate, so a caller without one learns nothing here.

Fail-open on a database it can't read: with no ``container_incidents``
table (V024 not applied) or no database at all, the guard can't have
recorded anything, and every frozen operation needs the database anyway.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from fastapi import HTTPException

from domovoi.container_guard import store

log = logging.getLogger(__name__)

CODE = "container_guard_frozen"
STATUS = 423

OPERATIONS: dict[str, str] = {
    "plugin_install": "Plugin installs are",
    "plugin_upgrade": "Plugin upgrades are",
    "plugin_enable": "Enabling plugins is",
    "satellite_upgrade": "Satellite code upgrades are",
    "satellite_channel": "Satellite code and plugin payload downloads are",
    "satellite_media": "Satellite media preparation is",
    "android_app": "Handing out the Android app is",
    "update_pull": "Pulling Domovoi updates is",
    "update_apply": "Applying Domovoi updates is",
}

# A short cache: the satellite channels answer one request per file.
_CACHE_SEC = 2.0
_cache: tuple[float, list[dict[str, Any]]] | None = None


def forget() -> None:
    """Drop the cached answer (an incident was just cleared; tests)."""
    global _cache
    _cache = None


def _default_session_scope() -> Any:
    from domovoi.db.session import session_scope

    return session_scope()


async def frozen_incidents(session_scope: Callable[[], Any] | None = None) -> list[dict[str, Any]]:
    """The open tamper incidents ([] when none, or when they can't be read)."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_SEC:
        return _cache[1]
    try:
        async with (session_scope or _default_session_scope)() as s:
            rows = await store.frozen_by(s)
    except Exception as e:  # noqa: BLE001 — fail open, see the module docstring
        log.debug("container guard freeze state unreadable: %s", e)
        rows = []
    _cache = (now, rows)
    return rows


def refusal_detail(operation: str, incidents: list[dict[str, Any]]) -> dict[str, Any]:
    first = incidents[0]
    opened = store.iso(first.get("opened_at"))
    more = f" and {len(incidents) - 1} more" if len(incidents) > 1 else ""
    what = OPERATIONS.get(operation, "This is")
    summary = str(first.get("summary") or "see the Containers page").rstrip(". ")
    message = (
        f"{what} paused: the container guard found signs of tampering "
        f"(incident #{first['id']}{more}: {summary}). "
        f"An admin must review and clear it on the Containers page first."
    )
    return {
        "error": {
            "code": CODE,
            "message": message,
            "details": {
                "operation": operation,
                "incidents": [
                    {"id": i["id"], "summary": i.get("summary"), "opened_at": store.iso(i.get("opened_at"))}
                    for i in incidents
                ],
                "opened_at": opened,
            },
        }
    }


PUBLIC_MESSAGE = "Paused by Domovoi's container guard until an admin reviews a problem."


async def require_unfrozen(
    operation: str,
    *,
    session_scope: Callable[[], Any] | None = None,
    public: bool = False,
) -> None:
    """Raise HTTP 423 naming the incident when ``operation`` is frozen.
    ``public``: the route has no credential gate (the satellite channels),
    so the refusal says only that it is paused."""
    incidents = await frozen_incidents(session_scope)
    if incidents:
        log.warning("container guard: refused %s while incident #%s is open",
                    operation, incidents[0]["id"])
        detail = (
            {"error": {"code": CODE, "message": PUBLIC_MESSAGE, "details": {}}}
            if public else refusal_detail(operation, incidents)
        )
        raise HTTPException(status_code=STATUS, detail=detail)


async def is_frozen(session_scope: Callable[[], Any] | None = None) -> bool:
    return bool(await frozen_incidents(session_scope))
