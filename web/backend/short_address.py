"""The short address: port 80 hands a browser over to the dashboard.

A person types ``domovoi.local`` (or the server's IP) with no port; the
browser asks port 80; this listener answers every request with a redirect
to the SAME host on the dashboard's port, path and query kept::

    http://domovoi.local/music?x=1  ->  302  http://domovoi.local:6369/music?x=1

The dashboard stays on 6369 and the core on 6370 (AGENTS.md rule 3): links,
satellites, the apps and bookmarks never see port 80. The listener is a few
lines of asyncio inside the web process, not a second web app: it parses
the request head itself, never reads a body, never reads or logs a cookie,
serves nothing but the redirect and the refusals below, and closes every
connection after one answer.

It is never an open redirect. The redirect keeps the host the browser asked
for, and only when that host is this server's own: the address the
connection arrived on (the socket's local address), or one of this
server's names (its host name, its network name with and without
``.local``, the name the core holds now, an exact name the operator put
in ``TRUSTED_HOSTS``). Anything else is a 400 whose
text names the server's own address. Only GET and HEAD are redirected;
every other method is a 405. The request target must be origin-form
(``/path?query``, printable ASCII), so the Location header can't be
split or pointed elsewhere.

On by default, but only when port 80 is free: the bind at start is the
probe. Something else holding it (a Pi-hole, a web server, IIS through
the Windows install's mirrored networking) leaves it off, quietly, and
Settings says so (``taken``). The admin's switch (``PUT
/api/short-address``) is kept in ``~/.domovoi/short_address.json`` and
applies at once: off frees the port, on tries it again.

On a Mac install the bind is the VM's, but the household reaches the Mac:
the Mac's helper redirects the Mac's own port 80 into the VM only while
nothing on the Mac serves it, and says which in
``/var/lib/domovoi-mac/short_address.json`` (:func:`read_host_report`). A
Mac whose port 80 is another program's makes the state ``taken`` here too,
so the header never offers ``domovoi.local`` while it reaches that program.

Linux only: every Domovoi server runs the dashboard on Linux (a Linux box,
the Windows install's WSL distro, the Mac install's VM). Binding a port
below 1024 needs ``CAP_NET_BIND_SERVICE``, which the dashboard's unit
grants and nothing else (scripts/linux/units/domovoi-web.service);
:func:`keep_bind_capability_from_children` keeps it out of the programs
the dashboard starts. A development box on Windows or a Mac says
``unsupported`` and Settings hides the switch.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import ipaddress
import json
import logging
import os
import re
import socket
import stat
import sys
from pathlib import Path
from typing import Any, Callable

# At module top on purpose: the web import guard refuses a NEW domovoi.*
# import once startup is done (web/backend/plugin_host.py).
from domovoi.config import settings as core_settings
from domovoi.transport_guard import configured_hosts

log = logging.getLogger(__name__)

#: The short address's port. An environment override exists for tests and
#: development only.
DEFAULT_PORT = 80
STATE_NAME = "short_address.json"

#: on: listening. off: the admin switched it off. taken: something else
#: holds the port. no_permission: the process may not bind a low port.
#: failed: another error. unsupported: not a Linux server (or not started:
#: the test suite never binds).
STATES = ("on", "off", "taken", "no_permission", "failed", "unsupported")

MESSAGES = {
    "on": "Short address on: browsers can open this server without :{dashboard}.",
    "off": "Short address off: an admin switched it off.",
    "taken": "Short address off: something else on this computer uses port {port}.",
    "no_permission": (
        "Short address off: this computer doesn't let Domovoi use port {port}. "
        "The next update sets that up."
    ),
    "failed": "Short address off: port {port} couldn't be opened.",
    "unsupported": "Short address not available on this computer.",
}

#: ``taken`` on a Mac install whose own port 80 is another program's.
TAKEN_ON_MAC = "Short address off: something else on this Mac uses port {port}{holder}."

# One request head, at most: a browser's is well under this.
HEAD_LIMIT = 8192
READ_TIMEOUT_SEC = 5.0
WRITE_TIMEOUT_SEC = 5.0
MAX_CONNECTIONS = 64
TARGET_MAX = 4096
NAMES_REFRESH_SEC = 60.0

_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_TARGET = re.compile(r"^/[\x21-\x7e]*$")
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


#: The Mac helper's word on the Mac's own port 80 (mac/Sources/DomovoiMacKit/
#: ShortAddressPort.swift, HostFiles.shortAddressFile): written as root into
#: the VM beside lan_name.json, ``{"schema": 1, "state": "on" | "taken" |
#: "unchecked", "holder": name | null}``.
HOST_REPORT = "/var/lib/domovoi-mac/short_address.json"
HOST_REPORT_MAX_BYTES = 1024
HOST_STATES = ("on", "taken", "unchecked")
_HOLDER = re.compile(r"^[A-Za-z0-9._ -]{1,32}$")


def _root_only(st: os.stat_result) -> bool:
    """Owned by root, writable by nobody else; nothing passes where there
    are no POSIX owners (the tests replace this)."""
    if not hasattr(os, "getuid"):
        return False
    return st.st_uid == 0 and not (st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def read_host_report(path: Path | None = None) -> dict[str, Any] | None:
    """The Mac helper's report on the Mac's own port 80, or None: no file
    (not a Mac install), not root's alone, or not the schema. Never raises."""
    p = path or Path(HOST_REPORT)
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not _root_only(st) or not _root_only(os.stat(p.parent)):
            log.warning("short address: %s isn't root's alone; not believing it", p)
            return None
        if st.st_size > HOST_REPORT_MAX_BYTES:
            return None
        raw = os.read(fd, HOST_REPORT_MAX_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != 1 or doc.get("state") not in HOST_STATES:
        return None
    holder = doc.get("holder")
    if not (isinstance(holder, str) and _HOLDER.match(holder)):
        holder = None
    return {"state": doc["state"], "holder": holder}


def state_path() -> Path:
    return Path.home() / ".domovoi" / STATE_NAME


def supported() -> bool:
    return sys.platform.startswith("linux")


def read_enabled(path: Path | None = None) -> bool:
    """The admin's switch; on when never set (or unreadable)."""
    p = path or state_path()
    try:
        raw = p.read_bytes()[:4096]
    except FileNotFoundError:
        return True
    except OSError as e:
        log.warning("short address: can't read %s: %s", p, e)
        return True
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        log.warning("short address: %s isn't JSON; treating the switch as on", p)
        return True
    v = doc.get("enabled") if isinstance(doc, dict) else None
    return v if isinstance(v, bool) else True


def write_enabled(on: bool, path: Path | None = None) -> None:
    """Saved 0600, replaced in one rename."""
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write((json.dumps({"v": 1, "enabled": bool(on)}) + "\n").encode("ascii"))
    os.replace(tmp, p)


# ─── the request, as pure functions ───────────────────────────────────────


def _ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def parse_host(value: str) -> tuple[str, int | None] | None:
    """``Host`` -> ``(host, port)``, lowercased, IPv6 without brackets;
    None for anything that isn't a plain name or address."""
    v = value.strip().lower()
    if not v or len(v) > 255:
        return None
    port: int | None = None
    if v.startswith("["):
        end = v.find("]")
        if end < 0:
            return None
        host, rest = v[1:end], v[end + 1:]
        ip = _ip(host)
        if ip is None or not isinstance(ip, ipaddress.IPv6Address) or "%" in host:
            return None
        if rest:
            if not rest.startswith(":") or not rest[1:].isdigit():
                return None
            port = int(rest[1:])
        return str(ip), port
    if v.count(":") > 1:
        return None
    host, sep, p = v.partition(":")
    if sep:
        if not p.isdigit() or len(p) > 5:
            return None
        port = int(p)
    if host.endswith("."):
        host = host[:-1]
    if not host:
        return None
    ip = _ip(host)
    if ip is not None:
        return (str(ip), port) if isinstance(ip, ipaddress.IPv4Address) else None
    if len(host) > 253 or not all(_LABEL.match(label) for label in host.split(".")):
        return None
    return host, port


def host_allowed(host: str, local_ip: str | None, names: frozenset[str] | set[str]) -> bool:
    """Is ``host`` (from :func:`parse_host`) this server's own? ``names``
    may hold addresses too: the ones the core reports for this server (a
    Mac install's VM sees the Mac's address arrive redirected to its own)."""
    local = _ip(local_ip) if local_ip else None
    ip = _ip(host)
    if ip is not None:
        if str(ip) in names:
            return True
        if local is None:
            return False
        if ip == local:
            return True
        return ip.is_loopback and local.is_loopback
    if host == "localhost":
        return local is not None and local.is_loopback
    return host in names


def server_names(labels: list[str | None], extra: list[str | None] = ()) -> frozenset[str]:
    """Each label alone and with ``.local``, plus whole names as given."""
    out: set[str] = set()
    for raw in labels:
        if not raw:
            continue
        label = str(raw).strip().lower().rstrip(".")
        if label.endswith(".local"):
            label = label[:-6]
        if _LABEL.match(label):
            out.add(label)
            out.add(label + ".local")
    for raw in extra:
        if not raw:
            continue
        name = str(raw).strip().lower().rstrip(".")
        if name and parse_host(name) is not None and _ip(name) is None:
            out.add(name)
    return frozenset(out)


def _reply(status: str, headers: list[tuple[str, str]], body: str, head_only: bool) -> bytes:
    data = body.encode("utf-8")
    lines = [f"HTTP/1.1 {status}"]
    lines += [f"{k}: {v}" for k, v in headers]
    lines += [
        "Content-Type: text/plain; charset=utf-8",
        f"Content-Length: {len(data)}",
        "Cache-Control: no-store",
        "X-Content-Type-Options: nosniff",
        "Connection: close",
    ]
    out = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
    return out if head_only else out + data


def _own_url(local_ip: str | None, target_port: int) -> str:
    ip = _ip(local_ip) if local_ip else None
    if ip is None:
        return f"this server's address with :{target_port}"
    host = f"[{ip}]" if isinstance(ip, ipaddress.IPv6Address) else str(ip)
    return f"http://{host}:{target_port}/"


def answer(head: bytes, *, local_ip: str | None, names: frozenset[str] | set[str],
           target_port: int, listen_port: int = DEFAULT_PORT) -> tuple[bytes, int]:
    """The whole reply to one request head (everything up to the blank
    line). Returns ``(bytes, status)``."""
    try:
        text = head.decode("ascii")
    except UnicodeDecodeError:
        return _reply("400 Bad Request", [], "Bad request.\n", False), 400
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or parts[2] not in ("HTTP/1.0", "HTTP/1.1") or not _TOKEN.match(parts[0]):
        return _reply("400 Bad Request", [], "Bad request.\n", False), 400
    method, target, _ = parts
    head_only = method == "HEAD"
    hosts: list[str] = []
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if not sep or not _TOKEN.match(name):
            return _reply("400 Bad Request", [], "Bad request.\n", head_only), 400
        if name.lower() == "host":
            hosts.append(value)
    if method not in ("GET", "HEAD"):
        return _reply("405 Method Not Allowed", [("Allow", "GET, HEAD")],
                      "This address only sends you on to the Domovoi dashboard.\n", False), 405
    if len(target) > TARGET_MAX or not _TARGET.match(target):
        return _reply("400 Bad Request", [], "Bad request.\n", head_only), 400
    parsed = parse_host(hosts[0]) if len(hosts) == 1 else None
    if (
        parsed is None
        or (parsed[1] is not None and parsed[1] != listen_port)
        or not host_allowed(parsed[0], local_ip, names)
    ):
        body = (
            "That name isn't this Domovoi server's. Open "
            + _own_url(local_ip, target_port) + " instead.\n"
        )
        return _reply("400 Bad Request", [], body, head_only), 400
    host = parsed[0]
    if _ip(host) is not None and ":" in host:
        host = f"[{host}]"
    location = f"http://{host}:{target_port}{target}"
    return _reply("302 Found", [("Location", location)], "", head_only), 302


# ─── the listener ──────────────────────────────────────────────────────────


class ShortAddress:
    """One per web process (:data:`SERVICE`)."""

    def __init__(
        self,
        *,
        port: int | None = None,
        state_file: Path | None = None,
        is_supported: Callable[[], bool] = supported,
        host_report: Path | None = None,
    ) -> None:
        env_port = os.environ.get("DOMOVOI_SHORT_ADDRESS_PORT")
        self.port = port if port is not None else int(env_port) if env_port else DEFAULT_PORT
        self.host = "0.0.0.0"
        self.target_port = 6369
        self._state_file = state_file
        self._supported = is_supported
        self._host_report = host_report
        self.state = "unsupported"
        self.error: str | None = None
        self.started = False
        self._server: asyncio.AbstractServer | None = None
        self._names: frozenset[str] = frozenset()
        self._names_task: asyncio.Task | None = None
        self._lan_labels: list[str | None] = []
        self._lan_addresses: list[str] = []
        self._active = 0
        self._lock = asyncio.Lock()
        self.fetch_lan_name: Callable[[], Any] | None = None

    # The switch -------------------------------------------------------
    def enabled(self) -> bool:
        return read_enabled(self._state_file)

    def status(self) -> dict[str, Any]:
        state = self.state
        message = MESSAGES[state].format(port=self.port, dashboard=self.target_port)
        if state == "on":
            # Listening in the VM says nothing about the Mac in front of it:
            # its helper redirects port 80 only while nothing on the Mac
            # serves it (read_host_report). Elsewhere there is no report.
            host = read_host_report(self._host_report)
            if host is not None and host["state"] == "taken":
                state = "taken"
                holder = f" ({host['holder']})" if host["holder"] else ""
                message = TAKEN_ON_MAC.format(port=self.port, holder=holder)
            elif host is not None and host["state"] == "unchecked":
                state = "failed"
                message = MESSAGES["failed"].format(port=self.port, dashboard=self.target_port)
        return {
            "enabled": self.enabled(),
            "state": state,
            "port": self.port,
            "dashboard_port": self.target_port,
            "message": message,
        }

    async def set_enabled(self, on: bool) -> dict[str, Any]:
        await asyncio.to_thread(write_enabled, on, self._state_file)
        if self.started:
            await self.apply()
        return self.status()

    # Start and stop ---------------------------------------------------
    async def start(self, *, host: str = "0.0.0.0", target_port: int = 6369) -> None:
        """At the web's start. Never raises."""
        self.host, self.target_port = host, target_port
        if not self._supported():
            self.state = "unsupported"
            return
        self.started = True
        try:
            await self.apply()
        except Exception:  # noqa: BLE001 - never stop the dashboard
            log.exception("short address: could not start")

    async def stop(self) -> None:
        self.started = False
        await self._close()

    async def apply(self) -> None:
        """Bind or free the port to match the switch."""
        async with self._lock:
            if not self.enabled():
                await self._close()
                self.state = "off"
                return
            if self._server is not None:
                self.state = "on"
                return
            await self._open()

    async def _open(self) -> None:
        self.error = None
        try:
            self._server = await asyncio.start_server(
                self._serve, self.host, self.port, limit=HEAD_LIMIT, backlog=64,
            )
        except PermissionError as e:
            self.state, self.error = "no_permission", str(e)
            log.info("short address off: no permission to use port %d (%s)", self.port, e)
            return
        except OSError as e:
            if e.errno == errno.EADDRINUSE:
                self.state = "taken"
                log.info("short address off: port %d is in use by something else", self.port)
            elif e.errno in (errno.EACCES, errno.EPERM, getattr(errno, "WSAEACCES", errno.EACCES)):
                self.state = "no_permission"
                log.info("short address off: no permission to use port %d", self.port)
            else:
                self.state = "failed"
                log.warning("short address off: port %d couldn't be opened: %s", self.port, e)
            self.error = str(e)
            return
        self.state = "on"
        log.info("short address on: port %d hands browsers to port %d", self.port, self.target_port)
        # This computer's own names now; the core's (a claimed domovoi2)
        # in the background, so a core still starting never holds this up.
        self._names = self._compute_names()
        if self._names_task is None or self._names_task.done():
            self._names_task = asyncio.create_task(self._names_loop(), name="short-address-names")

    async def _close(self) -> None:
        task, self._names_task = self._names_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        server, self._server = self._server, None
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server.wait_closed(), 3.0)
            log.info("short address: port %d freed", self.port)

    # This server's names ----------------------------------------------
    def names(self) -> frozenset[str]:
        return self._names

    def set_lan_labels(self, labels: list[str | None]) -> None:
        self._lan_labels = list(labels)
        self._names = self._compute_names()

    def _compute_names(self) -> frozenset[str]:
        hostname = None
        with contextlib.suppress(OSError):
            hostname = socket.gethostname()
        labels = [hostname, getattr(core_settings, "lan_name", None), *self._lan_labels]
        # The operator's own names for this box (TRUSTED_HOSTS), exactly:
        # a "*.example" pattern is no one name and stays out.
        trusted = [h for h in configured_hosts() if "*" not in h]
        # The addresses the core reports for this server (its network-name
        # status): on a Mac install the LAN reaches the Mac's address,
        # redirected into the VM, so the connection's own address isn't it.
        ips = {str(ip) for ip in map(_ip, self._lan_addresses) if ip is not None}
        return server_names(labels, [hostname, *trusted]) | ips

    async def _refresh_names(self) -> None:
        fetch = self.fetch_lan_name
        if fetch is not None:
            try:
                doc = await fetch()
            except Exception as e:  # noqa: BLE001 - names stay as they were
                log.debug("short address: network name unavailable: %s", e)
                doc = None
            if isinstance(doc, dict):
                self._lan_labels = [doc.get("requested"), doc.get("claimed")]
                addrs = doc.get("addresses")
                self._lan_addresses = [str(a) for a in addrs[:16]] if isinstance(addrs, list) else []
        self._names = self._compute_names()

    async def _names_loop(self) -> None:
        while True:
            await self._refresh_names()
            await asyncio.sleep(NAMES_REFRESH_SEC)

    # One connection ---------------------------------------------------
    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._active >= MAX_CONNECTIONS:
            writer.close()
            return
        self._active += 1
        try:
            reply: bytes | None
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), READ_TIMEOUT_SEC)
            except asyncio.LimitOverrunError:
                reply = _reply("431 Request Header Fields Too Large", [], "Request too large.\n", False)
            except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, OSError):
                reply = None
            else:
                sock = writer.get_extra_info("sockname")
                local_ip = sock[0] if isinstance(sock, tuple) and sock else None
                reply, _status = answer(
                    head, local_ip=local_ip, names=self._names,
                    target_port=self.target_port, listen_port=self.port,
                )
            if reply is not None:
                writer.write(reply)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT_SEC)
        finally:
            self._active -= 1
            with contextlib.suppress(Exception):
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), 2.0)


