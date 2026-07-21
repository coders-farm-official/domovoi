"""Letta client + tool bridge (Feature 8).

Under USE_STUBS (forced by conftest) and with chat_mode_enabled off,
``get_letta_client`` returns the deterministic stub, so these run without a
Letta server. The REAL client + live tool-calling is a documented spike
(see domovoi/README.md) and is not exercised here.
"""

from __future__ import annotations

import pytest

from domovoi.clients.letta import (
    LettaStubClient,
    get_letta_client,
)
from domovoi import letta_tools


@pytest.mark.asyncio
async def test_factory_returns_stub_under_stubs() -> None:
    client = get_letta_client()
    assert isinstance(client, LettaStubClient)


@pytest.mark.asyncio
async def test_stub_ensure_agent_is_deterministic() -> None:
    client = get_letta_client()
    a = await client.ensure_agent(agent_key="domovoi")
    b = await client.ensure_agent(agent_key="domovoi")
    assert a == b
    assert "domovoi" in a


@pytest.mark.asyncio
async def test_stub_chat_stream_yields_speakable_text() -> None:
    client = get_letta_client()
    agent_id = await client.ensure_agent(agent_key="domovoi")
    chunks = [
        delta
        async for delta in client.chat_stream(
            agent_id=agent_id, user_text="tell me a fun fact"
        )
    ]
    assert chunks, "stub must yield at least one assistant delta"
    full = "".join(chunks)
    assert full.strip()  # non-empty, speakable


def test_build_chat_tool_sources_only_chat_exposed_handlers() -> None:
    import ast

    from domovoi.handlers import HANDLERS

    sources = letta_tools.build_chat_tool_sources()
    # Opt-in: one proxy per chat_exposed handler (curated media set), NOT all 20.
    chat_handlers = [h for h in HANDLERS if getattr(h, "chat_exposed", False)]
    assert chat_handlers, "expected the media handlers to be chat_exposed"
    assert len(sources) == len(chat_handlers)

    fn_names = set()
    for src in sources:
        tree = ast.parse(src)  # each proxy is valid Python
        fn = tree.body[0]
        assert isinstance(fn, ast.FunctionDef)
        # Letta derives the tool schema from a Google-style docstring — required.
        assert ast.get_docstring(fn)
        # It proxies to the core callback endpoint, not local execution.
        assert "/v1/admin/chat-tool" in src
        fn_names.add(fn.name)

    assert fn_names == {h.name for h in chat_handlers}
    # Side-effect / transactional / sensitive handlers are NOT exposed to chat.
    for excluded in ("chat_mode", "dropin", "intercom", "wifi", "voice_profile"):
        assert excluded not in fn_names


@pytest.mark.asyncio
async def test_dispatch_refuses_non_chat_exposed_handler() -> None:
    # timer is a real handler but NOT chat_exposed → the /v1/admin/chat-tool
    # dispatch gate refuses it (returns a graceful string, no DB touched) rather
    # than letting a chat call reach a non-conversational handler.
    out = await letta_tools.dispatch_tool("timer", {"action": "create"}, app=None)
    assert isinstance(out, str) and out
    assert "can't" in out.lower() or "conversation" in out.lower()


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_degrades_not_raises() -> None:
    # An unknown tool name returns a graceful string rather than raising into
    # the Letta stream loop.
    out = await letta_tools.dispatch_tool("no_such_tool_xyz", {}, app=None)
    assert isinstance(out, str)
    assert out
