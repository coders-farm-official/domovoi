"""A one-shot mDNS / DNS-SD querier for printers and scanners (RFC 6762,
RFC 6763). Ours, standard library only: python-zeroconf and Avahi are LGPL
(docs/LICENSES.md), and a daemon is the wrong shape for "look once when
someone opens Settings".

It asks, for a few seconds, for the PTR records of ``_ipp._tcp``,
``_ipps._tcp``, ``_uscan._tcp`` and ``_uscans._tcp`` and reads whatever
answers, two ways at once (P0, 2026-10-21):

1. **Group mode first.** Bind UDP 5353 with ``SO_REUSEADDR`` (so it sits
   beside Avahi, Windows' own responder or anything else), join
   224.0.0.251 on the LAN interface and send an ordinary query. Answers
   come back to the group. On the Windows install's WSL distro, WSL's own
   Hyper-V rule (``WslCore-Allow-Inbound-mDNS-IPv4``: inbound UDP to
   224.0.0.251:5353) already lets those in, so no new firewall rule is
   needed.
2. **Legacy unicast second.** A query from an ordinary ephemeral port,
   which a responder MUST answer by unicast to that port (RFC 6762 §6.7).
   It needs no group membership, so it still works where binding 5353
   fails; but no Hyper-V rule admits it, so on the Windows install it is
   the one most likely to be dropped.

The result says which socket heard each device, which is what Settings
shows as "by multicast / by unicast" — the owner's live install proves the
question P0-A left open.

Every TXT value is a hint only: what is stored comes from the printer's
own Get-Printer-Attributes / ScannerCapabilities answer. Names and TXT
values are capped and stripped of control characters; parsing bounds every
loop (name-pointer depth, record counts) so a hostile packet costs nothing.
"""

from __future__ import annotations

import logging
import select
import socket
import struct
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

GROUP = "224.0.0.251"
PORT = 5353
SERVICE_TYPES = ("_ipp._tcp.local", "_ipps._tcp.local", "_uscan._tcp.local", "_uscans._tcp.local")
MAX_PACKET = 9000
MAX_RECORDS = 200
MAX_NAME_DEPTH = 16
MAX_TXT = 1023

T_A, T_PTR, T_TXT, T_AAAA, T_SRV = 1, 12, 16, 28, 33


def _qname(name: str) -> bytes:
    out = b""
    for label in name.split("."):
        raw = label.encode("utf-8")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def build_query(qid: int = 0, unicast_response: bool = False) -> bytes:
    """One packet asking PTR (IN) for every service type. ``unicast_response``
    sets the QU bit (not needed for a legacy-unicast query: the source port
    already says so)."""
    qclass = 0x8001 if unicast_response else 0x0001
    hdr = struct.pack("!HHHHHH", qid, 0, len(SERVICE_TYPES), 0, 0, 0)
    return hdr + b"".join(_qname(t) + struct.pack("!HH", T_PTR, qclass) for t in SERVICE_TYPES)


class MdnsParseError(ValueError):
    pass


def _clean(s: str, cap: int = 255) -> str:
    return "".join(ch for ch in s[:cap] if ch >= " " and ch != "\x7f")


def read_name(data: bytes, off: int, depth: int = 0) -> tuple[str, int]:
    """A (possibly compressed) domain name at ``off``: (name, offset after it)."""
    labels: list[str] = []
    end = None
    jumps = 0
    while True:
        if off >= len(data):
            raise MdnsParseError("name runs past the packet")
        ln = data[off]
        if ln == 0:
            off += 1
            break
        if ln & 0xC0 == 0xC0:
            if off + 1 >= len(data):
                raise MdnsParseError("truncated pointer")
            ptr = struct.unpack("!H", data[off:off + 2])[0] & 0x3FFF
            if end is None:
                end = off + 2
            jumps += 1
            if jumps > MAX_NAME_DEPTH or ptr >= len(data):
                raise MdnsParseError("bad name pointer")
            off = ptr
            continue
        if ln & 0xC0:
            raise MdnsParseError("bad label")
        if off + 1 + ln > len(data):
            raise MdnsParseError("label runs past the packet")
        labels.append(data[off + 1:off + 1 + ln].decode("utf-8", "replace"))
        off += 1 + ln
        if len(labels) > 64:
            raise MdnsParseError("too many labels")
    return _clean(".".join(labels), 400), (end if end is not None else off)


