"""What the server knows about a document's CONTENT: almost nothing.

The server relays and stores Yjs updates without reading them. Three jobs
need to look inside, and they all go through the root types registered
here:

* **restore** (``restore_into``): put an old version back as a NEW change,
  so everyone connected sees it arrive like any other edit and it can be
  undone by restoring the version taken just before it. Each registered root
  of the live document is cleared and the old version's content copied in,
  type by type (XML elements with their attributes, rich text with its
  formatting runs, maps and arrays).
* **preview** (``preview_text``): plain text for the history panel when no
  rich preview is available.
* **emptiness** (``is_empty``).

A Yjs update does not say what TYPE a root is (pycrdt hands roots decoded
from an update back untyped), so the names come from the editors:

* ``default``: an ``XmlFragment``, where Tiptap's Collaboration extension
  keeps the document (builder A's editor, ``field`` left at its default);
* ``text``: a ``Text``, used by the plain text stub and any editor that
  binds a textarea.

The spreadsheet editor (builder D) registers its own with
:func:`register_root`.
"""

from __future__ import annotations

from typing import Any

from pycrdt import Array, Doc, Map, Text, XmlElement, XmlFragment, XmlText

ROOTS: dict[str, type] = {
    "default": XmlFragment,
    "text": Text,
}


def register_root(name: str, kind: type) -> None:
    """Declare a root type an editor uses, so restore and preview see it."""
    if kind not in (XmlFragment, Text, Map, Array):
        raise ValueError(f"unsupported root type {kind!r}")
    ROOTS[name] = kind


def _present_roots(doc: Doc) -> list[tuple[str, type]]:
    names = set(doc.keys())
    return [(name, kind) for name, kind in ROOTS.items() if name in names]


# ─── Copying ──────────────────────────────────────────────────────────────


def _copy_text_runs(src: Text | XmlText, dst: Text | XmlText) -> None:
    offset = 0
    for chunk, attrs in src.diff():
        if isinstance(chunk, str):
            dst.insert(offset, chunk, dict(attrs) if attrs else None)
            offset += len(chunk)
        else:
            dst.insert_embed(offset, _plain(chunk), dict(attrs) if attrs else None)
            offset += 1


def _plain(value: Any) -> Any:
    """A JSON-ish value out of whatever a map, array or embed held."""
    if isinstance(value, (Map, Array, Text)):
        return value.to_py()
    return value


# Nothing here recurses without a bound: guard.py keeps every stored
# document within MAX_TYPE_DEPTH, the XML walks below use explicit stacks,
# and the map/array copy counts its depth and stops past the same limit.
_MAX_DEPTH = 64


def _copy_xml_children(src: XmlFragment | XmlElement, dst: XmlFragment | XmlElement) -> None:
    stack = [(src, dst, 0)]
    while stack:
        s_node, d_node, depth = stack.pop()
        if depth > _MAX_DEPTH:
            raise ValueError("document nested too deep to copy")
        for child in s_node.children:
            if isinstance(child, XmlElement):
                el = d_node.children.append(XmlElement(child.tag or "paragraph"))
                for key, value in child.attributes:
                    el.attributes[key] = value
                stack.append((child, el, depth + 1))
            elif isinstance(child, XmlText):
                tx = d_node.children.append(XmlText())
                for key, value in child.attributes:
                    tx.attributes[key] = value
                _copy_text_runs(child, tx)


def _copy_map(src: Map, dst: Map) -> None:
    for key, value in src.items():
        dst[key] = _prelim(value)


def _copy_array(src: Array, dst: Array) -> None:
    for value in src:
        dst.append(_prelim(value))


def _prelim(value: Any, depth: int = 0) -> Any:
    """A fresh, not-yet-integrated copy of a nested shared value."""
    if depth > _MAX_DEPTH:
        raise ValueError("document nested too deep to copy")
    if isinstance(value, Map):
        return Map({k: _prelim(v, depth + 1) for k, v in value.items()})
    if isinstance(value, Array):
        return Array([_prelim(v, depth + 1) for v in value])
    if isinstance(value, Text):
        return Text(str(value))
    return value


def _clear(root: Any) -> None:
    if isinstance(root, XmlFragment):
        n = len(root.children)
        if n:
            del root.children[0:n]
    elif isinstance(root, Text):
        root.clear()
    elif isinstance(root, Map):
        for key in list(root.keys()):
            del root[key]
    elif isinstance(root, Array):
        if len(root):
            del root[0:len(root)]


def restore_into(live: Doc, version: Doc) -> None:
    """Make every registered root of ``live`` hold what ``version`` holds, as
    one transaction on ``live`` (the caller encodes it as an update)."""
    names = {n for n, _ in _present_roots(live)} | {n for n, _ in _present_roots(version)}
    with live.transaction(origin="restore"):
        for name in sorted(names):
            kind = ROOTS[name]
            dst = live.get(name, type=kind)
            _clear(dst)
            if name not in set(version.keys()):
                continue
            src = version.get(name, type=kind)
            if kind is XmlFragment:
                _copy_xml_children(src, dst)
            elif kind is Text:
                _copy_text_runs(src, dst)
            elif kind is Map:
                _copy_map(src, dst)
            elif kind is Array:
                _copy_array(src, dst)


# ─── Reading ──────────────────────────────────────────────────────────────


def _xml_text(node: Any, out: list[str]) -> None:
    """Plain text of an XML tree, depth first, without recursion: every
    element that holds text ends a line (ProseMirror keeps marks on text,
    so elements are blocks), and so do hard breaks and rules."""
    stack: list[tuple[str, Any, int]] = [("children", node, 0)]
    while stack:
        op, item, depth = stack.pop()
        if op == "end":
            began, is_break = item
            if is_break or (len(out) > began and not out[-1].endswith("\n")):
                out.append("\n")
        elif op == "text":
            out.append("".join(c for c, _ in item.diff() if isinstance(c, str)))
        elif op == "elem":
            stack.append(("end", (len(out), item.tag in ("hardBreak", "horizontalRule")), depth))
            stack.append(("children", item, depth))
        elif depth <= _MAX_DEPTH:
            for child in reversed(list(item.children)):
                if isinstance(child, XmlText):
                    stack.append(("text", child, depth + 1))
                elif isinstance(child, XmlElement):
                    stack.append(("elem", child, depth + 1))


def preview_text(doc: Doc, limit: int = 20000) -> str:
    parts: list[str] = []
    for name, kind in _present_roots(doc):
        root = doc.get(name, type=kind)
        if kind is XmlFragment:
            buf: list[str] = []
            _xml_text(root, buf)
            parts.append("".join(buf))
        elif kind is Text:
            parts.append(str(root))
    text = "\n".join(p.strip("\n") for p in parts if p.strip())
    return text[:limit]


def is_empty(doc: Doc) -> bool:
    return doc.get_state() in (b"", b"\x00")


def doc_from_update(update: bytes | None) -> Doc:
    doc = Doc()
    if update:
        doc.apply_update(update)
    return doc
