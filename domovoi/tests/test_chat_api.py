"""Endpoint tests for the text-chat API — web/backend/api/chat.py.

Ollama is stubbed at the ``chat_stream`` boundary so the SSE send flow runs
end-to-end: user row persisted → deltas streamed → assistant row persisted
(including the vision-model switch when a message carries an upload).
Uploads use a tmp UPLOADS_DIR via monkeypatch.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

import web.backend.api.chat as chat_api
from domovoi.tests.conftest import requires_db
from web.backend.main import app


def _client():
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


@pytest.fixture
def uploads_dir(tmp_path, monkeypatch):
    d = tmp_path / "chat_uploads"
    monkeypatch.setattr(chat_api, "UPLOADS_DIR", d)
    return d


@pytest.fixture
def stub_stream(monkeypatch):
    """Capture the (messages, model) each send used; yield canned deltas."""
    calls: list[dict[str, Any]] = []

    async def _stream(messages, model, **kw):
        calls.append({"messages": messages, "model": model})
        for chunk in ("Hello", " there"):
            yield chunk

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    return calls


def _sse_events(text_body: str) -> list[tuple[str, Any]]:
    events = []
    for frame in text_body.split("\n\n"):
        ev, data = "message", ""
        for line in frame.split("\n"):
            if line.startswith("event: "):
                ev = line[7:].strip()
            elif line.startswith("data: "):
                data += line[6:]
        if data:
            events.append((ev, json.loads(data)))
    return events


# ─── Threads CRUD ───────────────────────────────────────────────────────────
@requires_db
def test_thread_lifecycle(stub_stream):
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        assert t["title"] is None and t["message_count"] == 0

        listed = c.get("/api/chat/threads").json()["threads"]
        assert any(row["id"] == t["id"] for row in listed)

        assert c.patch(f"/api/chat/threads/{t['id']}", json={"title": "Renamed"}).status_code == 200
        assert c.patch("/api/chat/threads/999999", json={"title": "x"}).status_code == 404

        assert c.delete(f"/api/chat/threads/{t['id']}").json() == {"deleted": t["id"]}
        assert c.delete(f"/api/chat/threads/{t['id']}").status_code == 404


# ─── Send / SSE ─────────────────────────────────────────────────────────────
@requires_db
def test_send_streams_and_persists(stub_stream):
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        r = c.post(f"/api/chat/threads/{t['id']}/messages",
                   json={"content": "hi there, domovoi"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = _sse_events(r.text)
        deltas = [d["text"] for ev, d in events if ev == "delta"]
        assert deltas == ["Hello", " there"]
        done = next(d for ev, d in events if ev == "done")
        assert done["role"] == "assistant" and done["content"] == "Hello there"
        assert done["error"] is None

        # Both rows persisted; thread titled from the first user line.
        msgs = c.get(f"/api/chat/threads/{t['id']}/messages").json()["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        threads = c.get("/api/chat/threads").json()["threads"]
        row = next(x for x in threads if x["id"] == t["id"])
        assert row["title"] == "hi there, domovoi"
        assert row["message_count"] == 2

        # History sent to the model included the user turn, default QA model.
        assert stub_stream[-1]["model"] == chat_api.core_settings.ollama_model
        assert stub_stream[-1]["messages"][-1] == {"role": "user", "content": "hi there, domovoi"}


@requires_db
def test_a_reply_keeps_its_stats_and_the_sender_its_device(monkeypatch):
    """V017: the assistant row carries the reply's figures (Ollama's and the
    server's own), the user row the sending device — read back by name."""
    async def _stream(messages, model, stats=None, **kw):
        yield "Hi"
        if stats is not None:
            stats.update({
                "done_reason": "length", "prompt_eval_count": 50, "eval_count": 20,
                "total_duration": 3_000_000_000, "load_duration": 1_500_000_000,
                "prompt_eval_duration": 500_000_000, "eval_duration": 1_000_000_000,
            })

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    with _client() as c:
        assert c.post("/api/devices/register", json={"device_id": "chat-test-phone", "name": "Kitchen tablet"}).status_code == 200
        t = c.post("/api/chat/threads", json={}).json()
        r = c.post(f"/api/chat/threads/{t['id']}/messages",
                   json={"content": "hello", "device_id": "chat-test-phone"})
        done = next(d for ev, d in _sse_events(r.text) if ev == "done")
        st = done["stats"]
        assert st["prompt_tokens"] == 50 and st["output_tokens"] == 20
        assert st["total_ms"] == 3000.0 and st["load_ms"] == 1500.0
        assert st["prompt_ms"] == 500.0 and st["generate_ms"] == 1000.0
        assert st["tokens_per_sec"] == 20.0
        assert st["done_reason"] == "length"
        assert st["model_role"] == "chat"
        assert st["context_sent"] == 1 and st["context_in_thread"] == 1
        assert st["context_limit"] == chat_api._HISTORY_LIMIT
        assert st["first_token_ms"] >= 0 and st["wall_ms"] >= st["first_token_ms"]

        msgs = c.get(f"/api/chat/threads/{t['id']}/messages").json()["messages"]
        user, reply = msgs
        assert user["device_id"] == "chat-test-phone"
        assert user["device_name"] == "Kitchen tablet"
        assert user["stats"] is None
        assert reply["stats"] == st and reply["device_id"] is None


