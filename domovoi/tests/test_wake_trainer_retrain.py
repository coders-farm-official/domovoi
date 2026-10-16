"""A retrain must train on the clips recorded since the last run.

The trainer keeps one run dir per slug on its cache volume, and train.py's
--augment_clips stage does nothing when ``positive_features_train.npy``
already exists. So before this fix, recording more clips and pressing Train
again reused the first run's features: the new clips were copied into
positive_train and then never used. The entrypoint
(``scripts/wake_word/docker/entrypoint.py``) now records which real clip set
the features were computed from and drops them when that set changes.
No Docker needed: these are the entrypoint's file-level helpers.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

DOCKER_DIR = Path(__file__).resolve().parents[2] / "scripts" / "wake_word" / "docker"


def _entrypoint():
    spec = importlib.util.spec_from_file_location("_wake_entrypoint", DOCKER_DIR / "entrypoint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ep = _entrypoint()


def _clips(root: Path, names: dict[str, bytes]) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    for name, body in names.items():
        (root / name).write_bytes(body)
    return sorted(root.glob("*.wav"))


def _run_dir(root: Path) -> Path:
    model_dir = root / "hey_domovoi"
    (model_dir / "positive_train").mkdir(parents=True)
    (model_dir / "positive_train" / "synth_0001.wav").write_bytes(b"synthetic")
    (model_dir / "positive_train" / "real_0000_000.wav").write_bytes(b"old copy")
    for name in ep.FEATURE_FILES:
        (model_dir / name).write_bytes(b"features")
    return model_dir


def test_features_from_an_unrecorded_clip_set_are_dropped(tmp_path):
    """A run dir from before this fix has features and no record of what
    they were computed from: compute them again."""
    model_dir = _run_dir(tmp_path / "run")
    wavs = _clips(tmp_path / "clips", {"clip_001.wav": b"a"})

    assert ep.reset_for_real_clips(model_dir, wavs) is True
    assert not any((model_dir / n).exists() for n in ep.FEATURE_FILES)
    # Old injected copies go; synthetic clips stay.
    assert not (model_dir / "positive_train" / "real_0000_000.wav").exists()
    assert (model_dir / "positive_train" / "synth_0001.wav").exists()


def test_same_clips_keep_the_features_more_clips_drop_them(tmp_path):
    model_dir = _run_dir(tmp_path / "run")
    clips = tmp_path / "clips"
    wavs = _clips(clips, {"clip_001.wav": b"a", "clip_002.wav": b"b"})
    ep.mark_features_current(model_dir, wavs)

    # Train again with nothing new: augmentation can be skipped.
    assert ep.reset_for_real_clips(model_dir, wavs) is False
    assert all((model_dir / n).exists() for n in ep.FEATURE_FILES)

    # Record one more clip, train again: features must be recomputed.
    wavs = _clips(clips, {"clip_003.wav": b"c"})
    assert ep.reset_for_real_clips(model_dir, wavs) is True
    assert not any((model_dir / n).exists() for n in ep.FEATURE_FILES)
    assert not (model_dir / ep.REAL_FINGERPRINT).exists()


def test_a_re_recorded_clip_with_the_same_name_counts_as_a_change(tmp_path):
    model_dir = _run_dir(tmp_path / "run")
    clips = tmp_path / "clips"
    wavs = _clips(clips, {"clip_001.wav": b"first take"})
    ep.mark_features_current(model_dir, wavs)
    wavs = _clips(clips, {"clip_001.wav": b"second take"})
    assert ep.reset_for_real_clips(model_dir, wavs) is True


def test_a_failed_augmentation_is_not_trusted_next_time(tmp_path):
    """The record is written only after --augment_clips succeeds, and
    dropped with the features, so half-written features from a crashed
    stage are recomputed even with the same clips."""
    model_dir = _run_dir(tmp_path / "run")
    wavs = _clips(tmp_path / "clips", {"clip_001.wav": b"a"})
    assert ep.reset_for_real_clips(model_dir, wavs) is True  # no record yet
    for name in ep.FEATURE_FILES:  # the crashed stage left partial files
        (model_dir / name).write_bytes(b"partial")
    assert ep.reset_for_real_clips(model_dir, wavs) is True
    assert not any((model_dir / n).exists() for n in ep.FEATURE_FILES)


def test_order_of_the_clip_list_does_not_matter(tmp_path):
    wavs = _clips(tmp_path / "clips", {"a.wav": b"1", "b.wav": b"2"})
    assert ep.real_clip_fingerprint(wavs) == ep.real_clip_fingerprint(list(reversed(wavs)))
    assert ep.real_clip_fingerprint([]) != ep.real_clip_fingerprint(wavs)


def test_main_records_the_clip_set_only_after_augmentation(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("WAKE_SLUG", "hey_domovoi")
    monkeypatch.setenv("WAKE_PHRASE", "hey domovoi")
    monkeypatch.setattr(ep, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(ep, "CLIPS_IN", tmp_path / "no-clips")
    monkeypatch.setattr(ep, "detect_device", lambda: "cpu")
    for name in ("ensure_dataset", "ensure_rirs", "ensure_background"):
        monkeypatch.setattr(ep, name, lambda: None)
    monkeypatch.setattr(ep, "render_config", lambda *a: tmp_path / "cfg.yaml")
    monkeypatch.setattr(ep, "export_model", lambda *a: calls.append("export"))
    monkeypatch.setattr(ep, "inject_real_clips", lambda *a: calls.append("inject") or 0)
    monkeypatch.setattr(ep, "mark_features_current", lambda *a: calls.append("mark"))
    monkeypatch.setattr(ep, "run_stage", lambda cfg, flag, dev: calls.append(flag))

    assert ep.main() == 0
    assert calls == ["--generate_clips", "inject", "--augment_clips", "mark",
                     "--train_model", "export"]


def test_no_copy_of_the_recordings_stays_in_the_cache(monkeypatch, tmp_path):
    """The copies of the household's clips go at the end of every run, failed
    or not; the synthetic clips and the computed features stay."""
    monkeypatch.setenv("WAKE_SLUG", "hey_domovoi")
    monkeypatch.setenv("WAKE_PHRASE", "hey domovoi")
    monkeypatch.setattr(ep, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(ep, "CLIPS_IN", tmp_path / "no-clips")
    monkeypatch.setattr(ep, "detect_device", lambda: "cpu")
    monkeypatch.setattr(ep, "lower_priority", lambda: None)
    for name in ("ensure_dataset", "ensure_rirs", "ensure_background"):
        monkeypatch.setattr(ep, name, lambda: None)
    monkeypatch.setattr(ep, "render_config", lambda *a: tmp_path / "cfg.yaml")
    pos = tmp_path / "cache" / "runs" / "hey_domovoi" / "hey_domovoi" / "positive_train"

    def inject(work, slug):
        pos.mkdir(parents=True, exist_ok=True)
        (pos / "real_0000_000.wav").write_bytes(b"voice")
        (pos / "clip_1.wav").write_bytes(b"synthetic")
        return 1

    monkeypatch.setattr(ep, "inject_real_clips", inject)
    monkeypatch.setattr(ep, "mark_features_current", lambda *a: None)

    def fail_training(cfg, flag, dev):
        if flag == "--train_model":
            raise SystemExit(1)

    monkeypatch.setattr(ep, "run_stage", fail_training)
    with pytest.raises(SystemExit):
        ep.main()
    assert not list(pos.glob("real_*.wav"))
    assert (pos / "clip_1.wav").is_file()
