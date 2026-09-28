"""Speculative transcription: Whisper starts at the first pause, and the
turn uses that transcript only when no speech came after it.

Early endpointing, part A (design notes 2026-09-28). A satellite ends a
capture after `listen.silence_timeout` of silence (1.2 s by default), but
every frame is on the core as it is spoken; the core transcribes a copy at
the first ~240 ms pause and, at `utterance_end`, uses it if and only if the
satellite's last voiced frame is inside the copy. These drive the real
`/v1/stream` socket with a Whisper stub whose latency the test sets and
which records every call — how long its audio was, and how many ran at
once — so "reused", "discarded" and "never two at once" are observed, not
inferred. No database: routing, TTS and voice identification are stubbed.

The pure pieces (`domovoi/endpointing.py`) are tested first.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import wave
from contextlib import asynccontextmanager

import numpy as np
import pytest
from fastapi.testclient import TestClient

from domovoi.config import settings
from domovoi.endpointing import (
    FRAME_MS,
    LevelPauseDetector,
    frame_limit,
    last_voiced_from_timeout,
)
from domovoi.main import app
from domovoi.models import Response
from domovoi.streaming import CORE_FEATURES, SPECULATIVE_MAX_DECODES, StreamSession

FRAME_BYTES = 960
LOUD = np.full(480, 8000, dtype=np.int16).tobytes()      # about -12 dBFS
QUIET = bytes(FRAME_BYTES)                                # digital silence


# ─── the pure pieces ───────────────────────────────────────────────────────


def test_frame_limit_truncates_exactly_like_the_satellite() -> None:
    # satellite/client.py: int(self.cfg.silence_timeout * 1000 / FRAME_MS)
    for seconds in (0.3, 0.7, 0.8, 1.0, 1.2, 1.5, 5.0, 30.0):
        assert frame_limit(seconds) == int(seconds * 1000 / 30)
    assert frame_limit(1.2) == 40
    assert frame_limit(0.8) == 26


def test_last_voiced_frame_from_the_silence_timeout() -> None:
    # 1.2 s = 40 silent frames close the capture: 60 frames → last voiced 19.
    assert last_voiced_from_timeout(60, 1.2, 30) == 19
    assert last_voiced_from_timeout(41, 1.2, 30) == 0
    # Too short to hold a voiced frame and the silence.
    assert last_voiced_from_timeout(40, 1.2, 30) is None
    # A capture cut off by max_record_seconds did not end on the silence.
    assert last_voiced_from_timeout(frame_limit(5.0), 0.3, 5.0) is None
    assert last_voiced_from_timeout(frame_limit(5.0) - 1, 0.3, 5.0) == frame_limit(5.0) - 12
    # Garbage in, nothing claimed.
    assert last_voiced_from_timeout(60, "soon", 30) is None
    assert last_voiced_from_timeout(60, 0, 30) is None
    assert last_voiced_from_timeout(60, 1.2, "long") is None
    assert last_voiced_from_timeout(60, 1.2, None) == 19


def test_the_level_detector_fires_once_per_pause_after_speech() -> None:
    d = LevelPauseDetector()
    # Room tone before anyone speaks is not a pause.
    assert not any(d.feed(QUIET) for _ in range(20))
    fired = [d.feed(f) for f in [LOUD] * 10 + [QUIET] * 20]
    # On the 8th silent frame (240 ms), and only then.
    assert fired.index(True) == 10 + 7
    assert fired.count(True) == 1
    assert d.in_pause
    # Speech again re-arms it; the next pause fires again.
    fired = [d.feed(f) for f in [LOUD] * 3 + [QUIET] * 8]
    assert fired == [False] * 10 + [True]


def test_a_capture_that_opens_mid_word_still_finds_its_pause() -> None:
    """No quiet frame to measure the speech against until the pause
    itself: the recent frames are judged again once there is one."""
    d = LevelPauseDetector()
    fired = [d.feed(f) for f in [LOUD] * 10 + [QUIET] * 10]
    assert fired.index(True) == 10 + 7 and fired.count(True) == 1


def test_a_click_is_not_somebody_talking() -> None:
    d = LevelPauseDetector()
    assert not any(d.feed(f) for f in [QUIET] * 5 + [LOUD] + [QUIET] * 20)
    # Three frames (90 ms) is.
    d = LevelPauseDetector()
    fired = [d.feed(f) for f in [QUIET] * 5 + [LOUD] * 3 + [QUIET] * 8]
    assert fired[-1] is True


def test_the_level_detector_judges_by_the_utterances_own_level() -> None:
    """A far-field voice and its room: speech at -40 dBFS over -62 room
    tone is still speech, and the room tone after it a pause."""
    speech = np.full(480, 330, dtype=np.int16).tobytes()   # about -40 dBFS
    room = np.full(480, 26, dtype=np.int16).tobytes()      # about -62 dBFS
    d = LevelPauseDetector()
    fired = [d.feed(f) for f in [room] * 5 + [speech] * 10 + [room] * 8]
    assert fired[-1] is True and fired.count(True) == 1
    # A steady noise with nothing louder in it never looks like speech.
    d = LevelPauseDetector()
    assert not any(d.feed(room) for _ in range(100))


# ─── a Whisper the test can watch ──────────────────────────────────────────


class _WatchedWhisper:
    """Answers each call after `delay`, with `texts[i]` for the i-th call
    (the last text repeats), and records every call's audio length and how
    many calls were running at the same time."""

    def __init__(self, *texts: str, delay: float = 0.05) -> None:
        self.texts = list(texts) or ["hello"]
        self.delay = delay
        self.calls: list[int] = []
        self.running = 0
        self.max_running = 0

    async def transcribe(self, pcm: bytes) -> str:
        i = len(self.calls)
        self.calls.append(len(pcm))
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.running -= 1
        return self.texts[min(i, len(self.texts) - 1)]

    async def transcribe_wav_bytes(self, wav: bytes) -> str:
        return self.texts[0]


def _wav(pcm: bytes = b"\x00\x00" * 50, sample_rate: int = 24_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class _TTS:
    async def synthesize(self, text, *, engine=None, voice=None) -> bytes:
        return _wav()


@asynccontextmanager
async def _no_db():
    yield None


@pytest.fixture
def pipeline(monkeypatch):
    """Everything but Whisper stubbed; `seen` collects each routed turn's
    transcript and timing document."""
    seen: dict = {"routed": [], "whisper": _WatchedWhisper()}

    async def _accept(self, ctrl):
        return True

    async def _route(intent, ctx, s):
        seen["routed"].append((intent.transcript, ctx.timings.row_document()))
        return Response(
            text="Done.", session_id=None, matched_handler="timer",
            matched_path="fast", online=True,
        )

    async def _identify(pcm):
        return None

    monkeypatch.setattr(StreamSession, "_validate_pairing", _accept)
    monkeypatch.setattr("domovoi.streaming.get_whisper_client", lambda: seen["whisper"])
    monkeypatch.setattr("domovoi.streaming.session_scope", _no_db)
    monkeypatch.setattr("domovoi.streaming.route", _route)
    monkeypatch.setattr("domovoi.streaming.get_tts_client", lambda: _TTS())
    monkeypatch.setattr("domovoi.voice_identifier.identify", _identify)
    monkeypatch.setattr(settings, "speculative_stt_enabled", True)
    return seen


def _connect(ws, *, hints: bool, room: str = "kitchen", config: dict | None = None) -> dict:
    hello = {"type": "hello", "room_id": room}
    if hints:
        hello["speech_pause"] = True
    ws.send_text(json.dumps(hello))
    ready = ws.receive_json()
    assert ready["type"] == "ready", ready
    if config is not None:
        ws.send_text(json.dumps({"type": "config_status", "config": config}))
    return ready


def _send(ws, frames: list[bytes]) -> None:
    for f in frames:
        ws.send_bytes(f)


def _finish_turn(ws) -> str:
    """Read one turn's frames; returns the transcript it routed."""
    msg = ws.receive_json()
    assert msg["type"] == "transcript", msg
    text = msg["text"]
    assert ws.receive_json()["type"] == "response_start"
    ws.receive_bytes()
    assert ws.receive_json()["type"] == "response_end"
    return text


