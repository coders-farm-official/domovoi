"""The Univer sheet editor's workbook model (web/backend/api/sheet_workbook.py)
and its routes (GET/PUT /api/documents/workbook, POST /api/documents/presence).

Univer's own .xlsx import/export is in its paid tier, so the server
converts with openpyxl. These pin the fidelity of that round trip on sample
workbooks made here: what the editor shows comes back exactly when nothing
was edited, an edit changes only what was edited, and what the editor
doesn't model (comments, data validation, conditional formatting) is kept.
"""

from __future__ import annotations

import codecs
import copy
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

from domovoi.config import settings
from domovoi.tests.auth_testkit import install_fake_db
from web.backend.api import documents as docs
from web.backend.api import sheet_workbook as sw
from web.backend.main import app


@pytest.fixture(autouse=True)
def _pre_setup_install(monkeypatch):
    # Document behaviour, not auth (as test_documents_sheet_preserve.py).
    install_fake_db(monkeypatch, admin=False)


@pytest.fixture
def docs_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    docs._presence.clear()
    return tmp_path


def _client():
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


# ─── sample workbooks ───────────────────────────────────────────────
def _household(path):
    """Values, formulas, dates, number formats, fonts, fills, borders,
    alignment, merges, sizes, freeze, three sheets (one hidden), and the
    things the editor doesn't model: a comment, data validation and
    conditional formatting."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws["A1"] = "Item"
    ws["B1"] = "Cost"
    ws["C1"] = "Paid on"
    for ref in ("A1", "B1", "C1"):
        ws[ref].font = Font(bold=True, color="FFFFFFFF", name="Arial", size=12)
        ws[ref].fill = PatternFill("solid", fgColor="FF336699")
        ws[ref].alignment = Alignment(horizontal="center", vertical="center")
    ws["A2"], ws["B2"], ws["C2"] = "Rent", 1200, dt.datetime(2026, 10, 1)
    ws["A3"], ws["B3"], ws["C3"] = "Food", 412.5, dt.date(2026, 10, 3)
    ws["B2"].number_format = ws["B3"].number_format = '"$"#,##0.00'
    ws["C2"].number_format = ws["C3"].number_format = "yyyy-mm-dd"
    ws["A4"] = "Total"
    ws["B4"] = "=SUM(B2:B3)"
    ws["A4"].font = Font(italic=True, underline="single", strike=True)
    thin = Side(style="thin", color="FF000000")
    ws["B4"].border = Border(top=thin, bottom=Side(style="double", color="FFFF0000"))
    ws["A6"] = "A long note that wraps inside its merged cells"
    ws["A6"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("A6:C7")
    ws["D1"] = True
    ws["D2"] = "007"
    ws["E1"] = "rotated"
    ws["E1"].alignment = Alignment(text_rotation=45)
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["C"].width = 14
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"
    ws["A2"].comment = Comment("monthly", "Kamron")
    dv = DataValidation(type="list", formula1='"yes,no"')
    ws.add_data_validation(dv)
    dv.add("D5")
    ws.conditional_formatting.add(
        "B2:B3", CellIsRule(operator="greaterThan", formula=["1000"], fill=PatternFill("solid", fgColor="FFFFC7CE"))
    )
    notes = wb.create_sheet("Notes")
    notes["A1"] = "keep me"
    notes.sheet_properties.tabColor = "FF00AA00"
    secret = wb.create_sheet("Hidden")
    secret["A1"] = 42
    secret.sheet_state = "hidden"
    wb.save(path)


def _cell_facts(c):
    f, fl, al, bd = c.font, c.fill, c.alignment, c.border
    return {
        "value": c.value,
        "fmt": c.number_format,
        "font": (f.name, f.sz, f.b, f.i, f.u, f.strike, sw._openpyxl_hex(f.color)),
        "fill": (fl.fill_type, sw._openpyxl_hex(fl.fgColor) if fl.fill_type else None),
        "align": (al.horizontal, al.vertical, al.wrap_text, al.text_rotation),
        "border": tuple((s.style, sw._openpyxl_hex(s.color) if s.style else None) if s else None
                        for s in (bd.top, bd.bottom, bd.left, bd.right)),
    }


def _facts(path):
    wb = load_workbook(path)
    out = {"sheets": [(ws.title, ws.sheet_state) for ws in wb.worksheets]}
    for ws in wb.worksheets:
        cells = {}
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None or c.has_style:
                    cells[c.coordinate] = _cell_facts(c)
        out[ws.title] = {
            "cells": cells,
            "merges": sorted(str(r) for r in ws.merged_cells.ranges),
            "widths": {k: d.width for k, d in ws.column_dimensions.items() if d.width},
            "heights": {k: d.height for k, d in ws.row_dimensions.items() if d.height},
            "freeze": ws.freeze_panes,
            "tab": sw._openpyxl_hex(ws.sheet_properties.tabColor) if ws.sheet_properties.tabColor else None,
            "comments": {c.coordinate: c.comment.text for row in ws.iter_rows() for c in row if c.comment},
            "validations": [(d.type, d.formula1, str(d.sqref)) for d in ws.data_validations.dataValidation],
            "cf": sorted(str(rng.sqref) for rng in ws.conditional_formatting),
        }
    return out


def _snapshot(path):
    """What the editor gets, then sends back untouched (JSON round trip,
    as the browser does)."""
    model = sw.read_xlsx(path)
    wb = json.loads(json.dumps(model["workbook"]))
    for sid, sheet in wb["sheets"].items():
        sheet["origin"] = sid
    return wb


def _save(path, snapshot):
    wb = load_workbook(path)
    sw.write_xlsx(wb, snapshot)
    wb.save(path)


# ─── fidelity: read → unchanged save is a no-op ─────────────────────
def test_reading_shows_what_the_file_holds(tmp_path):
    _household(tmp_path / "h.xlsx")
    model = sw.read_xlsx(tmp_path / "h.xlsx")
    wb = json.loads(json.dumps(model["workbook"]))  # as the browser gets it
    assert [wb["sheets"][s]["name"] for s in wb["sheetOrder"]] == ["Budget", "Notes", "Hidden"]
    budget, notes, hidden = (wb["sheets"][s] for s in wb["sheetOrder"])
    cells = budget["cellData"]
    style = lambda cell: wb["styles"][cell["s"]]  # noqa: E731
    assert cells["0"]["0"]["v"] == "Item" and style(cells["0"]["0"])["bl"] == 1
    assert style(cells["0"]["0"])["bg"] == {"rgb": "#336699"}
    assert style(cells["0"]["0"])["cl"] == {"rgb": "#FFFFFF"}
    assert style(cells["0"]["0"])["ff"] == "Arial" and style(cells["0"]["0"])["fs"] == 12
    assert style(cells["0"]["0"])["ht"] == 2 and style(cells["0"]["0"])["vt"] == 2
    assert cells["1"]["1"] == {"v": 1200, "t": 2, "s": cells["1"]["1"]["s"]}
    assert style(cells["1"]["1"])["n"] == {"pattern": '"$"#,##0.00'}
    # Dates are serial numbers with their date format (Univer shows them as dates).
    assert cells["1"]["2"]["v"] == 46296 and style(cells["1"]["2"])["n"] == {"pattern": "yyyy-mm-dd"}
    assert cells["3"]["1"]["f"] == "=SUM(B2:B3)"
    assert style(cells["3"]["0"])["it"] == 1 and style(cells["3"]["0"])["ul"] == {"s": 1}
    assert style(cells["3"]["0"])["st"] == {"s": 1}
    assert style(cells["3"]["1"])["bd"]["b"] == {"s": 7, "cl": {"rgb": "#FF0000"}}
    assert cells["0"]["3"] == {"v": 1, "t": 3}
    assert cells["1"]["3"] == {"v": "007", "t": 1}
    assert style(cells["0"]["4"])["tr"] == {"a": -45}   # Excel 45 = up = Univer -45
    assert budget["mergeData"] == [{"startRow": 5, "endRow": 6, "startColumn": 0, "endColumn": 2}]
    assert budget["columnData"]["0"]["w"] == sw.width_to_px(24)
    assert budget["rowData"]["0"]["h"] == 40
    assert budget["freeze"] == {"xSplit": 0, "ySplit": 1, "startRow": 1, "startColumn": 0}
    assert notes["tabColor"] == "#00AA00"
    assert hidden["hidden"] == 1 and hidden["cellData"]["0"]["0"] == {"v": 42, "t": 2}
    assert model["truncated"] == []


def test_an_unchanged_save_changes_nothing(tmp_path):
    path = tmp_path / "h.xlsx"
    _household(path)
    before = _facts(path)
    _save(path, _snapshot(path))
    assert _facts(path) == before


# ─── edits change only what was edited ──────────────────────────────
def test_an_edit_changes_that_cell_and_keeps_everything_else(tmp_path):
    path = tmp_path / "h.xlsx"
    _household(path)
    before = _facts(path)
    snap = _snapshot(path)
    budget = snap["sheets"]["s0"]
    budget["cellData"]["2"]["1"]["v"] = 450           # Food costs more
    budget["cellData"].setdefault("8", {})["0"] = {"v": "new row", "t": 1}
    _save(path, snap)
    after = _facts(path)
    assert after["Budget"]["cells"]["B3"]["value"] == 450
    assert after["Budget"]["cells"]["B3"]["fmt"] == '"$"#,##0.00'   # format kept
    assert after["Budget"]["cells"]["A9"]["value"] == "new row"
    del after["Budget"]["cells"]["A9"]
    after["Budget"]["cells"]["B3"]["value"] = 412.5
    assert after == before


def test_formatting_edits_are_written(tmp_path):
    path = tmp_path / "h.xlsx"
    _household(path)
    snap = _snapshot(path)
    sheet = snap["sheets"]["s0"]
    snap["styles"]["new1"] = {
        "bl": 1, "ff": "Inter", "fs": 14, "cl": {"rgb": "rgb(255, 0, 0)"},
        "bg": {"rgb": "#ffff00"}, "ht": 3, "tb": 3,
        "bd": {"l": {"s": 8, "cl": {"rgb": "#0000FF"}}},
        "n": {"pattern": "0.0%"},
    }
    sheet["cellData"]["2"]["0"]["s"] = "new1"        # A3 "Food"
    sheet["cellData"]["1"]["0"]["s"] = None          # A2 "Rent" loses its style (it had none)
    _save(path, snap)
    c = load_workbook(path)["Budget"]["A3"]
    assert c.value == "Food"
    assert (c.font.b, c.font.name, c.font.sz) == (True, "Inter", 14)
    assert sw._openpyxl_hex(c.font.color) == "#FF0000"
    assert c.fill.fill_type == "solid" and sw._openpyxl_hex(c.fill.fgColor) == "#FFFF00"
    assert c.alignment.horizontal == "right" and c.alignment.wrap_text
    assert c.border.left.style == "medium" and sw._openpyxl_hex(c.border.left.color) == "#0000FF"
    assert c.number_format == "0.0%"


def test_clearing_a_cell_and_unbolding_a_header(tmp_path):
    path = tmp_path / "h.xlsx"
    _household(path)
    snap = _snapshot(path)
    sheet = snap["sheets"]["s0"]
    del sheet["cellData"]["2"]["0"]                     # A3 cleared
    hdr = dict(snap["styles"][sheet["cellData"]["0"]["1"]["s"]])
    hdr.pop("bl")
    snap["styles"]["plain"] = hdr
    sheet["cellData"]["0"]["1"]["s"] = "plain"         # B1 no longer bold
    _save(path, snap)
    ws = load_workbook(path)["Budget"]
    assert ws["A3"].value is None
    assert ws["B1"].font.b is False and ws["B1"].value == "Cost"
    assert sw._openpyxl_hex(ws["B1"].fill.fgColor) == "#336699"   # fill kept


def test_merges_sizes_and_freeze(tmp_path):
    path = tmp_path / "h.xlsx"
    _household(path)
    snap = _snapshot(path)
    sheet = snap["sheets"]["s0"]
    sheet["mergeData"] = [{"startRow": 9, "endRow": 9, "startColumn": 0, "endColumn": 1}]
    sheet["columnData"] = {0: {"w": 200}, 3: {"w": 40, "hd": 1}}   # C loses its width
    sheet["rowData"] = {0: {"h": 40}, 4: {"h": 60}}
    sheet["freeze"] = {"xSplit": 1, "ySplit": 2, "startRow": 2, "startColumn": 1}
    _save(path, snap)
    ws = load_workbook(path)["Budget"]
    assert sorted(str(r) for r in ws.merged_cells.ranges) == ["A10:B10"]
    assert ws["A6"].value.startswith("A long note")            # unmerged, value kept
    assert ws.column_dimensions["A"].width == sw.px_to_width(200)
    assert ws.column_dimensions["D"].hidden and ws.column_dimensions["D"].width == sw.px_to_width(40)
    assert "C" not in ws.column_dimensions or not ws.column_dimensions["C"].width
    assert ws.row_dimensions[5].height == 45
    assert ws.freeze_panes == "B3"


def test_sheets_added_renamed_reordered_hidden_and_deleted(tmp_path):
    path = tmp_path / "h.xlsx"
    _household(path)
    snap = _snapshot(path)
    snap["sheets"]["s1"]["name"] = "Budget"          # swap names with s0
    snap["sheets"]["s0"]["name"] = "Notes"
    snap["sheets"]["s2"]["hidden"] = 0
    snap["sheets"]["newX"] = {"id": "newX", "name": "Plans", "origin": None,
                              "cellData": {0: {0: {"v": "trip", "t": 1}}}}
    snap["sheetOrder"] = ["newX", "s2", "s0"]       # Notes (s1) deleted
    _save(path, snap)
    wb = load_workbook(path)
    assert [(ws.title, ws.sheet_state) for ws in wb.worksheets] == [
        ("Plans", "visible"), ("Hidden", "visible"), ("Notes", "visible"),
    ]
    assert wb["Plans"]["A1"].value == "trip"
    assert wb["Notes"]["A1"].value == "Item"             # the old Budget, renamed
    assert wb["Notes"]["A2"].comment.text == "monthly"
    # The next read gives the new order's ids, which the next save uses.
    again = sw.read_xlsx(path)["workbook"]
    assert [again["sheets"][s]["name"] for s in again["sheetOrder"]] == ["Plans", "Hidden", "Notes"]


def test_a_date_cell_edited_stays_a_date(tmp_path):
    path = tmp_path / "h.xlsx"
    _household(path)
    snap = _snapshot(path)
    snap["sheets"]["s0"]["cellData"]["1"]["2"]["v"] = 46300   # 2026-10-05
    _save(path, snap)
    c = load_workbook(path)["Budget"]["C2"]
    assert c.value == dt.datetime(2026, 10, 5) and c.number_format == "yyyy-mm-dd"


def test_a_formula_typed_and_text_forced(tmp_path):
    path = tmp_path / "h.xlsx"
    _household(path)
    snap = _snapshot(path)
    cd = snap["sheets"]["s0"]["cellData"]
    cd["3"]["2"] = {"f": "=B4*2", "v": 3225}          # Univer sends the result too
    cd.setdefault("10", {})["0"] = {"v": "=not a formula", "t": 4}
    cd["10"]["1"] = {"v": "TRUE", "t": 3}
    _save(path, snap)
    ws = load_workbook(path)["Budget"]
    assert ws["C4"].value == "=B4*2"
    assert ws["A11"].value == "=not a formula" and ws["A11"].data_type == "s"
    assert ws["B11"].value is True


def test_text_turned_up_or_down_keeps_its_direction(tmp_path):
    # Univer turns text up with a negative angle, Excel with 1-90.
    path = tmp_path / "h.xlsx"
    _household(path)
    snap = _snapshot(path)
    snap["styles"]["up30"] = {"tr": {"a": -30}}
    snap["styles"]["down20"] = {"tr": {"a": 20}}
    snap["styles"]["stacked"] = {"tr": {"a": 0, "v": 1}}
    cd = snap["sheets"]["s0"]["cellData"]
    cd.setdefault("11", {})["0"] = {"v": "up", "t": 1, "s": "up30"}
    cd["11"]["1"] = {"v": "down", "t": 1, "s": "down20"}
    cd["11"]["2"] = {"v": "stacked", "t": 1, "s": "stacked"}
    _save(path, snap)
    ws = load_workbook(path)["Budget"]
    assert ws["A12"].alignment.text_rotation == 30
    assert ws["B12"].alignment.text_rotation == 110
    assert ws["C12"].alignment.text_rotation == 255
    again = json.loads(json.dumps(sw.read_xlsx(path)["workbook"]))
    styles, row = again["styles"], again["sheets"]["s0"]["cellData"]["11"]
    assert [styles[row[c]["s"]]["tr"] for c in ("0", "1", "2")] == [{"a": -30}, {"a": 20}, {"a": 0, "v": 1}]


def test_style_spellings_univer_may_use_compare_equal():
    a = sw.norm_style({"bl": 1, "cl": {"rgb": "rgb(0,0,0)"}, "bg": {"rgb": "#abc"}, "ht": 0, "ul": {"s": 0}})
    assert a == {"bl": 1, "bg": {"rgb": "#AABBCC"}}
    assert sw.norm_style({"tr": {"a": 45, "v": 0}}) == {"tr": {"a": 45}}
    assert sw.norm_style(None) == {}


def test_a_cell_past_the_window_is_refused_not_dropped(tmp_path):
    path = tmp_path / "h.xlsx"
    _household(path)
    snap = _snapshot(path)
    snap["sheets"]["s0"]["cellData"][sw.WINDOW_ROWS] = {0: {"v": "too far", "t": 1}}
    with pytest.raises(sw.WorkbookRefused) as err:
        _save(path, snap)
    assert err.value.status_code == 413


def test_a_sheet_larger_than_the_window_keeps_what_is_past_it(tmp_path):
    path = tmp_path / "big.xlsx"
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "top"
    ws.cell(row=sw.WINDOW_ROWS + 5, column=1, value="past the window")
    wb.save(path)
    model = sw.read_xlsx(path)
    assert model["truncated"] == ["Sheet"]
    snap = json.loads(json.dumps(model["workbook"]))
    snap["sheets"]["s0"]["origin"] = "s0"
    snap["sheets"]["s0"]["cellData"]["0"]["0"]["v"] = "changed"
    _save(path, snap)
    ws = load_workbook(path).active
    assert ws["A1"].value == "changed"
    assert ws.cell(row=sw.WINDOW_ROWS + 5, column=1).value == "past the window"


def test_grouped_column_widths_are_split_before_one_changes(tmp_path):
    path = tmp_path / "g.xlsx"
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "x"
    ws.column_dimensions.group("B", "D", hidden=False)
    ws.column_dimensions["B"].width = 20
    wb.save(path)
    snap = _snapshot(path)
    cols = snap["sheets"]["s0"]["columnData"]
    assert {int(k) for k in cols} == {1, 2, 3}
    cols["2"] = {"w": 300}
    _save(path, snap)
    dims = load_workbook(path).active.column_dimensions
    spans = sorted((d.min, d.max) for d in dims.values() if d.width)
    assert spans == [(2, 2), (3, 3), (4, 4)]               # no overlapping <col>
    assert dims["C"].width == sw.px_to_width(300)
    assert dims["B"].width == dims["D"].width == 20


# ─── .csv ───────────────────────────────────────────────────────────
def test_csv_numbers_only_when_they_read_back_the_same(tmp_path):
    path = tmp_path / "c.csv"
    path.write_bytes(codecs.BOM_UTF8 + b"name,qty,code,price\nbolt,1200,007,1.50\nnut,3,=B2*2,2.5\n")
    wb = json.loads(json.dumps(sw.read_csv(path, 100, 10)["workbook"]))
    cd = wb["sheets"]["s0"]["cellData"]
    assert cd["1"]["1"] == {"v": 1200, "t": 2}
    assert cd["1"]["2"] == {"v": "007", "t": 1}
    assert cd["1"]["3"] == {"v": "1.50", "t": 1}
    assert cd["2"]["3"] == {"v": 2.5, "t": 2}
    assert cd["2"]["2"] == {"f": "=B2*2"}
    rows = sw.csv_rows(wb)
    assert rows == [["name", "qty", "code", "price"], ["bolt", "1200", "007", "1.50"],
                    ["nut", "3", "=B2*2", "2.5"]]


# ─── the routes ─────────────────────────────────────────────────────
def test_get_and_put_round_trip_with_the_version_check(docs_dir):
    _household(docs_dir / "h.xlsx")
    before = _facts(docs_dir / "h.xlsx")
    with _client() as c:
        r = c.get("/api/documents/workbook/h.xlsx")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["editable"] is True and body["format"] == "xlsx" and body["version"]
        snap = body["workbook"]
        for sid, sheet in snap["sheets"].items():
            sheet["origin"] = sid
        r2 = c.put("/api/documents/workbook/h.xlsx", json={"workbook": snap, "base_version": body["version"]})
        assert r2.status_code == 200, r2.text
        assert r2.json()["version"] != body["version"]
        assert _facts(docs_dir / "h.xlsx") == before
        # A second save from the stale version is someone else's overwrite.
        stale = c.put("/api/documents/workbook/h.xlsx", json={"workbook": snap, "base_version": body["version"]})
        assert stale.status_code == 409
        assert stale.json()["detail"]["reason"] == "changed"
        forced = c.put("/api/documents/workbook/h.xlsx",
                       json={"workbook": snap, "base_version": body["version"], "force": True})
        assert forced.status_code == 200


def test_a_file_with_a_chart_opens_read_only_and_is_not_saved(docs_dir):
    path = docs_dir / "chart.xlsx"
    wb = Workbook()
    ws = wb.active
    for i in range(1, 4):
        ws.cell(row=i, column=1, value=i)
    chart = BarChart()
    chart.add_data(Reference(ws, min_col=1, min_row=1, max_row=3))
    ws.add_chart(chart, "C1")
    wb.save(path)
    raw = path.read_bytes()
    with _client() as c:
        body = c.get("/api/documents/workbook/chart.xlsx").json()
        assert body["editable"] is False and "charts" in body["why"]
        snap = body["workbook"]
        r = c.put("/api/documents/workbook/chart.xlsx", json={"workbook": snap, "base_version": body["version"]})
    assert r.status_code == 409
    assert path.read_bytes() == raw


def test_csv_route_keeps_its_bom(docs_dir):
    path = docs_dir / "list.csv"
    path.write_bytes(codecs.BOM_UTF8 + b"a,b\n1,2\n")
    with _client() as c:
        body = c.get("/api/documents/workbook/list.csv").json()
        assert body["format"] == "csv"
        snap = body["workbook"]
        snap["sheets"]["s0"]["cellData"]["1"]["1"] = {"v": 5, "t": 2}
        r = c.put("/api/documents/workbook/list.csv", json={"workbook": snap, "base_version": body["version"]})
        assert r.status_code == 200, r.text
    assert path.read_bytes() == codecs.BOM_UTF8 + b"a,b\r\n1,5\r\n"


def test_an_empty_new_xlsx_opens_blank_and_saves(docs_dir):
    (docs_dir / "new.xlsx").write_bytes(b"")
    with _client() as c:
        body = c.get("/api/documents/workbook/new.xlsx").json()
        snap = body["workbook"]
        snap["sheets"]["s0"]["cellData"] = {"0": {"0": {"v": "hello", "t": 1}}}
        r = c.put("/api/documents/workbook/new.xlsx", json={"workbook": snap, "base_version": body["version"]})
        assert r.status_code == 200, r.text
    ws = load_workbook(docs_dir / "new.xlsx").active
    assert ws["A1"].value == "hello" and ws.title == "Sheet1"


def test_other_types_are_refused(docs_dir):
    (docs_dir / "notes.md").write_text("# hi", encoding="utf-8")
    (docs_dir / "old.ods").write_bytes(b"PK")
    with _client() as c:
        assert c.get("/api/documents/workbook/old.ods").status_code == 415
        r = c.put("/api/documents/workbook/notes.md",
                  json={"workbook": {"sheetOrder": [], "sheets": {}}, "base_version": None})
        assert r.status_code == 415
    assert (docs_dir / "notes.md").read_text(encoding="utf-8") == "# hi"


def _ping(c, path, session, device=None, **extra):
    headers = {"X-Device-Id": device} if device else {}
    return c.post(f"/api/documents/presence/{path}", json={"session_id": session, **extra}, headers=headers)


def test_presence_names_the_other_editors(docs_dir):
    _household(docs_dir / "h.xlsx")
    with _client() as c:
        a = _ping(c, "h.xlsx", "session-aaaa", "dev-aaaa")
        assert a.status_code == 200, a.text
        assert a.json()["others"] == []
        b = _ping(c, "h.xlsx", "session-bbbb", "dev-bbbb")
        assert b.json()["others"] == ["another device"]   # no devices row in the fake DB
        _ping(c, "h.xlsx", "session-bbbb", "dev-bbbb", leaving=True)
        assert _ping(c, "h.xlsx", "session-aaaa", "dev-aaaa").json()["others"] == []


def test_presence_is_only_for_sheets_that_exist(docs_dir):
    """Review round 1: any path grew the table, for files that weren't there."""
    (docs_dir / "notes.md").write_text("# hi", encoding="utf-8")
    with _client() as c:
        for i in range(30):
            assert _ping(c, f"nothing/{i}.xlsx", f"session-{i:04d}").status_code == 404
        assert _ping(c, "notes.md", "session-mdmd").status_code == 404
    assert docs._presence == {}


