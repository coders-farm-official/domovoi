"""Names in the introduction, sound-alikes, and visible match scores
(wf/voice-id-names, 2026-11-11).

A household member was saved under a misheard spelling of their name: the
introduction read the name back only as a sound ("Nice to meet you, X. Did I
get that right?"), only a reply starting with yes/no reached it, and "yes,
but it's spelled …" saved the misheard name. A later "my name is <the
misheard name>" made a second person. And nothing showed what the speaker
matcher scored. Now:

* the read-back spells the name ("That's J, O, R, D, A, N."), the very next
  turn answers it (a choice, with a 2-minute backstop), and a correction or
  a spelling is read back again — "yes, but …" is never a yes;
* a heard name that sounds like a household member's ("Jordyn" for Jordan)
  asks "Did you mean Jordan?" instead of making a second person;
* every satellite turn logs one INFO line about its identification and
  keeps the outcome, the closest person and the runner-up with their scores
  on its conversation_log row (V049), which the dashboard shows to admins.

The satellite turns run through the real StreamSession harness of
test_voice_id_namesakes.py (real router, handler, identifier, history rows).
"""

from __future__ import annotations

import asyncio
import logging

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from domovoi import person_names, voice_identifier
from domovoi.db.session import SessionLocal
from domovoi.handlers import voice_profile
from domovoi.person_names import letters_of, read_name_reply, sounds_like_name, spell_out
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_music_under_capture import ROOM
from domovoi.tests.test_voice_id_namesakes import (  # noqa: F401 — the room fixture
    PRIYA_HERE,
    PRIYA_OFFICE,
    STRANGER,
    _people,
    _person,
    _reply,
    _samples,
    room,
)
from domovoi.voice_identifier import identify as real_identify

ALEX_HERE = np.full(16_000, 4444, dtype=np.int16).tobytes()


# ─── Spelled letters, in every shape Whisper writes them ──────────────────

LETTER_SHAPES = [
    ("J O R D Y N", "jordyn"),
    ("J-O-R-D-Y-N", "jordyn"),
    ("J-O-R-D-Y-N.", "jordyn"),
    ("J. O. R. D. Y. N.", "jordyn"),
    ("J.O.R.D.Y.N", "jordyn"),
    ("j, o, r, d, y, n", "jordyn"),
    ("J, O, R, D, Y, N.", "jordyn"),
    ("jay oh ar dee why en", "jordyn"),
    ("Jay, oh, are, dee, why, en.", "jordyn"),
    ("J O R D why N", "jordyn"),
    ("P-R-I-Y-A", "priya"),
    ("pee ar eye why ay", "priya"),
    ("A L E X", "alex"),
    ("ay el ee ex", "alex"),
    ("double u e ess", "wes"),
    ("double-you ee ess", "wes"),
    ("S. A. M.", "sam"),
    ("ess ay em", "sam"),
    # Not spellings.
    ("Jordan", None),
    ("J", None),
    ("", None),
    ("see you", None),
    ("are you okay", None),
    ("oh you", None),
    ("play some jazz", None),
]


@pytest.mark.parametrize("said,letters", LETTER_SHAPES)
def test_spelled_letters_in_every_shape(said: str, letters: str | None) -> None:
    assert letters_of(said) == letters


