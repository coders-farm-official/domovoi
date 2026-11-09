"""A satellite gives up on a request the core never answers.

2026-11-09: the core's speech recognition wedged, and every satellite sat
on the yellow "thinking" ring after its next wake word: the capture ended,
`utterance_end` went out, and the mic thread parked in
`_await_response_with_barge` for a `response_end` that never came. Nothing
but a dropped socket or a restart brought the room back.

`[listen] response_deadline_sec` (75 s by default, 0 = off) bounds that
wait: if NOTHING for the turn — transcript, response_start, reply audio,
response_end, error — arrives within it of the closing frame, the satellite
logs a warning, shows the error ring, plays the core's "no answer" line if
it has one, and listens for the wake word again. Pinned here:

* a core that never answers: the wait returns, error ring then idle, the
  clip helper called when the clip exists (and not when it doesn't), in
  both barge monitors (VAD and wake word), after a noisy capture too;
* a transcript (even one an early commit sends before the utterance_end)
  takes the deadline off for the rest of the turn; an announcement during
  the capture does not;
* 0 turns it off;
* the abandoned turn's late response_end does not end the next turn's wait
  (wherever it lands), is ignored at most once, and a late reply that does
  start still plays;
* the health sample's `awaiting_response_s`, the wake-stall self-check, and
  the config key.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import tomllib
import types
from pathlib import Path

import pytest

from satellite import health, remote_config
from satellite.tests.test_announce_delivery import (
    CHUNK,
    RATE,
    _announcement,
    _end,
    _reply,
    queued_audio,
    receive,
    receiver_sat,
)
from satellite.tests.test_send_queue_lifecycle import FakeVad, client, fill_mic

DEADLINE = 0.2


class FakeOww:
    def __init__(self) -> None:
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1

    def predict(self, chunk):
        return {}


@pytest.fixture
def loop():
    lp = asyncio.new_event_loop()
    yield lp
    lp.close()


@pytest.fixture(autouse=True)
def quick(monkeypatch, tmp_path):
    """No error-ring hold, no real clip, no real audio stack."""
    monkeypatch.setattr(client, "REPLY_DEADLINE_ERROR_HOLD_SEC", 0.0)
    monkeypatch.setattr(client, "webrtcvad", types.SimpleNamespace(Vad=FakeVad))
    monkeypatch.setattr(client, "_resolve_sound", lambda rel: tmp_path / "sounds" / rel)


def turn_sat(loop, *, deadline: float = DEADLINE, wake_barge: bool = False,
             backstop: float = 30.0):
    """A satellite with a session up, ready to run a turn and its wait."""
    sat = receiver_sat()
    sat.loop = loop
    sat.send_q = asyncio.Queue()
    sat.cfg.response_deadline_sec = deadline
    sat.cfg.response_backstop_sec = backstop
    sat.cfg.barge_in = False
    sat.cfg.barge_in_require_wake_word = wake_barge
    sat.cfg.vad_aggressiveness_during_tts = 3
    sat.cfg.barge_in_min_speech_ms = 250
    sat._played = []
    sat._play_canned_mp3 = lambda path, label: sat._played.append((path.name, label))
    return sat


def start_turn(sat) -> None:
    """Wake word, then the person speaks and stops: utterance_end goes out."""
    sat._begin_utterance("wake_word")
    sat.response_done.clear()
    sat.playback_active.clear()
    sat.stop_playback.clear()
    sat.expect_followup.clear()
    fill_mic(sat)
    assert sat._stream_capture([]) is True


def wait_bounded(sat, limit: float = 5.0) -> str:
    """The wait, in a thread: a deadline that does not fire fails the test
    instead of hanging the run."""
    th, out = wait_in_thread(sat)
    th.join(limit)
    if th.is_alive():
        sat.shutdown_event.set()
        th.join(2.0)
        pytest.fail("the wait did not end on its own: no reply deadline")
    return out["outcome"]


def wait_in_thread(sat):
    out: dict = {}

    def run():
        t0 = time.monotonic()
        out["outcome"] = sat._await_response_with_barge(FakeOww())
        out["took"] = time.monotonic() - t0

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th, out


# ─── a core that never answers ────────────────────────────────────────────


@pytest.mark.parametrize("wake_barge", [False, True], ids=["vad_barge", "wake_barge"])
def test_a_core_that_never_answers_ends_the_wait(loop, wake_barge, caplog):
    sat = turn_sat(loop, wake_barge=wake_barge)
    start_turn(sat)
    assert sat._leds.states[-1] == "thinking"
    caplog.set_level(logging.WARNING)
    t0 = time.monotonic()
    outcome = wait_bounded(sat)
    took = time.monotonic() - t0
    assert outcome == "done"
    assert DEADLINE * 0.5 <= took < DEADLINE + 2.0
    # The error ring, then back to idle (as the error-frame branch leaves it).
    assert sat._leds.states[-2:] == ["error", "idle"]
    assert sat.response_done.is_set()
    assert not sat.playback_active.is_set()
    assert sat._post_playback_state is None
    assert sat._reply_wait_since is None
    assert sat._abandoned_mark == sat._response_starts
    # No clip on this satellite: nothing played.
    assert sat._played == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("server never answered" in m for m in warnings), warnings


def test_the_no_answer_clip_plays_when_the_satellite_has_it(loop, tmp_path):
    sounds = tmp_path / "sounds"
    sounds.mkdir()
    (sounds / client.NO_ANSWER_CLIP).write_bytes(b"ID3")
    sat = turn_sat(loop)
    start_turn(sat)
    assert wait_bounded(sat) == "done"
    assert sat._played == [(client.NO_ANSWER_CLIP, "no-answer")]
    assert sat._leds.states[-1] == "idle"


def test_the_mic_thread_goes_back_to_wake_word_listening(loop):
    """Through the real inner loop of `_mic_thread_run`: 'done' with no
    follow-up armed leaves the turn (back to `_wait_for_wake`)."""
    sat = turn_sat(loop)
    start_turn(sat)
    outcome = wait_bounded(sat)
    assert outcome == "done" and not sat.expect_followup.is_set()


def test_a_noisy_capture_waits_for_its_apology_with_the_same_deadline(loop):
    sat = turn_sat(loop)
    sat.cfg.noise_gate_auto_calibrate = True
    sat.cfg.noise_gate_noisy_capture_dbfs = -100.0
    sat._maybe_recalibrate = lambda dbfs: None
    sat._begin_utterance("wake_word")
    fill_mic(sat, loud=40)
    assert sat._stream_capture([]) is True
    assert sat._reply_wait_since is not None, "noisy_capture armed the deadline"
    assert wait_bounded(sat) == "done"
    assert sat._leds.states[-2:] == ["error", "idle"]


def test_a_capture_with_no_session_does_not_arm_it(loop):
    sat = turn_sat(loop)
    sat.send_q = None
    sat._begin_utterance("wake_word")
    fill_mic(sat)
    assert sat._stream_capture([]) is False
    assert sat._reply_wait_since is None
    assert sat._awaiting_response_s(time.monotonic()) == 0.0


# ─── what takes the deadline off ──────────────────────────────────────────


def test_a_transcript_disarms_it_for_the_rest_of_the_turn(loop):
    sat = turn_sat(loop)
    start_turn(sat)
    receive(sat, [json.dumps({"type": "transcript", "text": "what is the weather"})])
    th, out = wait_in_thread(sat)
    th.join(DEADLINE * 4)
    assert th.is_alive(), "a slow reply after the transcript is not cut off"
    assert "error" not in sat._leds.states
    receive(sat, _reply("Sunny."))
    th.join(2.0)
    assert out["outcome"] == "done"
    assert sat._abandoned_mark is None


@pytest.mark.parametrize("frames", [
    [json.dumps({"type": "error", "message": "boom"})],
    [_end(interrupted=True)],
    _announcement("Here you go.")[:2],          # response_start + audio
], ids=["error", "bare_response_end", "response_start"])
def test_any_answer_disarms_it(loop, frames):
    sat = turn_sat(loop)
    start_turn(sat)
    receive(sat, frames)
    assert sat._reply_heard
    time.sleep(DEADLINE * 1.5)
    assert not sat._reply_deadline_due()


def test_an_early_commit_transcript_before_the_utterance_end_counts(loop):
    """The core ended the capture (`end_capture`) and transcribed at once;
    its transcript can reach the satellite while it is still listening on
    (`_listen_after_end`), before the utterance_end goes out."""
    sat = turn_sat(loop)
    sat._begin_utterance("wake_word")
    receive(sat, [json.dumps({"type": "transcript", "text": "pause the music"})])
    fill_mic(sat)
    assert sat._stream_capture([]) is True
    time.sleep(DEADLINE * 1.5)
    assert not sat._reply_deadline_due()
    assert sat._awaiting_response_s(time.monotonic()) == 0.0


def test_an_announcement_during_the_capture_is_not_the_answer(loop):
    """A timer announced while the person is still talking is not the
    core answering this capture."""
    sat = turn_sat(loop)
    sat._begin_utterance("wake_word")
    receive(sat, _announcement())
    fill_mic(sat)
    assert sat._stream_capture([]) is True
    assert not sat._reply_heard
    time.sleep(DEADLINE * 1.5)
    assert sat._reply_deadline_due()


def test_zero_turns_it_off(loop):
    sat = turn_sat(loop, deadline=0)
    start_turn(sat)
    th, out = wait_in_thread(sat)
    th.join(DEADLINE * 3)
    assert th.is_alive(), "with the deadline off the wait is as before"
    sat.shutdown_event.set()
    th.join(2.0)
    assert out["outcome"] == "shutdown"
    assert "error" not in sat._leds.states


# ─── late frames of the abandoned turn ────────────────────────────────────


def abandon_a_turn(loop):
    sat = turn_sat(loop)
    start_turn(sat)
    assert wait_bounded(sat) == "done"
    return sat


def test_its_late_response_end_does_not_end_the_next_turns_wait(loop):
    sat = abandon_a_turn(loop)
    sat.cfg.response_deadline_sec = 30.0
    start_turn(sat)                               # the next turn's utterance_end is out
    receive(sat, [_end(interrupted=True)])        # the abandoned turn's end, late
    assert not sat.response_done.is_set()
    assert sat._abandoned_mark is None, "spent: ignored at most once"
    assert not sat._reply_heard, "and it did not take the next turn's deadline off"
    receive(sat, _reply("It is three o'clock.", expect_followup=True))
    assert sat.response_done.is_set()
    assert sat.expect_followup.is_set()


def test_its_late_response_end_while_idle_is_spent_there(loop):
    """The late end lands before the next wake word: ignored, and the next
    turn's own bare end (a blank capture) then ends that turn as usual."""
    sat = abandon_a_turn(loop)
    receive(sat, [_end(interrupted=True)])
    assert sat._abandoned_mark is None
    start_turn(sat)
    receive(sat, [_end(interrupted=True)])
    assert sat.response_done.is_set()


