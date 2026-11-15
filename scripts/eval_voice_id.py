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
3D-Speaker's ERes2Net on ONNX Runtime; ``--model`` names its file when it
isn't in the voice-models directory). Decisions go through voice_identifier's
own scoring and :func:`voice_identifier._decide`. ``--tune`` runs the sweeps
every 1.3.0 setting was chosen from (what is embedded, the threshold, the
margins, the music gate, the confident band, the clustering threshold).
``--encoder ort:PATH`` scores a candidate ONNX model through the same rooms;
compare encoders with ``--op-point``, as thresholds are not portable.

Usage:
    python scripts/voice_id_corpus.py --piper-model PATH/en_US-libritts-high.onnx --out WORK/corpus
    python scripts/eval_voice_id.py --corpus WORK/corpus --cache WORK/emb.npz
    python scripts/eval_voice_id.py --corpus WORK/corpus --cache WORK/emb.npz --tune --op-point

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


# What voice ID could embed of a recording: "raw" the whole recording;
# "prep" the speech frames joined and levelled (domovoi/voice_prep.py
# prepare, what voice ID embeds: for_embedding); "edges"/"edges3" the
# recording trimmed to 0.1 s / 0.3 s before its first speech and after its
# last, pauses kept (trim_edges below).
PIPELINES = ("raw", "prep", "edges", "edges3")


def trim_edges(wav: np.ndarray, keep_sec: float = 0.1) -> Prepared:
    """The recording from just before its first speech to just after its
    last (``keep_sec`` of room either side), as it was: pauses between
    words stay and the level isn't touched. ``speech_sec`` and ``snr_db``
    are :func:`prepare`'s (the speech frames alone and how clear they were)."""
    from domovoi.voice_prep import FRAME, Prepared, _frame_db, prepare, speech_mask

    wav = np.asarray(wav, dtype=np.float32)
    db = _frame_db(wav)
    mask = speech_mask(db)
    full = prepare(wav)
    if not mask.any():
        return Prepared(audio=wav, speech_sec=full.speech_sec, snr_db=full.snr_db)
    idx = np.flatnonzero(mask)
    keep = int(round(keep_sec * SR))
    start = max(0, int(idx[0]) * FRAME - keep)
    stop = min(len(wav), (int(idx[-1]) + 1) * FRAME + keep)
    return Prepared(audio=wav[start:stop], speech_sec=full.speech_sec, snr_db=full.snr_db)


# ─── encoders ────────────────────────────────────────────────────────────
#
# ``--encoder`` picks what embeds the recordings:
#
# * ``eres2net`` (default): domovoi/clients/speaker_encoder.py, what voice ID
#   runs (``--model`` names the ONNX file when it isn't in the voice-models
#   directory);
# * ``ort:PATH``: another 3D-Speaker or WeSpeaker-style ONNX export (80-band
#   Kaldi fbank in, one embedding out) through the same front end, to
#   measure a candidate before anything changes in domovoi/.
#
# Resemblyzer, the encoder before 1.3.0, and the other candidates the 1.3.0
# choice was made from are in this file's history.


class Encoder:
    """One utterance (16 kHz float32) in, one embedding out."""

    name = "?"
    dim = 0
    model_bytes = 0

    def embed(self, wav: np.ndarray) -> np.ndarray:
        raise NotImplementedError


class OrtEncoder(Encoder):
    def __init__(self, path: Path | None, threads: int) -> None:
        from domovoi.clients import speaker_encoder as se

        if path is not None and path.name == se.MODEL_FILE:
            self._enc = se.SpeakerEncoder(path, threads=threads)
            self.name = se.MODEL_TAG
        elif path is None:
            self._enc = se.SpeakerEncoder(threads=threads)
            self.name = se.MODEL_TAG
            path = se.model_path()
        else:
            self._enc = _AnyOrt(path, threads)
            self.name = path.stem
        self.dim = se.EMBEDDING_DIM if isinstance(self._enc, se.SpeakerEncoder) else self._enc.dim
        self.model_bytes = path.stat().st_size

    def embed(self, wav: np.ndarray) -> np.ndarray:
        return self._enc.embed_utterance(wav)


class _AnyOrt:
    def __init__(self, path: Path, threads: int) -> None:
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        self._s = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        self._in = self._s.get_inputs()[0].name
        self.dim = int(self._s.get_outputs()[0].shape[-1])

    def embed_utterance(self, wav: np.ndarray) -> np.ndarray:
        from domovoi.clients.speaker_encoder import features

        e = self._s.run(None, {self._in: features(wav)[None]})[0].reshape(-1)
        return (e / np.linalg.norm(e)).astype(np.float32)


