"""Transport-level guards shared by the core and the web process.

Everything here runs BEFORE routing and knows nothing about who the
caller is — it is about where the request claims to be going and where it
says it came from, which is the layer the browser's own rules are
enforced at:

* :func:`host_allowed` / :class:`LanHostMiddleware` (CORE-8) — the ``Host``
  header must name this box on the LAN. Without this check a page on the
  public internet can point a name it controls at 127.0.0.1 (DNS
  rebinding) and then read this server from the victim's browser as if it
  were same-origin, because from the browser's point of view it is.
* :func:`origin_allowed` (CORE-2 / CORE-8) — a WebSocket upgrade is exempt
  from the same-origin policy: any page may open one to any host, and
  ``Origin`` is the only thing that says which page did. The LAN regex is
  the one the web process already enforces for CORS, so a page that
  cannot call the REST API cannot open a socket either.

Both are deliberately generous about what counts as "the LAN", because
the alternative — a household locked out of its own server — is the
failure mode people actually hit.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Any, Iterable
from urllib.parse import urlsplit

from domovoi.config import settings

log = logging.getLogger(__name__)

# Suffixes that only resolve on a local network. ``.local`` is mDNS,
# ``.home.arpa`` is the IETF's reserved home-network zone (RFC 8375), and
# ``.lan`` / ``.internal`` are the two every consumer router ships.
LOCAL_SUFFIXES = (".local", ".localhost", ".lan", ".home.arpa", ".internal")

# The origin regex the web process has always used for CORS, kept here so
# the WebSocket check and the CORS policy cannot drift apart.
_LAN_ORIGIN_ALTERNATIVES = (
    r"localhost(:\d+)?",
    r"127\.0\.0\.1(:\d+)?",
    r"\[::1\](:\d+)?",
    r"192\.168\.\d+\.\d+(:\d+)?",
    r"10\.\d+\.\d+\.\d+(:\d+)?",
    r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+(:\d+)?",
    r"169\.254\.\d+\.\d+(:\d+)?",
    r"[\w-]+\.local(:\d+)?",
    r"[\w-]+\.lan(:\d+)?",
    r"[\w-]+\.home\.arpa(:\d+)?",
    r"[\w-]+\.internal(:\d+)?",
)


def configured_hosts() -> tuple[str, ...]:
    """Extra host names the operator declared in ``TRUSTED_HOSTS``
    (comma-separated). For the household that reaches Domovoi through a
    real name — a reverse proxy, a tailnet, a DNS entry of their own —
    which no rule here could guess."""
    raw = str(getattr(settings, "trusted_hosts", "") or "")
    return tuple(h.strip().lower() for h in raw.split(",") if h.strip())


def _strip_port(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):  # [::1]:6370
        end = host.find("]")
        return host[1:end] if end != -1 else host[1:]
    # An IPv6 literal without brackets has several colons; leave it alone.
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host


def _matches_configured(host: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        if pattern.startswith("*."):
            if host == pattern[2:] or host.endswith(pattern[1:]):
                return True
        elif host == pattern:
            return True
    return False


def host_allowed(host: str | None) -> bool:
    """Is ``host`` a name that can only mean this box, on this LAN?

    A missing ``Host`` passes: rebinding needs a NAME, so a request that
    carries none cannot be a rebound one (and HTTP/1.0 health checks send
    none). Otherwise:

    * an IP literal passes when it is private, loopback or link-local —
      never a public address, which is what a rebound name resolves to;
    * a single-label name passes (``localhost``, ``beelink``, the
      container names the harnesses use). A bare label cannot be bought:
      public DNS names always contain a dot, so this cannot be a name an
      attacker controls;
    * a name under a local-only suffix passes;
    * anything the operator named in ``TRUSTED_HOSTS`` passes.
    """
    if not host:
        return True
    host = _strip_port(host)
    if not host:
        return True
    if _matches_configured(host, configured_hosts()):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return bool(
            ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified
        )
    if "." not in host:
        return True
    return host.endswith(LOCAL_SUFFIXES)


def lan_origin_regex() -> str:
    """The CORS / WebSocket origin pattern, including any configured
    hosts."""
    alternatives = list(_LAN_ORIGIN_ALTERNATIVES)
    for name in configured_hosts():
        alternatives.append(re.escape(name.lstrip("*.")) + r"(:\d+)?")
    return r"^https?://(" + r"|".join(alternatives) + r")$"


def origin_allowed(origin: str | None) -> bool:
    """May a page at ``origin`` open a socket to us?

    An ABSENT ``Origin`` passes, and has to: the satellites, the Android
    app and every command-line client send none. Only browsers set it,
    and only a browser is subject to the rule this enforces. A present
    ``Origin`` must be a LAN origin — including the literal ``null``,
    which a sandboxed frame sends and which is nobody's LAN.
    """
    if origin is None or not origin.strip():
        return True
    origin = origin.strip()
    if re.match(lan_origin_regex(), origin, flags=re.IGNORECASE):
        return True
    # Fall back to the host predicate for the names the CORS regex does
    # not spell out (a single-label host, a configured wildcard), so the
    # socket is never stricter than the REST API beside it.
    if origin.lower() == "null":
        return False
    host = urlsplit(origin).hostname
    if host is None:
        return False
    return host_allowed(host)


class LanHostMiddleware:
    """Reject an HTTP request whose ``Host`` is not this box on the LAN
    (400), before it reaches a route.

    Pure ASGI rather than Starlette's ``TrustedHostMiddleware`` because
    that one matches literal names and ``*.suffix`` only, and the hosts
    that matter here are whole private IP RANGES plus whatever the
    household's router calls this machine.

    ``websocket`` scopes pass through: a socket is judged on ``Origin``
    (:func:`origin_allowed`) in the route, which is the check that
    actually binds a browser, and closing an upgrade with an HTTP 400 is
    not something a WebSocket client can report usefully.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        host = None
        for key, value in scope.get("headers") or ():
            if key == b"host":
                host = value.decode("latin-1")
                break
        if host_allowed(host):
            await self.app(scope, receive, send)
            return
        log.warning("refused request for Host %r (not a LAN name)", host)
        body = b"Host header is not a name this server answers to"
        await send({
            "type": "http.response.start",
            "status": 400,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
