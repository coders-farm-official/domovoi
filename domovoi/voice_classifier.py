"""LLM classifier for third-party voice introductions.

Given the introduction utterance ("Domovoi, this is Alex"), the
introduced name ("Alex"), and the buffered transcripts from one
unknown-voice cluster, returns a 0–1 confidence that the cluster
represents the introduced person.

Used by ``streaming.py`` after ``route()`` returns: when the introducer
speaks again and there are buffered candidate clusters, each cluster is
scored. The highest-confidence cluster above ``classifier_threshold``
gets confirmed via the introducer ("By the way, was that Alex?").

Design notes:

* The classifier is *advisory*. The introducer's yes/no remains the
  final disambiguator — the classifier just decides whether the system
  even *asks*. So a misfiring classifier costs at most a stray prompt;
  it can't enroll the wrong person.
* Defensive on failure: parse errors, Ollama unreachable, malformed
  JSON, etc. all return 0.5 (ambiguous). The introducer's confirmation
  step still works.
* JSON-only prompt. The local Ollama models we use (qwen2.5:14b for
  routing, llama3.2:3b for QA) follow JSON-mode prompts reliably, and
  the prompt explicitly forbids prose so a stray "Sure, here's my
  analysis..." doesn't sink the parse.
"""

from __future__ import annotations

import json
import logging

from domovoi.clients.ollama import get_ollama_client

log = logging.getLogger(__name__)


_PROMPT_TEMPLATE = """Decide whether an unknown voice is consistent with someone who was just introduced.

Introduction utterance (from a known speaker): "{intro_text}"
Introduced person's name: {name}
Subsequent utterances from an unknown voice:
"{candidate_text}"

Respond with EXACTLY one JSON object and nothing else:
{{"is_match": true|false, "confidence": 0.0-1.0}}

Calibration:
- 0.85+ : Unknown voice is plausibly the introduced person AND the content is consistent (greeting back, answering questions, on-topic for an introduction).
- 0.5   : Plausibly the introduced person but content is generic or ambiguous.
- 0.15  : Unknown voice is clearly a different person (asks unrelated questions, references things the introducer wouldn't share, talks about leaving).

Reply with the JSON object only. No prose."""


_SYSTEM = (
    "You are a JSON-only voice-identity classifier. Reply with EXACTLY "
    "one JSON object and nothing else."
)


async def classify_third_party_match(
    *,
    name: str,
    intro_text: str,
    candidate_text: str,
) -> float:
    """Return a 0.0–1.0 confidence that ``candidate_text`` came from the
    introduced person. Returns 0.0 for empty candidates and 0.5 for any
    classifier-side failure (Ollama down, malformed JSON, etc.) so the
    confirmation flow still works — just less confidently filtered."""
    if not candidate_text.strip():
        return 0.0

    prompt = _PROMPT_TEMPLATE.format(
        intro_text=intro_text.strip(),
        name=name,
        candidate_text=candidate_text.strip(),
    )

    try:
        client = get_ollama_client()
        text = await client.qa(prompt, system_prompt=_SYSTEM)
    except Exception as e:
        log.warning("third-party classifier call failed: %s", e)
        return 0.5

    raw = (text or "").strip()
    # Strip code fences if the model wrapped the JSON in ```...```.
    if raw.startswith("```"):
        raw = raw.strip("`")
        # `lstrip("json")` would also strip stray j/s/o/n chars from a
        # genuine JSON body; instead drop a literal "json" prefix only.
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    # Some models prefix with one line of prose despite the system
    # prompt. Find the first '{' and try to parse from there.
    brace = raw.find("{")
    if brace > 0:
        raw = raw[brace:]

    try:
        parsed = json.loads(raw)
    except Exception as e:
        log.warning("third-party classifier parse failed: %s — text=%r", e, raw[:200])
        return 0.5

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    is_match = bool(parsed.get("is_match", False))

    # If the model says "no match," cap confidence so a high-confidence
    # *negative* doesn't accidentally cross the ask threshold.
    if not is_match:
        confidence = min(confidence, 0.4)

    return max(0.0, min(1.0, confidence))
