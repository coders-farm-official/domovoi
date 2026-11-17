"""The page-picture worker: ``python -m domovoi.scan_reader.page_worker DIR``.

A scan in Documents is a file anyone with write access could replace, and
PDFium is native code, so pages are never drawn in the core process:
:mod:`domovoi.scan_reader.pages` starts this module in a short-lived,
memory- and CPU-capped process (:mod:`domovoi.doc_extract.limits`). It
reads ``DIR/request.json``:

* ``{"action": "count", "path"}`` — the page count;
* ``{"action": "render", "path", "page", "dpi", "out"}`` — page ``page``
  (1-based) drawn in grey at ``dpi`` into the PNG ``out``, smaller when
  that would pass :data:`MAX_PIXELS`;

and writes ``DIR/result.json`` (``{"ok": false, "error"}`` on a refusal).
No network: nothing here opens a connection.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: The most pixels a drawn page may have (an A3 page at 300 dpi is ~17.4 M).
MAX_PIXELS = 40_000_000
MAX_PAGES = 500


def main(argv: list[str]) -> int:
    d = Path(argv[1])
    req = json.loads((d / "request.json").read_text(encoding="utf-8"))

    def done(result: dict) -> int:
        (d / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return 0

    from domovoi.doc_extract import limits

    try:
        limits.apply(int(req.get("memory_bytes") or limits.worker_memory_bytes()), int(req.get("cpu_seconds") or 60))
    except limits.LimitError as e:
        return done({"ok": False, "error": "The scan couldn't be opened.", "detail": type(e).__name__})
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(req["path"]))
    except Exception as e:  # noqa: BLE001 — damaged, a password, not a PDF
        return done({"ok": False, "error": "The scan couldn't be opened.", "detail": type(e).__name__})
    try:
        total = len(doc)
        if req.get("action") == "count":
            if total < 1 or total > MAX_PAGES:
                return done({"ok": False, "error": "The scan has no pages Domovoi can read."})
            return done({"ok": True, "pages": total})
        n = int(req.get("page") or 0)
        if not 1 <= n <= total:
            return done({"ok": False, "error": "That page isn't in the scan."})
        page = doc[n - 1]
        try:
            w, h = page.get_width(), page.get_height()
            scale = float(req.get("dpi") or 300) / 72.0
            if w * h * scale * scale > MAX_PIXELS:
                scale = (MAX_PIXELS / max(1.0, w * h)) ** 0.5
            bitmap = page.render(scale=scale, grayscale=True)
            try:
                bitmap.to_pil().save(str(req["out"]), "PNG")
            finally:
                bitmap.close()
        finally:
            page.close()
        return done({"ok": True})
    except MemoryError:
        return done({"ok": False, "error": "That page needs more memory to read than Domovoi allows."})
    except Exception as e:  # noqa: BLE001 — a bad page is a failed page
        return done({"ok": False, "error": "That page couldn't be drawn.", "detail": type(e).__name__})
    finally:
        doc.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
