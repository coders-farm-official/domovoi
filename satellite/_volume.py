"""Pure helpers for the satellite's hardware output-volume control.

The server's volume commands drive a single ALSA mixer control on
the satellite (the XVF3800's ``PCM``), which is the one gain both TTS
playback and music pass through — so one command controls Domovoi's voice
and the music together. These helpers build the ``amixer`` command lines
and parse the current level out of ``amixer get`` output. Kept free of
subprocess so they unit-test without hardware; the client runs the actual
commands in a worker thread (mirroring ``mic_gain.py``'s pure split).
"""

from __future__ import annotations

import re

# amixer prints a channel as e.g.
#   "Front Left: Playback 40 [67%] [-20.00dB] [on]"
_PERCENT_RE = re.compile(r"\[(\d{1,3})%\]")


def clamp(level: int) -> int:
    return max(0, min(100, int(level)))


def set_command(card: str, control: str, level: int) -> list[str]:
    """`amixer` argv to set ``control`` on ``card`` to ``level``% (unmuted)."""
    return ["amixer", "-c", str(card), "sset", control, f"{clamp(level)}%", "unmute"]


def get_command(card: str, control: str) -> list[str]:
    """`amixer` argv to read ``control`` on ``card``."""
    return ["amixer", "-c", str(card), "get", control]


def parse_volume_percent(amixer_output: str) -> int | None:
    """Return the first channel percentage from ``amixer get`` output, or
    None if no ``[NN%]`` token is present (control missing / parse fail)."""
    m = _PERCENT_RE.search(amixer_output)
    return int(m.group(1)) if m else None


# mpg123's default scale factor is 32768 (unity); larger values amplify the
# decoded PCM (hard-clipping peaks). See mpg123(1) --scale.
_MPG123_UNITY_SCALE = 32768


def mpg123_scale_args(gain: float) -> list[str]:
    """``mpg123 -f <factor>`` argv that applies the same linear ``gain`` to a
    locally-played clip that the streamed TTS PCM already gets, so greeting /
    canned clips match the voice level instead of playing at the quieter raw
    TTS-rendered level. Unity gain → no args (mpg123's default). Music is
    deliberately NOT scaled — MPD's MP3s are already at full scale."""
    if gain == 1.0:
        return []
    return ["-f", str(max(0, int(round(_MPG123_UNITY_SCALE * gain))))]
