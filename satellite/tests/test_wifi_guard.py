"""The connect requests nobody answered (2026-10-23, the third hardware
report).

Kamron's card: two TP-Link access points in EasyMesh, both "WPA3-Personal+
WPA2-PSK", 18 "Trying to associate" lines, every one "Authentication with
<AP> timed out" ten seconds later, and NetworkManager's "association took
too long" -> need-auth -> no-secrets, which nmcli reports as "Secrets were
required". wpa_supplicant had been handed

    WPA-PSK WPA-PSK-SHA256 FT-PSK SAE FT-SAE

for every wpa-psk attempt, PMF off included (NetworkManager 1.52 adds SAE
to a client wpa-psk profile whatever its PMF; FT whenever wpa_supplicant
has 802.11r). wpa_supplicant prefers FT-SAE, SAE, FT-PSK over WPA-PSK, and
the Pi Zero 2 W's brcmfmac completes none of them. satellite/wifi_guard.py
cuts the list to what the radio can do.
"""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from satellite import join_diag, wifi_adapt, wifi_guard, wifi_reasons
from satellite import provisioning_mode as pm

REPO = Path(__file__).resolve().parents[2]
ZERO_2_W = "Raspberry Pi Zero 2 W Rev 1.0"
PI_3B = "Raspberry Pi 3 Model B Rev 1.2"
PI_3BP = "Raspberry Pi 3 Model B Plus Rev 1.3"
PI_4 = "Raspberry Pi 4 Model B Rev 1.5"
NM_PSK = "WPA-PSK WPA-PSK-SHA256 FT-PSK SAE FT-SAE"     # what the card showed
NM_SAE = "SAE FT-SAE"


# ─── the policy ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("model, current, want", [
    (ZERO_2_W, NM_PSK, "WPA-PSK WPA-PSK-SHA256"),
    (PI_3B, NM_PSK, "WPA-PSK WPA-PSK-SHA256"),
    (PI_3BP, NM_PSK, "WPA-PSK WPA-PSK-SHA256 SAE"),          # no FT on any Pi
    (PI_4, NM_PSK, "WPA-PSK WPA-PSK-SHA256 SAE"),
    (ZERO_2_W, NM_SAE, "SAE"),           # WPA3-only: SAE is all there is
    (ZERO_2_W, "WPA-PSK-SHA256 SAE FT-SAE", "WPA-PSK-SHA256"),   # PMF required
    (ZERO_2_W, "WPA-PSK", None),         # nothing to cut
    (PI_4, "WPA-PSK WPA-PSK-SHA256 SAE", None),
    ("Radxa ZERO 3", NM_PSK, None),      # not a brcmfmac board: left alone
])
def test_what_each_board_keeps(model, current, want):
    assert wifi_guard.filtered(current, wifi_guard.policy_for(model)) == want


@pytest.mark.parametrize("override, want", [
    ({"join_no_ft": False}, "WPA-PSK WPA-PSK-SHA256 FT-PSK"),
    ({"join_key_mgmt": "WPA-PSK"}, "WPA-PSK"),
    ({"join_key_mgmt": "wpa-psk wpa-psk-sha256"}, "WPA-PSK WPA-PSK-SHA256"),
    ({"join_key_mgmt": "auto"}, None),
    ({"join_key_mgmt": "rm -rf /"}, "WPA-PSK WPA-PSK-SHA256"),   # ignored
])
def test_the_ap_json_diagnostic_overrides(override, want):
    pol = wifi_guard.policy_for(ZERO_2_W, override)
    assert wifi_guard.filtered(NM_PSK, pol) == want


@pytest.mark.parametrize("model", [ZERO_2_W, "Raspberry Pi Zero W Rev 1.1", PI_3B, PI_3BP,
                                   PI_4, "Raspberry Pi 5 Model B Rev 1.0", None, "Radxa ZERO 3"])
def test_the_guard_and_the_adapter_agree_on_wpa3(model):
    assert wifi_guard.board_can_sae(model) is wifi_adapt.board_can_sae(model)


def test_the_override_is_read_from_the_boot_partition(tmp_path):
    (tmp_path / "domovoi").mkdir()
    (tmp_path / "domovoi" / "ap.json").write_text(json.dumps(
        {"ssid": "Domovoi-Setup", "psk": "x" * 12, "join_no_ft": False, "other": 1}))
    assert wifi_guard.read_override((tmp_path,)) == {"join_no_ft": False}


# ─── one network, through the control interface ──────────────────────────


class Supplicant:
    """wpa_supplicant's control commands over a network table."""

    def __init__(self, nets, state="SCANNING"):
        self.nets = nets
        self.state = state
        self.sent: list[str] = []

    def __call__(self, line: str) -> str:
        self.sent.append(line)
        parts = line.split(" ", 3)
        if parts[0] == "GET_NETWORK":
            return self.nets[parts[1]].get(parts[2], "FAIL")
        if parts[0] == "SET_NETWORK":
            self.nets[parts[1]][parts[2]] = parts[3]
            return "OK"
        if parts[0] == "STATUS":
            return f"wpa_state={self.state}\nssid=x"
        return "OK"


