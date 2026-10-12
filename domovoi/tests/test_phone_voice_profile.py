"""Phone voice samples (V026): "my name is …" from the app's chat mic.

Owner design, 2026-10-12. A voice reaches a phone's mic and a satellite's
far-field mic very differently, so a person keeps two sets of samples
(``voice_profiles.source``): ``satellite`` and ``phone``.

* "my name is X" from the chat mic, where X already exists, never makes a
  second X: Domovoi asks "Add this phone's voice to X?", and "yes" on the
  next press stores the clip as one of X's PHONE samples; "no" changes
  nothing. A new name enrols as today, into the phone set.
* Satellites match satellite samples only — the test below fails if a
  phone sample reaches a satellite centroid. A phone turn matches a
  person's phone samples when they have any, else their satellite ones.
* No passive learning from phone turns; at most
  ``voice_profile_phone_sample_cap`` phone samples per person.
* Only the chat mic: a future "phone as a satellite" mode may not create
  or extend phone profiles (``voice_profile.phone_chat_mic``).

Speech-to-text is faked (the scripted transcripts); the voice embedder is
the stub (same PCM, same vector), and everything else — the identifier,
the router, the handler, the database — is real.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from domovoi import admin_auth, phone_voice, voice_identifier
from domovoi.clients.voice_embedder import StubVoiceEmbedder
from domovoi.config import settings
from domovoi.db.repositories import VoiceProfilesRepository
from domovoi.db.session import SessionLocal
from domovoi.handlers.voice_profile import phone_chat_mic
from domovoi.main import app as core_app
from domovoi.models import Context
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_phone_voice import ROOM, _client, _thread, _turn, _Whisper
from domovoi.voice_identifier import PHONE_SAMPLES, identify, phone_matching_samples

# One PCM per voice-and-mic: the stub embedder gives the same vector for
# the same bytes and an unrelated one (cosine near 0) for any other.
KAMRON_PHONE = np.full(16_000, 1234, dtype=np.int16).tobytes()
KAMRON_ROOM = np.full(16_000, 5678, dtype=np.int16).tobytes()
STRANGER = np.full(16_000, 9999, dtype=np.int16).tobytes()


@pytest.fixture
def whisper(monkeypatch):
    fake = _Whisper()
    monkeypatch.setattr(phone_voice, "get_whisper_client", lambda: fake)
    return fake


@pytest.fixture
def open_gate():
    core_app.dependency_overrides[admin_auth.require_device_after_setup] = lambda: None
    phone_voice.reset_for_tests()
    voice_identifier._CONFIDENT_HITS.clear()
    yield
    core_app.dependency_overrides.pop(admin_auth.require_device_after_setup, None)
    phone_voice.reset_for_tests()
    voice_identifier._CONFIDENT_HITS.clear()


async def _vec(pcm: bytes) -> bytes:
    return (await StubVoiceEmbedder().embed(pcm)).astype(np.float32).tobytes()


async def _person(name: str, *, satellite: bytes | None = None, phone: bytes | None = None) -> int:
    """A person, with a satellite sample (room 'kitchen') and/or a phone
    sample made from those PCMs."""
    async with SessionLocal() as s:
        pid = int((await s.execute(
            text("INSERT INTO people (name) VALUES (:n) RETURNING id"), {"n": name},
        )).scalar_one())
        repo = VoiceProfilesRepository(s)
        if satellite is not None:
            await repo.add(
                person_id=pid, embedding=await _vec(satellite),
                model=repo.EMBEDDING_MODEL_TAG, room_id="kitchen", sample_seconds=1.0,
            )
        if phone is not None:
            await repo.add(
                person_id=pid, embedding=await _vec(phone),
                model=repo.EMBEDDING_MODEL_TAG, room_id="phone-android-old", sample_seconds=None,
                source="phone",
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


async def _people_named(name: str) -> int:
    async with SessionLocal() as s:
        return int((await s.execute(
            text("SELECT count(*) FROM people WHERE lower(name) = lower(:n)"), {"n": name},
        )).scalar_one())


async def _say(c, tid: int, whisper: _Whisper, said: str, pcm: bytes) -> dict:
    whisper.texts = [said]
    r = await _turn(c, tid, audio=pcm)
    assert r.status_code == 200, r.text
    return r.json()


# ─── Pure ─────────────────────────────────────────────────────────────────


def test_a_phone_turn_scores_each_person_on_their_phone_set_when_they_have_one() -> None:
    rows = [
        (1, 10, b"a-sat", "kitchen", "satellite"),
        (2, 10, b"a-phone", "phone-x", "phone"),
        (3, 20, b"b-sat", "kitchen", "satellite"),
        (4, 20, b"b-sat2", "office", "satellite"),
        (5, 30, b"c-phone", "phone-y", "phone"),
    ]
    assert sorted(phone_matching_samples(rows)) == sorted([
        (10, b"a-phone"), (20, b"b-sat"), (20, b"b-sat2"), (30, b"c-phone"),
    ])


def test_only_the_chat_mic_may_touch_phone_samples() -> None:
    chat_mic = Context(room_id=ROOM, device_kind="phone", trigger="push_to_talk")
    assert phone_chat_mic(chat_mic)
    # A satellite's turn — and so a future "phone as a satellite" mode,
    # which would come through a satellite's stream — may not.
    assert not phone_chat_mic(Context(room_id="kitchen", trigger="wake_word"))
    assert not phone_chat_mic(Context(room_id="kitchen", trigger="push_to_talk"))
    assert not phone_chat_mic(chat_mic.model_copy(update={"room_id": "kitchen"}))
    assert not phone_chat_mic(chat_mic.model_copy(update={"trigger": "wake_word"}))
    assert not phone_chat_mic(chat_mic.model_copy(update={"device_kind": None}))


# ─── Matching ─────────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_satellite_matching_never_sees_a_phone_sample(db_session, open_gate) -> None:
    """Kamron's satellite centroid is his satellite sample alone, however
    many phone samples he has (with a phone sample in it his own room
    voice would score ~0.7, not 1.0); and a person whose only sample is
    a phone sample is nobody to a satellite."""
    kamron = await _person("Kamron", satellite=KAMRON_ROOM, phone=KAMRON_PHONE)
    await _person("Pat", phone=STRANGER)

    heard = await identify(KAMRON_ROOM, room_id="kitchen")
    assert heard.person_id == kamron
    assert heard.best_similarity == pytest.approx(1.0, abs=1e-4)

    # Pat's phone sample IS this clip; a satellite never scores it.
    pat = await identify(STRANGER, room_id="kitchen")
    assert pat.person_id is None and pat.best_similarity < 0.5
    assert (await identify(KAMRON_PHONE, room_id="kitchen")).person_id is None


@requires_db
@pytest.mark.asyncio
async def test_a_phone_turn_matches_phone_samples_first_and_learns_nothing(
    db_session, open_gate, monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "voice_profile_match_threshold", 0.95)
    monkeypatch.setattr(settings, "voice_profile_drift_near_threshold_margin", 0.01)
    monkeypatch.setattr(settings, "voice_profile_drift_reenroll_after", 1)
    kamron = await _person("Kamron", satellite=KAMRON_ROOM, phone=KAMRON_PHONE)
    ana = await _person("Ana", satellite=STRANGER)

    assert (await identify(KAMRON_PHONE, samples=PHONE_SAMPLES)).person_id == kamron
    # Kamron has phone samples, so his satellite one is not what a phone
    # turn is held against...
    assert (await identify(KAMRON_ROOM, samples=PHONE_SAMPLES)).person_id is None
    # ...and Ana, with none, is matched on her satellite sample.
    assert (await identify(STRANGER, samples=PHONE_SAMPLES)).person_id == ana
    # Confident matches, even with a room id handed in: nothing is saved.
    for _ in range(4):
        await identify(KAMRON_PHONE, room_id="phone-android-x", samples=PHONE_SAMPLES)
        await identify(STRANGER, room_id="phone-android-x", samples=PHONE_SAMPLES)
    assert await _samples(kamron) == [("satellite", "kitchen"), ("phone", "phone-android-old")]
    assert await _samples(ana) == [("satellite", "kitchen")]


# ─── "my name is …" from the chat mic ─────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_my_name_from_the_phone_for_someone_known_asks_then_adds_a_phone_sample(
    db_session, open_gate, whisper,
) -> None:
    kamron = await _person("Kamron", satellite=KAMRON_ROOM)
    tid = await _thread()
    async with _client() as c:
        asked = await _say(c, tid, whisper, "My name is Kamron.", KAMRON_PHONE)
        # The phone mic's Kamron is nobody yet: the satellite sample is too far.
        assert asked["speaker"] is None
        assert asked["reply"] == "Add this phone's voice to Kamron?"
        assert asked["expect_followup"] is True
        assert await _samples(kamron) == [("satellite", "kitchen")]

        done = await _say(c, tid, whisper, "Yes.", STRANGER)
        assert done["matched_path"] == "confirmation"
        assert done["reply"] == "Done — I'll know your voice on the phone now, Kamron."
        # No second Kamron; the QUESTION's clip went into his phone set,
        # not the "yes" (said here by someone else entirely).
        assert await _people_named("Kamron") == 1
        assert await _samples(kamron) == [("satellite", "kitchen"), ("phone", ROOM)]

        later = await _say(c, tid, whisper, "Who am I?", KAMRON_PHONE)
    assert later["speaker"]["person_id"] == kamron
    assert later["reply"] == "You're Kamron."
    # His satellite centroid did not move: the phone clip is nobody there.
    assert (await identify(KAMRON_PHONE, room_id="kitchen")).person_id is None
    assert (await identify(KAMRON_ROOM, room_id="kitchen")).best_similarity == pytest.approx(1.0, abs=1e-4)


@requires_db
@pytest.mark.asyncio
async def test_no_to_adding_the_phone_voice_changes_nothing(db_session, open_gate, whisper) -> None:
    kamron = await _person("Kamron", satellite=KAMRON_ROOM)
    tid = await _thread()
    async with _client() as c:
        await _say(c, tid, whisper, "my name is kamron", KAMRON_PHONE)
        no = await _say(c, tid, whisper, "No.", KAMRON_PHONE)
        assert no["reply"] == "Okay, I won't add it."
        later = await _say(c, tid, whisper, "Who am I?", KAMRON_PHONE)
    assert await _people_named("Kamron") == 1
    assert await _samples(kamron) == [("satellite", "kitchen")]
    assert later["speaker"] is None


@requires_db
@pytest.mark.asyncio
async def test_a_new_name_from_the_phone_enrols_into_the_phone_set(db_session, open_gate, whisper) -> None:
    tid = await _thread()
    async with _client() as c:
        asked = await _say(c, tid, whisper, "My name is Ana.", KAMRON_PHONE)
        assert asked["reply"] == "Nice to meet you, Ana. Did I get that right?"
        done = await _say(c, tid, whisper, "Yes.", KAMRON_PHONE)
        assert done["reply"] == "Got it, Ana."
        later = await _say(c, tid, whisper, "Who am I?", KAMRON_PHONE)
    async with SessionLocal() as s:
        ana = int((await s.execute(text("SELECT id FROM people WHERE name = 'Ana'"))).scalar_one())
    assert await _samples(ana) == [("phone", ROOM)]
    assert later["speaker"]["person_id"] == ana
    # A satellite does not know her from a phone sample.
    assert (await identify(KAMRON_PHONE, room_id="kitchen")).person_id is None


@requires_db
@pytest.mark.asyncio
async def test_phone_samples_are_capped_oldest_first(db_session, open_gate, whisper, monkeypatch) -> None:
    monkeypatch.setattr(settings, "voice_profile_phone_sample_cap", 2)
    kamron = await _person("Kamron", satellite=KAMRON_ROOM)
    clips = [np.full(16_000, v, dtype=np.int16).tobytes() for v in (101, 202, 303)]
    tid = await _thread()
    async with _client() as c:
        for clip in clips:
            asked = await _say(c, tid, whisper, "My name is Kamron.", clip)
            assert asked["reply"] == "Add this phone's voice to Kamron?"
            await _say(c, tid, whisper, "Yes.", clip)
    async with SessionLocal() as s:
        kept = (await s.execute(
            text("SELECT embedding FROM voice_profiles WHERE person_id = :p AND source = 'phone' ORDER BY id"),
            {"p": kamron},
        )).scalars().all()
    assert [bytes(e) for e in kept] == [await _vec(clips[1]), await _vec(clips[2])]
    assert [s for s in await _samples(kamron) if s[0] == "satellite"] == [("satellite", "kitchen")]


@requires_db
@pytest.mark.asyncio
async def test_a_person_forgotten_before_the_yes_is_not_brought_back(db_session, open_gate, whisper) -> None:
    kamron = await _person("Kamron", satellite=KAMRON_ROOM)
    tid = await _thread()
    async with _client() as c:
        await _say(c, tid, whisper, "My name is Kamron.", KAMRON_PHONE)
        async with SessionLocal() as s:
            await s.execute(text("DELETE FROM people WHERE id = :p"), {"p": kamron})
            await s.commit()
        done = await _say(c, tid, whisper, "Yes.", KAMRON_PHONE)
    assert done["reply"] == "I can't find Kamron any more, so I didn't save that."
    assert await _people_named("Kamron") == 0
    async with SessionLocal() as s:
        assert (await s.execute(text("SELECT count(*) FROM voice_profiles"))).scalar_one() == 0


@requires_db
@pytest.mark.asyncio
async def test_on_a_satellite_my_name_still_takes_the_old_path(db_session, open_gate) -> None:
    """Out of scope, pinned so it is a decision: a satellite's "my name is
    X" for an X who exists still asks "Did I get that right?" (the
    duplicate-name gap), and never writes a phone sample."""
    from domovoi.db.repositories import SessionRepository
    from domovoi.handlers.voice_profile import VoiceProfileHandler

    await _person("Kamron", satellite=KAMRON_ROOM)
    async with SessionLocal() as s:
        sid = await SessionRepository(s).get_or_create(None, "kitchen")
        await s.commit()
        ctx = Context(
            room_id="kitchen", session_id=sid, trigger="wake_word",
            embedding_bytes=await _vec(KAMRON_PHONE),
        )
        r = await VoiceProfileHandler()._start_self_intro("Kamron", ctx, s)
        await s.commit()
    assert r.text == "Nice to meet you, Kamron. Did I get that right?"


# ─── V026 ─────────────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_v026_applies_again_cleanly_and_files_old_phone_rows_as_phone(db_session) -> None:
    from sqlalchemy.exc import IntegrityError

    from domovoi.db.session import engine

    sql = (
        Path(phone_voice.__file__).parent / "db" / "migrations" / "V026__phone_voice_samples.sql"
    ).read_text(encoding="utf-8")
    statements = [
        st for st in sql.split(";")
        if any(ln.strip() and not ln.strip().startswith("--") for ln in st.splitlines())
    ]
    assert len(statements) == 3
    pid = await _person("Kamron")
    async with SessionLocal() as s:
        for room in ("kitchen", "phone-android-1a2b"):
            # As a phone's "my name is" wrote them before V026.
            await s.execute(
                text(
                    "INSERT INTO voice_profiles (person_id, embedding, model, room_id) "
                    "VALUES (:p, :e, 'resemblyzer-v1', :r)"
                ),
                {"p": pid, "e": await _vec(KAMRON_ROOM), "r": room},
            )
        await s.commit()
    for _ in range(2):
        async with engine.begin() as conn:
            for st in statements:
                await conn.exec_driver_sql(st)
    assert await _samples(pid) == [("satellite", "kitchen"), ("phone", "phone-android-1a2b")]
    async with SessionLocal() as s:
        with pytest.raises(IntegrityError):
            await s.execute(
                text(
                    "INSERT INTO voice_profiles (person_id, embedding, model, source) "
                    "VALUES (:p, :e, 'resemblyzer-v1', 'tablet')"
                ),
                {"p": pid, "e": await _vec(KAMRON_ROOM)},
            )


# ─── The People page's listing ────────────────────────────────────────────


@requires_db
def test_the_profiles_listing_says_which_set_and_an_admin_deletes_a_phone_sample() -> None:
    import asyncio

    from domovoi.db.session import engine
    from web.backend.main import app as web_app

    async def _seed() -> int:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE people RESTART IDENTITY CASCADE"))
            pid = int((await conn.execute(
                text("INSERT INTO people (name) VALUES ('Kamron') RETURNING id"),
            )).scalar_one())
            for room, source in (("kitchen", "satellite"), ("phone-android-1a2b", "phone")):
                await conn.execute(
                    text(
                        "INSERT INTO voice_profiles (person_id, embedding, model, room_id, source) "
                        "VALUES (:p, :e, 'resemblyzer-v1', :r, :s)"
                    ),
                    {"p": pid, "e": b"\0" * 1024, "r": room, "s": source},
                )
            return pid

    pid = asyncio.run(_seed())
    with TestClient(web_app) as c:
        listed = c.get(f"/api/people/{pid}/profiles")
        assert listed.status_code == 200, listed.text
        by_source = {row["source"]: row for row in listed.json()}
        assert set(by_source) == {"satellite", "phone"}
        assert by_source["phone"]["room_id"] == "phone-android-1a2b"
        assert "embedding" not in by_source["phone"]
        gone = c.delete(
            f"/api/people/{pid}/profiles/{by_source['phone']['id']}",
            headers={"X-Requested-With": "test"},
        )
        assert gone.status_code == 204, gone.text
        assert [row["source"] for row in c.get(f"/api/people/{pid}/profiles").json()] == ["satellite"]
