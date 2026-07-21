from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest
from sqlalchemy import text

from domovoi.clients.voice_embedder import StubVoiceEmbedder
from domovoi.db.repositories import (
    PeopleRepository,
    VoiceDenylistRepository,
    VoiceProfilesRepository,
    utcnow,
)
from domovoi.tests.conftest import requires_db
from domovoi.voice_identifier import (
    _NEAR_THRESHOLD_HITS,
    _cosine,
    _presence_tier_from_last_seen,
    identify,
)


# Test PCMs — same bytes ⇒ same stub embedding ⇒ matchable; different
# bytes ⇒ different embedding.
_SARAH = (np.full(16_000, 1234, dtype=np.int16)).tobytes()
_BOB = (np.full(16_000, 5678, dtype=np.int16)).tobytes()
_STRANGER = (np.full(16_000, 9999, dtype=np.int16)).tobytes()


# ─── Pure helper tests ──────────────────────────────────────────────────────


def test_cosine_same_vector_is_one() -> None:
    v = np.array([0.6, 0.8], dtype=np.float32)
    assert _cosine(v, v) == pytest.approx(1.0, abs=1e-5)


def test_cosine_orthogonal_is_zero() -> None:
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    assert _cosine(a, b) == pytest.approx(0.0, abs=1e-6)


def test_cosine_handles_zero_vector() -> None:
    """Zero-norm input returns 0 instead of NaN — defensive against a
    deserialization bug producing an all-zero embedding."""
    assert _cosine(np.zeros(8, dtype=np.float32), np.ones(8, dtype=np.float32)) == 0.0


def test_presence_tier_no_known_people_is_high() -> None:
    """First boot, nobody enrolled — tier defaults to high so the next
    explicit "I'm Sarah" gets enrolled rather than ignored."""
    assert _presence_tier_from_last_seen(None) == "high"


def test_presence_tier_recent_is_low() -> None:
    assert _presence_tier_from_last_seen(utcnow() - timedelta(seconds=60)) == "low"


def test_presence_tier_mid_range_is_medium() -> None:
    # SOFT default 3600s, HARD 14400s; pick a value squarely in between.
    assert _presence_tier_from_last_seen(utcnow() - timedelta(hours=2)) == "medium"


def test_presence_tier_long_idle_is_high() -> None:
    assert _presence_tier_from_last_seen(utcnow() - timedelta(hours=5)) == "high"


# ─── Identification (DB + stub embedder) ───────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_identify_with_no_profiles_returns_no_match(db_session) -> None:
    """Empty DB → no match. Embedding still populated so a downstream
    enrollment attempt has something to store."""
    result = await identify(_SARAH)
    assert result.person_id is None
    assert result.presence_tier == "high"  # no known people = high tier
    assert result.embedding is not None
    assert result.embedding.shape == (256,)


@requires_db
@pytest.mark.asyncio
async def test_identify_matches_enrolled_voice(db_session) -> None:
    """Pre-enroll Sarah's stub embedding, then identify the same PCM:
    person_id should be Sarah's row."""
    embedding = await StubVoiceEmbedder().embed(_SARAH)
    assert embedding is not None

    people = PeopleRepository(db_session)
    profiles = VoiceProfilesRepository(db_session)
    sarah_id = await people.create(name="Sarah")
    await profiles.add(
        person_id=sarah_id,
        embedding=embedding.astype(np.float32).tobytes(),
        model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
        room_id="kitchen",
        sample_seconds=1.0,
    )
    await db_session.commit()

    result = await identify(_SARAH)
    assert result.person_id == sarah_id
    assert result.best_similarity is not None and result.best_similarity > 0.99


@requires_db
@pytest.mark.asyncio
async def test_identify_unknown_voice_with_profiles_present(db_session) -> None:
    """Sarah is enrolled; a stranger's PCM should NOT match Sarah."""
    embedding = await StubVoiceEmbedder().embed(_SARAH)
    assert embedding is not None
    people = PeopleRepository(db_session)
    profiles = VoiceProfilesRepository(db_session)
    sarah_id = await people.create(name="Sarah")
    await profiles.add(
        person_id=sarah_id,
        embedding=embedding.astype(np.float32).tobytes(),
        model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
        room_id="kitchen",
        sample_seconds=1.0,
    )
    await db_session.commit()

    result = await identify(_STRANGER)
    assert result.person_id is None  # stranger doesn't match Sarah
    # Sarah was just enrolled (last_seen set in create()), so for a
    # stranger walking up "right after" Sarah the tier is low.
    assert result.presence_tier == "low"


