"""Comment-preserving TOML merge used when the dashboard pushes config
edits to a satellite."""

from __future__ import annotations

import tomllib

from satellite.config_writer import apply_changes

_SRC = """\
[wake]
# Detection threshold (0-1). Lower = more sensitive.
threshold = 0.5

[barge_in]
enabled = true
# require_wake_word = false
min_speech_ms = 250
"""


def test_replaces_in_place_and_preserves_comments() -> None:
    out = apply_changes(_SRC, {"wake.threshold": 0.7})
    assert "threshold = 0.7" in out
    assert "Lower = more sensitive" in out          # comment kept
    assert tomllib.loads(out)["wake"]["threshold"] == 0.7


def test_uncomments_a_commented_key() -> None:
    out = apply_changes(_SRC, {"barge_in.require_wake_word": True})
    assert tomllib.loads(out)["barge_in"]["require_wake_word"] is True
    # The other keys in the section are untouched.
    assert tomllib.loads(out)["barge_in"]["min_speech_ms"] == 250


def test_adds_missing_section_and_key() -> None:
    out = apply_changes(_SRC, {"leds.brightness": 200})
    d = tomllib.loads(out)
    assert d["leds"]["brightness"] == 200
    assert d["wake"]["threshold"] == 0.5            # untouched


def test_adds_missing_key_to_existing_section() -> None:
    out = apply_changes(_SRC, {"wake.wake_word": "hey_domovoi"})
    assert tomllib.loads(out)["wake"]["wake_word"] == "hey_domovoi"


def test_value_types_round_trip() -> None:
    out = apply_changes(
        _SRC,
        {
            "barge_in.enabled": False,
            "wake.threshold": 0.65,
            "barge_in.min_speech_ms": 500,
            "audio.input_device": "reSpeaker XVF3800",
        },
    )
    d = tomllib.loads(out)
    assert d["barge_in"]["enabled"] is False
    assert d["wake"]["threshold"] == 0.65
    assert d["barge_in"]["min_speech_ms"] == 500
    assert d["audio"]["input_device"] == "reSpeaker XVF3800"


def test_multiple_edits_still_parse() -> None:
    out = apply_changes(_SRC, {"wake.threshold": 0.8, "barge_in.enabled": False})
    # No exception == valid TOML.
    assert tomllib.loads(out)
