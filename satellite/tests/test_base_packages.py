"""A satellite puts its missing base packages back at boot (2026-10-17).

The office unit never had mpg123 (the card side of why is pinned in
domovoi/tests/test_satellite_base_packages.py). Pinned here, with a fake
dpkg/apt (the real ones were run in a Debian trixie container when this
was built):

* "present" means dpkg's ``install ok installed`` under any of a package's
  names — an unpacked, unconfigured mpg123 is missing;
* the repair waits for stage 2, does nothing when nothing is missing, waits
  for a route (and gives up for this boot without one), installs exactly
  the missing names with ``--no-install-recommends --no-remove`` and never
  anything that removes, picks the name apt has a candidate for, and
  records what it found and did;
* root's boot step runs it after the network settings; the client reads
  the record, logs it once, and says "reboot needed" while anything is
  missing — only on a unit where a reboot runs that step.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import types
from pathlib import Path

import pytest

from satellite import base_packages, net_settings, provisioning_mode
from satellite.tests._client_import import import_client

client = import_client()

ALL = [n for _, names in base_packages.BASE_PACKAGES for n in names]


class FakeApt:
    """dpkg-query / apt-cache / apt-get / ip, as a dict of installed names."""

    def __init__(self, installed, *, candidates=None, route=True, install_ok=True,
                 unpacked=()):
        self.installed = set(installed)
        self.unpacked = set(unpacked)
        self.candidates = set(ALL) - {"libasound2", "libportaudio2t64"} if candidates is None else set(candidates)
        self.route = route
        self.install_ok = install_ok
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kw):
        argv = list(argv)
        self.calls.append(argv)
        out, rc = "", 0
        if argv[0] == "dpkg-query":
            name = argv[-1]
            if name in self.installed:
                out = "install ok installed"
            elif name in self.unpacked:
                out = "install ok unpacked"
            else:
                rc = 1
        elif argv[:2] == ["apt-cache", "policy"]:
            name = argv[2]
            out = f"{name}:\n  Installed: (none)\n  Candidate: {'1.0-1' if name in self.candidates else '(none)'}\n"
        elif argv[:3] == ["ip", "route", "show"]:
            out = "default via 192.168.1.1 dev wlan0" if self.route else ""
        elif argv[0] == "apt-get" and "install" in argv:
            if self.install_ok:
                self.installed.add(argv[-1])
                self.unpacked.discard(argv[-1])
            else:
                rc, out = 100, "E: Unable to fetch some archives"
        return subprocess.CompletedProcess(argv, rc, out, "")


@pytest.fixture
def done(tmp_path):
    flag = tmp_path / "bootstrap-done"
    flag.write_text("")
    return flag


def _everything_but(*gone):
    present = set()
    for base, names in base_packages.BASE_PACKAGES:
        if base not in gone:
            present.add(names[0])
    return present


def test_present_means_installed_under_any_name():
    apt = FakeApt(_everything_but("libasound2") | {"libasound2t64"})
    assert base_packages.missing(apt) == []
    apt = FakeApt(_everything_but("mpg123"), unpacked={"mpg123"})
    assert base_packages.missing(apt) == ["mpg123"], "unpacked is not installed"


def test_nothing_runs_until_stage_2_is_done(tmp_path):
    apt = FakeApt(set())
    doc = base_packages.repair(run=apt, stage2_done=tmp_path / "absent",
                               report_path=tmp_path / "r.json")
    assert doc["state"] == "deferred" and apt.calls == []
    assert json.loads((tmp_path / "r.json").read_text())["state"] == "deferred"


def test_nothing_missing_nothing_installed(tmp_path, done):
    apt = FakeApt(_everything_but())
    doc = base_packages.repair(run=apt, stage2_done=done, report_path=tmp_path / "r.json")
    assert doc["state"] == "ok"
    assert not any(c[0] in ("apt-get", "ip") for c in apt.calls)


def test_the_office_unit_gets_mpg123_back(tmp_path, done):
    apt = FakeApt(_everything_but("mpg123"), unpacked={"mpg123"})
    doc = base_packages.repair(run=apt, stage2_done=done, report_path=tmp_path / "r.json",
                               sleep=lambda s: None)
    assert doc["state"] == "repaired"
    assert doc["missing"] == ["mpg123"] and doc["installed"] == ["mpg123"]
    apt_calls = [c for c in apt.calls if c[0] == "apt-get"]
    assert apt_calls[0][-2:] == ["update", "-qq"]
    install = apt_calls[1]
    assert install[-5:] == ["install", "-y", "--no-install-recommends", "--no-remove", "mpg123"]
    assert "DPkg::Lock::Timeout=120" in install
    record = json.loads((tmp_path / "r.json").read_text())
    assert record["state"] == "repaired" and record["still_missing"] == []


def test_apt_is_never_asked_to_remove_or_repair(tmp_path, done):
    apt = FakeApt(set())
    base_packages.repair(run=apt, stage2_done=done, report_path=tmp_path / "r.json",
                         sleep=lambda s: None)
    for c in apt.calls:
        if c[0] == "apt-get":
            assert not {"remove", "purge", "autoremove", "-f", "--fix-broken"} & set(c), c
            if "install" in c:
                assert "--no-remove" in c


def test_the_name_apt_has_a_candidate_for_is_installed(tmp_path, done):
    apt = FakeApt(_everything_but("libasound2", "libportaudio2"))
    doc = base_packages.repair(run=apt, stage2_done=done, report_path=tmp_path / "r.json",
                               sleep=lambda s: None)
    assert sorted(doc["installed"]) == ["libasound2t64", "libportaudio2"]
    assert doc["state"] == "repaired"


def test_no_route_gives_up_for_this_boot(tmp_path, done):
    t = [0.0]

    def sleep(s):
        t[0] += s

    apt = FakeApt(_everything_but("mpg123"), route=False)
    doc = base_packages.repair(run=apt, stage2_done=done, report_path=tmp_path / "r.json",
                               sleep=sleep, clock=lambda: t[0], network_wait=60.0)
    assert doc["state"] == "no_network" and doc["still_missing"] == ["mpg123"]
    assert not any(c[0] == "apt-get" for c in apt.calls)
    assert 60.0 <= t[0] <= 70.0


def test_a_failed_install_is_recorded_as_failed(tmp_path, done):
    apt = FakeApt(_everything_but("mpg123"), install_ok=False)
    doc = base_packages.repair(run=apt, stage2_done=done, report_path=tmp_path / "r.json",
                               sleep=lambda s: None)
    assert doc["state"] == "failed" and doc["still_missing"] == ["mpg123"]
    assert any("Unable to fetch" in e for e in doc["errors"])


def test_a_missing_tool_or_a_timeout_never_raises(tmp_path, done):
    def run(argv, **kw):
        if argv[0] == "dpkg-query":
            raise FileNotFoundError("dpkg-query")
        raise subprocess.TimeoutExpired(argv, 1)

    doc = base_packages.repair(run=run, stage2_done=done, report_path=tmp_path / "r.json",
                               sleep=lambda s: None, network_wait=0.0)
    assert doc["state"] == "no_network"


def test_the_package_names_are_constants_not_inputs():
    src = Path(base_packages.__file__).read_text(encoding="utf-8")
    assert "config.toml" not in src and ".domovoi" not in src and "urlopen" not in src


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_the_record_is_0644_and_never_written_through_a_link(tmp_path):
    target = tmp_path / "victim"
    target.write_text("keep")
    report = tmp_path / "base-packages.json"
    report.symlink_to(target)
    assert base_packages.write_report({"state": "ok"}, report)
    assert target.read_text() == "keep"
    assert not report.is_symlink() and (report.stat().st_mode & 0o777) == 0o644


def test_the_reboot_note():
    assert base_packages.reboot_note([], None, "b1") is None
    note = base_packages.reboot_note(["mpg123"], {"boot_id": "b1", "state": "no_network"}, "b1")
    assert note.startswith("system packages missing (mpg123): reboot") and "no network" in note
    assert len(note) <= 200
    other_boot = base_packages.reboot_note(["mpg123"], {"boot_id": "b0", "state": "failed"}, "b1")
    assert "failed" not in other_boot


# ─── who runs it ──────────────────────────────────────────────────────────


def test_the_boot_step_runs_it_after_the_network_settings(monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(provisioning_mode.sys, "platform", "linux")
    monkeypatch.setattr(provisioning_mode, "is_provisioned", lambda: True)
    monkeypatch.setattr(provisioning_mode, "apply_network_settings", lambda *a: order.append("net"))
    monkeypatch.setattr(provisioning_mode, "apply_base_packages", lambda: order.append("packages"))
    monkeypatch.setattr(provisioning_mode, "run", lambda *a, **k: order.append("run") or 0)
    monkeypatch.setattr(net_settings, "read_room_id", lambda *a: "office")
    assert provisioning_mode.main() == 0
    assert order == ["net", "packages", "run"]


def test_off_root_the_boot_hook_does_nothing(monkeypatch):
    monkeypatch.setattr(base_packages, "repair", lambda **k: pytest.fail("ran"))
    assert provisioning_mode.apply_base_packages() is None


def test_the_hook_never_raises(monkeypatch):
    monkeypatch.setattr(provisioning_mode.sys, "platform", "linux")
    monkeypatch.setattr(provisioning_mode.os, "geteuid", lambda: 0, raising=False)

    def boom(**k):
        raise RuntimeError("dpkg exploded")

    monkeypatch.setattr(base_packages, "repair", boom)
    assert provisioning_mode.apply_base_packages() is None


# ─── what the client says ─────────────────────────────────────────────────


def _client(monkeypatch, *, unit="enabled", gone=("mpg123",), report=None):
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace()
    monkeypatch.setattr(client.sys, "platform", "linux")
    monkeypatch.setattr(client.net_settings, "boot_step_unit_state", lambda: unit)
    monkeypatch.setattr(client.net_settings, "_boot_id", lambda: "b1")
    monkeypatch.setattr(client.base_packages, "missing", lambda *a, **k: list(gone))
    monkeypatch.setattr(client.base_packages, "read_report", lambda *a, **k: report)
    return sat


def test_a_missing_package_is_said_once_and_asks_for_a_reboot(monkeypatch, caplog):
    sat = _client(monkeypatch, report={"boot_id": "b1", "ts": 1.0, "state": "no_network",
                                       "still_missing": ["mpg123"], "detail": "no network"})
    with caplog.at_level(logging.INFO, logger="satellite"):
        sat._check_base_packages(0.0)
        sat._check_base_packages(700.0)
    assert sat._base_reboot_needed.startswith("system packages missing (mpg123)")
    assert caplog.text.count("system packages missing: mpg123") == 1
    assert caplog.text.count("base packages (root's boot step): no_network") == 1


def test_it_is_looked_up_every_ten_minutes_not_every_tick(monkeypatch):
    sat = _client(monkeypatch)
    n = [0]
    monkeypatch.setattr(client.base_packages, "missing", lambda *a, **k: n.__setitem__(0, n[0] + 1) or [])
    for t in (0.0, 30.0, 60.0, 599.0, 600.0):
        sat._check_base_packages(t)
    assert n[0] == 2


def test_no_reboot_note_where_no_boot_step_runs(monkeypatch):
    sat = _client(monkeypatch, unit="missing")
    sat._check_base_packages(0.0)
    assert sat._base_reboot_needed is None


def test_a_repair_is_logged(monkeypatch, caplog):
    sat = _client(monkeypatch, gone=(), report={"boot_id": "b1", "ts": 2.0, "state": "repaired",
                                                 "installed": ["mpg123"]})
    with caplog.at_level(logging.INFO, logger="satellite"):
        sat._check_base_packages(0.0)
    assert "base packages (installed as root at boot): mpg123" in caplog.text
    assert sat._base_reboot_needed is None


def test_the_health_sample_carries_the_package_note(tmp_path):
    from satellite.tests.test_health_client import health_sat

    sat = health_sat(tmp_path)
    sat._net_reboot_needed = None
    sat._base_reboot_needed = "system packages missing (mpg123): reboot this satellite"
    assert sat._health_process_facts(100.0)["reboot_needed"].startswith("system packages missing")
    sat._net_reboot_needed = "network settings are applied at boot: reboot this satellite once"
    assert sat._health_process_facts(160.0)["reboot_needed"].startswith("network settings")
