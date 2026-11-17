"""One connection between two friend houses: framing, handshake, frames.

Friend Houses ("serverless messaging and friend system between households")
carries everything between two houses over a single Tor stream per
connection. This module turns that byte stream into an authenticated,
encrypted channel and refuses everything else as early and as cheaply as it
can (threat-model finding 2: size, then authentication, then a fixed-schema
parse).

On the wire
-----------
::

    caller  ->  "DFH1"                            4-byte preface (version)
    caller  ->  frame(Noise IK message 1)         payload: HELLO
    answerer->  frame(Noise IK message 2)         payload: HELLO
    either  ->  frame(Noise transport message)    payload: kind byte + body
    ...

A frame is a 2-byte big-endian length followed by that many bytes; the
length can never exceed a Noise message (65,535 bytes).

The HELLO in each handshake message is the sender's signed address
statement plus its current sending epoch. The answering house checks the
caller's Noise static key against its **current** friend list before it
looks at anything else, and drops the connection without a word if the key
isn't a friend's. Tor's client authorization alone doesn't do this: a
removed friend can keep reaching the onion for minutes (spike S1).

Frame kinds
-----------
Only the kinds below exist; anything else closes the connection (finding
20). Later phases add kinds here, each with a fixed body size or a capped
one.
"""

from __future__ import annotations

import asyncio
import enum
import secrets
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from domovoi.friend_houses import identity as fid
from domovoi.friend_houses.noise import MAX_MESSAGE, TAGLEN, Handshake, NoiseError, Transport

PREFACE = b"DFH1"
PROLOGUE = b"domovoi-fh-session-v1"
HANDSHAKE_TIMEOUT_SEC = 30.0
IDLE_TIMEOUT_SEC = 120.0

_HELLO = struct.Struct(f">{fid.STATEMENT_SIZE}s{fid.EPOCH_BYTES}s")
# Noise IK message 1 = e (32) + encrypted s (32 + 16) + encrypted payload.
_MSG1_SIZE = 32 + 32 + TAGLEN + _HELLO.size + TAGLEN
# Message 2 = e (32) + encrypted payload.
_MSG2_SIZE = 32 + _HELLO.size + TAGLEN


class Kind(enum.IntEnum):
    PING = 0x10
    PONG = 0x11
    IM_BACK = 0x30  # "I'm online again": flush your outbox to me now
    BYE = 0x7F


# The exact body size each kind must have, or (min, max) for a capped one.
_BODY_SIZES: dict[Kind, int | tuple[int, int]] = {
    Kind.PING: 8,
    Kind.PONG: 8,
    Kind.IM_BACK: 0,
    Kind.BYE: 0,
}


class SessionRefused(Exception):
    """The other end isn't a friend, broke the protocol, or went quiet.

    Callers close the connection and log; they never tell the other end
    why (no roster or presence leak, finding 29)."""


@dataclass(frozen=True)
class Hello:
    statement: fid.AddressStatement
    epoch: bytes

    def to_bytes(self) -> bytes:
        return _HELLO.pack(self.statement.to_bytes(), self.epoch)

    @classmethod
    def parse(cls, data: bytes) -> Hello:
        if len(data) != _HELLO.size:
            raise SessionRefused("hello has the wrong size")
        raw_statement, epoch = _HELLO.unpack(data)
        statement = fid.AddressStatement.parse(raw_statement)
        if statement is None:
            raise SessionRefused("hello carries a bad statement")
        return cls(statement, epoch)


@dataclass(frozen=True)
class Friend:
    """What the answering house knows about a current friend."""

    house_key: bytes
    session_key: bytes


# ─── framing ──────────────────────────────────────────────────────────────


async def read_frame(reader: asyncio.StreamReader, *, max_size: int = MAX_MESSAGE,
                     timeout: float = IDLE_TIMEOUT_SEC) -> bytes:
    try:
        header = await asyncio.wait_for(reader.readexactly(2), timeout)
        (size,) = struct.unpack(">H", header)
        if size > max_size:
            raise SessionRefused("frame too large")
        return await asyncio.wait_for(reader.readexactly(size), timeout)
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError) as exc:
        raise SessionRefused("stream ended") from exc


def frame(data: bytes) -> bytes:
    if len(data) > MAX_MESSAGE:
        raise ValueError("frame too large")
    return struct.pack(">H", len(data)) + data


# ─── the session ──────────────────────────────────────────────────────────


