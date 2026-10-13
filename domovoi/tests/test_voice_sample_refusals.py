"""Settings -> Voices play buttons say why a sample can't play (Kamron,
2026-10-12: every button answered "sample failed: 502 Bad Gateway").

The web passes the core's own sentence through instead of a bare 502; the
core's 409 names the reason the voice's engine gave (Microsoft's voice
service refusing edge-tts with HTTP 403 that day). DB-free: the registry,
the core hop and the engines are fakes.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import egress
from domovoi.clients import tts as tts_mod
from domovoi.tests.auth_testkit import bearer, install_fake_db, web_app
from web.backend.api import voice as voice_api
from web.backend.api import voices as voices_api
from web.backend.domovoi_client import core_audio_refusal

BROWSER = {"X-Requested-With": "XMLHttpRequest"}
ROWS = [
    {"id": 1, "name": "Eric", "engine": "edge", "model_ref": "en-US-EricNeural", "is_default": False},
]


class FakeRepo:
    def __init__(self, _s) -> None:
        pass

    async def all(self):
        return list(ROWS)


def _core_answers(monkeypatch, module, status, body):
    calls = []

    async def fake_core(path, body_json, headers=None, timeout=30.0):
        calls.append((path, timeout))
        return status, body, {"content-type": "application/json"}

    monkeypatch.setattr(module, "post_admin_bytes", fake_core)
    return calls


@pytest.fixture
def registry(monkeypatch):
    @asynccontextmanager
    async def scope():
        yield object()

    monkeypatch.setattr(voices_api, "session_scope", scope)
    monkeypatch.setattr(voices_api, "VoicesRepository", FakeRepo)
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})


def _web() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test",
                       headers={**BROWSER, **bearer("admin-token")})


@pytest.mark.asyncio
async def test_the_cores_refusal_reaches_the_page_in_its_own_words(registry, monkeypatch) -> None:
    said = "Eric can't speak right now: Microsoft's voice service answered HTTP 403"
    calls = _core_answers(monkeypatch, voices_api, 409, json.dumps({"detail": said}).encode())
    async with _web() as c:
        r = await c.get("/api/voices/1/sample")
    assert r.status_code == 409 and r.json()["detail"] == said
    # a catalogue voice may download on its first sample: the web waits for it
    assert calls == [("/v1/admin/voices/sample", 120.0)]


@pytest.mark.asyncio
async def test_a_credential_refusal_keeps_its_status_and_sentence(registry, monkeypatch) -> None:
    """What every play button got before 19e9e43 (a bare fetch carried the
    cookie only): now the page sees the core's 403 and its words, which open
    the pair prompt, rather than "502 Bad Gateway"."""
    detail = "X-Device-Token required — the dashboard cookie does not authorize device-tier actions"
    _core_answers(monkeypatch, voices_api, 403, json.dumps({"detail": detail}).encode())
    async with _web() as c:
        r = await c.get("/api/voices/1/sample")
    assert r.status_code == 403 and r.json()["detail"] == detail


@pytest.mark.asyncio
async def test_an_unreachable_or_broken_core_is_a_502_that_says_so(registry, monkeypatch) -> None:
    _core_answers(monkeypatch, voices_api, 0, b"")
    async with _web() as c:
        r = await c.get("/api/voices/1/sample")
    assert r.status_code == 502 and r.json()["detail"] == "Eric's sample could not be made: the Domovoi server is unreachable"
    _core_answers(monkeypatch, voices_api, 500, b"Internal Server Error")
    async with _web() as c:
        r = await c.get("/api/voices/1/sample")
    assert r.status_code == 502 and r.json()["detail"] == "Eric's sample could not be made (HTTP 500)"


@pytest.mark.asyncio
async def test_a_preview_the_core_refuses_says_why(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    _core_answers(monkeypatch, voice_api, 502, json.dumps({"detail": "the voice en_US-norman-medium could not speak"}).encode())
    async with _web() as c:
        r = await c.post("/api/voice/pronunciations/preview", json={"word": "Kamron", "say_like": "KAM-ren"})
    assert r.status_code == 502
    assert r.json()["detail"] == "the preview could not be said: the voice en_US-norman-medium could not speak"


def test_core_audio_refusal_reads_only_a_detail_string():
    assert core_audio_refusal(404, b'{"detail": "no voice named \'X\'"}', "x").detail == "no voice named 'X'"
    assert core_audio_refusal(409, b'{"detail": {"code": 1}}', "x").detail == "x (HTTP 409)"
    assert core_audio_refusal(409, b"\xff\xfe", "x").detail == "x (HTTP 409)"


# ── the core: which rung failed, and why, in words ────────────────────────


class _Refused(Exception):
    """edge-tts's handshake error: a status, and the whole URL in the text."""

    status = 403

    def __str__(self) -> str:
        return "403, message='Invalid response status', url='wss://speech.platform.bing.com/x?TrustedClientToken=t'"


