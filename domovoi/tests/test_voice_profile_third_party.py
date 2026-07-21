"""Tests for the hybrid third-party introduction flow.

Covers the four failure modes the user raised plus the happy path:
  * Silent guest        — no enroll, expectation expires (test_ttl_expiry)
  * Introducer not enrolled — falls back to v1 ack-only stub
  * Stranger walks in   — gets buffered into a *separate* cluster from Alex
                          (verified via cluster splitting test)
  * Time-of-flight      — TTL expires before any cluster materializes
  * Happy path          — intro → buffer → introducer returns → classifier
                          confident → ask appended → yes enrolls

Stub-embedder caveat: ``StubVoiceEmbedder`` derives its vector from a
SHA-256 of the PCM bytes, so two utterances "from the same speaker"
must reuse the *exact same bytes* to produce a similar embedding.
Different bytes → uncorrelated random vectors. Tests reflect this.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

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
    THIRD_PARTY_EXPECTATION_KEY,
    VoiceProfileHandler,
    _THIRD_PARTY_INTRO_RE,
    buffer_unknown_voice_turn,
    clear_third_party_expectation,
    load_third_party_expectation,
    maybe_inject_third_party_ask,
    save_third_party_expectation,
)
from domovoi.models import Context, Response
from domovoi.tests.conftest import requires_db
from domovoi.voice_clusterer import add_to_clusters


# ─── Clusterer unit tests ──────────────────────────────────────────────────


def _fake_embedding(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    return v / float(np.linalg.norm(v))


def test_clusterer_merges_same_speaker() -> None:
    e = _fake_embedding(42)
    clusters: list[dict] = []
    add_to_clusters(clusters, e, "hi there", 1.5)
    add_to_clusters(clusters, e, "thanks", 1.0)
    assert len(clusters) == 1
    assert len(clusters[0]["samples"]) == 2
    assert clusters[0]["total_audio_seconds"] == pytest.approx(2.5)


def test_clusterer_splits_different_speakers() -> None:
    alex = _fake_embedding(42)
    stranger = _fake_embedding(7)
    clusters: list[dict] = []
    add_to_clusters(clusters, alex, "nice to meet you", 1.5)
    add_to_clusters(clusters, stranger, "can someone open the door", 1.2)
    assert len(clusters) == 2


# ─── _third_party_from_match: parks expectation ───────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_third_party_intro_parks_expectation_when_introducer_known(
    db_session,
) -> None:
    introducer_id = await PeopleRepository(db_session).create(name="Kamron")
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await db_session.commit()

    handler = VoiceProfileHandler()
    ctx = Context(
        session_id=sid, room_id="kitchen", online=True, person_id=introducer_id
    )
    m = _THIRD_PARTY_INTRO_RE.match("domovoi, this is alex")
    assert m is not None
    response = await handler._third_party_from_match(m, ctx, db_session)
    await db_session.commit()

    assert "alex" in response.text.lower()
    expectation = await load_third_party_expectation(db_session, sid)
    assert expectation is not None
    assert expectation["introducer_person_id"] == introducer_id
    assert expectation["name"] == "Alex"
    assert expectation["candidate_clusters"] == []


@requires_db
@pytest.mark.asyncio
async def test_third_party_intro_acks_only_when_introducer_unknown(
    db_session,
) -> None:
    """No identified introducer means no anchor for the deferred ask —
    the handler falls back to the v1 acknowledge-only behavior."""
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await db_session.commit()

    handler = VoiceProfileHandler()
    ctx = Context(session_id=sid, room_id="kitchen", online=True, person_id=None)
    m = _THIRD_PARTY_INTRO_RE.match("domovoi, this is alex")
    assert m is not None
    response = await handler._third_party_from_match(m, ctx, db_session)
    await db_session.commit()

    assert "alex" in response.text.lower()
    expectation = await load_third_party_expectation(db_session, sid)
    assert expectation is None


# ─── TTL expiry ────────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_expectation_ttl_expiry_clears_on_read(db_session) -> None:
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    stale = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat()
    await save_third_party_expectation(
        db_session,
        sid,
        {
            "introducer_person_id": 1,
            "name": "Alex",
            "intro_transcript": "domovoi, this is alex",
            "started_at": stale,
            "ttl_sec": 600,
            "candidate_clusters": [],
        },
    )
    await db_session.commit()

    expectation = await load_third_party_expectation(db_session, sid)
    assert expectation is None  # expired → returned None and cleared
    ctx_data = await SessionRepository(db_session).get_context(sid)
    assert ctx_data.get(THIRD_PARTY_EXPECTATION_KEY) is None


# ─── buffer_unknown_voice_turn ────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_buffer_unknown_voice_turn_no_expectation_is_noop(db_session) -> None:
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await db_session.commit()

    pcm = np.full(16_000, 1234, dtype=np.int16).tobytes()
    embedding = await StubVoiceEmbedder().embed(pcm)
    assert embedding is not None

    await buffer_unknown_voice_turn(
        db_session,
        session_id=sid,
        transcript="hi everyone",
        embedding=embedding,
        audio_seconds=1.5,
    )
    await db_session.commit()

    ctx_data = await SessionRepository(db_session).get_context(sid)
    assert ctx_data.get(THIRD_PARTY_EXPECTATION_KEY) is None


@requires_db
@pytest.mark.asyncio
async def test_buffer_unknown_voice_turn_appends_to_expectation(db_session) -> None:
    introducer_id = await PeopleRepository(db_session).create(name="Kamron")
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await save_third_party_expectation(
        db_session,
        sid,
        {
            "introducer_person_id": introducer_id,
            "name": "Alex",
            "intro_transcript": "domovoi, this is alex",
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "ttl_sec": 600,
            "candidate_clusters": [],
        },
    )
    await db_session.commit()

    pcm = np.full(16_000, 1234, dtype=np.int16).tobytes()
    embedding = await StubVoiceEmbedder().embed(pcm)
    assert embedding is not None

    await buffer_unknown_voice_turn(
        db_session,
        session_id=sid,
        transcript="hi everyone",
        embedding=embedding,
        audio_seconds=1.5,
    )
    await db_session.commit()

    expectation = await load_third_party_expectation(db_session, sid)
    assert expectation is not None
    clusters = expectation["candidate_clusters"]
    assert len(clusters) == 1
    assert clusters[0]["samples"][0]["transcript"] == "hi everyone"


@requires_db
@pytest.mark.asyncio
async def test_buffer_two_distinct_unknown_voices_create_two_clusters(
    db_session,
) -> None:
    """Stranger walks in mid-window — should land in its own cluster
    rather than polluting Alex's."""
    introducer_id = await PeopleRepository(db_session).create(name="Kamron")
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await save_third_party_expectation(
        db_session,
        sid,
        {
            "introducer_person_id": introducer_id,
            "name": "Alex",
            "intro_transcript": "domovoi, this is alex",
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "ttl_sec": 600,
            "candidate_clusters": [],
        },
    )
    await db_session.commit()

    alex_pcm = np.full(16_000, 1234, dtype=np.int16).tobytes()
    stranger_pcm = np.full(16_000, 5678, dtype=np.int16).tobytes()
    embedder = StubVoiceEmbedder()
    alex_emb = await embedder.embed(alex_pcm)
    stranger_emb = await embedder.embed(stranger_pcm)
    assert alex_emb is not None and stranger_emb is not None

    await buffer_unknown_voice_turn(
        db_session, session_id=sid, transcript="hi", embedding=alex_emb,
        audio_seconds=1.5,
    )
    await buffer_unknown_voice_turn(
        db_session, session_id=sid, transcript="open the door please",
        embedding=stranger_emb, audio_seconds=1.5,
    )
    await db_session.commit()

    expectation = await load_third_party_expectation(db_session, sid)
    assert expectation is not None
    assert len(expectation["candidate_clusters"]) == 2


