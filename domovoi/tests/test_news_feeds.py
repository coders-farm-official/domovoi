"""RSS parse + SearxNG feed-discovery tests (no DB).

Covers the discovery loop end-to-end against the SearxNG STUB client
(conftest forces USE_STUBS=true) plus monkeypatched probe/parse so no live
network is touched:
  * autodiscovery <link> extraction (rss + atom, relative href resolution)
  * discover_feeds returns validated feeds, capped at max_feeds
  * discover_feeds drops candidates that don't parse to articles
  * default category feed map
  * article links: only http(s) links survive ingest (FE-1)
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

import pytest

from domovoi.clients import news_feeds
from domovoi.clients.news_feeds import (
    CATEGORY_FEEDS,
    DiscoveredFeed,
    FeedArticle,
    _extract_autodiscovery_links,
    _parse_feed_blocking,
    default_feeds_for_category,
    discover_feeds,
    web_link_or_none,
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


# ─── article link scheme allowlist (FE-1) ───────────────────────────────


@pytest.mark.parametrize("link", [
    "https://news.example/story/1",
    "http://news.example/story/1",
    "HTTPS://News.Example/Story",
    "  https://news.example/padded  ",
])
def test_web_link_keeps_http_and_https(link: str) -> None:
    assert web_link_or_none(link) == link.strip()


@pytest.mark.parametrize("link", [
    "javascript:void(0)",
    "JavaScript:void(0)",
    "data:text/html;base64,AAAA",
    "intent://scan/#Intent;scheme=zxing;end",
    "tel:+15551234567",
    "sms:+15551234567",
    "file:///etc/hostname",
    "ftp://news.example/story",
    "mailto:desk@news.example",
    "//news.example/protocol-relative",
    "/relative/path",
    "http:no-host",
    "https:",
    "",
    "   ",
    None,
])
def test_web_link_nulls_every_other_scheme(link) -> None:
    assert web_link_or_none(link) is None


class _Entry(dict):
    """FeedParserDict stand-in: keys readable as attributes, missing → AttributeError."""

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _fake_feedparser(monkeypatch, entries: list[dict]) -> None:
    """Stand in for the optional feedparser dependency: ``parse`` returns an
    object shaped like feedparser's result (``feed`` mapping + ``entries``)."""
    def parse(_url: str):
        return types.SimpleNamespace(feed={"title": "Ex Feed"}, entries=[_Entry(e) for e in entries])
    monkeypatch.setitem(sys.modules, "feedparser", types.SimpleNamespace(parse=parse))


def test_ingest_nulls_non_web_links_but_keeps_the_article(monkeypatch) -> None:
    _fake_feedparser(monkeypatch, [
        {"id": "a1", "title": "Fine", "link": "https://news.example/a1"},
        {"id": "a2", "title": "Odd scheme", "link": "javascript:void(0)"},
        {"id": "a3", "title": "Phone", "link": "tel:+15551234567"},
        {"id": "a4", "title": "No link"},
    ])
    title, articles = _parse_feed_blocking("https://news.example/rss")
    assert title == "Ex Feed"
    assert [(a.guid, a.url) for a in articles] == [
        ("a1", "https://news.example/a1"),
        ("a2", None),
        ("a3", None),
        ("a4", None),
    ]
    assert [a.title for a in articles] == ["Fine", "Odd scheme", "Phone", "No link"]


def test_ingest_guid_fallback_still_uses_the_raw_link(monkeypatch) -> None:
    # An entry with no id keeps a stable guid (the raw link string) even
    # when that link is not stored as the article url.
    _fake_feedparser(monkeypatch, [
        {"title": "No id", "link": "ftp://news.example/x"},
    ])
    _title, articles = _parse_feed_blocking("https://news.example/rss")
    assert len(articles) == 1
    assert articles[0].guid == "ftp://news.example/x"
    assert articles[0].url is None
