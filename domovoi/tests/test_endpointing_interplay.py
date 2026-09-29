"""Early endpointing meets the greeting-echo, cold-start and QA fixes.

Two lines of work changed the same voice turn at the same time:

* early endpointing (test_speculative_stt.py, test_early_commit_stream.py):
  the core transcribes a copy at the first pause, may reuse it, and may end
  the capture itself (`end_capture`) when the copy is a whole command;
* the voice-answer fixes (test_greeting_echo.py, test_llm_cold_start.py,
  test_voice_qa_answers.py): a turn that is only Domovoi's own wake
  greeting is dropped, the satellite names the clip it played (and now
  plays it before it listens: satellite/tests/test_wake_ack.py), a cold
  model is announced before it loads, and QA answers are the model's plain
  text.

Each rule below is where the two meet:

1. A greeting-only transcript never early-commits, and a speculative copy
   of it is never used as a command (it is dropped like any other) —
   unless the satellite says the greeting finished before the capture
   opened (`ack_before_capture`), when it cannot be the greeting alone.
2. The satellite's report of the greeting and its endpoint reports share
   `utterance_end` and `speech_pause` (greeting_played + greeting_clip +
   ack_before_capture beside utt / frames / last_voiced_frame /
   exit_reason); the whole loop against the core is at the bottom here.
3. The cold-start notice opens the one response after an early commit or a
   reused copy just as after a whole-capture decode.
4. Plain-text QA answers the routed turn whether its transcript came from
   the copy or from the whole capture.
5. An opted-in room's command recording keeps the reason the capture
   really ended: server_endpoint for an early commit, no_speech_after_
   greeting for an older satellite's capture that waited out its greeting
   reply wait.
6. The turn's timings keep every stage and flag of both.

Driven through the real `/v1/stream` socket with the stubs of
test_speculative_stt.py (no database: routing, TTS and voice
identification stubbed, or the router run on fake repositories).
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import types
import wave

import pytest
from fastapi.testclient import TestClient

from domovoi.config import settings
from domovoi.main import app
from domovoi.models import Response
from domovoi.router import _COLD_START_TEXT
from domovoi.streaming import StreamSession, _Heard, _Speculation
from domovoi.tests.test_early_commit_stream import (
    HOLD_A_FRAMES,
    HOLD_B_FRAMES,
    _barrier,
    _session,
    commit_on,  # noqa: F401 - fixture
)
from domovoi.tests.test_early_endpointing_e2e import LOUD as SAT_LOUD
from domovoi.tests.test_early_endpointing_e2e import QUIET as SAT_QUIET
from domovoi.tests.test_early_endpointing_e2e import _capture as sat_capture
from domovoi.tests.test_early_endpointing_e2e import _close_loops  # noqa: F401 - autouse fixture
from domovoi.tests.test_early_endpointing_e2e import _hello as sat_hello
from domovoi.tests.test_early_endpointing_e2e import _replay, _satellite
from domovoi.tests.test_speculative_stt import (
    LOUD,
    QUIET,
    _finish_turn,
    _send,
    _WatchedWhisper,
    pipeline,  # noqa: F401 - fixture
)

YES_CLIP = "greet_yes.mp3"
BACK_CLIP = "greet_funny_back.mp3"
BANK = {YES_CLIP: "Yes?", BACK_CLIP: "Back so soon?"}


def _bank(*, phrases: bool = True, clips: bool = True) -> None:
    """The greeting bank as app.state holds it after boot (main.load_greeting_bank)."""
    app.state.greeting_phrases = list(BANK.values()) if phrases else []
    app.state.greeting_clips = dict(BANK) if clips else {}


def _hello(ws, *, capture_control: bool = True) -> None:
    msg = {"type": "hello", "room_id": "kitchen", "speech_pause": True}
    if capture_control:
        msg["capture_control"] = True
    ws.send_text(json.dumps(msg))
    assert ws.receive_json()["type"] == "ready"


def _pause(utt: int, frame: int, last: int, clip: str | None) -> str:
    msg = {
        "type": "speech_pause", "utt": utt, "frame": frame,
        "last_voiced_frame": last, "greeting_played": True,
    }
    if clip is not None:
        msg["greeting_clip"] = clip
    return json.dumps(msg)


def _end(utt: int, *, frames: int, last: int | None, clip: str | None,
         reason: str = "vad_silence_after_speech", **counts) -> str:
    msg = {
        "type": "utterance_end", "greeting_played": True, "utt": utt,
        "frames": frames, "last_voiced_frame": last, "exit_reason": reason, **counts,
    }
    if clip is not None:
        msg["greeting_clip"] = clip
    return json.dumps(msg)


def _read_turn(ws) -> list[dict]:
    """Every frame of one turn up to its response_end; audio as <audio>."""
    seen: list[dict] = []
    while not seen or seen[-1].get("type") != "response_end":
        msg = ws.receive()
        seen.append(json.loads(msg["text"]) if msg.get("text") is not None else {"type": "<audio>"})
    return seen


def _await_commit(timeout: float = 5.0) -> None:
    """The core has ended the capture. Polled with a deadline, so a
    regression that never commits fails here instead of leaving the test
    waiting forever on an end_capture that is not coming."""
    sess = _session()
    deadline = time.monotonic() + timeout
    while sess.utterance_active and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not sess.utterance_active, "the core never ended the capture"


def _dropped(frames: list[dict]) -> bool:
    return frames == [{"type": "response_end", "interrupted": True, "expect_followup": False}]


def _park_a_question(monkeypatch) -> None:
    """A yes/no question is parked in the session: a lone "yes" would
    answer it, and commits early as tier B."""
    from domovoi.handlers import HANDLER_BY_NAME

    kind = sorted(HANDLER_BY_NAME["voice_profile"].confirmation_kinds)[0]

    async def _context(self):
        return {"handler": "voice_profile", "kind": kind, "data": {}}, False

    monkeypatch.setattr(StreamSession, "_read_commit_context", _context)


# ─── 1. a greeting-only transcript never commits, never routes ────────────


@pytest.mark.parametrize("clip", [YES_CLIP, None], ids=["clip-named", "whole-bank"])
def test_a_copy_of_the_greeting_alone_never_ends_the_capture(commit_on, monkeypatch, clip) -> None:
    """The greeting "Yes?" bled past the AEC and the satellite reported a
    pause on it (a bleed that outlasted its greeting window). With a
    question parked, "Yes." is a whole answer — tier B, were it the
    person's. It is Domovoi's own word: the capture stays open, and the
    person's command after it is the one that commits."""
    _park_a_question(monkeypatch)
    whisper = commit_on["whisper"] = _WatchedWhisper("Yes?", "Yes? Pause the music.")
    with TestClient(app) as tc, tc.websocket_connect("/v1/stream/kitchen") as ws:
        _bank()
        _hello(ws)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
        _send(ws, [LOUD] * 10 + [QUIET] * 8)
        ws.send_text(_pause(1, 18, 9, clip))
        time.sleep(0.2)                       # the greeting copy's decode
        _send(ws, [QUIET] * (HOLD_B_FRAMES + 8))
        _barrier(ws)
        assert _session().utterance_active, "committed on the greeting"
        # Now the person speaks, and pauses.
        ws.send_text(json.dumps({"type": "speech_resume", "utt": 1, "frame": 48}))
        _send(ws, [LOUD] * 20 + [QUIET] * 8)
        ws.send_text(_pause(1, 76, 67, clip))
        time.sleep(0.2)
        _send(ws, [QUIET] * (HOLD_A_FRAMES - 8))
        _await_commit()
        assert ws.receive_json() == {"type": "end_capture", "utt": 1}
        assert _finish_turn(ws) == "Pause the music."
    assert len(whisper.calls) == 2
    (said, doc), = commit_on["routed"]
    assert said == "Pause the music."
    assert doc["early_commit"] == "A" and doc["stt_reused"] is True


