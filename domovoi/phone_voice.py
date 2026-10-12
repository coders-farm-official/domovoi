"""Phone push-to-talk turns — the chat mic (``POST /v1/phone/voice``).

The Android chat screen has a mic button that opens a hold-to-talk view
(android ``ui/screens/chat/VoiceView.kt``): press and hold records, release
sends. The recording comes here and is answered the way a satellite turn
is — speech-to-text, voice identification, the same router, text-to-speech
— and both turns are written into the chat thread the phone is looking at
(``chat_messages`` with ``source = 'voice'``, V025), so spoken and typed
messages scroll together.

A phone is NOT a satellite (the same rule as the drop-in socket,
domovoi/phone_dropin.py): nothing here registers in
``app.state.active_sessions``, so no room's Pi is evicted, no wake-word
routing or music player is involved, and a phone id can never be taken
for a room. Turns are logged under ``phone-<device id>`` (intents_log,
conversation_log) like any room. The phone is its own device
(``Context.device_kind == "phone"``): media asked for plays on the phone
(``Response.device_actions``, handed to the app as ``actions``), timers and
reminders go house-wide, and a satellite's own settings are refused.

THE WAKE PHRASE. A satellite's transcript starts after its wake word, so
the router never sees one. People say it into a push-to-talk phone anyway
("Hey Domovoi, play suicide boys"), and a leading "hey domovoit," (as
speech-to-text spells it) keeps every start-anchored fast path from
matching: the turn went to the tool model with the name buried in it,
and on 2026-10-12 "play Suicide Boys" from a phone played a random song.
:func:`strip_wake_phrase` takes it off before routing; the chat thread
keeps what was said.

FOLLOW-UPS. Pending confirmations and parked choices live in the routing
session, and the session is kept per phone AND chat thread (process
memory, like a satellite's, which a reconnect also starts afresh): a
question Domovoi asks ("did you mean …?") is answered by the next press in
that thread — "yes" resumes it, as a satellite's follow-up capture would.

REQUEST (multipart/form-data):

  audio       the recording: 16 kHz mono PCM16 little-endian, raw (what
              the drop-in socket streams) or as a WAV file of that format;
              at most :data:`MAX_CLIP_SEC` seconds
  phone_id    the app's device id (``Prefs.deviceId``; the devices tier's
              id shape)
  thread_id   the chat thread the two messages go into
  player      optional JSON: what the phone's own player is doing,
              {"title", "artist", "kind", "playing"} — for "what's playing"
  speak       optional, "true" (default) or "false": "false" when the phone
              speaks the reply itself (its own voice pick, downloaded): no
              text-to-speech here, and ``audio`` comes back null

RESPONSE (200, JSON): see :func:`run_phone_turn`.

AUTHENTICATION. The device tier with no pre-setup grace
(``admin_auth.require_device_after_setup``), as the drop-in socket: the
household ``X-Device-Token`` or an admin Bearer, and 501 before an admin
exists. The app reaches it through the web backend's
``POST /api/chat/threads/{thread_id}/voice`` on the server it is paired
with, which forwards the caller's token.
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import io
import json
import logging
import re
import struct
import time
import wave
from collections import OrderedDict
from typing import Any
from uuid import UUID

from sqlalchemy import text

from domovoi.clients.tts import get_tts_client
from domovoi.clients.whisper import (
    FULL_WINDOW_SEC,
    SHORT_WINDOW_SEC,
    SttUnavailableError,
    can_decode_full_window,
    get_whisper_client,
    transcribe_full_window,
    transcribe_with_window,
)
from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.models import PHONE_ROOM_PREFIX, Context, Intent, Response
from domovoi.voice_identifier import speaker_is_confident

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
BYTES_PER_SEC = SAMPLE_RATE * 2
#: The longest recording one turn takes (the app stops recording here).
MAX_CLIP_SEC = 30
MAX_CLIP_BYTES = MAX_CLIP_SEC * BYTES_PER_SEC
#: Anything shorter is a tap, not speech: not transcribed, nothing written.
MIN_CLIP_SEC = 0.3
#: The devices tier's id shape (web/backend/api/devices.py _DEVICE_ID_RE).
PHONE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$")
#: chat_threads.id is a BIGINT: anything past it is no thread at all.
MAX_THREAD_ID = 2**63 - 1
MAX_PLAYER_CHARS = 2048
_PLAYER_KEYS = ("title", "artist", "kind", "playing")

#: Said (and stored on the reply row) when the turn itself failed.
TURN_FAILED_TEXT = (
    "Domovoi couldn't answer that. Try again; if it keeps happening, an "
    "admin can find the reason in the server log."
)


class PhoneTurnRefused(Exception):
    """The turn was refused before anything was written; ``status`` and
    ``detail`` become the HTTP answer, ``code`` is for the app."""

    def __init__(self, status: int, detail: str, code: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code


# ─── Inputs ───────────────────────────────────────────────────────────────


def phone_room(phone_id: str) -> str:
    """The room id a phone's turns are logged under."""
    return phone_id if phone_id.startswith(PHONE_ROOM_PREFIX) else PHONE_ROOM_PREFIX + phone_id


