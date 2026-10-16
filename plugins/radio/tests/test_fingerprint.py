"""The landmark-hash fingerprinter + matcher (hasher and matcher share
one module so the algorithm parameters can't drift — that coupling is
the thing under test here, end to end against the DB).

Skips cleanly when numpy/scipy (the core's ``fingerprint`` extra) or the
ffmpeg program aren't installed — the runtime degrades the same way.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
if shutil.which("ffmpeg") is None:
    pytest.skip("ffmpeg (which decodes the audio) is not on PATH", allow_module_level=True)

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


def test_a_16k_mono_wav_decodes_to_its_own_samples(tmp_path) -> None:
    """No resampling and no mixing: ffmpeg gives the 16-bit samples scaled
    by 1/32768, exactly as the old librosa decode did."""
    wav = _write_wav(tmp_path / "song.wav", 1.5, [440.0, 1200.0])
    with wave.open(str(wav)) as w:
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    got = fp._load_audio_to_mono_16k(wav)
    assert got.dtype == np.float32 and len(got) == len(raw)
    assert np.array_equal(got, raw.astype(np.float32) / 32768.0)


def test_other_rates_and_stereo_come_out_16k_mono(tmp_path) -> None:
    src = tmp_path / "stereo44.flac"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=500:duration=2:sample_rate=44100",
                    "-ac", "2", str(src)], check=True)
    got = fp._load_audio_to_mono_16k(src)
    assert abs(len(got) - 2 * _SR) <= 32
    peak_hz = np.argmax(np.abs(np.fft.rfft(got[:_SR])))      # one second: 1 Hz bins
    assert abs(peak_hz - 500) <= 2


def test_a_long_track_is_cut_at_five_minutes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(fp, "_MAX_DECODE_SEC", 1)
    wav = _write_wav(tmp_path / "song.wav", 3.0, [440.0])
    assert len(fp._load_audio_to_mono_16k(wav)) == _SR


async def test_unreadable_missing_and_no_ffmpeg(tmp_path, monkeypatch) -> None:
    """In the child: ffmpeg refusing a file is a verdict (_DecodeRefused,
    an empty result); a missing file or no ffmpeg is a failure to retry."""
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"not audio at all" * 100)
    with pytest.raises(fp._DecodeRefused):
        fp._load_audio_to_mono_16k(junk)
    with pytest.raises(FileNotFoundError):
        fp._load_audio_to_mono_16k(tmp_path / "missing.mp3")
    assert fp._child_main(["fp", str(junk)]) == fp._EXIT_LOAD
    assert await fp.fingerprint_file(junk) == []
    wav = _write_wav(tmp_path / "song.wav", 2.0, [440.0])
    monkeypatch.setattr(fp.shutil, "which", lambda name: None)
    with pytest.raises(fp._DepMissingError):
        fp._load_audio_to_mono_16k(wav)
    assert fp._child_main(["fp", str(wav)]) == fp._EXIT_DEPS     # tried again later


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


async def test_a_file_the_decoder_refuses_returns_empty(tmp_path) -> None:
    """There, but not audio: a verdict about the file (marked, not retried)."""
    junk = tmp_path / "not-audio.wav"
    junk.write_bytes(b"this is not a wav file at all" * 10)
    assert await fp.fingerprint_file(junk) == []


async def test_a_file_that_is_not_there_is_a_failure_not_a_verdict(tmp_path) -> None:
    """Review round 2, case L7: a media disk not mounted made every track's
    file missing, and each was marked for good, a track a minute. Missing
    is FingerprintFailed: tried again later, counted toward the pause."""
    with pytest.raises(fp.FingerprintFailed, match="isn't there"):
        await fp.fingerprint_file(tmp_path / "unmounted" / "missing.wav")


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
