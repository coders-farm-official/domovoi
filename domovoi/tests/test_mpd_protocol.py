"""domovoi/clients/mpd_protocol.py — Domovoi's own MPD client (replaced
python-mpd2, LGPL, 2026-10-15) — against a fake MPD daemon on a loopback
port that speaks the protocol and records every line it is sent. Then
RealMPDClient and the dashboard's now-playing read on top of it. DB-free.
"""

from __future__ import annotations

import asyncio
from typing import Callable

import pytest

from domovoi.clients import mpd as mpd_module
from domovoi.clients import mpd_protocol as proto
from domovoi.clients.mpd_protocol import CommandError, MPDConnection, MPDConnectionError, ProtocolError

Reply = str | Callable[[str], str]


class FakeMPD:
    """A daemon on 127.0.0.1:<port>. ``replies`` maps a command name to the
    answer text (without the final OK) or ``"ACK ..."``; ``hello`` None
    says nothing at all; ``hang`` names a command it never answers."""

    def __init__(self, replies: dict[str, Reply] | None = None, *, hello: str | None = "OK MPD 0.23.12\n",
                 hang: str | None = None, close_on: str | None = None) -> None:
        self.replies = replies or {}
        self.hello = hello
        self.hang = hang
        self.close_on = close_on
        self.lines: list[str] = []
        self.connections = 0
        self.server: asyncio.base_events.Server | None = None
        self.port = 0
        self._handlers: set[asyncio.Task] = set()

    async def __aenter__(self) -> "FakeMPD":
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        assert self.server is not None
        self.server.close()
        for task in self._handlers:                  # a hung "daemon" ends with the test
            task.cancel()
        await self.server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        task = asyncio.current_task()
        if task is not None:
            self._handlers.add(task)
        try:
            if self.hello is None:
                await asyncio.sleep(30)
                return
            writer.write(self.hello.encode())
            await writer.drain()
            while True:
                raw = await reader.readline()
                if not raw:
                    return
                line = raw.decode().rstrip("\n")
                self.lines.append(line)
                name = line.split(" ", 1)[0]
                if name == self.hang:
                    await asyncio.sleep(30)
                    return
                if name == self.close_on:
                    writer.write(b"file: half/an/answer.mp3\n")
                    await writer.drain()
                    return
                reply = self.replies.get(name, "")
                text = reply(line) if callable(reply) else reply
                if text.startswith("ACK "):
                    writer.write(text.encode() + b"\n")
                else:
                    writer.write(text.encode() + b"OK\n")
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()


async def connected(fake: FakeMPD) -> MPDConnection:
    c = MPDConnection()
    await c.connect("127.0.0.1", fake.port)
    return c


# ─── the protocol ────────────────────────────────────────────────────────


async def test_hello_and_an_object_answer() -> None:
    async with FakeMPD({"status": "volume: 40\nState: pause\nelapsed: 12.500\n"}) as fake:
        c = await connected(fake)
        assert c.mpd_version == "0.23.12" and c.connected
        assert await c.status() == {"volume": "40", "state": "pause", "elapsed": "12.500"}
        c.disconnect()
        c.disconnect()                                    # twice is fine
        assert not c.connected
    assert fake.lines == ["status"]


async def test_songs_split_at_each_file_and_repeated_keys_gather() -> None:
    songs = ("file: a/one.mp3\nTitle: One\nArtist: X\nArtist: Y\nPos: 0\nId: 7\n"
             "file: b/two.flac\nTitle: Two\nPos: 1\nId: 8\n")
    async with FakeMPD({"search": songs, "playlistinfo": songs, "currentsong": "file: a/one.mp3\nArtist: X\nArtist: Y\n"}) as fake:
        c = await connected(fake)
        expected = [{"file": "a/one.mp3", "title": "One", "artist": ["X", "Y"], "pos": "0", "id": "7"},
                    {"file": "b/two.flac", "title": "Two", "pos": "1", "id": "8"}]
        assert await c.search("title", "One") == expected
        assert await c.playlistinfo() == expected
        assert await c.currentsong() == {"file": "a/one.mp3", "artist": ["X", "Y"]}
        c.disconnect()


async def test_empty_answers() -> None:
    async with FakeMPD() as fake:
        c = await connected(fake)
        assert await c.search("any", "nothing") == []
        assert await c.currentsong() == {}
        assert await c.status() == {}
        assert await c.addid("x.mp3") is None
        c.disconnect()


