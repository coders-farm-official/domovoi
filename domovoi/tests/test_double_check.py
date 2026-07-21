"""DoubleCheckHandler tests — extraction, verdict synthesis, response
formatting, and the offline / no-context / no-claim graceful paths.

Ollama and SearxNG are stubbed at the module level via the existing
USE_STUBS=true conftest setting, plus targeted patches inside each
test for the specific extractor / verifier outputs we want to exercise.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import text

from domovoi.clients.searxng import SearchResult, SearxNGStubClient
from domovoi.db.repositories import SessionRepository
from domovoi.handlers.double_check import (
    DoubleCheckHandler,
    _VERIFY_RE,
    _format_voice_response,
    _parse_verdict,
)
from domovoi.models import Context, Intent
from domovoi.tests.conftest import requires_db


# ─── Regex coverage ───────────────────────────────────────────────────────


def test_verify_regex_canonical_phrasings() -> None:
    for s in (
        "double check that",
        "double-check that",
        "can you double check that",
        "could you verify that",
        "would you fact-check this",
        "verify that",
        "verify this",
        "verify it",
        "fact check that",
        "are you sure",
        "are you sure about that",
        "is that right",
        "is that correct",
        "is that true",
        "is that accurate",
        "really?",
    ):
        assert _VERIFY_RE.match(s), f"expected match: {s!r}"


def test_verify_regex_does_not_swallow_unrelated() -> None:
    """Anchored against false positives — different intents that
    happen to contain 'sure' / 'verify' / 'check' shouldn't match."""
    for s in (
        "are you sure you want to do that",  # consent prompt
        "verify the timer",                  # different verb-target
        "double check the door is locked",   # not about verifying claims
        "sure thing",
        "really cool",
        "really good",
    ):
        assert not _VERIFY_RE.match(s), f"unexpected match: {s!r}"


# ─── Verdict parsing ──────────────────────────────────────────────────────


def test_parse_verdict_confirmed() -> None:
    raw = (
        "VERDICT: CONFIRMED\n"
        "SOURCE: https://en.wikipedia.org/wiki/Eiffel_Tower\n"
        "REASON: Wikipedia and the official site agree on the height."
    )
    verdict, source, reason = _parse_verdict(raw)
    assert verdict == "CONFIRMED"
    assert source == "https://en.wikipedia.org/wiki/Eiffel_Tower"
    assert "Wikipedia" in (reason or "")


def test_parse_verdict_refuted() -> None:
    raw = (
        "VERDICT: REFUTED\n"
        "SOURCE: https://example.com/correct\n"
        "REASON: The sources actually say it's 1,083 feet, not 1,063."
    )
    verdict, source, reason = _parse_verdict(raw)
    assert verdict == "REFUTED"
    assert reason and "1,083" in reason


def test_parse_verdict_ambiguous_explicit() -> None:
    raw = (
        "VERDICT: AMBIGUOUS\n"
        "SOURCE: NONE\n"
        "REASON: Sources disagree on the figure."
    )
    verdict, source, reason = _parse_verdict(raw)
    assert verdict == "AMBIGUOUS"
    assert source is None
    assert reason


def test_parse_verdict_falls_back_to_ambiguous_on_garbage() -> None:
    """If the model dropped the structured format entirely (long input
    pressure / refusal / hallucination), default to AMBIGUOUS rather
    than picking a verdict by accident."""
    raw = "I'm not really sure, the search results don't seem clear."
    verdict, source, reason = _parse_verdict(raw)
    assert verdict == "AMBIGUOUS"
    assert source is None
    assert reason is None


def test_parse_verdict_handles_empty_input() -> None:
    verdict, source, reason = _parse_verdict("")
    assert verdict == "AMBIGUOUS"


# ─── Voice formatting ─────────────────────────────────────────────────────