@pytest.mark.parametrize("clip", [YES_CLIP, None], ids=["clip-named", "whole-bank"])
def test_the_persons_yes_after_the_greeting_yes_commits_and_answers(
    commit_on, monkeypatch, clip,
) -> None:
    """The greeting "Yes?" came back through the mic and the person, with a
    question parked, answered "yes" after it: the copy is "Yes? Yes.". The
    greeting is stripped off its front; what is left was said after the
    greeting, so it is the person's answer — not the greeting heard twice.
    It commits (tier B) and the answer is routed, not dropped."""
    _park_a_question(monkeypatch)
    whisper = commit_on["whisper"] = _WatchedWhisper("Yes? Yes.")
    with TestClient(app) as tc, tc.websocket_connect("/v1/stream/kitchen") as ws:
        _bank()
        _hello(ws)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
        # The greeting's frames (not counted by the satellite), then "yes".
        _send(ws, [LOUD] * 30 + [QUIET] * 8)
        ws.send_text(_pause(1, 38, 29, clip))
        time.sleep(0.2)
        _send(ws, [QUIET] * (HOLD_B_FRAMES - 8))
        _await_commit()
        assert ws.receive_json() == {"type": "end_capture", "utt": 1}
        assert _finish_turn(ws) == "Yes."
    assert len(whisper.calls) == 1
    (said, doc), = commit_on["routed"]
    assert said == "Yes."
    assert doc["early_commit"] == "B" and doc["stt_reused"] is True


