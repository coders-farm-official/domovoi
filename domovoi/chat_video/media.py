"""ffprobe and ffmpeg for chat videos: the checks, and the passes' raw work.

The system's ``ffprobe``/``ffmpeg``, run as their own programs (the way
the Videos tab's poster frames and the audiobook indexer run them), never
linked in. Every run is at the lowest CPU priority the host offers (``nice
19`` on Linux and macOS, the idle priority class on Windows) and can be
held still while a voice turn runs (:class:`Proc`: ``suspend``/``resume``),
so a long decode never competes with a spoken answer.

The passes' raw pieces:

* :func:`probe` — duration, container and codecs, before a video is
  accepted;
* :func:`scene_changes` — pass 0: where the picture changes, read from the
  key frames only, at 160 px wide (a few seconds for a 3-minute 4K clip,
  where decoding every frame would take a minute);
* :func:`extract_audio` — the sound as 16 kHz mono PCM, what the speech
  model takes;
* :func:`speech_spans` — pass 0: where someone may be speaking (loudness
  over the clip's own quiet level), with no model;
* :func:`grab_frame` — pass 2: one frame as a JPEG for the vision model,
  upright (ffmpeg applies a phone's rotation), at most 768 px wide;
* :func:`dhash` — a frame's 64-bit picture fingerprint, to drop frames
  that look the same.
"""

from __future__ import annotations

import array
import asyncio
import contextlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from domovoi.chat_video import limits

log = logging.getLogger(__name__)

#: How long one ffprobe may take; one ffmpeg pass.
PROBE_TIMEOUT_SEC = 30.0
PASS_TIMEOUT_SEC = 300.0
#: The scene-change threshold (ffmpeg's ``scene`` score, 0-1).
SCENE_THRESHOLD = 0.3
#: The vision model's frame width; the fingerprint's.
FRAME_WIDTH = 768
#: Speech scan: 30 ms windows at 16 kHz.
SAMPLE_RATE = 16000
WINDOW = 480
#: Speech is louder than the clip's quiet level by this factor (about 10 dB)
#: and above an absolute floor (about -50 dBFS). A clip with no quiet part
#: at all (talk over steady noise, music throughout) would put its "quiet
#: level" at the speech itself, so the threshold never goes above
#: :data:`SPEECH_MAX_THRESHOLD` (about -35 dBFS): everything that loud is
#: sent to the speech model, which decides what was said.
SPEECH_OVER_FLOOR = 3.2
SPEECH_ABS_FLOOR = 100.0
SPEECH_MAX_THRESHOLD = 600.0
#: Gaps shorter than this join two spans; spans shorter than this go.
JOIN_GAP_SEC = 0.6
MIN_SPAN_SEC = 0.3

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_IDLE = getattr(subprocess, "IDLE_PRIORITY_CLASS", 0)


def tools_present() -> bool:
    """ffprobe and ffmpeg are both on PATH."""
    return shutil.which("ffprobe") is not None and shutil.which("ffmpeg") is not None


def _argv(argv: list[str]) -> list[str]:
    """``argv`` at the lowest priority: ``nice -n 19`` where there is one
    (Linux, macOS); Windows uses the idle priority class instead."""
    if sys.platform != "win32":
        nice = shutil.which("nice")
        if nice:
            return [nice, "-n", "19", *argv]
    return argv


