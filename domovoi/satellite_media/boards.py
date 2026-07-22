"""Board profiles for satellite media preparation.

A BoardProfile describes what the overlay/payload pipeline must produce for
one compute board: which stock OS the user flashes, the Python tag the
offline wheels must match, and how the boot partition is customized. Only
the Pi Zero 2 W path is supported today; the Radxa Zero 3W needs the
phase-2 image-injection investigation (its boot layout has no guaranteed
Windows-writable FAT hook) and is listed unsupported so the dashboard can
say so honestly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoardProfile:
    id: str
    label: str
    supported: bool
    # What the user flashes with any imaging tool before inserting the card.
    stock_os: str
    # Wheel targeting for the fully-offline payload.
    python_version: str          # e.g. "3.13"
    manylinux_platforms: tuple[str, ...]
    os_release: str              # matching Debian release for the deb cache
    # Boot-partition marker file that identifies a flashed card of this
    # board (target auto-detection).
    boot_marker: str
    note: str = ""


PI02W = BoardProfile(
    id="pi02w",
    label="Raspberry Pi Zero 2 W (and Pi 3/4/5)",
    supported=True,
    stock_os="Raspberry Pi OS Lite (64-bit), Trixie",
    python_version="3.13",
    manylinux_platforms=(
        "manylinux_2_17_aarch64",
        "manylinux_2_28_aarch64",
        "manylinux_2_34_aarch64",
    ),
    os_release="trixie",
    boot_marker="bcm2710-rpi-zero-2-w.dtb",
)

RADXA_ZERO3W = BoardProfile(
    id="radxa-zero3w",
    label="Radxa Zero 3W (video satellite)",
    supported=False,
    stock_os="Armbian / Radxa Debian",
    python_version="3.13",
    manylinux_platforms=(
        "manylinux_2_17_aarch64",
        "manylinux_2_28_aarch64",
        "manylinux_2_34_aarch64",
    ),
    os_release="trixie",
    boot_marker="",
    note=(
        "Prepared media for the RK3566 boot layout is not supported yet — "
        "provision manually per satellite/VIDEO_SATELLITE.md. (The board "
        "itself is fully supported once provisioned.)"
    ),
)

BOARDS: dict[str, BoardProfile] = {b.id: b for b in (PI02W, RADXA_ZERO3W)}

# Mic-board choices the prepare flow offers; drives stage-1's overlay/audio
# steps and the default [device] profile in the adopted config.
MIC_PROFILES = (
    "respeaker_2mic_hat_v2",
    "respeaker_2mic_hat_v1",
    "xvf3800_usb",
    "none",
)


def device_profile_for_mic(mic_profile: str) -> str:
    """The client [device] profile a mic choice maps to ('none' boots as a
    HAT-profile voice satellite with the mic stack off until adopted)."""
    if mic_profile == "xvf3800_usb":
        return "xvf3800_usb"
    return "respeaker_2mic_hat"
