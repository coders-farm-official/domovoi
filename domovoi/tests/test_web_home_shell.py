"""Home is the dashboard's default page, and the shell leads to it.

The shell half of the Home page (design-notes HOME-PLAN.md, approved
2026-09-26):

* ``home`` is a CORE route in all three places test_plugin_web_routes keeps
  in step (index.html's ``DomovoiCore.pages``, the core's
  ``CORE_WEB_ROUTES``, plugin_host's ``CORE_NAV``), so a plugin can never
  claim ``#home``; its published nav order sorts before chat's 8.
* a bare URL, the unknown-route fallback and the installed app
  (``manifest.webmanifest`` ``start_url``, name "Domovoi") land on Home;
  deep links such as ``#music`` keep working because a known route still
  resolves to its own page.
* the way home: the desktop sidebar's brand row is an ``<a href="#home">``
  (no longer a dead ``div`` carrying a fake "/ 1.0"), and the topbar's
  "domovoi" crumb is the same link — the only one on a phone, where the
  brand row is hidden.
* the phone strip (760px and below) keeps only the five ``primary`` tabs —
  home · music · satellites · calendar · chat, in that order — and every
  other page sits on Home's "everything" grid. The desktop sidebar keeps
  every item except a separate home row.
* a shared screen (an admin marks the device) drops the personal pages
  (People, Chat, Files, News) from every launcher — the sidebar, the strip
  and the grid — through one filter, ``navItemsFor``.
* plugin nav badges are polled once, by App, for the sidebar and the grid.
* the docked mini-player lifts above the strip on a phone instead of
  covering it, and ``.main`` keeps room for it.

The JSX is driven through domovoi/tests/jsx_interact_harness.js (the
dashboard's own Babel, a small stateful React). The harness has no layout
engine, so the 375px behaviour is pinned the way the browser decides it:
by the classes the components emit and the CSS rules those classes meet
inside the 760px media block.

No DB, never ``requires_db``; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from domovoi.plugins_runtime.contracts import ContractReport, check_web_routes
from domovoi.plugins_runtime.manifest import CORE_WEB_ROUTES, parse_manifest
from web.backend import plugin_host

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = "web/static/components.jsx"
HOME = "web/static/home.jsx"

PRIMARY = {"home", "music", "satellites", "calendar", "chat"}

_SETUP = (
    "ServerStore = { current: () => null, currentLabel: () => 'box:6369' };"
    # A plugin badge re-polls every 30 s; that timer must not hold node open.
    " const __st = setTimeout;"
    " setTimeout = (fn, ms, ...a) => { const t = __st(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };"
)
_RADIO = {"plugins": [{"slug": "radio", "pages": [
    {"route": "radio", "page": "RadioPage", "nav_label": "Radio", "nav_order": 11}]}]}

_NAV_ROWS = (
    "return h.tree().filter((e) => String(e.props.className || '').split(' ').includes('nav-item'))"
    ".map((e) => ({ route: e.props.key, cls: String(e.props.className).split(' ').sort(),"
    " strip: (e.props.style || {})['--strip-order'] ?? null }));"
)
_BADGED = {"plugins": [{"slug": "radio", "pages": [
    {"route": "radio", "page": "RadioPage", "nav_label": "Radio", "nav_order": 11,
     "badge": {"endpoint": "/api/plugins/radio/badge", "key": "live"}}]}]}


def _device(shared) -> str:
    """data.js's DeviceIdentity as the Sidebar sees it: the server's answer
    (true / false, or null for "not known yet") and whether this browser
    can register at all ("unpaired": it can't, and never learns)."""
    answer = {True: "true", False: "false", None: "null", "unpaired": "null"}[shared]
    can = "false" if shared == "unpaired" else "true"
    return (f" DeviceIdentity = {{ id: () => 'dev-1', sharedScreen: () => {answer},"
            f" canRegister: () => {can}, subscribeShared: () => () => {{}} }};")


def _sidebar(route: str, *, shared=False, props: dict | None = None) -> dict:
    return {
        "files": [COMPONENTS], "component": "Sidebar",
        "props": {"route": route, "counts": {"music": 3}, "manifest": _RADIO, **(props or {})},
        "fnProps": ["setRoute"], "setup": _SETUP + _device(shared),
        "script": (
            "h.render(); await h.settle(); h.rerender();"
            "const brand = h.find((e) => String(e.props.className || '') === 'brand-row');"
            "const rows = (() => {" + _NAV_ROWS + "})();"
            "return { brand: h.plain(brand), rows, text: h.text(),"
            " gets: h.calls.filter((c) => c.method === 'GET').map((c) => c.path) };"
        ),
    }


SCENARIOS = {
    "sidebar_on_podcasts": _sidebar("podcasts"),
    "sidebar_on_music": _sidebar("music"),
    "sidebar_on_home": _sidebar("home"),
    # A shared screen, and one whose answer hasn't come yet (a paired
    # browser: masked until the server says) - and an unpaired browser,
    # which never learns and so is never masked.
    "sidebar_shared": _sidebar("music", shared=True),
    "sidebar_shared_not_known_yet": _sidebar("music", shared=None),
    "sidebar_unpaired_not_known": _sidebar("music", shared="unpaired"),
    # The badge poll: a Sidebar handed App's badges polls nothing itself.
    "sidebar_polls_alone": _sidebar("music", props={"manifest": _BADGED}),
    "sidebar_given_badges": _sidebar("music", props={"manifest": _BADGED, "badges": {"radio": 4}}),
    "topbar_chat": {
        "files": [COMPONENTS], "component": "Topbar",
        "props": {"route": "chat", "theme": "light"}, "fnProps": ["setRoute", "setTheme"],
        "setup": _SETUP,
        "script": ("h.render(); return { links: h.findAll({ type: 'a' }).map(h.plain),"
                   " crumb: h.findAll({ type: 'strong' }).map((e) => e.text) };"),
    },
    # Home's "everything" grid, the phone's way to every page off the strip
    # (the whole page is driven by test_web_home_page.py).
    "home_everything": {
        "files": [COMPONENTS, HOME], "component": "HomeEverything",
        "props": {"counts": {"people": 2, "music": 7}},
        "setup": _SETUP + " window.DomovoiPluginManifest = " + json.dumps(_RADIO) + ";",
        "script": ("h.render(); return { tiles: h.findAll((e) => e.props.className === 'home-tile')"
                   ".map((e) => e.props.href), text: h.text() };"),
    },
    "home_everything_shared": {
        "files": [COMPONENTS, HOME], "component": "HomeEverything",
        "props": {"counts": {}, "shared": True, "badges": {}},
        "setup": _SETUP + " window.DomovoiPluginManifest = " + json.dumps(_RADIO) + ";",
        "script": ("h.render(); return { tiles: h.findAll((e) => e.props.className === 'home-tile')"
                   ".map((e) => e.props.href) };"),
    },
}
# The crumb label for every route the task added to the map, one Topbar each.
for _r in ("home", "chat", "videos"):
    SCENARIOS[f"crumb_{_r}"] = {
        "files": [COMPONENTS], "component": "Topbar",
        "props": {"route": _r, "theme": "light"}, "fnProps": ["setRoute", "setTheme"],
        "setup": _SETUP,
        "script": "h.render(); return h.findAll({ type: 'strong' }).map((e) => e.text);",
    }


@pytest.fixture(scope="module")
def driven() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
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


def _phone_block(css: str) -> str:
    """The body of the ``@media (max-width: 760px)`` block that lays the
    shell out for a phone (the first one in styles.css)."""
    start = css.index("@media (max-width: 760px) {")
    depth = 0
    for i in range(start, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start:i + 1]
    raise AssertionError("unterminated 760px block")


# ─── the route is reserved everywhere ────────────────────────────────────


def test_home_is_a_reserved_core_route() -> None:
    assert "home" in CORE_WEB_ROUTES
    assert "home" in plugin_host.CORE_ROUTES
    block = re.search(r"window\.DomovoiCore\.pages = \{(.*?)\};", _src("index.html"), re.S).group(1)
    assert re.search(r"^\s*home: \(\) => window\.HomePage,", block, re.M)


def test_home_sorts_before_every_other_core_page() -> None:
    assert plugin_host.CORE_NAV["home"] < plugin_host.CORE_NAV["chat"] == 8
    assert plugin_host.CORE_NAV["home"] == min(plugin_host.CORE_NAV.values())


def test_a_plugin_page_on_home_is_refused() -> None:
    """Contract check 7 refuses it at install / enable / boot, naming #home."""
    manifest = parse_manifest(textwrap.dedent(
        """
        [plugin]
        slug = "sneaky"
        name = "sneaky"
        version = "1.0.0"
        publisher = "tests"
        license = "MIT"
        description = "claims the home route"
        domovoi_api = ">=1.0,<2.0"

        [entry_points]
        core = "domovoi_plugin_sneaky.core"

        [web]
        scripts = ["web/static/sneaky.jsx"]

        [[web.pages]]
        route = "home"
        page = "SneakyHome"
        nav_label = "Home"
        """
    ))
    report = ContractReport()
    check_web_routes("sneaky", manifest, [], report)
    assert len(report.errors) == 1 and "#home" in report.errors[0]
    # The web host drops such a page from the frontend manifest too.
    assert "#home" in plugin_host.web_route_collision_message("home", "SneakyHome")


# ─── Home is where the dashboard lands ───────────────────────────────────


def test_the_default_and_fallback_routes_are_home() -> None:
    html = _src("index.html")
    assert "'#music'" not in html, "a #music default literal survived"
    assert html.count("(window.location.hash || '#home').slice(1)") == 2
    assert "Page = window.DomovoiCore.pages.home() || window.DomovoiCore.pages.music();" in html
    # ...and the crumb and the nav name the page that is showing, not the
    # unknown route (which left "domovoi / " with an empty label).
    assert "shownRoute = window.DomovoiCore.pages.home() ? 'home' : 'music';" in html
    assert "<Topbar route={shownRoute}" in html and "<Sidebar route={shownRoute}" in html
    # Deep links still resolve core-first: the route map is consulted
    # before any fallback, so #music opens Music.
    assert "const coreGetter = window.DomovoiCore.pages[route];" in html


def test_home_script_loads_after_the_pages_it_borrows_from() -> None:
    html = _src("index.html")
    scripts = re.findall(r'<script type="text/babel" data-presets="react" src="([^"]+)"></script>', html)
    assert scripts[-1] == "home.jsx", scripts
    for earlier in ("components.jsx", "satellites.jsx", "calendar.jsx", "manual.jsx"):
        assert scripts.index(earlier) < scripts.index("home.jsx")
    # ...and before the inline shell that renders it.
    assert html.index('src="home.jsx"') < html.index("window.DomovoiCore.pages = {")


def test_the_page_gets_the_one_sidebar_counts_result() -> None:
    html = _src("index.html")
    assert html.count("useSidebarCounts()") == 1
    assert html.count("usePluginBadges(") == 1
    assert "<Page counts={counts} badges={badges}/>" in html
    assert "manifest={manifest} badges={badges}/>" in html


def test_the_installed_app_opens_on_home_and_is_called_domovoi() -> None:
    manifest = json.loads(_src("manifest.webmanifest"))
    assert manifest["start_url"] == "/#home"
    assert manifest["name"] == "Domovoi"
    assert "music player" not in manifest["description"].lower()


def test_the_offline_shell_carries_home() -> None:
    assets = re.search(r"const SHELL_ASSETS = \[(.*?)\];", _src("sw.js"), re.S).group(1)
    assert "'/home.jsx'" in assets


# ─── the way home ────────────────────────────────────────────────────────


def test_the_brand_row_is_a_link_home_without_a_fake_version(driven) -> None:
    brand = driven["sidebar_on_music"]["brand"]
    assert brand["type"] == "a"
    assert brand["props"]["href"] == "#home"
    assert "/ 1.0" not in driven["sidebar_on_music"]["text"]
    assert 'className="ver"' not in _src("components.jsx")
    assert ".brand-row .ver" not in _src("styles.css")


def test_the_topbar_crumb_is_the_same_link(driven) -> None:
    (link,) = driven["topbar_chat"]["links"]
    assert link["text"] == "domovoi"
    assert link["props"]["href"] == "#home"


@pytest.mark.parametrize(("route", "label"), [("home", "Home"), ("chat", "Chat"), ("videos", "Videos")])
def test_the_crumb_names_home_chat_and_videos(driven, route, label) -> None:
    """chat and videos used to render an empty <strong>."""
    assert driven[f"crumb_{route}"] == [label]


def test_brand_row_and_crumb_style_as_links_not_browser_blue() -> None:
    css = _src("styles.css")
    brand = re.search(r"\.brand-row \{(.*?)\}", css, re.S).group(1)
    assert "text-decoration: none" in brand
    crumb = re.search(r"\.crumbs \.crumb-home \{(.*?)\}", css, re.S).group(1)
    assert "color: inherit" in crumb and "text-decoration: none" in crumb
    # The focus ring is the global one — it covers every <a>.
    assert re.search(r":where\([^)]*\ba\b[^)]*\):focus-visible", _src("colors_and_type.css"))


# ─── the phone strip ─────────────────────────────────────────────────────


def _classes(driven, scenario) -> dict[str, list[str]]:
    return {r["route"]: r["cls"] for r in driven[scenario]["rows"]}


def test_exactly_the_five_primary_tabs_are_marked_primary(driven) -> None:
    rows = _classes(driven, "sidebar_on_music")
    primary = {route for route, cls in rows.items() if "primary" in cls}
    assert primary == PRIMARY
    # Plugin pages are never primary: they live on the everything grid.
    assert "radio" in rows and "primary" not in rows["radio"]


def test_the_desktop_sidebar_keeps_every_page_but_hides_the_home_row(driven) -> None:
    rows = _classes(driven, "sidebar_on_music")
    assert set(rows) >= PRIMARY | {"podcasts", "audiobooks", "videos", "news", "people",
                                   "files", "plugins", "radio"}
    assert [r for r, cls in rows.items() if "brand-link" in cls] == ["home"]
    css = _src("styles.css")
    phone = _phone_block(css)
    desktop = css.replace(phone, "")
    assert re.search(r"\.sidebar \.nav-item\.brand-link \{ display: none; \}", desktop)


def test_the_phone_strip_shows_only_primary_tabs() -> None:
    phone = _phone_block(_src("styles.css"))
    assert ".sidebar .nav-item:not(.primary) { display: none; }" in phone
    # The tabs share the strip's full width: without the wrapper stretching,
    # each tab sized to its content (33px in headless Chrome at 375px) and
    # "Satellites" spilled into its neighbours.
    assert ".sidebar .nav-wrap { flex: 1; display: flex; min-width: 0; }" in phone
    assert 'className="nav-wrap"' in _src("components.jsx")
    # The home row the desktop hides comes back in the strip, AFTER the
    # desktop rule so it wins at equal specificity.
    assert ".sidebar .nav-item.brand-link { display: flex; }" in phone


def test_the_home_tab_lights_for_pages_on_the_everything_grid(driven) -> None:
    assert "more-active" in _classes(driven, "sidebar_on_podcasts")["home"]
    assert "more-active" not in _classes(driven, "sidebar_on_music")["home"]
    home = _classes(driven, "sidebar_on_home")["home"]
    assert "active" in home and "more-active" not in home
    assert ".sidebar .nav-item.more-active" in _phone_block(_src("styles.css"))


def test_the_mini_player_docks_above_the_strip_on_a_phone() -> None:
    css = _src("styles.css")
    phone = _phone_block(css)
    assert "--phone-nav-h: 56px" in css
    assert "--dock-bottom: var(--phone-nav-h)" in phone
    assert "grid-template-rows: var(--topbar-h) 1fr var(--phone-nav-h)" in phone
    # The bar is styled in styles.css ("Docked player"), where its phone
    # layout lives too (test_web_player_phone.py drives that).
    bar = re.search(r"^\.mini-player \{(.*?)\}", css, re.S | re.M).group(1)
    assert "position: fixed; left: 0; right: 0; bottom: var(--dock-bottom, 0px); z-index: 45;" in bar
    player = _src("player.jsx")
    assert '<div className="mini-player">' in player
    # The queue panel and cast menu float above the player, so they move with it.
    assert player.count("bottom: 'calc(var(--dock-bottom, 0px) + 76px)'") == 2
    assert "bottom: 76," not in player


def test_main_keeps_room_for_the_player_on_a_phone() -> None:
    """The phone block's ``padding: 16px`` used to reset the 88px the
    desktop reserves below the last row; with the player docked on top of
    the strip it would cover the end of every page."""
    phone = _phone_block(_src("styles.css"))
    main = re.search(r"\.main \{([^}]*)\}", phone).group(1)
    assert "padding-bottom: 88px" in main
    assert main.index("padding: 16px") < main.index("padding-bottom: 88px")


# ─── the everything grid ─────────────────────────────────────────────────


def test_the_everything_grid_lists_every_page_off_the_strip(driven) -> None:
    tiles = driven["home_everything"]["tiles"]
    assert "#radio" in tiles and "#settings" in tiles and "#manual" in tiles
    for r in ("podcasts", "audiobooks", "videos", "news", "people", "files", "plugins"):
        assert f"#{r}" in tiles
    for r in PRIMARY:
        assert f"#{r}" not in tiles, f"#{r} is a strip tab, not an everything tile"


def test_home_top_level_names_are_prefixed() -> None:
    """Every page script shares one Babel scope (the duplicate-global trap)."""
    names = re.findall(r"^(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)", _src("home.jsx"), re.M)
    assert names and all(n.startswith(("Home", "HOME_")) for n in names), names


# ─── shared screens and the launchers ────────────────────────────────────

PERSONAL = {"people", "chat", "files", "news"}


def test_a_shared_screen_drops_personal_pages_from_the_sidebar_and_the_strip(driven) -> None:
    """The sidebar and the phone strip are one element, so one render covers
    both widths: no personal row exists for either to show."""
    rows = _classes(driven, "sidebar_shared")
    assert not set(rows) & PERSONAL, set(rows) & PERSONAL
    assert {r for r, cls in rows.items() if "primary" in cls} == PRIMARY - {"chat"}
    assert {"podcasts", "videos", "plugins", "radio"} <= set(rows)
    assert not set(_classes(driven, "sidebar_shared_not_known_yet")) & PERSONAL
    assert PERSONAL <= set(_classes(driven, "sidebar_unpaired_not_known"))
    assert PERSONAL <= set(_classes(driven, "sidebar_on_music"))


def test_a_shared_screen_drops_the_same_pages_from_the_everything_grid(driven) -> None:
    tiles = set(driven["home_everything_shared"]["tiles"])
    assert not {f"#{r}" for r in PERSONAL} & tiles
    assert {"#podcasts", "#radio", "#settings", "#manual"} <= tiles


def test_one_list_decides_what_a_shared_screen_hides() -> None:
    comps = _src("components.jsx")
    assert "const SHARED_SCREEN_HIDDEN = new Set(['people', 'chat', 'files', 'news']);" in comps
    assert "const items = navItemsFor(manifest, { shared });" in comps
    home = _src("home.jsx")
    assert "navItemsFor(manifest, { shared })" in home
    assert "HOME_SHARED_HIDDEN" not in home and "'documents'" not in home


def test_the_strip_reads_home_music_satellites_calendar_chat(driven) -> None:
    strip = {r["route"]: r["strip"] for r in driven["sidebar_on_music"]["rows"] if r["strip"] is not None}
    assert sorted(strip, key=strip.get) == ["home", "music", "satellites", "calendar", "chat"]
    assert ".sidebar .nav-item.primary { order: var(--strip-order, 9); }" in _phone_block(_src("styles.css"))
    readme = (REPO_ROOT / "docs" / "design" / "README.md").read_text(encoding="utf-8")
    assert "(home · music · satellites · calendar · chat)" in readme


def test_a_sidebar_handed_the_badges_polls_nothing_itself(driven) -> None:
    assert driven["sidebar_polls_alone"]["gets"] == ["/api/plugins/radio/badge"]
    given = driven["sidebar_given_badges"]
    assert given["gets"] == []
    assert "4" in given["text"]


def test_toasts_clear_the_phone_strip_and_the_docked_player() -> None:
    """Fixed at bottom: 24px they covered the tab strip for up to 12 s;
    lifted by the strip alone they still covered the docked player's play
    button (measured at 375px: three "queued" toasts over the bar)."""
    lifted = "bottom: 'calc(var(--dock-bottom, 0px) + var(--player-h, 0px) + 24px)'"
    comps = _src("components.jsx")
    toast = comps[comps.index("const useToast"):comps.index("/* ---- Tabs")]
    assert lifted in toast and "bottom: 24," not in toast
    html = _src("index.html")
    assert lifted in html                                            # "plugins changed"
    assert "bottom:calc(var(--dock-bottom, 0px) + var(--player-h, 0px) + 24px)" in html  # "Domovoi has been updated"
    # --player-h is the bar's own height, and only while the bar shows.
    css = _src("styles.css")
    assert re.search(r"^html\.mp-docked \{ --player-h: 64px; \}", css, re.M)
    bar = re.search(r"^\.mini-player \{(.*?)\}", css, re.S | re.M).group(1)
    assert "height: 64px;" in bar


def test_a_page_header_wraps_on_a_phone() -> None:
    """Music's upload, rescan and enrich ran 9-83px off a 375px screen,
    with the page title squeezed into a column beside them."""
    phone = _phone_block(_src("styles.css"))
    head = re.search(r"\.page-header \{([^}]*)\}", phone).group(1)
    assert "flex-wrap: wrap" in head and "align-items: flex-start" in head
    assert "flex-wrap: wrap" in re.search(r"\.page-header \.actions \{([^}]*)\}", phone).group(1)


def test_a_new_page_starts_at_its_top() -> None:
    """.main is the one scroller every page shares: a tab tapped from a
    page scrolled 600px down opened the next page 865px down."""
    html = _src("index.html")
    assert '<main className="main" ref={mainRef}>' in html
    effect = re.search(r"React\.useLayoutEffect\(\(\) => \{(.*?)\}, \[(\w+)\]\);", html, re.S)
    assert effect and effect.group(2) == "route"
    assert "mainRef.current.scrollTop = 0" in effect.group(1)


def test_the_service_worker_is_the_dashboards_not_the_music_players() -> None:
    assert _src("sw.js").startswith("/* Domovoi — the dashboard's service worker.")
    assert "Domovoi Music" not in _src("sw.js")
