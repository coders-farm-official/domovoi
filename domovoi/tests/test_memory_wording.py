"""The question Domovoi asks before keeping a fact it noticed.

A live turn (2026-11-17) offered "By the way — Biscuit and Pepper are
their pets. Should I remember that?": the extractor's fact said word for
word, which sounded like Domovoi talking about someone else. The offer
is now said to the person (domovoi/memory_wording.py).
"""

from __future__ import annotations

import pytest

from domovoi.clients.ollama import _EXTRACT_MEMORIES_SYSTEM_PROMPT
from domovoi.memory_wording import offer_text, to_second_person


@pytest.mark.parametrize(
    ("body", "said"),
    [
        # The shape the extractor is now asked for: the speaker's own words.
        ("My pets are Biscuit and Pepper", "your pets are Biscuit and Pepper"),
        ("I am allergic to peanuts.", "you are allergic to peanuts"),
        ("I'm training for a marathon", "you're training for a marathon"),
        ("We moved from the US in 2019", "you moved from the US in 2019"),
        # What small models write anyway.
        ("Biscuit and Pepper are their pets", "Biscuit and Pepper are your pets"),
        ("The user has a dog named Max", "you have a dog named Max"),
        ("The user's daughter is Priya", "your daughter is Priya"),
        ("The user likes jazz", "you like jazz"),
        ("They work night shifts", "you work night shifts"),
        ("has a dog named Max", "you have a dog named Max"),
        ("is a nurse", "you are a nurse"),
        ("Prefers tea over coffee", "you prefer tea over coffee"),
        # Someone named stays named.
        ("Sarah watches hockey on Sundays", "Sarah watches hockey on Sundays"),
        # Curly apostrophes, as models often write them.
        ("I’m training for a marathon", "you're training for a marathon"),
        ("The user’s daughter is Priya", "your daughter is Priya"),
        # "us" alone still means the speaker.
        ("Max lives with us", "Max lives with you"),
        # "the user" as the object: the next word isn't a verb to change.
        ("Sarah texts the user sometimes", "Sarah texts you sometimes"),
        ("Sarah visits the user weekends", "Sarah visits you weekends"),
        ("The user and their wife live in Ohio", "you and your wife live in Ohio"),
        # A lower-case "i" isn't the speaker.
        ("i.e. I like jazz", "i.e. you like jazz"),
    ],
)
def test_fact_is_said_to_the_person(body: str, said: str) -> None:
    assert to_second_person(body) == said


def test_offer_reads_as_one_question() -> None:
    assert offer_text("Biscuit and Pepper are their pets") == (
        "By the way, Biscuit and Pepper are your pets. Should I remember that?"
    )
    assert offer_text("My pets are Biscuit and Pepper.") == (
        "By the way, your pets are Biscuit and Pepper. Should I remember that?"
    )


@pytest.mark.parametrize("body", ["", "  ", " . "])
def test_no_fact_no_offer(body: str) -> None:
    assert offer_text(body) == ""


@pytest.mark.parametrize("body", ["their pets","the user likes jazz", "My dog"])
def test_offer_never_talks_about_the_person(body: str) -> None:
    said = offer_text(body).lower()
    for word in (" their ", "the user", " my "):
        assert word not in f" {said} "


def test_extractor_asks_for_the_speakers_own_words() -> None:
    prompt = _EXTRACT_MEMORIES_SYSTEM_PROMPT.format()
    assert "first person" in prompt
    assert '"the user"' in prompt


# ─── Already known: never offered as if new ─────────────────────────────


@pytest.mark.parametrize(
    ("fact", "name", "known", "expected"),
    [
        # The person's own name, also when the speech-to-text misheard it.
        ("My name is Alex", "Alex", [], True),
        ("My name is Alec", "Alex", [], True),
        # A remembered fact said another way.
        ("Waffle and Plum are my pets", "Alex", ["My pets are Waffle and Plum"], True),
        ("Waffle and Plum are their pets", "Alex", ["My pets are Waffle and Plum"], True),
        # News.
        ("I work nights", "Alex", ["My pets are Waffle and Plum"], False),
        ("My dog's name is Rex", "Alex", [], False),
        ("My pets are Waffle, Plum and Biscuit", "Alex", ["My pets are Waffle and Plum"], False),
    ],
)
def test_already_known(fact, name, known, expected) -> None:
    from domovoi.memory_wording import already_known

    assert already_known(fact, name, known) is expected
