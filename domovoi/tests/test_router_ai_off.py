"""The router with the household's AI features off, and the truthful line
when a model was never downloaded (owner direction, 2026-10-21).

With AI off nothing asks a language model: a fixed phrase still runs its
command, and anything else — a question, a joke, a paraphrase of a
command — gets "I can't answer that one — I'm set up for home commands.
Say 'what can you do' to hear them." (the weather and the like: "I can't
look that up with AI turned off."). The cold-start line can't come up,
because nothing asks for a model.

With AI on but a model not installed (never pulled, or deleted in
Settings), the room is never told "I'm waking up my language model": the
router model is skipped, and a question hears that the question-answering
model isn't installed and where to get it.

DB-free (the repositories route() touches are faked, as in
test_llm_cold_start.py), plus one ``requires_db`` turn end to end.
"""

from __future__ import annotations

import pytest

import domovoi.router as router_mod
from domovoi import ai_features
from domovoi.config import settings
from domovoi.models import Context, Intent
from domovoi.router import (
    _COLD_START_TEXT,
    _LLM_UNREACHABLE_TEXT,
    _QA_MODEL_MISSING_TEXT,
    _ready_for,
    route,
)
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_router import _FakeSessionRepo, _RecordingLogRepo


class _NoModel:
    """A client every model call of which fails the test."""

    def __getattr__(self, name):
        raise AssertionError(f"asked the language model ({name}) with AI off")


@pytest.fixture
def offline_router(monkeypatch):
    rows: list[dict] = []

    class _Repo(_RecordingLogRepo):
        pass

    _Repo.rows = rows
    monkeypatch.setattr(router_mod, "SessionRepository", _FakeSessionRepo)
    monkeypatch.setattr(router_mod, "IntentLogRepository", _Repo)
    monkeypatch.setattr(router_mod, "ConversationLogRepository", _Repo)
    monkeypatch.setattr(router_mod, "get_ollama_client", lambda: _NoModel())
    return rows


