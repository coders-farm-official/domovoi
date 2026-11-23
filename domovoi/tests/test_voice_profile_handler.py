from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import text

from domovoi.clients.voice_embedder import EMBEDDING_DIM, StubVoiceEmbedder
from domovoi.db.repositories import (
    PeopleRepository,
    SatellitesRepository,
    SessionRepository,
    VoiceProfilesRepository,
)
from domovoi.handlers.voice_profile import (
    VoiceProfileHandler,
    _DENYLIST_RE,
    _FORGET_RE,
    _SELF_INTRO_RE,
    _THIRD_PARTY_INTRO_RE,
    _WHEREAMI_RE,
    _WHOAMI_RE,
    _clean_name,
)
from domovoi.models import Context
from domovoi.router import plan_route
from domovoi.tests.conftest import requires_db


# ─── Regex tests ────────────────────────────────────────────────────────────


def test_self_intro_regex_simple() -> None:
    for s in ("i'm sarah", "i am sarah", "my name is sarah", "call me sarah", "this is sarah"):
        m = _SELF_INTRO_RE.match(s)
        assert m, f"expected match: {s!r}"
        # All four alternatives capture into different group names.
        name = m.groupdict().get("name") or m.groupdict().get("name2") or m.groupdict().get("name3") or m.groupdict().get("name4")
        assert name == "sarah"


def test_self_intro_regex_multiword_name() -> None:
    m = _SELF_INTRO_RE.match("my name is mary jane")
    assert m and m.groupdict().get("name2") == "mary jane"


def test_self_intro_regex_does_not_match_non_names() -> None:
    """The regex captures, but `_clean_name` rejects common adjectives.
    The regex itself can match "i'm hungry" — the handler's blocklist
    is what stops false enrollments."""
    m = _SELF_INTRO_RE.match("i'm hungry")
    assert m is not None  # regex still matches
    assert _clean_name(m.group("name")) is None  # but cleaning rejects


def test_third_party_regex() -> None:
    # "domovoi, this is alex" / "this is my friend alex"
    for s in (
        "domovoi, this is alex",
        "hey domovoi, meet alex",
        "this is alex",
        "this is alex, my friend",
        "meet alex",
    ):
        m = _THIRD_PARTY_INTRO_RE.match(s)
        assert m, f"expected match: {s!r}"
        assert m.group("name") == "alex"


def test_whoami_regex() -> None:
    for s in (
        "who am i",
        "who am i talking to",
        "do you know me",
        "do you recognize me",
        "do you know who i am",
    ):
        assert _WHOAMI_RE.match(s), f"expected match: {s!r}"


# ─── "Who am I" / "where am I", however it's said (live 1.3.0, 2026-11-16) ──
# Each went to Q&A on the basement satellite and got a nonsense answer.
# Raw transcripts as Whisper writes them: routed through the router's own
# normalization (plan_route), so case, commas and the closing "?" count.

_WHO_AM_I_SAID = (
    "Who am I?",
    "Who am I right now?",
    "Who am I now?",
    "Who am I today?",
    "Who am I, Domo boy?",
    "Who am I, Doma boy?",
    "Who am I, Domovoi?",
    "Who am I Domovoi",
    "Who am I? Domovoi.",
    "Who am I right now, Domovoi?",
    "Domovoi, who am I?",
    "Hey Domovoi, who am I right now?",
    "Hey Domo, who am I?",
    "Domo boy, who am I?",
    "What is my name?",
    "What's my name?",
    "What's my name, Domovoi?",
    "What is my name again?",
    "Do you know my name?",
    "Do you know who I am?",
    "Do you know me?",
    "Who's talking?",
    "Who is speaking?",
    "Who is talking to you?",
    "Who am I talking to?",
)

_WHERE_AM_I_SAID = (
    "Where am I?",
    "Where am I right now?",
    "Where am I, Domovoi?",
    "Where am I, Domo boy?",
    "Domovoi, where am I?",
    "Which room am I in?",
    "What room am I in?",
    "What room is this?",
    "Which room is this, Domovoi?",
)


