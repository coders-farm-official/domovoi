"""Where the dashboard shows lyrics (lyrics contract §12.2–§12.4, [U12]–[U16]).

* the desktop docked bar (web/static/player.jsx MiniPlayer): a "lyrics"
  button before the queue button, only for a library song played here or a
  room's song with a library track id; it opens LyricsFloat, and the float,
  the queue and the cast target are one-at-a-time; a window narrowed to a
  phone, or a song with nothing to show (a station), closes it;
* the phone's player sheet (PlayerSheet): a "lyrics" section between the
  sleep line and "play on", CLOSED until opened, remembered in this browser;
* the Music page's Player tab (music_player_panel.jsx NowPlayingPanel): a
  "lyrics" section right after the head, OPEN unless closed, remembered —
  in room playback too, following the room;
* the room cards (music.jsx NPCard): the room's current TIMED line under
  the title, nothing for plain lyrics, a stream or a refused viewer;
* the Jobs tab (music.jsx JobsTab): LyricsJobsCard between the add-music bar
  and the table, nothing on 401/403 or for an empty library, every line of
  ``lyricsJobsLines`` pinned.

Driven through domovoi/tests/jsx_interact_harness.js (the dashboard's own
Babel, a small stateful React), the playback context scripted. Every lyric
line is INVENTED (lyrics contract [C2]).

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
PLAYER = ["web/static/components.jsx", "web/static/player.jsx", "web/static/lyrics.jsx"]
PANEL = PLAYER + ["web/static/music_player_panel.jsx"]
MUSIC = ["web/static/components.jsx", "web/static/lyrics.jsx", "web/static/music.jsx"]

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


DOC_API = {"GET /api/music/library/7/lyrics": _doc()}
ROOMS = {"GET /api/music/now-playing": [{"room_id": "den"}, {"room_id": "office"}]}
REFUSED = {"__error": {"status": 401, "message": "401 Unauthorized"}}

PRELUDE = r"""
const __st = setTimeout;
setTimeout = (fn, ms, ...a) => { const t = __st(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };
window.__intervals = [];
setInterval = (fn, ms) => { window.__intervals.push({ fn, ms, live: true }); return window.__intervals.length; };
clearInterval = (id) => { const t = window.__intervals[id - 1]; if (t) t.live = false; };
window.__store = {};
localStorage = {
  getItem: (k) => (k in window.__store ? window.__store[k] : null),
  setItem: (k, v) => { window.__store[k] = String(v); },
  removeItem: (k) => { delete window.__store[k]; },
};
window.__listeners = [];
window.addEventListener = (t, fn) => { window.__listeners.push({ t, fn }); };
window.removeEventListener = (t, fn) => {
  window.__listeners = window.__listeners.filter((l) => !(l.t === t && l.fn === fn));
};
document.addEventListener = () => {}; document.removeEventListener = () => {};
window.__phone = false;
window.__mqFns = [];
window.matchMedia = (q) => ({
  media: q,
  get matches() { return /760px/.test(q) ? window.__phone : false; },
  addEventListener(t, fn) { window.__mqFns.push(fn); },
  removeEventListener(t, fn) { window.__mqFns = window.__mqFns.filter((f) => f !== fn); },
});
window.__resize = (phone) => { window.__phone = phone; window.__mqFns.slice().forEach((fn) => fn()); };
window.history = { state: null, pushState(s) { this.state = s; }, back() { this.state = null; } };
window.__acts = [];
const __rec = (name) => (...a) => { window.__acts.push([name].concat(a.filter((x) => !(x && typeof x === 'object')))); };
window.__item = (n, extra) => Object.assign({
  uid: 'u' + n, kind: 'library', trackId: n, title: 'Track ' + n, artist: 'Artist ' + n,
  album: '', src: '/a/' + n, coverUrl: null, durationSec: 205, seekable: true, cacheable: true, meta: {},
}, extra || {});
window.__radio = { uid: 'r1', kind: 'radio', trackId: null, title: 'Night FM', artist: '', src: '/r',
  coverUrl: null, durationSec: null, seekable: false, cacheable: false, meta: {} };
window.__makeP = (over) => {
  const queue = [window.__item(7), window.__item(8)];
  return Object.assign({
    available: true, queue, index: 0, current: queue[0], status: 'playing',
    positionSec: 17, durationSec: 205, volume: 0.5, muted: false, eqBands: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    eqEnabled: false, playbackRate: 1, target: { kind: 'browser' }, remoteNp: null, sleepRemainingSec: null,
    toggle: __rec('toggle'), next: __rec('next'), prev: __rec('prev'), seek: __rec('seek'), stop: __rec('stop'),
    setVolume: __rec('setVolume'), toggleMute: __rec('toggleMute'), setSleep: __rec('setSleep'),
    jumpTo: __rec('jumpTo'), removeAt: __rec('removeAt'), moveItem: __rec('moveItem'), clearQueue: __rec('clearQueue'),
    castTo: (t) => { __rec('castTo')(t); return Promise.resolve(); },
    getAnalyser: () => null, offline: { supported: () => false },
  }, over || {});
};
"""

HELPERS = r"""
const W = () => h.global('window');
const cls = (c) => (e) => String((e.props && e.props.className) || '').split(' ').includes(c);
const iconOf = (e) => {
  const kids = [].concat((e.props && e.props.children) || []);
  const ic = kids.find((k) => k && typeof k === 'object' && k.props && k.props.name);
  return ic ? ic.props.name : null;
};
const settle = async () => { await h.settle(); h.rerender(); await h.settle(); h.rerender(); };
const start = async () => { h.render(); await settle(); };
const deskButtons = () => h.findAll((e) => e.type === 'button' && h.inside(e, cls('mp-side-desk')))
  .map((e) => e.props.title || iconOf(e));
const byTitle = (t) => (e) => e.type === 'button' && e.props.title === t;
const float = () => h.find(cls('lyr-float'));
const sheet = () => h.find(cls('mp-sheet'));
const lineStates = () => h.findAll(cls('lyr-line')).map((e) => (String(e.props.className).split(' ')
  .find((c) => /^lyr-(past|now|next)$/.test(c)) || '').slice(4));
const lineTypes = () => h.findAll(cls('lyr-line')).map((e) => e.type);
const view = () => h.find(cls('lyr-view'));
"""


def _scenario(script: str, *, p: dict | None = None, api: dict | None = None,
              component: str = "MiniPlayer", props: dict | None = None, files: list[str] | None = None,
              setup: str = "", fn_props: list[str] | None = None, playback: bool = True) -> dict:
    comp = f"(PlaybackContext._value = window.__P, {component})" if playback else component
    return {
        "files": files or PLAYER,
        "component": comp,
        "props": props or {},
        "fnProps": fn_props or [],
        "api": {**DOC_API, **ROOMS, **(api or {})},
        "setup": PRELUDE + f" window.__P = window.__makeP({json.dumps(p or {})});" + setup,
        "script": HELPERS + script,
    }


ROOM = {"target": {"kind": "room", "roomId": "den"},
        "remoteNp": {"room_id": "den", "track_id": 7, "state": "pause", "elapsed_sec": 17,
                     "duration_sec": 205, "readAt": 0}}
ROOM_NO_TRACK = {"target": {"kind": "room", "roomId": "den"},
                 "remoteNp": {"room_id": "den", "track_id": None, "state": "play", "elapsed_sec": 3,
                              "duration_sec": None, "readAt": 0}}


def _np(**over) -> dict:
    np = {"room_id": "den", "state": "play", "elapsed_sec": 17, "track_id": 7, "favorited": False,
          "song": {"file": "Example Band/Lantern Song.mp3", "title": "Lantern Song",
                   "artist": "The Example Band", "duration_sec": 205}}
    np.update(over)
    return np


CARD_FNS = ["onPlayRandom", "onPause", "onResume", "onSkip", "onStop", "onFavorite"]
STATUS = {
    "tracks": 5232, "scanned": 5232, "with_lyrics": 3100, "synced": 2500, "plain": 600, "instrumental": 80,
    "by_source": {"sidecar": 10, "embedded": 400, "lrclib": 2690},
    "lrclib": {"enabled": True, "state": "running", "asked": 3000, "found": 2700, "not_found": 250,
               "instrumental": 80, "skipped": 40, "errors": 5, "due": 812, "next_retry_at": None,
               "rate_limited_until": None, "paused_until": None, "last_error": None},
    "lrc_files": {"enabled": True, "written": 2300, "exists": 40, "edited": 2, "deleted": 1, "failed": 3,
                  "last_error": "permission"},
    "scan": {"state": "done", "unscanned": 0, "last_pass_at": None},
    "index": {"state": "done", "pending": 0, "indexed": 3100},
    "search_enabled": True,
}

SCENARIOS: dict[str, dict] = {
    # ── the desktop bar ────────────────────────────────────────────────
    "bar_library": _scenario(
        "await start(); const b = h.find(byTitle('lyrics'));"
        "return { buttons: deskButtons(), icon: iconOf(b), aria: b.props['aria-label'],"
        " pressed: b.props['aria-pressed'], desk: h.inside(b, cls('mp-desk')) };",
    ),
    "bar_radio": _scenario(
        "await start(); return deskButtons();",
        p={"current": None, "queue": [], "index": 0},
        setup=" window.__P.current = window.__radio; window.__P.queue = [window.__radio];",
    ),
    "bar_room_with_a_track": _scenario("await start(); return deskButtons();", p=ROOM),
    "bar_room_without_a_track": _scenario("await start(); return deskButtons();", p=ROOM_NO_TRACK),
    "bar_one_panel_at_a_time": _scenario(
        "await start(); const s = () => ({ lyrics: !!float(), queue: !!h.find(cls('mp-q-save')),"
        " cast: h.text().includes('play on'), pressed: h.find(byTitle('lyrics')).props['aria-pressed'] });"
        "await h.click(byTitle('lyrics')); const a = s();"
        "await h.click(byTitle('queue')); const b = s();"
        "await h.click(byTitle('lyrics')); const c = s();"
        "await h.click(byTitle('cast target')); const d = s();"
        "await h.click(byTitle('lyrics')); const e = s();"
        "await h.click(byTitle('lyrics')); const f = s();"
        "return { a, b, c, d, e, f };",
    ),
    "bar_float_follows_this_browser": _scenario(
        "await start(); await h.click(byTitle('lyrics')); await settle();"
        "const f = float();"
        "return { style: { right: f.props.style.right, bottom: f.props.style.bottom, width: f.props.style.width,"
        " height: f.props.style.height, zIndex: f.props.style.zIndex },"
        " layer: h.findAll((e) => e.type === 'div' && e.props.style && e.props.style.zIndex === 46).length,"
        " head: h.findAll((e) => e.type === 'div' && h.inside(e, cls('lyr-float')) && e.text === 'lyrics').length,"
        " compact: cls('lyr-compact')(view()), types: lineTypes(), states: lineStates(),"
        " nudge: !!h.find(cls('lyr-nudge')) };",
    ),
    "bar_float_follows_the_room": _scenario(
        "await start(); await h.click(byTitle('lyrics')); await settle();"
        "return { types: lineTypes(), states: lineStates(), nudge: !!h.find(cls('lyr-nudge')) };",
        p=ROOM,
    ),
    "bar_float_closes": _scenario(
        "await start(); await h.click(byTitle('lyrics')); await settle(); const open = !!float();"
        "await h.click((e) => e.type === 'button' && e.props['aria-label'] === 'close lyrics');"
        "const closedByX = !float();"
        "await h.click(byTitle('lyrics')); await settle();"
        "await h.click((e) => e.type === 'div' && e.props.style && e.props.style.zIndex === 46);"
        "const closedByLayer = !float();"
        "await h.click(byTitle('lyrics')); await settle();"
        "W().__resize(true); h.rerender(); const closedByPhone = !float();"
        "W().__resize(false); h.rerender();"
        "await h.click(byTitle('lyrics')); await settle();"
        "const P = W().__P; P.current = W().__radio; h.rerender(); await settle(); const closedByRadio = !float();"
        "P.current = W().__item(7); h.rerender(); await settle(); const staysClosed = !float();"
        "return { open, closedByX, closedByLayer, closedByPhone, closedByRadio, staysClosed };",
    ),
    "bar_refused_viewer": _scenario(
        "h.render(); const before = deskButtons().includes('lyrics');"
        "await settle();"
        "return { before, after: deskButtons(), float: !!float(),"
        " gets: h.calls.filter((c) => c.path === '/api/music/library/7/lyrics').length };",
        api={"GET /api/music/library/7/lyrics": REFUSED},
    ),
    "bar_reads_the_song_as_it_starts": _scenario(
        "h.render(); await settle(); const atStart = h.calls.filter((c) => c.path === '/api/music/library/7/lyrics').length;"
        "await h.click(byTitle('lyrics')); await settle();"
        "return { atStart, afterOpen: h.calls.filter((c) => c.path === '/api/music/library/7/lyrics').length,"
        " lines: lineStates().length,"
        " radio: (W().__P.current = W().__radio, h.rerender(), await settle(),"
        "         h.calls.filter((c) => c.path.includes('/lyrics')).length) };",
    ),
    # ── the phone sheet ────────────────────────────────────────────────
    "sheet_closed_by_default": _scenario(
        "await start(); await h.click(cls('mp-expand')); await settle();"
        "const secs = h.findAll((e) => e.type === 'section' && h.inside(e, cls('mp-sheet')))"
        "  .map((e) => e.props['aria-label']);"
        "const t = h.find(cls('lyr-sec-toggle'));"
        "const closed = { expanded: t.props['aria-expanded'], view: !!view(), head: cls('mp-sheet-sec-head')(t),"
        " icon: iconOf(t), gets: h.calls.filter((c) => c.path === '/api/music/library/7/lyrics').length };"
        "await h.click(cls('lyr-sec-toggle')); await settle();"
        "const opened = { expanded: h.find(cls('lyr-sec-toggle')).props['aria-expanded'], view: !!view(),"
        " height: view().props.style['--lyr-h'], compact: cls('lyr-compact')(view()),"
        " stored: W().__store['domovoi-lyrics-sheet-open'], types: lineTypes() };"
        "await h.click(cls('lyr-sec-toggle'));"
        "return { secs, closed, opened, reclosed: { view: !!view(), stored: W().__store['domovoi-lyrics-sheet-open'] } };",
        setup=" window.__phone = true;",
    ),
    "sheet_remembered_open": _scenario(
        "await start(); await h.click(cls('mp-expand')); await settle();"
        "return { expanded: h.find(cls('lyr-sec-toggle')).props['aria-expanded'], lines: lineStates().length };",
        setup=" window.__phone = true; window.__store['domovoi-lyrics-sheet-open'] = '1';",
    ),
    "sheet_room": _scenario(
        "await start(); await h.click(cls('mp-expand')); await settle();"
        "return { types: lineTypes(), states: lineStates(), nudge: !!h.find(cls('lyr-nudge')) };",
        p=ROOM, setup=" window.__phone = true; window.__store['domovoi-lyrics-sheet-open'] = '1';",
    ),
    "sheet_radio": _scenario(
        "await start(); await h.click(cls('mp-expand'));"
        "return h.findAll((e) => e.type === 'section' && h.inside(e, cls('mp-sheet'))).map((e) => e.props['aria-label']);",
        setup=(" window.__phone = true; window.__P.current = window.__radio; window.__P.queue = [window.__radio];"),
    ),
    "sheet_storage_throws": _scenario(
        "await start(); await h.click(cls('mp-expand')); await settle();"
        "const before = h.find(cls('lyr-sec-toggle')).props['aria-expanded'];"
        "await h.click(cls('lyr-sec-toggle')); await settle();"
        "return { before, after: h.find(cls('lyr-sec-toggle')).props['aria-expanded'], view: !!view(),"
        " error: h.lastError || null };",
        setup=(" window.__phone = true; localStorage = { getItem() { throw new Error('no'); },"
               " setItem() { throw new Error('no'); }, removeItem() { throw new Error('no'); } };"),
    ),
    # ── the Player tab ─────────────────────────────────────────────────
    "panel_local": _scenario(
        "await start(); const tree = h.tree(); const at = (pred) => tree.findIndex(pred);"
        "const t = h.find(cls('lyr-sec-toggle'));"
        "return { order: [at(cls('np-head')), at(cls('lyr-panel')), at((e) => e.type === 'canvas')],"
        " label: h.find(cls('lyr-panel')).props['aria-label'], expanded: t.props['aria-expanded'],"
        " height: view().props.style['--lyr-h'], compact: cls('lyr-compact')(view()),"
        " types: lineTypes(), states: lineStates() };",
        component="NowPlayingPanel", files=PANEL,
    ),
    "panel_room": _scenario(
        "await start(); return { panel: !!h.find(cls('lyr-panel')), types: lineTypes(), states: lineStates(),"
        " nudge: !!h.find(cls('lyr-nudge')), canvas: !!h.find((e) => e.type === 'canvas') };",
        component="NowPlayingPanel", files=PANEL, p=ROOM,
    ),
    "panel_toggle_remembered": _scenario(
        "await start(); await h.click(cls('lyr-sec-toggle')); await settle();"
        "return { expanded: h.find(cls('lyr-sec-toggle')).props['aria-expanded'], view: !!view(),"
        " stored: W().__store['domovoi-lyrics-panel-open'] };",
        component="NowPlayingPanel", files=PANEL,
    ),
    "panel_remembered_closed": _scenario(
        "await start(); return { expanded: h.find(cls('lyr-sec-toggle')).props['aria-expanded'], view: !!view(),"
        " gets: h.calls.filter((c) => c.path === '/api/music/library/7/lyrics').length };",
        component="NowPlayingPanel", files=PANEL,
        setup=" window.__store['domovoi-lyrics-panel-open'] = '0';",
    ),
    "panel_radio_and_room_stream": _scenario(
        "await start(); const radio = !!h.find(cls('lyr-panel'));"
        "const P = W().__P; P.current = W().__item(7); P.target = { kind: 'room', roomId: 'den' };"
        "P.remoteNp = { room_id: 'den', track_id: null, state: 'play', elapsed_sec: 2, duration_sec: null, readAt: 0 };"
        "h.rerender(); return { radio, roomStream: !!h.find(cls('lyr-panel')) };",
        component="NowPlayingPanel", files=PANEL,
        setup=" window.__P.current = window.__radio; window.__P.queue = [window.__radio];",
    ),
    # ── the room cards ─────────────────────────────────────────────────
    "card_timed": _scenario(
        "await start(); const line = () => { const e = h.find(cls('lyr-room-line')); return e ? e.text : null; };"
        "const at0 = line(); return { at0 };",
        component="NPCard", files=MUSIC, props={"np": _np(), "tick": 0}, fn_props=CARD_FNS, playback=False,
    ),
    "card_ticks": _scenario(
        "await start(); const e = h.find(cls('lyr-room-line')); return e ? e.text : null;",
        component="NPCard", files=MUSIC, props={"np": _np(), "tick": 5}, fn_props=CARD_FNS, playback=False,
    ),
    "card_paused_ignores_the_tick": _scenario(
        "await start(); const e = h.find(cls('lyr-room-line')); return e ? e.text : null;",
        component="NPCard", files=MUSIC, props={"np": _np(state="pause"), "tick": 10}, fn_props=CARD_FNS,
        playback=False,
    ),
    "card_gap_keeps_its_height": _scenario(
        "await start(); const e = h.find(cls('lyr-room-line')); return e ? e.text : null;",
        component="NPCard", files=MUSIC, props={"np": _np(elapsed_sec=21.0), "tick": 0}, fn_props=CARD_FNS,
        playback=False,
    ),
    "card_nudged": _scenario(
        "await start(); const e = h.find(cls('lyr-room-line')); return e ? e.text : null;",
        component="NPCard", files=MUSIC, props={"np": _np(), "tick": 0}, fn_props=CARD_FNS, playback=False,
        setup=" window.__store['domovoi-lyrics-nudge:den'] = '1000';",
    ),
    "card_where_it_sits": _scenario(
        "await start(); const tree = h.tree();"
        "const title = tree.findIndex((e) => e.type === 'div' && e.text === 'Lantern Song');"
        "const line = tree.findIndex(cls('lyr-room-line'));"
        "const bar = tree.findIndex((e) => e.type === 'div' && e.props.style && e.props.style.height === 4);"
        "return { title, line, bar, ok: title >= 0 && title < line && line < bar };",
        component="NPCard", files=MUSIC, props={"np": _np(), "tick": 0}, fn_props=CARD_FNS, playback=False,
    ),
    "card_plain": _scenario(
        "await start(); return { line: !!h.find(cls('lyr-room-line')) };",
        component="NPCard", files=MUSIC, props={"np": _np(), "tick": 0}, fn_props=CARD_FNS, playback=False,
        api={"GET /api/music/library/7/lyrics": _doc(status="plain", lines=None, text=L4)},
    ),
    "card_stream": _scenario(
        "await start(); return { line: !!h.find(cls('lyr-room-line')),"
        " gets: h.calls.filter((c) => c.path.includes('/lyrics')).length };",
        component="NPCard", files=MUSIC, fn_props=CARD_FNS, playback=False,
        props={"np": _np(track_id=None, song={"file": "http://radio.example/stream", "title": "a stream"}),
               "tick": 0},
    ),
    "card_stopped": _scenario(
        "await start(); return { line: !!h.find(cls('lyr-room-line')),"
        " gets: h.calls.filter((c) => c.path.includes('/lyrics')).length };",
        component="NPCard", files=MUSIC, props={"np": _np(state="stop"), "tick": 0}, fn_props=CARD_FNS,
        playback=False,
    ),
    "card_refused": _scenario(
        "await start(); return { line: !!h.find(cls('lyr-room-line')) };",
        component="NPCard", files=MUSIC, props={"np": _np(), "tick": 0}, fn_props=CARD_FNS, playback=False,
        api={"GET /api/music/library/7/lyrics": REFUSED},
    ),
    # ── the Jobs tab ───────────────────────────────────────────────────
    "jobs_card": _scenario(
        "await start(); const tree = h.tree();"
        "const at = (pred) => tree.findIndex(pred);"
        "return { order: [at((e) => e.type === 'input'), at(cls('lyr-jobs')),"
        "                 at((e) => e.text === 'no acquisition jobs')],"
        " head: h.findAll((e) => e.type === 'span' && h.inside(e, cls('lyr-jobs-head'))).map((e) => e.text),"
        " lines: h.findAll(cls('lyr-jobs-line')).map((e) => e.text),"
        " refresh: W().__intervals.filter((t) => t.live).map((t) => t.ms),"
        " asked: h.hookCalls };",
        component="JobsTab", files=MUSIC, playback=False,
        props={"jobs": [], "availability": {}, "loading": False, "rooms": []},
        fn_props=["onCancel", "fire", "refresh"],
        api={"GET /api/music/lyrics/status": STATUS},
    ),
    "jobs_card_refused": _scenario(
        "await start(); return { card: !!h.find(cls('lyr-jobs')), table: h.text().includes('no acquisition jobs'),"
        " polling: W().__intervals.filter((t) => t.live && t.ms === 10000).length };",
        component="JobsTab", files=MUSIC, playback=False,
        props={"jobs": [], "availability": {}, "loading": False, "rooms": []},
        fn_props=["onCancel", "fire", "refresh"],
        api={"GET /api/music/lyrics/status": REFUSED},
    ),
    "jobs_card_forbidden": _scenario(
        "await start(); return !!h.find(cls('lyr-jobs'));",
        component="LyricsJobsCard", files=MUSIC, playback=False,
        api={"GET /api/music/lyrics/status": {"__error": {"status": 403, "message": "403"}}},
    ),
    "jobs_card_empty_library": _scenario(
        "await start(); return !!h.find(cls('lyr-jobs'));",
        component="LyricsJobsCard", files=MUSIC, playback=False,
        api={"GET /api/music/lyrics/status": dict(STATUS, tracks=0)},
    ),
    "jobs_lines": _scenario(
        "const f = W().lyricsJobsLines; const base = " + json.dumps(STATUS) + ";"
        "const w = (over) => f(Object.assign({}, base, over));"
        "const lr = (o) => w({ lrclib: Object.assign({}, base.lrclib, o) });"
        "return {"
        " full: f(base),"
        " reading: w({ scan: { state: 'running', unscanned: 32, last_pass_at: null } }).slice(0, 2),"
        " off: lr({ state: 'off' })[2], internet_off: lr({ state: 'internet_off' })[2],"
        " offline: lr({ state: 'offline' })[2], rate_limited: lr({ state: 'rate_limited' })[2],"
        " paused: lr({ state: 'paused', last_error: 'unavailable:timeout' })[2],"
        " paused_bare: lr({ state: 'paused' })[2], error: lr({ state: 'error', last_error: 'OperationalError' })[2],"
        " idle: lr({ state: 'idle' })[2], done_again: lr({ state: 'done', next_retry_at: '2026-11-02T12:00:00+00:00' })[2],"
        " unknown: lr({ state: 'unknown' })[2], missing: w({ lrclib: {} })[2], one_due: lr({ due: 1 })[2],"
        " lrc_off: w({ lrc_files: Object.assign({}, base.lrc_files, { enabled: false }) }).length,"
        " lrc_plain: w({ lrc_files: { enabled: true, written: 1, exists: 0, failed: 0, last_error: null } })[3],"
        " lrc_one: w({ lrc_files: { enabled: true, written: 5, exists: 1, failed: 2, last_error: null } })[3],"
        " pending: w({ index: { state: 'running', pending: 1234, indexed: 1 } }),"
        " search_off: w({ search_enabled: false }).slice(-1)[0], search_unknown: w({ search_enabled: null }).length,"
        " one_song: w({ tracks: 1, with_lyrics: 1, synced: 0 })[0], nothing: f(null),"
        "};",
        component="LyricsJobsCard", files=MUSIC, playback=False,
    ),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.fail("node is required to drive the dashboard's JSX")
    spec = tmp_path_factory.mktemp("lyrics-player") / "scenarios.json"
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


# ─── the desktop bar ─────────────────────────────────────────────────────


def test_the_bar_offers_lyrics_before_the_queue_for_a_library_song(driven) -> None:
    d = driven["bar_library"]
    assert d["buttons"][d["buttons"].index("lyrics") + 1] == "queue"
    assert (d["icon"], d["aria"], d["pressed"], d["desk"]) == ("mic-vocal", "lyrics", False, True)


def test_no_lyrics_button_for_a_station_or_a_rooms_stream(driven) -> None:
    assert "lyrics" not in driven["bar_radio"]
    assert "lyrics" not in driven["bar_room_without_a_track"]
    assert "lyrics" in driven["bar_room_with_a_track"]


def test_lyrics_queue_and_cast_float_one_at_a_time(driven) -> None:
    d = driven["bar_one_panel_at_a_time"]
    assert d["a"] == {"lyrics": True, "queue": False, "cast": False, "pressed": True}
    assert d["b"] == {"lyrics": False, "queue": True, "cast": False, "pressed": False}
    assert d["c"] == {"lyrics": True, "queue": False, "cast": False, "pressed": True}
    assert d["d"] == {"lyrics": False, "queue": False, "cast": True, "pressed": False}
    assert d["e"] == {"lyrics": True, "queue": False, "cast": False, "pressed": True}
    assert d["f"] == {"lyrics": False, "queue": False, "cast": False, "pressed": False}


def test_the_float_sits_like_the_queue_and_follows_this_browser(driven) -> None:
    d = driven["bar_float_follows_this_browser"]
    assert d["style"] == {"right": 14, "bottom": "calc(var(--dock-bottom, 0px) + 76px)", "width": 380,
                          "height": "min(60vh, 460px)", "zIndex": 47}
    assert d["layer"] == 1 and d["head"] == 1 and d["compact"] is True
    assert set(d["types"]) == {"button"}           # this browser's playback: lines seek
    assert d["states"][:2] == ["past", "now"]      # 17 s in
    assert d["nudge"] is False


def test_the_float_follows_the_room_while_casting(driven) -> None:
    d = driven["bar_float_follows_the_room"]
    assert set(d["types"]) == {"div"}              # a room cannot seek
    assert d["states"][:2] == ["past", "now"]
    assert d["nudge"] is True


def test_the_float_closes(driven) -> None:
    assert driven["bar_float_closes"] == {
        "open": True, "closedByX": True, "closedByLayer": True, "closedByPhone": True,
        "closedByRadio": True, "staysClosed": True,
    }


def test_a_refused_viewer_loses_the_button_as_the_song_starts(driven) -> None:
    d = driven["bar_refused_viewer"]
    assert d["before"] is True                     # before the server has answered
    assert "lyrics" not in d["after"] and d["float"] is False
    assert d["gets"] == 1                          # asked once, never again


def test_the_bar_reads_the_songs_lyrics_once_as_it_starts(driven) -> None:
    d = driven["bar_reads_the_song_as_it_starts"]
    assert d["atStart"] == 1                       # read as the song starts...
    assert d["afterOpen"] == 1 and d["lines"] == 5  # ...and the float opens on it, no second read
    assert d["radio"] == 1                         # a station is never asked about


# ─── the phone sheet ─────────────────────────────────────────────────────


def test_the_sheet_section_sits_before_play_on_closed_until_opened(driven) -> None:
    d = driven["sheet_closed_by_default"]
    assert d["secs"] == ["lyrics", "play on", "queue"]
    # closed; the docked bar read the song's lyrics once as it started
    assert d["closed"] == {"expanded": False, "view": False, "head": True, "icon": "chevron-down", "gets": 1}
    assert d["opened"]["expanded"] is True and d["opened"]["view"] is True
    assert d["opened"]["height"] == "240px" and d["opened"]["compact"] is True
    assert d["opened"]["stored"] == "1"
    assert set(d["opened"]["types"]) == {"button"}
    assert d["reclosed"] == {"view": False, "stored": "0"}


def test_the_sheet_remembers_it_was_opened(driven) -> None:
    assert driven["sheet_remembered_open"] == {"expanded": True, "lines": 5}


def test_the_sheet_follows_the_room(driven) -> None:
    d = driven["sheet_room"]
    assert set(d["types"]) == {"div"} and d["states"][:2] == ["past", "now"] and d["nudge"] is True


def test_no_sheet_section_for_a_station(driven) -> None:
    assert driven["sheet_radio"] == ["play on", "queue"]


def test_the_sheet_toggle_works_when_storage_refuses(driven) -> None:
    assert driven["sheet_storage_throws"] == {"before": False, "after": True, "view": True, "error": None}


# ─── the Player tab ──────────────────────────────────────────────────────


def test_the_player_tab_shows_lyrics_after_the_head_open_by_default(driven) -> None:
    d = driven["panel_local"]
    head, lyr, canvas = d["order"]
    assert 0 <= head < lyr < canvas
    assert d["label"] == "lyrics" and d["expanded"] is True
    assert d["height"] == "320px" and d["compact"] is False
    assert set(d["types"]) == {"button"} and d["states"][:2] == ["past", "now"]


def test_the_player_tab_follows_a_room(driven) -> None:
    d = driven["panel_room"]
    assert d["panel"] is True and set(d["types"]) == {"div"} and d["nudge"] is True
    assert d["states"][:2] == ["past", "now"]
    assert d["canvas"] is False                    # no visualizer while casting, as before


def test_the_player_tab_remembers_a_closed_section(driven) -> None:
    assert driven["panel_toggle_remembered"] == {"expanded": False, "view": False, "stored": "0"}
    assert driven["panel_remembered_closed"] == {"expanded": False, "view": False, "gets": 0}


def test_no_player_tab_lyrics_for_a_station_or_a_rooms_stream(driven) -> None:
    assert driven["panel_radio_and_room_stream"] == {"radio": False, "roomStream": False}


# ─── the room cards ──────────────────────────────────────────────────────


def test_a_room_card_shows_its_current_timed_line(driven) -> None:
    assert driven["card_timed"]["at0"] == L2               # 17 s
    assert driven["card_ticks"] == L3                      # 17 + 5 s
    assert driven["card_paused_ignores_the_tick"] == L2
    assert driven["card_gap_keeps_its_height"] == " "  # the gap at 21.1 s
    assert driven["card_nudged"] == L1                     # 17 s, a second later
    assert driven["card_where_it_sits"]["ok"] is True, driven["card_where_it_sits"]


def test_a_room_card_shows_nothing_else(driven) -> None:
    assert driven["card_plain"] == {"line": False}
    assert driven["card_stream"] == {"line": False, "gets": 0}
    assert driven["card_stopped"] == {"line": False, "gets": 0}
    assert driven["card_refused"] == {"line": False}


# ─── the Jobs tab ────────────────────────────────────────────────────────


def test_the_jobs_card_sits_between_the_add_bar_and_the_table(driven) -> None:
    d = driven["jobs_card"]
    add_bar, card, table = d["order"]
    assert 0 <= add_bar < card < table
    assert d["head"] == ["lyrics · 3,100 of 5,232 songs · 2,500 timed"]
    assert d["lines"] == [
        "in your files: 10 .lrc · 400 in the songs' tags",
        "LRCLIB: asking — 812 songs to go · 2,700 found · 250 not found",
        ".lrc files: 2,300 saved · 40 songs already had one · 3 couldn't be saved (permission)",
    ]
    assert 10000 in d["refresh"]
    assert "/api/music/lyrics/status" in d["asked"]


def test_the_jobs_card_is_hidden_from_a_refused_viewer_and_an_empty_library(driven) -> None:
    # refused: nothing shown, and no 10 s re-read left asking
    assert driven["jobs_card_refused"] == {"card": False, "table": True, "polling": 0}
    assert driven["jobs_card_forbidden"] is False
    assert driven["jobs_card_empty_library"] is False


def test_every_jobs_line(driven) -> None:
    d = driven["jobs_lines"]
    assert d["full"] == [
        "lyrics · 3,100 of 5,232 songs · 2,500 timed",
        "in your files: 10 .lrc · 400 in the songs' tags",
        "LRCLIB: asking — 812 songs to go · 2,700 found · 250 not found",
        ".lrc files: 2,300 saved · 40 songs already had one · 3 couldn't be saved (permission)",
    ]
    assert d["reading"] == ["lyrics · 3,100 of 5,232 songs · 2,500 timed", "reading your files — 5,200 of 5,232"]
    assert d["off"] == "LRCLIB: off — Settings → Configuration → Library"
    assert d["internet_off"] == "LRCLIB: off — this Domovoi stays off the internet (Settings → Internet)"
    assert d["offline"] == "LRCLIB: paused — offline"
    assert d["rate_limited"] == "LRCLIB: paused — LRCLIB asked to slow down"
    assert d["paused"] == "LRCLIB: paused — unavailable:timeout"
    assert d["paused_bare"] == "LRCLIB: paused — it did not answer"
    assert d["error"] == "LRCLIB: paused — OperationalError"
    assert d["idle"] == "LRCLIB: done — 2,700 found · 250 not found"
    assert d["done_again"] == "LRCLIB: done — 2,700 found · 250 not found · asks again from Nov 2"
    assert d["unknown"] == "LRCLIB: status unknown"
    assert d["missing"] == "LRCLIB: status unknown"
    assert d["one_due"] == "LRCLIB: asking — 1 song to go · 2,700 found · 250 not found"
    assert d["lrc_off"] == 3                               # no .lrc line while saving is off
    assert d["lrc_plain"] == ".lrc files: 1 saved"
    assert d["lrc_one"] == ".lrc files: 5 saved · 1 song already had one · 2 couldn't be saved"
    assert d["pending"][-1] == "making lyrics searchable — 1,234 to go"
    assert d["search_off"] == "finding songs by their words: off — Settings → Configuration → Library"
    assert d["search_unknown"] == 4                        # only an explicit false says "off"
    assert d["one_song"] == "lyrics · 1 of 1 song · 0 timed"
    assert d["nothing"] == []


def test_the_jobs_card_reads_quietly_and_on_lyrics_changed() -> None:
    src = (STATIC / "lyrics.jsx").read_text(encoding="utf-8")
    call = re.search(r"useApiObject\('/api/music/lyrics/status',\s*\{([^}]*)\}\)", src)
    assert call, "LyricsJobsCard reads /api/music/lyrics/status through useApiObject"
    assert "quiet: true" in call.group(1)
    assert "eventTypes: ['lyrics.changed']" in call.group(1)
