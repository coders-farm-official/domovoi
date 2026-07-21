"""Protocol-level tests for the /v1/stream/{room_id} WebSocket endpoint.

These don't need a real DB — `route` is patched to return a fixed response.
The barge-in test stalls inside whisper so the cancel arrives mid-task.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import wave
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from domovoi.main import app
from domovoi.models import Response
from domovoi.streaming import _resample_pcm


def test_resample_pcm_converts_rate_and_is_noop_when_equal() -> None:
    """A sentence rendered by a fallback engine at a different rate is
    resampled to the response's announced rate so it doesn't play fast/slow.
    Output sample count scales by the rate ratio; equal rates / empty are
    no-ops."""
    import numpy as np

    src = (
        np.sin(np.linspace(0, 2 * np.pi * 440, 22050)).astype(np.float32) * 10000
    ).astype(np.int16).tobytes()
    out = _resample_pcm(src, 22050, 24000)
    assert abs(len(out) // 2 - 24000) <= 1  # scaled by 24000/22050
    # Down-convert too.
    down = _resample_pcm(src, 24000, 16000)
    assert abs(len(down) // 2 - int(22050 * 16000 / 24000)) <= 1
    # No-ops.
    assert _resample_pcm(src, 24000, 24000) == src
    assert _resample_pcm(b"", 22050, 24000) == b""


@asynccontextmanager
async def _fake_session_scope():
    """No-op replacement so streaming tests don't need a live Postgres."""
    yield None


def _make_wav(pcm: bytes = b"", sample_rate: int = 24_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class _FakeWhisper:
    def __init__(self, transcript: str = "hello") -> None:
        self._transcript = transcript

    async def transcribe(self, pcm: bytes) -> str:
        return self._transcript

    async def transcribe_wav_bytes(self, wav: bytes) -> str:
        return self._transcript


class _SlowWhisper:
    """Hangs in transcribe so tests can interrupt mid-flight."""

    async def transcribe(self, pcm: bytes) -> str:
        await asyncio.sleep(5)
        return "should never get here"

    async def transcribe_wav_bytes(self, wav: bytes) -> str:
        await asyncio.sleep(5)
        return "should never get here"


class _FakeTTS:
    def __init__(self, pcm: bytes = b"\x00\x00" * 50, sample_rate: int = 24_000) -> None:
        self._wav = _make_wav(pcm, sample_rate)

    async def synthesize(
        self, text: str, *, engine: str | None = None, voice: str | None = None
    ) -> bytes:
        return self._wav


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    whisper,
    tts=None,
    response: Response | None = None,
) -> None:
    monkeypatch.setattr("domovoi.streaming.get_whisper_client", lambda: whisper)
    monkeypatch.setattr("domovoi.streaming.session_scope", _fake_session_scope)
    if tts is not None:
        monkeypatch.setattr("domovoi.streaming.get_tts_client", lambda: tts)
    if response is not None:
        async def fake_route(intent, ctx, session):
            return response
        monkeypatch.setattr("domovoi.streaming.route", fake_route)


def test_stream_happy_path_emits_full_frame_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Response(
        text="Timer set for 5 minutes.",
        session_id=None,
        matched_handler="timer",
        matched_path="fast",
        online=True,
    )
    pcm = b"\x12\x34" * 200  # arbitrary non-empty PCM body
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper("set a timer for 5 minutes"),
        tts=_FakeTTS(pcm=pcm, sample_rate=24_000),
        response=response,
    )

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/kitchen") as ws:
            ready = ws.receive_json()
            assert ready["type"] == "ready"
            assert ready["protocol_version"] == "0.1"
            assert ready["room_id"] == "kitchen"
            assert ready["audio_sample_rate_in"] == 16_000

            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 1024)
            ws.send_text(json.dumps({"type": "utterance_end"}))

            transcript = ws.receive_json()
            assert transcript == {"type": "transcript", "text": "set a timer for 5 minutes"}

            start = ws.receive_json()
            assert start["type"] == "response_start"
            assert start["text"] == "Timer set for 5 minutes."
            assert start["matched_handler"] == "timer"
            assert start["matched_path"] == "fast"
            assert start["audio_sample_rate"] == 24_000

            # Single sentence, single chunk (200*2 = 400 bytes < 16 KB chunk size).
            audio = ws.receive_bytes()
            assert audio == pcm

            end = ws.receive_json()
            assert end == {"type": "response_end", "interrupted": False, "expect_followup": False}


