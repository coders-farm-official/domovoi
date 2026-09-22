"""Outbound URL safety — the one gate every server-side fetch of a
caller-chosen URL goes through (design §7.3, outbound-fetch tier).

Several features make the server fetch a URL somebody else picked: a
podcast feed and its enclosures, a news feed, a radio station's stream
(the browser proxy and the ffmpeg sampler), an Ollama registry for a
model pull. Each of those used to carry its own partial check, or none.
This module is the single answer for all of them:

* :func:`check_outbound_url` / :func:`is_safe_outbound_url` — scheme
  allowlist (``http`` / ``https`` only), every hostname resolved, and the
  URL refused when ANY address it resolves to is loopback, link-local,
  RFC 1918 private, CGNAT (100.64/10), IPv6 ULA, multicast, reserved or
  unspecified. IPv4-mapped / 6to4 / NAT64 IPv6 addresses are unwrapped
  and judged by the IPv4 they carry. Shorthand literals (``127.1``,
  ``0x7f000001``, ``2130706433``) are parsed the way a resolver would,
  so they are judged as the address they denote.
* :func:`open_stream` / :func:`fetch_bytes` (+ sync twins) — httpx
  fetchers that never follow a redirect themselves: each hop's target is
  run through the same check first, at most :data:`MAX_REDIRECTS` hops,
  and a byte cap is enforced while reading.

Resolution happens in :func:`resolve_host`, a module-level function so
tests patch it instead of touching DNS. A name that does not resolve is
refused for a fetch — nothing can be said about where it would go — but
allowed by ``require_resolution=False``, which the endpoints that merely
STORE a URL pass: a household offline (or a feed whose DNS is down) must
still be able to save a subscription, and the fetch itself re-checks with
resolution before a byte moves.

Importable by the web process (this module is re-exported through
:mod:`domovoi.webkit` for plugin web modules and :mod:`domovoi.sdk` for
plugin core modules) — so it depends on stdlib + httpx only.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit

log = logging.getLogger(__name__)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
MAX_REDIRECTS = 5

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# Address space the server must never fetch from, whatever a hostname
# resolves to. Kept explicit (rather than ``ip.is_private``) so the rule is
# readable and stable across Python releases; the documentation ranges
# (TEST-NET-1/2/3, 2001:db8::/32) are deliberately NOT here — they are
# unroutable placeholders, and tests use them as "some public address".
_BLOCKED_V4: tuple[ipaddress.IPv4Network, ...] = tuple(
    ipaddress.IPv4Network(n)
    for n in (
        "0.0.0.0/8",        # "this" network / unspecified
        "10.0.0.0/8",       # RFC 1918
        "100.64.0.0/10",    # CGNAT shared address space
        "127.0.0.0/8",      # loopback
        "169.254.0.0/16",   # link-local (incl. cloud metadata endpoints)
        "172.16.0.0/12",    # RFC 1918
        "192.0.0.0/24",     # IETF protocol assignments
        "192.168.0.0/16",   # RFC 1918
        "198.18.0.0/15",    # benchmarking
        "224.0.0.0/4",      # multicast
        "240.0.0.0/4",      # reserved + limited broadcast
    )
)
_BLOCKED_V6: tuple[ipaddress.IPv6Network, ...] = tuple(
    ipaddress.IPv6Network(n)
    for n in (
        "::/128",           # unspecified
        "::1/128",          # loopback
        "::/96",            # IPv4-compatible (deprecated)
        "fc00::/7",         # unique local
        "fe80::/10",        # link-local
        "ff00::/8",         # multicast
    )
)
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")


# ─── Errors ───────────────────────────────────────────────────────────────


class OutboundFetchError(ValueError):
    """Base for everything the safe fetchers refuse to do."""


class UnsafeOutboundURL(OutboundFetchError):
    """The URL (or a redirect target) fails :func:`check_outbound_url`."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{reason}: {url}")
        self.url = url
        self.reason = reason


class TooManyRedirects(OutboundFetchError):
    """More than :data:`MAX_REDIRECTS` hops, or a redirect without a target."""


class ResponseTooLarge(OutboundFetchError):
    """The body exceeded the caller's byte cap."""

    def __init__(self, url: str, max_bytes: int) -> None:
        super().__init__(f"response exceeds {max_bytes} bytes: {url}")
        self.url = url
        self.max_bytes = max_bytes


# ─── Address classification ───────────────────────────────────────────────


def is_public_address(address: IPAddress | str) -> bool:
    """True when ``address`` is somewhere the server may fetch from.
    IPv6 addresses that embed an IPv4 (mapped, 6to4, NAT64) are judged by
    the IPv4 they carry."""
    ip = ipaddress.ip_address(address) if isinstance(address, str) else address
    if ip.version == 6:
        embedded = ip.ipv4_mapped
        if embedded is None and ip in _NAT64:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is None:
            embedded = ip.sixtofour
        if embedded is not None:
            return is_public_address(embedded)
        return not any(ip in net for net in _BLOCKED_V6)
    return not any(ip in net for net in _BLOCKED_V4)


