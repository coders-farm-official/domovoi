"""The chime before an announcement or a broadcast (domovoi/announce_chime.py).

DB-free: a fake socket that keeps the audio bytes, a stub TTS (22.05 kHz,
a constant tone so the voice is easy to tell from the chime).
"""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from domovoi import announce_chime, streaming
from domovoi.config import settings
from domovoi.sdk import speech as speech_mod
from domovoi.sdk.speech import SpeechAPI, bind_active_sessions
from domovoi.streaming import StreamSession

RATE = 22_050
VOICE = b"\x10\x27" * 2205  # 0.1 s of a loud constant: no chime sample is this


def _wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


class _TTS:
    async def synthesize(self, text, *, engine=None, voice=None):
        return _wav(VOICE)


class _WS:
    def __init__(self) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(
            active_sessions={}, satellite_voice={}, resumable_music={},
            wifi_status={}, satellite_volume={}, satellite_config={},
            greeting_phrases=[], current_playlist={},
            probe=SimpleNamespace(online=True),
        ))
        self.audio = b""

    async def send_text(self, t: str) -> None:
        json.loads(t)

    async def send_bytes(self, b: bytes) -> None:
        self.audio += b


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    async def _voice(_name):
        return (None, None)

    monkeypatch.setattr(streaming, "get_tts_client", lambda: _TTS())
    monkeypatch.setattr(streaming, "resolve_voice", _voice)
    monkeypatch.setattr(settings, "announce_chime_enabled", True)
    original = speech_mod._active_sessions_provider
    yield
    speech_mod._active_sessions_provider = original


def _room(name: str) -> tuple[StreamSession, _WS]:
    ws = _WS()
    sess = StreamSession(ws, name)  # type: ignore[arg-type]
    ws.app.state.active_sessions[name] = sess
    return sess, ws


def _chime() -> bytes:
    return announce_chime.chime_pcm(RATE)


@pytest.mark.asyncio
async def test_an_announcement_starts_with_the_chime():
    sess, ws = _room("kitchen")
    await sess.announce("dinner is ready", kind="user")
    assert ws.audio == _chime() + VOICE
    assert len(_chime()) > 0


@pytest.mark.asyncio
async def test_a_plugin_announcement_gets_the_chime():
    sess, ws = _room("kitchen")
    bind_active_sessions(lambda: {"kitchen": sess})
    assert await SpeechAPI("kiwix").announce("kitchen", "summary ready") == ["kitchen"]
    assert ws.audio.startswith(_chime())
    assert ws.audio.endswith(VOICE)


@pytest.mark.asyncio
async def test_a_broadcast_chimes_every_room():
    rooms = [_room("kitchen"), _room("office")]
    for sess, _ in rooms:
        await sess.announce("time for bed", kind="user")
    assert all(ws.audio == _chime() + VOICE for _, ws in rooms)


@pytest.mark.asyncio
async def test_the_setting_off_means_no_chime(monkeypatch):
    monkeypatch.setattr(settings, "announce_chime_enabled", False)
    sess, ws = _room("kitchen")
    await sess.announce("dinner is ready", kind="user")
    assert ws.audio == VOICE


def test_a_normal_reply_does_not_use_the_chime():
    # Only announce() joins the chime on; the reply path never touches it.
    src = Path(streaming.__file__).read_text(encoding="utf-8")
    assert src.count("announce_chime.chime_pcm(") == 1
    assert src.index("announce_chime.chime_pcm(") > src.index("async def _announce(")
    assert src.index("announce_chime.chime_pcm(") < src.index("async def _send_announcement(")


def test_the_chime_is_short_and_at_the_drop_in_rate():
    assert announce_chime.SAMPLE_RATE == 16_000
    assert 0.3 < len(announce_chime.CHIME_PCM) / 2 / 16_000 < 1.0
