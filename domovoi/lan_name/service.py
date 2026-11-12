"""Who announces the network name, and what Settings is told about it.

The core owns the CHOICE (``LAN_NAME`` / ``LAN_NAME_ENABLED`` in ``.env``,
checked by :mod:`domovoi.lan_name.names`); whoever is on the home network
ANNOUNCES it (:func:`mode`):

* ``core`` — a Linux server: this process, with its own socket on UDP 5353
  (:class:`LanNameService`, :mod:`domovoi.lan_name.responder`). The name
  it held is kept in ``~/.domovoi/lan_name/state.json`` and tried first at
  the next start (``domovoi2`` stays ``domovoi2``).
* ``host`` — the Windows install: the core runs in a private WSL distro
  that isn't the one answering on the network, so DomovoiHost announces
  (``windows/common/MdnsResponder.cs``). The core writes
  ``~/.domovoi/lan_name/request.json``::

      {"v": 1, "id": "<32 hex>", "name": "kitchen", "enabled": true, "at": "<UTC>"}

  DomovoiHost reads it as the service user every 5 s, checks it again,
  probes, and writes ``/var/lib/domovoi-windows/lan_name.json`` as root::

      {"schema": 1, "request_id": "<id it acted on>"|null, "requested": "kitchen",
       "claimed": "domovoi2"|null, "state": "announced"|"held"|"checking"|"taken"|"off"|"no_lan"|"failed"|"public",
       "taken": "kitchen"|null, "addresses": ["192.168.1.30"], "error": null|"...",
       "written_at": "<UTC>"}

  believed only while root owns it and its folder alone (as ``limits.json``
  is). The Mac (phase 4) adds ``("mac", "/var/lib/domovoi-mac/lan_name.json")``
  to :data:`REPORT_FILES`; its helper reads the same request file.
* ``none`` — anything else (a development box on Windows or a Mac, WSL
  without DomovoiHost): nothing announces, Settings says so (``no_host``).
  A DomovoiHost from before this feature writes no report: also ``no_host``.

Phase 5 (DNS-SD ``_domovoi._tcp`` / ``_http._tcp``) adds service records
beside the host record in the responder and the C# copy; nothing here
changes for it but the snapshot.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import socket
import stat
import struct
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from domovoi.lan_name import names, packet
from domovoi.lan_name.responder import STATES, Out, Responder, lan_address_ok

log = logging.getLogger(__name__)

REPORT_FILES: tuple[tuple[str, str], ...] = (("windows", "/var/lib/domovoi-windows/lan_name.json"),)
_REPORT_MAX_BYTES = 4096
_STATE_MAX_BYTES = 4096
REQUEST_NAME = "request.json"
STATE_NAME = "state.json"
#: The host reads the request this often (MdnsResponder.cs, LanNameHost.Every).
HOST_POLL_SEC = 5
#: How often the core looks at the settings and the LAN address again.
RECHECK_SEC = 5.0
#: How long after a failed bind the socket is tried again.
REBIND_SEC = 60.0

HELP = "a name on your network isn't proof — satellites still check the server's key"


def state_dir() -> Path:
    return Path(os.environ.get("DOMOVOI_LAN_NAME_DIR") or (Path.home() / ".domovoi" / "lan_name"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def requested_name(raw: object | None = None) -> str:
    """The setting as a usable label; the default when it isn't one."""
    from domovoi.config import settings

    try:
        return names.normalize(settings.lan_name if raw is None else raw)
    except names.BadName:
        return names.DEFAULT


def enabled() -> bool:
    from domovoi.config import settings

    return bool(settings.lan_name_enabled)


# ─── who announces ──────────────────────────────────────────────────────


