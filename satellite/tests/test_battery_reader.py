"""A spare laptop reads its battery for the health sample (S1, 2026-10-13).

``health.read_battery`` against fake ``/sys/class/power_supply`` trees: a
Pi (no battery) sends no battery keys at all; a laptop sends
``battery_percent`` and ``battery_charging`` — the same two keys a phone in
room mode sends, which is what the core keeps (domovoi/satellite_health.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from satellite import health
from satellite.tests.test_health_sampler import TOOLS, fake_tools, make_proc


def supply(root: Path, name: str, **attrs: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    for key, value in attrs.items():
        (d / key).write_text(value + "\n")


def test_no_power_supply_class_means_no_battery(tmp_path):
    assert health.read_battery(tmp_path / "nothing") == {}


def test_a_pi_with_only_a_mains_supply_has_no_battery(tmp_path):
    supply(tmp_path, "rpi-power", type="Mains", online="1")
    assert health.read_battery(tmp_path) == {}


def test_a_laptop_charging(tmp_path):
    supply(tmp_path, "AC", type="Mains", online="1")
    supply(tmp_path, "BAT0", type="Battery", capacity="57", status="Charging")
    assert health.read_battery(tmp_path) == {"battery_percent": 57, "battery_charging": True}


def test_a_laptop_on_battery(tmp_path):
    supply(tmp_path, "AC", type="Mains", online="0")
    supply(tmp_path, "BAT0", type="Battery", capacity="31", status="Discharging")
    assert health.read_battery(tmp_path) == {"battery_percent": 31, "battery_charging": False}


@pytest.mark.parametrize("status", ["Not charging", "Full"])
def test_held_at_a_charge_limit_or_full_on_the_charger_counts_as_charging(tmp_path, status):
    supply(tmp_path, "ADP1", type="Mains", online="1")
    supply(tmp_path, "BAT1", type="Battery", capacity="80", status=status)
    assert health.read_battery(tmp_path) == {"battery_percent": 80, "battery_charging": True}


def test_usb_c_power_counts_as_the_charger(tmp_path):
    supply(tmp_path, "ucsi-source-psy-USBC000-001", type="USB", online="1")
    supply(tmp_path, "BAT0", type="Battery", capacity="80", status="Not charging")
    assert health.read_battery(tmp_path)["battery_charging"] is True


def test_unknown_status_and_no_mains_entry_leaves_charging_out(tmp_path):
    supply(tmp_path, "BAT0", type="Battery", capacity="66", status="Unknown")
    assert health.read_battery(tmp_path) == {"battery_percent": 66}


def test_a_mouse_s_battery_is_not_the_laptop_s(tmp_path):
    supply(tmp_path, "hidpp_battery_0", type="Battery", scope="Device", capacity="5", status="Discharging")
    assert health.read_battery(tmp_path) == {}
    supply(tmp_path, "BAT0", type="Battery", scope="System", capacity="90", status="Charging")
    assert health.read_battery(tmp_path) == {"battery_percent": 90, "battery_charging": True}


def test_two_packs_report_their_mean(tmp_path):
    supply(tmp_path, "BAT0", type="Battery", capacity="90", status="Discharging")
    supply(tmp_path, "BAT1", type="Battery", capacity="41", status="Discharging")
    assert health.read_battery(tmp_path)["battery_percent"] == 66


@pytest.mark.parametrize("capacity", ["", "abc", "101", "-3"])
def test_a_capacity_that_is_not_0_to_100_is_ignored(tmp_path, capacity):
    supply(tmp_path, "BAT0", type="Battery", capacity=capacity, status="Charging")
    assert health.read_battery(tmp_path) == {}


def test_the_sample_carries_the_battery_only_when_there_is_one(tmp_path):
    proc, sysfs = make_proc(tmp_path)
    sampler = health.Sampler(proc_root=str(proc), sys_root=str(sysfs), run=fake_tools(TOOLS),
                             connect=lambda *a, **k: None, ping=None)
    doc = sampler.sample({}, probe_core=False)
    assert not any(k.startswith("battery") for k in doc), "a Pi's sample is unchanged"
    supply(sysfs / "class" / "power_supply", "BAT0", type="Battery", capacity="81", status="Charging")
    doc = sampler.sample({}, probe_core=False)
    assert doc["battery_percent"] == 81 and doc["battery_charging"] is True
