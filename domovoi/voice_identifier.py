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

Two sample sets (V026, ``voice_profiles.source``). A satellite's turn
(``samples="satellite"``, the default) is matched against satellite
samples only, so a phone can never move a satellite centroid. A turn from
the app's chat mic (``samples="phone"``, domovoi/phone_voice.py) scores
each person against their phone samples when they have any, else against
their satellite samples (:func:`phone_matching_samples`). A phone turn
never learns (job 4 is satellite-only); phone samples are only added on
purpose, by "my name is …" said into the chat mic (handlers/voice_profile.py).

Claimed samples (V029, delta review S1). A sample added because somebody
said a known person's name and "yes" counts in matching like any other
(owner decision 2026-10-16: the claim teaches rooms the voice), but it is
marked, and so is a room sample learned from a voice that matches only
through it (:func:`learned_through_claim`). "Forget me" holds the voice
against the person's OWN samples (:func:`is_own_voice`) before it deletes
a person, and otherwise removes only that voice's claims
(:func:`claims_by_this_voice`).

Every decision is logged at INFO with the best and runner-up scores, so
a misrecognition can be read back from the journal: a satellite turn's
line (every turn, whatever happened: too short, no samples, timed out,
opted out) by the streaming layer, with the turn's history row and the
recording's length (:class:`VoiceMatch`, kept on the row since V049); a
phone turn's here.

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
    someone known, not a newcomer. ``candidates`` are those two people,
    best first (empty unless ambiguous) — "who am I" says when they share
    a name (handlers/voice_profile.py).
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
    candidates: tuple[int, ...] = ()
    #: What identification decided (one of the ``OUTCOME_*`` values), and
    #: who scored best and second best even when nobody was named — kept on
    #: a satellite turn's history row (V049, :class:`VoiceMatch`).
    outcome: str | None = None
    best_person_id: int | None = None
    runner_up_person_id: int | None = None


# ─── A turn's identification, as kept and shown (V049) ────────────────────

OUTCOME_CONFIDENT = "confident"
OUTCOME_MATCH = "match"
OUTCOME_AMBIGUOUS = "ambiguous"
OUTCOME_NO_MATCH = "no_match"
OUTCOME_TOO_SHORT = "too_short"
OUTCOME_NO_EMBEDDING = "no_embedding"
OUTCOME_NO_SAMPLES = "no_samples"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_DENYLIST = "denylist"
OUTCOME_ERROR = "error"

OUTCOMES = frozenset({
    OUTCOME_CONFIDENT, OUTCOME_MATCH, OUTCOME_AMBIGUOUS, OUTCOME_NO_MATCH,
    OUTCOME_TOO_SHORT, OUTCOME_NO_EMBEDDING, OUTCOME_NO_SAMPLES, OUTCOME_TIMEOUT,
    OUTCOME_DENYLIST, OUTCOME_ERROR,
})

# Outcomes with no person scored: nothing but the outcome is kept.
_UNSCORED = frozenset({
    OUTCOME_TOO_SHORT, OUTCOME_NO_EMBEDDING, OUTCOME_NO_SAMPLES, OUTCOME_TIMEOUT,
    OUTCOME_DENYLIST, OUTCOME_ERROR,
})


