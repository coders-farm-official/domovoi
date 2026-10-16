"""The 1.0 previews say so where people choose them (owner decision 2,
2026-10-16): the Windows installer, the phone's on-device assistant, and
spare computers and phones as rooms. Small, factual labels, never a dead
control. Pure file reads (the app's side: RoomModePreviewTest and
AssistantScreenTest in android/app/src/test).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_the_windows_installer_calls_itself_a_preview_not_a_test_build():
    iss = _read("windows/installer/DomovoiSetup.iss")
    assert re.search(r"^AppVerName=Domovoi \{#AppVersion\} \(preview, ", iss, re.M)
    assert re.search(r"^VersionInfoDescription=Domovoi Setup \(preview\)$", iss, re.M)
    shown = iss + _read("windows/helper/Preflight.cs")
    assert "test build" not in shown.lower()
    assert "test build" not in _read("docs/WINDOWS_HOST.md").lower()


def test_the_dashboard_s_spare_device_card_carries_the_preview_tag():
    components = _read("web/static/components.jsx")
    assert "const PreviewTag = () =>" in components
    assert re.search(r"Object\.assign\(window, \{[^}]*\bPreviewTag\b", components, re.S)
    assert ".preview-tag {" in _read("web/static/styles.css")
    sats = _read("web/static/satellites.jsx")
    header = sats.split(">add a spare device</span>", 1)[1].split("</button>", 1)[0]
    assert "<PreviewTag/>" in header, "the tag sits in the card's header, beside its name"


def test_the_usb_stick_for_old_computers_is_labelled_a_preview_too():
    """The room stick (wf/usb-stick) is a spare device as a room: its column
    on the card, its guide section and its release note say so."""
    sats = _read("web/static/satellites.jsx")
    column = sats.split("const SpareUsbStick = ", 1)[1].split("const SpareDeviceCard", 1)[0]
    title = column.split('<div className="label">old computer (usb stick)</div>', 1)[1].split("</div>", 1)[0]
    assert "<PreviewTag/>" in title, "the tag sits beside the column's name"
    guide = _read("docs/SPARE_DEVICE_SATELLITES.md").split("## Old computers (USB stick)", 1)[1].split("\n## ", 1)[0]
    assert "**The USB stick is a preview**" in guide
    notes = _read("docs/RELEASE_NOTES.md").split("— Old computers become rooms from a USB stick", 1)[1]
    assert "A **preview**" in notes.split("\n## ", 1)[0]
    readme = _read("README.md")
    previews = readme.split("**Previews**", 1)[1].split("\n**", 1)[0]
    assert "USB stick" in previews


def test_the_join_page_says_a_phone_room_is_a_preview():
    js = _read("domovoi/join_page/join.js")
    lede = next(line for line in js.splitlines() if "text('lede', 'It becomes the room" in line)
    assert "a preview" in lede


def test_the_app_labels_the_assistant_s_setup_and_room_mode():
    app = REPO / "android" / "app" / "src" / "main" / "java" / "com" / "domovoi" / "app"
    assert "fun PreviewLabel(" in (app / "ui" / "components" / "Kit.kt").read_text(encoding="utf-8")
    for rel in ("ui/screens/assistant/AssistantSetupScreen.kt", "roommode/RoomModeEntryCard.kt", "roommode/RoomSetupActivity.kt"):
        assert "PreviewLabel(" in (app / rel).read_text(encoding="utf-8"), rel


def test_the_readme_lists_the_previews_under_their_name():
    readme = _read("README.md")
    assert "**Previews**" in readme
    assert "early build" not in readme and "test build" not in readme
