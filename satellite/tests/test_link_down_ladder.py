"""The link-down recovery ladder (2026-10-17).

The office satellite (a Pi Zero 2 W at -75..-77 dBm) lost the house network
outright several times: NetworkManager not connected, no default route,
"Network is unreachable". The self-check waited 5 minutes (6 with the
two-sample rule) before its first `nmcli connection up id domovoi-home`,
which timed out after 30 s; the next try came 6 minutes later and timed out
again; the reboot that fixed it came at 16 minutes. What is pinned here:

* a link that is DOWN (not a core that is down) is recovered after 2
  minutes, retried after 2.5, and rebooted after 10 once two recoveries
  failed — and none of it fires while the gateway or the core's own machine
  answers (the "core_up" guard is unchanged);
* each recent self-check reboot doubles the next reboot's wait (capped), so
  a flapping link cannot reboot the unit in a loop; the history behind that
  survives the reboot;
* a `connection up` that times out (or fails) on a link that is down is
  followed by the radio-reset helper where the unit has it, and by nothing
  where it does not or where the radio is still associated;
* the client counts failed recoveries per outage and starts over when the
  server is back.
"""

from __future__ import annotations

import json
import logging
import subprocess
import types

import pytest

from satellite import health, wifi_profiles
from satellite.health import CheckInput, Decision, decide
from satellite.tests._client_import import import_client
from satellite.tests.test_health_client import health_sat, sample

client = import_client()

DOWN = dict(nm_connected=False, default_route=False, gateway_ok=None)


def _ci(**kw) -> CheckInput:
    base = dict(now=10_000.0, mic_expected=False, idle=True, rss_mb=100.0, fds=30)
    base.update(kw)
    return CheckInput(**base)


def acts(**kw) -> list[str]:
    return [d.action for d in decide(_ci(**kw))]


@pytest.mark.parametrize("label, kw, expected", [
    ("link down 1.5 min: wait", dict(no_server_for=90.0, **DOWN), []),
    ("link down 2 min: first recovery", dict(no_server_for=120.0, **DOWN), ["wifi_recover"]),
    ("link up, gateway silent 2 min: still the 5-minute rule",
     dict(no_server_for=120.0, nm_connected=True, default_route=True, gateway_ok=False), []),
    ("NM disconnected but a route exists 2 min: not 'down', 5-minute rule",
     dict(no_server_for=120.0, nm_connected=False, default_route=True, gateway_ok=None), []),
    ("link down but the gateway answers: never touch it",
     dict(no_server_for=900.0, nm_connected=False, default_route=False, gateway_ok=True,
          reboot_helper=True, recovery_failures=5), []),
    ("link down but the core's machine refuses (core_up path): never touch it",
     dict(no_server_for=900.0, core_host_ok=True, reboot_helper=True, recovery_failures=5,
          **DOWN), []),
    ("one failure, 100 s ago: wait", dict(no_server_for=300.0, recovery_failures=1,
                                          last_wifi_recovery=9_900.0, **DOWN), []),
    ("one failure, 150 s ago: retry", dict(no_server_for=300.0, recovery_failures=1,
                                           last_wifi_recovery=9_850.0, **DOWN), ["wifi_recover"]),
    ("no failure yet, last try 150 s ago: the usual 5-minute cooldown",
     dict(no_server_for=300.0, last_wifi_recovery=9_850.0, **DOWN), []),
    ("gateway silent with the link up, a failure 150 s ago: the usual cooldown",
     dict(no_server_for=400.0, nm_connected=True, gateway_ok=False, recovery_failures=1,
          last_wifi_recovery=9_850.0), []),
    ("10 min, two failures, helper: reboot",
     dict(no_server_for=600.0, recovery_failures=2, last_wifi_recovery=9_950.0,
          reboot_helper=True, **DOWN), ["reboot"]),
    ("10 min, one failure: no reboot yet",
     dict(no_server_for=600.0, recovery_failures=1, last_wifi_recovery=9_950.0,
          reboot_helper=True, **DOWN), []),
    ("10 min, two failures, no helper: say so",
     dict(no_server_for=600.0, recovery_failures=2, last_wifi_recovery=9_950.0, **DOWN),
     ["reboot_needed"]),
    ("10 min, two failures, a reboot an hour ago: held to 20 min",
     dict(no_server_for=600.0, recovery_failures=2, last_wifi_recovery=9_950.0,
          reboot_helper=True, recent_reboots=1, **DOWN), []),
    ("20 min, two failures, one recent reboot: reboot",
     dict(no_server_for=1200.0, recovery_failures=2, last_wifi_recovery=9_950.0,
          reboot_helper=True, recent_reboots=1, **DOWN), ["reboot"]),
    ("the old 15-minute rule, one recent reboot: held to 30 min",
     dict(no_server_for=900.0, gateway_ok=False, last_wifi_recovery=9_950.0,
          reboot_helper=True, recent_reboots=1), []),
])
def test_the_ladder(label, kw, expected):
    assert acts(**kw) == expected, label


