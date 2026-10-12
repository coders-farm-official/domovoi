"""The chat mic's core endpoint — ``POST /v1/phone/voice`` (domovoi/phone_voice.py).

A recording from the app's hold-to-talk view is answered like a satellite
turn (speech-to-text, voice identification, the router, text-to-speech)
and both turns land in the chat thread. Speech-to-text and voice
identification are faked at their module seams; the router, the handlers,
the stub TTS and the database are real.

What is pinned here:

* a turn writes exactly two rows (``source = 'voice'``, the speaker on the
  user row), titles the thread, logs under ``phone-<id>`` and answers with
  the transcript, the reply, WAV audio and the speaker;
* a blank or too-short recording writes nothing;
* the routing session is kept per phone and thread, so a "yes" on the
  next press resumes the question the last one parked;
* a phone is its own device: it never reaches a room's music player,
  timers it sets are house-wide, and a satellite's settings are refused;
* a leading wake phrase ("Hey Domovoit, …") is taken off before routing,
  so "play suicide boys" reaches the music fast path as on a satellite,
  and the thread keeps what was said;
* the gate: device tier, closed before setup (as the drop-in socket).
"""

from __future__ import annotations

import io
import json
import os
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth, phone_voice
from domovoi.clients import mpd as mpd_mod
from domovoi.clients.ollama import OllamaStubClient
from domovoi.db.session import SessionLocal
from domovoi.handlers.base import SATELLITE_SETTING_TEXT
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import HEADER, bearer, install_fake_db
from domovoi.tests.conftest import requires_db

PHONE = "android-test1"
ROOM = "phone-android-test1"


def _pcm(seconds: float = 1.0) -> bytes:
    """A quiet 220 Hz tone: enough samples to count as speech-length."""
    n = int(16_000 * seconds)
    t = np.arange(n) / 16_000
    return (np.sin(2 * np.pi * 220 * t) * 2000).astype("<i2").tobytes()


def _wav(pcm: bytes, rate: int = 16_000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class _Whisper:
    """Hands back the next scripted transcript for each recording."""

    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.calls = 0

    async def transcribe(self, pcm: bytes) -> str:
        self.calls += 1
        return self.texts.pop(0) if self.texts else ""


@pytest.fixture
def whisper(monkeypatch):
    fake = _Whisper()
    monkeypatch.setattr(phone_voice, "get_whisper_client", lambda: fake)
    return fake


@pytest.fixture
def heard_as(monkeypatch):
    """Who voice identification names: set ``heard_as.person`` (and
    ``.score``) before a turn; None is nobody."""
    state = SimpleNamespace(person=None, score=None, calls=0)

    async def _identify(pcm, *, embedding=None, room_id=None, samples="satellite"):
        state.calls += 1
        state.room = room_id
        state.samples = samples
        return SimpleNamespace(
            person_id=state.person,
            presence_tier="low",
            embedding=None,
            best_similarity=state.score,
            denylisted=False,
            runner_up_similarity=None,
            ambiguous=False,
        )

    import domovoi.voice_identifier as vi

    monkeypatch.setattr(vi, "identify", _identify)
    return state


@pytest.fixture
def open_gate():
    """The gate has its own tests below; the rest run past it."""
    core_app.dependency_overrides[admin_auth.require_device_after_setup] = lambda: None
    phone_voice.reset_for_tests()
    yield
    core_app.dependency_overrides.pop(admin_auth.require_device_after_setup, None)
    phone_voice.reset_for_tests()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=core_app), base_url="http://test")


async def _thread(title: str | None = None) -> int:
    async with SessionLocal() as s:
        tid = (
            await s.execute(
                text("INSERT INTO chat_threads (title) VALUES (:t) RETURNING id"), {"t": title}
            )
        ).scalar_one()
        await s.commit()
    return int(tid)


async def _person(name: str) -> int:
    async with SessionLocal() as s:
        pid = (
            await s.execute(
                text("INSERT INTO people (name) VALUES (:n) RETURNING id"), {"n": name}
            )
        ).scalar_one()
        await s.commit()
    return int(pid)


async def _rows(thread_id: int) -> list[dict[str, Any]]:
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT role, content, source, device_id, speaker_person_id, "
                    "speaker_confidence, stats FROM chat_messages WHERE thread_id = :t ORDER BY id"
                ),
                {"t": thread_id},
            )
        ).mappings().all()
    return [dict(r) for r in rows]


async def _turn(c: AsyncClient, thread_id: int, audio: bytes | None = None, **form: str):
    data = {"phone_id": PHONE, "thread_id": str(thread_id), **form}
    files = {"audio": ("turn.pcm", audio if audio is not None else _pcm(), "application/octet-stream")}
    return await c.post("/v1/phone/voice", data=data, files=files)


# ─── Inputs (no database) ─────────────────────────────────────────────────


def test_a_wav_of_the_right_shape_is_unwrapped_and_raw_pcm_passes_as_is() -> None:
    pcm = _pcm(0.5)
    assert phone_voice.pcm_from_upload(_wav(pcm)) == pcm
    assert phone_voice.pcm_from_upload(pcm) == pcm
    # An odd trailing byte is not half a sample to decode.
    assert phone_voice.pcm_from_upload(pcm + b"\x01") == pcm


@pytest.mark.parametrize(("rate", "channels"), [(48_000, 1), (16_000, 2)])
def test_a_wav_of_another_shape_is_refused(rate, channels) -> None:
    with pytest.raises(phone_voice.PhoneTurnRefused) as e:
        phone_voice.pcm_from_upload(_wav(_pcm(0.5), rate=rate, channels=channels))
    assert e.value.status == 415


def test_more_than_the_longest_turn_is_refused() -> None:
    with pytest.raises(phone_voice.PhoneTurnRefused) as e:
        phone_voice.pcm_from_upload(b"\0" * (phone_voice.MAX_CLIP_BYTES + 2))
    assert e.value.status == 413


@pytest.mark.parametrize("bad", ["", "x", "phone id with spaces", "../etc", "a" * 70])
def test_a_phone_id_has_the_device_id_shape(bad) -> None:
    with pytest.raises(phone_voice.PhoneTurnRefused):
        phone_voice.valid_phone_id(bad)


def test_the_room_a_phone_is_logged_under() -> None:
    assert phone_voice.phone_room("android-1a2b") == "phone-android-1a2b"
    assert phone_voice.phone_room("phone-x1y") == "phone-x1y"


