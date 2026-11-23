"""The spoken Q&A prompt for a recognised speaker.

A live turn: "Tell me, how far away is the moon from Earth?" was answered
(238,855 miles), and asked again a few minutes later by the same person,
now recognised by voice, it got "I'm not a navigational tool, I don't know
the moon's distance from Earth, sorry." The recognised turn's prompt also
carried the person's name and remembered facts. A plain fact question now
gets the same prompt as anyone's; the profile goes in only for a question
about the speaker.
"""

from __future__ import annotations

import pytest

from domovoi.clients.ollama import voice_qa_system_prompt
from domovoi.db.repositories import MemoriesRepository, PeopleRepository
from domovoi.models import Context, Intent
from domovoi.profile_context import about_the_speaker, build_profile_prefix
from domovoi.router import route
from domovoi.tests.conftest import requires_db


@pytest.mark.parametrize(
    ("transcript", "expected"),
    [
        ("Tell me, how far away is the moon from Earth?", False),
        ("What is a normal blood pressure?", False),
        ("Give me a fact about Mars.", False),
        ("What are my pets called?", True),
        ("What should I cook tonight?", True),
        ("Remind me what my wife likes.", True),
    ],
)
def test_about_the_speaker(transcript: str, expected: bool) -> None:
    assert about_the_speaker(transcript) is expected


@requires_db
@pytest.mark.asyncio
async def test_fact_question_prompt_is_the_same_as_anyones(db_session) -> None:
    person_id = await PeopleRepository(db_session).create(name="Alex")
    await MemoriesRepository(db_session).add(
        person_id=person_id, body="My pets are Waffle and Plum", source="explicit",
    )
    await db_session.commit()

    # What a recognised speaker's prompt used to carry.
    profile = await build_profile_prefix(person_id, db_session)
    assert "Name: Alex" in profile and "Waffle and Plum" in profile
    recognised = voice_qa_system_prompt(profile)
    anonymous = voice_qa_system_prompt(None)
    assert recognised.startswith(anonymous) and recognised != anonymous
    # No offer instruction in either: offers are added after the answer.
    assert "remember" not in recognised.lower()

    # The stub Q&A client echoes the profile it was given.
    ctx = Context(room_id="kitchen", person_id=person_id, online=False)
    fact = await route(
        Intent(transcript="Tell me, how far away is the moon from Earth?", room_id="kitchen"),
        ctx, db_session,
    )
    assert fact.matched_path == "qa"
    assert "[profile:" not in fact.text
    personal = await route(
        Intent(transcript="What are my pets called?", room_id="kitchen"),
        Context(room_id="kitchen", person_id=person_id, online=False), db_session,
    )
    assert "[profile:" in personal.text and "Waffle and Plum" in personal.text
