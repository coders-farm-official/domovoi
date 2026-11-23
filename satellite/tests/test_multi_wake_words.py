"""Several wake words in one room (1.4.0; satellite/wake_words.py).

The satellite loads every word of the room (main word first) into one
openWakeWord model, gates each at its own threshold, reports which word
fired, says in its hello how many words it can run (a Pi Zero 2 W 3,
anything else 5), and never loads more than that. A single-word push from
an older core leaves it on that one word. No database, no audio.
"""

from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from satellite import wake_words
from satellite.tests.test_wake_ack import _sat, client, settle


# ─── how many words a device runs ────────────────────────────────────────

@pytest.mark.parametrize("is_pc, model, limit", [
    (True, None, 5),
    (False, "Raspberry Pi Zero 2 W Rev 1.0", 3),
    (False, "Raspberry Pi 4 Model B Rev 1.5", 5),
    (False, "Raspberry Pi 5 Model B Rev 1.0", 5),
    (False, None, 5),
])
def test_the_device_limit(is_pc, model, limit):
    assert wake_words.device_limit(is_pc=is_pc, model=model) == limit


def test_the_board_model_is_read_from_the_device_tree(tmp_path):
    f = tmp_path / "model"
    f.write_bytes(b"Raspberry Pi Zero 2 W Rev 1.0\x00")
    assert wake_words.board_model(f) == "Raspberry Pi Zero 2 W Rev 1.0"
    assert wake_words.board_model(tmp_path / "missing") is None


def test_a_pushed_list_is_cleaned():
    got = wake_words.parse_words([
        {"slug": "domovoi_v1"}, {"slug": "porch_cat", "threshold": 0.62},
        {"slug": "porch_cat"}, {"slug": "../etc"}, "hey_jarvis",
        {"slug": "kitty", "threshold": 7}, {"slug": "a"}, {"slug": "b"}])
    assert got == [{"slug": "domovoi_v1", "threshold": None},
                   {"slug": "porch_cat", "threshold": 0.62},
                   {"slug": "hey_jarvis", "threshold": None},
                   {"slug": "kitty", "threshold": None},
                   {"slug": "a", "threshold": None}]
    assert wake_words.parse_words("domovoi_v1") == []


def test_the_list_is_used_only_while_it_starts_with_the_main_word(tmp_path):
    f = tmp_path / "wake_words.json"
    words = [{"slug": "domovoi_v1", "threshold": None}, {"slug": "porch_cat", "threshold": 0.6}]
    wake_words.write_list(f, words)
    assert wake_words.read_list(f, "domovoi_v1") == words
    # A later single-word push changed the main word: the main word alone.
    assert wake_words.read_list(f, "hey_jarvis") is None
    f.write_text("not json", encoding="utf-8")
    assert wake_words.read_list(f, "domovoi_v1") is None
    # One word needs no list.
    wake_words.write_list(f, words[:1])
    assert not f.exists()


# ─── which words are loaded ──────────────────────────────────────────────

@pytest.fixture
def sidecars(tmp_path, monkeypatch):
    models = tmp_path / "wake_models"
    models.mkdir()
    monkeypatch.setattr(client, "WAKE_SIDECAR", tmp_path / "wake")
    monkeypatch.setattr(client, "WAKE_WORDS_SIDECAR", tmp_path / "wake_words.json")
    monkeypatch.setattr(client, "WAKE_MODELS_DIR", models)
    monkeypatch.setattr(client, "WAKE_THRESHOLD_SIDECAR", tmp_path / "wake_threshold")
    monkeypatch.setattr(client, "_wake_words_limit", lambda cfg: 5)
    return tmp_path


def _cfg(**kw):
    return types.SimpleNamespace(wake_word="hey_jarvis", wake_threshold=0.7,
                                 wake_word_model_path=None, **kw)


def _push(tmp: Path, words: list[dict], models=()):
    (tmp / "wake").write_text(words[0]["slug"] + "\n", encoding="utf-8")
    (tmp / "wake_words.json").write_text(json.dumps(words), encoding="utf-8")
    for slug in models:
        (tmp / "wake_models" / f"{slug}.onnx").write_bytes(b"onnx")


def test_one_word_is_what_it_always_was(sidecars):
    assert client._effective_wake_words(_cfg()) == [
        {"slug": "hey_jarvis", "path": None, "threshold": 0.7}]


def test_every_word_is_loaded_with_its_own_threshold(sidecars):
    _push(sidecars, [{"slug": "domovoi_v1", "threshold": None},
                     {"slug": "porch_cat", "threshold": 0.62},
                     {"slug": "hey_jarvis", "threshold": None}],
          models=("domovoi_v1", "porch_cat"))
    got = client._effective_wake_words(_cfg())
    assert [(w["slug"], w["threshold"]) for w in got] == [
        ("domovoi_v1", 0.7), ("porch_cat", 0.62), ("hey_jarvis", 0.7)]
    assert got[0]["path"].endswith("domovoi_v1.onnx") and got[2]["path"] is None


