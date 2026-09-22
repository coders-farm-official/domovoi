"""Process-wide ASGI middleware for the web app (web/backend/main.py).

Plain ASGI classes rather than ``BaseHTTPMiddleware`` subclasses: these sit
in front of streamed responses (audio ranges, SSE chat, zip downloads) and
``BaseHTTPMiddleware`` buffers those through an anyio stream. Each one
passes non-``http`` scopes (``websocket``, ``lifespan``) straight through.

``BodyLimitMiddleware`` meters request bodies, ``RequireRequestedWithMiddleware``
is the preflight backstop in front of every write, and
``SecurityHeadersMiddleware`` stamps the response headers on everything this
process serves.

Registration order in ``main.py`` decides nesting: ``add_middleware`` puts
each new one at the FRONT of ``user_middleware``, and the front is the
outermost wrapper. So the last one registered sees a request first.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from starlette.responses import JSONResponse

from web.backend.api.csrf_guard import REQUESTED_WITH_HEADER

log = logging.getLogger(__name__)

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]

MB = 1024 * 1024

# Per-route request-body budgets, longest prefix first. A route absent from
# this table has no cap — most uploads here are the household's own media
# (a library import, a Documents zip) and a number pulled out of the air
# would break them.
#
# The budget is the whole REQUEST, so a multipart upload's parts, boundaries
# and form fields all count against it; each entry leaves headroom over the
# largest file its handler will accept.
BODY_LIMITS: tuple[tuple[str, int], ...] = (
    # A Piper voice: the .onnx (voices.py caps the model itself at 200 MB)
    # plus its .onnx.json and two form fields.
    ("/api/voices/piper", 210 * MB),
    # A plugin zip on its way to the core, which is what actually decides
    # whether this caller may install anything (api/plugins.py keeps the
    # same ceiling for a body that arrives without a Content-Length).
    ("/api/plugins/install", 66 * MB),
)


def body_limit_for(path: str) -> int | None:
    for prefix, limit in BODY_LIMITS:
        if path == prefix or path.startswith(prefix + "/"):
            return limit
    return None


class _BodyTooLarge(Exception):
    """Raised by the counting ``receive`` when a body outgrows its budget."""


class BodyLimitMiddleware:
    """Refuse an oversized request body BEFORE the app buffers it.

    Two checks, because a client may or may not announce the size:

    * a declared ``Content-Length`` over the budget is refused on the spot,
      with not one byte of the body read;
    * otherwise the body is metered as it arrives and the request is
      refused the moment the running total passes the budget — which is
      well before the multipart parser (or an upload handler) has the whole
      thing in hand.

    Either way the answer is ``413`` and the endpoint never runs, so no
    partial file is written and no row is created.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = body_limit_for(scope.get("path", ""))
        if limit is None:
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        if declared is not None and declared > limit:
            await _too_large(limit, scope, receive, send)
            return

        seen = 0
        started = False

        async def counting_receive() -> dict[str, Any]:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    # The app is mid-read. Raising unwinds it before the
                    # body is complete; __call__ turns that into the 413
                    # as long as nothing has been sent yet.
                    raise _BodyTooLarge()
            return message

        async def watching_send(message: dict[str, Any]) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, watching_send)
        except _BodyTooLarge:
            if started:
                # A response was already on the wire (a handler that
                # answered without reading the body). Nothing to add.
                return
            await _too_large(limit, scope, receive, send)


class RequireRequestedWithMiddleware:
    """Every write under ``/api/`` must carry ``X-Requested-With``.

    A browser sends a form post, a multipart upload or a body-less POST
    cross-origin without asking the server first — they are "simple
    requests" in fetch terms, so the side effect lands before the server
    has a say, and the SameSite cookie is beside the point on a route that
    needs no credential at all. A header outside the simple-request set
    forces the browser to preflight instead, and the preflight is a request
    this server can refuse.

    So: no ``X-Requested-With`` on a POST / PUT / PATCH / DELETE under
    ``/api/`` and the answer is 403, from here, before routing — the
    endpoint never runs and nothing is written. The dashboard
    (``web/static/data.js``, ``auth.js``) and the Android app
    (``net/ApiClient.kt``) put it on every call they make.

    GET and HEAD are untouched (they change nothing), and so is the CORS
    preflight itself: refusing the OPTIONS would tell the browser nothing
    and break the very negotiation this relies on.

    This is a BACKSTOP, not the gate. It answers "could a page on another
    origin have caused this", not "may this caller do it" — that is what
    the auth tiers are for, and every route worth gating still wears one.
    """

    # Same header as the per-route dependency in api/csrf_guard.py, which
    # stays on the multipart uploads: one spelling, two places to fail.
    HEADER = REQUESTED_WITH_HEADER.lower()
    GUARDED_PREFIX = "/api/"
    GUARDED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._needs_header(scope):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "X-Requested-With header required on writes — see "
                    "docs/API_REFERENCE.md"
                )
            },
        )
        log.info(
            "refused %s %s without X-Requested-With",
            scope.get("method"), scope.get("path"),
        )
        await response(scope, receive, send)

    def _needs_header(self, scope: Scope) -> bool:
        if scope.get("method") not in self.GUARDED_METHODS:
            return False
        if not scope.get("path", "").startswith(self.GUARDED_PREFIX):
            return False
        wanted = self.HEADER.encode()
        return not any(
            key == wanted and value.strip() for key, value in scope.get("headers", ())
        )


