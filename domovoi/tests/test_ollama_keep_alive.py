"""``keep_alive`` on every Ollama chat call.

Ollama unloads a model 5 minutes after its last request unless told
otherwise, so on a CPU host every sporadic household question paid a cold
start — model load plus the tool-schema prefill, measured at ~54 s cold
against ~4 s warm for the same question. The client must send its own
``keep_alive`` on ALL SIX chat paths, and must degrade the way the
``think`` flag does when the environment can't take it: omit it when the
installed client lacks the kwarg, retry once without it and latch off when
the server rejects the value, and never mistake an unrelated failure for
either.
"""

from __future__ import annotations

import inspect
import json

import pytest

from domovoi import reapply
from domovoi.clients import ollama as ollama_mod
from domovoi.clients.ollama import RealOllamaClient, _normalize_keep_alive
from domovoi.config import Settings
from domovoi.config_schema import FIELD_BY_NAME, coerce_and_validate

TOOLS = [{"name": "music", "description": "play music", "parameters": {}}]

# One reply shape that satisfies every path: `route` reads tool_calls, `qa`
# reads content, and the three format="json" paths parse content as JSON.
_CONTENT = json.dumps(
    {"answer": "hi", "needs_verification": False, "candidate_claim": "",
     "subject": "x", "refined_query": "y", "memories": []}
)
_REPLY = {
    "message": {
        "content": _CONTENT,
        "tool_calls": [{"function": {"name": "music", "arguments": {}}}],
    }
}


class _FakeChat:
    """Records kwargs; optionally rejects any call carrying `keep_alive`.

    Streaming calls hand back an async iterator whose rejection surfaces on
    the first iteration, exactly as ollama-python does (it opens the HTTP
    stream lazily), so the stream path's retry is exercised for real.
    """

    def __init__(self, *, reject_keep_alive: bool = False, reject_msg: str = "") -> None:
        self.calls: list[dict] = []
        self.reject_keep_alive = reject_keep_alive
        self.reject_msg = reject_msg or 'time: invalid duration "24 hours"'

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return self._stream(kwargs)
        self._maybe_reject(kwargs)
        return _REPLY

    def _maybe_reject(self, kwargs: dict) -> None:
        if self.reject_keep_alive and "keep_alive" in kwargs:
            raise RuntimeError(self.reject_msg)

    async def _stream(self, kwargs: dict):
        self._maybe_reject(kwargs)
        for piece in ("hel", "lo"):
            yield {"message": {"content": piece}}

    @property
    def keep_alive_values(self):
        return [c.get("keep_alive", "<absent>") for c in self.calls]


def _client(chat, *, keep_alive="24h", send_keep_alive: bool = True,
            send_think: bool = False) -> RealOllamaClient:
    c = RealOllamaClient.__new__(RealOllamaClient)   # skip __init__'s ollama import
    c._client = type("C", (), {"chat": staticmethod(chat)})()
    c._tool_model = "qwen3:8b"
    c._qa_model = "llama3.2:3b"
    c._tool_think = False
    c._send_think = send_think
    c._keep_alive = keep_alive
    c._send_keep_alive = send_keep_alive
    return c


async def _drain(agen) -> list[str]:
    return [piece async for piece in agen]


# Every chat path the real client has, as (name, call). A new call site
# that bypasses `_chat` would be a silent regression, so this list is the
# thing to extend when one is added.
_PATHS = [
    ("route", lambda c: c.route("play jazz", TOOLS)),
    ("qa", lambda c: c.qa("what time is it")),
    ("stream_qa", lambda c: _drain(c.stream_qa("what time is it"))),
    ("qa_with_uncertainty", lambda c: c.qa_with_uncertainty("who won in 1998")),
    ("extract_search_subject", lambda c: c.extract_search_subject("price of gold")),
    ("extract_memories", lambda c: c.extract_memories("user: I like jazz")),
]


@pytest.mark.parametrize("name,call", _PATHS, ids=[p[0] for p in _PATHS])
@pytest.mark.asyncio
async def test_every_chat_path_carries_keep_alive(name, call):
    chat = _FakeChat()
    await call(_client(chat))
    assert chat.keep_alive_values == ["24h"], name


def test_no_chat_call_site_bypasses_the_funnel():
    """Belt and braces for the list above: the real client must not call
    `self._client.chat` anywhere except inside `_chat` itself."""
    src = inspect.getsource(RealOllamaClient)
    funnel = inspect.getsource(RealOllamaClient._chat)
    assert src.count("self._client.chat(") == funnel.count("self._client.chat(") == 3


@pytest.mark.asyncio
async def test_think_and_keep_alive_travel_together_on_route():
    chat = _FakeChat()
    await _client(chat, send_think=True).route("play jazz", TOOLS)
    assert chat.calls[0]["think"] is False
    assert chat.calls[0]["keep_alive"] == "24h"


@pytest.mark.asyncio
async def test_omitted_when_client_too_old():
    """An ollama-python without the kwarg must get a call with no
    `keep_alive` at all, not a TypeError on every turn."""
    chat = _FakeChat()
    out = await _client(chat, send_keep_alive=False).qa("hi")
    assert out == _CONTENT
    assert chat.keep_alive_values == ["<absent>"]


@pytest.mark.asyncio
async def test_server_rejection_retries_once_and_latches_off():
    chat = _FakeChat(reject_keep_alive=True)
    c = _client(chat)

    assert await c.qa("hi") == _CONTENT                     # the turn still answers
    assert chat.keep_alive_values == ["24h", "<absent>"]    # retried without it
    assert c._send_keep_alive is False                      # and latched off

    await c.qa("again")
    assert len(chat.calls) == 3                             # no second retry cost
    assert chat.keep_alive_values[-1] == "<absent>"


