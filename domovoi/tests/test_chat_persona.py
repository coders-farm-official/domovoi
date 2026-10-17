"""Who answers in the typed chat — web/backend/api/chat.py.

The owner's report (2026-10-17, the app's chat on 1.0.0-rc2): a picture of
Domovoi's own promo image with "hey domo!" got, from the vision model
(qwen3-vl 2B), "I see you're excited to meet Domovoi, the open-source voice
assistant I'm helping with! I'm not a voice assistant...". The chat's calls
sent no system message at all, so every model answered as itself. Now each
call opens with ``ollama.chat_system_prompt``: the model is Domovoi, this
household's assistant; a call carrying pictures adds how to read them,
keeps the model's own Modelfile SYSTEM, and its history is cut to the vision
context window (``ollama_vision_num_ctx``).

Ollama is stubbed at the HTTP boundary (httpx.MockTransport), so what is
checked is the request body that would reach it.
"""

from __future__ import annotations

import io
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import web.backend.api.chat as chat_api
from domovoi.clients import ollama
from domovoi.tests.conftest import requires_db
from web.backend.main import app

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _png(color=(200, 30, 30), size=(64, 48)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _client() -> TestClient:
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


class _FakeOllama:
    """Answers /api/tags, /api/show and /api/chat; keeps each chat body."""

    def __init__(self) -> None:
        self.chats: list[dict[str, Any]] = []
        self.model_system: str | None = None
        self.reply = "Hi, I'm Domovoi."
        self.done_reason = "stop"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": chat_api.core_settings.ollama_vision_model},
                {"name": chat_api.core_settings.ollama_model},
            ]})
        if path == "/api/show":
            body = {"template": "{{ .Prompt }}"}
            if self.model_system is not None:
                body["system"] = self.model_system
            return httpx.Response(200, json=body)
        if path == "/api/chat":
            self.chats.append(json.loads(request.content))
            lines = [
                json.dumps({"message": {"content": self.reply}, "done": False}),
                json.dumps({"message": {"content": ""}, "done": True, "eval_count": 5,
                            "done_reason": self.done_reason}),
            ]
            return httpx.Response(200, content="\n".join(lines).encode())
        return httpx.Response(404, json={"error": "not here"})

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda *a, **k: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(self.handler), timeout=None),
        )


@pytest.fixture
def uploads_dir(tmp_path, monkeypatch):
    d = tmp_path / "chat_uploads"
    monkeypatch.setattr(chat_api, "UPLOADS_DIR", d)
    return d


def _upload(c, data: bytes, name="promo.png") -> str:
    up = c.post("/api/chat/uploads", files={"file": (name, io.BytesIO(data), "image/png")})
    assert up.status_code == 200, up.text
    return up.json()["token"]


def _send(c, thread_id: int, content: str, tokens: list[str] = ()) -> httpx.Response:
    r = c.post(f"/api/chat/threads/{thread_id}/messages", json={
        "content": content, "images": [{"token": t, "name": "p.png"} for t in tokens],
    })
    assert r.status_code == 200, r.text
    assert "event: error" not in r.text, r.text
    return r


# ─── the prompt itself ──────────────────────────────────────────────────────


def test_the_chat_prompt_says_who_is_answering():
    p = ollama.chat_system_prompt()
    assert p.startswith("You are Domovoi, this household's own assistant.")
    assert "Domo" in p and "answer as Domovoi" in p
    assert "pictures" not in p.lower()
    # Written chat: none of the voice prompt's "no markdown" rule.
    assert "markdown" not in p.lower()


def test_a_call_with_pictures_is_told_how_to_read_them_and_that_the_logo_is_itself():
    p = ollama.chat_system_prompt(pictures=True)
    assert p.startswith(ollama.chat_system_prompt())
    assert "describe it" in p and "read out any text" in p
    assert "shows the name Domovoi or its cat logo is a picture of you" in p


def test_the_models_own_system_and_the_profile_come_after_the_fixed_part():
    p = ollama.chat_system_prompt("User context: Name: Ana", pictures=True,
                                  model_system="Answer in French.")
    fixed = ollama.chat_system_prompt(pictures=True)
    assert p == fixed + "\n\nAnswer in French.\n\nUser context: Name: Ana"