REPLIES = [
    # Plain answers.
    ("Yes.", "yes", None, False),
    ("yeah, that's me", "yes", None, False),
    ("No.", "no", None, False),
    ("not quite", "no", None, False),
    ("never mind", "cancel", None, False),
    ("no thanks", "cancel", None, False),
    # Corrections: never a yes.
    ("Yes, but it's spelled J-O-R-D-Y-N.", "name", "Jordyn", True),
    ("yes it's spelled j o r d y n", "name", "Jordyn", True),
    ("Close, it's J. O. R. D. Y. N.", "name", "Jordyn", True),
    ("No, it's Jordyn.", "name", "Jordyn", False),
    ("No, I said Priya.", "name", "Priya", False),
    ("my name is Priya", "name", "Priya", False),
    ("it's Priya", "name", "Priya", False),
    ("Priya", "name", "Priya", False),
    ("J-O-R-D-Y-N", "name", "Jordyn", True),
    ("jay oh ar dee why en", "name", "Jordyn", True),
    ("Jordyn, J O R D Y N", "name", "Jordyn", True),
    ("It's spelled JORDYN.", "name", "Jordyn", True),
    ("No no, Jordyn. J-O-R-D-Y-N.", "name", "Jordyn", True),
    # A correction whose name can't be read: ask for the spelling.
    ("yes, but you spelled it wrong", "unclear", None, False),
    ("Yes, but with a y.", "unclear", None, False),
    ("close", "unclear", None, False),
    # Not about the name at all.
    ("what time is it", None, None, False),
    ("set a timer for 5 minutes", None, None, False),
    ("play some jazz", None, None, False),
]


@pytest.mark.parametrize("said,kind,name,spelled", REPLIES)
def test_replies_to_the_read_back(said: str, kind: str | None, name: str | None, spelled: bool) -> None:
    reply = read_name_reply(said)
    if kind is None:
        assert reply is None
        return
    assert reply is not None and reply.kind == kind, reply
    assert reply.name == name
    assert reply.spelled is spelled


def test_a_name_is_spelled_back_so_a_voice_says_each_letter() -> None:
    assert spell_out("Jordan") == "J, O, R, D, A, N"
    assert spell_out("Mary Jo") == "M, A, R, Y. J, O"
    # Measured with Domovoi's own phonemizer (the house Piper voices): with
    # commas every letter is said as its name; "A-L-E-X" runs together and
    # a lone "A" is read as the article.
    from domovoi.phonemizer import get_phonemizer

    said = get_phonemizer().phonemize(spell_out("Alex"))
    assert len(said) == 1 and said[0].count(",") == 3, said
    assert said[0].startswith("ˈeɪ"), said


@pytest.mark.parametrize("heard,known,alike", [
    ("Jordyn", "Jordan", True),
    ("jordan", "Jordan", False),          # the same name is not a sound-alike
    ("Preeya", "Priya", True),
    ("Kathryn", "Catherine", True),
    ("Alex", "Priya", False),
    ("Sam", "Pam", False),
])
def test_sound_alike_names(heard: str, known: str, alike: bool) -> None:
    assert sounds_like_name(heard, known) is alike


# ─── The introduction at a satellite ──────────────────────────────────────

JORDAN_ASKED = "Nice to meet you, Jordan. That's J, O, R, D, A, N. Did I get that right?"
JORDYN_AGAIN = "Got it, Jordyn: J, O, R, D, Y, N. Is that right?"


@requires_db
async def test_yes_but_spelled_is_a_correction_then_the_yes_saves_the_spelling(room) -> None:
    asked = await room.say("My name is Jordan.", STRANGER)
    assert _reply(asked) == JORDAN_ASKED
    assert asked["expect_followup"] is True
    pending = await room.pending()
    assert pending["kind"] == "core.self_intro" and pending["stage"] == "confirm"
    assert pending["expires_at"] > 0

    again = await room.say("Yes, but it's spelled J-O-R-D-Y-N.", STRANGER, trigger="followup")
    assert _reply(again) == JORDYN_AGAIN
    assert await _people() == []

    done = await room.say("Yes.", STRANGER, trigger="followup")
    assert _reply(done) == "Got it, Jordyn."
    people = await _people()
    assert [n for _pid, n in people] == ["Jordyn"]
    assert await _samples(people[0][0]) == [("satellite", ROOM)]
    assert await room.pending() is None


@requires_db
async def test_a_no_asks_for_the_name_and_a_spelling_answers_it(room) -> None:
    await room.say("My name is Jordan.", STRANGER)
    asked = await room.say("No.", STRANGER, trigger="followup")
    assert _reply(asked) == voice_profile._ASK_NAME_TEXT
    assert (await room.pending())["stage"] == "ask_name"
    again = await room.say("P. R. I. Y. A.", STRANGER, trigger="followup")
    assert _reply(again) == "Got it, Priya: P, R, I, Y, A. Is that right?"
    done = await room.say("Yeah.", STRANGER, trigger="followup")
    assert _reply(done) == "Got it, Priya."
    assert [n for _pid, n in await _people()] == ["Priya"]


