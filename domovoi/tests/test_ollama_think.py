"""The `think` flag on the tool-routing call.

Routing is the latency-critical step of every non-fast-path turn, so the
router must not silently pay a thinking model's reasoning cost — and, just
as importantly, an environment that can't accept the flag must degrade to
a normal call rather than failing every route into the QA fallthrough.
"""

from __future__ import annotations

import pytest

from domovoi.clients import ollama as ollama_mod
from domovoi.clients.ollama import RealOllamaClient

TOOLS = [{"name": "music", "description": "play music", "parameters": {}}]


def _reply(name: str = "music"):
    return {"message": {"tool_calls": [{"function": {"name": name, "arguments": {}}}]}}


class _FakeChat:
    """Records kwargs; optionally raises on the first call that sends `think`."""

    def __init__(self, *, reject_think: bool = False, reject_msg: str = "") -> None:
        self.calls: list[dict] = []
        self.reject_think = reject_think
        self.reject_msg = reject_msg or 'registry.ollama.ai: model does not support thinking'

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject_think and "think" in kwargs:
            raise RuntimeError(self.reject_msg)
        return _reply()

    @property
    def think_values(self):
        return [c.get("think", "<absent>") for c in self.calls]


def _client(monkeypatch, chat, *, send_think: bool, think: bool) -> RealOllamaClient:
    c = RealOllamaClient.__new__(RealOllamaClient)   # skip __init__'s ollama import
    c._client = type("C", (), {"chat": staticmethod(chat)})()
    c._tool_model = "qwen3:8b"
    c._qa_model = "llama3.2:3b"
    c._tool_think = think
    c._send_think = send_think
    return c


@pytest.mark.asyncio
async def test_think_false_is_sent_by_default(monkeypatch):
    chat = _FakeChat()
    out = await _client(monkeypatch, chat, send_think=True, think=False).route("play jazz", TOOLS)
    assert out == {"handler": "music", "args": {}}
    assert chat.think_values == [False]


@pytest.mark.asyncio
async def test_think_true_is_forwarded_when_enabled(monkeypatch):
    chat = _FakeChat()
    await _client(monkeypatch, chat, send_think=True, think=True).route("play jazz", TOOLS)
    assert chat.think_values == [True]


@pytest.mark.asyncio
async def test_flag_omitted_when_client_too_old(monkeypatch):
    """An ollama-python without the kwarg must get a call with no `think` at
    all, not a TypeError that degrades routing to QA."""
    chat = _FakeChat()
    out = await _client(monkeypatch, chat, send_think=False, think=False).route("play jazz", TOOLS)
    assert out == {"handler": "music", "args": {}}
    assert chat.think_values == ["<absent>"]


@pytest.mark.asyncio
async def test_server_rejection_retries_once_without_think(monkeypatch):
    chat = _FakeChat(reject_think=True)
    c = _client(monkeypatch, chat, send_think=True, think=False)

    out = await c.route("play jazz", TOOLS)

    assert out == {"handler": "music", "args": {}}          # the turn still routes
    assert chat.think_values == [False, "<absent>"]         # retried without it
    assert c._send_think is False                           # and latched off


@pytest.mark.asyncio
async def test_latched_off_means_no_second_retry_cost(monkeypatch):
    chat = _FakeChat(reject_think=True)
    c = _client(monkeypatch, chat, send_think=True, think=False)

    await c.route("play jazz", TOOLS)
    await c.route("play blues", TOOLS)

    # 2 for the first turn (reject + retry), 1 for the second — not 4.
    assert len(chat.calls) == 3
    assert chat.think_values[-1] == "<absent>"


@pytest.mark.asyncio
async def test_unrelated_error_still_degrades_to_qa(monkeypatch):
    """A real failure must not be mistaken for a think rejection."""
    class Boom:
        calls = 0

        async def __call__(self, **kwargs):
            Boom.calls += 1
            raise RuntimeError("connection refused")

    c = _client(monkeypatch, Boom(), send_think=True, think=False)
    assert await c.route("play jazz", TOOLS) is None
    assert Boom.calls == 1          # no pointless retry


def test_capability_probe_is_defensive(monkeypatch):
    """The probe must never raise, whatever the environment looks like."""
    ollama_mod._client_accepts_think.cache_clear()
    assert isinstance(ollama_mod._client_accepts_think(), bool)
