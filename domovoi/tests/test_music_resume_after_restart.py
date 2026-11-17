"""A core restart doesn't stop the music, so it must not forget it either.

1.3.0 device check, 2026-11-16: the garage, a member of the basement's
shared queue, played on through a core restart (an update); the first voice
turn after it ("turn it up") stopped the satellite's player for the
microphone as always, and nothing started it again. The resume intent
(``app.state.resumable_music``) was in memory only and started empty, so
the turn's auto-resume had nothing to send, while the shared queue itself
was read back from the database and the dashboard still showed the garage
playing. "Join the basement" then answered "this room is already playing
that" and left it silent.

Pinned here:

* at startup (``room_music.restore_resume_intents``) every room whose
  player, its own or its shared queue's host's, is playing gets its stream
  back as its resume intent: a shared queue's members (read back from the
  database) and a room playing alone alike;
* a paused or stopped player gets none (a person's pause is forgotten by a
  restart too, and a resume intent would undo it), a player that doesn't
  answer is left out, and an entry set since startup is kept;
* through the real app's startup: the first turn after a restart sends the
  room its music back;
* re-joining a shared queue a room is already in sends its speaker the
  stream again (a way out of any desync), as "play this in the garage too"
  does for a room already listening.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from domovoi import music_groups
from domovoi.clients import mpd as mpd_module
from domovoi.room_music import drain, restore_resume_intents
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_music_rooms import (  # noqa: F401 — the fixture
    FakeDB,
    _app,
    _ctx,
    _sat,
    _say,
    house,
)


async def _playing(house, room: str, song: str = "song.mp3") -> None:
    await house.clients[room].prepare_files([song, "next.mp3"])
    await house.clients[room].resume()


def _restarted():
    """A fresh process's app state: nothing in memory but what boot reads."""
    return _app()


# ─── what startup restores ────────────────────────────────────────────────


async def test_a_shared_queues_rooms_get_the_hosts_stream_back(house) -> None:
    await _playing(house, "garage")
    await music_groups.share(_app(), "garage", ["dining_room", "jordan_office"])
    await drain()
    app = _restarted()
    restored = await restore_resume_intents(app)
    url = mpd_module.own_stream_url_for("garage")
    for room in ("garage", "dining_room", "jordan_office"):
        assert app.state.resumable_music[room] == url, room
    assert set(restored) >= {"garage", "dining_room", "jordan_office"}
    # Nothing is sent at startup: the next turn's auto-resume sends it.
    assert all(not s.started for s in app.state.active_sessions.values())


async def test_a_room_playing_alone_gets_its_own_stream_back(house) -> None:
    await _playing(house, "kids_room")
    app = _restarted()
    await restore_resume_intents(app)
    assert app.state.resumable_music == {
        "kids_room": mpd_module.own_stream_url_for("kids_room"),
    }


async def test_a_paused_or_stopped_player_gets_nothing(house) -> None:
    await _playing(house, "garage")
    await house.clients["garage"].pause()  # maybe a person's pause
    await _playing(house, "dining_room")
    await music_groups.share(_app(), "dining_room", ["jordan_office"])
    await house.clients["dining_room"].pause()  # the group, paused
    app = _restarted()
    assert await restore_resume_intents(app) == {}
    assert app.state.resumable_music == {}


async def test_a_player_that_does_not_answer_is_left_out(house, monkeypatch) -> None:
    await _playing(house, "garage")
    await _playing(house, "dining_room")

    async def _gone():
        raise ConnectionRefusedError("not up yet")

    monkeypatch.setattr(house.clients["garage"], "state", _gone)
    app = _restarted()
    await restore_resume_intents(app)
    assert set(app.state.resumable_music) == {"dining_room"}


async def test_a_player_that_hangs_is_left_out_in_time(house, monkeypatch) -> None:
    import domovoi.room_music as room_music

    await _playing(house, "dining_room")
    monkeypatch.setattr(room_music, "RESTORE_PROBE_TIMEOUT_SEC", 0.05)

    async def _hang():
        await asyncio.sleep(30)

    monkeypatch.setattr(house.clients["garage"], "state", _hang)
    app = _restarted()
    await asyncio.wait_for(restore_resume_intents(app), 5)
    assert set(app.state.resumable_music) == {"dining_room"}


async def test_an_entry_set_since_startup_is_kept(house) -> None:
    await _playing(house, "garage")
    app = _restarted()
    app.state.resumable_music["garage"] = "http://192.168.1.20:9999"
    await restore_resume_intents(app)
    assert app.state.resumable_music["garage"] == "http://192.168.1.20:9999"


