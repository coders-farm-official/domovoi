"""The core never sends music_start into a room that is listening.

Dining room, 2026-09-30 ~23:56-00:00Z: with music playing, captures ran to
the 30 s `max_record_seconds` cap, their first speech pause 20-29 s in, and
the lyrics were routed and answered. The satellite starts its player the
moment its speaker is free, and a satellite older than the music hold
(satellite/tests/test_music_capture_hold.py) does that whatever else it is
doing — into an open capture, which then hears one long sentence. So the
core holds a music_start the room cannot take yet and sends it once the
room is free (`StreamSession.music_block`, `hold_music_start`).

Pinned here (no database: real StreamSessions on a recording socket;
Whisper, TTS, route() and the stream check replaced):

* A reply that asks a question (`expect_followup`) sends no music_start
  while its follow-up window is open — the Q&A online-check offer, a
  question the voice-profile hook appends after a streamed answer, a
  confirmation, the chat-mode entry. And the music comes back once that
  window has closed with no turn to bring it: the satellite's follow-up
  capture timed out (it sends no utterance_end — its audio stops), or no
  follow-up capture came at all (a bound). It used to stay off until the
  next wake word; an answer instead makes its own music decision, once.
* A reply that asks nothing sends music_start in the same breath as its
  response_end (unchanged).
* A dashboard/app cast and a drop-in's restore go out at once to a free
  room, and are held while a capture is open, a turn is being answered, a
  question's follow-up window is open, an announcement is on its way or
  wake-word clips are being recorded — then sent. A held start that finds
  the room busy again once its stream is ready holds on.
* The restart after an announcement steps aside for an open capture and
  is HELD, not dropped: a follow-up nobody answered gets its music back.
* A held start is superseded by anything that decides the room's music
  meanwhile (a turn's music_start or music_stop), and dropped when the room
  is stopped or its socket goes away.
* Unchanged: a capture that begins while a resume waits on the stream
  cancels it; a start already sent is never recalled; an early commit's
  end_capture precedes the turn's music_start.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import types
import wave
from contextlib import asynccontextmanager

import pytest

from domovoi import streaming
from domovoi.clients import mpd as mpd_module
from domovoi.models import Response
from domovoi.spoken_answer import SpokenAnswer, StreamedQA

ROOM = "dining-room"
URL = "http://192.0.2.10:8055"
FRAME = b"\x00\x00" * 480          # one 30 ms frame of 16 kHz int16 PCM


def _wav(seconds: float, rate: int = 22_050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


class _Socket:
    """What the session sends its satellite, with when it was sent."""

    def __init__(self, app) -> None:
        self.app = app
        self.sent: list[tuple[float, str, object]] = []

    async def send_text(self, data: str) -> None:
        self.sent.append((time.monotonic(), "text", json.loads(data)))

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append((time.monotonic(), "bytes", len(data)))


class Room:
    def __init__(self) -> None:
        self.app = types.SimpleNamespace(state=types.SimpleNamespace(
            satellite_voice={}, wifi_status={}, satellite_volume={},
            satellite_config={}, greeting_phrases=[], greeting_clips={},
            probe=types.SimpleNamespace(online=True),
            active_sessions={}, resumable_music={ROOM: URL},
            current_playlist={}, pending_music_start={},
        ))
        self.ws = _Socket(self.app)
        self.sess = streaming.StreamSession(self.ws, ROOM)  # type: ignore[arg-type]
        self.app.state.active_sessions[ROOM] = self.sess
        self.stream_wait = 0.0        # how long the stream check takes
        self.tts_seconds = 0.2        # audio per synthesized sentence
        self.reply: object = None     # what route() returns (or a factory)
        self.hook = None              # stands in for the voice-profile hooks
        self.mpd_resumed: list[str] = []

    # ── what the satellite got ──
    def texts(self) -> list[dict]:
        return [f for _t, k, f in self.ws.sent if k == "text"]  # type: ignore[misc]

    def kinds(self) -> list[str]:
        return [f["type"] for f in self.texts()]

    def starts(self) -> list[dict]:
        return [f for f in self.texts() if f["type"] == "music_start"]

    def sent_at(self, kind: str) -> float:
        return next(t for t, k, f in self.ws.sent if k == "text" and f["type"] == kind)  # type: ignore[index]

    # ── the satellite's side of a capture ──
    async def start_capture(self, trigger: str, utt: int) -> None:
        await self.sess._on_control({"type": "utterance_start", "trigger": trigger, "utt": utt})

    async def frames(self, n: int) -> None:
        for _ in range(n):
            await self.sess._on_audio(FRAME)

    async def end_capture(self, utt: int) -> None:
        await self.sess._on_control({"type": "utterance_end", "utt": utt})

    def capture_goes_quiet(self) -> None:
        """The capture's audio stopped arriving long enough ago: a follow-up
        the satellite gave up on (it sends no utterance_end)."""
        self.sess._last_audio_at -= streaming.ANNOUNCE_CAPTURE_FRESH_SEC + 0.1

    async def turn(self, trigger: str = "wake_word", utt: int = 1) -> None:
        """A whole capture, then wait for its reply (and its music) to go out."""
        await self.start_capture(trigger, utt)
        await self.frames(20)
        await self.end_capture(utt)
        task = self.sess._response_task
        assert task is not None
        await task


@asynccontextmanager
async def _no_db():
    yield None


@pytest.fixture
def room(monkeypatch):
    r = Room()

    class _Whisper:
        async def transcribe(self, pcm):
            return "what does the fox say"

        async def transcribe_wav_bytes(self, wav):
            return "what does the fox say"

    class _TTS:
        async def synthesize(self, text, engine=None, voice=None):
            return _wav(r.tts_seconds)

    async def _route(intent, ctx, session):
        reply = r.reply
        return reply() if callable(reply) else reply

    async def _identify(pcm, embedding=None, **_kw):
        return None

    async def _any_voice(name):
        return (None, None)

    async def _stream_check(room_id, stream_url=None, *, timeout=None):
        await asyncio.sleep(r.stream_wait)
        return True

    async def _resume(room_id):
        r.mpd_resumed.append(room_id)

    async def _hooks(self, s, *, response, **kw):
        if r.hook is not None:
            r.hook(response)

    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper())
    monkeypatch.setattr(streaming, "get_tts_client", lambda: _TTS())
    monkeypatch.setattr(streaming, "route", _route)
    monkeypatch.setattr(streaming, "session_scope", _no_db)
    monkeypatch.setattr(streaming, "resolve_voice", _any_voice)
    monkeypatch.setattr(streaming, "_resume_mpd_for_room", _resume)
    monkeypatch.setattr(streaming.StreamSession, "_voice_profile_hooks", _hooks)
    monkeypatch.setattr("domovoi.voice_identifier.identify", _identify)
    monkeypatch.setattr(mpd_module, "ensure_stream_serving", _stream_check)
    monkeypatch.setattr(streaming, "MUSIC_HOLD_POLL_SEC", 0.01, raising=False)
    yield r
    for entry in r.app.state.pending_music_start.values():
        entry["task"].cancel()
    for t in [*streaming._ANNOUNCE_MUSIC_RESTARTS, *getattr(streaming, "_MUSIC_HOLDS", ())]:
        t.cancel()


def _streamed(deltas: list[str], *, offer: str = ""):
    """A Q&A answer spoken as it is written (Response.qa_stream); its finish
    appends ``offer`` and asks for a follow-up when there is one, as
    router._finish_qa does for the online-check offer."""

    def factory() -> Response:
        async def stream():
            for d in deltas:
                await asyncio.sleep(0)
                yield d

        spoken = SpokenAnswer(stream, transcript="what does the fox say")

        async def finish(s):
            text = spoken.answer + (f" {offer}" if offer else "")
            return Response(text=text, matched_path="qa", online=True,
                            expect_followup=bool(offer)), offer

        return Response(text="", matched_path="qa", online=True,
                        qa_stream=StreamedQA(answer=spoken, finish=finish))

    return factory


OFFER = _streamed(["Nobody knows for sure.", " It might be a bark."],
                  offer="Want me to check that online?")


async def _settle(seconds: float = 0.05) -> None:
    await asyncio.sleep(seconds)


async def _until(cond, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "condition never became true"
        await asyncio.sleep(0.01)


# ─── A reply that asks a question ────────────────────────────────────────


async def test_a_qa_offer_holds_the_music_until_its_unanswered_followup_ends(room) -> None:
    """The live failure's other half: an older satellite spawns mpg123 the
    moment a music_start lands with its speaker free — and its follow-up
    capture opens in that same moment. Nothing goes out while the capture
    is live; once it times out (its audio stops; no utterance_end comes),
    the held start goes out, so the music comes back without the next
    wake word."""
    room.reply = OFFER
    await room.turn()
    assert room.texts()[-1] == {"type": "response_end", "interrupted": False,
                                "expect_followup": True}
    # The satellite is still playing the offer.
    await _settle(0.1)
    assert room.sess.music_block() == "followup"
    assert room.starts() == []
    assert room.mpd_resumed == []

    # Then it listens without a wake word: its frames stream from the first
    # one, and nobody answers.
    await room.start_capture("followup", 2)
    await room.frames(30)
    assert room.sess._capturing(time.monotonic())
    assert room.sess.music_block() == "capturing"
    await _settle(0.1)
    assert room.starts() == []

    # It gives up: no utterance_end, the audio just stops.
    room.capture_goes_quiet()
    assert room.sess.utterance_active is True
    await _until(lambda: room.starts() != [])
    assert room.starts() == [{"type": "music_start", "stream_url": URL}]
    assert room.app.state.pending_music_start[ROOM]["url"] == URL
    assert room.sess._music_hold_task is None


async def test_an_answered_question_decides_the_music_once(room) -> None:
    """Someone answers: their turn makes its own music decision (here the
    auto-resume), and the start held for the question is superseded — one
    music_start, after the answer's reply."""
    room.reply = OFFER
    await room.turn()
    room.reply = Response(text="Checking.", matched_handler="qa", matched_path="qa",
                          online=True)
    await room.turn(trigger="followup", utt=2)
    await _settle(0.1)
    assert room.kinds()[-2:] == ["response_end", "music_start"]
    assert len(room.starts()) == 1
    assert room.sess._music_hold_task is None


