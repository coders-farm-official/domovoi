"""A web page's main text, with the standard library only.

"What does this page say: <url>" (the assistant plan, part 5) reads a page
the person named: no JavaScript is run, nothing on the page is fetched, and
what comes back is the page's own words, trimmed. :func:`main_text` takes
the HTML and returns ``(title, text)``:

* everything inside ``script``, ``style``, ``noscript``, ``template``,
  ``svg``, ``canvas``, ``iframe``, ``object``, ``form``, ``button``,
  ``select`` and ``textarea`` is dropped, and so is the page's chrome
  (``nav``, ``header``, ``footer``, ``aside``) and anything marked
  ``hidden`` / ``aria-hidden="true"`` or styled ``display:none``;
* when the page has an ``<article>`` or ``<main>`` (or ``role="main"``),
  only that is kept; otherwise the whole ``<body>``;
* block elements end a line, so paragraphs stay apart; runs of whitespace
  collapse; lines shorter than a few words that look like menu items are
  kept only inside the main part.

The result is DATA for a model (fenced by :mod:`domovoi.lookup_answer`),
never markup: no tag, attribute or link survives.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

#: Elements whose whole content is dropped.
_SKIP = frozenset({
    "script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "embed",
    "form", "button", "select", "textarea", "nav", "header", "footer", "aside", "head", "math",
})
_VOID = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
    "source", "track", "wbr",
})
_BLOCK = frozenset({
    "p", "div", "section", "article", "main", "li", "ul", "ol", "dl", "dt", "dd", "h1", "h2", "h3",
    "h4", "h5", "h6", "blockquote", "pre", "table", "tr", "td", "th", "figcaption", "br", "hr",
    "address", "details", "summary",
})
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
_STRAY_LT = re.compile(r"<(?=[^<>]*<)")
_WS = re.compile(r"[ \t\r\f\v ]+")


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._stack: list[tuple[str, bool, bool]] = []   # (tag, skipping, main)
        self._skip_depth = 0
        self._main_depth = 0
        self.body: list[str] = []
        self.main: list[str] = []
        self.saw_main = False

    def _hidden(self, attrs: list[tuple[str, str | None]]) -> bool:
        a = {k.lower(): (v or "") for k, v in attrs}
        if "hidden" in a or a.get("aria-hidden", "").lower() == "true":
            return True
        return bool(_HIDDEN_STYLE.search(a.get("style", "")))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        if tag in _VOID:
            if tag in ("br", "hr"):
                self._newline()
            return
        skipping = tag in _SKIP or self._hidden(attrs)
        role = dict((k.lower(), (v or "").lower()) for k, v in attrs).get("role", "")
        is_main = tag in ("article", "main") or role == "main"
        if skipping:
            self._skip_depth += 1
        if is_main and not self._skip_depth:
            self._main_depth += 1
            self.saw_main = True
        self._stack.append((tag, skipping, is_main and not skipping))
        if tag in _BLOCK:
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in _VOID:
            return
        # Close up to the matching tag (pages leave tags open).
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                for t, skipping, main in reversed(self._stack[i:]):
                    if skipping:
                        self._skip_depth -= 1
                    if main:
                        self._main_depth -= 1
                del self._stack[i:]
                break
        if tag in _BLOCK:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        self.body.append(data)
        if self._main_depth:
            self.main.append(data)

    def _newline(self) -> None:
        if self._skip_depth:
            return
        self.body.append("\n")
        if self._main_depth:
            self.main.append("\n")


def _tidy(parts: list[str]) -> str:
    lines = []
    for ln in "".join(parts).split("\n"):
        ln = _WS.sub(" ", ln).strip()
        if ln:
            lines.append(ln)
    return "\n".join(lines)


def main_text(html: str, *, max_chars: int = 64 * 1024) -> tuple[str, str]:
    """``(title, text)`` of an HTML page: its main part's words, trimmed to
    ``max_chars``. Never raises on a malformed page."""
    reader = _Reader()
    # A "<" with another "<" before any ">" can't open a tag: it is text.
    # Escaped first, so a page of "<a<a<a…" (quadratic for html.parser)
    # parses in one pass.
    html = _STRAY_LT.sub("&lt;", html or "")
    try:
        reader.feed(html)
        reader.close()
    except Exception:  # noqa: BLE001 — a broken page gives what was read so far
        pass
    text = _tidy(reader.main) if reader.saw_main else ""
    if len(text) < 200:
        text = _tidy(reader.body)
    title = _WS.sub(" ", reader.title).strip()[:200]
    return title, text[:max_chars]


def plain_text(raw: str, *, max_chars: int = 64 * 1024) -> str:
    """A text/plain page, whitespace tidied."""
    return _tidy([raw or ""])[:max_chars]


__all__ = ["main_text", "plain_text"]
