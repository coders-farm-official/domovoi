"""Pages and lines for a draft in a template: the one layout both PDF
renderers draw (ReportLab here, the app's ``DraftPdfWriter`` after
``DraftLayout.kt``), so the same template gives the same text from the
server and the phone.

The rules, kept simple enough to hold identically in Kotlin:

* **NFKC first.** The Liberation fonts have no glyphs for the ff, ffi and
  ffl ligature characters, and ReportLab drops a character it has no glyph
  for ("coffee" became "coee" in D0); NFKC turns them into plain letters.
  Spaces and tabs collapse to one space; a character the font still can't
  draw becomes "?" (the ATS self-check would say so after a save).
* **Our own line breaking.** Greedy, at spaces, measured with the font's
  own advance widths (:mod:`.metrics`) in integer arithmetic: a line fits
  when ``units × size ≤ room × units_per_em``, every quantity in
  hundredths of a point. No renderer measures anything.
* **An explicit space at every wrap.** A wrapped line keeps the space it
  was broken at as its last character (the D0 round trip read "if I" as
  "ifI" across a wrap), so the laid-out text and the .docx keep it; a
  word longer than the line is cut between letters, with no space. In a
  PDF each line is drawn as its own text line, which readers always break
  on; the trailing space itself isn't drawn (:mod:`.pdf_file` says why).
* **Pages.** Letter size, the template's margins; a line that would cross
  the bottom margin starts a new page; a title, heading or bold line is
  kept with the line after it; space before a block is dropped at the top
  of a page.
"""

from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass, field

from domovoi.drafts import blocks as blocks_mod
from domovoi.drafts import metrics
from domovoi.drafts.templates import Template

#: Hundredths of a point per point.
SCALE = 100
_KEEP_WITH_NEXT = ("title", "heading", "strong")


@dataclass(frozen=True)
class Line:
    style: str
    font: str          # font file stem
    size: int          # hundredths of a point
    x: int             # left edge of the text, from the page's left
    y: int             # baseline, from the page's top
    text: str
    #: The bullet drawn before the text (at ``marker_x``), "" for none.
    marker: str = ""
    marker_x: int = 0


@dataclass
class Page:
    lines: list[Line] = field(default_factory=list)


@dataclass
class Layout:
    width: int
    height: int
    pages: list[Page]

    def text(self) -> str:
        """The text as drawn: page by page, a line per line."""
        return "\f".join("\n".join((ln.marker + " " if ln.marker else "") + ln.text for ln in p.lines)
                         for p in self.pages)

    def as_json(self) -> dict:
        return {"width": self.width, "height": self.height,
                "pages": [[asdict(ln) for ln in p.lines] for p in self.pages]}


def cpt(points: float) -> int:
    """Points as hundredths of a point (sizes have at most two decimals)."""
    return int(round(points * SCALE))


def prepare(text: str, font: metrics.Font) -> str:
    """NFKC, one space for any run of spaces and tabs, "?" for a
    character ``font`` can't draw."""
    s = unicodedata.normalize("NFKC", text or "")
    out: list[str] = []
    space = False
    for ch in s:
        if ch in (" ", "\t", "\n", "\r"):
            space = True
            continue
        if space and out:
            out.append(" ")
        space = False
        out.append(ch if font.has(ch) else "?")
    return "".join(out)


def fits(text: str, font: metrics.Font, size: int, room: int) -> bool:
    return font.width_units(text) * size <= room * font.units_per_em


def wrap(text: str, font: metrics.Font, size: int, room: int) -> list[str]:
    """``text`` (prepared) broken into lines that fit ``room``; every line
    but the last that was broken at a space ends with that space."""
    words = [w for w in text.split(" ") if w]
    if not words:
        return []
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = w if not cur else cur + " " + w
        if fits(cand, font, size, room):
            cur = cand
            continue
        if cur:
            lines.append(cur + " ")
        cur = w
        # A word wider than the line: cut it between letters (no space).
        while not fits(cur, font, size, room) and len(cur) > 1:
            n = len(cur) - 1
            while n > 1 and not fits(cur[:n], font, size, room):
                n -= 1
            lines.append(cur[:n])
            cur = cur[n:]
    lines.append(cur)
    return lines


def layout(markdown: str, template: Template) -> Layout:
    width, height, margin = cpt(template.width), cpt(template.height), cpt(template.margin)
    bottom = height - margin
    content = width - 2 * margin
    parsed = blocks_mod.parse(markdown)
    pages: list[Page] = [Page()]
    cursor = margin

    def new_page() -> None:
        nonlocal cursor
        pages.append(Page())
        cursor = margin

    for i, block in enumerate(parsed):
        st = template.styles[block.style]
        font = metrics.font(template.font_file(st.font))
        size, leading = cpt(st.size), cpt(st.leading)
        indent = cpt(st.indent) if block.style == "bullet" else 0
        room = content - indent
        wrapped: list[list[str]] = [wrap(prepare(line, font), font, size, room) for line in block.lines]
        count = sum(len(w) for w in wrapped)
        if count == 0:
            continue
        before = cpt(st.before) if cursor > margin else 0
        need = before + leading * count
        if block.style in _KEEP_WITH_NEXT and i + 1 < len(parsed):
            nxt = template.styles[parsed[i + 1].style]
            need += cpt(st.after) + cpt(nxt.before) + cpt(nxt.leading)
        if cursor + need > bottom and cursor > margin:
            new_page()
            before = 0
        cursor += before
        first = True
        for src in wrapped:
            for text in src:
                if cursor + leading > bottom and cursor > margin:
                    new_page()
                marker = st.marker if (block.style == "bullet" and first) else ""
                pages[-1].lines.append(Line(
                    style=block.style, font=font.stem, size=size, x=margin + indent,
                    y=cursor + size, text=text, marker=marker, marker_x=margin if marker else 0,
                ))
                cursor += leading
                first = False
        cursor += cpt(st.after)
    if len(pages) > 1 and not pages[-1].lines:
        pages.pop()
    return Layout(width=width, height=height, pages=pages)
