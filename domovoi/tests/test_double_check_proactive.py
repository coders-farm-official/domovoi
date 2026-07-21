"""Proactive web-search offer tests — heuristic categorizer, the QA
fallthrough offer flow, the confirmation yes/no handlers, the auto-
search short-circuit, and the per-speaker prefs threshold.

The DoubleCheck verify path (claim → SearxNG → verdict) is covered
in test_double_check.py; this file targets ONLY the proactive
additions.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import text

from domovoi.clients.ollama import (
    QAWithUncertainty,
    _parse_qa_json,
)
from domovoi.clients.searxng import SearchResult, SearxNGStubClient
from domovoi.db.repositories import (
    PeopleRepository,
    SessionRepository,
    WebSearchPrefsRepository,
)
from domovoi.handlers.double_check import (
    AUTO_SEARCH_OFFER_THRESHOLD,
    DoubleCheckHandler,
    _parse_answer_from_sources,
)
from domovoi.models import Context, Intent
from domovoi.router import route
from domovoi.tests.conftest import requires_db
from domovoi.clients.ollama import SearchSubject
from domovoi.uncertainty import (
    CATEGORY_CURRENT_EVENTS,
    CATEGORY_GENERAL_RECENT,
    CATEGORY_PRICES_FINANCE,
    CATEGORY_SPORTS_SCORES,
    CATEGORY_WEATHER,
    categorize_question,
)


# ─── Heuristic categorizer ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "transcript,expected",
    [
        ("what's the price of bitcoin", CATEGORY_PRICES_FINANCE),
        ("how much is a gallon of gas", CATEGORY_PRICES_FINANCE),
        ("what's the stock price of nvidia", CATEGORY_PRICES_FINANCE),
        ("who won the super bowl", CATEGORY_SPORTS_SCORES),
        ("what's the final score of the lakers game", CATEGORY_SPORTS_SCORES),
        ("what's the latest news", CATEGORY_CURRENT_EVENTS),
        ("what's happening in the news today", CATEGORY_CURRENT_EVENTS),
        ("who is the current president", CATEGORY_CURRENT_EVENTS),
        ("what's the weather in phoenix", CATEGORY_WEATHER),
        ("will it rain tomorrow", CATEGORY_WEATHER),
        ("what's the temperature outside", CATEGORY_WEATHER),
        ("what happened today", CATEGORY_GENERAL_RECENT),
        ("what's the latest version of python", CATEGORY_GENERAL_RECENT),
        ("is the meeting tonight", CATEGORY_GENERAL_RECENT),
    ],
)
def test_categorize_question_positives(transcript: str, expected: str) -> None:
    assert categorize_question(transcript) == expected


@pytest.mark.parametrize(
    "transcript",
    [
        "what is the capital of france",
        "tell me a joke",
        "how do i make pasta",
        "what is two plus two",
        "explain quantum physics",
        "",
    ],
)
def test_categorize_question_negatives(transcript: str) -> None:
    assert categorize_question(transcript) is None


# ─── Helper parsers ──────────────────────────────────────────────────────


def test_parse_answer_from_sources_clean() -> None:
    raw = (
        "ANSWER: The Lakers beat the Celtics 110 to 102.\n"
        "SOURCE: https://espn.com/game/123"
    )
    answer, source = _parse_answer_from_sources(raw)
    assert "Lakers" in answer and "110 to 102" in answer
    assert source == "https://espn.com/game/123"


def test_parse_answer_from_sources_no_source_token() -> None:
    raw = "ANSWER: Couldn't find a clear answer.\nSOURCE: NONE"
    answer, source = _parse_answer_from_sources(raw)
    assert "couldn't find" in answer.lower()
    assert source is None


def test_parse_answer_from_sources_format_dropped() -> None:
    """When the model ignores the format, return raw text — never blank."""
    answer, source = _parse_answer_from_sources("Just a sentence with no labels.")
    assert "sentence" in answer
    assert source is None


def test_parse_qa_json_well_formed() -> None:
    raw = (
        '{"answer": "The president is Joe Biden.", '
        '"needs_verification": true, '
        '"candidate_claim": "Joe Biden is the current president."}'
    )
    result = _parse_qa_json(raw)
    assert result.answer.startswith("The president")
    assert result.needs_verification is True
    assert "Joe Biden" in result.candidate_claim


def test_parse_qa_json_with_surrounding_prose() -> None:
    """Model wraps the JSON in conversational prose — we should still parse."""
    raw = (
        "Sure! Here is the JSON object: "
        '{"answer": "It is sunny.", "needs_verification": false, '
        '"candidate_claim": ""} '
        "Hope that helps!"
    )
    result = _parse_qa_json(raw)
    assert result.answer == "It is sunny."
    assert result.needs_verification is False


def test_parse_qa_json_garbage_falls_back_safely() -> None:
    result = _parse_qa_json("not json at all")
    assert result.needs_verification is False  # never trigger an offer on bad parse


# ─── Router → QA fallthrough → offer ──────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_volatile_question_emits_subject_offer(db_session) -> None:
    """A volatile-category question online skips the confident local
    guess and parks a subject-naming offer with the refined
    search query; matched_path is the new 'volatile_offer'."""
    intent = Intent(transcript="who is the current president", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()
    assert response.matched_path == "volatile_offer"
    assert "want me to check" in response.text.lower()
    # No confident local guess was spoken (qa_with_uncertainty skipped).
    assert "stub qa" not in response.text.lower()
    assert response.expect_followup is True

    pending = (
        await SessionRepository(db_session).get_context(response.session_id)
    ).get("pending_confirmation")
    assert isinstance(pending, dict)
    assert pending["handler"] == "double_check"
    assert pending["kind"] == "core.self_doubt_offer"
    assert pending["category"] == CATEGORY_CURRENT_EVENTS
    assert pending["search_query"]  # refined query parked for the resume
    assert pending["candidate_claim"] == ""


@requires_db
@pytest.mark.asyncio
async def test_volatile_question_offline_is_graceful_no_park(db_session) -> None:
    """Offline, a volatile question gets a plain "I'm offline" reply —
    no stale local guess, no parked offer."""
    # NOTE: "what's the latest news" now routes to the dedicated NewsHandler,
    # so use another current-events volatile phrase (same one the online
    # volatile test above uses) to exercise the proactive double-check path.
    intent = Intent(transcript="who is the current president", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=False)
    response = await route(intent, ctx, db_session)
    await db_session.commit()
    assert "offline" in response.text.lower()
    assert "check that online" not in response.text.lower()
    assert response.expect_followup is False
    pending = (
        await SessionRepository(db_session).get_context(response.session_id)
    ).get("pending_confirmation")
    assert pending is None


@requires_db
@pytest.mark.asyncio
async def test_volatile_empty_subject_uses_fallback_phrase(db_session) -> None:
    """When the model returns an empty subject, the offer falls back to a
    category noun-phrase rather than an awkward "want me to check ?"."""

    class _BlankSubjectOllama:
        async def route(self, transcript, tool_schemas):
            return None

        async def qa(self, transcript, system_prompt=None, history=None):
            return "(stub)"

        async def qa_with_uncertainty(self, transcript, history=None, profile_prefix=None):
            raise AssertionError("volatile gate must skip qa_with_uncertainty")

        async def extract_search_subject(self, transcript, history=None):
            return SearchSubject(subject="", refined_query=transcript)

    with patch("domovoi.router.get_ollama_client", lambda: _BlankSubjectOllama()):
        intent = Intent(transcript="what's the weather in phoenix", room_id="kitchen")
        ctx = Context(room_id="kitchen", online=True)
        response = await route(intent, ctx, db_session)
        await db_session.commit()

    assert response.matched_path == "volatile_offer"
    # Weather fallback phrase, not a dangling "check ?".
    assert "check the weather?" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_qa_llm_self_doubt_flag_emits_offer(db_session) -> None:
    """A question that the heuristic doesn't catch but the LLM
    self-flags as needing verification should still get an offer."""

    class _DoubtingOllama:
        async def route(self, transcript, tool_schemas):
            return None

        async def qa(self, transcript, system_prompt=None, history=None):
            return "(stub)"

        async def qa_with_uncertainty(self, transcript, history=None, profile_prefix=None):
            return QAWithUncertainty(
                answer="The library has 17 tracks.",
                needs_verification=True,
                candidate_claim="The library has 17 tracks.",
            )

    with patch("domovoi.router.get_ollama_client", lambda: _DoubtingOllama()):
        intent = Intent(transcript="how many tracks are in my library", room_id="kitchen")
        ctx = Context(room_id="kitchen", online=True)
        response = await route(intent, ctx, db_session)
        await db_session.commit()

    assert "check that online" in response.text.lower()
    pending = (
        await SessionRepository(db_session).get_context(response.session_id)
    ).get("pending_confirmation")
    assert pending and pending["kind"] == "core.self_doubt_offer"


@requires_db
@pytest.mark.asyncio
async def test_qa_no_category_no_doubt_no_offer(db_session) -> None:
    """Plain timeless question — no offer should fire."""
    intent = Intent(transcript="tell me a joke about cats", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()
    assert "check that online" not in response.text.lower()
    assert response.expect_followup is False


# ─── Confirmation handlers ───────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_yes_to_offer_runs_search_and_returns_synthesized_answer(
    db_session,
) -> None:
    """End-to-end: offer → "yes" routes to handle_confirmation →
    SearxNG search → Ollama re-prompt with answer + source."""
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await SessionRepository(db_session).set_context_key(
        sid,
        "pending_confirmation",
        {
            "handler": "double_check",
            "kind": "core.self_doubt_offer",
            "question": "who is the current president",
            "candidate_claim": "",
            "category": CATEGORY_CURRENT_EVENTS,
        },
    )
    await db_session.commit()

    class _SynthOllama:
        async def qa(self, transcript, system_prompt=None, history=None):
            if "voice assistant answering" in (system_prompt or ""):
                return (
                    "ANSWER: The current president is XYZ.\n"
                    "SOURCE: https://example.com/news"
                )
            return ""

    with patch(
        "domovoi.handlers.double_check.get_ollama_client",
        lambda: _SynthOllama(),
    ), patch(
        "domovoi.handlers.double_check.get_searxng_client",
        lambda: SearxNGStubClient(),
    ):
        intent = Intent(transcript="yes", room_id="kitchen", session_id=sid)
        ctx = Context(room_id="kitchen", session_id=sid, online=True)
        response = await route(intent, ctx, db_session)
        await db_session.commit()

    assert response.matched_handler == "double_check"
    assert response.matched_path == "confirmation"
    assert "XYZ" in response.text
    assert response.data.get("source") == "https://example.com/news"


@requires_db
@pytest.mark.asyncio
async def test_no_to_offer_acknowledges_without_search(db_session) -> None:
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await SessionRepository(db_session).set_context_key(
        sid,
        "pending_confirmation",
        {
            "handler": "double_check",
            "kind": "core.self_doubt_offer",
            "question": "who is the current president",
            # A normal self-doubt offer HAD an answer, so "no" keeps it.
            "candidate_claim": "Joe Biden is the current president.",
            "category": CATEGORY_CURRENT_EVENTS,
        },
    )
    await db_session.commit()

    class _ExplodingSearx:
        async def search(self, query, max_results=5):
            raise AssertionError("search must not run on no")

    with patch(
        "domovoi.handlers.double_check.get_searxng_client",
        lambda: _ExplodingSearx(),
    ):
        intent = Intent(transcript="no", room_id="kitchen", session_id=sid)
        ctx = Context(room_id="kitchen", session_id=sid, online=True)
        response = await route(intent, ctx, db_session)
        await db_session.commit()

    assert response.matched_handler == "double_check"
    assert "sticking" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_no_to_volatile_offer_says_wont_look_up(db_session) -> None:
    """A volatile offer parks no candidate_claim (no answer was given),
    so "no" must NOT claim to be "sticking with my answer"."""
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await SessionRepository(db_session).set_context_key(
        sid,
        "pending_confirmation",
        {
            "handler": "double_check",
            "kind": "core.self_doubt_offer",
            "question": "what's the weather in phoenix",
            "search_query": "phoenix weather today",
            "candidate_claim": "",
            "category": CATEGORY_WEATHER,
        },
    )
    await db_session.commit()

    intent = Intent(transcript="no", room_id="kitchen", session_id=sid)
    ctx = Context(room_id="kitchen", session_id=sid, online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()

    assert "sticking" not in response.text.lower()
    assert "won't look it up" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_yes_to_volatile_offer_searches_refined_query(db_session) -> None:
    """On "yes", the resume searches the parked refined search_query, not
    the raw transcript."""
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await SessionRepository(db_session).set_context_key(
        sid,
        "pending_confirmation",
        {
            "handler": "double_check",
            "kind": "core.self_doubt_offer",
            "question": "what's the weather",
            "search_query": "phoenix arizona weather forecast today",
            "candidate_claim": "",
            "category": CATEGORY_WEATHER,
        },
    )
    await db_session.commit()

    seen_queries: list[str] = []

    class _CapturingSearx:
        async def search(self, query, max_results=5):
            seen_queries.append(query)
            return [
                SearchResult(
                    title="Phoenix weather",
                    url="https://example.com/wx",
                    content="Sunny, high near 105F.",
                )
            ]

    class _SynthOllama:
        async def qa(self, transcript, system_prompt=None, history=None):
            if "voice assistant answering" in (system_prompt or ""):
                return "ANSWER: It's sunny.\nSOURCE: https://example.com/wx"
            return ""

    with patch(
        "domovoi.handlers.double_check.get_ollama_client",
        lambda: _SynthOllama(),
    ), patch(
        "domovoi.handlers.double_check.get_searxng_client",
        lambda: _CapturingSearx(),
    ):
        intent = Intent(transcript="yes", room_id="kitchen", session_id=sid)
        ctx = Context(room_id="kitchen", session_id=sid, online=True)
        response = await route(intent, ctx, db_session)
        await db_session.commit()

    assert response.matched_path == "confirmation"
    assert seen_queries == ["phoenix arizona weather forecast today"]


@requires_db
@pytest.mark.asyncio
async def test_anon_speaker_never_accumulates_pref_rows(db_session) -> None:
    """person_id is None → no row inserted into web_search_prefs."""
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await SessionRepository(db_session).set_context_key(
        sid,
        "pending_confirmation",
        {
            "handler": "double_check",
            "kind": "core.self_doubt_offer",
            "question": "what's happening today",
            "candidate_claim": "",
            "category": CATEGORY_GENERAL_RECENT,
        },
    )
    await db_session.commit()

    with patch(
        "domovoi.handlers.double_check.get_ollama_client",
        lambda: _SynthOllamaStub(),
    ), patch(
        "domovoi.handlers.double_check.get_searxng_client",
        lambda: SearxNGStubClient(),
    ):
        intent = Intent(transcript="yes", room_id="kitchen", session_id=sid)
        ctx = Context(room_id="kitchen", session_id=sid, online=True, person_id=None)
        await route(intent, ctx, db_session)
        await db_session.commit()

    rows = (
        await db_session.execute(text("SELECT COUNT(*) FROM web_search_prefs"))
    ).scalar()
    assert rows == 0


@requires_db
@pytest.mark.asyncio
async def test_third_yes_triggers_prefs_offer_followup(db_session) -> None:
    """After AUTO_SEARCH_OFFER_THRESHOLD yeses for the same (person, category),
    the response tacks on the auto-search meta-offer and parks a
    prefs_offer pending_confirmation."""
    person_id = await PeopleRepository(db_session).create(name="Kamron")
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")

    # Pre-seed: yes_count = threshold - 1 so this is the threshold yes.
    prefs_repo = WebSearchPrefsRepository(db_session)
    for _ in range(AUTO_SEARCH_OFFER_THRESHOLD - 1):
        await prefs_repo.record_offer_response(
            person_id, CATEGORY_CURRENT_EVENTS, True
        )
    await SessionRepository(db_session).set_context_key(
        sid,
        "pending_confirmation",
        {
            "handler": "double_check",
            "kind": "core.self_doubt_offer",
            "question": "who won the election",
            "candidate_claim": "",
            "category": CATEGORY_CURRENT_EVENTS,
        },
    )
    await db_session.commit()

    with patch(
        "domovoi.handlers.double_check.get_ollama_client",
        lambda: _SynthOllamaStub(),
    ), patch(
        "domovoi.handlers.double_check.get_searxng_client",
        lambda: SearxNGStubClient(),
    ):
        intent = Intent(transcript="yes", room_id="kitchen", session_id=sid)
        ctx = Context(
            room_id="kitchen", session_id=sid, online=True, person_id=person_id
        )
        response = await route(intent, ctx, db_session)
        await db_session.commit()

    # Auto-search meta-offer appended to the response.
    assert "automatically" in response.text.lower()
    # New pending_confirmation parked for prefs_offer.
    pending = (
        await SessionRepository(db_session).get_context(sid)
    ).get("pending_confirmation")
    assert pending and pending["kind"] == "core.prefs_offer"
    assert pending["category"] == CATEGORY_CURRENT_EVENTS


@requires_db
@pytest.mark.asyncio
async def test_yes_to_prefs_offer_flips_auto_search(db_session) -> None:
    person_id = await PeopleRepository(db_session).create(name="Kamron")
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await SessionRepository(db_session).set_context_key(
        sid,
        "pending_confirmation",
        {
            "handler": "double_check",
            "kind": "core.prefs_offer",
            "category": CATEGORY_CURRENT_EVENTS,
        },
    )
    await db_session.commit()

    intent = Intent(transcript="yes", room_id="kitchen", session_id=sid)
    ctx = Context(
        room_id="kitchen", session_id=sid, online=True, person_id=person_id
    )
    response = await route(intent, ctx, db_session)
    await db_session.commit()

    assert "automatically" in response.text.lower()
    prefs = await WebSearchPrefsRepository(db_session).get(
        person_id, CATEGORY_CURRENT_EVENTS
    )
    assert prefs is not None and prefs[0] is True  # auto_search = True


@requires_db
@pytest.mark.asyncio
async def test_auto_search_short_circuits_qa(db_session) -> None:
    """With auto_search=true, the router skips ollama_client.qa_with_uncertainty
    entirely and routes straight to DoubleCheck's answer_question_from_web."""
    person_id = await PeopleRepository(db_session).create(name="Kamron")
    await WebSearchPrefsRepository(db_session).set_auto(
        person_id, CATEGORY_PRICES_FINANCE, True
    )
    await db_session.commit()

    # Patch the QA path to explode — if it gets called, the test fails.
    class _ExplodingOllama:
        async def route(self, transcript, tool_schemas):
            return None

        async def qa(self, transcript, system_prompt=None, history=None):
            if "voice assistant answering" in (system_prompt or ""):
                return (
                    "ANSWER: Bitcoin is at $42,000.\n"
                    "SOURCE: https://coindesk.com/btc"
                )
            return ""

        async def qa_with_uncertainty(self, transcript, history=None, profile_prefix=None):
            raise AssertionError("auto_search should skip qa_with_uncertainty")

    with patch(
        "domovoi.router.get_ollama_client",
        lambda: _ExplodingOllama(),
    ), patch(
        "domovoi.handlers.double_check.get_ollama_client",
        lambda: _ExplodingOllama(),
    ), patch(
        "domovoi.handlers.double_check.get_searxng_client",
        lambda: SearxNGStubClient(),
    ):
        intent = Intent(transcript="what's the price of bitcoin", room_id="kitchen")
        ctx = Context(
            room_id="kitchen", online=True, person_id=person_id
        )
        response = await route(intent, ctx, db_session)
        await db_session.commit()

    assert response.matched_handler == "double_check"
    assert response.matched_path == "auto_search"
    assert "Bitcoin" in response.text


# ─── Local helpers ───────────────────────────────────────────────────────


class _SynthOllamaStub:
    """Minimal Ollama for the answer-from-sources path."""

    async def qa(self, transcript, system_prompt=None, history=None):
        if "voice assistant answering" in (system_prompt or ""):
            return (
                "ANSWER: Here is what I found.\n"
                "SOURCE: https://example.com/article"
            )
        return ""