def load_encoder(spec: str, threads: int = 1, model: Path | None = None) -> Encoder:
    if spec == "eres2net":
        return OrtEncoder(model, threads)
    kind, _, where = spec.partition(":")
    if kind == "ort":
        return OrtEncoder(Path(where), threads)
    raise SystemExit(f"unknown encoder {spec!r}: eres2net or ort:PATH")


def _rss() -> int:
    try:
        import psutil
    except ImportError:
        return 0
    return int(psutil.Process().memory_info().rss)


def embed_all(corpus: Path, cache: Path | None, encoder: str = "eres2net",
              threads: int = 1, model: Path | None = None) -> dict:
    """``{"speakers": [...], "lines": n, "rooms": [...], "emb": {pipeline: array
    (speaker, line, room, dim)}, "snr": (speaker, line, room), "ms": {...}}``."""
    if cache is not None and cache.is_file():
        z = np.load(cache, allow_pickle=True)
        return z["data"].item()
    from domovoi.voice_prep import prepare

    rss0 = _rss()
    enc = load_encoder(encoder, threads, model)
    rss_loaded = _rss()
    index = json.loads((corpus / "corpus.json").read_text(encoding="utf-8"))["speakers"]
    speakers = sorted(index)
    lines = min(len(v) for v in index.values())
    clean = {s: [_read(corpus / p) for p in sorted(index[s])[:lines]] for s in speakers}
    out = {p: np.zeros((len(speakers), lines, len(ROOMS), enc.dim), np.float32) for p in PIPELINES}
    snr = np.zeros((len(speakers), lines, len(ROOMS)), np.float32)
    secs = np.zeros((len(speakers), lines, len(ROOMS)), np.float32)
    ms = {p: [] for p in PIPELINES}
    enc_ms, enc_sec = [], []
    peak = rss_loaded
    for si, s in enumerate(speakers):
        other = clean[speakers[(si + 7) % len(speakers)]]
        babble = np.concatenate(other) * 0.5
        for li in range(lines):
            for ri, room in enumerate(ROOMS):
                rng = np.random.default_rng(hash((si, li, ri)) & 0xFFFFFFFF)
                rec = in_room(clean[s][li], room, rng, babble)
                cut = {
                    "raw": lambda r: r,
                    "prep": lambda r: prepare(r).audio,
                    "edges": lambda r: trim_edges(r, 0.1).audio,
                    "edges3": lambda r: trim_edges(r, 0.3).audio,
                }
                for p in PIPELINES:
                    t = time.perf_counter()
                    audio = cut[p](rec)
                    t2 = time.perf_counter()
                    out[p][si, li, ri] = enc.embed(audio)
                    t3 = time.perf_counter()
                    ms[p].append(t3 - t)
                    if p == "prep":
                        enc_ms.append(t3 - t2)
                        enc_sec.append(len(audio) / SR)
                prep = prepare(rec)
                snr[si, li, ri] = prep.snr_db
                secs[si, li, ri] = prep.speech_sec
        peak = max(peak, _rss())
        print(f"  embedded {s} ({si + 1}/{len(speakers)})", file=sys.stderr)
    data = {
        "speakers": speakers, "lines": lines, "rooms": [r.name for r in ROOMS],
        "emb": out, "snr": snr, "speech_sec": secs,
        "ms": {p: float(np.median(v) * 1000) for p, v in ms.items()},
        "encoder": {
            "name": enc.name, "spec": encoder, "dim": enc.dim, "threads": threads,
            "model_bytes": enc.model_bytes,
            # the encoder alone on the audio voice ID embeds ("prep")
            "ms_median": float(np.median(enc_ms) * 1000),
            "ms_p95": float(np.percentile(enc_ms, 95) * 1000),
            "audio_sec_median": float(np.median(enc_sec)),
            "rss_load_mb": (rss_loaded - rss0) / 2 ** 20,
            "rss_peak_mb": (peak - rss0) / 2 ** 20,
        },
    }
    if cache is not None:
        np.savez_compressed(cache, data=np.array(data, dtype=object))
    return data


# ─── scoring ─────────────────────────────────────────────────────────────

ENROLL_LINES = (0,)        # one introduction, as "my name is …" makes
ROOM_LINE = 1              # the line said in a room for that room's own sample
TEST_LINES_FROM = 2
MUSIC_ROOMS = ("garage+music", "garage+loud music", "dining+tv")


