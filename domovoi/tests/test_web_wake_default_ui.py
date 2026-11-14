"""Settings > Wake words > Household wake word (domovoi/wake_default.py).

Driven through the real JSX (jsx_interact_harness.js): the card says which
word new rooms get and that rooms you have keep theirs; a word that isn't on
the server says why and offers the download; "Use here" exists only for a
word a room can be given right now (no control that can't do what it says).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/settings.jsx"]

DOMOVOI = {"slug": "domovoi_v1", "name": "Domovoi", "phrase": "domovoi", "kind": "published",
           "threshold": None}
JARVIS = {"slug": "hey_jarvis", "name": "Hey Jarvis", "phrase": "hey jarvis", "kind": "builtin",
          "available": True, "reason": None, "threshold": None}
WHY = ("internet access is turned off for this box (Settings → Internet), so the Domovoi "
       "wake word can't be downloaded")
MISSING = {**DOMOVOI, "available": False, "reason": WHY}
HERE = {**DOMOVOI, "available": True, "reason": None}
ROOM = {"room_id": "kitchen", "status": "online", "wake_word": "hey_jarvis",
        "supports": ["wake_models", "wake_clips"]}


def _status(default, pending=(), note=None):
    return {"default": default, "options": [default, JARVIS], "pending": list(pending),
            "note": note, "boot_word": "hey_jarvis", "boot_word_here": True,
            "rooms": {"kitchen": "hey_jarvis"}}


def _api(status):
    return {"GET /api/wake-words": [], "GET /api/wake-words/trainer": {"enabled": False},
            "GET /api/wake-words/preview": {"state": "idle", "available": False,
                                            "unavailable_reason": "off"},
            "GET /api/satellites": [ROOM], "GET /api/config": {"wake_word_min_clips": 15},
            "GET /api/wake-words/default": status,
            "PUT /api/wake-words/default": status,
            "POST /api/wake-words/default/download": {**status, "download": {"ok": False, "message": WHY}},
            "POST /api/wake-words/use": {"pushed": True}}


READ = """
  h.render(); await h.settle();
  const buttons = h.findAll({ type: 'button' }).map((b) => String(b.text).trim());
  const options = h.findAll({ type: 'option' }).map((o) => String(o.text).trim());
  return { texts: h.text(), buttons, options };
"""

SCENARIOS = {
    "missing": {
        "files": FILES, "component": "WakeWordsPanel",
        "api": _api(_status(MISSING, pending=[{"room_id": "kitchen", "slug": "domovoi_v1"}],
                            note=f'New rooms wake on "Hey Jarvis" until they can be given '
                                 f'"Domovoi": {WHY}.')),
        "script": READ,
    },
    "here": {
        "files": FILES, "component": "WakeWordsPanel", "api": _api(_status(HERE)),
        "script": READ,
    },
    "use_and_choose": {
        "files": FILES, "component": "WakeWordsPanel", "api": _api(_status(HERE)),
        "script": """
          h.render(); await h.settle();
          await h.click({ type: 'button', text: 'Use “Domovoi” here' });
          await h.settle();
          const sel = h.find((el) => el.type === 'select'
            && h.findAll({ type: 'option' }).some((o) => String(o.text).includes('Hey Jarvis')));
          await h.change((el) => el === sel, 'hey_jarvis');
          await h.settle();
          return { calls: h.calls.filter((c) => c.method !== 'GET').map((c) => [c.method, c.path, c.body]) };
        """,
    },
    "download": {
        "files": FILES, "component": "WakeWordsPanel",
        "api": _api(_status(MISSING, note="x")),
        "script": """
          h.render(); await h.settle();
          await h.click({ type: 'button', text: 'Download “Domovoi” now' });
          await h.settle();
          return { texts: h.text(),
                   calls: h.calls.filter((c) => c.method !== 'GET').map((c) => [c.method, c.path]) };
        """,
    },
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


def _joined(texts) -> str:
    return " | ".join(texts) if isinstance(texts, list) else str(texts)


def test_the_card_says_rooms_keep_their_word(driven):
    t = _joined(driven["here"]["texts"])
    assert "Household wake word" in t
    assert "The word a new room wakes on. Rooms you already have keep theirs." in t
    assert "wakes on “Hey Jarvis”" in t


def test_a_word_that_isnt_here_says_why_and_offers_no_use_button(driven):
    d = driven["missing"]
    t = _joined(d["texts"])
    assert WHY in t
    assert "Waiting for “Domovoi”: kitchen" in t and "wake on" in t
    assert "Download “Domovoi” now" in d["buttons"]
    assert not any(b.startswith("Use “") for b in d["buttons"])
    assert any("not on this server yet" in o for o in d["options"])


def test_a_word_that_is_here_can_be_used_in_a_room(driven):
    d = driven["here"]
    assert "Use “Domovoi” here" in d["buttons"]
    assert "Download “Domovoi” now" not in d["buttons"]


def test_use_and_choose_call_the_right_endpoints(driven):
    calls = driven["use_and_choose"]["calls"]
    assert ["POST", "/api/wake-words/use", {"room_id": "kitchen", "slug": "domovoi_v1"}] in calls
    assert ["PUT", "/api/wake-words/default", {"slug": "hey_jarvis"}] in calls


def test_a_failed_download_says_why(driven):
    d = driven["download"]
    assert ["POST", "/api/wake-words/default/download"] in d["calls"]
    assert WHY in _joined(d["texts"])
