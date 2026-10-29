"""Text out of an attached document, for Chat (documents plan, part 1).

Every format becomes the same thing: an ordered list of **parts**, each
with the label an answer cites and its text in plain markdown
(:mod:`domovoi.doc_qa` searches them).

| Format | A part is | Cited as |
|---|---|---|
| PDF (:mod:`.pdf`) | a page | "p. 12" |
| .docx (:mod:`.word`), .odt (:mod:`.odf`), .md, .txt, formatted documents (.html, :mod:`.plain` through :mod:`domovoi.richdoc`) | a section under a heading, a long one split between paragraphs | "Section: Deposit", "Section: Deposit (para 3)", "para 14" |
| .xlsx, .csv (:mod:`.sheets`) | 40 rows, the header row repeated; a sheet's column totals | "Sheet Budget, rows 2-41", "rows 2-41", "Sheet Budget, column totals" |

The file is somebody else's bytes, so:

* :func:`kind_for` and :func:`sniff` take only the formats above, by
  extension AND by their first bytes; .doc and .rtf are refused with what
  to do instead;
* :func:`extract` (the reading itself) runs in a short-lived worker
  process with a time and memory cap (:mod:`.runner`, :mod:`.worker`,
  :mod:`.limits`); a PDF that hangs or crashes PDFium costs one
  attachment, not the web process;
* zips and XML go through :mod:`.safe` (zip-bomb and size checks, an XML
  parser that refuses DOCTYPEs and entities). Nothing here opens a
  network connection or follows a link in the file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from domovoi.doc_extract.safe import MAX_FILE_BYTES, Refused  # noqa: F401

#: The cache format of an extraction result; bump when parts change shape.
FORMAT_VERSION = 1

KIND_BY_EXT = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".odt": "odt",
    ".xlsx": "xlsx",
    ".csv": "csv",
    ".tsv": "csv",
    ".txt": "txt",
    ".text": "txt",
    ".md": "md",
    ".markdown": "md",
    # The Documents editor's formatted documents (domovoi/richdoc): read as
    # the markdown-shaped text richdoc.html_to_markdown makes of them.
    ".html": "html",
    ".htm": "html",
}
ACCEPTS = "PDF, Word (.docx), OpenDocument (.odt), Excel (.xlsx), CSV, text or markdown"

#: Old formats: refused with what to do instead (plan part 1).
OLD_FORMATS = {
    ".doc": "Save it as .docx or PDF, then attach it again.",
    ".rtf": "Save it as .docx or PDF, then attach it again.",
    ".wpd": "Save it as .docx or PDF, then attach it again.",
    ".xls": "Save it as .xlsx or .csv, then attach it again.",
    ".ods": "Save it as .xlsx or .csv, then attach it again.",
    ".pages": "Export it as .docx or PDF, then attach it again.",
}

#: The most pages a PDF may have (the time cap is the real limit).
MAX_PAGES = 5000
#: The most text kept from one document (review round 1): about 1,000
#: pages. Past it, the parts are cut and a note says what was read; the
#: readers stop early when they can.
MAX_TEXT_CHARS = 4_000_000


def kind_for(name: str) -> str | None:
    return KIND_BY_EXT.get(Path(name or "").suffix.lower())


def old_format_advice(name: str) -> str | None:
    return OLD_FORMATS.get(Path(name or "").suffix.lower())


def sniff(head: bytes, kind: str) -> str | None:
    """Why ``head`` (the file's first bytes) isn't a ``kind`` file, or None."""
    if kind == "pdf":
        return None if b"%PDF-" in head[:1024] else "That file isn't a PDF, though its name ends in .pdf."
    if kind in ("docx", "odt", "xlsx"):
        return None if head[:4] == b"PK\x03\x04" else "That file is damaged or isn't the kind its name says."
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return None
    if b"\x00" in head[:8192]:
        return "That file isn't text, though its name says it is."
    return None


def extract(path: Path, kind: str, *, single_pass: int | None = None) -> dict[str, Any]:
    """The parts of ``path`` and what is known about them. Raises
    :class:`~.safe.Refused` with a sentence for the person. Reads hostile
    bytes: call it in the worker (:func:`.runner.extract`), not in a
    server process."""
    from domovoi.doc_qa import notes as doc_notes
    from domovoi.doc_qa.answer import SINGLE_PASS_CHARS

    path = Path(path)
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise Refused(f"That file is too large for the chat: more than {MAX_FILE_BYTES // 2 ** 20} MB.")
    with path.open("rb") as f:
        why = sniff(f.read(8192), kind)
    if why:
        raise Refused(why)
    if kind == "pdf":
        from domovoi.doc_extract import pdf

        result = pdf.extract(path, MAX_PAGES, max_chars=MAX_TEXT_CHARS)
    elif kind == "docx":
        from domovoi.doc_extract import word

        result = word.extract(path)
    elif kind == "odt":
        from domovoi.doc_extract import odf

        result = odf.extract(path)
    elif kind == "xlsx":
        from domovoi.doc_extract import sheets

        result = sheets.extract_xlsx(path)
    elif kind == "csv":
        from domovoi.doc_extract import sheets

        result = sheets.extract_csv(path)
    elif kind in ("txt", "md"):
        from domovoi.doc_extract import plain

        result = plain.extract(path, markdown=kind == "md", max_chars=MAX_TEXT_CHARS)
    elif kind == "html":
        from domovoi.doc_extract import plain

        result = plain.extract_html(path, max_chars=MAX_TEXT_CHARS)
    else:
        raise Refused(f"Chat reads {ACCEPTS} files.")
    parts, cut = _cap(result.get("parts") or [], MAX_TEXT_CHARS)
    cut = cut or bool(result.get("truncated"))
    result["parts"] = parts
    result["kind"] = kind
    result["version"] = FORMAT_VERSION
    result["words"] = doc_notes.count_words(parts)
    result["chars"] = sum(len(str(p.get("text") or "")) + len(str(p.get("head") or "")) for p in parts)
    if not parts:
        if result.get("scanned_pages"):
            raise Refused(doc_notes.NO_TEXT_SCANNED)
        raise Refused(doc_notes.NO_TEXT)
    result["notes"] = doc_notes.attach_notes(result, single_pass or SINGLE_PASS_CHARS)
    result["truncated"] = cut
    if cut:
        pages_read = max((int(p["page"]) for p in parts if p.get("page")), default=None) if kind == "pdf" else None
        result["notes"].append(doc_notes.truncated_note(pages_read, result["words"]))
    return result


def _cap(parts: list[dict[str, Any]], max_chars: int) -> tuple[list[dict[str, Any]], bool]:
    """Non-empty parts kept whole up to ``max_chars`` (the phone's
    ``DocExtract.finish`` does the same); the first part alone is cut to fit
    when it is bigger than that. Returns (parts, whether anything was cut)."""
    out: list[dict[str, Any]] = []
    used = 0
    for p in parts:
        text = str(p.get("text") or "")
        if not text.strip():
            continue
        size = len(text) + len(str(p.get("head") or ""))
        if used + size <= max_chars:
            out.append(p)
            used += size
            continue
        room = max_chars - used - len(str(p.get("head") or ""))
        if room > 0 and not out:
            out.append({**p, "text": text[:room]})
        return out, True
    return out, False
