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
256-bit token (sha256-stored). A session ends after an hour with no admin
action (``admin_auth.SESSION_IDLE_TIMEOUT_MIN``: a change made as admin;
polls, reloads and page loads never count) and at 90 days whatever
happens. Every answer here says how long is left (``idle_in_sec``), so the
page drops to "signed out" on time instead of failing. Login/setup also
set the ``SameSite=Strict`` ``HttpOnly`` cookie so plain GET page loads
render authenticated state — mutations everywhere accept ONLY
``Authorization: Bearer`` (the dashboard JS holds the token in memory).
The cookie is a secret of its own, never the Bearer
(:func:`domovoi.admin_auth.issue_session_cookie`): a browser sends a
cookie to every port of its host, so whatever else runs on the box sees
it.

A reload forgets that in-memory token. For ``ADMIN_RELOAD_WINDOW_MIN``
(default 60, never more than the idle hour) minutes after the session's
last admin action, ``POST /resume`` gives the page holding the cookie its
session's token back — owner decision 2026-10-14, "refreshing should not
lose admin on the same machine, same session", counted from the last
action since 2026-11-16. It answers only a same-origin request
(:func:`same_origin`) carrying ``X-Requested-With``, and only for the
cookie's own session inside its window
(:func:`domovoi.admin_auth.resume_session`). ``GET /status`` says whether
this browser can (``resume_in_sec``), so the page asks only when it can.

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

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from domovoi import admin_auth, calendar_feeds
from domovoi.config import settings as core_settings
from domovoi.host_kind import host_kind
from web.backend.api.csrf_guard import require_requested_with
from web.backend.db import session_scope
from web.backend.domovoi_client import get_cached_snapshot

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# The cookie lasts as long as a session ever could (the 90-day cap); the
# session it names usually ends long before, at its idle hour, and the
# status read then clears it (auth_status).
COOKIE_MAX_AGE_SEC = admin_auth.SESSION_MAX_AGE_DAYS * 86400


# The last live window the core's snapshot carried, clamped; None until the
# first one arrives after this process started.
_last_live_window: int | None = None


def reload_window_min() -> int:
    """The live ``ADMIN_RELOAD_WINDOW_MIN``, in minutes (0 = off).

    Edited through the core (Settings → Advanced, tier ``hot``),
    which mutates the CORE's settings; this process's copy is whatever
    ``.env`` said when it booted. So the value comes from the core's
    snapshot (refreshed every 1.5 s), clamped to 0..1440; a value that is
    not a whole number is not a value.

    While the snapshot is missing (the core down, restarting, or slow to
    answer one poll) the answer may only get SMALLER, never wider than what
    an admin last set (review finding 2): the smaller of this process's
    boot-time copy and the last live value seen. An admin who turned it off
    is not turned back on by a core restart.

    Before ANY live value has been seen since this process started it is
    the boot-time copy — read from the same ``.env`` a Settings save
    writes, so current for a process that started after the save. (It used
    to be 0 then, and a server update restarts this process: the "Domovoi
    has been updated, reload" reload, made while the core was still coming
    back, lost the admin sign-in of someone who had just pressed Update.)
    The idle hour bounds it either way: nothing resumes a session that has
    gone an hour without an admin action."""
    global _last_live_window
    live = (get_cached_snapshot() or {}).get("admin_reload_window_min")
    if isinstance(live, int) and not isinstance(live, bool):
        _last_live_window = admin_auth.clamp_reload_window(live)
        return _last_live_window
    own = admin_auth.clamp_reload_window(core_settings.admin_reload_window_min)
    if _last_live_window is None:
        return own
    return min(_last_live_window, own)


