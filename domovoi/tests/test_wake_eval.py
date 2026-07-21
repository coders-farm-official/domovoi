"""Offline scorer — pure tests of the sliding max-over-clip logic with a fake
predictor (openWakeWord is NOT imported here). Guards the harness that
disambiguates the '0/30 real clips' result."""

from __future__ import annotations

import numpy as np

from domovoi import wake_eval


def test_iter_frames_pads_final_partial_frame():
    pcm = np.arange(1280 + 500, dtype=np.int16)
    frames = list(wake_eval.iter_frames(pcm, frame=1280))
    assert len(frames) == 2
    assert all(len(f) == 1280 for f in frames)
    # The tail was zero-padded.
    assert frames[1][500:].sum() == 0
    assert frames[1][0] == 1280  # first sample of the second frame


def test_iter_frames_empty():
    assert list(wake_eval.iter_frames(np.zeros(0, dtype=np.int16))) == []


def test_slide_max_takes_the_peak_frame():
    # A predictor that returns the peak-normalized energy of each frame — the
    # score should be the max across frames, not the first/last.
    def predict(chunk):
        return float(np.abs(chunk).max()) / 32768.0

    pcm = np.zeros(1280 * 4, dtype=np.int16)
    pcm[1280 * 2 : 1280 * 3] = 16384  # loud only in the 3rd frame
    assert abs(wake_eval.slide_max_score(predict, pcm) - 0.5) < 1e-6


def test_slide_max_streaming_state_is_per_frame_in_order():
    seen = []

    def predict(chunk):
        seen.append(int(chunk[0]))
        return 0.1 * len(seen)

    pcm = np.zeros(1280 * 3, dtype=np.int16)
    for i in range(3):
        pcm[i * 1280] = i + 1
    score = wake_eval.slide_max_score(predict, pcm)
    assert seen == [1, 2, 3]            # frames fed in order
    assert abs(score - 0.3) < 1e-9      # max of 0.1, 0.2, 0.3


def test_slide_max_empty_is_zero():
    assert wake_eval.slide_max_score(lambda c: 1.0, np.zeros(0, dtype=np.int16)) == 0.0


def test_loaded_model_predict_fn_extracts_key():
    class FakeModel:
        def predict(self, chunk):
            return {"hey_domovoi": 0.87, "other": 0.99}

    lm = wake_eval.LoadedModel(FakeModel(), "hey_domovoi")
    fn = lm.predict_fn()
    assert abs(fn(np.zeros(1280, dtype=np.int16)) - 0.87) < 1e-9


def test_loaded_model_predict_fn_falls_back_to_max():
    class FakeModel:
        def predict(self, chunk):
            return {"unrelated": 0.42}

    lm = wake_eval.LoadedModel(FakeModel(), "missing_key")
    fn = lm.predict_fn()
    assert abs(fn(np.zeros(1280, dtype=np.int16)) - 0.42) < 1e-9


def test_score_pcm_resets_between_clips():
    class FakeModel:
        def __init__(self):
            self.resets = 0

        def reset(self):
            self.resets += 1

        def predict(self, chunk):
            return {"k": 0.5}

    fm = FakeModel()
    lm = wake_eval.LoadedModel(fm, "k")
    wake_eval.score_pcm(lm, np.zeros(1280 * 2, dtype=np.int16))
    wake_eval.score_pcm(lm, np.zeros(1280, dtype=np.int16))
    assert fm.resets == 2


def test_summarize_recall_and_sanity():
    clips = [
        {"name": "clip_001.wav", "raw_score": 0.9, "trimmed_score": 0.95, "selected": True},
        {"name": "clip_002.wav", "raw_score": 0.1, "trimmed_score": 0.8, "selected": True},
    ]
    s = wake_eval.summarize(
        clips, threshold=0.5, silence=0.001, synthetic_scores=[0.9, 0.8, 0.2]
    )
    assert s["n_clips"] == 2
    assert s["raw_recall"] == 0.5          # 1 of 2 raw ≥ 0.5
    assert s["trimmed_recall"] == 1.0      # both trimmed ≥ 0.5
    assert s["silence_score"] == 0.001
    assert s["synthetic_recall"] == round(2 / 3, 3)
