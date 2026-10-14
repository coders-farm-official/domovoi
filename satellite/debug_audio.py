"""Test-only audio hooks: a microphone fed from WAV files, speakers that
write WAV files (or nothing).

Spare-device satellites on Windows (2026-10-13). An end-to-end test of a
satellite on someone's own computer must never open that person's real
microphone or play through their speakers. These hooks let the engine, and
the player processes it starts, run with stand-ins instead — and only when
an environment variable a person sets on purpose asks for them:

* ``DOMOVOI_DEBUG_MIC_DIR=<dir>`` — the capture stream is a
  :class:`FileMicStream`: it calls the engine's callback in real time with
  quiet noise, and plays each ``*.wav`` that appears in ``<dir>`` (16 kHz,
  mono, 16-bit — the satellite's own format) into it once, oldest name
  first, renaming it ``*.wav.done`` after. A test drops "hey jarvis, what
  time is it" in, and the engine hears it.
* ``DOMOVOI_DEBUG_AUDIO_SINK=null`` or ``=wav:<dir>`` — every output stream
  (the engine's speech, and the player processes' music, clips and chime)
  is a :class:`SinkStream` that keeps real-time pace and either discards
  what it is given or writes it to a new WAV file in ``<dir>``.

Neither variable is ever set by an installer, a unit or the core, nothing
the core sends can set one, and the engine logs a WARNING naming the hook
whenever one is in use. Without them every call here is the real
``sounddevice`` stream, unchanged. Stdlib only.
"""

from __future__ import annotations

import logging
import os
import random
import struct
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("satellite.debug_audio")

MIC_ENV = "DOMOVOI_DEBUG_MIC_DIR"
SINK_ENV = "DOMOVOI_DEBUG_AUDIO_SINK"
#: The quiet noise the fake mic delivers between files, in int16 counts
#: (about -60 dBFS): enough for the noise gate's calibration to measure.
NOISE_AMPLITUDE = 30


def mic_dir() -> Path | None:
    value = os.environ.get(MIC_ENV, "").strip()
    return Path(value) if value else None


def sink_spec() -> str | None:
    value = os.environ.get(SINK_ENV, "").strip()
    if not value:
        return None
    if value != "null" and not value.startswith("wav:"):
        raise ValueError(f"{SINK_ENV} must be null or wav:<dir>, not {value!r}")
    return value


# ─── the microphone ───────────────────────────────────────────────────────

class FileMicStream:
    """A stand-in for ``sounddevice.RawInputStream``: ``start``, ``stop``,
    ``close``, and the callback called every ``blocksize`` frames in real
    time. int16 only (the satellite's capture format on a spare PC)."""

    def __init__(self, folder: Path, *, samplerate: int, channels: int, dtype: str,
                 blocksize: int, callback: Callable[..., Any], **_ignored: Any) -> None:
        if dtype != "int16" or channels != 1:
            raise ValueError("the debug microphone delivers mono int16 only")
        self.folder = Path(folder)
        self.samplerate = int(samplerate)
        self.blocksize = int(blocksize)
        self.callback = callback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending = b""
        self.played: list[str] = []
        self.latency = 0.0

    def _next_file(self) -> bytes | None:
        try:
            names = sorted(p for p in self.folder.glob("*.wav") if p.is_file())
        except OSError:
            return None
        for p in names:
            try:
                with wave.open(str(p), "rb") as w:
                    if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, self.samplerate):
                        log.warning("debug mic: %s is not %d Hz mono 16-bit; skipped", p.name, self.samplerate)
                        data = None
                    else:
                        data = w.readframes(w.getnframes())
            except (OSError, wave.Error, EOFError) as e:
                log.warning("debug mic: %s unreadable (%s); skipped", p.name, e)
                data = None
            try:
                p.replace(p.with_name(p.name + ".done"))
            except OSError:
                pass
            if data:
                log.warning("debug mic: playing %s into the capture stream", p.name)
                self.played.append(p.name)
                return data
        return None

    def _block(self) -> bytes:
        need = self.blocksize * 2
        if not self._pending:
            self._pending = self._next_file() or b""
        if self._pending:
            chunk, self._pending = self._pending[:need], self._pending[need:]
            if len(chunk) < need:
                chunk += b"\x00" * (need - len(chunk))
            return chunk
        return struct.pack(f"<{self.blocksize}h",
                           *(random.randint(-NOISE_AMPLITUDE, NOISE_AMPLITUDE) for _ in range(self.blocksize)))

    def _run(self) -> None:
        period = self.blocksize / self.samplerate
        due = time.monotonic()
        while not self._stop.is_set():
            data = self._block()
            try:
                self.callback(data, self.blocksize, None, None)
            except Exception:  # noqa: BLE001 — the engine's callback must not kill the stream
                log.exception("debug mic: the capture callback failed")
            due += period
            delay = due - time.monotonic()
            if delay > 0:
                self._stop.wait(delay)
            elif delay < -1.0:
                due = time.monotonic()      # fell far behind: don't burst

    def start(self) -> None:
        log.warning("DEBUG MICROPHONE in use (%s=%s): no real microphone is open", MIC_ENV, self.folder)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="debug-mic", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(2.0)

    def close(self) -> None:
        self.stop()


