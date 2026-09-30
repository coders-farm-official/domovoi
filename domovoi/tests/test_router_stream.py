"""The tool router stops the moment it knows the answer.

Measured 2026-09-30 on qwen3:8b, CPU-only Ollama 0.34.2, the live Beelink
tool list (3,340 prompt tokens): with no tool to call, the router wrote a
plain-text answer of its own that nobody used — "Tell me a joke." 21-42
tokens (3-5 s), "Tell me about the history of Rome." 304 tokens (48 s) —
and only then did the Q&A model start. The routing call is now streamed
and dropped at the first word of text (no tool is coming) or at the first
tool call (Ollama sends it whole), and its length is capped either way.

Pinned here:
  * a streamed tool call routes, and the stream is closed right after it;
  * plain text ends the call at its first word — the rest is never read;
  * whitespace and a ``<think>`` block are not taken for an answer; any
    other text is, a tool call written out as text included (0.9+ sends a
    real call parsed, and nothing reads one out of the text);
  * only an Ollama known to stream tool calls (and take ``think``) gets a
    streamed call; older, unknown and unreachable ones get the plain
    request, and every routing call carries a ``num_predict`` cap — the
    tight one only where the model is known not to reason first;
  * ``think`` / ``keep_alive`` rejections that only surface once a stream
    is read are retried and latched off, as for the plain request;
  * on the wire, through the installed ollama client, the connection is
    closed after the first text chunk.
"""

from __future__ import annotations

import json

import httpx
import pytest

from domovoi.clients import ollama as ollama_mod
from domovoi.clients.ollama import (
    ROUTER_NUM_PREDICT,
    ROUTER_STREAM_MIN_OLLAMA,
    ROUTER_THINK_NUM_PREDICT,
    RealOllamaClient,
    _parse_ollama_version,
    _plain_text_started,
)
from domovoi.config import settings

TOOLS = [{"name": "music", "description": "play music", "parameters": {}}]
_CALL = {"function": {"name": "music", "arguments": {"action": "play", "query": "jazz"}}}


class _Stream:
    """An ollama-python response stream: yields chunks, counts how many were
    read, remembers whether it was closed."""

    def __init__(self, chunks, *, error: Exception | None = None) -> None:
        self.chunks = list(chunks)
        self.read = 0
        self.closed = False
        self.error = error

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.error is not None:
            raise self.error
        if self.read >= len(self.chunks):
            raise StopAsyncIteration
        chunk = self.chunks[self.read]
        self.read += 1
        return chunk

    async def aclose(self) -> None:
        self.closed = True


def _text(*pieces: str, done: bool = True) -> list[dict]:
    chunks = [{"message": {"role": "assistant", "content": p}, "done": False} for p in pieces]
    if done:
        chunks.append({"message": {"role": "assistant", "content": ""}, "done": True})
    return chunks


class _Chat:
    """``AsyncClient.chat`` stand-in: records kwargs; a streamed call gets
    the next scripted _Stream, a plain one the scripted reply."""

    def __init__(self, *streams, reply=None) -> None:
        self.streams = list(streams)
        self.reply = reply
        self.calls: list[dict] = []
        self.handed_out: list[_Stream] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if not kwargs.get("stream"):
            return self.reply
        stream = self.streams.pop(0)
        self.handed_out.append(stream)
        return stream


def _client(chat, *, stream: bool | None = True, send_think: bool = True, think=False) -> RealOllamaClient:
    c = RealOllamaClient.__new__(RealOllamaClient)   # skip __init__'s ollama import
    c._client = type("C", (), {"chat": staticmethod(chat)})()
    c._tool_model = "qwen3:8b"
    c._qa_model = "llama3.2:3b"
    c._tool_think = think
    c._send_think = send_think
    c._keep_alive = "24h"
    c._send_keep_alive = True
    c._route_stream = stream
    return c


# ─── streamed: stop at the answer ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_streamed_tool_call_routes_and_the_stream_is_closed_after_it() -> None:
    s = _Stream([
        {"message": {"content": "", "tool_calls": [_CALL]}, "done": False},
        {"message": {"content": ""}, "done": False},     # the model's tail
        {"message": {"content": ""}, "done": True},
    ])
    chat = _Chat(s)
    out = await _client(chat).route("put on some jazz", TOOLS)
    assert out == {"handler": "music", "args": {"action": "play", "query": "jazz"}}
    assert s.read == 1 and s.closed          # nothing after the call is waited for
    assert chat.calls[0]["stream"] is True


@pytest.mark.asyncio
async def test_plain_text_ends_the_call_at_its_first_word() -> None:
    s = _Stream(_text("Why", " did", " the", " scarecrow", " win", " an", " award?"))
    out = await _client(_Chat(s)).route("tell me a joke", TOOLS)
    assert out is None                        # the QA fallthrough answers it
    assert s.read == 1 and s.closed


