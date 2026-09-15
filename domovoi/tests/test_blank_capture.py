"""A capture Whisper hears as nothing must not be routed.

Found on hardware, on both satellites: every ``conversation_log`` row for the
room had ``user_text = ""`` and the same reply, "I didn't quite catch that
Want me to check that online?", dozens of times in a row. The empty
transcript reached the LLM fallback, the double-check heuristic appended its
offer and armed a follow-up capture, the Pi reopened the mic without a wake
word, heard nothing again, and the room apologised to itself in a loop -
one LLM call and one TTS per lap.

Whatever left the capture blank (the user stopping after the wake word, a
noise burst that satisfied the VAD, a mic delivering silence), the turn has
to end at the transcript, quietly, with the mic released and no follow-up
armed. These drive ``_process_utterance`` with a fake socket and a Whisper
that returns nothing; the router and the LLM must never be reached.
"""

from __future__ import annotations

import json
import types

import pytest

from domovoi import streaming
from domovoi.streaming import StreamSession


class _FakeWS:
    def __init__(self) -> None:
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                satellite_voice={}, wifi_status={}, satellite_volume={},
                greeting_phrases=[],
            )
        )
        self.sent_text: list[dict] = []
        self.sent_bytes: list[bytes] = []

    async def send_text(self, data: str) -> None:
        self.sent_text.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)


class _SilentWhisper:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.calls = 0

    async def transcribe(self, pcm_bytes: bytes) -> str:
        self.calls += 1
        return self.text


@pytest.fixture
def routed(monkeypatch):
    """Records whether the router was reached; the test's whole point is
    that it is not."""
    hits: list[object] = []

    async def _route(intent, ctx, session):
        hits.append(intent)
        raise AssertionError("route() must not be called for a blank capture")

    monkeypatch.setattr(streaming, "route", _route)
    return hits


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["wake_word", "followup", "barge_in"])
async def test_a_blank_transcript_ends_the_turn_without_routing(
    monkeypatch, routed, trigger
) -> None:
    whisper = _SilentWhisper("")
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: whisper)
    ws = _FakeWS()
    sess = StreamSession(ws, "office")  # type: ignore[arg-type]
    sess._last_spoken_text = "I didn't quite catch that. Want me to check that online?"

    await sess._process_utterance(b"\x00" * 32_000, trigger=trigger)

    assert whisper.calls == 1
    assert routed == []
    # The mic is released and NO follow-up is armed - the loop's fuel.
    ends = [f for f in ws.sent_text if f.get("type") == "response_end"]
    assert len(ends) == 1
    assert ends[0]["interrupted"] is True
    assert ends[0]["expect_followup"] is False
    # No transcript frame, no response_start, no audio: nothing was said.
    assert not any(f.get("type") in ("transcript", "response_start") for f in ws.sent_text)
    assert ws.sent_bytes == []


@pytest.mark.asyncio
async def test_whitespace_is_blank_too(monkeypatch, routed) -> None:
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _SilentWhisper("  \n "))
    ws = _FakeWS()
    sess = StreamSession(ws, "office")  # type: ignore[arg-type]
    await sess._process_utterance(b"\x00" * 3_200, trigger="wake_word")
    assert routed == []
    assert any(f.get("type") == "response_end" for f in ws.sent_text)


@pytest.mark.asyncio
async def test_a_real_transcript_still_reaches_the_router(monkeypatch) -> None:
    """The guard is a gate on emptiness only. A real command goes through
    exactly as before - proven by the router being reached with it."""
    seen: list[str] = []

    async def _route(intent, ctx, session):
        seen.append(intent.transcript)
        raise RuntimeError("stop here - the rest of the path needs a DB")

    monkeypatch.setattr(streaming, "route", _route)
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _SilentWhisper("what time is it"))

    class _Probe:
        online = True

    ws = _FakeWS()
    ws.app.state.probe = _Probe()
    sess = StreamSession(ws, "office")  # type: ignore[arg-type]
    await sess._process_utterance(b"\x00" * 3_200, trigger="wake_word")
    assert seen == ["what time is it"]
    assert any(f.get("type") == "transcript" and f["text"] == "what time is it" for f in ws.sent_text)
