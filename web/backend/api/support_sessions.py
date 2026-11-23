"""Satellite support sessions (1.5.0): the dashboard's half.

The core owns the sessions (domovoi/support_sessions.py and
domovoi/support_routes.py); every route here checks its own gate and then
forwards the caller's credential, so both processes check it.

The terminal's socket: a browser can't put a header on a WebSocket, so
``POST /api/satellites/{id}/support/open`` returns a single-use ticket
(30 seconds, bound to the admin session and the satellite, only its
sha256 kept by the core). The page offers it as the subprotocol
``ticket.<hex>`` next to ``domovoi-support``, never in the URL. The socket
also checks ``Origin`` (``collab.origin_ok``, and it must be present: a
browser always sends one), then relays every frame to the core's socket
over loopback, where the ticket is burned.
"""

from __future__ import annotations

import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel

from domovoi import admin_auth
from domovoi.admin_auth import (
    require_admin_mutation,
    require_admin_security,
    require_admin_security_read,
    require_device,
    require_device_read,
)
from web.backend.api.collab import origin_ok
from web.backend.db import session_scope
from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    delete_admin,
    domovoi_url,
    get_admin,
    post_admin,
)

log = logging.getLogger(__name__)

router = APIRouter()

SUBPROTOCOL = "domovoi-support"
TICKET_PREFIX = "ticket."
#: A relayed browser frame is at most this long (4 KB of input as base64 in JSON).
MAX_FRAME_CHARS = 8 * 1024

STALE_DETAIL = (
    "sign in with the password again first: this needs a password sign-in "
    f"within the last {admin_auth.PHONE_GRANT_FRESH_SIGNIN_SEC // 60} minutes"
)


class OpenRequest(BaseModel):
    password: str = ""
    cols: int = 100
    rows: int = 30


def _sat_path(satellite_id: str) -> str:
    if not satellite_id or len(satellite_id) > 120 or "/" in satellite_id:
        raise HTTPException(status_code=404, detail="no such satellite")
    return satellite_id


def _sid(session_id: str) -> str:
    if len(session_id) != 32 or any(c not in "0123456789abcdef" for c in session_id):
        raise HTTPException(status_code=404, detail="no such support session")
    return session_id


async def _require_fresh(request: Request) -> None:
    async with session_scope() as s:
        if not await admin_auth.signed_in_recently(s, admin_auth.bearer_token(request)):
            raise HTTPException(status_code=403, detail=STALE_DETAIL)


@router.post(
    "/api/satellites/{satellite_id}/support/allow",
    dependencies=[Depends(require_admin_security)],
)
async def allow(satellite_id: str, request: Request):
    """Turn "Allow support sessions" on: security tier and a password
    sign-in within five minutes, here and at the core."""
    await _require_fresh(request)
    status, payload = await post_admin(
        f"/v1/admin/satellites/{_sat_path(satellite_id)}/support/allow",
        headers=auth_forward_headers(request))
    return bridge_response(status, payload)


@router.post(
    "/api/satellites/{satellite_id}/support/disallow",
    dependencies=[Depends(require_admin_mutation)],
)
async def disallow(satellite_id: str, request: Request):
    """Turn it off (any admin); an open session on it ends."""
    status, payload = await post_admin(
        f"/v1/admin/satellites/{_sat_path(satellite_id)}/support/disallow",
        headers=auth_forward_headers(request))
    return bridge_response(status, payload)


@router.post(
    "/api/satellites/{satellite_id}/support/open",
    dependencies=[Depends(require_admin_security)],
)
async def open_session(satellite_id: str, body: OpenRequest, request: Request):
    """Open a session: the core checks the password typed again (with the
    login backoff, keyed on the forwarded address), the switch, the
    satellite's declared ability. Answers the socket's ticket."""
    await _require_fresh(request)
    status, payload = await post_admin(
        f"/v1/admin/satellites/{_sat_path(satellite_id)}/support/open",
        body={"password": body.password, "cols": body.cols, "rows": body.rows},
        headers=auth_forward_headers(request))
    response = bridge_response(status, payload)
    if hasattr(response, "headers"):
        response.headers["Cache-Control"] = "no-store"
    return response


def _offered_ticket(ws: WebSocket) -> str | None:
    offered = [p.strip() for p in (ws.headers.get("sec-websocket-protocol") or "").split(",")]
    if SUBPROTOCOL not in offered:
        return None
    for p in offered:
        if p.startswith(TICKET_PREFIX):
            ticket = p[len(TICKET_PREFIX):]
            if 16 <= len(ticket) <= 128 and all(c in "0123456789abcdef" for c in ticket):
                return ticket
    return None


