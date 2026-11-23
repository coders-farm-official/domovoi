"""Media understanding (V078): the catalogue walker and the runner.

* ``media_catalog`` (poll): looks every ``media_catalog_interval_sec``
  whether a walk is due (hourly, or after "Rescan") and walks Pictures and
  Videos by ``stat()`` (domovoi/media_understanding/catalog.py).
* ``media_understanding`` (long run): chat videos first, then library work
  in its windows, voice first (domovoi/media_understanding/runner.py).
  It replaced the 1.4.0 ``chat_video`` worker, whose jobs it runs. Its
  analysers: the fingerprint, then the videos' (media_understanding/videos.py).

Both skipped under stubs (the tests drive them directly). Core-internal.
"""

from __future__ import annotations

import asyncio

from domovoi.workers.base import LongRunWorker, Worker


class MediaCatalogWorker(Worker):
    name = "media_catalog"
    enabled_setting = None
    interval_setting = "media_catalog_interval_sec"
    stub_suppressed = True

    def __init__(self, catalog=None) -> None:
        self._catalog = catalog

    async def tick(self) -> None:
        from domovoi.db.session import session_scope
        from domovoi.media_understanding import switches
        from domovoi.media_understanding.catalog import Catalog

        if self._catalog is None:
            self._catalog = Catalog()
        async with session_scope() as s:
            sw = await switches.read(s)
        if not sw["enabled"]:
            return
        await self._catalog.tick(session_scope)


class MediaUnderstandingWorker(LongRunWorker):
    name = "media_understanding"
    enabled_setting = None
    stub_suppressed = True

    def __init__(self, runner=None) -> None:
        self._runner = runner

    async def run(self, shutdown: asyncio.Event) -> None:
        from domovoi.media_understanding import videos
        from domovoi.media_understanding.runner import Runner

        videos.install()     # slice 3: scan, faces hook, transcript, frames, summary
        await (self._runner or Runner()).run_forever(shutdown)
