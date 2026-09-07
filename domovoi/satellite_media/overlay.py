"""Boot-partition overlay for prepared satellite media.

The overlay is everything the prepare flow writes onto a stock-flashed
card's FAT boot partition (plain file writes — no admin rights, no raw
device access):

* ``config.txt``  — the dwc2 USB-gadget overlay (peripheral mode), appended
  idempotently under a marker comment;
* ``cmdline.txt`` — the ``systemd.run`` first-boot hook (the mechanism
  Raspberry Pi Imager itself uses), appended idempotently to the single
  kernel line;
* ``domovoi/``    — firstrun.sh (rendered stage 1), payload.tar.gz +
  payload.sha256, build-info.json, and the initial device-info.json.

Both editors are pure text→text functions (run-twice ⇒ identical output)
so they're trivially unit-testable and safe to re-apply to an already-
prepared card.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from string import Template

_MARKER = "# --- domovoi satellite (media-prep) ---"
_END_MARKER = "# --- end domovoi satellite ---"

# USB-gadget mode is written ONLY for units that onboard over the USB
# mass-storage transport. It pins the controller into peripheral mode, and
# on a single-data-port Pi (Zero 2 W) that is mutually exclusive with a USB
# microphone array on the same port — a portal unit that carried these
# would adopt cleanly and then be deaf.
_GADGET_CONFIG_LINE = "dtoverlay=dwc2,dr_mode=peripheral"
_GADGET_CMDLINE_TOKEN = "modules-load=dwc2"

_FIRSTRUN_CMDLINE_TOKENS = (
    "systemd.run=/boot/firmware/domovoi/firstrun.sh",
    "systemd.run_success_action=reboot",
    "systemd.unit=kernel-command-line.target",
)

SETUP_TRANSPORTS = ("usb", "portal")

# WPA2 needs 8-63 characters. The alphabet drops the glyph pairs people
# mistype off a printed label (0/O, 1/l/I), because this key is read off a
# box by a customer, once, under mild stress.
_PSK_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
_PSK_LEN = 12

# Wildcard DNS is what actually brings the OS connectivity-probe hostnames
# to us; without it the sign-in sheet never opens. NetworkManager's shared
# mode runs its own dnsmasq and reads drop-ins from this directory.
DNSMASQ_DROPIN_PATH = (
    "/etc/NetworkManager/dnsmasq-shared.d/domovoi-portal.conf"
)
DNSMASQ_DROPIN = (
    "# Domovoi setup portal — every name resolves to us so the captive\n"
    "# check fails deliberately and the phone opens the sign-in page.\n"
    "address=/#/192.168.4.1\n"
    "dhcp-option=6,192.168.4.1\n"
)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def edit_config_txt(text: str, *, usb_gadget: bool = True) -> str:
    """Append the media-prep block once. Idempotent.

    ``usb_gadget`` controls whether the dwc2 peripheral-mode overlay is
    included. Portal units must NOT get it: nothing ever reverts it, and it
    holds the only data port on a Pi Zero 2 W in peripheral mode, where a
    USB mic array cannot enumerate."""
    if _MARKER in text:
        return text
    if text and not text.endswith("\n"):
        text += "\n"
    lines = [_MARKER]
    if usb_gadget:
        lines.append(_GADGET_CONFIG_LINE)
    lines.append(_END_MARKER)
    return text + "\n".join(lines) + "\n"


def edit_cmdline_txt(text: str, *, usb_gadget: bool = True) -> str:
    """Append the first-boot hook tokens to the SINGLE kernel line (Pi
    firmware requires one line). Idempotent per token; preserves order.
    The dwc2 module is loaded only for USB-gadget units."""
    line = text.strip().splitlines()[0] if text.strip() else ""
    tokens = line.split() if line else []
    wanted = (
        (_GADGET_CMDLINE_TOKEN,) + _FIRSTRUN_CMDLINE_TOKENS
        if usb_gadget else _FIRSTRUN_CMDLINE_TOKENS
    )
    for tok in wanted:
        key = tok.split("=", 1)[0]
        if not any(t == tok or t.startswith(key + "=") for t in tokens):
            tokens.append(tok)
    return " ".join(tokens) + "\n"


def render_template(name: str, substitutions: dict[str, str]) -> str:
    """Render a templates/ file, replacing @KEY@ placeholders. LF-only
    output (these run on the device; CRLF would break shebangs)."""
    raw = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
    out = raw
    for key, value in substitutions.items():
        out = out.replace(f"@{key}@", value)
    return out.replace("\r\n", "\n")


def render_firstrun(
    sat_user: str,
    mic_profile: str,
    sat_type: str,
    setup_transport: str = "usb",
) -> str:
    return render_template(
        "firstrun.sh.tmpl",
        {"SAT_USER": sat_user, "MIC_PROFILE": mic_profile, "SAT_TYPE": sat_type,
            "SETUP_TRANSPORT": setup_transport},
    )


def render_stage2(sat_user: str) -> str:
    return render_template("stage2.sh.tmpl", {"SAT_USER": sat_user})


def build_info(
    *, board: str, mic_profile: str, sat_type: str, core_sha: str | None,
    python_version: str, os_release: str, plugins: list[dict[str, str]],
    offline: bool,
) -> dict:
    return {
        "schema": 1,
        "board": board,
        "mic_profile": mic_profile,
        "sat_type": sat_type,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "core_sha_label": core_sha,
        "python_version": python_version,
        "os_release": os_release,
        "offline": offline,
        "plugins": plugins,
    }


def generate_ap_credentials(rng=None) -> dict:
    """Per-device setup-AP credentials, baked at prepare time.

    The SSID carries a short random id rather than the MAC: the card has
    never booted, so there is no MAC to read yet. The PSK is what makes the
    setup link worth encrypting and doubles as weak proof of authenticity —
    something impersonating a satellite to farm Wi-Fi passwords has to know
    this unit's key. Print both on the box."""
    import secrets

    rng = rng or secrets
    ident = "".join(rng.choice("0123456789ABCDEF") for _ in range(4))
    psk = "".join(rng.choice(_PSK_ALPHABET) for _ in range(_PSK_LEN))
    return {"ssid": f"Domovoi-Setup-{ident}", "psk": psk}


