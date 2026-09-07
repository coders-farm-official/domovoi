"""Prepare-time transport choice: what media prep bakes onto the card.

The load-bearing one is `test_portal_build_does_not_pin_peripheral_mode`.
`dtoverlay=dwc2,dr_mode=peripheral` is never reverted once written, and on a
Pi Zero 2 W it holds the only USB data port in peripheral mode — where a USB
mic array cannot enumerate. A portal unit that carried it would onboard
perfectly and then be deaf.

No DB, no Docker, no hardware, so these run everywhere rather than skipping.
"""

from __future__ import annotations

import json
import random

import pytest

from domovoi.satellite_media import overlay

STOCK_CONFIG = "# stock config\ndtparam=audio=on\n"
STOCK_CMDLINE = "console=serial0,115200 root=PARTUUID=xxx rootwait\n"

GADGET_OVERLAY = "dtoverlay=dwc2,dr_mode=peripheral"
GADGET_MODULE = "modules-load=dwc2"


# ─── the regression that matters ──────────────────────────────────────────


def test_portal_build_does_not_pin_peripheral_mode():
    config = overlay.edit_config_txt(STOCK_CONFIG, usb_gadget=False)
    cmdline = overlay.edit_cmdline_txt(STOCK_CMDLINE, usb_gadget=False)
    assert GADGET_OVERLAY not in config
    assert GADGET_MODULE not in cmdline
    # The first-boot hook is transport-independent and must survive.
    assert "systemd.run=/boot/firmware/domovoi/firstrun.sh" in cmdline


def test_usb_build_still_gets_the_gadget():
    config = overlay.edit_config_txt(STOCK_CONFIG, usb_gadget=True)
    cmdline = overlay.edit_cmdline_txt(STOCK_CMDLINE, usb_gadget=True)
    assert GADGET_OVERLAY in config
    assert GADGET_MODULE in cmdline


@pytest.mark.parametrize("usb_gadget", [True, False])
def test_editors_stay_idempotent_either_way(usb_gadget):
    once = overlay.edit_config_txt(STOCK_CONFIG, usb_gadget=usb_gadget)
    assert overlay.edit_config_txt(once, usb_gadget=usb_gadget) == once
    once_cmd = overlay.edit_cmdline_txt(STOCK_CMDLINE, usb_gadget=usb_gadget)
    assert overlay.edit_cmdline_txt(once_cmd, usb_gadget=usb_gadget) == once_cmd
    assert once_cmd.count("\n") == 1          # Pi firmware wants ONE line


# ─── AP credentials ───────────────────────────────────────────────────────


def test_ap_credentials_shape():
    creds = overlay.generate_ap_credentials()
    assert creds["ssid"].startswith("Domovoi-Setup-")
    assert len(creds["ssid"].rsplit("-", 1)[1]) == 4
    assert 8 <= len(creds["psk"]) <= 63       # WPA2's own limits


def test_ap_psk_avoids_glyphs_people_mistype():
    """The key is read off a printed box by a customer, once."""
    creds = overlay.generate_ap_credentials(rng=random.Random(7))
    assert not set(creds["psk"]) & set("0O1lI")


def test_ap_credentials_differ_per_device():
    seen = {overlay.generate_ap_credentials()["psk"] for _ in range(25)}
    assert len(seen) == 25


# ─── device-info ──────────────────────────────────────────────────────────


def test_device_info_records_transport_and_ssid_but_never_the_key():
    creds = overlay.generate_ap_credentials()
    info = overlay.initial_device_info(
        "voice", setup_transport="portal", ap_ssid=creds["ssid"]
    )
    assert info["setup_transport"] == "portal"
    assert info["ap_ssid"] == creds["ssid"]
    # device-info is served to adopters — the PSK lives in its own sidecar.
    assert creds["psk"] not in json.dumps(info)


def test_device_info_defaults_to_usb():
    assert overlay.initial_device_info("voice")["setup_transport"] == "usb"


def test_device_info_rejects_unknown_transport():
    with pytest.raises(ValueError):
        overlay.initial_device_info("voice", setup_transport="carrier-pigeon")


# ─── what lands on the card ───────────────────────────────────────────────


def _write(tmp_path, *, ap, usb_gadget):
    boot = tmp_path / "bootfs"
    boot.mkdir()
    (boot / "config.txt").write_text(STOCK_CONFIG)
    (boot / "cmdline.txt").write_text(STOCK_CMDLINE)
    tar = tmp_path / "payload.tar.gz"
    tar.write_bytes(b"not really a tar")
    written = overlay.write_overlay(
        boot,
        payload_tar=tar,
        payload_sha256="0" * 64,
        firstrun="#!/bin/bash\ntrue\n",
        info={"build": "test"},
        device_info=overlay.initial_device_info(
            "voice",
            setup_transport="portal" if ap else "usb",
            ap_ssid=ap["ssid"] if ap else None,
        ),
        ap=ap,
        usb_gadget=usb_gadget,
    )
    return boot, written


def test_portal_card_carries_ap_json_and_no_gadget_overlay(tmp_path):
    creds = overlay.generate_ap_credentials()
    boot, written = _write(tmp_path, ap=creds, usb_gadget=False)
    assert "domovoi/ap.json" in written
    assert json.loads((boot / "domovoi" / "ap.json").read_text()) == creds
    assert GADGET_OVERLAY not in (boot / "config.txt").read_text()


def test_usb_card_carries_no_ap_json(tmp_path):
    boot, written = _write(tmp_path, ap=None, usb_gadget=True)
    assert "domovoi/ap.json" not in written
    assert not (boot / "domovoi" / "ap.json").exists()
    assert GADGET_OVERLAY in (boot / "config.txt").read_text()


# ─── firstrun rendering ───────────────────────────────────────────────────


def test_firstrun_installs_wildcard_dns_for_portal_units():
    script = overlay.render_firstrun("domo", "xvf3800_usb", "voice", "portal")
    assert "dnsmasq-shared.d/domovoi-portal.conf" in script
    assert "address=/#/192.168.4.1" in script
    assert 'SETUP_TRANSPORT="portal"' in script


def test_firstrun_seeds_the_baked_mic_profile():
    script = overlay.render_firstrun("domo", "xvf3800_usb", "voice", "portal")
    assert "image_device_profile" in script


def test_firstrun_has_no_unreplaced_placeholders():
    script = overlay.render_firstrun("domo", "xvf3800_usb", "voice", "portal")
    assert "@SETUP_TRANSPORT@" not in script
    assert "@SAT_USER@" not in script
    assert "\r" not in script                 # LF only — shebangs break on CRLF
