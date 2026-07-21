"""Synthesized drop-in start/end chimes (Feature 4).

Soft two-note sine chimes, 16 kHz mono int16 PCM — matching the drop-in relay
rate so the Pi plays them at the right pitch with no resampling. The START
chime RISES (connecting); the END chime FALLS (disconnecting), so the two are
clearly distinct but share a family resemblance. Soft (low amplitude, gentle
bell-like attack/decay) but obvious (two clean notes).

Generated deterministically at import with only the stdlib — no audio asset to
ship or sync. The core streams these over the open-mic channel at call
start/end (``StreamSession._begin_dropin`` / ``_end_dropin``), so BOTH rooms
hear the same cue at the same moment, through the exact path the live audio
uses (if relay audio plays, the chime plays).
"""

from __future__ import annotations

import array
import math

SAMPLE_RATE = 16_000
_AMPLITUDE = 0.22  # peak fraction of full scale — soft, not startling


def _tone(freq: float, dur_s: float, *, attack: float = 0.008) -> list[float]:
    """One note: fundamental + a quiet 2nd harmonic, raised-cosine attack into
    an exponential decay — a soft bell rather than a hard beep."""
    n = int(dur_s * SAMPLE_RATE)
    out: list[float] = []
    for i in range(n):
        t = i / SAMPLE_RATE
        if t < attack:
            env = 0.5 * (1.0 - math.cos(math.pi * t / attack))
        else:
            env = math.exp(-3.0 * (t - attack) / max(1e-6, dur_s - attack))
        s = math.sin(2 * math.pi * freq * t) + 0.18 * math.sin(2 * math.pi * 2 * freq * t)
        out.append(env * s)
    return out


def _silence(dur_s: float) -> list[float]:
    return [0.0] * int(dur_s * SAMPLE_RATE)


def _to_pcm(samples: list[float]) -> bytes:
    """Normalize to ``_AMPLITUDE`` peak and quantize to int16 LE."""
    peak = max((abs(s) for s in samples), default=1.0) or 1.0
    norm = _AMPLITUDE / peak
    buf = array.array("h")
    for s in samples:
        v = max(-1.0, min(1.0, s * norm))
        buf.append(int(v * 32767))
    return buf.tobytes()


# Start: D5 → G5 (rising). End: G5 → C5 (falling).
_START = _silence(0.03) + _tone(587.33, 0.13) + _silence(0.02) + _tone(783.99, 0.18)
_END = _silence(0.03) + _tone(783.99, 0.13) + _silence(0.02) + _tone(523.25, 0.20)

START_CHIME_PCM: bytes = _to_pcm(_START)
END_CHIME_PCM: bytes = _to_pcm(_END)
START_CHIME_SEC: float = len(_START) / SAMPLE_RATE
END_CHIME_SEC: float = len(_END) / SAMPLE_RATE
