"""Copy the Play listing into the fastlane folder F-Droid reads.

F-Droid shows an app's title, descriptions, icon, screenshots and per-release
changelog from ``fastlane/metadata/android/<locale>/`` in the build's subdir
(``android/app``). The Play listing in ``android/play/`` stays the one source:
this script copies it there, adds F-Droid's own notes to the full description,
and writes the changelog for the current app version (one file per per-ABI
versionCode, from ``WHATS-NEW-v<versionName>.txt``). Older changelogs stay.

    python android/play/fastlane_sync.py          # write
    python android/play/fastlane_sync.py --check  # exit 1 if anything is stale

``domovoi/tests/test_android_fastlane.py`` runs the check, so a listing or
version change that forgets this fails CI.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PLAY = Path(__file__).resolve().parent
ANDROID = PLAY.parent
GRADLE = ANDROID / "app" / "build.gradle.kts"
OUT = ANDROID / "app" / "fastlane" / "metadata" / "android" / "en-US"

# The per-ABI APKs F-Droid builds: versionCode = base * 10 + digit
# (android/app/build.gradle.kts, domovoi.apkPerAbi). Play's bundle is
# base * 10 and isn't built by F-Droid.
ABI_DIGITS = {"armeabi-v7a": 1, "arm64-v8a": 2}

# F-Droid's limits: title 50, short description 80, changelog 500.
TITLE = "Domovoi"

# Added to the Play description for F-Droid: the NonFreeNet anti-feature in
# plain words, and where updates come from.
FDROID_NOTES = """

<b>ON F-DROID</b>
• Optional web lookups can go through Brave Search, a non-free service, only if you add your own Brave API key. Wikipedia or your own SearXNG work without it.
• F-Droid keeps this app up to date. It is the same APK, signed with the same key, as the one on GitHub and on your Domovoi server, so a phone can move between them with an ordinary update.
"""

IMAGES = {
    "icon.png": PLAY / "graphics" / "icon-512.png",
    "featureGraphic.png": PLAY / "graphics" / "feature-graphic-1024x500.png",
}
SCREENSHOT_DIRS = {
    "phoneScreenshots": PLAY / "screenshots" / "phone",
    "sevenInchScreenshots": PLAY / "screenshots" / "tablet-7",
    "tenInchScreenshots": PLAY / "screenshots" / "tablet-10",
}


def app_version() -> tuple[str, int]:
    """The app's (versionName, base versionCode) from build.gradle.kts."""
    text = GRADLE.read_text(encoding="utf-8")
    name = re.search(r'versionName\s*=\s*"([^"]+)"', text)
    code = re.search(r"versionCode\s*=\s*(\d+)", text)
    if not name or not code:
        raise SystemExit(f"no versionName/versionCode in {GRADLE}")
    return name.group(1), int(code.group(1))


def wanted() -> dict[Path, bytes]:
    """Every file the fastlane folder should hold, with its bytes."""
    listing = PLAY / "listing" / "en-US"
    files: dict[Path, bytes] = {
        OUT / "title.txt": (TITLE + "\n").encode(),
        OUT / "short_description.txt": (listing / "short_description.txt").read_bytes(),
        OUT / "full_description.txt": (
            (listing / "full_description.txt").read_text(encoding="utf-8").rstrip("\n")
            + FDROID_NOTES
        ).encode("utf-8"),
    }
    for name, src in IMAGES.items():
        files[OUT / "images" / name] = src.read_bytes()
    for folder, src_dir in SCREENSHOT_DIRS.items():
        for i, shot in enumerate(sorted(src_dir.glob("*.png")), start=1):
            files[OUT / "images" / folder / f"{i}.png"] = shot.read_bytes()
    version, base = app_version()
    notes = PLAY / f"WHATS-NEW-v{version}.txt"
    if not notes.is_file():
        raise SystemExit(f"missing {notes.name}: write the release notes first")
    for digit in ABI_DIGITS.values():
        files[OUT / "changelogs" / f"{base * 10 + digit}.txt"] = notes.read_bytes()
    return files


def stale(files: dict[Path, bytes]) -> list[Path]:
    """Files that are missing or differ, plus generated images and
    screenshots that the listing no longer has."""
    out = [p for p, data in files.items() if not p.is_file() or p.read_bytes() != data]
    images = OUT / "images"
    if images.is_dir():
        out += [p for p in images.rglob("*") if p.is_file() and p not in files]
    return out


def main(argv: list[str]) -> int:
    files = wanted()
    bad = stale(files)
    if "--check" in argv:
        for p in bad:
            print(f"stale: {p.relative_to(ANDROID)}")
        if bad:
            print("run: python android/play/fastlane_sync.py")
        return 1 if bad else 0
    for p in bad:
        if p in files:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(files[p])
        else:
            p.unlink()
        print(
            f"wrote: {p.relative_to(ANDROID)}"
            if p in files
            else f"removed: {p.relative_to(ANDROID)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
