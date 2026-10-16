"""Sheet saves are bounded and off the event loop (2026-10-16 delta
security review, S6; web/backend/api/documents.py).

The review's proof: an in-place save of the editor's one-cell change read
the whole existing file on the web's event loop: a 2.4 MB workbook held
643 MB for 7.4 s, a 101 MB CSV 1.6 GB. Now an existing sheet over
_SHEET_SAVE_MAX_INFLATED_BYTES (.xlsx, unpacked) or _SHEET_SAVE_MAX_CSV_BYTES
(.csv) is refused with 413 before anything is read, and the save runs in
the one-thread ``sheets`` pool. DB-free (the fake auth of the other
documents tests).
"""

from __future__ import annotations

import asyncio
import threading
import time
import tracemalloc
import zipfile

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook

from domovoi import executors
from domovoi.config import settings
from domovoi.tests.auth_testkit import install_fake_db
from web.backend.api import documents as docs
from web.backend.main import app


@pytest.fixture(autouse=True)
def _pre_setup_install(monkeypatch):
    install_fake_db(monkeypatch, admin=False)


@pytest.fixture
def docs_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    return tmp_path


def _put(name: str, rows):
    with TestClient(app, headers={"X-Requested-With": "domovoi-tests"}) as c:
        return c.put(f"/api/documents/sheet/{name}", json={"rows": rows})


def _big_csv(path, nbytes: int) -> None:
    line = ",".join(f"cell{i}" for i in range(30)) + "\n"
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(line * (nbytes // len(line) + 1))


def _big_xlsx(path, inflated: int) -> None:
    """A workbook whose first sheet's XML unpacks to about ``inflated``
    bytes (numbers, which compress well), written without openpyxl."""
    Workbook().save(path)
    tmp = path.with_name(path.name + ".tmp")
    row = "".join(f'<c r="{chr(65 + i)}{{r}}"><v>1</v></c>' for i in range(20))
    per_row = len(row) + 20
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            if info.filename != "xl/worksheets/sheet1.xml":
                zout.writestr(info, zin.read(info.filename))
                continue
            with zout.open("xl/worksheets/sheet1.xml", "w") as fh:
                fh.write(b'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns='
                         b'"http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>')
                for r in range(1, inflated // per_row + 2):
                    fh.write(f'<row r="{r}">{row.format(r=r)}</row>'.encode())
                fh.write(b"</sheetData></worksheet>")
    tmp.replace(path)


def test_a_csv_too_large_to_save_into_is_refused_before_it_is_read(docs_dir) -> None:
    target = docs_dir / "bank.csv"
    _big_csv(target, docs._SHEET_SAVE_MAX_CSV_BYTES + 1024)
    before = target.read_bytes()
    tracemalloc.start()
    try:
        r = _put("bank.csv", [[{"v": "2", "f": None}]])
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert r.status_code == 413, r.text
    assert "too large to save from the sheet editor" in r.json()["detail"]
    assert "Nothing was changed" in r.json()["detail"]
    assert target.read_bytes() == before
    assert peak < len(before), "the file was read to refuse it"


def test_a_workbook_too_large_to_save_into_is_refused(docs_dir) -> None:
    target = docs_dir / "budget.xlsx"
    _big_xlsx(target, docs._SHEET_SAVE_MAX_INFLATED_BYTES + 64 * 1024)
    assert target.stat().st_size < 4 * 1024 * 1024        # small on disk: the zip says the rest
    before = target.read_bytes()
    r = _put("budget.xlsx", [[{"v": "2", "f": None}]])
    assert r.status_code == 413, r.text
    assert "unpacks to" in r.json()["detail"]
    assert target.read_bytes() == before


def test_sheets_under_the_limits_still_save(docs_dir) -> None:
    _big_csv(docs_dir / "ok.csv", 1024 * 1024)
    assert _put("ok.csv", [[{"v": "changed", "f": None}]]).status_code == 200
    assert (docs_dir / "ok.csv").read_text(encoding="utf-8").startswith("changed")
    _big_xlsx(docs_dir / "ok.xlsx", 1024 * 1024)
    assert _put("ok.xlsx", [[{"v": "changed", "f": None}]]).status_code == 200
    assert _put("new.xlsx", [[{"v": "fresh", "f": None}]]).status_code == 200


def test_a_save_runs_in_the_sheets_pool_and_the_loop_keeps_running(docs_dir, monkeypatch) -> None:
    seen: list[str] = []
    real = docs._write_sheet_grid

    def slow(target, rows):
        seen.append(threading.current_thread().name)
        time.sleep(1.0)
        return real(target, rows)

    monkeypatch.setattr(docs, "_write_sheet_grid", slow)

    async def go():
        gaps: list[float] = []
        done = asyncio.Event()

        async def beat():
            last = time.perf_counter()
            while not done.is_set():
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        task = asyncio.create_task(beat())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"X-Requested-With": "domovoi-tests"}) as c:
            r = await c.put("/api/documents/sheet/slow.csv", json={"rows": [[{"v": "1", "f": None}]]})
        done.set()
        await task
        return r, max(gaps)

    r, gap = asyncio.run(go())
    assert r.status_code == 200, r.text
    assert gap < 0.5, f"the loop stalled {gap:.2f} s"
    assert seen and seen[0].startswith("domovoi-sheets")
    assert executors.POOL_SIZES["sheets"] == 1