async def test_a_question_with_no_followup_capture_gets_its_music_back_after_the_window(
    room, monkeypatch,
) -> None:
    """No follow-up capture ever comes (the satellite was busy, or does
    not listen for one): the window is bounded — the estimated end of the
    question's playback plus ANNOUNCE_FOLLOWUP_HOLD_SEC — then the music
    goes out."""
    monkeypatch.setattr(streaming, "ANNOUNCE_FOLLOWUP_HOLD_SEC", 0.3)
    room.reply = OFFER
    await room.turn()
    assert room.sess.music_block() == "followup"
    assert room.starts() == []
    await _until(lambda: room.starts() != [])
    assert time.monotonic() >= room.sess._followup_hold_until


async def test_a_question_the_voice_profile_hook_appends_to_a_streamed_answer_holds_the_music(
    room,
) -> None:
    """The hooks run after a streamed answer has been said (they may append
    "By the way, was that Sam?"); the music decision reads the response they
    changed."""
    room.reply = _streamed(["It is a red fox.", " They yip and scream."])

    def ask(response):
        response.text += " By the way, was that Sam?"
        response.expect_followup = True

    room.hook = ask
    await room.turn()
    assert room.texts()[-1]["expect_followup"] is True
    assert room.starts() == []
    assert room.sess._music_hold_task is not None


