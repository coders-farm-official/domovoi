"""The real satellite capture loop against the real core socket.

The two halves of early endpointing are tested on their own side elsewhere
(satellite/tests/test_capture_endpointing.py; test_speculative_stt.py and
test_early_commit_stream.py here). This one wires them together: what
`satellite.client.Satellite._stream_capture` actually emits — its hints,
its frame counts, its `utterance_end` — is replayed frame by frame into
`/v1/stream`, after a `ready` the satellite really parsed. If a field name
or a count drifts between the two sides, this is where it shows.
"""

from __future__ import annotations

import asyncio
import json
import queue
import sys
import threading
import time
import types

import numpy as np
import pytest
from fastapi.testclient import TestClient

from domovoi.main import app
from domovoi.streaming import StreamSession
from domovoi.tests.test_speculative_stt import (  # noqa: F401 - `pipeline` is a fixture
    _finish_turn,
    _WatchedWhisper,
    pipeline,
)


def _satellite_client():
    """Import satellite.client with stand-ins for the audio modules a dev
    box lacks (see satellite/tests/_client_import.py), taken out again."""
    stubbed = []
    for name in ("sounddevice", "webrtcvad"):
        if name in sys.modules:
            continue
        try:
            __import__(name)
        except ImportError:
            sys.modules[name] = types.ModuleType(name)
            stubbed.append(name)
    try:
        from satellite import client
    finally:
        for name in stubbed:
            sys.modules.pop(name, None)
    return client


client = _satellite_client()
LOUD = np.full(client.FRAME_SAMPLES, 8000, dtype=np.int16).tobytes()
QUIET = bytes(client.FRAME_BYTES)


class _Vad:
    def __init__(self, level: int) -> None:
        pass

    def is_speech(self, frame: bytes, rate: int) -> bool:
        return any(frame)


class _Leds:
    def set_state(self, state: str) -> None:
        pass

    def set_state_unless(self, unless: str, state: str) -> None:
        pass


class _HookedQueue(queue.Queue):
    def __init__(self, at: int | None, hook) -> None:
        super().__init__()
        self.at, self.hook, self.taken = at, hook, 0

    def get(self, *a, **kw):
        item = super().get(*a, **kw)
        self.taken += 1
        if self.taken == self.at:
            self.hook()
        return item


_LOOPS: list[asyncio.AbstractEventLoop] = []


@pytest.fixture(autouse=True)
def _close_loops():
    yield
    while _LOOPS:
        _LOOPS.pop().close()


def _satellite(monkeypatch, ready: dict, *, early_commit: bool = True):
    """A Satellite with just the capture state, that has parsed `ready`."""
    monkeypatch.setattr(client, "webrtcvad", types.SimpleNamespace(Vad=_Vad))
    monkeypatch.setattr(client, "_promote_pending_server", lambda path: None)
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(
        vad_aggressiveness=2, silence_timeout=1.2, max_record_seconds=30.0,
        noise_gate_dbfs=-40.0, noise_gate_auto_calibrate=False, early_commit=early_commit,
    )
    sat.loop = asyncio.new_event_loop()
    _LOOPS.append(sat.loop)
    sat.send_q = asyncio.Queue()
    sat._offline_drops = 0
    sat._offline_drop_last_log = 0.0
    sat._leds = _Leds()
    sat.shutdown_event = threading.Event()
    sat._greeting_played_this_turn = False
    sat._end_capture = threading.Event()
    sat._capture_lock = threading.Lock()
    sat._upgrade_confirmed = False
    sat._sync_time_with_server = lambda: None
    sat._note_session_accepted = lambda: None
    sat._handle_text_frame(ready)
    return sat


def _capture(sat, frames: list[bytes], *, end_capture_at: int | None = None, utt=None) -> list:
    """Run the real capture loop; returns what it emitted, in order, as
    ("text", dict) / ("bytes", frame)."""
    def arrive() -> None:
        sat._handle_text_frame({"type": "end_capture", "utt": utt if utt is not None else sat._capture_utt})

    raw = _HookedQueue(end_capture_at, arrive)
    for f in frames:
        raw.put(f)
    sat.raw_q = raw
    sat._begin_utterance("wake_word")
    assert sat._stream_capture([]) is True
    sat.loop.run_until_complete(asyncio.sleep(0))
    out = []
    while not sat.send_q.empty():
        kind, data = sat.send_q.get_nowait()
        out.append((kind, json.loads(data) if kind == "text" else data))
    return out


def _hello(ws, sat) -> dict:
    ws.send_text(json.dumps({
        "type": "hello", "room_id": "kitchen", "speech_pause": True,
        "capture_control": sat.cfg.early_commit,
    }))
    ready = ws.receive_json()
    assert ready["type"] == "ready"
    return ready


def _next(ws) -> dict:
    """The next frame from the core, text or audio."""
    msg = ws.receive()
    if msg.get("text") is not None:
        return json.loads(msg["text"])
    return {"type": "<audio>"}


def _replay(ws, messages: list) -> None:
    for kind, data in messages:
        if kind == "text":
            ws.send_text(json.dumps(data))
        else:
            ws.send_bytes(data)


