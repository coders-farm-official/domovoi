"""The LAN-only check for printer and scanner addresses.

:mod:`domovoi.net_safety` refuses private addresses, because it guards
fetches of internet URLs. Printing needs the opposite: an address the
household typed, or one a printer announced, must be ON the home network
and nowhere else. "Add a printer" must never become a way to reach the
internet, the server itself, or the containers beside it (Postgres,
SearxNG and MPD live on Docker bridge networks, which are RFC 1918 too).

Allowed:

* an IPv4 address inside one of the server's own home subnets
  (:func:`home_networks`): on Linux the subnets routed out of the
  interface that holds the default route (``/proc/net/route``), which
  leaves Docker's bridges out by construction; elsewhere the /24 around the
  server's LAN address (:func:`domovoi.lan_address.lan_ipv4`);
* nothing else. Refused: public addresses, loopback, the server's own
  addresses, the subnet's network and broadcast addresses, link-local
  (169.254/16, which includes cloud metadata), CGNAT, multicast, other
  private ranges that are not the home subnet, and IPv6 (v1 limit: every
  printer we know of also answers on IPv4, and mDNS hands out its A
  record).

Names: an IP literal in any spelling (``net_safety.parse_ip_literal``),
or a LAN name (``printer.local``, a single label like ``office``). A
dotted name that is not ``.local``/``.lan``/``.home.arpa``/``.internal``
is refused BEFORE it is looked up (a lookup is egress too, and under
``INTERNET_ACCESS=never`` it must not happen). A name is resolved once and
every address it gives must pass; the caller then connects to the vetted
address only (:class:`domovoi.printing.http11.Endpoint`), never to the
name again, so DNS rebinding has nothing to rebind.

Ports: IPP on 631 and 443; eSCL on 80, 443, 8080, 8081, 9095 (and 9090,
8082, which some Kyocera and Konica models use). Any other port only when
an admin typed the full URL.

The one escape hatch is the existing operator setting
``OUTBOUND_ALLOW_HOSTS`` (empty by default, :mod:`domovoi.net_safety`):
an entry naming an exact ``host:port`` lets that endpoint through even
though it sits in a refused range, and ONLY for an address an admin typed
(``operator_ok``; the saved row keeps ``operator_allowed``): a printer on
another private subnet, say. An address discovered by mDNS or the sweep,
or typed by a household device, never uses it. An entry without a port
does NOT count.

**Loopback and the server's own addresses are refused whatever the
allowlist says** (owner, 2026-11-16: network printers only, so no local
CUPS or ipp-usb queue, ever): 127.0.0.0/8, ``::1`` (and its IPv4-mapped
spellings), and every address this server holds. :func:`_never` runs
before the allowlist is consulted, on every check, including each later
connection to a saved row, so a row saved before this rule stops working.

The test fakes live on 127.0.0.x. They get in through
:data:`ALLOW_LOOPBACK_UNDER_STUBS`, the repo's usual shape for a test-only
switch (``room_music.RESTORE_AT_STARTUP_IN_STUBS``,
``wake_default.ALLOW_DOWNLOAD_UNDER_STUBS``): a module constant, not a
setting, so neither ``.env`` nor Settings can reach it, and honoured only
while ``USE_STUBS`` is on, which a real install never runs with. A test
sets it with ``monkeypatch``. The bash harness (``test-printing.sh``)
drives the IPP and eSCL clients directly and never passes through this
check.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from domovoi import net_safety

log = logging.getLogger(__name__)

IPP_PORTS = frozenset({631, 443})
ESCL_PORTS = frozenset({80, 443, 8080, 8081, 8082, 9090, 9095})
LAN_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".localdomain")

_REFUSED_V4 = tuple(
    ipaddress.IPv4Network(n)
    for n in (
        "0.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "100.64.0.0/10",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "192.0.0.0/24",
        "198.18.0.0/15",
    )
)
_PRIVATE_V4 = tuple(
    ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)

# Patched by tests.
PROC_ROUTE = Path("/proc/net/route")


class NotOnLan(ValueError):
    """Refused, with a sentence a person can read."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Target:
    """A vetted place to connect: ``address`` is the IP literal to dial;
    ``host`` is the host as written (a name, or the address)."""

    scheme: str        # ipp | ipps | http | https
    host: str
    address: str
    port: int
    path: str

    @property
    def tls(self) -> bool:
        return self.scheme in ("ipps", "https")

    @property
    def uri(self) -> str:
        host = self.host if ":" not in self.host else "[%s]" % self.host
        default = {"ipp": 631, "ipps": 631, "http": 80, "https": 443}[self.scheme]
        port = "" if self.port == default else ":%d" % self.port
        return "%s://%s%s%s" % (self.scheme, host, port, self.path)