def test_its_late_response_end_during_the_next_capture_is_ignored(loop):
    sat = abandon_a_turn(loop)
    sat._begin_utterance("wake_word")
    sat.response_done.clear()
    receive(sat, [_end(interrupted=True)])
    assert not sat.response_done.is_set()


def test_a_transcript_for_the_next_turn_clears_the_mark(loop):
    """The core never sent the abandoned turn's end. The next turn is
    transcribed and then dropped with a bare response_end: that end is the
    next turn's, and must end its wait (its deadline is already off)."""
    sat = abandon_a_turn(loop)
    start_turn(sat)
    receive(sat, [json.dumps({"type": "transcript", "text": "hello"}), _end(interrupted=True)])
    assert sat.response_done.is_set()


def test_a_late_reply_that_starts_still_plays(loop):
    sat = abandon_a_turn(loop)
    assert sat.stop_playback.is_set()
    receive(sat, [json.dumps({"type": "transcript", "text": "what time is it"}),
                  *_announcement("It is three o'clock.")])
    assert queued_audio(sat) == [(RATE, CHUNK)] * 6
    assert sat._abandoned_mark is None


# ─── the health sample and the wake-stall self-check ──────────────────────


def test_the_health_sample_says_how_long_a_reply_has_been_awaited(loop, tmp_path):
    from satellite.tests.test_health_client import health_sat

    sat = health_sat(tmp_path)
    assert sat._health_process_facts(100.0)["awaiting_response_s"] == 0.0
    sat._reply_wait_since = 90.0
    sat._reply_heard = False
    assert sat._health_process_facts(100.0)["awaiting_response_s"] == 10.0
    sat._reply_heard = True
    assert sat._health_process_facts(100.0)["awaiting_response_s"] == 0.0