@pytest.mark.parametrize("said", _WHO_AM_I_SAID)
def test_who_am_i_said_any_way_routes_to_whoami(said: str) -> None:
    plan = plan_route(said)
    assert plan is not None and plan.handler.name == "voice_profile", said
    assert plan.fast_path.pattern is _WHOAMI_RE, said


@pytest.mark.parametrize("said", _WHERE_AM_I_SAID)
def test_where_am_i_said_any_way_routes_to_whereami(said: str) -> None:
    plan = plan_route(said)
    assert plan is not None and plan.handler.name == "voice_profile", said
    assert plan.fast_path.pattern is _WHEREAMI_RE, said


@pytest.mark.parametrize("said", (
    "Who am I to judge?",
    "Who am I kidding?",
    "Who am I supposed to call?",
    "Where am I going to put this?",
    "Where am I supposed to park?",
    "What is my name in Spanish?",
    "Do you know my name means wolf?",
    "Who's talking about the game?",
    "What room is this song from?",
))
def test_idioms_and_longer_questions_are_not_whoami(said: str) -> None:
    """Anchored at both ends: only the qualifiers and addresses ride along."""
    plan = plan_route(said)
    assert plan is None or plan.fast_path.pattern not in (_WHOAMI_RE, _WHEREAMI_RE), said


@pytest.mark.parametrize("said", ("My name is Sarah.", "My name's Sarah.", "I'm Sarah."))
def test_introductions_stay_introductions(said: str) -> None:
    plan = plan_route(said)
    assert plan is not None and plan.handler.name == "voice_profile"
    assert plan.fast_path.pattern is _SELF_INTRO_RE


class _Rows:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _LabelSession:
    """The one read ``_whereami`` makes: the room's label (or none)."""

    def __init__(self, label=None, *, fail: bool = False):
        self.label, self.fail, self.asked = label, fail, []

    async def execute(self, stmt, params=None):
        self.asked.append(params)
        if self.fail:
            raise RuntimeError("db down")
        return _Rows((self.label,) if self.label is not None else None)


@pytest.mark.asyncio
async def test_where_am_i_names_the_room_by_its_id() -> None:
    ctx = Context(session_id=None, room_id="basement", online=True)
    session = _LabelSession()
    r = await VoiceProfileHandler()._whereami_from_match(None, ctx, session)
    assert r.text == "You're in the basement."
    assert r.matched_handler == "voice_profile"
    assert session.asked == [{"r": "basement"}]


@pytest.mark.asyncio
async def test_where_am_i_uses_the_rooms_label() -> None:
    ctx = Context(session_id=None, room_id="sat_3", online=True)
    r = await VoiceProfileHandler()._whereami_from_match(None, ctx, _LabelSession("Living Room"))
    assert r.text == "You're in the living room."
    r = await VoiceProfileHandler()._whereami_from_match(None, ctx, _LabelSession("Alex's Office"))
    assert r.text == "You're in Alex's Office."


@pytest.mark.asyncio
async def test_where_am_i_without_the_label_still_names_the_room() -> None:
    ctx = Context(session_id=None, room_id="kids_room", online=True)
    r = await VoiceProfileHandler()._whereami_from_match(None, ctx, _LabelSession(fail=True))
    assert r.text == "You're in the kids room."


@pytest.mark.asyncio
async def test_where_am_i_from_a_phone_says_it_cant_tell() -> None:
    session = _LabelSession("Basement")
    for ctx in (
        Context(session_id=None, room_id="phone-abc", online=True, device_kind="phone"),
        Context(session_id=None, room_id="phone-abc", online=True),
    ):
        r = await VoiceProfileHandler()._whereami_from_match(None, ctx, session)
        assert r.text == (
            "You're talking to me from your phone, so I can't tell which room you're in."
        )
    assert session.asked == []


@pytest.mark.asyncio
async def test_where_am_i_with_no_room() -> None:
    r = await VoiceProfileHandler()._whereami_from_match(
        None, Context(session_id=None, room_id=None, online=True), _LabelSession("Basement"),
    )
    assert "can't tell which room you're in" in r.text


