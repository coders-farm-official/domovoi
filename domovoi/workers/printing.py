"""The printing workers (domovoi/printing/).

* ``printer_status`` — every 10 minutes, each saved printer's status and
  supplies (Settings re-reads them every minute while it is open, through
  ``GET /v1/printers?refresh=1``). Sleeping printers wake for a unicast
  request; a printer that misses twice reads ``offline``.
* ``print_jobs`` — every 15 seconds, any job still in flight (the job's own
  task follows it every 2 seconds for its first 10 minutes); a voice job
  that fails is said once in the room that asked.
* ``print_history`` — hourly: the job log past 90 days goes; scans left
  waiting for "another page?" are saved (every minute, so a forgotten
  session is saved within ~11 minutes).

The status and job workers talk to printers, so they are suppressed under
``USE_STUBS``; the history worker is pure DB and runs anyway. Their
cadences are constants here (:data:`CADENCE`), not settings.
"""

from __future__ import annotations

import logging

from domovoi.workers.base import Worker

log = logging.getLogger(__name__)


class _Cadence:
    """The settings source the worker runner reads cadences from."""

    printer_status_interval_sec = 600.0
    print_jobs_interval_sec = 15.0
    print_history_interval_sec = 60.0


CADENCE = _Cadence()


class PrinterStatusWorker(Worker):
    name = "printer_status"
    interval_setting = "printer_status_interval_sec"
    stub_suppressed = True

    async def tick(self) -> int:
        from domovoi.printing import devices, store

        n = 0
        for row in await store.list_printers():
            if not row.get("ipp_address"):
                continue
            try:
                await devices.refresh_status(row, force=True)
                n += 1
            except Exception as e:  # noqa: BLE001 — one printer never stops the rest
                log.debug("printer status %s: %s", row.get("name"), e)
        return n


class PrintJobsWorker(Worker):
    name = "print_jobs"
    interval_setting = "print_jobs_interval_sec"
    stub_suppressed = True

    async def tick(self) -> int:
        from domovoi.printing import jobs

        return await jobs.follow_all()


class PrintHistoryWorker(Worker):
    name = "print_history"
    interval_setting = "print_history_interval_sec"
    stub_suppressed = False

    def __init__(self) -> None:
        self._ticks = 0

    async def tick(self) -> int:
        from domovoi.printing import scans, store

        saved = await scans.sweep_idle()
        self._ticks += 1
        if self._ticks % 60 == 1:
            pruned = await store.prune_history()
            if pruned:
                log.info("print history: deleted %d entr%s older than %d days", pruned,
                         "y" if pruned == 1 else "ies", store.HISTORY_DAYS)
        return saved


def register(workers) -> None:
    """Called once from the core's lifespan."""
    from domovoi.printing import scans

    try:
        scans.clean_orphaned_spool()
    except Exception as e:  # noqa: BLE001 — a tidy-up never stops the core
        log.warning("printing: couldn't clean the scan spool: %s", e)
    for w in (PrinterStatusWorker(), PrintJobsWorker(), PrintHistoryWorker()):
        workers.add_worker(w, owner="core", settings_source=CADENCE)
