"""The setup network's radio settings, and what the card says about it.

Found on hardware (2026-10-17): a Samsung phone said "Couldn't connect to
network" to a Pi's setup hotspot every time, with the password typed right.
The hotspot was `nmcli device wifi hotspot`, which leaves PMF optional; the
Pi's brcmfmac driver breaks PMF in AP mode, and Samsung phones take it when
offered. The card itself said nothing: device-info.json still read
`stage1-done`, `error: null`.

These tests pin the nmcli arguments. Only a real phone against a real Pi
can prove the PMF fix itself.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from satellite import portal_transport as pt
from satellite import provisioning_mode as pm
from satellite import provisioning_protocol as proto
from satellite import setup_report

NONCE = "a1b2c3d4e5f60718"
PSK = "unit-test-key"
DEVICE_STATUS = "lo:loopback\np2p-dev-wlan0:wifi-p2p\nwlan0:wifi\n"
ACTIVE = "lo\ndomovoi-setup\n"


def _run(calls, *, scan="HomeNet:71:6\nNeighbour:40:6\nFar:20:11\n",
         fail_on=None):
    def run(cmd, **kw):
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if fail_on and fail_on in joined:
            return subprocess.CompletedProcess(cmd, 1, stdout="",
                                               stderr="Error: nmcli said no.")
        if "--active" in joined:
            out = ACTIVE
        elif "wifi list" in joined:
            out = scan
        elif "device status" in joined:
            out = DEVICE_STATUS
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    return run


def _transport(calls, tmp_path, **kw):
    run_kw = {k: kw.pop(k) for k in ("scan", "fail_on") if k in kw}
    return pt.PortalTransport(
        ap_ssid="Domovoi-Setup-A4F2", ap_psk=PSK, device_profile="xvf3800_usb",
        ip="127.0.0.1", bind_host="127.0.0.1", port=0,
        state_dir=tmp_path / "state", run=_run(calls, **run_kw), **kw,
    )


def _info(**over):
    info = proto.build_device_info(
        nonce=NONCE, mac="b8:27:eb:aa:bb:cc", board="raspberry_pi_zero_2_w",
        model="Raspberry Pi Zero 2 W", profiles_supported=["xvf3800_usb"],
    )
    info.update(over)
    return info


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    monkeypatch.setattr(pt.shutil, "which", lambda n: f"/usr/bin/{n}")


@pytest.fixture
def card(satellite_paths_in_tmp):
    """A prepared card's boot partition, as stage 1 leaves it."""
    folder = satellite_paths_in_tmp / "boot" / "domovoi"
    folder.mkdir(parents=True)
    path = folder / "device-info.json"
    path.write_text(json.dumps({
        "domovoi_setup": 1, "nonce": "firstboot", "status": "awaiting_provision",
        "step": "stage1-done", "error": None, "ap_ssid": "Domovoi-Setup-A4F2",
    }), encoding="utf-8")
    return path


def _created(calls) -> list[str]:
    return next(c for c in calls if c[1:3] == ["connection", "add"])


# ─── the nmcli arguments ──────────────────────────────────────────────────


def test_the_profile_is_wpa2_ccmp_with_pmf_off_on_a_fixed_24ghz_channel(tmp_path):
    calls = []
    t = _transport(calls, tmp_path)
    t.expose(_info())
    try:
        assert _created(calls)[1:] == [
            "connection", "add", "type", "wifi", "ifname", "wlan0",
            "con-name", "domovoi-setup",
            "connection.autoconnect", "no",
            "802-11-wireless.ssid", "Domovoi-Setup-A4F2",
            "802-11-wireless.mode", "ap",
            "802-11-wireless.band", "bg",
            "802-11-wireless.channel", "1",       # 6 and 11 are busier here
            "ipv4.method", "shared",
            "ipv4.addresses", "127.0.0.1/24",
            "ipv6.method", "ignore",
            "802-11-wireless-security.key-mgmt", "wpa-psk",
            "802-11-wireless-security.proto", "rsn",
            "802-11-wireless-security.pairwise", "ccmp",
            "802-11-wireless-security.group", "ccmp",
            "802-11-wireless-security.pmf", "disable",
            "802-11-wireless-security.psk", PSK,
        ]
    finally:
        t.withdraw()


