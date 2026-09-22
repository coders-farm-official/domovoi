"""F-009 — every Settings delete asks first, through one shared dialog.

Settings' trash icons called ``apiDelete`` directly: the first click on a
greeting, a voice, a wake word or one of its recorded clips WAS the
deletion — no dialog, no undo — while Files shipped its own confirm for
the same kind of action (finding F-009, card SET-01). The fix lifts the
dialog into components.jsx (``DeleteConfirmDialog`` + the
``useDeleteConfirm`` hook) and puts it in front of all four handlers, the
Models tab's ``window.confirm`` and Files' own copy.

The panels are driven for real: components.jsx and the page file are
compiled with the dashboard's vendored Babel and run in a Node vm with a
small stateful React (domovoi/tests/jsx_interact_harness.js), so a
scenario can click the trash icon, look for the dialog, cancel, click
again and confirm, and inspect what the page sent.

No DB, no ``requires_db`` — never skips. Needs ``node`` (the runtime the
JSX compile check already relies on) and fails, not skips, without it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = "web/static/components.jsx"
SETTINGS = "web/static/settings.jsx"

# One row per panel; the trash icon's title is how the row is found.
GREETING = {"id": 80, "text": "hello there", "category": "funny", "enabled": True}
PIPER_VOICE = {"id": 3, "name": "Amy", "engine": "piper", "model_ref": "amy.onnx", "is_default": False}
WAKE_WORD = {"id": 7, "name": "Hey Domovoi", "phrase": "hey domovoi", "status": "trained",
             "threshold": 0.5, "is_default": False, "clip_count": 20, "selected_count": 20}
CLIP = {"name": "clip_0003.wav", "verdict": "good", "selected": True, "has_trimmed": True,
        "metrics": {}, "env": []}
MODEL = {"name": "qwen2.5:7b", "size_bytes": 4_700_000_000, "loaded": False, "quant": "Q4_K_M"}

# Click the trash icon, cancel, click it again, confirm. The result says
# what was sent at each step and what the dialog showed.
DRIVE = r"""
  h.render();
  const trash = { type: 'button', title: TRASH };
  await h.click(trash);
  const afterClick = { calls: h.calls.length, dialog: !!h.find({ text: DIALOG_TITLE }),
                       texts: h.text() };
  await h.click({ type: 'button', text: 'Cancel' });
  const afterCancel = { calls: h.calls.length, dialog: !!h.find({ text: DIALOG_TITLE }) };
  await h.click(trash);
  await h.click({ type: 'button', text: 'Delete' });
  const afterConfirm = { calls: h.calls.map((c) => `${c.method} ${c.path}`),
                         dialog: !!h.find({ text: DIALOG_TITLE }) };
  return { afterClick, afterCancel, afterConfirm };
