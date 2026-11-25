"""One source of truth for weather (N3): a weather plugin that answers wins;
when its handler fails (raises, or gives no answer) core weather steps in if
the internet answer allows, else an honest short failure line. The voice turn
and double_check's web answer decide through the same helper
(domovoi/weather.py)."""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from domovoi import egress, home_location, weather
from domovoi.capabilities import CAPABILITIES, WEATHER_PROVIDER
from domovoi.clients.searxng import SearchOutcome, SearchResult
from domovoi.handlers import HANDLER_BY_NAME, register_handler, unregister_handler
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response
from domovoi.router import route
from domovoi.tests.conftest import requires_db

SLUG = "wx-fallback-test"
CORE_SAYS = "It's 70 and sunny in Springfield."
PLUGIN_SAYS = "The plugin says 12 degrees and drizzle."


class _FakeSearx:
    def __init__(self, spoken):
        self.spoken = spoken
        self.places: list[str] = []
        self.searched: list[str] = []

    async def weather(self, place):
        self.places.append(place)
        return self.spoken

    async def search_detailed(self, query, max_results=5):
        self.searched.append(query)
        return SearchOutcome(
            [SearchResult(title="t", url="https://example.com/x", content="c")], "ok"
        )


class _Ollama:
    async def qa(self, transcript, system_prompt=None, history=None):
        return "ANSWER: From general search.\nSOURCE: NONE"


class _WeatherPlugin(Handler):
    """A weather plugin's handler: ``mode`` is "answer", "raise" or "none"."""

    name = "wx_fallback_test"
    priority_band = 280
    display = HandlerDisplay(label="Weather", tone="neutral")
    tool_schema = {
        "name": "wx_fallback_test",
        "description": "The weather.",
        "parameters": {"type": "object", "properties": {}},
    }

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0
        self.fast_paths = [FastPath(re.compile(r"^.*\bweather\b.*$"), _WeatherPlugin._fp)]

    async def _fp(self, m, ctx, session):
        self.calls += 1
        if self.mode == "raise":
            raise RuntimeError("weather service down")
        if self.mode == "none":
            return None
        return Response(text=PLUGIN_SAYS, session_id=ctx.session_id)

    async def execute(self, intent, ctx, session):  # pragma: no cover
        return None


class _plugin:
    """Registers a weather plugin (handler + capability) for the block."""

    def __init__(self, mode: str) -> None:
        self.handler = _WeatherPlugin(mode)

    def __enter__(self) -> _WeatherPlugin:
        self.handler.plugin_slug = SLUG
        register_handler(self.handler)
        CAPABILITIES.register(WEATHER_PROVIDER, object(), slug=SLUG)
        return self.handler

    def __exit__(self, *a) -> None:
        CAPABILITIES.unregister(WEATHER_PROVIDER, slug=SLUG)
        unregister_handler(self.handler)


def _patched(fake):
    return (
        patch("domovoi.weather.get_searxng_client", lambda: fake),
        patch("domovoi.handlers.double_check.get_searxng_client", lambda: fake),
        patch("domovoi.handlers.double_check.get_ollama_client", lambda: _Ollama()),
    )


async def _home(db_session) -> None:
    await home_location.save(db_session, {"name": "Springfield"})
    await db_session.commit()


async def _ask(db_session, text="what's the weather for today", *, online=True):
    ctx = Context(room_id="kitchen", online=online)
    response = await route(Intent(transcript=text, room_id="kitchen"), ctx, db_session)
    await db_session.commit()
    return response


# -- the voice turn ---------------------------------------------------------


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["raise", "none"])
async def test_plugin_fails_core_answers_when_internet_allowed(db_session, mode) -> None:
    await _home(db_session)
    fake = _FakeSearx(CORE_SAYS)
    a, b, c = _patched(fake)
    with a, b, c, egress.override_policy("always"), _plugin(mode) as plugin:
        response = await _ask(db_session)
    assert response.text == CORE_SAYS
    assert response.matched_path == "auto_search"
    assert fake.places == ["Springfield"]
    assert plugin.calls == 1, "the failed plugin is not asked twice in one turn"


@requires_db
@pytest.mark.asyncio
async def test_plugin_fails_sometimes_gets_cores_offer(db_session) -> None:
    """Core steps in with its own gates: "Sometimes" still asks first."""
    fake = _FakeSearx("never spoken")
    a, b, c = _patched(fake)
    with a, b, c, egress.override_policy("sometimes"), _plugin("raise"):
        response = await _ask(db_session)
    assert response.matched_path == "volatile_offer"
    assert "want me to check" in response.text.lower()
    assert fake.places == []


@requires_db
@pytest.mark.asyncio
async def test_plugin_fails_internet_off_gives_honest_failure(db_session) -> None:
    fake = _FakeSearx("never spoken")
    a, b, c = _patched(fake)
    with a, b, c, egress.override_policy("never"), _plugin("raise"):
        response = await _ask(db_session)
    assert response.text == weather.FAILED_LINE
    assert SLUG not in response.text and "plugin" not in response.text.lower()
    assert fake.places == [] and fake.searched == []


