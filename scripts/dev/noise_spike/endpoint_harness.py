"""Offline harness: does a voice command spoken over party chatter get
captured and ended cleanly by the satellite? (Spike for the design's
noisy-room section, 5.1 items 1-2, and the best-mic score of 3.2.)

A dev tool, not part of the product. It runs the satellite's REAL capture
loop, ``satellite.client.Satellite._capture``, unmodified, on synthetic
audio: a stand-in object supplies the queue of 30 ms frames and swallows
what the loop sends. Three gates are compared:

* ``today_cold``: the gate as boot calibration leaves it in a quiet room
  (``Satellite._derive_threshold``: median + 5 dB), i.e. a party that
  started after the last recalibration.
* ``today_warm``: the same after the party has run long enough for the
  10-minute EWMA (``Satellite._maybe_recalibrate``, real code, run 12 times
  on 30 s of chatter) to settle. Above the -28 dBFS safety cap it stays put.
* ``new``: ``satellite.adaptive_gate.CaptureGate`` (pre-wake floor + 6 dB,
  then the speaker's own level - 15 dB), on top of the cold gate. It is
  plugged in from outside: the stand-in's ``cfg.noise_gate_dbfs`` returns
  the gate's threshold and the VAD wrapper feeds it every voiced frame, so
  the loop itself is the shipping one.

Material: ``make_tts.ps1`` (Windows' built-in voices; nothing recorded or
downloaded). Babble = 6 synthetic talkers, each pitch-shifted, offset and
re-levelled. SNR = the command's active-speech level over the babble's
long-term level.

Needs numpy, scipy, soundfile, webrtcvad-wheels, websockets, requests (the
client's imports); sounddevice is stubbed. Run from the repo root:

    python scripts/dev/noise_spike/endpoint_harness.py --material <folder> [--json out.json]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import queue
import random
import sys
import threading
import types
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))
import webrtcvad  # noqa: E402  (real one: the point is the real VAD)

from satellite import adaptive_gate, client  # noqa: E402

_REAL_VAD = webrtcvad.Vad

SR = 16_000
FRAME = SR * 30 // 1000
CMD_DBFS = -28.0         # the command's active-speech level at the near mic
QUIET_FLOOR_DBFS = -62.0  # a quiet room: fridge/fan noise
SILENCE_TIMEOUT = 1.2
MAX_RECORD = 30.0


def low_priority() -> None:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x40)
    else:
        os.nice(19)


# ── material ────────────────────────────────────────────────────────────────

def load(path: str) -> np.ndarray:
    x, sr = sf.read(path, dtype="float32")
    assert sr == SR, path
    return x if x.ndim == 1 else x.mean(axis=1)


def trim(x: np.ndarray) -> np.ndarray:
    """Cut leading/trailing silence (frames 40 dB under the loudest)."""
    n = len(x) // FRAME
    e = np.array([np.sqrt(np.mean(x[i * FRAME:(i + 1) * FRAME] ** 2)) + 1e-9 for i in range(n)])
    on = np.where(20 * np.log10(e / e.max()) > -40)[0]
    return x[on[0] * FRAME:(on[-1] + 1) * FRAME]


def active_rms(x: np.ndarray) -> float:
    n = len(x) // FRAME
    e = np.array([np.sqrt(np.mean(x[i * FRAME:(i + 1) * FRAME] ** 2)) for i in range(n)])
    e = e[20 * np.log10(e / e.max() + 1e-12) > -30]
    return float(np.sqrt(np.mean(e ** 2)))


def set_level(x: np.ndarray, dbfs: float, active: bool = True) -> np.ndarray:
    rms = active_rms(x) if active else float(np.sqrt(np.mean(x ** 2)))
    return x * (10 ** (dbfs / 20) / rms)


def pink(n: int, rng: np.random.Generator) -> np.ndarray:
    w = rng.standard_normal(n)
    f = np.fft.rfft(w)
    f /= np.sqrt(np.maximum(np.arange(len(f)), 1))
    return np.fft.irfft(f, n).astype(np.float32)


def babble(n: int, talkers: list[np.ndarray], rng: np.random.Generator, k: int = 6) -> np.ndarray:
    out = np.zeros(n, dtype=np.float32)
    for i in range(k):
        src = talkers[rng.integers(len(talkers))]
        up, down = [(17, 20), (19, 20), (21, 20), (23, 20), (9, 10), (11, 10)][rng.integers(6)]
        s = resample_poly(src, up, down).astype(np.float32)  # pitch + pace shift
        s = np.tile(s, int(np.ceil((n + len(s)) / len(s))))
        start = rng.integers(len(s) - n)
        seg = s[start:start + n]
        out += seg / (np.sqrt(np.mean(seg ** 2)) + 1e-9) * 10 ** (rng.uniform(-4, 4) / 20)
    return out


def dbfs_frames(x: np.ndarray) -> list[float]:
    pcm = np.clip(x * 32767, -32768, 32767).astype(np.int16)
    return [client._frame_dbfs(pcm[i * FRAME:(i + 1) * FRAME].tobytes()) for i in range(len(pcm) // FRAME)]


def to_frames(x: np.ndarray) -> list[bytes]:
    pcm = np.clip(x * 32767, -32768, 32767).astype(np.int16)
    return [pcm[i * FRAME:(i + 1) * FRAME].tobytes() for i in range(len(pcm) // FRAME)]


# ── one scene ───────────────────────────────────────────────────────────────

class Scene:
    """Background before and after a wake phrase, a command after it.
    Times in frames; the capture starts at ``t0``."""

    def __init__(self, wake, cmd, bg, *, cmd_gain_db=0.0, pre_s=3.0):
        pre, gap, lead = int(pre_s * SR), int(0.2 * SR), int(0.25 * SR)
        self.t0 = (pre + len(wake) + gap) // FRAME
        cmd_start = self.t0 * FRAME + lead
        n = self.t0 * FRAME + int((MAX_RECORD + 2) * SR)
        x = np.zeros(n, dtype=np.float32)
        x[pre:pre + len(wake)] += wake * 10 ** (cmd_gain_db / 20)
        x[cmd_start:cmd_start + len(cmd)] += cmd * 10 ** (cmd_gain_db / 20)
        x += bg[:n]
        self.x = x
        self.cmd_end = (cmd_start + len(cmd)) // FRAME - self.t0  # frames into the capture

    def pre_wake_dbfs(self) -> list[float]:
        return dbfs_frames(self.x[: self.t0 * FRAME])

    def capture_frames(self) -> list[bytes]:
        return to_frames(self.x[self.t0 * FRAME:])


# ── the real capture loop on a stand-in ─────────────────────────────────────

class _Cfg(types.SimpleNamespace):
    gate = None  # a CaptureGate, or None for the fixed gate

    @property
    def noise_gate_dbfs(self):  # read by _capture before every frame
        return self.gate.threshold() if self.gate is not None else self.fixed

    @noise_gate_dbfs.setter
    def noise_gate_dbfs(self, v):
        self.fixed = v


class _Vad:
    """Real webrtcvad; tells the CaptureGate about each voiced frame."""

    def __init__(self, mode, cfg):
        self._v, self._cfg = _REAL_VAD(mode), cfg

    def is_speech(self, frame, rate):
        r = self._v.is_speech(frame, rate)
        if r and self._cfg.gate is not None:
            self._cfg.gate.observe(client._frame_dbfs(frame), True)
        return r


def _stand_in(fixed_gate: float, gate) -> types.SimpleNamespace:
    cfg = _Cfg(vad_aggressiveness=2, silence_timeout=SILENCE_TIMEOUT, max_record_seconds=MAX_RECORD,
               noise_gate_auto_calibrate=True, noise_gate_noisy_capture_dbfs=-10.0,
               noise_gate_calibration_margin_db=5.0, speech_pause_ms=None)
    cfg.noise_gate_dbfs = fixed_gate
    cfg.gate = gate
    s = types.SimpleNamespace(
        cfg=cfg, raw_q=queue.Queue(), shutdown_event=threading.Event(), _end_capture=threading.Event(),
        _leds=types.SimpleNamespace(set_state=lambda *_: None), _utt_seq=1, _utt_started=None,
        _response_starts=0, _core_features=set(), sent_text=[], _reply_wait_since=None,
    )
    s._ack_fields = lambda: {}
    s._speech_pause_frames = lambda: 8
    s._emit_audio = lambda f: True
    s._emit_text = lambda p: (s.sent_text.append(p), True)[1]
    s._capture_clock = lambda started: {}
    s._stop_own_music_under_capture = lambda utt: False
    s._arm_reply_deadline = lambda: None
    s._maybe_recalibrate = lambda samples: None
    return s


def run_capture(scene: Scene, fixed_gate: float, gate=None) -> dict:
    s = _stand_in(fixed_gate, gate)
    for f in scene.capture_frames():
        s.raw_q.put(f)
    client.webrtcvad.Vad = lambda mode: _Vad(mode, s.cfg)
    try:
        client.Satellite._capture(s, [], None)
    finally:
        client.webrtcvad.Vad = _REAL_VAD
    end = next(p for p in s.sent_text if p.get("type") == "utterance_end")
    return {"exit": end["exit_reason"], "frames": end["frames"], "last_voiced": end["last_voiced_frame"],
            "voiced": end["voiced_frames"]}


def classify(r: dict, cmd_end: int) -> str:
    if r["exit"] == "max_record_seconds":
        return "cap" if r["voiced"] else "cap_nospeech"
    if r["frames"] < cmd_end:
        return "cut"
    late = r["frames"] - cmd_end - int(SILENCE_TIMEOUT * 1000 / 30)
    return "clean" if late <= int(1000 / 30) else "late"


# ── gates ───────────────────────────────────────────────────────────────────

class _Cal(types.SimpleNamespace):
    _NOISE_GATE_SAFETY_CAP_DBFS = client.Satellite._NOISE_GATE_SAFETY_CAP_DBFS


def cold_gate(quiet: np.ndarray) -> float:
    cal = _Cal(cfg=types.SimpleNamespace(noise_gate_calibration_margin_db=5.0))
    g = client.Satellite._derive_threshold(cal, dbfs_frames(quiet[: 5 * SR]))
    return g if g is not None else -45.0


def warm_gate(start: float, bg_only: np.ndarray) -> float:
    cal = _Cal(cfg=types.SimpleNamespace(noise_gate_calibration_margin_db=5.0, noise_gate_auto_calibrate=True,
                                         noise_gate_dbfs=start))
    cal._derive_threshold = lambda samples: client.Satellite._derive_threshold(cal, samples)
    lv = dbfs_frames(bg_only[: 30 * SR])
    for _ in range(12):
        client.Satellite._maybe_recalibrate(cal, lv)
    return cal.cfg.noise_gate_dbfs


# ── the study ───────────────────────────────────────────────────────────────

SNRS = ["quiet", 15, 10, 5, 0, -5]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--material", required=True)
    ap.add_argument("--json")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--margin", type=float, default=adaptive_gate.GateSettings.floor_margin_db)
    ap.add_argument("--drop", type=float, default=adaptive_gate.GateSettings.drop_db)
    ap.add_argument("--no-mic", action="store_true", help="skip the best-mic study")
    a = ap.parse_args()
    low_priority()
    gs = adaptive_gate.GateSettings(floor_margin_db=a.margin, drop_db=a.drop)
    print(f"new gate: floor + {a.margin} dB, speaker level - {a.drop} dB")
    rng = np.random.default_rng(a.seed)
    random.seed(a.seed)
    m = Path(a.material)
    cmds = {Path(p).stem: set_level(trim(load(p)), CMD_DBFS) for p in sorted(glob.glob(str(m / "cmd/*.wav")))}
    wakes = {Path(p).stem: set_level(trim(load(p)), CMD_DBFS) for p in glob.glob(str(m / "wake/*.wav"))}
    talkers = [load(p) for p in sorted(glob.glob(str(m / "babble/*.wav")))]
    n = int(40 * SR)

    # Variants of a command: as is; with a 0.7 s mid-command pause; with a
    # second half 12 dB softer (turning away). Quiet-room cut-off checks.
    names = list(cmds)
    def variant(kind, i):
        c = cmds[names[i]]
        if kind == "plain":
            return c
        other = cmds[names[(i + 3) % len(names)]]
        gap = np.zeros(int(0.7 * SR), dtype=np.float32)
        if kind == "pause":
            return np.concatenate([c, gap, other])
        return np.concatenate([c, np.zeros(int(0.15 * SR), dtype=np.float32), other * 10 ** (-12 / 20)])

    results = defaultdict(Counter)
    lat = defaultdict(list)
    for snr in SNRS:
        for kind in ("plain", "pause", "soft_tail"):
            for i, name in enumerate(names):
                quiet = pink(n, rng)
                quiet = set_level(quiet, QUIET_FLOOR_DBFS, active=False)
                if snr == "quiet":
                    bg, party = quiet, None
                else:
                    party = set_level(babble(n, talkers, rng), CMD_DBFS - snr, active=False)
                    bg = quiet + party
                wake = wakes[name.split("_")[0]]
                sc = Scene(wake, variant(kind, i), bg)
                g_cold = cold_gate(quiet)
                g_warm = g_cold if party is None else warm_gate(g_cold, quiet + set_level(
                    babble(n, talkers, rng), CMD_DBFS - snr, active=False))
                runs = {
                    "today_cold": run_capture(sc, g_cold),
                    "today_warm": run_capture(sc, g_warm),
                    "new": run_capture(sc, g_cold, adaptive_gate.CaptureGate.from_pre_wake(sc.pre_wake_dbfs(), g_cold, gs)),
                }
                for k, r in runs.items():
                    c = classify(r, sc.cmd_end)
                    results[(snr, kind, k)][c] += 1
                    if c in ("clean", "late"):
                        lat[(snr, k)].append((r["frames"] - sc.cmd_end) * 30)

    print("\n== endpointing: outcome counts per SNR / command kind (n=%d each) ==" % len(names))
    print(f"{'snr':>6} {'kind':>9} | {'today_cold':<28} | {'today_warm':<28} | {'new':<28}")
    for snr in SNRS:
        for kind in ("plain", "pause", "soft_tail"):
            row = []
            for k in ("today_cold", "today_warm", "new"):
                c = results[(snr, kind, k)]
                row.append(" ".join(f"{o}:{c[o]}" for o in ("clean", "late", "cut", "cap", "cap_nospeech") if c[o]))
            print(f"{str(snr):>6} {kind:>9} | {row[0]:<28} | {row[1]:<28} | {row[2]:<28}")
    print("\nmedian capture end after the command ends (ms), clean+late only:")
    for snr in SNRS:
        print(f"{str(snr):>6} " + "  ".join(
            f"{k}={int(np.median(lat[(snr, k)])) if lat[(snr, k)] else '-'}" for k in ("today_cold", "today_warm", "new")))

    if a.no_mic:
        return
    # ── best mic ────────────────────────────────────────────────────────────
    # Two satellites hear the same command. Near: command at SNR s. Far: the
    # command `delta` dB weaker, the crowd 0-3 dB louder there. Each device
    # has its own gain (AGC, mic gain): +-10 dB on everything it hears.
    print("\n== best mic: pick accuracy, relative score vs raw level ==")
    pick = defaultdict(lambda: [0, 0, 0])
    for s in (10, 5, 0, -5):
        for delta in (3, 6, 10):
            for i, name in enumerate(names):
                cmd, wake = cmds[name], wakes[name.split("_")[0]]
                scores = []
                for far in (False, True):
                    crowd_db = rng.uniform(0, 3) if far else 0.0
                    bg = set_level(pink(n, rng), QUIET_FLOOR_DBFS, active=False) + set_level(
                        babble(n, talkers, rng), CMD_DBFS - s + crowd_db, active=False)
                    sc = Scene(wake, cmd, bg, cmd_gain_db=-delta if far else 0.0)
                    gain = 10 ** (rng.uniform(-10, 10) / 20)
                    sc.x = sc.x * gain
                    pre, cap = sc.pre_wake_dbfs(), dbfs_frames(sc.x[sc.t0 * FRAME:sc.t0 * FRAME + int(1.5 * SR)])
                    scores.append((adaptive_gate.mic_score(pre, cap), adaptive_gate.percentile(cap, 90)))
                rel_ok = scores[0][0] > scores[1][0]
                raw_ok = scores[0][1] > scores[1][1]
                p = pick[(s, delta)]
                p[0] += int(rel_ok)
                p[1] += int(raw_ok)
                p[2] += 1
    print(f"{'snr':>5} {'delta':>5} | relative  raw-level")
    for (s, d), (r, w, t) in sorted(pick.items(), key=lambda kv: (-kv[0][0], kv[0][1])):
        print(f"{s:>5} {d:>5} | {r}/{t:<7} {w}/{t}")

    if a.json:
        Path(a.json).write_text(json.dumps({
            "endpointing": {f"{k[0]}|{k[1]}|{k[2]}": dict(v) for k, v in results.items()},
            "best_mic": {f"{k[0]}|{k[1]}": v for k, v in pick.items()},
        }, indent=1))


if __name__ == "__main__":
    main()
