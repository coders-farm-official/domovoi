"""Announcements and a room's music, across features (DB-free).

Since timers and reminders are announced in every room
(domovoi/timer_delivery.py), an announcement in a room playing music is a
daily event, and three things around it were wrong after the 2026-09-30
merge. Pinned here with real StreamSessions and a real TimerDelivery on
fake sockets (the review's repros, turned around):

* two timers due together in a room with music restarted it in between:
  the second one waits out the first one's playback and a settle, and the
  first one's music_start went out in that gap. Now the music comes back
  once, after the last one;
* a follow-up capture nobody answered (no utterance_end ever comes) left
  the room "capturing" for good, so every later announcement stopped its
  music and never restarted it;
* music a person paused was un-paused by any announcement's restart (the
  music_ready handshake always resumed MPD) — every room's, since a
  kitchen timer is announced in the office too. The player still comes
  back; MPD stays paused until a person resumes it;
* a drop-in ring prompt took no part in the room's announcement lock, so a
  timer due while it synthesized interleaved its frames with the prompt's.
"""

from __future__ import annotations

import asyncio

import pytest

from domovoi import streaming
from domovoi.clients import mpd as mpd_module
from domovoi.config import settings
from domovoi.handlers import music as music_handler_module
from domovoi.handlers.music import MusicHandler
from domovoi.models import Context
from domovoi.music_pause import paused_by_person
from domovoi.streaming import StreamSession, consume_music_ready, send_music_start
from domovoi.tests.test_timer_delivery import (  # noqa: F401 - stub_tts is a fixture
    BASE,
    Ledger,
    _real_app,
    _until,
    _WS,
    due,
    stub_tts,
)
from domovoi.timer_delivery import TimerDelivery

URL = "http://192.0.2.10:8051"


class FakeMPD:
    def __init__(self, state: str = "play") -> None:
        self.state_ = state
        self.resumes = 0

    async def pause(self) -> None:
        self.state_ = "pause"

    async def resume(self) -> None:
        self.resumes += 1
        if self.state_ == "pause":
            self.state_ = "play"

    async def stop(self) -> None:
        self.state_ = "stop"


@pytest.fixture
def mpd(monkeypatch) -> FakeMPD:
    player = FakeMPD()

    async def serving(room_id, stream_url=None, *, timeout=None):
        return True

    monkeypatch.setattr(mpd_module, "get_mpd_client_for", lambda room: player)
    monkeypatch.setattr(music_handler_module, "get_mpd_client_for", lambda room: player)
    monkeypatch.setattr(mpd_module, "ensure_stream_serving", serving)
    monkeypatch.setattr(settings, "music_prepare_fallback_sec", 0.05)
    return player


def _app():
    app = _real_app()
    app.state.pending_music_start = {}
    return app


def _room(app, room: str) -> tuple[StreamSession, _WS]:
    ws = _WS(app)
    sess = StreamSession(ws, room)  # type: ignore[arg-type]
    sess.token_authenticated = True          # a paired satellite
    app.state.active_sessions[room] = sess
    return sess, ws


def _types(ws: _WS) -> list[str]:
    return [f.get("type") for kind, f in ws.frames if kind == "text"]


def _delivery(app, ledger: Ledger) -> TimerDelivery:
    d = TimerDelivery(app, lambda: ledger, poll_sec=0.01, wall=lambda: BASE)
    app.state.timer_delivery = d
    d.set_accepting()
    return d


async def _settle_music(app) -> None:
    await asyncio.gather(*list(streaming._ANNOUNCE_MUSIC_RESTARTS), return_exceptions=True)
    await asyncio.sleep(0.15)                # the handshake's fallback (0.05 s)
    for entry in app.state.pending_music_start.values():
        entry["task"].cancel()


