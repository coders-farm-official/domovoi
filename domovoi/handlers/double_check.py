"""DoubleCheckHandler — claim verification via SearxNG + Ollama.

The flow when a user says "are you sure?" / "double check that" /
"verify that" right after the bot's previous response:

    1. Pull `last_assistant_response` from sessions.context (populated
       by SessionRepository.record_exchange on every routed turn).
    2. Ask Ollama to extract one verifiable factual claim from that
       response. Many responses have nothing to check ("OK, playing
       music." / "It's 3 PM." / "I don't know.") — we reply gracefully
       in those cases.
    3. Query SearxNG for the claim (locally hosted metasearch — see
       `docker-compose.yml` `searxng` service).
    4. Re-prompt Ollama with the claim + top search results to render
       a verdict: CONFIRMED / REFUTED / AMBIGUOUS, with a short reason
       and a source URL.
    5. Speak the verdict in voice-friendly form. URLs are saved into
       Response.data for the audit trail rather than read aloud.

Risk callouts the prompts try to mitigate:
* The LLM rubber-stamping its own previous claim when results are
  ambiguous — the verifier prompt explicitly demands "AMBIGUOUS" for
  weak evidence.
* Multi-claim responses — v1 picks the first claim only. The
  extraction prompt asks for ONE claim, the most concrete one. Adding
  multi-claim summarization is a clean follow-up.

Network: `requires_network="yes"` because SearxNG aggregates public
search engines. `fallback_offline` returns a graceful "I can't check"
rather than hallucinating a verdict — explicitly NEVER claim
something is verified when offline.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.clients.ollama import get_ollama_client
from domovoi.clients.searxng import SearchResult, get_searxng_client
from domovoi.config import settings
from domovoi.db.repositories import SessionRepository, WebSearchPrefsRepository
from domovoi.confirmations import request_confirmation
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

# Threshold of yeses in a category before Domovoi offers "want me to
# always check these?". Conservative default — see the user-facing
# rationale at the top of V010__web_search_prefs.sql.
AUTO_SEARCH_OFFER_THRESHOLD = 3

log = logging.getLogger(__name__)


# ─── Fast-path regexes ────────────────────────────────────────────────────
# Anchored to clear verification triggers — must NOT poach phrasings
# like "are you sure you want to do that?" (different intent) or
# "verify the timer" (different handler). The leading "(?:can|could)
# you" optional, "is/are that/this" optional in the middle, etc.

_VERIFY_RE = re.compile(
    r"^(?:"
    r"(?:can |could |would )?you (?:double[- ]?check|verify|fact[- ]?check) (?:that|this|it)?"
    r"|double[- ]?check (?:that|this|it)?"
    r"|verify (?:that|this|it)"
    r"|fact[- ]?check (?:that|this|it)?"
    r"|are you sure(?: about that)?"
    r"|is that (?:right|correct|true|accurate)"
    r"|really\??"
    r")$"
)


# ─── Prompts ──────────────────────────────────────────────────────────────

_EXTRACT_SYSTEM_PROMPT = (
    "You are a fact-checking assistant. Given an assistant's previous "
    "spoken response, extract ONE specific factual claim from it that "
    "could be verified by web search. Output ONLY the claim, as a "
    "single declarative sentence — no prefix, no quotes, no "
    "explanation. If the response contains nothing factually verifiable "
    "(greetings, opinions, system status, refusals, time-of-day, "
    "subjective comments), output exactly the single word: NONE"
)

_ANSWER_FROM_SOURCES_SYSTEM_PROMPT = (
    "You are a voice assistant answering a question using ONLY the "
    "search results provided. Output in this exact format with each "
    "field on its own line:\n"
    "ANSWER: <one or two short conversational sentences answering the "
    "question, suitable to be spoken aloud. No markdown. No URLs in "
    "this field.>\n"
    "SOURCE: <one URL from the results that best supports the answer, "
    "or NONE>\n"
    "\n"
    "Units: the user is in the United States and expects US units — "
    "temperatures in Fahrenheit, not Celsius. If a source gives a "
    "temperature in Celsius, CONVERT it to Fahrenheit (F = C * 9/5 + 32) "
    "before answering. Always state the unit, and NEVER copy a number from "
    "a source under a different unit label than the source used — reporting "
    "a Celsius value as Fahrenheit (or vice versa) is an error.\n"
    "\n"
    "If the results don't actually answer the question, say so plainly "
    "in ANSWER (\"The search didn't turn up a clear answer.\") and put "
    "NONE as the SOURCE. Don't speculate beyond what the results say."
)


_VERIFY_SYSTEM_PROMPT = (
    "You are a fact-checking judge. Given a claim and the top web "
    "search results about it, output a verdict in this exact format "
    "with each field on its own line:\n"
    "VERDICT: CONFIRMED | REFUTED | AMBIGUOUS\n"
    "SOURCE: <one URL from the results that supports the verdict, or NONE>\n"
    "REASON: <one short sentence in your own words>\n"
    "\n"
    "Use AMBIGUOUS when the search results don't clearly support or "
    "refute the claim, or when sources conflict, or when the claim is "
    "too vague to verify. Don't rubber-stamp the claim if the results "
    "are weak — AMBIGUOUS is the honest answer."
)


def _category_phrase(category: str) -> str:
    """Voice-friendly phrasing for a web_search_prefs category.

    Used in the auto-search meta-offer ("you've had me check a few
    <phrase> questions"). Falls back to a generic phrasing for any
    unknown category so a forgotten-to-add-case here never produces
    awkward "you've had me check a few current_events questions"
    voice output.
    """
    return {
        "current_events": "current-events",
        "prices_finance": "price",
        "sports_scores": "sports",
        "general_recent": "recent-info",
    }.get(category, "time-sensitive")


def _format_results_for_prompt(results: list[SearchResult]) -> str:
    """Compact bullet list of top search results for the verifier
    prompt. Cap content snippets so the prompt stays bounded — Ollama
    handles a few KB fine but very long inputs slow first-token."""
    lines: list[str] = []
    for i, r in enumerate(results[:5], start=1):
        snippet = (r.content or "").replace("\n", " ").strip()[:300]
        lines.append(f"{i}. {r.title} — {snippet}\n   ({r.url})")
    return "\n".join(lines) if lines else "(no results)"


def _parse_verdict(text: str) -> tuple[str, str | None, str | None]:
    """Pull (verdict, source_url, reason) out of the verifier's
    structured output. Falls back to AMBIGUOUS on parse failure rather
    than claiming a definitive verdict.

    The model occasionally drops the format under pressure (long
    inputs, ambiguous sources). When that happens we'd rather respond
    "I can't quite tell" than pick a verdict by accident.
    """
    verdict = "AMBIGUOUS"
    source: str | None = None
    reason: str | None = None

    for line in (text or "").splitlines():
        line = line.strip()
        if line.upper().startswith("VERDICT:"):
            v = line.split(":", 1)[1].strip().upper()
            if v.startswith("CONFIRMED"):
                verdict = "CONFIRMED"
            elif v.startswith("REFUTED"):
                verdict = "REFUTED"
            elif v.startswith("AMBIGUOUS"):
                verdict = "AMBIGUOUS"
        elif line.upper().startswith("SOURCE:"):
            s = line.split(":", 1)[1].strip()
            if s and s.upper() != "NONE":
                source = s
        elif line.upper().startswith("REASON:"):
            r = line.split(":", 1)[1].strip()
            if r:
                reason = r

    return verdict, source, reason


def _parse_answer_from_sources(text: str) -> tuple[str, str | None]:
    """Pull (answer, source_url) out of the answer-from-sources prompt.

    Falls back to returning the raw text as the answer and no source
    on parse failure — better to speak something than to silently
    blank out when the model drops the format.
    """
    answer = ""
    source: str | None = None
    for line in (text or "").splitlines():
        line = line.strip()
        if line.upper().startswith("ANSWER:"):
            v = line.split(":", 1)[1].strip()
            if v:
                answer = v
        elif line.upper().startswith("SOURCE:"):
            s = line.split(":", 1)[1].strip()
            if s and s.upper() != "NONE":
                source = s
    if not answer:
        # Model dropped the format — speak whatever it produced rather
        # than nothing.
        answer = (text or "").strip()
    return answer, source


def _format_voice_response(
    verdict: str, claim: str, reason: str | None, source: str | None
) -> str:
    """Voice-friendly verdict. URLs aren't spoken — they belong in the
    Response.data audit trail. Reason is the one sentence the user
    actually wants to hear."""
    if verdict == "CONFIRMED":
        head = "Yes, that checks out."
    elif verdict == "REFUTED":
        head = "Actually no, that doesn't check out."
    else:
        head = "I'm not sure either way."
    if reason:
        return f"{head} {reason}"
    if verdict == "AMBIGUOUS":
        return f"{head} The search results were inconclusive."
    return head


# ─── Handler ──────────────────────────────────────────────────────────────


class DoubleCheckHandler(Handler):
    name = "double_check"
    # band rationale: before anything matching "verify" / "are you sure" — currently
    #   nothing else does, but this band is the right place if media
    #   handlers ever grow such phrasings.
    priority_band = 190
    display = HandlerDisplay(label="Double-Check", tone="info")
    confirmation_kinds = ("core.self_doubt_offer", "core.prefs_offer")
    requires_network = "yes"

    tool_schema = {
        "name": "double_check",
        "description": (
            "Verify a factual claim from the assistant's previous spoken "
            "response by web search. Use when the user explicitly asks "
            "for fact-checking ('are you sure?', 'double check that', "
            "'verify that')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "claim": {
                    "type": "string",
                    "description": (
                        "The specific claim to verify. If absent, the "
                        "handler pulls the assistant's last response from "
                        "session context."
                    ),
                },
            },
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_VERIFY_RE, DoubleCheckHandler._from_match),
        ]

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        return Response(
            text="What should I double-check?",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        claim = (args.get("claim") or "").strip()
        if claim:
            return await self._verify_claim_directly(claim, ctx, session)
        return await self._verify(ctx, session)

    async def fallback_offline(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        return Response(
            text="I can't check that right now — I don't have internet.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            matched_path="fast_offline",
        )

    # ─── Fast-path adapter ────────────────────────────────────────────
    async def _from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._verify(ctx, session)

    # ─── Proactive offer (router QA fallthrough) ──────────────────────
    async def handle_confirmation(
        self,
        kind: str,
        data: dict,
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        """Resume the proactive web-search flow parked by the router.

        Two ``kind`` values land here:

        * ``self_doubt_offer`` — the QA fallthrough emitted an answer
          plus "want me to check that online?". Yes runs the search +
          synthesizes a fresh answer with a citation; no acknowledges
          and moves on. Yeses also bump the per-speaker counter and
          may surface ``prefs_offer``.

        * ``prefs_offer`` — after the speaker has said yes
          ``AUTO_SEARCH_OFFER_THRESHOLD`` times in a category, the
          previous turn asked whether to start auto-searching that
          category. Yes flips ``web_search_prefs.auto_search``; no
          is recorded so we never ask again for that category.
        """
        if kind == "core.self_doubt_offer":
            return await self._handle_self_doubt_offer(data, affirmative, ctx, session)
        if kind == "core.prefs_offer":
            return await self._handle_prefs_offer(data, affirmative, ctx, session)
        return Response(
            text="Got it.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _handle_self_doubt_offer(
        self,
        data: dict,
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        category = str(data.get("category") or "")
        # Prefer the refined query parked by the volatile gate; fall back
        # to the raw transcript for older self_doubt_offer parks that
        # never carried a search_query key.
        search = str(data.get("search_query") or data.get("question") or "").strip()

        # Update the per-speaker pref counter for known speakers. Anon
        # speakers don't accumulate (no FK target) and just see the
        # offer every time.
        new_yes_count = 0
        if ctx.person_id is not None and category:
            prefs_repo = WebSearchPrefsRepository(session)
            try:
                yes_c, _no_c = await prefs_repo.record_offer_response(
                    ctx.person_id, category, affirmative
                )
                new_yes_count = yes_c
            except Exception as e:
                log.warning("web_search_prefs counter update failed: %s", e)

        if not affirmative:
            # The volatile gate parks no candidate_claim (no answer was
            # ever spoken), so "sticking with my answer" would be a lie.
            # Only claim an answer when one actually existed.
            had_answer = bool(str(data.get("candidate_claim") or "").strip())
            return Response(
                text=(
                    "OK, sticking with my answer."
                    if had_answer
                    else "OK, I won't look it up."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        if not ctx.online:
            # User said yes but we lost connectivity between turns —
            # fall back to the standard offline message rather than
            # claiming to have searched.
            return Response(
                text="I can't check that right now — I don't have internet.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        if not search:
            return Response(
                text=(
                    "I lost track of what you wanted me to check. Ask "
                    "again and I'll look it up."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        response = await self._answer_question_from_web(search, ctx, session)

        # If this yes crossed the threshold and we haven't asked yet,
        # tack on the prefs offer and park a follow-up confirmation.
        if (
            ctx.person_id is not None
            and category
            and new_yes_count >= AUTO_SEARCH_OFFER_THRESHOLD
        ):
            await self._maybe_offer_prefs_followup(category, ctx, session, response)

        return response

    async def _maybe_offer_prefs_followup(
        self,
        category: str,
        ctx: Context,
        session: AsyncSession,
        response: Response,
    ) -> None:
        """Append the "want me to always check these?" question to
        ``response.text`` and park a prefs_offer pending_confirmation,
        but only if we haven't already asked for this (person, category)."""
        prefs_repo = WebSearchPrefsRepository(session)
        existing = await prefs_repo.get(ctx.person_id or 0, category)
        if existing is None:
            return
        _auto, _yes, _no, prefs_offered_at = existing
        if prefs_offered_at is not None:
            return  # already asked — don't pester
        if _auto:
            return  # already auto-searching; nothing to offer

        category_phrase = _category_phrase(category)
        response.text = (
            response.text
            + " By the way, you've had me check a few "
            + category_phrase
            + " questions — want me to do that automatically from now on?"
        )
        try:
            await prefs_repo.mark_prefs_offered(ctx.person_id or 0, category)
        except Exception as e:
            log.warning("mark_prefs_offered failed: %s", e)
        try:
            await request_confirmation(
                session,
                ctx.session_id,
                kind="core.prefs_offer",
                handler=self.name,
                data={"category": category},
            )
        except Exception as e:
            log.warning("couldn't park prefs_offer pending_confirmation: %s", e)
        # Signal the streaming layer to capture the yes/no without
        # requiring the wake word.
        response.expect_followup = True

    async def _handle_prefs_offer(
        self,
        data: dict,
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        category = str(data.get("category") or "")
        if ctx.person_id is None or not category:
            return Response(
                text="Got it.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        prefs_repo = WebSearchPrefsRepository(session)
        try:
            await prefs_repo.set_auto(ctx.person_id, category, affirmative)
        except Exception as e:
            log.warning("set_auto failed for (%s, %s): %s", ctx.person_id, category, e)
        if affirmative:
            return Response(
                text=(
                    "OK, I'll check "
                    + _category_phrase(category)
                    + " questions online automatically from now on."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return Response(
            text="OK, I'll keep asking first.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    # ─── Question-mode answer-from-web (proactive offer + auto-search) ─
    async def answer_question_from_web(
        self, question: str, ctx: Context, session: AsyncSession
    ) -> Response:
        """Public entry for the router's auto-search short-circuit.

        Same shape as ``_answer_question_from_web`` but with a clean
        public name. The router calls this when ``web_search_prefs.
        auto_search = TRUE`` for the matched category; the
        ``self_doubt_offer`` confirmation path calls the underscore
        version internally.
        """
        return await self._answer_question_from_web(question, ctx, session)

    async def _answer_question_from_web(
        self, question: str, ctx: Context, session: AsyncSession
    ) -> Response:
        results = await self._search(question)
        if not results:
            return Response(
                text=(
                    "I checked online but couldn't find a clear answer to that."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
                data={"question": question, "results": []},
            )
        answer, source = await self._answer_from_sources(question, results)
        return Response(
            text=answer,
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={
                "question": question,
                "source": source,
                "results": [r.to_dict() for r in results],
            },
        )

    async def _answer_from_sources(
        self, question: str, results: list[SearchResult]
    ) -> tuple[str, str | None]:
        """Re-prompt Ollama with question + results → spoken answer + source.

        Defaults to a graceful fallback on any failure so the user always
        gets *something* spoken back rather than dead air.
        """
        client = get_ollama_client()
        prompt = (
            f"Question: {question}\n\n"
            f"Search results:\n{_format_results_for_prompt(results)}\n"
        )
        try:
            raw = await client.qa(
                transcript=prompt,
                system_prompt=_ANSWER_FROM_SOURCES_SYSTEM_PROMPT,
            )
        except Exception as e:
            log.warning("answer_from_sources: ollama call failed: %s", e)
            return ("I checked online but had trouble pulling the answer together.", None)
        return _parse_answer_from_sources(raw or "")

    # ─── Core flow ────────────────────────────────────────────────────
    async def _verify(self, ctx: Context, session: AsyncSession) -> Response:
        """The main fact-check path: pull last_assistant_response,
        extract a claim, search, judge."""
        last = await self._read_last_assistant_response(ctx, session)
        if not last:
            return Response(
                text=(
                    "I don't have anything recent to double-check — ask me "
                    "something first, then I'll fact-check the answer."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        claim = await self._extract_claim(last)
        if not claim:
            return Response(
                text=(
                    "There's nothing in that response to fact-check — it "
                    "wasn't a factual claim I can look up."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
                data={"last_assistant_response": last},
            )

        return await self._verify_claim_directly(claim, ctx, session)

    async def _verify_claim_directly(
        self, claim: str, ctx: Context, session: AsyncSession
    ) -> Response:
        """Tool-call entry point + the back half of the fast-path flow:
        we already have a claim, search and judge it."""
        results = await self._search(claim)
        if not results:
            return Response(
                text=(
                    "I couldn't find anything about that to confirm or "
                    "deny it."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
                data={"claim": claim, "results": []},
            )

        verdict, source, reason = await self._judge_claim(claim, results)
        text = _format_voice_response(verdict, claim, reason, source)
        return Response(
            text=text,
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={
                "claim": claim,
                "verdict": verdict,
                "source": source,
                "reason": reason,
                "results": [r.to_dict() for r in results],
            },
        )

    # ─── Helpers ──────────────────────────────────────────────────────
    async def _read_last_assistant_response(
        self, ctx: Context, session: AsyncSession
    ) -> str | None:
        if ctx.session_id is None:
            return None
        try:
            ctx_data = await SessionRepository(session).get_context(ctx.session_id)
        except Exception as e:
            log.warning("DoubleCheck: get_context failed: %s", e)
            return None
        if not isinstance(ctx_data, dict):
            return None
        last = ctx_data.get("last_assistant_response")
        if not isinstance(last, str) or not last.strip():
            return None
        return last.strip()

    async def _extract_claim(self, last_response: str) -> str | None:
        """Use Ollama (qa model) to pull one verifiable claim out of
        the previous response. Returns None when nothing's verifiable
        (model output is "NONE" or empty/garbled).
        """
        client = get_ollama_client()
        try:
            raw = await client.qa(
                transcript=last_response,
                system_prompt=_EXTRACT_SYSTEM_PROMPT,
            )
        except Exception as e:
            log.warning("DoubleCheck: claim extraction failed: %s", e)
            return None
        if not raw:
            return None
        cleaned = raw.strip().strip('"').strip("'").strip()
        if not cleaned or cleaned.upper().startswith("NONE"):
            return None
        # Bounded length — extracted claim shouldn't be a paragraph.
        # Trim defensively in case the model ignored the system prompt.
        return cleaned[:300]

    async def _search(self, claim: str) -> list[SearchResult]:
        client = get_searxng_client()
        try:
            return await client.search(claim, max_results=5)
        except Exception as e:
            log.warning("DoubleCheck: SearxNG search failed: %s", e)
            return []

    async def _judge_claim(
        self, claim: str, results: list[SearchResult]
    ) -> tuple[str, str | None, str | None]:
        """Re-prompt Ollama with claim + results → structured verdict.
        Defaults to AMBIGUOUS on any failure so we never falsely
        confirm something on a parse / network hiccup."""
        client = get_ollama_client()
        prompt = (
            f"Claim: {claim}\n\n"
            f"Search results:\n{_format_results_for_prompt(results)}\n"
        )
        try:
            raw = await client.qa(
                transcript=prompt,
                system_prompt=_VERIFY_SYSTEM_PROMPT,
            )
        except Exception as e:
            log.warning("DoubleCheck: verdict generation failed: %s", e)
            return "AMBIGUOUS", None, None
        return _parse_verdict(raw or "")
