"""The streaming fast lane, in shadow mode (early-endpointing option D).

A second recognizer follows each capture's frames while the person is
still talking, and notes when a complete closed command would have been
safe to act on — then compares that with what Whisper heard. Pinned here,
with a scripted stand-in for the recognizer (no model, no sherpa-onnx):

* shadow mode never changes routing: the same frames go to the satellite
  and the same transcript reaches route(), lane on or off;
* the would-commit decision waits for its tier's hold of quiet, and never
  comes while speech is still arriving;
* agree / disagree / missed accounting, the log line, and the record on
  the turn's timings; the summary counts them with numbers only;
* one capture per room, only for turns that could be commands, and
  nothing left behind by a disconnect, a noisy capture or the next turn;
* the local matcher: the router's walk, spelled numbers, the tier table
  against the live registry, and no tier-A prefix of a different command;
* the model fetch: the pinned archive hash and file hashes are enforced
  and nothing but the pinned files is unpacked;
* settings: off by default, never on by accident.

DB-free except the one endpoint test marked ``requires_db``.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import sys
import tarfile
import threading
import time
import types
import wave
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from domovoi import fast_lane, streaming
from domovoi.config import Settings, settings
from domovoi.fast_lane import (
    FRAME_BYTES,
    HOLD_A_MS,
    HOLD_B_MS,
    TIERS,
    FastLaneEngine,
    ModelError,
    ModelSpec,
    resolve,
    shadow_summary,
    words_to_digits,
)
from domovoi.models import Response
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import requires_db
from domovoi.turn_timings import TurnTimings

# ─── the stand-in recognizer and some audio ──────────────────────────────


class _Stream:
    def __init__(self) -> None:
        self.ms = 0.0

    def accept_waveform(self, sample_rate: int, samples) -> None:
        self.ms += len(samples) * 1000.0 / sample_rate


class ScriptedRecognizer:
    """Partial text as a function of how much audio a stream has had:
    ``[(from_ms, text), ...]`` — the last entry reached wins."""

    def __init__(self, script: list[tuple[float, str]]) -> None:
        self.script = sorted(script)
        self.created = 0

    def create_stream(self) -> _Stream:
        self.created += 1
        return _Stream()

    def is_ready(self, stream: _Stream) -> bool:
        return False

    def decode_stream(self, stream: _Stream) -> None:  # pragma: no cover
        pass

    def get_result(self, stream: _Stream) -> str:
        text = ""
        for at, t in self.script:
            if stream.ms >= at:
                text = t
        return text


def _voiced(n: int = 1) -> list[bytes]:
    t = np.arange(FRAME_BYTES // 2) / 16_000
    frame = (9000 * np.sin(2 * np.pi * 440 * t)).astype("<i2").tobytes()
    return [frame] * n


def _quiet(n: int = 1) -> list[bytes]:
    frame = (np.array([20, -20] * (FRAME_BYTES // 4))).astype("<i2").tobytes()
    return [frame] * n


def _feed(cap, frames: list[bytes]) -> None:
    """Feed frames the way a satellite does: in order, and never so far
    ahead of the worker that the capture counts as backlogged (a real
    satellite sends one frame per 30 ms)."""
    for i, f in enumerate(frames, 1):
        cap.feed(f)
        if i % 20 == 0:
            cap._engine.drain()


@pytest.fixture
def lane(monkeypatch):
    """Shadow mode with a scripted engine; returns a maker for engines."""
    monkeypatch.setattr(settings, "fastlane_mode", "shadow")

    def make(script: list[tuple[float, str]]) -> FastLaneEngine:
        eng = FastLaneEngine(ScriptedRecognizer(script), model="stub", threads=1)
        fast_lane.install_engine(eng)
        return eng

    yield make
    fast_lane.install_engine(None)


def _capture(engine: FastLaneEngine, room: str = "kitchen", trigger: str = "wake_word"):
    cap = fast_lane.open_capture(room, trigger)
    assert cap is not None
    return cap


# ─── what a transcript routes to ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "path", "hold"),
    [
        ("Pause the music.", "music._pause_from_match", HOLD_A_MS),
        ("pause the music", "music._pause_from_match", HOLD_A_MS),
        ("please turn the volume down", "music._volume_down_from_match", HOLD_A_MS),
        ("stop the timer", "timer._cancel_from_match", HOLD_B_MS),  # "... for the pasta"
        ("skip this one", "music._next_from_match", HOLD_A_MS),
        ("set a timer for ten minutes", "timer._create_from_match", HOLD_B_MS),
        ("What time is it?", "clock._time_from_match", HOLD_B_MS),
        ("what is five plus three", "calculator._arith_from_match", HOLD_B_MS),
    ],
)
def test_resolve_is_the_routers_first_match(text, path, hold) -> None:
    cmd = resolve(text)
    assert cmd is not None and cmd.path == path and cmd.hold_ms == hold


@pytest.mark.parametrize(
    ("lane_text", "whisper_text"),
    [
        ("set a timer for ten minutes", "Set a timer for 10 minutes."),
        ("set the volume to forty", "Set the volume to 40."),
        ("what's five plus three", "What's 5 plus 3?"),
        ("remind me to call mom in twenty minutes", "Remind me to call mom in 20 minutes."),
        ("play my favorites", "Play my favorites."),
    ],
)
def test_spelled_and_written_numbers_are_the_same_command(lane_text, whisper_text) -> None:
    a, b = resolve(lane_text), resolve(whisper_text)
    assert a is not None and b is not None
    assert a.key == b.key


def test_a_different_slot_is_a_different_command() -> None:
    assert resolve("set the volume to forty").key != resolve("Set the volume to 14.").key
    assert resolve("set a timer for ten minutes").key != resolve(
        "Set a timer for 10 minutes for the pasta."
    ).key


@pytest.mark.parametrize(
    "text",
    [
        "play some jazz",                      # open slot: ^play (.+)$
        "remember that my locker code is 1234",
        "announce dinner is ready",
        "drop in on the kitchen",
        "who wrote the odyssey",               # the language model's
        "remind me to call mom in ten minutes",  # a free-text message
        "",
    ],
)
def test_open_slots_and_questions_never_commit(text) -> None:
    cmd = resolve(text)
    assert cmd is None or cmd.hold_ms is None


@pytest.mark.parametrize("text", ["stop", "next", "pause", "cancel", "go back", "skip"])
def test_bare_words_and_extensible_heads_get_the_long_hold(text) -> None:
    cmd = resolve(text)
    assert cmd is not None and cmd.hold_ms == HOLD_B_MS


def test_a_plugin_fast_path_never_commits(monkeypatch) -> None:
    from domovoi.handlers import HANDLERS, register_handler, unregister_handler
    from domovoi.handlers.base import FastPath, Handler, HandlerDisplay

    class _Plugin(Handler):
        name = "stub_plugin"
        priority_band = 105  # ahead of everything but dismiss
        plugin_slug = "stub"
        tool_schema = {"name": "stub_plugin"}
        display = HandlerDisplay(label="Stub")

        async def _pause(self, m, ctx, session):  # pragma: no cover
            return Response(text="")

        fast_paths = [FastPath(pattern=__import__("re").compile(r"^pause the music$"), method=_pause)]

        async def execute(self, intent, ctx, session):  # pragma: no cover
            return Response(text="")

    h = _Plugin()
    register_handler(h)
    try:
        cmd = resolve("pause the music")
        assert cmd is not None and cmd.handler == "stub_plugin" and cmd.hold_ms is None
    finally:
        unregister_handler(h)
    assert "stub_plugin" not in [x.name for x in HANDLERS]


def test_every_tier_names_a_live_core_fast_path() -> None:
    """A renamed handler method would silently drop out of the fast lane;
    this makes it loud."""
    from domovoi.handlers import HANDLER_BY_NAME
    from domovoi.handlers.base import as_fast_path

    for handler_name, method in TIERS:
        h = HANDLER_BY_NAME.get(handler_name)
        assert h is not None, handler_name
        assert getattr(h, "plugin_slug", None) is None, handler_name
        names = {as_fast_path(e).method.__name__ for e in h.fast_paths}
        assert method in names, f"{handler_name}.{method}"


# Commands as people say them (Whisper's spelling). The research corpus of
# the early-endpointing design, plus the phrasings found since.
CORPUS = """set a timer for 10 minutes
set a timer for 10 minutes and 30 seconds
set a timer for 10 minutes for the pasta
set a timer for 1 hour and a half
timer for 5 minutes called eggs
remind me to take out the trash in 10 minutes
stop
stop the timer
stop the music
stop the call
stop saving that i like jazz
pause
pause the music
resume
resume the music
continue
continue my book
next
next song
next chapter
skip
skip this song
skip forward 30 seconds
back
go back
go back a chapter
previous
previous chapter
volume up
volume down
turn the volume down
turn it down
set the volume to 20
volume 5
louder
quieter
what time is it
what time is it in tokyo
what day is it
what day is it today
play some jazz
play my favorites
play my workout playlist
shuffle
shuffle my favorites
cancel
cancel that
cancel the timer
cancel my reminder to call mom
no
no thanks
never mind
nothing else
what's 5 plus 3
what's 5 plus 3 times 2
repeat
repeat that
say that again
are you sure
what's playing
what song is this
hang up
how's the wifi
what voices do you have
how many songs do i have
list my reminders
how much time left on the timer
cancel the timer for the pasta
stop the timer called eggs
what was that song
what did you say about the weather
what are my reminders for tomorrow
how many albums does adele have
what's this book about""".splitlines()