@requires_db
@pytest.mark.asyncio
async def test_plugin_fails_offline_gives_honest_failure(db_session) -> None:
    fake = _FakeSearx("never spoken")
    a, b, c = _patched(fake)
    with a, b, c, egress.override_policy("always"), _plugin("none"):
        response = await _ask(db_session, online=False)
    assert response.text == weather.FAILED_LINE
    assert fake.places == []


@requires_db
@pytest.mark.asyncio
async def test_plugin_answers_wins_in_the_voice_turn(db_session) -> None:
    await _home(db_session)
    fake = _FakeSearx("never spoken")
    a, b, c = _patched(fake)
    with a, b, c, egress.override_policy("always"), _plugin("answer") as plugin:
        response = await _ask(db_session)
    assert response.text == PLUGIN_SAYS
    assert response.matched_handler == plugin.name
    assert fake.places == [] and fake.searched == []


# -- double_check's web answer: the same source -----------------------------


async def _web_answer(db_session, question="what's the weather for today", **kw):
    dc = HANDLER_BY_NAME["double_check"]
    return await dc.answer_question_from_web(
        question, Context(room_id="kitchen", online=True), db_session, **kw
    )


@requires_db
@pytest.mark.asyncio
async def test_double_check_uses_the_plugin_that_answers(db_session) -> None:
    await _home(db_session)
    fake = _FakeSearx("never spoken")
    a, b, c = _patched(fake)
    with a, b, c, egress.override_policy("always"), _plugin("answer") as plugin:
        response = await _web_answer(db_session)
    assert response.text == PLUGIN_SAYS
    assert plugin.calls == 1
    assert fake.places == [] and fake.searched == []


@requires_db
@pytest.mark.asyncio
async def test_double_check_falls_back_to_core_when_the_plugin_fails(db_session) -> None:
    await _home(db_session)
    fake = _FakeSearx(CORE_SAYS)
    a, b, c = _patched(fake)
    with a, b, c, egress.override_policy("sometimes"), _plugin("raise") as plugin:
        response = await _web_answer(db_session)
    assert response.text == CORE_SAYS
    assert plugin.calls == 1
    assert fake.places == ["Springfield"]


@requires_db
@pytest.mark.asyncio
async def test_double_check_skips_a_plugin_that_already_failed(db_session) -> None:
    await _home(db_session)
    fake = _FakeSearx(CORE_SAYS)
    a, b, c = _patched(fake)
    with a, b, c, egress.override_policy("always"), _plugin("raise") as plugin:
        response = await _web_answer(db_session, weather_plugin_failed=True)
    assert response.text == CORE_SAYS
    assert plugin.calls == 0


@requires_db
@pytest.mark.asyncio
async def test_double_check_plugin_fails_internet_off_is_honest(db_session) -> None:
    fake = _FakeSearx("never spoken")
    a, b, c = _patched(fake)
    with a, b, c, egress.override_policy("never"), _plugin("none"):
        response = await _web_answer(db_session)
    assert response.text == weather.FAILED_LINE
    assert fake.places == []


# -- no plugin: unchanged ---------------------------------------------------


@requires_db
@pytest.mark.asyncio
async def test_no_plugin_answer_for_is_cores_lookup(db_session) -> None:
    assert not weather.plugin_handles_weather()
    await _home(db_session)
    fake = _FakeSearx(CORE_SAYS)
    with patch("domovoi.weather.get_searxng_client", lambda: fake), egress.override_policy("never"):
        said = await weather.answer_for("what's the weather", Context(online=False), db_session)
    # Exactly answer_weather: no internet gate of its own, no failure line.
    assert said == CORE_SAYS and fake.places == ["Springfield"]


@requires_db
@pytest.mark.asyncio
async def test_no_plugin_voice_turn_unchanged(db_session) -> None:
    await _home(db_session)
    fake = _FakeSearx(CORE_SAYS)
    a, b, c = _patched(fake)
    with a, b, c, egress.override_policy("always"):
        response = await _ask(db_session)
    assert response.text == CORE_SAYS and response.matched_path == "auto_search"


# -- narrow: only weather, only a weather plugin ----------------------------


@pytest.mark.asyncio
async def test_plugin_attempt_lets_other_raises_through() -> None:
    async def boom():
        raise RuntimeError("x")

    with _plugin("answer") as plugin:
        # Not a weather question: the raise is not core weather's to cover.
        with pytest.raises(RuntimeError):
            await weather.plugin_attempt(plugin, boom(), "play some jazz")
    # Not a registered weather plugin's handler: untouched.
    other = _WeatherPlugin("raise")
    with pytest.raises(RuntimeError):
        await weather.plugin_attempt(other, boom(), "what's the weather")


@pytest.mark.asyncio
async def test_plugin_attempt_passes_an_answer_through() -> None:
    async def ok():
        return Response(text=PLUGIN_SAYS)

    with _plugin("answer") as plugin:
        response, failed = await weather.plugin_attempt(plugin, ok(), "what's the weather")
    assert response is not None and response.text == PLUGIN_SAYS and failed is False