# ─── The home network ────────────────────────────────────────────────────


def _linux_routes() -> list[ipaddress.IPv4Network]:
    """Subnets routed out of the interface holding the default route."""
    try:
        lines = PROC_ROUTE.read_text().splitlines()[1:]
    except OSError:
        return []
    rows = []
    for line in lines:
        f = line.split()
        if len(f) < 8:
            continue
        try:
            dest = struct.pack("<I", int(f[1], 16))
            mask = struct.pack("<I", int(f[7], 16))
        except ValueError:
            continue
        rows.append((f[0], socket.inet_ntoa(dest), socket.inet_ntoa(mask)))
    default_ifaces = {iface for iface, dest, mask in rows if dest == "0.0.0.0" and mask == "0.0.0.0"}
    nets = []
    for iface, dest, mask in rows:
        if iface not in default_ifaces or dest == "0.0.0.0":
            continue
        try:
            net = ipaddress.IPv4Network("%s/%s" % (dest, mask), strict=False)
        except ValueError:
            continue
        # Never wider than a /16: a bogus route must not make the whole
        # private range "home".
        if net.prefixlen >= 16 and any(net.subnet_of(p) for p in _PRIVATE_V4):
            nets.append(net)
    return nets


def server_lan_ipv4() -> str | None:
    from domovoi.lan_address import lan_ipv4

    return lan_ipv4()


def home_networks() -> list[ipaddress.IPv4Network]:
    """The server's home subnets (see the module note). Empty when the
    server has no LAN address at all."""
    nets: list[ipaddress.IPv4Network] = []
    if sys.platform.startswith("linux"):
        nets = _linux_routes()
    ip = server_lan_ipv4()
    if ip:
        addr = ipaddress.IPv4Address(ip)
        if not any(addr in n for n in nets) and any(addr in p for p in _PRIVATE_V4):
            nets.append(ipaddress.IPv4Network("%s/24" % ip, strict=False))
    return nets


class OwnAddressesUnknown(NotOnLan):
    """The server's own addresses could not be listed, so no address can be
    shown not to be one of them (CORE-45: fail closed)."""

    def __init__(self) -> None:
        super().__init__(
            "Domovoi couldn't check this server's own addresses just now, so it won't "
            "dial that. Try again in a moment."
        )


def own_addresses() -> set[str]:
    """Every IPv4 address this server holds, on every interface (the LAN
    one, Docker bridges, a VPN), plus what its host name resolves to.

    Raises :class:`OwnAddressesUnknown` when the interfaces can't be read
    (CORE-45, round 6): a short list used to pass the server's other
    addresses as if they were someone else's. IPv6 addresses are not
    listed: :func:`_never` refuses every IPv6 address that isn't an
    IPv4-mapped one, whoever typed it."""
    out: set[str] = set()
    ip = server_lan_ipv4()
    if ip:
        out.add(ip)
    try:
        import psutil

        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET and a.address:
                    out.add(a.address)
    except Exception as e:  # noqa: BLE001 — psutil missing or refused: fail closed
        log.warning("printing: couldn't list this server's addresses: %s", e)
        raise OwnAddressesUnknown() from e
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            out.add(info[4][0])
    except OSError:
        pass
    return out


def _allowlisted(host: str, port: int) -> bool:
    for entry_host, entry_port in net_safety.allow_entries():
        if entry_port is not None and entry_port == port and entry_host == net_safety._canonical_host(host):
            return True
    return False


