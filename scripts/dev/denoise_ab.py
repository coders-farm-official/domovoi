"""Offline A/B: does cleaning up a command's audio before Whisper help in a
noisy room? (1.5.0, noisy rooms, design §5 stage 2 item 4.) A dev tool,
not part of the product.

It runs the REAL speech recognition (``domovoi.clients.whisper.
FasterWhisperClient``, the same short-window / 30 s decode a turn gets)
on each clip as captured and after each cleanup mode of
``domovoi/stt_denoise.py``, and reports per noise level:

* **WER**: word error rate against the words actually said (numbers
  spelled out on both sides, punctuation and case ignored);
* **intent**: whether the transcript lands on the same quick command
  (fast path) as the words actually said, the router's first step
  (``scripts/eval_routing.py`` does the same dry run); for words with no
  quick command (the language-model route), a transcript with none either
  and at least :data:`LLM_ROUTE_MIN_RATIO` of the words. "exact" is a
  transcript equal to the words said;
* **quiet-room harm**: the "quiet" row, a room with only fan noise: any
  drop there is the cost of turning a mode on for every room;
* **CPU**: the cleanup's CPU time per second of audio (process time, one
  thread), next to Whisper's own decode time.

Two inputs:

1. ``--material DIR``: the noise spike's synthetic scenes
   (``make_tts.ps1``: ``cmd/*.wav`` commands, ``babble/*.wav`` talkers;
   Windows' built-in voices, nothing recorded). Each command is mixed with
   six-talker babble at each ``--snr`` (the command's active-speech level
   over the babble's long-term level) as the core would receive it: a
   moment of room before the command and about a second after (the
   satellite's end-of-speech silence). ``--babble-wav FILE`` adds a second
   babble source (e.g. the XVF3800 trial's ``party-babble.wav``, any rate).
   The spoken words come from ``DIR/commands.txt`` when present (line
   ``i % n`` for the i-th file in name order), else the spike's ten.

2. ``--captures DIR``: real command recordings, as Domovoi keeps them for
   a room opted in to Command recordings (``<id>.wav`` + ``<id>.json``,
   domovoi/command_captures.py), e.g. copied from the server after the
   XVF3800 room test. The words said are the planned line
   (``--commands``, the trial's commands.txt) closest to what any variant
   heard; a clip closer than ``--min-match`` to none is left out of WER
   and intent. ``--trial-log satellite.log`` (what xvf_trial_report.py
   pull writes) groups the clips by the DSP setting that was on (its
   ``xvf-trial: setting=X`` lines) instead of by noise level.

Run from the repo root, CPU only, at idle priority:

    python scripts/dev/denoise_ab.py --material <folder> [--babble-wav party-babble.wav]
    python scripts/dev/denoise_ab.py --captures <room folder> --commands commands.txt --trial-log satellite.log
    options: --modes off,spectral,rnnoise --model small.en --threads 4 --draws 2 --json out.json

``rnnoise`` needs pyrnnoise importable (``pip install --no-deps pyrnnoise``,
or ``--no-deps --target DIR`` and DIR on PYTHONPATH); without it that mode
falls back to the raw audio, which the report marks.
"""

from __future__ import annotations

import argparse
import difflib
import glob
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("USE_STUBS", "true")  # nothing else in the core loads

SR = 16_000
FRAME = SR * 30 // 1000
CMD_DBFS = -28.0          # the command's active-speech level at the mic
QUIET_FLOOR_DBFS = -62.0  # a quiet room: fan / fridge
LEAD_S, TAIL_S = 0.4, 1.2  # room before the command, end-of-speech silence after

SPIKE_COMMANDS = [
    "Play some jazz in the kitchen.",
    "Set a timer for ten minutes.",
    "Turn the volume down.",
    "What is the weather tomorrow?",
    "Skip this song.",
    "Add milk and eggs to the shopping list.",
    "Pause the music.",
    "Play the next episode of my podcast.",
    "Turn off the lights in the living room.",
    "How long until the pizza is ready?",
]


