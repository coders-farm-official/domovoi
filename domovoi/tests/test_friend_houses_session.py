"""A Friend Houses connection between two houses
(domovoi/friend_houses/session.py), over a loopback socket."""

from __future__ import annotations

import asyncio
import struct

import pytest

from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import session as fs


def _house(tmp_path, name: str, issued_at: int = 1_800_000_000):
    keys = fid.create(tmp_path / f"{name}.json")
    hello = fs.Hello(keys.address_statement(bytes(32), issued_at), b"\x01" * fid.EPOCH_BYTES)
    friend_view = fs.Friend(keys.house_key, keys.session_key)
    return keys, hello, friend_view


async def _serve(handler):
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


def _answerer(keys, hello, friends: dict[bytes, fs.Friend], results: list):
    async def friend_for(session_key: bytes):
        return friends.get(session_key)

    async def handler(reader, writer):
        try:
            sess = await fs.respond(reader, writer, keys=keys, me=hello, friend_for=friend_for)
            results.append(sess.peer_house_key)
            while True:
                kind, body = await sess.recv(timeout=5)
                if kind is fs.Kind.PING:
                    await sess.send(fs.Kind.PONG, body)
                elif kind is fs.Kind.BYE:
                    break
        except fs.SessionRefused as exc:
            results.append(exc)
        finally:
            writer.close()

    return handler


async def test_two_friends_connect_and_ping(tmp_path) -> None:
    a_keys, a_hello, a_view = _house(tmp_path, "a")
    b_keys, b_hello, b_view = _house(tmp_path, "b")
    results: list = []
    server, port = await _serve(_answerer(b_keys, b_hello, {a_keys.session_key: a_view}, results))
    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        sess = await fs.initiate(reader, writer, keys=a_keys, me=a_hello, friend=b_view)
        assert sess.peer_house_key == b_keys.house_key
        assert await sess.ping() >= 0
        await sess.close()
        await asyncio.sleep(0.05)
    assert results == [a_keys.house_key]


async def test_a_house_that_is_not_a_friend_is_dropped(tmp_path) -> None:
    a_keys, a_hello, _ = _house(tmp_path, "a")
    b_keys, b_hello, b_view = _house(tmp_path, "b")
    results: list = []
    server, port = await _serve(_answerer(b_keys, b_hello, {}, results))  # B knows nobody
    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        with pytest.raises(fs.SessionRefused):
            await fs.initiate(reader, writer, keys=a_keys, me=a_hello, friend=b_view)
        await asyncio.sleep(0.05)
    assert isinstance(results[0], fs.SessionRefused)


async def test_a_hello_from_a_different_house_is_refused(tmp_path) -> None:
    a_keys, _, a_view = _house(tmp_path, "a")
    b_keys, b_hello, b_view = _house(tmp_path, "b")
    m_keys, _, _ = _house(tmp_path, "m")
    # A's session key, but a hello signed by some other house that claims it.
    body = fid.AddressStatement(m_keys.house_key, a_keys.session_key, bytes(32), 1, b"").body()
    forged = fs.Hello(
        fid.AddressStatement(m_keys.house_key, a_keys.session_key, bytes(32), 1,
                             m_keys.sign(fid.CTX_ADDRESS, body)),
        b"\x01" * 8,
    )
    results: list = []
    server, port = await _serve(_answerer(b_keys, b_hello, {a_keys.session_key: a_view}, results))
    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        with pytest.raises(fs.SessionRefused):
            await fs.initiate(reader, writer, keys=a_keys, me=forged, friend=b_view)
        await asyncio.sleep(0.05)
    assert isinstance(results[0], fs.SessionRefused)


async def test_calling_the_wrong_house_fails_the_handshake(tmp_path) -> None:
    a_keys, a_hello, a_view = _house(tmp_path, "a")
    b_keys, b_hello, _ = _house(tmp_path, "b")
    _, _, c_view = _house(tmp_path, "c")  # A thinks it is calling C
    results: list = []
    server, port = await _serve(_answerer(b_keys, b_hello, {a_keys.session_key: a_view}, results))
    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        with pytest.raises(fs.SessionRefused):
            await fs.initiate(reader, writer, keys=a_keys, me=a_hello, friend=c_view)
        await asyncio.sleep(0.05)


@pytest.mark.parametrize("opening", [
    b"XXXX",                                   # wrong preface
    fs.PREFACE + struct.pack(">H", 60000),     # first frame far too large
    fs.PREFACE + struct.pack(">H", 10) + b"\x00" * 10,  # garbage handshake
])
async def test_hostile_openings_are_refused_cheaply(tmp_path, opening: bytes) -> None:
    b_keys, b_hello, _ = _house(tmp_path, "b")
    results: list = []
    server, port = await _serve(_answerer(b_keys, b_hello, {}, results))
    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(opening)
        await writer.drain()
        writer.write_eof()
        assert await reader.read() == b""
        writer.close()
        await asyncio.sleep(0.05)
    assert isinstance(results[0], fs.SessionRefused)


async def test_unknown_or_misshapen_frames_end_the_session(tmp_path) -> None:
    a_keys, a_hello, a_view = _house(tmp_path, "a")
    b_keys, b_hello, b_view = _house(tmp_path, "b")
    results: list = []
    server, port = await _serve(_answerer(b_keys, b_hello, {a_keys.session_key: a_view}, results))
    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        sess = await fs.initiate(reader, writer, keys=a_keys, me=a_hello, friend=b_view)
        # Bypass the kind check: an unknown kind straight onto the wire.
        writer.write(fs.frame(sess._transport.encrypt(b"\x55hello")))
        await writer.drain()
        assert await reader.read() == b""
        await asyncio.sleep(0.05)
    assert isinstance(results[-1], fs.SessionRefused)
    with pytest.raises(fs.SessionRefused):
        fs._check_body(fs.Kind.PING, b"short")


async def test_a_ping_has_one_deadline_however_many_frames_arrive(tmp_path) -> None:
    # A friend that keeps answering with the wrong pong must not hold a
    # ping (and the check that waits on it) open for ever.
    a_keys, a_hello, a_view = _house(tmp_path, "a")
    b_keys, b_hello, b_view = _house(tmp_path, "b")

    async def chatty(reader, writer):
        async def friend_for(session_key):
            return {a_keys.session_key: a_view}.get(session_key)
        try:
            sess = await fs.respond(reader, writer, keys=b_keys, me=b_hello, friend_for=friend_for)
            await sess.recv(timeout=5)
            while True:
                await sess.send(fs.Kind.PONG, b"\x00" * 8)
                await asyncio.sleep(0.05)
        except (fs.SessionRefused, ConnectionError):
            pass
        finally:
            writer.close()

    server, port = await _serve(chatty)
    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        sess = await fs.initiate(reader, writer, keys=a_keys, me=a_hello, friend=b_view)
        loop = asyncio.get_running_loop()
        start = loop.time()
        with pytest.raises(fs.SessionRefused):
            await sess.ping(timeout=0.5)
        assert loop.time() - start < 3
        writer.close()