@requires_db
async def test_no_its_the_same_name_asks_how_it_is_spelled(room) -> None:
    """ "No, it's Jordan" to "Jordan": it sounded right, so the spelling is
    what's wrong."""
    await room.say("My name is Jordan.", STRANGER)
    asked = await room.say("No, it's Jordan.", STRANGER, trigger="followup")
    assert _reply(asked) == voice_profile._ASK_SPELLING_TEXT
    again = await room.say("jay oh ar dee why en", STRANGER, trigger="followup")
    assert _reply(again) == JORDYN_AGAIN


@requires_db
async def test_a_correction_that_cannot_be_read_asks_for_the_spelling(room) -> None:
    await room.say("My name is Jordan.", STRANGER)
    asked = await room.say("Yes, but you spelled it wrong.", STRANGER, trigger="followup")
    assert _reply(asked) == voice_profile._ASK_SPELLING_TEXT
    assert await _people() == []


@requires_db
async def test_never_mind_saves_nothing(room) -> None:
    await room.say("My name is Jordan.", STRANGER)
    done = await room.say("Never mind.", STRANGER, trigger="followup")
    assert _reply(done) == "Okay, I won't save that."
    assert await _people() == [] and await room.pending() is None


@requires_db
async def test_a_stray_yes_after_another_turn_saves_nothing(room) -> None:
    await room.say("My name is Jordan.", STRANGER)
    other = await room.say("What time is it?", STRANGER)
    assert other["matched_handler"] != "voice_profile"
    assert await room.pending() is None
    later = await room.say("Yes.", STRANGER)
    assert later["matched_handler"] != "voice_profile"
    assert await _people() == []


@requires_db
async def test_the_question_runs_out(room, monkeypatch) -> None:
    monkeypatch.setattr(voice_profile, "SELF_INTRO_TTL_SEC", -1.0)
    await room.say("My name is Jordan.", STRANGER)
    late = await room.say("Yes.", STRANGER, trigger="followup")
    assert _reply(late) == voice_profile._INTRO_EXPIRED_TEXT
    assert await _people() == []


@requires_db
async def test_a_spelled_introduction_is_read_back_as_spelled(room) -> None:
    asked = await room.say("My name is J. O. R. D. Y. N.", STRANGER)
    assert _reply(asked) == "Nice to meet you, Jordyn. That's J, O, R, D, Y, N. Did I get that right?"


@requires_db
async def test_the_answer_is_still_taken_after_a_restart_forgot_the_choice(room) -> None:
    """A core restart forgets which sessions have a choice waiting: a reply
    that starts with yes/no still reaches the question (and a correction in
    it is still read as one)."""
    from domovoi.tests.music_choice_testkit import clear_choice_parked

    await room.say("My name is Jordan.", STRANGER)
    clear_choice_parked()
    again = await room.say("Yes, but it's spelled J-O-R-D-Y-N.", STRANGER, trigger="followup")
    assert _reply(again) == JORDYN_AGAIN


# ─── Sound-alikes ─────────────────────────────────────────────────────────

SOUND_ALIKE_ASKED = "Did you mean Jordan? I already know someone called Jordan."


@requires_db
async def test_a_sound_alike_asks_did_you_mean_and_yes_adds_to_that_person(room) -> None:
    jordan = await _person("Jordan", (PRIYA_OFFICE, "office"))
    asked = await room.say("My name is Jordyn.", STRANGER)
    assert _reply(asked) == SOUND_ALIKE_ASKED
    assert (await room.pending())["kind"] == "core.name_sound_alike"
    done = await room.say("Yes.", STRANGER, trigger="followup")
    assert _reply(done) == "Done — I'll know your voice in this room now, Jordan."
    assert await _people() == [(jordan, "Jordan")]
    assert await _samples(jordan) == [("satellite", "office"), ("satellite", ROOM)]
    async with SessionLocal() as s:
        claimed = (await s.execute(text(
            "SELECT count(*) FROM voice_profiles WHERE person_id = :p AND claimed_at IS NOT NULL"
        ), {"p": jordan})).scalar_one()
    assert claimed == 1


