"""The ``media_understanding`` runner: one background job for everything
the house works out about its photos and videos, built on
:class:`domovoi.voice_first.VoiceFirst` (design MEDIA-UNDERSTANDING §5).

**Chat videos first.** A video someone attached in chat has a person
waiting, so the runner always asks the chat-video runner
(:mod:`domovoi.chat_video.runner`) for its next job before any library
work. One step at a time across the house.

**When library work runs** (:meth:`Runner.mode`):

* the **night window**: the household's quiet hours (default 22:00-07:00);
  the backlog runs continuously, still pausing for voice;
* the **daytime trickle**: only new arrivals (found by a walk after the
  library's first, in the last 24 hours), only after 10 minutes with no
  voice turn, at a 1:1 duty cycle (rest as long as the last item took);
* **Run now** (Settings) ignores the window until the backlog is done.

**Caps.**

* **Voice first**: every model step through :meth:`VoiceFirst.model_step`
  (it waits for quiet and is cancelled the moment a turn starts, then
  asked again); helper programs are held still during a turn
  (:meth:`StepContext.hold`) and run at ``nice 19`` + ``ionice -c 3``.
* **Memory**: a vision step waits until ``MemAvailable`` exceeds the
  vision model's size plus 2 GB (:meth:`Runner.wait_for_memory`); on a
  floor-tier server (under 22 GB) the vision model is let go after each
  batch so the voice models aren't evicted.
* **Disk**: Python reads through the 20 MB/s bucket (:mod:`.ratelimit`).
* **Time**: a video's analysers share a per-video processing-time budget
  (:func:`budget.frame_budget`; the owner's per-video cap for CPU-only
  houses). Past it, the stage ends as done-and-capped with what it saved.
* **Failures**: 3 attempts per item, then ``failed`` with a help-desk code,
  skipped until the file changes (the walker re-queues a changed file).

**Resume.** Each analyser's stage row keeps its saved progress and time
spent; an item left ``running`` by a restart is taken first and each
analyser carries on from its saved progress.

**Hooks for slices 2-4** (the photo, video and face analysers): an
:class:`Analyser` registered with :func:`register`. Each one gets a
:class:`StepContext` with ``model_step`` (voice-cancelled, memory-gated
for vision), ``hold`` (for helper programs), ``read``/``fingerprint``
(rate-limited), ``frame_budget``, ``save`` (resume), ``charge`` (time
budget) and ``work_dir``.

Core-internal: not offered to plugins.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

from sqlalchemy import text

from domovoi import ai_features, voice_activity
from domovoi.db.session import session_scope
from domovoi.media_understanding import budget, catalog, ratelimit, switches
from domovoi.voice_first import POLL_SEC, QUIET_SEC, VoiceFirst

log = logging.getLogger(__name__)

#: 3 attempts per item (a restart counts), then failed.
MAX_ATTEMPTS = 3
#: Daytime trickle: only items first seen this recently, only after this
#: long with no voice turn.
TRICKLE_NEW_S = 24 * 3600.0
TRICKLE_IDLE_S = 10 * 60.0
#: The memory gate: the vision model's size plus this much free.
MEMORY_HEADROOM = 2 * 1024 ** 3
#: Below this much RAM the server is the floor tier: the vision model is
#: let go after each batch.
FLOOR_TIER_BYTES = 22 * 1024 ** 3
#: When the size of the vision model can't be read.
VISION_MODEL_GUESS_BYTES = int(2.5 * 1024 ** 3)
MEMORY_POLL_S = 5.0
IDLE_POLL_S = 5.0
NOTIFY_EVERY_S = 10.0
PAUSE_CHECK_S = 1.0

CODE_INTERRUPTED = "media-interrupted"
CODE_FAILED = "media-failed"
CODE_AI_OFF = "ai-off"
CODE_MISSING = "media-missing"

WORK_ROOT = Path.home() / ".domovoi" / "understanding" / "work"


# ─── the hooks for slices 2-4 ─────────────────────────────────────────────


@dataclass
class Item:
    id: int
    library_id: str
    rel_path: str
    kind: str
    size_bytes: int
    mtime: float
    fingerprint: str | None
    state: str
    attempts: int
    new_arrival: bool
    frame_pass: str | None
    duration_s: float | None
    path: Path | None = None
    waiting: bool = False

    @classmethod
    def from_row(cls, r: Any) -> "Item":
        return cls(id=int(r["id"]), library_id=r["library_id"], rel_path=r["rel_path"], kind=r["kind"],
                   size_bytes=int(r["size_bytes"]), mtime=float(r["mtime"]), fingerprint=r["fingerprint"],
                   state=r["state"], attempts=int(r["attempts"] or 0), new_arrival=bool(r["new_arrival"]),
                   frame_pass=r["frame_pass"], duration_s=r["duration_s"])


class Analyser(Protocol):
    """One kind of understanding (slice 2: photos; 3: videos; 4: faces).

    * ``name``: the stage name, unique (``media_item_stages.analyser``).
    * ``kinds``: the item kinds it reads (``{"image"}``, ``{"video"}``, both).
    * ``order``: lower runs first (cheap work first: faces and dates,
      then transcripts, then captions).
    * ``deepens``: True when a ``deep`` frame pass should run it again
      after the ``quick`` one (the video frame analyser).
    * ``wanted(item, sw)``: whether to run on this item under the
      household's switches (faces only when ``sw["faces"]``).
    * ``run(item, ctx)``: the work; save each result as it is done, and
      progress through ``ctx.save`` so a restart carries on.
    * ``requeue(s, model, ai_on)`` (optional): called when the runner starts
      and whenever the vision model or the AI switch changes; puts the
      items whose results are out of date back in the queue (pending, as
      backlog: the night window, never the daytime trickle) and returns
      how many. It compares what is stored, so a restart doesn't redo it.
    """

    name: str
    kinds: frozenset[str]
    order: int
    deepens: bool

    def wanted(self, item: Item, sw: dict[str, Any]) -> bool: ...

    async def run(self, item: Item, ctx: "StepContext") -> None: ...


_ANALYSERS: dict[str, Analyser] = {}


def register(analyser: Analyser) -> None:
    """Add an analyser (idempotent by name)."""
    _ANALYSERS[analyser.name] = analyser


def unregister(name: str) -> None:
    _ANALYSERS.pop(name, None)


def analysers() -> list[Analyser]:
    return sorted(_ANALYSERS.values(), key=lambda a: (a.order, a.name))


#: Work an analyser slice does around items rather than on one (faces:
#: fetching its models on opt-in, the nightly re-group). Each is called with
#: the runner at the start of every library step, once the switches say
#: understanding is on and not paused; a failure is logged, never fatal.
_HOUSEKEEPING: dict[str, Callable[["Runner"], Awaitable[None]]] = {}


def register_housekeeping(name: str, fn: Callable[["Runner"], Awaitable[None]]) -> None:
    _HOUSEKEEPING[name] = fn


def unregister_housekeeping(name: str) -> None:
    _HOUSEKEEPING.pop(name, None)


class TimeCapReached(Exception):
    """The item's processing-time budget is used up."""