@pytest.mark.parametrize(
    "reply",
    [
        Response(text="What number should I add?", matched_handler="calculator",
                 matched_path="fast", online=True, expect_followup=True),
        Response(text="Should I add the album too?", matched_handler="playlist",
                 matched_path="fast", online=True, expect_followup=True,
                 music_action="start", music_stream_url=URL),
    ],
    ids=["confirmation", "confirmation-with-start"],
)
async def test_whole_replies_that_ask_hold_the_music_through_the_followup(room, reply) -> None:
    room.reply = reply
    await room.turn()
    assert room.texts()[-1]["type"] == "response_end"
    assert room.texts()[-1]["expect_followup"] is True
    assert room.starts() == []
    if reply.music_action == "start":
        # MPD is unpaused at once, as before (no music_ready is coming yet).
        assert room.mpd_resumed == [ROOM]
    await room.start_capture("followup", 2)
    await room.frames(10)
    await _settle(0.05)
    assert room.starts() == []
    room.capture_goes_quiet()
    await _until(lambda: room.starts() != [])
    assert room.starts() == [{"type": "music_start", "stream_url": URL}]


async def test_the_chat_entry_holds_the_music_for_the_whole_chat(room) -> None:
    """Chat mode is an open mic: nothing goes out while it lasts (its turns
    make no music decision), and the music comes back when it ends."""
    room.reply = Response(text="Sure, let's chat.", matched_handler="chat_mode",
                          matched_path="fast", online=True, expect_followup=True)
    await room.turn()
    room.sess.conversational_mode = True        # what the chat_start sets
    room.sess._followup_hold_until = 0.0
    await _settle(0.1)
    assert room.sess.music_block() == "chat"
    assert room.starts() == []
    room.sess.conversational_mode = False       # chat_end
    await _until(lambda: room.starts() != [])


