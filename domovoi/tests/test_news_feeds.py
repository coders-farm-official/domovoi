"""RSS parse + SearxNG feed-discovery tests (no DB).

Covers the discovery loop end-to-end against the SearxNG STUB client
(conftest forces USE_STUBS=true) plus monkeypatched probe/parse so no live
network is touched:
  * autodiscovery <link> extraction (rss + atom, relative href resolution)
  * discover_feeds returns validated feeds, capped at max_feeds
  * discover_feeds drops candidates that don't parse to articles
  * default category feed map
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from domovoi.clients import news_feeds
from domovoi.clients.news_feeds import (
    CATEGORY_FEEDS,
    DiscoveredFeed,
    FeedArticle,
    _extract_autodiscovery_links,
    default_feeds_for_category,
    discover_feeds,
)


def _article(guid: str = "g1") -> FeedArticle:
    return FeedArticle(
        guid=guid, url=f"https://ex/{guid}", title=f"Title {guid}",
        source="Ex", summary="body", published_at=datetime.now(timezone.utc),
    )


# ─── autodiscovery link extraction ──────────────────────────────────────


def test_extract_autodiscovery_rss_link() -> None:
    html = (
        '<html><head>'
        '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
        '</head></html>'
    )
    links = _extract_autodiscovery_links(html, "https://site.example/page")
    assert links == ["https://site.example/feed.xml"]


def test_extract_autodiscovery_atom_and_absolute() -> None:
    html = (
        '<link rel="alternate" type="application/atom+xml" '
        'href="https://cdn.example/atom">'
    )
    links = _extract_autodiscovery_links(html, "https://site.example/")
    assert links == ["https://cdn.example/atom"]


def test_extract_ignores_non_feed_links() -> None:
    html = '<link rel="stylesheet" href="/style.css">'
    assert _extract_autodiscovery_links(html, "https://x/") == []


# ─── discover_feeds ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_feeds_validates_and_caps(monkeypatch) -> None:
    # The stub SearxNG returns N results with urls stub.example/r/{i}. Have
    # each probe yield one feed url, and every feed parse to one article.
    async def fake_probe(url: str) -> list[str]:
        return [url.rstrip("/") + "/feed.xml"]

    async def fake_parse(url: str):
        return "Stub Feed", [_article(url)]

    monkeypatch.setattr(news_feeds, "_probe_page_for_feed_urls", fake_probe)
    monkeypatch.setattr(news_feeds, "parse_feed", fake_parse)

    feeds = await discover_feeds("formula 1", max_feeds=2, max_probe=6)
    assert len(feeds) == 2
    assert all(isinstance(f, DiscoveredFeed) for f in feeds)
    assert all(f.url.endswith("/feed.xml") for f in feeds)


@pytest.mark.asyncio
async def test_discover_feeds_drops_unvalidated(monkeypatch) -> None:
    async def fake_probe(url: str) -> list[str]:
        return [url.rstrip("/") + "/feed.xml"]

    async def empty_parse(url: str):
        return None, []  # nothing validates

    monkeypatch.setattr(news_feeds, "_probe_page_for_feed_urls", fake_probe)
    monkeypatch.setattr(news_feeds, "parse_feed", empty_parse)

    feeds = await discover_feeds("obscure topic", max_feeds=3)
    assert feeds == []


@pytest.mark.asyncio
async def test_discover_feeds_empty_topic() -> None:
    assert await discover_feeds("   ") == []


# ─── default category map ───────────────────────────────────────────────


def test_default_feeds_for_known_category() -> None:
    assert default_feeds_for_category("tech") == CATEGORY_FEEDS["tech"]
    assert default_feeds_for_category("TECH") == CATEGORY_FEEDS["tech"]


def test_default_feeds_for_unknown_category() -> None:
    assert default_feeds_for_category("nonsense") == []
