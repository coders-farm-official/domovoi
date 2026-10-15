"""One name, one person — on every enrolment and identification path.

2026-10-15 (wf/voice-id-dupes). The phone's chat mic had the rule first
(test_phone_voice_profile.py): "my name is X" for an X who exists asks to
add the voice to X, and two people called X are never guessed between. A
satellite still enrolled a second X. Now:

* a satellite's "my name is X", for an X who exists and isn't who the
  voice was matched as, asks "I already know someone called X. Is that
  you?"; a yes on the very next turn adds the clip to X's SATELLITE
  samples, tagged with the room; a no says how to be told apart;
* the yes to "Did I get that right?" / "was that X?" re-checks the name,
  under a lock (two enrolments of one new name at once make one person);
* two people called X: nobody is guessed, and the answer says what to do
  (an admin renames or removes one on the People page);
* "who am I" says when someone else has your name, and when your voice
  matches two people (with or without the same name); "forget me" never
  guesses whose profile to delete;
* the People page: the roster says who shares a name (``namesakes``), and
  its rename refuses a name someone else has (409), admin tier.

The satellite turns run through a real StreamSession (the
test_music_under_capture harness), the real router, handler and
identifier, on the lane's database; Whisper says what each test scripts,
and the voice is the stub embedder's (same PCM, same vector; another PCM,
an unrelated one). The phone turns use the chat-mic harness.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from domovoi import streaming, voice_identifier
from domovoi.clients.voice_embedder import StubVoiceEmbedder
from domovoi.config import settings
from domovoi.db.repositories import (
    PeopleRepository,
    SessionRepository,
    VoiceDenylistRepository,
    VoiceProfilesRepository,
)
from domovoi.db.session import SessionLocal, session_scope
from domovoi.handlers.voice_profile import VoiceProfileHandler, _b64_embedding
from domovoi.models import Context
from domovoi.tests.conftest import requires_db
from domovoi.tests.music_choice_testkit import clear_choice_parked
from domovoi.tests.test_music_under_capture import ROOM, Room, _wav
from domovoi.tests.test_phone_voice import _client, _thread
from domovoi.tests.test_phone_voice_profile import (  # noqa: F401 — fixtures
    _say,
    open_gate,
    whisper,
)
from domovoi.voice_identifier import identify as real_identify

pytestmark = requires_db

# One PCM per voice-and-mic (the stub embedder: same bytes, same vector).
KAMRON_OFFICE = np.full(16_000, 1111, dtype=np.int16).tobytes()
KAMRON_HERE = np.full(16_000, 2222, dtype=np.int16).tobytes()
STRANGER = np.full(16_000, 3333, dtype=np.int16).tobytes()

ROOM_QUESTION = "I already know someone called Kamron. Is that you?"
SORT_IT_OUT = "An admin can rename or remove one of them on the People page."


def _more_than_one(name: str) -> str:
    return (
        f"There's more than one {name}, so I won't guess which one is you. "
        f"{SORT_IT_OUT} Then tell me your name again."
    )


async def _vec(pcm: bytes) -> bytes:
    return (await StubVoiceEmbedder().embed(pcm)).astype(np.float32).tobytes()


async def _person(name: str, *samples: tuple[bytes, str]) -> int:
    """A person with a satellite sample per ``(pcm, room)``."""
    async with SessionLocal() as s:
        pid = int((await s.execute(
            text("INSERT INTO people (name) VALUES (:n) RETURNING id"), {"n": name},
        )).scalar_one())
        repo = VoiceProfilesRepository(s)
        for pcm, room in samples:
            await repo.add(
                person_id=pid, embedding=await _vec(pcm),
                model=repo.EMBEDDING_MODEL_TAG, room_id=room, sample_seconds=1.0,
            )
        await s.commit()
    return pid


async def _samples(pid: int) -> list[tuple[str, str | None]]:
    async with SessionLocal() as s:
        rows = (await s.execute(
            text("SELECT source, room_id FROM voice_profiles WHERE person_id = :p ORDER BY id"),
            {"p": pid},
        )).all()
    return [(r[0], r[1]) for r in rows]


async def _people() -> list[tuple[int, str]]:
    async with SessionLocal() as s:
        rows = (await s.execute(text("SELECT id, name FROM people ORDER BY id"))).all()
    return [(int(r[0]), r[1]) for r in rows]


class SatelliteRoom(Room):
    """A satellite whose turns are heard as ``said`` in ``voice``, then
    identified and routed for real."""

    def __init__(self) -> None:
        super().__init__()
        self.said = ""
        self.voice = STRANGER
        self.utt = 0

    async def say(self, said: str, voice: bytes | None = None, *, trigger: str = "wake_word") -> dict:
        """One whole capture; returns its response_end frame, with the
        reply's text and handler (its response_start frame) added."""
        self.said = said
        if voice is not None:
            self.voice = voice
        self.utt += 1
        before = len(self.texts())
        await self.turn(trigger=trigger, utt=self.utt)
        sent = self.texts()[before:]
        ends = [f for f in sent if f["type"] == "response_end"]
        assert len(ends) == 1, sent
        starts = [f for f in sent if f["type"] == "response_start"]
        return {
            **ends[0],
            "text": starts[-1]["text"] if starts else None,
            "matched_handler": starts[-1].get("matched_handler") if starts else None,
            "frames": sent,
        }

    async def pending(self):
        async with session_scope() as s:
            ctx = await SessionRepository(s).get_context(self.sess.session_id) or {}
        return ctx.get("pending_confirmation")