class Skip(Exception):
    """The stage can't run here (its code says why); not a failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Paused(Exception):
    """An admin pressed Pause (or turned understanding off)."""


class FingerprintAnalyser:
    """Built in: size + SHA-256 of the first and last 1 MB, through the
    read-rate bucket, so a later move or rename keeps the results."""

    name = "fingerprint"
    kinds = frozenset({"image", "video"})
    order = 0
    deepens = False

    def wanted(self, item: Item, sw: dict[str, Any]) -> bool:
        return item.fingerprint is None

    async def run(self, item: Item, ctx: "StepContext") -> None:
        async with ctx.charge():
            fp = await ctx.fingerprint()
        async with session_scope() as s:
            await s.execute(text("UPDATE media_items SET fingerprint = :f WHERE id = :id"),
                            {"f": fp, "id": item.id})
        item.fingerprint = fp


register(FingerprintAnalyser())


class StepContext:
    """What an analyser gets for one item and one pass."""

    def __init__(self, runner: "Runner", item: Item, analyser: str, *, pass_kind: str | None,
                 progress: dict[str, Any], spent_s: float, time_budget_s: float | None) -> None:
        self.runner = runner
        self.item = item
        self.analyser = analyser
        self.pass_kind = pass_kind
        #: Saved progress from an earlier start (resume); ``save`` replaces it.
        self.progress = progress
        #: Seconds of work already spent on this item's pass (all starts).
        self.spent_s = spent_s
        #: The per-item processing-time budget, None for no cap.
        self.time_budget_s = time_budget_s
        self.work_dir = WORK_ROOT / str(item.id)

    # ── time ────────────────────────────────────────────────────────────
    def check_time(self) -> None:
        if self.time_budget_s is not None and self.spent_s >= self.time_budget_s:
            raise TimeCapReached()

    @contextlib.asynccontextmanager
    async def charge(self) -> AsyncIterator[None]:
        """Count the block's time against the budget (refused once spent)."""
        self.check_time()
        t0 = self.runner.clock()
        try:
            yield
        finally:
            self.spent_s += max(0.0, self.runner.clock() - t0)

    # ── voice first ─────────────────────────────────────────────────────
    async def model_step(self, make: Callable[[], Awaitable[Any]], *, vision: bool = False,
                         needs_ai: bool = True) -> Any:
        """One model call: waits for quiet (and, for ``vision``, memory),
        cancelled when a voice turn starts and asked again after it.
        Only the time the call itself ran counts against the budget."""
        self.check_time()
        if needs_ai and ai_features.turned_off():
            raise Skip(CODE_AI_OFF)
        if vision:
            await self.runner.wait_for_memory(self.item)
            self.runner.vision_used = True
        await self.runner.wait_for_quiet(self.item)

        async def timed() -> Any:
            t0 = self.runner.clock()
            try:
                return await make()
            finally:
                self.spent_s += max(0.0, self.runner.clock() - t0)

        return await self.runner.model_step(self.item, timed)

    def hold(self) -> Callable[[Any], Awaitable[None]]:
        """The ``hold`` for a helper program (``chat_video.media._run``):
        held still during a voice turn; Pause ends it."""
        return self.runner.pass_hold(self.item)

    # ── reading ─────────────────────────────────────────────────────────
    async def read(self, offset: int, length: int) -> bytes:
        assert self.item.path is not None
        return await ratelimit.read_range(self.item.path, offset, length, bucket=self.runner.bucket)

    async def fingerprint(self) -> str:
        assert self.item.path is not None
        return await ratelimit.fingerprint(self.item.path, bucket=self.runner.bucket)

    # ── budget and resume ───────────────────────────────────────────────
    async def frame_budget(self, video_seconds: float, scene_count: int) -> tuple[int, float]:
        """``(max_frames, time_budget_s)`` for this video and pass, from the
        stored benchmark (measured first when there is none for the
        current vision model) and the admin's cap. Sets the time cap."""
        tier = await self.runner.tier()
        frames, time_s = budget.frame_budget(video_seconds, scene_count, tier, self.pass_kind or "deep",
                                             switches.admin_cap(self.runner.sw))
        self.time_budget_s = time_s
        return frames, time_s

    async def save(self, progress: dict[str, Any] | None = None) -> None:
        if progress is not None:
            self.progress = progress
        async with session_scope() as s:
            await s.execute(text(
                "UPDATE media_item_stages SET progress = CAST(:p AS JSONB), spent_s = :t, updated_at = now() "
                "WHERE item_id = :i AND analyser = :a"),
                {"p": json.dumps(self.progress), "t": self.spent_s, "i": self.item.id, "a": self.analyser})
        await self.runner.notify()


