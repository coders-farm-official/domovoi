"""The page-drawing worker process: ``python -m domovoi.printing.render_worker DIR``.

PDFium is native code and a document is somebody else's bytes, so drawing
never happens in the core process: :mod:`domovoi.printing.render` starts
this module in a short-lived process with a time limit, and a bad PDF costs
one job, not the core. It reads ``DIR/request.json``, writes its outputs
into ``DIR`` and ``DIR/result.json`` (``{"ok": false, "error": ...}`` on a
refusal), and exits.

Actions:

* ``prepare`` — make ``doc.pdf`` from the input (a PDF is copied; text,
  markdown, a table or an image is drawn through
  :mod:`domovoi.printing.templates` at the chosen paper size), count its
  pages and draw thumbnails of the pages that will print.
* ``output`` — the bytes for the printer from ``doc.pdf``: the PDF itself
  (or only the chosen pages when the printer can't take ``page-ranges``),
  or PWG Raster / URF drawn page by page at the printer's resolution,
  fitted to the paper, grey when black-and-white was picked, back pages of
  a two-sided job turned the way the printer's ``sheet-back`` says.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import shutil
import sys
from pathlib import Path
from typing import Any

MAX_PAGES = 2000
THUMB_WIDTH = 150
THUMB_HEIGHT = 200
MAX_THUMBS = 12
MAX_TABLE_ROWS = 5000
MAX_TABLE_COLS = 100
MAX_TEXT_BYTES = 4 * 1024 * 1024
# A formatted document carries its pictures inside it, so it is read further.
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024

KIND_BY_EXT = {
    ".pdf": "pdf",
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image", ".gif": "image",
    ".bmp": "image", ".tif": "image", ".tiff": "image",
    ".txt": "text", ".md": "markdown", ".html": "document", ".htm": "document",
    ".csv": "table", ".xlsx": "table",
}


class Refused(Exception):
    pass


def _read_text(path: Path) -> str:
    raw = path.read_bytes()[:MAX_TEXT_BYTES]
    for enc in ("utf-8-sig", "utf-16"):
        try:
            if enc == "utf-16" and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp1252", "replace")


def _table_rows(path: Path, ext: str) -> list[list[str]]:
    if ext != ".xlsx":
        text = _read_text(path)
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = []
        for i, row in enumerate(csv.reader(io.StringIO(text), dialect)):
            if i >= MAX_TABLE_ROWS:
                break
            rows.append(row[:MAX_TABLE_COLS])
        return rows
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= MAX_TABLE_ROWS:
                break
            vals = ["" if v is None else str(v) for v in row[:MAX_TABLE_COLS]]
            rows.append(vals)
        while rows and not any(rows[-1]):
            rows.pop()
        if rows:
            width = max(len(r) for r in rows)
            while width and all((len(r) < width or not r[width - 1]) for r in rows):
                width -= 1
            rows = [r[:width] for r in rows]
        return rows
    finally:
        wb.close()


def prepare(req: dict[str, Any], d: Path) -> dict[str, Any]:
    from domovoi.printing import templates

    src = d / "input"
    kind = req["kind"]
    paper = tuple(req["paper"])
    out = d / "doc.pdf"
    notes: list[str] = []
    if kind == "pdf":
        shutil.copyfile(src, out)
    elif kind == "image":
        templates.image_document(out, src, paper=paper)
    elif kind in ("text", "markdown"):
        templates.text_document(out, _read_text(src), title=req.get("title") or "", paper=paper,
                                markdown=kind == "markdown")
    elif kind == "document":
        from domovoi.richdoc import html_to_markdown

        markup = src.read_bytes()[:MAX_DOCUMENT_BYTES].decode("utf-8", "replace")
        templates.text_document(out, html_to_markdown(markup), title=req.get("title") or "", paper=paper,
                                markdown=True)
        notes.append("Printed as its text, without pictures or fonts.")
    elif kind == "table":
        rows = _table_rows(src, str(req.get("ext") or ".csv").lower())
        if not rows:
            raise Refused("That spreadsheet is empty.")
        templates.table_document(out, rows, title=req.get("title") or "", paper=paper)
        notes.append("Printed as a plain table.")
    elif kind == "testpage":
        o = req["testpage"]
        templates.test_page(out, printer_name=o["printer_name"], model=o["model"], paper=paper,
                            paper_label=o["paper_label"], sides_label=o["sides_label"],
                            color_label=o["color_label"], color=bool(o["color"]))
    else:
        raise Refused("This kind of file can't be printed.")

    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(out))
    except pdfium.PdfiumError as e:
        msg = str(e).lower()
        if "password" in msg:
            raise Refused("This PDF has a password. Save a copy without the password, then print that.") from e
        raise Refused("That PDF couldn't be read.") from e
    try:
        total = len(doc)
        if total < 1:
            raise Refused("That document has no pages.")
        if total > MAX_PAGES:
            raise Refused("That document has more pages than Domovoi will print (%d)." % MAX_PAGES)
        pages = [p for p in select_pages(req.get("pages"), total)]
        from domovoi.printing import limits

        # Every page's size, before anything is drawn (reading a size is
        # cheap; a 2 x 14,400 pt "page" is refused here, not rendered).
        for n in pages:
            w_pt, h_pt = doc.get_page_size(n - 1)
            odd = limits.check_page_size(w_pt, h_pt)
            if odd:
                raise Refused(odd)
        thumbs = []
        gray = bool(req.get("gray"))
        for n in pages[:MAX_THUMBS] if req.get("thumbs", True) else []:
            page = doc[n - 1]
            w, h = page.get_size()
            # Fit the box both ways: a tall thin page must not become a
            # 150 x 1,080,000 px thumbnail.
            scale = min(THUMB_WIDTH / max(w, 1), THUMB_HEIGHT / max(h, 1))
            bm = page.render(scale=scale, grayscale=gray, fill_color=(255, 255, 255, 255))
            img = bm.to_pil()
            img = img.convert("L" if gray else "RGB")
            buf = io.BytesIO()
            img.save(buf, "PNG", optimize=True)
            thumbs.append({"page": n, "src": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()})
            page.close()
        sizes = []
        for i in range(min(total, 3)):
            w, h = doc[i].get_size()
            sizes.append([round(w), round(h)])
    finally:
        doc.close()
    return {"ok": True, "pages_total": total, "pages": pages, "thumbs": thumbs, "notes": notes, "sizes": sizes}


def select_pages(spec: Any, total: int) -> list[int]:
    from domovoi.printing.options import parse_page_ranges

    if not spec:
        return list(range(1, total + 1))
    ranges = parse_page_ranges(str(spec))
    out = []
    for lo, hi in ranges:
        for n in range(lo, min(hi, total) + 1):
            if n not in out:
                out.append(n)
    if not out:
        raise Refused("Those pages aren't in the document (it has %d)." % total)
    return out


def _fit_render(page, paper_px: tuple[int, int], dpi: int, gray: bool):
    """The page drawn to fit the paper (turned when its shape is the other
    way round), centred on a white sheet of exactly paper_px."""
    from PIL import Image

    w_pt, h_pt = page.get_size()
    W, H = paper_px
    rotate = 0
    if (w_pt > h_pt) != (W > H) and abs(w_pt - h_pt) > 1:
        rotate = 90
        w_pt, h_pt = h_pt, w_pt
    scale_px = min(W / (w_pt * dpi / 72.0), H / (h_pt * dpi / 72.0))
    scale = dpi / 72.0 * scale_px
    bm = page.render(scale=scale, rotation=rotate, grayscale=gray, rev_byteorder=not gray,
                     fill_color=(255, 255, 255, 255))
    img = bm.to_pil().convert("L" if gray else "RGB")
    if img.size == (W, H):
        return img
    if img.width > W or img.height > H:
        img = img.crop((0, 0, min(img.width, W), min(img.height, H)))
    sheet = Image.new("L" if gray else "RGB", (W, H), 255 if gray else (255, 255, 255))
    sheet.paste(img, ((W - img.width) // 2, (H - img.height) // 2))
    return sheet


def _pil_transform(page, cross: int, feed: int):
    from PIL import Image

    from domovoi.printing.raster import Page

    img = Image.frombytes("L" if page.bpp == 1 else "RGB", (page.width, page.height), page.data)
    if cross == -1 and feed == -1:
        img = img.transpose(Image.Transpose.ROTATE_180)
    elif cross == -1:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    elif feed == -1:
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return Page(page.w_pt, page.h_pt, page.width, page.height, page.bpp, img.tobytes())


def output(req: dict[str, Any], d: Path) -> dict[str, Any]:
    import pypdfium2 as pdfium

    from domovoi.printing import raster

    fmt = req["format"]
    src = d / "doc.pdf"
    out = d / "out.bin"
    doc = pdfium.PdfDocument(str(src))
    try:
        total = len(doc)
        pages = select_pages(req.get("pages"), total)
        repeat = max(1, int(req.get("repeat_copies") or 1))
        sides = req.get("sides") or "one-sided"
        if fmt == "pdf":
            if req.get("subset") and pages != list(range(1, total + 1)) or repeat > 1:
                new = pdfium.PdfDocument.new()
                seq = []
                for _ in range(repeat):
                    seq += [n - 1 for n in pages]
                    if sides != "one-sided" and len(pages) % 2 == 1 and repeat > 1:
                        seq.append(None)
                for idx in seq:
                    if idx is None:
                        w, h = doc[pages[-1] - 1].get_size()
                        new.new_page(w, h)
                    else:
                        new.import_pages(doc, [idx])
                new.save(str(out))
                new.close()
            else:
                shutil.copyfile(src, out)
            return {"ok": True, "pages_sent": len(pages) * repeat, "bytes": out.stat().st_size}
        dpi = int(req["dpi"])
        paper = tuple(req["paper"])
        paper_px = (round(paper[0] * dpi / 72.0), round(paper[1] * dpi / 72.0))
        from domovoi.printing import limits

        too_big = limits.check_sheet_pixels(*paper_px)
        if too_big:
            raise Refused(too_big)
        gray = bool(req.get("gray"))
        seq = []
        for _ in range(repeat):
            seq += pages
            if sides != "one-sided" and len(pages) % 2 == 1 and repeat > 1:
                seq.append(0)  # a blank back, so every copy starts on a new sheet
        with out.open("wb") as f:
            if fmt == "pwg":
                writer = raster.PwgWriter(f, dpi=dpi, typ="sgray_8" if gray else "srgb_8",
                                          total_pages=len(seq), sides=sides,
                                          sheet_back=req.get("sheet_back"))
                writer.transformer = _pil_transform
            else:
                writer = raster.UrfWriter(f, dpi=dpi, total_pages=len(seq), sides=sides)
            for n in seq:
                if n == 0:
                    from PIL import Image

                    img = Image.new("L" if gray else "RGB", paper_px, 255 if gray else (255, 255, 255))
                else:
                    page = doc[n - 1]
                    img = _fit_render(page, paper_px, dpi, gray)
                    page.close()
                writer.write(raster.Page(paper[0], paper[1], img.width, img.height, 1 if gray else 3,
                                         img.tobytes()))
                del img
        return {"ok": True, "pages_sent": len(seq), "bytes": out.stat().st_size}
    finally:
        doc.close()


def main(argv: list[str]) -> int:
    d = Path(argv[1])
    req = json.loads((d / "request.json").read_text(encoding="utf-8"))
    from domovoi.printing import limits

    try:
        limits.apply(int(req.get("memory_bytes") or limits.worker_memory_bytes()),
                     int(req.get("cpu_seconds") or 300))
    except limits.LimitError as e:
        (d / "result.json").write_text(json.dumps(
            {"ok": False, "error": "That document couldn't be drawn for printing.", "detail": str(e)}),
            encoding="utf-8")
        return 0
    try:
        from PIL import Image

        # Pictures and scanner pages past this are refused, not decoded.
        Image.MAX_IMAGE_PIXELS = limits.MAX_SHEET_PIXELS
        if req["action"] == "prepare":
            result = prepare(req, d)
        elif req["action"] == "output":
            result = output(req, d)
        else:
            result = {"ok": False, "error": "unknown action"}
    except Refused as e:
        result = {"ok": False, "error": str(e)}
    except MemoryError:
        result = {"ok": False, "error": "That document needs more memory to draw than Domovoi allows."}
    except Exception as e:  # noqa: BLE001 — a bad document is a failed job
        result = {"ok": False, "error": "That document couldn't be drawn for printing.", "detail": repr(e)[:300]}
    (d / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
