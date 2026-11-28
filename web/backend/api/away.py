"""Away chat on the dashboard's API: ``/api/away`` (1.5.0).

Away chat lets a household member's paired phone chat with its own house
from anywhere, over a Tor onion service of its own (``domovoi/away/``;
docs/SECURITY_PRIVACY.md, "Away chat"; docs/API_REFERENCE.md). Here:

The phone's setup, over the home network only
    ``POST /api/away/enroll``, ``DELETE /api/away/enroll`` and
    ``GET /api/away/status`` take the phone's identity exactly as the chat
    routes do: the device tier (``X-Device-Token`` or an admin Bearer) and
    the device KEY (``X-Domovoi-Device-Id`` + ``X-Domovoi-Device-Key``,
    V086); the device is the one its key resolves to, never an id the
    caller names. Enrolling and turning it off for itself are LAN-only: a
    caller whose address isn't private or loopback gets ``403 lan_only``.
    A guest, or a device marked a shared screen, gets ``409 guest``.

System → Chat from away (admins)
    ``GET /api/away/phones`` lists the phones that set it up,
    ``DELETE /api/away/phones/{device_id}`` removes one, and
    ``PUT /api/away/setting`` turns "Chat from away" on or off for the
    house (on by default; off removes the away onion).

The core's relay (not part of the API)
    ``POST /api/away/bridge``: the core hands each request a phone sent
    over the onion to the chat code here (``web/backend/away_bridge.py``).
    Loopback only, and only with the household token AND the core's
    bridge secret of this start (``domovoi/away/keys.py``).

Every change tells the core (``POST /v1/away/reconcile``), which rewrites
the listener's phone list (a removed phone is refused from its next
handshake) and Tor's keys (batched reloads). Nothing here is reachable
over the onion.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from domovoi import admin_auth, chat_access
from domovoi.admin_auth import (
    require_admin_read,
    require_admin_security,
    require_device,
    require_device_after_setup,
)
from domovoi.away import keys as away_keys
from domovoi.away import store as away_store
from domovoi.away import wire
from web.backend.api.csrf_guard import require_requested_with
from web.backend.db import session_scope
from web.backend.domovoi_client import domovoi_url, service_auth_headers

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/away", tags=["away-chat"])

DEVICE = [Depends(require_device)]
ADMIN_READ = [Depends(require_admin_read)]
# Turning a remote surface on (or a phone off) is the security tier's:
# Bearer only, closed before setup.
ADMIN_WRITE = [Depends(require_requested_with), Depends(require_admin_security)]

PORT = 80
BRIDGE_HEADER = "X-Domovoi-Away-Bridge"
#: The onion's states for a phone (``state`` in enroll and status). A phone
#: treats one it doesn't know as ``starting``.
STATES = ("ready", "publishing", "starting")


def _refused(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, "code": code})


def lan_caller(request: Request) -> bool:
    """Whether the caller is on the home network: a private, link-local or
    loopback address (the transport peer, or the forwarded client of a
    trusted proxy: ``admin_auth.request_source``)."""
    try:
        addr = ipaddress.ip_address(admin_auth.request_source(request).strip("[]"))
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_private or addr.is_loopback or addr.is_link_local


async def _caller(request: Request, s: Any) -> tuple[str, str] | JSONResponse:
    """``(device_id, device_key_hash)`` of the calling phone, proven by its
    device key, or the refusal to answer with."""
    if await admin_auth.request_is_guest(request):
        return _refused(409, "guest", "away chat is for household members' phones")
    presented = chat_access.presented_device_key(request)
    found = await chat_access.device_for_key(s, presented)
    if found is None or presented is None:
        return _refused(401, "device_key_required",
                        "this phone can't prove which device it is (its device key is missing or out of "
                        "date): register it again at home")
    device_id, shared_screen = found
    if shared_screen:
        return _refused(409, "guest", "a shared screen is the household's, not a member's phone")
    return device_id, chat_access.device_key_hash(presented)


async def tell_core(request: Request | None) -> bool:
    """Ask the core to apply the phones and the setting now (its watch
    would within a minute anyway). Best effort; False when it didn't
    answer."""
    import httpx

    headers = await service_auth_headers()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(f"{domovoi_url().rstrip('/')}/v1/away/reconcile", headers=headers)
        return r.status_code == 200
    except Exception as e:  # noqa: BLE001 — the core's watch catches up
        log.info("away chat: the core wasn't told (%s)", type(e).__name__)
        return False


async def core_state(device_id: str) -> str | None:
    """The core's word on the away onion for this phone: ``ready``,
    ``publishing`` (Tor has its key; the descriptor carrying it is probably
    still on its way), ``starting``, or None (not running). ``starting``
    when it doesn't answer."""
    import httpx

    headers = await service_auth_headers()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{domovoi_url().rstrip('/')}/v1/away/state",
                                 params={"device": device_id}, headers=headers)
        if r.status_code == 200:
            state = r.json().get("state")
            return state if state in STATES else None
    except Exception as e:  # noqa: BLE001 — said as starting
        log.info("away chat: the core's state couldn't be read (%s)", type(e).__name__)
    return "starting"


