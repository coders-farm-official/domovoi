"""NewsFetcher scheduling logic (no DB) — the daily 'is a fetch due' gate."""

from __future__ import annotations

from datetime import datetime

from domovoi.config import settings
from domovoi.workers.news_fetcher import NewsFetcher


def test_not_due_before_fetch_hour(monkeypatch) -> None:
    monkeypatch.setattr(settings, "news_fetch_hour", 5)
    f = NewsFetcher()
    assert f._due(datetime(2026, 7, 1, 4, 0)) is False


def test_due_after_hour_once_per_day(monkeypatch) -> None:
    monkeypatch.setattr(settings, "news_fetch_hour", 5)
    f = NewsFetcher()
    now = datetime(2026, 7, 1, 6, 0)
    assert f._due(now) is True
    # After a run, not due again the same day.
    f._last_run_date = now.date()
    assert f._due(now) is False
    # Next day it's due again.
    assert f._due(datetime(2026, 7, 2, 6, 0)) is True


def test_online_defaults_true_without_probe() -> None:
    # No app wired → assume online so a manual tick() still works.
    assert NewsFetcher()._online() is True