def valid_phone_id(raw: str | None) -> str:
    ident = (raw or "").strip()
    if not PHONE_ID_RE.match(ident):
        raise PhoneTurnRefused(422, "phone_id is not a device id", "bad_phone_id")
    return ident


def pcm_from_upload(data: bytes) -> bytes:
    """The recording as 16 kHz mono PCM16: raw bytes as they came, or a
    WAV of exactly that format unwrapped. Anything else, or anything
    longer than :data:`MAX_CLIP_SEC`, is refused."""
    if data[:4] == b"RIFF":
        try:
            with wave.open(io.BytesIO(data)) as w:
                if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, SAMPLE_RATE):
                    raise PhoneTurnRefused(
                        415, "audio must be 16 kHz mono 16-bit PCM", "bad_audio_format",
                    )
                pcm = w.readframes(w.getnframes())
        except (wave.Error, EOFError, RuntimeError, ValueError, OSError, struct.error) as e:
            # RuntimeError: the wave module's own answer to a chunk whose
            # size runs past the end of the file.
            raise PhoneTurnRefused(400, "audio is not a readable WAV file", "bad_audio") from e
    else:
        pcm = data
    if len(pcm) % 2:
        pcm = pcm[:-1]
    if len(pcm) > MAX_CLIP_BYTES:
        raise PhoneTurnRefused(
            413, f"a voice turn is at most {MAX_CLIP_SEC} seconds", "clip_too_long",
        )
    return pcm