@pytest.fixture
async def room(monkeypatch, db_session):
    """db_session only for its TRUNCATE; each turn opens its own session
    (streaming.session_scope), as on a satellite."""
    clear_choice_parked()
    voice_identifier._CONFIDENT_HITS.clear()
    r = SatelliteRoom()

    class _Whisper:
        async def transcribe(self, pcm):
            return r.said

        async def transcribe_wav_bytes(self, wav):
            return r.said

    class _TTS:
        async def synthesize(self, text, engine=None, voice=None):
            return _wav(0.05)

    async def _identify(pcm, embedding=None, **kw):
        # The capture's frames are silence; the voice is the scripted one.
        return await real_identify(r.voice, room_id=kw.get("room_id"))

    async def _any_voice(name):
        return (None, None)

    async def _hooks(self, s, *, response, **kw):
        return None

    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper())
    monkeypatch.setattr(streaming, "get_tts_client", lambda: _TTS())
    monkeypatch.setattr(streaming, "resolve_voice", _any_voice)
    monkeypatch.setattr(streaming.StreamSession, "_voice_profile_hooks", _hooks)
    monkeypatch.setattr("domovoi.voice_identifier.identify", _identify)
    yield r
    for entry in r.app.state.pending_music_start.values():
        entry["task"].cancel()
    clear_choice_parked()
    voice_identifier._CONFIDENT_HITS.clear()


def _reply(frame: dict) -> str:
    assert frame["text"] is not None, frame["frames"]
    return frame["text"]


# ─── The harness reads what it should ─────────────────────────────────────


async def test_the_harness_hears_the_scripted_voice(room) -> None:
    kamron = await _person("Kamron", (KAMRON_HERE, ROOM))
    end = await room.say("Who am I?", KAMRON_HERE)
    assert _reply(end) == "You're Kamron."
    end = await room.say("Who am I?", STRANGER)
    assert _reply(end) == "I don't recognize your voice yet."
    assert await _samples(kamron) == [("satellite", ROOM)]


# ─── "my name is X" at a satellite, for an X who exists ───────────────────