async def test_single_values_and_commands_that_answer_nothing() -> None:
    async with FakeMPD({"addid": "Id: 42\n", "update": "updating_db: 3\n"}) as fake:
        c = await connected(fake)
        assert await c.addid("http://example.lan/stream") == "42"
        assert await c.update() == "3"
        for call in (c.clear(), c.add("a.mp3"), c.play(), c.play(0), c.pause(1), c.stop(), c.next(),
                     c.previous(), c.seek(0, "73"), c.seekcur("+30"), c.setvol(55), c.delete("0:3"),
                     c.deleteid(7), c.moveid(7, 2), c.addtagid(42, "title", "A Title")):
            assert await call is None
        c.disconnect()
    assert fake.lines == ['addid "http://example.lan/stream"', "update", "clear", 'add "a.mp3"', "play",
                          'play "0"', 'pause "1"', "stop", "next", "previous", 'seek "0" "73"', 'seekcur "+30"',
                          'setvol "55"', 'delete "0:3"', 'deleteid "7"', 'moveid "7" "2"',
                          'addtagid "42" "title" "A Title"']


async def test_arguments_are_quoted_and_escaped() -> None:
    async with FakeMPD() as fake:
        c = await connected(fake)
        await c.search("title", 'say "hi" \\ bye', "artist", "Ünïcode")
        c.disconnect()
    assert fake.lines == ['search "title" "say \\"hi\\" \\\\ bye" "artist" "Ünïcode"']


async def test_an_argument_with_a_line_break_is_refused_and_nothing_is_sent() -> None:
    """It would end the command and start another one (``kill``, say)."""
    async with FakeMPD() as fake:
        c = await connected(fake)
        for bad in ("x\nkill", "x\rkill"):
            with pytest.raises(CommandError) as e:
                await c.addtagid(1, "title", bad)
            assert e.value.code == proto.ACK_ERROR_ARG
            assert mpd_module._is_ack(e.value)                 # the wrapper skips it like a refusal
        assert await c.status() == {}                          # the connection is still fine
        c.disconnect()
    assert fake.lines == ["status"]


async def test_ack_is_a_command_error_with_mpds_own_text_and_the_connection_survives() -> None:
    async with FakeMPD({"next": "ACK [55@0] {next} Not playing", "add": "ACK [50@0] {add} No such directory",
                        "status": "state: stop\n"}) as fake:
        c = await connected(fake)
        with pytest.raises(CommandError) as e:
            await c.next()
        assert str(e.value) == "[55@0] {next} Not playing"
        assert (e.value.code, e.value.command, e.value.message) == (55, "next", "Not playing")
        assert mpd_module._is_not_playing(e.value)
        with pytest.raises(CommandError) as e:
            await c.add("gone.mp3")
        assert mpd_module._is_ack(e.value) and not mpd_module._is_not_playing(e.value)
        assert await c.status() == {"state": "stop"}
        c.disconnect()


async def test_no_hello_in_time(monkeypatch) -> None:
    async with FakeMPD(hello=None) as fake:
        c = MPDConnection()
        with pytest.raises(MPDConnectionError, match="hello"):
            await c.connect("127.0.0.1", fake.port, timeout=0.2)
        assert not c.connected


async def test_something_that_isnt_mpd() -> None:
    async with FakeMPD(hello="SSH-2.0-OpenSSH_9.6\n") as fake:
        with pytest.raises(ProtocolError):
            await MPDConnection().connect("127.0.0.1", fake.port)


async def test_a_refused_port() -> None:
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    with pytest.raises(MPDConnectionError) as e:
        await MPDConnection().connect("127.0.0.1", port)
    assert isinstance(e.value, ConnectionError)               # an OSError too, for callers that catch those


async def test_the_daemon_going_away_mid_answer() -> None:
    async with FakeMPD(close_on="playlistinfo") as fake:
        c = await connected(fake)
        with pytest.raises(MPDConnectionError):
            await c.playlistinfo()
        assert not c.connected
        with pytest.raises(MPDConnectionError):
            await c.status()


async def test_a_cancelled_command_drops_the_connection() -> None:
    """What is left of its answer would be read as the next command's."""
    async with FakeMPD(hang="status") as fake:
        c = await connected(fake)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(c.status(), 0.2)
        assert not c.connected


async def test_an_unexpected_answer_to_a_command_that_answers_nothing() -> None:
    async with FakeMPD({"clear": "surprise: yes\n"}) as fake:
        c = await connected(fake)
        with pytest.raises(ProtocolError):
            await c.clear()
        c.disconnect()