@requires_db
@pytest.mark.asyncio
async def test_identify_picks_closest_when_multiple_known_people(db_session) -> None:
    """Two known voices in the DB; identifying Bob's PCM should return
    Bob's id, not Sarah's."""
    sarah_emb = await StubVoiceEmbedder().embed(_SARAH)
    bob_emb = await StubVoiceEmbedder().embed(_BOB)
    assert sarah_emb is not None and bob_emb is not None

    people = PeopleRepository(db_session)
    profiles = VoiceProfilesRepository(db_session)
    sarah_id = await people.create(name="Sarah")
    bob_id = await people.create(name="Bob")
    for pid, emb in ((sarah_id, sarah_emb), (bob_id, bob_emb)):
        await profiles.add(
            person_id=pid,
            embedding=emb.astype(np.float32).tobytes(),
            model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
            room_id="kitchen",
            sample_seconds=1.0,
        )
    await db_session.commit()

    bob_result = await identify(_BOB)
    assert bob_result.person_id == bob_id

    sarah_result = await identify(_SARAH)
    assert sarah_result.person_id == sarah_id


@requires_db
@pytest.mark.asyncio
async def test_identify_updates_last_seen_on_match(db_session) -> None:
    """A successful match must touch people.last_seen_at so subsequent
    presence-tier calculations reflect the new activity."""
    embedding = await StubVoiceEmbedder().embed(_SARAH)
    assert embedding is not None

    people = PeopleRepository(db_session)
    profiles = VoiceProfilesRepository(db_session)
    sarah_id = await people.create(name="Sarah")
    await profiles.add(
        person_id=sarah_id,
        embedding=embedding.astype(np.float32).tobytes(),
        model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
        room_id="kitchen",
        sample_seconds=1.0,
    )
    # Force last_seen back in time so we can detect the touch.
    await db_session.execute(
        text(
            "UPDATE people SET last_seen_at = NOW() - INTERVAL '1 day' "
            "WHERE id = :id"
        ),
        {"id": sarah_id},
    )
    await db_session.commit()

    await identify(_SARAH)

    row = (
        await db_session.execute(
            text("SELECT last_seen_at FROM people WHERE id = :id"),
            {"id": sarah_id},
        )
    ).first()
    assert row is not None
    elapsed = (utcnow() - row[0]).total_seconds()
    assert elapsed < 30, "expected last_seen_at to be touched to ~now"


# ─── Denylist ───────────────────────────────────────────────

@requires_db
@pytest.mark.asyncio
async def test_identify_denylist_match_suppresses_identification(db_session) -> None:
    """A voice in the denylist should not be identified as anyone, even
    if they have an enrolled profile too. The denylist takes
    precedence — it's an explicit opt-out."""
    embedding = await StubVoiceEmbedder().embed(_SARAH)
    assert embedding is not None

    # Sarah in voice_profiles AND voice_denylist — denylist wins.
    people = PeopleRepository(db_session)
    profiles = VoiceProfilesRepository(db_session)
    denylist = VoiceDenylistRepository(db_session)

    sarah_id = await people.create(name="Sarah")
    await profiles.add(
        person_id=sarah_id,
        embedding=embedding.astype(np.float32).tobytes(),
        model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
        room_id="kitchen",
        sample_seconds=1.0,
    )
    await denylist.add(
        embedding=embedding.astype(np.float32).tobytes(),
        model=VoiceDenylistRepository.EMBEDDING_MODEL_TAG,
        notes="user opted out",
    )
    await db_session.commit()

    result = await identify(_SARAH)
    assert result.denylisted is True
    assert result.person_id is None  # suppressed even though Sarah is enrolled
    assert result.presence_tier == "low"  # forced low to skip future prompts


@requires_db
@pytest.mark.asyncio
async def test_identify_denylist_does_not_affect_other_voices(db_session) -> None:
    """A denylisted voice doesn't mask OTHER speakers' identification —
    only the matching voice gets suppressed."""
    sarah_emb = await StubVoiceEmbedder().embed(_SARAH)
    assert sarah_emb is not None

    # Add Sarah to denylist; Bob is not denylisted but is enrolled.
    denylist = VoiceDenylistRepository(db_session)
    people = PeopleRepository(db_session)
    profiles = VoiceProfilesRepository(db_session)

    await denylist.add(
        embedding=sarah_emb.astype(np.float32).tobytes(),
        model=VoiceDenylistRepository.EMBEDDING_MODEL_TAG,
    )
    bob_emb = await StubVoiceEmbedder().embed(_BOB)
    bob_id = await people.create(name="Bob")
    await profiles.add(
        person_id=bob_id,
        embedding=bob_emb.astype(np.float32).tobytes(),
        model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
        room_id="kitchen",
        sample_seconds=1.0,
    )
    await db_session.commit()

    # Bob should still be identified normally.
    bob_result = await identify(_BOB)
    assert bob_result.denylisted is False
    assert bob_result.person_id == bob_id


@requires_db
@pytest.mark.asyncio
async def test_identify_denylist_short_circuits_before_match(db_session) -> None:
    """Denylist check runs before profile matching — verify by adding a
    denylist row but no profiles. A non-empty embedding match against
    the denylist should still suppress."""
    embedding = await StubVoiceEmbedder().embed(_SARAH)
    assert embedding is not None
    denylist = VoiceDenylistRepository(db_session)
    await denylist.add(
        embedding=embedding.astype(np.float32).tobytes(),
        model=VoiceDenylistRepository.EMBEDDING_MODEL_TAG,
    )
    await db_session.commit()

    result = await identify(_SARAH)
    assert result.denylisted is True


