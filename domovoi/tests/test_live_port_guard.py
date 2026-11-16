"""The repo-wide guard (domovoi/tests/live_port_guard.py, installed by the
repo-root conftest): no test reaches the Domovoi installed on this machine.

Each case dials a live port and then takes the recorded violation off the
guard itself, so the guard's own teardown does not fail the case. Nothing
here sends a packet to 6369/6370: the guard refuses before the socket call.
"""

from __future__ import annotations

import asyncio
import socket
import urllib.request

import pytest

from domovoi.tests import live_port_guard


@pytest.mark.parametrize("host, port", [
    ("127.0.0.1", 6370), ("localhost", 6370), ("::1", 6369), ("127.0.1.1", 6369),
    ("0.0.0.0", 6370), ("LOCALHOST", "6370"),
])
def test_every_way_to_name_this_machine_is_refused(no_live_domovoi_ports, host, port):
    with pytest.raises(ConnectionRefusedError):
        socket.getaddrinfo(host, port)
    assert no_live_domovoi_ports.clear() == [f"{host}:{int(port)}"]


def test_urllib_is_refused_and_recorded(no_live_domovoi_ports):
    with pytest.raises(OSError):
        urllib.request.urlopen("http://127.0.0.1:6370/v1/health", timeout=1)
    assert no_live_domovoi_ports.clear() == ["127.0.0.1:6370"]


def test_a_raw_socket_is_refused(no_live_domovoi_ports):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(ConnectionRefusedError):
            s.connect(("127.0.0.1", 6369))
        with pytest.raises(ConnectionRefusedError):
            s.connect_ex(("127.0.0.1", 6370))
    finally:
        s.close()
    assert no_live_domovoi_ports.clear() == ["127.0.0.1:6369", "127.0.0.1:6370"]


def test_asyncio_dialling_an_ip_literal_is_refused(no_live_domovoi_ports):
    """asyncio skips the resolver for an IP literal, and on Windows its
    proactor connects without ``socket.connect`` — the event loop's own
    ``sock_connect`` is watched too."""
    async def dial():
        await asyncio.open_connection("127.0.0.1", 6370)

    with pytest.raises(OSError):
        asyncio.run(dial())
    assert "127.0.0.1:6370" in no_live_domovoi_ports.clear()


def test_a_server_of_the_tests_own_is_reachable(no_live_domovoi_ports):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
    finally:
        server.close()
    assert no_live_domovoi_ports.clear() == []


def test_other_hosts_and_ports_are_not_its_business():
    guard = live_port_guard.LivePortGuard()
    for host, port in (("192.168.1.20", 6370), ("127.0.0.1", 6371), ("127.0.0.1", 9)):
        guard.check(host, port)
    assert guard.clear() == []