def parse_ip_literal(host: str) -> IPAddress | None:
    """The address a host string denotes when it is an IP literal — in the
    canonical dotted / colon form OR any shorthand ``inet_aton`` accepts
    (``127.1``, ``0x7f000001``, ``0177.0.0.1``, ``2130706433``). None when
    it is a name."""
    host = host.strip()
    if not host:
        return None
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        packed = socket.inet_aton(host)
    except OSError:
        return None
    return ipaddress.IPv4Address(packed)


def resolve_host(host: str) -> list[IPAddress]:
    """Every address ``host`` resolves to (A + AAAA), deduplicated. Empty
    when it does not resolve. Module-level so tests substitute a fake."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    out: list[IPAddress] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if ip not in out:
            out.append(ip)
    return out


def _addresses_for(host: str) -> list[IPAddress]:
    literal = parse_ip_literal(host)
    if literal is not None:
        return [literal]
    return resolve_host(host)


# ─── The check ────────────────────────────────────────────────────────────


def check_outbound_url(url: str, *, require_resolution: bool = True) -> str | None:
    """None when ``url`` may be fetched; otherwise a short reason.

    Refuses: a scheme other than http/https, a missing or malformed host,
    ``localhost`` (and ``*.localhost``), any address — literal or
    resolved — outside the public space (see :func:`is_public_address`),
    and, unless ``require_resolution`` is False, a name that does not
    resolve at all."""
    if not isinstance(url, str) or not url.strip():
        return "empty url"
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return "malformed url"
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        return f"scheme {scheme or '(none)'!r} is not http(s)"
    try:
        host = parts.hostname
        parts.port  # noqa: B018 — raises on a non-numeric / out-of-range port
    except ValueError:
        return "malformed host or port"
    if not host:
        return "missing host"
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return "host is localhost"
    addresses = _addresses_for(host)
    if not addresses:
        if require_resolution:
            return f"host {host!r} does not resolve"
        return None
    for ip in addresses:
        if not is_public_address(ip):
            return f"host {host!r} resolves to a non-public address"
    return None


def is_safe_outbound_url(url: str, *, require_resolution: bool = True) -> bool:
    """The boolean form of :func:`check_outbound_url`."""
    return check_outbound_url(url, require_resolution=require_resolution) is None


async def acheck_outbound_url(
    url: str, *, require_resolution: bool = True
) -> str | None:
    """:func:`check_outbound_url` off the event loop (resolution blocks)."""
    return await asyncio.to_thread(
        check_outbound_url, url, require_resolution=require_resolution
    )


def require_safe_outbound_url(url: str, *, require_resolution: bool = True) -> None:
    """Raise :class:`UnsafeOutboundURL` unless the check passes."""
    reason = check_outbound_url(url, require_resolution=require_resolution)
    if reason is not None:
        raise UnsafeOutboundURL(url, reason)


async def arequire_safe_outbound_url(
    url: str, *, require_resolution: bool = True
) -> None:
    reason = await acheck_outbound_url(url, require_resolution=require_resolution)
    if reason is not None:
        raise UnsafeOutboundURL(url, reason)


# ─── Fetchers: redirects re-checked per hop, bodies capped ────────────────


def _redirect_target(response: Any) -> str | None:
    """The absolute URL a redirect response points at, or None when the
    response is not a redirect."""
    if response.status_code not in _REDIRECT_STATUSES:
        return None
    location = response.headers.get("location")
    if not location:
        raise TooManyRedirects(f"redirect without a Location header: {response.url}")
    return urljoin(str(response.url), location)


async def open_stream(
    client: Any,
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    max_redirects: int = MAX_REDIRECTS,
) -> Any:
    """Open ``url`` on an ``httpx.AsyncClient`` and return the streaming
    response, having checked the URL and every redirect target it led
    through. The caller owns the response (``await response.aclose()``).
    Raises :class:`UnsafeOutboundURL` / :class:`TooManyRedirects`; network
    errors propagate as httpx raises them."""
    current = url
    for _hop in range(max_redirects + 1):
        await arequire_safe_outbound_url(current)
        request = client.build_request(method, current, headers=headers)
        response = await client.send(request, stream=True, follow_redirects=False)
        try:
            target = _redirect_target(response)
        except TooManyRedirects:
            await response.aclose()
            raise
        if target is None:
            return response
        await response.aclose()
        current = target
    raise TooManyRedirects(f"more than {max_redirects} redirects: {url}")


def open_stream_sync(
    client: Any,
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    max_redirects: int = MAX_REDIRECTS,
) -> Any:
    """Blocking twin of :func:`open_stream` for an ``httpx.Client``."""
    current = url
    for _hop in range(max_redirects + 1):
        require_safe_outbound_url(current)
        request = client.build_request(method, current, headers=headers)
        response = client.send(request, stream=True, follow_redirects=False)
        try:
            target = _redirect_target(response)
        except TooManyRedirects:
            response.close()
            raise
        if target is None:
            return response
        response.close()
        current = target
    raise TooManyRedirects(f"more than {max_redirects} redirects: {url}")


@dataclass(frozen=True)
class FetchResult:
    url: str            # the URL that answered (after any redirects)
    status_code: int
    content: bytes
    content_type: str | None = None


def _declared_too_large(response: Any, max_bytes: int) -> bool:
    try:
        declared = int(response.headers.get("content-length", ""))
    except ValueError:
        return False
    return declared > max_bytes


async def fetch_bytes(
    url: str,
    *,
    max_bytes: int,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    client: Any | None = None,
    chunk_size: int = 65536,
) -> FetchResult:
    """GET ``url`` through :func:`open_stream` and read at most
    ``max_bytes`` of body. Raises :class:`ResponseTooLarge` the moment the
    cap is exceeded (declared or observed); the caller decides what an
    HTTP error status means. A ``client`` may be supplied (tests pass one
    with a mock transport); otherwise a fresh one is used and closed."""
    import httpx

    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        response = await open_stream(client, url, headers=headers)
        try:
            if _declared_too_large(response, max_bytes):
                raise ResponseTooLarge(str(response.url), max_bytes)
            content = await _read_capped_async(response, max_bytes, chunk_size)
        finally:
            await response.aclose()
        return FetchResult(
            url=str(response.url),
            status_code=response.status_code,
            content=content,
            content_type=response.headers.get("content-type"),
        )
    finally:
        if own:
            await client.aclose()


def fetch_bytes_sync(
    url: str,
    *,
    max_bytes: int,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    client: Any | None = None,
    chunk_size: int = 65536,
) -> FetchResult:
    """Blocking twin of :func:`fetch_bytes` (for code already running in a
    worker thread, such as the feedparser callers)."""
    import httpx

    own = client is None
    if own:
        client = httpx.Client(timeout=timeout)
    try:
        response = open_stream_sync(client, url, headers=headers)
        try:
            if _declared_too_large(response, max_bytes):
                raise ResponseTooLarge(str(response.url), max_bytes)
            buf = bytearray()
            for chunk in response.iter_bytes(chunk_size):
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise ResponseTooLarge(str(response.url), max_bytes)
        finally:
            response.close()
        return FetchResult(
            url=str(response.url),
            status_code=response.status_code,
            content=bytes(buf),
            content_type=response.headers.get("content-type"),
        )
    finally:
        if own:
            client.close()


async def _read_capped_async(response: Any, max_bytes: int, chunk_size: int) -> bytes:
    buf = bytearray()
    async for chunk in response.aiter_bytes(chunk_size):
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise ResponseTooLarge(str(response.url), max_bytes)
    return bytes(buf)


async def iter_capped(response: Any, max_bytes: int, chunk_size: int = 65536):
    """Yield a streaming response's body chunks, raising
    :class:`ResponseTooLarge` once more than ``max_bytes`` have passed
    (checked against the declared length first)."""
    if _declared_too_large(response, max_bytes):
        raise ResponseTooLarge(str(response.url), max_bytes)
    total = 0
    async for chunk in response.aiter_bytes(chunk_size):
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLarge(str(response.url), max_bytes)
        yield chunk


async def resolve_final_url(
    url: str,
    *,
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
    client: Any | None = None,
) -> str:
    """The URL that actually answers for ``url`` after following its
    redirects through the check — for handing to a tool (ffmpeg) that
    would otherwise follow redirects on its own, unchecked. Opens and
    immediately closes the stream; the HTTP status is not interpreted."""
    import httpx

    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, read=timeout))
    try:
        response = await open_stream(client, url, headers=headers)
        try:
            return str(response.url)
        finally:
            await response.aclose()
    finally:
        if own:
            await client.aclose()


def describe_blocked(addresses: Iterable[IPAddress]) -> str:
    """Human wording for logs: which of ``addresses`` were refused."""
    return ", ".join(str(ip) for ip in addresses if not is_public_address(ip))


__all__ = [
    "ALLOWED_SCHEMES",
    "MAX_REDIRECTS",
    "FetchResult",
    "OutboundFetchError",
    "ResponseTooLarge",
    "TooManyRedirects",
    "UnsafeOutboundURL",
    "acheck_outbound_url",
    "arequire_safe_outbound_url",
    "check_outbound_url",
    "fetch_bytes",
    "fetch_bytes_sync",
    "is_public_address",
    "is_safe_outbound_url",
    "iter_capped",
    "open_stream",
    "open_stream_sync",
    "parse_ip_literal",
    "require_safe_outbound_url",
    "resolve_final_url",
    "resolve_host",
]
