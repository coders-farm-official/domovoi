"""Answers the house's settings decide, before any model (router._settings_answer).

"Restaurants near me?" on a house set to never use the internet got the
model's hedge and "try a mapping service" (M10, case 5.9): it now hears
that it needs the internet and where an admin changes that. The weather
with no Home location is in test_weather.py (M9).

DB-free: the repositories route() touches are faked, and every model call
fails the test.
"""

from __future__ import annotations

import pytest

import domovoi.router as router_mod
from domovoi import egress, off_answers
from domovoi.models import Context, Intent
from domovoi.router import route
from domovoi.tests.test_router import _FakeSessionRepo, _RecordingLogRepo
from domovoi.uncertainty import is_local_lookup


class _NoModel:
    def __getattr__(self, name):
        raise AssertionError(f"asked the language model ({name})")


@pytest.fixture
def no_model_router(monkeypatch):
    monkeypatch.setattr(router_mod, "SessionRepository", _FakeSessionRepo)
    monkeypatch.setattr(router_mod, "IntentLogRepository", _RecordingLogRepo)
    monkeypatch.setattr(router_mod, "ConversationLogRepository", _RecordingLogRepo)
    monkeypatch.setattr(router_mod, "get_ollama_client", lambda: _NoModel())


async def _say(text: str, *, admin: bool = False):
    return await route(
        Intent(transcript=text, room_id="kitchen"),
        Context(room_id="kitchen", online=True, proven_admin=admin),
        None,
    )


LINE = "I'd need the internet for that, and this house is set to never use it."


@pytest.mark.parametrize("said", [
    "restaurants near me", "Restaurants near me?", "what's a good pizza place nearby",
    "is the pharmacy open now", "find a coffee shop near here", "where can I get takeout in my area",
    "are there any good bars around here",
])
def test_local_lookups(said: str) -> None:
    assert is_local_lookup(said.lower().rstrip("?"))


@pytest.mark.parametrize("said", [
    "add shops to my list", "play bars by the band", "tell me a joke", "what's the capital of france",
    "the local variable is wrong",
])
def test_not_local_lookups(said: str) -> None:
    assert not is_local_lookup(said)


@pytest.mark.asyncio
@pytest.mark.parametrize("admin", [False, True])
@pytest.mark.parametrize("said", ["Restaurants near me?", "is the pharmacy open now"])
async def test_internet_never_says_so_before_any_model(no_model_router, said, admin) -> None:
    with egress.override_policy("never"):
        r = await _say(said, admin=admin)
    assert r.text == off_answers.with_turn_on(LINE, off_answers.INTERNET, admin=admin)
    assert r.matched_path == "qa" and r.matched_handler is None
    assert "mapping" not in r.text and "don't think" not in r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["always", "sometimes"])
async def test_with_the_internet_allowed_it_goes_on_as_before(no_model_router, policy) -> None:
    """Allowed: no fixed line, the turn goes on to the models (here, one
    that fails, which the router reports as a failed turn)."""
    with egress.override_policy(policy):
        r = await _say("restaurants near me")
    assert LINE not in r.text
    assert r.matched_path != "qa" or r.matched_handler is not None or r.failure is not None
