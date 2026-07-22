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
_CONFIG_LINES = (
    _MARKER,
    "dtoverlay=dwc2,dr_mode=peripheral",
    "# --- end domovoi satellite ---",
)
_CMDLINE_TOKENS = (
    "modules-load=dwc2",
    "systemd.run=/boot/firmware/domovoi/firstrun.sh",
    "systemd.run_success_action=reboot",
    "systemd.unit=kernel-command-line.target",
)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def edit_config_txt(text: str) -> str:
    """Append the gadget overlay block once. Idempotent."""
    if _MARKER in text:
        return text
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "\n".join(_CONFIG_LINES) + "\n"


def edit_cmdline_txt(text: str) -> str:
    """Append the first-boot hook tokens to the SINGLE kernel line (Pi
    firmware requires one line). Idempotent per token; preserves order."""
    line = text.strip().splitlines()[0] if text.strip() else ""
    tokens = line.split() if line else []
    for tok in _CMDLINE_TOKENS:
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


def render_firstrun(sat_user: str, mic_profile: str, sat_type: str) -> str:
    return render_template(
        "firstrun.sh.tmpl",
        {"SAT_USER": sat_user, "MIC_PROFILE": mic_profile, "SAT_TYPE": sat_type},
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


def initial_device_info(sat_type: str) -> dict:
    """The device-info.json the overlay seeds — stage 1 rewrites it as it
    progresses; the adoption gadget serves its own copy after boot 2."""
    return {
        "domovoi_setup": 1,
        "nonce": "unbooted",
        "mac": None,
        "board": None,
        "model": None,
        "client_version": None,
        "sat_type": sat_type,
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
        edited = editor(original)
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
