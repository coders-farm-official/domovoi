"""The container guard on the dashboard (docs/CONTAINER_GUARD.md).

Reads come straight from ``container_incidents`` (the web process shares
the core's Postgres), tiered the dashboard's usual way
(:mod:`domovoi.container_guard.views`):

* ``GET /api/containers/summary`` — OPEN: whether something needs an
  admin's attention and the banner's colour, for a guest's browser or an
  unpaired tablet. Nothing about what failed.
* ``GET /api/containers/incidents`` — household READ: the open and recent
  incidents in plain words, the banner and the freeze state. An admin
  caller (Bearer or the dashboard cookie) also gets what was expected and
  found, and who acknowledged or cleared what.
* ``GET /api/containers/status`` — admin READ: the core guard's last check
  (what it watches, what Docker showed, what it holds and why).

The actions are the core's (it is the only writer): acknowledge a
failure, clear a tamper incident after review, run a check now. Each is
``require_admin_mutation`` at both hops.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from domovoi import admin_auth
from domovoi.admin_auth import require_admin_mutation, require_admin_read, require_device_read
from domovoi.container_guard import store, views

from web.backend.db import session_scope
from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    get_admin,
    post_admin,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/containers", tags=["containers"])


async def _rows() -> list[dict[str, Any]]:
    """Open + recent incidents; [] when they can't be read (V024 not yet
    applied, the database away) — the guard can't have recorded any."""
    try:
        async with session_scope() as s:
            return await store.recent_incidents(s)
    except Exception as e:  # noqa: BLE001
        log.debug("container incidents unreadable: %s", e)
        return []


async def snapshot() -> dict[str, Any]:
    """The ``containers`` realtime channel's value: the household view.
    The state socket admits the household tier and up, so this never
    carries an admin's details (they read them over HTTP)."""
    return views.household_payload(await _rows(), admin=False)


async def _caller_is_admin(request: Request) -> bool:
    result = await admin_auth.check_admin_request(request)
    # pre-setup counts: before anyone claims the box, everything is open
    # to the LAN, the admin's view included (the household's usual grace).
    return result in ("ok", "cookie-only", "pre-setup")


@router.get("/summary")
async def containers_summary() -> dict[str, Any]:
    """OPEN. ``{attention, tone, text}`` — for a browser with no household
    credential: "Something needs an admin's attention", in red or amber,
    and nothing more."""
    return views.public_summary(await _rows())


@router.get("/incidents", dependencies=[Depends(require_device_read)])
async def containers_incidents(request: Request) -> dict[str, Any]:
    """Household READ. ``{incidents, banner, frozen, attention}``: every
    open incident and those resolved in the last 24 hours, newest first,
    in plain words; an admin caller also gets ``expected`` / ``found``."""
    return views.household_payload(await _rows(), admin=await _caller_is_admin(request))


@router.get("/status", dependencies=[Depends(require_admin_read)])
async def containers_status(request: Request):
    """Admin READ: the core guard's last check, proxied."""
    return bridge_response(
        *await get_admin("/v1/admin/containers", headers=auth_forward_headers(request))
    )


class ClearBody(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.post("/check", dependencies=[Depends(require_admin_mutation)])
async def containers_check(request: Request):
    """Run a check now (admin)."""
    return bridge_response(
        *await post_admin(
            "/v1/admin/containers/check", {}, headers=auth_forward_headers(request), timeout=90.0,
        )
    )


@router.post(
    "/incidents/{incident_id}/acknowledge", dependencies=[Depends(require_admin_mutation)]
)
async def containers_acknowledge(incident_id: int, request: Request):
    """Acknowledge an open failure (admin): the banner hides it until
    something new happens."""
    return bridge_response(
        *await post_admin(
            f"/v1/admin/containers/incidents/{incident_id}/acknowledge", {},
            headers=auth_forward_headers(request),
        )
    )


@router.post("/incidents/{incident_id}/clear", dependencies=[Depends(require_admin_mutation)])
async def containers_clear(incident_id: int, request: Request, body: ClearBody | None = None):
    """Clear a tamper incident after review (admin), with an optional note.
    The freeze lifts when no tamper incident is open."""
    return bridge_response(
        *await post_admin(
            f"/v1/admin/containers/incidents/{incident_id}/clear",
            {"note": body.note if body else None},
            headers=auth_forward_headers(request),
        )
    )
