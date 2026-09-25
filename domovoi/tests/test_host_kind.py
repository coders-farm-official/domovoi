"""Host-kind detection, and what the satellite surfaces do inside WSL.

The Windows installer runs Domovoi's Linux stack inside a private WSL2
distro. WSL automounts no removable drives, so USB satellite adoption and
writing a card from media prep can never find anything there. Detection
lives in ``domovoi/host_kind.py``; these tests pin it, and pin that every
drive-dependent surface steps aside on ``wsl`` while staying exactly as it
was everywhere else.

No DB, no hardware, no real /proc: fake kernel files and a monkeypatched
setting, so these run on every box rather than skipping.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest
from fastapi import HTTPException

from domovoi import host_kind as hk
from domovoi.config import settings

import web.backend.api.satellite_media as media_api
import web.backend.satellite_adoption as adoption
from web.backend.api import capabilities as caps_api
from web.backend.api.files_security import removable_drives_visible
from satellite import provisioning_protocol as proto


# ─── Detection ──────────────────────────────────────────────────────────────


def _kernel(tmp_path: Path, osrelease: str | None, version: str | None):
    rel = tmp_path / "osrelease"
    ver = tmp_path / "version"
    if osrelease is not None:
        rel.write_text(osrelease + "\n", encoding="utf-8")
    if version is not None:
        ver.write_text(version + "\n", encoding="utf-8")
    return rel, ver


@pytest.mark.parametrize(
    ("osrelease", "version", "expected"),
    [
        # WSL 2's kernel, lower-case.
        ("6.6.87.2-microsoft-standard-WSL2", None, "wsl"),
        # WSL 1 reports a capitalised Microsoft.
        ("4.4.0-19041-Microsoft", None, "wsl"),
        # An ordinary distro kernel (the Beelink's shape).
        ("7.0.0-14-generic", "Linux version 7.0.0-14-generic (buildd@lcy02)", "linux"),
        # osrelease unreadable: /proc/version is the fallback.
        (None, "Linux version 6.6.87.2-microsoft-standard-WSL2 (root@x)", "wsl"),
        (None, "Linux version 7.0.0-14-generic (buildd@lcy02)", "linux"),
        # Neither readable: plain Linux, never a guess of WSL.
        (None, None, "linux"),
        # osrelease answers, so /proc/version's extra text is never read.
        ("7.0.0-14-generic", "built by someone@microsoft.example", "linux"),
    ],
)
def test_detects_wsl_from_the_kernel(tmp_path, osrelease, version, expected):
    rel, ver = _kernel(tmp_path, osrelease, version)
    assert hk.detect_host_kind("linux", rel, ver) == expected


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("win32", "windows"), ("cygwin", "windows"), ("darwin", "darwin"),
     ("freebsd14", "freebsd14")],
)
def test_other_platforms_come_from_sys_platform(tmp_path, platform, expected):
    # A WSL-looking kernel file must not matter off Linux.
    rel, ver = _kernel(tmp_path, "6.6.87.2-microsoft-standard-WSL2", None)
    assert hk.detect_host_kind(platform, rel, ver) == expected


def test_the_setting_defaults_to_auto():
    from domovoi.config import Settings

    assert Settings.model_fields["domovoi_host_kind"].default == "auto"


@pytest.mark.parametrize(
    ("setting", "expected"),
    [("wsl", "wsl"), (" WSL ", "wsl"), ("linux", "linux"),
     ("windows", "windows"), ("auto", "darwin"), ("", "darwin")],
)
def test_the_override_wins_over_detection(monkeypatch, setting, expected):
    monkeypatch.setattr(hk, "_detected", lambda: "darwin")
    monkeypatch.setattr(settings, "domovoi_host_kind", setting)
    assert hk.host_kind() == expected


def test_a_misspelt_override_is_ignored_and_logged_once(monkeypatch, caplog):
    monkeypatch.setattr(hk, "_detected", lambda: "linux")
    monkeypatch.setattr(hk, "_WARNED", set())
    monkeypatch.setattr(settings, "domovoi_host_kind", "wls")
    with caplog.at_level(logging.WARNING, logger=hk.__name__):
        assert hk.host_kind() == "linux"
        assert hk.host_kind() == "linux"
    warned = [r for r in caplog.records if "DOMOVOI_HOST_KIND" in r.getMessage()]
    assert len(warned) == 1


# ─── The satellite surfaces inside WSL ──────────────────────────────────────


@pytest.fixture
def host(monkeypatch):
    """Pin the host kind for one test: ``host("wsl")``."""
    def _set(kind: str) -> None:
        monkeypatch.setattr(settings, "domovoi_host_kind", kind)
    return _set


@pytest.fixture
def card(tmp_path, monkeypatch):
    """A flashed Pi boot partition that detect_removable() reports."""
    boot = tmp_path / "bootfs"
    boot.mkdir()
    for name in ("config.txt", "cmdline.txt", "start4.elf", "bcm2710-rpi-zero-2-w.dtb"):
        (boot / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        media_api, "detect_removable",
        lambda: [{"mount": str(boot), "device": "/dev/sdb1", "read_only": False}],
    )
    return boot


def test_removable_drives_are_invisible_only_inside_wsl(host):
    host("wsl")
    assert removable_drives_visible() is False
    for kind in ("linux", "windows", "darwin"):
        host(kind)
        assert removable_drives_visible() is True


def test_media_targets_are_hidden_inside_wsl(host, card):
    host("wsl")
    assert asyncio.run(media_api.media_targets()) == []


def test_media_targets_unchanged_elsewhere(host, card):
    host("linux")
    targets = asyncio.run(media_api.media_targets())
    assert [t["kind"] for t in targets] == ["drive"]


def test_setup_transport_defaults_to_the_portal_inside_wsl(host):
    host("wsl")
    assert media_api.PrepareRequest().setup_transport == "portal"
    # An explicit choice is still the caller's.
    assert media_api.PrepareRequest(setup_transport="usb").setup_transport == "usb"


def test_setup_transport_default_unchanged_elsewhere(host):
    host("linux")
    assert media_api.PrepareRequest().setup_transport == "usb"


def test_writing_to_a_drive_is_refused_inside_wsl_with_the_way_out(host):
    host("wsl")
    body = media_api.PrepareRequest(target={"kind": "drive", "token": "sdb1"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(media_api.media_prepare(body, None))  # type: ignore[arg-type]
    assert exc.value.status_code == 422
    assert "WSL" in exc.value.detail and "zip" in exc.value.detail


def test_media_status_tells_the_card_what_this_host_can_do(host, monkeypatch):
    async def no_plugins():
        return []

    monkeypatch.setattr(media_api, "enabled_satellite_plugins", no_plugins)
    monkeypatch.setattr(media_api.fetchers, "docker_available", lambda: False)
    monkeypatch.setattr(media_api.cache, "status", lambda *a: {})

    host("wsl")
    body = asyncio.run(media_api.media_status())
    assert body["host_kind"] == "wsl"
    assert body["drive_targets"] is False
    assert body["default_setup_transport"] == "portal"

    host("linux")
    body = asyncio.run(media_api.media_status())
    assert body["drive_targets"] is True
    assert body["default_setup_transport"] == "usb"


NONCE = "0d15ea5e0ddba11a"


@pytest.fixture
def gadget(tmp_path, monkeypatch):
    """A DOMOVOI-SET volume reachable through detect_removable()."""
    monkeypatch.delenv("SATELLITE_ADOPTION_SCAN_DIRS", raising=False)
    monkeypatch.setattr(settings, "satellite_adoption_enabled", True)
    vol = tmp_path / "DOMOVOI-SET"
    vol.mkdir()
    info = proto.build_device_info(
        nonce=NONCE, mac="b8:27:eb:11:22:33",
        board="raspberry_pi_zero_2_w", model="Raspberry Pi Zero 2 W Rev 1.0",
        profiles_supported=["xvf3800_usb"],
    )
    (vol / proto.DEVICE_INFO_NAME).write_text(json.dumps(info), encoding="utf-8")
    monkeypatch.setattr(
        adoption, "detect_removable",
        lambda: [{"mount": str(vol), "device": "/dev/sdb1", "read_only": False}],
    )
    # The label read would ask udev/lsblk about a device that isn't real.
    monkeypatch.setattr(adoption, "_volume_label", lambda *a: None)
    adoption.invalidate_cache()
    yield vol
    adoption.invalidate_cache()


def test_usb_adoption_does_not_scan_inside_wsl(host, gadget):
    host("wsl")
    assert asyncio.run(adoption.snapshot_pending()) == []


def test_usb_adoption_dev_harness_still_works_inside_wsl(host, gadget, monkeypatch):
    host("wsl")
    monkeypatch.setenv("SATELLITE_ADOPTION_SCAN_DIRS", str(gadget))
    pending = asyncio.run(adoption.snapshot_pending())
    assert [p["pending_id"] for p in pending] == [NONCE]


def test_usb_adoption_unchanged_elsewhere(host, gadget):
    host("linux")
    pending = asyncio.run(adoption.snapshot_pending())
    assert [p["pending_id"] for p in pending] == [NONCE]


def test_capabilities_carry_the_host_kind(host):
    host("wsl")
    assert asyncio.run(caps_api.capabilities())["host_kind"] == "wsl"
    host("linux")
    assert asyncio.run(caps_api.capabilities())["host_kind"] == "linux"