def stall_sat(tmp_path):
    from satellite.tests.test_health_client import health_sat, sample

    sat = health_sat(tmp_path)
    sat._health_last_sample = sample(1)
    sat.ws = object()
    sat._session_ready_seen = True
    sat._playback_idle = threading.Event()
    sat._playback_idle.set()
    sat._turn_open = True
    sat._turn_capturing = False
    sat._wake_heartbeat = 0.0
    sat._turn_progress_at = 0.0
    sat._reply_wait_since = 0.0
    sat._reply_heard = False
    return sat


def ticks(sat, start: float, end: float) -> None:
    t = start
    while t <= end:
        sat._mic_frames_total += 1000
        sat._health_checks(t)
        t += 30.0


def test_connected_and_stuck_waiting_is_flagged(tmp_path, caplog):
    sat = stall_sat(tmp_path)
    caplog.set_level(logging.ERROR)
    ticks(sat, 30.0, 90.0)
    assert not [r for r in caplog.records if "wake word" in r.getMessage()]
    ticks(sat, 120.0, 150.0)
    msgs = [r.getMessage() for r in caplog.records if "not listening for the wake word" in r.getMessage()]
    assert len(msgs) == 1, msgs
    assert "waited 150s for the server to answer" in msgs[0]
    assert sat.fatal_error is None, "only flagged, never an exit"
    doc = json.loads((tmp_path / "last-health.json").read_text())
    assert doc["actions"][-1]["action"] == "flag"
    # Paced: said again only after ten minutes of the same stall.
    ticks(sat, 180.0, 600.0)
    msgs = [r.getMessage() for r in caplog.records if "not listening for the wake word" in r.getMessage()]
    assert len(msgs) == 1