# ─── A reply that asks nothing ───────────────────────────────────────────


async def test_a_streamed_answer_without_an_offer_resumes_music_while_it_is_still_playing(
    room,
) -> None:
    room.tts_seconds = 2.0
    room.reply = _streamed(["It is a red fox.", " They yip and scream at night."])
    await room.turn()
    kinds = room.kinds()
    assert kinds[-2:] == ["response_end", "music_start"]
    assert room.texts()[-2]["expect_followup"] is False
    # Sent in the same breath as response_end, with reply audio still to
    # play on the satellite: it waits for its speaker, and a current one
    # for its turn to be over.
    sent = room.sent_at("music_start")
    assert sent - room.sent_at("response_end") < 0.25
    assert room.sess._playout_until - sent > 3.0
    assert ROOM in room.app.state.pending_music_start


async def test_a_capture_that_starts_while_the_resume_waits_on_the_stream_cancels_it(
    room,
) -> None:
    room.stream_wait = 0.5
    room.reply = Response(text="It's ten past eight.", matched_handler="clock",
                          matched_path="fast", online=True)
    await room.start_capture("wake_word", 1)
    await room.frames(20)
    await room.end_capture(1)
    task = room.sess._response_task
    await _until(lambda: "response_end" in room.kinds())
    # The resume is waiting on the stream check; the next capture opens.
    await room.start_capture("wake_word", 2)
    await asyncio.gather(task, return_exceptions=True)
    await _settle(0.6)
    assert room.starts() == []


async def test_a_resume_already_sent_is_never_recalled_by_the_next_capture(room) -> None:
    """The crossing window: a music_start that left before the satellite's
    next utterance_start reached the core stays out — only the satellite
    (its turn hold and stop count) keeps it out of that capture."""
    room.reply = Response(text="It's ten past eight.", matched_handler="clock",
                          matched_path="fast", online=True)
    await room.turn()
    assert room.kinds()[-1] == "music_start"
    before = len(room.ws.sent)
    await room.start_capture("wake_word", 2)
    await room.frames(10)
    await _settle()
    assert [f for _t, k, f in room.ws.sent[before:] if k == "text"] == []


async def test_an_early_commit_ends_the_capture_before_the_turns_music_start(room) -> None:
    room.reply = Response(text="It's ten past eight.", matched_handler="clock",
                          matched_path="fast", online=True)
    committed = streaming._Committed(serial=1, utt=7, frames=20, tier="A")
    await room.sess._process_utterance(FRAME * 20, trigger="wake_word", early_commit=committed)
    kinds = room.kinds()
    assert kinds[0] == "end_capture"
    assert kinds.index("end_capture") < kinds.index("response_end") < kinds.index("music_start")


# ─── The restart after an announcement ───────────────────────────────────