# ─── Drift handling ────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_drift_counter():
    """Each drift test gets a fresh in-memory counter — the module-level
    dict persists across tests in the same run otherwise."""
    _NEAR_THRESHOLD_HITS.clear()
    yield
    _NEAR_THRESHOLD_HITS.clear()


@requires_db
@pytest.mark.asyncio
async def test_drift_counter_increments_on_near_threshold_match(
    db_session, monkeypatch,
) -> None:
    """When the same person matches with similarity in the near-threshold
    band, the in-memory counter increments per call."""
    # Force a near-threshold band that includes a perfect (1.0) match.
    monkeypatch.setattr(
        "domovoi.voice_identifier.settings.voice_profile_match_threshold",
        0.95,
    )
    monkeypatch.setattr(
        "domovoi.voice_identifier.settings.voice_profile_drift_near_threshold_margin",
        0.10,
    )
    monkeypatch.setattr(
        "domovoi.voice_identifier.settings.voice_profile_drift_reenroll_after",
        99,  # high so we don't trigger re-enroll in this test
    )

    embedding = await StubVoiceEmbedder().embed(_SARAH)
    sarah_id = await PeopleRepository(db_session).create(name="Sarah")
    await VoiceProfilesRepository(db_session).add(
        person_id=sarah_id,
        embedding=embedding.astype(np.float32).tobytes(),
        model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
        room_id="kitchen",
        sample_seconds=1.0,
    )
    await db_session.commit()

    await identify(_SARAH)
    await identify(_SARAH)
    await identify(_SARAH)

    assert _NEAR_THRESHOLD_HITS.get(sarah_id) == 3


@requires_db
@pytest.mark.asyncio
async def test_drift_reenrolls_after_threshold_count(
    db_session, monkeypatch,
) -> None:
    """After ``reenroll_after`` consecutive near-threshold matches, a
    fresh voice_profile row is appended for that person."""
    monkeypatch.setattr(
        "domovoi.voice_identifier.settings.voice_profile_match_threshold",
        0.95,
    )
    monkeypatch.setattr(
        "domovoi.voice_identifier.settings.voice_profile_drift_near_threshold_margin",
        0.10,
    )
    monkeypatch.setattr(
        "domovoi.voice_identifier.settings.voice_profile_drift_reenroll_after",
        3,
    )

    embedding = await StubVoiceEmbedder().embed(_SARAH)
    sarah_id = await PeopleRepository(db_session).create(name="Sarah")
    await VoiceProfilesRepository(db_session).add(
        person_id=sarah_id,
        embedding=embedding.astype(np.float32).tobytes(),
        model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
        room_id="kitchen",
        sample_seconds=1.0,
    )
    await db_session.commit()

    # Three near-threshold matches → re-enroll on the 3rd.
    await identify(_SARAH)
    await identify(_SARAH)
    await identify(_SARAH)

    rows = (await db_session.execute(
        text(
            "SELECT count(*) FROM voice_profiles WHERE person_id = :id"
        ),
        {"id": sarah_id},
    )).scalar_one()
    assert rows == 2  # original + drift-reenrolled fresh sample
    # Counter reset after re-enrollment.
    assert sarah_id not in _NEAR_THRESHOLD_HITS


@requires_db
@pytest.mark.asyncio
async def test_drift_high_confidence_match_resets_counter(
    db_session, monkeypatch,
) -> None:
    """A confidently-above-threshold match resets the drift counter so
    a noisy stretch followed by a clean match doesn't trigger
    spurious re-enrollment."""
    monkeypatch.setattr(
        "domovoi.voice_identifier.settings.voice_profile_match_threshold",
        0.95,
    )
    # Tight margin: similarity 1.0 is OUTSIDE the [0.95, 0.96] band → high-confidence.
    monkeypatch.setattr(
        "domovoi.voice_identifier.settings.voice_profile_drift_near_threshold_margin",
        0.01,
    )
    monkeypatch.setattr(
        "domovoi.voice_identifier.settings.voice_profile_drift_reenroll_after",
        2,
    )

    embedding = await StubVoiceEmbedder().embed(_SARAH)
    sarah_id = await PeopleRepository(db_session).create(name="Sarah")
    await VoiceProfilesRepository(db_session).add(
        person_id=sarah_id,
        embedding=embedding.astype(np.float32).tobytes(),
        model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
        room_id="kitchen",
        sample_seconds=1.0,
    )
    await db_session.commit()

    # Pre-populate the counter (simulating a prior near-threshold streak).
    _NEAR_THRESHOLD_HITS[sarah_id] = 1

    # High-confidence match with the tight margin → counter resets.
    await identify(_SARAH)
    assert sarah_id not in _NEAR_THRESHOLD_HITS

    # No second voice_profile row should have been added — high-conf doesn't re-enroll.
    rows = (await db_session.execute(
        text(
            "SELECT count(*) FROM voice_profiles WHERE person_id = :id"
        ),
        {"id": sarah_id},
    )).scalar_one()
    assert rows == 1
