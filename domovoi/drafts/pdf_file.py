"""A draft as a PDF: the laid-out lines (:mod:`.layout`) drawn by ReportLab,
one ``drawString`` per line, in the bundled Liberation fonts (subset and
embedded, with ToUnicode maps, so the text layer is real and searchable).

Single column by construction: lines are drawn top to bottom, left to
right, which is the order a text extractor (and an applicant tracking
system) reads them in. Each line is its own text line on its own baseline,
so every reader breaks the line there and the words either side of a wrap
never run together ("if I" never reads "ifI").

A wrapped line's kept space (:mod:`.layout`) is not drawn at the line's
end (the line break keeps the words apart), here and on the phone alike.
The phone's twin is ``DraftPdfWriter.kt``, the app's own small writer that
writes each line the way ReportLab does here (its own text object, one
``Tj`` with real spaces, no kerning, the same fonts embedded and subset):
pdfminer, pypdf, poppler (default and ``-raw``) and pdfium read the same
text from either (review round 1; Android's ``PdfDocument`` writes no
space glyphs, and those readers guessed differently). ReportLab can't tag
a PDF (that is its paid version), which is why .docx is offered beside it
and is the default for resumes and cover letters.
"""

from __future__ import annotations

import io

from domovoi.drafts import layout as layout_mod
from domovoi.drafts import metrics
from domovoi.drafts.templates import Template

_registered: set[str] = set()


def _font(stem: str) -> str:
    """The ReportLab name of a bundled font, registered once."""
    if stem not in _registered:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        pdfmetrics.registerFont(TTFont(stem, metrics.font_path(stem)))
        _registered.add(stem)
    return stem


def render(markdown: str, template: Template, *, title: str = "") -> bytes:
    from reportlab.pdfgen import canvas

    lay = layout_mod.layout(markdown, template)
    s = layout_mod.SCALE
    w, h = lay.width / s, lay.height / s
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h), invariant=1, pageCompression=1)
    c.setTitle(title or "")
    c.setAuthor("")
    c.setCreator("Domovoi")
    c.setProducer("Domovoi")
    for page in lay.pages:
        for ln in page.lines:
            c.setFont(_font(ln.font), ln.size / s)
            if ln.marker:
                c.drawString(ln.marker_x / s, h - ln.y / s, ln.marker + " ")
            c.drawString(ln.x / s, h - ln.y / s, ln.text.rstrip(" "))
        c.showPage()
    c.save()
    return buf.getvalue()
