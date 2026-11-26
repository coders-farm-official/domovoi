"""Voice ID 1.3.0: what is embedded (the speech alone, at one loudness),
which rooms know a voice, and the move to a new speaker model (V061: the
old samples go, their people are asked to introduce themselves again).

domovoi/voice_prep.py is pure numpy; the rest runs on the lane's database
with the stub embedder (domovoi/tests/test_voice_identifier.py has the
matching policy itself).
"""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from domovoi.db.repositories import VoiceProfilesRepository
from domovoi.tests.conftest import requires_db
from domovoi.voice_prep import TARGET_DBFS, prepare

SR = 16_000
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "speaker_encoder"


def _clip(name: str) -> np.ndarray:
    with wave.open(str(FIXTURES / name)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def _dbfs(x: np.ndarray) -> float:
    return 20 * np.log10(float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))))


# ─── what is embedded ─────────────────────────────────────────────────────


def test_the_silence_around_a_command_is_cut_and_the_level_set() -> None:
    """A satellite's recording: a little room before the command, the
    second of silence after it. Only the speech is kept, at -30 dBFS, and
    a quiet (far-off) recording comes out as loud as a near one."""
    speech = _clip("kristin_a.wav")
    rng = np.random.default_rng(0)
    for gain in (1.0, 0.05):                     # near, and 26 dB further off
        rec = np.concatenate([np.zeros(SR // 4), speech * gain, np.zeros(SR)])
        rec = rec + rng.standard_normal(rec.size).astype(np.float32) * 1e-4
        out = prepare(rec)
        assert 1.2 < out.speech_sec < 1.75        # the 1.25 s of quiet is gone
        assert _dbfs(out.audio) == pytest.approx(TARGET_DBFS, abs=0.5)
        assert out.snr_db > 25


def test_music_under_a_command_reads_as_noisy() -> None:
    """Music never lets the room go quiet, so the speech stands only a few
    dB above it: below the noisy bar (voice_profile_noisy_snr_db)."""
    speech = _clip("kristin_a.wav")
    t = np.arange(int(2.95 * SR)) / SR
    music = sum(np.sin(2 * np.pi * f * t) for f in (220, 277, 330, 440)).astype(np.float32)
    music *= 0.5 * float(np.sqrt(np.mean(speech ** 2))) / float(np.sqrt(np.mean(music ** 2)))
    rec = music.copy()
    rec[SR // 4: SR // 4 + speech.size] += speech
    assert prepare(rec).snr_db < 10.0


def test_speech_from_end_to_end_is_not_mistaken_for_noise() -> None:
    """A recording that is all speech (an excerpt cut from a sentence):
    the quietest moment between words is still the room's level."""
    for name in ("kristin_a.wav", "kristin_b.wav", "norman_a.wav"):
        assert prepare(_clip(name)).snr_db > 20, name


def test_silence_alone_is_returned_whole() -> None:
    out = prepare(np.zeros(SR, dtype=np.float32))
    assert out.speech_sec == pytest.approx(1.0)


# ─── which rooms know a voice ─────────────────────────────────────────────


@requires_db
def test_voice_rooms_lists_every_room_and_whether_it_knows_the_voice() -> None:
    from domovoi.db.session import engine
    from web.backend.main import app as web_app

    async def _seed() -> int:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE people RESTART IDENTITY CASCADE"))
            await conn.execute(text("DELETE FROM satellites"))
            await conn.execute(text("DELETE FROM rooms"))
            for room in ("dining", "garage", "office"):
                await conn.execute(text("INSERT INTO rooms (room_id, name) VALUES (:r, :r)"), {"r": room})
                await conn.execute(text("INSERT INTO satellites (satellite_id, room_id) VALUES (:r, :r)"),
                                   {"r": room})
            pid = int((await conn.execute(
                text("INSERT INTO people (name) VALUES ('Priya') RETURNING id"),
            )).scalar_one())
            for room, model, source in (
                ("office", VoiceProfilesRepository.EMBEDDING_MODEL_TAG, "satellite"),
                ("dining", "resemblyzer-v1", "satellite"),    # the old model: never counted
                ("attic", VoiceProfilesRepository.EMBEDDING_MODEL_TAG, "satellite"),  # no satellite now
                ("phone-android-1a2b", VoiceProfilesRepository.EMBEDDING_MODEL_TAG, "phone"),
            ):
                await conn.execute(
                    text(
                        "INSERT INTO voice_profiles (person_id, embedding, model, room_id, source) "
                        "VALUES (:p, :e, :m, :r, :s)"
                    ),
                    {"p": pid, "e": b"\0" * 1024, "m": model, "r": room, "s": source},
                )
            return pid

    pid = asyncio.run(_seed())
    with TestClient(web_app) as c:
        got = c.get(f"/api/people/{pid}/voice-rooms")
        assert got.status_code == 200, got.text
        assert got.json() == [
            {"room_id": "attic", "current": True},
            {"room_id": "dining", "current": False},
            {"room_id": "garage", "current": False},
            {"room_id": "office", "current": True},
        ]


# ─── the move to a new speaker model (V061) ──────────────────────────────

MIGRATION = Path(__file__).resolve().parents[1] / "db" / "migrations" / "V061__voice_id_eres2net.sql"


def _statements(path: Path) -> list[str]:
    body = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("--"))
    return [part for part in (p.strip() for p in body.split(";")) if part]


@requires_db
def test_v061_drops_old_samples_and_asks_their_people_to_introduce_themselves() -> None:
    """The migration, run again on the lane (it is idempotent): every sample
    and opt-out of another model goes, the people who had one are marked,
    and the People page says who has to say "my name is …" again until a
    sample of the new model arrives. Someone who never had a voice is not
    asked."""
    from domovoi.db.session import engine
    from web.backend.main import app as web_app

    tag = VoiceProfilesRepository.EMBEDDING_MODEL_TAG

    async def _seed() -> tuple[int, int, int]:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE people RESTART IDENTITY CASCADE"))
            await conn.execute(text("DELETE FROM voice_denylist"))
            ids = []
            for name in ("Priya", "Sam", "Noor"):
                ids.append(int((await conn.execute(
                    text("INSERT INTO people (name) VALUES (:n) RETURNING id"), {"n": name},
                )).scalar_one()))
            priya, sam, _noor = ids
            for pid, model in ((priya, "resemblyzer-v1"), (priya, "resemblyzer-v2"),
                               (sam, "resemblyzer-v1"), (sam, tag)):
                await conn.execute(
                    text("INSERT INTO voice_profiles (person_id, embedding, model, room_id) "
                         "VALUES (:p, :e, :m, 'office')"),
                    {"p": pid, "e": b"\0" * 1024, "m": model},
                )
            for model in ("resemblyzer-v1", tag):
                await conn.execute(
                    text("INSERT INTO voice_denylist (embedding, model) VALUES (:e, :m)"),
                    {"e": b"\0" * 768, "m": model},
                )
            for _ in range(2):                                    # twice: idempotent
                for statement in _statements(MIGRATION):
                    await conn.execute(text(statement))
            left = (await conn.execute(text("SELECT DISTINCT model FROM voice_profiles"))).scalars().all()
            assert left == [tag]
            deny = (await conn.execute(text("SELECT model FROM voice_denylist"))).scalars().all()
            assert deny == [tag]
            return tuple(ids)

    priya, sam, noor = asyncio.run(_seed())
    with TestClient(web_app) as c:
        people = {p["id"]: p for p in c.get("/api/people").json()}
        assert people[priya]["voice_relearn"] is True and people[priya]["voice_profile_count"] == 0
        # Sam had a sample of the new model too: recognized as before.
        assert people[sam]["voice_relearn"] is False and people[sam]["voice_profile_count"] == 1
        assert people[noor]["voice_relearn"] is False
        assert c.get(f"/api/people/{priya}").json()["voice_relearn"] is True
        status = c.get("/api/people/voice-id").json()
        assert status["relearn"] == 1
        assert c.get(f"/api/people/{priya}/profiles").json() == []

    async def _reintroduce() -> None:
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO voice_profiles (person_id, embedding, model, room_id) "
                     "VALUES (:p, :e, :m, 'office')"),
                {"p": priya, "e": b"\0" * 768, "m": tag},
            )

    asyncio.run(_reintroduce())
    with TestClient(web_app) as c:
        assert c.get(f"/api/people/{priya}").json()["voice_relearn"] is False
        assert c.get("/api/people/voice-id").json()["relearn"] == 0
