"""Endpoint tests for the text-chat API — web/backend/api/chat.py.

Ollama is stubbed at the ``chat_stream`` boundary so the SSE send flow runs
end-to-end: user row persisted → deltas streamed → assistant row persisted
(including the vision-model switch when a message carries an upload).
Uploads use a tmp UPLOADS_DIR via monkeypatch.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import web.backend.api.chat as chat_api
from domovoi.clients import ollama as ollama_mod
from domovoi.tests.conftest import requires_db
from web.backend.main import app


def _tiny_png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


# A real picture: since the delta review (S7) an upload must be one.
TINY_PNG = _tiny_png()

# Nothing a user reads may carry these (the owner's 2026-10-11 report: a
# raw httpx error naming http://localhost:11434/api/chat and a help URL).
_LEAKS = ("localhost", "127.0.0.1", "11434", "http", "mozilla", "api/chat", "Client error")


def _client():
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


@pytest.fixture
def uploads_dir(tmp_path, monkeypatch):
    d = tmp_path / "chat_uploads"
    monkeypatch.setattr(chat_api, "UPLOADS_DIR", d)
    return d


@pytest.fixture
def stub_stream(monkeypatch):
    """Capture the (messages, model, other arguments) each send used; yield
    canned deltas."""
    calls: list[dict[str, Any]] = []

    async def _stream(messages, model, **kw):
        calls.append({"messages": messages, "model": model, "kw": kw})
        for chunk in ("Hello", " there"):
            yield chunk

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    return calls


@pytest.fixture
def tags(monkeypatch):
    """What Ollama reports installed (``/api/tags``): set ``tags.models``
    to a list of names, or to None for "Ollama couldn't be asked"."""
    class _Tags:
        models: list[str] | None = []
        asked = 0

    async def _tags_or_none(*a, **kw):
        _Tags.asked += 1
        return None if _Tags.models is None else [{"name": n} for n in _Tags.models]

    async def _no_model_system(*a, **kw):
        return None

    monkeypatch.setattr(chat_api.ollama_client, "tags_or_none", _tags_or_none)
    # A picture turn also asks Ollama for the model's own SYSTEM (/api/show).
    monkeypatch.setattr(chat_api.ollama_client, "model_system_or_none", _no_model_system)
    return _Tags


def _upload(c, name="cat.png") -> str:
    up = c.post("/api/chat/uploads", files={"file": (name, io.BytesIO(TINY_PNG), "image/png")})
    assert up.status_code == 200
    return up.json()["token"]


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
def test_send_error_lands_on_row_in_plain_words(monkeypatch):
    """The exception's own text goes to the log, never to the user."""
    async def _boom(messages, model, **kw):
        raise RuntimeError("model exploded at http://127.0.0.1:11434/api/chat")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _boom)
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        r = c.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "hi"})
        events = _sse_events(r.text)
        err = next(d for ev, d in events if ev == "error")
        assert err["code"] == "model_error"
        assert err["model"] == chat_api.core_settings.ollama_model
        assert err["detail"].startswith("The language model couldn't answer this message.")
        assert "exploded" not in r.text
        msgs = c.get(f"/api/chat/threads/{t['id']}/messages").json()["messages"]
        assert msgs[-1]["role"] == "assistant" and msgs[-1]["error"] == err["detail"]
        assert msgs[-1]["stats"]["error_code"] == "model_error"


