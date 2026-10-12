"""Find the Domovoi server on the local network.

A satellite onboarded through the Wi-Fi portal has no way to learn the
server's address: the core is not a participant in that adoption, and the
customer shouldn't have to know their server's IP. So the config carries the
sentinel ``auto`` and this module resolves it.

The order (since 2026-10-12, :func:`locate`):

1. **The core's name** — ``domovoi.local`` and any name the core told this
   device about in its ``ready`` frame (:func:`server_names`). A name is
   only a hint: it is resolved HERE, to addresses, and each address is
   dialled and held to the pinned server identity like any other, so the
   proof is bound to the address actually dialled and a name that resolves
   to an impostor gets nowhere. This is what keeps a satellite with the
   core when the core's DHCP lease moves it to a new address.
2. **The last-known address** — the one config.toml holds (or the pending
   sidecar, before approval), verified the same way.
3. **The /24 sweep**, only as a last resort: ``GET /v1/health`` on every
   address of the interface's own subnet, keeping the host that answers as
   this device's core.

mDNS is not load-bearing anywhere in that list: multicast over Wi-Fi is the
thing that fails at 3 a.m. (multicast-to-unicast conversion, mesh backhauls
and guest-VLAN isolation all break it invisibly), so a name that does not
resolve simply falls through to the address that worked last time, and then
to the sweep. What worked is persisted by the caller (config.toml for an
approved device, the pending sidecar before that).
"""

from __future__ import annotations

import functools
import ipaddress
import json
import logging
import re
import socket
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

AUTO = "auto"
CORE_PORT = 6370
HEALTH_PATH = "/v1/health"

#: Tried first, before any name the core advertised: the name a stock
#: install answers to when its host is called ``domovoi`` (avahi publishes
#: ``<hostname>.local``).
DEFAULT_NAMES: tuple[str, ...] = ("domovoi.local",)
#: Names the core advertised in its ``ready`` frame (``server_names``).
SERVER_NAMES_PATH = Path("~/.domovoi").expanduser() / "server-names.json"
#: At most this many advertised names are kept.
MAX_NAMES = 4
_NAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")
# A name lookup that has not answered by then is a name that will not
# (nss-mdns gives up on its own after a few seconds anyway).
_NAME_TIMEOUT_SEC = 3.0
# One host, not a sweep: it may take its time to answer.
_SINGLE_PROBE_TIMEOUT_SEC = 2.0
# What resolves a name when the caller passes no resolver. A module
# attribute so the test suite can refuse real lookups (satellite/tests/
# conftest.py): resolving domovoi.local on a developer's machine asks the
# developer's actual house.
_DEFAULT_RESOLVER: Callable[..., Any] = socket.getaddrinfo

# A home is a /24. Anything wider isn't worth 16k probes — fall back to
# asking rather than hammering someone's network for a minute.
MAX_HOSTS = 1024
_PROBE_TIMEOUT_SEC = 0.3
_WORKERS = 32


