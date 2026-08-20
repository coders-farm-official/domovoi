"""The allowlist and the loopback forwarder.

This module is the reason the plugin is not an open proxy. Every remote
request passes :func:`resolve_route` before any local socket exists, and
``resolve_route`` is a pure function so the entire access-control surface
can be tested without a network.

Two properties are worth stating outright, because they are what make
remote access defensible at all:

**The plugin holds no Domovoi credential.** It never adds an
``Authorization`` header, never keeps an admin session, and cannot
authenticate itself to the dashboard or the core. A remote user logs into
Domovoi *through* the tunnel exactly as they would on the LAN, and their
credentials are inside the sealed layer the whole way. Compromising this
plugin's process yields a pipe, not an account.

**The caller's IP is not forwarded.** Domovoi rate-limits some endpoints
per source IP. Forwarding a remote client's address would let a device
with a rotating IP walk around those limits and would poison the core's
own view of who is talking to it. Local requests therefore arrive from
loopback and are tagged ``X-Domovoi-Remote`` so the dashboard can tell
they came from outside.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal
from urllib.parse import unquote, urlsplit

from . import framing

Origin = Literal["dashboard", "core"]

# Headers that describe one hop of a connection and must never be
# relayed onto the next one.
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade",
    "content-length", "host",
})

# Headers we refuse to accept FROM a remote client, because the local
# services would read them as statements about network position.
CLIENT_HEADER_DENYLIST = frozenset({
    "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
    "x-real-ip", "forwarded", "x-domovoi-remote",
})

REMOTE_MARKER_HEADER = "X-Domovoi-Remote"
REMOTE_MARKER_VALUE = "ltl"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_KNOWN_METHODS = frozenset({
    "GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE",
})


class StreamLimiter:
    """A counting limit that can be asked whether it has room.

    ``asyncio.Semaphore`` would queue an over-limit caller until a slot
    freed up, which is the wrong answer here: a remote client that has
    saturated the household deserves an immediate, explicit
    ``TOO_MANY_STREAMS`` rather than a request that appears to hang. This
    refuses instead of waiting, and does it without reading private
    attributes off a stdlib object.
    """

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def has_room(self) -> bool:
        return self._in_flight < self.limit

    def __enter__(self) -> "StreamLimiter":
        if not self.has_room:
            raise RuntimeError("stream limit exceeded")
        self._in_flight += 1
        return self

    def __exit__(self, *exc: object) -> None:
        self._in_flight = max(0, self._in_flight - 1)


@dataclass(frozen=True)
class Route:
    """One allowlist entry."""

    prefix: str
    origin: Origin
    websocket: bool = False
    #: Requires ``allow_core_admin``; these prefixes reach the core API,
    #: which includes Domovoi's admin tier.
    core_api: bool = False
    #: Requires ``allow_media_streaming`` and is always streamed rather
    #: than buffered.
    media: bool = False


# Order matters: the first matching prefix wins, so more specific
# prefixes are listed before the general ones they sit inside.
ALLOWLIST: tuple[Route, ...] = (
    Route("/ws/state", "dashboard", websocket=True),
    Route("/api/", "dashboard"),
    Route("/media/", "dashboard", media=True),
    Route("/plugins/", "dashboard"),
    Route("/static/", "dashboard"),
    Route("/assets/", "dashboard"),
    Route("/v1/stream/", "core", websocket=True, core_api=True),
    Route("/v1/", "core", core_api=True),
)


@dataclass(frozen=True)
class Allowed:
    route: Route
    origin_url: str
    path: str            # path + query, exactly as it will be requested


@dataclass(frozen=True)
class Denied:
    code: str
    message: str


Decision = Allowed | Denied


class OriginError(ValueError):
    """A configured local origin that is not safe to forward to."""


def validate_origin(url: str, *, label: str) -> str:
    """Accept only an ``http(s)://`` origin on a loopback or private
    address.

    A public address here would mean the household's Domovoi server had
    been turned into a relay onto the wider internet by a settings typo.
    Refusing at registration time makes that unreachable rather than
    merely unlikely.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise OriginError(f"{label} must be an http:// or https:// URL")
    if not parts.hostname:
        raise OriginError(f"{label} has no host")
    if parts.path.rstrip("/") or parts.query or parts.fragment:
        raise OriginError(f"{label} must be a bare origin with no path")
    host = parts.hostname
    if host == "localhost":
        return f"{parts.scheme}://{parts.netloc}"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as e:
        raise OriginError(
            f"{label} must be an IP address or localhost, not a hostname "
            "(a name can be repointed without changing this setting)"
        ) from e
    if not (address.is_loopback or address.is_private):
        raise OriginError(f"{label} must be a loopback or private address")
    return f"{parts.scheme}://{parts.netloc}"


_ENCODED_DOT = re.compile(r"%2e", re.IGNORECASE)


def _path_is_safe(path: str) -> bool:
    """Reject anything that could escape the prefix it matched.

    The check runs on a percent-decoded copy so ``%2e%2e%2f`` is caught,
    but the *original* string is what gets forwarded — decoding and then
    re-forwarding would change the request the client actually made.
    """
    if not path.startswith("/") or path.startswith("//"):
        return False
    if "\\" in path or "\x00" in path:
        return False
    decoded = unquote(_ENCODED_DOT.sub(".", path))
    if "\\" in decoded or "\x00" in decoded:
        return False
    segments = decoded.split("?", 1)[0].split("/")
    return ".." not in segments