def test_one_profile_one_activation_no_hotspot_shortcut(tmp_path):
    """The old up -> modify -> up flapped the setup network."""
    calls = []
    t = _transport(calls, tmp_path)
    t.expose(_info())
    t.withdraw()
    verbs = [" ".join(c[1:3]) for c in calls if c[1:2] == ["connection"]]
    assert verbs.index("connection delete") < verbs.index("connection add")
    assert verbs.count("connection up") == 1
    assert "connection modify" not in verbs
    assert not any("hotspot" in " ".join(c) for c in calls)


def test_a_missing_old_profile_is_not_an_error(tmp_path):
    """Delete-before-create fails on a fresh card (nothing to delete)."""
    calls = []
    t = _transport(calls, tmp_path, fail_on="connection delete")
    t.expose(_info())
    t.withdraw()
    assert _created(calls)


@pytest.mark.parametrize("rows, expected", [
    ([], 6),                                               # nothing seen
    ([{"ssid": "a", "signal": "80", "chan": "6"}], 1),     # 6 busy
    ([{"ssid": "a", "signal": "80", "chan": "1"},
      {"ssid": "b", "signal": "80", "chan": "6"}], 11),
    ([{"ssid": "a", "signal": "90", "chan": "36"}], 6),    # 5 GHz costs nothing
    ([{"ssid": "a", "signal": "?", "chan": "x"}], 6),      # garbage ignored
])
def test_the_quietest_of_1_6_11_is_chosen(rows, expected):
    assert pt.choose_channel(rows) == expected


def test_only_channels_legal_everywhere_are_ever_chosen():
    """1, 6 and 11 are legal in every regulatory domain, world (00)
    included, so the wireless country can't make the channel illegal."""
    assert set(pt.AP_CHANNELS) == {1, 6, 11}
    for chan in range(1, 15):
        rows = [{"ssid": "x", "signal": "99", "chan": str(chan)}]
        assert pt.choose_channel(rows) in (1, 6, 11)


def test_the_scan_reads_channels_and_keeps_colons_in_names():
    calls = []
    rows = pt.scan_rows(run=_run(calls, scan="My\\:Net:70:11\n:50:6\n"),
                        iface="wlan0")
    assert rows[0] == {"ssid": "My:Net", "signal": "70", "chan": "11"}
    assert "SSID,SIGNAL,CHAN" in calls[0]
    assert pt.scan_networks(run=_run([], scan="My\\:Net:70:11\n"),
                            iface="wlan0") == ["My:Net"]


# ─── overrides from ap.json on the boot partition ────────────────────────


def test_ap_json_can_retune_the_radio_without_a_rebuild(tmp_path):
    calls = []
    t = _transport(calls, tmp_path, radio={"channel": "11", "pmf": "optional"})
    t.expose(_info())
    t.withdraw()
    args = _created(calls)
    assert args[args.index("802-11-wireless.channel") + 1] == "11"
    assert args[args.index("802-11-wireless-security.pmf") + 1] == "optional"


@pytest.mark.parametrize("radio", [
    {"channel": "13"}, {"channel": "6; reboot"}, {"pmf": "required"},
    {"pmf": "$(id)"}, {"band": "a"},
])
def test_an_override_outside_the_allowed_values_never_reaches_nmcli(tmp_path, radio):
    calls = []
    t = _transport(calls, tmp_path, radio=radio)
    t.expose(_info())
    t.withdraw()
    args = _created(calls)
    assert args[args.index("802-11-wireless.channel") + 1] in ("1", "6", "11")
    assert args[args.index("802-11-wireless-security.pmf") + 1] == "disable"
    assert args[args.index("802-11-wireless.band") + 1] == "bg"
    for value in radio.values():
        assert value not in args


