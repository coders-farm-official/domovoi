"""Pure-logic tests for the satellite output-volume helpers."""

from __future__ import annotations

from satellite import _volume


def test_clamp():
    assert _volume.clamp(-5) == 0
    assert _volume.clamp(150) == 100
    assert _volume.clamp(50) == 50


def test_mpg123_scale_args():
    # Unity gain → no args (mpg123's default scale of 32768).
    assert _volume.mpg123_scale_args(1.0) == []
    # 3.0x → 3 * 32768 = 98304, matching the TTS PCM playback boost so
    # greeting/canned clips are as loud as the voice.
    assert _volume.mpg123_scale_args(3.0) == ["-f", "98304"]
    # Attenuation works too.
    assert _volume.mpg123_scale_args(0.5) == ["-f", "16384"]
    # Never negative.
    assert _volume.mpg123_scale_args(0.0) == ["-f", "0"]


def test_set_command():
    assert _volume.set_command("Array", "PCM", 80) == [
        "amixer", "-c", "Array", "sset", "PCM", "80%", "unmute",
    ]
    # Level is clamped into the command itself.
    assert _volume.set_command("0", "PCM", 250)[-2] == "100%"
    assert _volume.set_command("0", "PCM", -3)[-2] == "0%"


def test_get_command():
    assert _volume.get_command("Array", "PCM") == [
        "amixer", "-c", "Array", "get", "PCM",
    ]


def test_parse_volume_percent_from_real_output():
    # Real `amixer -c Array get PCM` output (the XVF3800 array).
    out = """Simple mixer control 'PCM',0
  Capabilities: pvolume pswitch
  Playback channels: Front Left - Front Right
  Limits: Playback 0 - 60
  Mono:
  Front Left: Playback 40 [67%] [-20.00dB] [on]
  Front Right: Playback 40 [67%] [-20.00dB] [on]
"""
    assert _volume.parse_volume_percent(out) == 67


def test_parse_volume_percent_full():
    out = "  Front Left: Playback 60 [100%] [0.00dB] [on]"
    assert _volume.parse_volume_percent(out) == 100


def test_parse_volume_percent_missing():
    assert _volume.parse_volume_percent("no percentage here") is None
    assert _volume.parse_volume_percent("") is None
