"""The wake chime: a short two-note acknowledgement, made on the Pi.

`[wake] ack_mode = "chime"` plays this instead of a spoken greeting (see
`Satellite._acknowledge_wake`). It is synthesized here rather than shipped
as an audio file for two reasons: the code-sync channel that upgrades a
satellite carries source files only (`_SAT_CODE_EXT_ALLOW` in
domovoi/main.py — clips ride `/v1/sounds`), so a bundled WAV would never
reach a Pi that was upgraded from the dashboard; and a few lines of
arithmetic are their own licence. Nothing here needs the Domovoi server, so the chime works offline
and with `[sounds] sync_enabled = false`.

Two plucked notes a fifth apart (A5 then E6), each a sine with a little
second harmonic for warmth, a 4 ms attack and a bell-like exponential
decay, 240 ms in all with a short fade to true silence at the end. Mono
16-bit at 22.05 kHz, about 10.6 KB as a WAV.

Level: the rendered greeting clips peak near full scale with an average
around -13 to -16 dBFS while they speak; the chime peaks at -8 dBFS and
averages about -19, a little under the voice — a cue, not an announcement
(two tones near 1 kHz, where the ear is most sensitive, carry further than
their level suggests). The satellite's `[playback] gain` multiplies it
exactly as `mpg123 --scale` multiplies the greeting clips, hard-clipped, so
one knob keeps the voice, the greetings and the chime level with each
other.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 22_050
DURATION_SEC = 0.24
# (frequency Hz, onset sec, decay time-constant sec) for each note.
_NOTES = ((880.0, 0.0, 0.07), (1318.51, 0.09, 0.05))
_SECOND_HARMONIC = 0.25
_ATTACK_SEC = 0.004
_FADE_OUT_SEC = 0.015
# Peak at unity gain, about -8 dBFS (see the module note on level). A
# `[playback] gain` up to 2.5 leaves it unclipped; the greeting clips, which
# peak close to full scale, clip well before that.
PEAK = 0.4


def render_pcm(gain: float = 1.0) -> np.ndarray:
    """The chime as int16 mono samples at ``SAMPLE_RATE``, scaled by
    ``gain`` and hard-clipped to the int16 range."""
    n = int(round(DURATION_SEC * SAMPLE_RATE))
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    out = np.zeros(n, dtype=np.float64)
    for freq, onset, tau in _NOTES:
        local = t - onset
        on = local >= 0.0
        lt = local[on]
        tone = np.sin(2 * np.pi * freq * lt) + _SECOND_HARMONIC * np.sin(4 * np.pi * freq * lt)
        env = np.minimum(1.0, lt / _ATTACK_SEC) * np.exp(-lt / tau)
        out[on] += tone * env
    # Normalize the sum, then land on silence: nothing clicks at the end.
    out *= PEAK / float(np.max(np.abs(out)))
    fade = int(_FADE_OUT_SEC * SAMPLE_RATE)
    out[-fade:] *= np.linspace(1.0, 0.0, fade)
    scaled = np.clip(out * float(gain) * 32767.0, -32768, 32767)
    return scaled.astype(np.int16)


def render_wav(gain: float = 1.0) -> bytes:
    """The chime as a complete WAV file (what `aplay` plays)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(render_pcm(gain).tobytes())
    return buf.getvalue()


def ensure(path: Path, gain: float = 1.0) -> Path:
    """Write the chime for ``gain`` to ``path`` unless it already holds
    exactly those bytes, and return ``path``. Written through a temp file
    and a rename, so a player never opens half a WAV."""
    data = render_wav(gain)
    try:
        if path.read_bytes() == data:
            return path
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path