def _connect_error():
    return httpx.ConnectError(
        "Client error for url 'http://localhost:11434/api/chat' For more information check: "
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404")


@requires_db
@pytest.mark.parametrize("exc,code", [
    (ollama_mod.OllamaChatError(404, "model 'llama3.2:3b' not found"), "model_missing"),
    (_connect_error(), "model_unreachable"),
    (httpx.ConnectTimeout("timed out connecting to http://localhost:11434"), "model_unreachable"),
    (httpx.PoolTimeout("timed out at http://localhost:11434"), "model_timeout"),
    (ollama_mod.OllamaChatError(500, "model requires more system memory (9 GiB) than is available"),
     "model_memory"),
    (ollama_mod.OllamaChatError(503, "server busy http://localhost:11434"), "model_error"),
])
def test_each_ollama_failure_reaches_the_user_plainly(monkeypatch, exc, code):
    async def _fail(messages, model, **kw):
        raise exc
        yield  # pragma: no cover

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _fail)
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        r = c.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "hi"})
        err = next(d for ev, d in _sse_events(r.text) if ev == "error")
        done = next(d for ev, d in _sse_events(r.text) if ev == "done")
        assert err["code"] == code and done["stats"]["error_code"] == code
        assert done["error"] == err["detail"]
        for leak in _LEAKS:
            assert leak not in r.text, leak
        # The cold-start figures stay: a failed reply still has its timings.
        assert done["stats"]["model_role"] == "chat" and "wall_ms" in done["stats"]


@requires_db
def test_a_partial_reply_keeps_its_words_and_says_why_it_stopped(monkeypatch):
    async def _cut(messages, model, **kw):
        yield "Once upon"
        raise httpx.RemoteProtocolError("peer closed connection (http://localhost:11434)")

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _cut)
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        r = c.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "a story"})
        done = next(d for ev, d in _sse_events(r.text) if ev == "done")
        assert done["content"] == "Once upon"
        assert done["stats"]["error_code"] == "model_unreachable"
        assert "localhost" not in r.text


@requires_db
def test_an_empty_reply_says_so_plainly(monkeypatch):
    async def _nothing(messages, model, **kw):
        return
        yield  # pragma: no cover

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _nothing)
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        r = c.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "hi"})
        err = next(d for ev, d in _sse_events(r.text) if ev == "error")
        assert err["code"] == "empty_reply"
        assert err["detail"] == "The model returned an empty answer. Try again, or ask it another way."


# ─── Pictures with no vision model to read them ─────────────────────────────
@requires_db
def test_a_picture_without_the_vision_model_is_refused_up_front(uploads_dir, stub_stream, tags, monkeypatch):
    """The owner's case: qwen2.5vl:7b set, not installed. The answer comes
    at once, Ollama is never asked to chat, and the turn stays in the thread."""
    monkeypatch.setattr(chat_api.core_settings, "ollama_vision_model", "qwen2.5vl:7b")
    tags.models = ["llama3.2:3b", "qwen2.5:7b", "qwen3:8b"]
    with _client() as c:
        token = _upload(c)
        t = c.post("/api/chat/threads", json={}).json()
        r = c.post(f"/api/chat/threads/{t['id']}/messages",
                   json={"content": "what is this?", "images": [{"token": token, "name": "cat.png"}]})
        assert r.status_code == 200
        events = _sse_events(r.text)
        assert stub_stream == []                      # no Ollama chat call
        assert [ev for ev, _ in events] == ["error", "done"]
        err = dict(events)["error"]
        assert err == {
            "code": "vision_model_missing", "model": "qwen2.5vl:7b",
            "detail": "Pictures need the vision model qwen2.5vl:7b, which isn't installed on this "
                      "Domovoi. An admin can install it in Settings → Models, or pick another "
                      "vision model there.",
        }
        done = dict(events)["done"]
        assert done["error"] == err["detail"] and done["content"] == ""
        assert done["model"] == "qwen2.5vl:7b"
        # No reply was tried, so no reply figures — only why.
        assert done["stats"] == {"model_role": "vision", "error_code": "vision_model_missing"}

        msgs = c.get(f"/api/chat/threads/{t['id']}/messages").json()["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["images"] == [{"token": token, "name": "cat.png"}]
        assert msgs[1]["stats"]["error_code"] == "vision_model_missing"

        # The models read says the same, for the clients' paperclip.
        info = c.get("/api/chat/models").json()
        assert info["vision_installed"] is False and info["vision_model"] == "qwen2.5vl:7b"


@requires_db
def test_no_vision_model_set_is_refused_without_asking_ollama(uploads_dir, stub_stream, tags, monkeypatch):
    monkeypatch.setattr(chat_api.core_settings, "ollama_vision_model", "")
    with _client() as c:
        token = _upload(c)
        t = c.post("/api/chat/threads", json={}).json()
        r = c.post(f"/api/chat/threads/{t['id']}/messages",
                   json={"content": "and this?", "images": [{"token": token, "name": "x.png"}]})
        err = next(d for ev, d in _sse_events(r.text) if ev == "error")
        assert err["code"] == "vision_model_missing"
        assert err["detail"].startswith("Pictures need a vision model, and none is set")
        assert stub_stream == [] and tags.asked == 0


@requires_db
def test_ollama_down_lets_the_picture_through_to_report_as_it_happens(uploads_dir, tags, monkeypatch):
    """Not knowing isn't a reason to refuse: the send goes ahead, and the
    failure it meets is the one the user reads."""
    tags.models = None

    async def _down(messages, model, **kw):
        raise httpx.ConnectError("All connection attempts failed")
        yield  # pragma: no cover

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _down)
    with _client() as c:
        token = _upload(c)
        t = c.post("/api/chat/threads", json={}).json()
        r = c.post(f"/api/chat/threads/{t['id']}/messages",
                   json={"content": "what is this?", "images": [{"token": token, "name": "cat.png"}]})
        err = next(d for ev, d in _sse_events(r.text) if ev == "error")
        assert err["code"] == "model_unreachable"
        assert tags.asked == 1
        assert c.get("/api/chat/models").json()["vision_installed"] is None


