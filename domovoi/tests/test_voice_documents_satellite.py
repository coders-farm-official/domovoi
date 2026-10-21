"""The D7 gate: "read me …" and "take a letter" from a satellite.

A simulated satellite on the real ``/v1/stream/{room}`` WebSocket (the
pairing check accepted, Whisper scripted, TTS faked), with the real router,
the lane database and a Documents folder of our own. What the satellite
sees is the contract: the reply text in ``response_start``, the audio, and
``response_end`` with ``expect_followup`` set while the room keeps
listening (dictation, "Shall I go on?").

* reading: a short document read whole; a long one says its length, reads
  a part, "Shall I go on?" keeps the room listening, "stop" ends it;
* a letter: "take a letter to the landlord" → "Who's it from?" (no voice
  ID on this satellite) → dictation → "end of letter" → "Writing it up
  now." → the room hears "Saved as …" as an announcement on the same socket
  when it's written, and the .docx is in Documents → From Chat;
* the quiet room: no "end of letter", the follow-up window closes with
  nothing said, and the letter is written up and announced all the same.
"""

from __future__ import annotations

import io
import json
import queue
import threading
import time
import wave

import pytest
from fastapi.testclient import TestClient

from domovoi import streaming
from domovoi.config import settings
from domovoi.drafts import docx_file, templates
from domovoi.main import app
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import requires_db
from domovoi.voice_docs import letters, reading

pytestmark = requires_db

LETTER = ("Sam Lee\n\nOctober 21, 2026\n\nDear Mr. Okafor,\n\nThe heating in flat 3B has been broken since "
          "Monday.\n\nBest wishes,\n\nSam Lee\n")


def _wav(sample_rate: int = 22_050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * 20)
    return buf.getvalue()


class _TTS:
    async def synthesize(self, text: str, *, engine=None, voice=None) -> bytes:
        return _wav()


class _Whisper:
    """Says what the test queued, one utterance at a time."""

    def __init__(self) -> None:
        self.next: list[str] = []

    async def transcribe(self, pcm: bytes) -> str:
        return self.next.pop(0)


@pytest.fixture
def satellite(tmp_path, monkeypatch):
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "House rules.md").write_text("# House rules\n\nShoes off at the door. Quiet after ten.\n",
                                         encoding="utf-8")
    long = "\n\n".join(f"Clause {i}. The tenant pays the rent on the first of the month and keeps the flat "
                       f"clean, and the landlord fixes the heating within three days of being told." for i in
                       range(1, 40))
    (docs / "Lease.docx").write_bytes(docx_file.render(long, templates.get("letter-block"), title="Lease"))
    monkeypatch.setattr(settings, "documents_dir", str(docs))
    reading._CACHE.clear()

    async def _accept(self, ctrl):
        self.token_authenticated = True
        return True

    monkeypatch.setattr(StreamSession, "_validate_pairing", _accept)
    whisper = _Whisper()
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: whisper)
    monkeypatch.setattr(streaming, "get_tts_client", lambda: _TTS())

    async def _generate(state, dictation):
        _generate.dictations.append(dictation)
        return LETTER, "llama3.2:3b"

    _generate.dictations = []
    monkeypatch.setattr(letters, "generate", _generate)
    whisper.docs = docs
    whisper.model = _generate
    return whisper


def _receive(ws, timeout: float = 20.0) -> dict:
    """The next frame, or fail (never hang) when none comes."""
    box: "queue.Queue[dict]" = queue.Queue()
    t = threading.Thread(target=lambda: box.put(ws.receive()), daemon=True)
    t.start()
    try:
        return box.get(timeout=timeout)
    except queue.Empty:
        raise AssertionError(f"no frame from the core within {timeout} s") from None


def _reply(ws) -> tuple[str, dict]:
    """A reply (or an announcement): its text and its response_end."""
    text = None
    while True:
        msg = _receive(ws)
        if msg.get("text") is None:
            continue  # audio
        frame = json.loads(msg["text"])
        if frame["type"] == "response_start":
            text = frame["text"]
        elif frame["type"] == "response_end":
            assert text is not None, frame
            return text, frame