class Session:
    """An authenticated connection to one friend house."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 transport: Transport, peer: Hello) -> None:
        self._reader = reader
        self._writer = writer
        self._transport = transport
        self.peer = peer
        self._send_lock = asyncio.Lock()

    @property
    def peer_house_key(self) -> bytes:
        return self.peer.statement.house_key

    async def send(self, kind: Kind, body: bytes = b"") -> None:
        _check_body(kind, body)
        async with self._send_lock:
            self._writer.write(frame(self._transport.encrypt(bytes([kind]) + body)))
            await self._writer.drain()

    async def recv(self, *, timeout: float = IDLE_TIMEOUT_SEC) -> tuple[Kind, bytes]:
        data = await read_frame(self._reader, timeout=timeout)
        try:
            plain = self._transport.decrypt(data)
        except NoiseError as exc:
            raise SessionRefused("frame refused") from exc
        if not plain:
            raise SessionRefused("empty frame")
        try:
            kind = Kind(plain[0])
        except ValueError as exc:
            raise SessionRefused("unknown frame kind") from exc
        body = plain[1:]
        _check_body(kind, body)
        return kind, body

    async def ping(self, *, timeout: float = 60.0) -> float:
        """Round-trip a PING; returns seconds. For checks and the spike rig."""
        loop = asyncio.get_running_loop()
        token = secrets.token_bytes(8)
        start = loop.time()
        await self.send(Kind.PING, token)
        while True:
            kind, body = await self.recv(timeout=timeout)
            if kind is Kind.PONG and body == token:
                return loop.time() - start
            if kind is Kind.PING:
                await self.send(Kind.PONG, body)

    async def close(self) -> None:
        try:
            await self.send(Kind.BYE)
        except Exception:  # noqa: BLE001 — closing a broken stream is fine
            pass
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except Exception:  # noqa: BLE001 — already gone
            pass


def _check_body(kind: Kind, body: bytes) -> None:
    rule = _BODY_SIZES[kind]
    if isinstance(rule, int):
        ok = len(body) == rule
    else:
        ok = rule[0] <= len(body) <= rule[1]
    if not ok:
        raise SessionRefused(f"{kind.name} body has the wrong size")


# ─── the handshake ────────────────────────────────────────────────────────


async def initiate(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    keys: fid.HouseKeys,
    me: Hello,
    friend: Friend,
) -> Session:
    """Open a session to ``friend`` over an already-connected stream."""
    hs = Handshake("IK", initiator=True, prologue=PROLOGUE, static=keys.session,
                   remote_static=friend.session_key)
    try:
        writer.write(PREFACE + frame(hs.write_message(me.to_bytes())))
        await writer.drain()
        msg2 = await read_frame(reader, max_size=_MSG2_SIZE, timeout=HANDSHAKE_TIMEOUT_SEC)
        peer = Hello.parse(hs.read_message(msg2))
    except (NoiseError, ConnectionError) as exc:
        raise SessionRefused("handshake failed") from exc
    _check_peer(peer, friend)
    return Session(reader, writer, hs.transport(), peer)


async def respond(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    keys: fid.HouseKeys,
    me: Hello,
    friend_for: Callable[[bytes], Awaitable[Friend | None]],
) -> Session:
    """Answer a caller. ``friend_for(session_key)`` looks the caller up in
    the CURRENT friend list; a caller who isn't there is refused before its
    hello is even parsed."""
    try:
        preface = await asyncio.wait_for(reader.readexactly(len(PREFACE)), HANDSHAKE_TIMEOUT_SEC)
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError) as exc:
        raise SessionRefused("no preface") from exc
    if preface != PREFACE:
        raise SessionRefused("unknown protocol version")
    msg1 = await read_frame(reader, max_size=_MSG1_SIZE, timeout=HANDSHAKE_TIMEOUT_SEC)
    hs = Handshake("IK", initiator=False, prologue=PROLOGUE, static=keys.session)
    try:
        payload = hs.read_message(msg1)
    except NoiseError as exc:
        raise SessionRefused("handshake failed") from exc
    caller_session_key = hs.remote_static
    assert caller_session_key is not None
    friend = await friend_for(caller_session_key)
    if friend is None:
        raise SessionRefused("not a friend")
    peer = Hello.parse(payload)
    _check_peer(peer, friend)
    try:
        writer.write(frame(hs.write_message(me.to_bytes())))
        await writer.drain()
    except ConnectionError as exc:
        raise SessionRefused("stream ended") from exc
    return Session(reader, writer, hs.transport(), peer)


def _check_peer(peer: Hello, friend: Friend) -> None:
    """The hello must be signed by the friend's house key and name the very
    session key the Noise handshake authenticated."""
    if not secrets.compare_digest(peer.statement.house_key, friend.house_key):
        raise SessionRefused("hello from a different house")
    if not secrets.compare_digest(peer.statement.session_key, friend.session_key):
        raise SessionRefused("hello names a different session key")