async def test_a_binary_chunk_is_read_past() -> None:
    async with FakeMPD({"currentsong": "size: 3\nbinary: 3\n\x01\x02\x03\nfile: after.mp3\n"}) as fake:
        c = await connected(fake)
        song = await c.currentsong()
        assert song["file"] == "after.mp3" and song["binary"] == "3"
        c.disconnect()


def test_parsing_helpers() -> None:
    assert proto.parse_objects(["a: 1", "b: 2", "a: 3"]) == [{"a": ["1", "3"], "b": "2"}]
    assert proto.parse_item(["Id: 5"]) == "5" and proto.parse_item([]) is None
    assert proto.parse_item(["a: 1", "b: 2"]) is None
    with pytest.raises(ProtocolError):
        proto.parse_objects(["no separator"])
    assert proto.quote('a"b\\c') == '"a\\"b\\\\c"'


# ─── RealMPDClient on top of it ──────────────────────────────────────────


def _queue_after_add() -> Callable[[str], str]:
    """playlistinfo: one old entry, then the two added files."""
    calls = {"n": 0}

    def reply(line: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return "file: old.mp3\nPos: 0\nId: 1\n"
        return "file: Artist/a.mp3\nPos: 0\nId: 11\nfile: Artist/b.mp3\nPos: 1\nId: 12\n"
    return reply


async def test_real_client_prepare_files_skips_a_refused_uri() -> None:
    async with FakeMPD({"playlistinfo": _queue_after_add(),
                        "add": lambda line: "ACK [50@0] {add} No such directory" if "gone" in line else ""}) as fake:
        client = mpd_module.RealMPDClient("127.0.0.1", fake.port)
        queued = await client.prepare_files(["Artist/a.mp3", "gone.mp3", "Artist/b.mp3"], start_sec=12.9)
    assert [(e["file"], e["id"], e["pos"]) for e in queued] == [("Artist/a.mp3", 11, 0), ("Artist/b.mp3", 12, 1)]
    assert fake.lines == ["playlistinfo", 'add "Artist/a.mp3"', 'add "gone.mp3"', 'add "Artist/b.mp3"',
                          'delete "0:1"', 'seek "0" "12"', 'pause "1"', "playlistinfo"]
    assert fake.connections == 1


async def test_real_client_next_on_a_stopped_daemon_is_mpd_not_playing() -> None:
    async with FakeMPD({"status": "state: stop\n", "next": "ACK [55@0] {next} Not playing"}) as fake:
        client = mpd_module.RealMPDClient("127.0.0.1", fake.port)
        with pytest.raises(mpd_module.MPDNotPlaying):
            await client.next()


async def test_real_client_queue_and_state_reads() -> None:
    async with FakeMPD({"playlistinfo": "file: a.mp3\nPos: 0\nId: 5\nfile: b.mp3\nPos: 1\n",
                        "status": "state: play\nvolume: 70\nelapsed: 3.250\nplaylistlength: 2\n",
                        "currentsong": "file: a.mp3\nTitle: A\n", "addid": "Id: 9\n",
                        "search": "file: c.mp3\nTitle: C\n"}) as fake:
        client = mpd_module.RealMPDClient("127.0.0.1", fake.port)
        assert [(e["file"], e["id"], e["pos"]) for e in await client.queue_list()] == [("a.mp3", 5, 0)]
        assert await client.state() == "play"
        assert await client.get_volume() == 70
        assert await client.elapsed_sec() == pytest.approx(3.25)
        assert await client.current_song() == {"file": "a.mp3", "title": "A"}
        added = await client.queue_add_tracks([{"title": "C"}])
        assert [(e["file"], e["id"]) for e in added] == [("c.mp3", 9)]
        assert await client.play_url("http://radio.lan/stream", title="Radio", artist="Lan") is True
    assert 'addtagid "9" "title" "Radio"' in fake.lines and 'addtagid "9" "artist" "Lan"' in fake.lines


async def test_dashboard_now_playing_read() -> None:
    from web.backend.api import music as music_api

    async with FakeMPD({"status": "state: pause\nelapsed: 61.000\n", "currentsong": "file: x.mp3\nTitle: X\n"}) as fake:
        state, song, elapsed = await music_api._read_mpd("127.0.0.1", fake.port)
    assert (state, song, elapsed) == ("pause", {"file": "x.mp3", "title": "X"}, 61.0)
    async with FakeMPD(hello=None) as fake:
        assert await music_api._read_mpd("127.0.0.1", fake.port, timeout=0.2) == ("stop", None, None)