def test_an_edge_refusal_is_said_without_its_url(monkeypatch):
    def refuse(text, voice, speed):
        raise _Refused()

    monkeypatch.setattr(tts_mod, "_synth_edge_sync", refuse)
    monkeypatch.setattr(tts_mod, "_synth_piper_sync", lambda *a: tts_mod._pcm_to_wav_bytes(b"\x01\x02" * 50, 22050))
    client = tts_mod.RealTTSClient(preferred_engine="piper", edge_voice="x", piper_voice="p", speed=1.0)
    with egress.override_policy("always"):
        result = client._synth_detailed_blocking("hello", "edge", "en-US-EricNeural")
    assert result.engine == "piper"
    assert result.failures == ("edge: Microsoft's voice service answered HTTP 403",)
    # equality (what callers compare) ignores the reasons
    assert result == tts_mod.SynthResult(wav=result.wav, engine="piper", voice="p")


def test_failure_words():
    assert tts_mod.engine_failure_words("piper", ValueError("bad config")) == "piper: bad config"
    assert tts_mod.engine_failure_words("edge", egress.InternetTurnedOff("Microsoft Edge voice")) == \
        f"edge: {egress.TURNED_OFF_REASON}"
    assert tts_mod.engine_failure_words("system", RuntimeError()) == "system: RuntimeError"


# ── the core's route: a fallback is no sample, and the 409 says why ────────


class _VoiceRow:
    def __init__(self, _s) -> None:
        pass

    async def get_by_name(self, name):
        return {"id": 1, "name": name, "engine": "edge", "model_ref": "en-US-EricNeural", "is_default": False}


class _FallbackTTS:
    async def synthesize_detailed(self, text, *, engine=None, voice=None):
        return tts_mod.SynthResult(wav=tts_mod._pcm_to_wav_bytes(b"\x01\x02" * 50, 22050), engine="piper",
                                   voice="en_US-norman-medium",
                                   failures=("edge: Microsoft's voice service answered HTTP 403",))


@pytest.mark.asyncio
async def test_the_core_says_why_a_voice_cant_give_its_sample(monkeypatch) -> None:
    from domovoi import main as core_main
    from domovoi.tests.auth_testkit import core_app

    @asynccontextmanager
    async def scope():
        yield object()

    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    monkeypatch.setattr(core_main, "session_scope", scope)
    monkeypatch.setattr(core_main, "VoicesRepository", _VoiceRow)
    monkeypatch.setattr(core_main, "get_tts_client", lambda: _FallbackTTS())
    async with AsyncClient(transport=ASGITransport(app=core_app), base_url="http://test",
                           headers={**BROWSER, **bearer("admin-token")}) as c:
        r = await c.post("/v1/admin/voices/sample", json={"name": "Eric"})
    assert r.status_code == 409
    assert r.json()["detail"] == "Eric can't speak right now: Microsoft's voice service answered HTTP 403"
