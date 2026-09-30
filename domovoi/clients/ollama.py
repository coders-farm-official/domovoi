"""Ollama LLM client.

Real path: `ollama.AsyncClient` against a local Ollama server.
Stub path: deterministic echo / "no tool call" for tests.

Three entry points:
  - route(transcript, tool_schemas)  → tool-call dict or None (intent routing)
  - qa(transcript, system_prompt?)   → full text response
  - stream_qa(...)                    → async generator yielding token chunks
                                         (for sentence-level TTS streaming)

Every real chat call carries ``keep_alive`` (``settings.ollama_keep_alive``)
so Ollama doesn't unload the models between sporadic turns — see
``RealOllamaClient._chat`` for why that matters on a CPU host. Two more
knobs ride along only when set, per role: ``think`` (``ollama_tool_think``
for routing, ``ollama_qa_think`` for the QA-model calls) and
``options.num_ctx`` (``ollama_tool_num_ctx`` / ``ollama_num_ctx``).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, AsyncIterator, Protocol

from domovoi.config import normalize_think_setting, settings

log = logging.getLogger(__name__)


# ─── Model-management HTTP helpers (list / pull / delete / ps) ─────────────
#
# These talk straight to Ollama's REST API over httpx rather than through
# the `ollama` python client, for three reasons:
#   1. They're needed by BOTH processes — the core (config apply,
#      hardware endpoint) AND the separate web backend (Models page) — and
#      httpx against ``settings.ollama_url`` works identically from either
#      without holding a client singleton.
#   2. ``pull`` is a long streamed transfer whose per-line {completed,total}
#      progress the ollama client doesn't surface as cleanly as the raw
#      newline-delimited JSON stream.
#   3. They must degrade gracefully when Ollama is down (local-first: the
#      installed/active views still render) — a bare httpx call with a
#      short timeout is the simplest thing that returns [] / raises clearly.
#
# All default to ``settings.ollama_url`` but accept an explicit base so a
# test can point them at a stub server.


def _ollama_base(base_url: str | None) -> str:
    return (base_url or settings.ollama_url).rstrip("/")


def _positive_int(value: Any) -> int | None:
    """``value`` as a positive int, else None. 0 (the setting default),
    negatives and junk all mean "not set"."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _num_ctx_options(value: Any) -> dict[str, int]:
    """The ``options`` entry for an ``ollama_num_ctx`` / ``ollama_tool_num_ctx``
    value: ``{"num_ctx": n}`` when set, ``{}`` when not — and then the call
    goes out exactly as it did before the setting existed."""
    n = _positive_int(value)
    return {"num_ctx": n} if n is not None else {}


async def list_models(base_url: str | None = None, timeout: float = 5.0) -> list[dict[str, Any]]:
    """Installed-on-disk Ollama models via ``GET /api/tags``.

    Returns the raw ``models`` list (each: name, size, digest, details{...},
    modified_at). Empty list if Ollama is unreachable or the shape changed —
    the caller renders an empty "no models installed / Ollama offline" state
    rather than erroring.
    """
    import httpx

    url = f"{_ollama_base(base_url)}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            resp = await c.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        log.warning("ollama /api/tags failed: %s", e)
        return []
    models = payload.get("models") if isinstance(payload, dict) else None
    return models if isinstance(models, list) else []


async def _ps_or_none(base_url: str | None, timeout: float) -> list[dict[str, Any]] | None:
    """``GET /api/ps``'s model list, or None when Ollama couldn't be asked —
    which, unlike an empty list, doesn't mean "nothing is loaded"."""
    import httpx

    url = f"{_ollama_base(base_url)}/api/ps"
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            resp = await c.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        log.warning("ollama /api/ps failed: %s", e)
        return None
    models = payload.get("models") if isinstance(payload, dict) else None
    return models if isinstance(models, list) else []


async def ps(base_url: str | None = None, timeout: float = 2.0) -> list[dict[str, Any]]:
    """Currently-loaded (VRAM-resident) models via ``GET /api/ps``.

    Distinct from :func:`list_models` (on-disk). Empty list on any failure.
    """
    return await _ps_or_none(base_url, timeout) or []


def _model_key(name: str | None) -> str:
    """A model name as Ollama reports it: lower case, ``:latest`` when no
    tag was given ("llama3.2" → "llama3.2:latest")."""
    n = (name or "").strip().lower()
    return n if ":" in n or not n else n + ":latest"


def _keep_alive_for_model(model: str) -> str | int | float | None:
    """The ``keep_alive`` the voice pipeline sends for ``model``, for a
    caller outside the voice client — the dashboard's text chat — so a chat
    turn on the Q&A model doesn't hand it back to Ollama's 5-minute
    default (Ollama keeps a model for whatever its latest request asked).
    None for any other model, a vision model included: that one unloads on
    the Ollama server's own schedule instead of crowding the voice models
    out of memory."""
    key = _model_key(model)
    general = _normalize_keep_alive(settings.ollama_keep_alive)
    if key == _model_key(settings.ollama_model):
        return general
    if key == _model_key(settings.ollama_tool_model):
        tool = _normalize_keep_alive(settings.ollama_tool_keep_alive)
        return general if tool is None else tool
    return None


async def delete_model(name: str, base_url: str | None = None, timeout: float = 30.0) -> None:
    """Delete an installed model via ``DELETE /api/delete``. Raises on failure
    so the caller can surface a toast — deletion is an explicit destructive
    action, not a best-effort read."""
    import httpx

    url = f"{_ollama_base(base_url)}/api/delete"
    async with httpx.AsyncClient(timeout=timeout) as c:
        # Ollama's delete takes the model name in the JSON body of a DELETE.
        resp = await c.request("DELETE", url, json={"model": name})
        resp.raise_for_status()


