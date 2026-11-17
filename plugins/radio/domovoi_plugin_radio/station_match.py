"""Matching a spoken station name against station names — pure, no I/O.

Whisper writes what it hears: "lo-fi 24 slash 7" and "Low 5 24-7" for a
favorite called "Lofi 24/7". The handler used to compare the two with
``LOWER(name) LIKE %query%``, which found neither. Here both sides are read
the way they are SAID, with the core's spoken-name rules (the SDK's
``spoken_names``: accents and punctuation folded, numbers as words both
ways round, sounds-alike keys), plus three radio-only readings:

* a spoken "slash" is the "/" of "24/7" (both read as a word break);
* "lo-fi" / "lo fi" / "low fi" are "lofi", and "hi-fi" / "high fi" "hifi";
* "radio", "station", "channel" and "the" carry no name of their own, so
  "play the lofi radio" names "Lofi" and "Radio Paradise" is named by
  "paradise".

Scoring (constants fixed before the test phrases were written, like the
core's music resolver):

* 1.0 — a shared spoken form, or the call sign said whole ("kexp");
* 0.98 — the same words once the carrier words are left out;
* :data:`CONTAINS` — every word said is a word of the name ("jazz" for
  "Jazz 24/7"), what the old substring search played;
* otherwise ``0.6 × spelling + 0.4 × sound`` over the whole name — and
  when both sides have words the other lacks, those words are scored on
  their own and the result is held to the mean of the two. That is what
  keeps "chill 24/7" from reaching "Lofi 24/7" on the strength of a shared
  "twenty four seven", while "low five 24/7" still gets as far as asking
  ("lowfive" and "lofi" sound alike).

:func:`decide` turns the ranked list into play / ask / choose / none:
play at :data:`PLAY` or above when nothing else is within :data:`MARGIN`;
ask "did you mean …?" from :data:`ASK`; offer the top two when they are
that close.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

#: Play outright from here.
PLAY = 0.90
#: Ask "did you mean …?" from here (below PLAY).
ASK = 0.75
#: Two names closer than this are both offered instead of one picked.
MARGIN = 0.05
#: Every spoken word is a word of the name.
CONTAINS = 0.92
#: The same words once the carrier words are dropped.
SAME_WORDS = 0.98

#: Words a station request carries that name nothing ("play SOME radio",
#: "my radio station" ask for no station in particular).
CARRIERS = frozenset({
    "the", "a", "an", "radio", "station", "channel", "please",
    "some", "any", "my", "our",
})
#: Names this short (as one run of letters) match exactly, never fuzzily.
SHORT = 3

_SLASH_RE = re.compile(r"(?<=\w)\s+(?:forward\s+)?slash\s+(?=\w)")
_LOFI_RE = re.compile(r"\b(?:lo|low)[\s-]?fi\b")
_HIFI_RE = re.compile(r"\b(?:hi|high)[\s-]?fi\b")


def _sn():
    # The SDK's spoken-name module, loaded on first use (it pulls in
    # rapidfuzz, anyascii and Metaphone).
    from domovoi.sdk import spoken_names

    return spoken_names


def radio_text(text: str | None) -> str:
    """The radio-only readings, before the core's spoken forms."""
    s = (text or "").lower()
    s = _SLASH_RE.sub("/", s)
    s = _LOFI_RE.sub("lofi", s)
    s = _HIFI_RE.sub("hifi", s)
    return s.strip()


@dataclass(frozen=True)
class _Form:
    spaced: str
    compact: str
    sound: str
    words: tuple[str, ...]


def _forms(text: str | None) -> list[_Form]:
    sn = _sn()
    out: list[_Form] = []
    for f in sn.spoken_forms(radio_text(text)):
        words = tuple(w for w in f.split() if w not in CARRIERS)
        out.append(_Form(f, sn.compact(f), sn.phonetic_key(f), words))
    return out