@pytest.mark.asyncio
async def test_whitespace_is_not_an_answer_yet() -> None:
    s = _Stream([
        {"message": {"content": "\n\n"}, "done": False},
        {"message": {"content": "", "tool_calls": [_CALL]}, "done": False},
        {"message": {"content": ""}, "done": True},
    ])
    out = await _client(_Chat(s)).route("play jazz", TOOLS)
    assert out is not None and out["handler"] == "music"
    assert s.read == 2 and s.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pieces",
    [
        ('{"name": "calculator", "arguments": {"action": "arithmetic"', "}}"),
        ("<tool", "_call>", '\n{"name": "music"', "}\n</tool_call>"),
        ("```json\n", '{"name": "dismiss"}', "\n```"),
    ],
)
async def test_a_tool_call_written_as_text_ends_the_call_too(pieces) -> None:
    """A call written out as text isn't a call: the streamed router only runs
    on Ollama 0.9.0+, whose parser sends a real one parsed, and nothing reads
    a call out of the text. So it ends the call at once, like any text —
    qwen3:8b wrote '{"name": "calculator", ...' as plain text for "can
    penguins fly?", and reading it out cost 6.6 s (measured 2026-09-30)."""
    s = _Stream(_text(*pieces))
    out = await _client(_Chat(s)).route("can penguins fly", TOOLS)
    assert out is None
    assert s.read == 1 and s.closed


@pytest.mark.asyncio
async def test_reasoning_left_in_the_text_is_skipped() -> None:
    s = _Stream([
        {"message": {"content": "<think>"}, "done": False},
        {"message": {"content": "the user wants music"}, "done": False},
        {"message": {"content": "</think>\n\n"}, "done": False},
        {"message": {"content": "", "tool_calls": [_CALL]}, "done": False},
        {"message": {"content": ""}, "done": True},
    ])
    out = await _client(_Chat(s)).route("play jazz", TOOLS)
    assert out is not None and out["handler"] == "music"
    assert s.read == 4


def test_what_counts_as_text_starting() -> None:
    assert _plain_text_started("I")
    assert _plain_text_started("  Sure")
    assert not _plain_text_started("")
    assert not _plain_text_started(" \n")
    # The start of a <think> block is not text yet, nor is the block itself.
    for partial in ("<", "<th", "<think", "<think>", "<think>hmm", "<think>hmm</think>", " <think>\n"):
        assert not _plain_text_started(partial), partial
    assert _plain_text_started("<think>\n\n</think>\n\nParis.")
    # Anything else is: a call written out as text is never read as one.
    for text in ("<tool", "<tool_call>", "{", '{"name": "x"', "[TOOL", "`", "```json", '{"answer": 1}'):
        assert _plain_text_started(text), text


@pytest.mark.asyncio
async def test_thinking_tokens_are_not_text() -> None:
    """With the router allowed to think (0.9+ sends it apart), reasoning
    arrives in ``thinking`` and must not end the call."""
    s = _Stream([
        {"message": {"content": "", "thinking": "The user wants"}, "done": False},
        {"message": {"content": "", "thinking": " music."}, "done": False},
        {"message": {"content": "", "tool_calls": [_CALL]}, "done": False},
    ])
    out = await _client(_Chat(s), think=True).route("play jazz", TOOLS)
    assert out is not None and out["handler"] == "music"


@pytest.mark.asyncio
async def test_a_client_that_answers_in_one_piece_still_routes() -> None:
    chat = _Chat()
    chat.streams = []

    async def one_piece(**kwargs):
        chat.calls.append(kwargs)
        return {"message": {"content": "", "tool_calls": [_CALL]}}

    c = _client(chat)
    c._client = type("C", (), {"chat": staticmethod(one_piece)})()
    out = await c.route("play jazz", TOOLS)
    assert out is not None and out["handler"] == "music"


# ─── the cap ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stream", "cap"),
    [(True, ROUTER_NUM_PREDICT), (False, ROUTER_THINK_NUM_PREDICT), (None, ROUTER_THINK_NUM_PREDICT)],
)
async def test_every_routing_call_is_capped(stream, cap) -> None:
    """The tight cap only on a streamed call sent with think=false; a plain
    request (an old server, or one whose version isn't known) may carry a
    hybrid model's reasoning before its call, which the tight cap cut off
    mid-thought (qwen3:8b: 4 of 6 LLM-routed commands lost, 2026-09-30)."""
    reply = {"message": {"content": "Paris."}}
    chat = _Chat(_Stream(_text("Paris.")), reply=reply)
    await _client(chat, stream=stream).route("capital of france", TOOLS)
    assert chat.calls[0]["stream"] is bool(stream)
    assert chat.calls[0]["options"] == {"temperature": 0, "num_predict": cap}


