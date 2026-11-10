"""When a wake-word score counts as a wake (false wakes, 2026-11-09).

openWakeWord scores every 80 ms chunk. The satellite used to wake on the
first chunk over ``[wake] threshold`` and listen again the instant a turn
ended, so a one-chunk blip from the TV woke the room, and media that kept
saying the wake word re-woke it seconds after every turn. Two rules now,
both defaults with no setup:

* **Patience** (``[wake] patience_frames``, default 2): the score must be
  over the threshold on that many chunks IN A ROW. A real wake word keeps
  the model's score high for several chunks (its features span about a
  second of audio), so it still wakes; a single-chunk blip does not. The
  cost is the extra chunks: 80 ms per chunk past the first (2 = +80 ms).
* **Cooldown** (``[wake] cooldown_sec``, default 2.0): no wake for that long
  after the satellite goes back to listening for the wake word, i.e. after
  every turn. The room's own reply tail, and media repeating the word right
  after a turn, can't re-wake it. A person rarely says the wake word within
  two seconds of the reply ending (the word itself takes most of a second
  to say, and its score peaks at its end).

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


class WakeGate:
    """Decides, chunk by chunk, whether the wake word was said.

    ``feed(score, now)`` is True on the chunk that completes a run of
    ``patience`` chunks over ``threshold`` outside the cooldown; the run's
    peak score and length are then in :attr:`last_peak` / :attr:`last_run`.
    A run that ended short of the patience is kept in
    :attr:`last_short_run` (peak, length) once, for the log."""

    def __init__(self, threshold: float, patience: int = PATIENCE_DEFAULT,
                 cooldown_sec: float = COOLDOWN_SEC_DEFAULT) -> None:
        self.threshold = float(threshold)
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

    def quiet_for(self, now: float) -> None:
        """The satellite is back to listening for the wake word (a turn just
        ended): no wake until the cooldown has passed."""
        self._quiet_until = now + self.cooldown_sec
        self.cooldown_peak = None
        self._reset_run()

    def in_cooldown(self, now: float) -> bool:
        return now < self._quiet_until

    def _reset_run(self) -> None:
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
            self._reset_run()
            return False
        if score > self.threshold:
            self._run += 1
            self._peak = max(self._peak, score)
            if self._run >= self.patience:
                self.last_peak = self._peak
                self.last_run = self._run
                self._reset_run()
                return True
            return False
        if self._run:
            self.last_short_run = (self._peak, self._run)
        self._reset_run()
        return False

    def take_short_run(self) -> tuple[float, int] | None:
        """The last run that stayed short of the patience, once."""
        short, self.last_short_run = self.last_short_run, None
        return short

    def take_cooldown_peak(self) -> float | None:
        """The highest over-threshold score the cooldown swallowed, once
        the cooldown is over (None while it runs, or when there was none)."""
        peak, self.cooldown_peak = self.cooldown_peak, None
        return peak