def test_no_tier_a_prefix_cuts_off_a_different_command() -> None:
    """A 350 ms hold is only for phrases nothing extends: no proper
    word-prefix of a corpus command may commit on the short hold unless it
    is the same command."""
    hazards = []
    for full in CORPUS:
        words = full.split()
        whole = resolve(full)
        for i in range(1, len(words)):
            prefix = " ".join(words[:i])
            cmd = resolve(prefix)
            if cmd is None or cmd.hold_ms != HOLD_A_MS:
                continue
            if whole is None or whole.key != cmd.key:
                hazards.append((prefix, full))
    assert hazards == []


def test_the_lanes_british_spellings_reach_the_american_patterns(lane) -> None:
    assert fast_lane.lane_spelling("play my favourites") == "play my favorites"
    eng = lane([(0, "play my favourites")])
    cap = _capture(eng)
    _feed(cap, _quiet(3) + _voiced(20) + _quiet(13))
    eng.drain()
    assert cap.decision.command.path == "playlist._play_favorites_from_match"
    cap.finish()


def test_words_to_digits() -> None:
    assert words_to_digits("set a timer for ten minutes") == "set a timer for 10 minutes"
    assert words_to_digits("forty five") == "45"
    assert words_to_digits("twenty-five") == "25"
    assert words_to_digits("one hundred and twenty five") == "125"
    assert words_to_digits("three hundred") == "300"
    assert words_to_digits("nothing to see") == "nothing to see"


