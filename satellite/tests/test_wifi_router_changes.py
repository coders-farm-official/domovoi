"""A router whose security changes after the satellite was set up
(2026-10-22).

Kamron switched his access point to "WPA3-Personal+WPA2-PSK" to match his
main router. Every satellite set up before it dropped off and stayed off,
and a power cycle did not help: the saved ``domovoi-home`` profile
(``wpa-psk``, PMF at NetworkManager's default) lets NM offer SAE to a mixed
router, and a Pi Zero 2 W cannot finish SAE. The satellite now looks at the
router (satellite/wifi_adapt.py) at boot and when its recovery fails, and
the self-check never reboot-loops over a router it cannot join.

The router, NetworkManager and the driver are the model in
test_wifi_security_matrix.py (:class:`Radio`), with the house profile
saved the way setup left it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import types
from pathlib import Path

import pytest

from satellite import health, net_settings, wifi_adapt, wifi_profiles
from satellite.health import CheckInput, decide
from satellite.tests._client_import import import_client
from satellite.tests.test_wifi_security_matrix import PSK, SSID, Radio

client = import_client()

ZERO_2_W = "Raspberry Pi Zero 2 W Rev 1.0"
PI_4 = "Raspberry Pi 4 Model B Rev 1.5"
HOME = wifi_adapt.HOME_PROFILE
REPO = Path(__file__).resolve().parents[2]


class Home(Radio):
    """:class:`Radio` plus a saved, provisioned ``domovoi-home`` and the
    commands the security check uses on it."""

    def __init__(self, *, km="wpa-psk", pmf="default", active=False, **kw):
        super().__init__(**kw)
        self.home = {"name": HOME, "ssid": self.ssid, "wifi-sec.psk": PSK,
                     "wifi-sec.key-mgmt": km}
        if pmf != "default":
            self.home["wifi-sec.pmf"] = pmf
        self.profiles["home-uuid"] = self.home
        self.active = active
        self.modifies: list[list[str]] = []
        self.ups = 0

    def joins(self) -> bool:
        """Would NetworkManager join with the profile as it is now?"""
        return self._up(["nmcli", "connection", "up"], self.home, "home-uuid").returncode == 0

    def __call__(self, cmd, **kw):
        cmd = list(cmd)
        text = bool(kw.get("text"))
        if "-g" in cmd and cmd[-2:] == ["id", HOME]:
            field = cmd[cmd.index("-g") + 1]
            val = {
                "802-11-wireless.ssid": self.ssid.replace(":", "\\:"),
                "GENERAL.STATE": "activated" if self.active else "",
                "802-11-wireless-security.key-mgmt": self.home.get("wifi-sec.key-mgmt", ""),
                "802-11-wireless-security.pmf": self.home.get("wifi-sec.pmf", "default"),
            }[field]
            return self._ok(cmd, val + "\n", text)
        if cmd[1:4] == ["connection", "modify", "id"]:
            self.modifies.append(cmd)
            args = dict(zip(cmd[5::2], cmd[6::2]))
            self.home["wifi-sec.key-mgmt"] = args["802-11-wireless-security.key-mgmt"]
            self.home["wifi-sec.pmf"] = args["802-11-wireless-security.pmf"]
            return self._ok(cmd, "", text)
        if cmd[1:3] == ["--wait", "45"] and cmd[3:5] == ["connection", "up"]:
            self.ups += 1
            ok = self.joins()
            self.active = ok
            return self._ok(cmd) if ok else self._fail(cmd, "Error: Connection activation "
                                                       "failed: Secrets were required")
        return super().__call__(cmd, **kw)


def _profile(km, pmf):
    return (km, pmf or "default")


def _settings(h: Home):
    return h.home.get("wifi-sec.key-mgmt"), h.home.get("wifi-sec.pmf", "default")


# ─── the table ────────────────────────────────────────────────────────────

CASES = [
    # (scan security, profile km, profile pmf, board, after_failure) -> state, attempts / kind
    ("WPA2 WPA3", "wpa-psk", "default", ZERO_2_W, True, "change",
     (("wpa-psk", "disable"), ("wpa-psk", "optional"))),
    ("WPA2 WPA3", "wpa-psk", "default", ZERO_2_W, False, "change", (("wpa-psk", "disable"),)),
    # PMF off failed on a mixed router: one that requires PMF (status 31).
    ("WPA2 WPA3", "wpa-psk", "disable", ZERO_2_W, True, "change", (("wpa-psk", "optional"),)),
    ("WPA2 WPA3", "wpa-psk", "disable", ZERO_2_W, False, "ok", ()),
    ("WPA2 WPA3", "wpa-psk", "optional", ZERO_2_W, True, "change", (("wpa-psk", "disable"),)),
    ("WPA2 WPA3", "wpa-psk", "optional", ZERO_2_W, False, "ok", ()),
    ("WPA3", "wpa-psk", "disable", ZERO_2_W, True, "mismatch", "wpa3-only-board"),
    ("WPA3", "wpa-psk", "disable", PI_4, True, "change", (("sae", "required"),)),
    ("WPA3", "sae", "required", PI_4, True, "ok", ()),
    ("WPA2", "sae", "required", PI_4, True, "change", (("wpa-psk", "disable"), ("wpa-psk", "optional"))),
    ("WPA2 WPA3", "sae", "required", ZERO_2_W, True, "change",
     (("wpa-psk", "disable"), ("wpa-psk", "optional"))),
    ("WPA2", "wpa-psk", "disable", ZERO_2_W, True, "change", (("wpa-psk", "optional"),)),
    ("WPA2", "wpa-psk", "disable", ZERO_2_W, False, "ok", ()),
    ("WPA2", "wpa-psk", "default", ZERO_2_W, True, "ok", ()),
    ("WPA2 802.1X", "wpa-psk", "disable", ZERO_2_W, True, "mismatch", "enterprise-now"),
    ("WEP", "wpa-psk", "disable", ZERO_2_W, True, "mismatch", "wep-now"),
    ("", "wpa-psk", "disable", ZERO_2_W, True, "mismatch", "open-now"),
    (None, "wpa-psk", "default", ZERO_2_W, True, "not-visible", ()),
]


@pytest.mark.parametrize("sec, km, pmf, model, after, state, want", CASES)
def test_the_table(sec, km, pmf, model, after, state, want):
    plan = wifi_adapt.decide(sec, km, pmf, wifi_adapt.board_can_sae(model), after_failure=after)
    assert plan.state == state, plan
    if state == "mismatch":
        assert plan.kind == want and want in wifi_adapt.MISMATCH_WORDS
    else:
        assert plan.attempts == want


@pytest.mark.parametrize("model, can", [
    (ZERO_2_W, False), ("Raspberry Pi Zero W Rev 1.1", False),
    ("Raspberry Pi 3 Model B Rev 1.2", False), ("Raspberry Pi 3 Model B Plus Rev 1.3", True),
    (PI_4, True), ("Raspberry Pi 5 Model B Rev 1.0", True), (None, None), ("Radxa ZERO 3", None),
])
def test_which_boards_can_do_wpa3(model, can):
    assert wifi_adapt.board_can_sae(model) is can


def test_pmf_is_read_in_every_shape_nmcli_prints():
    for raw, want in [("default", "default"), ("1 (disable)", "disable"), ("3", "required"),
                      ("optional", "optional"), ("", "default")]:
        assert wifi_adapt.normalize_pmf(raw) == want


# ─── the router changes after setup ──────────────────────────────────────


def test_wpa2_to_mixed_the_satellite_rejoins_by_itself():
    """Kamron's case: set up on WPA2 (the profile setup used to make), the
    router goes mixed, NM offers SAE, the Pi can't, and the link is gone."""
    h = Home(security="WPA2 WPA3", router=("wpa-psk", "sae"), km="wpa-psk", pmf="default")
    assert h.joins() is False                      # what the satellites hit
    res = wifi_adapt.adapt(h, "nmcli", model=ZERO_2_W, after_failure=True, sleep=lambda s: None)
    assert res.state == "adapted", res
    assert _settings(h) == ("wpa-psk", "disable")
    assert h.active and h.ups == 1
    assert any("PMF off" in c for c in res.changed)


