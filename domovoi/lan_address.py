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

**A server inside a VM** (the planned macOS installer's Lima VM) would find
the VM's private address that way, which no satellite can reach: the
household's devices reach the host's own LAN address, which forwards into
the VM. There the host side tells the core that address in
``/var/lib/domovoi-mac/lan.json`` (:data:`HOST_LAN_FILE`), written as root
inside the VM whenever it changes: ``{"schema": 1, "ipv4": "192.168.1.20"}``
(``written_at`` and ``interface`` may come along; they are only logged).
While that file exists and is root's alone (root owns it and its folder,
and nobody else can write either, the rule ``windows_side.read_side``
applies to its own file), its address wins over the lookup; one that isn't
root's, isn't valid or names loopback is ignored, with a warning, and the
lookup runs as before. No file (every Linux and Windows install today): the
lookup, unchanged. A WSL install keeps the lookup too: WSL's mirrored
networking gives the distro the PC's own address.

Importable by the web process — stdlib plus :mod:`domovoi.config`, which
the web backend already reads.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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

# Where the host side of a VM install writes the host's LAN address (see
# the module docstring). DOMOVOI_HOST_LAN_FILE overrides it, for tests.
HOST_LAN_FILE = "/var/lib/domovoi-mac/lan.json"
_HOST_LAN_MAX_BYTES = 4096

# Patched by tests; the server never touches these.
_clock = time.monotonic
_cached_ip: str | None = None
_cached_at: float | None = None
_cached_source: str | None = None
# Problems with the host file already logged, so a bad file is said once,
# not on every lookup.
_warned_host_file: set[str] = set()


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


def host_lan_file() -> Path:
    return Path(os.environ.get("DOMOVOI_HOST_LAN_FILE") or HOST_LAN_FILE)


def _root_only(st: os.stat_result) -> bool:
    """Owned by root and writable by nobody else. Where there are no POSIX
    owners (a Windows development box) nothing is root's, so nothing
    passes; the tests replace this."""
    if not hasattr(os, "getuid"):
        return False
    return st.st_uid == 0 and not (st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def _host_file_problem(path: Path, why: str) -> None:
    if why not in _warned_host_file:
        _warned_host_file.add(why)
        log.warning("host LAN address: %s %s; looking the address up instead", path, why)


def host_lan_ipv4() -> str | None:
    """The LAN IPv4 the host side wrote in :func:`host_lan_file`, or None:
    no file (the usual case), or one this module doesn't believe (not
    root's alone, not readable, not the format, or no usable address).
    Never raises."""
    path = host_lan_file()
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as e:
        _host_file_problem(path, f"can't be opened ({e.strerror or e})")
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not _root_only(st) or not _root_only(os.stat(path.parent)):
            _host_file_problem(path, "isn't root's alone")
            return None
        if st.st_size > _HOST_LAN_MAX_BYTES:
            _host_file_problem(path, "is too big")
            return None
        data = os.read(fd, _HOST_LAN_MAX_BYTES + 1)
    except OSError as e:
        _host_file_problem(path, f"can't be read ({e.strerror or e})")
        return None
    finally:
        os.close(fd)
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        doc = None
    ip = doc.get("ipv4") if isinstance(doc, dict) and doc.get("schema") == 1 else None
    usable = _usable(ip) if isinstance(ip, str) else None
    if usable is None:
        _host_file_problem(path, "names no usable LAN address")
        return None
    return usable


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
    global _cached_ip, _cached_at, _cached_source
    now = _clock()
    if _cached_at is not None and now - _cached_at < CACHE_TTL_SEC:
        return _cached_ip
    ip = host_lan_ipv4()
    source = "host" if ip else "lookup"
    if ip is None:
        ip = _usable(probe_lan_ipv4())
    first = _cached_at is None
    # Said when the host side's file starts or stops deciding (never on a
    # box that has none, so their logs stay as they were).
    if source != _cached_source and ip is not None and "host" in (source, _cached_source):
        log.info(
            "LAN address for 'auto' URLs comes from %s",
            f"the host side ({host_lan_file()})" if source == "host" else "this machine's own lookup",
        )
    _cached_source = source
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
    global _cached_ip, _cached_at, _cached_source
    _cached_ip, _cached_at, _cached_source = None, None, None
    _warned_host_file.clear()


def stream_url_for_peer(url: str, dialed_host: str | None) -> str:
    """``url`` (a room stream from :func:`mpd_http_base`) with its host
    swapped for ``dialed_host``, the address the satellite itself used to
    reach this server. MPD runs on this host, so the address the satellite
    already reached is the one that works, whatever the LAN lookup or a
    saved ``MPD_HTTP_BASE`` says: a Windows install pinned the address it
    had at setup, and after a DHCP move every room was sent a stream on an
    address nobody answers (2026-10-29: the music started, then stopped).

    Left as is: no dialed host, a URL that isn't plain http, and an
    ``MPD_HTTP_BASE`` set to a host *name*, which someone chose on purpose
    (an address written there is what Setup pins, so it is replaced)."""
    host = (dialed_host or "").strip().strip("[]")
    if not host:
        return url
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname:
        return url
    if not is_auto(settings.mpd_http_base):
        base_host = urlsplit(settings.mpd_http_base.strip()).hostname or ""
        try:
            ipaddress.ip_address(base_host)
        except ValueError:
            return url
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit(parts._replace(netloc=netloc))


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