@requires_db
async def test_a_no_to_the_sound_alike_reads_the_new_name_back(room) -> None:
    jordan = await _person("Jordan", (PRIYA_OFFICE, "office"))
    await room.say("My name is Jordyn.", STRANGER)
    asked = await room.say("No.", STRANGER, trigger="followup")
    assert _reply(asked) == "Nice to meet you, Jordyn. That's J, O, R, D, Y, N. Did I get that right?"
    done = await room.say("Yes.", STRANGER, trigger="followup")
    assert _reply(done) == "Got it, Jordyn."
    assert [n for _pid, n in await _people()] == ["Jordan", "Jordyn"]
    assert await _samples(jordan) == [("satellite", "office")]


@requires_db
async def test_a_recognized_voice_saying_a_sound_alike_of_its_own_name(room) -> None:
    await _person("Jordan", (PRIYA_HERE, ROOM))
    known = await room.say("My name is Jordyn.", PRIYA_HERE)
    assert _reply(known) == "I already know you, Jordan."
    spelled = await room.say("My name is J-O-R-D-Y-N.", PRIYA_HERE)
    assert _reply(spelled) == (
        "I know you as Jordan. If that's spelled wrong, an admin can change it "
        "in Settings, under People."
    )
    assert [n for _pid, n in await _people()] == ["Jordan"]


@requires_db
async def test_a_correction_to_a_known_name_asks_about_that_person(room) -> None:
    jordan = await _person("Jordan", (PRIYA_OFFICE, "office"))
    await room.say("My name is Priya.", STRANGER)
    asked = await room.say("No, it's Jordan.", STRANGER, trigger="followup")
    assert _reply(asked) == "I already know someone called Jordan. Is that you?"
    assert (await room.pending())["person_id"] == jordan


@requires_db
async def test_a_third_party_introduction_takes_the_households_spelling(room) -> None:
    await _person("Priya", (PRIYA_HERE, ROOM))
    await _person("Jordan", (PRIYA_OFFICE, "office"))
    said = await room.say("Domovoi, meet Jordyn.", PRIYA_HERE)
    assert _reply(said) == "Hi Jordan, nice to meet you."


# ─── Visible match scores (V049) ──────────────────────────────────────────


async def _last_turn() -> dict:
    async with SessionLocal() as s:
        row = (await s.execute(text(
            "SELECT id, voice_outcome, voice_best_person_id, voice_best_score, "
            "voice_runner_up_person_id, voice_runner_up_score "
            "FROM conversation_log ORDER BY id DESC LIMIT 1"
        ))).mappings().first()
    return dict(row) if row else {}


@requires_db
async def test_a_recognized_turn_keeps_the_best_and_the_runner_up(room, caplog) -> None:
    priya = await _person("Priya", (PRIYA_HERE, ROOM))
    alex = await _person("Alex", (ALEX_HERE, ROOM))
    with caplog.at_level(logging.INFO, logger="domovoi.turn_history"):
        end = await room.say("Who am I?", PRIYA_HERE)
        await asyncio.sleep(0.2)   # the log line's write runs beside the turn
    assert _reply(end) == "You're Priya."
    row = await _last_turn()
    assert row["voice_outcome"] == "confident"
    assert row["voice_best_person_id"] == priya and row["voice_best_score"] == pytest.approx(1.0, abs=1e-3)
    assert row["voice_runner_up_person_id"] == alex and row["voice_runner_up_score"] < 0.75
    lines = [r.getMessage() for r in caplog.records if "voice identification room=" in r.getMessage()]
    assert len(lines) == 1, lines
    assert f"turn={row['id']}" in lines[0] and "speech=" in lines[0]
    assert f"best person_id={priya} 1.000" in lines[0]