def local_ipv4() -> str | None:
    """This host's LAN address, without needing a route to the internet.
    The UDP connect() is address selection only — no packet is sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))   # TEST-NET-1: guaranteed unroutable
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def candidate_hosts(ip: str, prefix: int = 24) -> list[str]:
    """Every usable host address on ``ip``'s network, nearest-first.

    Ordering matters more than it looks: a home core is very often the
    router's first DHCP lease or a low reservation, so sorting by distance
    from our own address finds it in a handful of probes rather than 200.
    """
    try:
        net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    except ValueError:
        return []
    if net.num_addresses > MAX_HOSTS:
        log.warning("network /%d is too large to sweep — asking instead", prefix)
        return []
    me = ipaddress.ip_address(ip)
    hosts = [h for h in net.hosts() if h != me]
    hosts.sort(key=lambda h: abs(int(h) - int(me)))
    return [str(h) for h in hosts]


def probe(host: str, *, port: int = CORE_PORT, timeout: float = _PROBE_TIMEOUT_SEC,
          opener=None, expected_fingerprint: str | None = None) -> bool:
    """Whether ``host`` answers /v1/health as **this device's** Domovoi core.

    The bot_name check keeps us from adopting some unrelated service that
    happens to be listening on 6370. It is not, on its own, evidence of
    anything: a host that wants to be adopted can answer with it. That is
    what ``expected_fingerprint`` is for — the fingerprint baked into this
    device's image. When one is pinned, the host also has to sign a nonce
    this call just invented with the matching key, so answering the right
    way is no longer enough — and the signature has to cover the address
    we dialed (``host:port``), so a host that forwards our question to the
    real core and hands back its answer is not a candidate either: the
    core will not sign for an address that is not its own.

    With nothing pinned (a device prepared before server identities) the
    check is exactly what it always was.
    """
    import json
    import urllib.parse
    import urllib.request

    from satellite import server_identity

    opener = opener or urllib.request.urlopen
    challenge = server_identity.new_challenge() if expected_fingerprint else None
    addr = f"{host}:{port}"
    url = f"http://{host}:{port}{HEALTH_PATH}"
    if challenge:
        url = f"{url}?challenge={challenge}&addr={urllib.parse.quote(addr, safe='')}"
    try:
        with opener(url, timeout=timeout) as r:
            if getattr(r, "status", 200) != 200:
                return False
            doc = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:      # noqa: BLE001 — every failure is just "not here"
        return False
    if not (isinstance(doc, dict) and doc.get("status") == "ok" and doc.get("bot_name")):
        return False
    if not challenge:
        return True
    try:
        server_identity.verify_health_document(
            doc, challenge=challenge, expected_fingerprint=expected_fingerprint,
            addr=addr,
        )
    except server_identity.IdentityError as e:
        # Loud, unlike the silent misses above: something on this network
        # is answering as Domovoi and is not the server this device was
        # prepared for. That is worth a line in the journal.
        log.warning("%s answers as Domovoi but %s", host, e)
        return False
    return True


def find_core(
    *,
    port: int = CORE_PORT,
    ip: str | None = None,
    hosts: list[str] | None = None,
    probe_fn=probe,
    workers: int = _WORKERS,
    expected_fingerprint: str | None = None,
) -> str | None:
    """The first host on this subnet that answers as a Domovoi core, or None.

    Concurrent because a sequential sweep at 300 ms a host would take a
    minute; 32 workers finishes a /24 in a few seconds.

    ``expected_fingerprint`` is bound onto ``probe_fn`` rather than passed
    on every call, so a substituted probe that predates identities keeps
    the signature it always had.
    """
    if expected_fingerprint:
        probe_fn = functools.partial(
            probe_fn, expected_fingerprint=expected_fingerprint
        )
    if hosts is None:
        ip = ip or local_ipv4()
        if not ip:
            log.warning("no local IPv4 address — cannot discover the server")
            return None
        hosts = candidate_hosts(ip)
    if not hosts:
        return None

    log.info("discovering the Domovoi server across %d addresses", len(hosts))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(probe_fn, h, port=port): h for h in hosts}
        try:
            for fut in as_completed(futures):
                if fut.result():
                    found = futures[fut]
                    log.info("found the Domovoi server at %s", found)
                    return found
        finally:
            # Don't keep sweeping once we have an answer.
            for fut in futures:
                fut.cancel()
    log.warning("no Domovoi server answered on this network")
    return None


def resolve_url(configured: str, *, port: int = CORE_PORT, finder=None,
                expected_fingerprint: str | None = None) -> str | None:
    """Turn a configured ``domovoi_url`` into a usable one.

    Anything but the ``auto`` sentinel is returned untouched — an address
    typed into the setup portal always wins over discovery.

    A returned address is a candidate, not a decision: the caller still has
    to keep it out of config.toml until the server says this device is
    paired. ``expected_fingerprint`` makes the sweep itself selective.
    """
    value = (configured or "").strip()
    if value and value.lower() != AUTO:
        return value
    # Resolved at CALL time. As a default argument this binds once at import,
    # which silently ignores any later substitution — and a test that thinks
    # it injected a fake then sweeps the real network instead, passing or
    # failing on whatever happens to be plugged in.
    finder = find_core if finder is None else finder
    kwargs: dict[str, object] = {"port": port}
    if expected_fingerprint:
        kwargs["expected_fingerprint"] = expected_fingerprint
    host = finder(**kwargs)
    if host is None:
        return None
    return f"ws://{host}:{port}"


# ─── the order: name, last-known address, sweep ───────────────────────────


def valid_name(name: object) -> bool:
    """A host name (not an address) worth resolving."""
    if not isinstance(name, str):
        return False
    text = name.strip().rstrip(".").lower()
    if not text or text == "localhost":
        return False
    try:
        ipaddress.ip_address(text)
        return False
    except ValueError:
        pass
    return bool(_NAME_RE.match(text))


def _read_names(path: Path) -> list[str]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    names = doc.get("names") if isinstance(doc, dict) else None
    if not isinstance(names, list):
        return []
    return [n.strip().rstrip(".").lower() for n in names if valid_name(n)][:MAX_NAMES]


def server_names(path: Path | None = None) -> list[str]:
    """The names to try, in order: :data:`DEFAULT_NAMES`, then the ones the
    core advertised (deduplicated)."""
    out: list[str] = []
    for name in (*DEFAULT_NAMES, *_read_names(path or SERVER_NAMES_PATH)):
        if name not in out:
            out.append(name)
    return out


def remember_names(names: Any, path: Path | None = None) -> bool:
    """Keep the names the core advertised (its ``ready`` frame). Only the
    syntax is checked: a name is a hint the identity check settles, so a
    wrong one costs one failed lookup. True when the file changed."""
    if not isinstance(names, list):
        return False
    clean: list[str] = []
    for n in names:
        if valid_name(n):
            text = n.strip().rstrip(".").lower()
            if text not in clean:
                clean.append(text)
    clean = clean[:MAX_NAMES]
    path = path or SERVER_NAMES_PATH
    if not clean or _read_names(path) == clean:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"names": clean}), encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        log.debug("could not save the server's names: %s", e)
        return False
    return True


def resolve_name(name: str, *, timeout: float = _NAME_TIMEOUT_SEC,
                 resolver: Callable[..., Any] | None = None) -> list[str]:
    """The IPv4 addresses ``name`` resolves to, in the resolver's order, or
    ``[]`` when it does not resolve within ``timeout``. Never raises."""
    resolver = _DEFAULT_RESOLVER if resolver is None else resolver
    result: list[Any] = []

    def ask() -> None:
        try:
            result.extend(resolver(name, CORE_PORT, socket.AF_INET, socket.SOCK_STREAM))
        except (OSError, UnicodeError, ValueError):
            pass

    worker = threading.Thread(target=ask, name="resolve-server-name", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        log.info("%s did not resolve within %.0fs", name, timeout)
        return []
    out: list[str] = []
    for info in list(result):
        try:
            addr = info[4][0]
            ip = ipaddress.ip_address(addr)
        except (IndexError, TypeError, ValueError):
            continue
        if ip.version != 4 or ip.is_loopback or ip.is_unspecified or ip.is_multicast:
            continue
        if addr not in out:
            out.append(addr)
    return out


@dataclass(frozen=True)
class Located:
    url: str
    # "name", "last-known" or "scan".
    via: str
    detail: str = ""


def _host_port(url: str, default_port: int) -> tuple[str | None, int]:
    text = (url or "").strip()
    if "://" not in text:
        text = "ws://" + text
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port or default_port
    except ValueError:
        return None, default_port
    return parts.hostname, port


def locate(
    *,
    names: list[str] | tuple[str, ...] = DEFAULT_NAMES,
    last_known: str | None = None,
    port: int = CORE_PORT,
    expected_fingerprint: str | None = None,
    probe_fn: Callable[..., bool] | None = None,
    finder: Callable[..., str | None] | None = None,
    resolver: Callable[..., Any] | None = None,
    scan: bool = True,
) -> Located | None:
    """Find this device's core: by name, then at the last-known address,
    then by sweeping the subnet. Every candidate must pass ``probe_fn``
    (:func:`probe`: answers as Domovoi and, when a fingerprint is pinned,
    signs a fresh nonce bound to the address dialled with the pinned key)
    before it is returned. None when nothing did."""
    probe_fn = probe if probe_fn is None else probe_fn
    kwargs: dict[str, Any] = {"port": port, "timeout": _SINGLE_PROBE_TIMEOUT_SEC}
    if expected_fingerprint:
        kwargs["expected_fingerprint"] = expected_fingerprint

    def verified(host: str) -> bool:
        try:
            return bool(probe_fn(host, **kwargs))
        except Exception as e:  # noqa: BLE001 — a probe that breaks is a miss
            log.debug("probing %s failed: %s", host, e)
            return False

    tried: set[str] = set()
    for name in names:
        addrs = resolve_name(name, resolver=resolver)
        if not addrs:
            log.info("server name %s did not resolve", name)
            continue
        for addr in addrs:
            if addr in tried:
                continue
            tried.add(addr)
            if verified(addr):
                log.info("found the Domovoi server by name: %s -> %s", name, addr)
                return Located(f"ws://{addr}:{port}", "name", f"{name} -> {addr}")
        log.info("server name %s resolved to %s, none of which proved to be "
                 "this device's server", name, ", ".join(addrs))

    if last_known and last_known.strip().lower() != AUTO:
        host, last_port = _host_port(last_known, port)
        if host and host not in tried:
            tried.add(host)
            k = dict(kwargs, port=last_port)
            try:
                ok = bool(probe_fn(host, **k))
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                return Located(last_known.strip(), "last-known", host)
            log.info("the last-known server address %s did not answer as this "
                     "device's server", last_known)

    if not scan:
        return None
    # Resolved at CALL time (see resolve_url).
    finder = find_core if finder is None else finder
    fkw: dict[str, Any] = {"port": port}
    if expected_fingerprint:
        fkw["expected_fingerprint"] = expected_fingerprint
    host = finder(**fkw)
    if host is None:
        return None
    return Located(f"ws://{host}:{port}", "scan", host)
