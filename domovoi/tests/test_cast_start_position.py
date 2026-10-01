"""A cast starts the room where the listener is (2026-09-30).

The phone and the browser hand a room their queue with
``POST /api/music/play-tracks`` -> ``/v1/admin/music/play-tracks``. The
phone sent its whole queue, so the room always started at the queue's
first track whichever song was playing; and nothing could carry the place
in the song. The clients now send the queue from the current track on, plus
``start_sec``; the core starts MPD that far into the first track and still
leaves it paused for the music_ready handshake.

Pure client and schema checks against a recording MPD connection and the
stub: no Docker, no DB, no ``requires_db``, so they can never skip. The
endpoint and proxy halves (which need the DB) are in test_admin_endpoints.py
and test_browser_player_api.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from pydantic import ValidationError

from domovoi.clients.mpd import MPDStubClient, RealMPDClient


class _RecordingMPD:
    """The python-mpd2 calls prepare_tracks makes, recorded in order.

    ``library`` holds the titles a search can find; ``refuse_seek`` makes
    ``seek`` fail the way MPD does past the end of a file."""

    def __init__(self, library: list[str], *, refuse_seek: bool = False) -> None:
        self.library = library
        self.refuse_seek = refuse_seek
        self.sent: list[str] = []

    async def clear(self) -> None:
        self.sent.append("clear")

    async def search(self, *args: str) -> list[dict[str, Any]]:
        # Only exact title searches hit, which is enough for these specs.
        if len(args) >= 2 and args[0] == "title" and args[1] in self.library:
            return [{"file": f"music/{args[1]}.mp3", "title": args[1]}]
        return []

    async def add(self, file: str) -> None:
        self.sent.append(f"add {file}")

    async def play(self, pos: Any = None) -> None:
        self.sent.append("play" if pos is None else f"play {pos}")

    async def seek(self, pos: Any, t: Any) -> None:
        if self.refuse_seek:
            raise RuntimeError("[2@0] {seek} Decoder failed to seek")
        self.sent.append(f"seek {pos} {t}")

    async def pause(self, state: Any) -> None:
        self.sent.append(f"pause {state}")


def _client(conn: _RecordingMPD) -> RealMPDClient:
    client = RealMPDClient("127.0.0.1", 1)

    @asynccontextmanager
    async def one_connection():
        yield conn

    client._connect = one_connection  # type: ignore[method-assign]
    return client


def _specs(*titles: str) -> list[dict[str, str]]:
    return [{"title": t} for t in titles]


async def test_start_sec_starts_the_first_track_part_way_in_and_paused() -> None:
    conn = _RecordingMPD(["Three", "Four"])
    queued = await _client(conn).prepare_tracks(_specs("Three", "Four"), start_sec=73.6)
    assert [s["title"] for s in queued] == ["Three", "Four"]
    # `seek 0 73` plays queue entry 0 from 73 s; the pause right after keeps
    # the music_ready handshake exactly as before.
    assert conn.sent[-2:] == ["seek 0 73", "pause 1"]
    assert "play" not in conn.sent


async def test_no_start_sec_is_the_old_play_then_pause() -> None:
    conn = _RecordingMPD(["Three"])
    await _client(conn).prepare_tracks(_specs("Three"))
    assert conn.sent == ["clear", "add music/Three.mp3", "play", "pause 1"]


async def test_position_is_dropped_when_the_first_track_is_not_found() -> None:
    """The position belongs to the first REQUESTED song. MPD could not find
    it, so the room starts the next song from its top, not 73 s in."""
    conn = _RecordingMPD(["Four"])
    queued = await _client(conn).prepare_tracks(_specs("Missing", "Four"), start_sec=73)
    assert [s["title"] for s in queued] == ["Four"]
    assert conn.sent[-2:] == ["play", "pause 1"]
    assert not any(c.startswith("seek") for c in conn.sent)


async def test_a_refused_seek_still_casts_from_the_top() -> None:
    conn = _RecordingMPD(["Three"], refuse_seek=True)
    queued = await _client(conn).prepare_tracks(_specs("Three"), start_sec=9999)
    assert len(queued) == 1
    assert conn.sent[-2:] == ["play", "pause 1"]


async def test_nothing_found_touches_no_transport() -> None:
    conn = _RecordingMPD([])
    assert await _client(conn).prepare_tracks(_specs("Missing"), start_sec=30) == []
    assert conn.sent == ["clear"]


async def test_the_stub_lands_where_the_real_daemon_does() -> None:
    stub = MPDStubClient()
    await stub.prepare_tracks(_specs("Three", "Four"), start_sec=73.6)
    assert await stub.state() == "pause"
    song = await stub.current_song()
    assert song is not None and song["title"] == "Three"
    assert stub._song is not None and stub._song["_elapsed"] == 73.0

    plain = MPDStubClient()
    await plain.prepare_tracks(_specs("Three"))
    assert plain._song is not None and "_elapsed" not in plain._song


# ─── the request bodies ─────────────────────────────────────────────────────


def test_core_body_takes_an_optional_start_sec() -> None:
    from domovoi.main import _AdminPlayTracksBody

    assert _AdminPlayTracksBody(room_id="den", track_ids=[1]).start_sec == 0
    assert _AdminPlayTracksBody(room_id="den", track_ids=[1], start_sec=73).start_sec == 73
    with pytest.raises(ValidationError):
        _AdminPlayTracksBody(room_id="den", track_ids=[1], start_sec=-1)


def test_web_body_takes_an_optional_start_sec() -> None:
    from web.backend.schemas import CastTracksRequest

    assert CastTracksRequest(room_id="den", track_ids=[1]).start_sec == 0
    assert CastTracksRequest(room_id="den", track_ids=[1], start_sec=12.5).start_sec == 12.5
    with pytest.raises(ValidationError):
        CastTracksRequest(room_id="den", track_ids=[1], start_sec=-0.5)
