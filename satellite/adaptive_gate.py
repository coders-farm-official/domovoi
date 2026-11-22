"""A per-capture noise gate for noisy rooms (design: rooms with several
satellites, section 5.1, items 1 and 2). NOT wired into the capture loop
yet: nothing imports this module, so it is off.

Today a capture counts a 30 ms frame as speech when it is louder than the
satellite's noise gate (``[noise_gate] dbfs``, set at boot from a quiet room
and nudged every 10 minutes by ``Satellite._maybe_recalibrate``) and
webrtcvad calls it speech (``Satellite._capture``). At a party the chatter
passes both, the capture never hears 1.2 s of silence, runs to its 30 s cap,
and the core then drops the turn as background talk (``domovoi/overheard.py``).

:class:`CaptureGate` replaces the fixed gate for one capture with two rules:

1. **A floor per capture.** The room's level is measured over the ~2 s
   before the wake phrase (the wake loop already keeps those levels), and
   this capture's gate is ``max(noise gate, floor + floor_margin_db)``
   (6 dB; the design said 8, but at 8 a command at 0 dB SNR fell under the
   gate in 11 of 20 harness runs, at 6 in 1 of 20).
   Chatter at the room's level then counts as silence. The last
   ``wake_skip_frames`` before the wake fired are left out: they hold the
   wake phrase itself.
2. **End on the speaker's own level.** Once ``min_voiced_frames`` frames
   were speech, their level (a high percentile) is the command's level, and
   a frame more than ``drop_db`` below it is silence. The silence timeout is
   unchanged, so a short soft syllable never ends a capture. In the
   harness (scripts/dev/noise_spike/endpoint_harness.py) rule 1 does all
   the work at a party; rule 2 only ends quiet-room captures a little
   sooner. At 10 dB it cut soft endings off, so it stays at 15.

The same two numbers make the best-mic score (section 3.2, step 7):
:func:`mic_score` is the command's level over that device's own floor.

Pure arithmetic on frame levels in dBFS (``client._frame_dbfs``); no audio,
no I/O, so it runs the same on a Pi, a PC and in the tests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

FRAME_MS = 30


@dataclass(frozen=True)
class GateSettings:
    #: How much of the time before the wake phrase sets the floor.
    floor_window_frames: int = int(2000 / FRAME_MS)
    #: Frames just before the wake fired that hold the wake phrase (~0.9 s).
    wake_skip_frames: int = int(900 / FRAME_MS)
    #: The floor is this percentile of the window: the median, so a laugh
    #: or a clink in those 2 s does not lift it.
    floor_percentile: float = 50.0
    #: The capture's gate sits this far above the floor.
    floor_margin_db: float = 6.0
    #: A frame this far below the command's own level is silence.
    drop_db: float = 15.0
    #: The command's level is trusted after this much speech (~300 ms).
    min_voiced_frames: int = 10
    #: The command's level is this percentile of its voiced frames.
    level_percentile: float = 90.0
    #: A floor needs at least this many finite frames, else the fixed gate.
    min_floor_frames: int = 20


def _finite(values: Iterable[float]) -> list[float]:
    return [v for v in values if not math.isinf(v) and not math.isnan(v)]


def percentile(values: Iterable[float], pct: float) -> float | None:
    """Nearest-rank percentile of the finite values, or None if there are none."""
    xs = sorted(_finite(values))
    if not xs:
        return None
    k = min(len(xs) - 1, max(0, int(round(pct / 100.0 * (len(xs) - 1)))))
    return xs[k]


def pre_wake_floor(pre_wake_dbfs: list[float], settings: GateSettings = GateSettings()) -> float | None:
    """The room's level just before the wake phrase, or None when there is
    too little of it (a wake straight after boot or after a capture)."""
    end = len(pre_wake_dbfs) - settings.wake_skip_frames
    if end <= 0:
        return None
    window = _finite(pre_wake_dbfs[max(0, end - settings.floor_window_frames):end])
    if len(window) < settings.min_floor_frames:
        return None
    return percentile(window, settings.floor_percentile)


@dataclass
class CaptureGate:
    """One capture's speech gate. Ask :meth:`threshold` before each frame;
    tell :meth:`observe` about every frame the capture called speech."""

    base_gate_dbfs: float
    floor_dbfs: float | None = None
    settings: GateSettings = field(default_factory=GateSettings)
    _voiced: list[float] = field(default_factory=list, repr=False)
    _level: float | None = field(default=None, repr=False)

    @classmethod
    def from_pre_wake(
        cls,
        pre_wake_dbfs: list[float],
        base_gate_dbfs: float,
        settings: GateSettings = GateSettings(),
    ) -> "CaptureGate":
        return cls(base_gate_dbfs, pre_wake_floor(pre_wake_dbfs, settings), settings)

    @property
    def floor_gate_dbfs(self) -> float:
        """Rule 1 alone: the gate before any speech."""
        if self.floor_dbfs is None:
            return self.base_gate_dbfs
        return max(self.base_gate_dbfs, self.floor_dbfs + self.settings.floor_margin_db)

    @property
    def speech_dbfs(self) -> float | None:
        """The command's own level, once there is enough of it."""
        return self._level

    def threshold(self) -> float:
        """The level a frame must reach to count as speech now."""
        gate = self.floor_gate_dbfs
        if self._level is not None:
            gate = max(gate, self._level - self.settings.drop_db)
        return gate

    def observe(self, dbfs: float, voiced: bool) -> None:
        if not voiced or math.isinf(dbfs):
            return
        self._voiced.append(dbfs)
        if len(self._voiced) >= self.settings.min_voiced_frames:
            self._level = percentile(self._voiced, self.settings.level_percentile)

    def report(self) -> dict[str, float | None]:
        """The numbers for ``utterance_start`` / ``utterance_end`` (5.1, item 3)."""
        return {
            "floor_dbfs": None if self.floor_dbfs is None else round(self.floor_dbfs, 1),
            "speech_dbfs": None if self._level is None else round(self._level, 1),
            "gate_dbfs": round(self.threshold(), 1),
        }


def mic_score(
    pre_wake_dbfs: list[float],
    capture_dbfs: list[float],
    *,
    window_frames: int = int(1500 / FRAME_MS),
    settings: GateSettings = GateSettings(),
) -> float | None:
    """Best-mic score for one device: the command's level (a high percentile
    of the capture's first ``window_frames``) over that device's own
    pre-wake floor, in dB. Higher is better; None if either is unknown.
    Relative on purpose: a phone's or the XVF3800's AGC lifts the level,
    much less the level over its own floor."""
    floor = pre_wake_floor(pre_wake_dbfs, settings)
    level = percentile(capture_dbfs[:window_frames], settings.level_percentile)
    if floor is None or level is None:
        return None
    return level - floor
