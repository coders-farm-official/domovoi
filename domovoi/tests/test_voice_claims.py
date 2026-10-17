"""A name claim is not proof (delta security review S1, owner decision
2026-10-16).

Since V026 / 2026-10-15, "my name is X" for an X who exists, then "yes",
adds the speaker's clip to X's samples. The review proved on the DB that a
stranger's phone could then say "forget me" and delete Kamron, his profile
and his memories (``review-delta-1016/test_zz_voice_takeover.py``). Now:

* "yes, that's me" still adds the clip (it teaches rooms the voice), marked
  as a claim (V029: when, where, and who the voice was recognized as);
* "forget me" deletes the person only for a voice that matches the
  person's OWN samples (the existing match threshold), or an admin does it
  on the People page; any other voice loses only the samples it claimed
  (and what rooms learned through them), and is told so plainly;
* a room sample learned from a voice recognized only through a claim goes
  with that claim;
* another phone's claims can't push the person's own phone samples out;
* the People page marks claims; where, by whom, and a phone sample's room
  (it names the phone) reach an admin only (N4).

Speech-to-text is scripted, the voice embedder is the stub (same PCM, same
vector; another PCM, an unrelated one), the rest is real, on the lane's
database.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from domovoi import phone_voice, voice_identifier
from domovoi.clients.voice_embedder import EMBEDDING_DIM, StubVoiceEmbedder
from domovoi.config import settings
from domovoi.db.repositories import VoiceProfilesRepository, VoiceSample
from domovoi.db.session import SessionLocal
from domovoi.handlers.voice_profile import VoiceProfileHandler
from domovoi.models import Context
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_phone_voice import ROOM as PHONE_ROOM
from domovoi.tests.test_phone_voice import _client, _thread
from domovoi.tests.test_phone_voice_profile import (  # noqa: F401 — fixtures
    KAMRON_PHONE,
    KAMRON_ROOM,
    STRANGER,
    _people_named,
    _person,
    _say,
    _vec,
    open_gate,
    whisper,
)

# ─── Pure: whose voice is it ──────────────────────────────────────────────


def _unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(EMBEDDING_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _sample(i: int, vec: np.ndarray, *, source="satellite", claimed=False, claim_of=None) -> VoiceSample:
    return VoiceSample(
        id=i, embedding=vec.astype(np.float32).tobytes(), source=source, room_id=None,
        claimed=claimed, claim_of=claim_of,
    )


KAMRON, STRANGER_V, OTHER = _unit(1), _unit(2), _unit(3)


def test_only_the_persons_own_samples_say_whose_voice_it_is() -> None:
    samples = [_sample(1, KAMRON), _sample(2, STRANGER_V, claimed=True)]
    assert voice_identifier.is_own_voice(KAMRON, samples, phone=False)
    # The stranger matches the claim it made, 1.0, and the centroid of both
    # (0.7): neither counts.
    assert not voice_identifier.is_own_voice(STRANGER_V, samples, phone=False)
    # Nobody's own voice at all (an admin made the person, or every sample
    # is a claim): no voice can prove it is them.
    assert not voice_identifier.is_own_voice(STRANGER_V, [samples[1]], phone=False)


def test_a_phone_turn_is_held_against_the_own_phone_set_first() -> None:
    samples = [
        _sample(1, KAMRON, source="satellite"),
        _sample(2, OTHER, source="phone"),
        _sample(3, STRANGER_V, source="phone", claimed=True),
    ]
    assert voice_identifier.is_own_voice(OTHER, samples, phone=True)
    assert not voice_identifier.is_own_voice(KAMRON, samples, phone=True)
    # No own phone sample: their own satellite voice decides.
    assert voice_identifier.is_own_voice(KAMRON, [samples[0], samples[2]], phone=True)


def test_the_claims_a_voice_made_are_the_ones_it_matches_or_taught_a_room() -> None:
    samples = [
        _sample(1, KAMRON),
        _sample(2, STRANGER_V, claimed=True),
        _sample(3, OTHER, claimed=True),               # someone else's claim
        _sample(4, STRANGER_V, claim_of=3),            # learned through claim 3
    ]
    assert voice_identifier.claims_by_this_voice(STRANGER_V, samples) == [2, 3]
    assert voice_identifier.claims_by_this_voice(OTHER, samples) == [3]
    assert voice_identifier.claims_by_this_voice(KAMRON, samples) == []


def test_a_room_learns_a_claimed_voice_as_part_of_the_claim() -> None:
    samples = [_sample(1, KAMRON), _sample(2, STRANGER_V, claimed=True)]
    assert voice_identifier.learned_through_claim(STRANGER_V, samples) == 2
    assert voice_identifier.learned_through_claim(KAMRON, samples) is None
    # No claim at all: nothing to inherit, whatever the voice.
    assert voice_identifier.learned_through_claim(STRANGER_V, [samples[0]]) is None
    # A claim learned again names the claim it stands on, not itself.
    chained = samples + [_sample(5, STRANGER_V, claim_of=2)]
    assert voice_identifier.learned_through_claim(STRANGER_V, chained) == 2


# ─── The review's proof, as a regression (the chat mic) ───────────────────


async def _memory(pid: int, body: str) -> None:
    async with SessionLocal() as s:
        await s.execute(
            text("INSERT INTO memories (person_id, body, source) VALUES (:p, :b, 'explicit')"),
            {"p": pid, "b": body},
        )
        await s.commit()


async def _memories(body: str) -> int:
    async with SessionLocal() as s:
        return int((await s.execute(
            text("SELECT count(*) FROM memories WHERE body = :b"), {"b": body},
        )).scalar_one())


async def _rows(pid: int) -> list[dict]:
    async with SessionLocal() as s:
        rows = (await s.execute(
            text(
                "SELECT id, source, room_id, claimed_at, claim_room, claimed_by_person_id, claim_of "
                "FROM voice_profiles WHERE person_id = :p ORDER BY id"
            ),
            {"p": pid},
        )).mappings().all()
    return [dict(r) for r in rows]


@requires_db
@pytest.mark.asyncio
async def test_a_stranger_claims_kamron_then_forget_me_takes_only_the_strangers_voice(
    db_session, open_gate, whisper,
) -> None:
    """review-delta-1016/test_zz_voice_takeover.py: it ended "people named
    Kamron left: 0 | his memory rows left: 0"."""
    kamron = await _person("Kamron", satellite=KAMRON_ROOM)
    await _memory(kamron, "kamron private note")
    tid = await _thread()
    async with _client() as c:
        asked = await _say(c, tid, whisper, "My name is Kamron.", STRANGER)
        assert asked["reply"] == "Add this phone's voice to Kamron?"
        await _say(c, tid, whisper, "Yes.", STRANGER)
        rows = await _rows(kamron)
        assert [(r["source"], r["claimed_at"] is not None) for r in rows] == [
            ("satellite", False), ("phone", True),
        ]
        claim = rows[1]
        assert claim["claim_room"] == PHONE_ROOM and claim["room_id"] == PHONE_ROOM
        assert claim["claimed_by_person_id"] is None          # an unknown voice
        who = await _say(c, tid, whisper, "Who am I?", STRANGER)
        assert who["reply"].startswith("You're Kamron")       # the claim still teaches the phone
        gone = await _say(c, tid, whisper, "Forget me.", STRANGER)
    assert gone["reply"] == (
        "I've forgotten the voice you added to Kamron. I kept Kamron's profile and "
        "memories: your voice doesn't match the one I learned for Kamron, so only an "
        "admin can delete those, on the People page."
    )
    assert await _people_named("Kamron") == 1
    assert await _memories("kamron private note") == 1
    assert [(r["source"], r["room_id"]) for r in await _rows(kamron)] == [("satellite", "kitchen")]


