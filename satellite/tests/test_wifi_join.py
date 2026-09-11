"""Joining the customer's Wi-Fi after the setup portal comes down.

Found on hardware, reliably: the first join ALWAYS failed and the second
always worked, same password both times. The join ran the instant the setup
AP was torn down, while NetworkManager's scan cache was empty - it had been
hosting, not scanning - and `nmcli device wifi connect` refuses an SSID it
cannot see. The three retries were back-to-back with no rescan, so all
failed identically, and the failure was reported as "wrong password?" with
nmcli's real reason discarded.
"""

from __future__ import annotations

import subprocess

import pytest

from satellite import provisioning_mode as pm


class FakeNM:
    """A NetworkManager whose scan cache is empty for the first N scans -
    exactly the state the interface is in right after AP mode."""

    def __init__(self, ssid: str, *, visible_after_scans: int = 2, password_ok: bool = True):
        self.ssid = ssid
        self.visible_after = visible_after_scans
        self.password_ok = password_ok
        self.scans = 0
        self.connects = 0
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        joined = " ".join(cmd)
        if "wifi list" in joined:
            self.scans += 1
            visible = self.scans >= self.visible_after
            out = f"{self.ssid}\nNeighbour\n" if visible else "Neighbour\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        if "wifi connect" in joined:
            self.connects += 1
            if self.scans < self.visible_after:
                return subprocess.CompletedProcess(
                    cmd, 10, stdout=b"",
                    stderr=f"Error: No network with SSID '{self.ssid}' found.".encode(),
                )
            if not self.password_ok:
                return subprocess.CompletedProcess(
                    cmd, 4, stdout=b"",
                    stderr=b"Error: Connection activation failed: (7) Secrets were required, but not provided.",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout=b"ok", stderr=b"")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(pm.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    monkeypatch.setattr(pm, "_WIFI_SCAN_POLL_SEC", 0.0)


def test_the_join_waits_for_the_network_to_be_visible():
    """The whole bug: connect used to fire before the first scan."""
    nm = FakeNM("HomeNet", visible_after_scans=2)
    ok, err = pm.apply_wifi("HomeNet", "hunter2", "US", False, 30.0, run=nm)
    assert ok, err
    assert nm.scans >= 2
    # Every scan happened BEFORE the connect.
    first_connect = next(i for i, c in enumerate(nm.calls) if "connect" in c)
    scans_before = sum(1 for c in nm.calls[:first_connect] if "list" in c)
    assert scans_before == nm.scans
    assert nm.connects == 1


def test_a_first_try_that_used_to_fail_now_succeeds():
    """Same fixture the hardware presented: cache empty on the first look.
    Previously: connect immediately, 'No network with SSID found', reported
    as a wrong password. Now: one join, first time."""
    nm = FakeNM("HomeNet", visible_after_scans=1)
    ok, _ = pm.apply_wifi("HomeNet", "hunter2", None, False, 30.0, run=nm)
    assert ok and nm.connects == 1


def test_a_hidden_network_is_not_waited_for():
    """It never appears in a scan, by definition."""
    nm = FakeNM("Secret", visible_after_scans=999)
    ok, _ = pm.apply_wifi("Secret", "hunter2", None, True, 30.0, run=nm)
    assert nm.scans == 0
    assert any("hidden" in c for c in nm.calls)


def test_nmclis_own_reason_is_kept(monkeypatch):
    """Reporting every failure as 'wrong password?' sent people re-typing a
    password that was right. The real reason has to reach the log and the
    portal's error."""
    monkeypatch.setattr(pm, "_WIFI_SCAN_WAIT_SEC", 0.0)
    nm = FakeNM("HomeNet", visible_after_scans=999)   # never visible
    ok, err = pm.apply_wifi("HomeNet", "hunter2", None, False, 30.0, run=nm)
    assert not ok
    assert "No network with SSID" in err
    assert "wrong password" not in err


def test_a_genuinely_wrong_password_still_says_so():
    nm = FakeNM("HomeNet", visible_after_scans=1, password_ok=False)
    ok, err = pm.apply_wifi("HomeNet", "nope", None, False, 30.0, run=nm)
    assert not ok
    assert "Secrets were required" in err


def test_the_psk_never_reaches_the_error_or_the_log(caplog):
    nm = FakeNM("HomeNet", visible_after_scans=1, password_ok=False)
    with caplog.at_level("WARNING"):
        _, err = pm.apply_wifi("HomeNet", "s3cret-pw", None, False, 30.0, run=nm)
    assert "s3cret-pw" not in (err or "")
    assert "s3cret-pw" not in caplog.text


def test_retries_are_paused_not_back_to_back(monkeypatch, tmp_path):
    """Three identical attempts in the same instant fail identically."""
    pauses: list[float] = []
    monkeypatch.setattr(pm.time, "sleep", lambda s: pauses.append(s))
    monkeypatch.setattr(pm, "apply_wifi", lambda *a, **k: (False, "no"))
    monkeypatch.setattr(pm, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(pm, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(pm, "PAIRING_TOKEN_PATH", tmp_path / "pairing_token")
    monkeypatch.setattr(pm, "EXAMPLE_CONFIG", tmp_path / "config.toml.example")
    monkeypatch.setattr(pm, "give_to_satellite_user", lambda p: True)
    (tmp_path / "config.toml.example").write_text(
        "[satellite]\nroom_id = 'x'\n", encoding="utf-8"
    )

    class T:
        def clear_provision(self) -> None:
            pass

    payload = {
        "room_id": "office", "domovoi_url": "ws://x:6370",
        "device_profile": "xvf3800_usb", "pairing_token": "t",
        "wifi": {"ssid": "HomeNet", "psk": "p"},
    }
    ok, _ = pm.apply_provision(
        payload, transport=T(), wifi_attempts=3, wifi_join_timeout=1.0
    )
    assert not ok
    # Two pauses between three attempts, none after the last.
    assert pauses.count(pm._WIFI_RETRY_PAUSE_SEC) == 2
