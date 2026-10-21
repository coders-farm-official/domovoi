"""Every tab has an address (owner request 2026-10-12, after the first
Windows install).

Kamron: the installer's last page should link straight to Settings →
Configuration, where the admin account is made, but "if we link to settings,
it defaults to Greetings. Each tab should be able to be linked to directly
and when clicking around our URL should change."

* A tab's address is ``#<page>/<tab>``: ``#settings/advanced``,
  ``#settings/wake-words``, ``#music/queue``, ``#calendar/week``. Clicking a
  tab writes it as a new history entry (so Back goes to the tab before),
  opening an address selects its tab, a tab the page doesn't have is the
  page's default tab (and the address is corrected to ``#<page>``), and a
  tab named by its code id or an old name (``#settings/configuration``,
  since the regroup of 2026-10-21) is rewritten to its slug in place,
  markers kept. ``#settings`` alone is the list of groups (a desktop shows
  About & updates), and the old ``#satellites`` is ``#settings/rooms``.
* The shell picks the page by the first segment, so a deeper address never
  falls through to Home.
* ``#settings/configuration/setup`` is what the Windows installers already
  out open (``#settings/advanced/setup`` since 2026-10-21): on an unclaimed
  box Advanced opens the first-run admin setup by itself (as it does at
  ``#settings/advanced``); on a box claimed before the visit it says so in
  one line and nothing opens.
* Where the setup code is, in the words of the kind of server
  (``/api/capabilities`` ``host_kind``): the Windows installer's last page
  on a Windows (WSL) install, ``~/.domovoi/setup-code.txt`` elsewhere, both
  while unknown. The server's own refusal of a wrong code says the same.

Driven through domovoi/tests/jsx_interact_harness.js (the dashboard's own
Babel, a small stateful React). No DB, never ``requires_db``; needs
``node`` and fails without it.
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
COMPONENTS = "web/static/components.jsx"
SETTINGS = "web/static/settings.jsx"
MUSIC = "web/static/music.jsx"
CONTAINERS = "web/static/containers.jsx"
HEALTH = "web/static/health.jsx"
# Settings with Health registered, as index.html loads them.
SETTINGS_FILES = [COMPONENTS, SETTINGS, CONTAINERS, HEALTH]
CALENDAR = "web/static/calendar.jsx"

# The address bar, Back, and the window's events, recorded. replaceState
# moves the address in place (no hashchange), as a browser does; a script
# "navigates" with w.go(hash), which is what a click on a link does: the
# address changes and hashchange fires.
_SANDBOX = r"""
window.location.hash = __HASH;
window.__replaced = [];
window.history = { replaceState: (s, t, url) => { window.__replaced.push(url); window.location.hash = url; } };
window.__l = [];
window.addEventListener = (t, fn) => { window.__l.push({ t, fn }); };
window.removeEventListener = (t, fn) => { window.__l = window.__l.filter((l) => !(l.t === t && l.fn === fn)); };
window.dispatchEvent = (e) => { window.__l.filter((l) => l.t === e.type).forEach((l) => l.fn(e)); return true; };
window.go = (hash) => { window.location.hash = hash; window.dispatchEvent({ type: 'hashchange' }); };
CustomEvent = function (type, init) { this.type = type; this.detail = init && init.detail; };
window.__opened = 0;
globalThis.Auth = {
  token: null,
  status: null,
  modalOpen: false,
  subscribe: () => () => {},
  refreshStatus: () => { Auth.status = __STATUS; return Promise.resolve(Auth.status); },
  isLoggedIn: () => __LIVE,
  isPaired: () => true,
  headers: () => ({}),
  openModal: () => { window.__opened += 1; },
  logout: () => Promise.resolve(true),
};
window.__deep = (n) => {
  if (n == null || typeof n === 'boolean') return '';
  if (typeof n === 'string' || typeof n === 'number') return String(n);
  if (Array.isArray(n)) return n.map(window.__deep).join('');
  return n.props ? window.__deep(n.props.children) : '';
};
window.__cls = (e) => String((e && e.props && e.props.className) || '').split(' ');
window.__settle = async (h, n = 4) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
"""


def _setup(hash: str = "", *, status: dict | None = None, live: bool = False) -> str:
    st = status if status is not None else {"setup_complete": True, "authenticated": False}
    return (f"const __HASH = {json.dumps(hash)}; const __STATUS = {json.dumps(st)};"
            f" const __LIVE = {json.dumps(live)};\n" + _SANDBOX)


UNCLAIMED = {"setup_complete": False, "authenticated": False}
CLAIMED = {"setup_complete": True, "authenticated": False}

# The routes a page's tabs read, without rendering anything.
_PURE = r"""
  const w = h.global('window');
  const L = w.settingsLayout();
  const T = [...L.sections, ...L.groups.map((g) => ({ id: g.id }))];
  const read = (r) => w.readRouteTab(r, 'settings', T, 'index');
  const cases = ['#settings', 'settings', '#settings/', '#settings/configuration', 'settings/config',
                 '#Settings/Configuration/', '#settings/configuration/setup', '#settings/configuration/setup/',
                 '#settings/advanced', '#settings/advanced/setup',
                 '#settings/wake-words', '#settings/wakewords', '#settings/phone-bar', '#settings/satellites',
                 '#settings/satellites/media', '#settings/devices', '#settings/recordings', '#settings/voice',
                 '#settings/system', '#settings/health', '#settings/nonsense',
                 '#settings/nonsense/setup', '#satellites', '#satellites/x', '#containers', '#music/queue', '', '#'];
  const out = {};
  for (const c of cases) out[c] = read(c);
  return {
    out,
    groups: L.groups.map((g) => [g.label, g.sections.map((x) => x.label)]),
    parts: ['#a/b//c/', '  #Music/Queue ', '', null].map(w.routeParts),
    pages: ['#settings/advanced', 'music', '#', ''].map(w.routePage),
    resolve: ['satellites', '#satellites/drawer', 'settings/satellites', 'home', '#', 'containers',
              '#containers/x'].map(w.resolveRoute),
    slugs: L.sections.map((t) => [t.id, w.tabSlug(T, t.id)]),
    roundTrip: L.sections.map((t) => read('#settings/' + w.tabSlug(T, t.id)).tab),
    byAlias: L.sections.flatMap((t) => (t.aliases || []).map((a) => [a, read('#settings/' + a).tab])),
    setup: w.SETUP_ROUTE,
    acoustid: w.ACOUSTID_KEY_ROUTE,
    href: w.settingsTabHref('advanced'),
  };
