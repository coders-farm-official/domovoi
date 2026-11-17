"""Friend Houses on the dashboard: "Serverless messaging and friend system
between households" (``domovoi/friend_houses/``).

The core owns it (its private connection, the friend files, the requests
and the friends list); this router proxies the dashboard's Settings →
Household → Friend Houses section to the core's ``/v1/admin/friend-houses``
routes with the caller's credentials forwarded, as every live-state action
does. Each route wears the core's tier here too, so a refused caller is
refused before anything is forwarded: the security tier at both hops,
reads included (a friend file is a bearer secret, and who this house's
friends are is the household's business).

A friend file or a message only ever travels in a POST body (preview,
send a request, send a message) or a response body (a file just made, a
conversation): never in a URL, a query string or a log line. A bad body is refused with a fixed sentence, never with
FastAPI's validation echo of what was sent.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError

from domovoi.admin_auth import require_admin_security, require_admin_security_read
from web.backend.api.csrf_guard import require_requested_with
from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    delete_admin,
    get_admin,
    patch_admin,
    post_admin,
    put_admin,
)

router = APIRouter(prefix="/api/friend-houses", tags=["friend-houses"])
READ = [Depends(require_admin_security_read)]
WRITE = [Depends(require_requested_with), Depends(require_admin_security)]

CORE = "/v1/admin/friend-houses"

# The core's ids: an invite is 32 hex, a request or friend a short prefix
# plus hex. Anything else is not one of ours and never reaches the core's
# URL.
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# A friend file is a few hundred bytes; this is a ceiling, not a format.
MAX_FILE_CHARS = 16_000


def _id(value: str, what: str) -> str:
    if not _ID.match(value or ""):
        raise HTTPException(status_code=404, detail=f"No such {what}.")
    return value


async def _body(request: Request, model: type[BaseModel]) -> dict[str, Any]:
    """The JSON body checked against ``model``, refused in plain words.
    Read by hand rather than as a FastAPI body parameter: a validation
    error there echoes the input back, and the input may be a friend file."""
    try:
        raw = await request.json()
    except Exception:  # noqa: BLE001 — not JSON at all
        raw = None
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="That request wasn't understood.")
    try:
        return model.model_validate(raw).model_dump(exclude_none=True)
    except (ValidationError, RequestValidationError):
        raise HTTPException(status_code=400, detail="That request wasn't understood.") from None


class SettingsBody(BaseModel):
    house_name: str | None = None
    invite_days: int | None = None


class InviteBody(BaseModel):
    days: int | None = None
    label: str | None = None


class FileBody(BaseModel):
    file: str
    name: str | None = None


class NameBody(BaseModel):
    name: str


def _file_body(body: dict[str, Any]) -> dict[str, Any]:
    if len(body.get("file") or "") > MAX_FILE_CHARS:
        raise HTTPException(status_code=400, detail="That doesn't look like a friend file: it is far too long.")
    return body


# ─── status and this house ───────────────────────────────────────────────


@router.get("", dependencies=READ)
async def friend_houses_status(request: Request):
    """Whether the private connection runs (and why not), this house's
    fingerprint and name, the default friend-file length and the limits."""
    return bridge_response(*await get_admin(CORE, headers=auth_forward_headers(request)))


@router.put("/settings", dependencies=WRITE)
async def friend_houses_settings(request: Request):
    body = await _body(request, SettingsBody)
    return bridge_response(
        *await put_admin(f"{CORE}/settings", body, headers=auth_forward_headers(request))
    )


# ─── friend files this house made ────────────────────────────────────────


@router.get("/invites", dependencies=READ)
async def friend_houses_invites(request: Request):
    return bridge_response(*await get_admin(f"{CORE}/invites", headers=auth_forward_headers(request)))


@router.post("/invites", dependencies=WRITE)
async def friend_houses_make_invite(request: Request):
    """Make a friend file. ``file`` is null while the private connection
    is still being set up; the page reads ``/invites/{id}`` until it is
    there. The first start builds an image, so this may take a while."""
    body = await _body(request, InviteBody)
    return bridge_response(
        *await post_admin(f"{CORE}/invites", body, timeout=90.0, headers=auth_forward_headers(request))
    )


@router.get("/invites/{invite_id}", dependencies=READ)
async def friend_houses_invite(invite_id: str, request: Request):
    iid = _id(invite_id, "friend file")
    return bridge_response(
        *await get_admin(f"{CORE}/invites/{iid}", headers=auth_forward_headers(request))
    )


@router.delete("/invites/{invite_id}", dependencies=WRITE)
async def friend_houses_cancel_invite(invite_id: str, request: Request):
    iid = _id(invite_id, "friend file")
    return bridge_response(
        *await delete_admin(f"{CORE}/invites/{iid}", headers=auth_forward_headers(request))
    )


# ─── another house's friend file ─────────────────────────────────────────


@router.post("/preview", dependencies=WRITE)
async def friend_houses_preview(request: Request):
    """What a friend file says. Nothing is sent to the other house."""
    body = _file_body(await _body(request, FileBody))
    return bridge_response(
        *await post_admin(f"{CORE}/preview", body, timeout=30.0, headers=auth_forward_headers(request))
    )


@router.post("/requests", dependencies=WRITE)
async def friend_houses_send_request(request: Request):
    body = _file_body(await _body(request, FileBody))
    return bridge_response(
        *await post_admin(f"{CORE}/requests", body, timeout=90.0, headers=auth_forward_headers(request))
    )


# ─── requests ────────────────────────────────────────────────────────────


@router.get("/requests", dependencies=READ)
async def friend_houses_requests(request: Request):
    return bridge_response(*await get_admin(f"{CORE}/requests", headers=auth_forward_headers(request)))


@router.post("/requests/{request_id}/accept", dependencies=WRITE)
async def friend_houses_accept(request_id: str, request: Request):
    rid = _id(request_id, "request")
    body = await _body(request, NameBody)
    return bridge_response(
        *await post_admin(f"{CORE}/requests/{rid}/accept", body, timeout=60.0,
                          headers=auth_forward_headers(request))
    )


@router.post("/requests/{request_id}/decline", dependencies=WRITE)
async def friend_houses_decline(request_id: str, request: Request):
    rid = _id(request_id, "request")
    return bridge_response(
        *await post_admin(f"{CORE}/requests/{rid}/decline", {}, headers=auth_forward_headers(request))
    )


# ─── friends ─────────────────────────────────────────────────────────────


@router.get("/friends", dependencies=READ)
async def friend_houses_friends(request: Request):
    return bridge_response(*await get_admin(f"{CORE}/friends", headers=auth_forward_headers(request)))


@router.post("/friends/{friend_id}/verified", dependencies=WRITE)
async def friend_houses_verified(friend_id: str, request: Request):
    fid = _id(friend_id, "friend house")
    return bridge_response(
        *await post_admin(f"{CORE}/friends/{fid}/verified", {}, headers=auth_forward_headers(request))
    )


@router.patch("/friends/{friend_id}", dependencies=WRITE)
async def friend_houses_rename(friend_id: str, request: Request):
    fid = _id(friend_id, "friend house")
    body = await _body(request, NameBody)
    return bridge_response(
        *await patch_admin(f"{CORE}/friends/{fid}", body, headers=auth_forward_headers(request))
    )


@router.delete("/friends/{friend_id}", dependencies=WRITE)
async def friend_houses_unfriend(friend_id: str, request: Request):
    fid = _id(friend_id, "friend house")
    return bridge_response(
        *await delete_admin(f"{CORE}/friends/{fid}", timeout=60.0, headers=auth_forward_headers(request))
    )


# ─── messages (F3) ───────────────────────────────────────────────────────
# A message body only ever travels in a POST body (send) or a response
# body (a conversation, an export): never in a URL or a log line. A
# conversation is addressed by the other house's public house key (64 hex).

_HOUSE = re.compile(r"^[0-9a-f]{64}$")
ALERTS = ("inbox", "sender", "read")
MAX_MESSAGE_CHARS = 2000


def _house(value: str) -> str:
    if not _HOUSE.match(value or ""):
        raise HTTPException(status_code=404, detail="No such conversation.")
    return value


class MessageBody(BaseModel):
    friend_id: str
    text: str


class AlertBody(BaseModel):
    alert: str


@router.get("/conversations", dependencies=READ)
async def friend_houses_conversations(request: Request):
    """The house inbox: every friend (or former friend) with messages."""
    return bridge_response(
        *await get_admin(f"{CORE}/conversations", headers=auth_forward_headers(request))
    )


@router.get("/conversations/{house}", dependencies=READ)
async def friend_houses_conversation(
    house: str,
    request: Request,
    before: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """One house's messages, oldest first; ``before`` for earlier ones."""
    h = _house(house)
    q = f"?limit={limit}" + (f"&before={before}" if before is not None else "")
    return bridge_response(
        *await get_admin(f"{CORE}/conversations/{h}{q}", headers=auth_forward_headers(request))
    )


