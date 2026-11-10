"""Settings → System → Health, the checks it shares with Home, and the
settings search (owner design 2026-10-21, wf/settings-regroup).

* ONE set of checks (web/static/health.jsx healthChecks): Health shows
  every one, green or not, in its part (server, updates, rooms, plugins,
  internet, containers); Home's "needs attention" rows are the problems
  among the same checks, in the order Home always had, each linking to
  its part of Health (#settings/health/<part>).
* A check whose read hasn't answered is grey, never green.
* The container guard's page folds into Health: open incidents at the
  top, the rest under "behind the scenes"; the banner's "details" and the
  old #containers land there.
* The search at the top of Settings finds a section by its name, its old
  name and its words ("wake", "voice history"), a card inside one, and
  each of Advanced's settings; Printers & scanners shows (and is found)
  once its panel is registered.

Driven through domovoi/tests/jsx_interact_harness.js. No DB, never
``requires_db``; needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = "web/static/components.jsx"
SETTINGS = "web/static/settings.jsx"
CONTAINERS = "web/static/containers.jsx"
HEALTH = "web/static/health.jsx"
HOME = "web/static/home.jsx"

ADMIN = {"known": True, "unclaimed": False, "paired": True, "isAdmin": True}
HOUSEHOLD = {"known": True, "unclaimed": False, "paired": True, "isAdmin": False}
HEALTH_OK = {"status": "ok", "db_reachable": True, "domovoi_reachable": True, "stt": "ready"}


KB = 1024
MB = 1024 * KB
GB = 1024 * MB
# The Windows installer's distro, cache-heavy (core /v1/admin/memory).
MEM_WSL = {
    "available": True, "host": "wsl", "checked_at": "2026-11-09T10:00:00Z",
    "total_bytes": 65842876 * KB, "programs_bytes": 11719420 * KB, "cache_bytes": 49587220 * KB,
    "shared_bytes": 1048576 * KB, "free_bytes": 2345678 * KB, "available_bytes": 54123456 * KB,
    "swap_total_bytes": 4 * GB, "swap_used_bytes": 1 * GB,
    "breakdown": [
        {"kind": "service", "name": "domovoi-core.service", "label": "Domovoi server", "bytes": 2 * GB},
        {"kind": "container", "name": "domovoi-mpd-kitchen", "label": "music player for kitchen", "bytes": 40 * MB},
        {"kind": "container", "name": "domovoi-wake-train", "label": "wake-word trainer", "bytes": 9 * GB},
        {"kind": "container", "name": "someones-own-app", "label": "other container", "bytes": 300 * MB},
        {"kind": "process", "name": "python3", "label": "python3", "bytes": 700 * MB, "count": 2},
    ],
    "missing": [{"source": "processes", "why": "only some processes were looked at in time"}],
}
MEM_LINUX_LOW = {**MEM_WSL, "host": "linux", "available_bytes": 3 * GB, "breakdown": [], "missing": []}
MEM_OFF = {"available": False, "host": "windows", "why": "not available on this system"}


def room(rid: str, status: str = "online", **kw) -> dict:
    return {"room_id": rid, "status": status, "sat_type": "voice", "last_connected_at": "2026-10-21T08:00:00Z", **kw}


GREEN = {
    "viewer": ADMIN, "health": HEALTH_OK, "rooms": [room("kitchen"), room("office")],
    "plugins": {"plugins": [{"slug": "radio", "name": "Radio", "status": "loaded", "enabled": True}]},
    "pluginErrors": {}, "acq": {"core_reachable": True, "acquisitions": []},
    "approvals": [], "pending": [],
    "version": {"sha": "abc1234def", "version": "1.0.0", "channel": "stable",
                "last_update": {"status": "ok", "finished_at": "2026-10-20T03:00:00Z"}},
    "hardware": {"disk": {"percent": 41}}, "config": {"internet_access": "sometimes"},
    "internet": {"answer": "sometimes", "connectivity": {"online": True, "reason": "connected"}},
    "guard": {"loaded": True, "tier": "household", "data": {"incidents": [], "banner": {"show": False}}},
    "memory": MEM_WSL,
}
BAD = {
    **GREEN,
    "rooms": [room("kitchen", "offline"), room("office"), room("den", "waiting")],
    "health": {**HEALTH_OK, "stt": "fallback"},
    "hardware": {"disk": {"percent": 96}},
    "approvals": [{"room_id": "garage"}],
    "config": {"internet_access": ""},
    "plugins": {"plugins": [{"slug": "radio", "name": "Radio", "status": "load_error", "enabled": True}]},
    "version": {"sha": "abc1234def", "last_update": {"status": "failed", "finished_at": "2026-10-20T03:00:00Z"},
                "restart_required": True},
}
CORE_DOWN = {**GREEN, "health": {"status": "degraded", "db_reachable": True, "domovoi_reachable": False}}
UNREAD = {"viewer": {**ADMIN, "known": False}, "health": None, "rooms": None, "plugins": None, "pluginErrors": {}, "acq": None,
          "approvals": None, "pending": None, "version": None, "hardware": None, "config": None,
          "internet": None, "guard": {"loaded": False}, "memory": None}
MEM_LOW = {**GREEN, "memory": MEM_LINUX_LOW}
MEM_NA = {**GREEN, "memory": MEM_OFF}

_CHECKS = r"""
  const w = h.global('window');
  const cases = w.__CASES;
  const out = {};
  for (const [k, c] of Object.entries(cases)) {
    const checks = w.healthChecks(c);
    out[k] = {
      checks: checks.map((x) => ({ key: x.key, part: x.part, tone: x.tone, text: x.text, scope: x.scope,
                                   fix: x.fix || null, items: (x.items || []).map((i) => i.name) })),
      problems: w.healthProblems(checks).map((x) => ({ key: x.key, tone: x.tone, href: x.href || null })),
      home: w.HomeAttentionRows(c).map((x) => ({ key: x.key, tone: x.tone, text: x.text, href: x.href || null })),
    };
  }
  return out;