async def test_two_timers_due_together_restart_the_music_once_after_both(stub_tts, mpd) -> None:
    app = _app()
    _sess, ws = _room(app, "kitchen")
    app.state.resumable_music["kitchen"] = URL
    ledger = Ledger()
    d = _delivery(app, ledger)
    ledger.due.extend([due(1, "kitchen", label="pasta"), due(2, "kitchen", label="rice")])
    await d.tick()
    await _until(lambda: all(
        ledger._rows[f].get("kitchen", {}).get("outcome") == "spoken" for f in ledger._rows
    ), timeout=10)
    await _settle_music(app)
    await d.shutdown()
    assert ws.spoken() == ["Your pasta timer is done.", "Your rice timer is done."]
    assert _types(ws) == [
        "response_start", "response_end",
        "response_start", "response_end",
        "music_start",
    ]


async def test_a_queued_timer_that_is_acknowledged_lets_the_music_come_back(
    stub_tts, mpd,
) -> None:
    """The second fire never plays (someone said "stop the timer" in time,
    so its claim finds it done): the first one's restart, which waited for
    it, brings the music back."""
    app = _app()
    _sess, ws = _room(app, "kitchen")
    app.state.resumable_music["kitchen"] = URL
    ledger = Ledger()
    d = _delivery(app, ledger)
    ledger.due.extend([due(1, "kitchen", label="pasta"), due(2, "kitchen", label="rice")])
    await d.tick()
    await _until(lambda: ledger._rows[-1].get("kitchen", {}).get("outcome") == "spoken")
    assert d.announcing_to("kitchen")          # the rice timer is still to come
    await ledger.finish(-2, "kitchen", "cancelled", "acknowledged", None)
    await _until(lambda: not d.announcing_to("kitchen"))
    await _settle_music(app)
    await d.shutdown()
    assert ws.spoken() == ["Your pasta timer is done."]
    assert _types(ws) == ["response_start", "response_end", "music_start"]


async def test_music_comes_back_after_a_timer_once_a_followup_went_unanswered(
    stub_tts, mpd,
) -> None:
    """The review's repro: a follow-up capture the person never answered
    (utterance_start, a frame, then nothing — the satellite sends no
    utterance_end), music cast from the dashboard afterwards, then a timer
    from another room. The satellite stops mpg123 for the announcement;
    the music has to come back."""
    app = _app()
    office, ws = _room(app, "office")
    await office._on_control({"type": "utterance_start", "trigger": "followup", "utt": 7})
    await office._on_audio(b"\x00" * 960)
    assert office.utterance_active
    office._last_audio_at -= 30                 # 30 s later: it went quiet
    app.state.resumable_music["office"] = URL   # cast from the dashboard
    assert office.announce_block() is None

    ledger = Ledger()
    d = _delivery(app, ledger)
    ledger.due.append(due(1, "kitchen"))
    await d.tick()
    await _until(lambda: ledger._rows[-1].get("office", {}).get("outcome") == "spoken")
    await _settle_music(app)
    await d.shutdown()
    assert _types(ws) == ["response_start", "response_end", "music_start"]


async def test_a_kitchen_timer_does_not_unpause_music_a_person_paused(stub_tts, mpd) -> None:
    """Paused on the dashboard (POST /v1/admin/music/pause/office →
    MusicHandler's pause): a kitchen timer is announced in the office, the
    satellite's player comes back on the paused stream, and MPD stays
    paused — until a person resumes it."""
    app = _app()
    _room(app, "kitchen")
    _office, ws = _room(app, "office")
    app.state.resumable_music["office"] = URL
    handler = MusicHandler()
    ctx = Context(room_id="office", app=app)
    assert (await handler._simple_ack("pause", ctx)).text == "Paused."
    assert mpd.state_ == "pause" and paused_by_person(app, "office")

    ledger = Ledger()
    d = _delivery(app, ledger)
    ledger.due.append(due(1, "kitchen"))
    await d.tick()
    await _until(lambda: ledger._rows[-1].get("office", {}).get("outcome") == "spoken")
    await _settle_music(app)
    await d.shutdown()
    assert _types(ws) == ["response_start", "response_end", "music_start"]
    assert mpd.state_ == "pause" and mpd.resumes == 0

    # The person resumes: the pause is forgotten, and it plays.
    assert (await handler._simple_ack("resume", ctx)).text == "Resuming."
    assert mpd.state_ == "play" and not paused_by_person(app, "office")


