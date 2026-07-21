"""Wake-word clip quality scoring + auto-trim + curation.

Positive wake-word training clips are recorded on a satellite and written as
bare ``<wake_clips_dir>/<slug>/clip_NNN.wav`` files (16 kHz mono int16, ~2 s).
Until now they were opaque — you couldn't tell a clean capture from one the
XVF3800's AGC gated, or where the phrase actually sits in the 2 s window.

This module makes each clip inspectable and curatable, WITHOUT a DB migration:
for every raw clip it computes objective quality metrics + an energy-VAD trim,
and persists both in a hidden ``.analysis/`` subdir alongside the clip:

    <wake_clips_dir>/<slug>/
      clip_001.wav                 # raw (what the trainer + clip list see)
      .analysis/
        clip_001.json              # metrics, verdict, issues, trim, envelope, selected
        clip_001.trimmed.wav       # audit-only, end-aligned to the phrase
      .training/                   # rebuilt at train time from SELECTED raw clips

The ``.analysis``/``.training`` subdirs are invisible to the trainer's
top-level ``glob("*.wav")`` and to the web clip list's non-recursive
``iterdir()`` — so nothing here pollutes training input or the raw roster.

Deliberately dependency-light: numpy + stdlib ``wave`` only. No webrtcvad (it's
a pain to install on Windows and non-deterministic); energy-based VAD is plenty
for ~2 s single-phrase clips and is trivially unit-testable.

End-alignment: openWakeWord trains positives end-aligned (the phrase ends at
the window boundary). The trim keeps a small trailing pad so the trimmed clip
ends just after the phrase — matching that convention — while the raw clip is
left untouched.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import wave
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME_MS = 10
FRAME = SAMPLE_RATE * FRAME_MS // 1000           # 160 samples / frame
ENVELOPE_BINS = 48                               # sparkline resolution
ANALYSIS_VERSION = 1                             # bump to force a recompute
ANALYSIS_DIRNAME = ".analysis"
TRAINING_DIRNAME = ".training"

# int16 full scale. A sample within ~1% of it counts as clipped.
_FULL_SCALE = 32768.0
_CLIP_LEVEL = int(32767 * 0.99)

# Energy-VAD tuning.
_VOICE_MARGIN_DB = 8.0        # a frame is "voiced" this far above the noise floor
_ABS_FLOOR_DBFS = -50.0       # ...but never below this absolute level
_GAP_FRAMES = 8               # merge voiced spans separated by ≤ this many frames (80 ms)
_PREROLL_SAMPLES = int(0.080 * SAMPLE_RATE)   # 80 ms lead-in kept before the phrase
_TRAIL_SAMPLES = int(0.060 * SAMPLE_RATE)     # 60 ms tail kept after the phrase (end-align)
_DIGITAL_SILENCE_DBFS = -70.0                 # below this ≈ hard-zeroed (gating tell)

# Quality thresholds (documented, generous margins so the verdict is stable).
_GOOD_SNR_DB = 15.0
_POOR_SNR_DB = 6.0
_LOW_SNR_DB = 10.0
_CLIP_POOR_PCT = 2.0
_CLIP_WARN_PCT = 0.2
_QUIET_PEAK_DBFS = -30.0
_GOOD_PEAK_DBFS = -24.0
_MIN_SPEECH_MS = 120          # below this we call it "no speech"
_SHORT_SPEECH_MS = 200        # below this (but > no-speech) is "very short"
_LONG_SILENCE_MS = 1000


# ─── WAV IO (16 kHz mono int16) ────────────────────────────────────────────


def _read_wav_int16(path: Path) -> np.ndarray:
    """Read a mono int16 WAV → float32 array in [-1, 1). Empty on failure."""
    try:
        with wave.open(str(path), "rb") as w:
            n = w.getnframes()
            raw = w.readframes(n)
    except (wave.Error, EOFError, OSError) as e:
        log.warning("wake clip: unreadable WAV %s: %s", path, e)
        return np.zeros(0, dtype=np.float32)
    if not raw:
        return np.zeros(0, dtype=np.float32)
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    return pcm


def _write_wav_int16(path: Path, samples: np.ndarray) -> None:
    """Write a float/int array back as 16 kHz mono int16."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm16 = np.clip(np.rint(samples), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm16.tobytes())


# ─── Framewise energy ──────────────────────────────────────────────────────


def _frame_rms_lin(pcm: np.ndarray) -> np.ndarray:
    """Non-overlapping 10 ms frame RMS as a linear fraction of full scale."""
    n_frames = len(pcm) // FRAME
    if n_frames == 0:
        return np.zeros(0, dtype=np.float32)
    trimmed = pcm[: n_frames * FRAME].reshape(n_frames, FRAME) / _FULL_SCALE
    return np.sqrt(np.mean(trimmed * trimmed, axis=1) + 1e-12).astype(np.float32)