def test_the_player_state_keeps_only_what_a_handler_reads() -> None:
    doc = '{"title": " Song ", "artist": "Band", "playing": true, "token": "secret", "kind": 3}'
    assert phone_voice.parse_player(doc) == {"title": "Song", "artist": "Band", "playing": True}
    assert phone_voice.parse_player("not json") == {}
    assert phone_voice.parse_player("[1, 2]") == {}
    assert phone_voice.parse_player(None) == {}


def test_a_phone_never_resolves_to_a_rooms_music_player(monkeypatch) -> None:
    """The first-room fallback (curl-era /v1/intent) must never hand a
    phone's turn the kitchen's player: it would replace that room's queue."""
    monkeypatch.setattr(mpd_mod, "_room_ports", {"kitchen": (6651, 8051)})
    assert mpd_mod._resolve_room("phone-android-1a2b") is None
    assert mpd_mod._resolve_room(None) == "kitchen"
    assert mpd_mod._resolve_room("garage") == "kitchen"
    assert mpd_mod.mpd_stream_url_for("phone-android-1a2b") is None
    with pytest.raises(mpd_mod.MPDNotProvisioned):
        mpd_mod.get_mpd_client_for("phone-android-1a2b")


def test_the_speaker_is_drawn_softer_below_the_learning_bar(monkeypatch) -> None:
    from domovoi.config import settings
    from domovoi.voice_identifier import speaker_is_confident

    monkeypatch.setattr(settings, "voice_profile_match_threshold", 0.75)
    monkeypatch.setattr(settings, "voice_profile_drift_near_threshold_margin", 0.05)
    assert speaker_is_confident(0.81)
    assert speaker_is_confident(0.80)
    assert not speaker_is_confident(0.79)
    assert not speaker_is_confident(None)


# ─── A whole turn ─────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_a_turn_is_answered_and_both_rows_land_in_the_thread(
    db_session, open_gate, whisper, heard_as,
) -> None:
    ana = await _person("Ana")
    heard_as.person, heard_as.score = ana, 0.9
    whisper.texts = ["What time is it?"]
    tid = await _thread()
    async with _client() as c:
        r = await _turn(c, tid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["heard"] is True
    assert body["transcript"] == "What time is it?"
    assert body["matched_handler"] == "clock"
    assert body["reply"]
    assert body["audio"]["mime"] == "audio/wav" and body["audio"]["b64"]
    assert body["speaker"] == {"person_id": ana, "name": "Ana", "confidence": 0.9, "confident": True}
    assert body["room_id"] == ROOM
    assert body["actions"] == [] and body["error"] is None
    # Voice identification ran without a room: a phone never feeds the
    # identifier's passive learning (see test_phone_turns_never_learn_...).
    assert heard_as.room is None
    # ...and against the phone set first (V026, test_phone_voice_profile).
    assert heard_as.samples == "phone"

    user, assistant = await _rows(tid)
    assert (user["role"], user["content"], user["source"]) == ("user", "What time is it?", "voice")
    assert user["device_id"] == PHONE
    assert user["speaker_person_id"] == ana
    assert user["speaker_confidence"] == pytest.approx(0.9)
    assert (assistant["role"], assistant["source"]) == ("assistant", "voice")
    assert assistant["content"] == body["reply"]
    assert assistant["stats"]["matched_handler"] == "clock"
    assert {"stt_ms", "route_ms", "tts_ms", "wall_ms", "audio_ms"} <= set(assistant["stats"])
    # The rows the answer carries are the stored rows.
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["speaker_name"] == "Ana"
    assert body["messages"][0]["speaker_confident"] is True

    async with SessionLocal() as s:
        title = (
            await s.execute(text("SELECT title FROM chat_threads WHERE id = :t"), {"t": tid})
        ).scalar_one()
        logged = (
            await s.execute(text("SELECT room_id, transcript FROM intents_log"))
        ).all()
    assert title == "What time is it?"
    assert [(row[0], row[1]) for row in logged] == [(ROOM, "What time is it?")]


@requires_db
@pytest.mark.asyncio
async def test_a_wav_upload_is_answered_like_raw_pcm(db_session, open_gate, whisper, heard_as) -> None:
    whisper.texts = ["what time is it"]
    tid = await _thread()
    async with _client() as c:
        r = await _turn(c, tid, audio=_wav(_pcm()))
    assert r.status_code == 200, r.text
    assert r.json()["matched_handler"] == "clock"
    # Nobody recognized: no speaker, and no name on the row.
    assert r.json()["speaker"] is None
    assert (await _rows(tid))[0]["speaker_person_id"] is None


@requires_db
@pytest.mark.asyncio
async def test_a_blank_recording_writes_nothing(db_session, open_gate, whisper, heard_as) -> None:
    whisper.texts = [" . "]
    tid = await _thread()
    async with _client() as c:
        r = await _turn(c, tid)
    assert r.status_code == 200
    assert r.json()["heard"] is False and r.json()["reason"] == "blank"
    assert await _rows(tid) == []
    assert heard_as.calls == 0


@requires_db
@pytest.mark.asyncio
async def test_a_tap_is_not_transcribed(db_session, open_gate, whisper, heard_as) -> None:
    tid = await _thread()
    async with _client() as c:
        r = await _turn(c, tid, audio=_pcm(0.1))
    assert r.status_code == 200
    assert r.json()["heard"] is False and r.json()["reason"] == "too_short"
    assert whisper.calls == 0
    assert await _rows(tid) == []


@requires_db
@pytest.mark.asyncio
async def test_an_unknown_thread_is_404_before_anything_is_heard(db_session, open_gate, whisper) -> None:
    async with _client() as c:
        r = await _turn(c, 424243)
    assert r.status_code == 404
    assert r.json()["code"] == "thread_not_found"
    assert whisper.calls == 0


@requires_db
@pytest.mark.asyncio
async def test_without_speech_recognition_the_turn_is_refused_plainly(
    db_session, open_gate, monkeypatch,
) -> None:
    from domovoi.clients.whisper import SttUnavailableError

    def _down():
        raise SttUnavailableError("model failed to load: no such file C:/models")

    monkeypatch.setattr(phone_voice, "get_whisper_client", _down)
    tid = await _thread()
    async with _client() as c:
        r = await _turn(c, tid)
    assert r.status_code == 503
    assert r.json()["code"] == "stt_unavailable"
    assert "C:/models" not in r.text
    assert await _rows(tid) == []


@requires_db
@pytest.mark.asyncio
async def test_a_failed_turn_is_still_recorded_with_a_plain_error(
    db_session, open_gate, whisper, heard_as, monkeypatch,
) -> None:
    import domovoi.router as router_mod

    async def _boom(intent, ctx, session):
        raise RuntimeError("handler exploded at http://localhost:11434")

    monkeypatch.setattr(router_mod, "route", _boom)
    whisper.texts = ["what time is it"]
    tid = await _thread()
    async with _client() as c:
        r = await _turn(c, tid)
    assert r.status_code == 200
    body = r.json()
    assert body["error"]["code"] == "turn_failed"
    assert "localhost" not in r.text
    user, assistant = await _rows(tid)
    assert user["content"] == "what time is it"
    assert assistant["stats"]["error_code"] == "turn_failed"


# ─── Follow-ups: one session per phone and thread ─────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_yes_on_the_next_press_resumes_the_parked_question(
    db_session, open_gate, whisper, heard_as,
) -> None:
    """The phone's push-to-talk is one-shot, so a question Domovoi asks is
    answered by the NEXT press — through the same routing session."""
    from domovoi.confirmations import request_confirmation

    whisper.texts = ["what time is it", "yes"]
    tid = await _thread()
    async with _client() as c:
        first = (await _turn(c, tid)).json()
        sid = first["session_id"]
        async with SessionLocal() as s:
            await request_confirmation(
                s, uuid.UUID(sid), kind="core.pending_memory_offer",
                handler="memory", data={"memory_id": 424242},
            )
            await s.commit()
        second = (await _turn(c, tid)).json()
    assert second["session_id"] == sid
    assert second["matched_path"] == "confirmation"
    assert second["matched_handler"] == "memory"