@requires_db
@pytest.mark.asyncio
async def test_where_am_i_reads_the_label_from_settings_rooms(db_session) -> None:
    await SatellitesRepository(db_session).set_room_label("whereami_test_room", "Basement")
    ctx = Context(session_id=None, room_id="whereami_test_room", online=True)
    r = await VoiceProfileHandler()._whereami_from_match(None, ctx, db_session)
    assert r.text == "You're in the basement."


def test_forget_regex() -> None:
    for s in (
        "forget me",
        "forget my voice",
        "don't save my voice",
        "don't remember my voice",
        "delete my voice",
        "delete my voice profile",
    ):
        assert _FORGET_RE.match(s), f"expected match: {s!r}"


def test_clean_name_blocklist() -> None:
    assert _clean_name("Sarah") == "Sarah"
    assert _clean_name("mary jane") == "Mary Jane"
    assert _clean_name("hungry") is None
    assert _clean_name("tired.") is None
    assert _clean_name("") is None


# ─── Behavior: self-intro confirmation flow (DB-backed) ────────────────────


@requires_db
@pytest.mark.asyncio
async def test_self_intro_parks_pending_confirmation(db_session) -> None:
    """An "I'm Sarah" with a real embedding must park a pending_confirmation
    in session context and respond with a yes/no question — without
    enrolling yet. The response also flags expect_followup so the Pi
    skips its wake-word gate for the user's yes/no answer."""
    sarah_pcm = (np.full(16_000, 1234, dtype=np.int16)).tobytes()
    embedding = await StubVoiceEmbedder().embed(sarah_pcm)
    assert embedding is not None

    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await db_session.commit()

    handler = VoiceProfileHandler()
    ctx = Context(
        session_id=sid,
        room_id="kitchen",
        online=True,
        embedding_bytes=embedding.astype(np.float32).tobytes(),
    )
    m = _SELF_INTRO_RE.match("i'm sarah")
    assert m
    response = await handler._self_intro_from_match(m, ctx, db_session)
    await db_session.commit()

    # A satellite's introduction asks for the spelling (2026-11-16): the
    # answer settles the name and is more of the voice.
    assert response.text == "Nice to meet you! How do you spell that?"
    assert response.expect_followup is True

    # Pending confirmation parked.
    ctx_data = await SessionRepository(db_session).get_context(sid)
    pending = ctx_data.get("pending_confirmation")
    assert pending is not None
    assert pending["handler"] == "voice_profile"
    assert pending["kind"] == "core.self_intro"
    assert pending["name"] == "Sarah"
    assert pending["stage"] == "spell"
    assert "embedding_b64" in pending
    assert len(pending["clips"]) == 1

    # No people row created yet.
    rows = (await db_session.execute(text("SELECT count(*) FROM people"))).scalar_one()
    assert rows == 0


@requires_db
@pytest.mark.asyncio
async def test_handle_confirmation_affirmative_enrolls(db_session) -> None:
    """Calling handle_confirmation with affirmative=True should INSERT
    into people + voice_profiles using the parked embedding."""
    import base64

    sarah_pcm = (np.full(16_000, 1234, dtype=np.int16)).tobytes()
    embedding = await StubVoiceEmbedder().embed(sarah_pcm)
    assert embedding is not None
    embedding_b64 = base64.b64encode(embedding.astype(np.float32).tobytes()).decode("ascii")

    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await db_session.commit()

    handler = VoiceProfileHandler()
    ctx = Context(session_id=sid, room_id="kitchen", online=True)
    response = await handler.handle_confirmation(
        kind="core.self_intro",
        data={"name": "Sarah", "embedding_b64": embedding_b64},
        affirmative=True,
        ctx=ctx,
        session=db_session,
    )
    await db_session.commit()

    assert "sarah" in response.text.lower()

    people_rows = (
        await db_session.execute(text("SELECT id, name FROM people"))
    ).all()
    assert len(people_rows) == 1
    assert people_rows[0][1] == "Sarah"

    profile_rows = (
        await db_session.execute(
            text("SELECT person_id, length(embedding) FROM voice_profiles")
        )
    ).all()
    assert len(profile_rows) == 1
    assert profile_rows[0][0] == people_rows[0][0]
    # EMBEDDING_DIM floats × 4 bytes
    assert profile_rows[0][1] == EMBEDDING_DIM * 4