# ─── voiced frames ───────────────────────────────────────────────────────


def test_continuous_speech_stays_voiced_and_the_quiet_after_it_does_not() -> None:
    tr = fast_lane._VoicedTracker()
    quiet = fast_lane._frames_dbfs(_quiet()[0])[0]
    loud = fast_lane._frames_dbfs(_voiced()[0])[0]
    assert not tr.voiced(quiet)
    # Six seconds of unbroken speech: the floor must not creep up to it.
    assert all(tr.voiced(loud) for _ in range(200))
    assert not tr.voiced(quiet)


def test_a_capture_that_opens_mid_word_is_not_voiced_until_a_gap() -> None:
    tr = fast_lane._VoicedTracker()
    loud = fast_lane._frames_dbfs(_voiced()[0])[0]
    quiet = fast_lane._frames_dbfs(_quiet()[0])[0]
    assert not tr.voiced(loud)   # the floor starts at the speech
    assert not tr.voiced(quiet)  # the gap drops it
    assert tr.voiced(loud)


# ─── the decision ────────────────────────────────────────────────────────


def test_a_complete_command_commits_only_after_its_hold(lane) -> None:
    eng = lane([(0, "pause"), (300, "pause the music")])
    cap = _capture(eng)
    _feed(cap, _quiet(5) + _voiced(20))
    quiet_frames = HOLD_A_MS // 30  # 11 frames = 330 ms, one short
    _feed(cap, _quiet(quiet_frames))
    eng.drain()
    assert cap.decision is None
    _feed(cap, _quiet(1))
    eng.drain()
    assert cap.decision is not None
    assert cap.decision.command.path == "music._pause_from_match"
    assert cap.decision.lane_text == "pause the music"
    cap.finish()


def test_speech_that_resumes_before_the_hold_commits_nothing(lane) -> None:
    # "stop ... the timer" with a 400 ms pause: "stop" alone holds 650 ms.
    eng = lane([(0, "stop"), (1000, "stop the timer")])
    cap = _capture(eng)
    _feed(cap, _quiet(3) + _voiced(10) + _quiet(13))
    eng.drain()
    assert cap.decision is None
    _feed(cap, _voiced(10) + _quiet(5))
    eng.drain()
    assert cap.decision is None
    _feed(cap, _quiet(8))  # now 390 ms after "the timer", which can take a label
    eng.drain()
    assert cap.decision is None
    _feed(cap, _quiet(9))  # 660 ms
    eng.drain()
    assert cap.decision.command.path == "timer._cancel_from_match"
    cap.finish()


def test_tier_b_waits_longer(lane) -> None:
    eng = lane([(0, "set a timer for ten minutes")])
    cap = _capture(eng)
    _feed(cap, _quiet(3) + _voiced(30) + _quiet(20))  # 600 ms
    eng.drain()
    assert cap.decision is None
    _feed(cap, _quiet(2))                 # 660 ms
    eng.drain()
    assert cap.decision.command.path == "timer._create_from_match"
    cap.finish()


def test_an_open_slot_never_commits(lane) -> None:
    eng = lane([(0, "play some jazz")])
    cap = _capture(eng)
    _feed(cap, _quiet(3) + _voiced(30) + _quiet(60))
    eng.drain()
    assert cap.decision is None
    cap.finish()


def test_nothing_is_decided_after_the_capture_ended(lane) -> None:
    eng = lane([(0, "pause the music")])
    cap = _capture(eng)
    _feed(cap, _quiet(3) + _voiced(20))
    eng.drain()
    cap.finish()
    _feed(cap, _quiet(30))
    eng.drain()
    assert cap.decision is None


def test_a_long_capture_stops_being_decoded(lane) -> None:
    eng = lane([(0, "and then there was this one time")])
    cap = _capture(eng)
    _feed(cap, _quiet(3) + _voiced(int(fast_lane.MAX_DECODE_MS / 30) + 20))
    eng.drain()
    assert cap.gave_up == "long"
    assert eng.live_streams == 0
    cap.finish()


def test_a_backlog_drops_the_capture_instead_of_deciding_late(lane) -> None:
    eng = lane([(0, "pause the music")])
    cap = _capture(eng)
    gate = threading.Event()
    eng.submit(gate.wait)  # the worker is busy elsewhere
    for f in _voiced(int(fast_lane.MAX_BACKLOG_MS / 30) + 5):
        cap.feed(f)
    gate.set()
    eng.drain()
    assert cap.gave_up == "backlog"
    assert cap.decision is None
    cap.finish()
    eng.drain()
    assert eng.live_streams == 0


def test_the_batch_in_flight_at_utterance_end_is_not_decoded(lane) -> None:
    """utterance_end is when Whisper starts on the same CPU: the lane stops
    decoding there, even in the middle of a batch it already took."""
    eng = lane([(0, "pause the music")])
    inside = threading.Event()
    release = threading.Event()
    accepted: list[float] = []

    class _SlowStream(_Stream):
        def accept_waveform(self, sample_rate: int, samples) -> None:
            accepted.append(self.ms)
            if len(accepted) == 1:
                inside.set()
                release.wait(5)
            super().accept_waveform(sample_rate, samples)

    eng.recognizer.create_stream = _SlowStream
    cap = _capture(eng)
    gate = threading.Event()
    eng.submit(gate.wait)
    for f in _voiced(10):  # one batch of ten frames
        cap.feed(f)
    gate.set()
    assert inside.wait(5)  # the worker is inside the batch's first frame
    cap.finish()
    release.set()
    eng.drain()
    assert len(accepted) == 1
    assert eng.live_streams == 0


