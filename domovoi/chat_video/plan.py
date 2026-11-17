"""The job's plan and its time estimate: pure functions, no I/O.

**Frames** (:func:`candidate_times`, :func:`pick_frames`): a frame at the
start, one at each scene change, and one wherever no change came for
:data:`limits.FILL_EVERY_SEC`; frames that look the same as the one kept
before them go (their fingerprints differ by a few bits at most); past
:data:`limits.MAX_FRAMES` an even spread of the rest is kept.

**The estimate** (:func:`estimate`) starts from fixed guesses for a server
with no graphics card and is refined by measured speed: the speeds of this
server's earlier jobs (:func:`speeds_from_stats`) replace the guesses, and
during a job its own measured steps replace those. "Watching your video…
about N minutes" is :func:`progress_line`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from domovoi.chat_video import limits

#: Fingerprints this close are the same picture.
SAME_PICTURE_BITS = 6


# ─── Frames ─────────────────────────────────────────────────────────────
def candidate_times(duration: float, scenes: Iterable[tuple[float, float]] | Iterable[float]) -> list[float]:
    """Where to grab frames: the start (1 s in, past a fade), each scene
    change (a moment after it, once the picture settles), and fill frames
    across long stretches with no change. Sorted, at least 1.5 s apart,
    never past the end."""
    if duration <= 0:
        return []
    end = max(0.0, duration - 0.3)
    anchors = [min(1.0, end)]
    for s in scenes:
        t = s[0] if isinstance(s, (tuple, list)) else s
        anchors.append(min(float(t) + 0.4, end))
    anchors.sort()
    bounds = anchors + [end]
    times: list[float] = []
    for i, a in enumerate(anchors):
        times.append(a)
        gap = bounds[i + 1] - a
        n = int(gap // limits.FILL_EVERY_SEC)
        times += [a + gap * k / (n + 1) for k in range(1, n + 1)]
    out: list[float] = []
    for t in sorted(times):
        if not out or t - out[-1] >= 1.5:
            out.append(round(t, 2))
    return out


def drop_same(times: list[float], hashes: list[int | None]) -> list[float]:
    """``times`` without each frame that looks like the last one kept."""
    kept: list[float] = []
    last: int | None = None
    for t, h in zip(times, hashes):
        if h is not None and last is not None and bin(h ^ last).count("1") <= SAME_PICTURE_BITS:
            continue
        kept.append(t)
        if h is not None:
            last = h
    return kept


def spread(times: list[float], n: int = limits.MAX_FRAMES) -> list[float]:
    """At most ``n`` of ``times``, evenly spread, first and last kept."""
    if len(times) <= n:
        return list(times)
    if n <= 1:
        return times[:1]
    step = (len(times) - 1) / (n - 1)
    return [times[round(i * step)] for i in range(n)]


def pick_frames(times: list[float], hashes: list[int | None]) -> list[float]:
    return spread(drop_same(times, hashes))


# ─── Speed and the estimate ─────────────────────────────────────────────
#: Fixed first guesses, for a server with no graphics card (the house's
#: default): seconds of work per second of video for the scene scan and the
#: sound; per second of speech for the transcript; per frame for a
#: description (grab included); for the summary.
GUESS = {
    "scan_per_video_s": 0.05,
    "audio_per_video_s": 0.02,
    "stt_per_speech_s": 0.35,
    "frame_s": 25.0,
    "combine_s": 25.0,
}
#: Before the scan has counted: the share of a clip that is speech, and the
#: frames a clip gets per minute.
GUESS_SPEECH_SHARE = 0.6
GUESS_FRAMES_PER_MIN = 4.0


@dataclass
class Counts:
    duration: float
    speech_s: float | None = None
    frames: int | None = None

    def speech(self) -> float:
        return self.speech_s if self.speech_s is not None else self.duration * GUESS_SPEECH_SHARE

    def frame_count(self) -> int:
        if self.frames is not None:
            return self.frames
        guess = round(self.duration / 60.0 * GUESS_FRAMES_PER_MIN)
        return max(1, min(limits.MAX_FRAMES, max(limits.MIN_FRAMES, guess)))


def speeds_from_stats(rows: Iterable[dict[str, Any]]) -> dict[str, float]:
    """The guesses, with each one this server has measured (finished jobs'
    ``stats``, newest first) replaced by the median of the last five."""
    seen: dict[str, list[float]] = {k: [] for k in GUESS}
    for st in rows:
        speeds = (st or {}).get("speeds") or {}
        for k in GUESS:
            v = speeds.get(k)
            if isinstance(v, (int, float)) and v > 0 and len(seen[k]) < 5:
                seen[k].append(float(v))
    out = dict(GUESS)
    for k, vals in seen.items():
        if vals:
            vals.sort()
            out[k] = vals[len(vals) // 2]
    return out


def estimate(counts: Counts, speeds: dict[str, float] | None = None, *,
             done: dict[str, Any] | None = None) -> float:
    """Seconds of work left: everything, or (``done``) what isn't done yet:
    ``{"scan": bool, "transcribed_s": seconds of speech done, "frames":
    frames described, "combine": bool}``."""
    sp = {**GUESS, **(speeds or {})}
    d = done or {}
    left = 0.0
    if not d.get("scan"):
        left += counts.duration * (sp["scan_per_video_s"] + sp["audio_per_video_s"])
    left += max(0.0, counts.speech() - float(d.get("transcribed_s") or 0.0)) * sp["stt_per_speech_s"]
    left += max(0, counts.frame_count() - int(d.get("frames") or 0)) * sp["frame_s"]
    if not d.get("combine"):
        left += sp["combine_s"]
    return round(left, 1)


def about_minutes(seconds: float | None) -> str:
    """"about 4 minutes", "about a minute", "less than a minute"."""
    if seconds is None:
        return ""
    if seconds < 45:
        return "less than a minute"
    m = max(1, round(seconds / 60.0))
    return "about a minute" if m == 1 else f"about {m} minutes"


STAGE_WORDS = {
    "queued": "waiting to start",
    "scan": "looking through it",
    "transcribe": "listening to what's said",
    "frames": "looking at the pictures",
    "combine": "writing a short summary",
}


def progress_line(state: str, stage: str | None, eta_s: float | None, *, waiting_for_voice: bool = False,
                  queued_behind: int = 0) -> str:
    """The line on the attachment while the video is being watched."""
    if state == "queued":
        if queued_behind:
            return f"Waiting for {queued_behind} other video{'s' if queued_behind != 1 else ''} first…"
        return "Waiting to start…"
    if state != "running":
        return ""
    head = "Watching your video…"
    if waiting_for_voice:
        return f"{head} paused while someone talks to Domovoi"
    tail = about_minutes(eta_s)
    return f"{head} {tail} left" if tail else head
