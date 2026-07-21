"""Daily news fetcher.

Outer loop wakes every ``news_fetcher_loop_sec`` (default 15 min) and runs a
full fetch ONCE per day, when the configured ``news_fetch_hour`` has arrived
and it hasn't already run today. Cron-free — the in-memory ``_last_run_date``
plus the hour check is enough at homelab scale, and the whole fetch is
idempotent (dedup unique index), so a restart re-running it is harmless.

One fetch pass (gated by ``news_enabled`` + connectivity):

  1. Ensure the curated national/global house feeds exist + discover a local
     feed from ``news_location`` (so "what's the news today" answers offline).
  2. Fetch each house geographic scope (local/national/global) → house items.
  3. Feed discovery for any free-form topic that still has no feeds attached.
  4. When ``news_auto_fetch`` is on: fetch every person's topic feeds → per-
     person items, then summarize each person into ``news_briefings``.
  5. Retention sweep — delete items older than ``news_retention_days`` EXCEPT
     favorited ones.

Connectivity: the worker reads ``app.state.probe.online`` (the same probe the
router uses) and skips the whole tick when offline, so a transient outage
doesn't flag every feed invalid. Gated OFF-registration when ``news_enabled``
is false; skipped under USE_STUBS at the registration site.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import text

from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi import news_service
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)


class NewsFetcher(Worker):
    # Declarative registration (design §4.5): gated by news_enabled,
    # suppressed under stubs so the suite never fetches feeds / calls
    # Ollama. The runner drives tick() every news_fetcher_loop_sec;
    # the once-per-day gate lives in tick() itself (_due).
    name = "news_fetcher"
    enabled_setting = "news_enabled"
    interval_setting = "news_fetcher_loop_sec"
    stub_suppressed = True

    def __init__(self, app=None) -> None:
        self.app = app
        self._last_run_date: date | None = None

    def _online(self) -> bool:
        """Best-effort connectivity read off the shared probe. Assume online
        when no probe is wired (e.g. a direct test harness) so a manual
        ``tick()`` still works."""
        probe = getattr(getattr(self.app, "state", None), "probe", None)
        return bool(getattr(probe, "online", True))

    def _due(self, now: datetime) -> bool:
        """Whether a fetch is due: the fetch hour has arrived and we haven't
        already run today."""
        if now.hour < settings.news_fetch_hour:
            return False
        return self._last_run_date != now.date()

    async def tick(self) -> dict[str, int] | None:
        """Runner-facing poll tick: run the full fetch ONCE per day, when
        the configured ``news_fetch_hour`` has arrived, we haven't already
        run today, and we're online. Returns the fetch counts when a fetch
        ran, else None."""
        now = datetime.now()
        if not self._due(now):
            return None
        if not self._online():
            log.info("news fetcher: fetch due but offline; will retry")
            return None
        counts = await self.fetch_all()
        self._last_run_date = now.date()
        return counts

    async def fetch_all(self) -> dict[str, int]:
        """One full fetch pass. Returns coarse counts for logging/tests."""
        house = topics = deleted = discovered = briefings = 0
        async with session_scope() as session:
            # 1 + 2 — house-scope feeds + geographic fetch.
            await news_service.ensure_house_feeds(session)
            scopes = ["national", "global"]
            if settings.news_location.strip():
                scopes.insert(0, "local")
            for scope in scopes:
                house += await news_service.fetch_house_scope(session, scope)
            await session.commit()

            # 3 — discover feeds for free-form topics with none attached.
            discovered += await self._discover_missing_topic_feeds(session)
            await session.commit()

            # 4 — per-person topic fetch + briefing (gated by news_auto_fetch).
            if settings.news_auto_fetch:
                people = (
                    await session.execute(
                        text(
                            "SELECT DISTINCT person_id FROM news_topics ORDER BY person_id"
                        )
                    )
                ).all()
                for (person_id,) in people:
                    topics += await news_service.fetch_person_topics(session, int(person_id))
                    if await news_service.summarize_person_briefing(session, int(person_id)):
                        briefings += 1
                await session.commit()

            # 5 — retention sweep.
            deleted = await news_service.retention_sweep(
                session, settings.news_retention_days
            )
            await session.execute(
                text("SELECT pg_notify('news_changed', :p)"),
                {"p": f"house={house} topics={topics} del={deleted}"},
            )
            await session.commit()

        log.info(
            "news fetcher: house=%d topics=%d discovered=%d briefings=%d deleted=%d",
            house, topics, discovered, briefings, deleted,
        )
        return {
            "house": house, "topics": topics, "discovered": discovered,
            "briefings": briefings, "deleted": deleted,
        }

    async def _discover_missing_topic_feeds(self, session) -> int:
        """Run discovery for every free-form topic that has zero feeds. Called
        on each daily pass so a topic added while offline eventually resolves."""
        rows = (
            await session.execute(
                text(
                    """
                    SELECT nt.id, nt.topic
                      FROM news_topics nt
                      LEFT JOIN topic_feeds tf ON tf.topic_id = nt.id
                     WHERE nt.kind = 'freeform' AND tf.topic_id IS NULL
                    """
                )
            )
        ).all()
        total = 0
        for topic_id, phrase in rows:
            try:
                total += await news_service.discover_and_attach_feeds(
                    session, int(topic_id), phrase
                )
            except Exception as e:  # noqa: BLE001
                log.warning("news: discovery for topic %s failed: %s", topic_id, e)
        return total
