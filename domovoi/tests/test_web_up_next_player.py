"""Up next in the browser player (phase P3, 2026-11-12), driven.

Three layers, each in domovoi/tests/jsx_interact_harness.js (the
dashboard's own Babel, a small stateful React; audio stubbed like
test_web_player_cast.py):

* the real ``PlaybackProvider`` (web/static/player.jsx): a song clicked in
  a list starts a session (``POST /api/music/up-next/sessions``) and its
  picks join the queue as auto items after the song; a whole list plays as
  it is and arms a ``continue`` session; the queue refills once five or
  fewer songs are left (``next`` with ``have_through_seq``, the same number
  again after a failure, picks the server took back dropped); explicit adds
  land before the picks; Autoplay off drops a mix's waiting picks and asks
  for no more; "less like this" and remove reach the session; a play made
  meanwhile wins; unreachable says so, refused stays quiet; the listening
  history's rows carry the session; whose mix it is goes with the start;
* the queue's "up next" section (``PlayerUpNext``) with a scripted player:
  rows with their reasons, the "based on" / "shuffled from" / "more like"
  head, the Autoplay switch, "for <name>" only with two people or more,
  the one-line states, nothing for a guest or a queue without library songs,
  and the explicit queue rows leaving the picks to it;
* the Music page: a library row, a drawer's song and a playlist row start
  Up next with their list.

No DB, never ``requires_db``; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.tests.test_web_player_cast import HELPERS as CAST_HELPERS
from domovoi.tests.test_web_player_cast import PRELUDE as CAST_PRELUDE

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/player.jsx"]
BASE = "/api/music/up-next"

# A working localStorage, the data layer's apiFetch (routed to the logged
# apiPost / apiPatch), and scripted answers per path, in order.
UP_PRELUDE = r"""
window.__store = {};
localStorage = {
  getItem: (k) => (Object.prototype.hasOwnProperty.call(window.__store, k) ? window.__store[k] : null),
  setItem: (k, v) => { window.__store[k] = String(v); },
  removeItem: (k) => { delete window.__store[k]; },
};
window.__seq = {};
window.__bodies = [];
(() => {
  const post = apiPost, patch = apiPatch;
  const answer = (method, p, b, fallback) => {
    window.__bodies.push([method, p, b === undefined ? null : JSON.parse(JSON.stringify(b))]);
    const q = window.__seq[p];
    if (q && q.length) {
      const a = q.shift();
      if (a && a.__error) return Promise.reject(Object.assign(new Error(a.__error.message || 'error'), a.__error));
      return Promise.resolve(a == null ? a : JSON.parse(JSON.stringify(a)));
    }
    return fallback(p, b);
  };
  apiPost = (p, b) => answer('POST', p, b, post);
  apiPatch = (p, b) => answer('PATCH', p, b, patch);
})();
apiFetch = (p, o) => {
  const m = (o && o.method) || 'GET';
  const b = o && o.body ? JSON.parse(o.body) : undefined;
  return m === 'PATCH' ? apiPatch(p, b) : m === 'GET' ? apiGet(p) : apiPost(p, b);
};
window.__t = (n, artist) => ({ id: n, title: 'Song ' + n, artist: artist || 'Artist ' + n, album: '', duration_sec: 200 });
window.__pk = (seq, id, label, artist) => ({ seq, track_id: id, title: 'Pick ' + id, artist: artist || 'Artist ' + id,
  album: '', duration_sec: 180, reason_code: 'same_artist', reason_label: label || 'same artist', status: 'queued' });
window.__sess = (mode, picks, extra) => Object.assign({ id: 'S1', mode, autoplay: true, person: null,
  seed: { track_id: 10, title: 'Song 10', label: 'from road trip', list: mode === 'library' ? null : { kind: 'playlist', name: 'Road Trip' } },
  picks: picks || [], batch_no: 1, have_through_seq: (picks || []).reduce((m, p) => Math.max(m, p.seq), 0), ended: false }, extra || {});