async def test_an_announcement_restart_waits_out_an_open_followup_capture(room) -> None:
    await room.start_capture("followup", 1)
    await room.frames(10)
    room.sess._restart_music_after_announce(URL)
    await asyncio.gather(*list(streaming._ANNOUNCE_MUSIC_RESTARTS), return_exceptions=True)
    assert room.starts() == []
    # Held, not dropped: once the capture has gone quiet (nobody answered),
    # the music comes back.
    room.capture_goes_quiet()
    await _until(lambda: room.starts() != [])
    assert room.starts() == [{"type": "music_start", "stream_url": URL}]


# ─── Casts and a drop-in's restore ───────────────────────────────────────


@pytest.fixture
def admin_app(room, monkeypatch):
    from domovoi import main as main_module

    for name in ("active_sessions", "resumable_music", "current_playlist",
                 "pending_music_start"):
        monkeypatch.setattr(main_module.app.state, name,
                            getattr(room.app.state, name), raising=False)
    return main_module


CAST = Response(text="ok", matched_handler="music", music_action="start",
                music_stream_url=URL)


async def test_a_dashboard_cast_to_a_free_room_goes_out_at_once(room, admin_app) -> None:
    await admin_app._admin_dispatch_music(CAST, ROOM)
    assert room.starts() == [{"type": "music_start", "stream_url": URL}]
    assert room.sess._music_hold_task is None


async def test_a_dashboard_cast_waits_for_an_open_followup_capture(room, admin_app) -> None:
    await room.start_capture("followup", 1)
    await room.frames(10)
    assert room.sess._capturing(time.monotonic())
    await admin_app._admin_dispatch_music(CAST, ROOM)
    assert room.starts() == []
    room.capture_goes_quiet()
    await _until(lambda: room.starts() != [])
    assert room.starts() == [{"type": "music_start", "stream_url": URL}]


async def test_a_dashboard_cast_during_a_question_waits_for_its_followup(
    room, admin_app, monkeypatch,
) -> None:
    gate = asyncio.Event()
    room.reply = OFFER
    real_tts = streaming.get_tts_client()

    class _HeldTTS:
        async def synthesize(self, text, engine=None, voice=None):
            if text.startswith("Want me"):
                await gate.wait()       # the offer is still being said
            return await real_tts.synthesize(text, engine=engine, voice=voice)

    monkeypatch.setattr(streaming, "get_tts_client", lambda: _HeldTTS())
    await room.start_capture("wake_word", 1)
    await room.frames(20)
    await room.end_capture(1)
    task = room.sess._response_task
    assert task is not None
    await _until(lambda: "response_start" in room.kinds())
    assert not task.done()
    # Mid-reply, the question not yet asked: someone presses play.
    await admin_app._admin_dispatch_music(CAST, ROOM)
    assert room.sess.music_block() == "responding"
    gate.set()
    await task
    assert room.texts()[-1]["type"] == "response_end"
    assert room.texts()[-1]["expect_followup"] is True
    await _settle(0.1)
    # An older satellite would hold this start until its speaker is idle —
    # the moment it opens the follow-up capture — and then spawn mpg123.
    assert room.starts() == []
    await room.start_capture("followup", 2)
    await room.frames(10)
    room.capture_goes_quiet()
    await _until(lambda: room.starts() != [])
    assert len(room.starts()) == 1


async def test_a_dropin_restore_waits_for_an_open_capture(room) -> None:
    await room.start_capture("followup", 1)
    await room.frames(10)
    await room.sess._restore_music_for(room.sess)
    assert room.starts() == []
    room.capture_goes_quiet()
    await _until(lambda: room.starts() != [])