@dataclass(frozen=True)
class VoiceMatch:
    """How identification scored one satellite turn: what is logged for
    every turn and kept on its ``conversation_log`` row (V049). Scores are
    the per-person centroid cosines :func:`_decide` ranked; a denylisted
    voice keeps no scores (its similarity is to the opt-out, not to a
    person). ``speech_seconds``: the recording's length, which decides
    ``too_short``."""

    outcome: str
    best_person_id: int | None = None
    best_score: float | None = None
    runner_up_person_id: int | None = None
    runner_up_score: float | None = None
    speech_seconds: float | None = None

    @classmethod
    def of(
        cls,
        ident: "IdentificationResult | None",
        *,
        outcome: str | None = None,
        speech_seconds: float | None = None,
    ) -> "VoiceMatch":
        """The match for a turn's identification result; ``outcome``
        overrides it (``timeout`` / ``error`` when there is no result)."""
        if ident is None or outcome is not None:
            return cls(outcome=outcome or OUTCOME_ERROR, speech_seconds=speech_seconds)
        out = getattr(ident, "outcome", None) or _inferred_outcome(ident)
        if out in _UNSCORED:
            return cls(outcome=out, speech_seconds=speech_seconds)
        return cls(
            outcome=out,
            best_person_id=getattr(ident, "best_person_id", None),
            best_score=_rounded(getattr(ident, "best_similarity", None)),
            runner_up_person_id=getattr(ident, "runner_up_person_id", None),
            runner_up_score=_rounded(getattr(ident, "runner_up_similarity", None)),
            speech_seconds=speech_seconds,
        )

    def columns(self) -> dict[str, Any]:
        """The V049 columns of ``conversation_log``."""
        return {
            "voice_outcome": self.outcome,
            "voice_best_person_id": self.best_person_id,
            "voice_best_score": self.best_score,
            "voice_runner_up_person_id": self.runner_up_person_id,
            "voice_runner_up_score": self.runner_up_score,
        }

    def describe(self) -> str:
        """The log's words for it: "match: best person_id=3 0.812,
        runner-up person_id=5 0.604", "too short to tell (0.8 s < 1.0 s)"."""
        secs = self.speech_seconds
        if self.outcome == OUTCOME_TOO_SHORT:
            if secs is None:
                return "too short to tell"
            return (
                f"too short to tell ({secs:.1f} s < "
                f"{settings.voice_profile_min_utterance_sec:.1f} s)"
            )
        words = {
            OUTCOME_NO_EMBEDDING: "no voice embedding (the embedder gave none)",
            OUTCOME_NO_SAMPLES: "nobody has a voice sample to compare with",
            OUTCOME_TIMEOUT: "timed out; the turn went on unidentified",
            OUTCOME_DENYLIST: "an opted-out voice (denylist); not identified",
            OUTCOME_ERROR: "failed; the turn went on unidentified",
        }
        if self.outcome in words:
            return words[self.outcome]
        best = (
            "none" if self.best_person_id is None or self.best_score is None
            else f"person_id={self.best_person_id} {self.best_score:.3f}"
        )
        runner = (
            "none" if self.runner_up_person_id is None or self.runner_up_score is None
            else f"person_id={self.runner_up_person_id} {self.runner_up_score:.3f}"
        )
        return (
            f"{self.outcome.replace('_', ' ')}: best {best}, runner-up {runner} "
            f"(threshold {settings.voice_profile_match_threshold:.2f}, "
            f"margin {settings.voice_profile_match_margin:.2f})"
        )


def _inferred_outcome(ident: Any) -> str:
    """The outcome of a result that doesn't say (one made by hand)."""
    if getattr(ident, "denylisted", False):
        return OUTCOME_DENYLIST
    if getattr(ident, "person_id", None) is not None:
        return OUTCOME_MATCH
    if getattr(ident, "ambiguous", False):
        return OUTCOME_AMBIGUOUS
    if getattr(ident, "best_similarity", None) is not None:
        return OUTCOME_NO_MATCH
    if getattr(ident, "embedding", None) is None:
        return OUTCOME_NO_EMBEDDING
    return OUTCOME_NO_SAMPLES


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(float(value), 3)


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


#: ``identify(samples=...)``: which set a turn is matched against.
SATELLITE_SAMPLES = "satellite"
PHONE_SAMPLES = "phone"


# ─── A person's own voice, apart from claimed samples (V029) ──────────────


def centroid_score(embedding: np.ndarray, blobs: list[bytes]) -> float | None:
    """The cosine of ``embedding`` against the centroid of ``blobs`` (as
    :func:`_person_scores` scores a person), or None when none is usable."""
    vectors = []
    for blob in blobs:
        stored = _deserialize_embedding(blob)
        norm = float(np.linalg.norm(stored))
        if norm:
            vectors.append(stored / norm)
    if not vectors:
        return None
    return _cosine(embedding, np.mean(vectors, axis=0))