# ─── the runner ──────────────────────────────────────────────────────────


class Brains(Protocol):
    async def describe(self, jpeg: bytes) -> str: ...

    async def release_vision(self) -> None: ...


def _default_brains() -> Brains:
    from domovoi.chat_video.runner import default_brains

    return default_brains()


async def _ollama_placement() -> dict[str, Any] | None:
    """Where Ollama placed the vision model (``size``, ``size_vram``)."""
    from domovoi.clients import ollama
    from domovoi.config import settings

    want = (settings.ollama_vision_model or "").strip()
    for m in await ollama.ps():
        if isinstance(m, dict) and (m.get("name") == want or m.get("model") == want):
            return {"size": m.get("size"), "size_vram": m.get("size_vram")}
    return None


async def _ollama_model_bytes(model: str) -> int | None:
    from domovoi.clients import ollama

    for m in await ollama.list_models() or []:
        if isinstance(m, dict) and (m.get("name") == model or m.get("model") == model):
            size = m.get("size")
            return int(size) if isinstance(size, (int, float)) else None
    return None


def _memory() -> tuple[int, int]:
    """(available, total) bytes: ``MemAvailable`` on Linux."""
    import psutil

    vm = psutil.virtual_memory()
    return int(vm.available), int(vm.total)


def _local_now() -> datetime:
    from domovoi.quiet_hours import _local_now as now

    return now()


