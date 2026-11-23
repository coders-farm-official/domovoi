"""The core's support-session routes (1.5.0; domovoi/support_sessions.py).

| Route | Tier |
|---|---|
| ``POST /v1/admin/satellites/{satellite_id}/support/allow`` | security + password sign-in within 5 min |
| ``POST /v1/admin/satellites/{satellite_id}/support/disallow`` | admin (ends an open session) |
| ``POST /v1/admin/satellites/{satellite_id}/support/open`` | security + fresh sign-in + the password typed again |
| ``WS /v1/admin/support-sessions/{id}/terminal`` | the single-use ticket (``X-Support-Ticket``), relayed by the dashboard |
| ``POST /v1/admin/support-sessions/{id}/end`` | device tier: any paired household device may end one |
| ``GET /v1/admin/support-sessions/active`` | device read (the banner) |
| ``GET /v1/admin/support-sessions`` | security read, Bearer only (the audit list) |
| ``GET /v1/admin/support-sessions/{id}/transcript`` | security read, Bearer only |
| ``DELETE /v1/admin/support-sessions/{id}`` / ``DELETE /v1/admin/support-sessions`` | security |
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi import admin_auth, satellite_abilities, support_sessions
from domovoi.admin_auth import (
    require_admin_mutation,
    require_admin_security,
    require_admin_security_read,
    require_device,
    require_device_read,
)
from domovoi.db.session import session_scope
from domovoi.transport_guard import origin_allowed

log = logging.getLogger(__name__)

router = APIRouter()

TICKET_HEADER = "x-support-ticket"

STALE_DETAIL = (
    "sign in with the password again first: a support session needs a "
    f"password sign-in within the last {admin_auth.PHONE_GRANT_FRESH_SIGNIN_SEC // 60} minutes"
)
WRONG_PASSWORD_DETAIL = "that password is not right"


class OpenBody(BaseModel):
    # A plain str, checked in the handler: a pydantic constraint would echo
    # a rejected value back in a 422 body.
    password: str = ""
    cols: int = Field(default=100)
    rows: int = Field(default=30)


async def _require_fresh(request: Request) -> str:
    """The caller's Bearer, after checking it proved the password within
    five minutes (``signed_in_at``). A phone-grant session never has."""
    token = admin_auth.bearer_token(request)
    async with session_scope() as s:
        if not await admin_auth.signed_in_recently(s, token):
            raise HTTPException(status_code=403, detail=STALE_DETAIL)
    assert token is not None
    return token


async def _satellite_row(satellite_id: str) -> Any:
    async with session_scope() as s:
        row = (await s.execute(
            text("SELECT satellite_id, room_id, support_allowed FROM satellites "
                 "WHERE satellite_id = :id"),
            {"id": satellite_id},
        )).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no satellite {satellite_id!r}")
    return row


@router.post(
    "/v1/admin/satellites/{satellite_id}/support/allow",
    dependencies=[Depends(require_admin_security)],
)
async def support_allow(satellite_id: str, request: Request) -> dict[str, Any]:
    """Turn "Allow support sessions" ON for one satellite: the security
    tier, and a password sign-in within the last five minutes."""
    await _require_fresh(request)
    await _satellite_row(satellite_id)
    async with session_scope() as s:
        await s.execute(
            text("UPDATE satellites SET support_allowed = true, updated_at = now() "
                 "WHERE satellite_id = :id"), {"id": satellite_id})
    log.info("support sessions allowed on %s", satellite_id)
    return {"satellite_id": satellite_id, "support_allowed": True}


@router.post(
    "/v1/admin/satellites/{satellite_id}/support/disallow",
    dependencies=[Depends(require_admin_mutation)],
)
async def support_disallow(satellite_id: str, request: Request) -> dict[str, Any]:
    """Turn it OFF (any admin): an open session on it ends."""
    await _satellite_row(satellite_id)
    async with session_scope() as s:
        await s.execute(
            text("UPDATE satellites SET support_allowed = false, updated_at = now() "
                 "WHERE satellite_id = :id"), {"id": satellite_id})
    ended = await support_sessions.get_manager(request.app).end_for_satellite(
        satellite_id, "switch_off")
    log.info("support sessions turned off on %s (ended one: %s)", satellite_id, ended)
    return {"satellite_id": satellite_id, "support_allowed": False, "ended": ended}


@router.post(
    "/v1/admin/satellites/{satellite_id}/support/open",
    dependencies=[Depends(require_admin_security)],
)
async def support_open(satellite_id: str, body: OpenBody, request: Request) -> dict[str, Any]:
    """Open a support session: the security tier, a password sign-in
    within five minutes, the password typed again (behind the login
    backoff), the satellite's switch on, its hello declaring
    ``support_shell``, and no other session on it. Returns the browser
    socket's single-use ticket; the satellite hears nothing until that
    socket attaches."""
    token = await _require_fresh(request)
    source = admin_auth.enforce_login_backoff(request)
    ok = False
    if 0 < len(body.password) <= 1024:
        async with session_scope() as s:
            stored = await admin_auth.get_password_hash(s)
        ok = bool(stored) and admin_auth.verify_password(stored, body.password)
    if not ok:
        admin_auth.LOGIN_BACKOFF.record_failure(source)
        log.info("support session refused for %s: wrong password", satellite_id)
        raise HTTPException(status_code=403, detail=WRONG_PASSWORD_DETAIL)
    admin_auth.LOGIN_BACKOFF.release(source)

    row = await _satellite_row(satellite_id)
    if not row.support_allowed:
        raise HTTPException(
            status_code=409,
            detail="support sessions are off for this satellite: turn on Allow support sessions first",
        )
    target = (getattr(request.app.state, "active_sessions", None) or {}).get(satellite_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"{satellite_id} is not connected")
    abilities = satellite_abilities.session_abilities(target)
    if not abilities.allows(support_sessions.ABILITY) or getattr(target, "board", None) in \
            satellite_abilities.SPARE_BOARDS:
        raise HTTPException(
            status_code=409,
            detail=satellite_abilities.unsupported_detail(
                satellite_id, getattr(target, "board", None), "a support session",
                support_sessions.ABILITY),
        )
    if getattr(target, "dropin_peer", None) is not None:
        raise HTTPException(status_code=409, detail="the room is in a drop-in call")
    if getattr(target, "wake_recording", None) is not None:
        raise HTTPException(status_code=409, detail="the room is recording wake-word clips")
    mgr = support_sessions.get_manager(request.app)
    try:
        sess, ticket = await mgr.create(
            satellite_id=satellite_id, room_id=row.room_id,
            admin_hash=admin_auth.token_sha256(token), cols=body.cols, rows=body.rows,
        )
    except support_sessions.Refused as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None
    return {
        "session_id": sess.id,
        "ticket": ticket,
        "ticket_ttl_sec": int(support_sessions.TICKET_TTL_SEC),
        "max_sec": sess.max_sec,
        "idle_sec": sess.idle_sec,
    }


@router.websocket("/v1/admin/support-sessions/{session_id}/terminal")
async def support_terminal(ws: WebSocket, session_id: str) -> None:
    """The terminal's socket, reached through the dashboard's relay. The
    ticket (``X-Support-Ticket``) is single use and bound to the admin
    session and the satellite; the admin session must still be live."""
    if not origin_allowed(ws.headers.get("origin")):
        await ws.close(code=1008)
        return
    mgr = support_sessions.get_manager(ws.app)
    sess = None
    if support_sessions.valid_id(session_id):
        sess = mgr.redeem(ws.headers.get(TICKET_HEADER), session_id)
    if sess is None:
        log.info("support terminal refused: no valid ticket for %s", session_id[:8])
        await ws.close(code=1008)
        return
    await ws.accept()
    await mgr.run(ws, sess)
    try:
        await ws.close()
    except Exception:  # noqa: BLE001
        pass


@router.post(
    "/v1/admin/support-sessions/{session_id}/end",
    dependencies=[Depends(require_device)],
)
async def support_end(session_id: str, request: Request) -> dict[str, Any]:
    """End a session: the admin's End, or any paired household device
    (ending only removes power, so the device tier is enough)."""
    result = await admin_auth.check_admin_request(request)
    reason = "admin_ended" if result == "ok" else "household_ended"
    ended = await support_sessions.get_manager(request.app).end_by_id(session_id, reason)
    if not ended:
        raise HTTPException(status_code=404, detail="no such open support session")
    return {"ended": True, "reason": reason}


@router.get(
    "/v1/admin/support-sessions/active",
    dependencies=[Depends(require_device_read)],
)
async def support_active(request: Request) -> dict[str, Any]:
    """The open sessions, for the banner every dashboard viewer sees."""
    return {"sessions": support_sessions.get_manager(request.app).active()}


@router.get(
    "/v1/admin/support-sessions",
    dependencies=[Depends(require_admin_security_read)],
)
async def support_list(satellite_id: str | None = None) -> dict[str, Any]:
    return {"sessions": await support_sessions.list_sessions(satellite_id)}


@router.get(
    "/v1/admin/support-sessions/{session_id}/transcript",
    dependencies=[Depends(require_admin_security_read)],
)
async def support_transcript(session_id: str) -> dict[str, Any]:
    """The transcript: ``cast`` (asciicast v2, as stored) and ``text``
    (its output with escape codes taken out). The page shows ``text`` as
    text only; nothing here is ever HTML."""
    row = await support_sessions.get_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such support session")
    cast = support_sessions.read_transcript(session_id)
    if cast is None:
        raise HTTPException(status_code=404, detail="this session's transcript is gone")
    return {"session": row, "cast": cast, "text": support_sessions.plain_text(cast)}


@router.delete(
    "/v1/admin/support-sessions/{session_id}",
    dependencies=[Depends(require_admin_security)],
)
async def support_delete(session_id: str) -> dict[str, Any]:
    if not await support_sessions.delete_session(session_id):
        raise HTTPException(status_code=404, detail="no such closed support session")
    return {"deleted": 1}


@router.delete(
    "/v1/admin/support-sessions",
    dependencies=[Depends(require_admin_security)],
)
async def support_delete_all() -> dict[str, Any]:
    return {"deleted": await support_sessions.delete_all()}
