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

Matching is tolerant of mistranscription. A bled-in greeting reaches Whisper
as faint residual echo, and a small CPU model snaps unfamiliar syllables to
the nearest real words — "Beep boop. How can I help?" came back as
"Big boob, how can I help?" on the production box, which an exact match
never catches. So a leading window of the transcript is compared to each
greeting by character similarity (``difflib``), with the window allowed to
be one word shorter or longer than the greeting to absorb Whisper merging
or splitting words. A fuzzy match must also share at least
``FUZZY_MIN_MATCHED_WORDS`` whole words with the greeting (and half its
words), which is the shape of a real mistranscribed bleed — the ordinary
words survive and only the odd syllables get mangled — and which keeps a
look-alike command ("Make it quiet, please." vs the greeting "Make it
quick.") from losing its verb. Short greetings ("Yes?", "Go on.") match
exactly or not at all.

Whatever the match, the clause-boundary rule still gates the strip: the
greeting must end at punctuation and real content must follow.
"""

from __future__ import annotations

import difflib
import re

_NON_ALNUM = re.compile(r"[^a-z0-9\s]")

# Minimum difflib ratio between a leading window of the transcript and a
# greeting phrase for a fuzzy strip. 0.75 clears the mistranscriptions seen
# in the field ("big boob how can i help" vs "beep boop how can i help" is
# ~0.85) while a different sentence that merely shares the filler words
# ("bob how are you") stays under it.
FUZZY_MIN_RATIO = 0.75
# Character similarity alone is not enough: "make it quiet" scores ~0.85
# against the greeting "Make it quick." and would lose a real command its
# verb. A mistranscribed bleed has a distinctive shape — the odd syllables
# get mangled and the ordinary words come through verbatim — so a fuzzy
# match must also share at least this many whole words with the greeting,
# and at least half of the greeting's words. That confines fuzzy matching
# to greetings of three or more words; shorter ones match exactly or not
# at all, because a one- or two-word phrase resembles too many real
# commands.
FUZZY_MIN_MATCHED_WORDS = 3


def _norm(text: str) -> str:
    """Lowercase, collapse to alnum words. Apostrophes are deleted (so
    "what's" → "whats", one token) while other punctuation becomes a word
    boundary."""
    lowered = text.lower().replace("'", "").replace("’", "")
    return " ".join(_NON_ALNUM.sub(" ", lowered).split())


# Punctuation that marks a break after the greeting. A bled-in greeting is
# acoustically separate from the user's speech (clip plays → gap → speech),
# so Whisper punctuates the gap; a real command that merely starts with
# greeting words ("what's up with dogs") has no break there.
_BOUNDARY = set(".!?,;:")


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _matched_words(a: str, b: str) -> int:
    """How many whole words ``a`` and ``b`` share, in order."""
    sm = difflib.SequenceMatcher(None, a.split(), b.split())
    return sum(block.size for block in sm.get_matching_blocks())


def strip_leading_greeting(
    transcript: str,
    greeting_phrases,
    *,
    min_ratio: float = FUZZY_MIN_RATIO,
) -> str:
    """Remove a leading greeting phrase from ``transcript`` if present AND
    followed by a sentence/clause boundary.

    Returns the remainder (original casing/punctuation preserved) when a
    known greeting — exactly, or fuzzily for multi-word greetings — prefixes
    real content with a break after it; returns the transcript unchanged
    otherwise — including when the transcript *is* just a greeting, or when
    the greeting words run straight into the rest with no punctuation (a
    real command like "what's up with dogs", not a bleed). Prefers an exact
    match over a fuzzy one and a longer greeting over a shorter one, so
    "Hi there" wins over "Hi"."""
    orig_tokens = transcript.split()
    if not orig_tokens:
        return transcript
    # Normalize per whitespace token so a window of k normalized tokens
    # always maps back onto the first k original tokens, whatever
    # punctuation each one carried.
    norm_tokens = [_norm(t) for t in orig_tokens]

    # (ratio, phrase length) → window size in original tokens
    best_key: tuple[float, int] | None = None
    best_k = 0
    for phrase in greeting_phrases:
        p = _norm(phrase)
        if not p:
            continue
        p_words = len(p.split())
        fuzzy_ok = p_words >= FUZZY_MIN_MATCHED_WORDS
        for k in (p_words - 1, p_words, p_words + 1):
            # Require real content after the greeting, so a transcript
            # that's exactly a greeting is left intact.
            if k < 1 or k >= len(orig_tokens):
                continue
            # Only strip when the greeting is its own clause/sentence —
            # the last greeting token (in the original) must end with
            # boundary punctuation. This is what separates a real bleed
            # ("Hi there. play jazz") from a command that opens with
            # greeting-like words ("what's up with dogs").
            last = orig_tokens[k - 1]
            if not last or last[-1] not in _BOUNDARY:
                continue
            window = " ".join(t for t in norm_tokens[:k] if t)
            if not window:
                continue
            if window == p:
                ratio = 1.0
            elif fuzzy_ok:
                ratio = _similarity(window, p)
                if ratio < min_ratio:
                    continue
                shared = _matched_words(window, p)
                if shared < FUZZY_MIN_MATCHED_WORDS or shared * 2 < p_words:
                    continue
            else:
                continue
            key = (ratio, len(p))
            if best_key is None or key > best_key:
                best_key, best_k = key, k

    if best_key is None:
        return transcript

    remainder = " ".join(orig_tokens[best_k:]).strip()
    return remainder or transcript