"""


def _drive(trash_title: str, dialog_title: str) -> str:
    return (f"const TRASH = {json.dumps(trash_title)}; "
            f"const DIALOG_TITLE = {json.dumps(dialog_title)};" + DRIVE)


SCENARIOS = {
    "greetings": {
        "files": [COMPONENTS, SETTINGS], "component": "GreetingsPanel",
        "api": {"GET /api/greetings": [GREETING]},
        "script": _drive("delete", "Delete this greeting?"),
    },
    "voices": {
        "files": [COMPONENTS, SETTINGS], "component": "VoicesPanel",
        "api": {"GET /api/voices": [PIPER_VOICE]},
        "script": _drive("delete", "Delete the voice “Amy”?"),
    },
    "wake_words": {
        "files": [COMPONENTS, SETTINGS], "component": "WakeWordsPanel",
        "api": {"GET /api/wake-words": [WAKE_WORD],
                "GET /api/wake-words/7/clips": {"clips": [], "selected_count": 0, "min_clips": 15},
                "GET /api/satellites": []},
        "script": _drive("delete", "Delete the wake word “Hey Domovoi”?"),
    },
    "wake_clips": {
        "files": [COMPONENTS, SETTINGS], "component": "WakeClipGrid",
        "props": {"wid": 7, "canScore": True}, "fnProps": ["fire"],
        "api": {"GET /api/wake-words/7/clips": {"clips": [CLIP], "selected_count": 1, "min_clips": 15}},
        "script": _drive("delete clip", "Delete clip clip_0003.wav?"),
    },
    "models": {
        "files": [COMPONENTS, "web/static/models.jsx"], "component": "ModelsPanel",
        "api": {"GET /api/models/installed": {"installed": [MODEL], "ollama_reachable": True},
                "GET /api/models/catalog": {"ollama": [], "whisper": []},
                "GET /api/models/active": {"roles": []},
                "GET /api/models/hardware": {"gpus": [], "ram": None},
                "GET /api/models/jobs": {"jobs": []}},
        "script": _drive("delete from disk", "Delete qwen2.5:7b from disk?"),
    },
    # Files keeps its folder warning, now inside the shared dialog.
    "files_folder": {
        "files": [COMPONENTS, "web/static/files.jsx"], "component": "FilesDeleteConfirm",
        "props": {"state": {"entries": [{"rel": "docs", "is_dir": True}, {"rel": "a.txt", "is_dir": False}]},
                  "busy": False},
        "fnProps": ["onCancel", "onConfirm"],
        "script": "h.render(); return { texts: h.text(), plain: h.tree().map(h.plain) };",
    },
    "files_plain": {
        "files": [COMPONENTS, "web/static/files.jsx"], "component": "FilesDeleteConfirm",
        "props": {"state": {"entries": [{"rel": "a.txt", "is_dir": False}]}, "busy": False},
        "fnProps": ["onCancel", "onConfirm"],
        "script": "h.render(); return { texts: h.text() };",
    },
    # The dialog itself: backdrop click cancels, busy disables both buttons.
    "dialog_busy": {
        "files": [COMPONENTS], "component": "DeleteConfirmDialog",
        "props": {"title": "Delete x?", "busy": True}, "fnProps": ["onCancel", "onConfirm"],
        "script": ("h.render(); const btns = h.findAll({ type: 'button' }).map(h.plain);"
                   " return { btns, texts: h.text() };"),
    },
}

EXPECTED_DELETE = {
    "greetings": "DELETE /api/greetings/80",
    "voices": "DELETE /api/voices/3",
    "wake_words": "DELETE /api/wake-words/7",
    "wake_clips": "DELETE /api/wake-words/7/clips/clip_0003.wav",
    "models": "DELETE /api/models/qwen2.5%3A7b",
}


@pytest.fixture(scope="module")
def driven() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


@pytest.mark.parametrize("panel", sorted(EXPECTED_DELETE))
def test_first_click_only_opens_the_dialog(driven, panel):
    r = driven[panel]["afterClick"]
    assert r["calls"] == 0, f"{panel}: the trash icon still deletes on the first click"
    assert r["dialog"], f"{panel}: no confirm dialog after the trash click: {r['texts']}"
    assert "Cancel" in r["texts"] and "Delete" in r["texts"]


@pytest.mark.parametrize("panel", sorted(EXPECTED_DELETE))
def test_cancel_sends_nothing_and_closes(driven, panel):
    r = driven[panel]["afterCancel"]
    assert r["calls"] == 0
    assert not r["dialog"]


@pytest.mark.parametrize("panel", sorted(EXPECTED_DELETE))
def test_confirm_sends_the_one_delete(driven, panel):
    r = driven[panel]["afterConfirm"]
    assert r["calls"] == [EXPECTED_DELETE[panel]]
    assert not r["dialog"], f"{panel}: dialog still open after the delete settled"


def test_dialog_names_the_row_it_is_about(driven):
    assert "“hello there” leaves the funny bank" in " ".join(driven["greetings"]["afterClick"]["texts"])
    voice = " ".join(driven["voices"]["afterClick"]["texts"])
    assert "Piper model" in voice           # the expensive case says what is lost
    wake = " ".join(driven["wake_words"]["afterClick"]["texts"])
    assert "recorded clips and trained model" in wake


def test_files_keeps_its_folder_warning_inside_the_shared_dialog(driven):
    texts = driven["files_folder"]["texts"]
    assert "Delete 2 items?" in texts
    assert any("and everything inside it will be permanently" in t for t in texts)
    assert "Delete everything" in texts
    assert any(p["type"] == "Icon" and p["props"].get("name") == "folder"
               for p in driven["files_folder"]["plain"])
    plain = driven["files_plain"]["texts"]
    assert "Delete 1 item?" in plain and "Delete" in plain and "Delete everything" not in plain


def test_busy_dialog_disables_both_buttons(driven):
    btns = driven["dialog_busy"]["btns"]
    assert len(btns) == 2 and all(b["props"].get("disabled") for b in btns)
    assert "Deleting…" in driven["dialog_busy"]["texts"]


# The static half: exactly one dialog definition in the whole SPA (a
# second top-level `const DeleteConfirmDialog` would white-screen the
# page — one Babel scope), and no Settings delete left on window.confirm.
def test_one_dialog_definition_and_no_window_confirm_in_settings():
    defs = [f.name for f in STATIC.glob("*.jsx")
            if re.search(r"^const DeleteConfirmDialog\b", f.read_text(encoding="utf-8"), re.M)]
    assert defs == ["components.jsx"], defs
    for name in ("settings.jsx", "models.jsx"):
        src = (STATIC / name).read_text(encoding="utf-8")
        # window.confirm stays for the git-pull/restart prompts; no delete
        # may sit behind one (the text of every remaining call is checked).
        for m in re.finditer(r"window\.confirm\(([^;]*)", src):
            assert not re.search(r"delete", m.group(1), re.I), f"{name}: delete behind window.confirm"
        assert "useDeleteConfirm(" in src
