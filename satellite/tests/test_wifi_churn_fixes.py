"""The 2026-10-12 Wi-Fi churn fixes, as the client sees them.

design-notes/SAT-WIFI-FIX-BRIEF.md: the router kept registering the
satellites as new devices and they kept dropping off the server. What is
pinned here (the watcher's reachability rule is in test_wifi_watcher.py,
the recovery table in test_wifi_profiles.py, root's boot-time step in
test_net_settings.py, the server search order in test_discovery.py):

* the real signal strength (dBm) is read and reported, the rates kept;
* a radio hosting the setup hotspot is a self-check that fires;
* the recovery runs the plan through sudo, falling back only on sudo's own
  refusal, and runs nothing when the plan says so;
* what root changed at boot reaches this process's log (and so the core's
  retained log), and an upgraded-but-not-rebooted unit says so;
* a satellite follows its core to a new address, persisting it.
"""

from __future__ import annotations

import json
import logging
import subprocess
import types

import pytest

from satellite import discovery, health, net_settings, server_identity, wifi_profiles
from satellite.tests._client_import import import_client
from satellite.tests.test_server_identity import _health_opener, _server

client = import_client()


# ─── the link reading ─────────────────────────────────────────────────────

IW_LINK = """Connected to 9c:53:22:aa:bb:cc (on wlan0)
	SSID: Kamber Wifi 2.0
	freq: 2437
	RX: 1234567 bytes (8910 packets)
	TX: 234567 bytes (1234 packets)
	signal: -67 dBm
	rx bitrate: 1.0 MBit/s
	tx bitrate: 72.2 MBit/s MCS 7 short GI

	bss flags:	short-slot-time
	dtim period:	1
	beacon int:	100
"""


def test_the_signal_is_read_alongside_the_rates():
    link = client._parse_iw_link(IW_LINK)
    assert link == {"rx_mbits": 1.0, "tx_mbits": 72.2, "ssid": "Kamber Wifi 2.0",
                    "signal_dbm": -67, "mode": "managed"}


def test_a_link_without_a_signal_line_reports_none():
    link = client._parse_iw_link(IW_LINK.replace("\tsignal: -67 dBm\n", ""))
    assert link["signal_dbm"] is None and link["rx_mbits"] == 1.0


@pytest.mark.parametrize("line", ["\tsignal: 12 dBm\n", "\tsignal: -300 dBm\n"])
def test_an_impossible_signal_is_not_reported(line):
    link = client._parse_iw_link(IW_LINK.replace("\tsignal: -67 dBm\n", line))
    assert link["signal_dbm"] is None


def test_not_connected_asks_whether_the_radio_is_hosting():
    link = client._parse_iw_link("Not connected.\n", lambda: "ap")
    assert link["mode"] == "ap" and link["rx_mbits"] is None
    # A plain client with no association just has no numbers.
    assert "mode" not in client._parse_iw_link("Not connected.\n", lambda: None)


def test_the_wifi_status_frame_carries_signal_and_mode():
    sat = object.__new__(client.Satellite)
    sent = []
    sat._emit_text = sent.append
    sat._emit_wifi_status(client._parse_iw_link(IW_LINK))
    assert sent[0]["type"] == "wifi_status"
    assert sent[0]["signal_dbm"] == -67 and sent[0]["mode"] == "managed"
    assert sat._last_wifi_link["signal_dbm"] == -67, "the health sample's copy too"


# ─── the hotspot is a self-check ──────────────────────────────────────────


def _ci(**kw):
    base = dict(now=1000.0, mic_expected=False, idle=True)
    base.update(kw)
    return health.CheckInput(**base)


def test_a_radio_hosting_the_hotspot_is_recovered_after_five_minutes():
    """NetworkManager calls an AP "connected" and there is no gateway to be
    silent: before, nothing ever fired for it."""
    ci = _ci(no_server_for=300.0, nm_connected=True, gateway_ok=None, default_route=False,
             core_host_ok=False, ap_mode=True)
    assert [d.action for d in health.decide(ci)] == ["wifi_recover"]
    assert "setup hotspot" in health.decide(ci)[0].reason
    # The same facts without the hotspot fire nothing (NM says connected).
    assert health.decide(_ci(no_server_for=300.0, nm_connected=True, gateway_ok=None,
                             default_route=False, core_host_ok=False)) == []


def test_a_radio_hosting_the_hotspot_for_fifteen_minutes_reboots():
    """The reboot runs the boot-time step, which deletes the hotspot profile
    on a unit that was upgraded and never rebooted."""
    ci = _ci(no_server_for=900.0, nm_connected=True, gateway_ok=None, default_route=False,
             core_host_ok=False, ap_mode=True, reboot_helper=True)
    assert [d.action for d in health.decide(ci)] == ["reboot", "wifi_recover"]


def test_the_health_check_reads_the_hotspot_from_the_last_link():
    import inspect

    src = inspect.getsource(client.Satellite._health_checks)
    assert 'ap_mode=(self._last_wifi_link or {}).get("mode") == "ap"' in src


