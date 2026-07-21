"""Stale-lock sweeper for the Office Suite ``document_sessions`` table.

Each open office editor (OnlyOffice status callback / Collabora WOPI
PutFile) bumps its lock's ``last_seen_at``. A crashed browser tab, a
killed container, or a user who force-quits mid-edit stops sending
those heartbeats — and without cleanup the lock sits forever, greying
out the OTHER engine's "Open in …" button on that file. This worker
clears any lock whose ``last_seen_at`` is older than
``document_lock_stale_sec``, so a wedged editor self-heals.

Poll-loop shape mirrors ``TimerWatcher`` (workers/timer_watcher.py):
a background asyncio task with a stop-event, ticking on an interval.

WHERE THIS RUNS: this is a web-only feature — ``document_sessions`` is
written exclusively by the web backend (web/backend/api/documents.py),
and the web backend is the process guaranteed to be up whenever the
Office pages are in use. So it's registered in the WEB backend's
lifespan (web/backend/main.py), not the core's. It only needs
``session_scope`` (shared DB) + ``settings`` (thresholds), both of
which the web backend already imports from ``domovoi``. See
INTEGRATION_office.md for the rationale.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)


class DocumentLockSweeper(Worker):
    """Clears ``document_sessions`` rows idle longer than the timeout.

    Declarative registration (design §4.5): pure DB poll, safe to run
    unconditionally (no-op when no locks are held), so it isn't
    stub-suppressed and has no enabled gate."""

    name = "document_lock_sweeper"
    enabled_setting = None
    interval_setting = "document_lock_sweeper_interval_sec"
    stub_suppressed = False

    async def tick(self) -> int:
        """Delete every lock older than ``document_lock_stale_sec``.
        Returns how many were swept."""
        stale = settings.document_lock_stale_sec
        async with session_scope() as s:
            result = await s.execute(
                text(
                    "DELETE FROM document_sessions "
                    "WHERE last_seen_at < now() - make_interval(secs => :sec)"
                ),
                {"sec": stale},
            )
        n = result.rowcount or 0
        if n:
            log.info("document lock sweeper cleared %d stale lock(s)", n)
        return n
