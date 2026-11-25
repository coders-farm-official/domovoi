"""Speaker calibration, the satellite's side (docs/ROOM_AUDIO_PROTOCOL.md §11).

The server asks every speaker of a room, step by step, to record a window
while one of them plays a chirp at a stamped server time. This device:

* plays the chirp through the room player (``TimedPlayer.schedule_sound``)
  so its first sample reaches the hardware at ``at_us`` by the audio API's
  own time, without the calibrated latency it is measuring;
* records the window from the microphone the satellite already captures
  with (:class:`MicTap`, fed from the capture callback with each block's
  capture time);
* cross-correlates the recording with the same chirp (:func:`find_arrival`)
  and answers with the server time the chirp reached its microphone.

numpy and scipy are already satellite dependencies.
"""

from __future__ import annotations

import logging
import math
import threading
from typing import Any, Callable

import numpy as np

log = logging.getLogger("satellite.room_calibration")

FADE_S = 0.005
PRE_WINDOW_US = 100_000          # record from this long before the chirp is due
MIN_SNR_DB = 12.0
EARLIEST_WITHIN_DB = 6.0
#: The answer is computed this long after the window ends (the last blocks
#: of the window still have to come off the capture stream).
FINISH_SLACK_S = 0.3


# ─── the chirp and finding it ─────────────────────────────────────────────


def chirp(rate: int, f0_hz: float, f1_hz: float, ms: float, level: float = 1.0) -> np.ndarray:
    """The protocol's chirp (§11.1) at ``rate``: a linear sweep with 5 ms
    raised-cosine fades at both ends, float64 in -level..level."""
    dur = ms / 1000.0
    n = max(1, int(round(dur * rate)))
    t = np.arange(n) / rate
    x = level * np.sin(2 * np.pi * (f0_hz * t + (f1_hz - f0_hz) * t * t / (2 * dur)))
    fade = min(n // 2, int(round(FADE_S * rate)))
    if fade > 0:
        w = 0.5 - 0.5 * np.cos(np.pi * np.arange(fade) / fade)
        x[:fade] *= w
        x[-fade:] *= w[::-1]
    return x


def chirp_params(m: dict[str, Any]) -> tuple[float, float, float, float]:
    c = m.get("chirp") or {}
    f0 = float(c.get("f0_hz", 1000))
    f1 = float(c.get("f1_hz", 6000))
    ms = float(c.get("ms", 80))
    level = min(1.0, max(0.0, float(c.get("level", 0.3))))
    if not (20 <= f0 < 24_000 and 20 <= f1 < 24_000 and 5 <= ms <= 1000):
        raise ValueError("chirp out of range")
    return f0, f1, ms, level


def find_arrival(rec: np.ndarray, ref: np.ndarray, *,
                 min_snr_db: float = MIN_SNR_DB) -> tuple[float | None, float]:
    """Where ``ref`` starts in ``rec``, in samples (a fraction of one), and
    the peak's height over the median envelope in dB. ``None`` for the
    position when the chirp is not there (the peak is under ``min_snr_db``).

    The matched filter's envelope (magnitude of the analytic signal) is
    blind to the speaker's polarity and phase; the EARLIEST peak within
    6 dB of the highest is taken, since a reflection can be louder than the
    direct sound but never earlier."""
    from scipy.signal import correlate, hilbert

    rec = np.asarray(rec, dtype=np.float64)
    ref = np.asarray(ref, dtype=np.float64)
    if len(rec) < len(ref) + 8 or not np.any(ref):
        return None, 0.0
    corr = correlate(rec - rec.mean(), ref, mode="valid", method="fft")
    env = np.abs(hilbert(corr))
    peak = float(env.max())
    floor = float(np.median(env))
    snr = 20 * math.log10(peak / floor) if floor > 0 else (120.0 if peak > 0 else 0.0)
    if peak <= 0 or snr < min_snr_db:
        return None, snr
    i = int(np.flatnonzero(env >= peak * 10 ** (-EARLIEST_WITHIN_DB / 20))[0])
    while i + 1 < len(env) and env[i + 1] >= env[i]:
        i += 1
    frac = 0.0
    if 0 < i < len(env) - 1:
        y0, y1, y2 = env[i - 1], env[i], env[i + 1]
        den = y0 - 2 * y1 + y2
        if den < 0:
            frac = float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))
    return i + frac, snr


# ─── the microphone ───────────────────────────────────────────────────────