async def test_my_name_at_a_satellite_for_someone_known_asks_then_adds_a_room_sample(
    room, monkeypatch,
) -> None:
    kamron = await _person("Kamron", (KAMRON_OFFICE, "office"))
    asked = await room.say("My name is Kamron.", KAMRON_HERE)
    # This room's mic doesn't know him: the office sample is too far.
    assert _reply(asked) == ROOM_QUESTION
    assert asked["expect_followup"] is True
    pending = await room.pending()
    assert pending["kind"] == "core.room_voice_add" and pending["person_id"] == kamron
    assert await _samples(kamron) == [("satellite", "office")]

    # The answer comes in the follow-up; the QUESTION's clip is the one
    # saved, whoever says the yes.
    done = await room.say("Yes.", STRANGER, trigger="followup")
    assert _reply(done) == "Done — I'll know your voice in this room now, Kamron."
    assert await _people() == [(kamron, "Kamron")]
    assert await _samples(kamron) == [("satellite", "office"), ("satellite", ROOM)]
    assert await room.pending() is None

    # The stub's two clips of one person are unrelated vectors (a real
    # voice's are not), so his centroid scores ~0.71 against either: take
    # the threshold below that to see the room now name him.
    monkeypatch.setattr(settings, "voice_profile_match_threshold", 0.65)
    later = await room.say("Who am I?", KAMRON_HERE)
    assert _reply(later) == "You're Kamron."
    # A phone sample was never written.
    async with SessionLocal() as s:
        assert (await s.execute(
            text("SELECT count(*) FROM voice_profiles WHERE source = 'phone'"),
        )).scalar_one() == 0


async def test_no_says_how_to_be_told_apart_and_a_full_name_enrols(room) -> None:
    kamron = await _person("Kamron", (KAMRON_OFFICE, "office"))
    await room.say("My name is Kamron.", STRANGER)
    no = await room.say("No.", STRANGER, trigger="followup")
    assert _reply(no) == (
        "Okay, I won't add it. If you're a different Kamron, tell me your full "
        "name so I can tell you apart."
    )
    assert await _samples(kamron) == [("satellite", "office")]

    asked = await room.say("My name is Kamron Smith.", STRANGER)
    assert _reply(asked) == "Nice to meet you, Kamron Smith. Did I get that right?"
    done = await room.say("Yes.", STRANGER, trigger="followup")
    assert _reply(done) == "Got it, Kamron Smith."
    people = await _people()
    assert [n for _, n in people] == ["Kamron", "Kamron Smith"]
    assert await _samples(people[1][0]) == [("satellite", ROOM)]


async def test_two_people_with_the_name_at_a_satellite_are_never_guessed_between(room) -> None:
    dad = await _person("Alex", (KAMRON_OFFICE, "office"))
    son = await _person("alex", (STRANGER, "office"))
    asked = await room.say("My name is Alex.", KAMRON_HERE)
    assert _reply(asked) == _more_than_one("Alex")
    assert asked["expect_followup"] is False
    assert await room.pending() is None
    yes = await room.say("Yes.", KAMRON_HERE)
    assert yes["matched_handler"] != "voice_profile"
    assert await _samples(dad) == [("satellite", "office")]
    assert await _samples(son) == [("satellite", "office")]
    assert len(await _people()) == 2


async def test_someone_the_voice_already_names_is_told_so(room) -> None:
    """The voice named this Kamron (not a guess), so there is nothing to
    ask, even with a second Kamron in the house."""
    kamron = await _person("Kamron", (KAMRON_HERE, ROOM))
    await _person("Kamron", (KAMRON_OFFICE, "office"))
    end = await room.say("My name is Kamron.", KAMRON_HERE)
    assert _reply(end) == "I already know you, Kamron."
    assert await _samples(kamron) == [("satellite", ROOM)]


