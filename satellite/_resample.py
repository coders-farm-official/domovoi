"""Streaming int16 PCM resampler for the playback thread.

Why this exists: the XVF3800 USB array only accepts audio at its fixed
native rate over USB, but the Domovoi server streams TTS at whatever rate
its engine produced (Edge ≈ 24 kHz, Piper ≈ 22.05 kHz, system varies).
The playback thread therefore has to resample each incoming PCM chunk to
the device rate on the fly.

The naive approach — ``resample_poly`` on each chunk independently —
restarts the anti-alias FIR with zero history at every chunk boundary,
producing a discontinuity (audible click/buzz) at each seam on continuous
speech. This module does overlap-save: it carries the FIR's left context
(recently-emitted input) AND holds back the right edge of each chunk until
the next chunk supplies the future samples the filter window needs. The
result is bit-for-bit identical to resampling the whole stream at once,
emitted incrementally.

scipy is already a satellite dependency (see requirements.txt — pulled in
for openWakeWord), so ``scipy.signal.resample_poly`` adds no new package.
"""

from __future__ import annotations

import logging
from fractions import Fraction
from math import ceil, gcd

import numpy as np
from scipy.signal import resample_poly

log = logging.getLogger("satellite.resample")

# resample_poly builds a Kaiser-windowed-sinc FIR of length
# ``2 * _HALF_LEN * max(up, down) + 1`` taps at the upsampled rate. We
# mirror that constant to size the overlap context.
_HALF_LEN = 10

# Cap the polyphase ratio so an awkward source rate (22050 → 16000 has an
# exact ratio of 320/441) doesn't blow the FIR up to thousands of taps and
# stall the Pi Zero 2 W. Approximating the ratio with a denominator this
# small is a sub-0.1% rate error — an inaudible fraction-of-a-cent pitch
# shift on speech — while keeping the filter cheap.
_MAX_DENOM = 160


class StreamingResampler:
    """Resamples a stream of int16 PCM chunks from ``src_rate`` to
    ``dst_rate`` with continuous filter state across chunk boundaries.

    Usage from the playback thread::

        rs = StreamingResampler(src, dst)
        for chunk in chunks:
            out = rs.process(chunk)   # may be b"" while priming
            if out: stream.write(out)
        tail = rs.flush()             # emit the held-back final samples
        if tail: stream.write(tail)
        rs.reset()                    # before the next response
    """

    def __init__(self, src_rate: int, dst_rate: int) -> None:
        self.src_rate = int(src_rate)
        self.dst_rate = int(dst_rate)
        if self.src_rate == self.dst_rate:
            self.up = self.down = 1
        else:
            g = gcd(self.src_rate, self.dst_rate)
            up, down = self.dst_rate // g, self.src_rate // g
            if max(up, down) > _MAX_DENOM:
                fr = Fraction(self.dst_rate, self.src_rate).limit_denominator(_MAX_DENOM)
                up, down = fr.numerator, fr.denominator
            self.up, self.down = up, down

        # Overlap context in INPUT samples — half the FIR length, mapped
        # back through the upsample factor, plus slack. We keep this much
        # already-emitted input as left context AND withhold this much at
        # the right edge until the next chunk supplies the future samples
        # the filter window needs.
        self._margin = ceil(_HALF_LEN * max(self.up, self.down) / self.up) + 1

        # `_buf` is the working window: retained left context followed by
        # input not yet emitted. Its first sample sits at absolute input
        # index `_buf_start`, which we keep a multiple of `down` so that
        # local output index j maps EXACTLY to global output index
        # (_buf_start*up//down + j) — that alignment is what makes emission
        # contiguous and drift-free across arbitrarily-chunked input.
        self._buf = np.zeros(0, dtype=np.float32)
        self._buf_start = 0   # absolute input index of _buf[0] (multiple of down)
        self._emitted = 0     # absolute count of output samples emitted so far

    @property
    def passthrough(self) -> bool:
        return self.up == 1 and self.down == 1

    def reset(self) -> None:
        """Clear filter state so the next response starts clean."""
        self._buf = np.zeros(0, dtype=np.float32)
        self._buf_start = 0
        self._emitted = 0

    def _to_int16(self, y: np.ndarray) -> bytes:
        return np.clip(np.round(y), -32768, 32767).astype(np.int16).tobytes()

    def process(self, pcm: bytes) -> bytes:
        """Resample one chunk. Returns the int16 PCM safe to emit now (may
        be empty while the filter primes on the first chunk)."""
        if self.passthrough or not pcm:
            return pcm
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        self._buf = np.concatenate([self._buf, x])

        # Highest input index we have enough right-context to filter, and
        # the highest global output sample whose input center is within it.
        safe_in = self._buf_start + len(self._buf) - self._margin
        if safe_in <= 0:
            return b""
        m_safe = (safe_in * self.up) // self.down
        if m_safe <= self._emitted:
            return b""  # nothing new is safely emittable yet

        y = resample_poly(self._buf, self.up, self.down)
        m0 = (self._buf_start * self.up) // self.down  # exact: down | _buf_start
        lo = max(0, min(self._emitted - m0, len(y)))
        hi = max(lo, min(m_safe - m0, len(y)))
        emit = y[lo:hi]
        self._emitted = m0 + hi

        # Drop input we no longer need, keeping `_margin` of left context
        # before the next output's input position and staying aligned to a
        # multiple of `down`.
        next_in = (self._emitted * self.down) // self.up
        keep_from = max(0, next_in - self._margin)
        keep_from -= keep_from % self.down
        drop = keep_from - self._buf_start
        if drop > 0:
            self._buf = self._buf[drop:].copy()
            self._buf_start = keep_from
        return self._to_int16(emit)

    def flush(self) -> bytes:
        """Emit the remaining tail at end of stream. resample_poly
        zero-pads the right edge, which is correct at a genuine boundary.
        Leaves state reset for the next response."""
        if self.passthrough or len(self._buf) == 0:
            self.reset()
            return b""
        y = resample_poly(self._buf, self.up, self.down)
        m0 = (self._buf_start * self.up) // self.down
        lo = max(0, min(self._emitted - m0, len(y)))
        emit = y[lo:]
        self.reset()
        return self._to_int16(emit)
