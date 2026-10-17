"""The Windows card helper writes a satellite card byte for byte as the
server does.

The tray's card helper (windows/tray/BootText.cs, CardWriter.cs) merges
config.txt and cmdline.txt and writes domovoi/ap.json on a card the server
can't see (a Windows install: WSL has no SD cards). These goldens are the
server's own answers: ``overlay.write_overlay`` run on boot partitions with
the same stock, prepared, CRLF, trailing-space and odd inputs, for each way
a card is set up. The C# tests (windows/tests/TrayTests.cs, run by
windows/build.ps1) feed the same inputs to the C# merge and must get these
bytes; this test keeps the goldens equal to what overlay.py does today, so
a change to the server's edits fails here until the goldens (and the C#)
follow it.

Regenerate after a deliberate change to overlay.py's edits:

    python -m domovoi.tests.test_windows_card_parity --write
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from domovoi.satellite_media import overlay

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDENS = REPO_ROOT / "windows" / "tests" / "card-goldens.json"

# Representative stock boot files of Raspberry Pi OS Lite (64-bit), as
# flashed with no OS customisation (the PARTUUIDs are examples).
BOOKWORM_CONFIG = """\
# For more options and information see
# http://rptl.io/configtxt
# Some settings may impact device functionality. See link above for details

# Uncomment some or all of these to enable the optional hardware interfaces
#dtparam=i2c_arm=on
#dtparam=i2s=on
#dtparam=spi=on

# Enable audio (loads snd_bcm2835)
dtparam=audio=on

# Additional overlays and parameters are documented
# /boot/firmware/overlays/README

# Automatically load overlays for detected cameras
camera_auto_detect=1

# Automatically load overlays for detected DSI displays
display_auto_detect=1

# Automatically load initramfs files, if found
auto_initramfs=1

# Enable DRM VC4 V3D driver
dtoverlay=vc4-kms-v3d
max_framebuffers=2

# Don't have the firmware create an initial video= setting in cmdline.txt.
# Use the kernel's default instead.
disable_fw_kms_setup=1

# Run in 64-bit mode
arm_64bit=1

# Disable compensation for displays with overscan
disable_overscan=1

# Run as fast as firmware / board allows
arm_boost=1

[cm4]
# Enable host mode on the 2711 built-in XHCI USB controller.
# This line should be removed if the legacy DWC2 controller is required
# (e.g. for USB device mode) or if USB support is not required.
otg_mode=1

[cm5]
dtoverlay=dwc2,dr_mode=host

