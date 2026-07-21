"""Proactive web-search trigger — categorize a question for the
"want me to check that online?" offer.

The router calls ``categorize_question`` on every transcript that
reaches the QA fallthrough. A returned category fires the offer (or
auto-searches, if the speaker has opted in via web_search_prefs); a
None falls through to plain QA.

The categorizer is intentionally **conservative**: it only flags
queries whose answer is time-sensitive enough that a stale LLM
response would be actively wrong, not merely unsatisfying. The
Ollama JSON ``needs_verification`` flag (see
``ollama_client.qa_with_uncertainty``) is the second leg of the
hybrid trigger — either signal is enough.

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
    if _GENERAL_RECENT_RE.search(t):
        return CATEGORY_GENERAL_RECENT
    return None
