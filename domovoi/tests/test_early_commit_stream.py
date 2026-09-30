"""Early commit over the real socket: the core ends a capture itself when
what it has heard is a whole command, and only then.

Early endpointing, part B (design notes 2026-09-28). The protocol pieces:
`hello.capture_control` (the satellite honours `end_capture`),
`ready.features` (what the core understands), `utterance_start.utt` (so a
late `end_capture` can't end the next capture), and `end_capture {utt}`
itself. These drive `/v1/stream` with a Whisper stub (see
test_speculative_stt.py) and check each condition the design puts on an
early commit: the satellite declared it, the trigger is a wake word or a
follow-up, the transcript is a tier-A/B fast path (or a whole yes/no to a
parked question), the satellite's own detector has been silent for the
hold since the last voiced frame, and every voiced frame is inside the
transcribed copy. Old satellites never get `end_capture`.
"""

from __future__ import annotations

import json
import logging
import time
import types

import pytest
from fastapi.testclient import TestClient

from domovoi.config import settings
from domovoi.main import app
from domovoi.streaming import CORE_FEATURES, StreamSession
from domovoi.tests.test_speculative_stt import (  # noqa: F401 - `pipeline` is a fixture
    LOUD,
    QUIET,
    _end,
    _finish_turn,
    _pause,
    _send,
    _WatchedWhisper,
    pipeline,
)

HOLD_A_FRAMES = 12      # 360 ms: the first frame count past 350 ms
HOLD_B_FRAMES = 22      # 660 ms: past 650 ms


@pytest.fixture
def commit_on(pipeline, monkeypatch):
    monkeypatch.setattr(settings, "early_commit_enabled", True)
    monkeypatch.setattr(settings, "early_commit_tier_b", True)
    monkeypatch.setattr(settings, "early_commit_hold_a_ms", 350)
    monkeypatch.setattr(settings, "early_commit_hold_b_ms", 650)
    timings: list = []

    async def _route(intent, ctx, s):
        from domovoi.models import Response

        pipeline["routed"].append((intent.transcript, ctx.timings.row_document()))
        timings.append(ctx.timings)
        return Response(text="Done.", session_id=None, matched_handler="music",
                        matched_path="fast", online=True)

    monkeypatch.setattr("domovoi.streaming.route", _route)
    pipeline["timings"] = timings
    return pipeline


def _hello(ws, *, capture_control: bool = True, hints: bool = True) -> dict:
    msg = {"type": "hello", "room_id": "kitchen"}
    if hints:
        msg["speech_pause"] = True
    if capture_control:
        msg["capture_control"] = True
    ws.send_text(json.dumps(msg))
    ready = ws.receive_json()
    assert ready["type"] == "ready"
    return ready


def _session() -> StreamSession:
    return app.state.active_sessions["kitchen"]


def _barrier(ws) -> None:
    """Everything sent before this has been read by the server."""
    ws.send_text(json.dumps({"type": "ping"}))
    assert ws.receive_json() == {"type": "pong"}


def _speak_then_pause(ws, *, utt: int = 1, trigger: str = "wake_word", voiced: int = 20) -> int:
    ws.send_text(json.dumps({"type": "utterance_start", "trigger": trigger, "utt": utt}))
    _send(ws, [LOUD] * voiced + [QUIET] * 8)
    ws.send_text(_pause(utt, voiced + 8, voiced - 1))
    time.sleep(0.2)          # the copy's decode (50 ms) finishes
    return voiced + 8


def test_ready_lists_end_capture(commit_on) -> None:
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        ready = _hello(ws)
    assert ready["features"] == list(CORE_FEATURES) == ["speech_pause", "end_capture"]