SERVICE = ShortAddress()


async def fetch_lan_name_from_core() -> dict[str, Any] | None:
    """The core's ``GET /v1/lan-name`` on the web's own credential, for
    the names the listener accepts (a claimed ``domovoi2``)."""
    from web.backend.domovoi_client import get_admin, service_auth_headers

    status, doc = await get_admin("/v1/lan-name", timeout=5.0, headers=await service_auth_headers())
    return doc if status == 200 and isinstance(doc, dict) else None


SERVICE.fetch_lan_name = fetch_lan_name_from_core


# ─── the capability stays with this process ───────────────────────────────

_PR_CAP_AMBIENT = 47
_PR_CAP_AMBIENT_CLEAR_ALL = 4


def keep_bind_capability_from_children() -> bool:
    """The unit grants ``CAP_NET_BIND_SERVICE`` as an AMBIENT capability,
    which every program this process starts would inherit too (ffmpeg,
    the office converters, a plugin's helper). Clearing the ambient set
    keeps it in this process's own permitted and effective sets, so the
    switch can bind port 80 again later, and out of every child. Linux
    4.3 and later; elsewhere (or on failure) a no-op. True when cleared.
    Call it from the main thread before anything starts a thread: the
    capability sets are per thread, and new threads copy their creator's."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        rc = libc.prctl(_PR_CAP_AMBIENT, _PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0)
    except Exception:  # noqa: BLE001
        return False
    return rc == 0


__all__ = [
    "SERVICE",
    "ShortAddress",
    "answer",
    "host_allowed",
    "keep_bind_capability_from_children",
    "parse_host",
    "read_enabled",
    "server_names",
    "supported",
    "write_enabled",
]