async def _say(text: str, *, online: bool = True, spoken: list | None = None):
    async def speak(t):
        if spoken is not None:
            spoken.append(t)

    return await route(
        Intent(transcript=text, room_id="kitchen"),
        Context(room_id="kitchen", online=online, speak_interim=speak),
        None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("said", [
    "what is the capital of mongolia",
    "tell me a joke",
    "how many ounces are in one hundred grams",
    "could you please start a five minute countdown for me",
    "put on something from the hearth cats",
    "why is the sky blue?",
])
async def test_anything_no_command_takes_gets_the_home_commands_line(offline_router, said) -> None:
    assert router_mod.plan_route(said) is None
    spoken: list[str] = []
    with ai_features.override(False):
        r = await _say(said, spoken=spoken)
    assert r.text == ai_features.OFF_LINE
    assert r.text == (
        "I can't answer that one — I'm set up for home commands. "
        "Say 'what can you do' to hear them."
    )
    assert r.matched_path == "qa" and r.matched_handler is None
    assert r.qa_stream is None and not r.expect_followup
    # Never the outage line, never "waking up".
    assert spoken == [] and _COLD_START_TEXT not in r.text and r.text != _LLM_UNREACHABLE_TEXT
    # Still recorded like every turn (intents_log + conversation_log).
    assert len(offline_router) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("online", [True, False])
async def test_the_weather_and_the_news_of_the_day_get_the_lookup_line(offline_router, online) -> None:
    with ai_features.override(False):
        r = await _say("what's the weather going to be like tomorrow", online=online)
    assert r.text == ai_features.OFF_LOOKUP_LINE == "I can't look that up with AI turned off."


@pytest.mark.asyncio
async def test_a_fixed_phrase_still_runs_its_command(offline_router) -> None:
    with ai_features.override(False):
        r = await _say("what can you do")
    assert r.matched_handler == "help" and r.matched_path == "fast"
    # "what can you do" never offers questions or a fact-check.
    assert "are you sure" not in r.text.lower()


@pytest.mark.asyncio
async def test_are_you_sure_says_it_cant_look_that_up(offline_router) -> None:
    with ai_features.override(False):
        r = await _say("are you sure")
    assert r.matched_handler == "double_check"
    assert r.text == ai_features.OFF_LOOKUP_LINE


@pytest.mark.asyncio
async def test_with_ai_on_the_router_is_unchanged(offline_router, monkeypatch) -> None:
    from domovoi.tests.test_router import _DeadOllamaClient

    monkeypatch.setattr(router_mod, "get_ollama_client", lambda: _DeadOllamaClient())
    with ai_features.override(True):
        r = await _say("what is the capital of mongolia")
    assert r.text == _LLM_UNREACHABLE_TEXT


# ─── a model that was never downloaded ────────────────────────────────────


class _MissingClient:
    """Ollama up, nothing loaded, and the models not installed at all."""

    def __init__(self, missing=("tool", "qa")) -> None:
        self.missing = set(missing)
        self.routed = 0

    async def cold_models(self, *roles):
        return ["qwen3:8b" if r == "tool" else "llama3.2:3b" for r in roles]

    async def missing_models(self, *roles):
        return [("qwen3:8b" if r == "tool" else "llama3.2:3b") for r in roles if r in self.missing]

    def for_cold_start(self):
        return self

    async def route(self, transcript, tool_schemas):
        self.routed += 1
        return None

    async def qa_with_uncertainty(self, transcript, history=None, profile_prefix=None):
        from domovoi.clients.ollama import QAWithUncertainty

        return QAWithUncertainty(answer="Ulaanbaatar.", needs_verification=False)


@pytest.mark.asyncio
async def test_a_missing_model_is_never_woken(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ollama_cold_start_notice", True)
    spoken: list[str] = []

    async def speak(t):
        spoken.append(t)

    ctx = Context(room_id="office", speak_interim=speak)
    assert await _ready_for(_MissingClient(), ctx, "qa") is None
    assert spoken == []
    # Installed but not loaded: still the cold start, as before.
    present = _MissingClient(missing=())
    assert await _ready_for(present, ctx, "qa") is present
    assert spoken == [_COLD_START_TEXT]


@pytest.mark.asyncio
async def test_a_question_hears_that_the_model_isnt_installed(offline_router, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ollama_cold_start_notice", True)
    client = _MissingClient()
    monkeypatch.setattr(router_mod, "get_ollama_client", lambda: client)
    spoken: list[str] = []
    with ai_features.override(True):
        r = await _say("what is the capital of mongolia", spoken=spoken)
    assert r.text == _QA_MODEL_MISSING_TEXT
    assert "isn't installed" in r.text and "Settings, Models" in r.text
    assert spoken == []                 # no "waking up" first
    assert client.routed == 0           # the router model wasn't asked either
    assert r.matched_path == "error"


@pytest.mark.asyncio
async def test_only_the_router_model_missing_goes_straight_to_qa(offline_router, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ollama_cold_start_notice", True)
    client = _MissingClient(missing=("tool",))
    monkeypatch.setattr(router_mod, "get_ollama_client", lambda: client)
    spoken: list[str] = []
    with ai_features.override(True):
        r = await _say("what is the capital of mongolia", spoken=spoken)
    assert r.text == "Ulaanbaatar."
    assert client.routed == 0
    # The Q&A model was only cold: that one is said, once.
    assert spoken == [_COLD_START_TEXT]


@pytest.mark.asyncio
async def test_missing_models_reads_ollamas_list(monkeypatch) -> None:
    from domovoi.clients import ollama as ollama_mod
    from domovoi.tests.test_llm_cold_start import _Chat, _client

    async def tags(base_url=None, timeout=5.0):
        return [{"name": "llama3.2:3b"}]

    monkeypatch.setattr(ollama_mod, "tags_or_none", tags)
    c = _client(_Chat(), url="http://127.0.0.1:11434")
    assert await c.missing_models("qa") == []
    assert await c.missing_models("tool", "qa") == ["qwen3:8b"]

    async def down(base_url=None, timeout=5.0):
        return None

    monkeypatch.setattr(ollama_mod, "tags_or_none", down)
    assert await c.missing_models("qa") is None
    assert await _client(_Chat()).missing_models("qa") == []


# ─── end to end, on the test database ─────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_a_timer_and_a_question_with_ai_off(db_session, monkeypatch) -> None:
    monkeypatch.setattr(router_mod, "get_ollama_client", lambda: _NoModel())
    with ai_features.override(False):
        timer = await route(Intent(transcript="set a timer for 5 minutes", room_id="kitchen"),
                            Context(room_id="kitchen", online=True), db_session)
        question = await route(Intent(transcript="tell me a story about a dragon", room_id="kitchen"),
                               Context(room_id="kitchen", online=True), db_session)
    await db_session.commit()
    assert timer.matched_handler == "timer" and timer.matched_path == "fast"
    assert question.text == ai_features.OFF_LINE
