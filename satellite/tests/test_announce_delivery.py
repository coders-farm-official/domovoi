"""An announcement the core sends an idle satellite reaches its speaker.

Reminders, timers, intercom broadcasts and the dashboard's announce all
arrive the same way: `response_start`, the TTS PCM as binary frames, then
`response_end`, with no utterance of the satellite's own in flight
(`StreamSession.announce`, domovoi/streaming.py). The receiver drops every
binary frame while `stop_playback` is set, which is right for the tail of a
reply the person has just talked over, but `stop_playback` is also set when
a session ends (`_on_session_ended`) and when the core sends `error`, and
the only things that cleared it were the mic thread starting a turn and the
playback thread taking a chunk off the queue. The receiver never queued
one while the flag was up, so after any reconnect (a core restart, a Wi-Fi
flap) or any failed turn, every announcement arrived as a `response_start`
and a `response_end` with nothing in between, silently, until somebody said
the wake word in that room. The core counted each one as delivered.

Pinned here, through the real `_receiver_loop` and `_handle_text_frame`:

* an announcement to a satellite that has never had a turn plays (the
  control: this always worked);
* one after a session ended and a new one came up plays;
* one after an `error` frame plays;
* one that lands while a wake acknowledgement is playing stops the
  acknowledgement and plays;
* the barge-in drop still works: late audio of a reply that was talked over,
  with no new `response_start`, is still discarded.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from satellite.tests.test_send_queue_lifecycle import client, make_sat

RATE = 22_050
CHUNK = bytes(4096)          # one binary frame of TTS PCM from the core
N_CHUNKS = 6


def _announcement(text: str = "Reminder: check the oven") -> list:
    """The frames `StreamSession.announce` sends, in order."""
    return [
        json.dumps({
            "type": "response_start",
            "text": text,
            "matched_handler": "intercom",
            "matched_path": "intercom_broadcast",
            "session_id": None,
            "online": True,
            "audio_sample_rate": RATE,
        }),
        *([CHUNK] * N_CHUNKS),
        json.dumps({"type": "response_end", "interrupted": False, "expect_followup": False}),
    ]


class FakeWS:
    """The core's side of the socket: yields ``frames`` once, then closes."""

    def __init__(self, frames: list) -> None:
        self._frames = list(frames)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


class FakeAckPlayer:
    """A wake greeting / chime player that is still running."""

    def __init__(self) -> None:
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.terminate()

    def wait(self, timeout=None):
        return self.returncode


def receiver_sat(*, prebuffer_sec: float = 0.0):
    """A satellite with what the receiver and the response frames touch."""
    sat = make_sat()
    sat.cfg.tts_prebuffer_sec = prebuffer_sec
    sat.expect_followup = threading.Event()
    sat._playback_idle = threading.Event()
    sat._playback_idle.set()
    sat._response_audio_received = threading.Event()
    sat._prebuffer_active = False
    sat._prebuffer_buffer = []
    sat._prebuffer_accumulated = 0
    sat._prebuffer_target_bytes = 0
    sat._DROPIN_MAX_PLAYBACK_FRAMES = 10
    sat.audio_sample_rate = 16_000
    sat._music_lock = threading.Lock()
    sat._music_proc = None
    sat._ack_lock = threading.Lock()
    sat._ack_proc = None
    # Counted on every response_start (Satellite.__init__ sets it).
    sat._response_starts = 0
    return sat


def receive(sat, frames: list) -> None:
    sat.ws = FakeWS(frames)
    asyncio.run(sat._receiver_loop())


def queued_audio(sat) -> list:
    out = []
    while not sat.playback_q.empty():
        out.append(sat.playback_q.get_nowait())
    return out


@pytest.mark.parametrize("prebuffer_sec", [0.0, 0.5])
def test_an_announcement_to_an_idle_satellite_plays(prebuffer_sec):
    sat = receiver_sat(prebuffer_sec=prebuffer_sec)
    receive(sat, _announcement())
    audio = queued_audio(sat)
    assert audio == [(RATE, CHUNK)] * N_CHUNKS
    assert sat._leds.states[0] == "speaking"


@pytest.mark.parametrize("prebuffer_sec", [0.0, 0.5])
def test_an_announcement_after_a_reconnect_plays(prebuffer_sec):
    """The core restarted (or the Wi-Fi dropped) and the satellite came
    back: the first thing the core sends is a reminder, before anyone has
    spoken to this room."""
    sat = receiver_sat(prebuffer_sec=prebuffer_sec)
    sat._on_session_ended()           # the old socket went away
    assert sat.stop_playback.is_set()
    receive(sat, _announcement())     # the new session: a reminder fires
    audio = queued_audio(sat)
    assert audio == [(RATE, CHUNK)] * N_CHUNKS, (
        "the reminder's audio was dropped: the satellite showed 'speaking' "
        "and said nothing"
    )


def test_an_announcement_after_a_server_error_plays():
    """A turn that failed ends with `error` + `response_end`; the next
    announcement in that room must still be heard."""
    sat = receiver_sat()
    receive(sat, [
        json.dumps({"type": "error", "message": "TTS unavailable"}),
        json.dumps({"type": "response_end", "interrupted": True, "expect_followup": False}),
        *_announcement(),
    ])
    assert queued_audio(sat) == [(RATE, CHUNK)] * N_CHUNKS


def test_an_announcement_during_a_wake_acknowledgement_stops_it_and_plays():
    """ack_mode greeting / chime: the mic thread is blocked on the player
    when the reminder arrives. The receiver runs on its own thread, stops
    the player and plays the reminder. (ack_mode none has no player.)"""
    sat = receiver_sat()
    player = FakeAckPlayer()
    sat._ack_proc = player
    receive(sat, _announcement())
    assert player.terminated, "the greeting is cut so the reminder is heard"
    assert sat._ack_proc is None
    assert queued_audio(sat) == [(RATE, CHUNK)] * N_CHUNKS


def test_the_tail_of_a_reply_that_was_talked_over_is_still_dropped():
    """The barge-in drop is what `stop_playback` in the receiver is for:
    audio of the reply the person interrupted that was already on the wire.
    No new `response_start` has arrived, so it is the old reply's tail."""
    sat = receiver_sat()
    receive(sat, _announcement()[:3])      # a reply starts playing
    queued_audio(sat)
    sat.stop_playback.set()                # barge-in (mic thread)
    receive(sat, [CHUNK, CHUNK, CHUNK])    # its tail, still arriving
    assert queued_audio(sat) == []


def test_a_reply_after_a_barge_in_plays():
    """...and the reply to what the person said over it plays, even when it
    starts before the mic thread has come round to clear the flag."""
    sat = receiver_sat()
    sat.stop_playback.set()
    receive(sat, _announcement("Here is your answer."))
    assert queued_audio(sat) == [(RATE, CHUNK)] * N_CHUNKS


def test_the_hello_says_it_has_the_announcement_fix() -> None:
    """The core cannot hear whether an announcement played. A satellite
    without the fix above plays silence after every reconnect while the
    core records it spoken; this flag lets the core say so in its journal
    for the rooms that still need the upgrade."""
    import inspect

    src = inspect.getsource(client.Satellite._run_session)
    hello = src[src.index('"type": "hello"'):src.index("}))", src.index('"type": "hello"'))]
    assert '"announce_after_session_end": True' in hello
