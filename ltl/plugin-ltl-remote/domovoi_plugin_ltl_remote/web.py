"""Web-process entry point: ``register_web(ctx)``.

Runs in the SEPARATE dashboard process (:6369). Hard rules, tripwired at
install and enforced by the web process's import guard:

* never imports ``core.py``, the plugin's SDK-touching modules, or any
  ``domovoi.*`` module outside ``domovoi.webkit`` — this file touches
  only stdlib, FastAPI/pydantic/SQLAlchemy (present in the web process),
  and nothing else;
* DB access goes through ``ctx.db_session_scope``, which arrives with
  ``search_path = plugin_ltl_remote, public`` preset;
* anything that changes live state — opening a pairing window, approving
  a device, rotating the relay token — is PROXIED to the plugin's own
  core endpoints through ``ctx.core.post_admin`` with the caller's
  credentials forwarded. The web process holds no admin credential of
  its own and never reaches the relay socket.

Router mounts at ``/api/plugins/ltl_remote``; the page's JSX is served
from ``/plugins/ltl_remote/static``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Column lists live once each and are shared by the read endpoints AND
# the snapshots, so a schema change cannot drift between what the page
# fetches and what the websocket pushes.
_LINK_COLUMNS = """
    fingerprint, household_id, account_label, claimed_at,
    connection_state, last_connected_at, last_disconnected_at, last_error,
    pairing_code, pairing_expires_at,
    plan_code, quota_used_bytes, quota_limit_bytes, quota_period_end
"""

_DEVICE_COLUMNS = """
    device_id, label, fingerprint, status,
    registered_at, approved_at, revoked_at, last_seen_at, last_seen_country
