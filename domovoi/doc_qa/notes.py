"""The notes an attached document carries, worded once for both places.

The Kotlin twin is ``DocNotes.kt``; ``shared_cases.json`` holds them to
the same sentences. Each note is ``{"code": ..., "text": ...}``; the
codes never change meaning (the clients may word them themselves).
"""

from __future__ import annotations

from typing import Any, Sequence

from domovoi.doc_qa.answer import SINGLE_PASS_CHARS, _about


def pages_phrase(pages: Sequence[int]) -> str:
    """"Page 3", "Pages 3 and 7", "Pages 3, 7 and 9", "12 pages (3, 4, 5, …)"."""
    pages = list(pages)
    if len(pages) == 1:
        return f"Page {pages[0]}"
    if len(pages) <= 6:
        return "Pages " + ", ".join(str(p) for p in pages[:-1]) + f" and {pages[-1]}"
    return f"{len(pages)} pages (" + ", ".join(str(p) for p in pages[:3]) + ", …)"


def scanned_note(pages: Sequence[int]) -> dict[str, str]:
    one = len(pages) == 1
    return {"code": "scanned_pages",
            "text": f"{pages_phrase(pages)} {'is a picture' if one else 'are pictures'} of text "
                    f"(scanned), so Domovoi can't search {'it' if one else 'them'} yet."}


def garbled_note(pages: Sequence[int]) -> dict[str, str]:
    one = len(pages) == 1
    return {"code": "garbled_pages",
            "text": f"{pages_phrase(pages)} {'has' if one else 'have'} text the file stores scrambled "
                    f"(a font with no character map), so Domovoi can't search {'it' if one else 'them'} yet."}


def formulas_note(n: int) -> dict[str, str]:
    one = n == 1
    return {"code": "formulas_without_value",
            "text": f"{n:,} formula cell{'' if one else 's'} {'has' if one else 'have'} no saved value (the file "
                    f"was saved without calculating {'it' if one else 'them'}), so "
                    f"{'it reads' if one else 'they read'} as {'a formula' if one else 'formulas'}, like =SUM(B2:B9)."}


def long_note(words: int) -> dict[str, str]:
    return {"code": "long_document",
            "text": f"Long document (about {_about(words)} words): each question is answered from the "
                    "passages that best match it."}


def truncated_note(pages_read: int | None, words: int) -> dict[str, str]:
    """A document cut at the text cap (review round 1): what was read."""
    if pages_read is not None:
        return {"code": "truncated",
                "text": f"This document is very large; Domovoi read the first {pages_read} "
                        f"page{'' if pages_read == 1 else 's'}."}
    return {"code": "truncated",
            "text": f"This document is very large; Domovoi read the first part of it "
                    f"(about {_about(words)} words)."}


#: Why an attachment with no searchable text at all is refused.
NO_TEXT_SCANNED = ("That PDF is pictures of text (a scan), so there is no text Domovoi can search yet.")
NO_TEXT = "That file has no text Domovoi can read."


def count_words(parts: Sequence[dict[str, Any]]) -> int:
    from domovoi.doc_qa.text import words

    return sum(len(words(str(p.get("text") or ""))) for p in parts)


def summary_line(name: str, kind: str, pages: int | None, words: int, sheets: int | None = None) -> str:
    """The one-line marker the conversation keeps: "lease.pdf · 30 pages ·
    about 9,800 words"."""
    bits = [name]
    if kind == "pdf" and pages:
        bits.append(f"{pages} page{'' if pages == 1 else 's'}")
    if kind == "xlsx" and sheets:
        bits.append(f"{sheets} sheet{'' if sheets == 1 else 's'}")
    bits.append(f"about {_about(words)} word{'' if words == 1 else 's'}" if words >= 1000
                else f"{words} word{'' if words == 1 else 's'}")
    return " · ".join(bits)


def attach_notes(result: dict[str, Any], single_pass: int = SINGLE_PASS_CHARS) -> list[dict[str, str]]:
    """The notes for an extraction ``result`` (what an extractor returned,
    with ``words`` and ``chars`` counted)."""
    out = []
    if result.get("scanned_pages"):
        out.append(scanned_note(result["scanned_pages"]))
    if result.get("garbled_pages"):
        out.append(garbled_note(result["garbled_pages"]))
    if result.get("formulas_without_value"):
        out.append(formulas_note(int(result["formulas_without_value"])))
    if int(result.get("chars") or 0) > single_pass:
        out.append(long_note(int(result.get("words") or 0)))
    return out
