"""Print jobs: preview, send, follow, cancel, and the job log.

The path of a job (plan parts 1, 6 and 7):

1. the file kind decides whether it can print at all (:data:`KIND_BY_EXT`;
   uploaded Word/ODT files can't be laid out faithfully, so they don't);
2. the printer: the one asked for, the room's default, the household
   default, or the only one;
3. options resolved against the printer (:mod:`domovoi.printing.options`);
4. the document drawn as a PDF in the worker process, its pages counted;
5. the caps checked (:func:`check_limits`): pages, copies, sheets per job,
   jobs per hour per device, sheets per day for the household;
6. the bytes for the printer made in the worker: the PDF as it is when the
   printer takes PDF, else PWG Raster or URF at the printer's resolution;
7. Validate-Job (an option the printer refuses is dropped, with a note),
   then Create-Job + Send-Document (Print-Job for printers without them);
8. the job followed with Get-Job-Attributes until it ends; a voice job
   that fails within 10 minutes is said once in the room that asked.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domovoi.printing import capabilities as caps_mod
from domovoi.printing import devices, http11, ipp, options, render, store
from domovoi.printing.client import IppClient, IppError

# The same sentence the test page carries; not imported from templates,
# which pulls ReportLab into the core process.
LIGHT_GREY_NOTE = "Very light greys (under about 10-15%) may not print on some lasers."

log = logging.getLogger(__name__)

KIND_BY_EXT = {
    ".pdf": "pdf",
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image", ".gif": "image",
    ".bmp": "image", ".tif": "image", ".tiff": "image",
    ".txt": "text", ".md": "markdown",
    ".csv": "table", ".xlsx": "table",
}
PDF_FIRST_EXTS = (".docx", ".odt", ".doc", ".rtf")
PDF_FIRST_MESSAGE = "To print a Word file, save it as PDF in Word or Docs, then print the PDF."
CANT_PRINT = "This kind of file can't be printed."
MAX_INPUT_BYTES = 100 * 1024 * 1024
WATCH_FAST_SEC = 2.0
WATCH_FAST_FOR_SEC = 600.0
ANNOUNCE_WITHIN_SEC = 600.0

_tasks: set[asyncio.Task] = set()


class PrintRefused(Exception):
    """A sentence for people, with the HTTP status it maps to."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Who:
    label: str
    device_key: str
    source: str = "dashboard"      # dashboard | app | voice | test
    room_id: str | None = None
    speaker: str | None = None


def printable_info() -> dict[str, Any]:
    return {"exts": sorted(KIND_BY_EXT), "pdf_first_exts": list(PDF_FIRST_EXTS),
            "pdf_first_message": PDF_FIRST_MESSAGE}


