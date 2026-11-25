"""The core's scan reads: one at a time, low priority, voice first.

The voice-first rules are :class:`domovoi.voice_first.VoiceFirst`'s, the
same as chat video's runner: before each page the read waits until no
voice turn is under way (and :data:`~domovoi.voice_first.QUIET_SEC`
more), tesseract runs at the lowest priority on one thread and is held
still while a turn runs, and a page sent to the picture model is
cancelled the moment a turn starts and asked again after it.

For each page, in order:

1. the page is drawn in grey at 300 dpi in a capped worker process
   (:mod:`.pages`);
2. Tesseract reads it (:mod:`.ocr`); when its confidences say it read the
   page well (:func:`.ocr.readable`), that text is the page's;
3. otherwise (handwriting, a photo, smudged print) the page goes to the
   household's vision slot (``ollama_vision_model``) with the letter
   reader's transcription prompt (:mod:`domovoi.doc_qa.letter`); its text
   is kept as what the page **might say** (:data:`.store.MIGHT_SAY`).
   With AI features off, or no vision model, the picture model is skipped
   and the page is saved as one Domovoi couldn't read (OCR's unsure
   words are not kept as if they were the page's).

**Every page is saved the moment it is read**, so a restart carries on
from the next page (``attempts`` counts the starts; past
:data:`MAX_ATTEMPTS` the read fails cleanly instead of looping on a file
that stops the server). The page pictures live in a work folder that is
deleted when the read ends.

**The text is bound to the bytes it was read from** (security audit round
6, DOC-12): the scan is found inside Documents with no link anywhere on its
path (:func:`resolve`), copied into the read's own work folder, and hashed
and drawn from that copy, so a file replaced mid-read can't lend its pages
to the other's hash. **A read can be stopped** (``POST
/v1/scan/reads/{id}/cancel``), and ends after :data:`MAX_READ_SEC` of
running however many pages it has (DOC-11).

:func:`read_now` is the same page step without the wait, for the person
who asked by voice and is waiting for the answer (OCR only: a page that
needs the picture model is left for the background read).
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import logging
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable, Protocol

from sqlalchemy import text

from domovoi import ai_features
from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.scan_reader import ocr, pages, store
from domovoi.voice_first import POLL_SEC, QUIET_SEC, VoiceFirst

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
IDLE_POLL_SEC = 2.0
#: The longest a read may run, counted from its first start (a 500-page
#: scan read by OCR takes about an hour on a small server).
MAX_READ_SEC = 4 * 3600
#: The largest scan file a read copies and reads.
MAX_SCAN_BYTES = 1024 ** 3
#: The longest a picture-model transcription may run, tokens.
VISION_TOKENS = 1200

CODE_MISSING = "scan_missing"
CODE_NO_READER = "scan_no_reader"
CODE_INTERRUPTED = "scan_read_interrupted"
CODE_FAILED = "scan_read_failed"
CODE_TOO_LONG = "scan_read_too_long"
CODE_TOO_LARGE = "scan_too_large"
FAILURE_TEXT = {
    CODE_MISSING: "The scan isn't in Documents any more.",
    CODE_NO_READER: ("This server can't read scans yet: tesseract-ocr isn't installed, and the picture model "
                     "isn't available."),
    CODE_INTERRUPTED: "Reading this scan stopped the server more than once, so Domovoi gave up on it.",
    CODE_FAILED: "Something went wrong reading this scan.",
    CODE_TOO_LONG: "Reading this scan took more than 4 hours, so Domovoi stopped. A scan with fewer pages reads "
                   "more quickly.",
    CODE_TOO_LARGE: "This scan is larger than 1 GB, more than Domovoi reads.",
}


class Vision(Protocol):
    async def transcribe(self, jpeg: bytes) -> str: ...


class RealVision:
    """The household's vision slot, with the letter reader's prompt."""

    async def transcribe(self, jpeg: bytes) -> str:
        from domovoi.clients import ollama
        from domovoi.doc_qa import letter

        model = (settings.ollama_vision_model or "").strip()
        if not model:
            raise VisionUnavailable()
        msgs = [{"role": "system", "content": letter.TRANSCRIBE_SYSTEM},
                {"role": "user", "content": letter.TRANSCRIBE_QUESTION,
                 "images": [base64.b64encode(jpeg).decode("ascii")]}]
        out: list[str] = []
        try:
            async for d in ollama.chat_stream(msgs, model=model, think=False,
                                              num_ctx=settings.ollama_vision_num_ctx or None,
                                              num_predict=VISION_TOKENS, temperature=0.1):
                out.append(d)
        except ollama.OllamaChatError as e:
            if e.status == 404 or "not found" in e.detail.lower():
                raise VisionUnavailable() from None
            raise
        return letter.clean_transcript("".join(out))


class StubVision:
    async def transcribe(self, jpeg: bytes) -> str:
        return "A stub reading of the page."


def default_vision() -> Vision:
    return StubVision() if settings.use_stubs else RealVision()


class VisionUnavailable(Exception):
    """No vision model to ask."""


class ReadFailed(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Cancelled(Exception):
    pass


class _Gone(Exception):
    pass


class _Superseded(_Gone):
    """Another read of the same row (a voice "read it now" beside the
    background read) found different bytes at the path and took the row
    over: this one's pages are not saved under it."""