def parse_player(raw: str | None) -> dict[str, Any]:
    """The phone player's state the app sent along, reduced to the keys
    a handler reads; anything unreadable is simply nothing."""
    if not raw or len(raw) > MAX_PLAYER_CHARS:
        return {}
    try:
        doc = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(doc, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _PLAYER_KEYS:
        value = doc.get(key)
        if key == "playing":
            if isinstance(value, bool):
                out[key] = value
        elif isinstance(value, str) and value.strip():
            out[key] = value.strip()[:300]
    return out


# ─── The wake phrase ──────────────────────────────────────────────────────

#: What may part a wake phrase from the request: spaces and punctuation,
#: dashes and an ellipsis included ("Hey Domovoi… play jazz").
_WAKE_SEP = r"\s,.!?;:\-…—–"
#: Punctuation proper: what must follow a name said WITHOUT a greeting, so
#: "Max, turn it up" loses its "Max," but "Max out the volume" is a request.
_WAKE_PUNCT = ",.!?;:-…—–"
#: A hesitation, then the greeting the name is said with ("hey", "ok").
_WAKE_HEAD_RE = re.compile(
    rf"[{_WAKE_SEP}]*(?:(?:um|uh|oh)[{_WAKE_SEP}]+)?(?:(?P<greet>hey|hi|hello|ok|okay)\b[{_WAKE_SEP}]*)?",
    re.IGNORECASE,
)
#: One word and whatever separates it from the next.
_WAKE_WORD_RE = re.compile(rf"(?P<word>[a-z]+)(?P<sep>[{_WAKE_SEP}]*)", re.IGNORECASE)
#: The names the wake phrase is said with; the household's own name for the
#: assistant (``settings.bot_name``) joins them.
_WAKE_NAMES = ("domovoi", "domo")
#: Names this long or longer may be heard approximately; a shorter one
#: ("Domo", a short household name such as "Max") counts only as said:
#: "dome" and "demo" are words of their own ("OK, dome light on").
_WAKE_FUZZY_MIN_LEN = 5
#: How close a heard word must sound to a name (difflib's ratio) after a
#: greeting: "domavoy" is 0.71 from "domovoi", "damuvoi" 0.71, "dominic" 0.57.
_WAKE_NEAR = 0.7
#: Without a greeting only a near-exact one counts ("domovoit", 0.93): a bare
#: word that merely starts like it may be the request itself.
_WAKE_NEAR_ALONE = 0.8


def _wake_names() -> tuple[str, ...]:
    own = re.sub(r"[^a-z]", "", (settings.bot_name or "").lower())
    return _WAKE_NAMES + ((own,) if len(own) >= 3 and own not in _WAKE_NAMES else ())


def _sounds_like_the_name(heard: str, *, greeted: bool) -> bool:
    heard = heard.lower()
    for name in _wake_names():
        if heard == name:
            return True
        if len(name) < _WAKE_FUZZY_MIN_LEN:
            continue
        # Speech-to-text keeps the first sound, give or take a d/t.
        if heard[0] != name[0] and {heard[0], name[0]} != {"d", "t"}:
            continue
        near = _WAKE_NEAR if greeted else _WAKE_NEAR_ALONE
        if difflib.SequenceMatcher(None, heard, name).ratio() >= near:
            return True
    return False


def _name_ends(said: str, word: re.Match[str], *, greeted: bool) -> bool:
    """Whether the name heard ends where ``word`` does: nothing glued to it
    ("Domovoi's playlist" is a request about Domovoi), and, said without a
    greeting, punctuation after it ("Max, turn it up", not "Max out …")."""
    after = said[word.end("word"):word.end("word") + 1]
    if after and (after.isalnum() or after in "'’"):
        return False
    return greeted or any(ch in _WAKE_PUNCT for ch in word.group("sep"))


def _strip_one_wake_phrase(said: str) -> str | None:
    """``said`` after one leading wake phrase, or None when it has none."""
    head = _WAKE_HEAD_RE.match(said)
    greeted = bool(head and head.group("greet"))
    at = head.end() if head else 0
    first = _WAKE_WORD_RE.match(said, at)
    if first is None:
        return None
    # The name heard as two words ("doma voy", "domo voi"): only a space
    # between them, a short second word, and no longer than the name
    # itself would be heard ("domovoi play" is the name and a request).
    second = _WAKE_WORD_RE.match(said, first.end())
    if (
        second is not None
        and first.group("sep")
        and first.group("sep").strip(" -") == ""
        and len(second.group("word")) <= 4
        and len(first.group("word")) + len(second.group("word")) <= len("domovoi") + 2
        and _sounds_like_the_name(first.group("word") + second.group("word"), greeted=greeted)
        and _name_ends(said, second, greeted=greeted)
    ):
        return said[second.end():]
    if _sounds_like_the_name(first.group("word"), greeted=greeted) and _name_ends(
        said, first, greeted=greeted,
    ):
        return said[first.end():]
    return None


def strip_wake_phrase(transcript: str) -> str:
    """``transcript`` without a leading wake phrase — "Hey Domovoit, play
    Suicide Boys." → "play Suicide Boys." — so the router reads a phone turn
    the way it reads a satellite's (see THE WAKE PHRASE above).

    Fuzzy, as speech-to-text spells the name: a greeting (hey / hi / hello /
    ok / okay) and a word that sounds like Domovoi ("Domovoit", "Domavoy",
    "doma voy"), or is "Domo" or the household's own name for the assistant
    (a short name only as said); or, without a greeting, a near-exact name
    with punctuation after it ("Domovoi, …"). Never a name with something
    glued to it ("Domovoi's playlist"). Said twice, both go. A transcript
    that is nothing BUT the wake phrase comes back as it was (there is no
    request to route instead)."""
    rest = transcript
    for _ in range(2):
        after = _strip_one_wake_phrase(rest)
        if after is None:
            break
        rest = after
    if rest is transcript or not any(ch.isalnum() for ch in rest):
        return transcript
    return rest.strip()


# ─── Sessions: one per phone and thread ───────────────────────────────────

_SESSIONS: "OrderedDict[tuple[str, int], UUID]" = OrderedDict()
_SESSIONS_CAP = 512
#: Phones with a turn in progress. A second press while the first is still
#: being answered is refused (409) rather than queued behind it.
_BUSY: set[str] = set()
#: Fan-outs (intercom announcements) still running after the reply.
_BACKGROUND: set[asyncio.Task[Any]] = set()


def session_for(room: str, thread_id: int) -> UUID | None:
    return _SESSIONS.get((room, thread_id))


def remember_session(room: str, thread_id: int, session_id: UUID | None) -> None:
    if session_id is None:
        return
    key = (room, thread_id)
    _SESSIONS[key] = session_id
    _SESSIONS.move_to_end(key)
    while len(_SESSIONS) > _SESSIONS_CAP:
        _SESSIONS.popitem(last=False)


def reset_for_tests() -> None:
    _SESSIONS.clear()
    _BUSY.clear()


# ─── Who spoke ────────────────────────────────────────────────────────────


async def _identify(pcm: bytes, room: str) -> Any:
    """Who spoke — matching only, against each person's PHONE samples
    when they have any, else their satellite samples
    (``samples="phone"``, V026). No ``room_id`` goes to the identifier,
    and a phone match never learns anyway: a phone never feeds the passive
    learning (``_learn_room``: after a run of confident matches it saves
    the clip as that person's sample for the "room"). A phone id is
    self-asserted and changes with every reinstall, so each would add a
    sample. Phone samples are added only on purpose: "my name is …" said
    here, then "yes" (handlers/voice_profile.py)."""
    from domovoi.voice_identifier import PHONE_SAMPLES, identify

    try:
        return await identify(pcm, room_id=None, samples=PHONE_SAMPLES)
    except Exception as e:  # noqa: BLE001 — best-effort, as on a satellite
        log.warning("phone voice: voice identification failed: %s", e)
        return None


def _speaker_score(ident: Any) -> float | None:
    """The score stored with the message: the match's when someone was
    named, the best near miss otherwise, nothing for an opted-out voice."""
    if ident is None or getattr(ident, "denylisted", False):
        return None
    score = getattr(ident, "best_similarity", None)
    if score is None:
        return None
    return max(-1.0, min(1.0, round(float(score), 4)))


# ─── Speech to text ───────────────────────────────────────────────────────


async def _transcribe(pcm: bytes, stages: dict[str, int]) -> str:
    """Whisper on the whole recording, with the satellite's second hearing:
    a short-window transcript headed for the tool model is decoded again on
    the 30 s path first (``StreamSession._wants_second_hearing``)."""
    from domovoi.router import (
        goes_to_the_tool_model,
        is_question_about_the_world,
        is_request_for_a_story,
    )

    try:
        whisper = get_whisper_client()
    except SttUnavailableError as e:
        log.warning("phone voice: speech recognition is unavailable: %s", e)
        raise PhoneTurnRefused(
            503,
            "Speech recognition isn't running on this Domovoi. An admin can "
            "check the Whisper settings on the dashboard.",
            "stt_unavailable",
        ) from e
    t0 = time.perf_counter()
    heard, window = await transcribe_with_window(whisper, pcm)
    heard = (heard or "").strip()
    # Decided on the request as it will be routed: a leading "hey Domovoi"
    # would send every turn to the tool model, and so to a second hearing.
    request = strip_wake_phrase(heard)
    if (
        window == SHORT_WINDOW_SEC
        and settings.whisper_short_window_recheck
        and can_decode_full_window(whisper)
        and goes_to_the_tool_model(request)
        and not is_question_about_the_world(request)
        and not is_request_for_a_story(request)
    ):
        try:
            again, _ = await transcribe_full_window(whisper, pcm)
            if any(ch.isalnum() for ch in again or ""):
                heard = again.strip()
                window = FULL_WINDOW_SEC
        except Exception as e:  # noqa: BLE001 — the first hearing stands
            log.warning("phone voice: the second hearing failed: %s", e)
    stages["stt_ms"] = int((time.perf_counter() - t0) * 1000)
    if window is not None:
        stages["stt_window_s"] = int(window)
    return heard


# ─── The reply's audio ────────────────────────────────────────────────────


async def _synthesize(response: Response) -> bytes | None:
    """The reply as WAV, in the household's default voice (or the voice a
    sample asked for). None when there is nothing to say or TTS failed:
    the reply still stands as text."""
    if not (response.text or "").strip():
        return None
    from domovoi.streaming import resolve_voice

    try:
        engine, voice = await resolve_voice(getattr(response, "voice_override", None))
        wav = await get_tts_client().synthesize(response.text, engine=engine, voice=voice)
    except Exception as e:  # noqa: BLE001
        log.warning("phone voice: text-to-speech failed: %s", e)
        return None
    return wav or None


# ─── After the reply: what reaches other rooms ────────────────────────────


def _fan_out(app: Any, response: Response, room: str) -> None:
    """Intercom ("tell the kitchen dinner's ready") works from a phone as
    from any room: the announcement goes to each target room's satellite,
    in the background so the phone hears its confirmation at once."""
    rooms = list(getattr(response, "announce_to_rooms", None) or [])
    message = getattr(response, "announce_text", None)
    if not rooms or not message:
        return
    sessions = getattr(getattr(app, "state", None), "active_sessions", None) or {}

    async def _announce(target: Any, target_room: str) -> None:
        try:
            await target.announce(message)
        except Exception as e:  # noqa: BLE001
            log.warning("phone voice: intercom from %s to %s failed: %s", room, target_room, e)

    for target_room in rooms:
        target = sessions.get(target_room)
        if target is None:
            continue
        task = asyncio.create_task(_announce(target, target_room))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)


