"""The routing eval corpus, checked without a model.

``scripts/routing_corpus.json`` pins what the router must do with a fixed
set of utterances; ``scripts/eval_routing.py`` runs it against a real
tool model or a live core. The model-driven step itself can't be
asserted here, but everything around it can — and these assertions are
DB-free on purpose so they never skip:

* the corpus is well-formed and names real handlers;
* fast-path expectations hold, and no fast path poaches a QA case (the
  library "is ..." catch-all did, until 2026-09-15);
* the per-handler tool-offer gate (``Handler.offers_tool``) withholds
  the bait tools from the knowledge questions that misrouted on the CPU
  host, and keeps them for their real uses;
* gated tools sit at the end of the offered list (prompt-prefix cache);
* the routing prompt still names the failure mode.

The live half — POST the corpus at a running core — runs only when
``DOMOVOI_EVAL_CORE_URL`` is set (e.g. ``http://192.168.0.117:6370``). It
writes intents_log rows on that core, so it is opt-in, never default.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from domovoi.clients.ollama import ROUTER_SYSTEM_PROMPT, RealOllamaClient
from domovoi.handlers import HANDLER_BY_NAME, HANDLERS
from domovoi.handlers.base import Handler, as_fast_path
from domovoi.handlers.shared.tool_gate import answers_without_tools
from domovoi.models import Context, Intent
from domovoi.router import _LEADING_FILLER_RE, offered_tool_schemas, route
from domovoi.tests.conftest import requires_db

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = REPO_ROOT / "scripts" / "routing_corpus.json"
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]
GATED = {"calculator", "double_check", "news", "library"}


def _normalize(utterance: str) -> str:
    """Exactly what router.route() hands the fast paths and the gate."""
    t = utterance.lower().strip().rstrip(".,!?")
    return _LEADING_FILLER_RE.sub("", t)


def _fast_path_winner(normalized: str) -> str | None:
    for h in HANDLERS:
        for entry in h.fast_paths:
            if as_fast_path(entry).pattern.match(normalized):
                return h.name
    return None


def _offered(utterance: str) -> list[str]:
    return [s["name"] for s in offered_tool_schemas(_normalize(utterance))]


def _ids(cases):
    return [c["utterance"] for c in cases]


# ─── Corpus shape ─────────────────────────────────────────────────────


def test_corpus_is_well_formed() -> None:
    assert CORPUS, "empty corpus"
    seen: set[str] = set()
    for c in CORPUS:
        assert isinstance(c["utterance"], str) and c["utterance"].strip()
        assert c["utterance"] not in seen, f"duplicate case {c['utterance']!r}"
        seen.add(c["utterance"])
        assert c["handler"] is None or c["handler"] in HANDLER_BY_NAME, (
            f"{c['utterance']!r} expects unknown handler {c['handler']!r}"
        )
        assert c.get("path") in (None, "fast", "llm")
        assert c["tags"], f"{c['utterance']!r} has no tags"
    tags = {t for c in CORPUS for t in c["tags"]}
    assert {"qa", "calculator", "double_check", "action"} <= tags


# ─── Fast paths ───────────────────────────────────────────────────────


@pytest.mark.parametrize("case", CORPUS, ids=_ids(CORPUS))
def test_fast_path_expectations(case) -> None:
    """A QA case must not be poached by any fast path; a handler case
    must not be poached by ANOTHER handler's fast path; an explicit
    path='fast' case must actually hit its handler's fast path."""
    winner = _fast_path_winner(_normalize(case["utterance"]))
    if case["handler"] is None:
        assert winner is None, f"fast path of {winner!r} poached a QA utterance"
    elif case.get("path") == "fast":
        assert winner == case["handler"]
    else:
        assert winner in (None, case["handler"]), (
            f"fast path of {winner!r} poached a {case['handler']!r} utterance"
        )


def test_library_is_catch_all_no_longer_poaches_yes_no_questions() -> None:
    """Regression: `^is (.+)` with an OPTIONAL 'in my library' owned every
    yes/no question from band 310."""
    assert _fast_path_winner("is it true that the great wall is visible from space") == "double_check"
    assert _fast_path_winner("is the oven on") is None
    assert _fast_path_winner("is creep in my library") == "library"
    assert _fast_path_winner("do i have ok computer") == "library"


# ─── Tool-offer gate ──────────────────────────────────────────────────


_QA_CASES = [c for c in CORPUS if "qa" in c["tags"]]