def test_a_closed_command_ends_the_capture_after_the_short_hold(commit_on, caplog) -> None:
    caplog.set_level(logging.INFO, logger="domovoi.streaming")
    whisper = commit_on["whisper"] = _WatchedWhisper("Pause the music.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        frames = _speak_then_pause(ws)
        # 11 silent frames (330 ms): not yet.
        _send(ws, [QUIET] * (HOLD_A_FRAMES - 8 - 1))
        _barrier(ws)
        assert _session().utterance_active
        # The 12th (360 ms): the core stops listening and answers.
        _send(ws, [QUIET])
        frames += HOLD_A_FRAMES - 8
        assert ws.receive_json() == {"type": "end_capture", "utt": 1}
        assert _finish_turn(ws) == "Pause the music."
        # The satellite's own utterance_end, sent on the server_endpoint
        # exit: read, not answered.
        ws.send_text(json.dumps({
            "type": "utterance_end", "greeting_played": False, "utt": 1,
            "frames": frames, "last_voiced_frame": 19, "exit_reason": "server_endpoint",
        }))
        _barrier(ws)

    assert whisper.calls == [28 * 960]
    (_, doc), = commit_on["routed"]
    assert doc["early_commit"] == "A"
    assert doc["early_commit_hold_ms"] == 350
    assert doc["endpoint_silence_ms"] == HOLD_A_FRAMES * 30
    assert doc["stt_reused"] is True
    (timings,) = commit_on["timings"]
    assert timings.stages["post_commit_voiced_ms"] == 0
    assert any("early commit room=kitchen" in r.getMessage() and "tier=A" in r.getMessage()
               for r in caplog.records)
    assert not any("cut in on speech" in r.getMessage() for r in caplog.records)


def test_a_timer_waits_the_longer_hold(commit_on) -> None:
    commit_on["whisper"] = _WatchedWhisper("Set a timer for 10 minutes.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        _speak_then_pause(ws)
        _send(ws, [QUIET] * (HOLD_B_FRAMES - 8 - 1))
        _barrier(ws)
        assert _session().utterance_active, "tier B does not stop on the short hold"
        _send(ws, [QUIET])
        assert ws.receive_json() == {"type": "end_capture", "utt": 1}
        assert _finish_turn(ws) == "Set a timer for 10 minutes."
    (_, doc), = commit_on["routed"]
    assert doc["early_commit"] == "B" and doc["early_commit_hold_ms"] == 650


def test_with_tier_b_off_a_timer_waits_for_the_satellite(commit_on, monkeypatch) -> None:
    monkeypatch.setattr(settings, "early_commit_tier_b", False)
    commit_on["whisper"] = _WatchedWhisper("Set a timer for 10 minutes.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        _speak_then_pause(ws)
        _send(ws, [QUIET] * 32)
        _barrier(ws)
        assert _session().utterance_active
        ws.send_text(_end(1, frames=60, last=19))
        assert _finish_turn(ws) == "Set a timer for 10 minutes."
    (_, doc), = commit_on["routed"]
    assert "early_commit" not in doc and doc["stt_reused"] is True


def test_with_early_commit_off_nothing_ends_early(commit_on, monkeypatch) -> None:
    monkeypatch.setattr(settings, "early_commit_enabled", False)
    commit_on["whisper"] = _WatchedWhisper("Pause the music.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        _speak_then_pause(ws)
        _send(ws, [QUIET] * 32)
        ws.send_text(_end(1, frames=60, last=19))
        assert _finish_turn(ws) == "Pause the music."
    (_, doc), = commit_on["routed"]
    assert "early_commit" not in doc


def test_a_satellite_that_did_not_declare_capture_control_never_gets_end_capture(commit_on) -> None:
    """An old satellite (or one whose room opted out) ignores end_capture
    and keeps capturing — the reply would play into an open microphone. So
    it is never sent one; it still gets the speculative transcript."""
    commit_on["whisper"] = _WatchedWhisper("Pause the music.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws, capture_control=False)
        _speak_then_pause(ws)
        _send(ws, [QUIET] * 32)
        ws.send_text(_end(1, frames=60, last=19))
        # The first thing back is the transcript: no end_capture before it.
        assert _finish_turn(ws) == "Pause the music."
    (_, doc), = commit_on["routed"]
    assert "early_commit" not in doc and doc["stt_reused"] is True


def test_capture_control_without_pause_reports_is_not_enough(commit_on) -> None:
    """The hold is measured on the satellite's own detector; a client that
    doesn't report its pauses gives the core nothing exact to hold on."""
    commit_on["whisper"] = _WatchedWhisper("Pause the music.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws, hints=False)
        ws.send_text(json.dumps({"type": "config_status", "config": {"listen.silence_timeout": 1.2}}))
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
        _send(ws, [LOUD] * 20 + [QUIET] * 40)
        ws.send_text(_end())
        assert _finish_turn(ws) == "Pause the music."
    (_, doc), = commit_on["routed"]
    assert "early_commit" not in doc and doc["stt_reused"] is True


@pytest.mark.parametrize("trigger", ["chat", "barge_in", "push_to_talk"])
def test_only_wake_word_and_follow_up_turns_end_early(commit_on, trigger) -> None:
    commit_on["whisper"] = _WatchedWhisper("Pause the music.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        _speak_then_pause(ws, trigger=trigger)
        _send(ws, [QUIET] * 32)
        _barrier(ws)
        assert _session().utterance_active
        ws.send_text(_end(1, frames=60, last=19))
        _finish_turn(ws)
    (_, doc), = commit_on["routed"]
    assert "early_commit" not in doc


def test_a_follow_up_answer_ends_early_too(commit_on) -> None:
    commit_on["whisper"] = _WatchedWhisper("What's playing?")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        _speak_then_pause(ws, trigger="followup", utt=4)
        _send(ws, [QUIET] * (HOLD_A_FRAMES - 8))
        assert ws.receive_json() == {"type": "end_capture", "utt": 4}
        _finish_turn(ws)


@pytest.mark.parametrize("text", [
    "Who wrote the Odyssey?",                        # the language model's
    "Play some jazz.",                                # an open slot
    "Set a timer for 10 minutes. And 30 seconds.",    # two clauses
    "Set a timer for 10 minutes for the",             # cut off
])
def test_anything_but_a_whole_closed_command_waits_for_the_satellite(commit_on, text) -> None:
    commit_on["whisper"] = _WatchedWhisper(text)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        _speak_then_pause(ws)
        _send(ws, [QUIET] * 32)
        _barrier(ws)
        assert _session().utterance_active
        ws.send_text(_end(1, frames=60, last=19))
        assert _finish_turn(ws) == text


def test_speech_after_the_pause_keeps_the_capture_open(commit_on) -> None:
    whisper = commit_on["whisper"] = _WatchedWhisper("Pause", "Pause the music in the kitchen.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        _speak_then_pause(ws)
        ws.send_text(json.dumps({"type": "speech_resume", "utt": 1, "frame": 28}))
        _send(ws, [LOUD] * 11 + [QUIET] * 30)
        _barrier(ws)
        assert _session().utterance_active, "no pause reported since the speech came back"
        ws.send_text(_end(1, frames=69, last=38))
        assert _finish_turn(ws) == "Pause the music in the kitchen."
    assert len(whisper.calls) == 2


def test_speech_back_one_frame_short_of_the_hold_is_never_cut_off(commit_on) -> None:
    """The satellite sends `speech_resume` before the voiced frame that
    ends the pause, so every frame the core counts toward the hold is one
    the satellite called silence: speech that comes back on the frame that
    would have completed the hold never gets a commit on its first
    syllable."""
    whisper = commit_on["whisper"] = _WatchedWhisper(
        "Pause the music.", "Pause the music in the kitchen.",
    )
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        frames = _speak_then_pause(ws)              # voiced 0-19, hint at 28
        # Silent through frame 30: 11 frames (330 ms) — the 12th would
        # complete the 350 ms hold, and it is speech.
        _send(ws, [QUIET] * (HOLD_A_FRAMES - 8 - 1))
        frames += HOLD_A_FRAMES - 8 - 1
        ws.send_text(json.dumps({"type": "speech_resume", "utt": 1, "frame": frames}))
        _send(ws, [LOUD] * 10 + [QUIET] * 40)
        _barrier(ws)
        assert _session().utterance_active, "committed on the frame that resumed speech"
        ws.send_text(_end(1, frames=frames + 50, last=frames + 9))
        assert _finish_turn(ws) == "Pause the music in the kitchen."
    assert len(whisper.calls) == 2


def test_a_whole_yes_to_a_parked_question_ends_early(commit_on, monkeypatch) -> None:
    from domovoi.handlers import HANDLER_BY_NAME

    kind = sorted(HANDLER_BY_NAME["voice_profile"].confirmation_kinds)[0]

    async def _context(self):
        return {"handler": "voice_profile", "kind": kind, "data": {}}, False

    monkeypatch.setattr(StreamSession, "_read_commit_context", _context)
    commit_on["whisper"] = _WatchedWhisper("Yes.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        _speak_then_pause(ws, trigger="followup", voiced=10)
        _send(ws, [QUIET] * (HOLD_A_FRAMES - 8))
        _barrier(ws)
        assert _session().utterance_active, "a lone word holds as tier B"
        _send(ws, [QUIET] * (HOLD_B_FRAMES - HOLD_A_FRAMES))
        assert ws.receive_json() == {"type": "end_capture", "utt": 1}
        assert _finish_turn(ws) == "Yes."


def test_a_session_in_chat_mode_never_ends_early(commit_on, monkeypatch) -> None:
    async def _context(self):
        return None, True

    monkeypatch.setattr(StreamSession, "_read_commit_context", _context)
    commit_on["whisper"] = _WatchedWhisper("Pause the music.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        _speak_then_pause(ws)
        _send(ws, [QUIET] * 32)
        _barrier(ws)
        assert _session().utterance_active
        ws.send_text(_end(1, frames=60, last=19))
        _finish_turn(ws)


def test_a_parked_payload_of_an_odd_shape_never_drops_the_satellite(commit_on, monkeypatch) -> None:
    """The check runs inside the socket's receive loop: an exception in it
    must mean "no early commit", not a closed connection."""
    async def _context(self):
        return {"handler": ["not", "a", "name"], "kind": "core.x"}, False

    monkeypatch.setattr(StreamSession, "_read_commit_context", _context)
    commit_on["whisper"] = _WatchedWhisper("Yes.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        sess = _session()
        _speak_then_pause(ws, trigger="followup", voiced=10)
        _send(ws, [QUIET] * 32)
        # Not a ping barrier: a dropped connection would leave it waiting
        # for a pong forever. Watch the session read every frame instead.
        deadline = time.monotonic() + 5
        while sess._utt_frames < 50 and time.monotonic() < deadline:
            if app.state.active_sessions.get("kitchen") is not sess:
                break
            time.sleep(0.01)
        assert app.state.active_sessions.get("kitchen") is sess, "the satellite was dropped"
        assert sess._utt_frames == 50 and sess.utterance_active
        ws.send_text(_end(1, frames=50, last=9))
        assert _finish_turn(ws) == "Yes."


def test_an_unreadable_session_never_ends_early(commit_on, monkeypatch) -> None:
    async def _context(self):
        return None

    monkeypatch.setattr(StreamSession, "_read_commit_context", _context)
    commit_on["whisper"] = _WatchedWhisper("Pause the music.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        _speak_then_pause(ws)
        _send(ws, [QUIET] * 32)
        _barrier(ws)
        assert _session().utterance_active
        ws.send_text(_end(1, frames=60, last=19))
        _finish_turn(ws)


def test_noisy_capture_after_a_commit_does_not_cancel_the_answer(commit_on) -> None:
    commit_on["whisper"] = _WatchedWhisper("Pause the music.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        _speak_then_pause(ws)
        _send(ws, [QUIET] * (HOLD_A_FRAMES - 8))
        ws.send_text(json.dumps({"type": "noisy_capture"}))
        assert ws.receive_json() == {"type": "end_capture", "utt": 1}
        assert _finish_turn(ws) == "Pause the music."
        _barrier(ws)          # and no apology after it
    assert len(commit_on["routed"]) == 1


def test_speech_after_the_commit_is_logged_as_a_cut_in(commit_on, caplog) -> None:
    caplog.set_level(logging.INFO, logger="domovoi.streaming")
    commit_on["whisper"] = _WatchedWhisper("Set a timer for 10 minutes.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        frames = _speak_then_pause(ws)
        _send(ws, [QUIET] * (HOLD_B_FRAMES - 8))
        frames += HOLD_B_FRAMES - 8
        assert ws.receive_json() == {"type": "end_capture", "utt": 1}
        _finish_turn(ws)
        # The satellite kept hearing "… for the pasta" for 4 frames before
        # the end_capture reached it.
        ws.send_text(json.dumps({
            "type": "utterance_end", "greeting_played": False, "utt": 1,
            "frames": frames + 5, "last_voiced_frame": frames + 3,
            "exit_reason": "server_endpoint",
        }))
        _barrier(ws)
    (timings,) = commit_on["timings"]
    assert timings.stages["post_commit_voiced_ms"] == 4 * 30
    assert any("cut in on speech" in r.getMessage() for r in caplog.records)


def _commit_a_timer(ws) -> int:
    frames = _speak_then_pause(ws)
    _send(ws, [QUIET] * (HOLD_B_FRAMES - 8))
    frames += HOLD_B_FRAMES - 8
    assert ws.receive_json() == {"type": "end_capture", "utt": 1}
    _finish_turn(ws)
    return frames


def test_speech_after_the_capture_ended_counts_as_a_cut_in(commit_on, caplog) -> None:
    """The frames on their way when the satellite stopped were silent, but
    it listened on and heard the person go on ("... for the pasta") 150 ms
    after its capture ended — 2 frames past the server's stop, so 210 ms
    after the server stopped listening. Before satellites listened on,
    this was invisible."""
    caplog.set_level(logging.INFO, logger="domovoi.streaming")
    commit_on["whisper"] = _WatchedWhisper("Set a timer for 10 minutes.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        frames = _commit_a_timer(ws)
        ws.send_text(json.dumps({
            "type": "utterance_end", "greeting_played": False, "utt": 1,
            "frames": frames + 2, "last_voiced_frame": 19, "exit_reason": "server_endpoint",
            "voiced_after_end_ms": 150, "listened_after_end_ms": 150,
        }))
        _barrier(ws)
    (timings,) = commit_on["timings"]
    assert timings.stages["post_commit_voiced_ms"] == 0
    assert timings.stages["post_commit_resume_ms"] == 2 * 30 + 150
    assert timings.stages["post_commit_listened_ms"] == 2 * 30 + 150
    assert any("spoke again 210 ms after the server stopped listening" in r.getMessage()
               for r in caplog.records)


def test_a_satellite_that_listened_on_and_heard_nothing_records_how_long(commit_on, caplog) -> None:
    caplog.set_level(logging.INFO, logger="domovoi.streaming")
    commit_on["whisper"] = _WatchedWhisper("Set a timer for 10 minutes.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        frames = _commit_a_timer(ws)
        ws.send_text(json.dumps({
            "type": "utterance_end", "greeting_played": False, "utt": 1,
            "frames": frames + 1, "last_voiced_frame": 19, "exit_reason": "server_endpoint",
            "voiced_after_end_ms": 0, "listened_after_end_ms": 90,
        }))
        _barrier(ws)
    (timings,) = commit_on["timings"]
    assert timings.stages["post_commit_voiced_ms"] == 0
    assert timings.stages["post_commit_listened_ms"] == 30 + 90
    assert "post_commit_resume_ms" not in timings.stages
    assert not any("cut in on speech" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "watch",
    [
        {"voiced_after_end_ms": "150", "listened_after_end_ms": 150.0},
        {"voiced_after_end_ms": -30, "listened_after_end_ms": True},
        {"voiced_after_end_ms": 150, "listened_after_end_ms": 999_999},
        {"voiced_after_end_ms": 150},                     # no listened: not a watch report
    ],
)
def test_a_watch_report_that_is_not_one_is_ignored(commit_on, watch) -> None:
    commit_on["whisper"] = _WatchedWhisper("Set a timer for 10 minutes.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        frames = _commit_a_timer(ws)
        ws.send_text(json.dumps({
            "type": "utterance_end", "greeting_played": False, "utt": 1,
            "frames": frames, "last_voiced_frame": 19, "exit_reason": "server_endpoint",
            **watch,
        }))
        _barrier(ws)
    (timings,) = commit_on["timings"]
    assert timings.stages["post_commit_voiced_ms"] == 0
    assert "post_commit_listened_ms" not in timings.stages
    assert "post_commit_resume_ms" not in timings.stages


def test_the_late_stages_reach_the_row_after_its_post_route_write(commit_on, monkeypatch) -> None:
    """A satellite that listens on sends its utterance_end up to its silence
    timeout after the server stopped listening — often after the turn's
    post-route write has gone. The late stages are then written on their
    own, into the same row."""
    writes: list = []

    async def _write(self, row_id, patch):
        writes.append((row_id, dict(patch)))

    monkeypatch.setattr(StreamSession, "_write_post_route_timings", _write)

    async def _route(intent, ctx, s):
        from domovoi.models import Response

        ctx.timings.intents_log_id = 4242
        commit_on["timings"].append(ctx.timings)
        return Response(text="Done.", session_id=None, matched_handler="timer",
                        matched_path="fast", online=True)

    monkeypatch.setattr("domovoi.streaming.route", _route)
    commit_on["whisper"] = _WatchedWhisper("Set a timer for 10 minutes.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        frames = _commit_a_timer(ws)
        _barrier(ws)
        assert writes and "post_commit_listened_ms" not in writes[0][1]   # the turn's own
        ws.send_text(json.dumps({
            "type": "utterance_end", "greeting_played": False, "utt": 1,
            "frames": frames, "last_voiced_frame": 19, "exit_reason": "server_endpoint",
            "voiced_after_end_ms": 60, "listened_after_end_ms": 60,
        }))
        _barrier(ws)
    assert (4242, {"post_commit_voiced_ms": 0, "post_commit_listened_ms": 60,
                   "post_commit_resume_ms": 60}) in writes


def test_a_late_utterance_end_for_another_capture_is_ignored(commit_on) -> None:
    commit_on["whisper"] = _WatchedWhisper("Pause the music.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        _speak_then_pause(ws, utt=2)
        _send(ws, [QUIET] * (HOLD_A_FRAMES - 8))
        assert ws.receive_json() == {"type": "end_capture", "utt": 2}
        _finish_turn(ws)
        ws.send_text(_end(1, frames=99, last=90))
        _barrier(ws)
    (timings,) = commit_on["timings"]
    assert "post_commit_voiced_ms" not in timings.stages


def test_an_early_commit_is_kept_like_any_command_recording(commit_on, monkeypatch) -> None:
    """With opt-in command recordings (domovoi/command_captures.py): a
    capture the core ended itself is kept like one the satellite ended —
    the recording is every frame the core held when it stopped listening,
    and the sidecar says the core ended it."""
    kept: list = []

    async def _keep(room_id, pcm, sidecar):
        kept.append((room_id, len(pcm), sidecar))
        return sidecar["id"]

    monkeypatch.setattr("domovoi.command_captures.keep", _keep)
    commit_on["whisper"] = _WatchedWhisper("Pause the music.")
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _hello(ws)
        frames = _speak_then_pause(ws)
        _send(ws, [QUIET] * (HOLD_A_FRAMES - 8))
        frames += HOLD_A_FRAMES - 8
        assert ws.receive_json() == {"type": "end_capture", "utt": 1}
        assert _finish_turn(ws) == "Pause the music."
        ws.send_text(json.dumps({
            "type": "utterance_end", "greeting_played": False, "utt": 1,
            "frames": frames, "last_voiced_frame": 19, "exit_reason": "server_endpoint",
            "voiced_frames": 20, "trailing_silent_frames": HOLD_A_FRAMES,
            "silence_limit_frames": 40,
        }))
        _barrier(ws)
    ((room, pcm_len, sidecar),) = kept
    assert room == "kitchen" and pcm_len == frames * 960
    assert sidecar["trigger"] == "wake_word"
    assert sidecar["transcript"] == "Pause the music."
    assert sidecar["end_reason"] == "server_endpoint"
    assert sidecar["capture"] == {"frames": frames, "trailing_silent_frames": HOLD_A_FRAMES}
    assert sidecar["timings"]["early_commit"] == "A"


def test_early_commit_needs_every_condition() -> None:
    """The gate at utterance_start, condition by condition."""
    ws = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(satellite_config={})))
    sess = StreamSession(ws, "kitchen")  # type: ignore[arg-type]
    sess._capture_control = sess._speech_hints = sess._spec_on = True
    sess._utt_id = 3
    assert sess._early_commit_possible("wake_word")
    assert sess._early_commit_possible("followup")
    for trigger in ("chat", "barge_in", "wake_clip", "push_to_talk", None):
        assert not sess._early_commit_possible(trigger)
    for attr, value in (
        ("_capture_control", False), ("_speech_hints", False), ("_spec_on", False),
        ("_utt_id", None), ("dropin_peer", object()), ("wake_recording", object()),
        ("conversational_mode", True),
    ):
        saved = getattr(sess, attr)
        setattr(sess, attr, value)
        assert not sess._early_commit_possible("wake_word"), attr
        setattr(sess, attr, saved)
    original = settings.early_commit_enabled
    try:
        settings.early_commit_enabled = False
        assert not sess._early_commit_possible("wake_word")
    finally:
        settings.early_commit_enabled = original


def test_the_summary_counts_early_commits_and_speculative_transcripts() -> None:
    from domovoi.turn_timings import summarize

    rows = [
        ({"stt_ms": 700, "stt_reused": True, "speculative_decodes": 1,
          "early_commit": "A", "post_commit_voiced_ms": 0}, "fast"),
        ({"stt_ms": 700, "stt_reused": True, "speculative_decodes": 2,
          "early_commit": "B", "post_commit_voiced_ms": 120}, "fast"),
        # listened on after the commit: heard nothing, then heard speech
        ({"stt_ms": 700, "stt_reused": True, "speculative_decodes": 1,
          "early_commit": "B", "post_commit_voiced_ms": 0, "post_commit_listened_ms": 120}, "fast"),
        ({"stt_ms": 700, "stt_reused": True, "speculative_decodes": 1,
          "early_commit": "A", "post_commit_voiced_ms": 0, "post_commit_listened_ms": 210,
          "post_commit_resume_ms": 210}, "fast"),
        ({"stt_ms": 700, "stt_reused": False, "speculative_decodes": 1}, "qa"),
        ({"stt_ms": 700}, "qa"),                              # nothing speculative
        ({"stt_ms": 700, "early_commit": "Z"}, "fast"),       # not a tier
    ]
    s = summarize(rows)
    assert s["speculative"] == {"turns": 5, "reused": 4, "decodes": 6}
    assert s["early_commit"] == {"turns": 4, "A": 2, "B": 2, "cut_in": 2, "watched": 2}
