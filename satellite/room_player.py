"""The satellite's timed room player (1.5.0): every speaker in a room, in sync.

The device side of docs/ROOM_AUDIO_PROTOCOL.md, for a Pi, a Linux PC and a
Windows PC alike. It opens ``/v1/room-audio/{satellite_id}`` with the
device's pairing token, models the server clock from ping/pong exchanges,
and plays each 48 kHz stereo sample so it leaves the speaker at the server
time the room streamer stamped on it. Music, spoken answers, timers and
announcements all arrive on this one stream, already mixed by the server;
ducking is the server's ``gain`` messages.

Parts, each testable on its own:

* :class:`ClockFit`: the clock estimator of protocol §7 (a port of the
  server's reference, ``domovoi/room_playout/clock.py``);
* :class:`TimedPlayer`: the timeline buffer, timed ``flush`` / ``gain``,
  and playout with drift correction (§8);
* :class:`DacSmoother`: the audio API's reported output times, fitted
  against frames played (they jitter by about 1.5 ms on WASAPI);
* :class:`Output`: a PortAudio output stream (``sounddevice``, MIT) at the
  device's channels and rate (mono downmix; 16 kHz for the XVF3800 array);
* :class:`RoomPlayer`: the socket (its own threads, reconnecting with
  backoff), the output opened only while there is room audio to play, and
  the stats.

The output goes to ``[audio] output_device``, the device spoken answers
already use. On an XVF3800 that is the array's own USB playback, which is
what the chip's echo canceller takes as its reference: room audio played
here is cancelled out of the microphone like a reply is, so the wake word
is heard over it.

numpy, sounddevice, scipy and websockets are already satellite
dependencies; nothing new is installed.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
import struct
import sys
import threading
import time
from collections import deque
from typing import Any, Callable

import numpy as np

log = logging.getLogger("satellite.room_player")

# ─── the wire (protocol §2, §5.2) ─────────────────────────────────────────

PROTO = 1
RATE = 48_000
CHANNELS = 2
CHUNK_FRAMES = 960
KIND_AUDIO = 1
CODEC_PCM = 0
AUDIO_HDR = struct.Struct("<BBHIqq")
CLIENT_NAME = "room_player/1.5.0"


def now_us() -> float:
    """The local clock: monotonic, microseconds. ``perf_counter`` is
    ``CLOCK_MONOTONIC`` on Linux and ``QueryPerformanceCounter`` on
    Windows, the clocks PortAudio's stream times come from."""
    return time.perf_counter_ns() / 1000.0


def decode_audio(data: bytes) -> tuple[int, int, int, int, bytes] | None:
    """``(codec, frames, timeline_id, first_sample, payload)``, or None for
    a frame of another kind or one too short to be audio."""
    if len(data) < AUDIO_HDR.size:
        return None
    kind, codec, frames, tl, first, _play_at = AUDIO_HDR.unpack_from(data)
    if kind != KIND_AUDIO:
        return None
    return codec, frames, tl, first, bytes(data[AUDIO_HDR.size:])


def decode_msg(text: str) -> dict[str, Any] | None:
    try:
        msg = json.loads(text)
    except ValueError:
        return None
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        return None
    return msg


# ─── the clock (protocol §7) ──────────────────────────────────────────────

WINDOW_US = 60_000_000
KEEP_FRACTION = 0.10
MIN_KEEP = 4
RTT_SLACK_US = 100
RATE_SPAN_US = 10_000_000
YOUNG_US = 2_000_000
MAX_RATE = 500e-6
CONVERGED_SAMPLES = 16


