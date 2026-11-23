"""Reference room-audio client (protocol §7-§8), for tests and as a model.

Transport-free: feed it the socket's frames with the local time each was
read, ask it for pings, and call :meth:`TimedPlayer.render` from the output
device's callback with the local time that buffer reaches the speaker.
Device players (slice 6, the apps) port this; it leaves out what only a
real device needs (smoothing reported DAC times, ``timeBeginPeriod``).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from domovoi.room_playout import protocol as P
from domovoi.room_playout.clock import ClockFit

RING_FRAMES = P.RATE * 8
JUMP_SAMPLES = P.RATE * 50 // 1000      # 50 ms
EMA_ALPHA = 0.02
FADE_FRAMES = P.RATE * 5 // 1000        # 5 ms
HOLE_FRAMES = P.RATE // 2               # 500 ms
BURST_PINGS, BURST_INTERVAL_US, STEADY_INTERVAL_US = 20, 50_000, 250_000


class TimedPlayer:
    def __init__(self) -> None:
        self.fit = ClockFit()
        self.timeline_id: int | None = None
        self.t0_us = 0
        self.room_id: str | None = None
        self._ring = np.zeros((RING_FRAMES, 2), dtype=np.int16)
        self._at = np.full(RING_FRAMES, -1, dtype=np.int64)   # index held per slot
        self._have = 0
        self.pos: int | None = None
        self._last_played: int | None = None
        self.ema = 0.0
        self._gain = (1.0, 1.0, 0.0, 0.0)   # from, to, at_us, ramp_us
        self.pings_sent = 0
        self._next_ping = 0.0
        self.underruns = self.late = self.corrections = self.jumps = 0
        self.output_latency_us = 0.0      # the server's `latency` (§5.5)

    # ── clock ──────────────────────────────────────────────────────────
    def ping(self, local_us: float) -> dict[str, Any] | None:
        """A ping to send now, or None when it is not time yet."""
        if local_us < self._next_ping:
            return None
        self.pings_sent += 1
        gap = BURST_INTERVAL_US if self.pings_sent < BURST_PINGS else STEADY_INTERVAL_US
        self._next_ping = local_us + gap
        return {"type": "ping", "t0": local_us}

    # ── frames in ──────────────────────────────────────────────────────
    def on_text(self, text: str, local_us: float) -> None:
        m = P.decode_msg(text)
        if m is None:
            return
        kind = m["type"]
        if kind == "pong":
            self.fit.add(m["t0"], m["t1"], m["t2"], local_us)
        elif kind == "ready":
            self.room_id = m.get("room_id")
        elif kind == "timeline":
            self.timeline_id, self.t0_us = m["timeline_id"], m["t0_us"]
            self._at[:] = -1
            self._have = 0
            self.pos = None
            self._last_played = None
            self._gain = (1.0, 1.0, 0.0, 0.0)
        elif kind == "flush":
            self._flush(m["at_us"])
        elif kind == "latency":
            self.output_latency_us = float(m["output_latency_us"])
        elif kind == "gain":
            g = self._gain_at(np.array([float(m["at_us"])]))[0]
            self._gain = (float(g), float(m["gain"]), float(m["at_us"]), m["ramp_ms"] * 1000.0)

    def on_binary(self, data: bytes, local_us: float) -> None:
        f = P.decode_audio(data)
        if f is None or f.timeline_id != self.timeline_id or f.codec != P.CODEC_PCM:
            return
        if self.fit.converged:
            now = self.fit.to_server(local_us)
            if P.play_time_us(self.t0_us, f.first_sample + f.frames) <= now:
                self.late += 1
                return
        pcm = np.frombuffer(f.payload, dtype="<i2").reshape(-1, 2)
        idx = np.arange(f.first_sample, f.first_sample + len(pcm))
        self._ring[idx % RING_FRAMES] = pcm
        self._at[idx % RING_FRAMES] = idx
        self._have = max(self._have, f.first_sample + len(pcm))

    def _flush(self, at_us: float) -> None:
        cut = math.ceil((at_us - self.t0_us) * P.RATE / 1e6)
        if self._have <= cut:
            return
        fade = np.arange(cut, min(cut + FADE_FRAMES, self._have))
        slots = fade % RING_FRAMES
        ok = self._at[slots] == fade
        ramp = (1 - (fade - cut) / FADE_FRAMES)[:, None]
        self._ring[slots[ok]] = (self._ring[slots[ok]] * ramp[ok]).astype(np.int16)
        gone = np.arange(cut + FADE_FRAMES, self._have)
        self._at[gone % RING_FRAMES] = -1
        self._last_played = None

    def _gain_at(self, t: np.ndarray) -> np.ndarray:
        g0, g1, at, ramp = self._gain
        if ramp <= 0:
            return np.where(t < at, g0, g1)
        return g0 + (g1 - g0) * np.clip((t - at) / ramp, 0.0, 1.0)

    def _get(self, start: int, n: int) -> np.ndarray:
        idx = np.arange(start, start + n)
        slots = idx % RING_FRAMES
        ok = self._at[slots] == idx
        present = np.flatnonzero(ok)
        if len(present):
            # A hole: audio resumes under HOLE_FRAMES after it last played.
            # (A gap between sounds is longer; a first sound has no "last".)
            first = int(idx[present[0]])
            if (not ok[0] and self._last_played is not None
                    and first - self._last_played <= HOLE_FRAMES):
                self.underruns += 1
            elif not ok[present[0]:present[-1] + 1].all():
                self.underruns += 1
            self._last_played = int(idx[present[-1]])
        return np.where(ok[:, None], self._ring[slots], 0).astype(np.int16)

    # ── frames out ─────────────────────────────────────────────────────
    def render(self, dac_local_us: float, frames: int) -> np.ndarray:
        """The ``frames`` x 2 int16 buffer whose first frame reaches the
        speaker at local time ``dac_local_us``."""
        if self.timeline_id is None or not self.fit.converged:
            return np.zeros((frames, 2), dtype=np.int16)
        # The sound leaves the speaker output_latency_us after the API's time.
        server_t = self.fit.to_server(dac_local_us + self.output_latency_us)
        due = (server_t - self.t0_us) * P.RATE / 1e6
        if self.pos is None:
            self.pos, self.ema = round(due), 0.0
        err = self.pos - due
        self.ema += EMA_ALPHA * (err - self.ema)
        n_src, action = frames, 0
        if abs(err) > JUMP_SAMPLES:
            self.pos, self.ema = round(due), 0.0
            self.jumps += 1
        elif self.ema > 1.0:
            n_src, action = frames - 1, 1      # ahead: repeat one sample
            self.ema -= 1.0
        elif self.ema < -1.0:
            n_src, action = frames + 1, -1     # behind: drop one sample
            self.ema += 1.0
        src = self._get(self.pos, n_src)
        m = n_src // 2
        if action == 1:
            src = np.insert(src, m, src[m], axis=0)
            self.corrections += 1
        elif action == -1:
            src = np.delete(src, m, axis=0)
            self.corrections += 1
        self.pos += n_src
        t = server_t + np.arange(frames) * 1e6 / P.RATE
        g = self._gain_at(t)
        if np.any(g != 1.0):
            src = np.clip(np.round(src * g[:, None]), -32768, 32767).astype(np.int16)
        return src

    def stats(self) -> dict[str, Any]:
        return {"type": "stats", "offset_us": self.fit.a, "rate_ppm": self.fit.b * 1e6,
                "rtt_us": self.fit.best_rtt if math.isfinite(self.fit.best_rtt) else None,
                "err_us": abs(self.ema) * 1e6 / P.RATE, "underruns": self.underruns,
                "late": self.late, "corrections": self.corrections, "jumps": self.jumps}
