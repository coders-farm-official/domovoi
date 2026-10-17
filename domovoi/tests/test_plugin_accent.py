"""Plugin pages read as plugins: the frame and the accent (2026-10-17).

The owner's request: a plugin's page should look like a plugin, not like
part of Domovoi, and every plugin gets that from core. So:

- ``[plugin].accent`` / ``accent_dark`` (optional hex): validated and
  turned into the brand tokens for both themes by
  ``domovoi/plugins_runtime/accent.py``, with the contrast rules (text and
  links 4.5:1, the button fill 3:1, the text on it 4.5:1) checked on every
  surface. A bad value or core's own amber is refused with a warning (the
  default plugin accent instead); a colour that fails is moved until it
  passes, with a warning; nothing ever fails the plugin.
- No accent: the neutral plugin slate (hue 240), never amber.
- The web host hands the tokens (and the publisher) to the dashboard and
  to Android (/api/capabilities); the dashboard's PluginPageFrame sets them
  on the plugin page's wrapper only, so the shell stays amber.

The JSX is rendered with domovoi/tests/jsx_render_harness.js (needs node).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from domovoi.plugins_runtime import accent as acc
from domovoi.plugins_runtime.cli import _cmd_new
from domovoi.plugins_runtime.contracts import ContractReport, check_accent
from domovoi.plugins_runtime.manifest import parse_manifest
from web.backend import plugin_host

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_render_harness.js")
COMPONENTS = "web/static/components.jsx"
PLUGINS = "web/static/plugins.jsx"
TOKENS_CSS = REPO_ROOT / "web" / "static" / "colors_and_type.css"


def _manifest(plugin_lines: str = "") -> str:
    return textwrap.dedent(
        """
        [plugin]
        slug = "tint"
        name = "Tint"
        version = "1.0.0"
        publisher = "tests"
        license = "MIT"
        description = "generated test plugin"
        domovoi_api = ">=1.0,<2.0"
        {extra}

        [entry_points]
        core = "domovoi_plugin_tint.core"
        """
    ).format(extra=plugin_lines)


def _passes(tokens: dict[str, str], theme: str) -> list[str]:
    """Every rule the module promises, measured on the hex it hands out."""
    backs = [acc.parse_hex(acc.to_hex(c)) for c in acc.surfaces(theme).values()]
    fill = acc.parse_hex(tokens["brand"])
    soft_a = int(tokens["brand_soft"][7:9], 16) / 255
    soft = [acc.over(fill, soft_a, b) for b in backs]
    ink = acc.parse_hex(tokens["brand_ink"])
    fg = acc.parse_hex(tokens["brand_fg"])
    bad = []
    if acc.min_ratio(fill, backs) < acc.UI:
        bad.append(f"fill {acc.min_ratio(fill, backs):.2f}")
    if acc.min_ratio(ink, backs + soft) < acc.TEXT:
        bad.append(f"ink {acc.min_ratio(ink, backs + soft):.2f}")
    for k in ("brand", "brand_hover", "brand_press"):
        r = acc.ratio(fg, acc.parse_hex(tokens[k]))
        if r < acc.TEXT:
            bad.append(f"fg on {k} {r:.2f}")
    return bad


# ─── the module's surfaces are the dashboard's ───────────────────────────


def _css_block(css: str, selector: str) -> dict[str, str]:
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", css, re.S)
    assert m, selector
    body = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
    return dict(re.findall(r"--([\w-]+)\s*:\s*([^;]+);", body))


def test_surfaces_match_the_token_file() -> None:
    css = TOKENS_CSS.read_text(encoding="utf-8")
    light = _css_block(css, "/* ---- BASE TOKENS — light (default) -------------------------- */\n:root")
    dark = _css_block(css, ":root[data-theme='dark']")
    for theme, block in (("light", light), ("dark", dark)):
        for name, (L, C, H) in acc.SURFACES[theme].items():
            m = re.fullmatch(r"oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\)", block[name].strip())
            assert m, (theme, name, block[name])
            assert (float(m.group(1)), float(m.group(2))) == (L, C), (theme, name)
            if C:
                assert float(m.group(3)) == H, (theme, name)


def test_core_amber_is_still_cores_alone() -> None:
    # The token file keeps amber; nothing in the shared stylesheet re-points
    # --brand (only the plugin frame does, inline, on its own wrapper).
    css = TOKENS_CSS.read_text(encoding="utf-8")
    light = _css_block(css, "/* ---- BASE TOKENS — light (default) -------------------------- */\n:root")
    dark = _css_block(css, ":root[data-theme='dark']")
    assert light["brand"].strip() == "oklch(0.78 0.16 75)"
    assert dark["brand"].strip() == "oklch(0.80 0.16 75)"
    styles = (REPO_ROOT / "web" / "static" / "styles.css").read_text(encoding="utf-8")
    assert not re.search(r"--brand(-[a-z]+)?\s*:", styles)


# ─── deriving the tokens ─────────────────────────────────────────────────


def test_a_good_accent_is_used_as_declared_without_warnings() -> None:
    a = acc.resolve_accent("#2f7d6d")
    assert a.source == "plugin" and a.warnings == []
    assert a.light["brand"] == "#2f7d6d"           # it reads at 3:1 already
    assert set(a.light) == set(a.dark) == set(acc.TOKEN_KEYS)
    assert _passes(a.light, "light") == [] and _passes(a.dark, "dark") == []
    # The dark theme derives a lighter shade from the one colour.
    assert acc.luminance(acc.parse_hex(a.dark["brand"])) > acc.luminance(acc.parse_hex("#2f7d6d"))


def test_accent_dark_is_the_dark_themes_own_colour() -> None:
    a = acc.resolve_accent("#2f7d6d", "#5fb8a4")
    assert a.warnings == [] and a.dark["brand"] == "#5fb8a4"
    assert _passes(a.dark, "dark") == []


def test_short_hex_and_case_are_accepted() -> None:
    a = acc.resolve_accent("#36C")
    assert a.source == "plugin" and a.light["brand"] == "#3366cc"


@pytest.mark.parametrize("bad", ["teal", "#12345", "#ggg", "", "2f7d6d", "#2f7d6d80", 5, ["#2f7d6d"]])
def test_a_value_that_is_not_a_colour_gets_the_default_with_a_warning(bad) -> None:
    a = acc.resolve_accent(bad)
    assert a.source == "default"
    assert a.light == acc.default_accent().light
    (w,) = a.warnings
    assert w.startswith("plugin.accent ") and "default plugin accent" in w


@pytest.mark.parametrize("amber", ["#f2a618", "#ffb000", "#e0a030", "#ffd400"])
def test_cores_amber_family_is_refused(amber: str) -> None:
    a = acc.resolve_accent(amber)
    assert a.source == "default"
    assert "amber" in a.warnings[0]


@pytest.mark.parametrize("pale", ["#abc", "#a0e0ff", "#ffffff", "#ccffcc"])
def test_a_low_contrast_accent_is_moved_until_it_passes_and_warns(pale: str) -> None:
    a = acc.resolve_accent(pale)
    assert a.source == "plugin"
    (w,) = a.warnings
    assert "buttons need 3:1" in w and a.light["brand"] in w     # names the colour used
    assert a.light["brand"] != acc.to_hex(acc.parse_hex(pale))
    assert _passes(a.light, "light") == []


def test_a_bad_accent_dark_falls_back_to_the_derived_shade() -> None:
    a = acc.resolve_accent("#2f7d6d", "nope")
    assert a.source == "plugin" and a.dark == acc.resolve_accent("#2f7d6d").dark
    assert any("accent_dark" in w for w in a.warnings)


def test_accent_dark_alone_is_ignored_with_a_warning() -> None:
    a = acc.resolve_accent(None, "#5fb8a4")
    assert a.source == "default" and "only beside plugin.accent" in a.warnings[0]


def test_every_colour_on_a_grid_ends_up_passing_in_both_themes() -> None:
    steps = ("00", "33", "66", "99", "cc", "ff")
    failures = []
    for r in steps:
        for g in steps:
            for b in steps:
                hx = f"#{r}{g}{b}"
                a = acc.resolve_accent(hx, hx)
                for theme in ("light", "dark"):
                    bad = _passes(a.light if theme == "light" else a.dark, theme)
                    if bad:
                        failures.append((hx, theme, bad))
    assert failures == []


# ─── the default plugin accent ───────────────────────────────────────────


def test_the_default_is_a_muted_slate_far_from_amber_that_passes() -> None:
    d = acc.default_accent()
    assert d.source == "default" and d.warnings == []
    for theme, tokens in (("light", d.light), ("dark", d.dark)):
        assert _passes(tokens, theme) == []
        L, C, H = acc.rgb_to_oklch(acc.parse_hex(tokens["brand"]))
        assert 220 <= H <= 260 and C < 0.08, (theme, H, C)
        assert acc.accent_problem(tokens["brand"]) is None        # not amber
    # It is the calendar's "from outside Domovoi" hue (styles.css).
    styles = (REPO_ROOT / "web" / "static" / "styles.css").read_text(encoding="utf-8")
    assert "oklch(0.6 0.04 240)" in styles


# ─── the manifest and the load check ─────────────────────────────────────


def test_the_manifest_carries_the_keys_and_never_refuses_them() -> None:
    m = parse_manifest(_manifest('accent = "#2f7d6d"\naccent_dark = "#5fb8a4"'))
    assert (m.accent, m.accent_dark) == ("#2f7d6d", "#5fb8a4")
    assert m.raw["plugin"]["accent"] == "#2f7d6d"
    for bad in ('accent = "teal"', "accent = 5", 'accent = "#f2a618"'):
        parse_manifest(_manifest(bad))                     # no ManifestError
    assert parse_manifest(_manifest()).accent is None


@pytest.mark.parametrize(("line", "fragment"), [
    ('accent = "teal"', "is not a hex colour"),
    ('accent = "#f2a618"', "amber"),
    ('accent = "#abc"', "buttons need 3:1"),
])
def test_the_load_check_warns_and_still_loads(line: str, fragment: str) -> None:
    report = ContractReport()
    check_accent(parse_manifest(_manifest(line)), report)
    assert report.ok()
    assert any(fragment in w for w in report.warnings), report.warnings


def test_the_load_check_is_quiet_for_a_good_accent_or_none() -> None:
    for line in ('accent = "#2f7d6d"', ""):
        report = ContractReport()
        check_accent(parse_manifest(_manifest(line)), report)
        assert report.warnings == []


def test_the_bundled_radio_plugin_is_the_worked_example() -> None:
    m = parse_manifest((REPO_ROOT / "plugins/radio/domovoi-plugin.toml").read_text(encoding="utf-8"))
    assert m.accent == "#2f7d6d"
    assert acc.resolve_accent(m.accent, m.accent_dark).warnings == []


def test_the_scaffold_offers_the_accent(tmp_path: Path) -> None:
    assert _cmd_new(argparse.Namespace(slug="paint", dir=str(tmp_path))) == 0
    text = (tmp_path / "paint" / "domovoi-plugin.toml").read_text(encoding="utf-8")
    assert '# accent = "#2f7d6d"' in text and "# accent_dark = " in text
    assert parse_manifest(text).accent is None             # off until uncommented
    on = text.replace('# accent = "', 'accent = "').replace("# accent_dark = ", "accent_dark = ")
    m = parse_manifest(on)
    assert (m.accent, m.accent_dark) == ("#2f7d6d", "#5fb8a4")
    assert acc.resolve_accent(m.accent, m.accent_dark).warnings == []


# ─── the web host: the dashboard's manifest and Android's capabilities ──


def _row(plugin_extra: dict) -> dict:
    return {
        "slug": "tint", "name": "Tint", "version": "1.2.3", "publisher": "Coders Farm",
        "license": "MIT", "enabled": True, "bundled": False, "install_source": "zip",
        "source_ref": None, "install_dir": "/nowhere/tint", "status": "ok", "last_error": None,
        "installed_at": None, "updated_at": None,
        "manifest": {"plugin": {"slug": "tint", **plugin_extra},
                     "android": {"capabilities": ["tint"]},
                     "web": {"scripts": ["web/static/tint.jsx"],
                             "pages": [{"route": "tint", "page": "TintPage", "nav_label": "Tint"}]}},
    }


def _frontend(plugin_extra: dict) -> dict:
    host = plugin_host.PluginHost()
    host.rows = {"tint": _row(plugin_extra)}
    (entry,) = host.frontend_manifest()["plugins"]
    return entry


def test_frontend_manifest_carries_publisher_and_accent() -> None:
    entry = _frontend({"accent": "#2f7d6d"})
    assert entry["publisher"] == "Coders Farm" and entry["name"] == "Tint"
    assert entry["accent"]["source"] == "plugin"
    assert entry["accent"]["light"]["brand"] == "#2f7d6d"
    assert set(entry["accent"]["dark"]) == set(acc.TOKEN_KEYS)


def test_frontend_manifest_gives_a_plugin_without_an_accent_the_default() -> None:
    entry = _frontend({})
    assert entry["accent"] == acc.default_accent().as_dict()
    assert _frontend({"accent": "nope"})["accent"]["source"] == "default"


def test_capabilities_carry_the_frame_for_android(monkeypatch) -> None:
    from web.backend.api import capabilities as caps

    monkeypatch.setattr(caps.HOST, "rows", {"tint": _row({"accent": "#2f7d6d"})})
    body = asyncio.run(caps.capabilities())
    (p,) = body["plugins"]
    assert (p["name"], p["publisher"]) == ("Tint", "Coders Farm")
    assert p["accent"]["source"] == "plugin" and p["accent"]["light"]["brand"] == "#2f7d6d"
    assert p["android_capabilities"] == ["tint"]


# ─── the dashboard: the frame, and tokens scoped to it ───────────────────

RADIO = {
    "slug": "radio", "name": "Radio", "version": "1.3.1", "publisher": "Coders Farm",
    "accent": acc.resolve_accent("#2f7d6d").as_dict(),
}
PAGE = {"route": "stations", "page": "StationsPage", "nav_label": "Stations",
        "nav_icon": "/plugins/radio/static/icon.svg", "nav_icon_dark": None}
STYLE_PROBE = ("((p) => { const s = pluginAccentStyle(p.accent, p.dark); return React.createElement('i',"
               " Object.fromEntries(Object.entries(s).map(([k, v]) => ['data' + k, v]))); })")


def _frame(plugin: dict, theme: str = "light") -> dict:
    return {"file": COMPONENTS, "component": "PluginPageFrame", "theme": theme,
            "props": {"plugin": plugin, "page": PAGE}}


SCENARIOS = {
    "frame-light": _frame(RADIO),
    "frame-dark": _frame(RADIO, "dark"),
    "frame-no-accent": _frame({k: v for k, v in RADIO.items() if k != "accent"}),
    "frame-no-publisher": _frame({**RADIO, "publisher": None}),
    "style-light": {"file": COMPONENTS, "component": STYLE_PROBE,
                    "props": {"accent": RADIO["accent"], "dark": False}},
    "style-dark": {"file": COMPONENTS, "component": STYLE_PROBE,
                   "props": {"accent": RADIO["accent"], "dark": True}},
    "style-hostile": {"file": COMPONENTS, "component": STYLE_PROBE,
                      "props": {"accent": {"light": {"brand": "red; background: url(x)",
                                                     "brand_ink": "#123456", "other": "#000000"}},
                                "dark": False}},
    "style-none": {"file": COMPONENTS, "component": STYLE_PROBE, "props": {"accent": None, "dark": False}},
    "plugins-row-focused": {
        "file": PLUGINS, "component": "PluginRow", "preload": [COMPONENTS],
        "props": {"p": {"slug": "radio", "name": "Radio", "version": "1.3.1", "publisher": "Coders Farm",
                        "license": "MIT", "enabled": True, "status": "ok", "permissions": {},
                        "install_source": "bundled", "bundled": True, "pages": []},
                  "restartPending": False, "focused": True},
        "fnProps": ["onEnable", "onDisable", "onUninstall", "onUpgradeZip", "onUpgradeUrl"]},
    "plugins-row-plain": {
        "file": PLUGINS, "component": "PluginRow", "preload": [COMPONENTS],
        "props": {"p": {"slug": "radio", "name": "Radio", "version": "1.3.1", "publisher": "Coders Farm",
                        "license": "MIT", "enabled": True, "status": "ok", "permissions": {},
                        "install_source": "bundled", "bundled": True, "pages": []},
                  "restartPending": False},
        "fnProps": ["onEnable", "onDisable", "onUninstall", "onUpgradeZip", "onUpgradeUrl"]},
}


@pytest.fixture(scope="module")
def rendered() -> dict:
    node = shutil.which("node")
    assert node, "node is required to render web/static JSX (see jsxcheck)"
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _by_class(els: list[dict], cls: str) -> list[dict]:
    return [e for e in els if cls in str(e["props"].get("className", "")).split()]


def test_the_frame_names_the_plugin_and_links_to_its_row(rendered) -> None:
    els = rendered["frame-light"]
    (root,) = _by_class(els, "plugin-frame")
    assert root["props"]["data-plugin"] == "radio" and root["props"]["data-accent"] == "plugin"
    assert _by_class(els, "plugin-frame-name")[0]["text"] == "Radio"
    assert _by_class(els, "plugin-frame-publisher")[0]["text"] == "by Coders Farm"
    assert _by_class(els, "plugin-frame-badge")[0]["text"] == "plugin"
    (manage,) = _by_class(els, "plugin-frame-manage")
    assert manage["type"] == "a" and manage["props"]["href"] == "#plugins/radio"
    assert "version, update, disable" in manage["props"]["title"]
    assert _by_class(els, "plugin-frame-version")[0]["text"] == "v1.3.1"
    # The plugin's own nav icon, drawn by the shared PluginNavIcon (a mask).
    assert any(e["type"] == "span" and "plugin-ico" in str(e["props"].get("className")) for e in els)


def test_the_frame_reads_the_same_in_the_dark(rendered) -> None:
    (root,) = _by_class(rendered["frame-dark"], "plugin-frame")
    assert root["props"]["data-accent"] == "plugin"


def test_a_plugin_with_no_accent_still_gets_the_frame(rendered) -> None:
    els = rendered["frame-no-accent"]
    (root,) = _by_class(els, "plugin-frame")
    assert root["props"]["data-accent"] == "none"
    assert _by_class(els, "plugin-frame-badge")[0]["text"] == "plugin"
    assert _by_class(rendered["frame-no-publisher"], "plugin-frame-publisher") == []


def _vars(rendered: dict, name: str) -> dict:
    (el,) = rendered[name]
    return {k[4:]: v for k, v in el["props"].items() if k.startswith("data--")}


def test_the_frame_scopes_the_brand_tokens_per_theme(rendered) -> None:
    light, dark = _vars(rendered, "style-light"), _vars(rendered, "style-dark")
    tokens = RADIO["accent"]
    assert light["--brand"] == tokens["light"]["brand"] == "#2f7d6d"
    assert light["--brand-ink"] == tokens["light"]["brand_ink"]
    assert light["--brand-soft"] == tokens["light"]["brand_soft"]
    assert light["--fg-on-brand"] == light["--brand-fg"] == tokens["light"]["brand_fg"]
    assert dark["--brand"] == tokens["dark"]["brand"]
    # Only the brand family: no surfaces, text or status tokens.
    assert set(light) == {"--brand", "--brand-hover", "--brand-press", "--brand-fg", "--fg-on-brand",
                          "--brand-soft", "--brand-halo", "--brand-ring", "--brand-ink"}


def test_the_frame_sets_only_well_formed_colours(rendered) -> None:
    assert _vars(rendered, "style-hostile") == {"--brand-ink": "#123456"}
    assert _vars(rendered, "style-none") == {}


def test_only_plugin_pages_are_framed() -> None:
    html = (REPO_ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")
    assert html.count("<PluginPageFrame ") == 1
    # The frame is the plugin branch of one choice; a core page renders bare.
    m = re.search(r"\{pagePlugin \? \((.*?)\) : \((.*?)\)\}", html, re.S)
    assert m, "the shell picks the frame by pagePlugin"
    framed, bare = m.group(1), m.group(2)
    assert "<PluginPageFrame key={pageSlug} plugin={pagePlugin} page={pageDecl}>" in framed
    assert "PluginPageFrame" not in bare and "<Page counts={counts} badges={badges}/>" in bare
    # pagePlugin is set only where a manifest page resolved the route.
    assert "pagePlugin = p; pageDecl = pg;" in html
    assert len(re.findall(r"\bpagePlugin = (?!null)", html)) == 1


def test_the_manage_link_opens_the_plugins_row(rendered) -> None:
    focused = rendered["plugins-row-focused"]
    (row,) = [e for e in focused if e["props"].get("id") == "plugin-radio"]
    assert "focused" in row["props"]["className"]
    assert any(e["text"] == "slug" for e in focused)            # details open
    plain = rendered["plugins-row-plain"]
    assert not any(e["text"] == "slug" for e in plain)          # closed as before
    src = (REPO_ROOT / PLUGINS).read_text(encoding="utf-8")
    assert "focused={p.slug === focusSlug}" in src
    assert "routeParts(currentRoute())[1]" in src
