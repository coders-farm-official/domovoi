"""A small HTTP/1.1 client for printers and scanners, on asyncio streams.

Why not httpx (which the rest of the core uses):

* **The address is pinned.** The caller hands in the address the LAN check
  vetted (:mod:`domovoi.printing.lan_target`); this module connects to
  exactly that and never looks a name up, so a name can't be re-resolved
  to somewhere else between the check and the connection.
* **IPPS certificates are pinned before a byte is sent.** Printers use
  self-signed certificates, so there is nothing to verify against; we keep
  the SHA-256 of the certificate seen when the printer was added and
  compare it on every connection, right after the TLS handshake and before
  the request (and the document) goes out. httpx only exposes the peer
  certificate once the request has been sent.
* **Printer quirks:** every body goes with ``Content-Length`` (some
  printers refuse chunked POSTs), never ``Expect: 100-continue``, one
  request per connection (``Connection: close``).

Answers are read with a hard byte cap. Standard library only, so the
Linux harness container (no venv) can import it.
"""

from __future__ import annotations

import asyncio
import hashlib
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Union

BodyPart = Union[bytes, Path]

CHUNK = 64 * 1024
DEFAULT_MAX_BYTES = 1 << 20
MAX_HEADER_BYTES = 32 * 1024
USER_AGENT = "Domovoi"


class HttpError(Exception):
    """The exchange failed: connect, TLS, a malformed or oversized answer."""


class ConnectFailed(HttpError):
    """Nothing answered at the address (refused, timed out, unreachable)."""


class CertificateChanged(HttpError):
    """The device's TLS certificate is not the one pinned when it was
    added. Nothing was sent."""

    def __init__(self, seen: str) -> None:
        super().__init__("the device's security certificate changed")
        self.seen = seen


class TooLarge(HttpError):
    pass


@dataclass
class Endpoint:
    """Where to connect: ``address`` is an IP literal that has already
    passed the LAN check; ``host`` is what goes in the Host header (and
    the TLS server name when it is a name)."""

    address: str
    port: int
    tls: bool = False
    host: str | None = None
    cert_sha256: str | None = None  # the pin; None = first use (record it)

    @property
    def host_header(self) -> str:
        h = self.host or self.address
        if ":" in h and not h.startswith("["):
            h = "[%s]" % h
        default = 443 if self.tls else 80
        return h if self.port == default else "%s:%d" % (h, self.port)


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    cert_sha256: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


def _tls_context() -> ssl.SSLContext:
    # Printers' certificates are self-signed: there is no chain to check.
    # Trust comes from the pin (cert_sha256), compared before sending.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def body_length(parts: Iterable[BodyPart]) -> int:
    n = 0
    for p in parts:
        n += p.stat().st_size if isinstance(p, Path) else len(p)
    return n


async def request(
    endpoint: Endpoint,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: list[BodyPart] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    connect_timeout: float = 5.0,
    timeout: float = 60.0,
) -> HttpResponse:
    """One request on a fresh connection. Raises :class:`ConnectFailed`,
    :class:`CertificateChanged` (before sending anything), :class:`TooLarge`
    or :class:`HttpError`."""
    if not path.startswith("/"):
        raise HttpError("path must start with /")
    if any(c in path for c in "\r\n ") or any(c in method for c in "\r\n "):
        raise HttpError("bad request line")
    ssl_ctx = _tls_context() if endpoint.tls else None
    server_name = None
    if endpoint.tls and endpoint.host and not _is_ip(endpoint.host):
        server_name = endpoint.host
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                endpoint.address, endpoint.port, ssl=ssl_ctx,
                server_hostname=server_name if ssl_ctx else None,
            ),
            timeout=connect_timeout,
        )
    except (OSError, asyncio.TimeoutError, ssl.SSLError) as e:
        raise ConnectFailed("%s:%d did not answer (%s)" % (endpoint.address, endpoint.port, type(e).__name__)) from e
    seen = None
    try:
        if ssl_ctx is not None:
            sslobj = writer.get_extra_info("ssl_object")
            der = sslobj.getpeercert(binary_form=True) if sslobj is not None else None
            if not der:
                raise HttpError("no certificate from a TLS device")
            seen = hashlib.sha256(der).hexdigest()
            if endpoint.cert_sha256 and seen != endpoint.cert_sha256:
                raise CertificateChanged(seen)
        parts = list(body or [])
        length = body_length(parts)
        hdrs = {
            "Host": endpoint.host_header,
            "User-Agent": USER_AGENT,
            "Connection": "close",
            "Accept-Encoding": "identity",
        }
        if method in ("POST", "PUT") or parts:
            hdrs["Content-Length"] = str(length)
        for k, v in (headers or {}).items():
            if any(c in str(k) + str(v) for c in "\r\n"):
                raise HttpError("bad header")
            hdrs[k] = str(v)
        head = "%s %s HTTP/1.1\r\n" % (method, path)
        head += "".join("%s: %s\r\n" % kv for kv in hdrs.items()) + "\r\n"
        writer.write(head.encode("latin-1"))
        await asyncio.wait_for(_send_body(writer, parts), timeout=timeout)
        status, rheaders, rbody = await asyncio.wait_for(
            _read_response(reader, max_bytes, method), timeout=timeout
        )
        return HttpResponse(status=status, headers=rheaders, body=rbody, cert_sha256=seen)
    except asyncio.TimeoutError as e:
        raise HttpError("timed out") from e
    except (ConnectionError, ssl.SSLError, OSError) as e:
        raise HttpError("connection dropped (%s)" % type(e).__name__) from e
    except (ValueError, asyncio.LimitOverrunError, asyncio.IncompleteReadError) as e:
        # A line past the stream's limit (asyncio raises ValueError), a
        # length that makes no sense: a malformed answer, never a crash.
        raise HttpError("the device's answer didn't make sense (%s)" % type(e).__name__) from e
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
        except (Exception, asyncio.TimeoutError):  # noqa: BLE001 — closing only
            pass


