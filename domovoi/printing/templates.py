"""The PDFs Domovoi draws itself, with ReportLab (BSD).

* the **test page** (Settings and "set up the printer");
* **text and markdown** files, one plain column (11 pt, page numbers);
* **spreadsheets** as a plain table: header row repeated on every page,
  landscape past 8 columns, columns shrunk down to 7 pt, and past that the
  sheet split across pages left to right;
* **images**, turned to match the page and scaled to fit inside the
  margins, centred;
* **scans**: scanner JPEGs placed full page, embedded byte for byte
  (``rl_config.useA85 = 0``; with ReportLab's default ASCII85 wrapping the
  bytes are not verbatim and 26% larger — P0-D, 2026-10-21).

Colour: DeviceRGB only, never DeviceCMYK. P0's CMYK swatches came out of
the owner's Brother as deep blue and maroon; sRGB is what PDFium's raster
path produces too, so both paths now agree. The test page's grey ramp
starts at 10% (P0: the first step was white by mistake) and labels each
step.

Fonts: a Unicode TrueType font when the system has one (DejaVu Sans or
Liberation Sans, both permissively licensed system fonts), else
Helvetica, whose WinAnsi set can't draw every script (a documented limit).
These run in the render worker process, never in the core.
"""

from __future__ import annotations

import datetime as _dt
import html
import io
import re
from pathlib import Path
from typing import Iterable

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    LongTable,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)

MARGIN = 0.75 * inch
LIGHT_GREY_NOTE = "Very light greys (under about 10-15%) may not print on some lasers."

_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
     "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf"),
]
_fonts: tuple[str, str] | None = None


def fonts() -> tuple[str, str]:
    """(regular, bold) font names."""
    global _fonts
    if _fonts is not None:
        return _fonts
    for regular, bold in _FONT_CANDIDATES:
        if Path(regular).is_file() and Path(bold).is_file():
            try:
                pdfmetrics.registerFont(TTFont("DomovoiSans", regular))
                pdfmetrics.registerFont(TTFont("DomovoiSans-Bold", bold))
                _fonts = ("DomovoiSans", "DomovoiSans-Bold")
                return _fonts
            except Exception:  # noqa: BLE001 — a broken system font: fall back
                continue
    _fonts = ("Helvetica", "Helvetica-Bold")
    return _fonts


def _brand_glyph() -> Path | None:
    p = Path(__file__).resolve().parents[2] / "web" / "static" / "assets" / "icon-192.png"
    return p if p.is_file() else None


# ─── Test page ───────────────────────────────────────────────────────────


def test_page(
    out: Path, *, printer_name: str, model: str, paper: tuple[int, int], paper_label: str,
    sides_label: str, color_label: str, color: bool, when: _dt.datetime | None = None,
) -> None:
    regular, bold = fonts()
    w, h = paper
    c = canvas.Canvas(str(out), pagesize=paper, pageCompression=1)
    c.setTitle("Domovoi test page")
    c.setAuthor("Domovoi")
    glyph = _brand_glyph()
    top = h - MARGIN
    if glyph is not None:
        c.drawImage(str(glyph), MARGIN, top - 56, 56, 56, mask="auto")
    c.setFillColor(colors.Color(0, 0, 0))
    c.setFont(bold, 22)
    c.drawString(MARGIN + 70, top - 24, "Domovoi can print to %s" % printer_name[:40])
    c.setFont(regular, 11)
    when = when or _dt.datetime.now().astimezone()
    c.drawString(MARGIN + 70, top - 44, when.strftime("%A %d %B %Y, %H:%M"))
    y = top - 90
    for label, value in (("Printer", model), ("Paper", paper_label), ("Two-sided", sides_label),
                         ("Colour", color_label)):
        c.setFont(bold, 11)
        c.drawString(MARGIN, y, label)
        c.setFont(regular, 11)
        c.drawString(MARGIN + 90, y, value[:80])
        y -= 18
    y -= 16
    c.setFont(bold, 11)
    c.drawString(MARGIN, y, "Grey ramp")
    y -= 50
    step_w = (w - 2 * MARGIN) / 10
    for i in range(10):
        pct = (i + 1) * 10
        g = 1 - pct / 100
        c.setFillColor(colors.Color(g, g, g))
        c.rect(MARGIN + i * step_w, y, step_w, 36, stroke=0, fill=1)
        c.setFillColor(colors.Color(0, 0, 0))
        c.setFont(regular, 8)
        c.drawCentredString(MARGIN + i * step_w + step_w / 2, y - 11, "%d%%" % pct)
    y -= 34
    if color:
        c.setFont(bold, 11)
        c.drawString(MARGIN, y, "Colour")
        y -= 50
        swatches = [
            ("red", (1, 0, 0)), ("green", (0, 1, 0)), ("blue", (0, 0, 1)),
            ("cyan", (0, 1, 1)), ("magenta", (1, 0, 1)), ("yellow", (1, 1, 0)),
            ("amber", (0.94, 0.6, 0.18)), ("plum", (0.42, 0.32, 0.6)),
        ]
        sw = (w - 2 * MARGIN) / len(swatches)
        for i, (label, rgb) in enumerate(swatches):
            c.setFillColor(colors.Color(*rgb))
            c.rect(MARGIN + i * sw + 2, y, sw - 4, 36, stroke=0, fill=1)
            c.setFillColor(colors.Color(0, 0, 0))
            c.setFont(regular, 8)
            c.drawCentredString(MARGIN + i * sw + sw / 2, y - 11, label)
        y -= 34
    c.setFont(regular, 9)
    c.drawString(MARGIN, y - 10, LIGHT_GREY_NOTE)
    c.setLineWidth(0.5)
    c.rect(36, 36, w - 72, h - 72, stroke=1, fill=0)
    c.setFont(regular, 7)
    c.drawString(40, 40, "The frame is 0.5 in from each edge.")
    c.showPage()
    c.save()