def test_presence_is_bounded(docs_dir, monkeypatch):
    monkeypatch.setattr(docs, "_PRESENCE_MAX_PATHS", 2)
    monkeypatch.setattr(docs, "_PRESENCE_MAX_SESSIONS", 2)
    for n in ("a", "b", "c"):
        _household(docs_dir / f"{n}.xlsx")
    with _client() as c:
        assert _ping(c, "a.xlsx", "session-a1").status_code == 200
        assert _ping(c, "b.xlsx", "session-b1").status_code == 200
        assert _ping(c, "c.xlsx", "session-c1").status_code == 429     # paths
        assert _ping(c, "a.xlsx", "session-a2").status_code == 200
        assert _ping(c, "a.xlsx", "session-a3").status_code == 429     # sessions
        assert _ping(c, "a.xlsx", "session-a1").status_code == 200     # a known one still pings
    assert len(docs._presence) == 2 and len(docs._presence["a.xlsx"]) == 2


def test_presence_device_comes_from_the_request_not_the_body(docs_dir, monkeypatch):
    """A body naming someone else's device is ignored; the name shown is
    looked up only for devices actually on this sheet."""
    _household(docs_dir / "h.xlsx")
    asked = []

    async def names(ids):
        asked.append(list(ids))
        return {"dev-kitchen": "Kitchen tablet"}

    monkeypatch.setattr(docs, "_device_names", names)
    with _client() as c:
        _ping(c, "h.xlsx", "session-kkkk", "dev-kitchen")
        _ping(c, "h.xlsx", "session-spoof", None, device_id="dev-someone-else")
        r = _ping(c, "h.xlsx", "session-me00", "dev-me0000")
    assert r.json()["others"] == ["Kitchen tablet", "another device"]
    assert all("dev-someone-else" not in ids for ids in asked)
    # The same device in another tab is not "someone else".
    with _client() as c:
        assert _ping(c, "h.xlsx", "session-kkk2", "dev-kitchen").json()["others"] == ["another device"]