def test_voice_response_confirmed_includes_reason() -> None:
    text = _format_voice_response(
        "CONFIRMED", "Eiffel Tower is 1,063 feet tall.",
        "Wikipedia confirms.", "https://wiki/x",
    )
    assert text.startswith("Yes, that checks out.")
    assert "Wikipedia" in text


def test_voice_response_refuted_leads_with_correction_signal() -> None:
    text = _format_voice_response(
        "REFUTED", "It's 1,083 feet.",
        "Actually 1,063, per multiple sources.",
        "https://wiki/x",
    )
    assert "doesn't check out" in text


def test_voice_response_ambiguous_falls_back_when_no_reason() -> None:
    """When the verifier didn't supply a reason, AMBIGUOUS gets a
    default 'inconclusive' tail rather than terminating awkwardly."""
    text = _format_voice_response("AMBIGUOUS", "X", None, None)
    assert "not sure" in text.lower() or "inconclusive" in text.lower()


# ─── No-context / offline gracefuls ──────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_verify_with_no_session_responds_gracefully(db_session) -> None:
    """No session_id → no last_assistant_response → polite "ask me
    something first" rather than a vague error."""
    handler = DoubleCheckHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    response = await handler._verify(ctx, db_session)
    assert "nothing recent" in response.text.lower() or "ask me" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_verify_with_empty_last_response_responds_gracefully(db_session) -> None:
    """Session exists but last_assistant_response is empty/missing —
    same friendly fallback."""
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await db_session.commit()

    handler = DoubleCheckHandler()
    ctx = Context(session_id=sid, room_id="kitchen", online=True)
    response = await handler._verify(ctx, db_session)
    assert "nothing recent" in response.text.lower() or "ask me" in response.text.lower()


@pytest.mark.asyncio
async def test_fallback_offline_never_speaks_a_verdict() -> None:
    """Critical correctness: when offline we MUST refuse to verify,
    not fabricate a result. The risk callout in the rollout doc
    specifically cited this."""
    handler = DoubleCheckHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=False)
    intent = Intent(transcript="double check that", room_id="kitchen")
    response = await handler.fallback_offline(intent, ctx, None)
    assert "internet" in response.text.lower()
    # No verdict-shaped content snuck in.
    assert "checks out" not in response.text.lower()
    assert "doesn't check out" not in response.text.lower()


# ─── Full flow with mocked extractor + verifier ───────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_full_flow_confirmed_path(db_session) -> None:
    """End-to-end: pre-populate last_assistant_response, mock the
    Ollama extractor + verifier, and exercise the search → verdict
    → spoken response chain."""
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await SessionRepository(db_session).set_context_key(
        sid, "last_assistant_response",
        "The Eiffel Tower is 1,063 feet tall.",
    )
    await db_session.commit()

    extractor_calls = []
    verifier_calls = []

    class _MockOllama:
        async def qa(self, transcript, system_prompt=None, history=None):
            if "fact-checking assistant" in (system_prompt or "") and "extract ONE specific" in (system_prompt or ""):
                extractor_calls.append(transcript)
                return "Eiffel Tower is 1,063 feet tall."
            if "fact-checking judge" in (system_prompt or ""):
                verifier_calls.append(transcript)
                return (
                    "VERDICT: CONFIRMED\n"
                    "SOURCE: https://en.wikipedia.org/wiki/Eiffel_Tower\n"
                    "REASON: Multiple sources agree."
                )
            return ""

    with patch(
        "domovoi.handlers.double_check.get_ollama_client",
        lambda: _MockOllama(),
    ), patch(
        "domovoi.handlers.double_check.get_searxng_client",
        lambda: SearxNGStubClient(),
    ):
        handler = DoubleCheckHandler()
        ctx = Context(session_id=sid, room_id="kitchen", online=True)
        response = await handler._verify(ctx, db_session)

    assert "checks out" in response.text.lower()
    assert response.data["verdict"] == "CONFIRMED"
    assert response.data["claim"] == "Eiffel Tower is 1,063 feet tall."
    assert "wikipedia" in (response.data["source"] or "").lower()
    # Audit trail: search results are stored in data, not spoken.
    assert isinstance(response.data["results"], list)
    assert len(extractor_calls) == 1
    assert len(verifier_calls) == 1


