"""Per-role Ollama call knobs: ``options.num_ctx`` and the QA ``think`` flag.

Domovoi never set ``num_ctx``, so every call ran in the Ollama server's
default window (4096 on most hosts) while the router's prompt alone is
~3.4k tokens before plugins; and only the route call sent ``think``, so a
hybrid model used for Q&A reasoned before every answer. Three settings fix
that, each defaulting to "send nothing", which is exactly the payload every
call carried before they existed:

* ``ollama_num_ctx``      → ``options.num_ctx`` on the QA-model calls and
                            the dashboard's text chat;
* ``ollama_tool_num_ctx`` → ``options.num_ctx`` on the route call, next to
                            its ``temperature``;
* ``ollama_qa_think``     → ``think`` on the QA-model calls, sent and
                            degraded like the router's ``ollama_tool_think``
                            (omitted when the client can't take it, retried
                            once without it and latched off when the server
                            rejects it) but on a latch of its own.

These tests assert the request payloads.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from domovoi import reapply
from domovoi.clients import ollama as ollama_mod
from domovoi.clients.ollama import RealOllamaClient
from domovoi.config import Settings, settings
from domovoi.config_schema import FIELD_BY_NAME, coerce_and_validate

TOOLS = [{"name": "music", "description": "play music", "parameters": {}}]

# One reply shape that satisfies every path (see test_ollama_keep_alive).
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
    """Records kwargs; optionally rejects any call carrying ``think``.
    A streaming rejection surfaces on first iteration, as in ollama-python."""

    def __init__(self, *, reject_think: bool = False) -> None:
        self.calls: list[dict] = []
        self.reject_think = reject_think

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return self._stream(kwargs)
        self._maybe_reject(kwargs)
        return _REPLY

    def _maybe_reject(self, kwargs: dict) -> None:
        if self.reject_think and "think" in kwargs:
            raise RuntimeError('"llama3.2:3b" does not support thinking')

    async def _stream(self, kwargs: dict):
        self._maybe_reject(kwargs)
        for piece in ("hel", "lo"):
            yield {"message": {"content": piece}}

    def values(self, key: str) -> list:
        return [c.get(key, "<absent>") for c in self.calls]


def _client(
    chat,
    *,
    num_ctx: int | None = None,
    tool_num_ctx: int | None = None,
    qa_think: bool | None = None,
    send_qa_think: bool | None = None,
    send_think: bool = True,
) -> RealOllamaClient:
    c = RealOllamaClient.__new__(RealOllamaClient)   # skip __init__'s ollama import
    c._client = type("C", (), {"chat": staticmethod(chat)})()
    c._tool_model = "qwen3:8b"
    c._qa_model = "llama3.2:3b"
    c._tool_think = False
    c._send_think = send_think
    c._keep_alive = "24h"
    c._send_keep_alive = True
    c._num_ctx = num_ctx
    c._tool_num_ctx = tool_num_ctx
    c._qa_think = qa_think
    c._send_qa_think = (qa_think is not None) if send_qa_think is None else send_qa_think
    return c


async def _drain(agen) -> list[str]:
    return [piece async for piece in agen]


_QA_PATHS = [
    ("qa", lambda c: c.qa("what time is it")),
    ("stream_qa", lambda c: _drain(c.stream_qa("what time is it"))),
    ("qa_with_uncertainty", lambda c: c.qa_with_uncertainty("who won in 1998")),
    ("extract_search_subject", lambda c: c.extract_search_subject("price of gold")),
    ("extract_memories", lambda c: c.extract_memories("user: I like jazz")),
]
_ROUTE = ("route", lambda c: c.route("play jazz", TOOLS))
_QA_IDS = [p[0] for p in _QA_PATHS]


# ─── defaults: the payloads are what they always were ─────────────────────


@pytest.mark.parametrize("name,call", _QA_PATHS, ids=_QA_IDS)
async def test_qa_paths_send_no_options_and_no_think_by_default(name, call):
    chat = _FakeChat()
    await call(_client(chat))
    assert chat.values("options") == ["<absent>"], name
    assert chat.values("think") == ["<absent>"], name


async def test_route_sends_only_temperature_by_default():
    chat = _FakeChat()
    await _ROUTE[1](_client(chat))
    assert chat.calls[0]["options"] == {"temperature": 0}
    assert chat.calls[0]["think"] is False                 # ollama_tool_think, as before


async def test_a_client_built_without_init_sends_what_it_always_did():
    """The knobs have class-level defaults, so older test helpers (and any
    script assembling a client by hand) still send the old payloads."""
    chat = _FakeChat()
    c = RealOllamaClient.__new__(RealOllamaClient)
    c._client = type("C", (), {"chat": staticmethod(chat)})()
    c._tool_model, c._qa_model = "qwen3:8b", "llama3.2:3b"
    c._tool_think, c._send_think = False, True
    c._keep_alive, c._send_keep_alive = "24h", True

    await c.qa("hi")
    await c.route("play jazz", TOOLS)
    assert "options" not in chat.calls[0] and "think" not in chat.calls[0]
    assert chat.calls[1]["options"] == {"temperature": 0}


# ─── num_ctx per role ─────────────────────────────────────────────────────


@pytest.mark.parametrize("name,call", _QA_PATHS, ids=_QA_IDS)
async def test_qa_num_ctx_rides_every_qa_path(name, call):
    chat = _FakeChat()
    await call(_client(chat, num_ctx=8192))
    assert chat.values("options") == [{"num_ctx": 8192}], name
    assert chat.calls[0]["keep_alive"] == "24h"             # still through the funnel


async def test_qa_num_ctx_leaves_the_route_call_alone():
    chat = _FakeChat()
    await _ROUTE[1](_client(chat, num_ctx=8192))
    assert chat.calls[0]["options"] == {"temperature": 0}


async def test_tool_num_ctx_joins_the_route_options():
    chat = _FakeChat()
    await _ROUTE[1](_client(chat, tool_num_ctx=8192))
    assert chat.calls[0]["options"] == {"temperature": 0, "num_ctx": 8192}


@pytest.mark.parametrize("name,call", _QA_PATHS, ids=_QA_IDS)
async def test_tool_num_ctx_leaves_the_qa_paths_alone(name, call):
    chat = _FakeChat()
    await call(_client(chat, tool_num_ctx=8192))
    assert chat.values("options") == ["<absent>"], name


# ─── think on the QA calls ────────────────────────────────────────────────


@pytest.mark.parametrize("value", [False, True])
@pytest.mark.parametrize("name,call", _QA_PATHS, ids=_QA_IDS)
async def test_qa_think_is_sent_on_every_qa_path(name, call, value):
    chat = _FakeChat()
    await call(_client(chat, qa_think=value))
    assert chat.values("think") == [value], name


async def test_qa_think_does_not_touch_the_route_flag():
    chat = _FakeChat()
    await _ROUTE[1](_client(chat, qa_think=True))
    assert chat.calls[0]["think"] is False                 # still ollama_tool_think


async def test_qa_think_and_num_ctx_travel_together():
    chat = _FakeChat()
    await _client(chat, qa_think=False, num_ctx=4096).qa("hi")
    call = chat.calls[0]
    assert call["think"] is False
    assert call["options"] == {"num_ctx": 4096}
    assert call["keep_alive"] == "24h"
    assert call["model"] == "llama3.2:3b"


async def test_qa_think_omitted_when_client_too_old():
    chat = _FakeChat()
    out = await _client(chat, qa_think=False, send_qa_think=False).qa("hi")
    assert out == _CONTENT
    assert chat.values("think") == ["<absent>"]


async def test_server_rejection_retries_once_and_latches_off():
    chat = _FakeChat(reject_think=True)
    c = _client(chat, qa_think=False, num_ctx=4096)

    assert await c.qa("hi") == _CONTENT                    # the turn still answers
    assert chat.values("think") == [False, "<absent>"]     # retried without it
    assert chat.calls[1]["options"] == {"num_ctx": 4096}   # other knobs kept
    assert c._send_qa_think is False                       # and latched off
    assert c._send_think is True                           # the router's latch untouched

    await c.qa("again")
    assert len(chat.calls) == 3                            # no second retry cost
    assert chat.values("think")[-1] == "<absent>"


async def test_json_paths_recover_from_a_rejection_without_degrading():
    """qa_with_uncertainty would fall back to plain qa on a failed call;
    a think rejection must be retried before that, not turn into one."""
    chat = _FakeChat(reject_think=True)
    c = _client(chat, qa_think=False)
    out = await c.qa_with_uncertainty("who won in 1998")
    assert out.answer == "hi"
    assert [call.get("format") for call in chat.calls] == ["json", "json"]


async def test_stream_rejection_retries_before_the_first_chunk():
    chat = _FakeChat(reject_think=True)
    c = _client(chat, qa_think=False)

    out = await _drain(c.stream_qa("hi"))

    assert out == ["hel", "lo"]                            # full answer, once
    assert chat.values("think") == [False, "<absent>"]
    assert c._send_qa_think is False


async def test_stream_failure_after_first_chunk_is_not_retried():
    class MidStream(_FakeChat):
        async def _stream(self, kwargs):
            yield {"message": {"content": "hel"}}
            raise RuntimeError("model does not support thinking")

    chat = MidStream()
    c = _client(chat, qa_think=False)
    agen = c.stream_qa("hi")
    assert await agen.__anext__() == "hel"
    with pytest.raises(RuntimeError):
        await agen.__anext__()
    assert len(chat.calls) == 1
    assert c._send_qa_think is True


async def test_stream_survives_keep_alive_and_think_both_rejected():
    """Both flags refused on a streamed answer: each retry drops one, and
    the answer still arrives exactly once."""
    class Picky(_FakeChat):
        def _maybe_reject(self, kwargs):
            if "keep_alive" in kwargs:
                raise RuntimeError('time: invalid duration "24 hours"')
            if "think" in kwargs:
                raise RuntimeError("model does not support thinking")

    chat = Picky()
    c = _client(chat, qa_think=False)
    assert await _drain(c.stream_qa("hi")) == ["hel", "lo"]
    assert len(chat.calls) == 3
    assert c._send_keep_alive is False and c._send_qa_think is False


async def test_unrelated_error_is_not_mistaken_for_a_rejection():
    class Boom:
        calls = 0

        async def __call__(self, **kwargs):
            Boom.calls += 1
            raise RuntimeError("connection refused")

    c = _client(Boom(), qa_think=False)
    with pytest.raises(RuntimeError, match="connection refused"):
        await c.qa("hi")
    assert Boom.calls == 1
    assert c._send_qa_think is True


# ─── binding from settings ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "setting,expected",
    [("default", None), ("false", False), ("true", True), (False, False), (None, None),
     ("bogus", None)],
)
def test_qa_think_value(setting, expected):
    assert ollama_mod._qa_think_value(setting) is expected


@pytest.mark.parametrize("raw,expected", [(0, None), (-1, None), (8192, 8192), ("4096", 4096), ("x", None)])
def test_positive_int(raw, expected):
    assert ollama_mod._positive_int(raw) == expected


def test_real_init_binds_from_settings(monkeypatch):
    pytest.importorskip("ollama")
    monkeypatch.setattr(settings, "ollama_qa_think", "false")
    monkeypatch.setattr(settings, "ollama_num_ctx", 8192)
    monkeypatch.setattr(settings, "ollama_tool_num_ctx", 16384)
    c = RealOllamaClient(url="http://localhost:11434", qa_model="a", tool_model="b")
    assert c._qa_think is False
    assert c._send_qa_think is ollama_mod._client_accepts_think()
    assert (c._num_ctx, c._tool_num_ctx) == (8192, 16384)

    monkeypatch.setattr(settings, "ollama_qa_think", "default")
    monkeypatch.setattr(settings, "ollama_num_ctx", 0)
    monkeypatch.setattr(settings, "ollama_tool_num_ctx", 0)
    c = RealOllamaClient(url="http://localhost:11434", qa_model="a", tool_model="b")
    assert c._qa_think is None and c._send_qa_think is False
    assert (c._num_ctx, c._tool_num_ctx) == (None, None)


def test_reapply_hook_resets_the_client_for_the_new_knobs():
    from domovoi.main import _register_core_reapply_hooks

    reapply._HOOKS.clear()
    try:
        _register_core_reapply_hooks()
        registered = set(reapply.registered_fields())
        assert {"ollama_qa_think", "ollama_num_ctx", "ollama_tool_num_ctx"} <= registered
    finally:
        reapply._HOOKS.clear()


# ─── the dashboard's text chat (web process, raw /api/chat) ───────────────


def _capture_chat_body(monkeypatch) -> list[dict]:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        lines = [
            json.dumps({"message": {"content": "hi"}, "done": False}),
            json.dumps({"message": {"content": ""}, "done": True}),
        ]
        return httpx.Response(200, content="\n".join(lines).encode())

    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        return real(transport=httpx.MockTransport(handler), timeout=None)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return bodies


async def test_text_chat_sends_no_options_by_default(monkeypatch):
    monkeypatch.setattr(settings, "ollama_num_ctx", 0)
    bodies = _capture_chat_body(monkeypatch)
    out = [d async for d in ollama_mod.chat_stream([{"role": "user", "content": "hi"}], model="m")]
    assert out == ["hi"]
    assert bodies == [{"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True}]


async def test_text_chat_carries_the_qa_num_ctx(monkeypatch):
    monkeypatch.setattr(settings, "ollama_num_ctx", 8192)
    bodies = _capture_chat_body(monkeypatch)
    [d async for d in ollama_mod.chat_stream([{"role": "user", "content": "hi"}], model="m")]
    assert bodies[0]["options"] == {"num_ctx": 8192}
    assert "think" not in bodies[0]


# ─── the settings and their dashboard registration ────────────────────────


def test_defaults_send_nothing():
    fields = Settings.model_fields
    assert fields["ollama_qa_think"].default == "default"
    assert fields["ollama_num_ctx"].default == 0
    assert fields["ollama_tool_num_ctx"].default == 0


@pytest.mark.parametrize(
    "raw,expected",
    [("false", "false"), ("False", "false"), ("0", "false"), ("off", "false"),
     ("true", "true"), ("YES", "true"), ("default", "default"), ("", "default"),
     ("auto", "default")],
)
def test_env_spellings_normalize(monkeypatch, raw, expected):
    monkeypatch.setenv("OLLAMA_QA_THINK", raw)
    assert Settings(_env_file=None).ollama_qa_think == expected


def test_env_rejects_a_value_that_is_not_a_choice(monkeypatch):
    monkeypatch.setenv("OLLAMA_QA_THINK", "sometimes")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_num_ctx_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_NUM_CTX", "8192")
    monkeypatch.setenv("OLLAMA_TOOL_NUM_CTX", "16384")
    s = Settings(_env_file=None)
    assert (s.ollama_num_ctx, s.ollama_tool_num_ctx) == (8192, 16384)


def test_blank_num_ctx_env_means_unset_not_a_failed_boot(monkeypatch):
    """0 is "don't send it", and a blank value reads the same way — as a
    blank OLLAMA_KEEP_ALIVE does — instead of failing Settings() at boot."""
    monkeypatch.setenv("OLLAMA_NUM_CTX", "")
    monkeypatch.setenv("OLLAMA_TOOL_NUM_CTX", "  ")
    s = Settings(_env_file=None)
    assert (s.ollama_num_ctx, s.ollama_tool_num_ctx) == (0, 0)


def test_knobs_are_editable_next_to_the_other_ollama_settings():
    think = FIELD_BY_NAME["ollama_qa_think"]
    assert (think.group, think.section, think.tier, think.type) == ("Models", "common", "reapply", "choice")
    assert think.choices == ["default", "false", "true"]
    for name in ("ollama_num_ctx", "ollama_tool_num_ctx"):
        spec = FIELD_BY_NAME[name]
        assert (spec.group, spec.section, spec.tier, spec.type) == ("Models", "common", "reapply", "int")
        assert coerce_and_validate(spec, 0) == 0
        assert coerce_and_validate(spec, "8192") == 8192
        with pytest.raises(ValueError):
            coerce_and_validate(spec, -1)


def test_dashboard_refuses_a_think_value_outside_the_choices():
    with pytest.raises(ValueError):
        coerce_and_validate(FIELD_BY_NAME["ollama_qa_think"], "maybe")
    assert coerce_and_validate(FIELD_BY_NAME["ollama_qa_think"], "false") == "false"


def test_no_qa_model_call_skips_the_qa_knobs():
    """Belt and braces for _QA_PATHS: a QA-model call added later that went
    straight to ``_chat`` would silently lose num_ctx and think. Every
    method that calls the QA model must go through ``_qa_chat`` or add
    ``_qa_extras()`` itself."""
    import inspect

    callers = {
        name: inspect.getsource(fn)
        for name, fn in vars(RealOllamaClient).items()
        if inspect.isfunction(fn) and "model=self._qa_model" in inspect.getsource(fn)
    }
    assert set(_QA_IDS) <= set(callers)                   # the guard isn't vacuous
    for name, src in callers.items():
        assert "self._qa_chat(" in src or "self._qa_extras()" in src, name


# ─── on the wire: what the installed ollama client actually POSTs ─────────
#
# The tests above stop at the kwargs handed to ollama-python's chat(). These
# run the real AsyncClient against a fake server, so a client release that
# dropped or renamed a field — or reworded the rejection the retry keys on —
# fails here instead of quietly changing what goes out.


class _FakeOllamaServer:
    """Records each /api/chat body. With ``reject_think`` it answers a body
    carrying ``think: true`` the way Ollama answers it for a model with no
    thinking mode (HTTP 400 with an error message)."""

    def __init__(self, *, reject_think: bool = False) -> None:
        self.bodies: list[dict] = []
        self.reject_think = reject_think

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.reject_think and body.get("think") is True:
            return httpx.Response(
                400, json={"error": f'"{body["model"]}" does not support thinking'}
            )
        if body.get("stream"):
            lines = [
                json.dumps({"model": body["model"], "done": False,
                            "message": {"role": "assistant", "content": "hel"}}),
                json.dumps({"model": body["model"], "done": True,
                            "message": {"role": "assistant", "content": "lo"}}),
            ]
            return httpx.Response(200, content=("\n".join(lines) + "\n").encode())
        message = {"role": "assistant", "content": _CONTENT}
        if body.get("tools"):
            message["tool_calls"] = [{"function": {"name": "music", "arguments": {}}}]
        return httpx.Response(200, json={"model": body["model"], "message": message, "done": True})


def _wired_client(
    monkeypatch, server: _FakeOllamaServer, *,
    qa_think: str = "default", num_ctx: int = 0, tool_num_ctx: int = 0,
) -> RealOllamaClient:
    from ollama import AsyncClient  # a core dependency: no skip if it's missing

    monkeypatch.setattr(settings, "ollama_keep_alive", "24h")
    monkeypatch.setattr(settings, "ollama_tool_think", False)
    monkeypatch.setattr(settings, "ollama_qa_think", qa_think)
    monkeypatch.setattr(settings, "ollama_num_ctx", num_ctx)
    monkeypatch.setattr(settings, "ollama_tool_num_ctx", tool_num_ctx)
    c = RealOllamaClient(url="http://ollama.test:11434", qa_model="llama3.2:3b", tool_model="qwen3:8b")
    c._client = AsyncClient(host="http://ollama.test:11434", transport=httpx.MockTransport(server))
    return c


async def test_wire_defaults_are_the_payloads_every_call_always_sent(monkeypatch):
    server = _FakeOllamaServer()
    c = _wired_client(monkeypatch, server)

    await c.qa("hi")
    await c.route("play jazz", TOOLS)
    assert await _drain(c.stream_qa("hi")) == ["hel", "lo"]
    await c.extract_memories("user: I like jazz")

    qa, route, stream, memories = server.bodies
    for body in (qa, stream, memories):
        assert "options" not in body and "think" not in body, body
        assert body["keep_alive"] == "24h"
    assert route["options"] == {"temperature": 0}
    assert route["think"] is False


async def test_wire_carries_each_knob_to_its_own_role(monkeypatch):
    server = _FakeOllamaServer()
    c = _wired_client(monkeypatch, server, qa_think="false", num_ctx=8192, tool_num_ctx=16384)

    await c.qa("hi")
    await c.route("play jazz", TOOLS)
    await _drain(c.stream_qa("hi"))
    await c.qa_with_uncertainty("who won in 1998")

    qa, route, stream, uncertainty = server.bodies
    for body in (qa, stream, uncertainty):
        assert body["model"] == "llama3.2:3b"
        assert body["options"] == {"num_ctx": 8192}
        assert body["think"] is False
    assert uncertainty["format"] == "json"
    assert route["model"] == "qwen3:8b"
    assert route["options"] == {"temperature": 0, "num_ctx": 16384}
    assert route["think"] is False                         # ollama_tool_think, not QA's


async def test_wire_rejection_is_recognised_and_latched(monkeypatch):
    """Ollama's 400 for ``think`` on a model without a thinking mode, as the
    installed client raises it, trips the QA retry — non-streamed and
    streamed — and the flag stays off afterwards."""
    server = _FakeOllamaServer(reject_think=True)
    c = _wired_client(monkeypatch, server, qa_think="true")

    assert await c.qa("hi") == _CONTENT
    assert ["think" in b for b in server.bodies] == [True, False]
    assert c._send_qa_think is False

    server.bodies.clear()
    c = _wired_client(monkeypatch, server, qa_think="true")
    assert await _drain(c.stream_qa("hi")) == ["hel", "lo"]
    assert ["think" in b for b in server.bodies] == [True, False]
    assert c._send_qa_think is False
