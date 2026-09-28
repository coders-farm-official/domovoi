"""The Home page (web/static/home.jsx), driven outside a browser.

Home is the page every browser lands on (design-notes HOME-PLAN.md,
approved 2026-09-26), so the tests pin what that page does for each kind
of visitor and what it must never do:

* the four auth views — an unclaimed box, an unpaired browser, a paired
  phone, an admin — and that a non-admin's landing never issues an admin
  read or opens a sign-in / pair prompt (the request log is the proof);
* HOME_PROBLEMS_VISIBILITY's three modes (everyone / summary / admins) and
  the shared-screen cap (one neutral line at most);
* shared-screen masking: calendar times and "busy", "reminder · office",
  no personal tiles; rooms, timers, announce and stop keep working;
* nothing private anywhere on the page: no Wi-Fi name, no event
  description, and no read of transcripts, notes, memories or people;
* timers: the countdown against the server's clock, the elapsed bar, the
  "done · kitchen" line for a minute after one fires, and cancel through
  the pair prompt and replay;
* rooms: per-room transport, the "play" sheet (favorites, shuffled),
  "stop all" needing a second tap, "last known" when the core is down,
  one debounced re-read for a burst of pushes;
* the first-run hint, the empty states, and the phone's everything grid;
* data.js's `quiet` read option, which is what keeps a refused landing
  read from opening a prompt.

The page is compiled with the dashboard's own Babel and run by
domovoi/tests/jsx_interact_harness.js on top of the REAL auth.js and
data.js: only ``fetch`` and ``WebSocket`` are scripted (PRELUDE below), so
the quiet flag, the pair-and-replay path and the push-driven re-reads are
the production code. Node runs with TZ=UTC so "today" is deterministic.

No DB, never ``requires_db``; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")

FILES = [
    "web/static/auth.js", "web/static/data.js", "web/static/components.jsx",
    "web/static/satellites.jsx", "web/static/calendar.jsx", "web/static/home.jsx",
]
COMPONENT = ("(window.__Auth = Auth, window.__DI = DeviceIdentity,"
             " window.__rows = HomeAttentionRows, HomePage)")

NOW = 1790330400000          # fri 25 sep 2026, 10:00 UTC
MIN = 60 * 1000


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


ADMIN_PATHS = {"/api/satellites/approvals", "/api/satellites/pending",
               "/api/config/version", "/api/models/hardware"}
PRIVATE_PATH = re.compile(r"conversations|/notes|memories|/api/people|/api/chat|transcript")

# ─── the in-sandbox prelude: a scripted fetch, a stub socket, a clock ────

PRELUDE = r"""
let __now = __NOW0;
Date.now = () => __now;
window.__setNow = (ms) => { __now = ms; };
// Timers never hold the harness open (a toast's 12 s, a badge's 30 s poll).
const __st = setTimeout;
setTimeout = (fn, ms, ...a) => { const t = __st(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };
const __store = new Map(Object.entries(__LS || {}));
localStorage = {
  getItem: (k) => (__store.has(k) ? __store.get(k) : null),
  setItem: (k, v) => { __store.set(k, String(v)); },
  removeItem: (k) => { __store.delete(k); },
};
document.hidden = false;
// Listeners are recorded, so a scenario can fire `focus`; intervals are
// recorded and never run on their own, so a scenario ticks them by period.
window.__listeners = [];
window.addEventListener = (t, fn) => { window.__listeners.push({ t, fn }); };
window.removeEventListener = (t, fn) => {
  window.__listeners = window.__listeners.filter((l) => !(l.t === t && l.fn === fn));
};
document.addEventListener = window.addEventListener;
document.removeEventListener = window.removeEventListener;
window.__fire = (t) => window.__listeners.filter((l) => l.t === t).forEach((l) => l.fn({ type: t }));
window.__intervals = new Map();
let __iid = 0;
setInterval = (fn, ms) => { __iid += 1; window.__intervals.set(__iid, { fn, ms }); return __iid; };
clearInterval = (id) => { window.__intervals.delete(id); };
window.__every = (ms) => [...window.__intervals.values()].filter((x) => x.ms === ms).forEach((x) => x.fn());
window.__sockets = [];
WebSocket = function (url, protocols) { this.l = {}; window.__sockets.push(this); };
WebSocket.prototype.addEventListener = function (t, fn) { (this.l[t] = this.l[t] || []).push(fn); };
WebSocket.prototype.send = function () {};
window.__wsOpen = () => window.__sockets.forEach((s) => (s.l.open || []).forEach((f) => f()));
window.__wsClose = () => window.__sockets.forEach((s) => (s.l.close || []).forEach((f) => f()));
window.__wsEmit = (msg) => window.__sockets.forEach(
  (s) => (s.l.message || []).forEach((f) => f({ data: JSON.stringify(msg) })));
window.__fetches = [];
window.__table = __TABLE;
fetch = async (url, opts) => {
  const method = String((opts && opts.method) || 'GET').toUpperCase();
  const path = String(url).replace(/^https?:\/\/[^/]+/, '');
  const headers = (opts && opts.headers) || {};
  window.__fetches.push({ method, path, device: headers['X-Device-Token'] || null,
                          body: opts && opts.body ? String(opts.body) : null,
                          keys: Object.keys(opts || {}) });
  let hit = window.__table[`${method} ${path}`];
  if (hit === undefined) hit = window.__table[`${method} ${path.split('?')[0]}`];
  if (hit && hit.__seq) hit = hit.__seq.length > 1 ? hit.__seq.shift() : hit.__seq[0];
  if (hit && hit.__hang) return new Promise(() => {});
  let status = 200; let body = hit;
  if (hit === undefined) { status = 404; body = { detail: 'not found' }; }
  else if (hit && typeof hit === 'object' && !Array.isArray(hit) && '__status' in hit) {
    status = hit.__status; body = hit.__body;
  }
  const text = body === undefined || body === null ? '' : JSON.stringify(body);
  return { ok: status >= 200 && status < 300, status, statusText: String(status),
           text: async () => text, json: async () => JSON.parse(text) };
};
if (__PHONE !== null) {
  window.matchMedia = () => ({ matches: __PHONE, addEventListener() {}, removeEventListener() {} });
}
if (__MANIFEST) window.DomovoiPluginManifest = __MANIFEST;
window.__flush = async (h, n = 6) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
window.__cls = (e) => String((e.props && e.props.className) || '').split(' ');
window.__deepText = (n) => {
  if (n == null || typeof n === 'boolean') return '';
  if (typeof n === 'string' || typeof n === 'number') return String(n);
  if (Array.isArray(n)) return n.map(window.__deepText).join('');
  return n.props ? window.__deepText(n.props.children) : '';
};
window.__snap = (h) => {
  const w = window;
  const byCls = (cls) => h.findAll((e) => w.__cls(e).includes(cls));
  const liveEl = h.find((e) => w.__cls(e).includes('home-live'));
  return {
    text: h.text(),
    blob: h.tree().map((e) => [e.text].concat(Object.values(e.props || {})
      .filter((v) => typeof v === 'string')).join(' ')).join('\n'),
    fetches: w.__fetches.map((f) => `${f.method} ${f.path.split('?')[0]}`),
    modal: w.__Auth.modalOpen,
    pair: w.__Auth.pairModalOpen,
    attention: byCls('home-att-row').map((e) => ({ key: e.props['data-key'] || null,
      href: e.props.href || null, text: w.__deepText(e), extra: w.__cls(e).includes('home-phone-extra') })),
    quiet: byCls('home-att-quiet').map((e) => w.__deepText(e)),
    rooms: byCls('home-room').filter((e) => e.props['data-room']).map((e) => ({
      room: e.props['data-room'], dim: w.__cls(e).includes('dim'), text: w.__deepText(e) })),
    buttons: h.findAll({ type: 'button' }).map((e) => ({
      title: e.props.title || null, text: w.__deepText(e), disabled: !!e.props.disabled })),
    tiles: byCls('home-tile').map((e) => ({ href: e.props.href, text: w.__deepText(e) })),
    links: h.findAll({ type: 'a' }).map((e) => e.props.href),
    timers: byCls('home-timer').map((e) => ({ id: e.props['data-timer'], text: w.__deepText(e),
      extra: w.__cls(e).includes('home-phone-extra') })),
    line: byCls('home-status-line').map((e) => w.__deepText(e)).join(''),
    soon: byCls('soon').map((e) => w.__deepText(e)),
    bars: byCls('home-bar').map((e) => {
      const inner = [].concat(e.props.children || [])[0];
      return inner && inner.props && inner.props.style ? inner.props.style.width : null;
    }),
    done: byCls('home-timer-done').map((e) => w.__deepText(e)),
    events: byCls('home-today-row').map((e) => ({ text: w.__deepText(e),
      extra: w.__cls(e).includes('home-phone-extra') })),
    live: liveEl ? w.__deepText(liveEl) : null,
    sheet: !!h.find((e) => w.__cls(e).includes('home-sheet')),
  };
};
"""

# ─── the house the scenarios start from ──────────────────────────────────

STATUS_CLAIMED = {"setup_complete": True, "authenticated": False}
STATUS_ADMIN = {"setup_complete": True, "authenticated": True}
STATUS_UNCLAIMED = {"setup_complete": False, "authenticated": False}
PAIRED_LS = {"domovoi-device-token": "house-token", "domovoi-shared-screen": "0"}
SHARED_LS = {"domovoi-device-token": "house-token", "domovoi-shared-screen": "1"}


def cfg(visibility: str = "everyone") -> dict:
    return {"bot_name": "Kamron's house", "tts_voice": "v", "rooms": [], "web_version": "x",
            "wake_word_min_clips": 3, "home_problems_visibility": visibility}


HEALTH_OK = {"status": "ok", "db_reachable": True, "domovoi_reachable": True, "stt": "ok"}
ACQ_NONE = {"acquisitions": [], "fulfillers": [], "can_fulfill_query": True,
            "can_fulfill_url": True, "core_reachable": True}


def song(title: str, artist: str, dur: int = 238) -> dict:
    return {"file": f"music/{title}.mp3", "title": title, "artist": artist, "duration_sec": dur}


def room(rid: str, status: str = "online", *, state: str | None = None, s: dict | None = None,
         elapsed: float = 30, **extra) -> dict:
    r = {"room_id": rid, "status": status, "last_connected_at": iso(NOW - 2 * 60 * MIN),
         "wifi": {"rx_mbits": 40.0, "tx_mbits": 20.0, "ssid": "SECRETNET"}}
    if state:
        r["now_playing"] = {"room_id": rid, "state": state, "song": s or song("Creep", "Radiohead"),
                            "elapsed_sec": elapsed}
    r.update(extra)
    return r


def timer(tid: int, *, left_s: int, total_s: int = 600, label: str | None = None,
          message: str | None = None, room_id: str | None = "kitchen") -> dict:
    return {"id": tid, "expires_at": iso(NOW + left_s * 1000),
            "created_at": iso(NOW + (left_s - total_s) * 1000), "label": label,
            "message": message, "room_id": room_id, "is_reminder": message is not None}


PASTA = timer(5, left_s=300, total_s=600, label="pasta")
EVENTS = [
    {"id": 1, "title": "Standup", "starts_at": iso(NOW - 30 * MIN), "ends_at": iso(NOW + 30 * MIN),
     "location": "Office", "description": "SECRET DESCRIPTION", "source": "local"},
    {"id": 2, "title": "Dentist", "starts_at": iso(NOW + 60 * MIN), "ends_at": iso(NOW + 120 * MIN),
     "location": "Main St", "description": "SECRET DESCRIPTION", "source": "local"},
]


def house(**over) -> dict:
    """A claimed house: kitchen playing, office offline, a pasta timer."""
    t = {
        "GET /api/auth/status": STATUS_CLAIMED,
        "GET /api/config": cfg(),
        "GET /api/health": HEALTH_OK,
        "GET /api/satellites": [room("kitchen", state="play"), room("office", "offline")],
        "GET /api/timers": {"server_now": iso(NOW), "timers": [PASTA]},
        "GET /api/plugins": {"plugins": []},
        "GET /api/acquisitions": ACQ_NONE,
        "GET /api/calendar/events": [],
        "GET /api/satellites/approvals": [],
        "GET /api/satellites/pending": [],
        "GET /api/config/version": {"restart_required": False, "last_update": None, "bad_sha": None},
        "GET /api/models/hardware": {"disk": {"percent": 40.0}},
    }
    t.update({k: v for k, v in over.items()})
    return t


def scenario(table: dict, script: str, *, ls: dict | None = None, phone: bool | None = None,
             counts: dict | None = None, manifest: dict | None = None,
             badges: dict | None = None, component: str = COMPONENT) -> dict:
    head = (f"const __NOW0 = {NOW}; const __TABLE = {json.dumps(table)};"
            f" const __LS = {json.dumps(ls or {})}; const __PHONE = {json.dumps(phone)};"
            f" const __MANIFEST = {json.dumps(manifest)};\n")
    props = {"counts": counts or {}}
    if badges is not None:
        props["badges"] = badges
    return {"files": FILES, "component": component, "props": props,
            "setup": head + PRELUDE,
            "script": "const w = h.global('window'); h.render(); await w.__flush(h);\n" + script}


SNAP = "return w.__snap(h);"

# ─── the scenarios ───────────────────────────────────────────────────────

SCENARIOS: dict[str, dict] = {}

# Auth views ---------------------------------------------------------------
SCENARIOS["unpaired"] = scenario(
    # /api/plugins refused: a landing read that 401s must still open nothing.
    house(**{"GET /api/plugins": {"__status": 401, "__body": {"detail": "admin session required"}}}),
    "const before = w.__snap(h);"
    "await h.click({ type: 'button', text: 'pair this phone' });"
    "return { before, pairAfterClick: w.__Auth.pairModalOpen };",
)
SCENARIOS["paired"] = scenario(
    house(**{"GET /api/calendar/events": EVENTS}),
    "const before = w.__snap(h); w.__wsOpen(); await w.__flush(h);"
    # auth.js's own status probe is a bare fetch; every read Home makes
    # goes through data.js.
    "return { before, after: w.__snap(h), devices: w.__fetches"
    ".filter((f) => f.method === 'GET' && f.path !== '/api/auth/status').map((f) => f.device) };",
    ls=PAIRED_LS,
)
SCENARIOS["admin"] = scenario(
    house(**{
        "GET /api/auth/status": STATUS_ADMIN,
        "GET /api/satellites/approvals": [{"room_id": "garage"}],
        "GET /api/satellites/pending": [{"pending_id": "p1", "status": "awaiting_provision"}],
        "GET /api/config/version": {"restart_required": True, "bad_sha": None,
                                    "last_update": {"status": "failed", "finished_at": iso(NOW - MIN)}},
        "GET /api/models/hardware": {"disk": {"percent": 93.4}},
    }),
    SNAP, ls=PAIRED_LS,
)
SCENARIOS["admin_clean"] = scenario(
    house(**{"GET /api/auth/status": STATUS_ADMIN,
             "GET /api/satellites": [room("kitchen", state="play")]}),
    SNAP, ls=PAIRED_LS,
)
SCENARIOS["admin_checking"] = scenario(
    house(**{"GET /api/auth/status": STATUS_ADMIN,
             "GET /api/satellites": [room("kitchen")],
             "GET /api/models/hardware": {"__hang": True}}),
    SNAP, ls=PAIRED_LS,
)
SCENARIOS["admin_check_failed"] = scenario(
    house(**{"GET /api/auth/status": STATUS_ADMIN,
             "GET /api/satellites": [room("kitchen")],
             "GET /api/models/hardware": {"__status": 502, "__body": {"detail": "core down"}}}),
    SNAP, ls=PAIRED_LS,
)
SCENARIOS["unclaimed"] = scenario(
    house(**{"GET /api/auth/status": STATUS_UNCLAIMED}),
    "const before = w.__snap(h);"
    "await h.click({ type: 'button', text: 'claim it' });"
    "return { before, modalAfterClick: w.__Auth.modalOpen };",
    # The boot register's answer (index.html), as a reload remembers it.
    ls={"domovoi-shared-screen": "0"},
)

# HOME_PROBLEMS_VISIBILITY -------------------------------------------------
for _mode in ("everyone", "summary", "admins"):
    SCENARIOS[f"visibility_{_mode}"] = scenario(
        house(**{"GET /api/config": cfg(_mode),
                 "GET /api/satellites": [room("kitchen"), room("office", "offline"),
                                         room("den", "offline")]}),
        SNAP, ls=PAIRED_LS,
    )
    SCENARIOS[f"visibility_{_mode}_admin"] = scenario(
        house(**{"GET /api/config": cfg(_mode), "GET /api/auth/status": STATUS_ADMIN,
                 "GET /api/satellites": [room("kitchen"), room("office", "offline")]}),
        SNAP, ls=PAIRED_LS,
    )
# The setting has not answered yet: a household member sees no rows.
SCENARIOS["visibility_not_known_yet"] = scenario(
    house(**{"GET /api/config": {"__hang": True}}), SNAP, ls=PAIRED_LS)

# Shared screen ------------------------------------------------------------
_SHARED_HOUSE = house(**{
    "GET /api/calendar/events": EVENTS,
    "GET /api/timers": {"server_now": iso(NOW), "timers": [
        PASTA, timer(6, left_s=900, total_s=3600, message="call mum about the tickets", room_id="office")]},
    "DELETE /api/timers/6": {"__status": 204},
})
SCENARIOS["shared"] = scenario(_SHARED_HOUSE, SNAP, ls=SHARED_LS, phone=True)
SCENARIOS["shared_admin"] = scenario(
    {**_SHARED_HOUSE, "GET /api/auth/status": STATUS_ADMIN}, SNAP, ls=SHARED_LS)
SCENARIOS["shared_cancel_reminder"] = scenario(
    _SHARED_HOUSE,
    "await h.click({ type: 'button', title: 'cancel reminder' }); await w.__flush(h); return w.__snap(h);",
    ls=SHARED_LS,
)
SCENARIOS["not_shared_same_house"] = scenario(_SHARED_HOUSE, SNAP, ls=PAIRED_LS, phone=True)
SCENARIOS["shared_flag_arrives"] = scenario(
    {**_SHARED_HOUSE, "POST /api/devices/register": {"device_id": "d", "name": "Kitchen tablet",
                                                     "shared_screen": True}},
    "const before = w.__snap(h); await w.__DI.register(); await w.__flush(h);"
    "return { before, after: w.__snap(h) };",
    ls=PAIRED_LS,
)
SCENARIOS["paired_no_answer_yet"] = scenario(
    _SHARED_HOUSE, SNAP, ls={"domovoi-device-token": "house-token"})
SCENARIOS["unpaired_no_answer"] = scenario(_SHARED_HOUSE, SNAP)

# Rooms --------------------------------------------------------------------
SCENARIOS["rooms"] = scenario(
    house(**{
        "GET /api/satellites": [
            room("attic", "offline"),
            room("garage", "waiting"),
            room("office"),
            room("den", state="pause", wifi={"rx_mbits": 3.0, "ssid": "SECRETNET"}),
            room("kitchen", state="play", in_call_with="den"),
            room("lounge", sat_type="video", display={"kiosk_alive": False}),
        ],
        "POST /api/music/pause/kitchen": {"ok": True},
        "POST /api/music/play-playlist": {"ok": True},
    }),
    "const before = w.__snap(h);"
    "await h.click({ type: 'button', title: 'pause kitchen' });"
    "await h.click({ type: 'button', title: 'play in office' });"
    "const sheetOpen = w.__snap(h).sheet;"
    "await h.click((e) => e.type === 'button' && w.__cls(e).includes('home-sheet-opt'));"
    "await w.__flush(h);"
    "return { before, sheetOpen, after: w.__snap(h),"
    " posts: w.__fetches.filter((f) => f.method === 'POST').map((f) => ({ path: f.path, body: f.body })) };",
    ls=PAIRED_LS,
)
# A read taken after the song ran out (the next song's read not in yet).
SCENARIOS["room_overrun"] = scenario(
    house(**{"GET /api/satellites": [room("kitchen", state="play", elapsed=300)]}),
    SNAP, ls=PAIRED_LS,
)
SCENARIOS["stop_all"] = scenario(
    house(**{"GET /api/satellites": [room("kitchen", state="play"), room("office", state="play"),
                                     room("den")],
             "POST /api/music/stop/kitchen": {"ok": True}, "POST /api/music/stop/office": {"ok": True}}),
    "const posts = () => w.__fetches.filter((f) => f.method === 'POST').map((f) => f.path);"
    "await h.click({ type: 'button', text: 'stop all' });"
    "const armed = { posts: posts(), snap: w.__snap(h) };"
    "await h.click({ type: 'button', text: 'stop 2 rooms?' }); await w.__flush(h);"
    "return { armed, posts: posts(), after: w.__snap(h) };",
    ls=PAIRED_LS,
)
SCENARIOS["one_playing_no_stop_all"] = scenario(house(), SNAP, ls=PAIRED_LS)
SCENARIOS["core_down"] = scenario(
    house(**{"GET /api/auth/status": STATUS_ADMIN,
             "GET /api/health": {"status": "degraded", "db_reachable": True,
                                 "domovoi_reachable": False, "stt": None}}),
    SNAP, ls=PAIRED_LS,
)
SCENARIOS["push_debounced"] = scenario(
    house(),
    "w.__wsOpen(); await w.__flush(h);"
    "const n = () => w.__fetches.filter((f) => f.path === '/api/satellites').length;"
    "const start = n();"
    "for (const t of ['satellites.presence.changed', 'music.now_playing.changed', 'satellites.wifi.changed'])"
    "  w.__wsEmit({ type: t, data: {} });"
    "const immediately = n() - start;"
    "await new Promise((r) => setTimeout(r, 450)); await w.__flush(h);"
    "return { immediately, afterDebounce: n() - start };",
    ls=PAIRED_LS,
)

# Timers -------------------------------------------------------------------
_T_PASTA = timer(5, left_s=65, total_s=120, label="pasta")
_T_ROAST = timer(7, left_s=20 * 60, total_s=40 * 60, label="roast", room_id=None)
_T_TEA = timer(8, left_s=500, total_s=600, label="tea")
SCENARIOS["timer_countdown"] = scenario(
    house(**{"GET /api/satellites": [room("kitchen")],
             "GET /api/timers": {"server_now": iso(NOW), "timers": [_T_PASTA, _T_TEA, _T_ROAST]}}),
    "const t0 = w.__snap(h);"
    f"w.__setNow({NOW + 10_000}); h.rerender(); const t10 = w.__snap(h);"
    f"w.__setNow({NOW + 66_000}); h.rerender(); const fired = w.__snap(h);"
    # The server fires pasta (and tea is cancelled by voice meanwhile): the
    # next read has only the roast, pushed on the timers channel.
    f"w.__table['GET /api/timers'] = {{ server_now: '{iso(NOW + 66_000)}', timers: [{json.dumps(_T_ROAST)}] }};"
    "w.__wsOpen(); w.__wsEmit({ type: 'timers.changed', data: [] }); await w.__flush(h);"
    "const pushed = w.__snap(h);"
    f"w.__setNow({NOW + 130_000}); h.rerender(); const later = w.__snap(h);"
    "return { t0, t10, fired, pushed, later };",
    ls=PAIRED_LS,
)
SCENARIOS["timer_skewed_phone"] = scenario(
    # The phone's clock runs 2 minutes fast; server_now keeps the countdown true.
    house(**{"GET /api/timers": {"server_now": iso(NOW - 2 * MIN), "timers": [
        {**PASTA, "expires_at": iso(NOW - 2 * MIN + 300_000), "created_at": iso(NOW - 2 * MIN - 300_000)}]}}),
    SNAP, ls=PAIRED_LS,
)
SCENARIOS["cancel_pair_replay"] = scenario(
    house(**{"DELETE /api/timers/5": {"__seq": [
        {"__status": 401, "__body": {"detail": "X-Device-Token or admin session required"}},
        {"__status": 204}]}}),
    "const deletes = () => w.__fetches.filter((f) => f.method === 'DELETE');"
    "await h.click({ type: 'button', title: 'cancel pasta timer' });"
    "const prompted = { pair: w.__Auth.pairModalOpen, deletes: deletes().length };"
    "w.__Auth.pair('house-token'); await w.__flush(h);"
    "return { prompted, deletes: deletes().map((f) => ({ path: f.path, device: f.device })),"
    " pairAfter: w.__Auth.pairModalOpen, after: w.__snap(h) };",
)
SCENARIOS["cancel_already_fired"] = scenario(
    house(**{"DELETE /api/timers/5": {"__status": 404, "__body": {"detail": "timer 5 not found"}}}),
    "await h.click({ type: 'button', title: 'cancel pasta timer' }); await w.__flush(h); return w.__snap(h);",
    ls=PAIRED_LS,
)

# Empty / first run --------------------------------------------------------
_EMPTY = house(**{"GET /api/satellites": [], "GET /api/timers": {"server_now": iso(NOW), "timers": []},
                  "GET /api/calendar/events": []})
SCENARIOS["first_run"] = scenario(
    {**_EMPTY, "GET /api/capabilities/manual": {"handlers": [
        {"name": "calculator", "example_phrases": ["what is 2 plus 2"]},
        {"name": "timer", "example_phrases": []},
        {"name": "clock", "example_phrases": ["what time is it"]}]}},
    SNAP, ls=PAIRED_LS,
)
SCENARIOS["first_run_manual_down"] = scenario(
    {**_EMPTY, "GET /api/capabilities/manual": {"__status": 502, "__body": {"detail": "x"}}},
    SNAP, ls=PAIRED_LS,
)
SCENARIOS["today_next"] = scenario(
    house(**{"GET /api/calendar/events": [
        {"id": 9, "title": "Dentist", "starts_at": iso(NOW + 4 * 24 * 60 * MIN), "ends_at": None,
         "location": None, "description": None, "source": "local"}]}),
    SNAP, ls=PAIRED_LS,
)
SCENARIOS["today_many"] = scenario(
    house(**{"GET /api/calendar/events": [
        {"id": i, "title": f"Event {i}", "starts_at": iso(NOW + i * 30 * MIN),
         "ends_at": iso(NOW + i * 30 * MIN + 20 * MIN), "location": None, "description": None,
         "source": "local"} for i in range(1, 9)]}),
    SNAP, ls=PAIRED_LS,
)

# The phone launcher --------------------------------------------------------
_RADIO = {"plugins": [{"slug": "radio", "pages": [
    {"route": "radio", "page": "RadioPage", "nav_label": "Radio", "nav_order": 11,
     "badge": {"endpoint": "/api/plugins/radio/badge", "key": "live"}}]}]}
SCENARIOS["phone_launcher"] = scenario(
    house(**{"GET /api/plugins/radio/badge": {"live": 3}}),
    "await w.__flush(h); return w.__snap(h);",
    ls=PAIRED_LS, phone=True, counts={"people": 4, "music": 9}, manifest=_RADIO,
)
# App's one badge poll, handed down: the grid shows it and polls nothing.
SCENARIOS["phone_launcher_given_badges"] = scenario(
    house(**{"GET /api/plugins/radio/badge": {"live": 3}}),
    "await w.__flush(h); return w.__snap(h);",
    ls=PAIRED_LS, phone=True, manifest=_RADIO, badges={"radio": 7},
)
SCENARIOS["desktop_no_launcher"] = scenario(
    house(**{"GET /api/plugins/radio/badge": {"live": 3}}),
    SNAP, ls=PAIRED_LS, phone=False, manifest=_RADIO,
)

# Fewer fan-outs ---------------------------------------------------------------
_N = "const n = (p) => w.__fetches.filter((f) => f.path.split('?')[0] === p).length;"
_COUNTS = "({ sats: n('/api/satellites'), timers: n('/api/timers') })"
# The socket's first open lands a moment after the mount reads: no second
# /api/satellites (an MPD connection per room). A real gap does re-read.
SCENARIOS["first_connect"] = scenario(
    house(),
    _N + f"const before = {_COUNTS};"
    f"w.__wsOpen(); await w.__flush(h); const first = {_COUNTS};"
    f"w.__wsClose(); await w.__flush(h); w.__setNow({NOW + 60_000}); w.__wsOpen(); await w.__flush(h);"
    f"return {{ before, first, back: {_COUNTS} }};",
    ls=PAIRED_LS,
)
# A push while the tab is hidden re-reads nothing until the tab is back;
# a Wi-Fi report is merged in and never re-read.
SCENARIOS["hidden_push"] = scenario(
    house(**{"GET /api/satellites": [room("kitchen", state="play"), room("den")]}),
    _N + "w.__wsOpen(); await w.__flush(h); const start = n('/api/satellites');"
    "const doc = h.global('document'); doc.hidden = true;"
    "w.__wsEmit({ type: 'satellites.presence.changed', data: [] });"
    "await new Promise((r) => setTimeout(r, 450)); await w.__flush(h);"
    "const whileHidden = n('/api/satellites') - start;"
    f"doc.hidden = false; w.__setNow({NOW + 20_000}); w.__fire('focus');"
    "await new Promise((r) => setTimeout(r, 450)); await w.__flush(h);"
    "const onReturn = n('/api/satellites') - start;"
    "w.__wsEmit({ type: 'satellites.wifi.changed', data: { den: { rx_mbits: 2.5, tx_mbits: 1.0,"
    " ssid: 'SECRETNET' } } });"
    "await new Promise((r) => setTimeout(r, 450)); await w.__flush(h);"
    "return { whileHidden, onReturn, afterWifi: n('/api/satellites') - start, snap: w.__snap(h) };",
    ls=PAIRED_LS,
)
# The admin probes survive Home re-mounting (a phone passes through it on
# the way to every other page); the cheap reads are simply made again.
_REMOUNT = ("(window.__Auth = Auth, (() => { const W = () => {"
            " const [on, set] = React.useState(true); window.__toggle = () => set((x) => !x);"
            " return on ? React.createElement(HomePage, { counts: {} }) : null; }; return W; })())")
SCENARIOS["remount"] = scenario(
    house(**{"GET /api/auth/status": STATUS_ADMIN}),
    _N + "w.__toggle(); await w.__flush(h); w.__toggle(); await w.__flush(h);"
    "return { hardware: n('/api/models/hardware'), version: n('/api/config/version'),"
    " approvals: n('/api/satellites/approvals'), health: n('/api/health'), snap: w.__snap(h) };",
    ls=PAIRED_LS, component=_REMOUNT,
)
SCENARIOS["remount_after_ttl"] = scenario(
    house(**{"GET /api/auth/status": STATUS_ADMIN}),
    _N + f"w.__toggle(); await w.__flush(h); w.__setNow({NOW + 6 * 60_000}); w.__toggle(); await w.__flush(h);"
    "return { hardware: n('/api/models/hardware'), version: n('/api/config/version') };",
    ls=PAIRED_LS, component=_REMOUNT,
)

# Honest when things are down ----------------------------------------------------
SCENARIOS["core_down_frozen"] = scenario(
    house(**{"GET /api/health": {"status": "degraded", "db_reachable": True,
                                 "domovoi_reachable": False, "stt": None}}),
    f"const t0 = w.__snap(h); w.__setNow({NOW + 60_000}); h.rerender();"
    "return { t0, t60: w.__snap(h) };",
    ls=PAIRED_LS,
)
SCENARIOS["sats_fail"] = scenario(
    house(**{"GET /api/satellites": {"__status": 500, "__body": {"detail": "boom"}}}),
    SNAP, ls=PAIRED_LS,
)
SCENARIOS["db_down_rooms"] = scenario(
    house(**{"GET /api/health": {"status": "degraded", "db_reachable": False,
                                 "domovoi_reachable": True, "stt": "ok"},
             "GET /api/satellites": {"__status": 503, "__body": {"detail": "db"}}}),
    SNAP, ls=PAIRED_LS,
)
# An admin with a problem row AND a check that failed: both are said.
SCENARIOS["admin_rows_and_failed"] = scenario(
    house(**{"GET /api/auth/status": STATUS_ADMIN,
             "GET /api/models/hardware": {"__status": 502, "__body": {"detail": "core down"}}}),
    SNAP, ls=PAIRED_LS,
)
# The admin's in-memory sign-in is stale: the quiet admin reads 401.
SCENARIOS["admin_refused"] = scenario(
    house(**{"GET /api/auth/status": STATUS_ADMIN,
             "GET /api/models/hardware": {"__status": 401, "__body": {"detail": "admin session required"}},
             "GET /api/config/version": {"__status": 401, "__body": {"detail": "admin session required"}}}),
    "const before = w.__snap(h);"
    "await h.click({ type: 'button', text: 'sign in again' });"
    "return { before, modal: w.__Auth.modalOpen };",
    ls=PAIRED_LS,
)

# One press, one request ----------------------------------------------------------
SCENARIOS["cancel_double_tap"] = scenario(
    house(**{"DELETE /api/timers/5": {"__hang": True}}),
    "await h.click({ type: 'button', title: 'cancel pasta timer' });"
    "await h.click({ type: 'button', title: 'cancel pasta timer' });"
    "return { deletes: w.__fetches.filter((f) => f.method === 'DELETE').length, snap: w.__snap(h) };",
    ls=PAIRED_LS,
)
SCENARIOS["stop_all_busy"] = scenario(
    house(**{"GET /api/satellites": [room("kitchen", state="play"), room("office", state="play")],
             "POST /api/music/stop/kitchen": {"__hang": True},
             "POST /api/music/stop/office": {"__hang": True}}),
    "await h.click({ type: 'button', text: 'stop all' });"
    "await h.click({ type: 'button', text: 'stop 2 rooms?' }); await w.__flush(h);"
    "return w.__snap(h);",
    ls=PAIRED_LS,
)

# The attention rules on their own ---------------------------------------------
_V = {"known": True, "unclaimed": False, "paired": True, "isAdmin": True, "canRegister": True}
_RULE_CASES = {
    "stt_fallback": {"health": {**HEALTH_OK, "stt": "fallback"}},
    "stt_off": {"health": {**HEALTH_OK, "stt": "unavailable"}},
    "stt_not_loaded": {"health": {**HEALTH_OK, "stt": "not_loaded"}},
    "kiosk": {"rooms": [room("lounge", sat_type="video", display={"kiosk_alive": False})]},
    "offline_two_waiting_one": {"rooms": [room("a", "offline"), room("b", "offline"), room("c", "waiting")]},
    "plugin_one": {"plugins": {"plugins": [{"slug": "radio", "name": "Radio", "enabled": True,
                                            "status": "load_error"}]}},
    "plugin_degraded": {"plugins": {"plugins": [{"slug": "radio", "name": "Radio", "enabled": True,
                                                 "status": "degraded"}]}},
    "plugin_browser_error": {"plugins": {"plugins": [{"slug": "radio", "name": "Radio", "enabled": True,
                                                      "status": "ok"}]},
                             "pluginErrors": {"radio": [{"phase": "render", "message": "x", "count": 1}]}},
    "plugins_two": {"plugins": {"plugins": [
        {"slug": "radio", "name": "Radio", "enabled": True, "status": "ok", "web_load_error": "boom"},
        {"slug": "sleep", "name": "Sleep", "enabled": True, "status": "ok", "page_errors": ["dup route"]},
        {"slug": "off", "name": "Off", "enabled": False, "status": "ok", "page_errors": ["x"]}]}},
    "acq_stuck": {"acq": {**ACQ_NONE, "can_fulfill_query": False, "acquisitions": [
        {"id": 1, "kind": "query", "status": "pending", "text": "PRIVATE REQUEST"},
        {"id": 2, "kind": "url", "status": "pending", "text": "x"}]}},
    "update_rolled_back": {"version": {"restart_required": False, "bad_sha": "a" * 40,
                                       "last_update": {"status": "rolled_back"}}},
    # A plugin upgrade staged for restart, with and without pulled code.
    "restart_plugin_only": {"version": {"restart_required": True, "code_restart_required": False,
                                         "plugins_pending_restart": [
                                             {"slug": "radio", "from_version": "1.1.0",
                                              "to_version": "1.2.0", "where": ["core"]}]}},
    "restart_plugins_and_code": {"version": {"restart_required": True, "code_restart_required": True,
                                              "plugins_pending_restart": [
                                                  {"slug": "radio"}, {"slug": "sleep"}]}},
    "restart_two_plugins": {"version": {"restart_required": True, "code_restart_required": False,
                                         "plugins_pending_restart": [{"slug": "radio"}, {"slug": "sleep"}]}},
    "disk_full": {"hardware": {"disk": {"percent": 96.2}}},
    "disk_ok": {"hardware": {"disk": {"percent": 89.9}}},
    "db_down": {"health": {"status": "degraded", "db_reachable": False, "domovoi_reachable": True},
                "rooms": [room("a", "offline")], "hardware": {"disk": {"percent": 99}}},
    "core_down": {"health": {"status": "degraded", "db_reachable": True, "domovoi_reachable": False,
                             "stt": "unavailable"},
                  "rooms": [room("a", "offline")], "approvals": [{"room_id": "x"}]},
    "unclaimed_first": {"viewer": {**_V, "unclaimed": True}, "hardware": {"disk": {"percent": 91}},
                        "rooms": [room("a", "offline")], "health": {**HEALTH_OK, "stt": "unavailable"}},
}
SCENARIOS["rules"] = scenario(
    house(),
    "const base = { viewer: " + json.dumps(_V) + ", health: " + json.dumps(HEALTH_OK)
    + ", rooms: [], plugins: { plugins: [] }, pluginErrors: {}, acq: null, approvals: null,"
    " pending: null, version: null, hardware: null };"
    "const cases = " + json.dumps(_RULE_CASES) + ";"
    "const out = {};"
    "for (const [k, c] of Object.entries(cases)) out[k] = w.__rows({ ...base, ...c })"
    ".map((r) => ({ key: r.key, tone: r.tone, scope: r.scope, text: r.text }));"
    "return out;",
)

# data.js `quiet` ----------------------------------------------------------------
SCENARIOS["quiet_option"] = scenario(
    {"GET /api/x": {"__status": 401, "__body": {"detail": "admin session required"}},
     "GET /api/y": {"__status": 401, "__body": {"detail": "X-Device-Token or admin session required"}},
     "POST /api/z": {"__status": 401, "__body": {"detail": "X-Device-Token or admin session required"}}},
    "const fail = (p) => p.then(() => ({ ok: true }), (e) => ({ status: e.status,"
    " loginPrompted: !!e.loginPrompted, authCancelled: !!e.authCancelled }));"
    "const A = w.__Auth;"
    "const quietAdmin = await fail(w.apiGet('/api/x', { quiet: true }));"
    "const q1 = { modal: A.modalOpen, pair: A.pairModalOpen };"
    "const quietDevice = await fail(w.apiGet('/api/y', { quiet: true }));"
    "const q2 = { modal: A.modalOpen, pair: A.pairModalOpen };"
    "const keys = w.__fetches[w.__fetches.length - 1].keys;"
    "const loud = await fail(w.apiGet('/api/x'));"
    "const q3 = { modal: A.modalOpen, pair: A.pairModalOpen }; A.closeModal();"
    # A mutation ignores quiet: a press still prompts (and replays).
    "const pending = fail(w.apiFetch('/api/z', { method: 'POST', body: '{}', quiet: true }));"
    "await h.settle(); const q4 = { pair: A.pairModalOpen }; A.closePairModal();"
    "const mutation = await pending;"
    "return { quietAdmin, q1, quietDevice, q2, keys, loud, q3, q4, mutation };",
    component="(window.__Auth = Auth, () => null)",
)

# The shell's boot register (index.html → DeviceIdentity.boot) ---------------
# Home is where every browser lands, so the shell's own device registration
# must not open the pair prompt on a guest's phone either.
_REG_ROW = {"device_id": "browser-x", "name": "Chrome on Linux", "shared_screen": False}
_REFUSED = {"__status": 401, "__body": {"detail": "X-Device-Token or admin session required"}}
_BOOT_SNAP = ("({ posts: w.__fetches.filter((f) => f.method === 'POST' && f.path === '/api/devices/register')"
              ".map((f) => f.device), pair: w.__Auth.pairModalOpen, modal: w.__Auth.modalOpen })")


def boot(table: dict, script: str, *, ls: dict | None = None) -> dict:
    # boot() twice: the shell calls it once, and a second call is a no-op.
    return scenario(table, "await w.__DI.boot(); await w.__DI.boot(); await w.__flush(h);" + script,
                    ls=ls, component="(window.__Auth = Auth, window.__DI = DeviceIdentity, () => null)")


SCENARIOS["boot_unpaired"] = boot(
    {"GET /api/auth/status": STATUS_CLAIMED, "POST /api/devices/register": _REFUSED},
    f"const before = {_BOOT_SNAP};"
    f"w.__table['POST /api/devices/register'] = {json.dumps(_REG_ROW)};"
    "w.__Auth.pair('house-token'); await w.__flush(h);"
    f"return {{ before, paired: {_BOOT_SNAP} }};",
)
SCENARIOS["boot_paired"] = boot(
    {"GET /api/auth/status": STATUS_CLAIMED, "POST /api/devices/register": _REG_ROW},
    f"return {_BOOT_SNAP};", ls=PAIRED_LS,
)
SCENARIOS["boot_unclaimed"] = boot(
    {"GET /api/auth/status": STATUS_UNCLAIMED, "POST /api/devices/register": _REG_ROW},
    f"return {_BOOT_SNAP};",
)
SCENARIOS["boot_admin_signs_in"] = boot(
    {"GET /api/auth/status": STATUS_CLAIMED, "POST /api/devices/register": _REG_ROW,
     "POST /api/auth/login": {"token": "admin-bearer"},
     "GET /api/auth/device-token": {"token": "house-token"}},
    f"const before = {_BOOT_SNAP};"
    "await w.__Auth.login('pw'); await w.__flush(h);"
    f"return {{ before, after: {_BOOT_SNAP} }};",
)
# The household token was rotated since this browser stored it: the boot
# register and a later refresh (Home's focus handler) are both refused,
# and neither opens the pair prompt.
SCENARIOS["boot_stale_token"] = boot(
    {"GET /api/auth/status": STATUS_CLAIMED, "POST /api/devices/register": _REFUSED},
    f"const booted = {_BOOT_SNAP};"
    "await w.__DI.refresh(); await w.__flush(h);"
    f"return {{ booted, refreshed: {_BOOT_SNAP} }};",
    ls=PAIRED_LS,
)
# The same stale token, on Home, when the tab gets focus back (the shell's
# boot owns that re-register, at most every 15 s).
SCENARIOS["focus_stale_token"] = scenario(
    house(**{"POST /api/devices/register": _REFUSED}),
    "await w.__DI.boot(); await w.__flush(h);"
    "w.__fire('focus'); await w.__flush(h); const tooSoon = " + _BOOT_SNAP + ".posts.length;"
    f"w.__setNow({NOW + 20_000}); w.__fire('focus'); await w.__flush(h);"
    f"return {{ tooSoon, ...{_BOOT_SNAP} }};",
    ls=PAIRED_LS,
)
# A kitchen tablet sits on Home and never loses focus. An admin marks it a
# shared screen, and sets the problem rows to admins only, from their phone:
# no focus event, no reload — the page still catches up on its own timers.
SCENARIOS["tick_marks_the_tablet"] = scenario(
    house(**{"GET /api/calendar/events": EVENTS,
             "GET /api/satellites": [room("kitchen"), room("office", "offline")],
             "POST /api/devices/register": _REG_ROW}),
    "await w.__DI.boot(); await w.__flush(h); const before = w.__snap(h);"
    "w.__table['POST /api/devices/register'] = { ...w.__table['POST /api/devices/register'], shared_screen: true };"
    "w.__every(120000); await w.__flush(h);"
    "return { before, after: w.__snap(h) };",
    ls=PAIRED_LS,
)
SCENARIOS["tick_rereads_the_setting"] = scenario(
    house(**{"GET /api/satellites": [room("kitchen"), room("office", "offline")]}),
    "const before = w.__snap(h);"
    f"w.__table['GET /api/config'] = {json.dumps(cfg('admins'))};"
    "w.__every(60000); await w.__flush(h);"
    "return { before, after: w.__snap(h) };",
    ls=PAIRED_LS,
)


# ─── run ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("home") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), "@" + str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        env={**os.environ, "TZ": "UTC"},
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def _fetched(snap: dict) -> set[str]:
    return {f.split(" ", 1)[1] for f in snap["fetches"]}


def _titles(snap: dict) -> set[str]:
    return {b["title"] for b in snap["buttons"] if b["title"]}


def _room(snap: dict, rid: str) -> dict:
    return next(r for r in snap["rooms"] if r["room"] == rid)


# ─── auth views ──────────────────────────────────────────────────────────


def test_unpaired_landing_issues_no_admin_read_and_opens_no_prompt(driven) -> None:
    snap = driven["unpaired"]["before"]
    fetched = _fetched(snap)
    assert not fetched & ADMIN_PATHS, fetched & ADMIN_PATHS
    # Every open read ran — /api/plugins included, which was refused 401 —
    # and still nothing opened: the reads are quiet.
    assert {"/api/config", "/api/health", "/api/satellites", "/api/timers", "/api/plugins",
            "/api/acquisitions", "/api/calendar/events"} <= fetched
    assert snap["modal"] is False and snap["pair"] is False


def test_unpaired_sees_the_house_and_a_way_to_pair(driven) -> None:
    out = driven["unpaired"]
    snap = out["before"]
    assert any("pair this phone for live updates and controls" in b["text"] for b in snap["buttons"])
    assert snap["live"] == "not live · updates every 30s"
    assert [r["room"] for r in snap["rooms"]] == ["kitchen", "office"]
    assert any(t["id"] == 5 for t in snap["timers"])
    # The rows a household member can notice, each linking to its page.
    assert [(r["key"], r["href"]) for r in snap["attention"]] == [("offline", "#satellites")]
    assert "sign in for approvals and system checks" in " ".join(b["text"] for b in snap["buttons"])
    # The pair link is the only thing that opens the prompt.
    assert out["pairAfterClick"] is True


def test_paired_phone_is_live_with_no_pair_link_and_no_admin_rows(driven) -> None:
    out = driven["paired"]
    before, after = out["before"], out["after"]
    assert not _fetched(after) & ADMIN_PATHS
    assert not any("pair this phone" in b["text"] for b in after["buttons"])
    assert before["live"] == "not live · updates every 30s"
    assert after["live"] == "live"
    assert {r["key"] for r in after["attention"]} == {"offline"}
    # Reads carry the household token.
    assert set(out["devices"]) == {"house-token"}


def test_admin_gets_the_admin_rows_from_admin_reads(driven) -> None:
    snap = driven["admin"]
    assert ADMIN_PATHS <= _fetched(snap)
    keys = [r["key"] for r in snap["attention"]]
    assert set(keys) == {"offline", "approvals", "adopt", "update", "restart", "disk"}
    # err before warn: the failed update and nothing else is err here.
    assert keys[0] == "update"
    texts = {r["key"]: r["text"] for r in snap["attention"]}
    assert texts["approvals"].startswith("garage is waiting for approval")
    assert texts["disk"].startswith("home disk is 93% full")
    assert all(r["href"] for r in snap["attention"])
    assert not any("sign in for approvals" in b["text"] for b in snap["buttons"])
    assert not any("pair this phone" in b["text"] for b in snap["buttons"])


def test_admin_all_clear_only_after_every_source_answered(driven) -> None:
    assert driven["admin_clean"]["attention"] == []
    assert any(q.startswith("nothing needs you · checked ") for q in driven["admin_clean"]["quiet"])
    # One read still out: never a false all-clear.
    checking = driven["admin_checking"]
    assert checking["attention"] == [] and "checking…" in checking["quiet"]
    assert not any(q.startswith("nothing needs you") for q in checking["quiet"])
    failed = driven["admin_check_failed"]
    assert "couldn't check disk" in failed["quiet"]
    assert not any(q.startswith("nothing needs you") for q in failed["quiet"])


def test_unclaimed_box_says_so_first_and_offers_the_claim(driven) -> None:
    out = driven["unclaimed"]
    snap = out["before"]
    assert snap["attention"][0]["key"] == "claim"
    assert "this box isn't claimed yet" in snap["attention"][0]["text"]
    assert "#manual" in snap["links"]
    # The pre-setup grace opens every read, the admin ones included.
    assert ADMIN_PATHS <= _fetched(snap)
    assert not any("pair this phone" in b["text"] for b in snap["buttons"])
    assert snap["modal"] is False
    assert out["modalAfterClick"] is True


# ─── who sees problem rows ───────────────────────────────────────────────


def test_visibility_everyone_shows_household_rows(driven) -> None:
    snap = driven["visibility_everyone"]
    assert [(r["key"], r["text"]) for r in snap["attention"]] == [("offline", "2 rooms offline")]


def test_visibility_summary_shows_one_neutral_line(driven) -> None:
    snap = driven["visibility_summary"]
    (row,) = snap["attention"]
    assert row["key"] is None and row["href"] is None
    assert row["text"] == "something needs the admin's attention1"
    assert "2 rooms offline" not in snap["blob"]


def test_visibility_admins_shows_household_nothing(driven) -> None:
    snap = driven["visibility_admins"]
    assert snap["attention"] == []
    assert "needs attention" not in snap["text"]


def test_household_rows_wait_for_the_setting(driven) -> None:
    """An "admins only" house must never flash rows while /api/config loads."""
    snap = driven["visibility_not_known_yet"]
    assert snap["attention"] == []
    assert _room(snap, "office")["dim"]          # the page itself still renders


@pytest.mark.parametrize("mode", ["everyone", "summary", "admins"])
def test_an_admin_sees_every_row_whatever_the_setting(driven, mode) -> None:
    assert [r["key"] for r in driven[f"visibility_{mode}_admin"]["attention"]] == ["offline"]


# ─── shared screen ───────────────────────────────────────────────────────


def test_shared_screen_masks_calendar_reminders_and_problems(driven) -> None:
    snap = driven["shared"]
    blob = snap["blob"]
    for private in ("Dentist", "Standup", "Main St", "call mum", "SECRET DESCRIPTION"):
        assert private not in blob, private
    assert [e["text"].count("busy") for e in snap["events"]] == [1, 1]
    assert "reminder · office" in blob
    assert "pasta" in blob                      # a timer's own label stays
    (row,) = snap["attention"]
    assert row["text"].startswith("something needs the admin's attention")
    tiles = {t["href"] for t in snap["tiles"]}
    assert "#people" not in tiles and "#files" not in tiles
    assert {"#podcasts", "#settings", "#manual"} <= tiles


def test_shared_screen_keeps_rooms_timers_announce_and_stop(driven) -> None:
    snap = driven["shared"]
    titles = _titles(snap)
    assert {"pause kitchen", "stop kitchen", "cancel pasta timer", "cancel reminder",
            "announce in every room"} <= titles
    cancelled = driven["shared_cancel_reminder"]
    assert "cancelled reminder" in cancelled["text"]
    assert "call mum" not in cancelled["blob"]


def test_shared_screen_caps_an_admin_at_the_summary_line(driven) -> None:
    (row,) = driven["shared_admin"]["attention"]
    assert row["text"].startswith("something needs the admin's attention")


def test_the_same_house_unshared_shows_titles_places_and_reminder_text(driven) -> None:
    snap = driven["not_shared_same_house"]
    blob = snap["blob"]
    assert "Dentist" in blob and "Main St" in blob and "call mum about the tickets" in blob
    assert "SECRET DESCRIPTION" not in blob     # descriptions never, shared or not
    standup = next(e for e in snap["events"] if "Standup" in e["text"])
    assert "now" in standup["text"]
    assert "#people" in {t["href"] for t in snap["tiles"]}


def test_an_admin_marking_the_device_masks_the_page_without_a_reload(driven) -> None:
    out = driven["shared_flag_arrives"]
    assert "Dentist" in out["before"]["blob"]
    assert "Dentist" not in out["after"]["blob"]
    assert "busy" in out["after"]["blob"]


def test_a_paired_browser_stays_masked_until_the_server_answers(driven) -> None:
    assert "Dentist" not in driven["paired_no_answer_yet"]["blob"]
    # An unpaired browser can't register, so it is never a shared screen.
    assert "Dentist" in driven["unpaired_no_answer"]["blob"]


def test_nothing_private_is_read_or_shown(driven) -> None:
    for name, out in driven.items():
        snaps = [v for v in (out if isinstance(out, dict) else {}).values()
                 if isinstance(v, dict) and "blob" in v]
        if isinstance(out, dict) and "blob" in out:
            snaps.append(out)
        for snap in snaps:
            assert "SECRETNET" not in snap["blob"], name
            assert "SECRET DESCRIPTION" not in snap["blob"], name
            assert not [f for f in snap["fetches"] if PRIVATE_PATH.search(f)], name


# ─── rooms ───────────────────────────────────────────────────────────────


def test_rooms_order_state_and_transport(driven) -> None:
    out = driven["rooms"]
    snap = out["before"]
    assert [r["room"] for r in snap["rooms"]] == ["kitchen", "den", "lounge", "office", "garage", "attic"]
    titles = _titles(snap)
    assert {"pause kitchen", "stop kitchen", "resume den", "stop den", "play in office",
            "play in lounge"} <= titles
    assert not {t for t in titles if t.endswith(("attic", "garage"))}
    attic = _room(snap, "attic")
    assert attic["dim"] and "last seen" in attic["text"]
    assert _room(snap, "garage")["dim"]
    kitchen = _room(snap, "kitchen")
    assert "Creep" in kitchen["text"] and "Radiohead" in kitchen["text"] and "0:30 / 3:58" in kitchen["text"]
    assert "in call with den" in kitchen["text"]
    assert "5m 00s" in kitchen["text"]           # its timer chip
    assert "weak wi-fi" in _room(snap, "den")["text"]
    assert "screen stopped" in _room(snap, "lounge")["text"]
    assert "quiet" in _room(snap, "office")["text"]


def test_a_rooms_count_stops_at_the_songs_end(driven) -> None:
    # 300s into a 238s song: the bar is full and the count reads the length,
    # never "5:00 / 3:58".
    kitchen = _room(driven["room_overrun"], "kitchen")
    assert "3:58 / 3:58" in kitchen["text"]
    assert "5:00" not in kitchen["text"]


def test_pause_and_the_play_sheet_post_to_the_room(driven) -> None:
    out = driven["rooms"]
    assert out["sheetOpen"] is True
    assert out["after"]["sheet"] is False
    posts = out["posts"]
    assert posts[0] == {"path": "/api/music/pause/kitchen", "body": "{}"}
    assert posts[1]["path"] == "/api/music/play-playlist"
    assert json.loads(posts[1]["body"]) == {"room_id": "office", "playlist_id": 0, "shuffle": True}
    assert "playing favorites in office" in out["after"]["text"]


def test_stop_all_needs_a_second_tap(driven) -> None:
    out = driven["stop_all"]
    assert out["armed"]["posts"] == []
    assert any(b["text"] == "stop 2 rooms?" for b in out["armed"]["snap"]["buttons"])
    assert sorted(out["posts"]) == ["/api/music/stop/kitchen", "/api/music/stop/office"]
    assert "stopped 2 rooms" in out["after"]["text"]
    # One room playing: nothing to stop "all" of.
    assert not any(b["text"] == "stop all" for b in driven["one_playing_no_stop_all"]["buttons"])


def test_core_down_is_the_only_story_and_rooms_read_last_known(driven) -> None:
    snap = driven["core_down"]
    assert [r["key"] for r in snap["attention"]] == ["core"]
    assert all(r["dim"] and "last known" in r["text"] for r in snap["rooms"])
    assert not {t for t in _titles(snap) if t.startswith(("pause", "stop", "play in", "resume"))}
    assert "no rooms online" in snap["blob"]


def test_a_burst_of_pushes_is_one_reread(driven) -> None:
    out = driven["push_debounced"]
    assert out["immediately"] == 0
    assert out["afterDebounce"] == 1


# ─── timers ──────────────────────────────────────────────────────────────


def test_timers_count_down_soonest_first_with_the_elapsed_bar(driven) -> None:
    out = driven["timer_countdown"]
    t0 = out["t0"]
    assert [t["id"] for t in t0["timers"]] == [5, 8, 7]
    assert "1m 05s" in t0["timers"][0]["text"] and "pasta" in t0["timers"][0]["text"]
    assert "20m 00s" in t0["timers"][2]["text"] and "no room" in t0["timers"][2]["text"]
    assert t0["bars"][0] == f"{55 / 120 * 100}%"
    # Under ten minutes the countdown turns warn; the 20-minute roast doesn't.
    assert t0["soon"] == ["1m 05s", "8m 20s"]
    assert "3 timers" in t0["text"]
    assert "55s" in out["t10"]["timers"][0]["text"]


def test_a_fired_timer_says_done_for_a_minute(driven) -> None:
    out = driven["timer_countdown"]
    assert out["fired"]["done"] == ["done · kitchenpasta"]
    assert [t["id"] for t in out["fired"]["timers"]] == [8, 7]
    # The server deleted it and pushed; the line stays — and tea, which
    # vanished before its time, was cancelled, not fired: no line.
    assert out["pushed"]["done"] == ["done · kitchenpasta"]
    assert [t["id"] for t in out["pushed"]["timers"]] == [7]
    assert "1 timer" in out["pushed"]["text"]
    assert out["later"]["done"] == []


def test_countdown_follows_the_server_clock_not_the_phone(driven) -> None:
    (t,) = driven["timer_skewed_phone"]["timers"]
    assert "5m 00s" in t["text"]


def test_cancel_prompts_to_pair_then_replays(driven) -> None:
    out = driven["cancel_pair_replay"]
    assert out["prompted"] == {"pair": True, "deletes": 1}
    assert out["deletes"] == [{"path": "/api/timers/5", "device": None},
                              {"path": "/api/timers/5", "device": "house-token"}]
    assert out["pairAfter"] is False
    assert "cancelled pasta timer" in out["after"]["text"]


def test_cancelling_a_timer_that_already_fired_says_so(driven) -> None:
    assert "that one already finished" in driven["cancel_already_fired"]["text"]


# ─── empty states and first run ──────────────────────────────────────────


def test_an_empty_house_gets_the_first_run_hint(driven) -> None:
    snap = driven["first_run"]
    assert "try saying" in snap["text"]
    assert "“what time is it”" in snap["text"]     # the timer has none; clock's is next
    assert "no rooms yet" in snap["text"]
    assert "add a satellite" in snap["text"] and "#satellites" in snap["links"]
    assert "nothing on today" in snap["quiet"]
    assert snap["timers"] == [] and "timers" not in snap["text"]
    assert "“set a timer for 10 minutes”" in driven["first_run_manual_down"]["text"]


def test_the_manual_is_read_only_for_an_empty_house(driven) -> None:
    assert "/api/capabilities/manual" in _fetched(driven["first_run"])
    assert "/api/capabilities/manual" not in _fetched(driven["paired"]["after"])
    assert "try saying" not in driven["paired"]["after"]["text"]


def test_today_says_what_is_next_when_nothing_is_on(driven) -> None:
    (line,) = [q for q in driven["today_next"]["quiet"] if "nothing" in q]
    assert line.startswith("nothing on today · next: ") and line.endswith("Dentist")


def test_today_shows_six_and_a_phone_three(driven) -> None:
    events = driven["today_many"]["events"]
    assert len(events) == 6
    assert [e["extra"] for e in events] == [False] * 3 + [True] * 3


# ─── the phone launcher ──────────────────────────────────────────────────


def test_the_phone_launcher_lists_every_page_off_the_strip_with_badges(driven) -> None:
    tiles = {t["href"]: t["text"] for t in driven["phone_launcher"]["tiles"]}
    for r in ("podcasts", "audiobooks", "videos", "news", "people", "files", "plugins",
              "radio", "settings", "manual"):
        assert f"#{r}" in tiles, r
    for r in ("home", "music", "satellites", "calendar", "chat"):
        assert f"#{r}" not in tiles, r
    assert tiles["#radio"] == "Radio3"               # the plugin's own badge
    assert tiles["#people"] == "People4"             # App's counts, handed down


def test_the_grid_uses_the_shells_badges_and_polls_none_itself(driven) -> None:
    snap = driven["phone_launcher_given_badges"]
    assert {t["href"]: t["text"] for t in snap["tiles"]}["#radio"] == "Radio7"
    assert "/api/plugins/radio/badge" not in _fetched(snap)


def test_the_desktop_mounts_no_launcher_and_polls_no_badge(driven) -> None:
    snap = driven["desktop_no_launcher"]
    assert snap["tiles"] == []
    assert "/api/plugins/radio/badge" not in _fetched(snap)
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    home_css = css[css.index("Home page (home.jsx)"):]
    assert ".home-sec-everything { display: none; }" in home_css.split("@media")[0]
    phone = home_css[home_css.index("@media (max-width: 760px)"):]
    assert ".home-sec-everything { display: block; }" in phone
    assert ".home-phone-extra, .home-desktop-only { display: none; }" in phone
    assert "height: 44px; min-width: 44px" in phone


def _media(css: str, query: str) -> str:
    start = css.index(f"@media {query} {{")
    depth = 0
    for i in range(start, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start:i + 1]
    raise AssertionError(query)


def test_the_layout_holds_at_tablet_widths() -> None:
    """761-1279px used to overflow: room tiles ran under the right rail and
    the timers card crushed its countdown into Today's. The harness has no
    layout engine, so the rules are pinned here; the geometry was measured
    in headless Chrome at 375, 768, 800, 834, 1024, 1180 and 1280px (no
    overlap, no horizontal scroll, every phone control 44px)."""
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    home = css[css.index("Home page (home.jsx)"):]
    assert "grid-template-columns: repeat(auto-fill, minmax(min(280px, 100%), 1fr))" in home
    assert ".home .room-chip { white-space: nowrap; }" in home
    one_col = _media(home, "(max-width: 1099px)")
    assert ".home-col, .home-pair { display: contents; }" in one_col
    # The plan's order, the phone's: each section has its place.
    order = ["attention", "timers", "rooms", "announce", "today", "everything"]
    for i, sec in enumerate(order, 1):
        assert f".home-sec-{sec} {{ order: {i}; }}" in one_col, sec
    stacked = _media(home, "(min-width: 1100px) and (max-width: 1279px)")
    assert ".home-pair { flex-direction: column; align-items: stretch; }" in stacked


def test_every_one_tap_control_is_44px_on_a_phone() -> None:
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    phone = _media(css[css.index("Home page (home.jsx)"):], "(max-width: 760px)")
    for sel in (".home-room-actions .btn", ".home-timer .btn", ".home-sec-head .btn",
                ".home-sheet-head .btn", ".home-sec-announce .btn"):
        assert sel in phone, sel
    assert ".home-sec-announce input { height: 44px; }" in phone
    assert ".home-link { min-height: 44px;" in phone
    # The header's desktop pill must lose to the phone rule, and the
    # attention rows stay one line each.
    assert ".home-header .actions.home-desktop-only { display: none; }" in phone
    assert ".home-att-row .txt { white-space: nowrap;" in phone
    # Inline heights would outrank all of that: the compact announce row
    # takes its heights from styles.css.
    sats = (STATIC / "satellites.jsx").read_text(encoding="utf-8")
    start = sats.index('className="broadcast-compact"')
    compact = sats[start:sats.index("</Card>", start)]
    assert "height: 38" not in compact and "broadcast-send-label" in compact


# ─── fewer fan-outs ──────────────────────────────────────────────────────


def test_the_first_socket_open_rereads_nothing_but_a_real_reconnect_does(driven) -> None:
    out = driven["first_connect"]
    assert out["before"] == {"sats": 1, "timers": 1}
    assert out["first"] == {"sats": 1, "timers": 1}
    assert out["back"] == {"sats": 2, "timers": 2}


def test_a_hidden_tab_defers_room_rereads_and_wifi_is_merged(driven) -> None:
    out = driven["hidden_push"]
    assert out["whileHidden"] == 0
    assert out["onReturn"] == 1                      # once, when the tab is back
    assert out["afterWifi"] == 1                     # a Wi-Fi report re-reads nothing
    den = _room(out["snap"], "den")
    assert "weak wi-fi" in den["text"]
    assert "SECRETNET" not in out["snap"]["blob"]


def test_the_costly_admin_reads_survive_a_remount(driven) -> None:
    out = driven["remount"]
    assert out["hardware"] == 1 and out["version"] == 1 and out["approvals"] == 1
    assert out["health"] == 2                        # the cheap reads are simply made again
    assert "home disk" not in out["snap"]["blob"]    # 40%: and the cached answer is used
    later = driven["remount_after_ttl"]
    assert later["hardware"] == 2                    # past its 5-minute poll: asked again
    assert later["version"] == 1                     # the 10-minute one is still fresh


# ─── honest when things are down ─────────────────────────────────────────


def test_a_down_core_says_the_counts_are_last_known_and_freezes_progress(driven) -> None:
    out = driven["core_down_frozen"]
    line = out["t0"]["line"]
    assert "last known" in line and line.index("last known") < line.index("1 online")
    assert "playing" not in line and "1 timer" in line
    assert "0:30 / 3:58" in _room(out["t0"], "kitchen")["text"]
    assert "0:30 / 3:58" in _room(out["t60"], "kitchen")["text"]     # not extrapolated
    assert "the Domovoi server isn't answering" in out["t0"]["blob"]


def test_timer_counts_survive_a_failed_rooms_read(driven) -> None:
    snap = driven["sats_fail"]
    assert "1 timer" in snap["line"]
    assert "couldn't load rooms" in snap["quiet"]
    assert "rooms unavailable · the database isn't answering" in driven["db_down_rooms"]["quiet"]


def test_an_admin_sees_a_failed_check_beside_the_problem_rows(driven) -> None:
    snap = driven["admin_rows_and_failed"]
    assert [r["key"] for r in snap["attention"]] == ["offline"]
    assert "couldn't check disk" in snap["quiet"]
    out = driven["admin_refused"]
    assert "couldn't check updates, disk · sign in again" in out["before"]["quiet"]
    assert out["modal"] is True


# ─── one press, one request ──────────────────────────────────────────────


def test_a_double_tapped_cancel_sends_one_delete(driven) -> None:
    out = driven["cancel_double_tap"]
    assert out["deletes"] == 1
    (btn,) = [b for b in out["snap"]["buttons"] if b["title"] == "cancel pasta timer"]
    assert btn == {"title": "cancel pasta timer", "text": "cancelling…", "disabled": True}


def test_stop_all_holds_every_room_until_the_batch_settles(driven) -> None:
    buttons = {b["title"] or b["text"]: b for b in driven["stop_all_busy"]["buttons"]}
    assert buttons["stopping…"]["disabled"] is True
    for t in ("pause kitchen", "stop kitchen", "pause office", "stop office"):
        assert buttons[t]["disabled"] is True, t


def test_a_phone_shows_two_timers_and_a_way_to_the_rest(driven) -> None:
    t0 = driven["timer_countdown"]["t0"]
    assert [t["extra"] for t in t0["timers"]] == [False, False, True]
    assert any(b["text"] == "+1 more" for b in t0["buttons"])


# ─── the attention rules ─────────────────────────────────────────────────


def _rules(driven, case):
    return [(r["key"], r["tone"]) for r in driven["rules"][case]]


def test_speech_recognition_rows(driven) -> None:
    assert _rules(driven, "stt_fallback") == [("stt", "warn")]
    assert _rules(driven, "stt_off") == [("stt", "err")]
    assert _rules(driven, "stt_not_loaded") == []


def test_room_and_screen_rows_collapse_by_kind(driven) -> None:
    rows = {r["key"]: r["text"] for r in driven["rules"]["offline_two_waiting_one"]}
    assert rows == {"offline": "2 rooms offline", "waiting": "c was set up but hasn't connected yet"}
    assert _rules(driven, "kiosk") == [("kiosk", "warn")]


def test_plugin_rows(driven) -> None:
    assert driven["rules"]["plugin_one"][0]["text"] == "the Radio plugin failed to load"
    assert _rules(driven, "plugin_degraded") == [("plugins", "warn")]
    assert _rules(driven, "plugin_browser_error") == [("plugins", "err")]
    (row,) = driven["rules"]["plugins_two"]
    assert row["text"] == "2 plugins have problems"   # a disabled plugin is not counted


def test_a_stuck_media_request_never_shows_its_text(driven) -> None:
    (row,) = driven["rules"]["acq_stuck"]
    assert row["key"] == "acq" and row["text"].startswith("a media request is waiting")
    assert "PRIVATE" not in row["text"]


def test_update_and_disk_rows_are_admin_scoped(driven) -> None:
    assert [(r["key"], r["scope"]) for r in driven["rules"]["update_rolled_back"]] == [("update", "admin")]
    assert _rules(driven, "disk_full") == [("disk", "err")]
    assert _rules(driven, "disk_ok") == []


def test_a_staged_plugin_upgrade_asks_for_the_restart_by_name(driven) -> None:
    (row,) = driven["rules"]["restart_plugin_only"]
    assert (row["key"], row["tone"], row["scope"]) == ("restart", "warn", "admin")
    assert row["text"] == "a restart is pending · the radio upgrade isn't running yet"
    (row,) = driven["rules"]["restart_two_plugins"]
    assert row["text"] == "a restart is pending · 2 plugin upgrades aren't running yet"
    # Pulled code is the bigger news; one row, not two.
    (row,) = driven["rules"]["restart_plugins_and_code"]
    assert row["text"] == "a restart is pending · the pulled code isn't running yet"


def test_a_down_core_or_database_suppresses_what_depends_on_it(driven) -> None:
    assert _rules(driven, "db_down") == [("db", "err")]
    assert _rules(driven, "core_down") == [("core", "err")]


def test_the_claim_row_leads_and_errors_rank_first(driven) -> None:
    keys = [r["key"] for r in driven["rules"]["unclaimed_first"]]
    assert keys[0] == "claim"
    assert keys == ["claim", "stt", "offline", "disk"]


# ─── data.js quiet ───────────────────────────────────────────────────────


def test_a_quiet_read_opens_no_prompt(driven) -> None:
    out = driven["quiet_option"]
    assert out["quietAdmin"] == {"status": 401, "loginPrompted": False, "authCancelled": False}
    assert out["q1"] == {"modal": False, "pair": False}
    assert out["quietDevice"]["status"] == 401 and out["quietDevice"]["loginPrompted"] is False
    assert out["q2"] == {"modal": False, "pair": False}
    assert "quiet" not in out["keys"]                  # never handed to fetch()


def test_a_loud_read_and_any_mutation_still_prompt(driven) -> None:
    out = driven["quiet_option"]
    assert out["loud"]["loginPrompted"] is True and out["q3"]["modal"] is True
    assert out["q4"] == {"pair": True}
    assert out["mutation"]["authCancelled"] is True


# ─── the shell's boot register ───────────────────────────────────────────


def test_an_unpaired_browser_on_a_claimed_box_boots_with_no_register_and_no_prompt(driven) -> None:
    out = driven["boot_unpaired"]
    assert out["before"] == {"posts": [], "pair": False, "modal": False}
    # Pairing later (from any page) registers once, carrying the new token.
    assert out["paired"] == {"posts": ["house-token"], "pair": False, "modal": False}


@pytest.mark.parametrize(("case", "device"), [("boot_paired", "house-token"), ("boot_unclaimed", None)])
def test_a_browser_that_can_register_does_so_exactly_once(driven, case, device) -> None:
    assert driven[case] == {"posts": [device], "pair": False, "modal": False}


def test_signing_in_after_boot_registers_once(driven) -> None:
    out = driven["boot_admin_signs_in"]
    assert out["before"]["posts"] == []
    assert out["after"] == {"posts": ["house-token"], "pair": False, "modal": False}


def test_a_refused_background_register_never_prompts(driven) -> None:
    out = driven["boot_stale_token"]
    assert out["booted"] == {"posts": ["house-token"], "pair": False, "modal": False}
    assert out["refreshed"] == {"posts": ["house-token", "house-token"], "pair": False, "modal": False}
    focus = driven["focus_stale_token"]
    assert focus["tooSoon"] == 1                 # boot's own, then a focus inside 15 s: nothing
    assert focus["posts"] == ["house-token", "house-token"]
    assert focus["pair"] is False and focus["modal"] is False


def test_a_tablet_that_never_loses_focus_is_masked_on_the_next_tick(driven) -> None:
    out = driven["tick_marks_the_tablet"]
    assert "Dentist" in out["before"]["blob"]
    assert "Dentist" not in out["after"]["blob"] and "busy" in out["after"]["blob"]
    (row,) = out["after"]["attention"]
    assert row["text"].startswith("something needs the admin's attention")


def test_the_problem_rows_setting_is_reread_on_the_health_tick(driven) -> None:
    out = driven["tick_rereads_the_setting"]
    assert [r["key"] for r in out["before"]["attention"]] == ["offline"]
    assert out["after"]["attention"] == []


def test_the_shell_boots_the_device_identity_instead_of_registering_blind() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "DeviceIdentity.boot();" in html
    assert "DeviceIdentity.register()" not in html
    data = (STATIC / "data.js").read_text(encoding="utf-8")
    register = data[data.index("const register = () => {"):data.index("const refresh = () => {")]
    assert "noPrompt: true" in register


# ─── source pins ─────────────────────────────────────────────────────────


def test_home_borrows_helpers_through_window_not_copies() -> None:
    sats = (STATIC / "satellites.jsx").read_text(encoding="utf-8")
    assert "Object.assign(window, { wifiTone, fmtRemaining, remainingFromExpiresAt, Broadcast });" in sats
    cal = (STATIC / "calendar.jsx").read_text(encoding="utf-8")
    assert "Object.assign(window, { fmtClock, fmtDayLabel });" in cal
    home = (STATIC / "home.jsx").read_text(encoding="utf-8")
    for name in ("fmtRemaining", "wifiTone", "Broadcast", "fmtClock", "fmtDayLabel"):
        assert f"window.{name}" in home, name
        assert not re.search(rf"^const {name}\b", home, re.M), name


def test_every_home_read_is_quiet() -> None:
    home = (STATIC / "home.jsx").read_text(encoding="utf-8")
    calls = re.findall(r"useApiObject\(([^;]*?)\);", home, re.S)
    cached = re.findall(r"useCachedObject\(([^;]*?)\);", home, re.S)
    assert len(calls) + len(cached) >= 12 and len(cached) == 4
    for call in calls:
        assert "HOME_QUIET" in call or "quiet: true" in call, call
    # The cached reads go through one apiGet, quiet too; nothing else fetches.
    assert re.findall(r"\bapiGet\(([^)]*)\)", home) == ["path, HOME_QUIET"]