def _pause(utt: int, frames: int, last: int) -> str:
    return json.dumps({
        "type": "speech_pause", "utt": utt, "frame": frames,
        "last_voiced_frame": last, "greeting_played": False,
    })


def _end(utt: int | None = None, *, frames: int | None = None, last: int | None = None) -> str:
    msg: dict = {"type": "utterance_end", "greeting_played": False}
    if frames is not None:
        msg.update(utt=utt, frames=frames, last_voiced_frame=last,
                   exit_reason="vad_silence_after_speech")
    return json.dumps(msg)


def test_ready_lists_what_this_core_understands(pipeline) -> None:
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        ready = _connect(ws, hints=False)
    assert ready["features"] == list(CORE_FEATURES)
    assert "speech_pause" in ready["features"]


# ─── a satellite that reports its own pauses ──────────────────────────────


def test_the_copy_taken_at_the_pause_is_the_transcript_when_nothing_followed(pipeline) -> None:
    whisper = pipeline["whisper"] = _WatchedWhisper("set a timer for ten minutes", delay=0.05)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=True)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
        _send(ws, [LOUD] * 30 + [QUIET] * 8)
        ws.send_text(_pause(1, 38, 29))
        _send(ws, [QUIET] * 32)          # the rest of the 1.2 s the satellite counts
        ws.send_text(_end(1, frames=70, last=29))
        assert _finish_turn(ws) == "set a timer for ten minutes"

    # One decode, of the copy: 38 frames, not the 70 the capture ended with.
    assert whisper.calls == [38 * FRAME_BYTES]
    (transcript, doc), = pipeline["routed"]
    assert doc["stt_reused"] is True
    assert doc["speculative_decodes"] == 1
    assert doc["endpoint_silence_ms"] == 40 * FRAME_MS
    assert doc["capture_audio_ms"] == 70 * FRAME_MS
    assert doc["stt_ms"] >= 50 - 20      # the call's own time (timer slack)
    assert doc["speculative_ms"] == doc["stt_ms"]


