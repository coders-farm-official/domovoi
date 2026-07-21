"""RSS fetch/parse + SearxNG-assisted feed discovery for the News feature.

Two jobs, kept in one module because they're the two halves of "turn a
topic into articles":

  1. ``parse_feed(url)`` — fetch + parse an RSS/Atom feed via ``feedparser``
     (already in the ``[real-clients]`` extra) into a list of
     :class:`FeedArticle`. Used by the ``news_fetcher`` worker on every poll
     and by the discovery validator.

  2. ``discover_feeds(topic)`` — the novel bit that makes free-form topics
     work without a news API: query the existing SearxNG client for the
     topic, take the top result URLs, probe each page for an RSS
     autodiscovery ``<link rel="alternate" type="application/rss+xml">``
     (or a feed-looking URL), then VALIDATE each candidate by actually
     parsing it. Only feeds that parse to at least one entry are returned.

Plus a small curated ``CATEGORY_FEEDS`` / ``NATIONAL_FEEDS`` /
``GLOBAL_FEEDS`` map so curated categories + the national/global geographic
scopes resolve to known-good feeds instantly and offline-defined. LOCAL
scope is discovered from ``news_location`` (see ``local_feed_candidates``).

Network + parse both happen off the event loop (``asyncio.to_thread`` for
the blocking feedparser call, httpx async for the HTML probe). Everything
here is best-effort: a dead feed or an HTML page with no autodiscovery link
yields an empty list, never an exception to the caller.

Testability: ``parse_feed`` and ``_probe_page_for_feed_urls`` are
module-level so a test can monkeypatch them; ``discover_feeds`` drives the
SearxNG stub client and those two functions, so the whole discovery loop is
exercisable under ``USE_STUBS`` with no live network.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

from domovoi.clients.searxng import get_searxng_client
from domovoi.config import settings

log = logging.getLogger(__name__)


# ─── Curated default feeds ───────────────────────────────────────────────
# Category key → known-good RSS feeds. Instant, offline-defined, reliable —
# the reliable half of the topic model. Free-form topics use discovery.
# This map needs occasional maintenance (a shuttered feed goes stale); the
# validity dot on the web page surfaces breakage, and the user can add a
# replacement feed manually.
CATEGORY_FEEDS: dict[str, list[str]] = {
    "world":    ["http://feeds.bbci.co.uk/news/world/rss.xml",
                 "https://feeds.npr.org/1004/rss.xml"],
    "national": ["https://feeds.npr.org/1003/rss.xml",
                 "http://feeds.bbci.co.uk/news/rss.xml"],
    "tech":     ["https://feeds.arstechnica.com/arstechnica/index",
                 "https://www.theverge.com/rss/index.xml"],
    "business": ["https://feeds.npr.org/1006/rss.xml",
                 "https://feeds.bbci.co.uk/news/business/rss.xml"],
    "science":  ["https://feeds.npr.org/1007/rss.xml",
                 "https://www.sciencedaily.com/rss/all.xml"],
    "sports":   ["https://www.espn.com/espn/rss/news",
                 "https://feeds.bbci.co.uk/sport/rss.xml"],
    "health":   ["https://feeds.npr.org/1128/rss.xml"],
    "politics": ["https://feeds.npr.org/1014/rss.xml"],
    "entertainment": ["https://feeds.npr.org/1008/rss.xml"],
}

# The fixed set of curatable categories the web UI offers as chips.
CATEGORY_KEYS: tuple[str, ...] = tuple(CATEGORY_FEEDS.keys())

# House-scope geographic feeds. NATIONAL/GLOBAL are curated + stable; LOCAL
# is discovered from news_location.
NATIONAL_FEEDS: list[str] = [
    "https://feeds.npr.org/1003/rss.xml",
    "http://feeds.bbci.co.uk/news/rss.xml",
]
GLOBAL_FEEDS: list[str] = [
    "http://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.npr.org/1004/rss.xml",
]


@dataclass
class FeedArticle:
    guid: str
    url: str | None
    title: str | None
    source: str | None
    summary: str | None
    published_at: datetime | None


@dataclass
class DiscoveredFeed:
    url: str
    title: str | None
    source: str | None


# ─── RSS parse ───────────────────────────────────────────────────────────


def _entry_published(entry: Any) -> datetime | None:
    pp = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if pp is None and hasattr(entry, "get"):
        pp = entry.get("published_parsed") or entry.get("updated_parsed")
    if pp is None:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(pp), tz=timezone.utc)
    except (ValueError, OverflowError):
        return None


def _parse_feed_blocking(url: str) -> tuple[str | None, list[FeedArticle]]:
    """Blocking feedparser call — run via ``asyncio.to_thread``. Returns
    ``(feed_title, articles)``. Empty article list on any failure."""
    import feedparser  # local import — optional [real-clients] dep

    parsed = feedparser.parse(url)
    feed = getattr(parsed, "feed", {}) or {}
    source = feed.get("title") if hasattr(feed, "get") else getattr(feed, "title", None)

    articles: list[FeedArticle] = []
    for entry in getattr(parsed, "entries", []) or []:
        link = getattr(entry, "link", None)
        if link is None and hasattr(entry, "get"):
            link = entry.get("link")
        title = getattr(entry, "title", None)
        if title is None and hasattr(entry, "get"):
            title = entry.get("title")
        # guid: prefer the feed's id, fall back to the link, then the title.
        guid = getattr(entry, "id", None) or link or title
        if not guid:
            continue
        summary = getattr(entry, "summary", None)
        if summary is None and hasattr(entry, "get"):
            summary = entry.get("summary")
        articles.append(
            FeedArticle(
                guid=str(guid),
                url=str(link) if link else None,
                title=str(title) if title else None,
                source=str(source) if source else None,
                summary=str(summary) if summary else None,
                published_at=_entry_published(entry),
            )
        )
    return (str(source) if source else None), articles


async def parse_feed(url: str) -> tuple[str | None, list[FeedArticle]]:
    """Fetch + parse ``url`` into ``(feed_title, [FeedArticle])``. Best-effort:
    a network error / malformed feed returns ``(None, [])`` rather than
    raising, so the fetcher can flag the feed invalid and move on."""
    try:
        return await asyncio.to_thread(_parse_feed_blocking, url)
    except Exception as e:  # noqa: BLE001 — best-effort by contract
        log.warning("news: parse_feed failed for %s: %s", url, e)
        return None, []


async def feed_is_valid(url: str) -> bool:
    """Whether ``url`` parses to at least one article — the discovery
    validator and the web 'check validity' endpoint both use this."""
    _title, articles = await parse_feed(url)
    return len(articles) > 0


# ─── Feed discovery (SearxNG → probe → validate) ─────────────────────────

# HTML autodiscovery: <link rel="alternate" type="application/rss+xml" ...>.
# Also catch atom+xml. We parse with a permissive regex rather than a full
# HTML parser — autodiscovery links are simple and a heavy parser dep isn't
# worth it for this best-effort probe.
_FEED_MIME_HINTS = ("application/rss+xml", "application/atom+xml", "application/xml")


def _extract_autodiscovery_links(html: str, base_url: str) -> list[str]:
    """Pull feed URLs out of ``<link rel="alternate" type="...rss...">`` tags.
    Relative hrefs are resolved against ``base_url``. Regex-based on purpose
    (see module note); returns absolute URLs in document order, deduped."""
    import re

    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"<link\b[^>]*>", html, flags=re.IGNORECASE):
        tag = m.group(0)
        low = tag.lower()
        if "alternate" not in low:
            continue
        if not any(hint in low for hint in _FEED_MIME_HINTS):
            continue
        href_m = re.search(r"""href\s*=\s*["']([^"']+)["']""", tag, flags=re.IGNORECASE)
        if not href_m:
            continue
        href = urljoin(base_url, href_m.group(1).strip())
        if href not in seen:
            seen.add(href)
            out.append(href)
    return out


async def _probe_page_for_feed_urls(url: str) -> list[str]:
    """Fetch ``url`` and return any RSS autodiscovery feed URLs it advertises.
    If ``url`` itself already looks like a feed (``.rss``/``.xml``/``/feed``),
    it's returned as its own candidate. Best-effort — network error → []."""
    candidates: list[str] = []
    path = urlparse(url).path.lower()
    if path.endswith((".rss", ".xml", ".atom")) or path.rstrip("/").endswith("/feed") or "rss" in path:
        candidates.append(url)

    try:
        import httpx

        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Domovoi-news/1.0"})
            r.raise_for_status()
            html = r.text
    except Exception as e:  # noqa: BLE001
        log.debug("news: probe fetch failed for %s: %s", url, e)
        return candidates

    for feed_url in _extract_autodiscovery_links(html, url):
        if feed_url not in candidates:
            candidates.append(feed_url)
    return candidates


