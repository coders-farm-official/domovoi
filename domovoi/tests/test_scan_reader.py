"""The scan reader (1.4.0): "What does it say?" for scans in Documents.

Every page here is made in the test (text drawn with Pillow, put in a
picture PDF the way a scan is saved, with ReportLab); never a real letter.
OCR is a fake at the runner's ``ocr_fn`` boundary and the picture model a
fake ``Vision``, except :func:`test_real_tesseract_reads_a_printed_page`,
which runs tesseract when it is installed and is skipped otherwise.

What matters most:

* the confidences decide: a well-read page keeps OCR's text, a poorly
  read one goes to the picture model and is worded "might say", and with
  AI features off it is said to be unreadable (OCR still runs);
* voice first: a page waits while a voice turn runs, a picture-model step
  a turn starts is cancelled and asked again after it;
* every page is saved as it is read and a restart carries on from the next;
* a read scan is ``readable`` and its text is found wherever Domovoi reads
  a document (by the file's bytes), so "Ask about this document" works;
* the dashboard shows "What does it say?" only for a scan that is read or
  being read.
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from domovoi import ai_features, voice_activity
from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.scan_reader import ocr, pages, says, store
from domovoi.scan_reader import runner as runner_mod
from domovoi.tests.conftest import requires_db

REPO_ROOT = Path(__file__).resolve().parents[2]

LETTER = ["ACME WATER COMPANY", "Account number 4417-22", "Your bill of $84.20 is due on March 3, 2027.",
          "Please pay online or by post."]


# ─── Test pages: drawn text, saved the way a scan is ────────────────────
def page_jpeg(lines: list[str], size: tuple[int, int] = (1275, 1650)) -> bytes:
    """A page picture with ``lines`` printed on it (150 dpi letter)."""
    from PIL import Image, ImageDraw, ImageFont

    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=36)
    y = 120
    for line in lines:
        d.text((100, y), line, fill="black", font=font)
        y += 70
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def scan_pdf(path: Path, page_lines: list[list[str]]) -> Path:
    from domovoi.printing import templates

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        templates.scans_pdf((page_jpeg(x) for x in page_lines), page_size=(612.0, 792.0), title="Scan", sink=f)
    return path


def tsv(words: list[tuple[str, float]], per_line: int = 4) -> str:
    """Tesseract TSV for ``words`` (text, confidence), ``per_line`` a line."""
    rows = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
    for i, (w, c) in enumerate(words):
        rows.append(f"5\t1\t1\t1\t{i // per_line + 1}\t{i % per_line + 1}\t0\t0\t10\t10\t{c}\t{w}")
    return "\n".join(rows)


def good(lines: list[str]) -> ocr.PageOcr:
    """A confident reading of ``lines``, one TSV line each."""
    rows = [tsv([]).split("\n")[0]]
    for n, line in enumerate(lines, 1):
        for k, w in enumerate(line.split(), 1):
            rows.append(f"5\t1\t1\t1\t{n}\t{k}\t0\t0\t10\t10\t93.0\t{w}")
    return ocr.parse_tsv("\n".join(rows))


def poor() -> ocr.PageOcr:
    return ocr.parse_tsv(tsv([("Dear", 40.0), ("Gr4ndma", 22.0), ("thnk", 35.0), ("yu", 30.0)]))


class FakeOcr:
    """Answers by page number (the PNG's name): a PageOcr or an exception."""

    def __init__(self, answers: dict[int, Any]) -> None:
        self.answers = answers
        self.calls: list[int] = []

    async def __call__(self, png: Path, *, hold=None, langs=None) -> ocr.PageOcr:
        n = int(png.stem.split("-")[1])
        self.calls.append(n)
        a = self.answers.get(n, good(["blank"]))
        if isinstance(a, Exception):
            raise a
        return a


class FakeVision:
    def __init__(self, said: str = "Dear Grandma, thank you for the scarf.") -> None:
        self.said = said
        self.calls = 0
        self.gate: asyncio.Event | None = None

    async def transcribe(self, jpeg: bytes) -> str:
        assert jpeg[:2] == b"\xff\xd8"
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        return self.said


@pytest.fixture(autouse=True)
def _quiet_house():
    voice_activity._reset_for_tests()
    yield
    voice_activity._reset_for_tests()


@pytest.fixture
def docs(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "Documents"
    (d / "Scans").mkdir(parents=True)
    monkeypatch.setattr(settings, "documents_dir", str(d))
    monkeypatch.setattr(runner_mod, "work_root", lambda: tmp_path / "work")
    return d


# ─── OCR's confidences ──────────────────────────────────────────────────
def test_tsv_is_rebuilt_line_by_line_and_paragraph_by_paragraph() -> None:
    raw = "\n".join([
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
        "1\t1\t0\t0\t0\t0\t0\t0\t100\t100\t-1\t",
        "5\t1\t1\t1\t1\t1\t0\t0\t9\t9\t96.1\tDear",
        "5\t1\t1\t1\t1\t2\t0\t0\t9\t9\t95.0\tcustomer,",
        "5\t1\t1\t2\t1\t1\t0\t0\t9\t9\t91.2\tYour",
        "5\t1\t1\t2\t1\t2\t0\t0\t9\t9\t90.0\tbill",
        "5\t1\t1\t2\t2\t1\t0\t0\t9\t9\t89.0\tis",
        "5\t1\t1\t2\t2\t2\t0\t0\t9\t9\t-1\t ",
        "5\t1\t1\t2\t2\t3\t0\t0\t9\t9\t92.0\tdue.",
    ])
    got = ocr.parse_tsv(raw)
    assert got.text == "Dear customer,\n\nYour bill\nis due."
    assert got.words == 6 and 89 < got.confidence < 96 and got.low_share == 0
    assert ocr.readable(got)


def test_the_fallback_decision_follows_the_confidences() -> None:
    assert ocr.readable(good(LETTER))
    assert not ocr.readable(poor()), "handwriting-like confidences go to the picture model"
    assert not ocr.readable(good(["Hi"])), "too few words to trust"
    many_unsure = ocr.parse_tsv(tsv([("word", 95.0)] * 6 + [("w0rd", 20.0)] * 5))
    assert not ocr.readable(many_unsure)
    assert not ocr.readable(ocr.parse_tsv(tsv([])))
    assert ocr.languages() == ["eng"]
    assert ocr.argv(Path("p.png"), ["eng"])[-5:] == ["-l", "eng", "--psm", "3", "tsv"]


def test_a_guessed_page_is_worded_might_say_never_as_fact() -> None:
    rows = [{"page": 1, "method": "ocr", "text": "Your bill is due."},
            {"page": 2, "method": "vision", "text": "Dear Grandma"},
            {"page": 3, "method": "none", "text": ""}]
    full = store.full_text(rows)
    assert "Page 2\n" + store.MIGHT_SAY + "\nDear Grandma" in full
    assert "might say" in store.MIGHT_SAY
    assert "Page 3\n" + store.COULDNT_READ in full
    notes = says.page_notes([2], [3])
    assert notes[0]["code"] == "scan_pages_guessed" and "might say" in notes[0]["text"]
    assert notes[1]["code"] == "scan_pages_unread"


# ─── Pages drawn in the worker ──────────────────────────────────────────
async def test_pages_are_counted_and_drawn_in_the_worker(tmp_path) -> None:
    pdf = scan_pdf(tmp_path / "Scan.pdf", [LETTER, ["Page two"]])
    assert await pages.count(pdf) == 2
    png = await pages.render(pdf, 2, tmp_path / "p2.png", dpi=100)
    from PIL import Image

    with Image.open(png) as im:
        assert im.mode == "L" and im.width == pytest.approx(850, abs=2)
    assert pages.vision_jpeg(png)[:2] == b"\xff\xd8"
    with pytest.raises(pages.PageFailed):
        await pages.render(pdf, 3, tmp_path / "p3.png")
    (tmp_path / "bad.pdf").write_bytes(b"%PDF-1.4 nothing here")
    with pytest.raises(pages.PageFailed):
        await pages.count(tmp_path / "bad.pdf")


# ─── The page step, no database ─────────────────────────────────────────
@pytest.fixture
def saved(monkeypatch) -> list[tuple]:
    out: list[tuple] = []

    async def save_page(job_id, page, method, page_text, confidence, words, **kw):
        out.append((page, method, page_text))

    monkeypatch.setattr(runner_mod, "save_page", save_page)
    return out


def _job() -> runner_mod.Job:
    return runner_mod.Job(id="a" * 32, path="Scans/x.pdf", sha256=None, pages=1, languages=["eng"], attempts=1)


def _no_db(r: runner_mod.Runner) -> runner_mod.Runner:
    async def stop(job):
        return False

    async def mark(job, waiting):
        return None

    r._stop_requested = stop  # type: ignore[method-assign]
    r._mark_waiting = mark  # type: ignore[method-assign]
    return r


async def test_a_page_read_well_keeps_ocr_and_never_asks_the_picture_model(tmp_path, saved) -> None:
    pdf = scan_pdf(tmp_path / "Scan.pdf", [LETTER])
    vision = FakeVision()
    r = _no_db(runner_mod.Runner(vision=vision, ocr_fn=FakeOcr({1: good(LETTER)}), poll_sec=0.01, quiet_sec=0))
    assert await r._page(_job(), pdf, 1, tmp_path, gate=True)
    assert saved == [(1, "ocr", "\n".join(LETTER))]
    assert vision.calls == 0


async def test_a_poorly_read_page_goes_to_the_picture_model(tmp_path, saved) -> None:
    pdf = scan_pdf(tmp_path / "Scan.pdf", [["Dear Grandma"]])
    vision = FakeVision()
    r = _no_db(runner_mod.Runner(vision=vision, ocr_fn=FakeOcr({1: poor()}), poll_sec=0.01, quiet_sec=0))
    assert await r._page(_job(), pdf, 1, tmp_path, gate=True)
    assert saved == [(1, "vision", "Dear Grandma, thank you for the scarf.")]
    assert vision.calls == 1
    # No tesseract on the server at all: the picture model reads it.
    saved.clear()
    r = _no_db(runner_mod.Runner(vision=vision, ocr_fn=FakeOcr({1: ocr.OcrMissing()}), poll_sec=0.01, quiet_sec=0))
    assert await r._page(_job(), pdf, 1, tmp_path, gate=True)
    assert saved[0][1] == "vision"


async def test_ai_off_skips_the_picture_model_and_says_the_page_couldnt_be_read(tmp_path, saved) -> None:
    pdf = scan_pdf(tmp_path / "Scan.pdf", [["Dear Grandma"], LETTER])
    vision = FakeVision()
    r = _no_db(runner_mod.Runner(vision=vision, ocr_fn=FakeOcr({1: ocr.parse_tsv(tsv([("x", 10.0)])),
                                                                2: good(LETTER)}), poll_sec=0.01, quiet_sec=0))
    with ai_features.override(False):
        assert not runner_mod.vision_allowed()
        assert await r._page(_job(), pdf, 1, tmp_path, gate=True)
        assert await r._page(_job(), pdf, 2, tmp_path, gate=True)  # OCR isn't AI: it still runs
    assert saved == [(1, "none", ""), (2, "ocr", "\n".join(LETTER))]
    assert vision.calls == 0


async def test_inside_a_voice_turn_a_hard_page_is_left_for_later(tmp_path, saved) -> None:
    pdf = scan_pdf(tmp_path / "Scan.pdf", [["Dear Grandma"]])
    vision = FakeVision()
    r = runner_mod.Runner(vision=vision, ocr_fn=FakeOcr({1: poor()}))
    assert await r._page(_job(), pdf, 1, tmp_path, gate=False) is False
    assert saved == [] and vision.calls == 0


async def test_a_voice_turn_cancels_the_picture_model_and_it_asks_again_after(tmp_path, saved) -> None:
    pdf = scan_pdf(tmp_path / "Scan.pdf", [["Dear Grandma"]])
    vision = FakeVision()
    vision.gate = asyncio.Event()
    r = _no_db(runner_mod.Runner(vision=vision, ocr_fn=FakeOcr({1: poor()}), poll_sec=0.01, quiet_sec=0.05))
    step = asyncio.create_task(r._page(_job(), pdf, 1, tmp_path, gate=True))
    for _ in range(500):
        if vision.calls:
            break
        await asyncio.sleep(0.01)
    assert vision.calls == 1

    async def turn() -> None:
        async with voice_activity.turn("satellite"):
            await asyncio.sleep(0.2)

    t = asyncio.create_task(turn())
    await asyncio.sleep(0.05)
    assert r.voice_cancels == 1 and saved == [], "the step stops at once and nothing half-done is kept"
    vision.gate.set()
    await t
    assert await asyncio.wait_for(step, 10)
    assert vision.calls == 2 and r.voice_waits >= 1
    assert saved == [(1, "vision", vision.said)]


async def test_tesseract_is_held_still_while_a_turn_runs() -> None:
    class P:
        def __init__(self) -> None:
            self.log: list[str] = []

        def suspend(self) -> None:
            self.log.append("suspend")

        def resume(self) -> None:
            self.log.append("resume")

    r = runner_mod.Runner(vision=FakeVision(), poll_sec=0.01)
    p = P()

    async def turn() -> None:
        async with voice_activity.turn("satellite"):
            await asyncio.sleep(0.1)

    t = asyncio.create_task(turn())
    await asyncio.sleep(0.02)
    await r.hold_while_voice(p)
    await t
    assert p.log == ["suspend", "resume"]


def test_chat_video_and_the_scan_reader_share_one_set_of_voice_rules() -> None:
    from domovoi.chat_video import runner as video
    from domovoi.voice_first import VoiceFirst

    assert issubclass(video.Runner, VoiceFirst) and issubclass(runner_mod.Runner, VoiceFirst)
    assert video.Runner.wait_for_quiet is runner_mod.Runner.wait_for_quiet
    assert video.Runner.model_step is runner_mod.Runner.model_step


# ─── Documents read the scan's text ─────────────────────────────────────
def test_a_scan_with_its_reading_is_searchable_text(tmp_path) -> None:
    from domovoi import doc_extract
    from domovoi.doc_qa import notes as doc_notes

    pdf = scan_pdf(tmp_path / "Scan.pdf", [LETTER, ["Page two"]])
    with pytest.raises(doc_extract.Refused) as e:
        doc_extract.extract(pdf, "pdf")
    assert str(e.value) == doc_notes.NO_TEXT_SCANNED
    got = doc_extract.extract(pdf, "pdf", scan_text={1: "\n".join(LETTER)})
    assert got["parts"][0]["page"] == 1 and "due on March 3, 2027" in got["parts"][0]["text"]
    assert got["scan_read_pages"] == [1] and got["scanned_pages"] == [2]
    codes = [n["code"] for n in got["notes"]]
    assert "scan_read" in codes and "scanned_pages" in codes


# ─── A real Tesseract, when the server has one ─────────────────────────
@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract is not installed")
async def test_real_tesseract_reads_a_printed_page(tmp_path) -> None:
    pdf = scan_pdf(tmp_path / "Scan.pdf", [LETTER])
    png = await pages.render(pdf, 1, tmp_path / "page-001.png")
    got = await ocr.read_page(png)
    assert ocr.readable(got), got
    assert "WATER" in got.text and "84.20" in got.text


# ─── The dashboard button ───────────────────────────────────────────────
def _render(scenarios: dict[str, Any]) -> dict[str, Any]:
    node = shutil.which("node")
    assert node, "node is required to render web/static JSX"
    proc = subprocess.run([node, str(Path(__file__).with_name("jsx_render_harness.js")), str(REPO_ROOT),
                           json.dumps(scenarios)], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_dashboard_button_shows_for_a_read_scan_or_one_being_read() -> None:
    read = {"id": "b" * 32, "state": "running", "pages": 3, "pages_done": 1, "reading": True,
            "waiting_for_voice": False, "readable": False}
    base = {"file": "web/static/printers.jsx", "component": "ScanReadButton", "fnProps": ["onRead"]}
    out = _render({
        "readable": {**base, "props": {"scan": {"id": "s", "readable": True, "read": {**read, "state": "done",
                                                                                       "reading": False}}}},
        "reading": {**base, "props": {"scan": {"id": "s", "readable": False, "read": read}}},
        "paused": {**base, "props": {"scan": {"id": "s", "readable": False,
                                              "read": {**read, "waiting_for_voice": True}}}},
        "never": {**base, "props": {"scan": {"id": "s", "readable": False, "read": None}}},
        "failed": {**base, "props": {"scan": {"id": "s", "readable": False,
                                              "read": {**read, "state": "failed", "reading": False}}}},
        "no_reader": {"file": "web/static/printers.jsx", "component": "ScanReadButton",
                      "props": {"scan": {"id": "s", "readable": True}}},
    })

    def texts(name: str) -> str:
        return " ".join(e.get("text") or "" for e in out[name])

    assert "What does it say?" in texts("readable") and "reading" not in texts("readable")
    assert "What does it say? (reading page 2 of 3)" in texts("reading")
    assert "paused while someone talks to Domovoi" in texts("paused")
    for hidden in ("never", "failed", "no_reader"):
        assert "What does it say?" not in texts(hidden), hidden


def test_the_scan_sheet_and_the_scan_log_pass_on_read() -> None:
    src = (REPO_ROOT / "web" / "static" / "printers.jsx").read_text(encoding="utf-8")
    button = src[src.index("const PrintingScanButton"):src.index("Settings → Printers & scanners")]
    assert "<ScanSheet" in button and "onRead=" in button and "<ScanReadSheet" in button
    log = src[src.index("const PrintingScanLog"):src.index("const PrinterSupplies")]
    assert "<ScanReadButton scan={s} onRead=" in log
    sheet = src[src.index("const ScanSheet"):src.index("const PrintingScanButton")]
    assert "<ScanReadButton scan={scan} onRead={onRead}/>" in sheet


# ─── With the database ──────────────────────────────────────────────────
async def _q(sql: str, params: dict | None = None) -> list[dict[str, Any]]:
    async with session_scope() as s:
        r = await s.execute(text(sql), params or {})
        return [dict(x) for x in r.mappings().all()] if r.returns_rows else []


@requires_db
async def test_a_scan_is_read_page_by_page_and_becomes_readable(db_session, docs) -> None:
    from domovoi.doc_extract import runner as doc_runner

    pdf = scan_pdf(docs / "Scans" / "Scan 2026-11-16 09.12.pdf", [LETTER, ["Dear Grandma"]])
    read_id = await runner_mod.queue_scan("Scans/Scan 2026-11-16 09.12.pdf", scan_id="c" * 32)
    assert read_id
    before = await store.read_json_for(await store.get(read_id))
    assert before["state"] == "queued" and before["reading"] and not before["readable"]
    vision = FakeVision()
    fake = FakeOcr({1: good(LETTER), 2: poor()})
    r = runner_mod.Runner(vision=vision, ocr_fn=fake, poll_sec=0.01, quiet_sec=0)
    assert await r.run_next() == read_id
    after = await store.read_json_for(await store.get(read_id))
    assert after["state"] == "done" and after["readable"] and after["pages"] == 2 and after["pages_done"] == 2
    assert after["pages_guessed"] == 1 and after["pages_unread"] == 0
    rows = await store.pages(read_id)
    assert [(p["page"], p["method"]) for p in rows] == [(1, "ocr"), (2, "vision")]
    assert rows[0]["confidence"] > 90
    # Indexed like any document: found by the file's bytes, a copy too.
    got = await doc_runner.extract(pdf, "pdf")
    assert "due on March 3, 2027" in got["parts"][0]["text"]
    assert store.MIGHT_SAY in got["parts"][1]["text"]
    assert "scan_read" in [n["code"] for n in got["notes"]]
    copy = docs / "copy.pdf"
    shutil.copyfile(pdf, copy)
    assert (await doc_runner.extract(copy, "pdf"))["scan_read_pages"] == [1, 2]
    # "What does it say?": the letter reader's summary, kept once made.
    out = await says.summary(await store.get(read_id))
    assert out["ai"] and "stub company" in out["content"]
    assert "It's from the stub company." in out["said"]
    assert any(n["code"] == "scan_pages_guessed" for n in out["notes"])
    assert (await store.get(read_id))["summary"]["content"] == out["content"]
    full = await says.full(await store.get(read_id))
    assert "Page 1\nACME WATER COMPANY" in full["text"] and store.MIGHT_SAY in full["text"]


@requires_db
async def test_an_edited_file_is_not_given_the_old_text(db_session, docs) -> None:
    from domovoi.doc_extract import runner as doc_runner

    pdf = scan_pdf(docs / "Scans" / "Scan.pdf", [LETTER])
    read_id = await runner_mod.queue_scan("Scans/Scan.pdf")
    r = runner_mod.Runner(vision=FakeVision(), ocr_fn=FakeOcr({1: good(LETTER)}), poll_sec=0.01, quiet_sec=0)
    await r.run_next()
    assert (await store.get(read_id))["state"] == "done"
    scan_pdf(pdf, [["Something else entirely"]])
    with pytest.raises(doc_runner.ExtractFailed):
        await doc_runner.extract(pdf, "pdf")


@requires_db
async def test_a_restart_carries_on_from_the_next_page(db_session, docs) -> None:
    scan_pdf(docs / "Scans" / "Scan.pdf", [LETTER, LETTER, LETTER])
    read_id = await runner_mod.queue_scan("Scans/Scan.pdf")
    # The core stopped after page 1 was saved, mid-read.
    await _q("UPDATE scan_reads SET state = 'running', attempts = 1, pages = 3 WHERE id = :id", {"id": read_id})
    await runner_mod.save_page(read_id, 1, "ocr", "page one", 95.0, 2)
    fake = FakeOcr({2: good(LETTER), 3: good(LETTER)})
    r = runner_mod.Runner(vision=FakeVision(), ocr_fn=fake, poll_sec=0.01, quiet_sec=0)
    assert await r.run_next() == read_id
    assert fake.calls == [2, 3], "page 1 isn't read again"
    row = await store.get(read_id)
    assert row["state"] == "done" and row["attempts"] == 2
    assert [p["text"] for p in await store.pages(read_id)][0] == "page one"


@requires_db
async def test_a_read_that_keeps_stopping_the_server_fails_cleanly(db_session, docs) -> None:
    scan_pdf(docs / "Scans" / "Scan.pdf", [LETTER])
    read_id = await runner_mod.queue_scan("Scans/Scan.pdf")
    await _q("UPDATE scan_reads SET state = 'running', attempts = 3 WHERE id = :id", {"id": read_id})
    fake = FakeOcr({})
    await runner_mod.Runner(vision=FakeVision(), ocr_fn=fake).run_next()
    row = await store.get(read_id)
    assert row["state"] == "failed" and row["error_code"] == runner_mod.CODE_INTERRUPTED and fake.calls == []
    # And one whose file went fails with a plain reason.
    gone = await runner_mod.queue_scan("Scans/Scan.pdf")
    (docs / "Scans" / "Scan.pdf").unlink()
    await runner_mod.Runner(vision=FakeVision(), ocr_fn=fake).run_next()
    assert (await store.get(gone))["error"] == "The scan isn't in Documents any more."


@requires_db
async def test_ai_off_the_scan_is_still_read_and_answered_with_its_start(db_session, docs, monkeypatch) -> None:
    monkeypatch.setattr(ocr, "find", lambda: "/usr/bin/tesseract")  # a server with tesseract
    scan_pdf(docs / "Scans" / "Scan.pdf", [LETTER, ["Dear Grandma"]])
    with ai_features.override(False):
        read_id = await runner_mod.queue_scan("Scans/Scan.pdf")
        assert read_id, "OCR isn't AI: a server with tesseract reads scans with AI off"
        vision = FakeVision()
        await runner_mod.Runner(vision=vision, ocr_fn=FakeOcr({1: good(LETTER), 2: poor()}),
                                poll_sec=0.01, quiet_sec=0).run_next()
        assert vision.calls == 0
        row = await store.get(read_id)
        assert [p["method"] for p in await store.pages(read_id)] == ["ocr", "none"]
        out = await says.summary(row)
        assert out["ai"] is False and out["content"] is None
        assert out["said"].startswith("Here's how it starts: ACME WATER COMPANY Account number")
        assert any(n["code"] == "scan_pages_unread" and "AI features are off" in n["text"] for n in out["notes"])


@requires_db
async def test_the_routes_follow_a_read_and_refuse_paths_outside_scans(db_session, docs) -> None:
    import httpx

    from domovoi import admin_auth
    from domovoi.main import app

    scan_pdf(docs / "Scans" / "Scan.pdf", [LETTER])
    app.dependency_overrides[admin_auth.require_device_read] = lambda: None
    app.dependency_overrides[admin_auth.require_device] = lambda: None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/v1/scan/reads", params={"path": "Scans/Scan.pdf"})
            assert r.status_code == 200 and r.json()["read"] is None and r.json()["can_read"] is True
            r = await c.post("/v1/scan/reads", json={"path": "Scans/Scan.pdf"})
            assert r.status_code == 201, r.text
            read = r.json()
            assert read["state"] == "queued"
            again = await c.post("/v1/scan/reads", json={"path": "Scans/Scan.pdf"})
            assert again.json()["id"] == read["id"], "the same bytes aren't read twice"
            assert (await c.get(f"/v1/scan/reads/{read['id']}/summary")).status_code == 409
            await runner_mod.Runner(vision=FakeVision(), ocr_fn=FakeOcr({1: good(LETTER)}), poll_sec=0.01,
                                    quiet_sec=0).run_next()
            r = await c.get(f"/v1/scan/reads/{read['id']}")
            assert r.json()["readable"] is True
            r = await c.get(f"/v1/scan/reads/{read['id']}/summary")
            assert r.status_code == 200 and r.json()["said"]
            r = await c.get(f"/v1/scan/reads/{read['id']}/text")
            assert r.json()["pages"][0]["method"] == "ocr" and "ACME" in r.json()["text"]
            for bad in ("../secret.pdf", "Scans/../x.pdf", "Other/Scan.pdf", "Scans/.hidden.pdf", "Scans/a.docx"):
                assert (await c.get("/v1/scan/reads", params={"path": bad})).status_code == 400, bad
            assert (await c.get("/v1/scan/reads/" + "0" * 32)).status_code == 404
    finally:
        app.dependency_overrides.pop(admin_auth.require_device_read, None)
        app.dependency_overrides.pop(admin_auth.require_device, None)


@requires_db
async def test_yes_by_voice_reads_it_now_and_offers_the_whole_text(db_session, docs, monkeypatch) -> None:
    from domovoi import scan_reader

    scan_pdf(docs / "Scans" / "Scan.pdf", [LETTER])
    monkeypatch.setattr(ocr, "read_page", FakeOcr({1: good(LETTER)}))
    read_id = await runner_mod.queue_scan("Scans/Scan.pdf")
    # A voice turn is under way: the background read would wait, so the
    # answer reads the page itself.
    async with voice_activity.turn("satellite"):
        said, more = await scan_reader.answer_for_voice("Scans/Scan.pdf")
    assert more is True
    assert said.startswith("It's from the stub company.")
    assert (await store.get(read_id))["state"] == "done"


# ─── Security audit round 6 (DOC-11, DOC-12, FE-11) ─────────────────────
# Each test replays the finding's proof of concept; the attack fails now.

@pytest.fixture
def fresh_says():
    says._reset_for_tests()
    yield
    says._reset_for_tests()


def _done_read(read_id: str = "d" * 32) -> dict[str, Any]:
    return {"id": read_id, "state": "done", "summary": None, "path": "Scans/x.pdf", "sha256": "0"}


def _pages_of(monkeypatch, body: str = "\n".join(LETTER)) -> None:
    async def rows(read_id):
        return [{"page": 1, "method": "ocr", "text": body}]

    async def save(read_id, out):
        return None

    async def get(read_id):
        return None

    monkeypatch.setattr(store, "pages", rows)
    monkeypatch.setattr(store, "save_summary", save)
    monkeypatch.setattr(store, "get", get)


async def test_doc11_ten_asks_at_once_start_one_summary(monkeypatch, fresh_says) -> None:
    _pages_of(monkeypatch)
    calls = []
    gate = asyncio.Event()

    async def writer(system: str, user: str) -> str:
        calls.append(1)
        await gate.wait()
        return "FROM: ACME\nABOUT: A water bill.\nTODO: Pay it.\nUNCLEAR: nothing"

    # Before: each GET ran its own 500-token generation, with no lock.
    asks = [asyncio.create_task(says.summary(_done_read(), writer=writer, gate=True)) for _ in range(10)]
    await asyncio.sleep(0.3)
    gate.set()
    outs = await asyncio.gather(*asks)
    assert len(calls) == 1
    assert all(o["ai"] and o == outs[0] for o in outs)


async def test_doc11_a_summary_asked_from_a_screen_waits_for_voice(monkeypatch, fresh_says) -> None:
    _pages_of(monkeypatch)
    monkeypatch.setattr(says._GATE, "poll_sec", 0.01)
    monkeypatch.setattr(says._GATE, "quiet_sec", 0.05)
    calls = []

    async def writer(system: str, user: str) -> str:
        calls.append(time.monotonic())
        return "FROM: ACME\nABOUT: A bill.\nTODO: Pay.\nUNCLEAR: nothing"

    async with voice_activity.turn("satellite"):
        ask = asyncio.create_task(says.summary(_done_read(), writer=writer, gate=True))
        await asyncio.sleep(0.3)
        assert calls == [], "nothing is asked of the model while a room is answering"
        ended = time.monotonic()
    assert (await asyncio.wait_for(ask, 10))["ai"]
    assert calls and calls[0] - ended >= 0.04


async def test_doc11_one_summary_at_a_time_in_the_house(monkeypatch, fresh_says) -> None:
    _pages_of(monkeypatch)
    running = []
    most = []

    async def writer(system: str, user: str) -> str:
        running.append(1)
        most.append(len(running))
        await asyncio.sleep(0.1)
        running.pop()
        return "FROM: ACME\nABOUT: A bill.\nTODO: Pay.\nUNCLEAR: nothing"

    await asyncio.gather(*(says.summary(_done_read(c * 32), writer=writer, gate=True) for c in "abcd"))
    assert max(most) == 1 and len(most) == 4


async def test_doc11_a_failed_summary_isnt_tried_again_on_every_ask(monkeypatch, fresh_says) -> None:
    _pages_of(monkeypatch)
    monkeypatch.setattr("domovoi.voice_first.STEP_TRIES", 1)
    calls = []

    async def broken(system: str, user: str) -> str:
        calls.append(1)
        raise RuntimeError("the model service is down")

    first = await says.summary(_done_read(), writer=broken, gate=True)
    assert first["failed"] and first["said"].startswith("I couldn't sum it up just now.")
    for _ in range(20):
        again = await says.summary(_done_read(), writer=broken, gate=True)
        assert again["failed"]
    assert len(calls) == 1


async def test_doc11_read_now_keeps_its_budget_inside_a_page(tmp_path, monkeypatch) -> None:
    # Before: the 20 s budget was looked at only between pages, and one page
    # (drawn, then OCR) could take minutes.
    pdf = scan_pdf(tmp_path / "Scan.pdf", [LETTER])

    class SlowOcr:
        async def __call__(self, png, *, hold=None, langs=None):
            await asyncio.sleep(30)

    row = {"id": "e" * 32, "state": "running", "path": "Scans/Scan.pdf", "sha256": None, "pages": 1,
           "languages": "eng", "attempts": 1, "started_at": None}

    async def get(read_id):
        return row

    async def saved(read_id):
        return set()

    async def update(job_id, **cols):
        return None

    monkeypatch.setattr(store, "get", get)
    monkeypatch.setattr(runner_mod, "saved_pages", saved)
    monkeypatch.setattr(runner_mod, "_update", update)
    monkeypatch.setattr(runner_mod, "resolve", lambda rel: pdf)
    monkeypatch.setattr(runner_mod, "work_root", lambda: tmp_path / "work")
    r = runner_mod.Runner(vision=FakeVision(), ocr_fn=SlowOcr())
    t0 = asyncio.get_running_loop().time()
    await runner_mod.read_now("e" * 32, budget_sec=0.5, runner=r)
    assert asyncio.get_running_loop().time() - t0 < 5


@requires_db
async def test_doc11_a_read_can_be_stopped_queued_or_running(db_session, docs) -> None:
    import httpx

    from domovoi import admin_auth
    from domovoi.main import app

    scan_pdf(docs / "Scans" / "Queued.pdf", [LETTER])
    scan_pdf(docs / "Scans" / "Long.pdf", [LETTER, LETTER, LETTER, LETTER])
    app.dependency_overrides[admin_auth.require_device_read] = lambda: None
    app.dependency_overrides[admin_auth.require_device] = lambda: None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await _q("DELETE FROM scan_reads")
            queued = (await c.post("/v1/scan/reads", json={"path": "Scans/Queued.pdf"})).json()
            r = await c.post(f"/v1/scan/reads/{queued['id']}/cancel")
            assert r.status_code == 200 and r.json()["state"] == "cancelled"
            fake = FakeOcr({})
            assert await runner_mod.Runner(vision=FakeVision(), ocr_fn=fake).run_next() is None
            # A running one: stopped after its first page, at the next look.
            long = (await c.post("/v1/scan/reads", json={"path": "Scans/Long.pdf"})).json()

            class StopAfterFirst(FakeOcr):
                async def __call__(self, png, *, hold=None, langs=None):
                    got = await super().__call__(png, hold=hold, langs=langs)
                    if len(self.calls) == 1:
                        assert (await c.post(f"/v1/scan/reads/{long['id']}/cancel")).status_code == 200
                    return got

            stopper = StopAfterFirst({})
            await runner_mod.Runner(vision=FakeVision(), ocr_fn=stopper, poll_sec=0.01, quiet_sec=0).run_next()
            row = await store.get(long["id"])
            assert row["state"] == "cancelled" and stopper.calls == [1]
            assert (await c.post("/v1/scan/reads/" + "0" * 32 + "/cancel")).status_code == 404
    finally:
        app.dependency_overrides.pop(admin_auth.require_device_read, None)
        app.dependency_overrides.pop(admin_auth.require_device, None)


@requires_db
async def test_doc11_a_read_running_past_its_time_ends_plainly(db_session, docs) -> None:
    scan_pdf(docs / "Scans" / "Scan.pdf", [LETTER, LETTER])
    read_id = await runner_mod.queue_scan("Scans/Scan.pdf")
    await _q("UPDATE scan_reads SET state = 'running', attempts = 1, started_at = now() - interval '5 hours' "
             "WHERE id = :id", {"id": read_id})
    fake = FakeOcr({})
    await runner_mod.Runner(vision=FakeVision(), ocr_fn=fake, poll_sec=0.01, quiet_sec=0).run_next()
    row = await store.get(read_id)
    assert row["state"] == "failed" and row["error_code"] == runner_mod.CODE_TOO_LONG and fake.calls == []


def test_doc11_the_summary_route_is_gated_and_stop_is_device_tier() -> None:
    import inspect

    from domovoi import admin_auth
    from domovoi.main import app as core_app
    from domovoi.scan_reader import routes
    from domovoi.tests.route_walk import iter_route_contexts
    from web.backend.main import app as web_app

    assert "gate=True" in inspect.getsource(routes.read_summary)

    def gates(app, method: str, path: str) -> set:
        for rc in iter_route_contexts(app.routes):
            if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or ()):
                out, stack = set(), [rc.dependant]
                while stack:
                    for dep in stack.pop().dependencies:
                        if dep.call is not None:
                            out.add(dep.call)
                        stack.append(dep)
                return out
        raise AssertionError(f"{method} {path} is not a route")

    assert admin_auth.require_device in gates(core_app, "POST", "/v1/scan/reads/{read_id}/cancel")
    assert admin_auth.require_device in gates(web_app, "POST", "/api/scan/reads/{read_id}/cancel")


def test_doc12_a_link_anywhere_on_the_path_is_refused(tmp_path, monkeypatch) -> None:
    d = tmp_path / "Documents"
    (d / "Scans").mkdir(parents=True)
    (d / "tls").mkdir()
    scan_pdf(d / "tls" / "statement.pdf", [LETTER])
    scan_pdf(d / "Scans" / "real.pdf", [LETTER])
    monkeypatch.setattr(settings, "documents_dir", str(d))
    try:
        (d / "Scans" / "link-inside.pdf").symlink_to(d / "tls" / "statement.pdf")
        (d / "Scans" / "linked-dir").symlink_to(d / "tls", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system can't make links")
    # Before: Scans/link-inside.pdf resolved to tls/statement.pdf.
    assert runner_mod.resolve("Scans/link-inside.pdf") is None
    assert runner_mod.resolve("Scans/linked-dir/statement.pdf") is None
    assert runner_mod.resolve("Scans/real.pdf") == d.resolve() / "Scans" / "real.pdf"
    assert runner_mod.resolve("Scans/../tls/statement.pdf") is None


@requires_db
async def test_doc12_a_replaced_scan_is_not_shown_the_old_text(db_session, docs, monkeypatch) -> None:
    import httpx

    from domovoi import admin_auth, scan_reader
    from domovoi.main import app

    letter_b = ["LETTER B FROM THE BANK", "Your card is blocked, call this number now."]
    path_b = scan_pdf(docs / "Scans" / "x.pdf", [letter_b])
    bytes_b = path_b.read_bytes()
    read_id = await runner_mod.queue_scan("Scans/x.pdf")
    await runner_mod.Runner(vision=FakeVision(), ocr_fn=FakeOcr({1: good(letter_b)}), poll_sec=0.01,
                            quiet_sec=0).run_next()
    assert (await store.get(read_id))["state"] == "done"

    async def shown() -> Any:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/v1/scan/reads", params={"path": "Scans/x.pdf"})
        assert r.status_code == 200
        return r.json()["read"]

    app.dependency_overrides[admin_auth.require_device_read] = lambda: None
    try:
        assert (await shown())["id"] == read_id
        # Replaced with letter A: B's text is not shown for it any more.
        scan_pdf(docs / "Scans" / "x.pdf", [LETTER])
        assert await shown() is None
        # B's very bytes back: its read is B's again.
        path_b.write_bytes(bytes_b)
        assert (await shown())["id"] == read_id
        scan_pdf(docs / "Scans" / "x.pdf", [LETTER])
    finally:
        app.dependency_overrides.pop(admin_auth.require_device_read, None)
    # By voice: a new read of what is there now, never B's words.
    monkeypatch.setattr(ocr, "read_page", FakeOcr({1: good(LETTER)}))
    said, _more = await scan_reader.answer_for_voice("Scans/x.pdf")
    assert "BANK" not in said.upper() and "blocked" not in said
    assert (await store.latest_for_path("Scans/x.pdf"))["id"] != read_id


@requires_db
async def test_doc12_pages_are_drawn_from_the_copy_that_was_hashed(db_session, docs, monkeypatch) -> None:
    pdf = scan_pdf(docs / "Scans" / "Scan.pdf", [LETTER, LETTER])
    read_id = await runner_mod.queue_scan("Scans/Scan.pdf")
    drawn: list[Path] = []
    real_render = pages.render

    async def render(src, page, out, dpi=pages.DPI):
        drawn.append(Path(src))
        if page == 1:
            scan_pdf(pdf, [["Something else entirely"], ["and more"]])   # swapped mid-read
        return await real_render(src, page, out, dpi)

    monkeypatch.setattr(pages, "render", render)
    await runner_mod.Runner(vision=FakeVision(), ocr_fn=FakeOcr({}), poll_sec=0.01, quiet_sec=0).run_next()
    row = await store.get(read_id)
    assert row["state"] == "done"
    # Before: the hash was of the first file and the pages came from the path.
    assert all(d.name == "source.pdf" and d.parent.name == read_id for d in drawn)
    assert row["sha256"] != store.sha256_of(pdf)


def test_fe11_the_scan_sheet_shows_no_links_and_offers_stop() -> None:
    src = (REPO_ROOT / "web" / "static" / "printers.jsx").read_text(encoding="utf-8")
    sheet = src[src.index("const ScanReadSheet"):src.index("const ScanSheet")]
    assert "chatMarkdownHtml(t, { links: false })" in sheet
    assert "chatMarkdownHtml(t)" not in sheet
    assert "/cancel`" in sheet and "Stop reading" in sheet


# ─── Reads stay bound to the bytes they read ────────────────────────────
@requires_db
async def test_pages_of_bytes_replaced_by_another_read_are_not_saved(db_session, docs) -> None:
    scan_pdf(docs / "Scans" / "Scan.pdf", [LETTER])
    read_id = await runner_mod.queue_scan("Scans/Scan.pdf")
    row = await store.get(read_id)
    background = runner_mod.Job.from_row(row)
    await runner_mod._adopt_sha(background, row["sha256"])
    await runner_mod.save_page(read_id, 1, "ocr", "old page", 90.0, 2, sha=background.sha256)
    # A read by voice finds other bytes at the path and takes the row over.
    by_voice = runner_mod.Job.from_row(row)
    await runner_mod._adopt_sha(by_voice, "f" * 64)
    assert by_voice.pages == 0 and await runner_mod.saved_pages(read_id) == set()
    with pytest.raises(runner_mod._Superseded):
        await runner_mod.save_page(read_id, 2, "ocr", "old page", 90.0, 2, sha=background.sha256)
    assert await runner_mod.saved_pages(read_id) == set()
    await runner_mod.save_page(read_id, 1, "ocr", "new page", 90.0, 2, sha=by_voice.sha256)
    assert await runner_mod.saved_pages(read_id) == {1}


@requires_db
async def test_the_by_id_routes_refuse_a_read_of_replaced_bytes(db_session, docs) -> None:
    import httpx

    from domovoi import admin_auth
    from domovoi.main import app

    scan_pdf(docs / "Scans" / "Scan.pdf", [LETTER])
    read_id = await runner_mod.queue_scan("Scans/Scan.pdf")
    await runner_mod.Runner(vision=FakeVision(), ocr_fn=FakeOcr({1: good(LETTER)}), poll_sec=0.01,
                            quiet_sec=0).run_next()
    app.dependency_overrides[admin_auth.require_device_read] = lambda: None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            for tail in ("", "/text"):
                assert (await c.get(f"/v1/scan/reads/{read_id}{tail}")).status_code == 200
            scan_pdf(docs / "Scans" / "Scan.pdf", [["Something else entirely"]])
            for tail in ("", "/summary", "/text"):
                assert (await c.get(f"/v1/scan/reads/{read_id}{tail}")).status_code == 404, tail
    finally:
        app.dependency_overrides.pop(admin_auth.require_device_read, None)


@requires_db
async def test_asking_for_a_read_checks_the_size_first_and_hashes_once(db_session, docs, monkeypatch) -> None:
    import httpx

    from domovoi import admin_auth
    from domovoi.main import app

    pdf = scan_pdf(docs / "Scans" / "Scan.pdf", [LETTER])
    hashed: list[Path] = []
    real = store.sha256_of

    def counting(path):
        hashed.append(Path(path))
        return real(path)

    monkeypatch.setattr(store, "sha256_of", counting)
    store._SHA_CACHE.clear()
    app.dependency_overrides[admin_auth.require_device] = lambda: None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            first = await c.post("/v1/scan/reads", json={"path": "Scans/Scan.pdf"})
            again = await c.post("/v1/scan/reads", json={"path": "Scans/Scan.pdf"})
            assert first.status_code == 201 and again.json()["id"] == first.json()["id"]
            assert len(hashed) == 1, "an unchanged file is hashed once"
            monkeypatch.setattr(runner_mod, "MAX_SCAN_BYTES", pdf.stat().st_size - 1)
            hashed.clear()
            r = await c.post("/v1/scan/reads", json={"path": "Scans/Scan.pdf"})
            assert r.status_code == 413 and hashed == []
    finally:
        app.dependency_overrides.pop(admin_auth.require_device, None)
