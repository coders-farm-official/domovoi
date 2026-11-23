"""Word a remembered fact the way Domovoi says it back to the person.

The implicit memory extractor (``domovoi/workers/memory_extractor.py``)
asks the Q&A model for facts in the speaker's own words ("My pets are
Biscuit and Pepper"), the same shape as a fact someone asks Domovoi to
remember ("remember that I like jazz"). When Domovoi offers one of
those facts back ("By the way, your pets are Biscuit and Pepper. Should I
remember that?") it has to turn the speaker's "I" and "my" into "you"
and "your".

Small models don't always follow the wording rule. They also write
about "the user", "they" and "their", or drop the subject altogether
("has a dog named Max"). Said back word for word, those sound like
Domovoi talking about someone else in the room, so this module
rewrites them too.

Pure string work: no model, no database.
"""

from __future__ import annotations

import re

# The speaker's own words, in their own voice. Applied before anything
# else, so "I'm" never reaches the bare "I" rule.
# "I" is matched case-sensitively, so a lower-case "i" ("i.e.") stays.
_I_FORMS: tuple[tuple[str, str], ...] = (
    (r"\bI am\b", "you are"),
    (r"\bI'm\b", "you're"),
    (r"\bI was\b", "you were"),
    (r"\bI've\b", "you've"),
    (r"\bI'd\b", "you'd"),
    (r"\bI'll\b", "you'll"),
    (r"\bI\b", "you"),
)

_FIRST_PERSON: tuple[tuple[str, str], ...] = (
    (r"\bmyself\b", "yourself"),
    (r"\bmy\b", "your"),
    (r"\bmine\b", "yours"),
    (r"\bme\b", "you"),
    (r"\bwe are\b", "you are"),
    (r"\bwe're\b", "you're"),
    (r"\bwe were\b", "you were"),
    (r"\bwe\b", "you"),
    (r"\bour\b", "your"),
    (r"\bours\b", "yours"),
)

# "us" is matched case-sensitively, so "the US" stays a country.
_US = (r"\bus\b", "you")

# "the user" / "the speaker", the extractor prompt's own words leaking
# into the fact.
_USER_NOUN = r"the (?:user|speaker)"

# A third-person subject followed by a verb that has to change with it.
_SUBJECT_VERB: tuple[tuple[str, str], ...] = (
    ("is", "are"),
    ("was", "were"),
    ("has", "have"),
    ("does", "do"),
    ("doesn't", "don't"),
    ("isn't", "aren't"),
    ("wasn't", "weren't"),
    ("hasn't", "haven't"),
)

# A fact with no subject ("has a dog named Max", "likes jazz") starts
# with one of these, or with a verb ending in -s.
_BARE_VERB_STARTS = {v for v, _ in _SUBJECT_VERB} | {
    "loves", "likes", "hates", "prefers", "works", "lives", "owns",
    "enjoys", "plays", "drinks", "eats", "goes", "wants", "needs",
    "keeps", "takes", "watches", "studies", "runs", "uses", "visits",
}


def _plain_verb(word: str) -> str:
    """The "you" form of a third-person verb: has → have, likes → like."""
    lower = word.lower()
    for third, plain in _SUBJECT_VERB:
        if lower == third:
            return plain
    if lower.endswith("ies") and len(lower) > 4:
        return word[:-3] + "y"
    if re.search(r"(?:ss|ch|sh|x|z|o)es\Z", lower):
        return word[:-2]
    if lower.endswith("s") and not lower.endswith("ss"):
        return word[:-1]
    return word


def _looks_like_verb(word: str) -> bool:
    lower = word.lower()
    return lower in _BARE_VERB_STARTS or (
        lower.endswith("s") and not lower.endswith("ss")
    )


def _has_first_person(text: str) -> bool:
    if re.search(r"\bI\b", text) or re.search(_US[0], text):
        return True
    return re.search(
        r"\b(?:my|me|mine|myself|we|our|ours)\b", text, flags=re.IGNORECASE
    ) is not None