def test_the_boot_ap_json_carries_overrides_to_the_portal(monkeypatch, tmp_path):
    boot = tmp_path / "bootfs"
    (boot / "domovoi").mkdir(parents=True)
    (boot / "domovoi" / "ap.json").write_text(json.dumps(
        {"ssid": "Domovoi-Setup-A4F2", "psk": PSK, "channel": 11, "pmf": "optional"}))
    monkeypatch.setattr(pm, "BOOT_DIRS", (boot,))
    monkeypatch.setattr(pm, "CONFIG_DIR", tmp_path / "cfg")
    transport = pm._default_transport()
    assert isinstance(transport, pt.PortalTransport)
    assert transport.radio == {"band": "bg", "channel": "11", "pmf": "optional"}


def test_a_plain_ap_json_gets_the_defaults(monkeypatch, tmp_path):
    boot = tmp_path / "bootfs"
    (boot / "domovoi").mkdir(parents=True)
    (boot / "domovoi" / "ap.json").write_text(json.dumps(
        {"ssid": "Domovoi-Setup-A4F2", "psk": PSK}))
    monkeypatch.setattr(pm, "BOOT_DIRS", (boot,))
    monkeypatch.setattr(pm, "CONFIG_DIR", tmp_path / "cfg")
    assert pm._default_transport().radio == {"band": "bg", "channel": "auto",
                                             "pmf": "disable"}


# ─── a card that can't run a hotspot says so, once ───────────────────────


def test_missing_packages_are_named_before_any_radio_work(monkeypatch, tmp_path):
    monkeypatch.setattr(pt.shutil, "which",
                        lambda n: None if n == "dnsmasq" else f"/usr/bin/{n}")
    monkeypatch.setattr(pt, "_SBIN_DIRS", ())
    calls = []
    t = _transport(calls, tmp_path)
    with pytest.raises(pt.MissingPrerequisite, match="dnsmasq-base") as e:
        t.expose(_info())
    assert e.value.permanent is True
    assert calls == []                 # no scan, no profile, no flapping
    assert t._server is None


def test_a_binary_only_in_sbin_counts(monkeypatch, tmp_path):
    """shutil.which() depends on the caller's PATH; /usr/sbin may not be on it."""
    sbin = tmp_path / "sbin"
    sbin.mkdir()
    (sbin / "dnsmasq").write_text("")
    monkeypatch.setattr(pt.shutil, "which",
                        lambda n: None if n == "dnsmasq" else f"/usr/bin/{n}")
    monkeypatch.setattr(pt, "_SBIN_DIRS", (str(sbin),))
    pt.check_prerequisites()


class _FailingTransport:
    def __init__(self, exc_factory, succeed_after=None):
        self.exc_factory = exc_factory
        self.succeed_after = succeed_after
        self.exposed = 0
        self.withdrawn = 0

    def expose(self, info):
        self.exposed += 1
        if self.succeed_after is None or self.exposed <= self.succeed_after:
            raise self.exc_factory()

    def wait_for_provision(self, nonce):
        return None

    def withdraw(self):
        self.withdrawn += 1


@pytest.fixture
def home(tmp_path, monkeypatch):
    cfg = tmp_path / ".domovoi"
    monkeypatch.setattr(pm, "CONFIG_DIR", cfg)
    monkeypatch.setattr(pm, "CONFIG_PATH", cfg / "config.toml")
    monkeypatch.setattr(pm, "STATE_FILE", cfg / "provisioning_state.json")
    monkeypatch.setattr(pm, "read_board", lambda: ("b", "B"))
    monkeypatch.setattr(pm, "read_wlan_mac", lambda: None)
    sleeps: list[float] = []
    monkeypatch.setattr(pm.time, "sleep", sleeps.append)
    leds: list[str] = []
    monkeypatch.setattr(pm, "setup_status", lambda state, *a: leds.append(state))
    return {"sleeps": sleeps, "leds": leds}


def test_a_card_missing_its_packages_parks_with_a_red_ring_and_a_reason(home, card):
    t = _FailingTransport(lambda: pt.MissingPrerequisite(
        "this card is missing dnsmasq-base, which the setup network needs."))
    assert pm.run(t, max_loops=5) == pm.EXIT_CANNOT_SET_UP
    assert t.exposed == 1               # no retry loop, no flapping
    assert home["leds"] == ["startup-failed"]
    doc = json.loads(card.read_text(encoding="utf-8"))
    assert doc["step"] == "setup-network-unavailable"
    assert "dnsmasq-base" in doc["error"]