# ─── maybe_inject_third_party_ask ─────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_inject_ask_appends_when_classifier_confident(
    db_session, monkeypatch,
) -> None:
    """Classifier returns high confidence → response gets the appendix,
    expect_followup is set, and pending_confirmation is parked."""
    introducer_id = await PeopleRepository(db_session).create(name="Kamron")
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")

    pcm = np.full(16_000, 1234, dtype=np.int16).tobytes()
    embedding = await StubVoiceEmbedder().embed(pcm)
    assert embedding is not None

    await save_third_party_expectation(
        db_session,
        sid,
        {
            "introducer_person_id": introducer_id,
            "name": "Alex",
            "intro_transcript": "domovoi, this is alex",
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "ttl_sec": 600,
            "candidate_clusters": [],
        },
    )
    await buffer_unknown_voice_turn(
        db_session, session_id=sid, transcript="hi everyone, nice to meet you",
        embedding=embedding, audio_seconds=2.0,
    )
    await db_session.commit()

    async def fake_classify(*, name, intro_text, candidate_text):
        return 0.9

    monkeypatch.setattr(
        "domovoi.voice_classifier.classify_third_party_match",
        fake_classify,
    )

    ctx = Context(
        session_id=sid, room_id="kitchen", online=True, person_id=introducer_id
    )
    response = Response(text="Timer set for 5 minutes.", session_id=sid)
    injected = await maybe_inject_third_party_ask(
        db_session, ctx=ctx, response=response,
    )
    await db_session.commit()

    assert injected is True
    assert "alex" in response.text.lower()
    assert response.expect_followup is True

    ctx_data = await SessionRepository(db_session).get_context(sid)
    pending = ctx_data.get("pending_confirmation")
    assert pending is not None
    assert pending["handler"] == "voice_profile"
    assert pending["kind"] == "core.third_party_intro_confirm"
    assert pending["name"] == "Alex"
    assert "cluster_centroid_b64" in pending