@pytest.mark.parametrize("busy", ["playing", "draining", "capturing", "chat", "dropin", "ws_down"])
def test_a_long_answer_or_a_call_is_not_a_stall(tmp_path, caplog, busy):
    sat = stall_sat(tmp_path)
    if busy == "playing":
        sat.playback_active.set()
    elif busy == "draining":
        sat._playback_idle.clear()
    elif busy == "capturing":
        sat._turn_capturing = True
    elif busy == "chat":
        sat.chat_active.set()
    elif busy == "dropin":
        sat.dropin_active.set()
    elif busy == "ws_down":
        sat.ws = None
    caplog.set_level(logging.ERROR)
    ticks(sat, 30.0, 600.0)
    assert not [r for r in caplog.records if "not listening for the wake word" in r.getMessage()]


def test_a_turn_that_keeps_moving_is_not_a_stall(tmp_path, caplog):
    """A chain of follow-ups: the heartbeat is minutes old, but each frame
    from the core moves the turn on."""
    sat = stall_sat(tmp_path)
    caplog.set_level(logging.ERROR)
    t = 30.0
    while t <= 600.0:
        sat._turn_progress_at = t - 20.0
        sat._mic_frames_total += 1000
        sat._health_checks(t)
        t += 30.0
    assert not [r for r in caplog.records if "not listening for the wake word" in r.getMessage()]


def test_a_listening_wake_loop_is_not_a_stall():
    ci = health.CheckInput(now=1000.0, mic_expected=True, idle=True, wake_stall_for=0.1)
    assert not [d for d in health.decide(ci) if d.check == "wake_stall"]
    ci = health.CheckInput(now=1000.0, mic_expected=True, idle=False, wake_stall_for=121.0)
    assert [d.action for d in health.decide(ci) if d.check == "wake_stall"] == ["flag"]
    ci = health.CheckInput(now=1000.0, mic_expected=False, idle=False, wake_stall_for=500.0)
    assert not [d for d in health.decide(ci) if d.check == "wake_stall"]


# ─── the setting ──────────────────────────────────────────────────────────


def _cfg(**listen):
    doc = {"satellite": {"room_id": "den", "domovoi_url": "ws://x:6370"}}
    if listen:
        doc["listen"] = listen
    return client.Config.from_toml(doc)


def test_the_setting_defaults_to_sixty_and_takes_zero_for_off():
    assert _cfg().response_deadline_sec == 75.0
    assert _cfg(response_deadline_sec=0).response_deadline_sec == 0.0
    assert _cfg(response_deadline_sec=90).response_deadline_sec == 90.0
    assert _cfg().health_wake_stall_sec == 120.0


