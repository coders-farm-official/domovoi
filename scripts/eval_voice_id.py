"""Voice ID harness: how often each room names the right person, and the wrong one.

Voice ID's hard rule is that a wrong name is worse than no name, so this
reports, per simulated room, three rates over every test command:

* correct  — the speaker was named;
* wrong    — somebody else was named (for a guest nobody enrolled, any name);
* EER      — the equal error rate of the raw scores (own profile vs others'),
             which says how separable the voices are before any threshold.

The speakers are synthetic (scripts/voice_id_corpus.py: one multi-speaker
Piper voice, every speaker id a person), the rooms simulated here: each has
a distance (direct-to-reverberant ratio), a reverberation time, a mic
colouring, a loudness at the mic, a noise floor, and optionally music or a
TV under the command. Every recording carries what a satellite's does: a
quarter second of room before the command and the second of silence the
satellite waits through after it. Households are pairs ("the owner" and "a
second person") enrolled with one introduction in the office; guests are
speakers nobody enrolled.

The encoder is the one voice ID runs (domovoi/clients/speaker_encoder.py,
Resemblyzer's weights), so the torch extra and the resemblyzer weights must
be installed. Decisions go through voice_identifier's own scoring and
:func:`voice_identifier._decide`.

Usage:
    python scripts/voice_id_corpus.py --piper-model PATH/en_US-libritts-high.onnx --out WORK/corpus
    python scripts/eval_voice_id.py --corpus WORK/corpus --cache WORK/emb.npz
    python scripts/eval_voice_id.py --corpus WORK/corpus --cache WORK/emb.npz --experiments --op-point

``--cache`` keeps the embeddings (the slow part) between runs.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

SR = 16_000


# ─── rooms ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Room:
    name: str
    drr_db: float          # direct-to-reverberant ratio (near ≈ +10, across a room ≈ 0)
    rt60: float            # seconds
    level_dbfs: float      # the voice's loudness at the mic
    noise_snr: float       # room noise under the voice
    tilt: float            # mic/room colouring: + brighter, - duller
    music_snr: float | None = None
    tv_snr: float | None = None
    seed: int = 0


ROOMS = [
    Room("office", drr_db=8, rt60=0.35, level_dbfs=-26, noise_snr=35, tilt=0.0, seed=1),
    Room("kitchen", drr_db=3, rt60=0.55, level_dbfs=-32, noise_snr=28, tilt=0.3, seed=2),
    Room("dining", drr_db=0, rt60=0.7, level_dbfs=-38, noise_snr=24, tilt=-0.4, seed=3),
    Room("garage", drr_db=-2, rt60=1.0, level_dbfs=-38, noise_snr=18, tilt=0.5, seed=4),
    Room("garage+music", drr_db=-2, rt60=1.0, level_dbfs=-38, noise_snr=18, tilt=0.5,
         music_snr=6, seed=4),
    Room("garage+loud music", drr_db=-2, rt60=1.0, level_dbfs=-38, noise_snr=18, tilt=0.5,
         music_snr=0, seed=4),
    Room("dining+tv", drr_db=0, rt60=0.7, level_dbfs=-38, noise_snr=24, tilt=-0.4,
         tv_snr=6, seed=3),
]
ENROLL_ROOM = "office"


def _pink(n: int, rng: np.random.Generator) -> np.ndarray:
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(spec.size, dtype=np.float64)
    f[0] = 1.0
    return np.fft.irfft(spec / np.sqrt(f), n)


def _rir(room: Room, rng: np.random.Generator) -> np.ndarray:
    """A direct path plus an exponentially decaying, darkening tail; the
    room's own seed fixes its shape, ``rng`` varies it a little per take
    (the person stands somewhere slightly different)."""
    base = np.random.default_rng(1000 + room.seed)
    n = int(SR * min(1.2, room.rt60 * 1.2))
    t = np.arange(n) / SR
    tail = base.standard_normal(n) * 0.8 + rng.standard_normal(n) * 0.2
    tail *= np.exp(-6.9 * t / room.rt60)
    # Air and walls absorb highs as the tail goes on: a one-pole low-pass.
    from scipy.signal import lfilter

    tail = lfilter([0.35], [1, -0.65], tail)
    tail[: int(0.004 * SR)] = 0.0                   # first reflections arrive after the direct path
    direct = np.zeros(n)
    direct[0] = 1.0
    e_tail = float(np.sum(tail ** 2))
    tail *= np.sqrt(10 ** (-room.drr_db / 10) / e_tail)
    return direct + tail


def _tilt(x: np.ndarray, tilt: float) -> np.ndarray:
    """A first-order shelf: the room's mic and surfaces colour every voice the same way."""
    if tilt == 0:
        return x
    from scipy.signal import lfilter

    hp = lfilter([1, -1], [1, -0.95], x)          # the highs
    return x + tilt * hp