@requires_db
@pytest.mark.asyncio
async def test_inject_ask_skips_when_classifier_unconfident(
    db_session, monkeypatch,
) -> None:
    introducer_id = await PeopleRepository(db_session).create(name="Kamron")
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")

    pcm = np.full(16_000, 1234, dtype=np.int16).tobytes()
    embedding = await StubVoiceEmbedder().embed(pcm)
    assert embedding is not None

    await save_third_party_expectation(
        db_session,
        sid,
        {
            "introducer_person_id": introducer_id,
            "name": "Alex",
            "intro_transcript": "domovoi, this is alex",
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "ttl_sec": 600,
            "candidate_clusters": [],
        },
    )
    await buffer_unknown_voice_turn(
        db_session, session_id=sid, transcript="open the door",
        embedding=embedding, audio_seconds=2.0,
    )
    await db_session.commit()

    async def fake_classify(*, name, intro_text, candidate_text):
        return 0.2  # below threshold

    monkeypatch.setattr(
        "domovoi.voice_classifier.classify_third_party_match",
        fake_classify,
    )

    ctx = Context(
        session_id=sid, room_id="kitchen", online=True, person_id=introducer_id
    )
    response = Response(text="Timer set for 5 minutes.", session_id=sid)
    injected = await maybe_inject_third_party_ask(
        db_session, ctx=ctx, response=response,
    )
    await db_session.commit()

    assert injected is False
    assert "alex" not in response.text.lower()
    assert response.expect_followup is False


@requires_db
@pytest.mark.asyncio
async def test_inject_ask_skips_when_speaker_is_not_introducer(
    db_session, monkeypatch,
) -> None:
    introducer_id = await PeopleRepository(db_session).create(name="Kamron")
    other_id = await PeopleRepository(db_session).create(name="Other")
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")

    pcm = np.full(16_000, 1234, dtype=np.int16).tobytes()
    embedding = await StubVoiceEmbedder().embed(pcm)
    assert embedding is not None

    await save_third_party_expectation(
        db_session,
        sid,
        {
            "introducer_person_id": introducer_id,
            "name": "Alex",
            "intro_transcript": "domovoi, this is alex",
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "ttl_sec": 600,
            "candidate_clusters": [],
        },
    )
    await buffer_unknown_voice_turn(
        db_session, session_id=sid, transcript="hi",
        embedding=embedding, audio_seconds=2.0,
    )
    await db_session.commit()

    async def fake_classify(*, name, intro_text, candidate_text):
        return 0.99

    monkeypatch.setattr(
        "domovoi.voice_classifier.classify_third_party_match",
        fake_classify,
    )

    # Speaker is `other_id`, not the introducer → no ask.
    ctx = Context(
        session_id=sid, room_id="kitchen", online=True, person_id=other_id
    )
    response = Response(text="Sure.", session_id=sid)
    injected = await maybe_inject_third_party_ask(
        db_session, ctx=ctx, response=response,
    )
    assert injected is False


