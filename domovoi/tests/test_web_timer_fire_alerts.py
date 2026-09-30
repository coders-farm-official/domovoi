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
* at most three cards, then ``+N more``; ``role="alert"``; a card hides 30
  minutes after the fire; clicking one opens Home;
* the catch-up read is quiet: a 503 (no fire history, V017 missing) opens
  no prompt and raises nothing.

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
    return { id: c.props['data-fire'], title: within('timer-fire-title')[0] || null,
             body: within('timer-fire-body').length ? within('timer-fire-body')[0] : null,
             meta: within('timer-fire-meta')[0] || null };
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


def scenario(table: dict, script: str, *, ls: dict | None = None) -> dict:
    head = (f"const __NOW0 = {NOW}; const __TABLE = {json.dumps(table)};"
            f" const __LS = {json.dumps(ls or {})};"
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
_PLAIN = fire(43, age_ms=4 * SEC, room_id=None, total_s=600, summary="no satellite was online",
              deliveries=[])
_SEEN_40 = {SEEN: "40"}
_EMPTY = {"GET /api/timers/fires": fires_page()}

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
        "detail": "timer fire history needs database migration V017 — run Flyway"}}},
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
    assert out["first"]["seen"] == "41"


def test_a_later_push_updates_the_summary_in_place(driven) -> None:
    updated = driven["live"]["updated"]
    assert _ids(updated) == [42, 41]                     # newest first, still one card for 41
    reminder = updated["cards"][1]
    assert reminder["meta"].startswith("heard in garage, kitchen · ")
    pasta = updated["cards"][0]
    assert pasta["title"] == "Timer done · kitchen" and pasta["body"] == "pasta"
    assert updated["seen"] == "42"


def test_a_shared_screen_never_shows_a_reminders_words(driven) -> None:
    cards = {c["id"]: c for c in driven["shared"]["cards"]}
    assert cards[41]["title"] == "Reminder · garage" and cards[41]["body"] is None
    assert cards[42]["body"] == "pasta"                  # a timer's label stays
    assert cards[43]["title"] == "Timer done · no room"
    assert cards[43]["body"] == "10 min timer"
    assert cards[43]["meta"].startswith("no satellite was online · ")


def test_a_reminder_the_server_masked_shows_no_words(driven) -> None:
    (card,) = driven["masked"]["cards"]
    assert card["title"] == "Reminder · garage" and card["body"] is None


# ─── what alerts, and what is only taken as seen ─────────────────────────


def test_a_first_visit_alerts_only_the_last_two_minutes(driven) -> None:
    snap = driven["first_load"]
    assert _ids(snap) == [12]
    assert snap["seen"] == "12"
    # Nothing remembered: the newest page, not everything since id 0.
    assert snap["fetches"] == ["/api/timers/fires?limit=50"]
    empty = driven["first_load_empty_history"]
    assert empty["cards"] == [] and empty["seen"] == "0"


def test_a_failed_catch_up_prompts_nothing_and_the_first_push_is_a_first_visit(driven) -> None:
    out = driven["first_push_after_503"]
    assert out["before"]["cards"] == []
    assert out["before"]["prompt"] == {"pair": False, "login": False}
    assert _ids(out["after"]) == [21]
    assert out["after"]["seen"] == "21"


def test_a_reconnect_catches_up_since_the_last_fire_seen(driven) -> None:
    out = driven["catch_up"]
    assert out["before"]["cards"] == []
    assert out["after"]["fetches"][-1] == "/api/timers/fires?since_id=10&limit=50"
    # 15 minutes old: only taken as seen. 5 minutes old: alerts.
    assert _ids(out["after"]) == [12]
    assert out["after"]["seen"] == "12"


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


def test_the_stack_sits_above_the_dock_and_under_the_toasts() -> None:
    """Bottom right on the toast's lifted edge, z-index 110; a toast (the
    answer to a press) is 111, so a card that stays never hides it —
    found on a 375px phone, where both share the bottom edge."""
    comps = (REPO_ROOT / "web" / "static" / "components.jsx").read_text(encoding="utf-8")
    block = comps[comps.index("const TimerFireAlerts = "):comps.index("/* expose to other Babel scripts */")]
    assert "bottom: 'calc(var(--dock-bottom, 0px) + var(--player-h, 0px) + 24px)'" in block
    assert "zIndex: 110" in block
    toast = comps[comps.index("const useToast"):comps.index("/* ---- Tabs")]
    assert "zIndex: 111" in toast


def test_no_browser_notification_or_sound() -> None:
    comps = (REPO_ROOT / "web" / "static" / "components.jsx").read_text(encoding="utf-8")
    block = comps[comps.index("const TIMER_FIRE_SEEN_KEY"):comps.index("/* expose to other Babel scripts */")]
    for banned in ("Notification", "new Audio", "PushManager", "serviceWorker"):
        assert banned not in block, banned