def _root_only(st: os.stat_result) -> bool:
    """Owned by root, writable by nobody else; nothing passes where there
    are no POSIX owners (the tests replace this)."""
    if not hasattr(os, "getuid"):
        return False
    return st.st_uid == 0 and not (st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def _report_path(kind: str, default: str) -> Path:
    return Path(os.environ.get(f"DOMOVOI_LAN_NAME_REPORT_{kind.upper()}") or default)


def host_side() -> str | None:
    """``windows`` when DomovoiHost runs this install (its ``side.json``),
    else None."""
    from domovoi import windows_side

    return "windows" if windows_side.read_side() is not None else None


def mode() -> str:
    """``core``, ``host`` or ``none`` (module docstring)."""
    if host_side() is not None:
        return "host"
    from domovoi.host_kind import host_kind

    return "core" if host_kind() == "linux" else "none"


# ─── the host's report ──────────────────────────────────────────────────


def _read_root_file(path: Path, limit: int) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning("network name: can't open %s: %s", path, e)
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not _root_only(st) or not _root_only(os.stat(path.parent)):
            log.warning("network name: %s isn't root's alone; not believing it", path)
            return None
        if st.st_size > limit:
            return None
        return os.read(fd, limit + 1)
    except OSError as e:
        log.warning("network name: can't read %s: %s", path, e)
        return None
    finally:
        os.close(fd)


def _label_or_none(v: object) -> str | None:
    if v is None:
        return None
    try:
        n = names.normalize(v)
    except names.BadName:
        return None
    return n if n == v else None


def _short(v: object, n: int = 200) -> str | None:
    return v if isinstance(v, str) and 0 < len(v) <= n and not any(ord(c) < 32 or ord(c) == 127 for c in v) else None


def parse_report(data: bytes) -> dict[str, Any] | None:
    """A host report's text, every field checked; None when it can't be
    believed as a whole."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        return None
    state = doc.get("state")
    if state not in STATES:
        return None
    requested = _label_or_none(doc.get("requested"))
    if requested is None:
        return None
    rid = doc.get("request_id")
    addrs = doc.get("addresses")
    if not isinstance(addrs, list) or len(addrs) > 4 or not all(isinstance(a, str) and lan_address_ok(a) for a in addrs):
        return None
    return {
        "request_id": rid if isinstance(rid, str) and len(rid) == 32 and all(c in "0123456789abcdef" for c in rid) else None,
        "requested": requested,
        "claimed": _label_or_none(doc.get("claimed")),
        "state": state,
        "taken": _label_or_none(doc.get("taken")),
        "addresses": list(addrs),
        "error": _short(doc.get("error")),
        "written_at": _short(doc.get("written_at"), 40),
    }


def read_report() -> tuple[str, dict[str, Any]] | None:
    for kind, default in REPORT_FILES:
        data = _read_root_file(_report_path(kind, default), _REPORT_MAX_BYTES)
        if data is None:
            continue
        doc = parse_report(data)
        if doc is not None:
            return kind, doc
    return None


# ─── the request (host mode) ────────────────────────────────────────────


def read_request() -> dict[str, Any] | None:
    try:
        raw = (state_dir() / REQUEST_NAME).read_bytes()[:_STATE_MAX_BYTES + 1]
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(doc, dict) or doc.get("v") != 1:
        return None
    name = _label_or_none(doc.get("name"))
    rid = doc.get("id")
    if name is None or not isinstance(rid, str) or not isinstance(doc.get("enabled"), bool):
        return None
    return {"id": rid, "name": name, "enabled": doc["enabled"]}


def _write_private(name: str, doc: dict[str, Any]) -> None:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    tmp = d / f".{name}.{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write((json.dumps(doc) + "\n").encode("ascii"))
    os.replace(tmp, d / name)


def sync_request(*, force: bool = False) -> dict[str, Any]:
    """Make the request say what the settings say; a new id only when it
    changed (or ``force``: the household pressed Save again). Returns it."""
    want = {"name": requested_name(), "enabled": enabled()}
    have = read_request()
    if have and not force and have["name"] == want["name"] and have["enabled"] == want["enabled"]:
        return have
    doc = {"v": 1, "id": secrets.token_hex(16), **want, "at": _now_iso()}
    _write_private(REQUEST_NAME, doc)
    log.info("network name: asked the host for %s.local (%s)", want["name"], "on" if want["enabled"] else "off")
    return {"id": doc["id"], **want}


# ─── the name held last time (core mode) ────────────────────────────────


def read_state() -> dict[str, Any]:
    try:
        raw = (state_dir() / STATE_NAME).read_bytes()[:_STATE_MAX_BYTES + 1]
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(doc, dict) or doc.get("v") != 1:
        return {}
    return {"requested": _label_or_none(doc.get("requested")), "claimed": _label_or_none(doc.get("claimed"))}


def write_state(requested: str, claimed: str) -> None:
    try:
        _write_private(STATE_NAME, {"v": 1, "requested": requested, "claimed": claimed, "at": _now_iso()})
    except OSError as e:
        log.warning("network name: can't save the name held: %s", e)


# ─── the socket (core mode, Linux) ──────────────────────────────────────

#: Interfaces a name is never announced on (security review 2026-11-12,
#: L5): tunnels, VPNs and container or VM bridges. Besides these names, any
#: interface under /sys/devices/virtual (a bridge, veth, dummy, tun) counts
#: as virtual too.
VIRTUAL_PREFIXES = ("tun", "tap", "wg", "ppp", "docker", "veth", "br-", "virbr", "lxc", "lxd", "vnet",
                    "zt", "tailscale", "cni", "flannel", "cali", "vxlan", "dummy", "lo")

_SIOCGIFADDR = 0x8915
_SIOCGIFNETMASK = 0x891B
_IP_PKTINFO = getattr(socket, "IP_PKTINFO", 8)
#: Room for one in_pktinfo control message (Linux; the tests run anywhere).
_ANC_SPACE = socket.CMSG_SPACE(12) if hasattr(socket, "CMSG_SPACE") else 64


@dataclass(frozen=True)
class Iface:
    name: str
    index: int
    prefix: int | None
    #: Why a name can't go on it (a tunnel, a bridge), or None.
    virtual: str | None = None


def is_virtual(name: str, sys_class_net: Path = Path("/sys/class/net")) -> str | None:
    if name.startswith(VIRTUAL_PREFIXES):
        return name
    try:
        if "/devices/virtual/" in os.path.realpath(sys_class_net / name):
            return name
    except OSError:
        pass
    return None


def lan_interface(ip: str) -> Iface | None:
    """The interface that holds ``ip`` (Linux): its name, index, prefix and
    whether it is virtual. None elsewhere, or when none holds it."""
    if not sys.platform.startswith("linux"):
        return None
    import fcntl

    want = socket.inet_aton(ip)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for index, ifname in socket.if_nameindex()[:256]:
            req = struct.pack("256s", ifname.encode()[:15])
            try:
                if fcntl.ioctl(s.fileno(), _SIOCGIFADDR, req)[20:24] != want:
                    continue
                mask = fcntl.ioctl(s.fileno(), _SIOCGIFNETMASK, req)[20:24]
            except OSError:
                continue
            bits = int.from_bytes(mask, "big")
            prefix = bin(bits).count("1") if bits else None
            return Iface(ifname, index, prefix, is_virtual(ifname))
    finally:
        s.close()
    return None


def open_socket(lan_ip: str) -> socket.socket:
    """UDP 5353 beside any other responder on the box (SO_REUSEADDR: Avahi
    sets it too), the group joined on the LAN address only, multicast sent
    from it only (the spike: without IP_MULTICAST_IF, packets leave by a
    virtual adapter), and each packet's arriving interface reported
    (IP_PKTINFO), so one from another interface (a container's unicast to
    the docker0 address, say) is dropped."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if sys.platform.startswith("linux"):
            try:
                s.setsockopt(socket.IPPROTO_IP, 49, 0)   # IP_MULTICAST_ALL off: our group only
            except OSError:
                pass
            s.setsockopt(socket.IPPROTO_IP, _IP_PKTINFO, 1)
        s.bind(("", packet.PORT))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                     socket.inet_aton(packet.GROUP) + socket.inet_aton(lan_ip))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(lan_ip))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        s.setblocking(False)
    except BaseException:
        s.close()
        raise
    return s