@requires_db
@pytest.mark.asyncio
async def test_extractor_returns_none_skips_search(db_session) -> None:
    """When the previous response had nothing factual to verify, the
    extractor returns NONE; we should NOT hit search/verifier — that
    would waste API budget and produce a confused answer."""
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await SessionRepository(db_session).set_context_key(
        sid, "last_assistant_response", "Got it, playing music now.",
    )
    await db_session.commit()

    search_called = {"flag": False}

    class _NoneExtractor:
        async def qa(self, transcript, system_prompt=None, history=None):
            return "NONE"

    class _ExplodingSearx:
        async def search(self, query, max_results=5):
            search_called["flag"] = True
            raise AssertionError("search must not run when no claim")

    with patch(
        "domovoi.handlers.double_check.get_ollama_client",
        lambda: _NoneExtractor(),
    ), patch(
        "domovoi.handlers.double_check.get_searxng_client",
        lambda: _ExplodingSearx(),
    ):
        handler = DoubleCheckHandler()
        ctx = Context(session_id=sid, room_id="kitchen", online=True)
        response = await handler._verify(ctx, db_session)

    assert "nothing" in response.text.lower() or "not a factual" in response.text.lower()
    assert search_called["flag"] is False


@requires_db
@pytest.mark.asyncio
async def test_search_returning_no_results_responds_gracefully(db_session) -> None:
    """SearxNG returning [] (down / blocked / actually nothing) should
    produce a 'couldn't find anything' reply — never a verdict."""
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await SessionRepository(db_session).set_context_key(
        sid, "last_assistant_response", "X is true.",
    )
    await db_session.commit()

    class _ExtractClaim:
        async def qa(self, transcript, system_prompt=None, history=None):
            return "X is true."

    class _EmptySearx:
        async def search(self, query, max_results=5):
            return []

    with patch(
        "domovoi.handlers.double_check.get_ollama_client",
        lambda: _ExtractClaim(),
    ), patch(
        "domovoi.handlers.double_check.get_searxng_client",
        lambda: _EmptySearx(),
    ):
        handler = DoubleCheckHandler()
        ctx = Context(session_id=sid, room_id="kitchen", online=True)
        response = await handler._verify(ctx, db_session)

    assert "couldn't find" in response.text.lower()
    assert "verdict" not in response.data  # no verdict written


@requires_db
@pytest.mark.asyncio
async def test_tool_call_with_explicit_claim_skips_extraction(db_session) -> None:
    """LLM tool-routing path: when args contain `claim`, we bypass the
    extractor and verify that directly. Useful for tool calls coming
    from the LLM router with a precomputed claim."""
    extractor_calls = []

    class _CountingOllama:
        async def qa(self, transcript, system_prompt=None, history=None):
            if "extract ONE specific" in (system_prompt or ""):
                extractor_calls.append(transcript)
                return "should not be called"
            return (
                "VERDICT: CONFIRMED\n"
                "SOURCE: https://x\n"
                "REASON: ok."
            )

    with patch(
        "domovoi.handlers.double_check.get_ollama_client",
        lambda: _CountingOllama(),
    ), patch(
        "domovoi.handlers.double_check.get_searxng_client",
        lambda: SearxNGStubClient(),
    ):
        handler = DoubleCheckHandler()
        ctx = Context(session_id=None, room_id="kitchen", online=True)
        response = await handler.execute_from_tool(
            {"claim": "Water boils at 100C at sea level."}, ctx, db_session,
        )

    assert response.data["claim"] == "Water boils at 100C at sea level."
    assert response.data["verdict"] == "CONFIRMED"
    assert len(extractor_calls) == 0  # extractor bypassed
