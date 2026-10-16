"""Whisper without PyAV (2026-10-15): faster-whisper is handed float32
arrays, never a file, so PyAV (``av``, FFmpeg's libraries inside) isn't
installed any more; ``import_faster_whisper`` stands in a placeholder for
the ``import av`` faster-whisper still does when it loads.

DB-free. The comparisons with faster-whisper's own decoder and a real
model run where faster-whisper (and PyAV, for the old path) and a cached
model are installed, and skip saying so elsewhere.
"""

from __future__ import annotations

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from domovoi.clients import whisper as W

REPO = Path(__file__).resolve().parents[2]
HAVE_FW = importlib.util.find_spec("faster_whisper") is not None
HAVE_AV = importlib.util.find_spec("av") is not None


def pcm(seconds: float = 1.5, freq: float = 440.0) -> bytes:
    t = np.arange(int(seconds * 16000)) / 16000
    return (np.sin(2 * np.pi * freq * t) * 12000).astype(np.int16).tobytes()


def wav_bytes(data: bytes, rate: int = 16000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)
    return buf.getvalue()


# ─── the audio handed to faster-whisper ──────────────────────────────────


def test_pcm_becomes_samples_over_32768() -> None:
    data = np.array([0, 1, -1, 16384, -32768, 32767], dtype=np.int16).tobytes()
    got = W.pcm_to_float32(data)
    assert got.dtype == np.float32
    assert got.tolist() == [0.0, 1 / 32768, -1 / 32768, 0.5, -1.0, 32767 / 32768]


def test_a_16k_mono_wav_is_read_directly(monkeypatch) -> None:
    monkeypatch.setattr(W.shutil, "which", lambda name: None)       # no ffmpeg needed
    data = pcm()
    assert np.array_equal(W.wav_to_float32(wav_bytes(data)), W.pcm_to_float32(data))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not on PATH")
def test_another_wav_format_goes_through_ffmpeg() -> None:
    t = np.arange(44100) / 44100
    stereo44 = np.repeat((np.sin(2 * np.pi * 300 * t) * 12000).astype(np.int16), 2).tobytes()
    got = W.wav_to_float32(wav_bytes(stereo44, rate=44100, channels=2))
    assert got.dtype == np.float32 and abs(len(got) - 16000) <= 16
    assert abs(int(np.argmax(np.abs(np.fft.rfft(got[:16000])))) - 300) <= 2


def test_a_wav_ffmpeg_can_not_read_or_no_ffmpeg_is_an_error(monkeypatch) -> None:
    monkeypatch.setattr(W.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="ffmpeg"):
        W.wav_to_float32(wav_bytes(pcm(), rate=8000))


def test_the_model_gets_an_array_and_no_file_is_written(tmp_path, monkeypatch) -> None:
    seen = []

    class Model:
        def transcribe(self, audio, beam_size):
            seen.append((type(audio), getattr(audio, "dtype", None), len(audio), beam_size))
            return iter([type("S", (), {"text": " hello "})()]), None

    def no_temp_files(*a, **k):
        raise AssertionError("a temporary file was written")

    client = W.FasterWhisperClient.__new__(W.FasterWhisperClient)
    client._model = Model()
    monkeypatch.setattr("tempfile.NamedTemporaryFile", no_temp_files)
    assert client._transcribe_full_sync(pcm(2.0)) == "hello"
    assert client._transcribe_wav_sync(wav_bytes(pcm(1.0))) == "hello"
    assert seen == [(np.ndarray, np.float32, 32000, 5), (np.ndarray, np.float32, 16000, 5)]


# ─── importing faster-whisper without PyAV ───────────────────────────────


def test_the_placeholder_fails_loudly_but_answers_probes() -> None:
    av = W._no_pyav_module()
    assert getattr(av, "__file__", None) is None                    # sys.modules walkers are fine
    with pytest.raises(AttributeError, match="PyAV is not installed"):
        av.open("x.wav")


@pytest.mark.skipif(not HAVE_FW, reason="faster-whisper (real-clients) is not installed")
def test_faster_whisper_imports_with_pyav_unimportable() -> None:
    code = ("import sys\n"
            "sys.modules['av'] = None\n"
            "from domovoi.clients.whisper import import_faster_whisper\n"
            "fw = import_faster_whisper()\n"
            "import torch  # what ctranslate2's converters import next to it\n"
            "assert hasattr(fw, 'WhisperModel')\n"
            "print(getattr(sys.modules['av'], '__file__', 'placeholder'))\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=300,
                         env={**os.environ, "USE_STUBS": "true"})
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "placeholder"


@pytest.mark.skipif(not (HAVE_FW and HAVE_AV), reason="faster-whisper and PyAV (the old path) are not both installed")
def test_the_array_is_what_faster_whispers_own_decoder_made_of_the_wav(tmp_path) -> None:
    from faster_whisper import decode_audio

    data = pcm(2.3, 523.0) + np.random.RandomState(1).randint(-3000, 3000, 8000).astype(np.int16).tobytes()
    path = tmp_path / "capture.wav"
    path.write_bytes(wav_bytes(data))
    assert np.array_equal(decode_audio(str(path)), W.pcm_to_float32(data))


def _cached_model(name: str) -> bool:
    if not HAVE_FW:
        return False
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    return isinstance(try_to_load_from_cache(f"Systran/faster-whisper-{name}", "model.bin"), str)


@pytest.mark.skipif(not (HAVE_AV and _cached_model("tiny.en")), reason="PyAV or a cached tiny.en model is missing")
def test_a_real_transcription_is_unchanged(tmp_path) -> None:
    """A voice clip of the repo's own, transcribed the old way (a WAV
    file through PyAV) and the new way (the array): the same text and
    the same tokens."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg (to decode the clip) is not on PATH")
    clip = REPO / "domovoi" / "voice_clips" / "en_US-ljspeech-medium.ogg"
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"],
                          capture_output=True, check=True).stdout
    model = W.import_faster_whisper().WhisperModel("tiny.en", device="cpu", compute_type="int8", local_files_only=True)
    path = tmp_path / "clip.wav"
    path.write_bytes(wav_bytes(data))
    old = [(s.text, s.tokens) for s in model.transcribe(str(path), beam_size=5)[0]]
    new = [(s.text, s.tokens) for s in model.transcribe(W.pcm_to_float32(data), beam_size=5)[0]]
    assert old == new and "Linda" in " ".join(t for t, _ in new)
