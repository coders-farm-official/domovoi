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
  * Forget: "forget me" / "don't save my voice" → delete the person's
    own spoken turns (``conversation_log`` / ``intents_log`` rows
    attributed to them, :func:`voice_history.delete_person_history`), then
    cascade-delete rows from ``people`` (and ``voice_profiles`` via FK) —
    when the voice matches the person's OWN samples. A
    voice that was recognized only through samples a name claim added
    removes just those samples (V029, owner decision 2026-10-16, delta
    review S1): see :meth:`VoiceProfileHandler._forget`.

The confirmation flow uses the multi-turn ``pending_confirmation``
mechanism in ``router.py``: the introduction stashes the candidate
name + b64-serialized embedding into ``sessions.context`` and reads the
name back letter by letter ("Nice to meet you, Jordan. That's J, O, R, D,
A, N. Did I get that right?"). Since 2026-11-11 that question is a CHOICE
(``handle_choice_reply``): only the very next turn answers it, within
:data:`SELF_INTRO_TTL_SEC`, and the answer may correct the name — "no,
it's Jordyn", "yes, but it's spelled J-O-R-D-Y-N", the letters alone
(``domovoi/person_names.py`` reads every shape). "Yes, but …" is never a
yes. A heard name nobody has that sounds like one somebody has asks "Did
you mean Jordan?" (``core.name_sound_alike``) instead of making a second,
misheard person.

We keep the embedding around in the pending payload (rather than
re-embedding on confirmation) because re-embedding the *next*
utterance — likely "yes" — produces a different vector. The original
intro utterance is the one we actually want to enroll against.

From the app's chat mic (:func:`phone_chat_mic`; owner decision
2026-10-12) the voice goes into the person's PHONE samples
(``voice_profiles.source = 'phone'``, V026), which satellites never match:

  * "my name is X" where X is already someone → "Add this phone's voice
    to X?", and "yes" on the next press adds a phone sample to X (at most
    ``voice_profile_phone_sample_cap``, oldest dropped). No second X.
  * a new name → the usual "Did I get that right?", and the new person's
    sample is a phone sample.

Only the chat mic does this. A future "phone as a satellite" mode must
neither create nor extend phone profiles.

One name, one person — everywhere (2026-10-15; the phone had it first).
Nothing that enrols makes a second person with a name someone already
has (case and surrounding space aside, ``PeopleRepository.find_by_name``)
without asking first:

  * "my name is X" at a satellite, for an X who exists and isn't who the
    voice was matched as: "I already know someone called X. Is that
    you?" (:meth:`VoiceProfileHandler._offer_room_voice`), answered by
    the very next turn. Yes: it is X — this clip joins X's SATELLITE
    samples, tagged with the room. No: a different X — a new person with
    the name, from the same clip, at once ("Okay, I've added you as a new
    X. …", owner decision 2026-10-15); the pair is then namesakes.
  * the yes to "Did I get that right?" / "was that X?" (enrolment proper)
    re-checks the name under ``PeopleRepository.lock_names``: someone
    called X by then gets the sample instead of a twin.
  * two or more people called X (left over from before this rule, a
    "no" above, or a parent and child): nobody is guessed, and the answer
    says what to do —
    an admin renames or removes one in Settings, under People
    (:func:`_more_than_one`).
  * "who am I" names the person the voice matched and, when someone else
    has that name too, says so; a voice too close to two people says
    whether they share a name (``Context.voice_candidates``).
  * the People page's rename (``PATCH /api/people/{id}``) refuses a name
    someone else has, under the same lock.

A claim is not proof (delta review S1, owner decision 2026-10-16). Every
sample added to someone who already exists because the speaker said their
name and "yes" is marked as claimed (V029: when, where, and who the voice
was recognized as then); matching uses it like any other. "Forget me"
deletes the person only for a voice that matches the person's own samples
(:func:`voice_identifier.is_own_voice`) or for an admin on the People
page; any other voice loses only the samples it claimed, and is told
plainly what was forgotten and what was kept.

Fully local (`requires_network="no"`); embedding + match are CPU-only,
DB writes go to the same Postgres the rest of the system uses.
"""

from __future__ import annotations

import base64
import logging
import re
import time
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
from domovoi.log_privacy import said
from domovoi import person_names
from domovoi import voice_history
from domovoi.models import Context, Intent, Response, is_phone_room
from domovoi.person_names import read_name_reply, sound_alike_people, sounds_like_name, spell_out
from domovoi.voice_identifier import claims_by_this_voice, is_own_voice

log = logging.getLogger(__name__)


def phone_chat_mic(ctx: Context) -> bool:
    """Whether this turn's voice may go into a person's PHONE samples: a
    push-to-talk turn from the app's chat mic (domovoi/phone_voice.py sets
    all three). Nothing else may. In particular a future "phone as a
    satellite" mode — whose turns would come through a satellite's stream,
    not the chat mic — must neither create nor extend phone profiles
    (owner decision 2026-10-12): it must not set ``device_kind="phone"``."""
    return (
        ctx.device_kind == "phone"
        and is_phone_room(ctx.room_id)
        and ctx.trigger == "push_to_talk"
    )


#: How long "Add this phone's voice to X?" (and a satellite's "I already
#: know someone called X. Is that you?") stays answerable, in seconds: as
#: long as the other follow-up, "did you mean …?" (handlers/music_choice.py
#: CHOICE_TTL_SEC).
VOICE_ADD_TTL_SEC = 60.0

#: How long "… Did I get that right?" (the name of a new person, read back
#: letter by letter) stays answerable, in seconds. It is also a CHOICE: only
#: the very next turn answers it, and a turn about something else drops it
#: (handle_choice_reply), so a stray "yes" minutes later can never save a
#: misheard name. The clock is the backstop for the one case the next-turn
#: rule can't see: a core restarted between the question and the answer
#: forgets which sessions have a choice waiting, and a later yes/no would
#: otherwise still find the question parked. Two minutes rather than the
#: one of the other follow-ups: spelling a name out, or being asked to, takes
#: longer than a yes.
SELF_INTRO_TTL_SEC = 120.0

#: The stages of the self-introduction question (``stage`` in its payload):
#: "Nice to meet you, X. … Did I get that right?" and, after a "no" or a
#: correction that couldn't be read, "What's your name?".
_STAGE_CONFIRM = "confirm"
_STAGE_ASK_NAME = "ask_name"

#: The keys a parked enrolment question carries about where it was asked.
_ORIGIN_KEY = "origin"
_ORIGIN_ROOM_KEY = "origin_room"
#: Said when an answer comes from somewhere other than the question did.
_ELSEWHERE_TEXT = "I asked that somewhere else, so I won't take the answer here."


def _origin(ctx: Context) -> dict:
    """Where an enrolment question is asked: the chat mic of one phone, or
    anywhere else (a satellite, /v1/intent). Parked with the question, so
    the voice it saves goes into the set it was HEARD by."""
    return {
        _ORIGIN_KEY: "phone" if phone_chat_mic(ctx) else "satellite",
        _ORIGIN_ROOM_KEY: ctx.room_id,
    }


def _asked_on_the_phone(data: dict) -> bool:
    return data.get(_ORIGIN_KEY) == "phone"


def _answered_where_asked(data: dict, ctx: Context) -> bool:
    """A yes counts only from where the question was asked: the same
    phone's chat mic for a phone question (a /v1/intent "yes" carrying the
    phone's session id would otherwise file a phone clip as a satellite
    sample), and anything but a chat mic for the rest. A question parked
    before this rule says nothing and is a satellite's."""
    if _asked_on_the_phone(data):
        return phone_chat_mic(ctx) and data.get(_ORIGIN_ROOM_KEY) == ctx.room_id
    return not phone_chat_mic(ctx)


