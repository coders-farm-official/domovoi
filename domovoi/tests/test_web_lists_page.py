"""The dashboard's Lists page (web/static/lists.jsx, V048).

Driven through domovoi/tests/jsx_interact_harness.js with the API scripted:
the lists and the open one render, an item added by voice says who and
where, typing adds, the box crosses off, deleting a list asks first, and an
empty house says what to do. No DB; needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/lists.jsx"]

_LISTS = [
    {"id": 1, "name": "To-do", "position": 1, "open_count": 1, "done_count": 0,
     "created_at": "2026-11-11T10:00:00Z", "updated_at": "2026-11-11T10:00:00Z"},
    {"id": 2, "name": "Shopping", "position": 2, "open_count": 2, "done_count": 1,
     "created_at": "2026-11-11T10:00:00Z", "updated_at": "2026-11-11T10:00:00Z"},
]


def _item(i, text, *, done=False, voice=False, pos=1):
    return {"id": i, "list_id": 2, "text": text, "done": done, "done_at": "2026-11-11T11:00:00Z" if done else None,
            "position": pos, "added_by_person_id": 1 if voice else None, "added_by_name": "Alex" if voice else None,
            "added_by_room": "kitchen" if voice else None, "added_via": "voice" if voice else "screen",
            "created_at": "2026-11-11T10:00:00Z", "updated_at": "2026-11-11T10:00:00Z"}


_DETAIL = {"list": _LISTS[1], "items": [
    _item(10, "Milk", voice=True, pos=1), _item(11, "Eggs", pos=2), _item(12, "Bread", done=True, pos=3)]}

_SETUP = r"""
ServerStore = { current: () => null, currentLabel: () => 'box:6369' };
const __st = setTimeout;
setTimeout = (fn, ms, ...a) => { const t = __st(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };
window.__ls = new Map([['domovoi-lists-selected', '2']]);
window.localStorage = localStorage = {
  getItem: (k) => (window.__ls.has(k) ? window.__ls.get(k) : null),
  setItem: (k, v) => { window.__ls.set(k, String(v)); },
  removeItem: (k) => { window.__ls.delete(k); },
};
if (typeof useRetryAfterCredential === 'undefined') useRetryAfterCredential = () => {};
"""

_READ = r"""
const cls = (e) => String((e.props && e.props.className) || '').split(' ');
const texts = (c) => h.findAll((e) => cls(e).includes(c)).map((e) => e.text);
"""


def _page(script: str, api: dict | None = None) -> dict:
    return {
        "files": FILES, "component": "ListsPage", "setup": _SETUP,
        "api": api if api is not None else {"GET /api/lists": _LISTS, "GET /api/lists/2": _DETAIL,
                                            "POST /api/lists/2/items": {"item": _item(13, "Butter"), "outcome": "added"}},
        "script": "h.render(); await h.settle(); h.rerender(); await h.settle(); h.rerender();" + _READ + script,
    }


SCENARIOS = {
    "shown": _page(
        "return { lists: texts('lists-switcher-name'), items: texts('lists-item-text'),"
        " by: texts('lists-item-by'), text: h.text() };"),
    "add": _page(
        "await h.change((e) => e.type === 'input' && e.props['aria-label'] === 'add to Shopping', 'Butter');"
        " h.rerender(); await h.submit((e) => e.type === 'form' && cls(e).includes('lists-add'));"
        " await h.settle(); h.rerender(); return h.calls;"),
    # Typed fast: the next item goes in while the first is still being sent.
    "add_fast": _page(
        "const box = (e) => e.type === 'input' && e.props['aria-label'] === 'add to Shopping';"
        " const form = (e) => e.type === 'form' && cls(e).includes('lists-add');"
        " await h.change(box, 'Butter'); h.rerender(); h.submit(form); h.rerender();"
        " const after = h.find(box).props.value;"
        " await h.change(box, 'Jam'); h.rerender(); h.submit(form); h.rerender();"
        " await h.settle(); h.rerender(); return { after, calls: h.calls };"),
    "check": _page(
        "await h.click((e) => e.props && e.props.role === 'checkbox' && e.props['aria-label'] === 'cross off Milk');"
        " await h.settle(); h.rerender(); return h.calls;"),
    "delete_list": _page(
        "await h.click((e) => e.type === 'button' && e.props['aria-label'] === 'delete list'); h.rerender();"
        " const dialog = h.findAll((e) => e.props && e.props.role === 'dialog').map((e) => e.props['aria-label']);"
        " const before = h.calls.length;"
        " await h.click({ type: 'button', text: 'Delete list' }); await h.settle(); h.rerender();"
        " return { dialog, before, calls: h.calls };"),
    "empty": _page("return h.text();", api={"GET /api/lists": []}),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    spec = tmp_path_factory.mktemp("lists-page") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_the_lists_and_the_open_one_render(driven) -> None:
    o = driven["shown"]
    assert o["lists"] == ["To-do", "Shopping"]
    assert o["items"] == ["Milk", "Eggs", "Bread"]          # open first, then crossed off
    assert o["by"] == ["Alex · kitchen"]                    # a voice add says who and where
    text = " ".join(o["text"])
    assert "crossed off · 1" in text and "2 things to go" in text


def test_typing_adds_to_the_open_list(driven) -> None:
    calls = [c for c in driven["add"] if c["method"] == "POST"]
    assert calls == [{"method": "POST", "path": "/api/lists/2/items", "body": {"text": "Butter"}}]


def test_the_box_crosses_an_item_off(driven) -> None:
    calls = [c for c in driven["check"] if c["method"] == "PATCH"]
    assert calls == [{"method": "PATCH", "path": "/api/lists/2/items/10", "body": {"done": True}}]


def test_deleting_a_list_asks_first(driven) -> None:
    o = driven["delete_list"]
    assert o["dialog"] == ["Delete Shopping?"]
    assert not [c for c in o["calls"][: o["before"]] if c["method"] == "DELETE"]
    assert [c["path"] for c in o["calls"] if c["method"] == "DELETE"] == ["/api/lists/2"]


def test_an_empty_house_says_what_to_do(driven) -> None:
    text = " ".join(driven["empty"])
    assert "no lists yet" in text and "make a list called camping" in text


def test_typing_fast_loses_nothing(driven) -> None:
    o = driven["add_fast"]
    assert o["after"] == ""                                  # the box empties at once
    assert [c["body"]["text"] for c in o["calls"] if c["method"] == "POST"] == ["Butter", "Jam"]