async def test_the_room_question_is_answered_by_the_next_turn_or_not_at_all(room) -> None:
    kamron = await _person("Kamron", (KAMRON_OFFICE, "office"))
    await room.say("My name is Kamron.", KAMRON_HERE)
    other = await room.say("What time is it?", KAMRON_HERE, trigger="followup")
    assert other["matched_handler"] == "clock"
    assert await room.pending() is None
    late = await room.say("Yes.", KAMRON_HERE)
    assert late["matched_handler"] != "voice_profile"
    assert await _samples(kamron) == [("satellite", "office")]


async def test_the_room_question_runs_out(room, monkeypatch) -> None:
    from domovoi.handlers import voice_profile

    monkeypatch.setattr(voice_profile, "VOICE_ADD_TTL_SEC", -1.0)
    kamron = await _person("Kamron", (KAMRON_OFFICE, "office"))
    await room.say("My name is Kamron.", KAMRON_HERE)
    late = await room.say("Yes.", KAMRON_HERE, trigger="followup")
    assert _reply(late) == (
        "That was a while ago, so I didn't add it. Say your name again if you'd like me to."
    )
    assert await _samples(kamron) == [("satellite", "office")]


async def test_an_opted_out_voice_is_not_added_by_the_rooms_yes(room) -> None:
    kamron = await _person("Kamron", (KAMRON_OFFICE, "office"))
    await room.say("My name is Kamron.", KAMRON_HERE)
    # Opted out between the question and the yes (the identifier would
    # have hidden an opted-out voice from the question itself).
    async with SessionLocal() as s:
        await VoiceDenylistRepository(s).add(
            embedding=await _vec(KAMRON_HERE), model=VoiceDenylistRepository.EMBEDDING_MODEL_TAG,
        )
        await s.commit()
    no = await room.say("Yes.", STRANGER, trigger="followup")
    assert _reply(no) == "I wasn't able to save that voice profile."
    assert await _samples(kamron) == [("satellite", "office")]


async def test_a_person_forgotten_before_the_rooms_yes_is_not_brought_back(room) -> None:
    kamron = await _person("Kamron", (KAMRON_OFFICE, "office"))
    await room.say("My name is Kamron.", KAMRON_HERE)
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM people WHERE id = :p"), {"p": kamron})
        await s.commit()
    done = await room.say("Yes.", KAMRON_HERE, trigger="followup")
    assert _reply(done) == "I can't find Kamron any more, so I didn't save that."
    assert await _people() == []
    async with SessionLocal() as s:
        assert (await s.execute(text("SELECT count(*) FROM voice_profiles"))).scalar_one() == 0


@pytest.mark.asyncio
async def test_the_room_question_takes_no_answer_from_a_chat_mic(db_session) -> None:
    """A satellite's question is answered by a satellite: the same yes
    from a phone's chat mic writes nothing (and a phone-asked one is never
    a room's)."""
    kamron = await _person("Kamron", (KAMRON_OFFICE, "office"))
    handler = VoiceProfileHandler()
    vec = np.frombuffer(await _vec(KAMRON_HERE), dtype=np.float32)
    asked_here = {
        "name": "Kamron", "person_id": kamron, "embedding_b64": _b64_embedding(vec),
        "origin": "satellite", "origin_room": ROOM,
    }
    chat_mic = Context(room_id="phone-android-1", device_kind="phone", trigger="push_to_talk")
    satellite = Context(room_id=ROOM, trigger="followup")
    elsewhere = "I asked that somewhere else, so I won't take the answer here."
    async with SessionLocal() as s:
        r1 = await handler._handle_room_voice_add(asked_here, True, chat_mic, s)
        r2 = await handler._handle_room_voice_add(
            {**asked_here, "origin": "phone", "origin_room": "phone-android-1"}, True, chat_mic, s,
        )
        r3 = await handler._handle_room_voice_add(
            {**asked_here, "origin": "phone", "origin_room": "phone-android-1"}, True, satellite, s,
        )
        await s.commit()
    assert r1.text == r2.text == r3.text == elsewhere
    assert await _samples(kamron) == [("satellite", "office")]


