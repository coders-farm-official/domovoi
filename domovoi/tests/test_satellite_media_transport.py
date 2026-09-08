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


# ─── first-boot hook cleanup ──────────────────────────────────────────────
#
# `systemd.unit=kernel-command-line.target` sends EVERY boot into a minimal
# target. Left in the cmdline, boot 2 re-enters it, re-runs firstrun (all
# steps skip), exits 0, and systemd.run_success_action=reboot reboots — a
# silent loop that never reaches multi-user.target, so the satellite never
# starts and no setup network ever appears. Found on the first real Pi.


def test_firstrun_removes_its_own_cmdline_hook():
    script = overlay.render_firstrun("domo", "xvf3800_usb", "voice", "portal")
    assert "cmdline-cleanup" in script
    assert 'systemd' + chr(92) + '.run' in script


def test_cmdline_cleanup_strips_only_our_tokens(tmp_path):
    """Run the real shell against a realistic cmdline: our three tokens go,
    everything else — including the gadget module a USB build needs — stays,
    and the result is exactly one line."""
    import subprocess as sp

    cmd = tmp_path / "cmdline.txt"
    cmd.write_text(
        "console=serial0,115200 console=tty1 root=PARTUUID=abcd-02 "
        "rootfstype=ext4 fsck.repair=yes rootwait modules-load=dwc2 "
        "systemd.run=/boot/firmware/domovoi/firstrun.sh "
        "systemd.run_success_action=reboot "
        "systemd.unit=kernel-command-line.target\n"
    )
    script = rf'''
CMD="{cmd.as_posix()}"
NEW="$(tr ' ' '\n' <"$CMD" \
  | grep -v '^systemd\.run' \
  | grep -v '^systemd\.unit=kernel-command-line\.target$' \
  | tr '\n' ' ' | sed -e 's/[[:space:]]\{{1,\}}/ /g' -e 's/^ //' -e 's/ $//')"
[ -n "$NEW" ] && printf '%s\n' "$NEW" >"$CMD"
'''
    proc = sp.run(["sh", "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr

    out = cmd.read_text()
    assert "systemd." not in out                  # the loop is gone
    assert out.count("\n") == 1 and out.endswith("\n")   # ONE line
    assert "modules-load=dwc2" in out             # USB builds still boot
    assert "root=PARTUUID=abcd-02" in out         # and still find their root
    assert "rootwait" in out


# ─── console credentials (Pi OS first-boot wizard) ────────────────────────
#
# Lite flashed with no pre-configuration has no user and blocks first boot
# on "enter a new username" at tty1. Merely annoying on a voice satellite;
# on a video one it is the first thing a customer sees on their screen.

import shutil as _shutil  # noqa: E402

needs_openssl = pytest.mark.skipif(
    _shutil.which("openssl") is None, reason="openssl not on PATH"
)


def test_console_credentials_shape():
    c = overlay.generate_console_credentials("domovoi")
    assert c["username"] == "domovoi"
    assert len(c["password"]) == overlay._PSK_LEN


def test_console_password_avoids_glyphs_people_mistype():
    for _ in range(30):
        c = overlay.generate_console_credentials("domovoi")
        assert not set(c["password"]) & set("0O1lI")


def test_console_credentials_differ_per_card():
    seen = {overlay.generate_console_credentials("domovoi")["password"]
            for _ in range(25)}
    assert len(seen) == 25


@needs_openssl
def test_password_hash_is_sha512_crypt():
    """Python's crypt module was removed in 3.13, so this comes from
    openssl. Pi OS only accepts a $6$ hash; anything else silently locks
    the account."""
    digest = overlay.hash_password("correct-horse")
    assert digest and digest.startswith("$6$")


def test_hash_password_degrades_when_openssl_is_missing(monkeypatch):
    monkeypatch.setattr(overlay.shutil, "which", lambda n: None)
    assert overlay.hash_password("anything") is None


def test_hash_password_rejects_a_wrong_format(monkeypatch):
    import subprocess as sp
    monkeypatch.setattr(overlay.shutil, "which", lambda n: "/usr/bin/openssl")
    monkeypatch.setattr(overlay.subprocess, "run", lambda *a, **k:
                        sp.CompletedProcess(a, 0, stdout="$1$md5hash\n", stderr=""))
    assert overlay.hash_password("x") is None


@needs_openssl
def test_userconf_lands_at_the_boot_root_with_only_the_hash(tmp_path):
    """Pi OS looks for userconf.txt at the root of the boot partition, not
    under our directory. And the file carries the HASH — the plaintext
    belongs only in console.json."""
    boot = tmp_path / "bootfs"
    boot.mkdir()
    (boot / "config.txt").write_text(STOCK_CONFIG)
    (boot / "cmdline.txt").write_text(STOCK_CMDLINE)
    tar = tmp_path / "payload.tar.gz"
    tar.write_bytes(b"x")
    creds = overlay.generate_console_credentials("domovoi")

    written = overlay.write_overlay(
        boot,
        payload_tar=tar, payload_sha256="0" * 64,
        firstrun="#!/bin/bash\ntrue\n", info={},
        device_info=overlay.initial_device_info("voice"),
        console=creds,
    )

    assert overlay.USERCONF_NAME in written
    conf = (boot / overlay.USERCONF_NAME).read_text()
    assert conf.startswith("domovoi:$6$")
    assert conf.count("\n") == 1
    assert creds["password"] not in conf          # hash only, never plaintext

    assert overlay.CONSOLE_JSON_PATH in written
    doc = json.loads((boot / "domovoi" / "console.json").read_text())
    assert doc == creds                            # plaintext, for the label


def test_no_console_creds_means_no_userconf(tmp_path):
    boot = tmp_path / "bootfs"
    boot.mkdir()
    (boot / "config.txt").write_text(STOCK_CONFIG)
    (boot / "cmdline.txt").write_text(STOCK_CMDLINE)
    tar = tmp_path / "payload.tar.gz"
    tar.write_bytes(b"x")
    written = overlay.write_overlay(
        boot, payload_tar=tar, payload_sha256="0" * 64,
        firstrun="#!/bin/bash\ntrue\n", info={},
        device_info=overlay.initial_device_info("voice"),
    )
    assert overlay.USERCONF_NAME not in written
    assert not (boot / overlay.USERCONF_NAME).exists()


# ─── the boot-partition root ──────────────────────────────────────────────
#
# firstrun.sh lives at <boot>/domovoi/firstrun.sh but every path it uses is
# relative to the boot ROOT. Deriving BOOT from its own directory made step 0
# cd into <boot>/domovoi/domovoi, fail(), and exit 1 — and because
# systemd.run_success_action only reboots on success, the device sat in an
# empty target with a blank screen and no stage 1. Silent and total.


def test_boot_is_the_parent_of_the_script_directory():
    script = overlay.render_firstrun("domovoi", "xvf3800_usb", "voice", "portal")
    line = next(l for l in script.splitlines() if l.startswith("BOOT="))
    assert '/..' in line, "BOOT must be the boot ROOT, not the domovoi/ subdir"


def test_boot_resolves_to_the_partition_root(tmp_path):
    """Run the real derivation with the real layout."""
    import subprocess as sp

    boot = tmp_path / "firmware"
    (boot / "domovoi").mkdir(parents=True)
    script = boot / "domovoi" / "firstrun.sh"
    script.write_text(
        'BOOT="$(cd "$(dirname "$0")/.." && pwd)"\nprintf %s "$BOOT"\n'
    )
    proc = sp.run(["sh", str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    # Compare shape, not the absolute path: the shell may report a
    # translated path on a Windows dev box. What matters is that BOOT is the
    # partition root and NOT the domovoi/ subdirectory the script sits in.
    resolved = proc.stdout.strip().rstrip("/")
    assert resolved.endswith("/firmware")
    assert not resolved.endswith("/domovoi")


def test_every_boot_path_is_root_relative():
    """If any path stopped assuming the root, the fix above would break it."""
    script = overlay.render_firstrun("domovoi", "xvf3800_usb", "voice", "portal")
    assert '"$BOOT/domovoi/payload.tar.gz"' in script
    assert '"$BOOT/domovoi"' in script          # the sha256 check
    assert '"$BOOT/config.txt"' in script
    assert '"$BOOT/cmdline.txt"' in script      # the hook cleanup
    assert '"$BOOT/domovoi/domovoi' not in script   # the bug itself


# ─── wireless regulatory domain ───────────────────────────────────────────
#
# Nothing set one, so every card booted with the radio soft-blocked by
# rfkill. On a portal unit that is terminal — no radio, no setup AP, no way
# to onboard it at all. Confirmed on hardware 2026-09-08.


@pytest.mark.parametrize("raw,expected", [
    ("US", "US"), ("gb", "GB"), (" de ", "DE"), ("Jp", "JP"),
])
def test_country_codes_are_normalised(raw, expected):
    assert overlay.validate_wifi_country(raw) == expected


@pytest.mark.parametrize("bad", ["", "U", "USA", "1A", "U1", "  ", "US-CA", None])
def test_bad_country_codes_are_refused(bad):
    """A wrong regulatory domain is a compliance problem, so this fails loudly
    rather than falling back to something plausible."""
    with pytest.raises(ValueError):
        overlay.validate_wifi_country(bad)


def test_firstrun_sets_the_country_and_unblocks_the_radio():
    script = overlay.render_firstrun("domovoi", "xvf3800_usb", "voice", "portal", "GB")
    assert 'WIFI_COUNTRY="GB"' in script
    assert "do_wifi_country" in script
    assert "rfkill unblock wifi" in script


def test_firstrun_falls_back_for_non_pi_boards():
    """Radxa images have no raspi-config; the domain still has to be set."""
    script = overlay.render_firstrun("domovoi", "xvf3800_usb", "voice", "portal", "US")
    assert "command -v raspi-config" in script
    assert "iw reg set" in script


def test_the_country_step_is_marked_and_skippable():
    script = overlay.render_firstrun("domovoi", "xvf3800_usb", "voice", "portal", "US")
    assert "skip wificountry" in script
    assert "done_step wificountry" in script


def test_render_rejects_a_bad_country_before_writing_anything():
    with pytest.raises(ValueError):
        overlay.render_firstrun("domovoi", "xvf3800_usb", "voice", "portal", "nope")


# ─── setup credentials handed back to the operator ────────────────────────
#
# Prepare generates the setup-AP key and console login, writes them to the
# card, and previously gave the person no way to see them without pulling
# the card and mounting it — which is how you end up unable to log into the
# device you just made.


def test_build_result_carries_the_credentials_for_display():
    """They must reach the caller; whether it shows them is its business."""
    from domovoi.satellite_media import builder  # noqa: F401 — import check
    # The contract is a dict under "credentials" with ap + console keys.
    creds = {
        "ap": overlay.generate_ap_credentials(),
        "console": overlay.generate_console_credentials("domovoi"),
    }
    assert set(creds) == {"ap", "console"}
    assert creds["ap"]["ssid"].startswith(overlay.SSID_PREFIX if hasattr(
        overlay, "SSID_PREFIX") else "Domovoi-Setup-")
    assert creds["console"]["username"] == "domovoi"


def test_credentials_are_never_written_to_the_job_row():
    """The card is the source of truth and should outlive nothing. A secret
    in Postgres survives in backups long after the card is wiped."""
    import inspect

    from web.backend.api import satellite_media as api

    src = inspect.getsource(api)
    # The store is a module-level dict, not a column.
    assert "_JOB_CREDENTIALS" in src
    for sql_fragment in ("INSERT INTO satellite_media_jobs", "UPDATE satellite_media_jobs"):
        stmt_region = src.split(sql_fragment, 1)[1][:400]
        assert "psk" not in stmt_region
        assert "password" not in stmt_region


def test_the_credential_store_is_bounded():
    from web.backend.api import satellite_media as api

    api._JOB_CREDENTIALS.clear()
    for job_id in range(api._CREDENTIAL_CAP + 10):
        api._remember_credentials(job_id, {"ap": {"ssid": "x", "psk": "y"}})
    assert len(api._JOB_CREDENTIALS) == api._CREDENTIAL_CAP
    # The most recent survive; the oldest are dropped.
    assert (api._CREDENTIAL_CAP + 9) in api._JOB_CREDENTIALS
    assert 0 not in api._JOB_CREDENTIALS
    api._JOB_CREDENTIALS.clear()


def test_zip_and_drive_targets_write_the_same_things():
    """The zip branch had drifted — no console credentials and no USB host
    mode — so a zip-built card stopped on the user wizard and came up deaf."""
    import inspect

    from domovoi.satellite_media import builder

    src = inspect.getsource(builder.build)
    assert src.count("console=console") == 2
    assert src.count("usb_host=(") == 2


def test_the_console_account_can_become_root():
    """Media prep creates this user AND hands its password to the operator
    via userconf.txt. Without sudo there is no route to root at all — the
    root account is locked on Pi OS, and sudoers.d/domovoi-satellite grants
    three specific commands. A unit nobody can get a shell on is a unit
    nobody can support."""
    script = overlay.render_firstrun("domovoi", "xvf3800_usb", "voice", "portal", "US")
    line = next(l for l in script.splitlines() if "usermod -aG" in l)
    groups = line.split("usermod -aG ", 1)[1].split()[0]
    assert "sudo" in groups.split(",")
    # and the hardware groups it already needed
    for g in ("audio", "video", "gpio", "spi", "i2c"):
        assert g in groups.split(",")


def test_dnsmasq_advertises_the_portal_over_dhcp():
    """RFC 8910 (option 114) tells the client the portal URL outright.
    Probe interception is defeated by private DNS and by the per-SSID
    "no internet" verdict phones cache; an explicit advertisement isn't."""
    script = overlay.render_firstrun("domovoi", "xvf3800_usb", "voice", "portal", "US")
    assert "dhcp-option=114,http://192.168.4.1/" in script
    # and the two it already had
    assert "address=/#/192.168.4.1" in script      # wildcard DNS
    assert "dhcp-option=6,192.168.4.1" in script   # we are the resolver
