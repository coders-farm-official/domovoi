"""A plugin's accent colour: validated, and turned into dashboard tokens.

Plugin pages should read as plugins, not as part of Domovoi itself
(owner, 2026-10-17). The dashboard draws a frame around every plugin page
and, inside it, re-scopes the brand tokens (``--brand``, ``--brand-soft``,
``--brand-ink`` ...) to the plugin's own colour, so its primary buttons,
links and highlights take that colour while the shell around it stays
amber. This module is the one place that colour is judged:

* ``[plugin].accent`` (``"#146859"``) and the optional ``accent_dark``
  (the dark theme's colour; without it the dark tokens are derived from
  ``accent``) are ``#rgb`` / ``#rrggbb`` hex strings.
* Every derived token passes the dashboard's contrast rules on every
  surface of its theme (canvas, card, raised, sunken): text and links
  (``brand_ink``) 4.5:1, also on the accent's own wash; the button fill
  (``brand``) 3:1 as a UI part; the text on that fill (``brand_fg``) 4.5:1
  on the fill and its hover and press shades, and 3:1 on a disabled
  button (both faded to the dashboard's disabled opacity). A colour that
  falls short
  is moved in lightness (same hue and chroma) until it passes.
* Nothing here ever fails a plugin. A value that isn't a colour, or one
  in Domovoi's own amber family (amber is core's alone), is rejected with
  a warning and the plugin gets :data:`DEFAULT_ACCENT`; a declared colour
  that had to be moved for its button use is kept, moved, with a warning
  that names the colour used. The loader logs the warnings
  (contracts.check_accent) and ``domovoi plugin pack`` / ``dev`` print them.

The default plugin accent is slate (hue 240, low chroma), the design
system's existing colour for "comes from outside Domovoi" (an externally
synced calendar's chip, web/static/styles.css ``.src-google``): the hue
opposite amber, muted so it never competes with the shell or reads as a
status (green / orange / red / grey all mean something here).

Pure functions, stdlib only: the web process imports this (plugin_host,
/api/capabilities) as well as the core.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

TEXT = 4.5      # WCAG AA, normal text: brand_ink, brand_fg
UI = 3.0        # WCAG 1.4.11, UI parts: the brand fill; a disabled button
DISABLED_OPACITY = 0.65   # .btn:disabled in web/static/styles.css

HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# The theme surfaces, as web/static/colors_and_type.css writes them
# (oklch L C H). domovoi/tests/test_plugin_accent.py holds the two in step.
SURFACES: dict[str, dict[str, tuple[float, float, float]]] = {
    "light": {
        "canvas": (0.985, 0.005, 80),
        "card": (1.000, 0.0, 0),
        "raised": (0.998, 0.003, 80),
        "sunken": (0.965, 0.005, 80),
    },
    "dark": {
        "canvas": (0.155, 0.005, 80),
        "card": (0.195, 0.006, 80),
        "raised": (0.225, 0.006, 80),
        "sunken": (0.135, 0.005, 80),
    },
}

# Wash alphas, as core's amber uses them (--brand-soft / -halo / -ring).
ALPHAS: dict[str, dict[str, float]] = {
    "light": {"soft": 0.12, "halo": 0.22, "ring": 0.40},
    "dark": {"soft": 0.14, "halo": 0.30, "ring": 0.50},
}

# Hover / press shades: lightness steps, as core's amber takes them.
SHADES: dict[str, tuple[float, float]] = {
    "light": (-0.04, -0.08),
    "dark": (+0.04, -0.06),
}

# Core's amber (hue 75): an accent of that family would read as core.
CORE_AMBER_HUE = 75.0
AMBER_HUE_WINDOW = 20.0     # degrees either side
AMBER_MIN_CHROMA = 0.08     # below this a warm colour reads as a neutral

# Slate: see the module docstring. Light and dark starting points; both go
# through the same checks as an author's colour.
DEFAULT_ACCENT_OKLCH = (0.50, 0.05, 240)
DEFAULT_ACCENT_DARK_OKLCH = (0.74, 0.05, 240)

TOKEN_KEYS = (
    "brand", "brand_hover", "brand_press", "brand_fg",
    "brand_soft", "brand_halo", "brand_ring", "brand_ink",
)


# ─── colour maths (OKLab, sRGB, WCAG relative luminance) ────────────────

RGB = tuple[float, float, float]        # gamma-encoded sRGB, 0..1


def _decode(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _encode(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def oklch_to_rgb(L: float, C: float, H: float) -> RGB:
    """oklch -> sRGB, gamut-clipped per channel like the browser draws it."""
    h = math.radians(H)
    a, b = C * math.cos(h), C * math.sin(h)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    lin = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
           -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
    return tuple(_encode(min(1.0, max(0.0, c))) for c in lin)  # type: ignore[return-value]


def rgb_to_oklch(rgb: RGB) -> tuple[float, float, float]:
    r, g, b = (_decode(c) for c in rgb)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l, m, s))
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    C = math.hypot(a, bb)
    H = math.degrees(math.atan2(bb, a)) % 360
    return L, C, H


def parse_hex(value: str) -> RGB:
    v = value[1:]
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    return tuple(int(v[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def to_hex(rgb: RGB, alpha: float | None = None) -> str:
    out = "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02x}" for c in rgb)
    if alpha is not None:
        out += f"{round(alpha * 255):02x}"
    return out


def luminance(rgb: RGB) -> float:
    r, g, b = (_decode(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def over(top: RGB, alpha: float, under: RGB) -> RGB:
    """``top`` at ``alpha`` composited on ``under`` (gamma space, as the
    browser blends)."""
    return tuple(t * alpha + u * (1 - alpha) for t, u in zip(top, under))  # type: ignore[return-value]


def ratio(a: RGB, b: RGB) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def surfaces(theme: str) -> dict[str, RGB]:
    return {k: oklch_to_rgb(*v) for k, v in SURFACES[theme].items()}


def min_ratio(colour: RGB, backs: list[RGB]) -> float:
    return min(ratio(colour, b) for b in backs)


# ─── deriving one theme's tokens ────────────────────────────────────────

_STEP = 0.005


def _rgb(L: float, C: float, H: float) -> RGB:
    """oklch -> sRGB as the dashboard will draw it: through the hex the
    manifest carries, so every check below sees the rounded colour."""
    return parse_hex(to_hex(oklch_to_rgb(L, C, H)))


def _alpha(a: float) -> float:
    return round(a * 255) / 255


def _clamp_l(L: float) -> float:
    return min(0.99, max(0.02, L))


def _move_until(L: float, C: float, H: float, direction: float, ok) -> float:
    """Step lightness in ``direction`` (+1 lighter, -1 darker) until
    ``ok(L)`` holds or the end of the range."""
    for _ in range(400):
        if ok(L):
            return L
        nxt = _clamp_l(L + direction * _STEP)
        if nxt == L:
            return L
        L = nxt
    return L


def _fg_candidates(H: float) -> dict[str, RGB]:
    # Near-white like --danger-fg, near-black tinted with the accent's hue
    # like --brand-fg (never pure #fff / #000, design README).
    return {"light": _rgb(0.99, 0.003, 80), "dark": _rgb(0.18, 0.03, H)}


def _shades(theme: str, L: float, C: float, H: float) -> tuple[RGB, RGB, RGB]:
    dh, dp = SHADES[theme]
    return (_rgb(L, C, H), _rgb(_clamp_l(L + dh), C, H), _rgb(_clamp_l(L + dp), C, H))


def disabled_ratio(fg: RGB, fill: RGB, backs: list[RGB]) -> float:
    """A disabled primary button: the whole button (fill and text) faded to
    DISABLED_OPACITY over the surface it sits on; the worst surface."""
    return min(ratio(over(fg, DISABLED_OPACITY, b), over(fill, DISABLED_OPACITY, b)) for b in backs)


def _best_fg(theme: str, L: float, C: float, H: float, backs: list[RGB]) -> tuple[RGB, float]:
    """The text colour for the fill, and how well it does as a share of the
    rules (>= 1.0 passes): TEXT on the fill, hover and press, UI on the
    disabled button."""
    triple = _shades(theme, L, C, H)
    best: tuple[RGB, float] | None = None
    for fg in _fg_candidates(H).values():
        score = min(min(ratio(fg, t) for t in triple) / TEXT,
                    disabled_ratio(fg, triple[0], backs) / UI)
        if best is None or score > best[1]:
            best = (fg, score)
    assert best is not None
    return best


@dataclass
class ThemeTokens:
    tokens: dict[str, str]
    fill_moved: bool          # the declared colour failed 3:1 as a button
    fill_ratio_before: float  # the declared colour's worst UI ratio


def derive_theme(theme: str, base: RGB) -> ThemeTokens:
    """Tokens for one theme from one starting colour (see the module
    docstring for the rules). Light moves darker, dark moves lighter:
    both are the directions that raise contrast on that theme's surfaces
    and, for the fill, keep one of the two text colours readable on it."""
    backs = [parse_hex(to_hex(c)) for c in surfaces(theme).values()]
    away = -1.0 if theme == "light" else 1.0
    L0, C, H = rgb_to_oklch(base)
    before = min_ratio(base, backs)

    # 1. The fill reads as a UI part (3:1) on every surface.
    L = _move_until(L0, C, H, away, lambda l: min_ratio(_rgb(l, C, H), backs) >= UI)
    # 2. ... and carries readable text (4.5:1) on it, its hover and its press.
    L = _move_until(L, C, H, away, lambda l: _best_fg(theme, l, C, H, backs)[1] >= 1.0)
    fill, hover, press = _shades(theme, L, C, H)
    fg, _ = _best_fg(theme, L, C, H, backs)

    alphas = ALPHAS[theme]
    soft_backs = [over(fill, _alpha(alphas["soft"]), b) for b in backs]
    # 3. Ink: the accent as text, 4.5:1 on every surface and on its own wash.
    Li = _move_until(L, C, H, away,
                     lambda l: min_ratio(_rgb(l, C, H), backs + soft_backs) >= TEXT)
    ink = _rgb(Li, C, H)

    tokens = {
        "brand": to_hex(fill),
        "brand_hover": to_hex(hover),
        "brand_press": to_hex(press),
        "brand_fg": to_hex(fg),
        "brand_soft": to_hex(fill, alphas["soft"]),
        "brand_halo": to_hex(fill, alphas["halo"]),
        "brand_ring": to_hex(fill, alphas["ring"]),
        "brand_ink": to_hex(ink),
    }
    return ThemeTokens(tokens=tokens, fill_moved=L != L0, fill_ratio_before=before)


# ─── the manifest keys ──────────────────────────────────────────────────


@dataclass
class Accent:
    source: str                         # "plugin" | "default"
    light: dict[str, str]
    dark: dict[str, str]
    declared: dict[str, str | None] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "light": self.light, "dark": self.dark}


def accent_problem(value: Any) -> str | None:
    """Why ``value`` can't be an accent colour, or None when it can."""
    if not isinstance(value, str):
        return "must be a hex colour string like \"#146859\""
    if not HEX_RE.match(value.strip()):
        return "is not a hex colour (#rgb or #rrggbb)"
    L, C, H = rgb_to_oklch(parse_hex(value.strip()))
    dh = abs((H - CORE_AMBER_HUE + 180) % 360 - 180)
    if C >= AMBER_MIN_CHROMA and dh <= AMBER_HUE_WINDOW:
        return ("is in Domovoi's own amber family (amber is the dashboard's "
                "colour; a plugin page has to read as a plugin)")
    return None


