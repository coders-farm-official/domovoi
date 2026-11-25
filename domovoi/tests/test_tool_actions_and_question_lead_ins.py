"""Two live voice turns (2026-11-24, an office satellite on 1.4.1).

"Add seaweed, water." reached the routing model, which called the library
tool with ``action: "add"``, an action the library doesn't have, and the
house said "I don't know how to add the library." A tool call whose action
is not one its schema lists is now declined before the handler runs, so the
Q&A model answers instead of a broken sentence
(``router.tool_action_offered``).

"Tell me, how far away is Saturn from Earth?" got "I can't do that. Maybe
try a search engine?": the polite lead-in read as an order to the Q&A
model, whose prompt says to refuse orders. The lead-in is dropped from what
the model reads (``ollama.plain_question``).
"""

from __future__ import annotations

import types
import pytest

from domovoi.clients.ollama import plain_question
from domovoi.handlers import HANDLER_BY_NAME
from domovoi.models import Context, Intent
from domovoi.router import tool_action_offered
from domovoi.tests.conftest import requires_db


# ─── tool actions ─────────────────────────────────────────────────────────


def test_an_action_the_schema_lists_is_offered() -> None:
    library = HANDLER_BY_NAME["library"]
    for action in ("search", "have", "added_recently", "count", "enrich", "Search"):
        assert tool_action_offered(library, {"action": action}), action


def test_an_action_the_schema_doesnt_list_is_not() -> None:
    library = HANDLER_BY_NAME["library"]
    assert not tool_action_offered(library, {"action": "add", "query": "seaweed, water"})
    assert not tool_action_offered(HANDLER_BY_NAME["lists"], {"action": "delete_everything"})


def test_aliases_no_action_and_schemas_without_an_enum_are_left_to_the_handler() -> None:
    lists = HANDLER_BY_NAME["lists"]
    assert tool_action_offered(lists, {"action": "check_off", "item": "milk"})
    assert tool_action_offered(lists, {"action": "done", "item": "milk"})
    library = HANDLER_BY_NAME["library"]
    assert tool_action_offered(library, {}) and tool_action_offered(library, None)
    no_enum = types.SimpleNamespace(tool_schema={"parameters": {"properties": {"q": {"type": "string"}}}})
    assert tool_action_offered(no_enum, {"action": "anything"})


def test_a_plugins_tool_is_left_to_its_handler() -> None:
    plugin = types.SimpleNamespace(
        plugin_slug="radio",
        tool_schema={"parameters": {"properties": {"action": {"enum": ["play"]}}}},
    )
    assert tool_action_offered(plugin, {"action": "tune"})


def test_every_core_tools_aliases_are_not_already_in_its_enum() -> None:
    for name, h in HANDLER_BY_NAME.items():
        aliases = set(getattr(h, "tool_action_aliases", ()))
        if not aliases:
            continue
        enum = set((((h.tool_schema or {}).get("parameters") or {}).get("properties") or {})
                   .get("action", {}).get("enum") or [])
        assert enum and not (aliases & enum), name


@requires_db
@pytest.mark.asyncio
async def test_a_made_up_action_is_answered_by_qa_not_the_handler(db_session, monkeypatch) -> None:
    from domovoi import router as R

    stub = R.get_ollama_client()
    routed: list[str] = []

    class _Calls(type(stub)):
        async def route(self, transcript, schemas):
            routed.append(transcript)
            return {"handler": "library", "args": {"action": "add", "query": "seaweed, water"}}

    client = _Calls.__new__(_Calls)
    client.__dict__.update(stub.__dict__)
    monkeypatch.setattr(R, "get_ollama_client", lambda: client)
    ctx = Context(session_id=None, room_id="office", online=True)
    resp = await R.route(Intent(transcript="blorp the seaweed quux", room_id="office"), ctx, db_session)
    assert routed, "the tool model was asked"
    assert "know how to" not in (resp.text or "")
    assert resp.matched_handler != "library"


# ─── question lead-ins ────────────────────────────────────────────────────


@pytest.mark.parametrize(("said", "asked"), [
    ("Tell me, how far away is Saturn from Earth?", "How far away is Saturn from Earth?"),
    ("tell me how tall Everest is", "How tall Everest is"),
    ("Can you tell me who wrote Dune?", "Who wrote Dune?"),
    ("Could you please tell me what a parsec is", "What a parsec is"),
    ("do you know when the Eiffel Tower was built", "When the Eiffel Tower was built"),
    ("Okay, tell me: why is the sky blue?", "Why is the sky blue?"),
    ("I was wondering, how many moons does Jupiter have?", "How many moons does Jupiter have?"),
])
def test_a_lead_in_before_a_question_is_dropped(said: str, asked: str) -> None:
    assert plain_question(said) == asked


@pytest.mark.parametrize("said", [
    "tell me about Mars", "tell me a joke", "tell me a story about a dragon",
    "let me know when it rains", "tell me if it's raining", "how far is the moon?",
    "tell me", "", "Tell me something interesting.",
])
def test_anything_else_is_kept_as_said(said: str) -> None:
    assert plain_question(said) == said


def test_the_qa_model_reads_the_plain_question(monkeypatch) -> None:
    import asyncio

    from domovoi.clients.ollama import RealOllamaClient

    seen: dict = {}

    def fake_build(self, transcript, system_prompt, history):
        seen["t"] = transcript
        raise RuntimeError("stop here")

    monkeypatch.setattr(RealOllamaClient, "_build_messages", fake_build)
    obj = RealOllamaClient.__new__(RealOllamaClient)
    with pytest.raises(RuntimeError):
        asyncio.run(obj.qa_with_uncertainty("Tell me, how far away is Saturn from Earth?"))
    assert seen["t"] == "How far away is Saturn from Earth?"
