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
    voice_profile._ROOM_LEARN_OFFERED.clear()
    yield
    voice_profile._ROOM_CHECK_ASKED.clear()
    voice_profile._ROOM_LEARN_OFFERED.clear()


async def _known_person(
    db_session, *rooms: str, secs: float = 1.0, name: str = "Priya", level: int = 700,
) -> tuple[int, np.ndarray]:
    """``name`` with a ``secs`` sample per room in voice ``level`` (a
    hundred's worth of levels is one voice), and a fresh clip of that voice."""
    pid = int((await db_session.execute(
        text("INSERT INTO people (name) VALUES (:n) RETURNING id"), {"n": name},
    )).scalar_one())
    repo = VoiceProfilesRepository(db_session)
    for i, r in enumerate(rooms or ("office",)):
        await repo.add(
            person_id=pid, embedding=(await _vec(_pcm(1.0, level + i))).tobytes(),
            model=repo.EMBEDDING_MODEL_TAG, room_id=r, sample_seconds=secs,
        )
    await db_session.commit()
    return pid, await _vec(_pcm(1.5, level + 99))


async def _voice(level: int) -> np.ndarray:
    """Another clip of voice ``level``."""
    return await _vec(_pcm(1.2, level))


def _yes(ctx: Context, voice: np.ndarray | None, **kw) -> Context:
    """The yes turn, heard in ``voice`` (None: too short to embed)."""
    update = dict(
        embedding_bytes=None if voice is None else voice.astype(np.float32).tobytes(),
        voice_outcome=OUTCOME_TOO_SHORT if voice is None else OUTCOME_NO_MATCH,
        voice_speech_sec=0.4 if voice is None else 1.2,
    )
    update.update(kw)
    return ctx.model_copy(update=update)


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
        "core.room_check", pending, "Yes.", _yes(ctx, await _voice(788)), db_session,
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
    """A question asked before the room learned a good sample, answered
    after: the room has three, so nothing is added (and the good one is
    never replaced)."""
    pid, voice = await _known_person(db_session, "office", "kitchen", "kitchen", "kitchen")
    await db_session.execute(text(
        "UPDATE voice_profiles SET sample_seconds = 5.0 WHERE id = (SELECT max(id) "
        "FROM voice_profiles WHERE person_id = :p AND room_id = 'kitchen')"), {"p": pid})
    await db_session.commit()
    ctx, _response = await _turn(db_session)
    payload = {
        "person_id": pid, "name": "Priya", "embedding_b64": voice_profile._b64_embedding(voice),
        "score": 0.55, "expires_at": time.time() + 30, "origin": "satellite",
        "origin_room": "kitchen",
    }
    done = await VoiceProfileHandler().handle_choice_reply(
        "core.room_check", payload, "yes", _yes(ctx, await _voice(788)), db_session,
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
    # Only short samples there ask (1.4.1); a good one doesn't.
    ("the room has a good sample", {"rooms": ("office", "kitchen"), "secs": 5.0}),
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
    pid, voice = await _known_person(
        db_session, *change.get("rooms", ()), secs=change.get("secs", 1.0),
    )
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


# ─── Only the asked person's voice may answer (1.4.1) ─────────────────────
#
# Owner decision 2026-11-22. The yes is scored like any turn in the room:
# its score for X must be at least the asking turn's minus
# ROOM_CHECK_YES_SLACK, and nobody else may score higher or over their bar.

REFUSED_OTHER = "That didn't sound like Priya, so I won't save it. I'll ask Priya myself next time."


async def _asked(db_session, *rooms: str, secs: float = 1.0):
    pid, voice = await _known_person(db_session, *rooms, secs=secs)
    ctx, response = await _turn(db_session)
    assert await maybe_ask_room_check(db_session, ctx=ctx, response=response, ident=_ident(pid, voice))
    return pid, ctx, await _pending(db_session, ctx.session_id)


async def _count(db_session, pid: int) -> int:
    return (await db_session.execute(text(
        "SELECT count(*) FROM voice_profiles WHERE person_id = :p"), {"p": pid})).scalar_one()


@requires_db
async def test_another_enrolled_persons_yes_is_refused_and_saves_nothing(db_session, quiet_rooms) -> None:
    sarah, _sv = await _known_person(db_session, "kitchen", secs=10.0, name="Sarah", level=500)
    pid, ctx, pending = await _asked(db_session)
    sarah_before = await _count(db_session, sarah)
    no = await VoiceProfileHandler().handle_choice_reply(
        "core.room_check", pending, "Yes.", _yes(ctx, await _voice(588)), db_session,
    )
    assert no.text == REFUSED_OTHER
    assert not no.expect_followup
    assert await _count(db_session, pid) == 1
    assert await _count(db_session, sarah) == sarah_before


@requires_db
async def test_a_yes_too_short_to_tell_whose_it_is_saves_nothing(db_session, quiet_rooms) -> None:
    pid, ctx, pending = await _asked(db_session)
    no = await VoiceProfileHandler().handle_choice_reply(
        "core.room_check", pending, "Yes.", _yes(ctx, None), db_session,
    )
    assert no.text == (
        "I couldn't hear enough to be sure that was you, so I won't save it. "
        "I'll ask Priya myself next time."
    )
    assert await _count(db_session, pid) == 1


@requires_db
async def test_an_unknown_voice_that_isnt_priya_is_refused(db_session, quiet_rooms) -> None:
    pid, ctx, pending = await _asked(db_session)
    no = await VoiceProfileHandler().handle_choice_reply(
        "core.room_check", pending, "Yes.", _yes(ctx, await _voice(388)), db_session,
    )
    assert no.text == REFUSED_OTHER
    assert await _count(db_session, pid) == 1


# ─── A room with only short samples asks again (1.4.1) ────────────────────
#
# Its yes OFFERS the learning; the counting starts on a second yes (owner
# decision 2026-11-28: offer first). ROOM_CHECK_LEARNING = "start" is the
# test hook for 1.4.1's start-at-once.

LEARN_AFTER_CHECK = (
    "Thanks, Priya. Let me learn your voice better here: count from one to "
    "ten for me, at your normal voice."
)
OFFER = "Thanks. Want me to learn your voice better here? It takes about a minute."
LEARN_AFTER_OFFER = (
    "Great. Let me learn your voice better here: count from one to ten for "
    "me, at your normal voice."
)


async def _offered(db_session) -> tuple[int, Context, dict]:
    """Priya asked in a kitchen with one short sample of her, her bare yes
    answered: the learning offered and parked."""
    pid, ctx, pending = await _asked(db_session, "office", "kitchen")
    offer = await VoiceProfileHandler().handle_choice_reply(
        "core.room_check", pending, "Yes.", _yes(ctx, await _voice(788)), db_session,
    )
    assert offer.text == OFFER and offer.expect_followup is True
    parked = await _pending(db_session, ctx.session_id)
    assert parked["kind"] == "core.room_learn_offer" and parked["person_id"] == pid
    return pid, ctx, parked


@requires_db
async def test_a_short_sample_rooms_bare_yes_offers_and_a_second_yes_learns_the_room(
    db_session, quiet_rooms,
) -> None:
    """Priya's kitchen sample is one second long: the kitchen asks on a
    near-miss, her yes offers the learning (nothing saved, nothing
    started), and her second yes starts the counting that replaces that
    sample (only with more speech: the "Is that you?" tests below run the
    rest). Nothing is replaced yet; her office sample and other people's
    samples are not touched."""
    sarah, _sv = await _known_person(db_session, "kitchen", secs=1.0, name="Sarah", level=500)
    pid, ctx, offer = await _offered(db_session)
    before = (await db_session.execute(text(
        "SELECT id, room_id, sample_seconds FROM voice_profiles WHERE person_id = ANY(:p) ORDER BY id"),
        {"p": [pid, sarah]})).all()
    assert await _count(db_session, pid) == 2     # the offer saved nothing
    assert (pid, "kitchen") in voice_profile._ROOM_LEARN_OFFERED

    learn = await VoiceProfileHandler().handle_choice_reply(
        "core.room_learn_offer", offer, "Yes.", _yes(ctx, await _voice(789)), db_session,
    )
    assert learn.text == LEARN_AFTER_OFFER and learn.expect_followup is True
    assert (pid, "kitchen") not in voice_profile._ROOM_LEARN_OFFERED
    collecting = await _pending(db_session, ctx.session_id)
    assert collecting["kind"] == "core.self_intro" and collecting["stage"] == "count"
    assert collecting["learn_room"] == "kitchen"
    # Seeded with the clip the question was about.
    assert collecting["clips"] == offer["clips"]
    priya_kitchen = (await db_session.execute(text(
        "SELECT id FROM voice_profiles WHERE person_id = :p AND room_id = 'kitchen'"),
        {"p": pid})).scalars().all()
    assert collecting["replace_ids"] == [int(i) for i in priya_kitchen]
    after = (await db_session.execute(text(
        "SELECT id, room_id, sample_seconds FROM voice_profiles WHERE person_id = ANY(:p) ORDER BY id"),
        {"p": [pid, sarah]})).all()
    assert after == before


@requires_db
@pytest.mark.parametrize("reply", ["No.", "late", "what time is it"])
async def test_a_no_a_late_yes_or_anything_else_ends_the_offer_for_a_week(
    db_session, quiet_rooms, monkeypatch, reply,
) -> None:
    """The offer ends unanswered, and the room doesn't offer it to her
    again for seven days: the clip she already confirmed is kept as a
    short sample (review 5); the next day's yes saves its clip as a plain
    yes does (at most three); a week later it offers again."""
    pid, ctx, offer = await _offered(db_session)
    handler = VoiceProfileHandler()
    if reply == "late":
        offer["expires_at"] = time.time() - 1
        out = await handler.handle_choice_reply("core.room_learn_offer", offer, "yes", ctx, db_session)
        assert out.text == "That was a while ago, so I'll leave it for now."
    elif reply == "No.":
        out = await handler.handle_choice_reply("core.room_learn_offer", offer, reply, ctx, db_session)
        assert out.text == "Okay, I'll leave it."
    else:
        assert await handler.handle_choice_reply(
            "core.room_learn_offer", offer, reply, ctx, db_session,
        ) is None
    assert await _count(db_session, pid) == 3
    newest = (await db_session.execute(text(
        "SELECT embedding, room_id, sample_seconds FROM voice_profiles WHERE person_id = :p "
        "ORDER BY id DESC LIMIT 1"), {"p": pid})).one()
    assert newest[1] == "kitchen" and newest[2] == pytest.approx(1.5)
    assert voice_profile._b64_embedding(np.frombuffer(bytes(newest[0]), dtype=np.float32)) == (
        offer["clips"][0][0]
    )
    await SessionRepository(db_session).set_context_key(ctx.session_id, "pending_confirmation", None)

    real = time.time
    monkeypatch.setattr(voice_profile.time, "time", lambda: real() + 24 * 3600 + 5)
    ctx2, response = await _turn(db_session)
    assert await maybe_ask_room_check(db_session, ctx=ctx2, response=response, ident=_ident(pid, await _voice(790)))
    pending = await _pending(db_session, ctx2.session_id)
    yes = await handler.handle_choice_reply(
        "core.room_check", pending, "Yes.", _yes(ctx2, await _voice(791)), db_session,
    )
    assert yes.text == "Done — I'll know your voice in this room now, Priya."
    assert not yes.expect_followup
    # Nothing new parked (the router clears the answered question itself).
    assert (await _pending(db_session, ctx2.session_id))["kind"] == "core.room_check"
    assert await _count(db_session, pid) == 4

    monkeypatch.setattr(voice_profile.time, "time", lambda: real() + 8 * 24 * 3600)
    ctx3, response = await _turn(db_session)
    assert await maybe_ask_room_check(db_session, ctx=ctx3, response=response, ident=_ident(pid, await _voice(792)))
    pending = await _pending(db_session, ctx3.session_id)
    again = await handler.handle_choice_reply(
        "core.room_check", pending, "Yes.", _yes(ctx3, await _voice(793)), db_session,
    )
    assert again.text == OFFER


@requires_db
async def test_the_start_hook_starts_the_learning_on_the_first_yes(
    db_session, quiet_rooms, monkeypatch,
) -> None:
    """ROOM_CHECK_LEARNING = "start" (a test hook): 1.4.1's behaviour."""
    monkeypatch.setattr(voice_profile, "ROOM_CHECK_LEARNING", "start")
    pid, ctx, pending = await _asked(db_session, "office", "kitchen")
    assert pending["speech_sec"] == 1.5
    learn = await VoiceProfileHandler().handle_choice_reply(
        "core.room_check", pending, "Yes.", _yes(ctx, await _voice(788)), db_session,
    )
    assert learn.text == LEARN_AFTER_CHECK and learn.expect_followup is True
    collecting = await _pending(db_session, ctx.session_id)
    assert collecting["kind"] == "core.self_intro" and collecting["stage"] == "count"
    assert collecting["learn_room"] == "kitchen"
    priya_kitchen = (await db_session.execute(text(
        "SELECT id FROM voice_profiles WHERE person_id = :p AND room_id = 'kitchen'"),
        {"p": pid})).scalars().all()
    assert collecting["replace_ids"] == [int(i) for i in priya_kitchen]
    assert await _count(db_session, pid) == 2


# ─── "Yes, wrong song though. Play X" (2026-11-28) ────────────────────────
#
# The owner's dining room: the yes to "was that you, X?" carried a request,
# and the whole turn was taken as the yes — the counting started, and the
# play was lost. Now the yes counts, the request is routed as if said
# alone, in the same turn, and no learning is offered or started.

THE_CASE = "Yes, wrong song though. Play, you should be here."


async def _route_said(db_session, ctx: Context, said: str, monkeypatch) -> tuple[Response, list[str]]:
    """``said`` through the REAL router, as the turn after the question:
    the reply, and every transcript the router looked for a fast path in
    and found one (handler name: transcript)."""
    from domovoi import router as router_module
    from domovoi.models import Intent

    seen: list[str] = []
    real = router_module.first_fast_path

    def spy(transcript, handlers=None):
        hit = real(transcript, handlers)
        if hit is not None:
            seen.append(f"{hit[0].name}: {transcript}")
        return hit

    monkeypatch.setattr(router_module, "first_fast_path", spy)
    response = await router_module.route(
        Intent(transcript=said, room_id=ctx.room_id, session_id=ctx.session_id), ctx, db_session,
    )
    await db_session.commit()
    return response, seen


async def _logged(db_session, sid) -> tuple[str, str, str]:
    row = (await db_session.execute(text(
        "SELECT user_text, assistant_text, matched_handler FROM conversation_log "
        "WHERE session_id = :s ORDER BY id DESC LIMIT 1"), {"s": str(sid)})).one()
    return row[0], row[1], row[2]


@requires_db
@pytest.mark.parametrize("rooms", [(), ("office", "kitchen")], ids=["no sample here", "a short one here"])
async def test_the_case_a_yes_with_a_request_saves_the_yes_and_plays(
    db_session, quiet_rooms, monkeypatch, rooms,
) -> None:
    pid, voice = await _known_person(db_session, *rooms)
    ctx, response = await _turn(db_session)
    assert await maybe_ask_room_check(db_session, ctx=ctx, response=response, ident=_ident(pid, voice))
    before = await _count(db_session, pid)

    reply, seen = await _route_said(db_session, _yes(ctx, await _voice(788)), THE_CASE, monkeypatch)
    # The request reached music, on its own.
    assert "music: play you should be here" in seen
    assert reply.matched_handler == "music" and reply.matched_path == "fast"
    assert reply.text.startswith("Thanks, Priya. ")
    # The yes counted: that turn's clip is the kitchen's sample for her.
    assert await _count(db_session, pid) == before + 1
    kitchen = [r for r in await _sample_rows(pid) if r[1] == "kitchen"]
    assert kitchen[-1][0] == voice.tobytes()
    # No learning: nothing parked, no offer, nothing remembered as offered.
    pending = await _pending(db_session, ctx.session_id)
    assert not pending or pending.get("kind") not in ("core.self_intro", "core.room_learn_offer")
    assert "count from one to ten" not in reply.text and OFFER not in reply.text
    assert voice_profile._ROOM_LEARN_OFFERED == {}
    # One turn, one row: what was said, and what the room heard.
    user_text, assistant_text, handler = await _logged(db_session, ctx.session_id)
    assert user_text == THE_CASE and handler == "music"
    assert assistant_text == reply.text


@requires_db
async def test_a_bare_yes_is_as_before(db_session, quiet_rooms, monkeypatch) -> None:
    pid, voice = await _known_person(db_session)
    ctx, response = await _turn(db_session)
    assert await maybe_ask_room_check(db_session, ctx=ctx, response=response, ident=_ident(pid, voice))
    reply, seen = await _route_said(db_session, _yes(ctx, await _voice(788)), "Yes, that was me.", monkeypatch)
    assert reply.text == "Done — I'll know your voice in this room now, Priya."
    assert reply.matched_handler == "voice_profile" and reply.matched_path == "confirmation"
    assert seen == []
    assert await _count(db_session, pid) == 2


@requires_db
async def test_no_with_a_request_saves_nothing_and_plays(db_session, quiet_rooms, monkeypatch) -> None:
    pid, voice = await _known_person(db_session)
    ctx, response = await _turn(db_session)
    assert await maybe_ask_room_check(db_session, ctx=ctx, response=response, ident=_ident(pid, voice))
    reply, seen = await _route_said(db_session, _yes(ctx, await _voice(788)), "No, play some jazz.", monkeypatch)
    assert "music: play some jazz" in seen
    assert reply.matched_handler == "music"
    assert "my mistake" not in reply.text.lower()
    assert await _count(db_session, pid) == 1
    user_text, _said, _h = await _logged(db_session, ctx.session_id)
    assert user_text == "No, play some jazz."


@requires_db
async def test_a_yes_with_a_request_in_someone_elses_voice_saves_nothing_and_plays(
    db_session, quiet_rooms, monkeypatch,
) -> None:
    pid, voice = await _known_person(db_session)
    ctx, response = await _turn(db_session)
    assert await maybe_ask_room_check(db_session, ctx=ctx, response=response, ident=_ident(pid, voice))
    reply, seen = await _route_said(db_session, _yes(ctx, await _voice(388)), THE_CASE, monkeypatch)
    assert "music: play you should be here" in seen
    assert not reply.text.startswith("Thanks")
    assert await _count(db_session, pid) == 1


@requires_db
async def test_a_yes_with_a_request_to_the_offer_routes_it_and_starts_nothing(
    db_session, quiet_rooms, monkeypatch,
) -> None:
    pid, ctx, _offer = await _offered(db_session)
    reply, seen = await _route_said(
        db_session, _yes(ctx, await _voice(789)), "Yes, but play some jazz first.", monkeypatch,
    )
    assert "music: play some jazz first" in seen
    assert reply.matched_handler == "music"
    pending = await _pending(db_session, ctx.session_id)
    assert not pending or pending.get("kind") != "core.self_intro"
    # The offer ended without the learning: her confirmed clip is kept.
    assert await _count(db_session, pid) == 3


REPLIES = [
    # (said, as the router read it, (the answer, the rest routed — None:
    # only the answer))
    ("yes", True, (True, None)),
    ("yes, that was me", True, (True, None)),
    ("yeah, it's me, priya", True, (True, None)),
    ("yes, that was priya", True, (True, None)),
    ("yes it is, thanks", True, (True, None)),
    ("yep, that's right", True, (True, None)),
    ("yes, thank you domovoi", True, (True, None)),
    ("yes, wrong song though. play, you should be here", True, (True, "play you should be here")),
    ("yes, wrong song though. play you should be here", True, (True, "play you should be here")),
    ("yeah it was, turn it up", True, (True, "turn it up")),
    ("yes play some jazz", True, (True, "play some jazz")),
    ("um, yes, pause the music", True, (True, "pause the music")),
    ("yes, what's the capital of france", True, (True, "what's the capital of france")),
    ("yes it was. what's the capital of france", True, (True, "what's the capital of france")),
    # Review 1: natural yeses are a yes, nothing more for the model.
    ("yes please", True, (True, None)),
    ("sure, go ahead", True, (True, None)),
    ("yeah alright", True, (True, None)),
    ("yes, sounds good", True, (True, None)),
    ("yeah, let's do it", True, (True, None)),
    ("yes, sorry", True, (True, None)),
    ("yes, wrong song though", True, (True, None)),
    # Review 2: a yes that takes itself back is a no.
    ("yeah, no", True, (False, None)),
    ("yes, no wait, that was sam", True, (False, None)),
    ("yes, actually it wasn't", True, (False, None)),
    ("yeah no, play some jazz", True, (False, "play some jazz")),
    # Review 2/3: an introduction or a dismissal inside a yes is the answer.
    ("yes, my name is alex", True, (False, None)),
    ("yes, i'm in the kitchen", True, (True, None)),
    ("yes, that's fine", True, (True, None)),
    ("no", False, (False, None)),
    ("no, it was my sister", False, (False, None)),
    ("no it wasn't me", False, (False, None)),
    ("no, play some jazz", False, (False, "play some jazz")),
]


@pytest.mark.parametrize("said,polarity,read", REPLIES, ids=[r[0] for r in REPLIES])
def test_what_a_reply_says_besides_the_answer(said, polarity, read) -> None:
    parts = voice_profile.read_reply(normalize_transcript(said), polarity, "Priya")
    assert (parts.affirmative, parts.rest) == read
    assert voice_profile.reply_rest(normalize_transcript(said), polarity, "Priya") == read[1]


@pytest.mark.parametrize("said", [
    "yes", "yes please", "yeah, thanks", "sure thank you", "yep, please", "correct",
])
def test_a_yes_early_commit_takes_as_complete_is_a_bare_yes(said) -> None:
    """Agrees with early_commit: a turn it commits as a whole yes to a
    choice is read here as only the yes."""
    from domovoi.early_commit import _COMPLETE_YES_RE

    said = normalize_transcript(said)
    assert _COMPLETE_YES_RE.match(said)
    assert voice_profile.read_reply(said, True, "Priya") == (True, None, False)


def test_the_offers_yes_keeps_only_a_fast_path_request() -> None:
    said = normalize_transcript("Yes. What's the capital of France?")
    assert voice_profile.read_reply(said, True, "Priya", questions=False).rest is None
    said = normalize_transcript("Yes, but play some jazz first.")
    assert voice_profile.read_reply(said, True, "Priya", questions=False).rest == "play some jazz first"


def test_the_routed_rest_keeps_how_it_was_said() -> None:
    from domovoi.router import said_slice

    assert said_slice("Yes, announce Dinner is ready.", "announce dinner is ready") == (
        "announce Dinner is ready"
    )
    assert said_slice(THE_CASE, "play you should be here") == "Play you should be here"
    assert said_slice("Yes.", "something else") == "something else"


@requires_db
async def test_a_natural_yes_offers_and_a_natural_yes_to_the_offer_learns(
    db_session, quiet_rooms, monkeypatch,
) -> None:
    """Review 1: "Yes, sorry" to the room check is a bare yes (the offer,
    no sample, no model turn); "Yes please" to the offer starts the
    counting."""
    pid, ctx, _pending_check = await _asked(db_session, "office", "kitchen")
    await db_session.commit()
    offer, _seen = await _route_said(db_session, _yes(ctx, await _voice(788)), "Yes, sorry.", monkeypatch)
    assert offer.text == OFFER and offer.matched_path == "confirmation"
    assert await _count(db_session, pid) == 2
    learn, _seen = await _route_said(db_session, _yes(ctx, await _voice(789)), "Yes please.", monkeypatch)
    assert learn.text == LEARN_AFTER_OFFER
    assert (await _pending(db_session, ctx.session_id))["stage"] == "count"


@requires_db
@pytest.mark.parametrize("said", ["Yeah, no.", "Yes, no wait, that was Sam.", "Yes, my name is Sam."])
async def test_a_yes_that_takes_itself_back_saves_nothing(
    db_session, quiet_rooms, monkeypatch, said,
) -> None:
    pid, voice = await _known_person(db_session)
    ctx, response = await _turn(db_session)
    assert await maybe_ask_room_check(db_session, ctx=ctx, response=response, ident=_ident(pid, voice))
    reply, _seen = await _route_said(db_session, _yes(ctx, await _voice(788)), said, monkeypatch)
    assert reply.text == "Okay, my mistake."
    assert await _count(db_session, pid) == 1
    assert await _pending(db_session, ctx.session_id) is None     # no introduction either


@requires_db
@pytest.mark.parametrize("said", ["Yes, I'm in the kitchen.", "Yes, that's fine."])
async def test_an_introduction_or_dismissal_shape_in_a_yes_is_just_the_yes(
    db_session, quiet_rooms, monkeypatch, said,
) -> None:
    """Review 3: not routed to the introduction or the dismissal."""
    pid, voice = await _known_person(db_session)
    ctx, response = await _turn(db_session)
    assert await maybe_ask_room_check(db_session, ctx=ctx, response=response, ident=_ident(pid, voice))
    reply, _seen = await _route_said(db_session, _yes(ctx, await _voice(788)), said, monkeypatch)
    assert reply.text == "Done — I'll know your voice in this room now, Priya."
    assert await _count(db_session, pid) == 2
    assert await _pending(db_session, ctx.session_id) is None


@requires_db
async def test_counting_after_the_room_check_takes_only_the_asked_voice(
    db_session, quiet_rooms, monkeypatch,
) -> None:
    """Review 4: a clip in a room learning started from "was that you,
    X?" must pass that question's voice rule, not only resemble the clips
    so far (here everything "resembles", to show the rule decides)."""
    monkeypatch.setattr(voice_profile, "_resembles", lambda clips, clip: True)
    pid, ctx, offer = await _offered(db_session)
    learn = await VoiceProfileHandler().handle_choice_reply(
        "core.room_learn_offer", offer, "Yes.", _yes(ctx, await _voice(789)), db_session,
    )
    assert learn.text == LEARN_AFTER_OFFER
    collecting = await _pending(db_session, ctx.session_id)
    assert collecting["check_score"] == 0.55

    # Somebody else counts: the clip doesn't count.
    other = await VoiceProfileHandler().handle_choice_reply(
        "core.self_intro", dict(collecting), COUNTING, _yes(ctx, await _voice(388)), db_session,
    )
    assert other.text == SENTENCE
    assert len((await _pending(db_session, ctx.session_id))["clips"]) == 1
    # Priya counts: it does.
    mine = await VoiceProfileHandler().handle_choice_reply(
        "core.self_intro", dict(collecting), COUNTING, _yes(ctx, await _voice(787)), db_session,
    )
    assert mine.text == SENTENCE
    after = await _pending(db_session, ctx.session_id)
    assert len(after["clips"]) == 2 and after["check_score"] == 0.55


# ─── "I already know you" in a room that hasn't learned you (2026-11-16) ──

LEARN = (
    "I already know you, Sarah. Let me learn your voice better here: count "
    "from one to ten for me, at your normal voice."
)


async def _sarah(*samples: tuple[int, str, float]) -> int:
    """Sarah, with a satellite sample per ``(voice level, room, speech seconds)``."""
    return await _named("Sarah", *samples)


async def _named(name: str, *samples: tuple[int, str, float]) -> int:
    """``name``, with a satellite sample per ``(voice level, room, speech seconds)``."""
    async with SessionLocal() as s:
        pid = int((await s.execute(
            text("INSERT INTO people (name) VALUES (:n) RETURNING id"), {"n": name},
        )).scalar_one())
        repo = VoiceProfilesRepository(s)
        for level, room_id, secs in samples:
            await repo.add(
                person_id=pid, embedding=(await _vec(_pcm(1.0, level))).tobytes(),
                model=repo.EMBEDDING_MODEL_TAG, room_id=room_id, sample_seconds=secs,
            )
        await s.commit()
    return pid


async def _room_rows(pid: int, room_id: str) -> list[tuple[int, float | None, bool]]:
    async with SessionLocal() as s:
        rows = (await s.execute(text(
            "SELECT id, sample_seconds, claimed_at IS NOT NULL FROM voice_profiles "
            "WHERE person_id = :p AND room_id = :r ORDER BY id"
        ), {"p": pid, "r": room_id})).all()
    return [(int(r[0]), r[1], bool(r[2])) for r in rows]


@requires_db
async def test_recognised_in_a_room_without_a_sample_learns_it_there(room) -> None:
    pid = await _sarah((700, "garage", 1.0))
    asked = await room.say("My name is Sarah.", _pcm(1.0, 711))
    assert _reply(asked) == LEARN
    assert asked["expect_followup"] is True
    assert (await room.pending())["stage"] == "count"
    assert await _room_rows(pid, ROOM) == []

    # Five seconds: enough for a room sample (saved now), short of eight.
    more = await room.say(COUNTING, _pcm(4.0, 712), trigger="followup")
    assert _reply(more) == SENTENCE
    [(first_id, secs, claimed)] = await _room_rows(pid, ROOM)
    assert secs == pytest.approx(5.0) and claimed

    done = await room.say(
        "Good morning Domovoi, what's on today?", _pcm(1.0, 713), trigger="followup",
    )
    assert _reply(done) == "Thanks, Sarah! I'll know your voice now."
    assert await _room_rows(pid, ROOM) == [(first_id, pytest.approx(6.0), True)]
    assert [r[1] for r in await _room_rows(pid, "garage")] == [1.0]
    assert [n for _p, n in await _people()] == ["Sarah"]


@requires_db
async def test_a_short_sample_in_the_room_is_replaced_by_the_longer_one(room) -> None:
    pid = await _sarah((700, "garage", 1.0), (701, ROOM, 1.0))
    [(old_id, _s, _c)] = await _room_rows(pid, ROOM)
    asked = await room.say("My name is Sarah.", _pcm(1.0, 721))
    assert _reply(asked) == LEARN
    done = await room.say(COUNTING, _pcm(8.0, 722), trigger="followup")
    assert _reply(done) == "Thanks, Sarah! I'll know your voice now."
    rows = await _room_rows(pid, ROOM)
    assert len(rows) == 1
    new_id, secs, claimed = rows[0]
    assert new_id != old_id and secs == pytest.approx(9.0) and claimed


@requires_db
async def test_a_good_sample_in_the_room_keeps_the_plain_reply(room) -> None:
    pid = await _sarah((700, "garage", 1.0), (701, ROOM, 5.0))
    said = await room.say("My name is Sarah.", _pcm(1.0, 731))
    assert _reply(said) == "I already know you, Sarah."
    assert await room.pending() is None
    assert [r[1] for r in await _room_rows(pid, ROOM)] == [5.0]


# ─── "Is that you?" re-enrolment replaces a short room sample (1.4.1) ─────
#
# "my name is X" in a room whose short sample of X doesn't recognize X's
# voice goes through "I already know someone called X. Is that you?". Its
# yes used to ADD a sample beside the short one (a 1.17 s clip kept diluting
# the room); now it learns the room like a recognized X does: the longer
# recording replaces X's samples in THAT room only, and only once it holds
# more speech than they did.

IS_THAT_YOU = "I already know someone called Sarah. Is that you?"
LEARN_AFTER_YES = (
    "Thanks, Sarah. Let me learn your voice better here: count from one to "
    "ten for me, at your normal voice."
)


async def _ids(pid: int) -> list[int]:
    async with SessionLocal() as s:
        rows = (await s.execute(
            text("SELECT id FROM voice_profiles WHERE person_id = :p ORDER BY id"), {"p": pid},
        )).all()
    return [int(r[0]) for r in rows]


@requires_db
async def test_unrecognised_reintroduction_replaces_the_same_rooms_short_sample(room) -> None:
    sarah = await _sarah((700, "garage", 1.0), (701, ROOM, 1.17))
    # Another person with a short sample in the same room: never touched.
    priya = await _named("Priya", (500, ROOM, 1.0), (501, "garage", 1.0))
    [(old_id, _s, _c)] = await _room_rows(sarah, ROOM)
    garage_before = await _room_rows(sarah, "garage")
    priya_before = await _ids(priya)

    # A voice this room's short sample doesn't recognize as Sarah.
    asked = await room.say("My name is Sarah.", _pcm(1.0, 901))
    assert _reply(asked) == IS_THAT_YOU
    yes = await room.say("Yes.", _pcm(1.0, 902), trigger="followup")
    assert _reply(yes) == LEARN_AFTER_YES
    assert (await room.pending())["stage"] == "count"
    # Nothing replaced until the collection has more speech than the old.
    assert [r[0] for r in await _room_rows(sarah, ROOM)] == [old_id]

    done = await room.say(COUNTING, _pcm(8.0, 903), trigger="followup")
    assert _reply(done) == "Thanks, Sarah! I'll know your voice now."
    rows = await _room_rows(sarah, ROOM)
    assert len(rows) == 1
    new_id, secs, claimed = rows[0]
    assert new_id != old_id and secs > 1.17 and claimed
    # Her other room, and the other person, as they were.
    assert await _room_rows(sarah, "garage") == garage_before
    assert await _ids(priya) == priya_before
    assert [n for _p, n in await _people()] == ["Sarah", "Priya"]


@requires_db
async def test_unrecognised_reintroduction_that_stops_short_keeps_the_old_sample(room) -> None:
    sarah = await _sarah((700, "garage", 1.0), (701, ROOM, 3.0))
    before = await _ids(sarah)
    await room.say("My name is Sarah.", _pcm(1.0, 911))
    yes = await room.say("Yes.", _pcm(1.0, 912), trigger="followup")
    assert _reply(yes) == LEARN_AFTER_YES
    # Two seconds heard, the room's sample has three: "stop" saves nothing.
    stopped = await room.say("Stop.", _pcm(1.0, 913), trigger="followup")
    assert _reply(stopped) == "Okay, I'll stop there. I'll still know your voice, Sarah."
    assert await room.pending() is None
    assert await _ids(sarah) == before
    assert [r[1] for r in await _room_rows(sarah, ROOM)] == [3.0]


@requires_db
async def test_unrecognised_reintroduction_walked_away_from_keeps_the_old_sample(room) -> None:
    sarah = await _sarah((701, ROOM, 1.0))
    before = await _ids(sarah)
    await room.say("My name is Sarah.", _pcm(1.0, 921))
    await room.say("Yes.", _pcm(1.0, 922), trigger="followup")
    # No counting ever comes: the old sample is all there is.
    assert await _ids(sarah) == before


@requires_db
async def test_unrecognised_reintroduction_of_an_opted_out_voice_saves_nothing(room) -> None:
    from domovoi.db.repositories import VoiceDenylistRepository

    sarah = await _sarah((701, ROOM, 1.0))
    before = await _ids(sarah)
    await room.say("My name is Sarah.", _pcm(1.0, 931))
    async with SessionLocal() as s:
        await VoiceDenylistRepository(s).add(
            embedding=(await _vec(_pcm(1.0, 931))).tobytes(),
            model=VoiceDenylistRepository.EMBEDDING_MODEL_TAG,
        )
        await s.commit()
    no = await room.say("Yes.", _pcm(1.0, 932), trigger="followup")
    assert _reply(no) == "I wasn't able to save that voice profile."
    assert await room.pending() is None
    assert await _ids(sarah) == before


@requires_db
async def test_unrecognised_reintroduction_beside_a_good_sample_still_adds(room) -> None:
    """A room with a good sample of X (4 s or more) is not relearned, the
    recognized path's rule: the yes adds the clip as before."""
    sarah = await _sarah((701, ROOM, 5.0))
    await room.say("My name is Sarah.", _pcm(1.0, 941))
    yes = await room.say("Yes.", _pcm(1.0, 942), trigger="followup")
    assert _reply(yes) == COUNT
    assert len(await _room_rows(sarah, ROOM)) == 2