async def _send_body(writer: asyncio.StreamWriter, parts: list[BodyPart]) -> None:
    await writer.drain()
    for part in parts:
        if isinstance(part, Path):
            with part.open("rb") as f:
                while True:
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    writer.write(chunk)
                    await writer.drain()
        else:
            for i in range(0, len(part), CHUNK):
                writer.write(part[i:i + CHUNK])
                await writer.drain()


async def _read_response(
    reader: asyncio.StreamReader, max_bytes: int, method: str
) -> tuple[int, dict[str, str], bytes]:
    while True:
        status, headers = await _read_head(reader)
        if status != 100:
            break
    if method == "HEAD" or status in (204, 304):
        return status, headers, b""
    te = headers.get("transfer-encoding", "").lower()
    if "chunked" in te:
        return status, headers, await _read_chunked(reader, max_bytes)
    if "content-length" in headers:
        try:
            n = int(headers["content-length"])
        except ValueError as e:
            raise HttpError("bad Content-Length") from e
        if n < 0:
            raise HttpError("bad Content-Length")
        if n > max_bytes:
            raise TooLarge("answer of %d bytes is over the %d cap" % (n, max_bytes))
        try:
            return status, headers, await reader.readexactly(n)
        except asyncio.IncompleteReadError as e:
            raise HttpError("answer cut short") from e
    data = bytearray()
    while True:
        chunk = await reader.read(CHUNK)
        if not chunk:
            break
        data += chunk
        if len(data) > max_bytes:
            raise TooLarge("answer over the %d cap" % max_bytes)
    return status, headers, bytes(data)


async def _read_head(reader: asyncio.StreamReader) -> tuple[int, dict[str, str]]:
    total = 0
    line = await reader.readline()
    total += len(line)
    parts = line.decode("latin-1").strip().split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/1."):
        raise HttpError("not an HTTP answer")
    try:
        status = int(parts[1])
    except ValueError as e:
        raise HttpError("bad status line") from e
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        total += len(line)
        if total > MAX_HEADER_BYTES:
            raise TooLarge("headers too large")
        if not line:
            raise HttpError("answer cut short")
        text = line.decode("latin-1").rstrip("\r\n")
        if not text:
            break
        if ":" in text:
            k, v = text.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status, headers


async def _read_chunked(reader: asyncio.StreamReader, max_bytes: int) -> bytes:
    data = bytearray()
    while True:
        size_line = await reader.readline()
        if not size_line:
            raise HttpError("chunked answer cut short")
        digits = size_line.split(b";", 1)[0].strip()
        if not digits or len(digits) > 16 or any(c not in b"0123456789abcdefABCDEF" for c in digits):
            raise HttpError("bad chunk size")
        size = int(digits, 16)
        if size == 0:
            # trailers (a few at most)
            for _ in range(64):
                line = await reader.readline()
                if not line or line in (b"\r\n", b"\n"):
                    return bytes(data)
            raise HttpError("too many trailer lines")
        if len(data) + size > max_bytes:
            raise TooLarge("answer over the %d cap" % max_bytes)
        try:
            data += await reader.readexactly(size)
            await reader.readexactly(2)
        except asyncio.IncompleteReadError as e:
            raise HttpError("chunked answer cut short") from e


def _is_ip(host: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False
