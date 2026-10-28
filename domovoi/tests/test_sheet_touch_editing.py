"""Typing in a sheet on a phone (wf/editors-followups, wf/sheet-ime-double).

Univer types through a hidden contenteditable it empties after every
keystroke. An Android keyboard edits the text it believes is in the field, so
against that empty field accepting a suggestion inserted the word again
("Helo" + "Hello" -> "Helolo "), a composing keyboard committed words twice
("helloHello"), and before that a double tap never opened the editor at all.

On a touch-first device (pointer: coarse) the cell is now edited in a plain
text field of ours, SheetPhoneBar, which replaces Univer's formula bar there,
and written with Univer's setValue; Univer's hidden editors are kept from the
keyboard. Checked on the emulator with Gboard key taps: Enter, the tick,
tapping another cell, an accepted suggestion, autocorrect, backspace mid-word,
double tap, the cross, digits; every value landed once and was saved.
These pin the wiring and the Univer API it leans on.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EDITOR = (REPO / "web" / "static" / "sheet_editor.jsx").read_text(encoding="utf-8")
UNIVER = (REPO / "web" / "static" / "vendor" / "univer" / "univer.js").read_text(encoding="utf-8")


def _block(start: str) -> str:
    body = EDITOR[EDITOR.index(start):]
    return body[:body.index("\n};\n")]


def test_a_phone_types_in_a_real_field_not_in_univers_editor():
    assert "matchMedia('(pointer: coarse)')" in _block("const sheetPhoneMode")
    assert "formulaBar: !phone," in EDITOR
    assert "<SheetPhoneBar " in EDITOR
    bar = _block("const SheetPhoneBar")
    assert '<input ref={inputRef} type="text"' in bar
    assert "r.setValue(value)" in bar                       # written by Univer's own parser
    # Univer's hidden editors never get the keyboard on a phone
    assert "addEventListener('focusin', keepOff, true)" in bar
    assert "t.isContentEditable && holder && holder.contains(t)) t.blur()" in bar
    # the old shim that opened Univer's editor for a phone keyboard is gone
    assert "sheetTouchEditing" not in EDITOR
    assert "compositionstart" not in EDITOR


def test_typing_lands_in_the_cell_it_began_on_once():
    bar = _block("const SheetPhoneBar")
    assert "editRef.current = { cell: c, original: sheetCellText(rangeOf(c)) }" in bar
    assert "const r = rangeOf(ed.cell);" in bar             # not wherever the selection is now
    assert "if (value === ed.original) return false;" in bar
    assert "replace(/\\s+$/, '')" in bar                    # a suggestion's trailing space
    # buttons inside the form must not submit it
    assert bar.count('<Button type="button"') == 2


def test_desktop_plain_text_still_opens_the_cell_and_leaves_ime_alone():
    desk = _block("const sheetDesktopTyping")
    assert "e.inputType !== 'insertText' || e.isComposing" in desk
    assert "if (j.editable && !sheetPhoneMode()) disposeFns.push(sheetDesktopTyping(" in EDITOR


def test_the_univer_it_leans_on_is_the_bundled_one():
    assert "isCellEditing(){return this._injector.get(" in UNIVER
    assert 'id:"sheet.operation.set-cell-edit-visible"' in UNIVER
    assert 'id:"sheet.operation.set-selections"' in UNIVER
    assert 't[t.Dblclick=3]="Dblclick",t[t.Keyboard=4]="Keyboard"' in UNIVER
    assert 'var qM="__INTERNAL_EDITOR__",Qt=`${qM}DOCS_NORMAL`' in UNIVER
    assert "getFormula(){return this._formulaDataModel.getFormulaStringByCell(" in UNIVER
    assert "setActiveRange(e){let{unitId:n,sheetId:i}=e.getRange();" in UNIVER


# ─── wf/sheet-touch-2: a finger scrolls; tap-tap edits; long-press selects ───


def test_a_finger_scrolls_and_taps_select_on_a_phone_only():
    assert "if (sheetPhoneMode()) disposeFns.push(sheetTouchGestures(holderRef.current, univerAPI, fWorkbook));" in EDITOR
    gest = _block("const sheetTouchGestures")
    # touch only, caught before Univer; the rest of a gesture window-wide
    assert "e.pointerType !== 'touch'" in gest
    assert "holder.addEventListener('pointerdown', onDown, opts);" in gest
    assert "window.addEventListener('pointerup', onUp, opts);" in gest
    assert "const opts = { capture: true, passive: false };" in gest
    # a drag is Univer's own relative scroll; a tap or long-press is replayed to Univer
    assert "executeCommand('sheet.command.set-scroll-relative', { offsetX: dx, offsetY: dy })" in gest
    assert "replay('pointerdown', done.down, done.x0, done.y0);" in gest
    assert "replay('pointerdown', g.down, g.x0, g.y0);" in gest      # long press starts a range
    assert "SHEET_LONG_PRESS_MS = 450" in EDITOR
    # a second finger is left alone (pinch)
    assert "if (touching.size > 1) {" in gest
    # tapping the cell already selected edits it, as a double tap does
    assert "holder.dispatchEvent(new CustomEvent(SHEET_EDIT_EVENT));" in gest
    assert "holder.addEventListener(SHEET_EDIT_EVENT, editHere);" in EDITOR


def test_the_scroll_command_is_the_bundled_univers():
    assert 'id:"sheet.command.set-scroll-relative"' in UNIVER
    assert "{offsetX:u=0,offsetY:c=0}=e||{}" in UNIVER
    assert "getScrollState(){" in UNIVER


def test_the_app_lets_the_page_keep_its_drags():
    host = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "domovoi" / "app" / "ui" / "screens"
            / "documents" / "WebEditorHost.kt").read_text(encoding="utf-8")
    assert "v.parent?.requestDisallowInterceptTouchEvent(true)" in host
