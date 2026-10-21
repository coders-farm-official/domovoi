"""Plain text (.txt) and markdown (.md).

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


def extract(path: Path, markdown: bool) -> dict[str, Any]:
    text, enc = decode_text(path.read_bytes())
    text = normalize(text)
    return {"parts": blocks_to_parts(blocks_of(text, markdown)), "encoding": enc}
