"""A model that is merely loading is never reported as a dead one.

On the all-CPU server a voice turn that needs a model Ollama hasn't loaded
pays a cold start — the load plus the router's tool-schema prefill, ~54 s
against ~4 s warm — and nothing loaded the models except the first
question: after a core restart, a model switch or an Ollama restart, the
room sat silent through it, and a load slower than the 120 s read timeout
would have ended in "My language model isn't answering right now".

Pinned here:
  * keep_alive per model (the tool model may have its own), and the
    dashboard's text chat sends the voice models' keep_alive instead of
    handing them back to Ollama's 5-minute default;
  * `cold_models` tells "not loaded" from "can't ask", and `for_cold_start`
    is the same client with a read timeout that covers a load;
  * the boot / post-switch warm-up loads both models with the options their
    real calls send;
  * a voice turn on a cold model says "Just a moment, I'm waking up my
    language model." once, first, and then answers on the long-timeout
    client — never the outage line.
"""

from __future__ import annotations

import asyncio
import io
import json
import types
import wave

import httpx
import pytest

from domovoi import llm_warmup, reapply, streaming
from domovoi.clients import ollama as ollama_mod
from domovoi.clients.ollama import RealOllamaClient
from domovoi.config import Settings, settings
from domovoi.config_schema import FIELD_BY_NAME, coerce_and_validate
from domovoi.models import Context, Intent, Response
from domovoi.router import _COLD_START_TEXT, _LLM_UNREACHABLE_TEXT, _ready_for, route

TOOLS = [{"name": "music", "description": "play music", "parameters": {}}]


class _Chat:
    """ollama.AsyncClient.chat stand-in: records kwargs, answers QA calls
    with ``answer`` (or raises it) and routing calls with no tool call."""

    def __init__(self, answer="Jane Austen.") -> None:
        self.calls: list[dict] = []
        self.answer = answer

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("tools"):
            return {"message": {"content": ""}}
        if isinstance(self.answer, BaseException):
            raise self.answer
        return {"message": {"content": self.answer}}


def _client(chat, *, url=None, keep_alive="24h", tool_keep_alive=None,
            qa_model="llama3.2:3b", tool_model="qwen3:8b",
            num_ctx=None, tool_num_ctx=None) -> RealOllamaClient:
    c = RealOllamaClient.__new__(RealOllamaClient)   # skip __init__'s ollama import
    c._client = type("C", (), {"chat": staticmethod(chat)})()
    c._url = url
    c._qa_model = qa_model
    c._tool_model = tool_model
    c._tool_think = False
    c._send_think = False
    c._keep_alive = keep_alive
    c._tool_keep_alive = tool_keep_alive
    c._send_keep_alive = True
    c._num_ctx = num_ctx
    c._tool_num_ctx = tool_num_ctx
    return c


def _ps(monkeypatch, models):
    """Make /api/ps report ``models`` ([(name, context_length)]), or fail
    (None)."""
    async def fake(base_url, timeout):
        if models is None:
            return None
        return [{"name": n, "model": n, "context_length": ctx} for n, ctx in models]

    monkeypatch.setattr(ollama_mod, "_ps_or_none", fake)


# ─── keep_alive per model ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_tool_model_can_keep_its_own_keep_alive() -> None:
    chat = _Chat()
    c = _client(chat, keep_alive="24h", tool_keep_alive=-1)
    await c.route("play jazz", TOOLS)
    await c.qa("hi")
    assert [call.get("keep_alive") for call in chat.calls] == [-1, "24h"]


@pytest.mark.asyncio
async def test_blank_tool_keep_alive_inherits() -> None:
    chat = _Chat()
    await _client(chat, tool_keep_alive=None).route("play jazz", TOOLS)
    assert chat.calls[0]["keep_alive"] == "24h"


@pytest.mark.asyncio
async def test_one_model_in_both_roles_gets_one_keep_alive() -> None:
    chat = _Chat()
    c = _client(chat, tool_keep_alive=-1, qa_model="qwen3:8b", tool_model="qwen3:8b")
    await c.route("play jazz", TOOLS)
    await c.qa("hi")
    assert [call.get("keep_alive") for call in chat.calls] == ["24h", "24h"]


def test_real_init_binds_the_tool_keep_alive(monkeypatch) -> None:
    pytest.importorskip("ollama")
    monkeypatch.setattr(settings, "ollama_keep_alive", "")
    monkeypatch.setattr(settings, "ollama_tool_keep_alive", "-1")
    c = RealOllamaClient(url="http://localhost:11434", qa_model="a", tool_model="b")
    assert c._keep_alive is None and c._tool_keep_alive == -1
    assert c._send_keep_alive is True
    assert c._url == "http://localhost:11434"


