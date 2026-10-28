"""Typing in a sheet on a phone (wf/editors-followups, 2026-10-27).

On the Android emulator with Gboard, a double tap selected a cell but never
opened Univer's cell editor, and a soft keyboard's keydown is
"Unidentified" (229), so typed text went into Univer's hidden, closed editor
(it showed in the formula bar) and Enter, the tick or another cell dropped
it. ``sheetTouchEditing`` in web/static/sheet_editor.jsx opens the editor
the way the desktop would (double tap: edit as it is; text arriving: typing
mode). Checked by hand on the emulator (double tap + tick, tap + type +
another cell, tap + type + keyboard Enter, formula bar + tick); these pin
the wiring and the Univer API it leans on, so a Univer upgrade that renames
any of it fails here rather than silently on phones.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EDITOR = (REPO / "web" / "static" / "sheet_editor.jsx").read_text(encoding="utf-8")
UNIVER = (REPO / "web" / "static" / "vendor" / "univer" / "univer.js").read_text(encoding="utf-8")


def test_editable_workbooks_get_touch_editing():
    assert "const sheetTouchEditing = (holder, univerAPI, fWorkbook) =>" in EDITOR
    assert re.search(r"if \(j\.editable\) disposeFns\.push\(sheetTouchEditing\(holderRef\.current, univerAPI, fWorkbook\)\)",
                     EDITOR)


def test_it_listens_where_a_soft_keyboard_speaks():
    body = EDITOR[EDITOR.index("const sheetTouchEditing"):]
    body = body[:body.index("\n};\n")]
    for evt in ("pointerdown", "dblclick", "beforeinput", "compositionstart"):
        assert f"addEventListener('{evt}'" in body and f"removeEventListener('{evt}'" in body
    # only Univer's own cell editor, only text being inserted, only when no cell is open
    assert "__INTERNAL_EDITOR__DOCS_NORMAL" in EDITOR
    assert "/^insert/.test(e.inputType" in body
    assert "if (editing()) return;" in body


def test_the_univer_it_leans_on_is_the_bundled_one():
    # FWorkbook facade
    assert "startEditing(){" in UNIVER
    assert "isCellEditing(){return this._injector.get(" in UNIVER
    # the operation and its event types (DeviceInputEventType)
    assert 'id:"sheet.operation.set-cell-edit-visible"' in UNIVER
    assert 't[t.Dblclick=3]="Dblclick",t[t.Keyboard=4]="Keyboard"' in UNIVER
    assert "SHEET_EVENT_DBLCLICK = 3" in EDITOR and "SHEET_EVENT_KEYBOARD = 4" in EDITOR
    # the hidden cell editor's element id (the formula bar's is DOCS_FORMULA_BAR)
    assert 'var qM="__INTERNAL_EDITOR__",Qt=`${qM}DOCS_NORMAL`' in UNIVER