def test_opening_a_capture_never_raises_into_the_socket_loop(lane, monkeypatch, caplog) -> None:
    eng = lane([(0, "pause the music")])

    def _boom(**kw):
        raise RuntimeError("no more threads")

    monkeypatch.setattr(eng, "open_capture", _boom)
    assert fast_lane.open_capture("kitchen", "wake_word") is None
    assert any("could not open a capture" in r.getMessage() for r in caplog.records)


# ─── settling against Whisper ────────────────────────────────────────────


def _settled(cap, whisper_text: str) -> dict:
    timings = TurnTimings(audio_bytes=32_000)
    assert fast_lane.settle(cap, whisper_text, timings) is None
    return timings.row_document()


def test_agreement_is_recorded_and_logged(lane, caplog) -> None:
    caplog.set_level(logging.INFO, logger="domovoi.fast_lane")
    eng = lane([(0, "pause the music")])
    cap = _capture(eng)
    _feed(cap, _quiet(3) + _voiced(20) + _quiet(14))
    eng.drain()
    doc = _settled(cap, "Pause the music.")
    assert doc["fastlane_seen"] is True
    assert doc["fastlane_agree"] is True
    assert doc["fastlane_path"] == "music._pause_from_match"
    assert doc["fastlane_text"] == "pause the music"
    assert doc["fastlane_ms"] >= 0 and doc["fastlane_lead_ms"] >= 0
    assert doc["fastlane_after_ms"] == 0
    assert isinstance(doc["fastlane_cpu_ms"], int)
    line = next(r.getMessage() for r in caplog.records if "fastlane would commit" in r.getMessage())
    assert "music._pause_from_match at +" in line
    assert "whisper later said 'Pause the music.'" in line and "agree=True" in line


def test_disagreement_and_the_speech_that_came_after(lane) -> None:
    # The lane commits "stop" after 650 ms; the person then says "the timer".
    eng = lane([(0, "stop")])
    cap = _capture(eng)
    _feed(cap, _quiet(3) + _voiced(10) + _quiet(23))
    eng.drain()
    assert cap.decision is not None
    _feed(cap, _voiced(12))
    eng.drain()
    doc = _settled(cap, "Stop the timer.")
    assert doc["fastlane_agree"] is False
    assert doc["fastlane_path"] == "music._stop_from_match"
    assert doc["fastlane_after_ms"] == 12 * 30


def test_a_miss_is_counted_when_whisper_heard_a_committable_command(lane) -> None:
    eng = lane([(0, "paws the muse ik")])
    cap = _capture(eng)
    _feed(cap, _quiet(3) + _voiced(20) + _quiet(20))
    eng.drain()
    doc = _settled(cap, "Pause the music.")
    assert doc["fastlane_missed"] is True
    assert "fastlane_agree" not in doc and "fastlane_text" not in doc


def test_a_capture_the_core_ended_early_is_preempted_not_missed(lane, monkeypatch) -> None:
    """With early commit on (streaming.py, part B), the core can stop
    listening at the same moment the lane's own hold would have run out —
    the lane never got the quiet it waits for. Measured end to end with
    Whisper tiny: every tier-B command read as a lane "miss" before this."""
    monkeypatch.setattr(settings, "fastlane_mode", "shadow")
    eng = lane([(0, "set a timer for ten minutes")])
    cap = _capture(eng)
    _feed(cap, _quiet(3) + _voiced(20) + _quiet(15))   # 450 ms: short of 650
    eng.drain()
    assert cap.decision is None
    cap.finish(preempted=True)
    doc = _settled(cap, "Set a timer for 10 minutes.")
    assert doc["fastlane_missed"] is False and doc["fastlane_preempted"] is True
    s = shadow_summary([doc])
    assert (s["missed"], s["preempted"], s["would_commit"]) == (0, 1, 0)


def test_a_decision_made_before_the_early_commit_still_counts(lane) -> None:
    eng = lane([(0, "pause the music")])
    cap = _capture(eng)
    _feed(cap, _quiet(3) + _voiced(20) + _quiet(14))   # past the 350 ms hold
    eng.drain()
    assert cap.decision is not None
    cap.finish(preempted=True)
    doc = _settled(cap, "Pause the music.")
    assert doc["fastlane_agree"] is True and "fastlane_preempted" not in doc


def test_a_question_is_neither_a_commit_nor_a_miss(lane) -> None:
    eng = lane([(0, "who wrote the odyssey")])
    cap = _capture(eng)
    _feed(cap, _quiet(3) + _voiced(30) + _quiet(30))
    eng.drain()
    doc = _settled(cap, "Who wrote The Odyssey?")
    assert doc["fastlane_missed"] is False
    assert "fastlane_text" not in doc


def test_settle_never_raises(lane, monkeypatch, caplog) -> None:
    eng = lane([(0, "pause the music")])
    cap = _capture(eng)

    def _boom(text):
        raise RuntimeError("matcher broke")

    monkeypatch.setattr(fast_lane, "resolve", _boom)
    timings = TurnTimings()
    assert fast_lane.settle(cap, "Pause the music.", timings) is None
    assert timings.extra == {}
    assert any("could not settle" in r.getMessage() for r in caplog.records)
    assert fast_lane.settle(None, "anything", timings) is None


# ─── the summary: numbers only ───────────────────────────────────────────


