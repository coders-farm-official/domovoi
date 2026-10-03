from __future__ import annotations

import functools
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi import registered_values
from domovoi.clients.mpd import MPDNotProvisioned
from domovoi.clients.ollama import QAWithUncertainty, get_ollama_client
from domovoi.config import settings
from domovoi.confirmations import (
    CORE_KIND_PREFIX,
    PENDING_CONFIRMATION_KEY,
    request_confirmation,
    take_parked_choice,
)
from domovoi.db.repositories import (
    ConversationLogRepository,
    IntentLogRepository,
    MemoriesRepository,
    SessionRepository,
    WebSearchPrefsRepository,
)
from domovoi.handlers import HANDLER_BY_NAME, HANDLERS
from domovoi.handlers.base import FastPath, Handler, as_fast_path
from domovoi.handlers.shared.tool_gate import about_the_house, answers_without_tools
from domovoi.models import Context, Intent, Response
from domovoi.profile_context import build_profile_prefix
from domovoi.spoken_answer import SpokenAnswer, StreamedQA
from domovoi.turn_timings import timings_for_row
from domovoi.uncertainty import (
    VOLATILE_CATEGORIES,
    answer_admits_staleness,
    answer_offers_lookup,
    categorize_question,
    looks_like_question,
)

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


# Spoken when the QA model can't be reached — `qa_with_uncertainty`
# reports ``unreachable`` only when its Ollama calls themselves failed.
# Mirrors the plain wording of the volatile gate's offline line: state
# what's broken, then what still works, so the user can tell a dead
# language model from a dead satellite.
_LLM_UNREACHABLE_TEXT = (
    "My language model isn't answering right now — timers, music and "
    "the clock still work."
)

# Spoken when the model answered but said nothing usable (an empty reply
# twice running). The model is up, so the outage line above would be a
# lie; this one is true and short. Office row #187 on 2026-09-28 was this
# case announced as an outage: Domovoi's own greeting "Back so soon?"
# came back through the mic, llama3.2:3b returned an empty answer to it
# in under a second, and the user heard that the model was down.
_NO_ANSWER_TEXT = "Sorry, I don't have an answer for that."

_ONLINE_CHECK_OFFER = "Want me to check that online?"

# Said the moment a voice turn needs a model Ollama hasn't loaded, so the
# room isn't silent through the load (~54 s on an all-CPU host).
_COLD_START_TEXT = "Just a moment, I'm waking up my language model."


async def _ready_for(client, ctx: Context, role: str):
    """The Ollama client to use for the ``role`` ("tool" or "qa") model on
    this turn. When that model isn't loaded, this is a cold start, not an
    outage: say so in the room (``ctx.speak_interim``, once per turn, if
    ``ollama_cold_start_notice``), and hand back the client's cold-start
    twin, whose read timeout covers a model load — the ordinary timeout
    could otherwise end a slow load in "my language model isn't
    answering". Clients without the cold-start API (the stub, test fakes)
    are returned as they are; so is every client when Ollama can't be
    asked what's loaded, which is not a cold start."""
    check = getattr(client, "cold_models", None)
    if check is None:
        return client
    try:
        cold = await check(role)
    except Exception as e:  # noqa: BLE001 — never let the probe fail a turn
        log.debug("cold-model check failed: %s", e)
        return client
    if not cold:
        return client
    log.info(
        "cold start in room=%s: %s not loaded in Ollama — loading it for this turn",
        ctx.room_id, ", ".join(cold),
    )
    speak = getattr(ctx, "speak_interim", None)
    if speak is not None and settings.ollama_cold_start_notice:
        try:
            await speak(_COLD_START_TEXT)
        except Exception as e:  # noqa: BLE001
            log.warning("cold-start notice failed: %s", e)
    twin = getattr(client, "for_cold_start", None)
    return twin() if callable(twin) else client


def _end_sentence(text: str) -> str:
    """``text`` closed with a full stop if it doesn't already end a
    sentence, so an offer appended after it is heard as its own sentence
    ("…to answer that. Want me to check…", not "…to answer that Want me
    to check…")."""
    text = text.rstrip()
    if not text:
        return text
    tail = text.rstrip("\"'”’)»")
    if tail and tail[-1] in ".!?…":
        return text
    return text + "."


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


