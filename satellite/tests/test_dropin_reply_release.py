"""A reply said during a drop-in releases the mic thread, and the ring stays
the call's red.

During a call the playback thread keeps its output stream open for the
other room's audio, so a reply's `response_end` that defers `response_done`
until the stream drains (it had audio) is never released: the mic thread
sat in its wait for the rest of the call, sent the room nothing and heard
no wake word, so a spoken "hang up" could not end the call. The core now
follows every reply said in a call with a silent response (a
`response_start` with no audio, at the call's 16 kHz, then its end;
domovoi/streaming.py, `_release_reply_wait_in_call`), and holds the call's
audio off this room until it has gone out. That core fix works on every
satellite, upgraded or not.

The red ring is the live-microphone indicator: while a call is up the
room's mic is open to another room, and the ring must say so. A reply
said in a call used to end on idle, so the satellite change here ships
with the core's (an Upgrade is recommended).

Pinned here, through the real `_receiver_loop`, `_handle_text_frame` and
`_handle_binary_frame`, with and without the receiver's prebuffer:

* the reply's own end alone leaves the wait unreleased (the wedge);
* the silent response after it releases it, and the speaker is back at the
  call's rate;
* one binary frame between the silent start and end (call audio the core
  let through) defers that end too — why the core holds the relay;
* the ring goes back to the call's red after a reply in a call, never to
  idle while the call is up.
"""

from __future__ import annotations

import json

import pytest

from satellite.tests.test_announce_delivery import CHUNK, RATE, receive, receiver_sat

PREBUFFER = pytest.mark.parametrize("prebuffer_sec", [0.0, 0.5])


def _reply(text: str = "Okay, I'll stop there.") -> list:
    return [
        json.dumps({
            "type": "response_start", "text": text, "matched_handler": "voice_profile",
            "matched_path": "confirmation", "session_id": None, "online": True,
            "audio_sample_rate": RATE,
        }),
        CHUNK, CHUNK, CHUNK,
        json.dumps({"type": "response_end", "interrupted": False, "expect_followup": False}),
    ]


SILENT_START = json.dumps({
    "type": "response_start", "text": "", "matched_handler": "dropin",
    "matched_path": "system", "online": True, "audio_sample_rate": 16_000,
})
SILENT_END = json.dumps({"type": "response_end", "interrupted": False, "expect_followup": False})


def _in_call(prebuffer_sec: float):
    sat = receiver_sat(prebuffer_sec=prebuffer_sec)
    sat.dropin_active.set()
    sat._dropin_rate = 16_000
    return sat


@PREBUFFER
def test_a_reply_in_a_call_alone_never_releases_the_wait(prebuffer_sec) -> None:
    sat = _in_call(prebuffer_sec)
    receive(sat, _reply())
    assert not sat.response_done.is_set()
    assert sat._post_playback_state == "idle"     # waiting on a drain that never comes


@PREBUFFER
def test_the_silent_response_releases_it(prebuffer_sec) -> None:
    sat = _in_call(prebuffer_sec)
    receive(sat, [*_reply(), SILENT_START, SILENT_END])
    assert sat.response_done.is_set()
    assert sat._post_playback_state is None
    assert sat.audio_sample_rate == 16_000         # the call's rate for the relay
    # The reply's audio is still queued to play out.
    assert [c for _r, c in list(sat.playback_q.queue)].count(CHUNK) == 3


@PREBUFFER
def test_call_audio_between_the_silent_start_and_end_breaks_it(prebuffer_sec) -> None:
    """Why the core holds the relay into a room until its reply and the
    silent response have gone out."""
    sat = _in_call(prebuffer_sec)
    receive(sat, [*_reply(), SILENT_START, bytes(960), SILENT_END])
    assert not sat.response_done.is_set()


@PREBUFFER
def test_the_ring_goes_back_to_red_after_a_reply_in_a_call(prebuffer_sec) -> None:
    sat = _in_call(prebuffer_sec)
    receive(sat, [*_reply(), SILENT_START, SILENT_END])
    assert sat._leds.states[-1] == "dropin"
    assert "idle" not in sat._leds.states


@PREBUFFER
def test_outside_a_call_a_released_reply_still_goes_idle(prebuffer_sec) -> None:
    sat = receiver_sat(prebuffer_sec=prebuffer_sec)
    receive(sat, [SILENT_START, SILENT_END])
    assert sat.response_done.is_set()
    assert sat._leds.states[-1] == "idle"