@requires_db
@pytest.mark.parametrize("bad", ["../../etc", "a b", "<img src=x>", "x"])
def test_a_malformed_device_id_is_not_kept(stub_stream, bad):
    """Security review 2026-09-30: the id is self-asserted (like queue
    provenance) and was stored as sent; now only a well-formed one is kept,
    as every other route that reads a device id does."""
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        r = c.post(f"/api/chat/threads/{t['id']}/messages",
                   json={"content": "hello", "device_id": bad})
        assert r.status_code == 200
        user = c.get(f"/api/chat/threads/{t['id']}/messages").json()["messages"][0]
        assert user["role"] == "user" and user["device_id"] is None


def test_reply_stats_leave_out_what_is_unknown() -> None:
    """A failed stream reports nothing from Ollama: only the server's own
    figures remain, and no speed is invented from missing durations."""
    st = chat_api._reply_stats(
        {}, first_token_ms=None, wall_ms=12.34, context_sent=3,
        context_in_thread=40, model_role="vision",
    )
    assert st == {
        "wall_ms": 12.3, "context_sent": 3, "context_in_thread": 40,
        "context_limit": chat_api._HISTORY_LIMIT, "model_role": "vision",
        **({"num_ctx": chat_api.core_settings.ollama_num_ctx} if chat_api.core_settings.ollama_num_ctx else {}),
    }


@requires_db
def test_send_error_lands_on_row(monkeypatch):
    async def _boom(messages, model, **kw):
        raise RuntimeError("model exploded")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _boom)
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        r = c.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "hi"})
        events = _sse_events(r.text)
        assert any(ev == "error" and "model exploded" in d["detail"] for ev, d in events)
        msgs = c.get(f"/api/chat/threads/{t['id']}/messages").json()["messages"]
        assert msgs[-1]["role"] == "assistant" and "model exploded" in msgs[-1]["error"]


# ─── Uploads + vision switch ────────────────────────────────────────────────
@requires_db
def test_upload_and_vision_model_switch(uploads_dir, stub_stream):
    with _client() as c:
        up = c.post("/api/chat/uploads",
                    files={"file": ("cat.png", io.BytesIO(b"\x89PNGfake"), "image/png")})
        assert up.status_code == 200
        token = up.json()["token"]
        assert (uploads_dir / f"{token}.png").is_file()

        served = c.get(f"/api/chat/uploads/{token}")
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/png"
        # Read on the device tier, so no shared cache may keep a copy
        # for the next device that asks.
        assert served.headers["cache-control"].startswith("private")
        assert c.get("/api/chat/uploads/deadbeef").status_code == 404

        bad = c.post("/api/chat/uploads",
                     files={"file": ("x.exe", io.BytesIO(b"mz"), "application/x-dos")})
        assert bad.status_code == 415

        t = c.post("/api/chat/threads", json={}).json()
        r = c.post(f"/api/chat/threads/{t['id']}/messages",
                   json={"content": "what is this?",
                         "images": [{"token": token, "name": "cat.png"}]})
        assert r.status_code == 200
        # Vision model answered, and the image rode along base64.
        assert stub_stream[-1]["model"] == chat_api.core_settings.ollama_vision_model
        assert "images" in stub_stream[-1]["messages"][-1]
        msgs = c.get(f"/api/chat/threads/{t['id']}/messages").json()["messages"]
        assert msgs[0]["images"] == [{"token": token, "name": "cat.png"}]

        # Deleting the thread removes the now-unreferenced upload file.
        c.delete(f"/api/chat/threads/{t['id']}")
        assert not (uploads_dir / f"{token}.png").is_file()


@requires_db
def test_chat_models_endpoint(monkeypatch):
    async def _tags(**kw):
        return [{"name": "llama3.2:3b"}, {"name": "qwen2.5vl:7b"}]

    monkeypatch.setattr(chat_api.ollama_client, "list_models", _tags)
    with _client() as c:
        r = c.get("/api/chat/models").json()
    assert r["installed"] == ["llama3.2:3b", "qwen2.5vl:7b"]
    assert r["default_model"] == chat_api.core_settings.ollama_model
    assert r["vision_model"] == chat_api.core_settings.ollama_vision_model
