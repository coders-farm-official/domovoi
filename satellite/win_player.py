"""The Windows satellite's player: music, clips and the chime, one process each.

Spare-device satellites on Windows (2026-10-13). On a Pi and a Linux laptop
the engine starts ``mpg123`` and ``aplay`` as child processes and stops them
as processes (satellite/engine_platform.py ``AudioOut``). Windows has
neither, and a Windows build of mpg123 would mean shipping LGPL code, so
this module is the child process instead, with the same jobs and the same
contract (exit 0 when the sound ended, non-zero with a reason on stderr when
it could not play; stopped by terminating the process):

    python -m satellite.win_player stream <http-url> [--device D]
    python -m satellite.win_player mp3 <file> [--gain G] [--device D]
    python -m satellite.win_player wav <file> [--device D]

MP3 is decoded by miniaudio's dr_mp3 through ``pyminiaudio`` (both MIT;
dr_mp3 is public domain or MIT-0); the sound goes out through
``sounddevice`` (PortAudio, MIT) to the default speakers, or the device
``--device`` names (a number or part of a name, as ``--list-devices``
shows). A music stream's connection failures are reported in the words the
engine's retry logic looks for ("connection refused", "connection reset",
"no data at all from network resource"), exactly as mpg123 would. The
test-only ``DOMOVOI_DEBUG_AUDIO_SINK`` (satellite/debug_audio.py) replaces
the speakers with a WAV file or nothing.
"""

from __future__ import annotations

import argparse
import array
import collections
import sys
import threading
import urllib.error
import urllib.request
import wave
from typing import Any

from satellite import debug_audio

#: How much of a music stream is read ahead of the decoder, in bytes (about
#: 25 s at 320 kbit/s): what mpg123's ``-b 1024`` buffer did for Wi-Fi blips.
PREFETCH_BYTES = 1 << 20
#: The output device's buffer, in seconds (mpg123's ``--devbuffer 1.0``).
DEVICE_LATENCY_SEC = 1.0
CHUNK = 16384
STREAM_RATE = 44100
STREAM_CHANNELS = 2
CONNECT_TIMEOUT_SEC = 10.0


class PlayerError(Exception):
    """A sound that could not play; the message goes to stderr."""


def _device(value: str | None) -> Any:
    """``--device`` as PortAudio takes it: None (the default), a number, or
    part of a name — only a value devices.valid_windows_device accepts."""
    from satellite import devices

    if value is None or value in ("", "default"):
        return None
    if value.isdigit() and devices.valid_windows_device(int(value)):
        return int(value)
    if not devices.valid_windows_device(value):
        raise PlayerError(f"not a Windows audio device name: {value[:40]!r}")
    return value


def _open_output(device: Any, rate: int, channels: int, label: str) -> Any:
    if debug_audio.sink_spec() is None:
        import sounddevice as sd
    else:
        sd = None       # the test sink: PortAudio is never even loaded
    try:
        stream = debug_audio.output_stream(
            sd, label=label, samplerate=rate, channels=channels, dtype="int16",
            device=device, latency=DEVICE_LATENCY_SEC,
        )
        stream.start()
    except Exception as e:  # noqa: BLE001 — PortAudio errors are many
        raise PlayerError(f"could not open the output device ({e})") from e
    return stream


def _finish(stream: Any) -> None:
    try:
        stream.stop()        # waits until what was written has played
    finally:
        stream.close()


def scale(samples: array.array, gain: float) -> array.array:
    """int16 samples times ``gain``, hard-clipped — what ``mpg123 -f``
    does to the greeting clips so they match the voice's level."""
    if gain == 1.0:
        return samples
    return array.array("h", (max(-32768, min(32767, int(s * gain))) for s in samples))


# ─── a file: mp3 or wav ───────────────────────────────────────────────────