@dataclass
class Variant:
    name: str
    pipeline: str                    # embeddings of "raw", "prep", "edges" or "edges3" audio
    room_samples: bool = False       # each person also has one sample from each room
    room_scoring: bool = False       # score against the room's samples when there are any
    threshold: float = 0.75          # voice_profile_match_threshold
    margin: float = 0.05             # voice_profile_match_margin
    same_room_margin: float = 0.0    # voice_profile_same_room_margin
    noisy_snr: float | None = None   # below this SNR: name nobody (voice_profile_noisy_snr_db)
    confident_margin: float = 0.05   # voice_profile_drift_near_threshold_margin


def run(data: dict, v: Variant) -> dict[str, dict[str, float]]:
    """Every room's rates for ``v``, through voice_identifier's own
    scoring (``_score_turn``) and decision (``_decide``)."""
    from domovoi.config import settings
    from domovoi import voice_identifier as vi

    from domovoi.clients.voice_embedder import VoicePrint

    settings.voice_profile_match_threshold = v.threshold
    settings.voice_profile_match_margin = v.margin
    settings.voice_profile_same_room_margin = v.same_room_margin
    settings.voice_profile_drift_near_threshold_margin = v.confident_margin
    vi.EMBEDDING_DIM = data["emb"][v.pipeline].shape[-1]
    rooms = data["rooms"]
    enroll_ri = rooms.index(ENROLL_ROOM)
    emb = data["emb"][v.pipeline]
    snr = data["snr"]
    n = len(data["speakers"])
    enrolled = list(range(0, n - n // 3))
    guests = list(range(n - n // 3, n))
    households = [(enrolled[i], enrolled[i + 1]) for i in range(0, len(enrolled) - 1, 2)]

    def samples_for(person: int) -> list[tuple[int, bytes, str | None]]:
        out = [(person, emb[person, li, enroll_ri].tobytes(), ENROLL_ROOM) for li in ENROLL_LINES]
        if v.room_samples:
            for ri, room in enumerate(rooms):
                base = room.split("+")[0]
                if base != ENROLL_ROOM and "+" not in room:
                    out.append((person, emb[person, ROOM_LINE, ri].tobytes(), base))
        return out

    results: dict[str, dict[str, float]] = {}
    for ri, room in enumerate(rooms):
        base_room = room.split("+")[0]
        named_right = named_wrong = trials = 0
        g_wrong = g_trials = 0
        conf_right = conf_wrong = 0
        target, nontarget = [], []
        for hh in households:
            samples = samples_for(hh[0]) + samples_for(hh[1])
            for spk in list(hh) + guests:
                for li in range(TEST_LINES_FROM, data["lines"]):
                    scored = vi._score_turn(
                        VoicePrint(current=emb[spk, li, ri]), samples,
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
                    if pid is not None and d.confident:
                        conf_right += pid == spk
                        conf_wrong += pid != spk
                    if spk in hh:
                        trials += 1
                        named_right += pid == spk
                        named_wrong += pid is not None and pid != spk
                    else:
                        g_trials += 1
                        g_wrong += pid is not None
        results[room] = {
            "correct": named_right / trials,
            "none": (trials - named_right - named_wrong) / trials,   # a member, nobody named
            "wrong": (named_wrong + g_wrong) / (trials + g_trials),
            "wrong_member": named_wrong / trials,
            "wrong_guest": g_wrong / g_trials,
            "confident_right": conf_right / trials,
            "confident_wrong": conf_wrong,
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
    rows = [f"{name}", f"  {'room':<18} {'correct':>8} {'wrong':>7} {'none':>7} {'EER':>6} "
                       f"{'own':>6} {'other':>6}"]
    for room, r in res.items():
        rows.append(
            f"  {room:<18} {r['correct'] * 100:7.1f}% {r['wrong'] * 100:6.2f}% "
            f"{r['none'] * 100:6.1f}% "
            f"{r['eer'] * 100:5.1f}% {r['target_mean']:6.3f} {r['nontarget_mean']:6.3f}"
        )
    allc, allw, alln, alle = _means(res)
    rows.append(f"  {'mean':<18} {allc:7.1f}% {allw:6.2f}% {alln:6.1f}% {alle:5.1f}%")
    return "\n".join(rows)


def _means(res: dict[str, dict[str, float]]) -> tuple[float, float, float, float]:
    return tuple(float(np.mean([r[k] for r in res.values()]) * 100)
                 for k in ("correct", "wrong", "none", "eer"))


def _worst_wrong(res: dict[str, dict[str, float]]) -> float:
    return max(x["wrong"] for x in res.values())


def operating_point(data: dict, v: Variant, max_wrong: float) -> tuple[Variant, dict]:
    """The lowest threshold (so the most people named) at which no room
    names the wrong person more than ``max_wrong`` of the time."""
    from dataclasses import replace

    for th in np.arange(0.30, 0.96, 0.01):
        vv = replace(v, threshold=round(float(th), 2))
        r = run(data, vv)
        if _worst_wrong(r) <= max_wrong:
            return vv, r
    vv = replace(v, threshold=0.99)
    return vv, run(data, vv)


# What 1.3.0 ships (domovoi/config.py, domovoi/voice_prep.py): what voice ID
# embeds, one introduction in the office, the music gate; and the same once
# every room has learned a sample of each person.
SHIPPED_PIPELINE = "prep"
SHIPPED = [
    Variant("1.3.0: office introduction, music gate", SHIPPED_PIPELINE,
            threshold=0.58, margin=0.05, noisy_snr=10.0, confident_margin=0.05),
    Variant("1.3.0 + a sample in each room: room-first, same-room margin, music gate",
            SHIPPED_PIPELINE, room_samples=True, room_scoring=True, threshold=0.58, margin=0.05,
            same_room_margin=0.01, noisy_snr=10.0, confident_margin=0.05),
]


def tune(data: dict, max_wrong: float) -> dict:
    """The sweeps every 1.3.0 setting was chosen from (docs/RELEASE_NOTES.md,
    1.3.0, and the comments in domovoi/config.py): printed, and returned."""
    out: dict = {}
    print("\n== 1. what is embedded (office introduction, music gate 10 dB, margin 0.05) ==")
    best_p = None
    for p in PIPELINES:
        v, r = operating_point(data, Variant(p, p, noisy_snr=10.0), max_wrong)
        c, _w, _n, e = _means(r)
        print(f"  {p:<7} threshold {v.threshold:.2f}: mean right {c:5.1f}%  worst wrong "
              f"{_worst_wrong(r) * 100:4.2f}%  mean EER {e:4.1f}%  "
              + " ".join(f"{k}={x['correct'] * 100:.0f}" for k, x in r.items()))
        out[f"pipeline {p}"] = {"threshold": v.threshold, "rooms": r}
        if best_p is None or c > best_p[1]:
            best_p = (p, c, v.threshold)
    p, _c, th = best_p
    print(f"  -> {p} (most named at the ceiling)")

    print(f"\n== 2. the margin between the best two ({p}) ==")
    for m in (0.0, 0.02, 0.03, 0.05, 0.08, 0.10):
        v, r = operating_point(data, Variant("m", p, margin=m, noisy_snr=10.0), max_wrong)
        c, _w, _n, _e = _means(r)
        print(f"  margin {m:.2f}: threshold {v.threshold:.2f}, mean right {c:5.1f}%, worst wrong "
              f"{_worst_wrong(r) * 100:4.2f}%")
        out[f"margin {m}"] = {"threshold": v.threshold, "mean_right": c}

    print(f"\n== 3. the same-room margin (a sample in each room, room-first, threshold {th:.2f}) ==")
    for srm in (0.0, 0.01, 0.02, 0.03, 0.05, 0.08):
        r = run(data, Variant("r", p, room_samples=True, room_scoring=True, threshold=th,
                              same_room_margin=srm, noisy_snr=10.0))
        c, _w, _n, _e = _means(r)
        print(f"  +{srm:.2f}: mean right {c:5.1f}%  worst wrong {_worst_wrong(r) * 100:4.2f}%  "
              + " ".join(f"{k}={x['correct'] * 100:.0f}/{x['wrong'] * 100:.1f}" for k, x in r.items()))
        out[f"same-room {srm}"] = r

    print(f"\n== 4. the noisy gate (threshold {th:.2f}) ==")
    for g in (None, 4.0, 6.0, 8.0, 10.0, 12.0):
        r = run(data, Variant("g", p, threshold=th, noisy_snr=g))
        music = {k: r[k] for k in MUSIC_ROOMS}
        quiet = {k: x for k, x in r.items() if k not in MUSIC_ROOMS}
        print(f"  gate {g}: music/TV rooms right/wrong "
              + " ".join(f"{k}={x['correct'] * 100:.0f}/{x['wrong'] * 100:.1f}" for k, x in music.items())
              + f"; other rooms mean right {np.mean([x['correct'] for x in quiet.values()]) * 100:.1f}%")
        out[f"gate {g}"] = r
    snr = data["snr"]
    rooms = data["rooms"]
    print("  speech SNR by room (median dB): " + ", ".join(
        f"{room}={float(np.median(snr[:, :, i])):.1f}" for i, room in enumerate(rooms)))

    print(f"\n== 5. the confident band (learning a room; threshold {th:.2f}) ==")
    for cm in (0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12):
        a = run(data, Variant("c", p, threshold=th, noisy_snr=10.0, confident_margin=cm))
        b = run(data, Variant("c", p, room_samples=True, room_scoring=True, threshold=th,
                              noisy_snr=10.0, confident_margin=cm))
        cw = sum(x["confident_wrong"] for x in a.values()) + sum(x["confident_wrong"] for x in b.values())
        cr = np.mean([x["confident_right"] for x in a.values()]) * 100
        print(f"  +{cm:.2f}: confident wrong names {cw} (office-only + every-room runs), "
              f"confident right {cr:4.1f}% of members' commands")
        out[f"confident {cm}"] = {"wrong": cw, "right": cr}

    print("\n== 6. clustering unknown voices (one utterance against another, quiet rooms) ==")
    emb = data["emb"][p]
    same, diff = [], []
    n = len(data["speakers"])
    for i, room in enumerate(rooms):
        if room in MUSIC_ROOMS:
            continue
        e = emb[:, TEST_LINES_FROM:, i]
        flat = e.reshape(-1, e.shape[-1])
        who = np.repeat(np.arange(n), e.shape[1])
        sims = flat @ flat.T
        iu = np.triu_indices(len(flat), 1)
        s, same_mask = sims[iu], who[iu[0]] == who[iu[1]]
        same.append(s[same_mask])
        diff.append(s[~same_mask])
    same, diff = np.concatenate(same), np.concatenate(diff)
    for t in (0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7):
        print(f"  merge at {t:.2f}: same voice merged {np.mean(same >= t) * 100:5.1f}%, "
              f"two voices merged {np.mean(diff >= t) * 100:5.2f}%")
    out["cluster"] = {"same_p50": float(np.median(same)), "diff_p99": float(np.percentile(diff, 99))}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--cache", type=Path)
    ap.add_argument("--max-wrong", type=float, default=0.01,
                    help="the operating point: the worst room's wrong-name rate allowed (default 1%%)")
    ap.add_argument("--op-point", action="store_true",
                    help="also find each variant's operating point (slow)")
    ap.add_argument("--tune", action="store_true",
                    help="run the sweeps the 1.3.0 settings were chosen from (slow)")
    ap.add_argument("--json", type=Path, help="write every result here")
    ap.add_argument("--encoder", default="eres2net",
                    help="eres2net (default, what voice ID runs) or ort:PATH (see 'encoders' "
                         "in this file); give each its own --cache")
    ap.add_argument("--model", type=Path,
                    help="the ERes2Net ONNX file, when it isn't in the voice-models directory")
    ap.add_argument("--threads", type=int, default=1,
                    help="CPU threads the encoder may use (default 1: the timing a small server sees)")
    args = ap.parse_args(argv)
    with _timer("embeddings"):
        data = embed_all(args.corpus, args.cache, args.encoder, args.threads, args.model)
    print(f"{len(data['speakers'])} speakers x {data['lines']} lines x {len(data['rooms'])} rooms; "
          + ", ".join(f"{p} {ms:.0f} ms" for p, ms in data["ms"].items()))
    e = data.get("encoder")
    if e:
        print(f"encoder {e['name']}: {e['dim']}-dim, model {e['model_bytes'] / 2 ** 20:.1f} MB, "
              f"{e['ms_median']:.0f} ms median / {e['ms_p95']:.0f} ms p95 per embedding "
              f"({e['audio_sec_median']:.1f} s of audio, {e['threads']} thread(s)), "
              f"RAM +{e['rss_load_mb']:.0f} MB loaded, +{e['rss_peak_mb']:.0f} MB peak")
    everything: dict = {"_encoder": e} if e else {}
    for v in SHIPPED:
        res = run(data, v)
        everything[v.name] = res
        print(table(f"{v.name} (threshold {v.threshold}, margin {v.margin}, "
                    f"same room +{v.same_room_margin}, gate {v.noisy_snr} dB)", res))
        conf_wrong = sum(x["confident_wrong"] for x in res.values())
        print(f"  confident (learning) matches that named the wrong person: {conf_wrong}")
        if args.op_point:
            vv, r = operating_point(data, v, args.max_wrong)
            everything[f"{v.name} @ {vv.threshold}"] = r
            print(table(f"   at threshold {vv.threshold} (worst room wrong <= {args.max_wrong:.0%})", r))
    if args.tune:
        everything["tune"] = tune(data, args.max_wrong)
    if args.json:
        args.json.write_text(json.dumps(everything, indent=1, default=float), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
