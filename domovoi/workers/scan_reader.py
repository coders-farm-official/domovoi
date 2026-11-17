"""Reading scans (V065): the core's scan reader, as a long-run worker.

One read at a time, holding off while any voice turn is under way; the
read itself is :mod:`domovoi.scan_reader.runner`. Skipped under stubs (the
tests drive the runner directly). Core-internal: not offered to plugins.
"""

from __future__ import annotations

import asyncio

from domovoi.workers.base import LongRunWorker


class ScanReaderWorker(LongRunWorker):
    name = "scan_reader"
    enabled_setting = None
    stub_suppressed = True

    def __init__(self, runner=None) -> None:
        self._runner = runner

    async def run(self, shutdown: asyncio.Event) -> None:
        from domovoi.scan_reader.runner import Runner

        await (self._runner or Runner()).run_forever(shutdown)
