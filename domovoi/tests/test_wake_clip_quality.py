"""Wake-clip quality/trim/curation — pure unit tests (no DB, no openwakeword).

Synthesizes clips with known acoustics and asserts metrics, verdict, trim
bounds, envelope, idempotency, user-selection preservation, and the
selected-only training staging.
"""

from __future__ import annotations

import numpy as np
import pytest

from domovoi import wake_clip_quality as wq

SR = wq.SAMPLE_RATE


def _tone(amp: float, dur_s: float) -> np.ndarray:
    t = np.arange(int(dur_s * SR))
    return amp * np.sin(2 * np.pi * 220.0 * t / SR)


def _clip(*, phrase_amp: float, phrase_s: float, lead_s: float, total_s: float,
          noise_std: float = 30.0, seed: int = 0) -> np.ndarray:
    """A clip: quiet noise everywhere + a tone 'phrase' placed after lead_s."""
    rng = np.random.default_rng(seed)
    n = int(total_s * SR)
    sig = rng.normal(0.0, noise_std, n).astype(np.float32)
    if phrase_amp > 0 and phrase_s > 0:
        start = int(lead_s * SR)
        tone = _tone(phrase_amp, phrase_s)
        end = min(n, start + len(tone))
        sig[start:end] += tone[: end - start]
    return sig


def _write(tmp_path, name: str, samples: np.ndarray):
    p = tmp_path / name
    wq._write_wav_int16(p, samples)
    return p


# ─── analyze_pcm: verdicts ─────────────────────────────────────────────────


def test_good_clip():
    a = wq.analyze_pcm(_clip(phrase_amp=9830, phrase_s=0.6, lead_s=0.7, total_s=2.0))
    assert a["verdict"] == "good"
    assert a["issues"] == []
    assert a["metrics"]["snr_db"] >= wq._GOOD_SNR_DB
    assert a["metrics"]["clipping_pct"] == 0.0
    assert a["has_trimmed"] is True
    assert len(a["envelope"]) == wq.ENVELOPE_BINS


def test_silent_clip_is_no_speech():
    a = wq.analyze_pcm(_clip(phrase_amp=0, phrase_s=0, lead_s=0, total_s=2.0, noise_std=2.0))
    assert a["verdict"] == "poor"
    assert "no_speech" in a["issues"]
    assert a["has_trimmed"] is False


def test_clipped_clip_is_poor():
    # A very loud tone hard-clips on write to int16 → lots of full-scale samples.
    a = wq.analyze_pcm(_clip(phrase_amp=60000, phrase_s=0.6, lead_s=0.7, total_s=2.0))
    # analyze_pcm sees the raw float, but write/read clips it; emulate by clipping.
    clipped = np.clip(_clip(phrase_amp=60000, phrase_s=0.6, lead_s=0.7, total_s=2.0), -32768, 32767)
    a = wq.analyze_pcm(clipped)
    assert a["metrics"]["clipping_pct"] > wq._CLIP_POOR_PCT
    assert a["verdict"] == "poor"
    assert "clipping" in a["issues"]


def test_quiet_clip_is_fair_too_quiet():
    a = wq.analyze_pcm(_clip(phrase_amp=300, phrase_s=0.5, lead_s=0.6, total_s=2.0))
    assert a["verdict"] == "fair"
    assert "too_quiet" in a["issues"]


# ─── Trim bounds (end-aligned) ─────────────────────────────────────────────


def test_trim_isolates_phrase_and_end_aligns():
    a = wq.analyze_pcm(_clip(phrase_amp=9830, phrase_s=0.6, lead_s=0.7, total_s=2.0))
    trim = a["trim"]
    # Phrase is 0.7s..1.3s → trim starts a touch before 0.7s, ends a touch after 1.3s.
    assert 0.55 * SR <= trim["start_sample"] <= 0.7 * SR
    assert 1.3 * SR <= trim["end_sample"] <= 1.45 * SR
    # End-aligned: little trailing silence left after trim end.
    assert (2.0 * SR - trim["end_sample"]) / SR * 1000 > 400  # raw had ~700ms trailing
    assert 500 <= a["trimmed_duration_ms"] <= 900