@pytest.mark.parametrize("bad", [-1, 601, "soon", True])
def test_a_bad_setting_fails_loud(bad):
    with pytest.raises(ValueError):
        _cfg(response_deadline_sec=bad)


def test_the_setting_is_documented_reported_and_settable():
    example = Path(client.__file__).resolve().parent / "config.toml.example"
    text = example.read_text(encoding="utf-8")
    assert "# response_deadline_sec = 75" in text
    assert "# response_backstop_sec = 240" in text
    assert tomllib.loads(text)["health"]["wake_stall_sec"] == 120
    assert "listen.response_deadline_sec" in remote_config.SETTABLE
    sat = object.__new__(client.Satellite)
    sat.cfg = _cfg(response_deadline_sec=45)
    assert sat._config_report()["listen.response_deadline_sec"] == 45.0


def test_the_backstop_setting_defaults_to_240_and_takes_zero_for_off():
    assert _cfg().response_backstop_sec == 240.0
    assert _cfg(response_backstop_sec=0).response_backstop_sec == 0.0
    assert _cfg(response_backstop_sec=900).response_backstop_sec == 900.0
    for bad in (-1, 901, "later", True):
        with pytest.raises(ValueError):
            _cfg(response_backstop_sec=bad)
    assert "listen.response_backstop_sec" in remote_config.SETTABLE
    sat = object.__new__(client.Satellite)
    sat.cfg = _cfg(response_backstop_sec=300)
    assert sat._config_report()["listen.response_backstop_sec"] == 300.0


# ─── a reconnect forgets the abandoned turn ───────────────────────────────


def test_after_a_core_restart_the_next_blank_captures_end_is_not_swallowed(loop):
    """The deadline fires, then the core restarts: the old session can
    never send that turn's late end, so the new session's first blank
    capture — ended by a bare response_end — must end its wait."""
    sat = abandon_a_turn(loop)
    assert sat._abandoned_mark is not None
    receive(sat, _announcement("Reminder."))      # counters move before the restart
    sat._abandoned_mark = sat._response_starts   # (as if abandoned after it)
    sat._on_session_ended()                       # the core went away
    assert sat._abandoned_mark is None
    assert sat._response_starts == sat._turn_mark == 0
    sat.send_q = asyncio.Queue()                  # a new session is accepted
    start_turn(sat)
    receive(sat, [_end(interrupted=True)])
    assert sat.response_done.is_set()


# ─── the backstop, once the core has answered something ───────────────────

BACKSTOP = 0.3


def test_the_backstop_fires_after_silence_following_a_transcript(loop, caplog):
    sat = turn_sat(loop, backstop=BACKSTOP)
    start_turn(sat)
    receive(sat, [json.dumps({"type": "transcript", "text": "tell me a story"})])
    caplog.set_level(logging.WARNING)
    assert wait_bounded(sat) == "done"
    assert sat._leds.states[-2:] == ["error", "idle"]
    assert sat.response_done.is_set()
    assert sat._abandoned_mark == sat._response_starts
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("stopped part-way through a turn" in m and "backstop" in m for m in msgs), msgs
    # The abandoned turn's late end does not end the next turn's wait.
    start_turn(sat)
    receive(sat, [_end(interrupted=True)])
    assert not sat.response_done.is_set()


def test_the_backstop_plays_the_no_answer_clip(loop, tmp_path):
    sounds = tmp_path / "sounds"
    sounds.mkdir()
    (sounds / client.NO_ANSWER_CLIP).write_bytes(b"ID3")
    sat = turn_sat(loop, backstop=BACKSTOP)
    start_turn(sat)
    receive(sat, [json.dumps({"type": "transcript", "text": "hi"})])
    assert wait_bounded(sat) == "done"
    assert sat._played == [(client.NO_ANSWER_CLIP, "no-answer")]


