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

And the turn a wake word opens over an announcement ("hey jarvis, stop the
timer" while the timer is being announced — a daily path since every timer
is announced in every room) waits for its OWN reply: the announcement's
end, whether still to be released when its audio drains or sent late and
interrupted by the core after that capture's utterance_start, no longer
ends that wait (which cost the reply its barge-in and its follow-up).
"""

from __future__ import annotations

import asyncio
import json
import threading
import types

import pytest

from satellite.tests.test_send_queue_lifecycle import FakeVad, client, fill_mic, make_sat

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
    sat._turn_mark = 0
    sat._turn_capturing = False
    # `_begin_utterance`
    sat._utt_seq = 0
    sat._utt_started = None
    sat._woke_at = None
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
    start = src.index('"type": "hello"')
    hello = src[start:src.index("json.dumps(hello)", start)]
    assert '"announce_after_session_end": True' in hello


# ─── a wake word over an announcement ─────────────────────────────────────


def _end(*, interrupted: bool = False, expect_followup: bool = False) -> str:
    return json.dumps({
        "type": "response_end", "interrupted": interrupted, "expect_followup": expect_followup,
    })


def _reply(text: str, *, expect_followup: bool = False) -> list:
    """A reply with no audio of its own (released at once, no drain)."""
    return [
        json.dumps({"type": "response_start", "text": text, "matched_handler": "timer",
                    "matched_path": "fast", "session_id": None, "online": True,
                    "audio_sample_rate": RATE}),
        _end(expect_followup=expect_followup),
    ]


def _wake(sat, trigger: str = "wake_word") -> None:
    """The mic thread opens a capture, as `_mic_thread_run` does."""
    sat._begin_utterance(trigger)
    sat.response_done.clear()
    sat.playback_active.clear()
    sat.stop_playback.clear()
    sat.expect_followup.clear()


def _speak_and_stop(sat, monkeypatch) -> None:
    """The person speaks and stops: the capture runs to its utterance_end."""
    monkeypatch.setattr(client, "webrtcvad", types.SimpleNamespace(Vad=FakeVad))
    fill_mic(sat)
    sat._stream_capture([])


def test_an_announcement_cut_by_the_wake_word_does_not_end_the_new_turns_wait(monkeypatch):
    """The review's repro: the timer's announcement is cut mid-send by a wake
    word; the core sends its interrupted response_end AFTER that capture's
    utterance_start. It used to set response_done during the capture, so the
    wait for the reply to "stop the timer" returned at once: no barge-in on
    the reply, and a reply that asked a question never opened its
    follow-up."""
    sat = receiver_sat()
    receive(sat, _announcement("From the garage: Your 10 minute timer is done.")[:-1])
    _wake(sat)
    receive(sat, [_end(interrupted=True)])
    assert not sat.response_done.is_set()
    _speak_and_stop(sat, monkeypatch)
    assert not sat.response_done.is_set()

    receive(sat, _reply("Which timer?", expect_followup=True))
    assert sat.response_done.is_set()
    assert sat.expect_followup.is_set()


def test_an_announcement_still_draining_does_not_end_the_new_turns_wait(monkeypatch):
    """The announcement's whole response arrived; its release waits for its
    audio to drain (close_stream). A wake word before then: the capture
    drops that release, so the drain ends nothing of this turn's."""
    sat = receiver_sat()
    receive(sat, _announcement())
    assert sat._post_playback_state == "idle"
    assert not sat.response_done.is_set()
    _wake(sat)
    assert sat._post_playback_state is None
    _speak_and_stop(sat, monkeypatch)
    assert not sat.response_done.is_set()
    receive(sat, _reply("Okay."))
    assert sat.response_done.is_set()


def test_a_late_whole_end_of_an_announcement_is_not_the_turns_either():
    """The announcement's response_end (not interrupted: its frames had all
    gone) arrives after the capture began: neither released now nor
    deferred to its drain, and its expect_followup (none here) is not
    taken as this turn's."""
    sat = receiver_sat()
    receive(sat, _announcement()[:-1])
    _wake(sat)
    receive(sat, [_end(expect_followup=True)])
    assert not sat.response_done.is_set()
    assert sat._post_playback_state is None
    assert not sat.expect_followup.is_set()


def test_the_barged_replys_interrupted_end_does_not_end_the_barge_capture():
    """The same race after a barge-in: the core cancels the reply the person
    talked over and sends its interrupted end during the barge capture."""
    sat = receiver_sat()
    receive(sat, _announcement("It is ten past three.")[:3])
    _wake(sat, "barge_in")
    receive(sat, [_end(interrupted=True)])
    assert not sat.response_done.is_set()


def test_a_blank_capture_is_still_ended_by_a_bare_response_end(monkeypatch):
    """The core ends a capture it heard nothing in with a response_end and
    no response_start: once the capture's utterance_end has gone, that is
    this turn's end."""
    sat = receiver_sat()
    _wake(sat)
    _speak_and_stop(sat, monkeypatch)
    receive(sat, [_end(interrupted=True)])
    assert sat.response_done.is_set()


def test_a_bare_end_after_the_core_ended_the_capture_is_the_turns():
    """An early commit (`end_capture` for this capture): the satellite may
    still be listening on, but the core has the turn — a bare response_end
    (a turn it dropped) ends this wait."""
    sat = receiver_sat()
    _wake(sat)
    sat._capture_utt = sat._utt_seq             # `_stream_capture` is running
    receive(sat, [json.dumps({"type": "end_capture", "utt": sat._utt_seq})])
    assert sat._end_capture.is_set()
    receive(sat, [_end(interrupted=True)])
    assert sat.response_done.is_set()
