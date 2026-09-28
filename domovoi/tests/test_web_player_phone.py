"""The docked player on a phone: a compact bar, and a sheet for the rest.

At 375px the docked MiniPlayer (web/static/player.jsx) used to squeeze the
whole desktop bar into one row — cover, title, three transport buttons, a
seek bar with a 280px minimum, volume, queue, cast target and "open full
player", every button 30px. At 760px and below it is now:

* one 64px row: cover, title/artist (ellipsized), play/pause, next and
  "open player", each a 44px target, with a 2px position line on its top
  edge. What only a desktop shows is marked ``mp-desk`` and what only a
  phone shows ``mp-phone``; styles.css hides each at the other width.
* PlayerSheet, opened from that row: covers the screen above the tab strip
  and carries seek, previous, volume, the sleep countdown, where it plays
  (this browser or a room) and the queue. It closes with its chevron,
  Escape, the back gesture (it is a history entry of its own) or a tab in
  the strip (the one already showing too), when the queue it shows is
  cleared, and when the screen grows past 760px (a phone turned
  sideways). Focus goes to its close button when it opens and back to
  "open player" when it closes in place.
* the desktop bar and its floating queue / cast target are unchanged.

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
window.__backs = 0;
window.__stack = [];
window.history = {
  state: null,
  pushState(s) { window.__stack.push(this.state); this.state = s; window.__pushes.push(s); },
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
const controls = (pred) => h.findAll((e) => (e.type === 'button' || e.type === 'input') && pred(e))
  .map((e) => ({ type: e.type, icon: iconOf(e), title: e.props.title || null,
                 aria: e.props['aria-label'] || null, cls: String(e.props.className || ''),
                 minHeight: (e.props.style || {}).minHeight ?? null,
                 inSave: h.inside(e, cls('mp-q-save')), text: e.text,
                 phone: shownOn(e, 'mp-desk'), desk: shownOn(e, 'mp-phone') }));
const openSheet = () => h.click(cls('mp-expand'));
const focused = () => h.focused().map((e) => e.props['aria-label']);
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
        " listeners: W().__listeners.map((l) => l.t).sort(), media: W().__mqFns.length,"
        " asked: W().__mqAsked.slice(), focused: focused() };"
        "await h.click((e) => e.type === 'button' && e.props['aria-label'] === 'close player');"
        "return { opened, closed: !sheet(), backs: W().__backs, state: W().history.state,"
        " listeners: W().__listeners.map((l) => l.t), media: W().__mqFns.length,"
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
        "W().__fire('hashchange'); h.rerender();"
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
    # A phone turned sideways past 760px, where the sheet never shows.
    "sheet_screen_grows_past_a_phone": _scenario(
        "h.render(); await openSheet();"
        "W().__resize(true); h.rerender(); const openOnPhone = !!sheet();"
        "W().__resize(false); h.rerender();"
        "return { openOnPhone, closed: !sheet(), backs: W().__backs, state: W().history.state,"
        " media: W().__mqFns.length, focused: focused() };",
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
        "h.render(); await openSheet(); return h.find(cls('mp-sheet-sleep')).text;",
        p={"sleepRemainingSec": -1},
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
    # The Music page's Player tab carries its phone classes.
    "now_playing_panel": _scenario(
        "h.render(); return { head: !!h.find(cls('np-head')), transport: !!h.find(cls('np-transport')),"
        " presets: !!h.find(cls('np-eq-presets')) };",
        p={"eqBands": [0] * 10, "eqEnabled": False, "playbackRate": 1},
        component="NowPlayingPanel",
        files=FILES + ["web/static/music_player_panel.jsx"],
        refs=False,
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
    """(the player's rules outside any media block, its 760px block)."""
    css = _src("styles.css")
    start = css.index("Docked player (player.jsx")
    end = css.index("The Music page's Player tab")
    section = css[start:end]
    phone = _block(section, section.index("@media (max-width: 760px) {"))
    return section.replace(phone, ""), phone


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
    phone = [(c["icon"], c["aria"]) for c in bar["controls"] if c["phone"]]
    assert phone == [("pause", "pause"), ("skip-forward", "next"), ("chevron-up", "open player")], phone
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
        ("skip-back", "previous"), ("pause", "pause"), ("skip-forward", "next"),
        ("volume-2", None), ("input", None), ("list-music", "queue"),
        ("monitor-speaker", "cast target"), ("chevron-up", "open full player"),
    ], desk
    # One element per control: nothing the phone shows is a second copy of
    # a desktop control, except the phone's own "open player".
    both = [c["icon"] for c in driven["bar"]["controls"] if c["desk"] and c["phone"]]
    assert both == ["pause", "skip-forward"]


def test_every_phone_bar_control_is_a_44px_target(driven) -> None:
    _, phone = _player_css()
    assert _px(_rule(phone, ".mini-player .btn-icon"), "width") == [44]
    assert _px(_rule(phone, ".mini-player .btn-icon"), "height") == [44]
    for c in driven["bar"]["controls"]:
        if c["phone"]:
            assert "btn-icon" in c["cls"].split(), c


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


