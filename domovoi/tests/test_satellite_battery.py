"""A spare device's battery in its health sample (S0's object, sent by S1).

Laptops and phones send one shape, ``battery`` = ``{percent, charging,
on_power}``, and leave it out when they have no battery; a Pi sends none,
so its stored sample and its journal line are unchanged. There is no
second shape: flat ``battery_percent`` / ``battery_charging`` keys are
dropped like any key the core doesn't know. Pure: no DB.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domovoi import satellite_health as sh

FIXTURES = json.loads(
    (Path(__file__).resolve().parents[2] / "docs" / "uml" / "satellite-fixtures.json")
    .read_text(encoding="utf-8")
)


def test_the_object_is_kept():
    kept = sh.validate_sample({"rss_kb": 1, "battery": {"percent": 81, "charging": False, "on_power": True}})
    assert kept["battery"] == {"percent": 81, "charging": False, "on_power": True}


def test_flat_keys_are_not_a_second_shape():
    kept = sh.validate_sample({"rss_kb": 1, "battery_percent": 81, "battery_charging": True})
    assert "battery_percent" not in kept and "battery_charging" not in kept and "battery" not in kept


def test_a_device_without_a_battery_stores_no_battery():
    assert "battery" not in sh.validate_sample({"rss_kb": 1, "mic_fps": 33.3})


def test_the_journal_line_says_the_battery_only_when_there_is_one():
    pi = FIXTURES["devices"]["pi_2mic_hat"]["health"]["stored"]
    assert "battery" not in sh.format_health_line("kitchen", pi)
    pc = sh.validate_sample({"rss_kb": 1, "battery": {"percent": 81, "charging": True, "on_power": True}})
    assert sh.format_health_line("study", pc).endswith(" battery=81% charging")
    held = sh.validate_sample({"rss_kb": 1, "battery": {"percent": 80, "charging": False, "on_power": True}})
    assert sh.format_health_line("study", held).endswith(" battery=80% on power")


@pytest.mark.parametrize("device", ["linux_pc", "android_room"])
def test_the_spare_devices_in_the_fixtures_send_the_object(device):
    frame = FIXTURES["devices"][device]["health"]["frame"]
    assert set(frame["battery"]) == {"percent", "charging", "on_power"}
    assert not any(k.startswith("battery_") for k in frame)