@requires_db
@pytest.mark.asyncio
async def test_each_thread_keeps_its_own_session(db_session, open_gate, whisper, heard_as) -> None:
    whisper.texts = ["what time is it"] * 3
    a, b = await _thread(), await _thread()
    async with _client() as c:
        s1 = (await _turn(c, a)).json()["session_id"]
        s2 = (await _turn(c, a)).json()["session_id"]
        s3 = (await _turn(c, b)).json()["session_id"]
    assert s1 == s2
    assert s3 != s1


# ─── The phone is its own device ──────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_wifi_is_a_satellite_setting(db_session, open_gate, whisper, heard_as) -> None:
    whisper.texts = ["fix the wifi", "how's the wifi"]
    tid = await _thread()
    async with _client() as c:
        fix = (await _turn(c, tid)).json()
        how = (await _turn(c, tid)).json()
    for body in (fix, how):
        assert body["reply"] == SATELLITE_SETTING_TEXT
        assert body["matched_handler"] == "wifi"
        assert body["actions"] == []


@requires_db
@pytest.mark.asyncio
async def test_switching_voices_is_a_satellite_setting_but_listing_them_is_not(
    db_session, open_gate, whisper, heard_as,
) -> None:
    whisper.texts = ["switch your voice to ryan", "what voices are there"]
    tid = await _thread()
    async with _client() as c:
        switch = (await _turn(c, tid)).json()
        listing = (await _turn(c, tid)).json()
    assert switch["reply"] == SATELLITE_SETTING_TEXT
    assert listing["matched_handler"] == "voice"
    assert listing["reply"] != SATELLITE_SETTING_TEXT


@requires_db
@pytest.mark.asyncio
async def test_a_timer_set_from_a_phone_is_house_wide(db_session, open_gate, whisper, heard_as) -> None:
    whisper.texts = ["set a timer for 5 minutes"]
    tid = await _thread()
    async with _client() as c:
        body = (await _turn(c, tid)).json()
    assert body["matched_handler"] == "timer", body
    async with SessionLocal() as s:
        rooms = (await s.execute(text("SELECT room_id FROM timers"))).scalars().all()
    assert rooms == [None]


@requires_db
@pytest.mark.asyncio
async def test_music_from_a_phone_never_touches_a_rooms_player(
    db_session, open_gate, whisper, heard_as, monkeypatch,
) -> None:
    """With a room's player in place, a phone's "pause" must not reach it."""
    calls: list[str] = []

    class _RoomPlayer(mpd_mod.MPDStubClient):
        async def pause(self) -> None:  # type: ignore[override]
            calls.append("pause")

    monkeypatch.setitem(mpd_mod._clients, "kitchen", _RoomPlayer())
    monkeypatch.setattr(mpd_mod, "_room_ports", {"kitchen": (6651, 8051)})
    whisper.texts = ["pause"]
    tid = await _thread()
    async with _client() as c:
        body = (await _turn(c, tid)).json()
    assert body["matched_handler"] == "music"
    assert calls == []


# ─── The wake phrase ──────────────────────────────────────────────────────
# People say "Hey Domovoi" into the phone's push-to-talk; a satellite's
# transcript never holds it. On 2026-10-12 a phone's "Hey Domovoit, play
# Suicide Boys." missed every fast path, went to the tool model with the
# name buried in it, and played a random song.


@pytest.mark.parametrize(
    ("said", "routed"),
    [
        # As speech-to-text spelled them on Kamron's phone, 2026-10-12.
        ("Hey Domovoit, play Suicide Boys.", "play Suicide Boys."),
        ("Hey Domavoy, what time is it?", "what time is it?"),
        ("Hey Domovoi, turn it down.", "turn it down."),
        ("OK Domovoi play jazz", "play jazz"),
        ("Hi Domovoy. Next song.", "Next song."),
        ("Hello Domo, what's the weather?", "what's the weather?"),
        ("hey domo play the album strange engines", "play the album strange engines"),
        ("hey doma voy, pause", "pause"),
        ("hey domo voi stop", "stop"),
        ("hey tomovoi, stop", "stop"),
        ("Um, hey Domovoi, stop", "stop"),
        ("Domovoi, what time is it?", "what time is it?"),
        ("Domovoit, stop.", "stop."),
        ("Hey Domovoi. Hey Domovoi, play jazz.", "play jazz."),
        ("Hey Damuvoi, what time is it?", "what time is it?"),
        # An ellipsis or a dash parts the name from the request too.
        ("Hey Domovoi\u2026 play jazz", "play jazz"),
        ("Hey Domovoi \u2014 play jazz", "play jazz"),
        ("Hey Domovoi \u2013 play jazz", "play jazz"),
        ("Domovoi\u2026 what time is it?", "what time is it?"),
    ],
)
def test_a_leading_wake_phrase_is_taken_off(said, routed) -> None:
    assert phone_voice.strip_wake_phrase(said) == routed