@router.post("/conversations/{house}/read", dependencies=WRITE)
async def friend_houses_mark_read(house: str, request: Request):
    h = _house(house)
    return bridge_response(
        *await post_admin(f"{CORE}/conversations/{h}/read", {}, headers=auth_forward_headers(request))
    )


@router.get("/conversations/{house}/export", dependencies=READ)
async def friend_houses_export(house: str, request: Request):
    """Every message with one house, each with its signed envelope."""
    h = _house(house)
    return bridge_response(
        *await get_admin(f"{CORE}/conversations/{h}/export", timeout=60.0,
                         headers=auth_forward_headers(request))
    )


@router.delete("/conversations/{house}", dependencies=WRITE)
async def friend_houses_delete_conversation(house: str, request: Request):
    h = _house(house)
    return bridge_response(
        *await delete_admin(f"{CORE}/conversations/{h}", headers=auth_forward_headers(request))
    )


@router.post("/messages", dependencies=WRITE)
async def friend_houses_send(request: Request):
    """Queue a message to a friend (1–2000 characters)."""
    body = await _body(request, MessageBody)
    _id(body.get("friend_id", ""), "friend house")
    text = body.get("text") or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="Write something to send.")
    if len(text) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=400,
                            detail=f"A message can be at most {MAX_MESSAGE_CHARS} characters.")
    return bridge_response(
        *await post_admin(f"{CORE}/messages", body, timeout=30.0, headers=auth_forward_headers(request))
    )


