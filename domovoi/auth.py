"""Admin auth dependency for plugin management (design §7.1/§7.3).

Plugin-management mutations (install / confirm / enable / disable /
uninstall / upgrade) are gated through :func:`require_admin`:

* An ``admin_auth`` row exists (first-run setup completed) ⇒ a valid
  **Bearer** session token is REQUIRED — 401 without one, 403 when only
  the dashboard cookie is attached (cookies render GET state, never
  authorize mutations; §7.3 CSRF stance).
* No ``admin_auth`` row ⇒ **501 "auth not configured"** — the install
  pipeline is code execution (§7.1), so unlike the daily-use surfaces
  (which keep the open pre-setup LAN-trust grace, see
  :mod:`domovoi.admin_auth`), plugin management fails CLOSED until an
  admin password exists. Local development uses the ``domovoi plugin
  dev`` CLI (design §3.8), which never crosses HTTP.

Token validation (sha256 lookup + 30-day sliding expiry) lives in
:mod:`domovoi.admin_auth`, shared with the web process — both validate
against the same ``admin_sessions`` table.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from domovoi.admin_auth import check_admin_request


async def require_admin(request: Request) -> None:
    """FastAPI dependency: allow only authenticated admin sessions.

    501 while auth is unconfigured (no ``admin_auth`` row), 403 on a
    cookie-only mutation attempt, 401 on a missing/invalid/expired
    Bearer token.
    """
    result = await check_admin_request(request)
    if result == "ok":
        return
    if result == "pre-setup":
        raise HTTPException(
            status_code=501,
            detail=(
                "auth not configured — complete the first-run admin setup "
                "before managing plugins over HTTP (local development can "
                "use the `domovoi plugin dev` CLI instead)"
            ),
        )
    if result == "cookie-only":
        raise HTTPException(
            status_code=403,
            detail=(
                "mutations require Authorization: Bearer — the dashboard "
                "cookie only renders GET state"
            ),
        )
    raise HTTPException(status_code=401, detail="admin session required")