# ─── Text and markdown ───────────────────────────────────────────────────


def _numbered(canv, doc) -> None:
    regular, _ = fonts()
    canv.saveState()
    canv.setFont(regular, 8)
    canv.drawRightString(doc.pagesize[0] - MARGIN, MARGIN / 2, "%d" % doc.page)
    canv.restoreState()


_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _inline(text: str) -> str:
    s = html.escape(text, quote=False)
    s = _LINK.sub(r"\1", s)
    s = _BOLD.sub(r"<b>\1</b>", s)
    s = _ITALIC.sub(r"<i>\1</i>", s)
    s = _CODE.sub(r"<font face='Courier'>\1</font>", s)
    return s


def text_document(out: Path, body: str, *, title: str, paper: tuple[int, int], markdown: bool) -> None:
    regular, bold = fonts()
    base = ParagraphStyle("body", fontName=regular, fontSize=11, leading=15, spaceAfter=6)
    heads = {
        1: ParagraphStyle("h1", fontName=bold, fontSize=18, leading=22, spaceBefore=6, spaceAfter=8),
        2: ParagraphStyle("h2", fontName=bold, fontSize=15, leading=19, spaceBefore=6, spaceAfter=6),
        3: ParagraphStyle("h3", fontName=bold, fontSize=13, leading=17, spaceBefore=4, spaceAfter=4),
    }
    bullet = ParagraphStyle("li", parent=base, leftIndent=16, bulletIndent=4, spaceAfter=2)
    mono = ParagraphStyle("pre", fontName="Courier", fontSize=9, leading=11)
    story: list = []
    if not markdown:
        for para in re.split(r"\n\s*\n", body.replace("\r\n", "\n")):
            if para.strip():
                story.append(Paragraph(html.escape(para, quote=False).replace("\n", "<br/>"), base))
    else:
        in_code = False
        code: list[str] = []
        lines = body.replace("\r\n", "\n").split("\n")
        para: list[str] = []

        def flush() -> None:
            if para:
                story.append(Paragraph(_inline(" ".join(para)), base))
                para.clear()

        for line in lines:
            if line.strip().startswith("```"):
                if in_code:
                    story.append(Preformatted("\n".join(code), mono))
                    code.clear()
                    in_code = False
                else:
                    flush()
                    in_code = True
                continue
            if in_code:
                code.append(line)
                continue
            m = re.match(r"^(#{1,6})\s+(.*)$", line)
            if m:
                flush()
                story.append(Paragraph(_inline(m.group(2)), heads[min(3, len(m.group(1)))]))
                continue
            m = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$", line)
            if m:
                flush()
                story.append(Paragraph(_inline(m.group(1)), bullet, bulletText="•"))
                continue
            if re.match(r"^\s*(-{3,}|\*{3,})\s*$", line):
                flush()
                story.append(Spacer(1, 8))
                continue
            if not line.strip():
                flush()
                continue
            para.append(line.strip())
        flush()
        if code:
            story.append(Preformatted("\n".join(code), mono))
    if not story:
        story.append(Paragraph(" ", base))
    doc = SimpleDocTemplate(str(out), pagesize=paper, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=MARGIN, title=title[:120], author="Domovoi")
    doc.build(story, onFirstPage=_numbered, onLaterPages=_numbered)


# ─── Tables ──────────────────────────────────────────────────────────────

MIN_FONT = 7.0
MAX_FONT = 10.0