def own_voice_blobs(samples: list[Any], *, phone: bool) -> list[bytes]:
    """The samples a turn's voice is held against to decide whether it IS
    this person (delta review S1): the person's own samples only — never
    one added by a name claim, or learned through one — in the set the turn
    is matched with (a phone turn: their own phone samples when they have
    any, else their own satellite samples). ``samples`` are
    ``VoiceProfilesRepository.person_samples`` rows."""
    own = [s for s in samples if s.own]
    if phone:
        mine = [s.embedding for s in own if s.source == PHONE_SAMPLES]
        if mine:
            return mine
    return [s.embedding for s in own if s.source == SATELLITE_SAMPLES]


def is_own_voice(embedding: np.ndarray, samples: list[Any], *, phone: bool) -> bool:
    """Whether ``embedding`` matches the person's OWN voice at the match
    threshold (:func:`own_voice_blobs`) — what "forget me" asks before it
    deletes a person."""
    score = centroid_score(embedding, own_voice_blobs(samples, phone=phone))
    return score is not None and score >= settings.voice_profile_match_threshold


def claims_by_this_voice(embedding: np.ndarray, samples: list[Any]) -> list[int]:
    """The claims (sample ids) this voice made: every claimed sample that it
    matches at the threshold, or that a sample learned through it does —
    "forget me" from a voice that is not the person's own removes these,
    and with them what was learned through them (V029's cascade)."""
    threshold = settings.voice_profile_match_threshold
    roots: set[int] = set()
    for s in samples:
        root = s.claim_root
        if root is None or root in roots:
            continue
        if _cosine(embedding, _deserialize_embedding(s.embedding)) >= threshold:
            roots.add(root)
    claimed_ids = {s.id for s in samples if s.claimed}
    return sorted(r for r in roots if r in claimed_ids)


def learned_through_claim(embedding: np.ndarray, samples: list[Any]) -> int | None:
    """For a clip a satellite is about to learn (job 4): None when it
    matches the person's own satellite voice, else the claim it was
    recognized through — the claimed (or claim-learned) sample it is
    closest to — so the learned sample goes with that claim. ``samples``:
    the person's ``person_samples`` rows."""
    satellite = [s for s in samples if s.source == SATELLITE_SAMPLES]
    if all(s.own for s in satellite):
        return None
    if is_own_voice(embedding, satellite, phone=False):
        return None
    claimed = [s for s in satellite if not s.own]
    best = max(claimed, key=lambda s: _cosine(embedding, _deserialize_embedding(s.embedding)))
    return best.claim_root


def phone_matching_samples(
    rows: list[tuple[int, int, bytes, str | None, str]],
) -> list[tuple[int, bytes]]:
    """``(person_id, embedding_bytes)`` a phone turn is scored against,
    from ``VoiceProfilesRepository.phone_matching_rows``: each person's
    phone samples when they have any, else their satellite samples. Pure."""
    with_phone = {person_id for _id, person_id, _blob, _room, source in rows if source == "phone"}
    return [
        (person_id, blob)
        for _id, person_id, blob, _room, source in rows
        if (source == "phone") == (person_id in with_phone)
    ]


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