def test_stream_ping_pong(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pipeline(monkeypatch, whisper=_FakeWhisper())

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/office") as ws:
            ws.receive_json()  # ready
            ws.send_text(json.dumps({"type": "ping"}))
            pong = ws.receive_json()
            assert pong == {"type": "pong"}


def test_stream_unknown_control_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pipeline(monkeypatch, whisper=_FakeWhisper())

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/office") as ws:
            ws.receive_json()  # ready
            ws.send_text(json.dumps({"type": "totally_made_up"}))
            err = ws.receive_json()
            assert err["type"] == "error"
            assert "totally_made_up" in err["message"]


def test_stream_invalid_json_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pipeline(monkeypatch, whisper=_FakeWhisper())

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/office") as ws:
            ws.receive_json()  # ready
            ws.send_text("{not json")
            err = ws.receive_json()
            assert err == {"type": "error", "message": "invalid json"}


def test_stream_barge_in_cancels_in_flight_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pipeline(
        monkeypatch,
        whisper=_SlowWhisper(),
        tts=_FakeTTS(),
        response=Response(text="ignored", matched_handler=None, matched_path=None, online=True),
    )

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/kitchen") as ws:
            ws.receive_json()  # ready

            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 1024)
            ws.send_text(json.dumps({"type": "utterance_end"}))

            # Give the response task a moment to spawn and reach the slow await.
            time.sleep(0.1)
            ws.send_text(json.dumps({"type": "barge_in"}))

            end = ws.receive_json()
            assert end == {"type": "response_end", "interrupted": True, "expect_followup": False}


def test_stream_new_utterance_during_response_cancels_previous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(
        monkeypatch,
        whisper=_SlowWhisper(),
        tts=_FakeTTS(),
        response=Response(text="ignored", matched_handler=None, matched_path=None, online=True),
    )

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/kitchen") as ws:
            ws.receive_json()  # ready

            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 1024)
            ws.send_text(json.dumps({"type": "utterance_end"}))

            time.sleep(0.1)
            # Client decides to barge with a fresh utterance directly (no explicit barge_in).
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "barge_in"}))

            end = ws.receive_json()
            assert end == {"type": "response_end", "interrupted": True, "expect_followup": False}


# ─── Music-resume coordination ─────────────────────────────


def test_stream_resume_music_when_response_has_no_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a room has resumable music recorded and the response carries no
    music_action, the core must emit a music_start with the saved
    URL after response_end. Without this, "what time is it" said while
    music is playing kills the music permanently."""
    response = Response(
        text="It's 3:47 PM.",
        matched_handler="clock",
        matched_path="fast",
        online=True,
    )
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper("what time is it"),
        tts=_FakeTTS(),
        response=response,
    )

    with TestClient(app) as client:
        # Pre-seed: the kitchen had music playing previously (recorded by
        # an earlier music_action="start" response in real use).
        app.state.resumable_music["kitchen"] = "http://test.local:8001"
        with client.websocket_connect("/v1/stream/kitchen") as ws:
            ws.receive_json()  # ready
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 1024)
            ws.send_text(json.dumps({"type": "utterance_end"}))

            ws.receive_json()  # transcript
            ws.receive_json()  # response_start
            ws.receive_bytes()  # tts audio
            end = ws.receive_json()
            assert end["type"] == "response_end"

            resume = ws.receive_json()
            assert resume == {
                "type": "music_start",
                "stream_url": "http://test.local:8001",
            }


def test_stream_records_resumable_url_on_music_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response with music_action="start" must update resumable_music for
    that room so subsequent non-music turns auto-resume the new track."""
    response = Response(
        text="Playing creep.",
        matched_handler="music",
        matched_path="fast",
        online=True,
        music_action="start",
        music_stream_url="http://test.local:8002",
    )
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper("play creep"),
        tts=_FakeTTS(),
        response=response,
    )

    with TestClient(app) as client:
        app.state.resumable_music.clear()  # start clean
        with client.websocket_connect("/v1/stream/garage") as ws:
            ws.receive_json()  # ready
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 1024)
            ws.send_text(json.dumps({"type": "utterance_end"}))

            ws.receive_json()  # transcript
            ws.receive_json()  # response_start
            ws.receive_bytes()  # tts audio
            ws.receive_json()  # response_end
            music = ws.receive_json()
            assert music == {
                "type": "music_start",
                "stream_url": "http://test.local:8002",
            }

        assert app.state.resumable_music.get("garage") == "http://test.local:8002"