@requires_db
@pytest.mark.asyncio
async def test_forget_me_in_the_persons_own_voice_still_forgets_them(
    db_session, open_gate, whisper,
) -> None:
    """Kamron himself (his own phone sample) after a stranger's claim: the
    whole person goes, as it always did, and the answer says what."""
    kamron = await _person("Kamron", satellite=KAMRON_ROOM, phone=KAMRON_PHONE)
    async with SessionLocal() as s:
        # A second phone sample of his own, so a stranger's one claim
        # doesn't pull his phone centroid under the threshold (the stub's
        # voices are unrelated vectors, unlike two real speakers).
        repo = VoiceProfilesRepository(s)
        await repo.add(person_id=kamron, embedding=await _vec(KAMRON_PHONE), model=repo.EMBEDDING_MODEL_TAG,
                       room_id="phone-android-old", sample_seconds=None, source="phone")
        await s.commit()
    await _memory(kamron, "kamron private note")
    tid = await _thread()
    async with _client() as c:
        await _say(c, tid, whisper, "My name is Kamron.", STRANGER)
        await _say(c, tid, whisper, "Yes.", STRANGER)
        assert sum(r["claimed_at"] is not None for r in await _rows(kamron)) == 1
        asked = await _say(c, tid, whisper, "Forget me.", KAMRON_PHONE)
        assert asked["reply"] == "Kamron, I'll forget your voice, your profile, what I remember about you and everything you've said to me. Say yes to delete everything you've said."
        assert await _people_named("Kamron") == 1   # nothing before the yes
        gone = await _say(c, tid, whisper, "Yes.", KAMRON_PHONE)
    assert gone["reply"] == (
        "Okay, I've forgotten you, Kamron: your voice, your profile, everything "
        "I remembered about you and what you've said to me."
    )
    assert await _people_named("Kamron") == 0
    assert await _memories("kamron private note") == 0


