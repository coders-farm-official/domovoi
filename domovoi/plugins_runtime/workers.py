"""Declarative worker registry (design §4.5).

Plugins implement only ``tick()`` (poll shape) or ``run(shutdown)``
(long-run shape); :class:`WorkerRunner` owns start/stop, the
``asyncio.wait_for(stop.wait(), interval)`` poll loop, reverse-order
shutdown, and the LongRunWorker crash policy — restart with exponential
backoff (1 s doubling to a 60 s cap, reset after 10 min healthy) and a
``consecutive_failures`` counter surfaced on
``GET /v1/plugins/<slug>/status`` (§4.14).

Startup hooks are NAMED (registered ``<slug>.<name>`` — the key manifest
``kind="startup"`` entries cross-check against, §13.2 check 3), ordered
via ``after=``, and connectivity-gated: a ``requires_online`` hook fires
immediately when online, else on the first
``core.connectivity_changed → online`` event.

Single-process execution is retained deliberately — workers run on the
core event loop, which keeps in-memory state assumptions valid
(dossier §7 inv. 14).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from domovoi.config import settings
from domovoi.events import EVENTS

# The two worker ABCs live in domovoi/workers/base.py so worker modules
# stay importable by the web process without touching the plugin runtime
# (design §5.1). Re-exported here — plugin code imports them from this
# module per the SDK docs.
from domovoi.workers.base import LongRunWorker, Worker  # noqa: F401

log = logging.getLogger(__name__)

_LONGRUN_BACKOFF_INITIAL = 1.0
_LONGRUN_BACKOFF_CAP = 60.0
_LONGRUN_HEALTHY_RESET_SEC = 600.0


@dataclass
class StartupHook:
    name: str                      # full "<slug>.<name>"
    fn: Callable[[], Awaitable[None]]
    requires_online: bool = False
    after: str | None = None       # full hook name this one waits for
    state: str = "pending"         # pending | fired | failed
    error: str | None = None


@dataclass
class _WorkerEntry:
    worker: Worker | LongRunWorker
    owner: str
    kind: str                          # "poll" | "longrun"
    settings_source: Any
    task: asyncio.Task[None] | None = None
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    state: str = "stopped"             # running | stopped | backoff | suppressed
    last_tick_at: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    next_attempt_at: datetime | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WorkerRunner:
    """Owns every registered plugin worker + startup hook, keyed by owner
    slug so disable/uninstall tears down exactly one plugin's set."""

    def __init__(self) -> None:
        self._workers: dict[str, list[_WorkerEntry]] = {}   # owner → entries
        self._hooks: dict[str, list[StartupHook]] = {}      # owner → hooks
        self._hook_done: dict[str, asyncio.Event] = {}      # full name → done

    # ── registration ───────────────────────────────────────────────────────

    def add_worker(
        self,
        worker: Worker | LongRunWorker,
        *,
        owner: str,
        settings_source: Any | None = None,
    ) -> None:
        if isinstance(worker, Worker):
            kind = "poll"
            if not worker.interval_setting:
                raise ValueError(
                    f"worker {worker.name!r}: poll workers must declare "
                    f"interval_setting (the settings field holding the cadence)"
                )
        elif isinstance(worker, LongRunWorker):
            kind = "longrun"
        else:
            raise TypeError(
                f"{worker!r} is neither Worker nor LongRunWorker"
            )
        self._workers.setdefault(owner, []).append(
            _WorkerEntry(
                worker=worker,
                owner=owner,
                kind=kind,
                settings_source=settings_source or settings,
            )
        )

    def add_startup_hook(
        self,
        fn: Callable[[], Awaitable[None]],
        *,
        owner: str,
        name: str,
        requires_online: bool = False,
        after: str | None = None,
    ) -> StartupHook:
        if not name:
            raise ValueError("startup hooks require a name= (design §4.5)")
        full = f"{owner}.{name}"
        hook = StartupHook(
            name=full, fn=fn, requires_online=requires_online, after=after
        )
        self._hooks.setdefault(owner, []).append(hook)
        self._hook_done.setdefault(full, asyncio.Event())
        return hook

    def mark_core_hook_done(self, full_name: str) -> None:
        """Core lifespan calls this as its own milestones pass so plugin
        hooks can ``after="core.library_index"`` etc. (§12 name table)."""
        self._hook_done.setdefault(full_name, asyncio.Event()).set()

    # ── lifecycle ───────────────────────────────────────────────────────────

    async def start_owner(self, owner: str) -> None:
        for entry in self._workers.get(owner, []):
            self._start_entry(entry)
        for hook in self._hooks.get(owner, []):
            self._fire_hook(hook)

    async def stop_owner(self, owner: str) -> None:
        """Reverse-order shutdown of one owner's workers; hooks are dropped."""
        for entry in reversed(self._workers.get(owner, [])):
            entry.stop.set()
            if entry.task is not None:
                try:
                    await asyncio.wait_for(entry.task, timeout=10)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    entry.task.cancel()
                except Exception:  # noqa: BLE001 — task exceptions already logged
                    pass
                entry.task = None
            entry.state = "stopped"
        for hook in self._hooks.get(owner, []):
            self._hook_done.pop(hook.name, None)

    def remove_owner(self, owner: str) -> None:
        """Registration teardown AFTER stop_owner (disable/uninstall)."""
        self._workers.pop(owner, None)
        self._hooks.pop(owner, None)

    async def stop_all(self) -> None:
        for owner in list(self._workers):
            await self.stop_owner(owner)

    # ── status (§4.14) ──────────────────────────────────────────────────────

    def status(self, owner: str) -> dict[str, list[dict[str, Any]]]:
        workers = [
            {
                "name": e.worker.name,
                "kind": e.kind,
                "state": e.state,
                "last_tick_at": e.last_tick_at.isoformat() if e.last_tick_at else None,
                "last_error": e.last_error,
                "consecutive_failures": e.consecutive_failures,
                "next_attempt_at": e.next_attempt_at.isoformat() if e.next_attempt_at else None,
            }
            for e in self._workers.get(owner, [])
        ]
        hooks = [
            {"name": h.name, "state": h.state, "error": h.error}
            for h in self._hooks.get(owner, [])
        ]
        return {"workers": workers, "startup_hooks": hooks}

    def worker_names(self, owner: str) -> dict[str, str]:
        """name → kind, for the §13.2 manifest cross-check."""
        return {e.worker.name: e.kind for e in self._workers.get(owner, [])}

    def hook_names(self, owner: str) -> list[str]:
        prefix = f"{owner}."
        return [
            h.name.removeprefix(prefix) for h in self._hooks.get(owner, [])
        ]

    # ── internals ───────────────────────────────────────────────────────────

    def _resolve_setting(self, entry: _WorkerEntry, field_name: str) -> Any:
        return getattr(entry.settings_source, field_name, None)

    def _enabled(self, entry: _WorkerEntry) -> bool:
        w = entry.worker
        if w.enabled_setting:
            return bool(self._resolve_setting(entry, w.enabled_setting))
        return True

    def _start_entry(self, entry: _WorkerEntry) -> None:
        if entry.task is not None and not entry.task.done():
            return
        if entry.worker.stub_suppressed and settings.use_stubs:
            entry.state = "suppressed"
            return
        if not self._enabled(entry):
            entry.state = "stopped"
            return
        entry.stop = asyncio.Event()
        if entry.kind == "poll":
            entry.task = asyncio.create_task(
                self._poll_loop(entry), name=f"worker:{entry.owner}:{entry.worker.name}"
            )
        else:
            entry.task = asyncio.create_task(
                self._longrun_loop(entry),
                name=f"worker:{entry.owner}:{entry.worker.name}",
            )
        entry.state = "running"

    async def _poll_loop(self, entry: _WorkerEntry) -> None:
        worker = entry.worker
        assert isinstance(worker, Worker)
        while not entry.stop.is_set():
            interval = float(
                self._resolve_setting(entry, worker.interval_setting) or 60
            )
            if worker.requires_online and not self._online():
                pass  # skip the tick, keep cadence
            else:
                try:
                    await worker.tick()
                    entry.last_tick_at = _now()
                    entry.last_error = None
                    entry.consecutive_failures = 0
                except Exception as e:  # noqa: BLE001 — tick MUST NOT kill the loop
                    entry.last_error = str(e)
                    entry.consecutive_failures += 1
                    log.warning(
                        "worker %s.%s tick failed: %s",
                        entry.owner, worker.name, e,
                    )
            try:
                await asyncio.wait_for(entry.stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    async def _longrun_loop(self, entry: _WorkerEntry) -> None:
        """§4.5 crash policy: restart run() with exponential backoff."""
        worker = entry.worker
        assert isinstance(worker, LongRunWorker)
        backoff = _LONGRUN_BACKOFF_INITIAL
        while not entry.stop.is_set():
            started = asyncio.get_running_loop().time()
            try:
                entry.state = "running"
                await worker.run(entry.stop)
                if entry.stop.is_set():
                    break
                entry.last_error = "run() returned before shutdown was set"
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — the safety-net restart
                entry.last_error = str(e)
            entry.consecutive_failures += 1
            healthy_for = asyncio.get_running_loop().time() - started
            if healthy_for >= _LONGRUN_HEALTHY_RESET_SEC:
                backoff = _LONGRUN_BACKOFF_INITIAL
            entry.state = "backoff"
            entry.next_attempt_at = _now()
            log.warning(
                "long-run worker %s.%s stopped unexpectedly (%s) — "
                "restarting in %.0fs (failure #%d)",
                entry.owner, worker.name, entry.last_error, backoff,
                entry.consecutive_failures,
            )
            try:
                await asyncio.wait_for(entry.stop.wait(), timeout=backoff)
                break
            except asyncio.TimeoutError:
                backoff = min(backoff * 2, _LONGRUN_BACKOFF_CAP)
        entry.state = "stopped"

    def _online(self) -> bool:
        from domovoi.connectivity import current_probe

        probe = current_probe()
        return True if probe is None else bool(probe.online)

    def _fire_hook(self, hook: StartupHook) -> None:
        async def _run() -> None:
            try:
                if hook.after:
                    dep = self._hook_done.setdefault(hook.after, asyncio.Event())
                    await dep.wait()
                if hook.requires_online and not self._online():
                    online_event = asyncio.Event()

                    async def _on_conn(event: Any) -> None:
                        payload = getattr(event, "payload", event) or {}
                        if isinstance(payload, dict) and payload.get("online"):
                            online_event.set()

                    sub = EVENTS.subscribe(
                        "core.connectivity_changed", _on_conn,
                        owner=f"_hook:{hook.name}",
                    )
                    try:
                        await online_event.wait()
                    finally:
                        EVENTS.unsubscribe(sub)
                await hook.fn()
                hook.state = "fired"
            except Exception as e:  # noqa: BLE001 — a hook must never crash boot
                hook.state = "failed"
                hook.error = str(e)
                log.warning("startup hook %s failed: %s", hook.name, e)
            finally:
                self._hook_done.setdefault(hook.name, asyncio.Event()).set()

        asyncio.create_task(_run(), name=f"startup-hook:{hook.name}")


WORKERS = WorkerRunner()