class ClockFit:
    """``server ~= local + a + b * (local - tref)`` from four-timestamp pings."""

    def __init__(self) -> None:
        self._samples: deque[tuple[float, float, float]] = deque()  # mid, offset, rtt
        self.count = 0
        self.tref = 0.0
        self.a = 0.0
        self.b = 0.0
        self.best_rtt = math.inf

    @property
    def converged(self) -> bool:
        return self.count >= CONVERGED_SAMPLES

    def add(self, t0: float, t1: float, t2: float, t3: float) -> None:
        rtt = (t3 - t0) - (t2 - t1)
        if rtt < 0:
            return
        mid = (t0 + t3) / 2
        self._samples.append((mid, ((t1 - t0) + (t2 - t3)) / 2, rtt))
        while self._samples and self._samples[0][0] < mid - WINDOW_US:
            self._samples.popleft()
        self.count += 1
        self._refit(mid)

    def _refit(self, tref: float) -> None:
        ordered = sorted(self._samples, key=lambda s: s[2])
        n = max(MIN_KEEP, math.ceil(len(ordered) * KEEP_FRACTION))
        limit = max(ordered[min(n, len(ordered)) - 1][2], ordered[0][2] + RTT_SLACK_US)
        keep = [s for s in ordered if s[2] <= limit]
        self.best_rtt = keep[0][2]
        b = 0.0
        xs = [s[0] - tref for s in keep]
        if len(keep) >= MIN_KEEP and max(xs) - min(xs) >= RATE_SPAN_US:
            ys = [s[1] for s in keep]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            den = sum((x - mx) ** 2 for x in xs)
            if den > 0:
                b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
                b = max(-MAX_RATE, min(MAX_RATE, b))
        else:
            recent = [s for s in keep if s[0] >= tref - YOUNG_US]
            keep = recent or keep
        self.a = statistics.median(s[1] - b * (s[0] - tref) for s in keep)
        self.b = b
        self.tref = tref

    def to_server(self, local_us: float) -> float:
        return local_us + self.a + self.b * (local_us - self.tref)


# ─── the timeline and playout (protocol §5, §8) ───────────────────────────

RING_FRAMES = RATE * 8
JUMP_SAMPLES = RATE * 50 // 1000        # 50 ms
EMA_ALPHA = 0.02
FADE_FRAMES = RATE * 5 // 1000          # 5 ms
HOLE_FRAMES = RATE // 2                 # 500 ms
BURST_PINGS, BURST_INTERVAL_US, STEADY_INTERVAL_US = 20, 50_000, 250_000


