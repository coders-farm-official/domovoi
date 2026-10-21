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
          "internet": None, "guard": {"loaded": False}}

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
}

SCENARIOS: dict[str, dict] = {
    "checks": {
        "files": [COMPONENTS, HEALTH, HOME], "component": "(window.HomeAttentionRows = HomeAttentionRows, HomePage)",
        "setup": "window.__CASES = " + json.dumps({"green": GREEN, "bad": BAD, "core_down": CORE_DOWN,
                                                   "unread": UNREAD}) + ";",
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
    assert keys == ["claim", "core", "db", "stt", "disk", "version", "update", "restart", "rooms", "offline",
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
    assert {"stt", "disk", "rooms", "offline", "plugins", "acq", "internet", "connection"} <= set(waiting)


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
                       "/api/satellites/approvals"):
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
    assert ["Household token", "Rooms & devices › Phones & browsers",
            "settings/phones-and-browsers/household-token"] in b["token"]
    assert b["acoustid"][0] == ["AcoustID key", "System › Advanced", "settings/advanced/acoustid_api_key"]
    assert b["nothing"] == [] and b["blank"] == []
    # Printers & scanners: a slot, not a promise, until wf/printing registers it.
    assert b["printer"] == []
    a = driven["search"]["after"]
    assert a["printer"][0] == ["Printers & scanners", "Rooms & devices", "settings/printers"]
    assert a["rooms"] == ["Rooms", "Phones & browsers", "Printers & scanners", "App layout"]


def test_advanced_opens_folded_and_a_named_setting_unfolds_it() -> None:
    s = _src("settings.jsx")
    assert "case 'advanced': return <ConfigPanel setupAsked={mark === 'setup'} mark={mark} startFolded/>;" in s
    assert "React.useEffect(() => { if (markField) setFolded(false); }, [markField]);" in s
    # The update status moved out of it, into About & updates.
    config = s[s.index("const ConfigPanel = "):s.index("/* Internet tab")]
    assert "<VersionSection/>" not in config
    about = s[s.index("const AboutPanel = () => {"):]
    assert "<VersionSection/>" in about[:about.index("\n};")]
