"""The wake-word push button says which Wake sensitivity the room gets
(2026-11-11).

A push used to leave the word's own threshold (0.5 by default) on the room
device, where it quietly overrode the Wake sensitivity set on the room's
Settings tab. The satellite now writes the pushed value into that setting,
and the button in Settings → Voice → Wake words names it before you press
it: "Push to room (wake sensitivity 0.50)". The pill on a room's health
card that flags a device still on older software uses the same words.

Driven through the dashboard's own Babel (jsx_render_harness.js). No DB;
needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RENDER = Path(__file__).with_name("jsx_render_harness.js")
SETTINGS = "web/static/settings.jsx"
SATELLITES = "web/static/satellites.jsx"

ROOMS = [{"room_id": "kitchen", "supports": ["wake_models", "wake_clips"]}]
WORD = {"id": 3, "name": "Hey Home", "slug": "hey_home", "phrase": "hey home",
        "status": "ready", "threshold": 0.5, "clip_count": 20, "new_clips": 0,
        "is_default": False, "model_ref": "hey_home.onnx"}
FNS = ["fire", "onRecordStart", "onRecordStop", "onTrain", "onRename", "onThreshold",
       "onSetDefault", "onPush", "onDelete", "onTrainStop", "onPhrase", "onSyntheticCopy"]


def _row(word: dict) -> dict:
    return {"file": SETTINGS, "component": "WakeRow",
            "props": {"w": word, "rooms": ROOMS, "minClips": 15}, "fnProps": FNS,
            "preload": ["web/static/components.jsx"]}


SCENARIOS = {
    "default": _row(WORD),
    "tuned": _row({**WORD, "threshold": 0.8}),
    "no_number": _row({**WORD, "threshold": None}),
}


@pytest.fixture(scope="module")
def rendered() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run([node, str(RENDER), str(REPO_ROOT), json.dumps(SCENARIOS)],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def _push(nodes: list[dict]) -> dict:
    (btn,) = [n for n in nodes if n["type"] == "Button" and n["text"].startswith("Push to room")]
    return btn


def test_the_push_button_names_the_sensitivity_the_room_gets(rendered) -> None:
    btn = _push(rendered["default"])
    assert btn["text"] == "Push to room (wake sensitivity 0.50)"
    assert btn["props"]["title"] == "the room's Wake sensitivity becomes 0.50"
    assert _push(rendered["tuned"])["text"] == "Push to room (wake sensitivity 0.80)"


def test_a_word_without_a_number_says_only_push(rendered) -> None:
    btn = _push(rendered["no_number"])
    assert btn["text"] == "Push to room"
    assert btn["props"].get("title") is None


def test_the_rows_number_is_called_wake_sensitivity(rendered) -> None:
    nodes = rendered["default"]
    assert any(n["type"] == "span" and n["text"] == "wake sensitivity" for n in nodes)
    (box,) = [n for n in nodes if n["type"] == "input" and n["props"].get("aria-label") == "wake sensitivity"]
    assert box["props"]["value"] == 0.5


def test_the_satellite_pill_still_flags_an_older_device() -> None:
    """An older room device still reports a threshold in use that differs
    from its setting; the override check keeps flagging it."""
    src = (REPO_ROOT / SATELLITES).read_text(encoding="utf-8")
    assert "const satWakeOverride = (s) =>" in src
    assert "wake sensitivity {satWakeOverride(s).inUse}" in src