# ─── the phone's setup ───────────────────────────────────────────────────


class EnrollBody(BaseModel):
    # Plain strings, checked by hand: a validation echo must never carry a
    # key back out.
    tor_auth_pub: Any = None
    noise_pub: Any = None


@router.post(
    "/enroll",
    # The device tier without the pre-setup grace: no remote surface opens
    # on a house nobody has claimed (501 until an admin exists).
    dependencies=[Depends(require_device_after_setup)],
    responses={
        400: {"description": '{"code": "bad_key"}: a key has the wrong shape or length, or the two are the same'},
        401: {"description": '{"code": "device_key_required"}, or the device tier\'s own answer'},
        403: {"description": '{"code": "lan_only"}: not called from the home network'},
        409: {"description": '{"code": "away_off" | "internet_off" | "guest"}'},
    },
)
async def enroll(body: EnrollBody, request: Request) -> Any:
    """Set away chat up for this phone, or replace both of its keys. The
    phone sends the public halves of two X25519 keys it made (Tor client
    authorization: 52 characters of upper-case base32; Noise static: 43
    characters of base64url); the answer is the away onion, its port, the
    house's away Noise key, the protocol number and whether the onion is
    ``ready``, still ``starting`` (the first phone's enrolment restarts
    Tor; later ones ride a batched reload) or ``publishing`` (Tor has the
    new key, and the onion's descriptor carrying it is on its way: about a
    minute)."""
    if not lan_caller(request):
        return _refused(403, "lan_only", "away chat is set up on the home network only")
    tor_raw = away_store.tor_auth_raw(body.tor_auth_pub) if isinstance(body.tor_auth_pub, str) else None
    noise = away_keys.unb64url(body.noise_pub, 32) if isinstance(body.noise_pub, str) else None
    if tor_raw is None or noise is None or tor_raw == noise or noise == bytes(32):
        return _refused(400, "bad_key", "a key has the wrong shape or length, or the two keys are the same")
    async with session_scope() as s:
        caller = await _caller(request, s)
        if isinstance(caller, JSONResponse):
            return caller
        device_id, key_hash = caller
        state = await away_store.house_state(s)
        if state != "on":
            detail = ("an admin turned Chat from away off for the house" if state == "away_off"
                      else "the house's internet setting doesn't allow it")
            return _refused(409, state, detail)
        if await away_store.noise_taken(s, noise, device_id):
            return _refused(400, "bad_key", "that key is already in use")
        await away_store.enroll(s, device_id, key_hash, body.tor_auth_pub, noise)
    house_key = away_keys.noise_key(create=True)
    seed = away_keys.onion_seed(create=True)
    assert house_key is not None and seed is not None
    log.info("away chat: device %r enrolled", device_id)
    await tell_core(request)
    state = await core_state(device_id)
    return {
        "onion": away_keys.onion_address(seed),
        "port": PORT,
        "house_noise_pub": away_keys.b64url(away_keys.noise_public(house_key)),
        "proto": wire.PROTO,
        "state": state if state in ("ready", "publishing") else "starting",
    }


@router.delete("/enroll", dependencies=DEVICE)
async def unenroll(request: Request) -> Any:
    """Turn away chat off for this phone (or it is unpairing): its keys are
    revoked at once for the Noise check, and its Tor key goes in the next
    batched reload. Always ``{"ok": true}``, enrolled or not."""
    if not lan_caller(request):
        return _refused(403, "lan_only", "away chat is set up on the home network only")
    async with session_scope() as s:
        presented = chat_access.presented_device_key(request)
        found = await chat_access.device_for_key(s, presented)
        dropped = bool(found) and await away_store.revoke(s, found[0])
    if dropped:
        assert found is not None
        log.info("away chat: device %r turned it off", found[0])
        await tell_core(request)
    return {"ok": True}


