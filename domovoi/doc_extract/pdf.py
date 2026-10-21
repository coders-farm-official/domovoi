"""PDF text, page by page, in reading order (pypdfium2; D0-measured).

* Text comes from each page's text layer: the text rectangles PDFium
  reports, merged into lines by baseline and sorted left to right.
* **Two columns** (D0): a vertical gutter that few rectangles cross,
  with at least 8 segments on each side, AND both sides looking like
  running text (a median segment wider than a quarter of the page), so a
  table isn't taken for columns. Such a page reads the left column, then
  the right; a line that spans the gutter (a headline) flushes both.
  The interleaved two-column page scored 26.9% raw and 100% sorted.
* **Running headers and footers**: a line (digits ignored) in the top or
  bottom two lines of at least 60% of the text pages is dropped from
  every page and kept once, as the "page header" / "page footer" part.
* **Pages that need vision** (D0 rule, 3/3 found, no false alarm in 42):
  fewer than 20 visible characters on a page with an image (a scan), or
  more than half the words gibberish (a broken font map). They are
  listed, not searched; reading them with the vision model comes with
  the heads-up (D3).
* Text is NFKC'd (ligatures "ﬁ" become "fi").

Runs only inside the worker process (:mod:`domovoi.doc_extract.worker`).
"""

from __future__ import annotations

import math
import re
import statistics
from pathlib import Path
from typing import Any

from domovoi.doc_extract.safe import Refused
from domovoi.doc_qa.text import nfkc as normalize

_REAL = re.compile(r"^[A-Za-z][a-z]*[A-Za-z]?$|^\d[\d,.:/$%-]*$|^[A-Z]{2,}$")
_VOWEL = re.compile(r"[aeiouyAEIOUY]")

#: A page with fewer visible characters than this, and an image, is a scan.
SCAN_VISIBLE_CHARS = 20
#: A page whose words are more than this share gibberish has a broken font map.
GIBBERISH_SHARE = 0.5


def gibberish_share(text: str) -> float:
    toks = [t.strip(".,;:()\"'“”‘’!?") for t in text.split()]
    toks = [t for t in toks if t]
    if len(toks) < 15:
        return 0.0
    real = sum(1 for t in toks if _REAL.match(t) and (_VOWEL.search(t) or t[0].isdigit() or t.isupper()))
    return 1 - real / len(toks)


def _page_has_image(page: Any) -> bool:
    import pypdfium2.raw as pdfium_c

    for _obj in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,), max_depth=2):
        return True
    return False


def _segments(tp: Any) -> list[list[Any]]:
    segs = []
    for i in range(tp.count_rects()):
        left, bottom, right, top = tp.get_rect(i)
        txt = tp.get_text_bounded(left, bottom, right, top)
        txt = txt.replace("\r", "").replace("\n", " ").strip()
        if txt:
            segs.append([left, bottom, right, top, txt])
    return segs


def _merge_lines(segs: list[list[Any]]) -> list[str]:
    """Segments sharing a baseline band become one line, left to right."""
    segs = sorted(segs, key=lambda s: (-(s[1] + s[3]) / 2, s[0]))
    lines: list[dict[str, Any]] = []
    for s in segs:
        mid = (s[1] + s[3]) / 2
        h = max(1.0, s[3] - s[1])
        if lines and abs(lines[-1]["mid"] - mid) < h * 0.5:
            lines[-1]["segs"].append(s)
        else:
            lines.append({"mid": mid, "segs": [s]})
    return [" ".join(s[4] for s in sorted(ln["segs"], key=lambda s: s[0])) for ln in lines]


def find_gutter(segs: list[list[Any]], width: float) -> float | None:
    """The x of a two-column page's gutter, or None for one column."""
    if len(segs) < 12 or width <= 0:
        return None
    best = None
    for x in range(int(width * 0.3), int(width * 0.7), 2):
        cross = sum(1 for s in segs if s[0] < x < s[2])
        left = sum(1 for s in segs if s[2] <= x)
        right = sum(1 for s in segs if s[0] >= x)
        if left >= 0.2 * len(segs) and right >= 0.2 * len(segs) and left >= 8 and right >= 8:
            if best is None or cross < best[0]:
                best = (cross, x)
    if not best or best[0] > 0.15 * len(segs):
        return None
    x = best[1]
    lw = statistics.median([s[2] - s[0] for s in segs if s[2] <= x])
    rw = statistics.median([s[2] - s[0] for s in segs if s[0] >= x])
    if min(lw, rw) < 0.25 * width:
        return None
    return x