@requires_db
@pytest.mark.asyncio
async def test_handle_confirmation_negative_skips_enrollment(db_session) -> None:
    handler = VoiceProfileHandler()
    ctx = Context(
        session_id=None,
        room_id="kitchen",
        online=True,
    )
    response = await handler.handle_confirmation(
        kind="core.self_intro",
        data={"name": "Sarah", "embedding_b64": ""},
        affirmative=False,
        ctx=ctx,
        session=db_session,
    )
    assert "won't save" in response.text.lower()

    rows = (await db_session.execute(text("SELECT count(*) FROM people"))).scalar_one()
    assert rows == 0


# ─── Behavior: who-am-I + forget-me ────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_whoami_unknown(db_session) -> None:
    handler = VoiceProfileHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True, person_id=None)
    response = await handler._whoami(ctx, db_session)
    assert "don't recognize" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_whoami_unknown_after_the_model_change_says_how_to_be_known_again(db_session) -> None:
    """1.3.0's new speaker model can't use the old samples (V061): while
    someone still has to introduce themselves again, an unknown voice is
    told why it may not be recognized, never that it is."""
    handler = VoiceProfileHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True, person_id=None)
    pid = await PeopleRepository(db_session).create(name="Sarah")
    await db_session.execute(
        text("UPDATE people SET voice_relearn_since = NOW() WHERE id = :id"), {"id": pid}
    )
    await db_session.commit()
    text_ = (await handler._whoami(ctx, db_session)).text
    assert text_.startswith("I don't recognize your voice yet.")
    assert "upgraded" in text_ and '"my name is"' in text_
    # Once they have, the plain answer again.
    await VoiceProfilesRepository(db_session).add(
        person_id=pid, embedding=b"\0" * (EMBEDDING_DIM * 4),
        model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG, room_id="kitchen", sample_seconds=1.5,
    )
    await db_session.commit()
    assert (await handler._whoami(ctx, db_session)).text == "I don't recognize your voice yet."


@pytest.mark.asyncio
async def test_without_the_speaker_model_voice_replies_say_so(monkeypatch) -> None:
    """No speaker model on the server: "my name is …" and "who am I" say
    voices can't be recognized right now, not "try again in a moment" (it
    wouldn't help) and never a name."""
    from domovoi.handlers import voice_profile as vp

    monkeypatch.setattr(vp, "voice_id_unavailable", lambda: True)
    handler = VoiceProfileHandler()
    ctx = Context(session_id="11111111-1111-1111-1111-111111111111", room_id="kitchen",
                  online=True, person_id=None)
    hello = await handler._start_self_intro("Sarah", ctx, None)
    assert hello.text.startswith("Hi Sarah, nice to meet you.")
    assert "can't recognize voices right now" in hello.text
    who = await handler._whoami(Context(session_id=None, room_id="kitchen", online=True), None)
    assert "can't recognize voices right now" in who.text


