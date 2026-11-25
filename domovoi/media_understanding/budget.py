"""How much of a video to look at: the adaptive frame budget, and the
benchmark it stands on (owner, 2026-11-22, "Q11 updated").

**By hardware, measured.** A short benchmark times real vision work on
this server (:func:`run_benchmark`) the first time the runner needs it and
again after the vision model changes. It stores seconds per frame, frames
per second, and whether the model ran on a GPU (and how much of it sat in
video memory), as Ollama itself reports for the loaded model. Nothing is
ever guessed from a GPU's name. Every budget decision reads the stored
result (:class:`Tier`).

**By content.** Frames are picked at scene changes; the count grows with
the video's length and its scene count, up to the cap. Short clips use far
fewer.

**By time.** Each video gets a processing-time budget per tier
(:data:`TIME_BUDGET_S`), so one long video can't use a whole night on a
CPU-only house (the per-video time cap, owner answer).

**By backlog.** A first ``quick`` pass over a large backlog uses a lower
per-video budget so everything is covered sooner; a later ``deep`` pass
adds frames up to the cap (:func:`choose_pass`).

**An admin setting.** Automatic (the default) or a manual cap
(``switches.admin_cap``), which replaces the tier cap in both passes.

Transcripts stay at up to two hours (:data:`TRANSCRIPT_MAX_S`).

Pure functions apart from the benchmark's storage. Slices 2-3 call
:func:`frame_budget`.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy import text

# ─── the rule's numbers ───────────────────────────────────────────────────

#: Frames per video, at most: CPU-only houses (owner) and GPU houses.
CPU_CAP = 120
GPU_CAP = 600
#: Seconds of video per frame at full length: CPU 120 frames over 2 hours;
#: GPU 600 over 2 hours (one every 12 s, the owner's "every 10-15 s").
CPU_SPACING_S = 60.0
GPU_SPACING_S = 12.0
#: Every video gets at least this many frames (a short clip: never more
#: than one per 2 seconds of it).
MIN_FRAMES = 3
#: The quick first pass takes this share of the frames and of the time.
QUICK_FRACTION = 0.25
#: A GPU that does a frame in this many seconds or less gets the whole GPU
#: cap; a slower one proportionally less (never below the CPU cap).
GPU_REF_S_PER_FRAME = 1.0
#: With less video memory than this the model spills to the CPU: half cap.
GPU_LOW_VRAM_BYTES = 4 * 1024 ** 3
GPU_LOW_VRAM_CAP = 300
#: Per-video processing-time budget (seconds), by tier and pass.
TIME_BUDGET_S = {
    ("cpu", "quick"): 10 * 60.0,
    ("cpu", "deep"): 40 * 60.0,
    ("gpu", "quick"): 5 * 60.0,
    ("gpu", "deep"): 20 * 60.0,
}
#: The share of a video's time budget its frames may use (the rest is the
#: scan, the transcript and the summary).
FRAME_SHARE = 0.7
#: A manual cap may stretch the time budget, never past this.
MAX_VIDEO_BUDGET_S = 3 * 3600.0
#: Transcripts: the first two hours of a video (owner, Q11).
TRANSCRIPT_MAX_S = 2 * 3600.0
#: Before any benchmark: the slow side of the published estimates.
UNMEASURED_S_PER_FRAME = {"cpu": 25.0, "gpu": 3.0}
#: Videos with no frame pass yet: at this many or more, new ones get the
#: quick pass first.
QUICK_BACKLOG = 25

PASS_KINDS = ("quick", "deep")


@dataclass(frozen=True)
class Tier:
    """This server's measured vision speed: ``kind`` is ``cpu`` or ``gpu``
    (from where Ollama placed the model, never a GPU's name)."""

    kind: str = "cpu"
    s_per_frame: float | None = None
    vram_bytes: int | None = None

    @property
    def speed(self) -> float:
        if self.s_per_frame and self.s_per_frame > 0:
            return float(self.s_per_frame)
        return UNMEASURED_S_PER_FRAME.get(self.kind, UNMEASURED_S_PER_FRAME["cpu"])


def tier_cap(tier: Tier) -> int:
    """The automatic frames-per-video cap for ``tier``."""
    if tier.kind != "gpu":
        return CPU_CAP
    scale = min(1.0, GPU_REF_S_PER_FRAME / tier.speed)
    cap = max(CPU_CAP, min(GPU_CAP, int(round(GPU_CAP * scale))))
    if tier.vram_bytes is not None and tier.vram_bytes < GPU_LOW_VRAM_BYTES:
        cap = min(cap, GPU_LOW_VRAM_CAP)
    return cap


def frame_budget(video_seconds: float, scene_count: int, tier: Tier | str, pass_kind: str,
                 admin_setting: int | None = None) -> tuple[int, float]:
    """``(max_frames, time_budget_s)`` for one video.

    ``tier`` is a :class:`Tier` (or just ``"cpu"``/``"gpu"`` before a
    benchmark); ``pass_kind`` is ``quick`` or ``deep``; ``admin_setting``
    is the admin's manual cap (None for Automatic)."""
    if isinstance(tier, str):
        tier = Tier(kind=tier)
    if pass_kind not in PASS_KINDS:
        raise ValueError(f"pass_kind is quick or deep, not {pass_kind!r}")
    kind = "gpu" if tier.kind == "gpu" else "cpu"
    seconds = max(0.0, float(video_seconds or 0.0))
    scenes = max(0, int(scene_count or 0))

    floor = max(1, min(MIN_FRAMES, math.ceil(seconds / 2.0))) if seconds > 0 else 1
    cap = admin_setting if admin_setting else tier_cap(tier)
    if admin_setting:
        spacing = TRANSCRIPT_MAX_S / cap
    else:
        spacing = max(GPU_SPACING_S if kind == "gpu" else CPU_SPACING_S, TRANSCRIPT_MAX_S / cap)
    # Length: one frame per `spacing`, the full cap at two hours (a capped
    # spacing keeps a GPU with a smaller cap on the same curve).
    by_length = math.ceil(seconds / spacing)
    # Content: one per scene change, but a long scene-less video still gets
    # a quarter of its length's share.
    want = min(by_length, max(scenes, math.ceil(by_length / 4)))
    want = max(floor, min(want, cap))

    time_s = TIME_BUDGET_S[(kind, pass_kind)]
    if pass_kind == "quick":
        want = max(floor, int(math.ceil(want * QUICK_FRACTION)))
    if admin_setting and pass_kind == "deep":
        # The admin chose the frames: the time stretches to fit them.
        time_s = min(MAX_VIDEO_BUDGET_S, max(time_s, want * tier.speed / FRAME_SHARE))
    by_time = int(time_s * FRAME_SHARE // tier.speed)
    frames = max(min(floor, max(1, by_time)), min(want, by_time))
    return int(frames), float(time_s)


def choose_pass(frame_pass: str | None, unpassed_videos: int) -> str | None:
    """The next frame pass for a video that has had ``frame_pass`` (None,
    ``quick`` or ``deep``), with ``unpassed_videos`` videos still waiting
    for a first pass: ``quick`` over a big backlog, ``deep`` otherwise, and
    a quick-passed video deepens only once nothing waits for a first look.
    None when the video is done."""
    if frame_pass is None:
        return "quick" if unpassed_videos >= QUICK_BACKLOG else "deep"
    if frame_pass == "quick":
        return "deep" if unpassed_videos == 0 else None
    return None


# ─── the benchmark ────────────────────────────────────────────────────────

#: Frames timed after one warm-up call (the warm-up loads the model).
BENCH_FRAMES = 2
BENCH_VERSION = 1


def bench_image(size: int = 768) -> bytes:
    """A deterministic 768 px JPEG with shapes and text-like strokes: the
    same size and kind of work as a real frame."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (size, size * 9 // 16), (90, 120, 160))
    d = ImageDraw.Draw(img)
    w, h = img.size
    for i in range(12):
        x = (i * 61) % w
        d.rectangle([x, h // 3, x + 40, h // 3 + 80], fill=((i * 40) % 255, 180, (i * 70) % 255))
    d.ellipse([w // 2 - 90, h // 2 - 90, w // 2 + 90, h // 2 + 90], fill=(230, 200, 60))
    for line in range(4):
        d.line([20, 20 + line * 14, 300, 20 + line * 14], fill=(250, 250, 250), width=3)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def run_benchmark(describe: Callable[[bytes], Awaitable[Any]], model: str, *,
                        placement: Callable[[], Awaitable[dict[str, Any] | None]] | None = None,
                        frames: int = BENCH_FRAMES,
                        clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Time ``frames`` real vision calls (after one warm-up) and return
    the record to store. ``placement`` asks Ollama where the loaded model
    sits (``{"size": bytes, "size_vram": bytes}``); with some of it in
    video memory the tier is ``gpu``."""
    jpeg = bench_image()
    await describe(jpeg)  # warm-up: loads the model; not timed
    times = []
    for _ in range(max(1, frames)):
        t0 = clock()
        await describe(jpeg)
        times.append(max(1e-3, clock() - t0))
    s_per_frame = sum(times) / len(times)
    where: dict[str, Any] | None = None
    if placement is not None:
        try:
            where = await placement()
        except Exception:  # noqa: BLE001 — no placement says CPU, the safe side
            where = None
    vram = int(where.get("size_vram") or 0) if isinstance(where, dict) else 0
    return {
        "version": BENCH_VERSION,
        "model": model,
        "s_per_frame": round(s_per_frame, 3),
        "frames_per_s": round(1.0 / s_per_frame, 4),
        "tier": "gpu" if vram > 0 else "cpu",
        "vram_bytes": vram or None,
        "samples": len(times),
        "measured_at": time.time(),
    }


def tier_from_benchmark(bench: dict[str, Any] | None) -> Tier:
    if not isinstance(bench, dict):
        return Tier()
    return Tier(kind="gpu" if bench.get("tier") == "gpu" else "cpu",
                s_per_frame=float(bench["s_per_frame"]) if bench.get("s_per_frame") else None,
                vram_bytes=int(bench["vram_bytes"]) if bench.get("vram_bytes") else None)


def benchmark_is_current(bench: Any, model: str) -> bool:
    return (isinstance(bench, dict) and bench.get("version") == BENCH_VERSION
            and bench.get("model") == model and bool(bench.get("s_per_frame")))


async def stored_benchmark(s: Any) -> dict[str, Any] | None:
    row = (await s.execute(text("SELECT benchmark FROM media_runner_state WHERE id = 1"))).first()
    if row is None or row[0] is None:
        return None
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])


async def save_benchmark(s: Any, bench: dict[str, Any]) -> None:
    await s.execute(text(
        "INSERT INTO media_runner_state (id, benchmark, updated_at) VALUES (1, CAST(:b AS JSONB), now()) "
        "ON CONFLICT (id) DO UPDATE SET benchmark = EXCLUDED.benchmark, updated_at = now()"),
        {"b": json.dumps(bench)})


_bench_lock = asyncio.Lock()


async def ensure_benchmark(session_scope: Any, model: str,
                           measure: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    """The stored benchmark for ``model``, measuring (once) when there is
    none or it was for another model."""
    async with _bench_lock:
        async with session_scope() as s:
            bench = await stored_benchmark(s)
        if benchmark_is_current(bench, model):
            return bench  # type: ignore[return-value]
        bench = await measure()
        async with session_scope() as s:
            await save_benchmark(s, bench)
        return bench
