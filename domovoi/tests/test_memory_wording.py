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


@pytest.mark.parametrize("body", ["their pets", "the user likes jazz", "My dog"])
def test_offer_never_talks_about_the_person(body: str) -> None:
    said = offer_text(body).lower()
    for word in (" their ", "the user", " my "):
        assert word not in f" {said} "


def test_extractor_asks_for_the_speakers_own_words() -> None:
    prompt = _EXTRACT_MEMORIES_SYSTEM_PROMPT.format()
    assert "first person" in prompt
    assert '"the user"' in prompt