def test_the_summary_counts_and_leaks_no_text(monkeypatch) -> None:
    monkeypatch.setattr(settings, "fastlane_mode", "off")
    docs = [
        {"stt_ms": 900, "fastlane_seen": True, "fastlane_ms": 420, "fastlane_lead_ms": 900,
         "fastlane_text": "pause the music", "fastlane_path": "music._pause_from_match",
         "fastlane_agree": True, "fastlane_after_ms": 0, "fastlane_cpu_ms": 40},
        {"fastlane_seen": True, "fastlane_ms": 700, "fastlane_lead_ms": 500,
         "fastlane_text": "stop", "fastlane_path": "music._stop_from_match",
         "fastlane_agree": False, "fastlane_after_ms": 360, "fastlane_cpu_ms": 60},
        json.dumps({"fastlane_seen": True, "fastlane_missed": True, "fastlane_cpu_ms": 50}),
        {"fastlane_seen": True, "fastlane_missed": False, "fastlane_cpu_ms": 80},
        {"stt_ms": 700},               # a turn the lane didn't see
        "not json {", None,
    ]
    s = shadow_summary(docs)
    assert s["observed"] == 4 and s["would_commit"] == 2
    assert (s["agree"], s["disagree"], s["missed"], s["speech_after_commit"]) == (1, 1, 1, 1)
    assert s["commit_ms"] == {"count": 2, "p50": 560, "p95": 686, "max": 700}
    assert s["lead_ms"]["count"] == 2 and s["cpu_ms"]["count"] == 4
    raw = json.dumps(s)
    for leaked in ("pause", "stop", "music", "_from_match"):
        assert leaked not in raw, leaked


def test_the_summary_is_absent_while_off_with_nothing_to_count(monkeypatch) -> None:
    monkeypatch.setattr(settings, "fastlane_mode", "off")
    assert shadow_summary([{"stt_ms": 100}, None]) is None
    monkeypatch.setattr(settings, "fastlane_mode", "shadow")
    s = shadow_summary([])
    assert s["mode"] == "shadow" and s["observed"] == 0


# ─── through the streaming layer ─────────────────────────────────────────


class _FakeWS:
    def __init__(self) -> None:
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                satellite_voice={}, wifi_status={}, satellite_volume={},
                greeting_phrases=[], resumable_music={}, current_playlist={},
                active_sessions={}, probe=types.SimpleNamespace(online=True),
                # Every real app has it (main.py); the speculative-transcription
                # gate reads a satellite's reported silence timeout from it.
                satellite_config={},
            )
        )
        self.sent_text: list[dict] = []
        self.sent_bytes: list[bytes] = []

    async def send_text(self, data: str) -> None:
        self.sent_text.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)


def _wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(pcm)
    return buf.getvalue()


class _Whisper:
    def __init__(self, text: str) -> None:
        self.text = text

    async def transcribe(self, pcm: bytes) -> str:
        return self.text


class _TTS:
    async def synthesize(self, text, *, engine=None, voice=None) -> bytes:
        return _wav(b"\x01\x02" * 400)


@asynccontextmanager
async def _no_db():
    yield None


@pytest.fixture
def turn(monkeypatch):
    """A DB-free turn: route() records what it was given and what the row
    would carry."""
    seen: dict = {"routed": [], "rows": []}

    async def _route(intent, ctx, session):
        seen["routed"].append((intent.transcript, ctx.room_id, ctx.trigger))
        seen["rows"].append(ctx.timings.row_document())
        return Response(text="Paused.", matched_handler="music", matched_path="fast",
                        online=True)

    async def _identify(pcm):
        return None

    async def _merge(s, row_id, patch):  # pragma: no cover
        pass

    monkeypatch.setattr(streaming, "route", _route)
    monkeypatch.setattr(streaming, "merge_post_route", _merge)
    monkeypatch.setattr(streaming, "session_scope", _no_db)
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper("Pause the music."))
    monkeypatch.setattr("domovoi.voice_identifier.identify", _identify)
    monkeypatch.setattr(streaming, "get_tts_client", lambda: _TTS())
    return seen


async def _speak(sess: StreamSession, frames: list[bytes], trigger: str = "wake_word") -> None:
    await sess._on_control({"type": "utterance_start", "trigger": trigger})
    eng = fast_lane._ENGINE
    for i, f in enumerate(frames, 1):
        await sess._on_audio(f)
        if eng is not None and i % 20 == 0:
            await asyncio.to_thread(eng.drain)
    if eng is not None:
        await asyncio.to_thread(eng.drain)
    await sess._on_control({"type": "utterance_end"})
    if sess._response_task is not None:
        await sess._response_task


_COMMAND = _quiet(5) + _voiced(25) + _quiet(20)


@pytest.mark.asyncio
async def test_shadow_never_changes_routing(turn, lane, monkeypatch) -> None:
    """The same capture, lane off and lane on: the satellite gets the same
    frames and route() gets the same transcript. Only the timings row
    gains the shadow record."""
    monkeypatch.setattr(settings, "fastlane_mode", "off")
    ws_off = _FakeWS()
    await _speak(StreamSession(ws_off, "kitchen"), _COMMAND)  # type: ignore[arg-type]

    monkeypatch.setattr(settings, "fastlane_mode", "shadow")
    eng = lane([(0, "pause the music")])
    ws_on = _FakeWS()
    sess = StreamSession(ws_on, "kitchen")  # type: ignore[arg-type]
    await _speak(sess, _COMMAND)

    assert ws_on.sent_text == ws_off.sent_text
    assert ws_on.sent_bytes == ws_off.sent_bytes
    assert turn["routed"][0] == turn["routed"][1] == ("Pause the music.", "kitchen", "wake_word")
    off_row, on_row = turn["rows"]
    assert not any(k.startswith("fastlane") for k in off_row)
    assert on_row["fastlane_agree"] is True
    assert {k: v for k, v in on_row.items() if not k.startswith("fastlane")}.keys() == off_row.keys()
    assert sess._fastlane is None
    assert eng.open_captures == 0