@pytest.mark.parametrize("busy", ["playing", "draining", "music", "chat", "dropin", "wake_recording"])
def test_the_backstop_never_fires_while_the_room_is_busy(loop, busy):
    sat = turn_sat(loop, backstop=BACKSTOP)
    start_turn(sat)
    receive(sat, [json.dumps({"type": "transcript", "text": "play something"})])
    if busy == "playing":
        sat.playback_active.set()
    elif busy == "draining":
        sat._playback_idle.clear()
    elif busy == "music":
        sat._is_music_playing = lambda: True
    elif busy == "chat":
        sat.chat_active.set()
    elif busy == "dropin":
        sat.dropin_active.set()
    else:
        sat.wake_recording.set()
    th, out = wait_in_thread(sat)
    th.join(BACKSTOP * 4)
    assert th.is_alive(), f"the backstop cut off a wait while {busy}"
    assert "error" not in sat._leds.states
    # Busy time never counts: once the room is quiet the full backstop runs.
    t0 = time.monotonic()
    sat.playback_active.clear()
    sat._playback_idle.set()
    sat._is_music_playing = lambda: False
    sat.chat_active.clear()
    sat.dropin_active.clear()
    sat.wake_recording.clear()
    th.join(5.0)
    assert not th.is_alive() and out["outcome"] == "done"
    # (Busy time is stamped once per wait pass, ~0.1 s apart.)
    assert time.monotonic() - t0 >= BACKSTOP * 0.5


def test_each_frame_starts_the_backstop_again(loop):
    sat = turn_sat(loop, backstop=BACKSTOP)
    start_turn(sat)
    receive(sat, [json.dumps({"type": "transcript", "text": "a long one"})])
    th, out = wait_in_thread(sat)
    t_end = time.monotonic() + BACKSTOP * 4
    while time.monotonic() < t_end:
        time.sleep(BACKSTOP / 3)
        receive(sat, [json.dumps({"type": "transcript", "text": "still going"})])
    assert th.is_alive(), "a turn the core keeps answering is not given up"
    th.join(5.0)
    assert out["outcome"] == "done"


def test_a_backstop_of_zero_is_off(loop):
    sat = turn_sat(loop, backstop=0)
    start_turn(sat)
    receive(sat, [json.dumps({"type": "transcript", "text": "hello"})])
    th, out = wait_in_thread(sat)
    th.join(1.0)
    assert th.is_alive()
    sat.shutdown_event.set()
    th.join(2.0)
    assert out["outcome"] == "shutdown"
    assert "error" not in sat._leds.states


def test_the_first_deadline_does_not_wait_for_the_backstop(loop):
    """Before any frame, only response_deadline_sec applies."""
    sat = turn_sat(loop, deadline=DEADLINE, backstop=30.0)
    start_turn(sat)
    t0 = time.monotonic()
    assert wait_bounded(sat) == "done"
    assert time.monotonic() - t0 < 5.0


# ─── the core's `working` frames ──────────────────────────────────────────


def _working(stage: str = "thinking it over") -> str:
    return json.dumps({"type": "working", "stage": stage})


def test_a_working_frame_is_the_cores_first_answer(loop):
    sat = turn_sat(loop)
    start_turn(sat)
    receive(sat, [_working("transcribing")])
    assert sat._reply_heard
    time.sleep(DEADLINE * 1.5)
    assert not sat._reply_deadline_due()
    assert sat._awaiting_response_s(time.monotonic()) == 0.0


def test_working_frames_keep_the_backstop_off(loop):
    sat = turn_sat(loop, backstop=BACKSTOP)
    start_turn(sat)
    receive(sat, [json.dumps({"type": "transcript", "text": "plan my week"})])
    th, out = wait_in_thread(sat)
    t_end = time.monotonic() + BACKSTOP * 4
    while time.monotonic() < t_end:
        time.sleep(BACKSTOP / 3)
        receive(sat, [_working("asking the model")])
    assert th.is_alive(), "a core that says it is still working is not given up on"
    th.join(5.0)          # then it goes silent: the backstop fires
    assert out["outcome"] == "done"


def test_a_working_frame_is_quiet_and_clears_the_abandoned_mark(loop, caplog):
    sat = abandon_a_turn(loop)
    caplog.set_level(logging.INFO)
    start_turn(sat)
    receive(sat, [_working(), _end(interrupted=True)])
    assert sat.response_done.is_set(), "the end after `working` is this turn's"
    assert not [r for r in caplog.records
                if r.levelno >= logging.INFO and "working" in r.getMessage()]


def test_a_laptop_declares_turn_progress():
    from satellite import pc_engine

    assert "turn_progress" in pc_engine.PC_SUPPORTS