@pytest.mark.parametrize("satellite", ["hints", "old"])
def test_a_reused_copy_of_the_greeting_alone_is_dropped(pipeline, satellite) -> None:
    """The copy covers every voiced frame, so it is the turn's transcript —
    and the turn screens it like any other: only the greeting, dropped. No
    second decode, no route, no reply."""
    whisper = pipeline["whisper"] = _WatchedWhisper("Back so soon.")
    with TestClient(app) as tc, tc.websocket_connect("/v1/stream/kitchen") as ws:
        _bank()
        if satellite == "hints":
            _hello(ws, capture_control=False)
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
            _send(ws, [LOUD] * 10 + [QUIET] * 8)
            ws.send_text(_pause(1, 18, 9, BACK_CLIP))
            _send(ws, [QUIET] * 32)
            ws.send_text(_end(1, frames=50, last=9, clip=BACK_CLIP))
        else:
            # An older satellite: no hints, no clip, the whole bank — the
            # core finds the pause in the frames and works the last voiced
            # frame out from the reported silence timeout.
            ws.send_text(json.dumps({"type": "hello", "room_id": "kitchen"}))
            assert ws.receive_json()["type"] == "ready"
            ws.send_text(json.dumps({"type": "config_status",
                                     "config": {"listen.silence_timeout": 1.2}}))
            ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
            _send(ws, [LOUD] * 20 + [QUIET] * 40)
            ws.send_text(json.dumps({"type": "utterance_end", "greeting_played": True}))
        assert _dropped(_read_turn(ws))
    assert len(whisper.calls) == 1, "the copy was the transcript"
    assert pipeline["routed"] == []


def _decision_session(monkeypatch, *, pending: bool = True):
    monkeypatch.setattr(settings, "early_commit_tier_b", True)
    monkeypatch.setattr(settings, "early_commit_hold_a_ms", 350)
    monkeypatch.setattr(settings, "early_commit_hold_b_ms", 650)
    from domovoi.handlers import HANDLER_BY_NAME

    kind = sorted(HANDLER_BY_NAME["voice_profile"].confirmation_kinds)[0]
    state = types.SimpleNamespace(
        satellite_config={}, greeting_phrases=list(BANK.values()), greeting_clips=dict(BANK),
    )
    sess = StreamSession(types.SimpleNamespace(app=types.SimpleNamespace(state=state)), "kitchen")  # type: ignore[arg-type]
    fut = asyncio.get_running_loop().create_future()
    fut.set_result(({"handler": "voice_profile", "kind": kind, "data": {}} if pending else None, False))
    sess._commit_ctx_task = fut  # type: ignore[assignment]
    return sess


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "greeting,clip,heard,expected",
    [
        # The played line, heard back: never.
        (True, YES_CLIP, "Yes.", None),
        # The whole bank (no clip named) has "Yes?" in it: never — the
        # turn would drop it too.
        (True, None, "Yes.", None),
        # "Back so soon?" played; "Yes." is the person answering.
        (True, BACK_CLIP, "Yes.", ("B", 650)),
        # No greeting this turn: the person.
        (False, None, "Yes.", ("B", 650)),
        # The greeting, then the answer: judged on the answer.
        (True, BACK_CLIP, "Back so soon? Yes.", ("B", 650)),
        (True, BACK_CLIP, "Back so soon? Pause the music.", ("A", 350)),
        # ... even when the answer is a word the greeting bank also has:
        # what follows a stripped greeting was said after it, by the person.
        (True, YES_CLIP, "Yes? Yes.", ("B", 650)),
        (True, None, "Back so soon? Yes.", ("B", 650)),
        (True, None, "Yes? Yes.", ("B", 650)),
        # Without the strip, the break inside it would keep it open.
        (False, None, "Back so soon? Pause the music.", None),
    ],
)
async def test_the_commit_check_screens_the_copy_like_the_turn(
    monkeypatch, greeting, clip, heard, expected,
) -> None:
    sess = _decision_session(monkeypatch)
    sess._hint_greeting = greeting
    sess._hint_greeting_clip = clip
    spec = _Speculation(serial=0, frames=20)
    assert sess._commit_decision(spec, _Heard(text=heard, stt_ms=1, whisper=None)) == expected
    # Judged once per copy.
    assert spec.commit == (expected or ())


