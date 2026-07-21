"""Strip a satellite's own wake-word greeting out of a transcript.

The satellite plays a short greeting clip ("Hi there.", "What's up?") the
instant the wake word fires, overlapping command capture — the array's AEC
is supposed to keep it out of the mic, but it isn't perfect, so the greeting
sometimes bleeds in and Whisper transcribes it as a prefix:

    "Hi there. Say something mean."   ← greeting + the actual command

When the satellite reports it played a greeting this turn
(``greeting_played`` on ``utterance_end``), the core runs the
transcript through :func:`strip_leading_greeting`, which removes a leading
known greeting phrase (from the enabled ``client_greetings`` bank) so only
the real command is routed. Matching is on normalized text (lowercased,
punctuation dropped) so Whisper's casing/punctuation doesn't matter, and it
never strips a turn that is *only* a greeting-shaped phrase.
"""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9\s]")


def _norm(text: str) -> str:
    """Lowercase, collapse to alnum words. Apostrophes are deleted (so
    "what's" → "whats", one token) while other punctuation becomes a word
    boundary — this keeps the normalized word count aligned with the
    original's whitespace tokens for prefix reconstruction."""
    lowered = text.lower().replace("'", "").replace("’", "")
    return " ".join(_NON_ALNUM.sub(" ", lowered).split())


# Punctuation that marks a break after the greeting. A bled-in greeting is
# acoustically separate from the user's speech (clip plays → gap → speech),
# so Whisper punctuates the gap; a real command that merely starts with
# greeting words ("what's up with dogs") has no break there.
_BOUNDARY = set(".!?,;:")


def strip_leading_greeting(transcript: str, greeting_phrases) -> str:
    """Remove a leading greeting phrase from ``transcript`` if present AND
    followed by a sentence/clause boundary.

    Returns the remainder (original casing/punctuation preserved) when a
    known greeting prefixes real content with a break after it; returns the
    transcript unchanged otherwise — including when the transcript *is* just
    a greeting, or when the greeting words run straight into the rest with no
    punctuation (a real command like "what's up with dogs", not a bleed).
    Picks the longest matching greeting so "Hi there" wins over "Hi"."""
    orig_tokens = transcript.split()
    norm = _norm(transcript)
    if not norm or not orig_tokens:
        return transcript

    best = ""
    for phrase in greeting_phrases:
        p = _norm(phrase)
        if not p:
            continue
        # Require real content after the greeting (the trailing space), so a
        # transcript that's exactly a greeting is left intact.
        if norm.startswith(p + " ") and len(p) > len(best):
            best = p

    if not best:
        return transcript

    n_words = len(best.split())
    if n_words >= len(orig_tokens):
        return transcript

    # Only strip when the greeting is its own clause/sentence — the last
    # greeting word (in the original) must end with boundary punctuation.
    # This is what separates a real bleed ("Hi there. play jazz") from a
    # command that opens with greeting-like words ("what's up with dogs").
    last_greeting_token = orig_tokens[n_words - 1]
    if not last_greeting_token or last_greeting_token[-1] not in _BOUNDARY:
        return transcript

    remainder = " ".join(orig_tokens[n_words:]).strip()
    return remainder or transcript