def _capture_chat_body(monkeypatch) -> list[dict]:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        lines = [json.dumps({"message": {"content": "hi"}, "done": True})]
        return httpx.Response(200, content="\n".join(lines).encode())

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: real(transport=httpx.MockTransport(handler), timeout=None),
    )
    return bodies


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,expected",
    [
        ("llama3.2:3b", "24h"),        # the Q&A model: the voice keep_alive
        ("qwen3:8b", -1),              # the tool model: its own
        ("qwen2.5vl:7b", "<absent>"),  # the vision model: the server's own
    ],
)
async def test_text_chat_keeps_the_voice_models_loaded(monkeypatch, model, expected) -> None:
    monkeypatch.setattr(settings, "ollama_model", "llama3.2:3b")
    monkeypatch.setattr(settings, "ollama_tool_model", "qwen3:8b")
    monkeypatch.setattr(settings, "ollama_keep_alive", "24h")
    monkeypatch.setattr(settings, "ollama_tool_keep_alive", "-1")
    monkeypatch.setattr(settings, "ollama_num_ctx", 0)
    bodies = _capture_chat_body(monkeypatch)
    [d async for d in ollama_mod.chat_stream([{"role": "user", "content": "hi"}], model=model)]
    assert bodies[0].get("keep_alive", "<absent>") == expected


# ─── what's loaded, and the long-timeout twin ─────────────────────────────


@pytest.mark.asyncio
async def test_cold_models_reports_what_isnt_loaded(monkeypatch) -> None:
    c = _client(_Chat(), url="http://ollama.test:11434")
    _ps(monkeypatch, [("qwen3:8b", 4096)])
    assert await c.cold_models("tool", "qa") == ["llama3.2:3b"]
    _ps(monkeypatch, [])                                   # Ollama restarted
    assert await c.cold_models("tool", "qa") == ["qwen3:8b", "llama3.2:3b"]
    _ps(monkeypatch, [("qwen3:8b", 4096), ("llama3.2:3b", 4096)])
    assert await c.cold_models("tool", "qa") == []


@pytest.mark.asyncio
async def test_cant_ask_is_not_cold(monkeypatch) -> None:
    """Ollama down or hung: None, so nobody waits out a load that isn't
    happening."""
    _ps(monkeypatch, None)
    assert await _client(_Chat(), url="http://ollama.test:11434").cold_models("qa") is None


@pytest.mark.asyncio
async def test_a_loaded_model_with_the_wrong_context_is_cold(monkeypatch) -> None:
    """Ollama reloads a model whose num_ctx changes."""
    c = _client(_Chat(), url="http://ollama.test:11434", num_ctx=8192)
    _ps(monkeypatch, [("llama3.2:3b", 32768), ("qwen3:8b", 4096)])
    assert await c.cold_models("qa") == ["llama3.2:3b"]
    assert await c.cold_models("tool") == []           # no tool num_ctx set: any is fine


@pytest.mark.asyncio
async def test_a_window_ollama_keeps_reporting_is_its_clamp_not_a_reload(monkeypatch) -> None:
    """num_ctx above a model's trained length: Ollama clamps it, doesn't
    reload, and /api/ps reports the clamped window from then on. That is a
    cold start once at most — never "waking up" before every answer."""
    c = _client(_Chat(), url="http://ollama.test:11434", tool_num_ctx=65536)
    _ps(monkeypatch, [("qwen3:8b", 40960), ("llama3.2:3b", 4096)])
    assert await c.cold_models("tool") == ["qwen3:8b"]
    assert await c.cold_models("tool") == []
    assert await c.cold_models("tool") == []
    # A different window is news again (someone else reloaded it).
    _ps(monkeypatch, [("qwen3:8b", 8192)])
    assert await c.cold_models("tool") == ["qwen3:8b"]
    # Not loaded at all is always cold.
    _ps(monkeypatch, [])
    assert await c.cold_models("tool") == ["qwen3:8b"]
    assert await c.cold_models("tool") == ["qwen3:8b"]


@pytest.mark.asyncio
async def test_untagged_names_match_latest(monkeypatch) -> None:
    c = _client(_Chat(), url="http://ollama.test:11434", qa_model="llama3.2")
    _ps(monkeypatch, [("llama3.2:latest", 4096)])
    assert await c.cold_models("qa") == []


@pytest.mark.asyncio
async def test_a_client_without_a_url_is_always_warm() -> None:
    c = _client(_Chat())
    assert await c.cold_models("tool", "qa") == []
    assert c.for_cold_start() is c


