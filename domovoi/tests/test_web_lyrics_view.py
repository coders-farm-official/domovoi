"""The synced lyrics view — web/static/lyrics.jsx (lyrics contract §12).

What is pinned here:

* the pure parts: ``lyricsActiveIndex`` (the 150 ms lead, binary search),
  ``lyricsRoomPositionMs`` (elapsed + time since the reading while playing,
  less the room's nudge, kept inside the song), ``lyricsTrackIdFor`` (a
  library song here, the ROOM's song while casting, nothing for a stream or
  a viewer outside the household tier) and every line ``lyricsJobsLines``
  can say;
* ``useLyrics``: one read per song shared by every surface, a quiet read
  (a refusal opens no prompt), a cache of 30 with the least recently used
  out, a "still looking" doc read again after a minute and on
  ``lyrics.changed`` at most once per 15 s, a 401/403 hiding every surface;
* ``LyricsView`` in each state; the current line moving with the clock;
  that a change of line re-renders exactly the two lines whose state
  changed (the memo comparator, applied to what each line is handed);
  seeking a line in this browser's playback and never in a room's; the
  room nudge stored per room, clamped, and kept when storage refuses;
  following paused by a hand on the list and resumed by "follow";
* the file's own rules: every top-level name starts with Lyrics / lyrics /
  _lyr, nothing logs, and no open page (the kiosk display, Home) loads it.

The components run in domovoi/tests/jsx_interact_harness.js (the
dashboard's own Babel, a small stateful React) with the playback context
scripted. Every lyric line is INVENTED (lyrics contract [C2]).

No DB, never ``requires_db``; needs ``node`` and fails without it.
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
SCOPE_CHECK = Path(__file__).with_name("script_scope_check.js")
FILES = ["web/static/components.jsx", "web/static/player.jsx", "web/static/lyrics.jsx"]

# Invented lines (lyrics contract §0.2 [C2]).
L1 = "the lantern hums beside the river door"
L2 = "and every copper kettle sings at dawn"
L3 = "oh the paper boats are sailing down the hall"
L4 = "we carried paper boats along the hall"
SYNCED = [[12400, L1], [16850, L2], [21100, ""], [21300, L3], [65000, L3]]


def _doc(track_id: int = 7, **over) -> dict:
    doc = {
        "track_id": track_id, "status": "synced", "checking": False, "source": "sidecar",
        "source_label": "from Lantern Song.lrc",
        "lines": [{"t": t, "text": s} for t, s in SYNCED],
        "text": "\n".join([L1, L2, L3, L3]), "updated_at": "2026-10-05T12:00:00+00:00",
    }
    doc.update(over)
    return doc


PLAIN = _doc(status="plain", source="embedded", source_label="from the song file", lines=None,
             text=f"{L4}\n\n{L1}")
NONE = _doc(status="none", source=None, source_label=None, lines=None, text=None)
REFUSED = {"__error": {"status": 401, "message": "401 Unauthorized"}}
BROKEN = {"__error": {"status": 502, "message": "502 Bad Gateway"}}

PRELUDE = r"""
const __st = setTimeout;
setTimeout = (fn, ms, ...a) => { const t = __st(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };
window.__now = 1000000;
Date.now = () => window.__now;
window.__intervals = [];
setInterval = (fn, ms) => { window.__intervals.push({ fn, ms, live: true }); return window.__intervals.length; };
clearInterval = (id) => { const t = window.__intervals[id - 1]; if (t) t.live = false; };
window.__tick = () => window.__intervals.filter((t) => t.live).forEach((t) => t.fn());
window.__subs = [];
stateBus = { subscribe(cb) { window.__subs.push(cb); return () => { window.__subs = window.__subs.filter((c) => c !== cb); }; } };
window.__emit = (type) => window.__subs.slice().forEach((cb) => cb({ type, data: {} }));
window.__store = {};
localStorage = {
  getItem: (k) => (k in window.__store ? window.__store[k] : null),
  setItem: (k, v) => { window.__store[k] = String(v); },
  removeItem: (k) => { delete window.__store[k]; },
};
window.__acts = [];
const __rec = (name) => (...a) => { window.__acts.push([name].concat(a.filter((x) => !(x && typeof x === 'object')))); };
window.__item = (n, extra) => Object.assign({
  uid: 'u' + n, kind: 'library', trackId: n, title: 'Track ' + n, artist: 'Artist ' + n,
  album: '', src: '/a/' + n, coverUrl: null, durationSec: 205, seekable: true, cacheable: true, meta: {},
}, extra || {});
window.__makeP = (over) => Object.assign({
  available: true, queue: [window.__item(7)], index: 0, current: window.__item(7), status: 'playing',
  positionSec: 0, durationSec: 205, target: { kind: 'browser' }, remoteNp: null,
  seek: __rec('seek'),
}, over || {});
// Every memoised component is wrapped: what each instance is handed, per
// render, and its comparator — so a test can ask which lines React.memo
// would have re-rendered.
window.__memo = {};
window.__rows = [];
React.memo = (C, same) => {
  const name = C.name;
  window.__memo[name] = same;
  const W = function (props) { if (name === 'LyricsLine') window.__rows.push(props); return C(props); };
  Object.defineProperty(W, 'name', { value: name });
  return W;
};
"""

HELPERS = r"""
const W = () => h.global('window');
const cls = (c) => (e) => String((e.props && e.props.className) || '').split(' ').includes(c);
const lineState = (e) => (String(e.props.className).split(' ')
  .find((c) => /^lyr-(past|now|next)$/.test(c)) || '').slice(4);
const lines = () => h.findAll(cls('lyr-line')).map((e) => ({
  type: e.type, text: e.text, state: lineState(e),
  current: e.props['aria-current'] || null, gap: cls('lyr-gap')(e), label: e.props['aria-label'] || null }));
const gets = (path) => h.calls.filter((c) => c.method === 'GET' && c.path === path).length;
const settle = async () => { await h.settle(); h.rerender(); await h.settle(); h.rerender(); };
const start = async () => { h.render(); await settle(); };
const aria = (label) => (e) => e.type === 'button' && e.props['aria-label'] === label;
"""


def _scenario(script: str, *, p: dict | None = None, api: dict | None = None,
              component: str = "LyricsView", props: dict | None = None, setup: str = "",
              files: list[str] | None = None) -> dict:
    return {
        "files": files or FILES,
        "component": f"(PlaybackContext._value = window.__P, {component})",
        "props": props if props is not None else {"trackId": 7, "follow": {"kind": "local"}, "height": 240},
        "api": api if api is not None else {"GET /api/music/library/7/lyrics": _doc()},
        "setup": PRELUDE + f" window.__P = window.__makeP({json.dumps(p or {})});" + setup,
        "script": HELPERS + script,
    }


ROOM_P = {
    "target": {"kind": "room", "roomId": "den"},
    "remoteNp": {"room_id": "den", "track_id": 7, "state": "pause", "elapsed_sec": 17,
                 "duration_sec": 205, "readAt": 0},
}
ROOM_PROPS = {"trackId": 7, "follow": {"kind": "room", "roomId": "den"}, "height": 240}

PROBE = ("(window.__probe = (props) => { const r = useLyrics(window.__tid); "
         "window.__seen = r; return React.createElement('div', { className: 'probe' }, r.state); }, "
         "window.__probe)")

SCENARIOS: dict[str, dict] = {
    # ── the pure parts ──────────────────────────────────────────────────
    "pure": _scenario(
        "const w = W(); const L = [{t: 1000, text: 'a'}, {t: 2000, text: 'b'}, {t: 2000, text: 'c'}, {t: 9000, text: ''}];"
        "const ai = (ms, lines) => w.lyricsActiveIndex(lines === undefined ? L : lines, ms);"
        "const pos = (np, now, off) => w.lyricsRoomPositionMs(np, now, off);"
        "const np = { elapsed_sec: 10, state: 'play', duration_sec: 30, readAt: 5000 };"
        "return {"
        " active: [ai(0), ai(849), ai(850), ai(999), ai(1849), ai(1850), ai(8849), ai(8850), ai(1e9),"
        "          ai(5000, []), ai(5000, null), ai(NaN)],"
        " room: [pos(np, 5000, 0), pos(np, 7500, 0), pos(np, 7500, 500), pos(np, 7500, -1000),"
        "        pos(Object.assign({}, np, { state: 'pause' }), 99999, 0),"
        "        pos(np, 5000, 20000), pos(np, 30000, 0), pos(null, 1, 1),"
        "        pos({ elapsed_sec: 12, state: 'play', duration_sec: null, readAt: 0 }, 1000000, 0)],"
        "};",
    ),
    "track_ids": _scenario(
        "const w = W(); const f = (p) => w.lyricsTrackIdFor(p);"
        "const lib = { kind: 'library', trackId: 7 };"
        "return ["
        " f({ target: { kind: 'browser' }, current: lib }),"
        " f({ target: { kind: 'browser' }, current: { kind: 'radio', trackId: null } }),"
        " f({ target: { kind: 'browser' }, current: { kind: 'podcast', trackId: null } }),"
        " f({ target: { kind: 'browser' }, current: null }),"
        " f({ target: { kind: 'room', roomId: 'den' }, current: lib, remoteNp: { room_id: 'den', track_id: 9 } }),"
        " f({ target: { kind: 'room', roomId: 'den' }, current: lib, remoteNp: { room_id: 'den', track_id: null } }),"
        " f({ target: { kind: 'room', roomId: 'den' }, current: lib, remoteNp: null }),"
        " f(null),"
        "];",
    ),
    # ── LyricsView, state by state ──────────────────────────────────────
    "loading_then_synced": _scenario(
        "h.render(); const loading = { skel: h.findAll(cls('lyr-skel')).length,"
        " bars: h.findAll((e) => e.type === 'span' && h.inside(e, cls('lyr-skel'))).length, lines: lines().length };"
        "await start();"
        "const region = h.find(cls('lyr-scroll'));"
        "return { loading, lines: lines(), region: { role: region.props.role, label: region.props['aria-label'],"
        " tab: region.props.tabIndex, live: region.props['aria-live'] || null },"
        " foot: h.findAll(cls('lyr-source')).map((e) => e.text), gets: gets('/api/music/library/7/lyrics'),"
        " height: h.find(cls('lyr-view')).props.style['--lyr-h'], followBtn: !!h.find(cls('lyr-follow')),"
        " nudge: !!h.find(cls('lyr-nudge')) };",
        p={"positionSec": 13.0},
    ),
    "active_moves": _scenario(
        "await start(); const P = W().__P; const at = (sec) => { P.positionSec = sec; h.rerender();"
        " return lines().map((l) => l.state).join(','); };"
        "return { s0: at(0), s12_25: at(12.25), s12_26: at(12.26), s17: at(17), s21_2: at(21.2), s22: at(22),"
        " s70: at(70), current: (at(17), lines().filter((l) => l.current).map((l) => l.text)) };",
    ),
    "two_rows_per_change": _scenario(
        "await start(); const P = W().__P; const n = lines().length; const same = W().__memo.LyricsLine;"
        "const frame = (sec) => { P.positionSec = sec; W().__rows = []; h.rerender(); return W().__rows.slice(-n); };"
        "const a = frame(13); const b = frame(13.5); const c = frame(17); const d = frame(21.0);"
        "const e = frame(21.2);"
        "const diff = (x, y) => x.map((r, i) => (same(r, y[i]) ? null : i)).filter((i) => i !== null);"
        "return { n, hasCmp: typeof same, sameFrame: diff(a, b), nextLine: diff(b, c), intoGap: diff(c, d),"
        " outOfGap: diff(d, e), seekStable: a[0].seek === e[0].seek, seekIsFn: typeof a[0].seek };",
    ),
    "seek_in_this_browser": _scenario(
        "await start(); await h.click((e) => cls('lyr-line')(e) && e.text === " + json.dumps(L2) + ");"
        "const gap = h.find(cls('lyr-gap'));"
        "return { acts: W().__acts, gapType: gap.type, gapLabel: gap.props['aria-label'],"
        " gapNote: h.findAll((e) => e.type === 'span' && h.inside(e, cls('lyr-gap'))).map((e) => [e.text, e.props['aria-hidden']]),"
        " gapIcon: h.findAll((e) => e.type === 'Icon' && h.inside(e, cls('lyr-gap'))).map((e) => e.props.name) };",
        p={"positionSec": 13.0},
    ),
    "no_seek_for_a_live_item": _scenario(
        "await start(); return lines().map((l) => l.type);",
        p={"current": {"uid": "x", "kind": "library", "trackId": 7, "title": "t", "seekable": False}},
    ),
    "plain": _scenario(
        "await start(); const t = h.find(cls('lyr-plain'));"
        "return { text: t ? t.text : null, lines: lines().length, foot: h.findAll(cls('lyr-source')).map((e) => e.text) };",
        api={"GET /api/music/library/7/lyrics": PLAIN},
    ),
    "instrumental": _scenario(
        "await start(); return h.findAll(cls('lyr-empty')).map((e) => e.text);",
        api={"GET /api/music/library/7/lyrics": _doc(status="instrumental", source=None, source_label=None,
                                                     lines=None, text=None)},
    ),
    "none_checking": _scenario(
        "await start(); return h.findAll(cls('lyr-empty')).map((e) => e.text);",
        api={"GET /api/music/library/7/lyrics": dict(NONE, checking=True)},
    ),
    "none": _scenario(
        "await start(); return h.findAll(cls('lyr-empty')).map((e) => e.text);",
        api={"GET /api/music/library/7/lyrics": NONE},
    ),
    "error_then_retry": _scenario(
        "await start(); const err = h.findAll((e) => h.inside(e, cls('lyr-empty'))).map((e) => e.text).filter(Boolean);"
        "h.api['GET /api/music/library/7/lyrics'] = " + json.dumps(_doc()) + ";"
        "await h.click((e) => e.type === 'button' && e.text === 'retry'); await settle();"
        "return { err, after: lines().length, gets: gets('/api/music/library/7/lyrics') };",
        api={"GET /api/music/library/7/lyrics": BROKEN},
    ),
    "refused_hides_everything": _scenario(
        "h.render(); await settle(); const w = W();"
        "return { tree: h.tree().length, gets: gets('/api/music/library/7/lyrics'),"
        " trackId: w.lyricsTrackIdFor(w.__P) };",
        api={"GET /api/music/library/7/lyrics": REFUSED},
    ),
    "synced_with_no_usable_lines_reads_as_plain": _scenario(
        "await start(); const t = h.find(cls('lyr-plain')); return { plain: t ? t.text : null, lines: lines().length };",
        api={"GET /api/music/library/7/lyrics": _doc(lines=[], text=L4)},
    ),
    # ── following a room ────────────────────────────────────────────────
    "room": _scenario(
        "await start(); const before = lines().map((l) => [l.type, l.state]);"
        "const nudge = h.findAll((e) => e.type === 'button' && h.inside(e, cls('lyr-nudge')))"
        "  .map((e) => [e.props['aria-label'], e.text, !!e.props.disabled]);"
        "for (let i = 0; i < 4; i++) await h.click(aria('lyrics later'));"
        "const later = { store: Object.assign({}, W().__store), states: lines().map((l) => l.state),"
        " label: h.findAll((e) => e.type === 'span' && h.inside(e, cls('lyr-nudge'))).map((e) => e.text)[0] };"
        "await h.click(aria('reset lyrics timing'));"
        "const reset = { store: Object.assign({}, W().__store), states: lines().map((l) => l.state) };"
        "await h.click(aria('lyrics earlier'));"
        "return { before, nudge, later, reset, earlier: Object.assign({}, W().__store), acts: W().__acts };",
        p=ROOM_P, props=ROOM_PROPS,
    ),
    "room_clock_runs_while_playing": _scenario(
        "await start(); const P = W().__P;"
        "const at = (perfNow) => { W().__now = perfNow; h.rerender(); return lines().findIndex((l) => l.state === 'now'); };"
        "return [at(0), at(4000), at(4500), at(48000), at(53000)];",
        p={"target": {"kind": "room", "roomId": "den"},
           "remoteNp": {"room_id": "den", "track_id": 7, "state": "play", "elapsed_sec": 12.5,
                        "duration_sec": 205, "readAt": 0}},
        props=ROOM_PROPS,
        setup=" window.__now = 0;",
    ),
    "room_reading_of_another_room_is_ignored": _scenario(
        "await start(); return lines().findIndex((l) => l.state === 'now');",
        p={"target": {"kind": "room", "roomId": "den"},
           "remoteNp": {"room_id": "office", "track_id": 7, "state": "pause", "elapsed_sec": 30,
                        "duration_sec": 205, "readAt": 0}},
        props=ROOM_PROPS,
    ),
    "nudge_is_per_room_and_clamped": _scenario(
        "await start(); const label = () => h.findAll((e) => e.type === 'span' && h.inside(e, cls('lyr-nudge')))"
        "  .map((e) => e.text)[0];"
        "const first = label(); await h.click(aria('lyrics later')); const second = label();"
        "return { first, second, store: Object.assign({}, W().__store) };",
        p=ROOM_P, props=ROOM_PROPS,
        setup=" window.__store['domovoi-lyrics-nudge:den'] = '99999'; window.__store['domovoi-lyrics-nudge:office'] = '500';",
    ),
    "nudge_survives_storage_that_throws": _scenario(
        "await start(); await h.click(aria('lyrics later')); await h.click(aria('lyrics later'));"
        "return { label: h.findAll((e) => e.type === 'span' && h.inside(e, cls('lyr-nudge'))).map((e) => e.text)[0],"
        " states: lines().map((l) => l.state), error: h.lastError || null };",
        p=ROOM_P, props=ROOM_PROPS,
        setup=(" localStorage = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); },"
               " removeItem() { throw new Error('denied'); } };"),
    ),
    "no_nudge_outside_a_room": _scenario(
        "await start(); return { nudge: !!h.find(cls('lyr-nudge')), store: W().__store };",
    ),
    # ── a hand on the list ──────────────────────────────────────────────
    "follow_pauses_and_resumes": _scenario(
        "await start(); const sc = () => h.find(cls('lyr-scroll'));"
        "const idle = !!h.find(cls('lyr-follow'));"
        "await h.fire(cls('lyr-scroll'), 'onWheel', {}); const wheel = !!h.find(cls('lyr-follow'));"
        "await h.click(cls('lyr-follow')); const resumed = !h.find(cls('lyr-follow'));"
        "await h.fire(cls('lyr-scroll'), 'onKeyDown', { key: 'PageDown' }); const key = !!h.find(cls('lyr-follow'));"
        "await h.click(cls('lyr-follow'));"
        "await h.fire(cls('lyr-scroll'), 'onKeyDown', { key: 'a' }); const otherKey = !!h.find(cls('lyr-follow'));"
        "await h.fire(cls('lyr-scroll'), 'onTouchMove', {}); const touch = !!h.find(cls('lyr-follow'));"
        "await h.click((e) => cls('lyr-line')(e) && e.text === " + json.dumps(L1) + ");"
        "const afterSeek = !h.find(cls('lyr-follow'));"
        "await h.fire(cls('lyr-scroll'), 'onScroll', {}); const scrollAlone = !!h.find(cls('lyr-follow'));"
        "await h.fire(cls('lyr-scroll'), 'onPointerDown', {}); await h.fire(cls('lyr-scroll'), 'onScroll', {});"
        "const drag = !!h.find(cls('lyr-follow'));"
        "return { idle, wheel, resumed, key, otherKey, touch, afterSeek, scrollAlone, drag };",
        p={"positionSec": 13.0},
    ),
    # ── the store ───────────────────────────────────────────────────────
    "one_read_shared": _scenario(
        "h.render(); await settle(); h.rerender(); await settle();"
        "return { gets: gets('/api/music/library/7/lyrics'), state: W().__seen.state, opts: W().__opts };",
        component=PROBE, props={},
        setup=(" window.__tid = 7; window.__opts = [];"
               " const __get = apiGet; apiGet = (p, o) => { window.__opts.push(o || null); return __get(p, o); };"),
    ),
    "checking_is_read_again": _scenario(
        "h.render(); await settle(); const first = gets('/api/music/library/7/lyrics');"
        "W().__now += 30000; W().__tick(); await settle(); const at30 = gets('/api/music/library/7/lyrics');"
        "W().__now += 31000; W().__tick(); await settle(); const at61 = gets('/api/music/library/7/lyrics');"
        "W().__emit('lyrics.changed'); await settle(); const changed = gets('/api/music/library/7/lyrics');"
        "W().__now += 5000; W().__emit('lyrics.changed'); await settle(); const soon = gets('/api/music/library/7/lyrics');"
        "W().__now += 11000; W().__emit('lyrics.changed'); await settle(); const later = gets('/api/music/library/7/lyrics');"
        "W().__emit('music.now_playing.changed'); await settle(); const other = gets('/api/music/library/7/lyrics');"
        "return { first, at30, at61, changed, soon, later, other };",
        component=PROBE, props={}, setup=" window.__tid = 7;",
        api={"GET /api/music/library/7/lyrics": dict(NONE, checking=True)},
    ),
    "a_finished_doc_is_not_polled": _scenario(
        "h.render(); await settle(); W().__now += 120000; W().__tick(); W().__emit('lyrics.changed'); await settle();"
        "return { gets: gets('/api/music/library/7/lyrics'), intervals: W().__intervals.filter((t) => t.live).length };",
        component=PROBE, props={}, setup=" window.__tid = 7;",
    ),
    "cache_of_thirty": _scenario(
        "h.render(); await settle();"
        "for (let i = 1; i <= 31; i++) { W().__tid = i; h.rerender(); await settle(); }"
        "const n = (i) => gets('/api/music/library/' + i + '/lyrics');"
        "const firstPass = [n(1), n(2), n(31)];"
        "W().__tid = 31; h.rerender(); await settle();"
        "W().__tid = 2; h.rerender(); await settle();"
        "W().__tid = 1; h.rerender(); await settle();"
        "return { firstPass, again: [n(1), n(2), n(31)] };",
        component=PROBE, props={}, setup=" window.__tid = 1;",
        api={f"GET /api/music/library/{i}/lyrics": _doc(i) for i in range(1, 32)},
    ),
    "no_song_no_read": _scenario(
        "h.render(); await settle(); return { state: W().__seen.state, calls: h.calls.length };",
        component=PROBE, props={}, setup=" window.__tid = null;",
    ),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.fail("node is required to drive the dashboard's JSX")
    spec = tmp_path_factory.mktemp("lyrics-view") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), "@" + str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


# ─── the pure parts ──────────────────────────────────────────────────────


def test_the_active_line_is_the_last_one_at_the_clock_plus_150ms(driven) -> None:
    # Lines at 1000, 2000, 2000, 9000: the 150 ms lead shows each a hair early;
    # two lines on one time give the later of them; empty / no lines / NaN → -1.
    assert driven["pure"]["active"] == [-1, -1, 0, 0, 0, 2, 2, 3, 3, -1, -1, -1]


def test_the_room_position(driven) -> None:
    # elapsed 10 s read at 5000: playing adds the time since; a positive nudge
    # shows lyrics later (subtracts); paused adds nothing; clamped to 0..30 s;
    # no duration → only the floor.
    assert driven["pure"]["room"] == [10000, 12500, 12000, 13500, 10000, 0, 30000, 0, 1012000]


def test_which_song_a_player_shows_lyrics_for(driven) -> None:
    # a library item here; nothing for radio / podcasts / nothing; the ROOM's
    # song while casting (not this browser's queue item); nothing when the
    # room's song is not a library track or has not been read yet.
    assert driven["track_ids"] == [7, None, None, None, 9, None, None, None]


# ─── LyricsView, state by state ──────────────────────────────────────────


def test_loading_shows_three_bars_then_the_lines(driven) -> None:
    d = driven["loading_then_synced"]
    assert d["loading"] == {"skel": 1, "bars": 3, "lines": 0}
    assert [line["text"] for line in d["lines"]] == [L1, L2, "", L3, L3]
    assert [line["state"] for line in d["lines"]] == ["now", "next", "next", "next", "next"]
    assert d["region"] == {"role": "region", "label": "lyrics", "tab": 0, "live": None}
    assert d["foot"] == ["from Lantern Song.lrc"]
    assert d["gets"] == 1
    assert d["height"] == "240px"
    assert d["followBtn"] is False and d["nudge"] is False


def test_the_current_line_moves_with_the_clock(driven) -> None:
    d = driven["active_moves"]
    assert d["s0"] == "next,next,next,next,next"
    assert d["s12_25"] == "now,next,next,next,next"       # 12.25 + 0.15 lead = 12.4
    assert d["s12_26"] == "now,next,next,next,next"
    assert d["s17"] == "past,now,next,next,next"
    assert d["s21_2"] == "past,past,past,now,next"        # the gap at 21.1 s is over by 21.15
    assert d["s22"] == "past,past,past,now,next"
    assert d["s70"] == "past,past,past,past,now"
    assert d["current"] == [L2]


def test_a_change_of_line_re_renders_exactly_the_two_lines_that_changed(driven) -> None:
    d = driven["two_rows_per_change"]
    assert d["n"] == 5 and d["hasCmp"] == "function"
    assert d["sameFrame"] == []          # a frame on the same line re-renders no line
    assert d["nextLine"] == [0, 1]       # the line that stopped being current, and the next
    assert d["intoGap"] == [1, 2]        # into the instrumental gap (21.1 s)...
    assert d["outOfGap"] == [2, 3]       # ...and out of it: two lines each time
    assert d["seekStable"] is True and d["seekIsFn"] == "function"


def test_a_line_seeks_this_browsers_playback(driven) -> None:
    d = driven["seek_in_this_browser"]
    assert d["acts"] == [["seek", 16.85]]
    assert d["gapType"] == "button" and d["gapLabel"] == "instrumental break"
    # a muted music note, the design system's icon (no Unicode glyph as an icon)
    assert d["gapNote"] == [["", "true"]] and d["gapIcon"] == ["music"]


def test_a_live_item_is_not_seekable(driven) -> None:
    assert set(driven["no_seek_for_a_live_item"]) == {"div"}


def test_plain_lyrics(driven) -> None:
    d = driven["plain"]
    assert d["text"] == f"{L4}\n\n{L1}"
    assert d["lines"] == 0
    assert d["foot"] == ["from the song file"]


def test_instrumental_none_and_still_looking(driven) -> None:
    assert driven["instrumental"] == ["instrumental — no words to show"]
    assert driven["none_checking"] == ["looking for lyrics…"]
    assert driven["none"] == ["no lyrics for this song"]


def test_a_failed_read_offers_retry(driven) -> None:
    d = driven["error_then_retry"]
    assert "couldn't load the lyrics" in d["err"]
    assert d["after"] == 5 and d["gets"] == 2


def test_a_refusal_hides_the_view_and_every_surface(driven) -> None:
    d = driven["refused_hides_everything"]
    assert d["tree"] == 0
    assert d["gets"] == 1
    assert d["trackId"] is None          # lyricsTrackIdFor: no surface offers lyrics now


def test_timed_lyrics_with_no_usable_line_read_as_plain(driven) -> None:
    assert driven["synced_with_no_usable_lines_reads_as_plain"] == {"plain": L4, "lines": 0}


# ─── following a room ────────────────────────────────────────────────────


def test_a_room_is_followed_by_its_reading_with_a_nudge_per_room(driven) -> None:
    d = driven["room"]
    # paused at 17 s: line 2 current; plain lines (a room cannot seek)
    assert d["before"] == [["div", "past"], ["div", "now"], ["div", "next"], ["div", "next"], ["div", "next"]]
    assert d["nudge"] == [["lyrics earlier", "−¼ s", False], ["lyrics later", "+¼ s", False],
                          ["reset lyrics timing", "reset", True]]
    # four times later: +1 s — 17 - 1 + 0.15 lead is before line 2
    assert d["later"]["store"] == {"domovoi-lyrics-nudge:den": "1000"}
    assert d["later"]["states"] == ["now", "next", "next", "next", "next"]
    assert d["later"]["label"] == "timing +1.00 s"
    assert d["reset"]["store"] == {}
    assert d["reset"]["states"][1] == "now"
    assert d["earlier"] == {"domovoi-lyrics-nudge:den": "-250"}
    assert d["acts"] == []               # nothing seeks a room


def test_the_room_clock_runs_between_readings_while_it_plays(driven) -> None:
    # read at perf 0 with 12.5 s elapsed: line 1 (12.4 s) at once; 4 s on it
    # is 16.5 s (+0.15 lead, still line 1); 4.5 s on, 17.0 s, line 2; 48 s
    # on, 60.5 s, the line at 21.3 s; 53 s on, 65.5 s, the last line.
    assert driven["room_clock_runs_while_playing"] == [0, 0, 1, 3, 4]


def test_another_rooms_reading_is_not_this_rooms_clock(driven) -> None:
    assert driven["room_reading_of_another_room_is_ignored"] == -1


def test_the_nudge_is_clamped_and_kept_per_room(driven) -> None:
    d = driven["nudge_is_per_room_and_clamped"]
    assert d["first"] == "timing +10.00 s"           # 99999 stored → clamped to 10 s
    assert d["second"] == "timing +10.00 s"
    assert d["store"] == {"domovoi-lyrics-nudge:den": "10000", "domovoi-lyrics-nudge:office": "500"}


def test_the_nudge_works_when_storage_refuses(driven) -> None:
    d = driven["nudge_survives_storage_that_throws"]
    assert d["label"] == "timing +0.50 s"
    # 17 s elapsed, lyrics half a second later: 16.5 s, still the first line
    assert d["states"][:2] == ["now", "next"]
    assert d["error"] is None


def test_no_nudge_in_this_browsers_playback(driven) -> None:
    assert driven["no_nudge_outside_a_room"] == {"nudge": False, "store": {}}


# ─── a hand on the list ──────────────────────────────────────────────────


def test_a_hand_on_the_list_pauses_following_and_follow_resumes(driven) -> None:
    assert driven["follow_pauses_and_resumes"] == {
        "idle": False, "wheel": True, "resumed": True, "key": True, "otherKey": False,
        "touch": True, "afterSeek": True, "scrollAlone": False, "drag": True,
    }


# ─── the store ───────────────────────────────────────────────────────────


def test_one_read_per_song_and_a_quiet_one(driven) -> None:
    d = driven["one_read_shared"]
    assert d["gets"] == 1 and d["state"] == "ready"
    # quiet: a refusal opens no pair / sign-in prompt — nobody asked
    assert d["opts"] == [{"quiet": True}]


def test_a_still_looking_doc_is_read_again(driven) -> None:
    d = driven["checking_is_read_again"]
    assert (d["first"], d["at30"], d["at61"]) == (1, 1, 2)    # after a minute
    assert d["changed"] == 3                                   # lyrics.changed...
    assert d["soon"] == 3                                      # ...at most once per 15 s
    assert d["later"] == 4
    assert d["other"] == 4                                     # other events: nothing


def test_a_finished_doc_is_not_read_again(driven) -> None:
    assert driven["a_finished_doc_is_not_polled"] == {"gets": 1, "intervals": 0}


def test_the_cache_keeps_thirty_songs(driven) -> None:
    d = driven["cache_of_thirty"]
    assert d["firstPass"] == [1, 1, 1]
    # 31 songs read: song 1 (least recently used) fell out and is read
    # again; 2 and 31 are still held.
    assert d["again"] == [2, 1, 1]


def test_no_song_means_no_read(driven) -> None:
    assert driven["no_song_no_read"] == {"state": "hidden", "calls": 0}


# ─── the file's own rules ────────────────────────────────────────────────


def _declarations() -> dict:
    node = shutil.which("node")
    assert node, "node is required"
    out = subprocess.run([node, str(SCOPE_CHECK), str(REPO_ROOT)],
                         capture_output=True, text=True, timeout=300, check=True)
    return json.loads(out.stdout)["decls"]


def test_every_top_level_name_is_the_lyrics_files_own() -> None:
    decls = _declarations()
    mine = decls["lyrics.jsx"]
    names = mine["lexical"] + mine["other"]
    assert "LyricsView" in names and "useLyrics" in names
    bad = [n for n in names if not re.match(r"^(Lyrics|lyrics|_lyr)", n) and n != "useLyrics"]
    assert not bad, f"lyrics.jsx top-level names outside its prefixes: {bad}"


def test_the_exports() -> None:
    src = (STATIC / "lyrics.jsx").read_text(encoding="utf-8")
    exported = re.search(r"Object\.assign\(window, \{(.*?)\}\);", src, re.S).group(1)
    names = {n.strip() for n in exported.replace("\n", " ").split(",") if n.strip()}
    assert names == {"LyricsView", "LyricsFloat", "LyricsRoomLine", "LyricsJobsCard", "useLyrics",
                     "lyricsActiveIndex", "lyricsJobsLines", "lyricsTrackIdFor", "lyricsRoomPositionMs"}


def test_nothing_in_the_lyrics_file_logs_or_titles() -> None:
    src = (STATIC / "lyrics.jsx").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    assert "console." not in code
    assert "document.title" not in code
    assert "fire(" not in code and "toast" not in code.lower()


def test_no_open_page_carries_lyrics() -> None:
    """The kiosk display and Home never show lyrics (lyrics contract [U17])."""
    display_html = (STATIC / "display.html").read_text(encoding="utf-8")
    assert "lyrics" not in display_html.lower()
    for name in ("display.jsx", "home.jsx"):
        src = (STATIC / name).read_text(encoding="utf-8")
        assert not re.search(r"\b(Lyrics\w*|lyrics\w+|useLyrics)\b", src), name
        assert "/lyrics" not in src, name


def test_lyrics_load_right_after_the_player_and_are_in_the_offline_shell() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script type="text/babel" data-presets="react" src="([^"]+)"></script>', html)
    assert scripts[scripts.index("player.jsx") + 1] == "lyrics.jsx"
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    shell = re.search(r"const SHELL_ASSETS = \[(.*?)\];", sw, re.S).group(1)
    names = re.findall(r"'([^']+)'", shell)
    assert names[names.index("/player.jsx") + 1] == "/lyrics.jsx"