def test_the_backoff_is_capped_and_never_below_the_base():
    ci = _ci(no_server_for=0.0, recovery_failures=2, recent_reboots=9, **DOWN)
    at, why = health.reboot_threshold(ci)
    assert at == 60 * 60.0 and "9 self-check reboot" in why
    assert health.reboot_threshold(_ci(**DOWN))[0] == 15 * 60.0
    # A configured cap below the base never pulls the reboot earlier.
    low = _ci(reboot_after=30 * 60.0, reboot_backoff_cap=10 * 60.0, recent_reboots=1)
    assert health.reboot_threshold(low)[0] == 30 * 60.0


def test_the_ap_mode_case_is_link_down_too():
    assert acts(no_server_for=120.0, ap_mode=True, nm_connected=True) == ["wifi_recover"]


def test_reasons_say_how_many_tries_failed_and_why_the_reboot_came_early():
    ds = decide(_ci(no_server_for=600.0, recovery_failures=2, last_wifi_recovery=9_000.0,
                    reboot_helper=True, **DOWN))
    by = {d.action: d.reason for d in ds}
    assert "2 recovery attempt(s) failed" in by["wifi_recover"]
    assert "after 2 failed link recoveries" in by["reboot"]


def _office_outage(recover_ok_at: float | None, *, helper: bool = True,
                   recent_reboots: int = 0, **knobs) -> list[tuple[float, str]]:
    """The client's loop, off a Pi: a check every 30 s, a sample every 60 s,
    the two-observation filter, and recoveries that fail (each takes the
    45 s the nmcli timeout allows) until ``recover_ok_at``."""
    p = health.Persistence(2)
    last_recovery = None
    failures = 0
    acted: list[tuple[float, str]] = []
    t = 0.0
    seq = 0
    while t <= 30 * 60.0:
        if int(t) % 60 == 0:
            seq += 1
        ci = _ci(now=t, no_server_for=t, last_wifi_recovery=last_recovery,
                 recovery_failures=failures, reboot_helper=helper,
                 recent_reboots=recent_reboots, **DOWN, **knobs)
        for d in p.filter(decide(ci), seq):
            acted.append((t, d.action))
            p.forget(d.check)
            if d.action == "wifi_recover":
                last_recovery = t
                ok = recover_ok_at is not None and t >= recover_ok_at
                failures = 0 if ok else failures + 1
                if ok:
                    return acted
            if d.action == "reboot":
                return acted
        t += 30.0
    return acted


def test_the_office_outage_now_reboots_in_about_eleven_minutes_not_sixteen():
    acted = _office_outage(None)
    recovers = [t for t, a in acted if a == "wifi_recover"]
    reboot = [t for t, a in acted if a == "reboot"]
    assert recovers[0] <= 3.5 * 60, acted
    assert len(recovers) >= 2 and recovers[1] - recovers[0] <= 4 * 60, acted
    assert reboot and reboot[0] <= 11.5 * 60, acted
    # The same outage with the pre-2026-10-17 numbers: first try at 6 min,
    # the reboot at 16 - what the office unit's log shows.
    old = _office_outage(None, link_down_escalate_after=300.0, link_down_retry=300.0,
                         link_down_reboot_after=900.0)
    assert [t for t, a in old if a == "wifi_recover"][0] == 6 * 60
    assert [t for t, a in old if a == "reboot"][0] == 16 * 60


def test_a_link_that_comes_back_on_the_second_try_is_not_rebooted():
    acted = _office_outage(recover_ok_at=5 * 60)
    assert [a for _, a in acted] == ["wifi_recover", "wifi_recover"]


def test_a_unit_that_rebooted_for_this_recently_waits_longer():
    acted = _office_outage(None, recent_reboots=1)
    reboot = [t for t, a in acted if a == "reboot"]
    assert reboot and 20 * 60 <= reboot[0] <= 21.5 * 60, acted


