from __future__ import annotations

import logging
import re
import time
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi import registered_values
from domovoi.clients.ollama import get_ollama_client
from domovoi.config import settings
from domovoi.confirmations import CORE_KIND_PREFIX, request_confirmation
from domovoi.db.repositories import (
    ConversationLogRepository,
    IntentLogRepository,
    MemoriesRepository,
    SessionRepository,
    WebSearchPrefsRepository,
)
from domovoi.handlers import HANDLER_BY_NAME, HANDLERS
from domovoi.handlers.base import as_fast_path
from domovoi.models import Context, Intent, Response
from domovoi.profile_context import build_profile_prefix
from domovoi.uncertainty import VOLATILE_CATEGORIES, categorize_question

log = logging.getLogger(__name__)


# Noun-phrase fallback for the volatile-question offer when the model
# returns an empty subject. Distinct from double_check._category_phrase
# (which yields adjectival fragments like "price"/"sports" for the
# "a few ___ questions" slot — those read wrong in "want me to check
# ___?"). Keyed by uncertainty category; defaults to a safe generic.
_VOLATILE_SUBJECT_FALLBACK = {
    "weather": "the weather",
    "prices_finance": "that price",
    "sports_scores": "that score",
    "current_events": "the latest on that",
    "general_recent": "the latest on that",
}


# Yes/no detection for the multi-turn confirmation flow. Tight on
# purpose — anything not clearly affirmative or negative falls through
# to normal routing rather than getting accidentally wired into a
# pending confirmation. Terminal punctuation is already stripped by
# the time we get here.
_YES_RE = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|correct|right|that's right|exactly|affirmative|"
    r"that is right|that's correct|confirmed)\b"
)
_NO_RE = re.compile(
    r"^(?:no|nope|nah|wrong|incorrect|that's wrong|not quite|not exactly)\b"
)

# Polite/filler prefixes that Whisper transcribes verbatim and would
# confound the start-anchored fast-path regexes ("please add that to
# my library" → no fast-path match → QA fallthrough → Ollama
# hallucinates that it added the song; see 2026-05-08 12:35 incident).
# Stripped AFTER the yes/no pre-empt so a bare "yes" / "yeah" can
# still claim a pending confirmation; a "yes, add that..." with no
# pending confirmation falls through here, gets stripped to "add
# that...", and the add-to-library fast path matches as expected. Repeat the
# alternation under a `+` so stacked filler ("could you please add
# ...") collapses in one substitution.
_LEADING_FILLER_RE = re.compile(
    r"^(?:"
    r"(?:hey|um|uh|so|well|alright)[,\s]+"
    r"|(?:yeah|yes|yep|ok|okay)[,\s]+"
    r"|please[,\s]+"
    r"|(?:can|could|would|will) you(?:[,\s]+please)?[,\s]+"
    r")+"
)


def _parse_yes_no(transcript: str) -> bool | None:
    """True for affirmative, False for negative, None for neither."""
    if _YES_RE.match(transcript):
        return True
    if _NO_RE.match(transcript):
        return False
    return None


async def _persist_turn(
    *,
    session: AsyncSession,
    session_id: UUID,
    intent: Intent,
    ctx: Context,
    response: Response,
    matched_handler: str | None,
    matched_path: str,
    latency_ms: int,
) -> None:
    """Centralized post-routing bookkeeping.

    Every routing path winds up writing the same three records — one
    intents_log row (routing decision), one conversation_log row (full
    user/assistant text for the audit trail and multi-turn debugging),
    and a sessions.context.recent_turns append (history threading for
    QA). Bundling them here means a future fourth sink — turn-level
    metrics, training data export, whatever — only has to be added in
    one place.

    ``matched_path`` is an OPEN enum: V001 dropped the DB CHECK so
    plugins can add values, and validation moved here — app-side,
    against the in-process registered-values registry (design §6.4).
    An unregistered value raises exactly where the CHECK used to abort.
    """
    registered_values.require("matched_path", matched_path)
    await IntentLogRepository(session).log(
        room_id=ctx.room_id,
        transcript=intent.transcript,
        matched_handler=matched_handler,
        matched_path=matched_path,
        online=ctx.online,
        latency_ms=latency_ms,
        person_id=ctx.person_id,
        presence_tier=ctx.presence_tier,
    )
    await ConversationLogRepository(session).record_turn(
        session_id=session_id,
        room_id=ctx.room_id,
        person_id=ctx.person_id,
        user_text=intent.transcript,
        assistant_text=response.text,
        matched_handler=matched_handler,
        matched_path=matched_path,
        presence_tier=ctx.presence_tier,
        online=ctx.online,
        latency_ms=latency_ms,
    )
    await SessionRepository(session).record_exchange(
        session_id,
        intent.transcript,
        response.text,
        settings.session_recent_turns_cap,
    )


