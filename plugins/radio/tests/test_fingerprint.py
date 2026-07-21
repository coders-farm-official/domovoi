"""The landmark-hash fingerprinter + matcher (hasher and matcher share
one module so the algorithm parameters can't drift — that coupling is
the thing under test here, end to end against the DB).

Skips cleanly when the audio stack (numpy/scipy/librosa) isn't
installed — the runtime degrades the same way.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("librosa")

from domovoi_plugin_radio.clients import fingerprint as fp  # noqa: E402

_SR = 16_000


def _write_wav(path: Path, seconds: float, freqs: list[float]) -> Path:
    """A deterministic multi-tone + AM-warble test signal — busy enough
    in the spectrogram to produce peaks and landmark pairs."""
    n = int(seconds * _SR)
    frames = bytearray()
    for i in range(n):
        t = i / _SR
        sample = 0.0
        for k, f in enumerate(freqs):
            # Amplitude wobble at different rates per component keeps
            # the peak picker supplied with distinct local maxima.
            sample += math.sin(2 * math.pi * f * t) * (
                0.4 + 0.3 * math.sin(2 * math.pi * (0.7 + 0.31 * k) * t)
            )
        sample /= max(1, len(freqs))
        frames += struct.pack("<h", int(max(-1.0, min(1.0, sample)) * 32000))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_SR)
        w.writeframes(bytes(frames))
    return path


async def test_fingerprint_file_produces_hashes(tmp_path) -> None:
    wav = _write_wav(tmp_path / "song.wav", 6.0, [440.0, 1200.0, 2500.0])
    rows = await fp.fingerprint_file(wav)
    assert len(rows) > 20
    assert all(len(r.hash_bytes) == 8 for r in rows)
    # Second-granular binning stored as ms (the deliberate coarse bins).
    assert all(r.offset_ms % 1000 == 0 for r in rows)


async def test_fingerprint_is_deterministic(tmp_path) -> None:
    wav = _write_wav(tmp_path / "song.wav", 4.0, [523.0, 880.0])
    a = await fp.fingerprint_file(wav)
    b = await fp.fingerprint_file(wav)
    assert [(r.hash_bytes, r.offset_ms) for r in a] == [
        (r.hash_bytes, r.offset_ms) for r in b
    ]


async def test_silence_produces_no_hashes(tmp_path) -> None:
    wav = tmp_path / "silence.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_SR)
        w.writeframes(b"\x00\x00" * (_SR * 3))
    assert await fp.fingerprint_file(wav) == []


async def test_too_short_audio_rejected(tmp_path) -> None:
    wav = _write_wav(tmp_path / "blip.wav", 0.3, [440.0])
    assert await fp.fingerprint_file(wav) == []


async def test_unreadable_file_returns_empty(tmp_path) -> None:
    assert await fp.fingerprint_file(tmp_path / "missing.wav") == []


@requires_db
async def test_match_sample_roundtrip(tmp_path, db_session) -> None:
    """Fingerprint a 'library track', store the rows, then match a
    sample of the same audio → hit; different audio → None."""
    track_row = await db_session.execute(
        text(
            "INSERT INTO library_tracks (file_path, title, artist, source, "
            "added_via) VALUES ('C:/music/tone.mp3', 'Tone Song', 'Tester', "
            "'manual', 'manual') RETURNING id"
        )
    )
    track_id = int(track_row.scalar_one())

    library_wav = _write_wav(tmp_path / "library.wav", 8.0, [440.0, 1200.0, 2500.0])
    rows = await fp.fingerprint_file(library_wav)
    assert rows
    await db_session.execute(
        text(
            "INSERT INTO plugin_radio.track_fingerprints "
            "(library_track_id, hash, offset_ms) "
            "VALUES (:tid, :hash, :off) "
            "ON CONFLICT (library_track_id, hash, offset_ms) DO NOTHING"
        ),
        [
            {"tid": track_id, "hash": r.hash_bytes, "off": r.offset_ms}
            for r in rows
        ],
    )
    await db_session.commit()

    # Same signal → match with metadata resolved from the core table.
    sample_wav = _write_wav(tmp_path / "sample.wav", 5.0, [440.0, 1200.0, 2500.0])
    match = await fp.match_sample(db_session, sample_wav, min_confidence=5)
    assert match is not None
    assert match.library_track_id == track_id
    assert match.title == "Tone Song"
    assert match.artist == "Tester"
    identity = match.to_identity()
    assert identity.title == "Tone Song"

    # A very different signal → no match above the threshold.
    other_wav = _write_wav(tmp_path / "other.wav", 5.0, [313.0, 3170.0])
    other = await fp.match_sample(db_session, other_wav, min_confidence=5)
    assert other is None or other.library_track_id != track_id


@requires_db
async def test_match_sample_respects_threshold(tmp_path, db_session) -> None:
    wav = _write_wav(tmp_path / "s.wav", 4.0, [440.0, 990.0])
    # Empty fingerprint table + absurd threshold → always None.
    assert await fp.match_sample(db_session, wav, min_confidence=10_000) is None