@pytest.mark.parametrize(
    "said",
    [
        "play Suicide Boys.",
        # A greeting without the name is the router's own filler to drop.
        "Hey, what time is it?",
        "Hey there, play jazz",
        "hey dominic, play jazz",
        # Without a greeting only a near-exact name counts.
        "Domino, play jazz",
        "Do more of that",
        "today is monday",
        # Nothing but the wake phrase: there is no request to route instead.
        "Hey Domovoi.",
        "Hey Domovoi",
        # The review's must-keep list (2026-10-12).
        "Hello, Dolly!",
        "Hello Dolly by Louis Armstrong",
        "Domino effect",
        "Dominic is home",
        "Hey, Jude",
        "Hi Tom, set a timer",
        # Words of their own, not a short name heard approximately.
        "OK, dome light on",
        "Hey, demo mode on",
        # A name with something glued to it is part of the request.
        "Hey Domovoi's playlist",
        "Domovoi's playlist, please",
        # Without a greeting, only a name punctuation parts from the rest.
        "Domovoi play jazz",
        "Domo arigato",
    ],
)
def test_anything_else_is_routed_as_it_was_said(said) -> None:
    assert phone_voice.strip_wake_phrase(said) == said


def test_the_households_own_name_for_the_assistant_counts_too(monkeypatch) -> None:
    from domovoi.config import settings

    monkeypatch.setattr(settings, "bot_name", "Jarvis")
    assert phone_voice.strip_wake_phrase("Hey Jarvis, play jazz.") == "play jazz."
    assert phone_voice.strip_wake_phrase("Hey Domovoi, play jazz.") == "play jazz."
    # Said alone, the household's name goes only when punctuation follows:
    # "Max out the volume" is a request.
    monkeypatch.setattr(settings, "bot_name", "Max")
    assert phone_voice.strip_wake_phrase("Max, turn it up") == "turn it up"
    assert phone_voice.strip_wake_phrase("Hey Max turn it up") == "turn it up"
    assert phone_voice.strip_wake_phrase("Max out the volume") == "Max out the volume"
    assert phone_voice.strip_wake_phrase("Hey Maxine, hi") == "Hey Maxine, hi"