def test_stream_clears_resumable_url_on_music_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response with music_action="stop" must clear resumable_music so a
    later "what time is it" doesn't auto-resume after an explicit stop."""
    response = Response(
        text="Stopped.",
        matched_handler="music",
        matched_path="fast",
        online=True,
        music_action="stop",
    )
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper("stop"),
        tts=_FakeTTS(),
        response=response,
    )

    with TestClient(app) as client:
        app.state.resumable_music["garage"] = "http://test.local:8002"
        with client.websocket_connect("/v1/stream/garage") as ws:
            ws.receive_json()  # ready
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 1024)
            ws.send_text(json.dumps({"type": "utterance_end"}))

            ws.receive_json()  # transcript
            ws.receive_json()  # response_start
            ws.receive_bytes()  # tts audio
            ws.receive_json()  # response_end
            stop = ws.receive_json()
            assert stop == {"type": "music_stop"}

        assert "garage" not in app.state.resumable_music


def test_stream_suppresses_music_resume_when_expect_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a response carries `expect_followup=True`, the core
    must NOT emit a music_start (neither explicit nor auto-resume) —
    respawning mpg123 while the satellite is in its wake-word-free
    capture window saturates the Pi's mic and traps the user in a
    noisy_capture loop. The 2026-05-08 12:35 incident: 'add that to
    my library' → 'should I add it too?' → music auto-resumed → 4 noisy
    retries before the user gave up.

    The resumable URL is still recorded so that the followup turn's
    response (which won't carry expect_followup) auto-resumes normally.
    """
    response = Response(
        text="Should I add it too?",
        matched_handler="music",
        matched_path="fast",
        online=True,
        expect_followup=True,
    )
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper("add that to my library"),
        tts=_FakeTTS(),
        response=response,
    )

    with TestClient(app) as client:
        # Pre-seed: room had an external stream playing.
        app.state.resumable_music["kitchen"] = "http://test.local:8003"
        with client.websocket_connect("/v1/stream/kitchen") as ws:
            ws.receive_json()  # ready
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 1024)
            ws.send_text(json.dumps({"type": "utterance_end"}))

            ws.receive_json()  # transcript
            ws.receive_json()  # response_start
            ws.receive_bytes()  # tts audio
            end = ws.receive_json()
            assert end == {
                "type": "response_end",
                "interrupted": False,
                "expect_followup": True,
            }
            # No music_start should arrive. Use a ping to flush — the
            # next inbound has to be the pong, not a music_start.
            ws.send_text(json.dumps({"type": "ping"}))
            after = ws.receive_json()
            assert after == {"type": "pong"}

        # Resumable URL is preserved so the followup turn resumes music.
        assert app.state.resumable_music.get("kitchen") == "http://test.local:8003"


def test_stream_records_resumable_but_skips_start_when_expect_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a handler somehow returns both `music_action="start"` AND
    `expect_followup=True` (a weird combo, but defensible): record the
    URL in resumable_music for the eventual followup-turn auto-resume,
    but DO NOT emit music_start now. Same rationale as the no-action
    case — listening for the user's reply with music blasting
    saturates the mic."""
    # Single sentence to keep the wire-frame count predictable —
    # multi-sentence text would emit one audio chunk per sentence
    # (see _split_sentences) and complicate the receive sequence below.
    response = Response(
        text="Playing it should I queue another after this one",
        matched_handler="music",
        matched_path="fast",
        online=True,
        music_action="start",
        music_stream_url="http://test.local:8004",
        expect_followup=True,
    )
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper("play creep"),
        tts=_FakeTTS(),
        response=response,
    )

    with TestClient(app) as client:
        app.state.resumable_music.clear()
        with client.websocket_connect("/v1/stream/garage") as ws:
            ws.receive_json()  # ready
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 1024)
            ws.send_text(json.dumps({"type": "utterance_end"}))

            ws.receive_json()  # transcript
            ws.receive_json()  # response_start
            ws.receive_bytes()  # tts audio
            ws.receive_json()  # response_end
            # No music_start should arrive. Ping to flush.
            ws.send_text(json.dumps({"type": "ping"}))
            after = ws.receive_json()
            assert after == {"type": "pong"}

        assert app.state.resumable_music.get("garage") == "http://test.local:8004"