OcrFn = Callable[..., Awaitable[ocr.PageOcr]]


def vision_allowed() -> bool:
    """The picture model may be asked: AI features on and a vision model set."""
    return not ai_features.turned_off() and bool((settings.ollama_vision_model or "").strip())


def can_read() -> bool:
    """This server can read a scan at all: tesseract installed, or the
    picture model available. Voice offers to read only then."""
    return ocr.find() is not None or vision_allowed()


def documents_root() -> Path:
    return Path(settings.documents_dir).expanduser()


def _is_link(p: Path) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    return p.is_symlink() or bool(isjunction and isjunction(p))


def resolve(rel: str) -> Path | None:
    """The scan's file inside Documents, or None: no escaping it, and no
    link on any part of the path below Documents (a link in Scans would
    otherwise read a PDF from another folder, past the request's checks)."""
    root = documents_root()
    rel_path = PurePosixPath(str(rel or "").replace("\\", "/"))
    parts = rel_path.parts
    if (not parts or rel_path.is_absolute()
            or any(x in ("", ".", "..") or (sys.platform == "win32" and ":" in x) for x in parts)):
        return None
    try:
        p = root.resolve()
        for part in parts:
            p = p / part
            if _is_link(p):
                return None
        p.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if not p.is_file() or p.suffix.lower() != ".pdf":
        return None
    return p


