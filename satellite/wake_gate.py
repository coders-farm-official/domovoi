"""When a wake-word score counts as a wake (false wakes, 2026-11-09).

openWakeWord scores every 80 ms chunk. The satellite used to wake on the
first chunk over ``[wake] threshold`` and listen again the instant a turn
ended, so a one-chunk blip from the TV woke the room, and media that kept
saying the wake word re-woke it seconds after every turn. Two rules now,
both defaults with no setup:

* **Patience** (``[wake] patience_frames``, default 2): the score must stay
  up on that many chunks IN A ROW and reach the threshold on at least one
  of them. "Up" is a floor below the threshold, ``max(0.3, threshold x
  0.6)`` (never above the threshold), so a far-field wake word whose peak
  just clears a raised threshold (0.85 between two chunks of 0.6, at a
  threshold of 0.8) still wakes, while a lone blip (0.85 between chunks
  under 0.2) does not. A real wake word keeps the score raised for several
  chunks (the model's features span about a second); the cost is 80 ms per
  chunk of patience past the first (2 = +80 ms). Only the main wake loop
  uses it: a wake word said to interrupt a reply (barge-in) or during a
  drop-in call still wakes on one chunk.
* **Cooldown** (``[wake] cooldown_sec``, default 2.0): after a turn that
  went wrong — the core ignored it as background talk, the capture was cut
  off at the recording cap, or it ended in an error or no reply — no wake
  for that long, so media repeating the word can't start the next one at
  once. After an ordinary answered turn there is none: "hey Jarvis" right
  after a reply still works.

:class:`WakeGate` is pure (no audio, no clock of its own), so both rules are
unit-tested; the mic thread feeds it each chunk's score and the time.
"""

from __future__ import annotations

import math
from typing import Any

PATIENCE_DEFAULT = 2
PATIENCE_MIN = 1
PATIENCE_MAX = 5
COOLDOWN_SEC_DEFAULT = 2.0
COOLDOWN_SEC_MAX = 10.0
#: openWakeWord's chunk (1280 samples at 16 kHz): what one frame of patience costs.
CHUNK_MS = 80


def patience_setting(value: Any) -> int:
    """``[wake] patience_frames`` as the gate takes it: a whole number of
    chunks, PATIENCE_MIN..PATIENCE_MAX; missing or unusable is the default."""
    if isinstance(value, bool) or value is None:
        return PATIENCE_DEFAULT
    try:
        n = int(value)
    except (TypeError, ValueError):
        return PATIENCE_DEFAULT
    if isinstance(value, float) and value != n:
        return PATIENCE_DEFAULT
    return min(PATIENCE_MAX, max(PATIENCE_MIN, n))


def cooldown_setting(value: Any) -> float:
    """``[wake] cooldown_sec``: seconds, 0..COOLDOWN_SEC_MAX (0 turns it
    off); missing or unusable is the default."""
    if isinstance(value, bool) or value is None:
        return COOLDOWN_SEC_DEFAULT
    try:
        s = float(value)
    except (TypeError, ValueError):
        return COOLDOWN_SEC_DEFAULT
    if not math.isfinite(s):
        return COOLDOWN_SEC_DEFAULT
    return min(COOLDOWN_SEC_MAX, max(0.0, s))


def floor_for(threshold: float) -> float:
    """The score a chunk must keep to count toward the patience run:
    ``max(0.3, threshold x 0.6)``, never above the threshold."""
    t = float(threshold)
    return min(t, max(0.3, t * 0.6))


class WakeGate:
    """Decides, chunk by chunk, whether the wake word was said.

    ``feed(score, now)`` is True on the chunk that completes a run of
    ``patience`` chunks over the floor (:func:`floor_for`) whose peak is
    over ``threshold``, outside the cooldown; the run's peak score and
    length are then in :attr:`last_peak` / :attr:`last_run`. A run that
    reached the threshold but ended short of the patience is kept in
    :attr:`last_short_run` (peak, length) once, for the log."""

    def __init__(self, threshold: float, patience: int = PATIENCE_DEFAULT,
                 cooldown_sec: float = COOLDOWN_SEC_DEFAULT) -> None:
        self.threshold = float(threshold)
        self.floor = floor_for(self.threshold)
        self.patience = patience_setting(patience)
        self.cooldown_sec = cooldown_setting(cooldown_sec)
        self._quiet_until = 0.0
        self._run = 0
        self._peak = 0.0
        self.last_peak: float | None = None
        self.last_run = 0
        self.last_short_run: tuple[float, int] | None = None
        #: The highest score heard during the cooldown since it started
        #: (None: nothing over the threshold), for the log.
        self.cooldown_peak: float | None = None

    def quiet_for(self, now: float, seconds: float | None = None) -> None:
        """No wake until ``seconds`` (default: the cooldown) from ``now``."""
        span = self.cooldown_sec if seconds is None else max(0.0, float(seconds))
        self._quiet_until = now + span
        self.cooldown_peak = None
        self.reset()

    def in_cooldown(self, now: float) -> bool:
        return now < self._quiet_until

    def reset(self) -> None:
        """Forget the run in progress (a failed prediction, a cooldown)."""
        self._run = 0
        self._peak = 0.0

    def feed(self, score: float, now: float) -> bool:
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0
        if self.in_cooldown(now):
            if score > self.threshold:
                self.cooldown_peak = max(self.cooldown_peak or 0.0, score)
            self.reset()
            return False
        if score > self.floor or score > self.threshold:
            self._run += 1
            self._peak = max(self._peak, score)
            if self._run >= self.patience and self._peak > self.threshold:
                self.last_peak = self._peak
                self.last_run = self._run
                self.reset()
                return True
            return False
        if self._run and self._peak > self.threshold:
            self.last_short_run = (self._peak, self._run)
        self.reset()
        return False

    def take_short_run(self) -> tuple[float, int] | None:
        """The last run that reached the threshold but stayed short of the
        patience, once."""
        short, self.last_short_run = self.last_short_run, None
        return short

    def take_cooldown_peak(self) -> float | None:
        """The highest over-threshold score the cooldown swallowed, once
        the cooldown is over (None while it runs, or when there was none)."""
        peak, self.cooldown_peak = self.cooldown_peak, None
        return peak
