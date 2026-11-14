"""The Room queue tab: add songs from the tab, and a tab that stays quick.

The owner (2026-11-12, a phone on the LAN): "when seeing the rooms queue,
how do I add songs to that queue? Even scrolling to the bottom I dont see a
way to add anything", and "it took a while to load and seems very laggy".
Pinned here, against the real ``web/static/music.jsx`` and the search the
player's queue already had (``QueueSearchAdd`` in ``web/static/player.jsx``),
in ``jsx_interact_harness.js`` (no DB, never skips, fails without ``node``):

* **the search** sits under the room chips, finds library songs, and each
  result plays next or adds to the end of THAT room's queue
  (``POST /api/music/queue/{room}/add``, ``where: next|end``);
* it is drawn only where adding works: the queue answered for this room
  and says the caller may change it — a household device, a guest on a
  playing room — and never for a guest on a quiet room or a blocked device
  (no misleading controls); the blocked words are said instead;
* **no re-read storm**: a ``music.now_playing.changed`` about another room,
  or one that changes nothing for this room, no longer re-reads the queue;
* **long queues** draw 100 rows at a time, with "show more";
* **no serial wait**: the room this browser looked at last is read at once,
  before now-playing answers; while it hasn't, the tab says it is loading
  rooms rather than "no rooms set up yet";
* **no page-wide clock**: the Music page no longer re-renders every second
  (the elapsed-time tick lives in each playing room's card).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/player.jsx", "web/static/music.jsx"]


def item(song_id: int, title: str, *, playing: bool = False) -> dict:
    return {"song_id": song_id, "pos": 0, "file": f"{title}.mp3", "title": title,
            "artist": "Hearth Ensemble", "album": None, "duration_sec": 180,
            "added_by": None, "playing": playing}


ITEMS = [item(11, "Alpha"), item(12, "Bravo", playing=True), item(13, "Charlie")]
NP = [{"room_id": "office", "state": "play", "elapsed_sec": 10,
       "song": {"file": "Bravo.mp3", "title": "Bravo", "artist": "Hearth Ensemble"}},
      {"room_id": "den", "state": "play", "elapsed_sec": 3,
       "song": {"file": "Echo.mp3", "title": "Echo", "artist": "Hearth Ensemble"}}]
PROPS = {"rooms": ["office", "den"], "nowPlaying": NP, "npError": None}
SONGS = {"items": [{"id": 41, "title": "Blue Monday", "artist": "Order", "album": "Power"},
                   {"id": 42, "title": "Blue Train", "artist": "Coltrane", "album": "Blue Train"}]}
SEARCH_API = {"GET /api/music/library?q=blue&limit=8": SONGS,
              "GET /api/music/genres?q=blue&limit=2": {"items": []}}


def queue(room: str = "office", **over) -> dict:
    return {"room_id": room, "items": ITEMS, "current_song_id": 12, "editable": True,
            "blocked_reason": None, "guest": False, "admin": False, **over}


def qpath(room: str) -> str:
    return f"GET /api/music/queue/{room}?device_id=dev-1"


PRELUDE = 'API_BASE = "";'
HELPERS = r"""
const cls = (c) => (e) => String((e.props && e.props.className) || '').split(' ').includes(c);
const box = () => h.find((e) => e.type === 'input' && e.props.role === 'combobox');
const byLabel = (re) => (e) => e.type === 'button' && re.test(e.props['aria-label'] || '');
const posts = () => h.calls.filter((c) => c.method === 'POST').map((c) => [c.path, c.body]);
const note = () => h.findAll(cls('mp-q-note')).map((e) => e.text).join('|');
const shown = () => {
  const b = box();
  if (!b) return null;
  return { label: b.props['aria-label'], underChips: h.tree().findIndex(cls('rq-rooms')) < h.tree().indexOf(b),
           aboveRows: h.tree().indexOf(b) < h.tree().findIndex(cls('rq-row')) };
};
"""


def _scenario(script: str, *, api: dict | None = None, props: dict | None = None,
              setup: str = "", component: str = "QueueTab") -> dict:
    return {"files": FILES, "component": component, "props": props if props is not None else PROPS,
            "fnProps": ["fire", "onRoom"] if component == "QueueTab" else [],
            "api": {**SEARCH_API, **(api or {})}, "setup": PRELUDE + setup,
            "script": HELPERS + script}


ADD_SCRIPT = (
    "h.render(); const before = shown();"
    " await h.type((e) => e.type === 'input' && e.props.role === 'combobox', 'blue');"
    " await h.click(byLabel(/^play Blue Monday next/));"
    " const afterNext = note();"
    " await h.click(byLabel(/^add Blue Train to the queue/));"
    " return { before, posts: posts(), afterNext, after: note(), fn: h.fnCalls };"
)

# The no-storm scenario: catch the tab's event handler and count its re-reads.
EVENTS_SETUP = r"""
globalThis.__handlers = [];
globalThis.useStateEvents = (types, cb) => { globalThis.__handlers.push({ types, cb }); };
globalThis.__rereads = 0;
const __hook = globalThis.useApiObject;
globalThis.useApiObject = (p, o) => {
  const r = __hook(p, o);
  globalThis.__opts = o || null;
  r.refresh = () => { globalThis.__rereads += 1; return Promise.resolve(); };
  return r;
};
"""
EVENTS_SCRIPT = r"""
h.render();
const hs = h.global('__handlers');
const mine = hs.filter((x) => x.types.includes('music.now_playing.changed'));
const cb = mine[mine.length - 1].cb;
const reads = () => h.global('__rereads');
const ev = (data) => cb({ type: 'music.now_playing.changed', data });
const office = (title, state = 'play') => ({ state, song: { file: title + '.mp3', title }, stream_url: 'x' });
const out = {};
ev({ office: office('Bravo'), den: office('Foxtrot') });  out.otherRoom = reads();
ev({ office: office('Bravo'), den: office('Golf') });     out.otherAgain = reads();
ev({ office: office('Charlie'), den: office('Golf') });   out.thisRoomSong = reads();
ev({ office: office('Charlie'), den: office('Hotel') });  out.backToOther = reads();
ev({ office: office('Charlie', 'pause'), den: office('Hotel') });  out.thisRoomPaused = reads();
cb({ type: 'music.now_playing.changed' });               out.noData = reads();
out.hookEvents = (h.global('__opts') || {}).eventTypes || null;
return out;
"""

LONG = [item(100 + i, f"Song {i}", playing=(i == 0)) for i in range(250)]
DEEP = [item(100 + i, f"Song {i}", playing=(i == 400)) for i in range(1500)]

SCENARIOS = {
    "household_adds": _scenario(ADD_SCRIPT, api={
        qpath("office"): queue(), "POST /api/music/queue/office/add": {"queued": []}}),
    "household_other_room": _scenario(ADD_SCRIPT, props={**PROPS, "room": "den"}, api={
        qpath("den"): queue("den"), "POST /api/music/queue/den/add": {"queued": []}}),
    "guest_playing": _scenario(ADD_SCRIPT, api={
        qpath("office"): queue(guest=True), "POST /api/music/queue/office/add": {"queued": []}}),
    "guest_refused": _scenario(
        "h.render(); await h.type((e) => e.type === 'input' && e.props.role === 'combobox', 'blue');"
        " await h.click(byLabel(/^add Blue Train to the queue/));"
        " return { note: note(), fn: h.fnCalls };",
        api={qpath("office"): queue(guest=True), "POST /api/music/queue/office/add": {"__error": {
            "status": 403, "message": "403 Forbidden",
            "detail": {"detail": "that room isn't playing, so a guest can't use its queue"}}}}),
    "household_add_fails": _scenario(
        "h.render(); await h.type((e) => e.type === 'input' && e.props.role === 'combobox', 'blue');"
        " await h.click(byLabel(/^add Blue Train to the queue/));"
        " return { note: note() };",
        api={qpath("office"): queue(), "POST /api/music/queue/office/add": {"__error": {
            "status": 500, "message": "boom"}}}),
    "guest_idle": _scenario(
        "h.render(); return { box: shown(), text: h.text().join(' | ') };",
        api={qpath("office"): queue(items=[], current_song_id=None, editable=False, guest=True,
                                    blocked_reason="Pair this phone to change a room that isn't playing")}),
    "guest_banned": _scenario(
        "h.render(); return { box: shown(), text: h.text().join(' | ') };",
        api={qpath("office"): queue(editable=False, guest=True,
                                    blocked_reason="An admin has stopped guests on this phone from changing the music")}),
    "household_blocked_empty": _scenario(
        "h.render(); return { box: shown(), text: h.text().join(' | ') };",
        api={qpath("office"): queue(items=[], editable=False,
                                    blocked_reason="An admin has blocked this device from editing office's queue")}),
    "read_failed": _scenario(
        "h.render(); return { box: shown(), text: h.text().join(' | ') };",
        api={qpath("office"): {"__error": {"status": 401, "message": "401 Unauthorized"}}}),
    "other_rooms_answer": _scenario(
        # The hook still holds the office answer while den's read is out.
        "h.render(); return { box: shown(), text: h.text().join(' | ') };",
        props={**PROPS, "room": "den"}, api={qpath("den"): queue("office")}),
    "events": _scenario(EVENTS_SCRIPT, api={qpath("office"): queue()}, setup=EVENTS_SETUP),
    "long_queue": _scenario(
        "h.render(); const first = h.findAll(cls('rq-row')).length;"
        " const more = h.find(cls('rq-more')); const moreText = more && more.text;"
        " await h.click(cls('rq-more'));"
        " return { first, moreText, second: h.findAll(cls('rq-row')).length,"
        " moreAfter: (h.find(cls('rq-more')) || {}).text || null };",
        api={qpath("office"): queue(items=LONG)}),
    "deep_queue": _scenario(
        "const titles = () => h.text().filter((t) => /^Song [0-9]+$/.test(t));"
        " h.render(); const rows = h.findAll(cls('rq-row')); const before = titles();"
        " const fold = h.findAll(cls('rq-more')).map((e) => e.text);"
        " await h.click((e) => cls('rq-more')(e) && /played/.test(e.text));"
        " return { n: rows.length, firstTitle: before[0], fold, firstAfter: titles()[0],"
        " playingDrawn: before.includes('Song 400') };",
        api={qpath("office"): queue(items=DEEP)}),
    "kept_room_before_rooms": _scenario(
        "h.render(); return { hooks: h.hookCalls, text: h.text().join(' | '), box: !!shown() };",
        props={"rooms": [], "nowPlaying": [], "npError": None, "npLoading": True},
        setup=("localStorage.getItem = (k) => (k === 'domovoi.music.queueRoom' ? 'den' : null);"),
        api={qpath("den"): queue("den")}),
    "kept_room_gone": _scenario(
        "h.render(); return { hooks: h.hookCalls };",
        setup=("localStorage.getItem = (k) => (k === 'domovoi.music.queueRoom' ? 'attic' : null);"),
        api={qpath("office"): queue()}),
    "rooms_loading": _scenario(
        "h.render(); return { text: h.text().join(' | '), hooks: h.hookCalls };",
        props={"rooms": [], "nowPlaying": [], "npError": None, "npLoading": True}),
    "picked_room_is_kept": _scenario(
        "h.render(); return { kept: h.global('__kept') };",
        props={**PROPS, "room": "den"},
        setup="globalThis.__kept = []; localStorage.setItem = (k, v) => globalThis.__kept.push([k, v]);",
        api={qpath("den"): queue("den")}),
    "page_no_clock_rooms_folded": {
        "files": FILES, "component": "MusicPage",
        "setup": (PRELUDE + " globalThis.useGuestViewer = () => false; globalThis.__iv = [];"
                  " globalThis.setInterval = (fn, ms) => { globalThis.__iv.push(ms); return 1; };"
                  " localStorage.getItem = () => null;"),
        "api": {"GET /api/music/now-playing": NP, "GET /api/playlists": [],
                "GET /api/music/library/stats": None, "GET /api/acquisitions?limit=100": None,
                qpath("office"): queue()},
        "script": ("h.render(); await h.click({ type: 'button', text: 'Room queue' });"
                   " return { iv: h.global('__iv') };"),
    },
    "page_clock_per_playing_card": {
        "files": FILES, "component": "MusicPage",
        "setup": (PRELUDE + " globalThis.useGuestViewer = () => false; globalThis.__iv = [];"
                  " globalThis.setInterval = (fn, ms) => { globalThis.__iv.push(ms); return 1; };"),
        "api": {"GET /api/music/now-playing": NP + [{"room_id": "attic", "state": "stop", "song": None}],
                "GET /api/playlists": [], "GET /api/music/library/stats": None,
                "GET /api/acquisitions?limit=100": None, qpath("office"): queue()},
        "script": "h.render(); return { iv: h.global('__iv') };",
    },
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("room_queue_add") / "scenarios.json"
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


# ── the search ──────────────────────────────────────────────────────────


def test_the_search_is_the_tabs_first_control_under_the_room_chips(driven) -> None:
    b = driven["household_adds"]["before"]
    assert b == {"label": "search the library to add to office's queue",
                 "underChips": True, "aboveRows": True}


def test_play_next_and_add_go_to_the_picked_rooms_queue(driven) -> None:
    out = driven["household_adds"]
    assert out["posts"] == [
        ["/api/music/queue/office/add", {"track_ids": [41], "device_id": "dev-1", "where": "next"}],
        ["/api/music/queue/office/add", {"track_ids": [42], "device_id": "dev-1", "where": "end"}],
    ]
    assert "Playing next in office: Blue Monday" in out["afterNext"]
    assert "Added in office: Blue Train" in out["after"]
    other = driven["household_other_room"]
    assert [p[0] for p in other["posts"]] == ["/api/music/queue/den/add"] * 2
    assert other["before"]["label"] == "search the library to add to den's queue"


def test_a_guest_on_a_playing_room_gets_the_search(driven) -> None:
    out = driven["guest_playing"]
    assert out["before"] is not None
    assert [p[1]["where"] for p in out["posts"]] == ["next", "end"]


def test_a_guests_refused_add_is_said_in_words_where_the_box_is(driven) -> None:
    out = driven["guest_refused"]
    assert out["note"] == "add: that room isn't playing, so a guest can't use its queue"
    assert out["fn"] == []          # no toast, no prompt: said under the box


def test_a_household_add_that_fails_says_why(driven) -> None:
    assert "Couldn't add Blue Train: boom" in driven["household_add_fails"]["note"]


def test_no_search_where_adding_cant_work(driven) -> None:
    idle = driven["guest_idle"]
    assert idle["box"] is None
    assert "office isn't playing" in idle["text"]
    banned = driven["guest_banned"]
    assert banned["box"] is None
    assert "An admin has stopped guests on this phone from changing the music" in banned["text"]
    blocked = driven["household_blocked_empty"]
    assert blocked["box"] is None
    # The blocked words are said even when the queue is empty.
    assert "An admin has blocked this device from editing office's queue" in blocked["text"]
    assert driven["read_failed"]["box"] is None


def test_another_rooms_answer_is_never_shown_under_this_room(driven) -> None:
    out = driven["other_rooms_answer"]
    assert out["box"] is None
    assert "loading queue" in out["text"] and "Alpha" not in out["text"]


# ── no re-read storm ────────────────────────────────────────────────────


def test_only_a_change_in_this_room_rereads_its_queue(driven) -> None:
    e = driven["events"]
    assert e["hookEvents"] is None                   # not the blanket hook subscription
    assert e["otherRoom"] == 0 and e["otherAgain"] == 0
    assert e["thisRoomSong"] == 1
    assert e["backToOther"] == 1
    assert e["thisRoomPaused"] == 2
    assert e["noData"] == 3                          # an event without the rooms: re-read, as before


def test_the_music_page_has_no_page_wide_clock(driven) -> None:
    # Rooms folded away: nothing ticks at all (the 1 s page clock re-drew a
    # 1,500-song queue every second).
    assert 1000 not in driven["page_no_clock_rooms_folded"]["iv"]
    # Rooms shown: one clock per PLAYING room's card, none for a stopped one.
    assert driven["page_clock_per_playing_card"]["iv"].count(1000) == 2


# ── long queues ─────────────────────────────────────────────────────────


def test_a_long_queue_draws_100_rows_at_a_time(driven) -> None:
    out = driven["long_queue"]
    assert out["first"] == 100
    assert out["moreText"] == "show more (150 more)"
    assert out["second"] == 200
    assert out["moreAfter"] == "show more (50 more)"


def test_deep_in_a_long_queue_the_played_songs_fold_away(driven) -> None:
    out = driven["deep_queue"]
    assert out["n"] == 100 and out["playingDrawn"]
    assert out["firstTitle"] == "Song 398"
    assert out["fold"][0] == "show 398 played songs"
    assert out["firstAfter"] == "Song 0"


# ── no serial wait ──────────────────────────────────────────────────────


def test_the_room_looked_at_last_is_read_before_now_playing_answers(driven) -> None:
    out = driven["kept_room_before_rooms"]
    assert out["hooks"] == ["/api/music/queue/den?device_id=dev-1"]
    assert "Alpha" in out["text"] and out["box"] is True
    # A kept room that's gone falls back to the playing one.
    assert driven["kept_room_gone"]["hooks"] == ["/api/music/queue/office?device_id=dev-1"]
    assert driven["picked_room_is_kept"]["kept"] == [["domovoi.music.queueRoom", "den"]]


def test_waiting_for_rooms_is_not_no_rooms(driven) -> None:
    out = driven["rooms_loading"]
    assert "loading rooms" in out["text"] and "no rooms set up yet" not in out["text"]
    assert out["hooks"] == []
