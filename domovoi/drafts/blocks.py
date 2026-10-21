"""A draft's markdown as the blocks a template draws.

Only what the skeletons use: ``# Name`` is the title (the resume's name,
in the body, never a page header), ``## Section`` a heading, ``- item`` a
bullet, a line that is bold as a whole (``**Re: rent for May**``) a bold
line, anything else a body line. Lines next to each other stay separate
lines (a letter's address block); a blank line ends a paragraph. Inline
marks (bold, italic, code, links) are dropped: the template decides how
text looks, never the model. The Kotlin twin is ``DraftBlocks.kt``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\u2022)\s+(.*)$")
_WHOLE_BOLD = re.compile(r"^(?:\*\*|__)(.+?)(?:\*\*|__)$")
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_FENCE = re.compile(r"^\s*```")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
_ITALIC = re.compile(r"(?<![A-Za-z0-9_*])([*_])(?![ \t])(.+?)(?<![ \t])\1(?![A-Za-z0-9_*])")
_CODE = re.compile(r"`([^`]*)`")
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!])")
_SPACES = re.compile(r"[ \t\u00a0]+")


@dataclass
class Block:
    style: str                       # title | heading | strong | body | bullet
    lines: list[str] = field(default_factory=list)


def inline(text: str) -> str:
    """``text`` without markdown's inline marks, its spaces collapsed."""
    s = _LINK.sub(r"\1", text)
    s = _CODE.sub(r"\1", s)
    for _ in range(2):
        s = _BOLD.sub(r"\2", s)
    s = _ITALIC.sub(r"\2", s)
    s = _ESCAPE.sub(r"\1", s)
    return _SPACES.sub(" ", s).strip()


def parse(markdown: str) -> list[Block]:
    out: list[Block] = []
    para: Block | None = None
    for raw in (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if _FENCE.match(line) or _RULE.match(line):
            para = None
            continue
        if not line.strip():
            para = None
            continue
        m = _HEADING.match(line.strip())
        if m:
            text = inline(m.group(2))
            if text:
                out.append(Block("title" if len(m.group(1)) == 1 else "heading", [text]))
            para = None
            continue
        m = _BULLET.match(line)
        if m:
            text = inline(m.group(1))
            if text:
                out.append(Block("bullet", [text]))
            para = None
            continue
        m = _WHOLE_BOLD.match(line.strip())
        if m and "**" not in m.group(1) and "__" not in m.group(1):
            text = inline(m.group(1))
            if text:
                out.append(Block("strong", [text]))
            para = None
            continue
        text = inline(line)
        if not text:
            continue
        if para is None:
            para = Block("body", [])
            out.append(para)
        para.lines.append(text)
    return out


def plain_text(blocks: list[Block]) -> str:
    """The words a reader of the file sees, a line per line (no bullets)."""
    return "\n".join(line for b in blocks for line in b.lines)


def word_count(markdown: str) -> int:
    return sum(len(line.split()) for b in parse(markdown) for line in b.lines)