@pytest.mark.asyncio
async def test_the_pause_hint_carries_the_clip_for_its_utterance_only(monkeypatch) -> None:
    sess = _decision_session(monkeypatch)
    sess._speech_hints = True
    sess.utterance_active = True
    sess._utt_frames = 18
    monkeypatch.setattr(sess, "_on_pause", lambda: None)
    monkeypatch.setattr(sess, "_maybe_commit", lambda: None)

    def hint(**extra):
        sess._on_speech_hint("speech_pause", {
            "type": "speech_pause", "frame": 18, "last_voiced_frame": 9, **extra,
        })
        return sess._hint_greeting, sess._hint_greeting_clip

    assert hint(greeting_played=True, greeting_clip=YES_CLIP) == (True, YES_CLIP)
    assert hint(greeting_played=True) == (True, None)
    assert hint(greeting_played=True, greeting_clip=7) == (True, None)
    # A clip without the flag is not a greeting turn.
    assert hint(greeting_played=False, greeting_clip=YES_CLIP) == (False, None)
    hint(greeting_played=True, greeting_clip=YES_CLIP)
    sess._reset_utterance_tracking("wake_word")
    assert (sess._hint_greeting, sess._hint_greeting_clip) == (False, None)


# ─── 2. the wait past the greeting and the endpoint reports, together ─────


