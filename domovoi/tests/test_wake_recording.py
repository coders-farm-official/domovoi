"""Wake-word clip-recording streaming branch (Feature 5).

When a :class:`~domovoi.streaming.StreamSession` is in recording mode
(``self.wake_recording`` set), an ``utterance_end`` must divert the
buffered PCM into a training clip WAV + bump the wake word's
``clip_count`` — and must NOT transcribe or route it (the Pi is feeding
training audio, not a command).

These drive a bare ``StreamSession`` directly (no TestClient WebSocket
dance) with a tiny fake socket, monkeypatch ``settings.wake_clips_dir`` to
a tmp dir, and assert: the clip lands as ``<slug>/clip_001.wav`` in the
correct 16 kHz mono int16 format, ``route`` is never called, and the
clip_count bumps in the DB.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from domovoi import streaming
from domovoi.db.repositories import WakeWordsRepository
from domovoi.streaming import StreamSession, WakeRecordingState
from domovoi.tests.conftest import requires_db


class _FakeWS:
    """Minimal WebSocket stand-in for a bare StreamSession.

    Records text frames the session sends (start/stop_wake_recording) and
    swallows bytes. No app — the recording branch never touches
    ``self.ws.app``, only ``self.ws.send_text``."""

    def __init__(self) -> None:
        self.sent_text: list[dict] = []
        self.sent_bytes: list[bytes] = []

    async def send_text(self, data: str) -> None:
        self.sent_text.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)


def _make_session() -> tuple[StreamSession, _FakeWS]:
    ws = _FakeWS()
    sess = StreamSession(ws, "kitchen")  # type: ignore[arg-type]
    return sess, ws


@requires_db
@pytest.mark.asyncio
async def test_utterance_end_in_recording_writes_clip_and_bumps_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seed a real wake word row to bump against.
    from domovoi.db.session import engine
    from sqlalchemy import text as sql_text

    async with engine.begin() as conn:
        await conn.execute(sql_text("DELETE FROM wake_words"))
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        wid = await WakeWordsRepository(s).create(
            name="Hey Domovoi", slug="hey_domovoi", phrase="hey domovoi"
        )

    clips_dir = tmp_path / "wake_clips"
    monkeypatch.setattr(streaming.settings, "wake_clips_dir", str(clips_dir))

    # Guard: route must NEVER be called in recording mode.
    route_called = False

    async def _boom_route(intent, ctx, session):
        nonlocal route_called
        route_called = True
        raise AssertionError("route() must not run for a wake_clip utterance")

    monkeypatch.setattr(streaming, "route", _boom_route)

    sess, _ws = _make_session()
    sess.wake_recording = WakeRecordingState(
        wake_word_id=wid, slug="hey_domovoi", clip_seconds=2.0, target_count=3
    )

    # One positive clip: start (trigger=wake_clip) → PCM → end. Feed a
    # realistic clip length (~0.75 s of int16) — the server drops anything
    # below a ~0.3 s floor as a near-empty fluke, so a few hundred bytes
    # would be (correctly) discarded.
    pcm = b"\x11\x22" * 6000  # 12000 bytes ≈ 0.75 s of int16 @ 16 kHz
    await sess._on_control({"type": "utterance_start", "trigger": "wake_clip"})
    await sess._on_audio(pcm)
    await sess._on_control({"type": "utterance_end"})

    assert route_called is False

    # The clip landed as <slug>/clip_001.wav in 16 kHz mono int16.
    clip = clips_dir / "hey_domovoi" / "clip_001.wav"
    assert clip.is_file(), f"expected clip at {clip}"
    with wave.open(str(clip), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16_000
        assert wf.readframes(wf.getnframes()) == pcm

    # clip_count bumped in the DB, and the session's local counter ticked.
    assert sess.wake_recording.clips_written == 1
    async with session_scope() as s:
        row = await WakeWordsRepository(s).get(wid)
    assert row["clip_count"] == 1


@requires_db
@pytest.mark.asyncio
async def test_consecutive_clips_increment_filename_and_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each captured clip writes clip_NNN.wav with an incrementing index
    and bumps clip_count again."""
    from domovoi.db.session import engine, session_scope
    from sqlalchemy import text as sql_text

    async with engine.begin() as conn:
        await conn.execute(sql_text("DELETE FROM wake_words"))
    async with session_scope() as s:
        wid = await WakeWordsRepository(s).create(
            name="Athena", slug="athena", phrase="hey athena"
        )

    clips_dir = tmp_path / "wake_clips"
    monkeypatch.setattr(streaming.settings, "wake_clips_dir", str(clips_dir))
    monkeypatch.setattr(streaming, "route", _unused_route)

    sess, _ws = _make_session()
    sess.wake_recording = WakeRecordingState(
        wake_word_id=wid, slug="athena", clip_seconds=2.0, target_count=3
    )

    for i in range(3):
        await sess._on_control({"type": "utterance_start", "trigger": "wake_clip"})
        await sess._on_audio(b"\x00\x01" * 6000)  # ≈0.75 s, above the drop floor
        await sess._on_control({"type": "utterance_end"})

    for n in (1, 2, 3):
        assert (clips_dir / "athena" / f"clip_{n:03d}.wav").is_file()
    assert sess.wake_recording.clips_written == 3
    async with session_scope() as s:
        assert (await WakeWordsRepository(s).get(wid))["clip_count"] == 3


