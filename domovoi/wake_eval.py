"""Offline wake-word scorer — the trustworthy eval harness.

openWakeWord is a STREAMING detector: `predict()` consumes 80 ms / 1280-sample
frames, keeps internal state across calls, and a clip's real score is the
**max over all its frames**. A common mistake is to score a clip single-shot
(one window, no sliding, state reset) — which returns ~0 even for a clip the
deployed streaming detector would fire on. That single-shot vs. streaming
apples-to-oranges is the leading suspect for the observed "0/30 real clips
score ~0.001": the model may be fine and the *harness* wrong.

This module scores clips the way the Pi actually runs the model: reset state
per clip, feed 1280-sample frames in order, take the max. It scores the raw
clip AND the auto-trimmed/end-aligned copy (so we can see whether alignment,
not the model, was the problem), plus a silence clip and any held-out
synthetics — all through ONE identical path.

The frame-slicing / max logic (`slide_max_score`) is a pure function taking a
``predict_fn`` callable, so it's unit-testable with a fake predictor and CI
never needs openWakeWord installed. Only :func:`load_model` imports it, and it
degrades gracefully (raises :class:`WakeEvalUnavailable`) when the package or
the trained model isn't present — onnx inference runs on Windows even though
*training* is Linux-only.
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import Any, Callable

import numpy as np

from domovoi import wake_clip_quality as wq
from domovoi.config import settings

log = logging.getLogger(__name__)

FRAME = 1280            # openWakeWord's frame size (80 ms @ 16 kHz)
SAMPLE_RATE = 16_000
DEFAULT_THRESHOLD = 0.5

PredictFn = Callable[[np.ndarray], float]


class WakeEvalUnavailable(RuntimeError):
    """openWakeWord isn't installed, or the trained model isn't on disk."""


# ─── Pure scoring core (no openWakeWord) ──────────────────────────────────


def iter_frames(pcm_int16: np.ndarray, frame: int = FRAME):
    """Yield fixed-size ``frame``-sample int16 chunks in order; the final short
    chunk is zero-padded so a phrase sitting at the very end (end-aligned) is
    still presented as a full frame."""
    n = len(pcm_int16)
    if n == 0:
        return
    i = 0
    while i < n:
        chunk = pcm_int16[i : i + frame]
        if len(chunk) < frame:
            chunk = np.concatenate(
                [chunk, np.zeros(frame - len(chunk), dtype=pcm_int16.dtype)]
            )
        yield chunk
        i += frame


def slide_max_score(
    predict_fn: PredictFn, pcm_int16: np.ndarray, *, frame: int = FRAME
) -> float:
    """Feed ``pcm_int16`` through ``predict_fn`` one ``frame`` at a time (in
    order — streaming state lives inside the predictor) and return the max
    per-frame score. Empty input → 0.0."""
    best = 0.0
    for chunk in iter_frames(pcm_int16, frame):
        best = max(best, float(predict_fn(chunk)))
    return best


# ─── openWakeWord wrapper (graceful) ──────────────────────────────────────


def _read_int16(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
    if not raw:
        return np.zeros(0, dtype=np.int16)
    return np.frombuffer(raw, dtype=np.int16).copy()


def _resolve_model_path(slug: str, model_ref: str | None) -> Path:
    if model_ref:
        cand = Path(model_ref)
        if cand.is_file():
            return cand
    return Path(settings.wake_models_dir) / f"{slug}.onnx"


class LoadedModel:
    """A loaded openWakeWord model + the prediction-dict key (the slug)."""

    def __init__(self, model: Any, key: str) -> None:
        self._model = model
        self.key = key

    def reset(self) -> None:
        reset = getattr(self._model, "reset", None)
        if callable(reset):
            reset()

    def predict_fn(self) -> PredictFn:
        key = self.key

        def _fn(chunk_int16: np.ndarray) -> float:
            scores = self._model.predict(chunk_int16)
            if isinstance(scores, dict):
                if key in scores:
                    return float(scores[key])
                return float(max(scores.values())) if scores else 0.0
            return float(scores)

        return _fn


def load_model(slug: str, *, model_ref: str | None = None) -> LoadedModel:
    """Load the trained ``<slug>.onnx`` for offline scoring. Raises
    :class:`WakeEvalUnavailable` if openWakeWord isn't installed or the model
    file is missing."""
    try:
        from openwakeword.model import Model  # type: ignore
    except Exception as e:  # ImportError, or a broken optional install
        raise WakeEvalUnavailable(
            "openwakeword is not installed (pip install openwakeword)"
        ) from e
    model_path = _resolve_model_path(slug, model_ref)
    if not model_path.is_file():
        raise WakeEvalUnavailable(f"trained model not found: {model_path}")
    try:
        model = Model(
            wakeword_models=[str(model_path)], inference_framework="onnx"
        )
    except Exception as e:
        raise WakeEvalUnavailable(f"could not load model {model_path}: {e}") from e
    return LoadedModel(model, slug)


# ─── Clip / directory scoring ─────────────────────────────────────────────


def score_pcm(model: LoadedModel, pcm_int16: np.ndarray) -> float:
    """Max-over-clip score for one clip's int16 PCM (fresh streaming state)."""
    model.reset()
    return slide_max_score(model.predict_fn(), pcm_int16)