def speaker_is_confident(score: float | None) -> bool:
    """Whether a stored match score is one to show at full strength: the
    bar a match has to clear to count as *confident* here (the threshold
    plus ``drift_near_threshold_margin``, :func:`_decide`). The chat's
    speaker marker (a phone's voice turns, domovoi/phone_voice.py) is drawn
    softer below it. Judged from the score alone, so a message read back
    later is drawn the way it was when it arrived."""
    if score is None:
        return False
    return float(score) >= (
        settings.voice_profile_match_threshold
        + settings.voice_profile_drift_near_threshold_margin
    )


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
            repo = VoiceProfilesRepository(s)
            # A voice recognized only through somebody's name claim (V029)
            # is not this person's own: the room learns it as part of that
            # claim, so "forget me" from that voice takes it away too.
            claim_of = learned_through_claim(
                embedding, await repo.person_samples(person_id, repo.EMBEDDING_MODEL_TAG),
            )
            await repo.add(
                person_id=person_id,
                embedding=_serialize_embedding(embedding),
                model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
                room_id=room_id,
                sample_seconds=None,
                claim_of=claim_of,
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
    samples: str = SATELLITE_SAMPLES,
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

    ``samples``: :data:`SATELLITE_SAMPLES` (a satellite's turn: satellite
    samples only) or :data:`PHONE_SAMPLES` (the app's chat mic: a person's
    phone samples first, see the module docstring). A phone turn never
    learns, whatever ``room_id`` says.
    """
    phone = samples == PHONE_SAMPLES
    if embedding is _EMBED_HERE:
        embedding = await embed_voice(pcm_int16)

    if embedding is None:
        # Sub-second clip, embedder unavailable, etc. Use a high tier so
        # any explicit identity-revealing utterance ("I'm Sarah") still
        # gets prompted to enroll on the next turn that produces a
        # usable vector.
        seconds = len(pcm_int16 or b"") / (16_000 * 2)
        return IdentificationResult(
            person_id=None,
            presence_tier="high",
            embedding=None,
            best_similarity=None,
            outcome=(
                OUTCOME_TOO_SHORT if seconds < settings.voice_profile_min_utterance_sec
                else OUTCOME_NO_EMBEDDING
            ),
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
                    outcome=OUTCOME_DENYLIST,
                )
    except Exception as e:
        log.warning("voice denylist lookup failed: %s", e)
        # Fall through to normal matching — better to risk a "are you
        # X?" prompt than to fail-closed and treat everyone as
        # denylisted on a transient DB error.

    decision = _decide({})
    last_seen = None
    outcome = OUTCOME_ERROR

    try:
        async with session_scope() as s:
            if phone:
                phone_rows = await VoiceProfilesRepository(s).phone_matching_rows(
                    VoiceProfilesRepository.EMBEDDING_MODEL_TAG
                )
                profiles = []
                matched_against = phone_matching_samples(phone_rows)
            else:
                profiles = await VoiceProfilesRepository(s).all_for_model(
                    VoiceProfilesRepository.EMBEDDING_MODEL_TAG
                )
                matched_against = [
                    (person_id, blob) for _id, person_id, blob, _room in profiles
                ]
            last_seen = await PeopleRepository(s).latest_seen()

        decision = _decide(_person_scores(embedding, matched_against))
        outcome = _outcome_of(decision)
        if decision.best_person is not None and phone:
            # A satellite turn's line is logged by the streaming layer, for
            # every turn, with its history row and the recording's length
            # (turn_history.record_voice_match); a phone turn's here.
            log.info(
                "voice identification %s=%s: %s — best person_id=%d %.3f, "
                "runner-up %s (threshold %.2f, margin %.2f)",
                "phone" if phone else "room", room_id,
                decision.outcome, decision.best_person, decision.best,
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

            if not phone:
                # Learning is a satellite's alone: a phone turn never
                # saves a sample by itself, into either set.
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
        elif decision.best_person is not None and room_id is not None and not phone:
            # Top candidate but not named: a run of confident matches
            # for them in this room is broken.
            _CONFIDENT_HITS.pop((decision.best_person, room_id), None)
    except Exception as e:
        log.warning("voice identification DB lookup failed: %s", e)
        matched_person = None
        outcome = OUTCOME_ERROR

    ambiguous = decision.ambiguous and matched_person is None
    return IdentificationResult(
        person_id=matched_person,
        presence_tier=_presence_tier_from_last_seen(last_seen),
        embedding=embedding,
        best_similarity=decision.best,
        denylisted=False,
        runner_up_similarity=decision.runner_up,
        ambiguous=ambiguous,
        candidates=(
            tuple(
                pid for pid in (decision.best_person, decision.runner_up_person)
                if pid is not None
            )
            if ambiguous else ()
        ),
        outcome=outcome,
        best_person_id=decision.best_person,
        runner_up_person_id=decision.runner_up_person,
    )


def _outcome_of(decision: _Decision) -> str:
    """:data:`OUTCOME_*` for a verdict of :func:`_decide`."""
    if decision.best_person is None:
        return OUTCOME_NO_SAMPLES
    if decision.person_id is not None:
        return OUTCOME_CONFIDENT if decision.confident else OUTCOME_MATCH
    return OUTCOME_AMBIGUOUS if decision.ambiguous else OUTCOME_NO_MATCH