class _ToolModelThatLosesTheName(OllamaStubClient):
    """A tool model that answers "Hey Domovoit, play Suicide Boys." with
    music/play and no name — what the live turn's random song looks like.
    (The stub's own tool model answers nothing, which would send the turn
    to the Q&A model instead.) Records what it was asked."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def route(self, transcript: str, tool_schemas: list[dict]) -> dict:  # type: ignore[override]
        self.calls.append(transcript)
        return {"handler": "music", "args": {"action": "play"}}


@pytest.fixture
def resolver(monkeypatch):
    """The spoken-name resolver on, its process-wide index rebuilt from
    the library each test seeds."""
    from domovoi.config import settings
    from domovoi.handlers.shared import library_match

    monkeypatch.setattr(settings, "music_match_enabled", True)
    library_match.reset_for_tests()
    yield
    library_match.reset_for_tests()


async def _seed_library(tracks: list[dict[str, Any]]) -> None:
    async with SessionLocal() as s:
        for t in tracks:
            await s.execute(
                text(
                    "INSERT INTO library_tracks (id, file_path, title, artist, album) "
                    "VALUES (:id, :fp, :title, :artist, :album)"
                ),
                {"id": t["id"], "fp": t["file_path"], "title": t["title"],
                 "artist": t["artist"], "album": t["album"]},
            )
        await s.commit()


async def _hey_domovoi_play_suicide_boys(
    tracks: list[dict[str, Any]], whisper, monkeypatch,
) -> tuple[dict[str, Any], _ToolModelThatLosesTheName, int]:
    import domovoi.router as router_mod

    tool = _ToolModelThatLosesTheName()
    monkeypatch.setattr(router_mod, "get_ollama_client", lambda: tool)
    await _seed_library(tracks)
    whisper.texts = ["Hey Domovoit, play Suicide Boys."]
    tid = await _thread()
    async with _client() as c:
        r = await _turn(c, tid)
    assert r.status_code == 200, r.text
    return r.json(), tool, tid


def _played_ids(body: dict[str, Any]) -> set[int]:
    plays = [a for a in body["actions"] if a["type"] == "play"]
    assert len(plays) == 1, body
    return {int(item["id"]) for item in plays[0]["items"]}


@requires_db
@pytest.mark.asyncio
async def test_hey_domovoi_play_suicide_boys_plays_suicide_boys_on_the_phone(
    db_session, open_gate, whisper, heard_as, resolver, monkeypatch,
) -> None:
    """The turn takes the music fast path a satellite's "play suicide boys"
    takes — the spoken-name resolver, $uicideboy$ shuffled — and the tool
    model is never asked."""
    fixture = Path(__file__).parent / "fixtures" / "spoken_match_library.json"
    tracks = json.loads(fixture.read_text(encoding="utf-8"))["tracks"]
    body, tool, tid = await _hey_domovoi_play_suicide_boys(tracks, whisper, monkeypatch)

    assert tool.calls == []
    assert (body["matched_handler"], body["matched_path"]) == ("music", "fast")
    assert body["reply"] == "Playing Suicide Boys, shuffled."
    assert _played_ids(body) == {1, 2, 3, 4}
    # The thread (and the answer) keep what was said; the intent is what
    # was routed.
    assert body["transcript"] == "Hey Domovoit, play Suicide Boys."
    user, _ = await _rows(tid)
    assert user["content"] == "Hey Domovoit, play Suicide Boys."
    async with SessionLocal() as s:
        logged = (await s.execute(text("SELECT transcript FROM intents_log"))).scalars().all()
    assert logged == ["play Suicide Boys."]


@requires_db
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("DOMOVOI_SPOKEN_GATE_DIR"),
    reason="household library NOT RUN (set DOMOVOI_SPOKEN_GATE_DIR)",
)
async def test_hey_domovoi_play_suicide_boys_on_the_household_library(
    db_session, open_gate, whisper, heard_as, resolver, monkeypatch,
) -> None:
    """The same turn on the library it failed on (the spoken gate's
    library.json, household data kept outside the repo): every
    $uicideboy$ row plays, nothing else."""
    rows = json.loads(
        (Path(os.environ["DOMOVOI_SPOKEN_GATE_DIR"]) / "library.json").read_text(encoding="utf-8")
    )
    rows = rows["items"] if isinstance(rows, dict) else rows
    expected = {int(r["id"]) for r in rows if "$uicideboy$" in (r.get("artist") or "").lower()}
    assert expected, "the household library has no $uicideboy$ rows"
    body, tool, _ = await _hey_domovoi_play_suicide_boys(rows, whisper, monkeypatch)

    assert tool.calls == []
    assert (body["matched_handler"], body["matched_path"]) == ("music", "fast")
    assert body["reply"] == "Playing Suicide Boys, shuffled."
    assert _played_ids(body) == expected


@requires_db
@pytest.mark.asyncio
async def test_only_the_wake_phrase_is_answered_as_it_was_said(
    db_session, open_gate, whisper, heard_as,
) -> None:
    whisper.texts = ["Hey Domovoi."]
    tid = await _thread()
    async with _client() as c:
        body = (await _turn(c, tid)).json()
    assert body["heard"] is True and body["transcript"] == "Hey Domovoi."
    async with SessionLocal() as s:
        logged = (await s.execute(text("SELECT transcript FROM intents_log"))).scalars().all()
    assert logged == ["Hey Domovoi."]


# ─── The gate ─────────────────────────────────────────────────────────────


def _gates() -> list[Any]:
    from domovoi.tests.route_walk import iter_route_contexts
    from domovoi.tests.test_core_route_tiers import _dependency_calls

    for rc in iter_route_contexts(core_app.routes):
        if getattr(rc, "path", None) == "/v1/phone/voice":
            return list(_dependency_calls(getattr(rc, "dependant", None)))
    raise AssertionError("POST /v1/phone/voice is not a route on the core app")


def test_the_route_wears_the_device_tier_without_the_pre_setup_grace() -> None:
    gates = _gates()
    assert admin_auth.require_device_after_setup in gates
    assert admin_auth.require_device in gates
    assert admin_auth.require_admin_mutation not in gates


def _form() -> dict[str, Any]:
    return {
        "data": {"phone_id": PHONE, "thread_id": "1"},
        "files": {"audio": ("turn.pcm", _pcm(0.1), "application/octet-stream")},
    }


@pytest.mark.asyncio
async def test_after_setup_a_caller_with_no_credential_is_refused(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={"admin"}, device_token="household-token")
    async with AsyncClient(
        transport=ASGITransport(app=core_app, raise_app_exceptions=False), base_url="http://test",
    ) as c:
        assert (await c.post("/v1/phone/voice", **_form())).status_code == 401
        r = await c.post("/v1/phone/voice", headers={HEADER: "not-the-token"}, **_form())
        assert r.status_code == 401
        # The household token and an admin Bearer get past the gate (what the
        # handler then makes of a test box is not a refusal).
        r = await c.post("/v1/phone/voice", headers={HEADER: "household-token"}, **_form())
        assert r.status_code not in (401, 403, 501), r.text
        r = await c.post("/v1/phone/voice", headers=bearer("admin"), **_form())
        assert r.status_code not in (401, 403, 501), r.text


@pytest.mark.asyncio
async def test_before_setup_it_is_closed_whatever_is_presented(monkeypatch) -> None:
    """The drop-in socket's rule (CORE-14): no pre-setup LAN grace for a
    recording of someone in the house."""
    install_fake_db(monkeypatch, admin=False, device_token="household-token")
    async with AsyncClient(
        transport=ASGITransport(app=core_app, raise_app_exceptions=False), base_url="http://test",
    ) as c:
        for headers in ({}, {HEADER: "household-token"}):
            r = await c.post("/v1/phone/voice", headers=headers, **_form())
            assert r.status_code == 501, r.text



# ─── The web hop and the thread read back ─────────────────────────────────


@pytest.fixture
def web_to_core(monkeypatch, open_gate):
    """The web backend's voice route, its hop to the core answered by the
    real core app in-process."""
    import web.backend.api.chat as chat_api

    monkeypatch.setattr(chat_api, "domovoi_url", lambda: "http://test")
    monkeypatch.setattr(
        chat_api, "_core_client",
        lambda: AsyncClient(transport=ASGITransport(app=core_app), base_url="http://test"),
    )
    return chat_api


def _web() -> AsyncClient:
    from web.backend.main import app as web_app

    return AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test",
        headers={"X-Requested-With": "domovoi-tests"},
    )


@requires_db
@pytest.mark.asyncio
async def test_the_app_reaches_the_core_through_its_own_server(
    db_session, web_to_core, whisper, heard_as,
) -> None:
    ana = await _person("Ana")
    heard_as.person, heard_as.score = ana, 0.77
    whisper.texts = ["what time is it"]
    tid = await _thread()
    async with _web() as web:
        r = await web.post(
            f"/api/chat/threads/{tid}/voice",
            data={"phone_id": PHONE, "player": '{"title": "Song", "playing": true}'},
            files={"audio": ("turn.wav", _wav(_pcm()), "audio/wav")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["matched_handler"] == "clock"
        # A match below the learning bar: named, but drawn softer.
        assert body["speaker"]["confident"] is False

        listed = (await web.get(f"/api/chat/threads/{tid}/messages")).json()["messages"]
    user, assistant = listed
    assert user["source"] == "voice" and assistant["source"] == "voice"
    assert user["speaker_person_id"] == ana and user["speaker_name"] == "Ana"
    assert user["speaker_confidence"] == pytest.approx(0.77)
    assert user["speaker_confident"] is False
    assert assistant["speaker_person_id"] is None and assistant["speaker_confident"] is False


@requires_db
@pytest.mark.asyncio
async def test_typed_rows_read_back_as_typed(db_session, monkeypatch) -> None:
    import web.backend.api.chat as chat_api

    async def _stream(messages, model, **kw):
        yield "Hi"

    async def _tags(*a, **kw):
        return []

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    monkeypatch.setattr(chat_api.ollama_client, "tags_or_none", _tags)
    tid = await _thread()
    async with _web() as web:
        r = await web.post(f"/api/chat/threads/{tid}/messages", json={"content": "hello"})
        assert r.status_code == 200
        assert '"source": "typed"' in r.text  # the persisted reply in the done event
        listed = (await web.get(f"/api/chat/threads/{tid}/messages")).json()["messages"]
    assert [m["source"] for m in listed] == ["typed", "typed"]
    assert all(m["speaker_person_id"] is None and m["speaker_name"] is None for m in listed)


@pytest.mark.asyncio
async def test_a_core_that_is_down_is_a_plain_502(monkeypatch) -> None:
    import httpx

    import web.backend.api.chat as chat_api

    class _Down:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            raise httpx.ConnectError("connection refused to 127.0.0.1:6370")

    monkeypatch.setattr(chat_api, "_core_client", lambda: _Down())
    install_fake_db(monkeypatch, admin=False)
    async with _web() as web:
        r = await web.post(
            "/api/chat/threads/1/voice",
            data={"phone_id": PHONE},
            files={"audio": ("turn.pcm", _pcm(0.5), "application/octet-stream")},
        )
    assert r.status_code == 502
    assert r.json()["code"] == "core_unreachable"
    assert "6370" not in r.text


@pytest.mark.asyncio
async def test_the_web_hop_refuses_more_than_the_longest_turn(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=False)
    async with _web() as web:
        r = await web.post(
            "/api/chat/threads/1/voice",
            data={"phone_id": PHONE},
            files={"audio": ("turn.pcm", b"\0" * (phone_voice.MAX_CLIP_BYTES + 8192),
                             "application/octet-stream")},
        )
    assert r.status_code == 413


# ─── V025 ─────────────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_v025_applies_again_cleanly_and_holds_its_rules(db_session) -> None:
    from pathlib import Path

    from sqlalchemy.exc import IntegrityError

    from domovoi.db.session import engine

    sql = (
        Path(phone_voice.__file__).parent / "db" / "migrations" / "V025__chat_voice.sql"
    ).read_text(encoding="utf-8")
    # One statement at a time, split on ";" as the file promises it can be.
    statements = [
        st for st in sql.split(";")
        if any(ln.strip() and not ln.strip().startswith("--") for ln in st.splitlines())
    ]
    assert len(statements) == 4
    for _ in range(2):
        async with engine.begin() as conn:
            for st in statements:
                await conn.exec_driver_sql(st)

    tid = await _thread()
    ana = await _person("Ana")
    async with SessionLocal() as s:
        # A typed row (every row before V025) reads 'typed' without saying so.
        await s.execute(
            text("INSERT INTO chat_messages (thread_id, role, content) VALUES (:t, 'user', 'hi')"),
            {"t": tid},
        )
        await s.execute(
            text(
                "INSERT INTO chat_messages (thread_id, role, content, source, "
                "speaker_person_id, speaker_confidence) VALUES (:t, 'user', 'yo', 'voice', :p, 0.8)"
            ),
            {"t": tid, "p": ana},
        )
        await s.commit()
    for bad in (
        "INSERT INTO chat_messages (thread_id, role, content, source) VALUES (:t, 'user', 'x', 'shouted')",
        "INSERT INTO chat_messages (thread_id, role, content, speaker_confidence) VALUES (:t, 'user', 'x', 1.5)",
    ):
        async with SessionLocal() as s:
            with pytest.raises(IntegrityError):
                await s.execute(text(bad), {"t": tid})
    # Forgetting a person keeps what they said and clears the marker.
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM people WHERE id = :p"), {"p": ana})
        await s.commit()
    rows = await _rows(tid)
    assert [r["source"] for r in rows] == ["typed", "voice"]
    assert rows[1]["content"] == "yo" and rows[1]["speaker_person_id"] is None


# ─── Media from a phone plays on the phone (device actions) ───────────────


async def _tracks(*rows: tuple[str, str, str]) -> list[int]:
    """library_tracks rows (title, artist, album); their ids, in order."""
    from domovoi.handlers.shared import library_match

    ids = []
    async with SessionLocal() as s:
        for i, (title, artist, album) in enumerate(rows):
            ids.append(int((
                await s.execute(
                    text(
                        "INSERT INTO library_tracks (file_path, title, artist, album) "
                        "VALUES (:f, :t, :a, :al) RETURNING id"
                    ),
                    {"f": f"/music/{i}-{title}.mp3", "t": title, "a": artist, "al": album},
                )
            ).scalar_one()))
        await s.commit()
    library_match.reset_for_tests()
    return ids


@pytest.fixture
def room_player(monkeypatch):
    """A room's music player that records every call: a phone turn must
    never reach it."""
    calls: list[str] = []

    class _RoomPlayer(mpd_mod.MPDStubClient):
        def __getattribute__(self, name):
            attr = super().__getattribute__(name)
            if callable(attr) and not name.startswith("_"):
                calls.append(name)
            return attr

    monkeypatch.setitem(mpd_mod._clients, "kitchen", _RoomPlayer())
    monkeypatch.setattr(mpd_mod, "_room_ports", {"kitchen": (6651, 8051)})
    return calls


@requires_db
@pytest.mark.asyncio
async def test_a_song_asked_for_plays_on_the_phone(
    db_session, open_gate, whisper, heard_as, room_player,
) -> None:
    ids = await _tracks(("Glass Harbor", "Velvet Kites", "Tide"), ("Other Song", "Someone", "X"))
    whisper.texts = ["play glass harbor"]
    tid = await _thread()
    async with _client() as c:
        body = (await _turn(c, tid)).json()
    assert body["matched_handler"] == "music", body
    assert body["reply"].startswith("Playing")
    (action,) = body["actions"]
    assert action["type"] == "play"
    assert action["items"][0] == {
        "kind": "track", "id": ids[0], "title": "Glass Harbor", "artist": "Velvet Kites", "album": "Tide",
    }
    assert room_player == []
    async with SessionLocal() as s:
        played = (await s.execute(text("SELECT room_id, library_track_id FROM media_plays"))).all()
    assert [tuple(r) for r in played] == [(ROOM, ids[0])]


@requires_db
@pytest.mark.asyncio
async def test_a_song_the_library_lacks_is_said_plainly(
    db_session, open_gate, whisper, heard_as, room_player,
) -> None:
    await _tracks(("Glass Harbor", "Velvet Kites", "Tide"))
    whisper.texts = ["play the moonlight serenade"]
    tid = await _thread()
    async with _client() as c:
        body = (await _turn(c, tid)).json()
    assert body["actions"] == []
    assert "couldn't find" in body["reply"] and "library" in body["reply"]
    assert room_player == []


@requires_db
@pytest.mark.asyncio
async def test_something_random_and_a_playlist_play_on_the_phone(
    db_session, open_gate, whisper, heard_as, room_player,
) -> None:
    ids = await _tracks(("One", "A", "X"), ("Two", "B", "X"), ("Three", "C", "X"))
    async with SessionLocal() as s:
        pid = (
            await s.execute(text("INSERT INTO playlists (name) VALUES ('road trip') RETURNING id"))
        ).scalar_one()
        for pos, track in enumerate(ids):
            await s.execute(
                text("INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (:p, :t, :pos)"),
                {"p": pid, "t": track, "pos": pos},
            )
        await s.commit()
    whisper.texts = ["play something", "play the road trip playlist"]
    tid = await _thread()
    async with _client() as c:
        anything = (await _turn(c, tid)).json()
        playlist = (await _turn(c, tid)).json()
    assert anything["actions"][0]["type"] == "play"
    assert len(anything["actions"][0]["items"]) == 1
    assert anything["actions"][0]["items"][0]["id"] in ids
    assert playlist["matched_handler"] == "playlist"
    assert playlist["reply"] == "Playing road trip."
    # The whole list, in order, for the phone's own queue.
    assert [i["id"] for i in playlist["actions"][0]["items"]] == ids
    assert room_player == []


@requires_db
@pytest.mark.asyncio
async def test_transport_volume_and_whats_playing_act_on_the_phone(
    db_session, open_gate, whisper, heard_as, room_player,
) -> None:
    playing = '{"title": "Glass Harbor", "artist": "Velvet Kites", "playing": true}'
    whisper.texts = ["pause", "next", "what's playing", "volume 6", "turn it up", "pause"]
    tid = await _thread()
    async with _client() as c:
        pause = (await _turn(c, tid, player=playing)).json()
        nxt = (await _turn(c, tid, player=playing)).json()
        what = (await _turn(c, tid, player=playing)).json()
        vol = (await _turn(c, tid, player=playing)).json()
        up = (await _turn(c, tid, player=playing)).json()
        idle = (await _turn(c, tid)).json()
    assert pause["actions"] == [{"type": "pause"}] and pause["reply"] == "Paused."
    assert nxt["actions"] == [{"type": "next"}]
    assert what["reply"] == "This is Glass Harbor by Velvet Kites." and what["actions"] == []
    assert vol["actions"] == [{"type": "volume", "change": "set", "percent": 60}]
    assert up["actions"] == [{"type": "volume", "change": "up"}]
    # Nothing playing on the phone: said so, nothing to do.
    assert idle["reply"] == "Nothing is playing on this phone." and idle["actions"] == []
    assert room_player == []


@requires_db
@pytest.mark.asyncio
async def test_an_audiobook_asked_for_plays_on_the_phone(
    db_session, open_gate, whisper, heard_as, room_player,
) -> None:
    """The book goes to the phone, which resumes it where its listener left
    off (the app reads that position itself); no room's player is asked."""
    async with SessionLocal() as s:
        book = (await s.execute(text(
            "INSERT INTO audiobooks (title, author, file_path, duration_sec) "
            "VALUES ('Cellar Tales', 'N', '/b/cellar.m4b', 3600) RETURNING id"
        ))).scalar_one()
        await s.commit()
    whisper.texts = ["play the audiobook cellar tales"]
    tid = await _thread()
    async with _client() as c:
        body = (await _turn(c, tid)).json()
    assert body["matched_handler"] == "spoken_audio", body
    assert body["reply"] == "Playing Cellar Tales."
    assert body["actions"] == [{
        "type": "play", "resume": True,
        "items": [{"kind": "book", "id": book, "title": "Cellar Tales", "duration_sec": 3600}],
    }]
    assert room_player == []