"""


# ─── Schemas ──────────────────────────────────────────────────────────────


class LinkState(BaseModel):
    """One ``link_state`` row, minus anything binary.

    Public keys are deliberately absent: the fingerprint is the only part
    of the identity a person needs, and shipping 65 raw bytes to a page
    that would only hash them again is noise.
    """

    fingerprint: str = ""
    household_id: str | None = None
    account_label: str | None = None
    claimed_at: datetime | None = None
    connection_state: str = "idle"
    last_connected_at: datetime | None = None
    last_disconnected_at: datetime | None = None
    last_error: str | None = None
    pairing_code: str | None = None
    pairing_expires_at: datetime | None = None
    plan_code: str | None = None
    quota_used_bytes: int = 0
    quota_limit_bytes: int | None = None
    quota_period_end: datetime | None = None


class RemoteDevice(BaseModel):
    device_id: str
    label: str
    fingerprint: str
    status: str
    registered_at: datetime | None = None
    approved_at: datetime | None = None
    revoked_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_seen_country: str | None = None


class AccessLogEntry(BaseModel):
    id: int
    device_id: str | None = None
    at: datetime
    method: str
    path: str
    status: int | None = None
    outcome: str
    denial_code: str | None = None
    bytes_in: int = 0
    bytes_out: int = 0
    duration_ms: int | None = None


class DeviceAction(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=128)


# ─── Registration ─────────────────────────────────────────────────────────


def register_web(ctx: Any) -> None:
    ctx.add_router(build_router(ctx))


def build_router(ctx: Any) -> APIRouter:
    """Build the router against a WebPluginContext-shaped object
    (``db_session_scope`` + ``core``). Split from :func:`register_web` so
    the test harness can mount it directly."""
    router = APIRouter(tags=["plugin:ltl_remote"])
    session_scope = ctx.db_session_scope

    # ── Reads: straight from the plugin's own schema. ─────────────────

    @router.get("/state", response_model=LinkState)
    async def link_state() -> LinkState:
        async with session_scope() as session:
            row = (
                await session.execute(
                    text(f"SELECT {_LINK_COLUMNS} FROM link_state WHERE id = 1")
                )
            ).first()
        if row is None:
            # The migration inserts the singleton, so this only happens
            # if someone deleted it by hand. A clean default beats a 500.
            return LinkState()
        return LinkState(**dict(row._mapping))

    @router.get("/devices", response_model=list[RemoteDevice])
    async def devices(
        status: str | None = Query(default=None, pattern="^(pending|approved|revoked)$"),
    ) -> list[RemoteDevice]:
        clause = "WHERE status = :status " if status else ""
        async with session_scope() as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT {_DEVICE_COLUMNS} FROM remote_devices {clause}"
                        "ORDER BY registered_at DESC LIMIT 200"
                    ),
                    {"status": status} if status else {},
                )
            ).all()
        return [RemoteDevice(**dict(r._mapping)) for r in rows]

    @router.get("/access-log", response_model=list[AccessLogEntry])
    async def access_log(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        device_id: str | None = Query(default=None, max_length=128),
    ) -> list[AccessLogEntry]:
        clause = "WHERE device_id = :device_id " if device_id else ""
        async with session_scope() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, device_id, at, method, path, status, outcome, "
                        "denial_code, bytes_in, bytes_out, duration_ms "
                        f"FROM remote_access_log {clause}"
                        "ORDER BY at DESC LIMIT :limit OFFSET :offset"
                    ),
                    {"limit": limit, "offset": offset, "device_id": device_id},
                )
            ).all()
        return [AccessLogEntry(**dict(r._mapping)) for r in rows]

    @router.get("/badge")
    async def badge() -> dict[str, int | str]:
        """Sidebar badge: how many devices are waiting for a decision,
        plus the connection state so the nav can show a dot."""
        async with session_scope() as session:
            pending = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM remote_devices WHERE status = 'pending'"
                    )
                )
            ).scalar_one()
            state = (
                await session.execute(
                    text("SELECT connection_state FROM link_state WHERE id = 1")
                )
            ).scalar_one_or_none()
        return {"pending": int(pending or 0), "connection": state or "idle"}

    # ── Writes: proxied to core with the caller's credentials. ─────────
    #
    # The web process must not reach the relay socket or the key files,
    # and it holds no admin credential — so every mutation becomes an
    # admin-gated call to the plugin's own core router, with the incoming
    # request's Authorization/Cookie forwarded. Both processes then
    # enforce the gate independently.

    async def _proxy(request: Request, path: str, body: Any = None) -> Any:
        try:
            return await ctx.core.post_admin(path, json=body, request=request)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — CoreDown and friends
            raise HTTPException(
                status_code=503,
                detail="the Domovoi core service is not reachable",
            ) from e

    @router.post("/pairing/start")
    async def start_pairing(request: Request) -> Any:
        return await _proxy(request, "pairing/start")

    @router.post("/pairing/cancel")
    async def cancel_pairing(request: Request) -> Any:
        return await _proxy(request, "pairing/cancel")

    @router.post("/devices/approve")
    async def approve_device(body: DeviceAction, request: Request) -> Any:
        return await _proxy(request, "devices/approve", body.model_dump())

    @router.post("/devices/revoke")
    async def revoke_device(body: DeviceAction, request: Request) -> Any:
        return await _proxy(request, "devices/revoke", body.model_dump())

    @router.post("/token/rotate")
    async def rotate_token(request: Request) -> Any:
        return await _proxy(request, "token/rotate")

    @router.post("/unlink")
    async def unlink(request: Request) -> Any:
        return await _proxy(request, "unlink")

    return router


# ─── Realtime snapshots ───────────────────────────────────────────────────
#
# Called by the web state poll loop AND on NOTIFY; the return value is
# broadcast verbatim on the mapped channel. Both are deliberately cheap
# and carry nothing elapsed-time-shaped, which would make every poll look
# like a change.


async def snapshot_link(session: AsyncSession) -> dict[str, Any]:
    row = (
        await session.execute(
            text(f"SELECT {_LINK_COLUMNS} FROM link_state WHERE id = 1")
        )
    ).first()
    return {"link": dict(row._mapping) if row is not None else {}}


async def snapshot_devices(session: AsyncSession) -> dict[str, Any]:
    rows = (
        await session.execute(
            text(
                f"SELECT {_DEVICE_COLUMNS} FROM remote_devices "
                "WHERE status <> 'revoked' ORDER BY registered_at DESC LIMIT 200"
            )
        )
    ).all()
    return {"devices": [dict(r._mapping) for r in rows]}


SNAPSHOTS = {
    "snapshot_link": snapshot_link,
    "snapshot_devices": snapshot_devices,
}