@requires_db
async def test_a_turn_with_nobody_enrolled_says_so(room) -> None:
    await room.say("Who am I?", STRANGER)
    row = await _last_turn()
    assert row["voice_outcome"] == "no_samples" and row["voice_best_person_id"] is None


@requires_db
async def test_a_turn_too_short_to_embed_says_so(room, monkeypatch, caplog) -> None:
    short = np.full(8_000, 5555, dtype=np.int16).tobytes()   # 0.5 s

    async def _identify(pcm, embedding=None, **kw):
        # What the real embedder gives a sub-second clip: nothing.
        return await real_identify(short, embedding=None, room_id=kw.get("room_id"))

    monkeypatch.setattr("domovoi.voice_identifier.identify", _identify)
    await _person("Priya", (PRIYA_HERE, ROOM))
    with caplog.at_level(logging.INFO, logger="domovoi.turn_history"):
        await room.say("Who am I?", STRANGER)
        await asyncio.sleep(0.2)
    assert (await _last_turn())["voice_outcome"] == "too_short"
    assert any("too short to tell" in r.getMessage() for r in caplog.records)


@requires_db
async def test_a_turn_whose_identification_times_out_says_so(room, monkeypatch) -> None:
    from domovoi.config import settings

    async def _slow(pcm, embedding=None, **kw):
        await asyncio.sleep(5)

    monkeypatch.setattr("domovoi.voice_identifier.identify", _slow)
    monkeypatch.setattr(settings, "voice_identify_timeout_sec", 0.1)
    await room.say("Who am I?", STRANGER)
    assert (await _last_turn())["voice_outcome"] == "timeout"


@requires_db
async def test_an_opted_out_voice_keeps_no_scores(room) -> None:
    from domovoi.clients.voice_embedder import StubVoiceEmbedder
    from domovoi.db.repositories import VoiceDenylistRepository

    await _person("Priya", (PRIYA_HERE, ROOM))
    vec = (await StubVoiceEmbedder().embed(STRANGER)).astype(np.float32).tobytes()
    async with SessionLocal() as s:
        await VoiceDenylistRepository(s).add(
            embedding=vec, model=VoiceDenylistRepository.EMBEDDING_MODEL_TAG, notes=None,
        )
        await s.commit()
    await room.say("Who am I?", STRANGER)
    row = await _last_turn()
    assert row["voice_outcome"] == "denylist"
    assert row["voice_best_person_id"] is None and row["voice_best_score"] is None


def test_the_match_reads_back_for_every_outcome() -> None:
    m = voice_identifier.VoiceMatch(
        outcome="ambiguous", best_person_id=1, best_score=0.79,
        runner_up_person_id=2, runner_up_score=0.77, speech_seconds=2.0,
    )
    assert m.describe().startswith("ambiguous: best person_id=1 0.790, runner-up person_id=2 0.770")
    assert voice_identifier.VoiceMatch(outcome="too_short", speech_seconds=0.8).describe() == (
        "too short to tell (0.8 s < 1.0 s)"
    )
    for outcome in voice_identifier.OUTCOMES:
        assert voice_identifier.VoiceMatch(outcome=outcome).describe()
    # A result made by hand (no outcome) is read from what it says.
    ident = voice_identifier.IdentificationResult(
        person_id=None, presence_tier="high", embedding=None, best_similarity=None,
    )
    assert voice_identifier.VoiceMatch.of(ident).outcome == "no_embedding"


