"""Noise cleanup before speech to text: an A/B experiment, OFF by default
(1.5.0, noisy rooms, design §5 stage 2 item 4; owner answer Q9).

What it touches: only the command audio a satellite, a room-mode phone or
the phone app's push-to-talk sends after the wake word, on its way into
Whisper (``StreamSession._whisper_call`` and ``phone_voice._transcribe``).
Never wake detection (that runs on the satellite), never voice ID (it
reads the raw capture), never anything stored.

Modes (``settings.stt_denoise``, env ``STT_DENOISE``):

* ``off`` (the default): the audio goes to Whisper exactly as captured.
* ``spectral``: a Wiener filter on a short-time spectrum, in numpy only.
  The noise is estimated per frequency from the capture itself (a low
  percentile over time, a cheap stand-in for minimum statistics); the gain
  never drops below :data:`GAIN_FLOOR_DB`, which keeps the "musical noise"
  that hurts recognition down. Nothing to install.
* ``rnnoise``: RNNoise (Xiph, BSD-3-Clause), the small recurrent network
  WebRTC-style apps use. The library comes from the ``pyrnnoise`` wheel
  (Apache-2.0), installed WITHOUT its declared dependencies
  (``pip install --no-deps pyrnnoise``: they include PyAV); only its
  bundled shared library is loaded here, through ctypes. Not installed by
  default: without it this mode logs once and passes the audio through.

Fail safe: whatever goes wrong (the library missing, odd input, an
exception in the filter), the raw audio goes to Whisper and the turn
carries on. Which mode helps, if any, is decided by the offline A/B
(``scripts/dev/denoise_ab.py``) together with the XVF3800 room test;
until then the default stays off.
"""

from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import logging
import os
import sys
import threading
import time
from typing import Any

import numpy as np

from domovoi import executors
from domovoi.config import STT_DENOISE_MODES, settings

log = logging.getLogger("domovoi.stt_denoise")

MODES: tuple[str, ...] = STT_DENOISE_MODES

SAMPLE_RATE = 16_000

# ── spectral (Wiener) ───────────────────────────────────────────────────────

N_FFT = 512          # 32 ms at 16 kHz
HOP = N_FFT // 2     # sqrt-Hann analysis + synthesis at 50 %: sums to one
#: Percentile of each bin's power over the capture taken as its noise. A
#: command is speech for a second or two of a capture that is mostly the
#: room, so a low percentile sits on the room, not on the voice.
NOISE_PERCENTILE = 20.0
#: How far the filter may pull a bin down. Deeper cleans more and garbles
#: more; Whisper is trained on noisy speech and minds garbling more.
GAIN_FLOOR_DB = -12.0
#: Decision-directed smoothing of the a-priori SNR (Ephraim-Malah).
DD_ALPHA = 0.96
#: Over-subtraction of the noise estimate.
OVER_SUB = 1.5


def _spectral(x: np.ndarray) -> np.ndarray:
    n = len(x)
    if n < N_FFT * 2:
        return x
    win = np.sqrt(np.hanning(N_FFT + 1)[:-1]).astype(np.float32)
    pad = (-(n - N_FFT) % HOP) + HOP
    xp = np.concatenate([np.zeros(HOP, np.float32), x, np.zeros(pad, np.float32)])
    frames = 1 + (len(xp) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(frames)[:, None]
    spec = np.fft.rfft(xp[idx] * win, axis=1)
    power = np.abs(spec) ** 2
    noise = np.percentile(power, NOISE_PERCENTILE, axis=0) * OVER_SUB + 1e-12
    floor = 10 ** (GAIN_FLOOR_DB / 20)
    gains = np.empty_like(power)
    # Decision-directed a-priori SNR: the previous frame's cleaned power
    # (gain² × its posterior SNR) blended with this frame's evidence.
    clean_prev = np.zeros(power.shape[1])
    for t in range(frames):
        post = power[t] / noise
        prio = DD_ALPHA * clean_prev + (1 - DD_ALPHA) * np.maximum(post - 1, 0)
        g = np.maximum(prio / (1 + prio), floor)
        gains[t] = g
        clean_prev = g ** 2 * post
    out_frames = np.fft.irfft(spec * gains, n=N_FFT, axis=1) * win
    y = np.zeros(len(xp), np.float64)
    for t in range(frames):
        y[t * HOP:t * HOP + N_FFT] += out_frames[t]
    return y[HOP:HOP + n].astype(np.float32)


# ── RNNoise ─────────────────────────────────────────────────────────────────

RNN_RATE = 48_000
_rnn_lock = threading.Lock()
_rnn_lib: Any = None
_rnn_error: str | None = None


def _rnnoise_lib_path() -> str:
    """The shared library inside the installed pyrnnoise package, found
    without importing the package (its __init__ imports dependencies
    that aren't installed)."""
    spec = importlib.util.find_spec("pyrnnoise")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("pyrnnoise isn't installed (pip install --no-deps pyrnnoise)")
    folder = list(spec.submodule_search_locations)[0]
    name = {"win32": "rnnoise.dll", "darwin": "librnnoise.dylib"}.get(sys.platform, "librnnoise.so")
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        raise RuntimeError(f"pyrnnoise has no {name} for this platform")
    return path


def _load_rnnoise() -> Any:
    global _rnn_lib, _rnn_error
    with _rnn_lock:
        if _rnn_lib is not None:
            return _rnn_lib
        if _rnn_error is not None:
            raise RuntimeError(_rnn_error)
        try:
            lib = ctypes.CDLL(_rnnoise_lib_path())
            lib.rnnoise_create.argtypes = [ctypes.c_void_p]
            lib.rnnoise_create.restype = ctypes.c_void_p
            lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
            lib.rnnoise_get_frame_size.restype = ctypes.c_int
            lib.rnnoise_process_frame.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float),
            ]
            lib.rnnoise_process_frame.restype = ctypes.c_float
        except Exception as e:  # noqa: BLE001 - remembered, reported once
            _rnn_error = f"{type(e).__name__}: {e}"
            raise RuntimeError(_rnn_error) from e
        _rnn_lib = lib
        return lib


