"""Scanning to Documents over eSCL.

A scan is a short session: it scans the glass (one page) or everything in
the feeder, then waits for "another page" or "done". Done puts every page
into one PDF in ``Documents/Scans/``, named by date ("Scan 2026-10-21
15.40.pdf"), the scanner's JPEGs embedded byte for byte. A session nobody
finishes is saved anyway after :data:`IDLE_SAVE_SEC` (pages are never
thrown away silently); a scan that breaks off after some pages keeps them,
named "(incomplete)".

Defaults (owner answer Q11): 300 dpi colour, the feeder when it's loaded,
else the glass. eSCL can't tell whether anything is on the glass, so the
voice flow asks first and the dashboard simply scans.

One scan at a time per scanner (:data:`ScanBusy`), at most
``print_limits.max_scan_pages`` pages per scan.

**The seam for "what does this letter say?"** belongs to the documents
work: :func:`register_reader` installs the reader; until one exists
``readable`` is False everywhere, the dashboard and app show no "What
does it say?" button and voice doesn't offer to read (no misleading
controls).
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import io
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from domovoi.printing import devices, escl, http11, options, store

log = logging.getLogger(__name__)

SCANS_FOLDER = "Scans"
IDLE_SAVE_SEC = 600.0
FINISHED_KEEP_SEC = 3600.0

ScanReader = Callable[[str, dict[str, Any]], Awaitable[str]]
_reader: ScanReader | None = None


def register_reader(reader: ScanReader | None) -> None:
    """The documents work installs ``async (documents_rel_path, context) ->
    spoken text`` here."""
    global _reader
    _reader = reader


def reader() -> ScanReader | None:
    return _reader


class ScanRefused(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Session:
    id: str
    printer: dict[str, Any]
    color: str
    resolution: int
    source: str            # what was asked: auto | platen | adf | adf_duplex
    who: str
    origin: str            # dashboard | app | voice
    room_id: str | None
    pages: list[bytes] = field(default_factory=list)
    thumbs: list[dict[str, Any]] = field(default_factory=list)
    state: str = "scanning"
    source_used: str | None = None
    error: str | None = None
    name: str | None = None
    path: str | None = None
    incomplete: bool = False
    touched: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def json(self) -> dict[str, Any]:
        return {
            "id": self.id, "printer_id": self.printer["id"], "scanner_name": self.printer["name"],
            "state": self.state, "source_used": self.source_used, "pages": len(self.pages),
            "thumbs": self.thumbs, "can_scan_more": self.state == "waiting",
            "name": self.name, "library_id": "core:documents", "path": self.path,
            "incomplete": self.incomplete, "error": self.error,
            "readable": _reader is not None and self.state == "done",
        }


_sessions: dict[str, Session] = {}


def get(session_id: str) -> Session:
    s = _sessions.get(session_id)
    if s is None:
        raise ScanRefused("No such scan.", 404)
    return s


def _busy(printer_id: int) -> Session | None:
    for s in _sessions.values():
        if s.printer["id"] == printer_id and s.state in ("scanning", "waiting", "saving"):
            return s
    return None


def _caps_for(printer: dict[str, Any], source: str) -> dict[str, Any]:
    sc = printer.get("scan_capabilities") or {}
    return (sc.get("adf") if source.startswith("adf") else sc.get("platen")) or {}


def _region(caps: dict[str, Any]) -> tuple[int, int, tuple[float, float]]:
    """Width, height (1/300 inch) and the PDF page size (points) for the
    household paper, clamped to what the scanner can do."""
    paper = options.household_paper()
    w, h, page = (2550, 3300, (612.0, 792.0)) if paper == options.LETTER else (2480, 3508, (595.0, 842.0))
    w = min(w, int(caps.get("max_width") or w))
    h = min(h, int(caps.get("max_height") or h))
    return w, h, page


async def start(
    printer_id: int | None, *, source: str = "auto", color: str = "color", resolution: int = 300,
    who: str, origin: str, room_id: str | None = None,
) -> Session:
    if source not in ("auto", "platen", "adf", "adf_duplex"):
        raise ScanRefused("Choose the glass or the feeder.", 400)
    if color not in ("color", "grey"):
        raise ScanRefused("Choose colour or grey.", 400)
    try:
        printer = await devices.pick_printer(printer_id, room_id, need="scan")
    except devices.DeviceError as e:
        raise ScanRefused(str(e).replace("Which printer", "Which scanner"), 409) from e
    if _busy(printer["id"]) is not None:
        raise ScanRefused("%s is busy. Try again in a minute." % devices.The(_scanner_name(printer)))
    s = Session(id=uuid.uuid4().hex, printer=printer, color=color, resolution=resolution, source=source,
                who=who, origin=origin, room_id=room_id)
    _sessions[s.id] = s
    await _persist(s)
    async with s.lock:
        await _pass(s, first=True)
    if s.state == "failed":
        _sessions.pop(s.id, None)
        raise ScanRefused(s.error or "The scan didn't work.", 409)
    return s


def _scanner_name(printer: dict[str, Any]) -> str:
    n = printer["name"]
    return n.replace("printer", "scanner") if "printer" in n else n


async def status(printer_id: int | None, room_id: str | None) -> tuple[dict[str, Any], escl.Status]:
    printer = await devices.pick_printer(printer_id, room_id, need="scan")
    client = devices.escl_client(printer)
    return printer, await client.status()


async def _pass(s: Session, *, first: bool) -> None:
    """Scan once: the glass (one page) or the whole feeder."""
    lim = await store.get_limits()
    s.state = "scanning"
    s.touched = time.monotonic()
    name = _scanner_name(s.printer)
    try:
        client = devices.escl_client(s.printer)
        st = await client.status()
        if st.state and st.state not in ("Idle", "Ready"):
            raise ScanRefused("%s is busy. Try again in a minute." % devices.The(name))
        sc = s.printer.get("scan_capabilities") or {}
        src = s.source
        if src == "auto":
            src = "adf" if (sc.get("adf") and st.adf_loaded) else "platen"
            if src == "platen" and not sc.get("platen"):
                src = "adf"
        if src.startswith("adf"):
            if st.adf_jammed:
                raise ScanRefused("The feeder is jammed.")
            if st.adf_state and not st.adf_loaded:
                raise ScanRefused("There's nothing in the feeder.")
        caps = _caps_for(s.printer, src)
        modes = caps.get("color_modes") or ["RGB24"]
        mode = "RGB24" if s.color == "color" else "Grayscale8"
        if mode not in modes:
            mode = modes[0] if modes else "RGB24"
        res = caps.get("resolutions") or [300]
        resolution = s.resolution if s.resolution in res else min(res, key=lambda r: abs(r - s.resolution))
        width, height, _page = _region(caps)
        fmt = "image/jpeg"
        settings = escl.scan_settings_xml(source=src, color_mode=mode, resolution=resolution, width=width,
                                          height=height, duplex=src == "adf_duplex", document_format=fmt)
        s.source_used = src
        job = await client.start(settings)
        try:
            got = 0
            while True:
                if len(s.pages) >= lim["max_scan_pages"]:
                    s.error = "That's the most pages this house allows in one scan (%d)." % lim["max_scan_pages"]
                    break
                page = await client.next_page(job, first=got == 0)
                if page is None:
                    break
                if not page.startswith(b"\xff\xd8"):
                    raise ScanRefused("The scanner sent a page Domovoi can't read.")
                s.pages.append(page)
                s.thumbs.append(await asyncio.to_thread(_thumb, page, len(s.pages)))
                got += 1
                if src == "platen":
                    break
        finally:
            await client.delete(job)
        if got == 0:
            raise ScanRefused("The scanner didn't send any pages.")
        s.state = "waiting"
    except http11.CertificateChanged as e:
        await devices.note_cert_change(s.printer, e.seen)
        _broke(s, devices.CERT_MESSAGE.format(the=devices.The(name)))
    except ScanRefused as e:
        _broke(s, str(e))
    except devices.DeviceError as e:
        _broke(s, str(e))
    except escl.EsclError as e:
        msg = {"busy": "%s is busy. Try again in a minute." % devices.The(name),
               "jam": "The feeder is jammed."}.get(e.kind, "The scanner stopped answering.")
        _broke(s, msg)
    except http11.HttpError:
        _broke(s, "The scanner stopped answering.")
    s.touched = time.monotonic()
    await _persist(s)


def _broke(s: Session, message: str) -> None:
    if s.pages:
        # Keep what we have: the person decides (Done saves it).
        s.state = "waiting"
        s.error = message
        s.incomplete = True
    else:
        s.state = "failed"
        s.error = message[:-1] + "; nothing was saved." if message.endswith("stopped answering.") else message


def _thumb(jpeg: bytes, n: int) -> dict[str, Any]:
    from PIL import Image

    with Image.open(io.BytesIO(jpeg)) as im:
        im.draft("RGB", (300, 400))
        im = im.convert("RGB")
        im.thumbnail((150, 200))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=70)
    return {"page": n, "src": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}


async def more(session_id: str) -> Session:
    s = get(session_id)
    async with s.lock:
        if s.state != "waiting":
            raise ScanRefused("This scan isn't waiting for another page.")
        s.error = None
        await _pass(s, first=False)
        if s.state == "failed" and s.pages:
            s.state = "waiting"
    return s


async def finish(session_id: str) -> Session:
    s = get(session_id)
    async with s.lock:
        if s.state == "done":
            return s
        if s.state != "waiting" or not s.pages:
            raise ScanRefused("There's nothing to save.")
        s.state = "saving"
        try:
            rel = await asyncio.to_thread(_save, s)
        except OSError as e:
            log.warning("printing: saving scan %s failed: %s", s.id, e)
            s.state = "waiting"
            s.error = "The scan couldn't be saved to Documents."
            raise ScanRefused(s.error, 500) from e
        s.path = rel
        s.name = Path(rel).name
        s.state = "done"
        s.touched = time.monotonic()
        await _persist(s, finished=True)
        log.info("printing: saved a %d-page scan to Documents/%s", len(s.pages), rel)
    return s


async def cancel(session_id: str) -> Session:
    s = get(session_id)
    s.pages.clear()
    s.thumbs.clear()
    s.state = "canceled"
    await _persist(s, finished=True)
    return s


def documents_root() -> Path:
    from domovoi.config import settings

    return Path(settings.documents_dir).expanduser()


def scan_name(now: dt.datetime, incomplete: bool) -> str:
    return "Scan %s%s.pdf" % (now.strftime("%Y-%m-%d %H.%M"), " (incomplete)" if incomplete else "")


def _save(s: Session) -> str:
    from domovoi import household_time
    from domovoi.printing import templates

    root = documents_root()
    root.mkdir(parents=True, exist_ok=True)
    real_root = root.resolve()
    folder = root / SCANS_FOLDER
    if folder.is_symlink():
        raise OSError("Documents/Scans is a link; refusing to write through it")
    folder.mkdir(exist_ok=True)
    real_folder = folder.resolve()
    real_folder.relative_to(real_root)  # ValueError if it escapes: stays an error below
    sc = s.printer.get("scan_capabilities") or {}
    _w, _h, page = _region(_caps_for(s.printer, s.source_used or "platen") or sc)
    title = "Scan"
    pdf = templates.scans_pdf(s.pages, page_size=page, title=title)
    now = household_time.now()
    base = scan_name(now, s.incomplete)
    target = real_folder / base
    n = 2
    while target.exists():
        target = real_folder / base.replace(".pdf", " (%d).pdf" % n)
        n += 1
    tmp = real_folder / (".%s.part" % uuid.uuid4().hex)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(pdf)
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return target.relative_to(real_root).as_posix()


async def _persist(s: Session, *, finished: bool = False) -> None:
    values = {
        "id": s.id, "printer_id": s.printer["id"], "scanner_name": s.printer["name"], "state": s.state,
        "source_used": s.source_used, "pages": len(s.pages), "name": s.name, "path": s.path,
        "incomplete": s.incomplete, "error": s.error, "who": s.who[:120], "source": s.origin,
        "room_id": s.room_id,
    }
    try:
        await store.upsert_scan(values)
        if finished:
            from sqlalchemy import text

            from domovoi.db.session import session_scope

            async with session_scope() as db:
                await db.execute(text("UPDATE scan_jobs SET finished_at = now() WHERE id = :id"), {"id": s.id})
    except Exception as e:  # noqa: BLE001 — the log is a record, not the scan
        log.warning("printing: couldn't record scan %s: %s", s.id, e)


async def sweep_idle() -> int:
    """Worker tick: save sessions left waiting, forget old finished ones."""
    now = time.monotonic()
    saved = 0
    for s in list(_sessions.values()):
        if s.state == "waiting" and now - s.touched > IDLE_SAVE_SEC and s.pages:
            try:
                await finish(s.id)
                saved += 1
            except ScanRefused:
                pass
        elif s.state in ("done", "canceled", "failed") and now - s.touched > FINISHED_KEEP_SEC:
            _sessions.pop(s.id, None)
    return saved


def reset_state() -> None:
    """Tests only."""
    _sessions.clear()
    register_reader(None)