@pytest.mark.asyncio
async def test_a_disagreeing_lane_still_routes_what_whisper_heard(turn, lane) -> None:
    eng = lane([(0, "stop")])
    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    await _speak(sess, _quiet(3) + _voiced(10) + _quiet(25) + _voiced(10) + _quiet(5))
    assert turn["routed"] == [("Pause the music.", "kitchen", "wake_word")]
    assert turn["rows"][0]["fastlane_agree"] is False
    assert eng.open_captures == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["barge_in", "chat", "wake_clip", "push_to_talk", None])
async def test_turns_that_are_not_commands_open_no_capture(lane, trigger) -> None:
    eng = lane([(0, "pause the music")])
    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    await sess._on_control({"type": "utterance_start", "trigger": trigger})
    assert sess._fastlane is None and eng.open_captures == 0


@pytest.mark.asyncio
async def test_chat_mode_and_an_unready_lane_open_no_capture(lane, monkeypatch) -> None:
    eng = lane([(0, "pause the music")])
    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    sess.conversational_mode = True
    await sess._on_control({"type": "utterance_start", "trigger": "wake_word"})
    assert sess._fastlane is None

    fast_lane.install_engine(None)
    monkeypatch.setattr(settings, "fastlane_mode", "shadow")
    sess.conversational_mode = False
    await sess._on_control({"type": "utterance_start", "trigger": "wake_word"})
    assert sess._fastlane is None
    assert eng.open_captures == 0


@pytest.mark.asyncio
async def test_each_room_has_its_own_capture(lane, monkeypatch) -> None:
    async def _no_apology(self):
        return None

    monkeypatch.setattr(StreamSession, "_respond_noisy_capture", _no_apology)
    eng = lane([(0, "pause the music")])
    kitchen = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    office = StreamSession(_FakeWS(), "office")  # type: ignore[arg-type]
    for s in (kitchen, office):
        await s._on_control({"type": "utterance_start", "trigger": "wake_word"})
    assert eng.open_captures == 2
    assert kitchen._fastlane is not office._fastlane
    for f in _quiet(3) + _voiced(20):
        await kitchen._on_audio(f)
    for f in _quiet(20):
        await kitchen._on_audio(f)
        await office._on_audio(f)
    await asyncio.to_thread(eng.drain)
    assert kitchen._fastlane.decision is not None
    assert office._fastlane.decision is None  # it never heard any speech
    # The next utterance in a room replaces its capture; the old one closes.
    old = kitchen._fastlane
    await kitchen._on_control({"type": "utterance_start", "trigger": "followup"})
    assert kitchen._fastlane is not old and old.ended_at is not None
    assert eng.open_captures == 2
    # A noisy capture closes it too.
    await office._on_control({"type": "noisy_capture"})
    assert office._fastlane is None
    await asyncio.to_thread(eng.drain)
    kitchen._fastlane = fast_lane.close(kitchen._fastlane)
    if office._response_task is not None:
        await office._response_task
    await asyncio.to_thread(eng.drain)
    assert eng.open_captures == 0 and eng.live_streams == 0


class _ScriptedSocket(_FakeWS):
    """Drives StreamSession.run(): a hello, then the given frames, then a
    disconnect."""

    def __init__(self, frames: list[dict]) -> None:
        super().__init__()
        st = self.app.state
        for name in (
            "satellite_full_duplex", "satellite_synced_sha", "satellite_sat_type",
            "satellite_mic_enabled", "satellite_config", "satellite_display",
        ):
            setattr(st, name, {})
        self.client = types.SimpleNamespace(host="192.168.1.50")
        self.headers: dict[str, str] = {}
        self._frames = [{"type": "websocket.receive", "text": json.dumps({"type": "hello"})}]
        self._frames += frames + [{"type": "websocket.disconnect"}]

    async def accept(self) -> None:
        pass

    async def receive(self) -> dict:
        await asyncio.sleep(0)
        return self._frames.pop(0)

    async def close(self, code: int = 1000) -> None:
        pass


@pytest.mark.asyncio
async def test_nothing_is_left_behind_after_a_disconnect(lane, monkeypatch) -> None:
    """A satellite that drops mid-capture: the capture is closed, and the
    recognizer stream it held is released on the worker."""
    eng = lane([(0, "pause the")])

    async def _accept(self, ctrl):
        return True

    monkeypatch.setattr(StreamSession, "_validate_pairing", _accept)
    frames = [{"type": "websocket.receive",
               "text": json.dumps({"type": "utterance_start", "trigger": "wake_word"})}]
    frames += [{"type": "websocket.receive", "bytes": f} for f in _voiced(20)]
    ws = _ScriptedSocket(frames)
    sess = StreamSession(ws, "kitchen")  # type: ignore[arg-type]
    await sess.run()
    await asyncio.to_thread(eng.drain)
    assert sess._fastlane is None
    assert eng.open_captures == 0
    assert eng.live_streams == 0
    assert eng.recognizer.created == 1