@pytest.mark.parametrize("case", _QA_CASES, ids=_ids(_QA_CASES))
def test_qa_cases_are_not_offered_the_verification_or_news_tools(case) -> None:
    """None of the QA utterances asks for a check or for news, so the
    gate must keep double_check and news out of the model's sight."""
    offered = _offered(case["utterance"])
    assert "double_check" not in offered
    assert "news" not in offered


@pytest.mark.parametrize(
    "utterance",
    [
        "who wrote the odyssey",
        "who painted the mona lisa",
        "who invented the telephone",
        "where is the eiffel tower",
        "why is the sky blue",
        "tell me who wrote the odyssey",
        "can you tell me who wrote the odyssey",
        "do you know why the sky is blue",
    ],
)
def test_calculator_withheld_from_who_why_where_questions(utterance: str) -> None:
    assert "calculator" not in _offered(utterance)


@pytest.mark.parametrize(
    "utterance",
    [
        # F-V004: the measured misroute — a book question answered as a
        # miss against the music library.
        "who wrote pride and prejudice",
        "who wrote the odyssey",
        "who painted the mona lisa",
        "who invented the telephone",
        "where is the eiffel tower",
        "why is the sky blue",
        "tell me who wrote pride and prejudice",
    ],
)
def test_library_withheld_from_bare_knowledge_questions(utterance: str) -> None:
    assert "library" not in _offered(utterance)


@pytest.mark.parametrize(
    "utterance",
    [
        "who sings creep",
        "whose album is ok computer",
        "who is the artist on this track",
        "where did i put that playlist",
        "why is that song in my library twice",
        "do i have ok computer",
        "what did i add today",
        "how many songs do i have",
        "find creep in my library",
    ],
)
def test_library_kept_for_its_real_uses(utterance: str) -> None:
    """A musical cue anywhere in the utterance keeps the schema on
    offer, and every opener other than who/whose/whom/why/where does
    too — a wrongly withheld tool silently degrades a real command."""
    assert "library" in _offered(utterance)


@pytest.mark.parametrize(
    ("utterance", "handler"),
    [
        ("how many days until christmas", "calculator"),
        ("can you work out what 15 percent of 60 dollars is", "calculator"),
        ("what is a third of ninety", "calculator"),
        ("when is thanksgiving", "calculator"),
        ("who has 3 more than 5", "calculator"),          # a digit keeps it on offer
        ("is it true that the great wall is visible from space", "double_check"),
        ("fact check what you just said", "double_check"),
        ("verify that for me", "double_check"),
        ("that doesn't sound right", "double_check"),
        ("are you certain", "double_check"),
        ("i don't believe you", "double_check"),
        ("any news about the election", "news"),
        ("what's happening in the world today", "news"),
        ("read me the headlines", "news"),
        ("catch me up on the latest", "news"),
        ("give me the briefing", "news"),
    ],
)
def test_gate_keeps_tools_for_their_real_uses(utterance: str, handler: str) -> None:
    assert handler in _offered(utterance), f"{handler!r} withheld from {utterance!r}"


def test_ungated_handlers_always_offer() -> None:
    for h in HANDLERS:
        if h.name not in GATED:
            assert type(h).offers_tool is Handler.offers_tool, (
                f"{h.name} gates its tool — add it to GATED and cover it here"
            )
            assert h.offers_tool("who wrote the odyssey") is True


def test_gated_tools_are_offered_last() -> None:
    """Withholding a tool must only shorten the tail of the tool list —
    the prefix Ollama's KV cache reuses between turns stays put."""
    names = _offered("play some jazz in the kitchen")
    ungated = [n for n in names if n not in GATED]
    gated = [n for n in names if n in GATED]
    assert names == ungated + gated
    assert gated, "expected at least one gated tool on offer for a plain command"
    # Band order is preserved inside each group.
    band = {h.name: h.priority_band for h in HANDLERS}
    assert ungated == sorted(ungated, key=band.__getitem__)
    assert gated == sorted(gated, key=band.__getitem__)


def test_tools_offered_only_on_request_are_appended_after_the_usual_ones() -> None:
    """double_check and news are offered only when asked for; they go after
    calculator and library (on offer unless ruled out), so asking for one
    APPENDS a tool: the router re-reads that schema, not the ~900 tokens
    of calculator and library behind it, and the next ordinary turn finds
    its cached prefix intact."""
    usual = _offered("play some jazz in the kitchen")
    assert usual[-2:] == ["calculator", "library"]
    for utterance, extra in (
        ("fact check what you just said", ["double_check"]),
        ("what's happening in the world today", ["news"]),
        ("double check the latest news about the 3 timers i set", ["double_check", "news"]),
    ):
        names = _offered(utterance)
        assert names == usual + extra, utterance


