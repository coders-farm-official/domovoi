"""A bare "forget that" (N15, capability case 15.11).

Until 2026-11 a bare "forget that" / "forget it" was the dismiss handler's
("No worries.") and nothing was forgotten, though the person who had just
said "remember that …" believed it was. The rule now
(``MemoryHandler._forget_last``), in order:

1. the previous turn of this conversation saved a memory → forget exactly
   that one, for its owner only ("OK, forgotten: <fact>.");
2. the previous turn asked a question that is still parked → the forget
   drops the question (a memory offer is turned down like a "no");
3. "forget it" / "forget about it" / "never mind, forget that" → a plain
   brush-off, as before;
4. otherwise → "Forget what?", a parked choice whose reply names the fact.

"forget that <fact>" is unchanged.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.confirmations import PENDING_CONFIRMATION_KEY, request_confirmation
from domovoi.db.repositories import MemoriesRepository, PeopleRepository, SessionRepository
from domovoi.handlers.dismiss import _ACKS
from domovoi.handlers.memory import (
    _FORGET_LAST_RE,
    _FORGET_MEMORY_RE,
    _FORGET_WHAT_TEXT,
    LAST_SAVED_KEY,
)
from domovoi.models import Context, Intent
from domovoi.router import first_fast_path, normalize_transcript, route, strip_leading_filler
from domovoi.tests.conftest import requires_db

# ─── Phrasing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("said", "what", "lead"),
    [
        ("forget that", "that", False),
        ("forget it", "it", False),
        ("forget about it", "about it", False),
        ("forget about that", "about that", False),
        ("forget what i just said", "what i just said", False),
        ("forget what i said", "what i said", False),
        ("forget i said that", "i said that", False),
        ("never mind, forget that", "that", True),
        ("nevermind forget it", "it", True),
        ("just forget it please", "it", False),
    ],
)
def test_bare_forget_phrasings(said: str, what: str, lead: bool) -> None:
    m = _FORGET_LAST_RE.match(said)
    assert m, said
    assert m.group("what") == what
    assert bool(m.group("lead")) is lead


@pytest.mark.parametrize(
    "said",
    ["forget that i like jazz", "forget that name", "forget me", "forget my favorite team",
     "forget what i said about the dentist", "forget the timer"],
)
def test_not_a_bare_forget(said: str) -> None:
    assert not _FORGET_LAST_RE.match(said), said


@pytest.mark.parametrize(
    ("said", "winner"),
    [
        ("forget that", "memory"),
        ("Forget it.", "memory"),
        ("Okay, forget about it", "memory"),
        ("Never mind, forget that.", "memory"),
        ("forget what I just said", "memory"),
        # neighbours keep theirs
        ("forget that i like jazz", "memory"),
        ("forget that name", "music_alias"),
        ("forget me", "voice_profile"),
        ("never mind", "dismiss"),
        ("ignore that", "dismiss"),
        ("cancel that", "dismiss"),
    ],
)
def test_bare_forget_routes_to_memory(said: str, winner: str) -> None:
    hit = first_fast_path(strip_leading_filler(normalize_transcript(said)))
    assert hit is not None and hit[0].name == winner, (said, hit and hit[0].name)


def test_the_explicit_forget_still_takes_its_body() -> None:
    assert _FORGET_MEMORY_RE.match("forget that i like jazz").group("body") == "i like jazz"


# ─── Through the router ───────────────────────────────────────────────────


class _Talk:
    """One room's conversation through route(), one session throughout."""

    def __init__(self, db) -> None:
        self.db = db
        self.session_id = None

    async def __call__(self, said: str, **ctx_kw):
        ctx = Context(room_id="kitchen", session_id=self.session_id, online=True, **ctx_kw)
        resp = await route(Intent(transcript=said, room_id="kitchen"), ctx, self.db)
        await self.db.commit()
        self.session_id = resp.session_id
        return resp

    async def context(self) -> dict:
        return await SessionRepository(self.db).get_context(self.session_id) or {}