def test_response_task_failure_emits_error_then_response_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the response task throws (e.g. DB unreachable mid-turn),
    the core must emit BOTH an `error` frame AND a terminal
    `response_end` so the satellite's mic thread unblocks. Without
    the response_end pairing, the Pi hangs forever waiting for the
    end-of-turn signal and stays out of wake-word listen.

    Regression test for the 2026-05-08 19:09 incident: docker postgres
    went down, the core's response task threw, only `error` was sent, sat
    was stuck.
    """
    async def boom_route(intent, ctx, session):
        raise RuntimeError("simulated DB unreachable")

    monkeypatch.setattr("domovoi.streaming.get_whisper_client", lambda: _FakeWhisper("anything"))
    monkeypatch.setattr("domovoi.streaming.session_scope", _fake_session_scope)
    monkeypatch.setattr("domovoi.streaming.get_tts_client", lambda: _FakeTTS())
    monkeypatch.setattr("domovoi.streaming.route", boom_route)

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/kitchen") as ws:
            ws.receive_json()  # ready
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 1024)
            ws.send_text(json.dumps({"type": "utterance_end"}))

            ws.receive_json()  # transcript
            err = ws.receive_json()
            assert err["type"] == "error"
            assert "simulated DB unreachable" in err["message"]
            # The new behavior: response_end follows the error so the
            # protocol stays balanced — every utterance gets terminated.
            end = ws.receive_json()
            assert end == {
                "type": "response_end",
                "interrupted": True,
                "expect_followup": False,
            }


def test_stream_no_resume_when_resumable_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """If no music was recorded for the room, a no-action response must NOT
    emit a music_start — that would tell the Pi to spawn mpg123 against
    nothing."""
    response = Response(
        text="It's 3:47 PM.",
        matched_handler="clock",
        matched_path="fast",
        online=True,
    )
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper("what time is it"),
        tts=_FakeTTS(),
        response=response,
    )

    with TestClient(app) as client:
        app.state.resumable_music.clear()
        with client.websocket_connect("/v1/stream/livingroom") as ws:
            ws.receive_json()  # ready
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 1024)
            ws.send_text(json.dumps({"type": "utterance_end"}))

            ws.receive_json()  # transcript
            ws.receive_json()  # response_start
            ws.receive_bytes()  # tts audio
            end = ws.receive_json()
            assert end == {"type": "response_end", "interrupted": False, "expect_followup": False}
            # No further frame should arrive. Send a ping to flush; the
            # next server-side frame has to be the pong, not a music_start.
            ws.send_text(json.dumps({"type": "ping"}))
            after = ws.receive_json()
            assert after == {"type": "pong"}


# ─── Noisy-capture apology (Pi-side noise gate auto-tune) ────────────────


def test_noisy_capture_triggers_static_apology(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the Pi sends a `noisy_capture` control frame (its noise gate
    detected the room got too loud to capture cleanly), the core
    must respond with a stock TTS apology — no router, no Whisper,
    no intents_log write — and a normal response_start / audio /
    response_end cycle so the Pi handles it like any other reply."""
    pcm = b"\x12\x34" * 100
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper(),
        tts=_FakeTTS(pcm=pcm, sample_rate=24_000),
    )

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/kitchen") as ws:
            ws.receive_json()  # ready
            ws.send_text(json.dumps({"type": "noisy_capture"}))

            start = ws.receive_json()
            assert start["type"] == "response_start"
            assert start["matched_handler"] == "noisy_capture"
            assert start["matched_path"] == "system"
            # Apology text is canned and starts with the same lead-in
            # the user will recognize after they've heard it once.
            assert "trouble hearing" in start["text"].lower()

            audio = ws.receive_bytes()
            assert audio == pcm

            end = ws.receive_json()
            # `expect_followup` is intentionally false — the user needs
            # a beat to fix the audio environment before retrying.
            assert end == {
                "type": "response_end",
                "interrupted": False,
                "expect_followup": False,
            }


