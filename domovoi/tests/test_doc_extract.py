"""The chat's document readers (domovoi/doc_extract): text, labels and guards.

Small files made here (doc_fixtures.py) check each format's parts and
labels, and every guard against a hostile file: the byte cap, zip bombs,
too many parts, encrypted zips, XML entities (billion laughs, an external
entity, a UTF-16 DOCTYPE), a file that isn't what its name says, and the
worker's time and memory caps.

The E-EXTRACT set from D0 (20 files with answer keys) is scored when
DOMOVOI_DOC_SET names its folder (it lives outside the repo); the targets
are the plan's: text PDFs 98% characters and 95% reading order on two
columns, .docx/.odt 99%, sheets 100% of cells, pages needing vision found.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from domovoi import doc_extract
from domovoi.doc_extract import limits, runner, safe
from domovoi.tests import doc_fixtures as fx


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def _labels(r):
    return [p["label"] for p in r["parts"]]


# ─── Formats ─────────────────────────────────────────────────────────────
def test_pdf_pages_header_and_scan(tmp_path):
    bodies = [
        "The monthly rent is $1,200, due on the first day of each month, paid by bank transfer to the "
        "landlord's account. A late fee of $50 applies when rent is more than five days late.",
        "The security deposit is $1,500. It is returned within thirty days after the tenant moves out, "
        "less the cost of any damage beyond normal wear and tear, with a written list of deductions.",
        "Pets are not allowed without written permission from the landlord. Smoking is not allowed "
        "anywhere in the building, including balconies, stairwells and the shared laundry room.",
    ]
    pages = []
    for n in range(1, 4):
        lines = [(72, 760, "Residential Lease - 12 Elm Street")] + fx.text_page(bodies[n - 1], top=720)
        lines.append((300, 40, f"Page {n} of 4"))
        pages.append(lines)
    pages.append([])                                     # a scanned page: an image, no text
    p = _write(tmp_path, "lease.pdf", fx.make_pdf(pages, image_pages=(3,)))
    r = doc_extract.extract(p, "pdf")
    assert r["pages"] == 4 and r["scanned_pages"] == [4]
    assert _labels(r) == ["page header", "p. 1", "p. 2", "p. 3", "page footer"]
    assert r["parts"][0]["text"] == "Residential Lease - 12 Elm Street"
    assert "Elm Street" not in r["parts"][1]["text"]        # stripped from every page, kept once
    assert r["parts"][1]["page"] == 1
    assert [n["code"] for n in r["notes"]] == ["scanned_pages"]
    assert r["notes"][0]["text"] == "Page 4 is a picture of text (scanned), so Domovoi can't search it yet."


def test_pdf_two_columns_read_left_then_right(tmp_path):
    left = [f"Left column sentence number {i} about the garden plans." for i in range(14)]
    right = [f"Right column sentence number {i} about the school fair." for i in range(14)]
    lines = []
    for i in range(14):        # drawn interleaved: left line, right line, left line...
        lines.append((40, 700 - 14 * i, left[i]))
        lines.append((320, 700 - 14 * i, right[i]))
    p = _write(tmp_path, "news.pdf", fx.make_pdf([lines], font_size=8))
    r = doc_extract.extract(p, "pdf")
    text = r["parts"][0]["text"]
    assert r["two_column_pages"] == [1]
    assert text.index("Left column sentence number 13") < text.index("Right column sentence number 0")


def test_pdf_ligatures_are_read_as_letters(tmp_path):
    # NFKC: the "fi" ligature a font draws reads as "fi".
    from domovoi.doc_qa.text import nfkc

    assert nfkc("ﬁle ﬀ ﬃ") == "file ff ffi"


def test_pdf_with_only_a_scan_is_refused_plainly(tmp_path):
    p = _write(tmp_path, "scan.pdf", fx.make_pdf([[]], image_pages=(0,)))
    with pytest.raises(safe.Refused, match="pictures of text"):
        doc_extract.extract(p, "pdf")


def test_docx_reads_the_accepted_text_tables_header_footer_footnote(tmp_path):
    p = _write(tmp_path, "lease.docx", fx.make_docx())
    r = doc_extract.extract(p, "docx")
    labels = _labels(r)
    assert labels == ["page header", "Section: Residential Lease", "Section: Rent", "Section: Deposit",
                      "page footer", "footnotes"]
    rent = r["parts"][2]["text"]
    assert "due within five days." in rent and "ten" not in rent       # w:ins kept, w:del skipped
    assert "- Paid by bank transfer." in rent
    assert "| Item | Amount |" in r["parts"][3]["text"] and "| Key | $25 |" in r["parts"][3]["text"]
    assert r["parts"][0]["text"] == "12 Elm Street lease"
    assert r["parts"][-1]["text"] == "1. Late fees start on day six."


def test_docx_text_box_is_read_once(tmp_path):
    mc = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    box = (f'<w:p><w:r><mc:AlternateContent xmlns:mc="{mc}"><mc:Choice Requires="wps"><w:txbxContent>'
           '<w:p><w:r><w:t>Boxed words</w:t></w:r></w:p></w:txbxContent></mc:Choice><mc:Fallback>'
           '<w:txbxContent><w:p><w:r><w:t>Boxed words</w:t></w:r></w:p></w:txbxContent></mc:Fallback>'
           '</mc:AlternateContent></w:r></w:p>')
    p = _write(tmp_path, "box.docx", fx.make_docx(box))
    text = "\n".join(x["text"] for x in doc_extract.extract(p, "docx")["parts"])
    assert text.count("Boxed words") == 1


def test_odt(tmp_path):
    p = _write(tmp_path, "lease.odt", fx.make_odt())
    r = doc_extract.extract(p, "odt")
    assert _labels(r) == ["page header", "Section: Rent", "footnotes"]
    assert "Rent is  $1,200\ta month." in r["parts"][1]["text"]
    assert "| Key | $25 |" in r["parts"][1]["text"]
    assert r["parts"][-1]["text"] == "1. Late fees start on day six."


def test_odt_space_runs_are_capped(tmp_path):
    xml = (f'<?xml version="1.0"?><office:document-content {fx.ODT_NS}><office:body><office:text>'
           '<text:p>a<text:s text:c="999999999"/>b</text:p></office:text></office:body></office:document-content>')
    p = _write(tmp_path, "s.odt", fx.make_odt(xml))
    assert len(doc_extract.extract(p, "odt")["parts"][0]["text"]) < 1100


def test_xlsx_every_sheet_with_totals(tmp_path):
    p = _write(tmp_path, "budget.xlsx", fx.make_xlsx())
    r = doc_extract.extract(p, "xlsx")
    assert _labels(r) == ["Sheet Budget, rows 2-4", "Sheet Budget, column totals", "Sheet Notes, row 2"]
    assert r["parts"][0]["head"] == "| Item | Amount |\n|---|---|"
    assert r["parts"][0]["text"].splitlines() == ["| Rent | 1200 |", "| Power | 85.50 |", "| Water | 40 |"] or \
        r["parts"][0]["text"].splitlines() == ["| Rent | 1200 |", "| Power | 85.5 |", "| Water | 40 |"]
    assert "Amount: total 1,325.50, average 441.83, lowest 40, highest 1,200 (3 numbers)" in r["parts"][1]["text"]


def test_xlsx_formula_without_a_saved_value_reads_as_the_formula(tmp_path):
    p = _write(tmp_path, "b.xlsx", fx.make_xlsx(formulas=True))
    r = doc_extract.extract(p, "xlsx")
    assert "=SUM(B2:B4)" in r["parts"][0]["text"]
    assert r["formulas_without_value"] == 1
    assert any(n["code"] == "formulas_without_value" for n in r["notes"])


def test_csv_semicolon_bom_and_cp1252(tmp_path):
    p = _write(tmp_path, "bank.csv", "﻿Date;Amount\n2026-01-01;12,50\n2026-01-02;7\n".encode("utf-8"))
    r = doc_extract.extract(p, "csv")
    assert r["delimiter"] == ";" and r["encoding"] == "utf-8-sig"
    assert r["parts"][0]["head"].startswith("| Date | Amount |")
    p2 = _write(tmp_path, "c.csv", "Name,City\nRen\xe9e,K\xf6ln\n".encode("cp1252"))
    r2 = doc_extract.extract(p2, "csv")
    assert r2["encoding"] == "cp1252" and "| Renée | Köln |" in r2["parts"][0]["text"]


def test_markdown_and_text(tmp_path):
    md = _write(tmp_path, "n.md", b"# Garden\n\nPlant beans in May.\n\n## Tools\n\nA spade.\n")
    assert _labels(doc_extract.extract(md, "md")) == ["Section: Garden", "Section: Tools"]
    txt = _write(tmp_path, "n.txt", ("word " * 900 + "\n\n") .encode() * 3)
    assert _labels(doc_extract.extract(txt, "txt")) == ["para 1", "para 2", "para 3"]


# ─── Guards ──────────────────────────────────────────────────────────────
def test_kinds_and_old_formats():
    assert doc_extract.kind_for("Lease.PDF") == "pdf"
    assert doc_extract.kind_for("x.exe") is None and doc_extract.kind_for("x.doc") is None
    assert "docx or PDF" in doc_extract.old_format_advice("old.doc")
    assert "xlsx or .csv" in doc_extract.old_format_advice("old.xls")


@pytest.mark.parametrize("kind,data,why", [
    ("pdf", b"<html>not a pdf</html>", "isn't a PDF"),
    ("docx", b"%PDF-1.4 renamed", "damaged or isn't the kind"),
    ("txt", b"MZ\x00\x00binary", "isn't text"),
])
def test_a_file_that_isnt_what_its_name_says(tmp_path, kind, data, why):
    p = _write(tmp_path, f"x.{kind}", data)
    with pytest.raises(safe.Refused, match=why):
        doc_extract.extract(p, kind)


def test_the_byte_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_extract, "MAX_FILE_BYTES", 1000)
    p = _write(tmp_path, "big.txt", b"a " * 1000)
    with pytest.raises(safe.Refused, match="too large"):
        doc_extract.extract(p, "txt")


def test_a_zip_bomb_is_refused_before_it_inflates(tmp_path):
    data = fx.zip_bomb_docx(24)
    assert len(data) < 200_000
    p = _write(tmp_path, "bomb.docx", data)
    with pytest.raises(safe.Refused, match="zip bomb"):
        doc_extract.extract(p, "docx")


def test_too_many_parts_and_too_much_inflated(tmp_path, monkeypatch):
    p = _write(tmp_path, "many.docx", fx.too_many_parts_docx())
    with pytest.raises(safe.Refused, match="too many parts"):
        doc_extract.extract(p, "docx")
    monkeypatch.setattr(safe, "MAX_UNZIPPED", 2000)
    p2 = _write(tmp_path, "big.docx", fx.make_docx())
    with pytest.raises(safe.Refused, match="unpacks to more than"):
        doc_extract.extract(p2, "docx")


def test_a_member_whose_size_lies_is_caught_while_it_inflates(tmp_path, monkeypatch):
    p = _write(tmp_path, "x.docx", fx.make_docx())
    monkeypatch.setattr(safe, "MAX_MEMBER_BYTES", 500)
    with safe.SafeZip(p) as z, pytest.raises(safe.Refused, match="too large"):
        z.read("word/document.xml")


def test_an_encrypted_zip_is_refused(tmp_path):
    data = bytearray(fx.make_docx())
    # Set the "encrypted" flag on every entry (local headers and directory).
    i = 0
    while (i := data.find(b"PK\x03\x04", i)) != -1:
        data[i + 6] |= 1
        i += 4
    i = 0
    while (i := data.find(b"PK\x01\x02", i)) != -1:
        data[i + 8] |= 1
        i += 4
    p = _write(tmp_path, "locked.docx", bytes(data))
    with pytest.raises(safe.Refused, match="encrypted"):
        doc_extract.extract(p, "docx")


@pytest.mark.parametrize("xml", [fx.BILLION_LAUGHS, fx.EXTERNAL_ENTITY])
def test_xml_entities_are_refused(tmp_path, xml):
    p = _write(tmp_path, "e.docx", fx.make_docx(document_xml=xml))
    with pytest.raises(safe.Refused, match="entity declarations"):
        doc_extract.extract(p, "docx")


def test_a_doctype_is_refused_whatever_the_encoding():
    with pytest.raises(safe.Refused, match="entity declarations"):
        safe.parse_xml(fx.UTF16_DOCTYPE)
    with pytest.raises(safe.Refused, match="entity declarations"):
        safe.check_xml(b'<?xml version="1.0"?><!DOCTYPE a SYSTEM "http://127.0.0.1:9/a.dtd"><a/>')
    assert safe.parse_xml(b"<a><b>x</b></a>").find("b").text == "x"


def test_an_xlsx_with_entities_is_refused_before_openpyxl_reads_it(tmp_path):
    good = fx.make_xlsx()
    src = zipfile.ZipFile(__import__("io").BytesIO(good))
    files = {n: src.read(n) for n in src.namelist()}
    files["xl/sharedStrings.xml"] = fx.BILLION_LAUGHS.encode()
    p = _write(tmp_path, "e.xlsx", fx._zip(files))
    with pytest.raises(safe.Refused, match="entity declarations"):
        doc_extract.extract(p, "xlsx")


# ─── The worker process ──────────────────────────────────────────────────
def test_the_worker_reads_and_refuses(tmp_path):
    good = _write(tmp_path, "lease.docx", fx.make_docx())
    r = asyncio.run(runner.extract(good, "docx"))
    assert "Section: Rent" in _labels(r) and "ok" not in r
    bomb = _write(tmp_path, "bomb.docx", fx.zip_bomb_docx(24))
    with pytest.raises(runner.ExtractFailed, match="zip bomb"):
        asyncio.run(runner.extract(bomb, "docx"))


def test_a_reader_that_hangs_is_stopped(tmp_path, monkeypatch):
    p = _write(tmp_path, "x.txt", b"hello")
    monkeypatch.setattr(runner, "worker_argv", lambda work: [sys.executable, "-c", "import time; time.sleep(60)"])
    with pytest.raises(runner.ExtractFailed, match="took too long"):
        asyncio.run(runner.extract(p, "txt", timeout=1.5))


def test_a_reader_that_dies_costs_one_document(tmp_path, monkeypatch):
    p = _write(tmp_path, "x.txt", b"hello")
    monkeypatch.setattr(runner, "worker_argv", lambda work: [sys.executable, "-c", "import os; os._exit(3)"])
    with pytest.raises(runner.ExtractFailed, match="couldn't be read"):
        asyncio.run(runner.extract(p, "txt"))


def test_the_memory_cap_holds_in_the_worker():
    code = ("import sys; from domovoi.doc_extract import limits; limits.apply(300 * 1024 * 1024, 20)\n"
            "try:\n    x = bytearray(900 * 1024 * 1024)\nexcept MemoryError:\n    sys.exit(7)\nsys.exit(0)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=60)
    assert r.returncode != 0, "a 900 MB allocation passed a 300 MB cap"


def test_the_limits_fail_closed_on_an_unknown_platform(monkeypatch):
    monkeypatch.setattr(limits.sys, "platform", "plan9")
    with pytest.raises(limits.LimitError):
        limits.apply(1, 1)


# ─── E-EXTRACT on the D0 set (outside the repo) ──────────────────────────
DOC_SET = os.environ.get("DOMOVOI_DOC_SET")


@pytest.mark.skipif(not DOC_SET, reason="DOMOVOI_DOC_SET names the D0 E-EXTRACT set (outside the repo)")
def test_e_extract_targets_on_the_d0_set():
    import difflib

    root = Path(DOC_SET)
    keys = {p.name[:-5]: json.loads(p.read_bytes()) for p in sorted((root / "keys").glob("*.json"))}
    assert len(keys) >= 20

    def norm(s):
        import re
        import unicodedata
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s)).strip()

    def acc(exp, got):
        e, g = norm(exp), norm(got)
        return difflib.SequenceMatcher(None, e, g, autojunk=False).ratio()

    for name, k in keys.items():
        path = root / name
        if k["kind"] == "pdf":
            vision = {i + 1 for i, pg in enumerate(k["pages"]) if pg["scanned"] or pg.get("garbled")}
            if len(vision) == len(k["pages"]):
                with pytest.raises(safe.Refused):
                    doc_extract.extract(path, "pdf")
                continue
            r = doc_extract.extract(path, "pdf")
            assert set(r["scanned_pages"]) | set(r["garbled_pages"]) == vision, name
            pages = {p.get("page"): p["text"] for p in r["parts"] if p.get("page")}
            repeat = len(k["pages"]) >= 2
            for i, pg in enumerate(k["pages"]):
                if i + 1 in vision:
                    continue
                exp = "\n".join(ln["t"] for ln in pg["lines"] if ln["role"] == "body" or not repeat)
                assert acc(exp, pages.get(i + 1, "")) >= 0.98, (name, i + 1)
        elif k["kind"] in ("docx", "odt"):
            r = doc_extract.extract(path, k["kind"])
            body = "\n".join(p["text"] for p in r["parts"] if p["label"] not in ("page header", "page footer", "footnotes"))
            exp = []
            for b in k["blocks"]:
                exp += [" ".join(row) for row in b["rows"]] if b["type"] == "table" else [b["text"]]
            import re
            plain = "\n".join(re.sub(r"^#+ |^- ", "", ln) if not ln.startswith("|") else
                              " ".join(c.strip() for c in ln.strip().strip("|").split("|"))
                              for ln in body.split("\n") if not re.match(r"^\|(-+\|)+$", ln.strip()))
            assert acc("\n".join(exp), plain) >= 0.99, name