def _resume_fields(
    window: int,
    resume: admin_auth.ResumeWindow | None,
    idle: admin_auth.IdleClock | None = None,
) -> dict[str, Any]:
    """What every auth answer says about the session's clocks.

    * the reload window: its length (``reload_window_min``) and — when this
      browser's session is inside it — when it ends (``resume_until``, the
      server's clock) and how many seconds are left (``resume_in_sec``,
      what a browser counts from); ``None`` for both when there is nothing
      to resume;
    * the idle timeout: its length (``idle_timeout_min``, always) and, for
      the session the answer is about, when it signs out with no further
      admin action (``idle_until``) and the seconds left (``idle_in_sec``);
      ``None`` for both when there is no live session."""
    fields: dict[str, Any] = {
        "reload_window_min": window,
        "resume_until": None,
        "resume_in_sec": None,
        "idle_timeout_min": int(admin_auth.SESSION_IDLE_TIMEOUT_MIN),
        "idle_until": None,
        "idle_in_sec": None,
    }
    if resume is not None:
        fields.update(resume.as_json())
    if idle is not None:
        fields.update(idle.as_json())
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
    file the core wrote. The macOS installer's Linux VM (``mac``, a kind
    the Mac installer's host_kind adds) shows it in its Setup window, and
    again from the menu-bar icon's Repair or Uninstall Domovoi... >
    Show Setup Code...; nobody there opens the VM's ``~/.domovoi`` either."""
    kind = host_kind()
    if kind == "mac":
        return (
            "setup code required — use the one the Domovoi Setup window "
            "showed on the Mac; closed it? Click the Domovoi cat in the "
            "menu bar, choose Repair or Uninstall Domovoi..., then Show "
            "Setup Code..."
        )
    if kind == "wsl":
        return (
            "setup code required — use the one on the last page of "
            "Domovoi's Windows installer; a code older than a day, or a "
            "lost one, is replaced by running the installer again"
        )
    if host_kind() == "mac":
        # The Mac installer's VM: nobody on the Mac opens its ~/.domovoi.
        # The Domovoi app reads the code out of it (mac/ InstalledPage).
        return (
            "setup code required — on the Mac, open Domovoi in Applications "
            "and choose Show Setup Code; a code older than a day is "
            "replaced when Domovoi restarts (restarting the Mac does it)"
        )
    return (
        "setup code required — read it from "
        "~/.domovoi/setup-code.txt on the Domovoi server (the "
        "core also prints it when it runs in a terminal); a code "
        "older than a day is replaced by restarting the core"
    )