@requires_db
def test_a_text_turn_leaves_the_threads_pictures_out(uploads_dir, stub_stream, tags):
    """A text-only model handed a picture is refused by Ollama (400
    "Multimodal data provided, but model does not support multimodal
    requests." on 0.34.2), so one picture would break every text question
    after it. The vision turn still sees them."""
    tags.models = [chat_api.core_settings.ollama_vision_model, chat_api.core_settings.ollama_model]
    with _client() as c:
        token = _upload(c)
        t = c.post("/api/chat/threads", json={}).json()
        c.post(f"/api/chat/threads/{t['id']}/messages",
               json={"content": "what is this?", "images": [{"token": token, "name": "cat.png"}]})
        assert "images" in stub_stream[-1]["messages"][-1]
        c.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "tell me more"})
        text_turn = stub_stream[-1]
        assert text_turn["model"] == chat_api.core_settings.ollama_model
        # The system message first (test_chat_persona.py), then the thread.
        assert text_turn["messages"][0]["role"] == "system"
        assert [m["content"] for m in text_turn["messages"][1:]] == [
            "what is this?", "Hello there", "tell me more"]
        assert all("images" not in m for m in text_turn["messages"])
        # Another picture: the vision model sees both.
        token2 = _upload(c, "dog.png")
        c.post(f"/api/chat/threads/{t['id']}/messages",
               json={"content": "and this?", "images": [{"token": token2, "name": "dog.png"}]})
        assert sum(1 for m in stub_stream[-1]["messages"] if "images" in m) == 2


@requires_db
def test_an_older_rows_raw_error_is_read_out_plainly(monkeypatch):
    """Rows written before this fix keep the exception's text; the read
    turns it into the plain answer (the row itself is untouched)."""
    from web.backend.db import session_scope
    from sqlalchemy import text as sql

    monkeypatch.setattr(chat_api.core_settings, "ollama_vision_model", "qwen2.5vl:7b")
    raw = ("Client error '404 Not Found' for url 'http://localhost:11434/api/chat'\n"
           "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404")
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()

        async def _seed():
            async with session_scope() as s:
                await s.execute(sql(
                    "INSERT INTO chat_messages (thread_id, role, content, model, error, stats) "
                    "VALUES (:t, 'assistant', '', 'qwen2.5vl:7b', :e, CAST(:st AS JSONB))"),
                    {"t": t["id"], "e": raw, "st": json.dumps({"model_role": "vision", "wall_ms": 40.0})})
                await s.commit()

        c.portal.call(_seed)
        msg = c.get(f"/api/chat/threads/{t['id']}/messages").json()["messages"][-1]
        assert msg["error"] == chat_api.vision_missing_text("qwen2.5vl:7b")
        assert msg["stats"] == {"model_role": "vision", "wall_ms": 40.0, "error_code": "vision_model_missing"}