@pytest.mark.asyncio
async def test_a_think_flag_latched_off_gets_room_to_reason() -> None:
    """After the server rejected ``think`` it is no longer sent, so a hybrid
    model may reason first — in the stream too — and gets the larger cap."""
    chat = _Chat(_Stream(_text("Paris.")))
    await _client(chat, send_think=False).route("capital of france", TOOLS)
    assert "think" not in chat.calls[0]
    assert chat.calls[0]["options"]["num_predict"] == ROUTER_THINK_NUM_PREDICT


@pytest.mark.asyncio
async def test_a_rejected_think_flag_retries_with_the_larger_cap() -> None:
    rejected = _Stream([], error=RuntimeError('"qwen3:8b" does not support thinking'))
    ok = _Stream([{"message": {"content": "", "tool_calls": [_CALL]}, "done": False}])
    chat = _Chat(rejected, ok)
    await _client(chat).route("play jazz", TOOLS)
    assert [call["options"]["num_predict"] for call in chat.calls] == [
        ROUTER_NUM_PREDICT, ROUTER_THINK_NUM_PREDICT,
    ]


@pytest.mark.asyncio
async def test_the_cap_leaves_room_for_reasoning_when_the_router_thinks() -> None:
    chat = _Chat(_Stream(_text("Paris.")))
    await _client(chat, think=True).route("capital of france", TOOLS)
    assert chat.calls[0]["think"] is True
    assert chat.calls[0]["options"]["num_predict"] == ROUTER_THINK_NUM_PREDICT


def test_the_cap_fits_the_prompt_in_a_4096_context() -> None:
    """The live prompt is ~3,340 tokens with the bundled radio plugin: a
    reply past the rest of a 4096 window makes Ollama shift the context and
    drop the cached tool prefix. Only for the stock tool list — more plugin
    tools need a larger ``ollama_tool_num_ctx`` (llm_warmup warns)."""
    assert 3400 + ROUTER_NUM_PREDICT <= 4096
    assert 32 <= ROUTER_NUM_PREDICT


# ─── which servers get a streamed call ────────────────────────────────────


def test_version_parsing() -> None:
    assert _parse_ollama_version("0.34.2") == (0, 34, 2)
    assert _parse_ollama_version("0.9.1-rc0") == (0, 9, 1)
    assert _parse_ollama_version("v0.12") == (0, 12, 0)
    assert _parse_ollama_version("0.0.0") is None       # a source build says nothing
    assert _parse_ollama_version("") is None
    assert _parse_ollama_version(None) is None
    assert ROUTER_STREAM_MIN_OLLAMA == (0, 9, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "streams"),
    [("0.34.2", True), ("0.9.0", True), ("0.8.1", False), ("0.5.7", False),
     ("0.3.14", False), ("0.0.0", False)],
)
async def test_only_a_new_enough_server_gets_a_streamed_call(monkeypatch, version, streams) -> None:
    c = _client(_Chat(), stream=None)
    c._url = "http://ollama.test:11434"

    async def fake_version(self):
        return _parse_ollama_version(version)

    monkeypatch.setattr(RealOllamaClient, "_server_version", fake_version)
    assert await c._route_streams() is streams
    assert c._route_stream is (streams if _parse_ollama_version(version) else None)


@pytest.mark.asyncio
async def test_a_failed_probe_gets_the_plain_request_and_is_asked_again(monkeypatch) -> None:
    answers = [None, (0, 34, 2)]

    async def fake_version(self):
        return answers.pop(0)

    monkeypatch.setattr(RealOllamaClient, "_server_version", fake_version)
    reply = {"message": {"content": "", "tool_calls": [_CALL]}}
    chat = _Chat(_Stream([{"message": {"content": "", "tool_calls": [_CALL]}, "done": False}]), reply=reply)
    c = _client(chat, stream=None)
    c._url = "http://ollama.test:11434"
    await c.route("play jazz", TOOLS)
    await c.route("play jazz", TOOLS)
    assert [call["stream"] for call in chat.calls] == [False, True]


@pytest.mark.asyncio
async def test_a_client_without_a_url_never_streams() -> None:
    reply = {"message": {"content": "", "tool_calls": [_CALL]}}
    chat = _Chat(reply=reply)
    assert await _client(chat, stream=None).route("play jazz", TOOLS) is not None
    assert chat.calls[0]["stream"] is False


# ─── rejections that surface once the stream is read ──────────────────────


@pytest.mark.asyncio
async def test_a_think_rejection_in_the_stream_is_retried_without_it() -> None:
    rejected = _Stream([], error=RuntimeError('"qwen3:8b" does not support thinking'))
    ok = _Stream([{"message": {"content": "", "tool_calls": [_CALL]}, "done": False}])
    chat = _Chat(rejected, ok)
    c = _client(chat)
    out = await c.route("play jazz", TOOLS)
    assert out is not None and out["handler"] == "music"
    assert ["think" in call for call in chat.calls] == [True, False]
    assert c._send_think is False and rejected.closed