@requires_db
async def test_a_member_read_back_from_the_database_gets_the_hosts_stream(house, db_session) -> None:
    """The live case: the shared queue comes back from the database (memory
    wiped), and so does the member's resume intent."""
    music_groups.PERSIST = True
    await _playing(house, "garage")
    await music_groups.share(_app(), "garage", ["dining_room"])
    await drain()
    music_groups.reset()  # the restart: memory gone, the database kept
    await music_groups.load()
    assert music_groups.host_of("dining_room") == "garage"
    app = _restarted()
    await restore_resume_intents(app)
    assert app.state.resumable_music["dining_room"] == mpd_module.own_stream_url_for("garage")


# ─── through the real app's startup ───────────────────────────────────────


def test_the_first_turn_after_a_restart_brings_the_music_back(monkeypatch) -> None:
    """The app's startup restores the intent; a "turn it up"-like turn with
    no music of its own then sends the room its stream again."""
    from fastapi.testclient import TestClient

    from domovoi.clients.mpd import MPDStubClient
    from domovoi.main import app
    from domovoi.models import Response
    from domovoi.streaming import StreamSession
    from domovoi.tests.test_streaming import _FakeTTS, _FakeWhisper, _hello, _patch_pipeline

    async def _accept(self, ctrl):  # as test_streaming: a paired satellite
        self.token_authenticated = True
        return True

    monkeypatch.setattr(StreamSession, "_validate_pairing", _accept)
    import domovoi.room_music as room_music

    monkeypatch.setattr(room_music, "RESTORE_AT_STARTUP_IN_STUBS", True)
    stub = MPDStubClient()
    asyncio.run(stub.prepare_files(["song.mp3"]))
    asyncio.run(stub.resume())
    monkeypatch.setattr(mpd_module, "_clients", {"kitchen": stub})
    monkeypatch.setattr(mpd_module, "_room_ports", {"kitchen": (6650, 8050)})
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper("turn it up"),
        tts=_FakeTTS(),
        response=Response(text="Volume set to 8.", matched_handler="volume",
                          matched_path="fast", online=True),
    )
    with TestClient(app) as client:
        assert app.state.resumable_music.get("kitchen", "").endswith(":8050")
        with client.websocket_connect("/v1/stream/kitchen") as ws:
            _hello(ws)
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 1024)
            ws.send_text(json.dumps({"type": "utterance_end"}))
            ws.receive_json()  # transcript
            ws.receive_json()  # response_start
            ws.receive_bytes()  # tts audio
            assert ws.receive_json()["type"] == "response_end"
            frame = ws.receive_json()
            assert frame["type"] == "music_start"
            assert frame["stream_url"].endswith(":8050")


# ─── re-joining: the way out of a desync ──────────────────────────────────


async def test_rejoining_a_shared_queue_sends_the_stream_again(house) -> None:
    await _playing(house, "garage")
    ctx = _ctx()
    resp = await _say("join the garage", ctx, FakeDB())
    assert resp.text == "Playing the music from the garage here too."
    url = mpd_module.own_stream_url_for("garage")
    assert _sat(ctx, "dining_room").started == [url]
    # The satellite went silent while the server still counts it in (the
    # restart above, before this fix): the intent is gone.
    ctx.app.state.resumable_music.clear()
    resp = await _say("join the garage", ctx, FakeDB())
    assert resp.text == "Playing the music from the garage here."
    assert _sat(ctx, "dining_room").started == [url, url]
    assert ctx.app.state.resumable_music["dining_room"] == url
    assert music_groups.share_of("garage").members == ["dining_room"]


async def test_play_this_in_a_member_room_too_sends_it_again(house) -> None:
    await _playing(house, "dining_room")
    ctx = _ctx()
    await _say("play this in the garage too", ctx, FakeDB())
    url = mpd_module.own_stream_url_for("dining_room")
    assert _sat(ctx, "garage").started == [url]
    ctx.app.state.resumable_music.pop("garage", None)
    resp = await _say("play this in the garage too", ctx, FakeDB())
    assert resp.text == "Playing this in the garage too."
    assert _sat(ctx, "garage").started == [url, url]
    assert ctx.app.state.resumable_music["garage"] == url
    assert music_groups.share_of("dining_room").members == ["garage"]


@pytest.mark.parametrize("said", ["join the garage", "play the garage's music in here"])
async def test_rejoin_phrasings_resync(house, said) -> None:
    await _playing(house, "garage")
    ctx = _ctx()
    await music_groups.share(ctx.app, "garage", ["dining_room"])
    await drain()
    resp = await _say(said, ctx, FakeDB())
    assert resp.text == "Playing the music from the garage here."
    assert len(_sat(ctx, "dining_room").started) == 2