def _default() -> tuple[ThemeTokens, ThemeTokens]:
    return (derive_theme("light", oklch_to_rgb(*DEFAULT_ACCENT_OKLCH)),
            derive_theme("dark", oklch_to_rgb(*DEFAULT_ACCENT_DARK_OKLCH)))


_DEFAULT_CACHE: tuple[ThemeTokens, ThemeTokens] | None = None


def default_accent() -> Accent:
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        _DEFAULT_CACHE = _default()
    light, dark = _DEFAULT_CACHE
    return Accent(source="default", light=dict(light.tokens), dark=dict(dark.tokens))


def resolve_accent(accent: Any = None, accent_dark: Any = None) -> Accent:
    """The tokens a plugin page gets for ``[plugin].accent`` /
    ``accent_dark`` (raw manifest values; None when absent), plus the
    warnings to log. Never raises on a plugin's values."""
    warnings: list[str] = []
    if accent is None:
        out = default_accent()
        if accent_dark is not None:
            warnings.append(
                "plugin.accent_dark is used only beside plugin.accent; "
                "the page gets the default plugin accent"
            )
        out.warnings = warnings
        return out
    problem = accent_problem(accent)
    if problem:
        out = default_accent()
        out.warnings = [f"plugin.accent {accent!r} {problem}; the page gets the "
                        f"default plugin accent"]
        return out
    light_base = parse_hex(accent.strip())
    dark_value: str | None = None
    if accent_dark is not None:
        dproblem = accent_problem(accent_dark)
        if dproblem:
            warnings.append(f"plugin.accent_dark {accent_dark!r} {dproblem}; the dark "
                            f"theme derives its colour from plugin.accent")
        else:
            dark_value = accent_dark.strip()
    dark_base = parse_hex(dark_value) if dark_value else light_base

    light = derive_theme("light", light_base)
    dark = derive_theme("dark", dark_base)
    if light.fill_moved:
        warnings.append(_moved_warning("accent", accent, "light", light))
    if dark_value and dark.fill_moved:
        warnings.append(_moved_warning("accent_dark", accent_dark, "dark", dark))
    return Accent(source="plugin", light=light.tokens, dark=dark.tokens,
                  declared={"accent": accent, "accent_dark": accent_dark},
                  warnings=warnings)


def _moved_warning(key: str, value: str, theme: str, t: ThemeTokens) -> str:
    if t.fill_ratio_before < UI:
        why = (f"reads {t.fill_ratio_before:.1f}:1 on the {theme} theme's surfaces "
               f"(buttons need {UI:.0f}:1)")
    else:
        why = (f"can't carry readable button text ({TEXT}:1 on it and its hover "
               f"and press shades, {UI:.0f}:1 when disabled) in the {theme} theme")
    return f"plugin.{key} {value!r} {why}; the dashboard uses {t.tokens['brand']} there"


def manifest_accent(raw: dict[str, Any] | None) -> Accent:
    """:func:`resolve_accent` for a stored manifest (``plugins.manifest``
    JSONB, the raw TOML)."""
    plugin = (raw or {}).get("plugin") or {}
    if not isinstance(plugin, dict):
        plugin = {}
    return resolve_accent(plugin.get("accent"), plugin.get("accent_dark"))