def score_clip(model: LoadedModel, wav_path: Path) -> float:
    return score_pcm(model, _read_int16(Path(wav_path)))


def silence_score(model: LoadedModel, *, seconds: float = 2.0) -> float:
    return score_pcm(model, np.zeros(int(seconds * SAMPLE_RATE), dtype=np.int16))


def score_dir(model: LoadedModel, slug_dir: Path) -> list[dict[str, Any]]:
    """Score every raw clip AND its auto-trimmed copy in a slug dir. Returns
    ``[{name, raw_score, trimmed_score|None, selected}]`` in clip order."""
    slug_dir = Path(slug_dir)
    out: list[dict[str, Any]] = []
    for clip in wq.iter_raw_clips(slug_dir):
        try:
            rec = wq.ensure_analysis(clip)
        except FileNotFoundError:
            continue
        raw_score = score_clip(model, clip)
        trimmed_score = None
        tpath = wq.trimmed_path(clip)
        if rec.get("has_trimmed") and tpath.is_file():
            trimmed_score = score_clip(model, tpath)
        out.append(
            {
                "name": clip.name,
                "raw_score": round(raw_score, 4),
                "trimmed_score": round(trimmed_score, 4) if trimmed_score is not None else None,
                "selected": bool(rec.get("selected")),
            }
        )
    return out


def summarize(
    clip_scores: list[dict[str, Any]],
    *,
    threshold: float,
    silence: float | None = None,
    synthetic_scores: list[float] | None = None,
) -> dict[str, Any]:
    """Roll up per-clip scores into the numbers that decide the harness-vs-model
    question: real recall@threshold on raw and trimmed, the silence score, and
    (if provided) synthetic-through-this-harness recall — the sanity check."""
    n = len(clip_scores) or 1
    raw_hits = sum(1 for c in clip_scores if c["raw_score"] >= threshold)
    trimmed_vals = [c["trimmed_score"] for c in clip_scores if c["trimmed_score"] is not None]
    trimmed_hits = sum(1 for v in trimmed_vals if v >= threshold)
    summary: dict[str, Any] = {
        "threshold": threshold,
        "n_clips": len(clip_scores),
        "raw_recall": round(raw_hits / n, 3),
        "raw_max": round(max((c["raw_score"] for c in clip_scores), default=0.0), 4),
        "trimmed_recall": round(trimmed_hits / (len(trimmed_vals) or 1), 3) if trimmed_vals else None,
        "trimmed_max": round(max(trimmed_vals, default=0.0), 4) if trimmed_vals else None,
        "silence_score": round(silence, 4) if silence is not None else None,
    }
    if synthetic_scores:
        sn = len(synthetic_scores)
        summary["synthetic_recall"] = round(
            sum(1 for v in synthetic_scores if v >= threshold) / sn, 3
        )
        summary["synthetic_max"] = round(max(synthetic_scores), 4)
    return summary