@pytest.mark.parametrize("coming", ["synthesizing", "queued", "timer"])
async def test_a_cast_waits_for_an_announcement_on_its_way(room, admin_app, coming) -> None:
    """An announcement is on its way to the room: one still synthesizing its
    first sentence (`_announce_callers`), one queued on the room's lock (or
    a drop-in ring holding it), or a timer the delivery has yet to announce
    here (`TimerDelivery.announcing_to`). A music_start now would spawn the
    player for the moment before the announcement's response_start stops it
    again, and the announcement's own end restarts the music anyway. So a
    cast is held until nothing more is coming, then sent."""
    sess = room.sess
    if coming == "synthesizing":
        sess._announce_callers += 1

        def done() -> None:
            sess._announce_callers -= 1
    elif coming == "queued":
        await sess._announce_lock.acquire()
        done = sess._announce_lock.release
    else:
        due = {ROOM}
        room.app.state.timer_delivery = types.SimpleNamespace(
            announcing_to=lambda room_id: room_id in due,
        )
        done = due.clear
    await admin_app._admin_dispatch_music(CAST, ROOM)
    await _settle(0.1)
    assert room.starts() == []
    assert sess.music_block() == "announcing"
    assert sess._music_hold_task is not None
    done()
    await _until(lambda: room.starts() != [])
    assert room.starts() == [{"type": "music_start", "stream_url": URL}]


async def test_a_cast_made_while_an_announcement_is_synthesized_plays_after_it(
    room, admin_app, monkeypatch,
) -> None:
    """End to end through `announce`: play is pressed while the timer's
    announcement is still being synthesized. Nothing reaches the satellite
    before the announcement has been sent (its response_end); the music
    comes back after it."""
    gate = asyncio.Event()
    real_tts = streaming.get_tts_client()

    class _SlowTTS:
        async def synthesize(self, text, engine=None, voice=None):
            await gate.wait()
            return await real_tts.synthesize(text, engine=engine, voice=voice)

    monkeypatch.setattr(streaming, "get_tts_client", lambda: _SlowTTS())
    said = asyncio.create_task(room.sess.announce("The pasta timer is done."))
    await _until(lambda: room.sess._announce_lock.locked())
    await admin_app._admin_dispatch_music(CAST, ROOM)
    await _settle(0.1)
    assert room.kinds() == []
    gate.set()
    await said
    await _until(lambda: room.starts() != [])
    ended = room.sent_at("response_end")
    assert room.kinds()[:2] == ["response_start", "response_end"]
    assert all(
        t >= ended for t, k, f in room.ws.sent
        if k == "text" and f["type"] == "music_start"  # type: ignore[index]
    )


async def test_a_cast_waits_while_the_room_records_wake_word_clips(room, admin_app) -> None:
    """Recording wake-word clips (dashboard "Record on <room>"): every
    capture is saved as training audio for the new wake word, and music
    under it would be in every clip. A cast is held until the recording
    stops."""
    await room.sess.start_wake_recording(
        wake_word_id=3, slug="hey_domo", clip_seconds=2.0, target_count=20,
    )
    await admin_app._admin_dispatch_music(CAST, ROOM)
    await _settle(0.1)
    assert room.starts() == []
    assert room.sess.music_block() == "recording"
    assert room.sess._music_hold_task is not None
    await room.sess.stop_wake_recording()
    await _until(lambda: room.starts() != [])
    assert room.starts() == [{"type": "music_start", "stream_url": URL}]


async def test_a_held_start_looks_again_after_readying_the_stream(room, monkeypatch) -> None:
    """The room has come free, so the held start readies the stream first
    (`ensure_stream_serving`: up to music_stream_ready_timeout_sec when the
    stream is down). Someone says the wake word meanwhile. The start must
    look at the room again right before the frame (`send_music_start`'s
    still_wanted re-checks `music_block`) and hold on through the capture,
    not spawn the player into it."""
    room.reply = OFFER
    await room.turn()
    assert room.sess._music_hold_task is not None
    readying = asyncio.Event()
    ready = asyncio.Event()

    async def slow_stream(room_id, stream_url=None, *, timeout=None):
        readying.set()
        await ready.wait()
        return True

    monkeypatch.setattr(mpd_module, "ensure_stream_serving", slow_stream)
    room.sess._followup_hold_until = 0.0     # nobody answered; no capture came
    await asyncio.wait_for(readying.wait(), 2.0)
    await room.start_capture("wake_word", 2)
    await room.frames(10)
    ready.set()
    await _settle(0.1)
    assert room.starts() == []
    assert room.sess._music_hold_task is not None
    room.capture_goes_quiet()
    await _until(lambda: room.starts() != [])
    assert room.starts() == [{"type": "music_start", "stream_url": URL}]


