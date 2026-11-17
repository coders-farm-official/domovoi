"""The cold-start notice: said only when the model is really slow to come,
and listed in the room's history when it is (Mac test sitting, 2026-11-16,
S2-16).

The room said "Just a moment, I'm waking up my language model." and then
answered almost at once (the warm-up after a restart was nearly done), and
the drawer's history showed only the answer. Pinned here, DB-free:

* a model that comes up within the grace gets no notice, and is asked
  about every half second meanwhile;
* one still cold after it gets the notice, as before;
* Ollama going quiet during the grace counts as cold (the notice is said);
* what the room said first goes in the turn's conversation_log text, before
  the reply; the model's own history (record_exchange) keeps the reply alone.
"""

from __future__ import annotations

import pytest

import domovoi.router as router_mod
from domovoi.config import settings
from domovoi.models import Context, Intent
from domovoi.router import _COLD_START_TEXT, _ready_for, heard_text, route


class _Client:
    def __init__(self, answers):
        self.answers = list(answers)
        self.checks = 0
        self.twin = object()

    async def cold_models(self, *roles):
        self.checks += 1
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, BaseException):
            raise a
        return a

    async def missing_models(self, *roles):
        return []

    def for_cold_start(self):
        return self.twin


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(settings, "ollama_cold_start_notice", True)
    monkeypatch.setattr(router_mod, "COLD_NOTICE_GRACE_SEC", 0.06)
    monkeypatch.setattr(router_mod, "COLD_NOTICE_POLL_SEC", 0.02)


def _speaker():
    spoken: list[str] = []

    async def speak(text):
        spoken.append(text)
        return True

    return spoken, speak


async def test_a_model_that_comes_up_within_the_grace_gets_no_notice():
    spoken, speak = _speaker()
    client = _Client([["qwen3:8b"], ["qwen3:8b"], []])
    ctx = Context(room_id="office", speak_interim=speak)
    assert await _ready_for(client, ctx, "qa") is client.twin
    assert spoken == [] and ctx.interim_said == []
    assert client.checks == 3


async def test_a_model_still_cold_after_it_gets_the_notice_and_it_is_listed():
    spoken, speak = _speaker()
    client = _Client([["qwen3:8b"]])
    ctx = Context(room_id="office", speak_interim=speak)
    assert await _ready_for(client, ctx, "qa") is client.twin
    assert spoken == [_COLD_START_TEXT]
    assert ctx.interim_said == [_COLD_START_TEXT]


async def test_ollama_going_quiet_during_the_grace_is_cold():
    spoken, speak = _speaker()
    client = _Client([["qwen3:8b"], None])
    await _ready_for(client, Context(room_id="office", speak_interim=speak), "qa")
    assert spoken == [_COLD_START_TEXT]


async def test_a_notice_that_was_not_said_is_not_listed():
    async def mute(text):
        return False

    ctx = Context(room_id="office", speak_interim=mute)
    await _ready_for(_Client([["qwen3:8b"]]), ctx, "qa")
    assert ctx.interim_said == []


def test_heard_text():
    ctx = Context(room_id="office")
    assert heard_text(ctx, "Jane Austen.") == "Jane Austen."
    ctx.interim_said.append(_COLD_START_TEXT)
    assert heard_text(ctx, "Jane Austen.") == f"{_COLD_START_TEXT} Jane Austen."
    # A shallow copy (the router's) shares what was said.
    assert ctx.model_copy(update={"room_id": "x"}).interim_said is ctx.interim_said


async def test_the_history_row_carries_the_notice_and_the_model_history_does_not(monkeypatch):
    from domovoi.tests.test_llm_cold_start import _ColdClient
    from domovoi.tests.test_router import _FakeSessionRepo, _RecordingLogRepo

    rows: list[dict] = []
    exchanges: list[tuple] = []

    class _Repo(_RecordingLogRepo):
        pass

    class _Sessions(_FakeSessionRepo):
        async def record_exchange(self, session_id, user, assistant, cap):
            exchanges.append((user, assistant))

    _Repo.rows = rows
    monkeypatch.setattr(router_mod, "SessionRepository", _Sessions)
    monkeypatch.setattr(router_mod, "IntentLogRepository", _Repo)
    monkeypatch.setattr(router_mod, "ConversationLogRepository", _Repo)
    monkeypatch.setattr(router_mod, "get_ollama_client", lambda: _ColdClient())
    spoken, speak = _speaker()

    response = await route(
        Intent(transcript="what is the capital of mongolia", room_id="office"),
        Context(room_id="office", online=True, speak_interim=speak),
        None,
    )
    assert response.text == "Jane Austen."
    turn = [r for r in rows if "assistant_text" in r][-1]
    assert turn["assistant_text"].startswith(_COLD_START_TEXT)
    assert turn["assistant_text"].endswith("Jane Austen.")
    assert exchanges and exchanges[-1][1] == "Jane Austen."