# ─── The chat thread ──────────────────────────────────────────────────────

_ROW_COLUMNS = (
    "id, role, content, images, model, error, created_at, stats, device_id, "
    "source, speaker_person_id, speaker_confidence, "
    "(SELECT name FROM devices d WHERE d.device_id = chat_messages.device_id) AS device_name, "
    "(SELECT name FROM people p WHERE p.id = chat_messages.speaker_person_id) AS speaker_name"
)


def message_dict(row: Any) -> dict[str, Any]:
    """A chat_messages row as the chat API reads it out (the web's
    ``_msg_dict``, plus the voice columns)."""
    d = dict(row)
    created = d.get("created_at")
    if created is not None and hasattr(created, "isoformat"):
        d["created_at"] = created.isoformat()
    if isinstance(d.get("stats"), str):
        d["stats"] = json.loads(d["stats"])
    score = d.get("speaker_confidence")
    if score is not None:
        d["speaker_confidence"] = round(float(score), 4)
    d["speaker_confident"] = (
        d.get("speaker_person_id") is not None and speaker_is_confident(d.get("speaker_confidence"))
    )
    return d


async def _thread_exists(thread_id: int) -> bool:
    async with session_scope() as s:
        row = (
            await s.execute(text("SELECT 1 FROM chat_threads WHERE id = :id"), {"id": thread_id})
        ).first()
    return row is not None


