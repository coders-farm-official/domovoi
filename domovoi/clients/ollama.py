"""Ollama LLM client.

Real path: `ollama.AsyncClient` against a local Ollama server.
Stub path: deterministic echo / "no tool call" for tests.

Three entry points:
  - route(transcript, tool_schemas)  → tool-call dict or None (intent routing)
  - qa(transcript, system_prompt?)   → full text response
  - stream_qa(...)                    → async generator yielding token chunks
                                         (for sentence-level TTS streaming)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol

from domovoi.config import settings

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


async def ps(base_url: str | None = None, timeout: float = 2.0) -> list[dict[str, Any]]:
    """Currently-loaded (VRAM-resident) models via ``GET /api/ps``.

    Distinct from :func:`list_models` (on-disk). Empty list on any failure.
    """
    import httpx

    url = f"{_ollama_base(base_url)}/api/ps"
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            resp = await c.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        log.warning("ollama /api/ps failed: %s", e)
        return []
    models = payload.get("models") if isinstance(payload, dict) else None
    return models if isinstance(models, list) else []


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
) -> AsyncIterator[str]:
    """Stream one multi-turn chat completion via ``POST /api/chat``, yielding
    assistant-content deltas as they arrive.

    ``messages`` is the Ollama chat shape: ``[{"role": "user"|"assistant"|
    "system", "content": str, "images": [<base64>, ...]?}]`` — the optional
    ``images`` list is how vision models receive attachments. Used by the
    web text-chat surface (module-level like :func:`pull_model`; the voice
    pipeline's Protocol clients are untouched). No overall read timeout — a
    long completion on a big model is legitimate; only connect is bounded.
    Raises on transport errors so the caller can surface the failure."""
    import httpx

    url = f"{_ollama_base(base_url)}/api/chat"
    timeout = httpx.Timeout(connect=connect_timeout, read=None, write=30.0, pool=connect_timeout)
    async with httpx.AsyncClient(timeout=timeout) as c:
        async with c.stream(
            "POST", url, json={"model": model, "messages": messages, "stream": True}
        ) as resp:
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
                    return


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


# Wrapper around the JSON answer + self-doubt flag returned by
# ``qa_with_uncertainty``. ``needs_verification`` is the LLM's own
# guess at whether the answer should be checked against a web source
# (stale training data, fast-moving facts, etc.) — paired with the
# heuristic categorizer (``domovoi.uncertainty``) as the two
# legs of the proactive web-search offer.
@dataclass
class QAWithUncertainty:
    answer: str
    needs_verification: bool
    candidate_claim: str = ""


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


_UNCERTAINTY_SYSTEM_PROMPT = (
    "You are {bot}, a helpful voice assistant. Answer the user's "
    "question concisely and conversationally. Then judge whether your "
    "answer could be stale or unreliable — anything about current "
    "events, prices, scores, recent releases, or details from after "
    "your training cutoff. Output ONLY a JSON object with this exact "
    "shape, no preface or explanation:\n"
    '{{"answer": "<your spoken answer>", '
    '"needs_verification": <true|false>, '
    '"candidate_claim": "<one short verifiable claim from your answer, '
    'or empty string>"}}\n'
    "Set needs_verification=true when the answer depends on facts "
    "that change over time or that you're not confident about. Set "
    "it false for timeless facts (math, definitions, well-known "
    "history) you're confident in."
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


def _parse_qa_json(raw: str, fallback_answer: str = "") -> QAWithUncertainty:
    """Parse the JSON object returned by qa_with_uncertainty.

    Defensive on every field — models sometimes wrap the JSON in
    prose, drop the candidate_claim, or stringify the bool. We fall
    back to ``needs_verification=False`` on any parse failure so a
    malformed response never spuriously triggers a web-search offer.
    """
    if not raw:
        return QAWithUncertainty(answer=fallback_answer, needs_verification=False)
    # Models occasionally bracket the JSON with prose ("Here you go:
    # {...}"). Find the first { and last } and parse just that span.
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return QAWithUncertainty(answer=raw.strip(), needs_verification=False)
    blob = raw[start : end + 1]
    try:
        parsed = json.loads(blob)
    except Exception:
        return QAWithUncertainty(answer=raw.strip(), needs_verification=False)
    if not isinstance(parsed, dict):
        return QAWithUncertainty(answer=raw.strip(), needs_verification=False)
    answer = str(parsed.get("answer") or "").strip() or fallback_answer
    raw_flag = parsed.get("needs_verification")
    if isinstance(raw_flag, bool):
        needs = raw_flag
    elif isinstance(raw_flag, str):
        needs = raw_flag.strip().lower() in ("true", "yes", "1")
    else:
        needs = False
    claim = str(parsed.get("candidate_claim") or "").strip()[:300]
    return QAWithUncertainty(answer=answer, needs_verification=needs, candidate_claim=claim)


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


class RealOllamaClient:
    """Async Ollama client with tool-calling + streaming.

    Imports `ollama` lazily inside `__init__` so USE_STUBS=true doesn't require
    the package installed (though it's a core dep — the laziness is cheap
    insurance against transient install issues).
    """

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
        self._qa_model = qa_model
        self._tool_model = tool_model

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
            response = await self._client.chat(
                model=self._tool_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an intent router for a voice assistant. The transcript "
                            "is speech-to-text and may contain mis-heard or spurious words, "
                            "especially at the start (a garbled wake word or noise) — focus "
                            "on the actionable core of the utterance, not stray leading "
                            "words. If the user clearly wants an action (play/find/add music, "
                            "set a timer, control the home, etc.), pick the closest matching "
                            "tool even when the phrasing is imperfect. Only respond with "
                            "plain text and no tool call when the utterance is genuinely a "
                            "question or has no actionable intent."
                        ),
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
                options={"temperature": 0},
            )
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
        response = await self._client.chat(
            model=self._qa_model,
            messages=self._build_messages(transcript, system_prompt, history),
            stream=False,
        )
        message = response.get("message") if isinstance(response, dict) else getattr(response, "message", None)
        if message is None:
            return ""
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        return (content or "").strip()

    async def stream_qa(
        self,
        transcript: str,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        stream = await self._client.chat(
            model=self._qa_model,
            messages=self._build_messages(transcript, system_prompt, history),
            stream=True,
        )
        async for chunk in stream:
            message = chunk.get("message") if isinstance(chunk, dict) else getattr(chunk, "message", None)
            if message is None:
                continue
            content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
            if content:
                yield content

    async def qa_with_uncertainty(
        self,
        transcript: str,
        history: list[dict[str, str]] | None = None,
        profile_prefix: str | None = None,
    ) -> QAWithUncertainty:
        """Ask the QA model for an answer plus a self-doubt flag.

        Drives the proactive "want me to check that online?" offer. We
        ask for JSON via Ollama's ``format='json'`` parameter so the
        model is constrained to emit a parseable object — on parse
        failure we still produce a usable ``QAWithUncertainty`` with
        ``needs_verification=False`` so a bad parse never turns into
        a spurious offer.

        ``profile_prefix`` is the speaker's memories + favorites +
        prefs blob. Prepended to the system prompt so QA
        answers can lean on personal context without leaking into
        the JSON output contract.
        """
        system_prompt = _UNCERTAINTY_SYSTEM_PROMPT.format(bot=settings.bot_name)
        if profile_prefix:
            system_prompt = profile_prefix.rstrip() + "\n\n" + system_prompt
        messages = self._build_messages(transcript, system_prompt, history)
        try:
            response = await self._client.chat(
                model=self._qa_model,
                messages=messages,
                stream=False,
                format="json",
            )
        except Exception as e:
            log.warning("qa_with_uncertainty: ollama call failed: %s", e)
            # Fall back to plain qa so the user still gets an answer.
            try:
                plain = await self.qa(transcript, history=history)
            except Exception:
                plain = ""
            return QAWithUncertainty(
                answer=plain, needs_verification=False, candidate_claim=""
            )
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
        return _parse_qa_json(content or "")

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
            response = await self._client.chat(
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
            response = await self._client.chat(
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
    ``ollama_model`` / ``ollama_tool_model`` switch from the web Models page:
    the RealOllamaClient binds its qa/tool model names at construction, so a
    settings mutation alone wouldn't take — clearing the singleton makes every
    call site (all of which go through ``get_ollama_client()`` per-use) pick up
    the new model without a core restart. Mirrors
    ``tts.reset_tts_client``."""
    global _client
    _client = None


# Backward-compat alias used by existing code.
ollama_client: OllamaClient = OllamaStubClient()  # replaced at startup in main.py