# ─── The fast tiers, as pure functions ─────────────────────────────────────
# route() below is built on these, and so is plan_route(), which says what
# route() would do with a transcript without doing any of it — the
# satellite early-commit check (domovoi/early_commit.py) asks it while the
# person may still be talking. One implementation, so the two can't drift.


def normalize_transcript(raw: str) -> str:
    """Whisper transcribes with terminal punctuation ("Play X by Y." or
    "Add it to my library.") that fast-path regexes rarely tolerate. Strip
    trailing punctuation alongside the lowercase/strip pass so handlers
    don't each have to defend against it."""
    return raw.lower().strip().rstrip(".,!?")


def strip_leading_filler(transcript: str) -> str:
    """Drop leading politeness ("please", "can you", "yeah,") so the
    anchored fast-path regexes still match (see _LEADING_FILLER_RE)."""
    return _LEADING_FILLER_RE.sub("", transcript)


def first_fast_path(
    transcript: str, handlers: Iterable[Handler] | None = None
) -> tuple[Handler, FastPath, re.Match[str]] | None:
    """The first fast path, in band order, that matches ``transcript``
    (normalized and filler-stripped), with its match — or None."""
    for handler in HANDLERS if handlers is None else handlers:
        for entry in handler.fast_paths:
            fp = as_fast_path(entry)
            m = fp.pattern.match(transcript)
            if m:
                return handler, fp, m
    return None


def dispatchable_confirmation(
    pending: Any, *, warn: bool = False
) -> tuple[Handler, str] | None:
    """The handler and (namespaced) kind a parked ``pending_confirmation``
    payload resumes, or None when it resumes nothing."""
    if not isinstance(pending, dict):
        return None
    handler = HANDLER_BY_NAME.get(pending.get("handler", ""))
    if handler is None:
        return None
    kind = str(pending.get("kind", ""))
    if (
        kind not in handler.confirmation_kinds
        and CORE_KIND_PREFIX + kind in handler.confirmation_kinds
    ):
        # A payload parked before the namespacing cutover (live
        # sessions survive deploys) — normalize instead of wedging.
        kind = CORE_KIND_PREFIX + kind
    if kind in handler.confirmation_kinds:
        return handler, kind
    if warn:
        # Undispatchable payload (undeclared kind) — the mediated
        # pending API makes this unreachable for well-behaved
        # parkers; log loudly and fall through to normal routing
        # (the payload stays put).
        log.warning(
            "pending_confirmation kind %r is not declared by "
            "handler %r (confirmation_kinds=%r) — ignoring",
            kind, handler.name, handler.confirmation_kinds,
        )
    return None


def dispatchable_choice(pending: Any) -> tuple[Handler, str] | None:
    """Like :func:`dispatchable_confirmation`, but only for a parked CHOICE
    — a kind its handler also lists in ``choice_kinds``, whose reply may be
    free text (``Handler.handle_choice_reply``)."""
    target = dispatchable_confirmation(pending)
    if target is None or target[1] not in target[0].choice_kinds:
        return None
    return target


def offline_blocked(handler: Handler, fp: FastPath, ctx: Context) -> bool:
    """Whether the offline gate sends this fast path to the handler's
    ``fallback_offline`` instead (see route(), step 1)."""
    return not ctx.online and (
        handler.requires_network == "yes"
        or (handler.requires_network == "degraded" and fp.offline_ok is False)
    )


async def run_fast_path(
    handler: Handler,
    fp: FastPath,
    m: re.Match[str],
    ctx: Context,
    session: AsyncSession,
) -> Response:
    """Dispatch one fast path the way route() does — the offline gate, and
    a room with no music player answered instead of raised — for a handler
    that hands a reply on to another command (a parked choice answered
    "no, play X"). The caller stamps and records the turn."""
    if offline_blocked(handler, fp, ctx):
        intent = Intent(transcript=m.string, room_id=ctx.room_id, session_id=ctx.session_id)
        return await handler.fallback_offline(intent, ctx, session)
    try:
        return await fp.method(handler, m, ctx, session)
    except MPDNotProvisioned:
        return _no_speakers_yet(ctx.session_id, ctx)


@dataclass(frozen=True)
class RoutePlan:
    """What route() would do with a transcript on its fast tiers: resume a
    parked confirmation (``path="confirmation"``) or dispatch a fast path
    (``path="fast"``). ``transcript`` is the text the decision was made on."""

    path: str
    handler: Handler
    transcript: str
    fast_path: FastPath | None = None
    match: re.Match[str] | None = None
    kind: str | None = None