# ─── the reboot history behind the backoff ────────────────────────────────


def test_reboots_are_remembered_for_six_hours_across_lives(tmp_path):
    path = tmp_path / "self-restarts.json"
    clock = [100_000.0]
    b = health.RestartBudget(path, clock=lambda: clock[0])
    b.record("reboot", "no server for 10 min")
    clock[0] += 2 * 3600                  # two hours on, a new life
    b2 = health.RestartBudget(path, clock=lambda: clock[0])
    assert b2.allows("reboot"), "the hourly budget has freed up"
    assert b2.count("reboot", health.REBOOT_BACKOFF_WINDOW_S) == 1
    b2.record("exit", "rss")              # a later write keeps the old reboot
    b3 = health.RestartBudget(path, clock=lambda: clock[0])
    assert b3.count("reboot", health.REBOOT_BACKOFF_WINDOW_S) == 1
    assert len(b3.recent()) == 1, "the budget itself still looks back one hour"
    clock[0] += 5 * 3600
    assert health.RestartBudget(path, clock=lambda: clock[0]).count(
        "reboot", health.REBOOT_BACKOFF_WINDOW_S) == 0


def test_the_client_hands_the_ladder_its_facts(tmp_path, monkeypatch):
    seen: list[CheckInput] = []
    monkeypatch.setattr(client.health, "decide", lambda ci: seen.append(ci) or [])
    sat = health_sat(tmp_path)
    sat._voice_input_started = False
    sat._ws_disconnected_since = 0.0
    sat._health_budget.record("reboot", "earlier")
    sat._health_recovery_failures = 2
    sat._health_last_sample = sample(1, default_route=False, nm_state={"code": 30, "state": "disconnected"})
    sat._health_checks(300.0)
    ci = seen[-1]
    assert ci.recovery_failures == 2 and ci.recent_reboots == 1
    assert ci.link_down_escalate_after == 120.0 and ci.link_down_retry == 150.0
    assert ci.link_down_reboot_after == 600.0 and ci.reboot_backoff_cap == 3600.0
    assert health.link_down(ci)
    # The server is back: the count starts over.
    sat.ws = object()
    sat._health_checks(330.0)
    assert sat._health_recovery_failures == 0 and seen[-1].recovery_failures == 0


def test_failed_recoveries_are_counted_and_a_good_one_resets_them(tmp_path):
    sat = health_sat(tmp_path)
    results = [False, False, True]
    sat._reassociate_wifi = lambda: results.pop(0)
    d = Decision("wifi_recover", "link down", check="no_server_link", basis="sample")
    sat._health_apply([d], 100.0)
    sat._health_apply([d], 250.0)
    assert sat._health_recovery_failures == 2
    sat._health_apply([d], 400.0)
    assert sat._health_recovery_failures == 0


def test_the_config_keys_and_their_floors():
    cfg = client.Config.from_toml({
        "satellite": {"room_id": "office", "domovoi_url": "ws://192.168.1.20:6370"},
        "health": {"link_down_escalate_min": 0, "link_down_reboot_min": 1,
                   "link_down_retry_min": 3, "reboot_backoff_cap_min": 5},
    })
    assert cfg.health_link_down_escalate_min == 1.0
    assert cfg.health_link_down_reboot_min == 5.0
    assert cfg.health_link_down_retry_min == 3.0
    assert cfg.health_reboot_backoff_cap_min == 15.0


# ─── the radio reset after a `connection up` that timed out ───────────────


class Runner:
    def __init__(self, results):
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        r = self.results.pop(0)
        if isinstance(r, BaseException):
            raise r
        rc, out = r
        return subprocess.CompletedProcess(argv, rc, out, "")


def _sat(monkeypatch, tmp_path, results, *, helper=True, link=None, nm=None):
    monkeypatch.setattr(client.health, "read_nm_state", lambda *a, **k: nm)
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(wifi_enabled=link is not None)
    sat._last_wifi_link = None
    sat._check_wifi_link = lambda: link
    plan = wifi_profiles.Plan(
        steps=[wifi_profiles.Step("connection up id domovoi-home", wifi_profiles.connection_up_argv())],
        reason="bringing back 'domovoi-home' (the link is down)",
    )
    monkeypatch.setattr(client.wifi_profiles, "list_wifi_profiles", lambda *a, **k: [])
    monkeypatch.setattr(client.wifi_profiles, "visible_ssids", lambda *a, **k: set())
    monkeypatch.setattr(client.wifi_profiles, "plan_recovery", lambda *a, **k: plan)
    path = tmp_path / "domovoi-wifi-reset"
    if helper:
        path.write_text("#!/bin/sh\n")
    monkeypatch.setattr(client, "WIFI_RESET_HELPER", path)
    runner = Runner(results)
    monkeypatch.setattr(client.subprocess, "run", runner)
    return sat, runner


