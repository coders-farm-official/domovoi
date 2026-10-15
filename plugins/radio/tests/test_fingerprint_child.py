"""Fingerprinting off the event loop (incident 2026-10-14): the decode and
the hashing run in a child process with a hard time limit, a bounded
number at a time, and a file that runs out of time is recorded and never
tried again. The children here are stand-ins (a Python one-liner that
sleeps, spawns a grandchild or logs its start and end) wherever the test
is about the process handling rather than the hashes."""

from __future__ import annotations

import asyncio
import math
import struct
import sys
import time
import wave
from pathlib import Path

import pytest

from domovoi_plugin_radio.clients import fingerprint as fp

psutil = pytest.importorskip("psutil")

_SR = 16_000


def _write_wav(path: Path, seconds: float, freqs: list[float]) -> Path:
    n = int(seconds * _SR)
    frames = bytearray()
    for i in range(n):
        t = i / _SR
        sample = sum(
            math.sin(2 * math.pi * f * t) * (0.4 + 0.3 * math.sin(2 * math.pi * (0.7 + 0.31 * k) * t))
            for k, f in enumerate(freqs)
        ) / max(1, len(freqs))
        frames += struct.pack("<h", int(max(-1.0, min(1.0, sample)) * 32000))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_SR)
        w.writeframes(bytes(frames))
    return path


def _stand_in(monkeypatch, code: str) -> None:
    """Replace the child with ``python -c code`` (argv[1] is the file)."""
    monkeypatch.setattr(fp, "_child_command", lambda path: [sys.executable, "-c", code, str(path)])


def _gone(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


async def _wait_for_file(path: Path, timeout: float = 20.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return path.read_text().strip()
        await asyncio.sleep(0.05)
    raise AssertionError(f"{path} never written")


async def _wait_gone(pid: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _gone(pid):
            return True
        await asyncio.sleep(0.1)
    return _gone(pid)


async def test_the_work_happens_in_a_child_not_in_this_process(tmp_path, monkeypatch) -> None:
    """The decode and the hash functions are never called in the core's
    process, yet the hashes come back: the child did the work, and the
    loop kept ticking meanwhile."""
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    pytest.importorskip("librosa")

    def _not_here(*_a, **_k):
        raise AssertionError("decoded/hashed in the core process")

    monkeypatch.setattr(fp, "_load_audio_to_mono_16k", _not_here)
    monkeypatch.setattr(fp, "_hash_samples", _not_here)
    wav = _write_wav(tmp_path / "song.wav", 20.0, [440.0, 1200.0, 2500.0])

    lags: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        while not stop.is_set():
            t0 = time.perf_counter()
            await asyncio.sleep(0.02)
            lags.append(time.perf_counter() - t0 - 0.02)

    t = asyncio.create_task(ticker())
    rows = await fp.fingerprint_file(wav)
    stop.set()
    await t
    assert len(rows) > 20
    assert all(len(r.hash_bytes) == 8 and r.offset_ms % 1000 == 0 for r in rows)
    # In the process this was ~1.9 s of frozen loop for a 20 s track; a
    # child costs the loop nothing but its own bookkeeping.
    assert max(lags) < 0.5, max(lags)


async def test_a_hung_decode_is_killed_with_what_it_started(tmp_path, monkeypatch) -> None:
    """Past the limit the child AND its children are killed and the caller
    gets FingerprintTimeout, not a frozen worker."""
    pids = tmp_path / "pids.txt"
    _stand_in(monkeypatch, (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open(r'{pids}', 'w').write(f'{{__import__(\"os\").getpid()}} {{g.pid}}')\n"
        "time.sleep(120)\n"
    ))
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"not really audio")
    task = asyncio.create_task(fp.fingerprint_file(audio, timeout_sec=3.0))
    child_pid, grandchild_pid = map(int, (await _wait_for_file(pids)).split())
    t0 = time.monotonic()
    with pytest.raises(fp.FingerprintTimeout):
        await task
    assert time.monotonic() - t0 < 15
    assert await _wait_gone(child_pid), "the child outlived its timeout"
    assert await _wait_gone(grandchild_pid), "the child's own child outlived the timeout"


async def test_a_cancelled_caller_takes_the_child_down(tmp_path, monkeypatch) -> None:
    pid_file = tmp_path / "pid.txt"
    _stand_in(monkeypatch, (
        "import os, time\n"
        f"open(r'{pid_file}', 'w').write(str(os.getpid()))\n"
        "time.sleep(120)\n"
    ))
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"x")
    task = asyncio.create_task(fp.fingerprint_file(audio, timeout_sec=60.0))
    pid = int(await _wait_for_file(pid_file))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _wait_gone(pid), "a cancelled fingerprint left its child running"


async def test_children_are_bounded(tmp_path, monkeypatch) -> None:
    """However many files are asked for at once, at most MAX_CHILDREN
    children run at a time."""
    spans = tmp_path / "spans"
    spans.mkdir()
    # One file per child: appends from several processes to one file
    # lose lines on Windows.
    _stand_in(monkeypatch, (
        "import os, time\n"
        "t0 = time.time()\n"
        "time.sleep(0.8)\n"
        f"open(os.path.join(r'{spans}', str(os.getpid())), 'w').write(f'{{t0}} {{time.time()}}')\n"
    ))
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"x")
    results = await asyncio.gather(*(fp.fingerprint_file(audio, timeout_sec=60) for _ in range(5)))
    assert results == [[]] * 5   # no answer on stdout: treated as nothing usable
    events = sorted(
        ev
        for f in spans.iterdir()
        for ev in zip(map(float, f.read_text().split()), (1, -1))
    )
    running = peak = 0
    for _, d in events:
        running += d
        peak = max(peak, running)
    assert len(events) == 10
    assert peak <= fp.MAX_CHILDREN


