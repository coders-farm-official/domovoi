"""A computer set up from a Domovoi room stick reports its Secure Boot
state in its health sample (satellite/client.py ``_stick_boot_facts``,
2026-10-16): what root's per-boot check wrote to /run/domovoi-stick/
boot.json, firmware and booleans only; nothing on any other satellite.
"""

from __future__ import annotations

import json

from satellite import client


def test_the_satellite_reports_a_stick_computer_s_secure_boot_state(tmp_path, monkeypatch):
    f = tmp_path / "boot.json"
    f.write_text(json.dumps({"firmware": "uefi", "secure_boot": True, "ca_2023": False, "ca_2011": True,
                             "loader_held": True, "stick": True, "extra": "dropped"}), encoding="utf-8")
    monkeypatch.setattr(client.sys, "platform", "linux")
    assert client._stick_boot_facts(f) == {"firmware": "uefi", "secure_boot": True, "ca_2023": False,
                                           "ca_2011": True, "loader_held": True, "stick": True}
    assert client._stick_boot_facts(tmp_path / "none.json") is None, "every other satellite sends nothing"
    f.write_text("not json", encoding="utf-8")
    assert client._stick_boot_facts(f) is None
    f.write_text(json.dumps({"firmware": "uefi", "secure_boot": "yes"}), encoding="utf-8")
    assert client._stick_boot_facts(f) == {"firmware": "uefi"}
    monkeypatch.setattr(client.sys, "platform", "win32")
    assert client._stick_boot_facts(f) is None
