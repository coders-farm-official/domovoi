"""The allowlist for formatted documents (``.html``), server side.

A formatted document is somebody else's bytes until proven otherwise: a
file downloaded from the web and dropped into Documents, an export from
another program, or a save from a client that is not the dashboard. The
editor's own browser-side checks (web/static/sanitize_html.js, and
ProseMirror's schema) are a convenience, not the guard: every load and
every save goes through :func:`sanitize_html` here.

The approach is rebuild, not filter. The input is read by a linear
tokenizer of our own (:mod:`.tokens`, not ``html.parser``, which is slow
and can raise on hostile input), and the output is written from scratch:

* an element is written only when its name is on :data:`ALLOWED`, and
  only with attributes from that element's list, each value checked
  (an attribute that fails its check is left out, never "fixed");
* ``style`` keeps only the properties the editor itself writes for that
  element (colour, highlight, font, size, alignment, widths), each value
  matched against a strict pattern: no ``url()``, no ``expression()``, no
  escapes, no comments;
* links keep ``http``, ``https``, ``mailto`` and ``tel`` (and relative
  ones); pictures keep only ``data:image/...;base64`` (PNG, JPEG, GIF,
  WebP, AVIF, BMP; never SVG), so a document never fetches anything from
  anywhere when it is opened;
* scripts, styles, frames, objects, SVG and MathML go with their
  CONTENT; any other unknown element is dropped and its text kept;
  comments, doctypes and processing instructions are dropped;
* text is re-escaped, and the output is always balanced: every element
  written is closed.
"""

from __future__ import annotations

import html
import re

from domovoi.richdoc.tokens import tokenize

# ─── What survives ──────────────────────────────────────────────────
BLOCK_TAGS = frozenset({
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "ul", "ol",
    "li", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "colgroup",
    "figure", "figcaption", "hr", "div",
})
INLINE_TAGS = frozenset({
    "br", "strong", "b", "em", "i", "u", "s", "del", "strike", "mark", "span",
    "code", "a", "img", "sub", "sup", "label", "input", "col",
})
ALLOWED = BLOCK_TAGS | INLINE_TAGS

VOID = frozenset({"br", "hr", "img", "input", "col"})
# Every element HTML gives no end tag, allowed or not: one of these never
# opens a "skip until its end tag" (an <embed> would otherwise swallow the
# rest of the document waiting for an </embed> that never comes).
VOID_ANY = VOID | frozenset({
    "embed", "source", "track", "param", "area", "base", "link", "meta", "wbr",
    "keygen", "frame",
})

# Raw-text and active elements: the tag AND everything inside it go.
DROP_WITH_CONTENT = frozenset({
    "script", "style", "iframe", "frame", "frameset", "object", "embed",
    "applet", "template", "textarea", "title", "noscript", "noembed",
    "noframes", "xmp", "math", "svg", "head", "select", "option", "canvas",
    "audio", "video", "picture", "source", "track", "plaintext", "listing",
})

# Old spellings, written as what the editor itself would write.
RENAME = {"b": "strong", "i": "em", "del": "s", "strike": "s"}

GLOBAL_ATTRS = frozenset({"dir"})
PER_ELEMENT_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "target", "title"}),
    "img": frozenset({"src", "alt", "title", "width", "height"}),
    "figure": frozenset({"data-align", "data-width"}),
    "mark": frozenset({"data-color"}),
    "ul": frozenset({"data-type"}),
    "li": frozenset({"data-type", "data-checked"}),
    "input": frozenset({"type", "checked", "disabled"}),
    "ol": frozenset({"start", "type"}),
    "td": frozenset({"colspan", "rowspan", "colwidth"}),
    "th": frozenset({"colspan", "rowspan", "colwidth"}),
    "code": frozenset({"class"}),
}

# The style properties the editor writes, per element.
_ALIGN_ONLY = frozenset({"text-align"})
STYLE_PROPS: dict[str, frozenset[str]] = {
    **{t: _ALIGN_ONLY for t in ("p", "h1", "h2", "h3", "h4", "h5", "h6")},
    "span": frozenset({"color", "background-color", "font-family", "font-size"}),
    "mark": frozenset({"background-color", "color"}),
    "table": frozenset({"width", "min-width"}),
    "col": frozenset({"width", "min-width"}),
    "td": frozenset({"background-color"}),
    "th": frozenset({"background-color"}),
    "figure": frozenset({"width"}),
}