class _FakeWS:
    def __init__(self) -> None:
        self.app = types.SimpleNamespace(state=types.SimpleNamespace(
            satellite_config={}, satellite_voice={}, greeting_phrases=list(BANK.values()),
            greeting_clips=dict(BANK),
        ))
        self.sent: list[dict] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields,silence_ms",
    [
        # The person spoke after the greeting and stopped.
        ({"frames": 60, "last_voiced_frame": 19, "exit_reason": "vad_silence_after_speech",
          "voiced_frames": 20, "trailing_silent_frames": 40, "silence_limit_frames": 40},
         40 * 30),
        # An older satellite (greeting over the capture): only under the
        # greeting, the reply wait ran out, nothing to claim.
        ({"frames": 60, "last_voiced_frame": None, "exit_reason": "no_speech_after_greeting",
          "voiced_frames": 0, "trailing_silent_frames": 49, "silence_limit_frames": 49},
         None),
    ],
    ids=["speech-after", "older-satellite-reply-wait"],
)
async def test_utterance_end_hands_the_clip_and_the_endpoint_to_the_turn(
    monkeypatch, fields, silence_ms,
) -> None:
    calls: list[dict] = []

    async def _capture(self, pcm, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(StreamSession, "_process_utterance", _capture)
    sess = StreamSession(_FakeWS(), "office")  # type: ignore[arg-type]
    await sess._on_control({"type": "utterance_start", "trigger": "wake_word", "utt": 4})
    for _ in range(60):
        await sess._on_audio(QUIET)
    await sess._on_control({
        "type": "utterance_end", "greeting_played": True, "greeting_clip": BACK_CLIP,
        "utt": 4, **fields,
    })
    await sess._response_task
    (kw,) = calls
    assert kw["greeting_played"] is True and kw["greeting_clip"] == BACK_CLIP
    assert kw["endpoint_silence_ms"] == silence_ms
    assert kw["capture_meta"]["end_reason"] == fields["exit_reason"]
    assert kw["capture_meta"]["capture"] == {
        k: fields[k] for k in ("frames", "voiced_frames", "trailing_silent_frames",
                               "silence_limit_frames")
    }


def _kept(monkeypatch) -> list:
    kept: list = []

    async def _keep(room_id, pcm, sidecar):
        kept.append((len(pcm), sidecar))
        return sidecar["id"]

    monkeypatch.setattr("domovoi.command_captures.keep", _keep)
    return kept


@pytest.mark.parametrize(
    "heard,routed",
    [("Back so soon? Pause the music.", "Pause the music."), ("Back so soon.", None)],
    ids=["command-under-the-greeting", "greeting-only"],
)
def test_a_capture_that_waited_past_the_greeting(pipeline, monkeypatch, heard, routed) -> None:
    """An older satellite, which plays the greeting over its capture:
    everything was said under the greeting; it waited out its reply wait
    and sent the lot with no last voiced frame. No pause
    was reported, so there is no copy: the whole capture is transcribed,
    the greeting stripped off (or the turn dropped), and the recording
    says the capture ended on the reply wait."""
    kept = _kept(monkeypatch)
    whisper = pipeline["whisper"] = _WatchedWhisper(heard)
    counts = {"voiced_frames": 0, "trailing_silent_frames": 83, "silence_limit_frames": 83}
    with TestClient(app) as tc, tc.websocket_connect("/v1/stream/kitchen") as ws:
        _bank()
        _hello(ws)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
        _send(ws, [LOUD] * 30 + [QUIET] * 93)
        ws.send_text(_end(1, frames=123, last=None, clip=BACK_CLIP,
                          reason="no_speech_after_greeting", **counts))
        if routed is None:
            assert _dropped(_read_turn(ws))
        else:
            assert _finish_turn(ws) == routed
    assert whisper.calls == [123 * 960]
    if routed is None:
        assert pipeline["routed"] == [] and kept == []
        return
    (said, doc), = pipeline["routed"]
    assert said == routed
    assert "stt_reused" not in doc and "endpoint_silence_ms" not in doc
    ((pcm_len, sidecar),) = kept
    assert pcm_len == 123 * 960
    assert sidecar["end_reason"] == "no_speech_after_greeting"
    assert sidecar["capture"] == {"frames": 123, **counts}
    assert sidecar["transcript"] == routed


def test_the_clip_named_on_the_pause_reaches_the_commit_and_the_turn(commit_on, monkeypatch) -> None:
    """Only the played clip is known (the bank lists nothing): the commit
    check strips it off the copy, so the command commits, and the turn
    strips it the same way; the recording says the core ended it."""
    kept = _kept(monkeypatch)
    commit_on["whisper"] = _WatchedWhisper("Back so soon? Pause the music.")
    with TestClient(app) as tc, tc.websocket_connect("/v1/stream/kitchen") as ws:
        _bank(phrases=False)
        _hello(ws)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
        _send(ws, [LOUD] * 20 + [QUIET] * 8)
        ws.send_text(_pause(1, 28, 19, BACK_CLIP))
        time.sleep(0.2)
        _send(ws, [QUIET] * (HOLD_A_FRAMES - 8))
        _await_commit()
        assert ws.receive_json() == {"type": "end_capture", "utt": 1}
        assert _finish_turn(ws) == "Pause the music."
        ws.send_text(_end(1, frames=32, last=19, clip=BACK_CLIP, reason="server_endpoint"))
        _barrier(ws)
    (said, doc), = commit_on["routed"]
    assert said == "Pause the music." and doc["early_commit"] == "A"
    ((_, sidecar),) = kept
    assert sidecar["end_reason"] == "server_endpoint"
    assert sidecar["transcript"] == "Pause the music."


# ─── 3 + 6. the cold-start notice, and every timing kept ──────────────────


def _wav(seconds: float, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return buf.getvalue()


class _RateTTS:
    """The notice at 22.05 kHz, everything else at 24 kHz."""

    def __init__(self) -> None:
        self.said: list[str] = []

    async def synthesize(self, text, *, engine=None, voice=None) -> bytes:
        self.said.append(text)
        return _wav(0.2, 22_050 if text == _COLD_START_TEXT else 24_000)


def _cold_route(pipeline, monkeypatch):
    """A route that meets a cold model: the notice first, then the answer."""
    timings: list = []

    async def _route(intent, ctx, s):
        pipeline["routed"].append((intent.transcript, ctx.timings.row_document()))
        timings.append(ctx.timings)
        await ctx.speak_interim(_COLD_START_TEXT)
        await ctx.speak_interim(_COLD_START_TEXT)          # once per turn
        return Response(text="Paused. Anything else?", session_id=None,
                        matched_handler="music", matched_path="fast", online=True)

    tts = _RateTTS()
    monkeypatch.setattr("domovoi.streaming.route", _route)
    monkeypatch.setattr("domovoi.streaming.get_tts_client", lambda: tts)
    return timings, tts


def _one_response(frames: list[dict]) -> dict:
    starts = [f for f in frames if f["type"] == "response_start"]
    assert len(starts) == 1, starts
    assert starts[0]["text"] == _COLD_START_TEXT and starts[0]["audio_sample_rate"] == 22_050
    assert frames[-1] == {"type": "response_end", "interrupted": False, "expect_followup": False}
    return starts[0]


def test_the_cold_start_notice_rides_an_early_commit(commit_on, monkeypatch) -> None:
    timings, tts = _cold_route(commit_on, monkeypatch)
    commit_on["whisper"] = _WatchedWhisper("Pause the music.")
    with TestClient(app) as tc, tc.websocket_connect("/v1/stream/kitchen") as ws:
        _bank()
        _hello(ws)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
        _send(ws, [LOUD] * 20 + [QUIET] * 8)
        ws.send_text(json.dumps({"type": "speech_pause", "utt": 1, "frame": 28,
                                 "last_voiced_frame": 19, "greeting_played": False}))
        time.sleep(0.2)
        _send(ws, [QUIET] * (HOLD_A_FRAMES - 8))
        _await_commit()
        frames = _read_turn(ws)
        # The satellite stops listening before anything is said, the
        # notice opens the reply, and the answer carries on in it.
        assert frames[0] == {"type": "end_capture", "utt": 1}
        assert frames[1] == {"type": "transcript", "text": "Pause the music."}
        assert frames[2]["type"] == "response_start"
        _one_response(frames)
        ws.send_text(json.dumps({
            "type": "utterance_end", "greeting_played": False, "utt": 1, "frames": 32,
            "last_voiced_frame": 19, "exit_reason": "server_endpoint",
        }))
        _barrier(ws)
    assert tts.said == [_COLD_START_TEXT, "Paused.", "Anything else?"]
    (t,) = timings
    # 6. every stage and flag of both lines of work, on one turn.
    doc = t.row_document()
    for key in ("capture_audio_ms", "endpoint_silence_ms", "stt_ms", "stt_wait_ms",
                "identify_ms", "route_ms", "tts_first_ms", "total_ms",
                "speech_to_reply_ms", "early_commit_hold_ms", "post_commit_voiced_ms",
                "stt_reused", "speculative_decodes", "speculative_ms", "early_commit"):
        assert key in doc, key
    assert doc["early_commit"] == "A" and doc["stt_reused"] is True
    assert doc["post_commit_voiced_ms"] == 0
    assert set(t.post_route_patch()) == {
        "route_ms", "tts_first_ms", "total_ms", "speech_to_reply_ms", "post_commit_voiced_ms",
    }


def test_the_cold_start_notice_rides_a_reused_copy(pipeline, monkeypatch) -> None:
    timings, tts = _cold_route(pipeline, monkeypatch)
    whisper = pipeline["whisper"] = _WatchedWhisper("Who wrote Pride and Prejudice?")
    with TestClient(app) as tc, tc.websocket_connect("/v1/stream/kitchen") as ws:
        _bank()
        _hello(ws, capture_control=False)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
        _send(ws, [LOUD] * 30 + [QUIET] * 8)
        ws.send_text(json.dumps({"type": "speech_pause", "utt": 1, "frame": 38,
                                 "last_voiced_frame": 29, "greeting_played": False}))
        _send(ws, [QUIET] * 32)
        ws.send_text(json.dumps({
            "type": "utterance_end", "greeting_played": False, "utt": 1, "frames": 70,
            "last_voiced_frame": 29, "exit_reason": "vad_silence_after_speech",
        }))
        frames = _read_turn(ws)
        assert frames[0] == {"type": "transcript", "text": "Who wrote Pride and Prejudice?"}
        _one_response(frames)
    assert len(whisper.calls) == 1
    (t,) = timings
    doc = t.row_document()
    assert doc["stt_reused"] is True and doc["speculative_decodes"] == 1
    assert "total_ms" in doc and "early_commit" not in doc


# ─── 4. plain-text QA, whichever transcript ───────────────────────────────


@pytest.mark.parametrize("reused", [True, False], ids=["copy", "whole-capture"])
def test_plain_text_qa_answers_the_turn_either_way(pipeline, monkeypatch, reused) -> None:
    """The real router on fake repositories, with the real Ollama client
    over a scripted chat: the QA call carries no JSON contract, and the
    whole answer (punchline included) is spoken, whether the transcript
    was the copy taken at the pause or the whole capture's."""
    import domovoi.router as router_mod
    from domovoi.tests.test_router import _FakeSessionRepo, _RecordingLogRepo
    from domovoi.tests.test_voice_qa_answers import JOKE, _Chat, _client

    class _Repo(_RecordingLogRepo):
        rows: list = []

    chat = _Chat(JOKE)
    monkeypatch.setattr(router_mod, "SessionRepository", _FakeSessionRepo)
    monkeypatch.setattr(router_mod, "IntentLogRepository", _Repo)
    monkeypatch.setattr(router_mod, "ConversationLogRepository", _Repo)
    monkeypatch.setattr(router_mod, "get_ollama_client", lambda: _client(chat))
    monkeypatch.setattr("domovoi.streaming.route", router_mod.route)
    tts = _RateTTS()
    monkeypatch.setattr("domovoi.streaming.get_tts_client", lambda: tts)
    seen_timings: list = []

    async def _persist(**kw):
        # No database here: the audit write is test_voice_qa_answers'; this
        # keeps the turn's stopwatch it would have written.
        seen_timings.append(kw["ctx"].timings)

    monkeypatch.setattr(router_mod, "_persist_turn", _persist)

    whisper = pipeline["whisper"] = _WatchedWhisper(
        *(["Tell me a joke."] if reused else ["Tell me", "Tell me a joke."])
    )
    with TestClient(app) as tc, tc.websocket_connect("/v1/stream/kitchen") as ws:
        _bank()
        _hello(ws, capture_control=False)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
        _send(ws, [LOUD] * 20 + [QUIET] * 8)
        ws.send_text(json.dumps({"type": "speech_pause", "utt": 1, "frame": 28,
                                 "last_voiced_frame": 19, "greeting_played": False}))
        last, frames = 19, 28
        if not reused:
            # "… a joke": speech after the copy.
            ws.send_text(json.dumps({"type": "speech_resume", "utt": 1, "frame": 28}))
            _send(ws, [LOUD] * 10)
            last, frames = 37, 38
        _send(ws, [QUIET] * 40)
        frames += 40
        ws.send_text(json.dumps({
            "type": "utterance_end", "greeting_played": False, "utt": 1, "frames": frames,
            "last_voiced_frame": last, "exit_reason": "vad_silence_after_speech",
        }))
        turn = _read_turn(ws)
    assert turn[0] == {"type": "transcript", "text": "Tell me a joke."}
    (start,) = [f for f in turn if f["type"] == "response_start"]
    assert start["text"] == JOKE and start["matched_path"] == "qa"
    assert tts.said == ["What do you call a fake noodle?", "An impasta!"]
    (call,) = chat.calls
    assert "format" not in call
    assert call["messages"][-1] == {"role": "user", "content": "Tell me a joke."}
    assert len(whisper.calls) == (1 if reused else 2)
    (t,) = seen_timings
    assert t.flags["stt_reused"] is reused


# ─── 1 + 2 + 5, the real satellite loop against the real core ─────────────
#
# The satellite plays its wake acknowledgement to the end BEFORE it listens,
# and drops what the mic heard under it (satellite/tests/test_wake_ack.py
# drives that half). What reaches the capture is the state
# `_acknowledge_wake` leaves — the clip that played, and that it came first
# — and a mic that holds only what was said after it.


def _acked_satellite(monkeypatch, ready: dict, *, clip: str, early_commit: bool):
    """A satellite whose wake greeting ``clip`` has just finished playing."""
    sat = _satellite(monkeypatch, ready, early_commit=early_commit)
    sat._greeting_played_this_turn = True
    sat._greeting_clip_name = clip
    sat._ack_before_capture = True
    return sat


def test_the_real_loop_after_the_greeting_hears_only_the_person(pipeline, monkeypatch) -> None:
    """The greeting played first: the capture is the command, its one pause
    is the person's, both hints say which greeting played and that it came
    first, and the core reuses the copy as the turn."""
    whisper = pipeline["whisper"] = _WatchedWhisper("Pause the music.")
    with TestClient(app) as tc, tc.websocket_connect("/v1/stream/kitchen") as ws:
        _bank()
        probe = _satellite(monkeypatch, {"type": "ready", "features": []}, early_commit=False)
        ready = sat_hello(ws, probe)
        sat = _acked_satellite(monkeypatch, ready, clip=BACK_CLIP, early_commit=False)
        emitted = sat_capture(sat, [SAT_LOUD] * 20 + [SAT_QUIET] * 60)
        texts = [d for k, d in emitted if k == "text"]
        assert [d["type"] for d in texts] == ["utterance_start", "speech_pause", "utterance_end"]
        pause, end = texts[1], texts[2]
        for hint in (pause, end):
            assert (hint["greeting_played"], hint["greeting_clip"], hint["ack_before_capture"]) == (
                True, BACK_CLIP, True,
            )
        assert pause["last_voiced_frame"] == 19 and end["last_voiced_frame"] == 19
        _replay(ws, emitted)
        assert _finish_turn(ws) == "Pause the music."
    assert whisper.calls == [(19 + 1 + 8) * 960], "one decode: the copy at the person's pause"
    (said, doc), = pipeline["routed"]
    assert said == "Pause the music." and doc["stt_reused"] is True


def test_the_real_loop_the_persons_yes_after_the_greeting_yes_is_answered(
    pipeline, monkeypatch,
) -> None:
    """The greeting "Yes?" played and finished, then the person said "yes"
    (a question was parked). A capture that played the greeting over itself
    could hold the greeting alone, so a lone "Yes." was dropped as Domovoi
    heard back; this one cannot, and the core routes the person's answer."""
    whisper = pipeline["whisper"] = _WatchedWhisper("Yes.")
    with TestClient(app) as tc, tc.websocket_connect("/v1/stream/kitchen") as ws:
        _bank()
        probe = _satellite(monkeypatch, {"type": "ready", "features": []}, early_commit=False)
        ready = sat_hello(ws, probe)
        sat = _acked_satellite(monkeypatch, ready, clip=YES_CLIP, early_commit=False)
        emitted = sat_capture(sat, [SAT_LOUD] * 10 + [SAT_QUIET] * 60)
        end = [d for k, d in emitted if k == "text"][-1]
        assert (end["greeting_clip"], end["ack_before_capture"]) == (YES_CLIP, True)
        _replay(ws, emitted)
        assert _finish_turn(ws) == "Yes."
    assert len(whisper.calls) == 1
    (said, _doc), = pipeline["routed"]
    assert said == "Yes."


def test_the_same_yes_from_an_older_satellite_is_still_dropped(pipeline) -> None:
    """The safety net stays for a satellite that played the greeting over
    its capture: no `ack_before_capture`, so "Yes." after "Yes?" is the
    greeting heard back, and the turn ends unrouted."""
    pipeline["whisper"] = _WatchedWhisper("Yes.")
    with TestClient(app) as tc, tc.websocket_connect("/v1/stream/kitchen") as ws:
        _bank()
        _hello(ws, capture_control=False)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word", "utt": 1}))
        _send(ws, [LOUD] * 10 + [QUIET] * 40)
        ws.send_text(_end(1, frames=50, last=9, clip=YES_CLIP))
        assert _dropped(_read_turn(ws))
    assert pipeline["routed"] == []


# The `ready` of a core from before early endpointing — 8cb568d, the one the
# household runs today — word for word: no `features`.
_OLDER_READY = {
    "type": "ready", "protocol_version": "0.1", "room_id": "kitchen",
    "bot_name": "Domovoi", "audio_sample_rate_in": 16000,
}


@pytest.mark.parametrize("acked", [False, True], ids=["no-greeting", "greeting-before-capture"])
def test_the_merged_loop_sends_an_older_core_nothing_new(monkeypatch, acked) -> None:
    """Satellite code is served from the core's checkout, so a satellite
    upgraded after a pull but before the core restarts runs this loop
    against the older core, which answers any message type it doesn't know
    with `error` — the end of the turn on a Pi. With or without a greeting
    before it, the capture sends that core only utterance_start, its audio
    and utterance_end (whose new fields an older core ignores)."""
    if acked:
        sat = _acked_satellite(monkeypatch, _OLDER_READY, clip=BACK_CLIP, early_commit=True)
    else:
        sat = _satellite(monkeypatch, _OLDER_READY, early_commit=True)
    assert sat._core_features == frozenset()
    emitted = sat_capture(sat, [SAT_LOUD] * 30 + [SAT_QUIET] * 60)
    texts = [d for k, d in emitted if k == "text"]
    assert [d["type"] for d in texts] == ["utterance_start", "utterance_end"]
    assert sum(1 for k, _ in emitted if k == "bytes") == texts[-1]["frames"]
    assert texts[-1]["greeting_played"] is acked
    assert texts[-1].get("ack_before_capture") is (True if acked else None)


# ─── the greeting played before the capture: the commit check ─────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "clip,heard,expected",
    [
        # The greeting "Yes?" finished before the capture opened: a "Yes."
        # in it is the person answering the parked question, and commits.
        (YES_CLIP, "Yes.", ("B", 650)),
        (None, "Yes.", ("B", 650)),
        # The strip still runs — the safety net for an echo tail.
        (BACK_CLIP, "Back so soon? Pause the music.", ("A", 350)),
        (YES_CLIP, "Yes? Yes.", ("B", 650)),
    ],
    ids=["yes-clip", "yes-bank", "strip-then-command", "strip-then-yes"],
)
async def test_a_greeting_before_the_capture_never_holds_a_commit_back(
    monkeypatch, clip, heard, expected,
) -> None:
    sess = _decision_session(monkeypatch)
    sess._hint_greeting = True
    sess._hint_greeting_clip = clip
    sess._hint_ack_before = True
    spec = _Speculation(serial=0, frames=20)
    assert sess._commit_decision(spec, _Heard(text=heard, stt_ms=1, whisper=None)) == expected


@pytest.mark.asyncio
async def test_the_pause_hint_carries_ack_before_capture_for_its_utterance_only(monkeypatch) -> None:
    sess = _decision_session(monkeypatch)
    sess._speech_hints = True
    sess.utterance_active = True
    sess._utt_frames = 18
    monkeypatch.setattr(sess, "_on_pause", lambda: None)
    monkeypatch.setattr(sess, "_maybe_commit", lambda: None)

    def hint(**extra) -> bool:
        sess._on_speech_hint("speech_pause", {
            "type": "speech_pause", "frame": 18, "last_voiced_frame": 9,
            "greeting_played": True, "greeting_clip": YES_CLIP, **extra,
        })
        return sess._hint_ack_before

    assert hint(ack_before_capture=True) is True
    assert hint() is False                            # an older satellite
    assert hint(ack_before_capture="true") is False   # only a real true counts
    hint(ack_before_capture=True)
    sess._reset_utterance_tracking("wake_word")
    assert sess._hint_ack_before is False
