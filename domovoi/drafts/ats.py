"""The ATS self-check that runs after every save: the saved file's text is
read back (a PDF's text layer with pypdfium2, a .docx's paragraph XML as
D1 reads it) and compared, word by word and in order, with the draft.

A match means an applicant tracking system reads the same words in the
same order: ``text readable · single column``. A mismatch says where,
before the person sends the file. The phone's twin is ``DraftAts.kt``
(the phone's own PDF text API, and its own .docx reader).
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass

from domovoi.drafts import blocks as blocks_mod

PASSED = "text readable · single column"
_WORD = re.compile(r"\S+")
_BULLETS = frozenset({"•", "-", "*", "·"})


@dataclass(frozen=True)
class Check:
    ok: bool
    line: str
    #: Words read back, and words expected.
    read: int
    expected: int

    def as_json(self) -> dict:
        return {"ok": self.ok, "line": self.line, "read": self.read, "expected": self.expected}


def words(text: str) -> list[str]:
    s = unicodedata.normalize("NFKC", text or "")
    return [w for w in _WORD.findall(s) if w not in _BULLETS]


def expected_words(markdown: str) -> list[str]:
    return words(blocks_mod.plain_text(blocks_mod.parse(markdown)))


def compare(read: list[str], want: list[str]) -> Check:
    if read == want:
        return Check(True, PASSED, len(read), len(want))
    i = 0
    while i < min(len(read), len(want)) and read[i] == want[i]:
        i += 1
    if i < len(want):
        got = read[i] if i < len(read) else "nothing"
        line = f"text check: “{want[i]}” reads as “{got}” (word {i + 1} of {len(want)})"
    else:
        line = f"text check: {len(read) - len(want)} extra words after the end"
    return Check(False, line, len(read), len(want))


def pdf_text(data: bytes) -> str:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(data)
    try:
        out = []
        for i in range(len(doc)):
            page = doc[i]
            tp = page.get_textpage()
            try:
                out.append(tp.get_text_range())
            finally:
                tp.close()
                page.close()
        return "\n".join(out)
    finally:
        doc.close()


def docx_text(data: bytes) -> str:
    """The text of a .docx we wrote: every ``w:t`` (and line break) of
    every paragraph, in order, through D1's hardened XML parser."""
    from domovoi.doc_extract.safe import parse_xml

    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = parse_xml(z.read("word/document.xml"))
    lines = []
    for p in root.iter(w + "p"):
        parts = []
        for el in p.iter():
            if el.tag == w + "t":
                parts.append(el.text or "")
            elif el.tag == w + "br":
                parts.append("\n")
            elif el.tag == w + "tab":
                parts.append(" ")
        lines.append("".join(parts))
    return "\n".join(lines)


def check(data: bytes, fmt: str, markdown: str) -> Check:
    try:
        text = pdf_text(data) if fmt == "pdf" else docx_text(data)
    except Exception:  # noqa: BLE001 — a file we can't read back fails the check
        return Check(False, "text check: the saved file couldn't be read back", 0, len(expected_words(markdown)))
    return compare(words(text), expected_words(markdown))
