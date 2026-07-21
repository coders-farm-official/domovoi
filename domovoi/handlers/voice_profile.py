"""VoiceProfileHandler — local speaker enrollment + identification UX.

Pairs with the pre-router hook in ``streaming.py`` (which embeds the
utterance and attaches ``person_id`` + ``presence_tier`` to the
Context). This handler covers the *user-facing* parts:

  * Self-introductions: "I'm Sarah" / "my name is Sarah" / "call me
    Sarah" / "this is Sarah" → start a confirmation flow.
  * Third-party introductions: "Domovoi, this is my friend Alex" →
    same flow but the introducer attests.
  * Queries: "who am I", "do you know me", "do you know who I am" →
    answer from current ``ctx.person_id``.
  * Forget: "forget me" / "don't save my voice" → cascade-delete
    rows from ``people`` (and ``voice_profiles`` via FK), letting
    ``intents_log.person_id`` lapse to NULL via the ``ON DELETE
    SET NULL``.

The confirmation flow uses the multi-turn ``pending_confirmation``
mechanism in ``router.py``: the introduction stashes the candidate
name + b64-serialized embedding into ``sessions.context``, and the
follow-up "yes"/"no" routes to ``handle_confirmation`` here.

We keep the embedding around in the pending payload (rather than
re-embedding on confirmation) because re-embedding the *next*
utterance — likely "yes" — produces a different vector. The original
intro utterance is the one we actually want to enroll against.

Fully local (`requires_network="no"`); embedding + match are CPU-only,
DB writes go to the same Postgres the rest of the system uses.
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timezone

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.clients.voice_embedder import EMBEDDING_DIM
from domovoi.config import settings
from domovoi.db.repositories import (
    PeopleRepository,
    SessionRepository,
    VoiceDenylistRepository,
    VoiceProfilesRepository,
)
from domovoi.confirmations import request_confirmation
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


# ─── Fast-path regexes ────────────────────────────────────────────────────

# Self-intro. The lookahead `\b` before name guards against "I'm hungry"
# (no real name) — but a bare "I'm Sarah" is a real name, so we just
# require *some* trailing word. Capitalization isn't preserved (router
# lowercases) — we'll Title Case the captured name on enrollment.
_SELF_INTRO_RE = re.compile(
    r"^(?:"
    r"i(?:'m| am) (?P<name>[a-z][a-z'\-]+(?: [a-z][a-z'\-]+)?)"
    r"|my name(?:'s| is) (?P<name2>[a-z][a-z'\-]+(?: [a-z][a-z'\-]+)?)"
    r"|call me (?P<name3>[a-z][a-z'\-]+(?: [a-z][a-z'\-]+)?)"
    r"|this is (?P<name4>[a-z][a-z'\-]+(?: [a-z][a-z'\-]+)?)"
    r")$"
)

# Third-party intro. A known speaker attesting that the named person is
# also in the room. We *don't* enroll immediately — we have audio of the
# introducer, not of the introduced person. Instead we park a
# `third_party_expectation` in sessions.context; ``streaming.py``
# buffers any unknown-voice utterances during the window into clusters,
# and on the introducer's next turn we run an LLM classifier to score
# the clusters and ask "By the way, was that <name>?". The introducer's
# yes confirms enrollment; their no drops just that cluster, leaving
# the expectation alive for an actually-Alex utterance later.
_THIRD_PARTY_INTRO_RE = re.compile(
    r"^(?:domovoi|hey domovoi|jarvis|hey jarvis)?,?\s*"
    r"(?:meet|this is) (?P<name>[a-z][a-z'\-]+(?: [a-z][a-z'\-]+)?)"
    r"(?:,\s*(?:my|a) (?:friend|partner|colleague|kid|child|son|daughter|wife|husband|spouse))?"
    r"$"
)

# "Who am I (talking to)" / "do you know me" / "do you know who I am"
_WHOAMI_RE = re.compile(
    r"^(?:"
    r"who am i(?: talking to)?"
    r"|do you (?:know|recognize) me"
    r"|do you know who i am"
    r")$"
)

# "Forget me" / "don't save my voice" / "forget my voice"
_FORGET_RE = re.compile(
    r"^(?:"
    r"forget me"
    r"|forget my voice"
    r"|don't (?:save|remember|keep) my voice"
    r"|delete my voice(?: profile)?"
    r")$"
)

# Persistent opt-out. Distinct from `_FORGET_RE` —
# forget DELETES existing data, denylist ADDS the current voice to a
# persistent suppression list so future utterances from this voice
# don't get prompted for identity. The two are complementary: a user
# can "forget me" to wipe their existing profile AND "never save my
# voice" to prevent re-enrollment.
#
# Anchored against the regex's tendency to over-match — "never save"
# and "don't ever save" without "my voice" / "me" would be too greedy
# and could swallow phrasings that aren't about voice at all.
_DENYLIST_RE = re.compile(
    r"^(?:"
    r"never save my voice"
    r"|don't ever save my voice"
    r"|stop saving my voice"
    r"|block my voice(?: profile)?"
    r"|never (?:remember|recognize|save) me"
    r"|opt me out of voice (?:profiles|profile|recognition)"
    r")$"
)


# ─── Helpers ──────────────────────────────────────────────────────────────


# Reserved words that shouldn't become a person's name. Whisper sometimes
# transcribes "I'm bored" / "I'm hungry" / "I'm late" — the regex
# captures "bored" / "hungry" / "late" as the name. Drop a small
# blocklist of obvious common-adjectives to avoid creating a person
# named "Hungry."
_NAME_BLOCKLIST = frozenset(
    {
        "hungry", "tired", "bored", "late", "back", "sorry", "good", "fine",
        "okay", "ok", "ready", "done", "here", "there", "home", "out",
        "happy", "sad", "angry", "busy", "free", "lost", "stuck", "fed up",
        "in", "going", "leaving", "thinking", "wondering", "cold", "hot",
        "right", "wrong", "old", "young", "afraid", "scared", "excited",
    }
)


def _clean_name(raw: str) -> str | None:
    """Title-case a captured name, stripping trailing junk and rejecting
    obvious non-names. Returns None if the name fails the sniff test."""
    cleaned = raw.strip().rstrip(".,!?").lower()
    if cleaned in _NAME_BLOCKLIST:
        return None
    if not cleaned or len(cleaned) > 60:
        return None
    # Title-case each space-separated chunk so "mary jane" → "Mary Jane".
    return " ".join(part.capitalize() for part in cleaned.split())


def _b64_embedding(emb: np.ndarray) -> str:
    return base64.b64encode(emb.astype(np.float32).tobytes()).decode("ascii")


def _decode_b64_embedding(s: str) -> np.ndarray | None:
    try:
        raw = base64.b64decode(s.encode("ascii"))
        arr = np.frombuffer(raw, dtype=np.float32)
        if arr.size != EMBEDDING_DIM:
            return None
        return arr
    except Exception:
        return None


def _confirmation_payload(name: str, embedding: np.ndarray, kind: str) -> dict:
    return {
        "kind": kind,
        "name": name,
        "embedding_b64": _b64_embedding(embedding),
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }


# ─── Third-party expectation lifecycle ────────────────────────────────────


THIRD_PARTY_EXPECTATION_KEY = "third_party_expectation"


def _expectation_is_expired(expectation: dict) -> bool:
    """A stored expectation past `started_at + ttl_sec` is dropped on
    next read — see ``load_third_party_expectation``."""
    started_at = expectation.get("started_at")
    ttl = expectation.get("ttl_sec") or settings.third_party_intro_ttl_sec
    if not started_at:
        return True
    try:
        started = datetime.fromisoformat(started_at)
    except Exception:
        return True
    age = (datetime.now(tz=timezone.utc) - started).total_seconds()
    return age > float(ttl)


async def load_third_party_expectation(
    session: AsyncSession, session_id
) -> dict | None:
    """Read the expectation, transparently dropping it if past TTL.

    Returns the expectation dict on success, or None if no expectation
    is parked or it has expired (in which case the expired payload is
    cleared from sessions.context as a side effect)."""
    if session_id is None:
        return None
    repo = SessionRepository(session)
    ctx_data = await repo.get_context(session_id) or {}
    expectation = ctx_data.get(THIRD_PARTY_EXPECTATION_KEY)
    if not isinstance(expectation, dict):
        return None
    if _expectation_is_expired(expectation):
        log.info(
            "third-party expectation for %r expired; clearing",
            expectation.get("name"),
        )
        await repo.set_context_key(session_id, THIRD_PARTY_EXPECTATION_KEY, None)
        return None
    return expectation


async def save_third_party_expectation(
    session: AsyncSession, session_id, expectation: dict
) -> None:
    if session_id is None:
        return
    await SessionRepository(session).set_context_key(
        session_id, THIRD_PARTY_EXPECTATION_KEY, expectation
    )


async def clear_third_party_expectation(
    session: AsyncSession, session_id
) -> None:
    if session_id is None:
        return
    await SessionRepository(session).set_context_key(
        session_id, THIRD_PARTY_EXPECTATION_KEY, None
    )


# ─── Pre/post-route hooks for the streaming layer ──────────────────────────


async def buffer_unknown_voice_turn(
    session: AsyncSession,
    *,
    session_id,
    transcript: str,
    embedding: np.ndarray,
    audio_seconds: float,
) -> None:
    """If a third-party expectation is live and the current voice is
    unknown, append this turn to the expectation's candidate clusters
    via single-link cosine merge.

    No-op if no expectation is parked, the embedding is missing, or the
    audio is too short to be useful for enrollment downstream. Failures
    are best-effort: voice profiling never blocks the response cycle."""
    if session_id is None or embedding is None:
        return
    if audio_seconds < settings.voice_profile_min_utterance_sec:
        return
    try:
        expectation = await load_third_party_expectation(session, session_id)
    except Exception as e:
        log.warning("buffer_unknown_voice_turn: load failed: %s", e)
        return
    if expectation is None:
        return

    # Local import — keeps the voice_clusterer module out of the
    # voice_profile import graph except when actually exercised.
    from domovoi.voice_clusterer import add_to_clusters

    clusters = list(expectation.get("candidate_clusters") or [])
    add_to_clusters(
        clusters,
        embedding,
        transcript,
        audio_seconds,
        merge_threshold=settings.third_party_cluster_merge_threshold,
    )
    expectation["candidate_clusters"] = clusters
    try:
        await save_third_party_expectation(session, session_id, expectation)
    except Exception as e:
        log.warning("buffer_unknown_voice_turn: save failed: %s", e)
        return
    log.info(
        "buffered unknown-voice turn into expectation %r: %d cluster(s), "
        "%.1fs total in best cluster",
        expectation.get("name"),
        len(clusters),
        max(
            (c.get("total_audio_seconds", 0.0) for c in clusters), default=0.0
        ),
    )


async def maybe_inject_third_party_ask(
    session: AsyncSession,
    *,
    ctx: Context,
    response: Response,
) -> bool:
    """If the introducer just spoke and a buffered cluster scores above
    the classifier threshold, mutate ``response`` in place to append
    "By the way, was that <name>?", set ``expect_followup=True``, and
    park a pending_confirmation so the next turn's yes/no routes back
    to this handler.

    Returns True if an ask was injected — caller should then re-record
    the conversation_log row's assistant_text so the audit trail
    matches what the user actually heard.

    No-op when:
      * no expectation is parked
      * no candidate clusters have accumulated yet
      * the speaker isn't the introducer
      * total audio across all clusters is below the classifier minimum
      * every cluster scores below the classifier threshold
    """
    if ctx.session_id is None or ctx.person_id is None:
        return False

    try:
        expectation = await load_third_party_expectation(session, ctx.session_id)
    except Exception as e:
        log.warning("maybe_inject_third_party_ask: load failed: %s", e)
        return False
    if expectation is None:
        return False
    if ctx.person_id != expectation.get("introducer_person_id"):
        return False

    clusters = expectation.get("candidate_clusters") or []
    if not clusters:
        return False

    # Only run the classifier on clusters with enough material to be
    # worth asking about. Shorter ones stay buffered for next time.
    eligible = [
        (i, c) for i, c in enumerate(clusters)
        if c.get("total_audio_seconds", 0.0)
        >= settings.third_party_min_cluster_audio_sec
    ]
    if not eligible:
        return False

    from domovoi.voice_classifier import classify_third_party_match
    from domovoi.voice_clusterer import (
        cluster_centroid,
        cluster_transcript_text,
    )

    name = expectation.get("name", "")
    intro_text = expectation.get("intro_transcript", "")

    best_idx = -1
    best_conf = -1.0
    for idx, cluster in eligible:
        candidate_text = cluster_transcript_text(cluster)
        conf = await classify_third_party_match(
            name=name,
            intro_text=intro_text,
            candidate_text=candidate_text,
        )
        log.info(
            "third-party classifier: cluster %d conf=%.2f text=%r",
            idx, conf, candidate_text[:80],
        )
        if conf > best_conf:
            best_conf = conf
            best_idx = idx

    if best_idx < 0 or best_conf < settings.third_party_classifier_threshold:
        return False

    cluster = clusters[best_idx]
    centroid = cluster_centroid(cluster)
    if centroid is None:
        return False

    try:
        await request_confirmation(
            session,
            ctx.session_id,
            kind="core.third_party_intro_confirm",
            handler="voice_profile",
            data={
                "name": name,
                "cluster_centroid_b64": _b64_embedding(centroid),
                "cluster_idx": best_idx,
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
            },
        )
    except Exception as e:
        log.warning("maybe_inject_third_party_ask: park-pending failed: %s", e)
        return False

    ask = f" By the way, was that {name}?"
    response.text = (response.text or "").rstrip() + ask
    response.expect_followup = True
    log.info(
        "injected third-party ask: name=%r cluster=%d conf=%.2f",
        name, best_idx, best_conf,
    )
    return True


# ─── Handler ──────────────────────────────────────────────────────────────


class VoiceProfileHandler(Handler):
    name = "voice_profile"
    # band rationale: "i'm Sarah" / "forget me" / "who am I" win over any greedy capture
    #   (especially "this is X" vs music's "play X"). Sits right after
    #   dismiss (100), which owns the closed "I'm good"/"I'm fine" set.
    priority_band = 110
    display = HandlerDisplay(label="Voice Profile", tone="info")
    confirmation_kinds = ("core.self_intro", "core.third_party_intro_confirm")
    requires_network = "no"

    tool_schema = {
        "name": "voice_profile",
        "description": (
            "Manage who's currently talking. Use 'introduce' to start "
            "an enrollment flow when the user names themselves; 'query' "
            "to read back the current speaker; 'forget' to delete a "
            "person's voice profile."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["introduce_self", "query", "forget"],
                },
                "name": {
                    "type": "string",
                    "description": "Speaker's stated name (introduce_self only).",
                },
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            # Denylist BEFORE forget — both have "don't save my voice"
            # adjacent phrasings; explicit "never save my voice" should
            # add to denylist, not just trigger one-shot forget.
            FastPath(_DENYLIST_RE, VoiceProfileHandler._denylist_from_match),
            FastPath(_FORGET_RE, VoiceProfileHandler._forget_from_match),
            FastPath(_WHOAMI_RE, VoiceProfileHandler._whoami_from_match),
            # Third-party intros must come before self-intros — both can
            # match "this is Alex", but the third-party form's leading
            # "Domovoi," disambiguator wins when present.
            FastPath(_THIRD_PARTY_INTRO_RE, VoiceProfileHandler._third_party_from_match),
            FastPath(_SELF_INTRO_RE, VoiceProfileHandler._self_intro_from_match),
        ]

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        return Response(
            text="I didn't catch a voice-profile command.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        action = args.get("action")
        if action == "introduce_self":
            name = _clean_name(args.get("name") or "")
            if not name:
                return Response(
                    text="What name should I use?",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
            return await self._start_self_intro(name, ctx, session)
        if action == "query":
            return await self._whoami(ctx, session)
        if action == "forget":
            return await self._forget(ctx, session)
        return Response(
            text=f"I don't know how to {action} a voice profile.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def handle_confirmation(
        self,
        kind: str,
        data: dict,
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        """Resume an enrollment flow that the previous turn parked.

        Called by the router when ``sessions.context.pending_confirmation``
        targets this handler. ``kind`` distinguishes self-intros from
        third-party intros (latter is a no-op stub for v1) so the same
        confirmation lands in the right code path.
        """
        if kind == "core.self_intro":
            if not affirmative:
                return Response(
                    text="Got it — I won't save that.",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
            name = data.get("name", "")
            embedding = _decode_b64_embedding(data.get("embedding_b64", ""))
            if not name or embedding is None:
                return Response(
                    text=(
                        "Something went wrong storing that — try saying "
                        "your name again."
                    ),
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
            return await self._enroll(name, embedding, ctx, session)
        if kind == "core.third_party_intro_confirm":
            return await self._handle_third_party_confirm(
                data, affirmative, ctx, session
            )
        # Unknown kind — fall through silently rather than spook the
        # user with a "what kind?" answer.
        return Response(
            text="Got it.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _handle_third_party_confirm(
        self,
        data: dict,
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        """Resolve a "By the way, was that <name>?" ask.

        Yes → enroll the buffered cluster centroid as <name> and clear
        the whole expectation (we got our person).

        No → drop only the cluster the classifier picked. The
        expectation stays alive: maybe Alex hasn't actually spoken yet,
        and the introducer is correcting a misfire on someone else.
        """
        name = data.get("name", "")
        cluster_centroid_b64 = data.get("cluster_centroid_b64", "")
        cluster_idx = data.get("cluster_idx")

        if not affirmative:
            # Drop just the rejected cluster from the expectation, keep
            # everything else (name, introducer, TTL) intact.
            try:
                expectation = await load_third_party_expectation(
                    session, ctx.session_id
                )
                if expectation is not None and isinstance(cluster_idx, int):
                    clusters = expectation.get("candidate_clusters") or []
                    if 0 <= cluster_idx < len(clusters):
                        del clusters[cluster_idx]
                        expectation["candidate_clusters"] = clusters
                        await save_third_party_expectation(
                            session, ctx.session_id, expectation
                        )
            except Exception as e:
                log.warning(
                    "couldn't drop rejected cluster on third-party no: %s", e
                )
            return Response(
                text=f"Got it, that wasn't {name}.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        embedding = _decode_b64_embedding(cluster_centroid_b64)
        if not name or embedding is None:
            return Response(
                text=(
                    "Something went wrong saving that voice — try the "
                    "introduction again."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        response = await self._enroll(name, embedding, ctx, session)
        try:
            await clear_third_party_expectation(session, ctx.session_id)
        except Exception as e:
            log.warning("couldn't clear third-party expectation post-enroll: %s", e)
        return response

    # ─── Fast-path adapters ───────────────────────────────────────────
    async def _self_intro_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        # Different alternatives capture into different group names.
        raw_name = (
            m.groupdict().get("name")
            or m.groupdict().get("name2")
            or m.groupdict().get("name3")
            or m.groupdict().get("name4")
            or ""
        )
        name = _clean_name(raw_name)
        if not name:
            # Likely "I'm hungry" / "I'm tired" type false positive.
            # Returning a Response with empty announce_to_rooms means
            # the router stamps it as the matched handler — but we'd
            # rather fall through to QA. For v1 we accept the slight
            # friction of a "I didn't catch your name" response over
            # surgically modifying router behavior.
            return Response(
                text="I didn't catch a name there.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return await self._start_self_intro(name, ctx, session)

    async def _third_party_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        name = _clean_name(m.group("name"))
        if not name:
            return Response(
                text="I didn't catch the name.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        # We need an identified introducer to anchor the deferred
        # confirmation step ("By the way, was that Alex?"). Without one,
        # there's no known person to ask later, so fall back to the v1
        # acknowledge-only behavior.
        if ctx.person_id is None or ctx.session_id is None:
            return Response(
                text=f"Hi {name}, nice to meet you.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        expectation = {
            "introducer_person_id": ctx.person_id,
            "name": name,
            "intro_transcript": m.string,
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "ttl_sec": settings.third_party_intro_ttl_sec,
            "candidate_clusters": [],
        }
        try:
            await save_third_party_expectation(session, ctx.session_id, expectation)
        except Exception as e:
            log.warning("couldn't park third-party expectation: %s", e)
            return Response(
                text=f"Hi {name}, nice to meet you.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        log.info(
            "parked third-party expectation: introducer_person_id=%d name=%r",
            ctx.person_id, name,
        )
        return Response(
            text=f"Hi {name}, nice to meet you.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _whoami_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._whoami(ctx, session)

    async def _forget_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._forget(ctx, session)

    async def _denylist_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._denylist(ctx, session)

    # ─── Core actions ─────────────────────────────────────────────────
    async def _start_self_intro(
        self, name: str, ctx: Context, session: AsyncSession
    ) -> Response:
        """Park the candidate name + embedding in pending_confirmation,
        ask the user to confirm. Two failure modes:

        * No session_id (programmatic /v1/intent without WebSocket
          context) — we can't park anything, so we acknowledge without
          enrolling.
        * No embedding (sub-second clip, embedder unavailable) — same
          deal: acknowledge without enrolling. The user can re-introduce
          on the next utterance once a usable embedding lands.
        """
        if (
            ctx.session_id is None
            or ctx.embedding_bytes is None
            or len(ctx.embedding_bytes) != EMBEDDING_DIM * 4
        ):
            return Response(
                text=(
                    f"Hi {name}, nice to meet you. I couldn't quite save "
                    "your voice this time — try saying it again in a "
                    "moment."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        # Already enrolled with that name? Skip the confirmation dance.
        if ctx.person_id is not None:
            try:
                existing = await PeopleRepository(session).get(ctx.person_id)
            except Exception as e:
                log.warning("PeopleRepository.get failed: %s", e)
                existing = None
            if existing is not None and existing[1].lower() == name.lower():
                return Response(
                    text=f"I already know you, {name}.",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )

        embedding = np.frombuffer(ctx.embedding_bytes, dtype=np.float32)
        try:
            await request_confirmation(
                session,
                ctx.session_id,
                kind="core.self_intro",
                handler=self.name,
                data=_confirmation_payload(name, embedding, "core.self_intro"),
            )
        except Exception as e:
            log.warning("couldn't park pending_confirmation: %s", e)
            return Response(
                text=f"Hi {name}, nice to meet you.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        return Response(
            text=f"Nice to meet you, {name}. Did I get that right?",
            session_id=ctx.session_id,
            matched_handler=self.name,
            # Tell the Pi to skip the wake-word gate for the user's
            # yes/no answer. Pairs with the `pending_confirmation`
            # parked above — the router will route the answer through
            # `handle_confirmation` regardless, but without this flag
            # the user would have to re-engage with "Hey Jarvis" first.
            expect_followup=True,
        )

    async def _is_denylisted(
        self, embedding: np.ndarray, session: AsyncSession
    ) -> bool:
        """True when ``embedding`` cosine-matches a voice_denylist row.

        Enrollment-side twin of the identifier's pre-match denylist check.
        The identify() path already suppresses identification for opted-out
        voices, but enrollment can be reached with embeddings that never
        passed through identify()'s check — most notably the third-party
        introduction flow, whose buffered cluster centroid is produced by
        the clusterer, not the identifier. Without this check an introducer
        saying "this is Alex" + "yes" would enroll a voice whose owner
        explicitly said "never save my voice" (audit: denylist bypass via
        third-party intro). Same threshold as profile matching, biasing
        toward respecting the opt-out on noisy embeddings.

        Fail-open on DB errors (mirrors the identifier): a transient
        denylist read failure shouldn't block a legitimate enrollment.
        """
        try:
            denylist = await VoiceDenylistRepository(session).all_for_model(
                VoiceDenylistRepository.EMBEDDING_MODEL_TAG
            )
        except Exception as e:
            log.warning("enrollment denylist lookup failed: %s", e)
            return False
        for _denylist_id, blob in denylist:
            stored = np.frombuffer(blob, dtype=np.float32)
            if stored.size != embedding.size:
                continue
            na = float(np.linalg.norm(embedding))
            nb = float(np.linalg.norm(stored))
            if na == 0.0 or nb == 0.0:
                continue
            sim = float(np.dot(embedding, stored) / (na * nb))
            if sim >= settings.voice_profile_match_threshold:
                log.info(
                    "enrollment blocked: embedding matches denylist row "
                    "(sim=%.3f)", sim,
                )
                return True
        return False

    async def _enroll(
        self,
        name: str,
        embedding: np.ndarray,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        # Denylist gate FIRST — applies to every enrollment path (self-intro
        # confirmation AND third-party intro confirmation). The response is
        # deliberately neutral: announcing "that voice opted out" would leak
        # the opt-out to whoever is trying to enroll them.
        if await self._is_denylisted(embedding, session):
            return Response(
                text=(
                    "I wasn't able to save that voice profile."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        try:
            people = PeopleRepository(session)
            voice_profiles = VoiceProfilesRepository(session)
            person_id = await people.create(name=name)
            await voice_profiles.add(
                person_id=person_id,
                embedding=embedding.astype(np.float32).tobytes(),
                model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
                room_id=ctx.room_id,
                sample_seconds=None,
            )
        except Exception as e:
            log.warning("voice enrollment failed for name=%s: %s", name, e)
            return Response(
                text=(
                    "Something went wrong saving your voice — your name "
                    "didn't get recorded."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        log.info("enrolled person=%s id=%d", name, person_id)
        return Response(
            text=f"Got it, {name}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _whoami(self, ctx: Context, session: AsyncSession) -> Response:
        if ctx.person_id is None:
            return Response(
                text="I don't recognize your voice yet.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        try:
            row = await PeopleRepository(session).get(ctx.person_id)
        except Exception as e:
            log.warning("whoami: PeopleRepository.get failed: %s", e)
            row = None
        if row is None:
            return Response(
                text="I don't recognize your voice yet.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return Response(
            text=f"You're {row[1]}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _forget(self, ctx: Context, session: AsyncSession) -> Response:
        if ctx.person_id is None:
            return Response(
                text="I don't have a voice profile for you to forget.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        try:
            row = await PeopleRepository(session).get(ctx.person_id)
            name = row[1] if row else "you"
            await PeopleRepository(session).delete(ctx.person_id)
        except Exception as e:
            log.warning("forget-me delete failed: %s", e)
            return Response(
                text="Something went wrong — your profile is still there.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        log.info("forgot person id=%d name=%s", ctx.person_id, name)
        return Response(
            text=f"Okay, I've forgotten you, {name}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _denylist(self, ctx: Context, session: AsyncSession) -> Response:
        """Add the current speaker's embedding to the persistent
        denylist.

        After this lands, the identifier's pre-match denylist check
        suppresses identification for any utterance whose embedding
        matches a denylist row above the match threshold — so this
        user's future speech bypasses any tier-driven "who are you?"
        prompt entirely.

        Two failure modes that both produce a graceful response:
        * No embedding (sub-second clip / embedder unavailable) —
          we'd write a useless zero vector. Refuse and ask to retry.
        * DB error — log and report. The denylist isn't life-critical.

        Doesn't *also* delete an existing profile if there is one;
        users who want both should chain "never save my voice" with
        "forget me" (they're separate intents).
        """
        if (
            ctx.embedding_bytes is None
            or len(ctx.embedding_bytes) != EMBEDDING_DIM * 4
        ):
            return Response(
                text=(
                    "I couldn't quite capture your voice this time — "
                    "say it again in a moment so I can opt you out."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        try:
            await VoiceDenylistRepository(session).add(
                embedding=ctx.embedding_bytes,
                model=VoiceDenylistRepository.EMBEDDING_MODEL_TAG,
                notes=f"opted out from room={ctx.room_id}" if ctx.room_id else None,
            )
        except Exception as e:
            log.warning("denylist add failed: %s", e)
            return Response(
                text="Something went wrong — your opt-out didn't save.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        log.info("denylist add: opted out voice from room=%s", ctx.room_id)
        return Response(
            text=(
                "Got it — I won't save your voice or ask about it again."
            ),
            session_id=ctx.session_id,
            matched_handler=self.name,
        )
