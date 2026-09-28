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

The same capture also reports its own endpoint to a core that asks for it
(early endpointing, satellite/tests/test_capture_endpointing.py): the frame
counts, the last voiced frame and the exit reason in utterance_end, and
`speech_pause` / `end_capture` for speculative transcription and early
commit. Under the greeting the two meet: its words are streamed but not
counted, so they are never the voiced frames a pause or a last-voiced
index is about, and a capture the core ends early still names its clip.
"""

from __future__ import annotations

import asyncio
import json
import queue
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
    assert controls == [{
        "type": "utterance_end", "greeting_played": True, "greeting_clip": CLIP,
        # The endpoint counts: only the user's words are voiced frames, and
        # the last of them is the frame index in the whole stream.
        "utt": 0, "frames": len(audio), "last_voiced_frame": bleed + pause + user - 1,
        "exit_reason": "vad_silence_after_speech",
        "voiced_frames": user, "trailing_silent_frames": 40, "silence_limit_frames": 40,
    }]
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
    end = controls[-1]
    assert end["type"] == "utterance_end"
    assert end["greeting_clip"] == CLIP
    # It ended on the reply wait, and says so: the reason an opted-in
    # room's command recording keeps. Nothing voiced counted (the bleed
    # was under the greeting), so there is no last voiced frame to claim.
    assert end["exit_reason"] == "no_speech_after_greeting"
    assert end["frames"] == len(audio)
    assert end["last_voiced_frame"] is None and end["voiced_frames"] == 0
    assert end["trailing_silent_frames"] == end["silence_limit_frames"] == reply_limit


def test_a_command_said_over_the_greeting_is_still_sent(monkeypatch):
    """The user talks over a long greeting and stops before it ends: none
    of their speech counted toward endpointing, and it is all sent."""
    greeting = _frames(2.0)
    feed = [FRAME] * greeting + [SILENCE] * _frames(20.0)
    sat, loop = _sat_with_greeting(monkeypatch, feed, greeting_frames=greeting)
    ok, audio, controls = _capture(sat, loop)

    assert ok
    assert audio.count(FRAME) == greeting
    end = controls[-1]
    assert (end["type"], end["greeting_played"], end["greeting_clip"]) == (
        "utterance_end", True, CLIP,
    )
    # All of it was said under the greeting: the core gets no last voiced
    # frame, so it transcribes the whole capture rather than trust a copy.
    assert end["exit_reason"] == "no_speech_after_greeting"
    assert end["last_voiced_frame"] is None


def test_a_clean_greeting_leaves_the_capture_as_it_always_was(monkeypatch):
    """The AEC removed the greeting: nothing voiced under it, and the
    capture runs exactly as a capture without a greeting does."""
    feed = [SILENCE] * _frames(2.5) + [FRAME] * _frames(1.5) + [SILENCE] * _frames(2.0)

    sat, loop = _sat_with_greeting(monkeypatch, list(feed), greeting_frames=_frames(1.2))
    _ok, with_greeting, with_controls = _capture(sat, loop)
    sat, loop = _sat_with_greeting(monkeypatch, list(feed), greeting_frames=None)
    _ok, without, controls = _capture(sat, loop)

    assert with_greeting == without
    assert with_greeting.count(FRAME) == _frames(1.5)
    (end,) = controls
    assert end["greeting_played"] is False and "greeting_clip" not in end
    # The endpoint the core is told is the same, frame for frame.
    (with_end,) = with_controls
    assert with_end.pop("greeting_played") is True
    assert with_end.pop("greeting_clip") == CLIP
    end.pop("greeting_played")
    assert with_end == end
    assert end["exit_reason"] == "vad_silence_after_speech"


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


# ─── with early endpointing (a core that lists speech_pause) ──────────────


class _EndingQueue(queue.Queue):
    """The mic queue, which sets the capture's `end_capture` once the
    capture has read ``after`` frames: the core's early commit landing at
    that point of the stream."""

    def __init__(self, sat, after: int) -> None:
        super().__init__()
        self.sat, self.after, self.read = sat, after, 0

    def get(self, *args, **kwargs):
        frame = super().get(*args, **kwargs)
        self.read += 1
        if self.read == self.after:
            self.sat._end_capture.set()
        return frame


def _hinting(sat):
    sat._core_features = frozenset({"speech_pause"})
    return sat


def test_the_greetings_own_words_never_start_a_pause(monkeypatch):
    """A bleed then silence is exactly what a `speech_pause` is, if it
    counted. Under the greeting it doesn't: the only pause reported is the
    one after the user spoke, and it names the clip that played."""
    bleed, gap, user = _frames(1.0), 20, 20
    feed = [FRAME] * bleed + [SILENCE] * gap + [FRAME] * user + [SILENCE] * 60
    sat, loop = _sat_with_greeting(monkeypatch, feed, greeting_frames=_frames(1.2))
    ok, audio, controls = _capture(_hinting(sat), loop)

    assert ok
    pause, end = controls
    last = bleed + gap + user - 1
    assert pause == {
        "type": "speech_pause", "utt": 0, "frame": last + 1 + 8,
        "last_voiced_frame": last, "greeting_played": True, "greeting_clip": CLIP,
    }
    assert end["last_voiced_frame"] == last and end["voiced_frames"] == user
    assert end["exit_reason"] == "vad_silence_after_speech"
    assert (end["greeting_played"], end["greeting_clip"]) == (True, CLIP)


def test_a_bleed_alone_never_reports_a_pause(monkeypatch):
    """Nothing but the greeting was heard: no `speech_pause`, so the core
    has no copy of the greeting to transcribe early or end the capture on."""
    feed = [FRAME] * _frames(1.0) + [SILENCE] * _frames(10.0)
    sat, loop = _sat_with_greeting(monkeypatch, feed, greeting_frames=_frames(1.2))
    ok, _audio, controls = _capture(_hinting(sat), loop)

    assert ok
    (end,) = controls
    assert end["type"] == "utterance_end"
    assert end["exit_reason"] == "no_speech_after_greeting"
    assert end["last_voiced_frame"] is None


def test_the_core_can_still_end_a_greeting_turn_early(monkeypatch):
    """Greeting bleed, then "pause the music", then the core's end_capture:
    the capture stops there, with the greeting fields and the server's
    reason on its utterance_end (the late frame the core reads to log
    speech after its commit, and the reason its recording keeps)."""
    bleed, gap, user, hold = _frames(1.0), 20, 20, 12
    feed = [FRAME] * bleed + [SILENCE] * gap + [FRAME] * user + [SILENCE] * 60
    sat, loop = _sat_with_greeting(monkeypatch, [], greeting_frames=_frames(1.2))
    q = _EndingQueue(sat, after=bleed + gap + user + hold)
    for f in feed:
        q.put(f)
    sat.raw_q = q
    sat._greeting_proc = _GreetingProc(q, len(feed), _frames(1.2))
    ok, audio, controls = _capture(_hinting(sat), loop)

    assert ok
    assert len(audio) == bleed + gap + user + hold
    pause, end = controls
    assert pause["type"] == "speech_pause" and pause["greeting_clip"] == CLIP
    assert end["exit_reason"] == "server_endpoint"
    assert end["frames"] == len(audio)
    assert end["last_voiced_frame"] == bleed + gap + user - 1
    assert (end["greeting_played"], end["greeting_clip"]) == (True, CLIP)
