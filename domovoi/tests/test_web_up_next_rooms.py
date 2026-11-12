"""A room's Up next in the dashboard (phase P4, 2026-11-12), driven.

In domovoi/tests/jsx_interact_harness.js (the dashboard's own Babel, a
small stateful React, a scripted API table; no DB, never skips, fails
without ``node``):

* the Music page's **Room queue** tab (``QueueTab``): the "up next" head
  before the room's first pick (what the mix is built from, the Autoplay
  switch, one muted line), each pick's reason, a tap on any song plays it
  now (``POST …/queue/{room}/play``, a real jump), "less like this" on a
  pick, nothing of it for a room without Up next, and a guest gets the
  jump but no switch, no "less like this" and no saving;
* ``RoomUpNext`` — the same section the player's queue shows while casting
  and a room's drawer shows (its first few, "open queue");
* **save as playlist** for a room's queue (``PlayerQueueSave`` with a
  room): the room's own queue is saved, and what was left out is said;
* the **player while casting**: a queue row jumps within the room's queue
  (found by its song), a row the room no longer holds is cast again; a
  cast carries this browser's Up next session to the room and a hand-back
  brings it home; "play next" is offered while casting.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.tests.test_web_player_cast import HELPERS as CAST_HELPERS
from domovoi.tests.test_web_player_cast import PRELUDE as CAST_PRELUDE
from domovoi.tests.test_web_up_next_player import UP_HELPERS, UP_PRELUDE

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
MUSIC_FILES = ["web/static/components.jsx", "web/static/music.jsx"]
PLAYER_FILES = ["web/static/components.jsx", "web/static/player.jsx"]


def item(song_id: int, title: str, *, playing: bool = False, origin: str = "explicit",
         why: str | None = None, seq: int | None = None, track_id: int | None = None) -> dict:
    return {"song_id": song_id, "pos": 0, "file": f"{title}.flac", "title": title,
            "artist": f"{title} Band", "album": None, "duration_sec": 180,
            "added_by": None, "added_by_device_id": None, "added_at": None,
            "guest_device_id": None, "guest_source": None, "playing": playing,
            "origin": origin, "reason_label": why, "up_next_seq": seq, "track_id": track_id}


# Alpha played, Bravo playing, Charlie asked for, Delta and Echo picked.
ITEMS = [item(11, "Alpha", track_id=1), item(12, "Bravo", playing=True, track_id=2),
         item(13, "Charlie", track_id=3),
         item(14, "Delta", origin="auto", why="same artist", seq=4, track_id=4),
         item(15, "Echo", origin="auto", why="90s", seq=5, track_id=5)]
UP = {"session_id": "S7", "mode": "library", "autoplay": True,
      "seed": {"track_id": 2, "title": "Bravo", "artist": "Bravo Band", "label": None, "list": None},
      "ended": False}
NP = [{"room_id": "office", "state": "play", "elapsed_sec": 10,
       "song": {"title": "Bravo", "artist": "Bravo Band", "duration_sec": 180}}]
Q = "GET /api/music/queue/office"


def queue(**over) -> dict:
    return {"room_id": "office", "items": ITEMS, "current_song_id": 12, "editable": True,
            "blocked_reason": None, "guest": False, "admin": False,
            "session_id": "S7", "autoplay": True, "up_next": UP, **over}


ACTS_API = {
    "POST /api/music/queue/office/play": {"played": True},
    "POST /api/music/queue/office/remove": {"removed": [15], "skipped": []},
    "POST /api/music/up-next/sessions/S7/feedback": {"removed": [4, 5]},
    "PATCH /api/music/up-next/sessions/S7": {"id": "S7", "autoplay": False},
    "POST /api/music/queue/office/save-playlist": {"playlist_id": 9, "name": "Friday", "saved": 3,
                                                   "skipped": 2, "duplicates": 1},
}

QT_PROPS = {"rooms": ["office"], "nowPlaying": NP, "npError": None}
SNAP = (
    "const snap = () => ({ text: h.text(),"
    " titles: h.findAll({ type: 'button' }).map((b) => b.props.title || null),"
    " sw: (h.find((e) => e.type === 'input' && e.props.role === 'switch') || { props: {} }).props['aria-label'] || null });"
)
POSTS = "const posts = () => h.calls.filter((c) => c.method !== 'GET').map((c) => [c.method, c.path, c.body]);"


def _qt(script: str, **over) -> dict:
    return {"files": MUSIC_FILES, "component": "QueueTab", "props": QT_PROPS, "fnProps": ["fire"],
            "api": {Q: queue(**over), **ACTS_API}, "script": SNAP + POSTS + script}


def _run(component: str, script: str, *, q: dict | None = None, setup: str = "") -> dict:
    return {"files": PLAYER_FILES, "component": component, "props": {}, "fnProps": [],
            "api": {Q: q if q is not None else queue(), **ACTS_API}, "setup": setup,
            "script": SNAP + POSTS + script}


MAIN = "(e) => e.props && e.props.role === 'button' && (e.props.title || '').startsWith('play now')"

SCENARIOS: dict = {
    # ── the Room queue tab ──
    "qt_shape": _qt("h.render(); return snap();"),
    "qt_jump": _qt(f"h.render(); await h.click({MAIN}); return {{ posts: posts(), fire: h.fnCalls }};"),
    "qt_less": _qt("h.render(); await h.click({ type: 'button', title: 'less like this' });"
                   " return { posts: posts(), fire: h.fnCalls };"),
    "qt_toggle": _qt("h.render(); await h.fire((e) => e.type === 'input' && e.props.role === 'switch',"
                     " 'onChange', { target: { checked: false } }); return { posts: posts() };"),
    "qt_off": _qt("h.render(); return snap();", autoplay=False,
                  up_next={**UP, "autoplay": False}),
    "qt_guest": _qt(f"h.render(); const s = snap(); await h.click({MAIN}); return {{ s, posts: posts() }};",
                    guest=True),
    "qt_none": _qt("h.render(); return snap();", session_id=None, autoplay=None, up_next=None,
                   items=[item(11, "Alpha"), item(12, "Bravo", playing=True)]),
    "qt_none_waiting": _qt("h.render(); return snap();",
                           items=[item(11, "Alpha", track_id=1), item(12, "Bravo", playing=True, track_id=2)]),
    # ── RoomUpNext (the player while casting, a room's drawer) ──
    "run_shape": _run("(() => RoomUpNext({ roomId: 'office' }))", "h.render(); return snap();"),
    "run_drawer": _run("(() => RoomUpNext({ roomId: 'office', limit: 1, onOpenQueue: () => { window.__opened = 1; } }))",
                       "h.render(); const s = snap();"
                       " await h.click((e) => e.type === 'a'); return { s, opened: h.global('window').__opened || 0 };"),
    "run_actions": _run("(() => RoomUpNext({ roomId: 'office' }))",
                        f"h.render(); await h.click({MAIN});"
                        " await h.click((e) => e.type === 'button' && e.props['aria-label'] === 'remove Delta');"
                        " await h.click((e) => e.type === 'button' && /^less like this: Echo/.test(e.props['aria-label'] || ''));"
                        " const after = snap();"
                        " await h.fire((e) => e.type === 'input' && e.props.role === 'switch', 'onChange', { target: { checked: false } });"
                        " return { posts: posts(), after };"),
    "run_guest": _run("(() => RoomUpNext({ roomId: 'office' }))", "h.render(); return snap();",
                      q=queue(guest=True)),
    "run_none": _run("(() => RoomUpNext({ roomId: 'office' }))", "h.render(); return snap();",
                     q=queue(session_id=None, up_next=None, autoplay=None)),
    # ── save a room's queue as a playlist ──
    "save_room": _run("(() => PlayerQueueSave({ queue: [], roomId: 'office' }))",
                      "h.render(); await h.click({ type: 'button', text: 'save as playlist' });"
                      " await h.fire((e) => e.type === 'input', 'onChange', { target: { value: 'Friday' } });"
                      " await h.click({ type: 'button', text: 'save' });"
                      " return { posts: posts(), text: h.text() };"),
}

# ── the player while casting ──
OFFICE_NP = {"GET /api/music/now-playing": [{"room_id": "office", "state": "play", "elapsed_sec": 9.0,
                                              "song": {"title": "Track 2", "artist": "A", "duration_sec": 200}}]}


def _cast(script: str, *, api: dict | None = None) -> dict:
    return {"files": PLAYER_FILES, "component": "PlaybackProvider", "api": api or {}, "props": {},
            "fnProps": [], "setup": CAST_PRELUDE, "script": CAST_HELPERS + script}


def _rq(*track_ids: int, playing: int = 2) -> dict:
    return {"items": [{"song_id": 20 + t, "track_id": t, "playing": t == playing} for t in track_ids]}


SCENARIOS.update({
    "cast_jump": _cast(
        "await castingToOffice(); P().jumpTo(3); await step(); return { acts: acts(), state: state() };",
        api={**OFFICE_NP, Q: _rq(1, 2, 3, 4)}),
    "cast_jump_gone": _cast(
        "await castingToOffice(); P().jumpTo(3); await step(); return { acts: acts(), state: state() };",
        api={**OFFICE_NP, Q: _rq(1, 2, 3)}),
    "cast_carries_session": {
        "files": PLAYER_FILES, "component": "PlaybackProvider", "api": OFFICE_NP, "props": {}, "fnProps": [],
        "setup": CAST_PRELUDE + UP_PRELUDE,
        "script": CAST_HELPERS + UP_HELPERS + (
            "ans('/api/music/up-next/sessions', W().__sess('library', [W().__pk(1, 12, 'same artist')]));"
            "h.render(); P().playFromList([10, 11].map((n) => W().__t(n)), 0, null); await step();"
            "await castTo({ kind: 'room', roomId: 'office' });"
            "const cast = bodies(/play-tracks/);"
            "await castTo({ kind: 'browser' });"
            "return { cast, moved: bodies(/\\/move$/) };"),
    },
})


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("up_next_rooms") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), "@" + str(spec)],
                          capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


# ─── the Room queue tab ───────────────────────────────────────────────────


def test_the_room_queue_marks_the_picks_under_an_up_next_head(driven) -> None:
    s = driven["qt_shape"]
    text = s["text"]
    head = text.index("up next")
    assert text.index("Charlie") < head < text.index("Delta")       # before the first pick
    assert "based on Bravo" in text
    assert any("same artist" in t for t in text) and any("90s" in t for t in text)
    assert s["sw"] == "autoplay in office: keep playing similar songs when the queue ends"
    less = [t for t in s["titles"] if t and t.startswith("less like this")]
    assert less == ["less like this: no more Delta Band in this mix", "less like this: no more Echo Band in this mix"]
    assert "save as playlist" not in " ".join(text)   # PlayerQueueSave is the player's; absent in this harness


def test_a_tap_plays_that_song_now(driven) -> None:
    j = driven["qt_jump"]
    assert j["posts"] == [["POST", "/api/music/queue/office/play", {"song_id": 11, "device_id": "dev-1"}]]
    assert j["fire"][-1]["args"] == ['playing "Alpha" in office']


def test_less_like_this_and_the_switch(driven) -> None:
    assert driven["qt_less"]["posts"] == [
        ["POST", "/api/music/up-next/sessions/S7/feedback", {"seq": 4, "kind": "less_like_this"}]]
    assert driven["qt_less"]["fire"][-1]["args"] == ["no more Delta Band in this mix (1 more taken out)"]
    assert driven["qt_toggle"]["posts"] == [["PATCH", "/api/music/up-next/sessions/S7", {"autoplay": False}]]


def test_the_lines_and_a_room_without_up_next(driven) -> None:
    assert "autoplay is off — the queue stops at the end" in driven["qt_off"]["text"]
    none = driven["qt_none"]
    assert "up next" not in none["text"] and none["sw"] is None
    waiting = driven["qt_none_waiting"]["text"]
    assert waiting.index("up next") > waiting.index("Bravo")       # after the list
    assert "similar songs join here when the queue is nearly over" in waiting


def test_a_guest_jumps_but_gets_no_switch_and_no_less_like_this(driven) -> None:
    g = driven["qt_guest"]
    assert g["s"]["sw"] is None
    assert not [t for t in g["s"]["titles"] if t and t.startswith("less like this")]
    assert g["posts"] == [["POST", "/api/music/queue/office/play", {"song_id": 11, "device_id": "dev-1"}]]


# ─── RoomUpNext ───────────────────────────────────────────────────────────


def test_the_rooms_section_shows_its_waiting_picks(driven) -> None:
    s = driven["run_shape"]
    assert "up next" in s["text"] and "based on Bravo" in s["text"]
    assert "Delta" in s["text"] and "Echo" in s["text"] and "Charlie" not in s["text"]
    assert "same artist" in s["text"] and "90s" in s["text"]
    assert s["sw"] == "autoplay in office: keep playing similar songs when the queue ends"
    d = driven["run_drawer"]
    assert "Delta" in d["s"]["text"] and "Echo" not in d["s"]["text"]
    assert "open office's queue · 1 more" in d["s"]["text"] and d["opened"] == 1


def test_the_rooms_section_actions(driven) -> None:
    a = driven["run_actions"]
    assert a["posts"] == [
        ["POST", "/api/music/queue/office/play", {"song_id": 14, "device_id": "dev-1"}],
        ["POST", "/api/music/queue/office/remove", {"song_ids": [14], "device_id": "dev-1"}],
        ["POST", "/api/music/up-next/sessions/S7/feedback", {"seq": 5, "kind": "less_like_this"}],
        ["PATCH", "/api/music/up-next/sessions/S7", {"autoplay": False}],
    ]
    # Less like this: the artist's picks leave the list at once.
    assert "Echo" not in a["after"]["text"] and "Delta" not in a["after"]["text"]
    assert any("No more Echo Band in this mix (1 more taken out)." in t for t in a["after"]["text"])


def test_the_rooms_section_for_a_guest_and_without_up_next(driven) -> None:
    g = driven["run_guest"]
    assert g["sw"] is None and not [t for t in g["titles"] if t and t.startswith("less like this")]
    assert "remove" in g["titles"]
    assert driven["run_none"]["text"] == []


def test_save_a_rooms_queue_as_a_playlist(driven) -> None:
    s = driven["save_room"]
    assert s["posts"] == [["POST", "/api/music/queue/office/save-playlist", {"name": "Friday", "device_id": "dev-1"}]]
    assert ('Saved as "Friday" (3 songs); 2 left out: not songs from the library, 1 repeat saved once.'
            in s["text"])


# ─── the player while casting ─────────────────────────────────────────────


def test_a_queue_row_jumps_within_the_rooms_queue(driven) -> None:
    j = driven["cast_jump"]
    assert 'POST /api/music/queue/office/play {"song_id":24,"device_id":"dev-1"}' in j["acts"]
    assert not any(a.startswith("POST /api/music/play-tracks") for a in j["acts"])
    assert j["state"]["index"] == 3
    gone = driven["cast_jump_gone"]
    assert any(a.startswith("POST /api/music/play-tracks") for a in gone["acts"])
    assert not any("/queue/office/play" in a for a in gone["acts"])


def test_a_cast_carries_the_session_and_the_hand_back_brings_it_home(driven) -> None:
    c = driven["cast_carries_session"]
    (_m, _p, body), = c["cast"]
    assert body["up_next_session_id"] == "S1" and body["room_id"] == "office"
    assert "autoplay" not in body                       # on: the core's default
    assert c["moved"] == [["POST", "/api/music/up-next/sessions/S1/move",
                           {"target": {"kind": "device", "device_id": "dev-1"}}]]


# ─── source checks ────────────────────────────────────────────────────────


def _src(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_play_next_is_offered_while_casting_and_goes_to_the_room() -> None:
    src = _src("player.jsx")
    assert "where: next ? 'next' : 'end'" in src
    # The search is shared (QueueSearchAdd); the player names the room it casts to.
    assert "title={where ? `play next in ${where}` : 'play next'}" in src
    assert "where={casting ? p.target.roomId : null}" in src
    assert "{!casting && (\n                <IconButton name=\"corner-down-right\"" not in src


def test_the_room_views_use_the_rooms_section() -> None:
    assert "<RoomUpNext roomId={s.room_id} limit={3}" in _src("satellites.jsx")
    assert "return <RoomUpNext roomId={p.target.roomId}/>" in _src("player.jsx")
    music = _src("music.jsx")
    assert "roomPlayEntry(room, item.song_id, send)" in music
    assert "<PlayerQueueSave queue={[]} roomId={room}/>" in music


def test_room_starts_send_their_list_and_listener() -> None:
    music = _src("music.jsx")
    assert "apiPost('/api/music/play-track', roomStartBody({ room_id, track_id: track.id }))" in music
    assert "onPlayTracks(roomIds(false), room, what, false, context)" in music
    assert "upNextListenerFields()" in music


def test_the_room_styles_use_tokens_and_44px_on_a_phone() -> None:
    import re

    css = _src("styles.css")
    block = css[css.index("/* The room's Up next (P4)"):css.index("@media (max-width: 760px) {\n  .rq-un-head")]
    assert ".rq-un-head" in block and ".rq-why" in block and ".sat-upnext" in block
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|(?<![-\w])(white|black)(?![-\w])", block)
    assert ".rq-un-head .mp-un-auto { min-height: 44px; }" in css