@requires_db
@pytest.mark.asyncio
async def test_whoami_known(db_session) -> None:
    sarah_id = await PeopleRepository(db_session).create(name="Sarah")
    await db_session.commit()

    handler = VoiceProfileHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True, person_id=sarah_id)
    response = await handler._whoami(ctx, db_session)
    assert "sarah" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_forget_me_when_known(db_session) -> None:
    """Deleting a person cascades to voice_profiles via FK; intents_log
    rows pointing to them lapse to NULL via the FK's ON DELETE SET NULL."""
    sarah_pcm = (np.full(16_000, 1234, dtype=np.int16)).tobytes()
    embedding = await StubVoiceEmbedder().embed(sarah_pcm)
    assert embedding is not None

    sarah_id = await PeopleRepository(db_session).create(name="Sarah")
    await VoiceProfilesRepository(db_session).add(
        person_id=sarah_id,
        embedding=embedding.astype(np.float32).tobytes(),
        model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
        room_id="kitchen",
        sample_seconds=1.0,
    )
    await db_session.commit()

    handler = VoiceProfileHandler()
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await db_session.commit()
    ctx = Context(
        session_id=sid,
        room_id="kitchen",
        online=True,
        person_id=sarah_id,
    )
    # Asked first (review 2026-10-17): nothing goes before the yes.
    asked = await handler._forget(ctx, db_session)
    await db_session.commit()
    assert asked.text.endswith("Say yes to delete everything you've said.")
    assert (await db_session.execute(text("SELECT count(*) FROM people"))).scalar_one() == 1
    pending = (await SessionRepository(db_session).get_context(sid))["pending_confirmation"]
    assert pending["kind"] == "core.forget_me"
    response = await handler.handle_choice_reply(pending["kind"], pending, "yes", ctx, db_session)
    await db_session.commit()

    assert "forgotten" in response.text.lower()
    assert "sarah" in response.text.lower()

    people_count = (
        await db_session.execute(text("SELECT count(*) FROM people"))
    ).scalar_one()
    profile_count = (
        await db_session.execute(text("SELECT count(*) FROM voice_profiles"))
    ).scalar_one()
    assert people_count == 0
    assert profile_count == 0  # cascade


@requires_db
@pytest.mark.asyncio
async def test_forget_me_when_unknown(db_session) -> None:
    handler = VoiceProfileHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True, person_id=None)
    response = await handler._forget(ctx, db_session)
    assert "don't have" in response.text.lower()


# ─── Regression: F-V005 ────────────────────────────────────────────────────
#
# "forget me" deleted the `people` row but left ctx.person_id set, so the
# router's `_persist_turn` then inserted an intents_log row referencing the
# just-deleted person. The FK blew up, the shared session_scope rolled back,
# and the profile came back — the user got an error frame instead of "Okay,
# I've forgotten you". DB-free on purpose: the repository is stubbed so this
# runs (and fails, pre-fix) with no Postgres.


class _StubPeopleRepo:
    """Minimal stand-in for PeopleRepository: records the delete and can be
    told to blow up the way a real DB error would."""

    deleted: list[int] = []
    announced: list = []
    fail_delete = False

    def __init__(self, session) -> None:
        self.session = session

    async def get(self, person_id: int):
        return (person_id, "Guy", None)

    async def delete(self, person_id: int) -> int:
        if type(self).fail_delete:
            raise RuntimeError("simulated DB failure")
        type(self).deleted.append(person_id)
        return 1


@pytest.fixture
def stub_people_repo(monkeypatch):
    _StubPeopleRepo.deleted = []
    _StubPeopleRepo.fail_delete = False
    monkeypatch.setattr(
        "domovoi.handlers.voice_profile.PeopleRepository", _StubPeopleRepo
    )

    async def _whole_person(self, ctx, session):
        # The voice is the person's own (V029's check has its own tests,
        # test_voice_claims.py): the delete path is what these are about.
        return "person", []

    monkeypatch.setattr(VoiceProfileHandler, "_forget_scope", _whole_person)

    # What they said goes first (voice history, 2026-10-17); its SQL has
    # its own DB-backed tests (test_voice_history.py).
    async def _history(session, person_id):
        return {"conversation_log": 0, "intents_log": 0, "sessions_cleared": 0}

    monkeypatch.setattr("domovoi.voice_history.delete_person_history", _history)

    # Plugins hear about it after the turn commits (SDK 1.17); the
    # listener on a real session has its own tests
    # (test_voice_history_forget_event.py).
    _StubPeopleRepo.announced = []
    monkeypatch.setattr("domovoi.voice_history.announce_after_commit",
                        lambda session: _StubPeopleRepo.announced.append(session))
    return _StubPeopleRepo