class MicTap:
    """A recording window on the satellite's running capture stream. The
    capture callback calls :meth:`feed` with every block (mono int16) and
    the local time its first sample reached the microphone; it is a cheap
    no-op while nothing is armed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._window: tuple[float, float] | None = None
        self._blocks: list[tuple[float, np.ndarray]] = []
        self.rate: int | None = None

    @property
    def armed(self) -> bool:
        return self._window is not None

    def arm(self, start_local_us: float, end_local_us: float) -> None:
        with self._lock:
            self._window = (start_local_us, end_local_us)
            self._blocks = []
            self.rate = None

    def feed(self, block: Any, first_local_us: float, rate: int) -> None:
        w = self._window
        if w is None:
            return
        x = np.frombuffer(block, dtype="<i2") if isinstance(block, (bytes, bytearray, memoryview)) \
            else np.asarray(block, dtype=np.int16).reshape(-1)
        end = first_local_us + len(x) * 1e6 / rate
        if end < w[0] or first_local_us > w[1]:
            return
        with self._lock:
            if self._window is None:
                return
            if self.rate not in (None, rate):
                self._blocks = []
            self.rate = rate
            self._blocks.append((first_local_us, x.copy()))

    def take(self) -> tuple[np.ndarray, float, float] | None:
        """``(samples, local time of the first, us per sample)`` and disarm;
        None when nothing was captured. The blocks are taken as contiguous
        (they are, from one stream); the start time is the median over the
        blocks, so one late callback does not move it."""
        with self._lock:
            blocks, rate = self._blocks, self.rate
            self._window, self._blocks = None, []
        if not blocks or not rate:
            return None
        us = 1e6 / rate
        counts = np.cumsum([0] + [len(b) for _, b in blocks[:-1]])
        t0 = float(np.median([t - n * us for (t, _), n in zip(blocks, counts)]))
        return np.concatenate([b for _, b in blocks]), t0, us


def adc_local_us(now_us: float, time_info: Any, frames: int, rate: int) -> float:
    """When a capture block's first sample reached the microphone, on the
    local clock: PortAudio's ``inputBufferAdcTime`` against its
    ``currentTime``, else the block's length before now."""
    cur = float(getattr(time_info, "currentTime", 0.0) or 0.0)
    adc = float(getattr(time_info, "inputBufferAdcTime", 0.0) or 0.0)
    if cur > 0 and adc > 0:
        return now_us - (cur - adc) * 1e6
    return now_us - frames * 1e6 / rate


# ─── a calibration step ───────────────────────────────────────────────────


class Calibrator:
    """Answers the server's ``calibrate`` steps for one room player.

    ``send`` queues a message for the room-audio socket; ``has_mic`` says
    whether the satellite captures at all."""

    def __init__(self, player: Any, *, send: Callable[[dict[str, Any]], None],
                 has_mic: Callable[[], bool] = lambda: True,
                 clock: Callable[[], float],
                 timer: Callable[..., Any] = threading.Timer) -> None:
        self.player = player
        self.send = send
        self.has_mic = has_mic
        self.clock = clock
        self.timer = timer
        self.tap = MicTap()

    def on_mic(self, block: Any, time_info: Any, frames: int, rate: int) -> None:
        """From the capture callback: never raises."""
        if not self.tap.armed:
            return
        try:
            self.tap.feed(block, adc_local_us(self.clock(), time_info, frames, rate), rate)
        except Exception:  # noqa: BLE001
            log.debug("calibration: a mic block was not taken", exc_info=True)

    def _answer(self, m: dict[str, Any], **kw: Any) -> None:
        self.send({"type": "calibrate_result", "run_id": m.get("run_id"),
                   "step": m.get("step"), **kw})

    def on_calibrate(self, m: dict[str, Any]) -> None:
        try:
            at = float(m["at_us"])
            listen_us = max(50.0, min(2000.0, float(m.get("listen_ms", 400)))) * 1000.0
            f0, f1, ms, level = chirp_params(m)
        except (KeyError, TypeError, ValueError):
            log.info("calibration: a malformed step was ignored")
            return
        play = bool(m.get("play"))
        fit = self.player.fit
        if not fit.converged:
            self._answer(m, played=False, heard=False, reason="no_clock")
            return
        token = None
        if play:
            from satellite.room_player import RATE

            x = chirp(RATE, f0, f1, ms, level)
            pcm = np.round(x * 32767).astype(np.int16)
            token = self.player.schedule_sound(np.stack([pcm, pcm], axis=1), at)
        if not self.has_mic():
            self._answer(m, played=self._played(token), heard=False, reason="no_mic")
            return
        # Server to local: the fit's offset (its rate term is ppm over a
        # second here, far inside the window's margin).
        off = fit.to_server(self.clock()) - self.clock()
        start, end = at - PRE_WINDOW_US - off, at + listen_us - off
        self.tap.arm(start, end)
        delay = max(0.0, (end - self.clock()) / 1e6) + FINISH_SLACK_S
        t = self.timer(delay, self._finish, args=(m, at, (f0, f1, ms), token))
        t.daemon = True
        t.start()

    def _played(self, token: Any) -> bool:
        return token is not None and self.player.sound_started(token)

    def _finish(self, m: dict[str, Any], at: float, sweep: tuple[float, float, float],
                token: Any) -> None:
        played = self._played(token)
        got = self.tap.take()
        if got is None:
            self._answer(m, played=played, heard=False, reason="busy")
            return
        rec, t0_local, us = got
        rate = int(round(1e6 / us))
        idx, snr = find_arrival(rec, chirp(rate, *sweep))
        if idx is None:
            log.info("calibration: chirp not heard (%.1f dB)", snr)
            self._answer(m, played=played, heard=False, reason="not_heard",
                         snr_db=round(snr, 1))
            return
        arrival = self.player.fit.to_server(t0_local + idx * us)
        log.info("calibration: chirp heard %.2f ms after its time (%.1f dB)",
                 (arrival - at) / 1000.0, snr)
        self._answer(m, played=played, heard=True, arrival_us=round(arrival, 1),
                     snr_db=round(snr, 1))
