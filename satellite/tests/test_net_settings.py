"""The network settings root applies at boot: idempotent, logged, and
touching nothing else.

`satellite/net_settings.py` runs as root on every satellite at every boot
(and at the end of setup on a new card). Three settings, each applied only
where missing:

* the `domovoi-setup` hotspot profile deleted (by that name only);
* Wi-Fi power saving off on the house profile, and on the radio now;
* the host name `domovoi-<room>`, with /etc/hosts' 127.0.1.1 line to match
  and avahi restarted for the mDNS name — never over a name a person chose.

Every external command is a fake here: nothing on this machine is renamed
and no NetworkManager is asked anything.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from satellite import net_settings as ns
from satellite import provisioning_mode as pm


class FakeHost:
    """NetworkManager, iw, hostnamectl and systemctl, as one fake host."""

    def __init__(self, *, profiles=None, powersave=None, radio_ps="on",
                 hostnamectl_ok=True, avahi_active=True, nm_running=True,
                 undeletable=(), hostname_path=None):
        self.profiles = profiles if profiles is not None else [
            {"name": "Kamber Wifi 2.0", "uuid": "u-home", "mode": "infrastructure", "ssid": "Kamber Wifi 2.0"},
            {"name": "domovoi-setup", "uuid": "u-setup", "mode": "ap", "ssid": "Domovoi-Setup-AB12"},
        ]
        self.powersave = dict(powersave or {"u-home": 0})
        self.radio_ps = radio_ps
        self.hostnamectl_ok = hostnamectl_ok
        self.avahi_active = avahi_active
        self.nm_running = nm_running
        self.undeletable = set(undeletable)
        self.hostname_path = hostname_path
        self.calls: list[list[str]] = []

    def ok(self, argv, out=""):
        return subprocess.CompletedProcess(argv, 0, out, "")

    def fail(self, argv, rc=1, err="error"):
        return subprocess.CompletedProcess(argv, rc, "", err)

    def __call__(self, argv, **kw):
        argv = list(argv)
        self.calls.append(argv)
        j = " ".join(argv)
        if argv[0].endswith("nmcli"):
            if "RUNNING general" in j:
                return self.ok(argv, "running\n" if self.nm_running else "")
            if "NAME,UUID,TYPE,ACTIVE" in j:
                rows = [f"{p['name']}:{p['uuid']}:802-11-wireless:no" for p in self.profiles]
                return self.ok(argv, "".join(r + "\n" for r in rows))
            if "802-11-wireless.mode,802-11-wireless.ssid" in j:
                p = next(p for p in self.profiles if p["uuid"] == argv[-1])
                return self.ok(argv, f"802-11-wireless.mode:{p['mode']}\n802-11-wireless.ssid:{p['ssid']}\n")
            if "802-11-wireless.powersave" in j and "show" in argv:
                v = self.powersave.get(argv[-1], 0)
                word = {0: "default", 1: "ignore", 2: "disable", 3: "enable"}[v]
                return self.ok(argv, f"802-11-wireless.powersave:{v} ({word})\n")
            if argv[1:3] == ["connection", "modify"]:
                self.powersave[argv[4]] = int(argv[-1])
                return self.ok(argv)
            if argv[1:3] == ["connection", "delete"]:
                if argv[-1] in self.undeletable:
                    return self.fail(argv, 4, "Error: not authorized to delete connection.")
                self.profiles = [p for p in self.profiles if p["uuid"] != argv[-1]]
                return self.ok(argv)
        if argv[0].endswith("iw"):
            if argv[-1] == "power_save" and argv[-2] == "get":
                return self.ok(argv, f"Power save: {self.radio_ps}\n")
            if argv[-3:] == ["set", "power_save", "off"]:
                self.radio_ps = "off"
                return self.ok(argv)
        if argv[0] == "hostnamectl":
            if not self.hostnamectl_ok:
                return self.fail(argv, 1, "Failed to connect to bus")
            self.hostname_path.write_text(argv[-1] + "\n", encoding="utf-8")
            return self.ok(argv)
        if argv[0] == "hostname":
            return self.ok(argv)
        if argv[0] == "systemctl":
            if "is-active" in argv:
                return self.ok(argv) if self.avahi_active else self.fail(argv, 3)
            if "try-restart" in argv:
                return self.ok(argv)
        return self.fail(argv, 127, f"unexpected: {j}")

    def ran(self, *words):
        return [c for c in self.calls if all(w in c for w in words)]


STOCK_HOSTS = (
    "127.0.0.1\tlocalhost\n"
    "::1\t\tlocalhost ip6-localhost ip6-loopback\n"
    "ff02::1\t\tip6-allnodes\n"
    "ff02::2\t\tip6-allrouters\n"
    "\n"
    "127.0.1.1\t\traspberrypi\n"
)


@pytest.fixture
def host(tmp_path):
    hostname = tmp_path / "hostname"
    hostname.write_text("raspberrypi\n", encoding="utf-8")
    hosts = tmp_path / "hosts"
    hosts.write_text(STOCK_HOSTS, encoding="utf-8")
    fake = FakeHost(hostname_path=hostname)
    fake.paths = {
        "hostname_path": hostname, "hosts_path": hosts,
        "report_path": tmp_path / "home" / ".domovoi" / "net-settings.json",
    }
    return fake


def _apply(fake, room="kitchen", **kw):
    return ns.apply(room, run=fake, nmcli="/usr/bin/nmcli", sleep=lambda s: None,
                    nm_wait=0.0, **fake.paths, **kw)


# ─── the host name ────────────────────────────────────────────────────────


@pytest.mark.parametrize("room, name", [
    ("kitchen", "domovoi-kitchen"),
    ("living-room", "domovoi-living-room"),
    ("Living Room", "domovoi-living-room"),
    ("kids_room!", "domovoi-kids-room"),
    ("--weird--", "domovoi-weird"),
    ("", "domovoi-satellite"),
    (None, "domovoi-satellite"),
    ("!!!", "domovoi-satellite"),
    ("x" * 80, "domovoi-" + "x" * 55),
])
def test_the_host_name_is_a_valid_dns_label(room, name):
    got = ns.hostname_for_room(room)
    assert got == name
    assert len(got) <= 63
    assert got[0].isalnum() and got[-1].isalnum()
    assert all(c.isalnum() or c == "-" for c in got)


def test_a_name_somebody_chose_is_never_renamed():
    assert ns.may_rename("raspberrypi", "domovoi-kitchen")
    assert ns.may_rename("", "domovoi-kitchen")
    assert ns.may_rename("domovoi-den", "domovoi-kitchen"), "the room was renamed"
    assert not ns.may_rename("kamrons-pi", "domovoi-kitchen")
    assert not ns.may_rename("domovoi-kitchen", "domovoi-kitchen")


def test_hosts_keeps_every_other_line_and_name():
    text = "127.0.0.1 localhost\n127.0.1.1 raspberrypi extra-alias # mine\n10.0.0.2 nas\n"
    out = ns.rewrite_hosts(text, "raspberrypi", "domovoi-kitchen")
    assert out.splitlines()[0] == "127.0.0.1 localhost"
    assert out.splitlines()[2] == "10.0.0.2 nas"
    assert out.splitlines()[1].split("#")[0].split() == ["127.0.1.1", "domovoi-kitchen", "extra-alias"]
    assert "# mine" in out.splitlines()[1]
    assert ns.rewrite_hosts(out, "domovoi-kitchen", "domovoi-kitchen") is None


def test_hosts_without_the_line_gets_one():
    out = ns.rewrite_hosts("127.0.0.1 localhost", "raspberrypi", "domovoi-kitchen")
    assert out == "127.0.0.1 localhost\n127.0.1.1\tdomovoi-kitchen\n"


# ─── one run, then the next ───────────────────────────────────────────────


def test_the_first_boot_after_the_upgrade_applies_all_three(host):
    doc = _apply(host)
    assert doc["version"] == ns.NET_SETTINGS_VERSION
    assert doc["errors"] == []
    changed = " | ".join(doc["changed"])
    assert "deleted the setup hotspot profile domovoi-setup (u-setup)" in changed
    assert "power saving off on 'Kamber Wifi 2.0'" in changed
    assert "(iw)" in changed
    assert "host name 'raspberrypi' -> 'domovoi-kitchen'" in changed
    assert "avahi-daemon restarted" in changed
    assert host.paths["hostname_path"].read_text().strip() == "domovoi-kitchen"
    assert "127.0.1.1\tdomovoi-kitchen" in host.paths["hosts_path"].read_text()
    assert host.powersave["u-home"] == 2
    assert [p["name"] for p in host.profiles] == ["Kamber Wifi 2.0"]
    # The record the client logs into the core's retained log.
    assert json.loads(host.paths["report_path"].read_text()) == doc


def test_a_second_run_changes_nothing(host):
    _apply(host)
    host.calls.clear()
    hosts_before = host.paths["hosts_path"].read_text()
    doc = _apply(host)
    assert doc["changed"] == [] and doc["errors"] == []
    assert host.paths["hosts_path"].read_text() == hosts_before
    # Nothing that writes ran: only reads.
    assert not host.ran("connection", "modify")
    assert not host.ran("connection", "delete")
    assert not host.ran("hostnamectl")
    assert not host.ran("set", "power_save")
    assert not host.ran("try-restart")


def test_only_the_setup_profile_is_deleted(host):
    """Touch nothing else: an access point the owner made, and the house
    network, are left exactly as they are."""
    host.profiles.append({"name": "my-travel-ap", "uuid": "u-mine", "mode": "ap", "ssid": "Travel"})
    doc = _apply(host)
    assert [c[-1] for c in host.ran("connection", "delete")] == ["u-setup"]
    assert any("my-travel-ap" in s for s in doc["skipped"])
    # And the owner's AP gets no power-save change either: only client profiles.
    assert [c[4] for c in host.ran("connection", "modify")] == ["u-home"]


def test_a_chosen_host_name_is_left_and_said(host):
    host.paths["hostname_path"].write_text("kamrons-pi\n", encoding="utf-8")
    doc = _apply(host)
    assert not host.ran("hostnamectl")
    assert any("kamrons-pi" in s for s in doc["skipped"])
    assert "raspberrypi" in host.paths["hosts_path"].read_text(), "/etc/hosts untouched too"


def test_no_dbus_this_early_still_renames(host):
    host.hostnamectl_ok = False
    doc = _apply(host)
    assert host.paths["hostname_path"].read_text().strip() == "domovoi-kitchen"
    assert host.ran("hostname", "domovoi-kitchen")
    assert doc["errors"] == []


def test_avahi_is_left_alone_when_it_is_not_running(host):
    host.avahi_active = False
    _apply(host)
    assert not host.ran("try-restart")


def test_a_failure_is_recorded_not_raised(host):
    host.undeletable = {"u-setup"}
    doc = _apply(host)
    assert any("u-setup" in e for e in doc["errors"])
    # The rest still ran.
    assert host.powersave["u-home"] == 2


def test_networkmanager_not_answering_skips_profiles_and_does_the_rest(host):
    host.nm_running = False
    doc = _apply(host)
    assert any("NetworkManager did not answer" in e for e in doc["errors"])
    assert not host.ran("connection", "delete")
    assert host.paths["hostname_path"].read_text().strip() == "domovoi-kitchen"
    assert host.radio_ps == "off"


def test_no_room_no_rename(host):
    doc = _apply(host, room=None)
    assert not host.ran("hostnamectl")
    assert any("no room_id" in s for s in doc["skipped"])


def test_an_unexpected_exception_never_escapes(host, monkeypatch):
    monkeypatch.setattr(ns, "set_hostname", lambda *a, **k: 1 / 0)
    doc = _apply(host)
    assert any("unexpected failure" in e for e in doc["errors"])


# ─── what the client reads back ───────────────────────────────────────────


def test_reboot_needed_until_the_boot_step_has_run():
    assert ns.reboot_needed(None, unit_installed=True)
    assert ns.reboot_needed({"version": 0}, unit_installed=True)
    assert ns.reboot_needed({"version": ns.NET_SETTINGS_VERSION}, unit_installed=True) is None
    assert ns.reboot_needed({"version": True}, unit_installed=True), "a bool is not a version"
    # A unit that never runs the boot step is never told to reboot for it.
    assert ns.reboot_needed(None, unit_installed=False) is None


def test_the_room_comes_from_config_toml(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[satellite]\nroom_id = "office"\n', encoding="utf-8")
    assert ns.read_room_id(cfg) == "office"
    assert ns.read_room_id(tmp_path / "missing.toml") is None


# ─── who runs it ──────────────────────────────────────────────────────────


def test_off_the_satellite_it_is_a_no_op(monkeypatch):
    """A developer's machine or a test: never root on a satellite, so the
    step must not even import its way to a command."""
    monkeypatch.setattr(ns, "apply", lambda *a, **k: pytest.fail("ran off a satellite"))
    assert pm.apply_network_settings("kitchen", "boot") is None


def test_every_provisioned_boot_runs_the_step(monkeypatch):
    import inspect

    src = inspect.getsource(pm.main)
    assert "is_provisioned()" in src
    assert 'apply_network_settings(net_settings.read_room_id(CONFIG_PATH), "boot")' in src
    assert src.index("apply_network_settings") < src.index("return run()")
