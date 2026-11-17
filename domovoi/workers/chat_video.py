"""Videos attached in chat (V064): the core's runner, as a long-run worker.

One job at a time, holding off while any voice turn is under way; the job
itself is :mod:`domovoi.chat_video.runner`. Skipped under stubs (the tests
drive the runner directly). Core-internal: not offered to plugins.
"""

from __future__ import annotations

import asyncio

from domovoi.workers.base import LongRunWorker


class ChatVideoWorker(LongRunWorker):
    name = "chat_video"
    enabled_setting = None
    stub_suppressed = True

    def __init__(self, runner=None) -> None:
        self._runner = runner

    async def run(self, shutdown: asyncio.Event) -> None:
        from domovoi.chat_video.runner import Runner

        await (self._runner or Runner()).run_forever(shutdown)