def _set_session_cookie(response: JSONResponse, cookie: str) -> None:
    """The browser's session cookie: the secret
    :func:`domovoi.admin_auth.issue_session_cookie` minted for it, never the
    Bearer (every port on this host receives it)."""
    response.set_cookie(
        admin_auth.COOKIE_NAME,
        cookie,
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
async def auth_status(request: Request, response: Response) -> dict[str, Any]:
    """Open probe the dashboard uses to decide setup-vs-login-vs-quiet.
    ``authenticated`` reflects the Bearer when one is sent, else the
    cookie (GET state).

    ``resume_in_sec`` / ``resume_until`` are set while THIS browser's
    session cookie is inside its reload window — what tells a reloaded
    page to ask ``POST /resume`` for its sign-in back, and what the
    Settings Admin card says it lasts until.

    ``idle_in_sec`` / ``idle_until``: how long the session the request's
    credential names (the Bearer, else the cookie) has before it signs out
    for want of an admin action. A page holding a Bearer asks with it when
    its own count runs out: still live (another tab of this browser acted)
    and it counts again; over, and ``ended`` says why — ``"idle"`` (an hour
    without an admin action) or ``"ended"`` (signed out, revoked, a
    password change, the 90-day cap) — so it can say so in plain words.

    Read-only: nothing slides. A cookie whose session is over is cleared,
    so the browser stops carrying it."""
    window = reload_window_min()
    resume = None
    idle = None
    ended = None
    bearer = admin_auth.bearer_token(request)
    cookie = admin_auth.cookie_token(request)
    async with session_scope() as s:
        setup_complete = await admin_auth.has_admin_auth(s)
        result = await admin_auth.check_admin_request(request, s)
        if result in ("ok", "cookie-only"):
            resume = await admin_auth.resume_deadline(s, cookie, window)
            idle = await admin_auth.session_idle_clock(s, token=bearer, cookie=cookie)
        if bearer is not None and result != "ok" and setup_complete:
            ended = await admin_auth.session_end_reason(s, bearer)
        cookie_dead = cookie is not None and setup_complete and not (
            await admin_auth.validate_cookie(s, cookie)
        )
    if cookie_dead:
        response.delete_cookie(admin_auth.COOKIE_NAME, path="/")
    return {
        "setup_complete": setup_complete,
        "authenticated": result in ("ok", "cookie-only"),
        "ended": ended,
        **_resume_fields(window, resume, idle),
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
        cookie = await admin_auth.issue_session_cookie(s, token)
        window = reload_window_min()
        resume = await admin_auth.resume_deadline(s, cookie, window)
        idle = await admin_auth.session_idle_clock(s, token=token)
    admin_auth.LOGIN_BACKOFF.record_success(source)
    admin_auth.delete_setup_code()
    admin_auth.write_device_token_file(device_token)
    response = JSONResponse({"ok": True, "token": token, **_resume_fields(window, resume, idle)})
    _set_session_cookie(response, cookie)
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
        cookie = await admin_auth.issue_session_cookie(s, token)
        window = reload_window_min()
        resume = await admin_auth.resume_deadline(s, cookie, window)
        idle = await admin_auth.session_idle_clock(s, token=token)
    admin_auth.LOGIN_BACKOFF.record_success(source)
    response = JSONResponse({"ok": True, "token": token, **_resume_fields(window, resume, idle)})
    _set_session_cookie(response, cookie)
    return response


_RESUME_CROSS_SITE_DETAIL = (
    "a sign-in is only resumed for this dashboard's own page — this request "
    "came from another origin"
)
_RESUME_NO_COOKIE_DETAIL = "no admin sign-in in this browser to resume — sign in"
_RESUME_CLOSED_DETAIL = (
    "this browser's admin sign-in can't be resumed — it ended, or there "
    "has been no admin change in it for longer than the reload window; "
    "sign in again"
)


@router.post("/resume", dependencies=[Depends(require_requested_with)])
async def auth_resume(request: Request) -> JSONResponse:
    """Give a reloaded dashboard page its admin sign-in back (owner
    decision 2026-10-14).

    The page's Bearer lived in its memory and the reload forgot it; the
    HttpOnly session cookie the sign-in set survived. The cookie is not the
    Bearer, but the session row keeps the Bearer wrapped under it
    (:func:`domovoi.admin_auth.resume_session`), so this hands the
    session's own Bearer back — nothing new is minted — when ALL of these
    hold:

    * ``X-Requested-With`` (the dependency, and the process-wide write
      backstop in front of it): a cross-site page cannot send it without
      a preflight this server refuses;
    * the request is same-origin (:func:`same_origin`): not another port
      on this host, not a second dashboard through the server switcher;
    * the cookie names a live session (not idle for an hour, revoked,
      signed out, expired or past the 90-day cap) whose last admin action
      is less than ``ADMIN_RELOAD_WINDOW_MIN`` minutes ago (0 = never).

    403 for the first two, 401 for the rest. Nothing about the session
    changes, on a refusal or a success: a resume is not an admin action,
    so a page that reloads itself on a timer never keeps a session alive.
    The answer says when the window ends and when the session idles out.
    The cookie is not re-set: it is the one the sign-in set, unchanged."""
    if not same_origin(request):
        log.info(
            "refused a sign-in resume from another origin (Origin %r, Sec-Fetch-Site %r)",
            request.headers.get("origin"), request.headers.get("sec-fetch-site"),
        )
        raise HTTPException(status_code=403, detail=_RESUME_CROSS_SITE_DETAIL)
    cookie = admin_auth.cookie_token(request)
    if cookie is None:
        raise HTTPException(status_code=401, detail=_RESUME_NO_COOKIE_DETAIL)
    window = reload_window_min()
    async with session_scope() as s:
        resumed = await admin_auth.resume_session(s, cookie, window)
        idle = (
            await admin_auth.session_idle_clock(s, token=resumed[0]) if resumed else None
        )
    if resumed is None:
        raise HTTPException(status_code=401, detail=_RESUME_CLOSED_DETAIL)
    token, resume = resumed
    response = JSONResponse({"ok": True, "token": token, **_resume_fields(window, resume, idle)})
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
    bearer = admin_auth.bearer_token(request)
    async with session_scope() as s:
        # The Bearer's session, or the one the browser cookie names (the
        # cookie is its own secret, not the token).
        current_hash = (
            admin_auth.token_sha256(bearer) if bearer
            else await admin_auth.cookie_session_hash(s, admin_auth.cookie_token(request))
        )
        sessions = await admin_auth.list_sessions(s)
    for entry in sessions:
        entry["current"] = entry["token_hash"] == current_hash
    return {"sessions": sessions}


@router.delete("/sessions/{token_hash}")
async def auth_revoke_session(token_hash: str, request: Request) -> dict:
    """Revoke by token hash (the list endpoint's identifier). Mutation ⇒
    Bearer-only, and an admin action (the caller's idle hour restarts)."""
    result = await admin_auth.check_admin_request(request, activity=True)
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
    result = await admin_auth.check_admin_request(request, activity=True)
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
        # Every phone's day grant ends with the old password (and the
        # sessions they minted with it); the calling session is kept.
        await admin_auth.revoke_all_phone_grants(s, keep)
        revoked = await admin_auth.revoke_other_sessions(s, keep)
        await admin_auth.mark_signed_in(s, keep)
    return {"ok": True, "revoked_sessions": revoked}


# ─── Phone grants: admin on a phone for the rest of the day (V085) ────────
#
# docs/SECURITY_PRIVACY.md, "Admin on a phone: the day's grant". A phone
# that has just signed in with the password asks for a grant (256 bits,
# bound to its device id, 24 hours), keeps it behind its biometrics, and
# swaps it here for a fresh admin session when it needs a Bearer again.
# Never logged: the grant, the token, the password. The device id and the
# label are not secrets and are logged.

_GRANT_SECRET_MAX = 256

_PHONE_GRANT_DEVICE_DETAIL = (
    f"{admin_auth.DEVICE_TOKEN_HEADER} required — a phone grant is only for "
    "a paired phone"
)
_PHONE_GRANT_STALE_DETAIL = (
    "sign in with the password again first — a phone grant is only issued "
    f"within {admin_auth.PHONE_GRANT_FRESH_SIGNIN_SEC // 60} minutes of a "
    "password sign-in"
)
_PHONE_GRANT_INVALID_DETAIL = "phone grant not valid — sign in with the password"
_PHONE_GRANT_EXPIRED_DETAIL = "phone grant has expired — sign in with the password"
_PHONE_GRANT_PRE_SETUP_DETAIL = (
    "auth not configured — complete the first-run admin setup first"
)


class PhoneGrantRequest(BaseModel):
    device_id: str = Field(
        ...,
        min_length=admin_auth.PHONE_GRANT_DEVICE_ID_MIN,
        max_length=admin_auth.PHONE_GRANT_DEVICE_ID_MAX,
    )
    label: str = Field(..., min_length=1, max_length=admin_auth.PHONE_GRANT_LABEL_MAX)


class PhoneGrantExchangeRequest(BaseModel):
    # PLAIN strs, checked in the handler: a pydantic constraint reports
    # the rejected value back as `input` in FastAPI's 422 body, and a
    # near-miss grant must never travel back out (DeviceTokenRequest's rule).
    grant: str
    device_id: str


class PhoneGrantRevokeRequest(BaseModel):
    grant: str  # plain, for the same reason


def _grant_shape_ok(grant: str) -> bool:
    return 0 < len(grant) <= _GRANT_SECRET_MAX


async def _require_household_token(request: Request) -> None:
    """The phone-grant routes take the household token ITSELF: an admin
    Bearer is not a substitute (the device tier's admin fallback is not
    enough here)."""
    result = await admin_auth.check_device_request(request)
    if result == "ok":
        return
    if result == "throttled":
        raise admin_auth.device_token_throttled_error(request)
    raise HTTPException(status_code=401, detail=_PHONE_GRANT_DEVICE_DETAIL)


def _no_store(payload: dict[str, Any]) -> JSONResponse:
    response = JSONResponse(payload)
    response.headers["Cache-Control"] = "no-store"  # the answer is a credential
    return response


@router.post("/phone-grant")
async def auth_phone_grant(body: PhoneGrantRequest, request: Request) -> JSONResponse:
    """Issue this phone's grant for the rest of the day. Needs the
    household token AND an admin Bearer whose session proved the password
    within the last five minutes (``signed_in_at``): a Bearer that was
    kept alive, resumed, stolen or minted from a grant is refused (403).
    One grant per device id: a new one replaces the old, and ends the
    sessions the old one minted. The grant is in this answer and nowhere
    else."""
    await _require_household_token(request)
    result = await admin_auth.check_admin_request(request, activity=True)
    if result == "pre-setup":
        raise HTTPException(status_code=501, detail=_PHONE_GRANT_PRE_SETUP_DETAIL)
    if result == "cookie-only":
        raise HTTPException(
            status_code=403, detail="mutations require Authorization: Bearer"
        )
    if result != "ok":
        raise HTTPException(status_code=401, detail="admin session required")
    async with session_scope() as s:
        if not await admin_auth.signed_in_recently(s, admin_auth.bearer_token(request)):
            raise HTTPException(status_code=403, detail=_PHONE_GRANT_STALE_DETAIL)
        grant, expires_at = await admin_auth.issue_phone_grant(s, body.device_id, body.label)
    log.info("phone grant issued (device %r, label %r)", body.device_id, body.label)
    return _no_store({"grant": grant, "expires_at": expires_at.isoformat()})


@router.post("/phone-grant/exchange")
async def auth_phone_grant_exchange(
    body: PhoneGrantExchangeRequest, request: Request
) -> JSONResponse:
    """Swap a phone grant for a fresh admin session (``phone: <label>``,
    the usual idle hour, NOT freshly signed in, so it cannot mint a grant).
    Needs the household token, then sits behind the password login's own
    per-source backoff (the attempt is counted before the check; 429 with
    ``Retry-After`` while throttled). 401 when the grant is wrong, belongs
    to another device id, or has expired (the expired row is deleted)."""
    await _require_household_token(request)
    source = admin_auth.enforce_login_backoff(request)
    if not _grant_shape_ok(body.grant) or not (
        admin_auth.PHONE_GRANT_DEVICE_ID_MIN
        <= len(body.device_id)
        <= admin_auth.PHONE_GRANT_DEVICE_ID_MAX
    ):
        admin_auth.LOGIN_BACKOFF.record_failure(source)
        log.info("phone grant exchange refused: malformed request")
        raise HTTPException(status_code=401, detail=_PHONE_GRANT_INVALID_DETAIL)
    async with session_scope() as s:
        if not await admin_auth.has_admin_auth(s):
            admin_auth.LOGIN_BACKOFF.release(source)
            raise HTTPException(status_code=501, detail=_PHONE_GRANT_PRE_SETUP_DETAIL)
        outcome = await admin_auth.exchange_phone_grant(s, body.grant, body.device_id)
        idle = (
            await admin_auth.session_idle_clock(s, token=outcome[0])
            if isinstance(outcome, tuple) else None
        )
    if outcome == "invalid":
        admin_auth.LOGIN_BACKOFF.record_failure(source)
        log.info("phone grant exchange refused: no such grant (device %r)", body.device_id)
        raise HTTPException(status_code=401, detail=_PHONE_GRANT_INVALID_DETAIL)
    if outcome == "expired":
        admin_auth.LOGIN_BACKOFF.record_failure(source)
        log.info("phone grant exchange refused: expired (device %r)", body.device_id)
        raise HTTPException(status_code=401, detail=_PHONE_GRANT_EXPIRED_DETAIL)
    token, grant_expires_at = outcome
    # Hand back this attempt's reservation only. record_success would also
    # clear the address's wrong-password streak, and a good exchange says
    # nothing about the password, so that streak is left as it was.
    admin_auth.LOGIN_BACKOFF.release(source)
    log.info("phone grant exchanged for an admin session (device %r)", body.device_id)
    payload: dict[str, Any] = {
        "token": token,
        "grant_expires_at": grant_expires_at.isoformat(),
        "idle_timeout_min": int(admin_auth.SESSION_IDLE_TIMEOUT_MIN),
        "idle_until": None,
        "idle_in_sec": None,
    }
    if idle is not None:
        payload.update(idle.as_json())
    return _no_store(payload)


@router.post("/phone-grant/revoke")
async def auth_phone_grant_revoke(body: PhoneGrantRevokeRequest) -> dict:
    """The phone's own sign-out: end the grant it holds, and the sessions
    it minted. No admin needed: holding the grant proves it is yours. The
    same answer whether or not it existed."""
    revoked = False
    if _grant_shape_ok(body.grant):
        async with session_scope() as s:
            revoked = await admin_auth.revoke_phone_grant_by_secret(s, body.grant)
    if revoked:
        log.info("phone grant revoked by the phone holding it")
    return {"ok": True}


@router.get("/phone-grants")
async def auth_phone_grants(request: Request) -> dict:
    """The phone grants, for the dashboard list beside the sessions:
    device id, label, issued, until, last used, live. Never a secret or a
    hash. GET state: the cookie may render it."""
    result = await admin_auth.check_admin_request(request)
    if result not in ("ok", "cookie-only"):
        raise HTTPException(status_code=401, detail="admin session required")
    async with session_scope() as s:
        grants = await admin_auth.list_phone_grants(s)
    return {"grants": grants}


@router.delete("/phone-grants/{grant_id}")
async def auth_revoke_phone_grant(grant_id: int, request: Request) -> dict:
    """An admin revokes a phone's grant (and the sessions it minted).
    Bearer-only; an admin action."""
    result = await admin_auth.check_admin_request(request, activity=True)
    if result == "cookie-only":
        raise HTTPException(
            status_code=403, detail="mutations require Authorization: Bearer"
        )
    if result != "ok":
        raise HTTPException(status_code=401, detail="admin session required")
    async with session_scope() as s:
        revoked = await admin_auth.revoke_phone_grant(s, grant_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="no such phone grant")
    log.info("phone grant %d revoked by an admin", grant_id)
    return {"ok": True}


# ─── Household device token (device tier) ─────────────────────────────────


@router.get(
    "/device-token",
    dependencies=[Depends(admin_auth.require_admin_security_read)],
)
async def auth_device_token() -> dict:
    """The household device token for the settings page and for enrolling
    a phone. Same table and same gate as the core's
    ``GET /v1/admin/device-token``: Bearer-only (the cookie alone is 403 —
    it reaches every port of this host), 401 otherwise, 501 before setup."""
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
    await calendar_feeds.after_device_token_change(session_scope)
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
    await calendar_feeds.after_device_token_change(session_scope)
    log.info("device token rotated by an admin (web)")
    return {"token": token, "header": admin_auth.DEVICE_TOKEN_HEADER, "rotated": True}
