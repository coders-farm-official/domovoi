"""The shared <Tabs/> strip scrolls sideways instead of widening the page.

At 375px the Music page's six tabs measured 470px and Settings' 692px;
the strip was a plain flex row, so it pushed <main> wider than the
screen and the last tabs sat off-page. The strip now scrolls inside its
card, its tabs keep their width, and the baseline is an inset shadow so
the scroll box doesn't clip the active underline (a scrolling box clips
at its padding edge, where the old border-bottom overlap sat).

Rendered with the dashboard's vendored Babel through
domovoi/tests/jsx_interact_harness.js — no DB, never skips; needs node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")

TABS = [{"id": "library", "label": "Library", "count": 12}, {"id": "player", "label": "Player"},
        {"id": "queue", "label": "Room queue"}, {"id": "playlists", "label": "Playlists"},
        {"id": "stats", "label": "Stats"}, {"id": "jobs", "label": "Jobs"}]

SCENARIOS = {
    "tabs": {"files": ["web/static/components.jsx"], "component": "Tabs",
             "props": {"tabs": TABS, "value": "jobs"}, "fnProps": ["onChange"],
             "script": r"""
               h.render();
               const strip = h.find((el) => el.props && el.props.className === 'tabs-strip');
               return { strip: strip.props.style,
                        buttons: h.findAll({ type: 'button' }).map((b) => b.props.style) };
             """},
}


@pytest.fixture(scope="module")
def rendered() -> dict:
    node = shutil.which("node")
    assert node, "node is required to render web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)["tabs"]
    assert "__harness_error" not in out, out
    return out


def test_strip_scrolls_inside_its_card(rendered):
    assert rendered["strip"]["overflowX"] == "auto"


def test_baseline_is_an_inset_shadow_not_a_border(rendered):
    style = rendered["strip"]
    assert "borderBottom" not in style
    assert style["boxShadow"] == "inset 0 -1px 0 var(--border)"


def test_tabs_keep_their_width(rendered):
    assert len(rendered["buttons"]) == len(TABS)
    for style in rendered["buttons"]:
        assert style["flexShrink"] == 0
        assert style["whiteSpace"] == "nowrap"
        assert "marginBottom" not in style
