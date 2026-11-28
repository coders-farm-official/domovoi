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

At a satellite (2026-11-16) the first question is "Nice to meet you! How do
you spell that?" instead (the spelling given is the name), and once the
name is settled and the voice saved, the introduction asks the speaker to
count to ten, and maybe say one sentence, until it has heard about eight
seconds of speech; the one sample's embedding becomes all the clips'
together ("Hearing enough of a new voice" below). A room that nearly
recognizes someone it has no sample of asks "By the way, was that you, X?"
once a day (:func:`maybe_ask_room_check`).

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
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.clients.voice_embedder import (
    EMBEDDING_DIM,
    UNAVAILABLE_SPOKEN,
    voice_id_unavailable,
)
from domovoi import owner_easter_eggs, role_claims
from domovoi.config import settings
from domovoi.db.repositories import (
    PeopleRepository,
    SessionRepository,
    VoiceDenylistRepository,
    VoiceProfilesRepository,
)
from domovoi.confirmations import request_confirmation
from domovoi.dropin_common import starts_live_audio
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.handlers.shared.rooms import spoken_room
from domovoi.log_privacy import said
from domovoi import person_names
from domovoi import voice_history
from domovoi.models import Context, Intent, Response, is_phone_room
from domovoi.overheard import assistant_names
from domovoi.person_names import read_name_reply, sound_alike_people, sounds_like_name, spell_out
from domovoi.voice_identifier import (
    OUTCOME_DENYLIST,
    OUTCOME_NO_MATCH,
    OUTCOME_NOISY,
    ROOM_SAMPLE_GOOD_SEC,
    claims_by_this_voice,
    is_good_sample,
    is_own_voice,
    scores_in_room,
    unheard_text,
)

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

# An address to the assistant, before or after a short question: "Domovoi,
# who am I?", "who am I, Domo boy?" (Whisper's "Domovoi" said quickly),
# "hey Domo, where am I?". The names are the overheard guard's, so a new
# mis-hearing added there counts here too. Bare "domo" is fine HERE: the
# questions it hangs off are anchored at both ends.
_ADDRESS = (
    r"(?:(?:hey|hi|ok|okay)[,\s]+)?(?:"
    + "|".join([*assistant_names(settings.bot_name), "domo"])
    + r")"
)
# What Whisper puts between the parts: "who am I, Domo boy?", "who am I?
# Domovoi.", "who am I right now".
_SEP = r"[\s,.!?;:]+"
# Said after the question without changing it.
_QUALIFIER = r"(?:right now|now|today|again|then)"


def _addressed_question(core: str) -> re.Pattern[str]:
    """``core`` (an alternation, the whole turn on its own) with an optional
    leading address, then up to two trailing qualifiers or addresses —
    still anchored at both ends, so "who am I to judge" is not one."""
    return re.compile(
        rf"^(?:{_ADDRESS}{_SEP})?(?:{core})(?:{_SEP}(?:{_QUALIFIER}|{_ADDRESS})){{0,2}}$"
    )


# "Who am I (talking to)" / "what's my name" / "do you know me" / "who's
# talking". Not "my name is X": that is an introduction (_SELF_INTRO_RE).
_WHOAMI_RE = _addressed_question(
    r"who am i(?: talking to)?"
    r"|what(?:'s| is) my name"
    r"|do you (?:know|recognize) me"
    r"|do you know (?:who i am|my name)"
    r"|who(?:'s| is) (?:talking|speaking)(?: to you)?"
)

# "Where am I" / "which room am I in" / "what room is this": the room the
# turn came from.
_WHEREAMI_RE = _addressed_question(
    r"where am i"
    r"|(?:which|what) room am i in"
    r"|(?:which|what) room is this"
    r"|(?:which|what) room are you in"
)