# ─── Follow-up signal (skip wake-word for the bot's own questions) ────────


def test_response_end_carries_expect_followup_when_response_set_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handler that sets ``Response.expect_followup=True`` (e.g.,
    VoiceProfileHandler asking "did I get that right?") must surface
    that flag in the response_end frame so the Pi can skip its wake-word
    gate for the user's reply."""
    # Single-sentence text so there's only one audio chunk before
    # response_end — `_split_sentences` splits on ".!?\s+", and the
    # `?` here is followed by end-of-string, so it stays one piece.
    response = Response(
        text="Did I get that right?",
        matched_handler="voice_profile",
        matched_path="fast",
        online=True,
        expect_followup=True,
    )
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper("i'm sarah"),
        tts=_FakeTTS(),
        response=response,
    )

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/kitchen") as ws:
            ws.receive_json()  # ready
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 1024)
            ws.send_text(json.dumps({"type": "utterance_end"}))

            ws.receive_json()  # transcript
            ws.receive_json()  # response_start
            ws.receive_bytes()  # tts audio
            end = ws.receive_json()
            assert end == {
                "type": "response_end",
                "interrupted": False,
                "expect_followup": True,
            }


def test_response_end_drops_expect_followup_on_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the bot's question got cut off (interrupted=True), the
    ``expect_followup`` flag should NOT propagate — the user wasn't
    given a chance to hear the question, so we shouldn't expect them to
    answer."""
    _patch_pipeline(
        monkeypatch,
        whisper=_SlowWhisper(),
        tts=_FakeTTS(),
        response=Response(
            text="Did I get that right?",
            matched_handler="voice_profile",
            matched_path="fast",
            online=True,
            expect_followup=True,
        ),
    )

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/kitchen") as ws:
            ws.receive_json()  # ready
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 1024)
            ws.send_text(json.dumps({"type": "utterance_end"}))

            time.sleep(0.1)
            ws.send_text(json.dumps({"type": "barge_in"}))

            end = ws.receive_json()
            assert end == {
                "type": "response_end",
                "interrupted": True,
                "expect_followup": False,
            }


# ─── Active-Pi tracking + intercom fan-out ────────────────────────────────


def test_active_sessions_register_and_deregister(monkeypatch: pytest.MonkeyPatch) -> None:
    """On WebSocket accept the StreamSession registers itself in
    ``app.state.active_sessions`` so IntercomHandler can reach it; on
    disconnect the entry must be removed (otherwise stale references
    would receive future broadcasts and silently fail)."""
    _patch_pipeline(monkeypatch, whisper=_FakeWhisper())

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/kitchen") as ws:
            ws.receive_json()  # ready
            assert "kitchen" in app.state.active_sessions
            assert app.state.active_sessions["kitchen"].room_id == "kitchen"
        # Connection closed: the slot should have cleared.
        assert "kitchen" not in app.state.active_sessions


def test_announce_triggers_fan_out_to_target_rooms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response with announce_to_rooms populated must fan out the
    announcement audio to each target's WebSocket. The originating room
    is excluded from the fan-out (it already heard the response)."""
    response = Response(
        text="Broadcasting to the house.",
        matched_handler="intercom",
        matched_path="fast",
        online=True,
        announce_to_rooms=["kitchen", "garage"],
        announce_text="dinner is ready",
    )
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper("announce dinner is ready"),
        tts=_FakeTTS(),
        response=response,
    )

    with TestClient(app) as client:
        # Open a "garage" WS first so it shows up in active_sessions when
        # the kitchen WS triggers the fan-out. Two contexts side by side
        # in the same TestClient share app.state.
        with client.websocket_connect("/v1/stream/garage") as garage_ws:
            garage_ws.receive_json()  # ready
            assert "garage" in app.state.active_sessions

            with client.websocket_connect("/v1/stream/kitchen") as kitchen_ws:
                kitchen_ws.receive_json()  # ready
                kitchen_ws.send_text(
                    json.dumps({"type": "utterance_start", "trigger": "wake_word"})
                )
                kitchen_ws.send_bytes(b"\x00" * 1024)
                kitchen_ws.send_text(json.dumps({"type": "utterance_end"}))

                # Kitchen sees its own response cycle.
                kitchen_ws.receive_json()  # transcript
                kitchen_ws.receive_json()  # response_start
                kitchen_ws.receive_bytes()  # tts audio
                kitchen_ws.receive_json()  # response_end

                # Garage receives the fanned-out announcement as a normal
                # response_start / audio / response_end cycle.
                start = garage_ws.receive_json()
                assert start["type"] == "response_start"
                assert start["matched_handler"] == "intercom"
                assert start["matched_path"] == "intercom_broadcast"
                assert start["text"] == "dinner is ready"
                garage_ws.receive_bytes()  # audio
                end = garage_ws.receive_json()
                assert end == {"type": "response_end", "interrupted": False, "expect_followup": False}


