"""The sidebar's groups, and People as a Settings section (2026-11-09).

Owner design:

* the desktop sidebar is Home (the brand row) · Chat · **Media ▸** (Music,
  Podcasts, Audiobooks, Videos, and plugin pages that ask for it, such as
  the radio plugin's Stations) · **Tools ▸** (News, Calendar, Files) ·
  Plugins; a plugin page with no group is a top-level row, as before;
* the group names and core members are ONE table (components.jsx
  NAV_GROUPS), so renaming a group is one word;
* ``resolveNavTree`` builds the tree every launcher draws from three
  inputs: the table, the plugins' declared groups (``nav_group``) and a
  household's own layout (``{groups: [{id, label, items}], top: [...]}``,
  null for now; a later Settings → App layout editor writes it);
* a group with exactly one page left is that page's row; an empty group is
  not drawn (a shared screen hides News and Files: Tools is then Calendar);
* groups open in place: a button with ``aria-expanded``; Media starts
  open, the group of the page on screen opens, and each browser remembers
  the rest (localStorage, a convenience that may fail);
* at phone width the groups dissolve: the strip still shows only the
  phone bar's rows, in the bar's order;
* People left the main nav: it is Settings → Household → People (Rooms &
  devices was renamed Household; App layout moved to System). ``#people``
  and anything after it land there; a shared screen doesn't list it.

Driven through domovoi/tests/jsx_interact_harness.js, like
test_web_home_shell.py. No DB; needs ``node``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.plugins_runtime.manifest import NAV_GROUPS
from domovoi.tests.test_web_home_shell import _phone_block

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = "web/static/components.jsx"
HOME = "web/static/home.jsx"
SETTINGS = "web/static/settings.jsx"
PEOPLE = "web/static/people.jsx"

_STATIONS = {"slug": "radio", "pages": [
    {"route": "stations", "page": "StationsPage", "nav_label": "Stations", "nav_order": 50,
     "nav_group": "media", "badge": {"endpoint": "/api/plugins/radio/badge", "key": "favorites"}}]}
_IMAGES = {"slug": "imagegen", "pages": [
    {"route": "images", "page": "ImagesPage", "nav_label": "Images", "nav_order": 16, "nav_group": "media"}]}
_LOOSE = {"slug": "loose", "pages": [
    {"route": "loose", "page": "LoosePage", "nav_label": "Loose", "nav_order": 60}]}

_SETUP = r"""
ServerStore = { current: () => null, currentLabel: () => 'box:6369' };
const __st = setTimeout;
setTimeout = (fn, ms, ...a) => { const t = __st(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };
window.__ls = new Map(Object.entries(__LS));
window.__writes = [];
localStorage = {
  getItem: (k) => { if (__BROKEN) throw new Error('blocked'); return window.__ls.has(k) ? window.__ls.get(k) : null; },
  setItem: (k, v) => { if (__BROKEN) throw new Error('blocked'); window.__writes.push([k, String(v)]); window.__ls.set(k, String(v)); },
  removeItem: (k) => { if (__BROKEN) throw new Error('blocked'); window.__ls.delete(k); },
};
// A data.js helper the People page uses that the harness doesn't script.
if (typeof useRetryAfterCredential === 'undefined') useRetryAfterCredential = () => {};
window.history = { replaceState: (s, t, url) => { window.location.hash = url; },
                   pushState: (s, t, url) => { window.location.hash = url; } };
DeviceIdentity = { id: () => 'dev-1', sharedScreen: () => __SHARED, canRegister: () => true,
                   subscribeShared: () => () => {} };
"""


def _setup(ls=None, shared=False, broken=False, hash_=""):
    return (f"const __LS = {json.dumps(ls or {})}; const __BROKEN = {json.dumps(broken)};"
            f" const __SHARED = {json.dumps(shared)}; window.location.hash = {json.dumps(hash_)};"
            + _SETUP)


# What a Sidebar render says: each row (route, classes, the group it sits
# in) and each group head (id, label, aria-expanded, dot).
_READ = r"""
const cls = (e) => String((e.props && e.props.className) || '').split(' ');
const rows = h.findAll((e) => cls(e).includes('nav-item')).map((e) => {
  const box = h.ancestors(e).find((a) => cls(a).includes('nav-group-items'));
  return { route: e.props.key, type: e.type, href: e.props.href || null, cls: cls(e).sort(),
           group: box ? box.props.id.replace('nav-group-', '') : null,
           current: e.props['aria-current'] || null };
});
const heads = h.findAll((e) => cls(e).includes('nav-group-head')).map((e) => ({
  type: e.type, controls: e.props['aria-controls'], expanded: e.props['aria-expanded'],
  label: (h.findAll((x) => h.ancestors(x).includes(e) && x.type === 'span')[0] || {}).text || null,
  dot: h.findAll((x) => h.ancestors(x).includes(e) && cls(x).includes('nav-group-dot')).length > 0,
}));
const groups = h.findAll((e) => cls(e).includes('nav-group')).map((e) => ({ id: e.props['data-group'], cls: cls(e).sort() }));
return { rows, heads, groups, writes: h.global('window').__writes };
"""


def _sidebar(route, plugins=(), *, ls=None, shared=False, broken=False, counts=None, badges=None,
             before=""):
    return {
        "files": [COMPONENTS], "component": "Sidebar",
        "props": {"route": route, "counts": counts or {"music": 3},
                  "manifest": {"plugins": list(plugins)}, "badges": badges or {}},
        "fnProps": ["setRoute"], "setup": _setup(ls, shared, broken),
        "script": "h.render(); await h.settle(); h.rerender();" + before + _READ,
    }


def _toggle(group):
    return (f"await h.click((e) => e.props && e.props['aria-controls'] === 'nav-group-{group}');"
            " h.rerender();")


def _resolve(plugins, override, *, shared=False):
    return {
        "files": [COMPONENTS], "component": "Sidebar",
        "props": {"route": "home", "counts": {}, "manifest": {"plugins": []}, "badges": {}},
        "fnProps": ["setRoute"], "setup": _setup(shared=shared),
        "script": (
            "const w = h.global('window');"
            f" const items = w.navItemsFor({json.dumps({'plugins': list(plugins)})}, {{ shared: {json.dumps(shared)}, ai: true }});"
            f" return w.resolveNavTree(items, {json.dumps(override)}).map((e) => e.kind === 'item'"
            " ? e.item.route : { group: e.id, label: e.label, open: e.open, items: e.items.map((i) => i.route) });"
        ),
    }


SCENARIOS = {
    # ── the resolver, pure ──────────────────────────────────────────────
    "r_default": _resolve([], None),
    "r_plugins": _resolve([_STATIONS, _IMAGES, _LOOSE], None),
    "r_shared": _resolve([_STATIONS], None, shared=True),
    "r_rename_and_move": _resolve([_STATIONS], {
        "groups": [{"id": "media", "label": "Watch & listen", "items": ["files"]},
                   {"id": "tools", "label": "Tools", "items": []}],
        "top": []}),
    "r_new_group": _resolve([_STATIONS], {
        "groups": [{"id": "kids", "label": "Kids", "items": ["videos", "bogus", "stations", "videos"]},
                   {"id": "media", "label": "Media", "items": []},
                   {"id": "tools", "label": "Tools", "items": []}],
        "top": ["calendar", "bogus"]}),
    "r_dropped_group": _resolve([], {"groups": [{"id": "media", "label": "Media", "items": []}]}),
    "r_junk_override": _resolve([], {"groups": "nope", "top": 7}),
    "r_lone_plugin_group": _resolve([{"slug": "games", "pages": [
        {"route": "games", "page": "G", "nav_label": "Games", "nav_order": 70, "nav_group": "nope"}]}], None),
    # ── the sidebar ─────────────────────────────────────────────────────
    "s_cold_music": _sidebar("music", [_STATIONS]),
    "s_cold_news": _sidebar("news", [_STATIONS]),
    "s_home": _sidebar("home", [_STATIONS]),
    "s_shared": _sidebar("music", [_STATIONS], shared=True),
    "s_toggle_tools": _sidebar("home", [_STATIONS], before=_toggle("tools")),
    "s_fold_media_on_music": _sidebar("music", before=_toggle("media")),
    "s_remembered": _sidebar("home", ls={"domovoi-nav-groups": json.dumps({"media": False, "tools": True})}),
    "s_broken_storage": _sidebar("home", broken=True, before=_toggle("tools")),
    "s_dot": _sidebar("home", [_STATIONS], ls={"domovoi-nav-groups": json.dumps({"media": False})},
                      badges={"stations": 2}),
    "s_people_on_bar": _sidebar("settings", ls={"domovoi-phone-bar": json.dumps(["music", "people"])},
                                counts={"people": 4}),
    # ── Home's grid ─────────────────────────────────────────────────────
    "grid": {
        "files": [COMPONENTS, HOME], "component": "HomeEverything",
        "props": {"counts": {}, "badges": {}},
        "setup": _setup() + " window.DomovoiPluginManifest = " + json.dumps({"plugins": [_STATIONS]}) + ";",
        "script": (
            "h.render(); const cls = (e) => String((e.props && e.props.className) || '');"
            " return h.findAll((e) => cls(e) === 'home-grid-block').map((b) => ({"
            " head: (h.findAll((x) => h.ancestors(x).includes(b) && cls(x) === 'home-grid-head')[0] || {}).text || null,"
            " tiles: h.findAll((x) => h.ancestors(x).includes(b) && cls(x) === 'home-tile').map((t) => t.props.href) }));"
        ),
    },
    # ── Settings ────────────────────────────────────────────────────────
    "settings_people": {
        "files": [COMPONENTS, PEOPLE, SETTINGS], "component": "SettingsPage",
        "setup": _setup(hash_="#settings/people"),
        "api": {"GET /api/people": [{"id": 1, "name": "Alex", "last_seen_at": None}],
                "GET /api/denylist": []},
        "script": r"""
          const w = h.global('window');
          h.render(); await h.settle(); h.rerender();
          const cls = (e) => String((e.props && e.props.className) || '');
          return { hash: w.location.hash,
                   title: (h.find((e) => cls(e) === 'settings-section-title') || {}).text || null,
                   section: (h.find((e) => cls(e) === 'settings-content') || { props: {} }).props['data-section'],
                   pageHeaders: h.findAll((e) => cls(e).includes('page-header')).length,
                   count: (h.find((e) => cls(e) === 'people-count') || {}).text || null,
                   roster: h.text().includes('Alex'),
                   groups: w.settingsLayout().groups.map((g) => [g.id, g.label, g.sections.map((s) => s.id)]) };
        """,
    },
    "settings_people_shared": {
        "files": [COMPONENTS, PEOPLE, "web/static/satellite_media.jsx", "web/static/satellites.jsx",
                  SETTINGS],
        "component": "SettingsPage",
        "setup": _setup(shared=True, hash_="#settings/household"),
        "api": {"GET /api/people": [], "GET /api/denylist": [], "GET /api/satellites": [],
                "GET /api/satellites/approvals": [], "GET /api/satellites/pending": []},
        "script": r"""
          const w = h.global('window');
          h.render(); await h.settle(); h.rerender();
          const cls = (e) => String((e.props && e.props.className) || '');
          return { links: h.findAll((e) => e.type === 'a' && cls(e).includes('settings-nav-item')).map((e) => e.props.href),
                   layout: w.settingsLayout({ shared: true }).groups.find((g) => g.id === 'household').sections.map((s) => s.id),
                   search: w.settingsSearchIndex(w.settingsLayout({ shared: true }).groups, []).map((x) => x.key) };
        """,
    },
    "settings_old_group": {
        "files": [COMPONENTS, "web/static/satellite_media.jsx", "web/static/satellites.jsx", SETTINGS],
        "component": "SettingsPage",
        "setup": _setup(hash_="#settings/rooms-and-devices"),
        "api": {"GET /api/satellites": [], "GET /api/satellites/approvals": [],
                "GET /api/satellites/pending": []},
        "script": r"""
          const w = h.global('window');
          h.render(); await h.settle(); h.rerender();
          return { hash: w.location.hash,
                   people: w.resolveRoute('#people'), deep: w.resolveRoute('#people/memory'),
                   rooms: w.resolveRoute('#satellites') };
        """,
    },
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    # The file form: the scenarios are past Windows' command-line limit.
    spec = tmp_path_factory.mktemp("nav-groups") / "scenarios.json"
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


# ─── the one table ───────────────────────────────────────────────────────


def test_one_table_names_the_groups_and_the_manifest_allows_the_same_ids() -> None:
    comps = _src("components.jsx")
    block = comps[comps.index("const NAV_GROUPS = ["):]
    block = block[:block.index("];")]
    assert re.findall(r"id: '(\w+)'", block) == list(NAV_GROUPS) == ["media", "tools"]
    assert re.findall(r"label: '([^']+)'", block) == ["Media", "Tools"]
    assert "routes: ['music', 'podcasts', 'audiobooks', 'videos']" in block
    assert "routes: ['news', 'calendar', 'files']" in block
    # Media starts open, so Music is one click from a cold load.
    assert re.search(r"id: 'media'[^}]*open: true", block)
    # The labels live nowhere else in the shell.
    assert comps.count("'Media'") == 1 and comps.count("'Tools'") == 1


# ─── the resolver ────────────────────────────────────────────────────────

MEDIA = {"group": "media", "label": "Media", "open": True,
         "items": ["music", "podcasts", "audiobooks", "videos"]}
TOOLS = {"group": "tools", "label": "Tools", "open": False, "items": ["news", "calendar", "files"]}


def test_the_defaults(driven) -> None:
    assert driven["r_default"] == ["chat", MEDIA, TOOLS, "plugins"]


def test_plugin_pages_join_their_group_or_stay_top_level(driven) -> None:
    media = {**MEDIA, "items": ["music", "podcasts", "audiobooks", "videos", "images", "stations"]}
    assert driven["r_plugins"] == ["chat", media, TOOLS, "loose", "plugins"]


def test_an_unknown_group_is_top_level(driven) -> None:
    assert driven["r_lone_plugin_group"] == ["chat", MEDIA, TOOLS, "games", "plugins"]


def test_a_shared_screen_leaves_one_tool_which_is_a_plain_row(driven) -> None:
    # News and Files are hidden there, Chat too: Tools is Calendar alone.
    media = {**MEDIA, "items": MEDIA["items"] + ["stations"]}
    assert driven["r_shared"] == [media, "calendar", "plugins"]


def test_an_override_renames_a_group_and_moves_a_page_into_it(driven) -> None:
    out = driven["r_rename_and_move"]
    assert out == ["chat",
                   {"group": "media", "label": "Watch & listen", "open": True,
                    "items": ["files", "music", "podcasts", "audiobooks", "videos", "stations"]},
                   {"group": "tools", "label": "Tools", "open": False, "items": ["news", "calendar"]},
                   "plugins"]


def test_an_override_makes_a_group_ignores_unknown_routes_and_lifts_one_to_the_top(driven) -> None:
    out = driven["r_new_group"]
    assert out == ["chat",
                   {"group": "media", "label": "Media", "open": True,
                    "items": ["music", "podcasts", "audiobooks"]},
                   {"group": "kids", "label": "Kids", "open": False, "items": ["videos", "stations"]},
                   {"group": "tools", "label": "Tools", "open": False, "items": ["news", "files"]},
                   "calendar", "plugins"]


def test_a_group_the_override_leaves_out_is_gone_and_its_pages_go_top_level(driven) -> None:
    assert driven["r_dropped_group"] == ["chat", MEDIA, "news", "calendar", "files", "plugins"]


def test_a_malformed_override_changes_nothing_it_cannot_read(driven) -> None:
    # No readable groups: the defaults' groups; an unreadable top: nothing lifted.
    assert driven["r_junk_override"] == ["chat", MEDIA, TOOLS, "plugins"]


# ─── the sidebar ─────────────────────────────────────────────────────────


def _rows(o):
    return {r["route"]: r for r in o["rows"]}


def _heads(o):
    return {h["controls"].replace("nav-group-", ""): h for h in o["heads"]}


def test_the_sidebar_order_and_where_each_page_sits(driven) -> None:
    o = driven["s_cold_music"]
    order = [r["route"] for r in o["rows"]]
    assert order == ["home", "chat", "music", "podcasts", "audiobooks", "videos", "stations",
                     "news", "calendar", "files", "plugins"]
    rows = _rows(o)
    assert {r for r, v in rows.items() if v["group"] == "media"} == {
        "music", "podcasts", "audiobooks", "videos", "stations"}
    assert {r for r, v in rows.items() if v["group"] == "tools"} == {"news", "calendar", "files"}
    assert rows["chat"]["group"] is None and rows["plugins"]["group"] is None
    # People is in Settings now: no row.
    assert "people" not in rows
    # Rows are links (keyboard, middle-click); the page on screen says so.
    assert rows["music"]["type"] == "a" and rows["music"]["href"] == "#music"
    assert rows["music"]["current"] == "page" and rows["news"]["current"] is None


def test_group_heads_are_buttons_that_say_whether_they_are_open(driven) -> None:
    heads = _heads(driven["s_cold_music"])
    assert set(heads) == {"media", "tools"}
    assert all(h["type"] == "button" for h in heads.values())
    assert heads["media"]["expanded"] is True       # open on a cold load
    assert heads["tools"]["expanded"] is False
    assert heads["media"]["label"] == "Media" and heads["tools"]["label"] == "Tools"


def test_the_group_of_the_page_on_screen_is_open(driven) -> None:
    heads = _heads(driven["s_cold_news"])
    assert heads["tools"]["expanded"] is True
    groups = {g["id"]: g["cls"] for g in driven["s_cold_news"]["groups"]}
    assert "has-active" in groups["tools"]


def test_toggling_is_remembered_in_this_browser(driven) -> None:
    o = driven["s_toggle_tools"]
    assert _heads(o)["tools"]["expanded"] is True
    assert o["writes"][-1][0] == "domovoi-nav-groups"
    assert json.loads(o["writes"][-1][1]) == {"tools": True}
    remembered = _heads(driven["s_remembered"])
    assert remembered["media"]["expanded"] is False and remembered["tools"]["expanded"] is True


def test_the_group_on_screen_can_still_be_folded(driven) -> None:
    o = driven["s_fold_media_on_music"]
    assert _heads(o)["media"]["expanded"] is False
    assert json.loads(o["writes"][-1][1]) == {"media": False}


def test_storage_that_fails_still_toggles_for_the_visit(driven) -> None:
    o = driven["s_broken_storage"]
    assert _heads(o)["tools"]["expanded"] is True
    assert _heads(o)["media"]["expanded"] is True


def test_a_folded_group_shows_a_quiet_dot_for_a_count_inside(driven) -> None:
    assert _heads(driven["s_dot"])["media"]["dot"] is True
    assert _heads(driven["s_dot"])["tools"]["dot"] is False
    # Open, the count is on the row itself.
    assert _heads(driven["s_cold_music"])["media"]["dot"] is False


def test_a_shared_screen_keeps_the_rule_and_drops_lone_groups(driven) -> None:
    o = driven["s_shared"]
    rows = _rows(o)
    assert not {"people", "chat", "files", "news"} & set(rows)
    assert rows["calendar"]["group"] is None        # Tools with one page: a plain row
    assert set(_heads(o)) == {"media"}


def test_a_phone_bar_saved_with_people_keeps_its_tab(driven) -> None:
    rows = _rows(driven["s_people_on_bar"])
    people = rows["people"]
    assert "primary" in people["cls"] and "bar-only" in people["cls"]
    assert people["group"] is None
    # Not on the bar: no People row anywhere.
    assert "people" not in _rows(driven["s_home"])


def test_the_css_folds_groups_on_a_desktop_and_dissolves_them_on_a_phone() -> None:
    css = _src("styles.css")
    assert ".nav-group:not(.open) > .nav-group-items { display: none; }" in css
    assert ".sidebar .nav-item.bar-only { display: none; }" in css
    phone = _phone_block(css)
    assert ".sidebar .nav-group:not(.open) > .nav-group-items { display: contents; }" in phone
    assert ".sidebar .nav-group-head { display: none; }" in phone
    assert ".sidebar .nav-item.bar-only.primary { display: flex; }" in phone


# ─── Home's grid ─────────────────────────────────────────────────────────


def test_the_grid_has_the_same_groups(driven) -> None:
    blocks = driven["grid"]
    assert blocks == [
        {"head": "media", "tiles": ["#podcasts", "#audiobooks", "#stations"]},
        {"head": "tools", "tiles": ["#news", "#calendar", "#files"]},
        {"head": None, "tiles": ["#plugins", "#settings", "#manual"]},
    ]


# ─── Settings → Household → People ───────────────────────────────────────


def test_people_is_a_settings_section_and_the_old_address_lands_there(driven) -> None:
    o = driven["settings_people"]
    assert o["hash"] == "#settings/people"
    assert o["section"] == "people" and o["title"] == "People"
    # One page header (Settings), the People page's count line, its roster.
    assert o["pageHeaders"] == 1
    assert o["count"] == "1 enrolled · 0 heard in the last 5 min"
    assert o["roster"] is True
    groups = {g[0]: g for g in o["groups"]}
    assert groups["household"][1] == "Household"
    assert groups["household"][2] == ["rooms", "people", "phones-and-browsers"]
    # (Health shows once health.jsx registers its panel; not loaded here.)
    assert groups["system"][2] == ["about", "app-layout", "advanced"]
    assert "rooms-and-devices" not in groups


def test_the_old_group_address_and_page_addresses_resolve(driven) -> None:
    o = driven["settings_old_group"]
    assert o["hash"] == "#settings/household"
    assert o["people"] == "settings/people" and o["deep"] == "settings/people/memory"
    assert o["rooms"] == "settings/rooms"


def test_a_shared_screen_does_not_list_people_in_settings(driven) -> None:
    o = driven["settings_people_shared"]
    assert "#settings/people" not in o["links"]
    assert "people" not in o["layout"]
    assert not [k for k in o["search"] if k.startswith("people")]


def test_people_route_stays_reserved_and_opens_settings() -> None:
    html = _src("index.html")
    block = re.search(r"window\.DomovoiCore\.pages = \{(.*?)\};", html, re.S).group(1)
    assert re.search(r"^\s*people: \(\) => window\.SettingsPage,", block, re.M)