class TimedPlayer:
    """The room's timeline as this device holds it. The socket thread feeds
    it (``on_text`` / ``on_binary``), the audio callback drains it
    (``render``); one lock keeps the two apart."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.fit = ClockFit()
        self.timeline_id: int | None = None
        self.t0_us = 0.0
        self.room_id: str | None = None
        self._ring = np.zeros((RING_FRAMES, 2), dtype=np.int16)
        self._at = np.full(RING_FRAMES, -1, dtype=np.int64)
        self._have = 0
        self.pos: int | None = None
        self._last_played: int | None = None
        self.ema = 0.0
        self._gain = (1.0, 1.0, 0.0, 0.0)   # from, to, at_us, ramp_us
        self.pings_sent = 0
        self._next_ping = 0.0
        self.underruns = self.late = self.corrections = self.jumps = 0
        self._err_sum = 0.0
        self._err_n = 0

    # ── clock ──────────────────────────────────────────────────────────
    def ping(self, local_us: float) -> dict[str, Any] | None:
        """A ping to send now, or None when it is not time yet."""
        if local_us < self._next_ping:
            return None
        self.pings_sent += 1
        gap = BURST_INTERVAL_US if self.pings_sent < BURST_PINGS else STEADY_INTERVAL_US
        self._next_ping = local_us + gap
        return {"type": "ping", "t0": local_us}

    def next_ping_in(self, local_us: float) -> float:
        return max(0.0, self._next_ping - local_us)

    # ── frames in ──────────────────────────────────────────────────────
    def on_text(self, text: str, local_us: float) -> dict[str, Any] | None:
        m = decode_msg(text)
        if m is None:
            return None
        kind = m["type"]
        with self.lock:
            try:
                if kind == "pong":
                    self.fit.add(float(m["t0"]), float(m["t1"]), float(m["t2"]), local_us)
                elif kind == "ready":
                    self.room_id = m.get("room_id")
                elif kind == "timeline":
                    self.timeline_id, self.t0_us = int(m["timeline_id"]), float(m["t0_us"])
                    self._clear()
                elif kind == "flush":
                    self._flush(float(m["at_us"]))
                elif kind == "gain":
                    at = float(m["at_us"])
                    g = float(self._gain_at(np.array([at]))[0])
                    self._gain = (g, min(1.0, max(0.0, float(m["gain"]))), at,
                                  max(0.0, float(m.get("ramp_ms", 0))) * 1000.0)
            except (KeyError, TypeError, ValueError):
                log.debug("room audio: malformed %s ignored", kind)
        return m

    def _clear(self) -> None:
        self._at[:] = -1
        self._have = 0
        self.pos = None
        self._last_played = None
        self._gain = (1.0, 1.0, 0.0, 0.0)

    def reset(self) -> None:
        """The socket is gone: forget the timeline and the clock (a new
        connection may be to a restarted server, a new clock epoch)."""
        with self.lock:
            self.timeline_id = None
            self._clear()
            self.fit = ClockFit()
            self.pings_sent = 0
            self._next_ping = 0.0

    def on_binary(self, data: bytes, local_us: float) -> None:
        f = decode_audio(data)
        if f is None:
            return
        codec, frames, tl, first, payload = f
        with self.lock:
            if tl != self.timeline_id or codec != CODEC_PCM:
                return
            if self.fit.converged:
                now = self.fit.to_server(local_us)
                if self.t0_us + (first + frames) * 1e6 / RATE <= now:
                    self.late += 1
                    return
            pcm = np.frombuffer(payload, dtype="<i2")
            pcm = pcm[: len(pcm) - len(pcm) % 2].reshape(-1, 2)
            n = len(pcm)
            if n == 0:
                return
            i = first % RING_FRAMES
            j = i + n
            if j <= RING_FRAMES:
                self._ring[i:j] = pcm
                self._at[i:j] = np.arange(first, first + n)
            else:
                k = RING_FRAMES - i
                self._ring[i:] = pcm[:k]
                self._ring[: j - RING_FRAMES] = pcm[k:]
                self._at[i:] = np.arange(first, first + k)
                self._at[: j - RING_FRAMES] = np.arange(first + k, first + n)
            self._have = max(self._have, first + n)

    def _flush(self, at_us: float) -> None:
        cut = math.ceil((at_us - self.t0_us) * RATE / 1e6)
        if self._have <= cut:
            return
        fade = np.arange(cut, min(cut + FADE_FRAMES, self._have))
        slots = fade % RING_FRAMES
        ok = self._at[slots] == fade
        ramp = (1 - (fade - cut) / FADE_FRAMES)[:, None]
        self._ring[slots[ok]] = (self._ring[slots[ok]] * ramp[ok]).astype(np.int16)
        gone = np.arange(cut + FADE_FRAMES, self._have)
        self._at[gone % RING_FRAMES] = -1
        self._have = min(self._have, cut + FADE_FRAMES)
        self._last_played = None

    def _gain_at(self, t: np.ndarray) -> np.ndarray:
        g0, g1, at, ramp = self._gain
        if ramp <= 0:
            return np.where(t < at, g0, g1)
        return g0 + (g1 - g0) * np.clip((t - at) / ramp, 0.0, 1.0)

    def _get(self, start: int, n: int) -> np.ndarray:
        i = start % RING_FRAMES
        if i + n <= RING_FRAMES:
            at, src = self._at[i:i + n], self._ring[i:i + n]
        else:
            slots = np.arange(start, start + n) % RING_FRAMES
            at, src = self._at[slots], self._ring[slots]
        ok = at == np.arange(start, start + n)
        if ok.all():
            if self._last_played is not None and start - self._last_played > 1 \
                    and start - self._last_played <= HOLE_FRAMES:
                self.underruns += 1
            self._last_played = start + n - 1
            return src.copy()
        present = np.flatnonzero(ok)
        if len(present):
            first = start + int(present[0])
            if (not ok[0] and self._last_played is not None
                    and first - self._last_played <= HOLE_FRAMES):
                self.underruns += 1
            elif not ok[present[0]:present[-1] + 1].all():
                self.underruns += 1
            self._last_played = start + int(present[-1])
        return np.where(ok[:, None], src, 0).astype(np.int16)

    # ── frames out ─────────────────────────────────────────────────────
    def due_index(self, local_us: float) -> float | None:
        if self.timeline_id is None or not self.fit.converged:
            return None
        return (self.fit.to_server(local_us) - self.t0_us) * RATE / 1e6

    def wants_output(self, local_us: float) -> bool:
        """Something buffered is still to play."""
        with self.lock:
            due = self.due_index(local_us)
            return due is not None and self._have > 0 and self._have > due

    def restart_position(self) -> None:
        """The output (re)opened: place the next buffer by the clock."""
        with self.lock:
            self.pos = None

    def render(self, dac_local_us: float, frames: int) -> np.ndarray:
        """The ``frames`` x 2 int16 buffer (48 kHz) whose first frame
        reaches the speaker at local time ``dac_local_us``."""
        with self.lock:
            due = self.due_index(dac_local_us)
            if due is None:
                return np.zeros((frames, 2), dtype=np.int16)
            server_t = self.fit.to_server(dac_local_us)
            if self.pos is None:
                self.pos, self.ema = round(due), 0.0
            err = self.pos - due
            self.ema += EMA_ALPHA * (err - self.ema)
            n_src, action = frames, 0
            if abs(err) > JUMP_SAMPLES:
                self.pos, self.ema = round(due), 0.0
                self.jumps += 1
            elif self.ema > 1.0:
                n_src, action = frames - 1, 1       # ahead: repeat one sample
                self.ema -= 1.0
            elif self.ema < -1.0:
                n_src, action = frames + 1, -1      # behind: drop one sample
                self.ema += 1.0
            else:
                self._err_sum += abs(err)
                self._err_n += 1
            src = self._get(self.pos, n_src)
            m = n_src // 2
            if action == 1:
                src = np.insert(src, m, src[m], axis=0)
                self.corrections += 1
            elif action == -1:
                src = np.delete(src, m, axis=0)
                self.corrections += 1
            self.pos += n_src
            g0, g1, at, ramp = self._gain
            end_t = server_t + frames * 1e6 / RATE
            if g0 == g1 or server_t >= at + ramp:
                g = g1
            elif end_t <= at:
                g = g0
            else:
                t = server_t + np.arange(frames) * (1e6 / RATE)
                return (src * self._gain_at(t)[:, None]).astype(np.int16)
            return src if g == 1.0 else (src * g).astype(np.int16)

    def stats(self) -> dict[str, Any]:
        with self.lock:
            err_us = (self._err_sum / self._err_n * 1e6 / RATE) if self._err_n else None
            self._err_sum, self._err_n = 0.0, 0
            msg: dict[str, Any] = {
                "type": "stats", "offset_us": self.fit.a, "rate_ppm": self.fit.b * 1e6,
                "underruns": self.underruns, "late": self.late,
                "corrections": self.corrections, "jumps": self.jumps,
            }
            if math.isfinite(self.fit.best_rtt):
                msg["rtt_us"] = self.fit.best_rtt
            if err_us is not None:
                msg["err_us"] = err_us
            return msg


# ─── the output device ────────────────────────────────────────────────────

class DacSmoother:
    """When each buffer reaches the speaker, fitted against the frames
    played: the hardware eats frames at a steady rate, while the times the
    audio API reports jitter. A jump (an underflow, a device hiccup) starts
    the fit over."""

    HISTORY = 256
    MIN_POINTS = 32
    REFIT_EVERY = 16
    RESET_US = 4_000.0

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._hist: deque[tuple[float, float]] = deque(maxlen=self.HISTORY)
        self._fit: tuple[float, float, float] | None = None   # (frames_at, t_at, us_per_frame)
        self._n = 0
        self._off = 0

    def add(self, consumed: float, reported_us: float) -> float:
        if self._fit is not None:
            f0, t0, k = self._fit
            pred = t0 + k * (consumed - f0)
            if abs(reported_us - pred) > self.RESET_US:
                # One wild report is jitter; three in a row is a jump.
                self._off += 1
                if self._off < 3:
                    return pred
                self.reset()
            else:
                self._off = 0
        self._hist.append((consumed, reported_us))
        self._n += 1
        if len(self._hist) >= self.MIN_POINTS and (self._fit is None or self._n % self.REFIT_EVERY == 0):
            h = np.asarray(self._hist)
            x = h[:, 0] - consumed
            y = h[:, 1]
            mx, my = x.mean(), y.mean()
            den = float(((x - mx) ** 2).sum())
            if den > 0:
                k = float(((x - mx) * (y - my)).sum() / den)
                self._fit = (consumed, float(my - k * mx), k)
        if self._fit is None:
            return reported_us
        f0, t0, k = self._fit
        return t0 + k * (consumed - f0)


class Decimator:
    """48 kHz to ``RATE / factor`` with a short low-pass FIR whose state
    carries across buffers (the XVF3800's USB playback is 16 kHz only)."""

    def __init__(self, factor: int, channels: int = 2) -> None:
        from scipy.signal import firwin, lfilter_zi

        self.factor = factor
        self.taps = firwin(12 * factor + 1, 0.9 / factor).astype(np.float32)
        zi = lfilter_zi(self.taps, [1.0]).astype(np.float32)
        self.zi = np.zeros((len(zi), channels), dtype=np.float32)
        self._phase = 0

    def process(self, x: np.ndarray) -> np.ndarray:
        from scipy.signal import lfilter

        y, self.zi = lfilter(self.taps, [1.0], x.astype(np.float32), axis=0, zi=self.zi)
        out = y[self._phase::self.factor]
        self._phase = (self._phase - len(x)) % self.factor
        return np.clip(np.round(out), -32768, 32767).astype(np.int16)


#: The output's own buffer (PortAudio ``latency``), seconds. Generous for a
#: Pi Zero whose one Python also runs the wake-word model: the timing
#: accounts for whatever it is.
OUTPUT_LATENCY_SEC = 0.1
#: One callback's worth, in milliseconds.
BLOCK_MS = 10


class Output:
    """A PortAudio output stream driven by a :class:`TimedPlayer`.

    Opens at 48 kHz, or (a device that refuses it) at its own rate when that
    divides 48 kHz, decimating; ``channels`` is what the device plays, 1 or
    2 (a mono speaker plays ``(L + R) / 2``)."""

    def __init__(self, player: TimedPlayer, device: Any = None, *, sd: Any = None,
                 clock: Callable[[], float] = now_us) -> None:
        self.player = player
        self.device = device
        self._sd = sd
        self.clock = clock
        self.stream: Any = None
        self.rate = RATE
        self.channels = 2
        self.factor = 1
        self._dec: Decimator | None = None
        self._dac = DacSmoother()
        self._consumed = 0
        self.callbacks = 0
        self.errors = 0

    def _sounddevice(self) -> Any:
        if self._sd is None:
            import sounddevice

            self._sd = sounddevice
        return self._sd

    def _device_info(self, sd: Any) -> dict[str, Any]:
        try:
            return dict(sd.query_devices(self.device, "output"))
        except Exception:  # noqa: BLE001
            return {}

    def _extra(self, sd: Any) -> Any:
        """Windows: WASAPI converts the rate in shared mode when asked."""
        if sys.platform != "win32" or not hasattr(sd, "WasapiSettings"):
            return None
        try:
            api = sd.query_hostapis(sd.query_devices(self.device, "output")["hostapi"])
            if "WASAPI" in str(api.get("name", "")):
                return sd.WasapiSettings(auto_convert=True)
        except Exception:  # noqa: BLE001
            pass
        return None

    def _wasapi_default(self, sd: Any) -> None:
        """Windows, no device chosen: the WASAPI default speakers rather than
        MME's, whose output times are too coarse to schedule against (the
        spike's 1.5 ms jitter was WASAPI's)."""
        if sys.platform != "win32" or self.device not in (None, "", "default"):
            return
        try:
            for api in sd.query_hostapis():
                if "WASAPI" in str(api.get("name", "")) and api.get("default_output_device", -1) >= 0:
                    self.device = api["default_output_device"]
                    return
        except Exception:  # noqa: BLE001
            pass

    def open(self) -> bool:
        sd = self._sounddevice()
        self._wasapi_default(sd)
        info = self._device_info(sd)
        max_ch = int(info.get("max_output_channels", 2) or 2)
        self.channels = 2 if max_ch >= 2 else 1
        rates = [RATE]
        dflt = int(float(info.get("default_samplerate", 0) or 0))
        if dflt and dflt != RATE and RATE % dflt == 0:
            rates.append(dflt)
        for extra in (16_000,):
            if extra not in rates:
                rates.append(extra)
        last: Exception | None = None
        for rate in rates:
            try:
                kw: dict[str, Any] = dict(
                    samplerate=rate, channels=self.channels, dtype="int16",
                    device=self.device, blocksize=rate * BLOCK_MS // 1000,
                    latency=OUTPUT_LATENCY_SEC, callback=self._callback,
                )
                extra = self._extra(sd)
                if extra is not None:
                    kw["extra_settings"] = extra
                stream = sd.OutputStream(**kw)
                self.rate, self.factor = rate, RATE // rate
                self._dec = Decimator(self.factor, self.channels) if self.factor > 1 else None
                self._dac.reset()
                self._consumed = 0
                self.player.restart_position()
                self.stream = stream
                stream.start()
                log.info("room audio: output open at %d Hz, %d channel(s), device=%s",
                         rate, self.channels, self.device)
                return True
            except Exception as e:  # noqa: BLE001 — PortAudio errors are many
                last = e
                self.stream = None
        log.warning("room audio: could not open the output (%s)", last)
        return False

    def close(self) -> None:
        stream, self.stream = self.stream, None
        if stream is None:
            return
        try:
            stream.abort()
        except Exception:  # noqa: BLE001
            pass
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass

    @property
    def is_open(self) -> bool:
        return self.stream is not None

    def _callback(self, outdata: Any, frames: int, time_info: Any, status: Any) -> None:
        try:
            self.fill(outdata, frames, time_info, status)
        except Exception:  # noqa: BLE001 — never raise into PortAudio
            self.errors += 1
            outdata[:] = 0
            if self.errors == 1:
                log.exception("room audio: output callback failed")

    def fill(self, outdata: Any, frames: int, time_info: Any, status: Any) -> None:
        now = self.clock()
        cur = float(getattr(time_info, "currentTime", 0.0) or 0.0)
        dac = float(getattr(time_info, "outputBufferDacTime", 0.0) or 0.0)
        if cur > 0 and dac > 0:
            reported = now + (dac - cur) * 1e6
        else:
            reported = now + OUTPUT_LATENCY_SEC * 1e6
        if status and getattr(status, "output_underflow", False):
            self._dac.reset()
        dac_us = self._dac.add(self._consumed, reported)
        self._consumed += frames
        pcm = self.player.render(dac_us, frames * self.factor)
        if self._dec is not None:
            pcm = self._dec.process(pcm)
        if self.channels == 1:
            outdata[:, 0] = ((pcm[:, 0].astype(np.int32) + pcm[:, 1]) // 2).astype(np.int16)
        else:
            outdata[:] = pcm
        self.callbacks += 1


# ─── the socket ───────────────────────────────────────────────────────────

BACKOFF_START_S = 1.0
BACKOFF_MAX_S = 30.0
STATS_EVERY_S = 5.0
#: Close the output this long after the last buffered sample played.
IDLE_CLOSE_S = 2.0
OUTPUT_POLL_S = 0.02


def _stamped_connection_class(clock: Callable[[], float] = now_us) -> Any:
    """A websockets sync connection that notes the local time each message's
    frame comes off the socket, in the socket's reader thread, before it is
    parsed or queued (protocol §7.2): a time taken after a thread or event
    loop hop carried a 0.5 ms bias in the spike."""
    from websockets.frames import Opcode
    from websockets.sync.client import ClientConnection

    class StampedConnection(ClientConnection):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.rx_stamps: deque[float] = deque()
            super().__init__(*args, **kwargs)

        def process_event(self, event: Any) -> None:
            if getattr(event, "opcode", None) in (Opcode.TEXT, Opcode.BINARY):
                self.rx_stamps.append(clock())
            super().process_event(event)

    return StampedConnection


def _connect(url: str, clock: Callable[[], float] = now_us) -> Any:
    from websockets.sync.client import connect

    return connect(url, create_connection=_stamped_connection_class(clock), compression=None,
                   open_timeout=10, close_timeout=2, max_size=1 << 20)


class _TimerResolution:
    """``timeBeginPeriod(1)`` while connected on Windows, or its timers
    (the socket's waits among them) round to about 15.6 ms."""

    def __enter__(self) -> "_TimerResolution":
        self._on = False
        if sys.platform == "win32":
            try:
                import ctypes

                self._on = ctypes.windll.winmm.timeBeginPeriod(1) == 0
            except Exception:  # noqa: BLE001
                pass
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._on:
            import ctypes

            ctypes.windll.winmm.timeEndPeriod(1)


class RoomPlayer:
    """The room-audio connection and the output it drives.

    ``device_free`` says whether this device's speaker may be opened now (a
    Pi's ALSA card takes one stream at a time; its spoken replies and
    acknowledgements open their own); ``release_output`` gives it up at once
    for a moment."""

    def __init__(self, url: str, satellite_id: str, token: Callable[[], str | None], *,
                 device: Any = None, device_free: Callable[[], bool] = lambda: True,
                 stop_event: threading.Event | None = None, sd: Any = None,
                 connect: Callable[..., Any] = _connect,
                 clock: Callable[[], float] = now_us) -> None:
        self.url = url
        self.satellite_id = satellite_id
        self.token = token
        self.device_free = device_free
        self.stop_event = stop_event or threading.Event()
        self._connect = connect
        self.clock = clock
        self.player = TimedPlayer()
        self.output = Output(self.player, device, sd=sd, clock=clock)
        self.connected = threading.Event()   # `ready` received on the live socket
        self.last_error: str | None = None
        self._ws: Any = None
        self._out_lock = threading.Lock()
        self._hold_until = 0.0
        self._threads: list[threading.Thread] = []

    # ── lifecycle ──────────────────────────────────────────────────────
    def start(self) -> None:
        if self._threads:
            return
        for name, fn in (("room-audio", self._run), ("room-audio-out", self._output_loop)):
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self.stop_event.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        for t in self._threads:
            t.join(timeout=3)
        with self._out_lock:
            self.output.close()

    def release_output(self, hold_s: float = 0.5) -> None:
        """Close the output now, and keep it closed for ``hold_s`` (then
        until ``device_free`` says the speaker is free again)."""
        with self._out_lock:
            self._hold_until = time.monotonic() + hold_s
            if self.output.is_open:
                log.info("room audio: giving the speaker to a local sound")
                self.output.close()

    @property
    def playing(self) -> bool:
        return self.output.is_open

    def seconds_until(self, server_us: float) -> float | None:
        """How long from now until the server clock reads ``server_us``
        (a timed room control outside the stream, such as the room's
        ``set_volume``). None before the clock fit has converged."""
        fit = self.player.fit
        if not fit.converged:
            return None
        local = self.clock()
        return (server_us - fit.to_server(local)) / 1e6

    # ── the socket thread ──────────────────────────────────────────────
    def _run(self) -> None:
        backoff = BACKOFF_START_S
        while not self.stop_event.is_set():
            got_ready = False
            try:
                got_ready = self._session()
            except Exception as e:  # noqa: BLE001 — any failure: reconnect
                if self.last_error != str(e):
                    log.info("room audio: %s (%s)", type(e).__name__, e)
                self.last_error = str(e)
            finally:
                self.connected.clear()
                self._ws = None
                self.player.reset()
            if got_ready:
                backoff = BACKOFF_START_S
            if self.stop_event.wait(backoff):
                return
            backoff = min(BACKOFF_MAX_S, backoff * 2)

    def _hello(self) -> dict[str, Any]:
        return {"type": "hello", "proto": PROTO, "satellite_id": self.satellite_id,
                "pairing_token": self.token(), "codecs": ["pcm_s16le"],
                "channels": self.output.channels, "output_latency_us": 0,
                "client": CLIENT_NAME}

    def _session(self) -> bool:
        """One connection, until it ends. True when the server accepted it."""
        ws = self._connect(self.url, self.clock)
        self._ws = ws
        stamps: deque[float] | None = getattr(ws, "rx_stamps", None)
        got_ready = False
        next_stats = time.monotonic() + STATS_EVERY_S
        with _TimerResolution():
            try:
                ws.send(json.dumps(self._hello(), separators=(",", ":")))
                while not self.stop_event.is_set():
                    now = self.clock()
                    msg = self.player.ping(now)
                    if msg is not None and got_ready:
                        msg["t0"] = self.clock()
                        ws.send(json.dumps(msg, separators=(",", ":")))
                    if got_ready and time.monotonic() >= next_stats:
                        next_stats = time.monotonic() + STATS_EVERY_S
                        if self.output.is_open:
                            ws.send(json.dumps(self.player.stats(), separators=(",", ":")))
                    wait = min(0.25, self.player.next_ping_in(self.clock()) / 1e6) if got_ready else 0.25
                    try:
                        data = ws.recv(timeout=max(0.001, wait))
                    except TimeoutError:
                        continue
                    t3 = stamps.popleft() if stamps else self.clock()
                    if isinstance(data, (bytes, bytearray, memoryview)):
                        self.player.on_binary(bytes(data), t3)
                        continue
                    m = self.player.on_text(data, t3)
                    if m is None:
                        continue
                    if m["type"] == "ready":
                        got_ready = True
                        self.last_error = None
                        self.connected.set()
                        log.info("room audio: connected to room %s", m.get("room_id"))
                    elif m["type"] == "error":
                        self.last_error = str(m.get("reason"))
                        log.warning("room audio: refused (%s)", self.last_error)
                        return got_ready
            finally:
                try:
                    ws.close()
                except Exception:  # noqa: BLE001
                    pass
        return got_ready

    # ── the output thread ──────────────────────────────────────────────
    def _output_loop(self) -> None:
        idle_since: float | None = None
        while not self.stop_event.wait(OUTPUT_POLL_S):
            wants = self.player.wants_output(self.clock())
            with self._out_lock:
                if self.output.is_open:
                    if wants:
                        idle_since = None
                    elif idle_since is None:
                        idle_since = time.monotonic()
                    elif time.monotonic() - idle_since >= IDLE_CLOSE_S:
                        log.info("room audio: nothing to play; closing the output")
                        self.output.close()
                        idle_since = None
                    continue
                if not wants or time.monotonic() < self._hold_until:
                    continue
                try:
                    free = self.device_free()
                except Exception:  # noqa: BLE001
                    free = False
                if free and not self.output.open():
                    self._hold_until = time.monotonic() + BACKOFF_START_S
                idle_since = None
