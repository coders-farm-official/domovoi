"""StreamSession's side of house-wide timer delivery (contract §3.6).

``announce_block`` — when a room can take an out-of-turn announcement and
why not — and ``announce()``: one at a time per room, cut off by a new
capture exactly like a reply, and the two ways it gives up before sending
anything (``AnnounceNotStarted``). DB-free: a fake socket, a stub TTS,
``resolve_voice`` answering the defaults.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import wave
from types import SimpleNamespace

import pytest

from domovoi import streaming
from domovoi.streaming import (
    ANNOUNCE_CAPTURE_FRESH_SEC,
    ANNOUNCE_CONNECTING_SEC,
    ANNOUNCE_FOLLOWUP_HOLD_SEC,
    ANNOUNCE_SETTLE_SEC,
    StreamSession,
)
from domovoi.timer_delivery import AnnounceInterrupted, AnnounceNotStarted


def _wav(pcm: bytes, rate: int = 16_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class _TTS:
    """0.1 s of audio per sentence; ``gate`` holds synthesis open; ``fail``
    makes it raise."""

    def __init__(self) -> None:
        self.gate: asyncio.Event | None = None
        self.fail = False

    async def synthesize(self, text, *, engine=None, voice=None):
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("tts engine down")
        return _wav(b"\x01\x00" * 1600)


class _WS:
    """Records frames; ``hold`` parks every send_bytes until released."""

    def __init__(self) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(
            active_sessions={}, satellite_voice={}, resumable_music={},
            wifi_status={}, satellite_volume={}, satellite_config={},
            greeting_phrases=[], current_playlist={},
            probe=SimpleNamespace(online=True),
        ))
        self.frames: list[tuple[str, object]] = []
        self.hold: asyncio.Event | None = None
        self.sending = asyncio.Event()

    async def send_text(self, t: str) -> None:
        self.frames.append(("text", json.loads(t)))

    async def send_bytes(self, b: bytes) -> None:
        self.sending.set()
        if self.hold is not None:
            await self.hold.wait()
        self.frames.append(("bytes", len(b)))

    def texts(self, kind: str) -> list[dict]:
        return [f for k, f in self.frames if k == "text" and f.get("type") == kind]


@pytest.fixture
def tts(monkeypatch) -> _TTS:
    fake = _TTS()

    async def _voice(_name):
        return (None, None)

    monkeypatch.setattr(streaming, "get_tts_client", lambda: fake)
    monkeypatch.setattr(streaming, "resolve_voice", _voice)
    return fake


def _session(room: str = "kitchen") -> tuple[StreamSession, _WS]:
    ws = _WS()
    sess = StreamSession(ws, room)  # type: ignore[arg-type]
    ws.app.state.active_sessions[room] = sess
    return sess, ws


# ─── announce_block ──────────────────────────────────────────────────────


def test_an_idle_room_takes_an_announcement() -> None:
    sess, _ = _session()
    assert sess.announce_block(time.monotonic()) is None


def test_the_playout_estimate_comes_from_bytes_and_rate() -> None:
    sess, _ = _session()
    # One second of 16 kHz 16-bit mono, then half a second more.
    sess._note_audio_sent(32_000, 16_000, now=100.0)
    assert sess._playout_until == pytest.approx(101.0)
    sess._note_audio_sent(16_000, 16_000, now=100.2)
    assert sess._playout_until == pytest.approx(101.5)
    # Audio sent after the queue drained starts from now.
    sess._note_audio_sent(24_000, 24_000, now=200.0)
    assert sess._playout_until == pytest.approx(200.5)

    sess._playout_until = 101.5
    assert sess.announce_block(101.0) == ("playing", True)
    assert sess.announce_block(101.5 + ANNOUNCE_SETTLE_SEC / 2) == ("settling", False)
    assert sess.announce_block(101.5 + ANNOUNCE_SETTLE_SEC + 0.01) is None


def test_the_follow_up_hold_and_its_clearing_on_utterance_start() -> None:
    sess, _ = _session()
    now = time.monotonic()
    sess._playout_until = now + 2.0
    sess._note_response_end({"type": "response_end", "interrupted": False,
                             "expect_followup": True})
    assert sess._followup_hold_until == pytest.approx(now + 2.0 + ANNOUNCE_FOLLOWUP_HOLD_SEC)
    assert sess.announce_block(now + 5.0) == ("followup", False)
    assert sess.announce_block(now + 2.0 + ANNOUNCE_FOLLOWUP_HOLD_SEC + 1.1) is None


@pytest.mark.asyncio
async def test_utterance_start_clears_the_follow_up_hold() -> None:
    sess, _ = _session()
    sess._followup_hold_until = time.monotonic() + 60
    await sess._on_control({"type": "utterance_start", "trigger": "followup"})
    assert sess._followup_hold_until == 0.0
    # ...and the capture itself is now the reason to wait.
    assert sess.announce_block() == ("capturing", True)


@pytest.mark.asyncio
async def test_a_capture_with_no_fresh_audio_is_not_capturing() -> None:
    """A follow-up capture that times out with no speech sends
    utterance_start but never utterance_end: utterance_active stays True
    until the next wake. Frames stop, so it stops blocking."""
    sess, _ = _session()
    await sess._on_control({"type": "utterance_start", "trigger": "followup"})
    now = time.monotonic()
    assert sess.announce_block(now) == ("capturing", True)
    assert sess.announce_block(now + ANNOUNCE_CAPTURE_FRESH_SEC + 0.1) is None

    await sess._on_audio(b"\x00" * 640)
    assert sess.announce_block(time.monotonic()) == ("capturing", True)
    later = sess._last_audio_at + ANNOUNCE_CAPTURE_FRESH_SEC + 0.01
    assert sess.utterance_active and sess.announce_block(later) is None


def test_in_call_recording_responding_and_connecting() -> None:
    sess, _ = _session()
    now = time.monotonic()
    sess._connected_at = now
    assert sess.announce_block(now + 1.0) == ("connecting", False)
    assert sess.announce_block(now + ANNOUNCE_CONNECTING_SEC + 0.01) is None

    sess.wake_recording = object()  # type: ignore[assignment]
    assert sess.announce_block(now) == ("recording", True)
    sess.dropin_peer = object()  # type: ignore[assignment]
    assert sess.announce_block(now) == ("in_call", True)   # first in the order
    sess.dropin_peer = None
    sess.wake_recording = None


@pytest.mark.asyncio
async def test_responding_blocks_and_announce_refuses_without_sending() -> None:
    sess, ws = _session()
    done = asyncio.Event()
    sess._response_task = asyncio.create_task(done.wait())
    assert sess.announce_block() == ("responding", True)

    with pytest.raises(AnnounceNotStarted) as exc:
        await sess.announce("Your pasta timer is done.")
    assert exc.value.reason == "responding"
    assert isinstance(exc.value, RuntimeError)
    assert str(exc.value) == "room kitchen mid-response, announce skipped"
    assert ws.frames == []
    done.set()
    await sess._response_task


@pytest.mark.asyncio
async def test_barge_in_and_an_interrupted_response_end_reset_the_playout() -> None:
    sess, _ = _session()
    sess._playout_until = time.monotonic() + 30
    await sess._on_control({"type": "barge_in"})
    assert sess._playout_until <= time.monotonic()

    sess._playout_until = time.monotonic() + 30
    await sess._safe_send_text({"type": "response_end", "interrupted": True,
                                "expect_followup": False})
    assert sess._playout_until <= time.monotonic()


# ─── announce() ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_announce_sends_the_usual_frames_and_moves_the_playout(tts) -> None:
    sess, ws = _session()
    before = time.monotonic()
    await sess.announce("Your pasta timer is done.")
    assert [f["text"] for f in ws.texts("response_start")] == ["Your pasta timer is done."]
    assert ws.texts("response_end") == [
        {"type": "response_end", "interrupted": False, "expect_followup": False}]
    # 3200 bytes at 16 kHz = 0.1 s of audio still coming out of the speaker.
    assert sess._playout_until >= before + 0.1
    assert sess.announce_block()[0] in ("playing", "settling")


@pytest.mark.asyncio
async def test_utterance_start_cuts_a_running_announcement(tts) -> None:
    sess, ws = _session()
    ws.hold = asyncio.Event()
    task = asyncio.create_task(sess.announce("Reminder: call mom"))
    await asyncio.wait_for(ws.sending.wait(), 2)
    assert sess.announce_block() == ("announcing", True)

    await sess._on_control({"type": "utterance_start", "trigger": "wake_word"})
    with pytest.raises(AnnounceInterrupted):
        await asyncio.wait_for(task, 2)
    assert ws.texts("response_end") == [
        {"type": "response_end", "interrupted": True, "expect_followup": False}]
    assert sess._playout_until <= time.monotonic()
    assert sess._announce_task is None
    assert not sess._announce_lock.locked()


@pytest.mark.asyncio
async def test_barge_in_cuts_a_running_announcement_too(tts) -> None:
    sess, ws = _session()
    ws.hold = asyncio.Event()
    task = asyncio.create_task(sess.announce("Reminder: call mom"))
    await asyncio.wait_for(ws.sending.wait(), 2)
    await sess._on_control({"type": "barge_in"})
    with pytest.raises(AnnounceInterrupted):
        await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_cancelling_the_caller_is_not_an_interruption(tts) -> None:
    sess, ws = _session()
    ws.hold = asyncio.Event()
    task = asyncio.create_task(sess.announce("Reminder: call mom"))
    await asyncio.wait_for(ws.sending.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The satellite is still told the response is over.
    assert ws.texts("response_end")[-1]["interrupted"] is True


@pytest.mark.asyncio
async def test_concurrent_announcements_serialize(tts) -> None:
    sess, ws = _session()
    ws.hold = asyncio.Event()
    first = asyncio.create_task(sess.announce("From the garage: Your pasta timer is done."))
    await asyncio.wait_for(ws.sending.wait(), 2)
    second = asyncio.create_task(sess.announce("Dinner is ready."))
    await asyncio.sleep(0.05)
    ws.hold.set()
    await asyncio.gather(first, second)

    kinds = [
        (k, f["type"] if k == "text" else "audio") for k, f in ws.frames
    ]
    # start, audio…, end, then start, audio…, end — never interleaved.
    squashed = [kinds[0]]
    for item in kinds[1:]:
        if item != squashed[-1]:
            squashed.append(item)
    assert squashed == [
        ("text", "response_start"), ("bytes", "audio"), ("text", "response_end"),
        ("text", "response_start"), ("bytes", "audio"), ("text", "response_end"),
    ]
    assert [f["text"] for f in ws.texts("response_start")] == [
        "From the garage: Your pasta timer is done.", "Dinner is ready."]


@pytest.mark.asyncio
async def test_tts_failure_is_announce_not_started(tts) -> None:
    sess, ws = _session()
    tts.fail = True
    with pytest.raises(AnnounceNotStarted) as exc:
        await sess.announce("Reminder: call mom")
    assert exc.value.reason == "tts_failed"
    assert ws.frames == []


@pytest.mark.asyncio
async def test_a_capture_starting_while_it_synthesizes_wins(tts) -> None:
    sess, ws = _session()
    tts.gate = asyncio.Event()
    task = asyncio.create_task(sess.announce("Reminder: call mom"))
    await asyncio.sleep(0.05)
    await sess._on_control({"type": "utterance_start", "trigger": "wake_word"})
    tts.gate.set()
    with pytest.raises(AnnounceNotStarted) as exc:
        await asyncio.wait_for(task, 2)
    assert exc.value.reason == "capturing"
    assert ws.frames == []


@pytest.mark.asyncio
async def test_a_dead_socket_still_raises_and_evicts(tts) -> None:
    sess, ws = _session()

    async def _dead(_b):
        raise ConnectionError("socket closed")

    ws.send_bytes = _dead  # type: ignore[method-assign]
    with pytest.raises(ConnectionError):
        await sess.announce("Reminder: call mom")
    assert "kitchen" not in ws.app.state.active_sessions


def test_the_room_connected_hook_runs_after_ready(monkeypatch) -> None:
    """A satellite that (re)connects gets every unsettled fire it should
    still hear: StreamSession.run() tells the coordinator after `ready`."""
    from fastapi.testclient import TestClient

    from domovoi.main import app
    from domovoi.timer_delivery import TimerDelivery

    async def _accept(self, ctrl):
        return True

    monkeypatch.setattr(StreamSession, "_validate_pairing", _accept)
    seen: list[str] = []
    monkeypatch.setattr(TimerDelivery, "on_room_connected",
                        lambda self, room: seen.append(room))

    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/garage") as ws:
            ws.send_text(json.dumps({"type": "hello", "room_id": "garage"}))
            assert ws.receive_json()["type"] == "ready"
            ws.send_text(json.dumps({"type": "ping"}))
            assert ws.receive_json() == {"type": "pong"}
            sess = app.state.active_sessions["garage"]
            assert sess._connected_at > 0
            assert sess.announce_block()[0] == "connecting"
    assert seen == ["garage"]