def test_a_real_capture_is_transcribed_at_its_first_pause(pipeline, monkeypatch) -> None:
    whisper = pipeline["whisper"] = _WatchedWhisper("What is the capital of France?", delay=0.05)
    with TestClient(app) as tc, tc.websocket_connect("/v1/stream/kitchen") as ws:
        # The satellite parses the core's real ready first.
        probe = _satellite(monkeypatch, {"type": "ready", "features": []})
        ready = _hello(ws, probe)
        sat = _satellite(monkeypatch, ready)
        assert "speech_pause" in sat._core_features
        emitted = _capture(sat, [LOUD] * 30 + [QUIET] * 60)
        kinds = [d["type"] for k, d in emitted if k == "text"]
        assert kinds == ["utterance_start", "speech_pause", "utterance_end"]
        _replay(ws, emitted)
        assert _finish_turn(ws) == "What is the capital of France?"
    # One decode: the copy taken at the satellite's own pause (30 voiced
    # frames + 8 silent), reused at the end.
    assert whisper.calls == [38 * client.FRAME_BYTES]
    (_, doc), = pipeline["routed"]
    assert doc["stt_reused"] is True
    assert doc["endpoint_silence_ms"] == 40 * 30


def test_a_real_capture_is_ended_early_and_says_so(pipeline, monkeypatch) -> None:
    pipeline["whisper"] = _WatchedWhisper("Pause the music.", delay=0.05)
    with TestClient(app) as tc, tc.websocket_connect("/v1/stream/kitchen") as ws:
        probe = _satellite(monkeypatch, {"type": "ready", "features": []})
        ready = _hello(ws, probe)
        assert "end_capture" in ready["features"]
        sat = _satellite(monkeypatch, ready)
        # The whole capture as it would go without the core stepping in.
        emitted = _capture(sat, [LOUD] * 30 + [QUIET] * 60)
        start, pause = emitted[0], emitted[1 + 30 + 8]
        assert pause[1]["type"] == "speech_pause"
        # Replay it in real order until the core stops listening.
        _replay(ws, [start] + emitted[1:40])
        time.sleep(0.2)                 # the copy's decode
        sess: StreamSession = app.state.active_sessions["kitchen"]
        sent_frames = 38
        for kind, data in emitted[40:]:
            assert kind == "bytes"
            ws.send_bytes(data)
            sent_frames += 1
            ws.send_text(json.dumps({"type": "ping"}))
            first = _next(ws)
            if not sess.utterance_active:
                break
            assert first == {"type": "pong"}
        # The 12th silent frame (360 ms > 350) ended it.
        assert sent_frames == 30 + 12
        seen = [first]
        while seen[-1].get("type") != "response_end":
            seen.append(_next(ws))
        seen = [m for m in seen if m != {"type": "pong"}]
        assert [m["type"] for m in seen] == [
            "end_capture", "transcript", "response_start", "<audio>", "response_end",
        ]
        end_capture = seen[0]
        assert end_capture == {"type": "end_capture", "utt": sat._utt_seq}

        # The satellite honours it: the same capture, stopped where the
        # end_capture reaches it (a frame or two later on a real link).
        sat2 = _satellite(monkeypatch, ready)
        sat2._utt_seq = sat._utt_seq - 1
        stopped = _capture(sat2, [LOUD] * 30 + [QUIET] * 60, end_capture_at=sent_frames + 2)
        late_end = stopped[-1][1]
        assert late_end["type"] == "utterance_end"
        assert late_end["exit_reason"] == "server_endpoint"
        assert late_end["utt"] == end_capture["utt"]
        assert late_end["frames"] == sent_frames + 2 and late_end["last_voiced_frame"] == 29
        ws.send_text(json.dumps(late_end))
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json() == {"type": "pong"}, "read quietly, not answered"
    (_, doc), = pipeline["routed"]
    assert doc["early_commit"] == "A"


def test_a_satellite_that_opted_out_is_never_ended_early(pipeline, monkeypatch) -> None:
    pipeline["whisper"] = _WatchedWhisper("Pause the music.", delay=0.05)
    with TestClient(app) as tc, tc.websocket_connect("/v1/stream/kitchen") as ws:
        probe = _satellite(monkeypatch, {"type": "ready", "features": []}, early_commit=False)
        ready = _hello(ws, probe)
        sat = _satellite(monkeypatch, ready, early_commit=False)
        emitted = _capture(sat, [LOUD] * 30 + [QUIET] * 60)
        _replay(ws, emitted)
        # The first reply is the transcript: no end_capture ahead of it.
        assert _finish_turn(ws) == "Pause the music."
    (_, doc), = pipeline["routed"]
    assert "early_commit" not in doc and doc["stt_reused"] is True


@pytest.mark.parametrize("features", [[], None])
def test_against_an_older_core_the_capture_sends_nothing_new(monkeypatch, features) -> None:
    ready = {"type": "ready", "protocol_version": "0.1"}
    if features is not None:
        ready["features"] = features
    sat = _satellite(monkeypatch, ready)
    emitted = _capture(sat, [LOUD] * 30 + [QUIET] * 60)
    types_sent = {d["type"] for k, d in emitted if k == "text"}
    assert types_sent == {"utterance_start", "utterance_end"}, (
        "an older core answers any other type with error, which ends the turn"
    )
