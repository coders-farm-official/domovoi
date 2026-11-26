"""File pickers accept what Android calls the file (owner report, 2026-11-25).

Android's file picker filters by MIME type, and a zip saved on a phone is
often ``application/x-zip-compressed``, ``application/x-zip`` or
``application/octet-stream``. The Plugins page accepted only
``application/zip``, so a downloaded plugin zip was greyed out and could
not be installed from a phone. The page then checks the name itself.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "web" / "static"
PHONE_ZIP_TYPES = ("application/x-zip-compressed", "application/x-zip", "application/octet-stream")


def _accepts(src: str, needle: str) -> list[str]:
    """The accept="…" values on the line(s) that mention ``needle``."""
    out = []
    for m in re.finditer(r'accept="([^"]*)"', src):
        if needle in m.group(1):
            out.append(m.group(1))
    return out


def test_the_plugin_zip_picker_accepts_every_zip_type_a_phone_uses() -> None:
    src = (STATIC / "plugins.jsx").read_text(encoding="utf-8")
    (accept,) = _accepts(src, ".zip")
    types = accept.split(",")
    assert ".zip" in types and "application/zip" in types
    for t in PHONE_ZIP_TYPES:
        assert t in types, t


def test_the_plugin_page_turns_away_a_file_that_isnt_a_zip() -> None:
    src = (STATIC / "plugins.jsx").read_text(encoding="utf-8")
    assert "/\\.zip$/i.test(file.name" in src
    assert "That isn't a plugin zip." in src


def test_the_music_upload_takes_a_phone_zip_too() -> None:
    src = (STATIC / "music.jsx").read_text(encoding="utf-8")
    (accept,) = _accepts(src, ".zip")
    for t in ("application/zip", "application/x-zip-compressed", "application/x-zip"):
        assert t in accept.split(","), t


def test_the_voice_files_stay_pickable_on_a_phone() -> None:
    src = (STATIC / "settings.jsx").read_text(encoding="utf-8")
    (onnx,) = _accepts(src, ".onnx,")
    assert "application/octet-stream" in onnx.split(",")
    (cfg,) = _accepts(src, ".json,")
    assert {"application/json", "application/octet-stream"} <= set(cfg.split(","))


def test_no_zip_picker_accepts_only_application_zip() -> None:
    """The bug's shape, anywhere in the dashboard: a zip picker whose only
    MIME type is application/zip."""
    bad = []
    for f in STATIC.glob("*.jsx"):
        for accept in _accepts(f.read_text(encoding="utf-8"), ".zip"):
            mimes = {t for t in accept.split(",") if "/" in t and "zip" in t}
            if mimes == {"application/zip"}:
                bad.append(f"{f.name}: {accept}")
    assert bad == []