# "Forget me" / "don't save my voice" / "forget my voice"
_FORGET_RE = re.compile(
    r"^(?:"
    r"forget me"
    r"|forget my voice"
    r"|don't (?:save|remember|keep) my voice"
    r"|delete my voice(?: profile)?"
    # "Forget everything about me" (case 15.12): the same question first,
    # never only the AI router's tool.
    r"|forget (?:all|everything) (?:about me|you know about me)"
    r"|forget who i am"
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
_FORGET_ASK_BODY = (
    "I'll forget your voice, your profile, what I remember about you "
    "and everything you've said to me. Say yes to delete everything you've said."
)
_FORGET_ASK = "{name}, " + _FORGET_ASK_BODY


def _forget_name(raw: object) -> str:
    """The person's name for the "forget me" replies, or "" when there is
    none to say (no row, an empty name). Each reply has its own no-name
    wording: the name slot is never filled with "you" or left empty
    ("you, I'll forget …", case 15.12)."""
    name = " ".join(str(raw or "").split())
    return "" if name.lower() in ("", "you") else name


def _forget_ask(name: str) -> str:
    return _FORGET_ASK.format(name=name) if name else _FORGET_ASK_BODY

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


def _egg_key(ctx: Context) -> str:
    """Where an easter egg must not repeat its last line: this conversation
    (or room)."""
    return str(ctx.session_id or ctx.room_id or "")


def _clean_name(raw: str) -> str | None:
    """Title-case a captured name, stripping trailing junk and rejecting
    obvious non-names. Returns None if the name fails the sniff test."""
    cleaned = raw.strip().rstrip(".,!?").lower()
    if cleaned in _NAME_BLOCKLIST:
        return None
    if role_claims.is_role_not_name(cleaned):
        # "the owner", "admin", "a parent": a role, never a person's name
        # (domovoi/role_claims.py; "The Owner" was enrolled, case 18.8).
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


#: "I'm the mom", "I'm the guest", "I'm the …": no name was said.
_ROLE_CLAIM_NAME_TEXT = 'I learn people by name. Say "my name is" and then your name.'

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


# ─── Hearing enough of a new voice (2026-11-16) ───────────────────────────
#
# One "my name is X" is about a second of speech, too little for a stable
# voiceprint: a household member's own score in rooms sat at 0.41-0.61
# against the 0.58 bar, and another member's voice scored nearly as close.
# So at a satellite the introduction keeps listening across a few turns
# until it has about INTRO_SPEECH_TARGET_SEC of SPEECH (as the embedder
# measures it: Context.voice_speech_sec), stopping as soon as it has
# enough:
#
#   "my name is X"  ->  "Nice to meet you! How do you spell that?"
#   (the spelling) ->  saved; "Last bit: count from one to ten for me, …"
#   (counting)      ->  "Thanks — one more: say 'Good morning Domovoi, …'"
#   (the sentence)  ->  "Thanks, X! I'll know your voice now."
#
# The voice is saved as ONE sample, at the moment today's flow saved it
# (the name settled): each further clip REPLACES that sample's embedding
# with the mean of every clip's unit-norm embedding, re-normalised. The
# matcher already scores a person against the mean of their unit-norm
# samples (voice_identifier._person_scores), so several samples from one
# introduction would score the same in that room — but they would outweigh
# the person's other rooms in their overall centroid, and break "one
# sample per room" (learning, the room check's cap, "forget me"'s claims).
# A clip that is too short, under music, or unlike the clips so far
# (somebody else answering: _resembles) adds nothing; walking away,
# "stop" or "never mind" leaves the sample as it is, never worse than the
# one-clip introduction. The phone's chat mic keeps the one-clip flow.

#: Seconds of speech the introduction collects before it stops asking.
INTRO_SPEECH_TARGET_SEC = 8.0

# A room sample with less speech than ROOM_SAMPLE_GOOD_SEC (4 s, defined in
# voice_identifier.py beside the room rule it also decides; 1.3.0's relearn
# clips are about a second) is "short": "my name is X" in X's own
# recognized voice, in a room with no sample of X at least this long, goes
# on to learn the voice there (VoiceProfileHandler._offer_learn_here)
# instead of stopping at "I already know you".
#: The payload key of a room-learning collection's room.
_LEARN_ROOM_KEY = "learn_room"

#: The introduction's stages after the name (``stage`` in its payload):
#: "How do you spell that?" (the name not yet settled), then, the voice
#: saved, counting to ten and one sentence.
_STAGE_SPELL = "spell"
_STAGE_COUNT = "count"
_STAGE_SENTENCE = "sentence"
_COLLECT_STAGES = (_STAGE_COUNT, _STAGE_SENTENCE)

_SPELL_TEXT = "Nice to meet you! How do you spell that?"
_COUNT_TEXT = "Last bit: count from one to ten for me, at your normal voice."


def _sentence_text(bot_name: str) -> str:
    return (
        f"Thanks — one more: say 'Good morning {bot_name}, what's on today?' "
        "like you would at breakfast."
    )


def _thanks_text(name: str) -> str:
    return f"Thanks, {name}! I'll know your voice now."


def _stopped_text(name: str) -> str:
    return f"Okay, I'll stop there. I'll still know your voice, {name}."


#: The payload key of the clips heard so far: ``[[embedding_b64, speech_sec], …]``.
_CLIPS_KEY = "clips"
#: A reply that ends the collecting ("never mind", "no" are read too).
_STOP_RE = re.compile(
    r"^(?:stop|stop it|stop that|please stop|that's enough|enough|i'm done|done|quit)$"
)


def _turn_clip(ctx: Context, *, expected: int | None, first: bool = False) -> list | None:
    """This turn's voice as a clip of the introduction, ``[b64, speech
    seconds]``, or None when it doesn't count: no voiceprint (too little
    speech), an opted-out voice, music under it, or a voice recognized as
    somebody other than ``expected`` (a different person answered). The
    introduction's own clip (``first``) is always kept, as it always was;
    under music its speech just isn't counted."""
    raw = ctx.embedding_bytes
    if raw is None or len(raw) != EMBEDDING_DIM * 4:
        return None
    secs = float(ctx.voice_speech_sec) if ctx.voice_speech_sec is not None else 0.0
    if first:
        return [base64.b64encode(raw).decode("ascii"), 0.0 if ctx.voice_outcome == OUTCOME_NOISY else secs]
    if ctx.voice_outcome in (OUTCOME_NOISY, OUTCOME_DENYLIST):
        return None
    if ctx.person_id is not None and ctx.person_id != expected:
        return None
    if ctx.voice_candidates and expected not in ctx.voice_candidates:
        return None
    return [base64.b64encode(raw).decode("ascii"), secs]


def _clips(data: dict) -> list[list]:
    """The clips a parked introduction carries (unreadable ones dropped)."""
    out: list[list] = []
    for item in data.get(_CLIPS_KEY) or []:
        try:
            b64, secs = item
            if _decode_b64_embedding(str(b64)) is not None:
                out.append([str(b64), max(0.0, float(secs))])
        except (TypeError, ValueError):
            continue
    return out


def _speech_of(clips: list[list]) -> float:
    return float(sum(secs for _b64, secs in clips))


def _combined(clips: list[list]) -> np.ndarray | None:
    """One embedding for the clips: each L2-normalised, averaged, and the
    mean normalised again. None when no clip is usable."""
    vectors = []
    for b64, _secs in clips:
        vec = _decode_b64_embedding(b64)
        if vec is None:
            continue
        norm = float(np.linalg.norm(vec))
        if norm:
            vectors.append(vec / norm)
    if not vectors:
        return None
    mean = np.mean(vectors, axis=0)
    norm = float(np.linalg.norm(mean))
    if not norm:
        return None
    return (mean / norm).astype(np.float32)


def _resembles(clips: list[list], clip: list) -> bool:
    """Whether ``clip`` sounds like the clips collected so far: its cosine to
    their mean is at least ``voice_profile_intro_clip_min_similarity``.
    Somebody else answering the spelling or counting must not be mixed into
    the person's voice; a clip that fails just doesn't count."""
    mean = _combined(clips)
    vec = _decode_b64_embedding(clip[0])
    if mean is None or vec is None:
        return mean is None and vec is not None
    norm = float(np.linalg.norm(vec))
    if not norm:
        return False
    similarity = float(np.dot(mean, vec / norm))
    if similarity < settings.voice_profile_intro_clip_min_similarity:
        log.info(
            "introduction: a clip unlike the voice so far (%.3f < %.2f) doesn't count",
            similarity, settings.voice_profile_intro_clip_min_similarity,
        )
        return False
    return True


def _collecting(data: dict) -> bool:
    """Whether a parked introduction collects more of the voice: one asked
    at a satellite that started with a clip (not the phone's chat mic,
    not one parked before 2026-11-16)."""
    return _CLIPS_KEY in data and not _asked_on_the_phone(data)


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


# ─── "Was that you?" — a room checks once (2026-11-16) ────────────────────
#
# A voice that scores just under the bar for one known person, clearly
# ahead of everyone else, in a room that has no GOOD sample of them yet
# (none at least ROOM_SAMPLE_GOOD_SEC long: a room with only short ones asks
# too, owner decision 2026-11-22): after the answer, "By the way, was that
# you, X?". Only the very next turn's yes, in the same room within
# ROOM_CHECK_TTL_SEC, in a voice that could be X's (_room_check_voice),
# saves THAT turn's clip as the room's sample for X — or, where X has only
# short samples, starts the "count from one to ten" learning that replaces
# them (_start_room_learning). Nothing unconfirmed is ever saved: drift
# re-enrolment from borderline matches once filed one person's voice under
# another (voice_identifier.py, job 4). A room asks a person at most once a
# day, whatever the answer. Never from a phone, never inside an
# introduction, never on a turn too short or too noisy to score.

#: The lowest score that asks (the bar itself is the match threshold).
ROOM_CHECK_FLOOR = 0.50
#: How long the question waits for its yes, in seconds.
ROOM_CHECK_TTL_SEC = 30.0
#: How long a room stays quiet about a person after asking, in seconds.
ROOM_CHECK_QUIET_SEC = 24 * 3600.0
#: At most this many samples a room keeps of one person from a yes here.
ROOM_CHECK_CAP = 3
#: How far under the asking turn's score for X the yes may score for X and
#: still count as X answering (owner decision 2026-11-22: only the asked
#: person's voice may confirm). A "yes, that was me" holds less speech than
#: the command that prompted the question, so it embeds a little looser;
#: someone else's voice scores far lower (in the case behind this rule,
#: another household member scored 0.11 against X, X 0.53).
ROOM_CHECK_YES_SLACK = 0.10
#: When each (person_id, room_id) was last asked. In memory: a restart
#: forgets it, and the room may ask once more.
_ROOM_CHECK_ASKED: dict[tuple[int, str], float] = {}


def _room_check_wanted(ctx: Context, response: Response, ident) -> tuple[int, float] | None:
    """``(person_id, score)`` when this turn may ask "was that you?", from
    what is known without the database; None otherwise."""
    room = ctx.room_id
    if not room or is_phone_room(room) or ctx.from_phone or phone_chat_mic(ctx):
        return None
    if ctx.in_call or starts_live_audio(response):
        # A turn that opens a drop-in or an announcement (the question's
        # follow-up listen would fight the call for the mic and speaker),
        # or one heard during a call (the other room may be in the clip).
        return None
    if ident is None or getattr(ident, "outcome", None) != OUTCOME_NO_MATCH:
        # Not scored (too short, no voiceprint), under music, two people
        # too close to call, an opt-out, or a match.
        return None
    if ident.person_id is not None or ident.embedding is None or ident.denylisted:
        return None
    speech = getattr(ident, "speech_seconds", None)
    if speech is not None and speech < settings.voice_profile_min_utterance_sec:
        return None
    person_id, best = ident.best_person_id, ident.best_similarity
    if person_id is None or best is None:
        return None
    if not (ROOM_CHECK_FLOOR <= best < settings.voice_profile_match_threshold):
        return None
    runner_up = ident.runner_up_similarity
    if runner_up is not None and best - runner_up < settings.voice_profile_match_margin:
        return None
    if ctx.long_capture or not ctx.answerable:
        return None
    if response.matched_handler == VoiceProfileHandler.name or response.expect_followup:
        # An introduction (or another question) is going on.
        return None
    if not (response.text or "").strip():
        return None
    asked = _ROOM_CHECK_ASKED.get((int(person_id), room))
    if asked is not None and time.time() - asked < ROOM_CHECK_QUIET_SEC:
        return None
    return int(person_id), float(best)


async def maybe_ask_room_check(
    session: AsyncSession, *, ctx: Context, response: Response, ident,
) -> bool:
    """Append "By the way, was that you, X?" to ``response`` and park the
    question with this turn's voice, when the turn qualifies (see above).
    True when it asked. Best-effort: a failure asks nothing."""
    try:
        wanted = _room_check_wanted(ctx, response, ident)
        if wanted is None or response.session_id is None:
            return False
        person_id, best = wanted
        room = str(ctx.room_id)
        pending = (await SessionRepository(session).get_context(response.session_id) or {}).get(
            "pending_confirmation"
        )
        if pending:
            return False
        here = await VoiceProfilesRepository(session).room_samples(
            person_id, room, VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
        )
        if any(is_good_sample(secs) for _id, secs in here):
            # The room knows them well. Only short samples (or none):
            # it asks, and a yes learns the room again.
            return False
        person = await PeopleRepository(session).get(person_id)
        if person is None:
            return False
        await request_confirmation(
            session,
            response.session_id,
            kind="core.room_check",
            handler="voice_profile",
            data={
                "person_id": person_id,
                "name": person[1],
                "embedding_b64": _b64_embedding(np.asarray(ident.embedding, dtype=np.float32)),
                "score": round(best, 3),
                "speech_sec": getattr(ident, "speech_seconds", None),
                "expires_at": time.time() + ROOM_CHECK_TTL_SEC,
                **_origin(ctx),
            },
        )
    except Exception as e:
        log.warning("room check: asking failed: %s", e)
        return False
    _ROOM_CHECK_ASKED[(person_id, room)] = time.time()
    response.text = (response.text or "").rstrip() + f" By the way, was that you, {person[1]}?"
    response.expect_followup = True
    log.info("room check: asked person_id=%d in room=%s (score %.3f)", person_id, room, best)
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
        "core.room_check",
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
    # A room's "By the way, was that you, X?" (maybe_ask_room_check) too.
    choice_kinds = (
        "core.phone_voice_add", "core.room_voice_add", "core.forget_me",
        "core.self_intro", "core.name_sound_alike", "core.room_check",
    )
    requires_network = "no"
    # Never during a live drop-in: an introduction or a "was that you?"
    # yes would learn a clip that may hold the other room's voice, played
    # on this room's speaker (router.call_refusal).
    works_in_call = False

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
            FastPath(_WHEREAMI_RE, VoiceProfileHandler._whereami_from_match),
            # "Who's the owner?" / "who's in charge here?": a joke about the
            # People list (owner_easter_eggs). Never "who's the admin?".
            FastPath(owner_easter_eggs.WHO_RE, VoiceProfileHandler._who_owner_from_match),
            # Third-party intros must come before self-intros — both can
            # match "this is Alex", but the third-party form's leading
            # "Domovoi," disambiguator wins when present.
            FastPath(_THIRD_PARTY_INTRO_RE, VoiceProfileHandler._third_party_from_match),
            # "I'm the owner" before the self-intro, which would take "the
            # owner" as a name (role_claims; case 18.8).
            FastPath(role_claims.ROLE_CLAIM_RE, VoiceProfileHandler._role_claim_from_match),
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
            if data.get("stage") in _COLLECT_STAGES:
                # The voice is saved already; a bare yes/no here has
                # nothing left to confirm.
                return self._say("Got it.", ctx)
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
            return await self._enroll_collecting(
                name, embedding, data, ctx, session,
                clips=_clips(data) if _collecting(data) else None,
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
        if not name and role_claims.is_role_not_name(raw_name):
            # "I'm the best", "my name is the owner": no name was said.
            return self._say(_ROLE_CLAIM_NAME_TEXT, ctx)
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

    async def _who_owner_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        """"Who's the owner?": a joke naming people on the People list
        (``people`` rows only), or none when it can't be read."""
        try:
            names = [n for _i, n in await PeopleRepository(session).names()]
        except Exception as e:  # noqa: BLE001 — the no-names lines still answer
            log.warning("who's the owner: the People list couldn't be read: %s", type(e).__name__)
            names = []
        # A voice turn is never a guest's (house_facts.voice_turn_is_guest).
        from domovoi import house_facts

        guest = house_facts.voice_turn_is_guest(ctx)
        return self._say(owner_easter_eggs.who_line(names, _egg_key(ctx), guest=guest), ctx)

    async def _role_claim_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response | None:
        """"I'm the owner", "I'm the admin, update the house", "I'm the mom":
        a role where a name would go. Nothing is enrolled and nothing is
        granted. A command after the claim that a fast path takes never gets
        here: the router drops the claim and runs it
        (``router.strip_request_preamble``) and its reply follows a joke
        line. An authority title gets a joke line (owner_easter_eggs)
        whatever follows it, and nothing more; any other role, on its own,
        how to say a name, and with more after it the turn routes on."""
        found = role_claims.split_claim(m.group(0))
        if found is None:
            return None
        role, rest = found
        if role_claims.is_authority(role):
            # A joke, never a grant: who may do an admin's job is the
            # dashboard's admin sign-in, never a voice (owner_easter_eggs).
            return self._say(owner_easter_eggs.claim_line(_egg_key(ctx)), ctx)
        if rest:
            return None
        return self._say(_ROLE_CLAIM_NAME_TEXT, ctx)

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

    async def _whereami_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return Response(
            text=await self._whereami(ctx, session),
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _whereami(self, ctx: Context, session: AsyncSession) -> str:
        """"You're in the basement." — the room whose satellite took the
        turn, by its label in Settings → Rooms when it has one."""
        if ctx.from_phone or is_phone_room(ctx.room_id):
            return "You're talking to me from your phone, so I can't tell which room you're in."
        room_id = ctx.room_id
        if not room_id:
            return "You're not talking to me from a room, so I can't tell which room you're in."
        label: str | None = None
        try:
            row = (await session.execute(
                sql_text("SELECT NULLIF(name, room_id) FROM rooms WHERE room_id = :r"),
                {"r": room_id},
            )).first()
            label = row[0] if row else None
        except Exception as e:
            log.warning("whereami: reading the room label failed: %s", e)
        return f"You're in {spoken_room(room_id, label)}."

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
          on the next utterance once a usable embedding lands; when the
          speaker model isn't on the server at all, saying it again won't
          help, and the answer says so instead.
        """
        if (
            ctx.session_id is None
            or ctx.embedding_bytes is None
            or len(ctx.embedding_bytes) != EMBEDDING_DIM * 4
        ):
            if ctx.session_id is not None and voice_id_unavailable():
                return Response(
                    text=f"Hi {name}, nice to meet you. {UNAVAILABLE_SPOKEN}",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
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
        # At a satellite the introduction goes on listening until it has
        # heard enough of the voice (INTRO_SPEECH_TARGET_SEC); this turn is
        # its first clip.
        clips = None
        if not phone_chat_mic(ctx):
            first_clip = _turn_clip(ctx, expected=ctx.person_id, first=True)
            clips = [first_clip] if first_clip is not None else None
        return await self._propose_name(
            name, embedding, ctx, session, claimed_by=ctx.person_id, first=True,
            spelled=spelled, clips=clips,
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
        clips: list | None = None,
        accept: bool = False,
    ) -> Response:
        """``clips``: the introduction's clips so far, when it collects
        more of the voice (a satellite's); carried by every question asked
        from here. ``accept``: the name was spelled in reply to "How do you
        spell that?", so a new name is saved as spelled, not read back.

        A name for this voice — from "my name is X", or a correction or
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
            already = existing is not None and (
                _same_name(existing[1], name)
                # "my name is Jordyn" in Jordan's voice: Jordan, misheard.
                or (sounds_like_name(name, existing[1]) and not spelled)
            )
            if already:
                learn = await self._offer_learn_here(existing, ctx, session, clips)
                if learn is not None:
                    return learn
                return self._say(f"I already know you, {existing[1]}.", ctx)
            if existing is not None and sounds_like_name(name, existing[1]):
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
                known[0], embedding, ctx, session, claimed_by=claimed_by, clips=clips,
            )
        if check_sound_alike:
            alikes = await self._sound_alikes(name, session)
            if len(alikes) == 1:
                return await self._offer_sound_alike(
                    alikes[0], name, embedding, ctx, session, claimed_by=claimed_by,
                    clips=clips,
                )
        if accept and clips is not None:
            # "J O R D Y N" in reply to "How do you spell that?": their
            # spelling is the name.
            return await self._enroll_collecting(
                name, embedding, {**_origin(ctx), _CLAIMED_BY_KEY: claimed_by}, ctx, session,
                clips=clips,
            )
        if first and clips is not None and not spelled:
            # A satellite's introduction: the spelling both settles the
            # name and is more of the voice.
            text, stage = _SPELL_TEXT, _STAGE_SPELL
        else:
            text = _intro_question(name) if first else _corrected_question(name)
            stage = _STAGE_CONFIRM
        return await self._park_intro(
            name, embedding, ctx, session, claimed_by=claimed_by,
            text=text, stage=stage, clips=clips,
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
        clips: list | None = None,
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
                    **({_CLIPS_KEY: clips} if clips is not None else {}),
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
        clips: list | None = None,
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
            **({_CLIPS_KEY: clips} if clips is not None else {}),
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
            text=f"Should I add this phone's voice to {name}?",
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
        clips: list | None = None,
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
            **({_CLIPS_KEY: clips} if clips is not None else {}),
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
        room it was asked in. When X already has samples there, all of them
        short (under :data:`ROOM_SAMPLE_GOOD_SEC`), the yes instead starts
        the room-learning collection a recognized X gets
        (:meth:`_offer_learn_here`): its longer recording replaces X's
        samples in that room, and only if it holds more speech than they
        did. No: a different X — a new person with the
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
        clips, embedding = self._intro_voice(data, ctx, embedding)
        saved: list = [] if clips is not None else None
        if not affirmative:
            response = await self._enroll_namesake(
                name, embedding, room_id, ctx, session, saved=saved,
                sample_seconds=_speech_of(clips) if clips is not None else None,
            )
            return await self._go_on(response, saved, clips, ctx, session, keep_text=True)
        # X already has short samples here (a one-second clip, say): learn
        # the room as X's own recognized voice would (_offer_learn_here), so
        # the longer recording REPLACES them instead of sitting beside them.
        here = await self._short_room_samples(person_id, room_id, ctx, session, clips)
        if here:
            if await self._is_denylisted(embedding, session):
                return self._say("I wasn't able to save that voice profile.", ctx)
            learn = await self._start_room_learning(
                person_id, name, room_id, here, clips, ctx, session,
                claimed_by=_claimed_by(data), lead=f"Thanks, {name}.",
            )
            if learn is not None:
                return learn
        response = await self._add_room_sample(
            person_id, name, embedding, room_id, ctx, session, claimed_by=_claimed_by(data),
            saved=saved, sample_seconds=_speech_of(clips) if clips is not None else None,
        )
        return await self._go_on(response, saved, clips, ctx, session)

    async def _enroll_namesake(
        self,
        name: str,
        embedding: np.ndarray,
        room_id: str | None,
        ctx: Context,
        session: AsyncSession,
        *,
        saved: list | None = None,
        sample_seconds: float | None = None,
    ) -> Response:
        """``saved``: given, it gets ``(sample_id, person_id, name)`` of the
        sample saved (:meth:`_go_on`).

        The "no" to "I already know someone called X. Is that you?": a
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
            sample_id = await VoiceProfilesRepository(session).add(
                person_id=person_id,
                embedding=embedding.astype(np.float32).tobytes(),
                model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
                room_id=room_id,
                sample_seconds=sample_seconds,
            )
            if saved is not None:
                saved.append((sample_id, person_id, name))
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
        saved: list | None = None,
        sample_seconds: float | None = None,
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
            sample_id = await VoiceProfilesRepository(session).add(
                person_id=person_id,
                embedding=embedding.astype(np.float32).tobytes(),
                model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
                room_id=room_id,
                sample_seconds=sample_seconds,
                claimed=True,
                claim_room=room_id,
                claimed_by=claimed_by,
            )
            if saved is not None:
                saved.append((sample_id, person_id, person[1]))
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
        if kind == "core.room_check":
            return await self._handle_room_check_reply(data, transcript, ctx, session)
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
        clips: list | None = None, embedding: np.ndarray | None = None,
    ) -> Response:
        """Ask for the name again — "What's your name?" after a plain no,
        "how do you spell it?" after a correction that couldn't be read —
        keeping the introduction's voice. A fresh question, with its own
        :data:`SELF_INTRO_TTL_SEC`."""
        if embedding is None:
            embedding = _decode_b64_embedding(str(data.get("embedding_b64") or ""))
        if embedding is None:
            return self._say("Something went wrong storing that — try saying your name again.", ctx)
        heard = str(data.get("name") or data.get("heard") or "") or None
        return await self._park_intro(
            "", embedding, ctx, session, claimed_by=_claimed_by(data),
            text=_ASK_SPELLING_TEXT if spelling else _ASK_NAME_TEXT,
            stage=_STAGE_ASK_NAME, heard=heard, clips=clips,
        )

    # ─── Hearing more of the voice ────────────────────────────────────
    def _intro_voice(
        self, data: dict, ctx: Context, embedding: np.ndarray | None,
    ) -> tuple[list | None, np.ndarray | None]:
        """The clips of a collecting introduction with this reply's added
        (when it counts: :func:`_turn_clip`), and the voice they make
        together; ``(None, embedding)`` for one that doesn't collect."""
        if not _collecting(data):
            return None, embedding
        clips = _clips(data)
        clip = _turn_clip(ctx, expected=_claimed_by(data))
        if clip is not None and _resembles(clips, clip):
            clips.append(clip)
        together = _combined(clips)
        return clips, together if together is not None else embedding

    async def _enroll_collecting(
        self,
        name: str,
        embedding: np.ndarray,
        data: dict,
        ctx: Context,
        session: AsyncSession,
        *,
        clips: list | None,
    ) -> Response:
        """:meth:`_enroll` the settled name, then (a collecting
        introduction) go on listening (:meth:`_go_on`)."""
        saved: list | None = [] if clips is not None else None
        response = await self._enroll(
            name, embedding, ctx, session, phone=_asked_on_the_phone(data),
            room_id=data.get(_ORIGIN_ROOM_KEY), claimed_by=_claimed_by(data),
            saved=saved, sample_seconds=_speech_of(clips) if clips is not None else None,
        )
        return await self._go_on(response, saved, clips, ctx, session)

    async def _go_on(
        self,
        response: Response,
        saved: list | None,
        clips: list | None,
        ctx: Context,
        session: AsyncSession,
        *,
        keep_text: bool = False,
    ) -> Response:
        """After an introduction saved its sample: done ("Thanks, X! I'll
        know your voice now.") when the clips hold
        :data:`INTRO_SPEECH_TARGET_SEC` of speech, else "Last bit: count
        from one to ten …", parked as the next stage. ``response`` stands
        when nothing was saved (an opt-out, an error, two namesakes);
        ``keep_text``: its words go first (a new namesake's)."""
        if not saved or clips is None:
            return response
        sample_id, person_id, name = saved[0]
        if _speech_of(clips) >= INTRO_SPEECH_TARGET_SEC:
            log.info(
                "introduction: person_id=%d saved from %d clip(s), %.1f s of speech",
                person_id, len(clips), _speech_of(clips),
            )
            text = response.text if keep_text else _thanks_text(name)
            return self._say(text, ctx)
        parked = await self._park_collect(
            _STAGE_COUNT, name, person_id, sample_id, clips, ctx, session,
        )
        if not parked:
            return self._say(response.text if keep_text else _thanks_text(name), ctx)
        text = f"{response.text} {_COUNT_TEXT}" if keep_text else _COUNT_TEXT
        return Response(
            text=text, session_id=ctx.session_id, matched_handler=self.name,
            expect_followup=True,
        )

    async def _offer_learn_here(
        self,
        existing: tuple,
        ctx: Context,
        session: AsyncSession,
        clips: list | None,
    ) -> Response | None:
        """ "my name is X" in X's own recognized voice, at a satellite whose
        room has no good sample of X (none, or only ones under
        :data:`ROOM_SAMPLE_GOOD_SEC` of speech, like 1.3.0's one-second
        relearn clips): "I already know you, X. Let me learn your voice
        better here: count from one to ten …", and the same collection as
        an introduction, seeded with this turn's clip. Its result becomes
        the room's sample (claimed for the room), replacing the short ones
        there. None: the room has a good sample (today's "I already know
        you" stands), or this is not a satellite's introduction."""
        person_id, name = int(existing[0]), existing[1]
        here = await self._short_room_samples(person_id, ctx.room_id, ctx, session, clips)
        if here is None:
            return None
        return await self._start_room_learning(
            person_id, name, ctx.room_id, here, clips, ctx, session,
            claimed_by=person_id, lead=f"I already know you, {name}.",
        )

    async def _short_room_samples(
        self,
        person_id: int,
        room_id: str | None,
        ctx: Context,
        session: AsyncSession,
        clips: list | None,
    ) -> list[tuple[int, float | None]] | None:
        """``(id, sample_seconds)`` of ``person_id``'s SATELLITE samples in
        ``room_id`` (oldest first, maybe none) when none of them holds
        :data:`ROOM_SAMPLE_GOOD_SEC` of speech: the samples a room-learning
        collection (:meth:`_start_room_learning`) may replace. Only that
        person, only that room. None: the room has a good one, the read
        failed, or this is not a satellite's collecting introduction."""
        if clips is None or not room_id or phone_chat_mic(ctx) or is_phone_room(room_id):
            return None
        try:
            here = await VoiceProfilesRepository(session).room_samples(
                person_id, room_id, VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
            )
        except Exception as e:
            log.warning("learning a room: reading its samples failed: %s", e)
            return None
        if any((secs or 0.0) >= ROOM_SAMPLE_GOOD_SEC for _id, secs in here):
            return None
        return here

    async def _start_room_learning(
        self,
        person_id: int,
        name: str,
        room_id: str,
        here: list[tuple[int, float | None]],
        clips: list,
        ctx: Context,
        session: AsyncSession,
        *,
        claimed_by: int | None,
        lead: str,
    ) -> Response | None:
        """Park a room-learning collection for ``person_id`` in ``room_id``,
        seeded with ``clips``: "<lead> Let me learn your voice better here:
        count from one to ten …". Nothing is saved now. Its result becomes
        the room's sample in place of ``here`` (:meth:`_save_learned_room`),
        and only when it holds more speech than the best of them, so a
        collection that ends early, short or not at all keeps them. None:
        it couldn't be parked."""
        parked = await self._park_collect(
            _STAGE_COUNT, name, person_id, None, clips, ctx, session,
            extra={
                _LEARN_ROOM_KEY: room_id,
                "replace_ids": [int(i) for i, _s in here],
                "have_secs": max([float(s or 0.0) for _i, s in here], default=0.0),
                _CLAIMED_BY_KEY: claimed_by,
            },
        )
        if not parked:
            return None
        log.info(
            "learning person_id=%d's voice in room=%s (%d short sample(s) there)",
            person_id, room_id, len(here),
        )
        return Response(
            text=f"{lead} Let me learn your voice better here: "
            "count from one to ten for me, at your normal voice.",
            session_id=ctx.session_id, matched_handler=self.name, expect_followup=True,
        )

    async def _save_learned_room(
        self, data: dict, person_id: int, clips: list, session: AsyncSession,
    ) -> int | None:
        """The room-learning collection's result as the room's sample
        (claimed for the room, like "Is that you?"'s yes), in place of the
        short samples the room had of the person — only when it holds more
        speech than the best of them. The new sample's id, or None."""
        room = data.get(_LEARN_ROOM_KEY)
        together = _combined(clips)
        if not room or together is None:
            return None
        if _speech_of(clips) <= float(data.get("have_secs") or 0.0):
            return None
        if await self._is_denylisted(together, session):
            return None
        repo = VoiceProfilesRepository(session)
        try:
            replace = [int(i) for i in data.get("replace_ids") or []]
            if replace:
                await repo.delete_samples(person_id, replace)
            sample_id = await repo.add(
                person_id=person_id,
                embedding=together.astype(np.float32).tobytes(),
                model=repo.EMBEDDING_MODEL_TAG,
                room_id=room,
                sample_seconds=_speech_of(clips),
                claimed=True,
                claim_room=room,
                claimed_by=_claimed_by(data),
            )
        except Exception as e:
            log.warning("learning a room: saving the sample failed: %s", e)
            return None
        log.info(
            "learned person_id=%d's voice in room=%s: %d clip(s), %.1f s (replaced %d)",
            person_id, room, len(clips), _speech_of(clips), len(replace),
        )
        return sample_id

    async def _park_collect(
        self,
        stage: str,
        name: str,
        person_id: int,
        sample_id: int | None,
        clips: list,
        ctx: Context,
        session: AsyncSession,
        *,
        extra: dict | None = None,
    ) -> bool:
        """Park the next "say something for me" question (a choice: the
        very next turn answers it, within :data:`SELF_INTRO_TTL_SEC`).
        ``sample_id`` None: a room-learning collection whose sample isn't
        saved yet (``extra`` says where)."""
        try:
            await request_confirmation(
                session,
                ctx.session_id,
                kind="core.self_intro",
                handler=self.name,
                data={
                    **(extra or {}),
                    "stage": stage,
                    "name": name,
                    "person_id": int(person_id),
                    "sample_id": None if sample_id is None else int(sample_id),
                    _CLIPS_KEY: clips,
                    **_origin(ctx),
                    "created_at": datetime.now(tz=timezone.utc).isoformat(),
                    "expires_at": time.time() + SELF_INTRO_TTL_SEC,
                },
            )
        except Exception as e:
            log.warning("couldn't park the introduction's next question: %s", e)
            return False
        return True

    @staticmethod
    def _is_stop(transcript: str) -> bool:
        """ "stop", "never mind", "cancel", a plain "no": the person has
        had enough of the introduction."""
        from domovoi.router import normalize_transcript

        t = re.sub(r"[.!?,]+", " ", normalize_transcript(transcript or "").lower())
        t = " ".join(t.split())
        if _STOP_RE.match(t):
            return True
        reply = read_name_reply(t)
        return reply is not None and reply.kind in (person_names.CANCEL, person_names.NO)

    @staticmethod
    def _is_command(transcript: str) -> bool:
        """Whether a fast path takes the turn (as the router would read it)."""
        from domovoi.router import first_fast_path, normalize_transcript, strip_leading_filler

        t = normalize_transcript(transcript or "")
        return first_fast_path(t) is not None or first_fast_path(strip_leading_filler(t)) is not None

    async def _handle_collect_reply(
        self, data: dict, transcript: str, ctx: Context, session: AsyncSession,
    ) -> Response | None:
        """The turn after "count from one to ten" or "say 'Good morning
        Domovoi, …'". The sample is saved already, so whatever happens it
        stays: in time and from the same room, this turn's clip joins it
        (the sample's embedding becomes all the clips' together); "stop" or
        "never mind" ends it; a reply past its time isn't an answer (None:
        routed as usual).

        A room-learning collection (:meth:`_offer_learn_here`) has no
        sample yet: it saves one (:meth:`_save_learned_room`) once it holds
        :data:`ROOM_SAMPLE_GOOD_SEC` of speech, or when it ends ("stop", a
        command, the last question) with more speech than the room had."""
        if not _answered_where_asked(data, ctx):
            return self._say(_ELSEWHERE_TEXT, ctx)
        name = str(data.get("name") or "")
        learning = bool(data.get(_LEARN_ROOM_KEY))
        try:
            person_id = int(data.get("person_id"))
            sample_id = None if data.get("sample_id") is None else int(data.get("sample_id"))
        except (TypeError, ValueError):
            return None
        if sample_id is None and not learning:
            return None
        if _intro_expired(data):
            return None
        clips = _clips(data)
        if self._is_stop(transcript):
            if sample_id is None:
                await self._save_learned_room(data, person_id, clips, session)
            return self._say(_stopped_text(name), ctx)
        stage = data.get("stage")
        if stage == _STAGE_COUNT and self._is_command(transcript):
            # A command instead of counting ("who am I?", "play jazz"): the
            # introduction is over (its sample stays) and the turn is routed
            # as usual. Not after the sentence, which is meant to sound
            # like one ("… what's on today?").
            if sample_id is None:
                await self._save_learned_room(data, person_id, clips, session)
            return None
        clip = _turn_clip(ctx, expected=person_id)
        added = clip is not None and _resembles(clips, clip) and not await self._is_denylisted(
            _decode_b64_embedding(clip[0]), session,
        )
        if added:
            clips.append(clip)
        finished = _speech_of(clips) >= INTRO_SPEECH_TARGET_SEC or stage == _STAGE_SENTENCE
        if added and sample_id is not None:
            together = _combined(clips)
            try:
                kept = await VoiceProfilesRepository(session).replace_embedding(
                    sample_id, person_id, together.astype(np.float32).tobytes(),
                    _speech_of(clips),
                )
            except Exception as e:
                log.warning("introduction: updating the sample failed: %s", e)
                return self._say(_thanks_text(name), ctx)
            if not kept:
                return self._say(f"I can't find {name} any more, so I didn't save that.", ctx)
        elif sample_id is None and (finished or _speech_of(clips) >= ROOM_SAMPLE_GOOD_SEC):
            sample_id = await self._save_learned_room(data, person_id, clips, session)
        if finished:
            log.info(
                "introduction: person_id=%d's sample from %d clip(s), %.1f s of speech",
                person_id, len(clips), _speech_of(clips),
            )
            return self._say(_thanks_text(name), ctx)
        learn_keys = {
            k: data[k] for k in (_LEARN_ROOM_KEY, "replace_ids", "have_secs", _CLAIMED_BY_KEY)
            if k in data
        } if learning else None
        if not await self._park_collect(
            _STAGE_SENTENCE, name, person_id, sample_id, clips, ctx, session, extra=learn_keys,
        ):
            return self._say(_thanks_text(name), ctx)
        return Response(
            text=_sentence_text(ctx.bot_name), session_id=ctx.session_id,
            matched_handler=self.name, expect_followup=True,
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
          the turn routed as usual (None).

        "Nice to meet you! How do you spell that?" (a satellite's
        introduction, stage ``spell``) is answered the same way, and a
        spelling of a different name is taken as spelled, not read back.
        Each reply is more of the voice (:meth:`_intro_voice`). The stages
        after the voice is saved ("count from one to ten …") are
        :meth:`_handle_collect_reply`'s."""
        stage = data.get("stage") or _STAGE_CONFIRM
        if stage in _COLLECT_STAGES:
            return await self._handle_collect_reply(data, transcript, ctx, session)
        reply = self._read_reply(transcript)
        if reply is None:
            return None
        if not _answered_where_asked(data, ctx):
            return self._say(_ELSEWHERE_TEXT, ctx)
        name = str(data.get("name") or "")
        naming = (_STAGE_CONFIRM, _STAGE_SPELL)
        if reply.kind == person_names.CANCEL:
            return self._say(_WONT_SAVE_TEXT, ctx)
        clips, embedding = self._intro_voice(
            data, ctx, _decode_b64_embedding(str(data.get("embedding_b64") or "")),
        )
        if reply.kind == person_names.NO:
            if stage == _STAGE_ASK_NAME or not name:
                return self._say(_WONT_SAVE_TEXT, ctx)
            if _intro_expired(data):
                return self._say(_INTRO_EXPIRED_TEXT, ctx)
            return await self._ask_for_name(
                data, ctx, session, spelling=False, clips=clips, embedding=embedding,
            )
        if _intro_expired(data):
            return self._say(_INTRO_EXPIRED_TEXT, ctx)
        if embedding is None:
            return self._say("Something went wrong storing that — try saying your name again.", ctx)
        if reply.kind == person_names.UNCLEAR:
            return await self._ask_for_name(
                data, ctx, session, spelling=True, clips=clips, embedding=embedding,
            )
        if reply.kind == person_names.YES:
            if stage == _STAGE_ASK_NAME or not name:
                return await self._ask_for_name(
                    data, ctx, session, spelling=False, clips=clips, embedding=embedding,
                )
            return await self._enroll_collecting(name, embedding, data, ctx, session, clips=clips)
        # A name.
        new_name = reply.name or ""
        if name and _same_name(new_name, name) and stage in naming:
            if reply.polarity is False and not reply.spelled:
                # "No, it's Jordan" to "Jordan": the name sounded right and
                # the spelling is what's wrong.
                return await self._ask_for_name(
                    data, ctx, session, spelling=True, clips=clips, embedding=embedding,
                )
            if reply.spelled and new_name != name:
                # Spelled the same, written differently ("McDonald"): theirs.
                name = new_name
            return await self._enroll_collecting(name, embedding, data, ctx, session, clips=clips)
        log.info(
            "self-introduction corrected: %r -> %r (%s)",
            name or data.get("heard"), new_name, "spelled" if reply.spelled else "said",
        )
        return await self._propose_name(
            new_name, embedding, ctx, session, claimed_by=_claimed_by(data), first=False,
            spelled=reply.spelled, clips=clips,
            accept=stage == _STAGE_SPELL and reply.spelled,
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
        clips, embedding = self._intro_voice(data, ctx, embedding)
        is_known = reply.kind == person_names.YES or (
            reply.kind == person_names.NAME and _same_name(reply.name or "", known)
        )
        if is_known:
            if _asked_on_the_phone(data):
                return await self._add_phone_sample(
                    person_id, known, embedding, ctx, session, claimed_by=claimed_by,
                )
            saved: list | None = [] if clips is not None else None
            response = await self._add_room_sample(
                person_id, known, embedding, data.get(_ORIGIN_ROOM_KEY) or ctx.room_id,
                ctx, session, claimed_by=claimed_by, saved=saved,
                sample_seconds=_speech_of(clips) if clips is not None else None,
            )
            return await self._go_on(response, saved, clips, ctx, session)
        if reply.kind == person_names.UNCLEAR:
            return await self._ask_for_name(
                {**data, "name": "", "heard": heard}, ctx, session, spelling=True,
                clips=clips, embedding=embedding,
            )
        if reply.kind == person_names.NO:
            if not heard:
                return await self._ask_for_name(
                    {**data, "name": ""}, ctx, session, spelling=False,
                    clips=clips, embedding=embedding,
                )
            return await self._propose_name(
                heard, embedding, ctx, session, claimed_by=claimed_by, first=True,
                check_sound_alike=False, clips=clips,
            )
        return await self._propose_name(
            reply.name or "", embedding, ctx, session, claimed_by=claimed_by, first=False,
            spelled=reply.spelled, check_sound_alike=False, clips=clips,
        )

    async def _handle_room_check_reply(
        self, data: dict, transcript: str, ctx: Context, session: AsyncSession,
    ) -> Response | None:
        """The turn after "By the way, was that you, X?" (maybe_ask_room_check).
        Yes, in time, in the same room and in a voice that could be X's
        (:meth:`_room_check_voice`): the asking turn's clip becomes this
        room's sample for X (marked as claimed, like every sample a yes
        adds), unless X has :data:`ROOM_CHECK_CAP` here already. Where X's
        samples here are all short, the yes instead starts the room-learning
        collection (:meth:`_start_room_learning`), which replaces them only
        with more speech. A yes in somebody else's voice, or too short or
        noisy to tell whose, saves nothing. No: nothing. Anything else
        isn't an answer (None: routed as usual), and saves nothing either."""
        from domovoi.router import _parse_yes_no, normalize_transcript, strip_leading_filler

        said = normalize_transcript(transcript or "")
        affirmative = _parse_yes_no(said)
        if affirmative is None:
            affirmative = _parse_yes_no(strip_leading_filler(said))
        if affirmative is None:
            return None
        room_id = data.get(_ORIGIN_ROOM_KEY)
        if _asked_on_the_phone(data) or phone_chat_mic(ctx) or ctx.from_phone or room_id != ctx.room_id:
            return None
        name = str(data.get("name") or "")
        if not affirmative:
            return self._say("Okay, my mistake.", ctx)
        try:
            expired = time.time() > float(data.get("expires_at"))
        except (TypeError, ValueError):
            expired = True
        if expired:
            return self._say("That was a while ago, so I didn't save it.", ctx)
        try:
            person_id = int(data.get("person_id"))
        except (TypeError, ValueError):
            person_id = None
        embedding = _decode_b64_embedding(str(data.get("embedding_b64") or ""))
        if person_id is None or embedding is None or not name or not room_id:
            return self._say("Something went wrong storing that — your voice wasn't added.", ctx)
        verdict = await self._room_check_voice(data, person_id, room_id, ctx, session)
        if verdict != "theirs":
            log.info(
                "room check: a yes for person_id=%d in room=%s refused (%s voice)",
                person_id, room_id, verdict,
            )
            if verdict == "unheard":
                return self._say(
                    f"I couldn't hear enough to be sure that was you, so I won't save it. "
                    f"I'll ask {name} myself next time.", ctx,
                )
            return self._say(
                f"That didn't sound like {name}, so I won't save it. "
                f"I'll ask {name} myself next time.", ctx,
            )
        secs = data.get("speech_sec")
        try:
            secs = None if secs is None else max(0.0, float(secs))
        except (TypeError, ValueError):
            secs = None
        clips = [[str(data.get("embedding_b64")), secs or 0.0]]
        # X has only short samples here (owner decision 2026-11-22): learn
        # the room as "my name is X" does, so a longer recording REPLACES
        # them instead of a fourth short clip sitting beside them.
        short = await self._short_room_samples(person_id, room_id, ctx, session, clips)
        if short:
            if await self._is_denylisted(embedding, session):
                return self._say("I wasn't able to save that voice profile.", ctx)
            log.info("room check: person_id=%d confirmed in room=%s, learning it", person_id, room_id)
            learn = await self._start_room_learning(
                person_id, name, room_id, short, clips, ctx, session,
                claimed_by=None, lead=f"Thanks, {name}.",
            )
            if learn is not None:
                return learn
        try:
            here = await VoiceProfilesRepository(session).room_sample_count(
                person_id, room_id, VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
            )
        except Exception as e:
            log.warning("room check: counting the room's samples failed: %s", e)
            return self._say("Something went wrong saving that — your voice wasn't added.", ctx)
        if here >= ROOM_CHECK_CAP:
            return self._say(f"Thanks, {name}. I already know your voice in this room.", ctx)
        log.info("room check: person_id=%d confirmed in room=%s", person_id, room_id)
        return await self._add_room_sample(
            person_id, name, embedding, room_id, ctx, session, sample_seconds=secs,
        )

    async def _room_check_voice(
        self, data: dict, person_id: int, room_id: str, ctx: Context, session: AsyncSession,
    ) -> str:
        """Whose voice said yes to "was that you, X?" (owner decision
        2026-11-22: only X's may). ``"theirs"``, ``"someone else"`` or
        ``"unheard"`` (no voiceprint: too short, under music, or the scores
        couldn't be read).

        The rule. Requiring the yes to MATCH X (score over the bar) would
        refuse exactly the person the question is for: their voice scored
        under the bar in this room, which is why it asked. So the yes turn
        is scored the way any turn here is (voice_identifier.scores_in_room:
        the same samples, room rule and bars), and counts as X's when

        * its score for X is at least the asking turn's score minus
          :data:`ROOM_CHECK_YES_SLACK` — as much like X as the voice the
          question was about, give or take the looser embedding of a short
          "yes"; and
        * nobody else is as close: no other enrolled person scores higher
          than X, or over their own bar (the voice would be theirs, or too
          close to call).

        An opted-out voice is never X's answer."""
        if ctx.voice_outcome == OUTCOME_DENYLIST:
            return "someone else"
        raw = ctx.embedding_bytes
        if raw is None or len(raw) != EMBEDDING_DIM * 4 or ctx.voice_outcome == OUTCOME_NOISY:
            return "unheard"
        try:
            scored = await scores_in_room(session, np.frombuffer(raw, dtype=np.float32), room_id)
        except Exception as e:
            log.warning("room check: scoring the yes failed: %s", e)
            return "unheard"
        mine = scored.scores.get(person_id)
        if mine is None:
            return "someone else"
        try:
            asked = float(data.get("score"))
        except (TypeError, ValueError):
            asked = ROOM_CHECK_FLOOR
        if mine < asked - ROOM_CHECK_YES_SLACK:
            return "someone else"
        threshold = settings.voice_profile_match_threshold
        for other, score in scored.scores.items():
            if other != person_id and (score > mine or score >= scored.bars.get(other, threshold)):
                return "someone else"
        return "theirs"

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
        toward respecting the opt-out on noisy embeddings. Current-embedding
        rows only: a pre-1.3.0 opt-out gets its current twin the first time
        identify() hears that voice (voice_identifier._denylisted).

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
        saved: list | None = None,
        sample_seconds: float | None = None,
    ) -> Response:
        """``saved``: given, a satellite sample saved is reported in it as
        ``(sample_id, person_id, name)`` (:meth:`_go_on`).

        Save ``embedding`` as ``name``'s voice. ``phone``: the question
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
                saved=saved, sample_seconds=sample_seconds,
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
                sample_id = await voice_profiles.add(
                    person_id=person_id,
                    embedding=embedding.astype(np.float32).tobytes(),
                    model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG,
                    room_id=room_id,
                    sample_seconds=sample_seconds,
                )
                if saved is not None:
                    saved.append((sample_id, person_id, name))
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
            elif voice_id_unavailable():
                text = UNAVAILABLE_SPOKEN
            elif (unheard := unheard_text(ctx.voice_outcome, f"who am I, {ctx.bot_name}?")) is not None:
                # The voice was never scored (too little speech, no
                # embedding, identification didn't finish): that says
                # nothing about whether this person is known, so neither
                # does the reply.
                text = unheard
            else:
                text = "I don't recognize your voice yet."
                if await self._relearn_pending(session):
                    # 1.3.0's new speaker model can't use the voices the
                    # old one learned (V061): a person known before is
                    # unknown until they introduce themselves again.
                    text += (
                        " My voice recognition was upgraded, so if we've met before, "
                        "say \"my name is\" and your name, and I'll learn your voice again."
                    )
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

    async def _relearn_pending(self, session: AsyncSession) -> bool:
        try:
            return await PeopleRepository(session).relearn_pending(
                VoiceProfilesRepository.EMBEDDING_MODEL_TAG
            ) > 0
        except Exception as e:  # noqa: BLE001 — the plain answer stands
            log.warning("whoami: reading who has to re-introduce failed: %s", e)
            return False

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
            name = _forget_name(row[1] if row else "")
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
            text=_forget_ask(name),
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
        name = _forget_name(data.get("name"))
        if not affirmative:
            return Response(
                text=f"Okay, I've kept everything, {name}." if name else "Okay, I've kept everything.",
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
                text=(
                    f"That doesn't sound like {name}, so I haven't forgotten anything." if name
                    else "That doesn't sound like the same voice, so I haven't forgotten anything."
                ),
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
                    text=f"I've already forgotten {name}." if name else "I've already forgotten you.",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
            # What they said goes too (owner, 2026-10-17): their own spoken
            # turns, found by who voice identification heard speaking,
            # BEFORE the person row (whose delete sets the turns'
            # person_id to NULL). domovoi/voice_history.py.
            await voice_history.delete_person_history(session, person_id)
            await PeopleRepository(session).delete(person_id)
            # Plugins hear core.voice_history_deleted once the turn's
            # transaction commits (SDK 1.18); a rollback tells nobody.
            voice_history.announce_after_commit(session)
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
                f"Okay, I've forgotten you{', ' + name if name else ''}: your voice, your profile, "
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
        heard = ctx.embedding_bytes
        if all(s.own for s in samples):
            return "person", []
        if heard is None or len(heard) != EMBEDDING_DIM * 4:
            return "unheard", []
        voice = np.frombuffer(heard, dtype=np.float32)
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
            ) if name else (
                "I've forgotten the voice you added to that profile. I kept the "
                "profile and its memories: your voice doesn't match the one I learned "
                "for it, so only an admin can delete those, in Settings, under People."
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
                f"Your voice doesn't match the one I learned for {name or 'that profile'}, so I "
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
            if voice_id_unavailable():
                # Nothing is recognized or saved, so there is nothing to
                # opt out of yet; saying it again wouldn't change that.
                return Response(
                    text=(
                        "I can't recognize or save anyone's voice right now, so yours isn't "
                        "saved. Once voice recognition works again, say it once more and I'll "
                        "opt you out."
                    ),
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
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