def sorted_lines(segs: list[list[Any]], width: float) -> tuple[list[str], int]:
    """The page's lines in reading order, and how many columns it has."""
    g = find_gutter(segs, width)
    if g is None:
        return _merge_lines(segs), 1
    out: list[str] = []
    left: list[list[Any]] = []
    right: list[list[Any]] = []

    def flush() -> None:
        out.extend(_merge_lines(left))
        out.extend(_merge_lines(right))
        left.clear()
        right.clear()

    for s in sorted(segs, key=lambda s: -s[3]):
        if s[0] < g < s[2]:
            flush()
            out.append(s[4])
        elif s[2] <= g:
            left.append(s)
        else:
            right.append(s)
    flush()
    return out, 2


def _hf_norm(s: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", s.lower())).strip()


def strip_running(pages: list[list[str]], zone: int = 2) -> tuple[list[list[str]], list[str], list[str]]:
    """Lines repeated (digits ignored) in the top or bottom ``zone`` lines of
    most text pages: dropped from each page, and kept once."""
    n = sum(1 for p in pages if p)
    if n < 2:
        return pages, [], []
    need = max(2, math.ceil(0.6 * n))
    tops: dict[str, int] = {}
    bots: dict[str, int] = {}
    for lines in pages:
        for t in {_hf_norm(x) for x in lines[:zone]}:
            tops[t] = tops.get(t, 0) + 1
        for t in {_hf_norm(x) for x in lines[-zone:]}:
            bots[t] = bots.get(t, 0) + 1
    rt = {t for t, c in tops.items() if c >= need and t}
    rb = {t for t, c in bots.items() if c >= need and t}
    headers: list[str] = []
    footers: list[str] = []
    out = []
    for lines in pages:
        keep = list(lines)
        for _ in range(min(zone, len(keep))):
            if keep and _hf_norm(keep[0]) in rt:
                h = keep.pop(0)
                if _hf_norm(h) not in {_hf_norm(x) for x in headers}:
                    headers.append(h)
        for _ in range(min(zone, len(keep))):
            if keep and _hf_norm(keep[-1]) in rb:
                f = keep.pop()
                if _hf_norm(f) not in {_hf_norm(x) for x in footers}:
                    footers.append(f)
        out.append(keep)
    return out, headers, footers


def extract(path: Path, max_pages: int) -> dict[str, Any]:
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as e:
        if "password" in str(e).lower():
            raise Refused("This PDF has a password. Save a copy without the password, then attach that.") from e
        raise Refused("That PDF is damaged and couldn't be read.") from e
    try:
        total = len(doc)
        if total < 1:
            raise Refused("That PDF has no pages.")
        if total > max_pages:
            raise Refused(f"That PDF has {total:,} pages, more than Domovoi reads ({max_pages:,}).")
        pages: list[list[str]] = []
        scanned: list[int] = []
        garbled: list[int] = []
        two_col: list[int] = []
        for i in range(total):
            page = doc[i]
            try:
                tp = page.get_textpage()
                try:
                    lines, cols = sorted_lines(_segments(tp), page.get_width())
                finally:
                    tp.close()
                lines = [normalize(x).strip() for x in lines if x.strip()]
                text = " ".join(lines)
                visible = len(re.sub(r"\s", "", text))
                if visible < SCAN_VISIBLE_CHARS and _page_has_image(page):
                    scanned.append(i + 1)
                    lines = []
                elif gibberish_share(text) > GIBBERISH_SHARE:
                    garbled.append(i + 1)
                    lines = []
                elif cols == 2:
                    two_col.append(i + 1)
                pages.append(lines)
            finally:
                page.close()
    finally:
        doc.close()
    pages, headers, footers = strip_running(pages)
    parts: list[dict[str, Any]] = []
    if headers:
        parts.append({"label": "page header", "text": "\n".join(headers)})
    for i, lines in enumerate(pages):
        if lines:
            parts.append({"label": f"p. {i + 1}", "page": i + 1, "text": "\n".join(lines)})
    if footers:
        parts.append({"label": "page footer", "text": "\n".join(footers)})
    return {"pages": total, "parts": parts, "scanned_pages": scanned, "garbled_pages": garbled,
            "two_column_pages": two_col}