# ─── Review fixes (2026-10-12) ────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_forget_me_from_a_phone_still_writes_both_rows(
    db_session, open_gate, whisper, heard_as,
) -> None:
    """ "forget me" deletes the speaker inside the turn; the user row must
    then name nobody instead of tripping the foreign key and losing both
    rows (it used to come back as a 404 "thread not found")."""
    ana = await _person("Ana")
    heard_as.person, heard_as.score = ana, 0.9
    whisper.texts = ["forget me"]
    tid = await _thread()
    async with _client() as c:
        r = await _turn(c, tid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"] == "Okay, I've forgotten you, Ana."
    assert body["speaker"] is None
    user, assistant = await _rows(tid)
    assert (user["content"], user["speaker_person_id"], user["speaker_confidence"]) == ("forget me", None, None)
    assert assistant["content"] == body["reply"]
    async with SessionLocal() as s:
        assert (await s.execute(text("SELECT count(*) FROM people WHERE id = :p"), {"p": ana})).scalar_one() == 0


@requires_db
@pytest.mark.asyncio
async def test_a_speaker_deleted_by_an_admin_mid_turn_is_saved_as_nobody(
    db_session, open_gate, whisper, heard_as, monkeypatch,
) -> None:
    ana = await _person("Ana")
    heard_as.person, heard_as.score = ana, 0.9
    whisper.texts = ["what time is it"]
    real_synth = phone_voice._synthesize

    async def _admin_deletes_then_synth(response):
        # After routing, before the rows are written: the dashboard's delete.
        async with SessionLocal() as s:
            await s.execute(text("DELETE FROM people WHERE id = :p"), {"p": ana})
            await s.commit()
        return await real_synth(response)

    monkeypatch.setattr(phone_voice, "_synthesize", _admin_deletes_then_synth)
    tid = await _thread()
    async with _client() as c:
        r = await _turn(c, tid)
    assert r.status_code == 200, r.text
    assert r.json()["matched_handler"] == "clock" and r.json()["speaker"] is None
    user, _assistant = await _rows(tid)
    assert user["speaker_person_id"] is None and user["speaker_confidence"] is None


@requires_db
@pytest.mark.asyncio
async def test_phone_turns_never_learn_a_voice_sample(db_session, open_gate, whisper, monkeypatch) -> None:
    """Passive learning saves a person's clip as their sample for each
    "room" after a run of confident matches. A phone id is self-asserted
    (and new on every reinstall), so phones must not feed it: five phone
    ids, each matched confidently, leave the person's samples as they were."""
    from domovoi import voice_identifier
    from domovoi.clients.voice_embedder import StubVoiceEmbedder
    from domovoi.config import settings
    from domovoi.db.repositories import VoiceProfilesRepository

    monkeypatch.setattr(settings, "voice_profile_drift_reenroll_after", 1)
    voice_identifier._CONFIDENT_HITS.clear()
    clip = _pcm()
    ana = await _person("Ana")
    vec = await StubVoiceEmbedder().embed(clip)
    async with SessionLocal() as s:
        await VoiceProfilesRepository(s).add(
            person_id=ana, embedding=vec.astype("float32").tobytes(),
            model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG, room_id="kitchen", sample_seconds=1.0,
        )
        await s.commit()
    whisper.texts = ["what time is it"] * 5
    tid = await _thread()
    async with _client() as c:
        for i in range(5):
            r = await c.post(
                "/v1/phone/voice",
                data={"phone_id": f"android-spoof{i}", "thread_id": str(tid)},
                files={"audio": ("turn.pcm", clip, "application/octet-stream")},
            )
            assert r.json()["speaker"]["person_id"] == ana  # named, confidently
    async with SessionLocal() as s:
        samples = (
            await s.execute(text("SELECT count(*) FROM voice_profiles WHERE person_id = :p"), {"p": ana})
        ).scalar_one()
    assert samples == 1


_BAD_WAV = b"RIFF" + (30).to_bytes(4, "little") + b"WAVE" + b"LIST" + (5000).to_bytes(4, "little") + b"x" * 40


def test_a_wav_whose_chunk_runs_past_the_end_is_a_400() -> None:
    with pytest.raises(phone_voice.PhoneTurnRefused) as e:
        phone_voice.pcm_from_upload(_BAD_WAV)
    assert e.value.status == 400


@pytest.mark.asyncio
async def test_a_malformed_wav_is_refused_not_a_server_error(open_gate) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=core_app, raise_app_exceptions=False), base_url="http://test",
    ) as c:
        r = await c.post(
            "/v1/phone/voice", data={"phone_id": PHONE, "thread_id": "1"},
            files={"audio": ("turn.wav", _BAD_WAV, "audio/wav")},
        )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "bad_audio"