# ─── the lifecycle ───────────────────────────────────────────────────────


def test_settings_default_off_and_never_on_by_accident() -> None:
    from domovoi.config_schema import FIELD_BY_NAME

    assert Settings.model_fields["fastlane_mode"].default == "off"
    assert Settings(fastlane_mode=" SHADOW ").fastlane_mode == "shadow"
    assert Settings(fastlane_mode="on").fastlane_mode == "off"
    assert Settings(fastlane_mode="").fastlane_mode == "off"
    assert Settings.model_fields["fastlane_model"].default in fast_lane.MODELS
    assert Settings.model_fields["fastlane_cpu_threads"].default == 1
    mode = FIELD_BY_NAME["fastlane_mode"]
    assert (mode.type, mode.tier, mode.choices) == ("choice", "reapply", ["off", "shadow"])
    model = FIELD_BY_NAME["fastlane_model"]
    assert (model.type, model.tier) == ("choice", "restart")
    assert model.choices == list(fast_lane.MODELS)
    assert FIELD_BY_NAME["fastlane_cpu_threads"].tier == "restart"


def test_saving_the_mode_runs_the_lanes_hook() -> None:
    from domovoi import reapply
    from domovoi.main import _register_core_reapply_hooks

    _register_core_reapply_hooks()
    assert fast_lane.apply_mode in reapply._HOOKS["fastlane_mode"].values()


def test_off_loads_nothing_and_stubs_never_load(monkeypatch) -> None:
    started = []
    monkeypatch.setattr(fast_lane, "_spawn_loader", started.append)
    monkeypatch.setattr(settings, "fastlane_mode", "off")
    fast_lane.start()
    monkeypatch.setattr(settings, "fastlane_mode", "shadow")
    fast_lane.start()  # use_stubs is true under the suite
    assert started == []
    assert fast_lane.open_capture("kitchen", "wake_word") is None


