"""HelpHandler: "what can you do" / "what can I say" / "help", answered
from the live handler registry instead of the answer model (which made
abilities up: manual-content finding 4.2, 2026-10-17)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from domovoi.handlers import HANDLER_BY_NAME, register_handler, unregister_handler
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.handlers.help import HelpHandler, HouseState, capabilities_summary
from domovoi.models import Context
from domovoi.tests.conftest import fast_path_winner, requires_db


@pytest.mark.parametrize(
    "said",
    [
        "what can you do", "What can you do?", "what else can you do",
        "what all can you do for me", "what can I say", "what can I ask you",
        "what should I say", "what are you able to do", "what are you capable of",
        "what do you do", "what can domovoi do", "what can you help me with",
        "tell me what you can do", "what are your features", "list your commands",
        "how do I use you", "help", "Help.", "help me", "help please",
        "can you help me", "please help",
    ],
)
def test_the_questions_about_the_assistant_are_help(said: str) -> None:
    assert fast_path_winner(said) == "help"


@pytest.mark.parametrize(
    "said",
    [
        # a real question that starts the same way stays a question
        "what can you do about the noise",
        "what can you do about a leaky tap",
        "what can I say to my boss",
        "what can I say instead of sorry",
        "help me with my homework",
        "help me find my phone",
        "what do you do when you're bored",
        "can you help me write a poem",
    ],
)
def test_real_questions_are_not_poached(said: str) -> None:
    assert fast_path_winner(said) != "help"


def test_the_help_phrases_poach_nothing() -> None:
    """Without the help handler, none of its phrases matched any fast path
    (they all went to the answer model), so its band can't steal a turn."""
    from domovoi.handlers import HANDLERS
    from domovoi.handlers.base import as_fast_path
    from domovoi.router import normalize_transcript, strip_leading_filler

    for said in ("what can you do", "what can i say", "help", "help me", "what do you do",
                 "list your commands", "how do i use you", "what can you help me with"):
        t = strip_leading_filler(normalize_transcript(said))
        others = [
            h.name for h in HANDLERS if h.name != "help"
            for fp in h.fast_paths if as_fast_path(fp).pattern.match(t)
        ]
        assert others == [], (said, others)


def test_the_tool_is_never_offered() -> None:
    from domovoi.router import offered_tool_schemas

    for t in ("what can you do about the noise", "", "help me with my homework"):
        assert "help" not in [s["name"] for s in offered_tool_schemas(t)]


def test_the_answer_is_the_live_feature_list() -> None:
    text_ = capabilities_summary(HouseState(internet=True, ai=True, home=False))
    assert text_.startswith("Here are a few things I can do.")
    for name in ("timer", "reminder", "music", "calendar", "lists", "news"):
        h = HANDLER_BY_NAME[name]
        assert f'{h.display.label}: try "{h.example_phrases[0]}".' in text_, name
    assert text_.endswith("The rest is in the manual, on the dashboard.")
    # nothing the server can't do
    for made_up in ("groceries", "smart home", "lights"):
        assert made_up not in text_.lower()


class _PluginHandler(Handler):
    name = "radio_test"
    priority_band = 280
    display = HandlerDisplay(label="Radio", tone="media")
    tool_schema = {
        "name": "radio_test",
        "description": "Stream a station. Example: 'stream the jazz station'.",
        "parameters": {"type": "object", "properties": {}},
    }

    def __init__(self) -> None:
        self.fast_paths: list[FastPath] = []

    async def execute(self, intent, ctx, session):  # pragma: no cover
        return None


def test_installed_plugins_are_included() -> None:
    plugin = _PluginHandler()
    plugin.plugin_slug = "radio-test"
    register_handler(plugin)
    try:
        assert 'Radio: try "stream the jazz station".' in capabilities_summary()
    finally:
        unregister_handler(plugin)
    assert "stream the jazz station" not in capabilities_summary()


def test_a_disabled_chat_mode_is_never_suggested(monkeypatch) -> None:
    from domovoi.config import settings

    monkeypatch.setattr(settings, "chat_mode_enabled", False)
    assert HANDLER_BY_NAME["chat_mode"].example_phrases == ()
    assert "let's have a chat" not in capabilities_summary()
    monkeypatch.setattr(settings, "chat_mode_enabled", True)
    assert HANDLER_BY_NAME["chat_mode"].example_phrases == ("let's have a chat",)