@pytest.mark.asyncio
@pytest.mark.parametrize("thread_id", [str(2**63), str(2**64 + 5), "0", "-3"])
async def test_a_thread_id_past_a_bigint_is_refused_on_both_hops(thread_id, open_gate, monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=False)
    files = {"audio": ("turn.pcm", _pcm(0.5), "application/octet-stream")}
    async with AsyncClient(
        transport=ASGITransport(app=core_app, raise_app_exceptions=False), base_url="http://test",
    ) as c:
        r = await c.post("/v1/phone/voice", data={"phone_id": PHONE, "thread_id": thread_id}, files=files)
    assert r.status_code == 422, r.text
    async with _web() as web:
        r = await web.post(f"/api/chat/threads/{thread_id}/voice", data={"phone_id": PHONE}, files=files)
    assert r.status_code == 422, r.text


@requires_db
@pytest.mark.asyncio
async def test_a_second_turn_from_a_busy_phone_is_refused(db_session, open_gate, whisper, heard_as) -> None:
    """One turn per phone at a time: a press while the last is still being
    answered gets 409 and is not heard; once it is done the phone is free."""
    whisper.texts = ["what time is it"]
    tid = await _thread()
    phone_voice._BUSY.add(ROOM)
    try:
        async with _client() as c:
            r = await _turn(c, tid)
        assert r.status_code == 409 and r.json()["code"] == "busy"
        assert whisper.calls == 0 and await _rows(tid) == []
    finally:
        phone_voice._BUSY.discard(ROOM)
    async with _client() as c:
        assert (await _turn(c, tid)).status_code == 200
    assert ROOM not in phone_voice._BUSY


