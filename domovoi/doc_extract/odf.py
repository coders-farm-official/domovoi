"""OpenDocument text (.odt): ``content.xml`` and ``styles.xml`` from the zip.

Read with the standard library behind the zip and XML guards (D0: 100%
on the lease): ``text:h`` headings by outline level, ``text:p``
paragraphs, ``text:list`` items, ``table:table`` as markdown rows,
``text:s`` (runs of spaces), ``text:tab``, ``text:line-break``, and
``text:note`` (footnotes) gathered apart. Tracked changes: the deleted
text an .odt keeps lives in ``text:tracked-changes``, which isn't read,
so the accepted version is. Headers and footers live in ``styles.xml``'s
master pages and are kept once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from domovoi.doc_extract.safe import Refused, SafeZip
from domovoi.doc_extract.structure import Block, blocks_to_parts, md_table
from domovoi.doc_qa.text import clean as normalize

T = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
TB = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
ST = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"

_SKIP = {T + "tracked-changes", T + "note-citation", OFFICE + "annotation", T + "changed-region",
         T + "deletion"}


def _inline(el: ET.Element, notes: list[str]) -> str:
    out = [el.text or ""]
    for ch in el:
        tag = ch.tag
        if tag in _SKIP:
            pass
        elif tag == T + "s":
            try:
                n = int(ch.get(T + "c", "1"))
            except ValueError:
                n = 1
            out.append(" " * max(1, min(n, 1000)))
        elif tag == T + "tab":
            out.append("\t")
        elif tag == T + "line-break":
            out.append("\n")
        elif tag == T + "note":
            body = ch.find(T + "note-body")
            if body is not None:
                notes.append(" ".join(" ".join(_inline(p, notes).split()) for p in body.iter(T + "p")).strip())
        else:
            out.append(_inline(ch, notes))
        out.append(ch.tail or "")
    return "".join(out)


def _blocks(el: ET.Element, notes: list[str], out: list[Block]) -> None:
    for ch in el:
        tag = ch.tag
        if tag == T + "h":
            try:
                lvl = int(ch.get(T + "outline-level", "1"))
            except ValueError:
                lvl = 1
            out.append(Block("h", normalize(_inline(ch, notes)), lvl))
        elif tag == T + "p":
            out.append(Block("p", normalize(_inline(ch, notes))))
        elif tag == T + "list":
            for item in ch.iter(T + "list-item"):
                for p in item.findall(T + "p") + item.findall(T + "h"):
                    out.append(Block("p", "- " + normalize(_inline(p, notes)).strip()))
        elif tag == TB + "table":
            rows = [[" ".join(" ".join(normalize(_inline(p, notes)).split()) for p in c.iter(T + "p")).strip()
                     for c in r.findall(TB + "table-cell")] for r in ch.iter(TB + "table-row")]
            t = md_table(rows)
            if t:
                out.append(Block("table", t))
        elif tag in (T + "section", TB + "table-header-rows", T + "index-body") or tag in (
                T + "table-of-content",):
            _blocks(ch, notes, out)


def extract(path: Path) -> dict[str, Any]:
    with SafeZip(path, "OpenDocument file") as z:
        if not z.has("content.xml"):
            raise Refused("That isn't an OpenDocument text Domovoi can read (no content part inside).")
        content = z.xml("content.xml")
        styles = z.xml("styles.xml") if z.has("styles.xml") else None
    body = content.find(f"{OFFICE}body/{OFFICE}text")
    if body is None:
        raise Refused("That OpenDocument file has no text (is it a spreadsheet or a drawing?).")
    notes: list[str] = []
    blocks: list[Block] = []
    _blocks(body, notes, blocks)
    headers: list[str] = []
    footers: list[str] = []
    if styles is not None:
        for mp in styles.iter(ST + "master-page"):
            for tag, dst in ((ST + "header", headers), (ST + "footer", footers)):
                for h in mp.findall(tag):
                    t = normalize("\n".join(_inline(p, []) for p in h.iter(T + "p"))).strip()
                    if t and t not in dst:
                        dst.append(t)
    return {"parts": blocks_to_parts(blocks, headers=headers, footers=footers,
                                     footnotes=[normalize(n) for n in notes if n])}
