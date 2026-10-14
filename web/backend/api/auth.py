"""Admin auth endpoints (design §7.2/§7.3, v1 scope amendment).

The web process hosts the auth surface; the core process only
*validates* tokens (both against the same ``admin_auth`` /
``admin_sessions`` tables via :mod:`domovoi.admin_auth`).

Flow: the core boot wrote an 8-word setup code to
``~/.domovoi/setup-code.txt`` (and printed it, when its stdout is a
terminal — never into the journal). ``POST /setup`` requires
that code (proof of possession of the server — 403 without it, even
though no admin row exists yet), stores the argon2id hash, deletes the
code file, and returns a first session token. ``POST /login`` verifies
the password behind the per-source exponential backoff and mints a
256-bit token (sha256-stored, 30-day sliding expiry). Login/setup also
set the ``SameSite=Strict`` ``HttpOnly`` cookie so plain GET page loads
render authenticated state — mutations everywhere accept ONLY
``Authorization: Bearer`` (the dashboard JS holds the token in memory).

A reload forgets that in-memory token. For ``ADMIN_RELOAD_WINDOW_MIN``
(default 60) minutes after the password was entered, ``POST /resume``
gives the page holding the cookie its own token back — owner decision
2026-10-14, "refreshing should not lose admin on the same machine, same
session". It answers only a same-origin request (:func:`same_origin`)
carrying ``X-Requested-With``, and only for the cookie's own session
inside its window (:func:`domovoi.admin_auth.resume_session`). ``GET
/status`` says whether this browser can (``resume_in_sec``), so the page
asks only when it can.

v1 runs over plain LAN HTTP (TLS is on the documented hardening
backlog), so the cookie is NOT marked ``Secure``.

The household **device token** (device tier, ``X-Device-Token``) is
served, set and rotated here too — ``GET/POST
/api/auth/device-token[/rotate]`` work against the same
``household_device_tokens`` table as the core's ``/v1/admin/device-token``
endpoints, so either process answers identically.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from domovoi import admin_auth
from domovoi.config import settings as core_settings
from domovoi.host_kind import host_kind
from web.backend.api.csrf_guard import require_requested_with
from web.backend.db import session_scope
from web.backend.domovoi_client import get_cached_snapshot

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_MAX_AGE_SEC = admin_auth.SESSION_TTL_DAYS * 86400


def reload_window_min() -> int:
    """The live ``ADMIN_RELOAD_WINDOW_MIN``, in minutes (0 = off).

    Edited through the core (Settings → Configuration, tier ``hot``),
    which mutates the CORE's settings; this process's copy is whatever
    ``.env`` said when it booted. So the value comes from the core's
    snapshot (refreshed every 1.5 s), falling back to this process's own
    copy only while the core has not answered — the same rule as
    ``guest_queue_editing``. Either way it is clamped to 0..1440, and a
    value that is not a whole number reads as 0."""
    live = (get_cached_snapshot() or {}).get("admin_reload_window_min")
    if isinstance(live, int) and not isinstance(live, bool):
        return admin_auth.clamp_reload_window(live)
    return admin_auth.clamp_reload_window(core_settings.admin_reload_window_min)


def _resume_fields(
    window: int, resume: admin_auth.ResumeWindow | None
) -> dict[str, Any]:
    """What every auth answer says about the reload window: its length,
    and — when this browser's session is inside it — when it ends
    (``resume_until``, the server's clock) and how many seconds are left
    (``resume_in_sec``, what a browser counts from). ``None`` for both
    when there is nothing to resume."""
    fields: dict[str, Any] = {
        "reload_window_min": window,
        "resume_until": None,
        "resume_in_sec": None,
    }
    if resume is not None:
        fields.update(resume.as_json())
    return fields


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin_tuple(scheme: str, netloc: str) -> tuple[str, str, int] | None:
    scheme = (scheme or "").lower()
    if scheme not in _DEFAULT_PORTS or not netloc:
        return None
    try:
        parts = urlsplit(f"//{netloc}")
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:  # a port that is not a number
        return None
    if not host:
        return None
    return scheme, host, port if port is not None else _DEFAULT_PORTS[scheme]


def same_origin(request: Request) -> bool:
    """Did a page on THIS origin send this request?

    A browser puts ``Origin`` on every POST it makes, same-origin ones
    included, and no page can choose its value; it must be exactly the
    scheme, host and port this request was addressed to (the request's
    scheme and its ``Host``). A browser that leaves ``Origin`` off still
    sends ``Sec-Fetch-Site``, which must then say ``same-origin``; when it
    sends both, both must agree. A request with neither is refused — the
    resume exists for the dashboard page, and every browser it supports
    sends at least one.

    Why this on top of ``SameSite=Strict`` and ``X-Requested-With``: a
    "site" is a host, not a port. Another service on this box, or a second
    Domovoi dashboard reached through the server switcher, is same-site,
    so the browser brings this cookie to it, and the CORS allowance for
    the server switcher would let that page read the answer — the token."""
    fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site and fetch_site != "same-origin":
        return False
    origin = (request.headers.get("origin") or "").strip()
    if not origin:
        return fetch_site == "same-origin"
    if origin.lower() == "null":
        return False
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        return False
    theirs = _origin_tuple(parts.scheme, parts.netloc)
    ours = _origin_tuple(request.url.scheme, request.headers.get("host") or "")
    return theirs is not None and theirs == ours


def setup_code_refusal() -> str:
    """Why a setup was refused for its code, and where the right one is,
    in the words of this kind of host. The Windows installer's Linux
    system (``wsl``) shows its code on the installer's last page, and
    nobody there can open ``~/.domovoi`` (docs/WINDOWS_HOST.md: run the
    installer again for a fresh code); everywhere else the code is the
    file the core wrote."""
    if host_kind() == "wsl":
        return (
            "setup code required — use the one on the last page of "
            "Domovoi's Windows installer; a code older than a day, or a "
            "lost one, is replaced by running the installer again"
        )
    return (
        "setup code required — read it from "
        "~/.domovoi/setup-code.txt on the Domovoi server (the "
        "core also prints it when it runs in a terminal); a code "
        "older than a day is replaced by restarting the core"
    )


def _set_session_cookie(response: JSONResponse, token: str) -> None:
    response.set_cookie(
        admin_auth.COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE_SEC,
        httponly=True,
        samesite="strict",
        path="/",
        # No `secure=True` in v1: admin flows run over plain LAN HTTP
        # (scope amendment); a Secure cookie would simply never be sent.
    )


class SetupRequest(BaseModel):
    setup_code: str = Field(..., min_length=1)
    password: str = Field(..., min_length=10, max_length=256)


class LoginRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)
    label: str | None = Field(None, max_length=120)


class PasswordChangeRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=10, max_length=256)


@router.get("/status")
async def auth_status(request: Request) -> dict[str, Any]:
    """Open probe the dashboard uses to decide setup-vs-login-vs-quiet.
    ``authenticated`` reflects the cookie OR a Bearer (GET state).

    ``resume_in_sec`` / ``resume_until`` are set while THIS browser's
    session cookie is inside its reload window — what tells a reloaded
    page to ask ``POST /resume`` for its sign-in back, and what the
    Settings Admin card says it lasts until. Read-only: nothing slides."""
    window = reload_window_min()
    resume = None
    async with session_scope() as s:
        setup_complete = await admin_auth.has_admin_auth(s)
        result = await admin_auth.check_admin_request(request, s)
        if result in ("ok", "cookie-only"):
            resume = await admin_auth.resume_deadline(
                s, admin_auth.cookie_token(request), window
            )
    return {
        "setup_complete": setup_complete,
        "authenticated": result in ("ok", "cookie-only"),
        **_resume_fields(window, resume),
    }


@router.post("/setup")
async def auth_setup(body: SetupRequest, request: Request) -> JSONResponse:
    """First-run claim of the admin tier. Requires the setup code even
    though no admin row exists — closing the race where a hostile LAN
    host claims the unclaimed tier (§7.2). 409 once setup is done."""
    source = admin_auth.enforce_login_backoff(request)
    async with session_scope() as s:
        if await admin_auth.has_admin_auth(s):
            raise HTTPException(status_code=409, detail="admin setup already completed")
        if not admin_auth.verify_setup_code(body.setup_code):
            # A bad/missing/expired code counts toward the source's
            # backoff — the attempt was reserved before the check, so the
            # failure is already on the books; just confirm it. The code
            # is guessable-in-theory (64-bit) but never for free.
            admin_auth.LOGIN_BACKOFF.record_failure(source)
            raise HTTPException(status_code=403, detail=setup_code_refusal())
        await admin_auth.set_password(s, body.password)
        token = await admin_auth.create_session(s, label="setup")
        # The household device token was readable by anyone on the LAN
        # during the pre-setup window; a fresh one starts with the admin.
        device_token = await admin_auth.rotate_device_token(s)
        window = reload_window_min()
        resume = await admin_auth.resume_deadline(s, token, window)
    admin_auth.LOGIN_BACKOFF.record_success(source)
    admin_auth.delete_setup_code()
    admin_auth.write_device_token_file(device_token)
    response = JSONResponse({"ok": True, "token": token, **_resume_fields(window, resume)})
    _set_session_cookie(response, token)
    return response


@router.post("/login")
async def auth_login(body: LoginRequest, request: Request) -> JSONResponse:
    source = admin_auth.enforce_login_backoff(request)
    async with session_scope() as s:
        password_hash = await admin_auth.get_password_hash(s)
        if password_hash is None:
            raise HTTPException(
                status_code=409, detail="admin setup not completed yet"
            )
        if not admin_auth.verify_password(password_hash, body.password):
            # Reserved before the verify (enforce_login_backoff); confirm.
            admin_auth.LOGIN_BACKOFF.record_failure(source)
            raise HTTPException(status_code=401, detail="wrong password")
        token = await admin_auth.create_session(s, label=body.label)
        window = reload_window_min()
        resume = await admin_auth.resume_deadline(s, token, window)
    admin_auth.LOGIN_BACKOFF.record_success(source)
    response = JSONResponse({"ok": True, "token": token, **_resume_fields(window, resume)})
    _set_session_cookie(response, token)
    return response


_RESUME_CROSS_SITE_DETAIL = (
    "a sign-in is only resumed for this dashboard's own page — this request "
    "came from another origin"
)
_RESUME_NO_COOKIE_DETAIL = "no admin sign-in in this browser to resume — sign in"
_RESUME_CLOSED_DETAIL = (
    "this browser's admin sign-in can't be resumed — it ended, or it is "
    "more than the reload window old; sign in again"
)


@router.post("/resume", dependencies=[Depends(require_requested_with)])
async def auth_resume(request: Request) -> JSONResponse:
    """Give a reloaded dashboard page its admin sign-in back (owner
    decision 2026-10-14).

    The page's Bearer lived in its memory and the reload forgot it; the
    HttpOnly session cookie the sign-in set survived and IS that token.
    This hands it back — nothing new is minted — when ALL of these hold:

    * ``X-Requested-With`` (the dependency, and the process-wide write
      backstop in front of it): a cross-site page cannot send it without
      a preflight this server refuses;
    * the request is same-origin (:func:`same_origin`): not another port
      on this host, not a second dashboard through the server switcher;
    * the cookie names a live session (not revoked, signed out, expired or
      past the 90-day cap) whose ``signed_in_at`` is less than
      ``ADMIN_RELOAD_WINDOW_MIN`` minutes ago (0 = never).

    403 for the first two, 401 for the rest; nothing about the session
    changes on a refusal. A success slides the session's expiry like any
    use of it does, and says when the window ends. The cookie is not
    re-set: it is the one the sign-in set, unchanged."""
    if not same_origin(request):
        log.info(
            "refused a sign-in resume from another origin (Origin %r, Sec-Fetch-Site %r)",
            request.headers.get("origin"), request.headers.get("sec-fetch-site"),
        )
        raise HTTPException(status_code=403, detail=_RESUME_CROSS_SITE_DETAIL)
    token = admin_auth.cookie_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail=_RESUME_NO_COOKIE_DETAIL)
    window = reload_window_min()
    async with session_scope() as s:
        resume = await admin_auth.resume_session(s, token, window)
    if resume is None:
        raise HTTPException(status_code=401, detail=_RESUME_CLOSED_DETAIL)
    response = JSONResponse({"ok": True, "token": token, **_resume_fields(window, resume)})
    # The answer is a credential: never kept by a cache on the way.
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/logout")
async def auth_logout(request: Request) -> JSONResponse:
    """Revoke the CALLING session (Bearer-only — a cross-site POST with
    just the cookie must not be able to log the admin out)."""
    token = admin_auth.bearer_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="Bearer token required")
    async with session_scope() as s:
        revoked = await admin_auth.revoke_session(s, admin_auth.token_sha256(token))
    response = JSONResponse({"ok": True, "revoked": revoked})
    response.delete_cookie(admin_auth.COOKIE_NAME, path="/")
    return response


@router.get("/sessions")
async def auth_sessions(request: Request) -> dict:
    """Session list for the settings page's revoke UI. GET state — the
    cookie may render it; unauthenticated callers get 401."""
    result = await admin_auth.check_admin_request(request)
    if result not in ("ok", "cookie-only"):
        raise HTTPException(status_code=401, detail="admin session required")
    current = admin_auth.bearer_token(request) or admin_auth.cookie_token(request)
    current_hash = admin_auth.token_sha256(current) if current else None
    async with session_scope() as s:
        sessions = await admin_auth.list_sessions(s)
    for entry in sessions:
        entry["current"] = entry["token_hash"] == current_hash
    return {"sessions": sessions}


@router.delete("/sessions/{token_hash}")
async def auth_revoke_session(token_hash: str, request: Request) -> dict:
    """Revoke by token hash (the list endpoint's identifier). Mutation ⇒
    Bearer-only."""
    result = await admin_auth.check_admin_request(request)
    if result == "cookie-only":
        raise HTTPException(
            status_code=403,
            detail="mutations require Authorization: Bearer",
        )
    if result != "ok":
        raise HTTPException(status_code=401, detail="admin session required")
    async with session_scope() as s:
        revoked = await admin_auth.revoke_session(s, token_hash)
    if not revoked:
        raise HTTPException(status_code=404, detail="no such session")
    return {"ok": True}


@router.post("/password")
async def auth_change_password(
    body: PasswordChangeRequest, request: Request
) -> dict:
    """Change the admin password (old password re-verified, §7.2).
    Mutation ⇒ Bearer-only. Every OTHER session is revoked: a token
    minted under the old password does not survive it — and with it any
    reload window it had (``POST /resume``); the calling session stays
    logged in, and having just proved the password, its own reload window
    starts again."""
    result = await admin_auth.check_admin_request(request)
    if result == "cookie-only":
        raise HTTPException(
            status_code=403,
            detail="mutations require Authorization: Bearer",
        )
    if result != "ok":
        raise HTTPException(status_code=401, detail="admin session required")
    current = admin_auth.bearer_token(request)
    keep = admin_auth.token_sha256(current) if current else None
    async with session_scope() as s:
        password_hash = await admin_auth.get_password_hash(s)
        if password_hash is None or not admin_auth.verify_password(
            password_hash, body.old_password
        ):
            raise HTTPException(status_code=401, detail="wrong password")
        await admin_auth.set_password(s, body.new_password)
        revoked = await admin_auth.revoke_other_sessions(s, keep)
        await admin_auth.mark_signed_in(s, keep)
    return {"ok": True, "revoked_sessions": revoked}


# ─── Household device token (device tier) ─────────────────────────────────


@router.get(
    "/device-token",
    dependencies=[Depends(admin_auth.require_admin_security_read)],
)
async def auth_device_token() -> dict:
    """The household device token for the settings page and for enrolling
    a phone. Same table and same gate as the core's
    ``GET /v1/admin/device-token``: Bearer or cookie renders it, 401
    otherwise, 501 before setup."""
    async with session_scope() as s:
        token = await admin_auth.ensure_device_token_row(s)
    return {"token": token, "header": admin_auth.DEVICE_TOKEN_HEADER}


class DeviceTokenRequest(BaseModel):
    # A PLAIN str, deliberately: a pydantic constraint (min_length, a
    # pattern) reports the rejected value back as `input` in FastAPI's 422
    # body, and a near-miss household token must never travel back out.
    # The rule lives once, in admin_auth.validate_custom_device_token, so
    # this endpoint and the core's cannot drift apart.
    token: str


@router.post(
    "/device-token",
    dependencies=[Depends(admin_auth.require_admin_security)],
)
async def auth_set_device_token(body: DeviceTokenRequest) -> dict:
    """Replace the household device token with one an admin chose
    (Bearer-only, 501 before setup — setting a token IS a rotation).

    At least 12 characters of printable ASCII, stored verbatim with the
    outer whitespace trimmed. The previous token is refused from now on,
    every other device must be given the new one, and the file mirror is
    rewritten."""
    try:
        async with session_scope() as s:
            token = await admin_auth.set_device_token(s, body.token)
    except admin_auth.DeviceTokenRejected as e:
        # The message, never the value.
        raise HTTPException(status_code=400, detail=str(e))
    admin_auth.write_device_token_file(token)
    log.info("device token set by an admin (web)")
    return {"token": token, "header": admin_auth.DEVICE_TOKEN_HEADER, "rotated": True}


@router.post(
    "/device-token/rotate",
    dependencies=[Depends(admin_auth.require_admin_security)],
)
async def auth_rotate_device_token() -> dict:
    """Replace the household device token (Bearer-only, 501 before
    setup). The previous token is refused from now on; the file mirror
    is rewritten."""
    async with session_scope() as s:
        token = await admin_auth.rotate_device_token(s)
    admin_auth.write_device_token_file(token)
    log.info("device token rotated by an admin (web)")
    return {"token": token, "header": admin_auth.DEVICE_TOKEN_HEADER, "rotated": True}