def test_announce_skips_originating_room(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the broadcast list happens to include the requesting Pi (e.g.
    "announce to the house" from inside the house), the originating
    session must NOT receive an announcement on top of its own response —
    that would clip and double-play."""
    response = Response(
        text="Broadcasting.",
        matched_handler="intercom",
        matched_path="fast",
        online=True,
        announce_to_rooms=["kitchen"],
        announce_text="house broadcast text",
    )
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper("announce broadcast"),
        tts=_FakeTTS(),
        response=response,
    )

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/kitchen") as ws:
            ws.receive_json()  # ready
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 1024)
            ws.send_text(json.dumps({"type": "utterance_end"}))

            ws.receive_json()  # transcript
            ws.receive_json()  # response_start (original)
            ws.receive_bytes()  # tts audio
            end = ws.receive_json()
            assert end == {"type": "response_end", "interrupted": False, "expect_followup": False}

            # No second response_start should arrive — confirm by sending
            # ping and verifying the next inbound is the pong, not an
            # echoed announcement.
            ws.send_text(json.dumps({"type": "ping"}))
            after = ws.receive_json()
            assert after == {"type": "pong"}


def test_announce_to_offline_room_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a target room isn't currently connected, the fan-out skips it
    quietly. The requester still gets its normal response."""
    response = Response(
        text="Broadcasting.",
        matched_handler="intercom",
        matched_path="fast",
        online=True,
        announce_to_rooms=["bedroom"],  # bedroom never connects
        announce_text="hey bedroom",
    )
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper("announce in the bedroom hey"),
        tts=_FakeTTS(),
        response=response,
    )

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/kitchen") as ws:
            ws.receive_json()  # ready
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 1024)
            ws.send_text(json.dumps({"type": "utterance_end"}))

            ws.receive_json()  # transcript
            ws.receive_json()  # response_start
            ws.receive_bytes()  # tts audio
            end = ws.receive_json()
            assert end == {"type": "response_end", "interrupted": False, "expect_followup": False}

            ws.send_text(json.dumps({"type": "ping"}))
            assert ws.receive_json() == {"type": "pong"}


