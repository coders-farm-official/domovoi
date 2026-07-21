"""Per-satellite config editing (Phase B) — schema integrity + the
contract that the core schema keys match what the Pi reports."""

from __future__ import annotations

import pytest

from domovoi.satellite_config_schema import (
    EDITABLE_FIELDS,
    FIELD_BY_NAME,
    coerce_and_validate,
)

# Must stay in lockstep with satellite/client.py `_config_report()` — the
# web joins this schema's fields with the values that report caches. If you
# add/remove a field here, update the Pi report (and this set) too.
_EXPECTED_KEYS = {
    "wake.threshold",
    "barge_in.enabled", "barge_in.require_wake_word", "barge_in.min_speech_ms",
    "barge_in.vad_aggressiveness_during_tts",
    "listen.vad_aggressiveness", "listen.silence_timeout",
    "listen.max_record_seconds", "listen.followup_pre_speech_timeout",
    "noise_gate.dbfs", "noise_gate.auto_calibrate",
    "greeting.enabled", "greeting.funny_chance",
    "playback.gain", "playback.tts_prebuffer_sec",
    "sounds.sync_enabled", "log.level",
    "audio.input_device", "audio.output_device",
    "audio.output_mixer_card", "audio.output_mixer_control",
    "music.alsa_device",
    "leds.enabled", "leds.num_leds", "leds.brightness",
    "mic_gain.enabled", "mic_gain.target_dbfs",
    "wifi.enabled", "wifi.min_healthy_mbits",
}


def test_schema_keys_match_pi_report_contract() -> None:
    assert set(FIELD_BY_NAME) == _EXPECTED_KEYS


def test_field_specs_well_formed() -> None:
    for spec in EDITABLE_FIELDS:
        assert "." in spec.name, f"{spec.name} should be section.key"
        assert spec.help.strip(), f"{spec.name} has no tooltip"
        assert spec.section in ("common", "advanced")
        assert spec.type in ("int", "float", "bool", "str", "choice")
        if spec.type == "choice":
            assert spec.choices, f"{spec.name} is a choice with no choices"
        if spec.min is not None and spec.max is not None:
            assert spec.min <= spec.max, f"{spec.name} has min > max"


def test_coercion_and_bounds() -> None:
    assert coerce_and_validate(FIELD_BY_NAME["wake.threshold"], "0.7") == 0.7
    with pytest.raises(ValueError):
        coerce_and_validate(FIELD_BY_NAME["wake.threshold"], 1.5)   # > max 1.0
    assert coerce_and_validate(FIELD_BY_NAME["barge_in.enabled"], "true") is True
    assert coerce_and_validate(FIELD_BY_NAME["log.level"], "DEBUG") == "DEBUG"
    with pytest.raises(ValueError):
        coerce_and_validate(FIELD_BY_NAME["log.level"], "LOUD")
    assert coerce_and_validate(FIELD_BY_NAME["leds.brightness"], 200) == 200