def test_speech_after_the_copy_means_a_second_copy_at_the_next_pause(pipeline) -> None:
    """Speech resumes while the first copy is still decoding; the next
    pause waits for that decode, then copies again — and the turn uses the
    second copy. Never two decodes at once."""
    whisper = pipeline["whisper"] = _WatchedWhisper("set a timer for ten", "set a timer for ten minutes", delay=0.3)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=True)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 7}))
        _send(ws, [LOUD] * 20 + [QUIET] * 8)
        ws.send_text(_pause(7, 28, 19))
        _send(ws, [LOUD])
        ws.send_text(json.dumps({"type": "speech_resume", "utt": 7, "frame": 29}))
        _send(ws, [LOUD] * 9 + [QUIET] * 8)
        ws.send_text(_pause(7, 46, 37))
        # Real time: the satellite still has ~1 s of silence to count, and
        # the first decode finishes inside it — the second copy is taken
        # then, of the 46 frames there are.
        time.sleep(0.4)
        _send(ws, [QUIET] * 32)
        ws.send_text(_end(7, frames=78, last=37))
        assert _finish_turn(ws) == "set a timer for ten minutes"

    assert whisper.calls == [28 * FRAME_BYTES, 46 * FRAME_BYTES]
    assert whisper.max_running == 1
    (_, doc), = pipeline["routed"]
    assert doc["stt_reused"] is True and doc["speculative_decodes"] == 2


def test_speech_after_the_last_copy_throws_it_away(pipeline) -> None:
    """The satellite's last voiced frame is past the copy: the whole
    buffer is transcribed — after the copy's decode has finished."""
    whisper = pipeline["whisper"] = _WatchedWhisper("set a timer", "set a timer for ten minutes", delay=0.2)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=True)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "followup", "utt": 3}))
        _send(ws, [LOUD] * 20 + [QUIET] * 8)
        ws.send_text(_pause(3, 28, 19))
        # Speech again (a hint the core missed changes nothing: the count in
        # utterance_end is what decides).
        _send(ws, [LOUD] * 10 + [QUIET] * 40)
        ws.send_text(_end(3, frames=78, last=37))
        assert _finish_turn(ws) == "set a timer for ten minutes"

    assert whisper.calls == [28 * FRAME_BYTES, 78 * FRAME_BYTES]
    assert whisper.max_running == 1
    (_, doc), = pipeline["routed"]
    assert doc["stt_reused"] is False
    assert doc["speculative_decodes"] == 1
    # The wait covered the discarded decode finishing, then the full one.
    assert doc["stt_wait_ms"] >= doc["stt_ms"]


def test_a_new_utterance_discards_the_last_ones_copy(pipeline) -> None:
    whisper = pipeline["whisper"] = _WatchedWhisper("what time is it", "stop", delay=0.3)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=True)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
        _send(ws, [LOUD] * 20 + [QUIET] * 8)
        ws.send_text(_pause(1, 28, 19))
        # The satellite starts over (a reconnect-free restart of the
        # capture) before that decode is done.
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 2}))
        _send(ws, [LOUD] * 5 + [QUIET] * 5)
        ws.send_text(_end(2, frames=10, last=4))
        assert _finish_turn(ws) == "stop"

    # The old copy's decode ran to the end on its own (it can't be stopped)
    # and the new buffer waited for it rather than decode alongside.
    assert whisper.calls == [28 * FRAME_BYTES, 10 * FRAME_BYTES]
    assert whisper.max_running == 1
    (transcript, doc), = pipeline["routed"]
    assert transcript == "stop"
    assert "stt_reused" not in doc      # nothing speculative for THIS utterance


