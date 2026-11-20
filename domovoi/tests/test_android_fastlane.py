"""The F-Droid listing (android/app/fastlane) matches the Play listing.

F-Droid reads the app's title, descriptions, images and per-release changelog
from fastlane/metadata/android/en-US in the build's subdir. It is generated
from android/play by android/play/fastlane_sync.py; these checks fail when a
listing change, a new screenshot or a version bump wasn't synced, and hold
the text to F-Droid's limits.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SYNC = REPO / "android" / "play" / "fastlane_sync.py"
OUT = REPO / "android" / "app" / "fastlane" / "metadata" / "android" / "en-US"


def _sync():
    spec = importlib.util.spec_from_file_location("fastlane_sync", SYNC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_fastlane_folder_is_in_step_with_the_play_listing():
    sync = _sync()
    stale = sync.stale(sync.wanted())
    assert not stale, "run python android/play/fastlane_sync.py; stale: " + ", ".join(
        str(p.relative_to(REPO)) for p in stale
    )


def test_this_versions_changelogs_exist_for_both_abis():
    sync = _sync()
    _, base = sync.app_version()
    for digit in (1, 2):
        assert (OUT / "changelogs" / f"{base * 10 + digit}.txt").is_file()


def test_the_text_fits_f_droids_limits():
    assert len((OUT / "title.txt").read_text(encoding="utf-8").strip()) <= 50
    assert len((OUT / "short_description.txt").read_text(encoding="utf-8").strip()) <= 80
    assert len((OUT / "full_description.txt").read_text(encoding="utf-8")) <= 4000
    for log in (OUT / "changelogs").glob("*.txt"):
        assert len(log.read_text(encoding="utf-8")) <= 500, log.name
        assert re.fullmatch(r"\d{9}", log.stem), log.name


def test_the_description_discloses_brave_as_non_free():
    text = (OUT / "full_description.txt").read_text(encoding="utf-8")
    assert "Brave Search, a non-free service" in text
