"""Casting from the dashboard's player: the real PlaybackProvider, driven.

web/static/player.jsx's PlaybackProvider owns where playback goes (this
browser or a room). Until 2026-10-01 nothing ran it: reverting every cast
change in it left the suite green. Here it runs in
domovoi/tests/jsx_interact_harness.js (the dashboard's own Babel, a small
stateful React) with the browser's audio stubbed: ``Audio`` elements that
log play / pause / src, an ``AudioContext`` that builds nothing, and the
data layer's ``apiGet`` / ``apiPost`` logging into the SAME list, so a
scenario sees ORDER — the room told before this browser went quiet, the
room paused before this browser made a sound.

What is pinned:

* browser → room: a queue with nothing a room can play is refused before
  anything is paused or sent; the room starts on the current track at the
  element's ``currentTime`` (``start_sec``, whole seconds, only from 2 s);
  it is told first and the browser is silenced only once it took the queue,
  so a refused cast leaves the browser playing and saying so.
* room → room: the new room starts where the old one had got to (a fresh
  now-playing read, ``_castFollowRoom``) and the old room is paused after
  the new one took the queue; a refused cast pauses nothing.
* room → browser: the room is paused first; the browser picks up on the
  room's track at the room's time, playing only if the room was playing and
  took the pause, else waiting there paused (and play() starts it there).
* a "play here" while casting (playItems / playSpoken) ends the cast: the
  room is paused, the item at ``startIndex`` plays here, nothing is re-cast.
* a queue row picked while casting (jumpTo) re-casts from that row; the
  highlight moves only once the room took it.
* the picker says so when the room a cast left would not pause, and shows
  the castRefused message as it is.

Since 2026-10-01 (wf/music-remote):

* a "play here" made while a cast is still on its way wins: the cast does
  nothing more, and a room that already took the queue is paused again
  (a pick still waiting its turn is never sent; a hand-back in flight
  doesn't touch this browser; a queue row's re-cast neither);
* a cast from a PAUSED browser, or from a paused room, starts the room
  paused there (``start_paused``), and the player says paused;
* previous while casting is the room's previous (``/api/music/previous``),
  from the buttons, the keys and the OS media session's previoustrack;
* a room control the core answers ``ok: false`` (or non-2xx) is a pause
  that didn't happen, wherever a hand-off waits on one; and the Music
  page's "play here" toast says "paused office" only once office did.

No DB, never ``requires_db``; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/player.jsx"]

# ─── the sandbox: stubbed audio, logged API calls, a readable provider ───

PRELUDE = r"""
globalThis.API_BASE = '';
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
// A Provider that keeps the value it was handed (the harness's React reads
// only a context's default) and renders nothing: the scenarios drive the
// provider's actions, not the docked bar.
(() => {
  const make = React.createContext;
  React.createContext = (v) => {
    const ctx = make(v);
    ctx.Provider = ({ value }) => { ctx._live = value; return null; };
    return ctx;
  };
})();
window.__log = [];
window.__fail = [];
const __note = (s) => { window.__log.push(s); };
window.__audios = [];
globalThis.Audio = function () {
  const n = window.__audios.length;
  const el = {
    _src: '', currentTime: 0, paused: true, playbackRate: 1, readyState: 4, duration: 200,
    crossOrigin: null, preload: null,
    addEventListener() {}, load() {},
    play() { this.paused = false; __note('el' + n + '.play ' + this._src + ' @' + this.currentTime); return Promise.resolve(); },
    pause() { __note('el' + n + '.pause'); this.paused = true; },
  };
  Object.defineProperty(el, 'src', {
    get() { return this._src; },
    set(v) { this._src = v; this.currentTime = 0; __note('el' + n + '.src=' + v); },
  });
  window.__audios.push(el);
  return el;
};
window.AudioContext = function () {
  const node = () => ({ connect() {}, disconnect() {} });
  const param = (v) => ({ value: v, cancelScheduledValues() {}, setValueAtTime() {}, linearRampToValueAtTime() {} });
  return {
    state: 'running', currentTime: 0, destination: {},
    resume: () => Promise.resolve(), close() {},
    createMediaElementSource: () => node(),
    createGain: () => Object.assign(node(), { gain: param(1) }),
    createBiquadFilter: () => Object.assign(node(), { type: '', frequency: param(0), Q: param(1), gain: param(0) }),
    createAnalyser: () => Object.assign(node(), { fftSize: 0, smoothingTimeConstant: 0 }),
  };
};
// The data layer, logged into the same list as the audio; a path (or
// "path#room") in window.__fail answers 502.
(() => {
  const post = apiPost, get = apiGet;
  apiPost = (p, b) => {
    __note('POST ' + p + (b ? ' ' + JSON.stringify(b) : ''));
    const key = p + (b && b.room_id ? '#' + b.room_id : '');
    if (window.__fail.includes(p) || window.__fail.includes(key)) {
      return Promise.reject(Object.assign(new Error('502 Bad Gateway'), { status: 502 }));
    }
    // window.__holdPost: the next POST to that path (or "path#room") is
    // answered only on window.__releasePost() — a room still readying its
    // stream, so a "play here" can come while the cast is on its way.
    if (window.__holdPost && (window.__holdPost === p || window.__holdPost === key)) {
      window.__holdPost = null;
      return new Promise((res, rej) => { window.__releasePost = () => post(p, b).then(res, rej); });
    }
    return post(p, b);
  };
  apiGet = (p) => {
    __note('GET ' + p);
    if (window.__fail.includes(p)) {
      return Promise.reject(Object.assign(new Error('502 Bad Gateway'), { status: 502 }));
    }
    // window.__holdNext: the next now-playing read waits for
    // window.__release(rows) — a poll that lands late.
    if (window.__holdNext && p === '/api/music/now-playing') {
      window.__holdNext = false;
      return new Promise((res) => { window.__release = res; });
    }
    return get(p);
  };
})();
window.__lib = (n) => ({ uid: 'u' + n, kind: 'library', trackId: n, title: 'Track ' + n, artist: 'A',
  album: '', src: '/a/' + n, coverUrl: null, durationSec: 200, seekable: true, cacheable: true, meta: {} });
window.__pod = (n) => ({ uid: 'p' + n, kind: 'podcast', itemId: n, trackId: null, title: 'Episode ' + n,
  artist: 'Show', album: '', src: '/p/' + n, coverUrl: null, durationSec: 900, seekable: true,
  cacheable: true, meta: { itemType: 'podcast_episode' } });
