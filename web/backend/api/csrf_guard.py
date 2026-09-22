"""The preflight-forcing header check (WEB-6).

A ``multipart/form-data`` POST — and any POST with no body at all — is a
CORS **simple request**: a page on some other origin can submit one at the
dashboard without the browser ever asking this server for permission
first. The response stays unreadable to that page, but the side effect
lands. Requiring a header the fetch spec does not allow on a simple
request turns every such call into a preflighted one, which the browser
will only send after this server has said yes.

``X-Requested-With`` is the header, and it is what the dashboard
(``web/static/data.js``) and the Android app (``net/ApiClient.kt``) put on
every API call. This dependency is the per-route form for the routes that
would otherwise be simple requests (the multipart uploads); a
process-wide middleware over ``POST /api/*`` is the broader backstop.

The check is deliberately presence-only: the value is never a secret and
never proves anything on its own. What it proves is that a browser was
willing to preflight the call.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

REQUESTED_WITH_HEADER = "X-Requested-With"

_DETAIL = (
    f"{REQUESTED_WITH_HEADER} required — this endpoint only accepts requests "
    f"a browser has preflighted (the dashboard and the app send it)"
)


async def require_requested_with(request: Request) -> None:
    """Dependency: 403 unless the caller sent ``X-Requested-With``."""
    if not (request.headers.get(REQUESTED_WITH_HEADER.lower()) or "").strip():
        raise HTTPException(status_code=403, detail=_DETAIL)