@router.put("/friends/{friend_id}/alert", dependencies=WRITE)
async def friend_houses_alert(friend_id: str, request: Request):
    """What a new message from this friend does in the rooms."""
    fid = _id(friend_id, "friend house")
    body = await _body(request, AlertBody)
    if body.get("alert") not in ALERTS:
        raise HTTPException(status_code=400, detail="Choose one of the three choices.")
    return bridge_response(
        *await put_admin(f"{CORE}/friends/{fid}/alert", body, headers=auth_forward_headers(request))
    )


# ─── shared lists (F4) ───────────────────────────────────────────────────
# A shared list is addressed by its 32-hex id; a title or a name only ever
# travels in a body.

_SPACE = re.compile(r"^[0-9a-f]{32}$")


def _space(value: str) -> str:
    if not _SPACE.match(value or ""):
        raise HTTPException(status_code=404, detail="No such shared list.")
    return value


class ShareListBody(BaseModel):
    list_id: int = Field(ge=1, strict=True)
    friend_id: str


class AcceptListBody(BaseModel):
    name: str | None = None


@router.get("/shared-lists", dependencies=READ)
async def friend_houses_shared_lists(request: Request):
    """Lists shared with friend houses, both ways, and offers waiting here."""
    return bridge_response(
        *await get_admin(f"{CORE}/shared-lists", headers=auth_forward_headers(request))
    )


@router.post("/shared-lists", dependencies=WRITE)
async def friend_houses_share_list(request: Request):
    """Share one of this house's lists with a friend."""
    body = await _body(request, ShareListBody)
    _id(body.get("friend_id", ""), "friend house")
    return bridge_response(
        *await post_admin(f"{CORE}/shared-lists", body, timeout=30.0, headers=auth_forward_headers(request))
    )


@router.post("/shared-lists/{space_id}/accept", dependencies=WRITE)
async def friend_houses_accept_list(space_id: str, request: Request):
    """Take a friend's list: it becomes a new list here (``name`` optional)."""
    sid = _space(space_id)
    body = await _body(request, AcceptListBody)
    return bridge_response(
        *await post_admin(f"{CORE}/shared-lists/{sid}/accept", body, timeout=30.0,
                          headers=auth_forward_headers(request))
    )


@router.post("/shared-lists/{space_id}/decline", dependencies=WRITE)
async def friend_houses_decline_list(space_id: str, request: Request):
    """Forget a friend's offer (they aren't told)."""
    sid = _space(space_id)
    return bridge_response(
        *await post_admin(f"{CORE}/shared-lists/{sid}/decline", {}, headers=auth_forward_headers(request))
    )


@router.delete("/shared-lists/{space_id}", dependencies=WRITE)
async def friend_houses_leave_list(space_id: str, request: Request):
    """Stop sharing. Both houses keep their own copy as an ordinary list."""
    sid = _space(space_id)
    return bridge_response(
        *await delete_admin(f"{CORE}/shared-lists/{sid}", timeout=30.0, headers=auth_forward_headers(request))
    )
