"""When a capture's frames, its pause and its decode happened, against the
pace the audio was spoken at — recorded on the turn, numbers only.

Garage and office, 2026-09-30: on every short capture the speculative
decode started 0.3-0.4 s after the pause it should have started on, and
nothing on the row could say whether the satellite, the network or this
server was late. (It was the satellite: the wake model's reset held its
mic thread before the capture opened, satellite/tests/test_wake_model_reset.py.)
Now every streamed turn carries turn_timings.CAPTURE_TIMING_KEYS:

* the arithmetic (`_CaptureClock`): frame lags against the stream's own
  schedule, the pause's arrival, the decode's start after the last voiced
  frame, and the satellite's own numbers — each case the live system could
  show, told apart: a satellite that started behind, a network that held
  frames up, a decode that queued here;
* over the real socket: what a satellite that stamps its messages gets
  recorded, what an older one still gets, garbage refused, the turn's own
  decode waiting behind a copy it then threw away (the "hidden queue"), a
  second copy deferred behind the first, and an early commit;
* the summary's `capture_timing` section: numbers per key, the one signed
  key kept signed.

No database: routing, TTS and voice identification are stubbed, as in
test_speculative_stt.py.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from domovoi.config import settings
from domovoi.main import app
from domovoi.streaming import _CaptureClock, _Speculation
from domovoi.tests.test_speculative_stt import (  # noqa: F401 - `pipeline` is a fixture
    LOUD,
    QUIET,
    _finish_turn,
    _send,
    _WatchedWhisper,
    pipeline,
)
from domovoi.turn_timings import CAPTURE_TIMING_KEYS, summarize

F = 0.03    # one frame, in seconds


def _stream(clock: _CaptureClock, arrivals: list[float]) -> None:
    for k, t in enumerate(arrivals):
        clock.frame(k, t)


# ─── the arithmetic ────────────────────────────────────────────────────────


def test_a_capture_in_step_shows_nothing_late() -> None:
    c = _CaptureClock(start_rx=0.0, sat_start={"backlog_ms": 0, "wake_ms": 1300})
    _stream(c, [k * F for k in range(50)])
    # The pause after frame 29 (last voiced 21, 8 silent), decoded at once.
    spec = _Speculation(serial=1, frames=30, pause_rx=29 * F + 0.001,
                        pause_clock={"sat_ms": 871, "backlog_ms": 0}, last_voiced=21,
                        decode_started=29 * F + 0.003, decode_wait_ms=0)
    c.end_rx, c.end_frames, c.end_sat = 49 * F, 50, {"sat_ms": 1470, "backlog_ms": 0}
    assert c.flags(spec) == {
        "frame_lag_first_ms": 0, "frame_lag_max_ms": 0,
        "sat_start_backlog_ms": 0, "sat_wake_ms": 1300,
        "end_rx_lag_ms": 0, "sat_end_backlog_ms": 0,
        "pause_rx_lag_ms": 1, "pause_to_decode_ms": 2,
        # 8 frames of silence after the last voiced one: the pause length.
        "decode_start_ms": 243,
        "decode_wait_ms": 0,
        "sat_pause_backlog_ms": 0, "sat_pause_ms": 871, "pause_net_ms": 0,
    }
    assert set(c.flags(spec)) <= set(CAPTURE_TIMING_KEYS)


def test_a_satellite_that_started_behind_shows_it_everywhere() -> None:
    """What the office turn at 02:41:43 looked like: the wake model's reset
    held the mic thread 1.2 s, so frames 0-39 left the satellite in one
    burst when it ended, and the pause after frame 26 (last voiced 18)
    with them — its decode started 660 ms after the last word, not 240."""
    c = _CaptureClock(start_rx=1.2, sat_start={"backlog_ms": 1200, "wake_ms": 2900})
    _stream(c, [1.2 + k * 1e-4 if k < 40 else k * F for k in range(60)])
    spec = _Speculation(serial=1, frames=27, pause_rx=1.2027,
                        pause_clock={"sat_ms": 3, "backlog_ms": 390}, last_voiced=18,
                        decode_started=1.2031, decode_wait_ms=0)
    flags = c.flags(spec)
    assert flags["frame_lag_first_ms"] == 1200
    assert flags["pause_rx_lag_ms"] == 423
    assert flags["decode_start_ms"] == 663
    assert flags["pause_to_decode_ms"] == 0 and flags["decode_wait_ms"] == 0   # not this server
    assert flags["sat_start_backlog_ms"] == 1200 and flags["sat_pause_backlog_ms"] == 390
    assert abs(flags["pause_net_ms"]) <= 1                                   # nor the network


def test_a_network_that_held_frames_up_shows_as_frame_lag_and_net() -> None:
    arrivals = [k * F for k in range(60)]
    for k in range(20, 31):                      # frames 20-30 held up, then flushed
        arrivals[k] = 30 * F + 0.3               # frame 20 by 600 ms, frame 30 by 300
    c = _CaptureClock(start_rx=0.0)
    _stream(c, arrivals)
    spec = _Speculation(serial=1, frames=31, pause_rx=30 * F + 0.3,
                        pause_clock={"sat_ms": 900, "backlog_ms": 0}, last_voiced=22,
                        decode_started=30 * F + 0.301, decode_wait_ms=0)
    flags = c.flags(spec)
    assert flags["frame_lag_max_ms"] == 600 and flags["frame_lag_first_ms"] == 0
    assert flags["pause_rx_lag_ms"] == 300
    assert flags["sat_pause_backlog_ms"] == 0
    assert flags["pause_net_ms"] == 300


def test_a_decode_that_queued_here_shows_as_wait() -> None:
    c = _CaptureClock(start_rx=0.0)
    _stream(c, [k * F for k in range(40)])
    spec = _Speculation(serial=1, frames=30, pause_rx=29 * F, last_voiced=21,
                        decode_started=29 * F + 0.4, decode_wait_ms=400)
    flags = c.flags(spec)
    assert flags["pause_rx_lag_ms"] == 0
    assert (flags["pause_to_decode_ms"], flags["decode_wait_ms"]) == (400, 400)
    assert flags["decode_start_ms"] == 640


def test_what_cannot_be_known_is_left_out() -> None:
    c = _CaptureClock(start_rx=0.0)
    assert c.flags(None) == {}
    _stream(c, [k * F for k in range(10)])
    assert set(c.flags(None)) == {"frame_lag_first_ms", "frame_lag_max_ms"}
    # A copy whose decode never started, from a satellite with no clock and
    # no reported pause (the loudness detector found it).
    spec = _Speculation(serial=1, frames=8, pause_rx=7 * F)
    assert set(c.flags(spec)) == {"frame_lag_first_ms", "frame_lag_max_ms", "pause_rx_lag_ms"}


# ─── over the socket ──────────────────────────────────────────────────────


def _hello(ws, *, hints: bool = True, **extra) -> None:
    msg = {"type": "hello", "room_id": "kitchen", **extra}
    if hints:
        msg["speech_pause"] = True
    ws.send_text(json.dumps(msg))
    assert ws.receive_json()["type"] == "ready"


def _doc(pipeline) -> dict:
    (_, doc), = pipeline["routed"]
    return doc


def test_a_satellite_with_a_clock_gets_every_number_recorded(pipeline) -> None:
    pipeline["whisper"] = _WatchedWhisper("set a timer for ten minutes", delay=0.05)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1,
                                 "backlog_ms": 0, "wake_ms": 1310}))
        _send(ws, [LOUD] * 30 + [QUIET] * 8)
        ws.send_text(json.dumps({"type": "speech_pause", "utt": 1, "frame": 38,
                                 "last_voiced_frame": 29, "greeting_played": False,
                                 "sat_ms": 1140, "backlog_ms": 30}))
        _send(ws, [QUIET] * 32)
        ws.send_text(json.dumps({"type": "utterance_end", "greeting_played": False, "utt": 1,
                                 "frames": 70, "last_voiced_frame": 29,
                                 "exit_reason": "vad_silence_after_speech",
                                 "sat_ms": 2100, "backlog_ms": 0}))
        assert _finish_turn(ws) == "set a timer for ten minutes"

    doc = _doc(pipeline)
    assert doc["stt_reused"] is True
    assert (doc["sat_wake_ms"], doc["sat_start_backlog_ms"]) == (1310, 0)
    assert (doc["sat_pause_ms"], doc["sat_pause_backlog_ms"], doc["sat_end_backlog_ms"]) == (1140, 30, 0)
    for key in ("frame_lag_first_ms", "frame_lag_max_ms", "pause_rx_lag_ms", "end_rx_lag_ms",
                "pause_to_decode_ms", "decode_wait_ms", "decode_start_ms"):
        assert type(doc[key]) is int and doc[key] >= 0, key
    assert doc["decode_wait_ms"] == 0
    assert type(doc["pause_net_ms"]) is int
    # The used copy decoded its own audio; the turn made no call of its own.
    assert "stt_decode_wait_ms" not in doc
    assert set(doc) & set(CAPTURE_TIMING_KEYS) == set(CAPTURE_TIMING_KEYS) - {"stt_decode_wait_ms"}


def test_an_older_satellite_gets_what_the_server_can_see(pipeline) -> None:
    """No hints, no clock: the frame lags, the end, and the turn's own
    decode — which waited for nothing."""
    pipeline["whisper"] = _WatchedWhisper("what time is it", delay=0.02)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws, hints=False)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
        _send(ws, [LOUD] * 20 + [QUIET] * 20)
        ws.send_text(json.dumps({"type": "utterance_end", "greeting_played": False}))
        assert _finish_turn(ws) == "what time is it"
    doc = _doc(pipeline)
    assert set(doc) & set(CAPTURE_TIMING_KEYS) == {
        "frame_lag_first_ms", "frame_lag_max_ms", "end_rx_lag_ms", "stt_decode_wait_ms",
    }
    assert doc["stt_decode_wait_ms"] == 0


def test_garbage_clock_fields_are_not_recorded(pipeline) -> None:
    pipeline["whisper"] = _WatchedWhisper("pause the music", delay=0.02)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1,
                                 "backlog_ms": "lots", "wake_ms": -5}))
        _send(ws, [LOUD] * 10 + [QUIET] * 8)
        ws.send_text(json.dumps({"type": "speech_pause", "utt": 1, "frame": 18,
                                 "last_voiced_frame": 9, "sat_ms": True, "backlog_ms": 1.5}))
        _send(ws, [QUIET] * 32)
        ws.send_text(json.dumps({"type": "utterance_end", "greeting_played": False, "utt": 1,
                                 "frames": 50, "last_voiced_frame": 9, "backlog_ms": None}))
        _finish_turn(ws)
    doc = _doc(pipeline)
    assert not {k for k in doc if k.startswith("sat_")}
    assert "pause_net_ms" not in doc
    assert "decode_start_ms" in doc


def test_the_turns_own_decode_waiting_behind_a_copy_it_threw_away_is_counted(pipeline) -> None:
    """The hidden queue: speech after the copy, so the turn transcribes the
    whole capture — and has to wait for the copy's decode, still running,
    first. stt_ms leaves that wait out; stt_decode_wait_ms has it."""
    pipeline["whisper"] = _WatchedWhisper("set a timer", "set a timer for ten minutes", delay=0.4)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 3}))
        _send(ws, [LOUD] * 10 + [QUIET] * 8)
        ws.send_text(json.dumps({"type": "speech_pause", "utt": 3, "frame": 18, "last_voiced_frame": 9}))
        ws.send_text(json.dumps({"type": "speech_resume", "utt": 3, "frame": 18}))
        _send(ws, [LOUD] * 10 + [QUIET] * 4)
        ws.send_text(json.dumps({"type": "utterance_end", "greeting_played": False, "utt": 3,
                                 "frames": 32, "last_voiced_frame": 27}))
        assert _finish_turn(ws) == "set a timer for ten minutes"
    doc = _doc(pipeline)
    assert doc["stt_reused"] is False
    assert doc["stt_decode_wait_ms"] >= 200
    assert doc["stt_wait_ms"] >= doc["stt_ms"] + doc["stt_decode_wait_ms"] - 20
    # The copy's own record: its pause, decoded at once.
    assert doc["decode_wait_ms"] == 0 and doc["pause_to_decode_ms"] < 100


def test_a_second_copy_deferred_behind_the_first_shows_the_delay(pipeline) -> None:
    """A pause that arrives while the first copy is decoding is copied when
    that decode ends (never two at once): no wait inside the Whisper call,
    but the pause-to-decode gap has it."""
    pipeline["whisper"] = _WatchedWhisper("set a timer for ten", "set a timer for ten minutes", delay=0.3)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 7}))
        _send(ws, [LOUD] * 20 + [QUIET] * 8)
        ws.send_text(json.dumps({"type": "speech_pause", "utt": 7, "frame": 28, "last_voiced_frame": 19}))
        ws.send_text(json.dumps({"type": "speech_resume", "utt": 7, "frame": 28}))
        _send(ws, [LOUD] * 10 + [QUIET] * 8)
        ws.send_text(json.dumps({"type": "speech_pause", "utt": 7, "frame": 46, "last_voiced_frame": 37}))
        time.sleep(0.4)
        _send(ws, [QUIET] * 32)
        ws.send_text(json.dumps({"type": "utterance_end", "greeting_played": False, "utt": 7,
                                 "frames": 78, "last_voiced_frame": 37}))
        assert _finish_turn(ws) == "set a timer for ten minutes"
    doc = _doc(pipeline)
    assert doc["stt_reused"] is True and doc["speculative_decodes"] == 2
    assert doc["decode_wait_ms"] == 0
    assert doc["pause_to_decode_ms"] >= 100


@pytest.fixture
def commit_on(pipeline, monkeypatch):
    monkeypatch.setattr(settings, "early_commit_enabled", True)
    monkeypatch.setattr(settings, "early_commit_tier_b", True)
    monkeypatch.setattr(settings, "early_commit_hold_a_ms", 350)
    return pipeline


def test_an_early_commit_has_no_utterance_end_to_time(commit_on) -> None:
    commit_on["whisper"] = _WatchedWhisper("Pause the music.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws, capture_control=True)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1,
                                 "backlog_ms": 0, "wake_ms": 1250}))
        _send(ws, [LOUD] * 20 + [QUIET] * 8)
        ws.send_text(json.dumps({"type": "speech_pause", "utt": 1, "frame": 28,
                                 "last_voiced_frame": 19, "sat_ms": 840, "backlog_ms": 0}))
        time.sleep(0.2)
        _send(ws, [QUIET] * 4)
        assert ws.receive_json() == {"type": "end_capture", "utt": 1}
        _finish_turn(ws)
    doc = _doc(commit_on)
    assert doc["early_commit"] == "A"
    assert "end_rx_lag_ms" not in doc and "sat_end_backlog_ms" not in doc
    assert (doc["sat_wake_ms"], doc["sat_pause_ms"]) == (1250, 840)
    assert doc["decode_start_ms"] >= 0


# ─── the summary ──────────────────────────────────────────────────────────


def test_the_summary_reports_each_key_and_keeps_the_signed_one_signed() -> None:
    rows = [
        ({"total_ms": 900, "decode_start_ms": 240, "pause_net_ms": -12, "sat_start_backlog_ms": 0}, "fast"),
        ({"total_ms": 1200, "decode_start_ms": 660, "pause_net_ms": 4, "sat_start_backlog_ms": 1200}, "fast"),
        # Not numbers: skipped, never echoed.
        ({"decode_start_ms": "late", "pause_net_ms": True, "frame_lag_max_ms": -3}, "llm"),
    ]
    s = summarize(rows)
    ct = s["capture_timing"]
    assert set(ct) == set(CAPTURE_TIMING_KEYS)
    assert ct["decode_start_ms"] == {"count": 2, "p50": 450, "p95": 639, "max": 660}
    assert ct["pause_net_ms"] == {"count": 2, "p50": -4, "p95": 3, "max": 4}
    assert ct["sat_start_backlog_ms"]["max"] == 1200
    assert ct["frame_lag_max_ms"] == {"count": 0, "p50": None, "p95": None, "max": None}
    assert "late" not in json.dumps(s)