def test_no_more_words_than_the_device_runs(sidecars, monkeypatch, caplog):
    monkeypatch.setattr(client, "_wake_words_limit", lambda cfg: 3)
    names = ["domovoi_v1", "w2", "w3", "w4", "w5"]
    _push(sidecars, [{"slug": n, "threshold": None} for n in names], models=names)
    caplog.set_level(logging.INFO, logger="satellite")
    assert [w["slug"] for w in client._effective_wake_words(_cfg())] == names[:3]
    assert any("this device runs 3" in r.getMessage() for r in caplog.records)


def test_an_extra_word_without_its_model_is_left_out(sidecars, caplog):
    _push(sidecars, [{"slug": "domovoi_v1", "threshold": None},
                     {"slug": "porch_cat", "threshold": None}], models=("domovoi_v1",))
    assert [w["slug"] for w in client._effective_wake_words(_cfg())] == ["domovoi_v1"]


def test_the_model_loads_every_word(sidecars, monkeypatch):
    built = []

    class _Pre:
        pass

    class _Model:
        def __init__(self, **kwargs):
            built.append(kwargs)
            self.preprocessor = _Pre()

    fake = types.ModuleType("openwakeword.model")
    fake.Model = _Model
    monkeypatch.setitem(sys.modules, "openwakeword", types.ModuleType("openwakeword"))
    monkeypatch.setitem(sys.modules, "openwakeword.model", fake)
    _push(sidecars, [{"slug": "domovoi_v1", "threshold": None},
                     {"slug": "hey_jarvis", "threshold": 0.55}], models=("domovoi_v1",))
    sat = object.__new__(client.Satellite)
    sat.cfg = _cfg()
    sat._load_wake_model()
    assert built[0]["wakeword_models"] == [str(sidecars / "wake_models" / "domovoi_v1.onnx"),
                                           "hey_jarvis"]
    assert (sat._wake_word, sat._wake_threshold) == ("domovoi_v1", 0.7)
    assert sat._wake_thresholds == {"hey_jarvis": 0.55}
    assert sat._wake_model_loaded == "domovoi_v1"


# ─── which word fired ────────────────────────────────────────────────────

class _TwoWords:
    """A model scoring two words from a script, then 0."""

    def __init__(self, script: list[tuple[float, float]]) -> None:
        self.script = list(script)

    def reset(self) -> None:
        pass

    def predict(self, chunk):
        a, b = self.script.pop(0) if self.script else (0.0, 0.0)
        return {"hey_jarvis": a, "porch_cat": b}


def _start_frame(sat, loop) -> dict:
    sat._begin_utterance("wake_word")
    loop.run_until_complete(settle())
    while not sat.send_q.empty():
        kind, data = sat.send_q.get_nowait()
        if kind == "text":
            msg = json.loads(data)
            if msg.get("type") == "utterance_start":
                return msg
    raise AssertionError("no utterance_start")


def test_the_extra_word_wakes_at_its_own_threshold_and_is_reported(monkeypatch, caplog):
    sat, loop = _sat(monkeypatch, "none", [])
    sat._wake_thresholds = {"porch_cat": 0.4}
    caplog.set_level(logging.INFO, logger="satellite")
    try:
        # 0.45 is under the main word's 0.5 but over the extra word's 0.4.
        assert sat._wait_for_wake(_TwoWords([(0.45, 0.0), (0.1, 0.45)])) is True
        start = _start_frame(sat, loop)
    finally:
        loop.close()
    assert start["wake_word"] == "porch_cat" and start["wake_score"] == 0.45
    lines = [r.getMessage() for r in caplog.records]
    assert any("listening for wake words 'hey_jarvis', 'porch_cat'" in m for m in lines)
    assert any(m.startswith("wake word 'porch_cat' detected") for m in lines)


def test_the_main_word_is_reported_too(monkeypatch):
    sat, loop = _sat(monkeypatch, "none", [])
    try:
        assert sat._wait_for_wake(_TwoWords([(0.9, 0.0)])) is True
        start = _start_frame(sat, loop)
    finally:
        loop.close()
    assert start["wake_word"] == "hey_jarvis"


def test_barge_in_and_the_drop_in_watch_hear_every_word(monkeypatch):
    sat, loop = _sat(monkeypatch, "none", [])
    loop.close()
    sat._wake_thresholds = {"porch_cat": 0.4}
    assert sat._wake_hit({"hey_jarvis": 0.2, "porch_cat": 0.41}) == "porch_cat"
    assert sat._wake_hit({"hey_jarvis": 0.51, "porch_cat": 0.9}) == "hey_jarvis"
    assert sat._wake_hit({"hey_jarvis": 0.5, "porch_cat": 0.4}) is None


# ─── the hello and the push ──────────────────────────────────────────────

