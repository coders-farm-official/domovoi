"""Where a satellite capture's speech stops, as the core can tell it.

A satellite decides when a capture ends: after ``listen.silence_timeout``
(1.2 s by default) of frames its own voice detector calls silence, it sends
``utterance_end``. Every frame is on the core long before that — the
satellite streams its 30 ms frames live — so the core can start
transcribing at the first short pause instead of waiting out the whole
silence (speculative transcription, ``domovoi/streaming.py``). Two things
make that safe, and both live here:

* **When to start.** A new satellite says so itself (``speech_pause``,
  sent at 8 silent frames); for one that doesn't, :class:`LevelPauseDetector`
  finds the first ~240 ms pause after speech from the frames' own
  loudness. Starting too early or too late only wastes a decode or a
  little time — which transcript a turn USES is decided exactly, below.
* **Whether the early transcript covers everything said.** Only when no
  frame the satellite called speech came after the snapshot. A new
  satellite reports its last voiced frame in ``utterance_end``; for an old
  one :func:`last_voiced_from_timeout` works it out from the silence
  timeout it reported in ``config_status``: its capture loop ends exactly
  ``silence_limit`` frames after its last voiced frame
  (``satellite/client.py`` ``_stream_capture``), so the index is
  ``frames - silence_limit - 1``. Frame accounting, not a loudness guess.

Pure functions and a small state machine, no I/O — the unit tests drive
them with synthetic frames.
"""

from __future__ import annotations

import array
import math
from collections import deque

# The satellite's capture frame: 30 ms of 16 kHz mono int16 (960 bytes).
FRAME_MS = 30
PCM_BYTES_PER_MS = 32

# The pause that starts a speculative decode: 8 frames, the same count the
# satellite sends its own `speech_pause` at. Shorter pauses happen inside
# words; much longer ones give back the time this is for.
SPECULATIVE_PAUSE_MS = 240


def pcm_dbfs(data: bytes) -> float:
    """RMS level of 16-bit-LE PCM in dBFS (full scale = 0.0); empty or
    digitally silent → a -120 floor. Stdlib only."""
    n = len(data) // 2
    if n == 0:
        return -120.0
    samples = array.array("h")
    samples.frombytes(data[: n * 2])
    acc = 0
    for s in samples:
        acc += s * s
    rms = math.sqrt(acc / n)
    if rms < 1.0:
        return -120.0
    return 20.0 * math.log10(rms / 32768.0)


def frame_limit(seconds: float) -> int:
    """Frames in ``seconds`` exactly as the satellite counts them
    (``int(seconds * 1000 / FRAME_MS)`` — the same float expression, so
    the truncation matches frame for frame)."""
    return int(seconds * 1000 / FRAME_MS)


def last_voiced_from_timeout(
    frames: int, silence_timeout: float, max_record_seconds: float | None
) -> int | None:
    """Index of the last frame an old satellite's detector called speech,
    worked out from how its capture loop ends, or None when it can't be.

    The loop breaks on the ``silence_limit``-th consecutive silent frame
    after speech, so a capture of ``frames`` frames that ended that way has
    its last voiced frame at ``frames - silence_limit - 1``. A capture that
    hit ``max_record_seconds`` ended some other way — the silence count at
    the end is anything — so it gets None, as does one too short to hold
    both a voiced frame and the silence.
    """
    try:
        silence_limit = frame_limit(float(silence_timeout))
    except (TypeError, ValueError):
        return None
    if silence_limit <= 0:
        return None
    if max_record_seconds is not None:
        try:
            if frames >= frame_limit(float(max_record_seconds)):
                return None
        except (TypeError, ValueError):
            return None
    last = frames - silence_limit - 1
    return last if last >= 0 else None


class LevelPauseDetector:
    """The first short pause after speech, judged by loudness alone.

    For satellites that don't report their own pauses. A frame counts as
    speech when it is within ``rel_db`` of the loudest frame so far in this
    utterance and at least ``floor_margin_db`` above the quietest — the
    utterance's own speech level, not a fixed gate, so a far-field voice
    and a close one both work. Until the frames span ``min_range_db``
    nothing is judged at all (a capture that opens mid-word has no quiet
    frame to measure against yet; the recent frames are judged again once
    it has).

    :meth:`feed` returns True once per silence run: on the frame that makes
    the trailing run of non-speech frames ``pause_ms`` long, when at least
    ``min_speech_ms`` of speech came before it within the recent frames (a
    single click is not somebody talking). Every frame is judged against
    the CURRENT levels, over a short window of recent frames.

    Only a trigger. A detector that fires early or late costs a wasted
    decode or a little time; which transcript a turn uses is decided by
    frame accounting (see the module docstring).
    """

    __slots__ = (
        "pause_ms", "rel_db", "floor_margin_db", "min_range_db", "min_speech_ms",
        "peak", "floor", "in_pause", "_recent",
    )

    # Quieter than this is treated as this: an all-zero frame reads -120
    # and would otherwise pin the floor far below any real room.
    FLOOR_CLAMP_DB = -90.0
    # The window judged on every frame: ~3 s of 30 ms frames.
    WINDOW_FRAMES = 100

    def __init__(
        self,
        *,
        pause_ms: int = SPECULATIVE_PAUSE_MS,
        rel_db: float = 20.0,
        floor_margin_db: float = 9.0,
        min_range_db: float = 12.0,
        min_speech_ms: int = 90,
    ) -> None:
        self.pause_ms = pause_ms
        self.rel_db = rel_db
        self.floor_margin_db = floor_margin_db
        self.min_range_db = min_range_db
        self.min_speech_ms = min_speech_ms
        self.peak = -120.0
        self.floor = 0.0
        # Set on the frame that fires, cleared by the next speech frame.
        self.in_pause = False
        self._recent: deque[tuple[float, float]] = deque(maxlen=self.WINDOW_FRAMES)

    def feed(self, frame: bytes) -> bool:
        d = max(pcm_dbfs(frame), self.FLOOR_CLAMP_DB)
        self._recent.append((d, len(frame) / PCM_BYTES_PER_MS))
        self.peak = max(self.peak, d)
        self.floor = min(self.floor, d)
        if self.peak - self.floor < self.min_range_db:
            return False
        threshold = max(self.floor + self.floor_margin_db, self.peak - self.rel_db)
        if d >= threshold:
            self.in_pause = False
            return False
        if self.in_pause:
            return False
        # The trailing non-speech run, and the speech before it.
        silent_ms = 0.0
        speech_ms = 0.0
        for level, ms in reversed(self._recent):
            if level >= threshold:
                speech_ms += ms
            elif speech_ms == 0.0:
                silent_ms += ms
        if silent_ms >= self.pause_ms and speech_ms >= self.min_speech_ms:
            self.in_pause = True
            return True
        return False
