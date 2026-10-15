"""B12, the voice parts (CONTRACT 7.4), since the Microsoft Edge voices
were dropped (2026-10-12): there is no cloud voice to register or refuse;
a Piper voice on this server samples under INTERNET_ACCESS=never as on any
answer, a leftover Edge row is refused without asking the core, and the
voice handler switches to any voice offline.

DB-free: the registry and the core hop are fakes.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import egress
from domovoi.handlers.voice import VoiceHandler
from domovoi.models import Context
from domovoi.tests.auth_testkit import bearer, install_fake_db, web_app
from web.backend.api import voices as voices_api

BROWSER = {"X-Requested-With": "XMLHttpRequest"}
ROWS = [
    {"id": 1, "name": "Guy", "engine": "edge", "model_ref": "en-US-GuyNeural", "is_default": False},
    {"id": 2, "name": "Amy", "engine": "piper", "model_ref": "en_US-amy-medium", "is_default": True},
]


class FakeRepo:
    created: list[dict] = []

    def __init__(self, _s) -> None:
        pass

    async def all(self):
        return list(ROWS)

    async def create(self, **kw):
        FakeRepo.created.append(kw)
        return 99

    async def get_by_name(self, name):
        return {"id": 99, "name": name, "engine": "edge", "model_ref": "x", "is_default": False}


@pytest.fixture
def fake_registry(monkeypatch):
    FakeRepo.created = []

    @asynccontextmanager
    async def scope():
        yield object()

    monkeypatch.setattr(voices_api, "session_scope", scope)
    monkeypatch.setattr(voices_api, "VoicesRepository", FakeRepo)

    async def no_rerender(_request):
        return None

    monkeypatch.setattr(voices_api, "_trigger_rerender", no_rerender)
    calls: list[dict] = []

    async def fake_core(path, body, headers=None, timeout=30.0):
        calls.append({"path": path, "body": body})
        return 200, b"RIFFfake", {"content-type": "audio/wav", "x-sample-text": "hello"}

    monkeypatch.setattr(voices_api, "post_admin_bytes", fake_core)
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    return calls


def _web() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test",
                       headers={**BROWSER, **bearer("admin-token")})


@pytest.mark.parametrize("answer", ["", "always", "never"])
@pytest.mark.asyncio
async def test_there_is_no_edge_registration_any_more(fake_registry, answer) -> None:
    with egress.override_policy(answer):
        async with _web() as c:
            r = await c.post("/api/voices/edge", json={"name": "Aria", "voice_id": "en-US-AriaNeural"})
    assert r.status_code in (404, 405)
    assert FakeRepo.created == []  # nothing stored


@pytest.mark.parametrize("answer", ["", "always", "never"])
@pytest.mark.asyncio
async def test_a_leftover_edge_row_is_refused_before_the_core_is_asked(fake_registry, answer) -> None:
    with egress.override_policy(answer):
        async with _web() as c:
            r = await c.get("/api/voices/1/sample")
    assert r.status_code == 409
    assert "Microsoft Edge voice" in r.json()["detail"]
    assert fake_registry == []  # the core was never asked


@pytest.mark.asyncio
async def test_sampling_a_piper_voice_still_works_under_never(fake_registry, monkeypatch, tmp_path) -> None:
    """A Piper voice on this server speaks offline: its sample is asked of
    the core as before (one not on the server has no sample to play:
    test_voice_samples.py)."""
    import json

    from domovoi.clients import tts
    from domovoi.phonemizer import default_id_map

    monkeypatch.setattr(tts.settings, "voice_models_dir", str(tmp_path))
    (tmp_path / "en_US-amy-medium.onnx").write_bytes(b"model")
    (tmp_path / "en_US-amy-medium.onnx.json").write_text(json.dumps({
        "espeak": {"voice": "en"}, "phoneme_type": "espeak", "phoneme_id_map": default_id_map()}))
    with egress.override_policy("never"):
        async with _web() as c:
            r = await c.get("/api/voices/2/sample")
    assert r.status_code == 200
    assert fake_registry == [{"path": "/v1/admin/voices/sample", "body": {"name": "Amy"}}]


# ─── The voice handler's words ────────────────────────────────────────────


@pytest.fixture
def piper_voice(monkeypatch):
    async def lookup(name, session):
        return {"id": 1, "name": "Kristin", "engine": "piper", "model_ref": "en_US-kristin-medium"}

    monkeypatch.setattr(VoiceHandler, "_lookup", staticmethod(lookup))


@pytest.mark.parametrize("answer", ["", "never"])
@pytest.mark.asyncio
async def test_every_voice_samples_and_switches_offline(piper_voice, answer) -> None:
    h = VoiceHandler()
    ctx = Context(room_id="kitchen", online=False)
    with egress.override_policy(answer):
        sample = await h._sample_response("kristin", ctx, None)
        switch = await h._switch_response("kristin", ctx, None)
    assert sample.voice_override == "Kristin" and "cloud" not in sample.text
    assert switch.pi_action == "set_voice" and switch.pi_action_arg == "Kristin"
