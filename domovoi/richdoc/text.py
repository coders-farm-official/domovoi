"""A formatted document as plain, markdown-shaped text, for the readers.

Chat documents (:mod:`domovoi.doc_extract`), read-aloud
(:mod:`domovoi.voice_docs.reading`) and printing all understand markdown
text already, so a formatted document is handed to them as that:
headings as ``#`` lines, list items as ``-`` / ``1.`` lines, checklist
items as ``- [ ]`` / ``- [x]``, quotes as ``>``, code in fences, tables as
``|`` rows. Inline formatting is dropped (it is not something a reader
reads out), pictures become their caption or alt text, and nothing from a
``data:`` URI ever reaches the text.

It parses with the standard library and never runs, fetches or follows
anything in the file. The input need not be sanitised first: scripts,
styles and the like are skipped with their content.
"""

from __future__ import annotations

from html.parser import HTMLParser

from domovoi.richdoc.fileformat import document_body
from domovoi.richdoc.sanitize import DROP_WITH_CONTENT, VOID_ANY as VOID

_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_MAX_DEPTH = 200


class _Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict[str, str]):
        self.tag = tag
        self.attrs = attrs
        self.children: list[object] = []


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self.stack = [self.root]
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.skip or tag in DROP_WITH_CONTENT:
            if tag not in VOID:
                self.skip += 1
            return
        node = _Node(tag, {k.lower(): (v or "") for k, v in attrs if k})
        self.stack[-1].children.append(node)
        if tag not in VOID and len(self.stack) < _MAX_DEPTH:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self.skip or tag in DROP_WITH_CONTENT:
            return
        self.stack[-1].children.append(_Node(tag, {k.lower(): (v or "") for k, v in attrs if k}))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip:
            if tag in DROP_WITH_CONTENT:
                self.skip -= 1
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not self.skip and data:
            self.stack[-1].children.append(data)


def _inline(node: object, raw: bool = False) -> str:
    """The text of an inline run. Source line breaks are spaces (only a
    ``<br>`` breaks a line), except inside ``<pre>`` (``raw``)."""
    if isinstance(node, str):
        return node if raw else node.replace("\r", " ").replace("\n", " ")
    n: _Node = node  # type: ignore[assignment]
    if n.tag == "br":
        return "\n"
    if n.tag == "img":
        alt = n.attrs.get("alt", "").strip()
        return f"[Picture: {alt}]" if alt else ""
    if n.tag == "input":
        return ""
    return "".join(_inline(c, raw) for c in n.children)


def _squash(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines).strip()


_BLOCKS = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote",
    "pre", "table", "figure", "figcaption", "hr", "section", "article", "main",
    "header", "footer", "aside", "nav", "dl", "dt", "dd", "address", "tr",
}


class _Writer:
    def __init__(self) -> None:
        self.blocks: list[str] = []

    def emit(self, text: str) -> None:
        text = text.rstrip()
        if text.strip():
            self.blocks.append(text)

    def children(self, node: _Node, prefix: str = "") -> None:
        run: list[object] = []

        def flush() -> None:
            if run:
                t = _squash("".join(_inline(c) for c in run))
                if t:
                    self.emit(prefix + t.replace("\n", "\n" + prefix))
                run.clear()

        for child in node.children:
            if isinstance(child, _Node) and child.tag in _BLOCKS:
                flush()
                self.block(child, prefix)
            else:
                run.append(child)
        flush()

    def block(self, n: _Node, prefix: str) -> None:
        tag = n.tag
        if tag in _HEADINGS:
            t = _squash(_inline(n)).replace("\n", " ")
            if t:
                self.emit(f"{prefix}{'#' * _HEADINGS[tag]} {t}")
        elif tag in ("ul", "ol"):
            self.list_(n, prefix, 0)
        elif tag == "blockquote":
            self.children(n, prefix + "> ")
        elif tag == "pre":
            code = "".join(_inline(c, raw=True) for c in n.children).strip("\n")
            if code.strip():
                self.emit(f"{prefix}```\n{code}\n{prefix}```")
        elif tag == "table":
            self.table(n, prefix)
        elif tag == "hr":
            return
        else:  # p, div, figure, figcaption, li outside a list, sections
            self.children(n, prefix)

    def list_(self, n: _Node, prefix: str, depth: int) -> None:
        lines: list[str] = []
        number = 1
        try:
            number = int(n.attrs.get("start", "1") or 1)
        except ValueError:
            pass
        task = n.attrs.get("data-type") == "taskList"
        for li in n.children:
            if not isinstance(li, _Node) or li.tag != "li":
                continue
            if task or li.attrs.get("data-type") == "taskItem":
                mark = "- [x] " if li.attrs.get("data-checked") == "true" else "- [ ] "
            elif n.tag == "ol":
                mark = f"{number}. "
                number += 1
            else:
                mark = "- "
            own: list[object] = []
            nested: list[_Node] = []
            self._split_item(li, own, nested)
            text = _squash("".join(_inline(c) for c in own)).replace("\n", " ")
            lines.append("  " * depth + mark + text)
            for sub in nested:
                sub_writer = _Writer()
                sub_writer.list_(sub, "", depth + 1)
                lines.extend(sub_writer.blocks)
        if lines:
            self.emit("\n".join(prefix + line for line in "\n".join(lines).split("\n")))

    def _split_item(self, node: _Node, own: list[object], nested: list[_Node]) -> None:
        for c in node.children:
            if isinstance(c, _Node) and c.tag in ("ul", "ol"):
                nested.append(c)
            elif isinstance(c, _Node) and c.tag in ("p", "div", "label"):
                if own and c.tag != "label":
                    own.append(" ")
                self._split_item(c, own, nested)
            else:
                own.append(c)

    def table(self, n: _Node, prefix: str) -> None:
        rows: list[list[str]] = []

        def walk(x: _Node) -> None:
            for c in x.children:
                if not isinstance(c, _Node):
                    continue
                if c.tag == "tr":
                    cells = [
                        _squash(_inline(td)).replace("\n", " ").replace("|", "/")
                        for td in c.children if isinstance(td, _Node) and td.tag in ("td", "th")
                    ]
                    if any(cells):
                        rows.append(cells)
                elif c.tag in ("thead", "tbody", "tfoot"):
                    walk(c)

        walk(n)
        if not rows:
            return
        width = max(len(r) for r in rows)
        out = []
        for i, r in enumerate(rows):
            r = r + [""] * (width - len(r))
            out.append(prefix + "| " + " | ".join(r) + " |")
            if i == 0:
                out.append(prefix + "|" + "---|" * width)
        self.emit("\n".join(out))


def html_to_markdown(markup: str) -> str:
    """``markup`` (a whole file or just its content) as markdown text."""
    builder = _TreeBuilder()
    builder.feed(document_body(markup or ""))
    builder.close()
    writer = _Writer()
    writer.children(builder.root)
    return "\n\n".join(writer.blocks) + ("\n" if writer.blocks else "")