# ─── the recovery runs the plan, through sudo ─────────────────────────────


class Runner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        rc, err = self.results.pop(0)
        return subprocess.CompletedProcess(argv, rc, "", err)


def _recovering_sat(monkeypatch, plan, results):
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(wifi_enabled=False)
    monkeypatch.setattr(client.wifi_profiles, "list_wifi_profiles", lambda *a, **k: [])
    monkeypatch.setattr(client.wifi_profiles, "visible_ssids", lambda *a, **k: set())
    monkeypatch.setattr(client.wifi_profiles, "plan_recovery", lambda *a, **k: plan)
    runner = Runner(results)
    monkeypatch.setattr(client.subprocess, "run", runner)
    return sat, runner


def _plan(*argvs):
    return wifi_profiles.Plan(steps=[wifi_profiles.Step(" ".join(a[1:]), list(a)) for a in argvs],
                              reason="test")


def test_the_home_profile_by_name_is_tried_first(monkeypatch):
    plan = _plan(wifi_profiles.connection_up_argv(), wifi_profiles.device_connect_argv())
    sat, runner = _recovering_sat(monkeypatch, plan, [(0, "")])
    assert sat._reconnect_wifi_nm() is True
    assert runner.calls == [["sudo", "-n", *wifi_profiles.connection_up_argv()]]


def test_an_older_sudoers_falls_back_to_the_next_allowed_step(monkeypatch):
    plan = _plan(wifi_profiles.connection_up_argv(), wifi_profiles.device_connect_argv())
    sat, runner = _recovering_sat(monkeypatch, plan, [
        (1, "sudo: a password is required\n"), (0, ""),
    ])
    assert sat._reconnect_wifi_nm() is True
    assert runner.calls[1] == ["sudo", "-n", *wifi_profiles.device_connect_argv()]


def test_an_nmcli_failure_is_not_followed_by_a_guess(monkeypatch):
    """The home profile could not come up (the house network is not
    there): `device connect` after that is exactly the guess that found the
    hotspot."""
    plan = _plan(wifi_profiles.connection_up_argv(), wifi_profiles.device_connect_argv())
    sat, runner = _recovering_sat(monkeypatch, plan, [
        (4, "Error: Connection activation failed: No network with SSID 'HomeNet' found."),
    ])
    assert sat._reconnect_wifi_nm() is False
    assert len(runner.calls) == 1


def test_an_empty_plan_runs_nothing(monkeypatch, caplog):
    plan = wifi_profiles.Plan(reason="the setup hotspot profile is still on this unit")
    sat, runner = _recovering_sat(monkeypatch, plan, [])
    with caplog.at_level(logging.WARNING):
        assert sat._reconnect_wifi_nm() is False
    assert runner.calls == []
    assert "setup hotspot profile is still on this unit" in caplog.text


# ─── what root did at boot reaches the core's log ─────────────────────────


def _report(tmp_path, **kw):
    doc = {"version": net_settings.NET_SETTINGS_VERSION, "ts": 1760000000.5, "boot_id": "b1",
           "when": "boot", "hostname": "domovoi-kitchen",
           "changed": ["host name 'raspberrypi' -> 'domovoi-kitchen'"], "skipped": [], "errors": []}
    doc.update(kw)
    net_settings.REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    net_settings.REPORT_PATH.write_text(json.dumps(doc), encoding="utf-8")
    return doc


def test_root_s_changes_are_logged_once(tmp_path, caplog, monkeypatch):
    _report(tmp_path, errors=["delete the setup hotspot profile: not authorized"])
    sat = object.__new__(client.Satellite)
    with caplog.at_level(logging.INFO, logger="satellite"):
        sat._check_net_settings()
    assert "network settings (applied as root at boot): changed: host name 'raspberrypi' -> 'domovoi-kitchen'" in caplog.text
    assert "could not: delete the setup hotspot profile" in caplog.text
    caplog.clear()
    # The next process (a restart) does not log the same run again.
    again = object.__new__(client.Satellite)
    with caplog.at_level(logging.INFO, logger="satellite"):
        again._check_net_settings()
    assert "changed:" not in caplog.text


def test_upgraded_but_not_rebooted_says_reboot_needed(monkeypatch, caplog):
    """The upgrade restarts the client only; root's boot-time step runs at
    the next boot. Until then the health sample carries the reason."""
    monkeypatch.setattr(client.sys, "platform", "linux")
    monkeypatch.setattr(net_settings, "PROVISIONING_UNIT", types.SimpleNamespace(exists=lambda: True))
    sat = object.__new__(client.Satellite)
    with caplog.at_level(logging.WARNING, logger="satellite"):
        sat._check_net_settings()
    assert sat._net_reboot_needed and "reboot" in sat._net_reboot_needed
    assert "reboot needed" in caplog.text


