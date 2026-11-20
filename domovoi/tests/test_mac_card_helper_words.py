"""The Mac's menu-bar card helper, as the core and the dashboard tell it.

A Mac side whose root-written ``/var/lib/domovoi-mac/side.json`` says
``"card_helper": 1`` has Domovoi's menu-bar icon > Prepare an SD card...
(mac/Sources/Domovoi/CardWindow.swift). Then, and only then, the card
section, the rooms' empty state, the drive 422 and the zip's README point
there; a Mac side without the flag (an older Mac app under a newer core)
keeps the Finder steps, and Linux, WSL and Windows are as before.

No DB, no hardware, no real /var/lib: a fake record, as in
test_mac_host_kind.py.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from domovoi import host_kind as hk
from domovoi.config import settings
from domovoi.satellite_media import builder

import web.backend.api.satellite_media as media_api
from web.backend.api import capabilities as caps_api

MAC = {"version": "0.2.0-dev", "kind": "mac", "written_at": "2026-11-03T10:00:00Z"}
AP = {"ssid": "Domovoi-Setup-3FA9", "psk": "k7tqmzr4wd"}
STATIC = Path(__file__).resolve().parents[2] / "web" / "static"


@pytest.fixture
def record(tmp_path, monkeypatch):
    path = tmp_path / "domovoi-mac" / "side.json"
    path.parent.mkdir()
    monkeypatch.setenv("DOMOVOI_HOST_SIDE_FILE", str(path))
    monkeypatch.setattr(hk, "_root_only", lambda st: True)
    monkeypatch.setattr(hk, "_detected", lambda: "linux")
    monkeypatch.setattr(settings, "domovoi_host_kind", "auto")
    hk.reset_for_tests()

    def _write(doc):
        path.write_text(json.dumps(doc), encoding="utf-8")
        hk.reset_for_tests()

    yield _write
    hk.reset_for_tests()


@pytest.fixture
def status_stubs(monkeypatch):
    async def no_plugins():
        return []

    monkeypatch.setattr(media_api, "enabled_satellite_plugins", no_plugins)
    monkeypatch.setattr(media_api.fetchers, "docker_available", lambda: False)
    monkeypatch.setattr(media_api.cache, "status", lambda *a: {})


@pytest.mark.parametrize("value,expected", [(1, True), (0, False), (True, False), ("1", False), (2, False), (None, False)])
def test_only_the_number_one_means_the_card_helper(record, value, expected):
    doc = dict(MAC)
    if value is not None:
        doc["card_helper"] = value
    record(doc)
    assert hk.host_kind() == "mac"
    assert hk.mac_card_helper() is expected


def test_never_off_a_mac(record, monkeypatch):
    record({**MAC, "card_helper": 1})
    for kind in ("linux", "wsl", "windows", "darwin"):
        monkeypatch.setattr(settings, "domovoi_host_kind", kind)
        assert hk.mac_card_helper() is False, kind
    # Forced to "mac" with no Mac side: no helper to point at.
    monkeypatch.setenv("DOMOVOI_HOST_SIDE_FILE", "/nonexistent/side.json")
    monkeypatch.setattr(settings, "domovoi_host_kind", "mac")
    hk.reset_for_tests()
    assert hk.mac_card_helper() is False


@pytest.mark.parametrize("flag", [True, False])
def test_status_and_capabilities_carry_the_flag(record, status_stubs, flag):
    record({**MAC, **({"card_helper": 1} if flag else {})})
    body = asyncio.run(media_api.media_status())
    assert body["host_kind"] == "mac" and body["mac_card_helper"] is flag
    assert asyncio.run(caps_api.capabilities())["mac_card_helper"] is flag


def test_linux_status_says_no_helper(status_stubs, monkeypatch):
    monkeypatch.setattr(settings, "domovoi_host_kind", "linux")
    body = asyncio.run(media_api.media_status())
    assert body["mac_card_helper"] is False and body["drive_targets"] is True


@pytest.mark.parametrize("flag", [True, False])
def test_the_drive_refusal_points_where_the_mac_can_go(record, flag):
    record({**MAC, **({"card_helper": 1} if flag else {})})
    body = media_api.PrepareRequest(target={"kind": "drive", "token": "sdb1"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(media_api.media_prepare(body, None))  # type: ignore[arg-type]
    detail = exc.value.detail
    assert "Mac" in detail and "tray icon" not in detail
    if flag:
        assert "menu-bar icon" in detail and "Prepare an SD card" in detail
    else:
        assert "README.txt" in detail and "menu-bar" not in detail


def test_the_mac_readme_leads_with_the_menu_bar_item_only_with_the_flag():
    with_helper = builder.zip_readme(AP, windows_host=False, mac_host=True, mac_card_helper=True)
    flat = " ".join(with_helper.split())
    assert with_helper.startswith("On a Mac, use Domovoi's menu-bar icon")
    assert "Prepare an SD card..." in flat and "No administrator password" in flat
    # The Finder steps stay below it, word for word.
    for phrase in ("Doing it by hand instead", "Finder", "TextEdit", "untick Smart Quotes",
                   '{"ssid": "Domovoi-Setup-3FA9", "psk": "<the password>"}'):
        assert phrase in flat, phrase
    assert AP["psk"] not in with_helper and "tray icon" not in with_helper

    without = builder.zip_readme(AP, windows_host=False, mac_host=True)
    assert without == builder.zip_readme(AP, windows_host=False, mac_host=True, mac_card_helper=False)
    assert without.startswith("Putting these files on a room's SD card, on a Mac")
    assert "menu-bar" not in without
    # A USB card can't be set up on a Mac at all: no helper sentence.
    usb = builder.zip_readme(None, windows_host=False, mac_host=True, mac_card_helper=True)
    assert "menu-bar" not in usb and "Wi-Fi setup portal" in " ".join(usb.split())


def test_the_flag_changes_nothing_off_a_mac():
    for windows in (True, False):
        assert builder.zip_readme(AP, windows_host=windows, mac_card_helper=True) == builder.zip_readme(AP, windows_host=windows)


def test_the_dashboard_reads_the_flag_and_keeps_the_finder_route():
    media = (STATIC / "satellite_media.jsx").read_text(encoding="utf-8")
    assert "const macHelper = onMac && status?.mac_card_helper === true;" in media
    assert "const byHand = onMac && !macHelper;" in media
    assert "{macHelper && (" in media and "{byHand && (" in media
    assert "menu-bar icon writes the card" in media
    assert "copy onto the card in Finder" in media, "the route without the flag stays"
    sats = (STATIC / "satellites.jsx").read_text(encoding="utf-8")
    assert "const macHelper = onMac && caps?.mac_card_helper === true;" in sats
    assert "menu-bar icon's Prepare an SD card..." in sats
