"""Dial a friend house: SOCKS5 to the local Tor daemon, then the handshake.

Friend Houses ("serverless messaging and friend system between households")
never opens a network port of its own. Outbound, core speaks SOCKS5 to the
Tor daemon over a unix socket in the folder they share
(:mod:`domovoi.friend_houses.tor_config`). The SOCKS port is not a TCP port,
so no other program on the box or the network can use the daemon as a
proxy around the internet answer (threat-model finding 26).

Only the CONNECT command to a ``.onion`` name on port 80 is ever sent: this
is not a general-purpose proxy client.
"""

from __future__ import annotations

import asyncio
import struct
import sys
from pathlib import Path

from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import session as fs

CONNECT_TIMEOUT_SEC = 180.0  # spike S1: a cold onion connect can take minutes

# RFC 1928 reply codes, plus Tor's onion-service extensions (prop 304).
_SOCKS_REPLIES = {
    1: "general failure", 2: "not allowed", 3: "network unreachable",
    4: "host unreachable", 5: "connection refused", 6: "timed out",
    7: "command not supported", 8: "address type not supported",
    0xF0: "onion descriptor not found", 0xF1: "onion descriptor invalid",
    0xF2: "onion introduction failed", 0xF3: "onion rendezvous failed",
    0xF4: "onion client authorization missing",
    0xF5: "onion client authorization wrong", 0xF6: "onion address invalid",
    0xF7: "onion introduction timed out",
}


class Unreachable(Exception):
    """The friend house couldn't be reached right now. Plain words for the
    log; the outbox simply tries again later."""


async def _readexactly(reader: asyncio.StreamReader, n: int) -> bytes:
    try:
        return await reader.readexactly(n)
    except asyncio.IncompleteReadError as exc:
        raise Unreachable("the Tor daemon closed the connection") from exc


async def socks_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                        onion: str, port: int = 80) -> None:
    """Ask the daemon, over an open stream, for a stream to ``onion:port``."""
    if fid.parse_onion(onion) is None:
        raise ValueError("not a v3 onion address")
    host = onion.lower().encode("ascii")
    writer.write(b"\x05\x01\x00")  # version 5, one method: no authentication
    await writer.drain()
    if await _readexactly(reader, 2) != b"\x05\x00":
        raise Unreachable("the Tor daemon refused the SOCKS greeting")
    writer.write(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + struct.pack(">H", port))
    await writer.drain()
    head = await _readexactly(reader, 4)
    if head[0] != 5:
        raise Unreachable("the Tor daemon answered something other than SOCKS5")
    if head[1] != 0:
        raise Unreachable(_SOCKS_REPLIES.get(head[1], f"SOCKS error {head[1]}"))
    atyp = head[3]
    if atyp == 1:
        await _readexactly(reader, 4 + 2)
    elif atyp == 4:
        await _readexactly(reader, 16 + 2)
    elif atyp == 3:
        size = (await _readexactly(reader, 1))[0]
        await _readexactly(reader, size + 2)
    else:
        raise Unreachable("the Tor daemon answered an unknown address type")


async def open_stream(socks_path: Path, *, onion: str,
                      timeout: float = CONNECT_TIMEOUT_SEC) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """A raw stream to ``onion:80`` through the local Tor daemon (the
    pairing exchange runs its own handshake on it)."""
    if sys.platform == "win32":  # pragma: no cover — the daemon runs on Linux
        raise Unreachable("Friend Houses runs on the Linux side of the server")

    async def _dial() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            reader, writer = await asyncio.open_unix_connection(str(socks_path))
        except OSError as exc:
            raise Unreachable("the Tor daemon isn't running") from exc
        try:
            await socks_connect(reader, writer, onion)
        except BaseException:
            writer.close()
            raise
        return reader, writer

    try:
        return await asyncio.wait_for(_dial(), timeout)
    except asyncio.TimeoutError as exc:
        raise Unreachable("timed out reaching the other house") from exc


async def open_session(
    socks_path: Path,
    *,
    onion: str,
    keys: fid.HouseKeys,
    me: fs.Hello,
    friend: fs.Friend,
    timeout: float = CONNECT_TIMEOUT_SEC,
) -> fs.Session:
    """A session with a friend house, through the local Tor daemon."""
    if sys.platform == "win32":  # pragma: no cover — the daemon runs on Linux
        raise Unreachable("Friend Houses runs on the Linux side of the server")

    async def _dial() -> fs.Session:
        try:
            reader, writer = await asyncio.open_unix_connection(str(socks_path))
        except OSError as exc:
            raise Unreachable("the Tor daemon isn't running") from exc
        try:
            await socks_connect(reader, writer, onion)
            return await fs.initiate(reader, writer, keys=keys, me=me, friend=friend)
        except BaseException:
            writer.close()
            raise

    try:
        return await asyncio.wait_for(_dial(), timeout)
    except asyncio.TimeoutError as exc:
        raise Unreachable("timed out reaching the friend house") from exc