async def _write_turn(
    *,
    thread_id: int,
    phone_id: str,
    transcript: str,
    reply: str,
    speaker_person_id: int | None,
    speaker_confidence: float | None,
    model: str | None,
    error: str | None,
    stats: dict[str, Any],
) -> list[dict[str, Any]]:
    """Both turns into the thread, as the typed send writes them (and the
    thread titled by its first message), then ``chat_changed`` so open
    dashboards and apps re-read it.

    The speaker is re-read in the insert itself: the turn may have deleted
    them ("forget me"), or an admin may have while it was answered, and the
    person id voice identification gave before routing would then break
    the foreign key and lose both rows. A speaker who is gone is stored as
    nobody, with no score."""
    from sqlalchemy.exc import IntegrityError

    try:
        async with session_scope() as s:
            user = (
                await s.execute(
                    text(
                        f"""
                        INSERT INTO chat_messages
                            (thread_id, role, content, device_id, source,
                             speaker_person_id, speaker_confidence)
                        VALUES (
                            :t, 'user', :c, :dev, 'voice',
                            (SELECT p.id FROM people p WHERE p.id = CAST(:sp AS BIGINT)),
                            CASE
                                WHEN CAST(:sp AS BIGINT) IS NOT NULL AND NOT EXISTS (
                                    SELECT 1 FROM people p WHERE p.id = CAST(:sp AS BIGINT)
                                ) THEN NULL
                                ELSE CAST(:sc AS REAL)
                            END
                        )
                        RETURNING {_ROW_COLUMNS}
                        """
                    ),
                    {
                        "t": thread_id, "c": transcript, "dev": phone_id,
                        "sp": speaker_person_id, "sc": speaker_confidence,
                    },
                )
            ).mappings().first()
            assistant = (
                await s.execute(
                    text(
                        f"""
                        INSERT INTO chat_messages
                            (thread_id, role, content, model, error, stats, source)
                        VALUES (:t, 'assistant', :c, :m, :e, CAST(:st AS JSONB), 'voice')
                        RETURNING {_ROW_COLUMNS}
                        """
                    ),
                    {
                        "t": thread_id, "c": reply, "m": model, "e": error,
                        "st": json.dumps(stats),
                    },
                )
            ).mappings().first()
            await s.execute(
                text(
                    """
                    UPDATE chat_threads
                       SET updated_at = now(),
                           title = COALESCE(title, LEFT(:c, 60))
                     WHERE id = :t
                    """
                ),
                {"t": thread_id, "c": transcript.strip().splitlines()[0] if transcript.strip() else ""},
            )
            await s.execute(text("SELECT pg_notify('chat_changed', 'message')"))
    except IntegrityError as e:
        if not await _thread_exists(thread_id):
            # The thread was deleted while the turn was being answered.
            raise PhoneTurnRefused(404, "thread not found", "thread_not_found") from e
        log.error("phone voice: the turn into thread %s could not be saved: %s", thread_id, e)
        raise PhoneTurnRefused(
            500, "Domovoi answered, but couldn't save the turn into this chat.", "save_failed",
        ) from e
    return [message_dict(user), message_dict(assistant)]