def plan_route(raw_transcript: str, *, pending: Any = None) -> RoutePlan | None:
    """route()'s decision for ``raw_transcript`` on its fast tiers, worked
    out without dispatching anything: no handler runs, nothing is read or
    written. ``pending`` is the session's parked ``pending_confirmation``
    payload (the caller reads it; None when there is none), so a yes/no
    answer plans as the confirmation route() would resume. None means the
    turn would go on to the language model. The offline gate is not
    applied: it changes what the matched path does, not which path it is.
    """
    transcript = normalize_transcript(raw_transcript)
    if pending is not None and _parse_yes_no(transcript) is not None:
        target = dispatchable_confirmation(pending)
        if target is not None:
            return RoutePlan(
                path="confirmation", handler=target[0], transcript=transcript,
                kind=target[1],
            )
    transcript = strip_leading_filler(transcript)
    hit = first_fast_path(transcript)
    if hit is None:
        return None
    handler, fp, m = hit
    return RoutePlan(path="fast", handler=handler, transcript=transcript, fast_path=fp, match=m)


def goes_to_the_tool_model(raw_transcript: str) -> bool:
    """Whether route() would hand ``raw_transcript`` to the tool model (the
    LLM router): something in it, not a yes/no answer (a parked question's
    reply, or a bare "no"), no fast path, and not a plain question or Q&A
    request (``answers_without_tools``). Pure, like :func:`plan_route`.

    The voice turn's second hearing asks it (``streaming``): a short-window
    transcript headed there is either a command the short decode misheard
    or a turn about to spend seconds in the tool router, so it is decoded
    again on the 30 s path first. A fast-path command or a plain question
    keeps its short-window speed."""
    transcript = normalize_transcript(raw_transcript)
    if not any(ch.isalnum() for ch in transcript):
        return False
    if _parse_yes_no(transcript) is not None:
        return False
    transcript = strip_leading_filler(transcript)
    if first_fast_path(transcript) is not None:
        return False
    return not answers_without_tools(transcript)


# The shortest question a second hearing is skipped for (see below): two-
# and three-word ones are as often a short command misheard — "What
# plane?" and "What's true?" were "What's playing?" on far-field audio.
WORLD_QUESTION_MIN_WORDS = 4


def is_question_about_the_world(raw_transcript: str) -> bool:
    """A question — Whisper ended it with "?" — of at least
    ``WORLD_QUESTION_MIN_WORDS`` words with nothing about the house, the
    speaker, the assistant or something pointed at in it (the vocabulary
    ``tool_gate`` keeps): "What is the capital of France?", "How far away
    is the moon?". It goes to the tool model like any unmatched turn, but
    streamed the router gives it up in ~0.7 s; hearing it again would add
    a whole 30 s decode (~0.9-1.3 s) to a question no command was
    misheard as. On the review's far-field corpus, skipping the second
    hearing for these lost nothing in any of six conditions."""
    raw = (raw_transcript or "").strip()
    if not raw.endswith("?"):
        return False
    # Not filler-stripped: "could you put on something relaxing?" is a
    # request to the assistant, and its "you" is what says so.
    transcript = normalize_transcript(raw)
    if len(transcript.split()) < WORLD_QUESTION_MIN_WORDS:
        return False
    return not about_the_house(transcript)


# A request for something only the Q&A model makes up, however its article
# was heard: "Tell me it a joke.", "Tell me the joke." (small.en on two of
# three TTS voices asking for "a joke"). answers_without_tools wants the
# article exactly — "tell me the stories" is the news — but for the second
# hearing the question is only whether another decode could turn it into
# a command, and none has this shape.
_QA_REQUEST_LOOSE_RE = re.compile(
    r"^(?:tell|give|say|share|read)(?: me| us)?\b.*"
    r"\b(?:jokes?|riddles?|stor(?:y|ies)|poems?|limericks?|puns?|tongue twisters?)\b"
)


