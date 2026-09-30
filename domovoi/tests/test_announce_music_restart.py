"""An announcement never waits on a room's music stream.

`StreamSession.announce` ends by restarting the room's music (the
announcement's response_start stopped mpg123 on the satellite), and every
music_start now goes through `send_music_start`, which can spend up to
MUSIC_STREAM_READY_TIMEOUT_SEC (3 s) opening a stream that is not serving.
Every broadcast calls `announce` room by room — the intercom fan-out,
POST /v1/admin/announce, sdk.speech.announce, and a house-wide timer or
reminder — so with that restart awaited inside `announce`, one room whose
stream would not open delayed the announcement in every room after it.

Pinned here (no database, no Docker: real StreamSessions on a fake socket,
the stream check replaced by a timed stand-in):

* a slow restart in one room does not hold up the next room's
  announcement, through both the SDK and the admin broadcast loops — and
  the music still comes back once its stream is ready;
* the restart runs after `announce` returned, so it checks the room last
  thing before its music_start and sends nothing when the room was
  stopped, a turn started there, another announcement's frames are going
  out there, or a later announcement took over;
* it is bounded, and a failure in it is logged, never raised.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import types

import pytest

from domovoi import streaming
from domovoi.clients import mpd as mpd_module
from domovoi.config import settings
from domovoi.sdk import speech as speech_module
from domovoi.streaming import StreamSession
from domovoi.tests.test_streaming import _FakeTTS

URL = {"office": "http://192.0.2.10:8051", "kitchen": "http://192.0.2.10:8052"}


class _Socket:
    """What a StreamSession sends its satellite, with the time it was sent."""

    def __init__(self, app, room: str, sent: list) -> None:
        self.app = app
        self.room = room
        self.sent = sent

    async def send_text(self, data: str) -> None:
        self.sent.append((time.monotonic(), self.room, json.loads(data)))

    async def send_bytes(self, data: bytes) -> None:
        pass


class House:
    def __init__(self) -> None:
        self.app = types.SimpleNamespace(state=types.SimpleNamespace(
            satellite_voice={}, active_sessions={}, resumable_music={},
            pending_music_start={},
        ))
        self.sent: list[tuple[float, str, dict]] = []
        # Seconds each room's stream check takes (0 when absent).
        self.stream_wait: dict[str, float] = {}
        self.checked: list[str] = []

    def room(self, room: str, *, music: bool = False) -> StreamSession:
        sess = StreamSession(_Socket(self.app, room, self.sent), room)
        self.app.state.active_sessions[room] = sess
        if music:
            self.app.state.resumable_music[room] = URL[room]
        return sess

    def frames(self, room: str) -> list[str]:
        return [f["type"] for _, r, f in self.sent if r == room]

    def at(self, room: str, kind: str) -> float:
        return next(t for t, r, f in self.sent if r == room and f["type"] == kind)


@pytest.fixture
def house(monkeypatch):
    h = House()

    async def any_voice(name):
        return (None, None)

    async def stream_check(room_id, stream_url=None, *, timeout=None):
        h.checked.append(room_id)
        await asyncio.sleep(h.stream_wait.get(room_id, 0.0))
        return True

    monkeypatch.setattr(streaming, "get_tts_client", lambda: _FakeTTS())
    monkeypatch.setattr(streaming, "resolve_voice", any_voice)
    monkeypatch.setattr(mpd_module, "ensure_stream_serving", stream_check)
    yield h
    for entry in h.app.state.pending_music_start.values():
        entry["task"].cancel()


async def restarts_done() -> list:
    """Wait for every music restart still running; their outcomes."""
    return await asyncio.gather(
        *list(streaming._ANNOUNCE_MUSIC_RESTARTS), return_exceptions=True,
    )


async def _sdk_broadcast(house: House, monkeypatch, text: str) -> list[str]:
    monkeypatch.setattr(
        speech_module, "_active_sessions_provider",
        lambda: house.app.state.active_sessions,
    )
    return await speech_module.SpeechAPI("test").announce(None, text)


async def _admin_broadcast(house: House, monkeypatch, text: str) -> list[str]:
    from domovoi import main as main_module

    monkeypatch.setattr(
        main_module.app.state, "active_sessions", house.app.state.active_sessions,
        raising=False,
    )
    result = await main_module.admin_announce(main_module._AdminAnnounceBody(message=text))
    return result["announced_to"]


@pytest.mark.parametrize("broadcast", [_sdk_broadcast, _admin_broadcast], ids=["sdk", "admin"])
async def test_a_slow_stream_in_one_room_does_not_hold_up_the_next_room(
    house, monkeypatch, broadcast,
) -> None:
    house.room("office", music=True)
    house.room("kitchen")
    house.stream_wait["office"] = 1.5          # its stream will not open quickly

    began = time.monotonic()
    reached = await broadcast(house, monkeypatch, "Dinner is ready.")
    took = time.monotonic() - began

    assert reached == ["office", "kitchen"]
    assert took < 0.75, f"the broadcast waited {took:.2f} s on the office stream"
    assert house.frames("kitchen")[-1] == "response_end"
    assert house.at("kitchen", "response_start") - began < 0.75
    assert "music_start" not in house.frames("office")     # not yet

    # The office music still comes back, once its stream is ready.
    assert await restarts_done() == [None]
    assert house.frames("office")[-1] == "music_start"
    assert house.sent[-1][2] == {"type": "music_start", "stream_url": URL["office"]}
    assert house.app.state.pending_music_start["office"]["url"] == URL["office"]


async def test_the_music_comes_back_after_an_announcement(house) -> None:
    sess = house.room("office", music=True)
    await sess.announce("The laundry is done.")
    await restarts_done()
    assert house.frames("office") == ["response_start", "response_end", "music_start"]
    assert house.checked == ["office"]


async def test_a_room_without_music_gets_no_restart(house) -> None:
    sess = house.room("office")
    await sess.announce("The laundry is done.")
    assert not streaming._ANNOUNCE_MUSIC_RESTARTS
    assert house.frames("office") == ["response_start", "response_end"]


async def _announce_then(house: House, change) -> None:
    sess = house.room("office", music=True)
    house.stream_wait["office"] = 0.3
    await sess.announce("The laundry is done.")
    await asyncio.sleep(0.05)                  # the restart is waiting on the stream
    change(sess)
    assert await restarts_done() == [None]


async def test_a_room_stopped_while_its_stream_was_readied_gets_no_music_start(house) -> None:
    await _announce_then(house, lambda s: house.app.state.resumable_music.pop("office"))
    assert "music_start" not in house.frames("office")
    assert "office" not in house.app.state.pending_music_start


def _capture(sess) -> None:
    sess.utterance_active = True


def _reply(sess) -> None:
    sess._response_task = asyncio.get_running_loop().create_future()


def _call(sess) -> None:
    sess.dropin_peer = object()


@pytest.mark.parametrize("meanwhile", [_capture, _reply, _call], ids=["capture", "reply", "drop-in"])
async def test_a_turn_or_call_that_started_meanwhile_owns_the_music(house, meanwhile) -> None:
    """A wake word in that room (its capture, then its reply), or a drop-in
    call: a music_start now would spawn mpg123 into the capture, over the
    reply, or into the call. The turn's end auto-resumes the music, and the
    call's end restores it."""
    await _announce_then(house, meanwhile)
    assert "music_start" not in house.frames("office")