def kind_of(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext in PDF_FIRST_EXTS:
        raise PrintRefused(PDF_FIRST_MESSAGE, 415)
    kind = KIND_BY_EXT.get(ext)
    if kind is None:
        raise PrintRefused(CANT_PRINT, 415)
    return kind


# ─── JSON ────────────────────────────────────────────────────────────────

_STATE_WORDS = {"queued": "queued", "printing": "printing", "done": "done", "failed": "failed",
                "canceled": "canceled"}


def job_json(row: dict[str, Any]) -> dict[str, Any]:
    state = row["state"]
    progress = _STATE_WORDS.get(state, state)
    if state == "printing" and row.get("pages"):
        progress = "printing · %d of %d" % (min(row.get("pages_done") or 0, row["pages"] * row["copies"]),
                                          row["pages"] * row["copies"])
    return {
        "id": row["id"], "printer_id": row.get("printer_id"), "printer_name": row.get("printer_name") or "",
        "title": row.get("title") or "", "pages": row.get("pages") or 0, "copies": row.get("copies") or 1,
        "sheets": row.get("sheets") or 0, "options": row.get("options") or {}, "state": state,
        "pages_done": row.get("pages_done") or 0, "progress": progress, "error": row.get("error"),
        "who": row.get("who") or "", "source": row.get("source") or "",
        "created_at": store.iso(row.get("created_at")), "finished_at": store.iso(row.get("finished_at")),
        "can_cancel": state in ("queued", "printing"),
    }


# ─── Limits ──────────────────────────────────────────────────────────────


async def check_limits(*, pages: int, copies: int, sheets: int, device_key: str, source: str) -> None:
    lim = await store.get_limits()
    if pages > lim["max_pages_per_job"]:
        raise PrintRefused(
            "That's %d pages, more than this house allows in one job (%d). An admin can change the limit."
            % (pages, lim["max_pages_per_job"]), 413)
    if copies > lim["max_copies"]:
        raise PrintRefused(
            "That's %d copies, more than this house allows in one job (%d). An admin can change the limit."
            % (copies, lim["max_copies"]), 413)
    if sheets > lim["max_sheets_per_job"]:
        raise PrintRefused(
            "That's %d sheets of paper, more than this house allows in one job (%d). An admin can change the limit."
            % (sheets, lim["max_sheets_per_job"]), 413)
    jobs_hour, sheets_day = await store.usage(device_key)
    if source != "test" and jobs_hour >= lim["max_jobs_per_hour_per_device"]:
        raise PrintRefused(
            "That's more print jobs from here this hour than this house allows (%d). Try again later."
            % lim["max_jobs_per_hour_per_device"], 429)
    if sheets_day + sheets > lim["max_sheets_per_day"]:
        raise PrintRefused(
            "That would go over this house's %d sheets of paper a day. An admin can change the limit."
            % lim["max_sheets_per_day"], 429)


def over_limit_text(pages: int, copies: int, sheets: int, lim: dict[str, int]) -> str | None:
    if pages > lim["max_pages_per_job"]:
        return ("That's %d pages, more than this house allows in one job (%d). An admin can change the limit."
                % (pages, lim["max_pages_per_job"]))
    if copies > lim["max_copies"]:
        return ("That's %d copies, more than this house allows in one job (%d). An admin can change the limit."
                % (copies, lim["max_copies"]))
    if sheets > lim["max_sheets_per_job"]:
        return ("That's %d sheets of paper, more than this house allows in one job (%d). An admin can change "
                "the limit." % (sheets, lim["max_sheets_per_job"]))
    return None


# ─── Preview ─────────────────────────────────────────────────────────────


async def _resolve(row: dict[str, Any], wanted: dict[str, Any], *, max_copies: int) -> options.Resolved:
    caps = row.get("capabilities") or {}
    try:
        return options.resolve(caps, wanted or {}, two_sided=row.get("two_sided_default"),
                               color=row.get("color_default"), max_copies=max(max_copies, 999))
    except options.OptionError as e:
        raise PrintRefused(str(e), 400) from e


async def preview(
    src: Path, *, filename: str, title: str, printer_id: int | None, wanted: dict[str, Any],
    room_id: str | None = None,
) -> dict[str, Any]:
    kind = kind_of(filename)
    row = await _pick(printer_id, room_id)
    lim = await store.get_limits()
    r = await _resolve(row, wanted, max_copies=lim["max_copies"])
    paper = caps_mod.media_points(r.media)
    with render.Workdir() as w:
        shutil.copyfile(src, w.input())
        try:
            prep = await render.prepare(w, kind=kind, paper=list(paper), title=title, pages=r.pages_spec,
                                        gray=r.gray, thumbs=True, ext=Path(filename).suffix.lower())
        except render.RenderFailed as e:
            raise PrintRefused(str(e), 422) from e
    pages = prep["pages"]
    sheets = options.sheets_for(len(pages), r.copies, r.sides)
    notes = list(prep.get("notes") or [])
    notes.append(LIGHT_GREY_NOTE)
    return {
        "printer_id": row["id"], "printer_name": row["name"], "title": title, "kind": kind,
        "pages_total": prep["pages_total"], "pages": pages, "copies": r.copies, "sheets": sheets,
        "options": r.as_json(), "summary": options.summary(len(pages), sheets, r.sides, r.copies),
        "thumbs": prep["thumbs"], "notes": notes, "adjustments": r.adjustments,
        "over_limit": over_limit_text(len(pages), r.copies, sheets, lim),
        "choices": options.choices(row.get("capabilities") or {}, lim["max_copies"]),
    }


async def _pick(printer_id: int | None, room_id: str | None) -> dict[str, Any]:
    try:
        row = await devices.pick_printer(printer_id, room_id)
        devices.ipp_printer(row)  # cert / LAN problems surface before any work
        return row
    except devices.AmbiguousPrinter as e:
        raise PrintRefused(str(e), 409) from e
    except devices.DeviceError as e:
        raise PrintRefused(str(e), 409) from e


async def count_pages(src: Path, *, filename: str, title: str, row: dict[str, Any],
                      wanted: dict[str, Any]) -> tuple[int, options.Resolved]:
    """Pages that would print, for voice's "That's 2 pages … Print it?"."""
    kind = kind_of(filename)
    lim = await store.get_limits()
    r = await _resolve(row, wanted, max_copies=lim["max_copies"])
    paper = caps_mod.media_points(r.media)
    with render.Workdir() as w:
        shutil.copyfile(src, w.input())
        try:
            prep = await render.prepare(w, kind=kind, paper=list(paper), title=title, pages=r.pages_spec,
                                        gray=r.gray, thumbs=False, ext=Path(filename).suffix.lower())
        except render.RenderFailed as e:
            raise PrintRefused(str(e), 422) from e
    return len(prep["pages"]), r


# ─── Submit ──────────────────────────────────────────────────────────────


async def submit(
    src: Path | None, *, filename: str, title: str, printer_id: int | None, wanted: dict[str, Any],
    who: Who, library_id: str | None = None, path: str | None = None, kind: str | None = None,
    testpage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind = kind or kind_of(filename)
    row = await _pick(printer_id, who.room_id)
    lim = await store.get_limits()
    r = await _resolve(row, wanted, max_copies=lim["max_copies"])
    if r.copies > lim["max_copies"]:
        raise PrintRefused(
            "That's %d copies, more than this house allows in one job (%d). An admin can change the limit."
            % (r.copies, lim["max_copies"]), 413)
    paper = caps_mod.media_points(r.media)
    w = render.Workdir()
    try:
        if src is not None:
            shutil.copyfile(src, w.input())
        try:
            prep = await render.prepare(
                w, kind=kind, paper=list(paper), title=title, pages=r.pages_spec, gray=r.gray, thumbs=False,
                testpage=testpage, ext=Path(filename).suffix.lower(),
            )
        except render.RenderFailed as e:
            raise PrintRefused(str(e), 422) from e
        pages = prep["pages"]
        sheets = options.sheets_for(len(pages), r.copies, r.sides)
        await check_limits(pages=len(pages), copies=r.copies, sheets=sheets, device_key=who.device_key,
                           source=who.source)
        job_id = await store.insert_job({
            "printer_id": row["id"], "printer_name": row["name"], "title": title[:200],
            "library_id": library_id, "path": (path or "")[:1000] or None, "pages": len(pages),
            "copies": r.copies, "sheets": sheets, "options": r.as_json(), "state": "queued",
            "who": who.label[:120], "device_key": who.device_key[:120], "source": who.source,
            "room_id": who.room_id, "speaker": who.speaker,
        })
    except BaseException:
        w.cleanup()
        raise
    task = asyncio.create_task(_send_and_follow(job_id, row, r, w, pages, prep["pages_total"]),
                               name="print-job-%d" % job_id)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    job = await store.get_job(job_id)
    assert job is not None
    return job_json(job)


async def _drop_unsupported(client: IppClient, attrs: list[ipp.Attr], fmt: str, title: str) -> list[ipp.Attr]:
    try:
        v = await client.validate_job(attrs, document_format=fmt, job_name=title)
    except (IppError, http11.HttpError) as e:
        log.debug("printing: Validate-Job failed (%s); sending as is", e)
        return attrs
    unsupported = v.group(ipp.UNSUPPORTED)
    if v.status == ipp.STATUS_ATTRIBUTES_NOT_SUPPORTED or unsupported:
        names = set(unsupported)
        kept = [a for a in attrs if a.name not in names]
        if names:
            log.info("printing: printer refused %s; sending without them", sorted(names))
        return kept
    return attrs


async def _send_and_follow(job_id: int, row: dict[str, Any], r: options.Resolved, w: render.Workdir,
                           pages: list[int], pages_total: int) -> None:
    caps = row.get("capabilities") or {}
    try:
        job = await store.get_job(job_id)
        if job is None or job["state"] != "queued":
            return
        printer = devices.ipp_printer(row)
        send_copies = int(caps.get("copies_max") or 1) >= r.copies
        all_pages = pages == list(range(1, pages_total + 1))
        page_ranges = None
        if r.format == "application/pdf":
            if not all_pages and caps.get("page_ranges"):
                page_ranges = options.parse_page_ranges(r.pages_spec or "")
                await render.output(w, format="pdf", pages=None, repeat_copies=1 if send_copies else r.copies,
                                    sides=r.sides)
            else:
                await render.output(w, format="pdf", pages=r.pages_spec, subset=not all_pages,
                                    repeat_copies=1 if send_copies else r.copies, sides=r.sides)
        else:
            await render.output(
                w, format="pwg" if r.format == "image/pwg-raster" else "urf", dpi=r.dpi,
                paper=list(caps_mod.media_points(r.media)), gray=r.gray, pages=r.pages_spec, sides=r.sides,
                sheet_back=caps.get("sheet_back"), repeat_copies=1 if send_copies else r.copies,
            )
        attrs = options.job_attributes(r, caps, send_copies=send_copies, page_ranges=page_ranges)
        client = IppClient(printer)
        job = await store.get_job(job_id)
        if job is None or job["state"] != "queued":
            return
        title = (job.get("title") or "Domovoi")[:200]
        attrs = await _drop_unsupported(client, attrs, r.format, title)
        ops = caps.get("operations") or []
        data = [w.output()]
        ipp_id: int
        if 0x0005 in ops and 0x0006 in ops:
            try:
                ipp_id = await client.create_job(attrs, job_name=title)
                await store.update_job(job_id, ipp_job_id=ipp_id, state="printing")
                await client.send_document(ipp_id, data, document_format=r.format)
            except IppError as e:
                if e.status != ipp.STATUS_OPERATION_NOT_SUPPORTED:
                    raise
                ipp_id = await client.print_job(attrs, data, document_format=r.format, job_name=title)
        else:
            ipp_id = await client.print_job(attrs, data, document_format=r.format, job_name=title)
        await store.update_job(job_id, ipp_job_id=ipp_id, state="printing")
    except http11.CertificateChanged as e:
        await devices.note_cert_change(row, e.seen)
        await _fail(job_id, row, devices.CERT_MESSAGE.format(the=devices.The(row["name"])))
        return
    except (devices.DeviceError, render.RenderFailed, options.OptionError) as e:
        await _fail(job_id, row, str(e))
        return
    except IppError as e:
        msg = ("%s can't print that kind of file." % devices.The(row["name"])
               if e.status == ipp.STATUS_FORMAT_NOT_SUPPORTED else
               "%s refused the job." % devices.The(row["name"]))
        log.warning("printing: job %d refused by %s: %s", job_id, row.get("name"), e)
        await _fail(job_id, row, msg)
        return
    except http11.HttpError as e:
        log.warning("printing: job %d: %s didn't answer: %s", job_id, row.get("name"), e)
        await _fail(job_id, row, "%s isn't answering. Is it switched on?" % devices.The(row["name"]))
        return
    except Exception as e:  # noqa: BLE001 — a job never takes the core down
        log.exception("printing: job %d failed", job_id)
        await _fail(job_id, row, "Printing failed: %s" % type(e).__name__)
        return
    finally:
        w.cleanup()
    started = time.monotonic()
    while time.monotonic() - started < WATCH_FAST_FOR_SEC:
        await asyncio.sleep(WATCH_FAST_SEC)
        try:
            done = await follow(job_id)
        except Exception as e:  # noqa: BLE001
            log.debug("printing: follow job %d: %s", job_id, e)
            done = False
        if done:
            return


async def _fail(job_id: int, row: dict[str, Any], message: str) -> None:
    await store.update_job(job_id, state="failed", error=message[:300], finished_at="now")
    await announce_failure(job_id)


async def follow(job_id: int) -> bool:
    """One Get-Job-Attributes for a job in flight; True when it has ended."""
    job = await store.get_job(job_id)
    if job is None or job["state"] not in ("queued", "printing"):
        return True
    if not job.get("ipp_job_id"):
        return False
    row = await store.get_printer(job["printer_id"]) if job.get("printer_id") else None
    if row is None:
        await store.update_job(job_id, state="failed", error="The printer was removed.", finished_at="now")
        return True
    printer = devices.ipp_printer(row)
    client = IppClient(printer, timeout=10.0)
    try:
        ja = await client.get_job_attributes(int(job["ipp_job_id"]))
    except IppError as e:
        if e.status == 0x0406:  # not-found: the printer forgot it (finished long ago)
            await store.update_job(job_id, state="done", finished_at="now")
            return True
        raise
    state = ipp.first(ja, "job-state", 0)
    done_pages = ipp.first(ja, "job-impressions-completed", None)
    updates: dict[str, Any] = {}
    if isinstance(done_pages, int):
        updates["pages_done"] = max(0, done_pages)
    if state in (3, 4):
        updates["state"] = "queued"
    elif state in (5, 6):
        updates["state"] = "printing"
        if state == 6:
            status = await devices.refresh_status(row, force=True)
            words = status.get("words") or []
            updates["error"] = words[0] if words else "%s has stopped." % devices.The(row["name"])
        elif job.get("error"):
            updates["error"] = None
    elif state == 9:
        updates.update(state="done", finished_at="now", error=None)
    elif state == 7:
        updates.update(state="canceled", finished_at="now")
    elif state == 8:
        status = await devices.refresh_status(row, force=True)
        words = status.get("words") or []
        message = ipp.first(ja, "job-state-message", "") or ""
        err = words[0] if words else "%s stopped the job." % devices.The(row["name"])
        if message and not words:
            err += " (%s)" % str(message)[:120]
        updates.update(state="failed", finished_at="now", error=err)
    if updates:
        await store.update_job(job_id, **updates)
    ended = state in ipp.JOB_TERMINAL
    if updates.get("state") == "failed" or (state == 6 and updates.get("error")):
        await announce_failure(job_id)
    if ended:
        await devices.refresh_status(row, force=True)
    return ended


async def follow_all() -> int:
    """The worker's tick: every job still in flight, once."""
    n = 0
    for job in await store.active_jobs():
        try:
            await follow(job["id"])
        except Exception as e:  # noqa: BLE001
            log.debug("printing: follow job %s: %s", job["id"], e)
        n += 1
    return n


async def cancel(job_id: int) -> dict[str, Any]:
    job = await store.get_job(job_id)
    if job is None:
        raise PrintRefused("No such print job.", 404)
    if job["state"] not in ("queued", "printing"):
        return job_json(job)
    if job.get("ipp_job_id") and job.get("printer_id"):
        row = await store.get_printer(job["printer_id"])
        if row is not None:
            try:
                client = IppClient(devices.ipp_printer(row), timeout=10.0)
                await client.cancel_job(int(job["ipp_job_id"]))
            except (devices.DeviceError, IppError, http11.HttpError) as e:
                log.info("printing: Cancel-Job for job %d: %s", job_id, e)
    await store.update_job(job_id, state="canceled", finished_at="now")
    job = await store.get_job(job_id)
    assert job is not None
    return job_json(job)


# ─── Spoken failures ─────────────────────────────────────────────────────


async def announce_failure(job_id: int) -> None:
    """A voice job that fails (or stops on the printer) within 10 minutes
    is said ONCE, in the room that asked (owner answer Q19)."""
    job = await store.get_job(job_id)
    if job is None or job.get("source") != "voice" or job.get("announced") or not job.get("room_id"):
        return
    created = job.get("created_at")
    if created is not None:
        import datetime as dt

        age = (dt.datetime.now(dt.timezone.utc) - created).total_seconds()
        if age > ANNOUNCE_WITHIN_SEC:
            return
    text = job.get("error") or "Printing %s didn't work." % (job.get("title") or "that")
    await store.update_job(job_id, announced=True)
    from domovoi.speech_sanitize import sanitize_for_speech
    from domovoi.sdk.speech import SpeechAPI

    try:
        reached = await SpeechAPI("core").announce(job["room_id"], sanitize_for_speech(text))
        if not reached:
            log.info("printing: couldn't say the failure of job %d in %s (room offline)", job_id, job["room_id"])
    except Exception as e:  # noqa: BLE001
        log.info("printing: announcing job %d failed: %s", job_id, e)


# ─── Test page ───────────────────────────────────────────────────────────


async def test_page(printer_id: int, who: Who) -> dict[str, Any]:
    row = await store.get_printer(printer_id)
    if row is None or not row.get("ipp_address"):
        raise PrintRefused("That printer isn't set up.", 404)
    caps = row.get("capabilities") or {}
    d = options.defaults(caps, two_sided=row.get("two_sided_default"), color=row.get("color_default"))
    tp = {
        "printer_name": row["name"], "model": row.get("make_and_model") or "",
        "paper_label": caps_mod.media_label(d["media"]),
        "sides_label": {"one-sided": "one-sided", "two-sided-long-edge": "two-sided (long edge)",
                        "two-sided-short-edge": "two-sided (short edge)"}.get(d["sides"], d["sides"]),
        "color_label": "colour" if d["color"] == "color" else "black and white",
        "color": d["color"] == "color",
    }
    return await submit(None, filename="test-page.pdf", title="Domovoi test page", printer_id=printer_id,
                        wanted={"sides": "one-sided", "copies": 1}, who=who, kind="testpage", testpage=tp)


def file_too_large(size: int) -> bool:
    return size > MAX_INPUT_BYTES
