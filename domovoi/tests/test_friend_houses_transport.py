"""Friend Houses transport pieces: the Tor folder and its rules
(tor_config.py), the SOCKS dialer (connect.py) and the inbound listener
(listener.py). Everything runs over loopback; real Tor is the spike rig's
job, not the suite's."""

from __future__ import annotations

import asyncio
import json
import os
import struct

import pytest

from domovoi.friend_houses import connect as fc
from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import listener as fl
from domovoi.friend_houses import session as fs
from domovoi.friend_houses import tor_config as tc

# ─── what the daemon should be doing ──────────────────────────────────────


@pytest.mark.parametrize("enabled,policy,friends,invites,run", [
    (False, "always", 3, 1, False),   # off by default: nothing runs
    (True, "never", 3, 1, False),     # "never" stops it, friends or not
    (True, "always", 0, 0, False),    # nothing to do: stopped, not idle
    (True, "always", 1, 0, True),
    (True, "sometimes", 0, 1, True),
])
def test_the_daemon_runs_only_when_it_has_a_job(enabled, policy, friends, invites, run) -> None:
    w = tc.wanted(enabled=enabled, internet_policy=policy, friends=friends, open_invites=invites)
    assert w.run is run
    if not run:
        assert not w.main_onion and not w.pairing_onion


def test_no_friends_means_no_main_onion_in_the_config() -> None:
    only_invites = tc.render_torrc(tc.wanted(enabled=True, internet_policy="always",
                                             friends=0, open_invites=1))
    assert "hs-main" not in only_invites and "hs-pairing" in only_invites
    both = tc.render_torrc(tc.wanted(enabled=True, internet_policy="always",
                                     friends=2, open_invites=1))
    assert "hs-main" in both and "unix:/run/fh/listen/main.sock" in both
    for text in (only_invites, both):
        assert "SocksPort unix:/run/fh/socks" in text
        assert "ControlPort" not in text  # no control port, ever
        assert "SocksPort 9050" not in text


def test_an_onion_appearing_is_a_restart_never_a_reload() -> None:
    off = tc.Wanted(False, False, False)
    invites = tc.Wanted(True, False, True)
    friends = tc.Wanted(True, True, True)
    assert tc.plan_change(None, off) == "none"
    assert tc.plan_change(off, invites) == "start"
    assert tc.plan_change(invites, friends) == "restart"  # first friend: S1 finding 1
    assert tc.plan_change(friends, friends) == "reload"   # client list changed
    assert tc.plan_change(friends, tc.Wanted(True, True, False)) == "restart"
    assert tc.plan_change(friends, off) == "stop"


def test_the_folder_writes_tors_key_formats(tmp_path) -> None:
    folder = tc.TorFolder(tmp_path)
    folder.ensure()
    reach = tc.ReachKey.generate()
    folder.authorize("f1", reach.public)
    auth = (tmp_path / "data/hs-main/authorized_clients/f1.auth").read_text()
    assert auth == f"descriptor:x25519:{tc.tor_b32(reach.public)}\n"
    assert len(tc.tor_b32(reach.public)) == 52
    assert folder.authorized() == {"f1"}
    onion = bytes(range(32))
    folder.hold_key_for("f1", onion, reach.private)
    line = (tmp_path / "data/clientauth/f1.auth_private").read_text()
    assert line == f"{fid.onion_address(onion)[:-6]}:descriptor:x25519:{tc.tor_b32(reach.private)}\n"
    folder.deauthorize("f1")
    folder.drop_key_for("f1")
    assert folder.authorized() == set()
    with pytest.raises(ValueError):
        folder.authorize("../escape", reach.public)


def test_a_stray_key_file_name_is_ignored(tmp_path) -> None:
    folder = tc.TorFolder(tmp_path)
    folder.ensure()
    (tmp_path / "data/hs-main/authorized_clients/x!.auth").write_text("junk")
    (tmp_path / "data/clientauth/X Y.auth_private").write_text("junk")
    assert folder.authorized() == set() and folder.held() == set()


