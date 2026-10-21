"""The manual's "What Domovoi is good and not so good at" matches the weak-spots table.

The page is written by scripts/manual/build_manual.py from
domovoi/doc_qa/chat-weak-spots.json, the same file the chat's heads-up
reads (documents plan D3, gate: "the manual page matches the JSON"). The
manual's own in-sync test (test_manual_content.py) holds the built file to
its sources; this one holds the page to the table, cell by cell.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from domovoi.doc_qa import headsup as h

ROOT = Path(__file__).resolve().parents[2]
CONTENT = json.loads((ROOT / "web" / "static" / "manual-content.json").read_text(encoding="utf-8"))
TABLE = h.load_table()


def _builder():
    spec = importlib.util.spec_from_file_location("build_manual", ROOT / "scripts" / "manual" / "build_manual.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _page() -> dict:
    return next(p for p in CONTENT["pieces"] if p["id"] == "good-and-not")


def _items() -> list[str]:
    out = []
    for b in _page()["blocks"]:
        if b["type"] == "list":
            out += [i["text"] if isinstance(i, dict) else i for i in b["items"]]
    return out


def test_the_page_is_in_chat_files_and_calendar():
    page = _page()
    assert page["title"] == "What Domovoi is good and not so good at"
    assert page["topic"] == "chat-files-calendar"


def test_one_bullet_per_cell_in_the_tables_order():
    items = _items()
    m = TABLE["manual"]
    expected_cells = [c for kind in m["order"] for c in TABLE["cells"] if c["kind"] == kind]
    assert len(items) == len(expected_cells) == len(TABLE["cells"])
    for item, c in zip(items, expected_cells):
        assert item.startswith(f"**{m['titles'][c['kind']]}** ({m['verdicts'][c['verdict']]}): "), item


def test_every_measured_number_date_and_device_is_on_the_page():
    items = _items()
    m = TABLE["manual"]
    cells = [c for kind in m["order"] for c in TABLE["cells"] if c["kind"] == kind]
    for item, c in zip(items, cells):
        if c.get("pass") is not None and "{pct}" in c["manual"]:
            assert f"{round(c['pass'] * 100)}%" in item, item
        assert c["date"] in item and c["device"] in item and c["eval"] in item


def test_the_page_is_exactly_what_the_builder_writes_from_the_table():
    mod = _builder()
    lines = [ln for ln in mod.weak_spot_lines(TABLE) if ln.startswith("- ")]
    assert [mod.convert_inline(ln[2:]) for ln in lines] == _items()


def test_a_heads_up_is_promised_only_where_the_rules_fire():
    """The page says "a heads-up first" exactly for the cells whose
    verdict fires a card kind, so it can't promise one the app never shows."""
    items = _items()
    m = TABLE["manual"]
    cells = [c for kind in m["order"] for c in TABLE["cells"] if c["kind"] == kind]
    for item, c in zip(items, cells):
        promised = m["heads_up"] in item
        fires = c["kind"] in TABLE["kinds"] and c["verdict"] in h.FIRES and "heads-up" not in c["manual"]
        assert promised == fires, item
    handwriting = next(i for i, c in zip(items, cells) if c["kind"] == "handwriting")
    assert "heads-up before it is read" in handwriting
