"""Closing the whiteboard must ask before it throws the scene away.

The text, markdown and spreadsheet editors all guard Close the same way:
a ``dirty`` flag, an "unsaved — click Save" hint in the header, and a
``requestClose`` that confirms before calling ``onClose``. ``drawings.jsx``
had a bare ``<Button icon="x" onClick={onClose}>`` — one click and an
unsaved Excalidraw scene was gone, with no prompt and no undo, on the same
page whose save path now reassures the operator their work is still there.

Excalidraw keeps no dirty flag of its own, so the overlay derives one from
``getSceneVersion`` (the sum of the per-element version counters: it moves
for draw/edit/delete and stays put for pan, zoom and tool changes). That
is an implementation detail; what this module pins is the contract — the
whiteboard uses the SAME guard and the SAME sentence as its three
siblings, so there is only ever one of these to learn.

Source assertions only: no DB, no node, never skips. Reading the source
cannot tell whether the flag is armed at the right MOMENT — whether the
first stroke counts, and whether the canvas even holds the scene it is
about to overwrite. ``test_web_drawing_scene_load.py`` drives the real
component for that.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"

# The one sentence the dashboard uses for this, verbatim.
CONFIRM = "You have unsaved changes. Discard them and close?"
GUARD = (
    "if (dirty && !window.confirm('" + CONFIRM + "')) return;"
)


def _src(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_every_editor_asks_the_same_question() -> None:
    """One idiom, four editors. A second phrasing here is a bug."""
    for name in ("doc_editor.jsx", "sheet_editor.jsx", "files.jsx", "drawings.jsx"):
        src = _src(name)
        assert GUARD in src, f"{name} does not guard Close with the house confirm"


def test_the_whiteboard_close_button_goes_through_the_guard() -> None:
    src = _src("drawings.jsx")
    # The Close button is the one that discards; it must not call onClose
    # directly any more.
    assert re.search(r'icon="x"\s+onClick=\{requestClose\}', src), (
        "drawings.jsx Close no longer routes through requestClose"
    )
    assert 'icon="x" onClick={onClose}' not in src
    # requestClose is the only caller of onClose in the overlay.
    assert "const requestClose = () => {" in src


def test_the_whiteboard_knows_when_the_scene_has_moved() -> None:
    """A guard that can never be armed is worse than none: it teaches the
    operator the prompt does not appear, and then one day it should."""
    src = _src("drawings.jsx")
    assert "getSceneVersion" in src
    assert "onChange={onSceneChange}" in src
    # The header says so too, the way the other three do.
    assert "unsaved — click Save" in src


def test_an_svg_export_does_not_pretend_the_scene_is_saved() -> None:
    """Export SVG writes a picture under a different name; the editable
    .excalidraw is still unsaved and Close must still say so."""
    src = _src("drawings.jsx")
    excalidraw_write = src.index("fmt: 'excalidraw'")
    svg_write = src.index("fmt: 'svg'")
    clean = src.index("savedVersion.current = versionAtSave;")
    assert svg_write < excalidraw_write < clean, (
        "the dirty flag is cleared outside the .excalidraw save branch"
    )
