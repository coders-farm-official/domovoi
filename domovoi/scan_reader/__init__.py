"""The scan reader: "What does it say?" for scans (1.4.0, owner decision
2026-11-16: OCR first, then the picture model as a fallback).

A scan is saved to ``Documents/Scans`` as a PDF of the scanner's pictures
(:mod:`domovoi.printing.scans`), with no text in it. When it is saved the
core queues a read of it (:func:`runner.queue_scan`); the read runs in the
background, voice first (:mod:`.runner`), and saves each page's text in
the database (V065, :mod:`.store`) as it finishes:

* Tesseract OCR (:mod:`.ocr`) on each page, drawn in a capped worker
  process (:mod:`.pages`); its confidences decide whether the page came
  out readable;
* a page it couldn't read well goes to the vision slot
  (``ollama_vision_model``), whose text is worded as what the page
  **might say**, never as fact; with AI features off it is skipped and
  the page says it couldn't be read (OCR isn't AI: it still runs).

Once done the scan is ``readable``. Its text is found by the file's
SHA-256 wherever Domovoi reads a document (:mod:`domovoi.doc_extract.runner`),
so "Ask about this document", chat questions and "read me the scan" work
on it. "What does it say?" (:mod:`.says`) is a short plain summary from
the letter reader, plus the full text on request: on the dashboard's Scan
sheet and job log, in the apps (``/api/scan/reads``), and by voice after
"scan this" (:func:`answer_for_voice`).

Everything happens on the household's own server: no page, picture or
word leaves it.
"""

from __future__ import annotations


async def answer_for_voice(rel: str, context: dict | None = None, *, budget_sec: float = 20.0) -> tuple[str, bool]:
    """What a room says to "Want me to tell you what it says?" → yes, for
    the scan at ``rel``: (the words, whether there is more to read aloud).
    The person is waiting, so pages still unread are read now (OCR only;
    the background read asks the picture model for the rest later).
    It is the reader :func:`domovoi.printing.scans.register_reader` holds."""
    from domovoi.scan_reader import runner, says, store

    read = await store.latest_for_path(rel)
    if read is None or read["state"] in ("failed", "cancelled"):
        read_id = await runner.queue_scan(rel)
        if read_id is None:
            return "I can't read that scan here. It's in Documents, under Scans.", False
        read = await store.get(read_id)
    assert read is not None
    if read["state"] in ("queued", "running"):
        await runner.read_now(read["id"], budget_sec=budget_sec)
        read = await store.get(read["id"]) or read
    page_rows = await store.pages(read["id"])
    if not any(str(p.get("text") or "").strip() for p in page_rows):
        if read["state"] == "done":
            return "I couldn't read any words on that scan. It's in Documents, under Scans.", False
        return ("I'm still reading it. It's a hard one, so it'll take a little while. It's in Documents, "
                "under Scans, and the dashboard shows it once it's read."), False
    if read["state"] != "done":
        # Some pages wait for the picture model: say what's read so far.
        out = await says.summary(dict(read, summary=None), refresh=True, keep=False)
        return out["said"] + " Some of it was hard to read, and I'm still working on that part.", True
    out = await says.summary(read)
    return out["said"], True

