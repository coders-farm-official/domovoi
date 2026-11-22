"""Make a loopable party-babble track for the XVF3800 noisy-room trial.

Six overlapping talkers made from Domovoi's three shipped Piper voices
(Kristin, Linda = LJ Speech, Norman: public domain, see ``SHIPPED_VOICES``
in domovoi/clients/tts.py), each voice used twice with a different pitch,
pace and distance, saying unrelated everyday sentences with conversational
gaps; a light synthetic room reverb; a few synthetic glass clinks. Nothing
recorded or downloaded beyond the pinned voices, which are checked against
their pins before use.

    python scripts/dev/noise_spike/make_babble.py --voices DIR --out DIR [--minutes 4]

DIR holds the three voices' .onnx + .onnx.json. Writes party-babble.wav
(44.1 kHz stereo-identical mono, 16-bit) and, when ffmpeg is on PATH,
party-babble.mp3. The end crossfades into the start, so it loops without a
seam.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import fftconvolve, resample_poly

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from domovoi.clients import tts  # noqa: E402
from domovoi.phonemizer import Phonemizer  # noqa: E402

OUT_SR = 44_100
SENTENCES = [
    "So we finally drove up to the lake last weekend, and the weather was perfect the whole time.",
    "She said the new place has a much bigger kitchen, but the commute is going to be terrible.",
    "Did you try the dip over there? I think it has roasted peppers in it.",
    "He has been working on that car for three years and it has never actually started.",
    "We should all go to that concert in the spring. Tickets go on sale next Friday.",
    "My sister just got a puppy, and now every photo she sends me is of the dog.",
    "I told him the meeting was at nine, and of course he showed up at half past ten.",
    "The kids have a soccer tournament out of town, so the whole weekend is gone.",
    "Honestly, I would rather stay in, but everyone said this was going to be fun.",
    "Wait, are you the one who painted the mural downtown? How long did that take?",
    "We tried the noodle place on the corner, the one with the blue sign. It was great.",
    "I keep meaning to fix the fence, but it has rained every weekend this month.",
    "Oh, that reminds me, did anyone ever find the keys to the shed?",
    "No, no, the trick is to let the dough rest overnight in the fridge.",
    "They are moving to the coast in June, if the house sells in time.",
    "I have not seen that film yet, so please do not tell me how it ends.",
    "The train was forty minutes late again, and nobody said a word about why.",
    "You would not believe how expensive the tomatoes were at the market.",
    "We are thinking about painting the hallway green, a sort of soft sage.",
    "Right, and then the power went out in the middle of the second half.",
    "He swears the old bike is faster than the new one, which makes no sense.",
    "My grandmother still writes letters by hand, every single month.",
    "Are you going on the hiking trip, or is your knee still bothering you?",
    "I think the bakery closes at six on Sundays, so we would have to hurry.",
    "Yes, exactly, that is what I said, but nobody listened to me.",
    "The neighbours finally cut down that huge tree in their back garden.",
    "Can you pass me one of those napkins? Thanks, that is perfect.",
    "We spent the whole afternoon looking for a parking space downtown.",
    "Apparently the library is open late on Thursdays now, which is handy.",
    "I started running in the mornings, but only twice so far, to be fair.",
    "Do you remember the name of that song from the wedding? It is driving me mad.",
    "The cat knocked a whole plant off the windowsill at three in the morning.",
    "Last summer we rented a little cabin with no internet at all. It was lovely.",
    "Well, she is starting a new job on Monday, so she is pretty nervous.",
    "I could not find the right size, so I just ordered both and will send one back.",
    "Is that the same recipe as last time? It tastes a bit different.",
    "We should get the boys together for a game night sometime soon.",
    "My phone keeps telling me the storage is full, and I have no idea why.",
    "Ha, that is hilarious, you have to tell that story to everyone else.",
    "The school play is next Thursday, and he still has not learned his lines.",
]

# (voice, pitch factor, pace, gain dB): six people from three voices.
TALKERS = [
    ("en_US-kristin-medium", 1.00, 1.00, 0.0),
    ("en_US-kristin-medium", 0.90, 0.95, -3.0),
    ("en_US-ljspeech-medium", 1.00, 1.05, -1.0),
    ("en_US-ljspeech-medium", 1.10, 0.95, -4.0),
    ("en_US-norman-medium", 1.00, 1.00, -1.5),
    ("en_US-norman-medium", 0.92, 1.05, -3.5),
]


def check_voice(voices: Path, ref: str) -> Path:
    v = next(x for x in tts.SHIPPED_VOICES if x.model_ref == ref)
    onnx, cfg = voices / f"{ref}.onnx", voices / f"{ref}.onnx.json"
    import hashlib
    if hashlib.sha256(onnx.read_bytes()).hexdigest() != v.onnx_sha256:
        raise SystemExit(f"{onnx} is not the pinned model")
    if tts._git_blob_sha1(cfg.read_bytes()) != v.json_blob_sha1:
        raise SystemExit(f"{cfg} is not the pinned config")
    return onnx


def render_sentences(voices: Path) -> dict[str, list[np.ndarray]]:
    import onnxruntime
    onnxruntime.set_seed(20261122)
    ph = Phonemizer()
    out: dict[str, list[np.ndarray]] = {}
    for ref in sorted({t[0] for t in TALKERS}):
        voice = tts.PiperVoice(check_voice(voices, ref), threads=1)
        clips = []
        for s in SENTENCES:
            pcm = voice.synthesize_pcm(ph.phonemize(s, dialect=voice.dialect), 1.0)
            clips.append(np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0)
        out[ref] = [c for c in clips]
        out[ref + ":sr"] = voice.sample_rate  # type: ignore[assignment]
        print(f"{ref}: {len(clips)} sentences")
    return out


def to_out_rate(x: np.ndarray, sr: int, pitch: float) -> np.ndarray:
    """Resample so it plays at OUT_SR with its pitch (and pace) times ``pitch``."""
    r = Fraction(OUT_SR, sr) / Fraction(pitch).limit_denominator(50)
    r = r.limit_denominator(400)
    return resample_poly(x, r.numerator, r.denominator).astype(np.float32)


def talker_track(clips, sr, pitch, pace, n, rng) -> np.ndarray:
    out = np.zeros(n, dtype=np.float32)
    t = int(rng.uniform(0, 3) * OUT_SR)
    order = rng.permutation(len(clips))
    k = 0
    while t < n:
        c = to_out_rate(clips[order[k % len(order)]], sr, pitch * pace)
        k += 1
        c *= 10 ** (rng.uniform(-3, 2) / 20)
        end = min(n, t + len(c))
        out[t:end] += c[: end - t]
        # a conversational gap; now and then the other person talks for a while
        gap = rng.uniform(0.2, 0.9) if rng.random() > 0.2 else rng.uniform(2.0, 5.0)
        t = end + int(gap * OUT_SR)
    return out


def clinks(n: int, rng: np.random.Generator) -> np.ndarray:
    out = np.zeros(n, dtype=np.float32)
    t = np.arange(int(0.6 * OUT_SR)) / OUT_SR
    for _ in range(int(n / OUT_SR / 12)):  # about one every 12 s
        f0 = rng.uniform(2200, 3600)
        ring = sum(np.sin(2 * np.pi * f0 * m * t) * np.exp(-t * d) / m
                   for m, d in ((1, 9), (2.7, 14), (5.4, 22)))
        at = rng.integers(0, n - len(t))
        out[at:at + len(t)] += (ring * 10 ** (rng.uniform(-30, -20) / 20)).astype(np.float32)
    return out


def room(x: np.ndarray, rng: np.random.Generator, rt60: float = 0.5, wet: float = 0.3) -> np.ndarray:
    t = np.arange(int(rt60 * OUT_SR)) / OUT_SR
    ir = rng.standard_normal(len(t)).astype(np.float32) * np.exp(-6.9 * t / rt60).astype(np.float32)
    ir /= np.sqrt(np.sum(ir ** 2))
    return (1 - wet) * x + wet * fftconvolve(x, ir)[: len(x)].astype(np.float32)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voices", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--minutes", type=float, default=4.0)
    ap.add_argument("--seed", type=int, default=22)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    fade = 4 * OUT_SR
    n = int(a.minutes * 60 * OUT_SR) + fade
    clips = render_sentences(a.voices)
    mix = np.zeros(n, dtype=np.float32)
    for ref, pitch, pace, gain in TALKERS:
        mix += talker_track(clips[ref], clips[ref + ":sr"], pitch, pace, n, rng) * 10 ** (gain / 20)
    mix = room(mix, rng) + clinks(n, rng)
    # loop: the last `fade` samples crossfade into the first
    head, tail = mix[:fade].copy(), mix[-fade:]
    ramp = np.linspace(0, 1, fade, dtype=np.float32)
    mix = mix[: n - fade]
    mix[:fade] = head * ramp + tail * (1 - ramp)
    mix *= 10 ** (-18 / 20) / np.sqrt(np.mean(mix ** 2))  # -18 dBFS RMS
    mix = np.tanh(mix * 1.2) / 1.2  # soft peak limit
    a.out.mkdir(parents=True, exist_ok=True)
    wav = a.out / "party-babble.wav"
    sf.write(wav, mix, OUT_SR, subtype="PCM_16")
    print(f"{wav}: {len(mix) / OUT_SR:.0f} s, peak {20 * np.log10(np.abs(mix).max()):.1f} dBFS")
    ff = shutil.which("ffmpeg")
    if ff:
        subprocess.run([ff, "-loglevel", "error", "-y", "-i", str(wav), "-codec:a", "libmp3lame", "-b:a", "192k",
                        "-metadata", "title=Party babble (synthetic)", str(a.out / "party-babble.mp3")], check=True)
        print(a.out / "party-babble.mp3")


if __name__ == "__main__":
    main()