@router.websocket("/api/support-sessions/{session_id}/terminal")
async def terminal(ws: WebSocket, session_id: str) -> None:
    """The browser's terminal socket: Origin first (present, and a
    dashboard's), then the ticket, then a relay to the core's socket."""
    origin = (ws.headers.get("origin") or "").strip()
    if not origin or not origin_ok(ws):
        log.info("support terminal refused: Origin %r", origin)
        await ws.close(code=1008)
        return
    ticket = _offered_ticket(ws)
    if ticket is None or len(session_id) != 32:
        await ws.close(code=1008)
        return
    from websockets.asyncio.client import connect

    url = domovoi_url().rstrip("/").replace("http", "ws", 1) + \
        f"/v1/admin/support-sessions/{session_id}/terminal"
    try:
        upstream = await connect(url, additional_headers={"X-Support-Ticket": ticket},
                                 max_size=64 * 1024, open_timeout=10)
    except Exception as e:  # noqa: BLE001 - refused (bad ticket) or the core is down
        log.info("support terminal: the core refused or is away: %s", e)
        await ws.close(code=1008)
        return
    await ws.accept(subprotocol=SUBPROTOCOL)

    async def browser_to_core() -> None:
        while True:
            msg = await ws.receive_text()
            if len(msg) <= MAX_FRAME_CHARS:
                await upstream.send(msg)

    async def core_to_browser() -> None:
        async for msg in upstream:
            if isinstance(msg, str):
                await ws.send_text(msg)

    tasks = [asyncio.create_task(browser_to_core()), asyncio.create_task(core_to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        try:
            await upstream.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


@router.post(
    "/api/support-sessions/{session_id}/end",
    dependencies=[Depends(require_device)],
)
async def end(session_id: str, request: Request):
    """End a session: the admin, or any paired household device."""
    status, payload = await post_admin(
        f"/v1/admin/support-sessions/{_sid(session_id)}/end",
        headers=auth_forward_headers(request))
    return bridge_response(status, payload)


@router.get("/api/support-sessions/active", dependencies=[Depends(require_device_read)])
async def active(request: Request):
    """The open sessions, for the banner (every household viewer)."""
    status, payload = await get_admin(
        "/v1/admin/support-sessions/active", headers=auth_forward_headers(request))
    return bridge_response(status, payload)


@router.get("/api/support-sessions", dependencies=[Depends(require_admin_security_read)])
async def list_sessions(request: Request, satellite_id: str | None = None):
    path = "/v1/admin/support-sessions"
    if satellite_id:
        from urllib.parse import quote

        path += f"?satellite_id={quote(satellite_id, safe='')}"
    status, payload = await get_admin(path, headers=auth_forward_headers(request))
    return bridge_response(status, payload)


@router.get(
    "/api/support-sessions/{session_id}/transcript",
    dependencies=[Depends(require_admin_security_read)],
)
async def transcript(session_id: str, request: Request):
    """The transcript as JSON (``text`` is plain text: the page sets it as
    text, never as HTML)."""
    status, payload = await get_admin(
        f"/v1/admin/support-sessions/{_sid(session_id)}/transcript",
        headers=auth_forward_headers(request))
    return bridge_response(status, payload)


@router.get(
    "/api/support-sessions/{session_id}/transcript.cast",
    dependencies=[Depends(require_admin_security_read)],
)
async def transcript_download(session_id: str, request: Request) -> Response:
    """The asciicast file as a download: ``text/plain``, an attachment,
    ``nosniff`` and the ``sandbox`` CSP, so it never renders as a page."""
    status, payload = await get_admin(
        f"/v1/admin/support-sessions/{_sid(session_id)}/transcript",
        headers=auth_forward_headers(request))
    if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("cast"), str):
        return bridge_response(status, payload)
    return Response(
        content=payload["cast"].encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="support-session-{session_id}.cast"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "no-store",
        },
    )


@router.delete(
    "/api/support-sessions/{session_id}",
    dependencies=[Depends(require_admin_security)],
)
async def delete_one(session_id: str, request: Request):
    status, payload = await delete_admin(
        f"/v1/admin/support-sessions/{_sid(session_id)}", headers=auth_forward_headers(request))
    return bridge_response(status, payload)


@router.delete("/api/support-sessions", dependencies=[Depends(require_admin_security)])
async def delete_all(request: Request):
    status, payload = await delete_admin(
        "/v1/admin/support-sessions", headers=auth_forward_headers(request))
    return bridge_response(status, payload)