#: What an admin does about two people with one name, said at the end of
#: every answer that won't guess between them.
_SORT_IT_OUT = "An admin can rename or remove one of them in Settings, under People."


def _more_than_one(name: str) -> str:
    return (
        f"There's more than one {name}, so I won't guess which one is you. "
        f"{_SORT_IT_OUT} Then tell me your name again."
    )


def _same_name(a: str, b: str) -> bool:
    """``find_by_name``'s notion of one name, for names already read."""
    return a.strip().lower() == b.strip().lower()


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

# A self-intro spelled letter by letter: "my name is J. O. R. D. Y. N."
# (the plain form above takes at most two words, and "J-O-R-D-Y-N" as one).
_SELF_INTRO_SPELLED_RE = re.compile(
    r"^(?:my name(?:'s| is)|call me|i(?:'m| am)) (?P<name>[a-z](?:[\s.,\-]+[a-z]){2,})$"
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

# The LLM router is offered this handler's tool (whose actions include
# "forget") on a turn that mentions forgetting only when the turn asks to
# forget the SPEAKER — "forget about it", "forget that", "forget the
# timer" never reach it (review 2026-10-17, N-V4). Every "forget me" turn
# is asked about before anything is deleted anyway.
_FORGET_TALK_RE = re.compile(r"\bforget\b")
_FORGET_ME_TALK_RE = re.compile(
    r"\bforget (?:me|my voice|my profile|(?:all |everything )?about me|who i am)\b"
)

#: How long "forget me"'s question waits for its yes.
FORGET_CONFIRM_TTL_SEC = VOICE_ADD_TTL_SEC
#: What a "forget me" from the person's own voice asks before deleting.
_FORGET_ASK = (
    "{name}, I'll forget your voice, your profile, what I remember about you "
    "and everything you've said to me. Say yes to delete everything you've said."
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


#: The key a parked enrolment question keeps who the voice was recognized
#: as when it said the name (V029's ``claimed_by_person_id``).
_CLAIMED_BY_KEY = "claimed_by"


def _confirmation_payload(
    name: str, embedding: np.ndarray, kind: str, claimed_by: int | None = None,
) -> dict:
    return {
        "kind": kind,
        "name": name,
        "embedding_b64": _b64_embedding(embedding),
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        _CLAIMED_BY_KEY: claimed_by,
    }


def _claimed_by(data: dict) -> int | None:
    try:
        value = data.get(_CLAIMED_BY_KEY)
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _intro_expired(data: dict) -> bool:
    """Whether a parked self-introduction is too old to answer: past its
    ``expires_at``, or (a question parked before 2026-11-11, which had
    none) more than :data:`SELF_INTRO_TTL_SEC` after its ``created_at``. A
    payload with neither (written by hand) never expires."""
    expires_at = data.get("expires_at")
    if expires_at is not None:
        try:
            return time.time() > float(expires_at)
        except (TypeError, ValueError):
            return True
    created_at = data.get("created_at")
    if not created_at:
        return False
    try:
        created = datetime.fromisoformat(str(created_at))
    except ValueError:
        return True
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(tz=timezone.utc) - created).total_seconds() > SELF_INTRO_TTL_SEC


def _intro_question(name: str) -> str:
    """The first read-back of a new person's name: said, then spelled —
    "Jordan" and "Jordyn" sound the same, so only the letters show a
    misspelling (2026-11-11)."""
    return f"Nice to meet you, {name}. That's {spell_out(name)}. Did I get that right?"


def _corrected_question(name: str) -> str:
    """The read-back after a correction or a spelling."""
    return f"Got it, {name}: {spell_out(name)}. Is that right?"


#: Said after a plain "no" to the read-back.
_ASK_NAME_TEXT = "Sorry about that. What's your name? You can spell it if you like."
#: Said after a correction whose name couldn't be read ("yes, but it's
#: spelled wrong"), or a "no" to a name that sounded the same.
_ASK_SPELLING_TEXT = "Sorry, how do you spell it? Say the letters one at a time."
#: Said when the reply comes after the question's time ran out.
_INTRO_EXPIRED_TEXT = (
    "That was a while ago, so I didn't save it. Tell me your name again if you'd like me to."
)
_WONT_SAVE_TEXT = "Okay, I won't save that."


def _not_a_command(name: str) -> bool:
    """A lone word given as a name ("no, Jordyn") that no fast path takes
    as a command ("no, pause" is not somebody called Pause)."""
    from domovoi.router import first_fast_path

    return first_fast_path(name.lower()) is None


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
            idx, conf, said(candidate_text[:80]),
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
                **_origin(ctx),
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
    example_phrases = (
        "my name is Sarah",
        "who am I",
        "forget me",
        "never save my voice",
    )
    confirmation_kinds = (
        "core.self_intro", "core.third_party_intro_confirm", "core.phone_voice_add",
        "core.room_voice_add", "core.forget_me", "core.name_sound_alike",
    )
    # "Add this phone's voice to X?" and a satellite's "I already know
    # someone called X. Is that you?" are answered by the very next turn or
    # not at all (handle_choice_reply): choices, so the router hands them
    # every next turn, and drops them when that turn is about something
    # else.
    # "forget me"'s "Say yes to delete everything you've said" is one too:
    # the very next turn answers it, or nothing does.
    # So, since 2026-11-11, are "Nice to meet you, X. … Did I get that
    # right?" (its reply may be a correction or a spelling, not only a
    # yes/no) and "Did you mean X?" (a heard name that sounds like someone
    # the house knows).
    choice_kinds = (
        "core.phone_voice_add", "core.room_voice_add", "core.forget_me",
        "core.self_intro", "core.name_sound_alike",
    )
    requires_network = "no"

    def offers_tool(self, transcript: str) -> bool:
        """Withheld from a turn that talks about forgetting something other
        than the speaker ("forget about it", "forget that"): a misroute
        there could only reach the tool's "forget". Everything else keeps
        the tool (introductions and "who am I" phrasings the fast paths
        miss)."""
        t = transcript or ""
        return not (_FORGET_TALK_RE.search(t) and not _FORGET_ME_TALK_RE.search(t))

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
            FastPath(_SELF_INTRO_SPELLED_RE, VoiceProfileHandler._self_intro_from_match),
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
            raw = args.get("name") or ""
            letters = person_names.letters_of(raw)
            if letters:
                # "my name is S A M …": the tool model passes the letters on.
                return await self._start_self_intro(
                    person_names.name_from_letters(letters), ctx, session, spelled=True,
                )
            name = _clean_name(raw)
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
            if not _answered_where_asked(data, ctx):
                return Response(text=_ELSEWHERE_TEXT, session_id=ctx.session_id, matched_handler=self.name)
            if not affirmative:
                return Response(
                    text="Got it — I won't save that.",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
            if _intro_expired(data):
                return Response(
                    text=_INTRO_EXPIRED_TEXT, session_id=ctx.session_id, matched_handler=self.name,
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
            return await self._enroll(
                name, embedding, ctx, session, phone=_asked_on_the_phone(data),
                room_id=data.get(_ORIGIN_ROOM_KEY), claimed_by=_claimed_by(data),
            )
        if kind == "core.third_party_intro_confirm":
            return await self._handle_third_party_confirm(
                data, affirmative, ctx, session
            )
        if kind == "core.phone_voice_add":
            return await self._handle_phone_voice_add(data, affirmative, ctx, session)
        if kind == "core.room_voice_add":
            return await self._handle_room_voice_add(data, affirmative, ctx, session)
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

        if not _answered_where_asked(data, ctx):
            return Response(text=_ELSEWHERE_TEXT, session_id=ctx.session_id, matched_handler=self.name)
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
        # The introducer vouches for the name: if someone already has it,
        # the cluster is added as THEIR claim (V029), by the introducer.
        response = await self._enroll(
            name, embedding, ctx, session, phone=_asked_on_the_phone(data),
            room_id=data.get(_ORIGIN_ROOM_KEY), claimed_by=ctx.person_id,
        )
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
        letters = person_names.letters_of(raw_name)
        if letters and len(letters) > 2:
            # "my name is J-O-R-D-Y-N": spelled in the introduction itself.
            return await self._start_self_intro(
                person_names.name_from_letters(letters), ctx, session, spelled=True,
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
        # "Meet Jordyn" where the house knows a Jordan (and nobody called
        # Jordyn): the introduction is of Jordan, misheard — never the start
        # of a second person whose name differs by a letter. The answer says
        # the name it took, so a real Jordyn is one rename away.
        name = await self._known_spelling(name, session)

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

    # ─── Names that sound like someone the house knows ────────────────
    async def _sound_alikes(self, name: str, session: AsyncSession) -> list[tuple[int, str]]:
        """Household members whose name sounds like ``name`` without being
        it (:func:`person_names.sound_alike_people`); [] on a read error."""
        try:
            people = await PeopleRepository(session).names()
        except Exception as e:
            log.warning("reading the household's names failed: %s", e)
            return []
        return sound_alike_people(name, people)

    async def _known_spelling(self, name: str, session: AsyncSession) -> str:
        """``name``, or the one household name it sounds like when nobody is
        called ``name`` itself and exactly one name sounds like it."""
        try:
            if await PeopleRepository(session).find_by_name(name):
                return name
        except Exception as e:
            log.warning("PeopleRepository.find_by_name failed: %s", e)
            return name
        alikes = await self._sound_alikes(name, session)
        spellings = {" ".join(n.split()).lower() for _pid, n in alikes}
        if len(spellings) == 1:
            known = alikes[0][1]
            log.info("heard name %r taken as the household's %r", name, known)
            return known
        return name

    # ─── Core actions ─────────────────────────────────────────────────
    async def _start_self_intro(
        self, name: str, ctx: Context, session: AsyncSession, *, spelled: bool = False,
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

        embedding = np.frombuffer(ctx.embedding_bytes, dtype=np.float32)
        return await self._propose_name(
            name, embedding, ctx, session, claimed_by=ctx.person_id, first=True,
            spelled=spelled,
        )

    async def _propose_name(
        self,
        name: str,
        embedding: np.ndarray,
        ctx: Context,
        session: AsyncSession,
        *,
        claimed_by: int | None,
        first: bool,
        spelled: bool = False,
        check_sound_alike: bool = True,
    ) -> Response:
        """A name for this voice — from "my name is X", or a correction or
        spelling given in reply to the read-back — met against the people
        the house knows, in this order:

        * the voice was recognized as somebody (``claimed_by``) whose name
          this is, or sounds like: "I already know you, X." Nothing saved.
          Spelled out differently from that person's name: "I know you as
          X …" — the saved spelling is an admin's to change (People).
        * someone (exactly) has the name: "Add this phone's voice to X?" /
          "I already know someone called X. Is that you?"; two such
          people: the refusal (:func:`_more_than_one`).
        * a name nobody has but that sounds like one household name: "Did
          you mean X?" (``core.name_sound_alike``) — never a second,
          misheard person. Asked once: not again after its "no".
        * otherwise the read-back, with the letters: "Nice to meet you, X.
          That's X, Y, Z. Did I get that right?" (``first``), or "Got it,
          X: …. Is that right?" after a correction."""
        if claimed_by is not None:
            # Already enrolled with that name? Skip the confirmation dance.
            try:
                existing = await PeopleRepository(session).get(claimed_by)
            except Exception as e:
                log.warning("PeopleRepository.get failed: %s", e)
                existing = None
            if existing is not None and _same_name(existing[1], name):
                return self._say(f"I already know you, {existing[1]}.", ctx)
            if existing is not None and sounds_like_name(name, existing[1]):
                if not spelled:
                    # "my name is Jordyn" in Jordan's voice: Jordan, misheard.
                    return self._say(f"I already know you, {existing[1]}.", ctx)
                # Spelled out, and not how the house spells this voice's
                # name: the saved spelling may be the wrong one. Never a
                # second person with the same voice.
                return self._say(
                    f"I know you as {existing[1]}. If that's spelled wrong, an admin "
                    "can change it in Settings, under People.",
                    ctx,
                )

        # A name someone already has is that person asking to be known by
        # this voice too — from the chat mic or at a satellite — never a
        # second person with the name.
        try:
            known = await PeopleRepository(session).find_by_name(name)
        except Exception as e:
            # Never guess "new person" here: that is the duplicate.
            log.warning("PeopleRepository.find_by_name failed: %s", e)
            return Response(
                text="Something went wrong there — try saying your name again.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if len(known) > 1:
            # Two people share the name (a parent and child, say, or a
            # twin enrolled before this rule): never guess whose voice
            # this is.
            return Response(
                text=_more_than_one(name), session_id=ctx.session_id, matched_handler=self.name,
            )
        if known:
            if phone_chat_mic(ctx):
                return await self._offer_phone_voice(
                    known[0], embedding, ctx, session, claimed_by=claimed_by,
                )
            return await self._offer_room_voice(
                known[0], embedding, ctx, session, claimed_by=claimed_by,
            )
        if check_sound_alike:
            alikes = await self._sound_alikes(name, session)
            if len(alikes) == 1:
                return await self._offer_sound_alike(
                    alikes[0], name, embedding, ctx, session, claimed_by=claimed_by,
                )
        return await self._park_intro(
            name, embedding, ctx, session, claimed_by=claimed_by,
            text=_intro_question(name) if first else _corrected_question(name),
            stage=_STAGE_CONFIRM,
        )

    async def _park_intro(
        self,
        name: str,
        embedding: np.ndarray,
        ctx: Context,
        session: AsyncSession,
        *,
        claimed_by: int | None,
        text: str,
        stage: str,
        heard: str | None = None,
    ) -> Response:
        """Park the self-introduction question (a choice: the next turn
        answers it, within :data:`SELF_INTRO_TTL_SEC`) and ask ``text``.
        ``stage``: :data:`_STAGE_CONFIRM` (``name`` is read back) or
        :data:`_STAGE_ASK_NAME` (no name yet; ``heard`` is the one that was
        wrong). The voice parked is the introduction's, kept through every
        correction: it is the clip of the person saying their name."""
        try:
            await request_confirmation(
                session,
                ctx.session_id,
                kind="core.self_intro",
                handler=self.name,
                data={
                    **_confirmation_payload(
                        name, embedding, "core.self_intro", claimed_by=claimed_by,
                    ),
                    **_origin(ctx),
                    "stage": stage,
                    "heard": heard,
                    "expires_at": time.time() + SELF_INTRO_TTL_SEC,
                },
            )
        except Exception as e:
            log.warning("couldn't park pending_confirmation: %s", e)
            return Response(
                text=f"Hi {name}, nice to meet you." if name else "Something went wrong there — try saying your name again.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        return Response(
            text=text,
            session_id=ctx.session_id,
            matched_handler=self.name,
            # Tell the Pi to skip the wake-word gate for the user's
            # answer. Pairs with the question parked above — the router
            # hands the next turn to `handle_choice_reply` — but without
            # this flag the user would have to re-engage with the wake
            # word first.
            expect_followup=True,
        )

    async def _offer_sound_alike(
        self,
        known: tuple[int, str],
        heard: str,
        embedding: np.ndarray,
        ctx: Context,
        session: AsyncSession,
        *,
        claimed_by: int | None,
    ) -> Response:
        """ "my name is Jordyn" where nobody is called Jordyn but Jordan is
        known: "Did you mean Jordan?" — the name was most likely misheard
        (Whisper has no idea how the household spells its names). Yes: the
        clip joins Jordan's samples as a claim (like "I already know someone
        called X. Is that you?"); no: Jordyn is read back as a new person.
        The very next turn only, within :data:`VOICE_ADD_TTL_SEC`."""
        person_id, name = known
        payload = {
            **_confirmation_payload(
                name, embedding, "core.name_sound_alike", claimed_by=claimed_by,
            ),
            **_origin(ctx),
            "person_id": int(person_id),
            "heard": heard,
            "expires_at": time.time() + VOICE_ADD_TTL_SEC,
        }
        try:
            await request_confirmation(
                session,
                ctx.session_id,
                kind="core.name_sound_alike",
                handler=self.name,
                data=payload,
            )
        except Exception as e:
            log.warning("couldn't park the sound-alike question: %s", e)
            return Response(
                text="Something went wrong there — try saying your name again.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        log.info("heard name %r sounds like person_id=%d %r: asking", said(heard), person_id, name)
        return Response(
            text=f"Did you mean {name}? I already know someone called {name}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            expect_followup=True,
        )

    # ─── Phone voice (the chat mic) ───────────────────────────────────
    async def _offer_phone_voice(
        self,
        known: tuple[int, str],
        embedding: np.ndarray,
        ctx: Context,
        session: AsyncSession,
        *,
        claimed_by: int | None,
    ) -> Response:
        """ "my name is X" from the chat mic, for an X who exists: ask
        before adding this clip to X's phone samples. Only the NEXT press
        in the thread answers it, within :data:`VOICE_ADD_TTL_SEC`
        (it is a choice kind, see :meth:`handle_choice_reply`): a turn
        about anything else drops the question."""
        person_id, name = known
        payload = {
            **_confirmation_payload(
                name, embedding, "core.phone_voice_add", claimed_by=claimed_by,
            ),
            **_origin(ctx),
            "person_id": int(person_id),
            "expires_at": time.time() + VOICE_ADD_TTL_SEC,
        }
        try:
            await request_confirmation(
                session,
                ctx.session_id,
                kind="core.phone_voice_add",
                handler=self.name,
                data=payload,
            )
        except Exception as e:
            log.warning("couldn't park the phone voice offer: %s", e)
            return Response(
                text="Something went wrong there — try saying your name again.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return Response(
            text=f"Add this phone's voice to {name}?",
            session_id=ctx.session_id,
            matched_handler=self.name,
            expect_followup=True,
        )

    # ─── A satellite's voice for someone known ────────────────────────
    async def _offer_room_voice(
        self,
        known: tuple[int, str],
        embedding: np.ndarray,
        ctx: Context,
        session: AsyncSession,
        *,
        claimed_by: int | None,
    ) -> Response:
        """ "my name is X" at a satellite, for an X who exists but isn't
        who this voice was matched as (a room they were never heard in, a
        voice that drifted, or a mix-up with somebody else): ask before
        adding this clip to X's SATELLITE samples for this room — the
        phone's "Add this phone's voice to X?", in the room's words; a no
        enrols a second X on purpose (:meth:`_enroll_namesake`), never
        silently. Answered by the very next turn only, within
        :data:`VOICE_ADD_TTL_SEC` (a choice kind)."""
        person_id, name = known
        payload = {
            **_confirmation_payload(
                name, embedding, "core.room_voice_add", claimed_by=claimed_by,
            ),
            **_origin(ctx),
            "person_id": int(person_id),
            "expires_at": time.time() + VOICE_ADD_TTL_SEC,
        }
        try:
            await request_confirmation(
                session,
                ctx.session_id,
                kind="core.room_voice_add",
                handler=self.name,
                data=payload,
            )
        except Exception as e:
            log.warning("couldn't park the room voice offer: %s", e)
            return Response(
                text="Something went wrong there — try saying your name again.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return Response(
            text=f"I already know someone called {name}. Is that you?",
            session_id=ctx.session_id,
            matched_handler=self.name,
            expect_followup=True,
        )

    async def _handle_room_voice_add(
        self,
        data: dict,
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        """The answer to "I already know someone called X. Is that you?"
        (owner decision 2026-10-15). Yes: it is that person — the clip from
        the question's turn goes into X's SATELLITE samples, tagged with the
        room it was asked in. No: a different X — a new person with the
        name, enrolled from the same clip right away, and the People page
        flags the pair (``namesakes``). Only from a satellite: a chat mic
        never writes a satellite sample."""
        name = str(data.get("name") or "")
        if _asked_on_the_phone(data) or not _answered_where_asked(data, ctx):
            return Response(text=_ELSEWHERE_TEXT, session_id=ctx.session_id, matched_handler=self.name)
        try:
            person_id = int(data.get("person_id"))
        except (TypeError, ValueError):
            person_id = None
        embedding = _decode_b64_embedding(str(data.get("embedding_b64") or ""))
        if person_id is None or embedding is None or not name:
            return Response(
                text="Something went wrong storing that — try saying your name again.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        room_id = data.get(_ORIGIN_ROOM_KEY) or ctx.room_id
        if not affirmative:
            return await self._enroll_namesake(name, embedding, room_id, ctx, session)
        return await self._add_room_sample(
            person_id, name, embedding, room_id, ctx, session, claimed_by=_claimed_by(data),
        )

    async def _enroll_namesake(
        self,
        name: str,
        embedding: np.ndarray,
        room_id: str | None,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        """The "no" to "I already know someone called X. Is that you?": a
        new person called X, on purpose, with this clip as their satellite
        sample for ``room_id``. The opt-out list first, under the name lock
        like every enrolment. From then on "my name is X" from a voice
        neither of them is matched as meets the two-namesakes refusal."""
        if await self._is_denylisted(embedding, session):
            return Response(
                text="I wasn't able to save that voice profile.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        people = PeopleRepository(session)
        try:
            await people.lock_names()
            others = await people.find_by_name(name)
            person_id = await people.create(name=name)
            await VoiceProfilesRepository(session).add(
                person_id=person_id,
                embedding=embedding.astype(np.float32).tobytes(),
                model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
                room_id=room_id,
                sample_seconds=None,
            )
        except Exception as e:
            log.warning("namesake enrollment failed for name=%s: %s", name, e)
            return Response(
                text=(
                    "Something went wrong saving your voice — your name "
                    "didn't get recorded."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        log.info("enrolled namesake person=%s id=%d (%d before)", name, person_id, len(others))
        if not others:
            # The other X was forgotten since the question: no pair to flag.
            text = f"Got it, {name}."
        else:
            text = (
                f"Okay, I've added you as a new {name}. An admin can give one of "
                "you a different name in Settings, under People."
            )
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name)

    async def _add_room_sample(
        self,
        person_id: int,
        name: str,
        embedding: np.ndarray,
        room_id: str | None,
        ctx: Context,
        session: AsyncSession,
        *,
        claimed_by: int | None = None,
    ) -> Response:
        """One clip into ``person_id``'s SATELLITE samples, tagged with
        ``room_id``: the opt-out list first, then the person re-read (they
        may have been forgotten since the question). Marked as claimed
        (V029): the speaker said the name, nothing showed it is their
        voice; ``claimed_by`` is who that voice was recognized as."""
        if await self._is_denylisted(embedding, session):
            return Response(
                text="I wasn't able to save that voice profile.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        try:
            person = await PeopleRepository(session).get(person_id)
        except Exception as e:
            log.warning("room voice add: PeopleRepository.get failed: %s", e)
            person = None
        if person is None:
            return Response(
                text=f"I can't find {name} any more, so I didn't save that.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        try:
            await VoiceProfilesRepository(session).add(
                person_id=person_id,
                embedding=embedding.astype(np.float32).tobytes(),
                model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
                room_id=room_id,
                sample_seconds=None,
                claimed=True,
                claim_room=room_id,
                claimed_by=claimed_by,
            )
        except Exception as e:
            log.warning("room voice add failed for person_id=%s: %s", person_id, e)
            return Response(
                text="Something went wrong saving that — your voice wasn't added.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        log.info("room voice sample added: person_id=%d in room=%s", person_id, room_id)
        return Response(
            text=f"Done — I'll know your voice in this room now, {person[1]}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def handle_choice_reply(
        self,
        kind: str,
        data: dict,
        transcript: str,
        ctx: Context,
        session: AsyncSession,
    ) -> Response | None:
        """The next turn after "Add this phone's voice to X?" or "I already
        know someone called X. Is that you?" (the router has already
        cleared the question). A yes or a no answers it, if it comes in
        time; anything else is not about it, so the question is dropped
        and the turn routed as usual (None) — "no, I'm X Smith" is then
        the self-introduction it reads as."""
        from domovoi.router import _parse_yes_no, strip_leading_filler

        if kind not in self.choice_kinds:
            return None
        if kind == "core.forget_me":
            return await self._handle_forget_reply(data, transcript, ctx, session)
        if kind == "core.self_intro":
            return await self._handle_intro_reply(data, transcript, ctx, session)
        if kind == "core.name_sound_alike":
            return await self._handle_sound_alike_reply(data, transcript, ctx, session)
        affirmative = _parse_yes_no(transcript)
        if affirmative is None:
            affirmative = _parse_yes_no(strip_leading_filler(transcript))
        if affirmative is None:
            return None
        try:
            expired = time.time() > float(data.get("expires_at"))
        except (TypeError, ValueError):
            expired = True
        if expired:
            return Response(
                text="That was a while ago, so I didn't add it. Say your name again if you'd like me to.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if kind == "core.room_voice_add":
            return await self._handle_room_voice_add(data, affirmative, ctx, session)
        return await self._handle_phone_voice_add(data, affirmative, ctx, session)

    # ─── Replies to the name read-back ────────────────────────────────
    @staticmethod
    def _read_reply(transcript: str) -> person_names.NameReply | None:
        """:func:`person_names.read_name_reply`, with a lone word that is
        one of the house's commands not taken as a name: "no, pause" is a
        no (and the pause is not lost to a person called Pause — the
        question is answered, as a plain yes/no always did)."""
        from domovoi.router import normalize_transcript

        reply = read_name_reply(normalize_transcript(transcript), not_a_name=_NAME_BLOCKLIST)
        if reply is not None and reply.kind == person_names.NAME and reply.bare:
            if not _not_a_command(reply.name or ""):
                if reply.polarity is None:
                    return None
                kind = person_names.YES if reply.polarity else person_names.NO
                return person_names.NameReply(kind, polarity=reply.polarity)
        return reply

    def _say(self, text: str, ctx: Context) -> Response:
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name)

    async def _ask_for_name(
        self, data: dict, ctx: Context, session: AsyncSession, *, spelling: bool,
    ) -> Response:
        """Ask for the name again — "What's your name?" after a plain no,
        "how do you spell it?" after a correction that couldn't be read —
        keeping the introduction's voice. A fresh question, with its own
        :data:`SELF_INTRO_TTL_SEC`."""
        embedding = _decode_b64_embedding(str(data.get("embedding_b64") or ""))
        if embedding is None:
            return self._say("Something went wrong storing that — try saying your name again.", ctx)
        heard = str(data.get("name") or data.get("heard") or "") or None
        return await self._park_intro(
            "", embedding, ctx, session, claimed_by=_claimed_by(data),
            text=_ASK_SPELLING_TEXT if spelling else _ASK_NAME_TEXT,
            stage=_STAGE_ASK_NAME, heard=heard,
        )

    async def _handle_intro_reply(
        self, data: dict, transcript: str, ctx: Context, session: AsyncSession,
    ) -> Response | None:
        """The turn after "Nice to meet you, X. That's X, Y, Z. Did I get
        that right?" (or "What's your name?"). The router has already
        cleared the question.

        * yes — X is saved (in time, from where it was asked);
        * a name — "no, it's Jordyn", "it's spelled J-O-R-D-Y-N", "yes, but
          it's J O R D Y N", "my name is Jordyn", a bare spelling: read back
          again, spelled ("Got it, Jordyn: J, O, R, D, Y, N. Is that
          right?"), or met against the household like any introduction
          (:meth:`_propose_name`). The same name as X with a yes, or
          spelled the same: saved. The same name after a NO: it sounded
          right and is spelled wrong, so "how do you spell it?";
        * "yes, but …" / a correction that couldn't be read: "how do you
          spell it?" — never a yes;
        * no: "What's your name?" (once; a no to that saves nothing);
        * "never mind" / "forget it": nothing saved;
        * anything else: not about the name — the question is dropped and
          the turn routed as usual (None)."""
        reply = self._read_reply(transcript)
        if reply is None:
            return None
        if not _answered_where_asked(data, ctx):
            return self._say(_ELSEWHERE_TEXT, ctx)
        stage = data.get("stage") or _STAGE_CONFIRM
        name = str(data.get("name") or "")
        if reply.kind == person_names.CANCEL:
            return self._say(_WONT_SAVE_TEXT, ctx)
        if reply.kind == person_names.NO:
            if stage == _STAGE_ASK_NAME or not name:
                return self._say(_WONT_SAVE_TEXT, ctx)
            if _intro_expired(data):
                return self._say(_INTRO_EXPIRED_TEXT, ctx)
            return await self._ask_for_name(data, ctx, session, spelling=False)
        if _intro_expired(data):
            return self._say(_INTRO_EXPIRED_TEXT, ctx)
        embedding = _decode_b64_embedding(str(data.get("embedding_b64") or ""))
        if embedding is None:
            return self._say("Something went wrong storing that — try saying your name again.", ctx)
        if reply.kind == person_names.UNCLEAR:
            return await self._ask_for_name(data, ctx, session, spelling=True)
        if reply.kind == person_names.YES:
            if stage == _STAGE_ASK_NAME or not name:
                return await self._ask_for_name(data, ctx, session, spelling=False)
            return await self._enroll(
                name, embedding, ctx, session, phone=_asked_on_the_phone(data),
                room_id=data.get(_ORIGIN_ROOM_KEY), claimed_by=_claimed_by(data),
            )
        # A name.
        new_name = reply.name or ""
        if name and _same_name(new_name, name) and stage == _STAGE_CONFIRM:
            if reply.polarity is False and not reply.spelled:
                # "No, it's Jordan" to "Jordan": the name sounded right and
                # the spelling is what's wrong.
                return await self._ask_for_name(data, ctx, session, spelling=True)
            return await self._enroll(
                name, embedding, ctx, session, phone=_asked_on_the_phone(data),
                room_id=data.get(_ORIGIN_ROOM_KEY), claimed_by=_claimed_by(data),
            )
        log.info(
            "self-introduction corrected: %r -> %r (%s)",
            name or data.get("heard"), new_name, "spelled" if reply.spelled else "said",
        )
        return await self._propose_name(
            new_name, embedding, ctx, session, claimed_by=_claimed_by(data), first=False,
            spelled=reply.spelled,
        )

    async def _handle_sound_alike_reply(
        self, data: dict, transcript: str, ctx: Context, session: AsyncSession,
    ) -> Response | None:
        """The turn after "Did you mean Jordan? I already know someone
        called Jordan." Yes (or "Jordan"): this voice joins Jordan's samples
        as a claim, like "I already know someone called X. Is that you?".
        No: the heard name is read back as a new person ("Nice to meet you,
        Jordyn. That's …"). A different name or a spelling: met like any
        correction, without asking about sound-alikes again. Anything else:
        the question is dropped (None)."""
        reply = self._read_reply(transcript)
        if reply is None:
            return None
        if not _answered_where_asked(data, ctx):
            return self._say(_ELSEWHERE_TEXT, ctx)
        if reply.kind == person_names.CANCEL:
            return self._say(_WONT_SAVE_TEXT, ctx)
        try:
            expired = time.time() > float(data.get("expires_at"))
        except (TypeError, ValueError):
            expired = True
        if expired:
            return self._say(_INTRO_EXPIRED_TEXT, ctx)
        known = str(data.get("name") or "")
        heard = str(data.get("heard") or "")
        embedding = _decode_b64_embedding(str(data.get("embedding_b64") or ""))
        try:
            person_id = int(data.get("person_id"))
        except (TypeError, ValueError):
            person_id = None
        if embedding is None or person_id is None or not known:
            return self._say("Something went wrong storing that — try saying your name again.", ctx)
        claimed_by = _claimed_by(data)
        is_known = reply.kind == person_names.YES or (
            reply.kind == person_names.NAME and _same_name(reply.name or "", known)
        )
        if is_known:
            if _asked_on_the_phone(data):
                return await self._add_phone_sample(
                    person_id, known, embedding, ctx, session, claimed_by=claimed_by,
                )
            return await self._add_room_sample(
                person_id, known, embedding, data.get(_ORIGIN_ROOM_KEY) or ctx.room_id,
                ctx, session, claimed_by=claimed_by,
            )
        if reply.kind == person_names.UNCLEAR:
            return await self._ask_for_name(
                {**data, "name": "", "heard": heard}, ctx, session, spelling=True,
            )
        if reply.kind == person_names.NO:
            if not heard:
                return await self._ask_for_name({**data, "name": ""}, ctx, session, spelling=False)
            return await self._propose_name(
                heard, embedding, ctx, session, claimed_by=claimed_by, first=True,
                check_sound_alike=False,
            )
        return await self._propose_name(
            reply.name or "", embedding, ctx, session, claimed_by=claimed_by, first=False,
            spelled=reply.spelled, check_sound_alike=False,
        )

    async def _handle_phone_voice_add(
        self,
        data: dict,
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        """The answer to "Add this phone's voice to X?". No changes
        nothing. Yes adds the clip from the question's turn to X's PHONE
        samples — never to their satellite samples, never a new person —
        if the answer came from the same phone's chat mic."""
        name = str(data.get("name") or "")
        if not _asked_on_the_phone(data) or not _answered_where_asked(data, ctx):
            # The question is only ever asked on a phone's own session;
            # nothing else may write a phone sample.
            return Response(text=_ELSEWHERE_TEXT, session_id=ctx.session_id, matched_handler=self.name)
        if not affirmative:
            return Response(
                text="Okay, I won't add it.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        try:
            person_id = int(data.get("person_id"))
        except (TypeError, ValueError):
            person_id = None
        embedding = _decode_b64_embedding(str(data.get("embedding_b64") or ""))
        if person_id is None or embedding is None or not name:
            return Response(
                text="Something went wrong storing that — try saying your name again.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return await self._add_phone_sample(
            person_id, name, embedding, ctx, session, claimed_by=_claimed_by(data),
        )

    async def _add_phone_sample(
        self,
        person_id: int,
        name: str,
        embedding: np.ndarray,
        ctx: Context,
        session: AsyncSession,
        *,
        claimed_by: int | None = None,
    ) -> Response:
        """One clip into ``person_id``'s PHONE samples: the opt-out list
        first, the person re-read (they may have been forgotten since the
        question), then at most ``voice_profile_phone_sample_cap``. Marked
        as claimed (V029), like :meth:`_add_room_sample`."""
        if await self._is_denylisted(embedding, session):
            return Response(
                text="I wasn't able to save that voice profile.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        try:
            person = await PeopleRepository(session).get(person_id)
        except Exception as e:
            log.warning("phone voice add: PeopleRepository.get failed: %s", e)
            person = None
        if person is None:
            # Forgotten (or deleted by an admin) since the question.
            return Response(
                text=f"I can't find {name} any more, so I didn't save that.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        try:
            _new_id, dropped = await VoiceProfilesRepository(session).add_phone_sample(
                person_id=person_id,
                embedding=embedding.astype(np.float32).tobytes(),
                model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
                room_id=ctx.room_id,
                cap=settings.voice_profile_phone_sample_cap,
                claimed=True,
                claimed_by=claimed_by,
            )
        except Exception as e:
            log.warning("phone voice add failed for person_id=%s: %s", person_id, e)
            return Response(
                text="Something went wrong saving that — your voice wasn't added.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        log.info(
            "phone voice sample added: person_id=%d from %s (%d older dropped)",
            person_id, ctx.room_id, dropped,
        )
        return Response(
            text=f"Done — I'll know your voice on the phone now, {person[1]}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
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
        *,
        phone: bool = False,
        room_id: str | None = None,
        claimed_by: int | None = None,
    ) -> Response:
        """Save ``embedding`` as ``name``'s voice. ``phone``: the question
        was asked on a chat mic (its parked origin, never the answering
        turn's), so the voice goes into the PHONE set; otherwise into the
        SATELLITE set, tagged with ``room_id`` (where the question was
        asked; this turn's room when the question didn't say). Either way
        a name someone already has is not a new person."""
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
        room_id = room_id if room_id is not None else ctx.room_id
        # One name, one person, as "my name is" has it: a name someone has
        # (by now: the question may be a minute old) gets this voice as
        # their sample, since the yes just given was to "is this X?"; two
        # such people get a refusal; only a new name makes a new person.
        # Checked and written under the name lock, so two enrolments of one
        # new name at once make one person, not two.
        people = PeopleRepository(session)
        try:
            await people.lock_names()
            known = await people.find_by_name(name)
        except Exception as e:
            log.warning("PeopleRepository.find_by_name failed: %s", e)
            return Response(
                text="Something went wrong there — try the introduction again.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if len(known) > 1:
            return Response(
                text=_more_than_one(name), session_id=ctx.session_id, matched_handler=self.name,
            )
        if known:
            # Someone has the name by now: the clip joins them as a claim.
            if phone:
                return await self._add_phone_sample(
                    known[0][0], name, embedding, ctx, session, claimed_by=claimed_by,
                )
            return await self._add_room_sample(
                known[0][0], name, embedding, room_id, ctx, session, claimed_by=claimed_by,
            )
        try:
            voice_profiles = VoiceProfilesRepository(session)
            person_id = await people.create(name=name)
            if phone:
                # Enrolled from the chat mic: their first sample is a
                # phone sample, which satellites never match (V026).
                await voice_profiles.add_phone_sample(
                    person_id=person_id,
                    embedding=embedding.astype(np.float32).tobytes(),
                    model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
                    room_id=ctx.room_id,
                    cap=settings.voice_profile_phone_sample_cap,
                )
            else:
                await voice_profiles.add(
                    person_id=person_id,
                    embedding=embedding.astype(np.float32).tobytes(),
                    model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
                    room_id=room_id,
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

    async def _candidate_names(self, ctx: Context, session: AsyncSession) -> list[str]:
        """The names of the people this voice was too close to tell apart
        (``ctx.voice_candidates``), or [] when it wasn't, or one of them is
        gone or unreadable."""
        if len(ctx.voice_candidates) < 2:
            return []
        people = PeopleRepository(session)
        names: list[str] = []
        try:
            for pid in ctx.voice_candidates:
                row = await people.get(int(pid))
                if row is None:
                    return []
                names.append(row[1])
        except Exception as e:
            log.warning("whoami: reading the candidates failed: %s", e)
            return []
        return names

    async def _whoami(self, ctx: Context, session: AsyncSession) -> Response:
        if ctx.person_id is None:
            names = await self._candidate_names(ctx, session)
            if names and all(_same_name(n, names[0]) for n in names):
                # The duplicate-name case at its plainest: two Alexes with
                # Alex's voice, so neither can be named.
                text = (
                    f"Your voice matches more than one {names[0]}, so I can't tell "
                    f"which one you are. {_SORT_IT_OUT}"
                )
            elif names:
                text = (
                    "Your voice is close to two people I know, so I won't guess "
                    "which one you are. Tell me your name, and I'll learn your voice better."
                )
            else:
                text = "I don't recognize your voice yet."
            return Response(text=text, session_id=ctx.session_id, matched_handler=self.name)
        people = PeopleRepository(session)
        try:
            row = await people.get(ctx.person_id)
            namesakes = await people.find_by_name(row[1]) if row is not None else []
        except Exception as e:
            log.warning("whoami: PeopleRepository.get failed: %s", e)
            row, namesakes = None, []
        if row is None:
            return Response(
                text="I don't recognize your voice yet.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        text = f"You're {row[1]}."
        if len(namesakes) > 1:
            # The voice named one person, not a guess; but whoever asks
            # can't tell which of the two that is.
            text += f" I know more than one {row[1]}, though. {_SORT_IT_OUT}"
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name)

    async def _forget(self, ctx: Context, session: AsyncSession) -> Response:
        if ctx.person_id is None:
            if await self._candidate_names(ctx, session):
                # Somebody known, but not one person: never guess whose
                # profile to delete.
                return Response(
                    text=(
                        "Your voice is close to two people I know, so I won't guess "
                        "whose voice to forget. An admin can remove a person in Settings, "
                        "under People."
                    ),
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
            return Response(
                text="I don't have a voice profile for you to forget.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        removed = 0
        try:
            row = await PeopleRepository(session).get(ctx.person_id)
            name = row[1] if row else "you"
            scope, claim_ids = await self._forget_scope(ctx, session)
            if scope == "claims":
                removed = await VoiceProfilesRepository(session).delete_samples(
                    ctx.person_id, claim_ids,
                )
        except Exception as e:
            log.warning("forget-me delete failed: %s", e)
            return Response(
                text="Something went wrong — your profile is still there.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if scope != "person":
            return self._forget_claims_answer(ctx, scope, name, removed)
        return await self._ask_forget(ctx.person_id, name, ctx, session)

    async def _ask_forget(
        self, person_id: int, name: str, ctx: Context, session: AsyncSession,
    ) -> Response:
        """The person's own voice said "forget me" (or the tool router sent
        a turn here): nothing is deleted yet (review 2026-10-17, N-V4). The
        question is parked as a choice — the very next turn in the same
        room or on the same phone, within :data:`FORGET_CONFIRM_TTL_SEC`,
        says yes, or nothing happens."""
        data = {
            **_origin(ctx),
            "person_id": int(person_id),
            "name": name,
            "expires_at": time.time() + FORGET_CONFIRM_TTL_SEC,
        }
        try:
            await request_confirmation(
                session, ctx.session_id, kind="core.forget_me", handler=self.name, data=data,
            )
        except Exception as e:
            log.warning("couldn't park the forget-me question: %s", e)
            return Response(
                text="Something went wrong — I haven't forgotten anything.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return Response(
            text=_FORGET_ASK.format(name=name),
            session_id=ctx.session_id,
            matched_handler=self.name,
            expect_followup=True,
        )

    async def _handle_forget_reply(
        self, data: dict, transcript: str, ctx: Context, session: AsyncSession,
    ) -> Response | None:
        """The turn after "Say yes to delete everything you've said". Yes,
        in time, from where it was asked, and not in somebody else's
        recognized voice: the person goes (:meth:`_forget_person`). No: kept.
        Anything else is not an answer: the question is dropped and the
        turn routed as usual (None)."""
        from domovoi.router import _parse_yes_no, strip_leading_filler

        affirmative = _parse_yes_no(transcript)
        if affirmative is None:
            affirmative = _parse_yes_no(strip_leading_filler(transcript))
        if affirmative is None:
            return None
        name = str(data.get("name") or "you")
        if not affirmative:
            return Response(
                text=f"Okay, I've kept everything, {name}.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        try:
            expired = time.time() > float(data.get("expires_at"))
        except (TypeError, ValueError):
            expired = True
        if expired:
            return Response(
                text="That was a while ago, so I haven't forgotten anything. Say forget me again if you'd like me to.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        same_place = (
            data.get(_ORIGIN_ROOM_KEY) == ctx.room_id
            and _asked_on_the_phone(data) == phone_chat_mic(ctx)
        )
        if not same_place:
            return Response(text=_ELSEWHERE_TEXT, session_id=ctx.session_id, matched_handler=self.name)
        try:
            person_id = int(data.get("person_id"))
        except (TypeError, ValueError):
            return Response(
                text="Something went wrong — I haven't forgotten anything.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if ctx.person_id is not None and ctx.person_id != person_id:
            # A "yes" in somebody else's recognized voice forgets nobody.
            return Response(
                text=f"That doesn't sound like {name}, so I haven't forgotten anything.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return await self._forget_person(person_id, name, ctx, session)

    async def _forget_person(
        self, person_id: int, name: str, ctx: Context, session: AsyncSession,
    ) -> Response:
        """Delete the person and what they said, after their yes."""
        try:
            if await PeopleRepository(session).get(person_id) is None:
                return Response(
                    text=f"I've already forgotten {name}.",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
            # What they said goes too (owner, 2026-10-17): their own spoken
            # turns, found by who voice identification heard speaking,
            # BEFORE the person row (whose delete sets the turns'
            # person_id to NULL). domovoi/voice_history.py.
            await voice_history.delete_person_history(session, person_id)
            await PeopleRepository(session).delete(person_id)
        except Exception as e:
            log.warning("forget-me delete failed: %s", e)
            return Response(
                text="Something went wrong — your profile is still there.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        log.info("forgot person id=%d name=%s", person_id, name)
        # The `people` row is gone, but we're still inside the router's
        # session_scope — `_persist_turn` has yet to write this turn's
        # intents_log / conversation_log rows, and it stamps them from
        # ctx.person_id. Leaving the stale id there inserts an audit row
        # referencing a person that no longer exists, which raises a
        # ForeignKeyViolationError and rolls the whole transaction back —
        # un-deleting the profile the user just asked us to forget.
        # (The FKs' ON DELETE SET NULL only rewrites rows that already
        # existed at delete time; it can't save an insert made after it.)
        # Clear the identity for the remainder of this turn so the audit
        # rows are written with NULL, and drop to the anonymous presence
        # tier that `voice_identifier.identify` uses for an unmatched
        # speaker — which is exactly what this speaker now is.
        ctx.person_id = None
        ctx.presence_tier = "high"
        return Response(
            text=(
                f"Okay, I've forgotten you, {name}: your voice, your profile, "
                "everything I remembered about you and what you've said to me."
            ),
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _forget_scope(self, ctx: Context, session: AsyncSession) -> tuple[str, list[int]]:
        """What "forget me" may delete for this voice (delta review S1,
        owner decision 2026-10-16):

        * ``("person", [])`` — the person: their profile, every voice
          sample, memories, favorites. When none of their samples came from
          a name claim (every household before V029: nothing changes), or
          when this voice matches their OWN samples
          (:func:`voice_identifier.is_own_voice`).
        * ``("claims", ids)`` — only the claims this voice made
          (:func:`voice_identifier.claims_by_this_voice`), and what rooms
          learned through them.
        * ``("unverified", [])`` — nothing: the voice is not the person's own
          and claimed nothing of theirs that it matches.
        * ``("unheard", [])`` — nothing: the person has claimed samples and
          this turn has no voice to hold against them.
        """
        repo = VoiceProfilesRepository(session)
        samples = await repo.person_samples(ctx.person_id, repo.EMBEDDING_MODEL_TAG)
        if all(s.own for s in samples):
            return "person", []
        if ctx.embedding_bytes is None or len(ctx.embedding_bytes) != EMBEDDING_DIM * 4:
            return "unheard", []
        voice = np.frombuffer(ctx.embedding_bytes, dtype=np.float32)
        if is_own_voice(voice, samples, phone=phone_chat_mic(ctx)):
            return "person", []
        mine = claims_by_this_voice(voice, samples)
        if mine:
            return "claims", mine
        return "unverified", []

    def _forget_claims_answer(
        self, ctx: Context, scope: str, name: str, removed: int,
    ) -> Response:
        """The plain answer when "forget me" does not delete the person."""
        if scope == "claims":
            log.info(
                "forget me: removed %d claimed voice sample(s) from person_id=%d "
                "(the voice is not theirs); the person stays",
                removed, ctx.person_id,
            )
            # This voice is nobody now, for the rest of the turn too.
            ctx.person_id = None
            ctx.presence_tier = "high"
            text = (
                f"I've forgotten the voice you added to {name}. I kept {name}'s "
                f"profile and memories: your voice doesn't match the one I learned "
                f"for {name}, so only an admin can delete those, in Settings, under People."
            )
        elif scope == "unheard":
            text = (
                "I couldn't hear your voice well enough to check it's you, so I "
                "haven't forgotten anything. Try again, or an admin can remove a "
                "person in Settings, under People."
            )
        else:
            log.info(
                "forget me: the voice matches neither person_id=%d's own samples "
                "nor a claim it made; nothing removed", ctx.person_id,
            )
            text = (
                f"Your voice doesn't match the one I learned for {name}, so I "
                "haven't forgotten anything. An admin can remove a person in Settings, "
                "under People."
            )
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name)

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
