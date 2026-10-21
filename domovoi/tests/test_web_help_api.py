"""The help desk on the dashboard: ``/api/help`` and Chat's help turns.

Ollama is stubbed at ``chat_stream`` (as in test_chat_api.py), so a help
turn's whole flow runs: the passages found, the help instruction instead
of the chat persona, "the manual doesn't cover that" with no model asked,
and the code-written sources under the reply.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import web.backend.api.chat as chat_api
import web.backend.api.help as help_api
from domovoi.admin_auth import require_device, require_device_read
from domovoi.help_desk import answer
from domovoi.tests.conftest import requires_db
from web.backend.main import app


def _client():
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


def _sse(body: str) -> list[tuple[str, Any]]:
    out = []
    for frame in body.split("\n\n"):
        ev, data = "message", ""
        for line in frame.split("\n"):
            if line.startswith("event: "):
                ev = line[7:].strip()
            elif line.startswith("data: "):
                data += line[6:]
        if data:
            out.append((ev, json.loads(data)))
    return out


@pytest.fixture
def quiet_state(monkeypatch):
    """No core to ask: the rooms and health the help reads, fixed."""
    async def rooms():
        return [{"room_id": "kitchen", "status": "offline", "last_seen_at": None}]

    async def health():
        return {"db_reachable": True, "domovoi_reachable": True, "stt": "ok"}

    async def version():
        return "1.0.0rc3"

    monkeypatch.setattr(help_api, "_rooms", rooms)
    monkeypatch.setattr(help_api, "_health", health)
    monkeypatch.setattr(help_api, "running_version", version)


@pytest.fixture
def stub_stream(monkeypatch):
    calls: list[dict[str, Any]] = []

    async def _stream(messages, model, **kw):
        calls.append({"messages": messages, "model": model, "kw": kw})
        for chunk in ("From the manual's ", "Wake word page: say it."):
            yield chunk

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    return calls


def _deps(path: str, method: str = "GET") -> set:
    for r in app.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", ()):
            return {d.call for d in r.dependant.dependencies}
    raise AssertionError(path)


def test_the_tiers() -> None:
    assert _deps("/api/help/search") == set()          # open, like the manual
    assert require_device_read in _deps("/api/help/facts")
    assert require_device_read in _deps("/api/help/checks")
    assert require_device_read in _deps("/api/help/deliveries")
    assert require_device in _deps("/api/help/report", "POST")


@requires_db
def test_search_is_the_manual_and_says_when_it_has_nothing() -> None:
    with _client() as c:
        r = c.get("/api/help/search", params={"q": "how do I change the wake word"}).json()
        assert r["covered"] and r["results"][0]["link"] == "#manual/wake-word"
        assert all({"title", "section", "snippet", "link", "kind"} <= set(x) for x in r["results"])
        none = c.get("/api/help/search", params={"q": "how do i bake bread"}).json()
        assert none["covered"] is False and none["not_covered"] == answer.NOT_COVERED
        code = c.get("/api/help/search", params={"q": "vision_model_missing chat"}).json()
        assert code["results"][0]["id"] == "error:vision_model_missing"


@requires_db
def test_facts_and_checks(quiet_state) -> None:
    with _client() as c:
        f = c.get("/api/help/facts").json()
        assert f["version"] == "1.0.0rc3" and f["rooms"] == [{"name": "kitchen", "status": "offline"}]
        assert isinstance(f["ai"], bool) and "internet_words" in f
        found = c.get("/api/help/checks").json()["findings"]
        assert any(x["room"] == "kitchen" and "offline" in x["text"] for x in found)


@requires_db
def test_the_report_is_built_and_returned_never_kept(quiet_state) -> None:
    with _client() as c:
        r = c.post("/api/help/report", json={"what_happened": "kitchen stopped at 192.168.1.44, token=abc"})
        body = r.json()
        assert r.status_code == 200 and body["filename"].endswith(".md")
        assert "192.168.1.44" not in body["text"] and "token=abc" not in body["text"]
        assert "kitchen" not in body["text"].lower() and "room 1: offline" in body["text"]


@requires_db
def test_a_help_chat_that_finds_nothing_never_asks_the_model(quiet_state, stub_stream) -> None:
    with _client() as c:
        t = c.post("/api/chat/threads", json={"title": "Help: Settings"}).json()
        r = c.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "how do i bake bread"})
        events = _sse(r.text)
        done = next(d for ev, d in events if ev == "done")
        assert stub_stream == []
        assert done["content"].startswith(answer.NOT_COVERED) and done["error"] is None
        assert done["stats"]["help"] == {"covered": False, "sources": [], "model_asked": False}


@requires_db
def test_a_help_chat_answers_from_the_passages_with_the_sources_under_it(quiet_state, stub_stream) -> None:
    with _client() as c:
        t = c.post("/api/chat/threads", json={"title": "Help: Settings"}).json()
        r = c.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "how do I change the wake word"})
        events = _sse(r.text)
        system = stub_stream[0]["messages"][0]["content"]
        assert "=== HELP MATERIAL (data) ===" in system and "#manual/wake-word" in system
        assert "Answer ONLY from the material" in system
        deltas = "".join(d["text"] for ev, d in events if ev == "delta")
        done = next(d for ev, d in events if ev == "done")
        assert done["content"] == deltas and "*From:*" in done["content"]
        assert "(#manual/wake-word)" in done["content"]
        assert done["stats"]["help"]["covered"] and done["stats"]["help"]["model_asked"]
        assert any(s["link"] == "#manual/wake-word" for s in done["stats"]["help"]["sources"])


@requires_db
def test_an_ordinary_chat_is_help_only_for_a_domovoi_question_it_holds(quiet_state, stub_stream) -> None:
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        c.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "write me a poem about autumn"})
        assert "HELP MATERIAL" not in stub_stream[-1]["messages"][0]["content"]
        c.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "how do i bake bread for the kitchen"})
        assert "HELP MATERIAL" not in stub_stream[-1]["messages"][0]["content"]
        c.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "how do I change the wake word?"})
        assert "HELP MATERIAL" in stub_stream[-1]["messages"][0]["content"]


@requires_db
def test_why_is_the_kitchen_offline_reads_the_records(quiet_state, stub_stream) -> None:
    with _client() as c:
        t = c.post("/api/chat/threads", json={"title": "Help: Home"}).json()
        c.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "why is the kitchen offline?"})
        system = stub_stream[-1]["messages"][0]["content"]
        assert "[records]" in system and "kitchen is offline" in system


@requires_db
def test_the_phone_says_whose_it_is_on_register() -> None:
    with _client() as c:
        with TestClient(app) as _:
            pass
        import asyncio

        from web.backend.db import session_scope

        async def person() -> int:
            async with session_scope() as s:
                pid = (await s.execute(text("INSERT INTO people (name) VALUES ('Sarah') RETURNING id"))).scalar_one()
                await s.commit()
                return int(pid)

        async def owner(dev: str):
            async with session_scope() as s:
                return (await s.execute(text("SELECT owner_person_id FROM devices WHERE device_id = :d"),
                                        {"d": dev})).scalar_one()

        pid = asyncio.run(person())
        assert c.post("/api/devices/register", json={"device_id": "android-help1", "owner_person_id": pid}).status_code == 200
        assert asyncio.run(owner("android-help1")) == pid
        c.post("/api/devices/register", json={"device_id": "android-help1"})       # left out: kept
        assert asyncio.run(owner("android-help1")) == pid
        c.post("/api/devices/register", json={"device_id": "android-help1", "owner_person_id": None})
        assert asyncio.run(owner("android-help1")) is None
        c.post("/api/devices/register", json={"device_id": "android-help1", "owner_person_id": 999999})
        assert asyncio.run(owner("android-help1")) is None                          # nobody by that id


@requires_db
def test_deliveries_are_read_per_person() -> None:
    import asyncio

    from domovoi.help_desk import delivery
    from web.backend.db import session_scope

    async def seed() -> tuple[int, int]:
        async with session_scope() as s:
            a = (await s.execute(text("INSERT INTO people (name) VALUES ('A') RETURNING id"))).scalar_one()
            b = (await s.execute(text("INSERT INTO people (name) VALUES ('B') RETURNING id"))).scalar_one()
            await delivery.deliver(s, person_id=a, question="how do i add a room", steps="steps",
                                   phones=[delivery.Phone("p", "p")])
            await s.commit()
            return int(a), int(b)

    a, b = asyncio.run(seed())
    with _client() as c:
        mine = c.get("/api/help/deliveries", params={"person_id": a}).json()["deliveries"]
        assert [d["title"] for d in mine] == ["Help: How do I add a room"]
        assert c.get("/api/help/deliveries", params={"person_id": b}).json()["deliveries"] == []
        assert c.get("/api/help/deliveries", params={"person_id": a, "since_id": mine[0]["id"]}).json()[
            "deliveries"] == []