"""

# The nav's links: which one is on, and the level the page says it shows.
_NAV = r"""
  const w = h.global('window');
  const on = () => h.findAll((e) => e.type === 'a' && w.__cls(e).includes('settings-nav-item')
    && w.__cls(e).includes('on')).map((e) => w.__deep(e));
  const level = () => (h.find((e) => w.__cls(e).includes('settings-page')) || { props: {} }).props['data-level'];
  const title = () => w.__deep(h.find((e) => w.__cls(e).includes('settings-section-title')));
  const go = async (label) => {
    await h.click((e) => e.type === 'a' && w.__cls(e).includes('settings-nav-item') && w.__deep(e) === label);
    await w.__settle(h);
  };
"""

SCENARIOS: dict[str, dict] = {
    "pure": {
        "files": SETTINGS_FILES, "component": "SettingsPage",
        "setup": _setup(), "script": _PURE,
    },
    "settings_clicks": {
        "files": SETTINGS_FILES, "component": "SettingsPage",
        "setup": _setup("#settings/configuration"),
        "api": {"GET /api/greetings": [], "GET /api/config/editable": {"fields": []}},
        "script": _NAV + r"""
          h.render(); await w.__settle(h);
          const landed = { on: on(), title: title(), level: level(), hash: w.location.hash };
          await go('Voices');
          const voices = { on: on(), hash: w.location.hash };
          await go('Wake words');
          const wake = { on: on(), hash: w.location.hash };
          // Back: the browser puts the previous address back and says so.
          w.go('#settings/voices'); await w.__settle(h);
          const back = on();
          // A link to another section while Settings is on screen.
          w.go('#settings/internet'); await w.__settle(h);
          const link = on();
          // A group's address: a phone's list of its sections; its first section.
          w.go('#settings/voice'); await w.__settle(h);
          const group = { on: on(), level: level(), title: title() };
          // #settings alone: the list of groups (a desktop: About & updates).
          w.go('#settings'); await w.__settle(h);
          const bare = { on: on(), level: level(), title: title(), hash: w.location.hash };
          // An address for another page leaves the section alone (Settings is on its way out).
          w.go('#settings/greetings'); await w.__settle(h);
          w.go('#music/queue'); await w.__settle(h);
          const leaving = on();
          return { landed, voices, wake, back, link, group, bare, leaving, replaced: w.__replaced };
        """,
    },
    "settings_unknown_tab": {
        "files": SETTINGS_FILES, "component": "SettingsPage",
        "setup": _setup("#settings/nonsense"), "api": {},
        "script": _NAV + r"""
          h.render(); await w.__settle(h);
          return { level: level(), hash: w.location.hash, replaced: w.__replaced };
        """,
    },
    "settings_id_for_slug": {
        "files": SETTINGS_FILES, "component": "SettingsPage",
        "setup": _setup("#settings/config/setup"),
        "api": {"GET /api/config/editable": {"fields": []}},
        "script": r"""
          const w = h.global('window');
          h.render(); await w.__settle(h);
          return { hash: w.location.hash, replaced: w.__replaced };
        """,
    },
    "settings_groups_and_names": {
        "files": SETTINGS_FILES, "component": "SettingsPage",
        "setup": _setup("#settings/greetings"), "api": {"GET /api/greetings": []},
        "script": _NAV + r"""
          h.render(); await w.__settle(h);
          const heads = h.findAll((e) => w.__cls(e).includes('settings-nav-group-label')).map((e) => w.__deep(e));
          const items = h.findAll((e) => e.type === 'a' && w.__cls(e).includes('settings-nav-item'))
            .map((e) => [w.__deep(e), e.props.href]);
          return { heads, items, title: title() };
        """,
    },
    "music_tabs": {
        "files": [COMPONENTS, MUSIC], "component": "MusicPage",
        "setup": _setup("#music/queue") + "globalThis.useGuestViewer = () => false;",
        "api": {"GET /api/music/now-playing": [], "GET /api/playlists": [],
                "GET /api/music/library/stats": None, "GET /api/acquisitions?limit=100": None},
        "script": r"""
          const w = h.global('window');
          const on = (label) => {
            const b = h.findAll({ type: 'button' }).find((e) => w.__deep(e).startsWith(label));
            return b ? String((b.props.style || {}).borderBottom || '').includes('var(--brand)') : null;
          };
          h.render(); await w.__settle(h);
          const landed = { queue: on('Room queue'), library: on('Library') };
          await h.click((e) => e.type === 'button' && w.__deep(e).startsWith('Stats')); await w.__settle(h);
          const stats = { on: on('Stats'), hash: w.location.hash };
          w.go('#music'); await w.__settle(h);
          return { landed, stats, bare: on('Library') };
        """,
    },
    "calendar_views": {
        "files": [COMPONENTS, CALENDAR], "component": "CalendarPage",
        "setup": _setup("#calendar/week"),
        "api": {"GET /api/calendar/events": []},
        "script": r"""
          const w = h.global('window');
          const sel = () => h.findAll((e) => e.type === 'button' && e.props.role === 'tab')
            .filter((e) => e.props['aria-selected']).map((e) => w.__deep(e));
          h.render(); await w.__settle(h);
          const landed = sel();
          await h.click((e) => e.type === 'button' && e.props.role === 'tab' && w.__deep(e) === 'day');
          await w.__settle(h);
          return { landed, day: sel(), hash: w.location.hash };
        """,
    },
    # ── the first-run claim ──
    "claim_route_unclaimed": {
        "files": SETTINGS_FILES, "component": "SettingsPage",
        "setup": _setup("#settings/configuration/setup", status=UNCLAIMED),
        "api": {"GET /api/config/editable": {"fields": []}, "GET /api/capabilities": {"host_kind": "wsl"}},
        "script": r"""
          const w = h.global('window');
          h.render(); await w.__settle(h);
          return { opened: w.__opened, text: h.text().join(' '),
                   note: !!h.find((e) => w.__cls(e).includes('admin-already-set-up')) };
        """,
    },
    "claim_plain_config_unclaimed": {
        "files": SETTINGS_FILES, "component": "SettingsPage",
        "setup": _setup("#settings/configuration", status=UNCLAIMED),
        "api": {"GET /api/config/editable": {"fields": []}},
        "script": r"""
          const w = h.global('window');
          h.render(); await w.__settle(h);
          return { opened: w.__opened };
        """,
    },
    "claim_other_tab_unclaimed": {
        "files": SETTINGS_FILES, "component": "SettingsPage",
        "setup": _setup("#settings/greetings", status=UNCLAIMED),
        "api": {"GET /api/greetings": []},
        "script": r"""
          const w = h.global('window');
          h.render(); await w.__settle(h);
          return { opened: w.__opened };
        """,
    },
    "claim_route_claimed": {
        "files": SETTINGS_FILES, "component": "SettingsPage",
        "setup": _setup("#settings/configuration/setup", status=CLAIMED),
        "api": {"GET /api/config/editable": {"fields": []}},
        "script": r"""
          const w = h.global('window');
          h.render(); await w.__settle(h);
          const note = h.find((e) => w.__cls(e).includes('admin-already-set-up'));
          return { opened: w.__opened, note: note ? w.__deep(note) : null, hash: w.location.hash };
        """,
    },
    "claim_route_claimed_live": {
        "files": SETTINGS_FILES, "component": "SettingsPage",
        "setup": _setup("#settings/configuration/setup", status={"setup_complete": True, "authenticated": True},
                        live=True),
        "api": {"GET /api/config/editable": {"fields": []}},
        "script": r"""
          const w = h.global('window');
          h.render(); await w.__settle(h);
          const note = h.find((e) => w.__cls(e).includes('admin-already-set-up'));
          return { opened: w.__opened, note: note ? w.__deep(note) : null };
        """,
    },
    "claimed_plain_config_has_no_note": {
        "files": SETTINGS_FILES, "component": "SettingsPage",
        "setup": _setup("#settings/configuration", status=CLAIMED),
        "api": {"GET /api/config/editable": {"fields": []}},
        "script": r"""
          const w = h.global('window');
          h.render(); await w.__settle(h);
          return { opened: w.__opened, note: !!h.find((e) => w.__cls(e).includes('admin-already-set-up')) };
        """,
    },
    "claimed_here_drops_the_marker": {
        "files": SETTINGS_FILES, "component": "SettingsPage",
        "setup": _setup("#settings/configuration/setup", status=UNCLAIMED),
        "api": {"GET /api/config/editable": {"fields": []}, "GET /api/capabilities": {"host_kind": "wsl"}},
        "script": r"""
          const w = h.global('window');
          h.render(); await w.__settle(h);
          const before = w.location.hash;
          // The claim succeeded (auth.js signedIn): the status says so.
          h.global('Auth').status = { setup_complete: true, authenticated: true };
          h.rerender(); await w.__settle(h);
          return { before, after: w.location.hash, opened: w.__opened,
                   note: !!h.find((e) => w.__cls(e).includes('admin-already-set-up')) };
        """,
    },
}

# Where the setup code is: the dialog and the Admin card, per host kind.
for kind, caps in (("wsl", {"host_kind": "wsl"}), ("linux", {"host_kind": "linux"}),
                   ("unknown", None), ("old_server", {"plugins": []})):
    SCENARIOS[f"where_{kind}"] = {
        "files": [COMPONENTS, SETTINGS], "component": "LoginModal", "fnProps": ["onClose"],
        "setup": _setup(status=UNCLAIMED) + "Auth.status = __STATUS;",
        "api": {"GET /api/capabilities": caps} if caps is not None else {},
        "refs": True,
        "script": r"""
          const w = h.global('window');
          h.render(); await w.__settle(h);
          const hint = h.find((e) => w.__cls(e).includes('setup-code-where'));
          const labels = h.findAll({ type: 'label' }).map((e) => w.__deep(e));
          const buttons = h.findAll({ type: 'button' }).map((e) => w.__deep(e)).filter(Boolean);
          const title = w.__deep(h.find((e) => w.__cls(e).includes('ttl')));
          const code = h.find((e) => e.type === 'input' && e.props.placeholder === 'eight-words-separated-by-dashes');
          return { hint: hint ? h.tree().filter((e) => e === hint || h.ancestors(e).includes(hint)).map((e) => e.text).join(' ') : null, labels, buttons, title,
                   codeFocused: h.focused().map((f) => f.props.placeholder || f.props.type || f.type) };
        """,
    }
    SCENARIOS[f"card_{kind}"] = {
        "files": [COMPONENTS, SETTINGS], "component": "AdminSection",
        "setup": _setup(status=UNCLAIMED) + "Auth.status = __STATUS;",
        "api": {"GET /api/capabilities": caps} if caps is not None else {},
        "script": r"""
          const w = h.global('window');
          h.render(); await w.__settle(h);
          const hint = h.find((e) => w.__cls(e).includes('setup-code-where'));
          const buttons = h.findAll({ type: 'button' }).map((e) => w.__deep(e)).filter(Boolean);
          return { hint: hint ? h.tree().filter((e) => e === hint || h.ancestors(e).includes(hint)).map((e) => e.text).join(' ') : null, buttons };
        """,
    }


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("settings-routes") / "scenarios.json"
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


# ─── the addresses ───────────────────────────────────────────────────────


def test_settings_is_five_groups_in_plain_words(driven) -> None:
    """Owner design 2026-10-21. Printers & scanners, Search and Chat are
    slots: they show once their panel is registered, never as a promise."""
    assert driven["pure"]["groups"] == [
        ["Rooms & devices", ["Rooms", "Phones & browsers", "App layout"]],
        ["Voice", ["Wake words", "Voices", "Greetings"]],
        ["Chat & AI", ["Models"]],
        ["Privacy", ["Internet", "Voice history"]],
        ["System", ["About & updates", "Health", "Advanced"]],
    ]
    o = driven["settings_groups_and_names"]
    assert o["heads"] == ["Rooms & devices", "Voice", "Chat & AI", "Privacy", "System"]
    assert [label for label, _ in o["items"]] == [
        "Rooms", "Phones & browsers", "App layout", "Wake words", "Voices", "Greetings", "Models",
        "Internet", "Voice history", "About & updates", "Health", "Advanced"]
    assert dict(o["items"])["Voice history"] == "#settings/voice-history"
    assert o["title"] == "Greetings"


def test_every_settings_section_has_an_address_and_its_old_names_open_it(driven) -> None:
    o = driven["pure"]
    slugs = dict(o["slugs"])
    assert slugs == {s: s for s in (
        "rooms", "phones-and-browsers", "app-layout", "wake-words", "voices", "greetings", "models",
        "internet", "voice-history", "about", "health", "advanced")}
    ids = [i for i, _ in o["slugs"]]
    assert o["roundTrip"] == ids
    assert dict(o["byAlias"]) == {
        "satellites": "rooms", "devices": "phones-and-browsers", "phone-bar": "app-layout",
        "phonebar": "app-layout", "wakewords": "wake-words", "recordings": "voice-history",
        "configuration": "advanced", "config": "advanced"}
    assert o["setup"] == "settings/advanced/setup"
    assert o["acoustid"] == "settings/advanced/acoustid-key"
    assert o["href"] == "#settings/advanced"


def test_how_an_address_reads(driven) -> None:
    out = driven["pure"]["out"]

    def tab(route):
        r = out[route]
        return r["tab"], r["rest"], r["fix"]

    for bare in ("#settings", "settings", "#settings/", "", "#"):
        assert tab(bare) == ("index", [], None), bare
    assert tab("#settings/advanced") == ("advanced", [], None)
    # An old name opens the section, and the address is rewritten, markers kept.
    assert tab("#settings/configuration") == ("advanced", [], "settings/advanced")
    assert tab("#Settings/Configuration/") == ("advanced", [], "settings/advanced")
    assert tab("settings/config") == ("advanced", [], "settings/advanced")
    assert tab("#settings/configuration/setup") == ("advanced", ["setup"], "settings/advanced/setup")
    assert tab("#settings/configuration/setup/") == ("advanced", ["setup"], "settings/advanced/setup")
    assert tab("#settings/advanced/setup") == ("advanced", ["setup"], None)
    assert tab("#settings/wakewords") == ("wake-words", [], "settings/wake-words")
    assert tab("#settings/wake-words") == ("wake-words", [], None)
    assert tab("#settings/phone-bar") == ("app-layout", [], "settings/app-layout")
    assert tab("#settings/satellites") == ("rooms", [], "settings/rooms")
    # The tray's "prepare a satellite card" link keeps its marker.
    assert tab("#settings/satellites/media") == ("rooms", ["media"], "settings/rooms/media")
    assert tab("#settings/devices") == ("phones-and-browsers", [], "settings/phones-and-browsers")
    assert tab("#settings/recordings") == ("voice-history", [], "settings/voice-history")
    # A group has an address too.
    assert tab("#settings/voice") == ("voice", [], None)
    assert tab("#settings/system") == ("system", [], None)
    assert tab("#settings/health") == ("health", [], None)
    # A section Settings doesn't have: the list of groups, and the address says #settings.
    assert tab("#settings/nonsense") == ("index", [], "settings")
    assert tab("#settings/nonsense/setup") == ("index", [], "settings")
    # The old routes of pages that are part of Settings now.
    assert tab("#satellites") == ("rooms", [], None)
    assert tab("#satellites/x") == ("rooms", ["x"], None)
    assert tab("#containers") == ("health", ["containers"], None)
    # Another page's address says nothing about Settings' sections.
    assert tab("#music/queue") == ("index", [], None)


def test_the_route_helpers(driven) -> None:
    o = driven["pure"]
    assert o["parts"] == [["a", "b", "c"], ["music", "queue"], [], []]
    assert o["pages"] == ["settings", "music", "", ""]
    assert o["resolve"] == ["settings/rooms", "settings/rooms/drawer", "settings/satellites", "home", "",
                            "settings/health/containers", "settings/health/containers/x"]


def test_clicking_sections_writes_the_address_and_back_and_links_move_it(driven) -> None:
    o = driven["settings_clicks"]
    # An old deep link opens its section (renamed in place), not the list.
    assert o["landed"] == {"on": ["Advanced"], "title": "Advanced", "level": "section",
                           "hash": "#settings/advanced"}
    # A click writes the section's address (location.hash = …: a history entry).
    assert o["voices"] == {"on": ["Voices"], "hash": "settings/voices"}
    assert o["wake"] == {"on": ["Wake words"], "hash": "settings/wake-words"}
    # Back puts the previous address back; the section follows.
    assert o["back"] == ["Voices"]
    assert o["link"] == ["Internet"]
    assert o["group"] == {"on": ["Wake words"], "level": "group", "title": "Wake words"}
    assert o["bare"] == {"on": ["About & updates"], "level": "index", "title": "About & updates",
                         "hash": "#settings"}
    assert o["leaving"] == ["Greetings"]
    # Only the old name was rewritten in place.
    assert o["replaced"] == ["#settings/advanced"]


def test_an_unknown_section_is_the_list_and_the_address_says_so(driven) -> None:
    o = driven["settings_unknown_tab"]
    assert o == {"level": "index", "hash": "#settings", "replaced": ["#settings"]}


def test_an_old_name_in_the_address_is_rewritten_keeping_the_marker(driven) -> None:
    o = driven["settings_id_for_slug"]
    assert o == {"hash": "#settings/advanced/setup", "replaced": ["#settings/advanced/setup"]}


def test_music_and_calendar_tabs_have_addresses_too(driven) -> None:
    m = driven["music_tabs"]
    assert m["landed"] == {"queue": True, "library": False}
    assert m["stats"] == {"on": True, "hash": "music/stats"}
    assert m["bare"] is True
    c = driven["calendar_views"]
    assert c["landed"] == ["week"]
    assert c["day"] == ["day"] and c["hash"] == "calendar/day"


def test_the_shell_picks_the_page_by_the_first_segment() -> None:
    html = _src("index.html")
    assert "const page = routePage(route);" in html
    assert "const coreGetter = window.DomovoiCore.pages[page];" in html
    assert "if (pg.route === page) {" in html
    assert "let shownRoute = page;" in html
    # A tab of the same page keeps the scroll; a new page starts at its top.
    effect = re.search(r"React\.useLayoutEffect\(\(\) => \{(.*?)\}, \[(\w+)\]\);", html, re.S)
    assert effect and effect.group(2) == "page"
    # The address is still resolved on load, on hashchange and on setRoute.
    assert "React.useState(() => settingsTabRoute(initialRoute))" in html
    assert "setRouteState(settingsTabRoute((window.location.hash || '#home').slice(1)))" in html


def test_links_into_settings_name_their_tab() -> None:
    health = _src("health.jsx")
    for key, href in (("stt", "#settings/models"), ("update", "#settings/about"),
                      ("restart", "#settings/about"), ("internet", "#settings/internet"),
                      ("disk", "#settings/models"), ("offline", "#settings/rooms")):
        assert re.search(rf"key: '{key}'[^}}]*fix: '{re.escape(href)}'", health), key
    home = _src("home.jsx")
    assert '<a href="#settings/app-layout" className="home-link home-bar-edit">' in home
    assert '<a href="#settings/models" className="chat-note-link">' in _src("chat.jsx")
    assert '<a href="#settings/internet" className="needs-internet-link">' in _src("components.jsx")
    assert '<a href="#settings/internet" className="home-link">' in _src("settings.jsx")
    # Models' "Manage" rows open the tab they name (it used to go to #settings).
    assert "onClick={() => { openSettingsTab(tab); }}>Manage</Button>" in _src("models.jsx")
    # Home's room queue button opens Music on the Room queue tab.
    assert "window.location.hash = 'music/queue';" in _src("data.js")
    # Nothing rides session storage any more.
    for name in ("components.jsx", "settings.jsx", "home.jsx"):
        assert "domovoi.settings.tab" not in _src(name), name


# ─── the first-run claim ─────────────────────────────────────────────────


def test_the_installers_address_opens_the_setup_on_an_unclaimed_box(driven) -> None:
    o = driven["claim_route_unclaimed"]
    assert o["opened"] == 1
    assert o["note"] is False
    assert "set up admin" in o["text"]                     # the card's button, if it is closed
    # Advanced itself does the same, once per arrival.
    assert driven["claim_plain_config_unclaimed"]["opened"] == 1
    # Other tabs don't.
    assert driven["claim_other_tab_unclaimed"]["opened"] == 0


def test_the_installers_address_is_harmless_on_a_claimed_box(driven) -> None:
    o = driven["claim_route_claimed"]
    assert o["opened"] == 0
    assert o["note"] == "This Domovoi’s admin account is already set up: sign in with its password."
    assert o["hash"] == "#settings/advanced/setup"
    live = driven["claim_route_claimed_live"]
    assert live["opened"] == 0
    assert live["note"] == "This Domovoi’s admin account is already set up, and you’re signed in."
    # The note is the marker's: plain Advanced on a claimed box has none.
    assert driven["claimed_plain_config_has_no_note"] == {"opened": 0, "note": False}


def test_a_claim_made_on_the_installers_address_drops_the_marker(driven) -> None:
    o = driven["claimed_here_drops_the_marker"]
    assert o["before"] == "#settings/advanced/setup"
    assert o["after"] == "#settings/advanced"              # a reload won't say "already"
    assert o["opened"] == 1 and o["note"] is False


# ─── where the setup code is ─────────────────────────────────────────────

WINDOWS = "last page of Domovoi’s Windows installer"
LINUX = "~/.domovoi/setup-code.txt"


def test_a_windows_install_is_told_about_the_installer_only(driven) -> None:
    for name in ("where_wsl", "card_wsl"):
        hint = driven[name]["hint"]
        assert WINDOWS in hint and "Run the installer again" in hint, name
        assert LINUX not in hint and "terminal" not in hint, name


def test_a_linux_server_is_told_about_the_file_only(driven) -> None:
    for name in ("where_linux", "card_linux"):
        hint = driven[name]["hint"]
        assert LINUX in hint and "installer" not in hint, name


@pytest.mark.parametrize("kind", ["unknown", "old_server"])
def test_an_unknown_server_is_told_both(driven, kind) -> None:
    for name in (f"where_{kind}", f"card_{kind}"):
        hint = driven[name]["hint"]
        assert "Windows" in hint and "installer" in hint and LINUX in hint, name


def test_the_labels_the_installer_names(driven) -> None:
    """The Windows installer's last page walks a new owner through these by
    name (windows/installer/DomovoiSetup.iss, FinishSteps): renaming one
    here means renaming it there."""
    o = driven["where_wsl"]
    assert o["title"] == "first-run admin setup"
    assert o["labels"] == ["setup code", "admin password (min 10 chars)", "confirm password"]
    assert "set password" in o["buttons"]
    assert o["codeFocused"] == ["eight-words-separated-by-dashes"]   # paste and go
    assert "set up admin" in driven["card_wsl"]["buttons"]
    assert "Settings → Advanced" not in o["hint"]          # it is on that page


def test_the_servers_refusal_of_a_wrong_code_says_where_the_right_one_is(monkeypatch) -> None:
    from web.backend.api import auth

    monkeypatch.setattr(auth, "host_kind", lambda: "wsl")
    windows = auth.setup_code_refusal()
    assert "last page of Domovoi's Windows installer" in windows
    assert "running the installer again" in windows and "~/.domovoi" not in windows
    monkeypatch.setattr(auth, "host_kind", lambda: "linux")
    linux = auth.setup_code_refusal()
    assert "~/.domovoi/setup-code.txt" in linux and "installer" not in linux
    assert windows.startswith("setup code required") and linux.startswith("setup code required")