def test_an_ordinary_turn_offers_the_warm_up_list() -> None:
    """What the boot warm-up primes (offered_tool_schemas("")) is exactly
    what an ordinary routed turn offers, so the first turn finds it cached."""
    assert _offered("") == _offered("remind me to call mom at five")
    assert _offered("") == _offered("could you put on something relaxing")


# ─── Straight to QA ───────────────────────────────────────────────────


@pytest.mark.parametrize("case", CORPUS, ids=_ids(CORPUS))
def test_only_qa_utterances_skip_the_router(case) -> None:
    """Every utterance that goes straight to the Q&A model must be one the
    corpus expects QA to answer; a handler case must reach the router."""
    if answers_without_tools(_normalize(case["utterance"])):
        assert case["handler"] is None, (
            f"{case['utterance']!r} would skip the router but belongs to {case['handler']!r}"
        )


@pytest.mark.parametrize(
    "utterance",
    [
        # the bait class (F-V002/F-V004) and the measured slow answers
        "who wrote the odyssey", "who wrote pride and prejudice", "who painted the mona lisa",
        "who invented the telephone", "where is the eiffel tower", "why is the sky blue",
        "tell me who wrote the odyssey", "do you know why the sky is blue",
        "who won the 1998 world cup", "tell me a joke", "tell me another joke",
        "tell me a fun fact", "give me a fun fact", "tell me some facts about the moon",
        "tell me a story", "tell me a bedtime story about a dragon", "tell me a riddle",
        "tell me something interesting", "make me laugh", "explain how a rainbow forms",
    ],
)
def test_plain_questions_and_requests_skip_the_router(utterance: str) -> None:
    assert answers_without_tools(_normalize(utterance))


@pytest.mark.parametrize(
    "utterance",
    [
        # about the house, the speaker or something pointed at
        "who sings creep", "whose album is ok computer", "who is the artist on this track",
        "where did i put that playlist", "why is the wifi so slow",
        "where did i leave off in my audiobook", "who is in the kitchen", "why did you say that",
        "who is this", "where are my notes", "who is home", "where is everyone",
        "who is at the door", "why is the music off", "where's the timer",
        "tell me a joke about my dog", "tell me a news story", "tell me the stories",
        # openers that front real handler turns
        "what is the capital of mongolia", "how many days until christmas",
        "what time is it", "tell me about the history of rome",
    ],
)
def test_questions_about_the_house_still_reach_the_router(utterance: str) -> None:
    assert not answers_without_tools(_normalize(utterance))


# ─── Cue words (a false positive changes the router's prompt) ─────────


@pytest.mark.parametrize(
    ("utterance", "tool", "offered"),
    [
        # "history" held "story"; "reporter", "topical" held "report", "topic"
        ("tell me about the history of rome", "news", False),
        ("who was the first reporter on the moon", "news", False),
        ("is that topical", "news", False),
        ("tell me a story", "news", False),
        ("i want to hear a short story about pirates", "news", False),
        ("tell me a news story", "news", True),
        ("what's the top story", "news", True),
        ("read me the stories", "news", True),
        ("any reports on the election", "news", True),
        # everyday words that are not a request to verify
        ("tell me a fun fact", "double_check", False),
        ("remind me in twenty minutes to check the oven", "double_check", False),
        ("make sure the timer is set", "double_check", False),
        ("i really want some jazz", "double_check", False),
        ("actually play the other one", "double_check", False),
        ("i bet it rains", "double_check", False),
        ("what's the source of the nile", "double_check", True),
        # and the ones that are
        ("is that right", "double_check", True),
        ("is that really true", "double_check", True),
        ("are you sure", "double_check", True),
        ("you sure", "double_check", True),
        ("that's not right", "double_check", True),
        ("what's your source", "double_check", True),
        ("can you check that", "double_check", True),
        ("really", "double_check", True),
        ("is that a fact", "double_check", True),
        ("for real", "double_check", True),
    ],
)
def test_cue_words(utterance: str, tool: str, offered: bool) -> None:
    assert (tool in _offered(utterance)) is offered, utterance


def test_every_registered_handler_is_offered_for_a_plain_command() -> None:
    """The gate withholds only on evidence — a command with a digit, a
    verification word and a news word in it must see every tool."""
    names = _offered("double check the latest news about the 3 timers i set")
    assert set(names) == {h.name for h in HANDLERS}


# ─── Routing prompt ───────────────────────────────────────────────────


class _RecordingChat:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": "Homer"}}