"""

_SEARCH = r"""
  const w = h.global('window');
  const fields = [{ name: 'acoustid_api_key', label: 'AcoustID key', group: 'Library' },
                  { name: 'bot_name', label: 'Assistant name', group: 'General' }];
  const find = (q) => w.settingsSearch(w.settingsSearchIndex(w.settingsLayout().groups, fields), q)
    .map((x) => [x.label, x.where, x.route]);
  const before = { wake: find('wake'), history: find('voice history'), recordings: find('recordings'),
                   printer: find('printer'), token: find('token'), acoustid: find('acoustid'),
                   satellite: find('satellite'), nothing: find('zzzz'), blank: find('  ') };
  // wf/printing's seam: a page script registers the panel, the slot shows.
  w.DomovoiSettingsSections.printers = { Panel: () => null };
  const after = { printer: find('printer'),
                  rooms: w.settingsLayout().groups[0].sections.map((s) => s.label) };
  return { before, after };
"""

_PANEL = r"""
  const w = h.global('window');
  h.render(); await h.settle(); h.rerender(); await h.settle(); h.rerender();
  const cls = (e) => String((e && e.props && e.props.className) || '').split(' ');
  const parts = h.findAll((e) => e.props && e.props['data-health-part']).map((e) => e.props['data-health-part']);
  const rows = h.findAll((e) => cls(e).includes('health-row')).map((e) => [e.props['data-check'], e.props['data-tone']]);
  const behind = h.findAll((e) => e.type === 'button' && cls(e).includes('health-disclose')).length;
  const summary = h.find((e) => cls(e).includes('health-summary-line'));
  return { parts, rows, behind, summary: summary ? summary.props['data-problems'] : null,
           text: h.text().join(' '), fetched: h.hookCalls };
"""

_MEMORY = r"""
  h.render(); await h.settle(); h.rerender(); await h.settle(); h.rerender();
  const cls = (e) => String((e && e.props && e.props.className) || '').split(' ');
  const row = h.find((e) => cls(e).includes('health-row') && e.props['data-check'] === 'memory');
  const folded = h.findAll((e) => cls(e).includes('health-mem')).length;
  const toggle = () => h.find((e) => e.type === 'button' && cls(e).includes('health-mem-toggle'));
  const before = toggle() ? [toggle().props['aria-expanded'], toggle().text] : null;
  h.click((e) => e.type === 'button' && cls(e).includes('health-mem-toggle')); await h.settle(); h.rerender();
  const items = h.findAll((e) => cls(e).includes('health-mem-item'))
    .map((e) => [e.props['data-kind'], h.findAll((x) => h.ancestors(x).includes(e) && cls(x).includes('health-mem-name'))
      .map((x) => x.text).join('')]);
  const text = h.text().join(' ');
  const notes = h.findAll((e) => e.props && e.props['data-mem-note']).map((e) => e.props['data-mem-note']);
  const missing = h.findAll((e) => e.props && e.props['data-mem-missing']).map((e) => e.props['data-mem-missing']);
  const after = toggle() ? toggle().props['aria-expanded'] : null;
  h.click((e) => e.type === 'button' && cls(e).includes('health-mem-toggle')); await h.settle(); h.rerender();
  const refolded = h.findAll((e) => cls(e).includes('health-mem')).length;
  return { tone: row ? row.props['data-tone'] : null, folded, before, after, items, notes, missing, text,
           refolded, fetched: h.hookCalls };
