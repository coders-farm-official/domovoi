"""The phone bar, and Satellites as a Settings tab (2026-10-11).

Owner decisions (design-notes NAV-SATELLITES-BRIEF.md):

* the phone strip (760px and below) reads **home · music · videos · chat**
  by default, and each device may pick its own: Home always first, two to
  five tabs, any page a launcher may show (plugin pages too). It is kept
  in that browser's localStorage, a per-device convenience, so storage
  that is broken, blocked or full of junk falls back to the default and
  never breaks the shell. A shared screen keeps hiding the personal pages
  (components.jsx SHARED_SCREEN_HIDDEN) whatever the bar says, because
  the bar is picked from ``navItemsFor``, the one list every launcher
  reads.
* the editor lives in Settings → Phone bar (a tab of its own: it needs no
  sign-in, and the Devices tab's other cards ask for one), and Home's
  "everything" grid links to it.
* Satellites left the sidebar, the strip and the grid and is a Settings
  tab; the old ``#satellites`` (bookmarks, the docs, Home's room cards and
  problem rows) opens that tab, and the route stays reserved so no plugin
  can take it.

Driven through domovoi/tests/jsx_interact_harness.js (the dashboard's own
Babel, a small stateful React), like test_web_home_shell.py.

No DB, never ``requires_db``; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.plugins_runtime.manifest import CORE_WEB_ROUTES
from web.backend import plugin_host

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = "web/static/components.jsx"
HOME = "web/static/home.jsx"
SETTINGS = "web/static/settings.jsx"
SAT_FILES = [COMPONENTS, "web/static/satellite_media.jsx", "web/static/satellites.jsx", SETTINGS]

DEFAULT = ["home", "music", "videos", "chat"]
KEY = "domovoi-phone-bar"
_RADIO = {"plugins": [{"slug": "radio", "pages": [
    {"route": "radio", "page": "RadioPage", "nav_label": "Radio", "nav_order": 11}]}]}

# localStorage / sessionStorage / window events / history, recorded.
# __LS seeds localStorage; __BROKEN: "" (works), "read" (every call
# throws, a blocked store) or "write" (reads work, writes throw).
_SANDBOX = r"""
ServerStore = { current: () => null, currentLabel: () => 'box:6369' };
const __st = setTimeout;
setTimeout = (fn, ms, ...a) => { const t = __st(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };
window.__ls = new Map(Object.entries(__LS));
window.__writes = [];
localStorage = {
  getItem: (k) => { if (__BROKEN === 'read') throw new Error('blocked'); return window.__ls.has(k) ? window.__ls.get(k) : null; },
  setItem: (k, v) => { if (__BROKEN) throw new Error('blocked'); window.__writes.push([k, String(v)]); window.__ls.set(k, String(v)); },
  removeItem: (k) => { if (__BROKEN) throw new Error('blocked'); window.__writes.push([k, null]); window.__ls.delete(k); },
};
window.__ss = new Map(Object.entries(__SS));
sessionStorage = {
  getItem: (k) => (window.__ss.has(k) ? window.__ss.get(k) : null),
  setItem: (k, v) => { window.__ss.set(k, String(v)); },
  removeItem: (k) => { window.__ss.delete(k); },
};
window.__l = [];
window.__events = [];
window.addEventListener = (t, fn) => { window.__l.push({ t, fn }); };
window.removeEventListener = (t, fn) => { window.__l = window.__l.filter((l) => !(l.t === t && l.fn === fn)); };
window.dispatchEvent = (e) => {
  window.__events.push({ type: e.type, detail: e.detail });
  window.__l.filter((l) => l.t === e.type).forEach((l) => l.fn(e));
  return true;
};
CustomEvent = function (type, init) { this.type = type; this.detail = init && init.detail; };
window.__replaced = [];
window.history = { replaceState: (s, t, url) => { window.__replaced.push(url); } };
window.DomovoiPluginManifest = __MANIFEST;
window.__cls = (e) => String((e && e.props && e.props.className) || '').split(' ');
window.__deep = (n) => {
  if (n == null || typeof n === 'boolean') return '';
  if (typeof n === 'string' || typeof n === 'number') return String(n);
  if (Array.isArray(n)) return n.map(window.__deep).join('');
  return n.props ? window.__deep(n.props.children) : '';
};
window.__settle = async (h, n = 4) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
"""

_NAV_ROWS = (
    "h.tree().filter((e) => w.__cls(e).includes('nav-item'))"
    ".map((e) => ({ route: e.props.key, primary: w.__cls(e).includes('primary'),"
    " strip: (e.props.style || {})['--strip-order'] ?? null }))"
)


def _device(shared: bool) -> str:
    answer = "true" if shared else "false"
    return (f" DeviceIdentity = {{ id: () => 'dev-1', name: () => 'phone', sharedScreen: () => {answer},"
            " canRegister: () => true, subscribeShared: () => () => {},"
            " suggestedName: () => 'phone', register: () => Promise.resolve({ device_id: 'dev-1', name: 'phone' }),"
            " rename: (name) => Promise.resolve({ device_id: 'dev-1', name }) };")


def _setup(*, saved=None, raw: str | None = None, broken: str = "", shared: bool = False,
           ss: dict | None = None, manifest: dict | None = None) -> str:
    ls = {}
    if raw is not None:
        ls[KEY] = raw
    elif saved is not None:
        ls[KEY] = json.dumps(saved)
    return (f"const __LS = {json.dumps(ls)}; const __BROKEN = {json.dumps(broken)};"
            f" const __SS = {json.dumps(ss or {})}; const __MANIFEST = {json.dumps(manifest or _RADIO)};\n"
            + _SANDBOX + _device(shared))


def _sidebar(**kw) -> dict:
    return {
        "files": [COMPONENTS], "component": "Sidebar",
        "props": {"route": "music", "counts": {}, "manifest": kw.pop("manifest", _RADIO), "badges": {}},
        "fnProps": ["setRoute"], "setup": _setup(**kw),
        "script": ("const w = h.global('window'); h.render(); await w.__settle(h); return "
                   + _NAV_ROWS + ";"),
    }


def _grid(**kw) -> dict:
    return {
        "files": [COMPONENTS, HOME], "component": "HomeEverything",
        "props": {"counts": {}, "badges": {}, "shared": kw.get("shared", False)},
        "setup": _setup(**kw),
        "script": (
            "const w = h.global('window'); h.render(); await w.__settle(h);"
            "const tiles = h.findAll((e) => e.props.className === 'home-tile').map((e) => e.props.href);"
            "const edit = h.find((e) => w.__cls(e).includes('home-bar-edit'));"
            "await h.click((e) => w.__cls(e).includes('home-bar-edit'));"
            "return { tiles, edit: edit && { href: edit.props.href, text: edit.text },"
            " hash: w.location.hash, tab: w.__ss.get('domovoi.settings.tab') || null };"
        ),
    }


# The editor, driven: what it shows, and what each press stores.
_EDITOR_SNAP = r"""
  const w = h.global('window');
  const snap = () => ({
    rows: h.findAll((e) => w.__cls(e).includes('phone-bar-row')).map((e) => e.props['data-route']),
    preview: (h.find((e) => w.__cls(e).includes('phone-bar-preview')) || {}).text || null,
    adds: h.findAll((e) => e.type === 'button' && String(e.props.title || '').startsWith('add '))
      .map((e) => ({ title: e.props.title, disabled: !!e.props.disabled })),
    buttons: h.findAll({ type: 'button' }).map((e) => ({ title: e.props.title || null, text: w.__deep(e),
      disabled: !!e.props.disabled })),
    text: h.text().join(' '),
    stored: w.__ls.has('domovoi-phone-bar') ? JSON.parse(w.__ls.get('domovoi-phone-bar')) : null,
  });
  h.render(); await w.__settle(h);
"""

SCENARIOS: dict[str, dict] = {
    # ── the strip ──
    "default": _sidebar(),
    "custom": _sidebar(saved=["home", "calendar", "radio", "music"]),
    "custom_no_home_dupes_unknown_and_too_many": _sidebar(
        saved=["chat", "chat", "nope", "people", "files", "news", "calendar", "podcasts"]),
    "custom_left_with_home_only": _sidebar(saved=["home", "nope", "satellites"]),
    "junk_json": _sidebar(raw="{oops"),
    "not_a_list": _sidebar(raw='{"home": 1}'),
    "storage_blocked": _sidebar(saved=["home", "calendar"], broken="read"),
    "shared_drops_personal": _sidebar(saved=["home", "chat", "people", "music"], shared=True),
    "shared_falls_back": _sidebar(saved=["home", "chat", "people"], shared=True),
    "plugin_gone": _sidebar(saved=["home", "radio", "files"], manifest={"plugins": []}),
    # ── the grid ──
    "grid_default": _grid(),
    "grid_custom": _grid(saved=["home", "calendar", "radio"]),
    # ── the pure edit ──
    "edit_ops": {
        "files": [COMPONENTS], "component": "Sidebar",
        "props": {"route": "home", "counts": {}, "manifest": _RADIO, "badges": {}},
        "fnProps": ["setRoute"], "setup": _setup(),
        "script": r"""
          const e = h.global('window').phoneBarEdit;
          const d = ['home', 'music', 'videos', 'chat'];
          const five = ['home', 'music', 'videos', 'chat', 'calendar'];
          const two = ['home', 'music'];
          return {
            add: e(d, 'add', 'calendar'),
            addFull: e(five, 'add', 'files') === five,
            addHome: e(d, 'add', 'home') === d,
            addTwice: e(d, 'add', 'music') === d,
            remove: e(d, 'remove', 'videos'),
            removeAtMin: e(two, 'remove', 'music') === two,
            removeHome: e(d, 'remove', 'home') === d,
            left: e(d, 'left', 'chat'),
            right: e(d, 'right', 'music'),
            leftPastHome: e(d, 'left', 'music') === d,
            rightPastEnd: e(d, 'right', 'chat') === d,
            moveHome: e(d, 'right', 'home') === d,
            untouched: d,
          };
        """,
    },
    # ── the editor (Settings → Phone bar) ──
    "editor": {
        "files": [COMPONENTS, SETTINGS], "component": "PhoneBarCard", "fnProps": ["fire"],
        "setup": _setup(),
        "script": _EDITOR_SNAP + r"""
          const first = snap();
          await h.click({ type: 'button', title: 'add Calendar to the bar' });
          const added = snap();
          await h.click({ type: 'button', title: 'move Calendar left' });
          const moved = snap();
          await h.click({ type: 'button', title: 'take Music off the bar' });
          const removed = snap();
          await h.click({ type: 'button', title: 'add Radio to the bar' });
          await h.click({ type: 'button', title: 'add Files to the bar' });
          const full = snap();
          await h.click({ type: 'button', text: 'default' });
          const reset = snap();
          return { first, added, moved, removed, full, reset, writes: w.__writes,
                   fired: h.fnCalls.map((c) => c.args[0]) };
        """,
    },
    "editor_at_min": {
        "files": [COMPONENTS, SETTINGS], "component": "PhoneBarCard", "fnProps": ["fire"],
        "setup": _setup(saved=["home", "music"]),
        "script": _EDITOR_SNAP + "return snap();",
    },
    "editor_storage_refuses": {
        "files": [COMPONENTS, SETTINGS], "component": "PhoneBarCard", "fnProps": ["fire"],
        "setup": _setup(broken="write"),
        "script": _EDITOR_SNAP + r"""
          await h.click({ type: 'button', title: 'add Calendar to the bar' });
          return snap();
        """,
    },
    "editor_shared": {
        "files": [COMPONENTS, SETTINGS], "component": "PhoneBarCard", "fnProps": ["fire"],
        "setup": _setup(saved=["home", "chat", "music", "videos"], shared=True),
        "script": _EDITOR_SNAP + "return snap();",
    },
    # ── Satellites, a Settings tab ──
    "alias": {
        "files": [COMPONENTS], "component": "Sidebar",
        "props": {"route": "home", "counts": {}, "manifest": _RADIO, "badges": {}},
        "fnProps": ["setRoute"], "setup": _setup(),
        "script": r"""
          const w = h.global('window');
          const music = w.settingsTabRoute('music');
          const quiet = { ss: w.__ss.get('domovoi.settings.tab') || null, replaced: w.__replaced.slice(),
                          events: w.__events.length };
          const sats = w.settingsTabRoute('satellites');
          return { music, quiet, sats, ss: w.__ss.get('domovoi.settings.tab') || null,
                   replaced: w.__replaced, events: w.__events, aliases: w.SETTINGS_TAB_ROUTES };
        """,
    },
    "settings_on_satellites": {
        "files": SAT_FILES, "component": "(window.__tabs = SETTINGS_TABS, SettingsPage)",
        "setup": _setup(ss={"domovoi.settings.tab": "satellites"}),
        "api": {
            "GET /api/satellites": [{"room_id": "kitchen", "status": "online", "wifi": None,
                                     "now_playing": None, "sat_type": "voice"}],
            "GET /api/satellites/approvals": [],
            "GET /api/satellites/pending": [],
        },
        "script": r"""
          const w = h.global('window'); h.render(); await w.__settle(h);
          const headers = h.findAll((e) => w.__cls(e).includes('page-header') || e.type === 'h1')
            .map((e) => w.__deep(e));
          const summary = (h.find((e) => w.__cls(e).includes('sat-panel-summary')) || {}).text || null;
          const cards = h.findAll((e) => w.__cls(e).includes('sat-card')).length;
          const drawerBefore = h.findAll({ type: 'aside' }).length;
          await h.click((e) => w.__cls(e).includes('sat-card'));
          return { tabs: w.__tabs.map((t) => t.id), headers, summary, cards, drawerBefore,
                   drawerAfter: h.findAll({ type: 'aside' }).length,
                   stored: w.__ss.get('domovoi.settings.tab') || null };
        """,
    },
    "settings_switches_on_alias": {
        "files": SAT_FILES, "component": "SettingsPage",
        "setup": _setup(ss={"domovoi.settings.tab": "about"}),
        "api": {"GET /api/satellites": [], "GET /api/satellites/approvals": [],
                "GET /api/satellites/pending": []},
        "script": r"""
          const w = h.global('window'); h.render(); await w.__settle(h);
          const before = !!h.find((e) => w.__cls(e).includes('sat-panel'));
          w.settingsTabRoute('satellites'); await w.__settle(h);
          const after = !!h.find((e) => w.__cls(e).includes('sat-panel'));
          return { before, after, stored: w.__ss.get('domovoi.settings.tab') || null };
        """,
    },
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    # The file form: these scenarios are past Windows' ~32 KB command line.
    spec = tmp_path_factory.mktemp("phone-bar") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
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


def _strip(driven, name) -> list[str]:
    rows = [r for r in driven[name] if r["primary"]]
    return [r["route"] for r in sorted(rows, key=lambda r: r["strip"])]


# ─── the strip ───────────────────────────────────────────────────────────


def test_the_default_bar_is_home_music_videos_chat(driven) -> None:
    assert _strip(driven, "default") == DEFAULT
    js = _src("components.jsx")
    assert "const PHONE_BAR_DEFAULT = ['home', 'music', 'videos', 'chat'];" in js
    assert "const PHONE_BAR_MAX = 5;" in js and "const PHONE_BAR_MIN = 2;" in js


def test_a_device_picks_its_own_tabs_and_order_plugin_pages_included(driven) -> None:
    assert _strip(driven, "custom") == ["home", "calendar", "radio", "music"]
    # Every row still exists for the desktop sidebar; only the strip changed.
    routes = {r["route"] for r in driven["custom"]}
    assert {"videos", "chat", "podcasts"} <= routes


def test_home_leads_duplicates_and_unknown_pages_drop_and_five_is_the_cap(driven) -> None:
    assert _strip(driven, "custom_no_home_dupes_unknown_and_too_many") == [
        "home", "chat", "people", "files", "news"]


def test_a_bar_left_below_two_tabs_falls_back_to_the_default(driven) -> None:
    # "satellites" is no page a launcher offers any more: it is dropped too.
    assert _strip(driven, "custom_left_with_home_only") == DEFAULT
    # A plugin removed since: its tab goes, the rest stays.
    assert _strip(driven, "plugin_gone") == ["home", "files"]


@pytest.mark.parametrize("name", ["junk_json", "not_a_list", "storage_blocked"])
def test_broken_storage_is_the_default_never_a_broken_shell(driven, name) -> None:
    assert _strip(driven, name) == DEFAULT


def test_a_shared_screen_never_shows_personal_pages_whatever_the_bar_says(driven) -> None:
    assert _strip(driven, "shared_drops_personal") == ["home", "music"]
    assert not {"chat", "people"} & {r["route"] for r in driven["shared_drops_personal"]}
    # Nothing of the saved bar left but Home: the default, minus chat.
    assert _strip(driven, "shared_falls_back") == ["home", "music", "videos"]


def test_satellites_is_on_no_launcher(driven) -> None:
    assert "satellites" not in {r["route"] for r in driven["default"]}
    assert "#satellites" not in driven["grid_default"]["tiles"]
    nav = re.search(r"const CORE_NAV_ITEMS = \[(.*?)\];", _src("components.jsx"), re.S).group(1)
    assert "'satellites'" not in nav
    # ...and no sidebar count for it: Settings has no badge.
    counts = _src("data.js")
    counts = counts[counts.index("const useSidebarCounts"):counts.index("// ─── Device identity")]
    assert "/api/satellites" not in counts and "satellites:" not in counts


# ─── the grid ────────────────────────────────────────────────────────────


def test_the_grid_holds_what_the_bar_does_not(driven) -> None:
    tiles = driven["grid_default"]["tiles"]
    assert not {f"#{r}" for r in DEFAULT} & set(tiles)
    assert {"#calendar", "#radio", "#settings", "#manual"} <= set(tiles)
    custom = driven["grid_custom"]["tiles"]
    assert {"#music", "#videos", "#chat"} <= set(custom)
    assert not {"#home", "#calendar", "#radio"} & set(custom)


def test_the_grid_links_to_the_bar_editor(driven) -> None:
    out = driven["grid_default"]
    assert out["edit"] == {"href": "#settings", "text": "edit the bar"}
    assert out["tab"] == "phonebar" and out["hash"] == "settings"


# ─── the editor ──────────────────────────────────────────────────────────


def test_the_edit_rules(driven) -> None:
    o = driven["edit_ops"]
    assert o["add"] == ["home", "music", "videos", "chat", "calendar"]
    assert o["remove"] == ["home", "music", "chat"]
    assert o["left"] == ["home", "music", "chat", "videos"]
    assert o["right"] == ["home", "videos", "music", "chat"]
    for no_op in ("addFull", "addHome", "addTwice", "removeAtMin", "removeHome",
                  "leftPastHome", "rightPastEnd", "moveHome"):
        assert o[no_op] is True, no_op
    assert o["untouched"] == DEFAULT                 # an edit never changes its input


def test_the_editor_adds_moves_removes_and_resets(driven) -> None:
    o = driven["editor"]
    assert o["first"]["rows"] == DEFAULT and o["first"]["stored"] is None
    assert o["first"]["preview"] == "home · music · videos · chat"
    # Pages a launcher offers here are offered, plugin pages included.
    offered = {a["title"] for a in o["first"]["adds"]}
    assert {"add Calendar to the bar", "add Radio to the bar", "add Files to the bar"} <= offered
    assert "add Satellites to the bar" not in offered
    assert o["added"]["stored"] == ["home", "music", "videos", "chat", "calendar"]
    assert o["moved"]["stored"] == ["home", "music", "videos", "calendar", "chat"]
    assert o["removed"]["stored"] == ["home", "videos", "calendar", "chat"]
    # Five is full: the last add went nowhere, and every add is disabled.
    assert o["full"]["stored"] == ["home", "videos", "calendar", "chat", "radio"]
    assert o["full"]["adds"] and all(a["disabled"] for a in o["full"]["adds"])
    assert "the bar is full (5)" in o["full"]["text"]
    # Back to the default: the saved bar is gone, not a copy of the default.
    assert o["reset"]["stored"] is None and o["reset"]["rows"] == DEFAULT
    assert o["writes"][-1] == ["domovoi-phone-bar", None]
    assert o["fired"] == ["phone bar back to home · music · videos · chat"]


def test_home_has_no_buttons_and_two_tabs_cannot_shrink(driven) -> None:
    o = driven["editor_at_min"]
    assert o["rows"] == ["home", "music"]
    titles = {b["title"]: b["disabled"] for b in o["buttons"] if b["title"]}
    assert not any("Home" in t for t in titles)
    assert titles["take Music off the bar"] is True
    assert "always first" in o["text"]


def test_a_browser_that_cannot_keep_it_keeps_it_for_the_visit_and_says_so(driven) -> None:
    o = driven["editor_storage_refuses"]
    assert o["rows"] == ["home", "music", "videos", "chat", "calendar"]
    assert o["stored"] is None
    assert "can’t keep it" in o["text"]


def test_the_editor_on_a_shared_screen_offers_no_personal_page(driven) -> None:
    o = driven["editor_shared"]
    assert o["rows"] == ["home", "music", "videos"]
    offered = {a["title"] for a in o["adds"]}
    for personal in ("Chat", "People", "Files", "News"):
        assert f"add {personal} to the bar" not in offered
    assert "shared screen leaves people, chat, files and news off" in o["text"]


def test_the_editor_is_a_settings_tab_of_its_own(driven) -> None:
    tabs = driven["settings_on_satellites"]["tabs"]
    assert tabs.index("phonebar") == tabs.index("devices") + 1
    settings = _src("settings.jsx")
    panel = settings[settings.index("const PhoneBarPanel"):]
    assert "<PhoneBarCard fire={fire}/>" in panel[:panel.index("};")]
    assert "{tab === 'phonebar' && <PhoneBarPanel/>}" in settings
    # Not under Devices, whose other cards ask an admin to sign in.
    devices = settings[settings.index("const DevicesPanel"):]
    assert "PhoneBarCard" not in devices[:devices.index("};")]


# ─── Satellites, a Settings tab ──────────────────────────────────────────


def test_the_old_route_opens_the_settings_tab(driven) -> None:
    o = driven["alias"]
    assert o["music"] == "music"
    assert o["quiet"] == {"ss": None, "replaced": [], "events": 0}
    assert o["sats"] == "settings"
    assert o["ss"] == "satellites"
    assert o["replaced"] == ["#settings"]
    assert o["events"] == [{"type": "domovoi:settings-tab", "detail": {"tab": "satellites"}}]
    assert o["aliases"] == {"satellites": "satellites"}


def test_the_shell_resolves_the_alias_on_load_on_hashchange_and_on_setroute() -> None:
    html = _src("index.html")
    assert "React.useState(() => settingsTabRoute(initialRoute))" in html
    assert "setRouteState(settingsTabRoute((window.location.hash || '#home').slice(1)))" in html
    assert "const to = settingsTabRoute(r);" in html
    # The route stays a core one, so no plugin can take #satellites.
    block = re.search(r"window\.DomovoiCore\.pages = \{(.*?)\};", html, re.S).group(1)
    assert re.search(r"^\s*satellites: \(\) => window\.SettingsPage,", block, re.M)
    assert "satellites" in CORE_WEB_ROUTES and "satellites" in plugin_host.CORE_ROUTES


def test_settings_opens_on_satellites_with_the_whole_view(driven) -> None:
    o = driven["settings_on_satellites"]
    tabs = o["tabs"]
    assert tabs.index("satellites") == tabs.index("phonebar") + 1
    assert o["stored"] is None                       # the deep link was read once
    assert o["summary"] == "1 online · 0 offline · 1 known"
    assert o["cards"] == 1
    # One page header (Settings), not a second "Satellites" one.
    assert "Satellites" not in " ".join(o["headers"])
    # The room drawer still opens from a card.
    assert o["drawerBefore"] == 0 and o["drawerAfter"] == 1


def test_a_settings_page_already_on_screen_switches_to_the_tab(driven) -> None:
    o = driven["settings_switches_on_alias"]
    assert o == {"before": False, "after": True, "stored": None}


def test_homes_satellite_links_all_use_the_alias() -> None:
    home = _src("home.jsx")
    hrefs = re.findall(r"href(?:=|: )['\"]#(\w+)['\"]", home)
    assert hrefs.count("satellites") >= 8          # problem rows, room cards, "add a satellite"
    assert "on the satellites page" not in home