# ─── the server's other readers still read what the editor saved ────
def test_chat_extract_reads_a_workbook_the_editor_saved(tmp_path):
    from domovoi.doc_extract import sheets as extract

    path = tmp_path / "h.xlsx"
    _household(path)
    snap = _snapshot(path)
    snap["sheets"]["s0"]["cellData"]["2"]["1"]["v"] = 450
    _save(path, snap)
    fn = getattr(extract, "extract_xlsx", None) or getattr(extract, "extract")
    text = json.dumps(fn(path), default=str) if not isinstance(fn(path), str) else fn(path)
    assert "Rent" in text and "450" in text and "keep me" in text


# ─── security review round 1 (2026-10-26): limits, shapes, lost parts ─
def _put(c, name, snap, base=None, **extra):
    return c.put(f"/api/documents/workbook/{name}",
                 json={"workbook": snap, "base_version": base, **extra})


def _one_sheet(cells=None, **extra):
    sheet = {"id": "a", "name": "a", "cellData": cells or {}}
    sheet.update(extra)
    return {"sheetOrder": ["a"], "sheets": {"a": sheet}, "styles": {}}


def test_overlapping_merges_are_refused_before_openpyxl(docs_dir, monkeypatch):
    """Six nested full-window merges in 531 bytes took 27 s of the sheets
    pool. Now: 400, at once, and openpyxl is never asked."""
    import time

    import openpyxl

    def never(*a, **k):
        raise AssertionError("openpyxl was asked")

    merges = [{"startRow": 0, "endRow": 4999 - i, "startColumn": 0, "endColumn": 99} for i in range(6)]
    monkeypatch.setattr(openpyxl, "Workbook", never)
    monkeypatch.setattr(openpyxl, "load_workbook", never)
    with _client() as c:
        t0 = time.perf_counter()
        r = _put(c, "m.xlsx", _one_sheet(mergeData=merges))
        took = time.perf_counter() - t0
    assert r.status_code in (400, 413), r.text
    assert took < 2
    assert not (docs_dir / "m.xlsx").exists()