async def _bodies(db, person_id: int) -> list[str]:
    return [b for _i, b, _t, _s in await MemoriesRepository(db).list_active(person_id)]


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forget",
    ["forget that", "Forget it.", "forget what I just said", "Never mind, forget that."],
)
async def test_forget_right_after_a_save_forgets_exactly_it(db_session, forget) -> None:
    alex = await PeopleRepository(db_session).create(name="Alex")
    await MemoriesRepository(db_session).add(person_id=alex, body="my dog is named Max", source="explicit")
    talk = _Talk(db_session)

    saved = await talk("remember that I like jazz", person_id=alex)
    assert saved.matched_handler == "memory"
    # The short "forget that" is usually too short for voice ID.
    r = await talk(forget, voice_outcome="too_short")

    assert r.matched_handler == "memory"
    assert r.text == "OK, forgotten: i like jazz."  # the router lower-cases a turn
    assert r.text not in _ACKS
    assert await _bodies(db_session, alex) == ["my dog is named Max"]
    assert (await talk.context()).get(LAST_SAVED_KEY) is None


@requires_db
@pytest.mark.asyncio
async def test_a_second_forget_that_asks_instead_of_guessing(db_session) -> None:
    alex = await PeopleRepository(db_session).create(name="Alex")
    talk = _Talk(db_session)
    await talk("remember that I like jazz", person_id=alex)
    await talk("remember that my dog is named Max", person_id=alex)
    assert (await talk("forget that", person_id=alex)).text == "OK, forgotten: my dog is named max."
    # Only the LAST save: the one before it is not "that".
    r = await talk("forget that", person_id=alex)
    assert r.text == _FORGET_WHAT_TEXT
    assert await _bodies(db_session, alex) == ["i like jazz"]


@requires_db
@pytest.mark.asyncio
async def test_no_recent_memory_asks_forget_what(db_session) -> None:
    alex = await PeopleRepository(db_session).create(name="Alex")
    talk = _Talk(db_session)
    r = await talk("forget that", person_id=alex)
    assert r.matched_handler == "memory"
    assert r.text == _FORGET_WHAT_TEXT
    assert r.text not in _ACKS
    assert r.expect_followup is True
    pending = (await talk.context()).get(PENDING_CONFIRMATION_KEY)
    assert pending and pending["kind"] == "core.forget_what"


@requires_db
@pytest.mark.asyncio
async def test_a_turn_in_between_means_nothing_is_forgotten(db_session) -> None:
    alex = await PeopleRepository(db_session).create(name="Alex")
    talk = _Talk(db_session)
    await talk("remember that I like jazz", person_id=alex)
    assert (await talk("what time is it", person_id=alex)).matched_handler == "clock"
    r = await talk("forget that", person_id=alex)
    assert r.text == _FORGET_WHAT_TEXT
    assert await _bodies(db_session, alex) == ["i like jazz"]


@requires_db
@pytest.mark.asyncio
async def test_forget_it_with_nothing_to_forget_is_still_a_brush_off(db_session) -> None:
    alex = await PeopleRepository(db_session).create(name="Alex")
    await MemoriesRepository(db_session).add(person_id=alex, body="I like jazz", source="explicit")
    talk = _Talk(db_session)
    for said in ("forget it", "forget about it", "never mind, forget that"):
        r = await talk(said, person_id=alex)
        assert r.text in _ACKS, said
        assert r.expect_followup is False
    assert await _bodies(db_session, alex) == ["I like jazz"]


@requires_db
@pytest.mark.asyncio
async def test_forget_what_reply_names_the_fact(db_session) -> None:
    alex = await PeopleRepository(db_session).create(name="Alex")
    repo = MemoriesRepository(db_session)
    await repo.add(person_id=alex, body="I like jazz", source="explicit")
    await repo.add(person_id=alex, body="my dog is named Max", source="explicit")
    talk = _Talk(db_session)
    assert (await talk("forget that", person_id=alex)).text == _FORGET_WHAT_TEXT
    # The reply is too short to tell who: the person who was asked answers.
    r = await talk("that I like jazz", voice_outcome="too_short")
    assert r.matched_handler == "memory"
    assert r.text.startswith("OK, forgotten: I like jazz")
    assert await _bodies(db_session, alex) == ["my dog is named Max"]


@requires_db
@pytest.mark.asyncio
async def test_forget_what_then_something_else_routes_normally(db_session) -> None:
    alex = await PeopleRepository(db_session).create(name="Alex")
    await MemoriesRepository(db_session).add(person_id=alex, body="I like jazz", source="explicit")
    talk = _Talk(db_session)
    await talk("forget that", person_id=alex)
    r = await talk("what time is it", person_id=alex)
    assert r.matched_handler == "clock"
    assert await _bodies(db_session, alex) == ["I like jazz"]
    r = await talk("forget that")
    assert r.text == _FORGET_WHAT_TEXT
    assert (await talk("never mind")).text in _ACKS


