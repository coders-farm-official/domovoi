"""The acquisition time budget (domovoi/acquisitions.py, "The budget").

Each tick fails, for good, every claimed acquisition that has run longer
than ``settings.acquisition_max_seconds``, whatever the fulfilling plugin's
own limits are: its queue slot is free again, the page shows it as failed,
and the fulfiller's late completion is refused. One small UPDATE; runs
every ``acquisition_budget_sweep_interval_sec`` (default one minute).
"""

from __future__ import annotations

from domovoi.acquisitions import ACQUISITIONS
from domovoi.db.session import session_scope
from domovoi.workers.base import Worker


class AcquisitionBudgetSweeper(Worker):
    name = "acquisition_budget_sweeper"
    enabled_setting = None
    interval_setting = "acquisition_budget_sweep_interval_sec"
    stub_suppressed = True

    async def tick(self) -> int:
        async with session_scope() as s:
            return await ACQUISITIONS.sweep_over_budget(s)