def test_a_hint_from_a_satellite_that_never_declared_them_is_ignored_quietly(pipeline) -> None:
    whisper = pipeline["whisper"] = _WatchedWhisper("pause", delay=0.01)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=False)
        # Outside any utterance, and from an undeclared sender: neither
        # gets the unknown-type error (a Pi takes `error` as end of turn).
        ws.send_text(_pause(1, 0, 0))
        ws.send_text(json.dumps({"type": "speech_resume", "utt": 1, "frame": 0}))
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
        _send(ws, [LOUD] * 10 + [QUIET] * 8)
        ws.send_text(_pause(1, 18, 9))
        _send(ws, [QUIET] * 32)
        ws.send_text(_end())
        assert _finish_turn(ws) == "pause"
    # No config_status and no hints: nothing to check a copy against, so
    # no copy is taken.
    assert whisper.calls == [50 * FRAME_BYTES]


def test_a_stale_or_miscounted_hint_does_not_start_a_copy(pipeline) -> None:
    whisper = pipeline["whisper"] = _WatchedWhisper("pause", delay=0.01)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=True)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 5}))
        _send(ws, [LOUD] * 10 + [QUIET] * 8)
        ws.send_text(_pause(4, 18, 9))        # an older capture's
        ws.send_text(_pause(5, 17, 9))        # not the frames we hold
        _send(ws, [QUIET] * 32)
        ws.send_text(_end(5, frames=50, last=9))
        assert _finish_turn(ws) == "pause"
    assert whisper.calls == [50 * FRAME_BYTES]


def test_a_frame_count_that_disagrees_at_the_end_is_not_trusted(pipeline) -> None:
    whisper = pipeline["whisper"] = _WatchedWhisper("pause", delay=0.01)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=True)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
        _send(ws, [LOUD] * 10 + [QUIET] * 8)
        ws.send_text(_pause(1, 18, 9))
        _send(ws, [QUIET] * 32)
        time.sleep(0.1)
        ws.send_text(_end(1, frames=49, last=9))   # we hold 50
        assert _finish_turn(ws) == "pause"
    assert whisper.calls == [18 * FRAME_BYTES, 50 * FRAME_BYTES]
    (_, doc), = pipeline["routed"]
    assert doc["stt_reused"] is False
    assert "endpoint_silence_ms" not in doc


def test_speculation_off_transcribes_after_the_end_only(pipeline, monkeypatch) -> None:
    monkeypatch.setattr(settings, "speculative_stt_enabled", False)
    whisper = pipeline["whisper"] = _WatchedWhisper("pause", delay=0.01)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=True)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
        _send(ws, [LOUD] * 10 + [QUIET] * 8)
        ws.send_text(_pause(1, 18, 9))
        _send(ws, [QUIET] * 32)
        ws.send_text(_end(1, frames=50, last=9))
        assert _finish_turn(ws) == "pause"
    assert whisper.calls == [50 * FRAME_BYTES]
    (_, doc), = pipeline["routed"]
    assert "stt_reused" not in doc
    # The endpoint is still known exactly, so the felt latency is recorded.
    assert doc["endpoint_silence_ms"] == 40 * FRAME_MS


def test_a_wake_word_training_clip_is_never_transcribed(pipeline, monkeypatch) -> None:
    whisper = pipeline["whisper"] = _WatchedWhisper("hey domovoi", delay=0.01)
    saved: list[int] = []

    async def _save(self, pcm):
        saved.append(len(pcm))

    monkeypatch.setattr(StreamSession, "_save_wake_clip", _save)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=True)
        sess = app.state.active_sessions["kitchen"]
        sess.wake_recording = object()   # armed, as the dashboard would
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_clip", "utt": 1}))
        _send(ws, [LOUD] * 10 + [QUIET] * 8)
        ws.send_text(_pause(1, 18, 9))
        ws.send_text(_end(1, frames=18, last=9))
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json() == {"type": "pong"}
        sess.wake_recording = None
    assert whisper.calls == []
    assert saved == [18 * FRAME_BYTES]