def _music(n: int, rng: np.random.Generator) -> np.ndarray:
    """Chords of harmonic tones changing twice a second, a bass line and a
    kick: enough like music to sit in the same bands as a voice."""
    out = np.zeros(n)
    step = SR // 2
    t = np.arange(step) / SR
    env = np.minimum(1.0, t * 40) * np.exp(-t * 1.5)
    for start in range(0, n, step):
        seg = np.zeros(step)
        root = 110.0 * 2 ** (rng.integers(0, 12) / 12)
        for ratio in (1.0, 1.26, 1.5, 2.0):
            f0 = root * ratio * 2
            for h in range(1, 8):
                if f0 * h > 7000:
                    break
                seg += np.sin(2 * np.pi * f0 * h * t + rng.uniform(0, 6.28)) / h
        seg += 2 * np.sin(2 * np.pi * root / 2 * t)
        seg *= env
        kick = np.sin(2 * np.pi * 60 * t) * np.exp(-t * 30) * 3
        seg += kick
        seg += rng.standard_normal(step) * np.exp(-((t - 0.25) % 0.25) * 60) * 0.5   # hats
        m = min(step, n - start)
        out[start:start + m] = seg[:m]
    return out


def _at_snr(signal_rms: float, noise: np.ndarray, snr_db: float) -> np.ndarray:
    r = float(np.sqrt(np.mean(noise ** 2))) or 1.0
    return noise * (signal_rms / r) * 10 ** (-snr_db / 20)


def in_room(clean: np.ndarray, room: Room, rng: np.random.Generator,
            babble: np.ndarray | None = None) -> np.ndarray:
    """``clean`` (16 kHz float) as a satellite in ``room`` records it."""
    from scipy.signal import fftconvolve

    wet = fftconvolve(clean, _rir(room, rng))[: len(clean) + int(0.3 * SR)]
    wet = _tilt(wet, room.tilt)
    voiced = np.abs(clean) > 0.02 * np.max(np.abs(clean))
    rms_voice = float(np.sqrt(np.mean(wet[: len(clean)][voiced] ** 2))) or 1e-3
    wet *= 10 ** (room.level_dbfs / 20) / rms_voice
    pre, post = int(0.25 * SR), int(1.0 * SR)
    rec = np.concatenate([np.zeros(pre), wet, np.zeros(max(0, post - int(0.3 * SR)))])
    level = 10 ** (room.level_dbfs / 20)
    rec += _at_snr(level, _pink(len(rec), rng), room.noise_snr)
    if room.music_snr is not None:
        rec += _at_snr(level, _music(len(rec), rng), room.music_snr)
    if room.tv_snr is not None and babble is not None:
        tv = np.resize(babble, len(rec))
        rec += _at_snr(level, _tilt(tv, -0.6), room.tv_snr)
    return np.clip(rec, -1, 1).astype(np.float32)


# ─── the corpus, recorded in every room, embedded ────────────────────────


