"""The docked player, and the expanded player it opens, at both widths.

At 375px the docked MiniPlayer (web/static/player.jsx) used to squeeze the
whole desktop bar into one row — cover, title, three transport buttons, a
seek bar with a 280px minimum, volume, queue, cast target and "open full
player", every button 30px. At 760px and below it is now:

* one 64px row: cover, title/artist (ellipsized), play/pause, next and
  "open player", each a 44px target, with a 2px position line on its top
  edge. What only a desktop shows is marked ``mp-desk`` and what only a
  phone shows ``mp-phone``; styles.css hides each at the other width.
* the desktop bar and its floating queue / cast target are unchanged.

The expanded player (PlayerSheet, 2026-11-12) is one component for every
width — it replaced the Music page's Player tab and the phone-only sheet.
It opens from the bar's cover and title (a button at every width), the
desktop's chevron, the phone's "open player", and #music/player (rewritten
to #music, the sheet over it). It covers the screen above the tab strip on
a phone and the column right of the sidebar on a desktop, carries seek,
previous, volume, where it plays and a panel (Lyrics · Equalizer · Queue ·
Hide) over the visualizer. It is a modal dialog and a history entry of its
own: it closes with its chevron, Escape, Back, a page in the sidebar /
strip (the one already showing too) and when the queue it shows is
cleared — not when the window changes width. Focus goes to its close
button when it opens and back to the control that opened it when it closes
in place. In a browser it grows out of the bar (Web Animations; the
harness has none, so it opens and closes at once here).

The components run in domovoi/tests/jsx_interact_harness.js (the
dashboard's own Babel, a small stateful React) against a scripted playback
context; the harness has no layout engine, so each width's layout is
pinned the way the browser decides it: by the classes the components emit
and the rules those classes meet inside and outside the 760px block.

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
FILES = ["web/static/components.jsx", "web/static/player.jsx"]

# ─── the sandbox: recorded listeners, a history stack, a scripted player ──

PRELUDE = r"""
const __st = setTimeout;
setTimeout = (fn, ms, ...a) => { const t = __st(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };
window.__listeners = [];
window.addEventListener = (t, fn) => { window.__listeners.push({ t, fn }); };
window.removeEventListener = (t, fn) => {
  window.__listeners = window.__listeners.filter((l) => !(l.t === t && l.fn === fn));
};
window.__fire = (t, ev) => window.__listeners.filter((l) => l.t === t)
  .forEach((l) => l.fn(Object.assign({ type: t }, ev || {})));
// The document's listeners land on the same list as 'document:<type>'.
document.addEventListener = (t, fn) => { window.__listeners.push({ t: 'document:' + t, fn }); };
document.removeEventListener = (t, fn) => {
  window.__listeners = window.__listeners.filter((l) => !(l.t === 'document:' + t && l.fn === fn));
};
// A tap somewhere: `inside` is the selector its target's closest() finds.
window.__tap = (inside) => window.__fire('document:click',
  { target: { closest: (sel) => (sel === inside ? {} : null) } });
// The 760px media query: a phone until __resize(false).
window.__phone = true;
window.__mqFns = [];
window.__mqAsked = [];
window.matchMedia = (q) => {
  window.__mqAsked.push(q);
  return {
    media: q,
    get matches() { return window.__phone; },
    addEventListener(t, fn) { window.__mqFns.push(fn); },
    removeEventListener(t, fn) { window.__mqFns = window.__mqFns.filter((f) => f !== fn); },
  };
};
window.__resize = (phone) => { window.__phone = phone; window.__mqFns.slice().forEach((fn) => fn()); };
window.__pushes = [];
window.__replaces = [];
window.__backs = 0;
window.__stack = [];
window.history = {
  state: null,
  pushState(s) { window.__stack.push(this.state); this.state = s; window.__pushes.push(s); },
  replaceState(s, t, url) { this.state = s; window.__replaces.push(url); if (url) window.location.hash = url; },
  back() { window.__backs += 1; this.state = window.__stack.length ? window.__stack.pop() : null; },
};
window.__acts = [];
// A recorder drops the click event an onClick hands it (the real actions
// ignore it too) and keeps what an action is called with.
const __rec = (name) => (...a) => {
  const args = a.filter((x) => !(x && typeof x === 'object' && 'target' in x));
  window.__acts.push([name].concat(args.map((x) => (x && typeof x === 'object' ? JSON.stringify(x) : x))));
};
const __item = (n, extra) => Object.assign({
  uid: 'u' + n, kind: 'library', trackId: n, title: 'Track ' + n, artist: 'Artist ' + n,
  album: '', src: '/a/' + n, coverUrl: null, durationSec: 200, seekable: true,
  cacheable: true, meta: {},
}, extra || {});
window.__makeP = (over) => {
  const queue = [__item(1), __item(2), __item(3)];
  return Object.assign({
    available: true, queue, index: 0, current: queue[0], status: 'playing',
    positionSec: 50, durationSec: 200, volume: 0.5, muted: false,
    target: { kind: 'browser' }, sleepRemainingSec: null,
    toggle: __rec('toggle'), next: __rec('next'), prev: __rec('prev'), seek: __rec('seek'),
    setVolume: __rec('setVolume'), toggleMute: __rec('toggleMute'),
    jumpTo: __rec('jumpTo'), removeAt: __rec('removeAt'), moveItem: __rec('moveItem'),
    clearQueue: __rec('clearQueue'),
    castTo: (t) => { __rec('castTo')(t); return Promise.resolve(); },
    offline: { supported: () => false },
  }, over || {});
};
"""

# Helpers every script starts with: class tests, and which width shows a
# control (an element hidden by itself or by a wrapper at that width).
HELPERS = r"""
const cls = (c) => (e) => String((e.props && e.props.className) || '').split(' ').includes(c);
const has = (e, c) => cls(c)(e);
const shownOn = (e, hideCls) => !has(e, hideCls) && !h.inside(e, cls(hideCls));
const iconOf = (e) => {
  const kids = [].concat((e.props && e.props.children) || []);
  const ic = kids.find((k) => k && typeof k === 'object' && k.props && k.props.name);
  return ic ? ic.props.name : null;
};
const W = () => h.global('window');
const sheet = () => h.find(cls('mp-sheet'));
const inSheet = (e) => h.inside(e, cls('mp-sheet'));
const bar = () => h.find(cls('mini-player'));
const inBar = (e) => h.inside(e, cls('mini-player'));
const controls = (pred) => h.findAll((e) => ['button', 'input', 'select'].includes(e.type) && pred(e))
  .map((e) => ({ type: e.type, icon: iconOf(e), title: e.props.title || null,
                 aria: e.props['aria-label'] || null, cls: String(e.props.className || ''),
                 minHeight: (e.props.style || {}).minHeight ?? null,
                 inSave: h.inside(e, cls('mp-q-save')), text: e.text,
                 phone: shownOn(e, 'mp-desk'), desk: shownOn(e, 'mp-phone') }));
const openSheet = () => h.click(cls('mp-expand'));
const focused = () => h.focused().map((e) => e.props['aria-label']
  || (has(e, 'mp-info') ? 'cover and title' : e.props.title));
// The listeners the components added (player.jsx's module-level hashchange
// listener, for #music/player, is there before anything renders).
const BASE = W().__listeners.length;
const added = () => W().__listeners.slice(BASE).map((l) => l.t);
const acts = () => W().__acts.slice();
"""


def _scenario(script: str, *, p: dict | None = None, api: dict | None = None,
              component: str = "MiniPlayer", files: list[str] | None = None,
              setup: str = "", refs: bool = True) -> dict:
    return {
        "files": files or FILES,
        "component": f"(PlaybackContext._value = window.__P, {component})",
        "api": api or {},
        "setup": PRELUDE + f" window.__P = window.__makeP({json.dumps(p or {})});" + setup,
        # focus() on a ref is recorded (h.focused()); off for a component
        # that draws through its ref (the Player tab's visualizer canvas).
        "refs": refs,
        "script": HELPERS + script,
    }


ROOMS = {"GET /api/music/now-playing": [{"room_id": "kitchen"}, {"room_id": "office"}]}
KITCHEN = {"target": {"kind": "room", "roomId": "kitchen"}}
# MilkDrop turned on in this browser, and a browser that has WebGL 2.
VIZ_ON = r"""
window.__store = { 'domovoi-viz-milkdrop': '1' };
localStorage = {
  getItem: (k) => (k in window.__store ? window.__store[k] : null),
  setItem: (k, v) => { window.__store[k] = String(v); },
  removeItem: (k) => { delete window.__store[k]; },
};
document.createElement = () => ({ getContext: () => ({ getExtension: () => null }), setAttribute() {}, style: {} });
"""

SCENARIOS = {
    # The bar: which control each width shows.
    "bar": _scenario(
        "h.render();"
        "const line = h.find(cls('mp-line'));"
        "return { bar: h.plain(bar()), barStyle: bar().props.style ?? null, controls: controls(inBar),"
        " line: { phone: shownOn(line, 'mp-desk'), desk: shownOn(line, 'mp-phone'),"
        "         hidden: line.props['aria-hidden'],"
        "         width: h.find((e) => e.type === 'span' && h.inside(e, cls('mp-line'))).props.style.width },"
        " title: h.find(cls('mp-title')).text, sub: h.find(cls('mp-sub')).text,"
        " sleep: h.find(cls('mp-sleep')) ? shownOn(h.find(cls('mp-sleep')), 'mp-desk') : null,"
        " seekPhone: shownOn(h.find(cls('mp-seek')), 'mp-desk'),"
        " sheet: !!sheet(), hash: W().location.hash };",
        p={"sleepRemainingSec": 90},
    ),
    "bar_remote": _scenario(
        "h.render(); return { sub: h.findAll((e) => h.inside(e, cls('mp-sub'))).map((e) => e.text),"
        " volume: controls(inBar).filter((c) => c.type === 'input').length };",
        p=KITCHEN,
    ),
    "bar_empty": _scenario("h.render(); return { tree: h.tree().length };",
                           p={"current": None, "queue": [], "index": -1}),
    # Opening, and every way of closing.
    "sheet_open_close": _scenario(
        "h.render(); await openSheet();"
        "const s = sheet();"
        "const opened = { plain: h.plain(s), pushes: W().__pushes.slice(), state: W().history.state,"
        " expanded: h.find(cls('mp-expand')).props['aria-expanded'],"
        " heads: h.findAll(cls('mp-sheet-sec-head')).map((e) => e.text || h.findAll((x) => x.type === 'span'"
        "   && h.inside(x, (a) => a === e)).map((x) => x.text).join('')),"
        " listeners: added().sort(), media: W().__mqFns.length,"
        " asked: W().__mqAsked.slice(), focused: focused() };"
        "await h.click((e) => e.type === 'button' && e.props['aria-label'] === 'close player');"
        "return { opened, closed: !sheet(), backs: W().__backs, state: W().history.state,"
        " listeners: added(), media: W().__mqFns.length,"
        " focused: focused() };",
    ),
    "sheet_back_gesture": _scenario(
        "h.render(); await openSheet();"
        "W().history.back(); W().__backs = 0;"   # what the browser does on the gesture...
        "W().__fire('popstate', { state: W().history.state }); h.rerender();"  # ...then tells the page
        "return { closed: !sheet(), backs: W().__backs, bar: !!bar(), focused: focused() };",
    ),
    # A tab away and back again leaves an older sheet's entry behind the
    # page; open the sheet there and back lands on that entry, which still
    # carries the sheet's state. It must close this sheet all the same.
    "sheet_back_onto_an_older_sheet_entry": _scenario(
        "h.render(); await openSheet();"
        "W().history.back(); W().__backs = 0;"
        "W().__fire('popstate', { state: W().history.state }); h.rerender();"
        "return { landedOn: W().history.state, closed: !sheet(), backs: W().__backs };",
        setup=" window.history.state = { domovoiPlayerSheet: true };",
    ),
    "sheet_route_change": _scenario(
        "h.render(); await openSheet();"
        "W().location.hash = '#calendar'; W().__fire('hashchange'); h.rerender();"
        "return { closed: !sheet(), backs: W().__backs, focused: focused() };",
    ),
    # A tab that navigates: by the time the tap reaches the document the
    # browser has pushed past the sheet's entry (and fired popstate for
    # the new hash).
    "sheet_strip_tab_to_another_page": _scenario(
        "h.render(); await openSheet();"
        "W().history.pushState(null); W().location.hash = '#calendar';"
        "W().__fire('popstate', { state: null }); W().__tap('.sidebar .nav-item'); h.rerender();"
        "return { closed: !sheet(), backs: W().__backs, state: W().history.state, focused: focused() };",
    ),
    # The tab already showing changes no hash: only the tap tells.
    "sheet_strip_tab_already_showing": _scenario(
        "h.render(); await openSheet();"
        "W().__tap('.topbar'); h.rerender(); const openAfterOther = !!sheet();"
        "W().__tap('.sidebar .nav-item'); h.rerender();"
        "return { openAfterOther, closed: !sheet(), backs: W().__backs, state: W().history.state,"
        " focused: focused() };",
    ),
    "sheet_escape": _scenario(
        "h.render(); await openSheet();"
        "W().__fire('keydown', { key: 'Enter' }); h.rerender(); const stillOpen = !!sheet();"
        "W().__fire('keydown', { key: 'Escape' }); h.rerender();"
        "return { stillOpen, closed: !sheet(), backs: W().__backs, focused: focused() };",
    ),
    # A phone turned sideways past 760px: the expanded player is the same
    # at every width, so it stays.
    "sheet_screen_grows_past_a_phone": _scenario(
        "h.render(); await openSheet();"
        "W().__resize(false); h.rerender();"
        "return { open: !!sheet(), backs: W().__backs, state: W().history.state,"
        " media: W().__mqFns.length };",
    ),
    # Desktop: the chevron and the cover-and-title button open the same
    # sheet; focus goes back to whichever opened it.
    "desktop_chevron_opens_the_sheet": _scenario(
        "W().__phone = false; h.render();"
        "await h.click((e) => e.type === 'button' && e.props.title === 'open full player');"
        "const opened = { sheet: !!sheet(), pushes: W().__pushes.slice(), hash: W().location.hash,"
        " expanded: h.find((e) => e.type === 'button' && e.props.title === 'open full player').props['aria-expanded'] };"
        "W().__fire('keydown', { key: 'Escape' }); h.rerender();"
        "return { opened, closed: !sheet(), backs: W().__backs, focused: focused() };",
    ),
    "cover_and_title_open_the_sheet": _scenario(
        "h.render(); const b = h.find(cls('mp-info'));"
        "const info = { type: b.type, haspopup: b.props['aria-haspopup'], title: b.props.title };"
        "await h.click(cls('mp-info')); const open = !!sheet();"
        "await h.click((e) => e.type === 'button' && e.props['aria-label'] === 'close player');"
        "return { info, open, closed: !sheet(), focused: focused() };",
    ),
    # Escape that something inside already took (the visualizer's preset
    # list), or while the visualizer is full screen, leaves it open.
    "sheet_escape_taken_or_full_screen": _scenario(
        "h.render(); await openSheet();"
        "W().__fire('keydown', { key: 'Escape', defaultPrevented: true }); h.rerender(); const taken = !!sheet();"
        "h.global('document').fullscreenElement = {};"
        "W().__fire('keydown', { key: 'Escape' }); h.rerender(); const fs = !!sheet();"
        "h.global('document').fullscreenElement = null;"
        "W().__fire('keydown', { key: 'Escape' }); h.rerender();"
        "return { taken, fs, closed: !sheet() };",
    ),
    # #music/player: the old Player tab's address opens the sheet over Music.
    "route_at_load_with_something_queued": _scenario(
        "h.render(); return { sheet: !!sheet(), hash: W().location.hash, replaces: W().__replaces.slice(),"
        " pushes: W().__pushes.slice() };",
        setup=" window.location.hash = '#music/player';",
    ),
    "route_at_load_with_nothing_queued": _scenario(
        "h.render(); const P = W().__P; P.current = P.queue[0]; h.rerender();"
        "return { sheet: !!sheet(), hash: W().location.hash, pushes: W().__pushes.slice() };",
        p={"current": None, "queue": [], "index": -1},
        setup=" window.location.hash = '#music/player/';",
    ),
    "route_link_while_closed_and_open": _scenario(
        "h.render(); W().location.hash = '#music'; W().__fire('hashchange'); h.rerender();"
        "const plain = !!sheet();"
        "W().location.hash = '#music/player'; W().__fire('hashchange'); h.rerender();"
        "const opened = { sheet: !!sheet(), hash: W().location.hash, pushes: W().__pushes.length };"
        # The same link again, while open over #music: it stays open.
        "W().location.hash = '#music/player'; W().__fire('hashchange'); h.rerender();"
        "const again = { sheet: !!sheet(), hash: W().location.hash, pushes: W().__pushes.length };"
        "const api = { open: W().openPlayerSheet(), shown: !!sheet() };"
        "return { plain, opened, again, api };",
    ),
    "open_player_sheet_with_nothing_queued": _scenario(
        "h.render(); return { opened: W().openPlayerSheet(), tree: h.tree().length };",
        p={"current": None, "queue": [], "index": -1},
    ),
    # A desktop window narrowed to a phone: the floating panels close.
    "desktop_panels_close_on_a_phone": _scenario(
        "W().__phone = false; h.render(); const idle = W().__mqFns.length;"
        "await h.click((e) => e.type === 'button' && e.props.title === 'queue');"
        "const queueOpen = !!h.find(cls('mp-q-save')); const media = W().__mqFns.length;"
        "W().__resize(false); h.rerender(); const stillOpen = !!h.find(cls('mp-q-save'));"
        "W().__resize(true); h.rerender(); const queueClosed = !h.find(cls('mp-q-save'));"
        "W().__phone = false;"
        "await h.click((e) => e.type === 'button' && e.props.title === 'cast target');"
        "const castOpen = h.text().includes('play on');"
        "W().__resize(true); h.rerender();"
        "return { idle, queueOpen, media, stillOpen, queueClosed, castOpen,"
        " castClosed: !h.text().includes('play on'), after: W().__mqFns.length };",
        api=ROOMS,
    ),
    "sheet_closes_when_the_queue_is_cleared": _scenario(
        "h.render(); await openSheet();"
        "await h.click((e) => e.type === 'button' && e.props.title === 'clear queue' && inSheet(e));"
        "const P = W().__P; P.current = null; P.queue = []; P.index = -1; h.rerender();"
        "return { acts: acts(), tree: h.tree().length, backs: W().__backs, state: W().history.state };",
    ),
    # Everything the sheet carries, and that it does what the bar did.
    "sheet_contents": _scenario(
        "h.render(); await openSheet(); await h.settle(); h.rerender();"
        "return { controls: controls(inSheet),"
        " name: h.find(cls('mp-sheet-name')).text, by: h.find(cls('mp-sheet-by')).text,"
        " times: h.findAll((e) => e.type === 'span' && h.inside(e, cls('mp-sheet-seek'))"
        "   && has(e, 'mono')).map((e) => e.text),"
        " rows: h.findAll(cls('mp-q-row')).map((e) => ({ cur: has(e, 'cur'), inSheet: inSheet(e),"
        "   list: h.inside(e, cls('mp-q-list')) })),"
        " pill: h.findAll((e) => e.type === 'span' && has(e, 'pill')).length,"
        " sleep: h.find(cls('mp-sheet-sleep')) ? h.find(cls('mp-sheet-sleep')).text : null,"
        " hookCalls: h.hookCalls };",
        api=ROOMS,
    ),
    "sheet_actions": _scenario(
        "h.render(); await openSheet(); await h.settle(); h.rerender();"
        "const b = (aria) => (e) => e.type === 'button' && e.props['aria-label'] === aria && inSheet(e);"
        "await h.click(b('previous')); await h.click(b('pause')); await h.click(b('next'));"
        "await h.fire(cls('mp-sheet-track'), 'onClick',"
        "  { currentTarget: { getBoundingClientRect: () => ({ left: 10, width: 200 }) }, clientX: 60 });"
        "await h.change((e) => e.type === 'input' && e.props['aria-label'] === 'volume', '0.25');"
        "await h.click(b('mute'));"
        "await h.click((e) => has(e, 'mp-q-main') && h.findAll(cls('mp-q-main')).indexOf(e) === 1);"
        "await h.click(b('remove Track 3'));"
        "await h.click((e) => e.type === 'button' && inSheet(e) && e.text.includes('office'));"
        "const openAfterCast = !!sheet();"
        "await h.type((e) => e.type === 'input' && e.props['aria-label'] === 'playlist name', 'road trip');"
        "await h.click((e) => e.type === 'button' && e.text === 'save' && inSheet(e));"
        "return { acts: acts(), openAfterCast, calls: h.calls };",
        api={**ROOMS, "POST /api/playlists": {"id": 7}},
    ),
    "sheet_remote": _scenario(
        "h.render(); await openSheet(); await h.settle(); h.rerender();"
        "const rows = h.findAll((e) => e.type === 'button' && inSheet(e) && (e.props.style || {}).minHeight === 48);"
        "return { pill: h.findAll((e) => e.type === 'span' && has(e, 'pill')).map((e) => e.text),"
        " volume: controls(inSheet).filter((c) => c.aria === 'volume').length,"
        " active: rows.filter((e) => e.props.style.background === 'var(--brand-soft)').map((e) => e.text) };",
        p=KITCHEN, api=ROOMS,
    ),
    "sheet_live_stream": _scenario(
        "h.render(); await openSheet();"
        "await h.fire(cls('mp-sheet-track'), 'onClick',"
        "  { currentTarget: { getBoundingClientRect: () => ({ left: 0, width: 100 }) }, clientX: 50 });"
        "return { acts: acts(), by: h.find(cls('mp-sheet-by')).text,"
        " times: h.findAll((e) => e.type === 'span' && h.inside(e, cls('mp-sheet-seek'))"
        "   && has(e, 'mono')).map((e) => e.text),"
        " cursor: h.find(cls('mp-sheet-track')).props.style.cursor };",
        p={"current": {"uid": "r1", "kind": "radio", "trackId": None, "title": "Night FM", "artist": "",
                       "src": "/r", "coverUrl": None, "durationSec": None, "seekable": False,
                       "cacheable": False, "meta": {}},
           "durationSec": 0, "positionSec": 12},
    ),
    "sheet_sleep_end": _scenario(
        "h.render(); await openSheet();"
        "return h.findAll((e) => h.inside(e, cls('mp-sheet-more'))).map((e) => e.text).filter(Boolean);",
        p={"sleepRemainingSec": -1, "eqBands": [0] * 10, "eqEnabled": False, "playbackRate": 1},
        files=FILES + ["web/static/music_player_panel.jsx"],
    ),
    # The desktop bar's own panels still work as they did.
    "desktop_panels": _scenario(
        "h.render();"
        "await h.click((e) => e.type === 'button' && e.props.title === 'queue');"
        "const q = { rows: h.findAll(cls('mp-q-row')).map((e) => has(e, 'cur')),"
        "  save: !!h.find(cls('mp-q-save')), header: h.text().filter((t) => t.startsWith('queue')) };"
        "await h.click((e) => e.type === 'button' && e.props.title === 'queue');"
        "await h.click((e) => e.type === 'button' && e.props.title === 'cast target');"
        "await h.settle(); h.rerender();"
        "const cast = { rows: h.findAll((e) => e.type === 'button' && ['This browser', 'kitchen', 'office']"
        "  .some((t) => e.text.includes(t))).map((e) => [e.text.trim(), (e.props.style || {}).minHeight ?? null]) };"
        "await h.click((e) => e.type === 'button' && e.text.includes('office'));"
        "const castClosed = !h.findAll((e) => e.type === 'button' && e.text.includes('office')).length;"
        "await h.click((e) => e.type === 'button' && e.props.title === 'open full player');"
        "return { q, cast, castClosed, acts: acts(), hash: W().location.hash, sheet: !!sheet() };",
        api=ROOMS,
    ),
    # The rooms read is still out: the sheet's "play on" says it is looking,
    # not that there are none.
    "sheet_rooms_loading": _scenario(
        "h.render(); await openSheet();"
        "return h.findAll((e) => e.type === 'div' && inSheet(e) && typeof e.text === 'string'"
        "  && /rooms/.test(e.text)).map((e) => e.text);",
        setup=" useApiList = () => ({ items: [], loading: true, error: null,"
              " refresh: () => Promise.resolve(), setItems: () => {} });",
    ),
    "sheet_no_rooms": _scenario(
        "h.render(); await openSheet(); await h.settle(); h.rerender();"
        "return h.findAll((e) => e.type === 'div' && inSheet(e) && typeof e.text === 'string'"
        "  && /rooms/.test(e.text)).map((e) => e.text);",
        api={"GET /api/music/now-playing": []},
    ),
    # <html> carries .mp-docked exactly while the bar shows.
    "docked_class": _scenario(
        "const list = () => h.global('document').documentElement.classList.list.slice();"
        "h.render(); const shown = list();"
        "const P = W().__P; P.current = null; P.queue = []; P.index = -1; h.rerender();"
        "return { shown, gone: list() };",
        setup=" document.documentElement.classList = { list: [],"
              " add(c) { this.list.push(c); }, remove(c) { this.list = this.list.filter((x) => x !== c); } };",
    ),
    # What a browser starts at, from what it stored (or nothing).
    "start_volume": _scenario(
        "return W().__vols;",
        component="(window.__vols = [null, '', '  ', '0', '0.4', ' 0.25 ', '1', '1.5', '-1', 'abc']"
                  ".map(_playerStartVolume), MiniPlayer)",
    ),
    # With music_player_panel.jsx (as in the browser): the Equalizer tab
    # first (no lyrics here), the sleep timer and offline, speed; every
    # control a 44px target on a phone.
    "sheet_full": _scenario(
        "h.render(); await openSheet(); await h.settle(); h.rerender();"
        "const tabs = h.findAll(cls('mp-tab')).map((e) => ({ text: e.text, on: e.props['aria-selected'],"
        "  role: e.props.role, tabIndex: e.props.tabIndex }));"
        "const panel = h.find(cls('mp-panel'));"
        "return { tabs, panel: { cls: panel.props.className, role: panel.props.role, id: panel.props.id,"
        "  by: panel.props['aria-labelledby'] }, presets: !!h.find(cls('np-eq-presets')),"
        " controls: controls(inSheet), sections: h.findAll((e) => e.type === 'section' && inSheet(e))"
        "  .map((e) => e.props['aria-label']) };",
        p={"eqBands": [0] * 10, "eqEnabled": False, "playbackRate": 1, "setPlaybackRate": None},
        files=FILES + ["web/static/music_player_panel.jsx"],
        setup=" window.__P.setPlaybackRate = __rec('setPlaybackRate'); window.__P.setSleep = __rec('setSleep');",
    ),
    "sheet_tabs_pick_and_keys": _scenario(
        "h.render(); await openSheet();"
        "await h.click((e) => has(e, 'mp-tab') && e.text === 'Hide');"
        "const hidden = { panel: !!h.find(cls('mp-panel')), on: h.find((e) => has(e, 'mp-tab') && e.props['aria-selected']).text };"
        "await h.key(cls('mp-tabs'), 'ArrowLeft');"
        "const left = h.find((e) => has(e, 'mp-tab') && e.props['aria-selected']).text;"
        "await h.key(cls('mp-tabs'), 'Home');"
        "const home = h.find((e) => has(e, 'mp-tab') && e.props['aria-selected']).text;"
        "await h.key(cls('mp-tabs'), 'End');"
        "const end = h.find((e) => has(e, 'mp-tab') && e.props['aria-selected']).text;"
        "await h.click((e) => has(e, 'mp-tab') && e.text === 'Queue');"
        "const queue = { rows: h.findAll(cls('mp-q-row')).length, head: !!h.find(cls('mp-sheet-sec-head')) };"
        "return { hidden, left, home, end, queue };",
        p={"eqBands": [0] * 10, "eqEnabled": False, "playbackRate": 1},
        files=FILES + ["web/static/music_player_panel.jsx"],
    ),
    # While casting, previous is the room's (2026-10-01): every previous
    # button stays live and calls the player's prev, which posts the room's
    # /api/music/previous. They were disabled while previous could only
    # move the room forward (or do nothing).
    "casting_previous_buttons": _scenario(
        "h.render(); const prevIn = (where) => (e) => e.type === 'button'"
        " && e.props['aria-label'] === 'previous' && where(e);"
        "const inBarBtns = h.findAll(prevIn(inBar)).map((e) => !!e.props.disabled);"
        "await h.click(prevIn(inBar)); await openSheet();"
        "const inSheetBtns = h.findAll(prevIn(inSheet)).map((e) => !!e.props.disabled);"
        "await h.click(prevIn(inSheet));"
        "return { bar: inBarBtns, sheet: inSheetBtns, acts: acts() };",
        p=KITCHEN, api=ROOMS,
    ),
    # The visualizer as the panel's backdrop (visualizer.jsx). Node has no
    # WebGL 2 unless a probe canvas says so; refs off (the stage measures
    # itself through its ref).
    "viz_backdrop": _scenario(
        "h.render(); await openSheet(); await h.settle(); h.rerender();"
        "const over = h.find(cls('viz-over'));"
        "return { stage: String(h.find(cls('viz-stage')).props.className),"
        " opacity: over ? over.props.style.opacity : null,"
        " panelInOver: !!h.find((e) => has(e, 'mp-panel') && h.inside(e, cls('viz-over'))),"
        " tabsInHead: !!h.find((e) => has(e, 'mp-tabs') && h.inside(e, cls('viz-head'))),"
        " halves: h.findAll(cls('viz-half')).length, waiting: h.find(cls('viz-name')).text };",
        p={"eqBands": [0] * 10, "eqEnabled": False, "playbackRate": 1},
        files=FILES + ["web/static/music_player_panel.jsx", "web/static/visualizer.jsx"],
        setup=VIZ_ON + " window.__store['domovoi-viz-panel-opacity'] = '0.5';", refs=False,
    ),
    "viz_backdrop_hidden": _scenario(
        "h.render(); await openSheet();"
        "await h.click((e) => has(e, 'mp-tab') && e.text === 'Hide');"
        "return { over: !!h.find(cls('viz-over')), panel: !!h.find(cls('mp-panel')),"
        " stage: !!h.find(cls('viz-stage')) };",
        p={"eqBands": [0] * 10, "eqEnabled": False, "playbackRate": 1},
        files=FILES + ["web/static/music_player_panel.jsx", "web/static/visualizer.jsx"],
        setup=VIZ_ON, refs=False,
    ),
    # Off, or casting: the panel is a plain card, the honest line stays.
    "viz_off_plain_card": _scenario(
        "h.render(); await openSheet();"
        "return { solid: !!h.find((e) => has(e, 'mp-panel') && h.inside(e, cls('viz-solid'))),"
        " over: !!h.find(cls('viz-over')), bars: !!h.find(cls('viz-bars')) };",
        p={"eqBands": [0] * 10, "eqEnabled": False, "playbackRate": 1},
        files=FILES + ["web/static/music_player_panel.jsx", "web/static/visualizer.jsx"], refs=False,
    ),
    "viz_casting_plain_card": _scenario(
        "h.render(); await openSheet(); await h.settle(); h.rerender();"
        "return { solid: !!h.find((e) => has(e, 'mp-panel') && h.inside(e, cls('viz-solid'))),"
        " over: !!h.find(cls('viz-over')), note: h.find(cls('viz-note')).text,"
        " tabs: h.findAll(cls('mp-tab')).map((e) => e.text) };",
        p={"eqBands": [0] * 10, "eqEnabled": False, "playbackRate": 1, **KITCHEN},
        files=FILES + ["web/static/music_player_panel.jsx", "web/static/visualizer.jsx"],
        setup=VIZ_ON, refs=False, api=ROOMS,
    ),
    "viz_opacity_values": _scenario(
        "return W().__vals;",
        component="(window.__vals = [null, '', 'abc', '0', '0.1', '0.3', '0.55', '0.8', '1', '1.5']"
                  ".map(vizPanelOpacity), MiniPlayer)",
        files=FILES + ["web/static/music_player_panel.jsx", "web/static/visualizer.jsx"],
    ),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    # The file form: every scenario carries the prelude, past Windows'
    # command-line limit.
    spec = tmp_path_factory.mktemp("player") / "scenarios.json"
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


def _src(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _block(css: str, start: int) -> str:
    depth = 0
    for i in range(start, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start:i + 1]
    raise AssertionError("unterminated block")


def _player_css() -> tuple[str, str]:
    """(the docked bar's rules outside any media block, its 760px block)."""
    css = _src("styles.css")
    start = css.index("Docked player (player.jsx")
    end = css.index("The expanded player (player.jsx PlayerSheet)")
    section = css[start:end]
    phone = _block(section, section.index("@media (max-width: 760px) {"))
    return section.replace(phone, ""), phone


def _sheet_css() -> tuple[str, str, str]:
    """The expanded player's rules: (outside any media block, its 1100px
    block, its 760px block)."""
    css = _src("styles.css")
    start = css.index("The expanded player (player.jsx PlayerSheet)")
    section = css[start:css.index("The visualizer (visualizer.jsx)")]
    one = _block(section, section.index("@media (max-width: 1100px) {"))
    phone = _block(section, section.index("@media (max-width: 760px) {"))
    return section.replace(one, "").replace(phone, ""), one, phone


def _rule(css: str, selector: str) -> str:
    m = re.search(r"(?:^|\n)\s*" + re.escape(selector) + r" \{([^}]*)\}", css)
    assert m, f"no rule for {selector}"
    return " ".join(m.group(1).split())


def _px(decl: str, prop: str) -> list[int]:
    """The lengths in one declaration, in order (a bare 0 counts)."""
    m = re.search(rf"(?:^|;|\s){re.escape(prop)}: ([^;]+);", " " + decl)
    assert m, f"no {prop} in {decl}"
    return [int(t[:-2] if t.endswith("px") else t) for t in m.group(1).split()
            if re.fullmatch(r"\d+(px)?", t)]


# ─── the bar ─────────────────────────────────────────────────────────────


def test_the_phone_bar_is_cover_title_play_next_and_open(driven) -> None:
    bar = driven["bar"]
    phone = [(c["icon"], c["aria"] or c["title"]) for c in bar["controls"] if c["phone"]]
    # The cover and title are one button ("open player") at every width.
    assert phone == [(None, "open player"), ("pause", "pause"), ("skip-forward", "next"),
                     ("chevron-up", "open player"), ("x", "Stop and close")], phone
    # Title and artist stay in the row (ellipsized in CSS); the seek bar,
    # the sleep countdown and the rest are desktop-only.
    assert bar["title"] == "Track 1" and bar["sub"] == "Artist 1"
    assert bar["seekPhone"] is False and bar["sleep"] is False
    # ...and the phone keeps the position as a line on the bar's top edge.
    assert bar["line"] == {"phone": True, "desk": False, "hidden": "true", "width": "25%"}
    assert bar["sheet"] is False


def test_the_desktop_bar_keeps_every_control_it_had(driven) -> None:
    desk = [(c["icon"] or c["type"], c["title"]) for c in driven["bar"]["controls"] if c["desk"]]
    assert desk == [
        ("button", "open player"),
        ("skip-back", "previous"), ("pause", "pause"), ("skip-forward", "next"),
        ("volume-2", None), ("input", None), ("list-music", "queue"),
        ("monitor-speaker", "cast target"), ("chevron-up", "open full player"),
        ("x", "Stop and close"),
    ], desk
    # One element per control: nothing the phone shows is a second copy of
    # a desktop control, except the phone's own "open player".
    both = [c["icon"] for c in driven["bar"]["controls"] if c["desk"] and c["phone"]]
    assert both == [None, "pause", "skip-forward", "x"]


def test_every_phone_bar_control_is_a_44px_target(driven) -> None:
    _, phone = _player_css()
    assert _px(_rule(phone, ".mini-player .btn-icon"), "width") == [44]
    assert _px(_rule(phone, ".mini-player .btn-icon"), "height") == [44]
    for c in driven["bar"]["controls"]:
        if c["phone"] and c["cls"] != "mp-info":
            assert "btn-icon" in c["cls"].split(), c
    # The cover-and-title button is as tall as its 44px cover, and wider.
    assert 'className="mp-info"' in _src("player.jsx")
    assert "<CoverTile item={it} size={44}/>" in _src("player.jsx")


def test_the_cover_and_title_are_a_plain_button() -> None:
    desktop, _ = _player_css()
    r = _rule(desktop, "button.mp-info")
    for want in ("font: inherit;", "color: inherit;", "background: none;", "border: 0;", "padding: 0;",
                 "cursor: pointer;", "max-width: 100%;"):
        assert want in r, want
    # Inside a button only phrasing content: the title lines are spans.
    assert "display: block; min-width: 0;" in _rule(desktop, ".mp-text")
    assert "display: block;" in _rule(desktop, ".mp-title, .mp-sub")


def test_the_bar_is_styled_in_css_so_a_phone_can_restyle_it(driven) -> None:
    """Inline styles would outrank the 760px rules."""
    assert driven["bar"]["barStyle"] is None
    assert driven["bar"]["bar"]["props"]["className"] == "mini-player"
    player = _src("player.jsx")
    bar_jsx = player[player.index("const MiniPlayer = () =>"):player.index("const PlayerSheet =")]
    for c in ("mp-info", "mp-center", "mp-transport", "mp-seek", "mp-side", "mp-side-desk", "mp-vol"):
        assert re.search(rf'className="[^"]*\b{c}\b[^"]*"(?! style)', bar_jsx), c
    assert "gridTemplateColumns" not in bar_jsx


def test_a_room_cast_shows_the_room_and_no_volume(driven) -> None:
    assert "◆ kitchen · " in driven["bar_remote"]["sub"]
    assert driven["bar_remote"]["volume"] == 0


def test_no_bar_without_something_queued(driven) -> None:
    assert driven["bar_empty"]["tree"] == 0


# ─── the widths the bar was designed for ─────────────────────────────────


@pytest.mark.parametrize(("width", "title_text"), [(320, 66), (375, 121), (414, 160), (760, 506)])
def test_the_phone_bar_leaves_the_title_room_at_each_width(width, title_text) -> None:
    """The bar's fixed parts, read from the CSS: padding, the gaps between
    its three columns, play + next, "open player", "Stop and close" — then the 44px cover and
    its 10px gap. What's left is the title's text, ellipsized."""
    desktop, phone = _player_css()
    bar = _rule(phone, ".mini-player")
    assert "grid-template-columns: minmax(0, 1fr) auto auto;" in bar
    _, right, _, left = _px(bar, "padding")
    (gap,) = _px(bar, "gap")
    (target,) = _px(_rule(phone, ".mini-player .btn-icon"), "width")
    fixed = left + right + 2 * gap + 4 * target
    assert fixed == 200
    (cover_gap,) = _px(_rule(desktop, ".mp-info"), "gap")
    assert width - fixed - 44 - cover_gap == title_text
    assert f"{title_text}" in desktop     # the section comment states them


def test_nothing_in_the_bar_can_push_it_wider_than_the_screen() -> None:
    desktop, phone = _player_css()
    for sel in (".mp-info", ".mp-text"):
        assert "min-width: 0" in _rule(desktop, sel)
    for sel in (".mp-title", ".mp-sub"):
        r = _rule(desktop, sel)
        assert "text-overflow: ellipsis" in r and "white-space: nowrap" in r and "overflow: hidden" in r
    # The desktop centre column's 280px minimum is what overflowed a phone.
    assert "min-width: 280px" in _rule(desktop, ".mp-center")
    assert "min-width: 0" in _rule(phone, ".mini-player .mp-center")


def test_each_width_hides_the_others_controls() -> None:
    desktop, phone = _player_css()
    assert ".mini-player .mp-phone { display: none; }" in desktop
    assert ".mini-player .mp-desk { display: none; }" in phone
    assert ".mini-player .mp-desk" not in desktop
    # The phone's own controls come back after the desktop rule hid them
    # (same specificity; the media block is later in the file).
    assert ".mini-player .mp-line { display: block; }" in phone
    assert ".mini-player .mp-expand { display: inline-flex; }" in phone
    # The desktop bar is the one it always was.
    bar = _rule(desktop, ".mini-player")
    assert "grid-template-columns: 1fr auto 1fr;" in bar and "height: 64px;" in bar
    assert "gap: 12px; padding: 8px 14px;" in bar


# ─── the sheet ───────────────────────────────────────────────────────────


def test_open_player_opens_the_sheet_as_a_history_entry(driven) -> None:
    o = driven["sheet_open_close"]["opened"]
    assert o["plain"]["props"]["role"] == "dialog"
    # A modal one: Tab stays inside (the sidebar and strip still take a
    # tap, which closes it).
    assert o["plain"]["props"]["aria-modal"] == "true"
    assert o["pushes"] == [{"domovoiPlayerSheet": True}]
    assert o["state"] == {"domovoiPlayerSheet": True}
    assert o["expanded"] is True
    # Only the queue is in the panel here (no lyrics.jsx, no equalizer).
    assert sorted(o["heads"]) == ["play on", "queue · 3"]
    assert o["listeners"] == ["document:click", "hashchange", "keydown", "popstate"]
    # It no longer watches the width: it is the same sheet at every width.
    assert o["media"] == 0 and o["asked"] == []
    # Focus lands on its close button.
    assert o["focused"] == ["close player"]


def test_the_close_chevron_pops_the_entry_it_pushed(driven) -> None:
    c = driven["sheet_open_close"]
    assert c["closed"] is True
    assert c["backs"] == 1 and c["state"] is None
    assert c["listeners"] == [] and c["media"] == 0
    # ...and goes back to the button that opened it.
    assert c["focused"] == ["close player", "open player"]


def test_the_back_gesture_closes_the_sheet_and_nothing_more(driven) -> None:
    """The browser has already gone back; the sheet must not go back again
    (that would leave the page it was opened over)."""
    g = driven["sheet_back_gesture"]
    assert g == {"closed": True, "backs": 0, "bar": True, "focused": ["close player", "open player"]}


def test_back_onto_an_older_sheet_entry_still_closes_the_sheet(driven) -> None:
    b = driven["sheet_back_onto_an_older_sheet_entry"]
    assert b == {"landedOn": {"domovoiPlayerSheet": True}, "closed": True, "backs": 0}


def test_a_tab_in_the_strip_closes_the_sheet_without_going_back(driven) -> None:
    # Focus stays with the page the tab brought up.
    assert driven["sheet_route_change"] == {"closed": True, "backs": 0, "focused": ["close player"]}
    t = driven["sheet_strip_tab_to_another_page"]
    assert t == {"closed": True, "backs": 0, "state": None, "focused": ["close player"]}


def test_the_tab_already_showing_closes_the_sheet_and_pops_its_entry(driven) -> None:
    t = driven["sheet_strip_tab_already_showing"]
    # A tap anywhere else leaves it open.
    assert t["openAfterOther"] is True
    assert t["closed"] is True and t["backs"] == 1 and t["state"] is None
    assert t["focused"] == ["close player"]


def test_escape_closes_the_sheet(driven) -> None:
    assert driven["sheet_escape"] == {"stillOpen": True, "closed": True, "backs": 1,
                                      "focused": ["close player", "open player"]}


def test_a_screen_grown_past_a_phone_keeps_the_sheet(driven) -> None:
    """One expanded player for every width (2026-11-12): a phone turned
    sideways keeps it open, on its history entry."""
    g = driven["sheet_screen_grows_past_a_phone"]
    assert g == {"open": True, "backs": 0, "state": {"domovoiPlayerSheet": True}, "media": 0}


def test_the_desktop_chevron_opens_the_same_sheet(driven) -> None:
    d = driven["desktop_chevron_opens_the_sheet"]
    # Over this page: no address of its own, a history entry for Back.
    assert d["opened"] == {"sheet": True, "pushes": [{"domovoiPlayerSheet": True}], "hash": "",
                           "expanded": True}
    assert d["closed"] is True and d["backs"] == 1
    assert d["focused"] == ["close player", "open full player"]


def test_the_cover_and_title_open_it_and_take_the_focus_back(driven) -> None:
    c = driven["cover_and_title_open_the_sheet"]
    assert c["info"] == {"type": "button", "haspopup": "dialog", "title": "open player"}
    assert c["open"] is True and c["closed"] is True
    assert c["focused"] == ["close player", "cover and title"]


def test_escape_taken_inside_or_in_full_screen_leaves_it_open(driven) -> None:
    assert driven["sheet_escape_taken_or_full_screen"] == {"taken": True, "fs": True, "closed": True}


def test_a_desktop_narrowed_to_a_phone_closes_the_floating_panels(driven) -> None:
    d = driven["desktop_panels_close_on_a_phone"]
    # Only an open panel listens.
    assert d["idle"] == 0 and d["media"] == 1
    assert d["queueOpen"] is True and d["stillOpen"] is True and d["queueClosed"] is True
    assert d["castOpen"] is True and d["castClosed"] is True
    assert d["after"] == 0


def test_clearing_the_queue_closes_the_sheet(driven) -> None:
    c = driven["sheet_closes_when_the_queue_is_cleared"]
    assert c["acts"] == [["clearQueue"]]
    assert c["tree"] == 0 and c["backs"] == 1 and c["state"] is None


def test_the_sheet_carries_everything_the_bar_left_out(driven) -> None:
    s = driven["sheet_contents"]
    labels = [c["aria"] for c in s["controls"] if c["aria"]]
    for want in ("close player", "previous", "pause", "next", "mute", "volume",
                 "clear queue", "remove Track 1", "remove Track 2", "remove Track 3", "playlist name"):
        assert want in labels, (want, labels)
    assert s["name"] == "Track 1" and s["by"] == "Artist 1"
    assert s["times"] == ["0:50", "3:20"]
    assert s["rows"] == [{"cur": True, "inSheet": True, "list": True},
                         {"cur": False, "inSheet": True, "list": True},
                         {"cur": False, "inSheet": True, "list": True}]
    cast = [c["text"].strip() for c in s["controls"] if c["minHeight"] == 48]
    assert cast == ["This browser", "kitchen", "office"]
    assert "/api/music/now-playing" in s["hookCalls"]
    assert s["pill"] == 0 and s["sleep"] is None


def _big_on_a_phone(c: dict) -> bool:
    k = c["cls"].split()
    return ("btn-icon" in k or c["minHeight"] == 48 or "mp-tab" in k or "np-chip" in k
            or "mp-sheet-speed" in k or (c["inSave"] and "btn" in k) or "btn" in k)


def test_every_sheet_control_is_a_44px_target(driven) -> None:
    base, _, phone = _sheet_css()
    assert _px(_rule(phone, ".mp-sheet .btn-icon"), "width") == [44]
    assert _px(_rule(base, ".mp-sheet .mp-sheet-play"), "width") == [56]
    assert _px(_rule(base, ".mp-sheet .mp-q-save .btn"), "height") == [44]
    assert _px(_rule(base, ".mp-sheet .mp-q-save input"), "height") == [44]
    assert _px(_rule(phone, '.mp-sheet-vol input[type="range"]'), "height") == [44]
    assert _px(_rule(base, ".mp-sheet-track"), "height") == [44]
    assert _px(_rule(base, ".mp-sheet .mp-q-row"), "min-height") == [48]
    assert _px(_rule(phone, ".mp-tab"), "min-height") == [44]
    assert _px(_rule(phone, ".mp-sheet .np-chip"), "min-height") == [44]
    assert _px(_rule(phone, ".mp-sheet .mp-sheet-more .btn"), "height") == [44]
    assert _px(_rule(phone, ".mp-sheet .mp-sheet-speed"), "height") == [44]
    assert "height: 44px !important;" in _rule(phone, ".mp-sheet .mp-sheet-more select, .mp-sheet .mp-panel select")
    for c in driven["sheet_contents"]["controls"] + driven["sheet_full"]["controls"]:
        if c["type"] in ("input", "select"):
            continue
        assert _big_on_a_phone(c), c


def test_the_sheet_does_what_the_bar_and_its_panels_did(driven) -> None:
    a = driven["sheet_actions"]
    assert a["acts"] == [
        ["prev"], ["toggle"], ["next"],
        ["seek", 50],                         # (60 - 10) / 200 of 200 s
        ["setVolume", 0.25], ["toggleMute"],
        ["jumpTo", 1], ["removeAt", 2],
        ["castTo", '{"kind":"room","roomId":"office"}'],
    ]
    # Picking a room keeps the sheet open: it shows the new target.
    assert a["openAfterCast"] is True
    posts = [(c["method"], c["path"], c["body"]) for c in a["calls"] if c["method"] == "POST"]
    assert posts[0] == ("POST", "/api/playlists", {"name": "road trip"})
    assert [p[1] for p in posts[1:]] == ["/api/playlists/7/tracks"] * 3


def test_a_room_cast_in_the_sheet(driven) -> None:
    r = driven["sheet_remote"]
    assert r["pill"] == ["casting to kitchen"]
    assert r["volume"] == 0
    assert [t.strip() for t in r["active"]] == ["kitchen"]


def test_a_live_stream_in_the_sheet_does_not_seek(driven) -> None:
    lv = driven["sheet_live_stream"]
    assert lv["acts"] == []
    assert lv["by"] == "live stream"
    assert lv["times"] == ["0:12", "live"]
    assert lv["cursor"] == "default"


def test_the_sleep_timer_and_offline_are_in_the_sheet(driven) -> None:
    texts = driven["sheet_sleep_end"]
    assert texts[:6] == ["sleep", "15m", "30m", "45m", "60m", "end of track"]
    assert "ends after track" in texts and "offline cache unavailable" in texts


def test_the_sheet_covers_all_but_the_navigation_at_every_width() -> None:
    base, one, phone = _sheet_css()
    desktop, _ = _player_css()
    sheet = _rule(base, ".mp-sheet")
    # Desktop: right of the sidebar, top to bottom (topbar and bar under it).
    assert "position: fixed; left: var(--sidebar-w); right: 0; top: 0; bottom: 0; z-index: 48;" in sheet
    assert "display: flex;" in sheet and "background: var(--surface);" in sheet
    # Phone: the screen above the tab strip.
    assert "left: 0; bottom: var(--dock-bottom, 0px);" in _rule(phone, ".mp-sheet")
    # No longer hidden at any width.
    assert "display: none" not in sheet
    # Above the bar (45) and its floating panels (46/47), below drawers (60).
    assert "z-index: 45;" in _rule(desktop, ".mini-player")
    assert re.search(r"^\s+48\s+the expanded player \(PlayerSheet\)$", _src("styles.css"), re.M)
    # Its body scrolls; nothing in it widens the screen.
    body = _rule(base, ".mp-sheet-body")
    assert "overflow-y: auto;" in body and "min-height: 0;" in body
    assert "min-width: 0;" in _rule(base, ".mp-sheet-grid > *")
    for sel in (".mp-sheet-seek", ".mp-sheet-vol", ".mp-sheet .mp-q-row"):
        assert "minmax(0, 1fr)" in _rule(base, sel), sel
    assert "min-width: 0;" in _rule(desktop, ".mp-q-save input")
    # Wide: player | stage; narrower: one column, the stage under the transport.
    grid = _rule(base, ".mp-sheet-grid")
    assert "grid-template-columns: 320px minmax(0, 1fr);" in grid
    assert 'grid-template-areas: "now stage" "more stage" "on stage";' in grid
    assert 'grid-template-areas: "now" "stage" "more" "on";' in _rule(one, ".mp-sheet-grid")
    # The phone's swipe handle on the header.
    assert "touch-action: none;" in _rule(base, ".mp-sheet-head")
    # The bar hides under it while it is open (its left end would show
    # under the sidebar on a desktop), and is back as it shrinks.
    assert "visibility: hidden;" in _rule(desktop, ".mini-player.under-sheet")
    assert "display: block;" in _rule(phone, ".mp-sheet-grip")


def test_the_sheet_grows_out_of_the_bar_and_back() -> None:
    """The motion, in player.jsx: the sheet's clip-path runs from the bar's
    box to its own (280 ms) and back (240 ms) on the house ease, never past
    300 ms (docs/design/README.md); prefers-reduced-motion gets a 120 ms
    fade and no growth."""
    src = _src("player.jsx")
    assert "const _MP_OPEN_MS = 280;" in src and "const _MP_CLOSE_MS = 240;" in src
    assert "const _MP_FADE_MS = 120;" in src
    assert "const _MP_EASE = 'cubic-bezier(0.2, 0, 0, 1)';" in src
    assert "_mpClipTo(s, f) }, { clipPath: _MP_CLIP_FULL }" in src
    assert "{ clipPath: _MP_CLIP_FULL }, { clipPath: _mpClipTo(s, f) }" in src
    assert src.count("if (_mpReducedMotion())") == 2
    assert "matchMedia('(prefers-reduced-motion: reduce)')" in src
    # Unmounted only after the shrink (or at once without Web Animations,
    # or after a timeout in a hidden tab where it never runs).
    assert "anim.onfinish = finish;" in src and "setTimeout(finish, _MP_CLOSE_MS + 400)" in src
    # No CSS keyframe of its own any more.
    assert "@keyframes mp-sheet-in" not in _src("styles.css")


# ─── the desktop panels, unchanged ───────────────────────────────────────


def test_the_desktop_queue_and_cast_panels_still_work(driven) -> None:
    d = driven["desktop_panels"]
    assert d["q"] == {"rows": [True, False, False], "save": True, "header": ["queue · 3"]}
    # Desktop cast rows keep their size (no 48px minimum).
    assert d["cast"]["rows"] == [["This browser", None], ["kitchen", None], ["office", None]]
    assert d["castClosed"] is True          # a successful pick closes the menu
    assert d["acts"] == [["castTo", '{"kind":"room","roomId":"office"}']]
    # "open full player" opens the expanded player over this page.
    assert d["hash"] == "" and d["sheet"] is True
    player = _src("player.jsx")
    # The floating panels move with the bar, as before.
    assert player.count("bottom: 'calc(var(--dock-bottom, 0px) + 76px)'") == 2


def test_the_sheet_says_it_is_looking_for_rooms_until_they_answer(driven) -> None:
    # The rooms read asks every room's MPD (1.6 s on the live check), and
    # the sheet mounts the list afresh on every open.
    assert driven["sheet_rooms_loading"] == ["looking for rooms…"]
    assert driven["sheet_no_rooms"] == ["no rooms online — set up a room"]


def test_html_is_marked_docked_only_while_the_bar_shows(driven) -> None:
    assert driven["docked_class"] == {"shown": ["mp-docked"], "gone": []}


def test_a_new_browser_starts_at_full_volume(driven) -> None:
    """``Number(null)`` is 0: every new browser started silent and stored
    that 0, and a phone's bar has no volume control to notice it by."""
    #                            null '' '  ' '0'  '0.4' ' 0.25 ' '1' '1.5' '-1' 'abc'
    assert driven["start_volume"] == [1, 1, 1, 1, 0.4, 0.25, 1, 1, 1, 1]
    assert "_playerStartVolume(localStorage.getItem('domovoi-player-volume'))" in _src("player.jsx")


def test_the_bar_volume_slider_is_amber() -> None:
    # The bar lives outside .main, so `.main input[type="range"]` missed it.
    desktop, _ = _player_css()
    assert "accent-color: var(--brand);" in _rule(desktop, '.mini-player input[type="range"]')


# ─── #music/player, the old Player tab's address ─────────────────────────


def test_music_player_at_load_opens_the_sheet_over_music(driven) -> None:
    d = driven["route_at_load_with_something_queued"]
    # Rewritten in place (no extra entry), then the sheet's own entry on top.
    assert d == {"sheet": True, "hash": "#music", "replaces": ["#music"],
                 "pushes": [{"domovoiPlayerSheet": True}]}


def test_music_player_at_load_with_nothing_queued_lands_on_music(driven) -> None:
    # A trailing slash is the same address; nothing pops up later when
    # something is queued.
    assert driven["route_at_load_with_nothing_queued"] == {"sheet": False, "hash": "#music", "pushes": []}


def test_a_link_to_music_player_opens_it_and_leaves_an_open_one_open(driven) -> None:
    d = driven["route_link_while_closed_and_open"]
    assert d["plain"] is False
    assert d["opened"] == {"sheet": True, "hash": "#music", "pushes": 1}
    assert d["again"] == {"sheet": True, "hash": "#music", "pushes": 1}
    assert d["api"] == {"open": True, "shown": True}
    assert driven["open_player_sheet_with_nothing_queued"] == {"opened": False, "tree": 0}


def test_the_music_page_has_no_player_tab() -> None:
    music = _src("music.jsx")
    tabs = music[music.index("const MUSIC_PAGE_TABS = ["):music.index("];", music.index("const MUSIC_PAGE_TABS = ["))]
    assert "{ id: 'player' }" not in tabs and "'library'" in tabs and "'queue'" in tabs
    assert "NowPlayingPanel" not in music
    assert "const NowPlayingPanel" not in _src("music_player_panel.jsx")
    assert "label: 'Player'" not in music
    # The listener that rewrites the address is added as player.jsx loads,
    # before the shell's and the pages' own hashchange listeners.
    player = _src("player.jsx")
    assert "const PLAYER_SHEET_ROUTE = 'music/player';" in player
    assert "window.history.replaceState(window.history.state, '', '#music')" in player
    assert "\nconst openPlayerSheet = () => {" in player


# ─── the panel: tabs over the visualizer ─────────────────────────────────


def test_the_panel_is_a_tab_set_equalizer_first_without_lyrics(driven) -> None:
    f = driven["sheet_full"]
    assert f["tabs"] == [{"text": "Equalizer", "on": True, "role": "tab", "tabIndex": 0},
                         {"text": "Queue", "on": False, "role": "tab", "tabIndex": -1},
                         {"text": "Hide", "on": False, "role": "tab", "tabIndex": -1}]
    assert f["panel"] == {"cls": "mp-panel mp-panel-eq", "role": "tabpanel", "id": "mp-tabpanel",
                          "by": "mp-tab-eq"}
    assert f["presets"] is True
    assert f["sections"] == ["more", "play on"]
    # Speed, for music played here (spoken audio has its own).
    assert any(c["type"] == "select" and c["aria"] == "speed" for c in f["controls"])


def test_hide_and_the_arrow_keys(driven) -> None:
    t = driven["sheet_tabs_pick_and_keys"]
    assert t["hidden"] == {"panel": False, "on": "Hide"}
    assert (t["left"], t["home"], t["end"]) == ("Queue", "Equalizer", "Hide")
    assert t["queue"] == {"rows": 3, "head": True}


def test_the_visualizer_is_the_panels_backdrop(driven) -> None:
    v = driven["viz_backdrop"]
    assert v["stage"] == "viz-stage with-panel"
    # The stored 50 % from the visualizer's slider.
    assert v["opacity"] == 0.5
    assert v["panelInOver"] is True and v["tabsInHead"] is True
    # No clickable halves any more; the waiting line is in the name chip.
    assert v["halves"] == 0
    assert v["waiting"] == "starts when music plays in this browser"
    # Hide: the picture alone.
    assert driven["viz_backdrop_hidden"] == {"over": False, "panel": False, "stage": True}


def test_off_or_casting_the_panel_is_a_plain_card(driven) -> None:
    assert driven["viz_off_plain_card"] == {"solid": True, "over": False, "bars": True}
    c = driven["viz_casting_plain_card"]
    assert c["solid"] is True and c["over"] is False
    assert c["note"] == "visualizer shows when playing in this browser"
    # No equalizer while a room plays it.
    assert c["tabs"] == ["Queue", "Hide"]


def test_the_panel_opacity_is_kept_between_30_and_100_percent(driven) -> None:
    #                                  null  ''   abc  '0'  .1   .3   .55   .8   1   1.5
    assert driven["viz_opacity_values"] == [0.8, 0.8, 0.8, 0.3, 0.3, 0.3, 0.55, 0.8, 1, 1]
    viz = _src("visualizer.jsx")
    assert "const VIZ_PANEL_KEY = 'domovoi-viz-panel-opacity';" in viz
    assert 'aria-label="panel opacity"' in viz and "min={VIZ_PANEL_MIN * 100} max={100}" in viz
    # Previous / next preset are buttons in the bar; the arrow keys only
    # while the picture itself has the focus.
    assert 'aria-label="previous preset"' in viz and 'aria-label="next preset"' in viz
    assert "viz-half" not in viz and "viz-half" not in _src("styles.css")
    assert "if (e.target !== stageRef.current) return;" in viz
    # Full screen shows the picture alone.
    assert "const showPanel = panel != null && !isFs;" in viz


# ─── previous while casting ──────────────────────────────────────────────


def test_every_previous_button_works_while_casting(driven) -> None:
    out = driven["casting_previous_buttons"]
    assert out["bar"] == [False] and out["sheet"] == [False], out
    assert out["acts"] == [["prev"], ["prev"]], out["acts"]


def test_stop_and_close_saves_stops_the_room_then_clears() -> None:
    """Order matters: the spoken position is saved before anything is
    paused, a cast room is stopped (and a refusal throws, keeping the bar)
    before the queue is emptied, and the media controls are cleared."""
    player = _src("player.jsx")
    body = player[player.index("const closePlayer = async"):player.index("const toggle = ()")]
    order = [body.index(t) for t in (
        "saveSpokenPosition(true)", "_roomControl('stop', target.roomId)",
        "setQueue([])", "mediaSession.metadata = null")]
    assert order == sorted(order), body
    assert 'title="Stop and close"' in player and 'aria-label="Stop and close"' in player
