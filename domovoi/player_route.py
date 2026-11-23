"""How a room's music player reaches this core, and how the core knows it.

A room's player is an MPD container (``domovoi/mpd_provisioner.py``). When
the core hands it a URL on the core itself (the player relay,
``domovoi/player_relay.py``), two things have to be right on every host:

* **the address the player dials.** The core listens on every interface
  (port 6370), and on a Linux server, inside the Windows installer's WSL
  distro and inside the Mac installer's Lima VM the Docker engine runs on
  the same Linux system as the core. So the player dials its own network's
  gateway: the bridge address of that same system, reachable from the
  container with no DNS and no LAN address involved.
* **the address the core sees.** That connection arrives from the
  container's own address on that network, so a route served only to the
  room's player can compare the caller with it.

A development box that runs the core natively on Windows or macOS (host
kind ``windows`` / ``darwin``) uses Docker Desktop instead, whose engine
sits in its own VM: the container reaches the host as
``host.docker.internal``, and Docker Desktop delivers that connection from
loopback. There, any program on the same PC could present the same
caller address; the relay's token is what holds.

Under ``USE_STUBS`` (the test suite, a stub dev box) nothing is asked of
Docker and the answer is loopback both ways.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: The core's HTTP port (``domovoi.main``: uvicorn on every interface).
CORE_PORT = 6370

#: Host kinds whose Docker engine shares the core's Linux system.
SAME_SYSTEM_KINDS = ("linux", "wsl", "mac")
#: What a Docker Desktop container dials to reach its host.
DOCKER_DESKTOP_HOST = "host.docker.internal"
_LOOPBACK = "127.0.0.1"


@dataclass(frozen=True)
class PlayerRoute:
    """``host``:``port`` is what the room's player dials to reach the
    core; ``peer`` is the address the core sees that connection come
    from."""

    host: str
    peer: str
    port: int = CORE_PORT

    def url(self, path: str) -> str:
        """``http://host:port/path`` (an IPv6 host bracketed)."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}/{path.lstrip('/')}"

    def admits(self, address: str | None) -> bool:
        """True when ``address`` (a request's client host) is this route's
        player. IPv4-mapped IPv6 spellings compare as the IPv4 they carry."""
        return _same_address(address, self.peer)


def _norm(address: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address((address or "").strip())
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _same_address(a: str | None, b: str | None) -> bool:
    na, nb = _norm(a), _norm(b)
    return na is not None and na == nb


def route_from_inspect(networks: Any) -> PlayerRoute | None:
    """The route for a container on the core's own Linux system, from
    ``docker inspect``'s ``.NetworkSettings.Networks`` map: the first
    network with both an address and a gateway (a room's player is on one,
    Docker's default bridge). None when there is none to use."""
    if not isinstance(networks, dict):
        return None
    for name in sorted(networks):
        net = networks[name]
        if not isinstance(net, dict):
            continue
        ip, gateway = _norm(net.get("IPAddress")), _norm(net.get("Gateway"))
        if ip is not None and gateway is not None:
            return PlayerRoute(host=str(gateway), peer=str(ip))
    return None


def route_for_kind(kind: str, networks: Any = None) -> PlayerRoute | None:
    """The route on a host of kind ``kind`` (``domovoi.host_kind``), given
    the player container's networks where the kind needs them."""
    if kind in SAME_SYSTEM_KINDS:
        return route_from_inspect(networks)
    if kind in ("windows", "darwin"):
        return PlayerRoute(host=DOCKER_DESKTOP_HOST, peer=_LOOPBACK)
    return None


async def _container_networks(container: str) -> Any:
    from domovoi.mpd_provisioner import _run_docker

    rc, out, err = await _run_docker(
        "inspect", "--format", "{{json .NetworkSettings.Networks}}", container,
        timeout=10.0,
    )
    if rc != 0:
        log.warning("player route: docker inspect %s failed: %s", container, err.strip()[:200])
        return None
    try:
        return json.loads(out.strip() or "null")
    except ValueError:
        return None


def player_room(room_id: str | None) -> str | None:
    """The room whose player plays ``room_id``'s music: the host of its
    shared queue, resolved the way the MPD client resolves it."""
    from domovoi.clients import mpd as mpd_module

    return mpd_module._resolve_room(mpd_module._host_of(room_id))


async def player_route(room_id: str | None) -> PlayerRoute | None:
    """How the player that plays ``room_id``'s music reaches this core,
    or None when it can't be told (no player, Docker didn't answer)."""
    from domovoi.config import settings

    if settings.use_stubs:
        return PlayerRoute(host=_LOOPBACK, peer=_LOOPBACK)
    from domovoi import host_kind

    kind = host_kind.host_kind()
    if kind not in SAME_SYSTEM_KINDS:
        return route_for_kind(kind)
    room = player_room(room_id)
    if room is None:
        return None
    from domovoi.mpd_provisioner import _container_name

    return route_for_kind(kind, await _container_networks(_container_name(room)))
