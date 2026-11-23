"""Food allergies and diets: the remembered facts that are about safety.

A remembered fact like "I'm allergic to peanuts" or "We keep kosher" is
not a plain fact: a food suggestion that ignores it can hurt someone. This
module tells those facts apart (a keyword list on the memory's text, so it
works on every memory already stored, no new column), says which
recommendation questions they matter for, and words the short household
note the router adds after such an answer. The note is added by code, not
left to the Q&A model.

Pure string work: no model, no database.
"""

from __future__ import annotations

import re

# (pattern, what a dish has to be for this person). A None label is a
# safety fact with no one-word "free of" form ("I can't eat shellfish" has
# one, "I'm allergic to kiwi" doesn't, said generically).
_SAFETY_TERMS: tuple[tuple[str, str | None], ...] = (
    (r"\bpeanuts?\b", "peanut-free"),
    (r"\b(?:tree[ -])?nuts?\b|\bnut[ -]free\b", "nut-free"),
    (r"\bceliac\b|\bcoeliac\b|\bgluten\b|\bwheat\b", "gluten-free"),
    (r"\blactose\b|\bdairy\b|\bmilk\b", "dairy-free"),
    (r"\bshellfish\b|\bshrimp\b|\bprawns?\b", "shellfish-free"),
    (r"\beggs?\b", "egg-free"),
    (r"\bsoy\b", "soy-free"),
    (r"\bsesame\b", "sesame-free"),
    (r"\bvegan\b", "vegan"),
    (r"\bvegetarian\b", "vegetarian"),
    (r"\bpescatarian\b", "pescatarian"),
    (r"\bhalal\b", "halal"),
    (r"\bkosher\b", "kosher"),
)

# What makes a fact about safety at all: an allergy or intolerance word,
# a diet, or a "can't eat" form. "I like peanuts" is not a safety fact.
_SAFETY_RE = re.compile(
    r"\ballerg|\bintoleran|\bceliac\b|\bcoeliac\b|\banaphyla"
    r"|\bvegan\b|\bvegetarian\b|\bpescatarian\b|\bhalal\b|\bkosher\b"
    r"|\b(?:gluten|dairy|nut|peanut|lactose|egg|soy|sesame|shellfish|meat|pork)[ -]free\b"
    r"|\b(?:can't|cannot|can not|don't|doesn't|do not|does not|mustn't|shouldn't|never)\s+"
    r"(?:eat|have|drink|touch)\b",
    re.IGNORECASE,
)


def is_safety_fact(body: str) -> bool:
    """True for a food allergy, an intolerance or a dietary restriction."""
    return bool(_SAFETY_RE.search((body or "").replace("’", "'")))


def safety_labels(body: str) -> set[str]:
    """What a dish has to be for the person in this fact ("peanut-free",
    "vegetarian"). Empty for a fact that isn't about safety, or one with
    no simple label."""
    text = (body or "").replace("’", "'")
    if not is_safety_fact(text):
        return set()
    return {label for pattern, label in _SAFETY_TERMS
            if label and re.search(pattern, text, re.IGNORECASE)}


# ─── Which questions ─────────────────────────────────────────────────────

_RECOMMENDATION_RE = re.compile(
    r"\brecommend|\bsuggest|\bideas?\b|\bwhat should (?:i|we)\b|\bwhat can (?:i|we)\b"
    r"|\bany good\b|\bgood (?:place|restaurant|recipe|gift|book|movie|song|music|show)"
    r"|\bwhere (?:should|can) (?:i|we)\b|\bthings to do\b|\bsomething to (?:do|eat|cook|make|watch|read)\b"
    r"|\brecipes?\b|\bgifts?\b|\bpresent for\b|\brestaurants?\b|\bactivit(?:y|ies)\b",
    re.IGNORECASE,
)

_FOOD_RE = re.compile(
    r"\bfood\b|\beat\b|\beating\b|\brecipes?\b|\bmeals?\b|\bdinner\b|\blunch\b"
    r"|\bbreakfast\b|\bbrunch\b|\bsnacks?\b|\bcook\b|\bcooking\b|\bbake\b|\bbaking\b"
    r"|\bdish(?:es)?\b|\bcuisine\b|\bdesserts?\b|\brestaurants?\b|\btakeout\b|\btake-out\b",
    re.IGNORECASE,
)


def is_recommendation(transcript: str) -> bool:
    """A request for suggestions: restaurants, food, recipes, meal ideas,
    gifts, music, activities. Not a plain fact question."""
    return bool(_RECOMMENDATION_RE.search(transcript or ""))


def is_food_recommendation(transcript: str) -> bool:
    return is_recommendation(transcript) and bool(_FOOD_RE.search(transcript or ""))


# ─── The household note ──────────────────────────────────────────────────

UNKNOWN_SPEAKER_NOTE = (
    "I couldn't tell who's asking, so these don't take anyone's food "
    "allergies into account. Tell me who it's for and I'll filter them."
)
_GENERIC_OTHERS_NOTE = (
    "Heads up: someone in the house has a food allergy or diet these may not suit."
)


def _join(labels: list[str]) -> str:
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " or " + labels[-1]


def household_note(
    speaker_known: bool,
    speaker_facts: list[str],
    household_facts: list[str],
) -> str:
    """The line added after a food recommendation, or "" for none.

    ``speaker_facts``: the known speaker's own remembered facts (empty for
    an unknown speaker). ``household_facts``: everyone else's; for an
    unknown speaker, everyone's.
    An unknown speaker may be a guest: the note names no one and no
    allergy. A known speaker's own restrictions are in their prompt; the
    note covers the others' (by what a dish would have to be, never by
    whose it is).
    """
    others = [f for f in household_facts if is_safety_fact(f)]
    if not speaker_known:
        return UNKNOWN_SPEAKER_NOTE if others else ""
    if not others:
        return ""
    own: set[str] = set()
    for f in speaker_facts:
        own |= safety_labels(f)
    labels: set[str] = set()
    unlabelled = False
    for f in others:
        found = safety_labels(f)
        if not found:
            unlabelled = True
        labels |= found
    labels -= own
    if not labels:
        return _GENERIC_OTHERS_NOTE if unlabelled else ""
    return f"Heads up: not all of these are {_join(sorted(labels))}."
