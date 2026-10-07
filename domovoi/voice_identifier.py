"""Pre-router voice identification.

Called once per turn (in ``streaming.py``) between Whisper transcription
and router dispatch. Five jobs:

1. Embed the utterance via the configured ``VoiceEmbedder``.
2. Check the denylist. If the embedding matches
   anyone who's said "never save my voice", return early with
   ``denylisted=True`` and no person_id — downstream prompts skip them.
3. Score every known person and decide (:func:`_decide`). A person's
   score is the cosine against the centroid of their samples, so having
   more samples doesn't mean more chances to win. The best person is
   named only when they clear ``voice_profile_match_threshold`` AND beat
   the runner-up by ``voice_profile_match_margin``; two people both
   clearing the threshold within that gap is "ambiguous" — nobody is
   named, rather than a coin flip between two similar voices.
4. Learn rooms. A match that is *confident* — at least
   ``drift_near_threshold_margin`` above the threshold, with nobody else
   even reaching the threshold — counts towards learning that person's
   voice in this room. After ``drift_reenroll_after`` confident matches
   in a row there, the clip is saved as their sample for that room, at
   most one per room. Borderline or contested matches never save
   anything: saving them is how one person's voice ends up filed under
   somebody else, which then makes the mix-up permanent (2026-10, the
   Beelink: a 0.760 match, 0.010 over the cutoff, saved one voice into a
   friend's profile, and from then on it was recognized as the friend).
   In-memory counter — restart resets it.
5. Compute the presence tier — how long since *any* known voice was
   heard — so the rest of the system can decide how aggressively to
   prompt for identity. ``low`` (recent activity) means stay quiet;
   ``high`` (long idle) means an unknown voice deserves a direct ask.

Every decision is logged at INFO with the best and runner-up scores, so
a misrecognition can be read back from the journal.

Returns ``IdentificationResult`` plus the raw embedding so the
VoiceProfileHandler can persist it on enrollment without re-running
the encoder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

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
    the encoder. ``best_similarity`` is the closest person's score even
    when below threshold, and ``runner_up_similarity`` the next person's
    (None with fewer than two people enrolled).
    ``ambiguous`` is True when two people both cleared the threshold too
    close together to tell apart: ``person_id`` is None, but the voice is
    someone known, not a newcomer.
    ``denylisted`` is True when this voice has been opted out via the
    denylist — downstream tier-driven prompts should skip them.
    """

    person_id: int | None
    presence_tier: str  # "low" | "medium" | "high"
    embedding: np.ndarray | None
    best_similarity: float | None
    denylisted: bool = False
    runner_up_similarity: float | None = None
    ambiguous: bool = False


# In-memory counter for learning a person's voice in a room. Keyed by
# (person_id, room_id); value is consecutive confident matches there.
# Reset on server restart, which is fine — the worst case is one
# delayed learning cycle.
_CONFIDENT_HITS: dict[tuple[int, str], int] = {}


@dataclass(frozen=True)
class _Decision:
    """:func:`_decide`'s verdict on one embedding.

    ``best_person`` is the top-scoring person even when they aren't
    named; ``person_id`` is set only on a match. ``confident`` marks a
    match clear enough to learn from (see the module docstring)."""

    person_id: int | None
    best_person: int | None
    best: float | None
    runner_up_person: int | None
    runner_up: float | None
    ambiguous: bool
    confident: bool

    @property
    def outcome(self) -> str:
        if self.person_id is not None:
            return "confident match" if self.confident else "match"
        if self.ambiguous:
            return "ambiguous"
        return "no match"


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


def _person_scores(
    embedding: np.ndarray, samples: list[tuple[int, bytes]]
) -> dict[int, float]:
    """``{person_id: score}`` for ``(person_id, embedding_bytes)`` samples.

    A person's score is the cosine against the mean of their unit-norm
    samples — Resemblyzer's own way of building a speaker embedding from
    several utterances. Scoring each sample separately and taking the
    max (the old way) gave whoever had the most samples the most chances
    to win, and let one stray sample decide. Unusable rows (wrong size,
    all zeros) are skipped; a person with none left isn't scored."""
    by_person: dict[int, list[np.ndarray]] = {}
    for person_id, blob in samples:
        stored = _deserialize_embedding(blob)
        norm = float(np.linalg.norm(stored))
        if norm == 0.0:
            continue
        by_person.setdefault(person_id, []).append(stored / norm)
    return {
        person_id: _cosine(embedding, np.mean(vectors, axis=0))
        for person_id, vectors in by_person.items()
    }


def _decide(scores: dict[int, float]) -> _Decision:
    """Name the speaker, or decline to. Pure — settings in, verdict out.

    * best < threshold → no match.
    * best and runner-up both ≥ threshold and less than
      ``voice_profile_match_margin`` apart → ambiguous, nobody named.
    * otherwise the best person is named. The match is *confident* when
      best ≥ threshold + ``drift_near_threshold_margin`` and the
      runner-up didn't reach the threshold at all.

    A runner-up below the threshold never makes a match ambiguous: it
    wasn't a candidate, and the threshold alone already rejects it.
    """
    if not scores:
        return _Decision(None, None, None, None, None, False, False)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_person, best = ranked[0]
    runner_up_person, runner_up = ranked[1] if len(ranked) > 1 else (None, None)
    threshold = settings.voice_profile_match_threshold

    def verdict(person_id: int | None, ambiguous: bool, confident: bool) -> _Decision:
        return _Decision(
            person_id, best_person, best, runner_up_person, runner_up,
            ambiguous, confident,
        )

    if best < threshold:
        return verdict(None, False, False)
    contested = runner_up is not None and runner_up >= threshold
    if contested and best - runner_up < settings.voice_profile_match_margin:
        return verdict(None, True, False)
    confident = (
        not contested
        and best >= threshold + settings.voice_profile_drift_near_threshold_margin
    )
    return verdict(best_person, False, confident)