class Proc:
    """One running ffmpeg, which a voice turn can hold still."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self.suspended = False

    def _ps(self) -> Any:
        try:
            import psutil

            return psutil.Process(self.proc.pid)
        except Exception:  # noqa: BLE001 — psutil missing, or the process ended
            return None

    def suspend(self) -> None:
        if self.suspended or self.proc.returncode is not None:
            return
        p = self._ps()
        if p is not None:
            try:
                p.suspend()
                self.suspended = True
            except Exception:  # noqa: BLE001 — it ended meanwhile
                pass

    def resume(self) -> None:
        if not self.suspended:
            return
        p = self._ps()
        if p is not None:
            try:
                p.resume()
            except Exception:  # noqa: BLE001
                pass
        self.suspended = False


#: ``hold(proc)`` — called while a pass runs: holds ``proc`` still while a
#: voice turn is under way, and returns when the pass may go on (the
#: runner's :meth:`Runner.hold_while_voice`). None: never held.
Hold = Callable[[Proc], Awaitable[None]]


async def _run(argv: list[str], *, timeout: float, hold: Hold | None = None,
               stdout: bool = False, env: dict[str, str] | None = None) -> tuple[int, bytes, bytes]:
    """Run ``argv`` at low priority; (returncode, stdout, stderr). Raises
    FileNotFoundError when the program isn't installed, TimeoutError past
    ``timeout`` (the process is killed). ``env``: extra environment
    variables for the program. The scan reader runs tesseract through it
    too (:func:`run_low`)."""
    proc = await asyncio.create_subprocess_exec(
        *_argv(argv),
        env={**os.environ, **env} if env else None,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE if stdout else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        creationflags=_NO_WINDOW | _IDLE,
    )
    wrapped = Proc(proc)
    comm = asyncio.ensure_future(proc.communicate())
    watcher = asyncio.ensure_future(_watch(wrapped, hold, comm)) if hold is not None else None
    try:
        out, err = await asyncio.wait_for(asyncio.shield(comm), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        wrapped.resume()
        with _quiet():
            proc.kill()
        with _quiet():
            await asyncio.wait_for(comm, timeout=5)
        raise
    finally:
        if watcher is not None:
            watcher.cancel()
        wrapped.resume()
    return proc.returncode or 0, out or b"", err or b""


#: The public name for other background jobs (the scan reader's tesseract).
run_low = _run


async def _watch(proc: Proc, hold: Hold, done: "asyncio.Future[Any]") -> None:
    while not done.done():
        await hold(proc)
        await asyncio.sleep(0.1)


def _quiet() -> contextlib.suppress:
    return contextlib.suppress(ProcessLookupError, OSError, asyncio.TimeoutError)


# ─── Probe ──────────────────────────────────────────────────────────────
@dataclass
class ProbeResult:
    duration: float
    container: str
    video_codec: str | None
    audio_codec: str | None
    width: int | None
    height: int | None

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_codec)

    def to_json(self) -> dict[str, Any]:
        return {"duration_s": round(self.duration, 2), "container": self.container,
                "video_codec": self.video_codec, "audio_codec": self.audio_codec,
                "width": self.width, "height": self.height}


class ProbeFailed(Exception):
    """ffprobe couldn't read the file (or isn't installed: ``missing_tools``)."""

    def __init__(self, reason: str, missing_tools: bool = False) -> None:
        super().__init__(reason)
        self.missing_tools = missing_tools


def parse_probe(doc: dict[str, Any]) -> ProbeResult:
    fmt = doc.get("format") or {}
    streams = doc.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = _num(fmt.get("duration"))
    if dur is None and video is not None:
        dur = _num(video.get("duration"))
    return ProbeResult(
        duration=float(dur or 0.0),
        container=str(fmt.get("format_name") or ""),
        video_codec=(str(video.get("codec_name")) if video else None),
        audio_codec=(str(audio.get("codec_name")) if audio else None),
        width=_int(video.get("width")) if video else None,
        height=_int(video.get("height")) if video else None,
    )


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f >= 0 else None


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


async def probe(path: Path) -> ProbeResult:
    try:
        code, out, _err = await _run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            timeout=PROBE_TIMEOUT_SEC, stdout=True)
    except FileNotFoundError:
        raise ProbeFailed("ffprobe is not installed", missing_tools=True) from None
    except asyncio.TimeoutError:
        raise ProbeFailed("ffprobe took too long") from None
    if code != 0:
        raise ProbeFailed(f"ffprobe exit {code}")
    try:
        return parse_probe(json.loads(out.decode("utf-8", "replace")))
    except (ValueError, AttributeError) as e:
        raise ProbeFailed(f"ffprobe output unreadable: {type(e).__name__}") from None


def refusal(result: ProbeResult) -> tuple[str, str] | None:
    """Why chat won't take a probed video ((code, sentence)), or None."""
    containers = set(result.container.split(","))
    if not (containers & limits.CONTAINERS) or result.video_codec not in limits.VIDEO_CODECS:
        return limits.CODE_UNSUPPORTED, limits.UNSUPPORTED
    if result.duration <= 0:
        return limits.CODE_UNREADABLE, limits.UNREADABLE
    if result.duration > limits.MAX_DURATION_SEC + limits.DURATION_SLACK_SEC:
        return limits.CODE_TOO_LONG, limits.too_long_text(result.duration)
    return None


# ─── Pass 0: scenes ─────────────────────────────────────────────────────
_PTS = re.compile(r"pts_time:([0-9.]+)")
_SCORE = re.compile(r"lavfi\.scene_score=([0-9.]+)")


def parse_scenes(log_text: str) -> list[tuple[float, float]]:
    """``[(time, score)]`` from ffmpeg's ``metadata=print`` lines."""
    out: list[tuple[float, float]] = []
    t: float | None = None
    for line in log_text.splitlines():
        m = _PTS.search(line)
        if m:
            t = float(m.group(1))
            continue
        m = _SCORE.search(line)
        if m and t is not None:
            out.append((round(t, 2), round(float(m.group(1)), 3)))
            t = None
    return out


async def scene_changes(src: Path, *, hold: Hold | None = None) -> list[tuple[float, float]]:
    code, _out, err = await _run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-skip_frame", "nokey", "-i", str(src), "-an", "-sn",
         "-vf", f"scale=160:-2,select='gt(scene,{SCENE_THRESHOLD})',metadata=print", "-f", "null", "-"],
        timeout=PASS_TIMEOUT_SEC, hold=hold)
    if code != 0:
        raise RuntimeError(f"ffmpeg scene scan exit {code}")
    return parse_scenes(err.decode("utf-8", "replace"))


