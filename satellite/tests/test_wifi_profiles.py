"""Recovery brings back the HOUSE network, and never the setup hotspot.

The basement unit, found 2026-10-12 after 18 days: every recovery ran
`nmcli device connect wlan0`, which lets NetworkManager pick "the best
available profile" for the radio. The setup hotspot profile was still saved
and an access point is always available, so a satellite that lost its router
for a minute recovered onto its own setup network and stayed there.

`wifi_profiles.plan_recovery` decides what may run instead. These tests pin
its table: the home profile by name where that is possible, the old command
only where it cannot land on an access point, and nothing while the link is
up and the house network is not even in the last scan.
"""

from __future__ import annotations

import subprocess

import pytest

from satellite import portal_transport, provisioning_mode, wifi_profiles as wp

HOME = wp.Profile(name="domovoi-home", uuid="u-home", mode="infrastructure", ssid="HomeNet", active=True)
OLD_HOME = wp.Profile(name="HomeNet", uuid="u-old", mode="infrastructure", ssid="HomeNet", active=True)
SETUP = wp.Profile(name="domovoi-setup", uuid="u-setup", mode="ap", ssid="Domovoi-Setup-AB12")


def _argvs(plan):
    return [s.argv for s in plan.steps]


def test_the_names_agree_across_the_tree():
    assert wp.AP_CONNECTION == portal_transport.AP_CONNECTION
    assert wp.HOME_CONNECTION == provisioning_mode._HOME_CONNECTION


# ─── never the access point ───────────────────────────────────────────────


@pytest.mark.parametrize("associated", [True, False])
@pytest.mark.parametrize("visible", [set(), {"HomeNet"}, None])
@pytest.mark.parametrize("ap_active", [True, False])
def test_no_plan_ever_names_the_setup_profile(associated, visible, ap_active):
    for profiles in ([HOME, SETUP], [OLD_HOME, SETUP], [SETUP]):
        plan = wp.plan_recovery(profiles, visible=visible, associated=associated, ap_active=ap_active)
        for argv in _argvs(plan):
            assert "domovoi-setup" not in argv
            assert "u-setup" not in argv
        if plan.home is not None:
            assert not plan.home.is_ap


def test_the_old_command_is_refused_while_the_hotspot_profile_exists_and_home_is_out_of_sight():
    """The trap itself: router rebooting (home not in the scan), link down,
    hotspot profile saved. `device connect` here IS the hotspot."""
    plan = wp.plan_recovery([OLD_HOME, SETUP], visible=set(), associated=False)
    assert plan.steps == []
    assert "setup hotspot" in plan.reason


def test_the_old_command_is_allowed_once_the_hotspot_profile_is_gone():
    """An older unit after the upgrade and a reboot: the boot-time step
    deleted the hotspot profile, so NetworkManager's choice can only be a
    client profile."""
    plan = wp.plan_recovery([OLD_HOME], visible=set(), associated=False)
    assert _argvs(plan) == [wp.device_connect_argv()]


def test_the_old_command_is_allowed_when_the_house_network_is_in_sight():
    """With the house network in the scan its profile is available, and
    NetworkManager prefers the one it used last over a setup profile last
    used during setup."""
    plan = wp.plan_recovery([OLD_HOME, SETUP], visible={"HomeNet"}, associated=False)
    assert _argvs(plan) == [wp.device_connect_argv()]


def test_while_hosting_the_hotspot_the_old_command_is_never_used():
    """The radio cannot scan while it hosts, and the active AP is the
    profile `device connect` would pick again."""
    plan = wp.plan_recovery([OLD_HOME, SETUP], visible={"HomeNet"}, associated=False, ap_active=True)
    assert plan.steps == []


def test_a_new_card_brings_home_back_by_name():
    plan = wp.plan_recovery([HOME], visible=set(), associated=False)
    assert _argvs(plan)[0] == ["/usr/bin/nmcli", "connection", "up", "id", "domovoi-home"]


def test_a_new_card_hosting_a_hotspot_still_gets_home_by_name():
    plan = wp.plan_recovery([HOME, SETUP], visible=set(), associated=False, ap_active=True)
    assert _argvs(plan) == [wp.connection_up_argv()]


# ─── only when it can help ────────────────────────────────────────────────