# ─── The turn ─────────────────────────────────────────────────────────────


def _not_heard(reason: str, room: str) -> dict[str, Any]:
    return {
        "heard": False,
        "reason": reason,
        "transcript": "",
        "reply": "",
        "audio": None,
        "speaker": None,
        "expect_followup": False,
        "actions": [],
        "matched_handler": None,
        "matched_path": None,
        "session_id": None,
        "room_id": room,
        "messages": [],
        "error": None,
    }


async def run_phone_turn(
    app: Any,
    *,
    phone_id: str,
    thread_id: int,
    audio: bytes,
    player: dict[str, Any] | None = None,
    speak: bool = True,
) -> dict[str, Any]:
    """Answer one push-to-talk turn. Raises :class:`PhoneTurnRefused` for
    a turn refused before anything was written. ``speak=False``: the phone
    speaks the reply itself, so none is synthesized (``audio`` is null).
    Otherwise returns::

        {
          "heard": true,               # false: nothing to answer (no rows)
          "reason": null,              # "too_short" | "blank" when not heard
          "transcript": "what time is it",
          "reply": "It's 9:05.",
          "audio": {"mime": "audio/wav", "b64": "..."} | null,
          "speaker": {"person_id": 3, "name": "Ana", "confidence": 0.86,
                      "confident": true} | null,
          "expect_followup": false,    # Domovoi asked a question
          "actions": [ {"type": "play", "items": [...]}, ... ],
          "matched_handler": "clock", "matched_path": "fast",
          "session_id": "…", "room_id": "phone-android-1a2b",
          "messages": [<user row>, <assistant row>],
          "error": null | {"detail": "…", "code": "turn_failed"}
        }
    """
    phone_id = valid_phone_id(phone_id)
    if not 1 <= int(thread_id) <= MAX_THREAD_ID:
        raise PhoneTurnRefused(404, "thread not found", "thread_not_found")
    room = phone_room(phone_id)
    pcm = pcm_from_upload(audio)
    if not await _thread_exists(thread_id):
        raise PhoneTurnRefused(404, "thread not found", "thread_not_found")
    if len(pcm) < MIN_CLIP_SEC * BYTES_PER_SEC:
        return _not_heard("too_short", room)
    if room in _BUSY:
        raise PhoneTurnRefused(
            409, "this phone's last turn is still being answered", "busy",
        )
    _BUSY.add(room)
    try:
        return await _answer(app, room=room, phone_id=phone_id, thread_id=thread_id,
                             pcm=pcm, player=player or {}, speak=speak)
    finally:
        _BUSY.discard(room)


