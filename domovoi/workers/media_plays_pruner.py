"""Retention pruner for the ``media_plays`` table.

The Satellites "Recently played" tab records one row per play-start
across every room and source. Left unbounded the table grows forever
(like ``conversation_log`` / ``intents_log``, which we accept), but the
user asked for a retention policy here — so this worker periodically
deletes rows older than ``settings.media_plays_retention_days``.

Pure DB work (no MPD / network), so unlike the music-source sweeper it
runs even under ``USE_STUBS`` — though in tests the table is truncated
per-test, so the DELETE is a harmless no-op. A retention of ``<= 0``
disables pruning entirely (keep history forever).

Runs once shortly after start and then every
``media_plays_pruner_interval_sec`` (default 6h); the DELETE is a cheap
indexed range scan and a no-op when nothing has aged out.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)


class MediaPlaysPruner(Worker):
    # Declarative registration (design §4.5). Pure DB, so it runs even
    # under USE_STUBS (a harmless no-op against the truncated test
    # table); the tick itself no-ops when retention <= 0.
    name = "media_plays_pruner"
    enabled_setting = None
    interval_setting = "media_plays_pruner_interval_sec"
    stub_suppressed = False

    async def tick(self) -> int:
        """Delete rows older than the retention window. Returns the row
        count removed (0 when pruning is disabled or nothing aged out).

        Reads ``settings.media_plays_retention_days`` live each tick so the
        web config editor's change applies without a restart (a 'hot' field)."""
        retention_days = settings.media_plays_retention_days
        if retention_days <= 0:
            return 0
        async with session_scope() as s:
            result = await s.execute(
                text(
                    "DELETE FROM media_plays "
                    "WHERE started_at < NOW() - make_interval(days => :days)"
                ),
                {"days": retention_days},
            )
        pruned = result.rowcount or 0
        if pruned:
            log.info(
                "media_plays pruner: deleted %d row%s older than %d days",
                pruned, "" if pruned == 1 else "s", retention_days,
            )
        return pruned