def test_after_the_reboot_nothing_is_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(client.sys, "platform", "linux")
    monkeypatch.setattr(net_settings, "PROVISIONING_UNIT", types.SimpleNamespace(exists=lambda: True))
    _report(tmp_path, changed=[])
    sat = object.__new__(client.Satellite)
    sat._check_net_settings()
    assert sat._net_reboot_needed is None


def test_a_unit_without_the_boot_step_is_never_told_to_reboot(monkeypatch):
    monkeypatch.setattr(client.sys, "platform", "linux")
    monkeypatch.setattr(net_settings, "PROVISIONING_UNIT", types.SimpleNamespace(exists=lambda: False))
    sat = object.__new__(client.Satellite)
    sat._check_net_settings()
    assert sat._net_reboot_needed is None


def test_the_health_sample_carries_reboot_needed():
    import inspect

    src = inspect.getsource(client.Satellite._health_process_facts)
    assert '"reboot_needed": self._net_reboot_needed' in src


# ─── following the core to a new address ──────────────────────────────────

OURS = _server(1)


@pytest.fixture
def sidecars(tmp_path, monkeypatch):
    monkeypatch.setattr(server_identity, "RECORD_SIDECAR", tmp_path / "recorded.json")
    monkeypatch.setattr(server_identity, "ROOT_PIN", tmp_path / "etc-pin.json")
    monkeypatch.setattr(server_identity, "PENDING_SERVER_SIDECAR", tmp_path / "pending.json")
    cfg = tmp_path / "config.toml"
    cfg.write_text('[satellite]\n# ours\ndomovoi_url = "ws://192.168.0.117:6370"\nroom_id = "kitchen"\n',
                   encoding="utf-8")
    return cfg


_REAL_PROBE = discovery.probe


def _lan_probe(owners):
    """The real probe (identity check and all) over a pretend LAN."""
    def probe(host, *, port=6370, timeout=None, expected_fingerprint=None):
        if host not in owners:
            return False
        seed, public, _ = owners[host]
        return _REAL_PROBE(host, port=port, opener=_health_opener(seed, public),
                           expected_fingerprint=expected_fingerprint)
    return probe


def test_a_paired_satellite_follows_its_core_by_name_and_saves_it(sidecars, monkeypatch):
    monkeypatch.setattr(discovery, "_DEFAULT_RESOLVER",
                        lambda name, *a, **k: [(2, 1, 6, "", ("192.168.0.50", 6370))])
    monkeypatch.setattr(discovery, "probe", _lan_probe({"192.168.0.50": OURS}))
    monkeypatch.setattr(discovery, "find_core", lambda **k: pytest.fail("swept"))
    cfg = types.SimpleNamespace(domovoi_url="ws://192.168.0.117:6370", server_fingerprint=OURS[2],
                                room_id="kitchen")
    assert client._resolve_server_url(cfg, sidecars) is True
    assert cfg.domovoi_url == "ws://192.168.0.50:6370"
    body = sidecars.read_text(encoding="utf-8")
    assert 'domovoi_url = "ws://192.168.0.50:6370"' in body
    assert "# ours" in body, "comments survive"


def test_an_impostor_answering_the_name_changes_nothing(sidecars, monkeypatch):
    theirs = _server(2)
    monkeypatch.setattr(discovery, "_DEFAULT_RESOLVER",
                        lambda name, *a, **k: [(2, 1, 6, "", ("192.168.0.66", 6370))])
    monkeypatch.setattr(discovery, "probe", _lan_probe({"192.168.0.66": theirs}))
    monkeypatch.setattr(discovery, "find_core", lambda **k: None)
    before = sidecars.read_text(encoding="utf-8")
    cfg = types.SimpleNamespace(domovoi_url="ws://192.168.0.117:6370", server_fingerprint=OURS[2],
                                room_id="kitchen")
    assert client._resolve_server_url(cfg, sidecars) is True
    assert cfg.domovoi_url == "ws://192.168.0.117:6370"
    assert sidecars.read_text(encoding="utf-8") == before


def test_a_core_that_is_merely_down_is_not_searched_for(monkeypatch):
    """Its machine refuses at the old address: the server is there, just
    not listening (a restart). No name lookups, no sweep."""
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(domovoi_url="ws://192.168.0.117:6370", server_fingerprint=OURS[2])
    monkeypatch.setattr(client.health, "probe_service", lambda *a, **k: "refused")
    monkeypatch.setattr(client, "_locate_server", lambda cfg: pytest.fail("searched"))
    sat._relocate_server()


def test_the_connect_loop_searches_again_when_the_core_stays_dark():
    import inspect

    src = inspect.getsource(client.Satellite.run)
    assert "unreachable >= _RELOCATE_AFTER" in src
    assert "self._relocate_server" in src
    assert client._RELOCATE_EVERY_SEC >= 300


def test_the_names_the_core_advertises_are_kept():
    import inspect

    src = inspect.getsource(client.Satellite._handle_text_frame)
    assert 'discovery.remember_names(payload.get("server_names"))' in src