[all]
"""
BOOKWORM_CMDLINE = (
    "console=serial0,115200 console=tty1 root=PARTUUID=4e639091-02 rootfstype=ext4 "
    "fsck.repair=yes rootwait quiet init=/usr/lib/raspberrypi-sys-mods/firstboot\n"
)
TRIXIE_CONFIG = BOOKWORM_CONFIG.replace("# Run as fast as firmware / board allows\narm_boost=1\n",
                                        "# Run as fast as firmware / board allows\narm_boost=1\n\n"
                                        "# Enable the RP1 PCIe link at Gen 2 speed on Pi 5\n#dtparam=pciex1_gen=2\n")
TRIXIE_CMDLINE = (
    "console=serial0,115200 console=tty1 root=PARTUUID=8a438930-02 rootfstype=ext4 "
    "fsck.repair=yes rootwait quiet splash plymouth.ignore-serial-consoles resize\n"
)

# How a card is set up: (usb_gadget, usb_host), as builder.py derives them.
FLAGS = {
    "usb": (True, False),               # adopted over USB
    "portal": (False, False),           # Wi-Fi setup, a HAT microphone
    "portal-usb-mic": (False, True),    # Wi-Fi setup, a USB microphone array
}


def _inputs() -> list[tuple[str, bytes, bytes]]:
    crlf = lambda s: s.replace("\n", "\r\n").encode("utf-8")  # noqa: E731
    lf = lambda s: s.encode("utf-8")  # noqa: E731
    cases = [
        ("bookworm stock", lf(BOOKWORM_CONFIG), lf(BOOKWORM_CMDLINE)),
        ("trixie stock", lf(TRIXIE_CONFIG), lf(TRIXIE_CMDLINE)),
        ("bookworm CRLF", crlf(BOOKWORM_CONFIG), crlf(BOOKWORM_CMDLINE)),
        ("trixie CRLF", crlf(TRIXIE_CONFIG), crlf(TRIXIE_CMDLINE)),
        ("no final newline", lf(BOOKWORM_CONFIG.rstrip("\n")), lf(BOOKWORM_CMDLINE.rstrip("\n"))),
        ("trailing spaces", lf(BOOKWORM_CONFIG + "   \n\t\n"), lf("  " + BOOKWORM_CMDLINE.rstrip("\n") + "   \t\n\n")),
        ("tabs and doubled spaces", lf(BOOKWORM_CONFIG), lf("console=serial0,115200\t\tconsole=tty1  root=PARTUUID=4e639091-02 rootwait\n")),
        ("an existing dwc_otg.speed", lf(BOOKWORM_CONFIG), lf(BOOKWORM_CMDLINE.rstrip("\n") + " dwc_otg.speed=0\n")),
        ("an existing modules-load", lf(BOOKWORM_CONFIG), lf(BOOKWORM_CMDLINE.rstrip("\n") + " modules-load=dwc2,g_ether\n")),
        ("a second cmdline line", lf(BOOKWORM_CONFIG), lf(BOOKWORM_CMDLINE + "console=tty3 quiet\n")),
        ("lone CR line ends", BOOKWORM_CONFIG.replace("\n", "\r").encode("utf-8"), BOOKWORM_CMDLINE.replace("\n", "\r").encode("utf-8")),
        ("a byte-order mark", b"\xef\xbb\xbf" + lf(BOOKWORM_CONFIG), b"\xef\xbb\xbf" + lf(BOOKWORM_CMDLINE)),
        ("bytes that aren't UTF-8", lf(BOOKWORM_CONFIG) + b"# caf\xe9 \xc3\x28 \xe2\x82 \xf0\x9f\x98\n", lf(BOOKWORM_CMDLINE.rstrip("\n")) + b" x=\xff\xfe\n"),
        ("non-ASCII whitespace", lf(BOOKWORM_CONFIG), lf("console=tty1\u00a0rootwait\u2003quiet\u3000splash\n")),
        ("empty files", b"", b""),
        ("only whitespace", b"\n\n", b"  \n \n"),
        ("a stray marker only", lf("dtparam=audio=on\n" + "# --- domovoi satellite (media-prep) ---\n"), lf("rootwait\n")),
        ("the first-boot hook already there", lf(BOOKWORM_CONFIG), lf(BOOKWORM_CMDLINE.rstrip("\n") + " systemd.run=/boot/firmware/domovoi/firstrun.sh\n")),
        ("another first-boot script (Imager)", lf(BOOKWORM_CONFIG), lf(BOOKWORM_CMDLINE.rstrip("\n") + " systemd.run=/boot/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target\n")),
    ]
    return cases


def _write(boot: Path, *, usb_gadget: bool, usb_host: bool) -> None:
    """overlay.write_overlay, the server's own writer (no console, no AP:
    those don't touch the two files)."""
    payload = boot.parent / f"{boot.name}-payload.tar.gz"
    payload.write_bytes(b"payload")
    overlay.write_overlay(
        boot, payload_tar=payload, payload_sha256="0" * 64, firstrun="#!/bin/sh\n",
        info={}, device_info={}, usb_gadget=usb_gadget, usb_host=usb_host,
    )


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build_goldens() -> dict:
    out: dict = {
        "about": "Made by domovoi/tests/test_windows_card_parity.py from overlay.write_overlay; "
                 "checked against the C# card helper by windows/tests/TrayTests.cs. Inputs are base64; "
                 "each output is the SHA-256 and length of the file's bytes after the server's write.",
        "inputs": [{"name": name, "config": _b64(config), "cmdline": _b64(cmdline)} for name, config, cmdline in _inputs()],
        "zips": {},
        "cases": [],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        n = 0
        for flag_name, (gadget, host) in FLAGS.items():
            # What the zip carries: the edits run on an empty staging folder
            # (builder.py's zip path).
            stage = root / f"zip-{flag_name}"
            stage.mkdir()
            _write(stage, usb_gadget=gadget, usb_host=host)
            out["zips"][flag_name] = {
                "config": _b64((stage / "config.txt").read_bytes()),
                "cmdline": _b64((stage / "cmdline.txt").read_bytes()),
            }
            for index, (name, config, cmdline) in enumerate(_inputs()):
                n += 1
                boot = root / f"card-{n}"
                boot.mkdir()
                (boot / "config.txt").write_bytes(config)
                (boot / "cmdline.txt").write_bytes(cmdline)
                _write(boot, usb_gadget=gadget, usb_host=host)
                cfg = (boot / "config.txt").read_bytes()
                cmd = (boot / "cmdline.txt").read_bytes()
                out["cases"].append({
                    "name": name, "input": index, "flags": flag_name,
                    "config_sha256": _sha(cfg), "config_length": len(cfg),
                    "cmdline_sha256": _sha(cmd), "cmdline_length": len(cmd),
                    "config_written": cfg != config, "cmdline_written": cmd != cmdline,
                })
        # ap.json as the server writes it on a card it prepares itself.
        ap = {"ssid": "Domovoi-Setup-3FA9", "psk": "k7tqmzr4wd"}
        boot = root / "ap"
        boot.mkdir()
        payload = root / "ap-payload.tar.gz"
        payload.write_bytes(b"payload")
        overlay.write_overlay(boot, payload_tar=payload, payload_sha256="0" * 64, firstrun="",
                              info={}, device_info={}, ap=ap, usb_gadget=False)
        raw = (boot / "domovoi" / "ap.json").read_bytes()
        # The server runs on Linux, where write_text writes LF; on a Windows
        # test box Python's text mode would give CRLF, which no server ever
        # writes, so the golden is the Linux bytes.
        raw = raw.replace(b"\r\n", b"\n")
        out["ap_json"] = {"ssid": ap["ssid"], "psk": ap["psk"], "bytes": _b64(raw)}
    return out


def _render(goldens: dict) -> bytes:
    return (json.dumps(goldens, indent=1, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def test_the_goldens_are_what_the_server_writes():
    assert GOLDENS.is_file(), f"{GOLDENS} is missing: run python -m domovoi.tests.test_windows_card_parity --write"
    committed = json.loads(GOLDENS.read_text(encoding="ascii"))
    assert committed == build_goldens(), (
        "overlay.py's card edits changed: regenerate windows/tests/card-goldens.json "
        "(python -m domovoi.tests.test_windows_card_parity --write) and make the C# card helper match"
    )


def test_the_goldens_cover_what_the_brief_asks():
    g = json.loads(GOLDENS.read_text(encoding="ascii"))
    names = {c["name"] for c in g["cases"]}
    for need in ("bookworm stock", "trixie stock", "bookworm CRLF", "trailing spaces", "an existing dwc_otg.speed"):
        assert need in names, need
    assert {c["flags"] for c in g["cases"]} == set(FLAGS)
    # A config.txt that already has the marker is left alone.
    assert not next(c for c in g["cases"] if c["name"] == "a stray marker only")["config_written"]
    # The CRLF card is rewritten (to LF: the server's universal newlines).
    crlf = next(c for c in g["cases"] if c["name"] == "bookworm CRLF" and c["flags"] == "portal")
    assert crlf["config_written"] and crlf["cmdline_written"]


def test_a_prepared_card_is_left_byte_for_byte(tmp_path):
    """Run the server's writer twice: the second pass changes nothing (the
    card helper's tests check the same of the C# merge)."""
    for i, (name, config, cmdline) in enumerate(_inputs()):
        for flag_name, (gadget, host) in FLAGS.items():
            boot = tmp_path / f"again-{i}-{flag_name}"
            boot.mkdir()
            (boot / "config.txt").write_bytes(config)
            (boot / "cmdline.txt").write_bytes(cmdline)
            _write(boot, usb_gadget=gadget, usb_host=host)
            cfg, cmd = (boot / "config.txt").read_bytes(), (boot / "cmdline.txt").read_bytes()
            _write(boot, usb_gadget=gadget, usb_host=host)
            assert (boot / "config.txt").read_bytes() == cfg, name
            assert (boot / "cmdline.txt").read_bytes() == cmd, name


def test_the_ap_json_golden_is_what_stage_1_reads(tmp_path):
    """The satellite's own reader (provisioning_mode._read_ap_json) takes
    the bytes the card helper writes."""
    from satellite.provisioning_mode import _read_ap_json

    g = json.loads(GOLDENS.read_text(encoding="ascii"))
    p = tmp_path / "ap.json"
    p.write_bytes(base64.b64decode(g["ap_json"]["bytes"]))
    assert _read_ap_json(p) == {"ssid": g["ap_json"]["ssid"], "psk": g["ap_json"]["psk"]}
    assert not p.read_bytes().startswith(b"\xef\xbb\xbf") and b"\r" not in p.read_bytes()


def test_the_password_rules_match_the_card_helper():
    """CardWriter.cs's SetupPassword copies overlay.py's alphabet and length."""
    src = (REPO_ROOT / "windows" / "tray" / "CardWriter.cs").read_text(encoding="ascii")
    assert f'public const string Alphabet = "{overlay._PSK_ALPHABET}";' in src
    assert f"public const int Length = {overlay._AP_PSK_LEN};" in src
    boot = (REPO_ROOT / "windows" / "tray" / "BootText.cs").read_text(encoding="ascii")
    assert f'public const string Marker = "{overlay._MARKER}";' in boot
    assert f'public const string EndMarker = "{overlay._END_MARKER}";' in boot
    assert f'public const string FirstrunToken = "{overlay._FIRSTRUN_CMDLINE_TOKENS[0]}";' in boot


def test_the_card_helper_knows_the_servers_boards():
    """CardZip.cs's board table: every board the server prepares cards for,
    with its boot partition's marker file."""
    from domovoi.satellite_media.boards import BOARDS

    src = (REPO_ROOT / "windows" / "tray" / "CardZip.cs").read_text(encoding="ascii")
    for b in BOARDS.values():
        if b.supported:
            assert f'{{ "{b.id}", new[] {{ "{b.label}", "{b.boot_marker}" }} }}' in src, b.id


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        GOLDENS.write_bytes(_render(build_goldens()))
        print(f"wrote {GOLDENS}")
    else:
        print(__doc__)