_COLOUR = re.compile(
    r"^(#[0-9a-f]{3,8}"
    r"|rgba?\(\s*\d{1,3}%?\s*,\s*\d{1,3}%?\s*,\s*\d{1,3}%?\s*(,\s*(0|1|0?\.\d+|\d{1,3}%)\s*)?\)"
    r"|[a-z]{3,24})$",
    re.I,
)
_FONT_FAMILY = re.compile(r"^[A-Za-z0-9 ,'\"_-]{1,200}$")
_FONT_SIZE = re.compile(r"^\d{1,3}(\.\d{1,2})?(px|pt|em|rem|%)$", re.I)
_LENGTH = re.compile(r"^\d{1,5}(\.\d{1,3})?(px|%)$", re.I)
_ALIGN = frozenset({"left", "center", "right", "justify", "start", "end"})
_CSS_WORDS_REFUSED = re.compile(r"(url|expression|image|var|attr|calc|env|import)\s*\(|\\|/\*|[<>@{}]", re.I)

_VALUE_CHECK = {
    "color": _COLOUR.match,
    "background-color": _COLOUR.match,
    "font-family": _FONT_FAMILY.match,
    "font-size": _FONT_SIZE.match,
    "width": _LENGTH.match,
    "min-width": _LENGTH.match,
    "text-align": lambda v: v.lower() in _ALIGN,
}

_DATA_IMAGE = re.compile(
    r"^data:image/(png|jpeg|jpg|gif|webp|avif|bmp);base64,[a-z0-9+/=\s]+$", re.I
)
_SAFE_SCHEME = re.compile(r"^(https?|mailto|tel):", re.I)
_HAS_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
_URL_JUNK = re.compile(r"[\x00-\x20\x7f-\xa0]")

_DIGITS = re.compile(r"^\d{1,5}$")
_COLWIDTH = re.compile(r"^\d{1,5}(,\d{1,5}){0,50}$")
_LANGUAGE_CLASS = re.compile(r"^language-[A-Za-z0-9_+-]{1,40}$")

#: Deeper than this, start tags are dropped (their text stays). Nothing
#: the editor makes comes close; a hostile file nests to blow the stack
#: of whatever reads the output next.
MAX_DEPTH = 120


def clean_style(tag: str, value: str) -> str | None:
    """The ``style`` attribute ``tag`` may keep, rebuilt from ``value``:
    only allowed properties, each value checked. None when nothing is left."""
    allowed = STYLE_PROPS.get(tag)
    if not allowed or not value:
        return None
    if _CSS_WORDS_REFUSED.search(value):
        # Anything that could reach outside (url(), image-set()), run code
        # in old engines (expression()) or hide a property from this parser
        # (escapes, comments, at-rules) refuses the WHOLE attribute.
        return None
    kept: list[str] = []
    seen: set[str] = set()
    for decl in value.split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop = prop.strip().lower()
        val = val.strip()
        if val.lower().endswith("!important"):
            val = val[: -len("!important")].strip()
        if prop not in allowed or prop in seen or not val:
            continue
        check = _VALUE_CHECK.get(prop)
        if check is None or not check(val):
            continue
        seen.add(prop)
        kept.append(f"{prop}: {val}")
    return "; ".join(kept) if kept else None


def clean_url(value: str, attr: str) -> str | None:
    """A link or picture address that may stay, or None."""
    v = _URL_JUNK.sub("", value or "")
    if not v:
        return None
    if attr == "src":
        # Pictures are embedded or they are not there: a document opened on
        # this server must never fetch anything from anywhere.
        return v if _DATA_IMAGE.match(v) else None
    if not _HAS_SCHEME.match(v):
        return v  # relative, #anchor, ?query: no scheme to abuse
    return v if _SAFE_SCHEME.match(v) else None


def _clean_attr(tag: str, name: str, value: str) -> str | None:
    """The value ``name`` keeps on ``tag`` ("" for a bare attribute), or None."""
    if name in ("href", "src"):
        return clean_url(value, name)
    if name == "target":
        return "_blank" if value.strip().lower() == "_blank" else None
    if name in ("title", "alt"):
        return value[:500]
    if name == "dir":
        return value.lower() if value.lower() in ("ltr", "rtl", "auto") else None
    if name in ("width", "height", "colspan", "rowspan", "start"):
        return value if _DIGITS.match(value.strip()) else None
    if name == "colwidth":
        return value if _COLWIDTH.match(value.strip()) else None
    if name == "type":
        if tag == "input":
            return "checkbox" if value.strip().lower() == "checkbox" else None
        return value if value in ("1", "a", "A", "i", "I") else None
    if name in ("checked", "disabled"):
        return ""
    if name == "data-type":
        want = {"ul": "taskList", "li": "taskItem"}.get(tag)
        return want if value == want else None
    if name == "data-checked":
        return value if value in ("true", "false") else None
    if name == "data-align":
        return value if value in ("left", "center", "right") else None
    if name == "data-width":
        return value if _DIGITS.match(value) and 1 <= int(value) <= 100 else None
    if name == "data-color":
        return value if _COLOUR.match(value) else None
    if name == "class":
        return value if tag == "code" and _LANGUAGE_CLASS.match(value) else None
    return None


def _escape_attr(value: str) -> str:
    return html.escape(value, quote=True)