def test_key_files_are_written_through_a_fresh_temp_name(tmp_path, monkeypatch) -> None:
    folder = tc.TorFolder(tmp_path)
    folder.ensure()
    target = tmp_path / "data/torrc"
    # A file sitting at the old fixed temp name is neither used nor touched.
    victim = tmp_path / "victim"
    victim.write_text("keep")
    fixed = target.with_name(".torrc.tmp")
    fixed.write_text("planted")
    opened: list[tuple[str, int]] = []
    real_open = os.open

    def spy(path, flags, mode=0o777):
        opened.append((str(path), flags))
        return real_open(path, flags, mode)

    monkeypatch.setattr(tc.os, "open", spy)
    folder.write_torrc(tc.Wanted(True, False, True))
    (path, flags), = opened
    assert path != str(fixed) and flags & os.O_EXCL
    assert flags & getattr(os, "O_NOFOLLOW", 0) == getattr(os, "O_NOFOLLOW", 0)
    assert fixed.read_text() == "planted" and victim.read_text() == "keep"
    assert "HiddenServiceDir" in target.read_text()
    assert not [p for p in target.parent.iterdir() if p.name.endswith(".tmp") and p != fixed]


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_every_folder_tor_reads_is_private(tmp_path) -> None:
    # Tor refuses to start when an onion folder is group- or world-readable
    # (found by the F1 two-house rig).
    import stat

    os.umask(0o022)
    folder = tc.TorFolder(tmp_path / "fh" / "tor")
    folder.ensure()
    for path in (folder.root, folder.data, folder.data / "hs-main",
                 folder.data / "hs-main" / "authorized_clients", folder.data / "hs-pairing",
                 folder.data / "clientauth", folder.data / "tor", folder.run):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o700, path


def test_the_onion_key_comes_from_tors_hostname_file(tmp_path) -> None:
    folder = tc.TorFolder(tmp_path)
    folder.ensure()
    assert folder.onion_key() is None
    key = bytes(range(32))
    (tmp_path / "data/hs-main/hostname").write_text(fid.onion_address(key) + "\n")
    assert folder.onion_key() == key
    folder.forget_onion("hs-main")
    assert folder.onion_key() is None


# ─── the SOCKS dialer ─────────────────────────────────────────────────────


async def _fake_socks(reply: int, seen: list):
    async def handler(reader, writer):
        assert await reader.readexactly(3) == b"\x05\x01\x00"
        writer.write(b"\x05\x00")
        head = await reader.readexactly(5)
        host = await reader.readexactly(head[4])
        port = struct.unpack(">H", await reader.readexactly(2))[0]
        seen.append((head[:4], host.decode(), port))
        writer.write(bytes([5, reply, 0, 1]) + b"\x00" * 6)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_socks_asks_for_the_onion_on_port_80() -> None:
    seen: list = []
    onion = fid.onion_address(bytes(range(32)))
    server, port = await _fake_socks(0, seen)
    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await fc.socks_connect(reader, writer, onion)
        writer.close()
    assert seen == [(b"\x05\x01\x00\x03", onion, 80)]


async def test_socks_refusals_read_as_plain_words() -> None:
    onion = fid.onion_address(bytes(range(32)))
    server, port = await _fake_socks(0xF4, [])
    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        with pytest.raises(fc.Unreachable, match="client authorization missing"):
            await fc.socks_connect(reader, writer, onion)
        writer.close()


async def test_socks_refuses_anything_but_an_onion() -> None:
    reader = asyncio.StreamReader()
    with pytest.raises(ValueError):
        await fc.socks_connect(reader, None, "example.com")  # type: ignore[arg-type]


# ─── the listener ─────────────────────────────────────────────────────────


def _house(tmp_path, name):
    keys = fid.create(tmp_path / f"{name}.json")
    hello = fs.Hello(keys.address_statement(bytes(32), 1_800_000_000), b"\x02" * 8)
    return keys, hello


def _write_friends(folder, *keys_list) -> None:
    doc = {k.session_key.hex(): k.house_key.hex() for k in keys_list}
    path = folder / fl.FRIENDS_FILE
    path.write_text(json.dumps(doc), encoding="utf-8")
    # Make sure the change is seen even within one mtime tick.
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


async def _listener(tmp_path, keys, hello):
    folder = tmp_path / "listener"
    folder.mkdir()
    (folder / fl.HELLO_FILE).write_bytes(hello.to_bytes())
    state = fl.State(folder, keys)
    server = await asyncio.start_server(lambda r, w: fl.handle(r, w, state), "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], folder, state


async def _call(port, keys, hello, friend):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    return await fs.initiate(reader, writer, keys=keys, me=hello, friend=friend)


async def test_the_listener_answers_friends_and_reports_im_back(tmp_path, capsys) -> None:
    b_keys, b_hello = _house(tmp_path, "b")
    a_keys, a_hello = _house(tmp_path, "a")
    server, port, folder, _ = await _listener(tmp_path, b_keys, b_hello)
    _write_friends(folder, a_keys)
    async with server:
        sess = await _call(port, a_keys, a_hello, fs.Friend(b_keys.house_key, b_keys.session_key))
        assert await sess.ping() >= 0
        await sess.send(fs.Kind.IM_BACK)
        await sess.close()
        await asyncio.sleep(0.1)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e["event"] for e in events] == ["connected", "im_back"]
    assert events[1]["house"] == a_keys.house_key.hex()


