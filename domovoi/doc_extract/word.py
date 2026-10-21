"""Word (.docx) text from the paragraph XML itself.

D0 found python-docx's ``paragraph.text`` drops tracked insertions
(``w:ins``): 99.74% on the lease, against 100% read from the XML. So the
text comes from ``word/document.xml`` read here, through the zip and XML
guards (:mod:`domovoi.doc_extract.safe`):

* every ``w:t`` in a paragraph in document order, inserted runs included;
  ``w:delText`` (deleted text) and ``w:instrText`` (field codes) are not
  ``w:t``, so the accepted version is what is read; a field shows its
  last value; ``w:tab`` is a tab, ``w:br``/``w:cr`` a line break;
* a paragraph whose style is "Title" or "Heading N" (named in
  ``word/styles.xml``), or that has an outline level, is a heading; one
  with numbering (``w:numPr``) is a list item;
* tables (``w:tbl``) become markdown rows, cell by cell; content controls
  (``w:sdt``) are read through;
* text boxes are read where they sit in the XML;
* headers and footers come from the parts the document's sections name
  (``w:headerReference``/``w:footerReference``), kept once; footnotes and
  endnotes from their own parts.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from domovoi.doc_extract.safe import Refused, SafeZip
from domovoi.doc_extract.structure import Block, blocks_to_parts, md_table
from domovoi.doc_qa.text import clean as normalize

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
_HEADING = re.compile(r"^heading\s*(\d)$", re.IGNORECASE)


def _styles(z: SafeZip) -> dict[str, str]:
    """styleId -> style name."""
    if not z.has("word/styles.xml"):
        return {}
    out = {}
    for st in z.xml("word/styles.xml").iter(W + "style"):
        sid = st.get(W + "styleId")
        name = st.find(W + "name")
        if sid:
            out[sid] = (name.get(W + "val") if name is not None else None) or sid
    return out


def _iter(el: ET.Element):
    """``el`` and everything under it, in order, less the fallback copy of
    a drawing (``mc:Fallback``): a text box is written twice, once for new
    Word and once for old, and is read once."""
    yield el
    for ch in el:
        if ch.tag == MC_FALLBACK:
            continue
        yield from _iter(ch)


def para_text(p: ET.Element) -> str:
    out = []
    for el in _iter(p):
        tag = el.tag
        if tag == W + "t":
            out.append(el.text or "")
        elif tag == W + "tab":
            out.append("\t")
        elif tag in (W + "br", W + "cr"):
            out.append("\n")
    return normalize("".join(out))


def _para_block(p: ET.Element, styles: dict[str, str]) -> Block:
    text = para_text(p)
    ppr = p.find(W + "pPr")
    style = ""
    level = None
    numbered = False
    if ppr is not None:
        ps = ppr.find(W + "pStyle")
        if ps is not None:
            style = styles.get(ps.get(W + "val") or "", ps.get(W + "val") or "")
        ol = ppr.find(W + "outlineLvl")
        if ol is not None and (ol.get(W + "val") or "").isdigit():
            level = int(ol.get(W + "val")) + 2
        numbered = ppr.find(W + "numPr") is not None
    if style.lower() == "title":
        return Block("h", text, 1)
    m = _HEADING.match(style.strip())
    if m:
        return Block("h", text, int(m.group(1)) + 1)
    if level is not None and level <= 7:
        return Block("h", text, level)
    if numbered or style.lower().startswith("list"):
        return Block("p", "- " + text.strip())
    return Block("p", text)


def _table(tbl: ET.Element) -> str:
    rows = []
    for tr in tbl.findall(W + "tr"):
        cells = []
        for tc in tr.findall(W + "tc"):
            cells.append(" ".join(para_text(p).strip() for p in tc.iter(W + "p") if para_text(p).strip()))
        rows.append(cells)
    return md_table(rows)


def _walk(el: ET.Element, styles: dict[str, str], out: list[Block]) -> None:
    for ch in el:
        tag = ch.tag
        if tag == W + "p":
            out.append(_para_block(ch, styles))
        elif tag == W + "tbl":
            t = _table(ch)
            if t:
                out.append(Block("table", t))
        elif tag == W + "sdt":
            content = ch.find(W + "sdtContent")
            if content is not None:
                _walk(content, styles, out)
        elif tag in (W + "customXml", W + "ins", W + "smartTag"):
            _walk(ch, styles, out)


def _rels(z: SafeZip) -> dict[str, str]:
    if not z.has("word/_rels/document.xml.rels"):
        return {}
    out = {}
    for rel in z.xml("word/_rels/document.xml.rels").iter(PKG_REL + "Relationship"):
        rid, target = rel.get("Id"), rel.get("Target") or ""
        if rid and not target.startswith(("/", "http:", "https:")) and rel.get("TargetMode") != "External":
            out[rid] = "word/" + target.lstrip("./")
    return out


def _part_text(z: SafeZip, name: str) -> str:
    if not z.has(name):
        return ""
    root = z.xml(name)
    return "\n".join(t for t in (para_text(p).strip() for p in root.iter(W + "p")) if t)


def _notes(z: SafeZip, name: str, tag: str) -> list[str]:
    if not z.has(name):
        return []
    out = []
    for fn in z.xml(name).iter(W + tag):
        if fn.get(W + "type") in ("separator", "continuationSeparator", "continuationNotice"):
            continue
        try:
            if int(fn.get(W + "id") or "0") <= 0:
                continue
        except ValueError:
            continue
        t = " ".join(" ".join(para_text(p).split()) for p in fn.iter(W + "p")).strip()
        if t:
            out.append(t)
    return out


def extract(path: Path) -> dict[str, Any]:
    with SafeZip(path, "Word document") as z:
        if not z.has("word/document.xml"):
            raise Refused("That isn't a Word document Domovoi can read (no document part inside).")
        styles = _styles(z)
        root = z.xml("word/document.xml")
        body = root.find(W + "body")
        blocks: list[Block] = []
        if body is not None:
            _walk(body, styles, blocks)
        rels = _rels(z)
        headers: list[str] = []
        footers: list[str] = []
        for ref_tag, dst in ((W + "headerReference", headers), (W + "footerReference", footers)):
            for ref in root.iter(ref_tag):
                target = rels.get(ref.get(R + "id") or "")
                if target:
                    t = _part_text(z, target)
                    if t and t not in dst:
                        dst.append(t)
        notes = _notes(z, "word/footnotes.xml", "footnote") + _notes(z, "word/endnotes.xml", "endnote")
    parts = blocks_to_parts(blocks, headers=headers, footers=footers, footnotes=notes)
    return {"parts": parts}
