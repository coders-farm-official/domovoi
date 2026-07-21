"""The two declarative worker shapes (design §4.5).

Core workers and plugin workers implement ONLY ``tick()`` (poll shape)
or ``run(shutdown)`` (long-run shape) plus a handful of declarative
class attributes; the ``WorkerRunner``
(:mod:`domovoi.plugins_runtime.workers`) owns start/stop, the
``asyncio.wait_for(stop.wait(), interval)`` poll loop, reverse-order
shutdown, and the long-run crash policy. That split keeps ticks
unit-testable, as the test discipline demands (dossier
§7 inv. 5) — the start/stop paths are core-owned and tested once.

This module lives under ``domovoi/workers/`` (not the plugin runtime)
on purpose: worker modules are imported by BOTH processes (the web
backend reuses a couple of ticks), and the web process must never
import ``domovoi.plugins_runtime`` (design §5.1). The runner re-exports
these classes, so plugin code importing them from
``domovoi.plugins_runtime.workers`` keeps working.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod


class Worker(ABC):
    """Poll-loop shape. ``tick()`` SHOULD not raise — the runner catches
    and logs anyway, and failures surface on the status endpoint.

    Declarative attributes (all resolved live, per tick, so the web
    config editor's 'hot' fields apply without a restart):

    * ``name`` — unique within the owner; the §13.2 manifest cross-check
      key for plugin workers.
    * ``enabled_setting`` — a field name on the settings source gating
      the worker (``None`` = always on).
    * ``interval_setting`` — the settings field holding the poll cadence
      in seconds (REQUIRED for the poll shape).
    * ``stub_suppressed`` — skipped entirely when ``USE_STUBS=true``.
    * ``requires_online`` — skip ticks (keeping cadence) while offline.
    """

    name: str
    enabled_setting: str | None = None
    interval_setting: str = ""
    stub_suppressed: bool = True
    requires_online: bool = False

    @abstractmethod
    async def tick(self) -> None: ...


class LongRunWorker(ABC):
    """Persistent-connection shape (e.g. an upstream websocket). In-loop
    reconnects are the worker's own job; the runner's restart-with-backoff
    (1 s doubling to 60 s, reset after 10 min healthy) is the safety net
    for unhandled exceptions / early returns."""

    name: str
    enabled_setting: str | None = None
    stub_suppressed: bool = True

    @abstractmethod
    async def run(self, shutdown: asyncio.Event) -> None: ...