def arrived_on(ancdata: list) -> int | None:
    """The interface index from an IP_PKTINFO control message, or None."""
    for level, kind, data in ancdata:
        if level == socket.IPPROTO_IP and kind == _IP_PKTINFO and len(data) >= 4:
            return struct.unpack("i", data[:4])[0]
    return None


class SocketWire:
    """The real socket on the event loop: read with recvmsg (at most
    :data:`READS_PER_WAKE` packets a wake-up), a packet that arrived on
    another interface than ``ifindex`` dropped unread."""

    READS_PER_WAKE = 64

    def __init__(self, sock: socket.socket, ifindex: int | None, on_packet) -> None:
        self.sock = sock
        self.ifindex = ifindex
        self.on_packet = on_packet
        self.loop = asyncio.get_running_loop()
        self.loop.add_reader(sock.fileno(), self._readable)

    def _readable(self) -> None:
        for _ in range(self.READS_PER_WAKE):
            try:
                data, anc, flags, addr = self.sock.recvmsg(packet.MAX_PACKET + 1, _ANC_SPACE)
            except (BlockingIOError, InterruptedError):
                return
            except OSError as e:
                log.debug("network name: receive failed: %s", e)
                return
            if flags & getattr(socket, "MSG_TRUNC", 0) or not isinstance(addr, tuple):
                continue
            if self.ifindex is not None and arrived_on(anc) != self.ifindex:
                continue
            self.on_packet(data, (str(addr[0]), int(addr[1])))

    def sendto(self, data: bytes, dest: tuple[str, int]) -> None:
        self.sock.sendto(data, dest)

    def close(self) -> None:
        try:
            self.loop.remove_reader(self.sock.fileno())
        except (ValueError, OSError):
            pass
        self.sock.close()


