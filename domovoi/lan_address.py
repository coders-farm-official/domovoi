"""This host's LAN IPv4 address, for URLs other devices dial back to.

Two settings hand a satellite an address of this server:
``MPD_HTTP_BASE`` (the prefix of each room's MPD HTTP stream, which the Pi
fetches) and ``SATELLITE_ADOPTION_ADVERTISE_URL`` (the core URL the web
backend writes into a USB-adopted satellite). Both default to ``auto``:
the address is looked up when the URL is built instead of being fixed in
``.env``, so a fresh install works without editing either, and a host
whose DHCP lease moves it to a new address hands out the new one within
:data:`CACHE_TTL_SEC`. An explicit value is used exactly as written — set
one when the server has several network interfaces and the lookup picks
the wrong one.

The lookup is the UDP-connect trick: ``connect()`` a datagram socket
toward a TEST-NET address and read back the source address the kernel
chose for that route. Nothing is sent. When that fails, or yields
loopback, the host name is resolved instead.

Importable by the web process — stdlib plus :mod:`domovoi.config`, which
the web backend already reads.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time

from domovoi.config import settings

log = logging.getLogger(__name__)

# The value (any case) that asks for the address to be derived. A blank
# setting means the same thing.
AUTO = "auto"

# What MPD_HTTP_BASE falls back to when ``auto`` finds no LAN address:
# the setting's default before ``auto`` existed.
MPD_HTTP_BASE_FALLBACK = "http://localhost"

# How long a looked-up address is reused. The lookup is a routing-table
# query, so this is short: it bounds how long a DHCP renumbering can keep
# handing out the old address, while the dashboard's now-playing poll
# (one URL per room, every few seconds) doesn't repeat it per room.
CACHE_TTL_SEC = 10.0

# Patched by tests; the server never touches these.
_clock = time.monotonic
_cached_ip: str | None = None
_cached_at: float | None = None


def is_auto(value: str | None) -> bool:
    """Whether a URL setting asks for the address to be derived: ``auto``
    in any case, or blank."""
    return (value or "").strip().lower() in ("", AUTO)


def probe_lan_ipv4() -> str:
    """One uncached lookup of this host's LAN IPv4; ``""`` when there is
    none. The host-name fallback can return a loopback address (Debian and
    Ubuntu map the host name to 127.0.1.1), so a caller that must never
    hand one out has to check — :func:`lan_ipv4` does."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # TEST-NET; nothing is sent
        ip = s.getsockname()[0]
    except OSError:
        ip = ""
    finally:
        s.close()
    if not ip or ip.startswith("127."):
        # Last resort: resolve the host name. A multi-NIC box that lands
        # here should set its URL settings explicitly instead.
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            ip = ""
    return ip


def _usable(ip: str) -> str | None:
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return None
    if addr.is_loopback or addr.is_unspecified:
        return None
    return ip


def lan_ipv4() -> str | None:
    """This host's LAN IPv4 address, or ``None`` when there is no usable
    one. Never loopback: a satellite given a loopback URL dials itself.

    Reused for :data:`CACHE_TTL_SEC`, then looked up again, so an address
    change is picked up on the next use after that. Changes, and losing
    the address, are logged once each rather than on every call.
    """
    global _cached_ip, _cached_at
    now = _clock()
    if _cached_at is not None and now - _cached_at < CACHE_TTL_SEC:
        return _cached_ip
    ip = _usable(probe_lan_ipv4())
    first = _cached_at is None
    if first or ip != _cached_ip:
        if ip is None:
            log.warning(
                "no LAN IPv4 address found%s; URLs set to 'auto' use their "
                "fallbacks until one appears (set MPD_HTTP_BASE explicitly "
                "if this persists)",
                "" if first or _cached_ip is None else f" (was {_cached_ip})",
            )
        elif first or _cached_ip is None:
            log.info("LAN address for 'auto' URLs: %s", ip)
        else:
            log.info(
                "LAN address changed from %s to %s; 'auto' URLs follow it",
                _cached_ip, ip,
            )
    _cached_ip, _cached_at = ip, now
    return ip


def reset_cache() -> None:
    """Forget the cached address, so the next :func:`lan_ipv4` looks it up
    again."""
    global _cached_ip, _cached_at
    _cached_ip, _cached_at = None, None


def mpd_http_base() -> str:
    """The URL prefix satellites fetch per-room MPD streams from.

    ``settings.mpd_http_base`` exactly as written, unless it is ``auto``
    (the default) or blank: then ``http://<LAN IPv4>``, or
    :data:`MPD_HTTP_BASE_FALLBACK` when there is no LAN address. Callers
    append ``:<http port>``.
    """
    value = settings.mpd_http_base
    if not is_auto(value):
        return value
    ip = lan_ipv4()
    return f"http://{ip}" if ip else MPD_HTTP_BASE_FALLBACK