def test_the_unit_does_not_restart_a_card_that_cannot_set_up():
    unit = Path(pm.__file__).parent / "scripts" / "domovoi-provisioning.service"
    lines = unit.read_text(encoding="utf-8").splitlines()
    assert f"RestartPreventExitStatus={pm.EXIT_CANNOT_SET_UP}" in lines


def test_any_other_failure_is_retried_in_process_and_slowing(home, card):
    t = _FailingTransport(lambda: RuntimeError("activating the setup AP failed"),
                          succeed_after=3)
    pm.run(t, max_loops=10)
    assert t.exposed == 4
    assert home["sleeps"] == [15.0, 30.0, 60.0]
    assert home["leds"][:3] == ["startup-failed"] * 3
    assert home["leds"][3] == "ready-to-setup"


def test_the_retry_wait_is_capped(home):
    t = _FailingTransport(lambda: RuntimeError("no"))
    pm.run(t, max_loops=8)
    assert max(home["sleeps"]) == 300.0


def test_a_failed_portal_reports_on_the_card(home, card, tmp_path):
    calls = []
    t = _transport(calls, tmp_path, fail_on="connection up")
    pm.run(t, max_loops=1)
    doc = json.loads(card.read_text(encoding="utf-8"))
    assert doc["step"] == "setup-network-failed"
    assert "activating the setup AP" in doc["error"]
    net = doc["setup_network"]
    assert net["state"] == "failed" and net["attempts"] == 1
    assert "will be retried" in net["line"]
    assert PSK not in card.read_text(encoding="utf-8")
    # The half-made profile is taken down again.
    assert any(c[1:3] == ["connection", "down"] for c in calls)


# ─── what the card says while the hotspot is up ───────────────────────────


def test_a_working_portal_writes_its_state_onto_the_card(card, tmp_path):
    calls = []
    t = _transport(calls, tmp_path)
    t.expose(_info())
    t.withdraw()
    raw = card.read_text(encoding="utf-8")
    doc = json.loads(raw)
    assert doc["step"] == "setup-network-up" and doc["error"] is None
    assert doc["domovoi_setup"] == 1 and doc["ap_ssid"] == "Domovoi-Setup-A4F2"
    net = doc["setup_network"]
    assert net["state"] == "up"
    assert (net["band"], net["channel"], net["pmf"]) == ("bg", 1, "disable")
    assert net["phones_joined"] == 0
    assert "no phone has joined it yet" in net["line"]
    assert PSK not in raw
    assert "\r\n" not in raw


def test_phones_that_got_an_address_are_counted(card, tmp_path, monkeypatch):
    leases = tmp_path / "nm"
    leases.mkdir()
    (leases / "dnsmasq-wlan0.leases").write_text(
        "1760000000 aa:bb:cc:dd:ee:01 192.168.4.10 Galaxy-S23 *\n"
        "1760000000 aa:bb:cc:dd:ee:02 192.168.4.11 * *\n")
    monkeypatch.setattr(pt, "LEASES_DIR", leases)
    t = _transport([], tmp_path)
    t.expose(_info())
    t.withdraw()
    net = json.loads(card.read_text(encoding="utf-8"))["setup_network"]
    assert net["phones_joined"] == 2
    assert "2 phones have joined it" in net["line"]
    assert "aa:bb" not in card.read_text(encoding="utf-8")   # count only


def test_the_watcher_records_a_phone_that_joins_later(card, tmp_path, monkeypatch):
    leases = tmp_path / "nm"
    leases.mkdir()
    monkeypatch.setattr(pt, "LEASES_DIR", leases)
    monkeypatch.setattr(pt, "_REPORT_EVERY_SEC", 0.02)
    t = _transport([], tmp_path)
    t.expose(_info())
    try:
        (leases / "dnsmasq-wlan0.leases").write_text(
            "1760000000 aa:bb:cc:dd:ee:01 192.168.4.10 phone *\n")
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            net = json.loads(card.read_text(encoding="utf-8"))["setup_network"]
            if net["phones_joined"] == 1:
                break
            time.sleep(0.02)
        assert net["phones_joined"] == 1
    finally:
        t.withdraw()
    assert t._watcher is None