def test_the_cold_start_twin_waits_long_enough_for_a_load(monkeypatch) -> None:
    pytest.importorskip("ollama")
    monkeypatch.setattr(settings, "ollama_timeout_sec", 120.0)
    monkeypatch.setattr(settings, "ollama_load_timeout_sec", 300.0)
    c = RealOllamaClient(url="http://localhost:11434", qa_model="a", tool_model="b")
    twin = c.for_cold_start()
    assert twin is not c and twin.for_cold_start() is twin and c.for_cold_start() is twin
    assert (twin._qa_model, twin._tool_model, twin._keep_alive) == ("a", "b", c._keep_alive)
    assert c._client._client.timeout.read == 120.0
    assert twin._client._client.timeout.read == 300.0


def test_the_settings_and_their_defaults() -> None:
    fields = Settings.model_fields
    assert fields["ollama_keep_alive"].default == "24h"
    assert fields["ollama_tool_keep_alive"].default == ""
    assert fields["ollama_warmup"].default is True
    assert fields["ollama_load_timeout_sec"].default == 300.0
    assert fields["ollama_cold_start_notice"].default is True
    spec = FIELD_BY_NAME["ollama_tool_keep_alive"]
    assert (spec.group, spec.tier) == ("Models", "reapply")
    assert coerce_and_validate(spec, "") == ""
    with pytest.raises(ValueError):
        coerce_and_validate(spec, "forever")
    assert FIELD_BY_NAME["ollama_warmup"].type == "bool"
    assert FIELD_BY_NAME["ollama_cold_start_notice"].type == "bool"


# ─── the warm-up ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_warm_up_loads_both_models_like_their_real_calls(monkeypatch) -> None:
    chat = _Chat()
    c = _client(chat, url="http://ollama.test:11434", num_ctx=8192, tool_num_ctx=16384)
    c._cold_twin = c                     # no real HTTP client in a test
    _ps(monkeypatch, [])
    loaded = await c.warm_up(TOOLS)
    assert loaded == ["qwen3:8b", "llama3.2:3b"]
    tool_call, qa_call = chat.calls
    assert tool_call["model"] == "qwen3:8b"
    assert tool_call["tools"][0]["function"]["name"] == "music"   # the router's prefix
    assert tool_call["options"] == {"temperature": 0, "num_ctx": 16384, "num_predict": 1}
    assert tool_call["keep_alive"] == "24h"
    assert qa_call["model"] == "llama3.2:3b"
    assert qa_call["options"] == {"num_ctx": 8192, "num_predict": 1}
    assert qa_call["keep_alive"] == "24h"


@pytest.mark.asyncio
async def test_warm_up_rereads_the_router_prompt_even_when_loaded(monkeypatch) -> None:
    """Loaded models are not loaded again, but the router's prompt is read
    once more: a deploy (or a plugin change) that altered the tool list or
    the routing prompt would otherwise leave the whole ~3.4k-token prefix
    to the first routed question. The Q&A model is left alone — its cache
    may hold a room's conversation. Ollama down: nothing is sent."""
    chat = _Chat()
    c = _client(chat, url="http://ollama.test:11434")
    c._cold_twin = c
    c._route_stream = True
    _ps(monkeypatch, [("qwen3:8b", 4096), ("llama3.2:3b", 4096)])
    assert await c.warm_up(TOOLS) == []
    assert [call["model"] for call in chat.calls] == ["qwen3:8b"]
    assert chat.calls[0]["tools"][0]["function"]["name"] == "music"
    assert chat.calls[0]["options"]["num_predict"] == 1
    chat.calls.clear()
    _ps(monkeypatch, None)
    assert await c.warm_up(TOOLS) is None and chat.calls == []


@pytest.mark.asyncio
async def test_warm_up_learns_whether_routing_can_stream(monkeypatch) -> None:
    chat = _Chat()
    c = _client(chat, url="http://ollama.test:11434")
    c._cold_twin = c
    _ps(monkeypatch, [("qwen3:8b", 4096), ("llama3.2:3b", 4096)])

    async def version(self):
        return (0, 34, 2)

    monkeypatch.setattr(RealOllamaClient, "_server_version", version)
    await c.warm_up(TOOLS)
    assert c._route_stream is True


@pytest.mark.asyncio
async def test_a_plugin_change_schedules_a_router_reread(monkeypatch) -> None:
    from domovoi.plugins_runtime import installer

    reasons: list[str] = []
    monkeypatch.setattr(llm_warmup, "schedule_warm_up", lambda reason: reasons.append(reason))

    async def no_resync():
        return None

    import domovoi.plugins_runtime.letta_resync as letta_resync

    monkeypatch.setattr(letta_resync, "resync_tools", no_resync)
    await installer._best_effort_resync()
    assert reasons == ["plugin tools changed"]


