"""Every turn that ends with no routed reply brings the music back.

Office, 2026-11-22 (1.4.0): music playing, a question ("what's the
weather for today?" → "want me to check online?" → "Yes."), and afterwards
the room was silent while the dashboard said "playing". The wake word (or a
barge-in) stops the satellite's player for the capture, and only a routed
turn's end decided the music again. Turns that ended any other way left it
off, with MPD playing to nobody:

* a capture dropped as blank, as the wake greeting coming back, or as the
  reply's own echo (`_clean_transcript`);
* the noisy-capture apology and the "speech recognition is down" notice;
* a turn that failed;
* and every barge-in turn among them: the interrupted reply makes no music
  decision because the barge-in turn's end makes it — so a barge-in that
  is then dropped (most often the speaker's own echo) must.

Pinned here, on real StreamSessions over a recording socket (the harness of
test_music_under_capture): each of those endings sends music_start for the
room's music; the two-turn "want me to check?" → "Yes." exchange ends with
the music back, also when a barge-in on the answer's tail is dropped as
echo; a dropped answer to a question releases the start the question held.
"""

from __future__ import annotations

import pytest

from domovoi import streaming
from domovoi.clients.whisper import SttUnavailableError
from domovoi.models import Response
from domovoi.tests.test_music_under_capture import (  # noqa: F401 — `room` is a fixture
    OFFER,
    URL,
    _settle,
    _until,
    room,
)

START = {"type": "music_start", "stream_url": URL}


@pytest.fixture
def heard(monkeypatch):
    """What speech recognition hears next: set ``heard.text``."""

    class _Whisper:
        text = "what does the fox say"

        async def transcribe(self, pcm):
            return _Whisper.text

        async def transcribe_wav_bytes(self, wav):
            return _Whisper.text

    w = _Whisper()
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: w)
    return _Whisper


def _after_last_end(room) -> list[dict]:
    texts = room.texts()
    last = max(i for i, f in enumerate(texts) if f["type"] == "response_end")
    return texts[last + 1:]


async def _played_and_ended(room) -> None:
    """A turn that started the music (the room's music is playing), then
    the satellite's record of it cleared, as if its player had been stopped
    by the next wake word."""
    room.reply = Response(text="It's noon.", matched_handler="time", matched_path="fast",
                          online=True)
    await room.turn()
    await _settle()
    assert room.starts() == [START]
    for entry in room.app.state.pending_music_start.values():
        entry["task"].cancel()
    room.app.state.pending_music_start.clear()
    room.ws.sent.clear()


# ─── each dropped ending resumes ──────────────────────────────────────────


async def test_a_blank_capture_brings_the_music_back(room, heard) -> None:
    heard.text = " . "
    await room.turn()
    await _settle()
    assert room.kinds()[-2:] == ["response_end", "music_start"]
    assert room.starts() == [START]


async def test_a_capture_of_only_the_wake_greeting_brings_the_music_back(room, heard) -> None:
    room.app.state.greeting_phrases = ["Back so soon?"]
    heard.text = "Back so soon."
    await room.start_capture("wake_word", 1)
    await room.frames(20)
    await room.sess._on_control({"type": "utterance_end", "utt": 1, "greeting_played": True})
    await room.sess._response_task
    await _settle()
    assert "transcript" not in room.kinds()
    assert room.starts() == [START]


async def test_a_barge_in_dropped_as_the_replys_echo_brings_the_music_back(room, heard) -> None:
    """The barge-in stopped the player; its capture was the reply coming
    back through the mic. The interrupted reply decides nothing, so the
    dropped barge-in turn must."""
    await _played_and_ended(room)
    room.sess._last_spoken_text = "It's noon."
    heard.text = "It's noon."
    await room.turn(trigger="barge_in", utt=2)
    await _settle()
    assert "transcript" not in room.kinds()
    assert room.starts() == [START]


async def test_a_barge_in_dropped_as_blank_brings_the_music_back(room, heard) -> None:
    await _played_and_ended(room)
    heard.text = ""
    await room.turn(trigger="barge_in", utt=2)
    await _settle()
    assert room.starts() == [START]


async def test_the_noisy_capture_apology_brings_the_music_back(room) -> None:
    await room.start_capture("wake_word", 1)
    await room.frames(10)
    await room.sess._on_control({"type": "noisy_capture"})
    await room.sess._response_task
    await _settle()
    assert "response_start" in room.kinds()            # the apology
    assert room.kinds()[-1] == "music_start"
    assert room.starts() == [START]