def is_request_for_a_story(raw_transcript: str) -> bool:
    """"Tell me it a joke.", "give us the riddle": a request for a joke, a
    riddle, a story or a poem, whatever else was heard in it. The second
    hearing spares it — on the review's far-field corpus that lost nothing
    — and the router still decides where it goes."""
    return bool(_QA_REQUEST_LOOSE_RE.match(strip_leading_filler(normalize_transcript(raw_transcript or ""))))


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
    # A voice turn's stage timings (speech-to-text, voice identification,
    # the capture length) go in with the row, in this same transaction;
    # the stages after routing are merged into it by id once the reply is
    # playing (domovoi/turn_timings.py). `latency_ms` stays what it always
    # was: route() alone.
    timings_doc = await timings_for_row(session, ctx.timings)
    row_id = await IntentLogRepository(session).log(
        room_id=ctx.room_id,
        transcript=intent.transcript,
        matched_handler=matched_handler,
        matched_path=matched_path,
        online=ctx.online,
        latency_ms=latency_ms,
        person_id=ctx.person_id,
        presence_tier=ctx.presence_tier,
        timings=timings_doc,
    )
    if timings_doc is not None:
        ctx.timings.intents_log_id = row_id
    await ConversationLogRepository(session).record_turn(
        session_id=session_id,
        room_id=ctx.room_id,
        person_id=ctx.person_id,
        user_text=intent.transcript,
        assistant_text=response.text,
        matched_handler=matched_handler,
        matched_path=matched_path,
        presence_tier=ctx.presence_tier,
        trigger=ctx.trigger,
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


def _no_speakers_yet(session_id: UUID | None, ctx: Context) -> Response:
    """Audio was asked for before any satellite has ever connected.

    Per-room MPD daemons are provisioned lazily on a satellite's first
    connect, so on a fresh install there is nowhere to play. That is an
    ordinary first-run state — reachable simply by exercising
    ``/v1/intent`` before building a Pi — not a fault, so answer it the way
    a handler would instead of raising into a 500.
    """
    return Response(
        text=(
            "I can't play anything yet — no satellite has connected, so "
            "there are no speakers set up in any room."
        ),
        session_id=session_id,
        online=ctx.online,
        failure="no_speakers",
    )


def _has_tool_gate(handler: Handler) -> bool:
    return type(handler).offers_tool is not Handler.offers_tool


# Gated tools that keep their band place among the ungated ones. The
# reminder and timer gates withhold only on talk about a timer or reminder,
# which never reaches the router (answers_without_tools sends it to the Q&A
# model), so no routed turn's list changes with them — and they sit where
# the latency work measured the list: moved to the end with the gated
# tools, qwen3:8b answered "Can you check online?" from Q&A instead of
# calling double_check (2026-09-30, the live tool list).
_GATES_KEEP_BAND_PLACE = frozenset({"reminder", "timer"})


def _tool_order(handler: Handler) -> int:
    """0 for an ungated tool (or one in ``_GATES_KEEP_BAND_PLACE``), 1 for
    a gated tool that is on offer unless an utterance rules it out
    (calculator, library), 2 for one that is only offered when the
    utterance asks for it (double_check, news). An empty transcript tells
    the two gated kinds apart: it rules nothing out and asks for
    nothing."""
    if not _has_tool_gate(handler) or handler.name in _GATES_KEEP_BAND_PLACE:
        return 0
    try:
        return 1 if handler.offers_tool("") else 2
    except Exception:  # noqa: BLE001 — a broken plugin gate sorts last
        return 2


def offered_tool_schemas(transcript: str) -> list[dict]:
    """The tool schemas the LLM router is offered for this (normalized)
    transcript — every registered handler's ``tool_schema``, minus the
    ones whose ``offers_tool`` says the utterance can't be theirs.

    Order (band order within each group): ungated tools, then gated tools
    that are usually on offer, then the ones offered only on request. The
    tool list is rendered into the prompt prefix that Ollama's KV cache
    reuses between requests, and on a CPU host re-reading it costs ~12 ms
    a token: everything after the first tool that differs from the last
    turn is read again. Kept this way, the common changes only touch the
    end — a verification or news request appends its tool (~200 tokens,
    not the ~900 behind it), and the next ordinary turn finds the old
    prefix intact. The usually-offered ones (calculator, library) are
    withheld only from a plain question about the world, which doesn't
    reach the router at all (``answers_without_tools``): every turn that
    does reach it — a who/why/where question about the house included —
    sees them, so the list only ever changes at its end. (Withholding them
    from "where are my notes" cost the next ordinary turn 11-15 s of
    re-reading on a CPU host, measured 2026-09-30.) Reminder and timer are
    gated the same way — withheld only on talk about a timer or reminder,
    which doesn't reach the router either — and keep their band place
    (``_GATES_KEEP_BAND_PLACE``).
    """
    offered = [h for h in HANDLERS if h.offers_tool(transcript)]
    offered.sort(key=_tool_order)  # stable: band order within each group
    return [h.tool_schema for h in offered]


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
    # Lowercase, strip, and drop Whisper's terminal punctuation (see
    # normalize_transcript). plan_route() makes the same decisions as the
    # code below without dispatching; keep the two on the same helpers.
    transcript = normalize_transcript(intent.transcript)
    # latency_ms clock: route() alone. Speech-to-text has already happened
    # and text-to-speech hasn't — a voice turn's other stages are in
    # intents_log.timings (domovoi/turn_timings.py).
    t0 = time.monotonic()

    session_repo = SessionRepository(session)
    session_id = await session_repo.get_or_create(ctx.session_id, ctx.room_id)
    ctx = ctx.model_copy(update={"session_id": session_id})

    ollama_client = get_ollama_client()

    def _elapsed_ms() -> int:
        return int((time.monotonic() - t0) * 1000)

    async def _answer_choice(handler: Handler, kind: str, pending: dict) -> Response | None:
        """Hand this turn to a parked choice's ``handle_choice_reply``.
        The slot is cleared FIRST — the choice is answered or abandoned by
        this turn either way, and the handler may park a new one (a
        re-ask). None: the reply wasn't about the choice; route() goes on
        with the turn, and with the slot empty the yes/no pre-empt below
        can't fire on it."""
        await session_repo.set_context_key(session_id, PENDING_CONFIRMATION_KEY, None)
        try:
            response = await handler.handle_choice_reply(kind, pending, transcript, ctx, session)
        except MPDNotProvisioned:
            response = _no_speakers_yet(session_id, ctx)
        if response is None:
            return None
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
            matched_handler=response.matched_handler,
            matched_path="confirmation",
            latency_ms=_elapsed_ms(),
        )
        return response

    # ── A parked choice ("did you mean X?") ───────────────────────────
    # A question whose reply may be free text — "no, play the other one",
    # a bare name — not just yes/no (Handler.choice_kinds). Checked BEFORE
    # the yes/no pre-empt: its `\b` would read "no, play X" as a plain "no"
    # and the "play X" would be lost. The in-process CHOICE_PARKED set says
    # whether this session has one waiting, so an ordinary command turn
    # still makes no context read here.
    if take_parked_choice(session_id):
        ctx_data = await session_repo.get_context(session_id) or {}
        pending = ctx_data.get(PENDING_CONFIRMATION_KEY)
        choice = dispatchable_choice(pending)
        if choice is not None:
            response = await _answer_choice(choice[0], choice[1], pending)
            if response is not None:
                return response

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
        target = dispatchable_confirmation(pending, warn=True)
        if target is not None and target[1] in target[0].choice_kinds:
            # A choice CHOICE_PARKED didn't know about (the core restarted
            # since it was parked): the whole reply still goes to the
            # choice, so "no, play X" plays X here too.
            response = await _answer_choice(target[0], target[1], pending)
            if response is not None:
                return response
            target = None
        if target is not None:
            handler, kind = target
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

    # Strip leading polite/filler prefixes ("please", "can you", "yeah,"
    # etc.) so anchored fast-path regexes still match conversational
    # phrasings. Done AFTER the pending-confirmation pre-empt so a bare
    # "yes"/"yeah" still routes to handle_confirmation, but BEFORE fast
    # paths so "please add that to my library" reaches its media handler
    # instead of falling through to the QA hallucination path. The LLM
    # tool-call and QA fallback paths below intentionally use
    # `intent.transcript` (raw) so the LLM sees the user's natural
    # phrasing, not the stripped version.
    transcript = strip_leading_filler(transcript)

    # 1. Fast paths (with offline gate), over the band-sorted registry.
    #
    # Offline-gate asymmetry, re-specified (design §4.3, dossier §2.1 M4):
    # `requires_network == "yes"` auto-falls-back wholesale while offline,
    # with no per-path nuance. `"degraded"` handlers are gated PER FAST PATH —
    # only an `offline_ok=False` path auto-falls-back (unset defaults to
    # True), so a degraded handler's offline-capable paths keep running
    # and its `fallback_offline` is genuinely reachable for the rest.
    hit = first_fast_path(transcript)
    if hit is not None:
        handler, fp, m = hit
        if offline_blocked(handler, fp, ctx):
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

        try:
            response = await fp.method(handler, m, ctx, session)
        except MPDNotProvisioned:
            response = _no_speakers_yet(session_id, ctx)
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

    # 2. LLM tool-call fallback (the stub client returns None) — except for
    # a plain question about the world or a request for a joke or a story,
    # which no tool is for: those go straight to the Q&A model below. The
    # tool model would only answer them itself (thrown away) or reach for
    # a bait tool, and asking it costs a router call either way.
    tool_call = None
    if not answers_without_tools(transcript):
        tool_schemas = offered_tool_schemas(transcript)
        ollama_client = await _ready_for(ollama_client, ctx, "tool")
        tool_call = await ollama_client.route(intent.transcript, tool_schemas)
    if tool_call is not None:
        handler = HANDLER_BY_NAME.get(tool_call.get("handler", ""))
        if handler is not None and not any(
            s["name"] == handler.name for s in tool_schemas
        ):
            # The model named a tool it wasn't shown (withheld by its
            # offers_tool gate) — that is the QA fallthrough, not a route.
            log.info(
                "tool model called withheld tool %r for %r — ignoring",
                handler.name, intent.transcript,
            )
            handler = None
        if handler is not None:
            path = (
                "llm_offline"
                if (handler.requires_network == "yes" and not ctx.online)
                else "llm"
            )
            if path == "llm_offline":
                response = await handler.fallback_offline(intent, ctx, session)
            else:
                try:
                    response = await handler.execute_from_tool(
                        tool_call.get("args", {}), ctx, session
                    )
                except MPDNotProvisioned:
                    response = _no_speakers_yet(session_id, ctx)
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
            ollama_client = await _ready_for(ollama_client, ctx, "qa")
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
    # assembled into a "User context: ..." blob and added to the QA
    # system prompt, after its fixed instructions (so those stay cached
    # across speakers; the history after the profile doesn't — see
    # voice_qa_system_prompt). Anonymous speakers get ``""`` back — no-op.
    # Failure here is non-fatal: log + proceed without personalization
    # rather than break QA for a profile-side bug.
    try:
        profile_prefix = await build_profile_prefix(ctx.person_id, session)
    except Exception as e:
        log.warning("profile_prefix build failed: %s", e)
        profile_prefix = ""

    # Plain QA path. The answer is spoken exactly as the model wrote it.
    # The "want me to check that online?" offer rides on it only for a
    # real reason: a time-sensitive category (normally already taken by
    # the volatile gate above), or — for something the user actually
    # asked — the client's own flag or an answer that says in so many
    # words that it may be out of date. Suppressed offline because we
    # can't actually run the search.
    ollama_client = await _ready_for(ollama_client, ctx, "qa")
    finish_qa = functools.partial(
        _finish_qa, session_id=session_id, intent=intent, ctx=ctx, category=category,
    )

    # A voice turn asks for the answer as a stream (Context.stream_qa): the
    # streaming layer speaks each sentence as soon as the model has written
    # it, and records the turn through `finish` once it has all been said
    # (domovoi/spoken_answer.py). Everything else — /v1/intent, a client
    # without streaming — gets the whole reply, as before.
    stream_answer = getattr(ollama_client, "stream_voice_answer", None) if ctx.stream_qa else None
    if stream_answer is not None:
        spoken = SpokenAnswer(
            lambda: stream_answer(
                intent.transcript, history=history, profile_prefix=profile_prefix or None,
            ),
            transcript=intent.transcript,
        )

        async def finish(s: AsyncSession) -> tuple[Response, str]:
            # latency_ms: route() to the first sentence, the moment the
            # answer could start to be heard (the whole reply's wait, before).
            first = spoken.first_sentence_at
            latency_ms = int(((first if first is not None else time.monotonic()) - t0) * 1000)
            qa = QAWithUncertainty(
                answer=spoken.answer, needs_verification=False, unreachable=spoken.unreachable,
            )
            return await finish_qa(s, qa=qa, latency_ms=lambda: latency_ms, spoken=spoken.answer)

        return Response(
            text="",
            session_id=session_id,
            matched_handler=None,
            matched_path="qa",
            online=ctx.online,
            qa_stream=StreamedQA(answer=spoken, finish=finish),
        )

    qa = await ollama_client.qa_with_uncertainty(
        intent.transcript,
        history=history,
        profile_prefix=profile_prefix or None,
    )
    response, _rest = await finish_qa(session, qa=qa, latency_ms=_elapsed_ms, spoken="")
    return response