def test_mixed_to_wpa3_only_on_a_pi_zero_says_why_and_changes_nothing():
    h = Home(security="WPA3", router=("sae",), km="wpa-psk", pmf="disable")
    res = wifi_adapt.adapt(h, "nmcli", model=ZERO_2_W, after_failure=True, sleep=lambda s: None)
    assert res.state == "mismatch" and res.kind == wifi_adapt.WPA3_ONLY_BOARD
    assert "WPA2/WPA3 mixed" in res.words and "WPA3-only" in res.words
    assert h.modifies == [] and h.ups == 0


def test_mixed_to_wpa3_only_on_a_pi_4_switches_to_sae():
    h = Home(security="WPA3", router=("sae",), km="wpa-psk", pmf="disable", driver_sae=True)
    res = wifi_adapt.adapt(h, "nmcli", model=PI_4, after_failure=True, sleep=lambda s: None)
    assert res.state == "adapted" and _settings(h) == ("sae", "required")


@pytest.mark.parametrize("km, pmf, model, sae_ok", [
    ("wpa-psk", "disable", ZERO_2_W, False),     # set up on mixed, router back to WPA2
    ("sae", "required", PI_4, True),             # was WPA3-only on a Pi 4
])
def test_back_to_wpa2_still_joins(km, pmf, model, sae_ok):
    h = Home(security="WPA2", router=("wpa-psk",), km=km, pmf=pmf, driver_sae=sae_ok)
    if km == "wpa-psk":
        assert h.joins()                         # nothing to do at all
        return
    assert not h.joins()
    res = wifi_adapt.adapt(h, "nmcli", model=model, after_failure=True, sleep=lambda s: None)
    assert res.state == "adapted" and _settings(h) == ("wpa-psk", "disable")