def test_stream_audio_outside_utterance_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """PCM frames sent outside utterance_start/end must not feed any later utterance."""
    captured: list[int] = []

    class _CapturingWhisper:
        async def transcribe(self, pcm: bytes) -> str:
            captured.append(len(pcm))
            return "x"

        async def transcribe_wav_bytes(self, wav: bytes) -> str:
            captured.append(len(wav))
            return "x"

    _patch_pipeline(
        monkeypatch,
        whisper=_CapturingWhisper(),
        tts=_FakeTTS(),
        response=Response(text="x", matched_handler=None, matched_path=None, online=True),
    )

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/kitchen") as ws:
            ws.receive_json()  # ready

            ws.send_bytes(b"\xff" * 4096)  # before any utterance — should be discarded
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            ws.send_bytes(b"\x00" * 256)
            ws.send_text(json.dumps({"type": "utterance_end"}))

            ws.receive_json()  # transcript
            ws.receive_json()  # response_start
            # Drain remaining frames cleanly.
            while True:
                frame = ws.receive()
                if frame.get("type") == "websocket.send" and frame.get("text"):
                    payload = json.loads(frame["text"])
                    if payload.get("type") == "response_end":
                        break

    assert captured == [256], "leading 4096 PCM bytes leaked into the utterance"


# ─── music_ready handshake (prepare/resume) ────────────────────────────────


class _ResumeTrackingMPDStub:
    """Bare-bones MPD stub that records resume() calls.

    Inherits nothing so the test doesn't drag in MPDStubClient's stateful
    play/pause tracking — the handshake only cares about whether resume
    fires, when, and how many times.
    """

    def __init__(self) -> None:
        self.resume_calls = 0

    async def resume(self) -> None:
        self.resume_calls += 1


def test_music_ready_resumes_mpd_and_cancels_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Pi sends `music_ready` after its mpg123 has primed; the
    core must call `mpd.resume()` and drop the pending entry.
    Without this, MPD stays paused on the queued track until the
    fallback timer fires several seconds later — and the user hears
    silence in the meantime."""
    from domovoi.clients import mpd as mpd_module

    response = Response(
        text="Playing creep.",
        matched_handler="music",
        matched_path="fast",
        online=True,
        music_action="start",
        music_stream_url="http://test.local:8005",
    )
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper("play creep"),
        tts=_FakeTTS(),
        response=response,
    )

    tracker = _ResumeTrackingMPDStub()
    mpd_module._clients["kitchen"] = tracker  # type: ignore[assignment]

    try:
        with TestClient(app) as client:
            app.state.resumable_music.clear()
            app.state.pending_music_start.clear()
            with client.websocket_connect("/v1/stream/kitchen") as ws:
                ws.receive_json()  # ready
                ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
                ws.send_bytes(b"\x00" * 1024)
                ws.send_text(json.dumps({"type": "utterance_end"}))

                ws.receive_json()  # transcript
                ws.receive_json()  # response_start
                ws.receive_bytes()  # tts audio
                ws.receive_json()  # response_end
                music = ws.receive_json()
                assert music["type"] == "music_start"

                # Pending entry registered, fallback timer armed but not fired.
                pending = app.state.pending_music_start.get("kitchen")
                assert pending is not None
                assert pending["url"] == "http://test.local:8005"
                assert tracker.resume_calls == 0

                # Pi acks — should cancel the timer and fire resume.
                ws.send_text(json.dumps({"type": "music_ready"}))
                # Give the receive loop a tick to handle the frame.
                ws.send_text(json.dumps({"type": "ping"}))
                pong = ws.receive_json()
                assert pong == {"type": "pong"}

                assert tracker.resume_calls == 1
                assert "kitchen" not in app.state.pending_music_start
    finally:
        mpd_module._clients.pop("kitchen", None)


def test_music_prepare_falls_back_to_resume_when_no_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old satellites that don't speak music_ready (or any Pi that drops
    its WiFi between music_start and music_ready) must still get music —
    the fallback timer resumes MPD unconditionally after
    `music_prepare_fallback_sec`."""
    from domovoi.clients import mpd as mpd_module

    # Squeeze the fallback to something quick so the test isn't a 5s wait.
    monkeypatch.setattr(
        "domovoi.streaming.settings.music_prepare_fallback_sec", 0.2
    )

    response = Response(
        text="Playing creep.",
        matched_handler="music",
        matched_path="fast",
        online=True,
        music_action="start",
        music_stream_url="http://test.local:8006",
    )
    _patch_pipeline(
        monkeypatch,
        whisper=_FakeWhisper("play creep"),
        tts=_FakeTTS(),
        response=response,
    )

    tracker = _ResumeTrackingMPDStub()
    mpd_module._clients["garage"] = tracker  # type: ignore[assignment]

    try:
        with TestClient(app) as client:
            app.state.resumable_music.clear()
            app.state.pending_music_start.clear()
            with client.websocket_connect("/v1/stream/garage") as ws:
                ws.receive_json()  # ready
                ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
                ws.send_bytes(b"\x00" * 1024)
                ws.send_text(json.dumps({"type": "utterance_end"}))

                ws.receive_json()  # transcript
                ws.receive_json()  # response_start
                ws.receive_bytes()  # tts audio
                ws.receive_json()  # response_end
                ws.receive_json()  # music_start

                # Pretend the Pi never replies — wait past the fallback.
                time.sleep(0.5)

                assert tracker.resume_calls == 1
                # Entry should be cleared by the timer's finally block.
                assert "garage" not in app.state.pending_music_start
    finally:
        mpd_module._clients.pop("garage", None)