@pytest.mark.parametrize(("width", "title_text"), [(320, 110), (375, 165), (414, 204), (760, 550)])
def test_the_phone_bar_leaves_the_title_room_at_each_width(width, title_text) -> None:
    """The bar's fixed parts, read from the CSS: padding, the gaps between
    its three columns, play + next, "open player" — then the 44px cover and
    its 10px gap. What's left is the title's text, ellipsized."""
    desktop, phone = _player_css()
    bar = _rule(phone, ".mini-player")
    assert "grid-template-columns: minmax(0, 1fr) auto auto;" in bar
    _, right, _, left = _px(bar, "padding")
    (gap,) = _px(bar, "gap")
    (target,) = _px(_rule(phone, ".mini-player .btn-icon"), "width")
    fixed = left + right + 2 * gap + 3 * target
    assert fixed == 156
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
    # ...but not a modal one: the strip below it stays live.
    assert "aria-modal" not in o["plain"]["props"]
    assert o["pushes"] == [{"domovoiPlayerSheet": True}]
    assert o["state"] == {"domovoiPlayerSheet": True}
    assert o["expanded"] is True
    assert sorted(o["heads"]) == ["play on", "queue · 3"]
    assert o["listeners"] == ["document:click", "hashchange", "keydown", "popstate"]
    # It watches the phone breakpoint (styles.css's) while it is open.
    assert o["media"] == 1 and o["asked"] == ["(max-width: 760px)"]
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


def test_a_screen_grown_past_a_phone_closes_the_sheet(driven) -> None:
    """Past 760px styles.css never shows the sheet: left open, it would sit
    unseen on a history entry the next back spends doing nothing."""
    g = driven["sheet_screen_grows_past_a_phone"]
    assert g == {"openOnPhone": True, "closed": True, "backs": 1, "state": None,
                 "media": 0, "focused": ["close player"]}


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


def test_every_sheet_control_is_a_44px_target(driven) -> None:
    _, phone = _player_css()
    assert _px(_rule(phone, ".mp-sheet .btn-icon"), "width") == [44]
    assert _px(_rule(phone, ".mp-sheet .mp-sheet-play"), "width") == [56]
    assert _px(_rule(phone, ".mp-sheet .mp-q-save .btn"), "height") == [44]
    assert _px(_rule(phone, ".mp-sheet .mp-q-save input"), "height") == [44]
    assert _px(_rule(phone, '.mp-sheet-vol input[type="range"]'), "height") == [44]
    assert _px(_rule(phone, ".mp-sheet-track"), "height") == [44]
    assert _px(_rule(phone, ".mp-sheet .mp-q-row"), "min-height") == [48]
    for c in driven["sheet_contents"]["controls"]:
        if c["type"] == "input":
            continue
        big = ("btn-icon" in c["cls"].split() or c["minHeight"] == 48
               or (c["inSave"] and "btn" in c["cls"].split()))
        assert big, c


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


def test_the_sleep_countdown_moves_into_the_sheet(driven) -> None:
    assert "at the end" in driven["sheet_sleep_end"]


def test_the_sheet_covers_the_screen_above_the_strip_and_only_on_a_phone() -> None:
    desktop, phone = _player_css()
    assert ".mp-sheet { display: none; }" in desktop
    sheet = _rule(phone, ".mp-sheet")
    assert "position: fixed; left: 0; right: 0; top: 0; bottom: var(--dock-bottom, 0px); z-index: 48;" in sheet
    assert "display: flex;" in sheet
    # Above the bar (45) and its floating panels (46/47), below drawers (60).
    assert "z-index: 45;" in _rule(desktop, ".mini-player")
    ladder = _src("styles.css")
    assert re.search(r"^\s+48\s+its phone sheet$", ladder, re.M)
    # Its body scrolls; nothing in it widens the screen.
    body = _rule(phone, ".mp-sheet-body")
    assert "overflow-y: auto;" in body and "min-height: 0;" in body
    assert "flex: none; min-width: 0;" in _rule(phone, ".mp-sheet-body > *")
    for sel in (".mp-sheet-seek", ".mp-sheet-vol", ".mp-sheet .mp-q-row"):
        assert "minmax(0, 1fr)" in _rule(phone, sel), sel
    assert "min-width: 0;" in _rule(desktop, ".mp-q-save input")
    assert re.search(r"@media \(max-width: 760px\) and \(prefers-reduced-motion: reduce\) \{\s*"
                     r"\.mp-sheet \{ animation: none; \}", _src("styles.css"))


# ─── the desktop panels, unchanged ───────────────────────────────────────


def test_the_desktop_queue_and_cast_panels_still_work(driven) -> None:
    d = driven["desktop_panels"]
    assert d["q"] == {"rows": [True, False, False], "save": True, "header": ["queue · 3"]}
    # Desktop cast rows keep their size (no 48px minimum).
    assert d["cast"]["rows"] == [["This browser", None], ["kitchen", None], ["office", None]]
    assert d["castClosed"] is True          # a successful pick closes the menu
    assert d["acts"] == [["castTo", '{"kind":"room","roomId":"office"}']]
    assert d["hash"] == "music" and d["sheet"] is False
    player = _src("player.jsx")
    # The floating panels move with the bar, as before.
    assert player.count("bottom: 'calc(var(--dock-bottom, 0px) + 76px)'") == 2


# ─── the Music page's Player tab ─────────────────────────────────────────


def test_the_player_tab_stacks_on_a_phone(driven) -> None:
    assert driven["now_playing_panel"] == {"head": True, "transport": True, "presets": True}
    css = _src("styles.css")
    start = css.index("The Music page's Player tab")
    section = css[start:css.index("Calendar page")]
    phone = _block(section, section.index("@media (max-width: 760px) {"))
    desktop = section.replace(phone, "")
    assert "grid-template-columns: 220px 1fr;" in _rule(desktop, ".np-head")
    assert "grid-template-columns: minmax(0, 1fr);" in _rule(phone, ".np-head")
    assert "flex-wrap: wrap;" in _rule(phone, ".np-transport")
    assert "flex-wrap: wrap;" in _rule(phone, ".np-eq-presets")
    assert _px(_rule(phone, ".np-transport .btn-icon"), "width") == [44]
    assert "gridTemplateColumns: '220px 1fr'" not in _src("music_player_panel.jsx")
