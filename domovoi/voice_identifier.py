"""Pre-router voice identification.

Called once per turn (in ``streaming.py``) between Whisper transcription
and router dispatch. Five jobs:

1. Embed the utterance via the configured ``VoiceEmbedder``.
2. Check the denylist. If the embedding matches
   anyone who's said "never save my voice", return early with
   ``denylisted=True`` and no person_id — downstream prompts skip them.
3. Find the closest known speaker by cosine similarity against every
   row in ``voice_profiles``. Match if the best similarity beats
   ``settings.voice_profile_match_threshold``.
4. Drift detection. When the same person matches
   "near threshold" (within ``drift_near_threshold_margin`` of the
   match cutoff) for ``drift_reenroll_after`` consecutive interactions,
   append a fresh embedding sample so future rounds have a closer
   reference to match against. In-memory counter — restart resets it.
5. Compute the presence tier — how long since *any* known voice was
   heard — so the rest of the system can decide how aggressively to
   prompt for identity. ``low`` (recent activity) means stay quiet;
   ``high`` (long idle) means an unknown voice deserves a direct ask.

Returns ``IdentificationResult`` plus the raw embedding so the
VoiceProfileHandler can persist it on enrollment without re-running
the encoder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from domovoi.clients.voice_embedder import (
    EMBEDDING_DIM,
    get_voice_embedder,
)
from domovoi.config import settings
from domovoi.db.repositories import (
    PeopleRepository,
    VoiceDenylistRepository,
    VoiceProfilesRepository,
    utcnow,
)
from domovoi.db.session import session_scope

log = logging.getLogger(__name__)


@dataclass
class IdentificationResult:
    """What the pre-router needs to attach to ``Context``.

    ``embedding`` is the raw float32 vector — kept around so the handler
    can stash it in ``voice_profiles`` on enrollment without re-running
    the encoder. ``best_similarity`` is the closest match score even
    when below threshold; useful for "your voice is close to Sarah's,
    is that you?" prompts down the line, currently just logged.
    ``denylisted`` is True when this voice has been opted out via the
    denylist — downstream tier-driven prompts should skip them.
    """

    person_id: int | None
    presence_tier: str  # "low" | "medium" | "high"
    embedding: np.ndarray | None
    best_similarity: float | None
    denylisted: bool = False


# In-memory per-person counter for drift re-enrollment.
# Reset on server restart, which is fine — the worst case is one
# delayed re-enroll cycle. Keyed by person_id; value is consecutive
# near-threshold match count.
_NEAR_THRESHOLD_HITS: dict[int, int] = {}


def _serialize_embedding(emb: np.ndarray) -> bytes:
    return emb.astype(np.float32).tobytes()


def _deserialize_embedding(b: bytes) -> np.ndarray:
    arr = np.frombuffer(b, dtype=np.float32)
    if arr.size != EMBEDDING_DIM:
        # Older / wrong-model embeddings — skip the comparison rather
        # than blow up the cosine math with mismatched shapes.
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)
    return arr


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Both vectors are unit-norm coming out of the embedder, so the
    cosine reduces to a dot product. We renormalize defensively in
    case a stored profile drifted off the unit sphere."""
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _presence_tier_from_last_seen(last_seen) -> str:
    if last_seen is None:
        return "high"
    elapsed = (utcnow() - last_seen).total_seconds()
    if elapsed < settings.voice_profile_soft_tier_sec:
        return "low"
    if elapsed < settings.voice_profile_hard_tier_sec:
        return "medium"
    return "high"