"""

UP_HELPERS = r"""
const Q = () => P().queue.map((it) => [it.trackId, it.origin || '', it.upNextSeq || 0, it.reasonLabel || '', it.sessionId || '']);
const bodies = (re) => W().__bodies.filter((b) => re.test(b[1])).map((b) => [b[0], b[1], b[2]]);
const UN = () => { const u = P().upNext; return u ? JSON.parse(JSON.stringify(u)) : null; };
const ans = (path, ...a) => { W().__seq[path] = (W().__seq[path] || []).concat(a); };
"""


def _provider(script: str, *, setup: str = "", api: dict | None = None) -> dict:
    return {
        "files": FILES,
        "component": "PlaybackProvider",
        "api": api or {},
        "props": {},
        "fnProps": [],
        "setup": CAST_PRELUDE + UP_PRELUDE + setup,
        "script": CAST_HELPERS + UP_HELPERS + script,
    }


T3 = "[10, 11, 12].map((n) => W().__t(n))"
S = f"{BASE}/sessions"
NEXT = f"{BASE}/sessions/S1/next"
FB = f"{BASE}/sessions/S1/feedback"

SCENARIOS: dict = {
    # A song clicked in a playlist: it plays at once; the session's picks follow it.
    "click_starts": _provider(
        f"ans('{S}', W().__sess('list', [W().__pk(1, 12, 'same artist'), W().__pk(2, 10, '90s')]));"
        f"h.render(); P().playFromList({T3}, 1, {{ kind: 'playlist', ref: '7', name: 'Road Trip' }}); await step();"
        "return { posts: bodies(/up-next/), queue: Q(), index: P().index, un: UN(),"
        " played: log().filter((l) => l.includes('.play ')) };"),
    "click_ids": _provider(
        f"ans('{S}', W().__sess('list', []));"
        f"h.render(); P().playFromList({T3}, 0, {{ kind: 'ids', name: 'results for \"x\"' }}); await step();"
        "return { posts: bodies(/up-next/) };"),
    "click_alone": _provider(
        f"ans('{S}', W().__sess('library', [W().__pk(1, 40)]));"
        "h.render(); P().playFromList([W().__t(10)], 0, null); await step();"
        "return { posts: bodies(/up-next/), queue: Q() };"),
    # Six picks: nothing asked until five or fewer songs are left; the answer's
    # picks already held are not added twice; a pick the server took back goes.
    "refill": _provider(
        f"ans('{S}', W().__sess('library', [1, 2, 3, 4, 5, 6].map((s) => W().__pk(s, 20 + s))));"
        f"ans('{NEXT}', {{ batch_no: 1, picks: [W().__pk(5, 25), W().__pk(7, 27, 'wildcard'), W().__pk(8, 28)], removed: [6], ended: false }});"
        "h.render(); P().playFromList([W().__t(10)], 0, null); await step();"
        "const before = bodies(/next$/).length;"
        "P().next(); await step(); await step();"
        "return { before, nexts: bodies(/next$/), queue: Q(), index: P().index, un: UN() };"),
    # A small list played out: the refill's answer says the session went on
    # in the library ('continue'), and the head follows it.
    "list_goes_on": _provider(
        f"ans('{S}', W().__sess('list', [W().__pk(1, 11, 'same artist')]));"
        f"ans('{NEXT}', {{ batch_no: 1, picks: [W().__pk(2, 40, 'wildcard'), W().__pk(3, 41, '90s')], removed: [], ended: false, mode: 'continue' }});"
        "h.render(); P().playFromList([10, 11].map((n) => W().__t(n)), 0, { kind: 'ids', name: 'results for x' });"
        " await step(); await step();"
        "return { un: UN(), queue: Q(), basis: W().upNextBasis(UN()) };"),
    # A refill that failed is asked again, with the same number, when the song changes.
    "refill_retry": _provider(
        f"ans('{S}', W().__sess('library', [1, 2].map((s) => W().__pk(s, 20 + s))));"
        f"ans('{NEXT}', {{ __error: {{ status: 502, message: 'bad gateway' }} }},"
        "  { batch_no: 1, picks: [W().__pk(3, 23)], removed: [], ended: false });"
        "h.render(); P().playFromList([W().__t(10)], 0, null); await step(); await step();"
        "const failed = UN();"
        "P().next(); await step(); await step();"
        "return { nexts: bodies(/next$/), failedOffline: failed.offline, after: UN(), queue: Q() };"),
    # Autoplay off: a mix's waiting picks leave, the server is told, nothing more is asked.
    "autoplay_off": _provider(
        f"ans('{S}', W().__sess('library', [1, 2, 3].map((s) => W().__pk(s, 20 + s))));"
        "h.render(); P().playFromList([W().__t(10)], 0, null); await step();"
        "const asked = bodies(/next$/).length;"
        "await P().setAutoplay(false); await step();"
        "const q = Q(); P().next(); await step();"
        "return { patches: bodies(/S1$/), queue: q, nexts: bodies(/next$/).length - asked,"
        " stored: W().__store['domovoi-upnext-autoplay'], pref: P().autoplay };"),
    # ...a list keeps the rest of itself.
    "autoplay_off_list": _provider(
        f"ans('{S}', W().__sess('list', [W().__pk(1, 11), W().__pk(2, 12)]));"
        f"h.render(); P().playFromList({T3}, 0, {{ kind: 'playlist', ref: '7', name: 'Road Trip' }}); await step();"
        "await P().setAutoplay(false); await step();"
        "return { queue: Q() };"),
    # Started with autoplay off: the start says so.
    "starts_off": _provider(
        f"ans('{S}', W().__sess('library', [], {{ autoplay: false }}));"
        "W().__store['domovoi-upnext-autoplay'] = '0';"
        "h.render(); P().playFromList([W().__t(10)], 0, null); await step();"
        "return { posts: bodies(/sessions$/) };"),
    # A whole list played on purpose: the list is the queue; Up next waits for its end.
    "whole_list": _provider(
        f"ans('{S}', W().__sess('continue', []));"
        f"ans('{NEXT}', {{ batch_no: 0, picks: [W().__pk(1, 50, 'from road trip')], removed: [], ended: false }});"
        "h.render(); P().playList([10, 11, 12, 13, 14, 15, 16, 17].map((n) => W().__t(n)),"
        " { kind: 'playlist', ref: '7', name: 'Road Trip' }); await step();"
        "const first = { posts: bodies(/up-next/), queue: Q() };"
        "P().jumpTo(1); await step(); const atOne = bodies(/next$/).length;"
        "P().jumpTo(2); await step(); await step();"
        "return { first, atOne, nexts: bodies(/next$/), queue: Q() };"),
    # No list given: the songs themselves are the list.
    "whole_list_ids": _provider(
        f"ans('{S}', W().__sess('continue', []));"
        f"h.render(); P().playList({T3}, null); await step();"
        "return { posts: bodies(/sessions$/) };"),
    # A queue of library songs with no session (added from search, say) gets one
    # from the queue itself when it is nearly over.
    "queue_without_session": _provider(
        f"ans('{S}', W().__sess('continue', []));"
        "h.render(); P().playItems([10, 11].map((n) => W().itemFromTrack(W().__t(n))), 0); await step(); await step();"
        "return { posts: bodies(/sessions$/), queue: Q() };",
    ),
    "less_like_this": _provider(
        f"ans('{S}', W().__sess('library', [W().__pk(1, 21, 'same artist', 'Nina'), W().__pk(2, 22, '90s', 'Otis'),"
        " W().__pk(3, 23, 'soul', 'Nina')]));"
        f"ans('{FB}', {{ removed: [1, 3] }});"
        "h.render(); P().playFromList([W().__t(10)], 0, null); await step();"
        "const gone = await P().lessLikeThis(1); await step();"
        "return { gone, fb: bodies(/feedback$/), queue: Q() };"),
    "remove_pick": _provider(
        f"ans('{S}', W().__sess('library', [W().__pk(1, 21), W().__pk(2, 22)]));"
        "h.render(); P().playFromList([W().__t(10)], 0, null); await step();"
        "P().removeAt(2); await step();"
        "return { fb: bodies(/feedback$/), queue: Q() };"),
    "enqueue_before_picks": _provider(
        f"ans('{S}', W().__sess('library', [W().__pk(1, 21), W().__pk(2, 22)]));"
        "h.render(); P().playFromList([W().__t(10)], 0, null); await step();"
        "P().enqueue([W().itemFromTrack(W().__t(99))]); await step();"
        "P().playNext(W().itemFromTrack(W().__t(98))); await step();"
        "return { queue: Q() };",
    ),
    "offline": _provider(
        f"ans('{S}', {{ __error: {{ status: 502, message: 'bad gateway' }} }});"
        "h.render(); P().playFromList([W().__t(10)], 0, null); await step();"
        "return { un: UN(), queue: Q() };"),
    "unreachable_then_back": _provider(
        f"ans('{S}', {{ __error: {{ message: 'Failed to fetch' }} }}, W().__sess('library', [W().__pk(1, 21)]));"
        f"h.render(); P().playList({T3}.concat([W().__t(13)]), null); await step();"
        "const first = UN(); P().next(); await step(); await step();"
        "return { first, posts: bodies(/sessions$/).length, un: UN(), queue: Q() };"),
    "refused": _provider(
        f"ans('{S}', {{ __error: {{ status: 404, message: 'track not found' }} }});"
        "h.render(); P().playFromList([W().__t(10)], 0, null); await step(); await step();"
        "return { un: UN(), posts: bodies(/up-next/).length };"),
    # A play made while the start is on its way wins.
    "play_wins": _provider(
        f"W().__holdPost = '{S}';"
        "h.render(); P().playFromList([W().__t(10)], 0, null); await step();"
        "P().playItems([W().__pod(1)], 0); await step();"
        "W().__releasePost(); await step(); await step();"
        "return { un: UN(), queue: P().queue.map((it) => it.kind) };",
        api={f"POST {S}": {"id": "S1", "mode": "library", "autoplay": True, "seed": {}, "picks": [
            {"seq": 1, "track_id": 21, "title": "x", "artist": "y", "reason_label": "wildcard"}],
            "have_through_seq": 1}}),
    # Whose mix: listening as a person, as nobody on purpose, or the device's own.
    "listener_person": _provider(
        f"ans('{S}', W().__sess('library', []));"
        "W().__store['domovoi-listener-person'] = '5';"
        "h.render(); P().playFromList([W().__t(10)], 0, null); await step();"
        "return { posts: bodies(/sessions$/) };"),
    "listener_household": _provider(
        f"ans('{S}', W().__sess('library', []));"
        "W().__store['domovoi-listener-person'] = 'none';"
        "h.render(); P().playFromList([W().__t(10)], 0, null); await step();"
        "return { posts: bodies(/sessions$/) };"),
    "for_selector": _provider(
        f"ans('{S}', W().__sess('library', []));"
        f"ans('{BASE}/sessions/S1', Object.assign(W().__sess('library', []), {{ person: {{ id: 2, name: 'Sam' }} }}));"
        "h.render(); P().playFromList([W().__t(10)], 0, null); await step();"
        "await P().setUpNextListener(2); await step();"
        "return { patches: bodies(/S1$/), person: UN().person, listening: P().listenerPersonId };"),
    # The listening history carries the session and the origin back.
    "listen_rows": _provider(
        "h.render();"
        "W().ListenLog.follow({ uid: 'a', kind: 'library', trackId: 5, durationSec: 200, origin: 'auto', sessionId: 'S1' }, true, 200);"
        "W().ListenLog.close(true);"
        "W().ListenLog.follow({ uid: 'b', kind: 'library', trackId: 6, durationSec: 200, origin: 'explicit' }, true, 200);"
        "W().ListenLog.close(true);"
        "return W().ListenLog.outbox().map((r) => ({ track: r.track_id, session: r.session_id || null, origin: r.origin || null }));",
        setup="Date.now = (() => { let t = 1e12; return () => (t += 1000); })();"),
    # The session arrives after the song began: its row still carries it.
    "listen_session_late": _provider(
        f"W().__holdPost = '{S}';"
        "h.render(); P().playFromList([W().__t(10)], 0, null); await step();"
        "W().__releasePost(); await step(); await step();"
        "return { first: Q()[0] };",
        api={f"POST {S}": {"id": "S9", "mode": "library", "autoplay": True, "seed": {}, "picks": [],
                           "have_through_seq": 0}}),
}

# ─── the "up next" section with a scripted player ──────────────────────

SECTION_PRELUDE = r"""
API_BASE = '';
apiFetch = () => Promise.resolve(null);
window.__acts = [];
const __rec = (name, ret) => (...a) => { window.__acts.push([name].concat(a.map((x) => JSON.parse(JSON.stringify(x === undefined ? null : x))))); return ret; };
const __it = (n, extra) => Object.assign({ uid: 'u' + n, kind: 'library', trackId: n, title: 'Track ' + n,
  artist: 'Artist ' + n, album: '', src: '/a/' + n, coverUrl: null, durationSec: 200, seekable: true,
  cacheable: true, meta: {} }, extra || {});