def test_the_house_network_is_cut_before_it_associates():
    s = Supplicant({"0": {"mode": "0", "key_mgmt": NM_PSK}})
    done = wifi_guard.apply_to_network(s, "0", wifi_guard.policy_for(ZERO_2_W))
    assert s.nets["0"]["key_mgmt"] == "WPA-PSK WPA-PSK-SHA256"
    assert "REASSOCIATE" not in s.sent                      # not started yet
    assert "-> 'WPA-PSK WPA-PSK-SHA256'" in done and "no FT" in done


def test_an_association_already_under_way_is_restarted():
    s = Supplicant({"0": {"mode": "0", "key_mgmt": NM_PSK}}, state="ASSOCIATING")
    wifi_guard.apply_to_network(s, "0", wifi_guard.policy_for(ZERO_2_W))
    assert s.sent[-1] == "REASSOCIATE"


def test_the_setup_hotspot_is_never_touched():
    s = Supplicant({"1": {"mode": "2", "key_mgmt": "WPA-PSK"}})
    assert wifi_guard.apply_to_network(s, "1", wifi_guard.policy_for(ZERO_2_W)) is None
    assert not any(c.startswith("SET_NETWORK") for c in s.sent)


# ─── the mesh router, modelled ───────────────────────────────────────────

# wpa_supplicant's preference, first wins (wpa_supplicant_set_suites).
PRIORITY = ("FT-SAE", "SAE", "FT-PSK", "WPA-PSK-SHA256", "WPA-PSK")
# What each TP-Link access point advertised, as `iw` prints it (EasyMesh
# with 802.11r on).
MESH_AP_AKMS = {"WPA-PSK", "SAE", "FT-PSK", "FT-SAE"}
# What the Pi Zero 2 W's brcmfmac firmware completes.
BRCMFMAC_DOES = {"WPA-PSK", "WPA-PSK-SHA256"}


def _associate(key_mgmt: str, ap=MESH_AP_AKMS, radio=BRCMFMAC_DOES) -> str:
    offered = set(key_mgmt.split()) & ap
    chosen = next((a for a in PRIORITY if a in offered), None)
    if chosen is None:
        return "no common AKM"
    return f"joined with {chosen}" if chosen in radio else f"timed out ({chosen})"


def test_without_the_guard_every_attempt_hangs_on_the_mesh():
    """The card's 18 attempts: wpa-psk PMF off, PMF optional, sae - all
    reach the supplicant with FT and SAE in them."""
    for km in (NM_PSK, NM_PSK, NM_SAE):
        assert _associate(km).startswith("timed out")


def test_with_the_guard_the_mesh_is_joined_on_plain_wpa2():
    km = wifi_guard.filtered(NM_PSK, wifi_guard.policy_for(ZERO_2_W))
    assert _associate(km) == "joined with WPA-PSK"
    assert not any(t.startswith("FT-") for t in km.split()) and "SAE" not in km.split()


def test_a_pi_4_still_gets_wpa3_but_never_ft():
    km = wifi_guard.filtered(NM_PSK, wifi_guard.policy_for(PI_4))
    assert _associate(km, radio={"WPA-PSK", "SAE"}) == "joined with SAE"


# ─── what the card says about it ─────────────────────────────────────────

# Lines from Kamron's card, the BSSID kept so the diagnostics can label it.
BSSID = "50:91:e3:12:b1:f6"
CARD_LINES = "\n".join([
    "<info>  [1792777617.1239] Config: added 'key_mgmt' value 'WPA-PSK WPA-PSK-SHA256 FT-PSK SAE FT-SAE'",
    f"wlan0: Trying to associate with {BSSID} (SSID='Maple Wifi 2.0' freq=2462 MHz)",
    "<info>  [1792777617.1855] device (wlan0): supplicant interface state: disconnected -> associating",
    f"wlan0: Authentication with {BSSID} timed out.",
    f"wlan0: CTRL-EVENT-DISCONNECTED bssid={BSSID} reason=3 locally_generated=1",
    "<warn>  [1792777642.3904] device (wlan0): Activation: (wifi) association took too long",
    "<info>  [1792777642.3906] device (wlan0): state change: config -> need-auth (reason 'none', managed-type: 'full')",
    "<warn>  [1792777642.4145] device (wlan0): no secrets: No agents were available for this request.",
])


def _ok(cmd, out=""):
    return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


def test_a_request_nobody_answered_is_not_called_a_password_problem():
    d = join_diag.Diag()
    e = d.record(lambda c, **k: _ok(c, CARD_LINES), since=1_792_777_616.0, key_mgmt="wpa-psk",
                 pmf="optional", ok=False, kind=wifi_reasons.WRONG_PASSWORD,
                 detail="Error: Connection activation failed: Secrets were required, but not provided",
                 ssid="Maple Wifi 2.0", psk="pw-pw-pw-pw")
    assert e["outcome"] == wifi_reasons.NO_ANSWER
    assert e["supplicant_key_mgmt"] == NM_PSK
    assert any("never answered" in h for h in e["hints"])
    assert "password" not in wifi_reasons.CARD[wifi_reasons.NO_ANSWER].replace("not the password", "")
    assert "didn't answer" in wifi_reasons.PORTAL[wifi_reasons.NO_ANSWER]


