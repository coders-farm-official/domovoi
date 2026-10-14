"""The core's check of a Windows satellite's device names
(domovoi/satellite_config_schema.py spec_for_board) is the satellite's own
(satellite/devices.py valid_windows_device): on board windows_pc the three
device settings take Windows names, not ALSA ones; every other board keeps
the ALSA rule. Merged with wf/sat-pc's core-side ALSA check (eeb09c9)."""

from __future__ import annotations

import pytest

from domovoi import satellite_config_schema as schema
from satellite import alsa_names, devices, remote_config

NAMES = [
    "default", "0", "3", "12", "Speakers (Realtek(R) Audio)", "Microphone Array (Intel® Smart Sound)",
    "USB PnP Sound Device: Audio [3]", "Headset (Jabra Evolve2 65)", "Line 1 (Virtual Audio Cable)",
    "plughw:CARD=Array,DEV=0", "hw:1,0", 'file:"|calc"', "x & calc.exe", "a;b", "-x", " default", "",
    "a\tb", "x" * 80, "x" * 81, "Spe$akers", "Speakers`id`", "C:\\Windows\\x", "(Speakers)",
]
FIELDS = sorted(schema.WINDOWS_DEVICE_FIELDS)


def _takes(name: str, value: str, board: str | None) -> bool:
    try:
        schema.coerce_and_validate(schema.spec_for_board(schema.FIELD_BY_NAME[name], board), value)
        return True
    except ValueError:
        return False


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", NAMES)
def test_a_windows_pc_s_device_names_are_the_satellite_s_windows_rule(field, value):
    assert _takes(field, value, "windows_pc") == devices.valid_windows_device(value), (field, value)
    # ...and the satellite's own frame screen agrees.
    assert bool(remote_config.screen({field: value}, pc=True, windows=True)[0]) == \
        devices.valid_windows_device(value), (field, value)


@pytest.mark.parametrize("board", [None, "generic_pc", "pi_xvf3800"])
@pytest.mark.parametrize("value", NAMES)
def test_every_other_board_keeps_the_alsa_rule_for_music(board, value):
    assert _takes("music.alsa_device", value, board) == alsa_names.safe_pcm(value), (board, value)


def test_the_windows_rule_replaces_only_the_device_names():
    for spec in schema.EDITABLE_FIELDS:
        same = schema.spec_for_board(spec, "windows_pc")
        if spec.name in schema.WINDOWS_DEVICE_FIELDS:
            assert same.pattern == schema.WINDOWS_DEVICE_PATTERN
        else:
            assert same is spec, spec.name
    assert schema.WINDOWS_DEVICE_PATTERN == devices._WINDOWS_DEVICE_NAME_RE.pattern.strip("^$")
