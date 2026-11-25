"""One turn per wake word, the satellite's side (rooms slice 4, 1.5.0).

docs/uml/satellite-protocol.md, "One turn per wake word", and the
`room_turns` block of docs/uml/satellite-fixtures.json; the core's side is
domovoi/wake_claims.py (domovoi/tests/test_wake_claims.py). Pinned here,
through the real `_wait_for_wake` → `_begin_utterance` path with a fake wake
model and a fake core answering the claims:

* the claim goes out only while the core lists `wake_claim` and the room
  says `claim: true`; otherwise the wake is exactly what it was;
* owner: the acknowledgement and the capture as before, `claim` on the
  `utterance_start`; no answer in 150 ms: the same (fail open);
* quiet: no chime, no music stop, the LED only fades; at most `stream_ms`
  of the command streamed, then `quiet_end`; `capture_keep` carries it on
  to the end of speech, `end_capture {claim}` stops it;
* stand-down (`stream_ms: 0`): nothing streamed, nothing sent;
* a `quiet` grant after the fail-open ends that capture;
* a wake over the room's answer is a claim with `barge_in: true`;
* the hello: a Pi's list names what it was always assumed to have, plus
  `wake_claim`.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from domovoi import satellite_abilities as sa
from satellite import wake_claim
from satellite.tests.test_wake_ack import (
    SILENCE,
    USER,
    WAKE,
    _Oww,
    _sat,
    client,
    settle,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURES = json.loads((REPO / "docs" / "uml" / "satellite-fixtures.json").read_text(encoding="utf-8"))
RT = FIXTURES["room_turns"]
#: A wake word, as the fake model hears one (a whole 80 ms chunk of it).
W = [WAKE] * 3
#: One heard mid-stream: long enough to fill a chunk however the frames fall.
W2 = [WAKE] * 6


# ─── the frames, against the shared fixtures ─────────────────────────────

def test_the_claim_frame_is_the_fixtures_claim():
    c = RT["claim"]
    assert wake_claim.claim_frame(c["claim"], c["word"], c["score"], c["floor_dbfs"]) == c
    b = RT["barge_in_claim"]
    assert wake_claim.claim_frame(b["claim"], b["word"], b["score"], b["floor_dbfs"],
                                  barge_in=True) == b
    # No floor: null, never NaN or -inf on the wire.
    assert wake_claim.claim_frame(1, "w", 0.5, float("-inf"))["floor_dbfs"] is None
    assert wake_claim.claim_frame(1, "w", 0.5, None)["floor_dbfs"] is None


def test_the_grants_decide_the_role():
    owner = wake_claim.decide(RT["grant_owner"])
    assert (owner.role, owner.failed_open, owner.turn) == (wake_claim.OWNER, False, "t1")
    quiet = wake_claim.decide(RT["grant_quiet"])
    assert (quiet.role, quiet.stream_ms) == (wake_claim.QUIET, RT["constants"]["stream_ms"])
    assert quiet.stream_frames == RT["constants"]["stream_ms"] // 30
    down = wake_claim.decide({**RT["grant_quiet"], "stream_ms": 0})
    assert down.role == wake_claim.STAND_DOWN
    assert wake_claim.decide(None).failed_open is True
    assert wake_claim.decide(None).role == wake_claim.OWNER
    # Garbage never makes a device stream for long, nor go deaf.
    assert wake_claim.decide({**RT["grant_quiet"], "stream_ms": 10**9}).stream_ms == wake_claim.MAX_STREAM_MS
    assert wake_claim.decide({**RT["grant_quiet"], "stream_ms": True}).role == wake_claim.STAND_DOWN
    assert wake_claim.decide({"role": "boss", "claim": 1}).role == wake_claim.OWNER


def test_the_constants_are_the_protocol_s():
    k = RT["constants"]
    assert round(wake_claim.FAIL_OPEN_SEC * 1000) == k["fail_open_ms"]
    assert round(wake_claim.COOLDOWN_SEC * 1000) == k["cooldown_ms"]
    assert wake_claim.ABILITY == RT["hello_supports_adds"] == RT["ready_features_adds"]


def test_the_room_says_claim_only_when_it_literally_does():
    assert wake_claim.room_says_claim(RT["ready_room"]) is True
    assert wake_claim.room_says_claim(FIXTURES["devices"]["pi_2mic_hat"]["ready"]["room"]) is False
    assert wake_claim.room_says_claim({"claim": "true"}) is False
    assert wake_claim.room_says_claim(None) is False


# ─── the book between the two threads ────────────────────────────────────

def test_a_claim_with_no_answer_fails_open_in_150_ms():
    book = wake_claim.ClaimBook()
    claim = book.open()
    t0 = time.monotonic()
    assert book.wait(claim) is None
    assert 0.13 <= time.monotonic() - t0 < 1.0


def test_the_receiver_s_grant_wakes_the_waiting_claim():
    book = wake_claim.ClaimBook()
    claim = book.open()
    threading.Timer(0.02, book.on_grant, args=({**RT["grant_owner"], "claim": claim},)).start()
    t0 = time.monotonic()
    assert book.wait(claim, timeout=2.0)["role"] == "owner"
    assert time.monotonic() - t0 < 1.0
    # One for another claim is not this one's.
    other = book.open()
    assert book.on_grant({**RT["grant_owner"], "claim": other + 5}) is None
    assert book.wait(other, timeout=0.05) is None


def test_a_quiet_answer_after_the_fail_open_revokes_the_turn():
    book = wake_claim.ClaimBook()
    claim = book.open()
    assert book.wait(claim, timeout=0.01) is None
    book.own(claim)
    assert book.on_grant({**RT["grant_owner"], "claim": claim}) is None  # late owner: nothing to do
    assert not book.revoked(claim)
    assert book.on_grant({**RT["grant_quiet"], "claim": claim}) == claim
    assert book.revoked(claim) and not book.revoked(claim + 1) and not book.revoked(None)


def test_keep_and_end_name_the_quiet_stream():
    book = wake_claim.ClaimBook()
    book.start_quiet(3)
    assert book.quiet_active
    assert not book.on_keep({"type": "capture_keep", "claim": 4})
    assert book.on_keep(RT["capture_keep"]) and book.kept
    assert not book.on_end_capture({"type": "end_capture", "claim": 2})
    assert book.on_end_capture(RT["end_capture_quiet"]) and book.stopped
    book.end_quiet()
    assert not book.quiet_active and not book.on_keep(RT["capture_keep"])


def test_a_session_end_releases_a_waiting_claim_and_stops_a_quiet_stream():
    book = wake_claim.ClaimBook()
    claim = book.open()
    threading.Timer(0.02, book.abort).start()
    t0 = time.monotonic()
    assert book.wait(claim, timeout=2.0) is None
    assert time.monotonic() - t0 < 1.0
    book.start_quiet(9)
    book.abort()
    assert book.stopped


# ─── the hello ────────────────────────────────────────────────────────────

def test_a_pi_lists_everything_it_was_assumed_to_have_plus_wake_claim():
    assert [*client.PI_SUPPORTS, "wake_claim"] == RT["pi_hello_supports"]
    hello = {**FIXTURES["devices"]["pi_2mic_hat"]["hello"], "supports": RT["pi_hello_supports"]}
    declared = sa.from_hello(hello)
    legacy = sa.from_hello(FIXTURES["devices"]["pi_2mic_hat"]["hello"])
    for ability in sa.ABILITIES:
        if ability == "wake_claim":
            assert declared.allows(ability) and not legacy.allows(ability)
        else:
            # Nothing a Pi got before is lost by naming it.
            assert declared.allows(ability) == legacy.allows(ability), ability
    assert declared.unknown == ()


def test_the_pi_hello_sends_the_list():
    sat = client.Satellite(client.Config.from_toml({"device": {"profile": "respeaker_2mic_hat"}}))
    hello = sat._hello_frame()
    assert hello["supports"][: len(RT["pi_hello_supports"])] == RT["pi_hello_supports"]


# ─── the wake, through the real loop ─────────────────────────────────────

class _Core(wake_claim.ClaimBook):
    """The book, with a core that answers each claim from ``answers`` (a
    grant dict without its claim number, or None for no answer) after
    ``delay`` seconds, as the receiver would."""

    def __init__(self, answers: list[dict | None], delay: float = 0.01) -> None:
        super().__init__()
        self.answers = list(answers)
        self.delay = delay

    def open(self) -> int:
        claim = super().open()
        answer = self.answers.pop(0) if self.answers else None
        if answer is not None:
            threading.Timer(self.delay, self.on_grant, args=({**answer, "claim": claim},)).start()
        return claim


OWNER = {"type": "wake_grant", "turn": "t1", "role": "owner"}


def _quiet(ms: int) -> dict:
    return {"type": "wake_grant", "turn": "t1", "role": "quiet", "stream_ms": ms}


def _claiming(monkeypatch, upcoming, answers, *, mode="chime", room_claim=True,
              features=frozenset({"wake_claim"}), music=False):
    sat, loop = _sat(monkeypatch, mode, upcoming, features=features, music=music)
    sat._room_claim = room_claim
    sat._claims = _Core(answers)
    sat._playback_idle = threading.Event()
    sat._playback_idle.set()
    sat.expect_followup = threading.Event()
    monkeypatch.setattr(wake_claim, "COOLDOWN_SEC", 0.0)
    # A wake that never comes fails the test instead of hanging it.
    watchdog = threading.Timer(10.0, sat.shutdown_event.set)
    watchdog.daemon = True
    watchdog.start()
    return sat, loop


def _sent(sat, loop) -> list:
    """What went to the core: text frames decoded, audio as "audio"."""
    loop.run_until_complete(settle())
    out = []
    while not sat.send_q.empty():
        kind, data = sat.send_q.get_nowait()
        out.append(json.loads(data) if kind == "text" else "audio")
    return out


def _texts(out: list, kind: str) -> list[dict]:
    return [m for m in out if m != "audio" and m["type"] == kind]


def _wake_and_start(sat, loop) -> list:
    try:
        assert sat._wait_for_wake(_Oww()) is True
        sat._begin_utterance("wake_word")
        return _sent(sat, loop)
    finally:
        loop.close()


@pytest.mark.parametrize("room_claim, features", [
    (False, frozenset({"wake_claim"})),   # a room with one satellite
    (True, frozenset()),                  # an older core
])
def test_no_claim_unless_the_core_and_the_room_both_say_so(monkeypatch, room_claim, features):
    sat, loop = _claiming(monkeypatch, [*W, USER], [OWNER], room_claim=room_claim, features=features)
    out = _wake_and_start(sat, loop)
    assert not _texts(out, "wake_claim")
    start = _texts(out, "utterance_start")[0]
    assert "claim" not in start and start["trigger"] == "wake_word"
    # The acknowledgement played, as always.
    assert ("spawn", "aplay") in sat.events


def test_the_owner_chimes_and_its_capture_carries_the_claim(monkeypatch):
    sat, loop = _claiming(monkeypatch, [*W, USER], [OWNER])
    out = _wake_and_start(sat, loop)
    (claim,) = _texts(out, "wake_claim")
    assert set(claim) == set(RT["claim"]) and claim["word"] == "hey_jarvis"
    assert claim["claim"] == 1 and claim["score"] == 1.0
    start = _texts(out, "utterance_start")[0]
    assert start["claim"] == 1
    assert set(RT["owner_utterance_start"]) <= set(start)
    assert out.index(claim) < out.index(start)
    assert ("spawn", "aplay") in sat.events
    assert "listening" in sat._leds.states and "quiet" not in sat._leds.states


def test_no_answer_in_150_ms_is_owning_the_turn(monkeypatch):
    sat, loop = _claiming(monkeypatch, [*W, USER], [None])
    t0 = time.monotonic()
    out = _wake_and_start(sat, loop)
    assert time.monotonic() - t0 >= 0.13
    assert _texts(out, "utterance_start")[0]["claim"] == 1
    assert ("spawn", "aplay") in sat.events


def test_a_quiet_listener_streams_at_most_stream_ms_then_quiet_end(monkeypatch):
    # 300 ms = 10 frames of the command, then back to listening: the next
    # wake word (after the cooldown) is a claim of its own, answered owner.
    upcoming = W + [USER] * 15 + [*W2, USER]
    sat, loop = _claiming(monkeypatch, upcoming, [_quiet(300), OWNER], music=True)
    out = _wake_and_start(sat, loop)
    first, second = _texts(out, "wake_claim")
    (end,) = _texts(out, "quiet_end")
    assert end == {"type": "quiet_end", "claim": first["claim"], "frames": 10, "reason": "limit"}
    assert set(end) == set(RT["quiet_end"])
    between = out[out.index(first) + 1:out.index(end)]
    assert between == ["audio"] * 10
    assert out.index(end) < out.index(second)
    # The quiet one never sent an utterance_start; the owner's carries ITS claim.
    (start,) = _texts(out, "utterance_start")
    assert start["claim"] == second["claim"] == 2
    # No chime and no music stop for the quiet claim: the first sound and
    # the music stop are the owner's, after the second claim.
    assert sat.events.index(("music-stopped", "mpg123")) > sat.events.index(("read", "wake"))
    assert [e for e in sat.events if e[0] == "spawn"] == [("spawn", "aplay")]
    # The LED faded, it never showed listening for the quiet claim.
    assert sat._leds.states[0] == "quiet"


def test_stand_down_streams_nothing(monkeypatch):
    upcoming = W + [USER] * 5 + [*W2, USER]
    sat, loop = _claiming(monkeypatch, upcoming, [_quiet(0), OWNER])
    out = _wake_and_start(sat, loop)
    first, second = _texts(out, "wake_claim")
    assert not _texts(out, "quiet_end")
    assert "audio" not in out[: out.index(second)]
    assert sat._leds.states[0] == "quiet"


def _quiet_with(monkeypatch, upcoming, on_frame):
    """A quiet stream where ``on_frame(sat, n)`` runs as frame n goes out."""
    sat, loop = _claiming(monkeypatch, upcoming, [_quiet(300), OWNER])
    sat.cfg.max_record_seconds = 5.0
    sat.cfg.silence_timeout = 0.3
    sat.cfg.vad_aggressiveness = 2
    sat.cfg.noise_gate_dbfs = -40.0
    inner = sat._emit_audio
    count = [0]

    def emit(frame: bytes) -> bool:
        count[0] += 1
        on_frame(sat, count[0])
        return inner(frame)

    sat._emit_audio = emit
    return sat, loop


def test_capture_keep_carries_the_stream_to_the_end_of_speech(monkeypatch):
    # Picked at frame 3: it goes on past 300 ms to its own silence timeout
    # (0.3 s = 10 silent frames after speech).
    upcoming = W + [USER] * 20 + [SILENCE] * 12 + [*W2, USER]

    def keep(sat, n):
        if n == 3:
            sat._handle_text_frame({**RT["capture_keep"], "claim": 1})

    sat, loop = _quiet_with(monkeypatch, upcoming, keep)
    out = _wake_and_start(sat, loop)
    (end,) = _texts(out, "quiet_end")
    assert end["reason"] == "vad_silence_after_speech"
    assert end["frames"] == 20 + 10


def test_end_capture_stops_the_quiet_stream(monkeypatch):
    upcoming = W + [USER] * 20 + [*W2, USER]

    def stop(sat, n):
        if n == 3:
            sat._handle_text_frame({**RT["end_capture_quiet"], "claim": 1})
            # One for some other claim is ignored.
            sat._handle_text_frame({**RT["end_capture_quiet"], "claim": 7})

    sat, loop = _quiet_with(monkeypatch, upcoming, stop)
    out = _wake_and_start(sat, loop)
    (end,) = _texts(out, "quiet_end")
    assert (end["reason"], end["frames"]) == ("stopped", 3)


def test_a_late_quiet_answer_ends_the_capture_it_revokes(monkeypatch):
    sat, loop = _claiming(monkeypatch, [*W, USER], [None])
    try:
        assert sat._wait_for_wake(_Oww()) is True
        assert sat._owner_claim == 1
        sat._begin_utterance("wake_word")
        assert sat._capture_claim == 1
        with sat._capture_lock:
            sat._capture_utt = sat._utt_seq
        sat._handle_text_frame({**RT["grant_quiet"], "claim": 1})
        assert sat._end_capture.is_set()
        assert sat._claims.revoked(1)
    finally:
        loop.close()


def test_a_late_quiet_answer_before_the_capture_skips_it(monkeypatch):
    sat, loop = _claiming(monkeypatch, [*W, USER], [None])
    try:
        assert sat._wait_for_wake(_Oww()) is True
        sat._handle_text_frame({**RT["grant_quiet"], "claim": 1})
        # No capture was running: nothing to end, the mic loop checks it.
        assert not sat._end_capture.is_set()
        assert sat._claims.revoked(sat._owner_claim)
    finally:
        loop.close()


def test_a_wake_while_this_device_plays_the_answer_says_barge_in(monkeypatch):
    sat, loop = _claiming(monkeypatch, [*W, USER], [OWNER])
    sat._playback_idle.clear()   # the reply is still draining out of the speaker
    out = _wake_and_start(sat, loop)
    assert _texts(out, "wake_claim")[0]["barge_in"] is True


def test_the_barge_in_over_the_reply_is_a_claim_not_a_barge_in_frame(monkeypatch):
    sat, loop = _claiming(monkeypatch, [], [OWNER])
    sat._drain_playback_q = lambda: None
    sat._stop_music = lambda **kw: None
    try:
        assert sat._claim_barge("hey_jarvis", {"hey_jarvis": 0.8}, [-40.0] * 100) == "barge"
        out = _sent(sat, loop)
    finally:
        loop.close()
    (claim,) = _texts(out, "wake_claim")
    assert set(claim) == set(RT["barge_in_claim"]) and claim["barge_in"] is True
    assert claim["score"] == 0.8 and claim["floor_dbfs"] == -40.0
    assert not _texts(out, "barge_in")
    (start,) = _texts(out, "utterance_start")
    assert (start["trigger"], start["claim"]) == ("barge_in", claim["claim"])
    assert sat.stop_playback.is_set()


def test_a_barge_in_answered_quiet_streams_and_goes_back_to_listening(monkeypatch):
    sat, loop = _claiming(monkeypatch, [USER] * 15, [_quiet(150)])
    sat._drain_playback_q = lambda: None
    sat._stop_music = lambda **kw: None
    sat.expect_followup.set()
    try:
        assert sat._claim_barge("hey_jarvis", {"hey_jarvis": 0.8}, []) == "done"
        out = _sent(sat, loop)
    finally:
        loop.close()
    assert not _texts(out, "utterance_start")
    assert _texts(out, "quiet_end")[0]["frames"] == 5
    assert not sat.expect_followup.is_set()


# ─── the room frames ─────────────────────────────────────────────────────

def test_the_room_frame_turns_claims_on_and_off(monkeypatch):
    sat, loop = _claiming(monkeypatch, [], [], room_claim=False)
    loop.close()
    sat._handle_text_frame({"type": "room", "room": RT["ready_room"]})
    assert sat._room_claim is True
    sat._handle_text_frame({"type": "room", "room": {**RT["ready_room"], "claim": False}})
    assert sat._room_claim is False


def test_the_client_handles_every_frame_the_core_sends_a_claiming_device():
    from satellite.tests.test_protocol_fixtures import _handled_frame_types

    handled = _handled_frame_types()
    for key in ("grant_owner", "grant_quiet", "capture_keep", "end_capture_quiet"):
        assert RT[key]["type"] in handled, key
    assert "room" in handled


def test_a_wake_while_the_room_playout_speaks_says_barge_in(monkeypatch):
    """1.5.0 slice 5b: the room's answer plays on the synced playout, not
    down this device's reply path; a wake over it is still a barge-in."""
    sat, loop = _claiming(monkeypatch, [*W, USER], [OWNER])
    sat._room_player = SimpleNamespace(speech_playing=lambda: True, playing=True)
    assert sat._reply_playing_here() is True
    out = _wake_and_start(sat, loop)
    assert _texts(out, "wake_claim")[0]["barge_in"] is True


def test_a_room_volume_waits_for_its_server_time(monkeypatch):
    sat, loop = _claiming(monkeypatch, [], [])
    loop.close()
    assert sat._room_control_delay(None) == 0.0               # today's frame: at once
    sat._room_player = SimpleNamespace(seconds_until=lambda at: None)
    assert sat._room_control_delay(123) == 0.0                # no clock yet: at once
    sat._room_player = SimpleNamespace(seconds_until=lambda at: 0.08)
    assert sat._room_control_delay(123) == pytest.approx(0.08)
    sat._room_player = SimpleNamespace(seconds_until=lambda at: 30.0)
    assert sat._room_control_delay(123) == 1.0                # a bad stamp holds nothing long