@requires_db
@pytest.mark.asyncio
async def test_without_any_claim_forget_me_is_what_it_was(db_session, open_gate, whisper) -> None:
    ana = await _person("Ana", phone=KAMRON_PHONE)
    tid = await _thread()
    async with _client() as c:
        asked = await _say(c, tid, whisper, "Forget me.", KAMRON_PHONE)
        assert asked["reply"].startswith("Ana, I'll forget your voice")
        gone = await _say(c, tid, whisper, "Yes.", KAMRON_PHONE)
    assert gone["reply"].startswith("Okay, I've forgotten you, Ana")
    assert await _people_named("Ana") == 0
    assert ana


@requires_db
@pytest.mark.asyncio
async def test_a_known_voice_claiming_someone_else_is_recorded_as_who_claimed(
    db_session, open_gate, whisper,
) -> None:
    kamron = await _person("Kamron", satellite=KAMRON_ROOM)
    chevy = await _person("Chevy", phone=STRANGER)
    tid = await _thread()
    async with _client() as c:
        assert (await _say(c, tid, whisper, "My name is Kamron.", STRANGER))["reply"] == (
            "Add this phone's voice to Kamron?"
        )
        await _say(c, tid, whisper, "Yes.", STRANGER)
    claim = (await _rows(kamron))[-1]
    assert claim["claimed_by_person_id"] == chevy and claim["claimed_at"] is not None
    # That voice is now as close to Kamron (through the claim) as to Chevy:
    # "forget me" never guesses, and nobody is deleted.
    async with _client() as c:
        tid2 = await _thread()
        gone = await _say(c, tid2, whisper, "Forget me.", STRANGER)
    assert gone["reply"].startswith("Your voice is close to two people I know")
    assert await _people_named("Kamron") == 1 and await _people_named("Chevy") == 1


# ─── At a satellite ───────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_a_room_claim_is_marked_and_forget_me_from_that_voice_keeps_the_person(
    db_session,
) -> None:
    """The handler's own paths, at a satellite: "my name is Kamron" → "Is
    that you?" → yes adds the clip as a claim for the room; "forget me" from
    that voice (Kamron has no voice of his own yet: an admin made him on
    the People page) removes the claim and keeps Kamron."""
    from domovoi.db.repositories import PeopleRepository, SessionRepository

    handler = VoiceProfileHandler()
    async with SessionLocal() as s:
        kamron = await PeopleRepository(s).create(name="Kamron")
        sid = await SessionRepository(s).get_or_create(None, "garage")
        await s.commit()
    await _memory(kamron, "kamron garage note")
    stranger = await StubVoiceEmbedder().embed(STRANGER)
    ctx = Context(session_id=sid, room_id="garage", online=True, trigger="wake_word",
                  embedding_bytes=stranger.astype(np.float32).tobytes())
    async with SessionLocal() as s:
        asked = await handler._start_self_intro("Kamron", ctx, s)
        await s.commit()
    assert asked.text == "I already know someone called Kamron. Is that you?"
    async with SessionLocal() as s:
        pending = (await SessionRepository(s).get_context(sid))["pending_confirmation"]
        done = await handler.handle_choice_reply(pending["kind"], pending, "yes", ctx, s)
        await s.commit()
    assert done.text == "Done — I'll know your voice in this room now, Kamron."
    [row] = await _rows(kamron)
    assert row["claimed_at"] is not None and row["claim_room"] == "garage"
    assert row["claimed_by_person_id"] is None

    ctx = Context(session_id=sid, room_id="garage", online=True, trigger="wake_word",
                  person_id=kamron, embedding_bytes=stranger.astype(np.float32).tobytes())
    async with SessionLocal() as s:
        gone = await handler._forget(ctx, s)
        await s.commit()
    assert gone.text.startswith("I've forgotten the voice you added to Kamron.")
    assert ctx.person_id is None
    assert await _rows(kamron) == []
    assert await _people_named("Kamron") == 1
    assert await _memories("kamron garage note") == 1