def _dbfs(lin: float) -> float:
    if lin <= 1e-9:
        return -120.0
    return 20.0 * math.log10(lin)


def _voiced_mask(frame_rms: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Return (voiced_bool_per_frame, noise_lin, thr_lin).

    Noise floor = 10th percentile of frame RMS (robust when most of a 2 s clip
    is silence around a short phrase). Voiced = margin-above-floor AND above an
    absolute floor. Small gaps between voiced runs are closed so a brief dip
    mid-phrase doesn't split it.
    """
    if len(frame_rms) == 0:
        return np.zeros(0, dtype=bool), 0.0, 0.0
    noise_lin = float(np.percentile(frame_rms, 10))
    noise_dbfs = _dbfs(noise_lin)
    thr_dbfs = max(noise_dbfs + _VOICE_MARGIN_DB, _ABS_FLOOR_DBFS)
    thr_lin = 10.0 ** (thr_dbfs / 20.0)
    voiced = frame_rms >= thr_lin
    # Close short gaps (dilate then erode over runs of False ≤ _GAP_FRAMES).
    voiced = _close_gaps(voiced, _GAP_FRAMES)
    return voiced, noise_lin, thr_lin


def _close_gaps(mask: np.ndarray, max_gap: int) -> np.ndarray:
    """Fill runs of False shorter than or equal to ``max_gap`` that sit between
    two True regions — merges a phrase briefly dipping under threshold."""
    if not mask.any():
        return mask
    out = mask.copy()
    idx = np.flatnonzero(mask)
    for a, b in zip(idx[:-1], idx[1:]):
        if 1 < (b - a) <= max_gap + 1:
            out[a + 1 : b] = True
    return out


# ─── Analysis ──────────────────────────────────────────────────────────────


def _envelope(frame_rms: np.ndarray) -> list[float]:
    """Downsample frame RMS to a fixed-length, peak-normalized [0,1] sparkline."""
    if len(frame_rms) == 0:
        return [0.0] * ENVELOPE_BINS
    peak = float(frame_rms.max())
    if peak <= 0:
        return [0.0] * ENVELOPE_BINS
    idx = np.linspace(0, len(frame_rms), ENVELOPE_BINS + 1).astype(int)
    bins = [
        float(frame_rms[idx[i] : max(idx[i] + 1, idx[i + 1])].max()) / peak
        for i in range(ENVELOPE_BINS)
    ]
    return [round(v, 3) for v in bins]


def analyze_pcm(pcm: np.ndarray) -> dict[str, Any]:
    """Compute quality metrics + trim bounds for one clip's int16-scaled PCM.

    ``pcm`` is float32 in int16 range ([-32768, 32767]). Returns a dict with
    ``metrics``, ``verdict``, ``issues``, ``trim`` (sample bounds + ms),
    ``envelope``, and ``has_trimmed``.
    """
    n = len(pcm)
    duration_ms = int(round(n / SAMPLE_RATE * 1000))
    frame_rms = _frame_rms_lin(pcm)
    total_frames = len(frame_rms)

    peak = float(np.max(np.abs(pcm))) if n else 0.0
    peak_dbfs = _dbfs(peak / _FULL_SCALE)
    overall_rms = float(np.sqrt(np.mean((pcm / _FULL_SCALE) ** 2))) if n else 0.0
    rms_dbfs = _dbfs(overall_rms)
    clipping_pct = round(100.0 * float(np.count_nonzero(np.abs(pcm) >= _CLIP_LEVEL)) / n, 3) if n else 0.0

    voiced, noise_lin, _thr = _voiced_mask(frame_rms)
    noise_dbfs = _dbfs(noise_lin)
    voiced_idx = np.flatnonzero(voiced)
    if voiced_idx.size:
        start_f, end_f = int(voiced_idx[0]), int(voiced_idx[-1]) + 1
        speech_lin = float(np.sqrt(np.mean(frame_rms[voiced] ** 2)))
    else:
        start_f, end_f, speech_lin = 0, 0, 0.0
    snr_db = round(_dbfs(speech_lin) - noise_dbfs, 1) if speech_lin > 0 else 0.0

    speech_ratio = round(float(voiced.mean()), 3) if total_frames else 0.0
    voiced_ms = (end_f - start_f) * FRAME_MS
    leading_silence_ms = start_f * FRAME_MS
    trailing_silence_ms = (total_frames - end_f) * FRAME_MS if total_frames else 0

    # Gating heuristic: a genuinely loud clip that's also mostly hard-zeroed
    # (the XVF3800 noise-suppressor clamping the tail/gaps to digital silence).
    digital_silence_frac = (
        float(np.mean(frame_rms < 10.0 ** (_DIGITAL_SILENCE_DBFS / 20.0)))
        if total_frames else 0.0
    )
    gated = bool(voiced_idx.size and peak_dbfs > -12.0 and digital_silence_frac > 0.6)

    # ── Trim bounds (end-aligned) ──
    if voiced_idx.size:
        start = max(0, start_f * FRAME - _PREROLL_SAMPLES)
        end = min(n, end_f * FRAME + _TRAIL_SAMPLES)
        has_trimmed = end > start
    else:
        start, end, has_trimmed = 0, n, False

    # ── Verdict + issues ──
    issues: list[str] = []
    if voiced_ms < _MIN_SPEECH_MS or not voiced_idx.size:
        issues.append("no_speech")
    else:
        if voiced_ms < _SHORT_SPEECH_MS:
            issues.append("very_short")
    if clipping_pct > _CLIP_WARN_PCT:
        issues.append("clipping")
    if peak_dbfs < _QUIET_PEAK_DBFS:
        issues.append("too_quiet")
    if "no_speech" not in issues and snr_db < _LOW_SNR_DB:
        issues.append("low_snr")
    if leading_silence_ms > _LONG_SILENCE_MS or trailing_silence_ms > _LONG_SILENCE_MS:
        issues.append("long_silence")
    if gated:
        issues.append("gated")

    if "no_speech" in issues or clipping_pct > _CLIP_POOR_PCT or snr_db < _POOR_SNR_DB:
        verdict = "poor"
    elif (
        snr_db >= _GOOD_SNR_DB
        and clipping_pct <= _CLIP_WARN_PCT
        and peak_dbfs >= _GOOD_PEAK_DBFS
        and voiced_ms >= _SHORT_SPEECH_MS
        and not gated
    ):
        verdict = "good"
    else:
        verdict = "fair"

    return {
        "metrics": {
            "peak_dbfs": round(peak_dbfs, 1),
            "rms_dbfs": round(rms_dbfs, 1),
            "noise_dbfs": round(noise_dbfs, 1),
            "snr_db": snr_db,
            "clipping_pct": clipping_pct,
            "speech_ratio": speech_ratio,
            "voiced_ms": voiced_ms,
            "leading_silence_ms": leading_silence_ms,
            "trailing_silence_ms": trailing_silence_ms,
        },
        "verdict": verdict,
        "issues": issues,
        "trim": {
            "start_sample": int(start),
            "end_sample": int(end),
            "start_ms": int(round(start / SAMPLE_RATE * 1000)),
            "end_ms": int(round(end / SAMPLE_RATE * 1000)),
        },
        "envelope": _envelope(frame_rms),
        "raw_duration_ms": duration_ms,
        "trimmed_duration_ms": int(round((end - start) / SAMPLE_RATE * 1000)) if has_trimmed else 0,
        "has_trimmed": has_trimmed,
    }


# ─── Sidecar persistence (idempotent) ──────────────────────────────────────


def _analysis_dir(raw_path: Path) -> Path:
    return raw_path.parent / ANALYSIS_DIRNAME


def _sidecar_path(raw_path: Path) -> Path:
    return _analysis_dir(raw_path) / f"{raw_path.stem}.json"


def trimmed_path(raw_path: Path) -> Path:
    """Public: the audit-only trimmed WAV for a raw clip (may not exist)."""
    return _analysis_dir(raw_path) / f"{raw_path.stem}.trimmed.wav"


def _source_sig(raw_path: Path) -> dict[str, int]:
    st = raw_path.stat()
    return {"size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}


def _default_selected(verdict: str) -> bool:
    # Auto-exclude obviously-bad captures; the operator can re-include them.
    return verdict != "poor"


def ensure_analysis(raw_path: Path, *, force: bool = False) -> dict[str, Any]:
    """Return the analysis dict for ``raw_path``, computing + persisting it
    (sidecar JSON + trimmed WAV) if missing/stale. Idempotent.

    Preserves a user-set ``selected`` across recompute — recomputing metrics
    never silently re-includes a clip the operator deselected.
    """
    raw_path = Path(raw_path)
    sidecar = _sidecar_path(raw_path)
    try:
        sig = _source_sig(raw_path)
    except OSError:
        # Raw clip vanished — nothing to analyze.
        raise FileNotFoundError(raw_path)

    prior: dict[str, Any] = {}
    if sidecar.is_file():
        try:
            prior = json.loads(sidecar.read_text("utf-8"))
        except (OSError, ValueError):
            prior = {}
    fresh = (
        not force
        and prior.get("schema") == ANALYSIS_VERSION
        and prior.get("source") == sig
    )
    if fresh and (not prior.get("has_trimmed") or trimmed_path(raw_path).is_file()):
        return prior

    pcm = _read_wav_int16(raw_path)
    analysis = analyze_pcm(pcm)

    # Carry a user's explicit selection forward; otherwise default from verdict.
    if prior.get("selected_source") == "user":
        selected = bool(prior.get("selected", True))
        selected_source = "user"
    else:
        selected = _default_selected(analysis["verdict"])
        selected_source = "auto"

    tpath = trimmed_path(raw_path)
    if analysis["has_trimmed"]:
        try:
            _write_wav_int16(
                tpath, pcm[analysis["trim"]["start_sample"] : analysis["trim"]["end_sample"]]
            )
        except OSError as e:
            log.warning("wake clip: failed to write trimmed %s: %s", tpath, e)
            analysis["has_trimmed"] = False
    else:
        # Stale trimmed from a prior version → remove it.
        tpath.unlink(missing_ok=True)

    record = {
        "schema": ANALYSIS_VERSION,
        "source": sig,
        "name": raw_path.name,
        "selected": selected,
        "selected_source": selected_source,
        **analysis,
    }
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(record), "utf-8")
    except OSError as e:
        log.warning("wake clip: failed to write sidecar %s: %s", sidecar, e)
    return record


def set_selected(raw_path: Path, selected: bool) -> dict[str, Any]:
    """Persist an operator's include/exclude choice (marks it user-set so a
    later recompute won't override it). Ensures analysis exists first."""
    record = ensure_analysis(raw_path)
    record["selected"] = bool(selected)
    record["selected_source"] = "user"
    try:
        _sidecar_path(raw_path).write_text(json.dumps(record), "utf-8")
    except OSError as e:
        log.warning("wake clip: failed to persist selection for %s: %s", raw_path, e)
    return record


def set_score(raw_path: Path, score: float) -> dict[str, Any]:
    """Persist the offline openWakeWord max-over-clip score into a clip's
    sidecar (so the dashboard clip list can show it). Ensures analysis exists
    first."""
    record = ensure_analysis(raw_path)
    record["score"] = round(float(score), 4)
    try:
        _sidecar_path(raw_path).write_text(json.dumps(record), "utf-8")
    except OSError as e:
        log.warning("wake clip: failed to persist score for %s: %s", raw_path, e)
    return record


# ─── Directory-level helpers ───────────────────────────────────────────────


def iter_raw_clips(slug_dir: Path) -> list[Path]:
    """Sorted top-level ``clip_*.wav`` (the raw roster — never the analysis /
    training subdirs)."""
    slug_dir = Path(slug_dir)
    if not slug_dir.is_dir():
        return []
    return sorted(p for p in slug_dir.glob("*.wav") if p.is_file())


def analyze_dir(slug_dir: Path, *, force: bool = False) -> list[dict[str, Any]]:
    """Ensure + return analysis for every raw clip in a slug dir, in order.
    Backfills any clip missing a current sidecar."""
    out: list[dict[str, Any]] = []
    for clip in iter_raw_clips(slug_dir):
        try:
            out.append(ensure_analysis(clip, force=force))
        except FileNotFoundError:
            continue
    return out


def remove_analysis(raw_path: Path) -> None:
    """Delete a raw clip's sidecar + trimmed artifacts (call when deleting the
    raw clip)."""
    _sidecar_path(raw_path).unlink(missing_ok=True)
    trimmed_path(raw_path).unlink(missing_ok=True)


def selected_count(slug_dir: Path) -> int:
    """How many raw clips are currently selected for training."""
    return sum(1 for a in analyze_dir(slug_dir) if a.get("selected"))


def stage_selected_clips(slug_dir: Path) -> tuple[Path, int]:
    """Rebuild ``<slug>/.training/`` with the SELECTED raw clips, re-indexed
    ``clip_001.wav`` contiguously, and return ``(training_dir, count)``.

    This is what the trainer consumes — so training only ever sees the clips
    the operator curated. The staging dir is hidden from the raw roster + the
    trainer's own top-level glob, so it's safe inside the slug dir.
    """
    slug_dir = Path(slug_dir)
    training_dir = slug_dir / TRAINING_DIRNAME
    if training_dir.exists():
        shutil.rmtree(training_dir, ignore_errors=True)
    training_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for clip in iter_raw_clips(slug_dir):
        try:
            analysis = ensure_analysis(clip)
        except FileNotFoundError:
            continue
        if not analysis.get("selected"):
            continue
        count += 1
        shutil.copy2(clip, training_dir / f"clip_{count:03d}.wav")
    return training_dir, count
