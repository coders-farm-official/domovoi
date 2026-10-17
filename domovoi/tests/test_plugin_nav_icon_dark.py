"""A plugin page's nav icon follows the dashboard's theme (2026-10-17).

The owner's dark-mode screenshots showed every plugin's sidebar icon
(Images, Stations, Games, Library, the media provider's page) black on the
dark sidebar: the dashboard drew the manifest's ``nav_icon`` as an <img>,
and an SVG inside an <img> resolves ``currentColor`` to black. Now:

- one icon (``nav_icon``): the dashboard paints its SHAPE in the theme's
  text colour (a CSS mask), unless the icon is coloured artwork;
- two icons (``nav_icon`` + the optional ``nav_icon_dark``): each is shown
  as drawn in its own theme; a dark one that doesn't load falls back.

Covered here: the manifest key (parsing, the shared path rules, the load
warnings), the web host's frontend manifest, the scaffold, and the
dashboard choosing the right variant (rendered with the vendored Babel in
domovoi/tests/jsx_render_harness.js; needs `node`).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from domovoi.plugins_runtime.cli import _cmd_new
from domovoi.plugins_runtime.contracts import ContractReport, check_web_routes
from domovoi.plugins_runtime.manifest import ManifestError, nav_icon_problem, parse_manifest
from web.backend import plugin_host

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_render_harness.js")
COMPONENTS = "web/static/components.jsx"


def _manifest(page_lines: str) -> str:
    return textwrap.dedent(
        """
        [plugin]
        slug = "iconz"
        name = "Iconz"
        version = "1.0.0"
        publisher = "tests"
        license = "MIT"
        description = "generated test plugin"
        domovoi_api = ">=1.0,<2.0"

        [entry_points]
        core = "domovoi_plugin_iconz.core"

        [web]
        scripts = ["web/static/iconz.jsx"]

        [[web.pages]]
        route = "iconz"
        page = "IconzPage"
        nav_label = "Iconz"
        """
    ) + textwrap.dedent(page_lines)


def _report(page_lines: str) -> ContractReport:
    report = ContractReport()
    check_web_routes("iconz", parse_manifest(_manifest(page_lines)), [], report)
    return report


# ─── the manifest key ─────────────────────────────────────────────────────


def test_nav_icon_dark_is_parsed_beside_nav_icon() -> None:
    m = parse_manifest(_manifest('nav_icon = "web/static/icon.svg"\n'
                                 'nav_icon_dark = "web/static/icon-dark.svg"\n'))
    (page,) = m.web_pages
    assert page.nav_icon == "web/static/icon.svg"
    assert page.nav_icon_dark == "web/static/icon-dark.svg"


def test_a_single_icon_manifest_is_unchanged() -> None:
    (page,) = parse_manifest(_manifest('nav_icon = "web/static/icon.svg"\n')).web_pages
    assert page.nav_icon == "web/static/icon.svg"
    assert page.nav_icon_dark is None
    assert _report('nav_icon = "web/static/icon.svg"\n').warnings == []


def test_nav_icon_dark_must_be_a_string() -> None:
    with pytest.raises(ManifestError, match="nav_icon_dark must be a string"):
        parse_manifest(_manifest('nav_icon = "web/static/icon.svg"\nnav_icon_dark = 3\n'))


@pytest.mark.parametrize("path", [
    "web/static/icon.svg", "static/icon.svg", "icon.svg", "web/static/icons/dark/nav.png",
])
def test_paths_inside_the_static_folder_pass(path: str) -> None:
    assert nav_icon_problem(path) is None


@pytest.mark.parametrize("path", [
    "../icon.svg", "web/static/../../core.py", "web\\static\\..\\secret.svg",
    "/etc/passwd", "C:/Users/alex/icon.svg", "https://example.com/icon.svg", "", "icon\n.svg",
])
def test_paths_that_leave_the_static_folder_are_refused(path: str) -> None:
    assert nav_icon_problem(path)


@pytest.mark.parametrize("key", ["nav_icon", "nav_icon_dark"])
def test_both_keys_share_the_rules_and_only_warn(key: str) -> None:
    lines = 'nav_icon = "web/static/icon.svg"\n' if key == "nav_icon_dark" else ""
    report = _report(lines + f'{key} = "../outside.svg"\n')
    assert report.ok()                     # a bad icon never costs the plugin its load
    assert len(report.warnings) == 1
    assert key in report.warnings[0] and "../outside.svg" in report.warnings[0]


def test_a_dark_icon_without_a_light_one_is_a_warning() -> None:
    report = _report('nav_icon_dark = "web/static/icon-dark.svg"\n')
    assert report.ok()
    assert any("no nav_icon" in w for w in report.warnings)


# ─── the web host's frontend manifest ────────────────────────────────────


def _host(page: dict) -> dict:
    host = plugin_host.PluginHost()
    host.rows = {"iconz": {
        "slug": "iconz", "name": "iconz", "version": "1.0.0", "publisher": "t", "license": "MIT",
        "enabled": True, "bundled": False, "install_source": "zip", "source_ref": None,
        "install_dir": "/nowhere/iconz", "status": "ok", "last_error": None,
        "installed_at": None, "updated_at": None,
        "manifest": {"web": {"scripts": ["web/static/iconz.jsx"],
                             "pages": [{"route": "iconz", "page": "IconzPage",
                                        "nav_label": "Iconz", **page}]}},
    }}
    (entry,) = host.frontend_manifest()["plugins"]
    (pg,) = entry["pages"]
    return pg


def test_frontend_manifest_carries_both_icons() -> None:
    pg = _host({"nav_icon": "web/static/icon.svg", "nav_icon_dark": "web/static/icon-dark.svg"})
    assert pg["nav_icon"] == "/plugins/iconz/static/icon.svg"
    assert pg["nav_icon_dark"] == "/plugins/iconz/static/icon-dark.svg"


def test_frontend_manifest_single_icon_has_no_dark_one() -> None:
    pg = _host({"nav_icon": "web/static/icon.svg"})
    assert pg["nav_icon"] == "/plugins/iconz/static/icon.svg"
    assert pg["nav_icon_dark"] is None


@pytest.mark.parametrize(("page", "light"), [
    # a light icon outside the static folder: neither is used (no pair)
    ({"nav_icon": "../../core/secret.svg", "nav_icon_dark": "web/static/icon-dark.svg"}, None),
    # a dark icon alone is ignored
    ({"nav_icon_dark": "web/static/icon-dark.svg"}, None),
    # a dark icon that is a URL is ignored; the light one stays
    ({"nav_icon": "web/static/icon.svg", "nav_icon_dark": "https://example.com/x.svg"},
     "/plugins/iconz/static/icon.svg"),
])
def test_frontend_manifest_drops_icons_that_break_the_rules(page: dict, light: str | None) -> None:
    pg = _host(page)
    assert pg["nav_icon"] == light
    assert pg["nav_icon_dark"] is None


# ─── the scaffold shows the optional key ─────────────────────────────────


def test_scaffold_shows_the_optional_dark_icon(tmp_path: Path) -> None:
    assert _cmd_new(argparse.Namespace(slug="scaf", dir=str(tmp_path))) == 0
    text = (tmp_path / "scaf" / "domovoi-plugin.toml").read_text(encoding="utf-8")
    assert "# nav_icon_dark = " in text and "optional" in text
    parse_manifest(text)                      # still valid as scaffolded
    # Uncommented, the example page is a valid manifest with both icons.
    lines = [ln[2:] if ln.startswith("# ") and "=" in ln or ln.startswith("# [") else ln
             for ln in text.splitlines()]
    (page,) = parse_manifest("\n".join(lines)).web_pages
    assert page.nav_icon == "web/static/icon.svg"
    assert page.nav_icon_dark == "web/static/icon-dark.svg"


# ─── the dashboard picks the variant ─────────────────────────────────────


def _scenario(props: dict, theme: str | None = "light", setup: str | None = None) -> dict:
    return {"file": COMPONENTS, "component": "PluginNavIcon", "props": props,
            "theme": theme, "setup": setup}


SCENARIOS = {
    "one-svg-light": _scenario({"src": "/p/icon.svg"}),
    "one-svg-dark": _scenario({"src": "/p/icon.svg"}, theme="dark"),
    "pair-light": _scenario({"src": "/p/icon.svg", "srcDark": "/p/icon-dark.svg"}),
    "pair-dark": _scenario({"src": "/p/icon.svg", "srcDark": "/p/icon-dark.svg"}, theme="dark"),
    "pair-dark-missing": _scenario({"src": "/p/icon.svg", "srcDark": "/p/icon-dark.svg"}, theme="dark",
                                   setup="PLUGIN_ICON_LOOK.set('/p/icon-dark.svg', 'missing');"),
    "artwork": _scenario({"src": "/p/logo.png"}, theme="dark",
                         setup="PLUGIN_ICON_LOOK.set('/p/logo.png', 'image');"),
    "missing": _scenario({"src": "/p/gone.svg"}, setup="PLUGIN_ICON_LOOK.set('/p/gone.svg', 'missing');"),
    "none": _scenario({"src": None}),
    "nav-items": {"file": COMPONENTS, "component": "(() => { const it = buildNavItems({ plugins: [{ slug: 'iconz',"
                  " pages: [{ route: 'iconz', nav_label: 'Iconz', nav_icon: '/a.svg', nav_icon_dark: '/b.svg' },"
                  " { route: 'iconz2', nav_label: 'Two', nav_icon: '/c.svg' }] }] })"
                  ".filter((x) => !x.core); return () => React.createElement('div', null,"
                  " ...it.map((x) => React.createElement('i', { key: x.route, 'data-src': x.iconSrc,"
                  " 'data-dark': String(x.iconSrcDark) }))); })()", "props": {}},
}


@pytest.fixture(scope="module")
def rendered() -> dict:
    node = shutil.which("node")
    assert node, "node is required to render web/static JSX (see jsxcheck)"
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _icon(rendered: dict, name: str) -> dict:
    (el,) = rendered[name]
    return el


def test_one_icon_is_a_mask_in_the_text_colour_in_both_themes(rendered) -> None:
    # Never an <img>: an SVG's currentColor would be black on the dark sidebar.
    for name in ("one-svg-light", "one-svg-dark"):
        el = _icon(rendered, name)
        assert el["type"] == "span", name
        assert "plugin-ico" in el["props"]["className"].split()
        assert el["props"]["aria-hidden"] == "true"


def test_a_pair_shows_each_icon_as_drawn_in_its_theme(rendered) -> None:
    light, dark = _icon(rendered, "pair-light"), _icon(rendered, "pair-dark")
    assert (light["type"], light["props"]["src"]) == ("img", "/p/icon.svg")
    assert (dark["type"], dark["props"]["src"]) == ("img", "/p/icon-dark.svg")


def test_a_dark_icon_that_fails_falls_back_to_the_light_one_as_a_mask(rendered) -> None:
    el = _icon(rendered, "pair-dark-missing")
    assert el["type"] == "span" and "plugin-ico" in el["props"]["className"]


def test_coloured_artwork_is_shown_as_it_is(rendered) -> None:
    el = _icon(rendered, "artwork")
    assert (el["type"], el["props"]["src"]) == ("img", "/p/logo.png")


def test_no_icon_or_a_missing_one_is_the_puzzle_glyph(rendered) -> None:
    for name in ("missing", "none"):
        el = _icon(rendered, name)
        assert el["type"] == "Icon", name           # the lucide glyph, not an empty mask
    src = (REPO_ROOT / COMPONENTS).read_text(encoding="utf-8")
    assert "return <Icon name=\"puzzle\" className=\"ico\" size={size}/>;" in src


def test_nav_items_carry_the_dark_icon(rendered) -> None:
    icons = [e for e in rendered["nav-items"] if e["type"] == "i"]
    assert [(e["props"]["data-src"], e["props"]["data-dark"]) for e in icons] == [
        ("/a.svg", "/b.svg"), ("/c.svg", "null")]


def test_every_launcher_hands_the_dark_icon_on() -> None:
    # Sidebar, Home's grid and the phone bar editor all draw PluginNavIcon
    # from a nav item; the plugin page frame draws it from the page itself.
    for f in ("components.jsx", "home.jsx", "settings.jsx"):
        src = (REPO_ROOT / "web" / "static" / f).read_text(encoding="utf-8")
        uses = src.count("<PluginNavIcon src={it.iconSrc}")
        assert uses >= 1, f
        assert src.count("srcDark={it.iconSrcDark}") == uses, f
    src = (REPO_ROOT / COMPONENTS).read_text(encoding="utf-8")
    assert src.count("<PluginNavIcon ") == src.count("<PluginNavIcon src={it.iconSrc}") + 1
    assert "<PluginNavIcon src={pg.nav_icon || null} srcDark={pg.nav_icon_dark || null}" in src