async def test_another_announcements_frames_hold_the_restart_back(house) -> None:
    """Two timers due at once in a room with music: the first announcement's
    restart finds the second one's frames going out (its `_announce_task`,
    wf/int-0930) and sends nothing, rather than a music_start the satellite
    would start mpg123 on over that announcement. The second one's own
    restart, when its frames are done, brings the music back."""
    frames_going_out: asyncio.Future = asyncio.get_running_loop().create_future()
    await _announce_then(house, lambda s: setattr(s, "_announce_task", frames_going_out))
    assert "music_start" not in house.frames("office")

    sess = house.app.state.active_sessions["office"]
    frames_going_out.set_result(None)
    sess._announce_task = None
    sess._restart_music_after_announce(URL["office"])
    assert await restarts_done() == [None]
    assert house.frames("office")[-1] == "music_start"


async def test_a_replaced_socket_gets_no_music_start(house) -> None:
    await _announce_then(house, lambda s: house.app.state.active_sessions.pop("office"))
    assert "music_start" not in house.frames("office")
    assert "office" not in house.app.state.pending_music_start


async def test_a_later_announcement_takes_over_the_restart(house) -> None:
    sess = house.room("office", music=True)
    house.stream_wait["office"] = 0.2
    await sess.announce("The laundry is done.")
    await sess.announce("And the dryer.")
    assert await restarts_done() == [None, None]
    assert house.frames("office").count("music_start") == 1
    assert house.frames("office")[-1] == "music_start"


async def test_a_restart_that_fails_is_logged_not_raised(house, monkeypatch, caplog) -> None:
    async def broken(*args, **kwargs):
        raise RuntimeError("socket gone")

    monkeypatch.setattr(streaming, "send_music_start", broken)
    sess = house.room("office", music=True)
    await sess.announce("The laundry is done.")          # does not raise
    assert await restarts_done() == [None]
    assert "music restart for room=office failed: socket gone" in caplog.text


async def test_a_restart_is_bounded(house, monkeypatch, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=streaming.log.name)
    monkeypatch.setattr(settings, "music_stream_ready_timeout_sec", 0.1)
    monkeypatch.setattr(streaming, "_ANNOUNCE_MUSIC_RESTART_MARGIN_SEC", 0.1)
    sess = house.room("office", music=True)
    house.stream_wait["office"] = 30.0                   # never answers
    began = time.monotonic()
    await sess.announce("The laundry is done.")
    assert await restarts_done() == [None]
    assert time.monotonic() - began < 1.5
    assert "music_start" not in house.frames("office")
    assert "music restart for room=office gave up" in caplog.text