def low_priority() -> None:
    if sys.platform == "win32":
        import ctypes
        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), 0x40)  # IDLE_PRIORITY_CLASS
    else:
        os.nice(19)


# ── text ────────────────────────────────────────────────────────────────────

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen " \
        "sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _say_number(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        rest = n % 100
        return _ONES[n // 100] + " hundred" + ("" if rest == 0 else " " + _say_number(rest))
    return str(n)


def words(text: str) -> list[str]:
    t = text.lower().replace("'", "")
    t = re.sub(r"\d+", lambda m: " " + _say_number(int(m.group())) + " ", t)
    return re.sub(r"[^a-z ]+", " ", t).split()


def edit_distance(a: list[str], b: list[str]) -> int:
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (x != y))
    return d[len(b)]


_intent_cache: dict[str, str] = {}


def intent(text: str) -> str:
    """The quick command (fast path) the router's first step picks, or
    "-" for none (the language-model route)."""
    if text in _intent_cache:
        return _intent_cache[text]
    from domovoi.handlers import HANDLERS
    from domovoi.handlers.base import as_fast_path
    from domovoi.router import _LEADING_FILLER_RE, strip_request_preamble

    norm = text.lower().strip().rstrip(".,!?")
    norm = strip_request_preamble(_LEADING_FILLER_RE.sub("", norm))
    got = "-"
    for h in HANDLERS:
        if any(as_fast_path(e).pattern.match(norm) for e in h.fast_paths):
            got = h.name
            break
    _intent_cache[text] = got
    return got


#: A command with no quick command goes to the language model: its
#: transcript counts as right when it has no quick command either and is
#: this close (word-level ratio) to the words said.
LLM_ROUTE_MIN_RATIO = 0.75


def intent_ok(ref: str, hyp: str) -> bool:
    want, got = intent(ref), intent(hyp)
    if want != "-":
        return got == want
    return got == "-" and difflib.SequenceMatcher(None, words(ref), words(hyp)).ratio() >= LLM_ROUTE_MIN_RATIO


# ── audio ───────────────────────────────────────────────────────────────────

def load(path: str | Path) -> np.ndarray:
    import soundfile as sf
    from scipy.signal import resample_poly

    x, sr = sf.read(str(path), dtype="float32")
    x = x if x.ndim == 1 else x.mean(axis=1)
    if sr != SR:
        g = np.gcd(sr, SR)
        x = resample_poly(x, SR // g, sr // g).astype(np.float32)
    return x


def frame_rms(x: np.ndarray) -> np.ndarray:
    n = len(x) // FRAME
    return np.sqrt(np.mean(x[: n * FRAME].reshape(n, FRAME) ** 2, axis=1) + 1e-12)


def trim(x: np.ndarray) -> np.ndarray:
    e = frame_rms(x)
    on = np.where(20 * np.log10(e / e.max()) > -40)[0]
    return x[on[0] * FRAME:(on[-1] + 1) * FRAME]


def active_rms(x: np.ndarray) -> float:
    e = frame_rms(x)
    e = e[20 * np.log10(e / e.max()) > -30]
    return float(np.sqrt(np.mean(e ** 2)))


def set_level(x: np.ndarray, dbfs: float, active: bool = True) -> np.ndarray:
    rms = active_rms(x) if active else float(np.sqrt(np.mean(x ** 2)))
    return (x * (10 ** (dbfs / 20) / rms)).astype(np.float32)


def pink(n: int, rng: np.random.Generator) -> np.ndarray:
    f = np.fft.rfft(rng.standard_normal(n))
    f /= np.sqrt(np.maximum(np.arange(len(f)), 1))
    return np.fft.irfft(f, n).astype(np.float32)


def babble(n: int, talkers: list[np.ndarray], rng: np.random.Generator, k: int = 6) -> np.ndarray:
    """k talkers from the material, each pitch/pace-shifted and re-levelled
    (the noise spike's recipe)."""
    from scipy.signal import resample_poly

    out = np.zeros(n, dtype=np.float32)
    for _ in range(k):
        src = talkers[rng.integers(len(talkers))]
        up, down = [(17, 20), (19, 20), (21, 20), (23, 20), (9, 10), (11, 10)][rng.integers(6)]
        s = resample_poly(src, up, down).astype(np.float32)
        s = np.tile(s, int(np.ceil((n + len(s)) / len(s))))
        start = rng.integers(len(s) - n)
        seg = s[start:start + n]
        out += seg / (np.sqrt(np.mean(seg ** 2)) + 1e-9) * 10 ** (rng.uniform(-4, 4) / 20)
    return out


def loop_slice(track: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    reps = int(np.ceil((n + len(track)) / len(track)))
    t = np.tile(track, reps)
    start = rng.integers(len(t) - n)
    return t[start:start + n].copy()


def to_pcm(x: np.ndarray) -> bytes:
    return (np.clip(x, -1.0, 32767 / 32768) * 32768).astype("<i2").tobytes()


# ── the runs ────────────────────────────────────────────────────────────────

class Stt:
    def __init__(self, model: str, threads: int) -> None:
        from domovoi.config import settings
        settings.whisper_cpu_threads = threads
        from domovoi.clients.whisper import FasterWhisperClient

        self.client = FasterWhisperClient(model, "cpu", "int8")
        self.model = model

    def __call__(self, pcm: bytes) -> tuple[str, float]:
        t0 = time.perf_counter()
        text, _window = self.client._transcribe_pcm_sync(pcm)
        return text.strip(), time.perf_counter() - t0


def clean(pcm: bytes, mode: str) -> tuple[bytes, float, bool]:
    """(audio Whisper hears, CPU seconds spent, fell back to raw?)"""
    from domovoi import stt_denoise

    if mode == "off":
        return pcm, 0.0, False
    stt_denoise._cache = None  # time every clip, not the one-entry cache
    c0 = time.process_time()
    out = stt_denoise.denoise_pcm(pcm, mode)
    return out, time.process_time() - c0, out is pcm


class Tally:
    def __init__(self) -> None:
        self.errs = self.ref_words = self.ok_intent = self.exact = self.n = 0
        self.cpu = self.audio_s = self.stt_s = 0.0
        self.fallbacks = 0

    def add(self, ref: str | None, hyp: str, cpu: float, audio_s: float, stt_s: float, fell_back: bool) -> None:
        self.cpu += cpu
        self.audio_s += audio_s
        self.stt_s += stt_s
        self.fallbacks += int(fell_back)
        if ref is None:
            return
        r, h = words(ref), words(hyp)
        self.errs += edit_distance(r, h)
        self.ref_words += len(r)
        self.ok_intent += int(intent_ok(ref, hyp))
        self.exact += int(r == h)
        self.n += 1

    def row(self) -> dict:
        return {
            "n": self.n,
            "wer": round(self.errs / self.ref_words, 4) if self.ref_words else None,
            "intent": round(self.ok_intent / self.n, 4) if self.n else None,
            "exact": round(self.exact / self.n, 4) if self.n else None,
            "cpu_ms_per_s": round(1000 * self.cpu / self.audio_s, 1) if self.audio_s else None,
            "stt_ms_per_s": round(1000 * self.stt_s / self.audio_s, 1) if self.audio_s else None,
            "fallbacks": self.fallbacks,
        }


def run_clip(stt: Stt, pcm: bytes, modes: list[str]) -> dict[str, tuple[str, float, float, bool]]:
    out = {}
    for m in modes:
        audio, cpu, fell = clean(pcm, m)
        text, stt_s = stt(audio)
        out[m] = (text, cpu, stt_s, fell and m != "off")
    return out


def synthetic(a, stt: Stt, modes: list[str]) -> tuple[dict, list[dict]]:
    rng = np.random.default_rng(a.seed)
    m = Path(a.material)
    files = sorted(glob.glob(str(m / "cmd" / "*.wav")))
    if not files:
        raise SystemExit(f"no cmd/*.wav under {m} (make it with scripts/dev/noise_spike/make_tts.ps1)")
    said = SPIKE_COMMANDS
    if (m / "commands.txt").is_file():
        said = [x.strip() for x in (m / "commands.txt").read_text(encoding="utf-8").splitlines() if x.strip()]
    cmds = [(set_level(trim(load(p)), CMD_DBFS), said[i % len(said)]) for i, p in enumerate(files)]
    talkers = [load(p) for p in sorted(glob.glob(str(m / "babble" / "*.wav")))]
    extra = load(a.babble_wav) if a.babble_wav else None
    sources = (["spike"] if talkers else []) + (["file"] if extra is not None else [])
    if not sources:
        raise SystemExit("no babble: neither babble/*.wav in the material nor --babble-wav")

    tallies: dict[tuple[str, str], Tally] = defaultdict(Tally)
    rows = []
    for snr in a.snr:
        for d in range(a.draws):
            for ci, (cmd, ref) in enumerate(cmds):
                n = int((LEAD_S + TAIL_S) * SR) + len(cmd)
                bg = set_level(pink(n, rng), QUIET_FLOOR_DBFS, active=False)
                if snr != "quiet":
                    src = sources[(ci + d) % len(sources)]
                    crowd = babble(n, talkers, rng) if src == "spike" else loop_slice(extra, n, rng)
                    bg = bg + set_level(crowd, CMD_DBFS - float(snr), active=False)
                x = bg.copy()
                x[int(LEAD_S * SR):int(LEAD_S * SR) + len(cmd)] += cmd
                pcm = to_pcm(x)
                res = run_clip(stt, pcm, modes)
                for mode, (text, cpu, stt_s, fell) in res.items():
                    tallies[(str(snr), mode)].add(ref, text, cpu, len(x) / SR, stt_s, fell)
                rows.append({"snr": str(snr), "ref": ref, **{k: v[0] for k, v in res.items()}})
            print(f"  snr {snr}: draw {d + 1}/{a.draws} done", flush=True)
    return {k: v.row() for k, v in tallies.items()}, rows


def _trial_settings(log_path: str) -> list[tuple[datetime, str]]:
    marks = []
    for line in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
        mt = re.match(r"^(\S+)", line)
        ms = re.search(r"xvf-trial.*setting=(\S+)", line)
        if mt and ms:
            try:
                marks.append((datetime.strptime(mt.group(1), "%Y-%m-%dT%H:%M:%S%z"), ms.group(1).strip("'")))
            except ValueError:
                continue
    return sorted(marks)


def captures(a, stt: Stt, modes: list[str]) -> tuple[dict, list[dict]]:
    plan = [x.strip() for x in Path(a.commands).read_text(encoding="utf-8").splitlines()
            if x.strip() and not x.startswith("#")] if a.commands else []
    marks = _trial_settings(a.trial_log) if a.trial_log else []
    tallies: dict[tuple[str, str], Tally] = defaultdict(Tally)
    rows = []
    wavs = sorted(Path(a.captures).rglob("*.wav"))
    if not wavs:
        raise SystemExit(f"no .wav under {a.captures}")
    for w in wavs:
        side = {}
        if w.with_suffix(".json").is_file():
            side = json.loads(w.with_suffix(".json").read_text(encoding="utf-8"))
        x = load(w)
        pcm = to_pcm(x)
        res = run_clip(stt, pcm, modes)
        heard = [side.get("transcript") or ""] + [v[0] for v in res.values()]
        ref, best = None, 0.0
        for line in plan:
            for h in heard:
                s = difflib.SequenceMatcher(None, words(line), words(h)).ratio()
                if s > best:
                    ref, best = line, s
        if best < a.min_match:
            ref = None
        group = "all"
        at = side.get("captured_at")
        if marks and at:
            t = datetime.fromisoformat(at)
            group = next((s for when, s in reversed(marks) if when <= t), "before-trial")
        for mode, (text, cpu, stt_s, fell) in res.items():
            tallies[(group, mode)].add(ref, text, cpu, len(x) / SR, stt_s, fell)
        rows.append({"file": w.name, "group": group, "ref": ref, "match": round(best, 2),
                     **{k: v[0] for k, v in res.items()}})
        print(f"  {w.name} [{group}] ref={ref!r}: " + " | ".join(f"{k}: {v[0]}" for k, v in res.items()), flush=True)
    return {k: v.row() for k, v in tallies.items()}, rows


def print_table(table: dict, modes: list[str], groups: list[str], label: str) -> None:
    print(f"\n{label:>12} | " + " | ".join(f"{m:^30}" for m in modes))
    print(f"{'':>12} | " + " | ".join(f"{'WER':>7} {'intent':>7} {'exact':>6} {'n':>4}  " for _ in modes))
    for g in groups:
        cells = []
        for m in modes:
            r = table.get((g, m))
            if not r or r["n"] == 0:
                cells.append(f"{'-':>30}")
                continue
            cells.append(f"{r['wer'] * 100:6.1f}% {r['intent'] * 100:6.1f}% {r['exact'] * 100:5.1f}% {r['n']:>4}  ")
        print(f"{g:>12} | " + " | ".join(cells))
    print("\nCPU per second of audio (ms, this PC): cleanup / Whisper decode; fallbacks to raw")
    for m in modes:
        cpu = [table[(g, m)]["cpu_ms_per_s"] for g in groups if (g, m) in table]
        sttm = [table[(g, m)]["stt_ms_per_s"] for g in groups if (g, m) in table]
        fb = sum(table[(g, m)]["fallbacks"] for g in groups if (g, m) in table)
        print(f"  {m:>9}: cleanup {np.mean(cpu):6.1f}  whisper {np.mean(sttm):6.1f}  fallbacks {fb}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--material", help="the noise spike's make_tts.ps1 folder")
    src.add_argument("--captures", help="a folder of command recordings (<id>.wav + <id>.json)")
    ap.add_argument("--babble-wav", help="another babble track for --material (any rate)")
    ap.add_argument("--commands", help="--captures: the planned lines, one per line")
    ap.add_argument("--trial-log", help="--captures: satellite.log with xvf-trial setting= lines")
    ap.add_argument("--min-match", type=float, default=0.6)
    ap.add_argument("--snr", default="quiet,15,10,5,0", help="comma list; 'quiet' = fan noise only")
    ap.add_argument("--draws", type=int, default=2, help="babble draws per command and SNR")
    ap.add_argument("--modes", default="off,spectral,rnnoise")
    ap.add_argument("--model", default="small.en", help="Whisper model (a CPU-only house's)")
    ap.add_argument("--threads", type=int, default=4, help="Whisper CPU threads")
    ap.add_argument("--seed", type=int, default=22)
    ap.add_argument("--json", help="write the table and every transcript here")
    a = ap.parse_args()
    a.snr = [s.strip() for s in a.snr.split(",") if s.strip()]
    modes = [m.strip() for m in a.modes.split(",") if m.strip()]
    from domovoi.stt_denoise import MODES

    bad = [m for m in modes if m not in MODES]
    if bad:
        raise SystemExit(f"unknown mode(s) {bad}; known: {MODES}")
    low_priority()
    stt = Stt(a.model, a.threads)
    print(f"Whisper {a.model} cpu int8, {a.threads} threads; modes {modes}", flush=True)
    if a.material:
        table, rows = synthetic(a, stt, modes)
        groups, label = a.snr, "SNR dB"
    else:
        table, rows = captures(a, stt, modes)
        groups, label = sorted({g for g, _ in table}), "setting"
    print_table(table, modes, groups, label)
    if a.json:
        Path(a.json).write_text(json.dumps({
            "model": a.model, "threads": a.threads, "modes": modes,
            "table": {f"{g}|{m}": v for (g, m), v in table.items()}, "clips": rows,
        }, indent=1), encoding="utf-8")
        print(f"\n{a.json}")


if __name__ == "__main__":
    main()