async def test_the_handshake_keeps_a_persons_pause_and_forgets_it_on_a_new_start(
    mpd, monkeypatch,
) -> None:
    """music_ready after a music_start (a turn's auto-resume, a restart)
    leaves a person-paused room paused; starting something new clears the
    pause (the admin cast's `_admin_dispatch_music`), and the next
    handshake resumes as always."""
    from domovoi import main as main_module
    from domovoi.models import Response

    app = _app()
    office, ws = _room(app, "office")
    mpd.state_ = "pause"
    app.state.music_paused_by_person = {"office"}
    assert await send_music_start(app, office, "office", URL)
    await consume_music_ready(app, "office")
    assert mpd.resumes == 0 and mpd.state_ == "pause"

    monkeypatch.setattr(main_module.app, "state", app.state)
    await main_module._admin_dispatch_music(
        Response(text="ok", music_action="start", music_stream_url=URL), "office",
    )
    assert not paused_by_person(app, "office")
    await consume_music_ready(app, "office")
    assert mpd.resumes == 1 and mpd.state_ == "play"
    assert _types(ws) == ["music_start", "music_start"]


async def test_a_drop_in_prompt_holds_the_room_like_an_announcement(monkeypatch) -> None:
    """A timer due while the ring prompt synthesizes waits for it: the room
    reads as 'announcing', and the announcement's frames come after the
    prompt's response_end, never inside it."""

    class _SlowTTS:
        async def synthesize(self, text, *, engine=None, voice=None):
            await asyncio.sleep(0.2)
            from domovoi.tests.test_timer_delivery import _wav
            return _wav(b"\x01\x00" * 800)

    async def any_voice(_name):
        return (None, None)

    monkeypatch.setattr(streaming, "get_tts_client", lambda: _SlowTTS())
    monkeypatch.setattr(streaming, "resolve_voice", any_voice)
    app = _app()
    office, ws = _room(app, "office")
    prompt = asyncio.create_task(office.prompt_dropin("The kitchen wants to drop in. Is that okay?"))
    await asyncio.sleep(0.05)                    # the prompt's first sentence is synthesizing
    assert office.announce_block() == ("announcing", True)
    announcement = asyncio.create_task(office.announce("Reminder from the garage: the oven."))
    await asyncio.gather(prompt, announcement)
    frames = [f for kind, f in ws.frames if kind == "text"]
    assert [f["type"] for f in frames] == [
        "response_start", "response_end", "response_start", "response_end",
    ]
    assert frames[0]["matched_path"] == "dropin_invite"
    assert frames[1]["expect_followup"] is True
    assert frames[2]["matched_path"] == "intercom_broadcast"


async def test_a_drop_in_ring_cancels_a_music_restart_still_pending(stub_tts, mpd, monkeypatch) -> None:
    """The ring (and the call) owns the room's music from its music_stop: a
    restart still waiting on the stream from an earlier announcement sends
    nothing — the decline's turn or the call's end brings it back."""
    slow = asyncio.Event()

    async def slow_stream(room_id, stream_url=None, *, timeout=None):
        await slow.wait()
        return True

    monkeypatch.setattr(mpd_module, "ensure_stream_serving", slow_stream)
    app = _app()
    office, ws = _room(app, "office")
    app.state.resumable_music["office"] = URL
    await office.announce("Reminder from the garage: the oven.")
    await asyncio.sleep(0.05)                    # its restart is readying the stream
    await office._suppress_music_for(office)
    slow.set()
    await _settle_music(app)
    assert _types(ws) == ["response_start", "response_end", "music_stop"]