@pytest.mark.asyncio
async def test_the_web_hop_forwards_the_callers_credential_and_never_its_own(monkeypatch) -> None:
    """The core must see who is calling: the caller's household token or
    Bearer, never the web process's own service credential."""
    import httpx

    import web.backend.api.chat as chat_api
    from domovoi import admin_auth as aa

    install_fake_db(monkeypatch, admin=True, sessions={"admin-bearer"}, device_token="caller-token")
    monkeypatch.setattr(aa, "read_device_token_file", lambda: "service-token")
    seen: list[httpx.Request] = []

    def _core(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"heard": False, "reason": "blank", "messages": []})

    monkeypatch.setattr(
        chat_api, "_core_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(_core), base_url="http://test"),
    )
    files = {"audio": ("turn.pcm", _pcm(0.5), "application/octet-stream")}
    async with _web() as web:
        r = await web.post("/api/chat/threads/5/voice", data={"phone_id": PHONE}, files=files,
                           headers={HEADER: "caller-token"})
        assert r.status_code == 200, r.text
        r = await web.post("/api/chat/threads/5/voice", data={"phone_id": PHONE}, files=files,
                           headers=bearer("admin-bearer"))
        assert r.status_code == 200, r.text
    by_token, by_bearer = seen
    assert by_token.headers.get(HEADER) == "caller-token"
    assert by_token.headers.get("authorization") is None
    assert by_bearer.headers.get("authorization") == "Bearer admin-bearer"
    assert by_bearer.headers.get(HEADER) is None
    for req in seen:
        assert "service-token" not in str(req.headers)


@pytest.mark.asyncio
async def test_the_web_caps_a_voice_body_before_parsing_it(monkeypatch) -> None:
    """An oversized body, declared or streamed, is refused by the transport
    guard before the multipart parser spools it; a chat picture has its own
    21 MB cap rather than the transport's 64."""
    big = b"\0" * (2 * 1024 * 1024)
    async with _web() as web:
        r = await web.post("/api/chat/threads/1/voice", data={"phone_id": PHONE},
                           files={"audio": ("a.pcm", big, "application/octet-stream")})
        assert r.status_code == 413

        async def _stream():
            yield (b'--B\r\nContent-Disposition: form-data; name="audio"; filename="a.pcm"\r\n'
                   b"Content-Type: application/octet-stream\r\n\r\n")
            for _ in range(40):
                yield b"\0" * 65536
            yield b"\r\n--B--\r\n"

        r = await web.post("/api/chat/threads/1/voice", content=_stream(),
                           headers={"Content-Type": "multipart/form-data; boundary=B"})
        assert r.status_code in (400, 413)

    from web.backend import middleware as mw

    assert mw.body_limit_for("/api/chat/uploads") == 21 * mw.MB