async def test_removing_a_friend_takes_effect_on_the_next_connection(tmp_path) -> None:
    b_keys, b_hello = _house(tmp_path, "b")
    a_keys, a_hello = _house(tmp_path, "a")
    server, port, folder, _ = await _listener(tmp_path, b_keys, b_hello)
    b_view = fs.Friend(b_keys.house_key, b_keys.session_key)
    _write_friends(folder, a_keys)
    async with server:
        sess = await _call(port, a_keys, a_hello, b_view)
        await sess.close()
        _write_friends(folder)  # A removed
        with pytest.raises(fs.SessionRefused):
            await _call(port, a_keys, a_hello, b_view)


async def test_an_unreadable_friend_list_lets_nobody_in(tmp_path) -> None:
    b_keys, b_hello = _house(tmp_path, "b")
    a_keys, a_hello = _house(tmp_path, "a")
    server, port, folder, _ = await _listener(tmp_path, b_keys, b_hello)
    (folder / fl.FRIENDS_FILE).write_text("{not json", encoding="utf-8")
    async with server:
        with pytest.raises(fs.SessionRefused):
            await _call(port, a_keys, a_hello, fs.Friend(b_keys.house_key, b_keys.session_key))


async def test_one_friend_cant_hold_every_slot(tmp_path) -> None:
    b_keys, b_hello = _house(tmp_path, "b")
    a_keys, a_hello = _house(tmp_path, "a")
    server, port, folder, _ = await _listener(tmp_path, b_keys, b_hello)
    _write_friends(folder, a_keys)
    b_view = fs.Friend(b_keys.house_key, b_keys.session_key)
    async with server:
        held = [await _call(port, a_keys, a_hello, b_view) for _ in range(fl.MAX_PER_FRIEND)]
        with pytest.raises(fs.SessionRefused):
            await _call(port, a_keys, a_hello, b_view)
        for sess in held:
            await sess.close()


async def test_silent_callers_cant_hold_every_slot(tmp_path) -> None:
    b_keys, b_hello = _house(tmp_path, "b")
    a_keys, a_hello = _house(tmp_path, "a")
    server, port, folder, state = await _listener(tmp_path, b_keys, b_hello)
    _write_friends(folder, a_keys)
    async with server:
        silent = [await asyncio.open_connection("127.0.0.1", port)
                  for _ in range(fl.MAX_CONNECTIONS)]
        # The oldest silent callers were closed to make room.
        first_reader = silent[0][0]
        assert await asyncio.wait_for(first_reader.read(), 2.0) == b""
        assert len(state._handshaking) <= fl.MAX_HANDSHAKING
        sess = await _call(port, a_keys, a_hello, fs.Friend(b_keys.house_key, b_keys.session_key))
        assert await sess.ping() >= 0
        await sess.close()
        for _, w in silent:
            w.close()


async def test_a_ping_flood_closes_the_connection(tmp_path) -> None:
    b_keys, b_hello = _house(tmp_path, "b")
    a_keys, a_hello = _house(tmp_path, "a")
    server, port, folder, _ = await _listener(tmp_path, b_keys, b_hello)
    _write_friends(folder, a_keys)
    async with server:
        sess = await _call(port, a_keys, a_hello, fs.Friend(b_keys.house_key, b_keys.session_key))
        for _ in range(fl.PINGS_PER_CONNECTION):
            assert await sess.ping() >= 0
        with pytest.raises(fs.SessionRefused):
            await sess.ping(timeout=5)
        await sess.close()


def test_admitting_closes_the_oldest_unfinished_handshake_first() -> None:
    from collections import deque

    class W:
        closed = False

        def close(self):
            self.closed = True

    a, b = W(), W()
    waiting = deque([a, b])
    assert fl._admit(1, 8, waiting, 2) and a.closed and not b.closed  # too many waiting
    assert fl._admit(8, 8, waiting, 4) and b.closed                    # every slot taken
    assert not fl._admit(8, 8, deque(), 4)                             # all past the handshake