@requires_db
@pytest.mark.asyncio
async def test_a_pending_question_is_dropped_not_forgotten(db_session) -> None:
    """A question the previous turn asked: "forget it" / "forget that"
    drops it (a later "yes" can't act on it) and deletes nothing."""
    alex = await PeopleRepository(db_session).create(name="Alex")
    await MemoriesRepository(db_session).add(person_id=alex, body="I like jazz", source="explicit")
    talk = _Talk(db_session)
    for said in ("forget it", "forget that"):
        await talk("what time is it", person_id=alex)  # opens the session
        sessions = SessionRepository(db_session)
        await request_confirmation(
            db_session, talk.session_id, kind="core.self_doubt_offer", handler="double_check",
            data={"question": "who won", "candidate_claim": "x", "category": "general_recent"},
        )
        await sessions.record_exchange(
            talk.session_id, "who won", "Probably them. Want me to check that online?", 20)
        await db_session.commit()
        r = await talk(said, person_id=alex)
        assert r.matched_handler == "memory"
        assert r.text in _ACKS, said
        assert (await talk.context()).get(PENDING_CONFIRMATION_KEY) is None
    assert await _bodies(db_session, alex) == ["I like jazz"]


@requires_db
@pytest.mark.asyncio
async def test_forget_that_after_a_memory_offer_turns_it_down(db_session) -> None:
    alex = await PeopleRepository(db_session).create(name="Alex")
    repo = MemoriesRepository(db_session)
    mid = await repo.add(person_id=alex, body="your cat is called Pip", source="implicit", status="pending")
    talk = _Talk(db_session)
    await talk("what time is it", person_id=alex)
    await request_confirmation(
        db_session, talk.session_id, kind="core.pending_memory_offer", handler="memory",
        data={"memory_id": mid, "person_id": alex},
    )
    await SessionRepository(db_session).record_exchange(
        talk.session_id, "my cat pip is asleep",
        "Cats sleep a lot. By the way, your cat is called Pip. Should I remember that?", 20)
    await db_session.commit()
    r = await talk("forget that", voice_outcome="too_short")
    assert r.text == "OK, I won't save that."
    status = (await db_session.execute(
        text("SELECT status FROM memories WHERE id = :i"), {"i": mid})).scalar_one()
    assert status == "rejected"


@requires_db
@pytest.mark.asyncio
async def test_yes_to_a_memory_offer_then_forget_that(db_session) -> None:
    """The extractor's fact, saved by a "yes": the next "forget that" takes
    it back (the background path is traceable through the offer)."""
    alex = await PeopleRepository(db_session).create(name="Alex")
    mid = await MemoriesRepository(db_session).add(
        person_id=alex, body="your cat is called Pip", source="implicit", status="pending")
    talk = _Talk(db_session)
    await talk("what time is it", person_id=alex)
    await request_confirmation(
        db_session, talk.session_id, kind="core.pending_memory_offer", handler="memory",
        data={"memory_id": mid, "person_id": alex},
    )
    await db_session.commit()
    assert (await talk("yes", voice_outcome="too_short")).text == "Got it — I'll remember that."
    r = await talk("forget that", voice_outcome="too_short")
    assert r.text == "OK, forgotten: your cat is called Pip."
    assert await _bodies(db_session, alex) == []


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "who",
    [
        {"person_id": "sam"},              # a recognised other person
        {"voice_outcome": "no_match"},     # scored, and matched nobody
        {"voice_outcome": "ambiguous"},    # too close to call between people
    ],
)
async def test_another_persons_memory_is_never_touched(db_session, who) -> None:
    people = PeopleRepository(db_session)
    alex = await people.create(name="Alex")
    sam = await people.create(name="Sam")
    if who.get("person_id") == "sam":
        who = {"person_id": sam}
    talk = _Talk(db_session)
    await talk("remember that I like jazz", person_id=alex)
    r = await talk("forget that", **who)
    assert r.matched_handler == "memory"
    assert "only forget that for the person" in r.text
    assert await _bodies(db_session, alex) == ["i like jazz"]


@requires_db
@pytest.mark.asyncio
async def test_forget_that_fact_is_unchanged(db_session) -> None:
    alex = await PeopleRepository(db_session).create(name="Alex")
    talk = _Talk(db_session)
    await talk("remember that I like jazz", person_id=alex)
    await talk("remember that my dog is named Max", person_id=alex)
    r = await talk("forget that I like jazz", person_id=alex)
    assert r.text == "OK, forgotten: i like jazz"
    assert await _bodies(db_session, alex) == ["my dog is named max"]