def test_another_bot_name_has_no_domovoi_nickname_or_logo(monkeypatch):
    monkeypatch.setattr(ollama.settings, "bot_name", "Juniper")
    p = ollama.chat_system_prompt(pictures=True)
    assert "You are Juniper" in p and "Domo" not in p and "logo" not in p
    assert "shows the name Juniper is a picture of you" in p


async def test_model_system_or_none_reads_the_modelfile_system(monkeypatch):
    fake = _FakeOllama()
    fake.install(monkeypatch)
    assert await ollama.model_system_or_none("m") is None
    fake.model_system = "Describe images in detail."
    assert await ollama.model_system_or_none("m") == "Describe images in detail."
    fake.model_system = "   "
    assert await ollama.model_system_or_none("m") is None
    assert await ollama.model_system_or_none("") is None


async def test_model_system_or_none_is_none_when_ollama_is_down():
    # The suite pins OLLAMA_URL to a dead port (conftest).
    assert await ollama.model_system_or_none("m", timeout=1.0) is None


# ─── fitting the thread into the vision window ──────────────────────────────

_PIC = [{"token": "t"}]


def test_no_budget_sends_everything_oldest_first():
    rows = [("user", "c", _PIC), ("assistant", "b", None), ("user", "a", _PIC)]
    assert chat_api._fit_history(rows, None) == [
        ("user", "a", _PIC), ("assistant", "b", []), ("user", "c", _PIC)]


def test_older_pictures_go_first_then_older_words():
    t = chat_api._text_tokens
    pic = chat_api._PICTURE_TOKENS
    newest = ("user", "hey domo!", _PIC * 2)
    reply = ("assistant", "x" * 300, None)
    older = ("user", "and this one?", _PIC)
    oldest = ("user", "y" * 6000, None)  # ~2k tokens: never fits
    rows = [newest, reply, older, oldest]
    words = t(newest[1]) + t(reply[1]) + t(older[1])
    # Room for the words and the newest pictures, not the older picture.
    fitted = chat_api._fit_history(rows, 2 * pic + words + pic - 1)
    assert fitted == [("user", "and this one?", []), ("assistant", "x" * 300, []),
                      ("user", "hey domo!", _PIC * 2)]
    # Room for that picture too.
    fitted = chat_api._fit_history(rows, 3 * pic + words)
    assert fitted[0] == ("user", "and this one?", _PIC)
    # The newest message always goes, even past the budget.
    assert chat_api._fit_history(rows, 10) == [("user", "hey domo!", _PIC * 2)]


# ─── on the wire ────────────────────────────────────────────────────────────


@requires_db
def test_a_picture_turn_carries_the_persona_and_the_threads_history(uploads_dir, monkeypatch):
    """The owner's case: a thread that already has a typed exchange, then
    Domovoi's promo picture with "hey domo!". The vision request opens with
    the system message (Domovoi, how to read the picture), then the earlier
    turns, then the picture turn; think off and the vision window, as before."""
    fake = _FakeOllama()
    with _client() as c:
        fake.install(monkeypatch)
        t = c.post("/api/chat/threads", json={}).json()
        _send(c, t["id"], "what can you do?")
        text_call = fake.chats[-1]
        assert text_call["model"] == chat_api.core_settings.ollama_model
        assert text_call["messages"][0] == {"role": "system", "content": ollama.chat_system_prompt()}

        token = _upload(c, _png())
        _send(c, t["id"], "hey domo!", [token])
        call = fake.chats[-1]
        assert call["model"] == chat_api.core_settings.ollama_vision_model
        system, *history = call["messages"]
        assert system == {"role": "system", "content": ollama.chat_system_prompt(pictures=True)}
        assert "You are Domovoi" in system["content"] and "cat logo" in system["content"]
        assert [(m["role"], m["content"]) for m in history] == [
            ("user", "what can you do?"),
            ("assistant", "Hi, I'm Domovoi."),
            ("user", "hey domo!"),
        ]
        assert len(history[-1]["images"]) == 1
        assert call["think"] is False
        assert call["options"]["num_ctx"] == chat_api.core_settings.ollama_vision_num_ctx
        # The stats count the thread's turns sent, not the system message.
        msgs = c.get(f"/api/chat/threads/{t['id']}/messages").json()["messages"]
        assert msgs[-1]["stats"]["context_sent"] == 3


