"""Proactive web-search trigger — categorize a question for the
"want me to check that online?" offer.

The router calls ``categorize_question`` on every transcript that
reaches the QA fallthrough. A returned category fires the offer (or
auto-searches, if the speaker has opted in via web_search_prefs); a
None falls through to plain QA.

The categorizer is intentionally **conservative**: it only flags
queries whose answer is time-sensitive enough that a stale LLM
response would be actively wrong, not merely unsatisfying. The
second leg of the trigger reads the spoken answer itself:
``answer_admits_staleness`` catches a model that says its own
knowledge may be out of date ("I don't have real-time info, but…"),
and it only counts when the user actually asked a question
(``looks_like_question``). It replaced a self-reported JSON
``needs_verification`` flag that fired on background speech and
refusals and almost never on a stale fact.

Categories are PK'd with web_search_prefs.category (a CHECK
constraint) — adding one here requires a migration to widen the
constraint or auto-search prefs won't accept it.
"""

from __future__ import annotations

import re
from typing import Final

# Category names. Mirror the CHECK constraint exactly — adding
# one requires both a constant here AND a migration.
CATEGORY_CURRENT_EVENTS: Final = "current_events"
CATEGORY_PRICES_FINANCE: Final = "prices_finance"
CATEGORY_SPORTS_SCORES: Final = "sports_scores"
CATEGORY_WEATHER: Final = "weather"
CATEGORY_GENERAL_RECENT: Final = "general_recent"

ALL_CATEGORIES: Final = (
    CATEGORY_CURRENT_EVENTS,
    CATEGORY_PRICES_FINANCE,
    CATEGORY_SPORTS_SCORES,
    CATEGORY_WEATHER,
    CATEGORY_GENERAL_RECENT,
)

# The "volatile" subset — categories freshness-critical enough that the
# router skips the confident local guess entirely and goes straight to a
# subject-naming "want me to check X online?" confirmation. Per the
# maintainer's choice this is ALL categories, including the broad
# general_recent catch-all: no local guess for any time-flavored question.
VOLATILE_CATEGORIES: Final = frozenset(ALL_CATEGORIES)


# Ordered by specificity — first match wins, so put the narrow categories
# before the catch-all "general_recent". Each pattern is anchored on
# whole-word boundaries to avoid "current" inside "currently delicious"
# tripping current_events, etc.

_PRICES_RE = re.compile(
    r"\b("
    r"price of|cost of|how much (?:is|does|are)|"
    r"stock(?: price)?|share price|market cap|"
    r"exchange rate|conversion rate|"
    r"crypto|bitcoin|ethereum|dogecoin|"
    r"interest rate|mortgage rate|gas price|oil price"
    r")\b"
)

_SPORTS_RE = re.compile(
    r"\b("
    r"who won|final score|score of|"
    r"standings|playoff|championship|"
    r"game tonight|match (?:today|tonight|yesterday)|"
    r"world series|super bowl|world cup|stanley cup|nba finals"
    r")\b"
)

_CURRENT_EVENTS_RE = re.compile(
    r"\b("
    r"latest news|what(?:'s| is) (?:happening|going on)|"
    r"breaking news|in the news|"
    r"(?:current )?president|prime minister|election results?|"
    r"(?:current|today's) headlines"
    r")\b"
)

_WEATHER_RE = re.compile(
    r"\b("
    r"weather|forecast|temperature|"
    r"how (?:hot|cold|warm)|"
    r"will it (?:rain|snow)|is it (?:raining|snowing)|"
    r"degrees? (?:out|outside)|humidity|wind speed|uv index"
    r")\b"
)

# Catch-all for time-sensitive phrasings that aren't covered above.
# Triggers on words that pin a question to "right now / very recently" —
# anything an LLM with a stale training cutoff is at risk of fumbling.
_GENERAL_RECENT_RE = re.compile(
    r"\b("
    r"today|tonight|yesterday|this week|this month|this year|"
    r"right now|currently|at the moment|"
    r"latest|most recent|newest|"
    r"this morning|this afternoon|this evening"
    r")\b"
)

# Preference / advice framings. A question about what the speaker
# *should* do is answered from taste, not from a fresh web page — so a
# time word inside one ("what kind of music should I put on tonight")
# is saying *when*, not "this may have changed since your training
# cutoff". Only gates the broad general_recent catch-all: the narrow
# categories stay volatile even when phrased as advice ("should I bring
# a jacket, what's the weather tonight" still wants the real forecast).
_SUBJECTIVE_RE = re.compile(
    r"\b("
    r"should (?:i|we|he|she|they)|"
    r"what (?:kind|sort|type) of|"
    r"recommend|recommendation|suggest|suggestion|"
    r"any ideas|what(?:'s| is) a good|"
    r"do you think"
    r")\b"
)


# An answer in which the model says, in its own words, that what it knows
# may be stale — the one self-doubt signal worth an online check. Narrow on
# purpose: "I'm not sure who Chevy is" is not a stale fact, and an offer
# glued onto chit-chat is noise.
_STALE_ANSWER_RE = re.compile(
    r"\b("
    r"real[- ]time (?:info\w*|data|access|updates?|news)|"
    r"(?:knowledge|training) cut-?off|my training data|"
    r"as of my (?:last |latest )?(?:update|knowledge|training)|"
    r"(?:may|might|could) (?:be|have) (?:out of date|outdated|changed since)|"
    r"(?:don't|do not|doesn't|does not) have (?:access to )?"
    r"(?:current|up-to-date|up to date|live|the latest|recent) "
    r"(?:info\w*|data|news|details|figures)"
    r")\b"
)

# How a question opens once Whisper's punctuation is gone.
_QUESTION_OPENERS = frozenset((
    "what", "whats", "who", "whos", "whom", "whose", "when", "where", "why",
    "how", "which", "is", "are", "was", "were", "do", "does", "did", "can",
    "could", "will", "would", "should", "has", "have", "had",
))


def answer_admits_staleness(answer: str) -> bool:
    """True when the spoken answer says the model's knowledge may be out of
    date ("I don't have real-time info, but the latest I know of is…")."""
    return bool(answer) and bool(_STALE_ANSWER_RE.search(answer.lower().replace("’", "'")))


def looks_like_question(transcript: str) -> bool:
    """True for something the user asked — Whisper's closing "?" or an
    interrogative first word — as opposed to a remark or background speech
    ("German. Wow.", "sir."), where an offer to check online makes no sense."""
    text = (transcript or "").strip()
    if not text:
        return False
    if text.endswith("?"):
        return True
    first = re.sub(r"[^a-z]", "", text.split()[0].lower())
    return first in _QUESTION_OPENERS


def categorize_question(transcript: str) -> str | None:
    """Return the category for proactive web search, or None.

    ``transcript`` should already be lowercased + filler-stripped by
    the router. We re-lowercase defensively because external callers
    (tests, future handlers) may pass raw text.
    """
    if not transcript:
        return None
    t = transcript.lower()
    if _PRICES_RE.search(t):
        return CATEGORY_PRICES_FINANCE
    if _SPORTS_RE.search(t):
        return CATEGORY_SPORTS_SCORES
    if _CURRENT_EVENTS_RE.search(t):
        return CATEGORY_CURRENT_EVENTS
    if _WEATHER_RE.search(t):
        return CATEGORY_WEATHER
    if _GENERAL_RECENT_RE.search(t) and not _SUBJECTIVE_RE.search(t):
        return CATEGORY_GENERAL_RECENT
    return None