def _snapshot(path: Path, work: Path) -> Path:
    """``path`` copied into the read's work folder (never through a link);
    the copy is what is hashed and drawn."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    dest = work / "source.pdf"
    fd = os.open(str(path), flags)
    with os.fdopen(fd, "rb") as src:
        if os.fstat(src.fileno()).st_size > MAX_SCAN_BYTES:
            raise ReadFailed(CODE_TOO_LARGE)
        with dest.open("wb") as out:
            shutil.copyfileobj(src, out, 1024 * 1024)
    return dest


def too_large(path: Path) -> bool:
    """The scan is over :data:`MAX_SCAN_BYTES` (checked before any hashing)."""
    try:
        return path.stat().st_size > MAX_SCAN_BYTES
    except OSError:
        return False


async def queue_scan(rel: str, *, scan_id: str | None = None) -> str | None:
    """Queue a read of the scan at ``rel``; its id, or None when this
    server can't read scans or the file isn't there. A file over the size
    limit is queued unhashed: the read then fails with the size reason."""
    if not can_read():
        return None
    path = resolve(rel)
    if path is None:
        return None
    try:
        sha = None if too_large(path) else await asyncio.to_thread(store.current_sha, path)
    except OSError:
        return None
    return await store.queue(rel, sha256=sha, scan_id=scan_id, languages="+".join(ocr.languages()))


@dataclass
class Job:
    id: str
    path: str
    sha256: str | None
    pages: int
    languages: list[str]
    attempts: int
    waiting: bool = False
    started_at: dt.datetime | None = None

    @classmethod
    def from_row(cls, r: Any) -> "Job":
        return cls(id=r["id"], path=r["path"], sha256=r["sha256"], pages=int(r["pages"] or 0),
                   languages=[x for x in str(r["languages"] or "eng").split("+") if x] or ["eng"],
                   attempts=int(r["attempts"] or 0), started_at=r.get("started_at"))

    def overdue(self) -> bool:
        """Running past :data:`MAX_READ_SEC` since its first start."""
        if self.started_at is None:
            return False
        started = self.started_at if self.started_at.tzinfo else self.started_at.replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc) - started).total_seconds() > MAX_READ_SEC


async def cancel(read_id: str) -> bool:
    """Stop a read (the person's Stop): a queued one ends now, a running one
    at its next check (within about a second, a helper program ended at
    once). False when it had already ended."""
    if not store.valid_id(read_id):
        return False
    async with session_scope() as s:
        row = (await s.execute(text(
            "UPDATE scan_reads SET state = 'cancelled', waiting_for_voice = false, finished_at = now(), "
            "updated_at = now() WHERE id = :id AND state IN ('queued', 'running') RETURNING id"),
            {"id": read_id})).first()
        await s.execute(text("SELECT pg_notify('scan_read_changed', :id)"), {"id": read_id})
    return row is not None


async def _update(job_id: str, **cols: Any) -> None:
    sets = [f"{k} = :{k}" for k in cols] + ["updated_at = now()"]
    async with session_scope() as s:
        r = await s.execute(text(f"UPDATE scan_reads SET {', '.join(sets)} WHERE id = :id"), {"id": job_id, **cols})
        await s.execute(text("SELECT pg_notify('scan_read_changed', :id)"), {"id": job_id})
    if (r.rowcount or 0) == 0:
        raise _Gone()


async def saved_pages(job_id: str) -> set[int]:
    async with session_scope() as s:
        rows = (await s.execute(text("SELECT page FROM scan_read_pages WHERE read_id = :id"),
                                {"id": job_id})).scalars().all()
    return {int(r) for r in rows}


async def save_page(job_id: str, page: int, method: str, page_text: str, confidence: float | None,
                    words: int, *, sha: str | None = None) -> None:
    """Save one page; ``sha``: the hash of the bytes it was read from, which
    must still be the row's (checked under a row lock, so a read that
    switched the row to other bytes can't interleave)."""
    async with session_scope() as s:
        row = (await s.execute(text("SELECT sha256 FROM scan_reads WHERE id = :id FOR SHARE"),
                               {"id": job_id})).first()
        if row is None:
            raise _Gone()
        if sha is not None and row[0] != sha:
            raise _Superseded()
        await s.execute(text(
            "INSERT INTO scan_read_pages (read_id, page, method, confidence, words, text) "
            "VALUES (:id, :p, :m, :c, :w, :t) ON CONFLICT (read_id, page) DO NOTHING"),
            {"id": job_id, "p": page, "m": method, "c": confidence, "w": words, "t": page_text[:20000]})
        await s.execute(text("UPDATE scan_reads SET updated_at = now() WHERE id = :id"), {"id": job_id})
        await s.execute(text("SELECT pg_notify('scan_read_changed', :id)"), {"id": job_id})


async def _adopt_sha(job: Job, sha: str) -> None:
    """Make ``sha`` (the hash of the copy about to be read) the row's. When
    the row held other bytes, their pages go in the same transaction, under
    the row lock :func:`save_page` takes, so no page of the old bytes can
    land under the new hash."""
    async with session_scope() as s:
        row = (await s.execute(text("SELECT sha256, pages FROM scan_reads WHERE id = :id FOR UPDATE"),
                               {"id": job.id})).first()
        if row is None:
            raise _Gone()
        if row[0] == sha:
            job.pages = int(row[1] or 0)
        else:
            # The file changed since the read was queued: read what is there now.
            await s.execute(text("UPDATE scan_reads SET sha256 = :sha, pages = 0, updated_at = now() WHERE id = :id"),
                            {"id": job.id, "sha": sha})
            await s.execute(text("DELETE FROM scan_read_pages WHERE read_id = :id"), {"id": job.id})
            await s.execute(text("SELECT pg_notify('scan_read_changed', :id)"), {"id": job.id})
            job.pages = 0
    job.sha256 = sha


class Runner(VoiceFirst):
    """Runs the queued reads one at a time (:meth:`run_forever`)."""

    label = "scan read"
    passthrough = (VisionUnavailable, _Cancelled, _Gone)
    stopped = _Cancelled

    def __init__(self, *, vision: Vision | None = None, ocr_fn: OcrFn | None = None,
                 quiet_sec: float = QUIET_SEC, poll_sec: float = POLL_SEC) -> None:
        super().__init__(quiet_sec=quiet_sec, poll_sec=poll_sec)
        self.vision = vision or default_vision()
        self.ocr_fn = ocr_fn or ocr.read_page
        self.current: str | None = None

    async def _mark_waiting(self, job: Job, waiting: bool) -> None:
        await _update(job.id, waiting_for_voice=waiting)

    async def _stop_requested(self, job: Job) -> bool:
        async with session_scope() as s:
            row = (await s.execute(text("SELECT state FROM scan_reads WHERE id = :id"), {"id": job.id})).first()
        if row is None:
            raise _Gone()
        return row[0] == "cancelled"

    def _before_model_step(self) -> None:
        if not vision_allowed():
            raise VisionUnavailable()

    # ── the loop ────────────────────────────────────────────────────────
    async def run_forever(self, shutdown: asyncio.Event) -> None:
        """Take reads until ``shutdown``: one under way at shutdown stops
        where it is and carries on after the restart."""
        sweep_work()
        while not shutdown.is_set():
            job = asyncio.ensure_future(self.run_next())
            stop = asyncio.ensure_future(shutdown.wait())
            try:
                await asyncio.wait({job, stop}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                stop.cancel()
                if not job.done():
                    job.cancel()
                    try:
                        await job
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001 — stopping
                        pass
            if not job.done() or job.cancelled():
                break
            try:
                ran = job.result()
            except Exception as e:  # noqa: BLE001 — the next round tries again
                log.warning("scan read: the runner stumbled: %s", type(e).__name__)
                ran = None
            if ran is None:
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=IDLE_POLL_SEC)
                except asyncio.TimeoutError:
                    pass

    async def run_next(self) -> str | None:
        """Claim the oldest live read and run it to its end; its id, or None."""
        async with session_scope() as s:
            row = (await s.execute(text(
                f"UPDATE scan_reads SET state = 'running', attempts = attempts + 1, "
                f"started_at = COALESCE(started_at, now()), waiting_for_voice = false, updated_at = now() "
                f"WHERE id = (SELECT id FROM scan_reads WHERE state IN ('queued', 'running') "
                f"ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING {store.COLS}"))).mappings().first()
            if row is not None:
                await s.execute(text("SELECT pg_notify('scan_read_changed', :id)"), {"id": row["id"]})
        if row is None:
            return None
        job = Job.from_row(row)
        self.current = job.id
        try:
            await self.run_job(job)
        finally:
            self.current = None
        return job.id

    async def run_job(self, job: Job) -> None:
        work = work_dir(job.id)
        try:
            if job.attempts > MAX_ATTEMPTS:
                raise ReadFailed(CODE_INTERRUPTED)
            try:
                await self._read(job, work, gate=True)
            except _Superseded:
                # A read by voice found other bytes at the path and took the
                # row over: read the file as it is now.
                await self._read(job, work, gate=True)
            if await self._stop_requested(job):
                raise _Cancelled()
            await self._end(job, "done")
        except _Cancelled:
            await self._end(job, "cancelled")
        except _Gone:
            log.info("scan read %s: its row is gone; stopping", job.id[:8])
        except ReadFailed as e:
            await self._end(job, "failed", e.code)
        except asyncio.CancelledError:
            raise  # shutting down: the row says running; the next start carries on
        except Exception as e:  # noqa: BLE001
            log.warning("scan read %s failed: %s", job.id[:8], type(e).__name__)
            await self._end(job, "failed", CODE_FAILED)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    async def _end(self, job: Job, state: str, code: str | None = None) -> None:
        try:
            await _update(job.id, state=state, waiting_for_voice=False, error=FAILURE_TEXT.get(code or ""),
                          error_code=code)
            async with session_scope() as s:
                await s.execute(text("UPDATE scan_reads SET finished_at = now() WHERE id = :id"), {"id": job.id})
        except _Gone:
            return
        log.info("scan read %s: %s", job.id[:8], state)

    # ── the pages ───────────────────────────────────────────────────────
    async def _read(self, job: Job, work: Path, *, gate: bool, deadline: float | None = None) -> None:
        found = resolve(job.path)
        if found is None:
            raise ReadFailed(CODE_MISSING)
        if ocr.find() is None and not vision_allowed():
            raise ReadFailed(CODE_NO_READER)
        if gate:
            if job.overdue():
                raise ReadFailed(CODE_TOO_LONG)
            await self.wait_for_quiet(job)
        try:
            path = await asyncio.to_thread(_snapshot, found, work)
        except OSError:
            raise ReadFailed(CODE_MISSING) from None
        sha = await asyncio.to_thread(store.sha256_of, path)
        await _adopt_sha(job, sha)
        if job.pages <= 0:
            try:
                job.pages = await pages.count(path)
            except pages.PageFailed:
                raise ReadFailed(CODE_FAILED) from None
            await _update(job.id, pages=job.pages)
        done = await saved_pages(job.id)
        for n in range(1, job.pages + 1):
            if n in done:
                continue
            if deadline is not None and time.monotonic() > deadline:
                return
            if gate:
                if job.overdue():
                    raise ReadFailed(CODE_TOO_LONG)
                await self.wait_for_quiet(job)     # stop is looked at here too
            saved = await self._page(job, path, n, work, gate=gate)
            if saved:
                done.add(n)

    async def _page(self, job: Job, path: Path, n: int, work: Path, *, gate: bool) -> bool:
        """Read page ``n`` and save it; False when it was left for later
        (:func:`read_now`: a page that needs the picture model)."""
        png = work / f"page-{n:03d}.png"
        try:
            await pages.render(path, n, png)
        except pages.PageFailed:
            await save_page(job.id, n, "none", "", None, 0, sha=job.sha256)
            return True
        try:
            got: ocr.PageOcr | None = await self.ocr_fn(png, hold=self.pass_hold(job) if gate else None,
                                                         langs=job.languages)
        except ocr.OcrMissing:
            got = None
        except ocr.OcrFailed as e:
            log.info("scan read %s: tesseract failed on page %d (%s)", job.id[:8], n, e)
            got = None
        if got is not None and ocr.readable(got):
            await save_page(job.id, n, "ocr", got.text, got.confidence, got.words, sha=job.sha256)
            png.unlink(missing_ok=True)
            return True
        if not gate:
            return False  # the background read asks the picture model, after the turn
        said = ""
        if vision_allowed():
            try:
                jpeg = await asyncio.to_thread(pages.vision_jpeg, png)
                said = await self.model_step(job, lambda: self.vision.transcribe(jpeg))
            except VisionUnavailable:
                said = ""
        said = (said or "").strip()
        if said and said.replace("[unclear]", "").strip():
            await save_page(job.id, n, "vision", said, None, len(said.split()), sha=job.sha256)
        else:
            # Not readable, and no picture model to ask (AI features off): the
            # page says it couldn't be read, never OCR's unsure guess.
            await save_page(job.id, n, "none", "", got.confidence if got else None, 0,
                            sha=job.sha256)
        png.unlink(missing_ok=True)
        return True


async def read_now(read_id: str, *, budget_sec: float = 20.0, runner: Runner | None = None) -> None:
    """Read what OCR can of ``read_id`` right now, inside a voice turn that
    is waiting for it (no wait for quiet; pages that need the picture model
    are left for the background read)."""
    row = await store.get(read_id)
    if row is None or row["state"] not in ("queued", "running"):
        return
    r = runner or Runner()
    job = Job.from_row(row)
    work = work_dir(job.id, inline=True)
    try:
        # The budget holds inside a page too: a page still being drawn or
        # read when it runs out is stopped (its helper ended) and left for
        # the background read.
        try:
            await asyncio.wait_for(r._read(job, work, gate=False, deadline=time.monotonic() + budget_sec),
                                   timeout=budget_sec + 1.0)
        except asyncio.TimeoutError:
            pass
        left = set(range(1, job.pages + 1)) - await saved_pages(job.id)
        if job.pages and not left:
            await _update(job.id, state="done", waiting_for_voice=False)
            async with session_scope() as s:
                await s.execute(text("UPDATE scan_reads SET finished_at = now() WHERE id = :id"), {"id": job.id})
    except (ReadFailed, _Gone):
        return
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ─── The work folder ────────────────────────────────────────────────────
WORK_DIRNAME = "domovoi-scan-read"


def work_root() -> Path:
    return Path(tempfile.gettempdir()) / WORK_DIRNAME


def work_dir(read_id: str, *, inline: bool = False) -> Path:
    d = work_root() / (read_id + ("-now" if inline else ""))
    d.mkdir(parents=True, exist_ok=True)
    return d


def sweep_work() -> None:
    """At start: page pictures a stopped core left are removed (they are
    drawn again from the scan when needed)."""
    root = work_root()
    if root.is_dir() and not root.is_symlink():
        for child in root.iterdir():
            if not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
