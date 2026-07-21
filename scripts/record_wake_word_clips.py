"""Record wake-word training clips from a microphone.

openWakeWord's automatic-training pipeline expects a directory of short
WAVs of you saying the wake phrase. This script captures them one by one
with a visible countdown, normalizes to 16 kHz mono int16, and writes
them as `clip_NNN.wav` into the output directory.

Usage:
    python scripts/record_wake_word_clips.py --count 30 --out ~/.domovoi/wake_clips/hey_domovoi

Tips for good clips:
  - Vary your distance (close, arm's-length, across the room)
  - Vary your tone (calm, slightly raised, half-asked)
  - Some natural silence on either side (the script gives you ~2 s)
  - Different times of day if possible — different background noise

After recording, see scripts/wake_word/README.md for training.
"""

from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16_000
CLIP_SECONDS = 2.0


def _record_clip(seconds: float, device: int | None) -> np.ndarray:
    audio = sd.rec(
        int(seconds * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        device=device,
    )
    sd.wait()
    return audio.flatten()


def _save_wav(path: Path, pcm: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--count", type=int, default=30, help="Number of clips to record (default 30).")
    p.add_argument("--out", type=Path, required=True, help="Output directory.")
    p.add_argument("--device", type=int, default=None, help="sounddevice input device id.")
    p.add_argument(
        "--phrase", default="Hey Domovoi", help='Wake phrase to prompt for (default "Hey Domovoi").',
    )
    p.add_argument(
        "--seconds", type=float, default=CLIP_SECONDS,
        help="Clip length in seconds (default 2.0).",
    )
    p.add_argument(
        "--list-devices", action="store_true", help="Print sounddevice IDs and exit.",
    )
    args = p.parse_args(argv)

    if args.list_devices:
        print(sd.query_devices())
        return 0

    out = args.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)

    # Skip already-recorded clips so a re-run resumes from where you stopped.
    existing = sorted(out.glob("clip_*.wav"))
    start_idx = len(existing) + 1
    if start_idx > args.count:
        print(f"Already have {len(existing)} clips in {out}. Done.")
        return 0

    print(f"Recording {args.count - start_idx + 1} more clips of {args.seconds:.1f}s each.")
    print(f"Phrase: {args.phrase!r}")
    print(f"Output: {out}")
    print()
    print("Speak the phrase clearly each time. Vary distance, tone, and angle.")
    print("Press Ctrl+C to stop early — already-saved clips are preserved.")
    print()

    try:
        for i in range(start_idx, args.count + 1):
            print(f"[{i}/{args.count}] Get ready...", end="", flush=True)
            for c in (3, 2, 1):
                time.sleep(0.6)
                print(f" {c}", end="", flush=True)
            print(" GO")
            pcm = _record_clip(args.seconds, args.device)
            path = out / f"clip_{i:03d}.wav"
            _save_wav(path, pcm)
            peak = int(np.abs(pcm).max())
            quality = "GOOD" if peak > 6370 else ("LOW" if peak > 2000 else "VERY LOW — speak louder/closer")
            print(f"      saved {path.name}  peak={peak}  ({quality})")
    except KeyboardInterrupt:
        print("\nStopped. Re-run the script to continue from where you left off.")
        return 0

    print(f"\nDone. {args.count} clips in {out}.")
    print("Next: see scripts/wake_word/README.md to train the model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