# ─── the speakers ─────────────────────────────────────────────────────────

class SinkStream:
    """A stand-in for ``sounddevice.RawOutputStream``: ``start``, ``write``
    (blocking at real-time pace, like a device), ``stop``, ``close``. With
    ``wav:<dir>`` each stream becomes one WAV file there, named
    ``<ms>-<pid>-<label>.wav``."""

    def __init__(self, spec: str, *, samplerate: int, channels: int = 1, dtype: str = "int16",
                 label: str = "out", **_ignored: Any) -> None:
        if dtype != "int16":
            raise ValueError("the debug sink takes int16 only")
        self.samplerate = int(samplerate)
        self.channels = int(channels)
        self.latency = 0.0
        self._wav: wave.Wave_write | None = None
        self.path: Path | None = None
        if spec.startswith("wav:"):
            folder = Path(spec[4:])
            folder.mkdir(parents=True, exist_ok=True)
            self.path = folder / f"{time.time_ns() // 1_000_000}-{os.getpid()}-{label}.wav"
            self._wav = wave.open(str(self.path), "wb")
            self._wav.setnchannels(self.channels)
            self._wav.setsampwidth(2)
            self._wav.setframerate(self.samplerate)
        self._due: float | None = None
        self.frames = 0

    def start(self) -> None:
        self._due = time.monotonic()

    def write(self, data: Any) -> bool:
        raw = bytes(data)
        frames = len(raw) // (2 * self.channels)
        if self._wav is not None:
            self._wav.writeframes(raw)
        self.frames += frames
        if self._due is None:
            self._due = time.monotonic()
        self._due += frames / self.samplerate
        delay = self._due - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        return False        # sounddevice's "underflowed" flag

    def stop(self) -> None:
        pass

    def close(self) -> None:
        if self._wav is not None:
            self._wav.close()
            self._wav = None
            log.info("debug sink: wrote %s (%.2f s)", self.path, self.frames / max(1, self.samplerate))


# ─── what the engine and the players call ─────────────────────────────────

_warned_sink = False


def input_stream(sd: Any, **kwargs: Any) -> Any:
    """``sd.RawInputStream(**kwargs)``, or the file-fed stand-in when
    :data:`MIC_ENV` is set."""
    folder = mic_dir()
    if folder is None:
        return sd.RawInputStream(**kwargs)
    return FileMicStream(folder, **kwargs)


def output_stream(sd: Any, *, label: str = "speech", **kwargs: Any) -> Any:
    """``sd.RawOutputStream(**kwargs)``, or the sink stand-in when
    :data:`SINK_ENV` is set."""
    global _warned_sink
    spec = sink_spec()
    if spec is None:
        return sd.RawOutputStream(**kwargs)
    if not _warned_sink:
        _warned_sink = True
        log.warning("DEBUG AUDIO SINK in use (%s=%s): nothing plays through the speakers", SINK_ENV, spec)
    return SinkStream(spec, label=label, **kwargs)
