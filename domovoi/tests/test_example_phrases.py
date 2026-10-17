"""Every core feature's example phrases (``Handler.example_phrases``).

``/v1/handlers`` used to read them only from a trailing "Example: '...'"
in a tool description; the router-bait fixes took those out of every core
description (example text there pulls other turns towards the tool), so
the user manual, Home's first-run hint and the live feature list showed no
phrase for any core feature (manual-content finding 4.1, 2026-10-17). They
are their own field now, kept out of what the routing model reads, and
each one is checked here to land on its handler by its fast path.
"""

from __future__ import annotations

import pytest

from domovoi.handlers import HANDLER_BY_NAME, HANDLERS
from domovoi.handlers.base import Handler, HandlerDisplay, example_phrases_of

# chat_mode lists its phrase only while chat mode is on (off by default).
_EVERY_CORE = sorted(h.name for h in HANDLERS if h.plugin_slug is None and h.name != "chat_mode")
_PHRASES = [
    (h.name, p) for h in HANDLERS if h.plugin_slug is None for p in example_phrases_of(h)
]


@pytest.mark.parametrize("name", _EVERY_CORE)
def test_every_core_feature_has_example_phrases(name: str) -> None:
    assert example_phrases_of(HANDLER_BY_NAME[name]), f"{name} lists no phrase"


@pytest.mark.parametrize(("name", "phrase"), _PHRASES, ids=[f"{n}:{p}" for n, p in _PHRASES])
def test_each_phrase_lands_on_its_handler_by_its_fast_path(name: str, phrase: str) -> None:
    """What the manual tells people to say does what it says, with no
    model in the way. (music_alias's phrase matches its fast path, which
    declines at run time when the library lacks the band.)"""
    from domovoi.router import plan_route

    plan = plan_route(phrase)
    assert plan is not None and plan.path == "fast", f"{phrase!r} reaches no fast path"
    assert plan.handler.name == name, f"{phrase!r} lands on {plan.handler.name}, not {name}"


def test_phrases_stay_out_of_what_the_routing_model_reads() -> None:
    """No core tool description carries an "Example:" clause (the old
    source of the phrases), and the field is not part of the schema the
    router is shown."""
    from domovoi.router import offered_tool_schemas

    for h in HANDLERS:
        if h.plugin_slug is not None:
            continue
        assert "Example:" not in str(h.tool_schema.get("description") or ""), h.name
        assert "example_phrases" not in h.tool_schema, h.name
    for schema in offered_tool_schemas(""):
        assert "example_phrases" not in str(schema)


def test_the_home_hint_gets_the_timers_first_phrase() -> None:
    # web/static/home.jsx and the app's HomeModels.kt prefer timer's first
    # phrase; before, every core list was empty and the hint fell through
    # to radio's "stream the jazz station" (which needs a favourite).
    assert HANDLER_BY_NAME["timer"].example_phrases[0] == "set a timer for 10 minutes"


def test_chat_mode_lists_its_phrase_only_when_it_is_on(monkeypatch) -> None:
    from domovoi.config import settings

    monkeypatch.setattr(settings, "chat_mode_enabled", False)
    assert example_phrases_of(HANDLER_BY_NAME["chat_mode"]) == []
    monkeypatch.setattr(settings, "chat_mode_enabled", True)
    assert example_phrases_of(HANDLER_BY_NAME["chat_mode"]) == ["let's have a chat"]


class _OldStylePlugin(Handler):
    name = "old_style"
    priority_band = 400
    display = HandlerDisplay(label="Old style")
    tool_schema = {
        "name": "old_style",
        "description": "Stream a station. Example: 'stream the jazz station', 'stop the radio'.",
        "parameters": {"type": "object", "properties": {}},
    }
    fast_paths: list = []

    async def execute(self, intent, ctx, session):  # pragma: no cover
        return None


def test_a_plugin_without_the_field_keeps_its_description_examples() -> None:
    assert example_phrases_of(_OldStylePlugin()) == ["stream the jazz station", "stop the radio"]

    class _NewStyle(_OldStylePlugin):
        example_phrases = ("play the jazz station",)

    assert example_phrases_of(_NewStyle()) == ["play the jazz station"]


@pytest.mark.asyncio
async def test_v1_handlers_serves_them() -> None:
    from domovoi.main import handlers

    served = {h.name: h for h in await handlers()}
    assert served["timer"].example_phrases == list(HANDLER_BY_NAME["timer"].example_phrases)
    assert served["calendar"].example_phrases[0] == "what's on my calendar today"
    assert served["help"].example_phrases == ["what can you do", "what can I say"]
    assert served["timer"].tool_schema == HANDLER_BY_NAME["timer"].tool_schema


def test_the_web_display_table_matches_the_core_registry() -> None:
    """GET /api/capabilities (and the manual's offline fallback) name core
    handlers from a static table in the web process; it fell out of step
    once (music_alias, manual-content finding 4.12)."""
    from web.backend.api.capabilities import CORE_HANDLER_DISPLAY

    table = {h["name"]: (h["label"], h["tone"]) for h in CORE_HANDLER_DISPLAY}
    core = {
        h.name: (h.display.label, h.display.tone) for h in HANDLERS if h.plugin_slug is None
    }
    assert table == core
