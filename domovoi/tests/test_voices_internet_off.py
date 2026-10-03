"""B12, the voice parts (CONTRACT 7.4): under INTERNET_ACCESS=never the web
refuses to register a Microsoft Edge voice or to sample one (409 before the
core is asked), and the voice handler says why a cloud voice can't speak in
the internet answer's words. Unset keeps today's behaviour and wording.

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

    async def fake_core(path, body, headers=None):
        calls.append({"path": path, "body": body})
        return 200, b"RIFFfake", {"content-type": "audio/wav", "x-sample-text": "hello"}

    monkeypatch.setattr(voices_api, "post_admin_bytes", fake_core)
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    return calls


def _web() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test",
                       headers={**BROWSER, **bearer("admin-token")})


@pytest.mark.asyncio
async def test_registering_an_edge_voice_is_refused_under_never(fake_registry) -> None:
    with egress.override_policy("never"):
        async with _web() as c:
            r = await c.post("/api/voices/edge", json={"name": "Aria", "voice_id": "en-US-AriaNeural"})
    assert r.status_code == 409
    assert r.json()["detail"] == egress.TURNED_OFF_REASON
    assert r.headers["x-domovoi-refusal"] == "internet-off"
    assert FakeRepo.created == []  # nothing stored


@pytest.mark.asyncio
async def test_registering_an_edge_voice_still_works_unanswered(fake_registry) -> None:
    async with _web() as c:
        r = await c.post("/api/voices/edge", json={"name": "Aria", "voice_id": "en-US-AriaNeural"})
    assert r.status_code == 201, r.text
    assert FakeRepo.created and FakeRepo.created[0]["engine"] == "edge"


@pytest.mark.asyncio
async def test_sampling_an_edge_voice_is_refused_before_the_core_is_asked(fake_registry) -> None:
    with egress.override_policy("never"):
        async with _web() as c:
            r = await c.get("/api/voices/1/sample")
    assert r.status_code == 409
    assert r.headers["x-domovoi-refusal"] == "internet-off"
    assert fake_registry == []  # the core was never asked


@pytest.mark.asyncio
async def test_sampling_a_piper_voice_still_works_under_never(fake_registry) -> None:
    with egress.override_policy("never"):
        async with _web() as c:
            r = await c.get("/api/voices/2/sample")
    assert r.status_code == 200
    assert fake_registry == [{"path": "/v1/admin/voices/sample", "body": {"name": "Amy"}}]


@pytest.mark.parametrize("answer", ["", "always", "sometimes"])
@pytest.mark.asyncio
async def test_sampling_an_edge_voice_asks_the_core_otherwise(fake_registry, answer) -> None:
    with egress.override_policy(answer):
        async with _web() as c:
            r = await c.get("/api/voices/1/sample")
    assert r.status_code == 200
    assert [x["body"] for x in fake_registry] == [{"name": "Guy"}]


# ─── The voice handler's words ────────────────────────────────────────────


@pytest.fixture
def edge_voice(monkeypatch):
    async def lookup(name, session):
        return {"id": 1, "name": "Guy", "engine": "edge", "model_ref": "en-US-GuyNeural"}

    monkeypatch.setattr(VoiceHandler, "_lookup", staticmethod(lookup))


@pytest.mark.asyncio
async def test_cloud_voice_refusal_names_the_internet_answer_under_never(edge_voice) -> None:
    h = VoiceHandler()
    ctx = Context(room_id="kitchen", online=False)
    with egress.override_policy("never"):
        sample = await h._sample_response("guy", ctx, None)
        switch = await h._switch_response("guy", ctx, None)
    assert sample.text == "Guy is a cloud voice and I'm set to stay off the internet, so I can't play it."
    assert switch.text.startswith("Guy is a cloud voice and I'm set to stay off the internet, so I can't switch")


@pytest.mark.asyncio
async def test_cloud_voice_refusal_keeps_todays_words_unanswered(edge_voice) -> None:
    h = VoiceHandler()
    ctx = Context(room_id="kitchen", online=False)
    sample = await h._sample_response("guy", ctx, None)
    switch = await h._switch_response("guy", ctx, None)
    assert sample.text == "Guy is a cloud voice and I'm offline right now, so I can't play it."
    assert switch.text == "Guy is a cloud voice and I'm offline right now, so I can't switch to it. Try a local voice."