@router.get("/status", dependencies=DEVICE)
async def status(request: Request) -> dict[str, Any]:
    """Why the away onion can or can't be reached, for the app at home
    (the onion itself never says): the house's state (``on``,
    ``away_off``, ``internet_off``), whether this phone is enrolled, and
    whether its onion is ``ready``, ``publishing`` or ``starting`` (null
    when it isn't enrolled or the house has it off)."""
    async with session_scope() as s:
        house = await away_store.house_state(s)
        found = await chat_access.device_for_key(s, chat_access.presented_device_key(request))
        enrolled = bool(found) and await away_store.is_enrolled(s, found[0])
    state: str | None = None
    if enrolled and house == "on":
        assert found is not None
        state = await core_state(found[0])
        if state not in ("ready", "publishing"):
            await tell_core(request)  # nudge: a word that didn't arrive
            state = "starting"
    return {"house": house, "enrolled": enrolled, "state": state}


# ─── System → Chat from away ─────────────────────────────────────────────


@router.get("/phones", dependencies=ADMIN_READ)
async def list_phones() -> dict[str, Any]:
    """The house setting and every phone that set away chat up: its name,
    platform, when, and whether it counts now (``current``: false once the
    device lost the key it enrolled with, or became a shared screen)."""
    async with session_scope() as s:
        on = await away_store.setting_on(s)
        house = await away_store.house_state(s)
        phones = await away_store.list_for_admin(s)
    return {"on": on, "house": house, "phones": phones}


@router.delete("/phones/{device_id}", dependencies=ADMIN_WRITE)
async def remove_phone(device_id: str, request: Request) -> dict[str, Any]:
    """Remove one phone's away chat: the same as the phone turning it off."""
    if not 1 <= len(device_id) <= 64:
        raise HTTPException(status_code=404, detail="no such phone")
    async with session_scope() as s:
        dropped = await away_store.revoke(s, device_id)
    if not dropped:
        raise HTTPException(status_code=404, detail="no such phone")
    log.info("away chat: an admin removed device %r", device_id)
    await tell_core(request)
    return {"ok": True}


class SettingBody(BaseModel):
    on: bool = Field(..., description="Chat from away on (the default) or off for the house")


@router.put("/setting", dependencies=ADMIN_WRITE)
async def put_setting(body: SettingBody, request: Request) -> dict[str, Any]:
    """Turn "Chat from away" on or off for the house. Off removes the away
    onion (enrolled phones keep their keys and work again when it's back
    on); open connections are closed with ``BYE away_off``."""
    async with session_scope() as s:
        await away_store.set_setting(s, body.on)
        house = await away_store.house_state(s)
    log.info("away chat: an admin turned it %s", "on" if body.on else "off")
    await tell_core(request)
    return {"on": body.on, "house": house}


# ─── the core's relay ────────────────────────────────────────────────────


async def require_bridge(request: Request) -> None:
    """The away bridge route's own gate, on top of the device tier: a
    loopback caller presenting the core's bridge secret of this start."""
    peer = request.client.host if request.client else ""
    try:
        loopback = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        loopback = False
    if not loopback or not away_keys.bridge_matches(request.headers.get(BRIDGE_HEADER.lower())):
        raise HTTPException(status_code=401, detail="not the core's away bridge")


class BridgeBody(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=64)
    noise_pub: str = Field(..., min_length=64, max_length=64)
    request: dict[str, Any]
    picture: str = Field("", max_length=1_500_000)


@router.post("/bridge", include_in_schema=False,
             dependencies=[Depends(require_device), Depends(require_bridge)])
async def bridge(body: BridgeBody) -> StreamingResponse:
    """One phone request, through the home chat code; one JSON line per
    message back (``{"k": "resp"|"event"|"err"|"bye", "b": {...}, "end":
    bool}``). The reply goes on (and is stored) if this stream is cut."""
    from web.backend import away_bridge

    stream = away_bridge.handle(body.device_id, body.noise_pub, body.request, body.picture)
    return StreamingResponse(stream, media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