# ─── Enrolment proper: the yes re-checks the name ────────────────────────


async def test_a_name_taken_between_the_question_and_the_yes_gets_the_sample_not_a_twin(room) -> None:
    asked = await room.say("My name is Ana.", KAMRON_HERE)
    assert _reply(asked) == "Nice to meet you, Ana. Did I get that right?"
    ana = await _person("Ana", (KAMRON_OFFICE, "office"))   # enrolled elsewhere meanwhile
    done = await room.say("Yes.", KAMRON_HERE, trigger="followup")
    assert _reply(done) == "Done — I'll know your voice in this room now, Ana."
    assert await _people() == [(ana, "Ana")]
    assert await _samples(ana) == [("satellite", "office"), ("satellite", ROOM)]


@pytest.mark.asyncio
async def test_two_enrolments_of_one_new_name_at_once_make_one_person(db_session) -> None:
    """The name lock: without it both see "nobody called Ana" and both
    create one (the second holds its transaction open past the first's
    check)."""
    handler = VoiceProfileHandler()

    async def enrol(pcm: bytes, room_id: str) -> str:
        vec = np.frombuffer(await _vec(pcm), dtype=np.float32)
        async with SessionLocal() as s:
            r = await handler._enroll("Ana", vec, Context(room_id=room_id), s)
            await asyncio.sleep(0.3)   # the transaction stays open a while
            await s.commit()
        return r.text

    replies = await asyncio.gather(enrol(KAMRON_HERE, "kitchen"), enrol(STRANGER, "office"))
    people = await _people()
    assert [n for _, n in people] == ["Ana"]
    assert sorted(replies) == sorted([
        "Got it, Ana.", "Done — I'll know your voice in this room now, Ana.",
    ])
    assert sorted(r for _, r in await _samples(people[0][0])) == ["kitchen", "office"]


async def _park_was_that(sid: UUID, name: str, voice: bytes) -> None:
    """A satellite's "By the way, was that <name>?" (third-party intro), as
    maybe_inject_third_party_ask parks it."""
    from domovoi.confirmations import request_confirmation

    vec = np.frombuffer(await _vec(voice), dtype=np.float32)
    async with SessionLocal() as s:
        await request_confirmation(
            s, sid, kind="core.third_party_intro_confirm", handler="voice_profile",
            data={
                "name": name, "cluster_centroid_b64": _b64_embedding(vec), "cluster_idx": 0,
                "origin": "satellite", "origin_room": ROOM,
            },
        )
        await s.commit()


async def test_was_that_x_at_a_satellite_adds_to_the_existing_x(room) -> None:
    alex = await _person("Alex", (KAMRON_OFFICE, "office"))
    await room.say("What time is it?", STRANGER)
    await _park_was_that(room.sess.session_id, "Alex", KAMRON_HERE)
    yes = await room.say("Yes.", STRANGER, trigger="followup")
    assert _reply(yes) == "Done — I'll know your voice in this room now, Alex."
    assert await _people() == [(alex, "Alex")]
    assert await _samples(alex) == [("satellite", "office"), ("satellite", ROOM)]


async def test_was_that_x_at_a_satellite_never_guesses_between_two(room) -> None:
    await _person("Alex", (KAMRON_OFFICE, "office"))
    await _person("Alex", (STRANGER, "office"))
    await room.say("What time is it?", STRANGER)
    await _park_was_that(room.sess.session_id, "Alex", KAMRON_HERE)
    yes = await room.say("Yes.", STRANGER, trigger="followup")
    assert _reply(yes) == _more_than_one("Alex")
    assert len(await _people()) == 2
    async with SessionLocal() as s:
        assert (await s.execute(
            text("SELECT count(*) FROM voice_profiles WHERE room_id = :r"), {"r": ROOM},
        )).scalar_one() == 0


# ─── "who am I" / "forget me" ────────────────────────────────────────────