def _read(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


@contextlib.contextmanager
def _timer(label: str) -> Iterator[None]:
    t = time.perf_counter()
    yield
    print(f"  {label}: {time.perf_counter() - t:.1f} s", file=sys.stderr)


PIPELINES = ("raw", "prep")


def embed_all(corpus: Path, cache: Path | None) -> dict:
    """``{"speakers": [...], "lines": n, "rooms": [...], "emb": {pipeline: array
    (speaker, line, room, 256)}, "snr": (speaker, line, room), "ms": {...}}``."""
    if cache is not None and cache.is_file():
        z = np.load(cache, allow_pickle=True)
        return z["data"].item()
    from domovoi.clients.speaker_encoder import SpeakerEncoder
    from domovoi.voice_prep import prepare

    enc = SpeakerEncoder(device="cpu")
    index = json.loads((corpus / "corpus.json").read_text(encoding="utf-8"))["speakers"]
    speakers = sorted(index)
    lines = min(len(v) for v in index.values())
    clean = {s: [_read(corpus / p) for p in sorted(index[s])[:lines]] for s in speakers}
    out = {p: np.zeros((len(speakers), lines, len(ROOMS), 256), np.float32) for p in PIPELINES}
    snr = np.zeros((len(speakers), lines, len(ROOMS)), np.float32)
    secs = np.zeros((len(speakers), lines, len(ROOMS)), np.float32)
    ms = {p: [] for p in PIPELINES}
    for si, s in enumerate(speakers):
        other = clean[speakers[(si + 7) % len(speakers)]]
        babble = np.concatenate(other) * 0.5
        for li in range(lines):
            for ri, room in enumerate(ROOMS):
                rng = np.random.default_rng(hash((si, li, ri)) & 0xFFFFFFFF)
                rec = in_room(clean[s][li], room, rng, babble)
                t = time.perf_counter()
                out["raw"][si, li, ri] = enc.embed_utterance(rec)
                ms["raw"].append(time.perf_counter() - t)
                t = time.perf_counter()
                prep = prepare(rec)
                out["prep"][si, li, ri] = enc.embed_utterance(prep.audio)
                ms["prep"].append(time.perf_counter() - t)
                snr[si, li, ri] = prep.snr_db
                secs[si, li, ri] = prep.speech_sec
        print(f"  embedded {s} ({si + 1}/{len(speakers)})", file=sys.stderr)
    data = {
        "speakers": speakers, "lines": lines, "rooms": [r.name for r in ROOMS],
        "emb": out, "snr": snr, "speech_sec": secs,
        "ms": {p: float(np.median(v) * 1000) for p, v in ms.items()},
    }
    if cache is not None:
        np.savez_compressed(cache, data=np.array(data, dtype=object))
    return data


# ─── scoring ─────────────────────────────────────────────────────────────

ENROLL_LINES = (0,)        # one introduction, as "my name is …" makes
ROOM_LINE = 1              # the line said in a room for that room's own sample
TEST_LINES_FROM = 2


@dataclass
class Variant:
    name: str
    pipeline: str                    # embeddings of "raw" or "prep" audio
    enroll_pipeline: str | None = None   # samples enrolled under another pipeline (legacy)
    room_samples: bool = False       # each person also has one sample from each room
    room_scoring: bool = False       # score against the room's samples when there are any
    threshold: float = 0.75
    margin: float = 0.05
    same_room_margin: float = 0.0    # voice_profile_same_room_margin
    noisy_snr: float | None = None   # below this SNR: name nobody (voice_profile_noisy_snr_db)
    compensate: str | None = None    # "global" | "room-pop" | "room-house": subtract a mean voice


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def run(data: dict, v: Variant) -> dict[str, dict[str, float]]:
    from domovoi.config import settings
    from domovoi import voice_identifier as vi

    from domovoi.clients.voice_embedder import VoicePrint

    settings.voice_profile_match_threshold = v.threshold
    settings.voice_profile_match_margin = v.margin
    settings.voice_profile_same_room_margin = v.same_room_margin
    rooms = data["rooms"]
    enroll_ri = rooms.index(ENROLL_ROOM)
    emb = data["emb"][v.pipeline]
    enr = data["emb"][v.enroll_pipeline or v.pipeline]
    snr = data["snr"]
    n = len(data["speakers"])
    enrolled = list(range(0, n - n // 3))
    guests = list(range(n - n // 3, n))
    households = [(enrolled[i], enrolled[i + 1]) for i in range(0, len(enrolled) - 1, 2)]

    def samples_for(person: int) -> list[tuple[int, bytes, str | None]]:
        out = [(person, enr[person, li, enroll_ri].tobytes(), ENROLL_ROOM) for li in ENROLL_LINES]
        if v.room_samples:
            for ri, room in enumerate(rooms):
                base = room.split("+")[0]
                if base != ENROLL_ROOM and "+" not in room:
                    out.append((person, enr[person, ROOM_LINE, ri].tobytes(), base))
        return out

    def comp(e: np.ndarray, mean: np.ndarray | None) -> np.ndarray:
        if mean is None:
            return e
        d = e - mean
        return (d / np.linalg.norm(d)).astype(np.float32)

    glob_mean = None
    if v.compensate == "global":
        glob_mean = np.mean(_unit(emb[guests][:, :, :, :].reshape(-1, emb.shape[-1])), axis=0)

    def room_mean(ri: int, hh: tuple[int, int]) -> np.ndarray | None:
        if v.compensate == "global":
            return glob_mean
        if v.compensate == "room-pop":
            return np.mean(_unit(emb[guests, ROOM_LINE, ri]), axis=0)
        if v.compensate == "room-house":
            return np.mean(_unit(emb[list(hh), :TEST_LINES_FROM, ri].reshape(-1, emb.shape[-1])), axis=0)
        return None

    def samples_comp(hh: tuple[int, int]) -> list[tuple[int, bytes, str | None]]:
        if v.compensate is None:
            return samples_for(hh[0]) + samples_for(hh[1])
        out = []
        for p, blob, r in samples_for(hh[0]) + samples_for(hh[1]):
            e = np.frombuffer(blob, dtype=np.float32)
            out.append((p, comp(e, room_mean(rooms.index(r), hh)).tobytes(), r))
        return out

    results: dict[str, dict[str, float]] = {}
    for ri, room in enumerate(rooms):
        base_room = room.split("+")[0]
        named_right = named_wrong = trials = 0
        g_wrong = g_trials = 0
        target, nontarget = [], []
        for hh in households:
            samples = samples_comp(hh)
            mean_here = room_mean(ri, hh)
            speakers = list(hh) + guests
            for spk in speakers:
                for li in range(TEST_LINES_FROM, data["lines"]):
                    e = comp(emb[spk, li, ri], mean_here)
                    # voice_identifier's own scoring: with no room (or no
                    # sample from it) it is the pre-1.3.0 centroid score.
                    scored = vi._score_turn(
                        VoicePrint(current=e), samples, [],
                        room_id=base_room if v.room_scoring else None,
                    )
                    scores = scored.scores
                    if spk in hh:
                        target.append(scores[spk])
                        nontarget.extend(s for p, s in scores.items() if p != spk)
                    d = vi._decide(scores, scored.bars)
                    pid = d.person_id
                    if pid is not None and v.noisy_snr is not None and snr[spk, li, ri] < v.noisy_snr:
                        pid = None
                    if spk in hh:
                        trials += 1
                        named_right += pid == spk
                        named_wrong += pid is not None and pid != spk
                    else:
                        g_trials += 1
                        g_wrong += pid is not None
        results[room] = {
            "correct": named_right / trials,
            "wrong": (named_wrong + g_wrong) / (trials + g_trials),
            "wrong_member": named_wrong / trials,
            "wrong_guest": g_wrong / g_trials,
            "eer": eer(np.array(target), np.array(nontarget)),
            "target_mean": float(np.mean(target)),
            "nontarget_mean": float(np.mean(nontarget)),
        }
    return results


def eer(target: np.ndarray, nontarget: np.ndarray) -> float:
    ts = np.sort(np.concatenate([target, nontarget]))
    best = 1.0
    for t in ts[:: max(1, len(ts) // 2000)]:
        far = float(np.mean(nontarget >= t))
        frr = float(np.mean(target < t))
        best = min(best, max(far, frr))
    return best


def table(name: str, res: dict[str, dict[str, float]]) -> str:
    rows = [f"{name}", f"  {'room':<18} {'correct':>8} {'wrong':>7} {'EER':>6} {'own':>6} {'other':>6}"]
    for room, r in res.items():
        rows.append(
            f"  {room:<18} {r['correct'] * 100:7.1f}% {r['wrong'] * 100:6.2f}% "
            f"{r['eer'] * 100:5.1f}% {r['target_mean']:6.3f} {r['nontarget_mean']:6.3f}"
        )
    allc = np.mean([r["correct"] for r in res.values()]) * 100
    allw = np.mean([r["wrong"] for r in res.values()]) * 100
    rows.append(f"  {'mean':<18} {allc:7.1f}% {allw:6.2f}%")
    return "\n".join(rows)


def operating_point(data: dict, v: Variant, max_wrong: float) -> tuple[Variant, dict]:
    """The lowest threshold (so the most people named) at which no room
    names the wrong person more than ``max_wrong`` of the time."""
    from dataclasses import replace

    best = None
    for th in np.arange(0.40, 0.96, 0.01):
        vv = replace(v, threshold=round(float(th), 2))
        r = run(data, vv)
        if max(x["wrong"] for x in r.values()) <= max_wrong:
            best = (vv, r)
            break
    if best is None:
        vv = replace(v, threshold=0.99)
        best = (vv, run(data, vv))
    return best


# What main did before 1.3.0, and what it does now. The rest are the
# experiments the choice was made from (docs: the 1.3.0 release note).
BEFORE = Variant("before 1.3.0: whole recording, office enrolment", "raw")
AFTER_UPDATE = Variant("1.3.0 just updated: legacy samples, music gate", "raw", noisy_snr=10.0)
AFTER_MOVE = Variant("1.3.0 moved over: speech only, office sample only, music gate", "prep",
                     noisy_snr=10.0)
AFTER_ROOMS = Variant("1.3.0 + a sample in each room: room-first, +0.03 there, music gate",
                      "prep", room_samples=True, room_scoring=True, same_room_margin=0.03,
                      noisy_snr=10.0)
SHIPPED = [BEFORE, AFTER_UPDATE, AFTER_MOVE, AFTER_ROOMS]

EXPERIMENTS = [
    Variant("B  speech trimmed + levelled (re-enrolled)", "prep"),
    Variant("B' trimmed live, legacy raw enrolment", "prep", enroll_pipeline="raw"),
    Variant("C  raw + a sample per room, centroid of all", "raw", room_samples=True),
    Variant("D  raw + a sample per room, room-first scoring", "raw", room_samples=True, room_scoring=True),
    Variant("E  trimmed + a sample per room, room-first", "prep", room_samples=True, room_scoring=True),
    Variant("F  raw, global mean voice removed", "raw", compensate="global"),
    Variant("G  raw, room's population mean removed", "raw", compensate="room-pop"),
    Variant("H  raw, room's household mean removed", "raw", compensate="room-house"),
    Variant("J  trimmed + room samples, room-first, global mean removed", "prep", room_samples=True,
            room_scoring=True, compensate="global"),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--cache", type=Path)
    ap.add_argument("--max-wrong", type=float, default=0.01,
                    help="the operating point: the worst room's wrong-name rate allowed (default 1%%)")
    ap.add_argument("--experiments", action="store_true",
                    help="also run the experiments the 1.3.0 choice was made from")
    ap.add_argument("--op-point", action="store_true",
                    help="also find each variant's operating point (slow)")
    ap.add_argument("--json", type=Path, help="write every result here")
    args = ap.parse_args(argv)
    with _timer("embeddings"):
        data = embed_all(args.corpus, args.cache)
    print(f"{len(data['speakers'])} speakers x {data['lines']} lines x {len(data['rooms'])} rooms; "
          f"encoder median {data['ms']['raw']:.0f} ms raw, {data['ms']['prep']:.0f} ms trimmed")
    everything = {}
    for v in SHIPPED + (EXPERIMENTS if args.experiments else []):
        res = run(data, v)
        everything[v.name] = res
        print(table(f"{v.name} (threshold {v.threshold}, margin {v.margin}, "
                    f"same room +{v.same_room_margin})", res))
        if args.op_point:
            vv, r = operating_point(data, v, args.max_wrong)
            everything[f"{v.name} @ {vv.threshold}"] = r
            print(table(f"   at threshold {vv.threshold} (worst room wrong <= {args.max_wrong:.0%})", r))
    if args.json:
        args.json.write_text(json.dumps(everything, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
