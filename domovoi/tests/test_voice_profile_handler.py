from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import text

from domovoi.clients.voice_embedder import EMBEDDING_DIM, StubVoiceEmbedder
from domovoi.db.repositories import (
    PeopleRepository,
    SessionRepository,
    VoiceProfilesRepository,
)
from domovoi.handlers.voice_profile import (
    VoiceProfileHandler,
    _DENYLIST_RE,
    _FORGET_RE,
    _SELF_INTRO_RE,
    _THIRD_PARTY_INTRO_RE,
    _WHOAMI_RE,
    _clean_name,
)
from domovoi.models import Context
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

    assert "sarah" in response.text.lower()
    assert "right" in response.text.lower()  # asks confirmation
    assert response.expect_followup is True

    # Pending confirmation parked.
    ctx_data = await SessionRepository(db_session).get_context(sid)
    pending = ctx_data.get("pending_confirmation")
    assert pending is not None
    assert pending["handler"] == "voice_profile"
    assert pending["kind"] == "core.self_intro"
    assert pending["name"] == "Sarah"
    assert "embedding_b64" in pending

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
    # 256 floats × 4 bytes
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
    ctx = Context(
        session_id=None,
        room_id="kitchen",
        online=True,
        person_id=sarah_id,
    )
    response = await handler._forget(ctx, db_session)
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
