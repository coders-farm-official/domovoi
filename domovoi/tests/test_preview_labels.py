"""Only the USB stick for old computers is labelled a preview (owner
decision, 2026-11-17: it has not been tried on real computers yet). The
Windows installer, the phone's on-device chat and spare laptops, PCs and
phones as rooms are no longer previews, and the dashboard, the docs, the
manual and the installer must not say they are. Pure file reads.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _is_preview_line(line: str) -> bool:
    return bool(re.search(r"\(preview|\bpreview\)|a preview\b|previews\b|\*\(Preview\)\*|<PreviewTag", line, re.I))


def test_the_windows_installer_is_not_called_a_preview():
    iss = _read("windows/installer/DomovoiSetup.iss")
    assert re.search(r"^AppVerName=Domovoi \{#AppVersion\} \(\{#Copy\(", iss, re.M)
    assert re.search(r"^VersionInfoDescription=Domovoi Setup$", iss, re.M)
    sat = _read("windows/installer/DomovoiSatelliteSetup.iss")
    assert "preview" not in sat.lower()
    shown = iss + _read("windows/helper/Preflight.cs")
    assert "preview" not in shown.lower()
    assert "test build" not in shown.lower()
    host = _read("docs/WINDOWS_HOST.md")
    assert "preview" not in host.lower() and "test build" not in host.lower()


def test_the_dashboard_labels_only_the_usb_stick():
    components = _read("web/static/components.jsx")
    assert "const PreviewTag = () =>" in components
    assert re.search(r"Object\.assign\(window, \{[^}]*\bPreviewTag\b", components, re.S)
    assert ".preview-tag {" in _read("web/static/styles.css")
    sats = _read("web/static/satellites.jsx")
    assert sats.count("<PreviewTag/>") == 1, "the USB stick's column is the only place that wears it"
    column = sats.split("const SpareUsbStick = ", 1)[1].split("const SpareDeviceCard", 1)[0]
    title = column.split('<div className="label">old computer (usb stick)</div>', 1)[1].split("</div>", 1)[0]
    assert "<PreviewTag/>" in title, "the tag sits beside the column's name"
    assert "the stick is a preview" in " ".join(column.split()).lower()
    card = sats.split("const SpareDeviceCard", 1)[1]
    header = card.split(">add a spare device</span>", 1)[1].split("</button>", 1)[0]
    assert "preview" not in header.lower()
    for line in card.splitlines():
        if "<PreviewTag/>" in line or "USB stick is a preview" in line:
            continue
        assert "a preview" not in line, line


def test_the_usb_stick_is_labelled_a_preview_in_the_docs():
    guide = _read("docs/SPARE_DEVICE_SATELLITES.md").split("## Old computers (USB stick)", 1)[1].split("\n## ", 1)[0]
    assert "**The USB stick is a preview**" in guide
    notes = _read("docs/RELEASE_NOTES.md").split("— Old computers become rooms from a USB stick", 1)[1]
    assert "A **preview**" in notes.split("\n## ", 1)[0]
    readme = _read("README.md")
    works = readme.split("**Works today**", 1)[1].split("\n**", 1)[0]
    usb = " ".join(next(p for p in works.split("\n- ") if "USB stick" in p).split())
    assert "virtual machines" in usb and "real computers" in usb
    manual = _read("scripts/manual/manual.md")
    assert "### An old computer, from a USB stick *(Preview)*" in manual
    assert manual.count("*(Preview)*") == 2, "one heading, plus the line that explains the pill"


def test_the_others_are_not_called_previews_anywhere_users_read():
    # The spare-device guide: everything outside the USB stick section.
    guide = _read("docs/SPARE_DEVICE_SATELLITES.md")
    head, rest = guide.split("## Old computers (USB stick)", 1)
    tail = rest.split("\n## ", 1)[1] if "\n## " in rest else ""
    for part in (head, tail):
        for line in part.splitlines():
            assert "a preview" not in line.lower() or "usb stick" in line.lower(), line
    # The join page's lede (phone as a room) and the manual's sections.
    assert "preview" not in _read("domovoi/join_page/join.js").lower()
    manual = _read("scripts/manual/manual.md")
    for title in ("### Domovoi on this phone", "### A spare laptop or PC", "### An old phone or tablet as a room"):
        assert title in manual and f"{title} *(Preview)*" not in manual
    assert "Windows installer *(Preview)*" not in manual
    for rel in ("docs/FAQ.md", "docs/SETUP_RUNBOOK.md", "docs/ANDROID_COMPATIBILITY.md", "docs/SATELLITE_HARDWARE.md", "docs/LICENSES.md"):
        for line in _read(rel).splitlines():
            if _is_preview_line(line) and "install preview" not in line.lower() and "plugin" not in line.lower() and "camera preview" not in line.lower() and "usb" not in line.lower():
                raise AssertionError(f"{rel}: {line}")
    readme = _read("README.md")
    for line in readme.splitlines():
        if re.search(r"\(preview\)|; preview\)|a preview|preview:|\bpreview,|previews\)", line, re.I):
            assert "USB stick" in line, line


def test_the_readme_has_no_previews_section_and_says_what_is_untested():
    readme = _read("README.md")
    assert "**Previews**" not in readme
    assert "early build" not in readme and "test build" not in readme
    works = readme.split("**Works today**", 1)[1].split("\n**", 1)[0]
    for line in ("Chat on your phone", "A Windows installer", "Satellites on spare devices"):
        assert f"- **{line}" in works, line
    assert "not code-signed" in works
