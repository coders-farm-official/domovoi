"""Recognize the satellite's own spoken response inside a transcript.

Sibling of :mod:`domovoi.greeting_filter`, for a louder version of the same
failure. The greeting filter handles a short clip bleeding past the AEC at
the START of a capture; this handles the satellite's actual TTS reply
bleeding into a capture that BARGE-IN opened while the speaker was still
playing.

The 2026-09-10 office log is the worked example. Turn #56 the user said
"Stop.", the satellite said "Stopped.", and 3.9 seconds later turn #57
arrived with the transcript "stopped." — its own voice, routed as a new
question, which it then tried to answer. Barge-in fires on 250 ms of
anything VAD calls speech while playback is live; residual echo after an
imperfect AEC clears that bar, and the frames that tripped it are carried
into the next utterance as ``_barge_prefix``. The satellite interrupts
itself and then talks to itself.

Two checks, deliberately different in confidence:

  * :func:`is_self_echo` — the transcript is essentially NOTHING but words
    the satellite just said, contiguously. High confidence, and the caller
    drops the turn outright. This is the #57 case.
  * :func:`strip_leading_echo` — a leading run of the reply followed by a
    clause boundary, then real user speech. Conservative in exactly the way
    the greeting filter is: it would rather leave an echo in than eat a
    command.

A LIMIT worth stating plainly, because it bounds what this can fix: barge-in
cuts playback mid-word, so Whisper is transcribing a truncated, degraded
signal and tends to render it as a fluent paraphrase rather than the actual
words ("...associated with the color amber" came back as "the name Amber is
associated with the color A."). Paraphrases don't match contiguously, so a
partial echo like that survives both checks. This is a net, not a fix. The
fix is not letting a barge trigger on the satellite's own voice — see
``[barge_in] require_wake_word`` and the min_speech_ms / VAD thresholds.
"""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9\s]")

# Fraction of the transcript's words that must be accounted for by a
# contiguous run of the satellite's own reply before we call the whole thing
# an echo and drop it. Not 1.0: Whisper routinely adds or drops a filler word
# at the edges of a clipped capture.
WHOLE_ECHO_RATIO = 0.8

# Shortest leading run worth stripping. Below this, a match is more likely a
# coincidence of common words ("what is the...") than a real bleed.
MIN_PREFIX_WORDS = 3

# Punctuation marking a break after the echoed span — same reasoning as the
# greeting filter: the bleed is acoustically separate from the user's speech,
# so Whisper punctuates the seam.
_BOUNDARY = set(".!?,;:")


def _norm_words(text: str) -> list[str]:
    """Lowercase alphanumeric word tokens. Apostrophes are deleted (so
    "what's" stays one token) while other punctuation becomes a boundary,
    which keeps the token count aligned with the original's whitespace
    split — the same contract :mod:`domovoi.greeting_filter` relies on to
    reconstruct a prefix in the original casing."""
    lowered = text.lower().replace("'", "").replace("’", "")
    return _NON_ALNUM.sub(" ", lowered).split()


def _longest_contiguous_prefix(words: list[str], inside: list[str]) -> int:
    """Length of the longest leading run of ``words`` appearing as a
    contiguous block anywhere in ``inside``. 0 when nothing matches.

    Contiguous rather than fuzzy on purpose: a subsequence match with gaps
    would let any transcript built from common words look like an echo of
    any sufficiently long reply, and the penalty for a false positive here
    is a silently discarded turn.
    """
    if not words or not inside:
        return 0
    for k in range(min(len(words), len(inside)), 0, -1):
        head = words[:k]
        for start in range(len(inside) - k + 1):
            if inside[start:start + k] == head:
                return k
    return 0


def is_self_echo(
    transcript: str, spoken: str, *, min_ratio: float = WHOLE_ECHO_RATIO
) -> bool:
    """True when ``transcript`` is essentially just the satellite repeating
    ``spoken`` back to itself.

    ``spoken`` is the text of the reply that was playing when the capture
    opened. Empty inputs are never an echo — a caller with nothing to
    compare against must not start discarding turns.
    """
    t_words = _norm_words(transcript)
    s_words = _norm_words(spoken)
    if not t_words or not s_words:
        return False
    matched = _longest_contiguous_prefix(t_words, s_words)
    return (matched / len(t_words)) >= min_ratio


def strip_leading_echo(
    transcript: str, spoken: str, *, min_words: int = MIN_PREFIX_WORDS
) -> str:
    """Remove a leading echo of ``spoken`` from ``transcript``.

    Returns the remainder with original casing and punctuation intact when
    the transcript opens with at least ``min_words`` of the satellite's own
    reply AND that run ends on a clause boundary AND real content follows.
    Returns the transcript unchanged otherwise — including when the whole
    thing is an echo, which is :func:`is_self_echo`'s call to make, not this
    one's.
    """
    orig_tokens = transcript.split()
    t_words = _norm_words(transcript)
    s_words = _norm_words(spoken)
    if not orig_tokens or not t_words or not s_words:
        return transcript

    k = _longest_contiguous_prefix(t_words, s_words)
    if k < min_words or k >= len(orig_tokens):
        return transcript

    # The echoed span must be its own clause — the same guard the greeting
    # filter uses to tell a bleed from a command that merely opens with the
    # same words.
    last_echo_token = orig_tokens[k - 1]
    if not last_echo_token or last_echo_token[-1] not in _BOUNDARY:
        return transcript

    remainder = " ".join(orig_tokens[k:]).strip()
    return remainder or transcript
