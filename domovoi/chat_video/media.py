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

The file is somebody else's bytes, so every helper run here (ffprobe,
ffmpeg, and the scan reader's tesseract through :func:`run_low`) is also
**capped** (security audit round 6, DOC-7/DOC-8):

* a memory cap (:func:`helper_memory_bytes`): ``RLIMIT_AS`` on Linux (the
  server's platform, the Windows install's WSL distro included), a job
  object's per-process limit on a native Windows core; a frame too big for
  it fails that one pass, never the core;
* one decoding thread (``-threads 1``, ``-filter_threads 1``);
* every ffmpeg pass reads at most :func:`limits.pass_seconds` of the video
  (``-t`` before ``-i``), whatever its header claims, and only the stream
  the probe checked (``-map``); the sound pass is also cut off by size
  (``-fs``).
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


#: The memory cap of one helper program: 2 GiB, or a third of the
#: machine's memory when that is less, never under 1 GiB (ffmpeg's own
#: libraries take a few hundred MB of address space before it reads a
#: byte). An 8K phone video decodes well inside it.
HELPER_MEMORY_MAX = 2 * 1024 ** 3
HELPER_MEMORY_MIN = 1024 ** 3


def helper_memory_bytes() -> int:
    from domovoi.doc_extract import limits as caps

    total = caps._total_memory()
    if not total:
        return HELPER_MEMORY_MAX
    return int(min(HELPER_MEMORY_MAX, max(HELPER_MEMORY_MIN, total // 3)))


def _rlimit_as(memory_bytes: int) -> Callable[[], None]:
    """The child's first step before it runs the program (Linux): its
    address space capped at ``memory_bytes``. Never raises (an exception
    here would fail every pass)."""
    import resource

    def cap() -> None:
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        except (ValueError, OSError):
            pass

    return cap


def _windows_cap(pid: int, memory_bytes: int) -> Any:
    """A job object holding ``pid`` to ``memory_bytes`` (Windows); its handle
    (closed by :func:`_windows_release`), or None when it couldn't be set."""
    try:
        import ctypes
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", ctypes.c_ulonglong * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXTENDED()
        # PROCESS_MEMORY | KILL_ON_JOB_CLOSE: a core that dies takes its helper along.
        info.BasicLimitInformation.LimitFlags = 0x00000100 | 0x00002000
        info.ProcessMemoryLimit = int(memory_bytes)
        proc = k32.OpenProcess(0x0100 | 0x0001, False, int(pid))   # SET_QUOTA | TERMINATE
        ok = bool(proc) and k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info),
                                                        ctypes.sizeof(info)) \
            and k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(proc))
        if proc:
            k32.CloseHandle(wintypes.HANDLE(proc))
        if not ok:
            k32.CloseHandle(wintypes.HANDLE(job))
            return None
        return job
    except Exception as e:  # noqa: BLE001 — best effort: the Windows install runs the core in WSL (RLIMIT_AS)
        log.debug("chat video: couldn't cap a helper on Windows: %s", type(e).__name__)
        return None


def _windows_release(job: Any) -> None:
    if job is None:
        return
    with contextlib.suppress(Exception):
        import ctypes
        from ctypes import wintypes

        ctypes.WinDLL("kernel32").CloseHandle(wintypes.HANDLE(job))