@pytest.mark.asyncio
async def test_forget_me_clears_identity_from_context(stub_people_repo) -> None:
    """After a successful delete the Context must no longer name the person,
    or the audit write later in the same transaction violates the FK."""
    handler = VoiceProfileHandler()
    ctx = Context(
        session_id=None,
        room_id="kitchen",
        online=True,
        person_id=2,
        presence_tier="low",
    )

    response = await handler._forget_person(2, "Guy", ctx, object())

    assert "forgotten" in response.text.lower()
    assert "guy" in response.text.lower()
    assert stub_people_repo.deleted == [2]
    assert len(stub_people_repo.announced) == 1
    # The load-bearing assertions: nothing written after this point may
    # reference the deleted person.
    assert ctx.person_id is None
    assert ctx.presence_tier == "high"


@pytest.mark.asyncio
async def test_forget_me_keeps_identity_when_delete_fails(stub_people_repo) -> None:
    """The mirror case: the profile is still there, so the speaker is still
    that person and the turn's audit rows should still say so."""
    stub_people_repo.fail_delete = True
    handler = VoiceProfileHandler()
    ctx = Context(
        session_id=None,
        room_id="kitchen",
        online=True,
        person_id=2,
        presence_tier="low",
    )

    response = await handler._forget_person(2, "Guy", ctx, object())

    assert "still there" in response.text.lower()
    assert stub_people_repo.announced == []
    assert ctx.person_id == 2
    assert ctx.presence_tier == "low"


# ─── Edge cases ────────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_self_intro_without_embedding_acknowledges_only(db_session) -> None:
    """Sub-second clip / embedder unavailable → no embedding in ctx →
    handler acknowledges but doesn't park pending or enroll."""
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await db_session.commit()

    handler = VoiceProfileHandler()
    ctx = Context(
        session_id=sid,
        room_id="kitchen",
        online=True,
        embedding_bytes=None,
    )
    response = await handler._start_self_intro("Sarah", ctx, db_session)
    await db_session.commit()

    assert "sarah" in response.text.lower()
    ctx_data = await SessionRepository(db_session).get_context(sid)
    assert ctx_data.get("pending_confirmation") is None


# ─── Denylist ──────────────────────────────────────────────


def test_denylist_regex_canonical_phrasings() -> None:
    for s in (
        "never save my voice",
        "don't ever save my voice",
        "stop saving my voice",
        "block my voice",
        "block my voice profile",
        "never remember me",
        "never recognize me",
        "never save me",
        "opt me out of voice profiles",
        "opt me out of voice profile",
        "opt me out of voice recognition",
    ):
        assert _DENYLIST_RE.match(s), f"expected match: {s!r}"


def test_denylist_regex_does_not_swallow_unrelated_phrasings() -> None:
    """Anchored against false positives — these must NOT match."""
    for s in (
        "save my voice",         # no "never" / "don't ever"
        "save the world",
        "never save the world",
        "block the door",
        "never recognize that song",
    ):
        assert not _DENYLIST_RE.match(s), f"unexpected match: {s!r}"


def test_denylist_regex_distinct_from_forget_regex() -> None:
    """Forget vs denylist — adjacent phrasings but different intents.
    Forget DELETES; denylist ADDS to suppression list. The two regexes
    must not poach each other's territory."""
    # Forget territory:
    assert _FORGET_RE.match("forget me")
    assert _FORGET_RE.match("don't save my voice")  # one-shot, not denylist
    assert not _DENYLIST_RE.match("forget me")
    assert not _DENYLIST_RE.match("don't save my voice")
    # Denylist territory:
    assert _DENYLIST_RE.match("never save my voice")
    assert not _FORGET_RE.match("never save my voice")


