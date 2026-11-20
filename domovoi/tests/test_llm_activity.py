"""Is this core asking the language models for something right now?
(domovoi/llm_activity.py: the signal behind whisper_remote_shares_gpu.)

Pinned: a generating request counts from send to the close of its body,
the router's early stop included; listing and the like never count; the
signal bridges the gap between two calls of one answer and then clears;
a request left open by a bug stops counting; the core's Ollama clients
carry the counter. No network: httpx.MockTransport.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from domovoi import llm_activity


@pytest.fixture(autouse=True)
def _fresh():
    llm_activity.reset()
    yield
    llm_activity.reset()


def _ollama(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/chat":
        lines = [{"message": {"content": w}, "done": False} for w in ("one ", "two ", "three")]
        lines.append({"message": {"content": ""}, "done": True})
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
    return httpx.Response(200, json={"models": []})


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="http://ollama.test:11434",
                             transport=llm_activity.TrackingTransport(httpx.MockTransport(_ollama)))


def test_a_generating_request_counts_until_its_body_is_closed() -> None:
    async def go():
        async with _client() as c:
            async with c.stream("POST", "/api/chat", json={}) as r:
                assert llm_activity.in_flight() == 1 and llm_activity.busy()
                # Read to the end: the whole answer has arrived, so Ollama
                # has stopped generating (httpx closes the body there).
                await r.aread()
                assert llm_activity.in_flight() == 0
            assert llm_activity.in_flight() == 0

    asyncio.run(go())


def test_a_reader_that_stops_early_ends_the_count() -> None:
    """The router stops reading at the first tool call; Ollama stops
    generating then, and so does the count."""
    async def go():
        async with _client() as c:
            async with c.stream("POST", "/api/chat", json={}) as r:
                async for _line in r.aiter_lines():
                    break
            assert llm_activity.in_flight() == 0

    asyncio.run(go())


def test_a_plain_request_counts_until_it_is_read() -> None:
    async def go():
        async with _client() as c:
            await c.post("/api/generate", json={})
            assert llm_activity.in_flight() == 0
        assert llm_activity.busy(), "just ended: within the quiet gap"

    asyncio.run(go())


@pytest.mark.parametrize("method,path", [("GET", "/api/tags"), ("GET", "/api/ps"),
                                         ("POST", "/api/show"), ("POST", "/api/pull"),
                                         ("GET", "/api/chat")])
def test_listing_and_loading_never_count(method, path) -> None:
    async def go():
        async with _client() as c:
            async with c.stream(method, path) as _r:
                assert llm_activity.in_flight() == 0
        assert not llm_activity.busy()

    asyncio.run(go())


def test_a_failed_request_ends_the_count() -> None:
    def refuse(request):
        raise httpx.ConnectError("refused")

    async def go():
        t = llm_activity.TrackingTransport(httpx.MockTransport(refuse))
        async with httpx.AsyncClient(base_url="http://ollama.test", transport=t) as c:
            with pytest.raises(httpx.ConnectError):
                await c.post("/api/chat", json={})
        assert llm_activity.in_flight() == 0

    asyncio.run(go())


def test_busy_bridges_the_gap_between_two_calls_then_clears(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr(llm_activity.time, "monotonic", lambda: now[0])

    async def go():
        async with llm_activity.generating("test"):
            assert llm_activity.busy()
        now[0] += llm_activity.QUIET_SEC / 2
        assert llm_activity.busy(), "the router's call ended; the answer's starts in a moment"
        now[0] += llm_activity.QUIET_SEC
        assert not llm_activity.busy()

    asyncio.run(go())


def test_a_request_left_open_stops_counting(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr(llm_activity.time, "monotonic", lambda: now[0])
    llm_activity._start("leaked")
    assert llm_activity.busy()
    now[0] += llm_activity.MAX_REQUEST_SEC + 1
    assert llm_activity.in_flight() == 0 and not llm_activity.busy()


def test_the_cores_ollama_client_carries_the_counter() -> None:
    pytest.importorskip("ollama")
    from domovoi.clients.ollama import RealOllamaClient

    c = RealOllamaClient(url="http://127.0.0.1:11434", qa_model="a", tool_model="b")
    assert isinstance(c._client._client._transport, llm_activity.TrackingTransport)
    twin = c.for_cold_start()
    assert isinstance(twin._client._client._transport, llm_activity.TrackingTransport)


def test_chat_stream_counts_while_it_streams(monkeypatch) -> None:
    from domovoi.clients import ollama

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(_ollama), **kw))
    seen: list[int] = []

    async def go():
        async for _delta in ollama.chat_stream([{"role": "user", "content": "hi"}], model="m",
                                               base_url="http://ollama.test:11434"):
            seen.append(llm_activity.in_flight())
        assert llm_activity.in_flight() == 0

    asyncio.run(go())
    assert seen and all(n >= 1 for n in seen)
