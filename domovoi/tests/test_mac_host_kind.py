"""The Mac installer's Linux VM as a host kind (``mac``), and what the
drive-dependent surfaces do there.

The Mac installer runs Domovoi's Linux side in a Lima VM. Its kernel is a
plain Ubuntu one, so ``host_kind`` used to answer ``linux`` and the
dashboard offered USB adoption (the default!) and a drive list that can
never fill: Lima's VM gets none of the Mac's USB devices. The VM is told
apart by the Mac helper's root-written ``/var/lib/domovoi-mac/side.json``
(``"kind": "mac"``). These tests pin the detection and pin that every
surface that steps aside on ``wsl`` steps aside on ``mac`` too, with Mac
words, while every other host is exactly as before
(mac-installer PHASE8-PARITY.md 2.3).

No DB, no hardware, no real /var/lib: a fake record and monkeypatched
checks, so these run on every box.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from domovoi import host_kind as hk
from domovoi.config import settings

import web.backend.api.satellite_media as media_api
import web.backend.satellite_adoption as adoption
from web.backend.api import auth
from web.backend.api import capabilities as caps_api
from web.backend.api.files_security import removable_drives_visible


@pytest.fixture
def record(tmp_path, monkeypatch):
    """A Mac helper's record at a test path, root-owned as far as the
    check knows. ``record(doc)`` writes it; ``record(None)`` removes it."""
    path = tmp_path / "domovoi-mac" / "side.json"
    path.parent.mkdir()
    monkeypatch.setenv("DOMOVOI_HOST_SIDE_FILE", str(path))
    monkeypatch.setattr(hk, "_root_only", lambda st: True)
    monkeypatch.setattr(hk, "_detected", lambda: "linux")
    monkeypatch.setattr(settings, "domovoi_host_kind", "auto")
    hk.reset_for_tests()

    def _write(doc):
        if doc is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
        hk.reset_for_tests()
        return path

    yield _write
    hk.reset_for_tests()


MAC = {"version": "0.2.0-dev", "build_commit": "e" * 40, "kind": "mac",
       "written_at": "2026-10-30T22:02:01Z"}


# ─── detection ──────────────────────────────────────────────────────────────


def test_the_mac_helpers_record_makes_a_linux_host_a_mac(record):
    record(MAC)
    assert hk.host_kind() == "mac"
    assert hk.is_mac_vm() is True and hk.is_wsl() is False
    assert hk.sees_removable_drives() is False


def test_no_record_is_plain_linux(record):
    record(None)
    assert hk.host_kind() == "linux"
    assert hk.sees_removable_drives() is True


@pytest.mark.parametrize("doc", [
    {**MAC, "kind": "windows"},       # only "mac" means the Mac VM
    {k: v for k, v in MAC.items() if k != "kind"},
    "not json {",
    json.dumps(["kind", "mac"]),
    json.dumps({**MAC, "pad": "x" * 5000}),   # far bigger than a record
])
def test_an_odd_record_is_not_a_mac(record, doc):
    record(doc)
    assert hk.host_kind() == "linux"


def test_a_record_anyone_but_root_could_write_is_ignored(record, monkeypatch):
    record(MAC)
    monkeypatch.setattr(hk, "_root_only", lambda st: False)
    hk.reset_for_tests()
    assert hk.host_kind() == "linux"


@pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt",
                    reason="symlinks need a POSIX test box")
def test_a_symlinked_record_is_ignored(record, tmp_path):
    real = tmp_path / "elsewhere.json"
    real.write_text(json.dumps(MAC), encoding="utf-8")
    path = record(None)
    path.symlink_to(real)
    hk.reset_for_tests()
    assert hk.host_kind() == "linux"


def test_a_record_written_after_the_start_is_seen_on_the_next_look(record, monkeypatch):
    # The helper writes it after the core's first health check.
    record(None)
    now = [1000.0]
    monkeypatch.setattr(hk.time, "monotonic", lambda: now[0])
    hk.reset_for_tests()
    assert hk.host_kind() == "linux"
    path = Path(os.environ["DOMOVOI_HOST_SIDE_FILE"])
    path.write_text(json.dumps(MAC), encoding="utf-8")
    assert hk.host_kind() == "linux"                # still within the 10 s
    now[0] += hk._MAC_RECHECK_SEC + 0.1
    assert hk.host_kind() == "mac"


def test_wsl_and_other_kernels_never_read_the_record(record, monkeypatch):
    record(MAC)
    for kind in ("wsl", "windows", "darwin"):
        monkeypatch.setattr(hk, "_detected", lambda k=kind: k)
        assert hk.host_kind() == kind


@pytest.mark.parametrize(("setting", "expected"), [("linux", "linux"), ("mac", "mac"), ("wsl", "wsl")])
def test_the_override_still_wins(record, monkeypatch, setting, expected):
    record(MAC if setting == "linux" else None)
    monkeypatch.setattr(settings, "domovoi_host_kind", setting)
    assert hk.host_kind() == expected


def test_the_real_path_is_the_one_the_version_card_reads():
    from domovoi import windows_side

    assert hk.HOST_SIDE_FILE == windows_side.HOST_SIDE_FILE
    assert "mac" in windows_side.HOST_KINDS


# ─── the satellite surfaces on a Mac ────────────────────────────────────────


@pytest.fixture
def host(monkeypatch):
    def _set(kind: str) -> None:
        monkeypatch.setattr(settings, "domovoi_host_kind", kind)
    return _set


def test_no_drives_on_a_mac_and_everywhere_else_as_before(host):
    host("mac")
    assert removable_drives_visible() is False
    host("wsl")
    assert removable_drives_visible() is False
    for kind in ("linux", "windows", "darwin"):
        host(kind)
        assert removable_drives_visible() is True


def test_a_mac_card_defaults_to_the_wifi_portal(host):
    host("mac")
    assert media_api.PrepareRequest().setup_transport == "portal"
    host("linux")
    assert media_api.PrepareRequest().setup_transport == "usb"


def test_writing_to_a_drive_on_a_mac_is_refused_with_the_mac_way_out(host):
    host("mac")
    body = media_api.PrepareRequest(target={"kind": "drive", "token": "sdb1"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(media_api.media_prepare(body, None))  # type: ignore[arg-type]
    assert exc.value.status_code == 422
    detail = exc.value.detail
    assert "Mac" in detail and "README.txt" in detail
    assert "tray icon" not in detail and "WSL" not in detail


def test_the_wsl_refusal_is_unchanged(host):
    host("wsl")
    body = media_api.PrepareRequest(target={"kind": "drive", "token": "sdb1"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(media_api.media_prepare(body, None))  # type: ignore[arg-type]
    assert exc.value.detail == media_api._NO_DRIVES_DETAIL


def test_media_status_on_a_mac(host, monkeypatch):
    async def no_plugins():
        return []

    monkeypatch.setattr(media_api, "enabled_satellite_plugins", no_plugins)
    monkeypatch.setattr(media_api.fetchers, "docker_available", lambda: False)
    monkeypatch.setattr(media_api.cache, "status", lambda *a: {})
    host("mac")
    body = asyncio.run(media_api.media_status())
    assert body["host_kind"] == "mac"
    assert body["drive_targets"] is False
    assert body["default_setup_transport"] == "portal"


def test_usb_adoption_does_not_scan_on_a_mac(host, monkeypatch, tmp_path):
    monkeypatch.delenv("SATELLITE_ADOPTION_SCAN_DIRS", raising=False)
    monkeypatch.setattr(settings, "satellite_adoption_enabled", True)
    seen = []
    monkeypatch.setattr(adoption, "detect_removable", lambda: seen.append(1) or [])
    adoption.invalidate_cache()
    host("mac")
    try:
        assert asyncio.run(adoption.snapshot_pending()) == []
        assert seen == []
    finally:
        adoption.invalidate_cache()


def test_capabilities_say_mac(host):
    host("mac")
    assert asyncio.run(caps_api.capabilities())["host_kind"] == "mac"


# ─── the setup code and the help desk ───────────────────────────────────────


def test_the_setup_code_refusal_on_a_mac_points_at_the_app(monkeypatch):
    monkeypatch.setattr(auth, "host_kind", lambda: "mac")
    mac = auth.setup_code_refusal()
    assert mac.startswith("setup code required")
    assert "Show Setup Code" in mac and "menu bar" in mac
    assert "~/.domovoi" not in mac and "Windows" not in mac


def test_the_help_desk_names_the_mac_vm(monkeypatch):
    from domovoi.help_desk import diagnostics

    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hk, "read_mac_marker", lambda path=None: True)
    real_open = open

    def fake_open(path, *a, **kw):
        if str(path) == "/proc/version":
            raise OSError("no /proc here")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", fake_open)
    assert diagnostics.host_kind() == "macOS (Linux VM)"
    monkeypatch.setattr(hk, "read_mac_marker", lambda path=None: False)
    assert diagnostics.host_kind() == "Linux"


# ─── the zip's README on a Mac ──────────────────────────────────────────────


def test_the_mac_readme_is_the_finder_route():
    from domovoi.satellite_media import builder

    ap = {"ssid": "Domovoi-Setup-3FA9", "psk": "k7tqmzr4wd"}
    portal = builder.zip_readme(ap, windows_host=False, mac_host=True)
    flat = " ".join(portal.split())        # phrases may wrap across lines
    for phrase in ("on a Mac", "Raspberry Pi OS Lite (64-bit)", "choose No", "bootfs",
                   "Finder", "TextEdit", "Never replace the card's cmdline.txt",
                   "long line (TextEdit may wrap it", "Type a space", "Make Plain Text",
                   "untick Smart Quotes",
                   '{"ssid": "Domovoi-Setup-3FA9", "psk": "<the password>"}',
                   "ap.json", "set it up from a phone", "no passwords"):
        assert phrase in flat, phrase
    assert ap["psk"] not in portal
    assert "tray icon" not in portal and "Notepad" not in portal
    usb = " ".join(builder.zip_readme(None, windows_host=False, mac_host=True).split())
    assert "Wi-Fi setup portal" in usb and "ap.json" not in usb
    assert "plug it into the Domovoi server's USB" not in usb


def test_the_readme_elsewhere_is_unchanged():
    from domovoi.satellite_media import builder

    ap = {"ssid": "Domovoi-Setup-3FA9", "psk": "k7tqmzr4wd"}
    assert builder.zip_readme(ap, windows_host=False) == builder.zip_readme(
        ap, windows_host=False, mac_host=False)
    assert builder.zip_readme(ap, windows_host=False).splitlines()[0] == "Domovoi satellite overlay"
    # Windows wins over a stray Mac flag (they can't both be true).
    assert builder.zip_readme(ap, windows_host=True, mac_host=True).startswith("On Windows")