@dataclass
class _BenchJob:
    """The benchmark's stand-in job (for the voice-first waits)."""

    id: str = "benchmark"
    waiting: bool = False


class Runner(VoiceFirst):
    """Chat videos first, then library work in its windows (:meth:`step`)."""

    label = "media understanding"
    passthrough = (Paused, Skip, TimeCapReached)
    stopped = Paused

    def __init__(self, *, chat: Any = None, brains: Brains | None = None,
                 bucket: ratelimit.TokenBucket | None = None,
                 memory: Callable[[], tuple[int, int]] = _memory,
                 model_bytes: Callable[[str], Awaitable[int | None]] = _ollama_model_bytes,
                 placement: Callable[[], Awaitable[dict[str, Any] | None]] = _ollama_placement,
                 vision_model: Callable[[], str] | None = None,
                 quiet_hours: Callable[[], tuple[str, str] | None] | None = None,
                 now: Callable[[], datetime] = _local_now,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 quiet_sec: float = QUIET_SEC, poll_sec: float = POLL_SEC,
                 memory_poll_s: float = MEMORY_POLL_S) -> None:
        super().__init__(quiet_sec=quiet_sec, poll_sec=poll_sec)
        self._chat = chat
        self._brains = brains
        self.bucket = bucket or ratelimit.BUCKET
        self._memory = memory
        self._model_bytes = model_bytes
        self._placement = placement
        self._vision_model = vision_model or self._configured_vision_model
        self._quiet_hours = quiet_hours or self._house_hours
        self._now = now
        self.clock = clock
        self._sleep = sleep
        self.memory_poll_s = memory_poll_s
        self.sw: dict[str, Any] = dict(switches.DEFAULTS)
        #: Daytime duty cycle: no trickle item before this (monotonic).
        self.rest_until = 0.0
        self.vision_used = False
        self.memory_waits = 0
        self._last_notify = 0.0
        self._last_pause_check = 0.0
        self._phase = None
        self.current: int | None = None
        #: (vision model, AI on) the analysers' requeue last ran for.
        self._requeue_key: tuple[str, bool] | None = None

    # ── collaborators ───────────────────────────────────────────────────
    @property
    def brains(self) -> Brains:
        if self._brains is None:
            self._brains = _default_brains()
        return self._brains

    @property
    def chat(self) -> Any:
        if self._chat is None:
            from domovoi.chat_video.runner import Runner as ChatRunner

            self._chat = ChatRunner()
        return self._chat

    @staticmethod
    def _configured_vision_model() -> str:
        from domovoi.config import settings

        return (settings.ollama_vision_model or "").strip()

    @staticmethod
    def _house_hours() -> tuple[str, str] | None:
        from domovoi.quiet_hours import house_hours

        return house_hours()

    # ── voice first: this runner's hooks ────────────────────────────────
    async def _mark_waiting(self, job: Any, waiting: bool) -> None:
        async with session_scope() as s:
            await s.execute(text(
                "UPDATE media_runner_state SET waiting_for_voice = :w, updated_at = now() WHERE id = 1"),
                {"w": waiting})
        await self.notify(force=True)

    async def _stop_requested(self, job: Any) -> bool:
        now = self.clock()
        if now - self._last_pause_check < PAUSE_CHECK_S:
            return False
        self._last_pause_check = now
        async with session_scope() as s:
            sw = await switches.read(s)
        return bool(sw["paused"] or not sw["enabled"])

    # ── state for the dashboard ─────────────────────────────────────────
    async def set_phase(self, phase: str, item: int | None = None) -> None:
        if (phase, item) == self._phase:
            return
        self._phase = (phase, item)
        async with session_scope() as s:
            await s.execute(text(
                "UPDATE media_runner_state SET phase = :p, current_item = :i, updated_at = now() WHERE id = 1"),
                {"p": phase, "i": item})
        await self.notify(force=True)

    async def notify(self, force: bool = False) -> None:
        """A progress NOTIFY, at most every 10 s unless ``force``."""
        now = self.clock()
        if not force and now - self._last_notify < NOTIFY_EVERY_S:
            return
        self._last_notify = now
        try:
            async with session_scope() as s:
                await s.execute(text("SELECT pg_notify('media_understanding_changed', 'progress')"))
        except Exception:  # noqa: BLE001 — a missed notify only delays a redraw
            pass

    # ── windows ─────────────────────────────────────────────────────────
    def in_night(self) -> bool:
        from domovoi.quiet_hours import in_window

        hours = self._quiet_hours()
        if hours is None:
            # Quiet hours off: the house's default night still bounds the backlog.
            hours = ("22:00", "07:00")
        return in_window(self._now(), *hours)

    def trickle_ready(self) -> bool:
        """Daytime: 10 minutes since the last voice turn, and the duty
        cycle's rest is over."""
        if self.clock() < self.rest_until:
            return False
        snap = voice_activity.snapshot()
        if snap["busy"]:
            return False
        since = snap.get("since_last_ms")
        return since is None or since >= TRICKLE_IDLE_S * 1000

    def mode(self) -> str | None:
        """``night`` (the backlog), ``day`` (new arrivals only) or None."""
        if self.sw.get("run_now") or self.in_night():
            return "night"
        return "day" if self.trickle_ready() else None

    # ── the memory gate ─────────────────────────────────────────────────
    async def vision_need(self) -> int:
        model = self._vision_model()
        size = None
        try:
            size = await self._model_bytes(model) if model else None
        except Exception:  # noqa: BLE001
            size = None
        return int(size or VISION_MODEL_GUESS_BYTES) + MEMORY_HEADROOM

    async def wait_for_memory(self, item: Any) -> None:
        need = await self.vision_need()
        waited = False
        while True:
            available, _total = self._memory()
            if available >= need:
                break
            if not waited:
                waited = True
                self.memory_waits += 1
                await self.set_phase("waiting_for_memory", getattr(item, "id", None))
            if await self._stop_requested(item):
                raise Paused()
            await self._sleep(self.memory_poll_s)
        if waited:
            await self.set_phase("working", getattr(item, "id", None))

    async def release_after_batch(self) -> None:
        """On the floor tier, let the vision model go after a batch."""
        if not self.vision_used:
            return
        self.vision_used = False
        _avail, total = self._memory()
        if total < FLOOR_TIER_BYTES:
            try:
                await self.brains.release_vision()
            except Exception as e:  # noqa: BLE001
                log.debug("media understanding: release failed: %s", type(e).__name__)

    # ── the benchmark ───────────────────────────────────────────────────
    async def tier(self) -> budget.Tier:
        model = self._vision_model()
        if not model or ai_features.turned_off():
            return budget.Tier()

        job = _BenchJob()

        async def measure() -> dict[str, Any]:
            async def describe(jpeg: bytes) -> Any:
                return await self.model_step(job, lambda: self.brains.describe(jpeg))

            await self.wait_for_memory(job)
            self.vision_used = True
            return await budget.run_benchmark(describe, model, placement=self._placement, clock=self.clock)

        bench = await budget.ensure_benchmark(session_scope, model, measure)
        return budget.tier_from_benchmark(bench)

    # ── the loop ────────────────────────────────────────────────────────
    async def run_forever(self, shutdown: asyncio.Event) -> None:
        try:
            from domovoi.chat_video.runner import sweep_at_boot

            await sweep_at_boot()
        except Exception as e:  # noqa: BLE001
            log.warning("media understanding: the chat-video boot sweep failed: %s", type(e).__name__)
        while not shutdown.is_set():
            task = asyncio.ensure_future(self.step())
            stop = asyncio.ensure_future(shutdown.wait())
            try:
                await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                stop.cancel()
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
            if not task.done() or task.cancelled():
                break
            try:
                did = task.result()
            except Exception as e:  # noqa: BLE001 — the next round tries again
                log.warning("media understanding: the runner stumbled: %s", type(e).__name__)
                did = False
            if not did:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(shutdown.wait(), timeout=IDLE_POLL_S)

    async def step(self) -> bool:
        """One unit of work: a chat video if one waits, else one library
        item in its window. False when there was nothing to do."""
        if await self.chat.run_next():
            return True
        return await self.library_step()

    async def library_step(self) -> bool:
        async with session_scope() as s:
            self.sw = await switches.read(s)
        if not self.sw["enabled"]:
            await self.set_phase("off")
            return False
        if self.sw["paused"]:
            await self.set_phase("paused")
            return False
        await self.requeue_if_changed()
        for name, fn in list(_HOUSEKEEPING.items()):
            try:
                await fn(self)
            except Exception as e:  # noqa: BLE001 — the item work goes on
                log.warning("media understanding: %s housekeeping failed: %s", name, type(e).__name__)
        mode = self.mode()
        if mode is None:
            await self.release_after_batch()
            await self.set_phase("waiting_for_window")
            return False
        item = await self.claim(mode)
        if item is None:
            await self.release_after_batch()
            if self.sw.get("run_now") and mode == "night":
                await self._clear_run_now()
            await self.set_phase("idle")
            return False
        t0 = self.clock()
        self.current = item.id
        try:
            await self.set_phase("working", item.id)
            await self.run_item(item)
        except Paused:
            await self.set_phase("paused")
        finally:
            self.current = None
        if mode == "day":
            # 1:1 duty cycle: rest as long as the item took.
            self.rest_until = self.clock() + (self.clock() - t0)
        return True

    async def requeue_if_changed(self) -> int:
        """At start and after the vision model or the AI switch changed,
        each analyser's ``requeue`` (out-of-date results back in the
        backlog). The number of items requeued."""
        key = (self._vision_model(), ai_features.enabled())
        if key == self._requeue_key:
            return 0
        n = 0
        for a in analysers():
            hook = getattr(a, "requeue", None)
            if hook is None:
                continue
            async with session_scope() as s:
                n += int(await hook(s, key[0], key[1]) or 0)
        self._requeue_key = key
        if n:
            log.info("media understanding: %d items queued again (vision model or AI switch changed)", n)
            await self.notify(force=True)
        return n

    async def _clear_run_now(self) -> None:
        async with session_scope() as s:
            sw = await switches.read(s)
            if sw["run_now"]:
                sw["run_now"] = False
                await switches.write(s, sw)

    async def unpassed_videos(self) -> int:
        async with session_scope() as s:
            return int((await s.execute(text(
                "SELECT count(*) FROM media_items WHERE kind = 'video' AND frame_pass IS NULL "
                "AND state IN ('pending', 'running')"))).scalar() or 0)

    async def claim(self, mode: str) -> Item | None:
        """The next item for ``mode``: one left running by a restart first,
        then the newest pending file; at night with nothing pending, a
        quick-passed video to deepen."""
        cond = "state IN ('pending', 'running')"
        if mode == "day":
            cond += " AND new_arrival AND first_seen_at > now() - make_interval(secs => :new_s)"
        async with session_scope() as s:
            row = (await s.execute(text(
                "UPDATE media_items SET state = 'running', attempts = attempts + 1, updated_at = now() "
                f"WHERE id = (SELECT id FROM media_items WHERE {cond} "
                "ORDER BY (state = 'running') DESC, mtime DESC, id DESC LIMIT 1 FOR UPDATE SKIP LOCKED) "
                "RETURNING *"), {"new_s": TRICKLE_NEW_S})).mappings().first()
        if row is None and mode == "night" and await self.unpassed_videos() == 0:
            row = await self._claim_deepen()
        return self._with_path(Item.from_row(row)) if row is not None else None

    async def _claim_deepen(self) -> Any:
        deep = [a.name for a in analysers() if getattr(a, "deepens", False)]
        if not deep:
            return None
        async with session_scope() as s:
            row = (await s.execute(text(
                "UPDATE media_items SET state = 'running', attempts = 1, updated_at = now() "
                "WHERE id = (SELECT id FROM media_items WHERE kind = 'video' AND frame_pass = 'quick' "
                "AND state = 'done' ORDER BY mtime DESC, id DESC LIMIT 1 FOR UPDATE SKIP LOCKED) "
                "RETURNING *"))).mappings().first()
            if row is not None:
                await s.execute(text(
                    "UPDATE media_item_stages SET state = 'pending', attempts = 0, spent_s = 0, capped = false, "
                    "pass_kind = 'deep' WHERE item_id = :i AND analyser = ANY(:a)"),
                    {"i": row["id"], "a": deep})
        return row

    def _with_path(self, item: Item) -> Item:
        for lib in catalog.core_libraries():
            if lib.id == item.library_id:
                item.path = lib.root / item.rel_path
        return item

    async def _stages(self, item_id: int) -> dict[str, Any]:
        async with session_scope() as s:
            rows = (await s.execute(text(
                "SELECT analyser, state, pass_kind, progress, attempts, spent_s FROM media_item_stages "
                "WHERE item_id = :i"), {"i": item_id})).mappings().all()
        return {r["analyser"]: r for r in rows}

    async def run_item(self, item: Item) -> None:
        """Every wanted analyser on ``item``, cheapest first, each from its
        saved progress."""
        if item.attempts > MAX_ATTEMPTS:
            await self._end_item(item, "failed", CODE_INTERRUPTED)
            return
        if item.path is None or not item.path.is_file():
            # Gone, or its library not there: the walker deletes it or, when
            # the file is back, queues it again.
            await self._end_item(item, "skipped", CODE_MISSING, attempts_back=True)
            return
        stages = await self._stages(item.id)
        pass_kind = None
        if item.kind == "video":
            pass_kind = budget.choose_pass(item.frame_pass, await self.unpassed_videos()) or "deep"
        spent = sum(float(r["spent_s"] or 0) for r in stages.values())
        time_budget = None
        if item.kind == "video":
            tier_kind = "cpu"
            async with session_scope() as s:
                bench = await budget.stored_benchmark(s)
            if bench:
                tier_kind = budget.tier_from_benchmark(bench).kind
            time_budget = budget.TIME_BUDGET_S[(tier_kind, pass_kind)]
            if switches.admin_cap(self.sw) and pass_kind == "deep":
                time_budget = budget.MAX_VIDEO_BUDGET_S
        failed = False
        # A video that already had a frame pass keeps it unless a frame
        # analyser ran again now (a faces-only pass mustn't mark a quick
        # video deep).
        ran_frames = False
        for a in analysers():
            if item.kind not in a.kinds or not a.wanted(item, self.sw):
                continue
            row = stages.get(a.name)
            if row is not None and row["state"] in ("done", "skipped", "failed"):
                continue
            ctx = StepContext(self, item, a.name, pass_kind=pass_kind,
                              progress=dict(row["progress"] or {}) if row else {},
                              spent_s=spent, time_budget_s=time_budget)
            await self._stage_start(item, a.name, pass_kind)
            ran_frames = ran_frames or bool(getattr(a, "deepens", False))
            before = ctx.spent_s
            try:
                await a.run(item, ctx)
                await self._stage_end(item, a.name, "done", ctx)
            except TimeCapReached:
                await self._stage_end(item, a.name, "done", ctx, capped=True)
            except Skip as e:
                await self._stage_end(item, a.name, "skipped", ctx, code=e.code)
            except Paused:
                await ctx.save()
                await self._end_item(item, "pending", None, attempts_back=True)
                raise
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await ctx.save()
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("media understanding: %s on item %d failed: %s", a.name, item.id, type(e).__name__)
                tries = await self._stage_fail(item, a.name, ctx)
                if tries >= MAX_ATTEMPTS:
                    await self._end_item(item, "failed", CODE_FAILED)
                else:
                    await self._end_item(item, "pending", None)
                failed = True
                break
            finally:
                shutil.rmtree(ctx.work_dir, ignore_errors=True)
            spent += ctx.spent_s - before
        if not failed:
            keep = item.frame_pass is not None and not ran_frames
            await self._end_item(item, "done", None, frame_pass=None if keep else pass_kind)
        await self.notify()

    async def _stage_start(self, item: Item, name: str, pass_kind: str | None) -> None:
        async with session_scope() as s:
            await s.execute(text(
                "INSERT INTO media_item_stages (item_id, analyser, state, pass_kind, attempts) "
                "VALUES (:i, :a, 'running', :p, 1) ON CONFLICT (item_id, analyser) DO UPDATE SET "
                "state = 'running', pass_kind = COALESCE(EXCLUDED.pass_kind, media_item_stages.pass_kind), "
                "attempts = media_item_stages.attempts + 1, updated_at = now()"),
                {"i": item.id, "a": name, "p": pass_kind})
            await s.execute(text("UPDATE media_items SET stage = :a WHERE id = :i"), {"a": name, "i": item.id})

    async def _stage_end(self, item: Item, name: str, state: str, ctx: StepContext, *,
                         capped: bool = False, code: str | None = None) -> None:
        async with session_scope() as s:
            await s.execute(text(
                "UPDATE media_item_stages SET state = :st, progress = CAST(:p AS JSONB), spent_s = :t, "
                "capped = :c, error_code = :code, updated_at = now() WHERE item_id = :i AND analyser = :a"),
                {"st": state, "p": json.dumps(ctx.progress), "t": ctx.spent_s, "c": capped, "code": code,
                 "i": item.id, "a": name})

    async def _stage_fail(self, item: Item, name: str, ctx: StepContext) -> int:
        async with session_scope() as s:
            tries = (await s.execute(text(
                "UPDATE media_item_stages SET state = CASE WHEN attempts >= :m THEN 'failed' ELSE 'pending' END, "
                "progress = CAST(:p AS JSONB), spent_s = :t, error_code = :code, updated_at = now() "
                "WHERE item_id = :i AND analyser = :a RETURNING attempts"),
                {"m": MAX_ATTEMPTS, "p": json.dumps(ctx.progress), "t": ctx.spent_s, "code": CODE_FAILED,
                 "i": item.id, "a": name})).scalar()
        return int(tries or 0)

    async def _end_item(self, item: Item, state: str, code: str | None, *, frame_pass: str | None = None,
                        attempts_back: bool = False) -> None:
        async with session_scope() as s:
            await s.execute(text(
                "UPDATE media_items SET state = :st, error_code = :code, stage = NULL, "
                "attempts = CASE WHEN :back THEN GREATEST(attempts - 1, 0) "
                "WHEN :st = 'done' THEN 0 ELSE attempts END, "
                "frame_pass = COALESCE(:fp, frame_pass), "
                "analysed_at = CASE WHEN :st = 'done' THEN now() ELSE analysed_at END, updated_at = now() "
                "WHERE id = :i"),
                {"st": state, "code": code, "back": attempts_back, "fp": frame_pass, "i": item.id})


