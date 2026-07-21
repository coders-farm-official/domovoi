"""Admin auth endpoints (design §7.2/§7.3, v1 scope amendment).

The web process hosts the auth surface; the core process only
*validates* tokens (both against the same ``admin_auth`` /
``admin_sessions`` tables via :mod:`domovoi.admin_auth`).

Flow: the core boot wrote an 8-word setup code to
``~/.domovoi/setup-code.txt`` + its console. ``POST /setup`` requires
that code (proof of possession of the server — 403 without it, even
though no admin row exists yet), stores the argon2id hash, deletes the
code file, and returns a first session token. ``POST /login`` verifies
the password behind the per-source exponential backoff and mints a
256-bit token (sha256-stored, 30-day sliding expiry). Login/setup also
set the ``SameSite=Strict`` ``HttpOnly`` cookie so plain GET page loads
render authenticated state — mutations everywhere accept ONLY
``Authorization: Bearer`` (the dashboard JS holds the token in memory).

v1 runs over plain LAN HTTP (TLS is on the documented hardening
backlog), so the cookie is NOT marked ``Secure``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from domovoi import admin_auth
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_MAX_AGE_SEC = admin_auth.SESSION_TTL_DAYS * 86400


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
async def auth_status(request: Request) -> dict[str, bool]:
    """Open probe the dashboard uses to decide setup-vs-login-vs-quiet.
    ``authenticated`` reflects the cookie OR a Bearer (GET state)."""
    async with session_scope() as s:
        setup_complete = await admin_auth.has_admin_auth(s)
        result = await admin_auth.check_admin_request(request, s)
    return {
        "setup_complete": setup_complete,
        "authenticated": result in ("ok", "cookie-only"),
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
            # A bad/missing code counts toward the source's backoff —
            # the code is guessable-in-theory (64-bit) but never for free.
            admin_auth.LOGIN_BACKOFF.record_failure(source)
            raise HTTPException(
                status_code=403,
                detail=(
                    "setup code required — read it from the Domovoi server "
                    "console or ~/.domovoi/setup-code.txt"
                ),
            )
        await admin_auth.set_password(s, body.password)
        token = await admin_auth.create_session(s, label="setup")
    admin_auth.LOGIN_BACKOFF.record_success(source)
    admin_auth.delete_setup_code()
    response = JSONResponse({"ok": True, "token": token})
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
            admin_auth.LOGIN_BACKOFF.record_failure(source)
            raise HTTPException(status_code=401, detail="wrong password")
        token = await admin_auth.create_session(s, label=body.label)
    admin_auth.LOGIN_BACKOFF.record_success(source)
    response = JSONResponse({"ok": True, "token": token})
    _set_session_cookie(response, token)
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
    Mutation ⇒ Bearer-only."""
    result = await admin_auth.check_admin_request(request)
    if result == "cookie-only":
        raise HTTPException(
            status_code=403,
            detail="mutations require Authorization: Bearer",
        )
    if result != "ok":
        raise HTTPException(status_code=401, detail="admin session required")
    async with session_scope() as s:
        password_hash = await admin_auth.get_password_hash(s)
        if password_hash is None or not admin_auth.verify_password(
            password_hash, body.old_password
        ):
            raise HTTPException(status_code=401, detail="wrong password")
        await admin_auth.set_password(s, body.new_password)
    return {"ok": True}