@requires_db
@pytest.mark.asyncio
async def test_a_voice_that_neither_matches_nor_claimed_deletes_nothing(db_session) -> None:
    kamron = await _person("Kamron", satellite=KAMRON_ROOM)
    async with SessionLocal() as s:
        repo = VoiceProfilesRepository(s)
        await repo.add(person_id=kamron, embedding=await _vec(STRANGER), model=repo.EMBEDDING_MODEL_TAG,
                       room_id="office", sample_seconds=None, claimed=True, claim_room="office")
        await s.commit()
    third = await StubVoiceEmbedder().embed(np.full(16_000, 4242, dtype=np.int16).tobytes())
    handler = VoiceProfileHandler()
    for embedding, expected in (
        (third.astype(np.float32).tobytes(),
         "Your voice doesn't match the one I learned for Kamron, so I haven't forgotten anything."),
        (None, "I couldn't hear your voice well enough to check it's you, so I haven't forgotten anything."),
    ):
        ctx = Context(session_id=None, room_id="office", online=True, person_id=kamron,
                      embedding_bytes=embedding)
        async with SessionLocal() as s:
            r = await handler._forget(ctx, s)
            await s.commit()
        assert r.text.startswith(expected), r.text
        assert ctx.person_id == kamron
    assert len(await _rows(kamron)) == 2
    assert await _people_named("Kamron") == 1


@requires_db
@pytest.mark.asyncio
async def test_a_room_learns_a_claimed_voice_with_the_claim_and_loses_it_with_the_claim(
    db_session,
) -> None:
    """Job 4 (learning a room) on a voice recognized only through a claim:
    the learned sample points at the claim, and removing the claim (forget
    me, or an admin's delete) takes it too."""
    kamron = await _person("Kamron", satellite=KAMRON_ROOM)
    async with SessionLocal() as s:
        repo = VoiceProfilesRepository(s)
        claim = await repo.add(person_id=kamron, embedding=await _vec(STRANGER),
                               model=repo.EMBEDDING_MODEL_TAG, room_id="office",
                               sample_seconds=None, claimed=True, claim_room="office")
        await s.commit()
    decision = voice_identifier._Decision(kamron, kamron, 0.95, None, None, False, True)
    stranger = await StubVoiceEmbedder().embed(STRANGER)
    own = await StubVoiceEmbedder().embed(KAMRON_ROOM)
    voice_identifier._CONFIDENT_HITS.clear()
    try:
        for _ in range(settings.voice_profile_drift_reenroll_after):
            await voice_identifier._learn_room(kamron, "garage", stranger, {"kitchen", "office"}, decision)
        for _ in range(settings.voice_profile_drift_reenroll_after):
            await voice_identifier._learn_room(kamron, "attic", own, {"kitchen", "office"}, decision)
    finally:
        voice_identifier._CONFIDENT_HITS.clear()
    by_room = {r["room_id"]: r for r in await _rows(kamron)}
    assert by_room["garage"]["claim_of"] == claim and by_room["garage"]["claimed_at"] is None
    assert by_room["attic"]["claim_of"] is None                 # Kamron's own voice
    async with SessionLocal() as s:
        assert await VoiceProfilesRepository(s).delete_samples(kamron, [claim]) == 1
        await s.commit()
    assert sorted(r["room_id"] for r in await _rows(kamron)) == ["attic", "kitchen"]


