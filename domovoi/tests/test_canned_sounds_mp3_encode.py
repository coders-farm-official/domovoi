"""The satellite clips' MP3 encode (canned_sounds._wav_to_mp3): the system's
ffmpeg, run as its own program, since lameenc (LGPL, carrying LAME) left on
2026-10-15. The argv and every failure path against a fake ffmpeg; the real
one, when this machine has it, for an encode a reader can measure.
DB-free."""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import threading
import wave

import pytest

from domovoi import canned_sounds as cs
from domovoi import media_tags


def wav(seconds: float = 1.0, rate: int = 22050, width: int = 2, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00\x10" * int(seconds * rate) * channels if width == 2 else b"\x80" * int(seconds * rate))
    return buf.getvalue()


class FakeRun:
    def __init__(self, returncode: int = 0, stdout: bytes = b"\xff\xfbMP3", raises: BaseException | None = None):
        self.returncode, self.stdout, self.raises = returncode, stdout, raises
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, b"boom on stderr")


@pytest.fixture
def fake(monkeypatch):
    run = FakeRun()
    monkeypatch.setattr(cs.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(cs.subprocess, "run", run)
    monkeypatch.setattr(cs, "_warned_no_ffmpeg", False)
    return run


def test_the_encode_is_ffmpeg_libmp3lame_at_the_old_settings(fake) -> None:
    data = wav()
    assert cs._wav_to_mp3(data) == b"\xff\xfbMP3"
    (cmd, kw), = fake.calls
    assert cmd[0] == "/usr/bin/ffmpeg"
    pairs = dict(zip(cmd[1:], cmd[2:]))
    i = cmd.index("-i")
    assert cmd[i - 2:i + 2] == ["-f", "wav", "-i", "pipe:0"]
    assert pairs["-c:a"] == "libmp3lame"
    assert pairs["-b:a"] == "128k" and pairs["-compression_level"] == "2"     # lameenc's 128 kbps, quality 2
    assert pairs["-id3v2_version"] == "0" and pairs["-write_xing"] == "0"     # the bare stream lameenc wrote
    assert cmd[-3:] == ["-f", "mp3", "pipe:1"]
    assert kw["input"] == data and kw["capture_output"] is True and kw["timeout"] > 0
    assert cs._wav_to_mp3(data, bitrate=64) and "64k" in fake.calls[-1][0]


@pytest.mark.parametrize("bad", [b"", b"RIFF junk", wav(seconds=0), wav(width=1)],
                         ids=["empty", "not-wav", "no-frames", "8-bit"])
def test_audio_that_is_not_a_16_bit_wav_is_not_sent(fake, bad) -> None:
    assert cs._wav_to_mp3(bad) is None
    assert fake.calls == []


def test_a_failing_ffmpeg_gives_none_and_says_why(fake, caplog) -> None:
    fake.returncode = 1
    with caplog.at_level(logging.WARNING, logger="domovoi.canned_sounds"):
        assert cs._wav_to_mp3(wav()) is None
    assert "exit 1" in caplog.text and "boom on stderr" in caplog.text


@pytest.mark.parametrize("raises", [subprocess.TimeoutExpired("ffmpeg", 60), OSError("exec format error")])
def test_a_hung_or_unrunnable_ffmpeg_gives_none(fake, raises) -> None:
    fake.raises = raises
    assert cs._wav_to_mp3(wav()) is None


def test_empty_output_gives_none(fake) -> None:
    fake.stdout = b""
    assert cs._wav_to_mp3(wav()) is None


def test_without_ffmpeg_no_clip_and_one_warning(monkeypatch, caplog) -> None:
    monkeypatch.setattr(cs.shutil, "which", lambda name: None)
    monkeypatch.setattr(cs, "_warned_no_ffmpeg", False)
    with caplog.at_level(logging.WARNING, logger="domovoi.canned_sounds"):
        assert cs._wav_to_mp3(wav()) is None
        assert cs._wav_to_mp3(wav()) is None
    assert caplog.text.count("ffmpeg not found") == 1


async def test_the_encode_runs_off_the_event_loop(monkeypatch) -> None:
    from domovoi.clients.tts import SynthResult

    seen: list[str] = []

    def encode(data: bytes) -> bytes:
        seen.append(threading.current_thread().name)
        return b"MP3"

    async def synth(text, engine, model_ref):
        return SynthResult(wav=wav(0.1), engine="piper", voice="en_US-ryan-high")

    monkeypatch.setattr(cs, "_wav_to_mp3", encode)
    monkeypatch.setattr(cs, "_synth_detailed", synth)
    assert await cs._synth_clip_mp3("hi", "piper", "en_US-ryan-high") is not None
    assert seen and seen[0] != threading.main_thread().name


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg on this machine's PATH")
def test_a_real_encode_is_an_mp3_of_the_same_length(tmp_path) -> None:
    out = cs._wav_to_mp3(wav(seconds=2.0))
    assert out is not None and out[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")   # a frame, no ID3 tag
    p = tmp_path / "clip.mp3"
    p.write_bytes(out)
    f = media_tags.read(p)
    assert f.kind == "mp3" and f.tags is None
    assert f.duration == pytest.approx(2.0, abs=0.15)
