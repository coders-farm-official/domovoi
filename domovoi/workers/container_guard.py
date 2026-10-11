"""The container guard's poll worker (domovoi/container_guard/,
docs/CONTAINER_GUARD.md).

Each tick runs one check: what Domovoi expects against what Docker
reports, debounced into incidents. The guard backs off on its own while
Docker doesn't answer, so a tick may return without looking. Stub-
suppressed: the suite drives the guard directly with a fake Docker.
"""

from __future__ import annotations

from domovoi.workers.base import Worker


class ContainerGuardWorker(Worker):
    name = "container_guard"
    enabled_setting = "container_guard_enabled"
    interval_setting = "container_guard_interval_sec"
    stub_suppressed = True

    async def tick(self) -> None:
        from domovoi.container_guard.engine import get_guard

        await get_guard().check()
