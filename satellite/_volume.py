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


# ─── desktop sound servers (spare-device satellites, 2026-10-13) ──────────
#
# On a laptop the ALSA `default` device goes to PipeWire or PulseAudio in
# the user's session, and amixer's card 0 is often the HDMI output rather
# than the speakers. There the volume is the sound server's default sink:
# `wpctl` (PipeWire's WirePlumber) or `pactl` (PulseAudio, and PipeWire's
# pulse layer). Same pure split as above: argv builders and parsers here,
# the subprocess in satellite/engine_platform.py.

WPCTL_SINK = "@DEFAULT_AUDIO_SINK@"
PACTL_SINK = "@DEFAULT_SINK@"
# `wpctl get-volume` prints "Volume: 0.40" (and " [MUTED]" when muted).
_WPCTL_RE = re.compile(r"Volume:\s*([0-9]+(?:\.[0-9]+)?)")
# `pactl get-sink-volume` prints per channel "front-left: 26214 /  40% / …".
_PACTL_RE = re.compile(r"/\s*(\d{1,3})%")


def wpctl_get_command() -> list[str]:
    return ["wpctl", "get-volume", WPCTL_SINK]


def wpctl_set_commands(level: int) -> list[list[str]]:
    """Set the default sink to ``level``% and unmute it (two calls)."""
    return [
        ["wpctl", "set-volume", WPCTL_SINK, f"{clamp(level) / 100:.2f}"],
        ["wpctl", "set-mute", WPCTL_SINK, "0"],
    ]


def parse_wpctl_volume(output: str) -> int | None:
    m = _WPCTL_RE.search(output)
    if not m:
        return None
    return clamp(round(float(m.group(1)) * 100))


def pactl_get_command() -> list[str]:
    return ["pactl", "get-sink-volume", PACTL_SINK]


def pactl_set_commands(level: int) -> list[list[str]]:
    return [
        ["pactl", "set-sink-volume", PACTL_SINK, f"{clamp(level)}%"],
        ["pactl", "set-sink-mute", PACTL_SINK, "0"],
    ]


def parse_pactl_volume(output: str) -> int | None:
    m = _PACTL_RE.search(output)
    return clamp(int(m.group(1))) if m else None


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
