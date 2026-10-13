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
                 undeletable=(), hostname_path=None, unit_dir=None):
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
        # The radio as NetworkManager reports it, and what a DHCP re-ask
        # was told; systemd's enable links.
        self.link_state = 100
        self.link_conn = "Kamber Wifi 2.0"
        self.modify_ok = True
        self.dhcp_names: list[str] = []
        self.unit_dir = unit_dir
        self.enable_ok = True

    def ok(self, argv, out=""):
        return subprocess.CompletedProcess(argv, 0, out, "")

    def fail(self, argv, rc=1, err="error"):
        return subprocess.CompletedProcess(argv, rc, "", err)

    def __call__(self, argv, **kw):
        argv = list(argv)
        self.calls.append(argv)
        j = " ".join(argv)
        if argv[0].endswith("nmcli"):
            if "GENERAL.STATE,GENERAL.CONNECTION" in j:
                return self.ok(argv, f"GENERAL.STATE:{self.link_state} (whatever)\n"
                                     f"GENERAL.CONNECTION:{self.link_conn}\n")
            if argv[1:3] == ["device", "modify"]:
                if not self.modify_ok:
                    return self.fail(argv, 1, "Error: Reapplying connection failed.")
                self.dhcp_names.append(argv[-1])
                return self.ok(argv)
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
            if "enable" in argv:
                if not self.enable_ok:
                    return self.fail(argv, 1, "Failed to enable unit")
                wants = self.unit_dir / "multi-user.target.wants"
                wants.mkdir(parents=True, exist_ok=True)
                (wants / argv[-1]).write_text("link", encoding="utf-8")
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
    units = tmp_path / "systemd"
    units.mkdir()
    fake = FakeHost(hostname_path=hostname, unit_dir=units)
    fake.paths = {
        "hostname_path": hostname, "hosts_path": hosts,
        "report_path": tmp_path / "home" / ".domovoi" / "net-settings.json",
        "early_report_path": tmp_path / "state" / "net-settings-early.json",
        "unit_dir": units,
    }
    return fake


#: Where the satellite account lives in these tests (rendered into the unit).
SAT_HOME = "/home/kamron"