window.__makeP = (over) => {
  const queue = [__it(1, { origin: 'explicit', sessionId: 'S1' }), __it(2),
    __it(3, { origin: 'auto', upNextSeq: 1, reasonLabel: 'same artist', sessionId: 'S1' }),
    __it(4, { origin: 'auto', upNextSeq: 2, reasonLabel: '90s', sessionId: 'S1' })];
  return Object.assign({
    available: true, queue, index: 0, target: { kind: 'browser' }, autoplay: true,
    upNext: { id: 'S1', mode: 'library', autoplay: true, person: null,
              seed: { track_id: 1, title: 'Feeling Good', list: null } },
    jumpTo: __rec('jumpTo'), removeAt: __rec('removeAt'), setAutoplay: __rec('setAutoplay', Promise.resolve()),
    setUpNextListener: __rec('setUpNextListener', Promise.resolve()),
    lessLikeThis: (i) => { window.__acts.push(['lessLikeThis', i]); return window.__less ? window.__less() : Promise.resolve([1, 9]); },
  }, over || {});
};
"""

SECTION_HELPERS = r"""
const cls = (c) => (e) => String((e.props && e.props.className) || '').split(' ').includes(c);
const W = () => h.global('window');
const texts = (c) => h.findAll(cls(c)).map((e) => e.text);
const byLabel = (re) => (e) => e.type === 'button' && re.test(e.props['aria-label'] || '');
const sw = () => h.find((e) => e.type === 'input' && e.props.role === 'switch');
const sec = () => h.find(cls('mp-un'));
const acts = () => W().__acts.slice();
"""

PEOPLE2 = {"GET /api/people": [{"id": 1, "name": "Alex"}, {"id": 2, "name": "Sam"}]}
PEOPLE1 = {"GET /api/people": [{"id": 1, "name": "Alex"}]}


def _section(script: str, *, p: dict | str | None = None, api: dict | None = None, setup: str = "") -> dict:
    p_js = p if isinstance(p, str) else json.dumps(p or {})
    return {
        "files": FILES,
        "component": "(() => PlayerUpNext({ p: window.__P }))",
        "api": api if api is not None else PEOPLE2,
        "setup": SECTION_PRELUDE + setup + f" window.__P = window.__makeP({p_js});",
        "script": SECTION_HELPERS + script,
    }


SHAPE = (
    "h.render(); return { section: !!sec(), names: texts('mp-un-name'), basis: texts('mp-un-basis'),"
    " titles: texts('mp-q-title'), why: texts('mp-un-why'), line: texts('mp-un-line'),"
    " sw: sw() ? { checked: sw().props.checked, label: sw().props['aria-label'] } : null,"
    " auto: h.find(cls('mp-un-auto')) ? h.find(cls('mp-un-auto')).props.title : null,"
    " forSel: h.find((e) => e.type === 'select') ? h.findAll((e) => e.type === 'option').map((o) => o.text) : null,"
    " less: h.findAll(byLabel(/^less like this/)).map((b) => [b.props['aria-label'], b.props.title]),"
    " remove: h.findAll(byLabel(/^remove /)).map((b) => b.props['aria-label']) };"
)

SCENARIOS.update({
    "sec_shape": _section(SHAPE),
    "sec_one_person": _section(SHAPE, api=PEOPLE1),
    "sec_list": _section(SHAPE, p="{ upNext: { id: 'S1', mode: 'list', autoplay: true, seed: { title: 'Song', list: { kind: 'playlist', name: 'Road Trip' } } } }"),
    "sec_continue": _section(SHAPE, p="{ upNext: { id: 'S1', mode: 'continue', autoplay: true, seed: { title: 'Song', list: { kind: 'playlist', name: 'Road Trip' } } } }"),
    "sec_list_off": _section(SHAPE, p="{ upNext: { id: 'S1', mode: 'list', autoplay: false, seed: { title: 'Song', list: { kind: 'playlist', name: 'Road Trip' } } } }"),
    "sec_off": _section(SHAPE, p="{ upNext: { id: 'S1', mode: 'library', autoplay: false, seed: { title: 'Feeling Good' } }, queue: window.__makeP().queue.slice(0, 2) }"),
    "sec_offline": _section(SHAPE, p="{ upNext: { offline: true, autoplay: true }, queue: window.__makeP().queue.slice(0, 2) }"),
    "sec_loading": _section(SHAPE, p="{ upNext: { loading: true, autoplay: true }, queue: window.__makeP().queue.slice(0, 2) }"),
    "sec_waiting": _section(SHAPE, p="{ upNext: null, queue: window.__makeP().queue.slice(0, 2) }"),
    "sec_casting": _section(SHAPE, p="{ target: { kind: 'room', roomId: 'kitchen' } }"),
    "sec_no_library": _section(SHAPE, p="{ upNext: null, queue: [{ uid: 'p', kind: 'podcast', trackId: null, title: 'Ep' }] }"),
    "sec_refused": _section(SHAPE, p="{ upNext: { refused: true } }"),
    "sec_guest": _section(SHAPE, setup="apiFetch = undefined;"),
    "sec_less": _section(
        "h.render(); await h.click(byLabel(/^less like this: Artist 3/));"
        "return { acts: acts(), note: texts('mp-q-note').join('|') };"),
    "sec_less_auth": _section(
        "h.render(); await h.click(byLabel(/^less like this: Artist 3/));"
        "return { note: texts('mp-q-note').join('|') };",
        setup="window.__less = () => Promise.reject(Object.assign(new Error('no'), { status: 401, loginPrompted: true }));"),
    "sec_less_fails": _section(
        "h.render(); await h.click(byLabel(/^less like this: Artist 3/));"
        "return { note: texts('mp-q-note').join('|') };",
        setup="window.__less = () => Promise.reject(Object.assign(new Error('500 boom'), { status: 500 }));"),
    "sec_remove_jump": _section(
        "h.render(); await h.click(byLabel(/^remove Track 4/));"
        "await h.click({ title: 'play now' });"
        "return { acts: acts() };"),
    "sec_toggle": _section(
        "h.render(); await h.fire((e) => e.type === 'input' && e.props.role === 'switch', 'onChange', { target: { checked: false } });"
        "return { acts: acts() };"),
    "sec_for": _section(
        "h.render(); await h.fire((e) => e.type === 'select', 'onChange', { target: { value: '2' } });"
        "await h.fire((e) => e.type === 'select', 'onChange', { target: { value: '' } });"
        "return { acts: acts() };"),
    "rows_leave_picks": {
        "files": FILES,
        "component": "(() => PlayerQueueRows({ queue: window.__P.queue, index: 0, jumpTo() {}, removeAt() {}, moveItem() {} }))",
        "api": {},
        "setup": SECTION_PRELUDE + " window.__P = window.__makeP({});",
        "script": SECTION_HELPERS + "h.render(); return { titles: texts('mp-q-title'),"
                  " count: W().playerQueueCount(W().__P.queue, 0), countLater: W().playerQueueCount(W().__P.queue, 3) };",
    },
})

# ─── the Music page ────────────────────────────────────────────────────

MUSIC_PRELUDE = r"""
window.__plays = [];
const __j = (x) => JSON.parse(JSON.stringify(x === undefined ? null : x));
usePlayback = () => ({
  available: true,
  playFromList: (list, at, ctx) => { window.__plays.push(['playFromList', list.map((t) => t.id), at, __j(ctx)]); return null; },
  playList: (list, ctx) => { window.__plays.push(['playList', list.map((t) => t.id), __j(ctx)]); return null; },
  playItems: () => null, enqueue() {}, playNext() {},
});
window.itemFromTrack = (t) => ({ title: t.title });
window.__rec = (name) => (...a) => { window.__plays.push([name].concat(a.map((x) => __j(Array.isArray(x) ? x.map((t) => t.id) : x)))); };
"""
LIB_TRACKS = [{"id": 31, "title": "Warm Stones", "artist": "Nina"}, {"id": 32, "title": "Dock", "artist": "Otis"}]
MUSIC_API = {"GET /api/playlists": [], "GET /api/music/library/stats": None,
             "GET /api/acquisitions?limit=100": None, "GET /api/music/now-playing": [],
             "GET /api/music/library": {"total": 2, "items": LIB_TRACKS}}
MUSIC_FILES = ["web/static/components.jsx", "web/static/music.jsx"]

SCENARIOS.update({
    "music_library_row": {
        "files": MUSIC_FILES, "component": "MusicPage", "api": MUSIC_API, "setup": MUSIC_PRELUDE,
        "script": "h.render(); await h.settle(); h.rerender();"
                  "await h.click({ type: 'button', title: 'play in this browser', nth: 1 }); await h.settle();"
                  "return h.global('window').__plays;",
    },
    "music_drawer_song": {
        "files": MUSIC_FILES,
        "component": "(() => TrackSetSongs({ songs: { items: [{ id: 41, title: 'A' }, { id: 42, title: 'B' }], loading: false },"
                     " sub: () => '', onSelectTrack() {}, onQueueTrack() {}, onBrowserPlay: window.__rec('onBrowserPlay'),"
                     " context: { kind: 'artist', ref: 'Nina', name: 'Nina' } }))",
        "api": {}, "setup": MUSIC_PRELUDE,
        "script": "h.render(); await h.click({ type: 'button', title: 'play in this browser', nth: 1 });"
                  "return h.global('window').__plays;",
    },
    "music_playlist_rows": {
        "files": MUSIC_FILES,
        "component": "(() => PlaylistDrawer({ playlist: { id: 7, name: 'Road Trip', track_count: 2 }, rooms: [],"
                     " onClose() {}, onPlay() {}, onShuffle() {}, onRemoveTrack: () => Promise.resolve(),"
                     " onDelete() {}, onEdit() {}, onReorder: () => Promise.resolve(), fire() {},"
                     " onBrowserPlay: window.__rec('onBrowserPlay'), onBrowserPlayTracks: window.__rec('onBrowserPlayTracks') }))",
        "api": {"GET /api/playlists/7/tracks": LIB_TRACKS}, "setup": MUSIC_PRELUDE,
        "script": "h.render(); await h.click((e) => e.type === 'button' && e.props['aria-label'] === 'play Dock in this browser');"
                  "await h.click((e) => e.type === 'button' && /play here/.test(e.text || ''));"
                  "return h.global('window').__plays;",
    },
    "music_virtual_playlist_row": {
        "files": MUSIC_FILES,
        "component": "(() => PlaylistDrawer({ playlist: { id: 'favorites', name: 'Favorites', is_virtual: true, track_count: 2 }, rooms: [],"
                     " onClose() {}, onPlay() {}, onShuffle() {}, onRemoveTrack: () => Promise.resolve(),"
                     " onDelete() {}, onEdit() {}, onReorder: () => Promise.resolve(), fire() {},"
                     " onBrowserPlay: window.__rec('onBrowserPlay'), onBrowserPlayTracks: window.__rec('onBrowserPlayTracks') }))",
        "api": {"GET /api/playlists/favorites/tracks": LIB_TRACKS}, "setup": MUSIC_PRELUDE,
        "script": "h.render(); await h.click((e) => e.type === 'button' && e.props['aria-label'] === 'play Warm Stones in this browser');"
                  "return h.global('window').__plays;",
    },
})


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("up_next") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), "@" + str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=240,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


# ─── the provider ───────────────────────────────────────────────────────


def test_a_song_clicked_in_a_list_plays_and_starts_up_next(driven) -> None:
    c = driven["click_starts"]
    method, path, body = c["posts"][0]
    assert (method, path) == ("POST", "/api/music/up-next/sessions")
    assert body == {"target": {"kind": "device", "device_id": "dev-1"},
                    "start": {"track_id": 11, "context": {"kind": "playlist", "ref": "7", "name": "Road Trip"}},
                    "autoplay": True}
    # The clicked song plays at once; the picks follow it as auto items.
    assert c["index"] == 0 and any("/api/music/library/11/audio" in p for p in c["played"])
    assert c["queue"] == [[11, "explicit", 0, "", "S1"], [12, "auto", 1, "same artist", "S1"],
                          [10, "auto", 2, "90s", "S1"]]
    assert c["un"]["id"] == "S1" and c["un"]["mode"] == "list" and c["un"]["haveThrough"] == 2
    # Two picks: five or fewer songs are left, so more are asked for at once.
    assert c["posts"][1] == ["POST", "/api/music/up-next/sessions/S1/next", {"have_through_seq": 2}]


def test_an_ids_list_sends_its_songs_and_a_lone_song_sends_none(driven) -> None:
    ctx = driven["click_ids"]["posts"][0][2]["start"]["context"]
    assert ctx == {"kind": "ids", "name": 'results for "x"', "track_ids": [10, 11, 12]}
    alone = driven["click_alone"]
    assert alone["posts"][0][2]["start"] == {"track_id": 10}
    assert alone["queue"] == [[10, "explicit", 0, "", "S1"], [40, "auto", 1, "same artist", "S1"]]


def test_the_queue_refills_when_five_songs_are_left(driven) -> None:
    r = driven["refill"]
    assert r["before"] == 0                         # six left: nothing asked
    assert [b[2] for b in r["nexts"]] == [{"have_through_seq": 6}]
    # seq 5 was held already, 6 was taken back, 7 and 8 are new.
    assert [q[2] for q in r["queue"]] == [0, 1, 2, 3, 4, 5, 7, 8]
    assert r["queue"][6][3] == "wildcard"
    assert r["un"]["haveThrough"] == 8 and r["index"] == 1


def test_a_small_lists_session_going_on_in_the_library_moves_the_mode(driven) -> None:
    g = driven["list_goes_on"]
    assert g["un"]["mode"] == "continue" and g["un"]["haveThrough"] == 3
    assert [q[:4] for q in g["queue"]] == [[10, "explicit", 0, ""], [11, "auto", 1, "same artist"],
                                          [40, "auto", 2, "wildcard"], [41, "auto", 3, "90s"]]
    assert g["basis"] == "more like Road Trip"   # the scripted session's list


def test_a_failed_refill_is_asked_again_with_the_same_number(driven) -> None:
    r = driven["refill_retry"]
    assert [b[2] for b in r["nexts"]][:2] == [{"have_through_seq": 2}, {"have_through_seq": 2}]
    assert r["failedOffline"] is True and r["after"]["offline"] is False
    assert [q[2] for q in r["queue"]] == [0, 1, 2, 3]


def test_autoplay_off_drops_a_mixs_picks_and_asks_for_no_more(driven) -> None:
    a = driven["autoplay_off"]
    assert a["patches"] == [["PATCH", "/api/music/up-next/sessions/S1", {"autoplay": False}]]
    assert a["queue"] == [[10, "explicit", 0, "", "S1"]]
    assert a["nexts"] == 0
    assert a["stored"] == "0" and a["pref"] is False
    # A list keeps the rest of itself.
    assert [q[0] for q in driven["autoplay_off_list"]["queue"]] == [10, 11, 12]
    assert driven["starts_off"]["posts"][0][2]["autoplay"] is False


def test_a_whole_list_plays_as_it_is_and_up_next_waits_for_its_end(driven) -> None:
    w = driven["whole_list"]
    body = w["first"]["posts"][0][2]
    assert body["start"] == {"context": {"kind": "playlist", "ref": "7", "name": "Road Trip"},
                             "whole_list": True, "track_id": 10}
    assert [q[:2] for q in w["first"]["queue"]] == [[n, "list"] for n in range(10, 18)]
    assert all(q[4] == "S1" for q in w["first"]["queue"])
    assert w["atOne"] == 0                           # six left after song 2
    assert [b[2] for b in w["nexts"]] == [{"have_through_seq": 0}]
    assert w["queue"][-1] == [50, "auto", 1, "from road trip", "S1"]
    ids = driven["whole_list_ids"]["posts"][0][2]["start"]
    assert ids["context"] == {"kind": "ids", "name": "this list", "track_ids": [10, 11, 12]}


def test_a_library_queue_without_a_session_gets_one_near_its_end(driven) -> None:
    q = driven["queue_without_session"]
    assert len(q["posts"]) == 1
    start = q["posts"][0][2]["start"]
    assert start == {"context": {"kind": "ids", "name": "this queue", "track_ids": [10, 11]},
                     "whole_list": True, "track_id": 10}
    assert [it[4] for it in q["queue"]] == ["S1", "S1"]


def test_less_like_this_and_remove_reach_the_session(driven) -> None:
    lt = driven["less_like_this"]
    assert lt["fb"] == [["POST", "/api/music/up-next/sessions/S1/feedback", {"seq": 1, "kind": "less_like_this"}]]
    assert sorted(lt["gone"]) == [1, 3]
    assert [q[2] for q in lt["queue"]] == [0, 2]
    rm = driven["remove_pick"]
    assert rm["fb"] == [["POST", "/api/music/up-next/sessions/S1/feedback", {"seq": 2, "kind": "remove"}]]
    assert [q[2] for q in rm["queue"]] == [0, 1]


def test_explicit_adds_land_before_the_picks(driven) -> None:
    q = driven["enqueue_before_picks"]["queue"]
    assert [x[0] for x in q] == [10, 98, 99, 21, 22]
    assert [x[1] for x in q] == ["explicit", "", "", "auto", "auto"]


def test_unreachable_says_so_and_tries_again_refused_stays_quiet(driven) -> None:
    o = driven["offline"]
    assert o["un"]["offline"] is True and o["queue"] == [[10, "explicit", 0, "", ""]]
    u = driven["unreachable_then_back"]
    assert u["first"]["offline"] is True and u["posts"] == 2
    assert u["un"]["id"] == "S1" and u["queue"][-1][1] == "auto"
    r = driven["refused"]
    assert r["un"]["refused"] is True and r["posts"] == 1


def test_a_play_made_meanwhile_wins(driven) -> None:
    w = driven["play_wins"]
    assert w["un"] is None and w["queue"] == ["podcast"]


def test_whose_mix_goes_with_the_start_and_the_for_selector(driven) -> None:
    assert driven["listener_person"]["posts"][0][2]["listener_person_id"] == 5
    hh = driven["listener_household"]["posts"][0][2]
    assert hh["as_household"] is True and "listener_person_id" not in hh
    f = driven["for_selector"]
    assert f["patches"] == [["PATCH", "/api/music/up-next/sessions/S1", {"listener_person_id": 2}]]
    assert f["person"] == {"id": 2, "name": "Sam"} and f["listening"] == 2


def test_the_listening_history_carries_the_session_back(driven) -> None:
    assert driven["listen_rows"] == [{"track": 5, "session": "S1", "origin": "auto"},
                                     {"track": 6, "session": None, "origin": None}]
    assert driven["listen_session_late"]["first"][4] == "S9"


# ─── the "up next" section ──────────────────────────────────────────────


def test_the_section_shows_the_picks_with_their_reasons(driven) -> None:
    s = driven["sec_shape"]
    assert s["section"] and s["names"] == ["up next"]
    assert s["basis"] == ["based on Feeling Good"]
    assert s["titles"] == ["Track 3", "Track 4"]
    assert s["why"] == ["same artist", "90s"]
    assert s["sw"] == {"checked": True, "label": "autoplay: keep playing similar songs when the queue ends"}
    assert s["auto"] == "keep playing similar songs when the queue ends"
    assert s["line"] == []
    assert s["less"][0] == ["less like this: Artist 3", "less like this: no more Artist 3 in this mix"]
    assert s["remove"] == ["remove Track 3", "remove Track 4"]
    assert s["forSel"] == ["household", "Alex", "Sam"]


def test_for_needs_two_people(driven) -> None:
    assert driven["sec_one_person"]["forSel"] is None


def test_the_head_says_what_the_mix_is_built_from(driven) -> None:
    assert driven["sec_list"]["basis"] == ["shuffled from Road Trip"]
    assert driven["sec_continue"]["basis"] == ["more like Road Trip"]


def test_the_one_line_states(driven) -> None:
    assert driven["sec_off"]["line"] == ["autoplay is off — the queue stops at the end"]
    assert driven["sec_off"]["sw"]["checked"] is False
    lo = driven["sec_list_off"]
    assert lo["titles"] == ["Track 3", "Track 4"]
    assert lo["line"] == ["autoplay is off — stops after the last song of Road Trip"]
    assert driven["sec_offline"]["line"] == ["up next needs the home server"]
    assert driven["sec_loading"]["line"] == ["finding songs like this one…"]
    assert driven["sec_waiting"]["line"] == ["similar songs join here when the queue is nearly over"]
    assert driven["sec_waiting"]["forSel"] is None          # no session to follow yet
    # While casting, the section is the ROOM's own (P4, test_web_up_next_rooms.py):
    # a room with no Up next shows none, never this browser's copy.
    assert driven["sec_casting"]["section"] is False


def test_no_section_where_it_would_mislead(driven) -> None:
    for name in ("sec_no_library", "sec_refused", "sec_guest"):
        assert driven[name]["section"] is False, name


def test_section_actions(driven) -> None:
    less = driven["sec_less"]
    assert less["acts"] == [["lessLikeThis", 2]]
    assert less["note"] == "No more Artist 3 in this mix (1 more taken out)."
    assert driven["sec_less_auth"]["note"] == ""
    assert "Couldn't change the mix" in driven["sec_less_fails"]["note"]
    assert driven["sec_remove_jump"]["acts"] == [["removeAt", 3], ["jumpTo", 2]]
    assert driven["sec_toggle"]["acts"] == [["setAutoplay", False]]
    assert driven["sec_for"]["acts"] == [["setUpNextListener", 2], ["setUpNextListener", None]]


def test_the_queue_rows_leave_the_picks_to_the_section(driven) -> None:
    r = driven["rows_leave_picks"]
    assert r["titles"] == ["Track 1", "Track 2"]
    assert r["count"] == 2 and r["countLater"] == 4      # played picks count as queue


# ─── the Music page ─────────────────────────────────────────────────────


def test_music_page_plays_start_up_next_with_their_list(driven) -> None:
    # The whole library, unfiltered: the song on its own (library mode).
    assert driven["music_library_row"] == [["playFromList", [31, 32], 1, None]]
    assert driven["music_drawer_song"] == [
        ["onBrowserPlay", {"id": 42, "title": "B"}, {"kind": "artist", "ref": "Nina", "name": "Nina"}, [41, 42]]]
    pl = driven["music_playlist_rows"]
    ctx = {"kind": "playlist", "ref": "7", "name": "Road Trip"}
    assert pl[0] == ["onBrowserPlay", LIB_TRACKS[1], ctx, [31, 32]]
    assert pl[1] == ["onBrowserPlayTracks", [31, 32], "Road Trip", ctx]
    fav = driven["music_virtual_playlist_row"]
    assert fav == [["onBrowserPlay", LIB_TRACKS[0], {"kind": "ids", "name": "Favorites"}, [31, 32]]]


# ─── source checks ──────────────────────────────────────────────────────


def _src(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_both_queue_panels_draw_the_up_next_section() -> None:
    src = _src("player.jsx")
    # PlayerQueueView (P5) draws both panels: the section inside the
    # floating panel's list, after the list in the sheet.
    assert src.count("{!sheet && <PlayerUpNext p={p}/>}") == 1
    assert src.count("{sheet && <PlayerUpNext p={p}/>}") == 1
    assert src.count("<PlayerQueueView p={p}") == 2
    assert src.count("queue · {playerQueueCount(p.queue, p.index)}") == 1


def test_the_section_styles_exist_and_phone_targets_are_44px() -> None:
    css = _src("styles.css")
    for sel in (".mp-un ", ".mp-un-head", ".mp-un-auto input", ".mp-un-why", ".mp-un-line", ".mp-un-row"):
        assert sel in css, sel
    assert re.search(r"\.mp-sheet \.mp-un-auto \{ min-height: 44px;", css)
    # Colours only through the tokens.
    block = css[css.index("/* The queue's \"up next\" section"):css.index(".mp-un .mp-q-note")]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|(?<![-\w])(white|black)(?![-\w])", block)


def test_the_music_handlers_go_through_up_next() -> None:
    src = _src("music.jsx")
    assert "player.playFromList(" in src and "player.playList(" in src
    assert src.count("onBrowserPlay(t, context, loaded)") == 1
    assert "onBrowserPlay(t, rowContext, items)" in src
    assert "onBrowserPlay(t, context, items)" in src            # playlist drawer rows
