#!/usr/bin/env python
"""Offline-score a trained wake word's recorded clips — the decisive test.

Scores every recorded positive clip (raw AND the auto-trimmed/end-aligned copy)
against the trained ``<slug>.onnx`` the way the Pi actually runs it: reset
streaming state per clip, feed 1280-sample frames in order, take the
max-over-clip. Also scores a silence clip and, if given, a directory of
held-out synthetic positives through the SAME path.

The point (see the kickoff doc's Objective 1): if the synthetic positives ALSO
collapse to ~0 through this harness, the "0/30 real clips" result is a
harness/alignment bug, not a model failure. If the synthetics still score high
here but the real clips don't, you've *evidenced* a genuine real-audio gap.

Usage (from the repo root):
    python scripts/wake_word/score_clips.py hey_domovoi
    python scripts/wake_word/score_clips.py hey_domovoi --threshold 0.5 \
        --synthetic-dir /path/to/heldout_synthetic_wavs

Requires ``openwakeword`` installed (onnx inference runs on Windows). Exits 2
with a runbook pointer when it isn't.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from domovoi import wake_clip_quality as wq
from domovoi import wake_eval
from domovoi.config import settings


def _fmt(v) -> str:
    return "  —  " if v is None else f"{v:5.3f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("slug", help="the wake word slug (= model stem, = <slug>.onnx)")
    ap.add_argument("--threshold", type=float, default=wake_eval.DEFAULT_THRESHOLD)
    ap.add_argument("--model-ref", default=None, help="override path to the .onnx")
    ap.add_argument("--clips-dir", default=None, help="override the slug's clip dir")
    ap.add_argument(
        "--synthetic-dir",
        default=None,
        help="dir of held-out synthetic positive WAVs for the sanity check",
    )
    args = ap.parse_args(argv)

    slug_dir = Path(args.clips_dir) if args.clips_dir else Path(settings.wake_clips_dir) / args.slug

    try:
        model = wake_eval.load_model(args.slug, model_ref=args.model_ref)
    except wake_eval.WakeEvalUnavailable as e:
        print(f"error: {e}", file=sys.stderr)
        print(
            "  install the scorer deps (pip install openwakeword) and train the "
            "wake word first — see scripts/wake_word/README.md.",
            file=sys.stderr,
        )
        return 2

    clip_scores = wake_eval.score_dir(model, slug_dir)
    silence = wake_eval.silence_score(model)

    synthetic_scores: list[float] = []
    if args.synthetic_dir:
        for wav in sorted(Path(args.synthetic_dir).glob("*.wav")):
            synthetic_scores.append(wake_eval.score_clip(model, wav))

    thr = args.threshold
    print(f"\nwake word: {args.slug}   threshold: {thr}   clips: {slug_dir}\n")
    print(f"{'clip':<20} {'raw':>7} {'trim':>7}  sel  verdict")
    print("-" * 52)
    for c in clip_scores:
        rec = wq.ensure_analysis(slug_dir / c["name"])
        mark_raw = "*" if c["raw_score"] >= thr else " "
        mark_trim = "*" if (c["trimmed_score"] or 0) >= thr else " "
        print(
            f"{c['name']:<20} {_fmt(c['raw_score'])}{mark_raw} "
            f"{_fmt(c['trimmed_score'])}{mark_trim}  "
            f"{'Y' if c['selected'] else '.'}   {rec['verdict']}"
        )

    summary = wake_eval.summarize(
        clip_scores, threshold=thr, silence=silence,
        synthetic_scores=synthetic_scores or None,
    )
    print("\nsummary")
    print(f"  real raw   : recall {summary['raw_recall']:.2f}  (max {summary['raw_max']:.3f})")
    if summary.get("trimmed_recall") is not None:
        print(f"  real trim  : recall {summary['trimmed_recall']:.2f}  (max {summary['trimmed_max']:.3f})")
    print(f"  silence    : {summary['silence_score']:.3f}  (want ≈ 0)")
    if "synthetic_recall" in summary:
        print(f"  synthetic  : recall {summary['synthetic_recall']:.2f}  (max {summary['synthetic_max']:.3f})  ← harness sanity")
        print(
            "\n  If synthetic recall is LOW here too → harness/alignment bug, not the model.\n"
            "  If synthetic is HIGH but real is LOW → a genuine real-audio gap."
        )
    else:
        print("  (pass --synthetic-dir for the harness sanity check)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
