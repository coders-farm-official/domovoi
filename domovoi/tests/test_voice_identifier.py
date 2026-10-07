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
from domovoi import voice_identifier
from domovoi.voice_identifier import (
    _CONFIDENT_HITS,
    _cosine,
    _decide,
    _person_scores,
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


# ─── Deciding: per-person scores, the margin, confidence ────────────────────
# Pure — no database — so these run everywhere, including boxes where the
# requires_db tests skip.


def _unit(*components: float) -> np.ndarray:
    """A unit vector in the embedding space from its first few
    coordinates (the rest zero) — lets a test pin exact cosines."""
    v = np.zeros(256, dtype=np.float32)
    v[: len(components)] = components
    return v / np.linalg.norm(v)


def _blob(v: np.ndarray) -> bytes:
    return v.astype(np.float32).tobytes()


def test_decide_names_nobody_below_threshold() -> None:
    d = _decide({1: 0.70, 2: 0.40})
    assert d.person_id is None and not d.ambiguous and not d.confident
    assert d.best_person == 1 and d.best == pytest.approx(0.70)


def test_decide_two_close_candidates_is_ambiguous() -> None:
    """Both clear the 0.75 floor, 0.02 apart (< the 0.05 margin): two
    similar voices, so nobody is named instead of a coin flip."""
    d = _decide({1: 0.79, 2: 0.81})
    assert d.person_id is None
    assert d.ambiguous
    assert d.best_person == 2 and d.runner_up_person == 1


def test_decide_clear_lead_over_a_contender_matches_but_does_not_learn() -> None:
    """Runner-up also clears the floor but trails by more than the
    margin: named, yet never confident enough to save a sample from."""
    d = _decide({1: 0.88, 2: 0.78})
    assert d.person_id == 1
    assert not d.ambiguous
    assert not d.confident


def test_decide_runner_up_below_floor_is_not_a_contender() -> None:
    """A runner-up under the threshold can't make a match ambiguous,
    however close: the threshold already rejected it."""
    d = _decide({1: 0.77, 2: 0.745})
    assert d.person_id == 1 and not d.ambiguous


def test_decide_borderline_match_is_never_confident() -> None:
    """The Beelink incident's number: 0.760, 0.010 over the floor. It
    may still name someone, but it must never count towards saving."""
    d = _decide({2: 0.760, 1: 0.70})
    assert d.person_id == 2
    assert not d.confident


def test_decide_confident_match() -> None:
    d = _decide({1: 0.86, 2: 0.55})
    assert d.person_id == 1 and d.confident


def test_decide_only_one_person_enrolled() -> None:
    d = _decide({1: 0.86})
    assert d.person_id == 1 and d.confident and d.runner_up is None


def test_decide_empty() -> None:
    d = _decide({})
    assert d.person_id is None and d.best is None and d.outcome == "no match"


def test_decide_reads_the_margin_setting(monkeypatch) -> None:
    monkeypatch.setattr(voice_identifier.settings, "voice_profile_match_margin", 0.0)
    assert _decide({1: 0.79, 2: 0.78}).person_id == 1
    monkeypatch.setattr(voice_identifier.settings, "voice_profile_match_margin", 0.2)
    assert _decide({1: 0.95, 2: 0.78}).ambiguous


def test_person_scores_use_the_centroid_not_the_best_sample() -> None:
    """One stray sample that happens to sit on the query must not let a
    person win: their score is against the mean of their samples. Under
    the old max-over-samples rule person 2 scored 1.0 here."""
    query = _unit(1, 0, 0)
    person_1 = _unit(0.85, 0.5268, 0)           # cos 0.85 to the query
    person_2 = [_unit(1, 0, 0), _unit(0, 0, 1)]  # one on it, one far off
    scores = _person_scores(
        query,
        [(1, _blob(person_1)), (2, _blob(person_2[0])), (2, _blob(person_2[1]))],
    )
    assert scores[1] == pytest.approx(0.85, abs=1e-3)
    assert scores[2] == pytest.approx(1 / np.sqrt(2), abs=1e-3)
    assert _decide(scores).person_id == 1


def test_person_scores_skip_unusable_rows() -> None:
    """Wrong-size and all-zero rows are skipped, not averaged in; a
    person with nothing usable isn't scored at all."""
    query = _unit(1, 0)
    scores = _person_scores(
        query,
        [
            (1, _blob(_unit(1, 0))),
            (1, b"\x00" * 16),                        # wrong size
            (2, _blob(np.zeros(256, dtype=np.float32))),
        ],
    )
    assert scores == {1: pytest.approx(1.0, abs=1e-5)}


# ─── identify() end to end, on an in-memory store ──────────────────────────
# The real function, with the database swapped for a list, so the learning
# policy is exercised on every box (the DB-backed versions below skip
# without Postgres).


class _Store:
    def __init__(self) -> None:
        self.profiles: list[tuple[int, int, bytes, str | None]] = []
        self.touched: list[int] = []

    def enroll(self, person_id: int, v: np.ndarray, room: str | None) -> None:
        self.profiles.append((len(self.profiles) + 1, person_id, _blob(v), room))

    def count(self, person_id: int) -> int:
        return sum(1 for p in self.profiles if p[1] == person_id)


@pytest.fixture
def store(monkeypatch) -> _Store:
    st = _Store()

    class _Scope:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    class _Profiles:
        EMBEDDING_MODEL_TAG = "resemblyzer-v1"

        def __init__(self, _s) -> None: ...

        async def all_for_model(self, _model):
            return list(st.profiles)

        async def add(self, *, person_id, embedding, model, room_id, sample_seconds):
            st.profiles.append((len(st.profiles) + 1, person_id, embedding, room_id))
            return len(st.profiles)

    class _Denylist:
        EMBEDDING_MODEL_TAG = "resemblyzer-v1"

        def __init__(self, _s) -> None: ...

        async def all_for_model(self, _model):
            return []

    class _People:
        def __init__(self, _s) -> None: ...

        async def latest_seen(self):
            return None

        async def touch_last_seen(self, person_id):
            st.touched.append(person_id)

    monkeypatch.setattr(voice_identifier, "session_scope", _Scope)
    monkeypatch.setattr(voice_identifier, "VoiceProfilesRepository", _Profiles)
    monkeypatch.setattr(voice_identifier, "VoiceDenylistRepository", _Denylist)
    monkeypatch.setattr(voice_identifier, "PeopleRepository", _People)
    return st


# Two similar voices: the two enrollment clips are 0.74 apart.
_ALEX = _unit(1, 0, 0)
_BLAKE = _unit(0.74, 0.6726, 0)
# One of Alex's later utterances that happens to sit closer to Blake's
# enrollment (0.76) than to Alex's own (0.72) — the Beelink incident.
_ALEX_OFF_DAY = _unit(0.72, 0.3378, 0.6062)


async def test_borderline_mixup_is_never_saved(store) -> None:
    """The incident, replayed. Alex's voice scores 0.760 against Blake
    and 0.720 against Alex, again and again. The old code saved the
    third one into Blake's profile, after which Alex was "Blake" for
    good. Now no sample is ever saved from it."""
    store.enroll(1, _ALEX, "office")
    store.enroll(2, _BLAKE, "office")
    for _ in range(10):
        result = await identify(b"", embedding=_ALEX_OFF_DAY, room_id="basement")
        assert result.best_similarity == pytest.approx(0.76, abs=2e-3)
        assert result.runner_up_similarity == pytest.approx(0.72, abs=2e-3)
    assert store.count(1) == 1 and store.count(2) == 1
    assert _CONFIDENT_HITS == {}


async def test_two_close_voices_are_ambiguous_end_to_end(store) -> None:
    store.enroll(1, _ALEX, "office")
    store.enroll(2, _BLAKE, "office")
    # 0.80 to Alex, ~0.79 to Blake: both clear 0.75, 0.01 apart.
    between = _unit(0.80, 0.2944, 0.5227)
    result = await identify(b"", embedding=between, room_id="office")
    assert result.person_id is None
    assert result.ambiguous
    assert store.touched == []


async def test_confident_matches_learn_a_new_room_once(store, monkeypatch) -> None:
    monkeypatch.setattr(
        voice_identifier.settings, "voice_profile_drift_reenroll_after", 3
    )
    store.enroll(1, _ALEX, "office")
    store.enroll(2, _BLAKE, "office")
    clear = _unit(0.95, -0.2, 0.2397)  # 0.95 to Alex, ~0.57 to Blake

    for _ in range(2):
        result = await identify(b"", embedding=clear, room_id="basement")
        assert result.person_id == 1
    assert store.count(1) == 1                     # not yet
    await identify(b"", embedding=clear, room_id="basement")
    assert store.count(1) == 2                     # third in a row: saved
    assert store.profiles[-1][1:] == (1, _blob(clear), "basement")

    for _ in range(6):                             # basement is learned
        await identify(b"", embedding=clear, room_id="basement")
    for _ in range(6):                             # enrolled in the office
        await identify(b"", embedding=clear, room_id="office")
    assert store.count(1) == 2
    assert store.count(2) == 1
    assert store.touched == [1] * 15


async def test_a_borderline_match_breaks_the_run(store, monkeypatch) -> None:
    """"In a row" means in a row: a non-confident match of the same
    person in the same room starts the count over."""
    monkeypatch.setattr(
        voice_identifier.settings, "voice_profile_drift_reenroll_after", 3
    )
    store.enroll(1, _ALEX, "office")
    clear = _unit(1, 0, 0)
    borderline = _unit(0.77, 0, 0.6380)
    for v in (clear, clear, borderline, clear, clear):
        await identify(b"", embedding=v, room_id="garage")
    assert store.count(1) == 1
    await identify(b"", embedding=clear, room_id="garage")
    assert store.count(1) == 2


async def test_no_room_no_learning(store, monkeypatch) -> None:
    monkeypatch.setattr(
        voice_identifier.settings, "voice_profile_drift_reenroll_after", 1
    )
    store.enroll(1, _ALEX, "office")
    result = await identify(b"", embedding=_ALEX)
    assert result.person_id == 1
    assert store.count(1) == 1


# ─── Room learning against Postgres ────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_learning_counter():
    """Each test gets a fresh in-memory counter — the module-level dict
    persists across tests in the same run otherwise."""
    _CONFIDENT_HITS.clear()
    yield
    _CONFIDENT_HITS.clear()


async def _enroll_sarah(db_session, room: str = "kitchen") -> int:
    embedding = await StubVoiceEmbedder().embed(_SARAH)
    sarah_id = await PeopleRepository(db_session).create(name="Sarah")
    await VoiceProfilesRepository(db_session).add(
        person_id=sarah_id,
        embedding=embedding.astype(np.float32).tobytes(),
        model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
        room_id=room,
        sample_seconds=1.0,
    )
    await db_session.commit()
    return sarah_id


async def _sample_rooms(db_session, person_id: int) -> list[str | None]:
    rows = await db_session.execute(
        text(
            "SELECT room_id FROM voice_profiles WHERE person_id = :id "
            "ORDER BY id"
        ),
        {"id": person_id},
    )
    return [r[0] for r in rows.all()]


@requires_db
@pytest.mark.asyncio
async def test_learns_a_new_room_after_confident_matches(
    db_session, monkeypatch,
) -> None:
    """Stub embeddings of the same PCM score 1.0; with the floor at 0.95
    and the near band 0.01 wide, that's confident. Three in a row in a
    room without a sample saves one there, tagged with the room."""
    monkeypatch.setattr(voice_identifier.settings, "voice_profile_match_threshold", 0.95)
    monkeypatch.setattr(
        voice_identifier.settings, "voice_profile_drift_near_threshold_margin", 0.01
    )
    monkeypatch.setattr(voice_identifier.settings, "voice_profile_drift_reenroll_after", 3)
    sarah_id = await _enroll_sarah(db_session)

    for _ in range(3):
        result = await identify(_SARAH, room_id="garage")
        assert result.person_id == sarah_id
    assert await _sample_rooms(db_session, sarah_id) == ["kitchen", "garage"]
    assert (sarah_id, "garage") not in _CONFIDENT_HITS

    for _ in range(4):
        await identify(_SARAH, room_id="garage")
        await identify(_SARAH, room_id="kitchen")
    assert await _sample_rooms(db_session, sarah_id) == ["kitchen", "garage"]


@requires_db
@pytest.mark.asyncio
async def test_borderline_matches_never_save_a_sample(
    db_session, monkeypatch,
) -> None:
    """The near band covers the 1.0 score here, so every match is
    borderline: recognized, never saved — the old drift re-enroll saved
    the third one."""
    monkeypatch.setattr(voice_identifier.settings, "voice_profile_match_threshold", 0.95)
    monkeypatch.setattr(
        voice_identifier.settings, "voice_profile_drift_near_threshold_margin", 0.10
    )
    monkeypatch.setattr(voice_identifier.settings, "voice_profile_drift_reenroll_after", 1)
    sarah_id = await _enroll_sarah(db_session)

    for _ in range(5):
        result = await identify(_SARAH, room_id="garage")
        assert result.person_id == sarah_id
    assert await _sample_rooms(db_session, sarah_id) == ["kitchen"]
    assert _CONFIDENT_HITS == {}


@requires_db
@pytest.mark.asyncio
async def test_identical_voices_on_two_people_are_ambiguous(db_session) -> None:
    """Two people enrolled with the same voice: a tie, so nobody is
    named, and neither person's last_seen is touched."""
    embedding = await StubVoiceEmbedder().embed(_SARAH)
    people = PeopleRepository(db_session)
    profiles = VoiceProfilesRepository(db_session)
    ids = [await people.create(name=n) for n in ("Sarah", "Sam")]
    for pid in ids:
        await profiles.add(
            person_id=pid,
            embedding=embedding.astype(np.float32).tobytes(),
            model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
            room_id="kitchen",
            sample_seconds=1.0,
        )
    await db_session.commit()

    result = await identify(_SARAH, room_id="kitchen")
    assert result.person_id is None
    assert result.ambiguous
    assert result.best_similarity == pytest.approx(1.0, abs=1e-5)
    assert result.runner_up_similarity == pytest.approx(1.0, abs=1e-5)