def test_a_wpa2_router_that_now_requires_pmf_gets_pmf_back():
    h = Home(security="WPA2", router=("wpa-psk",), km="wpa-psk", pmf="disable",
             pmf_required=True)
    assert not h.joins()
    res = wifi_adapt.adapt(h, "nmcli", model=ZERO_2_W, after_failure=True, sleep=lambda s: None)
    assert res.state == "adapted" and _settings(h) == ("wpa-psk", "optional")


def test_a_mixed_router_that_requires_pmf_gets_pmf_optional():
    """A Wi-Fi 7 router in transition mode with PMF required refuses a
    PMF-off client (status 31); PMF optional joins, on a radio that does
    not claim SAE (so NetworkManager does not offer it)."""
    h = Home(security="WPA2 WPA3", router=("wpa-psk", "sae"), km="wpa-psk", pmf="disable",
             pmf_required=True, reports_sae=False)
    assert not h.joins()
    res = wifi_adapt.adapt(h, "nmcli", model=ZERO_2_W, after_failure=True, sleep=lambda s: None)
    assert res.state == "adapted" and _settings(h) == ("wpa-psk", "optional")


def test_a_link_that_is_up_is_never_touched():
    h = Home(security="WPA2 WPA3", router=("wpa-psk", "sae"), active=True)
    res = wifi_adapt.adapt(h, "nmcli", model=ZERO_2_W, after_failure=True, sleep=lambda s: None)
    assert res.state == "connected" and h.modifies == []


# ─── at boot: before the first join can give up ──────────────────────────


def test_the_boot_step_fixes_the_profile_before_joining():
    """What would have made the power cycle work."""
    h = Home(security="WPA2 WPA3", router=("wpa-psk", "sae"), km="wpa-psk", pmf="default")
    report = net_settings.Report()
    profiles = [wifi_profiles.Profile(name=HOME, uuid="home-uuid", ssid=SSID)]
    net_settings.adapt_home_security("nmcli", profiles, h, report, sleep=lambda s: None,
                                     model=ZERO_2_W)
    assert _settings(h) == ("wpa-psk", "disable") and h.active
    assert any("Wi-Fi security" in c for c in report.changed)
    doc = report.document(room_id="den", hostname="domovoi-den", when="boot")
    assert doc["wifi_security"]["state"] == "adapted"


