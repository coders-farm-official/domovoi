"""The dashboard's alert when a timer or reminder goes off
(web/static/components.jsx ``TimerFireAlerts``, rendered once by the App
shell in index.html), driven outside a browser.

Owner decision 2026-09-30: a timer or reminder going off anywhere in the
house reaches every room AND every open dashboard — he missed a garage
timer because he was not in the garage. What these tests pin:

* a ``timer_fires.changed`` push raises a card with the exact title
  (``Reminder · garage`` / ``Timer done · kitchen``), body and
  ``summary · time`` line; a later push for the same fire updates the
  summary in place instead of raising a second card;
* a reminder's words never show on a shared screen, nor when the server
  masked them (no household credential, rule M1); a timer's label does;
* a first visit (nothing remembered) alerts only what fired in the last
  2 minutes; a catch-up after the socket comes back alerts only the last
  10 minutes, asking ``/api/timers/fires?since_id=<seen>``; the last fire
  seen is remembered in localStorage;
* a dismissed card stays dismissed when its fire is pushed again, and the
  dismissal is remembered;
* at most three cards, then ``+N more`` and ``dismiss all``; on a phone
  (760px and below) only the newest card until ``+N more`` is tapped;
  ``role="alert"``; a card hides 30 minutes after the fire; clicking one
  opens Home;
* the catch-up read is quiet: a 503 (no fire history, V018 missing) opens
  no prompt and raises nothing; while the socket is down (an unpaired
  tablet's is refused for good) it runs every 30 s instead;
* a history that is BEHIND what this browser remembers (a rebuilt
  database, a reinstall on the same address) starts it over as a first
  visit, dismissals included, instead of skipping every new fire up to
  the old id — but an empty answer the server held to a window
  (``window_sec``: a browser with no household credential reads only the
  last 10 minutes, rule F1) says nothing about the history, and starts
  nothing over;
* such a browser gets no per-room rows (``deliveries: []``): a fire
  still on its way reads ``announcing…`` and its dot is the warning one,
  as on a paired screen.

The component runs on top of the REAL auth.js and data.js (scripted
``fetch`` and ``WebSocket`` only, like test_web_home_page), so the quiet
read and the socket's ``_status`` events are the production code.

No DB, never ``requires_db``; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/auth.js", "web/static/data.js", "web/static/components.jsx",
         "web/static/calendar.jsx"]
COMPONENT = "(window.__Auth = Auth, TimerFireAlerts)"

NOW = 1790330400000          # fri 25 sep 2026, 10:00 UTC
SEC = 1000
MIN = 60 * SEC
SEEN = "domovoi-timer-fire-seen"
DISMISSED = "domovoi-timer-fire-dismissed"
SHARED_LS = {"domovoi-device-token": "house-token", "domovoi-shared-screen": "1"}
PAIRED_LS = {"domovoi-device-token": "house-token", "domovoi-shared-screen": "0"}


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def fire(fid: int, *, age_ms: int, room_id: str | None = "garage", label: str | None = None,
         message: str | None = None, total_s: int = 600, summary: str = "heard in garage",
         masked: bool = False, deliveries: list | None = None) -> dict:
    fired = NOW - age_ms
    return {"id": fid, "timer_id": 100 + fid, "kind": "reminder" if message is not None or masked else "timer",
            "is_reminder": message is not None or masked, "label": label, "message": message,
            "masked": masked, "room_id": room_id, "created_at": iso(fired - total_s * SEC),
            "due_at": iso(fired), "fired_at": iso(fired), "settled_at": None, "acked_at": None,
            "acked_by": None, "heard_in": [room_id] if summary.startswith("heard") else [],
            "summary": summary,
            "deliveries": deliveries if deliveries is not None else [
                {"room_id": room_id, "is_origin": True, "outcome": "spoken", "detail": None,
                 "finished_at": iso(fired + 2 * SEC)}]}


def fires_page(*fires: dict, server_now: int = NOW) -> dict:
    return {"server_now": iso(server_now), "fires": list(fires)}


# ─── the in-sandbox prelude: a scripted fetch, a stub socket, a clock ────

PRELUDE = r"""
let __now = __NOW0;
Date.now = () => __now;
window.__setNow = (ms) => { __now = ms; };
const __st = setTimeout;
setTimeout = (fn, ms, ...a) => { const t = __st(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };
const __store = new Map(Object.entries(__LS || {}));
localStorage = {
  getItem: (k) => (__store.has(k) ? __store.get(k) : null),
  setItem: (k, v) => { __store.set(k, String(v)); },
  removeItem: (k) => { __store.delete(k); },
};
window.__ls = (k) => (__store.has(k) ? __store.get(k) : null);
window.matchMedia = __PHONE
  ? (q) => ({ matches: q === '(max-width: 760px)', addEventListener() {}, removeEventListener() {} })
  : undefined;
document.hidden = false;
window.__intervals = new Map();
let __iid = 0;
setInterval = (fn, ms) => { __iid += 1; window.__intervals.set(__iid, { fn, ms }); return __iid; };
clearInterval = (id) => { window.__intervals.delete(id); };
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
  window.__fetches.push({ method, path });
  let hit = window.__table[`${method} ${path}`];
  if (hit === undefined) hit = window.__table[`${method} ${path.split('?')[0]}`];
  let status = 200; let body = hit;
  if (hit === undefined) { status = 404; body = { detail: 'not found' }; }
  else if (hit && typeof hit === 'object' && !Array.isArray(hit) && '__status' in hit) {
    status = hit.__status; body = hit.__body;
  }
  const text = body === undefined || body === null ? '' : JSON.stringify(body);
  return { ok: status >= 200 && status < 300, status, statusText: String(status),
           text: async () => text, json: async () => JSON.parse(text) };
};
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
  const stack = h.find((e) => w.__cls(e).includes('timer-fire-alerts'));
  const cards = h.findAll((e) => w.__cls(e).includes('timer-fire-card')).map((c) => {
    const within = (cls) => h.findAll((e) => w.__cls(e).includes(cls) && h.inside(e, (a) => a === c))
      .map((e) => w.__deepText(e));
    const dot = h.findAll((e) => w.__cls(e).includes('dot') && h.inside(e, (a) => a === c))[0];
    return { id: c.props['data-fire'], title: within('timer-fire-title')[0] || null,
             body: within('timer-fire-body').length ? within('timer-fire-body')[0] : null,
             meta: within('timer-fire-meta')[0] || null,
             dot: dot && dot.props.style ? dot.props.style.background : null };
  });
  const more = h.find((e) => w.__cls(e).includes('timer-fire-more'));
  return {
    role: stack ? (stack.props.role || null) : null,
    cards,
    more: more ? w.__deepText(more) : null,
    seen: w.__ls(__SEEN),
    dismissed: w.__ls(__DISMISSED),
    fetches: w.__fetches.filter((f) => f.path.startsWith('/api/timers/fires')).map((f) => f.path),
    prompt: { pair: w.__Auth.pairModalOpen, login: w.__Auth.modalOpen },
    hash: w.location.hash,
  };
};
"""


def scenario(table: dict, script: str, *, ls: dict | None = None, phone: bool = False) -> dict:
    head = (f"const __NOW0 = {NOW}; const __TABLE = {json.dumps(table)};"
            f" const __LS = {json.dumps(ls or {})}; const __PHONE = {json.dumps(phone)};"
            f" const __SEEN = {json.dumps(SEEN)}; const __DISMISSED = {json.dumps(DISMISSED)};\n")
    return {"files": FILES, "component": COMPONENT, "props": {},
            "setup": head + PRELUDE,
            "script": "const w = h.global('window'); h.render(); await w.__flush(h);\n"
                      "const snap = () => w.__snap(h);\n" + script}


def emit(*fires: dict) -> str:
    return f"w.__wsEmit({{ type: 'timer_fires.changed', data: {json.dumps(list(fires))} }}); await w.__flush(h);"


# ─── the scenarios ───────────────────────────────────────────────────────

_REMINDER = fire(41, age_ms=10 * SEC, message="call mom", summary="heard in garage · still announcing")
_REMINDER_LATER = {**_REMINDER, "summary": "heard in garage, kitchen", "heard_in": ["garage", "kitchen"]}
_PASTA = fire(42, age_ms=5 * SEC, room_id="kitchen", label="pasta", summary="heard in kitchen")
_PLAIN = fire(43, age_ms=4 * SEC, room_id=None, total_s=600, summary="not announced in any room",
              deliveries=[])
_SEEN_40 = {SEEN: "40"}      # the plain id an earlier build stored: still read
# Nothing past fire 40, and the newest fire the server has IS 40: the
# history this browser remembers.
_EMPTY = {"GET /api/timers/fires": fires_page(),
          "GET /api/timers/fires?limit=1": fires_page(fire(40, age_ms=40 * MIN))}


def seen_of(snap: dict) -> dict | None:
    raw = snap["seen"]
    return None if raw is None else json.loads(raw)

SCENARIOS: dict[str, dict] = {}
SCENARIOS["live"] = scenario(
    _EMPTY,
    "const before = snap(); w.__wsOpen(); await w.__flush(h);"
    + emit(_REMINDER) + "const first = snap();"
    + emit(_REMINDER_LATER, _PASTA) + "const updated = snap();"
    "return { before, first, updated, clock: w.fmtClock(" + json.dumps(_REMINDER["fired_at"]) + ") };",
    ls={**PAIRED_LS, **_SEEN_40},
)
SCENARIOS["shared"] = scenario(
    _EMPTY, "w.__wsOpen(); await w.__flush(h);" + emit(_REMINDER, _PASTA, _PLAIN) + "return snap();",
    ls={**SHARED_LS, **_SEEN_40},
)
SCENARIOS["masked"] = scenario(
    {"GET /api/timers/fires": fires_page(fire(41, age_ms=30 * SEC, masked=True))},
    "return snap();",
    ls=_SEEN_40,
)
# A first visit: nothing remembered. Only what fired in the last 2 minutes.
SCENARIOS["first_load"] = scenario(
    {"GET /api/timers/fires": fires_page(
        fire(12, age_ms=30 * SEC, label="tea"),
        fire(11, age_ms=3 * MIN, label="eggs"),
        fire(10, age_ms=60 * MIN, label="roast"))},
    "return snap();",
    ls=PAIRED_LS,
)
SCENARIOS["first_load_empty_history"] = scenario(_EMPTY, "return snap();", ls=PAIRED_LS)
# The mount's catch-up failed (no fire history: 503); the first push then
# follows the first-visit rule too, and nothing prompts.
SCENARIOS["first_push_after_503"] = scenario(
    {"GET /api/timers/fires": {"__status": 503, "__body": {
        "detail": "timer fire history needs database migration V018 — run Flyway"}}},
    "const before = snap(); w.__wsOpen(); await w.__flush(h);"
    + emit(fire(21, age_ms=10 * SEC, label="tea"), fire(20, age_ms=20 * MIN, label="eggs"))
    + "return { before, after: snap() };",
    ls=PAIRED_LS,
)
# The socket drops and comes back: catch up since the last fire seen, and
# alert only what is under 10 minutes old.
SCENARIOS["catch_up"] = scenario(
    _EMPTY,
    "w.__wsOpen(); await w.__flush(h); const before = snap();"
    "w.__wsClose(); await w.__flush(h);"
    f"w.__table['GET /api/timers/fires'] = {json.dumps(fires_page(fire(11, age_ms=15 * MIN, label='eggs'), fire(12, age_ms=5 * MIN, label='tea')))};"
    "w.__wsOpen(); await w.__flush(h);"
    "return { before, after: snap() };",
    ls={**PAIRED_LS, SEEN: "10"},
)
SCENARIOS["dismiss"] = scenario(
    _EMPTY,
    "w.__wsOpen(); await w.__flush(h);" + emit(_REMINDER, _PASTA)
    + "const before = snap();"
    "await h.click((e) => e.type === 'button' && e.props.title === 'dismiss'"
    " && h.inside(e, (a) => a.props && a.props['data-fire'] === 41));"
    "const after = snap();"
    + emit(_REMINDER_LATER, _PASTA) + "const pushedAgain = snap();"
    "h.rerender(); return { before, after, pushedAgain, rerendered: snap() };",
    ls={**PAIRED_LS, **_SEEN_40},
)
SCENARIOS["many"] = scenario(
    _EMPTY,
    "w.__wsOpen(); await w.__flush(h);"
    + emit(*[fire(40 + i, age_ms=(10 - i) * SEC, label=f"t{i}") for i in range(1, 6)])
    + "return snap();",
    ls={**PAIRED_LS, **_SEEN_40},
)
SCENARIOS["open_home_and_hide"] = scenario(
    _EMPTY,
    "w.__wsOpen(); await w.__flush(h);" + emit(_PASTA)
    + "await h.click((e) => w.__cls(e).includes('timer-fire-open')); const clicked = snap();"
    f"w.__setNow({NOW + 29 * MIN}); h.rerender(); const at29 = snap();"
    f"w.__setNow({NOW + 31 * MIN}); h.rerender(); const at31 = snap();"
    "return { clicked, at29, at31 };",
    ls={**PAIRED_LS, **_SEEN_40},
)
# This browser remembers fire 100 (and a dismissed 5) from ANOTHER history:
# the database was rebuilt, and its ids are at 5. Nothing past 100; the
# newest fire is 5 < 100: start over as a first visit.
SCENARIOS["history_behind"] = scenario(
    {"GET /api/timers/fires?since_id=100&limit=50": fires_page(),
     "GET /api/timers/fires?limit=1": fires_page(fire(5, age_ms=30 * SEC, label="tea")),
     "GET /api/timers/fires?limit=50": fires_page(
         fire(5, age_ms=30 * SEC, label="tea"), fire(4, age_ms=5 * MIN, label="eggs"))},
    "return snap();",
    ls={**PAIRED_LS, SEEN: json.dumps({"id": 100, "at": iso(NOW - 60 * MIN)}),
        DISMISSED: "[5]"},
)
# An empty history (a fresh database) behind a remembered 100: start over too.
SCENARIOS["history_empty"] = scenario(
    {"GET /api/timers/fires": fires_page()},
    "return snap();",
    ls={**PAIRED_LS, SEEN: json.dumps({"id": 100, "at": iso(NOW - 60 * MIN)})},
)
# A push of fire 3 that went off AFTER the remembered fire 40: the ids
# started again under a live page.
SCENARIOS["push_after_restart"] = scenario(
    _EMPTY,
    "w.__wsOpen(); await w.__flush(h); const before = snap();"
    + emit(fire(3, age_ms=5 * SEC, label="tea"))
    + "const after = snap();"
    + emit(fire(39, age_ms=50 * MIN, label="old"))
    + "return { before, after, older: snap() };",
    ls={**PAIRED_LS, SEEN: json.dumps({"id": 40, "at": iso(NOW - 40 * MIN)})},
)
# A phone: the newest card only, "+2 more" opens the rest, "dismiss all".
SCENARIOS["phone"] = scenario(
    _EMPTY,
    "w.__wsOpen(); await w.__flush(h);"
    + emit(*[fire(40 + i, age_ms=(10 - i) * SEC, label=f"t{i}") for i in range(1, 4)])
    + "const collapsed = snap();"
    "await h.click((e) => w.__cls(e).includes('timer-fire-more')); const expanded = snap();"
    "await h.click((e) => w.__cls(e).includes('timer-fire-fewer')); const fewer = snap();"
    "await h.click((e) => w.__cls(e).includes('timer-fire-dismiss-all')); const gone = snap();"
    "return { collapsed, expanded, fewer, gone };",
    ls={**PAIRED_LS, **_SEEN_40},
    phone=True,
)
SCENARIOS["dismiss_all_desktop"] = scenario(
    _EMPTY,
    "w.__wsOpen(); await w.__flush(h);"
    + emit(*[fire(40 + i, age_ms=(10 - i) * SEC, label=f"t{i}") for i in range(1, 6)])
    + "const before = snap();"
    "await h.click((e) => w.__cls(e).includes('timer-fire-dismiss-all'));"
    "return { before, after: snap() };",
    ls={**PAIRED_LS, **_SEEN_40},
)
# The socket is refused (an unpaired tablet): no pushes, so the history is
# read every 30 s until the socket comes up.
SCENARIOS["poll_while_down"] = scenario(
    _EMPTY,
    "const polls = () => [...w.__intervals.values()].filter((i) => i.ms === 30000);"
    "const before = { n: polls().length, fetches: snap().fetches.length };"
    f"w.__table['GET /api/timers/fires?since_id=40&limit=50'] = {json.dumps(fires_page(fire(41, age_ms=20 * SEC, label='tea')))};"
    "for (const p of polls()) p.fn(); await w.__flush(h);"
    "const polled = snap();"
    "w.__wsOpen(); await w.__flush(h);"
    "return { before, polled, after: { n: polls().length } };",
    ls={**PAIRED_LS, **_SEEN_40},
)

# A browser with no household credential (rule F1): each fire comes cut
# down — no per-room rows, no who-stopped-it — and the history reaches back
# 10 minutes only (`window_sec: 600`). Fire 41 is on its way, 20 s old; 42
# was heard. Eleven minutes on, the windowed answers are empty: nothing
# went off lately, which says nothing about the history this browser
# remembers — no start-over, the cards stay, the dismissals stay.
_OPEN_41 = {**fire(41, age_ms=20 * SEC, message="call mom", summary="announcing…", deliveries=[]),
            "message": None, "label": None, "masked": True}
_OPEN_42 = fire(42, age_ms=15 * SEC, room_id="kitchen", label="pasta", summary="heard in kitchen",
                deliveries=[])
_OPEN_43 = fire(43, age_ms=10 * SEC, summary="not heard in any room (garage offline)", deliveries=[])


def open_page(*fires: dict, server_now: int = NOW) -> dict:
    return {**fires_page(*fires, server_now=server_now), "window_sec": 600}


# The same browser paired: the card on screen is re-read under the household
# token and its words come back — once per credential change, none for a
# modal that merely opened and closed.
SCENARIOS["masked_then_paired"] = scenario(
    {"GET /api/timers/fires?since_id=40&limit=50": open_page(_OPEN_41)},
    "const whole = () => w.__fetches.filter((f) => f.path === '/api/timers/fires?limit=50').length;"
    "const masked = snap();"
    "w.__Auth.requestPairing(); await w.__flush(h); w.__Auth.closePairModal(); await w.__flush(h);"
    "const modalOnly = whole();"
    f"w.__table['GET /api/timers/fires?limit=50'] = {json.dumps(fires_page({**_OPEN_41, 'message': 'call mom', 'masked': False}))};"
    "w.__Auth.pair('house-token'); await w.__flush(h);"
    "return { masked, modalOnly, paired: snap(), whole: whole() };",
    # Known not to be a shared screen, which would hide the words anyway.
    ls={SEEN: json.dumps({"id": 40, "at": iso(NOW - 40 * MIN)}), "domovoi-shared-screen": "0"},
)
SCENARIOS["open_window"] = scenario(
    {"GET /api/timers/fires?since_id=40&limit=50": open_page(_OPEN_41, _OPEN_42, _OPEN_43)},
    "const polls = () => [...w.__intervals.values()].filter((i) => i.ms === 30000);"
    "const shown = snap();"
    f"w.__table['GET /api/timers/fires?since_id=43&limit=50'] = {json.dumps(open_page(server_now=NOW + 11 * MIN))};"
    f"w.__table['GET /api/timers/fires?limit=1'] = {json.dumps(open_page(server_now=NOW + 11 * MIN))};"
    f"w.__setNow({NOW + 11 * MIN}); for (const p of polls()) p.fn(); await w.__flush(h);"
    "return { shown, later: snap() };",
    ls={SEEN: json.dumps({"id": 40, "at": iso(NOW - 40 * MIN)}), DISMISSED: "[7]"},
)


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("alerts") / "scenarios.json"
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


def _ids(snap: dict) -> list[int]:
    return [c["id"] for c in snap["cards"]]


# ─── a push raises a card ────────────────────────────────────────────────


def test_a_push_raises_a_card_with_title_body_and_summary(driven) -> None:
    out = driven["live"]
    assert out["before"]["cards"] == [] and out["before"]["role"] is None
    (card,) = out["first"]["cards"]
    assert card["title"] == "Reminder · garage"
    assert card["body"] == "call mom"
    assert card["meta"] == f"heard in garage · still announcing · {out['clock']}"
    assert out["first"]["role"] == "alert"
    assert seen_of(out["first"]) == {"id": 41, "at": _REMINDER["fired_at"]}


def test_a_later_push_updates_the_summary_in_place(driven) -> None:
    updated = driven["live"]["updated"]
    assert _ids(updated) == [42, 41]                     # newest first, still one card for 41
    reminder = updated["cards"][1]
    assert reminder["meta"].startswith("heard in garage, kitchen · ")
    pasta = updated["cards"][0]
    assert pasta["title"] == "Timer done · kitchen" and pasta["body"] == "pasta"
    assert seen_of(updated)["id"] == 42


def test_a_shared_screen_never_shows_a_reminders_words(driven) -> None:
    cards = {c["id"]: c for c in driven["shared"]["cards"]}
    assert cards[41]["title"] == "Reminder · garage" and cards[41]["body"] is None
    assert cards[42]["body"] == "pasta"                  # a timer's label stays
    assert cards[43]["title"] == "Timer done · no room"
    assert cards[43]["body"] == "10 min timer"
    assert cards[43]["meta"].startswith("not announced in any room · ")


def test_a_reminder_the_server_masked_shows_no_words(driven) -> None:
    (card,) = driven["masked"]["cards"]
    assert card["title"] == "Reminder · garage" and card["body"] is None


def test_pairing_rereads_the_cards_on_screen_and_unmasks_them(driven) -> None:
    """The alert reads by hand, not through the hooks, so it carries its
    own once-per-credential re-read (the dashboard's refetchOnAuth rule)."""
    out = driven["masked_then_paired"]
    (card,) = out["masked"]["cards"]
    assert card["id"] == 41 and card["body"] is None
    assert out["modalOnly"] == 0
    (card,) = out["paired"]["cards"]
    assert card["id"] == 41 and card["body"] == "call mom"
    assert out["whole"] == 1
    assert out["paired"]["prompt"] == {"pair": False, "login": False}


# ─── what alerts, and what is only taken as seen ─────────────────────────


def test_a_first_visit_alerts_only_the_last_two_minutes(driven) -> None:
    snap = driven["first_load"]
    assert _ids(snap) == [12]
    assert seen_of(snap)["id"] == 12
    # Nothing remembered: the newest page, not everything since id 0.
    assert snap["fetches"] == ["/api/timers/fires?limit=50"]
    empty = driven["first_load_empty_history"]
    assert empty["cards"] == [] and seen_of(empty)["id"] == 0


def test_a_failed_catch_up_prompts_nothing_and_the_first_push_is_a_first_visit(driven) -> None:
    out = driven["first_push_after_503"]
    assert out["before"]["cards"] == []
    assert out["before"]["prompt"] == {"pair": False, "login": False}
    assert _ids(out["after"]) == [21]
    assert seen_of(out["after"])["id"] == 21


def test_a_reconnect_catches_up_since_the_last_fire_seen(driven) -> None:
    out = driven["catch_up"]
    assert out["before"]["cards"] == []
    assert out["after"]["fetches"][-1] == "/api/timers/fires?since_id=10&limit=50"
    # 15 minutes old: only taken as seen. 5 minutes old: alerts.
    assert _ids(out["after"]) == [12]
    assert seen_of(out["after"])["id"] == 12


# ─── dismissing, the stack, opening Home, hiding ─────────────────────────


def test_a_dismissed_card_stays_dismissed(driven) -> None:
    out = driven["dismiss"]
    assert _ids(out["before"]) == [42, 41]
    assert _ids(out["after"]) == [42]
    assert json.loads(out["after"]["dismissed"]) == [41]
    assert _ids(out["pushedAgain"]) == [42]
    assert _ids(out["rerendered"]) == [42]


def test_at_most_three_cards_then_more(driven) -> None:
    snap = driven["many"]
    assert _ids(snap) == [45, 44, 43]
    assert snap["more"] == "+2 more"


def test_dismiss_all_clears_every_card_including_the_hidden_ones(driven) -> None:
    out = driven["dismiss_all_desktop"]
    assert _ids(out["before"]) == [45, 44, 43]
    assert out["after"]["cards"] == [] and out["after"]["role"] is None
    assert sorted(json.loads(out["after"]["dismissed"])) == [41, 42, 43, 44, 45]


def test_a_phone_shows_the_newest_card_until_more_is_tapped(driven) -> None:
    """At 375px three cards covered 38% of the screen and every dialog's
    buttons under them (the 2026-09-30 review): a phone shows one."""
    out = driven["phone"]
    assert _ids(out["collapsed"]) == [43]
    assert out["collapsed"]["more"] == "+2 more"
    assert _ids(out["expanded"]) == [43, 42, 41]
    assert out["expanded"]["more"] is None
    assert _ids(out["fewer"]) == [43]
    assert out["gone"]["cards"] == []
    assert sorted(json.loads(out["gone"]["dismissed"])) == [41, 42, 43]


# ─── another history on the same address ─────────────────────────────────


def test_a_history_behind_the_remembered_id_starts_over(driven) -> None:
    snap = driven["history_behind"]
    assert snap["fetches"] == ["/api/timers/fires?since_id=100&limit=50",
                               "/api/timers/fires?limit=1",
                               "/api/timers/fires?limit=50"]
    # A first visit: the 30 s old fire alerts, the 5 minute old one is only
    # taken as seen — and the stale dismissal of "5" no longer hides it.
    assert _ids(snap) == [5]
    assert seen_of(snap)["id"] == 5
    assert snap["dismissed"] is None


def test_an_empty_history_behind_the_remembered_id_starts_over(driven) -> None:
    snap = driven["history_empty"]
    assert seen_of(snap)["id"] == 0
    assert snap["cards"] == []


def test_a_push_that_went_off_after_the_remembered_fire_starts_over(driven) -> None:
    out = driven["push_after_restart"]
    assert out["before"]["cards"] == []
    # The mount's check found the history consistent (its newest is 40).
    assert "/api/timers/fires?limit=1" in out["before"]["fetches"]
    assert _ids(out["after"]) == [3]
    assert seen_of(out["after"])["id"] == 3
    # An older fire pushed again (a summary update) starts nothing over.
    assert _ids(out["older"]) == [3]


def test_a_windowed_empty_history_starts_nothing_over(driven) -> None:
    out = driven["open_window"]
    assert _ids(out["shown"]) == [43, 42, 41]
    later = out["later"]
    assert later["fetches"][-2:] == ["/api/timers/fires?since_id=43&limit=50",
                                     "/api/timers/fires?limit=1"]
    assert "/api/timers/fires?limit=50" not in later["fetches"]     # no first visit again
    assert _ids(later) == [43, 42, 41]                              # 11 minutes: still up
    assert seen_of(later)["id"] == 43
    assert later["dismissed"] == "[7]"


def test_a_cut_down_fire_still_reads_and_colours_right(driven) -> None:
    """No per-room rows to go by: the summary says a fire is on its way."""
    cards = {c["id"]: c for c in driven["open_window"]["shown"]["cards"]}
    assert cards[41]["title"] == "Reminder · garage" and cards[41]["body"] is None
    assert cards[41]["meta"].startswith("announcing… · ")
    assert cards[41]["dot"] == "var(--warn)"
    assert cards[42]["body"] == "pasta" and cards[42]["dot"] == "var(--ok)"
    assert cards[43]["meta"].startswith("not heard in any room (garage offline) · ")
    assert cards[43]["dot"] == "var(--err)"


def test_the_history_is_polled_while_the_socket_is_down(driven) -> None:
    out = driven["poll_while_down"]
    assert out["before"]["n"] == 1
    assert _ids(out["polled"]) == [41]
    assert out["polled"]["fetches"][-1] == "/api/timers/fires?since_id=40&limit=50"
    assert out["after"]["n"] == 0                        # connected: no more polling


def test_a_card_opens_home_and_hides_after_thirty_minutes(driven) -> None:
    out = driven["open_home_and_hide"]
    assert out["clicked"]["hash"] == "home"
    assert _ids(out["clicked"]) == [42]                  # opening Home does not dismiss it
    assert _ids(out["at29"]) == [42]
    assert out["at31"]["cards"] == [] and out["at31"]["role"] is None


# ─── the shell ───────────────────────────────────────────────────────────


def test_the_shell_renders_the_alerts_once() -> None:
    html = (REPO_ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")
    assert html.count("<TimerFireAlerts/>") == 1
    comps = (REPO_ROOT / "web" / "static" / "components.jsx").read_text(encoding="utf-8")
    assert "  TimerFireAlerts,\n});" in comps


def test_the_stack_sits_above_the_dock_and_under_the_dialogs_and_toasts() -> None:
    """Bottom right on the toast's lifted edge. z-index 85: over the
    full-screen editors (80), under the confirm dialogs (90), the modals
    (100) and the toast (111) — at 110 a card that stays covered a
    centred dialog's buttons on a 375px phone (2026-09-30 review)."""
    comps = (REPO_ROOT / "web" / "static" / "components.jsx").read_text(encoding="utf-8")
    block = comps[comps.index("const TimerFireAlerts = "):comps.index("/* expose to other Babel scripts */")]
    assert "bottom: 'calc(var(--dock-bottom, 0px) + var(--player-h, 0px) + 24px)'" in block
    assert "zIndex: 85" in block
    toast = comps[comps.index("const useToast"):comps.index("/* ---- Tabs")]
    assert "zIndex: 111" in toast
    confirm = comps[comps.index("const DeleteConfirmDialog"):comps.index("const DeleteConfirmDialog") + 600]
    assert "zIndex: 90" in confirm
    css = (REPO_ROOT / "web" / "static" / "styles.css").read_text(encoding="utf-8")
    modal = css[css.index(".cal-modal-bg"):css.index(".cal-modal-bg") + 300]
    assert "z-index: 100" in modal


def test_the_cards_buttons_are_44px_targets_on_a_phone() -> None:
    css = (REPO_ROOT / "web" / "static" / "styles.css").read_text(encoding="utf-8")
    assert ".timer-fire-card .btn-icon { width: 44px; height: 44px; }" in css
    assert ".timer-fire-foot .btn { min-height: 44px; }" in css
    before = css[:css.index(".timer-fire-card .btn-icon")]
    assert before.rfind("@media (max-width: 760px)") == before.rfind("@media")


def test_no_browser_notification_or_sound() -> None:
    comps = (REPO_ROOT / "web" / "static" / "components.jsx").read_text(encoding="utf-8")
    block = comps[comps.index("const TIMER_FIRE_SEEN_KEY"):comps.index("/* expose to other Babel scripts */")]
    for banned in ("Notification", "new Audio", "PushManager", "serviceWorker"):
        assert banned not in block, banned
