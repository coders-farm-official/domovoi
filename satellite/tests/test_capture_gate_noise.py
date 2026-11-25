"""The per-capture gate in the real capture loop (noisy rooms, stage 1).

`Satellite._capture` with `[listen] adaptive_gate` on uses
satellite/adaptive_gate.py's `CaptureGate`: the room's level just before
the wake word + 6 dB, then the speaker's own level - 15 dB. The noise
spike (scripts/dev/noise_spike/endpoint_harness.py) measured it on TTS
commands over synthetic babble; that material is made on a Windows box
and not in the repo, so these are small synthetic scenes of the same
shape, built from frame levels:

* a wake phrase after 3 s of room, then a command of syllables at
  -28 dBFS (plain; with a 0.7 s pause in it; with a second half 12 dB
  softer), then room until the cap;
* the room: a quiet one at -62 dBFS, or a party at -28 - SNR dBFS whose
  level moves +-4 dB frame to frame;
* a VAD that calls every non-silent frame speech, as webrtcvad does for
  babble: only the gate tells speech from the room.

Outcomes as the spike counts them: "clean" ends within 1 s of the silence
timeout after the command, "cut" before the command ended, "cap" at the
recording cap. The quiet room must come out 60/60 clean with the gate on,
exactly as with it off (the condition for keeping it on by default).
"""

from __future__ import annotations

import queue
import threading
import types
from collections import Counter

import numpy as np
import pytest

from satellite import adaptive_gate
from satellite.tests._client_import import import_client

client = import_client()

FRAME = client.FRAME_SAMPLES
CMD_DBFS = -28.0
QUIET_DBFS = -62.0
SILENCE_TIMEOUT = 1.2
MAX_RECORD = 10.0
SILENCE_FRAMES = int(SILENCE_TIMEOUT * 1000 / 30)
_UNIT = np.random.default_rng(1).standard_normal((32, FRAME))
_UNIT /= np.sqrt(np.mean(_UNIT ** 2, axis=1, keepdims=True))


def _frame(dbfs: float, i: int) -> bytes:
    amp = 32767.0 * 10 ** (dbfs / 20)
    return np.clip(_UNIT[i % 32] * amp, -32768, 32767).astype(np.int16).tobytes()


def _sum(a: float, b: float) -> float:
    return 10 * np.log10(10 ** (a / 10) + 10 ** (b / 10))


def _syllables(rng: np.random.Generator, seconds: float, level: float) -> list[float | None]:
    """Frame levels of speech: syllables of 5-8 frames around ``level``,
    1-3 frame dips between them (None: no speech in that frame)."""
    out: list[float | None] = []
    while len(out) < seconds * 1000 / 30:
        out += [level + rng.normal(0, 2.5) for _ in range(rng.integers(5, 9))]
        out += [None] * int(rng.integers(1, 4))
    return out[:-1] if out[-1] is None else out


def _command(rng: np.random.Generator, kind: str) -> list[float | None]:
    first = _syllables(rng, rng.uniform(1.2, 2.4), CMD_DBFS)
    if kind == "plain":
        return first
    if kind == "pause":
        return first + [None] * int(700 / 30) + _syllables(rng, rng.uniform(0.6, 1.2), CMD_DBFS)
    return first + [None] * 5 + _syllables(rng, rng.uniform(0.6, 1.2), CMD_DBFS - 12)


def _room(rng: np.random.Generator, snr: float | None, n: int) -> list[float]:
    if snr is None:
        return list(QUIET_DBFS + rng.uniform(-1, 1, n))
    party = CMD_DBFS - snr + rng.uniform(-4, 4, n)
    return [_sum(QUIET_DBFS, p) for p in party]


class Scene:
    def __init__(self, rng: np.random.Generator, snr: float | None, kind: str) -> None:
        pre = _room(rng, snr, 100)
        wake = [_sum(CMD_DBFS + rng.normal(0, 2), r) for r in _room(rng, snr, 30)]
        self.pre_wake = pre + wake
        cmd = [None] * 8 + _command(rng, kind)
        self.cmd_end = len(cmd)
        n = int((MAX_RECORD + 2) * 1000 / 30)
        room = _room(rng, snr, n)
        levels = [room[i] if (i >= len(cmd) or cmd[i] is None) else _sum(cmd[i], room[i])
                  for i in range(n)]
        self.frames = [_frame(d, i) for i, d in enumerate(levels)]


class _AnyVad:
    def __init__(self, mode: int) -> None:
        pass

    def is_speech(self, frame: bytes, rate: int) -> bool:
        return any(frame)


