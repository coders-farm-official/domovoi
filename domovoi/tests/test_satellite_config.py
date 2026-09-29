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
    "wake.threshold", "wake.ack_mode",
    "barge_in.enabled", "barge_in.require_wake_word", "barge_in.min_speech_ms",
    "barge_in.vad_aggressiveness_during_tts",
    "listen.vad_aggressiveness", "listen.silence_timeout",
    "listen.max_record_seconds", "listen.followup_pre_speech_timeout",
    "listen.early_commit",
    "noise_gate.dbfs", "noise_gate.auto_calibrate",
    "greeting.funny_chance",
    "playback.gain", "playback.tts_prebuffer_sec",
    "sounds.sync_enabled", "log.level",
    "mic.enabled",
    "display.idle_mode", "display.power_method",
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
        if spec.choice_labels is not None:
            # A label for every choice and nothing else: the dashboard shows
            # the label and saves the choice.
            assert spec.type == "choice", f"{spec.name} has labels but no choices"
            assert set(spec.choice_labels) == set(spec.choices), spec.name
            assert all(v.strip() for v in spec.choice_labels.values()), spec.name
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


def test_wake_ack_mode_field() -> None:
    """Kamron, 2026-09-29: the wake acknowledgement is per satellite, three
    modes, the spoken greeting by default — offered in words, saved as the
    value the Pi reads from [wake] ack_mode."""
    spec = FIELD_BY_NAME["wake.ack_mode"]
    assert (spec.label, spec.group, spec.type, spec.section) == (
        "Wake acknowledgement", "Wake word", "choice", "common",
    )
    assert spec.choices == ["greeting", "chime", "none"]
    assert spec.choice_labels == {
        "greeting": "Spoken greeting, then listen",
        "chime": "Short chime, then listen",
        "none": "Light only, listen right away",
    }
    for mode in spec.choices:
        assert coerce_and_validate(spec, mode) == mode
    with pytest.raises(ValueError):
        coerce_and_validate(spec, "Spoken greeting, then listen")   # the label is not the value
    # What it replaced is gone from the drawer.
    assert "greeting.enabled" not in FIELD_BY_NAME
    assert "greeting.reply_wait" not in FIELD_BY_NAME


def _satellite_client():
    """satellite.client, importable without the audio stack (see
    satellite/tests/_client_import.py)."""
    from satellite.tests._client_import import import_client

    return import_client()


def test_the_schema_is_exactly_what_the_pi_reports() -> None:
    """The contract, checked against the real client rather than a copy of
    it: every field the drawer offers is one the Pi reports, and back."""
    import tomllib

    client = _satellite_client()
    sat = object.__new__(client.Satellite)
    sat.cfg = client.Config.from_toml(tomllib.loads('[wake]\nack_mode = "chime"\n'))
    report = sat._config_report()
    assert set(report) == set(FIELD_BY_NAME)
    assert report["wake.ack_mode"] == "chime"
    # And every value the schema offers is one the Pi accepts.
    for mode in FIELD_BY_NAME["wake.ack_mode"].choices:
        assert client.Config.from_toml({"wake": {"ack_mode": mode}}).wake_ack_mode == mode


@pytest.mark.asyncio
async def test_the_config_read_carries_the_choice_labels(monkeypatch) -> None:
    from domovoi import main

    monkeypatch.setattr(main.app.state, "active_sessions", {"office": object()}, raising=False)
    monkeypatch.setattr(
        main.app.state, "satellite_config",
        {"office": {"wake.ack_mode": "chime", "wake.threshold": 0.5}}, raising=False,
    )
    body = await main.admin_get_satellite_config("office")
    by_name = {f["name"]: f for f in body["fields"]}
    ack = by_name["wake.ack_mode"]
    assert ack["value"] == "chime"
    assert ack["choices"] == ["greeting", "chime", "none"]
    assert ack["choice_labels"]["none"] == "Light only, listen right away"
    # A field without labels says so plainly.
    assert by_name["log.level"]["choice_labels"] is None
    # A satellite too old to report the setting: no value, and the dashboard
    # shows "(not set)" rather than guessing.
    monkeypatch.setattr(main.app.state, "satellite_config", {"office": {}}, raising=False)
    body = await main.admin_get_satellite_config("office")
    assert {f["name"]: f for f in body["fields"]}["wake.ack_mode"]["value"] is None
