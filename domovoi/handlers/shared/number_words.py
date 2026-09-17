"""Spelled-out numbers and spoken durations — one grammar for every handler.

Whisper frequently transcribes small numbers as words: "set a timer for
ten minutes", "remind me in an hour". A digit-only ``\\d+`` fast path
silently misses those and the utterance falls through to the LLM tool
router — measured on the Domovoi server 2026-09-15 via ``/v1/intent``:
"set a timer for 1 minute" hit the fast path in 0.03 s, "set a timer for
one minute" took ~4 s through the CPU-only tool model. Same command,
same household, a hundred times slower.

This module is the single source of truth for that grammar. Handlers
embed the exported regex FRAGMENTS in their own anchored fast paths and
call the parsers on the matched text:

* :data:`NUMBER_WORD_PATTERN` / :func:`parse_number` — 1..99 as words
  ("seven", "fourteen", "forty", "forty five", "forty-five").
* :data:`NUMBER_PATTERN` — digits OR words.
* :data:`DURATION_PATTERN` / :func:`parse_duration_seconds` — one or two
  "<amount> <unit>" clauses in seconds/minutes/hours, including the
  article forms ("a minute", "an hour"), halves ("half an hour",
  "an hour and a half", "one and a half hours"), and mixed digits/words
  ("1 hour and thirty minutes").

The fragments contain NO capturing groups, so a handler can keep using
its own numbered or named groups around them. They are also not greedy:
every alternative is a closed vocabulary, so embedding one cannot widen
a fast path into another handler's phrasing (the priority-band contract
in ``handlers/__init__.py`` is untouched). Transcripts reach fast paths
already lower-cased by the router; the parsers lower-case defensively
for direct callers.
"""

from __future__ import annotations

import re

# ─── Numbers ────────────────────────────────────────────────────────────────

_ONES: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
_TEENS: dict[str, int] = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS: dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

#: Every single-word number this module understands (1..19 and the tens).
NUMBER_WORDS: dict[str, int] = {**_ONES, **_TEENS, **_TENS}


def _alt(words: dict[str, int]) -> str:
    return "|".join(words)


#: 1..99 spelled out. Tens come first in the alternation so "seventy" and
#: "seventeen" are tried before "seven" — irrelevant for an anchored full
#: match (the engine backtracks) but it keeps ``re.search`` callers honest.
NUMBER_WORD_PATTERN = (
    rf"(?:(?:{_alt(_TENS)})(?:[ -](?:{_alt(_ONES)}))?|{_alt(_TEENS)}|{_alt(_ONES)})"
)

#: Digits or a spelled-out 1..99.
NUMBER_PATTERN = rf"(?:\d+|{NUMBER_WORD_PATTERN})"


def parse_number(text: str) -> int | None:
    """``"5"`` / ``"five"`` / ``"forty five"`` / ``"forty-five"`` → int.

    Returns ``None`` for anything outside the grammar (including "zero" —
    callers that need it can special-case it, none do today).
    """
    t = text.strip().lower()
    if t.isdigit():
        return int(t)
    parts = re.split(r"[ -]", t)
    if len(parts) == 1:
        return NUMBER_WORDS.get(parts[0])
    if len(parts) == 2:
        tens, ones = parts
        if tens in _TENS and ones in _ONES:
            return _TENS[tens] + _ONES[ones]
    return None


# ─── Ordinals ───────────────────────────────────────────────────────────────

# The cardinals whose ordinal isn't formed by the regular rules below.
_IRREGULAR_ORDINALS: dict[str, str] = {
    "one": "first", "two": "second", "three": "third", "five": "fifth",
    "eight": "eighth", "nine": "ninth", "twelve": "twelfth",
}


def _ordinal_word(word: str) -> str:
    """``"four"`` → ``"fourth"``, ``"twenty"`` → ``"twentieth"``,
    ``"three"`` → ``"third"``."""
    if word in _IRREGULAR_ORDINALS:
        return _IRREGULAR_ORDINALS[word]
    if word.endswith("y"):
        return f"{word[:-1]}ieth"
    return f"{word}th"


#: Every single-word ordinal this module understands, mapped to its value.
ORDINAL_WORDS: dict[str, int] = {
    _ordinal_word(word): value for word, value in NUMBER_WORDS.items()
}

_DIGIT_ORDINAL_RE = re.compile(r"^(\d+)(?:st|nd|rd|th)$")