def _apply(fake, room="kitchen", **kw):
    kw.setdefault("suffix", "")
    kw.setdefault("home", SAT_HOME)
    kw.setdefault("boot_id", "boot-1")
    kw.setdefault("link_wait", 0.0)
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
    assert ns.may_rename("domovoi-den", "domovoi-kitchen", set_before="domovoi-den"),         "the room was renamed, and this step had set the old name"
    assert not ns.may_rename("domovoi-den", "domovoi-kitchen"), "a domovoi-* name a person chose"
    assert not ns.may_rename("domovoi-pi", "domovoi-kitchen", set_before="domovoi-den")
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
    # Renamed after NetworkManager may already have asked DHCP as
    # raspberrypi: asked once more, under the new name.
    assert host.dhcp_names == ["domovoi-kitchen"]
    assert "asked the router for this unit's address again as 'domovoi-kitchen'" in changed
    # And from the next boot the name is set before the network starts.
    assert "installed" in changed and "enabled domovoi-hostname.service" in changed
    assert doc["renamed"] == ["raspberrypi", "domovoi-kitchen"]
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
    assert not host.ran("device", "modify"), "no rename, no second DHCP request"
    assert not host.ran("enable")


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
    """A developer's machine or a test: not root on a Linux satellite, so
    the step must not even import its way to a command."""
    monkeypatch.setattr(ns, "apply", lambda *a, **k: pytest.fail("ran off a satellite"))
    monkeypatch.setattr(pm.os, "geteuid", lambda: 1000, raising=False)
    assert pm.apply_network_settings("kitchen", "boot") is None
    monkeypatch.setattr(pm.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(pm.sys, "platform", "win32")
    assert pm.apply_network_settings("kitchen", "boot") is None


def test_as_root_on_a_satellite_it_runs_with_the_server_record_at_boot(monkeypatch):
    seen = []
    monkeypatch.setattr(ns, "apply", lambda room, **k: seen.append((room, k)) or {})
    monkeypatch.setattr(pm.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(pm.sys, "platform", "linux")
    pm.apply_network_settings("kitchen", "boot")
    pm.apply_network_settings("kitchen", "setup")
    assert seen[0][1]["server_pin"] is ns.refresh_server_pin
    assert seen[1][1]["server_pin"] is None


def test_every_provisioned_boot_runs_the_step(monkeypatch):
    import inspect

    src = inspect.getsource(pm.main)
    assert "is_provisioned()" in src
    assert 'apply_network_settings(net_settings.read_room_id(CONFIG_PATH), "boot")' in src
    assert src.index("apply_network_settings") < src.index("return run()")


# ─── review fixes (2026-10-12) ────────────────────────────────────────────


def test_a_room_with_nothing_usable_gets_a_name_of_its_own():
    """Two rooms named in a script with no ASCII letters must not both be
    domovoi-satellite.local."""
    assert ns.hostname_for_room("Кухня", "a1b2c3") == "domovoi-a1b2c3"
    assert ns.hostname_for_room("!!!", "a1b2c3") == "domovoi-a1b2c3"
    assert ns.hostname_for_room("kitchen", "a1b2c3") == "domovoi-kitchen"


def test_the_suffix_comes_from_the_mac_then_the_machine_id(tmp_path):
    mac = tmp_path / "address"
    mac.write_text("b8:27:eb:12:34:56\n", encoding="utf-8")
    mid = tmp_path / "machine-id"
    mid.write_text("0123456789abcdef0123456789abcdef\n", encoding="utf-8")
    assert ns.unit_suffix(mac, mid) == "123456"
    assert ns.unit_suffix(tmp_path / "none", mid) == "012345"
    assert ns.unit_suffix(tmp_path / "none", tmp_path / "none") == ""


def test_only_a_name_this_step_set_is_renamed_again(host):
    """A person's `domovoi-pi` is theirs; the name this step set for the den
    follows the room to the kitchen."""
    host.paths["hostname_path"].write_text("domovoi-pi\n", encoding="utf-8")
    doc = _apply(host)
    assert not host.ran("hostnamectl")
    assert any("domovoi-pi" in s for s in doc["skipped"])

    host.paths["hostname_path"].write_text("raspberrypi\n", encoding="utf-8")
    _apply(host, room="den")
    assert host.paths["hostname_path"].read_text().strip() == "domovoi-den"
    assert ns.HOSTNAME_RECORD.read_text(encoding="utf-8").strip() == "domovoi-den"
    _apply(host, room="kitchen")
    assert host.paths["hostname_path"].read_text().strip() == "domovoi-kitchen"


def test_power_saving_is_changed_on_the_house_profile_only(host):
    """Not every saved client profile: the one named domovoi-home, or on an
    older card the one in use."""
    host.profiles = [
        {"name": "Kamber Wifi 2.0", "uuid": "u-home", "mode": "infrastructure", "ssid": "Kamber Wifi 2.0"},
        {"name": "Cafe", "uuid": "u-cafe", "mode": "infrastructure", "ssid": "Cafe"},
    ]
    host.powersave = {"u-home": 0, "u-cafe": 0}
    doc = _apply(host)
    assert not host.ran("connection", "modify"), "two client profiles, none in use: no guess"
    assert any("no profile is clearly the house network" in s for s in doc["skipped"])
    host.profiles.append({"name": "domovoi-home", "uuid": "u-dh", "mode": "infrastructure", "ssid": "Kamber"})
    host.powersave["u-dh"] = 0
    _apply(host)
    assert [c[4] for c in host.ran("connection", "modify")] == ["u-dh"]


def test_reboot_needed_stays_until_a_run_without_errors():
    current = {"version": ns.NET_SETTINGS_VERSION}
    assert ns.reboot_needed(dict(current, errors=[]), unit_installed=True) is None
    why = ns.reboot_needed(dict(current, errors=["delete the setup hotspot profile: denied"]),
                           unit_installed=True)
    assert why and "denied" in why and "reboot" in why


# ─── root never writes through a link ─────────────────────────────────────

needs_symlinks = pytest.mark.skipif(
    not hasattr(ns.os, "O_NOFOLLOW") or ns.os.open not in ns.os.supports_dir_fd,
    reason="needs O_NOFOLLOW and dir_fd (Linux); run in the Linux container",
)


@needs_symlinks
def test_a_planted_link_is_never_written_through(tmp_path):
    """The reviewer's attack, 2026-10-12: a link planted under the record's
    name (or its temporary name) pointing at a file root owns. The write
    replaces the LINK and leaves its target alone."""
    target = tmp_path / "helper"
    target.write_text("original\n", encoding="utf-8")
    d = tmp_path / "state"
    d.mkdir()
    (d / "net-settings.json").symlink_to(target)
    (d / f".net-settings.json.tmp{ns.os.getpid()}").symlink_to(target)
    assert ns.write_report({"version": 1}, d / "net-settings.json") is True
    assert target.read_text(encoding="utf-8") == "original\n"
    assert not (d / "net-settings.json").is_symlink()
    assert '"version": 1' in (d / "net-settings.json").read_text(encoding="utf-8")


@needs_symlinks
def test_a_directory_that_is_a_link_is_refused(tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    link = tmp_path / "state"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        ns.safe_write(link, "x", b"y")
    assert not (real / "x").exists()


@needs_symlinks
def test_handing_a_file_to_the_satellite_user_never_follows_a_link(tmp_path, monkeypatch):
    target = tmp_path / "root-helper"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "config.toml"
    link.symlink_to(target)
    calls = []
    monkeypatch.setattr(pm.os, "chown", lambda *a, **k: calls.append((a, k)))
    assert pm.give_to_satellite_user(link) is False
    assert calls == []


def test_handing_a_file_over_does_not_follow_links_in_the_call(tmp_path, monkeypatch):
    """Even between the check and the call: the chown itself is lchown."""
    f = tmp_path / "pairing_token"
    f.write_text("t", encoding="utf-8")
    calls = []

    def chown(*a, **k):
        calls.append(k)

    monkeypatch.setattr(pm.os, "chown", chown, raising=False)
    monkeypatch.setattr(pm.os, "supports_follow_symlinks", {chown}, raising=False)
    assert pm.give_to_satellite_user(f) is True
    assert calls == [{"follow_symlinks": False}]


# ─── root's record of the server address follows a proven move ────────────


@pytest.fixture
def pins(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "server.url").write_text("ws://192.168.0.117:6370\n", encoding="utf-8")
    (etc / "server-identity.json").write_text('{"fingerprint": "SHA256:ours"}', encoding="utf-8")
    cfg = tmp_path / "config.toml"
    cfg.write_text('[satellite]\ndomovoi_url = "ws://192.168.0.50:6370"\n', encoding="utf-8")
    return {"pin_path": etc / "server.url", "identity_path": etc / "server-identity.json",
            "config_path": cfg}


def test_root_follows_a_move_only_after_checking_it_itself(pins):
    asked = []

    def probe(host, *, port, timeout, expected_fingerprint):
        asked.append((host, port, expected_fingerprint))
        return True

    report = ns.Report()
    ns.refresh_server_pin(report, probe=probe, sleep=lambda s: None, **pins)
    assert asked == [("192.168.0.50", 6370, "SHA256:ours")], "root's own pin, root's own probe"
    assert pins["pin_path"].read_text(encoding="utf-8").strip() == "ws://192.168.0.50:6370"
    assert any("192.168.0.117 -> ws://192.168.0.50" in c or "-> ws://192.168.0.50:6370" in c
               for c in report.changed)


def test_an_address_that_cannot_prove_itself_leaves_roots_record(pins):
    report = ns.Report()
    ns.refresh_server_pin(report, probe=lambda *a, **k: False, attempts=2, sleep=lambda s: None, **pins)
    assert pins["pin_path"].read_text(encoding="utf-8").strip() == "ws://192.168.0.117:6370"
    assert report.errors == [], "a server that is down this boot is retried, not an error"
    assert report.skipped


def test_without_roots_identity_pin_nothing_moves(pins):
    pins["identity_path"].unlink()
    report = ns.Report()
    ns.refresh_server_pin(report, probe=lambda *a, **k: pytest.fail("asked"), **pins)
    assert pins["pin_path"].read_text(encoding="utf-8").strip() == "ws://192.168.0.117:6370"


def test_a_current_record_is_left_alone(pins):
    pins["config_path"].write_text('[satellite]\ndomovoi_url = "ws://192.168.0.117:6370"\n',
                                   encoding="utf-8")
    report = ns.Report()
    ns.refresh_server_pin(report, probe=lambda *a, **k: pytest.fail("asked"), **pins)
    assert report.changed == []


def test_the_boot_step_includes_the_record_and_setup_does_not(monkeypatch):
    import inspect

    src = inspect.getsource(pm.apply_network_settings)
    assert 'server_pin=net_settings.refresh_server_pin if when == "boot" else None' in src


# ─── the name before the network (follow-up, 2026-10-12) ──────────────────
#
# On the four real satellites the rename landed after NetworkManager had
# already asked DHCP as `raspberrypi`; the router showed the new name only
# after a second reboot. Now: the host name first, a before-the-network
# unit for every later boot, and one DHCP re-ask when the boot step itself
# is the one that renamed.


def test_the_host_name_goes_before_anything_waits_for_networkmanager(host):
    _apply(host)
    first_nmcli = next(i for i, c in enumerate(host.calls) if c[0].endswith("nmcli"))
    rename = next(i for i, c in enumerate(host.calls) if c[0] == "hostnamectl")
    assert rename < first_nmcli


def test_a_rename_before_the_network_is_not_asked_again(host):
    """The early unit renamed the unit before NetworkManager started, so
    this boot's DHCP request already carried the name."""
    early = ns.apply_before_network("kitchen", run=host, suffix="",
                                    hostname_path=host.paths["hostname_path"],
                                    hosts_path=host.paths["hosts_path"],
                                    report_path=host.paths["early_report_path"])
    assert early["renamed"] == ["raspberrypi", "domovoi-kitchen"]
    # The early run touches nothing but the name: no NetworkManager, no units.
    assert not [c for c in host.calls if c[0].endswith("nmcli") or c[0] == "systemctl"]
    early_doc = json.loads(host.paths["early_report_path"].read_text())
    early_doc["boot_id"] = "boot-1"
    host.paths["early_report_path"].write_text(json.dumps(early_doc), encoding="utf-8")
    host.calls.clear()
    doc = _apply(host)
    assert not host.ran("device", "modify")
    assert not host.ran("hostnamectl")
    changed = " | ".join(doc["changed"])
    assert "before the network started: host name 'raspberrypi' -> 'domovoi-kitchen'" in changed
    assert "avahi-daemon restarted" in changed, "mDNS still follows a rename made this boot"
    assert doc["renamed"] == ["raspberrypi", "domovoi-kitchen"]


def test_an_early_record_from_another_boot_is_not_folded_in(host):
    host.paths["early_report_path"].parent.mkdir(parents=True, exist_ok=True)
    host.paths["early_report_path"].write_text(json.dumps({
        "boot_id": "boot-0", "changed": ["host name 'x' -> 'y'"], "renamed": ["x", "y"], "errors": [],
    }), encoding="utf-8")
    host.paths["hostname_path"].write_text("domovoi-kitchen\n", encoding="utf-8")
    doc = _apply(host)
    assert not any(c.startswith("before the network started") for c in doc["changed"])
    assert not host.ran("try-restart")


def test_dhcp_is_asked_again_only_on_the_house_network(host):
    host.link_conn = "domovoi-setup"
    doc = _apply(host)
    assert host.dhcp_names == []
    assert any("hosting 'domovoi-setup'" in s for s in doc["skipped"])


def test_a_link_that_is_not_up_is_left_to_its_next_connect(host):
    host.link_state = 30
    doc = _apply(host)
    assert host.dhcp_names == []
    assert any("not connected yet" in u for u in doc["unchanged"])
    assert doc["errors"] == []


def test_a_link_still_coming_up_is_waited_for(host):
    states = iter([70, 70, 100])
    real = host.__call__

    def run(argv, **kw):
        if "GENERAL.STATE,GENERAL.CONNECTION" in " ".join(argv):
            host.link_state = next(states)
        return real(argv, **kw)

    doc = ns.apply("kitchen", run=run, nmcli="/usr/bin/nmcli", sleep=lambda s: None, nm_wait=0.0,
                   suffix="", home=SAT_HOME, boot_id="boot-1", link_wait=60.0, **host.paths)
    assert host.dhcp_names == ["domovoi-kitchen"]
    assert doc["errors"] == []


def test_a_failed_re_ask_is_an_error_that_asks_for_a_reboot(host):
    host.modify_ok = False
    doc = _apply(host)
    assert any("ask DHCP again as 'domovoi-kitchen'" in e for e in doc["errors"])
    status, why = ns.assess(doc, unit_state="enabled", boot_id="boot-1", uptime=600.0)
    assert status["state"] == "failed" and why and "reboot" in why


def test_setup_does_not_ask_dhcp_again(host):
    """At setup the reboot that follows carries the new name."""
    _apply(host, when="setup")
    assert host.dhcp_names == []


def test_no_rename_no_second_dhcp_request(host):
    host.paths["hostname_path"].write_text("domovoi-kitchen\n", encoding="utf-8")
    _apply(host)
    assert host.dhcp_names == []


# ─── the before-the-network unit ──────────────────────────────────────────


def _unit_text() -> str:
    return ns.EARLY_UNIT_TEMPLATE.read_text(encoding="utf-8")


def _directives(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[")) or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out.setdefault(k.strip(), []).append(v.strip())
    return out


def test_the_unit_runs_the_name_alone_before_networkmanager():
    d = _directives(_unit_text())
    before = " ".join(d["Before"]).split()
    assert {"NetworkManager.service", "network-pre.target", "domovoi-provisioning.service"} <= set(before)
    assert d["Wants"] == ["network-pre.target"]
    assert d["Type"] == ["oneshot"], "NetworkManager waits for a oneshot to FINISH"
    assert d["User"] == ["root"]
    assert d["ExecStart"] == ["@HOME@/satellite-venv/bin/python -m satellite.net_settings --before-network"]
    assert d["WorkingDirectory"] == ["@HOME@/domovoi"]
    assert d["Environment"] == ["HOME=@HOME@"]
    assert d["ConditionPathExists"] == ["@HOME@/.domovoi/config.toml"]
    assert 0 < int(d["TimeoutStartSec"][0]) <= 30
    assert d["WantedBy"] == ["multi-user.target"]
    # Nothing that could fail the network: no Requires/BindsTo on it.
    assert "Requires" not in d and "BindsTo" not in d and "RequiredBy" not in d


def test_the_unit_is_installed_rendered_and_enabled_once(host):
    report = ns.Report()
    ns.ensure_early_unit(host, report, unit_dir=host.unit_dir, home=SAT_HOME)
    unit = host.unit_dir / ns.EARLY_UNIT_NAME
    assert unit.read_text(encoding="utf-8") == _unit_text().replace("@HOME@", SAT_HOME)
    assert "@HOME@" not in unit.read_text(encoding="utf-8")
    assert [c for c in host.calls if c[0] == "systemctl"] == [
        ["systemctl", "--no-reload", "enable", ns.EARLY_UNIT_NAME]]
    assert len(report.changed) == 2 and report.errors == []
    host.calls.clear()
    again = ns.Report()
    ns.ensure_early_unit(host, again, unit_dir=host.unit_dir, home=SAT_HOME)
    assert again.changed == [] and host.calls == []


def test_a_changed_template_updates_the_unit(host, tmp_path):
    ns.ensure_early_unit(host, ns.Report(), unit_dir=host.unit_dir, home=SAT_HOME)
    newer = tmp_path / "domovoi-hostname.service"
    newer.write_text(_unit_text() + "# a later version\n", encoding="utf-8")
    report = ns.Report()
    ns.ensure_early_unit(host, report, unit_dir=host.unit_dir, home=SAT_HOME, template=newer)
    assert any(c.startswith("updated") for c in report.changed)
    assert (host.unit_dir / ns.EARLY_UNIT_NAME).read_text(encoding="utf-8").endswith("# a later version\n")


@pytest.mark.parametrize("home", [None, "relative/home", "/home/a b", "/home/x\nExecStartPre=/bin/sh",
                                  "/home/../etc"])
def test_no_unit_without_a_home_it_can_trust(host, monkeypatch, home):
    monkeypatch.setattr(ns, "satellite_home", lambda: None)
    report = ns.Report()
    ns.ensure_early_unit(host, report, unit_dir=host.unit_dir, home=home)
    assert not (host.unit_dir / ns.EARLY_UNIT_NAME).exists()
    assert report.skipped and report.errors == []


def test_an_enable_that_fails_is_an_error(host):
    host.enable_ok = False
    report = ns.Report()
    ns.ensure_early_unit(host, report, unit_dir=host.unit_dir, home=SAT_HOME)
    assert any("enable domovoi-hostname.service" in e for e in report.errors)


@needs_symlinks
def test_a_link_planted_as_the_unit_is_replaced_not_followed(host, tmp_path):
    target = tmp_path / "root-file"
    target.write_text("original\n", encoding="utf-8")
    (host.unit_dir / ns.EARLY_UNIT_NAME).symlink_to(target)
    report = ns.Report()
    ns.ensure_early_unit(host, report, unit_dir=host.unit_dir, home=SAT_HOME)
    assert target.read_text(encoding="utf-8") == "original\n"
    assert not (host.unit_dir / ns.EARLY_UNIT_NAME).is_symlink()


def test_the_early_run_never_holds_the_network_up(monkeypatch):
    """Off a satellite, or not root: exit 0, so the unit never 'fails'."""
    monkeypatch.setattr(ns, "apply_before_network", lambda *a, **k: pytest.fail("ran"))
    monkeypatch.setattr(ns.sys, "platform", "win32")
    assert ns.main(["--before-network"]) == 0
    monkeypatch.setattr(ns.sys, "platform", "linux")
    monkeypatch.setattr(ns.os, "geteuid", lambda: 1000, raising=False)
    assert ns.main(["--before-network"]) == 0


def test_the_early_run_survives_its_own_failure(monkeypatch):
    monkeypatch.setattr(ns.sys, "platform", "linux")
    monkeypatch.setattr(ns.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(ns, "read_room_id", lambda *a: "kitchen")
    monkeypatch.setattr(ns, "apply_before_network", lambda *a, **k: 1 / 0)
    assert ns.main(["--before-network"]) == 0


def test_the_early_run_gives_hostnamectl_a_short_leash(host):
    seen = []
    real = host.__call__

    def run(argv, **kw):
        if argv[0] == "hostnamectl":
            seen.append(kw.get("timeout"))
        return real(argv, **kw)

    ns.apply_before_network("kitchen", run=run, suffix="",
                            hostname_path=host.paths["hostname_path"],
                            hosts_path=host.paths["hosts_path"],
                            report_path=host.paths["early_report_path"])
    assert seen and seen[0] <= 10


def test_a_manual_sudo_run_finds_the_satellite_home(tmp_path, monkeypatch):
    """Under sudo $HOME is root's: the config and the unit's home come from
    the account the code tree lives in."""
    sat = tmp_path / "sat"
    (sat / "satellite-venv").mkdir(parents=True)
    (sat / "domovoi" / "satellite").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "root"))
    monkeypatch.setattr(ns, "__file__", str(sat / "domovoi" / "satellite" / "net_settings.py"))
    assert ns.satellite_home() == sat
    monkeypatch.setenv("HOME", str(sat))
    assert ns.satellite_home() == sat
    monkeypatch.setattr(ns, "__file__", str(tmp_path / "x" / "y" / "z.py"))
    monkeypatch.setenv("HOME", str(tmp_path / "root"))
    assert ns.satellite_home() is None


# ─── what the health sample says ──────────────────────────────────────────


def _doc(**kw):
    d = {"version": ns.NET_SETTINGS_VERSION, "ts": 1760000000.0, "boot_id": "b1", "when": "boot",
         "hostname": "domovoi-office", "changed": [], "skipped": [], "errors": []}
    d.update(kw)
    return d


@pytest.mark.parametrize("doc, state, words", [
    (_doc(changed=["host name 'raspberrypi' -> 'domovoi-office'"]), "applied", "-> 'domovoi-office'"),
    (_doc(), "unchanged", "nothing to change (host name domovoi-office)"),
    (_doc(skipped=["access-point profile 'x' is not Domovoi's",
                   "host name 'kamrons-pi' looks chosen by a person; not renaming it to 'domovoi-office'"],
          changed=["Wi-Fi power saving off on 'home'"]),
     "skipped", "kamrons-pi"),
    (_doc(skipped=["host name: no room_id in config.toml"]), "skipped", "no room_id"),
    (_doc(errors=["a", "b"], changed=["c"]), "failed", "a (and 1 more)"),
])
def test_the_outcome_of_this_boot(doc, state, words):
    status, why = ns.assess(doc, unit_state="enabled", boot_id="b1", uptime=900.0)
    assert status["state"] == state and words in status["detail"]
    assert status["when"] == "boot" and status["version"] == ns.NET_SETTINGS_VERSION
    assert (why is not None) == (state == "failed")


def test_not_run_since_the_code_arrived_asks_for_a_reboot():
    """Upgraded (the client restarted), not rebooted: the record is the
    older version's, or there is none."""
    for doc in (None, _doc(version=1)):
        status, why = ns.assess(doc, unit_state="enabled", boot_id="b2", uptime=3600.0)
        assert status["state"] == "not_run" and why and "reboot" in why


def test_right_after_boot_it_is_still_running_not_missing():
    status, why = ns.assess(_doc(version=1, boot_id="b1"), unit_state="enabled",
                            boot_id="b2", uptime=40.0)
    assert status["state"] == "running" and why is None
    status, why = ns.assess(_doc(boot_id="b1"), unit_state="enabled", boot_id="b2", uptime=40.0)
    assert status["state"] == "running" and why is None


def test_a_boot_where_it_did_not_run_says_so_without_a_reboot():
    """The settings at this version were applied at an earlier boot; this
    boot the step never wrote its record (it crashed, or was disabled)."""
    status, why = ns.assess(_doc(boot_id="b1", changed=["x"]), unit_state="enabled",
                            boot_id="b2", uptime=900.0)
    assert status["state"] == "not_run" and "did not run this boot" in status["detail"]
    assert why is None


def test_nothing_runs_it_says_so_and_never_asks_for_a_useless_reboot():
    for unit_state, words in (("missing", "no domovoi-provisioning.service"),
                              ("disabled", "not enabled")):
        status, why = ns.assess(None, unit_state=unit_state, boot_id="b1", uptime=900.0)
        assert status["state"] == "not_installed" and words in status["detail"]
        assert why is None
    # A hand-run record is still reported for what it did.
    status, _ = ns.assess(_doc(when="manual", boot_id="b0", changed=["x"]), unit_state="missing",
                          boot_id="b1", uptime=900.0)
    assert status["state"] == "applied"


def test_the_detail_is_short():
    status, _ = ns.assess(_doc(changed=["x" * 500]), unit_state="enabled", boot_id="b1", uptime=900.0)
    assert len(status["detail"]) <= 200
    assert status["state"] in ns.STEP_STATES


def test_the_unit_state_reads_the_enable_link(tmp_path, monkeypatch):
    unit = tmp_path / "domovoi-provisioning.service"
    wants = tmp_path / "multi-user.target.wants" / "domovoi-provisioning.service"
    monkeypatch.setattr(ns, "PROVISIONING_UNIT", unit)
    monkeypatch.setattr(ns, "PROVISIONING_WANTS", wants)
    assert ns.boot_step_unit_state() == "missing"
    unit.write_text("[Unit]\n", encoding="utf-8")
    assert ns.boot_step_unit_state() == "disabled"
    wants.parent.mkdir()
    wants.write_text("link", encoding="utf-8")
    assert ns.boot_step_unit_state() == "enabled"


# ─── a new card: the name before setup joins the Wi-Fi ────────────────────


def test_setup_names_the_unit_before_it_joins_the_wifi(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(pm, "CONFIG_DIR", tmp_path / ".domovoi")
    monkeypatch.setattr(pm, "CONFIG_PATH", tmp_path / ".domovoi" / "config.toml")
    monkeypatch.setattr(pm, "PAIRING_TOKEN_PATH", tmp_path / ".domovoi" / "pairing_token")
    monkeypatch.setattr(pm, "give_to_satellite_user", lambda p: True)
    monkeypatch.setattr(pm, "_write_not_approved", lambda: None)
    monkeypatch.setattr(pm, "apply_wifi", lambda *a, **k: order.append("join") or (True, None))
    monkeypatch.setattr(pm, "resolve_auto_url", lambda u: u)
    monkeypatch.setattr(pm, "write_root_server_pin", lambda u: True)
    monkeypatch.setattr(pm, "sync_time_with_server", lambda *a, **k: None)

    class T:
        def clear_provision(self):
            order.append("clear")

    payload = {"room_id": "den", "domovoi_url": "ws://127.0.0.1:6370", "device_profile": "pi_xvf3800",
               "pairing_token": "t" * 64, "wifi": {"ssid": "Home", "psk": "hunter2hunter2"}}
    ok, _ = pm.apply_provision(payload, transport=T(), wifi_attempts=1, wifi_join_timeout=1,
                               before_join=lambda room: order.append(f"name:{room}"))
    assert ok and order == ["clear", "name:den", "join"]


def test_setup_passes_the_naming_step_and_it_is_a_no_op_off_a_satellite(monkeypatch):
    import inspect

    assert "before_join=name_before_join" in inspect.getsource(pm.run)
    monkeypatch.setattr(ns, "set_hostname", lambda *a, **k: pytest.fail("renamed this machine"))
    monkeypatch.setattr(pm.os, "geteuid", lambda: 1000, raising=False)
    pm.name_before_join("den")
    monkeypatch.setattr(pm.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(pm.sys, "platform", "win32")
    pm.name_before_join("den")