@pytest.mark.asyncio
async def test_start_and_stop_wake_recording_send_frames_and_set_state() -> None:
    """``start_wake_recording`` arms the state + sends the start frame;
    ``stop_wake_recording`` clears it + sends the stop frame. No DB needed."""
    sess, ws = _make_session()

    await sess.start_wake_recording(
        wake_word_id=7, slug="hey_domovoi", clip_seconds=2.0, target_count=30
    )
    assert sess.wake_recording is not None
    assert sess.wake_recording.wake_word_id == 7
    assert sess.wake_recording.slug == "hey_domovoi"
    assert ws.sent_text[-1] == {
        "type": "start_wake_recording",
        "wake_word_id": 7,
        "slug": "hey_domovoi",
        "clip_seconds": 2.0,
        "target_count": 30,
    }

    await sess.stop_wake_recording()
    assert sess.wake_recording is None
    assert ws.sent_text[-1] == {"type": "stop_wake_recording"}


@pytest.mark.asyncio
async def test_request_set_wake_word_sends_set_frame() -> None:
    """``request_set_wake_word`` raw-sends the slug push frame the Pi
    keys its sidecar + model load off of."""
    sess, ws = _make_session()
    await sess.request_set_wake_word(slug="hey_domovoi")
    assert ws.sent_text[-1] == {"type": "set_wake_word", "slug": "hey_domovoi"}


@requires_db
@pytest.mark.asyncio
async def test_near_empty_clip_is_dropped_not_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fluke utterance below the ~0.3 s floor is dropped — no WAV, no
    clip_count bump — so a junk positive can't inflate the count."""
    from domovoi.db.session import engine, session_scope
    from sqlalchemy import text as sql_text

    async with engine.begin() as conn:
        await conn.execute(sql_text("DELETE FROM wake_words"))
    async with session_scope() as s:
        wid = await WakeWordsRepository(s).create(
            name="Tiny", slug="tiny", phrase="hey tiny"
        )

    clips_dir = tmp_path / "wake_clips"
    monkeypatch.setattr(streaming.settings, "wake_clips_dir", str(clips_dir))
    monkeypatch.setattr(streaming, "route", _unused_route)

    sess, _ws = _make_session()
    sess.wake_recording = WakeRecordingState(
        wake_word_id=wid, slug="tiny", clip_seconds=2.0, target_count=3
    )
    await sess._on_control({"type": "utterance_start", "trigger": "wake_clip"})
    await sess._on_audio(b"\x00\x01" * 20)  # 40 bytes — far below the floor
    await sess._on_control({"type": "utterance_end"})

    slug_dir = clips_dir / "tiny"
    assert not slug_dir.exists() or not list(slug_dir.glob("*.wav"))
    assert sess.wake_recording.clips_written == 0
    async with session_scope() as s:
        assert (await WakeWordsRepository(s).get(wid))["clip_count"] == 0


@pytest.mark.asyncio
async def test_wake_clip_after_stop_is_never_routed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clip that STARTED as a wake_clip must never reach route() even if
    stop_wake_recording races in before utterance_end — the divert keys off
    the latched trigger, not the live wake_recording flag."""
    monkeypatch.setattr(streaming, "route", _unused_route)
    sess, _ws = _make_session()
    sess.wake_recording = WakeRecordingState(
        wake_word_id=1, slug="x", clip_seconds=2.0, target_count=3
    )
    await sess._on_control({"type": "utterance_start", "trigger": "wake_clip"})
    await sess._on_audio(b"\x00\x01" * 6000)
    sess.wake_recording = None  # stop arrives mid-utterance
    # Must complete without routing (route() is the raising _unused_route).
    await sess._on_control({"type": "utterance_end"})
    assert sess._response_task is None


async def _unused_route(intent, ctx, session):  # pragma: no cover - guard
    raise AssertionError("route() must not run for a wake_clip utterance")
