"""`utterance_end` says why the capture ended and counts its frames.

The core keeps these numbers only in a room an admin opted in to command
recording (domovoi/command_captures.py), to evaluate end-of-turn detection
against real captures (design notes 2026-09-28). What is pinned:

* the fields ride the EXISTING frame — never a new frame type, which an
  older core answers with `error`, and the satellite treats `error` as the
  end of the turn;
* the counts are exact: `frames` is everything streamed, the silent run
  the capture ended on equals `silence_limit_frames`, so the last voiced
  frame is `frames - trailing_silent_frames`;
* a capture that hit `max_record_seconds` says so;
* numbers and a reason only — nothing that was said.
"""

from __future__ import annotations

import asyncio
import json
import types

from satellite.tests._client_import import import_client
from satellite.tests.test_send_queue_lifecycle import (
    FRAME,
    SILENCE,
    FakeVad,
    fill_mic,
    make_sat,
    settle,
)

client = import_client()


def _capture(sat, loop) -> list:
    sat.send_q = asyncio.Queue()
    assert sat._stream_capture([]) is True
    loop.run_until_complete(settle())
    items = []
    while not sat.send_q.empty():
        items.append(sat.send_q.get_nowait())
    return items


def test_a_silence_ended_capture_reports_its_reason_and_exact_counts(monkeypatch):
    monkeypatch.setattr(client, "webrtcvad", types.SimpleNamespace(Vad=FakeVad))
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop=loop)
        consumed = fill_mic(sat, loud=7)
        items = _capture(sat, loop)
        kinds = [k for k, _ in items]
        assert kinds.count("text") == 1, "one control frame, and it is the existing one"
        end = json.loads(items[-1][1])
        assert end["type"] == "utterance_end"
        silence_limit = int(sat.cfg.silence_timeout * 1000 / client.FRAME_MS)
        assert end == {
            "type": "utterance_end",
            "greeting_played": False,
            # The early-endpointing fields share the frame (the core's
            # speculative-transcript check reads them).
            "utt": 0,
            "last_voiced_frame": 6,
            "exit_reason": "vad_silence_after_speech",
            "frames": consumed,
            "voiced_frames": 7,
            "trailing_silent_frames": silence_limit,
            "silence_limit_frames": silence_limit,
        }
        assert end["frames"] - end["trailing_silent_frames"] == 7
        assert end["last_voiced_frame"] == end["frames"] - end["trailing_silent_frames"] - 1
    finally:
        loop.close()


def test_a_capture_that_never_went_quiet_says_max_record_seconds(monkeypatch):
    monkeypatch.setattr(client, "webrtcvad", types.SimpleNamespace(Vad=FakeVad))
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop=loop)
        sat.cfg.max_record_seconds = 0.3        # 10 frames
        for _ in range(12):
            sat.raw_q.put(FRAME)
        end = json.loads(_capture(sat, loop)[-1][1])
        assert end["exit_reason"] == "max_record_seconds"
        assert end["frames"] == 10 and end["voiced_frames"] == 10
        assert end["trailing_silent_frames"] == 0
    finally:
        loop.close()


def test_speech_that_resumes_resets_the_silent_run(monkeypatch):
    monkeypatch.setattr(client, "webrtcvad", types.SimpleNamespace(Vad=FakeVad))
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop=loop)
        for f in [FRAME] * 3 + [SILENCE] * 4 + [FRAME] * 2:
            sat.raw_q.put(f)
        fill_mic(sat, loud=0)
        end = json.loads(_capture(sat, loop)[-1][1])
        assert end["voiced_frames"] == 5
        assert end["trailing_silent_frames"] == end["silence_limit_frames"]
    finally:
        loop.close()
