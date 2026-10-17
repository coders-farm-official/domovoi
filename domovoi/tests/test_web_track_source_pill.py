"""The dashboard's track drawer no longer reads "manual manual".

A track the library found on disk has ``added_via`` "manual" and no
``source``. The drawer's source pill used to fall back to "manual" too,
so the drawer showed two "manual" pills side by side. It now matches the
Android app (``trackSource``, MusicBits.kt): a source pill only for a
provider plugin's slug, and "—" when there is none. The library rows show
no source pill at all.

Driven with domovoi/tests/jsx_interact_harness.js (vendored Babel +
stateful mini-React in a Node vm). No DB, no ``requires_db`` — never
skips; fails, not skips, without ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/music.jsx"]

ON_DISK = {"id": 6, "title": "Warm Stones", "artist": "Hearth Ensemble", "album": "By The Hearth",
           "duration_sec": 200, "file_path": "/music/hearth_06.mp3", "added_at": "2026-09-01T00:00:00Z",
           "added_via": "manual", "favorited": False, "source": None, "source_id": None, "enriched_at": None}
FROM_PLUGIN = {**ON_DISK, "id": 7, "added_via": "voice", "source": "ytdlp", "source_id": "abc123"}
BLANK_SOURCE = {**ON_DISK, "id": 8, "source": "   "}

DRAWER_FNS = ["onClose", "onDelete", "onPlayInRoom", "onBrowserPlay", "onQueueTrack", "fire"]
# Each labelled cell of the drawer's detail grid, and every pill's text.
GRID = r"""
  h.render(); await h.settle(); h.rerender();
  const pills = h.findAll((el) => el.type === 'span' && el.props
      && typeof el.props.className === 'string' && el.props.className.indexOf('pill ') === 0)
    .map((el) => ({ text: el.text, cls: el.props.className }));
  return { texts: h.text(), pills };
"""


def _drawer(track: dict) -> dict:
    return {"files": FILES, "component": "Drawer", "props": {"track": track, "rooms": ["office"]},
            "fnProps": DRAWER_FNS, "script": GRID}


# LibraryTab takes the whole library hook (useLibrary) as `lib`; a small
# wrapper hands it one loaded page of rows, with recorder setters.
LIB_SETUP = r"""
  globalThis.LibraryRowsProbe = ({ rows }) => React.createElement(LibraryTab, {
    lib: { list: { items: rows, total: rows.length, hasMore: false, loading: false, error: null },
           items: rows, total: rows.length, loading: false,
           q: '', source: 'all', sort: 'added', favoritedOnly: false,
           setQ() {}, setSource() {}, setSort() {}, setFavoritedOnly() {} },
    libraryTotal: rows.length, sourceOptions: ['ytdlp'], playlists: [],
    onSelect() {}, onToggleFavorite() {}, onAddToPlaylist() {}, onBulkAddToPlaylist() {},
    onBrowserPlay() {}, onQueueTrack() {}, onPlayNextTrack() {}, fire() {},
  });
"""
PILLS = r"""
  const pills = () => h.findAll((el) => el.type === 'span' && el.props
      && typeof el.props.className === 'string' && el.props.className.indexOf('pill ') === 0)
    .map((el) => ({ text: el.text, cls: el.props.className }));
"""

SCENARIOS = {
    "drawer_on_disk": _drawer(ON_DISK),
    "drawer_from_plugin": _drawer(FROM_PLUGIN),
    "drawer_blank_source": _drawer(BLANK_SOURCE),
    "library_rows": {
        "files": FILES, "component": "LibraryRowsProbe", "setup": LIB_SETUP,
        "props": {"rows": [ON_DISK, FROM_PLUGIN]},
        "script": "h.render(); await h.settle(); h.rerender();" + PILLS
                  + "return { texts: h.text(), pills: pills() };",
    },
}


def _run(scenarios: dict) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(scenarios)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def driven() -> dict:
    return _run(SCENARIOS)


def _after(texts: list[str], label: str) -> str:
    """The text right after a grid label (its value)."""
    i = texts.index(label)
    return texts[i + 1]


def test_on_disk_track_reads_manual_once(driven):
    out = driven["drawer_on_disk"]
    assert [p["text"] for p in out["pills"]].count("manual") == 1
    assert _after(out["texts"], "added via") == "manual"


def test_on_disk_track_has_no_source_pill_but_a_dash(driven):
    out = driven["drawer_on_disk"]
    assert _after(out["texts"], "source") == "—"
    assert all(p["text"] != "manual" or p["cls"] == "pill idle" for p in out["pills"])


def test_blank_source_is_no_source(driven):
    out = driven["drawer_blank_source"]
    assert _after(out["texts"], "source") == "—"
    assert [p["text"] for p in out["pills"]].count("manual") == 1


def test_plugin_track_keeps_its_source_pill(driven):
    out = driven["drawer_from_plugin"]
    assert {"text": "ytdlp", "cls": "pill live"} in out["pills"]
    assert _after(out["texts"], "source") == "ytdlp"
    assert "manual" not in [p["text"] for p in out["pills"]]


def test_library_rows_show_no_source_pill(driven):
    out = driven["library_rows"]
    assert "Warm Stones" in out["texts"]
    assert out["pills"] == []
    assert "manual" not in out["texts"]