def test_music_stop_cancels_pending_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stop request mid-handshake (user said "stop" before mpg123 had
    a chance to prime) must cancel the fallback timer so it doesn't
    fire later and resume the same track the user just stopped."""
    from domovoi.clients import mpd as mpd_module

    monkeypatch.setattr(
        "domovoi.streaming.settings.music_prepare_fallback_sec", 0.2
    )

    # First turn: start playing.
    play_response = Response(
        text="Playing creep.",
        matched_handler="music",
        matched_path="fast",
        online=True,
        music_action="start",
        music_stream_url="http://test.local:8007",
    )
    # Second turn: stop.
    stop_response = Response(
        text="Stopped.",
        matched_handler="music",
        matched_path="fast",
        online=True,
        music_action="stop",
    )
    queue = [play_response, stop_response]

    async def route_seq(intent, ctx, session):
        return queue.pop(0)

    monkeypatch.setattr("domovoi.streaming.get_whisper_client", lambda: _FakeWhisper())
    monkeypatch.setattr("domovoi.streaming.session_scope", _fake_session_scope)
    monkeypatch.setattr("domovoi.streaming.get_tts_client", lambda: _FakeTTS())
    monkeypatch.setattr("domovoi.streaming.route", route_seq)

    tracker = _ResumeTrackingMPDStub()
    mpd_module._clients["office"] = tracker  # type: ignore[assignment]

    try:
        with TestClient(app) as client:
            app.state.resumable_music.clear()
            app.state.pending_music_start.clear()
            with client.websocket_connect("/v1/stream/office") as ws:
                ws.receive_json()  # ready

                # Turn 1: start
                ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
                ws.send_bytes(b"\x00" * 1024)
                ws.send_text(json.dumps({"type": "utterance_end"}))
                ws.receive_json()  # transcript
                ws.receive_json()  # response_start
                ws.receive_bytes()  # tts
                ws.receive_json()  # response_end
                ws.receive_json()  # music_start
                assert "office" in app.state.pending_music_start

                # Turn 2: stop — should cancel pending and emit music_stop
                ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
                ws.send_bytes(b"\x00" * 1024)
                ws.send_text(json.dumps({"type": "utterance_end"}))
                ws.receive_json()  # transcript
                ws.receive_json()  # response_start
                ws.receive_bytes()  # tts
                ws.receive_json()  # response_end
                stop = ws.receive_json()
                assert stop == {"type": "music_stop"}
                assert "office" not in app.state.pending_music_start

                # Sleep past where the fallback WOULD have fired. resume
                # must NOT be called — the user asked to stop.
                time.sleep(0.5)
                assert tracker.resume_calls == 0
    finally:
        mpd_module._clients.pop("office", None)