@requires_db
def test_the_dashboard_shows_scores_to_an_admin_only(monkeypatch) -> None:
    from domovoi.db.session import engine
    from domovoi.tests.auth_testkit import bearer, install_fake_db
    from web.backend import voice_match
    from web.backend.main import app as web_app

    ADMIN_TOKEN, DEVICE_TOKEN = "admin-token", "device-token"

    async def _seed() -> tuple[int, int]:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE people RESTART IDENTITY CASCADE"))
            await conn.execute(text("TRUNCATE conversation_log RESTART IDENTITY CASCADE"))
            priya = int((await conn.execute(
                text("INSERT INTO people (name) VALUES ('Priya') RETURNING id"))).scalar_one())
            alex = int((await conn.execute(
                text("INSERT INTO people (name) VALUES ('Alex') RETURNING id"))).scalar_one())
            await conn.execute(text(
                "INSERT INTO conversation_log (room_id, person_id, user_text, assistant_text, "
                "voice_outcome, voice_best_person_id, voice_best_score, "
                "voice_runner_up_person_id, voice_runner_up_score) VALUES "
                "('kitchen', :p, 'who am i', 'You''re Priya.', 'match', :p, 0.82, :a, 0.61), "
                "('kitchen', NULL, 'hi', 'Hello.', 'ambiguous', :a, 0.79, :p, 0.77), "
                "('kitchen', NULL, 'hey', 'Hey.', 'too_short', NULL, NULL, NULL, NULL)"
            ), {"p": priya, "a": alex})
            return priya, alex

    priya, alex = asyncio.run(_seed())
    voice_match.forget_probe()
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    with TestClient(web_app) as c:
        device = c.get(f"/api/satellites/kitchen/conversations?device_token={DEVICE_TOKEN}")
        admin = c.get("/api/satellites/kitchen/conversations", headers=bearer(ADMIN_TOKEN))
        person = c.get(f"/api/people/{priya}/conversations", headers=bearer(ADMIN_TOKEN))
        matches = c.get(f"/api/people/{priya}/voice-matches", headers=bearer(ADMIN_TOKEN))
        matches_device = c.get(f"/api/people/{priya}/voice-matches?device_token={DEVICE_TOKEN}")
    assert device.status_code == 200 and admin.status_code == 200
    assert all(t["voice_match"] is None for t in device.json())
    by_text = {t["user_text"]: t["voice_match"] for t in admin.json()}
    assert by_text["who am i"] == {
        "outcome": "match", "best_person_id": priya, "best_name": "Priya", "best_score": 0.82,
        "runner_up_person_id": alex, "runner_up_name": "Alex", "runner_up_score": 0.61,
    }
    assert by_text["hi"]["outcome"] == "ambiguous" and by_text["hi"]["best_name"] == "Alex"
    assert by_text["hey"] == {
        "outcome": "too_short", "best_person_id": None, "best_name": None, "best_score": None,
        "runner_up_person_id": None, "runner_up_name": None, "runner_up_score": None,
    }
    assert [t["voice_match"]["best_name"] for t in person.json()] == ["Priya"]
    # Priya was the best on one turn and the runner-up on another.
    assert sorted(t["user_text"] for t in matches.json()) == ["hi", "who am i"]
    assert matches_device.status_code == 200 and matches_device.json() == []


@requires_db
async def test_forgetting_a_person_clears_them_from_other_turns(db_session) -> None:
    from domovoi.db.repositories import PeopleRepository

    priya = await PeopleRepository(db_session).create(name="Priya")
    alex = await PeopleRepository(db_session).create(name="Alex")
    await db_session.execute(text(
        "INSERT INTO conversation_log (room_id, person_id, user_text, voice_outcome, "
        "voice_best_person_id, voice_best_score, voice_runner_up_person_id, voice_runner_up_score) "
        "VALUES ('kitchen', :a, 'hi', 'match', :a, 0.9, :p, 0.7)"
    ), {"a": alex, "p": priya})
    await db_session.commit()
    await PeopleRepository(db_session).delete(priya)
    await db_session.commit()
    row = (await db_session.execute(text(
        "SELECT voice_best_person_id, voice_runner_up_person_id, voice_runner_up_score "
        "FROM conversation_log"
    ))).one()
    assert row[0] == alex and row[1] is None


def test_person_names_module_is_pure() -> None:
    """No database, no settings: safe to import from the web process."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(person_names.__file__).read_text(encoding="utf-8"))
    imported = {
        (n.module if isinstance(n, ast.ImportFrom) else a.name)
        for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])
    }
    assert not any(m and (m.startswith("domovoi.db") or m == "domovoi.config") for m in imported)