async def test_a_child_that_fails_or_garbles_its_answer_is_an_empty_result(tmp_path, monkeypatch) -> None:
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"x")
    _stand_in(monkeypatch, "import sys; sys.stderr.write('boom'); sys.exit(4)")
    assert await fp.fingerprint_file(audio) == []
    _stand_in(monkeypatch, "import sys; sys.stdout.buffer.write(b'DOMOVOI-FP1\\n\\x05\\x00\\x00\\x00short')")
    assert await fp.fingerprint_file(audio) == []


def test_child_output_round_trips() -> None:
    rows = [fp.FingerprintRow(hash_bytes=bytes([i]) * 8, offset_ms=i * 1000) for i in range(3)]
    out = fp._MAGIC + fp._COUNT.pack(3) + b"".join(fp._ROW.pack(r.hash_bytes, r.offset_ms) for r in rows)
    assert fp._parse_child_output(out) == rows
    assert fp._parse_child_output(fp._MAGIC + fp._COUNT.pack(0)) == []
    assert fp._parse_child_output(b"nope") is None
    assert fp._parse_child_output(out[:-1]) is None


async def test_a_sample_that_runs_out_of_time_is_no_match(tmp_path, monkeypatch) -> None:
    _stand_in(monkeypatch, "import time; time.sleep(120)")
    wav = tmp_path / "sample.wav"
    wav.write_bytes(b"x")
    # No session is touched before the hashes exist.
    assert await fp.match_sample(None, wav, timeout_sec=1.0) is None  # type: ignore[arg-type]


def test_the_child_environment_carries_the_plugin_and_not_the_notify_socket(monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    monkeypatch.setenv("WATCHDOG_USEC", "90000000")
    env = fp._child_env()
    assert "NOTIFY_SOCKET" not in env and "WATCHDOG_USEC" not in env
    plugin_root = str(Path(fp.__file__).resolve().parents[2])
    assert env["PYTHONPATH"].split(__import__("os").pathsep)[0] == plugin_root