def _fuzzy(a_spaced: str, b_spaced: str) -> float:
    """``0.6 × spelling + 0.4 × sound`` of two spoken forms; spelling only
    when either has no sound key; 0 for a name of :data:`SHORT` letters or
    fewer (they match exactly or not at all)."""
    from rapidfuzz import fuzz

    sn = _sn()
    a_c, b_c = sn.compact(a_spaced), sn.compact(b_spaced)
    if not a_c or not b_c or len(a_c) <= SHORT or len(b_c) <= SHORT:
        return 0.0
    spelling = max(
        fuzz.ratio(a_c, b_c), 0.95 * fuzz.token_sort_ratio(a_spaced, b_spaced)
    ) / 100.0
    a_p, b_p = sn.phonetic_key(a_spaced), sn.phonetic_key(b_spaced)
    if not a_p or not b_p:
        return spelling
    sound = fuzz.ratio(a_p, b_p) / 100.0
    return 0.6 * spelling + 0.4 * sound


def _minus(words: Sequence[str], other: Sequence[str]) -> list[str]:
    rest = list(other)
    out: list[str] = []
    for w in words:
        if w in rest:
            rest.remove(w)
        else:
            out.append(w)
    return out


def _pair(q: _Form, n: _Form) -> float:
    if q.compact and q.compact == n.compact:
        return 1.0
    if not q.words:
        return 0.0
    if "".join(q.words) == "".join(n.words):
        return SAME_WORDS
    extra_q = _minus(q.words, n.words)
    extra_n = _minus(n.words, q.words)
    if not extra_q and len("".join(q.words)) >= SHORT:
        return CONTAINS
    total = _fuzzy(" ".join(q.words), " ".join(n.words))
    if extra_q and extra_n:
        own = _fuzzy(" ".join(extra_q), " ".join(extra_n))
        total = min(total, (total + own) / 2.0)
    return total


def score(query: str, name: str | None, call_sign: str | None = None) -> float:
    """How well ``query`` (what was heard) names a station called
    ``name`` (with call sign ``call_sign``), 0..1."""
    q_forms = _forms(query)
    if not q_forms:
        return 0.0
    best = 0.0
    if call_sign:
        sign = re.sub(r"[^a-z0-9]", "", call_sign.lower())
        if sign and any(f.compact == sign for f in q_forms):
            return 1.0
    for n in _forms(name):
        for q in q_forms:
            best = max(best, _pair(q, n))
            if best >= 1.0:
                return 1.0
    return best


@dataclass(frozen=True)
class Ranked:
    score: float
    station: Mapping[str, Any]


def rank(query: str, stations: Sequence[Mapping[str, Any]]) -> list[Ranked]:
    """Stations scored against ``query``, best first (ties by name), only
    those at :data:`ASK` or above."""
    out = [
        Ranked(score(query, s.get("name"), s.get("call_sign")), s)
        for s in stations
    ]
    out = [r for r in out if r.score >= ASK]
    out.sort(key=lambda r: (-r.score, str(r.station.get("name") or "").lower()))
    return out


@dataclass(frozen=True)
class Decision:
    """``play`` (one station), ``ask`` ("did you mean …?", one),
    ``choose`` (the top two, and how many were close), or ``none``."""

    action: str
    stations: tuple[Mapping[str, Any], ...] = ()
    close: int = 0


def decide(ranked: Sequence[Ranked]) -> Decision:
    if not ranked:
        return Decision("none")
    top = ranked[0]
    close = [r for r in ranked if top.score - r.score < MARGIN]
    if len(close) > 1:
        return Decision(
            "choose", tuple(r.station for r in close[:2]), close=len(close)
        )
    if top.score >= PLAY:
        return Decision("play", (top.station,))
    return Decision("ask", (top.station,))


def is_carrier_only(query: str | None) -> bool:
    """Nothing in ``query`` but carrier words ("the radio", "a station")."""
    words = re.findall(r"[a-z0-9]+", radio_text(query))
    return not [w for w in words if w not in CARRIERS]


__all__ = [
    "ASK",
    "CARRIERS",
    "CONTAINS",
    "Decision",
    "MARGIN",
    "PLAY",
    "Ranked",
    "decide",
    "is_carrier_only",
    "radio_text",
    "rank",
    "score",
]
