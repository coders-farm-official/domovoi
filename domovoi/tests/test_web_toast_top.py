"""Toasts at the top, bigger, in the other theme's palette (2026-11-05).

The owner missed toasts at the bottom of the page several times (the
latest: a plugin page's "already in your library"). Now useToast
(web/static/components.jsx) puts its stack at the top, centred, under the
topbar and the container guard's banner; the text is 15px; and the stack
carries `.theme-invert`, which colors_and_type.css gives the OTHER theme's
tokens: a light toast on a dark page, a dark one on a light page. Each
toast has a tone (info / ok / warn / err) drawn as its left rail in the ink
tokens, and is announced as a status, or as an alert when it is an error.

Inside a plugin's frame the brand tokens are the plugin's accent, never
amber, so the frame also hands an inverted toast its OTHER-theme accent
(--inv-*, PluginPageFrame), which styles.css maps back onto the brand
tokens there.

Static checks on the sources, the token maths of test_web_contrast.py, and
the real hook driven by jsx_interact_harness.js. No DB.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.plugins_runtime import accent as acc
from domovoi.tests.test_web_contrast import TEXT, THEMES, UI, _parse, _surface, ratio

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")


def _src(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _use_toast() -> str:
    comps = _src("components.jsx")
    return comps[comps.index("const useToast"):comps.index("/* ---- Tabs")]


def _rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


# ─── where it sits ───────────────────────────────────────────────────────


def test_the_stack_sits_at_the_top_under_the_topbar_and_the_guard_banner() -> None:
    toast = _use_toast()
    assert "top: 'calc(var(--topbar-h, 52px) + var(--guard-banner-h, 0px) + 12px)'" in toast
    assert "bottom:" not in toast
    # The viewport less a 16px gutter a side (no sideways scroll on a 375px
    # phone), clicks passing through between toasts.
    assert "left: 16, right: 16," in toast and "pointerEvents: 'none'" in toast
    css = _src("styles.css")
    toast_rule = _rule(css, ".toast-stack .toast")
    assert "pointer-events: auto;" in toast_rule
    assert "max-width: min(100%, 600px);" in toast_rule
    # Bigger: the type scale's 15px step, 12/16px of padding.
    assert "font-size: var(--t-md);" in toast_rule
    assert "padding: 12px 16px;" in _rule(css, ".toast-brief")


# ─── the other theme's palette ───────────────────────────────────────────


def test_the_stack_takes_the_other_themes_tokens() -> None:
    assert 'className="toast-stack theme-invert"' in _use_toast()
    tokens = _src("colors_and_type.css")
    assert re.search(r"\n:root,\n:root\[data-theme='dark'\] \.theme-invert \{\n  /\* Brand", tokens)
    assert re.search(r"\n:root\[data-theme='dark'\],\n:root:not\(\[data-theme='dark'\]\) \.theme-invert \{\n"
                     r"  --canvas:", tokens)
    assert ":root[data-theme='dark'] .theme-invert { color-scheme: light; }" in tokens
    assert ":root:not([data-theme='dark']) .theme-invert { color-scheme: dark; }" in tokens
    # The toast's surfaces and text are tokens, never written colours.
    css = _src("styles.css")
    toast_rule = _rule(css, ".toast-stack .toast")
    assert "background: var(--overlay); color: var(--fg);" in toast_rule
    assert not re.search(r"#[0-9a-f]{3,8}\b|oklch\(|rgb", toast_rule, re.I)


def test_the_inverted_highlight_is_the_dark_themes_own() -> None:
    # :root resolves --inner-highlight with the light --highlight; a dark
    # toast on a light page would inherit that white edge unless the dark
    # block restates it.
    assert "inner-highlight" in THEMES["dark"]
    dark_block = _src("colors_and_type.css").split("/* ---- DARK THEME", 1)[1].split("\n}\n", 1)[0]
    assert "--inner-highlight: inset 0 1px 0 var(--highlight);" in dark_block


TONE_RAIL = {"ok": "ok-ink", "warn": "warn-ink", "err": "err-ink"}


def test_each_tone_has_its_rail() -> None:
    css = _src("styles.css")
    for tone, ink in TONE_RAIL.items():
        assert f".toast[data-tone='{tone}']" in css
        assert f"--toast-rail: var(--{ink});" in _rule(css, f".toast[data-tone='{tone}']")
    # info keeps the neutral edge and the pulsing brand dot
    assert "var(--toast-rail, var(--border-strong))" in _rule(css, ".toast-stack .toast")


@pytest.mark.parametrize("palette", ["light", "dark"])
def test_toast_text_and_rails_pass_contrast_in_either_palette(palette: str) -> None:
    """The toast's background is --overlay in whichever palette it draws in
    (the page's opposite). Text 4.5:1, the help link 4.5:1, the tone rails
    3:1 as UI parts (WCAG AA)."""
    t = THEMES[palette]
    bg = _surface(palette, "overlay")
    for text in ("fg", "fg-muted", "brand-ink"):
        r = ratio(_parse(f"var(--{text})", t), bg)
        assert r >= TEXT, f"{palette} --{text} on --overlay: {r:.2f}"
    for tone, ink in TONE_RAIL.items():
        r = ratio(_parse(f"var(--{ink})", t), bg)
        assert r >= UI, f"{palette} {tone} rail --{ink} on --overlay: {r:.2f}"


@pytest.mark.parametrize("palette", ["light", "dark"])
def test_the_tones_stay_apart(palette: str) -> None:
    # Three rails in three distinct hues (green, orange, red), so an error
    # never reads like a success; info has none of them.
    hues = {}
    for tone, ink in TONE_RAIL.items():
        v = THEMES[palette][ink]
        v = THEMES[palette][v.strip()[6:-1]] if v.strip().startswith("var(") else v
        hues[tone] = float(re.search(r"oklch\(\s*[\d.]+\s+[\d.]+\s+([\d.]+)", v).group(1))
    assert len(set(hues.values())) == 3, hues
    assert abs(hues["ok"] - hues["err"]) > 90 and abs(hues["warn"] - hues["err"]) >= 30, hues


@pytest.mark.parametrize("page", ["light", "dark"])
def test_an_inverted_toast_stands_out_from_the_page(page: str) -> None:
    other = "dark" if page == "light" else "light"
    r = ratio(_surface(other, "overlay"), _surface(page, "canvas"))
    assert r >= 7, f"{other} toast on a {page} page: {r:.2f}"


# ─── plugin pages ────────────────────────────────────────────────────────


def test_a_plugin_frame_hands_the_toast_its_other_theme_accent() -> None:
    css = _src("styles.css")
    rule = _rule(css, ":root .plugin-frame[data-accent-inverse] .theme-invert")
    for name in ("brand", "brand-hover", "brand-press", "brand-fg", "fg-on-brand",
                 "brand-soft", "brand-halo", "brand-ring", "brand-ink"):
        assert f"--{name}: var(--inv-{name});" in rule, name
    comps = _src("components.jsx")
    frame = comps[comps.index("const PluginPageFrame"):comps.index("/* One nav row's classes.")]
    assert "data-accent-inverse={inverse ? '' : undefined}" in frame


# ─── the hook, driven ────────────────────────────────────────────────────

PROBE = ("(() => { const P = () => { const r = useToast(); window.__toast = { fire: r[0], dismissKey: r[2] };"
         " return React.createElement('div', null, r[1] || null); }; return P; })()")

FIRE = r"""
h.render();
const t = h.global('window').__toast;
t.fire('saved');
t.fire('install failed: no space', { help: { code: 'E_DISK' } });
t.fire('added to the queue', { tone: 'ok' });
t.fire('could not reach the box', { stay: true, key: 'k' });
t.fire('still cannot reach the box', { stay: true, key: 'k' });
h.rerender(); await h.settle();
const cls = (el) => String((el.props && el.props.className) || '');
const stack = h.find((el) => cls(el).includes('toast-stack'));
const toasts = h.findAll((el) => /(^| )toast( |$)/.test(cls(el))).map((el) => ({
  cls: cls(el), tone: el.props['data-tone'], role: el.props.role }));
const stayText = h.findAll((el) => cls(el) === 'toast-stay-text').map((el) => el.text);
const before = toasts.length;
h.global('window').__toast.dismissKey('k');
h.rerender(); await h.settle();
const after = h.findAll((el) => /(^| )toast( |$)/.test(cls(el))).length;
return { stackClass: cls(stack), stackStyle: stack.props.style, toasts, stayText, before, after };
"""

INVERSE = r"""
h.render();
const inv = h.global('window').__inv;
return { light: inv(__ACCENT__, false), dark: inv(__ACCENT__, true),
         partial: inv({ light: { brand: '#112233' }, dark: { brand: '#445566' } }, false),
         none: inv(null, false) };
"""

RADIO_ACCENT = acc.resolve_accent("#146859").as_dict()

SCENARIOS = {
    "fire": {"files": ["web/static/components.jsx"], "component": PROBE, "script": FIRE},
    "inverse": {"files": ["web/static/components.jsx"],
                "component": "(window.__inv = pluginAccentInverseStyle, () => null)",
                "script": INVERSE.replace("__ACCENT__", json.dumps(RADIO_ACCENT))},
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
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


def test_the_hook_renders_the_stack_inverted_at_the_top(driven) -> None:
    o = driven["fire"]
    assert o["stackClass"] == "toast-stack theme-invert"
    style = o["stackStyle"]
    assert style["position"] == "fixed" and style["zIndex"] == 111
    assert "bottom" not in style and "--topbar-h" in style["top"]


def test_tones_roles_and_keyed_replacement(driven) -> None:
    o = driven["fire"]
    got = [(t["tone"], t["role"], "toast-stay" in t["cls"]) for t in o["toasts"]]
    assert got == [
        ("info", "status", False),     # plain news
        ("err", "alert", False),       # a failure (reportMutationFailure's help code)
        ("ok", "status", False),       # an explicit tone
        ("err", "alert", True),        # one that stays; the same key replaced its first
    ], o["toasts"]
    assert o["stayText"] == ["still cannot reach the box"], o["stayText"]
    assert o["before"] == 4 and o["after"] == 3


def test_the_inverse_accent_is_the_other_themes_and_all_or_nothing(driven) -> None:
    o = driven["inverse"]
    light_page, dark_page = o["light"], o["dark"]
    # On a light page the toast is dark: it gets the accent's dark values.
    assert light_page["--inv-brand"] == RADIO_ACCENT["dark"]["brand"]
    assert dark_page["--inv-brand"] == RADIO_ACCENT["light"]["brand"]
    assert dark_page["--inv-brand-ink"] == RADIO_ACCENT["light"]["brand_ink"]
    assert dark_page["--inv-fg-on-brand"] == RADIO_ACCENT["light"]["brand_fg"]
    assert len(light_page) == 9
    assert o["partial"] is None and o["none"] is None
