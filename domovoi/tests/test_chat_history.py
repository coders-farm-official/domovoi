"""Chat history and titles (assistant plan part 7, V034): the server's half.

- titles: the first five words at once (typed and spoken), the model's in
  the background after a reply, never over a name a person set, stopped
  by the next message and written after it;
- ``GET /api/chat/threads?q=``: titles and messages, literally, archived
  ones included, with the words around the match;
- archive and rename through ``PATCH``;
- ``POST .../retry``: a new reply in place of the last one;
- ``stats.window_from_id``: where a reply's view of a long thread began.

Ollama is stubbed at ``chat_stream`` (as in test_chat_api.py) and the
model's title at ``chat_api._model_title``; nothing here reaches a model.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

import web.backend.api.chat as chat_api
from domovoi import chat_titles
from domovoi.tests.conftest import requires_db
from web.backend.main import app


def _client():
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


def _sse(body: str) -> list[tuple[str, Any]]:
    events = []
    for frame in body.split("\n\n"):
        ev, data = "message", ""
        for line in frame.split("\n"):
            if line.startswith("event: "):
                ev = line[7:].strip()
            elif line.startswith("data: "):
                data += line[6:]
        if data:
            events.append((ev, json.loads(data)))
    return events


@pytest.fixture
def replies(monkeypatch):
    """The model answers with ``replies.next`` (a list of deltas, or an
    exception to raise); each call's messages are kept in ``replies.calls``."""
    class _R:
        next: Any = ["Hello", " there"]
        calls: list[list[dict[str, Any]]] = []

    async def _stream(messages, model, **kw):
        _R.calls.append(messages)
        if isinstance(_R.next, Exception):
            raise _R.next
        for d in _R.next:
            yield d

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    return _R


@pytest.fixture
def titles(monkeypatch):
    """The title job on, at once, with the model's title from ``titles.say``
    (a string, or an asyncio.Event the job waits on first)."""
    class _T:
        say: Any = "Sky Colour Question"
        asked: list[tuple[str, str]] = []
        gate: asyncio.Event | None = None
        cancelled = 0

    async def _model_title(question, answer):
        _T.asked.append((question, answer))
        if _T.gate is not None:
            try:
                await _T.gate.wait()
            except asyncio.CancelledError:
                _T.cancelled += 1
                raise
        return _T.say

    monkeypatch.setattr(chat_api, "AUTO_TITLES", True)
    monkeypatch.setattr(chat_api, "TITLE_DELAY_SEC", 0)
    monkeypatch.setattr(chat_api, "_model_title", _model_title)
    monkeypatch.setattr(chat_api, "_title_failures", {})
    return _T


def _thread(c, tid: int) -> dict[str, Any]:
    rows = c.get("/api/chat/threads", params={"q": ""}).json()["threads"]
    rows += c.get("/api/chat/threads", params={"archived": True}).json()["threads"]
    return next(r for r in rows if r["id"] == tid)