async def _maybe_offer_pending_memory(
    ctx: Context, session: AsyncSession
) -> tuple[str, int] | None:
    """If the implicit extractor has parked a pending memory for the
    current speaker and it's outside the offer cooldown, return
    ``(offer_text, memory_id)`` and stamp ``last_offered_at`` so the
    same row doesn't surface every turn. Returns None if nothing's
    eligible.

    Wrapped in a broad try/except so a profile-side bug never blocks
    the QA response — failure here logs and returns None.
    """
    try:
        repo = MemoriesRepository(session)
        row = await repo.next_pending_for_offer(
            ctx.person_id,  # type: ignore[arg-type]  # caller guards
            settings.memory_extractor_offer_cooldown_sec,
        )
        if row is None:
            return None
        memory_id, body, _topic = row
        await repo.mark_offered(memory_id)
        # Conversational lead-in — keeps the offer from sounding
        # like a system prompt. Body is the LLM's extracted phrasing
        # so we trust it as-is rather than re-templating.
        offer_text = f"By the way — {body}. Should I remember that?"
        return offer_text, memory_id
    except Exception as e:
        log.warning("memory surfacing failed: %s", e)
        return None


async def route(intent: Intent, ctx: Context, session: AsyncSession) -> Response:
    # Conversational chat mode (Feature 8) is bypassed UPSTREAM of this
    # function. When a session is in ``conversational_mode`` (set by
    # ChatModeHandler into ``sessions.context``), the streaming layer reads
    # that flag in ``_process_utterance`` BEFORE calling route() and dispatches
    # the turn to Letta (or handles an exit phrase) instead — route() is never
    # invoked for a conversational turn. So command mode stays 100% on the
    # fast-path router here, and the "am I in chat mode?" check is a cheap
    # session-context read that never touches this latency-critical loop. The
    # turn that ENTERS chat mode ("let's have a chat") IS a normal command turn
    # and routes through ChatModeHandler's fast path below as usual.
    #
    # Whisper transcribes with terminal punctuation ("Play X by Y." or "Add
    # it to my library.") that fast-path regexes rarely tolerate. Strip
    # trailing punctuation alongside the lowercase/strip pass so handlers
    # don't each have to defend against it.
    transcript = intent.transcript.lower().strip().rstrip(".,!?")
    t0 = time.monotonic()

    session_repo = SessionRepository(session)
    session_id = await session_repo.get_or_create(ctx.session_id, ctx.room_id)
    ctx = ctx.model_copy(update={"session_id": session_id})

    ollama_client = get_ollama_client()

    def _elapsed_ms() -> int:
        return int((time.monotonic() - t0) * 1000)

    # ── Pending confirmation pre-empt ─────────────────────────────────
    # If the previous turn parked a pending_confirmation in the session
    # context (e.g., VoiceProfileHandler asking "did I get that right?"),
    # any clear yes/no answer routes back to that handler's
    # `handle_confirmation` method — on the Handler ABC now, dispatched
    # only for a kind the handler DECLARES in `confirmation_kinds`
    # (namespaced "core.<kind>" / "<slug>.<kind>", design §4.7). Anything
    # ambiguous lets the normal router run — the pending payload stays
    # put for one more turn so a follow-up "yes" still works.
    affirmative = _parse_yes_no(transcript)
    if affirmative is not None:
        ctx_data = await session_repo.get_context(session_id) or {}
        pending = ctx_data.get("pending_confirmation")
        if isinstance(pending, dict):
            handler = HANDLER_BY_NAME.get(pending.get("handler", ""))
            kind = str(pending.get("kind", ""))
            if (
                handler is not None
                and kind not in handler.confirmation_kinds
                and CORE_KIND_PREFIX + kind in handler.confirmation_kinds
            ):
                # A payload parked before the namespacing cutover (live
                # sessions survive deploys) — normalize instead of wedging.
                kind = CORE_KIND_PREFIX + kind
            if handler is not None and kind in handler.confirmation_kinds:
                response = await handler.handle_confirmation(
                    kind,
                    pending,
                    affirmative,
                    ctx,
                    session,
                )
                # One-shot: clear the pending payload after we route
                # to it whether the answer was yes or no, so a stray
                # "yes" next turn doesn't re-enroll the same person.
                # EXCEPT when the handler chained a new pending (e.g.
                # DoubleCheckHandler appending the prefs_offer meta-
                # question to a self_doubt_offer yes) — in that case
                # leave the fresh one alone.
                ctx_after = await session_repo.get_context(session_id) or {}
                pending_after = ctx_after.get("pending_confirmation")
                if pending_after == pending:
                    await session_repo.set_context_key(
                        session_id, "pending_confirmation", None
                    )
                response.matched_handler = response.matched_handler or handler.name
                response.matched_path = "confirmation"
                response.session_id = session_id
                response.online = ctx.online
                await _persist_turn(
                    session=session,
                    session_id=session_id,
                    intent=intent,
                    ctx=ctx,
                    response=response,
                    matched_handler=handler.name,
                    matched_path="confirmation",
                    latency_ms=_elapsed_ms(),
                )
                return response
            if handler is not None:
                # Undispatchable payload (undeclared kind) — the mediated
                # pending API makes this unreachable for well-behaved
                # parkers; log loudly and fall through to normal routing
                # (the payload stays put).
                log.warning(
                    "pending_confirmation kind %r is not declared by "
                    "handler %r (confirmation_kinds=%r) — ignoring",
                    kind, handler.name, handler.confirmation_kinds,
                )

    # Strip leading polite/filler prefixes ("please", "can you", "yeah,"
    # etc.) so anchored fast-path regexes still match conversational
    # phrasings. Done AFTER the pending-confirmation pre-empt so a bare
    # "yes"/"yeah" still routes to handle_confirmation, but BEFORE fast
    # paths so "please add that to my library" reaches its media handler
    # instead of falling through to the QA hallucination path. The LLM
    # tool-call and QA fallback paths below intentionally use
    # `intent.transcript` (raw) so the LLM sees the user's natural
    # phrasing, not the stripped version.
    transcript = _LEADING_FILLER_RE.sub("", transcript)

    # 1. Fast paths (with offline gate), over the band-sorted registry.
    #
    # Offline-gate asymmetry, re-specified (design §4.3, dossier §2.1 M4):
    # `requires_network == "yes"` auto-falls-back wholesale while offline,
    # with no per-path nuance. `"degraded"` handlers are gated PER FAST PATH —
    # only an `offline_ok=False` path auto-falls-back (unset defaults to
    # True), so a degraded handler's offline-capable paths keep running
    # and its `fallback_offline` is genuinely reachable for the rest.
    for handler in HANDLERS:
        for entry in handler.fast_paths:
            fp = as_fast_path(entry)
            m = fp.pattern.match(transcript)
            if not m:
                continue
            offline_blocked = not ctx.online and (
                handler.requires_network == "yes"
                or (
                    handler.requires_network == "degraded"
                    and fp.offline_ok is False
                )
            )
            if offline_blocked:
                response = await handler.fallback_offline(intent, ctx, session)
                response.matched_handler = handler.name
                response.matched_path = "fast_offline"
                response.session_id = session_id
                response.online = ctx.online
                await _persist_turn(
                    session=session,
                    session_id=session_id,
                    intent=intent,
                    ctx=ctx,
                    response=response,
                    matched_handler=handler.name,
                    matched_path="fast_offline",
                    latency_ms=_elapsed_ms(),
                )
                return response

            response = await fp.method(handler, m, ctx, session)
            response.matched_handler = handler.name
            response.matched_path = "fast"
            response.session_id = session_id
            response.online = ctx.online
            await _persist_turn(
                session=session,
                session_id=session_id,
                intent=intent,
                ctx=ctx,
                response=response,
                matched_handler=handler.name,
                matched_path="fast",
                latency_ms=_elapsed_ms(),
            )
            return response

    # 2. LLM tool-call fallback (the stub client returns None).
    tool_schemas = [h.tool_schema for h in HANDLERS]
    tool_call = await ollama_client.route(intent.transcript, tool_schemas)
    if tool_call is not None:
        handler = HANDLER_BY_NAME.get(tool_call.get("handler", ""))
        if handler is not None:
            path = (
                "llm_offline"
                if (handler.requires_network == "yes" and not ctx.online)
                else "llm"
            )
            if path == "llm_offline":
                response = await handler.fallback_offline(intent, ctx, session)
            else:
                response = await handler.execute_from_tool(
                    tool_call.get("args", {}), ctx, session
                )
            response.matched_handler = handler.name
            response.matched_path = path
            response.session_id = session_id
            response.online = ctx.online
            await _persist_turn(
                session=session,
                session_id=session_id,
                intent=intent,
                ctx=ctx,
                response=response,
                matched_handler=handler.name,
                matched_path=path,
                latency_ms=_elapsed_ms(),
            )
            return response

    # 3. General Q&A (Ollama is local, so always available). Pass recent
    # turns from session context so multi-turn QA actually feels stateful —
    # without history Ollama re-interprets every transcript from scratch.
    ctx_data = await session_repo.get_context(session_id)
    history = list(ctx_data.get("recent_turns") or [])

    # Proactive web-search categorizer. A category here means the
    # question is time-sensitive enough that Domovoi's training-data
    # answer may be stale; we either auto-search (known speaker has
    # opted in) or speak-then-offer.
    category = categorize_question(transcript)

    # Auto-search short-circuit: known speaker who's previously opted
    # in for this category. Skip the QA call entirely and answer from
    # SearxNG. Anonymous speakers and offline state both fall through
    # to the normal QA path.
    if (
        category is not None
        and ctx.person_id is not None
        and ctx.online
    ):
        prefs_repo = WebSearchPrefsRepository(session)
        try:
            auto = await prefs_repo.is_auto(ctx.person_id, category)
        except Exception as e:
            log.warning("web_search_prefs.is_auto failed: %s", e)
            auto = False
        if auto:
            dc = HANDLER_BY_NAME.get("double_check")
            if dc is not None and hasattr(dc, "answer_question_from_web"):
                response = await dc.answer_question_from_web(
                    intent.transcript, ctx, session
                )
                response.matched_handler = dc.name
                response.matched_path = "auto_search"
                response.session_id = session_id
                response.online = ctx.online
                await _persist_turn(
                    session=session,
                    session_id=session_id,
                    intent=intent,
                    ctx=ctx,
                    response=response,
                    matched_handler=dc.name,
                    matched_path="auto_search",
                    latency_ms=_elapsed_ms(),
                )
                return response

    # Volatile-question gate. For freshness-critical categories the
    # small QA model's confident local guess is exactly what goes stale,
    # so skip qa_with_uncertainty entirely and cut straight to a
    # subject-naming confirmation. On "yes" the existing self_doubt_offer
    # resume runs the web search with the refined query. Sits AFTER the
    # auto-search short-circuit so an opted-in speaker still auto-searches.
    if category is not None and category in VOLATILE_CATEGORIES:
        if ctx.online:
            subj = await ollama_client.extract_search_subject(
                intent.transcript, history=history,
            )
            phrase = (subj.subject or "").strip() or _VOLATILE_SUBJECT_FALLBACK.get(
                category, "that"
            )
            answer = (
                "I'd need the internet to get you that — "
                f"want me to check {phrase}?"
            )
            parked = False
            try:
                await request_confirmation(
                    session,
                    session_id,
                    kind="core.self_doubt_offer",
                    handler="double_check",
                    data={
                        "question": intent.transcript,
                        "search_query": subj.refined_query or intent.transcript,
                        # No answer was given (the whole point of the
                        # gate), so the resume "no" path knows not to
                        # say "sticking with my answer".
                        "candidate_claim": "",
                        "category": category,
                    },
                )
                parked = True
            except Exception as e:
                log.warning("couldn't park volatile self_doubt_offer: %s", e)
            if parked:
                response = Response(
                    text=answer,
                    session_id=session_id,
                    matched_handler=None,
                    matched_path="volatile_offer",
                    online=ctx.online,
                    expect_followup=True,
                )
                await _persist_turn(
                    session=session,
                    session_id=session_id,
                    intent=intent,
                    ctx=ctx,
                    response=response,
                    matched_handler=None,
                    matched_path="volatile_offer",
                    latency_ms=_elapsed_ms(),
                )
                return response
            # Park failed — fall through to the normal QA path below so we
            # don't promise an offer the next "yes" can't honor.
        else:
            # Offline: we can't search and we won't guess. Say so plainly
            # rather than emit a possibly-stale local answer.
            response = Response(
                text=(
                    "I'd need the internet to answer that, and we're "
                    "offline right now."
                ),
                session_id=session_id,
                matched_handler=None,
                matched_path="qa",
                online=ctx.online,
                expect_followup=False,
            )
            await _persist_turn(
                session=session,
                session_id=session_id,
                intent=intent,
                ctx=ctx,
                response=response,
                matched_handler=None,
                matched_path="qa",
                latency_ms=_elapsed_ms(),
            )
            return response

    # Per-speaker prompt-prefix injection. Memories +
    # favorites + selected preferences for ctx.person_id are
    # assembled into a "User context: ..." blob and prepended to the
    # QA system prompt. Anonymous speakers get ``""`` back — no-op.
    # Failure here is non-fatal: log + proceed without personalization
    # rather than break QA for a profile-side bug.
    try:
        profile_prefix = await build_profile_prefix(ctx.person_id, session)
    except Exception as e:
        log.warning("profile_prefix build failed: %s", e)
        profile_prefix = ""

    # Plain QA path — also pulls a self-doubt flag so the LLM can
    # nominate its own answers for verification (e.g. "X happened in
    # 2024 but my training data may be stale"). Either the heuristic
    # category OR the self-doubt flag triggers the offer; the offer is
    # suppressed offline because we can't actually run the search.
    qa = await ollama_client.qa_with_uncertainty(
        intent.transcript,
        history=history,
        profile_prefix=profile_prefix or None,
    )
    answer = qa.answer
    should_offer = ctx.online and (
        category is not None or qa.needs_verification
    )
    expect_followup = False
    if should_offer:
        answer = answer.rstrip() + " Want me to check that online?"
        try:
            await request_confirmation(
                session,
                session_id,
                kind="core.self_doubt_offer",
                handler="double_check",
                data={
                    "question": intent.transcript,
                    "candidate_claim": qa.candidate_claim,
                    "category": category or "general_recent",
                },
            )
            expect_followup = True
        except Exception as e:
            log.warning("couldn't park self_doubt_offer pending_confirmation: %s", e)
            # If we couldn't park the confirmation, drop the offer text
            # so we don't promise something the next "yes" can't honor.
            answer = qa.answer
    elif ctx.person_id is not None:
        # No higher-priority offer is firing — see if the implicit
        # memory extractor has parked anything to surface. Mutually
        # exclusive with the self-doubt offer above so we never park
        # two pending_confirmations at once.
        offer = await _maybe_offer_pending_memory(ctx, session)
        if offer is not None:
            offer_text, memory_id = offer
            answer = answer.rstrip() + " " + offer_text
            try:
                await request_confirmation(
                    session,
                    session_id,
                    kind="core.pending_memory_offer",
                    handler="memory",
                    data={"memory_id": memory_id},
                )
                expect_followup = True
            except Exception as e:
                log.warning(
                    "couldn't park pending_memory_offer pending_confirmation: %s", e
                )
                # Drop the offer text if we can't park it.
                answer = qa.answer

    response = Response(
        text=answer,
        session_id=session_id,
        matched_handler=None,
        matched_path="qa",
        online=ctx.online,
        expect_followup=expect_followup,
    )
    await _persist_turn(
        session=session,
        session_id=session_id,
        intent=intent,
        ctx=ctx,
        response=response,
        matched_handler=None,
        matched_path="qa",
        latency_ms=_elapsed_ms(),
    )
    return response