@dataclass
class Record:
    name: str
    rtype: int
    value: object


def parse_packet(data: bytes) -> tuple[bool, list[Record]]:
    """(is_response, records) of one mDNS packet."""
    if len(data) < 12:
        raise MdnsParseError("short packet")
    flags = struct.unpack("!H", data[2:4])[0]
    qd, an, ns, ar = struct.unpack("!HHHH", data[4:12])
    if qd + an + ns + ar > MAX_RECORDS:
        raise MdnsParseError("too many records")
    off = 12
    for _ in range(qd):
        _, off = read_name(data, off)
        off += 4
    out: list[Record] = []
    for _ in range(an + ns + ar):
        name, off = read_name(data, off)
        if off + 10 > len(data):
            raise MdnsParseError("truncated record")
        rtype, _cls, _ttl, rdlen = struct.unpack("!HHIH", data[off:off + 10])
        off += 10
        if off + rdlen > len(data):
            raise MdnsParseError("record data runs past the packet")
        rd = data[off:off + rdlen]
        value: object = None
        if rtype == T_PTR:
            value = read_name(data, off)[0]
        elif rtype == T_SRV and rdlen >= 7:
            port = struct.unpack("!H", rd[4:6])[0]
            value = (read_name(data, off + 6)[0], port)
        elif rtype == T_A and rdlen == 4:
            value = socket.inet_ntoa(rd)
        elif rtype == T_AAAA and rdlen == 16:
            value = socket.inet_ntop(socket.AF_INET6, rd)
        elif rtype == T_TXT:
            items: dict[str, str] = {}
            i = 0
            while i < len(rd):
                ln = rd[i]
                item = rd[i + 1:i + 1 + ln].decode("utf-8", "replace")
                i += 1 + ln
                if not item:
                    continue
                k, _, v = item.partition("=")
                key = _clean(k, 64).lower()
                if key and key not in items:
                    items[key] = _clean(v, MAX_TXT)
            value = items
        if value is not None:
            out.append(Record(name=name, rtype=rtype, value=value))
        off += rdlen
    return bool(flags & 0x8000), out


@dataclass
class Service:
    instance: str            # "Brother HL-L3220CDW._ipp._tcp.local"
    service_type: str        # "_ipp._tcp"
    target: str | None = None
    port: int | None = None
    txt: dict[str, str] = field(default_factory=dict)
    addresses: list[str] = field(default_factory=list)
    source: str | None = None   # the packet's source address
    heard_on: set[str] = field(default_factory=set)

    @property
    def address(self) -> str | None:
        return self.addresses[0] if self.addresses else self.source

    @property
    def label(self) -> str:
        """The instance name without its service type."""
        suffix = "." + self.service_type + ".local"
        name = self.instance[: -len(suffix)] if self.instance.endswith(suffix) else self.instance
        return name.replace("\\032", " ").replace("\\.", ".")[:120]


@dataclass
class BrowseResult:
    services: list[Service]
    heard: dict[str, set[str]]      # source address -> {"multicast", "unicast"}
    errors: list[str]