def test_the_boot_step_leaves_a_matching_profile_alone():
    h = Home(security="WPA2", router=("wpa-psk",), km="wpa-psk", pmf="disable")
    report = net_settings.Report()
    profiles = [wifi_profiles.Profile(name=HOME, uuid="home-uuid", ssid=SSID)]
    net_settings.adapt_home_security("nmcli", profiles, h, report, sleep=lambda s: None,
                                     model=ZERO_2_W)
    assert h.modifies == [] and report.changed == [] and report.errors == []


def test_the_boot_step_records_a_mismatch_for_the_client():
    h = Home(security="WPA3", router=("sae",), km="wpa-psk", pmf="disable")
    report = net_settings.Report()
    profiles = [wifi_profiles.Profile(name=HOME, uuid="home-uuid", ssid=SSID)]
    net_settings.adapt_home_security("nmcli", profiles, h, report, sleep=lambda s: None,
                                     model=ZERO_2_W)
    assert report.wifi["state"] == "mismatch" and report.wifi["kind"] == "wpa3-only-board"
    assert any("WPA3-only" in e for e in report.errors)


# ─── no reboot loop ──────────────────────────────────────────────────────

DOWN = dict(nm_connected=False, default_route=False, gateway_ok=None)


def _outage(mismatch: str | None, minutes: int = 90) -> list[tuple[float, str]]:
    """The self-check over an outage, every recovery failing."""
    p = health.Persistence(2)
    last, failures, acted, seq, t = None, 0, [], 0, 0.0
    while t <= minutes * 60.0:
        if int(t) % 60 == 0:
            seq += 1
        ci = CheckInput(now=t, mic_expected=False, idle=True, rss_mb=100.0, fds=30,
                        no_server_for=t, last_wifi_recovery=last, recovery_failures=failures,
                        reboot_helper=True, wifi_mismatch=mismatch, **DOWN)
        for d in p.filter(decide(ci), seq):
            acted.append((t, d.action))
            p.forget(d.check)
            if d.action == "wifi_recover":
                last, failures = t, failures + 1
            if d.action == "reboot":
                return acted
        t += 30.0
    return acted


def test_a_router_this_radio_cannot_join_never_reboots_and_keeps_rescanning():
    acted = _outage(wifi_adapt.MISMATCH_WORDS[wifi_adapt.WPA3_ONLY_BOARD])
    assert "reboot" not in [a for _, a in acted]
    recovers = [t for t, a in acted if a == "wifi_recover"]
    assert len(recovers) >= 15                      # 90 min, every ~5 min
    gaps = [b - a for a, b in zip(recovers, recovers[1:])]
    assert min(gaps) >= wifi_adapt.MISMATCH_RETRY_SEC


def test_without_a_mismatch_the_ladder_still_reboots():
    assert "reboot" in [a for _, a in _outage(None)]


def test_the_reason_is_in_the_recovery_line():
    words = wifi_adapt.MISMATCH_WORDS[wifi_adapt.WPA3_ONLY_BOARD]
    ci = CheckInput(now=10_000.0, no_server_for=600.0, wifi_mismatch=words, **DOWN)
    d = [d for d in decide(ci) if d.action == "wifi_recover"]
    assert d and words in d[0].reason


# ─── the client's side ───────────────────────────────────────────────────


def _sat(monkeypatch, tmp_path, rc, stdout, *, helper=True):
    sat = object.__new__(client.Satellite)
    sat._last_health = types.SimpleNamespace(actions=[], note_action=lambda a, r: sat._last_health.actions.append((a, r)))
    path = tmp_path / "domovoi-wifi-adapt"
    if helper:
        path.write_text("#!/bin/sh\n")
    monkeypatch.setattr(client, "WIFI_ADAPT_HELPER", path)
    calls, ring = [], []
    monkeypatch.setattr(client, "_setup_status", lambda state, *a: ring.append(state))

    def run(argv, **kw):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, rc, stdout, "")

    monkeypatch.setattr(client.subprocess, "run", run)
    return sat, calls, ring