async def pull_model(
    name: str,
    base_url: str | None = None,
    connect_timeout: float = 10.0,
) -> AsyncIterator[dict[str, Any]]:
    """Stream ``POST /api/pull`` progress for ``name``.

    Yields each decoded JSON line Ollama emits:
      ``{"status": "pulling manifest"}``
      ``{"status": "downloading <digest>", "completed": 12345, "total": 99999}``
      ``{"status": "verifying sha256 digest"}``
      ``{"status": "success"}``

    The read side has NO overall timeout (a multi-GB pull legitimately takes
    minutes); only the initial connect is bounded. Raises on a transport
    error so the job is marked failed. The caller derives pct from
    completed/total and persists throttled progress.
    """
    import httpx

    url = f"{_ollama_base(base_url)}/api/pull"
    timeout = httpx.Timeout(connect=connect_timeout, read=None, write=30.0, pool=connect_timeout)
    async with httpx.AsyncClient(timeout=timeout) as c:
        async with c.stream("POST", url, json={"model": name, "stream": True}) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


async def chat_stream(
    messages: list[dict[str, Any]],
    model: str,
    base_url: str | None = None,
    connect_timeout: float = 10.0,
    stats: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """Stream one multi-turn chat completion via ``POST /api/chat``, yielding
    assistant-content deltas as they arrive.

    ``messages`` is the Ollama chat shape: ``[{"role": "user"|"assistant"|
    "system", "content": str, "images": [<base64>, ...]?}]`` — the optional
    ``images`` list is how vision models receive attachments. Used by the
    web text-chat surface (module-level like :func:`pull_model`; the voice
    pipeline's Protocol clients are untouched). No overall read timeout — a
    long completion on a big model is legitimate; only connect is bounded.
    Raises on transport errors so the caller can surface the failure.

    Pass a dict as ``stats`` to get Ollama's closing figures for the reply
    copied into it when the stream ends: ``done_reason``, the token counts
    ``prompt_eval_count`` / ``eval_count`` and the nanosecond durations
    ``total_duration`` / ``load_duration`` / ``prompt_eval_duration`` /
    ``eval_duration`` — whichever the server sent. Untouched on failure."""
    import httpx

    url = f"{_ollama_base(base_url)}/api/chat"
    body: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
    # Same context window as the voice pipeline's QA calls; unset (0)
    # sends nothing, as before.
    options = _num_ctx_options(settings.ollama_num_ctx)
    if options:
        body["options"] = options
    # And the same keep_alive when this is one of the voice models.
    keep_alive = _keep_alive_for_model(model)
    if keep_alive is not None:
        body["keep_alive"] = keep_alive
    timeout = httpx.Timeout(connect=connect_timeout, read=None, write=30.0, pool=connect_timeout)
    async with httpx.AsyncClient(timeout=timeout) as c:
        async with c.stream("POST", url, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    raise RuntimeError(str(chunk["error"]))
                delta = (chunk.get("message") or {}).get("content")
                if delta:
                    yield delta
                if chunk.get("done"):
                    if stats is not None:
                        stats.update({k: chunk[k] for k in _DONE_STATS if chunk.get(k) is not None})
                    return


# The closing figures of a streamed chat reply (see chat_stream's ``stats``).
_DONE_STATS = (
    "done_reason", "prompt_eval_count", "eval_count",
    "total_duration", "load_duration", "prompt_eval_duration", "eval_duration",
)


def pct_from_progress(chunk: dict[str, Any]) -> int | None:
    """Map one Ollama pull line to a 0-100 percentage, or None if the line
    carries no byte totals (manifest / verify phases). Clamped to [0,100]."""
    completed = chunk.get("completed")
    total = chunk.get("total")
    try:
        if completed is None or not total:
            return None
        pct = int(round(100.0 * float(completed) / float(total)))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return max(0, min(100, pct))


DEFAULT_SYSTEM_PROMPT = (
    "You are {bot}, a helpful voice assistant. Keep responses concise and "
    "conversational — this is a voice interaction, so avoid markdown, bullet "
    "lists, code blocks, and long structured responses. Speak naturally, in "
    "short sentences."
)


# System prompt for the spoken Q&A fallthrough (``qa_with_uncertainty``).
# The reply is spoken exactly as the model wrote it — nothing downstream
# trims it — so this prompt is what keeps a voice answer short: the
# general prompt plus a sentence budget. Plain text on purpose: this
# answer used to be a field of a JSON object, and llama3.2:3b closes a
# JSON string early — after a question mark (the joke's setup without its
# punchline), at a contraction's apostrophe ("I don"), with a placeholder
# ("None") or with nothing at all. Measured 2026-09-28 on llama3.2:3b, 30
# seeds per case: 60/60 jokes with their punchline, explanations down
# from a median of 59 words to 43. Resist spelling out "a joke needs its
# punchline" here: the same measurement found that naming jokes makes the
# model reach for a long setup and stop after it, about one time in five.
VOICE_QA_SYSTEM_PROMPT = (
    DEFAULT_SYSTEM_PROMPT + " Most replies should be one to three sentences."
)


# System prompt for the tool-routing call (``RealOllamaClient.route``).
#
# The router runs at temperature 0 with every handler's schema on offer,
# and a small tool model on a CPU host will reach for the nearest tool
# before it will answer "no tool" — measured 2026-09-15 on qwen3:8b:
# "who wrote the odyssey" → calculator, "who painted the mona lisa" →
# double_check (the question was "verified" as a claim); on qwen2.5:14b
# the same questions go to news as a subject lookup. The knowledge-
# question paragraph exists to name that failure explicitly. Keep the
# action bias in the first paragraph: imperfect commands ("…play the
# new TI song") must still route. Verify edits with
# ``scripts/eval_routing.py`` before deploying — this prompt is only as
# good as the model reading it.
ROUTER_SYSTEM_PROMPT = (
    "You are an intent router for a voice assistant. The transcript "
    "is speech-to-text and may contain mis-heard or spurious words, "
    "especially at the start (a garbled wake word or noise) — focus "
    "on the actionable core of the utterance, not stray leading "
    "words. If the user clearly wants an action (play/find/add music, "
    "set a timer, control the home, etc.), pick the closest matching "
    "tool even when the phrasing is imperfect.\n"
    "General-knowledge questions are NOT actions and get NO tool call: "
    "'who wrote the odyssey', 'who painted the mona lisa', 'what is "
    "the capital of mongolia', 'why is the sky blue', 'when was the "
    "declaration signed' are answered by a separate model. Never use "
    "a tool to look up, compute, or verify the answer to a question: "
    "a question is not a claim, and a fact is not news. Verification "
    "is only an explicit request to check something the assistant "
    "already said ('are you sure', 'double check that', 'is it true "
    "that ...'); the calculator is only for numbers, quantities, "
    "units or dates to compute with. When no tool clearly fits, "
    "respond with plain text and no tool call."
)


# What ``qa_with_uncertainty`` hands the router: the spoken answer, plus
# ``needs_verification`` — whether the client has its own reason to think
# the answer should be checked against a web source. The real client
# always says False: the model's self-reported doubt flag it used to ask
# for fired on junk turns and refusals and almost never on a stale fact,
# so the router's offer now rests on the heuristic categorizer and on the
# answer's own words (``domovoi.uncertainty``). ``unreachable`` is True only
# when the Ollama calls themselves failed; an empty ``answer`` with
# ``unreachable`` False means the model answered and said nothing usable.
@dataclass
class QAWithUncertainty:
    answer: str
    needs_verification: bool
    candidate_claim: str = ""
    unreachable: bool = False


@dataclass
class SearchSubject:
    """What a volatile-question gate offer will name + search. The model
    is FORBIDDEN to answer — it only names the subject noun phrase (to
    splice into 'want me to check ___?') and a concise search query, so
    a stale fabricated fact can't leak into the spoken confirmation."""
    subject: str
    refined_query: str


# One candidate memory the implicit extractor returned. The worker
# filters on ``confidence`` against ``memory_extractor_min_confidence``
# and writes rows above the cut as ``status='pending'``.
@dataclass
class ExtractedMemory:
    fact: str
    confidence: float
    topic: str = ""


_EXTRACT_MEMORIES_SYSTEM_PROMPT = (
    "You read a transcript of a conversation between a user and a "
    "voice assistant and extract long-term-worthwhile facts about the "
    "USER (not the assistant). Output ONLY a JSON object of this "
    "exact shape, no preface or explanation:\n"
    '{{"memories": [{{"fact": "<one short factual statement>", '
    '"confidence": <0.0-1.0>, '
    '"topic": "<short tag like \\"food\\", \\"family\\", '
    '\\"work\\", or empty>"}}]}}\n'
    "\n"
    "Rules:\n"
    "- Only include facts that would still be useful WEEKS LATER: "
    "preferences, allergies, family/pet names, recurring routines, "
    "ongoing projects.\n"
    "- Do NOT include transient state (what's happening right now, "
    "today's weather, current emotion).\n"
    "- Do NOT include facts about the assistant or third parties.\n"
    "- Confidence: 1.0 = the user explicitly stated it; 0.7 = "
    "strongly implied across multiple turns; 0.5 = only mentioned "
    "once in passing.\n"
    "- If nothing is worth extracting, return {{\"memories\": []}}.\n"
)


_EXTRACT_SUBJECT_SYSTEM_PROMPT = (
    "The user asked a question whose answer changes over time, so it "
    "MUST be looked up online — you must NOT answer it. Identify only: "
    "(a) SUBJECT — a short noun phrase naming what to look up, phrased "
    "to slot into the sentence 'want me to check ___?' "
    "(e.g. 'the weather in Phoenix today', \"Apple's current stock "
    "price\", 'the Lakers score'); and (b) QUERY — a concise web search "
    "query. Output ONLY a JSON object of this exact shape, no preface:\n"
    '{"subject": "<noun phrase>", "query": "<search query>"}\n'
    "NEVER include an answer, guess, number, or fact in either field — "
    "naming the subject is allowed, stating its value is NOT."
)


# A reply that is only a placeholder, not an answer. These are what
# llama3.2:3b wrote into the old JSON "answer" field instead of a refusal
# ("I can't provide information on…" came back as "None"); free text makes
# them rare, and one is retried rather than spoken.
_PLACEHOLDER_ANSWER_RE = re.compile(
    r"^(?:none(?: found)?|null|n/?a|not applicable|unknown|no answer)[.!]?$",
    re.IGNORECASE,
)
# A reply that stops on a contraction's stem ("I don", "that doesn") with no
# closing punctuation was cut off mid-word; the live office log has "I don",
# "I couldn" and "I didn" from the old JSON contract. "can" and "won" are
# left out, and the match is case-sensitive ("Ask Don"): they are words in
# their own right.
_CUT_AT_CONTRACTION_RE = re.compile(
    r"\b(?:don|didn|doesn|couldn|wouldn|shouldn|isn|aren|wasn|weren|hasn|"
    r"haven|hadn|mustn|needn)$"
)
_SENTENCE_END = ".!?…"
_CLOSING_QUOTES = "\"'”’)»"


def _last_complete_sentences(text: str) -> str:
    """``text`` up to its last sentence-ending punctuation, or "" if it has
    none — what is left of a reply after its broken last words are dropped."""
    cut = max(text.rfind(ch) for ch in _SENTENCE_END)
    if cut < 0:
        return ""
    end = cut + 1
    while end < len(text) and text[end] in _CLOSING_QUOTES:
        end += 1
    return text[:end].strip()


def _is_timeout(exc: BaseException) -> bool:
    """A read/connect timeout from httpx (ollama-python lets them through
    unwrapped) or asyncio, as opposed to a refused connection or an error
    reply."""
    return isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()


def _clean_spoken_answer(raw: str) -> tuple[str, str | None]:
    """Tidy a free-text QA reply for speech and say what, if anything, is
    wrong with it: ``(text, problem)``, where ``problem`` is None for a
    usable answer, else "empty", "placeholder" or "cut". Never shortens a
    good answer — the prompt, not this, keeps voice answers short."""
    text = (raw or "").strip()
    # A reply wrapped whole in one pair of quotes would be read with them.
    if len(text) >= 2 and text[0] == text[-1] == '"' and text.count('"') == 2:
        text = text[1:-1].strip()
    if not text:
        return "", "empty"
    if _PLACEHOLDER_ANSWER_RE.match(text):
        return text, "placeholder"
    if text[-1] not in _SENTENCE_END + _CLOSING_QUOTES and _CUT_AT_CONTRACTION_RE.search(text):
        return text, "cut"
    return text, None


def _parse_subject_json(raw: str, fallback_query: str = "") -> SearchSubject:
    """Parse the JSON from ``extract_search_subject``. Defensive on every
    field — any failure yields an empty subject (the router renders a
    category-phrase fallback offer) and the raw transcript as the query."""
    if not raw:
        return SearchSubject(subject="", refined_query=fallback_query)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return SearchSubject(subject="", refined_query=fallback_query)
    try:
        parsed = json.loads(raw[start : end + 1])
    except Exception:
        return SearchSubject(subject="", refined_query=fallback_query)
    if not isinstance(parsed, dict):
        return SearchSubject(subject="", refined_query=fallback_query)
    subject = str(parsed.get("subject") or "").strip()[:120]
    query = str(parsed.get("query") or "").strip()[:200] or fallback_query
    return SearchSubject(subject=subject, refined_query=query)


def _parse_extracted_memories(raw: str) -> list[ExtractedMemory]:
    """Parse the JSON object returned by ``extract_memories``.

    Defensive on every field — models sometimes wrap the JSON in
    prose, drop the topic, stringify the confidence, or return a
    bare list instead of the expected ``{memories: [...]}`` envelope.
    We accept both shapes and return ``[]`` on anything we can't
    parse. Confidence is clamped to [0.0, 1.0] so a stray ``"high"``
    or ``2`` doesn't break downstream comparisons.
    """
    if not raw:
        return []
    start = raw.find("{")
    list_start = raw.find("[")
    # Prefer the envelope object if both `{...}` and `[...]` appear.
    if 0 <= start <= list_start or (start >= 0 and list_start < 0):
        end = raw.rfind("}")
        if end <= start:
            return []
        blob = raw[start : end + 1]
        try:
            parsed = json.loads(blob)
        except Exception:
            return []
        items = parsed.get("memories") if isinstance(parsed, dict) else None
    else:
        end = raw.rfind("]")
        if end <= list_start:
            return []
        try:
            items = json.loads(raw[list_start : end + 1])
        except Exception:
            return []
    if not isinstance(items, list):
        return []
    out: list[ExtractedMemory] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        fact = str(it.get("fact") or "").strip()
        if not fact:
            continue
        raw_conf = it.get("confidence")
        try:
            conf = float(raw_conf) if raw_conf is not None else 0.0
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        topic = str(it.get("topic") or "").strip()[:64]
        out.append(ExtractedMemory(fact=fact[:500], confidence=conf, topic=topic))
    return out


class OllamaClient(Protocol):
    async def route(
        self, transcript: str, tool_schemas: list[dict[str, Any]]
    ) -> dict[str, Any] | None: ...

    async def qa(
        self,
        transcript: str,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str: ...

    def stream_qa(
        self,
        transcript: str,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]: ...

    async def qa_with_uncertainty(
        self,
        transcript: str,
        history: list[dict[str, str]] | None = None,
        profile_prefix: str | None = None,
    ) -> QAWithUncertainty: ...

    async def extract_search_subject(
        self,
        transcript: str,
        history: list[dict[str, str]] | None = None,
    ) -> SearchSubject: ...

    async def extract_memories(
        self,
        transcript_block: str,
    ) -> list[ExtractedMemory]: ...


class OllamaStubClient:
    """Deterministic stub. route() always returns None (falls through to qa)."""

    async def route(
        self, transcript: str, tool_schemas: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        return None

    async def qa(
        self,
        transcript: str,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        return f"(stub qa) I heard: {transcript}"

    async def stream_qa(
        self,
        transcript: str,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        yield "(stub qa) I heard: "
        yield transcript

    async def qa_with_uncertainty(
        self,
        transcript: str,
        history: list[dict[str, str]] | None = None,
        profile_prefix: str | None = None,
    ) -> QAWithUncertainty:
        # Deterministic stub — needs_verification=False so the offer
        # path doesn't fire spuriously in tests that aren't exercising
        # it. ``profile_prefix`` is surfaced in the stubbed answer so
        # router-side tests can assert the personal context made it
        # through (e.g. "user context: favorite genre is jazz" lands
        # in the spoken answer).
        prefix_tag = f" [profile: {profile_prefix}]" if profile_prefix else ""
        return QAWithUncertainty(
            answer=f"(stub qa) I heard: {transcript}{prefix_tag}",
            needs_verification=False,
            candidate_claim="",
        )

    async def extract_search_subject(
        self,
        transcript: str,
        history: list[dict[str, str]] | None = None,
    ) -> SearchSubject:
        # Deterministic, non-empty subject so the volatile-gate offer
        # text is exercised under USE_STUBS. Tests covering the
        # empty-subject fallback patch this to return subject="".
        return SearchSubject(subject=transcript, refined_query=transcript)

    async def extract_memories(
        self,
        transcript_block: str,
    ) -> list[ExtractedMemory]:
        # Deterministic stub — empty extraction so the worker is a
        # safe no-op in tests that don't explicitly exercise the
        # extraction path. Tests targeting the extractor patch this
        # method directly.
        return []


def _chat_accepts(param: str) -> bool:
    """Whether the installed ollama-python's ``AsyncClient.chat`` takes
    ``param`` as a keyword. Never raises — no ollama, odd signature: report
    False and the caller simply omits the kwarg."""
    try:
        import inspect

        from ollama import AsyncClient

        return param in inspect.signature(AsyncClient.chat).parameters
    except Exception:  # noqa: BLE001 — no ollama, odd signature: just don't send it
        return False


@lru_cache(maxsize=1)
def _client_accepts_think() -> bool:
    """Whether the installed ollama-python accepts ``chat(think=...)``.

    The kwarg arrived well after this repo's ``ollama>=0.3`` floor, so an
    older environment would raise TypeError on every route and silently
    degrade all routing to the QA fallthrough. Probe the signature once
    instead of discovering it per-turn.
    """
    return _chat_accepts("think")


@lru_cache(maxsize=1)
def _client_accepts_keep_alive() -> bool:
    """Whether the installed ollama-python accepts ``chat(keep_alive=...)``.

    It has since long before the ``ollama>=0.3`` floor, so this is expected
    to be True everywhere — the probe is the same cheap insurance as for
    ``think``: an environment that can't take the kwarg must lose keep-alive,
    not every chat call.
    """
    return _chat_accepts("keep_alive")


def _normalize_keep_alive(value: str | None) -> str | int | float | None:
    """Turn ``settings.ollama_keep_alive`` into what Ollama's API expects.

    Ollama takes either a duration string ("24h", "90m") or a NUMBER of
    seconds, where any negative number means "forever". A bare number typed
    as a string ("-1", "3600") is NOT a valid duration to Ollama's parser
    (Go's ``time.ParseDuration`` wants a unit), so those go out as JSON
    numbers. Blank means "don't send it" — the Ollama server's own default,
    or ``OLLAMA_KEEP_ALIVE`` in its unit file, governs instead.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _qa_think_value(value: Any) -> bool | None:
    """``settings.ollama_qa_think`` as it goes on the wire: None (leave the
    field out, the default), True or False. A value that is none of the
    setting's spellings is treated as the default rather than failing every
    QA call; the settings validator and the dashboard refuse such values
    before they can get here."""
    try:
        choice = normalize_think_setting(value)
    except ValueError:
        log.warning("ignoring ollama_qa_think=%r: expected default, true or false", value)
        return None
    return None if choice == "default" else choice == "true"


class RealOllamaClient:
    """Async Ollama client with tool-calling + streaming.

    Imports `ollama` lazily inside `__init__` so USE_STUBS=true doesn't require
    the package installed (though it's a core dep — the laziness is cheap
    insurance against transient install issues).
    """

    _THINK_UNSUPPORTED_HINTS = ("think", "reasoning")
    # What a server or client complaint about `keep_alive` looks like: Ollama
    # answers an unparseable value with Go's `time: invalid duration "..."`
    # / `missing unit in duration`; a too-old client raises
    # `unexpected keyword argument 'keep_alive'`.
    _KEEP_ALIVE_UNSUPPORTED_HINTS = ("keep_alive", "keepalive", "duration")

    # The optional per-role knobs, bound in __init__. Declared here as well
    # so a client assembled without __init__ (the tests build one that way)
    # sends exactly what every call sent before these knobs existed.
    _qa_think: bool | None = None
    _send_qa_think: bool = False
    _num_ctx: int | None = None
    _tool_num_ctx: int | None = None
    # The cold-start machinery (`cold_models`, `for_cold_start`), bound in
    # __init__ too. Without a URL a client can't ask Ollama what is loaded,
    # so it reports every model warm and never swaps its HTTP client.
    _url: str | None = None
    _cold_twin: "RealOllamaClient | None" = None
    _tool_keep_alive: str | int | float | None = None

    def __init__(
        self,
        *,
        url: str,
        qa_model: str,
        tool_model: str,
    ) -> None:
        import httpx
        from ollama import AsyncClient

        # Bound every request so a stalled Ollama (model loading, GPU hang)
        # can't pin a user-facing voice turn open indefinitely. `read` is the
        # max time between bytes, so a long-but-progressing generation is
        # fine; a truly hung server raises and the turn degrades gracefully.
        self._client = AsyncClient(
            host=url,
            timeout=httpx.Timeout(
                connect=5.0,
                read=settings.ollama_timeout_sec,
                write=10.0,
                pool=5.0,
            ),
        )
        self._url = url
        # See `cold_models`; shared with the cold-start twin (a shallow copy).
        self._ctx_mismatches: dict[str, set[int]] = {}
        self._qa_model = qa_model
        self._tool_model = tool_model
        # Bound at construction like the model names (reset_ollama_client is
        # the reapply hook for all of them). `_send_think` starts as "the
        # installed client accepts the kwarg" and latches off if the server
        # rejects it for this model — see `route`.
        self._tool_think = settings.ollama_tool_think
        self._send_think = _client_accepts_think()
        # keep_alive is bound and degrades the same way — see `_chat`. A blank
        # setting means never send it (the Ollama server's default governs).
        # The tool model can have its own (`_keep_alive_for`).
        self._keep_alive = _normalize_keep_alive(settings.ollama_keep_alive)
        self._tool_keep_alive = _normalize_keep_alive(settings.ollama_tool_keep_alive)
        self._send_keep_alive = (
            self._keep_alive is not None or self._tool_keep_alive is not None
        ) and _client_accepts_keep_alive()
        # The QA-model calls' `think`. None (the default) leaves the field
        # out, as every QA call always has. Otherwise it's sent and degrades
        # exactly like the router's, on a latch of its own: the QA model
        # rejecting the flag says nothing about the tool model.
        self._qa_think = _qa_think_value(settings.ollama_qa_think)
        self._send_qa_think = self._qa_think is not None and _client_accepts_think()
        # options.num_ctx per role; None sends none (the server's default).
        self._num_ctx = _positive_int(settings.ollama_num_ctx)
        self._tool_num_ctx = _positive_int(settings.ollama_tool_num_ctx)

    def _system_prompt(self, override: str | None) -> str:
        if override:
            return override
        return DEFAULT_SYSTEM_PROMPT.format(bot=settings.bot_name)

    async def route(
        self, transcript: str, tool_schemas: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Ask the model to pick a tool. Returns {handler, args} or None.

        `tool_schemas` is a list of handler tool schemas in Ollama's expected
        function-calling format. We wrap each in `{"type": "function", "function": schema}`.
        """
        if not tool_schemas:
            return None

        tools = [{"type": "function", "function": s} for s in tool_schemas]

        try:
            response = await self._route_chat(transcript, tools)
        except Exception as e:
            log.warning("ollama route failed: %s — falling through to qa", e)
            return None

        message = response.get("message") if isinstance(response, dict) else getattr(response, "message", None)
        if message is None:
            return None
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
        if not tool_calls:
            return None

        first = tool_calls[0]
        func = first.get("function") if isinstance(first, dict) else getattr(first, "function", None)
        if func is None:
            return None
        name = func.get("name") if isinstance(func, dict) else getattr(func, "name", None)
        args = func.get("arguments") if isinstance(func, dict) else getattr(func, "arguments", {})
        if not name:
            return None
        return {"handler": name, "args": args or {}}

    async def _route_chat(
        self,
        transcript: str,
        tools: list[dict[str, Any]],
        extra_options: dict[str, Any] | None = None,
    ) -> Any:
        """The routing chat call, with a one-time retry that drops ``think``.

        Thinking is a per-model capability, so a server can reject the flag
        even when the client understands it. Rather than let that degrade
        every routed turn to the QA fallthrough, retry once without it and
        latch the flag off for this client's lifetime — the cost is paid on a
        single turn, not forever.
        """
        try:
            return await self._chat_for_route(
                transcript, tools, send_think=self._send_think, extra_options=extra_options,
            )
        except Exception as e:
            if not self._send_think or not self._looks_like_think_rejection(e):
                raise
            log.info(
                "tool model %s rejected the 'think' flag (%s) — retrying without it "
                "and disabling it for this client",
                self._tool_model, e,
            )
            self._send_think = False
            return await self._chat_for_route(
                transcript, tools, send_think=False, extra_options=extra_options,
            )

    @classmethod
    def _looks_like_think_rejection(cls, exc: Exception) -> bool:
        text = str(exc).lower()
        return any(h in text for h in cls._THINK_UNSUPPORTED_HINTS)

    # ── keep_alive on every call ─────────────────────────────────────────

    @classmethod
    def _looks_like_keep_alive_rejection(cls, exc: Exception) -> bool:
        text = str(exc).lower()
        return any(h in text for h in cls._KEEP_ALIVE_UNSUPPORTED_HINTS)

    def _disable_keep_alive(self, exc: Exception) -> None:
        log.warning(
            "ollama rejected keep_alive=%r (%s) — retrying without it and "
            "disabling it for this client; models will now unload on the "
            "Ollama server's own schedule (5 min by default). Check the "
            "ollama_keep_alive setting.",
            self._keep_alive, exc,
        )
        self._send_keep_alive = False

    def _keep_alive_for(self, model: Any) -> str | int | float | None:
        """``ollama_tool_keep_alive`` for the tool model when it has one and
        the tool model is not also the QA model (Ollama keeps a model for
        whatever its latest request asked, so one model can't have two);
        ``ollama_keep_alive`` otherwise."""
        if (
            self._tool_keep_alive is not None
            and model == self._tool_model
            and model != self._qa_model
        ):
            return self._tool_keep_alive
        return self._keep_alive

    async def _chat(self, **kwargs: Any) -> Any:
        """Every Ollama chat call funnels through here so ``keep_alive`` rides
        along on all of them — router, QA, streaming QA, uncertainty, subject
        extraction, memory extraction, warm-up. Without it Ollama falls back
        to its own 5-minute default and a CPU host pays a cold start after
        every quiet spell between household questions: model load plus the
        tool-schema prefill, measured at ~54 s cold against ~4 s warm for the
        same turn.

        Degrades the way ``think`` does: the kwarg is omitted when the
        installed client can't take it, and if the server rejects the value
        (a duration it can't parse) the call is retried once without it and
        the flag latches off for this client's lifetime. A streaming request
        only raises once iterated, so ``stream_qa`` carries its own
        first-chunk retry.
        """
        keep_alive = self._keep_alive_for(kwargs.get("model"))
        if not self._send_keep_alive or keep_alive is None:
            return await self._client.chat(**kwargs)
        try:
            return await self._client.chat(keep_alive=keep_alive, **kwargs)
        except Exception as e:
            if not self._looks_like_keep_alive_rejection(e):
                raise
            self._disable_keep_alive(e)
            return await self._client.chat(**kwargs)

    async def _chat_for_route(
        self,
        transcript: str,
        tools: list[dict[str, Any]],
        *,
        send_think: bool,
        extra_options: dict[str, Any] | None = None,
    ) -> Any:
        extra: dict[str, Any] = {"think": self._tool_think} if send_think else {}
        return await self._chat(
            model=self._tool_model,
            messages=[
                {
                    "role": "system",
                    "content": ROUTER_SYSTEM_PROMPT,
                },
                {"role": "user", "content": transcript},
            ],
            tools=tools,
            stream=False,
            # Deterministic, best-guess routing. Intent classification
            # wants the model's single most-likely tool for a given
            # transcript, not sampled variety — at Ollama's default
            # temperature (0.8) the same utterance routes inconsistently
            # (observed: a lightly-garbled "…play the new TI song" routed
            # to `music` on some calls and declined to plain text on
            # others). temperature=0 makes a transcript route the same
            # way every time and biases toward acting on borderline
            # commands instead of randomly bailing to the QA fallthrough.
            # num_ctx only when ollama_tool_num_ctx is set.
            options={
                "temperature": 0,
                **_num_ctx_options(self._tool_num_ctx),
                **(extra_options or {}),
            },
            **extra,
            )

    # ── the QA-model calls' optional knobs ───────────────────────────────

    def _qa_extras(self, extra_options: dict[str, Any] | None = None) -> dict[str, Any]:
        """``options.num_ctx`` and ``think`` for a QA-model call — each only
        when its setting asks for it, so with both unset the call is exactly
        what it was before they existed. ``extra_options`` joins the options
        (the warm-up's one-token limit)."""
        extra: dict[str, Any] = {}
        options = {**_num_ctx_options(self._num_ctx), **(extra_options or {})}
        if options:
            extra["options"] = options
        if self._send_qa_think:
            extra["think"] = self._qa_think
        return extra

    def _disable_qa_think(self, exc: Exception) -> None:
        log.info(
            "QA model %s rejected the 'think' flag (%s) — retrying without it "
            "and disabling it for this client",
            self._qa_model, exc,
        )
        self._send_qa_think = False

    async def _qa_chat(
        self, *, extra_options: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        """A non-streaming QA-model call: ``_chat`` plus :meth:`_qa_extras`,
        with the router's one-time retry when the server rejects ``think``
        (see ``_route_chat``). ``stream_qa`` carries its own retry, because
        a streamed rejection only surfaces once the stream is iterated."""
        try:
            return await self._chat(**kwargs, **self._qa_extras(extra_options))
        except Exception as e:
            if not self._send_qa_think or not self._looks_like_think_rejection(e):
                raise
            self._disable_qa_think(e)
            return await self._chat(**kwargs, **self._qa_extras(extra_options))

    def _build_messages(
        self,
        transcript: str,
        system_prompt: str | None,
        history: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        # `history` comes from SessionRepository.record_exchange — entries have
        # `role` ("user"/"assistant") and `text`; ollama expects `content`.
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt(system_prompt)},
        ]
        for turn in history or ():
            role = turn.get("role")
            content = turn.get("text") or turn.get("content") or ""
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": transcript})
        return messages

    async def qa(
        self,
        transcript: str,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        response = await self._qa_chat(
            model=self._qa_model,
            messages=self._build_messages(transcript, system_prompt, history),
            stream=False,
        )
        return self._chunk_content(response).strip()

    async def stream_qa(
        self,
        transcript: str,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        messages = self._build_messages(transcript, system_prompt, history)
        yielded = False
        while True:
            stream = await self._chat(
                model=self._qa_model, messages=messages, stream=True, **self._qa_extras()
            )
            try:
                async for chunk in stream:
                    content = self._chunk_content(chunk)
                    if content:
                        yielded = True
                        yield content
                return
            except Exception as e:
                # ollama-python only opens the HTTP stream on first iteration,
                # so a keep_alive or think rejection lands here, not in
                # `_chat`. Retrying is safe only before the first chunk —
                # after that the caller already holds partial text and a
                # restart would duplicate it. Each retry latches one flag
                # off, so this goes round at most twice more.
                if yielded:
                    raise
                if self._send_keep_alive and self._looks_like_keep_alive_rejection(e):
                    self._disable_keep_alive(e)
                elif self._send_qa_think and self._looks_like_think_rejection(e):
                    self._disable_qa_think(e)
                else:
                    raise

    @staticmethod
    def _chunk_content(chunk: Any) -> str:
        """The text of one response / stream chunk, whether ollama-python hands
        back a dict or a typed object; "" when there's none."""
        message = chunk.get("message") if isinstance(chunk, dict) else getattr(chunk, "message", None)
        if message is None:
            return ""
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        return content or ""

    async def qa_with_uncertainty(
        self,
        transcript: str,
        history: list[dict[str, str]] | None = None,
        profile_prefix: str | None = None,
    ) -> QAWithUncertainty:
        """The spoken answer for the voice Q&A fallthrough.

        Plain free text from the QA model under ``VOICE_QA_SYSTEM_PROMPT``
        — never a field of a JSON object. It used to be one
        (``format='json'``, ``{"answer", "needs_verification",
        "candidate_claim"}``), and llama3.2:3b closed that string early
        often enough to be heard: the joke "What do you call a fake
        noodle?" with no punchline, "I don" for "I don't…", "None" for a
        refusal, or nothing at all, which the router then announced as a
        dead language model. Same model, same questions, free text: none
        of those (probe, 2026-09-28).

        A reply that is empty, only a placeholder, or stops on a
        contraction stem is asked for once more. If the retry is no better,
        a cut reply keeps its complete sentences, and an empty one comes
        back as ``answer=""`` with ``unreachable=False`` so the router can
        say so honestly. ``unreachable=True`` is reserved for the Ollama
        calls themselves failing. ``needs_verification`` is always False
        here: the router judges the offer from the question and from what
        the answer says (see ``domovoi.uncertainty``).

        ``profile_prefix`` is the speaker's memories + favorites + prefs
        blob, prepended to the system prompt so answers can lean on
        personal context.
        """
        system_prompt = VOICE_QA_SYSTEM_PROMPT.format(bot=settings.bot_name)
        if profile_prefix:
            system_prompt = profile_prefix.rstrip() + "\n\n" + system_prompt
        messages = self._build_messages(transcript, system_prompt, history)
        text, problem = "", "empty"
        for attempt in (1, 2):
            try:
                response = await self._qa_chat(
                    model=self._qa_model,
                    messages=messages,
                    stream=False,
                )
            except Exception as e:
                log.warning(
                    "qa_with_uncertainty: ollama call failed (attempt %d): %s",
                    attempt, e,
                )
                if attempt == 2 or _is_timeout(e):
                    # A timeout already waited the full read limit; a
                    # second one would only double the silence.
                    return QAWithUncertainty(
                        answer="", needs_verification=False, unreachable=True,
                    )
                continue
            raw = self._chunk_content(response)
            text, problem = _clean_spoken_answer(raw)
            if problem is None:
                break
            log.warning(
                "qa_with_uncertainty: %s answer for %r (attempt %d): %r",
                problem, transcript, attempt, raw,
            )
        if problem == "cut":
            text = _last_complete_sentences(text)
        elif problem is not None:
            text = ""
        return QAWithUncertainty(answer=text, needs_verification=False)

    # ── cold starts: what's loaded, a long-timeout twin, the warm-up ─────

    async def cold_models(self, *roles: str) -> list[str] | None:
        """The configured models for ``roles`` ("tool", "qa") that Ollama
        doesn't have ready right now — not loaded, or loaded with a
        different context window than this client asks for, which Ollama
        answers by reloading. None when Ollama can't be asked (down, or
        hung past the 2 s /api/ps limit): that is not a cold start, and
        the caller must not wait on it as if it were."""
        if self._url is None:
            return []
        loaded = await _ps_or_none(self._url, 2.0)
        if loaded is None:
            return None
        contexts: dict[str, Any] = {}
        for entry in loaded:
            if not isinstance(entry, dict):
                continue
            for field in ("name", "model"):
                if entry.get(field):
                    contexts[_model_key(entry[field])] = entry.get("context_length")
        wanted = {
            "tool": (self._tool_model, self._tool_num_ctx),
            "qa": (self._qa_model, self._num_ctx),
        }
        # Context windows /api/ps has already reported for a model that
        # differ from the one asked for (see below). Per client, so a
        # settings change (a new client) starts afresh.
        mismatches: dict[str, set[int]] = self.__dict__.setdefault("_ctx_mismatches", {})
        cold: list[str] = []
        for role in roles:
            model, num_ctx = wanted[role]
            key = _model_key(model)
            if key not in contexts:
                cold.append(model)
            elif num_ctx and contexts[key] and int(contexts[key]) != num_ctx:
                # Ollama reloads a model asked for with a different window —
                # but it also clamps a num_ctx above the model's trained
                # length and then reports the clamped window for good: a
                # mismatch that never reloads and never goes away. Counting
                # it every time would say "waking up" before every answer,
                # so a given reported window is a cold start only the first
                # time it is seen.
                seen = mismatches.setdefault(key, set())
                if int(contexts[key]) not in seen:
                    seen.add(int(contexts[key]))
                    cold.append(model)
        return list(dict.fromkeys(cold))

    def for_cold_start(self) -> "RealOllamaClient":
        """This client, but with a read timeout long enough for Ollama to
        load a model before its first byte (``ollama_load_timeout_sec``).
        The ordinary ``ollama_timeout_sec`` bounds a stalled server; with a
        cold model the load and the prompt prefill all come before the
        reply, and cutting that off would turn "still loading" into "my
        language model isn't answering". Built once per client; same models
        and knobs."""
        if self._url is None:
            return self
        if self._cold_twin is None:
            import copy

            import httpx
            from ollama import AsyncClient

            twin = copy.copy(self)
            twin._client = AsyncClient(
                host=self._url,
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=max(settings.ollama_timeout_sec, settings.ollama_load_timeout_sec),
                    write=10.0,
                    pool=5.0,
                ),
            )
            twin._cold_twin = twin
            self._cold_twin = twin
        return self._cold_twin

    async def warm_up(self, tool_schemas: list[dict[str, Any]]) -> list[str] | None:
        """Load the tool-routing and QA models ahead of the first voice
        turn, each with the exact options its real calls send (a different
        num_ctx would only make Ollama load it again), the router's system
        prompt and tool list included so that prefix is already processed.
        One generated token each. Returns the models it loaded, [] when
        both were already loaded, None when Ollama couldn't be asked."""
        cold = await self.cold_models("tool", "qa")
        if not cold:
            return cold
        twin = self.for_cold_start()
        loaded: list[str] = []
        if self._tool_model in cold and tool_schemas:
            await twin._warm_tool_model(tool_schemas)
            loaded.append(self._tool_model)
        if self._qa_model in cold and self._qa_model not in loaded:
            await twin._warm_qa_model()
            loaded.append(self._qa_model)
        return loaded

    async def _warm_tool_model(self, tool_schemas: list[dict[str, Any]]) -> None:
        tools = [{"type": "function", "function": s} for s in tool_schemas]
        await self._route_chat("hello", tools, extra_options={"num_predict": 1})

    async def _warm_qa_model(self) -> None:
        await self._qa_chat(
            model=self._qa_model,
            messages=self._build_messages(
                "hello", VOICE_QA_SYSTEM_PROMPT.format(bot=settings.bot_name), None
            ),
            stream=False,
            extra_options={"num_predict": 1},
        )

    async def extract_search_subject(
        self,
        transcript: str,
        history: list[dict[str, str]] | None = None,
    ) -> SearchSubject:
        """Name the subject + search query for a volatile-question offer
        WITHOUT answering (the QA model is forbidden to state a value).
        Runs on the QA model with ``format='json'``; any failure degrades
        to an empty subject + the raw transcript as the query, which the
        router renders as a category-phrase fallback offer."""
        messages = self._build_messages(
            transcript, _EXTRACT_SUBJECT_SYSTEM_PROMPT, history
        )
        try:
            response = await self._qa_chat(
                model=self._qa_model,
                messages=messages,
                stream=False,
                format="json",
            )
        except Exception as e:
            log.warning("extract_search_subject: ollama call failed: %s", e)
            return SearchSubject(subject="", refined_query=transcript)
        message = (
            response.get("message")
            if isinstance(response, dict)
            else getattr(response, "message", None)
        )
        content = (
            message.get("content")
            if isinstance(message, dict)
            else getattr(message, "content", "")
        ) if message is not None else ""
        return _parse_subject_json(content or "", fallback_query=transcript)

    async def extract_memories(
        self,
        transcript_block: str,
    ) -> list[ExtractedMemory]:
        """Ask the QA model for long-term-worthwhile facts from a
        block of conversation. Returns rows with confidence already
        attached; the worker filters against the configured minimum.

        On any failure (parse error, transport blip, malformed list)
        we return ``[]`` so the worker treats this pass as a no-op
        instead of writing garbage memories. Real extraction failures
        are logged for the operator to investigate.
        """
        system_prompt = _EXTRACT_MEMORIES_SYSTEM_PROMPT
        try:
            response = await self._qa_chat(
                model=self._qa_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": transcript_block},
                ],
                stream=False,
                format="json",
            )
        except Exception as e:
            log.warning("extract_memories: ollama call failed: %s", e)
            return []
        message = (
            response.get("message")
            if isinstance(response, dict)
            else getattr(response, "message", None)
        )
        content = (
            message.get("content")
            if isinstance(message, dict)
            else getattr(message, "content", "")
        ) if message is not None else ""
        return _parse_extracted_memories(content or "")


_client: OllamaClient | None = None


def get_ollama_client() -> OllamaClient:
    global _client
    if _client is not None:
        return _client
    if settings.use_stubs:
        _client = OllamaStubClient()
    else:
        _client = RealOllamaClient(
            url=settings.ollama_url,
            qa_model=settings.ollama_model,
            tool_model=settings.ollama_tool_model,
        )
    return _client


def reset_ollama_client() -> None:
    """Drop the cached client so the next ``get_ollama_client()`` rebuilds it
    from the current ``settings``. This is the 'reapply' hook for a live
    ``ollama_model`` / ``ollama_tool_model`` switch from the web Models page
    (and for ``ollama_tool_think`` / ``ollama_qa_think`` / ``ollama_keep_alive``
    / ``ollama_num_ctx`` / ``ollama_tool_num_ctx``, bound alongside):
    the RealOllamaClient binds its qa/tool model names at construction, so a
    settings mutation alone wouldn't take — clearing the singleton makes every
    call site (all of which go through ``get_ollama_client()`` per-use) pick up
    the new model without a core restart. Mirrors
    ``tts.reset_tts_client``."""
    global _client
    _client = None


# Backward-compat alias used by existing code.
ollama_client: OllamaClient = OllamaStubClient()  # replaced at startup in main.py
