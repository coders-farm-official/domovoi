"""The iPhone app's App Store material: lengths, the icon, the privacy manifest.

App Store Connect refuses text over its limits only when someone pastes it,
and refuses an icon with an alpha channel only when a build is uploaded; a
privacy manifest that declares the wrong required-reason APIs is found by
App Review, or not at all. These checks find all three in the repo first.

Limits (App Store Connect's own): name and subtitle 30 characters,
promotional text 170, description and What's New 4000, keywords 100 bytes in
all, review notes 4000 bytes.
"""

from __future__ import annotations

import json
import plistlib
import re
import struct
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
IOS = REPO / "ios"
STORE = IOS / "store"
ICONSET = IOS / "Domovoi" / "Assets.xcassets" / "AppIcon.appiconset"
MANIFEST = IOS / "Domovoi" / "PrivacyInfo.xcprivacy"


def _text(name: str) -> str:
    return (STORE / name).read_text(encoding="utf-8").rstrip("\n")


@pytest.mark.parametrize(
    ("name", "limit"),
    [
        ("name.txt", 30),
        ("subtitle.txt", 30),
        ("promotional_text.txt", 170),
        ("description.txt", 4000),
        ("WHATS-NEW-1.0.0.txt", 4000),
    ],
)
def test_store_text_fits_its_field(name: str, limit: int) -> None:
    text = _text(name)
    assert text.strip(), f"{name} is empty"
    assert len(text) <= limit, f"{name} is {len(text)} characters; App Store Connect takes {limit}"


def test_single_line_fields_are_one_line() -> None:
    for name in ("name.txt", "subtitle.txt", "keywords.txt", "support_url.txt", "privacy_url.txt"):
        assert "\n" not in _text(name), f"{name} must be one line"


def test_keywords_fit_and_are_a_clean_list() -> None:
    text = _text("keywords.txt")
    assert len(text.encode("utf-8")) <= 100, f"keywords are {len(text.encode('utf-8'))} bytes; the limit is 100"
    words = text.split(",")
    assert all(w.strip() == w and w for w in words), "keywords: no empty entries, no spaces around commas"
    assert len({w.lower() for w in words}) == len(words), "keywords: a repeated keyword wastes bytes"


def test_review_notes_fit_in_4000_bytes() -> None:
    size = len((STORE / "REVIEW-NOTES.txt").read_bytes())
    assert size <= 4000, f"REVIEW-NOTES.txt is {size} bytes; App Review takes 4000"


def test_urls_are_https() -> None:
    for name in ("support_url.txt", "privacy_url.txt"):
        assert re.fullmatch(r"https://\S+", _text(name)), f"{name} must be one https URL"


def test_store_text_names_no_other_platform() -> None:
    """Guideline 2.3.10: no other mobile platform in the metadata."""
    banned = re.compile(r"(?i)\b(android|google play|play store|apk)\b")
    for name in ("name.txt", "subtitle.txt", "promotional_text.txt", "description.txt",
                 "keywords.txt", "WHATS-NEW-1.0.0.txt"):
        hit = banned.search(_text(name))
        assert hit is None, f"{name} mentions {hit.group(0)!r}"


# ---- the icon -----------------------------------------------------------------

def _png_header(path: Path) -> tuple[int, int, int]:
    """(width, height, colour type) from a PNG's IHDR, without Pillow."""
    data = path.read_bytes()[:33]
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR", f"{path.name} is not a PNG"
    width, height, _depth, colour = struct.unpack(">IIBB", data[16:26])
    return width, height, colour


def test_app_icon_set_is_complete_and_the_store_icon_has_no_alpha() -> None:
    contents = json.loads((ICONSET / "Contents.json").read_text(encoding="utf-8"))
    by_look: dict[str, Path] = {}
    for image in contents["images"]:
        look = "any"
        for appearance in image.get("appearances", []):
            look = appearance["value"]
        assert image["size"] == "1024x1024" and image["idiom"] == "universal" and image["platform"] == "ios"
        by_look[look] = ICONSET / image["filename"]
    assert set(by_look) == {"any", "dark", "tinted"}
    for look, path in by_look.items():
        width, height, colour = _png_header(path)
        assert (width, height) == (1024, 1024), f"{path.name} is {width}x{height}"
        if look in ("any", "tinted"):
            # 2 is RGB; 6 (RGBA) or 4 (gray+alpha) is refused for the store icon.
            assert colour in (0, 2), f"{path.name} has an alpha channel (PNG colour type {colour})"