@pytest.mark.parametrize("trigger", ["wake_word", "barge_in"])
async def test_speech_recognition_down_still_brings_the_music_back(
    room, monkeypatch, trigger,
) -> None:
    def _down():
        raise SttUnavailableError("speech recognition did not load")

    monkeypatch.setattr(streaming, "get_whisper_client", _down)
    await room.turn(trigger=trigger)
    await _settle()
    assert room.kinds()[-1] == "music_start"
    assert room.starts() == [START]


async def test_a_turn_that_failed_brings_the_music_back(room) -> None:
    def _boom():
        raise RuntimeError("the database went away")

    room.reply = _boom
    await room.turn()
    await _settle()
    assert room.kinds()[-3:] == ["error", "response_end", "music_start"]


# ─── the question and its answer ──────────────────────────────────────────


async def test_want_me_to_check_then_yes_ends_with_the_music_back(room, heard) -> None:
    """#991 "what's the weather for today?" → "Want me to check that
    online?" (the start is held for the answer), #992 "Yes." → the answer,
    which asks nothing: the music comes back, once."""
    room.reply = OFFER
    await room.turn()
    await _settle(0.1)
    assert room.starts() == []                          # held for the answer
    heard.text = "Yes."
    room.reply = Response(text="It will be sunny, high of 20.", matched_handler="qa",
                          matched_path="qa", online=True)
    await room.turn(trigger="followup", utt=2)
    await _settle(0.1)
    assert room.kinds()[-2:] == ["response_end", "music_start"]
    assert room.starts() == [START]
    assert room.sess._music_hold_task is None


async def test_a_barge_in_on_the_answers_tail_dropped_as_echo_keeps_the_music(
    room, heard,
) -> None:
    """The sharpest chain the investigation found: the answer to "Yes." is
    long, barge-in fires on its tail after the core has sent its music_start,
    the satellite's barge stops that start before it plays, and the barge
    capture is the answer's own echo. Exactly one music_start after the
    drop."""
    room.reply = OFFER
    await room.turn()
    heard.text = "Yes."
    answer = "It will be sunny today, with a high of twenty degrees."
    room.reply = Response(text=answer, matched_handler="qa", matched_path="qa", online=True)
    await room.turn(trigger="followup", utt=2)
    await _settle(0.1)
    assert len(room.starts()) == 1
    room.ws.sent.clear()

    heard.text = "with a high of twenty degrees"
    await room.turn(trigger="barge_in", utt=3)
    await _settle(0.1)
    assert "transcript" not in room.kinds()             # dropped as echo
    assert room.starts() == [START]
    assert _after_last_end(room) == [START]


async def test_a_dropped_answer_to_a_question_releases_the_held_start(room, heard) -> None:
    """The question held the music for its answer; the "answer" is a blank
    capture. The held start goes out, once."""
    room.reply = OFFER
    await room.turn()
    assert room.sess._music_hold_task is not None
    heard.text = ""
    await room.turn(trigger="followup", utt=2)
    await _until(lambda: room.starts() != [])
    await _settle(0.1)
    assert room.starts() == [START]
    assert room.sess._music_hold_task is None


# ─── a reconnect keeps a held start ───────────────────────────────────────


async def test_a_start_held_when_the_socket_closed_is_sent_after_the_reconnect(room) -> None:
    """A Wi-Fi blip between the question and its answer: the closing
    socket used to drop the held start, and nothing sent music_start on
    reconnect. The next session for the room sends it."""
    from domovoi.tests.test_music_under_capture import ROOM, _Socket

    room.reply = OFFER
    await room.turn()
    assert room.sess._music_hold_task is not None
    room.app.state.active_sessions.pop(ROOM)
    room.sess._carry_held_music()                       # the closing socket
    room.sess._drop_music_hold()

    ws2 = _Socket(room.app)
    sess2 = streaming.StreamSession(ws2, ROOM)  # type: ignore[arg-type]
    room.app.state.active_sessions[ROOM] = sess2
    sess2._take_carried_music()                         # after its `ready`
    await _until(lambda: any(k == "text" and f["type"] == "music_start"
                             for _t, k, f in ws2.sent))
    assert [f for _t, k, f in ws2.sent if k == "text"] == [START]
    assert room.starts() == []
