"""Source-level regression checks for the no-build dashboard (web/static).

The dashboard is Babel-in-browser JSX with no test runner of its own, so
the findings fixed here are pinned the way test_vendor_excalidraw.py pins
its invariants: read the file, assert the shape. Each check names the
finding and the functional card it came from.

Pure file reads — no DB, no ``requires_db`` — these must never skip.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"


def _src(name: str) -> str:
    """File text with block comments stripped (as dupglobals.js does), so a
    comment explaining a removal cannot satisfy or trip a check."""
    text = (STATIC / name).read_text(encoding="utf-8")
    return re.sub(r"/\*[\s\S]*?\*/", "", text)


def _component(src: str, name: str) -> str:
    """The text of a top-level `const Name = (...) => {` up to the next
    top-level `const`, enough to look inside one component."""
    m = re.search(rf"^const {name} = .*?(?=^const )", src, re.MULTILINE | re.DOTALL)
    assert m, f"{name} not found"
    return m.group(0)


# ── F-007 · SH-10 ─────────────────────────────────────────────────────
# The topbar carried a tab-focusable role="button" reading "search
# anything ⌘K" with no handler, no shortcut and no search behind it. Until
# a command palette exists the shell must not advertise one.

def test_topbar_has_no_inert_search_affordance():
    topbar = _component(_src("components.jsx"), "Topbar")
    assert 'className="cmdk"' not in topbar
    assert "search anything" not in topbar
    assert "⌘K" not in topbar
    # No other focusable non-button decoration crept in either.
    assert 'role="button"' not in topbar


def test_cmdk_styles_went_with_the_control():
    assert ".cmdk" not in _src("styles.css")
