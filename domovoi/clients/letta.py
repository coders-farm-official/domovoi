"""Letta agent client — the conversational chat-mode (#8) bridge.

Chat mode is a *separate* path from the fast-path command router: only
conversational turns (a wake-word-triggered open-mic session) hit Letta,
so command latency is untouched. A bound Letta agent gives Domovoi
persistent memory (Letta's memory blocks + pgvector archival) and lets
the local Ollama drive tool-calls back into a CURATED media subset of the
core's existing handlers (music, playlist, library, and any
chat-exposed plugin handlers) via server-side proxy tools — see ``domovoi/letta_tools.py``.

Mirrors the Protocol / Stub / Real / factory shape of
``domovoi/clients/ollama.py``:

  - ``LettaClient`` Protocol — the interface the core wiring and
    the test suite both build against.
  - ``LettaStubClient`` — deterministic fakes used whenever ``use_stubs``
    OR ``not chat_mode_enabled`` (i.e. always in CI/tests, and in any
    deployment that hasn't opted into chat mode). The TTS path still has
    something to speak.
  - ``RealLettaClient`` — lazy ``from letta_client import Letta`` so a
    deployment without the ``[chat]`` extra installed never imports it.
  - ``get_letta_client()`` — module-level cached factory.

Two methods:
  - ``ensure_agent(agent_key)``  → resolve-or-create a household agent;
                                    returns the agent_id (bound into
                                    ``sessions.context`` by the streaming
                                    layer so a room maps to a stable agent).
  - ``chat_stream(agent_id, user_text)`` → async generator yielding ONLY
                                    assistant text deltas. ``reasoning_message``
                                    (chain-of-thought) and ``tool_call_message``
                                    are filtered out so Domovoi never speaks
                                    its internal reasoning.

⚠️  SPIKE — the LIVE path is unproven. ``RealLettaClient`` is written to
    the documented ``letta-client`` SDK API, but the full stack (Letta +
    its bundled Postgres/pgvector + a self-hosted Ollama serving both the
    LLM and the embedding model + local-model tool-calling reliability)
    has NOT been validated end-to-end. Tests exercise the STUB only; Letta
    is not running in CI. Before relying on conversational mode in
    production, bring the stack up and smoke-test agent creation +
    streaming + at least one tool round-trip per the "Conversational chat
    mode (#8, opt-in)" runbook in ``domovoi/README.md``. API drift is
    a known risk — see the inline notes on ``messages.create`` shape.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Protocol

from domovoi.config import settings

log = logging.getLogger(__name__)


# Memory blocks seeded onto a freshly-created household agent. ``persona``
# is Domovoi's identity/behavior; ``human`` accumulates facts about the
# household as the conversation runs (Letta self-edits this block);
# ``household`` is a shared scratchpad for home-lab context. ``{bot}`` is
# filled from ``settings.bot_name`` at create time. Kept short and
# voice-friendly — Letta speaks these traits, so no markdown / lists.
_PERSONA_BLOCK = (
    "You are {bot}, a friendly, concise home voice assistant running "
    "entirely on the household's local machine. You are having a spoken "
    "conversation, so keep replies short and natural — no markdown, no "
    "bullet lists, no code blocks. You can play music, playlists, and "
    "the local library when it genuinely fits the moment — for "
    "instance, put a song on because it suits what you're talking about. Use a "
    "tool only when it truly enriches the conversation, not for every passing "
    "mention, and after using one just keep chatting naturally. If you don't "
    "know something, say so plainly."
)
_HUMAN_BLOCK = (
    "This block holds long-term facts about the people in this "
    "household — names, preferences, routines. It starts mostly empty; "
    "update it as you learn things worth remembering across "
    "conversations."
)
_HOUSEHOLD_BLOCK = (
    "Shared context about this home and its lab — rooms, devices, "
    "ongoing projects. Update it as relevant facts come up."
)


class LettaClient(Protocol):
    async def ensure_agent(self, *, agent_key: str) -> str:
        """Resolve-or-create the household agent for ``agent_key``.

        Returns the Letta ``agent_id``. The caller persists it in the
        session context so subsequent turns reuse the same agent (and
        thus its accumulated memory)."""
        ...

    def chat_stream(self, *, agent_id: str, user_text: str) -> AsyncIterator[str]:
        """Stream the agent's spoken reply as text deltas.

        Yields ONLY user-facing assistant text — reasoning and tool-call
        messages are filtered out upstream so Domovoi never speaks its
        chain-of-thought. The streaming layer pipes these deltas through
        the existing per-sentence TTS path."""
        ...


class LettaStubClient:
    """Deterministic stub — used in tests and whenever chat mode is off.

    ``ensure_agent`` returns a stable fake id derived from ``agent_key``
    (no network). ``chat_stream`` yields a short canned reply derived
    from ``user_text`` in a couple of chunks, so the TTS path downstream
    has something to speak without a live Letta server."""

    async def ensure_agent(self, *, agent_key: str) -> str:
        return f"stub-agent-{agent_key}"

    async def chat_stream(
        self, *, agent_id: str, user_text: str
    ) -> AsyncIterator[str]:
        # Two text chunks so streaming/sentence-splitting code paths are
        # exercised (mirrors OllamaStubClient.stream_qa). Deterministic so
        # tests can assert on the spoken text.
        yield "(stub chat) "
        yield f"You said: {user_text}"


class RealLettaClient:
    """Live Letta client over the self-hosted REST server.

    Lazy ``from letta_client import Letta`` inside ``__init__`` so the
    package (the ``[chat]`` extra) is only required when chat mode is
    actually enabled — a deployment that never flips ``chat_mode_enabled``
    doesn't need ``letta-client`` installed at all.

    ⚠️  SPIKE. See the module docstring: this whole path is documented
        against the SDK but unvalidated against a running Letta+Ollama+
        pgvector stack. Treat every SDK call here as load-bearing-but-
        unproven until the README runbook has been walked.
    """

    def __init__(self) -> None:
        from letta_client import AsyncLetta

        # ASYNC self-hosted client. letta-client 0.1.x's create_stream() returns
        # an ASYNC generator; the sync Letta client returns a plain generator that
        # chat_stream's `async for` cannot consume (TypeError: 'async for' requires
        # __aiter__). Every other call becomes a coroutine that the _maybe_await
        # adapters below await uniformly. token == the LETTA_SERVER_PASSWORD set on
        # the container (SECURE=true — see docker-compose). Validated 2026-06/07
        # against letta-client 0.1.324.
        self._client = AsyncLetta(
            base_url=settings.letta_base_url,
            token=settings.letta_token,
        )
        self._model = settings.letta_model
        self._embedding = settings.letta_embedding_model

    async def ensure_agent(self, *, agent_key: str) -> str:
        """Find the household agent by name, creating it on first use.

        There is no idempotent create in the SDK, so we list-by-name and
        create on miss. The agent is named after ``agent_key`` (e.g. the
        household / room id) so the lookup is stable across restarts and
        a room always rebinds to the same agent + its accumulated memory.

        On any SDK error we let it propagate to the streaming layer, which
        degrades the turn gracefully (it speaks an "I couldn't reach my
        memory right now" style fallback rather than crashing the WS).
        """
        from domovoi.letta_tools import build_chat_tool_sources

        agent_name = f"domovoi-{agent_key}"

        # list() supports a name filter on current letta-client builds;
        # fall back to scanning the returned list if the kwarg is rejected
        # by a different SDK version (API drift guard).
        try:
            existing = await self._alist_agents(name=agent_name)
        except TypeError:
            existing = await self._alist_agents()
        for a in existing or ():
            a_name = getattr(a, "name", None)
            a_id = getattr(a, "id", None)
            if a_name == agent_name and a_id:
                return str(a_id)

        # Miss → create. Embedding is REQUIRED on self-hosted Docker Letta
        # (archival memory search). Tools are the curated chat_exposed media
        # set as server-side proxy sources; ``_ensure_tools`` upserts each
        # and returns the tool ids to attach at creation.
        tool_ids = await self._ensure_tools(build_chat_tool_sources())
        persona = _PERSONA_BLOCK.format(bot=settings.bot_name)
        created = await self._acreate_agent(
            name=agent_name,
            tool_ids=tool_ids,
            memory_blocks=[
                {"label": "persona", "value": persona},
                {"label": "human", "value": _HUMAN_BLOCK},
                {"label": "household", "value": _HOUSEHOLD_BLOCK},
            ],
        )
        new_id = getattr(created, "id", None)
        if not new_id:
            raise RuntimeError("Letta agent create returned no id")
        return str(new_id)

    async def chat_stream(
        self, *, agent_id: str, user_text: str
    ) -> AsyncIterator[str]:
        """Send ``user_text`` and stream the assistant reply as text deltas.

        Filters the streamed message types: forwards only
        ``assistant_message.content`` (the spoken reply). ``reasoning_message``
        is the model's chain-of-thought (never spoken). The chat tools are
        SERVER-SIDE proxies (``tools.upsert(source_code=…)``): Letta runs
        them in its own sandbox — each POSTs to ``/v1/admin/chat-tool`` and
        Letta folds the result back into a later ``assistant_message``
        itself. So there is NOTHING to dispatch client-side here; we just
        drop the tool_call/tool_return plumbing frames and speak only the
        user-facing text.

        Bounded by the SDK's own transport timeout; mirrors
        ``RealOllamaClient.stream_qa`` — a stalled Letta can't pin the
        voice turn open forever (the streaming layer also guards with
        ``chat_silence_timeout_sec`` upstream).
        """
        # ``streaming=True, stream_tokens=True`` is the documented shape;
        # some SDK builds expose ``messages.create_stream(...)`` instead.
        # Prefer the documented kwargs and let the wiring surface drift.
        stream = await self._acreate_message(
            agent_id=agent_id,
            user_text=user_text,
        )
        async for chunk in stream:
            mtype = getattr(chunk, "message_type", None)
            if mtype == "assistant_message":
                content = getattr(chunk, "content", "") or ""
                if content:
                    yield content
            # tool_call_message / tool_return_message: Letta runs the proxy tool
            # SERVER-SIDE (the tool POSTs to /v1/admin/chat-tool) and folds the
            # result back into a later assistant_message itself — nothing to
            # dispatch client-side. reasoning_message / stop_reason /
            # usage_statistics → intentionally dropped (not user-facing).

    # ── SDK adapters (thin wrappers, isolated so API drift is one-file) ──
    #
    # The letta-client surface is sync-or-async depending on the build and
    # has shifted across versions. These helpers keep every actual SDK
    # call in one place so a drift fix touches exactly one method each.

    async def _alist_agents(self, *, name: str | None = None):
        kwargs = {"name": name} if name is not None else {}
        result = self._client.agents.list(**kwargs)
        return await _maybe_await(result)

    async def _acreate_agent(self, *, name, tool_ids, memory_blocks):
        result = self._client.agents.create(
            name=name,
            model=self._model,
            embedding=self._embedding,
            memory_blocks=memory_blocks,
            tool_ids=tool_ids,
        )
        return await _maybe_await(result)

    async def _acreate_message(self, *, agent_id: str, user_text: str):
        # letta-client 0.1.x streams via a DEDICATED create_stream() method; the
        # older documented create(streaming=True, stream_tokens=True) shape raises
        # `TypeError: create() got an unexpected keyword argument 'streaming'` on
        # this SDK build. Validated against letta-client 0.1.324 (2026-06/07):
        # create_stream yields assistant_message chunks the same way chat_stream
        # already consumes them.
        result = self._client.agents.messages.create_stream(
            agent_id=agent_id,
            messages=[{"role": "user", "content": user_text}],
        )
        return await _maybe_await(result)

    async def _ensure_tools(self, tool_sources: list[str]) -> list[str]:
        """Register the chat proxy tools with Letta, returning their ids.

        Each entry is Python SOURCE for a proxy function (see
        ``letta_tools.build_chat_tool_sources``); Letta derives the tool's name
        + arg schema from the function signature + Google-style docstring, then
        runs it SERVER-SIDE when the model calls it (the function POSTs back to
        ``/v1/admin/chat-tool``). ``upsert`` is idempotent by function name, so a
        re-create after a restart reuses the existing tool row. Best-effort: a
        tool that fails to register is logged + skipped rather than sinking the
        whole agent create.
        """
        ids: list[str] = []
        for src in tool_sources:
            try:
                tool = await _maybe_await(
                    self._client.tools.upsert(source_code=src)
                )
            except Exception as e:  # noqa: BLE001 — best-effort registration
                log.warning("letta chat-tool registration failed: %s", e)
                continue
            tid = getattr(tool, "id", None)
            if tid:
                ids.append(str(tid))
        return ids


async def _maybe_await(result):
    """Await ``result`` if it's awaitable, else return it as-is.

    ``letta-client`` exposes both sync and async surfaces across builds;
    this lets the adapters call the SDK uniformly regardless of which one
    the installed version returns. A drift guard, not a correctness
    crutch — if the live SDK turns out to be consistently one or the
    other, the wrappers can be simplified."""
    import inspect

    if inspect.isawaitable(result):
        return await result
    return result


_client: LettaClient | None = None


def get_letta_client() -> LettaClient:
    """Return the cached Letta client.

    Returns the STUB whenever ``use_stubs`` (tests/CI) OR chat mode is
    disabled — so the real client (and its ``letta-client`` import) is
    only constructed in a deployment that has actually opted into
    conversational mode. Cached after first construction like the other
    client factories."""
    global _client
    if _client is not None:
        return _client
    if settings.use_stubs or not settings.chat_mode_enabled:
        _client = LettaStubClient()
    else:
        _client = RealLettaClient()
    return _client