window.__q = () => [1, 2, 3, 4].map(window.__lib);
"""

# The OS media controls (lock screen, headset keys, the browser's media hub):
# what player.jsx registers, by action.
MEDIA_SESSION = r"""
window.__ms = {};
navigator.mediaSession = {
  metadata: null, playbackState: 'none',
  setActionHandler(a, h) { if (h) window.__ms[a] = h; else delete window.__ms[a]; },
};
window.MediaMetadata = function (o) { Object.assign(this, o); };
"""

HELPERS = r"""
const W = () => h.global('window');
const P = () => W().PlaybackContext._live;
const step = async () => { await h.settle(); h.rerender(); await h.settle(); h.rerender(); };
const log = () => W().__log.slice();
const clear = () => { W().__log.length = 0; };
const el0 = () => W().__audios[0];
const state = () => ({ kind: P().target.kind, room: P().target.roomId || null,
                       index: P().index, status: P().status });
// This browser playing `items` from `at`, `sec` seconds in.
const playing = async (items, at, sec) => {
  h.render(); P().playItems(items, at); await step(); el0().currentTime = sec; clear();
};
const castTo = async (t) => {
  let r = null, err = null;
  try { r = await P().castTo(t); } catch (e) { err = { message: e.message, castRefused: !!e.castRefused }; }
  await step();
  return { r, err };
};
// Casting to office after playing Track 2, 7.6 s in.
const castingToOffice = async () => {
  await playing(W().__q(), 1, 7.6);
  await castTo({ kind: 'room', roomId: 'office' });
  clear();
};
// What the scenario returns: the log without the background reads.
const acts = () => log().filter((l) => !l.startsWith('GET '));
// Steps until `prefix` has been logged (a cast reaching the wire).
const onTheWire = async (prefix) => {
  for (let i = 0; i < 20 && !log().some((l) => l.startsWith(prefix)); i++) await step();
  if (!log().some((l) => l.startsWith(prefix))) throw new Error('never sent: ' + prefix);
};
const settled = async (pr) => { try { return await pr; } catch (e) { return { error: String(e) }; } };
"""


def _np(room: str, state: str, title: str | None, elapsed: float) -> dict:
    row: dict = {"room_id": room, "state": state, "elapsed_sec": elapsed}
    if title:
        row["song"] = {"title": title, "artist": "A", "duration_sec": 200}
    return row


def _scenario(script: str, *, api: dict | None = None, setup: str = "",
              component: str = "PlaybackProvider", files: list[str] | None = None,
              fn_props: list[str] | None = None, props: dict | None = None) -> dict:
    return {
        "files": files or FILES,
        "component": component,
        "api": api or {},
        "props": props or {},
        "fnProps": fn_props or [],
        "setup": PRELUDE + setup,
        "script": HELPERS + script,
    }


OFFICE_AT = lambda state, title, elapsed: {  # noqa: E731
    "GET /api/music/now-playing": [_np("office", state, title, elapsed), _np("den", "stop", None, 0)],
}

SCENARIOS = {
    # ── browser → room ────────────────────────────────────────────────────
    "refused_before_anything": _scenario(
        "await playing([W().__pod(1), W().__pod(2)], 0, 40);"
        "const { err } = await castTo({ kind: 'room', roomId: 'office' });"
        "return { err, log: log(), state: state(), paused: el0().paused };",
    ),
    "browser_to_room": _scenario(
        "await playing(W().__q(), 1, 7.6);"
        "const { r, err } = await castTo({ kind: 'room', roomId: 'office' });"
        "return { r, err, acts: acts(), state: state(), paused: el0().paused };",
    ),
    "browser_to_room_at_the_top": _scenario(
        "await playing(W().__q(), 2, 1.4);"
        "await castTo({ kind: 'room', roomId: 'office' });"
        "return { acts: acts() };",
    ),
    "browser_to_room_refused_by_the_room": _scenario(
        "await playing(W().__q(), 1, 7.6); W().__fail.push('/api/music/play-tracks');"
        "const { err } = await castTo({ kind: 'room', roomId: 'office' });"
        "return { err, acts: acts(), state: state(), paused: el0().paused };",
    ),
    # ── room → room ───────────────────────────────────────────────────────
    "room_to_room": _scenario(
        "await castingToOffice();"
        "const { r, err } = await castTo({ kind: 'room', roomId: 'den' });"
        "return { r, err, log: log(), state: state() };",
        api=OFFICE_AT("play", "Track 3", 42.4),
    ),
    "room_to_room_refused": _scenario(
        "await castingToOffice(); W().__fail.push('/api/music/play-tracks#den');"
        "const { err } = await castTo({ kind: 'room', roomId: 'den' });"
        "return { err, acts: acts(), state: state() };",
        api=OFFICE_AT("play", "Track 3", 42.4),
    ),
    # office's reading can't be had now: the last poll's row stands in.
    "room_to_room_when_the_room_cant_be_read": _scenario(
        "await castingToOffice(); W().__fail.push('/api/music/now-playing');"
        "const { r, err } = await castTo({ kind: 'room', roomId: 'den' });"
        "return { r, err, acts: acts(), state: state() };",
        api=OFFICE_AT("play", "Track 3", 42.4),
    ),
    "room_to_the_same_room": _scenario(
        "await castingToOffice();"
        "const { r, err } = await castTo({ kind: 'room', roomId: 'office' });"
        "return { r, err, acts: acts(), state: state() };",
        api=OFFICE_AT("play", "Track 3", 20.0),
    ),
    # Two picks, the second made before the first had answered (one
    # render's castTo, called twice without waiting).
    "two_rooms_picked_in_a_row": _scenario(
        "await playing(W().__q(), 1, 7.6); const p = P();"
        "const a = p.castTo({ kind: 'room', roomId: 'office' });"
        "const b = p.castTo({ kind: 'room', roomId: 'den' });"
        "let ra = null, rb = null; try { ra = await a; } catch (e) { ra = String(e); }"
        "try { rb = await b; } catch (e) { rb = String(e); }"
        "await step();"
        "return { ra, rb, acts: acts(), state: state() };",
        api=OFFICE_AT("play", "Track 2", 9.0),
    ),
    "a_pick_after_a_refused_one": _scenario(
        "await playing(W().__q(), 1, 7.6); W().__fail.push('/api/music/play-tracks#office');"
        "const first = await castTo({ kind: 'room', roomId: 'office' });"
        "const second = await castTo({ kind: 'room', roomId: 'den' });"
        "return { first: first.err, second: second.r, err: second.err, state: state() };",
    ),
    "here_picked_while_a_cast_is_on_its_way": _scenario(
        "await playing(W().__q(), 1, 7.6); const p = P();"
        "const a = p.castTo({ kind: 'room', roomId: 'office' });"
        "const b = p.castTo({ kind: 'browser' });"
        "let ra = null, rb = null; try { ra = await a; } catch (e) { ra = String(e); }"
        "try { rb = await b; } catch (e) { rb = String(e); }"
        "await step();"
        "return { ra, rb, acts: acts(), state: state() };",
        api=OFFICE_AT("play", "Track 2", 9.0),
    ),
    "room_to_room_old_room_wont_pause": _scenario(
        "await castingToOffice(); W().__fail.push('/api/music/pause/office');"
        "const { r } = await castTo({ kind: 'room', roomId: 'den' });"
        "return { r, state: state() };",
        api=OFFICE_AT("play", "Track 3", 42.4),
    ),
    # ── room → this browser ───────────────────────────────────────────────
    "back_from_a_playing_room": _scenario(
        "await castingToOffice();"
        "const { r } = await castTo({ kind: 'browser' });"
        "return { r, log: log(), state: state(), at: el0().currentTime, src: el0().src };",
        api=OFFICE_AT("play", "Track 4", 12.5),
    ),
    "back_from_a_paused_room": _scenario(
        "await castingToOffice();"
        "const { r } = await castTo({ kind: 'browser' });"
        "const waited = { acts: acts(), state: state(), at: el0().currentTime, src: el0().src };"
        "clear(); P().play(); await step();"
        "return { r, waited, play: acts(), after: state() };",
        api=OFFICE_AT("pause", "Track 3", 30.0),
    ),
    "back_when_the_room_cant_be_read": _scenario(
        "await castingToOffice(); W().__fail.push('/api/music/now-playing');"
        "const { r } = await castTo({ kind: 'browser' });"
        "return { r, acts: acts(), state: state(), at: el0().currentTime, src: el0().src };",
        api=OFFICE_AT("play", "Track 3", 30.0),
    ),
    "back_when_the_room_wont_pause": _scenario(
        "await castingToOffice(); W().__fail.push('/api/music/pause/office');"
        "const { r } = await castTo({ kind: 'browser' });"
        "return { r, acts: acts(), state: state() };",
        api=OFFICE_AT("play", "Track 3", 30.0),
    ),
    # The poll's read for office lands only after this browser took over.
    "late_poll_after_the_hand_back": _scenario(
        "await playing(W().__q(), 1, 7.6); W().__holdNext = true;"
        "await castTo({ kind: 'room', roomId: 'office' });"
        "const { r } = await castTo({ kind: 'browser' });"
        "W().__release([{ room_id: 'office', state: 'pause', elapsed_sec: 99, song: { title: 'Track 1' } }]);"
        "await step();"
        "return { r, state: state() };",
        api=OFFICE_AT("play", "Track 4", 12.5),
    ),
    "back_from_a_room_on_another_song": _scenario(
        "await castingToOffice();"
        "const { r } = await castTo({ kind: 'browser' });"
        "return { r, acts: acts(), state: state(), at: el0().currentTime };",
        api=OFFICE_AT("play", "Not In This Queue", 50.0),
    ),
    # ── while casting ─────────────────────────────────────────────────────
    "play_here_while_casting": _scenario(
        "await castingToOffice();"
        "const left = P().playItems([W().__lib(7), W().__lib(8), W().__lib(9)], 1); await step();"
        "return { left, acts: acts(), state: state(), src: el0().src };",
        api=OFFICE_AT("play", "Track 2", 9.0),
    ),
    "play_spoken_while_casting": _scenario(
        "await castingToOffice();"
        "P().playSpoken(W().__pod(5), { resumeSec: 120 }); await step();"
        "return { acts: acts(), state: state(), at: el0().currentTime };",
        api=OFFICE_AT("play", "Track 2", 9.0),
    ),
    "previous_while_casting": _scenario(
        "await castingToOffice();"
        "P().prev(); await step(); const prev = acts(); clear();"
        "P().next(); await step();"
        "return { prev, next: acts(), state: state() };",
        api=OFFICE_AT("play", "Track 2", 9.0),
    ),
    "previous_from_the_os_media_controls_while_casting": _scenario(
        "await castingToOffice(); h.rerender(); await step();"
        "const has = Object.keys(W().__ms).sort();"
        "W().__ms.previoustrack(); await step(); const prev = acts(); clear();"
        "W().__ms.nexttrack(); await step();"
        "return { has, prev, next: acts() };",
        api=OFFICE_AT("play", "Track 2", 9.0), setup=MEDIA_SESSION,
    ),
    "queue_row_while_casting": _scenario(
        "await castingToOffice();"
        "P().jumpTo(3); await step(); const lib = { acts: acts(), state: state() };"
        "clear(); h.rerender(); P().jumpTo(0); await step();"
        "return { lib, again: { acts: acts(), state: state() } };",
        api=OFFICE_AT("play", "Track 2", 9.0),
    ),
    "queue_row_refused_while_casting": _scenario(
        "await playing([W().__lib(1), W().__pod(2)], 0, 3);"
        "await castTo({ kind: 'room', roomId: 'office' }); clear();"
        "P().jumpTo(1); await step();"
        "return { acts: acts(), state: state() };",
    ),
}

# The pure follow-the-room helper, read off the sandbox.
SCENARIOS["follow_room"] = _scenario(
    "const f = W().__follow; const q = W().__q();"
    "const np = (title, sec) => ({ room_id: 'office', state: 'play', elapsed_sec: sec,"
    "  song: title ? { title } : null });"
    "return {"
    " later: f(q, 1, np('Track 3', 42.4)),"
    " before_index_is_not_matched: f(q, 1, np('Track 1', 5)),"
    " phone_copy_skipped: f([W().__pod(1), Object.assign(W().__lib(2), { title: 'Episode 1' })], 0,"
    "   np('Episode 1', 9)),"
    " no_reading: f(q, 1, null),"
    " stopped_room: f(q, 1, { room_id: 'office', state: 'stop', elapsed_sec: 0 }),"
    "};",
    component="(window.__follow = _castFollowRoom, () => null)",
)

# The picker (PlayerCastTargets) over a scripted player: what it says.
_PICKER_P = r"""
window.__picks = [];
window.__P = (castResult) => ({
  target: { kind: 'browser' },
  castTo: (t) => { window.__picks.push(t); return castResult(t); },
});
"""

SCENARIOS.update({
    "picker_left_room_wont_pause": _scenario(
        "h.render(); await h.click({ type: 'button', text: 'den' });"
        "return { text: h.text(), picked: h.fnCalls.map((c) => c.name) };",
        component="(props) => PlayerCastTargets({ ...props, p: window.__P(() => Promise.resolve("
                  "{ kind: 'room', roomId: 'den', left: 'office', leftPaused: false })) })",
        api={"GET /api/music/now-playing": [_np("office", "play", "Track 1", 3), _np("den", "stop", None, 0)]},
        setup=_PICKER_P, fn_props=["onPicked"],
    ),
    "picker_all_well": _scenario(
        "h.render(); await h.click({ type: 'button', text: 'den' });"
        "return { text: h.text(), picked: h.fnCalls.map((c) => c.name) };",
        component="(props) => PlayerCastTargets({ ...props, p: window.__P(() => Promise.resolve("
                  "{ kind: 'room', roomId: 'den', left: 'office', leftPaused: true })) })",
        api={"GET /api/music/now-playing": [_np("office", "play", "Track 1", 3), _np("den", "stop", None, 0)]},
        setup=_PICKER_P, fn_props=["onPicked"],
    ),
    "picker_refused": _scenario(
        "h.render(); await h.click({ type: 'button', text: 'den' });"
        "return { text: h.text(), picked: h.fnCalls.map((c) => c.name) };",
        component="(props) => PlayerCastTargets({ ...props, p: window.__P(() => Promise.reject("
                  "Object.assign(new Error('only library songs can be cast to a room — nothing from here on is in the library'),"
                  " { castRefused: true }))) })",
        api={"GET /api/music/now-playing": [_np("den", "stop", None, 0)]},
        setup=_PICKER_P, fn_props=["onPicked"],
    ),
})

# ── a "play here" while a cast is still on its way (2026-10-01) ────────────
SCENARIOS.update({
    "play_here_during_a_cast_from_this_browser": _scenario(
        "await playing(W().__q(), 1, 7.6); W().__holdPost = '/api/music/play-tracks';"
        "const a = P().castTo({ kind: 'room', roomId: 'office' });"
        "await onTheWire('POST /api/music/play-tracks');"
        "const left = P().playItems([W().__lib(7)], 0); await step(); const mark = log().length;"
        "W().__releasePost(); const r = await settled(a); await step();"
        "return { r, left, acts: acts(), after: log().slice(mark), state: state(), src: el0().src,"
        "         paused: el0().paused };",
    ),
    "play_here_during_a_room_to_room_cast": _scenario(
        "await castingToOffice(); W().__holdPost = '/api/music/play-tracks#den';"
        "const a = P().castTo({ kind: 'room', roomId: 'den' });"
        "await onTheWire('POST /api/music/play-tracks {\"room_id\":\"den\"');"
        "const left = P().playItems([W().__lib(7)], 0); await step();"
        "W().__releasePost(); const r = await settled(a); await step();"
        "return { r, left, acts: acts(), state: state(), src: el0().src };",
        api=OFFICE_AT("play", "Track 3", 42.4),
    ),
    "play_here_while_a_pick_waits_its_turn": _scenario(
        "await playing(W().__q(), 1, 7.6); W().__holdPost = '/api/music/play-tracks';"
        "const a = P().castTo({ kind: 'room', roomId: 'office' });"
        "const b = P().castTo({ kind: 'room', roomId: 'den' });"
        "await onTheWire('POST /api/music/play-tracks');"
        "P().playItems([W().__lib(7)], 0); await step();"
        "W().__releasePost(); const ra = await settled(a); const rb = await settled(b); await step();"
        "return { ra, rb, acts: acts(), state: state() };",
    ),
    "play_here_during_a_hand_back": _scenario(
        "await castingToOffice(); W().__holdPost = '/api/music/pause/office';"
        "const a = P().castTo({ kind: 'browser' });"
        "await onTheWire('POST /api/music/pause/office');"
        "P().playItems([W().__lib(7)], 0); await step(); const mark = log().length;"
        "W().__releasePost(); const r = await settled(a); await step();"
        "return { r, after: log().slice(mark), state: state(), src: el0().src };",
        api=OFFICE_AT("play", "Track 3", 30.0),
    ),
    "play_here_during_a_queue_row_recast": _scenario(
        "await castingToOffice(); W().__holdPost = '/api/music/play-tracks';"
        "P().jumpTo(3);"
        "await onTheWire('POST /api/music/play-tracks');"
        "P().playItems([W().__lib(7)], 0); await step();"
        "W().__releasePost(); await step(); await step();"
        "return { acts: acts(), state: state(), src: el0().src };",
        api=OFFICE_AT("play", "Track 2", 9.0),
    ),
    "play_spoken_during_a_cast_from_this_browser": _scenario(
        "await playing(W().__q(), 1, 7.6); W().__holdPost = '/api/music/play-tracks';"
        "const a = P().castTo({ kind: 'room', roomId: 'office' });"
        "await onTheWire('POST /api/music/play-tracks');"
        "P().playSpoken(W().__pod(5), { resumeSec: 120 }); await step();"
        "W().__releasePost(); const r = await settled(a); await step();"
        "return { r, state: state(), src: el0().src };",
    ),
    # The play here comes while the cast still reads where the old room is.
    "play_here_while_the_old_room_is_read": _scenario(
        "await castingToOffice(); W().__holdNext = true;"
        "const a = P().castTo({ kind: 'room', roomId: 'den' });"
        "await step();"
        "P().playItems([W().__lib(7)], 0); await step();"
        "W().__release([{ room_id: 'office', state: 'play', elapsed_sec: 42, song: { title: 'Track 3' } }]);"
        "const r = await settled(a); await step();"
        "return { r, acts: acts(), state: state() };",
        api=OFFICE_AT("play", "Track 3", 42.4),
    ),
    # A queue row tapped while a cast to den is on its way: it waits its
    # turn, and then re-casts the room the controls point at by then.
    "queue_row_during_a_cast": _scenario(
        "await castingToOffice(); W().__holdPost = '/api/music/play-tracks#den';"
        "const a = P().castTo({ kind: 'room', roomId: 'den' });"
        "await onTheWire('POST /api/music/play-tracks {\"room_id\":\"den\"');"
        "h.rerender(); P().jumpTo(3); await step();"
        "W().__releasePost(); await settled(a); await step(); await step();"
        "return { acts: acts(), state: state() };",
        api=OFFICE_AT("play", "Track 3", 42.4),
    ),
    "play_here_during_a_cast_the_room_wont_undo": _scenario(
        "await playing(W().__q(), 1, 7.6); W().__holdPost = '/api/music/play-tracks';"
        "W().__fail.push('/api/music/pause/office');"
        "const a = P().castTo({ kind: 'room', roomId: 'office' });"
        "await onTheWire('POST /api/music/play-tracks');"
        "P().playItems([W().__lib(7)], 0); await step();"
        "W().__releasePost(); const r = await settled(a); await step();"
        "return { r, state: state() };",
    ),
    # ── a cast from a paused player starts the room paused ──────────────────
    "cast_from_a_paused_browser": _scenario(
        "await playing(W().__q(), 1, 151.5); P().pause(); await step(); clear();"
        "const { r, err } = await castTo({ kind: 'room', roomId: 'office' });"
        "return { r, err, acts: acts(), state: state() };",
    ),
    "cast_from_a_paused_room_to_another": _scenario(
        "await castingToOffice();"
        "const { r, err } = await castTo({ kind: 'room', roomId: 'den' });"
        "return { r, err, acts: acts() };",
        api=OFFICE_AT("pause", "Track 3", 42.4),
    ),
    # ── a room control the core says didn't happen ───────────────────────────
    "back_when_the_room_answers_not_paused": _scenario(
        "await castingToOffice();"
        "const { r } = await castTo({ kind: 'browser' });"
        "return { r, acts: acts(), state: state() };",
        api={**OFFICE_AT("play", "Track 3", 30.0), "POST /api/music/pause/office": {"ok": False}},
    ),
    "room_to_room_old_room_answers_not_paused": _scenario(
        "await castingToOffice();"
        "const { r } = await castTo({ kind: 'room', roomId: 'den' });"
        "return { r };",
        api={**OFFICE_AT("play", "Track 3", 42.4), "POST /api/music/pause/office": {"ok": False}},
    ),
    "play_here_hears_how_the_rooms_pause_went": _scenario(
        "const heard = [];"
        "await castingToOffice();"
        "P().playItems([W().__lib(7)], 0, { onLeft: (room, paused) => heard.push([room, paused]) });"
        "await step();"
        "await castTo({ kind: 'room', roomId: 'office' }); W().__fail.push('/api/music/pause/office');"
        "P().playItems([W().__lib(8)], 0, { onLeft: (room, paused) => heard.push([room, paused]) });"
        "await step();"
        "return { heard };",
        api=OFFICE_AT("play", "Track 2", 9.0),
    ),
})

# The picker, told a "play here" beat its pick.
SCENARIOS.update({
    "picker_superseded_room_still_playing": _scenario(
        "h.render(); await h.click({ type: 'button', text: 'den' });"
        "return { text: h.text(), picked: h.fnCalls.map((c) => c.name) };",
        component="(props) => PlayerCastTargets({ ...props, p: window.__P(() => Promise.resolve("
                  "{ kind: 'superseded', roomId: 'den', sent: true, undone: false })) })",
        api={"GET /api/music/now-playing": [_np("den", "stop", None, 0)]},
        setup=_PICKER_P, fn_props=["onPicked"],
    ),
    "picker_superseded_undone": _scenario(
        "h.render(); await h.click({ type: 'button', text: 'den' });"
        "return { text: h.text(), picked: h.fnCalls.map((c) => c.name) };",
        component="(props) => PlayerCastTargets({ ...props, p: window.__P(() => Promise.resolve("
                  "{ kind: 'superseded', roomId: 'den', sent: true, undone: true })) })",
        api={"GET /api/music/now-playing": [_np("den", "stop", None, 0)]},
        setup=_PICKER_P, fn_props=["onPicked"],
    ),
})

# The Music page's "play here" toast, over a scripted player whose play here
# left office and reports how office's pause went.
_MUSIC_TRACK = {"id": 6, "title": "Warm Stones", "artist": "Hearth Ensemble", "album": "By The Hearth",
                "duration_sec": 200, "file_path": "/music/hearth_06.mp3", "added_at": "2026-09-01T00:00:00Z",
                "added_via": "manual", "favorited": False}
_MUSIC_API = {"GET /api/playlists": [], "GET /api/music/library/stats": None,
              "GET /api/acquisitions?limit=100": None, "GET /api/music/now-playing": [],
              "GET /api/music/library": {"total": 1, "items": [_MUSIC_TRACK]}}
_MUSIC_PLAYER = r"""
window.__played = [];
usePlayback = () => ({
  available: true,
  playItems: (items, at, opts) => {
    window.__played.push(items.map((i) => i.title));
    if (window.__left) Promise.resolve().then(() => opts && opts.onLeft && opts.onLeft('office', window.__leftPaused));
    return window.__left;
  },
  enqueue() {}, playNext() {},
});
window.itemFromTrack = (t) => ({ title: t.title });
"""
_MUSIC_PLAY_HERE = (
    "h.render(); await h.settle(); h.rerender();"
    "await h.click({ type: 'button', title: 'play in this browser' }); await h.settle(); h.rerender();"
    "return { text: h.text().filter((t) => t.includes('in this browser')), played: W().__played };"
)
for name, left, paused in (("music_play_here_paused_office", "'office'", "true"),
                           ("music_play_here_office_wont_pause", "'office'", "false"),
                           ("music_play_here_no_room", "null", "true")):
    SCENARIOS[name] = _scenario(
        _MUSIC_PLAY_HERE, files=["web/static/components.jsx", "web/static/music.jsx"], component="MusicPage",
        api=_MUSIC_API, setup=_MUSIC_PLAYER + f"window.__left = {left}; window.__leftPaused = {paused};",
    )


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("player_cast") / "scenarios.json"
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


def _index(lines: list[str], prefix: str) -> int:
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            return i
    raise AssertionError(f"no {prefix!r} in {lines}")


# ── browser → room ─────────────────────────────────────────────────────────


def test_a_queue_a_room_cannot_play_is_refused_before_anything_changes(driven):
    out = driven["refused_before_anything"]
    assert out["err"]["castRefused"] is True
    assert "only library songs" in out["err"]["message"]
    assert out["log"] == [], "something was paused or sent for a refused cast"
    assert out["paused"] is False
    assert out["state"] == {"kind": "browser", "room": None, "index": 0, "status": "playing"}


def test_the_room_starts_on_the_current_track_at_the_elements_time(driven):
    out = driven["browser_to_room"]
    assert out["err"] is None
    post = 'POST /api/music/play-tracks {"room_id":"office","track_ids":[2,3,4],"start_sec":7}'
    assert out["acts"][0] == post
    assert out["state"] == {"kind": "room", "room": "office", "index": 1, "status": "playing"}
    assert out["paused"] is True
    assert out["r"]["startIndex"] == 1 and out["r"]["startSec"] == 7.6
    assert out["r"]["left"] is None


def test_the_room_is_told_before_this_browser_goes_quiet(driven):
    acts = driven["browser_to_room"]["acts"]
    assert _index(acts, "POST /api/music/play-tracks") < _index(acts, "el0.pause")


def test_under_two_seconds_in_starts_the_track_from_the_top(driven):
    assert driven["browser_to_room_at_the_top"]["acts"][0] == (
        'POST /api/music/play-tracks {"room_id":"office","track_ids":[3,4]}'
    )


def test_a_room_that_refuses_the_cast_leaves_this_browser_playing(driven):
    out = driven["browser_to_room_refused_by_the_room"]
    assert out["err"] is not None and out["err"]["castRefused"] is False
    assert out["acts"] == ['POST /api/music/play-tracks {"room_id":"office","track_ids":[2,3,4],"start_sec":7}']
    assert out["paused"] is False
    assert out["state"]["kind"] == "browser" and out["state"]["status"] == "playing"


# ── room → room ────────────────────────────────────────────────────────────


def test_room_to_room_starts_where_the_first_room_had_got_to_then_pauses_it(driven):
    out = driven["room_to_room"]
    assert out["err"] is None
    log = out["log"]
    # A fresh read of office first, then den, then office paused.
    read = _index(log, "GET /api/music/now-playing")
    cast = _index(log, 'POST /api/music/play-tracks {"room_id":"den","track_ids":[3,4],"start_sec":42}')
    pause = _index(log, "POST /api/music/pause/office")
    assert read < cast < pause
    # (status then mirrors den's own now-playing row, scripted as stopped)
    assert {k: out["state"][k] for k in ("kind", "room", "index")} == {"kind": "room", "room": "den", "index": 2}
    assert out["r"]["left"] == "office" and out["r"]["leftPaused"] is True


def test_a_room_that_refuses_leaves_the_first_room_playing(driven):
    out = driven["room_to_room_refused"]
    assert out["err"] is not None
    assert not any("pause/office" in a for a in out["acts"])
    assert out["state"]["room"] == "office"


def test_a_first_room_that_wont_pause_is_reported(driven):
    out = driven["room_to_room_old_room_wont_pause"]
    assert out["state"]["room"] == "den"
    assert out["r"]["left"] == "office" and out["r"]["leftPaused"] is False


def test_room_to_room_when_the_room_cant_be_read_follows_the_last_poll(driven):
    out = driven["room_to_room_when_the_room_cant_be_read"]
    assert out["err"] is None
    assert 'POST /api/music/play-tracks {"room_id":"den","track_ids":[3,4],"start_sec":42}' in out["acts"]
    assert out["r"]["left"] == "office"


def test_picking_the_room_already_cast_to_restarts_it_there_and_pauses_nothing(driven):
    out = driven["room_to_the_same_room"]
    assert out["err"] is None
    assert 'POST /api/music/play-tracks {"room_id":"office","track_ids":[3,4],"start_sec":20}' in out["acts"]
    assert not any("pause/office" in a for a in out["acts"]), "paused the room it had just started"
    assert out["r"]["left"] is None
    assert out["state"]["kind"] == "room" and out["state"]["room"] == "office"


# ── a second pick while a cast is on its way ───────────────────────────────
# Before 2026-10-01 both picks started from the target as it was (this
# browser): office then den left BOTH rooms playing, office never paused;
# office then "This browser" was a no-op and the cast to office landed after.


def test_a_second_room_picked_before_the_first_answered_waits_then_pauses_the_first(driven):
    out = driven["two_rooms_picked_in_a_row"]
    acts = out["acts"]
    office = _index(acts, 'POST /api/music/play-tracks {"room_id":"office"')
    den = _index(acts, 'POST /api/music/play-tracks {"room_id":"den"')
    pause = _index(acts, "POST /api/music/pause/office")
    assert office < den < pause, acts
    assert out["state"]["kind"] == "room" and out["state"]["room"] == "den"
    assert out["rb"]["left"] == "office" and out["rb"]["leftPaused"] is True


def test_this_browser_picked_before_a_cast_answered_comes_back_from_that_room(driven):
    out = driven["here_picked_while_a_cast_is_on_its_way"]
    acts = out["acts"]
    assert _index(acts, 'POST /api/music/play-tracks {"room_id":"office"') < _index(
        acts, "POST /api/music/pause/office"
    )
    assert out["state"]["kind"] == "browser"
    assert out["rb"]["left"] == "office" and out["rb"]["playing"] is True


def test_a_refused_cast_does_not_hold_up_the_next_pick(driven):
    out = driven["a_pick_after_a_refused_one"]
    assert out["first"] is not None
    assert out["err"] is None, out["err"]
    assert out["second"]["roomId"] == "den" and out["second"]["left"] is None
    assert out["state"]["kind"] == "room" and out["state"]["room"] == "den"


# ── room → this browser ────────────────────────────────────────────────────


def test_back_from_a_playing_room_pauses_it_then_plays_here_where_it_was(driven):
    out = driven["back_from_a_playing_room"]
    log = out["log"]
    pause = _index(log, "POST /api/music/pause/office")
    play = _index(log, "el0.play /a/4")
    assert pause < play, "this browser played before the room was paused"
    assert out["src"] == "/a/4" and out["at"] == 12.5
    assert out["state"] == {"kind": "browser", "room": None, "index": 3, "status": "playing"}
    assert out["r"]["playing"] is True and out["r"]["leftPaused"] is True


def test_back_from_a_paused_room_waits_here_paused_at_the_rooms_place(driven):
    out = driven["back_from_a_paused_room"]
    waited = out["waited"]
    assert waited["acts"][0] == "POST /api/music/pause/office"
    assert not any(a.startswith("el0.play") for a in waited["acts"]), "played over a paused room"
    assert waited["src"] == "/a/3" and waited["at"] == 30
    assert waited["state"] == {"kind": "browser", "room": None, "index": 2, "status": "paused"}
    assert out["r"]["playing"] is False and out["r"]["leftWasPlaying"] is False
    # Play then starts it there, not from the top of another track.
    assert out["play"] == ["el0.play /a/3 @30"]
    assert out["after"]["status"] == "playing"


def test_back_when_the_room_cant_be_read_follows_the_last_poll(driven):
    out = driven["back_when_the_room_cant_be_read"]
    assert out["acts"][0] == "POST /api/music/pause/office"
    assert out["src"] == "/a/3" and out["at"] == 30
    assert out["r"]["playing"] is True and out["state"]["kind"] == "browser"


def test_a_room_that_wont_pause_keeps_this_browser_quiet(driven):
    out = driven["back_when_the_room_wont_pause"]
    assert not any(a.startswith("el0.play") for a in out["acts"])
    assert out["state"]["kind"] == "browser" and out["state"]["status"] == "paused"
    assert out["r"]["playing"] is False and out["r"]["leftPaused"] is False


def test_a_room_on_a_song_not_in_the_queue_hands_back_where_this_browser_cast(driven):
    out = driven["back_from_a_room_on_another_song"]
    assert out["state"]["index"] == 1
    assert out["at"] == 7.6
    assert out["r"]["playing"] is True


def test_a_poll_that_lands_after_the_hand_back_changes_nothing(driven):
    out = driven["late_poll_after_the_hand_back"]
    assert out["r"]["playing"] is True
    assert out["state"] == {"kind": "browser", "room": None, "index": 3, "status": "playing"}, (
        "a late reading of the room left behind marked this browser paused"
    )


# ── while casting ──────────────────────────────────────────────────────────


def test_play_here_while_casting_pauses_the_room_and_plays_the_picked_item_here(driven):
    out = driven["play_here_while_casting"]
    assert "POST /api/music/pause/office" in out["acts"]
    assert not any(a.startswith("POST /api/music/play-tracks") for a in out["acts"]), "re-cast"
    assert out["src"] == "/a/8", "startIndex was not the item played"
    assert out["state"] == {"kind": "browser", "room": None, "index": 1, "status": "playing"}
    assert out["left"] == "office", "the caller can't say which room was paused"


def test_a_podcast_while_casting_ends_the_cast_too(driven):
    out = driven["play_spoken_while_casting"]
    assert "POST /api/music/pause/office" in out["acts"]
    assert out["state"]["kind"] == "browser" and out["state"]["status"] == "playing"
    assert out["at"] == 120


def test_previous_while_casting_is_the_rooms_previous(driven):
    # It used to POST /api/music/skip: previous moved the room FORWARD. Then
    # (with no previous route) it did nothing. The core's previous follows
    # the room's queue since 2026-10-01, and the web proxies it.
    out = driven["previous_while_casting"]
    assert out["prev"] == ["POST /api/music/previous/office"], out["prev"]
    assert out["next"] == ["POST /api/music/skip/office"], out["next"]
    assert out["state"]["kind"] == "room" and out["state"]["room"] == "office"


def test_the_os_media_controls_previous_is_the_rooms_previous_while_casting(driven):
    out = driven["previous_from_the_os_media_controls_while_casting"]
    assert "previoustrack" in out["has"] and "nexttrack" in out["has"]
    assert out["prev"] == ["POST /api/music/previous/office"], out["prev"]
    assert out["next"] == ["POST /api/music/skip/office"], out["next"]


def test_a_queue_row_while_casting_recasts_from_that_row(driven):
    out = driven["queue_row_while_casting"]
    assert out["lib"]["acts"] == ['POST /api/music/play-tracks {"room_id":"office","track_ids":[4]}']
    assert out["lib"]["state"] == {"kind": "room", "room": "office", "index": 3, "status": "playing"}
    assert out["again"]["acts"] == ['POST /api/music/play-tracks {"room_id":"office","track_ids":[1,2,3,4]}']


# ── a "play here" while a cast is still on its way ─────────────────────────
# Before 2026-10-01 a play here during the 3-5 s a room takes to ready its
# stream played here, then the cast landed: it paused this browser and moved
# the target to the room, and the play here was lost.


def test_a_play_here_during_a_cast_wins_and_the_room_is_paused_again(driven):
    out = driven["play_here_during_a_cast_from_this_browser"]
    assert out["r"] == {"kind": "superseded", "roomId": "office", "sent": True, "undone": True}
    assert out["left"] is None  # the cast had not landed: nothing was being cast
    assert out["state"] == {"kind": "browser", "room": None, "index": 0, "status": "playing"}
    assert out["src"] == "/a/7" and out["paused"] is False
    # After the room took the queue: office paused, this browser untouched.
    assert out["after"] == ["POST /api/music/pause/office"], out["after"]


def test_a_play_here_during_a_room_to_room_cast_pauses_both_rooms(driven):
    out = driven["play_here_during_a_room_to_room_cast"]
    acts = out["acts"]
    assert out["left"] == "office"
    assert out["r"]["kind"] == "superseded" and out["r"]["roomId"] == "den" and out["r"]["undone"] is True
    assert _index(acts, 'POST /api/music/play-tracks {"room_id":"den"') < _index(acts, "POST /api/music/pause/den")
    assert "POST /api/music/pause/office" in acts
    assert out["state"]["kind"] == "browser" and out["src"] == "/a/7"


def test_a_pick_still_waiting_its_turn_is_never_sent_after_a_play_here(driven):
    out = driven["play_here_while_a_pick_waits_its_turn"]
    assert out["ra"]["kind"] == "superseded" and out["ra"]["sent"] is True
    assert out["rb"] == {"kind": "superseded", "roomId": "den", "sent": False}
    assert not any('"room_id":"den"' in a for a in out["acts"]), out["acts"]
    assert out["state"]["kind"] == "browser" and out["state"]["status"] == "playing"


def test_a_play_here_during_a_hand_back_is_left_alone(driven):
    out = driven["play_here_during_a_hand_back"]
    assert out["r"]["kind"] == "superseded"
    # The hand-back would have loaded office's track (Track 3) here.
    assert not any(a.startswith("el0.src=") or a.startswith("el1.src=") for a in out["after"]), out["after"]
    assert out["src"] == "/a/7"
    assert out["state"] == {"kind": "browser", "room": None, "index": 0, "status": "playing"}


def test_a_queue_rows_recast_loses_to_a_play_here(driven):
    out = driven["play_here_during_a_queue_row_recast"]
    acts = out["acts"]
    assert acts.count("POST /api/music/pause/office") == 2, acts  # the play here's, and the undo
    assert _index(acts, "POST /api/music/play-tracks") < len(acts) - 1 - acts[::-1].index("POST /api/music/pause/office")
    assert out["state"] == {"kind": "browser", "room": None, "index": 0, "status": "playing"}


def test_a_podcast_played_here_during_a_cast_wins_too(driven):
    out = driven["play_spoken_during_a_cast_from_this_browser"]
    assert out["r"]["kind"] == "superseded" and out["r"]["undone"] is True
    assert out["state"]["kind"] == "browser" and out["state"]["status"] == "playing"
    assert out["src"] == "/p/5"


def test_a_play_here_while_the_old_room_is_read_sends_the_new_room_nothing(driven):
    out = driven["play_here_while_the_old_room_is_read"]
    assert out["r"] == {"kind": "superseded", "roomId": "den", "sent": False}
    assert not any('"room_id":"den"' in a for a in out["acts"]), out["acts"]
    assert "POST /api/music/pause/office" in out["acts"]
    assert out["state"]["kind"] == "browser"


def test_a_queue_row_tapped_during_a_cast_waits_its_turn(driven):
    out = driven["queue_row_during_a_cast"]
    casts = [a for a in out["acts"] if a.startswith("POST /api/music/play-tracks")]
    assert casts == [
        'POST /api/music/play-tracks {"room_id":"den","track_ids":[3,4],"start_sec":42}',
        'POST /api/music/play-tracks {"room_id":"den","track_ids":[4]}',
    ], out["acts"]
    assert out["state"]["room"] == "den" and out["state"]["index"] == 3


def test_a_room_that_took_the_queue_and_wont_pause_again_is_reported(driven):
    out = driven["play_here_during_a_cast_the_room_wont_undo"]
    assert out["r"] == {"kind": "superseded", "roomId": "office", "sent": True, "undone": False}
    assert out["state"]["kind"] == "browser"


def test_the_picker_says_when_a_superseded_cast_left_a_room_playing(driven):
    out = driven["picker_superseded_room_still_playing"]
    assert any("den couldn't be paused" in t for t in out["text"]), out["text"]
    assert out["picked"] == []
    ok = driven["picker_superseded_undone"]
    assert ok["picked"] == ["onPicked"]


# ── a cast from a paused player waits paused ───────────────────────────────


def test_a_cast_from_a_paused_browser_starts_the_room_paused_there(driven):
    out = driven["cast_from_a_paused_browser"]
    assert out["err"] is None
    assert out["acts"][0] == (
        'POST /api/music/play-tracks {"room_id":"office","track_ids":[2,3,4],"start_sec":151,"start_paused":true}'
    )
    assert out["r"]["paused"] is True
    assert out["state"]["kind"] == "room" and out["state"]["status"] == "paused"


def test_a_cast_from_a_playing_browser_says_nothing_of_pausing(driven):
    out = driven["browser_to_room"]
    assert "start_paused" not in out["acts"][0]
    assert out["r"]["paused"] is False


def test_a_cast_from_a_paused_room_starts_the_next_room_paused(driven):
    out = driven["cast_from_a_paused_room_to_another"]
    assert out["err"] is None
    assert (
        'POST /api/music/play-tracks {"room_id":"den","track_ids":[3,4],"start_sec":42,"start_paused":true}'
        in out["acts"]
    ), out["acts"]
    assert out["r"]["paused"] is True


# ── a control the room says didn't happen ──────────────────────────────────


def test_a_hand_back_whose_pause_answered_not_done_keeps_this_browser_quiet(driven):
    out = driven["back_when_the_room_answers_not_paused"]
    assert out["r"]["leftPaused"] is False and out["r"]["playing"] is False
    assert not any(a.startswith("el0.play") for a in out["acts"])


def test_a_room_to_room_whose_old_room_answered_not_paused_says_so(driven):
    out = driven["room_to_room_old_room_answers_not_paused"]
    assert out["r"]["left"] == "office" and out["r"]["leftPaused"] is False


def test_play_here_hears_whether_the_room_it_left_paused(driven):
    assert driven["play_here_hears_how_the_rooms_pause_went"]["heard"] == [
        ["office", True], ["office", False],
    ]


def test_the_music_pages_play_here_toast_waits_for_the_rooms_answer(driven):
    paused = driven["music_play_here_paused_office"]
    assert paused["played"] == [["Warm Stones"]]
    assert paused["text"] == ['playing "Warm Stones" in this browser · paused office'], paused["text"]
    wont = driven["music_play_here_office_wont_pause"]
    assert wont["text"] == [
        'playing "Warm Stones" in this browser · couldn\'t pause office, it may still be playing'
    ], wont["text"]
    alone = driven["music_play_here_no_room"]
    assert alone["text"] == ['playing "Warm Stones" in this browser'], alone["text"]


def test_a_queue_row_a_room_cannot_play_moves_nothing(driven):
    out = driven["queue_row_refused_while_casting"]
    assert out["acts"] == []
    assert out["state"]["index"] == 0 and out["state"]["room"] == "office"


# ── the picker ─────────────────────────────────────────────────────────────


def test_the_picker_says_when_the_room_it_left_would_not_pause(driven):
    out = driven["picker_left_room_wont_pause"]
    assert any("Couldn't pause office" in t for t in out["text"]), out["text"]
    assert out["picked"] == [], "the picker closed as if all went well"
    ok = driven["picker_all_well"]
    assert ok["picked"] == ["onPicked"]
    assert not any("Couldn't" in t for t in ok["text"])


def test_the_picker_shows_a_refusal_as_it_is(driven):
    out = driven["picker_refused"]
    assert any("only library songs can be cast" in t for t in out["text"]), out["text"]
    assert not any("isn't reachable" in t for t in out["text"])
    assert out["picked"] == []


def test_follow_room_matches_library_titles_from_the_current_item(driven):
    out = driven["follow_room"]
    assert out["later"] == {"at": 2, "sec": 42.4, "known": True}
    assert out["before_index_is_not_matched"] == {"at": 1, "sec": 0, "known": False}
    assert out["phone_copy_skipped"] == {"at": 1, "sec": 9, "known": True}
    assert out["no_reading"] == {"at": 1, "sec": 0, "known": False}
    assert out["stopped_room"] == {"at": 1, "sec": 0, "known": False}
