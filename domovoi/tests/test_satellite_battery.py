"""A spare device's battery in its health sample (S1, 2026-10-13).

Laptops and phones send the same two keys, ``battery_percent`` (a whole
number 0-100) and ``battery_charging`` (true while on the charger), and
leave both out when they have no battery; a Pi sends neither, so its
stored sample and its journal line are unchanged. Pure: no DB.
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


def test_the_two_keys_are_kept():
    kept = sh.validate_sample({"rss_kb": 1, "battery_percent": 81, "battery_charging": True})
    assert kept["battery_percent"] == 81 and kept["battery_charging"] is True


@pytest.mark.parametrize("bad", [101, -1, True, False, "50", 50.5, float("nan"), float("inf"), None, [50]],
                         ids=["101", "-1", "true", "false", "str", "fraction", "nan", "inf", "none", "list"])
def test_a_percent_that_is_not_a_whole_number_0_to_100_is_dropped(bad):
    assert sh.validate_sample({"rss_kb": 1, "battery_percent": bad})["battery_percent"] is None


@pytest.mark.parametrize("good,want", [(0, 0), (64, 64), (100, 100), (81.0, 81)])
def test_a_whole_percent_is_kept(good, want):
    assert sh.validate_sample({"rss_kb": 1, "battery_percent": good})["battery_percent"] == want


@pytest.mark.parametrize("bad", [1, 0, "true", None])
def test_charging_is_a_bool_or_nothing(bad):
    assert sh.validate_sample({"rss_kb": 1, "battery_charging": bad})["battery_charging"] is None


def test_a_device_without_a_battery_stores_no_battery_keys():
    kept = sh.validate_sample({"rss_kb": 1, "mic_fps": 33.3})
    assert not any(k.startswith("battery") for k in kept)


def test_battery_alone_is_a_sample_worth_keeping():
    assert sh.validate_sample({"seq": 1, "battery_percent": 50}) == {"seq": 1, "battery_percent": 50}


def test_the_journal_line_says_the_battery_only_when_there_is_one():
    pi = FIXTURES["devices"]["pi_2mic_hat"]["health"]["stored"]
    assert "battery" not in sh.format_health_line("kitchen", pi)
    pc = sh.validate_sample({"rss_kb": 1, "battery_percent": 81, "battery_charging": True})
    assert sh.format_health_line("study", pc).endswith(" battery=81% on the charger")
    low = sh.validate_sample({"rss_kb": 1, "battery_percent": 12, "battery_charging": False})
    assert sh.format_health_line("study", low).endswith(" battery=12% on battery")


@pytest.mark.parametrize("device", ["linux_pc", "android_room"])
def test_the_spare_devices_in_the_fixtures_send_the_flat_keys(device):
    frame = FIXTURES["devices"][device]["health"]["frame"]
    assert isinstance(frame["battery_percent"], int) and isinstance(frame["battery_charging"], bool)
    assert "battery" not in frame, "S0's draft object is not what a device sends"
