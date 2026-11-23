"""How a room's music player reaches the core, per host (domovoi/player_route.py).

The radio relay hands a room's player a URL on the core and serves it
only to that player's address, so both halves have to be right on each
host: the Linux server, the Windows installer's WSL distro and the Mac
installer's Lima VM (Docker on the same Linux system as the core), and a
development box on Docker Desktop. DB-free, Docker-free.
"""

from __future__ import annotations

import json

import pytest

from domovoi import player_route as pr
from domovoi.player_route import PlayerRoute

# `docker inspect --format '{{json .NetworkSettings.Networks}}'` for a
# room's player on Docker's default bridge.
BRIDGE = json.loads(
    '{"bridge":{"IPAMConfig":null,"Links":null,"Aliases":null,'
    '"Gateway":"172.17.0.1","IPAddress":"172.17.0.3","IPPrefixLen":16,'
    '"IPv6Gateway":"","GlobalIPv6Address":""}}'
)


@pytest.mark.parametrize("kind", ["linux", "wsl", "mac"])
def test_same_system_hosts_dial_the_bridge_gateway(kind) -> None:
    route = pr.route_for_kind(kind, BRIDGE)
    assert route == PlayerRoute(host="172.17.0.1", peer="172.17.0.3")
    assert route.url("/v1/plugins/radio/player-relay/t") == (
        "http://172.17.0.1:6370/v1/plugins/radio/player-relay/t"
    )
    assert route.admits("172.17.0.3")
    assert route.admits("::ffff:172.17.0.3")
    assert not route.admits("172.17.0.4")
    assert not route.admits("127.0.0.1")
    assert not route.admits(None)
    assert not route.admits("testclient")


@pytest.mark.parametrize("kind", ["windows", "darwin"])
def test_docker_desktop_dials_its_host_name_and_arrives_from_loopback(kind) -> None:
    route = pr.route_for_kind(kind, BRIDGE)
    assert route == PlayerRoute(host="host.docker.internal", peer="127.0.0.1")
    assert route.url("x") == "http://host.docker.internal:6370/x"


@pytest.mark.parametrize(
    "networks",
    [None, {}, "junk", {"bridge": {"IPAddress": "", "Gateway": ""}},
     {"bridge": {"IPAddress": "172.17.0.3"}}, {"bridge": {"IPAddress": "x", "Gateway": "y"}}],
)
def test_no_usable_network_is_no_route(networks) -> None:
    assert pr.route_for_kind("linux", networks) is None


def test_an_unknown_host_kind_is_no_route() -> None:
    assert pr.route_for_kind("freebsd", BRIDGE) is None


def test_an_ipv6_gateway_is_bracketed() -> None:
    route = pr.route_from_inspect({"n": {"IPAddress": "fd00::3", "Gateway": "fd00::1"}})
    assert route.url("/p") == "http://[fd00::1]:6370/p"


async def test_under_stubs_the_route_is_loopback_both_ways() -> None:
    route = await pr.player_route("kitchen")
    assert route == PlayerRoute(host="127.0.0.1", peer="127.0.0.1")


async def test_the_route_is_the_players_that_plays_the_room(monkeypatch) -> None:
    """A room in a shared queue is played by its host's player: the route
    is that container's."""
    from domovoi import host_kind, mpd_provisioner
    from domovoi.config import settings

    monkeypatch.setattr(settings, "use_stubs", False)
    monkeypatch.setattr(host_kind, "host_kind", lambda: "wsl")
    monkeypatch.setattr(pr, "player_room", lambda room: "living room")
    asked: list[tuple] = []

    async def fake_docker(*args, timeout=60.0):
        asked.append(args)
        return 0, json.dumps(BRIDGE), ""

    monkeypatch.setattr(mpd_provisioner, "_run_docker", fake_docker)
    route = await pr.player_route("kitchen")
    assert route == PlayerRoute(host="172.17.0.1", peer="172.17.0.3")
    assert asked[0][-1] == mpd_provisioner._container_name("living room")


async def test_docker_not_answering_is_no_route(monkeypatch) -> None:
    from domovoi import host_kind, mpd_provisioner
    from domovoi.config import settings

    monkeypatch.setattr(settings, "use_stubs", False)
    monkeypatch.setattr(host_kind, "host_kind", lambda: "linux")
    monkeypatch.setattr(pr, "player_room", lambda room: room)

    async def failing(*args, timeout=60.0):
        return 1, "", "no such container"

    monkeypatch.setattr(mpd_provisioner, "_run_docker", failing)
    assert await pr.player_route("kitchen") is None
