"""Blocks to parts, for the formats with headings (.docx, .odt, .md, .txt).

A part is a section under a heading ("Section: Deposit"); a section
longer than :data:`PART_CHARS` is split between paragraphs, and each piece
after the first names the paragraph it starts at ("Section: Deposit (para
3)"). Text before the first heading is "Opening". A document with no
headings at all is cut the same way, labelled by paragraph ("para 1",
"para 14"). Headers, footers and footnotes are parts of their own, kept
once.

Parts are what answers cite, so they are kept small (about 1,000 tokens);
a long document's summary reads them in groups of about 3,500 tokens
(:func:`domovoi.doc_qa.longdoc.map_chunks`, D2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PART_CHARS = 4000
_LABEL_MAX = 60


@dataclass
class Block:
    #: "h" (a heading, with ``level``), "p" (a paragraph or list item) or
    #: "table" (markdown rows, one per line).
    kind: str
    text: str
    level: int = 0


def _short(s: str) -> str:
    s = " ".join(s.split())
    return s if len(s) <= _LABEL_MAX else s[: _LABEL_MAX - 1].rstrip() + "…"


def blocks_to_parts(blocks: list[Block], *, headers: list[str] | None = None,
                    footers: list[str] | None = None, footnotes: list[str] | None = None,
                    part_chars: int = PART_CHARS) -> list[dict[str, Any]]:
    has_headings = any(b.kind == "h" and b.text.strip() for b in blocks)
    sections: list[tuple[str | None, str, list[str]]] = []   # (heading, md heading line, paragraphs)
    cur_head: str | None = None
    cur_line = ""
    cur: list[str] = []
    for b in blocks:
        t = b.text.strip()
        if not t:
            continue
        if b.kind == "h":
            if cur or cur_head is not None:
                sections.append((cur_head, cur_line, cur))
            cur_head, cur_line, cur = t, "#" * max(1, min(6, b.level or 1)) + " " + t, []
        else:
            cur.append(t)
    if cur or cur_head is not None:
        sections.append((cur_head, cur_line, cur))

    parts: list[dict[str, Any]] = []
    if headers:
        parts.append({"label": "page header", "text": "\n".join(headers)})
    para_no = 0           # paragraphs so far in the whole document
    for head, line, paras in sections:
        if head is not None:
            base: str | None = f"Section: {_short(head)}"
        elif has_headings:
            base = "Opening"
        else:
            base = None
        chunk: list[str] = [line] if line else []
        size = len(line)
        in_chunk = 0
        start_k, start_doc, first = 1, para_no + 1, True
        for k, p in enumerate(paras, 1):
            para_no += 1
            if in_chunk and size + len(p) > part_chars:
                parts.append(_part(base, start_k, start_doc, chunk, first))
                first = False
                chunk, size, in_chunk = [], 0, 0
                start_k, start_doc = k, para_no
            chunk.append(p)
            size += len(p) + 2
            in_chunk += 1
        if chunk:
            parts.append(_part(base, start_k, start_doc, chunk, first))
    if footers:
        parts.append({"label": "page footer", "text": "\n".join(footers)})
    if footnotes:
        parts.append({"label": "footnotes", "text": "\n".join(f"{i}. {t}" for i, t in enumerate(footnotes, 1))})
    return parts


def _part(base: str | None, start_k: int, start_doc: int, chunk: list[str], first: bool) -> dict[str, Any]:
    if base is None:
        label = f"para {start_doc}"
    elif first:
        label = base
    else:
        label = f"{base} (para {start_k})"
    return {"label": label, "text": "\n\n".join(chunk)}


def md_table(rows: list[list[str]]) -> str:
    """Rows as a markdown table (the first row is the header)."""
    rows = [[" ".join(c.replace("|", "/").split()) for c in r] for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)