@pytest.mark.parametrize("merges, status", [
    ([{"startRow": 0, "endRow": 1, "startColumn": 0, "endColumn": 1},
      {"startRow": 1, "endRow": 2, "startColumn": 1, "endColumn": 2}], 400),        # overlap
    ([{"startRow": r, "endRow": r, "startColumn": 0, "endColumn": 1}
      for r in range(sw.MAX_MERGES + 1)], 413),                                    # too many
    ([{"startRow": 0, "endRow": 2000, "startColumn": 0, "endColumn": 99}], 413),    # too much area
    ([{"startRow": 0, "endRow": sw.WINDOW_ROWS, "startColumn": 0, "endColumn": 1}], 400),  # past the window
    ([{"startRow": 3, "endRow": 1, "startColumn": 0, "endColumn": 1}], 400),        # backwards
])
def test_merge_limits(merges, status):
    with pytest.raises(sw.WorkbookRefused) as err:
        sw.validate_snapshot(_one_sheet(mergeData=merges))
    assert err.value.status_code == status


def test_too_many_cells_or_sheets_are_refused_before_openpyxl(docs_dir, monkeypatch):
    cells = {str(r): {str(c): {"v": 1} for c in range(100)} for r in range(sw.MAX_SAVE_CELLS // 100 + 1)}
    with pytest.raises(sw.WorkbookRefused) as err:
        sw.validate_snapshot(_one_sheet(cells))
    assert err.value.status_code == 413
    many = {"sheetOrder": [f"s{i}" for i in range(sw.MAX_SHEETS + 1)],
            "sheets": {f"s{i}": {"name": f"S{i}", "cellData": {}} for i in range(sw.MAX_SHEETS + 1)}}
    with _client() as c:
        r = _put(c, "many.xlsx", many)
    assert r.status_code == 413
    assert not (docs_dir / "many.xlsx").exists()


def test_the_body_limit_matches_the_cell_limit():
    from domovoi.transport_guard import body_limit_for

    assert body_limit_for("/api/documents/workbook/x.xlsx") == 32 * 1024 * 1024


@pytest.mark.parametrize("sheet_extra, cells", [
    ({"freeze": []}, None),
    ({"freeze": {"ySplit": "two"}}, None),
    ({"mergeData": 5}, None),
    ({"mergeData": [{"startRow": True, "endRow": 1, "startColumn": 0, "endColumn": 1}]}, None),
    ({"rowData": {"0": {"h": 1e308}}}, None),
    ({"columnData": {"0": {"w": float("inf")}}}, None),
    ({"columnData": []}, None),
    ({"name": "Bad\x01Name"}, None),
    ({}, {"0": {"0": {"v": "x\x02y", "t": 1}}}),
    ({}, {"0": {"0": {"f": "=A1\x07"}}}),
    ({}, {"0": {"0": {"v": 10 ** 400}}}),
    ({}, {"0": {"0": {"v": "x", "s": {"fs": float("inf")}}}}),
    ({}, {"0": {"0": {"v": "x", "s": {"tr": {"a": float("nan")}}}}}),
    ({}, {"0": "not a cell"}),
    ({}, {"0": {"0": {"v": "x" * (sw.MAX_TEXT + 1)}}}),
])
def test_malformed_snapshots_are_400_not_500(sheet_extra, cells):
    with pytest.raises(sw.WorkbookRefused) as err:
        sw.validate_snapshot(_one_sheet(cells, **sheet_extra))
    assert err.value.status_code in (400, 413)


def test_malformed_bodies_over_http_are_400(docs_dir):
    c = TestClient(app, headers={"X-Requested-With": "domovoi-tests", "Content-Type": "application/json"},
                   raise_server_exceptions=False)
    for body in (
        '{"workbook":{"sheetOrder":["a"],"sheets":{"a":{"name":"a","cellData":{},'
        '"columnData":{"0":{"w":Infinity}}}}}}',
        '{"workbook":{"sheetOrder":["a"],"sheets":{"a":{"name":"a","mergeData":5}}}}',
        '{"workbook":{"sheetOrder":["a"],"sheets":{"a":{"name":"a","cellData":{"0":{"0":{"v":"x\\u0002"}}}}}}}',
        '{"workbook": 5}',
        'not json',
        '{"workbook":{"sheetOrder":["a"],"sheets":{"a":{"name":"a"}}},"force":"yes"}',
    ):
        r = c.put("/api/documents/workbook/m.xlsx", content=body)
        assert r.status_code == 400, (body, r.status_code, r.text)
    assert not (docs_dir / "m.xlsx").exists()


def test_a_control_character_in_a_sheet_name_never_reaches_the_file(docs_dir):
    p = docs_dir / "t.xlsx"
    wb = Workbook()
    wb.active["A1"] = "keep"
    wb.save(p)
    raw = p.read_bytes()
    with _client() as c:
        g = c.get("/api/documents/workbook/t.xlsx").json()
        snap = g["workbook"]
        snap["sheets"]["s0"]["origin"] = "s0"
        snap["sheets"]["s0"]["name"] = "Bad\x01Name"
        r = _put(c, "t.xlsx", snap, g["version"])
    assert r.status_code == 400 and "control characters" in r.json()["detail"]
    assert p.read_bytes() == raw


def test_a_big_workbook_opens_read_only_within_a_cell_budget(docs_dir, monkeypatch):
    """GET was unbounded across sheets (3 x 5000 x 100: 11.5 s, 33 MB, and
    the JSON encoding on the event loop). Now one budget for the whole
    workbook; past it the sheet opens read-only, saying why."""
    monkeypatch.setattr(sw, "MAX_OPEN_CELLS", 50)
    wb = Workbook()
    ws = wb.active
    for s in range(3):
        if s:
            ws = wb.create_sheet(f"S{s}")
        for r in range(1, 11):
            for col in range(1, 4):
                ws.cell(row=r, column=col, value=r * col)
    wb.save(docs_dir / "big.xlsx")
    with _client() as c:
        r = c.get("/api/documents/workbook/big.xlsx")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["editable"] is False and "too big to edit here" in body["why"]
    total = sum(len(row) for sh in body["workbook"]["sheets"].values() for row in sh["cellData"].values())
    assert total <= 50
    assert "over_budget" not in body and "lost" not in body


def test_the_values_only_read_shares_one_budget_across_sheets(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "MAX_OPEN_CELLS", 40)
    wb = Workbook(write_only=True)
    for s in range(3):
        ws = wb.create_sheet(f"S{s}")
        for r in range(30):
            ws.append([r] * 5)
    wb.save(tmp_path / "w.xlsx")
    model = sw.read_xlsx_values_only(tmp_path / "w.xlsx")
    total = sum(len(row) for sh in model["workbook"]["sheets"].values() for row in sh["cellData"].values())
    assert total <= 40 and model["over_budget"] is True
    assert model["truncated"] == ["S0", "S1", "S2"]


def test_a_big_csv_opens_read_only(docs_dir, monkeypatch):
    monkeypatch.setattr(sw, "MAX_OPEN_CELLS", 20)
    (docs_dir / "big.csv").write_text("\n".join("a,b,c,d" for _ in range(50)), encoding="utf-8")
    with _client() as c:
        body = c.get("/api/documents/workbook/big.csv").json()
    assert body["editable"] is False and "too big" in body["why"]


def test_the_read_is_serialised_in_the_pool(docs_dir):
    _household(docs_dir / "h.xlsx")
    raw = docs._read_workbook_json(docs_dir / "h.xlsx", "h.xlsx")
    assert isinstance(raw, bytes)
    assert json.loads(raw)["workbook"]["sheetOrder"] == ["s0", "s1", "s2"]


_SPARKLINES = (
    '<extLst><ext uri="{05C60535-1F16-4fd2-B633-F4F36F0B64E0}" '
    'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main">'
    '<x14:sparklineGroups xmlns:xm="http://schemas.microsoft.com/office/excel/2006/main">'
    '<x14:sparklineGroup displayEmptyCellsAs="gap"><x14:colorSeries rgb="FF376092"/>'
    '<x14:sparklines><x14:sparkline><xm:f>Sheet!A1:C1</xm:f><xm:sqref>D1</xm:sqref></x14:sparkline>'
    '</x14:sparklines></x14:sparklineGroup></x14:sparklineGroups></ext></extLst>'
)


def _splice(path, member, transform=None, extra=None):
    import io
    import zipfile

    src = zipfile.ZipFile(path)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for i in src.infolist():
            data = src.read(i.filename)
            if transform and i.filename == member:
                data = transform(data)
            z.writestr(i, data)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    src.close()
    path.write_bytes(out.getvalue())


def test_sparklines_open_read_only_and_are_never_stripped(docs_dir):
    """openpyxl drops x14 extensions (sparklines, x14 validation, data bars)
    on a load-and-save and only WARNS. That warning now makes the file
    read-only, and a save is refused (proven: sparklines were stripped)."""
    p = docs_dir / "spark.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet"
    ws.append([1, 2, 3])
    wb.save(p)
    _splice(p, "xl/worksheets/sheet1.xml", lambda d: d.replace(b"</worksheet>", _SPARKLINES.encode() + b"</worksheet>"))
    raw = p.read_bytes()
    _, lost = sw.load_xlsx(p)
    assert lost and any("Sparkline" in m for m in lost)
    with _client() as c:
        g = c.get("/api/documents/workbook/spark.xlsx").json()
        assert g["editable"] is False and "sparklines" in g["why"]
        snap = g["workbook"]
        for sid in snap["sheetOrder"]:
            snap["sheets"][sid]["origin"] = sid
        snap["sheets"]["s0"]["cellData"]["5"] = {"0": {"v": "edit", "t": 1}}
        r = _put(c, "spark.xlsx", snap, g["version"])
        assert r.status_code == 409, r.text
        # The phone app's grid editor route is held to the same rule.
        assert c.put("/api/documents/sheet/spark.xlsx", json={"rows": [[{"v": "x"}]]}).status_code == 409
    assert p.read_bytes() == raw


@pytest.mark.parametrize("part", [
    "xl/threadedComments/threadedComment1.xml", "xl/slicers/slicer1.xml",
    "xl/slicerCaches/slicerCache1.xml", "xl/timelines/timeline1.xml", "xl/ctrlProps/ctrlProp1.xml",
    "xl/activeX/activeX1.xml", "xl/embeddings/oleObject1.bin", "xl/model/item.data",
])
def test_parts_openpyxl_drops_make_the_file_read_only(docs_dir, part):
    p = docs_dir / "parts.xlsx"
    wb = Workbook()
    wb.active["A1"] = "x"
    wb.save(p)
    _splice(p, "", extra={part: b"<x/>"})
    raw = p.read_bytes()
    with _client() as c:
        g = c.get("/api/documents/workbook/parts.xlsx").json()
        assert g["editable"] is False
        snap = g["workbook"]
        snap["sheets"]["s0"]["origin"] = "s0"
        assert _put(c, "parts.xlsx", snap, g["version"]).status_code == 409
    assert p.read_bytes() == raw


def _named_workbook(path):
    from openpyxl.workbook.defined_name import DefinedName

    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws["A1"] = 0.2
    other = wb.create_sheet("Calc")
    other["A1"] = "=Rate*100"
    free = wb.create_sheet("Free")
    free["A1"] = "nobody points here"
    wb.defined_names["Rate"] = DefinedName("Rate", attr_text="Data!$A$1")
    dv = DataValidation(type="list", formula1="'Calc'!$A$1:$A$3")
    other.add_data_validation(dv)
    dv.add("B1")
    wb.save(path)


def test_renaming_a_referenced_sheet_is_refused_plainly(docs_dir):
    p = docs_dir / "dn.xlsx"
    _named_workbook(p)
    raw = p.read_bytes()
    with _client() as c:
        g = c.get("/api/documents/workbook/dn.xlsx").json()
        snap = g["workbook"]
        for sid in snap["sheetOrder"]:
            snap["sheets"][sid]["origin"] = sid
        renamed = copy.deepcopy(snap)
        renamed["sheets"]["s0"]["name"] = "Rates"           # named range points here
        r = _put(c, "dn.xlsx", renamed, g["version"])
        assert r.status_code == 409 and "can't be renamed" in r.json()["detail"]
        renamed = copy.deepcopy(snap)
        renamed["sheets"]["s1"]["name"] = "Sums"            # validation points here
        assert _put(c, "dn.xlsx", renamed, g["version"]).status_code == 409
        gone = copy.deepcopy(snap)
        gone["sheetOrder"] = ["s1", "s2"]                    # delete Data
        r = _put(c, "dn.xlsx", gone, g["version"])
        assert r.status_code == 409 and "can't be deleted" in r.json()["detail"]
        assert p.read_bytes() == raw
        fine = copy.deepcopy(snap)
        fine["sheets"]["s2"]["name"] = "Notes"               # nothing points at Free
        assert _put(c, "dn.xlsx", fine, g["version"]).status_code == 200
    wb = load_workbook(p)
    assert wb.sheetnames == ["Data", "Calc", "Notes"]
    assert wb.defined_names["Rate"].attr_text == "Data!$A$1"


def test_a_formula_univer_left_on_the_old_name_also_blocks_a_rename(tmp_path):
    p = tmp_path / "f.xlsx"
    wb = Workbook()
    wb.active.title = "Data"
    wb.active["A1"] = 1
    wb.create_sheet("Calc")["A1"] = "=Data!A1*2"
    wb.save(p)
    snap = _snapshot(p)
    snap["sheets"]["s0"]["name"] = "Figures"
    with pytest.raises(sw.WorkbookRefused) as err:
        _save(p, snap)
    assert err.value.status_code == 409
    snap["sheets"]["s1"]["cellData"]["0"]["0"]["f"] = "=Figures!A1*2"   # Univer rewrote it
    _save(p, snap)
    assert load_workbook(p)["Calc"]["A1"].value == "=Figures!A1*2"


def test_mentions_sheet():
    assert sw._mentions_sheet("=SUM(Data!A1:A3)", "Data")
    assert sw._mentions_sheet("='My Data'!$A$1", "My Data")
    assert sw._mentions_sheet("='Bob''s'!A1", "Bob's")
    assert not sw._mentions_sheet("=OldData!A1", "Data")
    assert not sw._mentions_sheet("=A1", "Data")


def test_a_billion_laughs_workbook_is_not_expanded(docs_dir):
    import time

    p = docs_dir / "lol.xlsx"
    wb = Workbook()
    wb.active["A1"] = "x"
    wb.save(p)
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            + "".join(f'<!ENTITY lol{i} "{("&lol%s;" % (i - 1 if i > 1 else "")) * 10}">' for i in range(1, 10))
            + ']><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" '
            'uniqueCount="1"><si><t>&lol9;</t></si></sst>')
    _splice(p, "xl/sharedStrings.xml", lambda d: bomb.encode(), extra=None)
    t0 = time.perf_counter()
    with _client() as c:
        r = c.get("/api/documents/workbook/lol.xlsx")
    assert time.perf_counter() - t0 < 5
    assert r.status_code in (200, 415)
    assert len(r.content) < 1_000_000


def test_image_urls_are_limited_to_this_server(tmp_path):
    """=IMAGE("https://elsewhere/...") would have every browser that opens
    the sheet fetch from a third party. The bundled override allows only
    data: pictures and this server (scripts/vendor/univer/safe_image.js)."""
    import shutil
    import subprocess

    from pathlib import Path

    node = shutil.which("node")
    assert node, "node is required"
    module = Path(__file__).resolve().parents[2] / "scripts" / "vendor" / "univer" / "safe_image.js"
    script = tmp_path / "check.mjs"
    script.write_text(
        "import { imageUrlAllowed, makeSafeImage } from " + json.dumps(module.as_uri()) + ";\n"
        "const o = 'http://192.168.1.20:6369';\n"
        "const cases = ['https://tracker.example/p.png', 'http://192.168.1.20:6369/api/x.png',\n"
        "  '/api/documents/raw/a.png', 'a.png', 'data:image/png;base64,AAAA', 'data:text/html,hi',\n"
        "  '//evil.example/x.png', 'javascript:alert(1)', 'HTTP://192.168.1.20:6369/x.png', '',\n"
        "  'http://192.168.1.20:6370/x.png', 'file:///etc/passwd'];\n"
        "class Base { _handleSingleObject(s) { return 'drawn:' + s.getValue(); } }\n"
        "const Err = { create: (e) => e };\n"
        "const Safe = makeSafeImage(Base, Err, () => o);\n"
        "const str = (v) => ({ isString: () => true, getValue: () => v });\n"
        "console.log(JSON.stringify({ allowed: cases.map((u) => imageUrlAllowed(u, o)),\n"
        "  via: [new Safe()._handleSingleObject(str('https://tracker.example/p.png')),\n"
        "        new Safe()._handleSingleObject(str('/x.png'))] }));\n",
        encoding="utf-8",
    )
    out = json.loads(subprocess.run([node, str(script)], capture_output=True, text=True, check=True).stdout)
    assert out["allowed"] == [False, True, True, True, True, False, False, False, True, False, False, False]
    assert out["via"] == ["#VALUE!", "drawn:/x.png"]
    bundle = Path(__file__).resolve().parents[2] / "web" / "static" / "vendor" / "univer" / "univer.js"
    text = bundle.read_text(encoding="utf-8")
    assert "formulaFunctions" in text and "data:image" in text
    editor = (Path(__file__).resolve().parents[2] / "web" / "static" / "sheet_editor.jsx").read_text(encoding="utf-8")
    assert "formula: { function: lib.formulaFunctions || [] }" in editor


def test_the_bundle_carries_no_hyphenation_patterns():
    from pathlib import Path

    vendor = Path(__file__).resolve().parents[2] / "web" / "static" / "vendor" / "univer"
    assert not (vendor / "chunks").exists() or not any((vendor / "chunks").iterdir())
    text = (vendor / "univer.js").read_text(encoding="utf-8")
    assert "EnUs" not in text            # the one pattern set Univer inlines
    notices = (vendor / "THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8")
    assert "hyphenation patterns" in notices and "NOT included" in notices
