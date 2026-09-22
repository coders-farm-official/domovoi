"""Find the Domovoi server on the local network.

A satellite onboarded through the Wi-Fi portal has no way to learn the
server's address: the core is not a participant in that adoption, and the
customer shouldn't have to know their server's IP. So the config carries the
sentinel ``auto`` and this module resolves it once, at first start.

**Deliberately not mDNS.** Satellites are configured with the core's IP
rather than ``domovoi.local`` precisely because multicast over Wi-Fi is the
thing that fails at 3 a.m. — multicast-to-unicast conversion, mesh
backhauls and guest-VLAN isolation all break it in ways that are invisible
until they aren't. Making that load-bearing for a customer's first five
minutes would be the worst possible place to inherit it.

Instead: sweep the interface's own /24 for ``GET /v1/health`` and keep the
host that answers with the Domovoi signature. Unicast HTTP is boring and
works. The result is persisted, so this runs once rather than on every
connect, and needs no new dependency and no core-side change at all.
"""

from __future__ import annotations

import functools
import ipaddress
import logging
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger(__name__)

AUTO = "auto"
CORE_PORT = 6370
HEALTH_PATH = "/v1/health"

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
    way is no longer enough.

    With nothing pinned (a device prepared before server identities) the
    check is exactly what it always was.
    """
    import json
    import urllib.request

    from satellite import server_identity

    opener = opener or urllib.request.urlopen
    challenge = server_identity.new_challenge() if expected_fingerprint else None
    url = f"http://{host}:{port}{HEALTH_PATH}"
    if challenge:
        url = f"{url}?challenge={challenge}"
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
            doc, challenge=challenge, expected_fingerprint=expected_fingerprint
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