@requires_db
@pytest.mark.asyncio
async def test_route_answers_it_on_the_fast_path_with_no_model(db_session, monkeypatch) -> None:
    from domovoi import router
    from domovoi.models import Intent

    class _NoModel:
        async def route(self, *a, **kw):  # pragma: no cover - must not run
            raise AssertionError("the tool model was asked")

        async def qa_with_uncertainty(self, *a, **kw):  # pragma: no cover
            raise AssertionError("the answer model was asked")

    monkeypatch.setattr(router, "get_ollama_client", lambda: _NoModel())
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=False)
    r = await router.route(Intent(transcript="What can you do?", room_id="kitchen"), ctx, db_session)
    assert (r.matched_handler, r.matched_path) == ("help", "fast")
    # offline right now: no news offered
    assert r.text == capabilities_summary(HouseState(internet=False, ai=True, home=False))
    assert "News:" not in r.text
    assert isinstance(HANDLER_BY_NAME["help"], HelpHandler)


# ─── N13: only what this house can do right now ──────────────────────────


def _labels(state: HouseState) -> str:
    return capabilities_summary(state)


@pytest.fixture
def no_weather_plugin(monkeypatch):
    from domovoi import weather

    monkeypatch.setattr(weather, "plugin_handles_weather", lambda: False)


def test_internet_no_leaves_out_news_and_weather(no_weather_plugin) -> None:
    text_ = _labels(HouseState(internet=False, ai=True, home=True))
    assert "News:" not in text_
    assert "Weather:" not in text_
    for label in ("Timers:", "Lists:", "Music:"):
        assert label in text_


def test_ai_off_and_internet_no_still_offers_the_house_commands(no_weather_plugin) -> None:
    """Capability case 12.1: still useful, and nothing it can't do."""
    text_ = _labels(HouseState(internet=False, ai=False, home=False))
    assert text_.startswith("Here are a few things I can do.")
    for label in ("Timers:", "Reminders:", "Music:", "Lists:"):
        assert label in text_
    for gone in ("News:", "Weather:", "chat"):
        assert gone not in text_


def test_weather_needs_a_home_location(no_weather_plugin) -> None:
    assert "Weather:" not in _labels(HouseState(internet=True, ai=True, home=False))
    assert """Weather: try "what's the weather".""" in _labels(
        HouseState(internet=True, ai=True, home=True))


def test_weather_needs_ai(no_weather_plugin) -> None:
    """With AI off a weather question gets the lookup refusal (router)."""
    text_ = _labels(HouseState(internet=True, ai=False, home=True))
    assert "Weather:" not in text_
    assert "News:" in text_      # headlines need no model


def test_a_weather_plugin_takes_over_the_weather_area(monkeypatch) -> None:
    from domovoi import weather

    monkeypatch.setattr(weather, "plugin_handles_weather", lambda: True)
    assert "Weather:" not in _labels(HouseState(internet=True, ai=True, home=True))


def test_internet_answer_no_reads_from_the_setting(no_weather_plugin) -> None:
    from domovoi import ai_features, egress

    with egress.override_policy("never"), ai_features.override(True):
        assert "News:" not in capabilities_summary()
    with egress.override_policy("always"), ai_features.override(True):
        assert "News:" in capabilities_summary()


class _OnlinePlugin(_PluginHandler):
    name = "fetch_test"
    requires_network = "yes"
    display = HandlerDisplay(label="Downloads", tone="media")
    tool_schema = {
        "name": "fetch_test",
        "description": "Fetch a video. Example: 'download the cat video'.",
        "parameters": {"type": "object", "properties": {}},
    }

    async def fallback_offline(self, intent, ctx, session):  # pragma: no cover
        return None


def test_an_online_only_plugin_is_left_out_with_no_internet() -> None:
    """Capability case 12.2: plugins that need the internet aren't offered."""
    plugin = _OnlinePlugin()
    plugin.plugin_slug = "fetch-test"
    register_handler(plugin)
    try:
        assert "Downloads:" in _labels(HouseState(internet=True, ai=True, home=False))
        assert "Downloads:" not in _labels(HouseState(internet=False, ai=True, home=False))
    finally:
        unregister_handler(plugin)


@requires_db
@pytest.mark.asyncio
async def test_the_spoken_answer_reads_the_home_location(db_session, no_weather_plugin) -> None:
    from domovoi import ai_features, egress, home_location
    from domovoi.handlers.help import house_state

    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
    with egress.override_policy("always"), ai_features.override(True):
        await home_location.save(db_session, None)
        r = await HANDLER_BY_NAME["help"].execute(None, ctx, db_session)
        assert "Weather:" not in r.text
        await home_location.save(db_session, {"name": "Springfield"})
        assert (await house_state(ctx, db_session)).home is True
        r = await HANDLER_BY_NAME["help"].execute(None, ctx, db_session)
        assert "Weather:" in r.text
        assert "News:" in r.text
    with egress.override_policy("never"), ai_features.override(True):
        r = await HANDLER_BY_NAME["help"].execute(None, ctx, db_session)
        assert "News:" not in r.text and "Weather:" not in r.text