def collect(packets: list[tuple[bytes, str, str]]) -> BrowseResult:
    """Fold (data, source address, socket kind) packets into services."""
    services: dict[str, Service] = {}
    hosts: dict[str, list[str]] = {}
    heard: dict[str, set[str]] = {}
    errors: list[str] = []
    parsed: list[tuple[list[Record], str, str]] = []
    for data, src, kind in packets:
        try:
            is_response, records = parse_packet(data)
        except MdnsParseError as e:
            log.debug("mdns: dropped a packet from %s: %s", src, e)
            continue
        if is_response:
            parsed.append((records, src, kind))
    # PTRs first, from every packet: a responder may send the SRV/TXT in
    # an earlier packet than the PTR that names the instance.
    relevant_sources: set[tuple[str, str]] = set()
    for records, src, kind in parsed:
        for r in records:
            if r.rtype == T_PTR and r.name.lower() in SERVICE_TYPES:
                stype = r.name.lower()[: -len(".local")]
                svc = services.setdefault(str(r.value).lower(), Service(instance=str(r.value), service_type=stype))
                svc.heard_on.add(kind)
                svc.source = svc.source or src
                relevant_sources.add((src, kind))
    for src, kind in relevant_sources:
        heard.setdefault(src, set()).add(kind)
    for records, src, kind in parsed:
        for r in records:
            key = r.name.lower()
            if r.rtype == T_SRV and key in services:
                services[key].target, services[key].port = r.value  # type: ignore[misc]
                services[key].heard_on.add(kind)
            elif r.rtype == T_TXT and key in services:
                services[key].txt.update(r.value)  # type: ignore[arg-type]
            elif r.rtype == T_A:
                hosts.setdefault(key, [])
                if r.value not in hosts[key]:
                    hosts[key].append(str(r.value))
    for svc in services.values():
        if svc.target:
            svc.addresses = list(hosts.get(svc.target.lower(), []))
    return BrowseResult(services=[s for s in services.values() if s.port], heard=heard, errors=errors)


def browse(iface_ip: str | None, seconds: float = 3.0, modes: tuple[str, ...] = ("group", "legacy")) -> BrowseResult:
    """Ask for ``seconds`` (blocking; run it in a thread). ``iface_ip`` is
    the server's LAN address: queries go out of that interface
    (``IP_MULTICAST_IF``) — out of the default route they can reach
    nothing (measured on the Windows host, 2026-10-21)."""
    socks: dict[str, socket.socket] = {}
    errors: list[str] = []
    if "group" in modes:
        try:
            m = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            try:
                m.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if hasattr(socket, "SO_REUSEPORT"):
                    try:
                        m.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                    except OSError:
                        pass
                m.bind(("", PORT))
                mreq = socket.inet_aton(GROUP) + socket.inet_aton(iface_ip or "0.0.0.0")
                m.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                m.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
                if iface_ip:
                    m.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface_ip))
                m.setblocking(False)
                socks["multicast"] = m
            except OSError:
                m.close()
                raise
        except OSError as e:
            errors.append("multicast: couldn't listen on port 5353 (%s)" % (e.strerror or type(e).__name__))
    if "legacy" in modes:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            try:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
                if iface_ip:
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface_ip))
                s.bind((iface_ip or "", 0))
                s.setblocking(False)
                socks["unicast"] = s
            except OSError:
                s.close()
                raise
        except OSError as e:
            errors.append("unicast: couldn't open a socket (%s)" % (e.strerror or type(e).__name__))
    packets: list[tuple[bytes, str, str]] = []
    query = build_query()
    sends = [0.0, 1.0, 2.0]
    t0 = time.monotonic()
    try:
        while socks and time.monotonic() - t0 < seconds:
            elapsed = time.monotonic() - t0
            while sends and elapsed >= sends[0]:
                sends.pop(0)
                for kind, sock in socks.items():
                    try:
                        sock.sendto(query, (GROUP, PORT))
                    except OSError as e:
                        msg = "%s: couldn't send (%s)" % (kind, e.strerror or type(e).__name__)
                        if msg not in errors:
                            errors.append(msg)
            ready, _, _ = select.select(list(socks.values()), [], [], 0.1)
            for sock in ready:
                kind = next(k for k, v in socks.items() if v is sock)
                try:
                    data, addr = sock.recvfrom(MAX_PACKET)
                except OSError:
                    continue
                if len(packets) < 2000:
                    packets.append((data, addr[0], kind))
    finally:
        for sock in socks.values():
            sock.close()
    result = collect(packets)
    result.errors.extend(errors)
    return result
