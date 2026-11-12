"""The queue panel's search: add songs to the queue from the queue itself.

The text box under the queue (web/static/player.jsx) used to be "save queue
as playlist". It is now a search (title / artist / album through the library
search, genre through the genres list) with Play next and Add to queue on
each result, and "save as playlist" is a secondary button that opens a name
prompt. The panel renders in the expanded player's Queue tab and in the
desktop bar's floating queue; both draw the same ``PlayerQueueAdd``.

Run in domovoi/tests/jsx_interact_harness.js against a scripted playback
context. No DB, never ``requires_db``; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/player.jsx"]

PRELUDE = r"""
API_BASE = "";
window.__rnd = 0;
Math.random = () => window.__rnd;
// A real debounce, flushed by hand: the harness's own passes the value straight through.
window.__dbDelay = null;
window.__flush = null;
useDebouncedValue = (v, d) => {
  window.__dbDelay = d;
  const [x, setX] = React.useState(v);
  window.__flush = () => setX(v);
  return x;
};
window.__acts = [];
const __rec = (name) => (...a) => { window.__acts.push([name].concat(a.map((x) => JSON.stringify(x)))); };
const __item = (n, extra) => Object.assign({
  uid: 'u' + n, kind: 'library', trackId: n, title: 'Track ' + n, artist: 'Artist ' + n,
  album: '', src: '/a/' + n, coverUrl: null, durationSec: 200, seekable: true, cacheable: true, meta: {},
}, extra || {});
window.__makeP = (over) => {
  const queue = [__item(1), __item(2)];
  return Object.assign({
    available: true, queue, index: 0, current: queue[0], status: 'playing',
    target: { kind: 'browser' },
    enqueue: __rec('enqueue'), playNext: __rec('playNext'),
  }, over || {});
};
"""

HELPERS = r"""
const cls = (c) => (e) => String((e.props && e.props.className) || '').split(' ').includes(c);
const W = () => h.global('window');
const search = () => h.find(cls('mp-q-search'));
const opts = () => h.findAll((e) => e.props && e.props.role === 'option');
const optTitles = () => opts().map((o) => h.findAll((e) => (cls('mp-q-title')(e) || cls('mp-q-sub')(e)) && h.inside(e, (a) => a === o)).map((e) => e.text).join(' | '));
const acts = () => W().__acts.slice();
const get = (re) => h.calls.filter((c) => c.method === 'GET' && re.test(c.path));
const typeAndFlush = async (v) => { await h.type(cls('mp-q-search'), v); W().__flush(); await h.fire(cls('mp-q-search'), 'onKeyDown', { key: 'Shift', preventDefault() {} }); };
const byLabel = (re) => (e) => e.type === 'button' && re.test(e.props['aria-label'] || '');
const note = () => h.findAll(cls('mp-q-note')).map((e) => e.text).join('|');
"""

SONGS = {"items": [
    {"id": 11, "title": "Blue Monday", "artist": "Order", "album": "Power"},
    {"id": 12, "title": "Blue Train", "artist": "Coltrane", "album": "Blue Train"},
]}
GENRES = {"items": [{"key": "rock", "name": "Rock", "track_count": 30}]}
ROCK = {"items": [{"id": 100 + i, "title": f"Rock {i}", "artist": "Band"} for i in range(30)]}
API = {
    "GET /api/music/library?q=blue&limit=8": SONGS,
    "GET /api/music/genres?q=blue&limit=2": GENRES,
    "GET /api/music/library?genre=rock&limit=200": ROCK,
    "POST /api/playlists": {"id": 7},
}


def _scenario(script: str, *, p: dict | None = None, api: dict | None = None, setup: str = "") -> dict:
    return {
        "files": FILES,
        "component": "(() => PlayerQueueAdd({ p: window.__P }))",
        "api": {**API, **(api or {})},
        "setup": PRELUDE + f" window.__P = window.__makeP({json.dumps(p or {})});" + setup,
        "script": HELPERS + script,
    }


SCENARIOS = {
    # A random prompt per open, kept while typing.
    "prompt_first": _scenario("h.render(); return search().props.placeholder;"),
    "prompt_last": _scenario("h.render(); await h.type(cls('mp-q-search'), 'x');"
                             " return search().props.placeholder;",
                             setup=" window.__rnd = 0.999;"),
    "combobox": _scenario(
        "h.render(); const p = search().props;"
        "return { role: p.role, label: p['aria-label'], auto: p['aria-autocomplete'], list: p['aria-controls'],"
        " expanded: p['aria-expanded'], results: !!h.find(cls('mp-q-results')) };"),
    # Debounced ~250 ms, two characters at least; songs and genres both asked.
    "debounce": _scenario(
        "h.render(); await h.type(cls('mp-q-search'), 'b');"
        "W().__flush(); h.rerender(); await h.settle();"
        "const one = h.calls.length;"
        "await h.type(cls('mp-q-search'), 'blue');"
        "const before = h.calls.length;"
        "W().__flush(); await h.fire(cls('mp-q-search'), 'onKeyDown', { key: 'Shift' });"
        "return { delay: W().__dbDelay, one, before, paths: h.calls.map((c) => c.path), titles: optTitles() };"),
    "results_shape": _scenario(
        "h.render(); await typeAndFlush('blue');"
        "const o = opts(); const first = o[0].props;"
        "return { titles: optTitles(), selected: o.map((x) => x.props['aria-selected']),"
        " active: search().props['aria-activedescendant'], firstId: first.id,"
        " expanded: search().props['aria-expanded'], listbox: !!h.find((e) => e.props && e.props.role === 'listbox'),"
        " btns: h.findAll((e) => e.type === 'button' && /^(add|play)/.test(e.props['aria-label'] || '')).length };"),
    "keyboard": _scenario(
        "h.render(); await typeAndFlush('blue');"
        "await h.key(cls('mp-q-search'), 'Enter');"
        "await h.key(cls('mp-q-search'), 'ArrowDown');"
        "const moved = search().props['aria-activedescendant'];"
        "await h.fire(cls('mp-q-search'), 'onKeyDown', { key: 'Enter', shiftKey: true, preventDefault() {} });"
        "await h.key(cls('mp-q-search'), 'ArrowDown'); await h.key(cls('mp-q-search'), 'ArrowDown');"
        "const wrapped = search().props['aria-activedescendant'];"
        "await h.key(cls('mp-q-search'), 'ArrowUp');"
        "const up = search().props['aria-activedescendant'];"
        "return { acts: acts(), moved, wrapped, up, status: note() };"),
    "click_actions": _scenario(
        "h.render(); await typeAndFlush('blue');"
        "await h.click(byLabel(/^add Blue Train to the queue/));"
        "await h.click(byLabel(/^play Blue Monday next/));"
        "return { acts: acts(), status: note() };"),
    "genre_row": _scenario(
        "h.render(); await typeAndFlush('blue');"
        "await h.click(byLabel(/^add Rock songs to the queue/));"
        "const added = JSON.parse(acts()[0][1]);"
        "W().__acts.length = 0;"
        "await h.click(byLabel(/^play Rock songs next/));"
        "const next = acts();"
        "return { titles: optTitles(), added: added.map((i) => i.trackId), nextN: next.length,"
        " nextNames: [...new Set(next.map((a) => a[0]))],"
        " genreGets: get(/genre=rock/).length, status: note() };"),
    "empty_state": _scenario(
        "h.render(); await typeAndFlush('zzz');"
        "return { opts: opts().length, text: h.text().join(' ') };",
        api={"GET /api/music/library?q=zzz&limit=8": {"items": []}, "GET /api/music/genres?q=zzz&limit=2": {"items": []}}),
    "escape": _scenario(
        "h.render(); await typeAndFlush('blue');"
        "let prevented = 0;"
        "await h.fire(cls('mp-q-search'), 'onKeyDown', { key: 'Escape', preventDefault() { prevented++; } });"
        "const cleared = { value: search().props.value, opts: opts().length };"
        "await h.fire(cls('mp-q-search'), 'onKeyDown', { key: 'Escape', preventDefault() { prevented++; } });"
        "return { prevented, cleared };"),
    # Casting: the panel's queue is the one kept for the room; an add goes to it and the room.
    "casting": _scenario(
        "h.render(); await typeAndFlush('blue');"
        "await h.key(cls('mp-q-search'), 'Enter');"
        "return { acts: acts(), posts: h.calls.filter((c) => c.method === 'POST').map((c) => [c.path, c.body]),"
        " playNext: h.findAll(byLabel(/next/)).length, status: note() };",
        p={"target": {"kind": "room", "roomId": "kitchen"}},
        api={"POST /api/music/queue/kitchen/add": {"queued": []}}),
    "casting_room_refuses": _scenario(
        "h.render(); await typeAndFlush('blue'); await h.key(cls('mp-q-search'), 'Enter');"
        "return { acts: acts(), status: note() };",
        p={"target": {"kind": "room", "roomId": "kitchen"}},
        api={"POST /api/music/queue/kitchen/add": {"__error": {"status": 409, "message": "busy"}}}),
    "search_fails": _scenario(
        "h.render(); await typeAndFlush('blue'); return { opts: opts().length, status: note() };",
        api={"GET /api/music/library?q=blue&limit=8": {"__error": {"status": 500, "message": "boom"}}}),
    "search_auth_failure": _scenario(
        "h.render(); await typeAndFlush('blue'); return { opts: opts().length, status: note() };",
        api={"GET /api/music/library?q=blue&limit=8": {"__error": {"status": 401, "message": "no", "loginPrompted": True}}}),
    "add_auth_failure": _scenario(
        "h.render(); await typeAndFlush('blue'); await h.key(cls('mp-q-search'), 'Enter');"
        "return { status: note() };",
        p={"target": {"kind": "room", "roomId": "kitchen"}},
        api={"POST /api/music/queue/kitchen/add": {"__error": {"status": 401, "message": "no", "loginPrompted": True}}}),
    "no_player": _scenario(
        "h.render(); await typeAndFlush('blue'); await h.key(cls('mp-q-search'), 'Enter');"
        "return { acts: acts(), status: note() };", p={"available": False}),
    # Save as playlist: secondary, opens a prompt.
    "save_button": _scenario(
        "h.render(); const b = h.find((e) => e.type === 'button' && /save as playlist/.test(e.text || ''));"
        "return { text: b.text, disabled: !!b.props.disabled, nameInput: !!h.find({ placeholder: 'name your playlist…' }),"
        " searchFirst: h.tree().findIndex(cls('mp-q-search')) < h.tree().findIndex((e) => e === b) };"),
    "save_flow": _scenario(
        "h.render();"
        "await h.click((e) => e.type === 'button' && /save as playlist/.test(e.text || ''));"
        "const open = !!h.find({ placeholder: 'name your playlist…' });"
        "await h.type({ placeholder: 'name your playlist…' }, '  Friday  ');"
        "await h.key({ placeholder: 'name your playlist…' }, 'Enter');"
        "return { open, posts: h.calls.filter((c) => c.method === 'POST').map((c) => [c.path, c.body]),"
        " closed: !h.find({ placeholder: 'name your playlist…' }), status: note(),"
        " saveText: h.findAll((e) => /Saved as/.test(e.text || '')).length };"),
    "save_cancel": _scenario(
        "h.render(); await h.click((e) => e.type === 'button' && /save as playlist/.test(e.text || ''));"
        "await h.click((e) => e.type === 'button' && e.text === 'cancel');"
        "return { prompt: !!h.find({ placeholder: 'name your playlist…' }), posts: h.calls.filter((c) => c.method === 'POST').length };"),
    "save_disabled_without_library": _scenario(
        "h.render(); const b = h.find((e) => e.type === 'button' && /save as playlist/.test(e.text || ''));"
        "return { disabled: !!b.props.disabled, title: b.props.title };",
        p={"queue": [{"uid": "s1", "kind": "stream", "title": "Radio", "artist": ""}]}),
    "save_fails": _scenario(
        "h.render(); await h.click((e) => e.type === 'button' && /save as playlist/.test(e.text || ''));"
        "await h.type({ placeholder: 'name your playlist…' }, 'X');"
        "await h.key({ placeholder: 'name your playlist…' }, 'Enter');"
        "return { status: h.findAll(cls('mp-q-note')).map((e) => e.text).join('|') };",
        api={"POST /api/playlists": {"__error": {"status": 500, "message": "boom"}}}),
    "save_auth_failure": _scenario(
        "h.render(); await h.click((e) => e.type === 'button' && /save as playlist/.test(e.text || ''));"
        "await h.type({ placeholder: 'name your playlist…' }, 'X');"
        "await h.key({ placeholder: 'name your playlist…' }, 'Enter');"
        "return { status: h.findAll(cls('mp-q-note')).map((e) => e.text).join('|'),"
        " stillOpen: !!h.find({ placeholder: 'name your playlist…' }) };",
        api={"POST /api/playlists": {"__error": {"status": 401, "message": "no", "loginPrompted": True}}}),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("queue_add") / "scenarios.json"
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


def _src() -> str:
    return (STATIC / "player.jsx").read_text(encoding="utf-8")


def _prompts() -> list[str]:
    block = re.search(r"const _QUEUE_ASK_PROMPTS = \[(.*?)\n\];", _src(), re.S).group(1)
    return [a or b for a, b in re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"", block)]


def test_the_prompt_is_one_of_many_friendly_ones_picked_per_open(driven) -> None:
    prompts = _prompts()
    assert len(prompts) >= 12 and len(set(prompts)) == len(prompts)
    assert driven["prompt_first"] == prompts[0]
    assert driven["prompt_last"] == prompts[-1]      # kept while typing
    assert "Math.random" in _src()


def test_the_prompts_name_no_brands() -> None:
    low = " ".join(_prompts()).lower()
    for brand in ("spotify", "netflix", "alexa", "siri", "google", "apple", "youtube"):
        assert brand not in low


def test_the_box_is_a_combobox_over_a_listbox(driven) -> None:
    c = driven["combobox"]
    assert c["role"] == "combobox" and c["auto"] == "list" and c["list"] == "mp-q-results"
    assert c["expanded"] is False and c["results"] is False
    r = driven["results_shape"]
    assert r["listbox"] and r["expanded"] is True
    assert r["selected"] == [True, False, False]
    assert r["active"] == r["firstId"] == "mp-q-opt-0"


def test_typing_searches_after_the_debounce_with_two_characters_at_least(driven) -> None:
    d = driven["debounce"]
    assert d["delay"] == 250
    assert d["one"] == 0                      # one character: nothing asked
    assert d["before"] == 0                   # typed, not yet settled
    assert d["paths"] == ["/api/music/library?q=blue&limit=8", "/api/music/genres?q=blue&limit=2"]
    assert len(d["titles"]) == 3


def test_a_song_row_reads_title_artist_with_the_album_muted(driven) -> None:
    t = driven["results_shape"]["titles"]
    assert "Blue Monday · Order" in t[0] and "Power" in t[0]
    assert "Blue Train · Coltrane" in t[1]
    assert "genre" in t[2] and "Rock" in t[2] and "30 songs" in t[2]
    assert driven["results_shape"]["btns"] == 6      # play next + add on each of 3


def test_enter_adds_the_highlighted_row_shift_enter_plays_it_next(driven) -> None:
    k = driven["keyboard"]
    acts = k["acts"]
    assert acts[0][0] == "enqueue" and json.loads(acts[0][1])[0]["trackId"] == 11
    assert k["moved"] == "mp-q-opt-1"
    assert acts[1][0] == "playNext" and json.loads(acts[1][1])["trackId"] == 12
    assert k["wrapped"] == "mp-q-opt-0"        # 1 -> 2 -> 0: Down wraps past the last row
    assert k["up"] == "mp-q-opt-2"             # and Up wraps past the first
    assert "Playing next" in k["status"]


def test_the_row_buttons_add_and_play_next(driven) -> None:
    c = driven["click_actions"]
    assert c["acts"][0][0] == "enqueue" and json.loads(c["acts"][0][1])[0]["trackId"] == 12
    assert c["acts"][1][0] == "playNext" and json.loads(c["acts"][1][1])["trackId"] == 11
    assert "Playing next: Blue Monday" in c["status"]


def test_a_genre_row_adds_up_to_25_of_its_songs_shuffled(driven) -> None:
    g = driven["genre_row"]
    assert len(g["added"]) == 25 and len(set(g["added"])) == 25
    assert all(100 <= i < 130 for i in g["added"])
    assert g["nextN"] == 25 and g["nextNames"] == ["playNext"]
    assert g["genreGets"] == 2
    assert "Playing next: Rock songs (25 songs)" in g["status"]


def test_nothing_matches_is_said_honestly(driven) -> None:
    e = driven["empty_state"]
    assert e["opts"] == 0 and 'Nothing matches "zzz"' in e["text"]


def test_escape_clears_the_search_and_keeps_the_player_open(driven) -> None:
    e = driven["escape"]
    assert e["cleared"] == {"value": "", "opts": 0}
    assert e["prevented"] == 1          # the first only: the empty box lets Escape close the player


def test_casting_adds_to_the_queue_shown_and_to_the_room(driven) -> None:
    c = driven["casting"]
    assert c["acts"][0][0] == "enqueue"
    # "add" lands before the room's Up next picks (where: end, P4).
    assert c["posts"] == [["/api/music/queue/kitchen/add", {"track_ids": [11], "device_id": "dev-1", "where": "end"}]]
    assert c["playNext"] == 3           # play next is offered in a room too (where: next, P4)
    assert "Added in kitchen: Blue Monday" in c["status"]
    r = driven["casting_room_refuses"]
    assert "kitchen didn't take it" in r["status"]


def test_failures_are_said_but_a_refused_sign_in_adds_no_error(driven) -> None:
    assert "Couldn't search the library" in driven["search_fails"]["status"]
    assert driven["search_fails"]["opts"] == 0
    assert driven["search_auth_failure"] == {"opts": 0, "status": ""}
    assert driven["add_auth_failure"]["status"] == ""
    assert "isn't available" in driven["no_player"]["status"] and driven["no_player"]["acts"] == []


def test_save_as_playlist_is_a_secondary_button_below_the_search(driven) -> None:
    b = driven["save_button"]
    assert b["text"].strip() == "save as playlist" and b["disabled"] is False
    assert b["nameInput"] is False and b["searchFirst"] is True


def test_the_save_button_opens_a_name_prompt_and_saves(driven) -> None:
    f = driven["save_flow"]
    assert f["open"] and f["closed"]
    assert f["posts"] == [["/api/playlists", {"name": "Friday"}],
                          ["/api/playlists/7/tracks", {"track_id": 1}],
                          ["/api/playlists/7/tracks", {"track_id": 2}]]
    assert 'Saved as "Friday"' in f["status"]


def test_cancel_closes_the_prompt_without_saving(driven) -> None:
    assert driven["save_cancel"] == {"prompt": False, "posts": 0}


def test_save_is_disabled_with_no_library_songs_in_the_queue(driven) -> None:
    d = driven["save_disabled_without_library"]
    assert d["disabled"] is True and "no library songs" in d["title"]


def test_a_failed_save_is_said_a_refused_sign_in_is_not(driven) -> None:
    assert "Couldn't save the playlist" in driven["save_fails"]["status"]
    assert driven["save_auth_failure"]["status"] == ""
    assert driven["save_auth_failure"]["stillOpen"] is True


def test_both_queue_panels_draw_the_search_not_the_old_save_box() -> None:
    src = _src()
    assert src.count("<PlayerQueueAdd p={p}/>") == 2          # the player's Queue tab and the bar's panel
    assert src.count("<PlayerQueueSave queue=") == 1          # only inside the search block
    assert "save queue as playlist…" not in src


def test_the_panel_styles_exist_and_phone_targets_are_44px() -> None:
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    for sel in (".mp-q-results", ".mp-q-res", ".mp-q-res-main", ".mp-q-saverow", ".mp-q-note"):
        assert sel + " " in css or sel + "{" in css
    assert re.search(r"\.mp-sheet \.mp-q-save \.btn-icon \{ width: 44px; \}", css)
    assert re.search(r"\.mp-sheet \.mp-q-res-main \{ min-height: 44px;", css)