# ─── Pass 0/1: sound ────────────────────────────────────────────────────
async def extract_audio(src: Path, dest: Path, *, hold: Hold | None = None) -> bool:
    """The sound as 16 kHz mono 16-bit PCM in ``dest``; False when the
    video has none."""
    part = dest.with_suffix(".part")
    code, _out, _err = await _run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-vn", "-sn", "-ac", "1",
         "-ar", str(SAMPLE_RATE), "-f", "s16le", str(part)],
        timeout=PASS_TIMEOUT_SEC, hold=hold)
    if code != 0 or not part.is_file():
        part.unlink(missing_ok=True)
        return False
    part.replace(dest)
    return True


def window_levels(pcm: bytes) -> list[float]:
    """The RMS level of each 30 ms window of 16-bit PCM."""
    try:
        import numpy as np

        a = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2").astype(np.float32)
        n = len(a) // WINDOW
        if n == 0:
            return []
        a = a[: n * WINDOW].reshape(n, WINDOW)
        return [float(x) for x in np.sqrt((a * a).mean(axis=1))]
    except ImportError:  # pragma: no cover — numpy is everywhere Whisper is
        samples = array.array("h")
        samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
        if sys.byteorder == "big":
            samples.byteswap()
        out = []
        for i in range(0, len(samples) - WINDOW + 1, WINDOW):
            chunk = samples[i:i + WINDOW]
            out.append(math.sqrt(sum(x * x for x in chunk) / WINDOW))
        return out


def speech_spans(levels: list[float]) -> list[tuple[float, float]]:
    """Where someone may be speaking: windows louder than the clip's own
    quiet level, joined across short gaps. Generous on purpose (music and
    noise count too): the speech model decides what was said, this only
    saves it the silence."""
    if not levels:
        return []
    ordered = sorted(levels)
    floor = ordered[len(ordered) // 10]
    threshold = max(SPEECH_ABS_FLOOR, min(floor * SPEECH_OVER_FLOOR, SPEECH_MAX_THRESHOLD))
    win = WINDOW / SAMPLE_RATE
    spans: list[list[float]] = []
    for i, lv in enumerate(levels):
        if lv < threshold:
            continue
        t0, t1 = i * win, (i + 1) * win
        if spans and t0 - spans[-1][1] <= JOIN_GAP_SEC:
            spans[-1][1] = t1
        else:
            spans.append([t0, t1])
    return [(round(a, 2), round(b, 2)) for a, b in spans if b - a >= MIN_SPAN_SEC]


def speech_pieces(spans: list[tuple[float, float]], levels: list[float],
                  max_sec: float = limits.SPEECH_PIECE_SEC) -> list[tuple[float, float]]:
    """``spans`` cut into pieces of at most ``max_sec``, each cut at the
    quietest window in the last third of the piece (a pause between words
    where there is one), with a little sound kept either side."""
    win = WINDOW / SAMPLE_RATE
    out: list[tuple[float, float]] = []
    total = len(levels) * win
    for a, b in spans:
        a = max(0.0, a - 0.15)
        b = min(total if total else b + 0.15, b + 0.15)
        start = a
        while b - start > max_sec:
            lo = start + max_sec * 2 / 3
            hi = start + max_sec
            i0, i1 = int(lo / win), max(int(lo / win) + 1, int(hi / win))
            seg = levels[i0:i1]
            cut = (i0 + seg.index(min(seg))) * win if seg else hi
            cut = min(max(cut, lo), hi)
            out.append((round(start, 2), round(cut, 2)))
            start = cut
        if b - start >= MIN_SPAN_SEC:
            out.append((round(start, 2), round(b, 2)))
    return out


def pcm_slice(pcm: bytes, start: float, end: float) -> bytes:
    i0 = max(0, int(start * SAMPLE_RATE)) * 2
    i1 = max(i0, int(end * SAMPLE_RATE)) * 2
    return pcm[i0:i1]


# ─── Pass 2: frames ─────────────────────────────────────────────────────
async def grab_frame(src: Path, at: float, dest: Path, *, hold: Hold | None = None) -> bool:
    """One frame at ``at`` seconds as a JPEG, upright, at most
    :data:`FRAME_WIDTH` wide."""
    part = dest.with_name(dest.stem + ".part.jpg")
    code, _out, _err = await _run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{max(0.0, at):.2f}", "-i", str(src),
         "-frames:v", "1", "-vf", f"scale='min({FRAME_WIDTH},iw)':-2", "-q:v", "3", str(part)],
        timeout=60.0, hold=hold)
    if code != 0 or not part.is_file() or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        return False
    part.replace(dest)
    return True


def dhash(path: Path) -> int | None:
    """A 64-bit difference hash of the picture (None when unreadable)."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            g = im.convert("L").resize((9, 8))
            px = list(g.tobytes())
    except Exception:  # noqa: BLE001 — an unreadable frame is simply kept
        return None
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def remove_quietly(*paths: Path) -> None:
    for p in paths:
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
        except OSError:
            pass


def disk_free(path: Path) -> int | None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(path).free
    except OSError:
        return None


__all__ = [
    "Proc", "ProbeFailed", "ProbeResult", "dhash", "disk_free", "extract_audio", "grab_frame",
    "hamming", "parse_probe", "parse_scenes", "pcm_slice", "probe", "refusal", "remove_quietly",
    "scene_changes", "speech_pieces", "speech_spans", "tools_present", "window_levels",
]
