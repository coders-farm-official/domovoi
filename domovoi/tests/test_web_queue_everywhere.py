"""Up next P5 (Feature B): the queue easier to find and to use, on the
screens that already exist (owner, 2026-11-12: no new queue screens).

* One "play next / add to queue" menu on every song row
  (components.jsx SongRowMenu), whose words follow where the music plays:
  "play next here", "play next in kitchen".
* The player's queue rows and a room's queue rows share their parts
  (QueueGrip: drag, or the arrow keys; QueueSectionHead: "now playing",
  "next"); "play next" on a queued song.
* The queue panel picks whose queue it shows: this browser or a room
  (player.jsx PlayerQueueView, which draws the Music page's Room queue
  `embedded` for a room).
* Home's room cards and a room's drawer open that room's queue
  (openQueueFor); a room's Recently played offers the menu on library
  songs (``track_id``).

Driven through domovoi/tests/jsx_interact_harness.js; DB-free.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")

PRELUDE = r"""
API_BASE = "";
window.__acts = [];
window.__fired = [];
window.__fire = (m) => { window.__fired.push(String(m)); };
"""

# A stand-in player for SongRowMenu (player.jsx isn't loaded in these).
PLAYER_STUB = r"""
usePlayback = () => window.__P;
playerAddTracks = async (p, list, next, name) => {
  window.__acts.push(['add', list.map((t) => t.id), next, name]);
  return (next ? 'Playing next: ' : 'Added: ') + name;
};
"""

HELPERS = r"""
const cls = (c) => (e) => String((e.props && e.props.className) || '').split(' ').includes(c);
const W = () => h.global('window');
const items = () => h.findAll((e) => e.props && e.props.role === 'menuitem').map((e) => e.text.trim());
const menuBtn = (e) => e.type === 'button' && e.props && e.props['aria-haspopup'] === 'menu';
"""


def _menu(script: str, *, p, props: dict | None = None, api: dict | None = None) -> dict:
    return {
        "files": ["web/static/components.jsx"],
        "component": "(() => SongRowMenu(Object.assign({ track: { id: 5, title: 'Blue' }, fire: window.__fire }, window.__props)))",
        "api": api or {},
        "setup": PRELUDE + PLAYER_STUB
        + f" window.__P = {json.dumps(p)}; window.__props = {json.dumps(props or {})};",
        "script": HELPERS + script,
    }


ROWS_SETUP = r"""
const __it = (n, extra) => Object.assign({ uid: 'u' + n, kind: 'library', trackId: n, title: 'Track ' + n, artist: 'A' + n }, extra || {});
window.__Q = [__it(1), __it(2), __it(3), __it(4, { origin: 'auto' })];
window.__rec = (name) => (...a) => { window.__acts.push([name].concat(a)); };
"""

ROOM_QUEUE = {
    "room_id": "kitchen", "editable": True, "guest": False, "session_id": None,
    "items": [
        {"song_id": 41, "title": "One", "artist": "A", "playing": True, "origin": "explicit"},
        {"song_id": 42, "title": "Two", "artist": "B", "origin": "explicit"},
        {"song_id": 43, "title": "Three", "artist": "C", "origin": "explicit"},
    ],
}

SCENARIOS = {
    # ── the song-row menu ───────────────────────────────────────────────
    "menu_here": _menu(
        "h.render(); await h.click(menuBtn); const words = items();"
        "await h.click((e) => e.props && e.props.role === 'menuitem' && /play next/.test(e.text));"
        "return { words, acts: W().__acts, fired: W().__fired, open: items().length };",
        p={"available": True, "target": {"kind": "browser"}}),
    "menu_casting": _menu(
        "h.render(); await h.click(menuBtn); const words = items();"
        "await h.click((e) => e.props && e.props.role === 'menuitem' && /add to/.test(e.text));"
        "return { words, acts: W().__acts, posts: h.calls.filter((c) => c.method === 'POST').length };",
        p={"available": True, "target": {"kind": "room", "roomId": "kitchen"}}),
    "menu_room_drawer": _menu(
        "h.render(); await h.click(menuBtn); const words = items();"
        "await h.click((e) => e.props && e.props.role === 'menuitem' && /add to/.test(e.text));"
        "return { words, acts: W().__acts, calls: h.calls.map((c) => [c.method, c.path, c.body]), fired: W().__fired };",
        p={"available": True, "target": {"kind": "browser"}}, props={"room": "office"}),
    "menu_nowhere": _menu(
        "h.render(); return { buttons: h.findAll((e) => e.type === 'button').length };",
        p={"available": False, "target": {"kind": "browser"}}),
    # ── the player's queue rows ─────────────────────────────────────────
    "player_rows": {
        "files": ["web/static/components.jsx", "web/static/player.jsx"],
        "component": "(() => PlayerQueueRows({ queue: window.__Q, index: 0, jumpTo: window.__rec('jump'),"
                     " removeAt: window.__rec('remove'), moveItem: window.__rec('move') }))",
        "api": {},
        "setup": PRELUDE + ROWS_SETUP,
        "script": HELPERS + (
            "h.render();"
            "const heads = h.findAll(cls('q-sec')).map((e) => e.text.trim());"
            "const grips = h.findAll(cls('q-grip')).length;"
            "await h.key((e) => cls('q-grip')(e) && /Track 2/.test(e.props['aria-label']), 'ArrowDown');"
            "await h.key((e) => cls('q-grip')(e) && /Track 1/.test(e.props['aria-label']), 'ArrowUp');"
            "await h.click((e) => e.type === 'button' && e.props['aria-label'] === 'play Track 3 next');"
            "const nextOnPlaying = !!h.find((e) => e.type === 'button' && e.props['aria-label'] === 'play Track 1 next');"
            "return { heads, grips, acts: W().__acts, nextOnPlaying };"),
    },
    "sheet_rows_no_grip": {
        "files": ["web/static/components.jsx", "web/static/player.jsx"],
        "component": "(() => PlayerQueueRows({ queue: window.__Q, index: 0, grip: false, jumpTo: () => {},"
                     " removeAt: () => {}, moveItem: () => {} }))",
        "api": {},
        "setup": PRELUDE + ROWS_SETUP,
        "script": HELPERS + "h.render(); return h.findAll(cls('q-grip')).length;",
    },
    # ── a room's queue, inside the player's queue panel ─────────────────
    "room_embedded": {
        "files": ["web/static/components.jsx", "web/static/music.jsx"],
        "component": "(() => QueueTab({ embedded: true, rooms: ['kitchen', 'office'], nowPlaying: [],"
                     " npLoading: false, fire: window.__fire, room: 'kitchen', onRoom: () => {} }))",
        "api": {"GET /api/music/queue/kitchen?device_id=": ROOM_QUEUE},
        "setup": PRELUDE + " DeviceIdentity = { id: () => '' };",
        "script": HELPERS + (
            "h.render(); await h.settle();"
            "const chips = h.findAll(cls('rq-room')).length;"
            "const heads = h.findAll(cls('q-sec')).map((e) => e.text.trim());"
            "await h.key((e) => cls('q-grip')(e) && /Three/.test(e.props['aria-label']), 'ArrowUp');"
            "return { chips, heads, calls: h.calls.filter((c) => c.method === 'POST').map((c) => [c.path, c.body]) };"),
    },
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("queue_everywhere") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), "@" + str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


# ── the song-row menu ─────────────────────────────────────────────────


def test_the_menu_says_here_and_adds_here(driven) -> None:
    r = driven["menu_here"]
    assert r["words"] == ["play next here", "add to queue here"]
    assert r["acts"] == [["add", [5], True, "Blue"]]
    assert r["fired"] == ["Playing next: Blue"]
    assert r["open"] == 0                       # the menu closes after a pick


def test_while_casting_the_menu_names_the_room_and_keeps_both_queues(driven) -> None:
    r = driven["menu_casting"]
    assert r["words"] == ["play next in kitchen", "add to kitchen's queue"]
    # Through the player (its copy + the room's queue), not a second POST here.
    assert r["acts"] == [["add", [5], False, "Blue"]]
    assert r["posts"] == 0


def test_a_rooms_drawer_aims_the_menu_at_that_room(driven) -> None:
    r = driven["menu_room_drawer"]
    assert r["words"] == ["play next in office", "add to office's queue"]
    assert r["acts"] == []
    assert r["calls"] == [["POST", "/api/music/queue/office/add",
                           {"track_ids": [5], "device_id": r["calls"][0][2]["device_id"], "where": "end"}]]
    assert r["fired"] == ["Added in office: Blue"]


def test_no_menu_where_nothing_can_take_the_song(driven) -> None:
    assert driven["menu_nowhere"]["buttons"] == 0


# ── the queue rows ────────────────────────────────────────────────────


def test_the_players_rows_have_heads_grips_and_play_next(driven) -> None:
    r = driven["player_rows"]
    # The waiting Up next pick (Track 4) is the "up next" section's, not a row here.
    assert r["heads"] == ["now playing", "next"]
    assert r["grips"] == 3
    assert r["acts"] == [["move", 1, 2], ["move", 2, 1]]   # the arrow keys, then "play next" on Track 3
    assert r["nextOnPlaying"] is False


def test_the_sheet_rows_have_no_grip(driven) -> None:
    # A finger can't drag; the sheet's rows move with "play next".
    assert driven["sheet_rows_no_grip"] == 0


def test_a_room_queue_in_the_panel_has_the_same_parts_and_no_room_chips(driven) -> None:
    r = driven["room_embedded"]
    assert r["chips"] == 0
    assert r["heads"] == ["now playing", "next"]
    assert r["calls"] == [["/api/music/queue/kitchen/move",
                           {"song_id": 43, "to_position": 1, "device_id": ""}]]


# ── the wiring (source) ───────────────────────────────────────────────


def _src(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_every_song_row_has_the_menu() -> None:
    music = _src("music.jsx")
    # Library rows, artist / album / genre songs, playlist rows.
    assert music.count("<MusicSongMenu track={t} fire={fire}/>") == 3
    assert 'title="add to browser queue"' not in music
    sats = _src("satellites.jsx")
    assert "<SongRowMenu track={{ id: r.track_id, title: r.title }} room={room} fire={fire}/>" in sats


def test_home_and_the_room_drawer_open_the_rooms_queue() -> None:
    home = _src("home.jsx")
    assert home.count("onClick={() => { HomeOpenQueue(s.room_id); }}") == 2     # playing and paused cards
    assert "if (typeof openQueueFor === 'function') openQueueFor(room);" in home
    sats = _src("satellites.jsx")
    assert "onClick={() => satOpenQueue(s.room_id)}" in sats
    assert "onOpenQueue={() => satOpenQueue(s.room_id)}" in sats


def test_the_panel_picks_whose_queue_it_shows() -> None:
    src = _src("player.jsx")
    assert 'aria-label="which queue to show"' in src
    assert "<QueueTab embedded rooms={rooms}" in src
    # Aimed from elsewhere: the floating panel on a desktop, the sheet's Queue tab on a phone.
    assert "_mpQueueHub.openers.add(fn);" in src
    assert "if (!opened && typeof openRoomQueue === 'function') openRoomQueue(room);" in src


def test_recently_played_carries_the_library_song() -> None:
    from web.backend.schemas import RecentlyPlayed

    assert "track_id" in RecentlyPlayed.model_fields
    sql = (REPO_ROOT / "web/backend/api/satellites.py").read_text(encoding="utf-8")
    assert "WHERE lt.id = mp.library_track_id) AS track_id" in sql
