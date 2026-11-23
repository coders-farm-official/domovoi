"""Markdown or plain text as Word (.docx).

The structures the document editor's toolbar can produce: headings,
bullet and numbered lists, fenced code blocks, plain paragraphs with
bold, italic and code inline. Built in the Word-conversion worker
(:mod:`domovoi.richdoc.worker`), never in the web process: python-docx
costs more per line the longer the document gets.
"""

from __future__ import annotations

import io
import re

_MD_INLINE = re.compile(r"(\*\*.+?\*\*|\*.+?\*|`.+?`)")


def md_runs(paragraph, text_: str) -> None:
    """Split ``text_`` on **bold** / *italic* / `code` markers and add the
    corresponding python-docx runs. Deliberately minimal — the doc editor
    is markdown-first with a basic toolbar, and the export mirrors it."""
    for part in _MD_INLINE.split(text_):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            paragraph.add_run(part[1:-1]).italic = True
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            run = paragraph.add_run(part[1:-1])
            run.font.name = "Consolas"
        else:
            paragraph.add_run(part)


def markdown_to_docx(text_: str) -> bytes:
    """Markdown → .docx with the structures the editor's toolbar can
    produce: headings, bullet/numbered lists, fenced code blocks, plain
    paragraphs with bold/italic/code inline."""
    from docx import Document

    from domovoi.richdoc.word import xml_safe

    doc = Document()
    in_code = False
    code_lines: list[str] = []
    # A character XML doesn't allow (a control character) would stop the
    # whole conversion: it is left out (DOC-14).
    for line in xml_safe(text_).splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                p = doc.add_paragraph()
                run = p.add_run("\n".join(code_lines))
                run.font.name = "Consolas"
                code_lines = []
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            doc.add_heading(m.group(2), level=min(len(m.group(1)), 6))
            continue
        if re.match(r"^[-*]\s+", stripped):
            p = doc.add_paragraph(style="List Bullet")
            md_runs(p, re.sub(r"^[-*]\s+", "", stripped))
            continue
        if re.match(r"^\d+\.\s+", stripped):
            p = doc.add_paragraph(style="List Number")
            md_runs(p, re.sub(r"^\d+\.\s+", "", stripped))
            continue
        if not stripped:
            continue
        md_runs(doc.add_paragraph(), stripped)
    if in_code and code_lines:  # unterminated fence
        p = doc.add_paragraph()
        p.add_run("\n".join(code_lines)).font.name = "Consolas"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