@requires_db
def test_the_vision_models_own_system_is_kept(uploads_dir, monkeypatch):
    fake = _FakeOllama()
    fake.model_system = "When shown an image, describe it in detail."
    with _client() as c:
        fake.install(monkeypatch)
        t = c.post("/api/chat/threads", json={}).json()
        _send(c, t["id"], "what is this?", [_upload(c, _png())])
        system = fake.chats[-1]["messages"][0]["content"]
        assert system == ollama.chat_system_prompt(
            pictures=True, model_system="When shown an image, describe it in detail.")


@requires_db
def test_a_threads_person_gets_their_profile(uploads_dir, monkeypatch):
    from sqlalchemy import text as sql

    from web.backend.db import session_scope

    fake = _FakeOllama()
    with _client() as c:
        fake.install(monkeypatch)
        t = c.post("/api/chat/threads", json={}).json()

        async def _owner():
            async with session_scope() as s:
                pid = (await s.execute(sql(
                    "INSERT INTO people (name) VALUES ('Persona Test Ana') RETURNING id"))).scalar_one()
                await s.execute(sql(
                    "INSERT INTO memories (person_id, body, source) VALUES (:p, 'likes jazz', 'manual')"),
                    {"p": pid})
                await s.execute(sql("UPDATE chat_threads SET person_id = :p WHERE id = :t"),
                                {"p": pid, "t": t["id"]})
                await s.commit()
                return pid

        pid = c.portal.call(_owner)
        try:
            _send(c, t["id"], "what is this?", [_upload(c, _png())])
            system = fake.chats[-1]["messages"][0]["content"]
            assert system.startswith(ollama.chat_system_prompt(pictures=True))
            assert "Name: Persona Test Ana" in system and "likes jazz" in system
        finally:
            async def _forget():
                async with session_scope() as s:
                    await s.execute(sql("DELETE FROM people WHERE id = :p"), {"p": pid})
                    await s.commit()
            c.portal.call(_forget)


@requires_db
def test_a_long_picture_thread_is_cut_to_the_vision_window(uploads_dir, monkeypatch):
    """Three earlier pictures and the new one don't all fit a small window:
    the newest picture always goes, older pictures are left out (their
    words stay), and the request stays under the window less the reply's
    room."""
    fake = _FakeOllama()
    pic = chat_api._PICTURE_TOKENS
    window = 3 * pic + chat_api._reply_room() + 400
    monkeypatch.setattr(chat_api.core_settings, "ollama_vision_num_ctx", window)
    with _client() as c:
        fake.install(monkeypatch)
        t = c.post("/api/chat/threads", json={}).json()
        for i in range(3):
            _send(c, t["id"], f"picture {i}", [_upload(c, _png((i * 60, 10, 10)))])
        _send(c, t["id"], "hey domo!", [_upload(c, _png((9, 9, 200)))])
        system, *history = fake.chats[-1]["messages"]
        assert history[-1]["content"] == "hey domo!" and len(history[-1]["images"]) == 1
        with_pics = [m["content"] for m in history if m.get("images")]
        assert with_pics[-1] == "hey domo!" and len(with_pics) < 4
        # Every turn's words still went (they are short).
        assert [m["content"] for m in history if m["role"] == "user"] == [
            "picture 0", "picture 1", "picture 2", "hey domo!"]
        estimate = chat_api._text_tokens(system["content"]) + sum(
            chat_api._text_tokens(m["content"]) + pic * len(m.get("images", [])) for m in history)
        assert estimate <= window - chat_api._reply_room()
        assert fake.chats[-1]["options"]["num_ctx"] == window


# ─── the reply's length cap (ollama_chat_num_predict) ───────────────────────
# One live reply on 2026-10-17 (qwen3-vl 2B) fell into "I am here. I am at
# work." until the 8192-token window was full. Every chat call now asks for
# at most ollama_chat_num_predict tokens, and a reply stopped by it ends
# with "…" and a note.