def test_a_real_wrong_key_is_still_a_wrong_key():
    lines = CARD_LINES.replace(f"wlan0: Authentication with {BSSID} timed out.",
                               "<info> device (wlan0): supplicant interface state: associating -> 4way_handshake\n"
                               "wlan0: WPA: 4-Way Handshake failed - pre-shared key may be incorrect")
    e = join_diag.Diag().record(lambda c, **k: _ok(c, lines), since=1.0, key_mgmt="wpa-psk",
                                pmf="disable", ok=False, kind=wifi_reasons.WRONG_PASSWORD,
                                detail="", ssid="Maple Wifi 2.0", psk="pw-pw-pw-pw")
    assert e["outcome"] == wifi_reasons.WRONG_PASSWORD


def test_the_join_reports_no_answer_through_to_the_page(monkeypatch):
    from satellite.tests.test_wifi_security_matrix import Radio, SSID, PSK

    class Hangs(Radio):
        def __call__(self, cmd, **kw):
            cmd = list(cmd)
            if cmd[0] == "journalctl" and "--since" in cmd and cmd[cmd.index("--since") + 1].startswith("@"):
                return _ok(cmd, CARD_LINES)
            return super().__call__(cmd, **kw)

    monkeypatch.setattr(pm.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    monkeypatch.setattr(pm, "_WIFI_SCAN_POLL_SEC", 0.0)
    monkeypatch.setattr(pm, "_WIFI_SCAN_WAIT_SEC", 0.0)
    pm.JOIN_DIAG.reset()
    r = Hangs(security="WPA2 WPA3", router=(), guard=False)     # nothing ever answers
    ok, err = pm.apply_wifi(SSID, PSK, None, False, 30.0, run=r)
    assert ok is False
    assert wifi_reasons.kind_of(err) == wifi_reasons.NO_ANSWER


IW_DUMP = """BSS 50:91:e3:12:b1:f6(on wlan0)
\tfreq: 2462
\tSSID: Maple Wifi 2.0
\tRSN:\t * Version: 1
\t\t * Group cipher: CCMP
\t\t * Pairwise ciphers: CCMP
\t\t * Authentication suites: PSK SAE FT/PSK FT/SAE
\t\t * Capabilities: 1-PTKSA-RC 1-GTKSA-RC MFP-capable (0x0080)
\tHT capabilities:
\tHE capabilities:
\tEHT capabilities:
\tMobility Domain:
BSS a8:42:a1:77:31:cc(on wlan0)
\tfreq: 2412
\tSSID: Maple Wifi 2.0
\tRSN:\t * Version: 1
\t\t * Authentication suites: PSK SAE
\t\t * Capabilities: 16-PTKSA-RC 1-GTKSA-RC MFP-capable MFP-required (0x00cc)
\tHT capabilities:
\tHE capabilities:
"""


def test_the_scan_says_which_access_point_advertises_fast_roaming():
    rsn = join_diag.Diag.rsn_details(lambda c, **k: _ok(c, IW_DUMP))
    a = rsn["50:91:e3:12:b1:f6"]
    assert a["akm"] == ["PSK", "SAE", "FT/PSK", "FT/SAE"] and a["ft"] is True
    assert a["pmf"] == "capable" and a["wifi"] == 7
    b = rsn["a8:42:a1:77:31:cc"]
    assert b["ft"] is False and b["pmf"] == "required" and b["wifi"] == 6


# ─── on the card image ───────────────────────────────────────────────────


def test_the_guard_is_self_contained_stdlib_only():
    tree = ast.parse((REPO / "satellite" / "wifi_guard.py").read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0])
    assert "satellite" not in mods, "installed root-owned on its own: no package imports"
    assert mods <= {"__future__", "json", "logging", "os", "socket", "sys", "time",
                    "dataclasses", "pathlib"}


def test_stage_1_installs_it_root_owned_and_runs_it_isolated():
    unit = (REPO / "satellite" / "scripts" / "domovoi-wifi-guard.service").read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/python3 -I /usr/local/lib/domovoi/wifi-guard" in unit
    assert "@HOME@" not in unit.split("[Unit]", 1)[1]        # never the account's tree
    assert "ProtectSystem=strict" in unit and "RestrictAddressFamilies=AF_UNIX" in unit
    fr = (REPO / "domovoi" / "satellite_media" / "templates" / "firstrun.sh.tmpl").read_text(encoding="utf-8")
    assert '"$PAYDIR/system/domovoi-wifi-guard" /usr/local/lib/domovoi/wifi-guard' in fr
    assert "install -m 0755 -o root -g root" in fr
    assert "$EARLY_UNIT $WIFI_GUARD" in fr
    payload = (REPO / "domovoi" / "satellite_media" / "payload.py").read_text(encoding="utf-8")
    assert 'sat_dir / "wifi_guard.py", system / "domovoi-wifi-guard"' in payload
