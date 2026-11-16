"""A longer introduction, and a room's one-time "was that you?"
(wf/voice-intro-longer, 2026-11-16).

1.3.0's speaker model learned a person from ONE "my name is X" — about a
second of speech — and on the owner's device check his own scores in rooms
sat at 0.41-0.61 against the 0.58 bar, with another household member
scoring nearly as close. Now, at a satellite:

* the introduction keeps listening until it has about eight seconds of
  speech, stopping as soon as it has enough: "Nice to meet you! How do you
  spell that?" (the spelling settles the name: theirs is used), then "Last
  bit: count from one to ten …", then, only if still short, "Thanks — one
  more: say 'Good morning Domovoi, what's on today?' …"; it closes with
  "Thanks, X! I'll know your voice now.";
* the voice is ONE sample, saved when the name is settled (as before), its
  embedding replaced by all the clips' together (each unit-norm, averaged,
  normalised again) as more are heard; "stop" / "never mind" keeps it;
* a turn scoring just under the bar for one clearly-best known person, in a
  room with no sample of them, adds "By the way, was that you, X?" once a
  day; only a yes, in that room, within 30 s, saves THAT turn's clip as the
  room's sample (at most three per person per room). Never from a phone.

The satellite turns run through test_voice_id_namesakes.py's real
StreamSession harness; the stub embedder gives each PCM its own vector, and
its speech length is the recording's.
"""

from __future__ import annotations

import hashlib
import time

import numpy as np
import pytest
from sqlalchemy import text

from domovoi import streaming
from domovoi.clients.voice_embedder import StubVoiceEmbedder
from domovoi.db.repositories import SessionRepository, VoiceProfilesRepository
from domovoi.db.session import SessionLocal
from domovoi.handlers import voice_profile
from domovoi.handlers.voice_profile import (
    VoiceProfileHandler,
    _combined,
    _turn_clip,
    maybe_ask_room_check,
)
from domovoi.models import Context, Response
from domovoi.router import first_fast_path, normalize_transcript
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_music_under_capture import ROOM
from domovoi.tests.test_voice_id_namesakes import (  # noqa: F401 — the room fixture
    _people,
    _person,
    _reply,
    _samples,
    room,
)
from domovoi.voice_identifier import (
    OUTCOME_AMBIGUOUS,
    OUTCOME_MATCH,
    OUTCOME_NO_MATCH,
    OUTCOME_NOISY,
    OUTCOME_TOO_SHORT,
    IdentificationResult,
)

SPELL = "Nice to meet you! How do you spell that?"
COUNT = "Last bit: count from one to ten for me, at your normal voice."
SENTENCE = (
    "Thanks — one more: say 'Good morning Domovoi, what's on today?' like you "
    "would at breakfast."
)
COUNTING = "One, two, three, four, five, six, seven, eight, nine, ten."


def _pcm(seconds: float, level: int) -> bytes:
    """``seconds`` of one voice: ``level`` is the voice (see ``voices``)."""
    return np.full(int(16_000 * seconds), level, dtype=np.int16).tobytes()