@pytest.mark.asyncio
async def test_route_sends_the_knowledge_question_prompt() -> None:
    """Pin the two sentences that name the failure mode, so a prompt
    tidy-up can't quietly drop them."""
    chat = _RecordingChat()
    c = RealOllamaClient.__new__(RealOllamaClient)
    c._client = type("C", (), {"chat": staticmethod(chat)})()
    c._tool_model = "qwen3:8b"
    c._qa_model = "llama3.2:3b"
    c._tool_think = False
    c._send_think = False
    c._keep_alive = None
    c._send_keep_alive = False

    out = await c.route("who wrote the odyssey", offered_tool_schemas("who wrote the odyssey"))
    assert out is None
    system = chat.calls[0]["messages"][0]["content"]
    assert system == ROUTER_SYSTEM_PROMPT
    assert "NO tool call" in system
    assert "a question is not a claim" in system
    offered = {t["function"]["name"] for t in chat.calls[0]["tools"]}
    assert "calculator" not in offered and "double_check" not in offered


# ─── Router integration (needs the test DB) ───────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_router_offers_gated_schemas_and_ignores_withheld_calls(db_session) -> None:
    """The router hands the client the gated list, and a call naming a
    tool that wasn't offered is treated as the QA fallthrough."""
    seen: dict[str, list[str]] = {}

    class _BaitingOllama:
        async def route(self, transcript, tool_schemas):
            seen["names"] = [s["name"] for s in tool_schemas]
            return {"handler": "calculator", "args": {"action": "arithmetic", "expression": "1+1"}}

        async def qa(self, transcript, system_prompt=None, history=None):
            return "(stub)"

        async def qa_with_uncertainty(self, transcript, history=None, profile_prefix=None):
            from domovoi.clients.ollama import QAWithUncertainty

            return QAWithUncertainty(answer="Homer.", needs_verification=False, candidate_claim="")

        async def extract_search_subject(self, transcript, history=None):
            from domovoi.clients.ollama import SearchSubject

            return SearchSubject(subject="", refined_query=transcript)

    with patch("domovoi.router.get_ollama_client", lambda: _BaitingOllama()):
        # A who-question about music reaches the router, calculator withheld.
        response = await route(
            Intent(transcript="who sings creep", room_id="kitchen"),
            Context(room_id="kitchen", online=True),
            db_session,
        )
        await db_session.commit()

    assert "calculator" not in seen["names"]
    assert "library" in seen["names"]
    assert "double_check" not in seen["names"]
    assert response.matched_handler is None
    assert response.matched_path == "qa"
    assert "Homer" in response.text


@requires_db
@pytest.mark.asyncio
async def test_a_plain_question_never_asks_the_tool_model(db_session) -> None:
    """"who wrote the odyssey" goes straight to the Q&A model: no router
    call to answer it in throwaway text, and no bait tool to pick."""
    routed: list[str] = []

    class _Ollama:
        async def route(self, transcript, tool_schemas):
            routed.append(transcript)
            return {"handler": "calculator", "args": {"action": "arithmetic", "expression": "1+1"}}

        async def qa_with_uncertainty(self, transcript, history=None, profile_prefix=None):
            from domovoi.clients.ollama import QAWithUncertainty

            return QAWithUncertainty(answer="Homer.", needs_verification=False, candidate_claim="")

    with patch("domovoi.router.get_ollama_client", lambda: _Ollama()):
        for utterance in ("Who wrote the Odyssey?", "Tell me a joke.", "Tell me a fun fact."):
            response = await route(
                Intent(transcript=utterance, room_id="kitchen"),
                Context(room_id="kitchen", online=True),
                db_session,
            )
            assert (response.matched_path, response.text) == ("qa", "Homer."), utterance
        await db_session.commit()

    assert routed == []


# ─── Live core (opt-in) ───────────────────────────────────────────────


_LIVE_URL = os.environ.get("DOMOVOI_EVAL_CORE_URL", "").strip()


def _load_eval_script():
    spec = importlib.util.spec_from_file_location(
        "eval_routing", REPO_ROOT / "scripts" / "eval_routing.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not _LIVE_URL, reason="set DOMOVOI_EVAL_CORE_URL=http://host:6370 to run the corpus against a live core")
def test_live_core_routes_the_corpus() -> None:
    ev = _load_eval_script()
    cases = ev.load_corpus(CORPUS_PATH)
    results = ev.run_core(cases, _LIVE_URL, os.environ.get("DOMOVOI_EVAL_ROOM", "eval"), 120.0)
    bad = [
        f"{r.utterance!r}: want {r.expected_handler}/{r.expected_path or '*'} "
        f"got {r.got_handler}/{r.got_path} ({r.detail})"
        for r in results
        if not r.ok
    ]
    assert not bad, "misrouted on live core:\n" + "\n".join(bad)