# ─── Sidecar persistence + idempotency + selection ─────────────────────────


def test_ensure_analysis_writes_sidecar_and_trimmed(tmp_path):
    raw = _write(tmp_path, "clip_001.wav", _clip(phrase_amp=9830, phrase_s=0.6, lead_s=0.7, total_s=2.0))
    rec = wq.ensure_analysis(raw)
    assert rec["verdict"] == "good"
    assert rec["selected"] is True and rec["selected_source"] == "auto"
    assert wq._sidecar_path(raw).is_file()
    assert wq.trimmed_path(raw).is_file()


def test_ensure_analysis_idempotent(tmp_path):
    raw = _write(tmp_path, "clip_001.wav", _clip(phrase_amp=9830, phrase_s=0.6, lead_s=0.7, total_s=2.0))
    a = wq.ensure_analysis(raw)
    b = wq.ensure_analysis(raw)
    assert a == b


def test_poor_clip_auto_deselected(tmp_path):
    raw = _write(tmp_path, "clip_001.wav", _clip(phrase_amp=0, phrase_s=0, lead_s=0, total_s=2.0, noise_std=2.0))
    rec = wq.ensure_analysis(raw)
    assert rec["verdict"] == "poor"
    assert rec["selected"] is False and rec["selected_source"] == "auto"


def test_user_selection_survives_recompute(tmp_path):
    raw = _write(tmp_path, "clip_001.wav", _clip(phrase_amp=9830, phrase_s=0.6, lead_s=0.7, total_s=2.0))
    wq.ensure_analysis(raw)
    wq.set_selected(raw, False)
    rec = wq.ensure_analysis(raw, force=True)  # recompute metrics
    assert rec["selected"] is False
    assert rec["selected_source"] == "user"


def test_remove_analysis(tmp_path):
    raw = _write(tmp_path, "clip_001.wav", _clip(phrase_amp=9830, phrase_s=0.6, lead_s=0.7, total_s=2.0))
    wq.ensure_analysis(raw)
    wq.remove_analysis(raw)
    assert not wq._sidecar_path(raw).is_file()
    assert not wq.trimmed_path(raw).is_file()


# ─── Directory staging (selected-only) ─────────────────────────────────────


def test_stage_selected_clips(tmp_path):
    slug = tmp_path / "hey_domovoi"
    slug.mkdir()
    _write(slug, "clip_001.wav", _clip(phrase_amp=9830, phrase_s=0.6, lead_s=0.7, total_s=2.0))
    _write(slug, "clip_002.wav", _clip(phrase_amp=9830, phrase_s=0.6, lead_s=0.7, total_s=2.0, seed=1))
    _write(slug, "clip_003.wav", _clip(phrase_amp=0, phrase_s=0, lead_s=0, total_s=2.0, noise_std=2.0))  # poor

    assert wq.selected_count(slug) == 2  # the poor one auto-deselected
    training_dir, count = wq.stage_selected_clips(slug)
    assert count == 2
    staged = sorted(p.name for p in training_dir.glob("*.wav"))
    assert staged == ["clip_001.wav", "clip_002.wav"]

    # Re-include the poor clip → staging now carries all three.
    wq.set_selected(slug / "clip_003.wav", True)
    _, count2 = wq.stage_selected_clips(slug)
    assert count2 == 3


def test_iter_raw_clips_ignores_subdirs(tmp_path):
    slug = tmp_path / "hey_domovoi"
    slug.mkdir()
    _write(slug, "clip_001.wav", _clip(phrase_amp=9830, phrase_s=0.6, lead_s=0.7, total_s=2.0))
    wq.ensure_analysis(slug / "clip_001.wav")  # creates .analysis/clip_001.trimmed.wav
    wq.stage_selected_clips(slug)              # creates .training/clip_001.wav
    names = [p.name for p in wq.iter_raw_clips(slug)]
    assert names == ["clip_001.wav"]  # subdir WAVs are NOT part of the raw roster