def test_project_uses_the_icon_and_bundles_the_manifest() -> None:
    spec = (IOS / "project.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s*ASSETCATALOG_COMPILER_APPICON_NAME:\s*AppIcon\s*$", spec, re.M)
    assert re.search(r"-\s*path:\s*Domovoi/PrivacyInfo\.xcprivacy\s*\n\s*buildPhase:\s*resources", spec)
    assert re.search(r'^\s*MARKETING_VERSION:\s*"\d+\.\d+\.\d+"\s*$', spec, re.M)


# ---- the privacy manifest -------------------------------------------------------

# Apple's required-reason API categories, the Swift spellings of the calls in
# each, and the reason codes Apple lists for each (App Privacy Configuration,
# NSPrivacyAccessedAPITypeReasons).
CATEGORIES: dict[str, tuple[re.Pattern[str], set[str]]] = {
    "NSPrivacyAccessedAPICategoryUserDefaults": (
        re.compile(r"\bUserDefaults\b|@AppStorage|NSUserDefaults"),
        {"CA92.1", "1C8F.1", "C56D.1", "AC6B.1"},
    ),
    "NSPrivacyAccessedAPICategoryFileTimestamp": (
        re.compile(r"\b(creationDate|modificationDate|fileModificationDate|contentModificationDateKey|"
                   r"creationDateKey|contentModificationDate)\b|\b(getattrlist|getattrlistbulk|fgetattrlist|"
                   r"getattrlistat|fstatat|fstat|lstat|stat)\s*\(|attributesOfItem"),
        {"DDA9.1", "C617.1", "3B52.1", "0A2A.1"},
    ),
    "NSPrivacyAccessedAPICategorySystemBootTime": (
        re.compile(r"\bsystemUptime\b|\bmach_absolute_time\b"),
        {"35F9.1", "8FFB.1", "3D61.1"},
    ),
    "NSPrivacyAccessedAPICategoryDiskSpace": (
        re.compile(r"volumeAvailableCapacity|volumeTotalCapacity|systemFreeSize|systemSize\b|"
                   r"\b(statfs|statvfs|fstatfs|fstatvfs)\s*\("),
        {"85F4.1", "E174.1", "7D9E.1", "B728.1"},
    ),
    "NSPrivacyAccessedAPICategoryActiveKeyboards": (
        re.compile(r"activeInputModes"),
        {"3EC4.1", "54BD.1"},
    ),
}

# PHAsset.creationDate is Photos metadata, not a file-timestamp API.
NOT_A_FILE_TIMESTAMP = re.compile(r"asset\.creationDate|NSSortDescriptor\(key: \"creationDate\"")


def _shipping_swift() -> list[Path]:
    files = list((IOS / "Domovoi").rglob("*.swift"))
    for package in (IOS / "Packages").iterdir():
        sources = package / "Sources"
        if sources.is_dir():
            files += sources.rglob("*.swift")
    return files


def _code_lines(path: Path) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        out.append(line.split(" //", 1)[0])
    return out


def _used_categories() -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    for path in _shipping_swift():
        for number, line in enumerate(_code_lines(path), 1):
            if NOT_A_FILE_TIMESTAMP.search(line):
                continue
            for category, (pattern, _reasons) in CATEGORIES.items():
                if pattern.search(line):
                    used.setdefault(category, []).append(f"{path.relative_to(REPO).as_posix()}:{number}")
    return used


def _manifest() -> dict:
    return plistlib.loads(MANIFEST.read_bytes())


def test_manifest_says_no_tracking_and_no_collected_data() -> None:
    manifest = _manifest()
    assert manifest["NSPrivacyTracking"] is False
    assert manifest["NSPrivacyTrackingDomains"] == []
    assert manifest["NSPrivacyCollectedDataTypes"] == []


def test_manifest_declares_exactly_the_required_reason_apis_the_code_calls() -> None:
    declared = {entry["NSPrivacyAccessedAPIType"]: entry["NSPrivacyAccessedAPITypeReasons"]
                for entry in _manifest()["NSPrivacyAccessedAPITypes"]}
    used = _used_categories()
    missing = sorted(set(used) - set(declared))
    assert not missing, "used but not declared: " + "; ".join(f"{c} at {used[c][:3]}" for c in missing)
    unused = sorted(set(declared) - set(used))
    assert not unused, f"declared but no code calls them: {unused}"
    for category, reasons in declared.items():
        assert reasons, f"{category} has no reason"
        bad = set(reasons) - CATEGORIES[category][1]
        assert not bad, f"{category}: {sorted(bad)} is not one of Apple's codes for it"


def test_owner_only_release_files_are_ignored() -> None:
    """The team id file and the archives never get committed by accident."""
    for rel in ("ios/release.local", "ios/.release/26111401/x.xcarchive"):
        proc = subprocess.run(["git", "check-ignore", "-q", rel], cwd=REPO)
        assert proc.returncode == 0, f"{rel} is not in .gitignore"
