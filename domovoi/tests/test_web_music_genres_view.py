"""The Music page's Genres tab and genre drawer, rendered outside a browser
(domovoi/tests/jsx_render_harness.js: music.jsx compiled with the
dashboard's vendored Babel, a plain-object React).

Pins what the owner asked for on 2026-11-12: a Genres tab at #music/genres
listing the library's genres with track counts, loaded as it scrolls; each
opening its songs with play / shuffle / add to queue in a room and play /
shuffle / queue here (the Artists drawer's actions); and, while the genres
are still being read from the files, a line that says so with how far it
has got, rather than a partial list passed off as the whole. The counts,
search, sort and paging are the server's (test_library_genre_db.py).

No DB. Needs ``node`` and fails, not skips, without it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_render_harness.js")
MUSIC = "web/static/music.jsx"

SETUP = """
globalThis.CoverArt = function CoverArt(p) { return { type: 'CoverArt', props: p }; };
globalThis.libraryCoverUrl = (id) => `/api/music/library/${id}/cover`;
globalThis.URLSearchParams = class {
  constructor(init) { this.kv = Object.entries(init || {}); }
  set(k, v) { this.kv = this.kv.filter(([a]) => a !== k).concat([[k, String(v)]]); }
  toString() { return this.kv.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&'); }
};
"""

GENRES = [
    {"key": "hip hop", "name": "Hip Hop", "track_count": 12, "cover_track_id": 4},
    {"key": "soul", "name": "Soul", "track_count": 1, "cover_track_id": 9},
]
DONE = {"tags_read": 900, "tags_total": 900, "reading": False, "untagged_count": 3}
READING = {"tags_read": 1204, "tags_total": 6000, "reading": True, "untagged_count": 3}
SOUL = GENRES[1]
DETAIL = {"key": "soul", "name": "Soul", "track_count": 2, "track_ids": [9, 12],
          "track_ids_truncated": False}
SONGS = [
    {"id": 9, "title": "Respect", "artist": "Aretha Franklin", "album": "I Never Loved a Man"},
    {"id": 12, "title": "Try a Little Tenderness", "artist": "Otis Redding", "album": None},
]
DRAWER_FNS = ["onClose", "onPlayTracks", "onQueueTracksInRoom", "onBrowserPlayTracks",
              "onBrowserQueueTracks", "onSelectTrack", "onBrowserPlay", "onQueueTrack"]


def _tab(items, extra, **paged):
    return {"file": MUSIC, "component": "GenresTab", "fnProps": ["onOpen"],
            "apiList": {"items": items, "paged": {"extra": extra, **paged}}, "setup": SETUP}


SCENARIOS = {
    "tab": _tab(GENRES, DONE),
    "tab_reading": _tab(GENRES, READING),
    "tab_more_pages": _tab(GENRES, DONE, hasMore=True),
    "tab_empty": _tab([], {**DONE, "untagged_count": 0}),
    "tab_empty_reading": _tab([], {**READING, "untagged_count": 0}),
    "tab_only_untagged": _tab([], DONE),
    "drawer": {"file": MUSIC, "component": "GenreDrawer",
               "props": {"genre": SOUL, "rooms": ["kitchen", "den"]}, "fnProps": DRAWER_FNS,
               "apiObject": {"data": DETAIL, "loading": False},
               "apiList": {"items": SONGS}, "setup": SETUP},
    "drawer_no_genre": {"file": MUSIC, "component": "GenreDrawer",
                        "props": {"genre": {"key": "", "name": "no genre", "track_count": 3},
                                  "rooms": []}, "fnProps": DRAWER_FNS,
                        "apiObject": {"data": None, "loading": True},
                        "apiList": {"items": SONGS}, "setup": SETUP},
    "drawer_closed": {"file": MUSIC, "component": "GenreDrawer",
                      "props": {"genre": None, "rooms": ["kitchen"]}, "fnProps": DRAWER_FNS,
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


def _rows(nodes: list[dict]) -> list[dict]:
    return [n for n in nodes if n["type"] == "button" and "onClick" in n["on"]]


def test_the_tab_lists_genres_with_counts_and_a_search(rendered) -> None:
    nodes = rendered["tab"]
    texts = _texts(nodes)
    assert "Hip Hop" in texts and "Soul" in texts
    assert "12 tracks" in texts and "1 track" in texts
    assert "2 genres" in texts
    assert any(n["type"] == "input" and n["props"].get("aria-label") == "search genres" for n in nodes)
    assert [n["props"].get("value") for n in nodes if n["type"] == "option"] == ["name", "tracks"]
    assert any(n["type"] == "PagedListFooter" for n in nodes)
    # No progress line once every file has been read.
    assert not any("reading genres" in t for t in texts)


def test_tracks_with_no_genre_are_the_last_row(rendered) -> None:
    texts = _texts(rendered["tab"])
    assert texts.index("no genre") > texts.index("Soul")
    assert "3 tracks" in texts
    assert len(_rows(rendered["tab"])) == 3
    # Not until the last page has loaded (it would sit in the middle).
    assert "no genre" not in _texts(rendered["tab_more_pages"])


def test_while_reading_the_tab_says_so(rendered) -> None:
    nodes = rendered["tab_reading"]
    texts = _texts(nodes)
    assert "reading genres from your files… 1,204 of 6,000" in texts
    assert "· the list fills in as it goes" in texts
    assert any(n["type"] == "StatusDot" and n["props"].get("live") for n in nodes)
    assert any(n["props"].get("role") == "status" for n in nodes)
    # The genres found so far are still listed under it.
    assert "Hip Hop" in texts


def test_empty_states_are_honest(rendered) -> None:
    titles = lambda name: [n["props"].get("title") for n in rendered[name] if n["type"] == "Empty"]  # noqa: E731
    assert titles("tab_empty") == ["no genres yet"]
    assert titles("tab_empty_reading") == ["no genres found yet"]
    assert "reading genres from your files… 1,204 of 6,000" in _texts(rendered["tab_empty_reading"])
    # Only untagged tracks: their row, not "no genres yet".
    assert titles("tab_only_untagged") == []
    assert "no genre" in _texts(rendered["tab_only_untagged"])


def test_the_drawer_has_the_artists_drawer_actions(rendered) -> None:
    nodes = rendered["drawer"]
    texts = _texts(nodes)
    assert "Soul" in texts and "genre" in texts
    assert "2 tracks" in texts
    assert {"Respect", "Try a Little Tenderness"} <= set(texts)
    assert "Aretha Franklin · I Never Loved a Man" in texts and "Otis Redding" in texts
    buttons = _buttons(nodes)
    for label in ("play", "shuffle", "add to queue", "play here", "shuffle here", "queue here"):
        assert label in buttons, label
        assert not buttons[label]["props"].get("disabled"), label
    assert any(n["type"] == "RoomChips" or n["text"] == "kitchen" for n in nodes)


def test_the_no_genre_drawer(rendered) -> None:
    nodes = rendered["drawer_no_genre"]
    texts = _texts(nodes)
    assert "no genre" in texts
    assert "3 tracks · their tags name no genre" in texts
    buttons = _buttons(nodes)
    for label in ("play", "shuffle", "add to queue"):      # no room, no ids yet
        assert buttons[label]["props"].get("disabled") is True, label
    assert not buttons["play here"]["props"].get("disabled")


def test_a_closed_drawer_renders_nothing(rendered) -> None:
    assert rendered["drawer_closed"] == []


def test_the_tab_has_an_address_and_a_label() -> None:
    src = (REPO_ROOT / MUSIC).read_text(encoding="utf-8")
    tabs = re.search(r"const MUSIC_PAGE_TABS = \[(.*?)\];", src, re.S).group(1)
    ids = re.findall(r"id: '(\w+)'", tabs)
    assert ids.index("genres") == ids.index("artists") + 1         # #music/genres
    assert "{ id: 'genres',    label: 'Genres' }" in src
    assert "<GenresTab onOpen={setOpenGenre}/>" in src
    assert "<GenreDrawer genre={openGenre}" in src