# ─── The phone set's cap ──────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_another_phones_claims_never_push_the_persons_own_phone_voice_out(db_session) -> None:
    kamron = await _person("Kamron")
    cap = settings.voice_profile_phone_sample_cap
    async with SessionLocal() as s:
        repo = VoiceProfilesRepository(s)
        for i in range(cap):
            await repo.add_phone_sample(person_id=kamron, embedding=_unit(100 + i).tobytes(),
                                        model=repo.EMBEDDING_MODEL_TAG, room_id="phone-own", cap=cap)
        for i in range(cap + 2):
            await repo.add_phone_sample(person_id=kamron, embedding=_unit(200 + i).tobytes(),
                                        model=repo.EMBEDDING_MODEL_TAG, room_id="phone-other",
                                        cap=cap, claimed=True)
        await s.commit()
    rows = await _rows(kamron)
    assert len(rows) == cap
    # The newest claim is kept (it was just said yes to); the rest are the
    # person's own, newest first.
    assert [r["room_id"] for r in rows].count("phone-other") == 1
    assert [r["room_id"] for r in rows].count("phone-own") == cap - 1


# ─── The People page (and N4) ─────────────────────────────────────────────


@requires_db
def test_the_listing_marks_claims_and_keeps_who_and_where_for_an_admin(monkeypatch) -> None:
    from domovoi.db.session import engine
    from domovoi.tests.auth_testkit import bearer, install_fake_db

    ADMIN_TOKEN, DEVICE_TOKEN = "admin-token", "device-token"
    from web.backend.main import app as web_app

    async def _seed() -> tuple[int, int]:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE people RESTART IDENTITY CASCADE"))
            kamron = int((await conn.execute(
                text("INSERT INTO people (name) VALUES ('Kamron') RETURNING id"))).scalar_one())
            chevy = int((await conn.execute(
                text("INSERT INTO people (name) VALUES ('Chevy') RETURNING id"))).scalar_one())
            for room, source, claimed, by in (
                ("kitchen", "satellite", False, None),
                ("phone-android-own", "phone", False, None),
                ("phone-android-1a2b", "phone", True, chevy),
            ):
                await conn.execute(
                    text(
                        "INSERT INTO voice_profiles (person_id, embedding, model, room_id, source, "
                        "claimed_at, claim_room, claimed_by_person_id) VALUES (:p, :e, 'resemblyzer-v1', "
                        ":r, :s, CASE WHEN :c THEN NOW() END, CASE WHEN :c THEN :r END, :b)"
                    ),
                    {"p": kamron, "e": b"\0" * 1024, "r": room, "s": source, "c": claimed, "b": by},
                )
            return kamron, chevy

    kamron, chevy = asyncio.run(_seed())
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    with TestClient(web_app) as c:
        open_rows = c.get(f"/api/people/{kamron}/profiles").json()
        admin_rows = c.get(f"/api/people/{kamron}/profiles", headers=bearer(ADMIN_TOKEN)).json()
    by_room_open = sorted((r["source"], r["room_id"], r["claimed"]) for r in open_rows)
    # N4: an open caller never sees a phone's id, nor who claimed or where.
    assert by_room_open == [("phone", None, False), ("phone", None, True), ("satellite", "kitchen", False)]
    assert all(r["claim_room"] is None and r["claimed_by_person_id"] is None
               and r["claimed_by_name"] is None for r in open_rows)
    assert [r for r in open_rows if r["claimed"]][0]["claimed_at"] is not None
    claim = [r for r in admin_rows if r["claimed"]][0]
    assert (claim["room_id"], claim["claim_room"], claim["claimed_by_person_id"], claim["claimed_by_name"]) == (
        "phone-android-1a2b", "phone-android-1a2b", chevy, "Chevy",
    )
    assert sorted(r["room_id"] for r in admin_rows) == ["kitchen", "phone-android-1a2b", "phone-android-own"]


# ─── V029 ─────────────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_v029_applies_again_cleanly_and_leaves_old_rows_as_the_persons_own(db_session) -> None:
    from domovoi.db.session import engine

    sql = (
        Path(phone_voice.__file__).parent / "db" / "migrations" / "V029__voice_sample_claims.sql"
    ).read_text(encoding="utf-8")
    statements = [
        st for st in sql.split(";")
        if any(ln.strip() and not ln.strip().startswith("--") for ln in st.splitlines())
    ]
    assert len(statements) == 5
    kamron = await _person("Kamron", satellite=KAMRON_ROOM)
    for _ in range(2):
        async with engine.begin() as conn:
            for st in statements:
                await conn.exec_driver_sql(st)
    [row] = await _rows(kamron)
    assert (row["claimed_at"], row["claim_room"], row["claimed_by_person_id"], row["claim_of"]) == (
        None, None, None, None,
    )