def initial_device_info(
    sat_type: str,
    *,
    setup_transport: str = "usb",
    ap_ssid: str | None = None,
) -> dict:
    """The device-info.json the overlay seeds — stage 1 rewrites it as it
    progresses; the adoption transport serves its own copy after boot 2.

    ``ap_ssid`` is recorded but the PSK deliberately is NOT: this document
    is served to adopters, and the key lives in its own sidecar."""
    if setup_transport not in SETUP_TRANSPORTS:
        raise ValueError(f"unknown setup transport {setup_transport!r}")
    return {
        "domovoi_setup": 1,
        "nonce": "unbooted",
        "mac": None,
        "board": None,
        "model": None,
        "client_version": None,
        "sat_type": sat_type,
        "setup_transport": setup_transport,
        "ap_ssid": ap_ssid,
        "status": "bootstrapping",
        "step": "flashed",
        "error": None,
        "profiles_supported": [],
    }


def write_overlay(
    boot_dir: Path,
    *,
    payload_tar: Path,
    payload_sha256: str,
    firstrun: str,
    info: dict,
    device_info: dict,
    ap: dict | None = None,
    usb_gadget: bool = True,
) -> list[str]:
    """Write the overlay onto a mounted boot partition (or any staging
    dir for the zip path). Returns the relative paths written. The tar is
    COPIED (it may be hundreds of MB; caller pre-checked free space)."""
    written: list[str] = []
    ddir = boot_dir / "domovoi"
    ddir.mkdir(parents=True, exist_ok=True)

    for name, editor in (("config.txt", edit_config_txt), ("cmdline.txt", edit_cmdline_txt)):
        p = boot_dir / name
        original = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
        edited = editor(original, usb_gadget=usb_gadget)
        if edited != original:
            p.write_text(edited, encoding="utf-8", newline="\n")
            written.append(name)

    (ddir / "firstrun.sh").write_text(firstrun, encoding="utf-8", newline="\n")
    written.append("domovoi/firstrun.sh")
    (ddir / "payload.sha256").write_text(
        f"{payload_sha256}  payload.tar.gz\n", encoding="utf-8", newline="\n"
    )
    written.append("domovoi/payload.sha256")
    (ddir / "build-info.json").write_text(
        json.dumps(info, indent=2), encoding="utf-8"
    )
    written.append("domovoi/build-info.json")
    (ddir / "device-info.json").write_text(
        json.dumps(device_info, indent=2), encoding="utf-8"
    )
    written.append("domovoi/device-info.json")

    if ap is not None:
        # Kept out of device-info.json, which is served to adopters.
        (ddir / "ap.json").write_text(json.dumps(ap, indent=2), encoding="utf-8")
        written.append("domovoi/ap.json")

    dest_tar = ddir / "payload.tar.gz"
    dest_tar.write_bytes(payload_tar.read_bytes())
    written.append("domovoi/payload.tar.gz")
    return written


def looks_like_pi_boot(mount: Path, marker: str) -> bool:
    """Whether a mounted volume looks like a flashed Pi boot partition for
    the target board (config.txt + cmdline.txt + the board's dtb)."""
    return (
        (mount / "config.txt").is_file()
        and (mount / "cmdline.txt").is_file()
        and (not marker or (mount / marker).is_file())
    )