# Opening one of these ends an open paragraph (HTML's own implied end
# tags, so `<p>a<ul>` isn't written as a list inside a paragraph).
_CLOSES_P = BLOCK_TAGS - {"li", "td", "th", "tr", "thead", "tbody", "tfoot", "figcaption", "colgroup"}
# An element that ends an open sibling of the listed kinds.
_IMPLIED_END = {
    "li": ("li",),
    "td": ("td", "th"),
    "th": ("td", "th"),
    "tr": ("tr", "td", "th"),
    "thead": ("thead", "tbody", "tfoot", "tr", "td", "th"),
    "tbody": ("thead", "tbody", "tfoot", "tr", "td", "th"),
    "tfoot": ("thead", "tbody", "tfoot", "tr", "td", "th"),
}
# Where an implied end stops looking (a list inside a list item is its own).
_SCOPE = {"li": ("ul", "ol"), "td": ("table",), "th": ("table",), "tr": ("table",),
          "thead": ("table",), "tbody": ("table",), "tfoot": ("table",)}


class _Rebuilder:
    """Rebuilds the allowed markup from :func:`.tokens.tokenize`'s stream."""

    def __init__(self) -> None:
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip: list[str] = []      # open DROP_WITH_CONTENT elements
        self.dropped_depth = 0         # start tags past MAX_DEPTH

    # ── tags ──
    def _close_to(self, name: str) -> None:
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == name:
                return

    def _implied_ends(self, tag: str) -> None:
        if tag in _CLOSES_P and "p" in self.stack:
            # only the innermost open p, and only if nothing block-level
            # sits between it and the top
            idx = len(self.stack) - 1 - self.stack[::-1].index("p")
            if all(t not in BLOCK_TAGS for t in self.stack[idx + 1:]):
                self._close_to("p")
        ends = _IMPLIED_END.get(tag)
        if not ends:
            return
        scope = _SCOPE.get(tag, ())
        for i in range(len(self.stack) - 1, -1, -1):
            t = self.stack[i]
            if t in scope:
                return
            if t in ends:
                self._close_to(t)
                return

    def start(self, tag: str, attrs, self_closing: bool) -> None:
        if self.skip:
            if tag in DROP_WITH_CONTENT and not self_closing and tag not in VOID_ANY:
                self.skip.append(tag)
            return
        if tag in DROP_WITH_CONTENT:
            if not self_closing and tag not in VOID_ANY:
                self.skip.append(tag)
            return
        tag = RENAME.get(tag, tag)
        if tag not in ALLOWED:
            return
        if len(self.stack) >= MAX_DEPTH:
            if tag not in VOID:
                self.dropped_depth += 1
            return
        self._implied_ends(tag)
        parts = [tag]
        if tag == "a":
            parts.append('rel="noopener noreferrer nofollow"')
        seen: set[str] = set()
        allowed = PER_ELEMENT_ATTRS.get(tag, frozenset())
        for name, value in attrs:
            if name in seen or name.startswith("on"):
                continue
            if name == "style":
                kept = clean_style(tag, value)
            elif name in GLOBAL_ATTRS or name in allowed:
                kept = _clean_attr(tag, name, value)
            else:
                continue
            if kept is None:
                continue
            seen.add(name)
            parts.append(name if kept == "" else f'{name}="{_escape_attr(kept)}"')
        if tag == "img" and "src" not in seen:
            return  # an outside or unsafe picture: nothing to show
        if tag == "input" and "type" not in seen:
            return  # only checkboxes (task lists)
        self.out.append(f"<{' '.join(parts)}>")
        if tag not in VOID:
            self.stack.append(tag)

    def end(self, tag: str) -> None:
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            return
        tag = RENAME.get(tag, tag)
        if tag not in ALLOWED or tag in VOID:
            return
        if tag in self.stack:
            self._close_to(tag)
        elif self.dropped_depth:
            self.dropped_depth -= 1

    def text(self, data: str) -> None:
        if self.skip or not data:
            return
        self.out.append(html.escape(data, quote=False))

    def finish(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_html(markup: str) -> str:
    """``markup`` rebuilt from the allowlist (module docstring). Always a
    string, always balanced, never containing anything that runs. Linear in
    the input (:mod:`.tokens`); anything unexpected while reading ends in
    the input's text, escaped, rather than an error."""
    r = _Rebuilder()
    try:
        for t in tokenize(markup):
            if t.kind == "start":
                r.start(t.name, t.attrs, t.self_closing)
            elif t.kind == "end":
                r.end(t.name)
            else:
                r.text(t.data)
        return r.finish()
    except RecursionError:  # pragma: no cover - nothing here recurses
        raise
    except Exception:  # noqa: BLE001 - fail closed: text, never markup
        return f"<p>{html.escape(str(markup or ''), quote=False)}</p>"
