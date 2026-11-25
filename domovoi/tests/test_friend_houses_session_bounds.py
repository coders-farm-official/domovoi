"""Friend Houses sessions stay bounded when the friend misbehaves: closing
never waits on a peer that stopped reading, and a wait for an ack or a pong
does not last for as long as the friend keeps pinging
(domovoi/friend_houses/session.py, messages.py)."""

from __future__ import annotations

import asyncio
import time

from domovoi.friend_houses import messages as fm
from domovoi.friend_houses import session as fs


class _StuckWriter:
    """A stream whose other end stopped reading: drain never returns."""

    def __init__(self) -> None:
        self.closed = False

    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        await asyncio.sleep(3600)

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        await asyncio.sleep(3600)


class _Transport:
    def encrypt(self, data: bytes) -> bytes:
        return data


async def test_close_does_not_wait_on_a_peer_that_stopped_reading(monkeypatch) -> None:
    monkeypatch.setattr(fs, "CLOSE_DEADLINE_SEC", 0.05)
    writer = _StuckWriter()
    sess = fs.Session(None, writer, _Transport(), None)  # type: ignore[arg-type]
    started = time.monotonic()
    await asyncio.wait_for(sess.close(), 2)
    assert writer.closed and time.monotonic() - started < 1


class _Pinger:
    """A friend that answers every wait with another ping."""

    def __init__(self) -> None:
        self.pongs = 0

    async def recv(self, *, timeout: float = 0) -> tuple[fs.Kind, bytes]:
        return fs.Kind.PING, b"x" * 8

    async def send(self, kind: fs.Kind, body: bytes = b"") -> None:
        if kind is fs.Kind.PONG:
            self.pongs += 1


async def test_an_ack_wait_ends_when_the_friend_only_pings() -> None:
    friend = _Pinger()
    kind, _ = await asyncio.wait_for(fm._await_ack(friend, b"\x01" * 16), 2)  # type: ignore[arg-type]
    assert kind is None and friend.pongs == fs.PONGS_PER_WAIT
