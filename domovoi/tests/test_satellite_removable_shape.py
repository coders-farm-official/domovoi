"""The real-hardware path through ``detect_removable()``.

Every other satellite-onboarding test reaches the scanner through the
``SATELLITE_ADOPTION_SCAN_DIRS`` dev harness (or stubs the builder), which
returns *before* ``detect_removable()`` is ever called. That left the one
code path a real SD card or gadget volume actually takes uncovered — and it
was broken: ``detect_removable()`` yields ``{"mount", "device",
"read_only"}`` dicts, but both consumers treated the elements as strings.

These tests deliberately take no shortcut: no SCAN_DIRS, no DB, so they run
everywhere rather than skipping quietly.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import web.backend.api.satellite_media as media_api
from web.backend.api.files_security import drive_token
import web.backend.satellite_adoption as adoption
from satellite import provisioning_protocol as proto

NONCE = "4c1d90fe2ab73155"


def _fake_removable(mount: Path):
    """The exact shape ``detect_removable()`` promises its callers."""
    return lambda: [
        {"mount": str(mount), "device": "/dev/sdb1", "read_only": False}
    ]


@pytest.fixture
def usb(tmp_path, monkeypatch):
    """A gadget-shaped volume reachable ONLY via detect_removable()."""
    monkeypatch.delenv("SATELLITE_ADOPTION_SCAN_DIRS", raising=False)
    vol = tmp_path / "DOMOVOI-SET"
    vol.mkdir()
    info = proto.build_device_info(
        nonce=NONCE,
        mac="b8:27:eb:11:22:33",
        board="raspberry_pi_zero_2_w",
        model="Raspberry Pi Zero 2 W Rev 1.0",
        profiles_supported=["xvf3800_usb"],
    )
    (vol / proto.DEVICE_INFO_NAME).write_text(json.dumps(info), encoding="utf-8")
    adoption.invalidate_cache()
    yield vol
    adoption.invalidate_cache()


def test_candidate_mounts_reads_the_dict_shape(usb, monkeypatch):
    monkeypatch.setattr(adoption, "detect_removable", _fake_removable(usb))
    assert adoption._candidate_mounts() == [(Path(usb), True)]


def test_candidate_mounts_skips_entries_without_a_mount(monkeypatch):
    monkeypatch.delenv("SATELLITE_ADOPTION_SCAN_DIRS", raising=False)
    monkeypatch.setattr(
        adoption, "detect_removable", lambda: [{"device": "/dev/sdb1"}]
    )
    assert adoption._candidate_mounts() == []


def test_scan_pending_sees_a_real_removable_volume(usb, monkeypatch):
    """The production path: a plugged-in gadget volume becomes a pending
    satellite without the SCAN_DIRS harness."""
    monkeypatch.setattr(adoption, "detect_removable", _fake_removable(usb))
    pending = adoption.scan_pending()
    assert [p["pending_id"] for p in pending] == [NONCE]
    assert pending[0]["board"] == "raspberry_pi_zero_2_w"
    assert adoption.mount_for(NONCE) == Path(usb)


def test_media_targets_reads_the_dict_shape(tmp_path, monkeypatch):
    """An inserted card must enumerate as a drive target, not blow up the
    endpoint. ``Path(dict)`` raised TypeError, which the route's ``except
    OSError`` never caught."""
    card = tmp_path / "bootfs"
    card.mkdir()
    monkeypatch.setattr(media_api, "detect_removable", _fake_removable(card))
    targets = asyncio.run(media_api.media_targets())
    assert len(targets) == 1
    assert targets[0]["kind"] == "drive"
    # drive_token is platform-shaped (a letter on Windows, the last path
    # segment on POSIX) — assert agreement, not a hard-coded value.
    assert targets[0]["token"] == drive_token(str(card))
    assert targets[0]["looks_like_pi_boot"] is False