def to_second_person(body: str) -> str:
    """Rewrite a remembered fact so it is said to the person it is about.

    "My pets are Biscuit and Pepper"      → "your pets are Biscuit and Pepper"
    "Biscuit and Pepper are their pets"   → "Biscuit and Pepper are your pets"
    "The user has a dog named Max"     → "you have a dog named Max"
    "has a dog named Max"              → "you have a dog named Max"

    The result starts lower case when it starts with a word this
    function wrote ("you", "your"), so it can follow "By the way, ".
    A name or any other word the fact started with keeps its case.
    """
    # Models write curly apostrophes too ("I’m", "the user’s").
    text = (body or "").replace("’", "'").replace("‘", "'")
    text = " ".join(text.split()).rstrip(" .!?;,")
    if not text:
        return ""

    if _has_first_person(text):
        for pattern, repl in _I_FORMS:
            text = re.sub(pattern, repl, text)
        for pattern, repl in _FIRST_PERSON:
            text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
        text = re.sub(_US[0], _US[1], text)
        return text

    # "The user's" / "the speaker's" → "your".
    text = re.sub(rf"\b{_USER_NOUN}'s\b", "your", text, flags=re.IGNORECASE)
    # "the user is" / "the user likes" → "you are" / "you like", only
    # where "the user" is the subject (the start, or after "and", "but"
    # or "that") and the next word reads as a verb. In "Sarah texts the
    # user sometimes", "sometimes" stays.
    text = re.sub(
        rf"(^|\b(?:and|but|that) ){_USER_NOUN}\s+([A-Za-z']+)",
        lambda m: m.group(1) + "you " + (
            _plain_verb(m.group(2)) if _looks_like_verb(m.group(2)) else m.group(2)
        ),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(rf"\b{_USER_NOUN}\b", "you", text, flags=re.IGNORECASE)

    # "they" / "their" can only mean the person when no one else is
    # named as the subject. A fact about the person with "their" in it
    # ("Biscuit and Pepper are their pets") reads right as "your"; the
    # extractor is told to leave third parties out, so a "their" that
    # belongs to someone else is rare and costs only an odd question.
    text = re.sub(r"\bthey are\b", "you are", text, flags=re.IGNORECASE)
    text = re.sub(r"\bthey're\b", "you're", text, flags=re.IGNORECASE)
    text = re.sub(r"\bthey were\b", "you were", text, flags=re.IGNORECASE)
    text = re.sub(r"\bthey\b", "you", text, flags=re.IGNORECASE)
    text = re.sub(r"\btheirs\b", "yours", text, flags=re.IGNORECASE)
    text = re.sub(r"\btheir\b", "your", text, flags=re.IGNORECASE)
    text = re.sub(r"\bthemselves\b", "yourself", text, flags=re.IGNORECASE)
    text = re.sub(r"\bthem\b", "you", text, flags=re.IGNORECASE)

    # A fact with no subject: "has a dog named Max".
    first, _, rest = text.partition(" ")
    if first.lower() in _BARE_VERB_STARTS:
        text = "you " + _plain_verb(first.lower()) + (" " + rest if rest else "")

    return text


def offer_text(body: str) -> str:
    """The question Domovoi adds to an answer to confirm a fact it noticed.

    Empty when there is no fact to offer.
    """
    fact = to_second_person(body)
    if not fact:
        return ""
    if fact[:1].isupper() and fact.split(" ", 1)[0] in {
        "You", "Your", "You're", "You've", "You'd", "You'll", "Yours", "Yourself",
    }:
        fact = fact[0].lower() + fact[1:]
    return f"By the way, {fact}. Should I remember that?"


# Words that carry no fact of their own: who it is about, the verb that
# links it, articles. What is left of "My pets are Waffle and Plum" is
# {pet, waffle, plum}, the same as what is left of "Waffle and Plum are
# your pets".
_FILLER_WORDS = frozenset((
    "i", "im", "ive", "id", "me", "my", "mine", "myself", "we", "our", "ours",
    "us", "you", "youre", "your", "yours", "yourself", "user", "users",
    "speaker", "they", "their", "them", "the", "a", "an", "and", "is", "are",
    "am", "was", "were", "be", "has", "have", "had", "of", "to", "called",
    "named", "s",
))


def _fact_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower().replace("’", "").replace("'", ""))
    out = set()
    for w in words:
        if w in _FILLER_WORDS:
            continue
        # "pets" and "pet" are the same fact.
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return out


def already_known(fact: str, name: str | None, known: list[str]) -> bool:
    """True when ``fact`` says nothing the house doesn't already know about
    the person: their name ("My name is Alex"), or every word of
    it is in one fact already remembered ("Waffle and Plum are my pets"
    next to "My pets are Waffle and Plum"). Offering it again sounds like
    Domovoi just learned it."""
    words = _fact_words(fact)
    if not words:
        return True
    # "My name is Alex", or "My name is Alec" when the speech-to-text
    # misheard it: the person's name is on their profile already, and a
    # memory doesn't rename anyone.
    if name and "name" in words and (
        len(words) == 2 or words <= _fact_words(name) | {"name"}
    ):
        return True
    return any(words <= _fact_words(body) for body in known)
