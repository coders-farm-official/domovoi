"""The link recovery picks a way that works on the room's network stack
(Mac test sitting, 2026-11-16, S2-15).

The Pi's every recovery ran ``wpa_cli -i wlan0 reassociate``, which answered
"Failed to connect to non-global ctrl_ifname: wlan0": its supplicant had no
control socket for wlan0 (NetworkManager drives its own over D-Bus), so only
the 16-minute watchdog reboot brought the link back. Pinned here:

* NetworkManager that answers is used, as before;
* wpa_cli runs only where a wpa_supplicant control socket for wlan0 exists
  (or the folder can't be read: then as before);
* NetworkManager that is on the image but doesn't answer never gets
  wpa_cli: the radio reset is tried, and only on a link that is down;
* any other stack gets nothing but a log line: the self-check's restart;
* why NetworkManager said no is logged once, not every round.
"""

from __future__ import annotations

import logging
import subprocess

from satellite.tests._client_import import import_client
from satellite.tests.test_health_client import health_sat

client = import_client()


def _nmcli(monkeypatch, *, rc=0, out="", exc=None):
    calls: list[list[str]] = []

    def run(argv, **kw):
        calls.append(list(argv))
        if argv[0] == "nmcli":
            if exc is not None:
                raise exc
            return subprocess.CompletedProcess(argv, rc, out, "Error: NetworkManager is not running." if rc else "")
        if "wpa_cli" in " ".join(argv):
            return subprocess.CompletedProcess(argv, 255, "", "Failed to connect to non-global ctrl_ifname: wlan0")
        raise AssertionError(f"unexpected {argv}")

    monkeypatch.setattr(client.subprocess, "run", run)
    return calls


def test_networkmanager_that_answers_is_used(tmp_path, monkeypatch):
    sat = health_sat(tmp_path)
    _nmcli(monkeypatch, out="wlan0:disconnected\nlo:unmanaged\n")
    assert sat._wifi_stack() == "nm"


def test_a_wpa_supplicant_with_its_socket_gets_wpa_cli(tmp_path, monkeypatch):
    sat = health_sat(tmp_path)
    _nmcli(monkeypatch, exc=FileNotFoundError("nmcli"))
    ctrl = tmp_path / "wpa"
    ctrl.mkdir()
    (ctrl / "wlan0").write_text("")
    monkeypatch.setattr(client, "WPA_CTRL_DIRS", (ctrl,))
    assert sat._wifi_stack() == "wpa_supplicant"


def test_the_sitting_pi_never_runs_wpa_cli(tmp_path, monkeypatch, caplog):
    # NetworkManager on the image, not answering; no control socket.
    sat = health_sat(tmp_path)
    calls = _nmcli(monkeypatch, rc=8)
    monkeypatch.setattr(client, "WPA_CTRL_DIRS", (tmp_path / "no-wpa",))
    monkeypatch.setattr(client, "_nm_installed", lambda *a: True)
    sat.cfg.wifi_enabled = True
    sat._check_wifi_link = lambda: {"ssid": None, "rx_mbits": None}
    reset: list[tuple] = []
    sat._wifi_reset_radio = lambda why, associated, ap_active: reset.append((associated, ap_active)) or False
    with caplog.at_level(logging.WARNING, logger="satellite"):
        assert sat._reassociate_wifi() is False
        assert sat._reassociate_wifi() is False
    assert not any("wpa_cli" in " ".join(c) for c in calls)
    assert reset == [(False, False), (False, False)]
    assert caplog.text.count("NetworkManager runs this room's network but did not answer") == 1
    assert "NetworkManager is not running" in caplog.text


def test_a_working_link_is_never_dropped(tmp_path, monkeypatch):
    # The radio reset itself refuses an associated link; it is told so.
    sat = health_sat(tmp_path)
    _nmcli(monkeypatch, exc=subprocess.TimeoutExpired("nmcli", 12))
    monkeypatch.setattr(client, "WPA_CTRL_DIRS", (tmp_path / "no-wpa",))
    monkeypatch.setattr(client, "_nm_installed", lambda *a: True)
    sat.cfg.wifi_enabled = True
    sat._check_wifi_link = lambda: {"ssid": "home", "rx_mbits": 72.2}
    monkeypatch.setattr(client.health, "nm_connected", lambda *a: None)
    monkeypatch.setattr(client.health, "read_nm_state", lambda *a: None)
    monkeypatch.setattr(client, "_wifi_reset_helper_installed", lambda cfg: True)
    ran: list = []
    real = client.subprocess.run

    def run(argv, **kw):
        if "domovoi-wifi-reset" in " ".join(map(str, argv)):
            ran.append(argv)
        return real(argv, **kw)

    monkeypatch.setattr(client.subprocess, "run", run)
    assert sat._reassociate_wifi() is False
    assert ran == []


def test_another_stack_gets_no_tool_that_cannot_work(tmp_path, monkeypatch, caplog):
    sat = health_sat(tmp_path)
    calls = _nmcli(monkeypatch, exc=FileNotFoundError("nmcli"))
    monkeypatch.setattr(client, "WPA_CTRL_DIRS", (tmp_path / "no-wpa",))
    monkeypatch.setattr(client, "_nm_installed", lambda *a: False)
    sat._wifi_reset_radio = lambda *a: (_ for _ in ()).throw(AssertionError("reset"))
    with caplog.at_level(logging.WARNING, logger="satellite"):
        assert sat._reassociate_wifi() is False
    assert sat._wifi_stack() == "other"
    assert not any("wpa_cli" in " ".join(c) for c in calls)
    assert "nothing here can reconnect it" in caplog.text


def test_an_unreadable_socket_folder_keeps_the_old_wpa_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(client, "_wpa_ctrl_socket", lambda iface, dirs=None: None)
    sat = health_sat(tmp_path)
    calls = _nmcli(monkeypatch, exc=FileNotFoundError("nmcli"))
    assert sat._wifi_stack() == "unknown"
    sat._reassociate_wifi()
    assert any("wpa_cli" in " ".join(c) for c in calls)


def test_a_slow_nmcli_is_given_time():
    assert client.NMCLI_STATUS_TIMEOUT_SEC >= 10.0


def test_why_networkmanager_said_no_is_kept(tmp_path, monkeypatch):
    sat = health_sat(tmp_path)
    _nmcli(monkeypatch, out="wlan0:unmanaged\n")
    assert sat._nm_manages_wifi() is False
    assert sat._nm_probe_note == "NetworkManager lists wlan0 as unmanaged"