def test_the_hello_names_the_words_and_the_limit(sidecars, monkeypatch):
    monkeypatch.setattr(client, "_wake_words_limit", lambda cfg: 3)
    _push(sidecars, [{"slug": "domovoi_v1", "threshold": None},
                     {"slug": "hey_jarvis", "threshold": None}], models=("domovoi_v1",))
    sat = client.Satellite(client.Config.from_toml({"device": {"profile": "xvf3800_usb"}}))
    hello = sat._hello_frame()
    assert hello["wake_word"] == "domovoi_v1"
    assert hello["wake_words"] == ["domovoi_v1", "hey_jarvis"]
    assert hello["wake_words_max"] == 3


def test_a_pushed_list_is_kept_and_the_room_restarts(sidecars, monkeypatch):
    sat = object.__new__(client.Satellite)
    sat.cfg = _cfg()
    done = []
    monkeypatch.setattr(sat, "_sync_wake_models", lambda: done.append("sync"), raising=False)
    monkeypatch.setattr(sat, "_restart_service", lambda: done.append("restart"), raising=False)
    monkeypatch.setattr(client, "_store_wake_threshold", lambda *a, **k: True)
    sat._apply_wake_switch("domovoi_v1", None, words=[
        {"slug": "domovoi_v1", "threshold": None}, {"slug": "porch_cat", "threshold": 0.62}])
    assert (sidecars / "wake").read_text(encoding="utf-8").strip() == "domovoi_v1"
    assert json.loads((sidecars / "wake_words.json").read_text(encoding="utf-8")) == [
        {"slug": "domovoi_v1", "threshold": None}, {"slug": "porch_cat", "threshold": 0.62}]
    assert done == ["sync", "restart"]
    # An older core's one-word push: back to one word.
    sat._apply_wake_switch("hey_jarvis", None)
    assert not (sidecars / "wake_words.json").exists()
    assert [w["slug"] for w in client._effective_wake_words(_cfg())] == ["hey_jarvis"]


def test_the_set_wake_word_frame_carries_the_list(monkeypatch):
    sat, loop = _sat(monkeypatch, "none", [])
    loop.close()
    got = []
    monkeypatch.setattr(sat, "_held", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(sat, "_apply_wake_switch",
                        lambda slug, threshold=None, words=None: got.append((slug, threshold, words)))

    class _Now:
        def __init__(self, target, args=(), kwargs=None, **_):
            self.go = lambda: target(*args, **(kwargs or {}))

        def start(self):
            self.go()

    monkeypatch.setattr(client.threading, "Thread", _Now)
    sat._handle_text_frame({"type": "set_wake_word", "slug": "domovoi_v1", "words": [
        {"slug": "domovoi_v1"}, {"slug": "porch_cat", "threshold": 0.6}]})
    sat._handle_text_frame({"type": "set_wake_word", "slug": "hey_jarvis"})
    assert got == [
        ("domovoi_v1", None, [{"slug": "domovoi_v1", "threshold": None},
                              {"slug": "porch_cat", "threshold": 0.6}]),
        ("hey_jarvis", None, None),
    ]


def test_a_set_wake_word_slug_that_is_not_a_slug_is_ignored(monkeypatch):
    sat, loop = _sat(monkeypatch, "none", [])
    loop.close()
    got = []
    monkeypatch.setattr(sat, "_held", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(sat, "_apply_wake_switch",
                        lambda slug, threshold=None, words=None: got.append(slug))
    monkeypatch.setattr(client.threading, "Thread",
                        lambda target, args=(), kwargs=None, **_: types.SimpleNamespace(
                            start=lambda: target(*args, **(kwargs or {}))))
    for bad in ("../outside", "a/b", "a\\b", "x" * 81, ".hidden"):
        sat._handle_text_frame({"type": "set_wake_word", "slug": bad})
    assert got == []


def test_a_saved_slug_that_is_not_a_slug_falls_back_to_the_config(sidecars):
    (sidecars / "wake").write_text("../../elsewhere\n", encoding="utf-8")
    assert client._effective_wake_word(_cfg()) == "hey_jarvis"
    assert client._effective_wake_model_path(_cfg()) is None
    (sidecars / "wake").write_text("porch_cat\n", encoding="utf-8")
    (sidecars / "wake_models" / "porch_cat.onnx").write_bytes(b"onnx")
    assert client._effective_wake_word(_cfg()) == "porch_cat"
    assert client._effective_wake_model_path(_cfg()).endswith("porch_cat.onnx")


def test_extra_words_cost_little(monkeypatch):
    """The front end is shared: an extra word is one more small classifier
    (measured 2026-11-16: 1.79 ms front end vs 0.012-0.026 ms per word per
    80 ms chunk on the dev PC). The real model, when openWakeWord is here."""
    oww = pytest.importorskip("openwakeword.model")
    try:
        one = oww.Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        three = oww.Model(wakeword_models=["hey_jarvis", "alexa", "hey_mycroft"],
                          inference_framework="onnx")
    except Exception as e:  # noqa: BLE001 - its stock models aren't downloaded here
        pytest.skip(f"openWakeWord's stock models aren't here: {e}")
    chunk = np.zeros(1280, dtype=np.int16)
    assert set(three.predict(chunk)) == {"hey_jarvis", "alexa", "hey_mycroft"}
    assert set(one.predict(chunk)) == {"hey_jarvis"}
