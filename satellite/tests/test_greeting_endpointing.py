"""A wake capture is not ended by its own greeting.

Office satellite, 2026-09-28 (conversation_log #187): the wake greeting
"Back so soon?" bled past the XVF3800's echo cancellation, VAD took it for
speech, and the capture ended `silence_timeout` (1.2 s) after it — on the
pause a person naturally leaves after a question — holding nothing but the
greeting. The core answered Domovoi's own words; the user's real request
("Tell me a joke.") went into a closed mic and was drained at the next wake.

Now the frames under the greeting (and a short tail after it) still stream
but don't count toward endpointing; after a greeting the mic partly heard,
the user gets `greeting.reply_wait` seconds to start talking; and
utterance_end names the clip that played. Driven with the same FakeVad
harness as test_send_queue_lifecycle: FRAME is voice, SILENCE is not.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import types
from pathlib import Path

from satellite.tests.test_send_queue_lifecycle import (
    FRAME,
    SILENCE,
    FakeVad,
    client,
    make_sat,
    settle,
)

MS = client.FRAME_MS
CLIP = "greet_funny_a995c16f.mp3"   # "Back so soon?", what #187's Pi played


def _frames(sec: float) -> int:
    return int(sec * 1000 / MS)


class _GreetingProc:
    """mpg123 stand-in: 'playing' until the capture has read ``frames``
    mic frames off the queue."""

    def __init__(self, q, total: int, frames: int) -> None:
        self.q, self.total, self.frames = q, total, frames

    def poll(self):
        consumed = self.total - self.q.qsize()
        return None if consumed <= self.frames else 0


def _sat_with_greeting(monkeypatch, feed: list[bytes], *, greeting_frames: int | None):
    monkeypatch.setattr(client, "webrtcvad", types.SimpleNamespace(Vad=FakeVad))
    loop = asyncio.new_event_loop()
    sat = make_sat(loop=loop)
    sat.send_q = asyncio.Queue()
    sat.cfg.silence_timeout = 1.2          # the shipped defaults
    sat.cfg.max_record_seconds = 30.0
    sat.cfg.greeting_reply_wait = 2.5
    sat._greeting_lock = threading.Lock()
    for f in feed:
        sat.raw_q.put(f)
    sat._greeting_played_this_turn = greeting_frames is not None
    sat._greeting_clip_name = CLIP if greeting_frames is not None else None
    sat._greeting_proc = (
        _GreetingProc(sat.raw_q, len(feed), greeting_frames)
        if greeting_frames is not None else None
    )
    return sat, loop


def _capture(sat, loop):
    try:
        ok = sat._stream_capture([])       # a wake turn: no prefix, no pre-speech limit
        loop.run_until_complete(settle())
        items = []
        while not sat.send_q.empty():
            items.append(sat.send_q.get_nowait())
    finally:
        loop.close()
    audio = [d for kind, d in items if kind == "bytes"]
    controls = [json.loads(d) for kind, d in items if kind == "text"]
    return ok, audio, controls


def test_pausing_after_the_greeting_no_longer_loses_the_request(monkeypatch):
    """#187's shape: 1 s of greeting bleed, a 1.5 s pause, then the user."""
    bleed, pause, user = _frames(1.0), _frames(1.5), _frames(1.5)
    feed = [FRAME] * bleed + [SILENCE] * pause + [FRAME] * user + [SILENCE] * _frames(2.0)
    sat, loop = _sat_with_greeting(monkeypatch, feed, greeting_frames=_frames(1.2))
    ok, audio, controls = _capture(sat, loop)

    assert ok
    assert audio[bleed + pause:].count(FRAME) == user, "all of the request was sent"
    assert audio == feed[: bleed + pause + user + _frames(1.2)]   # ended 1.2 s after the user
    assert controls == [
        {"type": "utterance_end", "greeting_played": True, "greeting_clip": CLIP}
    ]
    # One-shot: the next turn is not greeting-filtered.
    assert sat._greeting_played_this_turn is False
    assert sat._greeting_clip_name is None


def test_a_bleed_with_nobody_speaking_is_sent_after_the_reply_wait(monkeypatch):
    """Only the greeting was heard: the capture waits reply_wait for the
    user, then sends it anyway — the core drops a greeting-only turn."""
    greeting = _frames(1.2)
    feed = [FRAME] * _frames(1.0) + [SILENCE] * _frames(20.0)
    sat, loop = _sat_with_greeting(monkeypatch, feed, greeting_frames=greeting)
    ok, audio, controls = _capture(sat, loop)

    reply_limit = int(2.5 * 1000 / MS)
    assert ok
    assert len(audio) == greeting + client._GREETING_TAIL_FRAMES + reply_limit
    assert controls[-1]["type"] == "utterance_end"
    assert controls[-1]["greeting_clip"] == CLIP


def test_a_command_said_over_the_greeting_is_still_sent(monkeypatch):
    """The user talks over a long greeting and stops before it ends: none
    of their speech counted toward endpointing, and it is all sent."""
    greeting = _frames(2.0)
    feed = [FRAME] * greeting + [SILENCE] * _frames(20.0)
    sat, loop = _sat_with_greeting(monkeypatch, feed, greeting_frames=greeting)
    ok, audio, controls = _capture(sat, loop)

    assert ok
    assert audio.count(FRAME) == greeting
    assert controls[-1] == {"type": "utterance_end", "greeting_played": True, "greeting_clip": CLIP}


def test_a_clean_greeting_leaves_the_capture_as_it_always_was(monkeypatch):
    """The AEC removed the greeting: nothing voiced under it, and the
    capture runs exactly as a capture without a greeting does."""
    feed = [SILENCE] * _frames(2.5) + [FRAME] * _frames(1.5) + [SILENCE] * _frames(2.0)

    sat, loop = _sat_with_greeting(monkeypatch, list(feed), greeting_frames=_frames(1.2))
    _ok, with_greeting, _c = _capture(sat, loop)
    sat, loop = _sat_with_greeting(monkeypatch, list(feed), greeting_frames=None)
    _ok, without, controls = _capture(sat, loop)

    assert with_greeting == without
    assert with_greeting.count(FRAME) == _frames(1.5)
    assert controls == [{"type": "utterance_end", "greeting_played": False}]


def test_play_greeting_remembers_which_clip_played(monkeypatch, tmp_path):
    clip = tmp_path / CLIP
    clip.write_bytes(b"")
    sat = make_sat()
    sat.cfg.greeting_enabled = True
    sat.cfg.tts_playback_gain = 1.0
    sat.cfg.music_alsa_device = "default"
    sat._greeting_lock = threading.Lock()
    sat._greeting_proc = None
    sat._greeting_clip_name = None
    monkeypatch.setattr(client.Satellite, "_pick_greeting", lambda self: Path(clip))

    class _Popen:
        def __init__(self, args, **kwargs):
            self.args = args

        def poll(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    sat._play_greeting()
    assert sat._greeting_played_this_turn is True
    assert sat._greeting_clip_name == CLIP
    assert sat._greeting_playing() is True
