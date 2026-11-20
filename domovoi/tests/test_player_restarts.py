"""A room's music player that restarts comes back where the music was
(domovoi/player_restarts.py).

MPD restores its state file, written 120 s after the last change to the
queue or the player: a daemon that died as a song ended came back PLAYING
that song from its middle, and in a group every room heard it (2026-11-17,
1.3.0). These play that restart on a stub player (the restored daemon:
smaller ``uptime``, the old song at the old place) and check the listen
logger's look puts the music back.
"""

from __future__ import annotations

import asyncio

from domovoi.clients.mpd import MPDStubClient
from domovoi.player_restarts import RestartWatch
from domovoi.workers.listen_logger import ListenLogger

ROOM = "kitchen"
FILES = ["Band/Album/01 First.mp3", "Band/Album/02 Second.mp3", "Band/Album/03 Third.mp3"]
LENGTH = 240.0


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _run(coro):
    return asyncio.run(coro)


async def _playing(at: int, sec: float, *, uptime: float = 5000.0) -> MPDStubClient:
    """A player on FILES, playing entry ``at`` ``sec`` seconds in."""
    p = MPDStubClient()
    await p.prepare_files(FILES)
    for e in p._queue:
        e["duration"] = LENGTH
    await p.play_id(p._queue[at]["id"])
    await p.seek_to(sec)
    p.uptime = uptime
    return p


async def _restart(p: MPDStubClient, *, at: int, sec: float) -> None:
    """The daemon dies and comes back from its state file: a new daemon
    (small uptime) playing entry ``at`` from ``sec``."""
    await p.play_id(p._queue[at]["id"])
    await p.seek_to(sec)
    p.uptime = 2.0


async def _where(p: MPDStubClient) -> tuple[str, int | None, float | None]:
    snap = await p.playback_snapshot()
    return snap["state"], snap["pos"], snap["elapsed"]


def test_a_player_that_died_as_its_song_ended_plays_the_next_song_not_the_old_middle() -> None:
    async def go() -> None:
        clock = _Clock()
        watch = RestartWatch(clock=clock)
        p = await _playing(1, 236.0)
        assert not await watch.look(ROOM, p, await p.playback_snapshot())
        clock.t += 5.0  # the song ran out; the daemon died with it
        await _restart(p, at=1, sec=120.0)  # its state file: two minutes in
        assert await watch.look(ROOM, p, await p.playback_snapshot())
        assert await _where(p) == ("play", 2, 0.0)

    _run(go())


def test_a_player_that_died_mid_song_goes_back_to_where_the_song_was() -> None:
    async def go() -> None:
        clock = _Clock()
        watch = RestartWatch(clock=clock)
        p = await _playing(1, 170.0)
        assert not await watch.look(ROOM, p, await p.playback_snapshot())
        clock.t += 5.0
        await _restart(p, at=0, sec=120.0)  # an older file: the song before
        assert await watch.look(ROOM, p, await p.playback_snapshot())
        assert await _where(p) == ("play", 1, 175.0)

    _run(go())


def test_the_last_song_that_ended_with_its_player_stops_the_music() -> None:
    async def go() -> None:
        clock = _Clock()
        watch = RestartWatch(clock=clock)
        p = await _playing(2, 238.0)
        await watch.look(ROOM, p, await p.playback_snapshot())
        clock.t += 5.0
        await _restart(p, at=2, sec=120.0)
        assert await watch.look(ROOM, p, await p.playback_snapshot())
        assert (await _where(p))[0] == "stop"

    _run(go())


def test_the_same_player_moving_back_is_a_person_and_is_left_alone() -> None:
    async def go() -> None:
        clock = _Clock()
        watch = RestartWatch(clock=clock)
        p = await _playing(1, 200.0)
        await watch.look(ROOM, p, await p.playback_snapshot())
        clock.t += 5.0
        await p.seek_to(30.0)  # "go back", the daemon still running
        p.uptime = 5005.0
        assert not await watch.look(ROOM, p, await p.playback_snapshot())
        assert await _where(p) == ("play", 1, 30.0)

    _run(go())


def test_a_restart_while_paused_or_after_a_long_gap_moves_nothing() -> None:
    async def go() -> None:
        clock = _Clock()
        watch = RestartWatch(clock=clock)
        p = await _playing(1, 200.0)
        await p.pause()
        await watch.look(ROOM, p, await p.playback_snapshot())
        clock.t += 5.0
        await _restart(p, at=1, sec=120.0)
        assert not await watch.look(ROOM, p, await p.playback_snapshot())
        assert await _where(p) == ("play", 1, 120.0)

        q = await _playing(1, 200.0)
        await watch.look("den", q, await q.playback_snapshot())
        clock.t += 600.0
        await _restart(q, at=1, sec=120.0)
        assert not await watch.look("den", q, await q.playback_snapshot())

    _run(go())


def test_the_listen_loggers_look_puts_a_restarted_player_back() -> None:
    """The wiring: the worker that reads every room's player each tick is
    the one that notices the restart."""

    async def go() -> None:
        clock = _Clock()
        logger = ListenLogger(clock=clock)
        p = await _playing(0, 237.0)
        await logger._look(ROOM, p)
        clock.t += 5.0
        await _restart(p, at=0, sec=120.0)
        await logger._look(ROOM, p)
        assert await _where(p) == ("play", 1, 0.0)

    _run(go())


def test_the_real_players_snapshot_carries_its_uptime() -> None:
    """``uptime`` comes from MPD's ``stats``, on the same connection; a
    daemon that refuses ``stats`` still answers the rest of the look."""
    from domovoi.clients import mpd as mpd_module
    from domovoi.tests.test_mpd_protocol import FakeMPD

    async def go() -> None:
        replies = {"status": "state: play\nsong: 1\nsongid: 7\nelapsed: 61.5\nduration: 240.0\n",
                   "currentsong": "file: a.mp3\n", "stats": "uptime: 4321\nplaytime: 400\n"}
        async with FakeMPD(replies) as fake:
            snap = await mpd_module.RealMPDClient("127.0.0.1", fake.port).playback_snapshot()
        assert snap["uptime"] == 4321.0 and snap["pos"] == 1 and snap["elapsed"] == 61.5
        assert fake.connections == 1
        replies["stats"] = "ACK [5@0] {stats} unknown command"
        async with FakeMPD(replies) as fake:
            snap = await mpd_module.RealMPDClient("127.0.0.1", fake.port).playback_snapshot()
        assert snap["uptime"] is None and snap["file"] == "a.mp3"

    _run(go())