def _wait_state(want: str, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = fast_lane.status()["state"]
        if state == want:
            return state
        time.sleep(0.02)
    return fast_lane.status()["state"]


def test_without_the_extra_the_lane_is_unavailable_not_broken(monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "use_stubs", False)
    monkeypatch.setattr(settings, "fastlane_mode", "shadow")
    monkeypatch.setitem(sys.modules, "sherpa_onnx", None)  # import fails
    fast_lane.install_engine(None)
    try:
        fast_lane.start()
        assert _wait_state("unavailable") == "unavailable"
        assert any("sherpa-onnx is not installed" in r.getMessage() for r in caplog.records)
        assert fast_lane.open_capture("kitchen", "wake_word") is None
        # Not retried per utterance; saving the mode again retries.
        threads: list[int] = []
        monkeypatch.setattr(fast_lane, "_spawn_loader", threads.append)
        fast_lane.open_capture("kitchen", "wake_word")
        assert threads == []
        fast_lane.apply_mode()
        assert len(threads) == 1
    finally:
        fast_lane.install_engine(None)


def test_turning_it_off_drops_the_engine(lane, monkeypatch) -> None:
    eng = lane([(0, "pause the music")])
    cap = _capture(eng)
    monkeypatch.setattr(settings, "fastlane_mode", "off")
    fast_lane.apply_mode()
    assert fast_lane.status()["state"] == "off"
    assert cap.ended_at is not None
    assert fast_lane.open_capture("kitchen", "wake_word") is None


# ─── the model fetch ─────────────────────────────────────────────────────


def _archive(tmp_path: Path, files: dict[str, bytes], extra: dict[str, bytes] | None = None) -> Path:
    path = tmp_path / "model.tar.bz2"
    with tarfile.open(path, "w:bz2") as tf:
        for name, data in {**files, **(extra or {})}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _spec(archive: Path, files: dict[str, bytes]) -> ModelSpec:
    roles = dict(zip(("encoder", "decoder", "joiner", "tokens"), files))
    return ModelSpec(
        name="stub-model",
        url="https://example.invalid/model.tar.bz2",
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        size=archive.stat().st_size,
        archive_dir="stub",
        files={role: (Path(name).name, hashlib.sha256(files[name]).hexdigest())
               for role, name in roles.items()},
        source="test",
    )


_FILES = {
    "stub/encoder.onnx": b"enc" * 100,
    "stub/decoder.onnx": b"dec" * 100,
    "stub/joiner.onnx": b"joi" * 100,
    "stub/tokens.txt": b"a 1\nb 2\n",
}


def _fetcher(archive: Path, calls: list):
    def fetch(url, dest, *, size):
        calls.append(url)
        data = archive.read_bytes()
        Path(dest).write_bytes(data)
        return hashlib.sha256(data).hexdigest()
    return fetch


def test_the_model_is_fetched_once_and_only_its_pinned_files_unpacked(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    archive = _archive(src, _FILES, extra={
        "stub/test_wavs/0.wav": b"RIFF",
        "../escape.txt": b"nope",
        "stub/../../escape2.txt": b"nope",
    })
    spec = _spec(archive, _FILES)
    root = tmp_path / "models"
    calls: list = []
    target = fast_lane.ensure_model(spec, root, fetch=_fetcher(archive, calls))
    assert sorted(p.name for p in target.iterdir()) == sorted(
        [".verified", "decoder.onnx", "encoder.onnx", "joiner.onnx", "tokens.txt"]
    )
    assert not (tmp_path / "escape.txt").exists() and not (root / "escape2.txt").exists()
    assert sorted(p.name for p in root.iterdir()) == ["stub-model"]  # no leftovers
    fast_lane.ensure_model(spec, root, fetch=_fetcher(archive, calls))
    assert len(calls) == 1


def test_a_wrong_archive_hash_leaves_nothing(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    archive = _archive(src, _FILES)
    spec = _spec(archive, _FILES)
    bad = ModelSpec(**{**spec.__dict__, "sha256": "0" * 64})
    root = tmp_path / "models"
    with pytest.raises(ModelError, match="checksum mismatch"):
        fast_lane.ensure_model(bad, root, fetch=_fetcher(archive, []))
    assert list(root.iterdir()) == []


def test_a_file_that_fails_its_hash_is_refused(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    archive = _archive(src, _FILES)
    spec = _spec(archive, _FILES)
    files = dict(spec.files)
    files["joiner"] = ("joiner.onnx", "f" * 64)
    bad = ModelSpec(**{**spec.__dict__, "files": files})
    root = tmp_path / "models"
    with pytest.raises(ModelError, match="joiner.onnx"):
        fast_lane.ensure_model(bad, root, fetch=_fetcher(archive, []))
    assert list(root.iterdir()) == []


def test_a_file_changed_after_the_download_is_never_handed_to_the_loader(tmp_path) -> None:
    """sherpa-onnx ends the process on a model file with the wrong metadata,
    so the marker alone never vouches for the files: they are checked on
    every call, and a damaged set is fetched again."""
    src = tmp_path / "src"
    src.mkdir()
    archive = _archive(src, _FILES)
    spec = _spec(archive, _FILES)
    root = tmp_path / "models"
    calls: list = []
    target = fast_lane.ensure_model(spec, root, fetch=_fetcher(archive, calls))
    (target / "encoder.onnx").write_bytes(b"dec" * 100)  # another valid-looking file
    assert (target / ".verified").is_file()
    again = fast_lane.ensure_model(spec, root, fetch=_fetcher(archive, calls))
    assert len(calls) == 2
    assert (again / "encoder.onnx").read_bytes() == _FILES["stub/encoder.onnx"]


def test_a_native_library_that_will_not_load_is_unavailable_not_stuck(monkeypatch, caplog) -> None:
    import builtins

    real_import = builtins.__import__

    def _import(name, *args, **kwargs):
        if name == "sherpa_onnx":
            raise OSError("libonnxruntime.so: cannot open shared object file")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(settings, "use_stubs", False)
    monkeypatch.setattr(settings, "fastlane_mode", "shadow")
    monkeypatch.setattr(builtins, "__import__", _import)
    fast_lane.install_engine(None)
    try:
        fast_lane.start()
        assert _wait_state("unavailable") == "unavailable"
        assert any("won't load" in r.getMessage() for r in caplog.records)
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)
        fast_lane.install_engine(None)


def test_the_pinned_model_is_the_default_and_fully_pinned() -> None:
    spec = fast_lane.MODELS[fast_lane.DEFAULT_MODEL]
    assert spec.url.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/")
    assert len(spec.sha256) == 64 and spec.size > 0
    assert set(spec.files) == {"encoder", "decoder", "joiner", "tokens"}
    assert all(len(digest) == 64 for _, digest in spec.files.values())


# ─── the latency endpoint ────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_the_latency_endpoint_counts_the_shadow_records(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from domovoi import turn_timings
    from domovoi.db.session import session_scope
    from domovoi.main import app
    from domovoi.tests.test_turn_timings import _ensure_v015, _truncate

    await _ensure_v015()
    monkeypatch.setattr(turn_timings, "_HAS_TIMINGS_COLUMN", None)
    monkeypatch.setattr(settings, "fastlane_mode", "off")
    await _truncate()
    now = datetime.now(timezone.utc)
    docs = [
        {"stt_ms": 800, "fastlane_seen": True, "fastlane_ms": 450, "fastlane_lead_ms": 950,
         "fastlane_text": "pause the music", "fastlane_path": "music._pause_from_match",
         "fastlane_agree": True, "fastlane_after_ms": 0, "fastlane_cpu_ms": 35},
        {"stt_ms": 900, "fastlane_seen": True, "fastlane_missed": False, "fastlane_cpu_ms": 70},
    ]
    try:
        async with session_scope() as s:
            for i, doc in enumerate(docs):
                await s.execute(
                    text(
                        "INSERT INTO intents_log (room_id, transcript, matched_handler, "
                        "matched_path, online, latency_ms, timings, at) VALUES "
                        "('kitchen', 'pause the music', 'music', 'fast', true, 9, "
                        "CAST(:doc AS jsonb), :at)"
                    ),
                    {"doc": json.dumps(doc), "at": now - timedelta(minutes=i)},
                )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/v1/stats/latency")
        assert r.status_code == 200, r.text
        fl = r.json()["fastlane"]
        assert (fl["observed"], fl["would_commit"], fl["agree"]) == (2, 1, 1)
        assert fl["commit_ms"]["p50"] == 450
        # Numbers only: neither the lane's text nor its handler path comes
        # back. Checked on the values, since the answer's own key names
        # now say "pause" (capture_timing's sat_pause_ms and the like).

        def _strings(v):
            if isinstance(v, dict):
                for x in v.values():
                    yield from _strings(x)
            elif isinstance(v, list):
                for x in v:
                    yield from _strings(x)
            elif isinstance(v, str):
                yield v

        assert [s for s in _strings(r.json()) if "pause" in s or "music" in s] == []
        assert "music" not in r.text and "pause the" not in r.text
    finally:
        await _truncate()
