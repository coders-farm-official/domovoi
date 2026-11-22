"""Weather answers: SearXNG's weather category, the household place, the
fallback to general search, the "always" shortcut, and the plugin hand-off."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from domovoi import egress
from domovoi.capabilities import CAPABILITIES, WEATHER_PROVIDER
from domovoi.clients import searxng
from domovoi.clients.searxng import RealSearxNGClient, SearchOutcome, SearchResult
from domovoi.config import settings
from domovoi.models import Context, Intent
from domovoi.router import route
from domovoi.tests.conftest import requires_db

ANSWER = {
    "answers": [
        {
            "engine": "openmeteo",
            "current": {
                "location": {"name": "Springfield"},
                "temperature": {"val": 71.6, "unit": "\u00b0F"},
                "condition": "Partly cloudy",
            },
        }
    ]
}


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _fake_httpx(payload, seen):
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            seen.append(params)
            return _Resp(payload)

    return _Client


@pytest.mark.asyncio
async def test_weather_query_uses_weather_category_and_place_and_speaks_answer() -> None:
    seen: list = []
    with patch.object(httpx, "AsyncClient", _fake_httpx(ANSWER, seen)):
        spoken = await RealSearxNGClient("http://searxng.test").weather("Springfield")
    assert seen[0]["engines"] == "openmeteo" and "categories" not in seen[0]
    assert seen[0]["q"] == "Springfield"
    assert spoken == "Right now in Springfield it's 72 degrees Fahrenheit and partly cloudy."


@pytest.mark.asyncio
async def test_empty_weather_result_is_none() -> None:
    with patch.object(httpx, "AsyncClient", _fake_httpx({"answers": [], "results": []}, [])):
        assert await RealSearxNGClient("http://searxng.test").weather("Springfield") is None


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


def _patched(fake):
    return (
        patch("domovoi.weather.get_searxng_client", lambda: fake),
        patch("domovoi.handlers.double_check.get_searxng_client", lambda: fake),
        patch("domovoi.handlers.double_check.get_ollama_client", lambda: _Ollama()),
    )


async def _ask(db_session, text="what's the weather for today"):
    ctx = Context(room_id="kitchen", online=True)
    response = await route(Intent(transcript=text, room_id="kitchen"), ctx, db_session)
    await db_session.commit()
    return response


@requires_db
@pytest.mark.asyncio
async def test_always_skips_offer_and_speaks_weather(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "news_location", "Springfield")
    fake = _FakeSearx("It's 70 and sunny in Springfield.")
    a, b, c = _patched(fake)
    with a, b, c, egress.override_policy("always"):
        response = await _ask(db_session)
    assert "want me to check" not in response.text.lower()
    assert response.text == "It's 70 and sunny in Springfield."
    assert fake.places == ["Springfield"]
    assert fake.searched == []


@requires_db
@pytest.mark.asyncio
async def test_empty_weather_falls_back_to_general_search(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "news_location", "")
    fake = _FakeSearx(None)
    a, b, c = _patched(fake)
    with a, b, c, egress.override_policy("always"):
        response = await _ask(db_session)
    assert fake.searched, "general search ran"
    assert "general search" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_ask_policy_still_offers(db_session) -> None:
    fake = _FakeSearx("never spoken")
    a, b, c = _patched(fake)
    with a, b, c, egress.override_policy("sometimes"):
        response = await _ask(db_session)
    assert response.matched_path == "volatile_offer"
    assert "want me to check" in response.text.lower()
    assert fake.places == []


@requires_db
@pytest.mark.asyncio
async def test_weather_plugin_capability_steps_core_aside(db_session) -> None:
    fake = _FakeSearx("never spoken")
    a, b, c = _patched(fake)
    CAPABILITIES.register(WEATHER_PROVIDER, object(), slug="wx-test")
    try:
        with a, b, c, egress.override_policy("always"):
            response = await _ask(db_session)
    finally:
        CAPABILITIES.unregister(WEATHER_PROVIDER, slug="wx-test")
    assert fake.places == [] and fake.searched == []
    assert response.matched_path not in ("volatile_offer", "auto_search")


def test_a_us_place_hears_fahrenheit_and_others_celsius():
    """The weather engines answer in Celsius everywhere (seen on the pinned
    SearXNG, 2026-11-22); a US household expects Fahrenheit."""
    from domovoi.clients.searxng import spoken_weather

    def payload(cc):
        return {"answers": [{"current": {
            "location": {"name": "Springfield", "country_code": cc},
            "temperature": {"val": 7.0, "unit": "°C"},
            "condition": "Light rain"}}]}

    assert spoken_weather(payload("US")) == (
        "Right now in Springfield it's 45 degrees Fahrenheit and light rain.")
    assert spoken_weather(payload("GB")) == (
        "Right now in Springfield it's 7 degrees Celsius and light rain.")


def test_a_plugin_can_read_the_household_place_and_take_weather_over():
    """SDK 1.12: the weather plugin (1.5.0) reads the same place and
    registers the "weather" capability to take weather questions over."""
    from domovoi.capabilities import WEATHER_PROVIDER
    from domovoi.sdk import API_VERSION
    from domovoi.sdk.coreconfig import CORE_CONFIG_WHITELIST

    assert "news_location" in CORE_CONFIG_WHITELIST
    assert WEATHER_PROVIDER == "weather"
    assert tuple(int(p) for p in API_VERSION.split(".")[:2]) >= (1, 12)