@pytest.fixture(autouse=True)
def voices(monkeypatch):
    """A stub embedder that hears voices: the plain stub gives every PCM an
    unrelated vector, so two clips of one person would never resemble each
    other. Here a clip's vector is its voice's (the PCM's level, rounded to
    hundreds: 601 and 602 are one person) plus a little of the clip's own,
    about 0.9 apart for one voice and about 0 for two."""
    from domovoi.clients import voice_embedder

    async def embed_print(self, pcm: bytes):
        if not pcm:
            return None
        level = int(np.frombuffer(pcm[:2], dtype=np.int16)[0])
        base = np.random.default_rng(level // 100).standard_normal(voice_embedder.EMBEDDING_DIM)
        own = np.random.default_rng(
            int.from_bytes(hashlib.sha256(pcm).digest()[:8], "big"),
        ).standard_normal(voice_embedder.EMBEDDING_DIM)
        v = (base + 0.3 * own).astype(np.float32)
        return voice_embedder.VoicePrint(
            current=v / np.linalg.norm(v), snr_db=None, speech_sec=len(pcm) / 32_000,
        )

    monkeypatch.setattr(StubVoiceEmbedder, "embed_print", embed_print)


async def _vec(pcm: bytes) -> np.ndarray:
    return (await StubVoiceEmbedder().embed(pcm)).astype(np.float32)


def _together(*vecs: np.ndarray) -> np.ndarray:
    unit = [v / np.linalg.norm(v) for v in vecs]
    mean = np.mean(unit, axis=0)
    return (mean / np.linalg.norm(mean)).astype(np.float32)


async def _sample_rows(pid: int) -> list[tuple[bytes, str | None, float | None]]:
    async with SessionLocal() as s:
        rows = (await s.execute(text(
            "SELECT embedding, room_id, sample_seconds FROM voice_profiles "
            "WHERE person_id = :p ORDER BY id"
        ), {"p": pid})).all()
    return [(bytes(r[0]), r[1], r[2]) for r in rows]


# ─── The introduction at a satellite ──────────────────────────────────────


@requires_db
async def test_a_long_speller_is_done_after_the_spelling(room) -> None:
    """Eight seconds by the spelling: no counting asked for."""
    intro, spelled = _pcm(5.0, 501), _pcm(4.0, 502)
    asked = await room.say("My name is Sarah.", intro)
    assert _reply(asked) == SPELL
    assert asked["expect_followup"] is True
    assert await _people() == []

    done = await room.say("S A R A H", spelled, trigger="followup")
    assert _reply(done) == "Thanks, Sarah! I'll know your voice now."
    assert done["expect_followup"] is False
    assert await room.pending() is None
    people = await _people()
    assert [n for _p, n in people] == ["Sarah"]
    rows = await _sample_rows(people[0][0])
    assert len(rows) == 1
    blob, room_id, secs = rows[0]
    assert room_id == ROOM and secs == pytest.approx(9.0)
    # ONE sample: both clips together, each unit-norm, averaged, normalised.
    saved = np.frombuffer(blob, dtype=np.float32)
    np.testing.assert_allclose(saved, _together(await _vec(intro), await _vec(spelled)), atol=1e-6)


@requires_db
async def test_a_short_name_goes_on_to_counting_then_one_sentence(room) -> None:
    intro, spelled, counted, sentence = (
        _pcm(1.0, 601), _pcm(1.0, 602), _pcm(3.0, 603), _pcm(1.0, 604),
    )
    assert _reply(await room.say("My name is Sarah.", intro)) == SPELL
    saved = await room.say("S. A. R. A. H.", spelled, trigger="followup")
    # The name is settled and the voice saved (as the yes always did) ...
    assert _reply(saved) == COUNT
    assert saved["expect_followup"] is True
    people = await _people()
    assert [n for _p, n in people] == ["Sarah"]
    pid = people[0][0]
    assert len(await _sample_rows(pid)) == 1
    assert (await room.pending())["stage"] == "count"

    # ... and five seconds is still short of eight.
    more = await room.say(COUNTING, counted, trigger="followup")
    assert _reply(more) == SENTENCE
    assert (await room.pending())["stage"] == "sentence"

    # The sentence ends it, enough or not.
    done = await room.say("Good morning Domovoi, what's on today?", sentence, trigger="followup")
    assert _reply(done) == "Thanks, Sarah! I'll know your voice now."
    assert await room.pending() is None
    rows = await _sample_rows(pid)
    assert len(rows) == 1
    blob, _room, secs = rows[0]
    assert secs == pytest.approx(6.0)
    vecs = [await _vec(p) for p in (intro, spelled, counted, sentence)]
    np.testing.assert_allclose(np.frombuffer(blob, dtype=np.float32), _together(*vecs), atol=1e-6)


@requires_db
async def test_counting_that_reaches_eight_seconds_ends_it(room) -> None:
    await room.say("My name is Sarah.", _pcm(1.0, 611))
    await room.say("S A R A H", _pcm(1.0, 612), trigger="followup")
    done = await room.say(COUNTING, _pcm(6.0, 613), trigger="followup")
    assert _reply(done) == "Thanks, Sarah! I'll know your voice now."
    assert await room.pending() is None


@requires_db
async def test_somebody_else_counting_is_not_mixed_in(room) -> None:
    """Another voice (unknown to the house) counts instead: it would have
    made eight seconds, but it doesn't sound like the introduction, so it
    counts as if it were too short and the sentence is asked for."""
    intro, spelled = _pcm(1.0, 691), _pcm(1.5, 692)
    await room.say("My name is Sarah.", intro)
    assert _reply(await room.say("S A R A H", spelled, trigger="followup")) == COUNT
    other = await room.say(COUNTING, _pcm(6.0, 901), trigger="followup")
    assert _reply(other) == SENTENCE
    people = await _people()
    rows = await _sample_rows(people[0][0])
    assert len(rows) == 1 and rows[0][2] == pytest.approx(2.5)
    np.testing.assert_allclose(
        np.frombuffer(rows[0][0], dtype=np.float32),
        _together(await _vec(intro), await _vec(spelled)), atol=1e-6,
    )


def test_a_clip_counts_only_when_it_resembles_the_clips_so_far(monkeypatch) -> None:
    a = np.zeros(voice_profile.EMBEDDING_DIM, dtype=np.float32)
    a[0] = 1.0
    near = a.copy()
    near[1] = 1.0                        # cosine 0.707 to a
    far = np.zeros_like(a)
    far[2] = 1.0                         # cosine 0
    clips = [[voice_profile._b64_embedding(a), 1.0]]
    assert voice_profile._resembles(clips, [voice_profile._b64_embedding(near), 1.0])
    assert not voice_profile._resembles(clips, [voice_profile._b64_embedding(far), 1.0])
    monkeypatch.setattr(voice_profile.settings, "voice_profile_intro_clip_min_similarity", 0.8)
    assert not voice_profile._resembles(clips, [voice_profile._b64_embedding(near), 1.0])


@requires_db
async def test_stop_keeps_what_was_heard(room) -> None:
    intro, spelled = _pcm(1.0, 621), _pcm(1.0, 622)
    await room.say("My name is Sarah.", intro)
    await room.say("S A R A H", spelled, trigger="followup")
    stopped = await room.say("Stop.", _pcm(1.0, 623), trigger="followup")
    assert _reply(stopped) == "Okay, I'll stop there. I'll still know your voice, Sarah."
    assert await room.pending() is None
    people = await _people()
    rows = await _sample_rows(people[0][0])
    assert len(rows) == 1
    # The two clips heard before the stop, not the stop itself.
    np.testing.assert_allclose(
        np.frombuffer(rows[0][0], dtype=np.float32),
        _together(await _vec(intro), await _vec(spelled)), atol=1e-6,
    )


@requires_db
async def test_never_mind_while_counting_and_before_the_name_is_settled(room) -> None:
    await room.say("My name is Sarah.", _pcm(1.0, 631))
    await room.say("S A R A H", _pcm(1.0, 632), trigger="followup")
    done = await room.say("Never mind.", _pcm(1.0, 633), trigger="followup")
    assert _reply(done) == "Okay, I'll stop there. I'll still know your voice, Sarah."
    assert [n for _p, n in await _people()] == ["Sarah"]

    # Before the name is settled nothing is saved, as before.
    await room.say("My name is Priya.", _pcm(1.0, 834))
    assert _reply(await room.say("Never mind.", _pcm(1.0, 834), trigger="followup")) == (
        "Okay, I won't save that."
    )
    assert [n for _p, n in await _people()] == ["Sarah"]


@requires_db
async def test_the_spelling_corrects_the_heard_name(room) -> None:
    await room.say("My name is Sara.", _pcm(1.0, 641))
    saved = await room.say("S A R A H", _pcm(1.0, 642), trigger="followup")
    assert _reply(saved) == COUNT
    assert [n for _p, n in await _people()] == ["Sarah"]


@requires_db
async def test_yes_to_the_spelling_question_keeps_the_heard_name(room) -> None:
    await room.say("My name is Priya.", _pcm(1.0, 651))
    assert _reply(await room.say("Yes.", _pcm(1.0, 652), trigger="followup")) == COUNT
    assert [n for _p, n in await _people()] == ["Priya"]


@requires_db
async def test_no_its_another_name_is_still_read_back(room) -> None:
    await room.say("My name is Jordan.", _pcm(1.0, 661))
    again = await room.say("No, it's Priya.", _pcm(1.0, 662), trigger="followup")
    assert _reply(again) == "Got it, Priya: P, R, I, Y, A. Is that right?"
    assert await _people() == []
    assert _reply(await room.say("Yes.", _pcm(1.0, 663), trigger="followup")) == COUNT
    assert [n for _p, n in await _people()] == ["Priya"]


@requires_db
async def test_a_command_instead_of_counting_ends_the_introduction(room) -> None:
    await room.say("My name is Sarah.", _pcm(1.0, 671))
    await room.say("S A R A H", _pcm(1.0, 672), trigger="followup")
    other = await room.say("Who am I?", _pcm(1.0, 672))
    assert other["matched_handler"] == "voice_profile"
    assert "count" not in _reply(other).lower()
    assert await room.pending() is None
    assert len(await _sample_rows((await _people())[0][0])) == 1


@requires_db
async def test_a_reintroduction_for_someone_known_collects_too(room) -> None:
    """1.3.0's relearn (V061): the person exists with no current sample, so
    "my name is Priya" asks "Is that you?", and the yes goes on listening."""
    priya = await _person("Priya")
    asked = await room.say("My name is Priya.", _pcm(4.0, 681))
    assert _reply(asked) == "I already know someone called Priya. Is that you?"
    done = await room.say("Yes, that's me.", _pcm(4.0, 682), trigger="followup")
    assert _reply(done) == "Thanks, Priya! I'll know your voice now."
    assert await _samples(priya) == [("satellite", ROOM)]


def test_counting_is_not_a_command() -> None:
    """The counting turn is a clip, not something the router would take."""
    for said in (COUNTING, "one two three four five six seven eight nine ten", "1, 2, 3, 4, 5"):
        assert first_fast_path(normalize_transcript(said)) is None, said


# ─── Clips ────────────────────────────────────────────────────────────────


def test_clips_are_combined_unit_norm_then_normalised() -> None:
    a = np.zeros(voice_profile.EMBEDDING_DIM, dtype=np.float32)
    b = np.zeros(voice_profile.EMBEDDING_DIM, dtype=np.float32)
    a[0], b[1] = 3.0, 0.5   # different lengths count the same
    clips = [[voice_profile._b64_embedding(a), 1.0], [voice_profile._b64_embedding(b), 2.0]]
    together = _combined(clips)
    expected = np.zeros(voice_profile.EMBEDDING_DIM, dtype=np.float32)
    expected[0] = expected[1] = 1 / np.sqrt(2)
    np.testing.assert_allclose(together, expected, atol=1e-6)
    assert voice_profile._speech_of(clips) == 3.0
    assert _combined([]) is None


def _ctx(**kw) -> Context:
    emb = np.ones(voice_profile.EMBEDDING_DIM, dtype=np.float32)
    base = dict(room_id="kitchen", embedding_bytes=emb.tobytes(), voice_speech_sec=2.5,
                voice_outcome=OUTCOME_NO_MATCH)
    base.update(kw)
    return Context(**base)


def test_which_turns_count_towards_the_introduction() -> None:
    assert _turn_clip(_ctx(), expected=None)[1] == 2.5
    # Too short: no voiceprint at all.
    assert _turn_clip(_ctx(embedding_bytes=None, voice_outcome=OUTCOME_TOO_SHORT), expected=None) is None
    # Music under it.
    assert _turn_clip(_ctx(voice_outcome=OUTCOME_NOISY), expected=None) is None
    # Somebody else answered.
    assert _turn_clip(_ctx(person_id=7, voice_outcome=OUTCOME_MATCH), expected=3) is None
    assert _turn_clip(_ctx(person_id=3, voice_outcome=OUTCOME_MATCH), expected=3) is not None
    assert _turn_clip(
        _ctx(voice_candidates=[5, 6], voice_outcome=OUTCOME_AMBIGUOUS), expected=None,
    ) is None
    # The introduction's own clip is kept as it always was; under music its
    # speech just isn't counted.
    assert _turn_clip(_ctx(voice_outcome=OUTCOME_NOISY), expected=None, first=True)[1] == 0.0


def test_the_phone_keeps_the_one_clip_introduction() -> None:
    """The chat mic's introduction is unchanged (test_phone_voice_profile.py
    runs it end to end): nothing to collect there."""
    assert voice_profile._collecting({"origin": "phone", "clips": []}) is False
    assert voice_profile._collecting({"origin": "satellite", "clips": []}) is True
    assert voice_profile._collecting({"origin": "satellite"}) is False


# ─── "By the way, was that you, X?" ───────────────────────────────────────


@pytest.fixture
def quiet_rooms():
    voice_profile._ROOM_CHECK_ASKED.clear()
    yield
    voice_profile._ROOM_CHECK_ASKED.clear()


async def _known_person(db_session, *rooms: str) -> tuple[int, np.ndarray]:
    pid = int((await db_session.execute(
        text("INSERT INTO people (name) VALUES ('Priya') RETURNING id"),
    )).scalar_one())
    repo = VoiceProfilesRepository(db_session)
    for i, r in enumerate(rooms or ("office",)):
        await repo.add(
            person_id=pid, embedding=(await _vec(_pcm(1.0, 700 + i))).tobytes(),
            model=repo.EMBEDDING_MODEL_TAG, room_id=r, sample_seconds=1.0,
        )
    await db_session.commit()
    return pid, await _vec(_pcm(1.5, 799))


def _ident(pid: int, voice: np.ndarray, **kw) -> IdentificationResult:
    base = dict(
        person_id=None, presence_tier="high", embedding=voice, best_similarity=0.55,
        runner_up_similarity=0.40, outcome=OUTCOME_NO_MATCH, best_person_id=pid,
        runner_up_person_id=None, speech_seconds=1.5,
    )
    base.update(kw)
    return IdentificationResult(**base)


async def _turn(db_session, room_id: str = "kitchen", **ctx_kw) -> tuple[Context, Response]:
    sid = await SessionRepository(db_session).get_or_create(None, room_id)
    await db_session.commit()
    ctx = Context(room_id=room_id, session_id=sid, **ctx_kw)
    return ctx, Response(text="It's five o'clock.", session_id=sid, matched_handler="time")


async def _pending(db_session, sid):
    return (await SessionRepository(db_session).get_context(sid) or {}).get("pending_confirmation")


@requires_db
async def test_a_grey_zone_turn_asks_once_and_yes_saves_that_clip_to_that_room(
    db_session, quiet_rooms,
) -> None:
    pid, voice = await _known_person(db_session)
    ctx, response = await _turn(db_session)
    assert await maybe_ask_room_check(db_session, ctx=ctx, response=response, ident=_ident(pid, voice))
    assert response.text == "It's five o'clock. By the way, was that you, Priya?"
    assert response.expect_followup is True
    pending = await _pending(db_session, ctx.session_id)
    assert pending["kind"] == "core.room_check" and pending["person_id"] == pid

    done = await VoiceProfileHandler().handle_choice_reply(
        "core.room_check", pending, "Yes.", ctx, db_session,
    )
    assert done.text == "Done — I'll know your voice in this room now, Priya."
    rows = (await db_session.execute(text(
        "SELECT embedding, room_id, claimed_at IS NOT NULL FROM voice_profiles "
        "WHERE person_id = :p ORDER BY id"
    ), {"p": pid})).all()
    assert [(r[1], r[2]) for r in rows] == [("office", False), ("kitchen", True)]
    assert bytes(rows[1][0]) == voice.tobytes()   # exactly that turn's clip


@requires_db
async def test_no_saves_nothing_and_the_room_stays_quiet_for_a_day(
    db_session, quiet_rooms, monkeypatch,
) -> None:
    pid, voice = await _known_person(db_session)
    ctx, response = await _turn(db_session)
    assert await maybe_ask_room_check(db_session, ctx=ctx, response=response, ident=_ident(pid, voice))
    pending = await _pending(db_session, ctx.session_id)
    no = await VoiceProfileHandler().handle_choice_reply(
        "core.room_check", pending, "No.", ctx, db_session,
    )
    assert no.text == "Okay, my mistake."
    # Anything else isn't an answer, and saves nothing either.
    assert await VoiceProfileHandler().handle_choice_reply(
        "core.room_check", pending, "play some jazz", ctx, db_session,
    ) is None
    count = (await db_session.execute(text(
        "SELECT count(*) FROM voice_profiles WHERE person_id = :p"), {"p": pid})).scalar_one()
    assert count == 1

    await SessionRepository(db_session).set_context_key(ctx.session_id, "pending_confirmation", None)
    ctx2, again = await _turn(db_session)
    assert not await maybe_ask_room_check(db_session, ctx=ctx2, response=again, ident=_ident(pid, voice))
    assert again.text == "It's five o'clock."
    # A day later it may ask again.
    real = time.time
    monkeypatch.setattr(voice_profile.time, "time", lambda: real() + 24 * 3600 + 5)
    ctx3, later = await _turn(db_session)
    assert await maybe_ask_room_check(db_session, ctx=ctx3, response=later, ident=_ident(pid, voice))


@requires_db
async def test_a_late_yes_saves_nothing(db_session, quiet_rooms, monkeypatch) -> None:
    pid, voice = await _known_person(db_session)
    ctx, response = await _turn(db_session)
    await maybe_ask_room_check(db_session, ctx=ctx, response=response, ident=_ident(pid, voice))
    pending = await _pending(db_session, ctx.session_id)
    pending["expires_at"] = time.time() - 1
    late = await VoiceProfileHandler().handle_choice_reply(
        "core.room_check", pending, "yes", ctx, db_session,
    )
    assert late.text == "That was a while ago, so I didn't save it."
    count = (await db_session.execute(text(
        "SELECT count(*) FROM voice_profiles WHERE person_id = :p"), {"p": pid})).scalar_one()
    assert count == 1


@requires_db
async def test_a_room_keeps_at_most_three_samples_from_yeses(db_session, quiet_rooms) -> None:
    pid, voice = await _known_person(db_session, "office", "kitchen", "kitchen", "kitchen")
    ctx, _response = await _turn(db_session)
    payload = {
        "person_id": pid, "name": "Priya", "embedding_b64": voice_profile._b64_embedding(voice),
        "expires_at": time.time() + 30, "origin": "satellite", "origin_room": "kitchen",
    }
    done = await VoiceProfileHandler().handle_choice_reply(
        "core.room_check", payload, "yes", ctx, db_session,
    )
    assert done.text == "Thanks, Priya. I already know your voice in this room."
    count = (await db_session.execute(text(
        "SELECT count(*) FROM voice_profiles WHERE person_id = :p AND room_id = 'kitchen'"),
        {"p": pid})).scalar_one()
    assert count == 3


@requires_db
async def test_a_yes_from_another_room_is_not_an_answer(db_session, quiet_rooms) -> None:
    pid, voice = await _known_person(db_session)
    ctx, response = await _turn(db_session)
    await maybe_ask_room_check(db_session, ctx=ctx, response=response, ident=_ident(pid, voice))
    pending = await _pending(db_session, ctx.session_id)
    elsewhere = ctx.model_copy(update={"room_id": "office"})
    assert await VoiceProfileHandler().handle_choice_reply(
        "core.room_check", pending, "yes", elsewhere, db_session,
    ) is None


NO_ASK = [
    ("below the floor", {"ident": {"best_similarity": 0.49}}),
    ("at the bar (a match)", {"ident": {"best_similarity": 0.58}}),
    ("runner-up too close", {"ident": {"runner_up_similarity": 0.52}}),
    ("too short", {"ident": {"outcome": OUTCOME_TOO_SHORT, "embedding": None}}),
    ("short speech", {"ident": {"speech_seconds": 0.6}}),
    ("under music", {"ident": {"outcome": OUTCOME_NOISY}}),
    ("two people too close", {"ident": {"outcome": OUTCOME_AMBIGUOUS, "ambiguous": True}}),
    ("an opted-out voice", {"ident": {"denylisted": True}}),
    ("the room has a sample", {"rooms": ("office", "kitchen")}),
    ("an introduction", {"response": {"matched_handler": "voice_profile"}}),
    ("another question", {"response": {"expect_followup": True}}),
    ("a silent reply", {"response": {"text": ""}}),
    ("a phone", {"room": "phone-7", "ctx": {"device_kind": "phone", "trigger": "push_to_talk"}}),
    ("a question parked", {"pending": True}),
    ("a long capture", {"ctx": {"long_capture": True}}),
]


@requires_db
@pytest.mark.parametrize("why,change", NO_ASK, ids=[w for w, _c in NO_ASK])
async def test_no_ask_when_any_condition_fails(db_session, quiet_rooms, why, change) -> None:
    pid, voice = await _known_person(db_session, *change.get("rooms", ()))
    ctx, response = await _turn(db_session, change.get("room", "kitchen"), **change.get("ctx", {}))
    for key, value in change.get("response", {}).items():
        setattr(response, key, value)
    if change.get("pending"):
        await SessionRepository(db_session).set_context_key(
            ctx.session_id, "pending_confirmation", {"handler": "timer", "kind": "core.x"},
        )
    before = response.text
    ident = _ident(pid, voice, **change.get("ident", {}))
    assert not await maybe_ask_room_check(db_session, ctx=ctx, response=response, ident=ident), why
    assert response.text == before
    assert voice_profile._ROOM_CHECK_ASKED == {}


@requires_db
async def test_the_ask_rides_on_the_turns_hooks(db_session, quiet_rooms) -> None:
    """run_voice_profile_hooks (a satellite's turn and a phone's) asks it
    for an unnamed voice, and the turn's log row says what was heard."""
    pid, voice = await _known_person(db_session)
    ctx, response = await _turn(db_session)
    await streaming.run_voice_profile_hooks(
        db_session, response=response, ctx=ctx, ident=_ident(pid, voice), person_id=None,
        transcript="what time is it", audio_seconds=1.5,
    )
    assert response.text.endswith("By the way, was that you, Priya?")
    # Not for a named voice.
    voice_profile._ROOM_CHECK_ASKED.clear()
    ctx2, named = await _turn(db_session)
    await streaming.run_voice_profile_hooks(
        db_session, response=named, ctx=ctx2,
        ident=_ident(pid, voice, person_id=pid, outcome=OUTCOME_MATCH), person_id=pid,
        transcript="what time is it", audio_seconds=1.5,
    )
    assert named.text == "It's five o'clock."
