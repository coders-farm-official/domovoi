"""A drop-in opens only after the reply that announced it has played out.

Owner report, 2026-11-12, two XVF3800 rooms: "Dropping in on the kitchen."
(and, in confirm mode, the target's "Okay, connecting you with the
office.") was cut off before the room name. The core sends `dropin_start`
right after the reply's `response_end`, when the reply is still queued on
the satellite (the core streams speech faster than it plays). Entering the
call there set `dropin_active`, and from then on every inbound binary frame
(the start chime, then the peer's mic every 30 ms) trimmed the playback
queue to `_DROPIN_MAX_PLAYBACK_FRAMES` by dropping the OLDEST audio, which
was the rest of the reply.

Pinned here, with the satellite's playback reduced to a fake consumer of
its playback queue (no sound card):

* a `dropin_start` marked `await_ready` while a reply is playing waits for
  it: every queued chunk of the reply is played before the call opens, and
  `dropin_ready` goes out only then;
* a playback that never finishes holds the call for DROPIN_REPLY_WAIT_SEC
  at most;
* nothing playing: the call opens at once and says `dropin_ready`;
* a dropin_end (or a disconnect) while waiting drops the call;
* a core from before (no `await_ready`) gets today's behaviour exactly: the
  call opens at once, and no `dropin_ready` is sent (that core would answer
  an unknown frame with `error`);
* the hello says `dropin_ready`.
"""

from __future__ import annotations

import queue
import threading
import time
import types
from pathlib import Path

import pytest

from satellite.tests._client_import import import_client

client = import_client()

# Real seconds are scaled down by this much: a "3 s" reply plays in 0.3 s,
# and the 8 s cap is 0.8 s.
SCALE = 10.0
CHUNK_PLAY_SEC = 0.3 / SCALE  # one 0.3 s chunk of a reply


class Leds:
    def __init__(self) -> None:
        self.states: list[str] = []

    def set_state(self, s: str) -> None:
        self.states.append(s)

    def set_state_unless(self, _keep: str, s: str) -> None:
        self.states.append(s)


def make_sat(monkeypatch):
    monkeypatch.setattr(client.pc_engine, "full_duplex", lambda cfg: True)
    monkeypatch.setattr(
        client.Satellite, "DROPIN_REPLY_WAIT_SEC", 8.0 / SCALE, raising=True,
    )
    monkeypatch.setattr(client.Satellite, "DROPIN_REPLY_POLL_SEC", 0.005, raising=True)
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(mic_enabled=True, device=types.SimpleNamespace(name="xvf3800"))
    sat.loop = None
    sat.playback_q = queue.Queue()
    sat.playback_active = threading.Event()
    sat.stop_playback = threading.Event()
    sat.response_done = threading.Event()
    sat.shutdown_event = threading.Event()
    sat.dropin_active = threading.Event()
    sat._playback_idle = threading.Event()
    sat._playback_idle.set()
    sat._prebuffer_active = False
    sat._post_playback_state = None
    sat._dropin_rate = 16_000
    sat._dropin_pending = None
    sat._dropin_seq = 0
    sat.audio_sample_rate = 22_050
    sat._leds = Leds()
    sat._stop_music = lambda: None
    sat._stop_ack = lambda: None
    sat.sent: list[tuple[float, dict]] = []
    sat._emit_text = lambda p: sat.sent.append((time.monotonic(), p)) or True
    return sat


def start_frame(*, await_ready: bool) -> dict:
    frame = {
        "type": "dropin_start", "peer_room": "kitchen", "peer_label": "kitchen",
        "audio_sample_rate": 16000, "full_duplex": True,
    }
    if await_ready:
        frame["await_ready"] = True
    return frame


def queue_reply(sat, seconds: float) -> int:
    """A reply whose response_end has come (the core sends dropin_start
    after it) with `seconds` of speech still queued, being played by a
    stand-in for the playback thread. Returns the number of chunks."""
    n = round(seconds / 0.3)
    for i in range(n):
        sat.playback_q.put((22_050, b"reply-%d" % i))
    sat._playback_idle.clear()   # the stream is open
    sat._post_playback_state = "idle"  # response_end deferred until the drain
    return n