def decode_mp3(path: str, gain: float = 1.0) -> tuple[bytes, int, int]:
    """``(int16 PCM, rate, channels)`` of an MP3 file, scaled by ``gain``."""
    import miniaudio

    try:
        # dr_mp3 itself: the file's own rate and channels, as int16.
        decoded = miniaudio.mp3_read_file_s16(path)
    except (OSError, miniaudio.MiniaudioError) as e:
        raise PlayerError(f"cannot decode {path}: {e}") from e
    return scale(decoded.samples, gain).tobytes(), decoded.sample_rate, decoded.nchannels


def read_wav(path: str) -> tuple[bytes, int, int]:
    """``(int16 PCM, rate, channels)`` of a 16-bit WAV file."""
    try:
        with wave.open(path, "rb") as w:
            if w.getsampwidth() != 2:
                raise PlayerError(f"{path}: only 16-bit WAV is supported")
            return w.readframes(w.getnframes()), w.getframerate(), w.getnchannels()
    except (OSError, wave.Error, EOFError) as e:
        raise PlayerError(f"cannot read {path}: {e}") from e


def play_pcm(pcm: bytes, rate: int, channels: int, *, device: Any = None, label: str = "clip",
             stop: threading.Event | None = None) -> bool:
    """Play int16 PCM to the end, or until ``stop`` is set (then the rest
    is dropped at once). Returns whether it played to the end."""
    stream = _open_output(device, rate, channels, label)
    step = max(2 * channels, (rate // 20) * 2 * channels)       # 50 ms per write
    try:
        for i in range(0, len(pcm), step):
            if stop is not None and stop.is_set():
                abort = getattr(stream, "abort", None)
                if abort is not None:
                    abort()
                return False
            stream.write(pcm[i:i + step])
        return True
    finally:
        _finish(stream)


def play_mp3(path: str, *, gain: float = 1.0, device: Any = None) -> None:
    pcm, rate, channels = decode_mp3(path, gain)
    play_pcm(pcm, rate, channels, device=device, label="clip")


def play_wav(path: str, *, device: Any = None) -> None:
    pcm, rate, channels = read_wav(path)
    play_pcm(pcm, rate, channels, device=device, label="chime")


class InProcessPlay:
    """A wake acknowledgement played inside the engine, shaped like the
    ``subprocess.Popen`` the engine's ``_play_ack`` waits on (``poll``,
    ``wait``, ``terminate``, ``kill``, ``returncode``).

    Why: a new Python process costs a fifth of a second or more before its
    first sample, and the person is already talking after the wake word —
    the acknowledgement must be as quick as ``aplay`` is on a Pi. The engine
    already has the audio library loaded, so the chime and the greeting
    play here, on a thread, and stopping one is a flag, not a process
    kill. Music stays a separate process (``stream``)."""

    pid = None

    def __init__(self, kind: str, path: str, *, gain: float = 1.0, device: Any = None) -> None:
        self.args = [kind, path]
        self.returncode: int | None = None
        self._stop = threading.Event()
        self._done = threading.Event()
        self.error: str | None = None
        self._thread = threading.Thread(target=self._run, args=(kind, path, gain, device),
                                        name=f"ack-{kind}", daemon=True)
        self._thread.start()

    def _run(self, kind: str, path: str, gain: float, device: Any) -> None:
        try:
            pcm, rate, channels = decode_mp3(path, gain) if kind == "mp3" else read_wav(path)
            done = play_pcm(pcm, rate, channels, device=device, label=f"ack-{kind}", stop=self._stop)
            self.returncode = 0 if done else -9
        except PlayerError as e:
            self.error = str(e)
            self.returncode = 1
        except Exception as e:  # noqa: BLE001 — a player must never take the engine down
            self.error = f"{type(e).__name__}: {e}"
            self.returncode = 1
        finally:
            self._done.set()

    def poll(self) -> int | None:
        return self.returncode if self._done.is_set() else None

    def wait(self, timeout: float | None = None) -> int | None:
        import subprocess

        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode

    def terminate(self) -> None:
        self._stop.set()

    kill = terminate


# ─── a music stream ───────────────────────────────────────────────────────

class _Prefetch:
    """Reads the HTTP body on its own thread into a bounded buffer, so a
    network stall drains the buffer instead of the speakers. ``read`` blocks
    until there is data, or returns b"" at the end."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self._chunks: collections.deque[bytes] = collections.deque()
        self._size = 0
        self._cond = threading.Condition()
        self._eof = False
        self.error: BaseException | None = None
        self.received = 0
        threading.Thread(target=self._run, name="music-prefetch", daemon=True).start()

    def _run(self) -> None:
        try:
            while True:
                chunk = self._response.read(CHUNK)
                if not chunk:
                    break
                with self._cond:
                    while self._size >= PREFETCH_BYTES:
                        self._cond.wait(0.5)
                    self._chunks.append(chunk)
                    self._size += len(chunk)
                    self.received += len(chunk)
                    self._cond.notify_all()
        except BaseException as e:  # noqa: BLE001 — reported by the reader
            self.error = e
        finally:
            with self._cond:
                self._eof = True
                self._cond.notify_all()

    def read(self, n: int) -> bytes:
        with self._cond:
            while not self._chunks and not self._eof:
                self._cond.wait(0.5)
            if not self._chunks:
                return b""
            out = bytearray()
            while self._chunks and len(out) < n:
                chunk = self._chunks.popleft()
                take = chunk[: n - len(out)]
                out += take
                if len(take) < len(chunk):
                    self._chunks.appendleft(chunk[len(take):])
            self._size -= len(out)
            self._cond.notify_all()
            return bytes(out)


def _open_stream(url: str) -> Any:
    if not url.lower().startswith(("http://", "https://")):
        raise PlayerError(f"not an http(s) stream: {url[:80]}")
    try:
        return urllib.request.urlopen(url, timeout=CONNECT_TIMEOUT_SEC)
    except urllib.error.HTTPError as e:
        raise PlayerError(f"HTTP {e.code} from {url}") from e
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, ConnectionRefusedError):
            raise PlayerError(f"connection refused ({url})") from e
        if isinstance(reason, ConnectionResetError):
            raise PlayerError(f"connection reset ({url})") from e
        raise PlayerError(f"unable to establish connection to {url}: {reason}") from e
    except ConnectionResetError as e:
        raise PlayerError(f"connection reset ({url})") from e
    except OSError as e:
        raise PlayerError(f"unable to establish connection to {url}: {e}") from e


def play_stream(url: str, *, device: Any = None) -> None:
    import miniaudio

    response = _open_stream(url)
    feed = _Prefetch(response)

    class Source(miniaudio.StreamableSource):
        def read(self, num_bytes: int) -> bytes:
            return feed.read(num_bytes)

    try:
        frames = miniaudio.stream_any(
            Source(), source_format=miniaudio.FileFormat.MP3,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=STREAM_CHANNELS, sample_rate=STREAM_RATE, frames_to_read=4096,
        )
    except miniaudio.MiniaudioError as e:
        if feed.received == 0:
            if isinstance(feed.error, ConnectionResetError):
                raise PlayerError(f"connection reset ({url})") from e
            raise PlayerError(f"no data at all from network resource {url}") from e
        raise PlayerError(f"cannot decode the stream: {e}") from e
    stream = _open_output(device, STREAM_RATE, STREAM_CHANNELS, "music")
    try:
        for samples in frames:
            stream.write(samples.tobytes())
    finally:
        _finish(stream)
    if feed.error is not None:
        raise PlayerError(f"the stream broke off: {feed.error}")


# ─── command line ─────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m satellite.win_player")
    sub = parser.add_subparsers(dest="kind", required=True)
    s = sub.add_parser("stream")
    s.add_argument("url")
    m = sub.add_parser("mp3")
    m.add_argument("path")
    m.add_argument("--gain", type=float, default=1.0)
    w = sub.add_parser("wav")
    w.add_argument("path")
    for p in (s, m, w):
        p.add_argument("--device", default="default")
    args = parser.parse_args(argv)
    try:
        device = _device(args.device)
        if args.kind == "stream":
            play_stream(args.url, device=device)
        elif args.kind == "mp3":
            play_mp3(args.path, gain=args.gain, device=device)
        else:
            play_wav(args.path, device=device)
    except PlayerError as e:
        print(f"error: {e}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