@pytest.mark.asyncio
async def test_a_keep_alive_rejection_in_the_stream_is_retried_without_it() -> None:
    rejected = _Stream([], error=RuntimeError('time: invalid duration "forever"'))
    ok = _Stream(_text("Paris."))
    chat = _Chat(rejected, ok)
    c = _client(chat)
    assert await c.route("capital of france", TOOLS) is None
    assert ["keep_alive" in call for call in chat.calls] == [True, False]
    assert c._send_keep_alive is False


@pytest.mark.asyncio
async def test_any_other_stream_failure_falls_through_to_qa_without_a_retry() -> None:
    broken = _Stream([], error=RuntimeError("connection reset"))
    chat = _Chat(broken)
    assert await _client(chat).route("play jazz", TOOLS) is None
    assert len(chat.calls) == 1


# ─── on the wire ──────────────────────────────────────────────────────────


class _LineStream(httpx.AsyncByteStream):
    """A streamed /api/chat body that records how far it was read and
    whether the client closed it."""

    def __init__(self, lines: list[dict]) -> None:
        self.lines = lines
        self.sent = 0
        self.closed = False

    async def __aiter__(self):
        for line in self.lines:
            self.sent += 1
            yield (json.dumps(line) + "\n").encode()

    async def aclose(self) -> None:
        self.closed = True


class _Server:
    def __init__(self, version: str, lines: list[dict]) -> None:
        self.version = version
        self.body = _LineStream(lines)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": self.version})
        self.requests.append(json.loads(request.content))
        return httpx.Response(200, stream=self.body)


def _wired(monkeypatch, server: _Server) -> RealOllamaClient:
    from ollama import AsyncClient  # a core dependency: no skip if it's missing

    monkeypatch.setattr(settings, "ollama_keep_alive", "24h")
    monkeypatch.setattr(settings, "ollama_tool_think", False)
    monkeypatch.setattr(settings, "ollama_tool_num_ctx", 0)
    c = RealOllamaClient(url="http://ollama.test:11434", qa_model="llama3.2:3b", tool_model="qwen3:8b")
    c._client = AsyncClient(host="http://ollama.test:11434", transport=httpx.MockTransport(server))
    return c


@pytest.mark.asyncio
async def test_on_the_wire_text_closes_the_connection_after_its_first_chunk(monkeypatch) -> None:
    lines = [
        {"model": "qwen3:8b", "message": {"role": "assistant", "content": w}, "done": False}
        for w in ("I", " don't", " have", " a", " tool", " for", " that.")
    ] + [{"model": "qwen3:8b", "message": {"role": "assistant", "content": ""}, "done": True}]
    server = _Server("0.34.2", lines)
    c = _wired(monkeypatch, server)
    assert await c.route("Tell me a joke.", TOOLS) is None
    body = server.requests[0]
    assert body["stream"] is True
    assert body["options"] == {"temperature": 0, "num_predict": ROUTER_NUM_PREDICT}
    assert body["think"] is False and body["keep_alive"] == "24h"
    assert server.body.closed
    assert server.body.sent < len(lines)


@pytest.mark.asyncio
async def test_on_the_wire_a_tool_call_routes(monkeypatch) -> None:
    lines = [
        {"model": "qwen3:8b", "message": {"role": "assistant", "content": "", "tool_calls": [_CALL]}, "done": False},
        {"model": "qwen3:8b", "message": {"role": "assistant", "content": ""}, "done": True},
    ]
    server = _Server("0.34.2", lines)
    out = await _wired(monkeypatch, server).route("Put on some jazz.", TOOLS)
    assert out == {"handler": "music", "args": {"action": "play", "query": "jazz"}}
    assert server.body.closed


@pytest.mark.asyncio
async def test_on_the_wire_an_old_server_gets_the_plain_request(monkeypatch) -> None:
    class _Old(_Server):
        def __call__(self, request):
            if request.url.path == "/api/version":
                return httpx.Response(200, json={"version": "0.5.7"})
            self.requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                "model": "qwen3:8b", "done": True,
                "message": {"role": "assistant", "content": "", "tool_calls": [_CALL]},
            })

    server = _Old("0.5.7", [])
    out = await _wired(monkeypatch, server).route("Put on some jazz.", TOOLS)
    assert out is not None and out["handler"] == "music"
    assert server.requests[0]["stream"] is False
    assert server.requests[0]["options"]["num_predict"] == ROUTER_THINK_NUM_PREDICT


def test_the_module_exports_what_the_tests_pin() -> None:
    assert ollama_mod.ROUTER_NUM_PREDICT == ROUTER_NUM_PREDICT
