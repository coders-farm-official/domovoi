"""The dashboard's colours stay readable in both themes (WCAG 2.x ratios).

The owner's dark-mode report (2026-10-17) found plugin nav icons and the
"prepare satellite media" title nearly invisible; the audit behind the fix
(scripts/dev/contrast_audit.py, every page and tab of a running dashboard
in both themes) found the same disease in three root causes, which this
module holds closed without a browser:

1. the token ramp itself: `--fg-faint` read 2.2:1 on the dark card and
   1.9:1 on white, `--fg-subtle` 3.8:1 and 3.6:1, and amber / status text
   on white about 2:1. Every text token must read at 4.5:1 on every
   surface of its theme; amber and status TEXT goes through the -ink
   tokens; a disabled button keeps 3:1.
2. the browser's own colours: no `color-scheme`, so a <button> or <input>
   with no colour of its own drew black text on the dark card.
3. colours written past the tokens: white text on amber or red, a
   `var(--danger, #d8503c)` fallback for a token that didn't exist,
   amber / status hues as text where the -ink token belongs.

Colours are computed from the CSS text (oklch -> sRGB -> relative
luminance), the way the browser composites them (alpha in gamma space).
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
TOKENS = STATIC / "colors_and_type.css"
DOC_TOKENS = REPO_ROOT / "docs" / "design" / "colors_and_type.css"
STYLES = STATIC / "styles.css"

TEXT = 4.5      # WCAG AA, normal text
UI = 3.0        # icons, UI parts, large text, and (owner's floor) disabled controls


# ─── colour maths ────────────────────────────────────────────────────────


def _oklch_to_linear(L: float, C: float, H: float) -> tuple[float, float, float]:
    h = math.radians(H)
    a, b = C * math.cos(h), C * math.sin(h)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    rgb = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
           -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
    return tuple(min(1.0, max(0.0, c)) for c in rgb)  # gamut-clipped like the browser


def _encode(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _decode(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


Colour = tuple[float, float, float, float]   # gamma-encoded sRGB 0..1 + alpha


def _parse(value: str, tokens: dict[str, str]) -> Colour:
    value = value.strip()
    m = re.fullmatch(r"var\(--([\w-]+)\)", value)
    if m:
        return _parse(tokens[m.group(1)], tokens)
    m = re.fullmatch(r"oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*(?:/\s*([\d.]+)\s*)?\)", value)
    assert m, f"unparsed colour {value!r}"
    rgb = _oklch_to_linear(float(m.group(1)), float(m.group(2)), float(m.group(3)))
    return (*(_encode(c) for c in rgb), float(m.group(4)) if m.group(4) else 1.0)


def _over(top: Colour, under: Colour) -> Colour:
    a = top[3]
    return (*(t * a + u * (1 - a) for t, u in zip(top[:3], under[:3])), 1.0)


def _faded(c: Colour, alpha: float) -> Colour:
    return (*c[:3], c[3] * alpha)


def _lum(c: Colour) -> float:
    r, g, b = (_decode(x) for x in c[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(fg: Colour, bg: Colour) -> float:
    fg = _over(fg, bg)
    hi, lo = sorted((_lum(fg), _lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# ─── the token file ──────────────────────────────────────────────────────


def _block(css: str, selector: str) -> dict[str, str]:
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", css, re.S)
    assert m, selector
    body = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
    return dict(re.findall(r"--([\w-]+)\s*:\s*([^;]+);", body))


def _themes(path: Path = TOKENS) -> dict[str, dict[str, str]]:
    css = path.read_text(encoding="utf-8")
    light = _block(css, "/* ---- BASE TOKENS — light (default) -------------------------- */\n:root")
    dark = {**light, **_block(css, ":root[data-theme='dark']")}
    return {"light": light, "dark": dark}


THEMES = _themes()
SURFACES = ("canvas", "card", "raised", "sunken")


def _c(theme: str, token: str) -> Colour:
    return _parse(f"var(--{token})", THEMES[theme])


def _surface(theme: str, token: str) -> Colour:
    return _over(_c(theme, token), _c(theme, "canvas"))


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("text", ["fg", "fg-muted", "fg-subtle", "fg-faint"])
def test_every_text_token_reads_on_every_surface(theme: str, text: str) -> None:
    for surface in SURFACES:
        r = ratio(_c(theme, text), _surface(theme, surface))
        assert r >= TEXT, f"{theme} --{text} on --{surface}: {r:.2f}:1 (< {TEXT})"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_text_ramp_still_steps_down(theme: str) -> None:
    # Readable, and still a ramp: fg > muted > subtle >= faint in contrast.
    card = _surface(theme, "card")
    steps = [ratio(_c(theme, t), card) for t in ("fg", "fg-muted", "fg-subtle", "fg-faint")]
    assert steps == sorted(steps, reverse=True), steps


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("hue", ["brand", "ok", "warn", "err"])
def test_ink_reads_as_text_on_surfaces_and_on_its_own_wash(theme: str, hue: str) -> None:
    ink = _c(theme, f"{hue}-ink")
    wash = f"{'brand' if hue == 'brand' else hue}-soft"
    for surface in SURFACES:
        assert ratio(ink, _surface(theme, surface)) >= TEXT, (theme, hue, surface)
    on_wash = _over(_c(theme, wash), _surface(theme, "card"))
    assert ratio(ink, on_wash) >= TEXT, (theme, hue, "own wash: a pill")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_ink_is_the_fill_in_the_dark_theme_only(theme: str) -> None:
    for hue in ("brand", "ok", "warn", "err"):
        same = THEMES[theme][f"{hue}-ink"].strip() == f"var(--{hue})"
        assert same == (theme == "dark"), (theme, hue)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(("fg", "bg"), [("brand-fg", "brand"), ("danger-fg", "danger")])
def test_text_on_filled_buttons(theme: str, fg: str, bg: str) -> None:
    r = ratio(_c(theme, fg), _surface(theme, bg))
    assert r >= TEXT, f"{theme} --{fg} on --{bg}: {r:.2f}"


def test_the_docs_copy_carries_the_same_tokens() -> None:
    docs = _themes(DOC_TOKENS)
    for theme in ("light", "dark"):
        web = {k: v for k, v in THEMES[theme].items() if k != "bg"}
        assert {k: v for k, v in docs[theme].items() if k != "bg"} == web, theme


# ─── the browser's own colours ───────────────────────────────────────────


def test_each_theme_tells_the_browser_its_colour_scheme() -> None:
    css = TOKENS.read_text(encoding="utf-8")
    assert re.search(r":root\s*\{\s*color-scheme:\s*light;\s*\}", css)
    assert re.search(r":root\[data-theme='dark'\]\s*\{\s*color-scheme:\s*dark;\s*\}", css)


def test_controls_take_their_surroundings_colour() -> None:
    # Zero specificity, so any class or inline colour still wins.
    css = TOKENS.read_text(encoding="utf-8")
    assert re.search(r":where\(button, input, select, textarea\)\s*\{\s*color:\s*inherit;\s*\}", css)
    assert re.search(r":where\(input, textarea\)::placeholder\s*\{\s*color:\s*var\(--fg-subtle\);"
                     r"\s*opacity:\s*1;\s*\}", css)


# ─── disabled buttons keep 3:1 ───────────────────────────────────────────


def _disabled_opacity() -> float:
    css = STYLES.read_text(encoding="utf-8")
    m = re.search(r"\.btn:disabled,\s*\.btn\[disabled\]\s*\{[^}]*?opacity:\s*([\d.]+)", css, re.S)
    assert m
    return float(m.group(1))


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(("variant", "fg", "fill"), [
    ("primary", "brand-fg", "brand"), ("secondary", "fg", "card"), ("ghost", "fg-muted", None),
])
def test_a_disabled_button_is_still_readable(theme: str, variant: str, fg: str, fill: str | None) -> None:
    # opacity fades the whole button (its fill and its text) into the card.
    o = _disabled_opacity()
    card = _surface(theme, "card")
    if fill:
        fill_c = _over(_c(theme, fill), card)
        text = _over(_faded(_over(_c(theme, fg), fill_c), o), card)
        bg = _over(_faded(fill_c, o), card)
    else:
        text = _over(_faded(_c(theme, fg), o), card)
        bg = card
    r = ratio(text, bg)
    assert r >= UI, f"{theme} disabled {variant}: {r:.2f}:1 at opacity {o}"


# ─── colours written past the tokens ─────────────────────────────────────

FRONTEND = sorted([*STATIC.glob("*.jsx"), *STATIC.glob("*.js"), STYLES, STATIC / "index.html",
                   *(REPO_ROOT / "plugins" / "radio" / "web" / "static").glob("*.jsx")])
# The only place the brand fill colours a foreground on purpose: the logo
# (WCAG exempts logotypes), brand amber in both themes.
FILL_AS_TEXT_ALLOWED = {".brand-row .glyph {"}
_FILL_AS_TEXT = re.compile(r"(?<![-\w])color\s*:[^;,}\n]*var\(--(brand|brand-press|brand-hover|ok|warn|err)\)")


def _lines(path: Path):
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        yield i, line


def test_amber_and_status_text_uses_the_ink_tokens() -> None:
    bad = [f"{p.relative_to(REPO_ROOT)}:{i}: {line.strip()[:100]}"
           for p in FRONTEND for i, line in _lines(p)
           if _FILL_AS_TEXT.search(line) and not any(a in line for a in FILL_AS_TEXT_ALLOWED)]
    assert not bad, "use var(--brand-ink / ok-ink / warn-ink / err-ink) for text:\n" + "\n".join(bad)


_LITERAL_TEXT = re.compile(r"(?<![-\w])color\s*:\s*['\"]?(white|black|#fff\b|#ffffff\b|#000\b|#000000\b)",
                           re.I)


def test_no_literal_white_or_black_text() -> None:
    # White on amber read 2:1; a theme token reads in both themes.
    bad = [f"{p.relative_to(REPO_ROOT)}:{i}: {line.strip()[:100]}"
           for p in FRONTEND if p.suffix == ".jsx" for i, line in _lines(p)
           if _LITERAL_TEXT.search(line)]
    assert not bad, "\n".join(bad)


def test_every_token_the_frontend_uses_exists() -> None:
    # `var(--danger, #d8503c)` hid a token that was never defined: the
    # fallback colour, not the theme's, was what showed.
    defined = set(THEMES["light"]) | set(THEMES["dark"])
    used = set()
    for p in FRONTEND:
        if p.parent != STATIC:
            continue
        used |= set(re.findall(r"var\(--((?:fg|brand|ok|warn|err|danger|idle)[\w-]*)", p.read_text(encoding="utf-8")))
    assert used - defined == set()


def test_an_icon_takes_the_colour_it_is_given() -> None:
    # <Icon style={{ color }}/> was dropped on the floor: the playlist cover's
    # star inherited the light text colour onto its amber swatch (1.39:1).
    src = (STATIC / "components.jsx").read_text(encoding="utf-8")
    m = re.search(r"const Icon = \(\{([^}]*)\}\) => \{(.*?)\n\};", src, re.S)
    assert m and "style" in m.group(1)
    assert "...(style || {})" in m.group(2)