def play(sat, played: list, *, stuck: bool = False) -> threading.Thread:
    """The playback thread, minus the sound card: take each chunk, note
    whether the call was already open when it played, and close the stream
    (`_playback_idle`) once the queue is empty, as close_stream does."""

    def run() -> None:
        while True:
            try:
                _sr, data = sat.playback_q.get(timeout=0.5)
            except queue.Empty:
                break
            played.append((data, sat.dropin_active.is_set(), time.monotonic()))
            time.sleep(CHUNK_PLAY_SEC)
            if sat.playback_q.empty() and not stuck:
                sat._post_playback_state = None
                sat._playback_idle.set()
                break

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def wait_for(cond, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


def ready_frames(sat) -> list[float]:
    return [at for at, p in sat.sent if p.get("type") == "dropin_ready"]


def test_a_3s_reply_plays_out_before_the_call_opens(monkeypatch):
    sat = make_sat(monkeypatch)
    n = queue_reply(sat, 3.0)
    played: list = []
    player = play(sat, played)

    sat._handle_text_frame(start_frame(await_ready=True))
    # Not yet: the reply is still playing.
    assert not sat.dropin_active.is_set()
    assert sat._dropin_pending is not None
    assert ready_frames(sat) == []

    assert wait_for(sat.dropin_active.is_set)
    player.join(timeout=2)
    # All of it played, and all of it before the call took the speaker.
    assert [d for d, _open, _at in played] == [b"reply-%d" % i for i in range(n)]
    assert not any(opened for _d, opened, _at in played)
    # dropin_ready went out once, after the last chunk.
    assert wait_for(lambda: len(ready_frames(sat)) == 1)
    assert ready_frames(sat)[0] >= played[-1][2]
    # And the call is set up exactly as before.
    assert sat.audio_sample_rate == 16_000
    assert sat._prebuffer_active is False
    assert sat._leds.states[-1] == "dropin"
    assert sat._dropin_pending is None


def test_a_stuck_playback_holds_the_call_for_the_cap_only(monkeypatch):
    sat = make_sat(monkeypatch)
    queue_reply(sat, 0.3)
    played: list = []
    play(sat, played, stuck=True)  # never closes its stream
    began = time.monotonic()

    sat._handle_text_frame(start_frame(await_ready=True))
    assert wait_for(sat.dropin_active.is_set, timeout=3.0)
    waited = time.monotonic() - began
    cap = client.Satellite.DROPIN_REPLY_WAIT_SEC
    assert cap * 0.9 <= waited < cap + 1.0
    assert wait_for(lambda: len(ready_frames(sat)) == 1)


def test_nothing_playing_opens_the_call_at_once(monkeypatch):
    sat = make_sat(monkeypatch)
    sat._handle_text_frame(start_frame(await_ready=True))
    assert sat.dropin_active.is_set()
    assert [p["type"] for _at, p in sat.sent] == ["dropin_ready"]


def test_a_dropin_end_while_waiting_drops_the_call(monkeypatch):
    sat = make_sat(monkeypatch)
    queue_reply(sat, 1.5)
    played: list = []
    player = play(sat, played)
    sat._handle_text_frame(start_frame(await_ready=True))
    sat._handle_text_frame({"type": "dropin_end", "reason": "ended"})
    player.join(timeout=2)
    time.sleep(0.05)
    assert not sat.dropin_active.is_set()
    assert ready_frames(sat) == []
    assert sat._dropin_pending is None


def test_a_newer_dropin_start_replaces_one_still_waiting(monkeypatch):
    sat = make_sat(monkeypatch)
    queue_reply(sat, 0.9)
    played: list = []
    play(sat, played)
    sat._handle_text_frame(start_frame(await_ready=True))
    sat._handle_text_frame(start_frame(await_ready=True))
    assert wait_for(sat.dropin_active.is_set)
    time.sleep(0.05)
    assert len(ready_frames(sat)) == 1


def test_an_older_core_gets_todays_behaviour(monkeypatch):
    """No `await_ready`: the call opens on the frame, reply or not, and no
    `dropin_ready` goes to a core that would answer it with `error`."""
    sat = make_sat(monkeypatch)
    queue_reply(sat, 3.0)
    sat._handle_text_frame(start_frame(await_ready=False))
    assert sat.dropin_active.is_set()
    assert sat._dropin_pending is None
    assert ready_frames(sat) == []


def test_the_hello_declares_dropin_ready():
    src = (Path(client.__file__)).read_text(encoding="utf-8")
    assert '"dropin_ready": True,' in src


@pytest.mark.parametrize("busy", ["playback_active", "_prebuffer_active", "queued",
                                  "_post_playback_state", "stream_open"])
def test_every_stage_of_a_reply_counts_as_still_playing(monkeypatch, busy):
    sat = make_sat(monkeypatch)
    assert not sat._reply_still_playing()
    if busy == "playback_active":
        sat.playback_active.set()
    elif busy == "_prebuffer_active":
        sat._prebuffer_active = True
    elif busy == "queued":
        sat.playback_q.put((22_050, b"x"))
    elif busy == "_post_playback_state":
        sat._post_playback_state = "idle"
    else:
        sat._playback_idle.clear()
    assert sat._reply_still_playing()