@pytest.mark.asyncio
async def test_boot_warm_up_retries_until_ollama_answers(monkeypatch) -> None:
    answers = [None, None, ["qwen3:8b"]]

    class _Client:
        async def warm_up(self, tool_schemas):
            assert tool_schemas, "the router's tool list"
            return answers.pop(0)

    monkeypatch.setattr(settings, "use_stubs", False)
    monkeypatch.setattr(settings, "ollama_warmup", True)
    monkeypatch.setattr(llm_warmup, "_RETRY_DELAY_SEC", 0.0)
    monkeypatch.setattr(ollama_mod, "get_ollama_client", lambda: _Client())
    assert await llm_warmup.warm_up_models("boot") == ["qwen3:8b"]
    assert answers == []


@pytest.mark.asyncio
async def test_warm_up_is_off_under_stubs_or_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "use_stubs", True)
    assert llm_warmup.schedule_warm_up("boot") is None
    monkeypatch.setattr(settings, "use_stubs", False)
    monkeypatch.setattr(settings, "ollama_warmup", False)
    assert llm_warmup.schedule_warm_up("boot") is None
    assert await llm_warmup.warm_up_models("boot") == []


def test_a_model_setting_change_reloads_the_models() -> None:
    from domovoi.main import _register_core_reapply_hooks

    reapply._HOOKS.clear()
    try:
        _register_core_reapply_hooks()
        for field in (
            "ollama_model", "ollama_tool_model", "ollama_keep_alive",
            "ollama_tool_keep_alive", "ollama_num_ctx", "ollama_tool_num_ctx",
        ):
            assert reapply.run_for([field]) == [
                f"{field}:reset_ollama_client", f"{field}:schedule_llm_warmup",
            ]
        assert reapply.run_for(["ollama_qa_think"]) == ["ollama_qa_think:reset_ollama_client"]
    finally:
        reapply._HOOKS.clear()


@pytest.mark.asyncio
async def test_boot_schedules_the_warm_up(monkeypatch) -> None:
    from domovoi.main import app
    from domovoi.tests.conftest import _DB_OK

    if not _DB_OK:
        pytest.skip("the core lifespan needs the test database")
    reasons: list[str] = []
    monkeypatch.setattr(llm_warmup, "schedule_warm_up", lambda reason: reasons.append(reason))
    async with app.router.lifespan_context(app):
        assert reasons == ["boot"]


# ─── a voice turn on a cold model ─────────────────────────────────────────


class _ColdClient:
    """A client whose models aren't loaded: its ordinary HTTP client times
    out (the load outlasts the read timeout), its cold-start twin answers."""

    def __init__(self, answer: str = "Jane Austen.") -> None:
        self.twin = types.SimpleNamespace(
            route=self._route, qa_with_uncertainty=self._answer,
            cold_models=self.cold_models, for_cold_start=lambda: self.twin,
        )
        self.answer = answer
        self.checks: list[tuple[str, ...]] = []

    async def cold_models(self, *roles):
        self.checks.append(roles)
        return ["qwen3:8b"] if "tool" in roles else ["llama3.2:3b"]

    def for_cold_start(self):
        return self.twin

    async def route(self, transcript, tool_schemas):
        raise AssertionError("the ordinary client would time out on a cold model")

    async def qa_with_uncertainty(self, transcript, history=None, profile_prefix=None):
        raise AssertionError("the ordinary client would time out on a cold model")

    async def _route(self, transcript, tool_schemas):
        return None

    async def _answer(self, transcript, history=None, profile_prefix=None):
        from domovoi.clients.ollama import QAWithUncertainty

        return QAWithUncertainty(answer=self.answer, needs_verification=False)