async def test_who_am_i_says_when_someone_else_has_your_name(room) -> None:
    await _person("Kamron", (KAMRON_HERE, ROOM))
    await _person("Kamron", (KAMRON_OFFICE, "office"))
    end = await room.say("Who am I?", KAMRON_HERE)
    assert _reply(end) == f"You're Kamron. I know more than one Kamron, though. {SORT_IT_OUT}"


async def test_who_am_i_with_a_voice_that_matches_two_kamrons(room) -> None:
    """The duplicate the old satellite path made: two Kamrons with
    Kamron's voice, so the identifier can name neither."""
    first = await _person("Kamron", (KAMRON_HERE, "office"))
    second = await _person("Kamron", (KAMRON_HERE, ROOM))
    heard = await real_identify(KAMRON_HERE, room_id=ROOM)
    assert heard.person_id is None and heard.ambiguous
    assert sorted(heard.candidates) == sorted([first, second])
    end = await room.say("Who am I?", KAMRON_HERE)
    assert _reply(end) == (
        f"Your voice matches more than one Kamron, so I can't tell which one you are. {SORT_IT_OUT}"
    )
    # And "my name is Kamron" won't pick either.
    asked = await room.say("My name is Kamron.", KAMRON_HERE)
    assert _reply(asked) == _more_than_one("Kamron")


async def test_who_am_i_with_a_voice_close_to_two_different_people(room) -> None:
    await _person("Kamron", (KAMRON_HERE, "office"))
    await _person("Chevy", (KAMRON_HERE, "office"))
    end = await room.say("Who am I?", KAMRON_HERE)
    assert _reply(end) == (
        "Your voice is close to two people I know, so I won't guess which one you are. "
        "Tell me your name, and I'll learn your voice better."
    )


async def test_forget_me_never_guesses_whose_profile_to_delete(room) -> None:
    await _person("Kamron", (KAMRON_HERE, "office"))
    await _person("Kamron", (KAMRON_HERE, ROOM))
    end = await room.say("Forget me.", KAMRON_HERE)
    assert _reply(end) == (
        "Your voice is close to two people I know, so I won't guess whose voice to "
        "forget. An admin can remove a person on the People page."
    )
    assert len(await _people()) == 2
    plain = await room.say("Forget me.", STRANGER)
    assert _reply(plain) == "I don't have a voice profile for you to forget."


async def test_who_am_i_from_the_phone_with_a_voice_that_matches_two_kamrons(
    db_session, open_gate, whisper,
) -> None:
    """The chat mic's turn carries the identifier's candidates too
    (domovoi/phone_voice.py): with no phone samples, a phone turn is
    matched on the satellite ones, here both Kamrons'."""
    await _person("Kamron", (KAMRON_HERE, "office"))
    await _person("Kamron", (KAMRON_HERE, "kitchen"))
    tid = await _thread()
    async with _client() as c:
        said = await _say(c, tid, whisper, "Who am I?", KAMRON_HERE)
    assert said["speaker"] is None
    assert said["reply"] == (
        f"Your voice matches more than one Kamron, so I can't tell which one you are. {SORT_IT_OUT}"
    )


@pytest.mark.asyncio
async def test_the_identifier_names_its_candidates_only_when_it_cannot_choose(db_session) -> None:
    kamron = await _person("Kamron", (KAMRON_HERE, "office"))
    chevy = await _person("Chevy", (KAMRON_HERE, "office"))
    sam = await _person("Sam", (STRANGER, "kitchen"))
    tied = await real_identify(KAMRON_HERE, room_id="office")
    assert tied.ambiguous and tied.person_id is None
    assert sorted(tied.candidates) == sorted([kamron, chevy])
    clear = await real_identify(STRANGER, room_id="kitchen")
    assert clear.person_id == sam and not clear.ambiguous and clear.candidates == ()
    nobody = await real_identify(KAMRON_OFFICE, room_id="office")
    assert nobody.person_id is None and nobody.candidates == ()