@requires_db
@pytest.mark.asyncio
async def test_denylist_action_writes_row(db_session) -> None:
    """Saying "never save my voice" with a present embedding writes a
    voice_denylist row using ctx.embedding_bytes."""
    embedding = await StubVoiceEmbedder().embed(
        (np.full(16_000, 4242, dtype=np.int16)).tobytes()
    )
    assert embedding is not None
    embedding_bytes = embedding.astype(np.float32).tobytes()

    handler = VoiceProfileHandler()
    ctx = Context(
        session_id=None,
        room_id="kitchen",
        online=True,
        embedding_bytes=embedding_bytes,
    )
    response = await handler._denylist(ctx, db_session)
    await db_session.commit()

    assert "won't" in response.text.lower() or "save" in response.text.lower()
    rows = (
        await db_session.execute(
            text("SELECT count(*), embedding, model FROM voice_denylist GROUP BY embedding, model")
        )
    ).all()
    assert len(rows) == 1
    assert rows[0][0] == 1
    # The exact bytes were stored.
    assert bytes(rows[0][1]) == embedding_bytes
    # Notes captured the room.
    note_row = (
        await db_session.execute(text("SELECT notes FROM voice_denylist"))
    ).first()
    assert note_row is not None
    assert "kitchen" in (note_row[0] or "")


@requires_db
@pytest.mark.asyncio
async def test_denylist_action_without_embedding_responds_gracefully(db_session) -> None:
    """No embedding → don't write a useless zero-vector row; ask the
    user to retry."""
    handler = VoiceProfileHandler()
    ctx = Context(
        session_id=None,
        room_id="kitchen",
        online=True,
        embedding_bytes=None,
    )
    response = await handler._denylist(ctx, db_session)
    await db_session.commit()

    # Friendly retry message; "couldn't" / "say it again" type phrasing.
    assert "again" in response.text.lower() or "couldn't" in response.text.lower()
    rows = (
        await db_session.execute(text("SELECT count(*) FROM voice_denylist"))
    ).scalar_one()
    assert rows == 0  # no useless write


@requires_db
@pytest.mark.asyncio
async def test_enroll_refuses_denylisted_voice(db_session) -> None:
    """The denylist must gate EVERY enrollment path, not just
    identification. The third-party-introduction confirm hands `_enroll`
    a buffered cluster centroid that never passed through the
    identifier's denylist pre-check, so without an enrollment-side gate
    an introducer saying "this is Alex" + "yes" could enroll a voice
    whose owner explicitly said "never save my voice". An embedding that
    cosine-matches a denylist row is refused — no people row, no
    voice_profiles row — with a neutral response that doesn't disclose
    the opt-out."""
    from domovoi.db.repositories import VoiceDenylistRepository

    denied = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    denied[0] = 1.0  # unit vector — cosine 1.0 against itself
    await VoiceDenylistRepository(db_session).add(
        embedding=denied.tobytes(),
        model=VoiceDenylistRepository.EMBEDDING_MODEL_TAG,
        notes="test opt-out",
    )
    await db_session.commit()

    handler = VoiceProfileHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    response = await handler._enroll("alex", denied, ctx, db_session)
    await db_session.commit()

    assert "wasn't able" in response.text.lower()
    assert (
        await db_session.execute(text("SELECT count(*) FROM people"))
    ).scalar_one() == 0
    assert (
        await db_session.execute(text("SELECT count(*) FROM voice_profiles"))
    ).scalar_one() == 0


@requires_db
@pytest.mark.asyncio
async def test_enroll_allows_non_denylisted_voice(db_session) -> None:
    """The enrollment denylist gate only blocks matches: an embedding
    orthogonal to every denylist row (cosine 0.0, far below threshold)
    still enrolls normally."""
    from domovoi.db.repositories import VoiceDenylistRepository

    denied = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    denied[0] = 1.0
    await VoiceDenylistRepository(db_session).add(
        embedding=denied.tobytes(),
        model=VoiceDenylistRepository.EMBEDDING_MODEL_TAG,
        notes="test opt-out",
    )
    await db_session.commit()

    other = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    other[1] = 1.0  # orthogonal to `denied`

    handler = VoiceProfileHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    response = await handler._enroll("sarah", other, ctx, db_session)
    await db_session.commit()

    assert "sarah" in response.text.lower()
    assert (
        await db_session.execute(text("SELECT count(*) FROM people"))
    ).scalar_one() == 1
    assert (
        await db_session.execute(text("SELECT count(*) FROM voice_profiles"))
    ).scalar_one() == 1