def _timeout():
    return subprocess.TimeoutExpired(["sudo"], client.NMCLI_UP_TIMEOUT_SEC)


def test_a_timed_out_connection_up_is_followed_by_the_radio_reset(monkeypatch, tmp_path):
    sat, runner = _sat(monkeypatch, tmp_path, [_timeout(), (0, "")])
    assert sat._reconnect_wifi_nm() is True
    assert runner.calls[0] == ["sudo", "-n", *wifi_profiles.connection_up_argv()]
    assert runner.calls[1] == ["sudo", "-n", str(tmp_path / "domovoi-wifi-reset")], "argv-less"


def test_a_failed_activation_on_a_down_link_gets_the_radio_reset_too(monkeypatch, tmp_path):
    sat, runner = _sat(monkeypatch, tmp_path, [(4, ""), (1, "no domovoi-home profile")])
    assert sat._reconnect_wifi_nm() is False
    assert len(runner.calls) == 2


def test_the_connection_up_waits_longer_than_it_did(monkeypatch, tmp_path):
    seen = {}

    def run(argv, **kw):
        seen["timeout"] = kw.get("timeout")
        return subprocess.CompletedProcess(argv, 0, "", "")

    sat, _ = _sat(monkeypatch, tmp_path, [])
    monkeypatch.setattr(client.subprocess, "run", run)
    assert sat._reconnect_wifi_nm() is True
    assert seen["timeout"] == client.NMCLI_UP_TIMEOUT_SEC >= 45.0


def test_no_radio_reset_while_the_radio_is_associated(monkeypatch, tmp_path):
    link = {"rx_mbits": 14.4, "ssid": "HomeNet", "signal_dbm": -77, "mode": "managed"}
    sat, runner = _sat(monkeypatch, tmp_path, [_timeout()], link=link)
    assert sat._reconnect_wifi_nm() is False
    assert len(runner.calls) == 1


def test_no_radio_reset_while_networkmanager_says_connected(monkeypatch, tmp_path):
    """No `iw` reading to go by (the watcher off): NetworkManager decides."""
    sat, runner = _sat(monkeypatch, tmp_path, [_timeout()], nm={"code": 100, "state": "connected"})
    assert sat._reconnect_wifi_nm() is False
    assert len(runner.calls) == 1


def test_no_radio_reset_while_hosting_the_hotspot(monkeypatch, tmp_path):
    link = {"rx_mbits": None, "ssid": None, "mode": "ap"}
    sat, runner = _sat(monkeypatch, tmp_path, [_timeout()], link=link)
    assert sat._reconnect_wifi_nm() is False
    assert len(runner.calls) == 1


def test_a_unit_without_the_helper_says_so_once(monkeypatch, tmp_path, caplog):
    sat, runner = _sat(monkeypatch, tmp_path, [_timeout(), _timeout()], helper=False)
    with caplog.at_level(logging.WARNING):
        assert sat._reconnect_wifi_nm() is False
        assert sat._reconnect_wifi_nm() is False
    assert len(runner.calls) == 2, "only the two connection-up tries"
    assert sum("no " in r.getMessage() and "domovoi-wifi-reset" in r.getMessage()
               for r in caplog.records) == 1


def test_sudo_s_own_refusal_is_not_answered_with_the_helper(monkeypatch, tmp_path):
    sat, runner = _sat(monkeypatch, tmp_path, [])

    def run(argv, **kw):
        runner.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 1, "", "sudo: a password is required\n")

    monkeypatch.setattr(client.subprocess, "run", run)
    assert sat._reconnect_wifi_nm() is False
    assert len(runner.calls) == 1


def test_the_example_config_documents_the_new_keys():
    from pathlib import Path

    text = (Path(client.__file__).parent / "config.toml.example").read_text(encoding="utf-8")
    for key in ("link_down_escalate_min", "link_down_retry_min", "link_down_reboot_min",
                "reboot_backoff_cap_min"):
        assert f"\n{key} = " in text, key
