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


# ─── USB mic clocking (host mode) ─────────────────────────────────────────

HOST_OVERLAY = "dtoverlay=dwc2,dr_mode=host"


def test_portal_xvf3800_gets_host_mode():
    """Without this the unit lands on the legacy dwc_otg driver, which
    delivers USB audio ~8x too fast — the wake word never fires. It also
    means a plain data cable works instead of a true OTG adapter."""
    config = overlay.edit_config_txt(STOCK_CONFIG, usb_gadget=False, usb_host=True)
    assert HOST_OVERLAY in config
    assert GADGET_OVERLAY not in config


def test_gadget_wins_over_host():
    """A USB-transport unit must be a peripheral to be adopted at all; it
    swaps to host once adoption is done."""
    config = overlay.edit_config_txt(STOCK_CONFIG, usb_gadget=True, usb_host=True)
    assert GADGET_OVERLAY in config
    assert HOST_OVERLAY not in config


def test_hat_units_get_neither():
    config = overlay.edit_config_txt(STOCK_CONFIG, usb_gadget=False, usb_host=False)
    assert HOST_OVERLAY not in config and GADGET_OVERLAY not in config


def test_host_mode_write_is_idempotent():
    once = overlay.edit_config_txt(STOCK_CONFIG, usb_gadget=False, usb_host=True)
    assert overlay.edit_config_txt(once, usb_gadget=False, usb_host=True) == once


def test_portal_card_for_a_usb_mic_carries_host_mode(tmp_path):
    creds = overlay.generate_ap_credentials()
    boot = tmp_path / "bootfs"
    boot.mkdir()
    (boot / "config.txt").write_text(STOCK_CONFIG)
    (boot / "cmdline.txt").write_text(STOCK_CMDLINE)
    tar = tmp_path / "payload.tar.gz"
    tar.write_bytes(b"x")
    overlay.write_overlay(
        boot,
        payload_tar=tar, payload_sha256="0" * 64,
        firstrun="#!/bin/bash\ntrue\n", info={},
        device_info=overlay.initial_device_info(
            "voice", setup_transport="portal", ap_ssid=creds["ssid"]),
        ap=creds, usb_gadget=False, usb_host=True,
    )
    assert HOST_OVERLAY in (boot / "config.txt").read_text()


# ─── cache refresh tolerance ──────────────────────────────────────────────
#
# One unfetchable item used to abort a whole bucket: an sdist-only wheel took
# every other wheel with it, and a library Debian renamed in the time_t
# transition took every other deb with it.

from domovoi.satellite_media import fetchers  # noqa: E402


def test_sdist_only_packages_are_separated():
    """spidev publishes no wheel for any platform, so --only-binary can never
    satisfy it — and it fails the entire download."""
    keep, skip = fetchers.split_unfetchable(
        ["numpy>=1.26", "spidev>=3.6", "scipy>=1.3,<2"]
    )
    assert keep == ["numpy>=1.26", "scipy>=1.3,<2"]
    assert skip == ["spidev>=3.6"]


@pytest.mark.parametrize("spec,name", [
    ("spidev>=3.6", "spidev"),
    ("spidev", "spidev"),
    ("SpiDev == 3.6", "spidev"),
    ("scikit-learn>=1,<2", "scikit-learn"),
    ("requests[socks]>=2", "requests"),
    ("webrtcvad-wheels>=2.0.14", "webrtcvad-wheels"),
])
def test_requirement_names_are_parsed_from_specs(spec, name):
    assert fetchers._requirement_name(spec) == name


def test_nothing_is_skipped_when_everything_has_wheels():
    reqs = ["numpy>=1.26", "scipy>=1.3,<2"]
    assert fetchers.split_unfetchable(reqs) == (reqs, [])


def test_renamed_debian_libraries_are_tried_first():
    """libasound2 is a virtual package on Trixie with no candidate; the real
    one is libasound2t64. apt-get download cannot fetch a virtual name."""
    script = fetchers.build_deb_script(["libasound2"])
    assert script.index("libasound2t64") < script.index("libasound2:arm64")


def test_one_missing_package_does_not_abort_the_rest():
    script = fetchers.build_deb_script(["libasound2", "mpg123", "mtools"])
    assert "set -e" not in script
    assert script.count("if ") == 3          # each package independently
    assert "mpg123" in script and "mtools" in script


def test_missing_packages_are_reported_not_swallowed():
    script = fetchers.build_deb_script(["mpg123"])
    assert fetchers.MISSING_MARKER in script
    assert fetchers.parse_missing(
        f"downloading...\n{fetchers.MISSING_MARKER} libasound2 mtools\n"
    ) == ["libasound2", "mtools"]


def test_a_clean_run_reports_nothing_missing():
    assert fetchers.parse_missing("all fine\n") == []


def test_generated_script_is_valid_shell():
    """A quoting slip here fails inside a container, where the error is a
    wall of apt output rather than a syntax message."""
    import subprocess as sp
    script = fetchers.build_deb_script(sorted(fetchers.BASE_APT_PACKAGES))
    proc = sp.run(["sh", "-n"], input=script, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
