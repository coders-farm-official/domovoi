"""Keep voice history (owner, 2026-10-17): the daily cleanup.

Each tick first finishes any "Delete all voice history" still open (one a
restart cut short), then, once a day, deletes everything older than
``settings.voice_history_keep`` (30 days, 90 by default, a year; nothing
for forever) from the conversation and intent logs, the rooms'
short-term conversations and the satellites' logs kept on this server —
:func:`domovoi.voice_history.prune_before`, in small batches with a pause
between, so neither Postgres nor the event loop is held for long.

The tick runs every ``voice_history_pruner_interval_sec`` (an hour) but a
retention run happens at most once a day (``voice_history.RETENTION_EVERY``,
read from the last run recorded in ``voice_history_jobs``, so a restart
doesn't make it run again). Saving the setting runs one at once (the
field's reapply hook). Stub-suppressed like the other workers that write
on their own: the suite drives :meth:`tick` and the functions directly.
"""

from __future__ import annotations

from typing import Any

from domovoi import voice_history
from domovoi.workers.base import Worker


class VoiceHistoryPruner(Worker):
    name = "voice_history_pruner"
    enabled_setting = None
    interval_setting = "voice_history_pruner_interval_sec"
    stub_suppressed = True

    async def tick(self) -> dict[str, Any]:
        jobs = await voice_history.run_pending_jobs()
        retention = await voice_history.run_retention()
        return {"delete_all": jobs, "retention": retention}