async def _answer(
    app: Any,
    *,
    room: str,
    phone_id: str,
    thread_id: int,
    pcm: bytes,
    player: dict[str, Any],
    speak: bool = True,
) -> dict[str, Any]:
    from domovoi.router import route
    from domovoi.streaming import run_voice_profile_hooks

    started = time.perf_counter()
    stages: dict[str, int] = {"audio_ms": int(len(pcm) * 1000 / BYTES_PER_SEC)}
    transcript = await _transcribe(pcm, stages)
    if not any(ch.isalnum() for ch in transcript):
        # A satellite's blank guard: nothing to route, nothing written.
        log.info("phone voice: blank recording from %s (%.1f s)", room, len(pcm) / BYTES_PER_SEC)
        return _not_heard("blank", room)
    # What is routed (and logged as the intent); the thread keeps what was said.
    request = strip_wake_phrase(transcript)

    t0 = time.perf_counter()
    ident = await _identify(pcm, room)
    stages["identify_ms"] = int((time.perf_counter() - t0) * 1000)
    person_id = getattr(ident, "person_id", None)
    embedding = getattr(ident, "embedding", None)

    probe = getattr(getattr(app, "state", None), "probe", None)
    session_id = session_for(room, thread_id)
    ctx = Context(
        room_id=room,
        session_id=session_id,
        online=bool(getattr(probe, "online", True)),
        bot_name=settings.bot_name,
        person_id=person_id,
        presence_tier=getattr(ident, "presence_tier", None),
        embedding_bytes=embedding.astype("float32").tobytes() if embedding is not None else None,
        trigger="push_to_talk",
        app=app,
        device_kind="phone",
        device_player=player,
    )
    intent = Intent(transcript=request, room_id=room, session_id=session_id)

    response: Response | None = None
    error: dict[str, str] | None = None
    t0 = time.perf_counter()
    try:
        async with session_scope() as s:
            response = await route(intent, ctx, s)
            await run_voice_profile_hooks(
                s, response=response, ctx=ctx, ident=ident, person_id=person_id,
                transcript=request, audio_seconds=len(pcm) / BYTES_PER_SEC,
            )
    except Exception:  # noqa: BLE001 — recorded in the thread, not lost
        log.exception("phone voice: the turn from %s failed", room)
        response = None
        error = {"detail": TURN_FAILED_TEXT, "code": "turn_failed"}
    stages["route_ms"] = int((time.perf_counter() - t0) * 1000)

    reply = response.text if response is not None else ""
    if response is not None:
        remember_session(room, thread_id, response.session_id)

    t0 = time.perf_counter()
    # The phone that speaks the reply itself (its own voice pick) asked for
    # no audio: none is rendered, saving the time and the bytes.
    wav = await _synthesize(response) if response is not None and speak else None
    stages["tts_ms"] = int((time.perf_counter() - t0) * 1000)

    actions = list(getattr(response, "device_actions", None) or []) if response is not None else []
    expect_followup = bool(response is not None and response.expect_followup)
    if response is not None:
        _fan_out(app, response, room)

    score = _speaker_score(ident)
    stats: dict[str, Any] = {
        **stages,
        "model_role": "voice",
        "wall_ms": int((time.perf_counter() - started) * 1000),
    }
    if response is not None:
        stats["matched_handler"] = response.matched_handler
        stats["matched_path"] = response.matched_path
    if error is not None:
        stats["error_code"] = error["code"]
    messages = await _write_turn(
        thread_id=thread_id,
        phone_id=phone_id,
        transcript=transcript,
        reply=reply,
        speaker_person_id=person_id,
        speaker_confidence=score,
        # The Q&A model wrote a "qa" reply; a command's reply is Domovoi's own.
        model=settings.ollama_model if response is not None and response.matched_path == "qa" else None,
        error=error["detail"] if error is not None else None,
        stats={k: v for k, v in stats.items() if v is not None},
    )
    log.info(
        "phone voice: %s thread=%s heard %r%s -> %s/%s (speaker %s %s) in %d ms",
        room, thread_id, transcript,
        f" (routed as {request!r})" if request != transcript else "",
        getattr(response, "matched_handler", None), getattr(response, "matched_path", None),
        person_id, "-" if score is None else f"{score:.3f}", stats["wall_ms"],
    )
    return {
        "heard": True,
        "reason": None,
        "transcript": transcript,
        "reply": reply,
        "audio": (
            {"mime": "audio/wav", "b64": base64.b64encode(wav).decode("ascii")}
            if wav else None
        ),
        # Who the stored row names: nobody when the turn forgot them.
        "speaker": (
            {
                "person_id": messages[0]["speaker_person_id"],
                "name": messages[0].get("speaker_name"),
                "confidence": messages[0].get("speaker_confidence"),
                "confident": messages[0]["speaker_confident"],
            }
            if messages[0].get("speaker_person_id") is not None else None
        ),
        "expect_followup": expect_followup,
        "actions": actions,
        "matched_handler": getattr(response, "matched_handler", None),
        "matched_path": getattr(response, "matched_path", None),
        "session_id": str(response.session_id) if response is not None and response.session_id else None,
        "room_id": room,
        "messages": messages,
        "error": error,
    }