def open_wire(ip: str, iface: Iface | None, on_packet) -> SocketWire:
    return SocketWire(open_socket(ip), iface.index if iface else None, on_packet)


class LanNameService:
    """The core-mode announcer: one socket, one :class:`Responder`, one
    task. ``lan_ip``, ``iface`` and ``wire`` are for the tests."""

    def __init__(self, *, lan_ip=None, iface=None, wire=open_wire, clock=time.monotonic) -> None:
        self._lan_ip = lan_ip or _default_lan_ip
        self._iface = iface or lan_interface
        self._open = wire
        self._clock = clock
        self.responder = Responder()
        self._task: asyncio.Task | None = None
        self._wire = None
        self._ip: str | None = None
        self._wake = asyncio.Event()
        self._saved: tuple[str, str] | None = None
        self._kick = False
        self.running = False

    def start(self) -> None:
        if self._task is None:
            self.running = True
            self._task = asyncio.get_running_loop().create_task(self._run(), name="lan-name")

    def kick(self) -> None:
        """Look at the settings now (a Save in Settings)."""
        self._kick = True
        self._wake.set()

    async def stop(self) -> None:
        self.running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - stopping
                pass
            self._task = None
        self._emit(self.responder.stop(self._clock()))
        self._close()

    def snapshot(self) -> dict[str, Any]:
        snap = self.responder.snapshot()
        snap["addresses"] = [self._ip] if self._ip and snap["claimed"] else []
        return snap

    # ─── internals ─────────────────────────────────────────────────────

    def _close(self) -> None:
        if self._wire is not None:
            self._wire.close()
            self._wire = None

    def _emit(self, outs: list[Out]) -> None:
        w = self._wire
        if w is None:
            return
        for o in outs:
            try:
                w.sendto(o.data, o.dest or (packet.GROUP, packet.PORT))
            except OSError as e:
                log.debug("network name: send failed: %s", e)
        self._save_claim()

    def _save_claim(self) -> None:
        r = self.responder
        if r.claimed and r.requested and r.state in ("announced", "taken", "held") and r.phase in ("announced", "held"):
            pair = (r.requested, r.claimed)
            if pair != self._saved:
                self._saved = pair
                write_state(*pair)
                log.info("network name: %s.local (%s)", r.claimed, r.state)

    def _packet(self, data: bytes, src: tuple[str, int]) -> None:
        self._emit(self.responder.on_packet(data, src, self._clock()))
        self._wake.set()

    def _ensure_wire(self, ip: str | None, iface: Iface | None) -> None:
        if ip == self._ip and self._wire is not None:
            return
        self._close()
        self._ip = ip
        if ip is None:
            return
        self._wire = self._open(ip, iface, self._packet)

    async def _run(self) -> None:
        sticky = read_state().get("claimed")
        failed_at: float | None = None
        last_check = -1e9
        while True:
            now = self._clock()
            if now - last_check >= RECHECK_SEC:
                last_check = now
                ip = await asyncio.to_thread(self._lan_ip)
                ip = ip if lan_address_ok(ip) else None
                iface = await asyncio.to_thread(self._iface, ip) if ip else None
                why_not = None
                if iface is not None and iface.virtual:
                    why_not = f"the default route goes through {iface.virtual}, a virtual or tunnel interface"
                    ip = None
                want_on = enabled()
                if failed_at is None or now - failed_at >= REBIND_SEC:
                    if self._ip is not None and self._ip != (ip if want_on else None):
                        # A new address, or none: the goodbye leaves by the
                        # old socket first.
                        self._emit(self.responder.stop(now))
                    try:
                        self._ensure_wire(ip if want_on else None, iface)
                        failed_at = None
                    except OSError as e:
                        failed_at = now
                        self._ip = None
                        self.responder.fail(f"can't listen on UDP 5353: {e.strerror or e}")
                        log.warning("network name: can't listen on UDP 5353: %s", e)
                if failed_at is None:
                    old = self.responder
                    outs = old.configure(requested_name(), want_on, ip, now, sticky=sticky,
                                         prefix=iface.prefix if iface else None)
                    if why_not and old.phase == "no_lan":
                        old.error = why_not
                    sticky = None if old.claimed else sticky
                    self._emit(outs)
            self._emit(self.responder.on_timer(self._clock()))
            nxt = self.responder.next_at
            wait = RECHECK_SEC if nxt is None else max(0.0, min(RECHECK_SEC, nxt - self._clock()))
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
            if self._kick:
                # A Save: look at the settings at once.
                self._kick = False
                last_check = -1e9