#: Test-only: let the fakes on 127.0.0.x past :func:`_never`. Honoured only
#: under ``USE_STUBS`` (the test suite); not a setting, so no ``.env`` or
#: Settings value reaches it. Tests set it with ``monkeypatch``.
ALLOW_LOOPBACK_UNDER_STUBS = False

LOOPBACK_REFUSED = "That's this server itself. Domovoi only prints to printers on your network, not USB or local ones."


def _loopback_for_tests() -> bool:
    if not ALLOW_LOOPBACK_UNDER_STUBS:
        return False
    from domovoi.config import settings

    return bool(settings.use_stubs)


IPV6_REFUSED = "Use the printer's IPv4 address (like 192.168.1.50)."


def _never(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    """Refused whatever the operator allowlist says: loopback, the
    server's own addresses (owner, 2026-11-16), and every IPv6 address
    that is not an IPv4-mapped one (CORE-45, round 6).

    The IPv6 rule is the whole of the fix for the server's own IPv6
    addresses: ``fd00::20`` or ``fe80::20`` could be this server, and so
    could ``::127.0.0.1`` (IPv4-compatible), ``64:ff9b::7f00:1`` (NAT64)
    or a 6to4/Teredo form, and the own-address list held IPv4 only, so an
    admin's allowlisted entry let them through. Printers on a home network
    are reached by IPv4; refusing the rest is simpler than proving each
    IPv6 form isn't the server."""
    if ip.version == 6:
        if ip.ipv4_mapped is None:
            if ip.is_unspecified or ip.is_loopback:
                raise NotOnLan(LOOPBACK_REFUSED)
            raise NotOnLan(IPV6_REFUSED)
        ip = ip.ipv4_mapped
    if ip.is_unspecified or (ip.is_loopback and not _loopback_for_tests()):
        raise NotOnLan(LOOPBACK_REFUSED)
    if str(ip) in own_addresses():
        raise NotOnLan("That's this server's own address, not a printer.")


def check_address(address: str, port: int, *, host: str | None = None, operator_ok: bool = False) -> None:
    """Raise :class:`NotOnLan` unless ``address`` may be dialled.

    Loopback and the server's own addresses are refused first, always
    (:func:`_never`). After that, ``operator_ok`` lets the operator's
    ``OUTBOUND_ALLOW_HOSTS`` exact ``host:port`` entries through. Only an
    address an ADMIN typed (and the row saved from it) passes it: anything
    discovered, swept or typed by a household device is held to the
    home-network rules."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as e:
        raise NotOnLan("That isn't an address.") from e
    _never(ip)
    if operator_ok and (_allowlisted(host or address, port) or _allowlisted(address, port)):
        return
    if ip.version == 6:
        mapped = ip.ipv4_mapped
        if mapped is None:   # _never refused it already; kept for clarity
            raise NotOnLan(IPV6_REFUSED)
        ip = mapped
    if any(ip in n for n in _REFUSED_V4) or not any(ip in p for p in _PRIVATE_V4):
        raise NotOnLan("That address isn't on your home network.")
    nets = home_networks()
    home = [n for n in nets if ip in n]
    if not home:
        raise NotOnLan("That address isn't on your home network.")
    for n in home:
        if n.prefixlen <= 30 and ip in (n.network_address, n.broadcast_address):
            raise NotOnLan("That address isn't a device.")
    if str(ip) in own_addresses():
        raise NotOnLan("That's this server's own address, not a printer.")


def needs_operator(target: "Target") -> bool:
    """Whether ``target`` passed only through the operator's allowlist."""
    try:
        check_address(target.address, target.port, host=target.host)
    except NotOnLan:
        return True
    return False


def is_lan_name(host: str) -> bool:
    h = host.strip().lower().rstrip(".")
    if not h or len(h) > 253:
        return False
    if net_safety.parse_ip_literal(h) is not None:
        return True
    if any(c in h for c in "/\\@:% "):
        return False
    return "." not in h or h.endswith(LAN_SUFFIXES)


def resolve(host: str, port: int, *, operator_ok: bool = False) -> str:
    """The one address to dial for ``host``: an IP literal as written, or
    the single lookup of a LAN name, every answer of which must pass."""
    literal = net_safety.parse_ip_literal(host.strip().strip("[]"))
    if literal is not None:
        if literal.version == 6 and literal.ipv4_mapped is not None:
            literal = literal.ipv4_mapped
        check_address(str(literal), port, host=host, operator_ok=operator_ok)
        return str(literal)
    if not is_lan_name(host):
        if operator_ok and _allowlisted(host, port):
            pass
        else:
            raise NotOnLan("That name isn't on your home network. Use the printer's address, like 192.168.1.50.")
    addrs = resolve_name(host)
    v4 = [a for a in addrs if ":" not in a]
    if not v4:
        raise NotOnLan("Couldn't find %s on your network. Try its address instead." % host[:80])
    for a in v4:
        check_address(a, port, host=host, operator_ok=operator_ok)
    return v4[0]


def resolve_name(host: str) -> list[str]:
    """Module-level so tests substitute it."""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    out: list[str] = []
    for info in infos:
        a = info[4][0]
        if a not in out:
            out.append(a)
    return out


# ─── Typed input ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TypedAddress:
    host: str
    port: int | None
    scheme: str | None    # None = an address alone, try IPP then eSCL
    path: str | None
    full_url: bool


def parse_typed(text: str) -> TypedAddress:
    """``192.168.1.50`` / ``printer.local`` / ``192.168.1.50:631`` /
    ``ipp://…/ipp/print`` / ``ipps://…`` / ``http://…/eSCL`` /
    ``https://…/eSCL``."""
    t = (text or "").strip()
    if not t or len(t) > 300 or any(ord(c) < 32 for c in t):
        raise NotOnLan("Type the printer's address, like 192.168.1.50.")
    if "://" in t:
        try:
            u = urlsplit(t)
            port = u.port
        except ValueError as e:
            raise NotOnLan("That address doesn't look right.") from e
        scheme = (u.scheme or "").lower()
        if scheme not in ("ipp", "ipps", "http", "https"):
            raise NotOnLan("Use an ipp://, ipps://, http:// or https:// address.")
        if not u.hostname or u.username or u.password or u.query or u.fragment:
            raise NotOnLan("That address doesn't look right.")
        path = u.path or ("/ipp/print" if scheme.startswith("ipp") else "/eSCL")
        if not path.startswith("/") or ".." in path or any(c in path for c in " \\"):
            raise NotOnLan("That address doesn't look right.")
        return TypedAddress(host=u.hostname, port=port, scheme=scheme, path=path.rstrip("/") or "/", full_url=True)
    host, port = t, None
    if t.count(":") == 1:
        host, _, p = t.partition(":")
        if not p.isdigit() or not 1 <= int(p) <= 65535:
            raise NotOnLan("That port doesn't look right.")
        port = int(p)
    if "/" in host or not host:
        raise NotOnLan("That address doesn't look right.")
    return TypedAddress(host=host, port=port, scheme=None, path=None, full_url=False)


def vet(
    scheme: str, host: str, port: int, path: str, *, allow_any_port: bool = False, operator_ok: bool = False,
) -> Target:
    """A :class:`Target` for this URL, or :class:`NotOnLan`. Discovery and
    household devices pass neither flag; an admin's typed full URL passes
    both (``allow_any_port``, ``operator_ok``)."""
    kind = "ipp" if scheme in ("ipp", "ipps") else "escl"
    allowed = IPP_PORTS if kind == "ipp" else ESCL_PORTS
    if not allow_any_port and port not in allowed and not (operator_ok and _allowlisted(host, port)):
        raise NotOnLan(
            "Domovoi only talks to printers on their usual ports. An admin can type the full address."
        )
    address = resolve(host, port, operator_ok=operator_ok)
    return Target(scheme=scheme, host=host, address=address, port=port, path=path)