async def identify(pcm_int16: bytes) -> IdentificationResult:
    """Embed + match + compute presence. Best-effort — failures degrade
    to ``person_id=None, presence_tier="high"`` so the core
    keeps working when the embedder's broken or the DB hiccups.
    """
    embedder = get_voice_embedder()
    try:
        embedding = await embedder.embed(pcm_int16)
    except Exception as e:
        log.warning("voice embedder threw: %s", e)
        embedding = None

    if embedding is None:
        # Sub-second clip, embedder unavailable, etc. Use a high tier so
        # any explicit identity-revealing utterance ("I'm Sarah") still
        # gets prompted to enroll on the next turn that produces a
        # usable vector.
        return IdentificationResult(
            person_id=None,
            presence_tier="high",
            embedding=None,
            best_similarity=None,
        )

    # Denylist pre-empt. Check before the regular profile
    # match so a denylisted user never gets fed into the identification
    # path. We use the same threshold as profile matching — a borderline
    # match here is treated as a hit, biasing toward "respect their
    # opt-out even on noisy embeddings."
    try:
        async with session_scope() as s:
            denylist = await VoiceDenylistRepository(s).all_for_model(
                VoiceDenylistRepository.EMBEDDING_MODEL_TAG
            )
        for _denylist_id, blob in denylist:
            stored = _deserialize_embedding(blob)
            sim = _cosine(embedding, stored)
            if sim >= settings.voice_profile_match_threshold:
                log.info(
                    "voice identification: denylist hit (sim=%.3f); "
                    "suppressing identification + tier",
                    sim,
                )
                return IdentificationResult(
                    person_id=None,
                    # Force "low" tier: prevents any future tier-driven
                    # "I don't recognize you, who are you?" prompt from
                    # nagging someone who's explicitly opted out.
                    presence_tier="low",
                    embedding=embedding,
                    best_similarity=sim,
                    denylisted=True,
                )
    except Exception as e:
        log.warning("voice denylist lookup failed: %s", e)
        # Fall through to normal matching — better to risk a "are you
        # X?" prompt than to fail-closed and treat everyone as
        # denylisted on a transient DB error.

    best_person: int | None = None
    best_sim: float | None = None
    last_seen = None

    try:
        async with session_scope() as s:
            profiles = await VoiceProfilesRepository(s).all_for_model(
                VoiceProfilesRepository.EMBEDDING_MODEL_TAG
            )
            last_seen = await PeopleRepository(s).latest_seen()

        for _profile_id, person_id, blob in profiles:
            stored = _deserialize_embedding(blob)
            sim = _cosine(embedding, stored)
            if best_sim is None or sim > best_sim:
                best_sim = sim
                # Track best person even sub-threshold; we apply the cut
                # below.
                best_person = person_id

        matched_person: int | None = None
        if best_sim is not None and best_sim >= settings.voice_profile_match_threshold:
            matched_person = best_person

        if matched_person is not None:
            # Touch last_seen on a separate session so the read above
            # doesn't see its own write and miscount tiers. The new
            # last_seen lands in the DB but the in-memory `last_seen`
            # we computed the tier from is the pre-touch value, which
            # is what we want — "low" tier means *another* known voice
            # was active recently, not the one currently speaking.
            try:
                async with session_scope() as s:
                    await PeopleRepository(s).touch_last_seen(matched_person)
            except Exception as e:
                log.warning("touch_last_seen failed: %s", e)

            # Drift detection. When the match is in the
            # near-threshold band for several consecutive interactions,
            # append a fresh sample so subsequent rounds get a closer
            # reference vector. Reset the counter on a high-confidence
            # match so we don't keep accruing samples for a person whose
            # voice is being matched cleanly.
            margin = (best_sim or 0.0) - settings.voice_profile_match_threshold
            if 0.0 <= margin <= settings.voice_profile_drift_near_threshold_margin:
                _NEAR_THRESHOLD_HITS[matched_person] = (
                    _NEAR_THRESHOLD_HITS.get(matched_person, 0) + 1
                )
                if (
                    _NEAR_THRESHOLD_HITS[matched_person]
                    >= settings.voice_profile_drift_reenroll_after
                ):
                    try:
                        async with session_scope() as s:
                            await VoiceProfilesRepository(s).add(
                                person_id=matched_person,
                                embedding=_serialize_embedding(embedding),
                                model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
                                room_id=None,
                                sample_seconds=None,
                            )
                        log.info(
                            "drift re-enroll: appended fresh sample for "
                            "person_id=%d after %d near-threshold matches "
                            "(latest sim=%.3f, margin=%.3f)",
                            matched_person,
                            _NEAR_THRESHOLD_HITS[matched_person],
                            best_sim,
                            margin,
                        )
                        _NEAR_THRESHOLD_HITS.pop(matched_person, None)
                    except Exception as e:
                        log.warning("drift re-enroll DB write failed: %s", e)
                        # Don't reset the counter on a write failure —
                        # the next interaction will retry.
            else:
                # High-confidence match (or somehow below threshold —
                # shouldn't happen given the matched_person guard).
                # Reset the counter for this person.
                _NEAR_THRESHOLD_HITS.pop(matched_person, None)
    except Exception as e:
        log.warning("voice identification DB lookup failed: %s", e)
        matched_person = None

    return IdentificationResult(
        person_id=matched_person,
        presence_tier=_presence_tier_from_last_seen(last_seen),
        embedding=embedding,
        best_similarity=best_sim,
        denylisted=False,
    )
