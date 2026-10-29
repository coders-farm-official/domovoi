"""Plain text (.txt), markdown (.md) and formatted documents (.html).

UTF-8 (a BOM dropped), UTF-16 by its BOM, cp1252 as the fallback. A
markdown ``#`` line is a heading; paragraphs are separated by blank lines
(a markdown list or table stays one paragraph). A .txt has no headings,
so its parts are labelled by paragraph.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from domovoi.doc_extract.sheets import decode_text
from domovoi.doc_extract.structure import Block, blocks_to_parts
from domovoi.doc_qa.text import clean as normalize

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def blocks_of(text: str, markdown: bool) -> list[Block]:
    blocks: list[Block] = []
    para: list[str] = []
    fence = False

    def flush() -> None:
        if para:
            blocks.append(Block("p", "\n".join(para).strip()))
            para.clear()

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if markdown and line.strip().startswith("```"):
            fence = not fence
            para.append(line)
            continue
        m = _MD_HEADING.match(line) if markdown and not fence else None
        if m:
            flush()
            blocks.append(Block("h", m.group(2), len(m.group(1))))
        elif not line.strip() and not fence:
            flush()
        else:
            para.append(line.rstrip())
    flush()
    return blocks


def extract(path: Path, markdown: bool, max_chars: int | None = None) -> dict[str, Any]:
    """``max_chars``: read no more of the file than that many characters
    can take (4 bytes each at most), and say when the file went on."""
    with path.open("rb") as f:
        raw = f.read(max_chars * 4 + 4 if max_chars else -1)
        more = bool(max_chars) and bool(f.read(1))
    text, enc = decode_text(raw)
    text = normalize(text)
    if max_chars and len(text) > max_chars:
        text, more = text[:max_chars], True
    return {"parts": blocks_to_parts(blocks_of(text, markdown)), "encoding": enc, "truncated": more}


#: A formatted document's pictures are in the file (data: URIs), so its
#: size says little about its text; this much of it is parsed at most.
HTML_MAX_BYTES = 64 * 1024 * 1024


def extract_html(path: Path, max_chars: int | None = None) -> dict[str, Any]:
    """A formatted document (the Documents editor's .html, or any HTML
    page) as markdown-shaped text (domovoi.richdoc.html_to_markdown: the
    headings, lists, checklists, tables and captions; no pictures, nothing
    run or fetched), then read like a markdown file."""
    from domovoi.richdoc import html_to_markdown

    with path.open("rb") as f:
        raw = f.read(HTML_MAX_BYTES)
    markup, enc = decode_text(raw)
    text = normalize(html_to_markdown(markup))
    more = False
    if max_chars and len(text) > max_chars:
        text, more = text[:max_chars], True
    return {"parts": blocks_to_parts(blocks_of(text, True)), "encoding": enc, "truncated": more}