# ─── What supersedes or drops a held start ───────────────────────────────


async def test_a_stop_drops_a_held_start(room) -> None:
    room.reply = OFFER
    await room.turn()
    assert room.sess._music_hold_task is not None
    room.reply = Response(text="Stopped.", matched_handler="music", matched_path="fast",
                          online=True, music_action="stop")
    await room.turn(trigger="followup", utt=2)
    await _settle(0.1)
    assert room.kinds()[-1] == "music_stop"
    assert room.sess._music_hold_task is None
    assert room.starts() == []


async def test_a_room_stopped_elsewhere_drops_its_held_start(room) -> None:
    """The dashboard's stop (or the playback sweeper) pops the room's
    resume intent: the held start has nothing to resume."""
    room.reply = OFFER
    await room.turn()
    hold = room.sess._music_hold_task
    room.app.state.resumable_music.pop(ROOM)
    room.sess._followup_hold_until = 0.0
    await asyncio.wait_for(asyncio.shield(hold), 1.0)
    assert room.starts() == []


async def test_a_closed_socket_drops_its_held_start(room) -> None:
    room.reply = OFFER
    await room.turn()
    hold = room.sess._music_hold_task
    room.app.state.active_sessions.pop(ROOM)    # replaced or gone
    room.sess._followup_hold_until = 0.0
    await asyncio.wait_for(asyncio.shield(hold), 1.0)
    assert room.starts() == []


async def _wait_for_py311(fut, timeout):
    """`asyncio.wait_for` as Python 3.11 runs it (requires-python is 3.11):
    the awaitable in a task of its own, a cancel of the caller passed on to
    it unless it has already finished."""
    loop = asyncio.get_running_loop()
    fut = asyncio.ensure_future(fut)
    waiter = loop.create_future()

    def release(*_a) -> None:
        if not waiter.done():
            waiter.set_result(None)

    handle = loop.call_later(timeout, release)
    fut.add_done_callback(release)
    try:
        try:
            await waiter
        except asyncio.CancelledError:
            if fut.done():
                return fut.result()
            fut.remove_done_callback(release)
            fut.cancel()
            await asyncio.gather(fut, return_exceptions=True)
            raise
        if fut.done():
            return fut.result()
        fut.remove_done_callback(release)
        fut.cancel()
        await asyncio.gather(fut, return_exceptions=True)
        raise TimeoutError
    finally:
        handle.cancel()


async def test_a_held_start_is_sent_by_its_own_task(room, monkeypatch) -> None:
    """The send drops the hold it was made for (`_safe_send_text`), which
    must find the hold's own task doing it and let it finish. Bounded with
    Python 3.11's `asyncio.wait_for`, the send ran in a task of its own:
    the hold cancelled itself mid-send and the start never went out."""
    monkeypatch.setattr(asyncio, "wait_for", _wait_for_py311)
    send = room.ws.send_text

    async def send_after_a_yield(data: str) -> None:
        await asyncio.sleep(0)              # the socket yields before the write lands
        await send(data)

    room.ws.send_text = send_after_a_yield  # type: ignore[method-assign]
    room.reply = OFFER
    await room.turn()
    assert room.sess._music_hold_task is not None
    room.sess._followup_hold_until = 0.0    # nobody answered; no capture came
    await _until(lambda: room.starts() != [])
    await _settle(0.1)
    assert room.starts() == [{"type": "music_start", "stream_url": URL}]
    assert room.app.state.pending_music_start[ROOM]["url"] == URL
    assert room.sess._music_hold_task is None


async def test_a_held_start_gives_up_after_a_long_while(room, monkeypatch, caplog) -> None:
    monkeypatch.setattr(streaming, "MUSIC_HOLD_MAX_SEC", 0.1)
    room.sess.conversational_mode = True
    room.sess.hold_music_start(URL, "chat")
    hold = room.sess._music_hold_task
    await asyncio.wait_for(asyncio.shield(hold), 1.0)
    assert room.starts() == []
    assert "stayed busy" in caplog.text