async def _finish_qa(
    session: AsyncSession,
    *,
    session_id: UUID,
    intent: Intent,
    ctx: Context,
    category: str | None,
    qa: Any,
    latency_ms: Any,
    spoken: str,
) -> tuple[Response, str]:
    """The end of a Q&A turn, once the answer is known: the fixed line for
    no answer, the online-check or memory offer (parked, and appended as a
    sentence of its own), and the turn's records. ``latency_ms()`` is read
    when the turn is written. ``spoken`` is what of the answer has already
    been said (a streamed answer: all of it); returns the response and the
    text still to be said after it — the offer, or the whole line when
    nothing was said."""
    answer = qa.answer.strip()
    if getattr(qa, "unreachable", False) or not answer:
        # Empty text synthesises to zero audio — the turn would end in
        # total silence, which the user can't tell apart from a dead
        # satellite (F-V011) — so a fixed line is spoken either way. Which
        # line depends on why: the Ollama calls failed (say the model is
        # down, stamp matched_path="error" so the audit trail separates an
        # outage from a real answer; the fast paths named in the line keep
        # working without the LLM), or the model is up and answered with
        # nothing usable even on its retry (say so, as a normal qa turn).
        unreachable = bool(getattr(qa, "unreachable", False))
        if unreachable:
            log.warning(
                "qa: the LLM is unreachable for %r; speaking the outage line "
                "instead of silence",
                intent.transcript,
            )
        else:
            log.warning(
                "qa: the LLM gave no usable answer for %r; speaking the "
                "no-answer line",
                intent.transcript,
            )
        path = "error" if unreachable else "qa"
        response = Response(
            text=_LLM_UNREACHABLE_TEXT if unreachable else _NO_ANSWER_TEXT,
            session_id=session_id,
            matched_handler=None,
            matched_path=path,
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
            matched_path=path,
            latency_ms=latency_ms(),
        )
        return response, response.text
    # The model may already close with an offer of its own ("…can I look
    # that up for you?"): that counts as its doubt, and it is the offer —
    # parked below so a "yes" is kept, not asked a second time.
    offers_itself = answer_offers_lookup(answer)
    self_doubt = looks_like_question(intent.transcript) and (
        qa.needs_verification or answer_admits_staleness(answer) or offers_itself
    )
    should_offer = ctx.online and (category is not None or self_doubt)
    expect_followup = False
    offer = ""
    if should_offer:
        if not offers_itself:
            offer = _ONLINE_CHECK_OFFER
        try:
            await request_confirmation(
                session,
                session_id,
                kind="core.self_doubt_offer",
                handler="double_check",
                data={
                    "question": intent.transcript,
                    # An answer WAS spoken, so a "no" can honestly say
                    # "sticking with my answer" (double_check keys that
                    # off a non-empty claim).
                    "candidate_claim": (qa.candidate_claim or qa.answer).strip()[:300],
                    "category": category or "general_recent",
                },
            )
            expect_followup = True
        except Exception as e:
            log.warning("couldn't park self_doubt_offer pending_confirmation: %s", e)
            # If we couldn't park the confirmation, drop the offer text
            # so we don't promise something the next "yes" can't honor.
            offer = ""
    elif ctx.person_id is not None:
        # No higher-priority offer is firing — see if the implicit
        # memory extractor has parked anything to surface. Mutually
        # exclusive with the self-doubt offer above so we never park
        # two pending_confirmations at once.
        memory_offer = await _maybe_offer_pending_memory(ctx, session)
        if memory_offer is not None:
            offer_text, memory_id = memory_offer
            offer = offer_text
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
                offer = ""

    if offer:
        answer = _end_sentence(answer) + " " + offer
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
        latency_ms=latency_ms(),
    )
    return response, (offer if spoken else response.text)
