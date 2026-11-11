"""The Music page's Artists tab and artist drawer, rendered outside a
browser (domovoi/tests/jsx_render_harness.js: music.jsx compiled with the
dashboard's vendored Babel, a plain-object React).

Pins what the owner asked for on 2026-11-11: an Artists list with counts,
searched and sorted by the server and loaded as it scrolls; an artist's
albums and songs, with play / shuffle / add-to-queue for a room and play /
queue for this browser — the playlist drawer's actions. The search, sort,
splitting and paging themselves are the server's (test_music_search.py).

No DB. Needs ``node`` and fails, not skips, without it.
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

# What the harness doesn't stub: components and helpers from other files.
SETUP = """
globalThis.CoverArt = function CoverArt(p) { return { type: 'CoverArt', props: p }; };
globalThis.libraryCoverUrl = (id) => `/api/music/library/${id}/cover`;
globalThis.URLSearchParams = class {
  constructor(init) { this.kv = Object.entries(init || {}); }
  set(k, v) { this.kv = this.kv.filter(([a]) => a !== k).concat([[k, String(v)]]); }
  toString() { return this.kv.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&'); }
};
"""

ARTISTS = [
    {"name": "T.I.", "track_count": 4, "album_count": 2, "cover_track_id": 3},
    {"name": "Bryson Tiller", "track_count": 1, "album_count": 0, "cover_track_id": None},
]
TI = ARTISTS[0]
DETAIL = {
    "name": "T.I.", "track_count": 4, "track_ids": [3, 4, 5, 10], "track_ids_truncated": False,
    "albums": [
        {"name": "Late Hours", "track_count": 1, "cover_track_id": 5},
        {"name": "Paper Route", "track_count": 2, "cover_track_id": 3},
        {"name": None, "track_count": 1, "cover_track_id": 10},
    ],
}
SONGS = [
    {"id": 5, "title": "Go Crazy", "artist": "Jeezy, T.I.", "album": "Late Hours"},
    {"id": 3, "title": "Whatever You Like", "artist": "T.I.", "album": "Paper Route"},
]
DRAWER_FNS = ["onClose", "onPlayTracks", "onQueueTracksInRoom", "onBrowserPlayTracks",
              "onBrowserQueueTracks", "onSelectTrack", "onBrowserPlay", "onQueueTrack"]

SCENARIOS = {
    "tab": {"file": MUSIC, "component": "ArtistsTab", "fnProps": ["onOpen"],
            "apiList": {"items": ARTISTS}, "setup": SETUP},
    "tab_empty": {"file": MUSIC, "component": "ArtistsTab", "fnProps": ["onOpen"],
                  "apiList": {"items": []}, "setup": SETUP},
    "drawer": {"file": MUSIC, "component": "ArtistDrawer",
               "props": {"artist": TI, "rooms": ["kitchen", "den"]}, "fnProps": DRAWER_FNS,
               "apiObject": {"data": DETAIL, "loading": False},
               "apiList": {"items": SONGS}, "setup": SETUP},
    "drawer_no_rooms": {"file": MUSIC, "component": "ArtistDrawer",
                        "props": {"artist": TI, "rooms": []}, "fnProps": DRAWER_FNS,
                        "apiObject": {"data": DETAIL, "loading": False},
                        "apiList": {"items": SONGS}, "setup": SETUP},
    "drawer_closed": {"file": MUSIC, "component": "ArtistDrawer",
                      "props": {"artist": None, "rooms": ["kitchen"]}, "fnProps": DRAWER_FNS,
                      "setup": SETUP},
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


def _buttons(nodes: list[dict]) -> dict[str, dict]:
    return {n["text"]: n for n in nodes if n["type"] == "Button"}


def test_the_tab_lists_artists_with_counts_and_a_search(rendered) -> None:
    nodes = rendered["tab"]
    texts = _texts(nodes)
    assert "T.I." in texts and "Bryson Tiller" in texts
    assert "4 tracks · 2 albums" in texts
    assert "1 track" in texts
    assert "2 artists" in texts
    assert any(n["type"] == "input" and n["props"].get("aria-label") == "search artists" for n in nodes)
    # Name and track-count sorts, both the server's.
    assert [n["props"].get("value") for n in nodes if n["type"] == "option"] == ["name", "tracks"]
    # Rows open the drawer; the list loads as it scrolls.
    assert sum(1 for n in nodes if n["type"] == "button" and "onClick" in n["on"]) == 2
    assert any(n["type"] == "PagedListFooter" for n in nodes)


def test_an_empty_list_says_so(rendered) -> None:
    assert "no artists yet" in [n["props"].get("title") for n in rendered["tab_empty"]]


def test_the_drawer_shows_albums_songs_and_the_playlist_actions(rendered) -> None:
    nodes = rendered["drawer"]
    texts = _texts(nodes)
    assert "T.I." in texts
    assert {"all songs", "Late Hours", "Paper Route", "no album"} <= set(texts)
    assert {"Go Crazy", "Whatever You Like"} <= set(texts)
    # Another credit on a song is shown beside its album.
    assert "Late Hours · Jeezy, T.I." in texts
    buttons = _buttons(nodes)
    for label in ("play", "shuffle", "add to queue", "play here", "shuffle here", "queue here"):
        assert label in buttons, label
        assert not buttons[label]["props"].get("disabled"), label
    assert any(n["type"] == "RoomChips" or n["text"] == "kitchen" for n in nodes)


def test_without_a_room_the_room_actions_are_off(rendered) -> None:
    buttons = _buttons(rendered["drawer_no_rooms"])
    for label in ("play", "shuffle", "add to queue"):
        assert buttons[label]["props"].get("disabled") is True, label
    assert not buttons["play here"]["props"].get("disabled")


def test_a_closed_drawer_renders_nothing(rendered) -> None:
    assert rendered["drawer_closed"] == []
