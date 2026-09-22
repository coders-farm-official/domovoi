"""Music page renders, checked outside a browser (F-023, F-025).

web/static/music.jsx is compiled with the dashboard's own vendored Babel
and evaluated with a plain-object React (domovoi/tests/jsx_render_harness.js),
so a component can be called with props and every element it would hand
to React inspected. That is enough to pin two Stats/Playlists findings
that only showed up in a browser before:

* F-023 (MUS-07): the playlist drawer's header said "3 tracks" over a
  two-row list — it read the count from the playlist row captured when
  the drawer opened, while the list came from a live fetch.
* F-025 (MUS-11): the Stats tab picked `voice` and `manual` out of
  by_added_via by name and dropped every other bucket, so an 11-track
  library with added_via NULL rendered "added via voice 0 / 0 manual";
  by_source was never rendered at all; 81 s read "1 minutes".

No DB, no ``requires_db`` — never skips. Needs ``node`` (the runtime the
JSX compile check already relies on) and fails, not skips, without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_render_harness.js")
MUSIC = "web/static/music.jsx"

PLAYLIST = {"id": 2, "name": "W1b Mix", "track_count": 3, "is_virtual": False,
            "description": "", "cover_color": "", "cover_emoji": ""}
TWO_TRACKS = [{"id": 5, "title": "a", "artist": "x"}, {"id": 9, "title": "b", "artist": "y"}]
DRAWER_FNS = ["onClose", "onPlay", "onShuffle", "onRemoveTrack", "onDelete", "onEdit", "onReorder", "fire"]

# MUS-11's seeded library, verbatim from GET /api/music/library/stats.
SEEDED_STATS = {"total_tracks": 11, "total_duration_sec": 81,
                "by_added_via": {"unknown": 11}, "by_source": {"library": 11},
                "enriched_count": 0}
MIXED_STATS = {"total_tracks": 12, "total_duration_sec": 7200,
               "by_added_via": {"voice": 7, "manual": 2, "unknown": 3},
               "by_source": {"library": 9, "ytdlp": 3}, "enriched_count": 12}

SCENARIOS = {
    "drawer_after_remove": {
        "file": MUSIC, "component": "PlaylistDrawer",
        "props": {"playlist": PLAYLIST, "rooms": ["kitchen"]}, "fnProps": DRAWER_FNS,
        "apiObject": {"data": TWO_TRACKS, "loading": False},
    },
    "drawer_before_fetch": {
        "file": MUSIC, "component": "PlaylistDrawer",
        "props": {"playlist": PLAYLIST, "rooms": ["kitchen"]}, "fnProps": DRAWER_FNS,
        "apiObject": {"data": None, "loading": True},
    },
    "stats_seeded": {"file": MUSIC, "component": "StatsTab",
                     "props": {"stats": SEEDED_STATS, "loading": False}},
    "stats_mixed": {"file": MUSIC, "component": "StatsTab",
                    "props": {"stats": MIXED_STATS, "loading": False}},
    "stats_empty": {"file": MUSIC, "component": "StatsTab",
                    "props": {"stats": {"total_tracks": 0, "total_duration_sec": 0,
                                        "by_added_via": {}, "by_source": {}, "enriched_count": 0},
                              "loading": False}},
}


@pytest.fixture(scope="module")
def rendered() -> dict:
    node = shutil.which("node")
    assert node, "node is required to render web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _texts(nodes: list[dict]) -> list[str]:
    return [n["text"] for n in nodes if n["text"]]


def _stats(nodes: list[dict]) -> dict[str, tuple]:
    """{label: (value, sub)} for every Stat tile in render order."""
    return {n["props"]["label"]: (n["props"].get("value"), n["props"].get("sub"))
            for n in nodes if n["type"] == "Stat"}


# ── F-023 ─────────────────────────────────────────────────────────────

def test_drawer_header_count_follows_the_fetched_tracks(rendered):
    texts = _texts(rendered["drawer_after_remove"])
    assert "2 tracks" in texts
    assert "3 tracks" not in texts


def test_drawer_header_uses_the_row_count_until_the_fetch_lands(rendered):
    texts = _texts(rendered["drawer_before_fetch"])
    assert "3 tracks" in texts


# ── F-025 ─────────────────────────────────────────────────────────────

def test_stats_show_the_unknown_added_via_bucket(rendered):
    tiles = _stats(rendered["stats_seeded"])
    assert tiles["added via unknown"] == (11, "all tracks")
    assert not any(label.startswith("added via voice") for label in tiles)


def test_stats_have_a_by_source_tile(rendered):
    tiles = _stats(rendered["stats_seeded"])
    assert tiles["by source library"] == (11, "all tracks")


def test_stats_render_every_bucket_largest_first(rendered):
    tiles = _stats(rendered["stats_mixed"])
    assert tiles["added via voice"] == (7, "3 unknown · 2 manual")
    assert tiles["by source library"] == (9, "3 ytdlp")


def test_stats_pluralise_minutes(rendered):
    assert _stats(rendered["stats_seeded"])["total duration"] == ("1m", "1 minute")
    assert _stats(rendered["stats_mixed"])["total duration"] == ("2h 0m", "120 minutes")


def test_stats_empty_library_has_no_phantom_buckets(rendered):
    tiles = _stats(rendered["stats_empty"])
    assert tiles["added via"] == ("—", "no tracks")
    assert tiles["by source"] == ("—", "no tracks")