"""

# The file-cache trim's last run (core /v1/admin/memory `cache_trim`,
# scripts/linux/cache-trim.py), and the Windows install's "this PC" card
# (core /v1/admin/host-limits), 2026-11-10.
TRIM_ON = {"enabled": True, "why_enabled": "wsl", "limit_bytes": int(6.3 * GB), "action": "under",
           "checked_at": "2026-11-10T05:02:00Z",
           "last_trim": {"at": "2026-11-10T04:59:00Z", "freed_bytes": 22 * GB}}
MEM_WSL_TRIMMED = {**MEM_WSL, "cache_trim": TRIM_ON}
LIMITS = {
    "available": True, "ram_gb": 128, "logical_processors": 32,
    "memory_gb": {"min": 8, "max": 124, "default": 32, "chosen": None, "in_file": 32},
    "processors": {"min": 2, "max": 32, "default": 32, "chosen": None, "in_file": None},
    "running": {"memory_gb": 62.6, "processors": 32}, "pending": True, "waiting_for_host": False,
    "request": "none", "request_problem": None, "restart": "none", "reported_at": "2026-11-10T05:00:00Z",
    "warn_below_gb": 16, "training_gb": 24,
}

_TRIM_WORDS = r"""
  const w = h.global('window');
  const at = new Date(Date.now() - 3 * 60 * 1000).toISOString();
  const GB = 1024 ** 3;
  return {
    none: w.healthTrimWords(null),
    on: w.healthTrimWords({ enabled: true, why_enabled: 'wsl', limit_bytes: 8 * GB, checked_at: at,
                            last_trim: { at, freed_bytes: 22 * GB } }),
    nothing: w.healthTrimWords({ enabled: true, why_enabled: 'wsl', limit_bytes: 8 * GB, checked_at: at, last_trim: null }),
    linux: w.healthTrimWords({ enabled: false, why_enabled: 'not-wsl', checked_at: at }),
    operator: w.healthTrimWords({ enabled: false, why_enabled: 'operator', checked_at: at }),
    low: w.healthLimitsWords({ warn_below_gb: 16, training_gb: 24, memory_gb: { default: 32 } }, { memory_gb: 12 })
      .map((n) => [n.key, n.tone]),
    auto: w.healthLimitsWords({ warn_below_gb: 16, training_gb: 24, memory_gb: { default: 32 } }, { memory_gb: null })
      .map((n) => n.key),
  };
"""

_MEMORY_SUB = r"""
  h.render(); await h.settle(); h.rerender(); await h.settle(); h.rerender();
  const sub = h.find((e) => e.props && e.props['data-health-sub'] === 'memory');
  h.click((e) => e.type === 'button' && String(e.props.className || '').includes('health-mem-toggle'));
  await h.settle(); h.rerender();
  return { sub: sub ? sub.text : null,
           text: h.text().join(' ') };