def test_a_failed_join_reason_stays_on_the_card_when_the_portal_returns(card, tmp_path):
    t = _transport([], tmp_path)
    t.expose(_info(status="wifi_failed",
                   error="wifi join failed for 'HomeNet': Secrets were required"))
    t.withdraw()
    raw = card.read_text(encoding="utf-8")
    doc = json.loads(raw)
    assert doc["status"] == "wifi_failed"
    assert doc["error"] == setup_report.JOIN_FAILED
    assert doc["setup_network"]["last_error"] == setup_report.JOIN_FAILED
    # The house network's name stays off the card; the portal page keeps it.
    assert "HomeNet" not in raw
    assert "HomeNet" in t.device_info["error"]


def test_no_card_no_file(satellite_paths_in_tmp, tmp_path):
    """A hand-built unit (no domovoi folder on its boot partition) never
    grows one."""
    t = _transport([], tmp_path)
    t.expose(_info())
    t.withdraw()
    assert not (satellite_paths_in_tmp / "boot").exists()
    assert setup_report.update(top={"x": 1}) is False


def test_an_unwritable_card_never_stops_setup(card, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("read-only file system")
    monkeypatch.setattr(setup_report.os, "replace", boom)
    t = _transport([], tmp_path)
    t.expose(_info())          # no raise
    t.withdraw()


def test_a_successful_setup_is_recorded(home, card, monkeypatch):
    class _Done(_FailingTransport):
        def __init__(self):
            super().__init__(lambda: None, succeed_after=0)

        def wait_for_provision(self, nonce):
            return {"room_id": "den"}

        def dispose(self):
            pass

        def reboot(self):
            pass

    monkeypatch.setattr(pm, "apply_provision", lambda *a, **k: (True, None))
    assert pm.run(_Done(), max_loops=2, after_setup=lambda *a: None) == 0
    doc = json.loads(card.read_text(encoding="utf-8"))
    assert (doc["status"], doc["step"], doc["error"]) == (
        "provisioned", "joined-home-wifi", None)


def test_each_attempt_first_marks_the_card_as_starting(home, card):
    """Otherwise a previous boot's failure reads as current for the whole
    time this attempt waits for the radio (seen in the container run)."""
    seen = []

    class _Peek(_FailingTransport):
        def expose(self, info):
            seen.append(json.loads(card.read_text(encoding="utf-8"))["step"])
            super().expose(info)

        def network_report(self, state, error):
            return {"state": state, "last_error": error}

    t = _Peek(lambda: RuntimeError("no"), succeed_after=1)
    pm.run(t, max_loops=3)
    assert seen == ["setup-network-starting", "setup-network-starting"]


def test_no_radio_fails_after_one_wait_without_scanning(monkeypatch, tmp_path):
    monkeypatch.setattr(pt, "_IFACE_WAIT_SEC", 0.05)
    monkeypatch.setattr(pt, "_IFACE_POLL_SEC", 0.01)
    monkeypatch.setattr(pt.time, "sleep", lambda s: None)
    calls = []

    def run(cmd, **kw):
        calls.append(" ".join(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="lo:loopback\n", stderr="")

    t = _transport([], tmp_path)
    t.run = run
    with pytest.raises(RuntimeError, match="no Wi-Fi interface"):
        t.expose(_info())
    assert not any("wifi list" in c for c in calls)
    assert t._server is None


# ─── review fixes (2026-10-17) ────────────────────────────────────────────


def test_a_failed_join_never_puts_the_house_network_on_the_card(home, card, monkeypatch):
    """The join error names the network ({ssid!r}); through every report a
    failed join makes - the join itself, the next attempt's start, the
    portal coming back - the card gets generic words only."""
    snapshots = []

    class _Retry(_FailingTransport):
        def __init__(self):
            super().__init__(lambda: None, succeed_after=0)
            self.ap_ssid = "Domovoi-Setup-A4F2"

        def expose(self, info):
            snapshots.append(card.read_text(encoding="utf-8"))
            super().expose(info)

        def wait_for_provision(self, nonce):
            return {"room_id": "den"}

        def network_report(self, state, error):
            return {"state": state, "last_error": error,
                    "line": setup_report.line(state=state, error=error)}

    monkeypatch.setattr(pm, "apply_provision", lambda *a, **k: (
        False, "wifi join failed for 'Kitchen Router 5G': Secrets were required"))
    pm.run(_Retry(), max_loops=2)
    snapshots.append(card.read_text(encoding="utf-8"))
    assert len(snapshots) == 3
    for raw in snapshots:
        assert "Kitchen Router" not in raw
    doc = json.loads(snapshots[-1])
    assert doc["error"] == setup_report.JOIN_FAILED


@pytest.mark.parametrize("err, expected", [
    ("wifi join failed for 'HomeNet': Secrets were required", setup_report.JOIN_FAILED),
    ("wifi join timed out for 'HomeNet'", setup_report.JOIN_TIMED_OUT),
    ("wifi network name refused: 'HomeNet' is too long", setup_report.JOIN_REFUSED),
    ("wifi tooling unavailable: FileNotFoundError", setup_report.JOIN_NO_TOOLS),
    # Not a password problem, so not called one (2026-10-22).
    ("anything else naming 'HomeNet'", "couldn't join your home Wi-Fi"),
])
def test_a_join_error_becomes_generic_words(err, expected):
    assert setup_report.card_error("wifi_failed", err) == expected
    assert "HomeNet" not in expected


def test_setup_network_errors_pass_through_and_none_stays_none():
    assert setup_report.card_error("awaiting_provision", "activating the setup AP failed")         == "activating the setup AP failed"
    assert setup_report.card_error("wifi_failed", None) is None


def test_a_card_file_that_does_not_parse_is_left_alone(card):
    card.write_text('{"domovoi_setup": 1, "status": "bootstr', encoding="utf-8")
    before = card.read_bytes()
    assert setup_report.update(top={"step": "x"}, network={"state": "up"}) is False
    assert card.read_bytes() == before


def test_a_card_folder_without_the_file_gets_one(card):
    card.unlink()
    assert setup_report.update(top={"step": "x"}) is True
    assert json.loads(card.read_text(encoding="utf-8")) == {"step": "x"}


def test_concurrent_writers_never_tear_the_file(card):
    """The state machine and the phone counter both write; they share one
    temporary name, so they take turns."""
    import threading

    results, errors = [], []

    def writer(n):
        try:
            for i in range(15):
                results.append(setup_report.update(
                    top={"step": f"w{n}-{i}"}, network={"phones_joined": i}))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and all(results) and len(results) == 90
    doc = json.loads(card.read_text(encoding="utf-8"))
    assert doc["domovoi_setup"] == 1 and doc["step"].startswith("w")


def test_nmcli_error_text_is_scrubbed_before_it_reaches_the_card(home, card, tmp_path):
    calls = []
    t = _transport(calls, tmp_path)

    def run(cmd, **kw):
        joined = " ".join(cmd)
        if "connection up" in joined:
            return subprocess.CompletedProcess(
                cmd, 4, stdout="", stderr=f"Error: secrets {PSK} rejected")
        return _run(calls)(cmd, **kw)

    t.run = run
    pm.run(t, max_loops=1)
    raw = card.read_text(encoding="utf-8")
    assert PSK not in raw
    assert "<psk>" in json.loads(raw)["error"]


def test_an_nmcli_timeout_never_prints_the_command(tmp_path):
    """TimeoutExpired's own text is the whole argv, the setup key with it."""
    t = _transport([], tmp_path)

    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 30)

    t.run = run
    with pytest.raises(RuntimeError) as e:
        t._nmcli(t.ap_profile_args("wlan0"), "creating the setup AP profile")
    assert PSK not in str(e.value)
    assert "timed out after 30s" in str(e.value)
