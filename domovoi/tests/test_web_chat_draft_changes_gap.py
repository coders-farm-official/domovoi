"""The draft changes view (chat_drafts.jsx ``Changes``) puts a display-only
paragraph gap between a long removed run and the added run right after it.

Past its work budget the server's word diff (``_replaced`` in
domovoi/drafts/diff.py) shows everything between the shared start and end
as one removed run then one added run. Drawn inline, the old text's last
word ran straight into the new text's first. The gap is an empty
``aria-hidden`` block, so the words themselves (and a copy of them) are
unchanged; a short swap, or a junction that already breaks the line,
gets none.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_render_harness.js")

OLD = "I am writing to apply for the warehouse lead job at your company this spring"
NEW = "Please consider me for the warehouse lead role"
SCENARIOS = {
    "long": [["=", "Dear Ms. Lee,\n\n"], ["-", OLD], ["+", NEW], ["=", ".\n\nSincerely"]],
    "short": [["=", "the "], ["-", "cat"], ["+", "dog"], ["=", " sat"]],
    "broken": [["-", OLD + "\n\n"], ["+", NEW]],
    "added_first": [["+", NEW], ["-", OLD]],
}


def _render() -> dict:
    node = shutil.which("node")
    assert node, "node is required to render web/static JSX"
    scen = {name: {"file": "web/static/chat_drafts.jsx", "component": "ChatDrafts.Changes", "props": {"ops": ops}}
            for name, ops in SCENARIOS.items()}
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), json.dumps(scen)],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _gaps(els: list[dict]) -> list[dict]:
    return [e for e in els if (e["props"].get("className") or "") == "chat-draft-gap"]


def test_a_long_removed_run_and_the_added_run_after_it_have_a_gap_between():
    out = _render()
    els = out["long"]
    kinds = [(e["type"], e["props"].get("className")) for e in els[1:]]
    assert kinds == [("span", None), ("del", "chat-draft-del"), ("span", "chat-draft-gap"),
                     ("span", "chat-draft-add"), ("span", None)]
    gap = _gaps(els)[0]
    assert gap["text"] == "" and gap["props"].get("aria-hidden") == "true"
    # The words are unchanged: the gap adds no text of its own.
    assert "".join(e["text"] for e in els) == "".join(t for _, t in SCENARIOS["long"])


def test_no_gap_for_a_short_swap_a_broken_junction_or_added_then_removed():
    out = _render()
    for name in ("short", "broken", "added_first"):
        assert _gaps(out[name]) == [], name
