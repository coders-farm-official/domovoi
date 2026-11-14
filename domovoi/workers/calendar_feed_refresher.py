"""Refresh the calendars added by link (V055; domovoi/calendar_feeds.py).

Every ``calendar_feed_tick_sec`` (5 min) the worker reads each link
calendar whose ``next_refresh_at`` has come: every 30 min normally, every
2 h when the internet answer is "sometimes", later after failures
(backoff). Under "never" nothing is fetched: each due calendar is marked
``internet_off`` (not a failure: a setting, not a verdict on the link) and
keeps its last copy. While the connectivity probe reads offline the tick is
skipped, so an outage doesn't count against every calendar.

Phone calendars are not read here (phones send their own, on the LAN,
under any internet answer), but each tick deletes those of a phone not
heard from for 7 days (calendar_feeds.expire_phones: an uninstalled app, a
lost phone, a server the phone forgot), offline or not.
"""

from __future__ import annotations

import logging

from domovoi import calendar_feeds, connectivity, egress
from domovoi.db.session import session_scope
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)


class CalendarFeedRefresher(Worker):
    name = "calendar_feed_refresher"
    interval_setting = "calendar_feed_tick_sec"
    stub_suppressed = True
    # Gated in tick() itself: under "never" it still marks the calendars,
    # so the page can say why they don't refresh.
    requires_online = False

    async def tick(self) -> dict[str, int]:
        async with session_scope() as session:
            expired = await calendar_feeds.expire_phones(session)
        if not egress.internet_turned_off():
            probe = connectivity.current_probe()
            if probe is not None and not getattr(probe, "online", True):
                return {"refreshed": 0, "failed": 0, "skipped": 1, "expired": expired}
        refreshed = failed = 0
        async with session_scope() as session:
            due = await calendar_feeds.due_links(session)
        for source_id in due:
            async with session_scope() as session:
                result = await calendar_feeds.refresh_link(session, source_id)
            if result.ok:
                refreshed += 1
            elif not result.busy:
                failed += 1
        return {"refreshed": refreshed, "failed": failed, "skipped": 0, "expired": expired}