def _argv(argv: list[str]) -> list[str]:
    """``argv`` at the lowest priority: ``nice -n 19`` where there is one
    (Linux, macOS), and the idle I/O class (``ionice -c 3``, Linux) so a
    pass over a big video never starves the house's disk (media
    understanding, design §5); Windows uses the idle priority class
    instead."""
    if sys.platform != "win32":
        ionice = shutil.which("ionice") if sys.platform.startswith("linux") else None
        if ionice:
            argv = [ionice, "-c", "3", *argv]
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
               stdout: bool = False, env: dict[str, str] | None = None,
               memory_bytes: int | None = None) -> tuple[int, bytes, bytes]:
    """Run ``argv`` at low priority, memory-capped; (returncode, stdout,
    stderr). Raises FileNotFoundError when the program isn't installed,
    TimeoutError past ``timeout`` (the process is killed), and whatever
    ``hold`` raises (the job's stop: the process is killed at once).
    ``env``: extra environment variables for the program. ``memory_bytes``:
    the cap (:func:`helper_memory_bytes` when None, 0 for none). The scan
    reader runs tesseract through it too (:func:`run_low`)."""
    mem = helper_memory_bytes() if memory_bytes is None else int(memory_bytes)
    extra: dict[str, Any] = {}
    if mem and sys.platform.startswith("linux"):
        # macOS doesn't take a lower RLIMIT_AS (its address space is mostly
        # shared mappings): there the one thread and the stream and length
        # caps remain.
        extra["preexec_fn"] = _rlimit_as(mem)
    proc = await asyncio.create_subprocess_exec(
        *_argv(argv),
        env={**os.environ, **env} if env else None,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE if stdout else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        creationflags=_NO_WINDOW | _IDLE,
        **extra,
    )
    job = _windows_cap(proc.pid, mem) if mem and sys.platform == "win32" else None
    wrapped = Proc(proc)
    comm = asyncio.ensure_future(proc.communicate())
    watcher = asyncio.ensure_future(_watch(wrapped, hold, comm)) if hold is not None else None
    try:
        waits = {comm} if watcher is None else {comm, watcher}
        done, _pending = await asyncio.wait(waits, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if comm not in done:
            stopped = watcher.exception() if watcher is not None and watcher in done else None
            if stopped is not None:
                raise stopped       # the job was stopped while the program ran
            raise asyncio.TimeoutError()
        out, err = comm.result()
    except BaseException:
        wrapped.resume()
        with _quiet():
            proc.kill()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(comm), timeout=5)
        raise
    finally:
        if watcher is not None:
            watcher.cancel()
            with contextlib.suppress(BaseException):
                await watcher
        wrapped.resume()
        _windows_release(job)
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
    #: The stream indexes of the picture and the sound the probe checked
    #: (what every pass then maps, and nothing else).
    video_index: int | None = None
    audio_index: int | None = None
    #: The frame size (width, height) of EVERY picture stream in the file,
    #: cover pictures included: each one is checked, not only the first.
    sizes: tuple[tuple[int, int], ...] = ()

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_codec)

    def to_json(self) -> dict[str, Any]:
        return {"duration_s": round(self.duration, 2), "container": self.container,
                "video_codec": self.video_codec, "audio_codec": self.audio_codec,
                "width": self.width, "height": self.height,
                "video_index": self.video_index, "audio_index": self.audio_index}


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
    sizes = tuple((_int(st.get("width")) or 0, _int(st.get("height")) or 0)
                  for st in streams if st.get("codec_type") == "video")
    return ProbeResult(
        duration=float(dur or 0.0),
        container=str(fmt.get("format_name") or ""),
        video_codec=(str(video.get("codec_name")) if video else None),
        audio_codec=(str(audio.get("codec_name")) if audio else None),
        width=_int(video.get("width")) if video else None,
        height=_int(video.get("height")) if video else None,
        video_index=_int(video.get("index")) if video else None,
        audio_index=_int(audio.get("index")) if audio else None,
        sizes=sizes,
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
    except (TypeError, ValueError, OverflowError):  # OverflowError: JSON's 1e400 is inf
        return None


async def probe(path: Path) -> ProbeResult:
    try:
        code, out, _err = await _run(
            ["ffprobe", "-v", "error", "-threads", "1", "-print_format", "json", "-show_format", "-show_streams",
             str(path)],
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
    sizes = result.sizes or ((result.width or 0, result.height or 0),)
    if not all(picture_fits(sz) for sz in sizes):
        return limits.CODE_UNSUPPORTED, limits.TOO_BIG_PICTURE
    return None


def picture_fits(size: tuple[int, int]) -> bool:
    """A frame of ``size`` is one a phone records: 8K at most, either way up
    (:data:`limits.MAX_PIXELS`, :data:`limits.MAX_SIDE`)."""
    w, h = size
    return max(w, h) <= limits.MAX_SIDE and w * h <= limits.MAX_PIXELS


# ─── Pass 0: scenes ─────────────────────────────────────────────────────
# One number each ("1.2.3" is not one: float() refused it, fuzzing 2026-11).
_PTS = re.compile(r"pts_time:([0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_SCORE = re.compile(r"lavfi\.scene_score=([0-9]+(?:\.[0-9]*)?|\.[0-9]+)")


def parse_scenes(log_text: str) -> list[tuple[float, float]]:
    """``[(time, score)]`` from ffmpeg's ``metadata=print`` lines (at most
    :data:`limits.MAX_SCENE_ROWS`)."""
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
            if len(out) >= limits.MAX_SCENE_ROWS:
                break
    return out


def _map(index: int | None, fallback: str) -> list[str]:
    """``-map`` for the stream the probe checked (a job queued before the
    probe kept indexes maps the first stream of the kind)."""
    return ["-map", f"0:{int(index)}" if isinstance(index, int) and index >= 0 else fallback]


def _read_cap(seconds: float) -> list[str]:
    """The input options every pass starts with: one decoding thread, and at
    most ``seconds`` of the video read, whatever its header says."""
    return ["-threads", "1", "-filter_threads", "1", "-t", f"{max(0.1, float(seconds)):.2f}"]


def scene_argv(src: Path, duration: float | None, video_index: int | None = None) -> list[str]:
    """Pass 0's scene scan of a video the upload accepted at ``duration``."""
    return ["ffmpeg", "-nostdin", "-hide_banner", "-skip_frame", "nokey",
            *_read_cap(limits.pass_seconds(duration)), "-i", str(src),
            *_map(video_index, "0:V:0?"), "-an", "-sn",
            "-vf", f"scale=160:-2,select='gt(scene,{SCENE_THRESHOLD})',metadata=print", "-f", "null", "-"]


def audio_argv(src: Path, dest: Path, duration: float | None, audio_index: int | None = None) -> list[str]:
    """The sound pass of a video the upload accepted at ``duration``."""
    return ["ffmpeg", "-nostdin", "-v", "error", "-y", *_read_cap(limits.pass_seconds(duration)), "-i", str(src),
            *_map(audio_index, "0:a:0?"), "-vn", "-sn", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-fs", str(limits.pcm_max_bytes(duration)), "-f", "s16le", str(dest)]


def frame_argv(src: Path, at: float, dest: Path, video_index: int | None = None) -> list[str]:
    """One frame grab at ``at`` seconds."""
    return ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{max(0.0, at):.2f}", *_read_cap(FRAME_READ_SEC),
            "-i", str(src), *_map(video_index, "0:V:0?"), "-frames:v", "1",
            "-vf", f"scale='min({FRAME_WIDTH},iw)':-2", "-q:v", "3", str(dest)]


#: A frame grab reads at most this many seconds from where it seeks to.
FRAME_READ_SEC = 10.0


async def scene_changes(src: Path, *, duration: float | None = None, video_index: int | None = None,
                        hold: Hold | None = None) -> list[tuple[float, float]]:
    """Pass 0's scene changes in the first :func:`limits.pass_seconds` of
    ``src`` (``duration``: the length the upload accepted)."""
    code, _out, err = await _run(scene_argv(src, duration, video_index),
                                 timeout=PASS_TIMEOUT_SEC, hold=hold)
    if code != 0:
        raise RuntimeError(f"ffmpeg scene scan exit {code}")
    return parse_scenes(err.decode("utf-8", "replace"))


# ─── Pass 0/1: sound ────────────────────────────────────────────────────
async def extract_audio(src: Path, dest: Path, *, duration: float | None = None,
                        audio_index: int | None = None, hold: Hold | None = None) -> bool:
    """The sound of the first :func:`limits.pass_seconds` of ``src`` as 16
    kHz mono 16-bit PCM in ``dest``, never larger than
    :func:`limits.pcm_max_bytes`; False when the video has none."""
    part = dest.with_suffix(".part")
    code, _out, _err = await _run(audio_argv(src, part, duration, audio_index),
                                  timeout=PASS_TIMEOUT_SEC, hold=hold)
    if code != 0 or not part.is_file():
        part.unlink(missing_ok=True)
        return False
    part.replace(dest)
    return True


def read_pcm(path: Path, duration: float | None = None) -> bytes:
    """The sound file, at most :func:`limits.pcm_max_bytes` of it (what the
    pass may write; anything past that is never read)."""
    with Path(path).open("rb") as f:
        return f.read(limits.pcm_max_bytes(duration))


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
    return out[:limits.MAX_SPEECH_PIECES]


def pcm_slice(pcm: bytes, start: float, end: float) -> bytes:
    i0 = max(0, int(start * SAMPLE_RATE)) * 2
    i1 = max(i0, int(end * SAMPLE_RATE)) * 2
    return pcm[i0:i1]


# ─── Pass 2: frames ─────────────────────────────────────────────────────
async def grab_frame(src: Path, at: float, dest: Path, *, video_index: int | None = None,
                     hold: Hold | None = None) -> bool:
    """One frame at ``at`` seconds as a JPEG, upright, at most
    :data:`FRAME_WIDTH` wide."""
    part = dest.with_name(dest.stem + ".part.jpg")
    code, _out, _err = await _run(frame_argv(src, at, part, video_index), timeout=60.0, hold=hold)
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
    "Proc", "ProbeFailed", "ProbeResult", "audio_argv", "dhash", "disk_free", "extract_audio", "frame_argv",
    "grab_frame", "hamming", "helper_memory_bytes", "parse_probe", "parse_scenes", "pcm_slice", "picture_fits",
    "probe", "read_pcm", "refusal", "remove_quietly", "scene_argv", "scene_changes", "speech_pieces",
    "speech_spans", "tools_present", "window_levels",
]
