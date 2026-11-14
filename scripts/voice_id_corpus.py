"""Make the synthetic speaker corpus that scripts/eval_voice_id.py scores.

Real household recordings never go in the repo, so the voice ID harness
speaks its test lines with a multi-speaker Piper voice: every speaker id of
the model is a different (synthetic) person. Any multi-speaker Piper model
works; the numbers in docs were made with ``en_US-libritts-high`` (904
speakers; the voice is MIT, trained on LibriTTS, CC BY 4.0). Nothing is
downloaded here: pass the model you have.

Usage:
    python scripts/voice_id_corpus.py --piper-model PATH/en_US-libritts-high.onnx \
        --out WORKDIR/corpus --speakers 48 --lines 10

Writes ``<out>/<speaker>/<n>.wav`` (16 kHz mono int16) and ``<out>/corpus.json``.
Each line is said with a slightly different speed and expressiveness, so two
lines of one speaker differ the way two commands of one person do.
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

LINES = [
    "Domovoi, turn off the lights in the kitchen.",
    "What's the weather going to be like tomorrow morning?",
    "Play some quiet music in the dining room.",
    "Set a timer for twelve minutes, please.",
    "Add milk and coffee beans to the shopping list.",
    "What time is it right now?",
    "Skip this song, I don't like it.",
    "Remind me to call the plumber at four o'clock.",
    "How long until the laundry is done?",
    "Turn the volume down a little bit.",
    "Read me my shopping list.",
    "Who wrote the book I was reading last night?",
    "Pause the music for a minute.",
    "Tell me a joke about cats.",
    "Is the garage door closed?",
    "Start the playlist I made on Sunday.",
]

_VOICE = None


def _speak(job: tuple[str, int, int, str, float, float]) -> tuple[int, int, int, np.ndarray]:
    model, speaker, n, text, length_scale, noise_scale = job
    global _VOICE
    from piper import PiperVoice, SynthesisConfig

    if _VOICE is None:
        _VOICE = PiperVoice.load(model)
    chunks = list(_VOICE.synthesize(text, syn_config=SynthesisConfig(
        speaker_id=speaker, length_scale=length_scale, noise_scale=noise_scale,
    )))
    audio = np.concatenate([c.audio_int16_array for c in chunks]).astype(np.float32)
    rate = chunks[0].sample_rate
    return speaker, n, rate, audio


def resample(audio: np.ndarray, rate: int, to: int = 16_000) -> np.ndarray:
    """Band-limited resample (scipy's polyphase filter)."""
    if rate == to:
        return audio
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(rate, to)
    return resample_poly(audio, to // g, rate // g)


def write_wav(path: Path, audio: np.ndarray, rate: int = 16_000) -> None:
    pcm = np.clip(np.round(audio), -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--piper-model", required=True, help="a multi-speaker Piper .onnx (its .onnx.json beside it)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--speakers", type=int, default=48)
    ap.add_argument("--lines", type=int, default=10)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    cfg = json.loads(Path(args.piper_model + ".json").read_text(encoding="utf-8"))
    total = int(cfg.get("num_speakers") or 1)
    if total < 4:
        print("the model has fewer than 4 speakers; pass a multi-speaker Piper voice", file=sys.stderr)
        return 2
    rng = np.random.default_rng(args.seed)
    speakers = sorted(rng.choice(total, size=min(args.speakers, total), replace=False).tolist())
    jobs = []
    for s in speakers:
        order = rng.permutation(len(LINES))[: args.lines]
        for n, li in enumerate(order):
            jobs.append((args.piper_model, int(s), n, LINES[li],
                         float(rng.uniform(0.9, 1.15)), float(rng.uniform(0.5, 0.8))))
    args.out.mkdir(parents=True, exist_ok=True)
    index: dict[str, list[str]] = {}
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for speaker, n, rate, audio in pool.map(_speak, jobs, chunksize=4):
            d = args.out / f"s{speaker:03d}"
            d.mkdir(exist_ok=True)
            path = d / f"{n:02d}.wav"
            write_wav(path, resample(audio, int(rate)))
            index.setdefault(d.name, []).append(str(path.relative_to(args.out)).replace("\\", "/"))
    (args.out / "corpus.json").write_text(json.dumps(
        {"model": Path(args.piper_model).name, "speakers": {k: sorted(v) for k, v in sorted(index.items())}},
        indent=1,
    ), encoding="utf-8")
    print(f"{len(jobs)} lines from {len(index)} speakers in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
