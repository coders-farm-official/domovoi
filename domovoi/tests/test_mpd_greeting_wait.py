"""The provisioner waits for MPD itself, not for a TCP accept.

Each room's MPD control port is published through Docker's port proxy,
and from the core's host that proxy accepts a connection even while
nothing in the container is listening yet — then closes it with no bytes
(measured on Docker Desktop: "connected" in 0 ms, 0 bytes, while the
daemon refused). A readiness wait that stopped at the connect therefore
said "up" at once, and `ensure_room` / the startup volume pin went on to a
daemon that was not there. `_wait_for_mpd` reads MPD's greeting
(``OK MPD <version>``) instead.

Pinned with local sockets standing in for the port (no Docker, no DB):
an accept-and-close is not ready; the greeting is; a daemon that comes up
after a few such closes is waited for; a silent one and a refusing one are
given up on within the bound.
"""

from __future__ import annotations

import asyncio
import socket
import time

from domovoi.mpd_provisioner import _wait_for_mpd

HOST = "127.0.0.1"


async def _serve(handler):
    server = await asyncio.start_server(handler, HOST, 0)
    return server, server.sockets[0].getsockname()[1]


async def _closed(server) -> None:
    server.close()
    await server.wait_closed()


async def test_an_accept_that_closes_with_nothing_is_not_mpd() -> None:
    """Docker's proxy in front of a daemon that is not listening yet."""

    async def proxy_with_nothing_behind(reader, writer):
        writer.close()

    server, port = await _serve(proxy_with_nothing_behind)
    try:
        began = time.monotonic()
        assert await _wait_for_mpd(HOST, port, timeout=0.5, interval=0.05) is False
        assert 0.4 < time.monotonic() - began < 1.5
    finally:
        await _closed(server)


async def test_the_greeting_is_ready() -> None:
    async def mpd(reader, writer):
        writer.write(b"OK MPD 0.23.5\n")
        await writer.drain()
        await reader.read()                 # until the waiter hangs up
        writer.close()

    server, port = await _serve(mpd)
    try:
        began = time.monotonic()
        assert await _wait_for_mpd(HOST, port, timeout=3.0) is True
        assert time.monotonic() - began < 0.5
    finally:
        await _closed(server)


async def test_a_daemon_that_comes_up_behind_the_proxy_is_waited_for() -> None:
    connections = 0

    async def starting(reader, writer):
        nonlocal connections
        connections += 1
        if connections >= 3:
            writer.write(b"OK MPD 0.23.12\n")
            await writer.drain()
        writer.close()

    server, port = await _serve(starting)
    try:
        assert await _wait_for_mpd(HOST, port, timeout=3.0, interval=0.05) is True
        assert connections == 3
    finally:
        await _closed(server)


async def test_something_else_on_the_port_is_not_mpd() -> None:
    async def http(reader, writer):
        writer.write(b"HTTP/1.1 200 OK\r\n\r\n")
        await writer.drain()
        writer.close()

    server, port = await _serve(http)
    try:
        assert await _wait_for_mpd(HOST, port, timeout=0.3, interval=0.05) is False
    finally:
        await _closed(server)


async def test_a_daemon_that_never_speaks_is_bounded() -> None:
    """A frozen container: accepted, and then nothing."""
    release = asyncio.Event()

    async def frozen(reader, writer):
        await release.wait()
        writer.close()

    server, port = await _serve(frozen)
    try:
        began = time.monotonic()
        assert await _wait_for_mpd(HOST, port, timeout=0.4, interval=0.05) is False
        assert time.monotonic() - began < 1.2
    finally:
        release.set()
        await _closed(server)


async def test_nothing_listening_is_bounded() -> None:
    with socket.socket() as s:
        s.bind((HOST, 0))
        port = s.getsockname()[1]
    began = time.monotonic()
    assert await _wait_for_mpd(HOST, port, timeout=0.4, interval=0.05) is False
    # Windows reports a refused loopback connect only after ~2 s of SYN
    # retries; the wait is bounded by what is left of its timeout anyway.
    assert time.monotonic() - began < 1.2