def test_at_most_a_few_copies_per_utterance(pipeline) -> None:
    whisper = pipeline["whisper"] = _WatchedWhisper("one", delay=0.01)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=True)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
        frames = 0
        for _ in range(SPECULATIVE_MAX_DECODES + 2):
            _send(ws, [LOUD] * 5 + [QUIET] * 8)
            frames += 13
            ws.send_text(_pause(1, frames, frames - 9))
            time.sleep(0.05)     # let each decode finish before the next pause
            _send(ws, [LOUD])
            ws.send_text(json.dumps({"type": "speech_resume", "utt": 1, "frame": frames + 1}))
            frames += 1
        _send(ws, [QUIET] * 40)
        frames += 40
        ws.send_text(_end(1, frames=frames, last=frames - 41))
        _finish_turn(ws)
    # The cap, then the whole buffer once the capture ended.
    assert len(whisper.calls) == SPECULATIVE_MAX_DECODES + 1
    assert whisper.calls[-1] == frames * FRAME_BYTES


def test_noisy_capture_discards_the_copy(pipeline) -> None:
    whisper = pipeline["whisper"] = _WatchedWhisper("pause", delay=0.2)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=True)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
        _send(ws, [LOUD] * 10 + [QUIET] * 8)
        ws.send_text(_pause(1, 18, 9))
        ws.send_text(json.dumps({"type": "noisy_capture"}))
        start = ws.receive_json()
        assert start["type"] == "response_start" and start["matched_handler"] == "noisy_capture"
        ws.receive_bytes()
        assert ws.receive_json()["type"] == "response_end"
        sess = app.state.active_sessions["kitchen"]
        assert sess._spec is None and not sess._spec_on
    assert pipeline["routed"] == []


# ─── a satellite that doesn't report its pauses ───────────────────────────


OLD_CONFIG = {"listen.silence_timeout": 0.3, "listen.max_record_seconds": 5.0}   # 10 frames


def test_an_old_satellite_gets_it_too_from_its_reported_silence_timeout(pipeline) -> None:
    whisper = pipeline["whisper"] = _WatchedWhisper("volume up", delay=0.05)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=False, config=OLD_CONFIG)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
        _send(ws, [QUIET] * 3 + [LOUD] * 20 + [QUIET] * 10)
        ws.send_text(_end())                 # an old client: no counts at all
        assert _finish_turn(ws) == "volume up"
    # The core found the pause on its own at the 8th silent frame.
    assert whisper.calls == [(3 + 20 + 8) * FRAME_BYTES]
    (_, doc), = pipeline["routed"]
    assert doc["stt_reused"] is True
    assert doc["endpoint_silence_ms"] == 10 * FRAME_MS


def test_an_old_satellite_that_spoke_after_the_copy_gets_the_whole_buffer(pipeline) -> None:
    whisper = pipeline["whisper"] = _WatchedWhisper("volume", "volume up", delay=0.2)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=False, config=OLD_CONFIG)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
        # A pause long enough for the core (8 frames), too short for the
        # satellite (10) — then more speech.
        _send(ws, [LOUD] * 20 + [QUIET] * 9 + [LOUD] * 5 + [QUIET] * 10)
        ws.send_text(_end())
        assert _finish_turn(ws) == "volume up"
    assert whisper.calls == [28 * FRAME_BYTES, 44 * FRAME_BYTES]
    assert whisper.max_running == 1
    (_, doc), = pipeline["routed"]
    assert doc["stt_reused"] is False


def test_an_old_satellites_capture_that_hit_its_cap_is_never_reused(pipeline) -> None:
    whisper = pipeline["whisper"] = _WatchedWhisper("the tv", delay=0.01)
    cap = int(5.0 * 1000 / 30)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=False, config=OLD_CONFIG)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
        _send(ws, [LOUD] * 20 + [QUIET] * 8)
        time.sleep(0.05)
        _send(ws, [LOUD] * (cap - 28))
        ws.send_text(_end())
        _finish_turn(ws)
    assert whisper.calls == [28 * FRAME_BYTES, cap * FRAME_BYTES]


def test_without_a_reported_timeout_an_old_satellite_is_left_alone(pipeline) -> None:
    whisper = pipeline["whisper"] = _WatchedWhisper("pause", delay=0.01)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=False)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
        _send(ws, [LOUD] * 20 + [QUIET] * 40)
        ws.send_text(_end())
        _finish_turn(ws)
    assert whisper.calls == [60 * FRAME_BYTES]
    (_, doc), = pipeline["routed"]
    assert "stt_reused" not in doc and "endpoint_silence_ms" not in doc