def _default_lan_ip() -> str | None:
    from domovoi import lan_address

    return lan_address.probe_lan_ipv4() or None


SERVICE: LanNameService | None = None


async def start() -> None:
    """At the core's start: the announcer on Linux, the request on the
    Windows install, nothing elsewhere. Never raises."""
    global SERVICE
    try:
        m = await asyncio.to_thread(mode)
        if m == "host":
            await asyncio.to_thread(sync_request)
        elif m == "core":
            SERVICE = LanNameService()
            SERVICE.start()
    except Exception:  # noqa: BLE001 - a name must never stop the core
        log.exception("network name: could not start")


async def stop() -> None:
    global SERVICE
    if SERVICE is not None:
        svc, SERVICE = SERVICE, None
        await svc.stop()


def status() -> dict[str, Any]:
    """``GET /v1/lan-name``: ``{requested, claimed, fqdn, state, taken,
    error, enabled, host_kind, addresses, hint, help}``."""
    requested = requested_name()
    on = enabled()
    m = mode()
    out: dict[str, Any] = {"requested": requested, "enabled": on, "hint": names.HINT, "help": HELP}
    if m == "core":
        out["host_kind"] = "linux"
        if SERVICE is None:
            snap = {"claimed": None, "state": "off" if not on else "failed", "taken": None,
                    "error": None if not on else "the announcer isn't running", "addresses": []}
        else:
            snap = SERVICE.snapshot()
            if snap.get("requested") not in (None, requested) or (snap["state"] == "off" and on):
                snap["state"] = "checking"
        out.update({k: snap.get(k) for k in ("claimed", "state", "taken", "error", "addresses")})
    elif m == "host":
        rep = read_report()
        out["host_kind"] = rep[0] if rep else "windows"
        req = read_request()
        if rep is None:
            out.update({"claimed": None, "state": "no_host", "taken": None, "addresses": [],
                        "error": "this version of the Windows side doesn't announce a name; update it"})
        else:
            doc = rep[1]
            out.update({k: doc[k] for k in ("claimed", "state", "taken", "error", "addresses")})
            if req is not None and req["id"] != doc["request_id"]:
                out["state"] = "checking"
    else:
        from domovoi.host_kind import host_kind

        out.update({"host_kind": host_kind(), "claimed": None, "state": "no_host", "taken": None,
                    "addresses": [], "error": "nothing on this machine announces a name"})
    out["fqdn"] = names.fqdn(out.get("claimed"))
    return out


def claimed_name() -> str | None:
    """The name held now, ``domovoi2.local`` style; None when none
    (``ready.server_names`` puts it first)."""
    try:
        st = status()
    except Exception:  # noqa: BLE001 - never break a satellite's hello
        return None
    if st.get("state") in ("announced", "held", "taken", "checking") and st.get("claimed"):
        return names.fqdn(st["claimed"])
    return None


def save(name: object | None, on: bool | None) -> dict[str, Any]:
    """``PUT /v1/lan-name``: check, write ``.env``, apply. :class:`names.BadName`
    for a name that can't be one."""
    from domovoi.config import settings
    from domovoi.config_env_writer import write_env_values

    changes: dict[str, Any] = {}
    if name is not None:
        changes["lan_name"] = names.normalize(name)
    if on is not None:
        changes["lan_name_enabled"] = bool(on)
    if changes:
        write_env_values(changes)
        for k, v in changes.items():
            setattr(settings, k, v)
    if mode() == "host":
        sync_request(force=bool(changes))
    elif SERVICE is not None:
        SERVICE.kick()
    return status()
