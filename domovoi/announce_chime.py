"""The chime before an announcement or a broadcast.

A soft two-note cue ahead of the first spoken word, so people in the room
look up before the message starts. It is joined onto the front of the same
audio stream the announcement speaks on, so every satellite type (Pi, spare
PC, phone room mode) plays it with no client change. Normal replies never
get it: only ``StreamSession.announce`` (intercom, Broadcast, timers, and
plugins' ``sdk.speech.announce``) calls this.

Generated at import from the drop-in chime's synth (domovoi/dropin_chimes.py):
nothing to ship or download. Household setting: ``announce_chime_enabled``.
"""

from __future__ import annotations

from domovoi import dropin_chimes as _dc

SAMPLE_RATE = _dc.SAMPLE_RATE

# E5 -> A5 (rising), then a short beat so the first word isn't clipped.
_SAMPLES = (
    _dc._silence(0.02) + _dc._tone(659.25, 0.12) + _dc._silence(0.02)
    + _dc._tone(880.0, 0.20) + _dc._silence(0.22)
)

CHIME_PCM: bytes = _dc._to_pcm(_SAMPLES)


def chime_pcm(sample_rate: int) -> bytes:
    """The chime as int16 mono PCM at ``sample_rate``."""
    if sample_rate == SAMPLE_RATE:
        return CHIME_PCM
    from domovoi.streaming import _resample_pcm

    return _resample_pcm(CHIME_PCM, SAMPLE_RATE, sample_rate)
