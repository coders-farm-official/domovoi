"""Settings > Wake words: synthetic voices only, and "Hear the training voices".

Drives the real settings.jsx (vendored Babel, the interact harness) with a
scripted API: every model says what it was made from; a synthetic-voices-
only word has no Record and no clip count; "Train on synthetic voices only"
asks first and then makes the twin; the training voices play only from the
trainer, and when training is off the control is replaced by the sentence
saying what it needs (never another voice). No DB; needs ``node``.
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

BASE = {"threshold": 0.5, "is_default": False, "status": "ready", "error": None,
        "new_clips": 0, "source_room_id": None, "trained_clips_from": "model"}
RECORDED = {**BASE, "id": 1, "name": "Hey Alex", "slug": "hey_alex", "phrase": "hey alex",
            "model_ref": "hey_alex", "clip_count": 23, "synthetic_only": False, "trained_clips": 23}
SYNTH = {**BASE, "id": 2, "name": "Hey Sam (synthetic voices)", "slug": "hey_sam_synthetic",
         "phrase": "hey sam", "model_ref": "hey_sam_synthetic", "clip_count": 0,
         "synthetic_only": True, "trained_clips": 0}
OLD = {**BASE, "id": 3, "name": "Old One", "slug": "old_one", "phrase": "old one",
       "model_ref": "old_one", "clip_count": 30, "synthetic_only": False, "trained_clips": None,
       "trained_clips_from": None}
TRAINER_ON = {"enabled": True, "job": {"state": "done"},
              "requirements": {"estimate": {"device": "gpu", "minutes_per_word": 20}}}
PREVIEW_OK = {"state": "idle", "job": None, "available": True, "unavailable_reason": None}
PREVIEW_OFF = {"state": "idle", "job": None, "available": False,
               "unavailable_reason": "Turn on wake-word training above to hear the training voices: "
                                     "they live in the trainer."}


def _api(preview=PREVIEW_OK, words=(RECORDED, SYNTH, OLD)):
    return {"GET /api/wake-words": list(words), "GET /api/wake-words/trainer": TRAINER_ON,
            "GET /api/wake-words/preview": preview, "GET /api/satellites": [],
            "GET /api/config": {"wake_word_min_clips": 15}}


SCENARIOS = {
    "rows": {
        "files": FILES, "component": "WakeWordsPanel", "api": _api(),
        "script": """
          h.render(); await h.settle();
          const buttons = h.findAll({ type: 'button' }).map((b) => String(b.text).trim());
          return { texts: h.text(), buttons };
        """,
    },
    "copy": {
        "files": FILES, "component": "WakeWordsPanel", "api": _api(words=(RECORDED,)),
        "script": """
          h.render(); await h.settle();
          await h.click({ type: 'button', text: 'Train on synthetic voices only' });
          const dialog = h.text();
          await h.click({ type: 'button', text: 'Create and train' });
          await h.settle();
          return { dialog, calls: h.calls.map((c) => `${c.method} ${c.path}`) };
        """,
    },
    "copy_exists": {
        "files": FILES, "component": "WakeWordsPanel",
        "api": _api(words=(RECORDED, {**SYNTH, "slug": "hey_alex_synthetic",
                                      "name": "Hey Alex (synthetic voices)"})),
        "script": """
          h.render(); await h.settle();
          return { texts: h.text(),
                   button: !!h.find({ type: 'button', text: 'Train on synthetic voices only' }) };
        """,
    },
    "preview_off": {
        "files": FILES, "component": "WakeWordsPanel", "api": _api(preview=PREVIEW_OFF, words=()),
        "script": """
          h.render(); await h.settle();
          return { texts: h.text(),
                   button: !!h.find({ type: 'button', text: 'Hear the training voices' }) };
        """,
    },
    "preview_new_word": {
        "files": FILES, "component": "WakeWordsPanel",
        "api": {**_api(words=()), "POST /api/wake-words/preview":
                {"state": "running", "job": "0123456789ab"}},
        "script": """
          h.render(); await h.settle();
          const disabledEmpty = h.find({ type: 'button', text: 'Hear the training voices' }).props.disabled;
          await h.type((el) => el.type === 'input' && String(el.props.placeholder || '').startsWith('Spoken phrase'), 'hey al ex');
          await h.click({ type: 'button', text: 'Hear the training voices' });
          await h.settle();
          return { disabledEmpty, calls: h.calls.map((c) => [c.method, c.path, c.body]) };
        """,
    },
    "create_synthetic": {
        "files": FILES, "component": "WakeWordsPanel", "api": _api(words=()),
        "script": """
          h.render(); await h.settle();
          await h.type((el) => el.type === 'input' && String(el.props.placeholder || '').startsWith('Name'), 'Hey Alex');
          await h.type((el) => el.type === 'input' && String(el.props.placeholder || '').startsWith('Spoken phrase'), 'hey alex');
          const box = h.find((el) => el.type === 'input' && el.props.type === 'checkbox');
          await h.change((el) => el === box, true);
          await h.submit({ type: 'form' });
          await h.settle();
          return { calls: h.calls.map((c) => [c.method, c.path, c.body]) };
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


def test_each_model_says_what_it_was_made_from(driven):
    t = _joined(driven["rows"]["texts"])
    assert "model: synthetic voices + 23 of your recordings" in t
    assert "model: synthetic voices only" in t
    assert "model trained before Domovoi kept count: it may include your recordings" in t


def test_a_synthetic_word_has_no_recording_controls(driven):
    buttons = driven["rows"]["buttons"]
    # Two recorded words have Record; the synthetic one doesn't.
    assert buttons.count("Record") == 2
    assert buttons.count("Train on synthetic voices only") == 2
    assert "clips (23)" in buttons and "clips (0)" not in buttons
    assert "synthetic voices only" in _joined(driven["rows"]["texts"])


def test_the_synthetic_copy_asks_first_then_makes_the_twin(driven):
    d = _joined(driven["copy"]["dialog"])
    assert "Train “Hey Alex” on synthetic voices only?" in d
    assert "none of your recordings go into it" in d
    assert "stay as they are" in d
    assert driven["copy"]["calls"] == ["POST /api/wake-words/1/synthetic-copy"]


def test_no_second_copy_offered(driven):
    assert driven["copy_exists"]["button"] is False
    assert "synthetic-voices copy: Hey Alex (synthetic voices)" in _joined(driven["copy_exists"]["texts"])


def test_training_off_replaces_the_control_with_what_it_needs(driven):
    assert driven["preview_off"]["button"] is False
    assert "Turn on wake-word training above to hear the training voices" in \
        _joined(driven["preview_off"]["texts"])


def test_hear_the_training_voices_sends_the_unsaved_spelling(driven):
    r = driven["preview_new_word"]
    assert r["disabledEmpty"] is True
    assert ["POST", "/api/wake-words/preview", {"phrase": "hey al ex"}] in r["calls"]


def test_create_a_synthetic_word(driven):
    assert ["POST", "/api/wake-words",
            {"name": "Hey Alex", "phrase": "hey alex", "synthetic_only": True}] in \
        driven["create_synthetic"]["calls"]
