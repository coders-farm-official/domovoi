"""The wpa_supplicant fallback in apply_wifi (taken only when nmcli is
absent), and the network-name rule both join paths apply (SAT-4).

The block appended to wpa_supplicant.conf is built here, not taken from
wpa_passphrase: the name goes in as hex so no byte of it is ever read as
syntax, the key goes in derived so the passphrase itself is never in the
file, and a name that could not be carried safely is refused before
anything is written.
"""

from __future__ import annotations

import hashlib
import subprocess

import pytest

from satellite import provisioning_mode as pm
from satellite import provisioning_protocol as proto

PSK = "hunter2hunter2"


class FakeWpa:
    """No nmcli; wpa_cli reports COMPLETED on the first status call."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        if cmd[:2] == ["wpa_cli", "-i"] and cmd[-1] == "status":
            return subprocess.CompletedProcess(cmd, 0, stdout=b"wpa_state=COMPLETED\n", stderr=b"")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")


@pytest.fixture
def no_nmcli(monkeypatch, tmp_path):
    monkeypatch.setattr(pm.shutil, "which", lambda n: None)
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    conf = tmp_path / "wpa_supplicant.conf"
    conf.write_text("ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev\nupdate_config=1\n",
                    encoding="utf-8")
    monkeypatch.setattr(pm, "WPA_SUPPLICANT_CONF", conf)
    return conf


def _block(conf) -> str:
    text = conf.read_text(encoding="utf-8")
    assert text.count("network={") == 1
    return text[text.index("network={"):]


# ─── the block ────────────────────────────────────────────────────────────


def test_the_fallback_writes_the_name_as_hex_and_the_key_derived(no_nmcli):
    wpa = FakeWpa()
    ok, err = pm.apply_wifi("HomeNet", PSK, None, False, 30.0, run=wpa)
    assert ok is True and err is None
    block = _block(no_nmcli)
    assert "\tssid=486f6d654e6574\n" in block          # "HomeNet" as hex
    expected = hashlib.pbkdf2_hmac("sha1", PSK.encode(), b"HomeNet", 4096, 32).hex()
    assert f"\tpsk={expected}\n" in block
    # Neither the name nor the passphrase appears in the clear, and there
    # is no commented-out passphrase line.
    assert "HomeNet" not in block
    assert PSK not in block
    assert "#psk" not in block
    assert 'ssid="' not in block
    # wpa_passphrase is not consulted; wpa_cli reconfigures and is polled.
    assert not any(c[0] == "wpa_passphrase" for c in wpa.calls)
    assert ["wpa_cli", "-i", "wlan0", "reconfigure"] in wpa.calls


def test_the_derivation_matches_the_published_test_vectors():
    # IEEE 802.11i, Annex H.4.
    assert proto.wpa_psk_hex("IEEE", "password") == (
        "f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e"
    )
    assert proto.wpa_psk_hex("ThisIsASSID", "ThisIsAPassword") == (
        "0dc0d6eb90555ed6419756b9a15ec3e3209b63df707dd508d14581f8982721af"
    )


def test_a_raw_key_is_written_as_is():
    key = "AB" * 32
    block = proto.wpa_supplicant_network_block("HomeNet", key)
    assert f"\tpsk={key.lower()}\n" in block


def test_a_hidden_network_gets_scan_ssid(no_nmcli):
    ok, _ = pm.apply_wifi("Secret", PSK, None, True, 30.0, run=FakeWpa())
    assert ok is True
    block = _block(no_nmcli)
    assert "\tscan_ssid=1\n" in block
    assert block.endswith("}\n")


def test_a_visible_network_gets_no_scan_ssid(no_nmcli):
    pm.apply_wifi("HomeNet", PSK, None, False, 30.0, run=FakeWpa())
    assert "scan_ssid" not in _block(no_nmcli)


def test_the_fallback_replaces_its_own_block_instead_of_stacking(no_nmcli):
    """Three submissions of the same form used to leave three blocks in a
    root-owned config file that nothing ever prunes. wpa_supplicant takes
    the first one it likes, so a stale block outranks the correction."""
    for _ in range(3):
        ok, err = pm.apply_wifi("HomeNet", PSK, None, False, 30.0, run=FakeWpa())
        assert ok is True, err
    text = no_nmcli.read_text(encoding="utf-8")
    assert text.count("network={") == 1
    assert text.startswith("ctrl_interface=")           # the rest is untouched
    assert "update_config=1\n" in text


def test_another_networks_block_is_left_alone(no_nmcli):
    pm.apply_wifi("Neighbour", PSK, None, False, 30.0, run=FakeWpa())
    pm.apply_wifi("HomeNet", PSK, None, False, 30.0, run=FakeWpa())
    pm.apply_wifi("HomeNet", PSK, None, False, 30.0, run=FakeWpa())
    text = no_nmcli.read_text(encoding="utf-8")
    assert text.count("network={") == 2
    assert "\tssid=" + "Neighbour".encode().hex() + "\n" in text
    assert "\tssid=" + "HomeNet".encode().hex() + "\n" in text


def test_the_block_is_exactly_four_or_five_lines():
    """Nothing but the network directive, the two values and the brace:
    no directive can ride in on a name or a passphrase."""
    lines = proto.wpa_supplicant_network_block("Home Net (5G)", PSK).splitlines()
    assert lines == [
        "network={",
        "\tssid=" + "Home Net (5G)".encode().hex(),
        "\tpsk=" + proto.wpa_psk_hex("Home Net (5G)", PSK),
        "}",
    ]


def test_a_utf8_name_is_carried_by_its_bytes():
    block = proto.wpa_supplicant_network_block("café", PSK)
    assert "\tssid=" + "café".encode("utf-8").hex() + "\n" in block


# ─── names and passphrases that are refused ───────────────────────────────


@pytest.mark.parametrize("ssid", [
    "Home\nNet",              # newline
    "Home\rNet",
    'Home"Net',               # quote
    "Home{Net",               # brace
    "Home}Net",
    "Home\tNet",              # tab
    "Home\x00Net",
    "Home\x7fNet",
    "",
    "x" * 33,                 # over 32 bytes
    "é" * 17,            # 34 bytes of UTF-8
])
def test_a_name_that_cannot_be_carried_is_refused_and_nothing_is_written(no_nmcli, ssid):
    before = no_nmcli.read_text(encoding="utf-8")
    wpa = FakeWpa()
    ok, err = pm.apply_wifi(ssid, PSK, None, False, 30.0, run=wpa)
    assert ok is False
    assert err and "refused" in err
    assert not ssid or ssid not in err                  # never echoed
    assert no_nmcli.read_text(encoding="utf-8") == before
    assert wpa.calls == []                              # nothing reconfigured


@pytest.mark.parametrize("ssid", ['Home"Net', "Home{Net", "Home\nNet"])
def test_the_nmcli_path_refuses_the_same_names(monkeypatch, ssid):
    monkeypatch.setattr(pm.shutil, "which", lambda n: f"/usr/bin/{n}")
    calls = []

    def nm(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    ok, err = pm.apply_wifi(ssid, PSK, None, False, 30.0, run=nm)
    assert ok is False and "refused" in (err or "")
    assert calls == []


@pytest.mark.parametrize("psk", ["short", "x" * 64 + "y", "é" * 10, "with\nnewline!"])
def test_a_passphrase_outside_wpa2_bounds_is_refused(no_nmcli, psk):
    ok, err = pm.apply_wifi("HomeNet", psk, None, False, 30.0, run=FakeWpa())
    assert ok is False
    assert psk not in (err or "")


def test_validate_provision_refuses_a_name_the_device_cannot_carry():
    doc = proto.build_provision(
        nonce="ab" * 8, room_id="den", domovoi_url="ws://192.168.1.50:6370",
        sat_type="voice", device_profile="xvf3800_usb", pairing_token="c" * 64,
        wifi_ssid='Home"Net', wifi_psk=PSK,
    )
    with pytest.raises(proto.ProvisionInvalid) as e:
        proto.validate_provision(doc, "ab" * 8)
    assert 'Home"Net' not in str(e.value)


def test_the_portal_form_refuses_the_same_names(monkeypatch, tmp_path):
    from satellite import portal_transport as pt

    monkeypatch.setattr(pt.shutil, "which", lambda name: f"/usr/bin/{name}")
    transport = pt.PortalTransport(
        ap_ssid="Domovoi-Setup-A4F2", ap_psk="unit-test-key",
        device_profile="xvf3800_usb", ip="127.0.0.1", bind_host="127.0.0.1",
        port=0, state_dir=tmp_path,
    )
    transport.nonce = "a1b2c3d4e5f60718"
    for ssid in ('Home"Net', "Home{Net", "Home\nNet"):
        with pytest.raises(proto.ProvisionInvalid) as e:
            transport.build_payload({"ssid": ssid, "psk": PSK, "room": "kitchen"})
        assert "cannot be used" in str(e.value)


def test_the_dashboard_adopt_form_refuses_the_same_names():
    from pydantic import ValidationError

    from web.backend.schemas import AdoptRequest

    good = AdoptRequest(room_id="den", wifi_ssid="HomeNet", wifi_psk=PSK)
    assert good.wifi_ssid == "HomeNet"
    for ssid in ('Home"Net', "Home{Net", "Home\nNet"):
        with pytest.raises(ValidationError):
            AdoptRequest(room_id="den", wifi_ssid=ssid, wifi_psk=PSK)