# ─── Uploads + vision switch ────────────────────────────────────────────────
@requires_db
def test_upload_and_vision_model_switch(uploads_dir, stub_stream, tags):
    tags.models = [chat_api.core_settings.ollama_vision_model]
    with _client() as c:
        up = c.post("/api/chat/uploads",
                    files={"file": ("cat.png", io.BytesIO(TINY_PNG), "image/png")})
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
def test_a_picture_reaches_the_model_shrunk_without_thinking(uploads_dir, stub_stream, tags):
    """Owner decision 2026-10-12: the model gets each picture at most 1280
    px on its long edge, from a copy beside the upload, and a call carrying
    pictures sends think=false and the vision context window. A text turn
    after it sends neither; deleting the thread removes the copy too."""
    from PIL import Image

    tags.models = [chat_api.core_settings.ollama_vision_model]
    photo = io.BytesIO()
    Image.new("RGB", (4032, 3024), (120, 90, 60)).save(photo, "JPEG", quality=85)
    with _client() as c:
        up = c.post("/api/chat/uploads", files={"file": ("kitchen.jpg", io.BytesIO(photo.getvalue()), "image/jpeg")})
        token = up.json()["token"]
        t = c.post("/api/chat/threads", json={}).json()
        r = c.post(f"/api/chat/threads/{t['id']}/messages",
                   json={"content": "describe this room", "images": [{"token": token, "name": "kitchen.jpg"}]})
        assert r.status_code == 200
        sent = stub_stream[-1]
        (b64,) = sent["messages"][-1]["images"]
        got = Image.open(io.BytesIO(base64.b64decode(b64)))
        assert got.size == (1280, 960) and got.format == "JPEG"
        assert (uploads_dir / f"{token}-1280.jpg").is_file()
        # The user's own picture is what the thread shows, untouched.
        served = c.get(f"/api/chat/uploads/{token}")
        assert Image.open(io.BytesIO(served.content)).size == (4032, 3024)
        vision_ctx = chat_api.core_settings.ollama_vision_num_ctx
        assert sent["kw"]["think"] is False and sent["kw"]["num_ctx"] == vision_ctx
        done = next(d for ev, d in _sse_events(r.text) if ev == "done")
        expected_ctx = vision_ctx or chat_api.core_settings.ollama_num_ctx
        assert done["stats"].get("num_ctx") == (expected_ctx or None)

        c.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "and the light?"})
        assert "think" not in stub_stream[-1]["kw"] and "num_ctx" not in stub_stream[-1]["kw"]

        c.delete(f"/api/chat/threads/{t['id']}")
        assert list(uploads_dir.iterdir()) == []


@requires_db
def test_chat_models_endpoint(tags, monkeypatch):
    monkeypatch.setattr(chat_api.core_settings, "ollama_vision_model", "qwen2.5vl:7b")
    tags.models = ["qwen2.5vl:7b", "llama3.2:3b"]
    with _client() as c:
        r = c.get("/api/chat/models").json()
        assert r["installed"] == ["llama3.2:3b", "qwen2.5vl:7b"]
        assert r["default_model"] == chat_api.core_settings.ollama_model
        assert r["vision_model"] == "qwen2.5vl:7b"
        assert r["vision_installed"] is True

        # The owner's box: three models, none of them the vision one.
        tags.models = ["llama3.2:3b", "qwen2.5:7b", "qwen3:8b"]
        assert c.get("/api/chat/models").json()["vision_installed"] is False
        # Ollama down: not known, and nothing listed.
        tags.models = None
        r = c.get("/api/chat/models").json()
        assert r["vision_installed"] is None and r["installed"] == []