def _resample(x: np.ndarray, n_out: int) -> np.ndarray:
    """Band-limited resampling by zero-padding / truncating the spectrum
    (a whole capture at once: at most 30 s)."""
    spec = np.fft.rfft(x)
    out = np.zeros(n_out // 2 + 1, dtype=complex)
    k = min(len(spec), len(out))
    out[:k] = spec[:k]
    return (np.fft.irfft(out, n_out) * (n_out / len(x))).astype(np.float32)


def _rnnoise(x: np.ndarray) -> np.ndarray:
    lib = _load_rnnoise()
    n = len(x)
    fs = int(lib.rnnoise_get_frame_size())  # 480: 10 ms at 48 kHz
    up = _resample(x, n * 3) * 32768.0
    m = -(-len(up) // fs) * fs + fs  # one extra frame: the network's delay
    buf = np.zeros(m, np.float32)
    buf[: len(up)] = up
    out = np.empty_like(buf)
    state = lib.rnnoise_create(None)
    if not state:
        raise RuntimeError("rnnoise_create failed")
    try:
        frame = np.empty(fs, np.float32)
        ptr = frame.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        for i in range(0, m, fs):
            frame[:] = buf[i:i + fs]
            lib.rnnoise_process_frame(state, ptr, ptr)
            out[i:i + fs] = frame
    finally:
        lib.rnnoise_destroy(state)
    # RNNoise's output lags its input by one frame (10 ms).
    out = out[fs:fs + len(up)] / 32768.0
    return _resample(out, n)


# ── entry points ────────────────────────────────────────────────────────────

_FILTERS = {"spectral": _spectral, "rnnoise": _rnnoise}
_warned: set[str] = set()
_cache_lock = threading.Lock()
# The second hearing and a speculative decode hand the same audio in again;
# one entry is enough to not filter it twice.
_cache: tuple[str, str, bytes] | None = None


def current_mode() -> str:
    mode = str(getattr(settings, "stt_denoise", "off") or "off").strip().lower()
    return mode if mode in MODES else "off"


def _warn_once(key: str, msg: str, *args: Any) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning(msg, *args)


def denoise_pcm(pcm: bytes, mode: str | None = None) -> bytes:
    """``pcm`` (16 kHz mono int16) cleaned by ``mode`` (the setting when
    None). Any failure returns ``pcm`` unchanged: the experiment must
    never cost a turn."""
    global _cache
    mode = current_mode() if mode is None else mode
    if mode == "off" or not pcm:
        return pcm
    f = _FILTERS.get(mode)
    if f is None:
        _warn_once(f"mode:{mode}", "stt denoise: unknown mode %r; the audio goes to Whisper as captured", mode)
        return pcm
    try:
        key = hashlib.blake2b(pcm, digest_size=16).hexdigest()
        with _cache_lock:
            hit = _cache
        if hit is not None and hit[0] == mode and hit[1] == key:
            return hit[2]
        t0 = time.perf_counter()
        usable = len(pcm) - len(pcm) % 2
        x = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32) / 32768.0
        y = f(x)
        if y.shape != x.shape or not np.all(np.isfinite(y)):
            raise ValueError("filter returned unusable audio")
        out = (np.clip(y, -1.0, 32767 / 32768) * 32768.0).astype("<i2").tobytes() + pcm[usable:]
        log.debug("stt denoise %s: %.1f s of audio in %d ms", mode,
                  usable / (2 * SAMPLE_RATE), int((time.perf_counter() - t0) * 1000))
        with _cache_lock:
            _cache = (mode, key, out)
        return out
    except Exception as e:  # noqa: BLE001 - fail safe to the raw audio
        _warn_once(f"err:{mode}:{type(e).__name__}",
                   "stt denoise %s failed (%s: %s); the audio goes to Whisper as captured",
                   mode, type(e).__name__, e)
        return pcm


async def for_stt(pcm: bytes) -> bytes:
    """The audio Whisper should hear for this command capture: ``pcm``
    itself when the experiment is off (no thread hop), else the cleaned
    copy, filtered in the speech pool. Never raises."""
    mode = current_mode()
    if mode == "off":
        return pcm
    try:
        return await executors.run("speech", denoise_pcm, pcm, mode)
    except Exception as e:  # noqa: BLE001 - e.g. the pool shut down
        _warn_once(f"run:{type(e).__name__}", "stt denoise: couldn't run (%s); using the raw audio", e)
        return pcm
