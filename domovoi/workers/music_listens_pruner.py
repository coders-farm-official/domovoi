"""Retention for the listening history (``music_listens``, V051).

Deletes listens older than "Keep listening history"
(``settings.music_listens_keep``: 365 days by default, or 90), and every
listen while it is "Don't keep". Read live each tick, so a save applies
without a restart; the save itself also prunes at once
(:func:`domovoi.music_listens.schedule_prune_now`, the setting's reapply
hook). Pure database work, so it runs under ``USE_STUBS`` too, like
:class:`domovoi.workers.media_plays_pruner.MediaPlaysPruner`, its twin for
"Recently played".
"""

from __future__ import annotations

import logging

from domovoi import music_listens
from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)


class MusicListensPruner(Worker):
    name = "music_listens_pruner"
    enabled_setting = None
    interval_setting = "music_listens_pruner_interval_sec"
    stub_suppressed = False

    async def tick(self) -> int:
        """Delete what the setting no longer keeps. Returns the rows deleted."""
        keep = settings.music_listens_keep
        async with session_scope() as s:
            pruned = await music_listens.prune(s, keep)
        if pruned:
            log.info("listening history: deleted %d listen%s (keep %s)",
                     pruned, "" if pruned == 1 else "s", keep)
        return pruned