@pytest.mark.asyncio
async def test_a_cold_turn_says_so_once_then_answers(monkeypatch) -> None:
    """DB-free: the repositories the QA fallthrough touches are faked."""
    import domovoi.router as router_mod
    from domovoi.tests.test_router import (
        _FakeSessionRepo,
        _RecordingLogRepo,
        _dry_run_winner,
    )

    # A question the tool model is asked about (a plain who/why question
    # goes straight to the Q&A model and never needs the tool model).
    said = "what is the capital of mongolia"
    assert _dry_run_winner(said) is None
    rows: list[dict] = []

    class _Repo(_RecordingLogRepo):
        pass

    _Repo.rows = rows
    client = _ColdClient()
    monkeypatch.setattr(router_mod, "SessionRepository", _FakeSessionRepo)
    monkeypatch.setattr(router_mod, "IntentLogRepository", _Repo)
    monkeypatch.setattr(router_mod, "ConversationLogRepository", _Repo)
    monkeypatch.setattr(router_mod, "get_ollama_client", lambda: client)
    monkeypatch.setattr(settings, "ollama_cold_start_notice", True)

    spoken: list[str] = []

    async def speak(text):
        spoken.append(text)

    response = await route(
        Intent(transcript=said, room_id="office"),
        Context(room_id="office", online=True, speak_interim=speak),
        None,
    )
    assert response.text == "Jane Austen."
    assert response.text != _LLM_UNREACHABLE_TEXT
    assert response.matched_path == "qa"
    # Asked before each model was needed; told the room each time, and the
    # streaming layer's speaker says it only once per turn.
    assert client.checks == [("tool",), ("qa",)]
    assert spoken == [_COLD_START_TEXT, _COLD_START_TEXT]


@pytest.mark.asyncio
async def test_the_notice_can_be_turned_off(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ollama_cold_start_notice", False)
    spoken: list[str] = []

    async def speak(text):
        spoken.append(text)

    client = _ColdClient()
    ready = await _ready_for(client, Context(room_id="office", speak_interim=speak), "qa")
    assert ready is client.twin and spoken == []


@pytest.mark.asyncio
async def test_a_warm_or_unknown_model_changes_nothing() -> None:
    spoken: list[str] = []

    async def speak(text):
        spoken.append(text)

    class _Warm(_ColdClient):
        async def cold_models(self, *roles):
            return []

    class _Unknown(_ColdClient):
        async def cold_models(self, *roles):
            return None

    ctx = Context(room_id="office", speak_interim=speak)
    for client in (_Warm(), _Unknown()):
        assert await _ready_for(client, ctx, "qa") is client
    stub = types.SimpleNamespace()                         # no cold-start API
    assert await _ready_for(stub, ctx, "qa") is stub
    assert spoken == []


# ─── the streaming side: the notice opens the reply ───────────────────────


def _wav(seconds: float, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return buf.getvalue()


class _FakeWS:
    def __init__(self) -> None:
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                satellite_voice={}, wifi_status={}, satellite_volume={},
                greeting_phrases=[], greeting_clips={},
                probe=types.SimpleNamespace(online=True),
                active_sessions={}, resumable_music={}, current_playlist={},
            )
        )
        self.frames: list[tuple[str, object]] = []

    async def send_text(self, data: str) -> None:
        self.frames.append(("text", json.loads(data)))

    async def send_bytes(self, data: bytes) -> None:
        self.frames.append(("bytes", data))


@pytest.mark.asyncio
async def test_the_interim_line_and_the_reply_are_one_response(monkeypatch) -> None:
    from domovoi.tests.conftest import _DB_OK

    if not _DB_OK:
        pytest.skip("the streaming turn opens a DB session around route()")

    class _Whisper:
        async def transcribe(self, pcm):
            return "who wrote pride and prejudice"

    class _TTS:
        async def synthesize(self, text, engine=None, voice=None):
            # The notice at 22.05 kHz, the answer at 24 kHz: the answer must
            # be brought to the rate the Pi is already playing.
            return _wav(0.5, 22_050 if text == _COLD_START_TEXT else 24_000)

    async def _route(intent, ctx, session):
        await ctx.speak_interim(_COLD_START_TEXT)
        await ctx.speak_interim(_COLD_START_TEXT)          # once per turn
        return Response(text="Jane Austen.", matched_path="qa", online=True)

    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper())
    monkeypatch.setattr(streaming, "get_tts_client", lambda: _TTS())
    monkeypatch.setattr(streaming, "route", _route)
    ws = _FakeWS()
    sess = streaming.StreamSession(ws, "office")  # type: ignore[arg-type]
    await sess._process_utterance(b"\x00" * 32_000, trigger="wake_word")

    texts = [f for kind, f in ws.frames if kind == "text"]
    starts = [f for f in texts if f.get("type") == "response_start"]
    assert len(starts) == 1, starts
    assert starts[0]["text"] == _COLD_START_TEXT
    assert starts[0]["audio_sample_rate"] == 22_050
    audio = b"".join(f for kind, f in ws.frames if kind == "bytes")
    # Half a second of notice at 22.05 kHz plus half a second of answer
    # resampled to it (within a frame or two).
    assert abs(len(audio) // 2 - 22_050) < 200
    ends = [f for f in texts if f.get("type") == "response_end"]
    assert len(ends) == 1 and ends[0]["interrupted"] is False
    assert "Jane Austen." in sess._last_spoken_text