def _say(ws, whisper: _Whisper, words: str, trigger: str = "wake_word") -> tuple[str, dict]:
    whisper.next.append(words)
    ws.send_text(json.dumps({"type": "utterance_start", "trigger": trigger}))
    ws.send_bytes(b"\x00" * 3200)
    ws.send_text(json.dumps({"type": "utterance_end"}))
    return _reply(ws)


def test_read_me_from_a_satellite(satellite):
    with TestClient(app) as client, client.websocket_connect("/v1/stream/d7-gate") as ws:
        ws.send_text(json.dumps({"type": "hello", "room_id": "d7-gate"}))
        assert json.loads(_receive(ws)["text"])["type"] == "ready"

        text, end = _say(ws, satellite, "Read me the house rules.")
        assert text == "House rules. Shoes off at the door. Quiet after ten."
        assert end["expect_followup"] is False

        text, end = _say(ws, satellite, "Read me the lease.")
        assert text == "Lease. It's 3 pages, about 7 minutes. The whole thing, or just the start?"
        assert end["expect_followup"] is True
        text, end = _say(ws, satellite, "The whole thing.", trigger="followup")
        assert text.startswith("Clause 1. The tenant") and text.endswith("Shall I go on?")
        assert end["expect_followup"] is True
        text, end = _say(ws, satellite, "Stop.", trigger="barge_in")
        assert text == "Okay, I've stopped reading." and end["expect_followup"] is False


def test_take_a_letter_from_a_satellite(satellite):
    with TestClient(app) as client, client.websocket_connect("/v1/stream/d7-gate") as ws:
        ws.send_text(json.dumps({"type": "hello", "room_id": "d7-gate"}))
        assert json.loads(_receive(ws)["text"])["type"] == "ready"

        text, end = _say(ws, satellite, "Take a letter to the landlord.")
        assert text == "Who's it from?" and end["expect_followup"] is True
        text, end = _say(ws, satellite, "Sam Lee.", trigger="followup")
        assert text == "Thanks, Sam Lee. Go ahead. Say end of letter when you're done."
        assert end["expect_followup"] is True
        text, end = _say(ws, satellite, "The heating in flat 3B has been broken since Monday.", trigger="followup")
        assert text == "Go on." and end["expect_followup"] is True
        text, end = _say(ws, satellite, "End of letter.", trigger="followup")
        assert text == "Writing it up now. I'll tell you when it's saved." and end["expect_followup"] is False
        # The announcement, on the same socket, once it's written and saved.
        text, end = _reply(ws)
        assert text == "Saved as Letter to the landlord, in Documents, From Chat. Say read it back to hear it."
        assert satellite.model.dictations == ["the heating in flat 3b has been broken since monday"]
        assert (satellite.docs / "From Chat" / "Letter to the landlord v1.docx").is_file()

        text, end = _say(ws, satellite, "Read it back.")
        assert text.startswith("Sam Lee. October 21, 2026. Dear Mr. Okafor,")


def test_a_quiet_room_ends_the_letter_from_a_satellite(satellite, monkeypatch):
    monkeypatch.setattr(letters, "IDLE_SEC", 0.5)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/d7-gate") as ws:
        ws.send_text(json.dumps({"type": "hello", "room_id": "d7-gate"}))
        assert json.loads(_receive(ws)["text"])["type"] == "ready"
        _say(ws, satellite, "Take a letter to the landlord.")
        _say(ws, satellite, "Nobody.", trigger="followup")
        text, end = _say(ws, satellite, "The heating is broken again.", trigger="followup")
        assert text == "Go on." and end["expect_followup"] is True
        # The satellite's follow-up window closes with nothing said.
        started = time.monotonic()
        text, end = _reply(ws)
        assert text == "Saved as Letter to the landlord, in Documents, From Chat. Say read it back to hear it."
        assert time.monotonic() - started < 15
