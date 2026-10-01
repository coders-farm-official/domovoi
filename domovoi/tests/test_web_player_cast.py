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
        "P().prev(); await step();"
        "return { acts: acts(), state: state() };",
        api=OFFICE_AT("play", "Track 2", 9.0),
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


def test_previous_while_casting_touches_neither_the_room_nor_this_browser(driven):
    # It used to POST /api/music/skip: previous moved the room FORWARD (and
    # the core's skip swaps a cast queue for a random library track).
    out = driven["previous_while_casting"]
    assert out["acts"] == [], out["acts"]
    assert out["state"]["kind"] == "room" and out["state"]["room"] == "office"


def test_a_queue_row_while_casting_recasts_from_that_row(driven):
    out = driven["queue_row_while_casting"]
    assert out["lib"]["acts"] == ['POST /api/music/play-tracks {"room_id":"office","track_ids":[4]}']
    assert out["lib"]["state"] == {"kind": "room", "room": "office", "index": 3, "status": "playing"}
    assert out["again"]["acts"] == ['POST /api/music/play-tracks {"room_id":"office","track_ids":[1,2,3,4]}']


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