def _run(scene: Scene, *, adaptive: bool, fixed_gate: float) -> str:
    cfg = types.SimpleNamespace(
        vad_aggressiveness=2, silence_timeout=SILENCE_TIMEOUT, max_record_seconds=MAX_RECORD,
        noise_gate_auto_calibrate=False, noise_gate_dbfs=fixed_gate, adaptive_gate=adaptive,
        speech_pause_ms=None,
    )
    sent: list[dict] = []
    s = types.SimpleNamespace(
        cfg=cfg, raw_q=queue.Queue(), shutdown_event=threading.Event(), _end_capture=threading.Event(),
        _leds=types.SimpleNamespace(set_state=lambda *_: None), _utt_seq=1, _utt_started=None,
        _response_starts=0, _core_features=frozenset(), _reply_wait_since=None,
        _pre_wake_dbfs=[client._frame_dbfs(_frame(d, i)) for i, d in enumerate(scene.pre_wake)],
    )
    s._ack_fields = lambda: {}
    s._speech_pause_frames = lambda: 8
    s._emit_audio = lambda f: True
    s._emit_text = lambda p: (sent.append(p), True)[1]
    s._capture_clock = lambda started: {}
    s._stop_own_music_under_capture = lambda utt: False
    s._arm_reply_deadline = lambda: None
    s._maybe_recalibrate = lambda samples: None
    s._capture_gate = lambda pre: client.Satellite._capture_gate(s, pre)
    for f in scene.frames:
        s.raw_q.put(f)
    client.Satellite._capture(s, [], None)
    end = next(p for p in sent if p.get("type") == "utterance_end")
    if end["exit_reason"] == "max_record_seconds":
        return "cap"
    if end["frames"] < scene.cmd_end:
        return "cut"
    late = end["frames"] - scene.cmd_end - SILENCE_FRAMES
    return "clean" if late <= int(1000 / 30) else "late"


#: The gate a quiet room's boot calibration leaves (`_derive_threshold`:
#: median + 5 dB).
COLD_GATE = QUIET_DBFS + 5.0
KINDS = ("plain", "pause", "soft_tail")


def _study(snr: float | None, *, adaptive: bool, n: int = 20, seed: int = 7,
           kinds: tuple[str, ...] = KINDS) -> Counter:
    rng = np.random.default_rng(seed)
    out: Counter = Counter()
    for kind in kinds:
        for _ in range(n):
            out[_run(Scene(rng, snr, kind), adaptive=adaptive, fixed_gate=COLD_GATE)] += 1
    return out


@pytest.fixture(autouse=True)
def _vad(monkeypatch):
    monkeypatch.setattr(client, "webrtcvad", types.SimpleNamespace(Vad=_AnyVad))


def test_the_quiet_room_is_60_of_60_clean_with_the_gate_and_without():
    assert _study(None, adaptive=False) == Counter(clean=60)
    assert _study(None, adaptive=True) == Counter(clean=60)


@pytest.mark.parametrize("snr", [15, 10, 5])
def test_at_a_party_the_gate_ends_the_command_where_the_fixed_gate_runs_to_the_cap(snr):
    # Today every one of them runs to the cap (and the core then drops it as
    # background talk). With the gate a plain command, and one with a pause
    # in it, ends cleanly.
    assert _study(snr, adaptive=False) == Counter(cap=60)
    assert _study(snr, adaptive=True, kinds=("plain", "pause")) == Counter(clean=40)


@pytest.mark.parametrize("snr, clean", [(15, 20), (10, 10), (5, 8)])
def test_a_tail_softer_than_the_crowd_may_be_cut_but_never_runs_to_the_cap(snr, clean):
    # A second half 12 dB softer sits under the chatter from SNR 10 down:
    # the capture can end after the loud half ("cut"), never at the cap.
    got = _study(snr, adaptive=True, kinds=("soft_tail",))
    assert got["clean"] >= clean and got["cap"] == 0, got


def test_at_0_db_snr_most_commands_still_end_before_the_cap():
    # The spike's edge (6 dB margin): the command is as loud as the crowd.
    # Measured here: 23 clean, 36 cut, 1 cap of 60; today: 60 cap.
    got = _study(0, adaptive=True)
    assert got["cap"] <= 3 and got["clean"] >= 15, got


def test_the_gate_is_off_when_the_setting_is():
    sat = types.SimpleNamespace(cfg=types.SimpleNamespace(adaptive_gate=False, noise_gate_dbfs=-50.0))
    assert client.Satellite._capture_gate(sat, [-40.0] * 100) is None
    sat.cfg.adaptive_gate = True
    gate = client.Satellite._capture_gate(sat, [-40.0] * 100)
    assert isinstance(gate, adaptive_gate.CaptureGate) and gate.floor_dbfs == -40.0


def test_the_setting_defaults_on_and_reads_from_listen():
    assert client.Config.from_toml({}).adaptive_gate is True
    assert client.Config.from_toml({"listen": {"adaptive_gate": False}}).adaptive_gate is False
