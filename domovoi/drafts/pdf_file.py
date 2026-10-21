"""A draft as a PDF: the laid-out lines (:mod:`.layout`) drawn by ReportLab,
one ``drawString`` per line, in the bundled Liberation fonts (subset and
embedded, with ToUnicode maps, so the text layer is real and searchable).

Single column by construction: lines are drawn top to bottom, left to
right, which is the order a text extractor (and an applicant tracking
system) reads them in. ReportLab can't tag a PDF (that is its paid
version), which is why .docx is offered beside it and is the default for
resumes and cover letters. The phone's twin is ``DraftPdf.kt``.
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
            c.drawString(ln.x / s, h - ln.y / s, ln.text)
        c.showPage()
    c.save()
    return buf.getvalue()
