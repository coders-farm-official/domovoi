"""What voice ID hears of a recording: the speech in it, at one loudness.

A room's recording is not only the command. It starts with a little of the
room before the person spoke and ends with the silence the satellite waited
through to know they had finished (``listen.silence_timeout``, about a
second), and a far-field mic hears a person 10–20 dB quieter than one they
stand next to. Both reach the voice embedding: the quiet tail is averaged
in as if it were voice, and the same person at two loudnesses can give two
different vectors. :func:`prepare` keeps the speech alone, at one level,
with numpy (a 30 ms energy VAD).

Written for Resemblyzer in 1.3.0's first builds, it was measured again for
the ERes2Net model that replaced it (scripts/eval_voice_id.py --tune, step
1): the whole recording separates voices a little better (equal error rate
11.0 % against 15.2 % over the seven simulated rooms), but at the house's
ceiling of 1 % wrong names in every room, embedding the speech alone names
the speaker more often (24.2 % of commands against 20.5 %; trimming only
the silent edges, 0.1 or 0.3 s kept, 17.9 % and 18.4 %), because the room
that sets the ceiling, a TV talking under the command, needs a higher
threshold for the untrimmed recordings. So :func:`for_embedding` is
:func:`prepare`.

It also measures how much of what was heard is the room rather than the
person (:attr:`Prepared.snr_db`): music or a TV under a command colours the
voice the embedding is made from, and voice ID treats such a turn with care
(domovoi/voice_identifier.py).

Pure numpy, no I/O; 16 kHz mono float audio in [-1, 1].
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16_000
FRAME = 480                # 30 ms analysis frames
TARGET_DBFS = -30.0        # the level the speech is set to (Resemblyzer's, kept)
_FLOOR_PCT = 10            # the quietest tenth of a recording is "the room"
_PEAK_PCT = 95
_ABOVE_FLOOR_DB = 6.0      # speech: this far above the room...
_BELOW_PEAK_DB = 35.0      # ...and within this of the loudest speech
_SILENCE_DBFS = -70.0      # a frame below this is silence whatever the rest is
_HANGOVER = 3              # frames kept either side of speech (90 ms)
_MIN_RUN = 3               # a burst shorter than this (90 ms) is a click, not speech
_QUIETEST = 3              # the room's level: its quietest 90 ms


@dataclass(frozen=True)
class Prepared:
    """:func:`prepare`'s result. ``audio``: the speech frames joined, at
    :data:`TARGET_DBFS`; ``speech_sec``: how long that is; ``snr_db``: the
    speech's median level over the recording's quietest 90 ms — low when
    music or a TV ran under the command, high when the room fell quiet even
    for a moment (between words, or after the command)."""

    audio: np.ndarray
    speech_sec: float
    snr_db: float


def _frame_db(wav: np.ndarray) -> np.ndarray:
    n = len(wav) // FRAME
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    frames = wav[: n * FRAME].astype(np.float64).reshape(n, FRAME)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    return 20.0 * np.log10(np.maximum(rms, 1e-7))


def speech_mask(db: np.ndarray) -> np.ndarray:
    """Which 30 ms frames are speech, from their levels (``_frame_db``):
    above the room's floor and near the loudest part, as runs of at least
    :data:`_MIN_RUN` frames, each widened by :data:`_HANGOVER`."""
    if db.size == 0:
        return np.zeros(0, dtype=bool)
    floor = float(np.percentile(db, _FLOOR_PCT))
    peak = float(np.percentile(db, _PEAK_PCT))
    cut = max(floor + _ABOVE_FLOOR_DB, peak - _BELOW_PEAK_DB, _SILENCE_DBFS)
    loud = db > cut
    # Drop bursts too short to be speech.
    mask = np.zeros_like(loud)
    i = 0
    while i < loud.size:
        if not loud[i]:
            i += 1
            continue
        j = i
        while j < loud.size and loud[j]:
            j += 1
        if j - i >= _MIN_RUN:
            mask[i:j] = True
        i = j
    if not mask.any():
        return mask
    # Hangover: keep the soft starts and ends of words.
    widened = mask.copy()
    for k in range(1, _HANGOVER + 1):
        widened[k:] |= mask[:-k]
        widened[:-k] |= mask[k:]
    return widened


def prepare(wav: np.ndarray) -> Prepared:
    """The speech in ``wav`` at :data:`TARGET_DBFS`, and how clear it was.

    A recording with no frame that stands out (all silence, or all one
    level) is returned whole: there is nothing to cut it to, and the
    caller's length check decides whether it can be used."""
    wav = np.asarray(wav, dtype=np.float32)
    db = _frame_db(wav)
    mask = speech_mask(db)
    if mask.any():
        frames = wav[: mask.size * FRAME].reshape(mask.size, FRAME)
        speech = frames[mask].reshape(-1)
        # The room's own level: the quietest moment of the recording. Music
        # or a TV never lets the room go quiet; a person between words (or
        # the second of silence a satellite waits through) does.
        floor = float(np.mean(np.sort(db)[:_QUIETEST]))
        snr = float(np.median(db[mask]) - floor)
    else:
        speech = wav
        snr = 0.0
    rms = float(np.sqrt(np.mean(np.square(speech, dtype=np.float64)))) if speech.size else 0.0
    if rms > 0:
        gain = 10.0 ** ((TARGET_DBFS - 20.0 * np.log10(rms)) / 20.0)
        speech = np.clip(speech * gain, -1.0, 1.0).astype(np.float32)
    return Prepared(audio=speech, speech_sec=speech.size / SAMPLE_RATE, snr_db=snr)


def for_embedding(wav: np.ndarray) -> Prepared:
    """What voice ID embeds of a recording (domovoi/clients/voice_embedder.py):
    :func:`prepare`'s speech alone, at one level (the module docstring says
    why, measured)."""
    return prepare(wav)
