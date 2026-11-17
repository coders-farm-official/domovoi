"""The trainer's optional extra material (scripts/wake_word/docker/entrypoint.py):
more near-miss phrases, extra synthetic clips, room echoes and noise beds,
and the switches that leave out the inputs with weak licences. None of it
is a household recording. No Docker needed: the entrypoint's helpers.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import soundfile as sf

DOCKER_DIR = Path(__file__).resolve().parents[2] / "scripts" / "wake_word" / "docker"


def _entrypoint():
    spec = importlib.util.spec_from_file_location("_wake_entrypoint_x", DOCKER_DIR / "entrypoint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ep = _entrypoint()


def _wav(path: Path, rate: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.zeros(rate // 10, dtype="float32"), rate, subtype="PCM_16")
    return path


def test_extra_negative_phrases_are_cleaned():
    got = ep.extra_negative_phrases("Domino; the  movie\n;do move;domino;Domovoi;bad\"quote;", "domovoi")
    assert got == ["domino", "the movie", "do move"]


def test_the_config_carries_the_extra_negatives(monkeypatch, tmp_path):
    template = tmp_path / "t.yaml"
    template.write_text("custom_negative_phrases: __CUSTOM_NEGATIVES__\n"
                        "rir_paths: __RIR_PATHS__\nbackground_paths: __BACKGROUND_PATHS__\n"
                        "background_paths_duplication_rate: __BACKGROUND_DUP__\n", encoding="utf-8")
    monkeypatch.setattr(ep, "TEMPLATE", template)
    monkeypatch.setattr(ep, "near_misses", lambda phrase: ["dom"])
    monkeypatch.setenv("WAKE_EXTRA_NEGATIVES", "domino;dom;the movie")
    monkeypatch.setattr(ep, "EXTRA_RIRS_IN", tmp_path / "none")
    monkeypatch.setattr(ep, "EXTRA_BACKGROUND_IN", tmp_path / "none")
    text = ep.render_config("domovoi", "domovoi", "cpu", tmp_path).read_text(encoding="utf-8")
    assert 'custom_negative_phrases: ["dom", "domino", "the movie"]' in text
    assert f"rir_paths: {json.dumps([str(ep.RIR_DIR)])}" in text
    assert "background_paths_duplication_rate: [1]" in text
    real = (DOCKER_DIR / "train_config.template.yaml").read_text(encoding="utf-8")
    for key in ("__RIR_PATHS__", "__BACKGROUND_PATHS__", "__BACKGROUND_DUP__"):
        assert key in real


def test_extra_rirs_and_backgrounds_are_added_and_mit_left_out_only_on_request(monkeypatch, tmp_path):
    rirs, bg = tmp_path / "rirs", tmp_path / "bg"
    _wav(rirs / "a" / "r1.wav")
    _wav(bg / "b1.wav")
    monkeypatch.setattr(ep, "EXTRA_RIRS_IN", rirs)
    monkeypatch.setattr(ep, "EXTRA_BACKGROUND_IN", bg)
    r, b = ep.augmentation_dirs()
    assert r == [str(ep.RIR_DIR), str(rirs / "a")] and b == [str(ep.BACKGROUND_DIR), str(bg)]
    monkeypatch.setenv("WAKE_SKIP_MIT_RIRS", "1")
    assert ep.skip_mit_rirs()
    assert ep.augmentation_dirs()[0] == [str(rirs / "a")]
    # Asked to skip but no replacement: the MIT ones stay.
    monkeypatch.setattr(ep, "EXTRA_RIRS_IN", tmp_path / "empty")
    assert not ep.skip_mit_rirs()
    assert ep.augmentation_dirs()[0] == [str(ep.RIR_DIR)]


def test_extra_clips_are_copied_and_a_changed_set_recomputes_features(monkeypatch, tmp_path):
    pos, neg = tmp_path / "pos", tmp_path / "neg"
    _wav(pos / "v1" / "p1.wav", 22050)
    _wav(pos / "v1" / "p2.wav")
    _wav(neg / "n1.wav")
    monkeypatch.setattr(ep, "EXTRA_POS_IN", pos)
    monkeypatch.setattr(ep, "EXTRA_NEG_IN", neg)
    model_dir = tmp_path / "run" / "domovoi"
    (model_dir / "positive_train").mkdir(parents=True)
    (model_dir / "positive_train" / "synth_1.wav").write_bytes(b"x")
    for name in ep.FEATURE_FILES:
        (model_dir / name).write_bytes(b"f")
    assert ep.inject_extra_clips(model_dir) == (2, 1)
    assert not any((model_dir / n).exists() for n in ep.FEATURE_FILES)
    copied = sorted(p.name for p in (model_dir / "positive_train").glob("extra_*.wav"))
    assert copied == ["extra_000000.wav", "extra_000001.wav"]
    assert sf.info(str(model_dir / "positive_train" / "extra_000000.wav")).samplerate == 16000
    assert (model_dir / "positive_train" / "synth_1.wav").exists()
    ep.mark_extra_current(model_dir)
    # Same set again: the features (recomputed by augment) are kept.
    for name in ep.FEATURE_FILES:
        (model_dir / name).write_bytes(b"f")
    ep.inject_extra_clips(model_dir)
    assert all((model_dir / n).exists() for n in ep.FEATURE_FILES)
    # A changed set: computed again.
    _wav(neg / "n2.wav")
    ep.inject_extra_clips(model_dir)
    assert not any((model_dir / n).exists() for n in ep.FEATURE_FILES)
    ep.drop_extra_copies(model_dir)
    assert not list(model_dir.rglob("extra_*.wav"))


def test_extras_are_used_even_for_a_synthetic_only_word(monkeypatch, tmp_path):
    """They are synthetic, not recordings: WAKE_SYNTHETIC_ONLY keeps them."""
    pos = tmp_path / "pos"
    _wav(pos / "p1.wav")
    monkeypatch.setattr(ep, "EXTRA_POS_IN", pos)
    monkeypatch.setattr(ep, "EXTRA_NEG_IN", tmp_path / "none")
    monkeypatch.setenv("WAKE_SYNTHETIC_ONLY", "1")
    assert ep.inject_extra_clips(tmp_path / "run") == (1, 0)


def test_no_extras_and_none_before_touches_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(ep, "EXTRA_POS_IN", tmp_path / "none")
    monkeypatch.setattr(ep, "EXTRA_NEG_IN", tmp_path / "none")
    model_dir = tmp_path / "run"
    model_dir.mkdir()
    for name in ep.FEATURE_FILES:
        (model_dir / name).write_bytes(b"f")
    assert ep.inject_extra_clips(model_dir) == (0, 0)
    assert all((model_dir / n).exists() for n in ep.FEATURE_FILES)


def test_a_custom_validation_file_replaces_the_datasets(monkeypatch, tmp_path):
    template = tmp_path / "t.yaml"
    template.write_text("false_positive_validation_data_path: __FP_VAL_NPY__\n", encoding="utf-8")
    monkeypatch.setattr(ep, "TEMPLATE", template)
    monkeypatch.setattr(ep, "near_misses", lambda phrase: [])
    own = tmp_path / "val.npy"
    own.write_bytes(b"npy")
    monkeypatch.setenv("WAKE_FP_VAL_NPY", str(own))
    text = ep.render_config("domovoi", "domovoi", "cpu", tmp_path).read_text(encoding="utf-8")
    assert f"false_positive_validation_data_path: {own}" in text
