"""The Room queue tab on a phone, and for a guest (2026-10-10).

A friend's phone on the Wi-Fi could not find a room's queue, opening it
popped a pairing prompt, and its only way to reorder was HTML5 drag, which
a finger can't do. Pinned here, against the real ``web/static/music.jsx``
in ``jsx_interact_harness.js`` (vendored Babel + mini-React in a Node vm,
a scripted API table — no DB, never skips, fails without ``node``):

* **tap controls** on every row — move up, move down, play next — post
  the same move a drag does, to the place the entry lands once taken out;
* **a guest** (the read says ``guest: true``) never sees Clear, nor a
  remove button on the song playing now; on a room that isn't playing it
  sees "isn't playing" and a pair button — no prompt until it presses it;
* **an admin** gets "ban the guest that added this" on a guest's entry;
* **the queue button** on a room's now-playing card opens the tab on that
  room; a guest's card has no stop and no favorite;
* the track drawer adds to a room's queue without replacing it.
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
MUSIC = REPO_ROOT / "web" / "static" / "music.jsx"


def item(song_id: int, title: str, *, playing: bool = False, **extra) -> dict:
    return {"song_id": song_id, "pos": 0, "file": f"{title}.mp3", "title": title,
            "artist": "Hearth Ensemble", "album": None, "duration_sec": 180,
            "added_by": None, "added_by_device_id": None, "added_at": None,
            "guest_device_id": None, "guest_source": None, "playing": playing, **extra}


# Four entries, the second playing.
ITEMS = [item(11, "Alpha"), item(12, "Bravo", playing=True), item(13, "Charlie"),
         item(14, "Delta", added_by="a guest", guest_device_id="browser-party",
              guest_source="192.168.1.77")]
OFFICE_NP = [{"room_id": "office", "state": "play", "elapsed_sec": 10,
              "song": {"title": "Bravo", "artist": "Hearth Ensemble", "duration_sec": 180}},
             {"room_id": "den", "state": "stop", "song": None}]
QUEUE_PROPS = {"rooms": ["office", "den"], "nowPlaying": OFFICE_NP, "npError": None}
Q = "GET /api/music/queue/office"


def queue(**over) -> dict:
    return {"room_id": "office", "items": ITEMS, "current_song_id": 12,
            "editable": True, "blocked_reason": None, "guest": False, "admin": False, **over}


SNAP = (
    "const snap = () => ({ text: h.text().join(' | '),"
    " titles: h.findAll({ type: 'button' }).map((b) => b.props.title || null),"
    " buttons: h.findAll({ type: 'button' }).map((b) => b.text) });"
)

SCENARIOS = {
    "guest_playing": {
        "files": FILES, "component": "QueueTab", "props": QUEUE_PROPS, "fnProps": ["fire"],
        "api": {Q: queue(guest=True), "POST /api/music/queue/office/move": {"moved": True}},
        "script": SNAP + (
            "h.render(); const before = snap();"
            # play next on Delta (idx 3, below the playing Bravo at idx 1) -> 2
            " await h.click({ type: 'button', title: 'play next', nth: 2 });"
            # move up on the THIRD row, which the optimistic order has
            # made Delta (Alpha, Bravo, Delta, Charlie) -> 1
            " await h.click({ type: 'button', title: 'move up', nth: 2 });"
            " return { before, calls: h.calls.filter((c) => c.method === 'POST') };"
        ),
    },
    "guest_play_next_from_above": {
        "files": FILES, "component": "QueueTab", "props": QUEUE_PROPS, "fnProps": ["fire"],
        "api": {Q: queue(guest=True), "POST /api/music/queue/office/move": {"moved": True}},
        "script": (
            "h.render(); await h.click({ type: 'button', title: 'play next' });"
            " return { calls: h.calls.filter((c) => c.method === 'POST') };"
        ),
    },
    "guest_idle": {
        "files": FILES, "component": "QueueTab", "props": QUEUE_PROPS, "fnProps": ["fire"],
        "setup": ("globalThis.__pair = 0;"
                  " globalThis.Auth = { requestPairing() { globalThis.__pair += 1; } };"),
        "api": {Q: queue(items=[], current_song_id=None, editable=False, guest=True,
                         blocked_reason="Pair this phone to change a room that isn't playing")},
        "script": SNAP + (
            "h.render(); const before = snap(); const pairBefore = h.global('__pair');"
            " await h.click({ type: 'button', text: 'pair this phone' });"
            " return { before, pairBefore, pairAfter: h.global('__pair') };"
        ),
    },
    "household": {
        "files": FILES, "component": "QueueTab", "props": QUEUE_PROPS, "fnProps": ["fire"],
        "api": {Q: queue()},
        "script": SNAP + "h.render(); return snap();",
    },
    "admin": {
        "files": FILES, "component": "QueueTab", "props": QUEUE_PROPS, "fnProps": ["fire"],
        "api": {Q: queue(admin=True), "POST /api/music/guest-bans": {"id": 1}},
        "script": SNAP + (
            "h.render(); const before = snap();"
            " await h.click({ type: 'button', title: 'ban the guest' });"
            " return { before, calls: h.calls.filter((c) => c.method === 'POST') };"
        ),
    },
    "tap_targets": {
        # A top-level const is no sandbox property: hand it out with the component.
        "files": FILES, "component": "(window.__qtt = queueTapTarget, QueueTab)",
        "props": QUEUE_PROPS, "fnProps": ["fire"],
        "api": {Q: queue()},
        "script": (
            "const f = h.global('window').__qtt;"
            " return { nextBelow: f('next', 3, 1, 4), nextAbove: f('next', 0, 2, 4),"
            " nextAlready: f('next', 2, 1, 4), nextSelf: f('next', 1, 1, 4),"
            " nextNoCurrent: f('next', 2, -1, 4), upTop: f('up', 0, 1, 4), up: f('up', 2, 1, 4),"
            " downBottom: f('down', 3, 1, 4), down: f('down', 0, 1, 4) };"
        ),
    },
    "page_queue_button": {
        "files": FILES, "component": "MusicPage",
        "setup": "globalThis.useGuestViewer = () => true;",
        "api": {"GET /api/music/now-playing": OFFICE_NP, "GET /api/playlists": [],
                "GET /api/music/library/stats": None, "GET /api/acquisitions?limit=100": None,
                Q: queue(guest=True), "GET /api/music/queue/den": queue(items=[], guest=True)},
        "script": SNAP + (
            "h.render(); const before = snap();"
            " await h.click({ type: 'button', title: \"office's queue\" });"
            " return { before, after: snap(), hookCalls: h.hookCalls };"
        ),
    },
    "drawer_add_to_room_queue": {
        "files": FILES, "component": "Drawer",
        "props": {"track": {"id": 6, "title": "Warm Stones", "artist": "Hearth Ensemble",
                            "album": None, "duration_sec": 200, "file_path": "/music/a.mp3",
                            "added_at": None, "added_via": "manual", "favorited": False},
                  "rooms": ["office"]},
        "fnProps": ["onClose", "onDelete", "onPlayInRoom", "onQueueInRoom", "onBrowserPlay",
                    "onQueueTrack"],
        "script": ("h.render(); await h.click({ type: 'button', text: \"add to office's queue\" });"
                   " return { fnCalls: h.fnCalls };"),
    },
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    scenarios = tmp_path_factory.mktemp("room_queue") / "scenarios.json"
    scenarios.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), f"@{scenarios}"],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


def _count(titles: list, needle: str) -> int:
    return sum(1 for t in titles if t and needle in t)


def test_tap_controls_post_the_same_move_a_drag_does(driven) -> None:
    out = driven["guest_playing"]
    titles = out["before"]["titles"]
    # Up and down on every row; play next on every row but the playing one.
    assert _count(titles, "move up") == 4 and _count(titles, "move down") == 4
    assert _count(titles, "play next") == 3
    moves = [c["body"] for c in out["calls"]]
    assert moves == [
        {"song_id": 14, "to_position": 2, "device_id": "dev-1"},   # Delta, play next
        {"song_id": 14, "to_position": 1, "device_id": "dev-1"},   # Delta again, move up
    ], moves


def test_play_next_from_above_the_playing_entry_lands_right_after_it(driven) -> None:
    # Alpha (idx 0) above the playing Bravo (idx 1): taken out, Bravo is at
    # 0, so "right after it" is position 1 — the playing entry's own index.
    assert [c["body"]["to_position"] for c in driven["guest_play_next_from_above"]["calls"]] == [1]


def test_the_tap_target_rule(driven) -> None:
    t = driven["tap_targets"]
    assert t == {"nextBelow": 2, "nextAbove": 2, "nextAlready": None, "nextSelf": None,
                 "nextNoCurrent": None, "upTop": None, "up": 1, "downBottom": None, "down": 1}


def test_a_guest_never_sees_clear_or_a_remove_on_the_playing_song(driven) -> None:
    out = driven["guest_playing"]["before"]
    assert not any(b == "clear" for b in out["buttons"])
    assert _count(out["titles"], "remove from queue") == 3   # four entries, one playing
    assert "You're a guest" in out["text"]
    assert _count(out["titles"], "ban the guest") == 0       # not an admin


def test_a_guest_on_a_room_that_isnt_playing_is_told_and_offered_pairing(driven) -> None:
    out = driven["guest_idle"]
    text = out["before"]["text"]
    assert "office isn't playing" in text
    assert "Pair this phone to change a room that isn't playing" in text
    assert "pair this phone" in out["before"]["buttons"]
    # Nothing prompted until the person pressed it.
    assert out["pairBefore"] == 0 and out["pairAfter"] == 1
    assert _count(out["before"]["titles"], "move up") == 0


def test_a_household_reader_keeps_clear_and_every_remove(driven) -> None:
    out = driven["household"]
    assert "clear" in out["buttons"]
    assert _count(out["titles"], "remove from queue") == 4
    assert "You're a guest" not in out["text"]


def test_an_admin_bans_the_guest_behind_an_entry(driven) -> None:
    out = driven["admin"]
    assert _count(out["before"]["titles"], "ban the guest") == 1   # only Delta was a guest's
    assert out["calls"] == [{"method": "POST", "path": "/api/music/guest-bans",
                             "body": {"device_id": "browser-party", "source": "192.168.1.77"}}]


def test_a_rooms_queue_button_opens_the_tab_on_that_room(driven) -> None:
    out = driven["page_queue_button"]
    before = out["before"]
    # Only the card with something on it has the button.
    assert _count(before["titles"], "'s queue") == 1
    assert "Room queue" not in before["text"] or "queue is empty" not in before["text"]
    assert "/api/music/queue/office?device_id=dev-1" in out["hookCalls"]
    assert "Bravo" in out["after"]["text"] and "You're a guest" in out["after"]["text"]


def test_a_guests_room_card_has_no_stop_and_no_favorite(driven) -> None:
    titles = driven["page_queue_button"]["before"]["titles"]
    buttons = driven["page_queue_button"]["before"]["buttons"]
    assert not any(t and t.startswith("favorite") for t in titles)
    assert "queue" in buttons


def test_the_drawer_adds_to_a_rooms_queue(driven) -> None:
    calls = driven["drawer_add_to_room_queue"]["fnCalls"]
    assert [c["name"] for c in calls] == ["onQueueInRoom"]
    assert calls[0]["args"][1] == "office"


def test_the_queue_read_is_quiet() -> None:
    """Opening the tab or switching rooms is nobody pressing anything: the
    read must never open the pair prompt (data.js `quiet`). The harness's
    scripted hook ignores options, so this one is read off the source."""
    src = MUSIC.read_text(encoding="utf-8")
    start = src.index("const QueueTab = ")
    body = src[start:src.index("/* ---- Playlists tab")]
    assert "{ eventTypes: ['music.now_playing.changed'], quiet: true }" in body
