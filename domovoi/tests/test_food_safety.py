"""Allergies and diets in spoken answers (domovoi/food_safety.py).

A recognised speaker's profile goes into the Q&A prompt for a question
about them or a request for suggestions, never for a plain fact question.
After a food suggestion, code adds a fixed line when someone in the house
has a remembered allergy or diet: an unrecognised speaker (maybe a guest)
hears that nobody's allergies were taken into account, with no name and
no allergen; a recognised one hears what the others need, with no name.
"""

from __future__ import annotations

import pytest

from domovoi import food_safety
from domovoi.db.repositories import MemoriesRepository, PeopleRepository
from domovoi.models import Context, Intent
from domovoi.profile_context import build_profile_prefix, wants_profile
from domovoi.router import route
from domovoi.tests.conftest import requires_db

FACT = "Tell me, how far away is the moon from Earth?"
RECOMMENDATION = "Can you recommend a good book?"
FOOD = "What should we cook for dinner tonight?"


@pytest.mark.parametrize(
    ("body", "safety", "labels"),
    [
        ("I'm allergic to peanuts", True, {"peanut-free"}),
        ("I keep kosher", True, {"kosher"}),
        ("I have celiac disease", True, {"gluten-free"}),
        ("I'm lactose intolerant", True, {"dairy-free"}),
        ("I can't eat shellfish", True, {"shellfish-free"}),
        ("I'm vegetarian", True, {"vegetarian"}),
        ("I'm allergic to kiwi", True, set()),
        ("I like peanuts", False, set()),
        ("I want a stress-free life", False, set()),
        ("My pets are Waffle and Plum", False, set()),
    ],
)
def test_safety_facts(body: str, safety: bool, labels: set[str]) -> None:
    assert food_safety.is_safety_fact(body) is safety
    assert food_safety.safety_labels(body) == labels


@pytest.mark.parametrize(
    ("transcript", "profile", "food"),
    [
        (FACT, False, False),
        ("What is a normal blood pressure?", False, False),
        (RECOMMENDATION, True, False),
        ("Gift ideas for my sister?", True, False),
        ("Any good restaurants nearby?", True, True),
        (FOOD, True, True),
        ("Give me a recipe for brownies.", True, True),
        ("What are my pets called?", True, False),
    ],
)
def test_which_questions(transcript: str, profile: bool, food: bool) -> None:
    assert wants_profile(transcript) is profile
    assert food_safety.is_food_recommendation(transcript) is food


def test_household_note_wording() -> None:
    others = ["I'm allergic to peanuts", "I'm vegetarian"]
    # Unknown speaker: the fixed line, no allergen.
    note = food_safety.household_note(False, [], others)
    assert note == food_safety.UNKNOWN_SPEAKER_NOTE
    assert "peanut" not in note.lower() and "vegetarian" not in note.lower()
    # Known speaker: the others' needs, minus the speaker's own.
    assert food_safety.household_note(True, ["I'm vegetarian"], others) == (
        "Heads up: not all of these are peanut-free."
    )
    assert food_safety.household_note(True, [], others) == (
        "Heads up: not all of these are peanut-free or vegetarian."
    )
    # An allergy with no one-word label still gets a note.
    assert food_safety.household_note(True, [], ["I'm allergic to kiwi"]).startswith("Heads up")
    # Nothing in the house: nothing extra.
    assert food_safety.household_note(True, [], ["My pets are Waffle"]) == ""
    assert food_safety.household_note(False, [], ["My pets are Waffle"]) == ""


# ─── Through the router (the stub Q&A client echoes its profile) ─────────


async def _house(db_session, *, others_allergic: bool) -> int:
    people = PeopleRepository(db_session)
    memories = MemoriesRepository(db_session)
    alex = await people.create(name="Alex")
    await memories.add(person_id=alex, body="I'm vegetarian", source="explicit")
    await memories.add(person_id=alex, body="I like jazz", source="explicit")
    sam = await people.create(name="Sam")
    await memories.add(
        person_id=sam,
        body="I'm allergic to peanuts" if others_allergic else "I like hiking",
        source="explicit",
    )
    await db_session.commit()
    return alex


async def _ask(db_session, transcript: str, person_id: int | None) -> str:
    response = await route(
        Intent(transcript=transcript, room_id="kitchen"),
        Context(room_id="kitchen", person_id=person_id, online=False),
        db_session,
    )
    await db_session.commit()
    assert response.matched_path == "qa", transcript
    return response.text


@requires_db
@pytest.mark.asyncio
async def test_recognised_speaker(db_session) -> None:
    alex = await _house(db_session, others_allergic=True)

    fact = await _ask(db_session, FACT, alex)
    assert "[profile:" not in fact and "Heads up" not in fact

    rec = await _ask(db_session, RECOMMENDATION, alex)
    assert "[profile:" in rec and "Heads up" not in rec

    food = await _ask(db_session, FOOD, alex)
    # Their own diet is in the prompt; the others' needs are in the note.
    assert "Allergies and diet: I'm vegetarian" in food
    assert food.endswith("Heads up: not all of these are peanut-free.")
    assert "Sam" not in food


@requires_db
@pytest.mark.asyncio
async def test_recognised_speaker_no_household_allergies(db_session) -> None:
    alex = await _house(db_session, others_allergic=False)
    food = await _ask(db_session, FOOD, alex)
    assert "Allergies and diet: I'm vegetarian" in food
    assert "Heads up" not in food and "couldn't tell" not in food


@requires_db
@pytest.mark.asyncio
async def test_unrecognised_speaker(db_session) -> None:
    await _house(db_session, others_allergic=True)

    fact = await _ask(db_session, FACT, None)
    assert "couldn't tell" not in fact

    food = await _ask(db_session, FOOD, None)
    assert food.endswith(food_safety.UNKNOWN_SPEAKER_NOTE)
    for leak in ("Alex", "Sam", "peanut", "vegetarian", "[profile:"):
        assert leak.lower() not in food.lower(), leak


@requires_db
@pytest.mark.asyncio
async def test_unrecognised_speaker_no_household_allergies(db_session) -> None:
    people = PeopleRepository(db_session)
    sam = await people.create(name="Sam")
    await MemoriesRepository(db_session).add(person_id=sam, body="I like hiking", source="explicit")
    await db_session.commit()
    food = await _ask(db_session, FOOD, None)
    assert "couldn't tell" not in food and "Heads up" not in food


@requires_db
@pytest.mark.asyncio
async def test_safety_facts_survive_the_soft_cap(db_session) -> None:
    alex = await PeopleRepository(db_session).create(name="Alex")
    memories = MemoriesRepository(db_session)
    for i in range(80):
        await memories.add(person_id=alex, body=f"I once visited town number {i}", source="explicit")
    await memories.add(person_id=alex, body="I'm allergic to peanuts", source="explicit")
    await db_session.commit()
    blob = await build_profile_prefix(alex, db_session)
    assert "Allergies and diet: I'm allergic to peanuts" in blob
