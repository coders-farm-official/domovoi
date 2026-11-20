"""The now-playing sheet on a phone (owner report 2026-11-17).

A vertical swipe that began on the visualizer, the lyrics or the equalizer
panel did not scroll the sheet: the panel and the lyric list were scroll
containers with overscroll-behavior: contain, so the finger was trapped in
them. On a touch screen they now chain to the sheet; the equalizer's
sliders alone claim their drag. Read as text, no DB.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "web" / "static"
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")


def _coarse() -> str:
    m = re.search(r"@media \(pointer: coarse\) \{(.*?)\n\}", CSS, re.S)
    assert m, "no touch-screen block"
    return m.group(1)


def test_panel_and_lyrics_chain_to_the_sheet_on_touch():
    block = _coarse()
    assert re.search(r"\.mp-panel, \.lyr-scroll \{ overscroll-behavior-y: auto; \}", block)


def test_only_the_eq_sliders_claim_touch():
    assert re.search(r"\.mp-panel \.eq-slider \{ touch-action: none; \}", _coarse())
    for sel in (".viz-stage", ".viz-canvas", ".viz-over", ".mp-panel", ".lyr-scroll", ".lyr-view"):
        for m in re.finditer(r"(?m)^\s*" + re.escape(sel) + r"\s*\{(.*?)\}", CSS, re.S):
            assert "touch-action: none" not in m.group(1), sel


def test_no_touch_handler_prevents_scroll_in_viz_or_lyrics():
    # lyrics.jsx has a touch-move handler that only stops auto-following;
    # neither file may cancel a touch (that would freeze the sheet).
    for name in ("visualizer.jsx", "lyrics.jsx"):
        src = (STATIC / name).read_text(encoding="utf-8")
        assert not re.search(r"pointermove|addEventListener\(\s*['\"]touch", src, re.I), name
        for m in re.finditer(r"onTouch\w+=\{([^}]*)\}", src):
            assert "preventDefault" not in m.group(1), name