# ─── The People page ─────────────────────────────────────────────────────


def _web_people(*names: str) -> list[int]:
    from domovoi.db.session import engine

    async def _seed() -> list[int]:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE people RESTART IDENTITY CASCADE"))
            ids = []
            for n in names:
                ids.append(int((await conn.execute(
                    text("INSERT INTO people (name) VALUES (:n) RETURNING id"), {"n": n},
                )).scalar_one()))
            return ids

    return asyncio.run(_seed())


def _web():
    from web.backend.main import app as web_app

    return TestClient(web_app, headers={"X-Requested-With": "test"})


def test_the_roster_says_who_shares_a_name() -> None:
    alex, alex2, sam = _web_people("Alex", " alex ", "Sam")
    with _web() as c:
        listed = {p["id"]: p["namesakes"] for p in c.get("/api/people").json()}
        assert listed == {alex: 1, alex2: 1, sam: 0}
        assert c.get(f"/api/people/{alex2}").json()["namesakes"] == 1
        assert c.get(f"/api/people/{sam}").json()["namesakes"] == 0


def test_rename_refuses_a_name_someone_else_has_and_sorts_out_a_pair() -> None:
    alex, alex2, sam = _web_people("Alex", "Alex", "Sam")
    with _web() as c:
        taken = c.patch(f"/api/people/{sam}", json={"name": "  ALEX "})
        assert taken.status_code == 409, taken.text
        assert taken.json()["detail"].startswith("Someone else is already called ALEX.")
        assert c.get(f"/api/people/{sam}").json()["name"] == "Sam"

        # Two Alexes: renaming one tells them apart, for the roster and voice.
        done = c.patch(f"/api/people/{alex2}", json={"name": " Alex   Junior "})
        assert done.status_code == 200, done.text
        assert done.json()["name"] == "Alex Junior" and done.json()["namesakes"] == 0
        assert {p["id"]: p["namesakes"] for p in c.get("/api/people").json()} == {
            alex: 0, alex2: 0, sam: 0,
        }
        # A person's own name in another case is not "someone else's".
        own = c.patch(f"/api/people/{alex}", json={"name": "ALEX"})
        assert own.status_code == 200 and own.json()["name"] == "ALEX"

        assert c.patch("/api/people/999999", json={"name": "Zed"}).status_code == 404
        assert c.patch(f"/api/people/{sam}", json={"name": "   "}).status_code == 422
        assert c.patch(f"/api/people/{sam}", json={"name": "x" * 61}).status_code == 422
        assert c.patch(f"/api/people/{sam}", json={"name": "Sam\x1bSmith"}).status_code == 422
        # Whitespace of any kind is a single space.
        assert c.patch(f"/api/people/{sam}", json={"name": "Sam\n\tSmith"}).json()["name"] == "Sam Smith"


@pytest.mark.asyncio
async def test_after_the_rename_my_name_finds_the_one_person(db_session) -> None:
    """What the spoken refusal tells the household to do works: once an
    admin renames one Alex, "my name is Alex" asks about the other."""
    dad = await _person("Alex", (KAMRON_OFFICE, "office"))
    son = await _person("Alex", (STRANGER, "office"))
    async with SessionLocal() as s:
        assert await PeopleRepository(s).rename(son, "Alex Junior") == "renamed"
        assert await PeopleRepository(s).rename(dad, "alex junior") == "taken"
        sid = await SessionRepository(s).get_or_create(None, ROOM)
        await s.commit()
        ctx = Context(
            room_id=ROOM, session_id=sid, trigger="wake_word",
            embedding_bytes=await _vec(KAMRON_HERE),
        )
        r = await VoiceProfileHandler()._start_self_intro("Alex", ctx, s)
        await s.commit()
    assert r.text == "I already know someone called Alex. Is that you?"