def _fit_columns(widths: list[float], avail: float) -> tuple[list[list[int]], float]:
    """Groups of column indexes (left to right) and the font size: shrink
    every column down to MIN_FONT, and past that split into pages."""
    total = sum(widths)
    if total <= avail:
        return [list(range(len(widths)))], MAX_FONT
    scale = avail / total
    size = MAX_FONT * scale
    if size >= MIN_FONT:
        return [list(range(len(widths)))], size
    factor = MIN_FONT / MAX_FONT
    groups: list[list[int]] = []
    cur: list[int] = []
    used = 0.0
    for i, w in enumerate(widths):
        cw = w * factor
        if cur and used + cw > avail:
            groups.append(cur)
            cur, used = [], 0.0
        cur.append(i)
        used += cw
    if cur:
        groups.append(cur)
    return groups, MIN_FONT


def table_document(out: Path, rows: list[list[str]], *, title: str, paper: tuple[int, int]) -> None:
    regular, bold = fonts()
    rows = [[("" if v is None else str(v))[:300] for v in r] for r in rows] or [[""]]
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    pw, ph = paper
    pagesize = (max(pw, ph), min(pw, ph)) if ncols > 8 else (min(pw, ph), max(pw, ph))
    avail = pagesize[0] - 2 * MARGIN
    widths = []
    for col in range(ncols):
        longest = max(pdfmetrics.stringWidth(r[col][:60], regular, MAX_FONT) for r in rows[:500])
        widths.append(min(max(longest + 8, 24), 260))
    groups, size = _fit_columns(widths, avail)
    cell = ParagraphStyle("cell", fontName=regular, fontSize=size, leading=size * 1.25)
    head = ParagraphStyle("head", fontName=bold, fontSize=size, leading=size * 1.25)
    story: list = []
    for gi, group in enumerate(groups):
        scale = size / MAX_FONT
        col_widths = [widths[i] * scale for i in group]
        if len(groups) == 1 and sum(col_widths) < avail:
            col_widths = [w * avail / sum(col_widths) for w in col_widths]
        data = []
        for ri, r in enumerate(rows):
            style = head if ri == 0 else cell
            data.append([Paragraph(html.escape(r[i], quote=False), style) for i in group])
        t = LongTable(data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.Color(0, 0, 0)),
            ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.Color(0.75, 0.75, 0.75)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        if gi:
            story.append(PageBreak())
        story.append(t)
    doc = SimpleDocTemplate(str(out), pagesize=pagesize, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=MARGIN, title=title[:120], author="Domovoi")
    doc.build(story, onFirstPage=_numbered, onLaterPages=_numbered)


# ─── Images ──────────────────────────────────────────────────────────────


def image_document(out: Path, image_path: Path, *, paper: tuple[int, int]) -> None:
    from PIL import Image, ImageOps

    with Image.open(image_path) as im:
        im.seek(0)
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        pw, ph = paper
        if (im.width > im.height) != (pw > ph):
            im = im.rotate(90, expand=True)
        buf = io.BytesIO()
        im.save(buf, "PNG" if im.mode == "L" else "JPEG", quality=92)
        iw, ih = im.size
    from reportlab.lib.utils import ImageReader

    c = canvas.Canvas(str(out), pagesize=paper, pageCompression=1)
    aw, ah = pw - 2 * MARGIN, ph - 2 * MARGIN
    s = min(aw / iw, ah / ih)
    dw, dh = iw * s, ih * s
    buf.seek(0)
    c.drawImage(ImageReader(buf), (pw - dw) / 2, (ph - dh) / 2, dw, dh)
    c.showPage()
    c.save()


# ─── Scans ───────────────────────────────────────────────────────────────


def scans_pdf(jpegs: Iterable[bytes], *, page_size: tuple[float, float], title: str, sink=None) -> bytes:
    """One PDF page per scanner JPEG, the JPEG bytes embedded verbatim.
    Written to ``sink`` (a binary file) when given, else returned."""
    from reportlab.lib.utils import ImageReader

    old = rl_config.useA85
    rl_config.useA85 = 0
    try:
        buf = sink if sink is not None else io.BytesIO()
        c = canvas.Canvas(buf, pagesize=page_size)
        c.setTitle(title[:120])
        c.setAuthor("Domovoi")
        for data in jpegs:
            reader = ImageReader(io.BytesIO(data))
            iw, ih = reader.getSize()
            pw, ph = page_size
            # The page follows the scan's own shape (a landscape scan gets a
            # landscape page of the same size).
            if (iw > ih) != (pw > ph):
                pw, ph = ph, pw
            c.setPageSize((pw, ph))
            c.drawImage(reader, 0, 0, pw, ph)
            c.showPage()
        c.save()
        return b"" if sink is not None else buf.getvalue()
    finally:
        rl_config.useA85 = old
