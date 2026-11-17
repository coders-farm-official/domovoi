"""A room's wake words in its Settings (1.4.0; web/static/satellites.jsx).

Driven through the real JSX (jsx_interact_harness.js). A room with one word
shows that word and an optional "Add another wake word"; an admin adds,
reorders and removes words and Save sends the list. No misleading controls:
there is no add button where the device can't run another word (the
reason shows instead), and the false-wake note is there whenever a room
has more than one word.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/satellites.jsx"]
NOTE = "Each extra word makes accidental wake-ups a little more likely."

OPTIONS = [
    {"slug": "domovoi_v1", "name": "Domovoi", "phrase": "domovoi", "kind": "published",
     "available": True, "reason": None, "threshold": None},
    {"slug": "hey_jarvis", "name": "Hey Jarvis", "phrase": "hey jarvis", "kind": "builtin",
     "available": True, "reason": None, "threshold": None},
    {"slug": "porch_cat", "name": "Porch Cat", "phrase": "porch cat", "kind": "trained",
     "available": True, "reason": None, "threshold": 0.62},
]
STATUS = {"default": OPTIONS[0], "options": OPTIONS, "pending": [], "note": None,
          "boot_word": "hey_jarvis", "boot_word_here": True, "rooms": {}}


def _word(slug, name, running=True):
    return {"slug": slug, "name": name, "running": running}


def _room(words, *, limit=5, device_max=5, status="online", supports=None):
    first = words[0]
    return {"room_id": "kitchen", "status": status, "supports": supports,
            "wakes_on": {"slug": first["slug"], "name": first["name"], "source": "device",
                         "pending_slug": None, "pending_name": None, "text": first["name"],
                         "words": words, "device_max": device_max, "limit": limit,
                         "max_words": 5}}


API = {"GET /api/wake-words/default": STATUS, "PUT /api/wake-words/room": {"pushed": True}}

READ = """
  h.render(); await h.settle();
  const buttons = h.findAll({ type: 'button' }).map((b) => String(b.text).trim());
  return { texts: h.text(), buttons };
"""


def _scenario(room, script=READ):
    return {"files": FILES, "component": "RoomWakeWords", "props": {"s": room},
            "fnProps": ["fire", "refresh"], "api": API, "script": script}


ONE = [_word("domovoi_v1", "Domovoi")]
TWO = [_word("domovoi_v1", "Domovoi"), _word("hey_jarvis", "Hey Jarvis")]
THREE = [*TWO, _word("porch_cat", "Porch Cat")]

SCENARIOS = {
    "one": _scenario(_room(ONE)),
    "two": _scenario(_room(TWO)),
    "zero_full": _scenario(_room(THREE, limit=3, device_max=3)),
    "older": _scenario(_room(ONE, limit=1, device_max=None)),
    "five": _scenario(_room([_word(f"w{i}", f"W{i}") for i in range(5)])),
    "offline": _scenario(_room(TWO, status="offline")),
    "no_models": _scenario(_room(ONE, supports=["restart"])),
    "not_running": _scenario(_room([_word("domovoi_v1", "Domovoi"),
                                    _word("hey_jarvis", "Hey Jarvis", running=False)],
                                   limit=1, device_max=None)),
    "add": _scenario(_room(ONE), """
      h.render(); await h.settle();
      await h.click({ type: 'button', text: 'Add another wake word' });
      await h.settle();
      const options = h.findAll({ type: 'option' }).map((o) => String(o.text).trim());
      await h.change({ type: 'select' }, 'porch_cat');
      await h.settle();
      await h.click({ type: 'button', text: 'Add' });
      await h.settle();
      const mid = h.text();
      await h.click({ type: 'button', text: 'Save' });
      await h.settle();
      return { options, mid,
               calls: h.calls.filter((c) => c.method !== 'GET').map((c) => [c.method, c.path, c.body]),
               fired: h.fnCalls };
    """),
    "reorder_remove": _scenario(_room(THREE), """
      h.render(); await h.settle();
      await h.click({ type: 'button', title: 'move down' });
      await h.settle();
      await h.click((el) => el.type === 'button' && el.props && el.props['aria-label'] === 'remove “Porch Cat”');
      await h.settle();
      await h.click({ type: 'button', text: 'Save' });
      await h.settle();
      return { calls: h.calls.filter((c) => c.method !== 'GET').map((c) => [c.method, c.path, c.body]) };
    """),
}


@pytest.fixture(scope="module")
def driven() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


def _t(d) -> str:
    texts = d["texts"] if "texts" in d else d
    return " | ".join(texts) if isinstance(texts, list) else str(texts)


def test_one_word_is_the_word_and_an_optional_add(driven):
    d = driven["one"]
    assert "“Domovoi”" in _t(d)
    assert "Add another wake word" in d["buttons"]
    # Nothing to reorder, remove or save, and no warning for one word.
    assert not any(b in ("↑", "↓", "✕", "Save") for b in d["buttons"])
    assert NOTE not in _t(d)


def test_several_words_list_in_order_with_the_note(driven):
    d = driven["two"]
    t = _t(d)
    assert t.index("Domovoi") < t.index("Hey Jarvis")
    assert "main word" in t and NOTE in t
    assert "✕" in d["buttons"]


def test_no_add_button_where_the_device_runs_no_more(driven):
    full = driven["zero_full"]
    assert "Add another wake word" not in full["buttons"]
    assert "This room’s device can run 3 wake words." in _t(full)
    older = driven["older"]
    assert "Add another wake word" not in older["buttons"]
    assert "This room’s software runs one wake word. Upgrading it lets it run more." in _t(older)
    five = driven["five"]
    assert "Add another wake word" not in five["buttons"]
    assert "A room can have up to 5 wake words." in _t(five)


def test_no_editing_offline_or_without_pushed_models(driven):
    for name in ("offline", "no_models"):
        d = driven[name]
        assert "Add another wake word" not in d["buttons"], name
        assert not any(b in ("↑", "↓", "✕", "Save") for b in d["buttons"]), name


def test_a_word_the_device_does_not_run_says_so(driven):
    assert "not loaded on this device" in _t(driven["not_running"])


def test_adding_a_word_saves_the_list(driven):
    d = driven["add"]
    assert any("Porch Cat" in o for o in d["options"])
    assert not any(o.startswith("Domovoi") for o in d["options"]), "already the room's word"
    assert NOTE in _t(d["mid"]) and "the room restarts to load them" in _t(d["mid"])
    assert d["calls"] == [["PUT", "/api/wake-words/room",
                           {"room_id": "kitchen", "slugs": ["domovoi_v1", "porch_cat"]}]]


def test_reorder_and_remove_save_the_new_order(driven):
    assert driven["reorder_remove"]["calls"] == [
        ["PUT", "/api/wake-words/room", {"room_id": "kitchen", "slugs": ["hey_jarvis", "domovoi_v1"]}]]