@pytest.mark.asyncio
async def test_too_old_client_typeerror_also_latches_off():
    """The signature probe can be wrong (a shim, a fork) — the runtime
    TypeError must be treated like a rejection, not surfaced per turn."""
    chat = _FakeChat(
        reject_keep_alive=True,
        reject_msg="chat() got an unexpected keyword argument 'keep_alive'",
    )
    c = _client(chat)
    assert await c.qa("hi") == _CONTENT
    assert c._send_keep_alive is False


@pytest.mark.asyncio
async def test_stream_rejection_retries_before_the_first_chunk():
    chat = _FakeChat(reject_keep_alive=True)
    c = _client(chat)

    out = await _drain(c.stream_qa("hi"))

    assert out == ["hel", "lo"]                             # full answer, once
    assert chat.keep_alive_values == ["24h", "<absent>"]
    assert c._send_keep_alive is False


@pytest.mark.asyncio
async def test_stream_failure_after_first_chunk_is_not_retried():
    """Once text has been yielded a restart would duplicate it — the
    error must propagate instead, whatever it says."""
    class MidStream(_FakeChat):
        async def _stream(self, kwargs):
            yield {"message": {"content": "hel"}}
            raise RuntimeError('time: invalid duration "x"')

    chat = MidStream()
    c = _client(chat)
    agen = c.stream_qa("hi")
    assert await agen.__anext__() == "hel"
    with pytest.raises(RuntimeError):
        await agen.__anext__()
    assert len(chat.calls) == 1
    assert c._send_keep_alive is True


@pytest.mark.asyncio
async def test_unrelated_error_is_not_mistaken_for_a_rejection():
    class Boom:
        calls = 0

        async def __call__(self, **kwargs):
            Boom.calls += 1
            raise RuntimeError("connection refused")

    c = _client(Boom())
    with pytest.raises(RuntimeError, match="connection refused"):
        await c.qa("hi")
    assert Boom.calls == 1                                  # no pointless retry
    assert c._send_keep_alive is True                       # and not latched off


# ─── Value handling ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("24h", "24h"),                 # durations go through as strings
        ("1h30m", "1h30m"),
        ("-1", -1),                     # bare numbers are seconds → JSON numbers
        ("3600", 3600),                 # ("-1" as a STRING is not a valid Go duration)
        ("0", 0),
        ("1.5", 1.5),
        (" 24h ", "24h"),
        ("", None),                     # blank = don't send it
        ("   ", None),
        (None, None),
    ],
)
def test_normalize_keep_alive(raw, expected):
    got = _normalize_keep_alive(raw)
    assert got == expected and type(got) is type(expected)


def test_real_init_binds_from_settings(monkeypatch):
    pytest.importorskip("ollama")
    monkeypatch.setattr(ollama_mod.settings, "ollama_keep_alive", "-1")
    c = RealOllamaClient(url="http://localhost:11434", qa_model="a", tool_model="b")
    assert c._keep_alive == -1
    assert c._send_keep_alive is True

    monkeypatch.setattr(ollama_mod.settings, "ollama_keep_alive", "")
    c = RealOllamaClient(url="http://localhost:11434", qa_model="a", tool_model="b")
    assert c._keep_alive is None
    assert c._send_keep_alive is False                      # blank → never sent


def test_capability_probe_is_defensive():
    ollama_mod._client_accepts_keep_alive.cache_clear()
    assert isinstance(ollama_mod._client_accepts_keep_alive(), bool)


# ─── The setting and its dashboard registration ──────────────────────────


def test_setting_exists_with_a_generous_default():
    assert Settings.model_fields["ollama_keep_alive"].default == "24h"
    assert Settings().ollama_keep_alive == "24h"


def test_setting_is_editable_from_the_models_page():
    spec = FIELD_BY_NAME["ollama_keep_alive"]
    assert spec.group == "Models"
    assert spec.section == "common"
    assert spec.tier == "reapply"       # bound at client construction, like think
    assert spec.type == "str"


@pytest.mark.parametrize("ok", ["24h", "90m", "1h30m", "500ms", "3600", "-1", "0", "", " 24h "])
def test_dashboard_accepts_ollama_durations(ok):
    assert coerce_and_validate(FIELD_BY_NAME["ollama_keep_alive"], ok) == ok


@pytest.mark.parametrize("bad", ["24 hours", "forever", "1d", "h", "24h!", "1e3"])
def test_dashboard_rejects_what_ollama_cannot_parse(bad):
    """A value Ollama can't parse would fail every chat call — the write
    endpoint must refuse it (pydantic doesn't validate on assignment)."""
    with pytest.raises(ValueError, match="Ollama duration"):
        coerce_and_validate(FIELD_BY_NAME["ollama_keep_alive"], bad)


def test_reapply_hook_resets_the_client():
    """The setting is read once at client construction, so a dashboard edit
    only takes if the lifespan registers it against reset_ollama_client —
    the 'shipped but not where it's read' trap."""
    from domovoi.main import _register_core_reapply_hooks

    reapply._HOOKS.clear()
    try:
        _register_core_reapply_hooks()
        registered = set(reapply.registered_fields())
        assert {"ollama_keep_alive", "ollama_tool_think"} <= registered
        assert reapply.run_for(["ollama_keep_alive"]) == ["ollama_keep_alive:reset_ollama_client"]
    finally:
        reapply._HOOKS.clear()