"""

_LIMITS = r"""
  h.render(); await h.settle(); h.rerender(); await h.settle(); h.rerender();
  const card = h.find((e) => e.props && e.props['data-health-limits'] === 'card');
  if (!card) return { card: false, fetched: h.hookCalls };
  const text0 = h.text().join(' ');
  const sliders0 = h.findAll((e) => e.type === 'input' && e.props.type === 'range').length;
  // Memory off automatic: a slider inside the PC's bounds.
  await h.change((e) => e.type === 'input' && e.props.type === 'checkbox'
    && h.ancestors(e).some((a) => a.props && a.props['data-limit'] === 'memory'), false);
  const rangeSel = (e) => e.type === 'input' && e.props.type === 'range';
  const range = () => h.find(rangeSel);
  const bounds = range() ? [range().props.min, range().props.max, range().props.value] : null;
  await h.change(rangeSel, '12');
  const low = h.findAll((e) => e.props && e.props['data-limit-note']).map((e) => e.props['data-limit-note']);
  h.click((e) => e.props && e.props['data-limit-action'] === 'save'); await h.settle(); h.rerender();
  h.click((e) => e.props && e.props['data-limit-action'] === 'restart'); await h.settle(); h.rerender();
  const confirming = !!h.find((e) => e.props && e.props['data-limit-action'] === 'confirm');
  h.click((e) => e.props && e.props['data-limit-action'] === 'confirm'); await h.settle(); h.rerender();
  return { card: true, text0, sliders0, bounds, low, confirming,
           posts: h.calls.filter((c) => c.method === 'POST').map((c) => [c.path, c.body]),
           text: h.text().join(' '), fetched: h.hookCalls };
