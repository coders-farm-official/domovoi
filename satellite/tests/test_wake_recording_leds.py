"""The ring during a wake-word training take (2026-11-10, found on hardware).

* Each clip shows its own steady light ("recording") and the ring goes dark
  in the gap, so a person can see where one clip ends and the next starts.
  It used the sound-following "listening" look before, which hid the clips.
* When the take ends, by Stop mid-clip, the ring goes back to idle. The
  cleanup used to say "idle unless listening", and a clip IS listening, so a
  Stop left the ring stuck in the capture look after recording had ended.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import types

from satellite.tests._client_import import import_client
from satellite import leds as leds_mod

client = import_client()


class RealisticLeds:
    """Tracks state with the real controller's set / set-unless rules."""

    def __init__(self) -> None:
        self.state = "idle"
        self.history: list[str] = []

    def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.history.append(state)

    def set_state_unless(self, unless: str, new: str) -> None:
        if self.state in (unless, new):
            return
        self.state = new
        self.history.append(new)


FRAME = b"\x01\x00" * 480


def _sat(target: int, *, stop_after_frames: int | None = None):
    sat = object.__new__(client.Satellite)
    sat._leds = RealisticLeds()
    sat.raw_q = queue.Queue()
    sat.shutdown_event = threading.Event()
    sat.wake_recording = threading.Event()
    sat.wake_recording.set()
    sat._wake_rec_params = {"slug": "hey_domovoi", "clip_seconds": 0.09,
                            "target_count": target}
    sat.sent: list = []
    sat._drain_mic = lambda: None
    sat._begin_utterance = lambda trigger: sat.sent.append(("start", trigger))
    sat._emit_audio = lambda frame: sat.sent.append("audio")
    sat._emit_text = lambda msg: sat.sent.append(msg.get("type"))
    fed = {"n": 0}

    def get(timeout=None):
        fed["n"] += 1
        if stop_after_frames is not None and fed["n"] > stop_after_frames:
            sat.wake_recording.clear()        # what a stop_wake_recording frame does
            raise queue.Empty
        return FRAME

    sat.raw_q.get = get
    # No real waiting between clips.
    sat.shutdown_event.wait = lambda t=None: False
    return sat


def test_each_clip_lights_the_ring_and_it_goes_dark_between():
    sat = _sat(target=3)
    sat._wake_recording_loop()
    assert sat.sent.count(("start", "wake_clip")) == 3
    assert sat._leds.history == ["recording", "idle", "recording", "idle", "recording", "idle"]
    assert sat._leds.state == "idle"


def test_a_stop_mid_clip_returns_the_ring_to_idle():
    sat = _sat(target=50, stop_after_frames=4)    # 3 frames = one clip; stop inside clip 2
    sat._wake_recording_loop()
    assert sat.sent.count(("start", "wake_clip")) == 1      # no partial clip sent
    assert sat._leds.state == "idle"
    assert not sat.wake_recording.is_set()


def test_the_ring_has_a_recording_look_on_both_kinds_of_board():
    native = [c for c in vars(leds_mod).values()
              if isinstance(c, type) and isinstance(getattr(c, "_STATES", None), dict)]
    assert native and all("recording" in c._STATES for c in native)
    assert leds_mod.COLOR_RECORDING != leds_mod.COLOR_LISTENING