def _wait(what: str, ok, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if ok():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _send(c, tid: int, content: str) -> list[tuple[str, Any]]:
    r = c.post(f"/api/chat/threads/{tid}/messages", json={"content": content})
    assert r.status_code == 200, r.text
    return _sse(r.text)


# ─── The title rules (no database) ──────────────────────────────────────────
def test_first_words_title() -> None:
    assert chat_titles.first_words_title("hi there, domovoi") == "hi there, domovoi"
    assert chat_titles.first_words_title("  Why is the\nsky blue at noon today?") == "Why is the sky blue…"
    assert chat_titles.first_words_title("") is None
    assert chat_titles.first_words_title(None) is None
    long = "x" * 100
    assert chat_titles.first_words_title(long) == "x" * 60 + "…"


@pytest.mark.parametrize(
    ("raw", "title"),
    [
        ("Sky Colour Question", "Sky Colour Question"),
        ('"Why The Sky Is Blue."', "Why The Sky Is Blue"),
        ("Title: **Pickle's toy advice**\n\nSure!", "Pickle's toy advice"),
        ("A title that runs on far past five words", "A title that runs on"),
        ("\n\n  Dinner ideas  \n", "Dinner ideas"),
        ("", None),
        ("  \n ", None),
        ('""', None),
        (None, None),
    ],
)
def test_the_models_title_is_cleaned(raw, title) -> None:
    assert chat_titles.clean_model_title(raw) == title


def test_the_title_prompt_is_the_first_exchange_cut_short() -> None:
    p = chat_titles.title_prompt("q" * 2000, "a" * 2000)
    assert p.startswith("Chat:\nPerson: " + "q" * 800 + "\nDomovoi: " + "a" * 800)
    assert p.endswith("\n\nTitle:")


def test_a_search_matches_literally_and_shows_its_words() -> None:
    assert chat_api._like_pattern("50%_off\\") == "%50\\%\\_off\\\\%"
    words = "one two three four five six seven eight nine ten " * 3
    snippet = chat_api.match_snippet(words + "lentil soup " + words, "LENTIL")
    assert snippet.startswith("…") and snippet.endswith("…") and "lentil soup" in snippet
    assert chat_api.match_snippet("short note", "note") == "short note"
    assert chat_api.match_snippet("nothing here", "lentil") is None


# ─── Titles on the server ───────────────────────────────────────────────────
@requires_db
def test_a_thread_is_titled_by_its_first_five_words(replies) -> None:
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        assert t["title"] is None and t["title_by_user"] is False
        _send(c, t["id"], "Why is the sky blue at noon?")
        _send(c, t["id"], "And at sunset?")
        row = _thread(c, t["id"])
        assert row["title"] == "Why is the sky blue…"
        assert row["title_by_user"] is False
        c.delete(f"/api/chat/threads/{t['id']}")


@requires_db
def test_no_model_title_in_a_stub_run(replies, monkeypatch) -> None:
    """USE_STUBS (every test run): the job doesn't start, so no test asks
    a model by accident."""
    asked = []

    async def _model_title(q, a):
        asked.append(q)
        return "Never"

    monkeypatch.setattr(chat_api, "_model_title", _model_title)
    monkeypatch.setattr(chat_api, "TITLE_DELAY_SEC", 0)
    assert chat_api.AUTO_TITLES is None and chat_api.core_settings.use_stubs
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        _send(c, t["id"], "hello there")
        time.sleep(0.3)
        assert _thread(c, t["id"])["title"] == "hello there"
        c.delete(f"/api/chat/threads/{t['id']}")
    assert asked == []


@requires_db
def test_the_model_titles_a_thread_after_its_first_reply(replies, titles) -> None:
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        events = _send(c, t["id"], "why is the sky blue")
        assert [e for e, _ in events][-1] == "done"
        _wait("the model's title", lambda: _thread(c, t["id"])["title"] == "Sky Colour Question")
        assert titles.asked == [("why is the sky blue", "Hello there")]
        # Written once: the next reply doesn't ask again.
        titles.say = "Something Else"
        _send(c, t["id"], "and at night?")
        time.sleep(0.3)
        assert _thread(c, t["id"])["title"] == "Sky Colour Question"
        assert len(titles.asked) == 1
        c.delete(f"/api/chat/threads/{t['id']}")


@requires_db
def test_a_name_a_person_set_is_never_written_over(replies, titles) -> None:
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        assert c.patch(f"/api/chat/threads/{t['id']}", json={"title": "  Our trip  "}).status_code == 200
        row = _thread(c, t["id"])
        assert row["title"] == "Our trip" and row["title_by_user"] is True
        _send(c, t["id"], "where should we go")
        time.sleep(0.3)
        assert _thread(c, t["id"])["title"] == "Our trip"
        assert titles.asked == []
        # A blank name is refused, and changes nothing.
        assert c.patch(f"/api/chat/threads/{t['id']}", json={"title": "   "}).status_code == 400
        assert _thread(c, t["id"])["title"] == "Our trip"
        c.delete(f"/api/chat/threads/{t['id']}")


@requires_db
def test_a_rename_while_the_title_is_written_wins(replies, titles) -> None:
    titles.gate = asyncio.Event()
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        _send(c, t["id"], "plan a dinner")
        _wait("the title job asked", lambda: len(titles.asked) == 1)
        assert c.patch(f"/api/chat/threads/{t['id']}", json={"title": "Dinner"}).status_code == 200
        _wait("the job stopped", lambda: titles.cancelled == 1)
        assert _thread(c, t["id"])["title"] == "Dinner"
        c.delete(f"/api/chat/threads/{t['id']}")


@requires_db
def test_the_next_message_stops_the_title_and_it_is_written_after(replies, titles) -> None:
    titles.gate = asyncio.Event()
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        _send(c, t["id"], "plan a dinner")
        _wait("the title job asked", lambda: len(titles.asked) == 1)
        # The next message: the job stops before the reply asks the model.
        _send(c, t["id"], "for six people")
        assert titles.cancelled == 1
        assert _thread(c, t["id"])["title"] == "plan a dinner"
        # After that reply it runs again, and this time it is let through.
        _wait("asked again", lambda: len(titles.asked) == 2)
        c.portal.call(titles.gate.set)
        _wait("the model's title", lambda: _thread(c, t["id"])["title"] == "Sky Colour Question")
        c.delete(f"/api/chat/threads/{t['id']}")


@requires_db
def test_a_failed_reply_brings_no_title_and_a_bad_title_keeps_the_words(replies, titles) -> None:
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        replies.next = RuntimeError("model gone")
        _send(c, t["id"], "first try")
        time.sleep(0.3)
        assert titles.asked == []
        replies.next = ["Fine"]
        titles.say = '""'
        _send(c, t["id"], "second try")
        _wait("asked", lambda: len(titles.asked) == 1)
        time.sleep(0.2)
        assert _thread(c, t["id"])["title"] == "first try"
        # The first question and the first good answer after it.
        assert titles.asked == [("first try", "Fine")]
        c.delete(f"/api/chat/threads/{t['id']}")


@requires_db
def test_no_title_job_with_the_households_ai_off(replies, titles, monkeypatch) -> None:
    monkeypatch.setattr(chat_api, "_ai_turned_off", lambda: True)
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        _send(c, t["id"], "hello there")
        time.sleep(0.3)
        assert titles.asked == []
        assert _thread(c, t["id"])["title"] == "hello there"
        c.delete(f"/api/chat/threads/{t['id']}")


# ─── Search and archive ─────────────────────────────────────────────────────
@requires_db
def test_search_finds_titles_and_messages_and_archived_threads(replies) -> None:
    with _client() as c:
        a = c.post("/api/chat/threads", json={}).json()["id"]
        b = c.post("/api/chat/threads", json={}).json()["id"]
        other = c.post("/api/chat/threads", json={}).json()["id"]
        _send(c, a, "Lentil soup for a cold evening")
        _send(c, b, "what should I cook tonight")
        replies.next = ["Try a red lentil dal with rice."]
        _send(c, b, "something warm")
        replies.next = ["Hello", " there"]
        _send(c, other, "100% sure about 50_50 odds?")
        assert c.patch(f"/api/chat/threads/{b}", json={"archived": True}).status_code == 200

        found = c.get("/api/chat/threads", params={"q": "LENTIL"}).json()["threads"]
        ids = [r["id"] for r in found]
        assert set(ids) >= {a, b} and other not in ids
        hit_b = next(r for r in found if r["id"] == b)
        assert hit_b["archived"] is True and "lentil dal" in hit_b["match_snippet"]
        assert next(r for r in found if r["id"] == a)["match_snippet"].startswith("Lentil soup")

        # Literal: % and _ are characters, not wildcards.
        assert [r["id"] for r in c.get("/api/chat/threads", params={"q": "0%"}).json()["threads"]] == [other]
        assert c.get("/api/chat/threads", params={"q": "5_5"}).json()["threads"] == []
        # A search can be held to one list.
        live = c.get("/api/chat/threads", params={"q": "lentil", "archived": False}).json()["threads"]
        assert b not in [r["id"] for r in live]

        # Without a search: the archived thread is only in the archived list.
        assert b not in [r["id"] for r in c.get("/api/chat/threads").json()["threads"]]
        assert b in [r["id"] for r in c.get("/api/chat/threads", params={"archived": True}).json()["threads"]]
        assert c.patch(f"/api/chat/threads/{b}", json={"archived": False}).status_code == 200
        assert b in [r["id"] for r in c.get("/api/chat/threads").json()["threads"]]
        assert c.get("/api/chat/threads", params={"q": "x" * 201}).status_code == 422
        for tid in (a, b, other):
            c.delete(f"/api/chat/threads/{tid}")


# ─── Try again ──────────────────────────────────────────────────────────────
@requires_db
def test_try_again_replaces_the_last_reply(replies) -> None:
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()["id"]
        _send(c, t, "tell me a joke")
        old = c.get(f"/api/chat/threads/{t}/messages").json()["messages"][-1]
        replies.next = ["A better", " joke"]
        r = c.post(f"/api/chat/threads/{t}/retry")
        assert r.status_code == 200
        events = _sse(r.text)
        assert [d["text"] for e, d in events if e == "delta"] == ["A better", " joke"]
        done = next(d for e, d in events if e == "done")
        assert done["replaces"] == old["id"] and done["content"] == "A better joke"
        msgs = c.get(f"/api/chat/threads/{t}/messages").json()["messages"]
        assert [(m["role"], m["content"]) for m in msgs] == [("user", "tell me a joke"), ("assistant", "A better joke")]
        # The model was asked the question again, without the old reply.
        assert replies.calls[-1][1:] == [{"role": "user", "content": "tell me a joke"}]

        # A retry that fails keeps the reply it was to replace.
        replies.next = RuntimeError("model gone")
        events = _sse(c.post(f"/api/chat/threads/{t}/retry").text)
        done = next(d for e, d in events if e == "done")
        assert "replaces" not in done and done["error"]
        msgs = c.get(f"/api/chat/threads/{t}/messages").json()["messages"]
        assert [m["content"] for m in msgs][:2] == ["tell me a joke", "A better joke"]
        c.delete(f"/api/chat/threads/{t}")


@requires_db
def test_try_again_needs_a_reply_to_replace(replies) -> None:
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()["id"]
        r = c.post(f"/api/chat/threads/{t}/retry")
        assert r.status_code == 409 and r.json()["code"] == "nothing_to_retry"
        assert c.post("/api/chat/threads/999999999/retry").status_code == 404
        c.delete(f"/api/chat/threads/{t}")


# ─── Where the model's view began ───────────────────────────────────────────
@requires_db
def test_a_reply_says_where_its_view_of_a_long_thread_began(replies) -> None:
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()["id"]
        first = _send(c, t, "turn 0")
        assert "window_from_id" not in next(d for e, d in first if e == "done")["stats"]
        for i in range(1, 16):
            _send(c, t, f"turn {i}")
        msgs = c.get(f"/api/chat/threads/{t}/messages").json()["messages"]
        assert len(msgs) == 32
        stats = msgs[-1]["stats"]
        # 31 kept rows before the reply, the newest 30 sent: it began at the
        # second message.
        assert stats["context_sent"] == 30 and stats["context_in_thread"] == 31
        assert stats["window_from_id"] == msgs[1]["id"]
        c.delete(f"/api/chat/threads/{t}")
