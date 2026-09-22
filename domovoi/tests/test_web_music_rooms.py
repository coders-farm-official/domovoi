"""F-020 — no phantom room, and a failed fetch is not an empty queue.

``useKnownRooms`` (web/static/music.jsx) fell back to ``['kitchen']``
whenever the now-playing list was empty, so on a box with no mpd_rooms
the Room queue tab offered a room that did not exist, fetched
``/api/music/queue/kitchen`` (a 502 with no core) and rendered the
failure as the success-shaped "queue is empty · say play something in
kitchen" (finding F-020, card MUS-01). The "no rooms provisioned yet"
branch was unreachable.

Now the room set is exactly what now-playing returned; every consumer
renders the empty set on its own (the drawers say so and disable play,
the Playlists tab toasts, the add-music bar attributes to ``web``), and
the Room queue tab tells an errored fetch apart from an empty queue —
for the queue itself and for the now-playing list it derives rooms from.

Driven with domovoi/tests/jsx_interact_harness.js (vendored Babel +
stateful mini-React in a Node vm; the API is a scripted table). No DB,
no ``requires_db`` — never skips; fails, not skips, without ``node``.
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

NP_502 = {"__error": {"status": 502, "message": '502 Bad Gateway: {"detail":"domovoi unreachable"}'}}
OFFICE_NP = [{"room_id": "office", "state": "stop", "song": None}]
TRACK = {"id": 6, "title": "Warm Stones", "artist": "Hearth Ensemble", "album": "By The Hearth",
         "duration_sec": 200, "file_path": "/music/hearth_06.mp3", "added_at": "2026-09-01T00:00:00Z",
         "added_via": "manual", "favorited": False}
PLAYLIST = {"id": 2, "name": "W1b Mix", "track_count": 1, "is_virtual": False,
            "description": "", "cover_color": "", "cover_emoji": "", "created_at": None}
PAGE_API = {"GET /api/playlists": [PLAYLIST], "GET /api/music/library/stats": None,
            "GET /api/acquisitions?limit=100": None}

MUSIC_PAGE_SCRIPT = r"""
  h.render();
  const header = h.text();
  await h.click({ type: 'button', text: 'Room queue' });
  const queueTab = h.text();
  await h.click({ type: 'button', text: 'Playlists' });
  await h.click({ type: 'button', title: 'play' });
  return { header, queueTab, afterPlay: h.text(), hookCalls: h.hookCalls,
           calls: h.calls.map((c) => `${c.method} ${c.path}`) };