async def discover_feeds(
    topic: str,
    *,
    max_feeds: int | None = None,
    max_probe: int | None = None,
) -> list[DiscoveredFeed]:
    """SearxNG → probe → validate loop for a free-form topic.

    Query SearxNG for ``topic``, probe the top result URLs for RSS
    autodiscovery, validate each candidate by parsing, and return up to
    ``max_feeds`` feeds that actually parse to articles. Deduped by URL.

    Returns ``[]`` when nothing validates — the caller surfaces "no feeds
    found, add one manually". Never raises.
    """
    max_feeds = max_feeds if max_feeds is not None else settings.news_discovery_max_feeds
    max_probe = max_probe if max_probe is not None else settings.news_discovery_max_probe
    topic = (topic or "").strip()
    if not topic:
        return []

    client = get_searxng_client()
    try:
        results = await client.search(f"{topic} rss feed", max_results=max_probe)
    except Exception as e:  # noqa: BLE001
        log.warning("news: SearxNG discovery search failed for %r: %s", topic, e)
        return []

    out: list[DiscoveredFeed] = []
    seen: set[str] = set()
    for result in results:
        if len(out) >= max_feeds:
            break
        result_url = getattr(result, "url", None)
        if not result_url:
            continue
        try:
            candidate_urls = await _probe_page_for_feed_urls(result_url)
        except Exception as e:  # noqa: BLE001
            log.debug("news: probe raised for %s: %s", result_url, e)
            continue
        for feed_url in candidate_urls:
            if len(out) >= max_feeds or feed_url in seen:
                continue
            seen.add(feed_url)
            title, articles = await parse_feed(feed_url)
            if not articles:
                continue  # didn't validate — skip
            out.append(
                DiscoveredFeed(
                    url=feed_url,
                    title=title or getattr(result, "title", None),
                    source=title,
                )
            )
    log.info("news: discovered %d feed(s) for topic %r", len(out), topic)
    return out


async def local_feed_candidates(location: str) -> list[DiscoveredFeed]:
    """Discover a LOCAL-scope feed from a location string (city / region).
    Thin wrapper over ``discover_feeds`` seeded with 'news' so the SearxNG
    query targets a local news outlet's feed."""
    location = (location or "").strip()
    if not location:
        return []
    return await discover_feeds(f"{location} local news", max_feeds=2)


def default_feeds_for_category(category: str) -> list[str]:
    """Curated feed URLs for a category key, or ``[]`` for an unknown key."""
    return list(CATEGORY_FEEDS.get((category or "").strip().lower(), []))
