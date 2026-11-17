"""domovoi/vm_guard.py: Health's "the VM fence isn't loaded" input.

Null on a box provisioned without --vm-guard (nothing is shown there), and
the unit's state on one provisioned with it.
"""

from __future__ import annotations

import subprocess

import pytest

from domovoi import vm_guard


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(vm_guard.sys, "platform", "linux")


def test_a_box_without_the_option_reports_nothing(tmp_path, monkeypatch, linux):
    monkeypatch.setattr(vm_guard, "RULES_FILE", tmp_path / "vm-guard.nft")
    called = []
    monkeypatch.setattr(vm_guard.subprocess, "run", lambda *a, **k: called.append(a))
    assert vm_guard.status() is None
    assert called == [], "a box without --vm-guard must not even ask systemd"


def test_not_linux_reports_nothing(tmp_path, monkeypatch):
    rules = tmp_path / "vm-guard.nft"
    rules.write_text("table inet x\n")
    monkeypatch.setattr(vm_guard, "RULES_FILE", rules)
    monkeypatch.setattr(vm_guard.sys, "platform", "win32")
    assert vm_guard.status() is None


@pytest.mark.parametrize("rc, loaded", [(0, True), (3, False), (4, False)])
def test_a_fenced_box_reports_the_units_state(tmp_path, monkeypatch, linux, rc, loaded):
    rules = tmp_path / "vm-guard.nft"
    rules.write_text("table inet x\n")
    monkeypatch.setattr(vm_guard, "RULES_FILE", rules)
    seen = []

    def fake(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, rc)

    monkeypatch.setattr(vm_guard.subprocess, "run", fake)
    assert vm_guard.status() == {"loaded": loaded, "unit": "domovoi-vm-guard.service"}
    assert seen == [["systemctl", "is-active", "--quiet", "domovoi-vm-guard.service"]]


def test_no_systemd_is_unknown_not_a_warning(tmp_path, monkeypatch, linux):
    rules = tmp_path / "vm-guard.nft"
    rules.write_text("table inet x\n")
    monkeypatch.setattr(vm_guard, "RULES_FILE", rules)

    def boom(*a, **k):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(vm_guard.subprocess, "run", boom)
    assert vm_guard.status()["loaded"] is None