def _presence_tier_from_last_seen(last_seen) -> str:
    if last_seen is None:
        return "high"
    elapsed = (utcnow() - last_seen).total_seconds()
    if elapsed < settings.voice_profile_soft_tier_sec:
        return "low"
    if elapsed < settings.voice_profile_hard_tier_sec:
        return "medium"
    return "high"


async def embed_voice(pcm_int16: bytes) -> np.ndarray | None:
    """Job 1 alone: the utterance's embedding, or None when the embedder
    can't make one. Pure — no database, no learning counter, no last_seen —
    so it can run on a speculative copy of a capture that the turn may
    never use (``domovoi/streaming.py``, "Speculative transcription").
    The rest of :func:`identify` has side effects and runs once per turn.
    """
    embedder = get_voice_embedder()
    try:
        return await embedder.embed(pcm_int16)
    except Exception as e:
        log.warning("voice embedder threw: %s", e)
        return None


# `identify(embedding=...)` default: embed the audio here.
_EMBED_HERE: Any = object()


async def _learn_room(
    person_id: int,
    room_id: str | None,
    embedding: np.ndarray,
    rooms_with_sample: set[str],
    decision: _Decision,
) -> None:
    """Job 4: count a match towards learning ``person_id``'s voice in
    ``room_id``, and save the clip once the count is reached.

    Only a confident match counts; any other match of this person in
    this room resets the run, so "in a row" means in a row. Nothing is
    learned without a room, or in a room the person already has a
    sample from."""
    if room_id is None or room_id in rooms_with_sample:
        return
    key = (person_id, room_id)
    if not decision.confident:
        _CONFIDENT_HITS.pop(key, None)
        return
    _CONFIDENT_HITS[key] = _CONFIDENT_HITS.get(key, 0) + 1
    if _CONFIDENT_HITS[key] < settings.voice_profile_drift_reenroll_after:
        return
    try:
        async with session_scope() as s:
            await VoiceProfilesRepository(s).add(
                person_id=person_id,
                embedding=_serialize_embedding(embedding),
                model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
                room_id=room_id,
                sample_seconds=None,
            )
    except Exception as e:
        log.warning("voice sample for a new room: DB write failed: %s", e)
        # Don't reset the counter on a write failure — the next
        # confident match will retry.
        return
    log.info(
        "voice sample for a new room: saved person_id=%d's voice in "
        "room=%s after %d confident matches (latest %.3f, runner-up %s)",
        person_id, room_id, _CONFIDENT_HITS[key], decision.best,
        "none" if decision.runner_up is None else f"{decision.runner_up:.3f}",
    )
    _CONFIDENT_HITS.pop(key, None)


async def identify(
    pcm_int16: bytes,
    *,
    embedding: Any = _EMBED_HERE,
    room_id: str | None = None,
) -> IdentificationResult:
    """Embed + match + compute presence. Best-effort — failures degrade
    to ``person_id=None, presence_tier="high"`` so the core
    keeps working when the embedder's broken or the DB hiccups.

    ``embedding``: :func:`embed_voice`'s result for this same audio, when
    it was computed already (alongside a speculative decode); then only
    the matching runs here. Either way this is the one call per turn that
    touches ``last_seen`` and the learning counter.

    ``room_id``: the room the audio came from. Without one, nothing is
    learned (job 4).
    """
    if embedding is _EMBED_HERE:
        embedding = await embed_voice(pcm_int16)

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

    decision = _decide({})
    last_seen = None

    try:
        async with session_scope() as s:
            profiles = await VoiceProfilesRepository(s).all_for_model(
                VoiceProfilesRepository.EMBEDDING_MODEL_TAG
            )
            last_seen = await PeopleRepository(s).latest_seen()

        decision = _decide(
            _person_scores(
                embedding,
                [(person_id, blob) for _id, person_id, blob, _room in profiles],
            )
        )
        if decision.best_person is not None:
            log.info(
                "voice identification room=%s: %s — best person_id=%d %.3f, "
                "runner-up %s (threshold %.2f, margin %.2f)",
                room_id, decision.outcome, decision.best_person, decision.best,
                "none" if decision.runner_up is None
                else f"person_id={decision.runner_up_person} {decision.runner_up:.3f}",
                settings.voice_profile_match_threshold,
                settings.voice_profile_match_margin,
            )

        matched_person = decision.person_id
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

            await _learn_room(
                matched_person,
                room_id,
                embedding,
                {
                    room for _id, person_id, _blob, room in profiles
                    if person_id == matched_person and room is not None
                },
                decision,
            )
        elif decision.best_person is not None and room_id is not None:
            # Top candidate but not named: a run of confident matches
            # for them in this room is broken.
            _CONFIDENT_HITS.pop((decision.best_person, room_id), None)
    except Exception as e:
        log.warning("voice identification DB lookup failed: %s", e)
        matched_person = None

    return IdentificationResult(
        person_id=matched_person,
        presence_tier=_presence_tier_from_last_seen(last_seen),
        embedding=embedding,
        best_similarity=decision.best,
        denylisted=False,
        runner_up_similarity=decision.runner_up,
        ambiguous=decision.ambiguous and matched_person is None,
    )