async def progress(s: Any) -> dict[str, Any]:
    """What the Settings card shows: per kind done / left / failed, the
    runner's phase, and the benchmark."""
    rows = (await s.execute(text(
        "SELECT kind, state, count(*) AS n FROM media_items GROUP BY kind, state"))).mappings().all()
    kinds: dict[str, dict[str, int]] = {k: {"total": 0, "done": 0, "left": 0, "failed": 0}
                                        for k in ("image", "video")}
    for r in rows:
        k = kinds.setdefault(r["kind"], {"total": 0, "done": 0, "left": 0, "failed": 0})
        n = int(r["n"])
        k["total"] += n
        if r["state"] in ("done", "skipped"):
            k["done"] += n
        elif r["state"] == "failed":
            k["failed"] += n
        else:
            k["left"] += n
    st = (await s.execute(text(
        "SELECT phase, waiting_for_voice, current_item, rescan_requested, benchmark, updated_at "
        "FROM media_runner_state WHERE id = 1"))).mappings().first()
    bench = st["benchmark"] if st else None
    if isinstance(bench, str):
        bench = json.loads(bench)
    return {
        "photos": kinds["image"],
        "videos": kinds["video"],
        "phase": st["phase"] if st else "idle",
        "waiting_for_voice": bool(st and st["waiting_for_voice"]),
        "rescan_requested": bool(st and st["rescan_requested"]),
        "benchmark": bench,
        "updated_at": st["updated_at"].isoformat() if st and st["updated_at"] else None,
    }


@dataclass
class Estimate:
    """The plain estimate ("about 9 nights left"), from the stored speed."""

    seconds: float
    nights: int = field(default=0)


def estimate(left_photos: int, left_videos: int, bench: dict[str, Any] | None,
             night_hours: float = 9.0) -> Estimate:
    tier = budget.tier_from_benchmark(bench)
    per_photo = tier.speed
    _frames, per_video = budget.frame_budget(600.0, 10, tier, "quick")
    secs = left_photos * per_photo + left_videos * min(per_video, 10 * per_photo + 120)
    nights = int(-(-secs // (night_hours * 3600))) if secs > 0 else 0
    return Estimate(seconds=secs, nights=nights)