"""

SCENARIOS = {
    "page_no_rooms": {
        "files": FILES, "component": "MusicPage",
        "api": {**PAGE_API, "GET /api/music/now-playing": []},
        "script": MUSIC_PAGE_SCRIPT,
    },
    "page_np_502": {
        "files": FILES, "component": "MusicPage",
        "api": {**PAGE_API, "GET /api/music/now-playing": NP_502},
        "script": MUSIC_PAGE_SCRIPT,
    },
    "queue_502": {
        "files": FILES, "component": "QueueTab",
        "props": {"rooms": ["office"], "nowPlaying": OFFICE_NP, "npError": None}, "fnProps": ["fire"],
        "api": {"GET /api/music/queue/office": {"__error": {"status": 502, "message": "502 Bad Gateway: domovoi unreachable"}}},
        "script": "h.render(); return { texts: h.text(), buttons: h.findAll({type:'button'}).map((b) => b.text) };",
    },
    "queue_empty": {
        "files": FILES, "component": "QueueTab",
        "props": {"rooms": ["office"], "nowPlaying": OFFICE_NP, "npError": None}, "fnProps": ["fire"],
        "api": {"GET /api/music/queue/office": {"items": [], "editable": True}},
        "script": "h.render(); return { texts: h.text() };",
    },
    "drawer_no_rooms": {
        "files": FILES, "component": "Drawer",
        "props": {"track": TRACK, "rooms": []},
        "fnProps": ["onClose", "onDelete", "onPlayInRoom", "onBrowserPlay", "onQueueTrack"],
        "script": ("h.render(); const play = h.find({ type: 'button', text: 'play in' });"
                   " return { texts: h.text(), play: h.plain(play) };"),
    },
    "drawer_one_room": {
        "files": FILES, "component": "Drawer",
        "props": {"track": TRACK, "rooms": ["office"]},
        "fnProps": ["onClose", "onDelete", "onPlayInRoom", "onBrowserPlay", "onQueueTrack"],
        "script": ("h.render(); const play = h.find({ type: 'button', text: 'play in' });"
                   " return { texts: h.text(), play: h.plain(play) };"),
    },
    "playlist_drawer_no_rooms": {
        "files": FILES, "component": "PlaylistDrawer",
        "props": {"playlist": PLAYLIST, "rooms": []},
        "fnProps": ["onClose", "onPlay", "onShuffle", "onRemoveTrack", "onDelete", "onEdit", "onReorder", "fire"],
        "api": {"GET /api/playlists/2/tracks": []},
        "script": ("h.render(); const btns = h.findAll({ type: 'button' }).map(h.plain);"
                   " return { texts: h.text(), btns };"),
    },
    "add_music_no_rooms": {
        "files": FILES, "component": "AddMusicBar",
        "props": {"rooms": [], "canFulfillQuery": True}, "fnProps": ["fire", "onQueued"],
        "api": {"POST /api/music/add-by-query": {"queued": True, "message": "queued"}},
        "script": ("h.render(); await h.type({ type: 'input' }, 'creep'); await h.click({ type: 'button', text: 'add music' });"
                   " return { calls: h.calls };"),
    },
}


@pytest.fixture(scope="module")
def driven() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


def _joined(texts: list[str]) -> str:
    return " | ".join(texts)


# ── no rooms: nothing invented ────────────────────────────────────────

def test_no_rooms_means_no_kitchen_anywhere(driven):
    r = driven["page_no_rooms"]
    assert "kitchen" not in _joined(r["header"] + r["queueTab"] + r["afterPlay"])
    assert not any(p.startswith("/api/music/queue/") for p in r["hookCalls"]), r["hookCalls"]


def test_room_queue_tab_reaches_its_no_rooms_branch(driven):
    r = driven["page_no_rooms"]
    assert "no rooms provisioned yet" in r["queueTab"]
    assert "queue is empty" not in _joined(r["queueTab"])


def test_playlist_play_without_rooms_toasts_instead_of_posting(driven):
    r = driven["page_no_rooms"]
    assert not any(c.startswith("POST /api/music/play-playlist") for c in r["calls"]), r["calls"]
    assert any("no rooms provisioned yet" in t for t in r["afterPlay"])


def test_drawers_say_no_rooms_and_disable_play(driven):
    d = driven["drawer_no_rooms"]
    assert any("no rooms provisioned yet" in t for t in d["texts"])
    assert d["play"]["props"].get("disabled") is True
    assert d["play"]["text"] == "play in a room"
    p = driven["playlist_drawer_no_rooms"]
    assert any("no rooms provisioned yet" in t for t in p["texts"])
    for label in ("play", "shuffle"):
        btn = next(b for b in p["btns"] if b["text"] == label)
        assert btn["props"].get("disabled") is True, label


def test_drawer_with_a_room_still_plays_there(driven):
    d = driven["drawer_one_room"]
    assert d["play"]["text"] == "play in office"
    assert not d["play"]["props"].get("disabled")


def test_add_music_attributes_to_web_not_a_made_up_room(driven):
    calls = driven["add_music_no_rooms"]["calls"]
    assert calls == [{"method": "POST", "path": "/api/music/add-by-query",
                      "body": {"room_id": "web", "query": "creep"}}]


# ── error is not empty ───────────────────────────────────────────────

def test_failed_queue_fetch_is_an_error_state_with_retry(driven):
    r = driven["queue_502"]
    joined = _joined(r["texts"])
    assert "couldn't load office's queue" in joined
    assert "domovoi unreachable" in joined
    assert "queue is empty" not in joined
    assert "retry" in r["buttons"]


def test_empty_queue_is_still_empty(driven):
    joined = _joined(driven["queue_empty"]["texts"])
    assert "queue is empty" in joined and "couldn't load" not in joined


def test_now_playing_failure_is_not_first_boot(driven):
    r = driven["page_np_502"]
    header = _joined(r["header"])
    assert "rooms unavailable" in header and "domovoi unreachable" in header
    assert "no rooms provisioned yet" not in header
    assert "retry" in r["header"]
    queue = _joined(r["queueTab"])
    assert "rooms unavailable" in queue and "no rooms provisioned yet" not in queue
    assert "kitchen" not in queue
