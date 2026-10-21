"""Chat history, round-1 review fixes (wf/chat-history): try again can't
answer one question twice, only replaces the chat model's own typed
replies, and the title jobs run one at a time, stop for any reply and
leave the help desk's threads alone. In-process (ASGI) so two requests can
overlap; Ollama and the model's title are stubbed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import web.backend.api.chat as chat_api
from domovoi import chat_titles
from domovoi.tests.conftest import requires_db
from web.backend.db import session_scope
from web.backend.main import app


def _web() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                       base_url="http://test", headers={"X-Requested-With": "domovoi-tests"})


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


async def _rows(tid: int) -> list[tuple[str, str, Any]]:
    async with session_scope() as s:
        r = await s.execute(text("SELECT role, content, error FROM chat_messages WHERE thread_id = :t ORDER BY id"),
                            {"t": tid})
        return [tuple(x) for x in r.all()]


async def _title(tid: int) -> str | None:
    async with session_scope() as s:
        return (await s.execute(text("SELECT title FROM chat_threads WHERE id = :t"), {"t": tid})).scalar_one()


@pytest.fixture
def titles(monkeypatch):
    """The title queue on, at once, fresh; the model's title from `say`,
    each call waiting on its own gate when `gated`."""
    class _T:
        say = "Model Title"
        asked: list[str] = []
        gated = False
        gates: list[asyncio.Event] = []
        cancelled = 0
        inflight = 0
        most_at_once = 0

    async def _model_title(question, answer):
        _T.asked.append(question)
        _T.inflight += 1
        _T.most_at_once = max(_T.most_at_once, _T.inflight)
        try:
            if _T.gated:
                gate = asyncio.Event()
                _T.gates.append(gate)
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    _T.cancelled += 1
                    raise
            return _T.say
        finally:
            _T.inflight -= 1

    monkeypatch.setattr(chat_api, "AUTO_TITLES", True)
    monkeypatch.setattr(chat_api, "TITLE_DELAY_SEC", 0)
    monkeypatch.setattr(chat_api, "_model_title", _model_title)
    monkeypatch.setattr(chat_api, "_title_failures", {})
    monkeypatch.setattr(chat_api, "_title_queue", [])
    monkeypatch.setattr(chat_api, "_title_worker", None)
    monkeypatch.setattr(chat_api, "_title_running", None)
    monkeypatch.setattr(chat_api, "_replies_busy", 0)
    return _T


async def _until(what: str, ok, timeout: float = 20.0) -> None:
    for _ in range(int(timeout / 0.02)):
        if await ok() if asyncio.iscoroutinefunction(ok) else ok():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


# ─── Try again ──────────────────────────────────────────────────────────────
@requires_db
async def test_two_tries_at_once_give_one_answer(monkeypatch) -> None:
    gate = asyncio.Event()
    n = 0

    async def _stream(messages, model, **kw):
        nonlocal n
        n += 1
        mine = n
        if mine > 1:
            await gate.wait()
        yield f"answer {mine}"

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    async with _web() as c:
        t = (await c.post("/api/chat/threads", json={})).json()["id"]
        await c.post(f"/api/chat/threads/{t}/messages", json={"content": "tell me a joke"})
        first = asyncio.create_task(c.post(f"/api/chat/threads/{t}/retry"))
        await _until("the first retry streaming", lambda: n == 2)
        second = await c.post(f"/api/chat/threads/{t}/retry")
        assert second.status_code == 409 and second.json()["code"] == "already_retried"
        gate.set()
        done = next(d for e, d in _sse((await first).text) if e == "done")
        assert done["content"] == "answer 2" and "replaces" in done
        assert await _rows(t) == [("user", "tell me a joke", None), ("assistant", "answer 2", None)]
        await c.delete(f"/api/chat/threads/{t}")


@requires_db
async def test_a_reply_already_replaced_elsewhere_is_not_answered_twice(monkeypatch) -> None:
    """Another server process (or a stale tab) replaced the reply while
    this retry streamed: the old row is gone, so nothing is written."""
    n = 0

    async def _stream(messages, model, **kw):
        nonlocal n
        n += 1
        if n > 1:
            async with session_scope() as s:
                await s.execute(text("DELETE FROM chat_messages WHERE content = 'answer 1'"))
                await s.execute(text("INSERT INTO chat_messages (thread_id, role, content) "
                                     "SELECT thread_id, 'assistant', 'from elsewhere' FROM chat_messages "
                                     "WHERE content = 'tell me a joke'"))
                await s.commit()
        yield f"answer {n}"

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    async with _web() as c:
        t = (await c.post("/api/chat/threads", json={})).json()["id"]
        await c.post(f"/api/chat/threads/{t}/messages", json={"content": "tell me a joke"})
        events = _sse((await c.post(f"/api/chat/threads/{t}/retry")).text)
        assert ("error", "already_retried") in [(e, d.get("code")) for e, d in events]
        assert await _rows(t) == [("user", "tell me a joke", None), ("assistant", "from elsewhere", None)]
        await c.delete(f"/api/chat/threads/{t}")


@requires_db
@pytest.mark.parametrize(
    ("source", "stats"),
    [
        ("voice", {"model_role": "voice", "matched_handler": "timer"}),
        ("typed", {"model_role": "help"}),
        # The help desk's steps sent by voice (wf/help-desk fa81e6f4).
        ("voice", {"model_role": "help"}),
        ("typed", {"matched_handler": "timer"}),
    ],
)
async def test_a_spoken_action_or_help_reply_is_not_tried_again(monkeypatch, source, stats) -> None:
    asked: list[Any] = []

    async def _stream(messages, model, **kw):
        asked.append(messages)
        yield "Sure, I've set a timer."

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    async with _web() as c:
        t = (await c.post("/api/chat/threads", json={})).json()["id"]
        async with session_scope() as s:
            await s.execute(text("INSERT INTO chat_messages (thread_id, role, content, source) "
                                 "VALUES (:t, 'user', 'set a timer for ten minutes', :src)"), {"t": t, "src": source})
            await s.execute(text("INSERT INTO chat_messages (thread_id, role, content, source, stats) "
                                 "VALUES (:t, 'assistant', 'Timer set for 10 minutes.', :src, CAST(:st AS JSONB))"),
                            {"t": t, "src": source, "st": json.dumps(stats)})
            await s.commit()
        r = await c.post(f"/api/chat/threads/{t}/retry")
        assert r.status_code == 409 and r.json()["code"] == "not_retryable"
        assert asked == []
        msgs = (await c.get(f"/api/chat/threads/{t}/messages")).json()["messages"]
        assert [m["retryable"] for m in msgs] == [False, False]
        assert (await _rows(t))[-1][1] == "Timer set for 10 minutes."
        await c.delete(f"/api/chat/threads/{t}")


@requires_db
async def test_a_retry_dropped_before_its_first_byte_leaves_no_mark(monkeypatch) -> None:
    """The route answered, the client went away before the stream began:
    nothing marks the thread, and the next try again works."""
    async def _stream(messages, model, **kw):
        yield "an answer"

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    monkeypatch.setattr(chat_api, "_retrying", set())
    async with _web() as c:
        t = (await c.post("/api/chat/threads", json={})).json()["id"]
        await c.post(f"/api/chat/threads/{t}/messages", json={"content": "tell me a joke"})
        dropped = await chat_api.retry_reply(t)
        assert dropped.status_code == 200
        del dropped                      # never read: no stream ran
        assert t not in chat_api._retrying
        r = await c.post(f"/api/chat/threads/{t}/retry")
        assert r.status_code == 200
        assert next(d for e, d in _sse(r.text) if e == "done")["content"] == "an answer"
        assert t not in chat_api._retrying
        await c.delete(f"/api/chat/threads/{t}")


@requires_db
async def test_a_refused_request_leaves_the_title_job_running(monkeypatch, titles) -> None:
    async def _stream(messages, model, **kw):
        yield "ok"

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    titles.gated = True
    async with _web() as c:
        a = (await c.post("/api/chat/threads", json={})).json()["id"]
        empty = (await c.post("/api/chat/threads", json={})).json()["id"]
        await c.post(f"/api/chat/threads/{a}/messages", json={"content": "first thread"})
        await _until("a's title asked", lambda: titles.asked == ["first thread"])
        assert (await c.post(f"/api/chat/threads/{empty}/retry")).status_code == 409
        assert (await c.post("/api/chat/threads/999999999/retry")).status_code == 404
        assert (await c.post("/api/chat/threads/999999999/messages", json={"content": "x"})).status_code == 404
        await asyncio.sleep(0.2)
        assert titles.cancelled == 0
        for g in titles.gates:
            g.set()
        titles.gated = False
        await _until("titled", lambda: True)
        for t in (a, empty):
            await c.delete(f"/api/chat/threads/{t}")


def test_which_replies_can_be_tried_again() -> None:
    assert chat_api.reply_retryable("typed", {"model_role": "chat"})
    assert chat_api.reply_retryable("typed", json.dumps({"model_role": "vision"}))
    assert chat_api.reply_retryable("typed", None)          # before V017
    assert chat_api.reply_retryable(None, {})
    assert not chat_api.reply_retryable("voice", {"model_role": "voice"})
    assert not chat_api.reply_retryable("typed", {"model_role": "help"})
    assert not chat_api.reply_retryable("voice", {"model_role": "help"})


# ─── Title jobs ─────────────────────────────────────────────────────────────
async def _stub_replies(monkeypatch) -> None:
    async def _stream(messages, model, **kw):
        yield "ok"

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)


@requires_db
async def test_titles_run_one_at_a_time_and_any_message_stops_them(monkeypatch, titles) -> None:
    await _stub_replies(monkeypatch)
    titles.gated = True
    async with _web() as c:
        a = (await c.post("/api/chat/threads", json={})).json()["id"]
        b = (await c.post("/api/chat/threads", json={})).json()["id"]
        other = (await c.post("/api/chat/threads", json={})).json()["id"]
        await c.post(f"/api/chat/threads/{a}/messages", json={"content": "first thread"})
        await c.post(f"/api/chat/threads/{b}/messages", json={"content": "second thread"})
        # (a's title may already have started, and been stopped by b's
        # message: either way it is a's, and only one runs.)
        await _until("a's title asked", lambda: titles.inflight == 1)
        await asyncio.sleep(0.2)
        assert set(titles.asked) == {"first thread"} and titles.inflight == 1, "one title at a time"
        stopped, asked = titles.cancelled, len(titles.asked)
        # A message to a third thread stops the title being written...
        await c.post(f"/api/chat/threads/{other}/messages", json={"content": "something else"})
        await _until("the title stopped", lambda: titles.cancelled == stopped + 1)
        # ...and the queue starts again after it, a's title first.
        await _until("asked again", lambda: len(titles.asked) == asked + 1)
        assert titles.asked[-1] == "first thread"
        titles.gated = False
        for g in titles.gates:
            g.set()

        async def _titled() -> bool:
            return all([await _title(t) == "Model Title" for t in (a, b, other)])
        await _until("every thread titled", _titled)
        assert titles.most_at_once == 1
        for t in (a, b, other):
            await c.delete(f"/api/chat/threads/{t}")


@requires_db
async def test_help_threads_and_threads_titled_on_purpose_keep_their_titles(monkeypatch, titles) -> None:
    await _stub_replies(monkeypatch)
    async with _web() as c:
        async with session_scope() as s:
            helped = (await s.execute(text("INSERT INTO chat_threads (title) VALUES ('Help: reset the router') "
                                           "RETURNING id"))).scalar_one()
            marked = (await s.execute(text("INSERT INTO chat_threads (title, title_generated) "
                                           "VALUES ('Steps for the kitchen', true) RETURNING id"))).scalar_one()
            await s.commit()
        for t in (helped, marked):
            await c.post(f"/api/chat/threads/{t}/messages", json={"content": "thanks, and then?"})
        plain = (await c.post("/api/chat/threads", json={})).json()["id"]
        await c.post(f"/api/chat/threads/{plain}/messages", json={"content": "an ordinary chat"})
        await _until("the ordinary chat titled", lambda: titles.asked == ["an ordinary chat"])
        await asyncio.sleep(0.2)
        assert await _title(helped) == "Help: reset the router"
        assert await _title(marked) == "Steps for the kitchen"
        assert titles.asked == ["an ordinary chat"]
        for t in (helped, marked, plain):
            await c.delete(f"/api/chat/threads/{t}")


def test_titles_drop_control_and_bidi_characters() -> None:
    assert chat_titles.first_words_title("evil‮gnp.exe and\tmore") == "evilgnp.exe and more"
    assert chat_titles.clean_model_title("​Dinner⁦ plans⁩\x07") == "Dinner plans"
    assert chat_titles.plain_title("  Our‮  trip\n ") == "Our trip"
    assert chat_titles.plain_title("‮") == ""