def normalize_number_token(token: str) -> str:
    """Collapse one rendering of a number onto its digits.

    ``"11th"`` → ``"11"``, ``"eleventh"`` → ``"11"``, ``"eleven"`` → ``"11"``,
    ``"11"`` → ``"11"``. A token that isn't a number in this module's grammar
    comes back unchanged, so this is safe to map over every word of a
    transcript. Recognition is case-insensitive.

    Why it exists: the same number reaches us written several ways in one
    turn. Our own TTS writes a date as "September 11th" (``_ordinal`` in the
    clock and calculator handlers) while Whisper transcribes the *echo* of
    that speech as "September 11", which defeats any word-for-word compare
    between what we said and what we heard (see
    :mod:`domovoi.self_echo_filter`).

    Single tokens only — a compound ordinal ("twenty-first") splits into two
    words before it gets here and each half normalises on its own. The digit
    form ("21st"), which is what our handlers actually emit, is covered.
    """
    t = token.lower()
    m = _DIGIT_ORDINAL_RE.match(t)
    if m:
        return m.group(1)
    value = ORDINAL_WORDS.get(t, NUMBER_WORDS.get(t))
    return str(value) if value is not None else token


# ─── Durations ──────────────────────────────────────────────────────────────

#: Seconds per spoken unit (singular form; the grammar accepts an optional "s").
UNIT_TO_SECONDS: dict[str, int] = {"second": 1, "minute": 60, "hour": 3600}

_UNIT = "|".join(UNIT_TO_SECONDS)


def _clause(tag: str | None) -> str:
    """One duration clause. ``tag`` is a suffix for the named groups the
    parser reads (``"1"`` / ``"2"`` for the first / second clause); ``None``
    emits the same grammar with non-capturing groups for embedding.

    Accepted shapes:

    * ``5 minutes`` / ``five minutes`` / ``forty-five minutes`` / ``1 minute``
    * ``one and a half hours`` / ``5 minutes and a half``
    * ``a minute`` / ``an hour`` / ``an hour and a half``
    * ``half an hour`` / ``half a minute`` / ``a half hour``

    ``an?`` is deliberately loose about which article precedes which unit —
    "a hour" costs nothing to accept and Whisper does emit it.
    """

    def g(name: str, body: str) -> str:
        return f"(?P<{name}{tag}>{body})" if tag is not None else f"(?:{body})"

    return (
        rf"{g('num', NUMBER_PATTERN)}{g('half_mid', ' and a half')}?"
        rf" {g('unit', _UNIT)}s?{g('half_end', ' and a half')}?"
        rf"|an? {g('art_unit', _UNIT)}{g('art_half', ' and a half')}?"
        rf"|(?:a )?half (?:an? )?{g('half_unit', _UNIT)}"
    )


# Two clauses may be joined by "and", a comma, or a bare space:
# "1 hour and 30 minutes" / "1 hour, 30 minutes" / "5 minutes 30 seconds".
_SEP = r"(?:,? and |,? )"

#: A spoken duration: one clause, optionally followed by a second one.
#: No capturing groups — safe to embed inside a handler's own pattern.
DURATION_PATTERN = rf"(?:{_clause(None)})(?:{_SEP}(?:{_clause(None)}))?"

_DURATION_RE = re.compile(rf"^(?:{_clause('1')})(?:{_SEP}(?:{_clause('2')}))?$")


def _clause_seconds(gd: dict[str, str | None], tag: str) -> int | None:
    unit = gd.get(f"unit{tag}") or gd.get(f"art_unit{tag}") or gd.get(f"half_unit{tag}")
    if unit is None:
        return None  # clause absent (only ever the optional second one)
    unit_sec = UNIT_TO_SECONDS[unit]
    if gd.get(f"half_unit{tag}"):
        return unit_sec // 2
    if gd.get(f"art_unit{tag}"):
        return unit_sec + (unit_sec // 2 if gd.get(f"art_half{tag}") else 0)
    amount = parse_number(gd[f"num{tag}"] or "")
    if amount is None:
        return None  # unreachable once the regex matched; belt and braces
    half = bool(gd.get(f"half_mid{tag}") or gd.get(f"half_end{tag}"))
    return amount * unit_sec + (unit_sec // 2 if half else 0)


def parse_duration_seconds(text: str) -> int | None:
    """``"ten minutes"`` → 600, ``"an hour and a half"`` → 5400,
    ``"1 hour and 30 minutes"`` → 5400. ``None`` when ``text`` is not a
    duration in this grammar.

    Integer arithmetic throughout: "half" of a unit is ``unit // 2``, so
    "two and a half seconds" rounds down to 2 — nobody sets that timer.
    A zero amount ("0 minutes") parses to 0; callers decide whether that
    is an error (the timer handler refuses it).
    """
    m = _DURATION_RE.match(text.strip().lower())
    if not m:
        return None
    gd = m.groupdict()
    first = _clause_seconds(gd, "1")
    if first is None:
        return None
    second = _clause_seconds(gd, "2")
    return first + (second or 0)


__all__ = [
    "DURATION_PATTERN",
    "NUMBER_PATTERN",
    "NUMBER_WORD_PATTERN",
    "NUMBER_WORDS",
    "ORDINAL_WORDS",
    "UNIT_TO_SECONDS",
    "normalize_number_token",
    "parse_duration_seconds",
    "parse_number",
]