def test_a_working_association_is_not_dropped_for_a_network_that_is_not_there():
    """Associated, but the house network is not in the last scan: nothing
    to reconnect TO, and reconnecting would drop the link that exists."""
    plan = wp.plan_recovery([HOME], visible={"Neighbour"}, associated=True)
    assert plan.steps == []
    assert "not in the last scan" in plan.reason


def test_an_associated_link_with_home_in_sight_is_recovered():
    """The 2026-05-06 wedge: associated, nothing answering, home in sight."""
    plan = wp.plan_recovery([HOME], visible={"HomeNet"}, associated=True)
    assert plan.steps and plan.home_visible


def test_a_link_that_is_down_is_recovered_without_a_scan():
    plan = wp.plan_recovery([HOME], visible=None, associated=False)
    assert plan.steps


def test_nothing_is_guessed_when_networkmanager_cannot_be_asked():
    plan = wp.plan_recovery(None, visible=None, associated=False)
    assert plan.steps == []


def test_a_unit_with_no_house_profile_does_nothing():
    plan = wp.plan_recovery([SETUP], visible=None, associated=False)
    assert plan.steps == [] and plan.home is None


# ─── which profile is home ────────────────────────────────────────────────


def test_home_is_the_named_profile_first():
    other = wp.Profile(name="Cafe", uuid="u-c", mode="infrastructure", ssid="Cafe", active=True)
    assert wp.home_profile([other, HOME, SETUP]) is HOME


def test_home_is_the_network_the_radio_was_last_on_otherwise():
    a = wp.Profile(name="A", uuid="a", mode="infrastructure", ssid="NetA")
    b = wp.Profile(name="B", uuid="b", mode="infrastructure", ssid="NetB")
    assert wp.home_profile([a, b, SETUP], last_ssid="NetB") is b


def test_a_profile_named_like_the_setup_profile_is_an_ap_whatever_its_mode():
    odd = wp.Profile(name="domovoi-setup", uuid="x", mode="", ssid="")
    assert odd.is_ap
    assert wp.home_profile([odd]) is None


# ─── reading NetworkManager ───────────────────────────────────────────────


class FakeNmcli:
    def __init__(self, profiles, scan=("HomeNet", "Neighbour")):
        self.profiles = profiles
        self.scan = scan
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        if "NAME,UUID,TYPE,ACTIVE" in joined:
            rows = [f"{p['name'].replace(':', chr(92) + ':')}:{p['uuid']}:{p['type']}:{p.get('active', 'no')}"
                    for p in self.profiles]
            return subprocess.CompletedProcess(argv, 0, "\n".join(rows) + "\n", "")
        if "802-11-wireless.mode,802-11-wireless.ssid" in joined:
            p = next(p for p in self.profiles if p["uuid"] == argv[-1])
            out = f"802-11-wireless.mode:{p['mode']}\n802-11-wireless.ssid:{p['ssid'].replace(':', chr(92) + ':')}\n"
            return subprocess.CompletedProcess(argv, 0, out, "")
        if "device wifi list" in joined:
            assert argv[-2:] == ["--rescan", "no"], "a recovery must not take the radio off-channel"
            return subprocess.CompletedProcess(argv, 0, "".join(s + "\n" for s in self.scan), "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected")


def test_profiles_are_read_with_their_mode_and_ssid():
    nm = FakeNmcli([
        {"name": "domovoi-home", "uuid": "u1", "type": "802-11-wireless", "mode": "infrastructure",
         "ssid": "Guest:5G", "active": "yes"},
        {"name": "domovoi-setup", "uuid": "u2", "type": "802-11-wireless", "mode": "ap", "ssid": "Domovoi-Setup-X"},
        {"name": "Wired", "uuid": "u3", "type": "802-3-ethernet", "mode": "", "ssid": ""},
    ])
    profiles = wp.list_wifi_profiles(nm)
    assert [(p.name, p.mode, p.ssid, p.active) for p in profiles] == [
        ("domovoi-home", "infrastructure", "Guest:5G", True),
        ("domovoi-setup", "ap", "Domovoi-Setup-X", False),
    ]
    assert wp.visible_ssids(nm) == {"HomeNet", "Neighbour"}


def test_an_nmcli_that_fails_means_none_not_an_empty_list():
    def broken(argv, **kw):
        return subprocess.CompletedProcess(argv, 8, "", "Error: NetworkManager is not running.")

    assert wp.list_wifi_profiles(broken) is None
    assert wp.visible_ssids(broken) is None
    assert wp.plan_recovery(wp.list_wifi_profiles(broken), visible=None, associated=False).steps == []