def resolve_route(
    method: str,
    raw_path: str,
    settings: Any,
) -> Decision:
    """Decide whether a remote request may touch this house.

    Pure: no sockets, no database, no clock. Everything it consults is an
    argument, which is what lets the test suite enumerate the refusal
    cases exhaustively.
    """
    method = (method or "").upper()
    if method not in _KNOWN_METHODS:
        return Denied(framing.ERR_PROTOCOL, f"unsupported method {method!r}")

    if not isinstance(raw_path, str) or not raw_path:
        return Denied(framing.ERR_PROTOCOL, "missing path")
    if not _path_is_safe(raw_path):
        return Denied(framing.ERR_PATH_NOT_ALLOWED, "path is not addressable")

    bare_path = raw_path.split("?", 1)[0]
    route = next((r for r in ALLOWLIST if bare_path.startswith(r.prefix)), None)
    if route is None:
        return Denied(
            framing.ERR_PATH_NOT_ALLOWED,
            "this path is not exposed to remote access",
        )

    if getattr(settings, "read_only", False) and method not in _SAFE_METHODS:
        return Denied(
            framing.ERR_PATH_NOT_ALLOWED,
            "remote access is configured read-only on this server",
        )
    if route.core_api and not getattr(settings, "allow_core_admin", True):
        return Denied(
            framing.ERR_PATH_NOT_ALLOWED,
            "remote access to the core API is turned off on this server",
        )
    if route.media and not getattr(settings, "allow_media_streaming", True):
        return Denied(
            framing.ERR_PATH_NOT_ALLOWED,
            "remote media streaming is turned off on this server",
        )

    origin_url = (
        settings.core_origin if route.origin == "core" else settings.dashboard_origin
    )
    return Allowed(route=route, origin_url=origin_url.rstrip("/"), path=raw_path)


def sanitize_request_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip hop-by-hop headers and anything that asserts a network
    position, then tag the request as having arrived from outside."""
    out = {
        k: v
        for k, v in (headers or {}).items()
        if k.lower() not in HOP_BY_HOP and k.lower() not in CLIENT_HEADER_DENYLIST
    }
    out[REMOTE_MARKER_HEADER] = REMOTE_MARKER_VALUE
    return out


def sanitize_response_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip hop-by-hop headers from what goes back.

    ``Set-Cookie`` passes through unchanged but with any ``Domain``
    attribute removed: a Domovoi session cookie must stay scoped to
    whatever origin the client is actually talking to, and a ``Domain``
    written for a LAN hostname would either be dropped by the browser or,
    worse, widen the cookie's scope on the client side.
    """
    out: dict[str, str] = {}
    for key, value in (headers or {}).items():
        lowered = key.lower()
        if lowered in HOP_BY_HOP:
            continue
        if lowered == "set-cookie":
            value = re.sub(r";\s*Domain=[^;]*", "", value, flags=re.IGNORECASE)
        out[key] = value
    return out


class LocalProxy:
    """Forwards an allowed request to a loopback origin.

    Responses are streamed, never buffered. A remote client asking for a
    two-hour audiobook must not become a two-hour allocation on the
    household's server, so the body is read in
    :data:`framing.CHUNK_BYTES` pieces and sealed piece by piece.
    """

    def __init__(self, settings: Any, *, log: Any) -> None:
        self._settings = settings
        self._log = log

    # httpx is imported lazily: it is a declared plugin requirement, and
    # keeping it out of module import means the pure allowlist above can
    # be imported and tested with nothing installed.
    def _client(self, timeout: float):
        import httpx

        return httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            follow_redirects=False,     # a redirect is the client's decision
            trust_env=False,            # never route loopback through a proxy
        )

    async def stream_response(
        self,
        allowed: Allowed,
        method: str,
        headers: dict[str, str],
        body: bytes,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Yield ``("head", (status, headers))`` then ``("chunk", bytes)``
        repeatedly, then ``("end", None)``.

        Errors are yielded as ``("error", Denied)`` rather than raised, so
        the caller has one code path that turns everything into a sealed
        frame for the client.
        """
        url = allowed.origin_url + allowed.path
        timeout = float(getattr(self._settings, "request_timeout_sec", 30.0))
        try:
            async with self._client(timeout) as client:
                async with client.stream(
                    method,
                    url,
                    headers=sanitize_request_headers(headers),
                    content=body or None,
                ) as response:
                    yield (
                        "head",
                        (
                            response.status_code,
                            sanitize_response_headers(dict(response.headers)),
                        ),
                    )
                    async for chunk in response.aiter_bytes(framing.CHUNK_BYTES):
                        if chunk:
                            yield ("chunk", chunk)
                    yield ("end", None)
        except Exception as e:  # noqa: BLE001 — every local failure reads the same to a client
            self._log.warning(
                "ltl_remote: local request %s %s failed: %s", method, allowed.path, e
            )
            yield (
                "error",
                Denied(
                    framing.ERR_LOCAL_UNREACHABLE,
                    "the Domovoi service did not answer",
                ),
            )

    def websocket_url(self, allowed: Allowed) -> str:
        """Loopback ``ws(s)://`` URL for a tunneled socket."""
        return re.sub(r"^http", "ws", allowed.origin_url, count=1) + allowed.path

    def connect_websocket(self, allowed: Allowed, headers: dict[str, str]):
        """Open the local WebSocket. Returns the ``websockets`` client
        context manager; the caller owns the pumping loop.

        This is the seam phone voice will use: core's
        ``/v1/stream/{room_id}`` is just another allowlisted WebSocket, so
        when core ships remote voice capture, it works through the tunnel
        with no change here.
        """
        import websockets

        return websockets.connect(
            self.websocket_url(allowed),
            additional_headers=sanitize_request_headers(headers),
            open_timeout=10,
            ping_interval=20,
            max_size=framing.MAX_INNER_BYTES,
        )
