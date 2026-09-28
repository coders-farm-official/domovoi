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

A transcript that is *only* the greeting is the other half of the same
bleed: the capture closed on the pause after the greeting, before the user
spoke. :func:`is_greeting_only` recognises it so the streaming layer can
drop the turn instead of routing Domovoi's own words as a command.
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


# Every apostrophe Whisper or a greeting bank might spell a contraction
# with: straight, curly (right and left), modifier letter, backtick, acute.
_APOSTROPHES = str.maketrans("", "", "'’‘ʼ`´")


def _norm(text: str) -> str:
    """Lowercase, collapse to alnum words. Apostrophes are deleted (so
    "what's" → "whats", one token) while other punctuation — quotes of any
    kind included — becomes a word boundary."""
    lowered = text.lower().translate(_APOSTROPHES)
    return " ".join(_NON_ALNUM.sub(" ", lowered).split())


# Whisper writes a contraction out in full as often as not ("I am
# listening." for the greeting "I'm listening."); fold the common ones to
# the contracted spelling, which is how _norm leaves the greeting.
_EXPANSIONS = (
    (re.compile(r"\bi am\b"), "im"),
    (re.compile(r"\byou are\b"), "youre"),
    (re.compile(r"\bwhat is\b"), "whats"),
    (re.compile(r"\bit is\b"), "its"),
    (re.compile(r"\bthat is\b"), "thats"),
    (re.compile(r"\bhere is\b"), "heres"),
    (re.compile(r"\bwho is\b"), "whos"),
    (re.compile(r"\blet us\b"), "lets"),
    (re.compile(r"\bi will\b"), "ill"),
    (re.compile(r"\bdo not\b"), "dont"),
    (re.compile(r"\bcan ?not\b"), "cant"),
)


def _canon(text: str) -> str:
    """:func:`_norm`, with written-out contractions folded."""
    n = _norm(text)
    for pattern, contracted in _EXPANSIONS:
        n = pattern.sub(contracted, n)
    return n


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


# How close a transcript must be to the one greeting that is known to have
# played (the satellite names its clip) to count as that greeting, compared
# with the spaces taken out. Looser than FUZZY_MIN_RATIO because there is a
# single candidate rather than a whole bank.
KNOWN_GREETING_MIN_RATIO = 0.8

# Hesitation noises Whisper may put around a bled-in greeting ("Uh, back so
# soon?"). Only these may pad a greeting-only match; any other extra word
# makes it a sentence of the user's ("What's going on here?").
_FILLER = frozenset((
    "uh", "um", "umm", "uhm", "er", "erm", "ah", "oh", "hmm", "hm", "mm", "mhm",
))


def _unpadded(words: list[str]) -> list[str]:
    start, end = 0, len(words)
    while start < end and words[start] in _FILLER:
        start += 1
    while end > start and words[end - 1] in _FILLER:
        end -= 1
    return words[start:end]


# How close each word Whisper got wrong must stay to the greeting's word it
# replaced (character similarity of the two spans) for a whole transcript
# to still count as that greeting. A bled-in greeting comes back misspelt —
# "big boob" for "beep boop" (0.59), "to" for "so", "ya" for "you" (0.4) —
# while a person's question that merely shares a greeting's frame swaps in
# a different word: "What can YOU do for ME?" against "What can I do for
# you?", "How can YOU help?", "What do you MEAN?" against "What do you
# need?" (0.25 and under). The overall ratio can't tell those apart; the
# swapped words can. Dropping a real question in silence costs far more
# than answering a stray echo, so the bar leans toward the question.
_MISHEARD_WORD_MIN_RATIO = 0.4


def _only_misheard(t_words: list[str], p_words: list[str]) -> bool:
    """Whether ``t_words`` differ from the greeting's ``p_words`` only the
    way a mistranscription does: greeting words dropped, or replaced by
    near-spellings — never a word added, and never a different word."""
    sm = difflib.SequenceMatcher(None, t_words, p_words, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "delete":
            return False          # a word the greeting doesn't have
        if op == "replace" and _similarity(
            " ".join(t_words[i1:i2]), " ".join(p_words[j1:j2])
        ) < _MISHEARD_WORD_MIN_RATIO:
            return False
    return True


def is_greeting_only(transcript: str, greeting_phrases, *, played: bool = False) -> bool:
    """Whether ``transcript`` is nothing but one of ``greeting_phrases`` —
    the satellite hearing its own wake greeting, not a command.

    Office row #187 on 2026-09-28: the greeting "Back so soon?" bled past
    the array's AEC, the capture ended on the pause after it, and Whisper's
    "Back so soon." was routed to the LLM as if the user had said it.
    :func:`strip_leading_greeting` deliberately leaves such a turn alone
    (there is no content after the greeting to keep); this is the check
    that lets the caller drop it.

    Tolerant the way a bled-in greeting is mistranscribed: case,
    punctuation, whitespace, any style of apostrophe or quote, written-out
    contractions ("I am listening."), words merged or split ("Hey there" /
    "Heythere"), hesitation noises around it ("Uh, back so soon?").
    Greetings of ``FUZZY_MIN_MATCHED_WORDS`` or more words also match
    fuzzily under the same rule as the leading strip, with a word fewer
    allowed but never one more — and every word that differs must be a
    near-spelling of the greeting's (``_only_misheard``), so a question
    built on a greeting's frame ("What can you do for me?", "How can you
    help?") is never taken for the greeting and dropped. ``played=True``
    says ``greeting_phrases`` is the single greeting known to have played
    this turn, which allows a close match at any length, a short greeting
    included (``KNOWN_GREETING_MIN_RATIO``); against the whole bank a short
    greeting must match exactly, because "Hello." and "Yes?" are also
    things a person says.
    """
    words = _canon(transcript).split()
    if not words:
        return False
    forms = {" ".join(words)}
    unpadded = _unpadded(words)
    if unpadded:
        forms.add(" ".join(unpadded))
    for phrase in greeting_phrases:
        p = _canon(phrase)
        if not p:
            continue
        p_words = p.split()
        p_compact = p.replace(" ", "")
        for t in forms:
            t_words = t.split()
            if t == p or t.replace(" ", "") == p_compact:
                return True
            if not len(p_words) - 1 <= len(t_words) <= len(p_words):
                continue
            if not _only_misheard(t_words, p_words):
                continue
            if len(p_words) >= FUZZY_MIN_MATCHED_WORDS:
                shared = _matched_words(t, p)
                if (
                    _similarity(t, p) >= FUZZY_MIN_RATIO
                    and shared >= FUZZY_MIN_MATCHED_WORDS
                    and shared * 2 >= len(p_words)
                ):
                    return True
            # The one line known to have played may be misheard more
            # loosely, at any length ("Back to soon." for "Back so soon?").
            if played and _similarity(t.replace(" ", ""), p_compact) >= KNOWN_GREETING_MIN_RATIO:
                return True
    return False