# ─── Confirmation handler ─────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_third_party_confirm_yes_enrolls_and_clears_expectation(
    db_session,
) -> None:
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")

    pcm = np.full(16_000, 1234, dtype=np.int16).tobytes()
    embedding = await StubVoiceEmbedder().embed(pcm)
    assert embedding is not None
    centroid_b64 = base64.b64encode(
        embedding.astype(np.float32).tobytes()
    ).decode("ascii")

    introducer_id = await PeopleRepository(db_session).create(name="Kamron")
    await save_third_party_expectation(
        db_session,
        sid,
        {
            "introducer_person_id": introducer_id,
            "name": "Alex",
            "intro_transcript": "domovoi, this is alex",
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "ttl_sec": 600,
            "candidate_clusters": [
                {"centroid_b64": centroid_b64, "samples": [], "total_audio_seconds": 2.0}
            ],
        },
    )
    await db_session.commit()

    handler = VoiceProfileHandler()
    ctx = Context(
        session_id=sid, room_id="kitchen", online=True, person_id=introducer_id
    )
    response = await handler.handle_confirmation(
        kind="core.third_party_intro_confirm",
        data={
            "name": "Alex",
            "cluster_centroid_b64": centroid_b64,
            "cluster_idx": 0,
        },
        affirmative=True,
        ctx=ctx,
        session=db_session,
    )
    await db_session.commit()

    assert "alex" in response.text.lower()
    rows = (
        await db_session.execute(text("SELECT name FROM people ORDER BY id"))
    ).all()
    names = [r[0] for r in rows]
    assert "Alex" in names

    profile_count = (
        await db_session.execute(text("SELECT count(*) FROM voice_profiles"))
    ).scalar_one()
    assert profile_count == 1

    expectation = await load_third_party_expectation(db_session, sid)
    assert expectation is None  # cleared on yes


@requires_db
@pytest.mark.asyncio
async def test_third_party_confirm_no_drops_cluster_keeps_expectation(
    db_session,
) -> None:
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")

    e1 = base64.b64encode(_fake_embedding(1).astype(np.float32).tobytes()).decode(
        "ascii"
    )
    e2 = base64.b64encode(_fake_embedding(2).astype(np.float32).tobytes()).decode(
        "ascii"
    )
    introducer_id = await PeopleRepository(db_session).create(name="Kamron")
    await save_third_party_expectation(
        db_session,
        sid,
        {
            "introducer_person_id": introducer_id,
            "name": "Alex",
            "intro_transcript": "domovoi, this is alex",
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "ttl_sec": 600,
            "candidate_clusters": [
                {"centroid_b64": e1, "samples": [], "total_audio_seconds": 2.0},
                {"centroid_b64": e2, "samples": [], "total_audio_seconds": 2.0},
            ],
        },
    )
    await db_session.commit()

    handler = VoiceProfileHandler()
    ctx = Context(
        session_id=sid, room_id="kitchen", online=True, person_id=introducer_id
    )
    response = await handler.handle_confirmation(
        kind="core.third_party_intro_confirm",
        data={
            "name": "Alex",
            "cluster_centroid_b64": e1,
            "cluster_idx": 0,
        },
        affirmative=False,
        ctx=ctx,
        session=db_session,
    )
    await db_session.commit()

    assert "alex" in response.text.lower()
    expectation = await load_third_party_expectation(db_session, sid)
    assert expectation is not None
    # Cluster 0 dropped; cluster 1 (formerly index 1) is the sole survivor.
    remaining = expectation["candidate_clusters"]
    assert len(remaining) == 1
    assert remaining[0]["centroid_b64"] == e2

    # No enrollment happened.
    rows = (
        await db_session.execute(text("SELECT name FROM people"))
    ).all()
    names = [r[0] for r in rows]
    assert "Alex" not in names