# ─── Response headers (WEB-8) ─────────────────────────────────────────────

# The dashboard compiles its own JSX in the browser (@babel/standalone) and
# runs a plugin's page code through `new Function`, so 'unsafe-eval' and
# 'unsafe-inline' are the price of the zero-build bundle — see
# web/static/index.html. What is worth having anyway is the rest: nothing
# may frame this page, no plugin or object embeds, no <base> rewrite of
# every relative URL on it.
#
# Two directives stay wide on purpose:
#   * connect-src — the server switcher points the dashboard at ANOTHER
#     Domovoi on the LAN (data.js API_BASE), and a CSP cannot express "any
#     RFC 1918 host" the way the CORS regex can;
#   * img-src / media-src — plugin pages render artwork from whatever
#     service they front (a Jellyfin poster, a RomM box art).
CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
    "style-src 'self' 'unsafe-inline'",
    "img-src * data: blob:",
    "media-src * data: blob:",
    "font-src 'self' data:",
    "connect-src * ws: wss: data: blob:",
    "worker-src 'self' blob:",
    "manifest-src 'self'",
    "frame-src 'self' http: https:",
    "form-action 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
))

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CSP.encode()),
    # For anything that predates frame-ancestors.
    (b"x-frame-options", b"DENY"),
    # A stored file served from /api/files/raw is data, never a page.
    (b"x-content-type-options", b"nosniff"),
    # Media reads may carry the household token in the query
    # (require_device_read's ?device_token=), so send no referrer anywhere.
    (b"referrer-policy", b"no-referrer"),
)


class SecurityHeadersMiddleware:
    """Stamp the response headers on everything this process serves — the
    dashboard shell, the static bundle, plugin assets, API JSON and the
    refusals from the middleware above it.

    Registered last in main.py, which makes it outermost (Starlette wraps
    ``user_middleware[0]``, the most recently added, around everything
    else), so a CORS preflight and a 403 from the write backstop carry the
    headers too. An endpoint that sets one of these itself keeps its own
    value.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {k.lower() for k, _ in headers}
                for key, value in SECURITY_HEADERS:
                    if key not in present:
                        headers.append((key, value))
            await send(message)

        await self.app(scope, receive, send_with_headers)


def cors_origin_regex(port: int) -> str:
    """Origins allowed to make credentialed cross-origin calls: a LAN host
    (or localhost, or an mDNS name) **on this process's own port**.

    The port matters. Every other service on the same machine — a media
    server, a printer's web UI, anything a household runs — is a different
    origin but the *same site*, so a page served by one of them sat inside
    the old any-port regex and could read this API with the dashboard's
    cookie attached. The only cross-origin caller that legitimately exists
    is another Domovoi dashboard reached through the server switcher, and
    that one answers on this same port.
    """
    return (
        r"^https?://("
        r"localhost"
        r"|127\.0\.0\.1"
        r"|\[::1\]"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
        r"|[\w-]+\.local"
        r"):" + str(int(port)) + r"$"
    )


def _content_length(scope: Scope) -> int | None:
    for key, value in scope.get("headers", ()):
        if key == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _too_large(limit: int, scope: Scope, receive: Receive, send: Send) -> None:
    log.info("refused an oversized body on %s (budget %d bytes)", scope.get("path"), limit)
    response = JSONResponse(
        status_code=413,
        content={"detail": f"request body too large (limit {limit} bytes)"},
    )
    await response(scope, receive, send)