def test_the_cap_is_a_setting_with_a_default(monkeypatch):
    from domovoi.config import Settings
    from domovoi.config_schema import FIELD_BY_NAME, coerce_and_validate

    assert Settings.model_fields["ollama_chat_num_predict"].default == 2048
    spec = FIELD_BY_NAME["ollama_chat_num_predict"]
    assert spec.type == "int" and spec.group == "Models"
    assert coerce_and_validate(spec, 0) == 0
    with pytest.raises(ValueError):
        coerce_and_validate(spec, -1)
    # A blank env value means "no cap", like the context-window settings.
    monkeypatch.setenv("OLLAMA_CHAT_NUM_PREDICT", "")
    assert Settings().ollama_chat_num_predict == 0
    monkeypatch.setenv("OLLAMA_CHAT_NUM_PREDICT", "1024")
    assert Settings().ollama_chat_num_predict == 1024


async def test_chat_stream_sends_num_predict_only_when_set(monkeypatch):
    fake = _FakeOllama()
    fake.install(monkeypatch)
    msgs = [{"role": "user", "content": "hi"}]
    [d async for d in ollama.chat_stream(msgs, model="m", num_ctx=8192, num_predict=2048)]
    assert fake.chats[-1]["options"] == {"num_ctx": 8192, "num_predict": 2048}
    for unset in (None, 0):
        [d async for d in ollama.chat_stream(msgs, model="m", num_ctx=8192, num_predict=unset)]
        assert fake.chats[-1]["options"] == {"num_ctx": 8192}


@requires_db
@pytest.mark.parametrize("pictures", [False, True])
def test_every_chat_call_is_capped(uploads_dir, monkeypatch, pictures):
    fake = _FakeOllama()
    with _client() as c:
        fake.install(monkeypatch)
        t = c.post("/api/chat/threads", json={}).json()
        _send(c, t["id"], "hello", [_upload(c, _png())] if pictures else [])
        assert fake.chats[-1]["options"]["num_predict"] == 2048
        monkeypatch.setattr(chat_api.core_settings, "ollama_chat_num_predict", 0)
        _send(c, t["id"], "again", [_upload(c, _png())] if pictures else [])
        assert "num_predict" not in fake.chats[-1].get("options", {})


@requires_db
@pytest.mark.parametrize("pictures", [False, True])
def test_a_reply_stopped_by_the_cap_ends_cleanly(uploads_dir, monkeypatch, pictures):
    """The stream and the kept row both end with "…" and the note; no error
    event, and the stats say it stopped at the length limit."""
    fake = _FakeOllama()
    fake.reply = "I am here. I am at work. I am here. I am at work."
    fake.done_reason = "length"
    with _client() as c:
        fake.install(monkeypatch)
        t = c.post("/api/chat/threads", json={}).json()
        r = _send(c, t["id"], "hey domo!", [_upload(c, _png())] if pictures else [])
        events = [(e, d) for e, d in _sse(r.text)]
        deltas = "".join(d["text"] for e, d in events if e == "delta")
        assert deltas == fake.reply + "…" + chat_api.CUT_OFF_NOTE
        done = next(d for e, d in events if e == "done")
        assert done["content"] == deltas and done["error"] is None
        assert done["stats"]["done_reason"] == "length"
        assert "error_code" not in done["stats"]
        row = c.get(f"/api/chat/threads/{t['id']}/messages").json()["messages"][-1]
        assert row["content"].endswith("length limit.*")
        # The next turn hands the model its earlier reply, note and all,
        # as the thread shows it.
        fake.done_reason = "stop"
        _send(c, t["id"], "are you ok?")
        assert fake.chats[-1]["messages"][-2]["content"] == deltas


@requires_db
def test_a_reply_that_finishes_gets_no_note(uploads_dir, monkeypatch):
    fake = _FakeOllama()
    with _client() as c:
        fake.install(monkeypatch)
        t = c.post("/api/chat/threads", json={}).json()
        r = _send(c, t["id"], "hey domo!")
        done = next(d for e, d in _sse(r.text) if e == "done")
        assert done["content"] == fake.reply


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