def test_the_client_records_a_mismatch_and_shows_it_once(monkeypatch, tmp_path):
    out = "the router switched to WPA3-only\nresult=mismatch kind=wpa3-only-board\n"
    sat, calls, ring = _sat(monkeypatch, tmp_path, 7, out)
    assert sat._wifi_adapt(False, False) is False
    assert sat._wifi_adapt(False, False) is False
    assert calls[0] == ["sudo", "-n", str(tmp_path / "domovoi-wifi-adapt")], "argv-less"
    assert sat._wifi_mismatch == wifi_adapt.MISMATCH_WORDS["wpa3-only-board"]
    assert ring == ["join-failed"]                       # red, once
    assert [a for a, _ in sat._last_health.actions] == ["wifi_mismatch"]


def test_the_client_clears_it_when_the_router_changes_back(monkeypatch, tmp_path):
    sat, _, _ = _sat(monkeypatch, tmp_path, 0, "changed=wpa-psk/disable\nresult=adapted kind=-\n")
    sat._wifi_mismatch = "old"
    assert sat._wifi_adapt(False, False) is True
    assert sat._wifi_mismatch is None


def test_nothing_to_change_falls_through_to_the_radio_reset(monkeypatch, tmp_path):
    sat, _, _ = _sat(monkeypatch, tmp_path, 1, "result=ok kind=-\n")
    assert sat._wifi_adapt(False, False) is None


def test_no_helper_no_call(monkeypatch, tmp_path):
    sat, calls, _ = _sat(monkeypatch, tmp_path, 0, "", helper=False)
    assert sat._wifi_adapt(False, False) is None and calls == []


def test_never_while_associated_or_hosting(monkeypatch, tmp_path):
    sat, calls, _ = _sat(monkeypatch, tmp_path, 0, "")
    assert sat._wifi_adapt(True, False) is None and sat._wifi_adapt(False, True) is None
    assert calls == []


# ─── the root helper: same table, in sh ──────────────────────────────────

_SH = shutil.which("dash") or shutil.which("sh")

FAKE_NMCLI = r"""#!/bin/sh
D="__DIR__"
echo "$*" >> "$D/calls"
case "$*" in
  "-t -f NAME connection show") cat "$D/names" ;;
  "-t -g GENERAL.STATE connection show id domovoi-home") cat "$D/state" ;;
  "-t -g 802-11-wireless.ssid connection show id domovoi-home") cat "$D/ssid" ;;
  "-t -g 802-11-wireless-security.key-mgmt connection show id domovoi-home") cat "$D/km" ;;
  "-t -g 802-11-wireless-security.pmf connection show id domovoi-home") cat "$D/pmf" ;;
  "-t -f SSID,SECURITY device wifi list --rescan yes") cat "$D/scan" ;;
  "connection modify id domovoi-home "*) echo "$*" >> "$D/modify" ;;
  "--wait 45 connection up id domovoi-home") exit "$(cat "$D/up_rc")" ;;
esac
exit 0
"""