"""

API_GREEN = {
    "GET /api/config": {"internet_access": "always", "home_problems_visibility": "everyone"},
    "GET /api/health": HEALTH_OK,
    "GET /api/satellites": [room("kitchen"), room("office", "offline")],
    "GET /api/plugins": {"plugins": []},
    "GET /api/acquisitions?status=pending&limit=100": {"core_reachable": True, "acquisitions": []},
    "GET /api/satellites/approvals": [], "GET /api/satellites/pending": [],
    "GET /api/config/version": GREEN["version"], "GET /api/models/hardware": {"disk": {"percent": 30}},
    "GET /api/config/internet": GREEN["internet"],
    "GET /api/containers/incidents": {"incidents": [], "banner": {"show": False}},
    "GET /api/models/memory": MEM_WSL,
}

SCENARIOS: dict[str, dict] = {
    "checks": {
        "files": [COMPONENTS, HEALTH, HOME], "component": "(window.HomeAttentionRows = HomeAttentionRows, HomePage)",
        "setup": "window.__CASES = " + json.dumps({"green": GREEN, "bad": BAD, "core_down": CORE_DOWN,
                                                   "unread": UNREAD, "mem_low": MEM_LOW,
                                                   "mem_na": MEM_NA}) + ";",
        "script": _CHECKS,
    },
    "search": {
        "files": [COMPONENTS, SETTINGS, CONTAINERS, HEALTH], "component": "SettingsPage",
        "script": _SEARCH,
    },
    "panel_admin": {
        "files": [COMPONENTS, CONTAINERS, HEALTH], "component": "HealthPanel",
        "api": API_GREEN, "script": _PANEL,
    },
    "memory_wsl": {
        "files": [COMPONENTS, CONTAINERS, HEALTH], "component": "HealthPanel",
        "api": API_GREEN, "script": _MEMORY,
    },
    "memory_linux_low": {
        "files": [COMPONENTS, CONTAINERS, HEALTH], "component": "HealthPanel",
        "api": {**API_GREEN, "GET /api/models/memory": {**MEM_LINUX_LOW, "breakdown": MEM_WSL["breakdown"][:1],
                                                        "cache_bytes": 2 * GB}},
        "script": _MEMORY,
    },
    "trim_words": {
        "files": [COMPONENTS, CONTAINERS, HEALTH], "component": "HealthPanel",
        "script": _TRIM_WORDS,
    },
    "memory_trimmed": {
        "files": [COMPONENTS, CONTAINERS, HEALTH], "component": "HealthPanel",
        "api": {**API_GREEN, "GET /api/models/memory": MEM_WSL_TRIMMED}, "script": _MEMORY_SUB,
    },
    "limits_card": {
        "files": [COMPONENTS, CONTAINERS, HEALTH], "component": "HealthPanel",
        "api": {**API_GREEN, "GET /api/models/host-limits": LIMITS,
                "POST /api/models/host-limits": LIMITS}, "script": _LIMITS,
    },
    "limits_off_windows": {
        "files": [COMPONENTS, CONTAINERS, HEALTH], "component": "HealthPanel",
        "api": {**API_GREEN, "GET /api/models/host-limits": {"available": False, "why": "x"}},
        "script": _LIMITS,
    },
    "panel_household_admins_only": {
        "files": [COMPONENTS, CONTAINERS, HEALTH], "component": "HealthPanel",
        "setup": "globalThis.Auth = { status: { setup_complete: true }, subscribe: () => () => {},"
                 " isLoggedIn: () => false, isPaired: () => true, headers: () => ({}) };",
        "api": {**API_GREEN, "GET /api/config": {"home_problems_visibility": "admins"}},
        "script": _PANEL,
    },
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("settings-health") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def _src(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


# ─── one set of checks ───────────────────────────────────────────────────


def test_health_shows_every_check_green_or_not(driven) -> None:
    green = driven["checks"]["green"]
    keys = [c["key"] for c in green["checks"]]
    assert keys == ["claim", "core", "db", "stt", "disk", "memory", "version", "update", "restart", "rooms", "offline",
                    "waiting", "kiosk", "approvals", "adopt", "plugins", "acq", "internet", "connection",
                    "containers"]
    parts = {c["part"] for c in green["checks"]}
    assert parts == {"server", "updates", "rooms", "plugins", "internet", "containers"}
    assert all(c["tone"] in ("ok", "idle") for c in green["checks"]), green["checks"]
    by = {c["key"]: c for c in green["checks"]}
    assert by["version"]["text"] == "running 1.0.0 · stable channel"
    assert by["rooms"]["text"] == "2 of 2 online"
    assert by["disk"]["text"] == "home disk is 41% full"
    assert by["internet"]["text"] == "has internet: sometimes"
    assert by["connection"]["text"] == "working"
    # Nothing to show, nothing on Home.
    assert green["problems"] == [] and green["home"] == []


def test_a_check_that_has_not_answered_is_grey_never_green(driven) -> None:
    unread = driven["checks"]["unread"]["checks"]
    assert all(c["tone"] == "idle" for c in unread), unread
    assert driven["checks"]["unread"]["home"] == []


def test_home_lists_the_problems_among_the_same_checks_linking_into_health(driven) -> None:
    bad = driven["checks"]["bad"]
    problems = [(c["key"], c["tone"]) for c in bad["checks"] if c["tone"] in ("err", "warn")]
    # The same set, err before warn, in the order Home always had.
    assert [(r["key"], r["tone"]) for r in bad["home"]] == [
        ("plugins", "err"), ("update", "err"), ("disk", "err"),
        ("stt", "warn"), ("offline", "warn"), ("waiting", "warn"), ("approvals", "warn"),
        ("restart", "warn"), ("internet", "warn")]
    assert sorted(bad["home"], key=lambda r: r["key"]) and set(k for k, _ in problems) == {r["key"] for r in bad["home"]}
    hrefs = {r["key"]: r["href"] for r in bad["home"]}
    assert hrefs == {"plugins": "#settings/health/plugins", "update": "#settings/health/updates",
                     "disk": "#settings/health/server", "stt": "#settings/health/server",
                     "offline": "#settings/health/rooms", "waiting": "#settings/health/rooms",
                     "approvals": "#settings/health/rooms", "restart": "#settings/health/updates",
                     "internet": "#settings/health/internet"}
    # Health's own rows link on to the page that fixes them, and name the rooms.
    by = {c["key"]: c for c in bad["checks"]}
    assert by["offline"]["fix"] == "#settings/rooms" and by["offline"]["items"] == ["kitchen"]
    assert by["approvals"]["items"] == ["garage"]
    assert by["plugins"]["fix"] == "#plugins" and by["plugins"]["items"] == ["Radio"]
    assert by["update"]["fix"] == "#settings/about"


def test_a_down_core_is_the_only_story_and_the_rest_wait(driven) -> None:
    down = driven["checks"]["core_down"]
    assert [r["key"] for r in down["home"]] == ["core"]
    waiting = [c["key"] for c in down["checks"] if c["text"] == "waiting for the Domovoi server"]
    assert {"stt", "disk", "memory", "rooms", "offline", "plugins", "acq", "internet", "connection"} <= set(waiting)


# ─── memory ──────────────────────────────────────────────────────────────


def test_the_memory_row_says_programs_and_cache_in_plain_words(driven) -> None:
    by = {c["key"]: c for c in driven["checks"]["green"]["checks"]}
    assert by["memory"]["part"] == "server" and by["memory"]["scope"] == "admin"
    assert by["memory"]["tone"] == "ok"
    assert by["memory"]["text"] == "using 11 GB for programs · 47 GB file cache (handed back when needed)"
    # Short of memory for programs: amber, on Home too.
    low = {c["key"]: c for c in driven["checks"]["mem_low"]["checks"]}["memory"]
    assert low["tone"] == "warn" and low["text"] == "running low · 3.0 GB of 63 GB left for programs"
    assert ("memory", "warn", "#settings/health/server") in [
        (r["key"], r["tone"], r["href"]) for r in driven["checks"]["mem_low"]["home"]]
    na = {c["key"]: c for c in driven["checks"]["mem_na"]["checks"]}["memory"]
    assert na["tone"] == "idle" and na["text"] == "not available on this system"
    assert {c["key"]: c for c in driven["checks"]["unread"]["checks"]}["memory"]["text"] == "not checked yet"


def test_the_memory_row_unfolds_into_what_uses_it_largest_first(driven) -> None:
    o = driven["memory_wsl"]
    assert o["tone"] == "ok" and o["folded"] == 0
    assert o["before"] == [False, "what uses it"] and o["after"] is True
    assert o["items"] == [
        ["container", "wake-word trainer"], ["service", "Domovoi server"], ["process", "python3"],
        ["container", "other container"], ["container", "music player for kitchen"]]
    # The honest guidance: the cache is normal; on Windows, given back when idle.
    assert o["notes"] == ["limit", "cache"]
    assert "Domovoi may use up to about 63 GB of this PC’s memory" in o["text"]
    assert "programs get it back the moment they need it" in o["text"]
    assert "Windows gets it back once Domovoi has been idle" in o["text"]
    assert "63 GB in all · 2.2 GB unused · 1.0 GB shared · 1.0 GB moved to swap" in o["text"]
    assert "someones-own-app" in o["text"]                  # an unknown container is named
    assert "2 running" in o["text"]
    assert o["missing"] == ["processes"] and "couldn’t check other programs" in o["text"]
    assert o["refolded"] == 0
    assert "/api/models/memory" in o["fetched"]


def test_no_cache_note_when_programs_use_the_memory(driven) -> None:
    o = driven["memory_linux_low"]
    assert o["tone"] == "warn"
    assert o["notes"] == []                                  # not WSL, not cache-heavy
    assert o["missing"] == []


def test_the_memory_row_says_whether_the_cache_is_trimmed(driven) -> None:
    o = driven["trim_words"]
    assert o["none"] is None                                 # not reached by an update yet: nothing said
    assert o["on"] == "file cache is trimmed automatically above 8.0 GB · last trimmed 3m ago (freed 22 GB)"
    assert o["nothing"] == "file cache is trimmed automatically above 8.0 GB · nothing to trim so far"
    assert o["linux"] == "file cache isn’t trimmed here: this server hands it to programs as they need it"
    assert o["operator"] == "automatic file-cache trimming is turned off on this server"
    m = driven["memory_trimmed"]
    assert m["sub"] and m["sub"].startswith("file cache is trimmed automatically above 6.3 GB · last trimmed")
    assert "(freed 22 GB)" in m["sub"]
    # Unfolded, the cache note says Domovoi hands it back itself now.
    assert "Domovoi hands what is over 6.3 GB back to Windows every few minutes" in m["text"]
    assert "once Domovoi has been idle" not in m["text"]


def test_this_pc_card_sets_the_ceiling_inside_the_pcs_bounds(driven) -> None:
    o = driven["limits_card"]
    assert o["card"] is True and "/api/models/host-limits" in o["fetched"]
    assert "This PC has 128 GB of memory and 32 processor cores" in o["text0"]
    assert "automatic (32 GB)" in o["text0"] and "automatic (all 32)" in o["text0"]
    assert o["sliders0"] == 0                                 # both automatic: no slider yet
    assert o["bounds"] == [8, 124, 32]                        # min, max: the PC's; starts at the default
    assert "low" in o["low"] and "training" in o["low"]       # 12 GB: warned, and too little to train
    assert "Domovoi still runs on 62.6 GB" in o["text0"]
    assert "The new limits apply when Domovoi next restarts" in o["text0"]
    assert o["confirming"] is True
    assert o["posts"] == [
        ["/api/models/host-limits", {"memory_gb": 12, "processors": None, "restart_now": False}],
        # The restart right after the save sends what was saved, not the
        # report's old `chosen` (DomovoiHost hasn't read the request yet).
        ["/api/models/host-limits", {"memory_gb": 12, "processors": None, "restart_now": True}],
    ]


def test_no_this_pc_card_off_the_windows_install(driven) -> None:
    assert driven["limits_off_windows"]["card"] is False
    assert driven["panel_admin"]["text"].count("This PC has") == 0


def test_home_holds_no_rules_of_its_own() -> None:
    home = _src("home.jsx")
    assert "const HomeAttentionRows = (inputs) => healthProblems(healthChecks(inputs));" in home
    assert "healthAudience(" in home
    for rule_text in ("is offline · it can't hear anyone", "plugins have problems", "home disk is"):
        assert rule_text not in home, rule_text
        assert rule_text in _src("health.jsx"), rule_text


# ─── the page ────────────────────────────────────────────────────────────


def test_the_health_page_has_every_part_and_folds_the_containers_details(driven) -> None:
    o = driven["panel_admin"]
    assert o["parts"] == ["server", "updates", "rooms", "plugins", "internet", "containers"]
    rows = dict(o["rows"])
    assert rows["offline"] == "warn" and rows["core"] == "ok" and rows["containers"] == "ok"
    assert o["summary"] == 1
    assert o["behind"] == 1                         # "behind the scenes", folded
    assert "office is offline" in o["text"] and "open rooms" in o["text"]
    assert "/api/config/version" in o["fetched"] and "/api/config/internet" in o["fetched"]


def test_a_household_member_in_an_admins_only_house_sees_no_details(driven) -> None:
    o = driven["panel_household_admins_only"]
    assert o["parts"] == [] and o["rows"] == []
    assert "the admin sees" in o["text"]
    # The admin reads are never made for anyone else.
    for admin_read in ("/api/config/version", "/api/models/hardware", "/api/config/internet",
                       "/api/satellites/approvals", "/api/models/memory"):
        assert admin_read not in o["fetched"], admin_read


def test_the_banner_and_the_old_containers_address_open_health() -> None:
    comp = _src("components.jsx")
    assert '<a className="guard-banner-link mono" href="#settings/health/containers">details</a>' in comp
    assert "containers: ['health', 'containers']" in comp
    html = _src("index.html")
    assert "containers: () => window.SettingsPage," in html
    assert html.index('src="containers.jsx"') < html.index('src="health.jsx"') < html.index('src="home.jsx"')
    assert "'/health.jsx'," in _src("sw.js")
    ctr = _src("containers.jsx")
    assert "rebuild the music player image" in ctr and "CtrBehindPart" in ctr
    assert "const ContainersPage" not in ctr


# ─── search ──────────────────────────────────────────────────────────────


def test_search_jumps_straight_to_a_setting(driven) -> None:
    b = driven["search"]["before"]
    assert b["wake"][0] == ["Wake words", "Voice", "settings/wake-words"]
    assert b["history"][0] == ["Voice history", "Privacy", "settings/voice-history"]
    assert b["recordings"][0][0] == "Voice history"          # its old name finds it
    assert b["satellite"][0][0] == "Rooms"
    assert ["Household token", "Household › Phones & browsers",
            "settings/phones-and-browsers/household-token"] in b["token"]
    assert b["acoustid"][0] == ["AcoustID key", "System › Advanced", "settings/advanced/acoustid_api_key"]
    assert b["nothing"] == [] and b["blank"] == []
    # Printers & scanners: a slot, not a promise, until wf/printing registers it.
    assert b["printer"] == []
    a = driven["search"]["after"]
    assert a["printer"][0] == ["Printers & scanners", "Household", "settings/printers"]
    assert a["rooms"] == ["Rooms", "People", "Phones & browsers", "Printers & scanners"]


def test_advanced_opens_folded_and_a_named_setting_unfolds_it() -> None:
    s = _src("settings.jsx")
    assert "case 'advanced': return <ConfigPanel setupAsked={mark === 'setup'} mark={mark} startFolded/>;" in s
    assert "React.useEffect(() => { if (markField) setFolded(false); }, [markField]);" in s
    # The update status moved out of it, into About & updates.
    config = s[s.index("const ConfigPanel = "):s.index("/* Internet tab")]
    assert "<VersionSection/>" not in config
    about = s[s.index("const AboutPanel = () => {"):]
    assert "<VersionSection/>" in about[:about.index("\n};")]
