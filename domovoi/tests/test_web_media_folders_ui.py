"""Settings → Household → Media folders (web/static/media_folders.jsx).

Rendered with domovoi/tests/jsx_render_harness.js (needs node) for the three
places it is read from: a browser on the Domovoi PC (Windows install, tray
running), another device's browser, and a Linux server; plus a Windows
install whose DomovoiHost predates the feature. The rule under test is the
owner's: a control that can't do what it says isn't shown.

No DB.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_render_harness.js")
SETUP = "useToast = () => [() => {}, null];"

ROOT = "C:\\Users\\Public\\Domovoi"


def _windows_rows():
    rows = []
    for kind, label in (("music", "Music"), ("videos", "Videos"), ("pictures", "Pictures"),
                        ("documents", "Documents"), ("audiobooks", "Audiobooks")):
        rows.append({"kind": kind, "label": label, "field": f"{kind}_dir", "linux_path": f"/srv/media/{kind}",
                     "location": f"{ROOT}\\{label}", "where": "host", "editable": False,
                     "exists": True, "readable": True, "connected": True})
    rows.append({"kind": "podcasts", "label": "Podcasts", "field": "podcasts_dir",
                 "linux_path": "/home/domovoi/.domovoi/podcasts", "location": None, "where": "internal",
                 "editable": False, "exists": False, "readable": False, "connected": False})
    return rows


WINDOWS = {
    "host_kind": "windows", "place": "the Domovoi PC", "root": ROOT, "source": "report",
    "kinds": _windows_rows(),
    "open": {"available": True, "why": None, "last": None},
    "change": {"available": True, "why": None, "state": "none", "problem": None, "requested_root": None,
               "previous_root": None, "waiting_for_host": False},
    "default_root": ROOT, "on_this_pc": True,
}
LINUX = {
    "host_kind": "linux", "place": "the Domovoi server", "root": None, "source": None,
    "kinds": [
        {"kind": "music", "label": "Music", "field": "music_dir", "linux_path": "/srv/music", "location": "/srv/music",
         "where": "server", "editable": True, "exists": True, "readable": True},
        {"kind": "pictures", "label": "Pictures", "field": "pictures_dir", "linux_path": "/home/alex/Pictures",
         "location": "/home/alex/Pictures", "where": "server", "editable": False, "exists": True, "readable": True},
    ],
    "open": {"available": False, "why": "a Linux server opens no windows; copy the path instead", "last": None},
    "change": {"available": False, "why": "on a Linux server, change each folder's path below", "state": "none"},
    "default_root": None, "on_this_pc": False,
}


def _render(scenarios: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is needed to render the dashboard's JSX")
    full = {name: {"file": "web/static/media_folders.jsx", "component": "MediaFoldersPanel",
                   "apiObject": {"data": data}, "setup": SETUP}
            for name, data in scenarios.items()}
    out = subprocess.run([node, str(HARNESS), str(REPO), json.dumps(full)],
                         capture_output=True, text=True, encoding="utf-8", timeout=120, check=True)
    return json.loads(out.stdout)


def _rows(els: list[dict]) -> dict[str, list[str]]:
    """Each media kind's button labels, by the row they are in."""
    rows: dict[str, list[str]] = {}
    current = None
    for e in els:
        kind = e["props"].get("data-media-kind")
        if kind:
            current = kind
            rows[kind] = []
            continue
        if e["props"].get("className") == "media-folders-move":
            current = None
        if current and e["type"] == "Button":
            rows[current].append(e["text"])
    return rows


def _all_text(els: list[dict]) -> str:
    return " ".join(e["text"] for e in els if e["text"]) + " " + " ".join(
        str(e["props"].get("sub", "")) for e in els)


def test_on_the_domovoi_pc_the_windows_folders_open_there() -> None:
    els = _render({"pc": WINDOWS})["pc"]
    rows = _rows(els)
    assert rows["music"] == ["open on the Domovoi PC", "copy path"]
    assert all(rows[k] == ["open on the Domovoi PC", "copy path"]
               for k in ("videos", "pictures", "documents", "audiobooks"))
    assert rows["podcasts"] == [], "a folder inside Domovoi has nothing to open or copy"
    text = _all_text(els)
    assert ROOT + "\\Music" in text and "/srv/media/music" not in text
    assert "kept inside Domovoi" in text
    assert any(e["type"] == "Button" and e["text"] == "move…" for e in els)
    assert not any(e["text"] == "change" for e in els), "no Linux path to edit on Windows"


def test_another_device_gets_the_path_to_copy_and_why() -> None:
    els = _render({"far": {**WINDOWS, "on_this_pc": False}})["far"]
    rows = _rows(els)
    assert rows["music"] == ["copy path"]
    text = _all_text(els)
    assert "on the Domovoi PC, not on this device" in text
    assert "open on the Domovoi PC" not in [e["text"] for e in els]


def test_no_tray_no_open_button_and_the_reason() -> None:
    data = {**WINDOWS, "open": {"available": False, "why": "Domovoi's icon isn't running on the Domovoi PC", "last": None}}
    els = _render({"notray": data})["notray"]
    assert _rows(els)["music"] == ["copy path"]
    assert "icon isn't running" in _all_text(els)


def test_an_older_windows_side_shows_folders_but_no_move() -> None:
    data = {**WINDOWS, "source": "fstab", "root": None,
            "open": {"available": False, "why": "the next update of its Windows side brings it", "last": None},
            "change": {"available": False, "why": "run Domovoi Setup again and choose the new folder there",
                       "state": "none"}}
    els = _render({"old": data})["old"]
    assert _rows(els)["music"] == ["copy path"]
    assert not any(e["type"] == "input" for e in els), "no box for a move that can't happen"
    assert "Setup again" in _all_text(els)


def test_a_move_under_way_shows_its_state_not_the_box() -> None:
    data = {**WINDOWS, "change": {**WINDOWS["change"], "state": "applying", "requested_root": "D:\\Media"}}
    els = _render({"moving": data})["moving"]
    assert not any(e["type"] == "input" for e in els)
    assert "Domovoi restarts" in _all_text(els)
    data = {**WINDOWS, "change": {**WINDOWS["change"], "state": "done", "requested_root": "D:\\Media",
                                  "previous_root": ROOT}}
    els = _render({"done": data})["done"]
    assert f"the files in {ROOT} are still there" in _all_text(els)


def test_a_linux_server_copies_and_changes_in_place() -> None:
    els = _render({"linux": LINUX})["linux"]
    rows = _rows(els)
    assert rows["music"] == ["copy path", "change"]
    assert rows["pictures"] == ["copy path"]
    text = _all_text(els)
    assert "/srv/music" in text and "open on the Domovoi PC" not in [e["text"] for e in els]
    assert not any(e["props"].get("className") == "media-folders-move" for e in els), "no move on Linux"