def _run_helper(tmp_path, *, sec, km, pmf, model, ssid=SSID, up_rc=0, names=HOME):
    d = tmp_path
    for name, val in {"names": names + "\n", "state": "\n",
                      "ssid": ssid.replace("\\", "\\\\").replace(":", "\\:") + "\n",
                      "km": km + "\n", "pmf": pmf + "\n", "up_rc": str(up_rc) + "\n",
                      "model": model + "\0"}.items():
        (d / name).write_bytes(val.encode("utf-8"))
    esc = ssid.replace("\\", "\\\\").replace(":", "\\:")
    scan = "Neighbour:WPA2\n" + (f"{esc}:{sec}\n" if sec is not None else "")
    (d / "scan").write_text(scan, encoding="utf-8", newline="\n")
    fake = d / "nmcli"
    fake.write_text(FAKE_NMCLI.replace("__DIR__", d.as_posix()), encoding="utf-8", newline="\n")
    os.chmod(fake, 0o755)
    src = (REPO / "satellite" / "scripts" / "domovoi-wifi-adapt").read_text(encoding="utf-8")
    src = (src.replace("NMCLI=/usr/bin/nmcli", f"NMCLI={fake.as_posix()}")
              .replace("MODEL_FILE=/proc/device-tree/model", f"MODEL_FILE={(d / 'model').as_posix()}")
              .replace("sleep 5", "sleep 0"))
    script = d / "adapt.sh"
    script.write_text(src, encoding="utf-8", newline="\n")
    r = subprocess.run([_SH, script.as_posix()], capture_output=True, text=True, timeout=60)
    modify = (d / "modify").read_text(encoding="utf-8") if (d / "modify").exists() else ""
    return r, modify


@pytest.mark.skipif(_SH is None, reason="no POSIX sh on this machine")
@pytest.mark.parametrize("sec, km, pmf, model, after, state, want", [
    c for c in CASES if c[4] is True
])
def test_the_root_helper_follows_the_same_table(tmp_path, sec, km, pmf, model, after, state, want):
    r, modify = _run_helper(tmp_path, sec=sec, km=km, pmf=pmf, model=model)
    last = r.stdout.strip().splitlines()[-1]
    got_state, kind = wifi_adapt.parse_result(last)
    expect_rc = {"change": 0, "ok": 1, "mismatch": 7, "not-visible": 10}[state]
    assert r.returncode == expect_rc, (r.stdout, r.stderr)
    if state == "mismatch":
        assert kind == want
    if state == "change":
        assert got_state == "adapted"
        first_km, first_pmf = want[0]
        assert (f"802-11-wireless-security.key-mgmt {first_km} "
                f"802-11-wireless-security.pmf {first_pmf}") in modify
    else:
        assert modify == ""


@pytest.mark.skipif(_SH is None, reason="no POSIX sh on this machine")
@pytest.mark.parametrize("ssid", ["Maple Wifi 2.0", "Guest:5G", "back\\slash", "café ☕"])
def test_the_root_helper_finds_names_with_awkward_characters(tmp_path, ssid):
    r, modify = _run_helper(tmp_path, sec="WPA2 WPA3", km="wpa-psk", pmf="default",
                            model=ZERO_2_W, ssid=ssid)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "pmf disable" in modify


@pytest.mark.skipif(_SH is None, reason="no POSIX sh on this machine")
def test_the_root_helper_tries_the_next_setting_and_then_gives_up(tmp_path):
    r, modify = _run_helper(tmp_path, sec="WPA2", km="sae", pmf="required",
                            model=PI_4, up_rc=4)
    assert r.returncode == 8
    assert modify.count("connection modify") == 2         # psk/disable, psk/default


@pytest.mark.skipif(_SH is None, reason="no POSIX sh on this machine")
def test_the_root_helper_takes_no_arguments(tmp_path):
    r = subprocess.run([_SH, (REPO / "satellite" / "scripts" / "domovoi-wifi-adapt").as_posix(), "x"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 2


def test_the_root_helper_edits_only_the_house_profile_and_only_security():
    src = (REPO / "satellite" / "scripts" / "domovoi-wifi-adapt").read_text(encoding="utf-8")
    assert src.startswith("#!/bin/sh\n") and "\r" not in src
    assert "NMCLI=/usr/bin/nmcli" in src and "HOME_PROFILE=domovoi-home" in src
    assert "connection delete" not in src and "device connect" not in src
    assert "connection add" not in src and "radio wifi" not in src
    modifies = [ln for ln in src.splitlines() if "connection modify" in ln]
    assert modifies == ['  "$NMCLI" connection modify id "$HOME_PROFILE" \\']
    assert '"$@"' not in src
